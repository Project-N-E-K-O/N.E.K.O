# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Conversation turn pipeline for ``LLMSessionManager``: text/audio
intake, transcripts, response completion/discard bookkeeping,
voice-echo suppression, and the mirror channel.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
from main_logic.voice_turn.transcript_admission import assess_transcript, TranscriptDisposition
import json
import re
import time
from collections import deque
from typing import Any, Callable, Optional
from datetime import datetime
from fastapi import WebSocketDisconnect
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from utils.llm_client import AIMessage
from utils.game_route_state import get_active_game_route_generation_identity
from utils.external_route_registry import is_route_slot_taken
from main_logic.session_state import SessionEvent, TurnOwner
from main_logic.agent_event_bus import (
    dispatch_user_utterance,
    publish_conversation_turn_observed_best_effort,
)
from config import SESSION_ARCHIVE_TRIGGER_TOKENS, SESSION_TURN_THRESHOLD
from uuid import uuid4
import numpy as np
from ._shared import (
    _REQUEST_ID_UNSET,
    _ReplyTurn,
    _taken_over_reply_turn,
    _MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX,
    _VOICE_ECHO_LOOKBACK_SECONDS,
    _VOICE_ECHO_LOOKBACK_CHARS,
    _looks_like_recent_ai_echo,
    logger,
    _proactive_expected_sid,
    _proactive_published_text_chunks,
    _get_chat_locale_text,
)
from .game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class MirrorSpeechStream:
    """One streaming mirror speech of :meth:`TurnMixin.open_mirror_speech_stream` (OD-15 v3).

    ``push`` / ``finish`` / ``abort`` are synchronous: they only record the
    request. One task per stream applies them in order. Streams of one
    manager take the TTS turn one after another, in the order they were
    opened: a stream claims it (its speech id becomes the current one, the
    turn's done flags are reset, the TTS pipeline is started) only once the
    stream opened before it got its end marker into the TTS queue (not just
    deferred until the worker is ready), was aborted -- and its interruption
    finished -- or failed. ``_PREDECESSOR_WAIT_S`` bounds that wait; a
    predecessor still holding the turn then is failed and interrupted
    before the claim. A stream does not claim at all when an ordinary turn
    started after it was opened (the family spoke first): it fails instead.
    Each text then goes
    through ``_enqueue_tts_text_chunk`` (``tts_pending_chunks`` while the
    worker is not ready) and the end through ``_request_tts_done_locked``,
    under ``tts_cache_lock``, and only while its speech id is still the
    current one: a stream whose turn was taken over (ordinary chat, a newer
    stream after the bound) stops touching the shared TTS state. Text is
    never mirrored to the page (``mirror_text=False``) and never enters the
    private chat history.

    ``push`` / ``finish`` return False once the stream is closed (finished,
    aborted, failed); ``finish`` returns ``"no_worker"`` when it already
    knows no worker can take the end marker. ``abort`` is terminal and
    idempotent: it stops the task, drops the ``on_enqueued`` registration
    and, while this speech is still the current one, runs
    :meth:`TurnMixin.interrupt_mirror_speech`. ``on_failed()`` is called
    once when the stream stops without its end marker for any reason but
    ``abort`` (no worker, a TTS start or enqueue error, turn taken over); an
    enqueue error after text reached TTS also interrupts the partial line.
    """

    NO_WORKER = "no_worker"
    _PREDECESSOR_WAIT_S = 30.0
    _DEFERRED_POLL_S = 0.25

    def __init__(
        self,
        mgr: Any,
        speech_id: str,
        *,
        metadata: dict,
        request_id: str,
        on_failed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._mgr = mgr
        self._speech_id = speech_id
        self.metadata = dict(metadata or {})
        self.request_id = request_id
        self._on_failed = on_failed
        self._ops: deque = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self._aborted = False
        self._started = False
        self._claimed = False
        self._task: Optional[asyncio.Future] = None
        self._predecessor: Optional["MirrorSpeechStream"] = None
        # 打开时的当前语音：认领时它若被普通对话换掉（亲人先开口），本流不抢那一轮
        self._base_speech_id = getattr(mgr, "current_speech_id", None)
        self._end_deferred = False
        # 本流交出 TTS 轮次的时刻（结束标记真正入队 / 中止且打断清理完 / 失败）：下一条流等它
        self._released = asyncio.get_running_loop().create_future()

    @property
    def speech_id(self) -> str:
        return self._speech_id

    @property
    def closed(self) -> bool:
        return self._closed

    def _start(self, predecessor: Optional["MirrorSpeechStream"]) -> None:
        self._predecessor = predecessor
        self._task = self._mgr._fire_task(self._run())

    def push(self, delta: str) -> bool:
        if self._closed:
            return False
        if delta:
            self._ops.append(delta)
            self._wake.set()
        return True

    def finish(self) -> Any:
        if self._closed:
            return False
        if self._started and not self._worker_alive():
            # 已经知道没有 worker 接得住结束标记：当场告诉调用方（它据此改按估时放字幕）
            self._close()
            self._release()
            return self.NO_WORKER
        self._closed = True
        self._ops.append(None)
        self._wake.set()
        return True

    def abort(self) -> bool:
        if self._aborted:
            return False
        self._aborted = True
        self._closed = True
        self._ops.clear()
        self._mgr._mirror_stream_callbacks.pop(self._speech_id, None)
        self._mgr._mirror_stream_ends.pop(self._speech_id, None)
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        # 打断清理做完才交出轮次：下一条流不会在清理途中写进待发缓存、随后被一并清掉
        self._mgr._fire_task(self._abort_then_release())
        return True

    async def _abort_then_release(self) -> None:
        try:
            await self._mgr._interrupt_mirror_stream(self._speech_id)
        finally:
            self._release()

    def _close(self) -> None:
        self._closed = True
        self._ops.clear()
        self._mgr._mirror_stream_callbacks.pop(self._speech_id, None)
        self._mgr._mirror_stream_ends.pop(self._speech_id, None)

    async def _give_up(self) -> None:
        """A successor waited too long: fail this stream and interrupt what it queued, then hand over."""
        if self._released.done():
            return
        self._fail()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        try:
            if self._claimed:
                await self._mgr._interrupt_mirror_stream(self._speech_id)
        finally:
            self._release()

    def _release(self) -> None:
        if not self._released.done():
            self._released.set_result(None)

    def _fail(self) -> None:
        self._close()
        callback, self._on_failed = self._on_failed, None
        if callback is not None:
            try:
                callback()
            except Exception as exc:
                logger.warning("[%s] mirror speech failure callback failed: %s", self._mgr.lanlan_name, exc)

    def _owns_turn(self) -> bool:
        return self._mgr.current_speech_id == self._speech_id

    def _worker_alive(self) -> bool:
        mgr = self._mgr
        return bool(
            getattr(mgr, "_tts_audio_output_supported", True)
            and mgr.tts_thread and mgr.tts_thread.is_alive()
        )

    async def _run(self) -> None:
        try:
            await self._drain()
            if self._end_deferred:
                await self._watch_deferred_end()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 起不了 TTS / 入队出错：已有文字进了 TTS 就打断这半句，再关掉本流（之后的 push 返回 False）
            logger.warning("[%s] mirror speech stream failed: %s", self._mgr.lanlan_name, exc)
            self._fail()
            if self._claimed:
                await self._mgr._interrupt_mirror_stream(self._speech_id)
        finally:
            if not self._aborted and not self._end_deferred:
                # 中止由 abort() 那边在打断清理做完之后交出轮次；结束标记推迟到 worker 就绪的，
                # 等它真正入队（_request_tts_done_locked 回调）再交出——这里都不能抢先交出
                self._release()

    async def _watch_deferred_end(self) -> None:
        """The end marker waits for the worker: fail and hand over if that pending work is discarded.

        An interruption or a session restart clears ``tts_pending_chunks`` and
        the deferred flag without queuing the marker; this speech then never
        ends, so the stream reports failure instead of holding the turn.
        """
        mgr = self._mgr
        while not self._released.done():
            await asyncio.wait([self._released], timeout=self._DEFERRED_POLL_S)
            if self._released.done():
                return
            if not (getattr(mgr, "_tts_done_pending_until_ready", False) and self._owns_turn()):
                # 推迟的结束标记被别处清掉了（打断 / 会话重建）：它不会再入队
                self._fail()
                self._release()
                return

    def _end_queued(self) -> None:
        """``_request_tts_done_locked`` queued this speech's end marker: hand the turn over."""
        self._release()

    async def _drain(self) -> None:
        mgr = self._mgr
        predecessor = self._predecessor
        if predecessor is not None and not predecessor._released.done():
            # 按打开顺序轮流认领：前一条流交出轮次之前，本流不能覆盖它的 speech id 与 done 标记
            await asyncio.wait([predecessor._released], timeout=self._PREDECESSOR_WAIT_S)
            if not predecessor._released.done():
                # 前一条一直不收尾：先让它失败、打断它已入队的半句，两行不会被合成一句
                await predecessor._give_up()
        async with mgr.lock:
            expected = {self._base_speech_id}
            if predecessor is not None:
                expected.add(predecessor.speech_id)
            if mgr.current_speech_id not in expected:
                # 打开之后普通对话开了新一轮（亲人先开口）：不抢那一轮，本流放弃
                self._fail()
                return
            mgr.current_speech_id = self._speech_id
            mgr._tts_done_queued_for_turn = False
            mgr._tts_done_pending_until_ready = False
            mgr._mirror_stream_ends[self._speech_id] = self._end_queued
        self._claimed = True
        mgr.remember_speech_playback_gain(self._speech_id, 1.0)
        await mgr.ensure_tts_pipeline_alive()
        self._started = True
        while True:
            while not self._ops:
                self._wake.clear()
                await self._wake.wait()
            async with mgr.tts_cache_lock:
                if self._aborted:
                    return
                if not self._owns_turn():
                    # 轮次被别人接走（普通聊天、超过等待上限后认领的下一条流）：不再碰共享的 TTS 状态
                    self._fail()
                    return
                if not self._worker_alive():
                    # worker 不在 / 不出音频：之后的 push 立刻返回 False，调用方改按估时放字幕
                    self._fail()
                    return
                while self._ops:
                    item = self._ops.popleft()
                    if item is None:
                        status = mgr._request_tts_done_locked()
                        if status == self.NO_WORKER:
                            self._fail()
                        elif status == "deferred":
                            # worker 还没就绪：结束标记补发时才交出轮次（下一条流认领会清掉推迟标记）
                            self._end_deferred = True
                        return
                    if mgr.tts_ready:
                        mgr._enqueue_tts_text_chunk(self._speech_id, item)
                    else:
                        mgr.tts_pending_chunks.append((self._speech_id, item))


class TurnMixin:
    """Conversation turn pipeline methods (see module docstring)."""

    async def handle_new_message(self):
        """Handle new model output: clear the TTS queue and notify the frontend"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: suppressing ordinary realtime new-message handling", self.lanlan_name)
            return

        # 重置音频重采样器状态（新轮次音频不应与上轮次连续）
        self.audio_resampler.clear()
        await self._clear_tts_pipeline()
        # _tts_done_queued_for_turn 的权威清零已经在 _clear_tts_pipeline 入口、
        # 与 __interrupt__ 入队同步完成（取消落在它内部的 sleep 上也不会留下
        # "worker 已中断、记账还说已排队"的残留）。这里保留一次重复清零，兜底那
        # ~20ms 窗口内被并发路径重新置 True 的情况；pending_until_ready 则由
        # _clear_tts_pipeline 末尾与 tts_pending_chunks 成对清，这里同样是兜底。
        self._tts_done_queued_for_turn = False  # 新轮次重置 TTS 结束信号标记
        self._tts_done_pending_until_ready = False
        # 新一轮开始：清空上一轮 AI 文本累加器（即使上轮 turn end 已清过，
        # proactive abort 等异常路径可能漏清，新轮次起点重置最稳）
        self._reset_ai_turn_buffer()
        self._discarded_turn_open = False

        await self.send_user_activity()

        # 立即生成新的 speech_id，确保新回复不会使用被打断的 ID
        # 这样即使 handle_input_transcript 先于 handle_new_message 执行，
        # 新回复的 audio_chunk 也不会被错误丢弃
        async with self.lock:
            self.current_speech_id = str(uuid4())
            new_sid = self.current_speech_id
            # 必须在 self.lock 内同步翻 _preempted 标记，使新 sid + preempt 对
            # 同样在 self.lock 内复查 is_proactive_preempted 的 prepare_proactive_delivery
            # 原子可见；否则 proactive 会插到 lock 释放 ~ fire() 之间把 user sid
            # 再覆盖成 proactive sid。完整 USER_INPUT 事件仍在锁外 fire，以更新
            # owner/user_sid 并派发订阅者。
            self.state.mark_user_input_preempt()
        await self.state.fire(SessionEvent.USER_INPUT, sid=new_sid)

    def read_current_speech_id(self) -> str | None:
        """Hand the realtime client the host's notion of "which turn is live".

        Passed to ``OmniRealtimeClient`` as ``get_host_turn_id``. The speech id
        already IS the host's turn token — every writer that starts a turn
        assigns a fresh one — so the client needs no token of its own, only a
        way to sample this one at a turn start and compare at the end (#2612).

        Deliberately not locked. The callers sample and compare a single
        immutable string; taking ``self.lock`` here would put a host lock in
        the realtime receive loop's path, and reading one instant staler only
        shifts the guard's boundary, never inverts its answer.
        """
        return self.current_speech_id

    async def rotate_speech_id_for_response_done(self):
        """Lightweight sid rotation for realtime providers without server VAD.

        Triggered at OmniRealtimeClient's response.done event (the pass-through of
        Gemini's turn_complete) when ``_has_server_vad=False`` (lanlan.app+free /
        livestream). Without server VAD, ``speech_stopped`` never fires, so
        the canonical ``handle_new_message`` rotation path stays dormant and
        every turn ends up reusing the initial session sid — TTS upstream
        silently drops text after the first ``tts.response.done`` closes
        that sid, and turn 2+ goes silent.

        Why not reuse ``handle_new_message``: that helper rotates sid AND
        clears the TTS pipeline AND fires USER_INPUT. Both side effects are
        correct at ``speech_stopped`` (user just spoke, AI hasn't started,
        leftover TTS belongs to an interrupted prior turn). At
        ``response.done`` they're wrong — leftover TTS is the trailing audio
        of the AI turn that just ended; clearing it would clip the last
        few syllables. ``USER_INPUT`` mischaracterizes the trigger (no user
        input actually happened — this is end-of-AI-turn, not start-of-user).
        Resetting ``audio_resampler`` is safe because the next turn's audio
        is a fresh stream — keeping stale soxr state would only risk a
        boundary artefact at turn 2's first frame.

        On the write order and cancellation (#2619): the flags are cleared
        before the ``async with self.lock`` that rotates the sid, which looks
        like it could be cancelled half-applied. It cannot. No holder of
        ``self.lock`` anywhere in this package suspends while holding it, so
        the lock is never observed held, so this acquire always takes the
        uncontended fast path and never yields — and a cancellation therefore
        lands either before this method is entered or after it has returned,
        never between these writes. That property is the whole reason this
        shape is safe, so it is enforced rather than assumed: see
        CORE_LOCK_NO_AWAIT in ``scripts/check_core_contracts.py``. Adding an
        ``await`` inside any of those blocks makes the lock contendable and
        makes this window real; move the awaited work out of the block
        instead of reordering the writes here.
        """
        if self._takeover_active:
            return
        self.audio_resampler.clear()
        # 必须重置这两个 flag，否则下一轮 ``_request_tts_done_locked`` 会因
        # ``_tts_done_queued_for_turn=True`` 直接 early-return，下一轮的 TTS
        # flush sentinel 永远不入队，server 拿不到 ``tts.flush`` 句尾音频
        # 可能挂在 buffer 里、长句 utterance 不会 finalize。``handle_new_message``
        # 在 speech_stopped 路径也是这样 reset 的（本文件同名方法；上帝文件
        # main_logic/core.py 拆包后两者同住 main_logic/core/turn.py），这里和它对偶。
        self._tts_done_queued_for_turn = False
        self._tts_done_pending_until_ready = False
        async with self.lock:
            self.current_speech_id = str(uuid4())

    async def handle_text_data(
        self,
        text: str,
        is_first_chunk: bool = False,
        *,
        ui_enabled: bool = True,
        tts_enabled: bool = True,
    ):
        """Text callback: handles text display and TTS (for text mode).

        The ``ui_enabled`` / ``tts_enabled`` split is used by OmniOfflineClient's
        long-reply summary path: the tail text after the cutover goes to UI only
        (keeping the frontend "show full text"), while the condensed version from
        the summary LLM goes to TTS only (keeping TTS from reading the whole
        tail). Both flags off is also consistent — going to neither UI nor TTS
        equals discarding the segment, so return immediately.
        """
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime text chunk len=%d", self.lanlan_name, len(text or ""))
            return

        if not ui_enabled and not tts_enabled:
            return

        # 主动搭话 race guard：prompt_ephemeral 路径会设置 _proactive_expected_sid
        # contextvar。若其与 current_speech_id 不一致，说明用户已在 proactive
        # 生成期间打断并换了 sid，本 chunk 属于已被作废的 proactive 轮次，必须
        # 整体丢弃（含前端显示和 TTS），避免污染用户当前轮次。user stream_text
        # 在自己的 task 里 contextvar 为 None，不受影响。
        expected_sid = _proactive_expected_sid.get()
        published_text_chunks = _proactive_published_text_chunks.get()
        if expected_sid is not None and expected_sid != self.current_speech_id:
            logger.debug(
                "handle_text_data drop: expected_sid=%s current_sid=%s len=%d",
                expected_sid, self.current_speech_id, len(text),
            )
            return

        # 如果是新消息的第一个chunk，清空TTS队列和缓存以打断之前的语音。
        # summary epilogue 触发的 TTS-only 注入 is_first_chunk=False，不会
        # 误清掉本轮已经播放/排队的 prefix 音频。
        if is_first_chunk and self.use_tts and tts_enabled:
            async with self.tts_cache_lock:
                # The proactive sid may be preempted while this task waits for
                # the cache lock.  Recheck before clearing anything owned by
                # the replacement user turn.
                if expected_sid is not None and self.current_speech_id != expected_sid:
                    return
                self.tts_pending_chunks.clear()
                self._discard_pending_ai_voice_echo()

            if self.tts_thread and self.tts_thread.is_alive():
                # 清空响应队列中待发送的音频数据
                while not self.tts_response_queue.empty():
                    try:
                        self.tts_response_queue.get_nowait()
                    except Exception:
                        break

        # 文本模式下，无论是否使用TTS，都要发送文本到前端显示
        if ui_enabled:
            on_published = None
            if expected_sid is not None and published_text_chunks is not None:
                visible_text = self.emotion_pattern.sub('', text)

                def _record_published_text(_published_at: float) -> None:
                    published_text_chunks.append(visible_text)

                on_published = _record_published_text

            publish_result = await self.send_lanlan_response(
                text,
                is_first_chunk,
                turn_id=expected_sid,
                remember_voice_echo=not self.use_tts,
                expected_speech_id=expected_sid,
                on_published=on_published,
            )
            # ``None`` means the guarded send lost ownership at its internal
            # queue-write boundary.  Do not leak the same stale chunk into TTS.
            if expected_sid is not None and publish_result is None:
                return

        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts and tts_enabled:
            async with self.tts_cache_lock:
                # ``send_lanlan_response`` may await the WebSocket after its
                # synchronous UI publish.  Recheck before the TTS write so an
                # intervening user turn cannot inherit proactive audio.
                if expected_sid is not None and self.current_speech_id != expected_sid:
                    return
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self._enqueue_tts_text_chunk(self.current_speech_id, text)
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存（规范化延迟到 _flush_tts_pending_chunks）
                    self.tts_pending_chunks.append((self.current_speech_id, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info("TTS未就绪，开始缓存文本chunk...")
                    # 仅在回复首 chunk 尝试拉起，避免每个 chunk 都重试
                    if is_first_chunk and self.tts_thread and (
                        not self.tts_thread.is_alive()
                        or not self._tts_runtime_is_current(self._snapshot_tts_runtime())
                    ):
                        self._respawn_tts_worker()

    def _set_conversation_turn_language(self, language: str | None) -> None:
        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.set_language(language)

    def _note_user_turn(self, *, text: str | None = None, now: float | None = None) -> None:
        # Master 情绪画像：异步分析用户这轮说的话（节流 + 开关都在 tracker 内部）。
        # 语音转写 / 文本输入两条路径的对偶 chokepoint。fire-and-forget、best-effort，
        # 绝不阻塞 turn 记录、不让分析异常冒泡。
        _me = getattr(self, '_master_emotion', None)
        if _me is not None:
            # 先 snapshot「本轮 analyze 启动前」的读数，供 _focus_inline_decision 用，
            # 保证 emotion 信号滞后一拍（当前 turn 不消费下面这次 analyze 的结果）。
            # 读 .latest 已含 TTL / 开关 gate。
            self._focus_emotion_reading = _me.latest
        if text and text.strip() and _me is not None:
            try:
                # Pass the session language: it is the frontend's i18n truth, while
                # the process-wide value is the Steam/system locale -- they disagree
                # whenever the user picks a different language inside the app.
                self._fire_task(_me.analyze(
                    text, now=now, ui_language=getattr(self, 'user_language', None),
                ))
            except Exception as _me_err:
                logger.debug("[%s] master emotion fire failed: %s", self.lanlan_name, _me_err)

        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.note_user_message(text=text, now=now)
            return
        if now is None:
            self._activity_tracker.on_user_message(text=text)
        else:
            self._activity_tracker.on_user_message(text=text, now=now)

    def _note_ai_turn(self, *, text: str | None = None, now: float | None = None) -> None:
        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.note_ai_message(text=text, now=now)
            return
        if now is None:
            self._activity_tracker.on_ai_message(text=text)
        else:
            self._activity_tracker.on_ai_message(text=text, now=now)

    def _reset_ai_turn_buffer(self) -> None:
        """Clear buffered AI text and its identity without changing discard state."""
        self._current_ai_turn_text = ''
        self._current_ai_turn_id = ''
        self._current_ai_turn_started_at = 0.0
        self._current_ai_turn_type = None
        self._current_ai_turn_client_owned = False

    def _flush_ai_turn_text_to_tracker(self, *, turn_type: str | None = None) -> None:
        """Flush the per-turn AI text buffer into conversation turn sinks.

        Called from each AI-turn-end exit point — there are three:
          - ``_emit_turn_end`` for regular replies (and truncate-recovery)
          - ``handle_proactive_complete`` for the agent direct-reply path
          - ``finish_proactive_delivery`` for /api/proactive_chat success

        The activity sink runs the question heuristic over the text and
        (when text is non-empty) bumps ``_conv_seq`` for open_threads cache
        invalidation. Other sinks, such as background topic collection, see
        the same turn without living inside ``UserActivityTracker``.

        ``turn_type`` is also the conversation-bus label
        (``assistant_message`` / ``proactive_reply``).
        """
        # 文本与轮次身份一起取快照再清空：flush 之后旧 id 不能留给下一轮，
        # 也不能被恢复路径的补记文本沿用。
        if turn_type is None:
            turn_type = getattr(self, "_current_ai_turn_type", None) or (
                "proactive_reply"
                if getattr(getattr(self, "state", None), "owner", None) is TurnOwner.PROACTIVE
                else "assistant_message"
            )
        ai_text = self._current_ai_turn_text
        turn_id = getattr(self, "_current_ai_turn_id", "")
        started_at = float(getattr(self, "_current_ai_turn_started_at", 0.0) or 0.0)
        client_owned = getattr(self, "_current_ai_turn_client_owned", False)
        self._note_ai_turn(text=ai_text or None)
        self._reset_ai_turn_buffer()
        self._discarded_turn_open = False
        self._publish_ai_message_to_plugin_bus(
            ai_text, turn_type, turn_id=turn_id, started_at=started_at,
            client_owned=client_owned,
        )

    def _publish_ai_message_to_plugin_bus(
        self,
        text: Optional[str],
        turn_type: str,
        *,
        turn_id: str = "",
        started_at: float = 0.0,
        client_owned: bool = False,
    ) -> None:
        """Publish one finished AI turn to the plugin conversation bus.

        Plugins need the whole sentence plus when it was spoken. This store
        previously only received proactive turns from the offline client, so
        realtime replies were invisible; publishing here (paired with the user
        side in ``_publish_user_utterance_to_plugin_bus``) lets plugins read
        both sides of every turn — ordered by ``metadata.ts`` — without
        touching host files or competing for the frontend websocket.

        ``ts`` is the first-chunk time, not the flush time: an interrupted turn
        would otherwise be stamped after the user's next message.
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        # Snapshot at the first chunk: interruption/session close can flush from
        # a different task, whose context no longer carries the proactive sid.
        if client_owned:
            return
        try:
            finished_at = time.time()
            spoken_at = float(started_at or 0.0) or finished_at
            resolved_turn_id = str(turn_id or "")
            self._fire_task(publish_conversation_turn_observed_best_effort(
                self.lanlan_name,
                content=cleaned,
                turn_type=str(turn_type or "assistant_message"),
                conversation_id=resolved_turn_id,
                source="main_logic.core",
                message_count=1,
                metadata={"role": "cat", "ts": spoken_at, "ts_end": finished_at},
                ts=spoken_at,
            ))
        except Exception:
            logger.debug("[plugin-bus] ai message not published", exc_info=True)

    async def _interrupt_offline_reply(self, session) -> bool:
        """Stop ``session``'s reply and close it as its own AI turn.

        Every interruption point goes through here rather than awaiting
        ``handle_interruption()`` itself: an interruption takes the reply's
        close over from its completion (which then does not run), so it must
        be followed by the close, or that reply never gets a turn end.
        Returns whether a reply was interrupted.

        An interrupted reply's completion will not run, so the wrap-up it
        would have run (``_finalize_turn_after_emit``: renewal check, final
        swap, queued agent callbacks) is recorded as owed rather than run
        here: a newer reply is usually about to stream, and running it now
        could start a final swap or clear the renewal cache mid-reply. The
        next finalize pays it (the new turn's own completion, as before this
        change); when none comes, ``_settle_owed_turn_wrap_up`` pays it once
        the offline session is idle. Only a "response" reply owes one:
        ``handle_proactive_complete`` never runs that wrap-up.
        """
        interrupt = getattr(session, "handle_interruption", None)
        if not callable(interrupt):
            return False
        try:
            kind = await interrupt()
        except asyncio.CancelledError as exc:
            # This task was cancelled while the interruption waited for the
            # reply's task (an ASR detector worker closed on an error or a
            # restart): the reply is taken over all the same, so close it now,
            # and send its frontend notice before the cancellation goes on, as
            # below, so it lands before any newer reply. The sends are best
            # effort and raise nothing; a second cancellation only cuts one
            # short.
            taken_over = getattr(exc, "interrupted_reply", None)
            if taken_over:
                frontend_send = self._close_taken_over_offline_reply(
                    taken_over, displaced=False,
                )
                if frontend_send is not None:
                    await frontend_send()
            raise
        if not kind:
            return False
        # Every caller starts its reply only after this returns, after its
        # user_activity, so the frontend notice lands before anything newer.
        # No TTS done: the interrupting path clears the pipeline itself.
        frontend_send = self._close_taken_over_offline_reply(kind, displaced=False)
        if frontend_send is not None:
            await frontend_send()
        return True

    def _close_taken_over_offline_reply(self, kind, *, displaced: bool):
        """Close an Offline reply whose close was taken over from its completion.

        The one rule for both takers: an interrupter
        (``_interrupt_offline_reply``) and a user reply that began over it
        (``_close_displaced_offline_turn``, ``displaced``). ``kind`` is what
        was handed over (``OmniOfflineClient.InterruptedReply``: the kind of
        turn end the skipped completion would have sent, whether the reply
        had streamed to the end, and the snapshot of a bound reply). The
        reply is closed as its own AI turn (``_close_interrupted_offline_turn``)
        and a "response" records its wrap-up as owed: the completion that
        would have run it will not run.

        Returns what the frontend still needs, as a callable returning an
        awaitable, or None:

        - the frontend copy of the reply's turn end, which seals the
          frontend's current bubble, when the reply said something and that
          bubble is still its own: a reply that streamed to the end and was
          only waiting on its completion (``kind.finished``: a claim leaves
          no live generation, and an interrupter starts its reply only after
          this), and any displaced reply, finished or cut mid-stream: the
          displacing reply has emitted nothing yet, and no user_activity came
          after the displaced reply's text. The turn end also releases the
          request a bound reply was for.
        - otherwise ``turn abandoned`` for the request a bound reply was for,
          which only releases what the frontend held for it: a reply an
          interrupter cut mid-stream is left to the interruption (its user
          input has split the bubbles), and one that said nothing has no
          bubble to seal.
        - nothing for an unbound reply: it answers no request of its own.

        A bound reply whose turn already ended (``_ReplyTurn.turn_ended``: a
        final discard sent its turn end while its generation was still live)
        is not closed again: the frontend already has (or is being sent) its
        turn end. Only its request id is released, as a close would, should
        the discard not have reached its own release yet, and nothing is
        returned. Every bound reply taken over is marked ``taken_over``; a
        discard recovery still running for it reads that, stops sending, and
        records and settles its own wrap-up (``_settle_owed_turn_wrap_up``)
        rather than running it inside the taker's hold.
        """
        owner = _taken_over_reply_turn(kind)
        if owner is not None:
            owner.taken_over = True
        if owner is not None and owner.turn_ended:
            if owner.request_id and self._active_text_request_id == owner.request_id:
                self._active_text_request_id = None
            return None
        finished = getattr(kind, "finished", False) is True
        taken_over = kind if isinstance(kind, str) else "response"
        is_response = str(taken_over) == "response"
        turn_end_msg = self._close_interrupted_offline_turn(taken_over)
        if is_response:
            self._turn_wrap_up_owed = True
        if isinstance(turn_end_msg, dict) and (finished or displaced):
            return lambda: self._send_turn_end_to_frontend(turn_end_msg)
        if is_response and owner is not None and owner.request_id:
            request_id = owner.request_id
            return lambda: self._send_turn_abandoned(request_id)
        return None

    async def _send_turn_abandoned(self, request_id) -> None:
        """Tell the frontend the reply to ``request_id`` was cut off.

        Not a ``turn end``: the reply was cut mid-stream, and the frontend
        seals whatever bubble is current on a turn end, which the
        interrupting turn is about to take over. This only releases what was
        held for ``request_id`` (its rollback draft and the last-submitted
        marker), which the skipped completion's turn end used to do. Best
        effort.
        """
        try:
            if self._has_connected_websocket():
                await self.websocket.send_json({'type': 'system', 'data': 'turn abandoned', 'request_id': request_id})
        except Exception as e:
            logger.debug("[%s] turn abandoned not sent: %s", self.lanlan_name, e)

    async def _settle_owed_turn_wrap_up(self) -> None:
        """Pay an interrupted reply's owed wrap-up once nothing else will.

        Runs when the offline session reports it went idle
        (``_on_offline_session_idle``), when a typed input (a command that
        starts no reply included) or a mini-game command has been handled
        (``_with_owed_wrap_up_held``), and when an independent-ASR voice turn
        ends (``_abandon_core_voice_turn``). Left owed while one of
        those is still being handled (a typed input's or a voice turn's reply
        completion pays it, and a wrap-up between the interruption and that
        reply could start a final swap right before it, or while the user is
        still speaking; a command seals its own turn first), and while the
        offline session is not idle: a reply live, guard-paused, waiting on
        its completion, or a cancelled one still finishing its task (a tool
        handler, its history commit). The session's idle notification pays
        it once that drains. Nothing is paid without a live session: a
        torn-down one has its debt reset (``_init_renew_status``).
        """
        if not getattr(self, "_turn_wrap_up_owed", False):
            return
        if self.session is None or not self.is_active:
            return
        if getattr(self, "_reply_setup_depth", 0) > 0:
            return
        if self._voice_turn_holds_owed_wrap_up():
            return
        session = self.session
        if isinstance(session, OmniOfflineClient) and not session.is_idle():
            return
        await self._finalize_turn_after_emit()

    async def _with_owed_wrap_up_held(self, work, *, skip_settle_if=None):
        """Await ``work`` holding an owed wrap-up, then settle it.

        For input handling that interrupts the offline reply and then does
        its own work over several awaits: a typed input setting up its reply
        (``_process_stream_input``), a mini-game command sealing its own turn
        (``_maybe_handle_mini_game_magic_command``). The interrupted reply's
        task can end in that window, and its idle notification must not pay
        the wrap-up then. Settled once ``work`` is done, unless it returned
        ``skip_settle_if`` (the input was deferred and its replay settles); a
        cancelled ``work`` settles in a task of its own, since the session it
        interrupted may well live on and go idle no more. A failing settle
        never replaces an error ``work`` raised. Returns what ``work``
        returned.
        """
        self._reply_setup_depth = getattr(self, "_reply_setup_depth", 0) + 1
        cancelled = False
        result = None
        try:
            result = await work
            return result
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            self._reply_setup_depth -= 1
            deferred = skip_settle_if is not None and result is skip_settle_if
            if cancelled:
                # In a task of its own, and never in place of the
                # cancellation this finally is carrying.
                if getattr(self, "_turn_wrap_up_owed", False):
                    try:
                        self._fire_task(self._settle_owed_turn_wrap_up())
                    except Exception as settle_error:
                        logger.warning(
                            "[%s] owed turn wrap-up after a cancelled input failed: %s",
                            self.lanlan_name,
                            settle_error,
                        )
            elif not deferred:
                try:
                    await self._settle_owed_turn_wrap_up()
                except Exception as settle_error:
                    logger.warning(
                        "[%s] owed turn wrap-up after an input failed: %s",
                        self.lanlan_name,
                        settle_error,
                    )

    def _voice_turn_holds_owed_wrap_up(self) -> bool:
        """Whether an independent-ASR voice turn still holds the owed wrap-up.

        The voice counterpart of ``_with_owed_wrap_up_held``, for a turn that
        interrupts the offline reply at speech onset and gets its reply only
        once the user has finished speaking. The hold is the turn's id
        (``_voice_turn_wrap_up_hold``), set before that interruption and
        released when the turn ends (``_abandon_core_voice_turn``, which
        every end of a Core voice turn goes through, after its reply call
        has returned); the reply's own completion pays the debt. A newer
        voice turn replaces the hold, ``_init_renew_status`` clears it, and
        a hold whose turn record is gone (every end of a turn drops its
        record) no longer counts and is dropped here, so a missed release
        cannot keep the debt unpaid.
        """
        turn_id = getattr(self, "_voice_turn_wrap_up_hold", None)
        if turn_id is None:
            return False
        if turn_id in (getattr(self, "_core_multimodal_turns", None) or {}):
            return True
        self._voice_turn_wrap_up_hold = None
        return False

    def _on_offline_session_idle(self) -> None:
        """``OmniOfflineClient.on_idle``: its last reply call returned.

        Sync (called from the client's own task): an owed wrap-up is paid in
        a task of its own, which checks the session again when it runs.
        """
        if getattr(self, "_turn_wrap_up_owed", False):
            self._fire_task(self._settle_owed_turn_wrap_up())

    def _close_interrupted_offline_turn(self, kind: str = "response") -> dict | None:
        """Close an offline reply whose close was handed over to the caller.

        ``kind`` is what ``handle_interruption()`` (or a displacing begin)
        reported: the turn end the skipped completion would have sent. When
        it carries the reply's own snapshot (``InterruptedReply.owner``, the
        ``_ReplyTurn`` Core handed over with the reply), the close uses that
        reply's request id and meta, never the shared fields, which by now
        may hold another reply's (a newer typed input's id, or the meta a
        retired client's parked avatar reply staged), and releases the shared
        ones only while they still hold its own. An unbound reply
        (independent-ASR voice, proactive) answers no request: only a typed
        input sets ``_active_text_request_id`` and every typed reply is
        bound, so whatever that field holds is a typed turn's (one still
        setting up its reply, under the cut one) and is left alone. An
        unbound "response" still carries the pending turn meta.
        "agent_callback" is a proactive reply, closed with ``turn end
        agent_callback`` as ``handle_proactive_complete`` would, so
        cross_server does not send an analyze request for it. Without a turn
        end, the text it already said is glued onto the next reply's AI turn
        in the activity tracker, and ``cross_server`` never leaves its
        assistant turn: the next reply lands in the same memory message and
        the interrupting user input is written after it.

        Runs before the next reply emits anything. For a response it carries
        any pending turn meta (an interrupted avatar-interaction reply still
        owns it: the avatar path clears it only after prompt_ephemeral
        returns), so cross_server keeps that text on the isolated avatar
        path, off ordinary memory. A bound reply's request id is retired
        (what the completion would have done) and its turn end carries it.
        It flushes the tracker and puts a sync-only turn end on the queue;
        the WebSocket and TTS are the caller's business. Nothing said since
        the last turn end means no turn end to send, but the request id and
        the turn meta are taken either way, so neither can ride the next
        turn's turn end. A discard that emptied the AI-turn buffer did not
        end cross_server's turn (``_discarded_turn_open``), so that still
        counts as said. Returns the turn end it queued, or None.
        """
        owner = _taken_over_reply_turn(kind)
        kind = str(kind) if isinstance(kind, str) else "response"
        said = bool(self._current_ai_turn_text) or getattr(
            self, "_discarded_turn_open", False
        )
        if kind == "agent_callback":
            if not said:
                return None
            self._flush_ai_turn_text_to_tracker()
            return self._queue_agent_callback_turn_end()
        request_id = None
        if owner is not None:
            request_id = owner.request_id
            if request_id and self._active_text_request_id == request_id:
                self._active_text_request_id = None
        if not said:
            if owner is None or (
                owner.meta is not None and self._pending_turn_meta is owner.meta
            ):
                self._pending_turn_meta = None
            return None
        turn_end_msg = self._queue_turn_end(request_id, reply_turn=owner)
        self._flush_ai_turn_text_to_tracker()
        return turn_end_msg

    def _close_displaced_offline_turn(self, kind: str = "response"):
        """``OmniOfflineClient.on_response_displaced``: a user reply began
        over this one without an interruption and handed its close here.

        Called synchronously from the new reply's begin, before it emits
        anything, so the AI-turn buffer holds only the displaced reply's
        text, and closed by the same rule as an interrupted one
        (``_close_taken_over_offline_reply``). Unlike an interruption, no
        user_activity came between the displaced reply's text and the new
        reply, so the frontend's current bubble is still the displaced
        reply's whether it streamed to the end or was cut mid-stream: it gets
        the frontend copy of its turn end, which seals that bubble before the
        new reply starts its own (and releases a bound reply's request). This
        callback is sync, so it returns that send for the client to await
        right after the begin, before the new reply sends its provider
        request: it reaches the WebSocket before that reply's first chunk,
        however long the send itself takes (a task could lose that race).
        Returns None when there is nothing to send. No TTS done: the new
        reply streams on the current speech id.
        """
        return self._close_taken_over_offline_reply(kind, displaced=True)

    async def handle_proactive_complete(self, content_committed: bool = True):
        """Lightweight completion for proactive (agent callback) replies.

        Only flushes TTS and sends turn_end to the frontend so that the
        realistic-queue buffer is flushed.  Does NOT trigger hot-swap,
        analyze_request, or agent-callback re-delivery — those belong
        exclusively to user-initiated conversation turns.
        """
        if not content_committed:
            logger.debug("[%s] handle_proactive_complete: no content committed, skipping completion flush", self.lanlan_name)
            return
        # Activity tracker flush：proactive 也算 AI 在说话。和 _emit_turn_end
        # 对称，让 seconds_since_ai_msg 不分主动/被动。proactive 文本同样走过
        # send_lanlan_response（finish_proactive_delivery 内部会调），所以
        # _current_ai_turn_text 已经累加好。
        self._flush_ai_turn_text_to_tracker(turn_type="proactive_reply")
        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            try:
                await self._request_tts_done_for_turn("handle_proactive_complete")
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败 (proactive): {e}")
        turn_end_msg = self._queue_agent_callback_turn_end()
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json(turn_end_msg)
                logger.debug("[%s] handle_proactive_complete: turn_end (agent_callback) sent to frontend", self.lanlan_name)
            else:
                logger.warning("[%s] handle_proactive_complete: websocket not connected, turn_end NOT sent", self.lanlan_name)
        except Exception as e:
            logger.warning("[%s] handle_proactive_complete: WS send turn_end error: %s", self.lanlan_name, e)

    def _queue_turn_end(
        self,
        request_id,
        *,
        reply_turn: _ReplyTurn | None = None,
    ) -> dict:
        """Put a ``turn end`` for ``request_id`` on the sync queue and return it.

        The one builder for the memory side of a regular turn end:
        ``_emit_turn_end`` adds the frontend copy, a handed-over reply's close
        may not. It carries ``_pending_turn_meta`` and consumes it; a bound
        reply (``reply_turn``) carries the meta it was started with instead,
        never takes a ``_pending_turn_meta`` another reply staged, and clears
        the shared field only while that still holds its own.
        """
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end'}
        pending_meta = (
            getattr(self, '_pending_turn_meta', None)
            if reply_turn is None else reply_turn.meta
        )
        if pending_meta:
            turn_end_msg['meta'] = pending_meta
            if getattr(self, '_pending_turn_meta', None) is pending_meta:
                self._pending_turn_meta = None
        if request_id:
            turn_end_msg['request_id'] = request_id
        self.sync_message_queue.put(turn_end_msg)
        if reply_turn is not None:
            reply_turn.turn_ended = True
        return turn_end_msg

    def _queue_agent_callback_turn_end(self, request_id=None) -> dict:
        """Put a ``turn end agent_callback`` on the sync queue and return it.

        The one builder for the memory side of a proactive reply's turn end
        (``handle_proactive_complete``, the close of a taken-over proactive
        reply, a command's own turn), the counterpart of ``_queue_turn_end``:
        cross_server ends the assistant turn without an analyze request.
        ``_send_turn_end_to_frontend`` sends its WebSocket copy."""
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end agent_callback'}
        if request_id:
            turn_end_msg['request_id'] = request_id
        if self.sync_message_queue:
            self.sync_message_queue.put(turn_end_msg)
        return turn_end_msg

    async def _send_turn_end_to_frontend(self, turn_end_msg: dict) -> None:
        """The WebSocket copy of a turn end already on the sync queue: a
        ``turn end`` always names its request id (None included) and carries
        the same meta; ``turn end agent_callback`` is sent as is."""
        if turn_end_msg['data'] == 'turn end':
            ws_msg: dict = {
                'type': 'system',
                'data': 'turn end',
                'request_id': turn_end_msg.get('request_id'),
            }
            if 'meta' in turn_end_msg:
                ws_msg['meta'] = turn_end_msg['meta']
        else:
            ws_msg = dict(turn_end_msg)
        try:
            if self._has_connected_websocket():
                await self.websocket.send_json(ws_msg)
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")

    async def _emit_turn_end(
        self,
        active_request_id,
        *,
        reply_turn: _ReplyTurn | None = None,
    ) -> None:
        """Send the turn end signal to both sync_message_queue and the WebSocket,
        passing ``_pending_turn_meta`` through both channels before clearing it.
        Shared by two paths:
        - ``handle_response_complete`` normal completion
        - ``handle_response_discarded``'s truncate-recovery / too-long-final
        Unified semantics: sync queue and WS carry the same meta, avoiding one
        having meta while the other doesn't.

        A bound reply (``reply_turn``) carries the meta it was started with
        instead (see ``_queue_turn_end``), and its ``turn_ended`` is set once
        the turn end is queued, so the completion that follows a final
        discard does not end the turn again."""
        turn_end_msg = self._queue_turn_end(active_request_id, reply_turn=reply_turn)
        # Activity tracker flush：AI 刚结束一轮（普通完成 + truncate-recovery 都
        # 走这里）。text 用于 unfinished_thread 检测——tracker 跑问号启发式决定
        # 要不要开 5min 跟进窗口；为 None 时不开窗，但仍更新 seconds_since_ai_msg。
        # 必须在下面等 WS 发送之前：等的这段时间里新一轮可能已经开始往这个
        # buffer 里写，放在后面就会把新一轮的半段算进这一轮、再把它清掉。
        self._flush_ai_turn_text_to_tracker()
        await self._send_turn_end_to_frontend(turn_end_msg)

    async def _emit_agent_callback_turn_end(self, active_request_id) -> None:
        turn_end_msg = self._queue_agent_callback_turn_end(active_request_id)
        await self._send_turn_end_to_frontend(turn_end_msg)

    def _mark_magic_command_image_drop_request(self, request_id: object) -> None:
        request_id_str = str(request_id or "")
        if not request_id_str or request_id_str in self._magic_command_image_drop_request_ids:
            return
        self._magic_command_image_drop_request_ids.add(request_id_str)
        self._magic_command_image_drop_request_order.append(request_id_str)
        while len(self._magic_command_image_drop_request_order) > _MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX:
            stale_request_id = self._magic_command_image_drop_request_order.popleft()
            self._magic_command_image_drop_request_ids.discard(stale_request_id)

    def _should_drop_magic_command_image(self, request_id: object) -> bool:
        request_id_str = str(request_id or "")
        return bool(request_id_str and request_id_str in self._magic_command_image_drop_request_ids)

    def _record_request_staged_image(self, request_id: object, image: object) -> None:
        """Remember which request staged ``image`` into the offline attachment queue.

        ``_pending_images`` carries no request identity, so a consumed slash
        command needs this ledger to remove only its own already-staged
        attachments. Entries whose image has already left the queue are pruned
        on every record and whenever a text turn has claimed the queue, so the
        ledger never keeps a consumed frame alive.
        """
        request_id_str = str(request_id or "")
        if not request_id_str:
            return
        self._prune_request_staged_images()
        self._request_staged_images.append((request_id_str, image))

    def _prune_request_staged_images(self) -> None:
        """Drop ledger entries whose image is no longer in the session's attachment queue."""
        ledger = getattr(self, "_request_staged_images", None)
        if ledger is None:
            self._request_staged_images = deque()
            return
        if not ledger:
            return
        pending = getattr(self.session, "_pending_images", None)
        if not isinstance(pending, list):
            ledger.clear()
            return
        live = [
            (rid, staged) for rid, staged in ledger
            if any(queued is staged for queued in pending)
        ]
        ledger.clear()
        ledger.extend(live)

    def _discard_request_staged_images(self, request_id: object) -> None:
        """Remove attachments this request already staged; other requests' images stay."""
        request_id_str = str(request_id or "")
        ledger = getattr(self, "_request_staged_images", None)
        if not request_id_str or not ledger:
            return
        pending = getattr(self.session, "_pending_images", None)
        if isinstance(pending, list):
            for rid, staged in ledger:
                if rid != request_id_str:
                    continue
                for index, queued in enumerate(pending):
                    if queued is staged:
                        del pending[index]
                        break
        self._prune_request_staged_images()

    def _begin_reply_turn(
        self,
        *,
        speech_id: str | None,
        request_id: str | None = None,
        meta: dict | None = None,
    ) -> _ReplyTurn:
        """Open the snapshot for a reply Core is about to hand to the Offline client.

        It becomes the open reply that ``_carry_reply_turn`` moves along; the
        caller attaches ``session`` right before the hand-over and passes the
        snapshot to ``_end_reply_turn`` once the reply has returned.
        """
        reply_turn = _ReplyTurn(speech_id=speech_id, request_id=request_id, meta=meta)
        self._open_reply_turn = reply_turn
        return reply_turn

    def _end_reply_turn(self, reply_turn: _ReplyTurn) -> None:
        if self._open_reply_turn is reply_turn:
            self._open_reply_turn = None

    def _carry_reply_turn(self, previous_speech_id: str | None) -> None:
        """Move the open reply onto the speech id that just replaced ``previous_speech_id``.

        Only for the two rotations that start no turn. After the hot-swap
        promotion, a reply the old session was cut off in is still the host's
        turn, and its late completion is the only thing that closes it. A
        truncation recovery re-speaks the same reply under a fresh id, and its
        remaining steps must keep treating that reply as the owner.
        """
        reply_turn = getattr(self, "_open_reply_turn", None)
        if reply_turn is not None and reply_turn.speech_id == previous_speech_id:
            reply_turn.speech_id = self.current_speech_id

    def _reply_turn_is_current(self, reply_turn: _ReplyTurn) -> bool:
        """Does this reply still own the host's turn?

        While its client is still ``self.session`` the answer is yes, as before
        replies carried a snapshot: turn succession on a live client belongs to
        the interruption path, which cancels the reply before the interrupter's
        turn starts. A retired client (closed by ``end_session`` or a hot swap)
        has no such hand-over, so its reply owns the turn only while the host is
        still on the speech id it spoke under. Every writer that starts a turn
        assigns a fresh speech id, and the rotations that start no turn (the
        hot-swap promotion, a truncation recovery) carry the open reply along.

        Client identity alone cannot decide it: ``end_session`` closes the client
        before it clears ``self.session``, and after a hot swap the retired
        client's completion is still the only thing that closes the turn it cut.
        """
        if reply_turn.session is self.session:
            return True
        return self.current_speech_id == reply_turn.speech_id

    def _stand_down_reply_turn(self, reply_turn: _ReplyTurn) -> None:
        """Leave the host's newer turn alone; release only the request id this reply owns.

        A newer voice or proactive turn does not replace
        ``_active_text_request_id``, so the id may still be this reply's and
        would otherwise ride on that turn's end. A staged meta is left to the
        path that staged it (the avatar path clears its own once it sees the
        speech id moved on).
        """
        logger.info(
            "[%s] a retired reply completed after the host moved to a newer turn "
            "(request_id=%s); leaving that turn alone",
            self.lanlan_name,
            reply_turn.request_id,
        )
        if self._active_text_request_id == reply_turn.request_id:
            self._active_text_request_id = None

    async def handle_response_complete(self, *, reply_turn: _ReplyTurn | None = None):
        """Qwen completion callback: handles the Core API's response-complete event, including TTS and hot-swap logic.

        ``reply_turn`` is set when Core bound the callback to one Offline reply
        (``_ReplyTurn``). Such a completion ends the turn with its own request id
        and meta rather than whatever the shared fields hold when it finally
        runs, and when a newer turn already owns the host
        (``_reply_turn_is_current``) it leaves every shared effect (TTS done,
        turn end, AI text flush, wrap-up) to that turn. When a final discard
        already ended its turn (``_ReplyTurn.turn_ended``) it does nothing: the
        discard sent that turn end and has settled the wrap-up itself, or
        recorded it as owed when the reply was taken over meanwhile.

        Unbound completions read the shared fields as they stand: realtime
        clients, which guard their own turn ends, and the Offline replies Core
        does not bind (independent-ASR voice turns, whose completion runs inside
        ``close()`` rather than after it, and proactive replies without
        ``on_proactive_done``). An unbound reply keeps no record of a discard's
        turn end, so after a final discard it still ends the turn a second
        time. Skipping the completion on the client side instead would lose the
        only close whenever the discard stood down without ending the turn.
        """
        if reply_turn is not None and reply_turn.turn_ended:
            return
        # 先于接管清理：已经不拥有这一轮的迟到回调也不该去清接管方自己的
        # TTS（镜像台词）和簿记。
        if reply_turn is not None and not self._reply_turn_is_current(reply_turn):
            self._stand_down_reply_turn(reply_turn)
            return

        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime response completion", self.lanlan_name)
            await self._clear_tts_pipeline()
            self._pending_turn_meta = None
            self._reset_ai_turn_buffer()
            self._discarded_turn_open = False
            self._active_text_request_id = None
            return

        if reply_turn is None:
            active_request_id = self._active_text_request_id
            tts_expected_speech_id = None
        else:
            active_request_id = reply_turn.request_id
            tts_expected_speech_id = reply_turn.speech_id

        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            logger.info("📨 Response complete (LLM 回复结束)")
            try:
                await self._request_tts_done_for_turn(
                    "handle_response_complete",
                    expected_speech_id=tts_expected_speech_id,
                )
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败: {e}")
            # 上面等 tts_cache_lock 的这段时间里可能开了新一轮：复查一次。
            if reply_turn is not None and not self._reply_turn_is_current(reply_turn):
                self._stand_down_reply_turn(reply_turn)
                return
        try:
            await self._emit_turn_end(active_request_id, reply_turn=reply_turn)
        finally:
            # Compare-and-clear：仅在共享字段仍是本轮快照时才清空，避免
            # 抹掉用户在 turn end 发出前提交的新轮 request_id。
            if self._active_text_request_id == active_request_id:
                self._active_text_request_id = None

        # 发 WS turn end 那一下也会让出：期间开始的新一轮自己会收尾。
        if reply_turn is not None and not self._reply_turn_is_current(reply_turn):
            self._stand_down_reply_turn(reply_turn)
            return
        await self._finalize_turn_after_emit()

    async def _finalize_turn_after_emit(self) -> None:
        """Unified wrap-up after turn end: renew/prewarm decision + agent callback delivery.

        Shared by ``handle_response_complete`` and the recovery / too-long-final
        branches of ``handle_response_discarded``, so that consecutive
        RESPONSE_LENGTH_TRUNCATED / RESPONSE_TOO_LONG runs don't skip session
        archiving/prewarming and fall into the "context grows → keeps truncating
        and recovering" loop.
        """
        self._turn_wrap_up_owed = False
        # ── 热切换逻辑 ─────────────────────────────────────────────────────────
        # 正在切换过程中则跳过所有热切换判断
        if not self.is_hot_swap_imminent:
            try:
                # 1. 轮次 / 上下文 token 任一满足 → 准备新 session + 记忆归档。
                #    （已删除 elapsed >= 40s 的纯时间触发：长时间发呆不应强制
                #     归档 cache，由 turn / token 真实驱动。）
                if hasattr(self, 'is_preparing_new_session') and not self.is_preparing_new_session:
                    _turn_threshold_met = self._session_turn_count >= SESSION_TURN_THRESHOLD
                    # Session 历史 token 总量阈值。turn-end 后的冷路径，
                    # sync count_tokens 即可（10 条消息合计 < 50ms）。
                    # m.content 在多模态消息下是 list[dict]（含 image_url base64）；
                    # 直接 str() 会把 base64 一起算进 budget，带图对话会被过早判定。
                    # 这里只统计可见文本部分。
                    if isinstance(self.session, OmniOfflineClient):
                        from utils.tokenize import count_tokens as _ct

                        def _budget_text(message) -> str:
                            content = getattr(message, "content", "")
                            if isinstance(content, str):
                                return content
                            if isinstance(content, list):
                                return "\n".join(
                                    str(part.get("text") or "").strip()
                                    for part in content
                                    if isinstance(part, dict)
                                    and str(part.get("type") or "") in {"text", "input_text", "output_text"}
                                )
                            return ""

                        _ctx_total = sum(
                            _ct(_budget_text(m))
                            for m in self.session._conversation_history[1:]
                        )
                        _ctx_threshold_met = _ctx_total >= SESSION_ARCHIVE_TRIGGER_TOKENS
                    else:
                        _ctx_threshold_met = False
                    if _turn_threshold_met or _ctx_threshold_met:
                        logger.info(
                            "[%s] Main Listener: session renewal threshold met "
                            "(turns=%d/%d turn_threshold=%s context_tokens=%d/%d "
                            "context_threshold=%s). Marking for new session preparation.",
                            self.lanlan_name,
                            self._session_turn_count,
                            SESSION_TURN_THRESHOLD,
                            _turn_threshold_met,
                            _ctx_total if isinstance(self.session, OmniOfflineClient) else 0,
                            SESSION_ARCHIVE_TRIGGER_TOKENS,
                            _ctx_threshold_met,
                        )
                        self.is_preparing_new_session = True
                        self.summary_triggered_time = datetime.now()
                        self.message_cache_for_new_session = []
                        self.initial_cache_snapshot_len = 0
                        self._primed_context_snapshot = None
                        self.initial_next_session_context_snapshot_len = 0
                        self.sync_message_queue.put({'type': 'system', 'data': 'renew session'})

                # 2. agent 任务结果即时触发（无需等待 40s）：有挂起的额外提示 → 立刻启动预热
                has_extra = bool(getattr(self, 'pending_extra_replies', None))
                if has_extra and not self.is_preparing_new_session:
                    await self._trigger_immediate_preparation_for_extra()

                # 3. 后台预热（10s 延迟，适用于定时触发路径；
                #    即时路径由 _trigger_immediate_preparation_for_extra 在内部直接启动，不走这里）
                if self.is_preparing_new_session and \
                        self.summary_triggered_time and \
                        (datetime.now() - self.summary_triggered_time).total_seconds() >= 10 and \
                        (not self.background_preparation_task or self.background_preparation_task.done()) and \
                        not (self.pending_session_warmed_up_event and self.pending_session_warmed_up_event.is_set()):
                    logger.info(f"[{self.lanlan_name}] Main Listener: Conditions met to start BACKGROUND PREPARATION of pending session.")
                    self.pending_session_warmed_up_event = asyncio.Event()
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())

                # 4. 后台预热完成 + 当前轮次结束 → 执行最终热切换
                elif self.pending_session_warmed_up_event and \
                        self.pending_session_warmed_up_event.is_set() and \
                        not self.is_hot_swap_imminent and \
                        (not self.final_swap_task or self.final_swap_task.done()):
                    logger.info(
                        "Main Listener: OLD session completed a turn & PENDING session is warmed up. Triggering FINAL SWAP sequence.")
                    self.is_hot_swap_imminent = True
                    self.pending_session_final_prime_complete_event = asyncio.Event()
                    self.final_swap_task = asyncio.create_task(
                        self._perform_final_swap_sequence()
                    )
            except Exception as e:
                logger.error(f"💥 Hot-swap preparation error: {e}")

        # After each turn: deliver any queued agent task callbacks via LLM rephrase
        if self.pending_agent_callbacks:
            self._fire_task(self.trigger_agent_callbacks())

    async def handle_response_discarded(
        self,
        reason: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        message: Optional[str] = None,
        *,
        request_id: Any = _REQUEST_ID_UNSET,
        reply_turn: _ReplyTurn | None = None,
    ):
        """
        Handle the response-discarded notification: clear the TTS pipeline + frontend output, sending turn end if necessary

        ``reply_turn`` binds the notification to one Offline reply the same way
        it binds that reply's completion: once a newer turn owns the host
        (``_reply_turn_is_current``), the discard no longer touches shared
        output. The request id alone cannot see a newer voice or proactive
        turn, which leaves ``_active_text_request_id`` as it was.
        """
        # 文本流在发起时显式绑定 request_id；旧调用者未传时才兼容回读共享字段。
        # 不能只在函数入口快照 self._active_text_request_id：旧请求 A 的回调若在
        # 新请求 B 启动后才进入本函数，入口快照本身就已经是 B，会让前端误取消 B。
        active_request_id = (
            self._active_text_request_id
            if request_id is _REQUEST_ID_UNSET
            else request_id
        )
        request_has_owner = (
            request_id is not _REQUEST_ID_UNSET
            and active_request_id is not None
        )

        def may_clear_shared_output() -> bool:
            # Legacy / proactive callbacks have no request owner and keep their
            # historical global-clear behavior. Request-bound callbacks may
            # mutate shared TTS/queue state only while their owner is current.
            # A reply whose close was taken over owns nothing any more, with
            # or without a request id to tell it so.
            return (
                not request_has_owner
                or self._active_text_request_id == active_request_id
            ) and (
                reply_turn is None
                or (self._reply_turn_is_current(reply_turn) and not reply_turn.taken_over)
            )

        logger.warning(f"[{self.lanlan_name}] 响应异常已丢弃 (reason={reason}, attempt={attempt}/{max_attempts}, will_retry={will_retry})")

        # 检测是否为 RESPONSE_TOO_LONG 最终丢弃 / RESPONSE_LENGTH_TRUNCATED 截断恢复
        _is_too_long_final = False
        _truncated_text = None  # 非 None 表示进入 reroll 耗尽后的"截断到句末"恢复路径
        if not will_retry and message:
            try:
                parsed = json.loads(message) if isinstance(message, str) else message
                if isinstance(parsed, dict):
                    if parsed.get('code') == 'RESPONSE_TOO_LONG':
                        _is_too_long_final = True
                    elif parsed.get('code') == 'RESPONSE_LENGTH_TRUNCATED':
                        candidate = parsed.get('text')
                        if isinstance(candidate, str) and candidate.strip():
                            _truncated_text = candidate
            except Exception as _parse_err:
                # message 可能含 RESPONSE_LENGTH_TRUNCATED.text（截断后的 AI 原文），
                # 不写进 logger；只记元数据，原文走 print 兜底。
                logger.debug(
                    f"[{self.lanlan_name}] response_discarded JSON 解析失败: {_parse_err} (msg_len={len(message or '')})"
                )
                print(f"[response_discarded parse_err] raw: {message!r}")

        # 被丢弃的这段回复前端已经 clear 掉了，用户没看到——不该作为"AI 说过的
        # 话"进 activity tracker 的 unfinished_thread 检测。_clear_tts_pipeline
        # 只管 TTS 队列和音频缓存，从不碰 _current_ai_turn_text，而这个 buffer
        # 唯一的出口是 turn end 的 _flush_ai_turn_text_to_tracker()，所以丢弃的
        # 文本会一直躺到下一次 turn end 才被算进去：
        #   - will_retry：重试后的文本继续往同一个 buffer 追加，turn end 时
        #     flush 的是"丢弃版 + 重发版"拼在一起的内容
        #   - recovery：截断恢复的正文追加在丢弃版后面，一并 flush
        # 清空而不是 flush：这一轮要么还会重试、要么下面 recovery 会补发正文，
        # 都还没到 turn end，flush 会凭空多喂 tracker 一个 AI turn。
        #
        # buffer 清空和 TTS 清理是"清掉本轮丢弃留下的共享输出"的两半，同受产权
        # 门控（#2534 合并时留的路标就是指这里）：文本请求由 websocket_router
        # 作为各自独立的后台任务分发，旧请求 A 的迟到 discard 可以落在新请求 B
        # 已经开始 publish 之后，无门控地清会连 B 的前缀一起抹掉。
        #
        # cross_server 的 response_discarded_clear 也是同一份共享输出，同步地
        # 和 buffer 一起清，必须赶在任何 await 之前：下面的 recovery 会先发正文
        # 和 turn end，再 compare-and-clear 掉 request id，等它跑完再判产权就
        # 恒为 False，clear 永远发不出去，cross_server 会把丢弃版和恢复正文
        # 一起写进记忆。
        #
        # 前端的 response_discarded 是同一份共享输出的另一半，要和上面的同步
        # 清除一起发；但它必须排在被丢弃回复的全部音频之后。所以 TTS 清理拆开：
        # 中断（worker 收到 __interrupt__、已排队的旧音频丢掉）和等它落地
        # （handler 手里已取出的那段音频在这期间发出、泄漏的再丢一次）都在
        # 通知之前；加锁的收尾在通知之后。TTS worker 不在时中断不用等，通知
        # 和同步清除之间没有 await。等待的那 20ms 里若被接管，通知不再发：
        # 接管方此时可能已经开始往前端输出（例如小游戏命令），过期的通知会
        # 清掉它的气泡和音频；代价是这种情况下前端气泡留着被丢弃的文本。
        # 每次 await 之后都重新确认产权，接管方会自己做完整清理。
        owns_shared_output = may_clear_shared_output()
        tts_interrupt = None
        if owns_shared_output:
            if self._current_ai_turn_text:
                # That text already reached cross_server, whose assistant turn
                # stays open until a turn end: should this reply's close be
                # taken over now, the close must still send one.
                self._discarded_turn_open = True
            self._reset_ai_turn_buffer()
            if self.sync_message_queue:
                self.sync_message_queue.put({
                    'type': 'system',
                    'data': 'response_discarded_clear'
                })
            tts_interrupt = self._interrupt_tts_now()
            await self._let_tts_interrupt_land(
                tts_interrupt, still_owned=may_clear_shared_output,
            )

        # A request-bound discard is only relevant while that request still owns
        # the shared response. Emitting a stale A notification after B becomes
        # active would make the frontend clear B's bubble, buffers, and audio.
        if owns_shared_output and may_clear_shared_output() and \
                self.websocket and hasattr(self.websocket, 'client_state') and \
                self.websocket.client_state == self.websocket.client_state.CONNECTED:
            try:
                await self.websocket.send_json({
                    "type": "response_discarded",
                    "reason": reason,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "will_retry": will_retry,
                    "message": message or "",
                    # 透传函数开头的 snapshot，避免新轮覆盖后串轮
                    "request_id": active_request_id,
                })
            except Exception as e:
                logger.warning(f"发送 response_discarded 到前端失败: {e}")

        # Rechecked: the frontend send above is an await, and a reply that took
        # this one over in it may already be feeding the TTS pipeline (the
        # taker clears what this reply left there itself).
        if owns_shared_output and may_clear_shared_output():
            await self._finish_tts_clear(tts_interrupt)

        # RESPONSE_TOO_LONG 最终丢弃时：发送可爱回复 + 用角色 TTS 音色念出来。
        # RESPONSE_LENGTH_TRUNCATED：reroll 耗尽后回退到最后句末标点截断的恢复路径，
        # 把截断后的文本当作正常回复重新喂给前端 + TTS（用户输入不回滚）。
        #
        # 这里要复用 handle_response_complete 的"turn 收尾"语义：
        #   - 消费 _pending_turn_meta：把它挂到 turn_end，再清空，避免漏挂或
        #     被下一轮 turn 误消费。
        #   - 尊重 ephemeral 语义：avatar_interaction 由 prompt_ephemeral
        #     (persist_response=False) 触发，本来不该写 _conversation_history；
        #     truncate-recovery / too-long-final 走到这里时不能强行 append。
        recovery_owns_shared_state = (
            (_is_too_long_final or _truncated_text is not None)
            and may_clear_shared_output()
        )
        if recovery_owns_shared_state:
            def _mark_recovery_published(_published_at) -> None:
                # The recovered body went to cross_server untracked (see
                # _track_recovery_ai_turn_text): from here an interruption
                # cutting the steps below must still end that turn there. Not
                # before: a body cut before it was published opened nothing.
                self._discarded_turn_open = True
                # History gets the body at the same moment, ownership just
                # confirmed (publish_if): a takeover in the send's await stops
                # the steps below, but what the user saw and memory recorded
                # still lands in history, before the taker's user message.
                _append_recovery_history()

            try:
                if _truncated_text is not None:
                    body_text = _truncated_text
                else:
                    body_text = _get_chat_locale_text(
                        self.user_language,
                        'responseTooLong',
                        "Response too long and was discarded; your input has been restored.",
                    )

                # 冻结本轮 recovery 用的 turn/speech id snapshot——后面所有
                # send_lanlan_response / feed_tts_chunk 都用这个本地变量，
                # 不再回读共享字段；否则用户在 response_discarded 发出后立刻
                # 提交下一条文本时，新轮会改写 self.current_speech_id，截断
                # 恢复出来的正文 + 音频会带着新轮的 turn_id 发出去，前端
                # （app-websocket.js assistant turn 生命周期是按 turn_id 建的）
                # 会把恢复内容和新轮串到一起。
                if self.use_tts:
                    async with self.lock:
                        replaced_speech_id = self.current_speech_id
                        recovery_turn_id = str(uuid4())
                        self.current_speech_id = recovery_turn_id
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                        # 同一轮只是换了 speech id，本回复仍是这一轮的主人。
                        self._carry_reply_turn(replaced_speech_id)
                else:
                    recovery_turn_id = self.current_speech_id

                async def _track_recovery_ai_turn_text() -> None:
                    # send_lanlan_response 的 track_ai_turn 累加是同步的、发生在
                    # 它内部任何 await 之前：A 若在那次 await 里让位给 B，这段
                    # 文本已经进了共享 buffer，而 A 随后 break、不再走
                    # _emit_turn_end flush，残留就会被算进 B 的 turn。所以
                    # recovery 这一路让 send 不 track，改由本步补记——它是同步
                    # 的、紧挨 _emit_turn_end，中间没有能丢产权的 await 窗口。
                    # 文本处理跟 send_lanlan_response 内部保持一致（剥表情标签）。
                    if not self._current_ai_turn_text:
                        self._current_ai_turn_started_at = time.time()
                        self._current_ai_turn_client_owned = (
                            _proactive_expected_sid.get() is not None
                            and isinstance(getattr(self, "session", None), OmniOfflineClient)
                        )
                    self._current_ai_turn_id = str(recovery_turn_id or '')
                    self._current_ai_turn_text += self.emotion_pattern.sub('', body_text)

                def _append_recovery_history() -> None:
                    # 仅当本轮**不是** ephemeral（即非 avatar_interaction 等
                    # persist_response=False 的路径）时才写历史。avatar_interaction
                    # 触发 RESPONSE_TOO_LONG/TRUNCATED 时本就该和 ephemeral 一致地
                    # 不留下 AIMessage 痕迹。
                    # 绑定了快照的回复只看自己起轮时的 meta，不拿别的回复挂上的。
                    pending_meta = (
                        self._pending_turn_meta if reply_turn is None else reply_turn.meta
                    )
                    is_ephemeral = bool(pending_meta) and pending_meta.get("kind") == "avatar_interaction"
                    if not is_ephemeral and self.session and hasattr(self.session, '_conversation_history'):
                        self.session._conversation_history.append(AIMessage(content=body_text))

                # recovery 的每一步都 await，其间用户可能提交新请求把产权抢走。
                # 写成顺序步骤表、由下面的循环统一在每步之后复验产权，而不是
                # 每步手写一个 if-return——后者在将来插入新步骤时容易漏掉一处，
                # 漏一处就等于让旧轮的正文 / 音频 / turn end 打到新轮上。
                recovery_steps = [
                    # 发送文本到前端显示。显式传 active_request_id snapshot，
                    # 避免 send_lanlan_response 内部回读共享字段时拿到新轮 id
                    # 串掉前端 rollback 绑定。
                    lambda: self.send_lanlan_response(
                        body_text,
                        is_first_chunk=True,
                        turn_id=recovery_turn_id,
                        request_id=active_request_id,
                        track_ai_turn=False,
                        on_published=_mark_recovery_published,
                        publish_if=may_clear_shared_output,
                    ),
                ]
                if self.use_tts:
                    # 喂给 TTS 管线用角色音色念。done 信号也要带
                    # expected_speech_id 校验，否则旧 recovery 的 done 会结束
                    # 新轮的 TTS（首句被截 / 整轮静音）。
                    recovery_steps.append(
                        lambda: self.feed_tts_chunk(body_text, expected_speech_id=recovery_turn_id)
                    )
                    recovery_steps.append(
                        lambda: self._request_tts_done_for_turn(
                            "handle_response_discarded:length_truncated"
                            if _truncated_text is not None
                            else "handle_response_discarded:too_long_final",
                            expected_speech_id=recovery_turn_id,
                        )
                    )
                # 补记 activity tracker 的 AI turn 文本，紧挨 turn end——见
                # _track_recovery_ai_turn_text 里的说明：放在这里才没有能丢产权
                # 的 await 窗口，前面任何一步 break 掉都不会给 B 留下残留。
                recovery_steps.append(_track_recovery_ai_turn_text)
                # turn end —— 复用 _emit_turn_end helper（同 handle_response_complete
                # 走同一套语义；sync queue 和 WS 都带相同 meta）。
                # 注：_append_recovery_history 读 pending_meta 已经触发 is_ephemeral
                # 判定，但这里 _emit_turn_end 自己会再读一次（同一个快照或共享
                # 字段）做透传 + 清空，二者读的是同一个值，幂等。
                recovery_steps.append(
                    lambda: self._emit_turn_end(active_request_id, reply_turn=reply_turn)
                )

                for recovery_step in recovery_steps:
                    await recovery_step()
                    if not may_clear_shared_output():
                        break
            except Exception as e:
                logger.warning(f"⚠️ {'RESPONSE_LENGTH_TRUNCATED' if _truncated_text is not None else 'RESPONSE_TOO_LONG'} 回复发送失败: {e}")
            finally:
                # Compare-and-clear：见函数顶部 active_request_id 快照说明。
                if self._active_text_request_id == active_request_id:
                    self._active_text_request_id = None

        if not will_retry and not _is_too_long_final and _truncated_text is None:
            # Compare-and-clear：仅当共享字段仍是本轮快照时才清空。
            if self._active_text_request_id == active_request_id:
                self._active_text_request_id = None

        # Recovery / too-long-final 路径相当于"这一轮 LLM 已完成"——必须
        # 跑跟 handle_response_complete 同款的 turn 后置流程（renew/prewarm
        # 判断 + agent callback 投递），否则连续多轮走 RESPONSE_LENGTH_TRUNCATED
        # / RESPONSE_TOO_LONG 时 session 不归档/不预热，会卡进"上下文越来越
        # 大→一直截断恢复"的死循环。普通 will_retry / RESPONSE_INVALID 路径
        # 还会重试同轮，不算 turn 真正结束，跳过 finalize。
        #
        # 这里刻意不套 may_clear_shared_output()：产权门控管的是"往前端 / TTS
        # 写共享输出"，而 finalize 做的是 session 级结算（归档 / 预热）。A 被 B
        # 抢占不代表 A 这一轮不用结算——按产权跳过的话，连续截断又被连续打断
        # 时会链式跳过归档，正好落回上面那个死循环。同理放在 try 外：不能让它
        # 的异常被上面 "回复发送失败" 的 except 吞成一条 warning。
        #
        # 唯一跳过的是已退役 client 的回复、且宿主已经换到新一轮（见
        # _reply_turn_is_current；client 仍在用时它恒为真，上面的理由不受影响）：
        # 它的 session 已经不在了，没有要它结算的上下文；这时跑 finalize 读的是
        # 新 session，可能在新一轮说到一半时触发续期 / 热切换，结算由新一轮自己做。
        #
        # 被打断方接管（reply_turn.taken_over，含接管落在恢复自己的 turn end
        # 之后、接管方没再记欠账的情况）或欠账已经记下时，记为欠账并改走
        # _settle_owed_turn_wrap_up：结算只是延后到打字输入 / 语音轮的持有
        # 释放、会话空闲之后，不会被跳过，也不会抢在打断方的新回复之前。
        if (_is_too_long_final or _truncated_text is not None) and (
            reply_turn is None or self._reply_turn_is_current(reply_turn)
        ):
            if getattr(self, "_turn_wrap_up_owed", False) or (
                reply_turn is not None and reply_turn.taken_over
            ):
                self._turn_wrap_up_owed = True
                await self._settle_owed_turn_wrap_up()
            else:
                await self._finalize_turn_after_emit()

    async def handle_audio_data(self, audio_data: bytes):
        """Qwen audio callback: push audio to the WebSocket frontend"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime audio bytes=%d", self.lanlan_name, len(audio_data or b""))
            return
        if not self.use_tts:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                # 这里假设audio_data为PCM16字节流，使用流式重采样器处理
                audio = np.frombuffer(audio_data, dtype=np.int16)
                audio_float = audio.astype(np.float32) / 32768.0
                # 使用流式重采样器（维护内部状态，避免 chunk 边界不连续）
                resampled_float = self.audio_resampler.resample_chunk(audio_float)
                audio = (resampled_float * 32767.0).clip(-32768, 32767).astype(np.int16)
                await self.send_speech(audio.tobytes())
            else:
                pass  # websocket未连接时忽略

    async def handle_audio_done(self):
        """Realtime provider closed the audio stream of the current response.

        Dual of ``handle_audio_data``: that one streams the provider's native
        audio out, this one tells the frontend no more of it is coming for
        ``current_speech_id``. Without it the frontend has to guess the end of
        playback from momentarily-empty audio queues and finalizes too early
        (issue #1566).

        The transport awaits this callback only after the last audio delta of
        the response has been awaited, so the notice can never overtake audio.
        """
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime audio-done notice", self.lanlan_name)
            return
        # 只在原生音频通路上发。use_tts=True 时 provider 的原生音频在
        # handle_audio_data 里被整段丢弃，实际发声走 TTS worker 那条路，
        # 由它自己的 __audio_done__ 哨兵发信号 —— 这里再发一次就是错 sid /
        # 重复发。
        if not self.use_tts:
            await self.send_audio_done(self.current_speech_id)

    def _publish_user_utterance_to_plugin_bus(
        self, text: Optional[str], *, is_voice_source: bool, ts: float | None = None
    ) -> None:
        """Publish one verbatim user utterance to the plugin bus's user-context bucket.

        Plugins read it via ``ctx.bus.memory.get(bucket_id=...)``. Written to two
        buckets at once: ``"default"`` (matching the protocols.py doc example,
        globally readable) and ``self.lanlan_name`` (character-scoped) — but if the
        two names collide it is written only once, so the same utterance isn't
        consumed twice.

        Why: before this, the whole ``state.add_user_context_event`` chain was dead
        infrastructure — server, handler, and plugin SDK were all in place, but
        nothing ever wrote, so plugins always read empty. This is the first
        gateway where verbatim user input enters the system (voice transcription +
        text input), making it the most faithful place to publish "the user's
        actual words".
        """
        if not isinstance(text, str):
            return
        cleaned = text.strip()
        if not cleaned:
            return
        # 插件总线（conversations store）：主人侧原话，与 _publish_ai_message_to_plugin_bus
        # 成对；语音时间为转写到达时刻，不保证跨角色的实际说话顺序。
        try:
            published_at = time.time() if ts is None else ts
            # Speech ids remain diagnostic context; each record is one message.
            user_turn_id = str(getattr(self, "current_speech_id", "") or "")
            self._fire_task(publish_conversation_turn_observed_best_effort(
                self.lanlan_name,
                content=cleaned,
                turn_type="user_message",
                conversation_id=user_turn_id,
                source="main_logic.core",
                message_count=1,
                metadata={
                    "role": "master",
                    "is_voice": bool(is_voice_source),
                    "ts": published_at,
                },
                ts=published_at,
            ))
        except Exception:
            logger.debug("[plugin-bus] user message not published", exc_info=True)
        event = {
            "type": "user_message",
            "content": cleaned,
            "lanlan": self.lanlan_name,
            "is_voice": bool(is_voice_source),
            "source": "main_logic.core",
        }
        # dict.fromkeys 保留顺序的同时去重：lanlan_name == "default" 或为空
        # 时不会重复写入 default bucket。
        for bucket in dict.fromkeys(("default", self.lanlan_name)):
            if not isinstance(bucket, str) or not bucket:
                continue
            # dispatch_user_utterance fans out to every sink (plugin runtime
            # registers ``plugin.core.state.add_user_context_event`` at app
            # startup via app/runtime_bindings.py). Per-sink errors are
            # swallowed inside the dispatcher.
            dispatch_user_utterance(bucket, event)

    def _clean_frontend_memory_text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+", "", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""
        return cleaned[:500]

    async def _broadcast_voice_transcript_observed(self, transcript: str) -> None:
        """Best-effort fan-out of voice transcripts to plugins.

        Plugins are observers here, not arbiters for the current user turn.
        Main must never wait for or apply plugin-produced actions from this
        path.
        """
        session_snapshot = self.session
        try:
            await _core_facade.publish_voice_transcript_observed_best_effort(
                self.lanlan_name,
                transcript,
                metadata={
                    "session_type": type(session_snapshot).__name__ if session_snapshot else "",
                    "voice_source": True,
                },
            )
        except Exception as exc:
            logger.debug("[%s] voice transcript observer broadcast failed: %s", self.lanlan_name, exc)

    def _reset_voice_echo_suppression_cache(self) -> None:
        self._recent_ai_voice_echo_text = ''
        self._recent_ai_voice_echo_at = 0.0
        self._pending_ai_voice_echo_text = ''
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            self._pending_ai_voice_echo_chunks = deque()
        else:
            pending_chunks.clear()
        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is None:
            self._confirmed_ai_voice_echo_audio_speech_ids = set()
        else:
            confirmed_speech_ids.clear()

    def _remember_recent_ai_voice_echo(self, text: str) -> None:
        if not text:
            return
        recent_echo_text = (getattr(self, "_recent_ai_voice_echo_text", "") or "") + text
        self._recent_ai_voice_echo_text = recent_echo_text[-_VOICE_ECHO_LOOKBACK_CHARS:]
        self._recent_ai_voice_echo_at = time.time()

    @staticmethod
    def _pending_ai_voice_echo_item_speech_id(item) -> str | None:
        if isinstance(item, tuple) and len(item) == 2:
            return item[0]
        return None

    @staticmethod
    def _pending_ai_voice_echo_item_text(item) -> str:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
        return item

    def _remember_pending_ai_voice_echo(self, speech_id: str | None, text: str) -> None:
        if not text:
            return
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_chunks = deque()
            self._pending_ai_voice_echo_chunks = pending_chunks
        pending_chunks.append((speech_id, text))
        self._sync_pending_ai_voice_echo_text()

    def _sync_pending_ai_voice_echo_text(self) -> None:
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_chunks = deque()
            pending_echo_text = getattr(self, "_pending_ai_voice_echo_text", "") or ""
            if pending_echo_text:
                pending_chunks.append((None, pending_echo_text))
            self._pending_ai_voice_echo_chunks = pending_chunks

        pending_echo_text = "".join(
            self._pending_ai_voice_echo_item_text(chunk)
            for chunk in pending_chunks
        )
        excess = max(0, len(pending_echo_text) - _VOICE_ECHO_LOOKBACK_CHARS)
        while pending_chunks:
            first_text = self._pending_ai_voice_echo_item_text(pending_chunks[0])
            if not first_text:
                pending_chunks.popleft()
                continue
            if excess < len(first_text):
                break
            excess -= len(first_text)
            pending_chunks.popleft()
        if pending_chunks and excess > 0:
            first_chunk = pending_chunks[0]
            first_speech_id = self._pending_ai_voice_echo_item_speech_id(first_chunk)
            first_text = self._pending_ai_voice_echo_item_text(first_chunk)
            pending_chunks[0] = (first_speech_id, first_text[excess:])

        self._pending_ai_voice_echo_text = "".join(
            self._pending_ai_voice_echo_item_text(chunk)
            for chunk in pending_chunks
        )

    def _confirm_pending_ai_voice_echo(self, speech_id: str | None = None) -> None:
        if speech_id is None:
            return

        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is None:
            confirmed_speech_ids = set()
            self._confirmed_ai_voice_echo_audio_speech_ids = confirmed_speech_ids
        # Without chunk-level playback confirmation, one speech id can only safely promote one chunk.
        if speech_id in confirmed_speech_ids:
            return

        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_echo_text = getattr(self, "_pending_ai_voice_echo_text", "") or ""
            pending_chunks = deque()
            if pending_echo_text:
                pending_chunks.append((None, pending_echo_text))
            self._pending_ai_voice_echo_chunks = pending_chunks
            self._sync_pending_ai_voice_echo_text()
            return

        if not pending_chunks:
            self._pending_ai_voice_echo_text = ''
            return

        pending_speech_id = self._pending_ai_voice_echo_item_speech_id(pending_chunks[0])
        if pending_speech_id != speech_id:
            return

        pending_echo_text = self._pending_ai_voice_echo_item_text(pending_chunks.popleft())
        self._sync_pending_ai_voice_echo_text()
        confirmed_speech_ids.add(speech_id)
        self._remember_recent_ai_voice_echo(pending_echo_text)

    def _discard_pending_ai_voice_echo(self) -> None:
        self._pending_ai_voice_echo_text = ''
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is not None:
            pending_chunks.clear()
        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is not None:
            confirmed_speech_ids.clear()

    def _should_suppress_dirty_voice_transcript(self, transcript_text: str) -> bool:
        if not _core_facade.HIDE_DIRTY_VOICE_TRANSCRIPTS:
            return False
        recent_ai_at = float(getattr(self, "_recent_ai_voice_echo_at", 0.0) or 0.0)
        if recent_ai_at <= 0 or (time.time() - recent_ai_at) > _VOICE_ECHO_LOOKBACK_SECONDS:
            return False
        recent_ai_text = getattr(self, "_recent_ai_voice_echo_text", "") or ""
        return _looks_like_recent_ai_echo(transcript_text, recent_ai_text)

    async def _dispatch_mini_game_invite_keyword(self, user_text: str) -> None:
        """Scan the user's words once for mini-game invite accept/decline/later keywords; on a
        hit, trigger the corresponding state transition + push ``mini_game_invite_resolved``
        so the frontend dismisses the ChoicePrompt (on accept it doubles as the launch
        signal carrying game_url).

        Shared by the text input path (``_process_stream_data_internal``) and the voice
        transcription path (``handle_input_transcript``) — voice users can't click the
        ChoicePrompt's three buttons, they can only speak; a spoken "not now" must
        trigger the real decline cooldown just like typing / clicking. Otherwise a
        spoken refusal neither counts as decline nor escapes being treated by the next
        proactive tick's ``_mini_game_invite_advance_response`` as an implicit
        dismiss = 'later' (only a 5min suppress), and the invite keeps coming back.
        **Does not consume the message**: the normal chat pipeline still responds to it.

        main_routers' keyword matcher is registered as a hook on the bus
        (see app/runtime_bindings.py). Dispatcher swallows per-hook errors;
        if no hook is bound (e.g. entrypoint without main_routers), result
        is None.
        """
        outcome = _core_facade.dispatch_text_user_message(self.lanlan_name, user_text or '')
        # 推一条 mini_game_invite_resolved 给前端：accept 时兼当 launch 信号
        # （带 game_url），decline/later 时让 ChoicePrompt UI 清掉不让按钮挂着——
        # codex P2 指出，原版只对 accept 推，decline/later 命中后前端 prompt 不
        # 消失，用户后续点按钮会被 endpoint 当 expired，state 早变了。
        if not (outcome and outcome.get('action')):
            return
        try:
            if self.websocket and hasattr(self.websocket, 'send_json'):
                ws_state = getattr(self.websocket, 'client_state', None)
                if ws_state is None or ws_state == ws_state.CONNECTED:
                    payload = {
                        'type': 'mini_game_invite_resolved',
                        'session_id': outcome.get('session_id') or '',
                        'action': outcome['action'],
                    }
                    if outcome.get('game_url'):
                        payload['game_url'] = outcome['game_url']
                    if outcome.get('game_type'):
                        payload['game_type'] = outcome['game_type']
                    await self.websocket.send_json(payload)
        except Exception as _push_err:
            logger.warning(
                f"[{self.lanlan_name}] mini_game_invite_resolved "
                f"WS push failed: {_push_err}",
            )

    async def handle_text_input_transcript(self, transcript: str):
        """Reuse transcript queue/cache plumbing for text-mode sessions."""
        await self.handle_input_transcript(transcript, is_voice_source=False)

    @staticmethod
    def _normalize_explicit_openclaw_magic_command(text: str) -> Optional[str]:
        raw = str(text or "").strip()
        if not raw:
            return None
        lowered = " ".join(raw.lower().split())
        prefix = None
        for candidate in ("/openclaw ", "/qwenpaw "):
            if lowered.startswith(candidate):
                prefix = candidate
                break
        if prefix is None:
            return None
        command = lowered[len(prefix):].strip()
        if command in {"/clear", "clear"}:
            return "/clear"
        if command in {"/new", "new"}:
            return "/new"
        if command in {"/stop", "stop"}:
            return "/stop"
        if command in {"/daemon approve", "daemon approve", "/approve", "approve"}:
            return "/daemon approve"
        return None

    @staticmethod
    def _normalize_mini_game_magic_command(text: str) -> Optional[str]:
        """Return the game_type for a whole-message ``/<alias>`` mini-game command.

        The message must be exactly one slash (ASCII or full-width) followed by
        an alias from ``MINI_GAME_MAGIC_COMMANDS``; case and repeated whitespace
        are ignored. Anything else returns None so ordinary chat never opens a
        game implicitly.
        """
        raw = str(text or "").strip()
        if len(raw) < 2 or raw[0] not in ("/", "／"):
            return None
        command = " ".join(raw[1:].lower().split())
        if not command:
            return None
        from config import MINI_GAME_LAUNCH_URL_BY_GAME
        from config.prompts.prompts_proactive import MINI_GAME_MAGIC_COMMANDS

        for game_type, aliases_by_locale in MINI_GAME_MAGIC_COMMANDS.items():
            if game_type not in MINI_GAME_LAUNCH_URL_BY_GAME:
                continue
            if any(command in aliases for aliases in aliases_by_locale.values()):
                return game_type
        return None

    async def _maybe_handle_mini_game_magic_command(self, message: dict) -> bool:
        """Consume a text message that is a slash mini-game command.

        Runs before any session readiness check, auto-start, or realtime ->
        offline handoff: opening a game needs no LLM session, so a failed
        session start must not swallow the command and typing one during a
        voice session must not tear that session down. Returns True when the
        message was consumed.
        """
        data = message.get("data")
        if not isinstance(data, str):
            return False
        game_type = self._normalize_mini_game_magic_command(data)
        if not game_type:
            return False
        # No reply follows a command: the owed wrap-up of the reply it
        # interrupts is paid once the command's own turn is sealed, not when
        # that reply's task happens to end in one of the command's awaits.
        await self._with_owed_wrap_up_held(
            self._run_mini_game_magic_command(message, data, game_type)
        )
        return True

    async def _run_mini_game_magic_command(self, message: dict, data: str, game_type: str) -> None:
        """Interrupt the offline reply, seal the command's turn, launch the game."""
        request_id = message.get("request_id")
        # Drop only this command's own attachments: those it already staged (the
        # composer sends images before the text) and any arriving later.
        # ``_pending_images`` is session-wide with no request identity, so
        # clearing it would strip an earlier message's image whose text task
        # has not reached ``stream_text`` yet.
        self._discard_request_staged_images(request_id)
        self._mark_magic_command_image_drop_request(request_id)
        if isinstance(self.session, OmniOfflineClient):
            # The command never reaches stream_text, so a staged proactive
            # screenshot or plugin ``read`` image would otherwise leak into the
            # next unrelated message.
            pending_plugin_images = getattr(self.session, "_pending_plugin_images", None)
            if hasattr(pending_plugin_images, "clear"):
                pending_plugin_images.clear()
            clear_shot = getattr(self.session, "set_proactive_screenshot", None)
            if callable(clear_shot):
                clear_shot(None)
        # Another external route (not a mini-game) holding this character would
        # make the game's /route/start refuse the slot after its window opened,
        # so say why instead of opening it -- after dropping this command's
        # attachments, but before the interrupt steps below, which would cut off
        # what that route is still saying. The turn end
        # settles this request on the frontend. A mini-game replacing another
        # one is handled by the game route's own supersede logic.
        if is_route_slot_taken(
            self.lanlan_name, kind="game", takeover_owner=self.takeover_owner(),
        ):
            # The user did type the command: record it like a launched one, so
            # the sync stream shows what the refusal answers.
            await self.mirror_user_input(
                data,
                metadata={
                    "source": "mini_game",
                    "kind": "magic_command",
                    "command": game_type,
                },
                request_id=request_id,
            )
            await self.send_status(json.dumps({
                "code": "MINI_GAME_BLOCKED_BY_EXTERNAL_ROUTE",
                "details": {"game_type": game_type},
            }))
            await self._emit_agent_callback_turn_end(request_id)
            logger.info(
                "[%s] mini-game magic command not launched, external route active: %s",
                self.lanlan_name,
                game_type,
            )
            return True
        # The turn end below seals the frontend's current assistant bubble, so
        # stop an in-flight reply first, the same way a new text message does,
        # without tearing the session down. Only the offline producer is
        # cancelled here; a realtime voice session keeps running and just has
        # its current speech dropped by the frontend.
        async with self.lock:
            interrupted_speech_id = self.current_speech_id
        if isinstance(self.session, OmniOfflineClient):
            try:
                await self._interrupt_offline_reply(self.session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[%s] mini-game magic command could not interrupt the reply: %s",
                    self.lanlan_name, exc,
                )
        self.audio_resampler.clear()
        await self._clear_tts_pipeline()
        await self.send_user_activity(interrupted_speech_id)
        await self.mirror_user_input(
            data,
            metadata={
                "source": "mini_game",
                "kind": "magic_command",
                "command": game_type,
            },
            request_id=request_id,
        )
        await self._emit_agent_callback_turn_end(request_id)
        await self._push_mini_game_magic_command_launch(game_type)
        logger.info(
            "[%s] text input sent mini-game magic command: %s",
            self.lanlan_name,
            game_type,
        )

    async def _push_mini_game_magic_command_launch(self, game_type: str) -> bool:
        """Ask the frontend to open ``game_type`` through the invite launch event.

        Reuses ``mini_game_invite_resolved`` with ``action='open_game'`` so all
        chat surfaces keep one launch handler (the pet/single-window leader
        opens, chat.html followers skip). The fresh session_id never matches a
        pending ChoicePrompt, and the invite state machine is not touched.
        """
        from urllib.parse import urlencode
        from config import MINI_GAME_LAUNCH_URL_BY_GAME

        url_template = MINI_GAME_LAUNCH_URL_BY_GAME.get(game_type)
        if not url_template:
            return False
        session_id = str(uuid4())
        separator = "&" if "?" in url_template else "?"
        query = urlencode({"lanlan_name": self.lanlan_name, "session_id": session_id})
        payload = {
            "type": "mini_game_invite_resolved",
            "session_id": session_id,
            "action": "open_game",
            "game_url": f"{url_template}{separator}{query}",
            "game_type": game_type,
        }
        try:
            ws = self.websocket
            if ws and hasattr(ws, "send_json"):
                ws_state = getattr(ws, "client_state", None)
                if ws_state is None or ws_state == ws_state.CONNECTED:
                    await ws.send_json(payload)
                    return True
        except Exception as exc:
            logger.warning(
                "[%s] mini-game magic command launch push failed (game=%s): %s",
                self.lanlan_name, game_type, exc,
            )
            return False
        logger.warning(
            "[%s] mini-game magic command launch skipped: websocket not connected (game=%s)",
            self.lanlan_name, game_type,
        )
        return False

    def _clear_text_pending_images(self) -> None:
        if not isinstance(self.session, OmniOfflineClient):
            return
        pending_images = getattr(self.session, "_pending_images", None)
        if hasattr(pending_images, "clear"):
            pending_images.clear()
        # 队列清空后，请求→附件记录里的条目都已失效，一并释放。
        self._prune_request_staged_images()
        # 插件 read 图片的独立暂存位同为「待发视觉上下文」，走同一个失效判据。
        pending_plugin_images = getattr(self.session, "_pending_plugin_images", None)
        if hasattr(pending_plugin_images, "clear"):
            pending_plugin_images.clear()
        # 走 magic-command 等绕过 stream_text 的 text 输入时，主动搭话暂存的屏幕
        # 截图也不再是"下一条回复的背景"——这些路径不经 stream_text 消费它，残留
        # 会被注进后续不相关消息。一并清掉（与 _pending_images 同为"用户做了别的
        # 动作 → 失效待发视觉上下文"的对偶 choke point，Codex P2）。
        clear_shot = getattr(self.session, "set_proactive_screenshot", None)
        if callable(clear_shot):
            clear_shot(None)

    async def _publish_openclaw_magic_command(self, command: str) -> None:
        try:
            sent = await _core_facade.publish_analyze_request_reliably(
                lanlan_name=self.lanlan_name,
                trigger="text_openclaw_magic_command",
                messages=[{"role": "user", "content": command}],
                ack_timeout_s=0.8,
                retries=1,
                conversation_id=uuid4().hex,
                # Session locale (full code) so the agent process prompts in the
                # UI language instead of its own Steam/system locale.
                language=getattr(self, "user_language", None),
            )
        except Exception as exc:
            logger.warning("[%s] openclaw magic command publish failed: %s", self.lanlan_name, exc)
            await self.send_status(json.dumps({
                "code": "OPENCLAW_COMMAND_DISPATCH_FAILED",
                "details": {"command": command},
            }))
            return
        if not sent:
            logger.warning("[%s] openclaw magic command publish failed: no ack", self.lanlan_name)
            await self.send_status(json.dumps({
                "code": "OPENCLAW_COMMAND_DISPATCH_FAILED",
                "details": {"command": command},
            }))

    async def handle_input_transcript(
        self,
        transcript: str,
        *,
        is_voice_source: bool = True,
        source: str | None = None,
        metadata: dict | None = None,
        source_game_route_identity: Any = Ellipsis,
    ):
        """Sync transcript text into queues/cache and push it to the frontend.

        ``is_voice_source`` defaults to True for the realtime-client
        callbacks (genuine VAD-captured speech). Text-mode call sites
        that reuse this function for non-voice transcript display/cache paths
        pass False so that:
          - voice_rms is NOT marked (no fake voice_engaged state)
          - on_user_message is skipped here (the text-mode entry has
            already called it directly with the input data — calling
            twice would double-bump _conv_seq and add the text to the
            buffer twice)
        """
        transcript_text = transcript.strip()
        admission = assess_transcript(
            transcript_text, (metadata or {}).get("speech_evidence"),
            is_voice_source=is_voice_source, final=True,
        )
        if admission.disposition is TranscriptDisposition.REJECT:
            logger.info("[voice-admission] decision=reject reason=%s", admission.reason)
            return False
        record_transcript_text = transcript_text
        voice_rms_recorded = False
        source_identity_was_explicit = (
            source_game_route_identity is not Ellipsis
        )
        if not is_voice_source:
            source_game_route_identity = None
        elif source_game_route_identity is Ellipsis:
            # Compatibility for direct callers that do not have an ingress
            # token. Realtime and independent-ASR production paths pass the
            # identity captured when the utterance began.
            source_game_route_identity = get_active_game_route_generation_identity(
                self.lanlan_name
            )
        elif not (
            source_game_route_identity is None
            or (
                isinstance(source_game_route_identity, tuple)
                and len(source_game_route_identity) == 3
            )
        ):
            source_game_route_identity = None

        # A final can arrive after the utterance's route was replaced.  The
        # production voice paths provide an ingress snapshot, so never let an
        # A utterance enter B's takeover dispatcher or ordinary chat state.
        if is_voice_source and source_identity_was_explicit:
            current_route_identity = get_active_game_route_generation_identity(
                self.lanlan_name
            )
            if source_game_route_identity != current_route_identity:
                # Loud on purpose. This drop is total -- it happens above the
                # takeover dispatcher AND above every recording path, so the
                # utterance leaves no trace anywhere: not in the game, not in
                # chat, not in the logs. That is exactly how a route-ownership
                # regression hides, and this code has already been reworked
                # several times in both directions ("dropped a sentence" vs
                # "bound one to the wrong route") with nothing to tell them
                # apart afterwards. Text is deliberately not logged; the length
                # is enough to line an incident up with what the user said.
                logger.info(
                    "[%s] realtime STT transcript dropped: route ownership "
                    "mismatch source=%s current=%s len=%d",
                    self.lanlan_name,
                    source_game_route_identity,
                    current_route_identity,
                    len(transcript_text),
                )
                return False

        # 更新用户活动时间戳（用于主动搭话检测）。先捕获「转写到达时刻」局部变量，
        # 下面 last_user_message_time 复用同一时刻——若 takeover dispatcher 注册，
        # 这条转写会先 await 它再走到下面的真消息块；用 await 之后的 time.time() 会
        # 把时间戳推迟，万一 await 期间投递了 invite，invite 之前说的话会被记成 >
        # delivered_at、被下个 tick 误判成 invite 之后的回应（codex P2）。
        _transcript_arrival_ts = time.time()
        self.last_user_activity_time = _transcript_arrival_ts
        if (
            is_voice_source
            and transcript_text
            and self._takeover_input_dispatcher is not None
        ):
            # takeover 路由优先于 echo suppression；否则接管流程里用户说出
            # 与 AI 近期播报相同的口令时，会被当成脏回声提前吞掉。
            self._activity_tracker.on_voice_rms()
            voice_rms_recorded = True
            try:
                handled = await self._takeover_input_dispatcher(
                    self.lanlan_name,
                    transcript_text,
                    request_id=f"realtime-stt-{uuid4()}",
                )
                logger.info(
                    "[%s] session takeover dispatcher: realtime STT transcript routed handled=%s len=%d",
                    self.lanlan_name, handled, len(transcript_text),
                )
                if handled:
                    self.note_user_engagement(at=_transcript_arrival_ts)
                    if isinstance(self.session, OmniRealtimeClient):
                        try:
                            await self.session.cancel_response()
                            logger.info("[%s] session takeover: cancelled ordinary realtime response after STT transcript", self.lanlan_name)
                        except Exception as cancel_exc:
                            logger.debug("[%s] session takeover: realtime response cancel skipped/failed: %s", self.lanlan_name, cancel_exc)
                    return False
            except Exception as exc:
                logger.warning("[%s] session takeover dispatcher failed: %s", self.lanlan_name, exc)

        if (
            is_voice_source
            and transcript_text
            and self._should_suppress_dirty_voice_transcript(transcript_text)
        ):
            logger.info(
                "[%s] suppressed likely AI echo voice transcript len=%d",
                self.lanlan_name, len(transcript_text),
            )
            return False

        if is_voice_source and not voice_rms_recorded:
            # transcript 到达 → VAD 在窗口内捕捉到声音，标记 voice RMS 活跃；
            # 即使转录为空（VAD 误触发或转录失败）也算一次"用户在发声"，
            # 维持 voice_engaged 状态。
            self._activity_tracker.on_voice_rms()

        if is_voice_source and record_transcript_text:
            self._fire_task(self._broadcast_voice_transcript_observed(record_transcript_text))

        if is_voice_source:
            # 仅非空转录才算"用户消息"：on_user_message 会清掉 unfinished_thread、
            # bump _conv_seq（让 open_threads 缓存失效）、把文本进 buffer 给
            # emotion-tier LLM 用——空 transcript 这些副作用都不该触发。
            if record_transcript_text:
                self._note_user_turn(text=transcript)
                # 真实用户语音消息（已过 echo 抑制 + 非空）才刷「真消息」时间戳，
                # 给 mini-game 邀请隐式 dismiss 用，避免回声/空噪声误判用户已回应。
                # 用顶部捕获的到达时刻而非此处 time.time()：takeover dispatcher 的
                # await 不会把它推迟到 await 之后（codex P2）。
                self.last_user_message_time = _transcript_arrival_ts
                self.note_user_engagement(at=_transcript_arrival_ts)
                self._session_turn_count += 1
                # Telemetry：D1 漏斗——本进程首条用户消息（语音路径）。
                try:
                    from utils.token_tracker import TokenTracker as _TT
                    _tt = _TT.get_instance()
                    _tt.note_first_user_message("voice")
                    # 每条用户消息：user_message_sent counter（轮数 + voice/text 占比）
                    # + 累加 per-session 轮数（session_end emit session_turn_count）。
                    # 只在此真语音消息点调，避开非语音复用路径，杜绝双计。
                    _tt.note_user_message("voice")
                except Exception:
                    # 埋点 best-effort，绝不阻塞语音转录消息处理（同文本路径）。
                    pass
                # 与 on_user_message 对偶：把"用户原话"推到插件总线 user-context
                # bucket。文本路径在 _process_stream_data_internal 已自行调用，
                # 这里只覆盖语音路径，避免非语音复用路径重复发布。
                self._publish_user_utterance_to_plugin_bus(
                    transcript, is_voice_source=True, ts=_transcript_arrival_ts,
                )

                # 与文本路径（_process_stream_data_internal）对偶：dispatch 已
                # 同步跑完 ban-topic 抽取 + 落盘，本轮真抽到新指令就把禁令块写进
                # next-session 缓存，赶上正在预热的那次热切换。
                # ⚠️ **不**碰当前会话，realtime 尤其不能：那条路会回落到
                # prime_context，而它在 Gemini 上会另开一个 user turn 并吞掉本轮
                # 全部音频与转写。判据写在 _inject_pending_user_directives 的
                # lifetime 注释里。
                await self._inject_pending_user_directives()

                # Mini-game 邀请关键词兜底：与文本路径
                # （_process_stream_data_internal）对偶。语音用户没法点
                # ChoicePrompt 三按钮，只能说话——口头"现在不想玩"必须和打字 /
                # 点按钮一样触发真正的 decline 冷却，否则邀请会按 5min 隐式
                # dismiss 反复重来。详见 _dispatch_mini_game_invite_keyword。
                await self._dispatch_mini_game_invite_keyword(transcript)
        else:
            # Non-voice reuse of this method.
            # Skip activity-tracker hooks entirely — the text-mode entry
            # at `_process_stream_data_internal` has already recorded the
            # user message. We still need the queue/cache plumbing below
            # to work normally, so just bypass the tracker block.
            if record_transcript_text:
                self._session_turn_count += 1

        # 推送到同步消息队列
        user_message = {"input_type": "transcript", "data": record_transcript_text}
        source_value = str(source or "").strip()
        if source_value:
            user_message["source"] = source_value
        if isinstance(metadata, dict) and metadata:
            user_message["metadata"] = metadata
        if not is_voice_source and self._active_text_request_id:
            user_message["request_id"] = self._active_text_request_id
        self.sync_message_queue.put({"type": "user", "data": user_message})
        
        # 只在语音模式（OmniRealtimeClient）下发送到前端显示用户转录
        # 文本模式下前端会自己显示，无需后端发送，避免重复
        # [DIAG] 切换猫娘后对话框空白问题：记录是否触发、session 类型、ws 状态
        _ws_connected_dbg = bool(
            self.websocket
            and hasattr(self.websocket, 'client_state')
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        )
        logger.info(
            "[%s] voice user_transcript session=%s ws_connected=%s len=%d",
            self.lanlan_name, type(self.session).__name__, _ws_connected_dbg, len(record_transcript_text),
        )
        if isinstance(self.session, OmniRealtimeClient):
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    message = {
                        "type": "user_transcript",
                        "text": transcript.strip()
                    }
                    if source_game_route_identity is not None:
                        game_type, session_id, route_instance_id = source_game_route_identity
                        message["game_type"] = game_type
                        message["session_id"] = session_id
                        if route_instance_id:
                            message["sdk_route_instance_id"] = route_instance_id
                    await self.websocket.send_json(message)
                except Exception as e:
                    logger.error(f"⚠️ 发送用户转录到前端失败: {e}")
        
        # 缓存到session cache
        if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session.append({"role": self.master_name, "text": record_transcript_text})
            elif self.message_cache_for_new_session[-1]['role'] == self.master_name:
                self.message_cache_for_new_session[-1]['text'] += record_transcript_text
        # 注意: 这里不能修改 current_speech_id.
        # speech_id 仅应在“模型新回复开始”时更新 (handle_new_message / 文本模式 stream 入口),
        # 否则会导致前端把同一轮 AI 语音误判为新轮次, 出现首包被重置/吞掉的问题.
        return bool(record_transcript_text)

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        """Output transcription callback: handles text display and TTS (for voice mode).

        The turn this chunk belongs to is read ONCE, before the first await,
        and carried from there — never re-read afterwards. ``send_lanlan_response``
        suspends on the frontend WebSocket, and a user turn starting during that
        suspension takes ``current_speech_id`` with it (#2612): re-reading the
        field on the way out would queue a dead turn's tail under the successor's
        speech id and speak it as part of the new turn.

        Same shape ``handle_text_data`` already uses for the proactive
        contextvar. What differs here is that the identity matters even when
        that contextvar is unset — the ordinary voice path has no expected sid,
        but it still has a turn, and that is the path #2612 was filed against.
        """
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime output transcript len=%d", self.lanlan_name, len(text or ""))
            return

        # 同 handle_text_data：proactive 路径设置的 sid 期望值若与 current 不符，
        # 丢弃本 chunk，避免 proactive 文本被错插进用户新轮次。
        expected_sid = _proactive_expected_sid.get()
        if expected_sid is not None and expected_sid != self.current_speech_id:
            logger.debug(
                "handle_output_transcript drop: expected_sid=%s current_sid=%s len=%d",
                expected_sid, self.current_speech_id, len(text),
            )
            return
        # 本 chunk 的轮次身份：proactive 路径就是调用方钉住的那个 sid，普通语音
        # 路径则是此刻在跑的那一轮。两者都在第一个 await 之前读、之后用。
        turn_sid = expected_sid if expected_sid is not None else self.current_speech_id
        # 无论是否使用TTS，都要发送文本到前端显示
        publish_result = await self.send_lanlan_response(
            text,
            is_first_chunk,
            turn_id=turn_sid,
            remember_voice_echo=not self.use_tts,
            expected_speech_id=expected_sid,
        )
        # ``None`` means the guarded send lost ownership at its internal queue-write
        # boundary. Do not leak the same stale chunk into TTS. (handle_text_data
        # 同款。仅对 proactive 有意义：普通轮次不传 expected_speech_id，永不返回 None。)
        if expected_sid is not None and publish_result is None:
            return

        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts:
            async with self.tts_cache_lock:
                # send_lanlan_response 会在前端 WebSocket 上挂起。写 TTS 之前重新
                # 确认这一轮还是自己的：期间用户开了新一轮的话，下面这段文本属于
                # 已经死掉的那轮，排进新 sid 会被当成新一轮的话念出来。
                if self.current_speech_id != turn_sid:
                    logger.debug(
                        "handle_output_transcript drop after publish: "
                        "turn_sid=%s current_sid=%s len=%d",
                        turn_sid, self.current_speech_id, len(text),
                    )
                    return
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self._enqueue_tts_text_chunk(turn_sid, text)
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存（规范化延迟到 _flush_tts_pending_chunks）
                    self.tts_pending_chunks.append((turn_sid, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info("TTS未就绪，开始缓存文本chunk...")
                    # 仅在回复首 chunk 尝试拉起，避免每个 chunk 都重试
                    if is_first_chunk and self.tts_thread and (
                        not self.tts_thread.is_alive()
                        or not self._tts_runtime_is_current(self._snapshot_tts_runtime())
                    ):
                        self._respawn_tts_worker()

    async def send_lanlan_response(
        self,
        text: str,
        is_first_chunk: bool = False,
        turn_id: str | None = None,
        *,
        metadata: dict | None = None,
        request_id: Any = _REQUEST_ID_UNSET,
        track_ai_turn: bool = True,
        cache_for_new_session: bool = True,
        remember_voice_echo: bool = False,
        expected_speech_id: str | None = None,
        expected_user_engagement_time: Any = Ellipsis,
        on_published: Callable[[float], None] | None = None,
        publish_if: Callable[[], bool] | None = None,
    ):
        """Qwen output transcription callback: usable for frontend display/cache/sync.

        ``publish_if``, when given, is rechecked with the other guards at the
        publish boundary (after the Focus cleanup await): a caller whose turn
        can be taken over in that await (the discard recovery) publishes
        nothing once it no longer owns the shared output.

        ``request_id`` is tri-state:
          - not passed (i.e. the default ``_REQUEST_ID_UNSET``) → falls back to the
            shared field ``self._active_text_request_id``, preserving the behavior
            of existing LLM streaming call sites
          - explicitly passing ``None`` → genuinely "frozen to empty"; proactive /
            no-request_id scenarios need the frontend to know this message is
            bound to no user request
          - explicitly passing a str → cross-turn safety: discard / recovery must
            use the ``active_request_id`` snapshotted at the start of the
            function, so that after a new turn has written the shared field, a
            re-read doesn't pick up the wrong id and make the frontend roll back
            the wrong turn
        The default sentinel is the module-level ``_REQUEST_ID_UNSET = object()``
        to distinguish "not passed" from "explicit None", unlike a plain
        ``request_id is None`` check.
        """
        def guarded_delivery_is_current() -> bool:
            if publish_if is not None and not publish_if():
                return False
            if (
                expected_speech_id is not None
                and self.current_speech_id != expected_speech_id
            ):
                return False
            return not (
                expected_user_engagement_time is not Ellipsis
                and self.last_user_engagement_time
                != expected_user_engagement_time
            )

        # A stale proactive callback must not clear a replacement user turn's
        # Focus bubble. Recheck again after the cleanup await below to retain
        # the actual publish-boundary guarantee.
        if not guarded_delivery_is_current():
            return None

        text_clean = self.emotion_pattern.sub('', text)
        effective_turn_id = turn_id or self.current_speech_id
        effective_request_id = (
            self._active_text_request_id
            if request_id is _REQUEST_ID_UNSET
            else request_id
        )
        message = {
            "type": "gemini_response",
            "text": text_clean,
            "isNewMessage": is_first_chunk,
            "turn_id": effective_turn_id,
            "request_id": effective_request_id,
        }
        if metadata:
            message["metadata"] = metadata

        # 无论 WS 发送成功与否，始终将消息写入 sync_message_queue 和 message_cache，
        # 确保 cross_server 历史组装不因 WS 断连而丢失 assistant 内容。
        if is_first_chunk:
            logger.debug("[%s] send_lanlan_response: first chunk (len=%d)", self.lanlan_name, len(text_clean))
            # First visible content of the turn → the model has finished its
            # (hidden) 凝神 thinking, so drop the thinking-dots bubble. Idempotent,
            # so this is a no-op on regular / proactive turns that never lit it.
            await self._push_focus_thinking(False)
        # Guarded proactive delivery must revalidate at the actual internal
        # publish boundary. There are no awaits between these checks and the
        # sync queue write below, so a user click/text that arrived during
        # state.fire(PROACTIVE_COMMITTING) or Focus cleanup wins cleanly.
        # ``None`` is reserved for this guarded rejection; ``False`` still
        # means the sync publish succeeded but the best-effort WebSocket send
        # did not.
        if not guarded_delivery_is_current():
            return None

        # 累加到当前轮 AI 文本 buffer，turn end 时一并交给 activity tracker 做
        # unfinished_thread 检测。Guarded publish 被拒绝时不能污染 buffer，因此
        # 必须放在最终校验之后。emotion_pattern 已剥掉表情标签，但保留 <expr>
        # 等可能的 markup——tracker 自己会做二次 strip。
        if track_ai_turn:
            if not self._current_ai_turn_text:
                # 轮次开始：记下她"开口"的时刻，flush 时用它做插件总线的时间戳。
                self._current_ai_turn_started_at = time.time()
                self._current_ai_turn_client_owned = (
                    _proactive_expected_sid.get() is not None
                    and isinstance(getattr(self, "session", None), OmniOfflineClient)
                )
                if isinstance(getattr(self, "session", None), OmniRealtimeClient):
                    self._current_ai_turn_type = self.session.get_conversation_turn_type()
                else:
                    self._current_ai_turn_type = (
                        "proactive_reply"
                        if getattr(getattr(self, "state", None), "owner", None) is TurnOwner.PROACTIVE
                        else "assistant_message"
                    )
            self._current_ai_turn_text += text_clean
            self._current_ai_turn_id = str(effective_turn_id or '')
            if remember_voice_echo:
                self._remember_recent_ai_voice_echo(text_clean)
        published_at = time.time()
        self.sync_message_queue.put({"type": "json", "data": message})
        logger.debug(
            "[voice-chain] stage=model_text_publish turn_id=%s request_id=%s first=%s text_len=%d",
            effective_turn_id,
            effective_request_id,
            is_first_chunk,
            len(text_clean),
        )
        if on_published is not None:
            on_published(published_at)
        if cache_for_new_session and hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            # 注意：缓存使用原始文本，不翻译（用于记忆等内部处理）
            # Only guarded proactive deliveries pass expected_speech_id. Each
            # one gets its own entry marked as such, so the screen-history
            # guard (NotifyMixin._convert_cache_to_str) never reads two
            # independent deliveries, or a delivery and a reply, as one chain.
            # A first chunk under the same speech id is a retry of that
            # delivery (prompt_ephemeral restarts each attempt with
            # is_first_chunk=True); it replaces the discarded attempt's text.
            source = "proactive" if expected_speech_id is not None else None
            cache = self.message_cache_for_new_session
            last = cache[-1] if cache else None
            same_kind = (
                last is not None
                and last['role'] == self.lanlan_name
                and last.get('source') == source
            )
            if same_kind and not (source and is_first_chunk):
                last['text'] += text_clean
            elif same_kind and last.get('speech_id') == expected_speech_id:
                last['text'] = text_clean
            elif last is None or last['role'] in (self.master_name, self.lanlan_name):
                entry = {"role": self.lanlan_name, "text": text_clean}
                if source:
                    entry['source'] = source
                    entry['speech_id'] = expected_speech_id
                cache.append(entry)

        # WS 发送（可能失败，但 sync/cache 已保存）
        # [DIAG] 切换猫娘后对话框空白问题：仅首 chunk 记录，避免流式刷屏
        if is_first_chunk:
            logger.info(
                "[%s] send_lanlan_response first=%s len=%d ws_state=%s",
                self.lanlan_name, is_first_chunk, len(text_clean),
                getattr(self.websocket, 'client_state', None),
            )
        try:
            async def _do_send():
                if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                    await self.websocket.send_json(message)
                    return True
                return False

            if self.websocket_lock:
                async with self.websocket_lock:
                    ws_ok = await _do_send()
            else:
                ws_ok = await _do_send()
            return ws_ok

        except WebSocketDisconnect:
            logger.info("Frontend disconnected.")
            return False
        except Exception as e:
            logger.error(f"💥 WS Send Lanlan Response Error: {e}")
            return False

    # ------------------------------------------------------------------
    # Mirror channel (chat-bubble passthrough that enters context as
    # AIMessage; user-side inputs intentionally do NOT enter chat history
    # as UserMessage — see ``main_logic.mirror_meta``).
    # ------------------------------------------------------------------

    async def mirror_user_input(
        self,
        text: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        input_type: str | None = None,
        send_to_frontend: bool = False,
    ) -> None:
        """Record an external-controller user input into the sync stream.

        The text is logged for monitor/display purposes but does not
        flush into ``chat_history`` as a UserMessage (cross_server skips
        ``input_type`` values listed in ``mirror_meta.MIRROR_USER_INPUT_TYPES``).
        Use this when an external controller (e.g. a game route) has
        captured what the user said but the ordinary chat LLM should not
        see it.
        """
        from main_logic.mirror_meta import MIRROR_USER_TEXT_INPUT_TYPE

        clean = str(text or "").strip()
        if not clean:
            return
        resolved_input_type = input_type or MIRROR_USER_TEXT_INPUT_TYPE
        source = str(metadata.get("source") or "mirror") if isinstance(metadata, dict) else "mirror"
        _user_input_time = time.time()
        self.last_user_activity_time = _user_input_time
        self.note_user_engagement(at=_user_input_time)
        self.sync_message_queue.put({
            "type": "user",
            "data": {
                "input_type": resolved_input_type,
                "data": clean,
                "source": source,
                "metadata": metadata if isinstance(metadata, dict) else {},
                "request_id": request_id or "",
            },
        })
        if (
            send_to_frontend
            and self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            try:
                message = {
                    "type": "user_transcript",
                    "text": clean,
                    "source": source,
                    "request_id": request_id,
                }
                if isinstance(metadata, dict):
                    route_identity = {
                        "game_type": metadata.get("game_type") or metadata.get("kind"),
                        "session_id": metadata.get("session_id"),
                        "sdk_route_instance_id": metadata.get("sdk_route_instance_id"),
                    }
                    for key, raw_value in route_identity.items():
                        value = str(raw_value or "").strip()
                        if value:
                            message[key] = value
                await self.websocket.send_json(message)
            except Exception as e:
                logger.error(f"⚠️ mirror_user_input frontend dispatch failed: {e}")

    async def mirror_assistant_output(
        self,
        text: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        turn_id: str | None = None,
        finalize_turn: bool = False,
    ) -> dict:
        """Push an external-controller assistant line into the chat bubble.

        Reuses the ordinary :meth:`send_lanlan_response` path with
        ``track_ai_turn=False`` and ``cache_for_new_session=False`` so
        the line shows on frontend + sync stream as an AIMessage but
        doesn't pollute activity-tracker / hot-swap caches.
        """
        clean = str(text or "").strip()
        if not clean:
            return {"ok": False, "reason": "missing_line", "mirrored": False}

        effective_turn_id = turn_id or request_id or str(uuid4())
        await self.send_lanlan_response(
            clean,
            is_first_chunk=True,
            turn_id=effective_turn_id,
            metadata=metadata,
            request_id=request_id,
            track_ai_turn=False,
            cache_for_new_session=False,
        )
        if finalize_turn:
            await self.emit_mirror_turn_end(
                metadata=metadata,
                request_id=request_id,
                log_context="mirror assistant",
            )
        return {
            "ok": True,
            "mirrored": True,
            "turn_id": effective_turn_id,
            "request_id": request_id or "",
            "metadata": metadata if isinstance(metadata, dict) else {},
            "turn_finalized": bool(finalize_turn),
        }

    async def passthrough_to_chat_bubble(
        self,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        turn_id: str | None = None,
        source: str = "passthrough",
    ) -> bool:
        """Render external text verbatim into the chat bubble WITHOUT
        entering chat-LLM context.

        Distinct from :meth:`mirror_assistant_output`: that writes to
        ``sync_message_queue`` (so cross_server may add an AIMessage to
        chat history). ``passthrough_to_chat_bubble`` skips
        ``sync_message_queue`` entirely — frontend sees the bubble, but
        the chat LLM never sees it in the next turn.

        Use case: plugin / agent_server pushes verbatim with
        ``visibility=["chat"] + ai_behavior="blind"`` — operator wants
        the user to read it but the LLM should remain ignorant.

        This is a generic SessionManager capability; it does not assume
        any particular consumer.

        .. warning::
           **NO PRODUCTION CALLER remains.** That chat-blind push was the
           only one, and it now renders through :meth:`render_chat_blocks`
           as a source-labelled SYSTEM message instead — a plugin bubble
           must not wear the assistant's identity, and a ``chat_blocks``
           frame opens no assistant turn, so it needs no matching turn-end.

           Two consequences for anyone touching this:

           * The ``blocks`` parameter, ``message["blocks"]``, and the whole
             ``gemini_response.blocks`` consumer chain on the frontend
             (``hasStructuredResponseBlocks`` in ``app-websocket.js``, the
             ``structuredResponseBlocks`` branch in ``app-chat-adapter.js``)
             are reachable ONLY from tests. A regression there is invisible
             in production.
           * The turn-lifecycle contract below is likewise only a contract
             on paper right now. Honour it if you add a caller back, but do
             not assume it is being exercised today.

           Kept, rather than deleted, because it is the only way to render
           verbatim text *as the assistant* without touching chat history —
           a capability with no other spelling. If nothing claims it,
           delete it together with the frontend branch above; leaving one
           half is worse than either.

        Returns ``True`` iff a ``gemini_response`` frame was actually
        handed to ``send_json`` without raising. ``False`` covers every
        no-op path: no visible text or structured blocks, websocket missing
        or disconnected, and ``send_json`` failures swallowed below. Callers
        that open an assistant-turn lifecycle on the frontend (e.g.
        ``main_server`` chat-blind) MUST gate their turn-end emit on this
        flag — a swallowed send means the frontend never opened a turn,
        so emitting turn-end would close a lifecycle that never started.
        """
        # Why: caller passes raw_text deliberately (PR #1128 0ac9e8881).
        # We empty-check on the stripped form but forward the ORIGINAL so
        # leading/trailing whitespace, newlines, and indentation render
        # exactly as the plugin authored them.
        raw = str(text or "")
        structured_blocks = [dict(block) for block in blocks or [] if isinstance(block, dict)]
        if (not raw or not raw.strip()) and not structured_blocks:
            return False
        effective_turn_id = turn_id or request_id or str(uuid4())
        message = {
            "type": "gemini_response",
            "text": raw,
            "isNewMessage": True,
            "turn_id": effective_turn_id,
            "request_id": request_id,
            "metadata": {"source": source, "passthrough": True},
        }
        if structured_blocks:
            message["blocks"] = structured_blocks
        if not (
            self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json(message)
        except Exception as e:
            logger.warning(
                "[%s] passthrough_to_chat_bubble WS send failed: %s",
                self.lanlan_name, e,
            )
            return False
        return True

    async def render_chat_blocks(
        self,
        blocks: list[dict[str, Any]],
        *,
        request_id: str | None = None,
        source: str = "plugin",
        source_name: str | None = None,
    ) -> bool:
        """Render structured blocks as a SYSTEM message.

        Not the assistant and not the user. Content pushed by a plugin is
        neither, and rendering it as either is a claim the reader has no way to
        check: an assistant bubble the assistant has no memory of producing, or
        a user bubble the user never wrote.

        Opens no assistant turn, so callers must NOT emit a turn-end for it.

        ``source_name`` is the plugin's own display name when it supplied one;
        the frontend labels the bubble with it so the origin is visible rather
        than inferred.
        """
        structured_blocks = [dict(block) for block in blocks if isinstance(block, dict)]
        if not structured_blocks:
            return False
        if not (
            self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json({
                "type": "chat_blocks",
                "blocks": structured_blocks,
                "request_id": request_id,
                "metadata": {
                    "source": source,
                    "source_name": source_name or "",
                    "passthrough": True,
                },
            })
        except Exception as e:
            logger.warning(
                "[%s] render_chat_blocks WS send failed: %s",
                self.lanlan_name,
                e,
            )
            return False
        return True

    async def emit_mirror_turn_end(
        self,
        *,
        metadata: dict,
        request_id: str | None = None,
        log_context: str = "",
    ) -> None:
        """Emit a turn-end carrying mirror metadata (cross_server uses
        the metadata to decide whether to fold the turn into ordinary
        chat memory or skip it)."""
        turn_end_msg = {
            "type": "system",
            "data": "turn end",
            "request_id": request_id,
            "meta": metadata if isinstance(metadata, dict) else {},
        }
        self.sync_message_queue.put(turn_end_msg)
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                await self.websocket.send_json(turn_end_msg)
        except Exception as e:
            logger.warning("[%s] %s turn_end send failed: %s", self.lanlan_name, log_context or "mirror", e)

    async def interrupt_ordinary_speech_for_takeover(self) -> None:
        """Invalidate queued/in-flight ordinary output before a host takes audio."""
        async with self.lock:
            if not self._takeover_active:
                return
            interrupted_speech_id = self.current_speech_id
            self.current_speech_id = str(uuid4())
        self.audio_resampler.clear()
        await self._clear_tts_pipeline()
        self.release_speech_playback_gain(interrupted_speech_id)
        if isinstance(self.session, OmniRealtimeClient):
            try:
                await self.session.cancel_response()
            except Exception:
                # Handler-level takeover guards still discard provider output.
                pass
        await self.send_user_activity(interrupted_speech_id)

    async def interrupt_mirror_speech(self) -> None:
        """Cut whatever the character is saying before a mirrored line takes over.

        The ``interrupt_audio`` prelude of :meth:`mirror_assistant_speech`,
        shared with :meth:`open_mirror_speech_stream` (``abort``): clear the
        resampler and the TTS pipeline, release the playback gain of the
        interrupted speech, cancel a realtime provider's response and tell
        the page which speech id was interrupted.
        """
        async with self.lock:
            interrupted_speech_id = self.current_speech_id
        self.audio_resampler.clear()
        # Mirror channel feeds the project TTS pipeline regardless of
        # ``self.use_tts``, so always clear it on interrupt — the inner
        # liveness gate inside ``_clear_tts_pipeline`` makes this safe
        # when no worker is actually running.
        await self._clear_tts_pipeline()
        self.release_speech_playback_gain(interrupted_speech_id)
        # Realtime native voice: also tell the provider to stop generating
        # so further audio.delta / output_audio.delta won't keep streaming
        # past the interruption point.  Local takeover guards drop these
        # at handler level too, but cancelling on the wire avoids wasted
        # tokens and stale audio still in the wire buffer.
        if isinstance(self.session, OmniRealtimeClient):
            try:
                await self.session.cancel_response()
            except Exception as cancel_exc:
                logger.debug(
                    "[%s] mirror_assistant_speech: realtime cancel_response skipped/failed: %s",
                    self.lanlan_name, cancel_exc,
                )
        await self.send_user_activity(interrupted_speech_id)

    async def _interrupt_mirror_stream(self, speech_id: str) -> None:
        """``MirrorSpeechStream.abort``: interrupt only while ``speech_id`` is still the current speech."""
        async with self.lock:
            current = self.current_speech_id
        if current != speech_id:
            return
        await self.interrupt_mirror_speech()

    def open_mirror_speech_stream(
        self,
        *,
        metadata: dict,
        request_id: str,
        on_enqueued: Optional[Callable[[int], None]] = None,
        on_failed: Optional[Callable[[], None]] = None,
    ) -> MirrorSpeechStream:
        """Open one streaming mirror speech with its own speech id (OD-15 v3).

        The visit runtime speaks each line through one stream: ``push``
        LLM deltas, ``finish`` at the end of the line (one end marker for the
        whole line), ``abort`` to stop it. ``on_enqueued(n)`` is told how
        many characters of this speech reached the TTS request queue (text
        cleaned to nothing is not reported); it stays registered until the
        speech's end marker is queued or the stream is aborted.
        ``on_failed()`` reports a stream that stopped without its end marker
        (see :class:`MirrorSpeechStream`). Streams take the TTS turn in the
        order they are opened. Ordinary chat never opens one and is
        unaffected.
        """
        speech_id = str(uuid4())
        if on_enqueued is not None:
            callbacks = self._mirror_stream_callbacks
            callbacks[speech_id] = on_enqueued
            while len(callbacks) > 64:
                # 兜底上限：异常路径（别处清了管线、结束标记再没入队）留下的登记不无限增长
                callbacks.pop(next(iter(callbacks)))
        stream = MirrorSpeechStream(self, speech_id, metadata=metadata, request_id=request_id,
                                    on_failed=on_failed)
        predecessor, self._mirror_stream_tail = self._mirror_stream_tail, stream
        stream._start(predecessor)
        return stream

    async def mirror_assistant_speech(
        self,
        line: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        mirror_text: bool = True,
        emit_turn_end_after: bool = True,
        interrupt_audio: bool = False,
        playback_gain: float = 1.0,
        reuse_synthesized_audio: bool = False,
        wait_for_audio_completion: bool = False,
        audio_completion_timeout: float = 45.0,
        speech_correlation_id: str = "",
    ) -> dict:
        """Mirror an assistant line + play it through the project TTS pipeline.

        Combines :meth:`mirror_assistant_output` with TTS chunk
        enqueue.  TTS pipeline is started lazily via
        :meth:`ensure_tts_pipeline_alive`; if the worker isn't ready
        yet, the chunk is buffered in ``tts_pending_chunks`` and the
        handler picks it up when ``__ready__`` arrives.
        """
        clean = str(line or "").strip()
        if not clean:
            return {"ok": False, "reason": "missing_line", "audio_sent": False}

        cache_key = ""
        runtime_signature = ""
        cached_chunks = None
        if reuse_synthesized_audio:
            cache_key, runtime_signature = self.game_speech_audio_cache_identity(clean)
            cached_chunks = GAME_SPEECH_AUDIO_CACHE.get(cache_key)

        if interrupt_audio:
            await self.interrupt_mirror_speech()

        async with self.lock:
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            turn_id = self.current_speech_id
            self.state.mark_user_input_preempt()
        normalized_playback_gain = self.remember_speech_playback_gain(turn_id, playback_gain)
        await self.state.fire(SessionEvent.USER_INPUT, sid=turn_id)

        if mirror_text:
            await self.send_lanlan_response(
                clean,
                is_first_chunk=True,
                turn_id=turn_id,
                metadata=metadata,
                request_id=request_id,
                track_ai_turn=False,
                cache_for_new_session=False,
            )

        if cached_chunks is not None:
            self._remember_game_speech_correlation(turn_id, speech_correlation_id)
            # One call, one lock hold: sending frame by frame let an overlapping
            # ordinary/project TTS stream interleave between them, and the
            # frontend schedules decoded chunks in arrival order.
            audio_sent, done_sent = await self.send_cached_speech_batch(
                cached_chunks, turn_id,
            )
            audio_sent = audio_sent and bool(done_sent)
            if emit_turn_end_after:
                await self.emit_mirror_turn_end(
                    metadata=metadata,
                    request_id=request_id,
                    log_context="mirror cached speech",
                )
            # Cached audio is handed straight to the websocket, so there is no
            # worker completion sentinel to await and no client acknowledgement
            # to wait on. Report that honestly instead of claiming a completion
            # that was never observed: the caller asked to be told when the line
            # finished playing, and on this path we only know it was delivered.
            # (Ordering still holds -- the browser plays its queue in order.)
            return {
                "ok": audio_sent,
                "method": "project_tts_cache",
                "cache_status": "hit",
                "speech_id": turn_id,
                "audio_sent": audio_sent,
                "audio_queued": False,
                # Not observed, so not claimed. The sibling path below already
                # answers None when completion was not awaited, and delivery is
                # carried separately by audio_sent -- reusing it here would both
                # duplicate that signal and read as a completion that never
                # happened, since these chunks were only written to the socket.
                "audio_completed": None,
                "audio_completion_supported": False,
                **(
                    {"completion_note": "cache_hit_completion_not_observable"}
                    if wait_for_audio_completion else {}
                ),
                "turn_end_emitted": bool(emit_turn_end_after),
                "interrupt_audio": bool(interrupt_audio),
                "playback_gain": normalized_playback_gain,
                "voice_source": {
                    "provider": "project_tts_cache",
                    "method": "project_tts_cache",
                    "use_existing_send_speech": True,
                },
            }

        await self.ensure_tts_pipeline_alive()
        self._remember_game_speech_correlation(turn_id, speech_correlation_id)
        audio_queued = False
        capture_started = False
        completion_supported = bool(
            getattr(self, "_tts_completion_supported", True)
        )
        audio_output_supported = bool(
            getattr(self, "_tts_audio_output_supported", True)
        )
        effective_wait_for_audio_completion = (
            wait_for_audio_completion and completion_supported
        )
        completion_future = (
            self._begin_game_speech_completion_wait(turn_id)
            if effective_wait_for_audio_completion
            else None
        )
        try:
            if reuse_synthesized_audio:
                capture_started = GAME_SPEECH_AUDIO_CACHE.begin_capture(
                    self,
                    turn_id,
                    cache_key,
                    runtime_signature,
                )
            if audio_output_supported and self.tts_thread and self.tts_thread.is_alive():
                async with self.tts_cache_lock:
                    if self.tts_ready:
                        self._enqueue_tts_text_chunk(turn_id, clean)
                    else:
                        self.tts_pending_chunks.append((turn_id, clean))
                    status = self._request_tts_done_locked()
                    audio_queued = status in {"queued", "deferred", "already"}
            if capture_started and not audio_queued:
                GAME_SPEECH_AUDIO_CACHE.fail_capture(self, turn_id)
            if emit_turn_end_after:
                await self.emit_mirror_turn_end(
                    metadata=metadata,
                    request_id=request_id,
                    log_context="mirror speech",
                )
        except BaseException:
            if completion_future is not None:
                self._cancel_game_speech_completion_wait()
            self._clear_game_speech_correlation(turn_id)
            if audio_queued:
                # A cancellation or mirror failure after queueing must not let
                # the old worker outlive the request-level serialization lock.
                # Preserve the original exception after the bounded teardown.
                try:
                    await self._clear_tts_pipeline()
                except Exception as cleanup_exc:
                    logger.warning(
                        "[%s] queued game speech cleanup failed: %s",
                        self.lanlan_name,
                        cleanup_exc,
                    )
            raise

        audio_completed = False
        if completion_future is not None and audio_queued:
            try:
                audio_completed = await self._wait_for_game_speech_completion(
                    turn_id,
                    completion_future,
                    audio_completion_timeout,
                )
            except asyncio.CancelledError:
                # The HTTP caller can disconnect while the worker still owns
                # unscoped audio.  Stop and drain it before the router lock is
                # released, then preserve cancellation for the request task.
                await self._clear_tts_pipeline()
                raise
            if not audio_completed:
                # Do not release the game-router serialization lock while an
                # unscoped worker could still emit bytes for this speech ID.
                await self._clear_tts_pipeline()
        elif completion_future is not None:
            self._cancel_game_speech_completion_wait()
            self._clear_game_speech_correlation(turn_id)

        completion_timed_out = bool(
            effective_wait_for_audio_completion and audio_queued and not audio_completed
        )
        return {
            "ok": bool(audio_queued and not completion_timed_out),
            **(
                {"reason": "tts_unavailable"} if not audio_queued
                else {"reason": "audio_completion_timeout"} if completion_timed_out
                else {}
            ),
            "method": "project_tts",
            "cache_status": (
                "miss" if capture_started
                else "bypass_capacity" if reuse_synthesized_audio
                else "disabled"
            ),
            "speech_id": turn_id,
            "audio_sent": audio_queued,
            "audio_queued": audio_queued,
            "audio_completed": audio_completed if effective_wait_for_audio_completion else None,
            "audio_completion_supported": completion_supported,
            "turn_end_emitted": bool(emit_turn_end_after),
            "interrupt_audio": bool(interrupt_audio),
            "playback_gain": normalized_playback_gain,
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "use_existing_send_speech": True,
            },
        }
