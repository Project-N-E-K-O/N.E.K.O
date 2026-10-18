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
"""Live input streaming for ``LLMSessionManager``: screen/audio stream
intake, hot-swap cache flushes, and stream-time turn-end
bookkeeping.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import json
import time
from .session_records import INPUT_DISPATCH_DEFERRED
from websockets import exceptions as web_exceptions
from utils.screenshot_utils import overlay_avatar_annotation
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionEvent
from services.chat_sync.source import is_chat_input_source, mark_turn_origin
from utils.external_route_registry import RouteClaim
from utils.language_utils import get_global_language_full
from uuid import uuid4
from ._shared import (
    _TEXT_SESSION_INPUT_TYPES,
    _IMAGE_INPUT_TYPES,
    _LIVE_VISION_STREAM_INPUT_TYPES,
    FRONTEND_START_SESSION_TIMEOUT_SECONDS,
    logger,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class StreamingMixin:
    """Live input streaming methods (see module docstring)."""

    @staticmethod
    def _user_input_ingress_time(message: dict) -> float:
        """Return the server-captured ingress time, or sample a safe fallback."""
        captured_at = message.get("_user_input_ingress_time")
        if isinstance(captured_at, (int, float)):
            return float(captured_at)
        return time.time()

    def note_stream_input_ingress(self, message: dict) -> bool:
        """Record nonblank text/image input before fallible staging."""
        input_type = message.get("input_type")
        if input_type == "text":
            memory_text = self._clean_frontend_memory_text(
                message.get("memory_text")
            )
            content = memory_text or message.get("data")
        elif input_type in {"avatar_drop_image", "user_image"}:
            content = message.get("data")
        else:
            return False

        if isinstance(content, str):
            has_content = bool(content.strip())
        elif isinstance(content, (bytes, bytearray)):
            has_content = bool(content)
        else:
            has_content = False
        if not has_content:
            return False

        self.note_user_engagement(
            at=self._user_input_ingress_time(message)
        )
        return True

    def _emit_cooldown_turn_end_if_needed(self):
        """Deduplicated turn_end emission during cooldown, at most once per second. Returns True when currently cooling down."""
        if not self._memory_error_retry_after or time.time() >= self._memory_error_retry_after:
            return False
        now = time.time()
        if now - self._last_cooldown_turn_end_time >= 1.0:
            self._last_cooldown_turn_end_time = now
            time_left = int(self._memory_error_retry_after - now)
            self._fire_task(self.send_status(json.dumps({
                "code": "MEMORY_SERVER_COOLDOWN",
                "details": {"wait_time": time_left}
            })))
            self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                self._fire_task(self.websocket.send_json({'type': 'system', 'data': 'turn end'}))
        return True
    
    async def _flush_pending_input_data(self):
        """Send the cached input data to the session"""
        # A realtime -> offline attachment handoff must stage the attachment
        # before inputs that arrived while the replacement session was
        # starting. ``start_session`` normally flushes as soon as the session
        # becomes ready; defer that nested flush until the owning attachment
        # finishes its one-shot ``stream_image`` call.
        if getattr(self, "_deferred_pending_input_flush_count", 0) > 0:
            return
        async with self.input_cache_lock:
            if getattr(self, "_pending_input_flush_active", False):
                return
            if not self.pending_input_data:
                return
            self._pending_input_flush_active = True
            idle_event = asyncio.Event()
            self._pending_input_flush_idle_event = idle_event

        def release_gate():
            if getattr(self, "_pending_input_flush_idle_event", None) is idle_event:
                self._pending_input_flush_active = False
                self._pending_input_flush_idle_event = None
            # Rollback precedes this signal, including on cancellation.
            idle_event.set()

        try:
            while True:
                async with self.input_cache_lock:
                    if not self.pending_input_data:
                        release_gate()
                        return
                    # Drain atomically, then process outside this lock. One-shot
                    # image attachments may need _ensure_offline_session_for_text_input(),
                    # whose handoff reacquires input_cache_lock; awaiting that
                    # path while still holding the lock would deadlock.
                    pending_messages = list(self.pending_input_data)
                    self.pending_input_data.clear()
                    self._pending_input_flush_batch = tuple(pending_messages)

                # Once detached from ``pending_input_data``, this local batch
                # owns every message until each item reaches a terminal handling
                # point. Cancellation/session teardown must put the untouched
                # suffix back ahead of live inputs queued while the flush was
                # active; otherwise startup cancellation silently loses input.
                next_unprocessed = 0
                try:
                    if not self.session or not self.is_active:
                        return

                    # Buffering (_stream_data_now) does not know whether the final session will be voice or
                    # text. If a voice session starts successfully, flushing buffered plain text directly
                    # into _process_stream_data_internal would switch the newly ready voice
                    # session to text; continue dropping plain text. avatar_drop_image/user_image,
                    # however, are explicit one-shot attachments and must retain their offline-vision contract. screen /
                    # camera continue using valid realtime paths. Audio never appears during buffering.
                    dropped_text_for_voice = 0
                    for index, message in enumerate(pending_messages):
                        msg_input_type = message.get("input_type")
                        # Keep the current item in the rollback window until
                        # the provider dispatch boundary is reached. Several
                        # text/image paths await preparation first (for
                        # example Focus scoring), so advancing this cursor at
                        # loop entry would lose an item when that await is
                        # cancelled.
                        def mark_dispatch_attempted() -> None:
                            nonlocal next_unprocessed
                            next_unprocessed = index + 1

                        try:
                            if msg_input_type == "audio":
                                # Queue admission is the audio dispatch
                                # boundary; the queue worker owns the later
                                # provider handoff.
                                mark_dispatch_attempted()
                                await self._enqueue_audio_stream_data(message)
                            else:
                                if (
                                    isinstance(self.session, OmniRealtimeClient)
                                    and msg_input_type == "text"
                                ):
                                    self.note_stream_input_ingress(message)
                                    dropped_text_for_voice += 1
                                    next_unprocessed = index + 1
                                    continue
                                result = await self._process_stream_input(
                                    message,
                                    on_dispatch_attempted=mark_dispatch_attempted,
                                )
                                if result is INPUT_DISPATCH_DEFERRED:
                                    return  # finally restores this item and its suffix.
                                # A normal early return (validation failure or
                                # an intentional drop) is terminal handling,
                                # even though it never reaches a provider.
                                next_unprocessed = index + 1
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            # The message was attempted and is now terminally
                            # failed. Retrying it at the queue head would block
                            # every later cached/live input indefinitely; drop
                            # only this item and preserve batch ordering.
                            next_unprocessed = index + 1
                            logger.error(
                                "💥 发送缓存的输入数据失败，丢弃当前消息: %s",
                                e,
                            )
                            continue
                    if dropped_text_for_voice:
                        logger.info(
                            "[%s] _flush_pending_input_data: dropped %d cached text "
                            "message(s) because final session is voice mode",
                            self.lanlan_name,
                            dropped_text_for_voice,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 发送缓存的输入数据失败: {e}")
                    return
                finally:
                    unprocessed = pending_messages[next_unprocessed:]
                    if unprocessed:
                        async with self.input_cache_lock:
                            self.pending_input_data[0:0] = unprocessed
                    self._pending_input_flush_batch = ()
        finally:
            async with self.input_cache_lock:
                release_gate()
    
    def _should_drop_live_vision_stream(self, input_type: str | None) -> bool:
        """Deliberately checked at each stream boundary; callers may enter below stream_data."""
        return input_type in _LIVE_VISION_STREAM_INPUT_TYPES and self.is_goodbye_silent()

    async def stream_data(self, message: dict):  # Send media data to Core API.
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        if input_type == "audio":
            await self._enqueue_audio_stream_data(message)
            return
        await self._stream_data_now(message)

    async def _stream_data_now(self, message: dict):
        input_type = message.get("input_type")
        if self._should_drop_live_vision_stream(input_type):
            return
        if input_type in _TEXT_SESSION_INPUT_TYPES:
            # Preserve when the user action reached the server. Session startup,
            # router task scheduling, mode rebuilds, and pending-input flushes
            # may delay actual handling. Preserve a router-provided timestamp;
            # direct/internal callers get a safe fallback sampled here.
            # Copy so callers cannot observe this internal transport metadata.
            message = {
                **message,
                "_user_input_ingress_time": self._user_input_ingress_time(message),
            }
            # Genuine one-shot input must reset unanswered evidence even if a
            # circuit breaker, failed startup, or final voice-mode flush drops
            # it before the normal text/image processing branches are reached.
            self.note_stream_input_ingress(message)
        elif input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
            # Preserve router ingress ordering across the independent screen /
            # camera tasks and their threaded validation. Direct/internal
            # callers receive a monotonic fallback sampled before any await.
            captured_at = message.get("_visual_input_ingress_time")
            message = {
                **message,
                "_visual_input_ingress_time": (
                    float(captured_at)
                    if isinstance(captured_at, (int, float))
                    else time.monotonic()
                ),
            }
        # Slash mini-game shortcuts (/一起看, /足球, etc.) need no LLM session; handle before session-readiness
        # checks, automatic creation, and realtime→offline handoff. Otherwise creation failure
        # disables commands, and typing /一起看 in a voice session would first tear it down.
        if input_type == "text" and await self._maybe_handle_mini_game_magic_command(message):
            return
        # Check whether session is ready.
        async with self.input_cache_lock:
            if (
                getattr(self, "_pending_input_flush_active", False)
                or getattr(self, "_pending_input_flush_scheduled", None) is not None
            ):
                # Replay owns ordering until its current batch finishes. Queue
                # live input behind it instead of racing the same offline
                # session's stream_text/stream_image call.
                self.pending_input_data.append(message)
                return
            if not self.session_ready:
                # Check whether session is starting; buffer only during startup.
                if self._starting_session_count > 0:
                    if input_type == "audio":
                        return
                    # Session starting: buffer input data.
                    self.pending_input_data.append(message)
                    if len(self.pending_input_data) == 1:
                        logger.info("Session正在启动中，开始缓存输入数据...")
                    else:
                        logger.debug(f"继续缓存输入数据 (总计: {len(self.pending_input_data)} 条)...")
                    return

        # Check whether a new session is needed outside the lock (creating under lock risks deadlock).
        if not self.session_ready and self._starting_session_count == 0:
            if not self.session or not self.is_active:
                if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                    return
                # Determine mode from input type.
                mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
                # External routers may claim automatic voice-session creation (the voice entry bypassing websocket_router's
                # start_session branch); create normally if unclaimed.
                # Ask routers before the ordinary-session-only cooldown / breaker checks below; a router claiming
                # this startup does not create an ordinary session and must not be blocked by those checks.
                if mode == 'audio':
                    # A route replaced while deciding is re-asked inside the
                    # registry; an owner that keeps changing drops this frame.
                    claim, _route = await _core_facade.route_external_start_session(
                        self.lanlan_name, {'input_type': 'audio'},
                    )
                    if claim is not RouteClaim.UNCLAIMED:
                        return
                    # The claim check may have suspended: another frame can
                    # have started a session meanwhile. Audio arriving during a
                    # start is dropped, as above; a session that came up is used.
                    if self._starting_session_count > 0:
                        return
                    if self.session_ready or (self.session and self.is_active):
                        await self._process_stream_data_internal(message)
                        return
                # Memory Server-specific cooldown check.
                if self._emit_cooldown_turn_end_if_needed():
                    return
                # Breaker early return: start_session also guards internally, but this extra layer avoids
                # an 'automatically create session' info log for every audio packet, preventing log flooding.
                if self._session_start_circuit_open:
                    return
                logger.info(f"Session未就绪且不存在，根据输入类型 {input_type} 自动创建 session")
                try:
                    await self.start_session(self.websocket, new=False, input_mode=mode)
                except asyncio.CancelledError as exc:
                    # A concurrent end/reset revoked this auto-start. The
                    # websocket receive loop remains owned by the caller.
                    if not self._consume_start_retirement_cancellation(exc):
                        raise
                    logger.info("Session auto-start cancelled; dropping this input")
                    return

                # Check whether startup succeeded.
                if not self.session or not self.is_active:
                    logger.warning("⚠️ Session启动失败，放弃本次数据流")
                    return
        
        # Session ready: process directly.
        await self._process_stream_input(message)

    async def _ensure_offline_session_for_text_input(
        self,
        input_type: str,
    ) -> bool:
        """Move text/attachment input onto its offline-session contract."""
        if isinstance(self.session, OmniOfflineClient):
            return True
        # Share the gate with _handoff_to_offline_vlm_and_submit. Both replace the current
        # session in place with an offline client, each with an await interval spanning end_session +
        # start_session. Without a shared gate, concurrent handoffs tear down each other's
        # newly created offline session: the later arrival destroys the first one's result, then the first
        # submits to a retired client. Lock order matches lifecycle (handoff gate outside,
        # swap gate inside), without inversion.
        lock = getattr(self, '_multimodal_handoff_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_handoff_lock = lock
        try:
            await asyncio.wait_for(
                lock.acquire(),
                # Gate holder is rebuilding a session; use the session-startup budget while waiting.
                timeout=FRONTEND_START_SESSION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # Timeout must not bypass the gate and rebuild independently, exactly the concurrency this gate prevents. Succeed only if the other
            # task already replaced the session with offline (doing the work for us).
            if isinstance(self.session, OmniOfflineClient):
                return True
            logger.error(
                "💥 %s 等待 offline handoff 闸超时，放弃本次数据流",
                input_type,
            )
            return False
        try:
            return await self._rebuild_offline_session_for_text_input(input_type)
        finally:
            lock.release()

    async def _rebuild_offline_session_for_text_input(
        self,
        input_type: str,
    ) -> bool:
        """The handoff body itself; runs holding ``_multimodal_handoff_lock``."""
        # Reread after acquiring the gate: an earlier handoff may already have switched to offline;
        # another teardown would needlessly interrupt a newly built session.
        if isinstance(self.session, OmniOfflineClient):
            return True
        if self.session_start_failure_count >= self.session_start_max_failures:
            logger.error(
                "💥 %s 需要文本模式，但失败次数过多，已停止自动重建",
                input_type,
            )
            return False

        logger.info(
            "%s 需要 OmniOfflineClient，但当前是 %s. 自动重建 session。",
            input_type,
            type(self.session).__name__,
        )
        # Hold the startup guard across end_session's await window. Without
        # this, concurrent microphone work can recreate an audio session before
        # the attachment/text handoff starts its offline replacement.
        async with self.input_cache_lock:
            self.session_ready = False
        self._starting_session_count += 1
        self._starting_input_mode = "text"
        try:
            if self.session:
                # preserve_pending_input: this teardown is a step in replacing the session in place with offline,
                # not ending it. session_ready is already False above, so concurrent text / attachment
                # tasks buffer messages throughout the teardown await interval
                # into pending_input_data. end_session clears this queue by default, silently
                # losing those inputs. After reconstruction,
                # _flush_pending_input_data releases them normally.
                await self.end_session(
                    # by_server=True: this is an internal in-place Realtime → Offline replacement, not
                    # the end of a user session. Default cleanup pushes CHARACTER_LEFT to the frontend;
                    # sending text or dragging an image would make users see character departure before session restart,
                    # potentially triggering frontend chat cleanup. The existing internal idle_session_reset path
                    # suppresses this notification with by_server=True, using the same criterion.
                    by_server=True,
                    reset_starting_count=False,
                    preserve_pending_input=True,
                )
        finally:
            self._starting_session_count = max(0, self._starting_session_count - 1)
            if self._starting_session_count == 0:
                self._starting_input_mode = None
        # Do not await between releasing the guard and entering start_session;
        # its synchronous prologue reacquires the startup ownership.
        try:
            await self.start_session(
                self.websocket,
                new=False,
                input_mode="text",
            )
        except asyncio.CancelledError as exc:
            if not self._consume_start_retirement_cancellation(exc):
                raise
            return False
        if (
            not self.session
            or not self.is_active
            or not isinstance(self.session, OmniOfflineClient)
        ):
            logger.error("💥 文本模式Session重建失败，放弃本次数据流")
            return False
        return True

    async def _process_stream_input(self, message: dict, *, on_dispatch_attempted=None):
        """Process one ready input; a typed text input holds an owed wrap-up.

        A typed input interrupts the offline reply and then starts its own,
        with several awaits in between. The interrupted reply's owed wrap-up
        is not paid in that gap (a final swap would start right before the
        new reply, even if the interrupted task ends first): the new reply's
        completion pays it, or, when none ran (a command, an abandoned input,
        a failure, a reply interrupted in turn), the settle once this input
        has been handled. An input deferred back to the pending queue
        (``INPUT_DISPATCH_DEFERRED``) has not been handled: no settle then,
        its replay settles. Returns what ``_process_stream_data_internal``
        returned.
        """
        if message.get("input_type") != "text" or not isinstance(message.get("data"), str):
            return await self._process_stream_data_internal(
                message, on_dispatch_attempted=on_dispatch_attempted,
            )
        return await self._with_owed_wrap_up_held(
            self._process_stream_data_internal(
                message, on_dispatch_attempted=on_dispatch_attempted,
            ),
            skip_settle_if=INPUT_DISPATCH_DEFERRED,
        )

    async def _process_stream_data_internal(
        self,
        message: dict,
        *,
        on_dispatch_attempted=None,
    ):
        """Internal method: the actual stream_data processing logic"""
        data = message.get("data")
        input_type = message.get("input_type")
        if input_type == "audio" and any(
            not retirement.handoff_safe.is_set()
            for retirement in getattr(self, "_session_retirements", ())
        ):
            # A detached old session is inactive before its ASR and output
            # producers finish. PCM already in flight must not treat that gap
            # as an auto-start request. After handoff the ordinary startup and
            # microphone lease guards decide whether new audio is admissible.
            return
        if self._should_drop_live_vision_stream(input_type):
            return
        # Check for fatal session errors (e.g. 1011, Response timeout).
        if (
            input_type != "audio"
            and self.session
            and isinstance(self.session, OmniRealtimeClient)
        ):
            if hasattr(self.session, '_fatal_error_occurred') and self.session._fatal_error_occurred:
                logger.warning("⚠️ Session已发生致命错误，忽略新的输入数据")
                return
        
        # This should not happen during startup, because stream_data already checked.
        if self._starting_session_count > 0:
            operation = self._current_start_request()
            owns_ready_flush = (
                operation is not None
                and operation is getattr(self, "_start_operation", None)
                and operation.valid
                and self.session_ready
                and self.is_active
                and self.session is not None
                and getattr(self, "_pending_input_flush_active", False)
            )
            if not owns_ready_flush:
                logger.debug("Session正在启动中，跳过...")
                if callable(on_dispatch_attempted):
                    return INPUT_DISPATCH_DEFERRED
                return

        # If session is missing/inactive, check whether it can rebuild automatically.
        if not self.session or not self.is_active:
            if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                return
            # Memory Server-specific cooldown check.
            if self._emit_cooldown_turn_end_if_needed():
                return
            # Failure-limit protection: start_session's internal breaker returns early; this extra guard
            # avoids a 'Session missing' info log for every streaming packet, reducing logging cost.
            if self._session_start_circuit_open:
                return

            logger.info(f"Session 不存在或未激活，根据输入类型 {input_type} 自动创建 session")
            # Check WebSocket state.
            ws_exists = self.websocket is not None
            if ws_exists:
                has_state = hasattr(self.websocket, 'client_state')
                if has_state:
                    logger.info(f"  └─ WebSocket状态: exists=True, state={self.websocket.client_state}")
                    # Further check connection state.
                    if self.websocket.client_state != self.websocket.client_state.CONNECTED:
                        logger.error(f"  └─ WebSocket未连接，状态: {self.websocket.client_state}")
                        self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                        return
                else:
                    logger.warning("  └─ WebSocket状态: exists=True, 但没有client_state属性!")
            else:
                logger.error("  └─ WebSocket状态: exists=False! 连接可能已断开，请刷新页面")
                # Send error notification through sync_message_queue.
                self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                return
            
            # Determine mode from input type.
            mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
            try:
                await self.start_session(self.websocket, new=False, input_mode=mode)
            except asyncio.CancelledError as exc:
                if not self._consume_start_retirement_cancellation(exc):
                    raise
                return
            
            # Check whether startup succeeded.
            if not self.session or not self.is_active:
                logger.warning("⚠️ Session启动失败，放弃本次数据流")
                return
        
        defer_pending_flush = False
        try:
            validated_one_shot_image_b64 = None
            if input_type == "text" and not isinstance(data, str):
                logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            if input_type in {"avatar_drop_image", "user_image"}:
                if self._should_drop_magic_command_image(message.get("request_id")):
                    return
                # Validate one-shot attachments before the destructive realtime
                # -> offline handoff. A malformed/oversized attachment must not
                # tear down an otherwise healthy voice session only to be
                # rejected a few lines later.
                try:
                    validated_one_shot_image_b64 = (
                        await _core_facade.process_screen_data(data)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing image data: {e}")
                    return
                if not validated_one_shot_image_b64:
                    logger.error("💥 Stream: 图像数据验证失败")
                    return

                if not isinstance(self.session, OmniOfflineClient):
                    self._deferred_pending_input_flush_count = (
                        getattr(self, "_deferred_pending_input_flush_count", 0)
                        + 1
                    )
                    defer_pending_flush = True

            if input_type == "text" and not isinstance(self.session, OmniOfflineClient):
                # Plain text also initiates this handoff and needs the same owner-before-flush
                # lock. end_session now preserves inputs buffered during teardown
                # (preserve_pending_input), while start_session flushes them before finishing;
                # this message **initiating** handoff is submitted only later in this function. Without blocking
                # flush, a later message enters history and generates first, then this earlier message interrupts it,
                # reversing the user's two messages.
                self._deferred_pending_input_flush_count = (
                    getattr(self, "_deferred_pending_input_flush_count", 0) + 1
                )
                defer_pending_flush = True

            if input_type in _TEXT_SESSION_INPUT_TYPES:
                if not await self._ensure_offline_session_for_text_input(input_type):
                    return

            if input_type == 'text':
                # Text mode: send text directly.
                if isinstance(data, str):
                    memory_text = self._clean_frontend_memory_text(message.get("memory_text"))
                    message_source = str(message.get("source") or "").strip()
                    record_data = memory_text or data
                    # Additional turn context (knowledge/corpus/memory): start parallel retrieval as soon as original user text arrives,
                    # alongside interruption, TTS cleanup, and focused-cognition checks below; retrieve results before model submission,
                    # sharing one time budget. Query with visible original user text (record_data),
                    # not data that may contain hidden scaffolding.
                    _turn_context_job = self._start_turn_context(
                        record_data, ingress_time=self._user_input_ingress_time(message),
                    )
                    # Update user activity timestamp (symmetric with handle_input_transcript / _record_external_user_input).
                    # The idle-reset loop uses this field for silence duration; omitting it for text
                    # would make text-only sessions always appear silent ≥ 30 min and reset incorrectly.
                    _user_input_time = self._user_input_ingress_time(message)
                    _last_activity_time = getattr(
                        self,
                        "last_user_activity_time",
                        None,
                    )
                    self.last_user_activity_time = (
                        max(float(_last_activity_time), _user_input_time)
                        if isinstance(_last_activity_time, (int, float))
                        else _user_input_time
                    )
                    # Real-message timestamp: refresh only for nonempty stripped input, symmetric with voice-path
                    # `if transcript_text:`; whitespace is not a real response and must not wrongly
                    # advance mini-game invitation implicit dismissal (CodeRabbit). Note
                    # last_user_activity_time still refreshes unconditionally for idle reset, measuring
                    # whether a request arrived, distinct from whether it contains a real message.
                    if record_data.strip():
                        _last_message_time = getattr(
                            self,
                            "last_user_message_time",
                            None,
                        )
                        self.last_user_message_time = (
                            max(float(_last_message_time), _user_input_time)
                            if isinstance(_last_message_time, (int, float))
                            else _user_input_time
                        )
                        self.note_user_engagement(at=_user_input_time)

                    # Update word limit (user may have changed settings during the conversation).
                    if hasattr(self.session, 'update_max_response_length'):
                        self.session.update_max_response_length(self._get_text_guard_max_length())

                    # First interrupt currently playing speech (old speech_id), avoiding interruption of the new response.
                    async with self.lock:
                        interrupted_speech_id = self.current_speech_id

                    # Then stop the **producer**: an offline session may be running an independent-ASR
                    # external turn (_external_voice_submit_task), or an ordinary text response not yet
                    # fully received. speech_id rotates below; without cancellation that stream
                    # continues emitting deltas attached to the new message's sid. Both streams also share
                    # _is_responding; whichever finishes first sets False and truncates the other.
                    # Same criterion as handle_interruption() before preparing an independent-ASR turn.
                    try:
                        await self._interrupt_offline_reply(self.session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as _interrupt_error:
                        # Interruption is best effort: a broken session must not swallow the user's newly typed message
                        # too. On failure the old stream may keep emitting deltas (the issue this code addresses),
                        # but that is less harmful than losing the message.
                        logger.warning(
                            "[%s] text input could not interrupt the session: %s",
                            self.lanlan_name,
                            _interrupt_error,
                        )
                    # Interrupted responses no longer reach turn end (cancelled generations skip
                    # on_response_done). _interrupt_offline_reply finalizes when something was actually
                    # interrupted (record AI turn + synchronize turn end); pending renewal checks
                    # and callback delivery become debt, unpaid during this input's processing (_process_stream_input),
                    # settled by the new response's _finalize_turn_after_emit; if no new response occurs, settle after input processing
                    # when the session becomes idle.

                    self.audio_resampler.clear()
                    await self._clear_tts_pipeline()
                    await self.send_user_activity(interrupted_speech_id)

                    # Then generate a new speech_id for this response (TTS and lipsync).
                    async with self.lock:
                        self.current_speech_id = str(uuid4())
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                        new_user_sid = self.current_speech_id
                        # Like handle_new_message: synchronously flip _preempted in the same critical section
                        # as writing sid, preventing prepare_proactive_delivery from intervening between lock
                        # release and fire() to overwrite the new user sid.
                        self.state.mark_user_input_preempt()
                    # State machine: text-mode stream_text must emit USER_INPUT too.
                    # handle_new_message runs only in voice mode; this is its text-mode counterpart.
                    await self.state.fire(SessionEvent.USER_INPUT, sid=new_user_sid)
                    # Activity tracker: real text-mode user input. Deliberately avoid placing it in handle_new_message,
                    # which proactive-abort cleanup also calls (see
                    # main_routers/system_router.py), not representing user activity.
                    # Buffer text for emotion-tier.
                    self._note_user_turn(text=record_data)
                    # Telemetry: D1 funnel, first user message in this process (lazy import avoids cycles).
                    try:
                        from utils.token_tracker import TokenTracker as _TT
                        _tt = _TT.get_instance()
                        _tt.note_first_user_message("text")
                        # Every user message: user_message_sent counter + per-session turn count.
                        # Sole text-side on_user_message entry, exactly once per real message.
                        _tt.note_user_message("text")
                    except Exception:
                        # Telemetry is best effort and never blocks message handling; note_first_user_message
                        # is idempotent itself, so losing one call does not affect D1 funnel statistics.
                        pass
                    # Symmetric with on_user_message: publish original user text to plugin bus user-context
                    # bucket. Voice publishes in handle_input_transcript; cover only text here
                    # to avoid duplicate publication with the voice entry.
                    self._publish_user_utterance_to_plugin_bus(
                        record_data,
                        is_voice_source=False,
                    )

                    # Dispatch above synchronously extracts and persists ban-topic instructions. If it found
                    # a new instruction this turn, update the next-session cache's ban block to catch the
                    # warming hot swap (warmup already finalized its prompt, swap awaits next
                    # turn end, otherwise instructions saved in between are missed). Do not write current session:
                    # original text remains visible in _conversation_history. Voice has a symmetric site in
                    # handle_input_transcript.
                    await self._inject_pending_user_directives()

                    # Mini-game invitation keyword fallback for text (PR #1141 follow-up E2).
                    # User types during a pending invitation instead of using ChoicePrompt's three buttons:
                    # matching keywords trigger the corresponding state transition, sharing the same method as
                    # voice transcription (handle_input_transcript); see logic in
                    # _dispatch_mini_game_invite_keyword。
                    await self._dispatch_mini_game_invite_keyword(
                        record_data,
                    )

                    openclaw_magic_command = self._normalize_explicit_openclaw_magic_command(data)
                    if (
                        openclaw_magic_command
                        and self._is_agent_enabled()
                        and self.agent_flags.get("openclaw_enabled", False)
                        and self.agent_flags.get("openclaw_ready", False)
                    ):
                        self._session_turn_count += 1
                        self._clear_text_pending_images()
                        self._mark_magic_command_image_drop_request(message.get("request_id"))
                        await self.mirror_user_input(
                            data,
                            metadata={
                                "source": "openclaw",
                                "kind": "magic_command",
                                "command": openclaw_magic_command,
                            },
                            request_id=message.get("request_id"),
                        )
                        await self._emit_agent_callback_turn_end(message.get("request_id"))
                        # An owed wrap-up is not settled here: typed input only
                        # reaches this through _process_stream_input, whose hold
                        # (_with_owed_wrap_up_held) pays it once this input has
                        # been handled; a settle here would return at once.
                        self._fire_task(self._publish_openclaw_magic_command(openclaw_magic_command))
                        logger.info("[%s] text input sent explicit openclaw magic command", self.lanlan_name)
                        if _turn_context_job is not None:
                            _turn_context_job.cancel()
                        return

                    # Text mode: **prefix this user message's content in place** with pending agent-task
                    # callbacks. LLM treats them as additional context accompanying the user's current utterance,
                    # naturally mentioning them in the same answer instead of starting an independent
                    # turn (issue #1033). Drained strings already include
                    # ``======[系统通知] 来自xxx的xxx======`` watermark, letting the LLM
                    # distinguish system notices from original user text.
                    #
                    # Symmetric with voice mode: ``prime_context(skipped=False)`` on
                    # GPT/GLM/Step also uses ``create_response`` to inject callbacks
                    # as user-role messages. Inlining into offline user
                    # content preserves those semantics; callback text is persisted in transcript with the user message
                    # (the issue's old 'persistence contamination' concern is obsolete;
                    # passive callbacks remain in history with user input for later AI
                    # reference).
                    #
                    # Drain also removes hot-swap mirrors. Keep every drained
                    # callback retryable until this turn reaches history.
                    _agent_cb_ctx = ""
                    _agent_cb_images = []
                    _agent_cb_drained = []
                    _agent_cb_extra_snapshot = []
                    # Warning: bind **before** the outer try: rollback can occur at any await after
                    # drain, including awaits preceding the assignment below. Defining inside try
                    # leaves the name unbound in finally on early cancellation, silently breaking rollback.
                    _cb_turn_committed = False
                    if self.pending_agent_callbacks:
                        callbacks_snapshot = self._claim_agent_callbacks_for_llm()
                        try:
                            _passive_media_outcome = await self._stage_passive_callback_media(
                                callbacks_snapshot,
                                self.session,
                            )
                            _agent_cb_extra_snapshot = list(
                                getattr(self, "pending_extra_replies", None) or []
                            )
                            # Taken after the media await and before the
                            # synchronous drain: a callback another path
                            # dequeued during that await is not this drain's.
                            _queued_before_drain = {
                                id(cb) for cb in self.pending_agent_callbacks
                            }
                            _agent_cb_ctx = (
                                self.drain_agent_callbacks_for_llm(
                                    callbacks_snapshot
                                )
                                or ""
                            )
                            if _agent_cb_ctx:
                                _agent_cb_images = list(
                                    _passive_media_outcome.get(
                                        "system_prefix_images",
                                        [],
                                    )
                                )
                                # Track only callbacks this drain consumed;
                                # deferred entries must retain their queue order.
                                _still_queued = {
                                    id(cb) for cb in self.pending_agent_callbacks
                                }
                                _agent_cb_drained = [
                                    cb
                                    for cb in callbacks_snapshot
                                    if isinstance(cb, dict)
                                    and id(cb) in _queued_before_drain
                                    and id(cb) not in _still_queued
                                ]
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback drain failed: {_cb_err}")
                            _agent_cb_ctx = ""
                        finally:
                            self._release_agent_callback_prompt_claims(
                                callbacks_snapshot
                            )

                    # Like _cb_turn_committed: bind before try so finally can read it.
                    reply_turn = None
                    try:
                        text_request_id = message.get("request_id")
                        self._active_text_request_id = text_request_id
                        # Snapshot for this response: its discard / completion callback may run much later
                        # (close()-truncated responses finalize only when a stalled tool or backoff returns),
                        # Shared request id / metadata may belong to a newer turn by then.
                        reply_turn = self._begin_reply_turn(
                            speech_id=new_user_sid,
                            request_id=text_request_id,
                        )
                        # Record consumed tool context as provenance, even when
                        # this reply also contains ordinary conversation. Current
                        # sync policy stores the whole mixed reply locally; later
                        # tool sync must change the policy, not disguise its origin.
                        if not is_chat_input_source(message_source):
                            mark_turn_origin(self, new_user_sid, 'system')
                        elif _agent_cb_ctx:
                            mark_turn_origin(self, new_user_sid, 'agent')
                        # Path A (inline) focused cognition: score this user message and, if
                        # over the bar, run THIS reply thinking-on. Scored on
                        # ``record_data`` (= memory_text or data) — the user-VISIBLE
                        # text that also feeds the activity tracker / cadence baseline
                        # and history replacement. Scoring raw ``data`` instead would
                        # read a hidden scaffold prompt (e.g. avatar-drop file
                        # contents) the user never typed, mismatching the cadence
                        # signal and entering Focus on evidence the user didn't author.
                        _focus_thinking = await self._focus_inline_decision(record_data)
                        # Retrieve this turn's additional context until the budget expires; discard entirely if a newer message superseded
                        # the turn (rotated speech id) or session changed (captured at task start,
                        # see _collect_turn_context).
                        _turn_context = await self._collect_turn_context(
                            _turn_context_job,
                            still_current=lambda: self.current_speech_id == new_user_sid,
                        )

                        async def response_discarded_callback(
                            reason: str,
                            attempt: int,
                            max_attempts: int,
                            will_retry: bool,
                            discard_message: str | None = None,
                            *,
                            _request_id=text_request_id,
                            _reply_turn=reply_turn,
                        ) -> None:
                            await self.handle_response_discarded(
                                reason,
                                attempt,
                                max_attempts,
                                will_retry,
                                discard_message,
                                request_id=_request_id,
                                reply_turn=_reply_turn,
                            )

                        async def response_done_callback(
                            *,
                            _reply_turn=reply_turn,
                        ) -> None:
                            await self.handle_response_complete(reply_turn=_reply_turn)

                        transcript_metadata = {"source": message_source} if memory_text and message_source else None

                        async def input_transcript_callback(
                            _transcript: str,
                            *,
                            _memory_text: str = record_data,
                            _message_source: str = message_source if memory_text else '',
                            _transcript_metadata: dict | None = transcript_metadata,
                            _request_id=text_request_id,
                            _original_text: str = record_data,
                            _original_source: str = message_source,
                        ) -> None:
                            # Keep complete visible input; the provider strips its callback argument.
                            # Raw origin is separate so ordinary memory metadata stays unchanged.
                            await self.handle_input_transcript(
                                _memory_text,
                                is_voice_source=False,
                                source=_message_source,
                                metadata=_transcript_metadata,
                                chat_original_text=_original_text,
                                chat_request_id=_request_id,
                                chat_input_source=_original_source,
                            )

                        stream_text_kwargs = {
                            "system_prefix": _agent_cb_ctx or None,
                            "thinking_on": _focus_thinking,
                            "response_discarded_callback": response_discarded_callback,
                            "response_done_callback": response_done_callback,
                            "reply_owner": reply_turn,
                        }
                        def _mark_cb_turn_committed() -> None:
                            nonlocal _cb_turn_committed
                            _cb_turn_committed = True

                        if _agent_cb_images:
                            stream_text_kwargs["system_prefix_images"] = _agent_cb_images
                        if _agent_cb_drained:
                            # Use this request's commit notification for both
                            # text-only and image callbacks. Another request's
                            # history growth cannot establish this one's delivery.
                            stream_text_kwargs["on_turn_committed"] = (
                                _mark_cb_turn_committed
                            )
                        if input_transcript_callback:
                            stream_text_kwargs["input_transcript_callback"] = input_transcript_callback
                        if memory_text:
                            stream_text_kwargs["history_replacement_text"] = memory_text
                        if _turn_context is not None:
                            stream_text_kwargs["turn_context"] = _turn_context
                        try:
                            if _focus_thinking:
                                # Focused-cognition turns enable thinking: pulse the frontend in advance so
                                # the bubble shows up the instant the turn starts
                                # (immediate feedback), before any reasoning chunk
                                # arrives. Idempotent and harmless — a non-Focus turn
                                # instead pulses lazily from
                                # OmniOfflineClient.on_thinking_active on its first
                                # reasoning chunk (handle_thinking_active). Either way
                                # the bubble clears on the first visible token
                                # (send_lanlan_response) or in the finally below.
                                #
                                # Warning: set True **inside** try; this call itself awaits
                                # WebSocket lock / send_json. Cancellation here during teardown leaves
                                # _focus_thinking_active set and notification queued,
                                # but skips cleanup forever, leaving the bubble lit until some later turn happens to clear it.
                                await self._push_focus_thinking(True)
                            if callable(on_dispatch_attempted):
                                on_dispatch_attempted()
                            # Several awaits (thinking bubble, etc.) follow context collection; recheck immediately before the actual call,
                            # without further awaits, and omit context if session changed or this turn was superseded.
                            if "turn_context" in stream_text_kwargs and not (
                                self.session is getattr(_turn_context_job, "session", None)
                                and self.current_speech_id == new_user_sid
                            ):
                                stream_text_kwargs.pop("turn_context")
                            # No await before the following call: this captures the actual client receiving this turn.
                            reply_turn.session = self.session
                            await self.session.stream_text(data, **stream_text_kwargs)
                        finally:
                            # stream_text claims the staged attachments (or puts them
                            # back on failure); release ledger entries for claimed ones.
                            self._prune_request_staged_images()
                            # Clear unconditionally: a non-Focus turn may have pulsed the
                            # bubble True via the reasoning callback, so gating the clear
                            # on _focus_thinking would leave it stuck on tool-only / empty
                            # / error turns. _push_focus_thinking is idempotent, so a no-op
                            # clear when nothing pulsed costs nothing.
                            await self._push_focus_thinking(False)
                    finally:
                        # Cover **every** await between drain and history insertion,
                        # not just stream_text: teardown can cancel this input task inside
                        # _focus_inline_decision / _push_focus_thinking(True), after callback dequeue
                        # and successful-delivery reporting, permanently losing
                        # both text and image.
                        #
                        # Still roll back only if **this turn never entered history**: failures after commit
                        # belong to the existing best-effort contract (the model already sees the content),
                        # and rollback would duplicate delivery.
                        if _agent_cb_drained and not _cb_turn_committed:
                            self._requeue_undelivered_callbacks(
                                _agent_cb_drained, _agent_cb_extra_snapshot
                            )
                        if reply_turn is not None:
                            try:
                                if reply_turn.reply_tail is not None:
                                    await reply_turn.reply_tail.registry.finish(reply_turn.reply_tail)
                            finally:
                                self._end_reply_turn(reply_turn)
                else:
                    logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            
            if input_type in _IMAGE_INPUT_TYPES:
                try:
                    if self._should_drop_magic_command_image(message.get("request_id")):
                        return
                    target_session = self.session
                    image_arrival_time = (
                        self._user_input_ingress_time(message)
                        if input_type in {"avatar_drop_image", "user_image"}
                        else None
                    )
                    # Process data with unified image utilities (validate only, no resizing).
                    image_b64 = (
                        validated_one_shot_image_b64
                        if input_type in {"avatar_drop_image", "user_image"}
                        else await _core_facade.process_screen_data(data)
                    )

                    if image_b64:
                        image_accepted = False
                        # Overlay Avatar text annotations only when this message includes position metadata.
                        # Do not fall back to self._avatar_position: absent frontend position means no overlay on this image
                        # (e.g. window screenshots or mobile camera).
                        av_pos = message.get('avatar_position') if input_type in {"screen", "camera"} else None
                        if av_pos and isinstance(av_pos, dict):
                            try:
                                image_b64 = await asyncio.to_thread(
                                    overlay_avatar_annotation,
                                    image_b64, av_pos, self.lanlan_name,
                                    get_global_language_full(),
                                )
                            except Exception as ann_err:
                                logger.warning("[%s] avatar annotation failed, sending original: %s",
                                               self.lanlan_name, ann_err)

                        if not self.is_active or self.session is not target_session:
                            return

                        independent_live_frame = (
                            input_type in _LIVE_VISION_STREAM_INPUT_TYPES
                            and getattr(self, "_asr_route_mode", "blocked")
                            == "independent"
                        )
                        if independent_live_frame:
                            captured_at = message.get("_visual_input_ingress_time")
                            image_accepted = self._stage_independent_visual_frame(
                                image_b64,
                                source=input_type,
                                request_id=message.get("request_id"),
                                captured_at=captured_at,
                            )
                            # Keep the active Realtime adapter's provider-neutral
                            # latest-frame cache warm for proactive observation,
                            # without sending the image to the provider. Once a
                            # handoff promotes Offline, Core remains the sole owner
                            # of live frames and they never leak into its attachment
                            # queue.
                            stage_session_frame = getattr(
                                self.session,
                                "stage_multimodal_frame",
                                None,
                            )
                            if image_accepted and callable(stage_session_frame):
                                try:
                                    stage_session_frame(
                                        image_b64,
                                        source=input_type,
                                        request_id=message.get("request_id"),
                                        captured_at=captured_at,
                                    )
                                except asyncio.CancelledError:
                                    raise
                                except Exception as stage_error:
                                    logger.warning(
                                        "[%s] Realtime multimodal frame cache update failed: %s",
                                        self.lanlan_name,
                                        stage_error,
                                    )

                        # In text mode (OmniOfflineClient), store images without sending immediately.
                        elif isinstance(target_session, OmniOfflineClient):
                            # screen/camera validation runs in background; the same request's slash shortcut
                            # may already have executed, so recheck before buffering to avoid attaching its screenshot to the next message.
                            if self._should_drop_magic_command_image(message.get("request_id")):
                                return
                            # Add only to pending queue for sending with text.
                            if callable(on_dispatch_attempted):
                                on_dispatch_attempted()
                            await target_session.stream_image(image_b64)
                            if not self.is_active or self.session is not target_session:
                                return
                            image_accepted = True
                            # Record the attachment's request owner: later slash shortcuts remove
                            # only their own buffered attachments, leaving earlier messages' images alone.
                            self._record_request_staged_image(
                                message.get("request_id"), image_b64
                            )
                            image_data = (
                                ""
                                if input_type in {"avatar_drop_image", "user_image"}
                                else f"data:image/jpeg;base64,{image_b64}"
                            )
                            image_message = {
                                "input_type": input_type,
                                "data": image_data,
                                "has_image": True,
                                "mime_type": "image/jpeg",
                            }
                            message_source = str(message.get("source") or "").strip()
                            if message_source:
                                image_message["source"] = message_source
                            if message.get("request_id"):
                                image_message["request_id"] = message.get("request_id")
                            self.sync_message_queue.put({
                                "type": "user",
                                "data": image_message,
                            })

                        # In voice mode (OmniRealtimeClient), check vision support and send directly.
                        elif isinstance(target_session, OmniRealtimeClient):
                            # Check WebSocket connection.
                            if not hasattr(target_session, 'ws') or not target_session.ws:
                                logger.error("💥 Stream: Session websocket not available")
                                return

                            # screen/camera are live environmental frames. The
                            # Realtime session owns whether they are delivered
                            # natively or staged for external vision, and needs
                            # their source/request identity to bind a fixed
                            # generation to the matching independent-ASR turn.
                            # One-shot avatar/chat attachments retain the
                            # pre-existing text/offline contract above.
                            if input_type in _LIVE_VISION_STREAM_INPUT_TYPES:
                                if callable(on_dispatch_attempted):
                                    on_dispatch_attempted()
                                stage_result = await target_session.stream_image(
                                    image_b64,
                                    source=input_type,
                                    request_id=message.get("request_id"),
                                    captured_at=message.get(
                                        "_visual_input_ingress_time"
                                    ),
                                )
                                if (
                                    not self.is_active
                                    or self.session is not target_session
                                ):
                                    return
                                image_accepted = bool(
                                    getattr(stage_result, "accepted", False)
                                )
                            else:
                                logger.info(
                                    "[%s] one-shot image kept out of realtime "
                                    "visual staging: input_type=%s",
                                    self.lanlan_name,
                                    input_type,
                                )
                        if (
                            image_accepted
                            and image_arrival_time is not None
                        ):
                            self.note_user_engagement(at=image_arrival_time)
                    else:
                        logger.error("💥 Stream: 图像数据验证失败")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing image data: {e}")
                    return

        except web_exceptions.ConnectionClosedError as e:
            logger.error(f"💥 Stream: Error sending data to session: {e}")
            if '1011' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1011_MIC_CHECK"}))
            if '1007' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1007_ARREARS"}))
            await self.disconnected_by_server()
            return
        except Exception as e:
            error_message = f"Stream: Error sending data to session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(json.dumps({"code": "API_UNKNOWN_ERROR", "details": {"msg": error_message}}))
        finally:
            if defer_pending_flush:
                self._deferred_pending_input_flush_count = max(
                    0,
                    getattr(self, "_deferred_pending_input_flush_count", 1) - 1,
                )
                if (
                    self._deferred_pending_input_flush_count == 0
                    and self.is_active
                    and isinstance(self.session, OmniOfflineClient)
                ):
                    try:
                        await self._flush_pending_input_data()
                    except asyncio.CancelledError:
                        raise
                    except Exception as flush_error:
                        logger.error(
                            "💥 deferred attachment input flush failed: %s",
                            flush_error,
                        )
