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
"""Session lifecycle for ``LLMSessionManager``: start/end/cleanup,
pending-session preparation, the hot-swap finalization sequence, the
idle reset loop, and error/silence recovery.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import inspect
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import session_reply_in_progress
from main_logic.provider_failure_signals import (
    CODES_REQUIRING_MSG_DETAIL,
    classify_provider_failure_text,
)
from main_logic.proactive_delivery import (
    DELIVERY_RETRACTED_KEY,
    PASSIVE_MEDIA_BUDGET_DEFERRED_KEY,
    PASSIVE_MEDIA_MAX_RETRIES,
    PASSIVE_MEDIA_RETRY_KEY,
    PASSIVE_MEDIA_TRANSIENT_KEY,
    SWAP_PRIME_DELIVERY_CLAIM_KEY,
    resolve_callback_delivery_ack,
)
from utils.gptsovits_config import is_gsv_disabled_voice_id
from config.prompts.prompts_sys import get_context_summary_ready
from utils.config_manager import _as_bool, ensure_default_yui_voice_for_free_api
from utils.language_utils import normalize_language_code, get_global_language_full
from utils.game_route_state import get_active_game_route_generation_identity
from queue import Empty
from uuid import uuid4
import httpx
from ._shared import (
    logger,
    IDLE_SESSION_RESET_THRESHOLD_SECONDS,
    IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS,
    FRONTEND_START_SESSION_TIMEOUT_SECONDS,
    _HANDSHAKE_OVERRIDE_UNSET,
    _START_LLM_CONCURRENT_ABORTED,
    _ORPHAN_SESSION_REAPER_TASKS,
    _PASSIVE_MEDIA_SESSION_UPDATE_ACK_TIMEOUT_S,
)
from .callback_render import (
    _build_callback_instruction,
    _render_pending_extra_replies_by_origin,
    _select_callbacks_within_token_budget,
)

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade
from .session_records import start_phase

class LifecycleMixin:
    """Session lifecycle methods (see module docstring)."""

    def is_goodbye_silent(self) -> bool:
        """Whether cat-mode silence after being asked to leave is in effect."""
        return bool(getattr(self, "goodbye_silent", False))

    def set_goodbye_silent(self, active: bool, reason: str = "") -> None:
        """Sync the frontend cat-mode silence state, and park queued proactive callbacks in the persistent queue."""
        active = bool(active)
        reason = str(reason or "")[:64]
        was_active = self.is_goodbye_silent()
        if active and not was_active:
            self.goodbye_silent_started_monotonic = time.monotonic()
            self.goodbye_silent_completed_duration = None
        elif not active and was_active:
            started_at = float(getattr(self, "goodbye_silent_started_monotonic", 0.0) or 0.0)
            self.goodbye_silent_completed_duration = (
                max(0.0, time.monotonic() - started_at)
                if started_at > 0.0
                else None
            )
            self.goodbye_silent_started_monotonic = 0.0
        self.goodbye_silent = active
        self.goodbye_silent_reason = reason
        self.goodbye_silent_updated_at = time.time()
        if active:
            self._park_proactive_for_goodbye()
        if was_active != active:
            logger.info("[%s] goodbye_silent=%s reason=%s", self.lanlan_name, active, reason or "-")

    def consume_goodbye_cycle_duration(self) -> float | None:
        """Return one completed server-observed goodbye duration at most once."""
        if self.is_goodbye_silent():
            return None
        duration = getattr(self, "goodbye_silent_completed_duration", None)
        self.goodbye_silent_completed_duration = None
        return duration


    async def handle_silence_timeout(self, *, expected_session=None):
        """Handle voice-input silence timeout: automatically close the session while keeping the Live2D display"""
        try:
            if expected_session is not None:
                if expected_session is self.pending_session:
                    logger.info("⏭️ handle_silence_timeout: expected_session is pending_session, delegating to pending teardown")
                    await self._teardown_pending_session_from_lifecycle_callback(expected_session)
                    return
                if expected_session is not self.session:
                    logger.info("⏭️ handle_silence_timeout: expected_session stale, skipping")
                    return
            logger.warning(f"[{self.lanlan_name}] 检测到长时间无语音输入，自动关闭session")
            
            # Silent closure is the authoritative suppression boundary; never retain partial candidate audio.
            async with self.hot_swap_cache_lock:
                # Re-check: a hot-swap could have completed while we waited for the lock.
                if expected_session is not None and expected_session is not self.session and expected_session is not self.pending_session:
                    logger.info("⏭️ handle_silence_timeout: expected_session stale after acquiring cache lock, skipping")
                    return
                if self.hot_swap_audio_cache:
                    cached_duration_ms = self.hot_swap_audio_cache.duration_ms
                    self.hot_swap_audio_cache.clear()
                    logger.info(
                        "🗑️ 静默超时：已清空 %s ms 热切换音频缓存",
                        cached_duration_ms,
                    )
            
            # Re-check before websocket side-effects
            if expected_session is not None and expected_session is not self.session and expected_session is not self.pending_session:
                logger.info("⏭️ handle_silence_timeout: expected_session stale before WS send, skipping")
                return
            
            # Deriving the payload needs no socket, so it is hoisted out of the
            # display-socket guard: the lease holder must hear the microphone
            # teardown even when the display socket is already gone. Closing a
            # chat window leaves mgr.websocket pointing at the dead socket until
            # that disconnect's voice handover repoints it, and the silence
            # timeout can fire inside exactly that window.
            session_for_reason = expected_session or self.session or self.pending_session
            timeout_api_type = str(
                getattr(session_for_reason, "_api_type", "") or getattr(self, "core_api_type", "") or ""
            ).lower()
            timeout_model = str(
                getattr(session_for_reason, "_model_lower", "")
                or getattr(session_for_reason, "model", "")
                or ""
            ).lower()
            is_free_timeout = timeout_api_type == "free" or "free" in timeout_model
            timeout_reason_code = (
                "free_api_silence_timeout" if is_free_timeout else "silence_timeout"
            )
            # Snapshot the lease this timeout is speaking to, BEFORE the sends
            # below await. Compared again just before the fan-out.
            #
            # Connection id only, deliberately NOT the lease generation: the
            # generation is bumped by the SAME holder on every hard_mute /
            # hard_unmute / focus_suppress / focus_resume / lease_sync
            # (asr_runtime.py _handle_voice_input_control). Including it makes a
            # user muting during the display send look like a lease handover, so
            # the teardown is skipped while end_session below still runs -- the
            # backend closes the session and the recorder never hears about it,
            # which is the exact zombie microphone this fan-out exists to
            # prevent. Identity is what decides whether we are still talking to
            # the same window; its mute state is not.
            voice_lease_identity = getattr(self, "_voice_lease_connection_id", "")
            if is_free_timeout:
                # Pure display toast; send_status guards its own socket.
                await self.send_status(json.dumps({"code": "FREE_API_AUTO_CLOSE_VOICE"}))
            auto_close_payload = {
                "type": "auto_close_mic",
                "reason_code": timeout_reason_code,
                "api_type": timeout_api_type,
                "message": f"{self.lanlan_name}检测到长时间无语音输入，已自动关闭麦克风"
            }
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    await self.websocket.send_json(auto_close_payload)
                except WebSocketDisconnect:
                    # Isolated: a display socket that dies between the CONNECTED
                    # check and the send must not skip the fan-out below, nor
                    # the end_session that follows it.
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[%s] auto_close_mic display push failed: %s",
                        self.lanlan_name,
                        e,
                    )
            # Re-validate before targeting the LEASE holder. Codex P2: the sends
            # above await and nothing here holds ``self.lock``, so a replacement
            # session can be installed during them -- and _send_to_voice_owner
            # resolves the owner socket at CALL time, so this old timeout's
            # auto_close_mic would land on the NEW recorder and stop a
            # microphone the user just opened. Reproduced: with the lease moved
            # mid-send, the fresh recorder received the stale teardown.
            #
            # end_session below is guarded by expected_session and refuses on
            # its own, which is precisely why the damage was frontend-only and
            # invisible from the backend. It still runs: a stale timeout has
            # nothing to close, and the guard is what decides that.
            #
            # The lease holder is the window with the live hardware; the
            # current socket may be a newer chat window with no microphone.
            # Game owner exempt, mirroring _revoke_lease_for_blocked_route.
            # (The FREE_API_AUTO_CLOSE_VOICE send_status above stays
            # current-socket-only on purpose: it is a pure toast.)
            session_still_current = expected_session is None or (
                expected_session is self.session
                or expected_session is self.pending_session
            )
            lease_still_current = voice_lease_identity == getattr(
                self, "_voice_lease_connection_id", ""
            )
            if not session_still_current or not lease_still_current:
                logger.info(
                    "⏭️ handle_silence_timeout: session/lease moved during the display send, "
                    "skipping the recorder teardown (session_current=%s lease_current=%s)",
                    session_still_current,
                    lease_still_current,
                )
            elif getattr(self, "_voice_lease_owner", "none") != "game":
                send_to_voice_owner = getattr(self, "_send_to_voice_owner", None)
                if callable(send_to_voice_owner):
                    await send_to_voice_owner(auto_close_payload)
            
            await self.end_session(by_server=True, expected_session=expected_session)
            
        except Exception as e:
            logger.error(f"处理静默超时时出错: {e}")
    
    async def handle_connection_error(self, message=None, *, expected_session=None):
        message_text = str(message) if message is not None else ""
        try:
            _parsed = json.loads(message_text) if message_text.startswith('{') else None
        except (json.JSONDecodeError, TypeError):
            _parsed = None

        defer_native_idle_reconnect = False
        async with self.lock:
            is_pending = False
            if expected_session is not None:
                if expected_session is self.pending_session:
                    is_pending = True
                elif expected_session is not self.session:
                    logger.info("⏭️ handle_connection_error: expected_session stale (not current session), skipping")
                    return
                details = _parsed.get('details') if isinstance(_parsed, dict) else None
                failure_generation = (
                    details.get('connection_generation')
                    if isinstance(details, dict)
                    else None
                )
                current_generation = getattr(
                    expected_session, '_connection_generation', None
                )
                if (
                    isinstance(failure_generation, int)
                    and isinstance(current_generation, int)
                    and failure_generation != current_generation
                ):
                    logger.info(
                        "⏭️ handle_connection_error: connection generation stale "
                        "(failure=%s current=%s), skipping",
                        failure_generation,
                        current_generation,
                    )
                    return
            # Only flag the manager-level flag for main session errors (or unguarded calls).
            # A pending_session failure must not misclassify the main session as closed.
            if not is_pending:
                self.session_closed_by_server = True

                # Voice-session activation deliberately keeps microphone PCM
                # local while waiting. Some native providers retire an otherwise
                # healthy socket during that quiet interval. Keep the Core voice
                # lease alive for this one classified condition so the first
                # owner-authorized output can reconnect the same client before
                # replaying. Every other provider failure retains the ordinary
                # fail-closed teardown below.
                capture_activation_generation = getattr(
                    self,
                    "_capture_voice_session_activation_generation",
                    None,
                )
                activation_runtime = getattr(
                    self,
                    "_voice_session_activation_runtime",
                    None,
                )
                voice_input_accepts_pcm = getattr(
                    self,
                    "_voice_input_accepts_pcm",
                    None,
                )
                defer_native_idle_reconnect = bool(
                    isinstance(_parsed, dict)
                    and _parsed.get("code") == "API_IDLE_TIMEOUT"
                    and expected_session is not None
                    and getattr(self, "_voice_session_activation_factory", None)
                    is not None
                    and activation_runtime is not None
                    and getattr(self, "_asr_route_mode", "blocked") == "native"
                    and getattr(self, "_voice_lease_owner", "none") == "core"
                    and callable(capture_activation_generation)
                    and activation_runtime.generation
                    == capture_activation_generation()
                    and callable(voice_input_accepts_pcm)
                    and voice_input_accepts_pcm()
                )
                if defer_native_idle_reconnect:
                    self._native_activation_idle_reconnect_identity = (
                        activation_runtime.generation,
                        getattr(expected_session, "_connection_generation", None),
                    )
        
        if is_pending:
            logger.info("⏭️ handle_connection_error: expected_session is pending_session, delegating to pending teardown")
            await self._teardown_pending_session_from_lifecycle_callback(expected_session, message)
            return

        if defer_native_idle_reconnect:
            logger.info(
                "[%s] native provider idled while voice-session activation "
                "owns the microphone; deferring reconnect until authorized output",
                self.lanlan_name,
            )
            return
        
        status_code = None
        if message:
            # Pre-classified structured errors from omni_realtime_client (JSON with "code")
            # Forward them directly so the frontend sees the original code.
            if _parsed and isinstance(_parsed, dict) and _parsed.get('code'):
                status_code = _parsed.get('code')
                # Peer disconnects use the existing recovery below, which
                # supplies CHARACTER_DISCONNECTED with the configured name.
                # Forwarding this marker here would show the same toast twice.
                if status_code != 'CHARACTER_DISCONNECTED':
                    await self.send_status(message_text)
            else:
                # Same criteria, and the same ordering, the realtime close
                # path reads — from one place, so a keyword added for one
                # provider never goes missing on the other side. The details
                # payload stays this side's own: here we are holding a real
                # upstream diagnostic and echo it, where a peer-controlled
                # close reason is deliberately withheld.
                status_code = (
                    classify_provider_failure_text(message_text)
                    or "API_UNKNOWN_ERROR"
                )
                status_payload = {"code": status_code}
                if status_code in CODES_REQUIRING_MSG_DETAIL:
                    status_payload["details"] = {"msg": message_text}
                await self.send_status(json.dumps(status_payload))
        logger.info("💥 Realtime connection recovery requested.")
        # CHARACTER_DISCONNECTED makes a recording frontend restart the voice
        # session 7.5s later. After the free service's daily quota is spent
        # that restart is rejected every time (the server does not say when
        # the window reopens), so end the session without it; the user's next
        # manual start retries. Paid providers keep the restart: their quota
        # wording also covers recoverable per-minute 429s.
        free_quota_spent = (
            status_code == 'API_QUOTA_TIME'
            and getattr(self, 'core_api_type', '') == 'free'
        )
        await self.disconnected_by_server(
            expected_session=expected_session,
            announce_disconnect=not free_quota_spent,
        )
    
    async def handle_repetition_detected(self):
        """Handle the repetition-detection callback: reset Focus state, notify the frontend"""
        try:
            logger.warning(f"[{self.lanlan_name}] 检测到高重复度对话")

            # Repetition recovery wiped _conversation_history — the Focus
            # accumulator charge / mode and the cadence baseline are evidence
            # from the now-erased conversation, so clear them too (symmetrical with
            # _init_renew_status's session-wide cleanup). clear_focus emits no FOCUS_EXIT:
            # a degenerate looping episode is not a coherent episode to
            # synthesize. Best-effort — never block the frontend notice.
            try:
                await self.state.clear_focus()
                self._focus_scorer.reset()
                self._master_emotion.reset()
            except Exception as _focus_err:
                logger.debug(f"[{self.lanlan_name}] focus reset on repetition failed: {_focus_err}")

            # Send a duplicate warning to the frontend using an i18n key.
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "repetition_warning",
                    "name": self.lanlan_name  # The frontend fills its i18n template with this name.
                })
            
        except Exception as e:
            logger.error(f"处理重复度检测时出错: {e}")

    def _bind_session_lifecycle_callbacks(self, session):
        """Bind lifecycle callbacks with closure-captured session reference.
        
        Ensures that even if self.session is replaced later, the callbacks
        still carry a reference to the session they were bound to,
        enabling the expected_session guard to detect stale callbacks.
        """
        self._bind_owned_output_callbacks(session)
        async def on_connection_error(message=None, session_ref=session):
            await self._run_owned_lifecycle_callback(
                session_ref, self.handle_connection_error, message, expected_session=session_ref,
            )
        
        # OmniRealtimeClient stores as .on_connection_error
        if isinstance(session, OmniRealtimeClient):
            session.on_connection_error = on_connection_error
        # OmniOfflineClient stores as .handle_connection_error
        elif isinstance(session, OmniOfflineClient):
            session.handle_connection_error = on_connection_error
        
        if hasattr(session, 'on_silence_timeout'):
            async def on_silence_timeout(session_ref=session):
                await self._run_owned_lifecycle_callback(
                    session_ref, self.handle_silence_timeout, expected_session=session_ref,
                )
            session.on_silence_timeout = on_silence_timeout

    async def _restart_message_handler_after_session_reconnect(
        self,
        session_ref,
    ) -> bool:
        """Replace the receive task after an in-place Provider reconnect.

        Gemini quarantine reconnects the same ``OmniRealtimeClient`` object,
        while its existing handler remains bound to the retired SDK session.
        Keep task ownership in Core and use the manager/session identity as the
        CAS fence so a concurrent end-session or hot swap cannot resurrect a
        listener for a session it already replaced.
        """

        if session_ref is not self.session or not self.is_active:
            return False
        previous_task = self.message_handler_task
        if previous_task is not None and previous_task is not asyncio.current_task():
            if not previous_task.done():
                previous_task.cancel()
            # Wait boundedly: receive tasks tied to retired SDK sessions may delay or swallow
            # CancelledError, and several helper callers enter **holding**
            # _core_voice_session_swap_lock (e.g. asr_runtime.py's
            # final-submit path). Unbounded gather would deadlock later voice finals and hot swaps.
            # Follow handoff listener-timeout criteria: wait until the deadline, not cancellation completion.
            done, _pending = await asyncio.wait(
                {previous_task},
                timeout=getattr(
                    self,
                    "_core_voice_listener_cancel_timeout_s",
                    2.0,
                ),
            )
            if not done:
                # An unstoppable listener remains tied to the retired session. Never install a replacement
                # over it, running two receive loops on the same client.
                #
                # Merely returning False is insufficient: callers abandon only this delivery while
                # self.session / is_active / message_handler_task still reference an apparently
                # live client with no receive loop. Every later turn would hit the same
                # stuck task and time out, leaving voice replies permanently unavailable. Therefore
                # retire this session, following the handoff timeout path's same criterion.
                logger.error(
                    '[%s] session reconnect: previous listener cancellation timed out; retiring the unusable session',
                    self.lanlan_name,
                )
                orphan_session = session_ref
                stuck_listener = previous_task
                async with self.lock:
                    # Dual-identity CAS: never clear a concurrent winner's new session or
                    # listener through this failure path.
                    if (
                        self.session is session_ref
                        and self.message_handler_task is previous_task
                    ):
                        self.session = None
                        self.message_handler_task = None
                        self.is_active = False
                        self.session_ready = False
                    else:
                        orphan_session = None

                if orphan_session is not None:
                    async def _reap_reconnect_session_after_listener_exit():
                        # Close first (close() synchronously detaches the socket), then bounded join;
                        # reversing this waits for a task already proven unstoppable.
                        try:
                            await self._close_owned_session(orphan_session)
                        except Exception as reap_err:
                            logger.debug(
                                '[%s] session reconnect: orphan close failed: %s',
                                self.lanlan_name,
                                reap_err,
                            )
                        try:
                            await asyncio.wait(
                                {stuck_listener},
                                timeout=getattr(
                                    self,
                                    "_core_voice_listener_cancel_timeout_s",
                                    2.0,
                                ),
                            )
                        except (asyncio.CancelledError, Exception):
                            pass

                    reaper = asyncio.create_task(
                        _reap_reconnect_session_after_listener_exit()
                    )
                    _ORPHAN_SESSION_REAPER_TASKS.add(reaper)
                    reaper.add_done_callback(_ORPHAN_SESSION_REAPER_TASKS.discard)
                    # With no session, the microphone must not keep collecting PCM; standalone ASR would send transcripts
                    # into a nonexistent reply session. Follow handoff's
                    # listener_timeout_fail_closed behavior, only after winning CAS and actually
                    # clearing this session ourselves.
                    await self._close_independent_asr(next_route_mode="blocked")
                    await self.send_session_ended_by_server()
                return False
            # Retrieve the result once so old listener exceptions do not become never-retrieved warnings.
            if not previous_task.cancelled():
                previous_task.exception()

        async with self.lock:
            if session_ref is not self.session or not self.is_active:
                return False
            current_task = self.message_handler_task
            if (
                current_task is not None
                and current_task is not previous_task
                and not current_task.done()
            ):
                return True
            self.message_handler_task = asyncio.create_task(
                session_ref.handle_messages()
            )
        return True

    async def _teardown_pending_session_from_lifecycle_callback(self, expected_session, message=None):
        """Handle lifecycle callback (connection_error / silence_timeout) fired
        by a pending_session that has NOT yet been promoted to self.session.
        
        This avoids routing through the main session cleanup flow which would
        incorrectly kill the active main session.
        """
        if message:
            message_text = str(message)
            logger.warning(f"💥 Pending session lifecycle error: {message_text}")
        else:
            logger.warning("💥 Pending session lifecycle event (silence/disconnect)")
        
        if expected_session is self.pending_session:
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
        else:
            # pending_session already swapped or cleaned by someone else
            logger.info("⏭️ _teardown_pending: expected_session no longer matches pending_session, skipping")

    async def _reset_preparation_state(self, clear_main_cache=False, from_final_swap=False):
        """[Hot-swap related] Helper to reset flags and pending components related to new session prep.
        
        async because we await cancelled tasks to guarantee they have exited
        before clearing references — prevents >2 concurrent OmniRealtimeClient.
        """
        self.is_preparing_new_session = False
        self._require_context_append_current_delivery = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self._primed_context_snapshot = None
        
        # Snapshot task refs, cancel, await completion, THEN clear.
        # This ensures CancelledError handlers (e.g. _cleanup_pending_session_resources)
        # finish before we drop references, preventing races with newly created tasks.
        bg_task_ref = self.background_preparation_task
        swap_task_ref = self.final_swap_task if not from_final_swap else None
        # Self-reference guard: final_swap_task often calls this cleanup itself (swap abort handlers,
        # entry guards, and prime-failure exits omit from_final_swap). Canceling the current task delivers
        # CancelledError at gather below into the caller's except block, truncating all subsequent
        # cleanup. Production-topology tests found swap fail-close/recovery tails unreachable this way;
        # entry guards before try can also leave is_hot_swap_imminent stuck True after reset.
        # Never cancel or await the current task, since awaiting oneself deadlocks.
        _current_task = asyncio.current_task()
        if bg_task_ref is _current_task:
            bg_task_ref = None
        if swap_task_ref is _current_task:
            swap_task_ref = None

        tasks_to_await = []
        if bg_task_ref and not bg_task_ref.done():
            bg_task_ref.cancel()
            tasks_to_await.append(bg_task_ref)
        if swap_task_ref and not swap_task_ref.done():
            swap_task_ref.cancel()
            tasks_to_await.append(swap_task_ref)
        # Parallel wait: bg and swap tasks are canceled; sequential waits cost up to 4s, gather caps this at 2s.
        if tasks_to_await:
            async def _wait_one(t):
                try:
                    await asyncio.wait_for(t, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    # Cleanup path: canceled tasks necessarily raise one of these two; swallow them.
                    pass
                except Exception as e:
                    # Unexpected exceptions must not block readiness reset; log debug for diagnosis.
                    logger.debug(f"_wait_one: ignored unexpected exception: {e}")
            await asyncio.gather(*(_wait_one(t) for t in tasks_to_await), return_exceptions=True)
        
        if self.background_preparation_task is bg_task_ref:
            self.background_preparation_task = None
        if not from_final_swap and self.final_swap_task is swap_task_ref:
            self.final_swap_task = None
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.pending_use_tts = None

        if clear_main_cache:
            self.message_cache_for_new_session = []
            self.initial_next_session_context_snapshot_len = 0

    async def _cleanup_pending_session_resources(self):
        """[Hot-swap related] Safely cleans up ONLY PENDING connector and session if they exist AND are not the current main session.

        The close runs as a task this manager owns and every caller awaits it
        through ``shield``. Its usual caller is the background prep task's
        CancelledError handler, and ``_reset_preparation_state`` caps its wait
        at 2s by cancelling that same task a second time — which, when the
        close was awaited directly, interrupted it after the reference had
        already been dropped: nobody left to finish closing the socket, and
        ``_wait_one`` swallows the timeout so the reset reports success. The
        cap now bounds only how long the caller waits.
        """
        # Stop any listener specifically for the pending session (if different from main listener structure)
        # The _listen_for_pending_session_response tasks are short-lived and managed by their callers.
        session, self.pending_session = self.pending_session, None
        if session:
            task = asyncio.create_task(self._close_detached_pending_session(session))
            self._pending_session_close_tasks.add(task)
            task.add_done_callback(self._pending_session_close_tasks.discard)
            await asyncio.shield(task)

    async def _close_detached_pending_session(self, session):
        """Close a pending session that no longer has a slot to be cleared from."""
        try:
            logger.info("🧹 清理pending_session资源...")
            await self._close_owned_session(session)
            logger.info("✅ Pending session已关闭")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"💥 清理pending_session时出错: {e}")

    async def _init_renew_status(self):
        # Retire pending proactive HTTP work before teardown's first await.
        self._proactive_screenshot_request_id = None
        # A reply this end cut short whose completion is the session-level
        # callback (an independent-ASR voice reply, a proactive one) never
        # runs it: the retired connection's output callbacks are dropped
        # (_bind_owned_output_callbacks). Close its AI turn here, or what it
        # said rides the next session's first turn end into the tracker as
        # part of that reply. cross_server needs nothing: the session end
        # closes its turn. Done before this method's first await.
        if getattr(self, "_current_ai_turn_text", "") or getattr(
            self, "_discarded_turn_open", False
        ):
            self._flush_ai_turn_text_to_tracker()
        await self._reset_preparation_state(True)
        self.session_start_time = None
        await self._cleanup_pending_session_resources()  # The manager owns closure, so cancellation cannot lose it.
        self.is_hot_swap_imminent = False
        self._turn_wrap_up_owed = False
        self._voice_turn_wrap_up_hold = None
        # The state machine is per-manager, reusing one instance across start_session/end_session.
        # If the prior proactive turn's WebSocket disconnects during PHASE1/PHASE2 before PROACTIVE_DONE
        # fires, phase/_preempted leaks into the new session and blocks can_start_proactive.
        # Teardown must use force=True: default reset() no-ops during active phases to protect
        # auto-start, but end_session ends the entire turn and must force cleanup.
        await self.state.reset(force=True)
        # Symmetric with SM.reset clearing focus: isolate scorer cadence baselines by session, preventing new sessions
        # from inheriting previous message-length baselines. Master emotion profiles are session-isolated too.
        self._focus_scorer.reset()
        self._master_emotion.reset()

    def _realtime_base_url(self) -> str:
        """Read the realtime route's base_url, for the native voice routing host remap
        (overseas free free→free_intl). Returns an empty string when unreadable, treated as non-lanlan.app."""
        try:
            return str((self._config_manager.get_model_api_config('realtime') or {}).get('base_url') or '')
        except Exception:
            return ''

    async def _handle_session_start_exception(
        self, e: BaseException, input_mode: str, diag_start: float,
        *, request_id=None, also_notify=None,
    ) -> None:
        """Unified handling of session start failure: log, send the status code, send_session_failed, cleanup.

        Used by start_session's outer except, covering both the prelude
        (_cleanup_pending_session_resources / end_session etc.) and the gather
        block, so the frontend doesn't get stuck on preparing.
        """
        self.session_start_failure_count += 1
        self.session_start_last_failure_time = datetime.now()
        logger.error(f"[语音会话诊断] start_session 失败 (总耗时: {time.time() - diag_start:.2f}秒): {e}")
        # Telemetry: voice-session startup failure in a voice-first desktop pet means voice breaks before the user speaks,
        # silently losing D1 users (currently invisible). Use exception class names as low-cardinality reason enums.
        # Count **audio mode only**: this handler serves text/audio start_session; text startup failures must not
        # pollute voice_setup_failed. Best-effort instrumentation must not block failure cleanup.
        if input_mode == 'audio':
            try:
                from utils.instrument import counter as _instr_counter
                _instr_counter("voice_setup_failed", reason=type(e).__name__[:32])
            except Exception:
                pass  # Best-effort telemetry: unavailable instrumentation must not block failure cleanup.
        error_str = str(e) or ("Session startup timed out" if isinstance(e, TimeoutError) else "")

        is_memory_server_error = isinstance(e, ConnectionError) and any(
            kw in error_str.lower() for kw in ["memory server", "记忆服务"]
        )

        if is_memory_server_error:
            logger.error(f"🧠 {error_str}")
            await self.send_status(json.dumps({"code": "MEMORY_SERVER_NOT_RUNNING"}))
            self._check_start_operation()
            # Exclude Memory Server errors from failure counts: these are configuration issues, not network failures.
            self.session_start_failure_count -= 1
            self._memory_error_retry_after = time.time() + self._memory_error_cooldown_seconds
        else:
            error_message = f"Error starting session: {e}"
            logger.exception(f"💥 {error_message} (失败次数: {self.session_start_failure_count})")

            if self.session_start_failure_count >= self.session_start_max_failures:
                # Emit CRITICAL + status only when the circuit **just trips**; subsequent failures
                # are intercepted by start_session's early return (theoretically never reaching here), emitting CRITICAL once.
                if not self._session_start_circuit_open:
                    self._session_start_circuit_open = True
                    critical_message = f"⛔ Session启动连续失败{self.session_start_failure_count}次，已停止自动重试。请检查网络连接和API配置，然后刷新页面重试。"
                    logger.critical(critical_message)
                    await self.send_status(json.dumps({"code": "SESSION_START_CRITICAL", "details": {"count": self.session_start_failure_count}}))
                    self._check_start_operation()
            else:
                await self.send_status(json.dumps({"code": "SESSION_START_FAILED", "details": {"error": error_str, "count": self.session_start_failure_count}}))
                self._check_start_operation()

            if isinstance(e, TimeoutError):
                await self.send_status(json.dumps({"code": "CONNECTION_TIMEOUT", "details": {"error": error_str}}))
                self._check_start_operation()
            elif 'WinError 10061' in error_str or 'WinError 10054' in error_str:
                if str(self.memory_server_port) in error_str or '48912' in error_str:
                    await self.send_status(json.dumps({"code": "MEMORY_SERVER_CRASHED", "details": {"port": self.memory_server_port}}))
                    self._check_start_operation()
                else:
                    await self.send_status(json.dumps({"code": "CONNECTION_REFUSED"}))
                    self._check_start_operation()
            elif ('401' in error_str or 'unauthorized' in error_str.lower()
                    or 'authentication' in error_str.lower()
                    or 'incorrect api key' in error_str.lower()
                    or 'invalid_api_key' in error_str.lower()
                    or ('invalid' in error_str.lower() and 'key' in error_str.lower())):
                await self.send_status(json.dumps({"code": "API_KEY_REJECTED"}))
                self._check_start_operation()
            elif '429' in error_str:
                await self.send_status(json.dumps({"code": "API_RATE_LIMIT_SESSION"}))
                self._check_start_operation()
            elif 'HTTP 503' in error_str:
                await self.send_status(json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
                self._check_start_operation()
            elif classify_provider_failure_text(error_str) == 'API_QUOTA_TIME':
                # Free servers reject a spent quota with a close frame right
                # after the handshake, which can land inside start_session.
                # Kept after 429 so "429 ... quota exceeded" stays a rate limit.
                await self.send_status(json.dumps({"code": "API_QUOTA_TIME"}))
                self._check_start_operation()
            elif 'All connection attempts failed' in error_str:
                await self.send_status(json.dumps({"code": "LLM_CONNECTION_FAILED"}))
                self._check_start_operation()
            else:
                await self.send_status(json.dumps({"code": "CONNECTION_CLOSED_ABNORMAL", "details": {"error": error_str}}))
                self._check_start_operation()

        # Send before cleanup, which clears the websocket reference.
        await self.send_session_failed(
            input_mode, request_id=request_id, also_notify=also_notify,
        )
        self._check_start_operation()
        if self._current_start_request() is not None:
            # The frontend has a terminal result. Retirement owns the input
            # clear, producers and physical close, and its barrier prevents the
            # next start from using that state before handoff. Waiting for close
            # here would extend an exhausted startup budget indefinitely.
            self.request_end_session(by_server=True)
            return
        # reset_starting_count=False: called from failed start_session's except;
        # that start_session's finally exclusively owns _starting_session_count guard
        # and decrements it last. Clearing count here opens a window where the failed task
        # has not fully finished but count is zero, allowing waiting cross-mode restarts to reenter and then be
        # clobbered by residual cleanup clearing websocket and finally decrementing the guard (Codex P2).
        self._check_start_operation()
        await self.end_session(by_server=True, reset_starting_count=False)
        self._check_start_operation()
        # However reset_starting_count=False makes end_session's inactive-early path skip
        # pending_input_data.clear(), coupled with guard release, leaving failed-start buffered input
        # to be wrongly injected by the next successful startup's _flush_pending_input_data() (Codex P2).
        # Explicitly clear this failed start's own input here: its finally still owns count (>0),
        # so no concurrent start passes and the buffer can contain only this failed start's input; clearing is safe.
        # Avoid changing end_session gating, preserving rebuild paths that also use reset_starting_count=False
        # but require buffered-input replay.
        async with self.input_cache_lock:
            self._check_start_operation()
            self.session_ready = False
            self.pending_input_data.clear()
            self._clear_pending_context_appends()

    @property
    def is_starting(self) -> bool:
        """The window where the start_session coroutine is running but is_active isn't True yet.
        Externals (e.g. the catgirl-switch path) use this to decide whether to keep
        the current manager instance, avoiding replacing a manager mid-initialization
        and leaking an orphan session.
        """
        return self._starting_session_count > 0

    @property
    def starting_input_mode(self):
        """Return the target mode being started, avoiding reads of an input_mode that hasn't finished switching."""
        if self._starting_session_count <= 0:
            return None
        return self._starting_input_mode

    def reset_session_start_circuit(self) -> None:
        """Clear the circuit breaker + failure counter + memory cooldown. Only for
        websocket_router upon receiving an explicit user start_session action — that is
        equivalent to "the user saw CRITICAL, chose to retry, and declares the config
        fixed". So _memory_error_retry_after is cleared along the way; otherwise the
        user would still wait an extra 10 seconds after starting the memory server.
        Internal recovery paths must never call this, or the circuit breaker becomes
        meaningless."""
        if (self._session_start_circuit_open
                or self.session_start_failure_count
                or self._memory_error_retry_after):
            logger.info(f"🔄 重置 session 启动熔断 (之前失败 {self.session_start_failure_count} 次)")
        self._session_start_circuit_open = False
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self._memory_error_retry_after = 0

    def shutdown(self) -> None:
        """Manager-level shutdown — cancels the idle reset background task. Caller:
        main_server's ``_init_character_resources``, before replacing the old
        manager with a new one.

        Why needed: ``_idle_session_reset_task`` is a bound-method coroutine
        holding a strong reference to ``self`` — after a config hot-reload creates
        a new LLMSessionManager to replace the old one, the old manager should be
        GC'd, but the leftover task wakes every 60s (even though it only takes the
        ``is_active==False`` early-exit branch), extending the old manager's
        lifetime indefinitely; N copies accumulate after repeated reloads.
        """
        task = self._idle_session_reset_task
        if task is not None and not task.done():
            task.cancel()
        self._idle_session_reset_task = None

    def _ensure_idle_session_reset_loop(self) -> None:
        """Lazily start the idle reset background task. Idempotent, safe to call repeatedly."""
        if self._idle_session_reset_task is not None and not self._idle_session_reset_task.done():
            return
        try:
            self._idle_session_reset_task = asyncio.create_task(self._idle_session_reset_loop())
        except RuntimeError:
            # Exceptional case: no running event loop (should not occur on the start_session path).
            logger.debug("[%s] _ensure_idle_session_reset_loop: no running loop, skip", self.lanlan_name)

    async def _idle_session_reset_loop(self) -> None:
        """Periodically check the user's silence duration; past the threshold, proactively
        end_session so the next message triggers fresh /new_dialog context injection.
        Guards: reply work in progress / takeover / session starting / no activity
        timestamp → skip this round, re-evaluate next round.

        Reply work is ``session_reply_in_progress`` and, on an offline session,
        anything ``is_idle`` still counts (the same check the owed wrap-up's
        settle waits on): a guard-paused reply has ``_is_responding`` down
        while still live, and a reply call can still be before its begin (a
        proactive reply's image setup) or finishing a cancelled reply's tool
        handler. Ending the session there would cut that reply.
        """
        while True:
            try:
                await asyncio.sleep(IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS)
                if not self.is_active or self.session is None:
                    continue
                if self._starting_session_count > 0:
                    continue
                if self._takeover_active:
                    continue
                session = self.session
                if session_reply_in_progress(session) or (
                    isinstance(session, OmniOfflineClient) and not session.is_idle()
                ):
                    continue
                last_activity = self.last_user_activity_time
                if last_activity is None:
                    continue
                idle_seconds = time.time() - last_activity
                if idle_seconds < IDLE_SESSION_RESET_THRESHOLD_SECONDS:
                    continue
                # Snapshot current session for end_session's expected_session guard:
                # if another user-triggered start_session replaces self.session during end_session's awaits,
                # end_session returns early without clearing the new session
                # or _starting_session_count guard (see end_session comments at 6011–6013).
                session_snapshot = self.session
                logger.info(
                    "[%s] idle_session_reset: 用户静默 %.0fs ≥ %ds，主动关闭 session 让下一条消息刷新上下文",
                    self.lanlan_name, idle_seconds, IDLE_SESSION_RESET_THRESHOLD_SECONDS,
                )
                try:
                    # by_server=True suppresses the final CHARACTER_LEFT status, distinguishing this
                    # path from user-initiated departure. reset_starting_count=False:
                    # expected_session early return already guards the race; add protection against clearing
                    # a new start_session guard acquired during an await.
                    await self.end_session(
                        by_server=True,
                        expected_session=session_snapshot,
                        reset_starting_count=False,
                    )
                except Exception as e:
                    logger.warning("[%s] idle_session_reset: end_session 失败: %s", self.lanlan_name, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[%s] idle_session_reset 单轮异常: %s", self.lanlan_name, e)

    async def _maybe_kick_activity_loop_for_context_prompt(self) -> None:
        """Start the activity tracker's background heartbeat.

        Context-prompt detection (entering gaming/entertainment / entering focused
        work) hangs off the tracker's 20s heartbeat, and the heartbeat lazy-starts
        only on the first get_snapshot; get_snapshot in turn is only called by
        paths where proactive chat is on. Proactive chat defaults to off at first
        start, so without an explicit kick a user who hasn't enabled proactive chat
        would never detect entering a game and the prompt would never show. Here we
        kick once when the session comes up.

        The context prompt used to be gated to the vision_chat_default_off A/B group;
        it's now merged into main and open to everyone. OS signal collection is still
        gated on the user having *explicitly* allowed autonomous vision (privacy mode
        off), but the topic candidate heartbeat is privacy-independent and should
        run even when vision is disabled.
        """
        try:
            self._activity_tracker.ensure_activity_guess_loop_started()
            # Kick only when proactiveVisionEnabled is explicitly persisted as True: get_snapshot starts
            # SystemSignalCollector's window/process collection even in privacy mode (the loop skips only the LLM,
            # not collection). Do not use is_privacy_mode_active(), which fails open as privacy-off when proactiveVisionEnabled
            # is absent, misclassifying first-start users whose UI defaults to privacy-on before settings synchronize
            # (overseas first-start default proactiveVisionEnabled=false). Starting one session would collect
            # windows/processes (Codex P1). Read raw persisted values; never kick for missing/False.
            # Wait for a later session after settings synchronize and vision is genuinely enabled. Privacy-on users already
            # no-op because screen sharing cannot start, so skipping loses nothing.
            from utils.preferences import aload_global_conversation_settings
            settings = await aload_global_conversation_settings()
            if settings.get('proactiveVisionEnabled') is not True:
                return
            # Clear contextual-popup baseline: tracker survives sessions, so a user remaining in the same game/work
            # state across sessions would otherwise have no entry transition and miss this session's popup.
            self._activity_tracker.reset_context_prompt_baseline()
            await self._activity_tracker.get_snapshot()
        except Exception as e:
            logger.debug("[%s] 活动心跳 kick 失败: %s", self.lanlan_name, e)

    async def start_session(
        self, websocket: WebSocket, new=False, input_mode='audio', *,
        user_initiated=False, _allow_cross_mode_restart=True,
        handshake_override=_HANDSHAKE_OVERRIDE_UNSET,
        resource_optimization_override=_HANDSHAKE_OVERRIDE_UNSET,
        provider_preference_override=_HANDSHAKE_OVERRIDE_UNSET,
        request_id=None, _deadline=None,
    ):
        session_handshake_override = (
            getattr(self, '_independent_asr_handshake_override', None)
            if handshake_override is _HANDSHAKE_OVERRIDE_UNSET else handshake_override
        )
        session_resource_override = (
            getattr(self, '_voice_input_resource_optimization_handshake_override', None)
            if resource_optimization_override is _HANDSHAKE_OVERRIDE_UNSET
            else resource_optimization_override
        )
        session_provider_preference_handshake_override = (
            getattr(
                self,
                '_independent_asr_provider_preference_handshake_override',
                None,
            )
            if provider_preference_override is _HANDSHAKE_OVERRIDE_UNSET
            else provider_preference_override
        )
        deadline = _deadline or (
            asyncio.get_running_loop().time() + FRONTEND_START_SESSION_TIMEOUT_SECONDS
        )
        abandon_epoch = getattr(self, '_user_session_abandon_epoch', 0)
        if self._session_start_circuit_open:
            return
        if await self._start_session_handle_inflight(
            websocket, new, input_mode, user_initiated=user_initiated,
            _allow_cross_mode_restart=_allow_cross_mode_restart,
            request_id=request_id, handshake_override=session_handshake_override,
            resource_optimization_override=session_resource_override,
            provider_preference_override=session_provider_preference_handshake_override,
            deadline=deadline,
        ):
            return
        operation, token = self._claim_start_operation(
            websocket, request_id, input_mode, deadline, advance_generation=False,
        )
        diag_start = time.time()
        new_dialog_task = None
        try:
            # Reserve admission before the first handoff await so ingress
            # queues behind this request instead of starting another session.
            try:
                await self._wait_session_handoff(deadline)
            except Exception:
                await self._discard_start_reservation_inputs(operation)
                await self.send_session_failed(input_mode, request_id=request_id, also_notify=websocket)
                return
            if abandon_epoch != getattr(self, '_user_session_abandon_epoch', 0):
                await self._discard_start_reservation_inputs(operation)
                if request_id is not None:
                    await self.send_session_failed(input_mode, request_id=request_id, also_notify=websocket, allow_retired_operation=True)
                return
            self._check_start_operation()
            # The reservation must not take state ownership away from the
            # preceding retirement while its memory/input cleanup is running.
            self._session_generation = operation.generation
            async with asyncio.timeout_at(deadline):
                # Finish the previous conversation's state/memory boundary
                # before mutating config, input or output state for this one.
                await self._start_session_retire_previous()
                self._check_start_operation()
                self._start_session_seed_turn_language()
                self.session_closed_by_server = False
                self.last_audio_send_error_time = 0.0
                self._reset_proactive_gate()
                self._ensure_idle_session_reset_loop()
                self.last_user_activity_time = time.time()
                realtime_config, core_config = await self._start_session_prepare_runtime(
                    websocket, input_mode, new, diag_start,
                )
                await self._start_session_reset_stream_state(input_mode, realtime_config, core_config)
                mem_start = time.time()
                new_dialog_task = asyncio.create_task(self._start_session_fetch_new_dialog(
                    self.lanlan_name, self.memory_server_port,
                ))
                if new:
                    await self._start_session_reset_state_for_new()
                tts_result, llm_result = await asyncio.gather(
                    self._start_session_start_tts_if_needed(),
                    self._start_session_start_llm(input_mode, core_config, realtime_config,
                                                  new_dialog_task, mem_start),
                    return_exceptions=True,
                )
                self._check_start_operation()
                if isinstance(tts_result, asyncio.CancelledError):
                    raise tts_result
                if isinstance(tts_result, BaseException):
                    # TTS is an optional output path. Keep the LLM session
                    # usable and let the normal response/respawn path recover
                    # the worker instead of turning this into session_failed.
                    # The queue handler may have received a late __ready__
                    # while gather was still waiting for the LLM connection.
                    # Keep readiness owned by that handler and runtime.
                    logger.warning("TTS startup wait failed; preserving handler readiness: %s", tts_result)
                if isinstance(llm_result, BaseException):
                    raise llm_result
                if llm_result is _START_LLM_CONCURRENT_ABORTED:
                    return
                if self.session is None:
                    raise RuntimeError('Session not initialized')
                await self._start_session_activate(
                    input_mode, llm_result, diag_start, request_id=request_id,
                    handshake_override=session_handshake_override,
                    resource_optimization_override=session_resource_override,
                    provider_preference_override=session_provider_preference_handshake_override,
                )
                if (isinstance(tts_result, BaseException) and self.use_tts
                        and not (self._tts_runtime_is_current(self._snapshot_tts_runtime())
                                 and self.is_tts_pipeline_ready)):
                    # Publication fixes recovery ownership to the new session.
                    # Retired live workers retain capacity until physical exit.
                    self._tts_capacity_exhausted = False
                    self._schedule_tts_capacity_recovery()
        except asyncio.CancelledError:
            await self._discard_start_reservation_inputs(operation)
            if request_id is not None:
                await self.send_session_failed(input_mode, request_id=request_id, also_notify=websocket, allow_retired_operation=True)
            if operation.valid:
                self.request_end_session(by_server=True)
            # An accepted user end revoked this operation. The manager-owned
            # retirement drains its children and releases its resources.
            raise
        except Exception as exc:
            if operation.valid and self._start_operation is operation:
                await self._handle_session_start_exception(
                    exc, input_mode, diag_start,
                    request_id=request_id, also_notify=websocket,
                )
        finally:
            try:
                if new_dialog_task is not None and not new_dialog_task.done():
                    new_dialog_task.cancel()
                    # Child cancellation is a result; cancellation of the
                    # caller still propagates and always releases ownership.
                    await asyncio.gather(new_dialog_task, return_exceptions=True)
            finally:
                self._finish_start_operation(operation, token)

    async def _start_session_handle_inflight(
        self,
        websocket,
        new,
        input_mode,
        *,
        user_initiated,
        _allow_cross_mode_restart,
        request_id,
        handshake_override,
        resource_optimization_override,
        provider_preference_override,
        deadline=None,
    ):
        """Handle a start request that collides with an in-flight start_session.

        Returns True when the collision was fully handled here (same-mode dedup
        ack, cross-mode restart, or drop) and the caller must return without
        starting anything; False when no start is in flight.

        NOTE: the no-collision fast path must stay await-free so the caller's
        guard check -> increment sequence remains atomic on the event loop.
        """
        if self._starting_session_count <= 0:
            return False
        deadline = deadline or (asyncio.get_running_loop().time() + FRONTEND_START_SESSION_TIMEOUT_SECONDS)
        inflight_operation = getattr(self, '_start_operation', None)
        def inflight_is_current():
            return inflight_operation is None or (
                inflight_operation.valid
                and getattr(self, '_start_operation', None) is inflight_operation
            )
        async def fail_deduped_request():
            if request_id is not None:
                await self.send_session_failed(
                    input_mode, request_id=request_id, also_notify=websocket
                )
        # Another start_session, typically greeting auto-start, is in flight. Previously silently
        # returning left frontend start_session awaiting a session_started acknowledgment
        # that never arrived after deduplication, timing out and getting stuck after 15s. Race: user sends
        # before greeting appears; greeting owns in-flight startup, then its acknowledgment precedes
        # frontend waiting, leaving the frontend missing both paths.
        #
        # Resend acknowledgments only for **same-mode** deduplicated requests: in-flight startup uses its own mode.
        # Reusing session_started(text) across modes (greeting starts text while another request asks for audio)
        # would switch frontend UI to text and resolve its promise while the requested
        # audio session never started (CodeRabbit).
        if (self._starting_input_mode or input_mode) == input_mode:
            logger.warning("⚠️ Session正在启动中，等 in-flight 启动落定后给本请求补发 session_started")
            # Wait for in-flight startup to **settle itself**, returning _starting_session_count to zero.
            # Do not predicate on session_ready: it may retain True from the previous session
            # until in-flight start passes several awaits and resets it. The loop would skip
            # immediately, falsely reporting started before actual readiness (Codex P1).
            # Reserve time to deliver failure notifications for request_id-bearing frontend calls, preventing their
            # 15s timeout from sending end_session and closing a session that completes afterward.
            #
            # Snapshot this request's voice-lease identity on entry: waiting may take over ten seconds, and a third
            # audio start may claim the microphone meanwhile. Rerouting for that holder using **this displaced request's**
            # handshake would misconfigure the new owner's route. The new holder follows this path itself
            # with a matching snapshot, so skipping here loses nothing (Codex P2).
            _lease_at_request = getattr(self, "_voice_lease_connection_id", "")
            # Wall-clock baseline for frontend deadline remaining below. Do not use _waited:
            # it accumulates nominal 50ms intervals, while event-loop stalls or oversleep can put real
            # elapsed time seconds ahead, overestimating budget and permitting up to 12s of ASR
            # connection work, delivering the replacement acknowledgment after frontend timeout (Codex P2).
            _wait_started = time.monotonic()
            _waited = 0.0
            response_deadline = deadline - (0.25 if request_id is not None else 0)
            while self._starting_session_count > 0 and asyncio.get_running_loop().time() < response_deadline:
                await asyncio.sleep(0.05)
                _waited += 0.05
                if not inflight_is_current():
                    await fail_deduped_request()
                    return True
            # Resend session_started only when in-flight startup truly settled (count zero, loop exited
            # by settlement rather than timeout) and the session is active, idempotent with
            # in-flight startup's acknowledgment through the frontend's one-shot resolver. On timeout
            # (count >0, startup unfinished), restart may leave self.session/is_active True from
            # the previous session, making a resend falsely positive (Codex P1),
            # so never send started then. Target failure only to this request_id, without
            # canceling in-flight startup or failing other windows' requests.
            if self._starting_session_count == 0 and self.session and self.is_active:
                # Resent acknowledgments carry in-flight startup's routing decision (see
                # send_session_started); this path never reruns decisions itself. That decision may be stale
                # for this requester: claiming the voice lease invalidates the in-flight
                # ASR start, which returns ASR_START_STALE, leaving routing at a blocked
                # placeholder without status. Both windows then latch fail-closed and cannot reopen microphones
                # during this session. Rerun routing before acknowledging so the acknowledgment
                # carries this requester's actual route (see _rerun_route_for_deduped_start).
                await self._rerun_route_for_deduped_start(
                    input_mode,
                    lease_connection_id=_lease_at_request,
                    remaining_deadline_seconds=(
                        deadline - asyncio.get_running_loop().time()
                    ),
                    handshake_override=handshake_override,
                    resource_optimization_override=resource_optimization_override,
                    provider_preference_override=provider_preference_override,
                )
                if not inflight_is_current():
                    await fail_deduped_request()
                    return True
                # ``also_notify``: fail-closed rerouting revokes the lease, clearing
                # _voice_lease_connection_id and the voice socket, removing this requester
                # from every delivery surface (self.websocket may point to a newer window).
                # It would await its 15s timeout, whose end_session tears down the newly started
                # session. This request's ws is known; deliver directly too (Codex P2).
                #
                # Recheck microphone ownership **after** rerouting: provider connection awaits
                # up to 12s, during which a third window can claim the microphone
                # and settle a healthy route invisible to a pre-reroute snapshot (Codex P2).
                # An empty lease does not mean ownership changed: this fail-closed attempt revoked it itself;
                # routing is blocked and should be reported as such.
                _lease_now = getattr(self, "_voice_lease_connection_id", "")
                _lease_moved = bool(_lease_now) and _lease_now != _lease_at_request
                #
                # After ownership changes, acknowledgments must report blocked. Current _asr_route_mode
                # belongs to the **new holder** and may already be native/independent;
                # reporting that healthy route to this displaced requester would open its microphone,
                # while server voice identity belongs to the new holder and every later PCM
                # frame is discarded as stale, leaving another microphone speaking into nothing (Codex P2).
                # Reporting blocked closes the UI cleanly fail-closed and permits retry.
                await self.send_session_started(
                    input_mode,
                    request_id=request_id,
                    also_notify=websocket,
                    microphone_route_override="blocked" if _lease_moved else None,
                )
            else:
                await fail_deduped_request()
        elif user_initiated and _allow_cross_mode_restart:
            # Cross-mode collision with explicit user startup: typically a proactive/greeting
            # text auto-start is still in flight when the user requests voice conversation
            # (audio). Previously silently returning discarded explicit audio intent,
            # making the frontend wait 15s for an acknowledgment, then send end_session that also
            # tears down proactive text startup and prevents its speech playback.
            # Instead wait until in-flight startup settles (_starting_session_count zero),
            # then recursively start a session in this mode, replacing the newly created old-mode session through
            # _start_session_retire_previous cleanup. Do not reuse cross-mode
            # acknowledgments, which switch UI to the wrong mode as explained above.
            logger.warning("⚠️ Session正在启动中（跨模式），等 in-flight 落定后改起 %s 会话", input_mode)
            # Snapshot user-abandonment count, incremented only by frontend/user end_session
            # (see its beginning). True in-flight settlement clears count through its own finally,
            # leaving abandon epoch unchanged; frontend timeout end_session clears count
            # and increments abandon epoch. Restart only with count zero **and unchanged abandon epoch**,
            # distinguishing actual settlement from abandoned startup cleared by end_session, avoiding orphan
            # sessions after UI rejection (Codex P2). Never use _audio_stream_epoch:
            # failed startup's by_server cleanup also increments it, wrongly treating still-awaited
            # audio startup as abandoned and returning to the 15s empty wait (CodeRabbit).
            _abandon_epoch = self._user_session_abandon_epoch
            _waited = 0.0
            while self._starting_session_count > 0 and _waited < _core_facade.CROSS_MODE_RESTART_WAIT_SECONDS and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
                _waited += 0.05
            # Both connection conditions must hold before restarting:
            #   1) This request's ws parameter remains connected. Failed in-flight startup's
            #      _handle_session_start_exception -> cleanup() clears self.websocket to
            #      None while the browser connection remains open. Restart must still work here, avoiding a 15s audio wait
            #      (Codex P2). Inspect parameter ws connection state, not self.websocket; actual browser refresh/disconnection
            #      makes that parameter ws DISCONNECTED.
            #   2) self.websocket still equals this connection or None. A different new connection
            #      indicates reconnection; never restart with old ws and compete against the new connection (Codex P2 stale ws).
            try:
                _param_ws_connected = (
                    websocket is not None
                    and hasattr(websocket, 'client_state')
                    and websocket.client_state == websocket.client_state.CONNECTED
                )
            except Exception:  # noqa: BLE001
                _param_ws_connected = False
            _self_ws_ok = self.websocket is websocket or self.websocket is None
            if self._starting_session_count != 0:
                logger.warning("⚠️ 跨模式等待 in-flight 启动超时（%.1fs，留余量给重启），放弃改起 %s", _waited, input_mode)
            elif self._user_session_abandon_epoch != _abandon_epoch:
                logger.warning("⚠️ 跨模式等待期间用户主动结束了启动（已放弃），不再改起 %s", input_mode)
            elif not (_param_ws_connected and _self_ws_ok):
                logger.warning("⚠️ 跨模式等待期间 websocket 已断连/被新连接替换，不再改起 %s", input_mode)
            else:
                # In-flight startup settled cleanly with a live connection: recursively start the target mode.
                # Clear the circuit breaker first: explicit user requests should do so per websocket_router, but
                # _starting_session_count>0 prevented it earlier. If failed in-flight startup tripped it,
                # recursion silently returns at start_session's opening breaker check without session_failed, leaving frontend waiting
                # 15s. Clear here so restart either succeeds or reports normal failure (Codex P2).
                # Disable cross-mode restart on reentry (_allow_cross_mode_restart=False), limiting recursion to depth 1;
                # a second collision returns silently instead of recursing unboundedly (greptile P2). No await precedes the
                # _starting_session_count guard check, making count-zero detection through reentry atomic.
                self.reset_session_start_circuit()
                await self.start_session(
                    websocket,
                    new,
                    input_mode,
                    user_initiated=True,
                    _allow_cross_mode_restart=False,
                    request_id=request_id,
                    handshake_override=handshake_override,
                    resource_optimization_override=resource_optimization_override,
                    provider_preference_override=provider_preference_override,
                    _deadline=deadline,
                )
        else:
            logger.warning("⚠️ Session正在启动中（跨模式重复请求），忽略")
        return True

    def _start_session_seed_turn_language(self):
        """Seed ``user_language`` and the conversation-turn language once.

        Only seeds when ``user_language`` was never set; an existing session
        truth is preserved (late global-cache updates go through the
        ``refresh_global_language`` path instead).
        """
        # Previously every start_session overwrote user_language using get_global_language() to apply changes
        # immediately, but this replaced frontend i18n truth already delivered by ws greeting_check
        # (e.g. correct zh-CN when Steam=zh / system=en) with an incorrect global cache
        # value (en after losing the race), falling game/proactive/memory prompts
        # back to English. Seed only when user_language has never been set, preserving established
        # session truth. Late global-cache updates use the independent refresh_global_language
        # path (see main_routers/config_router.py:steam_language endpoint).
        topic_language_seed = None
        if not getattr(self, 'user_language', None):
            topic_language_seed = normalize_language_code(get_global_language_full(), format='full')
            # Seed the FULL code (e.g. 'zh-TW'), consistent with set_user_language's
            # format='full'; every consumer short-normalizes at its use site. Keeping
            # the Hant variant here is what lets resolve_dialog_slop_lang route a
            # Traditional-Chinese session to the 'zh-TW' slop rules instead of the
            # Simplified ones (a short 'zh' would hide the distinction entirely).
            self.user_language = topic_language_seed
            self._conversation_turn_language = topic_language_seed
        self._set_conversation_turn_language(
            self._conversation_turn_language
            or topic_language_seed
            or self.user_language
        )

    @start_phase
    async def _start_session_prepare_runtime(self, websocket, input_mode, new, diag_start):
        """Bind the websocket, reload config and voice routing, and notify the
        frontend that preparation started (silent window begins).

        Returns ``(realtime_config, core_config_snapshot)`` so later phases
        reuse the single config read.
        """
        # Reclaim leftover hot-swap resources so main + pending + new-main cannot exceed two sessions.
        await self._cleanup_pending_session_resources()
        self._check_start_operation()
        await self._reset_preparation_state(clear_main_cache=False)
        self._check_start_operation()

        logger.info(f"[语音会话诊断] 开始 start_session: input_mode={input_mode}, new={new}")
        logger.info(f"启动新session: input_mode={input_mode}, new={new}")
        self.websocket = websocket
        self.input_mode = input_mode
        self._reset_voice_echo_suppression_cache()

        # Start the activity-tracker heartbeat so game/entertainment/work transitions trigger contextual popups (see
        # _maybe_kick_activity_loop_for_context_prompt). Fire-and-forget without blocking session
        # startup; kick only with explicitly enabled vision (privacy off), otherwise early-return at no cost.
        self._fire_task(self._maybe_kick_activity_loop_for_context_prompt())

        # Immediately notify the frontend that preparation is underway and the silent period begins.
        await self.send_session_preparing(input_mode)
        self._check_start_operation()

        # The following lines pin routing for the entire session, so allow an in-flight region probe a final
        # settling window (startup warmup join may have expired during DNS resolution). Already-resolved cases return immediately
        # at zero normal-path cost; waiting is offloaded, not blocking the event loop.
        await self._config_manager.aensure_region_resolved()
        self._check_start_operation()

        # Reread configuration for hot reload.
        # Read core_api_type from realtime configuration, automatically using local for custom realtime APIs.
        # Combine two synchronous I/O operations: read core_config.json once and resolve realtime from the same snapshot.
        core_config_snapshot = await self._config_manager.aget_core_config()
        self._check_start_operation()
        realtime_config = await self._config_manager.aget_model_api_config(
            'realtime', core_config=core_config_snapshot
        )
        self._check_start_operation()
        self.core_api_type = realtime_config.get('api_type', '') or core_config_snapshot.get('CORE_API_TYPE', '')
        self.audio_api_key = core_config_snapshot['AUDIO_API_KEY']

        # Clear invalid voice_id before each session start to avoid stale character voices causing startup failure.
        try:
            cleaned_count, legacy_names = await asyncio.to_thread(self._config_manager.cleanup_invalid_voice_ids)
            self._check_start_operation()
            if cleaned_count > 0:
                logger.info(f"🧹 start_session 前已清理 {cleaned_count} 个无效 voice_id")
            self._enqueue_voice_migration_notice(legacy_names)
        except Exception as e:
            logger.warning(f"⚠️ start_session 清理无效 voice_id 失败，继续启动会话: {e}")

        # Default YUI-card free-voice binding deliberately waits for region resolution, because wrong bindings cannot be corrected.
        # It previously ran only when saving core configuration or clearing voice IDs. Switching to free
        # API invokes it immediately after saving, while probing has just started and is inevitably unresolved, so it defers;
        # no later path retries, making the next pass nonexistent. This is that pass, immediately following
        # aensure_region_resolved's best-effort resolution. Run every session idempotently (writes only for default
        # YUI cards with empty voice_id), before reading voice_id below, so binding applies immediately.
        try:
            await ensure_default_yui_voice_for_free_api(self._config_manager, core_config_snapshot)
            self._check_start_operation()
        except Exception as e:
            logger.warning(f"⚠️ start_session 绑定默认 YUI 音色失败，继续启动会话: {e}")

        # Reread character configuration for latest voice_id, supporting voice hot updates after character switching.
        _, _, _, self.lanlan_basic_config, _, _, _, _, _ = await self._config_manager.aget_character_data()
        self._check_start_operation()
        old_voice_id = self.voice_id
        self._apply_voice_id_for_route()

        # Without character voice_id, try custom API's TTS_VOICE_ID fallback.
        if not self.voice_id:
            # core_config is immutable during one start_session (changes use save_core_api -> end_session); reuse the initial snapshot.
            tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
            # Filter GPT-SoVITS-disabled placeholders (format: __gptsovits_disabled__|...).
            if (
                tts_voice_id
                and not is_gsv_disabled_voice_id(tts_voice_id)
                and (
                    _as_bool(core_config_snapshot.get('ENABLE_CUSTOM_API'), False)
                    or core_config_snapshot.get('GPTSOVITS_ENABLED')
                )
            ):
                self.voice_id = tts_voice_id
                logger.info(f"🔄 使用自定义TTS回退音色: '{self.voice_id}'")
                self._is_free_preset_voice = False

        if old_voice_id != self.voice_id:
            logger.info(f"🔄 voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")
        if self._is_free_preset_voice:
            logger.info(f"🆓 当前使用免费预设音色: '{self.voice_id}'")

        # Log model configuration directly from configuration, avoiding unnecessary instance variables.
        _realtime_model = realtime_config.get('model', '')
        _conversation_model = (await self._config_manager.aget_model_api_config(
            'conversation', core_config=core_config_snapshot
        )).get('model', '')
        self._check_start_operation()
        _vision_model = (await self._config_manager.aget_model_api_config(
            'vision', core_config=core_config_snapshot
        )).get('model', '')
        self._check_start_operation()
        logger.info(f"📌 已重新加载配置: core_api={self.core_api_type}, realtime_model={_realtime_model}, text_model={_conversation_model}, vision_model={_vision_model}, voice_id={self.voice_id}")
        logger.info(f"[语音会话诊断] 配置加载完成 (耗时: {time.time() - diag_start:.2f}秒)")
        return realtime_config, core_config_snapshot

    @start_phase
    async def _start_session_reset_stream_state(self, input_mode, realtime_config,
                                                core_config_snapshot):
        """Reset the TTS/input caches for the new session and resolve
        ``use_tts``."""
        # Reset TTS buffer state. If a living TTS worker was already confirmed ready,
        # clear only queued text, retaining readiness: the existing worker will not send
        # another __ready__ for a new text session, or post-game one-shot text
        # would remain in pending chunks forever.
        preserve_tts_ready = self._can_preserve_tts_ready_for_session_start()
        async with self.tts_cache_lock:
            self._check_start_operation()
            self.tts_ready = preserve_tts_ready
            self.tts_pending_chunks.clear()
            # Session replacement invalidates the previous utterance replay ledger.
            # New sessions must not inherit previous TTS playback text or end signals.
            self._reset_tts_replay_state()

        # Reset input-buffer state.
        async with self.input_cache_lock:
            self._check_start_operation()
            self.session_ready = False
            # Do not clear pending_input_data, which may already contain buffered data.

        self.use_tts = self._resolve_session_use_tts(
            input_mode,
            realtime_config,
            core_config_snapshot,
        )

    @start_phase
    async def _start_session_retire_previous(self):
        if self.session is not None or self.is_active:
            self.request_end_session(by_server=True, reset_starting_count=False,
                                     preserve_pending_input=True)
            record = self._session_retirements[-1]
            await record.handoff_finished.wait()
            if record.handoff_error is not None and not record.handoff_safe.is_set():
                raise RuntimeError("Session handoff failed") from record.handoff_error
            if record.memory_completion is not None:
                await asyncio.shield(record.memory_completion)

    async def _start_session_start_tts_if_needed(self):
        """Wait for an owned, healthy runtime within the shared startup budget."""
        self._check_start_operation()
        loop = asyncio.get_running_loop()
        start_deadline = self._current_start_deadline()
        deadline = min(start_deadline - 0.1, loop.time() + 5.0)
        if not self.use_tts:
            runtime = self._snapshot_tts_runtime()
            if runtime is not None and not runtime.retired:
                self._retire_tts_runtime(runtime)
                await self._stop_tts_response_handler(deadline=deadline)
                self._check_start_operation()
            return True
        # TTS is a recoverable output dependency. Reserve a small tail of the
        # shared startup budget for LLM publication, and cap a slow TTS
        # provider so it cannot turn a healthy session into session_failed.
        if deadline <= loop.time():
            return False
        runtime = self._snapshot_tts_runtime()
        if (runtime is not None and not runtime.retired
                and getattr(self, "_tts_runtime_key", None) != self._build_tts_runtime_key()):
            self._retire_tts_runtime(runtime)
        await self.ensure_tts_pipeline_alive(deadline=deadline)
        self._check_start_operation()
        # The handler is the sole response queue consumer, including during
        # initialization. Fallback transfers that handler to its fresh runtime.
        while True:
            self._check_start_operation()
            runtime = self._snapshot_tts_runtime()
            ready = False
            if runtime is not None and not self._tts_runtime_is_current(runtime):
                self._check_start_operation()
                fallback_task = runtime.fallback_task
                if not (
                    fallback_task is not None
                    and fallback_task is self.tts_handler_task
                    and not fallback_task.done()
                    and not fallback_task.cancelling()
                ):
                    raise RuntimeError("TTS runtime retired during startup")
                # An owned fallback may wait for this worker's physical exit.
                # Use the same bounded polling below until its successor is ready.
            else:
                async with self.tts_cache_lock:
                    self._check_start_operation()
                    if not self._tts_runtime_is_current(runtime):
                        continue
                    ready = bool(self.tts_ready and self.tts_thread and self.tts_thread.is_alive())
            if ready:
                await self._flush_tts_pending_chunks()
                self._check_start_operation()
                if not self._tts_runtime_is_current(runtime):
                    continue
                return True
            if getattr(self, "_tts_capacity_exhausted", False):
                raise RuntimeError("TTS runtime capacity exhausted")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("TTS runtime was not ready before startup deadline")
            await asyncio.sleep(min(0.02, remaining))

    def _new_dialog_request_kwargs(self) -> dict:
        """Share explicit-locale provenance across initial and hot-swap bootstrap."""
        request_kwargs = {"timeout": 5.0}
        if getattr(self, "_user_language_explicit", False):
            request_kwargs["params"] = {"language": self.user_language}
        elif getattr(self, "_conversation_render_language", None):
            request_kwargs["params"] = {
                "render_language": self._conversation_render_language,
            }
        return request_kwargs

    async def _start_session_fetch_new_dialog(self, lanlan_name, port):
        """Independent task: fetch the /new_dialog response. Kicked off before the gather,
        deliberately avoiding the GIL contention window during TTS worker startup."""
        from utils.internal_http_client import get_internal_http_client
        _mem_client = get_internal_http_client()
        try:
            resp = await _mem_client.get(
                f"http://127.0.0.1:{port}/new_dialog/{lanlan_name}",
                **self._new_dialog_request_kwargs(),
            )
        except httpx.ConnectError:
            raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {port})")
        except httpx.TimeoutException:
            raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {port})")
        except Exception as e:
            raise ConnectionError(f"❌ 记忆服务连接失败: {e} (端口 {port})")
        if not resp.is_success:
            raise ConnectionError(f"❌ 记忆服务返回非2xx状态 {resp.status_code}: {resp.text[:200]}")
        return resp.text

    def _create_offline_vlm_client(
        self,
        *,
        conversation_config: dict,
        vision_config: dict,
        tool_definitions: list,
        max_response_length: int,
        external_tts_enabled: bool,
    ) -> OmniOfflineClient:
        """Build the shared Offline/VLM client used by starts and promotions."""

        session = OmniOfflineClient(
            base_url=conversation_config['base_url'],
            api_key=conversation_config['api_key'],
            model=conversation_config['model'],
            vision_model=vision_config['model'],
            vision_base_url=vision_config['base_url'],
            vision_api_key=vision_config['api_key'],
            provider_type=conversation_config.get('provider_type'),
            vision_provider_type=vision_config.get('provider_type'),
            on_text_delta=self.handle_text_data,
            on_input_transcript=self.handle_text_input_transcript,
            on_output_transcript=self.handle_output_transcript,
            on_connection_error=self.handle_connection_error,
            on_response_done=self.handle_response_complete,
            on_repetition_detected=self.handle_repetition_detected,
            on_response_discarded=self.handle_response_discarded,
            on_status_message=self.send_status,
            max_response_length=max_response_length,
            lanlan_name=self.lanlan_name,
            master_name=self.master_name,
            user_language_provider=lambda: self.user_language,
            on_tool_call=None,
            tool_definitions=tool_definitions,
            enable_long_response_summary=external_tts_enabled,
        )
        session.on_proactive_done = self.handle_proactive_complete
        # A reply whose completion is skipped is closed by whoever took it
        # over: a displacing user reply reports it here; the owed wrap-up is
        # paid once the session goes idle (the interrupted reply's own
        # completion would have run it).
        session.on_response_displaced = self._close_displaced_offline_turn
        session.on_idle = self._on_offline_session_idle
        session.on_thinking_active = self._make_thinking_active_callback(session)
        # Match both realtime construction sites: bind the handle to this client itself, rather than
        # construction-time self.session. Before promotion, a handoff candidate is neither
        # self.session nor pending_session and _sync_tools_to_active_session
        # cannot find it; it must recognize itself from creation.
        session.on_tool_call = self._make_tool_call_handler(session)
        return session

    async def _create_offline_vlm_handoff_candidate(
        self,
        *,
        cached_turns: list[dict],
        previous_core_url: str,
    ):
        """Connect an Offline VLM without mutating active-session ownership."""

        await self._config_manager.aensure_region_resolved()
        core_config = await self._config_manager.aget_core_config()
        current_core_url = str(core_config.get('CORE_URL') or '')
        if str(previous_core_url or '') != current_core_url:
            logger.warning(
                "[GeoIP] Offline VLM handoff: 区域结论在 Realtime 会话与候选创建之间发生变化"
                "（%s → %s），本场音色可能落到服务端默认",
                previous_core_url,
                current_core_url,
            )
            self._drop_free_voice_on_route_flip(
                previous_core_url,
                current_core_url,
            )
        conversation_config, vision_config = await asyncio.gather(
            self._config_manager.aget_model_api_config(
                'conversation', core_config=core_config,
            ),
            self._config_manager.aget_model_api_config(
                'vision', core_config=core_config,
            ),
        )
        self._register_builtin_tools()
        candidate = self._create_offline_vlm_client(
            conversation_config=conversation_config,
            vision_config=vision_config,
            tool_definitions=self.tool_registry.all(),
            max_response_length=self._get_text_guard_max_length(),
            external_tts_enabled=not core_config.get('DISABLE_TTS', False),
        )
        next_context = self._snapshot_next_session_context_messages()
        try:
            initial_prompt = await self._build_initial_prompt()
            initial_prompt += await self._start_session_fetch_new_dialog(
                self.lanlan_name,
                self.memory_server_port,
            )
            # One call for both slices: a screen chain split across them is
            # judged as one run (see _convert_cache_to_str).
            initial_prompt += self._convert_cache_to_str(
                list(next_context) + list(cached_turns)
            )
            self._bind_session_lifecycle_callbacks(candidate)
            await self._connect_owned_session(candidate, initial_prompt, native_audio=False)
        except BaseException:
            try:
                await self._close_owned_session(candidate)
            except Exception:
                pass
            raise
        return candidate, len(next_context)

    async def _handoff_to_offline_vlm_and_submit(
        self,
        turn,
        *,
        expected_session,
        prepared_session,
        operation_is_current,
        cached_turns_before_final: list[dict],
        visual_still_owned=None,
        turn_context=None,
    ) -> bool:
        """Two-phase Realtime -> Offline VLM promotion for one raw-image turn.

        Candidate construction is side-effect free for the active session. The
        old Realtime session is retired only after the Offline VLM connected and
        the independent-ASR route still owns the same turn.
        """

        lock = getattr(self, '_multimodal_handoff_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            self._multimodal_handoff_lock = lock
        async with lock:
            # A normal hot-swap can promote an Offline session after the ASR
            # dispatcher releases its barrier but before this handoff lock is
            # entered. Re-read behind the same swap barrier and prepare that
            # exact session before using the fast path; a naked type check here
            # would race a second promotion and submit into a retired client.
            session_swap_lock = self._core_voice_session_swap_lock
            try:
                await asyncio.wait_for(
                    session_swap_lock.acquire(),
                    timeout=self._core_voice_session_swap_barrier_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    '[%s] Offline VLM handoff timed out waiting for entry barrier',
                    self.lanlan_name,
                )
                return False
            try:
                current = getattr(self, 'session', None)
                if isinstance(current, OmniOfflineClient):
                    # Omit this argument without turn-attached context, preserving the existing call.
                    context_kwargs = (
                        {'turn_context': turn_context} if turn_context is not None else {}
                    )
                    if not operation_is_current():
                        return False
                    # The session captured at ASR prepare already owns the turn:
                    # repeating interruption/handle_new_message would rotate its
                    # speech id twice. Only a hot-swap replacement needs the
                    # preparation that could not run at the original boundary.
                    if current is not prepared_session:
                        # Hot-swapped sessions are excluded: retrieval excludes the old session's resident knowledge, which does not apply here.
                        context_kwargs = {}
                        prepare = getattr(
                            current,
                            'prepare_external_voice_turn',
                            None,
                        )
                        if callable(prepare):
                            reconnected = await prepare(turn_id=turn.turn_id)
                            if reconnected is True and not await (
                                self._restart_message_handler_after_session_reconnect(
                                    current
                                )
                            ):
                                return False
                        else:
                            # Close the offline reply this turn interrupted
                            # before handle_new_message clears its text; its
                            # wrap-up is owed (see _interrupt_offline_reply)
                            # and held by this voice turn until it ends (the
                            # dispatch's _abandon_core_voice_turn).
                            self._hold_owed_wrap_up_for_voice_turn(turn.turn_id)
                            await self._interrupt_offline_reply(current)
                        if (
                            not operation_is_current()
                            or self.session is not current
                        ):
                            return False
                        await self.handle_new_message()
                        if (
                            not operation_is_current()
                            or self.session is not current
                        ):
                            return False
                    submit = getattr(current, 'submit_multimodal_turn', None)
                    if not callable(submit):
                        return False
                    self.response_backend = 'offline_vlm'
                    try:
                        await self.ensure_tts_pipeline_alive()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            '[%s] Offline VLM submit TTS retry failed: %s',
                            self.lanlan_name,
                            exc,
                        )
                        return False
                    if (
                        not operation_is_current()
                        or self.session is not current
                    ):
                        return False
                    if visual_still_owned is not None and not visual_still_owned():
                        # Apply the same criterion as the slow path below: this fast path also crosses
                        # prepare_external_voice_turn / handle_interruption /
                        # several awaits in handle_new_message / ensure_tts_pipeline_alive,
                        # any of which permits later speech to claim frames. Missing frames degrades only
                        # to text; still deliver the message.
                        submit_text = getattr(
                            current,
                            'submit_external_voice_turn',
                            None,
                        )
                        if not callable(submit_text):
                            # This branch already returns False if no submission method exists,
                            # unlike the slow path's exception; preserve that behavior.
                            return False
                        logger.info(
                            '[%s] Offline submit lost frame ownership mid-flight; '
                            'submitting turn %s without its frames',
                            self.lanlan_name,
                            turn.turn_id,
                        )
                        delivered = await submit_text(
                            turn.transcript,
                            turn_id=turn.turn_id,
                            **context_kwargs,
                        )
                        return delivered is not False
                    delivered = await submit(
                        turn.transcript,
                        turn.images,
                        turn_id=turn.turn_id,
                        # Capture channel frozen alongside these frames. Without it, offline handling labels
                        # both screen and camera frames as user.
                        source=turn.source,
                        # Turn-attached context: the offline session handles this turn on the fast path,
                        # so retrieval excludes its own resident knowledge.
                        **context_kwargs,
                    )
                    return delivered is not False
                if current is None or not operation_is_current():
                    return False
                if current is not expected_session:
                    # A same-conversation normal hot-swap may have promoted a
                    # newer Realtime session after the dispatcher selected its
                    # handoff source. The multimodal user turn has priority:
                    # prepare the replacement behind the barrier and continue
                    # candidate construction from that exact live identity.
                    prepare = getattr(
                        current,
                        'prepare_external_voice_turn',
                        None,
                    )
                    if callable(prepare):
                        reconnected = await prepare(turn_id=turn.turn_id)
                        if reconnected is True and not await (
                            self._restart_message_handler_after_session_reconnect(
                                current
                            )
                        ):
                            return False
                    else:
                        # Through the helper, never a bare handle_interruption():
                        # an interruption that claims a finished reply's
                        # completion must also close that reply's turn. No
                        # wrap-up is scheduled here: this user turn has not
                        # marked its input yet, and an Offline session never
                        # reaches this branch (the fast path above returns).
                        await self._interrupt_offline_reply(current)
                    if (
                        not operation_is_current()
                        or self.session is not current
                    ):
                        return False
                    expected_session = current
            finally:
                session_swap_lock.release()

            # A user-owned multimodal final supersedes speculative archival
            # preparation. Cancel and close that candidate before building a
            # dedicated local replacement; never borrow pending_session's slot.
            await self._reset_preparation_state(clear_main_cache=False)
            await self._cleanup_pending_session_resources()
            if (
                not operation_is_current()
                or self.session is not expected_session
            ):
                return False

            try:
                candidate, next_context_count = (
                    await self._create_offline_vlm_handoff_candidate(
                        cached_turns=cached_turns_before_final,
                        previous_core_url=str(
                            getattr(expected_session, 'base_url', '') or ''
                        ),
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    '[%s] Offline VLM handoff preparation failed: %s',
                    self.lanlan_name,
                    exc,
                )
                return False

            promoted = False
            old_listener = self.message_handler_task
            listener_cancel_timed_out = False
            listener_timeout_fail_closed = False
            listener_cancelled_for_handoff = False
            ownership_lost_after_close = False
            try:
                if (
                    not operation_is_current()
                    or self.session is not expected_session
                ):
                    return False
                try:
                    await asyncio.wait_for(
                        session_swap_lock.acquire(),
                        timeout=self._core_voice_session_swap_barrier_timeout_s,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        '[%s] Offline VLM handoff timed out waiting for promotion barrier',
                        self.lanlan_name,
                    )
                    return False
                try:
                    if (
                        not operation_is_current()
                        or self.session is not expected_session
                    ):
                        return False
                    if old_listener and not old_listener.done():
                        old_listener.cancel()
                        listener_cancelled_for_handoff = True
                        try:
                            # Deliberately avoid wait_for: on timeout it cancels the target and **continues waiting**
                            # for cancellation. If handle_messages delays or swallows CancelledError
                            # while stuck in recv(), this never raises TimeoutError.
                            # _core_voice_session_swap_lock is still held, so a hang blocks all later
                            # voice turns and hot swaps, and the fail-closed
                            # branch never runs. asyncio.wait waits only until deadline, not cancellation completion.
                            done, _pending = await asyncio.wait(
                                {old_listener},
                                timeout=getattr(
                                    self,
                                    "_core_voice_listener_cancel_timeout_s",
                                    2.0,
                                ),
                            )
                            if not done:
                                raise asyncio.TimeoutError
                            # Target finished: retrieve its exception, preserving wait_for's same
                            # propagation behavior (cancellation echoes still use the branch below).
                            old_listener.result()
                        except asyncio.CancelledError:
                            current_task = asyncio.current_task()
                            if current_task is not None and current_task.cancelling():
                                raise
                        except asyncio.TimeoutError:
                            logger.error(
                                '[%s] Offline VLM handoff: old listener cancellation timed out',
                                self.lanlan_name,
                            )
                            listener_cancel_timed_out = True
                            # Once cancellation has timed out, the old listener
                            # may still own recv() and cannot be closed in place.
                            # Fail-close only if both active ownership refs still
                            # identify the pair observed before promotion; a
                            # concurrent winner must never be cleared here.
                            async with self.lock:
                                listener_timeout_fail_closed = bool(
                                    self.session is expected_session
                                    and self.message_handler_task is old_listener
                                )
                                if listener_timeout_fail_closed:
                                    self.session = None
                                    self.message_handler_task = None
                                    self.is_active = False
                                    self.session_ready = False

                            stuck_listener = old_listener
                            orphan_session = expected_session

                            async def _reap_handoff_session_after_listener_exit():
                                # Close the session first, then wait boundedly for the listener. This path
                                # means the listener swallowed cancellation and cannot stop; waiting first
                                # leaves the WebSocket open forever, allowing the runaway listener
                                # to keep invoking callbacks after Core declares the session ended.
                                # OmniRealtimeClient.close() synchronously detaches the socket first,
                                # so starting it immediately stops the damage.
                                try:
                                    await self._close_owned_session(orphan_session)
                                except Exception as reap_err:
                                    logger.debug(
                                        '[%s] Offline VLM handoff: orphan close failed: %s',
                                        self.lanlan_name,
                                        reap_err,
                                    )
                                try:
                                    await asyncio.wait(
                                        {stuck_listener},
                                        timeout=getattr(
                                            self,
                                            "_core_voice_listener_cancel_timeout_s",
                                            2.0,
                                        ),
                                    )
                                except (asyncio.CancelledError, Exception):
                                    pass

                            reaper = asyncio.create_task(
                                _reap_handoff_session_after_listener_exit()
                            )
                            _ORPHAN_SESSION_REAPER_TASKS.add(reaper)
                            reaper.add_done_callback(
                                _ORPHAN_SESSION_REAPER_TASKS.discard
                            )
                        except Exception as exc:
                            logger.debug(
                                '[%s] Offline VLM handoff: old listener exited with error: %s',
                                self.lanlan_name,
                                exc,
                            )
                    if not listener_cancel_timed_out:
                        if (
                            not operation_is_current()
                            or self.session is not expected_session
                        ):
                            # Cancellation retired the receive task, but the
                            # Realtime session itself is still healthy. If this
                            # handoff merely lost turn ownership, restore Core's
                            # listener before abandoning the candidate.
                            if (
                                listener_cancelled_for_handoff
                                and self.session is expected_session
                            ):
                                await self._restart_message_handler_after_session_reconnect(
                                    expected_session
                                )
                            return False
                        try:
                            await self._close_owned_session(expected_session)
                        except Exception as exc:
                            logger.warning(
                                '[%s] Offline VLM handoff: old session close failed: %s',
                                self.lanlan_name,
                                exc,
                            )
                        async with self.lock:
                            if self.session is not expected_session:
                                return False
                            if not operation_is_current():
                                # The old session has already crossed its
                                # destructive close boundary, so neither it nor
                                # the stale candidate may remain active.
                                self.session = None
                                self.message_handler_task = None
                                self.is_active = False
                                self.session_ready = False
                                ownership_lost_after_close = True
                            else:
                                self.session = candidate
                                promoted = True
                finally:
                    session_swap_lock.release()

                if listener_cancel_timed_out:
                    if listener_timeout_fail_closed:
                        await self._close_independent_asr(
                            next_route_mode="blocked",
                        )
                        await self.send_session_ended_by_server()
                    return False
                if ownership_lost_after_close:
                    await self._close_independent_asr(
                        next_route_mode="blocked",
                    )
                    await self.send_session_ended_by_server()
                    return False

                self.response_backend = 'offline_vlm'
                # Offline emits text even though microphone ownership remains
                # audio/independent-ASR, so its response always needs the
                # external TTS pipeline.
                self.use_tts = True
                self.message_handler_task = asyncio.create_task(
                    candidate.handle_messages()
                )
                # Promotion is already committed. Settle the context ownership
                # before any later initialization await so a fail-closed turn
                # cannot leave the promoted session paired with stale cache.
                self._consume_next_session_context_messages(next_context_count)
                self.message_cache_for_new_session = []
                self.is_preparing_new_session = False
                self.summary_triggered_time = None
                try:
                    await self.ensure_tts_pipeline_alive()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Keep the promoted Offline session coherent and listened
                    # to, but do not submit a reply that cannot own its TTS
                    # lifecycle. A later turn may retry the TTS pipeline.
                    logger.warning(
                        '[%s] Offline VLM handoff TTS initialization failed: %s',
                        self.lanlan_name,
                        exc,
                    )
                    return False
                try:
                    await self._sync_tools_to_active_session()
                except Exception as exc:
                    logger.warning(
                        '[%s] Offline VLM handoff tool sync failed: %s',
                        self.lanlan_name,
                        exc,
                    )
                # New speech can invalidate the frozen image owner while TTS
                # or tool synchronization is awaiting. The candidate remains
                # the active answer backend, but the superseded turn must never
                # reach it.
                if (
                    not operation_is_current()
                    or self.session is not candidate
                ):
                    return False
                if visual_still_owned is not None and not visual_still_owned():
                    # Ownership was lost during the long sequence of candidate connection / promotion / TTS pipeline startup / tool synchronization
                    # awaits, the longest window in this chain;
                    # operation_is_current covers route identity only, not frame ownership.
                    #
                    # Only **images** were lost, not speech: promote normally because routing already requires
                    # offline, sending this turn as text only. Returning False would make callers report
                    # ASR_MULTIMODAL_TURN_FAILED and lose the user's entire utterance.
                    logger.info(
                        '[%s] Offline VLM handoff lost frame ownership mid-flight; '
                        'submitting turn %s without its frames',
                        self.lanlan_name,
                        turn.turn_id,
                    )
                    submit_text = getattr(
                        candidate,
                        'submit_external_voice_turn',
                        None,
                    )
                    if not callable(submit_text):
                        raise RuntimeError('OFFLINE_EXTERNAL_SUBMIT_UNAVAILABLE')
                    delivered = await submit_text(
                        turn.transcript,
                        turn_id=turn.turn_id,
                    )
                    return delivered is not False
                submit = getattr(candidate, 'submit_multimodal_turn', None)
                if not callable(submit):
                    raise RuntimeError('OFFLINE_MULTIMODAL_SUBMIT_UNAVAILABLE')
                delivered = await submit(
                    turn.transcript,
                    turn.images,
                    turn_id=turn.turn_id,
                    # As above: frame-bus channel labels travel with frames.
                    source=turn.source,
                )
                return delivered is not False
            finally:
                if not promoted:
                    try:
                        await self._close_owned_session(candidate)
                    except Exception:
                        pass

    @start_phase
    async def _start_session_start_llm(self, input_mode, core_config_snapshot,
                                       prepared_realtime_config,
                                       new_dialog_task, mem_start):
        """Asynchronously create and connect the LLM Session.

        Uses connect-then-assign: a local new_session is created and connected
        first.  Only after connect() succeeds is it promoted to self.session.
        On failure the half-initialised session is closed and an exception raised.

        Returns the number of next-session context messages folded into the
        start prompt (consumed after activation), or
        ``_START_LLM_CONCURRENT_ABORTED`` when the CAS loses to a concurrent
        start_session.
        """
        next_context_count = 0
        # Strong CAS semantics: assign only when self.session is None (start_session cleared it)
        # or already our new_session. Every other state means losing concurrently;
        # close this new_session to avoid overwriting the winner and creating an orphan.
        #
        # Counterexample: comparing only the entry snapshot lets winner B occupy self.session;
        # loser A returns and releases its guard in finally; third contender C snapshots B,
        # then passes reflexive B==B CAS and overwrites B, creating another orphan.
        guard_max_length = self._get_text_guard_max_length()
        _lang = normalize_language_code(self.user_language, format='short')
        initial_prompt = await self._build_initial_prompt()
        self._check_start_operation()
        next_session_context_messages = self._snapshot_next_session_context_messages()
        start_prompt_context_owner = object()
        self._mark_pending_context_appends_delivered_in_start_prompt(
            next_session_context_messages,
            owner=start_prompt_context_owner,
        )

        # Await the /new_dialog request issued earlier.
        try:
            _nd_text = await new_dialog_task
            self._check_start_operation()
            initial_prompt += (
                _nd_text
                + self._convert_cache_to_str(next_session_context_messages)
                # Choose wording for the session's actual mode: text mode previously announced continuing
                # by voice too, because input_mode branching happened only during client creation
                # below while this message was assembled unconditionally.
                + get_context_summary_ready(_lang, input_mode=input_mode).format(
                    name=self.lanlan_name, master=self.master_name,
                )
            )
            logger.info(f"[语音会话诊断] 记忆上下文获取完成 (耗时: {time.time() - mem_start:.2f}秒)")
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"❌ 记忆服务连接失败: {e} (端口 {self.memory_server_port})")

        logger.info(f"🤖 开始创建 LLM Session (input_mode={input_mode})")
        logger.info("[语音会话诊断] 开始创建 LLM 连接 (realtime/text)...")
        _llm_create_start = time.time()

        # Create into a LOCAL variable — not self.session yet
        new_session = None
        # Before snapshotting, align built-in tool descriptions with current
        # user_language. At __init__, user_language may still have been None,
        # producing English placeholders. It is now settled, so reregister to replace
        # old registry descriptions before the snapshot below reads them.
        self._register_builtin_tools()
        # Snapshot the registry once per session create so the
        # tools list seen by the wire matches what the registry
        # held at connect time. ``set_tools`` keeps it live for
        # later mutations.
        _initial_tool_defs = self.tool_registry.all()
        logger.info(
            "[%s] session tools snapshot (input_mode=%s client=%s): %s",
            self.lanlan_name,
            input_mode,
            "offline" if input_mode == 'text' else "realtime",
            [t.name for t in _initial_tool_defs],
        )

        # Both branches below reread configuration now and freeze base_url into their clients (text's
        # OmniOfflineClient / realtime connection configuration). prepare_runtime resolved once, but
        # memory retrieval above can await seconds; if resolution used Steam fallback or timed out
        # fail-open, authoritative IP results may arrive meanwhile. Give another settling window,
        # matching the frozen snapshot to the final result. Already-resolved paths cost nothing.
        await self._config_manager.aensure_region_resolved()
        self._check_start_operation()

        if input_mode == 'text':
            # Do not reuse prepare_runtime snapshots: /new_dialog memory retrieval above may take
            # seconds, during which users can save new credentials via /core_api. Read a **fresh snapshot** before
            # constructing clients, resolving conversation and vision from it; separate reads could straddle
            # a save and produce torn old-conversation/new-vision configuration.
            _fresh_core_config = await self._config_manager.aget_core_config()
            self._check_start_operation()
            # Region may flip during memory retrieval (prepare used Steam fallback/timeout and authoritative IP arrived
            # later), leaving prepare's voice_id from the old region's catalog
            # while this snapshot routes to the new region. Realtime has a snapshot-based pairing gate
            # withholding mismatches for server defaults; text TTS directly uses self.voice_id,
            # so _drop_free_voice_on_route_flip applies the same fail-safe, clearing free voices
            # to server defaults this session and resolving the new region normally next session.
            if (str(core_config_snapshot.get('CORE_URL') or '')
                    != str(_fresh_core_config.get('CORE_URL') or '')):
                logger.warning(
                    "[GeoIP] 区域结论在会话准备与连接创建之间发生变化"
                    "（%s → %s），本场音色可能落到服务端默认",
                    core_config_snapshot.get('CORE_URL'), _fresh_core_config.get('CORE_URL'),
                )
                self._drop_free_voice_on_route_flip(
                    core_config_snapshot.get('CORE_URL'), _fresh_core_config.get('CORE_URL'),
                )
            conversation_config = await self._config_manager.aget_model_api_config(
                'conversation', core_config=_fresh_core_config
            )
            self._check_start_operation()
            vision_config = await self._config_manager.aget_model_api_config(
                'vision', core_config=_fresh_core_config
            )
            self._check_start_operation()
            new_session = self._create_offline_vlm_client(
                conversation_config=conversation_config,
                vision_config=vision_config,
                tool_definitions=_initial_tool_defs,
                max_response_length=guard_max_length,
                external_tts_enabled=(
                    self.use_tts
                    and not core_config_snapshot.get('DISABLE_TTS', False)
                ),
            )
        else:
            # As above: reread after memory retrieval awaits rather than reuse prepare_runtime's snapshot.
            _prev_realtime_base = str((prepared_realtime_config or {}).get('base_url') or '')
            realtime_config = await self._config_manager.aget_model_api_config('realtime')
            self._check_start_operation()
            # Region-flip diagnostics mirror text; realtime's voice pairing gate uses this snapshot's
            # base_url, withholding mismatches for server defaults (fail-safe).
            if _prev_realtime_base != str(realtime_config.get('base_url') or ''):
                logger.warning(
                    "[GeoIP] 区域结论在会话准备与连接创建之间发生变化"
                    "（%s → %s），本场音色可能落到服务端默认",
                    _prev_realtime_base, realtime_config.get('base_url'),
                )
            nr_enabled = (await _core_facade.aload_global_conversation_settings()).get('noiseReductionEnabled', True)
            self._check_start_operation()
            new_session = OmniRealtimeClient(
                base_url=realtime_config.get('base_url', ''),
                api_key=realtime_config['api_key'],
                model=realtime_config['model'],
                voice=self._resolve_realtime_voice(realtime_config),
                # As above: character ownership for copied frames.
                lanlan_name=self.lanlan_name,
                on_text_delta=self.handle_text_data,
                on_audio_delta=self.handle_audio_data,
                on_audio_done=self.handle_audio_done,
                on_new_message=self.handle_new_message,
                on_sid_rotate=self.rotate_speech_id_for_response_done,
                get_host_turn_id=self.read_current_speech_id,
                on_input_transcript=self.handle_input_transcript,
                on_input_transcript_with_route=self.handle_input_transcript,
                get_input_route_identity=lambda: get_active_game_route_generation_identity(
                    self.lanlan_name
                ),
                on_output_transcript=self.handle_output_transcript,
                on_connection_error=self.handle_connection_error,
                on_response_done=self.handle_response_complete,
                on_silence_timeout=self.handle_silence_timeout,
                on_status_message=self.send_status,
                on_repetition_detected=self.handle_repetition_detected,
                api_type=self.core_api_type,
                on_tool_call=None,
                tool_definitions=_initial_tool_defs,
                livestream_mode=self._is_livestream_active(),
                noise_reduction_enabled=nr_enabled,
                turn_admission_lock=self._voice_proactive_inject_lock,
            )
            # Apply user's noise reduction preference to the AudioProcessor
            if hasattr(new_session, '_audio_processor') and new_session._audio_processor:
                await new_session.set_audio_noise_reduction_enabled(nr_enabled)
                self._check_start_operation()

        new_session.on_tool_call = self._make_tool_call_handler(new_session)

        # Bind guarded callbacks BEFORE connect — connect() can invoke
        # on_connection_error during the handshake, and without the guard
        # it would run the raw unbound handler and potentially kill the
        # current active session.
        self._bind_session_lifecycle_callbacks(new_session)

        try:
            await self._connect_owned_session(new_session, initial_prompt, native_audio=not self.use_tts)
            self._check_start_operation()
        except BaseException:
            record = self._connection_record(new_session)
            if record is not None:
                self._close_connection_record(record)
            raise

        # Strong CAS promotion: assign only when self.session is None (cleared by end_session)
        # or already self, never overwriting an established winning session.
        concurrent_winner = False
        async with self.lock:
            self._check_start_operation()
            if self.session is None or self.session is new_session:
                self.session = new_session
                if not self.current_speech_id:
                    self.current_speech_id = str(uuid4())
                next_context_count = len(next_session_context_messages)
            else:
                concurrent_winner = True

        if concurrent_winner:
            self._clear_pending_context_start_prompt_marks(owner=start_prompt_context_owner)
            logger.warning("⚠️ start_llm_session: 检测到并发 start_session 已抢先建立 session，关闭本次 new_session 避免孤儿泄漏")
            try:
                await self._close_owned_session(new_session)
                self._check_start_operation()
            except Exception as _close_err:
                logger.error(f"💥 关闭并发落败的 new_session 失败: {_close_err}")
            # Return a sentinel rather than raise, avoiding start_session's generic except, which calls
            # unguarded cleanup() and could dismantle the winner's session/ws,
            # increment session_start_failure_count, and send SESSION_START_FAILED.
            return _START_LLM_CONCURRENT_ABORTED

        # Final race barrier: construction placed a registry snapshot into the client,
        # but register_tool / unregister_tool during connect() may have caused the earlier
        # asynchronous _sync_tools_to_active_session to miss self.session
        # while it was None / old. Now self.session is installed,
        # sync again for eventual consistency between wire tools and registry.
        try:
            await self._sync_tools_to_active_session()
            self._check_start_operation()
        except Exception as _sync_err:
            logger.warning("⚠️ start_llm_session: post-connect tool sync failed: %s", _sync_err)

        logger.info("✅ LLM Session 已连接")
        logger.info(f"[语音会话诊断] LLM 连接并 connect 完成 (耗时: {time.time() - _llm_create_start:.2f}秒)")
        # ``initial_prompt`` contains memory and raw conversation context.
        # Never emit it to stdout or a persistent logger.
        return next_context_count

    @start_phase
    async def _start_session_reset_state_for_new(self):
        """Reset per-conversation caches when the caller asked for a brand-new
        dialog (``new=True``)."""
        self.message_cache_for_new_session = []
        self.next_session_context_messages = []
        self.last_time = None
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self._primed_context_snapshot = None
        self.initial_next_session_context_snapshot_len = 0
        # Clear buffered input; new conversations need no previous input.
        async with self.input_cache_lock:
            self._check_start_operation()
            self.pending_input_data.clear()
            self._clear_pending_context_appends(release_durable_cached=True)

    @start_phase
    async def _start_session_activate(
        self,
        input_mode,
        next_context_count,
        diag_start,
        *,
        request_id=None,
        handshake_override=...,
        resource_optimization_override=...,
        provider_preference_override=...,
    ):
        """Post-connect activation: flip the active flags, start the message
        handler, drain queued context, open the input gate, and then acknowledge
        readiness before replaying queued input as owned background work."""
        async with self.lock:
            self._check_start_operation()
            self.is_active = True
        self.response_backend = (
            'offline_vlm'
            if isinstance(self.session, OmniOfflineClient)
            else 'realtime'
        )

        # Activity tracker: voice_engaged strictly requires the voice-mode flag.
        # Set False in text mode to prevent voice_engaged; in audio mode,
        # handle_input_transcript's on_voice_rms() maintains the 8s activity window.
        self._activity_tracker.on_voice_mode(input_mode == 'audio')

        self.session_start_time = datetime.now()
        self._session_turn_count = 0
        # Actual conversation start (not hot swap): passive recall excludes facts written during this conversation.
        self._turn_context_conversation_started_at = self.session_start_time

        # Start the message handler task.
        self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        # Resolve the microphone route before session_started opens the frontend
        # input path. When independent ASR is required, failure is non-fatal to
        # the Core session but keeps microphone input blocked instead of using Omni.
        await self._start_independent_asr_if_enabled(
            input_mode,
            handshake_override=handshake_override,
            resource_optimization_override=resource_optimization_override,
            provider_preference_override=provider_preference_override,
        )
        self._check_start_operation()

        # Startup succeeded: reset failure counter and circuit breaker.
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self._memory_error_retry_after = 0
        self._session_start_circuit_open = False
        if self.is_goodbye_silent():
            self.set_goodbye_silent(False)

        # Keep input gated until queued context enters the session, preventing the first
        # buffered/concurrent user input from reaching the model before context.
        async with self.input_cache_lock:
            self._check_start_operation()
            await self._drain_pending_context_appends_before_ready()
            self._check_start_operation()
            self.session_ready = True
            flush_reservation = object()
            self._pending_input_flush_scheduled = flush_reservation

        self._consume_next_session_context_messages(next_context_count)

        # Ready means the queued context is installed and the input gate can
        # accept work. A queued text response may stream for much longer than
        # the startup budget, so process it as owned session work after the ack.
        logger.info(f"[语音会话诊断] 即将通知前端 session_started (start_session 总耗时: {time.time() - diag_start:.2f}秒)")
        try:
            await self.send_session_started(input_mode, request_id=request_id)
            self._check_start_operation()
        except BaseException:
            if self._pending_input_flush_scheduled is flush_reservation:
                self._pending_input_flush_scheduled = None
            raise
        self._schedule_session_input_flush(flush_reservation)

        # Deliver agent-task callbacks accumulated during WebSocket disconnection after reconnection.
        if self.pending_agent_callbacks:
            self._fire_task(self.trigger_agent_callbacks())


    async def _background_prepare_pending_session(self):
        """[Hot-swap related] Prewarm the pending session in the background"""

        # Release previous pending sessions to prevent a third-instance leak.
        if self.pending_session:
            logger.info("🧹 BG Prep: 清理残留的 pending session 后再创建新的")
            await self._cleanup_pending_session_resources()

        # 2. Create PENDING session components (as before, store in self.pending_connector, self.pending_session)
        try:
            # Mirror _start_session_prepare_runtime: hot-swap candidates also pin routing for their
            # lifetime, so allow in-flight region probes a settling window here too.
            await self._config_manager.aensure_region_resolved()

            # Reread configuration for hot reload.
            # Resolve core_api_type from realtime configuration, automatically using local for custom realtime APIs.
            # Combine synchronous I/O: read core_config.json once and reuse the snapshot for realtime resolution.
            core_config_snapshot = await self._config_manager.aget_core_config()
            realtime_config = await self._config_manager.aget_model_api_config(
                'realtime', core_config=core_config_snapshot
            )
            self.core_api_type = realtime_config.get('api_type', '') or core_config_snapshot.get('CORE_API_TYPE', '')
            self.audio_api_key = core_config_snapshot['AUDIO_API_KEY']

            # Hot-swap preparation also clears invalid voice_id to prevent legacy voices entering the swap path.
            try:
                cleaned_count, legacy_names = await asyncio.to_thread(self._config_manager.cleanup_invalid_voice_ids)
                if cleaned_count > 0:
                    logger.info(f"🧹 热切换准备: 已清理 {cleaned_count} 个无效 voice_id")
                self._enqueue_voice_migration_notice(legacy_names)
            except Exception as e:
                logger.warning(f"⚠️ 热切换准备: 清理无效 voice_id 失败，继续准备会话: {e}")

            # Mirror _start_session_prepare_runtime: complete default YUI voice binding delayed by unresolved
            # region, since no other path returns to complete it.
            try:
                await ensure_default_yui_voice_for_free_api(self._config_manager, core_config_snapshot)
            except Exception as e:
                logger.warning(f"⚠️ 热切换准备: 绑定默认 YUI 音色失败，继续准备会话: {e}")

            # Reread character configuration for latest voice_id, supporting voice hot updates after character switching.
            _, _, _, self.lanlan_basic_config, _, _, _, _, _ = await self._config_manager.aget_character_data()
            old_voice_id = self.voice_id
            self._apply_voice_id_for_route()

            # Without character voice_id, try custom API's TTS_VOICE_ID fallback.
            if not self.voice_id:
                # Reuse this hot-swap preparation's initial snapshot; save_core_api ends the session before modifying core_config.
                tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
                # Filter GPT-SoVITS-disabled placeholders (format: __gptsovits_disabled__|...).
                if (
                    tts_voice_id
                    and not is_gsv_disabled_voice_id(tts_voice_id)
                    and (
                        _as_bool(core_config_snapshot.get('ENABLE_CUSTOM_API'), False)
                        or core_config_snapshot.get('GPTSOVITS_ENABLED')
                    )
                ):
                    self.voice_id = tts_voice_id
                    logger.info(f"🔄 热切换准备: 使用自定义TTS回退音色: '{self.voice_id}'")
                    self._is_free_preset_voice = False
            
            if old_voice_id != self.voice_id:
                logger.info(f"🔄 热切换准备: voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")

            pending_offline_vlm = (
                self.input_mode == 'text'
                or getattr(self, 'response_backend', 'realtime') == 'offline_vlm'
            )
            self.pending_use_tts = self._resolve_session_use_tts(
                'text' if pending_offline_vlm else self.input_mode,
                realtime_config,
                core_config_snapshot,
                log_prefix="热切换准备: ",
            )
            
            # Create the pending-session type corresponding to input_mode.
            # Reuse the main session's ToolRegistry state (manager-scoped and persistent
            # across sessions), preserving tool sets through hot swaps.
            # Hot swaps may cross languages after user_language changes;
            # refresh built-in descriptions before snapshotting.
            self._register_builtin_tools()
            _pending_tool_defs = self.tool_registry.all()
            logger.info(
                "[%s] pending session tools snapshot (input_mode=%s client=%s): %s",
                self.lanlan_name,
                self.input_mode,
                "offline" if pending_offline_vlm else "realtime",
                [t.name for t in _pending_tool_defs],
            )
            if pending_offline_vlm:
                # Text mode: use OmniOfflineClient.
                # Mirror main-session construction: character-data awaits separate the initial snapshot from here,
                # so read a fresh snapshot shared by conversation / vision to avoid torn configuration.
                _fresh_core_config = await self._config_manager.aget_core_config()
                # Mirror main-session construction: region may flip during cleanup / character reads
                # above because Steam fallback at preparation does not finalize _region_cache.
                # self.voice_id came from the old region; apply the same fail-safe, clearing free
                # voices to server defaults so promoted TTS never sends old-region IDs to new endpoints.
                if (str(core_config_snapshot.get('CORE_URL') or '')
                        != str(_fresh_core_config.get('CORE_URL') or '')):
                    logger.warning(
                        "[GeoIP] 热切换准备: 区域结论在准备与连接创建之间发生变化"
                        "（%s → %s），本场音色可能落到服务端默认",
                        core_config_snapshot.get('CORE_URL'), _fresh_core_config.get('CORE_URL'),
                    )
                    self._drop_free_voice_on_route_flip(
                        core_config_snapshot.get('CORE_URL'), _fresh_core_config.get('CORE_URL'),
                    )
                conversation_config = await self._config_manager.aget_model_api_config(
                    'conversation', core_config=_fresh_core_config
                )
                vision_config = await self._config_manager.aget_model_api_config(
                    'vision', core_config=_fresh_core_config
                )
                guard_max_length = self._get_text_guard_max_length()
                self.pending_session = self._create_offline_vlm_client(
                    conversation_config=conversation_config,
                    vision_config=vision_config,
                    tool_definitions=_pending_tool_defs,
                    max_response_length=guard_max_length,
                    external_tts_enabled=(
                        self.pending_use_tts
                        and not core_config_snapshot.get('DISABLE_TTS', False)
                    ),
                )
                logger.info("🔄 热切换准备: 创建文本模式 OmniOfflineClient")
            else:
                # Audio mode: use OmniRealtimeClient.
                # As above: do not reuse the initial snapshot.
                realtime_config = await self._config_manager.aget_model_api_config('realtime')
                nr_enabled = (await _core_facade.aload_global_conversation_settings()).get('noiseReductionEnabled', True)
                self.pending_session = OmniRealtimeClient(
                    base_url=realtime_config.get('base_url', ''),
                    api_key=realtime_config['api_key'],
                    model=realtime_config['model'],
                    voice=self._resolve_realtime_voice(realtime_config),
                    # Copied frames need character ownership: frames/all is shared across concurrent character
                    # realtime sessions, so plugins cannot identify ownership without this name. Offline already
                    # supplied it, while realtime previously always used None.
                    lanlan_name=self.lanlan_name,
                    on_text_delta=self.handle_text_data,
                    on_audio_delta=self.handle_audio_data,
                    on_audio_done=self.handle_audio_done,
                    on_new_message=self.handle_new_message,
                    on_sid_rotate=self.rotate_speech_id_for_response_done,
                    get_host_turn_id=self.read_current_speech_id,
                    on_input_transcript=self.handle_input_transcript,
                    on_input_transcript_with_route=self.handle_input_transcript,
                    get_input_route_identity=lambda: get_active_game_route_generation_identity(
                        self.lanlan_name
                    ),
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_silence_timeout=self.handle_silence_timeout,
                    on_status_message=self.send_status,
                    on_repetition_detected=self.handle_repetition_detected,
                    api_type=self.core_api_type,
                    on_tool_call=None,
                    tool_definitions=_pending_tool_defs,
                    livestream_mode=self._is_livestream_active(),
                    noise_reduction_enabled=nr_enabled,
                    turn_admission_lock=self._voice_proactive_inject_lock,
                )
                # Apply user's noise reduction preference to the AudioProcessor
                if hasattr(self.pending_session, '_audio_processor') and self.pending_session._audio_processor:
                    await self.pending_session.set_audio_noise_reduction_enabled(nr_enabled)
                logger.info("🔄 热切换准备: 创建语音模式 OmniRealtimeClient")
            
            self.pending_session.on_tool_call = self._make_tool_call_handler(
                self.pending_session
            )

            initial_prompt = await self._build_initial_prompt()
            next_session_context_messages = list(getattr(self, "next_session_context_messages", []) or [])
            self.initial_next_session_context_snapshot_len = len(next_session_context_messages)
            self.initial_cache_snapshot_len = len(self.message_cache_for_new_session)
            # Snapshot the cache the same way as next_session_context_messages
            # above: the prompt must render the state the snapshot length was
            # taken from. Anything appended while the memory request is in
            # flight is picked up by the swap-time slice
            # (``initial_cache_snapshot_len:`` below), so rendering the live
            # cache here primes those entries twice.
            initial_cache_snapshot = list(self.message_cache_for_new_session)
            from utils.internal_http_client import get_internal_http_client
            _hs_client = get_internal_http_client()
            try:
                resp = await _hs_client.get(
                    f"http://127.0.0.1:{self.memory_server_port}/new_dialog/{self.lanlan_name}",
                    **self._new_dialog_request_kwargs(),
                )
            except httpx.ConnectError:
                raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {self.memory_server_port})")
            except httpx.TimeoutException:
                raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {self.memory_server_port})")
            if not resp.is_success:
                raise ConnectionError(f"❌ 记忆服务热切换时返回非2xx状态 {resp.status_code}: {resp.text[:200]}")
            # Freeze exactly what is primed: a reply still streaming appends
            # to the last cache entry in place, and the final swap must judge
            # its increment against the text the pending session received,
            # not against that later growth.
            primed_context = [
                dict(entry)
                for entry in list(next_session_context_messages) + list(initial_cache_snapshot)
            ]
            self._primed_context_snapshot = primed_context
            initial_prompt += (
                resp.text
                # One call for both slices: a screen chain split across them
                # is judged as one run (see _convert_cache_to_str).
                + self._convert_cache_to_str(primed_context)
            )
            self._bind_session_lifecycle_callbacks(self.pending_session)
            await self._connect_owned_session(self.pending_session, initial_prompt, native_audio=not self.pending_use_tts)

            # Like main sessions, hot-swap pending_session needs another sync after connect,
            # covering register/unregister races during connection.
            try:
                await self._sync_tools_to_active_session()
            except Exception as _sync_err:
                logger.warning("⚠️ pending_session post-connect tool sync failed: %s", _sync_err)

            if self.pending_session_warmed_up_event:
                self.pending_session_warmed_up_event.set()

        except asyncio.CancelledError:
            logger.error("💥 BG Prep Stage 1: Task cancelled.")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event here if cancelled.
        except Exception as e:
            # Record detailed HTTP errors, such as 503.
            error_detail = str(e)
            if hasattr(e, 'status_code'):
                error_detail = f"HTTP {e.status_code}: {e}"
            if hasattr(e, 'body'):
                error_detail += f" | Body: {e.body}"
            logger.error(f"💥 BG Prep Stage 1: Error: {error_detail}", exc_info=True)
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event on error.
        finally:
            # Ensure this task variable is cleared so it's known to be done
            if self.background_preparation_task and self.background_preparation_task.done():
                self.background_preparation_task = None

    async def _trigger_immediate_preparation_for_extra(self):
        """When extra prompts need injecting and preparation hasn't started yet, start preparing immediately and schedule the renew logic."""
        try:
            if not self.is_preparing_new_session:
                logger.info("Extra Reply: Triggering preparation due to pending extra reply.")
                self.is_preparing_new_session = True
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []
                self.initial_cache_snapshot_len = 0
                self._primed_context_snapshot = None
                self.initial_next_session_context_snapshot_len = 0
                # Start background warmup immediately without waiting ten seconds.
                self.pending_session_warmed_up_event = asyncio.Event()
                if not self.background_preparation_task or self.background_preparation_task.done():
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())
        except Exception as e:
            logger.error(f"💥 Extra Reply: preparation trigger error: {e}")

    @staticmethod
    def _swap_session_is_dead(session) -> bool:
        """[Hot-swap related] Closed/unusable session detection for the swap
        abort handlers: a dead session must be fail-closed instead of getting
        a listener restarted on it. Realtime clients clear ``ws`` on close();
        offline (text) clients clear ``llm`` on close().
        """
        if not session:
            return False
        if isinstance(session, OmniRealtimeClient):
            return not session.ws
        if isinstance(session, OmniOfflineClient):
            return session.llm is None
        return False

    def _restore_undelivered_swap_extras(self, injected_extras: list, cb_backed_ids: set = None) -> None:
        """[Hot-swap related] Return removed-but-undelivered extras to the queue head.

        ``_perform_final_swap_sequence`` keeps ``pending_extra_replies``
        untouched through the prime window and removes the budget-selected
        entries only at promote success — so every pre-promote abort keeps the
        queue intact and needs no restore. The exits where removal has already
        happened but the promoted session dies before speaking (post-promote
        ws-invalid fail-close, post-promote external cancellation) put the
        removed entries back at the queue head so the next hot-swap delivers
        them, mirroring the ``_deferred`` entries that stay queued across
        aborts.

        ``cb_backed_ids``: delivery ids whose paired callback was still in
        ``pending_agent_callbacks`` at removal time. An id in this set whose
        callback is GONE by restore time was consumed inside the window (a
        successful voice delivery prunes both queues; the extras half no-ops
        on checked-out entries) — restoring it would announce the callback a
        second time on the next hot-swap, so it is dropped.
        """
        if not injected_extras:
            return
        try:
            from config import AGENT_CALLBACK_QUEUE_MAX_ITEMS
            # Paired callbacks may be retracted during the window: retraction sweeps only mirror entries
            # still queued at that moment, missing removed _selected items.
            # Before restoring, remove IDs still marked retracted in pending_agent_callbacks.
            retracted_ids = {
                cb.get("_callback_delivery_id")
                for cb in (getattr(self, "pending_agent_callbacks", None) or [])
                if isinstance(cb, dict)
                and cb.get(DELIVERY_RETRACTED_KEY)
                and cb.get("_callback_delivery_id")
            }
            queued_ids = {
                extra.get("_callback_delivery_id")
                for extra in self.pending_extra_replies
                if isinstance(extra, dict) and extra.get("_callback_delivery_id")
            }
            # Never restore topic-hook extras: mainline retraction (voice blocking / acknowledgment timeout)
            # marks then synchronously purges, so window-time markers are already gone and invisible to
            # retracted_ids above. Restoring would bypass _drop_pending_topic_hooks_for_voice,
            # resurrecting forbidden voice hooks; TopicHookPool has its own
            # acknowledgment/retry accounting, so discarding extras loses no content.
            # Premise: retraction is currently exclusive to topic hooks. Every DELIVERY_RETRACTED_KEY
            # setter gates on channel=="topic_hook" or is reachable only from topic delivery
            # (three proactive.py sites + proactive_delivery.retract's sole call chain
            # topic/delivery._remove_callback_from_manager). Excluding topic therefore prevents
            # resurrection of window-time retractions. Future non-topic retraction
            # requires a queryable retraction ledger here instead of rechecking markers.
            # Detect consumption during the window: a paired callback existed on removal but is now gone, meaning voice delivery or similar
            # consumed it; do not restore. Extras-only entries without callbacks on removal can only be delivered
            # by hot swap itself, so cannot be consumed in the window and may pass.
            current_cb_ids = {
                cb.get("_callback_delivery_id")
                for cb in (getattr(self, "pending_agent_callbacks", None) or [])
                if isinstance(cb, dict) and cb.get("_callback_delivery_id")
            }
            consumed_ids = (cb_backed_ids or set()) - current_cb_ids
            restored = [
                extra for extra in injected_extras
                if not isinstance(extra, dict)
                or (extra.get("source_kind") != "topic"
                    and extra.get("_callback_delivery_id") not in retracted_ids
                    and extra.get("_callback_delivery_id") not in queued_ids
                    and extra.get("_callback_delivery_id") not in consumed_ids)
            ]
            if not restored:
                return
            # Restore at the queue head in original relative order (_selected preceded _deferred).
            self.pending_extra_replies = restored + self.pending_extra_replies
            # Match enqueue_agent_callback's flood guard: drop oldest.
            if len(self.pending_extra_replies) > AGENT_CALLBACK_QUEUE_MAX_ITEMS:
                self.pending_extra_replies = self.pending_extra_replies[-AGENT_CALLBACK_QUEUE_MAX_ITEMS:]
            logger.info(
                "Final Swap Sequence: %d undelivered extra replies restored to queue head after aborted swap",
                len(restored),
            )
        except Exception as e:
            # Restoration is best-effort; queue accounting must never interrupt abort cleanup.
            logger.warning(f"Final Swap Sequence: failed to restore undelivered extras: {e}")

    def _select_passive_callbacks_for_swap_prime(
        self,
        extras_selected: list = None,
        *,
        require_media_ready: bool = True,
        render: bool = True,
    ) -> tuple:
        """[Hot-swap related] Pick queued passive callbacks to ride the swap prime.

        Passive (``delivery_mode="passive"`` / ai_behavior="read") callbacks
        never mirror into ``pending_extra_replies`` (PR #2469), so a pure
        voice session has no user-turn drain to carry them. Their delivery
        point is HERE: the next NATURALLY-occurring hot swap hands them to
        the new session as background context. Non-Gemini providers get a
        DEDICATED ``prime_context(skipped=True)`` call (instructions channel,
        no turn) AFTER the announce prime — physically separate, so read
        content can never become part of the user turn that triggers a
        spoken response. Gemini has no instructions channel and a global,
        response-unscoped skip guard, so one swap may carry at most ONE
        prime turn: on a no-extras swap the passive block merges into the
        single ``skipped=True`` prime; a swap that also announces extras
        skips the ride-along and leaves passive queued for the next swap.

        Deliberate trade-off (owner decision): an ACTIVE voice session gets
        no mid-session context push for passive cues — they wait for the
        next natural swap, even if that is several user turns away. Pushing
        into a live session would reopen the interruption/session-churn
        surface this design exists to close.

        Returns ``(selected, rendered_text)``. Selection shares the
        ``AGENT_CALLBACK_TOTAL_MAX_TOKENS`` budget with the already-selected
        proactive extras: ``extras_selected`` is prepended to the candidate
        list before the budget walk, so passive only takes what the extras
        left over. Over-budget passive callbacks stay queued for the next
        swap (same semantics as the extras ``_deferred``).

        The queue is NOT drained here — removal is deferred to promote
        success via :meth:`_remove_swap_delivered_callbacks`. Selected
        entries are atomically marked provider-owned before this method
        returns, so enqueue coalescing, text drain, staleness sweeps, and the
        flood guard cannot retract them during the prime await. Every
        pre-promote exit releases that claim in the swap sequence's ``finally``
        block. Topic-hook snapshots are excluded: they have their own ack/retry
        lifecycle and delivery gates that this path must not bypass.
        """
        try:
            candidates = [
                cb
                for cb in (
                    getattr(self, "pending_agent_callbacks", []) or []
                )
                if isinstance(cb, dict)
                and cb.get("delivery_mode") == "passive"
                and not cb.get(DELIVERY_RETRACTED_KEY)
                and not cb.get(SWAP_PRIME_DELIVERY_CLAIM_KEY)
                and cb.get("channel") != "topic_hook"
                and (
                    not require_media_ready
                    or self._callback_media_ready_for_session(
                        cb,
                        getattr(self, "pending_session", None),
                    )
                )
            ]
            if not candidates:
                return [], ""
            # Same staleness hygiene as the text-mode drain: a same-key
            # superseded cue must not deliver, and gets purged from the live
            # queue (ack False) rather than lingering until the next drain.
            self._retract_stale_coalesced(candidates)
            self._purge_undeliverable_callbacks()
            candidates = [
                cb for cb in candidates if not cb.get(DELIVERY_RETRACTED_KEY)
            ]
            if not candidates:
                return [], ""
            from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
            _extras = list(extras_selected or [])
            selected_all, _ = _select_callbacks_within_token_budget(
                _extras + candidates, AGENT_CALLBACK_TOTAL_MAX_TOKENS
            )
            selected = selected_all[len(_extras):]
            if not selected:
                return [], ""
            rendered = ""
            if render:
                # Follow proactive's three delivery paths: defer script normalization to rendering.
                _lang = normalize_language_code(self.user_language, format='full')
                rendered = _build_callback_instruction(
                    selected,
                    lang=_lang,
                    lanlan_name=getattr(self, "lanlan_name", "") or "",
                    master_name=getattr(self, "master_name", "") or "",
                    passive=True,
                )
            # No await exists between selection and this ownership claim. From
            # here until promote/abort, every queue mutation sees the same
            # provider-owned boundary as the swap sequence.
            for cb in selected:
                cb[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
            return selected, rendered
        except Exception as e:
            # Selection/rendering failure must not interrupt swap; retain these passive entries until next pass.
            logger.warning(f"Final Swap Sequence: passive callback selection failed: {e}")
            return [], ""

    def _render_claimed_passive_callbacks_for_swap_prime(
        self,
        selected: list,
    ) -> tuple:
        """Render the media-ready subset of one pre-staging swap snapshot."""
        ready = []
        for callback in selected:
            if callback.get(PASSIVE_MEDIA_BUDGET_DEFERRED_KEY):
                # This pass's image budget did not reach this item. STOP, not skip or block only image entries:
                # split_callbacks_by_image_budget is **strict FIFO**, marking the entire suffix
                # after budget exhaustion, including text-only callbacks. Since
                # _callback_media_ready_for_session always accepts text-only callbacks,
                # filtering only through it would let text **after** a deferred image cue
                # be delivered and dequeued by this swap first, reversing model-visible order.
                # Same criterion as drain_agent_callbacks_for_llm, the first consumption point;
                # this is the second.
                break
            if callback.get(DELIVERY_RETRACTED_KEY):
                continue
            if not self._callback_media_ready_for_session(
                callback,
                getattr(self, "pending_session", None),
            ):
                # STOP whenever media is unready, **whether transient or terminal**. Skipping to render later items
                # dequeues them after promotion while retaining this one, reversing model-visible
                # order, the same FIFO argument as budget deferral.
                #
                # STOP on terminal failure does not block forever: drain handles terminal failures
                # best-effort, delivering text without images this turn; the next user turn releases it
                # and following items. This only prevents overtaking during this swap,
                # never permanent nondelivery.
                #
                # Do not classify transient/terminal here either: staging's exception path deliberately omits
                # PASSIVE_MEDIA_TRANSIENT_KEY (drain's existing response is still delivering text).
                # Using that flag would misclassify exceptions as terminal. Both cases STOP,
                # so classification is unnecessary.
                break
            ready.append(callback)
        ready_obj_ids = {id(callback) for callback in ready}
        self._release_swap_prime_passive_claims(
            [callback for callback in selected if id(callback) not in ready_obj_ids]
        )
        if not ready:
            return [], ""
        _lang = normalize_language_code(self.user_language, format='full')
        return ready, _build_callback_instruction(
            ready,
            lang=_lang,
            lanlan_name=getattr(self, "lanlan_name", "") or "",
            master_name=getattr(self, "master_name", "") or "",
            passive=True,
        )

    @staticmethod
    def _release_swap_prime_passive_claims(selected: list) -> None:
        """Release provider ownership after promote or any abort exit."""
        for cb in selected or []:
            if isinstance(cb, dict):
                cb.pop(SWAP_PRIME_DELIVERY_CLAIM_KEY, None)

    def _remove_swap_delivered_callbacks(self, selected: list) -> list:
        """[Hot-swap related] Dequeue prime-injected callback objects at
        promote success; returns the actually-removed subset (ack'd True).

        Also used for callbacks paired with delivered extra-reply mirrors.

        Identity-based removal, same as the extras counterpart: entries a
        concurrent path consumed inside the prime→promote window (text-turn
        drain, retraction purge, flood cap) no-op here instead of deleting a
        re-queued same-id newcomer.
        """
        if not selected:
            return []
        self._release_swap_prime_passive_claims(selected)
        try:
            selected_obj_ids = {id(cb) for cb in selected}
            removed = [
                cb for cb in self.pending_agent_callbacks
                if id(cb) in selected_obj_ids
            ]
            if not removed:
                return []
            self.pending_agent_callbacks = [
                cb for cb in self.pending_agent_callbacks
                if id(cb) not in selected_obj_ids
            ]
            for cb in removed:
                resolve_callback_delivery_ack(cb, True)
            return removed
        except Exception as e:
            logger.warning(f"Final Swap Sequence: passive callback dequeue failed: {e}")
            return []

    def _restore_undelivered_swap_passive_cbs(self, removed_cbs: list) -> None:
        """[Hot-swap related] Mirror of :meth:`_restore_undelivered_swap_extras`
        for prime-injected passive callbacks.

        Only the post-promote death exits call this (promoted session dies
        before its next reply, so the primed context is lost): put the
        removed entries back at the queue head for the next swap. Staleness
        is deliberately NOT re-checked here — every delivery point
        (text-turn drain / next swap's selection) re-runs
        ``_retract_stale_coalesced`` anyway. Topic-hook snapshots and entries
        whose delivery id is already back in the queue (a newer re-enqueue
        won) are skipped, matching the extras restore guards.
        """
        if not removed_cbs:
            return
        try:
            from config import AGENT_CALLBACK_QUEUE_MAX_ITEMS
            queued_ids = {
                cb.get("_callback_delivery_id")
                for cb in (self.pending_agent_callbacks or [])
                if isinstance(cb, dict) and cb.get("_callback_delivery_id")
            }
            restored = [
                cb for cb in removed_cbs
                if isinstance(cb, dict)
                and not cb.get(DELIVERY_RETRACTED_KEY)
                and cb.get("channel") != "topic_hook"
                and cb.get("_callback_delivery_id") not in queued_ids
            ]
            if not restored:
                return
            self.pending_agent_callbacks = restored + self.pending_agent_callbacks
            self._enforce_agent_callback_queue_limit(
                AGENT_CALLBACK_QUEUE_MAX_ITEMS
            )
            logger.info(
                "Final Swap Sequence: %d undelivered passive callback(s) restored to queue head after aborted swap",
                len(restored),
            )
        except Exception as e:
            # Restoration is best-effort; queue accounting must never interrupt abort cleanup.
            logger.warning(f"Final Swap Sequence: failed to restore undelivered passive callbacks: {e}")

    async def _perform_final_swap_sequence(self):
        """[Hot-swap related] Perform the final swap sequence"""
        logger.info("Final Swap Sequence: Starting...")
        if not self.pending_session:
            logger.error("💥 Final Swap Sequence: Pending session not found. Aborting swap.")
            await self._reset_preparation_state(clear_main_cache=True)  # Reset all flags and cache for clean restart
            self.is_hot_swap_imminent = False
            return
        
        # Check pending_session WebSocket validity.
        if isinstance(self.pending_session, OmniRealtimeClient):
            if not hasattr(self.pending_session, 'ws') or not self.pending_session.ws:
                logger.error("💥 Final Swap Sequence: Pending session的WebSocket已关闭，放弃swap操作")
                await self._cleanup_pending_session_resources()
                await self._reset_preparation_state(clear_main_cache=True)
                self.is_hot_swap_imminent = False
                return
            
            # Check for fatal errors.
            if hasattr(self.pending_session, '_fatal_error_occurred') and self.pending_session._fatal_error_occurred:
                logger.error("💥 Final Swap Sequence: Pending session已发生致命错误，放弃swap操作")
                await self._cleanup_pending_session_resources()
                await self._reset_preparation_state(clear_main_cache=True)
                self.is_hot_swap_imminent = False
                return

        # A normal native swap may only start priming while the current input
        # connection is between utterances.  This is a read-only preflight; the
        # authoritative check is repeated by _begin_voice_activation_handoff
        # after priming and again after its output pause settles.  Deferring at
        # this point leaves the single warmed pending session and every queue in
        # place, so a later response-complete event can retry without polling.
        if (
            getattr(self, "_voice_session_activation_factory", None) is not None
            and getattr(self, "_asr_route_mode", "blocked") == "native"
        ):
            source_boundary = getattr(
                getattr(self, "session", None),
                "can_handoff_voice_input",
                None,
            )
            try:
                source_boundary_ready = bool(
                    callable(source_boundary) and source_boundary() is True
                )
            except Exception as boundary_error:
                source_boundary_ready = False
                logger.warning(
                    "Final Swap Sequence: native voice handoff boundary check "
                    "failed: %s",
                    boundary_error,
                )
            if not source_boundary_ready:
                logger.info(
                    "Final Swap Sequence: native voice input is not at a safe "
                    "handoff boundary; deferring to a later turn completion"
                )
                self.is_hot_swap_imminent = False
                return

        try:
            new_session = None  # Initialize early for safe except access; actual assignment occurs in PERFORM ACTUAL HOT SWAP.
            voice_handoff_ticket = None
            old_listener_cancel_timed_out = False  # Old-listener cancellation-timeout flag for except's fail-close decisions.
            # References to _selected entries injected into pending_session (still queued in place until
            # successful promotion); _removed_extras is the subset actually removed on promotion, restored only
            # on exits where removal happened and the injected session died (invalid-ws fail-close / external cancellation
            # after promotion). Other aborts never changed queues and need no restoration.
            # _removed_cb_backed_ids: delivery_ids whose paired callbacks remained in pending_agent_callbacks
            # on removal; restoration uses them to detect voice consumption during the window.
            _prime_selected_extras: list = []
            _removed_extras: list = []
            _removed_cb_backed_ids: set = set()
            _prime_extra_callbacks: list = []
            _prime_extra_claimed: list = []
            # Set by the cancellation exit: a session promoted before the cancel
            # is about to be closed by the canceller, so its primed extras are
            # not delivered even when the swap removed none of them.
            _swap_cancelled = False
            # Passive callbacks riding this swap (voice pending session only).
            # Same deferred-removal bookkeeping as _prime_selected_extras:
            # queue untouched until promote succeeds. Injection goes through
            # its OWN prime_context(skipped=True) call below — never merged
            # into the announce prime text.
            _prime_selected_passive_cbs: list = []
            _removed_passive_cbs: list = []
            _passive_sel: list = []
            _passive_swap_text = ""
            _extras_for_budget: list = []
            _passive_media_outcome: dict[str, bool] | None = None

            def _abort_if_passive_claim_retracted(stage: str) -> None:
                if any(cb.get(DELIVERY_RETRACTED_KEY)
                       for cb in _prime_selected_passive_cbs):
                    logger.info(
                        "Final Swap Sequence: passive callback retracted %s; abandoning pending session",
                        stage,
                    )
                    raise asyncio.CancelledError()

            async def _abort_if_native_prefix_lost_its_text(
                outcome: dict | None,
                rendered_text: str,
                stage: str,
            ) -> bool:
                if rendered_text or not (
                    outcome and outcome.get("native_prefix_committed")
                ):
                    return False
                logger.warning(
                    "Final Swap Sequence: passive native media lost its "
                    "callback text %s; abandoning pending session",
                    stage,
                )
                await self._cleanup_pending_session_resources()
                await self._reset_preparation_state(clear_main_cache=True)
                self.is_hot_swap_imminent = False
                return True

            next_session_context_messages = getattr(self, "next_session_context_messages", []) or []
            incremental_next_session_context = next_session_context_messages[
                self.initial_next_session_context_snapshot_len:
            ]
            # Copies: a reply still streaming grows its cache entry in place,
            # and that growth is not in the final prime rendered below.
            incremental_cache = [
                dict(entry)
                for entry in (
                    list(incremental_next_session_context)
                    + self.message_cache_for_new_session[self.initial_cache_snapshot_len:]
                )
            ]
            # What the pending session was primed with at preparation, in
            # prime order: next-session context snapshot, then cache snapshot,
            # as frozen when it was rendered.
            primed_snapshot = getattr(self, "_primed_context_snapshot", None)
            if primed_snapshot is None:
                primed_snapshot = (
                    list(next_session_context_messages[
                        :self.initial_next_session_context_snapshot_len
                    ])
                    + list(self.message_cache_for_new_session[
                        :self.initial_cache_snapshot_len
                    ])
                )
            primed_snapshot = list(primed_snapshot)
            # ...and everything the final prime below adds. Late context
            # (arriving while that prime awaits) is judged after all of it.
            primed_context_sequence = primed_snapshot + incremental_cache
            # 1. Send incremental cache (or a heartbeat) to PENDING session for its *second* ignored response
            if incremental_cache:
                # Judged together with exactly what the pending session was
                # already primed with, so a chain crossing that boundary counts.
                final_prime_text = self._convert_cache_to_str(
                    incremental_cache,
                    preceding=primed_snapshot,
                )
            else:  # Ensure session cycles a turn even if no incremental cache
                final_prime_text = ""  # Initialize to empty string to prevent NameError
                logger.debug(f"🔄 No incremental cache found. 缓存长度: {len(self.message_cache_for_new_session)}, 快照长度: {self.initial_cache_snapshot_len}")

            # Re-check queue-backed and orphan voice mirrors at the final
            # render boundary. They may have expired while this pending session
            # was warming up, after leaving the proactive delivery manager.
            self.pending_extra_replies = self.filter_deliverable_callbacks(
                list(self.pending_extra_replies)
            )
            # If extra hints need injection, instruct the model to ignore the previous message and include all hints in the next response to the user.
            if self.pending_extra_replies and len(self.pending_extra_replies) > 0:
                _lang = normalize_language_code(self.user_language, format='short')
                from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
                # Budget-aware selection (mirror of the text-mode drain): render
                # only what fits, keep the rest for the next hot-swap rather than
                # dropping it after clearing the queue.
                _selected, _deferred = _select_callbacks_within_token_budget(
                    list(self.pending_extra_replies), AGENT_CALLBACK_TOTAL_MAX_TOKENS
                )
                # Pull-model staleness guard (same contract as the voice/text
                # delivery points): drop extras whose coalesce_key has a newer
                # submission — the push-side eviction removes queue entries but
                # cannot reach text already baked into final_prime_text, so the
                # check must run here, synchronously between select and render
                # (no await in between; a newer cue landing during the prime
                # await below is an accepted residual window). Stale extras are
                # NOT added to _deferred: their newer same-key mirror is (or will
                # be) queued in their place.
                _selected = [
                    e for e in _selected
                    if not self._coalesce_entry_is_stale(e)
                ]
                # Snapshot paired objects before the provider await. A later
                # same-id enqueue must not be acknowledged or removed for text
                # that came from this snapshot.
                _selected_delivery_ids = {
                    e.get("_callback_delivery_id") for e in _selected
                    if isinstance(e, dict) and e.get("_callback_delivery_id")
                }
                # Include callbacks a text turn has claimed: if that turn later
                # fails before commit it restores them, and the swap must still
                # be able to consume them once their mirror is delivered. Only
                # claim (and later release) the ones nobody else holds, so the
                # text turn's claim is never cleared from under it.
                _prime_extra_callbacks = [
                    cb for cb in (getattr(self, "pending_agent_callbacks", None) or [])
                    if isinstance(cb, dict)
                    and cb.get("_callback_delivery_id") in _selected_delivery_ids
                    and not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                _prime_extra_claimed = [
                    cb for cb in _prime_extra_callbacks
                    if not cb.get(SWAP_PRIME_DELIVERY_CLAIM_KEY)
                ]
                for cb in _prime_extra_claimed:
                    cb[SWAP_PRIME_DELIVERY_CLAIM_KEY] = True
                final_prime_text += _render_pending_extra_replies_by_origin(
                    _selected,
                    lang=_lang,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name,
                )
                # Passive read callbacks piggyback here only by recording budget references (extras take
                # priority); defer selection until the unified injection point after main prime, narrowing the stale
                # window when updated same-key cues enqueue during main-prime awaits (Codex P2).
                # Never mix injection into this branch's skipped=False announcement prime: read content
                # must not become part of the user turn triggering proactive speech. Gemini does not
                # piggyback on swaps with extras (the unified point skips it), since global
                # _skip_until_next_response ignores response identity; a second turn would
                # discard the announcement and expose the read response (Codex P1). Retain until a swap
                # without extras.
                _extras_for_budget = _selected
                try:
                    await self.pending_session.prime_context(final_prime_text, skipped=False)
                except (
                    web_exceptions.ConnectionClosed,
                    AttributeError,
                    ConnectionError,
                    RuntimeError,
                    asyncio.TimeoutError,
                ) as e:
                    # pending_session connection closed or WebSocket None: abandon the entire swap.
                    # skipped=False prime uses create_response -> response arbiter;
                    # ticket failures surface as ConnectionError / RuntimeError / TimeoutError
                    # (interrupted dispatch, unavailable connection, terminal-state timeout, etc.). These also mean
                    # prime was not delivered, abandon this swap, and keep the old session. Use this branch's
                    # targeted cleanup rather than outer fallback reporting a benign abort as INTERNAL_UPDATE_FAILED
                    # to the frontend.
                    logger.error(f"💥 Final Swap Sequence: pending_session不可用，放弃swap操作: {e}")
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
                # After successful injection, leave the queue **unchanged**, retaining selected object references;
                # defer actual removal until promotion succeeds and the injected session becomes active.
                # Concurrent removers during prime->promotion (successful proactive voice delivery clearing
                # both queues by delivery_id in trigger_agent_callbacks, retraction
                # purge, topic voice-blocking sweeps, flood caps) still find queued entries normally,
                # eliminating the TOCTOU blind spot where removed entries escape cleanup and resurrect on abort.
                # All pre-promotion aborts naturally retain the full queue, matching _deferred,
                # without restoration code. Over-budget _deferred waits for next pass.
                _prime_selected_extras = _selected
            else:
                _lang = normalize_language_code(self.user_language, format='short')
                # Match main-session construction: hot swap also runs in text mode,
                # as the self.input_mode == 'text' pending branch below shows.
                final_prime_text += get_context_summary_ready(
                    _lang, input_mode=self.input_mode,
                ).format(name=self.lanlan_name, master=self.master_name)
                # Passive read piggyback: this branch has no proactive extras,
                # so passive content gets the whole budget. Non-Gemini uses unified
                # skipped=True injection after if/else; Gemini merges it here into the branch's only
                # skipped=True prime. Gemini has no instructions channel and every
                # prime is a turn; two turns in one swap would be discarded in the wrong order by the global
                # skip flag (Codex P1), so require one turn: skip-guard discards its response
                # while content remains in context, preserving read semantics.
                if (isinstance(self.pending_session, OmniRealtimeClient)
                        and getattr(self.pending_session, "_is_gemini", False)):
                    _passive_sel, _ = (
                        self._select_passive_callbacks_for_swap_prime(
                            require_media_ready=False,
                            render=False,
                        )
                    )
                    _passive_media_outcome = await self._stage_passive_callback_media(
                        _passive_sel,
                        self.pending_session,
                    )
                    if not _passive_media_outcome["safe_to_continue"]:
                        logger.warning(
                            "Final Swap Sequence: passive native media staging became partial/rejected; abandoning pending session"
                        )
                        await self._cleanup_pending_session_resources()
                        await self._reset_preparation_state(clear_main_cache=True)
                        self.is_hot_swap_imminent = False
                        return
                    _passive_sel, _passive_swap_text = (
                        self._render_claimed_passive_callbacks_for_swap_prime(
                            _passive_sel
                        )
                    )
                    if await _abort_if_native_prefix_lost_its_text(
                        _passive_media_outcome,
                        _passive_swap_text,
                        "after Gemini media staging",
                    ):
                        return
                    if _passive_swap_text:
                        final_prime_text += "\n" + _passive_swap_text
                try:
                    await self.pending_session.prime_context(final_prime_text, skipped=True)
                except (
                    web_exceptions.ConnectionClosed,
                    AttributeError,
                    ConnectionError,
                    RuntimeError,
                    asyncio.TimeoutError,
                ) as e:
                    # Mirror the skipped=False branch's narrow-then-broaden pattern: Gemini's
                    # skipped=True prime uses SDK send, likewise surfacing RuntimeError /
                    # ConnectionError; benign swap abandonment must not reach outer fallback.
                    logger.error(f"💥 Final Swap Sequence: pending_session不可用，放弃swap操作: {e}")
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
                # Gemini merged piggyback succeeded: prime already contains the passive block; account for it now.
                # The unified injection point skips _is_gemini, preventing duplicate delivery.
                if _passive_swap_text:
                    _prime_selected_passive_cbs = _passive_sel

            # Unified passive-read piggyback injection point for non-Gemini; Gemini merges a single turn in the
            # no-extras branch and never piggybacks with extras.
            # Select after awaiting main prime: updated same-key cues enqueued during prime
            # retract old queued entries, so selecting now avoids injecting an expired
            # snapshot into the new session (Codex P2). Only this injection's own await
            # remains, matching extras' accepted residual window. Non-Gemini skipped=True
            # uses session instructions (Qwen too), creating no
            # turn and staying physically separate from announcement prime. Once provider invocation begins, errors cannot
            # prove whether remote writes occurred; abandon and close this pending session,
            # never promote while retaining cues for future retry and duplicate delivery.
            if (isinstance(self.pending_session, OmniRealtimeClient)
                    and not getattr(self.pending_session, "_is_gemini", False)):
                _passive_sel, _ = (
                    self._select_passive_callbacks_for_swap_prime(
                        extras_selected=_extras_for_budget,
                        require_media_ready=False,
                        render=False,
                    )
                )
                _passive_media_outcome = await self._stage_passive_callback_media(
                    _passive_sel,
                    self.pending_session,
                )
                if not _passive_media_outcome["safe_to_continue"]:
                    logger.warning(
                        "Final Swap Sequence: passive native media staging became partial/rejected; abandoning pending session"
                    )
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
                _passive_sel, _passive_swap_text = (
                    self._render_claimed_passive_callbacks_for_swap_prime(
                        _passive_sel
                    )
                )
                if await _abort_if_native_prefix_lost_its_text(
                    _passive_media_outcome,
                    _passive_swap_text,
                    "after media staging",
                ):
                    return
                if _passive_swap_text:
                    try:
                        await self.pending_session.prime_context(_passive_swap_text, skipped=True)
                        _prime_selected_passive_cbs = _passive_sel
                    except Exception as e:
                        logger.warning(
                            f"Final Swap Sequence: passive ride-along prime failed; abandoning pending session: {e}"
                        )
                        await self._cleanup_pending_session_resources()
                        await self._reset_preparation_state(clear_main_cache=True)
                        self.is_hot_swap_imminent = False
                        return

            _abort_if_passive_claim_retracted("during prime")

            # 2. Start temporary listener for PENDING session's *second* ignored response
            if self.pending_session_final_prime_complete_event:
                self.pending_session_final_prime_complete_event.set()

            # --- PERFORM ACTUAL HOT SWAP ---
            logger.info("Final Swap Sequence: Starting actual session swap...")
            old_main_session = self.session
            old_main_message_handler_task = self.message_handler_task

            begin_voice_handoff = getattr(
                self,
                "_begin_voice_activation_handoff",
                None,
            )
            if (
                getattr(self, "_voice_session_activation_factory", None)
                is not None
                and callable(begin_voice_handoff)
            ):
                voice_handoff_ticket = await begin_voice_handoff(
                    self.pending_session
                )
                if voice_handoff_ticket is False:
                    # Priming has already changed this pending connection's
                    # private context.  Retire that one instance rather than
                    # retrying the prime and duplicating callback/context
                    # injection.  The Core handoff hook has already applied
                    # the route-specific failure policy (native may fail-close
                    # its source); the authoritative queues stay untouched so
                    # recovery can prepare exactly one replacement and retry.
                    logger.info(
                        "Final Swap Sequence: voice activation handoff deferred; "
                        "retiring the primed pending session for an event-driven retry"
                    )
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return

            def _voice_handoff_is_current(*, allow_promoted: bool = False) -> bool:
                if voice_handoff_ticket is None:
                    return True
                checker = getattr(
                    self,
                    "_voice_activation_handoff_is_current",
                    None,
                )
                return bool(
                    callable(checker)
                    and checker(
                        voice_handoff_ticket,
                        allow_promoted=allow_promoted,
                    )
                )

            def _settle_owned_handoff_step(step_task: asyncio.Task) -> None:
                _ORPHAN_SESSION_REAPER_TASKS.discard(step_task)
                if step_task.cancelled():
                    return
                try:
                    step_task.exception()
                except (asyncio.CancelledError, Exception):
                    pass

            async def _await_voice_handoff_step(
                awaitable,
                *,
                stage: str,
                allow_promoted: bool = False,
            ):
                if voice_handoff_ticket is None:
                    return await awaitable
                if not _voice_handoff_is_current(
                    allow_promoted=allow_promoted
                ):
                    if inspect.iscoroutine(awaitable):
                        awaitable.close()
                    raise RuntimeError(
                        f"voice activation handoff became stale before {stage}"
                    )
                step_task = asyncio.ensure_future(awaitable)
                _ORPHAN_SESSION_REAPER_TASKS.add(step_task)
                step_task.add_done_callback(_settle_owned_handoff_step)
                remaining = max(
                    0.0,
                    voice_handoff_ticket.deadline
                    - asyncio.get_running_loop().time(),
                )
                try:
                    done, _pending = await asyncio.wait(
                        {step_task},
                        timeout=remaining,
                    )
                except asyncio.CancelledError:
                    step_task.cancel()
                    await asyncio.wait({step_task}, timeout=0.1)
                    raise
                if not done:
                    step_task.cancel()
                    settled, _pending = await asyncio.wait(
                        {step_task},
                        timeout=0.1,
                    )
                    if not settled:
                        step_task.cancel()
                        logger.warning(
                            "Final Swap Sequence: %s ignored cancellation; "
                            "retained in the owned cleanup registry",
                            stage,
                        )
                    raise TimeoutError(
                        f"voice activation handoff deadline exceeded during {stage}"
                    )
                result = step_task.result()
                if not _voice_handoff_is_current(
                    allow_promoted=allow_promoted
                ):
                    raise RuntimeError(
                        f"voice activation handoff became stale after {stage}"
                    )
                return result

            async def _abort_voice_handoff(reason: str) -> None:
                if voice_handoff_ticket is None:
                    return
                abort_handoff = getattr(
                    self,
                    "_abort_voice_activation_handoff",
                    None,
                )
                if callable(abort_handoff):
                    await abort_handoff(
                        voice_handoff_ticket,
                        reason=reason,
                    )

            # Provider close implementations are not required to cooperate
            # with cancellation.  Keep each retirement close in an owned task
            # and wait only inside the handoff's absolute budget; a close that
            # swallows CancelledError must never pin the swap past five seconds.
            _replacement_close_tasks: dict[int, asyncio.Task] = {}

            def _settle_owned_replacement_close(close_task: asyncio.Task) -> None:
                _ORPHAN_SESSION_REAPER_TASKS.discard(close_task)
                if close_task.cancelled():
                    return
                try:
                    close_task.exception()
                except (asyncio.CancelledError, Exception):
                    pass

            async def _retire_replacement_session(
                session_to_close,
                *,
                stage: str,
            ) -> None:
                if session_to_close is None:
                    return
                close_task = _replacement_close_tasks.get(id(session_to_close))
                if close_task is None:
                    close_task = asyncio.create_task(
                        self._close_owned_session(session_to_close)
                    )
                    _replacement_close_tasks[id(session_to_close)] = close_task
                    _ORPHAN_SESSION_REAPER_TASKS.add(close_task)
                    close_task.add_done_callback(
                        _settle_owned_replacement_close
                    )
                if voice_handoff_ticket is None:
                    try:
                        await close_task
                    except asyncio.CancelledError:
                        raise
                    except Exception as close_err:
                        logger.debug(
                            "Final Swap Sequence: %s close failed (ignored): %s",
                            stage,
                            close_err,
                        )
                    return
                remaining = max(
                    0.0,
                    voice_handoff_ticket.deadline
                    - asyncio.get_running_loop().time(),
                )
                # Even after the transaction deadline, give an ordinary close
                # one scheduler slice before cancellation.  This is bounded
                # cleanup (10 ms), not an extension of the handoff itself.
                done, _pending = await asyncio.wait(
                    {close_task},
                    timeout=remaining if remaining > 0.0 else 0.01,
                )
                if not done:
                    close_task.cancel()
                    done, _pending = await asyncio.wait(
                        {close_task},
                        timeout=0.1,
                    )
                if not done:
                    # A provider that swallows cancellation cannot be forcibly
                    # killed by asyncio.  Keep the task in the module registry
                    # for ownership/diagnostics, issue one final cancellation,
                    # and let the handoff return on its bounded path.
                    close_task.cancel()
                    logger.warning(
                        "Final Swap Sequence: %s close ignored cancellation; "
                        "retained in the owned cleanup registry",
                        stage,
                    )
                    return
                if close_task.cancelled():
                    return
                try:
                    close_task.result()
                except Exception as close_err:
                    logger.debug(
                        "Final Swap Sequence: %s close failed (ignored): %s",
                        stage,
                        close_err,
                    )

            if voice_handoff_ticket is not None:
                mark_irreversible = getattr(
                    self,
                    "_mark_voice_activation_handoff_irreversible",
                    None,
                )
                if not callable(mark_irreversible) or not mark_irreversible(
                    voice_handoff_ticket
                ):
                    await _abort_voice_handoff(
                        "handoff_irreversible_rejected"
                    )
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
            # Immediately hold the new session locally and clear self.pending_session.
            # Do so before any await: CancelledError during later cancellation/closure
            # invokes _cleanup_pending_session_resources(), which checks
            # self.pending_session and would close the new session's WebSocket unless cleared first.
            new_session = self.pending_session
            self.pending_session = None

            # ── Step 1: stop the old listener first ──
            # Complete before old_main_session.close(): ws.close() performs a closing handshake
            # awaiting the server CLOSE frame, effectively recv(). If the old task remains
            # in async-for recv(), this causes
            # "cannot call recv while another coroutine is already running recv" concurrency conflicts.
            if old_main_message_handler_task and not old_main_message_handler_task.done():
                old_main_message_handler_task.cancel()
                try:
                    if voice_handoff_ticket is None:
                        listener_wait = asyncio.wait_for(
                            old_main_message_handler_task,
                            timeout=2.0,
                        )
                        await _await_voice_handoff_step(
                            listener_wait,
                            stage="old listener stop",
                        )
                    else:
                        listener_timeout = min(
                            2.0,
                            max(
                                0.0,
                                voice_handoff_ticket.deadline
                                - asyncio.get_running_loop().time(),
                            ),
                        )
                        done, _pending = await _await_voice_handoff_step(
                            asyncio.wait(
                                {old_main_message_handler_task},
                                timeout=listener_timeout,
                            ),
                            stage="old listener stop",
                        )
                        if not done:
                            raise asyncio.TimeoutError
                        old_main_message_handler_task.result()
                    logger.info("Final Swap Sequence: Old message handler task stopped")
                except asyncio.TimeoutError:
                    # Old task still owns recv(); proceeding to close() repeats the concurrent-recv conflict.
                    # Close new_session to prevent WebSocket leaks, mark timeout, and abort swap.
                    old_listener_cancel_timed_out = True
                    logger.error("Final Swap Sequence: 旧 listener 取消超时，中止热切换")
                    await _retire_replacement_session(
                        new_session,
                        stage="listener-timeout replacement",
                    )
                    raise RuntimeError("旧 listener 取消超时，热切换中止")
                except asyncio.CancelledError:
                    # Swallow only the cancellation echo from the old listener just canceled.
                    # Outer cancellation of final_swap_task itself (_reset_preparation_state /
                    # end_session) also raises CancelledError at this await. Swallowing both
                    # lets swap survive cancellation (final_swap_task loses tracking after _wait_one's 2s timeout),
                    # then promote self.session without locking and corrupt/overwrite the new start_session winner.
                    # cancelling() > 0 means this task has an unacknowledged cancellation request; re-raise so
                    # the outer CancelledError handler closes new_session and resets state.
                    _swap_task = asyncio.current_task()
                    if _swap_task is not None and _swap_task.cancelling() > 0:
                        raise
                except Exception as e:
                    if voice_handoff_ticket is not None:
                        raise
                    logger.warning(f"Final Swap Sequence: Old task exited with error: {e}")

            _abort_if_passive_claim_retracted("before old session close")

            # Exclude Core voice-final delivery across the entire close+promote
            # window. The ASR dispatcher shares this lock, so a final either
            # completes before the old arbiter closes or sees the replacement
            # after promotion; it can no longer land between the two.
            core_voice_session_lock = getattr(
                self,
                "_core_voice_session_swap_lock",
                None,
            )
            if core_voice_session_lock is None:
                core_voice_session_lock = asyncio.Lock()
                self._core_voice_session_swap_lock = core_voice_session_lock

            @asynccontextmanager
            async def _hold_core_voice_session_lock():
                if voice_handoff_ticket is None:
                    async with core_voice_session_lock:
                        yield
                else:
                    if not _voice_handoff_is_current():
                        raise RuntimeError(
                            "voice activation handoff became stale before core "
                            "voice swap lock acquisition"
                        )
                    async with asyncio.timeout_at(
                        voice_handoff_ticket.deadline
                    ):
                        async with core_voice_session_lock:
                            if not _voice_handoff_is_current():
                                raise RuntimeError(
                                    "voice activation handoff became stale after "
                                    "core voice swap lock acquisition"
                                )
                            yield

            async with _hold_core_voice_session_lock():
                _abort_if_passive_claim_retracted(
                    "while waiting for core voice swap lock"
                )
                # ── Step 2: old task stopped; safely close the old session ──
                if old_main_session:
                    try:
                        await _await_voice_handoff_step(
                            self._close_owned_session(old_main_session),
                            stage="old session close",
                        )
                    except Exception as e:
                        logger.error(f"💥 Final Swap Sequence: Error closing old session: {e}")
                        if voice_handoff_ticket is not None:
                            raise

                _abort_if_passive_claim_retracted("before promote")

                # ── Cooperative cancellation checkpoint before promotion ──
                # Python 3.11 asyncio.wait_for (step 1) and some session.close()
                # implementations (step 2) swallow outer cancellation as a normal return when it arrives just after
                # their inner await completes. Any unacknowledged cancellation request must reach the
                # CancelledError handler below, closing new_session and resetting state.
                _swap_task = asyncio.current_task()
                if _swap_task is not None and _swap_task.cancelling() > 0:
                    raise asyncio.CancelledError()

                # ── Step 3: promote the new session ──
                # Mirror startup's strong CAS: any divergence means concurrent start/end_session
                # took ownership; overwriting self.session would orphan the winner.
                if voice_handoff_ticket is None:
                    async with self.lock:
                        _abort_if_passive_claim_retracted(
                            "while waiting for promote lock"
                        )
                        _promote_allowed = self.session is old_main_session
                        if _promote_allowed:
                            self.session = new_session
                else:
                    if not _voice_handoff_is_current():
                        raise RuntimeError(
                            "voice activation handoff became stale before "
                            "session promote lock acquisition"
                        )
                    async with asyncio.timeout_at(
                        voice_handoff_ticket.deadline
                    ):
                        async with self.lock:
                            if not _voice_handoff_is_current():
                                raise RuntimeError(
                                    "voice activation handoff became stale after "
                                    "session promote lock acquisition"
                                )
                            _abort_if_passive_claim_retracted(
                                "while waiting for promote lock"
                            )
                            _promote_allowed = self.session is old_main_session
                            if _promote_allowed:
                                self.session = new_session
                if voice_handoff_ticket is not None and not _voice_handoff_is_current(
                    allow_promoted=True
                ):
                    raise RuntimeError(
                        "voice activation handoff became stale during session promote"
                    )
            if not _promote_allowed:
                logger.warning("⚠️ Final Swap Sequence: promote 时 self.session 已被并发接管，中止 swap 并关闭 new_session")
                await _abort_voice_handoff("session_promote_rejected")
                await _retire_replacement_session(
                    new_session,
                    stage="rejected promotion replacement",
                )
                # Queue unchanged: _selected injected into new_session remains queued for
                # the takeover epoch's next hot swap, like _deferred.
                return

            # WebSocket-native image writes are only provisionally accepted.
            # The passive text prime sends a session.update after those writes;
            # its matching session.updated snapshot is the ordered Provider
            # boundary proving that the entire media+text prefix was processed.
            # Do not replace this with a timing grace period: an image error can
            # legitimately arrive later than a local sleep.
            if (
                _passive_media_outcome is not None
                and _passive_media_outcome["native_rejection_pending"]
            ):
                session_update_ack = new_session.expect_session_update_ack(
                    new_session.instructions
                )
                replacement_listener = asyncio.create_task(
                    new_session.handle_messages()
                )
                self.message_handler_task = replacement_listener
                rejection_wait = asyncio.create_task(
                    _passive_media_outcome["rejection_observed"].wait()
                )
                try:
                    media_barrier_timeout = (
                        _PASSIVE_MEDIA_SESSION_UPDATE_ACK_TIMEOUT_S
                    )
                    if voice_handoff_ticket is not None:
                        media_barrier_timeout = min(
                            media_barrier_timeout,
                            max(
                                0.0,
                                voice_handoff_ticket.deadline
                                - asyncio.get_running_loop().time(),
                            ),
                        )
                    done, _pending = await asyncio.wait(
                        {session_update_ack, rejection_wait},
                        timeout=media_barrier_timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not _voice_handoff_is_current(allow_promoted=True):
                        raise RuntimeError(
                            "voice activation handoff became stale during passive "
                            "media readiness barrier"
                        )
                finally:
                    _passive_media_outcome["settled"] = True
                    # Barrier settled; late image-rejection callbacks no longer matter. Their closures
                    # retain entire callbacks, potentially several ~13MB base64 images. Without detaching them,
                    # 60s expiration leaves hundreds of MB retained after several successful image callbacks.
                    for _image_event_id in _passive_media_outcome.get(
                        "rejection_event_ids", ()
                    ):
                        getattr(
                            new_session, "_inject_rejection_handlers", {}
                        ).pop(_image_event_id, None)
                    new_session.discard_session_update_ack(session_update_ack)
                    if not rejection_wait.done():
                        rejection_wait.cancel()
                    await asyncio.gather(rejection_wait, return_exceptions=True)
                media_prefix_committed = (
                    session_update_ack in done
                    and not session_update_ack.cancelled()
                    and session_update_ack.exception() is None
                    and not _passive_media_outcome["rejected"]
                )
                if not media_prefix_committed:
                    logger.warning(
                        "Final Swap Sequence: passive native media was rejected or its session-update barrier timed out; retiring promoted replacement before callback ACK"
                    )
                    await _abort_voice_handoff("passive_media_barrier_failed")
                    # Retire only the listener/session this swap created.  A
                    # concurrent start_session may have replaced both manager
                    # slots while the Provider barrier above was pending; reading
                    # self.message_handler_task here would capture and cancel that
                    # winner instead of this replacement's listener.
                    async with self.lock:
                        owned_before_retire = bool(
                            self.session is new_session
                            and self.message_handler_task is replacement_listener
                        )
                    if not replacement_listener.done():
                        replacement_listener.cancel()
                        await asyncio.gather(
                            replacement_listener,
                            return_exceptions=True,
                        )
                    await _retire_replacement_session(
                        new_session,
                        stage="passive-media replacement",
                    )
                    # Cancellation and close both yield.  Revalidate the complete
                    # ownership pair before touching shared lifecycle state: if a
                    # winner arrived during either await, local retirement above
                    # is the only cleanup this stale swap is allowed to perform.
                    async with self.lock:
                        still_owns_replacement = bool(
                            owned_before_retire
                            and self.session is new_session
                            and self.message_handler_task is replacement_listener
                        )
                        if still_owns_replacement:
                            self.session = None
                            self.message_handler_task = None
                            self.is_active = False
                    if not still_owns_replacement:
                        logger.info(
                            "Final Swap Sequence: rejected passive media replacement lost ownership during retirement; preserving concurrent session"
                        )
                        return
                    await self._close_independent_asr(
                        next_route_mode="blocked",
                    )
                    await self.send_status(json.dumps({
                        "code": "INTERNAL_UPDATE_FAILED",
                        "details": {
                            "error": (
                                "passive media session-update barrier failed"
                            ),
                        },
                    }))
                    await self.send_session_ended_by_server()
                    await self._reset_preparation_state(
                        clear_main_cache=True,
                        from_final_swap=True,
                    )
                    return
            # Promotion succeeded: the injected session is active and its next reply necessarily delivers
            # injected content. Only now remove _selected by object identity: items already removed by concurrent
            # voice delivery/retraction/sweeps/caps no-op here,
            # never deleting newly requeued entries sharing the same ID. _removed_extras records the actually
            # removed subset for restoration on the immediately following invalid-WebSocket fail-close exit.
            if _prime_selected_extras:
                _selected_obj_ids = {id(extra) for extra in _prime_selected_extras}
                _removed_extras = [
                    extra for extra in self.pending_extra_replies
                    if id(extra) in _selected_obj_ids
                ]
                if _removed_extras:
                    self.pending_extra_replies = [
                        extra for extra in self.pending_extra_replies
                        if id(extra) not in _selected_obj_ids
                    ]
                    # Snapshot delivery_ids with paired callbacks **now**: if a callback disappears by restore,
                    # consumption occurred during the window (successful voice delivery prunes both queues,
                    # while extras removal no-ops for already detached entries); restoration would duplicate announcements.
                    _removed_ids = {
                        extra.get("_callback_delivery_id")
                        for extra in _removed_extras
                        if isinstance(extra, dict) and extra.get("_callback_delivery_id")
                    }
                    _removed_cb_backed_ids = {
                        cb.get("_callback_delivery_id")
                        for cb in (getattr(self, "pending_agent_callbacks", None) or [])
                        if isinstance(cb, dict)
                        and cb.get("_callback_delivery_id") in _removed_ids
                    }
            # Symmetric dequeue for passive piggyback: also by object identity and only after successful
            # promotion. Entries consumed earlier by drain/sweeps during the window no-op here.
            _removed_passive_cbs = self._remove_swap_delivered_callbacks(
                _prime_selected_passive_cbs
            )
            self.response_backend = (
                'offline_vlm'
                if isinstance(self.session, OmniOfflineClient)
                else 'realtime'
            )
            self._require_context_append_current_delivery = True
            next_context_count_at_promote = len(self._snapshot_next_session_context_messages())
            await _await_voice_handoff_step(
                self._apply_pending_tts_route_after_swap(),
                stage="post-promote TTS route application",
                allow_promoted=True,
            )
            # The pending Omni session is now the active session. If its Core
            # provider changed, replace the independent ASR before replaying
            # cached microphone audio so one frame can never cross providers.
            await _await_voice_handoff_step(
                self._reconcile_independent_asr_after_core_change(),
                stage="post-promote ASR reconciliation",
                allow_promoted=True,
            )
            replaced_speech_id = self.current_speech_id
            self.current_speech_id = str(uuid4())
            # Session replacement does not begin a new turn: unfinished replies interrupted by old-session close()
            # still belong to this turn, and late completion callbacks must recognize them (see _carry_reply_turn).
            self._carry_reply_turn(replaced_speech_id)
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            self.session_start_time = datetime.now()
            self._session_turn_count = 0

            # Push current registry state immediately after promotion: the swap sequence
            # ``self.pending_session -> local new_session -> self.session``
            # crosses awaits where register_tool's _sync may miss both
            # pending_session (already moved to None) and self.session
            # (not assigned yet), leaving the promoted session without that registration's tools.
            try:
                await _await_voice_handoff_step(
                    self._sync_tools_to_active_session(),
                    stage="post-promote tool reconciliation",
                    allow_promoted=True,
                )
            except Exception as _sync_err:
                logger.warning("⚠️ final swap post-promote tool sync failed: %s", _sync_err)
                if voice_handoff_ticket is not None:
                    raise

            # Verify the new session's WebSocket remains valid; servers may disconnect during swap.
            if isinstance(self.session, OmniRealtimeClient) and not self.session.ws:
                # Old session is closed and cannot roll back; raise so except rebuilds.
                raise RuntimeError("新session的WebSocket在swap后已失效，热切换失败")

            transferred_next_context_count = (
                self.initial_next_session_context_snapshot_len
                + len(incremental_next_session_context)
            )
            consumed_next_context_count = await _await_voice_handoff_step(
                self._prime_late_next_session_context_after_swap(
                    transferred_next_context_count,
                    next_context_count_at_promote,
                    preceding=primed_context_sequence,
                ),
                stage="late context reconciliation",
                allow_promoted=True,
            )

            # ── Step 4: start the new listener ──
            if (
                self.session
                and hasattr(self.session, 'handle_messages')
                and (
                    not self.message_handler_task
                    or self.message_handler_task.done()
                )
            ):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

            # The replacement transport and listener are now installed, and
            # route reconciliation has completed under the ticket's one shared
            # deadline.  Only this commit may move the activation writer to the
            # promoted session and release its ordered output backlog.
            if voice_handoff_ticket is not None:
                if not _voice_handoff_is_current(allow_promoted=True):
                    raise RuntimeError(
                        "voice activation handoff became stale before commit"
                    )
                commit_voice_handoff = getattr(
                    self,
                    "_commit_voice_activation_handoff",
                    None,
                )
                if not callable(commit_voice_handoff):
                    raise RuntimeError(
                        "voice activation handoff commit hook is unavailable"
                    )
                async with asyncio.timeout_at(voice_handoff_ticket.deadline):
                    handoff_committed = await commit_voice_handoff(
                        voice_handoff_ticket
                    )
                if handoff_committed is not True or self.session is not new_session:
                    raise RuntimeError(
                        "voice activation handoff commit was rejected"
                    )

            # Clear stale state only after promotion, listener installation,
            # and any activation handoff commit have all succeeded.
            if (
                self.session is new_session
                and self.message_handler_task is not None
                and not self.message_handler_task.done()
            ):
                self.session_closed_by_server = False

            # ── Step 5: flush hot-swap audio buffers to the new session ──
            # Must follow promotion: _flush_hot_swap_audio_cache sends through self.session,
            # now the new session, ensuring correct audio delivery.
            await self._flush_hot_swap_audio_cache()
            self._consume_next_session_context_messages(consumed_next_context_count)

            # Reset all preparation states and clear the *main* cache now that it's fully transferred
            # pending_session was cleared immediately after swap; reset only other state here.
            await self._reset_preparation_state(
                clear_main_cache=True, from_final_swap=True)  # This will clear pending_*, is_preparing_new_session, etc. and self.message_cache_for_new_session
            logger.info("✅ 热切换完成")
            

        except asyncio.CancelledError:
            _swap_cancelled = True
            logger.info("Final Swap Sequence: Task cancelled.")
            self.is_hot_swap_imminent = False
            if voice_handoff_ticket is not None:
                abort_handoff = getattr(
                    self,
                    "_abort_voice_activation_handoff",
                    None,
                )
                if callable(abort_handoff):
                    await abort_handoff(
                        voice_handoff_ticket,
                        reason="final_swap_cancelled",
                    )
            # After self.pending_session = None, only a local variable holds new_session.
            # If swap is canceled before promotion, _cleanup_pending_session_resources no longer owns it;
            # close manually here to prevent WebSocket leaks.
            if new_session is not None and new_session is not self.session:
                await _retire_replacement_session(
                    new_session,
                    stage="cancelled replacement",
                )
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
            # Mirror except Exception's dead-session fail-close: cancellation after old-session
            # close() (which cleared realtime ws / text offline llm), or connection loss after promotion,
            # means restarting below would create a listener on a dead session.
            # Fail closed and let frontend reconnect. Preserve restart when pending-lifecycle errors trigger reset
            # while the old session remains alive.
            if self._swap_session_is_dead(self.session):
                self.session = None
                self.is_active = False
            # Pre-promotion cancellation: queue unchanged and _removed_extras empty; this call no-ops.
            # Post-promotion cancellation comes only from external reset/end_session/new start_session
            # prelude, which subsequently closes the promoted session, preventing injected delivery. Restore
            # entries removed at promotion to the queue head for the next hot swap.
            self._restore_undelivered_swap_extras(_removed_extras, _removed_cb_backed_ids)
            self._restore_undelivered_swap_passive_cbs(_removed_passive_cbs)
            # Do not restart listeners after promotion cancellation (_removed_extras / _removed_passive_cbs nonempty):
            # the canceller will close the promoted session, and restarting could play already-primed
            # content before closure, duplicating restored queued content. Without
            # a listener, server replies are never consumed for playback. Restart only recovers the old session's
            # lost listener after pre-promotion cancellation.
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and not _removed_extras and not _removed_passive_cbs and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        except Exception as e:
            logger.error(f"💥 Final Swap Sequence: Error: {e}")
            self.is_hot_swap_imminent = False
            if voice_handoff_ticket is not None:
                abort_handoff = getattr(
                    self,
                    "_abort_voice_activation_handoff",
                    None,
                )
                if callable(abort_handoff):
                    await abort_handoff(
                        voice_handoff_ticket,
                        reason="final_swap_failed",
                    )
            await self.send_status(json.dumps({"code": "INTERNAL_UPDATE_FAILED", "details": {"error": str(e)}}))
            # As above: manually close unpromoted new_session to prevent WebSocket leaks.
            if new_session is not None and new_session is not self.session:
                await _retire_replacement_session(
                    new_session,
                    stage="failed replacement",
                )
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
            if old_listener_cancel_timed_out:
                # Old-listener cancellation timed out: the task may exit only after this function returns,
                # making task.done() and replacement-listener creation unsafe and potentially leaving active-without-listener state.
                # Fail closed directly: clear session state for frontend reconnection rather than deadlocking later input.
                # Pre-promotion exit: queue unchanged, _selected still queued, no restoration needed.
                # Old session cannot close in place because recv() is stuck, causing concurrent conflicts (step 1),
                # but dropping references leaks WebSockets. Spawn detached reaping to await actual old-listener
                # exit and then best-effort close. If it never exits, behavior matches bare reference clearing
                # without regression; otherwise the WebSocket is reclaimed.
                _stuck_listener_task = old_main_message_handler_task
                _orphan_old_session = old_main_session

                async def _reap_old_session_after_listener_exit():
                    if _stuck_listener_task is not None:
                        try:
                            await _stuck_listener_task
                        except (asyncio.CancelledError, Exception):
                            pass  # Reaping cares only that it exited, regardless of how.
                    if _orphan_old_session is not None:
                        try:
                            await self._close_owned_session(_orphan_old_session)
                        except Exception as _reap_err:
                            logger.debug(f"Final Swap Sequence: 收尸关闭旧 session 失败（可忽略）: {_reap_err}")

                _reaper = asyncio.create_task(_reap_old_session_after_listener_exit())
                _ORPHAN_SESSION_REAPER_TASKS.add(_reaper)
                _reaper.add_done_callback(_ORPHAN_SESSION_REAPER_TASKS.discard)
                self.session = None
                self.message_handler_task = None
                self.is_active = False
                return
            # If self.session is dead (post-promotion realtime ws invalid / offline llm
            # cleared, or cancellation after old-session close), clear session state,
            # preventing is_active=True with dead connections from accepting later input.
            # This also handles actual removal at promotion followed by death of the injected session: restore
            # _removed_extras actually removed at promotion to the queue head for next hot swap.
            # Other failures with a living promoted session do not restore: injected content remains in context and
            # arrives with its next reply; restoration would duplicate it.
            if self._swap_session_is_dead(self.session):
                self.session = None
                self.is_active = False
                self._restore_undelivered_swap_extras(_removed_extras, _removed_cb_backed_ids)
                self._restore_undelivered_swap_passive_cbs(_removed_passive_cbs)
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
        finally:
            # Keep paired objects through all failure-recovery awaits. An extra
            # restored by an abort is still pending; a primed extra in a
            # surviving promoted session has actually completed this delivery,
            # whether the swap removed it or a concurrent path (a text drain)
            # took it out of the queue inside the prime→promote window.
            if (
                _prime_extra_callbacks
                and _prime_selected_extras
                and not _swap_cancelled
                and new_session is not None
                and self.session is new_session
                and not self._swap_session_is_dead(new_session)
            ):
                # Nuitka 4.2.x cannot clone comprehension scopes inside this
                # async finally block. Keep the same snapshots and ordering
                # with ordinary loops so all desktop targets can compile it.
                queued_extra_objects = set(map(id, self.pending_extra_replies))
                delivered_ids = set()
                for delivered_extra in _prime_selected_extras:
                    if isinstance(delivered_extra, dict) and id(delivered_extra) not in queued_extra_objects:
                        delivered_ids.add(delivered_extra.get("_callback_delivery_id"))
                delivered_cbs = []
                for delivered_cb in _prime_extra_callbacks:
                    if delivered_cb.get("_callback_delivery_id") in delivered_ids:
                        delivered_cbs.append(delivered_cb)
                queued_cb_objects = set(map(id, self.pending_agent_callbacks))
                queued_delivered_cbs = []
                for delivered_cb in delivered_cbs:
                    if id(delivered_cb) in queued_cb_objects:
                        queued_delivered_cbs.append(delivered_cb)
                self._remove_swap_delivered_callbacks(queued_delivered_cbs)
                # A paired callback outside the queue is held by a text turn
                # that drained it. Retract it so a precommit failure there does
                # not restore it (and its mirror) after the swap delivered them.
                for cb in delivered_cbs:
                    if id(cb) not in queued_cb_objects:
                        cb[DELIVERY_RETRACTED_KEY] = True
            self._release_swap_prime_passive_claims(_prime_extra_claimed)
            self._release_swap_prime_passive_claims(_passive_sel)
            self._purge_undeliverable_callbacks()
            self.is_hot_swap_imminent = False  # Always reset this flag
            if self.final_swap_task and self.final_swap_task.done():
                self.final_swap_task = None

    async def disconnected_by_server(self, *, expected_session=None, announce_disconnect=True):
        if expected_session is not None and expected_session is not self.session:
            logger.info("⏭️ disconnected_by_server: expected_session stale, skipping")
            return
        if announce_disconnect:
            await self.send_status(json.dumps({"code": "CHARACTER_DISCONNECTED", "details": {"name": self.lanlan_name}}))
        await self.send_session_ended_by_server()
        self.sync_message_queue.put({'type': 'system', 'data': 'API server disconnected'})
        await self.cleanup(expected_session=expected_session)

    def _queue_session_end_memory_barrier(self, callback):
        """Queue a terminal memory settlement followed by one local callback."""
        completion = asyncio.get_running_loop().create_future()
        self.sync_message_queue.put({
            'type': 'system',
            'data': 'session end',
            '_after_memory_settlement': callback,
            '_memory_settlement_done': completion,
        })
        return completion

    async def _wait_for_session_end_memory_barrier(
        self,
        completion,
        callback,
        *,
        timeout_seconds: float,
    ) -> None:
        """Wait for connector settlement, with an idempotent immediate fallback.

        The queue item retains the callback after a timeout.  Therefore a slow or
        temporarily stopped connector will run it again *after* its eventual
        memory write, closing the late-write window that motivated the barrier.
        """
        try:
            await asyncio.wait_for(
                asyncio.shield(completion),
                timeout=max(0.1, float(timeout_seconds)),
            )
            return
        except asyncio.TimeoutError:
            logger.warning(
                "[%s] memory settlement barrier timed out; clearing recent "
                "context now and leaving a queued post-settlement cleanup",
                self.lanlan_name,
            )

            # The caller no longer awaits this future after the fallback. Consume
            # a possible late callback exception to avoid an unhandled-future log;
            # the connector logs the failure at its source as well.
            def _consume_late_completion(future):
                if future.cancelled():
                    return
                try:
                    future.exception()
                except Exception:
                    pass

            completion.add_done_callback(_consume_late_completion)
            result = callback()
            if inspect.isawaitable(result):
                await result

    async def settle_session_memory_if_idle(
        self,
        callback,
        *,
        timeout_seconds: float = 15.0,
    ) -> bool:
        """Queue a memory barrier only while this manager is still idle."""
        self._init_session_lifecycle_state()
        async with self.lock:
            if self.is_active or self.is_starting:
                return False
            completion = self._queue_session_end_memory_barrier(callback)
            self._idle_memory_barriers.add(completion)
            completion.add_done_callback(self._idle_memory_barriers.discard)
        waiter = self._own_cleanup_task(self._wait_for_session_end_memory_barrier(
            completion, callback, timeout_seconds=timeout_seconds,
        ))
        await asyncio.shield(waiter)
        return True

    async def end_session(
        self, by_server=False, *, expected_session=None, reset_starting_count=True,
        after_memory_settlement=None, memory_settlement_timeout=15.0,
        preserve_pending_input=False,
    ):
        """Wait for safe handoff with bounded cleanup grace; retain slow resources."""
        task = self.request_end_session(
            by_server=by_server, expected_session=expected_session,
            reset_starting_count=reset_starting_count,
            after_memory_settlement=after_memory_settlement,
            memory_settlement_timeout=memory_settlement_timeout,
            preserve_pending_input=preserve_pending_input,
        )
        await self._wait_session_end(task)

    async def cleanup(self, expected_websocket=None, *, expected_session=None, reset_starting_count=True):
        self._init_session_lifecycle_state()
        socket = self.websocket if expected_websocket is None else expected_websocket
        if self.websocket is not None and self.websocket is not socket:
            return
        if expected_session is not None and self.session is not expected_session:
            return
        operation = self._start_operation
        preserve_replacement = (
            expected_websocket is None and operation is not None
            and operation.valid and not operation.finished.is_set()
            and operation.task is not asyncio.current_task()
            and (self.session is None or self.session is operation.previous_session)
        )
        if (reset_starting_count and operation is not None
                and operation.websocket is socket and not operation.finished.is_set()
                and not preserve_replacement):
            # Disconnect is terminal intent for this requester, unlike a
            # targetless server-side config cleanup. Revoke before the first
            # await so retirement owns its startup children and TTS resources.
            operation.valid = False
        generation = self._session_generation
        try:
            await self.end_session(by_server=True, expected_session=expected_session,
                                   reset_starting_count=reset_starting_count)
        except Exception as exc:
            # Disconnect is terminal for this socket even when physical close
            # failed. The retirement registry retains unsafe resources and
            # capacity; unbinding below still obeys socket/generation identity.
            logger.warning("Session disconnect cleanup failed: %s", exc)
        # A microphone pause uses end_session directly. Only a matching
        # disconnected transport may unbind the chat socket, after rechecking
        # both the connection and conversation ownership behind the lock.
        def unbind():
            if (not preserve_replacement and self._session_generation == generation
                    and self.websocket is socket):
                self.websocket = None
        if getattr(self, 'websocket_lock', None):
            async with self.websocket_lock:
                unbind()
        else:
            unbind()
