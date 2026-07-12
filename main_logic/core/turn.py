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
import json
import re
import time
from collections import deque
from typing import Any, Optional
from datetime import datetime
from fastapi import WebSocketDisconnect
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from utils.llm_client import AIMessage
from main_logic.session_state import SessionEvent
from main_logic.agent_event_bus import dispatch_user_utterance
from config import SESSION_ARCHIVE_TRIGGER_TOKENS, SESSION_TURN_THRESHOLD
from uuid import uuid4
import numpy as np
from ._shared import (
    _REQUEST_ID_UNSET,
    _MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX,
    _VOICE_ECHO_LOOKBACK_SECONDS,
    _VOICE_ECHO_LOOKBACK_CHARS,
    _looks_like_recent_ai_echo,
    logger,
    _proactive_expected_sid,
    _get_chat_locale_text,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


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
        self._tts_done_queued_for_turn = False  # 新轮次重置 TTS 结束信号标记
        self._tts_done_pending_until_ready = False
        # 新一轮开始：清空上一轮 AI 文本累加器（即使上轮 turn end 已清过，
        # proactive abort 等异常路径可能漏清，新轮次起点重置最稳）
        self._current_ai_turn_text = ''

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
        """
        if self._takeover_active:
            return
        self.audio_resampler.clear()
        # 必须重置这两个 flag，否则下一轮 ``_request_tts_done_locked`` 会因
        # ``_tts_done_queued_for_turn=True`` 直接 early-return，下一轮的 TTS
        # flush sentinel 永远不入队，server 拿不到 ``tts.flush`` 句尾音频
        # 可能挂在 buffer 里、长句 utterance 不会 finalize。``handle_new_message``
        # 在 speech_stopped 路径也是这样 reset 的（[core.py:1214](main_logic/core.py:1214)），
        # 这里和它对偶。
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
            await self.send_lanlan_response(
                text,
                is_first_chunk,
                remember_voice_echo=not self.use_tts,
            )

        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts and tts_enabled:
            async with self.tts_cache_lock:
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
                    if is_first_chunk and self.tts_thread and not self.tts_thread.is_alive():
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
                self._fire_task(_me.analyze(text, now=now))
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

    def _flush_ai_turn_text_to_tracker(self) -> None:
        """Flush the per-turn AI text buffer into conversation turn sinks.

        Called from each AI-turn-end exit point — there are three:
          - ``_emit_turn_end`` for regular replies (and truncate-recovery)
          - ``handle_proactive_complete`` for the agent direct-reply path
          - ``finish_proactive_delivery`` for /api/proactive_chat success

        The activity sink runs the question heuristic over the text and
        (when text is non-empty) bumps ``_conv_seq`` for open_threads cache
        invalidation. Other sinks, such as background topic collection, see
        the same turn without living inside ``UserActivityTracker``.
        """
        self._note_ai_turn(text=self._current_ai_turn_text or None)
        self._current_ai_turn_text = ''

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
        self._flush_ai_turn_text_to_tracker()
        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            try:
                await self._request_tts_done_for_turn("handle_proactive_complete")
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败 (proactive): {e}")
        if self.sync_message_queue:
            self.sync_message_queue.put({'type': 'system', 'data': 'turn end agent_callback'})
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({'type': 'system', 'data': 'turn end agent_callback'})
                logger.debug("[%s] handle_proactive_complete: turn_end (agent_callback) sent to frontend", self.lanlan_name)
            else:
                logger.warning("[%s] handle_proactive_complete: websocket not connected, turn_end NOT sent", self.lanlan_name)
        except Exception as e:
            logger.warning("[%s] handle_proactive_complete: WS send turn_end error: %s", self.lanlan_name, e)

    async def _emit_turn_end(self, active_request_id) -> None:
        """Send the turn end signal to both sync_message_queue and the WebSocket,
        passing ``_pending_turn_meta`` through both channels before clearing it.
        Shared by two paths:
        - ``handle_response_complete`` normal completion
        - ``handle_response_discarded``'s truncate-recovery / too-long-final
        Unified semantics: sync queue and WS carry the same meta, avoiding one
        having meta while the other doesn't."""
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end'}
        pending_meta = self._pending_turn_meta
        if pending_meta:
            turn_end_msg['meta'] = pending_meta
            self._pending_turn_meta = None
        if active_request_id:
            turn_end_msg['request_id'] = active_request_id
        self.sync_message_queue.put(turn_end_msg)
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                ws_msg = {
                    'type': 'system',
                    'data': 'turn end',
                    'request_id': active_request_id,
                }
                if 'meta' in turn_end_msg:
                    ws_msg['meta'] = turn_end_msg['meta']
                await self.websocket.send_json(ws_msg)
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")
        # Activity tracker flush：AI 刚结束一轮（普通完成 + truncate-recovery 都
        # 走这里）。text 用于 unfinished_thread 检测——tracker 跑问号启发式决定
        # 要不要开 5min 跟进窗口；为 None 时不开窗，但仍更新 seconds_since_ai_msg。
        self._flush_ai_turn_text_to_tracker()

    async def _emit_agent_callback_turn_end(self, active_request_id) -> None:
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end agent_callback'}
        if active_request_id:
            turn_end_msg['request_id'] = active_request_id
        self.sync_message_queue.put(turn_end_msg)
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json(turn_end_msg)
        except Exception as e:
            logger.error(f"💥 WS Send Agent Callback Turn End Error: {e}")

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

    async def handle_response_complete(self):
        """Qwen completion callback: handles the Core API's response-complete event, including TTS and hot-swap logic"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime response completion", self.lanlan_name)
            await self._clear_tts_pipeline()
            self._pending_turn_meta = None
            self._current_ai_turn_text = ""
            self._active_text_request_id = None
            return

        active_request_id = self._active_text_request_id

        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            logger.info("📨 Response complete (LLM 回复结束)")
            try:
                await self._request_tts_done_for_turn("handle_response_complete")
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败: {e}")
        try:
            await self._emit_turn_end(active_request_id)
        finally:
            # Compare-and-clear：仅在共享字段仍是本轮快照时才清空，避免
            # 抹掉用户在 turn end 发出前提交的新轮 request_id。
            if self._active_text_request_id == active_request_id:
                self._active_text_request_id = None

        await self._finalize_turn_after_emit()

    async def _finalize_turn_after_emit(self) -> None:
        """Unified wrap-up after turn end: renew/prewarm decision + agent callback delivery.

        Shared by ``handle_response_complete`` and the recovery / too-long-final
        branches of ``handle_response_discarded``, so that consecutive
        RESPONSE_LENGTH_TRUNCATED / RESPONSE_TOO_LONG runs don't skip session
        archiving/prewarming and fall into the "context grows → keeps truncating
        and recovering" loop.
        """
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
                        logger.info(f"[{self.lanlan_name}] Main Listener: Uptime threshold met. Marking for new session preparation.")
                        self.is_preparing_new_session = True
                        self.summary_triggered_time = datetime.now()
                        self.message_cache_for_new_session = []
                        self.initial_cache_snapshot_len = 0
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

    async def handle_response_discarded(self, reason: str, attempt: int, max_attempts: int, will_retry: bool, message: Optional[str] = None):
        """
        Handle the response-discarded notification: clear the TTS pipeline + frontend output, sending turn end if necessary
        """
        # 快照本轮的 request_id，函数末尾只在仍等于快照时才清空——
        # 防止用户在本轮 turn end 发出前就提交下一条文本时，新轮的
        # request_id 被旧 discard 回调误抹掉（前端 rollback / clearPending
        # rollback 会跨轮串掉）。
        active_request_id = self._active_text_request_id
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

        await self._clear_tts_pipeline()

        if self.websocket and hasattr(self.websocket, 'client_state') and \
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
        if _is_too_long_final or _truncated_text is not None:
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
                        recovery_turn_id = str(uuid4())
                        self.current_speech_id = recovery_turn_id
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                else:
                    recovery_turn_id = self.current_speech_id

                # 发送文本到前端显示。显式传 active_request_id snapshot，
                # 避免 send_lanlan_response 内部回读共享字段时拿到新轮 id
                # 串掉前端 rollback 绑定。
                await self.send_lanlan_response(
                    body_text,
                    is_first_chunk=True,
                    turn_id=recovery_turn_id,
                    request_id=active_request_id,
                )

                # 仅当本轮**不是** ephemeral（即非 avatar_interaction 等
                # persist_response=False 的路径）时才写历史。avatar_interaction
                # 触发 RESPONSE_TOO_LONG/TRUNCATED 时本就该和 ephemeral 一致地
                # 不留下 AIMessage 痕迹。
                pending_meta = self._pending_turn_meta
                is_ephemeral = bool(pending_meta) and pending_meta.get("kind") == "avatar_interaction"
                if not is_ephemeral and self.session and hasattr(self.session, '_conversation_history'):
                    self.session._conversation_history.append(AIMessage(content=body_text))

                # 喂给 TTS 管线用角色音色念。recovery 路径下两次 await
                # 之间用户可能开新轮（ self.current_speech_id 被改），所以
                # done 信号也要带 expected_speech_id 校验，否则旧 recovery
                # 的 done 会结束新轮的 TTS（首句被截 / 整轮静音）。
                if self.use_tts:
                    await self.feed_tts_chunk(body_text, expected_speech_id=recovery_turn_id)
                    await self._request_tts_done_for_turn(
                        "handle_response_discarded:length_truncated"
                        if _truncated_text is not None
                        else "handle_response_discarded:too_long_final",
                        expected_speech_id=recovery_turn_id,
                    )

                # turn end —— 复用 _emit_turn_end helper（同 handle_response_complete
                # 走同一套语义；sync queue 和 WS 都带相同 meta）。
                # 注：上面读 pending_meta 已经触发 is_ephemeral 判定，但这里
                # _emit_turn_end 自己会再读一次 _pending_turn_meta 做透传 + 清空，
                # 二者读的是同一个值，幂等。
                await self._emit_turn_end(active_request_id)
            except Exception as e:
                logger.warning(f"⚠️ {'RESPONSE_LENGTH_TRUNCATED' if _truncated_text is not None else 'RESPONSE_TOO_LONG'} 回复发送失败: {e}")
            finally:
                # Compare-and-clear：见函数顶部 active_request_id 快照说明。
                if self._active_text_request_id == active_request_id:
                    self._active_text_request_id = None

        if self.sync_message_queue:
            self.sync_message_queue.put({
                'type': 'system',
                'data': 'response_discarded_clear'
            })

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
        if _is_too_long_final or _truncated_text is not None:
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

    def _publish_user_utterance_to_plugin_bus(
        self, text: Optional[str], *, is_voice_source: bool
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

    def _clear_text_pending_images(self) -> None:
        if not isinstance(self.session, OmniOfflineClient):
            return
        pending_images = getattr(self.session, "_pending_images", None)
        if hasattr(pending_images, "clear"):
            pending_images.clear()
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
        record_transcript_text = transcript_text
        voice_rms_recorded = False

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
                    if isinstance(self.session, OmniRealtimeClient):
                        try:
                            await self.session.cancel_response()
                            logger.info("[%s] session takeover: cancelled ordinary realtime response after STT transcript", self.lanlan_name)
                        except Exception as cancel_exc:
                            logger.debug("[%s] session takeover: realtime response cancel skipped/failed: %s", self.lanlan_name, cancel_exc)
                    return
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
            return

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
                self._publish_user_utterance_to_plugin_bus(transcript, is_voice_source=True)

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

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        """Output transcription callback: handles text display and TTS (for voice mode)"""
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
        # 无论是否使用TTS，都要发送文本到前端显示
        await self.send_lanlan_response(
            text,
            is_first_chunk,
            remember_voice_echo=not self.use_tts,
        )
        
        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts:
            async with self.tts_cache_lock:
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
                    if is_first_chunk and self.tts_thread and not self.tts_thread.is_alive():
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
    ):
        """Qwen output transcription callback: usable for frontend display/cache/sync.

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
        text_clean = self.emotion_pattern.sub('', text)
        # 累加到当前轮 AI 文本 buffer，turn end 时一并交给 activity tracker 做
        # unfinished_thread 检测。emotion_pattern 已剥掉表情标签，但保留 <expr>
        # 等可能的 markup——tracker 自己会做二次 strip。
        if track_ai_turn:
            self._current_ai_turn_text += text_clean
            if remember_voice_echo:
                self._remember_recent_ai_voice_echo(text_clean)
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
        self.sync_message_queue.put({"type": "json", "data": message})
        if cache_for_new_session and hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            # 注意：缓存使用原始文本，不翻译（用于记忆等内部处理）
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role']==self.master_name:
                self.message_cache_for_new_session.append(
                    {"role": self.lanlan_name, "text": text_clean})
            elif self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session[-1]['text'] += text_clean

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
        self.last_user_activity_time = time.time()
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
                await self.websocket.send_json({
                    "type": "user_transcript",
                    "text": clean,
                    "source": source,
                    "request_id": request_id,
                })
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

        Returns ``True`` iff a ``gemini_response`` frame was actually
        handed to ``send_json`` without raising. ``False`` covers every
        no-op path: empty/whitespace text, websocket missing or
        disconnected, and ``send_json`` failures swallowed below. Callers
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
        if not raw or not raw.strip():
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

    async def mirror_assistant_speech(
        self,
        line: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        mirror_text: bool = True,
        emit_turn_end_after: bool = True,
        interrupt_audio: bool = False,
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

        interrupted_speech_id = None
        if interrupt_audio:
            async with self.lock:
                interrupted_speech_id = self.current_speech_id
            self.audio_resampler.clear()
            # Mirror channel feeds the project TTS pipeline regardless of
            # ``self.use_tts``, so always clear it on interrupt — the inner
            # liveness gate inside ``_clear_tts_pipeline`` makes this safe
            # when no worker is actually running.
            await self._clear_tts_pipeline()
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

        async with self.lock:
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            turn_id = self.current_speech_id
            self.state.mark_user_input_preempt()
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

        await self.ensure_tts_pipeline_alive()
        audio_queued = False
        if self.tts_thread and self.tts_thread.is_alive():
            async with self.tts_cache_lock:
                if self.tts_ready:
                    self._enqueue_tts_text_chunk(turn_id, clean)
                else:
                    self.tts_pending_chunks.append((turn_id, clean))
                status = self._request_tts_done_locked()
                audio_queued = status in {"queued", "deferred", "already"}
        if emit_turn_end_after:
            await self.emit_mirror_turn_end(
                metadata=metadata,
                request_id=request_id,
                log_context="mirror speech",
            )

        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": turn_id,
            "audio_sent": audio_queued,
            "audio_queued": audio_queued,
            "turn_end_emitted": bool(emit_turn_end_after),
            "interrupt_audio": bool(interrupt_audio),
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "use_existing_send_speech": True,
            },
        }
