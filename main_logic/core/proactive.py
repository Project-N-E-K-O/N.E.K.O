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
"""Proactive delivery for ``LLMSessionManager``: agent callbacks, topic
hooks, voice nudges, screenshot staging, playback gating, and the
prepare/feed/finish proactive delivery protocol.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import time
from typing import Any, Optional
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from utils.llm_client import AIMessage
from main_logic.session_state import SessionEvent, ProactivePhase
from main_logic.proactive_delivery import DELIVERY_RETRACTED_KEY, resolve_callback_delivery_ack
from config import ANTI_REPEAT_EXEMPT_SOURCE_TAGS
from utils.language_utils import normalize_language_code, get_global_language
from uuid import uuid4
from ._shared import _VOICE_PROACTIVE_ACK_GRACE_S, logger, _proactive_expected_sid
from .callback_render import _build_callback_instruction, _select_callbacks_within_token_budget


class ProactiveMixin:
    """Proactive delivery methods (see module docstring)."""

    def _park_proactive_for_goodbye(self) -> None:
        """While cat-mode silent, move the manager's pending-release callbacks into the persistent queue, so nothing is dropped or released on timeout during the silence."""
        try:
            leftover = self.proactive_manager.drain_pending()
            for callback in leftover:
                self.enqueue_agent_callback(callback)
            self.proactive_manager.reset_gate()
            if leftover:
                logger.info("[%s] goodbye_silent parked proactive callbacks n=%d", self.lanlan_name, len(leftover))
        except Exception:
            logger.exception("[%s] goodbye_silent proactive park failed", self.lanlan_name)

    # 供主服务调用，更新Agent模式相关开关
    def update_agent_flags(self, flags: dict):
        try:
            for k in [
                'agent_enabled',
                'computer_use_enabled',
                'browser_use_enabled',
                'user_plugin_enabled',
                'openclaw_enabled',
                'openclaw_ready',
                'openfang_enabled',
            ]:
                if k in flags and isinstance(flags[k], bool):
                    self.agent_flags[k] = flags[k]
        except Exception:
            # Malformed flags payload — keep the current flags.
            pass

    # ------------------------------------------------------------------
    # Voice-chat proactive audio nudge (dedicated path)
    # ------------------------------------------------------------------

    async def trigger_voice_proactive_nudge(self) -> bool:
        """Inject a pre-recorded audio prompt to nudge the voice model into speaking.

        This is the **only** caller of ``OmniRealtimeClient.prompt_ephemeral``
        for the voice-chat proactive feature.  It is completely independent of
        ``trigger_agent_callbacks`` (which handles agent task results).

        Returns True if the audio was fully injected, False if skipped.
        """
        if not self.is_active or not isinstance(self.session, OmniRealtimeClient):
            return False
        if self.is_goodbye_silent():
            logger.info("[%s] voice proactive nudge skipped: goodbye silent", self.lanlan_name)
            return False
        if self._takeover_active:
            logger.info("[%s] voice proactive nudge skipped: session takeover active", self.lanlan_name)
            return False
        if self.is_hot_swap_imminent:
            logger.info("[%s] voice proactive nudge skipped: hot-swap imminent", self.lanlan_name)
            return False
        _lang = normalize_language_code(self.user_language, format='short') or 'en'
        delivered = await self.session.prompt_ephemeral(language=_lang)
        if delivered:
            logger.info("[%s] voice proactive nudge delivered (%s)", self.lanlan_name, _lang)
        else:
            logger.info("[%s] voice proactive nudge skipped (guard)", self.lanlan_name)
        return delivered

    # ------------------------------------------------------------------
    # Proactive streaming helpers (Phase 2 流式 TTS + 完整文本投递)
    # ------------------------------------------------------------------

    async def request_fresh_screenshot(self, timeout: float = 3.0) -> str:
        """Request the latest screenshot from the frontend over WebSocket, falling back to backend pyautogui on failure.

        Both paths normalize the screenshot down to 720p/JPEG-80 and return the
        normalized base64 (without prefix), so a native-resolution frontend image
        never goes straight to the vision LLM and trips the proxy's 413.
        """
        # 策略1: 前端 WebSocket 截图
        if self.websocket:
            try:
                loop = asyncio.get_running_loop()
                self._screenshot_future = loop.create_future()
                await self.websocket.send_json({"type": "request_screenshot"})
                b64 = await asyncio.wait_for(self._screenshot_future, timeout=timeout)
                if b64:
                    # 前端有的截图路径（如 Electron 主进程直捕 captureSourceAsDataUrl）
                    # 返回原生分辨率，未走 720p 缩放，base64 可达 ~1.4MB，直接发给
                    # vision LLM 会被代理 nginx 以 413 Request Entity Too Large 拒掉。
                    # 这里和下方 pyautogui 兜底分支对称，统一压到 720p/JPEG-80 再返回
                    # （avatar 注解会在其上二次编码，不影响）。压缩失败则退回原图。
                    try:
                        from utils.screenshot_utils import (
                            decode_and_compress_screenshot_b64,
                            COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY,
                        )
                        b64 = await asyncio.to_thread(
                            decode_and_compress_screenshot_b64,
                            b64, COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY,
                        )
                    except Exception as comp_err:
                        logger.warning(
                            "[%s] request_fresh_screenshot WS compress failed, using raw: %s",
                            self.lanlan_name, comp_err,
                        )
                    return b64
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("[%s] request_fresh_screenshot WS failed: %s", self.lanlan_name, e)
            finally:
                self._screenshot_future = None

        # 策略2: 后端 pyautogui 兜底（仅限本机连接，远程服务器截图无意义）
        is_local = False
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client') and ws.client:
                is_local = ws.client.host in ('127.0.0.1', '::1', 'localhost')
        except Exception:
            # Introspection failure just means "not local" — keep the False default.
            pass
        if is_local:
            try:
                import pyautogui
                from utils.screenshot_utils import compress_screenshot, COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY
                import base64 as b64mod
                def _capture_and_compress() -> bytes:
                    shot = pyautogui.screenshot()
                    if shot.mode in ('RGBA', 'LA', 'P'):
                        shot = shot.convert('RGB')
                    return compress_screenshot(
                        shot,
                        target_h=COMPRESS_TARGET_HEIGHT,
                        quality=COMPRESS_JPEG_QUALITY,
                    )

                jpg_bytes = await asyncio.to_thread(_capture_and_compress)
                b64_str = b64mod.b64encode(jpg_bytes).decode('utf-8')
                logger.info("[%s] request_fresh_screenshot: 后端 pyautogui 兜底成功 (%dKB)", self.lanlan_name, len(jpg_bytes) // 1024)
                return b64_str
            except Exception as e2:
                logger.warning("[%s] request_fresh_screenshot backend fallback failed: %s", self.lanlan_name, e2)

        return ''

    def resolve_screenshot_request(self, b64: str):
        """Called by the WebSocket router to hand the frontend's returned screenshot to the waiting future."""
        if self._screenshot_future and not self._screenshot_future.done():
            self._screenshot_future.set_result(b64)

    async def prepare_proactive_delivery(self, min_idle_secs: float = 10.0) -> bool:
        """Pre-checks before Phase 2 streaming + speech_id generation. Returns True if it's OK to proceed."""
        if self.is_goodbye_silent():
            logger.info("[%s] prepare_proactive_delivery: goodbye silent", self.lanlan_name)
            return False
        # 早期抢占检查：在任何 await / sid 改写前快速短路，防止用户刚在入口之后
        # 抢占而后续 self.current_speech_id 写入覆盖用户的 user_sid。默认 reset()
        # 对活动 phase no-op（保护 auto-start 期间偶发并发 reset），但 end_session
        # 走 force=True 强制清场——这里短路不依赖 reset() 的语义差异，单纯是为
        # 了更早放弃已被抢占的 proactive 轮次。
        if self.state.is_proactive_preempted():
            logger.info("[%s] prepare_proactive_delivery: preempted before claim", self.lanlan_name)
            return False
        if self.last_user_activity_time is not None:
            if time.time() - self.last_user_activity_time < min_idle_secs:
                logger.info("[%s] prepare_proactive_delivery: user active recently", self.lanlan_name)
                return False
        if self.is_active and isinstance(self.session, OmniRealtimeClient):
            logger.info("[%s] prepare_proactive_delivery: voice session active", self.lanlan_name)
            return False
        if not self.websocket:
            return False
        try:
            if (hasattr(self.websocket, 'client_state')
                    and self.websocket.client_state != self.websocket.client_state.CONNECTED):
                return False
        except Exception:
            # ws state introspection failed — fall through to the remaining gates.
            pass
        if not self.session or not hasattr(self.session, '_conversation_history'):
            try:
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] prepare_proactive_delivery: session start failed: %s", self.lanlan_name, e)
                return False
            if not self.session or not hasattr(self.session, '_conversation_history'):
                return False
            # auto-start 期间耗时 await；再次确认 proactive 未被用户抢占
            if self.state.is_proactive_preempted():
                logger.info("[%s] prepare_proactive_delivery: preempted during auto-start", self.lanlan_name)
                return False
        async with self.lock:
            # lock 内二次复查：USER_INPUT 在 self.lock 内 rotate sid，sticky preempt
            # flag 先于 sid mutation 翻起；此处若已被抢占则不写 current_speech_id。
            if self.state.is_proactive_preempted():
                logger.info("[%s] prepare_proactive_delivery: preempted in claim lock", self.lanlan_name)
                return False
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            claim_sid = self.current_speech_id
        # 状态机：正式 claim turn。订阅者（诊断、frontend sync 等）在此之后
        # 观察到 proactive_sid 已与 current_speech_id 一致。
        await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=claim_sid)
        return True

    async def feed_tts_chunk(self, text: str, expected_speech_id: str | None = None):
        """Feed text to the TTS pipeline only, without sending it to the frontend display.

        expected_speech_id: if not None and it doesn't match the current
        current_speech_id (meaning the caller's turn has been taken over by another
        path, e.g. the user interrupted during proactive streaming), drop this
        chunk and return. The check happens inside the lock to stay atomic with
        the enqueue, so proactive text can't be mislabeled with the new turn's
        speech_id and flow into the user's normal reply audio.
        """
        if not self.use_tts:
            return
        async with self.tts_cache_lock:
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.debug(
                    "feed_tts_chunk drop: expected_sid=%s current_sid=%s len=%d",
                    expected_speech_id, self.current_speech_id, len(text),
                )
                return
            if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                try:
                    self._enqueue_tts_text_chunk(self.current_speech_id, text)
                except Exception as e:
                    logger.warning(f"⚠️ feed_tts_chunk 失败: {e}")
            else:
                self.tts_pending_chunks.append((self.current_speech_id, text))
                # Worker 已死亡则尝试拉起（受 12 秒冷却限制，不会风暴重连）
                if self.tts_thread and not self.tts_thread.is_alive():
                    self._respawn_tts_worker()

    async def finish_proactive_delivery(
        self,
        full_text: str,
        expected_speech_id: str | None = None,
        action_note: str | None = None,
        source_tag: str | None = None,
        vision_screenshot_b64: str | None = None,
    ) -> bool:
        """Wrap-up after streaming completes: deliver the full text in one shot + record history + TTS/turn end signals.

        expected_speech_id: if not None and it no longer matches
        current_speech_id after entering _proactive_write_lock, the user
        interrupted and took over the turn between the end of the Phase 2 stream
        and finish (stream_text cleared the queue + rotated the sid). In that case
        the frontend/history/TTS end signals must all be skipped, or the proactive
        text bubble would appear after the user's reply, history would be
        polluted, and TTS done would wrongly terminate the user's in-progress
        reply.

        action_note: optional; when non-empty it is appended to the tail of that
        AIMessage's content in _conversation_history (history-only — never enters
        send_lanlan_response or TTS). Used to leave "what song was actually
        played / what content was shared / where it came from" as metadata for
        the LLM to see next turn, so when the user asks "what was that song just
        now" the AI isn't clueless — remembering only what it said but not what
        it did. Construction logic in
        ``config.prompts.prompts_proactive.build_proactive_action_note``.

        vision_screenshot_b64: optional; the screen this proactive round obtained
        when a vision model was available (the caller passes it whenever a
        screenshot was captured, regardless of which channel the talk landed on).
        Staged onto the session (NOT committed into history as an image) only on a
        genuine commit, so the user's NEXT text reply folds it in as leading
        visual context — the conversation model otherwise sees only the proactive
        text and can't tell what was on screen. Passing None clears any previously
        staged screenshot, so a new proactive round always discards the prior
        cache (and may fill a fresh one). The session enforces a short TTL
        (``_PROACTIVE_SCREENSHOT_TTL_SECONDS``) on the staged screenshot at
        injection time. Staging happens inside the sid-guard
        below, so a user takeover (sid change → early return) never stages a
        screenshot for an undelivered turn.

        Returns True when genuinely persisted, False when skipped due to a sid
        change. The caller uses this to short-circuit downstream side effects
        (_record_proactive_chat / topic usage / surfaced reflection etc.), so
        undelivered content is never recorded as "delivered".
        """
        async with self._proactive_write_lock:
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.info(
                    "[%s] finish_proactive_delivery skip: sid changed (expected=%s current=%s)，用户已接管本轮",
                    self.lanlan_name, expected_speech_id, self.current_speech_id,
                )
                return False
            # 冻结 commit 用的 turn_id：current_speech_id 由 self.lock 保护，不在
            # _proactive_write_lock 范围内，下面 send_lanlan_response 之前若用户经
            # handle_new_message/stream_text 抢占完成 sid 轮换，再让 send_lanlan_response
            # 默认从 self.current_speech_id 取值会把这条 proactive 气泡打到用户新
            # turn 上、前端分组串掉。expected_speech_id 在 phase2 已经一路传到这里
            # 并且刚校验过，作为冻结快照最稳。
            commit_sid = expected_speech_id or self.current_speech_id
            # 状态机：进入 COMMITTING 阶段；期间若用户抢占仍会 sticky 到 _preempted，
            # 但本处 lock 内 sid 已校验过，commit 本身安全。
            await self.state.fire(SessionEvent.PROACTIVE_COMMITTING)
            await self.send_lanlan_response(full_text, is_first_chunk=True, turn_id=commit_sid)

            # Flush per-turn AI-text buffer to activity tracker. The regular
            # /api/proactive_chat path doesn't call handle_proactive_complete
            # (only the agent-direct-reply path in main_server.py does), so
            # without this the buffer would carry the proactive text forward
            # and contaminate the next user-initiated turn's AI message.
            self._flush_ai_turn_text_to_tracker()

            if self.session and hasattr(self.session, '_conversation_history'):
                # action_note 只进历史，不进 send_lanlan_response（前端不展示）
                # 也不进 TTS。空 full_text + 非空 note 的场景目前不会发生
                # （proactive 不允许空文本），但写法上仍然兜底拼接。
                history_text = full_text
                if action_note:
                    note = action_note.strip()
                    if note:
                        history_text = f"{full_text}\n{note}" if full_text else note
                self.session._conversation_history.append(AIMessage(content=history_text))
                # 本轮拿到截图（有可用 vision 模型）时，把那张截图暂存到 session
                # （仅暂存，不作为图片写进历史），下一条用户 text 回复经 stream_text
                # 时会把它作为前导视觉背景注入——否则对话模型只看到搭话文本，回复
                # 时完全不知道刚才评论的屏幕长什么样。新一轮主动搭话产生即覆盖/清掉
                # 旧缓存（没拿到截图的轮次传 None 清），session 侧再用短 TTL
                # （_PROACTIVE_SCREENSHOT_TTL_SECONDS）兜底过期。set_* 在 sid 校验之
                # 后，用户接管（sid 变→早 return）时
                # 绝不会为未投递的轮次暂存截图。
                if hasattr(self.session, "set_proactive_screenshot"):
                    self.session.set_proactive_screenshot(vision_screenshot_b64)
                # LLM 给自己的元数据备忘，不算复读对象。素材推送类 channel（推歌）
                # 的台词天生模板化，录进 corpus 会污染 FG 窗、漂移其它 channel 的
                # 复读基线，故按 ANTI_REPEAT_EXEMPT_SOURCE_TAGS 豁免（与出口的
                # BM25 评分豁免对偶）。
                if source_tag not in ANTI_REPEAT_EXEMPT_SOURCE_TAGS:
                    try:
                        from memory.anti_repeat import get_anti_repeat_corpus
                        get_anti_repeat_corpus().record_output(
                            self.lanlan_name, full_text, is_proactive=True,
                        )
                    except Exception as _exc:  # pragma: no cover
                        logger.debug("[AntiRepeat] record proactive skipped: %s", _exc)

            if self.use_tts and self.tts_thread and self.tts_thread.is_alive() and not self._tts_done_queued_for_turn:
                try:
                    await self._request_tts_done_for_turn("finish_proactive_delivery")
                except Exception:
                    # TTS done-signal is best-effort; the delivery itself already succeeded.
                    pass

            self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            try:
                if (self.websocket
                        and hasattr(self.websocket, 'client_state')
                        and self.websocket.client_state == self.websocket.client_state.CONNECTED):
                    await self.websocket.send_json({'type': 'system', 'data': 'turn end'})
            except Exception:
                # Turn-end push is best-effort; the client may have gone away.
                pass
        # proactive 原文不写 logger（隐私）；本地 print 兜底
        logger.info("[%s] Proactive stream delivered (text_len=%d)", self.lanlan_name, len(full_text or ""))
        print(f"[{self.lanlan_name}] Proactive stream delivered: {(full_text or '')[:40]}…")
        return True

    def _purge_retracted_agent_callbacks(self) -> None:
        retracted_ids = {
            cb.get("_callback_delivery_id")
            for cb in self.pending_agent_callbacks
            if cb.get(DELIVERY_RETRACTED_KEY) and cb.get("_callback_delivery_id")
        }
        has_retracted = any(
            cb.get(DELIVERY_RETRACTED_KEY)
            for cb in self.pending_agent_callbacks
        )
        if not has_retracted:
            return
        self.pending_agent_callbacks = [
            cb for cb in self.pending_agent_callbacks
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        if retracted_ids:
            self.pending_extra_replies = [
                extra for extra in self.pending_extra_replies
                if extra.get("_callback_delivery_id") not in retracted_ids
            ]

    def _purge_retracted_agent_callback_extras(self, callbacks: list) -> None:
        retracted_ids = {
            cb.get("_callback_delivery_id")
            for cb in callbacks
            if cb.get(DELIVERY_RETRACTED_KEY) and cb.get("_callback_delivery_id")
        }
        if retracted_ids:
            self.pending_extra_replies = [
                extra for extra in self.pending_extra_replies
                if extra.get("_callback_delivery_id") not in retracted_ids
            ]

    async def trigger_agent_callbacks(self) -> bool:
        """Proactively deliver pending agent task results via LLM rephrase.

        Design:
        - Text mode (OmniOfflineClient): claims proactive turn via
          ``state.try_start_proactive()`` then calls ``prompt_ephemeral()`` so
          the LLM generates a styled response in the character's voice.
        - Voice mode (OmniRealtimeClient): defers to hot-swap — callbacks are
          kept in pending_extra_replies for injection via prime_context();
          does not participate in the SM state machine (hot-swap has its own
          independent lifecycle).
        - On failure or when the session is busy, restores callbacks so the next
          handle_response_complete() call will retry automatically.
        - Re-entrancy and the "AI is replying" mutual exclusion are handled by
          the SM's atomic claim; also mutually exclusive with
          ``/api/proactive_chat`` / ``trigger_greeting``.
        """
        def _active_proactive_callbacks(callbacks: list) -> list:
            return [
                cb for cb in callbacks
                if cb.get("delivery_mode") != "passive"
                and not cb.get(DELIVERY_RETRACTED_KEY)
            ]

        sess_type = type(self.session).__name__ if self.session else "None"
        logger.info(
            "[%s] trigger_agent_callbacks enter: session=%s phase=%s pending=%d",
            self.lanlan_name, sess_type, self.state.phase.value, len(self.pending_agent_callbacks),
        )
        if not self.pending_agent_callbacks:
            return False
        self._purge_retracted_agent_callbacks()
        if not self.pending_agent_callbacks:
            return False
        if self.is_goodbye_silent():
            logger.info(
                "[%s] trigger_agent_callbacks deferred: goodbye silent, keeping %d callback(s)",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False
        # 与 handle_text_data / handle_response_complete 等输出 handler 对偶：
        # takeover 期间普通 chat LLM 输出会被静音，所以现在派发会被吞掉、callback
        # 内容白丢。把入口卡住，callback 留在队列里等 takeover 释放。
        if self._takeover_active:
            logger.info(
                "[%s] trigger_agent_callbacks deferred: session takeover active, keeping %d callback(s) for next attempt",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False

        # Hard delivery contract: trigger_agent_callbacks ONLY consumes
        # proactive callbacks. Passive ones must remain in the queue and
        # surface only at the next user turn via drain_agent_callbacks_for_llm.
        # Without this filter, a passive callback enqueued earlier would get
        # piggy-backed onto any later proactive trigger — silently breaking
        # ``delivery="passive"``'s "don't interrupt" promise.
        proactive_cbs = _active_proactive_callbacks(self.pending_agent_callbacks)
        if not proactive_cbs:
            logger.debug(
                "[%s] trigger_agent_callbacks: queue has only passive callbacks (n=%d); deferring to next user turn",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False

        # Voice mode：直接 conversation.item.create(role=user) + response.create，
        # 让 LLM 立即用本角色嗓音主动回应 proactive callback，不等用户开口。
        #
        # Gate：realtime API 同一时刻只允许一个 active response。如果 user 正在
        # 说话（server-VAD 触发 → 自动 response.create）或上一个 response 还
        # 没结束（含 prompt_ephemeral 走的 fudge response），client 再发
        # response.create 会被 reject。phase != IDLE 时说明 text-mode proactive
        # 流水线在跑，也跳。两条都不满足时 callbacks 留在队列，等
        # _finalize_turn_after_emit 在 response.done 之后重新调用本函数重试。
        if isinstance(self.session, OmniRealtimeClient):
            # Serialize the whole check-and-claim against concurrent trigger
            # tasks (see ``_voice_proactive_inject_lock``). Hold the lock across
            # gate → render → inject → prune; a second task blocks here and,
            # once it acquires, re-filters the (now-pruned) queue and finds
            # nothing left to send.
            async with self._voice_proactive_inject_lock:
                # Read the session INSIDE the lock — start_session / end_session
                # / hot-swap may have swapped or torn it down while we waited
                # for the lock. Re-check the type; if it's no longer a voice
                # session, bail (a text-mode path / no session shouldn't be
                # driven from this branch). Using the lock-time instance for
                # gate + inject avoids injecting into a closing old session.
                voice_sess = self.session
                if not isinstance(voice_sess, OmniRealtimeClient):
                    return False
                # Re-filter inside the lock: a concurrent task may have already
                # injected+pruned these cbs while we waited on the lock.
                self._purge_retracted_agent_callbacks()
                proactive_cbs = _active_proactive_callbacks(self.pending_agent_callbacks)
                if not proactive_cbs:
                    return False
                # Playback-aware gate: ``_voice_playback_active`` is True
                # between the FRONTEND's voice_play_start and voice_play_end,
                # i.e. while buffered audio is still AUDIBLY playing — which
                # outlasts the realtime API's response.done (generation end).
                # Injecting then makes her interrupt herself, so defer; the
                # voice_play_end signal re-fires this and the manager releases
                # the next cue only once she has truly stopped talking.
                if (
                    self.state.phase is not ProactivePhase.IDLE
                    or voice_sess.is_active_response()
                    or self._is_voice_playing()
                ):
                    logger.debug(
                        "[%s] trigger_agent_callbacks: voice session busy (phase=%s, active_response=%s, playback=%s); deferring proactive (n=%d)",
                        self.lanlan_name,
                        self.state.phase.value,
                        voice_sess.is_active_response(),
                        self._voice_playback_active,
                        len(proactive_cbs),
                    )
                    return False

                _lang = normalize_language_code(self.user_language, format='short')
                voice_snapshot = [
                    cb for cb in proactive_cbs
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                if not voice_snapshot:
                    return False
                # NOTE: the callback instruction is built AFTER the media-stream
                # gate + retraction re-filter below (right before inject), so it
                # reflects the final delivered set. Don't build it here — that
                # copy would be stale the moment a cb retracts during streaming.
                # Snapshot the paired extras entries NOW (before prune) so the
                # rejection handler can restore BOTH queues if the server
                # rejects asynchronously.
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in voice_snapshot
                    if cb.get("_callback_delivery_id")
                }
                voice_extra_snapshot = [
                    extra for extra in self.pending_extra_replies
                    if extra.get("_callback_delivery_id") in delivered_ids
                ]

                # Server-side rejection of ``response.create`` (e.g.
                # ``response_already_active`` from a VAD race winning between
                # our gate check and our send) is delivered asynchronously as
                # an ``error`` event, not via this call's return value or an
                # exception — and ``handle_messages`` can dispatch it WHILE we
                # are still awaiting ``inject_text_and_request_response`` (i.e.
                # BEFORE the optimistic prune below runs). The handler must
                # survive both orderings:
                #   (a) reject fires DURING the await (cb still in the queue):
                #       set ``_rejected`` so the post-await code SKIPS the prune
                #       — otherwise the success prune would delete a cb the
                #       server refused. Do NOT re-add here (it's still present).
                #   (b) reject fires AFTER the trigger returned + pruned (cb
                #       gone): re-add to BOTH queues by id (dedup-guarded) and,
                #       if idle, re-fire trigger so it doesn't wait for the next
                #       unrelated response.done.
                # The dedup-by-presence check distinguishes the two: present →
                # case (a) (skip re-add, rely on skip-prune); absent → case (b).
                lanlan_name_snapshot = self.lanlan_name
                _reject_state = {"rejected": False}

                def _on_voice_inject_rejected(
                    error_msg: str,
                    _snapshot=voice_snapshot,
                    _extra_snapshot=voice_extra_snapshot,
                    _lanlan=lanlan_name_snapshot,
                    _state=_reject_state,
                ) -> None:
                    _state["rejected"] = True
                    logger.warning(
                        "[%s] voice proactive inject rejected by server: %s; re-enqueuing %d cb(s) for retry",
                        _lanlan, error_msg, len(_snapshot),
                    )
                    # Restore BOTH queues in lockstep — only entries whose
                    # delivery_id is not already present. Present means the
                    # optimistic prune hasn't run yet (case a): leave them and
                    # let the post-await ``_rejected`` check skip the prune.
                    existing_cb_ids = {
                        cb.get("_callback_delivery_id")
                        for cb in self.pending_agent_callbacks
                        if cb.get("_callback_delivery_id")
                    }
                    # Object-identity fallback, symmetric with the success-path
                    # prune: an unstamped cb (no _callback_delivery_id, e.g. a
                    # future caller bypassing enqueue_agent_callback) would
                    # otherwise fail the id-based dedup and get re-appended even
                    # when it's still in the queue (case a) — then skip-prune
                    # keeps both copies → double-delivery on retry. Dedup such
                    # entries by Python id().
                    existing_cb_obj_ids = {id(cb) for cb in self.pending_agent_callbacks}
                    existing_extra_ids = {
                        extra.get("_callback_delivery_id")
                        for extra in self.pending_extra_replies
                        if extra.get("_callback_delivery_id")
                    }
                    for cb in _snapshot:
                        cb_id = cb.get("_callback_delivery_id")
                        if (cb_id and cb_id in existing_cb_ids) or (
                            not cb_id and id(cb) in existing_cb_obj_ids
                        ):
                            continue
                        self.pending_agent_callbacks.append(cb)
                        if cb_id:
                            existing_cb_ids.add(cb_id)
                        else:
                            existing_cb_obj_ids.add(id(cb))
                    for extra in _extra_snapshot:
                        extra_id = extra.get("_callback_delivery_id")
                        if extra_id and extra_id in existing_extra_ids:
                            continue
                        self.pending_extra_replies.append(extra)
                    # Do NOT immediately re-fire trigger here. The dominant
                    # rejection is ``response_already_active``, which by
                    # definition means an active response exists — but the
                    # client may not have processed its ``response.created``
                    # yet, so ``is_active_response()`` reads a STALE False. A
                    # re-fire on that stale state would re-inject → re-reject →
                    # tight loop until state flips (Codex P1). Instead rely on
                    # the retry guaranteed by that active response's
                    # ``response.done`` → ``handle_response_complete`` →
                    # ``_finalize_turn_after_emit`` (which re-calls this when
                    # ``pending_agent_callbacks`` is non-empty). The cb is kept
                    # queued above, so the retry is not lost — just deferred to
                    # the loop-free turn-end hook.

                # Stream any images carried by these cues into the (guaranteed)
                # voice session right before inject, so the proactive response
                # sees the matching visual context (Codex P2).
                if not await self._stream_cb_media(voice_snapshot, voice_sess):
                    # A media stream failed — DEFER the whole inject so this cb
                    # retries WITH its image rather than being delivered
                    # text-only and pruned (which would lose the retained
                    # media). cbs are still in pending_agent_callbacks (not yet
                    # pruned). The manager already emptied its queue and its
                    # inflight timeout only pumps manager-queued items, and no
                    # response.create fired (so no response.done / voice_play_end
                    # to re-drive trigger) — so re-arm a delayed retry here,
                    # otherwise a transient media/WS failure leaves the cue
                    # waiting for an unrelated user turn (Codex P2).
                    logger.info(
                        "[%s] trigger_agent_callbacks: proactive media stream failed; deferring voice inject (%d cb kept, retry armed)",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    self._schedule_proactive_retry(self.proactive_manager.min_gap_s)
                    return False
                # Pull-model staleness: a newer same-coalesce_key cue may have
                # been submitted during the media await above (possibly still
                # held by the manager, so the enqueue-time push scan never saw
                # this snapshot). Retract the superseded cues so the re-filter
                # below drops them and the purge sweeps both queues.
                self._retract_stale_coalesced(voice_snapshot)
                voice_snapshot[:] = [
                    cb for cb in voice_snapshot
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                self._purge_retracted_agent_callbacks()
                if not voice_snapshot:
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice proactive callbacks retracted before inject",
                        self.lanlan_name,
                    )
                    return False
                instruction = _build_callback_instruction(
                    voice_snapshot,
                    lang=_lang,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name,
                    passive=False,
                )
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in voice_snapshot
                    if cb.get("_callback_delivery_id")
                }
                voice_extra_snapshot[:] = [
                    extra for extra in voice_extra_snapshot
                    if extra.get("_callback_delivery_id") in delivered_ids
                ]
                try:
                    await voice_sess.inject_text_and_request_response(
                        instruction, on_rejected=_on_voice_inject_rejected
                    )
                except NotImplementedError:
                    # Defensive fallback. As of now every realtime provider
                    # (OpenAI / GLM / Step / free / GPT / Qwen / Grok via
                    # conversation.item.create, Gemini via send_client_content)
                    # supports manual inject, so this branch is unreachable in
                    # practice — kept so a hypothetical future provider that
                    # raises NotImplementedError degrades to hot-swap instead of
                    # losing the cb. Drop the proactive cbs so they don't loop
                    # forever, but keep ``pending_extra_replies`` populated for
                    # the next user-turn prime_context() drain.
                    voice_ids = {id(cb) for cb in voice_snapshot}
                    self.pending_agent_callbacks = [
                        cb for cb in self.pending_agent_callbacks
                        if id(cb) not in voice_ids
                    ]
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice provider does not support manual inject; falling back to hot-swap (n=%d)",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    return False
                except Exception as exc:
                    # WS error / fatal / response_already_active race — keep cbs
                    # in the queue so the next phase-idle hook retries them.
                    logger.warning(
                        "[%s] trigger_agent_callbacks: voice proactive inject failed: %s; keeping cbs for retry",
                        self.lanlan_name, exc,
                    )
                    return False

                # If the server rejected asynchronously DURING the await above
                # (case a — ``_on_voice_inject_rejected`` already fired while
                # the cbs were still in the queue), the cbs were intentionally
                # left in place. Pruning now would delete a cb the server
                # refused → silent loss. Skip the prune; the cbs stay queued and
                # are retried via _finalize_turn_after_emit (or the re-fire the
                # handler scheduled). The active response that caused the
                # rejection will fire response.done and trigger the retry.
                if _reject_state["rejected"]:
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice proactive inject rejected during await; keeping %d cb(s) queued for retry",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    return False

                # Inject succeeded. Drop the cbs we delivered from BOTH queues:
                # ``pending_agent_callbacks`` (text-mode drain + proactive
                # trigger) AND the matching ``pending_extra_replies`` entries
                # (voice hot-swap prime channel). Leaving the extras intact would
                # have two concrete bad consequences:
                #   1. ``_finalize_turn_after_emit`` gates immediate session
                #      preparation on ``bool(pending_extra_replies)`` — stale
                #      entries trigger needless background hot-swap prep.
                #   2. The eventual hot-swap re-primes the new session with cbs
                #      the AI already spoke about, producing duplicate
                #      announcements.
                # Match by the stable ``_callback_delivery_id`` stamped on both
                # entries by ``enqueue_agent_callback``. Length-based alignment
                # would be unsafe — ``drain_agent_callbacks_for_llm`` clears
                # ``pending_agent_callbacks`` while leaving
                # ``pending_extra_replies`` intact, so the queues legitimately
                # drift apart across user turns.
                # Object-identity fallback for pending_agent_callbacks: defense
                # in depth against any future code path that appends a cb
                # without going through ``enqueue_agent_callback`` (the only
                # stamper of ``_callback_delivery_id``). extras dicts are fresh
                # objects so there is no id() link — extras rely on the
                # delivery_id contract.
                voice_obj_ids = {id(cb) for cb in voice_snapshot}
                self.pending_agent_callbacks = [
                    cb for cb in self.pending_agent_callbacks
                    if cb.get("_callback_delivery_id") not in delivered_ids
                    and id(cb) not in voice_obj_ids
                ]
                self.pending_extra_replies = [
                    extra for extra in self.pending_extra_replies
                    if extra.get("_callback_delivery_id") not in delivered_ids
                ]
                logger.info(
                    "[%s] trigger_agent_callbacks: voice proactive inject sent (n=%d)",
                    self.lanlan_name, len(voice_snapshot),
                )
                def _resolve_voice_ack_after_rejection_window(
                    _snapshot=tuple(voice_snapshot),
                    _state=_reject_state,
                ) -> None:
                    if _state["rejected"]:
                        return
                    for cb in _snapshot:
                        if cb.get(DELIVERY_RETRACTED_KEY):
                            continue
                        resolve_callback_delivery_ack(cb, True)

                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(
                        _VOICE_PROACTIVE_ACK_GRACE_S,
                        _resolve_voice_ack_after_rejection_window,
                    )
                except RuntimeError:
                    _resolve_voice_ack_after_rejection_window()
                return True

        callbacks_snapshot = list(proactive_cbs)

        # 原子 check-and-claim：若另一路 proactive（router/greeting）在跑或 AI
        # 正在为用户回复，SM 拒绝本次投递，callbacks 留在 pending 下轮重试。
        claim_session = self.session if isinstance(self.session, OmniOfflineClient) else None
        if not await self.state.try_start_proactive(session=claim_session):
            logger.debug(
                "[%s] trigger_agent_callbacks: SM denied claim (phase=%s), re-queuing",
                self.lanlan_name, self.state.phase.value,
            )
            return False

        callbacks_snapshot = [
            cb for cb in callbacks_snapshot
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        self._purge_retracted_agent_callbacks()
        if not callbacks_snapshot:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)
            return False

        # Drop only the snapshot cbs from the queue once we have the SM
        # claim — keep both pre-existing passive cbs and any callbacks
        # that another task enqueued during the ``await try_start_proactive``
        # window (``enqueue_agent_callback`` is sync + lock-free, so this race
        # window is real). Filtering by ``delivery_mode == "passive"`` would
        # wipe such fresh proactive cbs since ``callbacks_snapshot`` only
        # restores pre-claim entries on exception. preempt / not-delivered /
        # exception 路径靠 ``extend(callbacks_snapshot)`` 把本次 snapshot
        # 放回队列，保证投递失败不会丢消息。
        snapshot_ids = {id(cb) for cb in callbacks_snapshot}
        self.pending_agent_callbacks = [
            cb for cb in self.pending_agent_callbacks
            if id(cb) not in snapshot_ids
        ]

        delivered = False
        try:
            if isinstance(self.session, OmniOfflineClient):
                delivered = await self._deliver_agent_callbacks_text(callbacks_snapshot)
            else:
                ws = self.websocket
                if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                    try:
                        await self.start_session(ws, new=False, input_mode='text')
                    except Exception as e:
                        logger.warning("[%s] trigger_agent_callbacks: auto start_session failed: %s", self.lanlan_name, e)
                if isinstance(self.session, OmniOfflineClient):
                    delivered = await self._deliver_agent_callbacks_text(callbacks_snapshot)
                    logger.debug("[%s] trigger_agent_callbacks: auto text session delivered", self.lanlan_name)
                else:
                    logger.debug("[%s] trigger_agent_callbacks: no websocket/session, re-queueing for later", self.lanlan_name)
                    self.pending_agent_callbacks.extend(callbacks_snapshot)
                    callbacks_snapshot[:] = []
        except Exception as e:
            logger.warning("[%s] trigger_agent_callbacks error: %s", self.lanlan_name, e)
            self.pending_agent_callbacks.extend(callbacks_snapshot)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)
        if delivered:
            for cb in callbacks_snapshot:
                resolve_callback_delivery_ack(cb, True)
        return delivered

    async def _deliver_agent_callbacks_text(self, callbacks_snapshot: list) -> bool:
        """Execute prompt_ephemeral on an OmniOfflineClient session inside the
        proactive write lock. Caller holds the SM proactive claim (PHASE1).

        Returns True iff genuinely delivered. Returns False when the user preempts
        between the claim and the lock (``mark_user_input_preempt`` flipped
        ``_preempted`` inside ``self.lock`` and ``current_speech_id`` has already
        rotated to the new user sid) — in that case we must not overwrite.
        """
        async with self._proactive_write_lock:
            async with self.lock:
                # Delivery-point topic re-gate (1/2 — cheap early-out before the
                # sid claim). A topic hook can pass the release gate, get copied
                # into callbacks_snapshot + removed from pending_agent_callbacks,
                # then this trigger parks on try_start_proactive /
                # _proactive_write_lock while the user starts a new turn, opens a
                # voice session, or otherwise closes the callback-specific gate.
                # That in-flight snapshot is in neither queue, so queue sweeps
                # cannot reach it and the release gate's check has gone stale.
                # Drop topic hooks with ack False so TopicHookPool retries later;
                # the retracted filter below removes them + their extras. A SECOND
                # identical re-gate runs right before prompt_ephemeral to catch a
                # gate closure that lands during the CLAIM/PHASE2 awaits in between.
                if self._retract_unavailable_topic_hook_snapshots(callbacks_snapshot):
                    logger.info("[%s] trigger_agent_callbacks: topic hook dropped before claim — delivery gate closed mid-delivery", self.lanlan_name)
                # Pull-model staleness (1/2, cheap early-out): the snapshot was
                # checked OUT of pending_agent_callbacks before the claim awaits,
                # so a newer same-coalesce_key cue enqueued meanwhile could not
                # retract it via the push-side queue scan.
                self._retract_stale_coalesced(callbacks_snapshot)
                self._purge_retracted_agent_callback_extras(callbacks_snapshot)
                active_callbacks = [
                    cb for cb in callbacks_snapshot
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                if not active_callbacks:
                    logger.info("[%s] trigger_agent_callbacks: text proactive callbacks retracted before prompt", self.lanlan_name)
                    # Nothing will emit text_start/text_end to free the manager's
                    # inflight slot, so release it now (mirrors
                    # _deliver_proactive_batch's no-op release) — else the next
                    # cue stalls until the inflight timeout.
                    self.proactive_manager.release_inflight_noop()
                    return False
                callbacks_snapshot[:] = active_callbacks
                # sticky preempt 复查：与 prepare_proactive_delivery 同样，在持有
                # self.lock 的临界区内判定。USER_INPUT 路径在本锁段内翻 flag 和
                # 写 user sid 是原子的，如果此处 preempt==True 说明用户已抢到
                # 本轮 turn，必须放弃本次 proactive（否则会把用户刚写好的 sid
                # 再覆盖成 proactive sid，污染 TTS/chunk 分发）。
                if self.state.is_proactive_preempted():
                    logger.info("[%s] trigger_agent_callbacks: preempted before sid claim, skipping", self.lanlan_name)
                    self.pending_agent_callbacks.extend(active_callbacks)
                    return False
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False
                self._tts_done_pending_until_ready = False
                proactive_sid = self.current_speech_id
            # SM：发射 CLAIM（把 proactive_sid 写入 state，供诊断/订阅者观察）
            # 随后立刻 PHASE2，因 prompt_ephemeral 没有可分离的 phase1/phase2 边界
            await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
            await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
            logger.debug("[%s] trigger_agent_callbacks: text session ready, calling prompt_ephemeral", self.lanlan_name)
            # 更新字数限制（可能用户在对话期间修改了设置）
            if hasattr(self.session, 'update_max_response_length'):
                self.session.update_max_response_length(self._get_text_guard_max_length())
            # NOTE: queue mutation moved to caller (trigger_agent_callbacks
            # extracts the proactive subset before claim). Do NOT clear
            # pending_agent_callbacks here — passive cbs would also get wiped.
            # per-task contextvar：prompt_ephemeral 回调链里 handle_text_data
            # 识别本路径 chunk 并在 sid 被用户抢走后丢弃
            # Collect proactive images carried ON the callbacks and pass them
            # EXPLICITLY to prompt_ephemeral — separate from the user's
            # _pending_images staging queue (which holds the user's next
            # screen/camera frame). Sharing that queue would steal the user's
            # pending image into this proactive turn and rob the user's next
            # message of its visual context (Codex P2). Media stays on the cb
            # until the cb is delivered & pruned, so a failed retry re-collects
            # and re-passes it (preserve-until-success). NOTE: we do NOT call
            # _stream_cb_media for text mode (that's the voice path, which uses
            # the realtime session's persistent conversation.item).
            # Delivery-point topic re-gate (2/2 — authoritative, immediately
            # before prompt_ephemeral). CLAIM/PHASE2 were just awaited above, so
            # the user may have switched to audio or sent a fresh turn since the
            # pre-claim check; re-drop topic hooks here so a stale hook cannot
            # still prompt the old text session.
            if self._retract_unavailable_topic_hook_snapshots(active_callbacks):
                logger.info("[%s] trigger_agent_callbacks: topic hook dropped at prompt — delivery gate closed mid-delivery", self.lanlan_name)
            # Pull-model staleness (2/2, authoritative — immediately before
            # prompt_ephemeral): catches a newer same-coalesce_key cue that
            # landed during the CLAIM/PHASE2 awaits above.
            self._retract_stale_coalesced(active_callbacks)
            self._purge_retracted_agent_callback_extras(active_callbacks)
            active_callbacks = [
                cb for cb in active_callbacks
                if not cb.get(DELIVERY_RETRACTED_KEY)
            ]
            callbacks_snapshot[:] = active_callbacks
            if not active_callbacks:
                logger.info("[%s] trigger_agent_callbacks: text proactive callbacks retracted before prompt", self.lanlan_name)
                # Free the inflight slot — text_start/text_end below won't run.
                self.proactive_manager.release_inflight_noop()
                return False
            async with self.lock:
                preempted_before_prompt = (
                    self.state.is_proactive_preempted()
                    or self.current_speech_id != proactive_sid
                )
            if preempted_before_prompt:
                logger.info("[%s] trigger_agent_callbacks: preempted before prompt, re-queueing", self.lanlan_name)
                self.pending_agent_callbacks.extend(active_callbacks)
                callbacks_snapshot[:] = []
                self.proactive_manager.release_inflight_noop()
                return False
            _proactive_images: list = []
            for _cb in active_callbacks:
                if isinstance(_cb, dict):
                    _proactive_images.extend(_cb.get("media_images") or [])
            _lang = normalize_language_code(self.user_language, format='short')
            instruction = _build_callback_instruction(
                active_callbacks,
                lang=_lang,
                lanlan_name=self.lanlan_name,
                master_name=self.master_name,
                passive=False,
            )
            ack_resolved = False

            def _resolve_text_delivery_ack(delivered: bool) -> None:
                nonlocal ack_resolved
                if ack_resolved:
                    return
                ack_resolved = True
                for cb in active_callbacks:
                    resolve_callback_delivery_ack(cb, delivered)

            # Deep-topic teaser: now committed to opening (passed both re-gates
            # and the preempt check), surface the frontend-only "she has a topic
            # she'd like to bring up" bubble just before the opener streams. One
            # bubble per batch even if several topic hooks coalesced. Failure to
            # send is non-fatal — the opener still goes out. If the opener then
            # fails before any committed output (API error / no-output), the
            # teaser is retracted below so it can't dangle as an orphan.
            topic_hint_sent = False
            if any(
                isinstance(cb, dict) and cb.get("channel") == "topic_hook"
                for cb in active_callbacks
            ):
                topic_hint_sent = await self.send_topic_hint(turn_id=proactive_sid)
                # send_topic_hint awaits a WS write — a NEW yield point past the
                # last preempt check. If the user grabbed the turn during that
                # await, abort before prompt_ephemeral (whose output would be
                # SID-dropped yet could still ack as committed and leave the
                # teaser/history as if the opener landed): retract the teaser and
                # requeue, mirroring the preempt handling above.
                async with self.lock:
                    preempted_after_hint = (
                        self.state.is_proactive_preempted()
                        or self.current_speech_id != proactive_sid
                    )
                if preempted_after_hint:
                    logger.info("[%s] trigger_agent_callbacks: preempted during topic hint send, aborting before prompt", self.lanlan_name)
                    if topic_hint_sent:
                        await self.send_cancel_topic_hint(turn_id=proactive_sid)
                    self.pending_agent_callbacks.extend(active_callbacks)
                    callbacks_snapshot[:] = []
                    self.proactive_manager.release_inflight_noop()
                    return False

            _sid_token = _proactive_expected_sid.set(proactive_sid)
            # Text-mode playback boundary for the pacing manager: no frontend
            # audio signal arrives for text delivery, so bracket prompt_ephemeral
            # with text_start/text_end. text_end clears the manager's in-flight
            # slot + applies min-gap before the next proactive cue.
            try:
                self.lifecycle_bus.emit("text_start")
            except Exception:
                # A lifecycle signal must never break delivery. The bus
                # already isolates per-handler failures (logger.exception);
                # this guard only covers an emit() that itself somehow raises.
                logger.debug("[%s] lifecycle_bus emit(text_start) failed", self.lanlan_name)
            try:
                try:
                    delivered = await self.session.prompt_ephemeral(
                        instruction,
                        images=_proactive_images or None,
                        on_committed=lambda: _resolve_text_delivery_ack(True),
                    )
                except Exception as exc:
                    if ack_resolved:
                        logger.warning(
                            "[%s] trigger_agent_callbacks: prompt_ephemeral failed after committed output; treating callback delivery as complete: %s",
                            self.lanlan_name,
                            exc,
                        )
                        delivered = True
                    else:
                        # Opener errored before any committed output — retract the
                        # teaser so the frontend doesn't keep an orphan bubble.
                        if topic_hint_sent:
                            await self.send_cancel_topic_hint(turn_id=proactive_sid)
                        raise
            finally:
                _proactive_expected_sid.reset(_sid_token)
                try:
                    self.lifecycle_bus.emit("text_end")
                except Exception:
                    # Same rationale as text_start; never let signalling break
                    # the delivery path's finally cleanup.
                    logger.debug("[%s] lifecycle_bus emit(text_end) failed", self.lanlan_name)
            logger.debug("[%s] trigger_agent_callbacks: prompt_ephemeral delivered=%s", self.lanlan_name, delivered)
            if delivered or ack_resolved:
                _resolve_text_delivery_ack(True)
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in active_callbacks
                    if cb.get("_callback_delivery_id")
                }
                if delivered_ids:
                    self.pending_extra_replies = [
                        extra for extra in self.pending_extra_replies
                        if extra.get("_callback_delivery_id") not in delivered_ids
                    ]
                return True
            else:
                _resolve_text_delivery_ack(False)
                # No committed output — retract the teaser so it can't dangle
                # while the callback is requeued for a later retry (which will
                # send its own fresh teaser).
                if topic_hint_sent:
                    await self.send_cancel_topic_hint(turn_id=proactive_sid)
                self.pending_agent_callbacks.extend(active_callbacks)
                return False

    def _is_voice_session_active_or_starting(self) -> bool:
        """Returns True while a voice session is starting or already active, to keep greetings from disturbing the voice stream."""
        if self._starting_session_count > 0 and (self._starting_input_mode or self.input_mode) == 'audio':
            return True
        if self.is_active and self.input_mode == 'audio':
            return True
        return False

    def _voice_delivery_blocked(self) -> bool:
        """True whenever a deep-topic hook could still reach the voice path, so
        topic delivery must defer. The union of two predicates, each covering a
        transition window the other misses:
          - ``isinstance(self.session, OmniRealtimeClient)``: the live session is
            realtime. This still holds during an audio→text switch, where
            ``start_session`` flips the input-mode flags to text while the old
            voice session lingers in ``self.session`` for several awaited
            teardown steps — and ``trigger_agent_callbacks`` would still take its
            ``isinstance``-gated voice branch and inject into that old session.
          - ``_is_voice_session_active_or_starting()``: a voice session is active
            or starting, covering the text→audio startup window before the
            realtime client is installed in ``self.session``.
        Using the union keeps the gate aligned with the exact condition under
        which the voice branch fires."""
        return (
            isinstance(self.session, OmniRealtimeClient)
            or self._is_voice_session_active_or_starting()
        )

    def topic_hook_delivery_allowed(self) -> bool:
        """Whether a background deep-topic hook may interrupt right now.

        Deep topic hooks are brand-new text openers — the most intrusive,
        "better none than forced" kind of proactive content. They must honour
        the same activity gate as ``/api/proactive_chat``: delivery never
        surfaces when the user's propensity is ``closed`` or
        ``restricted_screen_only`` (gaming / focused_work). Unlike the
        proactive reminiscence path there is NO open-thread exception — a
        fresh deep topic is not a follow-up to something already on the table,
        so it shouldn't borrow that escape hatch.

        Voice sessions never receive deep topic hooks. A topic hook is a
        text-mode opener; injecting one mid voice conversation would cut across
        a live spoken exchange, which is exactly the "forced" intrusion this
        feature avoids. Gate on ``_voice_delivery_blocked()`` — the union of "the
        live session is realtime" and "a voice session is active/starting" — so
        the gate matches the exact condition under which
        ``trigger_agent_callbacks`` takes its voice branch, including both the
        text→audio startup window (realtime client not yet installed) and the
        audio→text teardown window (old realtime client still in ``self.session``).
        Returning False here defers rather than drops — the process-global
        per-character ``TopicHookPool`` keeps the material pending and retries
        it once the user is back in a text session, so a voice-heavy user still
        gets the hook later instead of losing it. This is the chokepoint both
        delivery gates consult (``_topic_activity_gate_open`` at submit,
        ``_deliver_proactive_batch`` at release); the session-start drain /
        already-pending / extras-only paths are closed separately in
        ``_reset_proactive_gate`` + ``_drop_pending_topic_hooks_for_voice``.

        Privacy mode is deliberately NOT checked here and no longer gates the
        deep-topic chain upstream either. Store/candidate/prepare/delivery all
        proceed independently from that toggle; this method only answers
        whether a prepared hook may interrupt the current activity context.
        Activity snapshot lookup remains fail-open when no snapshot is
        available, mirroring the proactive path's "snapshot None ⇒ open
        propensity" default.
        """
        if self._voice_delivery_blocked():
            return False
        tracker = getattr(self, '_activity_tracker', None)
        if tracker is None:
            return True
        try:
            snap = tracker.get_snapshot_sync()
        except Exception:
            return True
        propensity = getattr(snap, 'propensity', None)
        if propensity in ('closed', 'restricted_screen_only'):
            return False
        if getattr(snap, 'unfinished_thread', None) is not None:
            logger.info(
                "[%s] topic hook delivery skipped: unfinished thread is still open",
                self.lanlan_name,
            )
            return False
        return True

    def current_topic_language(self) -> Optional[str]:
        """Live full-locale topic language, for re-resolving at delivery time.

        A topic hook captures its language when it is scheduled; if the
        session language changes while the material is pending delivery, that
        captured value goes stale. Topic delivery re-resolves from here so the
        hook renders in the current locale (preserving zh-TW etc.). Returns
        None when no dispatcher is available so the caller keeps the captured
        language.
        """
        dispatcher = getattr(self, '_turn_dispatcher', None)
        getter = getattr(dispatcher, 'current_language', None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _next_coalesce_seq(self) -> int:
        """Monotonic per-manager submission counter shared by the manager-held
        (submit_proactive_callback) and direct-enqueue (enqueue_agent_callback)
        coalescing paths, so same-key newest-wins is consistent across both.
        Lazily initialised — the core mixins have no single __init__."""
        seq = getattr(self, "_coalesce_seq_counter", 0) + 1
        self._coalesce_seq_counter = seq
        return seq

    def _note_coalesce_submission(self, key: str, seq: int) -> None:
        """Record the latest submission seq seen for a coalesce_key.

        This map is the pull-model source of truth for staleness: enqueue-time
        (push) retraction can only reach cues sitting in the LIVE queues, while
        a cue checked out into a local delivery snapshot (voice_snapshot, the
        text callbacks_snapshot, a hot-swap prime selection) is invisible to
        that scan across its await boundaries. Delivery points therefore ask
        ``_coalesce_entry_is_stale`` / ``_retract_stale_coalesced`` right
        before actually sending, which needs the freshest seq per key recorded
        HERE at both submission sites — including manager submit time, so a
        newer cue still held by ProactiveDeliveryManager already marks older
        same-key cues stale. Lazily initialised like ``_coalesce_seq_counter``."""
        latest = getattr(self, "_coalesce_latest", None)
        if latest is None:
            latest = self._coalesce_latest = {}
        if seq > latest.get(key, -1):
            latest[key] = seq

    def _coalesce_entry_is_stale(self, entry: Any) -> bool:
        """True when ``entry``'s coalesce_key has a NEWER recorded submission.

        Works for both callback dicts and their pending_extra_replies mirrors
        (both carry ``coalesce_key`` + ``_coalesce_submit_seq``). Non-dict
        (legacy plain-string extras), unkeyed, or unstamped entries are never
        stale — coalescing stays strictly opt-in."""
        if not isinstance(entry, dict):
            return False
        key = str(entry.get("coalesce_key") or "").strip()
        seq = entry.get("_coalesce_submit_seq")
        if not key or not isinstance(seq, int):
            return False
        latest = getattr(self, "_coalesce_latest", {}).get(key)
        return isinstance(latest, int) and latest > seq

    def _retract_stale_coalesced(self, callbacks: list) -> bool:
        """Pull-model staleness sweep for a delivery-point snapshot.

        Marks every same-key-superseded callback retracted (ack False), so the
        existing ``DELIVERY_RETRACTED_KEY`` re-filters at each delivery point
        drop it — exactly like the other mid-delivery removal paths. Returns
        True when anything was retracted."""
        retracted = False
        for cb in callbacks:
            if not isinstance(cb, dict) or cb.get(DELIVERY_RETRACTED_KEY):
                continue
            if self._coalesce_entry_is_stale(cb):
                resolve_callback_delivery_ack(cb, False)
                cb[DELIVERY_RETRACTED_KEY] = True
                retracted = True
        if retracted:
            logger.info(
                "[%s] retracted stale coalesced callback(s) at delivery point",
                self.lanlan_name,
            )
        return retracted

    def submit_proactive_callback(
        self,
        callback: dict,
        *,
        priority: int = 0,
        coalesce_key: Optional[str] = None,
    ) -> None:
        """Hand a proactive (ai_behavior="respond") cue to the delivery
        manager, which paces/orders/coalesces it before release.

        Replaces the EventBus's old "enqueue + immediately fire trigger"
        for proactive cues. Passive/silent cues do NOT come here — they keep
        their existing direct enqueue-only path so ``delivery="passive"``'s
        "don't interrupt" promise is unchanged.
        """
        # Persist the coalesce_key + a monotonic submission seq onto the callback
        # up front so it survives the manager→enqueue handoff (the manager stores
        # the key on its cue, not the callback dict) and both delivery paths order
        # by the same clock. Without the writeback a manager-released respond cue
        # reaches enqueue with an empty key and skips coalescing ("Manager key is
        # lost"); without the seq the enqueue path cannot tell a late manager
        # release of an older respond cue from a newer direct-queued read cue.
        if coalesce_key:
            callback["coalesce_key"] = coalesce_key
        _key = str(callback.get("coalesce_key") or "").strip()
        if _key:
            callback.setdefault("_coalesce_submit_seq", self._next_coalesce_seq())
            # Bump the latest-seq map AT SUBMIT TIME (not only at enqueue): a
            # newer cue may sit in ProactiveDeliveryManager through a whole
            # playback window before reaching enqueue, and the pull-model
            # delivery guards must already see older same-key cues as stale
            # during exactly that window.
            self._note_coalesce_submission(_key, callback["_coalesce_submit_seq"])
        if self.is_goodbye_silent():
            self.enqueue_agent_callback(callback)
            logger.debug(
                "[%s] goodbye_silent queued proactive callback for later delivery",
                self.lanlan_name,
            )
            return
        self.proactive_manager.submit(callback, priority=priority, coalesce_key=coalesce_key)

    async def _deliver_proactive_batch(self, callbacks: list) -> None:
        """Release hook invoked by ProactiveDeliveryManager when the gate is
        open. Enqueues the WHOLE batch then fires ONE trigger — trigger drains
        all pending proactive callbacks into a single LLM turn, restoring the
        legacy "several near-simultaneous cues batched into one turn"
        behaviour (the manager only governs WHEN the batch is released, not
        how many cues per turn)."""
        callbacks = [cb for cb in callbacks if not cb.get(DELIVERY_RETRACTED_KEY)]
        # Topic hooks re-validate the delivery gate at RELEASE: the submit-time
        # check in trigger_topic_hook_once can go stale while the manager paces
        # the cue (min-gap / playback). If the user has since moved into a
        # restricted activity OR a voice session has taken over (topic hooks are
        # text-mode openers, never injected mid voice — see
        # topic_hook_delivery_allowed), drop the topic hook (ack=False) so
        # TopicHookPool retries later instead of opening a fresh deep topic at
        # the wrong moment. Other channels are unaffected.
        if callbacks:
            kept = []
            for cb in callbacks:
                if cb.get("channel") == "topic_hook" and not self._topic_hook_release_allowed(cb):
                    resolve_callback_delivery_ack(cb, False)
                    logger.info(
                        "[%s] topic hook held at release: delivery gate restricts interruption",
                        self.lanlan_name,
                    )
                else:
                    kept.append(cb)
            callbacks = kept
        if not callbacks:
            # This release delivered nothing (everything retracted or dropped
            # at the gate), so no playback/text lifecycle signal will arrive to
            # clear the manager's inflight slot. Free it now so the next cue
            # isn't held behind a phantom in-flight delivery for the timeout.
            self.proactive_manager.release_inflight_noop()
            return
        for callback in callbacks:
            self.enqueue_agent_callback(callback)
        # enqueue-time coalescing may retract a released cue on the spot (an
        # older manager-held respond cue losing to a newer same-key cue that was
        # direct-queued while it waited out playback). If the WHOLE batch died
        # that way, nothing below will emit a playback/text signal — free the
        # manager's inflight slot now (mirrors the pre-enqueue empty check
        # above) instead of stalling the next cue until the inflight timeout.
        if all(cb.get(DELIVERY_RETRACTED_KEY) for cb in callbacks):
            self.proactive_manager.release_inflight_noop()
            return
        # NOTE: images carried by these cues (push_message media_parts for
        # ai_behavior="respond") are streamed at the ACTUAL delivery point
        # inside trigger_agent_callbacks via _stream_cb_media — NOT here. That
        # binds streaming to a guaranteed session and covers every delivery
        # path (manager release / reconnect redelivery / turn-end retry),
        # instead of streaming into a possibly-None / about-to-be-swapped
        # session at release time (Codex P2).
        await self.trigger_agent_callbacks()

    async def _stream_cb_media(self, callbacks: list, session) -> bool:
        """Stream images carried by proactive callbacks (push_message
        media_parts with ai_behavior="respond") into ``session`` right before
        delivery, so the proactive response sees matching visual context.

        Returns False if ANY image failed to stream — the caller must then
        DEFER the whole delivery (don't inject/prompt the text), so the cb
        retries WITH its media next time. Delivering text-only here and then
        pruning the cb would drop the retained media for good (Codex P2).
        Returns True when every image streamed (or there were none / no
        session).

        Bound to the delivery point (not the manager-release point) so it
        covers every path that actually delivers a cb — manager release,
        reconnect redelivery, turn-end retry.

        VOICE path only: OmniRealtimeClient.stream_image() persists the image
        as a conversation.item the immediately-following proactive
        response.create sees (same-turn), and the realtime conversation is an
        accumulating log (not a single-consume queue), so adding a proactive
        image can't steal a user's pending frame. TEXT mode does NOT go through
        here — its proactive images are passed explicitly to prompt_ephemeral()
        (separate from the user's _pending_images staging queue); see
        _deliver_agent_callbacks_text.

        Media is LEFT on the cb (NOT popped) until the cb is delivered &
        pruned, so a deferred / failed-and-retried cb re-streams it instead of
        losing the visual context. On a PARTIAL stream failure the FULL set is
        kept (not just the tail): a stream failure usually means the session is
        closing, so the retry lands on a new session that has none of the
        earlier images — re-streaming everything is correct (Codex P2)."""
        si = getattr(session, "stream_image", None)
        if si is None:
            return True
        all_ok = True
        for cb in callbacks:
            if not isinstance(cb, dict):
                continue
            images = cb.get("media_images")
            if not images:
                continue
            streamed = 0
            for b64 in images:
                try:
                    # Deliberate cue image: bypass the native-vision frame-rate
                    # throttle so it isn't silently dropped behind a recent
                    # high-frequency screen/camera frame (Codex P2).
                    await si(b64, bypass_rate_limit=True)
                    streamed += 1
                except Exception as e:
                    # Keep the FULL media set (do NOT trim already-streamed
                    # ones): a voice stream_image failure almost always means
                    # the session is closing, so the retry runs on a NEW session
                    # whose conversation has none of the earlier images —
                    # trimming would permanently drop them. Re-streaming the
                    # whole set on the (likely new) session is correct; the only
                    # downside is duplicate items if the SAME session is retried,
                    # which is rare in a failure path and harmless (Codex P2 —
                    # overrides the earlier tail-trim). media_images is left
                    # untouched (already the full set). Signal the caller to
                    # DEFER (don't send text-only and prune the media away).
                    logger.warning(
                        "[%s] proactive media stream_image failed (streamed %d/%d); keeping FULL set for retry: %s",
                        self.lanlan_name, streamed, len(images), e,
                    )
                    all_ok = False
                    break
            # All streamed: keep media_images on the cb until it's delivered+
            # pruned (preserve-until-success) so an inject/prompt failure retry
            # re-streams it. Successful delivery removes the cb (and its media).
        return all_ok

    def on_voice_playback_signal(self, *, playing: bool, **meta) -> None:
        """Handle a FRONTEND-reported audio playback boundary.

        ``playing=True`` (voice_play_start) → real audio started; close the
        voice inject gate. ``playing=False`` (voice_play_end) → the browser's
        audio queue fully drained (she actually stopped talking) → open the
        gate and let the manager release the next cue. Re-fires
        ``trigger_agent_callbacks`` on end so a cue deferred mid-playback is
        delivered promptly rather than waiting for the next response.done.
        """
        self._voice_playback_active = bool(playing)
        if playing:
            self._voice_playback_started_ts = time.monotonic()
        try:
            self.lifecycle_bus.emit(
                "voice_play_start" if playing else "voice_play_end", **meta
            )
        except Exception:
            logger.exception("[%s] lifecycle_bus emit failed", self.lanlan_name)
        if not playing and self.pending_agent_callbacks:
            # A cue deferred while she was speaking (gate busy at release) can
            # now go out — but honor the manager's min-gap so this retry doesn't
            # start the next proactive turn with ZERO gap right at audio end.
            # Parity with the manager's own post-playback pump, which also
            # waits min_gap (Codex P2). trigger_agent_callbacks re-gates itself,
            # so a fire that lands while she's speaking again just defers.
            try:
                delay = max(0.0, float(self.proactive_manager.min_gap_s))
            except Exception:
                delay = 0.0
            if delay <= 0.0:
                self._fire_task(self.trigger_agent_callbacks())
            else:
                try:
                    asyncio.get_running_loop().call_later(
                        delay, lambda: self._fire_task(self.trigger_agent_callbacks())
                    )
                except RuntimeError:
                    self._fire_task(self.trigger_agent_callbacks())

    def _is_voice_playing(self) -> bool:
        """Time-bounded read of the playback gate. Auto-clears a stuck
        ``_voice_playback_active`` when no voice_play_end has arrived within
        ``_VOICE_PLAYBACK_STALE_S`` (frontend disconnect/refresh mid-playback),
        so the voice inject gate can never wedge proactive delivery forever."""
        if not self._voice_playback_active:
            return False
        if time.monotonic() - self._voice_playback_started_ts > self._VOICE_PLAYBACK_STALE_S:
            logger.warning(
                "[%s] voice playback gate watchdog: no voice_play_end after %.0fs; clearing stuck flag",
                self.lanlan_name, self._VOICE_PLAYBACK_STALE_S,
            )
            self._voice_playback_active = False
            return False
        return True

    def _schedule_proactive_retry(self, delay: float) -> None:
        """Schedule a delayed ``trigger_agent_callbacks`` so a cb left in
        pending_agent_callbacks (e.g. the voice media-stream deferral path) is
        retried even when nothing else would drive it — the manager has already
        emptied its queue and its inflight timeout only pumps manager-queued
        items, and a media failure before response.create means no
        response.done / voice_play_end arrives to re-fire trigger."""
        try:
            asyncio.get_running_loop().call_later(
                max(0.0, float(delay)),
                lambda: self._fire_task(self.trigger_agent_callbacks()),
            )
        except RuntimeError:
            self._fire_task(self.trigger_agent_callbacks())

    def _can_release_proactive(self) -> bool:
        """Manager-release gate, mirroring the defer conditions in
        ``trigger_agent_callbacks`` so cues stay UNDER manager ordering
        (coalescing/priority) until they can actually be delivered — rather
        than being released into the inner trigger, deferred, and parked in
        ``pending_agent_callbacks`` outside the manager (Codex P2).

        Returns False while: audio is playing (frontend gate), the SM is not
        IDLE (another proactive/greeting turn owns it), or the session is still
        GENERATING a response (_is_responding — covers BOTH the realtime
        response.created→voice_play_start window the playback gate can't see,
        AND an active offline/text user response where try_start_proactive
        would deny the claim)."""
        if self.is_goodbye_silent():
            return False
        # Time-bounded read (NOT the raw _voice_playback_active flag): if the
        # frontend dropped voice_play_end, _is_voice_playing() self-heals after
        # the 30s watchdog, so a stuck flag can't make can_release return False
        # forever and wedge the queue in an endless busy-recheck (Codex P1).
        if self._is_voice_playing():
            return False
        try:
            if self.state.phase is not ProactivePhase.IDLE:
                return False
        except Exception:
            # State unavailable → treat as IDLE and fall through to the rest of
            # the gate; never block delivery on a phase-read hiccup.
            logger.debug("[%s] _can_release_proactive: state.phase unavailable; treating as IDLE", self.lanlan_name)
        sess = self.session
        # Both realtime AND offline sessions expose _is_responding (set while
        # generating a response — user OR proactive); realtime's
        # is_active_response() is just a read of it. Releasing while True would
        # have trigger deny/defer the claim (voice: is_active_response gate;
        # text: try_start_proactive denies during _is_responding) and park the
        # cue in pending_agent_callbacks outside the manager (Codex P2).
        try:
            if sess is not None and getattr(sess, "_is_responding", False):
                return False
        except Exception:
            # Read hiccup → treat as not-responding rather than wedging the queue.
            logger.debug("[%s] _can_release_proactive: _is_responding check failed; treating as not-responding", self.lanlan_name)
        return True

    def _reset_proactive_gate(self) -> None:
        """Reset the playback gate on session lifecycle boundaries (session
        start / end / character switch) so a dropped voice_play_end can't
        carry stale playback state into the next session and wedge delivery.

        Proactive cues are generally important, so cues still queued in the
        manager are NOT dropped: they're moved into pending_agent_callbacks,
        which persists across teardown and is redelivered by the reconnect /
        next-turn path. Only the gate/single-flight state is cleared."""
        self._voice_playback_active = False
        self._voice_playback_started_ts = 0.0
        # end_session may run against a partially constructed manager (e.g.
        # teardown after an early start_session failure), so read the manager
        # defensively like the other teardown helpers on this path.
        manager = getattr(self, "proactive_manager", None)
        if manager is None:
            return
        try:
            leftover = manager.drain_pending()
            for cb in leftover:
                # Hand back to the persistent queue so the reconnect path
                # (websocket_router) / next trigger redelivers rather than
                # losing it.
                self.enqueue_agent_callback(cb)
            manager.reset_gate()
            # Deep topic hooks are text-mode openers and must never be spoken in
            # voice. start_session sets the audio starting flags BEFORE calling
            # us, so when entering / within a voice session this is the one
            # boundary where we sweep EVERY queued topic hook out of
            # pending_agent_callbacks (and the paired pending_extra_replies):
            # both the cbs just drained from the manager AND any released earlier
            # by _deliver_proactive_batch into the pending queue but left
            # deferred (SM busy / media-stream fail / no text session). Both the
            # voice branch of trigger_agent_callbacks (re-fired by start_session)
            # and the hot-swap prime path inject those two queues WITHOUT
            # re-consulting topic_hook_delivery_allowed, so neither delivery gate
            # covers this. Resolve ack False so TopicHookPool defers and retries
            # on a text session.
            if self._voice_delivery_blocked():
                self._drop_pending_topic_hooks_for_voice()
        except Exception:
            # getattr fallback: the except path must never raise itself
            # (a second AttributeError here would abort end_session teardown).
            logger.exception("[%s] proactive_manager reset/drain failed", getattr(self, "lanlan_name", "?"))

    def _drop_pending_topic_hooks_for_voice(self) -> None:
        """Drop every queued deep-topic hook when entering / within a voice
        session, across BOTH delivery queues.

        1. ``pending_agent_callbacks``: hooks here still carry their callback, so
           resolve each one's delivery ack False (``TopicHookPool`` defers +
           retries on a text session) and retract it, letting
           ``_purge_retracted_agent_callbacks`` sweep it and its paired
           ``pending_extra_replies`` entry by ``_callback_delivery_id``.
        2. ``pending_extra_replies`` orphans: ``drain_agent_callbacks_for_llm``
           clears ``pending_agent_callbacks`` on a text user turn but leaves the
           paired extras behind, so a topic hook can survive as an extras-only
           entry (callback already delivered + acked in text) and be rendered by
           the hot-swap ``prime_context`` path. Those have no callback left to
           ack/retract — just drop them. They are identified by
           ``source_kind == "topic"`` (stamped by ``build_topic_hook_callback``
           and copied onto the extra by ``enqueue_agent_callback``).

        See ``_reset_proactive_gate`` for why this is needed beyond the submit /
        release gates."""
        pending = getattr(self, "pending_agent_callbacks", None) or []
        hooks = [
            cb for cb in pending
            if isinstance(cb, dict) and cb.get("channel") == "topic_hook"
        ]
        for cb in hooks:
            resolve_callback_delivery_ack(cb, False)
            cb[DELIVERY_RETRACTED_KEY] = True
        if hooks:
            self._purge_retracted_agent_callbacks()
        # Sweep extras-only topic hooks (callback side already gone).
        extras = getattr(self, "pending_extra_replies", None)
        dropped_extras = 0
        if isinstance(extras, list):
            kept = [
                extra for extra in extras
                if not (isinstance(extra, dict) and extra.get("source_kind") == "topic")
            ]
            dropped_extras = len(extras) - len(kept)
            if dropped_extras:
                self.pending_extra_replies = kept
        if hooks or dropped_extras:
            logger.info(
                "[%s] dropped %d queued + %d extras-only topic hook(s) at voice start: deferred for a text session",
                self.lanlan_name, len(hooks), dropped_extras,
            )

    def _topic_hook_release_allowed(self, callback: dict) -> bool:
        if callback.get("channel") != "topic_hook":
            return True
        if not self.topic_hook_delivery_allowed():
            return False
        release_available = callback.get("_topic_release_available")
        if not callable(release_available):
            return True
        try:
            return bool(release_available())
        except Exception as exc:
            logger.warning(
                "[%s] topic hook release predicate failed closed: %s",
                self.lanlan_name,
                exc,
            )
            return False

    def _retract_unavailable_topic_hook_snapshots(self, callbacks: list) -> int:
        """Retract in-flight topic hooks whose release-time gate closed."""
        n = 0
        for cb in callbacks:
            if (
                isinstance(cb, dict)
                and cb.get("channel") == "topic_hook"
                and not cb.get(DELIVERY_RETRACTED_KEY)
                and not self._topic_hook_release_allowed(cb)
            ):
                resolve_callback_delivery_ack(cb, False)
                cb[DELIVERY_RETRACTED_KEY] = True
                n += 1
        return n

    def _retract_topic_hook_snapshots(self, callbacks: list) -> int:
        """Mark in-flight topic-hook snapshot entries retracted + ack False so the
        text delivery path drops them and ``TopicHookPool`` retries on a text
        session. This is the voice-specific subset of the broader topic release
        gate: a snapshot held by an in-flight ``trigger_agent_callbacks`` is in
        neither pending queue, so the voice-start sweep can't reach it. Returns
        the number retracted."""
        n = 0
        for cb in callbacks:
            if (
                isinstance(cb, dict)
                and cb.get("channel") == "topic_hook"
                and not cb.get(DELIVERY_RETRACTED_KEY)
            ):
                resolve_callback_delivery_ack(cb, False)
                cb[DELIVERY_RETRACTED_KEY] = True
                n += 1
        return n

    def enqueue_agent_callback(self, callback: dict) -> None:
        """Enqueue a structured agent task callback for LLM injection.

        Text mode: drained before the next stream_text call and injected via
        prompt_ephemeral(), OR proactively via trigger_agent_callbacks().
        Voice mode: also appended to pending_extra_replies for hot-swap
        injection via prime_context().

        Voice queue element shape is structured (not flat text) so the
        hot-swap renderer can:
          1. Pick TASK vs EVENT wrapper from ``origin``.
          2. Recover status / source phrasing when both ``summary`` and
             ``detail`` are empty — typical for failure callbacks where
             the meaning lives in the header (e.g. ``status="failed"`` +
             ``error_message="Connection refused"``).

        ``summary`` and ``detail`` are normalized **independently** (strip
        each, then prefer summary → detail), so a blank-whitespace
        ``summary`` doesn't shadow a real ``detail`` via the legacy
        ``summary or detail`` chain.

        The two queues stay independent (text-mode drain and voice-mode
        hot-swap fire at different lifecycle points).
        """
        try:
            from config import (
                AGENT_CALLBACK_QUEUE_MAX_ITEMS,
            )
            context_source = "topic.hook" if callback.get("channel") == "topic_hook" else "proactive.callback"
            # Per-item input budget: summary/detail flow into the LLM verbatim.
            # Reuse the same source-policy normalizer as append_context() so
            # proactive/topic callbacks do not grow a parallel budget path.
            summary_raw = str(callback.get("summary") or "").strip()
            detail_raw = str(callback.get("detail") or "").strip()
            summary = self._normalize_context_text_for_source(context_source, summary_raw)
            # summary/detail frequently carry the SAME body (proactive_bridge
            # sets both to the aggregated text) — reuse the encode, don't do it
            # twice.
            detail = summary if detail_raw == summary_raw else self._normalize_context_text_for_source(
                context_source,
                detail_raw,
            )
            # Write the capped text back so the text-mode drain (which reads the
            # callback dict directly) injects the truncated body too.
            callback["summary"] = summary
            callback["detail"] = detail
            error_message = str(callback.get("error_message") or "").strip()
            source_name = str(callback.get("source_name") or "").strip()
            status = callback.get("status") or "completed"
            origin = callback.get("origin")
            if origin not in ("task_result", "event"):
                # Fail-safe: missing/unknown origin defaults to event so the
                # hot-swap renderer does not fabricate "我完成了任务" for what
                # may actually be an external event push.
                origin = "event"
            # Skip enqueue (BOTH queues) only when there is *truly* nothing
            # to convey: no body text, no error context, no identifiable
            # source, and a benign completed status. Anything else
            # (failed/cancelled/blocked, an error message, or a named source)
            # carries meaning even with empty summary/detail and must survive
            # into the hot-swap output.
            #
            # The two queues must filter consistently — otherwise text mode
            # (which drains pending_agent_callbacks) would inject a garbage
            # header-only block for callbacks the voice mode already
            # discarded.
            if not summary and not detail and not error_message and not source_name and status == "completed":
                return
            # Stable delivery id so the voice inject success path can
            # precisely drop the matching extras entry from
            # ``pending_extra_replies``. Length-based alignment is unsafe:
            # ``drain_agent_callbacks_for_llm`` clears
            # ``pending_agent_callbacks`` while leaving
            # ``pending_extra_replies`` intact, so the queues legitimately
            # drift apart across user turns.
            delivery_id = callback.setdefault("_callback_delivery_id", uuid4().hex)
            # Coalescing is OPT-IN and channel-agnostic: when a callback carries
            # a non-empty ``coalesce_key``, the newest cue collapses any already
            # queued cue that set the SAME key — mirroring
            # ``ProactiveDeliveryManager.submit``'s dedup for the proactive path.
            # Passive/read cues never reach that manager (they take this direct
            # enqueue-only path so they don't interrupt), so without this a rapid
            # ``ai_behavior="read"`` stream reusing one key would pile up stale
            # snapshots, bounded only by the drop-oldest flood guard below. An
            # empty key never coalesces, so no existing producer regresses.
            new_key = str(callback.get("coalesce_key") or "").strip()
            if new_key:
                # Submission-order stamp: cues carry a monotonic seq assigned at
                # submit_proactive_callback (manager-held respond cues) or here
                # (direct read cues), so newest-wins holds ACROSS the manager and
                # enqueue paths. A manager release of an OLDER respond cue must not
                # overwrite a newer direct-queued read cue sharing the key.
                new_seq = callback.get("_coalesce_submit_seq")
                if not isinstance(new_seq, int):
                    new_seq = self._next_coalesce_seq()
                    callback["_coalesce_submit_seq"] = new_seq
                # Feed the pull-model staleness map: delivery-point guards
                # (_retract_stale_coalesced / _coalesce_entry_is_stale) compare
                # in-flight snapshot entries against this latest seq.
                self._note_coalesce_submission(new_key, new_seq)
                incoming_superseded = False
                dropped = 0
                superseded_ids: set = set()
                surviving: list[dict] = []
                for _cb in self.pending_agent_callbacks:
                    # isinstance guard: the queue should hold dicts, but never let
                    # a malformed entry raise here (the broad except below would
                    # silently drop the whole enqueue and lose this callback).
                    if not (isinstance(_cb, dict)
                            and str(_cb.get("coalesce_key") or "").strip() == new_key):
                        surviving.append(_cb)
                        continue
                    _old_seq = _cb.get("_coalesce_submit_seq")
                    _old_seq = _old_seq if isinstance(_old_seq, int) else -1
                    if _old_seq > new_seq:
                        # A newer same-key cue is already queued → the incoming one
                        # (an older manager-released respond) loses; keep the queued.
                        incoming_superseded = True
                        surviving.append(_cb)
                    else:
                        # Incoming is newer → retract the older queued cue. Mark
                        # retracted, don't just drop: trigger_agent_callbacks
                        # snapshots pending_agent_callbacks into a local
                        # voice_snapshot BEFORE its await and re-filters it only by
                        # DELIVERY_RETRACTED_KEY, so an in-flight cue must carry the
                        # flag or it is still spoken even though its ack was resolved
                        # False. Mirrors retract() / _drop_pending_topic_hooks_for_voice.
                        resolve_callback_delivery_ack(_cb, False)
                        _cb[DELIVERY_RETRACTED_KEY] = True
                        dropped += 1
                        _did = _cb.get("_callback_delivery_id")
                        if _did:
                            superseded_ids.add(_did)
                if dropped:
                    logger.debug(
                        "[%s] coalesced %d queued callback(s) on key=%r seq=%d (enqueue path)",
                        self.lanlan_name, dropped, new_key, new_seq,
                    )
                    self.pending_agent_callbacks = surviving
                # Evict OLDER same-key voice mirrors by key, covering the extras-only
                # orphan: drain_agent_callbacks_for_llm clears pending_agent_callbacks
                # on a text user turn but keeps the paired pending_extra_replies for
                # the hot-swap path, so a superseded cue can survive as an
                # extras-only entry whose callback half is gone — id-matching misses
                # it. The isinstance guard also skips legacy plain-string extras that
                # _render_pending_extra_replies_by_origin tolerates (calling .get()
                # on a str would raise into the broad except and lose the enqueue).
                if self.pending_extra_replies:
                    kept_extras: list = []
                    for _extra in self.pending_extra_replies:
                        if not isinstance(_extra, dict):
                            kept_extras.append(_extra)
                            continue
                        # A mirror paired with a just-retracted callback is
                        # evicted by delivery_id even when it predates the
                        # coalesce_key stamp (legacy mirror shape) — key-only
                        # matching would orphan it for the hot-swap path.
                        if _extra.get("_callback_delivery_id") in superseded_ids:
                            continue
                        if str(_extra.get("coalesce_key") or "").strip() != new_key:
                            kept_extras.append(_extra)
                            continue
                        _ex_seq = _extra.get("_coalesce_submit_seq")
                        _ex_seq = _ex_seq if isinstance(_ex_seq, int) else -1
                        if _ex_seq > new_seq:
                            # A newer snapshot already mirrored → incoming loses.
                            incoming_superseded = True
                            kept_extras.append(_extra)
                        # else: drop the older mirror
                    if len(kept_extras) != len(self.pending_extra_replies):
                        self.pending_extra_replies = kept_extras
                if incoming_superseded:
                    # A newer same-key cue already won on both queues; don't enqueue
                    # this stale one (ack it False so any waiter unblocks).
                    resolve_callback_delivery_ack(callback, False)
                    callback[DELIVERY_RETRACTED_KEY] = True
                    return
            self.pending_agent_callbacks.append(callback)
            self.pending_extra_replies.append({
                "_callback_delivery_id": delivery_id,
                # Stamp the coalesce_key + submission seq so a later same-key cue
                # can evict this voice mirror even after its callback half is
                # drained, and only when the incoming cue is actually newer.
                "coalesce_key": new_key,
                "_coalesce_submit_seq": callback.get("_coalesce_submit_seq"),
                "origin": origin,
                "summary": summary,
                "detail": detail,
                "status": status,
                "context_source": context_source,
                "source_kind": callback.get("source_kind") or "unknown",
                "source_name": source_name,
                "error_message": error_message,
            })
            # Flood guard: a runaway plugin event stream must not grow either
            # queue without bound. Keep the most recent N (newest = most
            # relevant); drop-oldest.
            if len(self.pending_agent_callbacks) > AGENT_CALLBACK_QUEUE_MAX_ITEMS:
                overflow = len(self.pending_agent_callbacks) - AGENT_CALLBACK_QUEUE_MAX_ITEMS
                dropped = self.pending_agent_callbacks[:overflow]
                dropped_ids = {
                    _cb.get("_callback_delivery_id")
                    for _cb in dropped
                    if isinstance(_cb, dict) and _cb.get("_callback_delivery_id")
                }
                self.pending_agent_callbacks = self.pending_agent_callbacks[overflow:]
                # Resolve any delivery-ack future on a dropped callback NOW, so a
                # waiter (e.g. topic-hook delivery) unblocks immediately instead
                # of stalling until its timeout.
                for _cb in dropped:
                    resolve_callback_delivery_ack(_cb, False)
                # Drop the matching voice-queue mirrors by delivery_id (the two
                # queues drift, so positional trimming is unreliable) — otherwise
                # a callback acked False here could still be injected via hot-swap.
                if dropped_ids:
                    self.pending_extra_replies = [
                        _extra for _extra in self.pending_extra_replies
                        if _extra.get("_callback_delivery_id") not in dropped_ids
                    ]
            if len(self.pending_extra_replies) > AGENT_CALLBACK_QUEUE_MAX_ITEMS:
                self.pending_extra_replies = self.pending_extra_replies[-AGENT_CALLBACK_QUEUE_MAX_ITEMS:]
        except Exception:
            # Pruning is best-effort housekeeping — never let it break callback bookkeeping.
            pass

    def drain_agent_callbacks_for_llm(self) -> str:
        """Drain pending_agent_callbacks and format as a system context string.

        Clears pending_agent_callbacks (NOT pending_extra_replies, which is
        consumed separately by the voice-mode hot-swap path).
        Returns an empty string if there are no callbacks.

        Renders with the same grouped/source-aware logic as
        :meth:`trigger_agent_callbacks` but in passive mode — so the resulting
        string already includes its own outer header (PASSIVE for delivery
        ``"passive"`` callbacks, PROACTIVE for any "proactive" ones that
        ended up here because the SM denied the claim earlier). The caller
        therefore should NOT prepend an additional notification template.
        """
        self._purge_retracted_agent_callbacks()
        if not self.pending_agent_callbacks:
            return ""
        candidate_callbacks = list(self.pending_agent_callbacks)
        if self._retract_unavailable_topic_hook_snapshots(candidate_callbacks):
            logger.info(
                "[%s] drain_agent_callbacks_for_llm: topic hook dropped before passive drain — delivery gate closed",
                self.lanlan_name,
            )
        # Pull-model staleness: uniform with the voice/text/hot-swap delivery
        # points — a cue restored from a failed proactive attempt (or any path
        # that re-appends without the push-side scan) must not deliver once a
        # newer same-coalesce_key cue exists.
        self._retract_stale_coalesced(candidate_callbacks)
        self._purge_retracted_agent_callback_extras(candidate_callbacks)
        self._purge_retracted_agent_callbacks()
        active_callbacks = [
            cb for cb in candidate_callbacks
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        if not active_callbacks:
            return ""
        from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
        # Budget-aware selection: render (and ack) only the callbacks that fit
        # the total budget this turn; defer the rest to the next drain instead
        # of acking them as delivered while their text falls off the cap.
        callbacks_snapshot, deferred = _select_callbacks_within_token_budget(
            active_callbacks, AGENT_CALLBACK_TOTAL_MAX_TOKENS
        )
        delivered_to_prompt = False
        try:
            _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
            rendered = _build_callback_instruction(
                callbacks_snapshot,
                lang=_lang,
                lanlan_name=getattr(self, "lanlan_name", "") or "",
                master_name=getattr(self, "master_name", "") or "",
                passive=False,
            )
            delivered_to_prompt = True
            return rendered
        finally:
            if delivered_to_prompt:
                for cb in callbacks_snapshot:
                    resolve_callback_delivery_ack(cb, True)
            # Keep deferred (over-budget) callbacks for the next turn; only the
            # rendered+acked ones leave the queue.
            self.pending_agent_callbacks = deferred
