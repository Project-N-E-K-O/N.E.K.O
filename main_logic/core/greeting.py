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
"""Greeting flows for ``LLMSessionManager``: session/cat/new-character
greetings and avatar interaction handling.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import time
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient, _strip_nonverbal_directives
from main_logic.session_state import SessionEvent, session_reply_in_progress
from services.chat_sync.source import mark_turn_origin
from main_logic.startup_greeting_policy import (
    _STARTUP_GREETING_BURST_SECONDS,
    _STARTUP_GREETING_EARLIER_SAMPLES,
    _STARTUP_GREETING_MIN_GAP_SECONDS,
    _STARTUP_GREETING_RECALL_SECONDS,
    _STARTUP_GREETING_STRICT_SAMPLES,
    _STARTUP_GREETING_VARIANT_MEMORY,
    _select_startup_followup,
    _select_startup_greeting_variant,
    _startup_greeting_burst_age,
    split_startup_history_windows,
)
from memory.anti_repeat import get_anti_repeat_corpus
from memory.startup_greeting_history import get_startup_greeting_history
from config.prompts.avatar_interaction_contract import (
    normalize_avatar_interaction_payload,
)
from config.prompts.prompts_avatar_interaction import (
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
    _sanitize_avatar_interaction_text_context,
)
from utils.config_manager import get_config_manager
from utils.avatar_tool_store import (
    AvatarToolStoreError,
    get_avatar_tool_store,
    is_local_avatar_tool_id,
)
from utils.cloudsave_runtime import MaintenanceModeError
from utils.language_utils import normalize_language_code, get_global_language_full
from uuid import uuid4
from ._shared import (
    logger,
    _proactive_expected_sid,
    _proactive_published_text_chunks,
)


class GreetingMixin:
    """Greeting and avatar-interaction methods (see module docstring)."""

    @staticmethod
    def _greeting_locale_keys(language: str | None) -> tuple[str, str]:
        """Return the short prompt locale and full regional holiday locale."""
        selected_language = language or get_global_language_full()
        return (
            normalize_language_code(selected_language, format='short'),
            normalize_language_code(selected_language, format='full'),
        )

    def _remember_avatar_interaction_id(self, interaction_id: str) -> None:
        if interaction_id in self._recent_avatar_interaction_id_set:
            return
        if self._recent_avatar_interaction_ids.maxlen and len(self._recent_avatar_interaction_ids) >= self._recent_avatar_interaction_ids.maxlen:
            oldest_id = self._recent_avatar_interaction_ids[0]
            self._recent_avatar_interaction_id_set.discard(oldest_id)
        self._recent_avatar_interaction_ids.append(interaction_id)
        self._recent_avatar_interaction_id_set.add(interaction_id)

    @staticmethod
    def _avatar_interaction_ingress_time(payload: dict) -> float:
        captured_at = payload.get("_user_input_ingress_time")
        if isinstance(captured_at, (int, float)):
            return float(captured_at)
        return time.time()

    @staticmethod
    def _avatar_interaction_contract_payload(payload: dict) -> dict:
        """Hide server transport metadata from the strict public contract."""
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if key not in {
                "_user_input_ingress_time",
                "_avatar_interaction_ingress_reserved",
            }
        }

    @staticmethod
    def _resolve_local_avatar_tool_prompt_record(raw: dict, record: dict) -> dict | None:
        version = record.get("recordVersion")
        if version == 2:
            change_items = record.get("imageChange", {}).get("items")
            change_index = raw.get("change_index")
            if (
                "image_id" in raw
                or not isinstance(change_items, list)
                or isinstance(change_index, bool)
                or not isinstance(change_index, int)
                or change_index < 0
                or change_index >= len(change_items)
            ):
                raise ValueError("invalid local change index")
            meaning = change_items[change_index]["meaning"]
        elif version == 3:
            image_id = raw.get("image_id")
            images = record.get("images")
            if "change_index" in raw or not isinstance(images, list):
                raise ValueError("invalid local image ID")
            image = next((item for item in images if item["id"] == image_id), None)
            if image is None:
                raise ValueError("invalid local image ID")
            meaning = image["meaning"]
        else:
            raise ValueError("invalid local record version")
        special = record.get("interaction", {}).get("special")
        has_special_fact = "special_triggered" in raw
        if bool(special) != has_special_fact:
            raise ValueError("local special fact does not match record")
        selected_meaning = (
            special["meaning"] if special and raw["special_triggered"] is True else meaning
        )
        if not selected_meaning:
            return None
        return {
            "name": record["name"],
            "meaning": selected_meaning,
        }

    def note_avatar_interaction_ingress(self, payload: dict) -> bool:
        """Expose validated avatar engagement before background dispatch."""
        raw = normalize_avatar_interaction_payload(
            self._avatar_interaction_contract_payload(payload),
            sanitize_text_context=_sanitize_avatar_interaction_text_context,
        )
        if not raw:
            return False
        interaction_id = raw["interaction_id"]
        if interaction_id in self._recent_avatar_interaction_id_set:
            return False
        # The WebSocket dispatch loop calls this synchronously. Reserve before
        # scheduling the handler so a second frame with the same ID is already
        # a duplicate even when the first task has not started yet.
        self.note_user_engagement(
            at=self._avatar_interaction_ingress_time(payload)
        )
        self._remember_avatar_interaction_id(interaction_id)
        return True

    async def handle_avatar_interaction(self, payload: dict) -> dict:
        raw_interaction_id = str(payload.get("interaction_id") or payload.get("interactionId") or "").strip() if isinstance(payload, dict) else ""
        raw = normalize_avatar_interaction_payload(
            self._avatar_interaction_contract_payload(payload),
            sanitize_text_context=_sanitize_avatar_interaction_text_context,
        )
        if not raw:
            logger.debug("[%s] handle_avatar_interaction: ignored invalid payload", self.lanlan_name)
            await self.send_avatar_interaction_ack(raw_interaction_id, False, "invalid_payload")
            return {"accepted": False, "reason": "invalid_payload"}

        local_record = None
        local_prompt_record = None
        local_store = None
        if is_local_avatar_tool_id(raw["tool_id"]):
            local_store = get_avatar_tool_store(self._config_manager)
            try:
                local_record = await asyncio.to_thread(
                    local_store.read_record,
                    raw["tool_id"],
                    verify_resources=False,
                )
            except (AvatarToolStoreError, MaintenanceModeError, OSError):
                logger.debug(
                    "[%s] handle_avatar_interaction: missing or invalid local tool=%s",
                    self.lanlan_name,
                    raw["tool_id"],
                )
                await self.send_avatar_interaction_ack(
                    raw_interaction_id, False, "invalid_payload"
                )
                return {"accepted": False, "reason": "invalid_payload"}
            if raw["tool_revision"] != local_store.record_revision(local_record):
                logger.debug(
                    "[%s] handle_avatar_interaction: stale local tool revision=%s",
                    self.lanlan_name,
                    raw["tool_id"],
                )
                await self.send_avatar_interaction_ack(
                    raw_interaction_id, False, "stale_tool_revision"
                )
                return {"accepted": False, "reason": "stale_tool_revision"}
            try:
                local_prompt_record = self._resolve_local_avatar_tool_prompt_record(
                    raw, local_record
                )
            except (KeyError, TypeError, ValueError):
                await self.send_avatar_interaction_ack(
                    raw_interaction_id, False, "invalid_payload"
                )
                return {"accepted": False, "reason": "invalid_payload"}

        interaction_id = raw["interaction_id"]
        ingress_reserved = (
            payload.get("_avatar_interaction_ingress_reserved") is True
        )

        if (
            interaction_id in self._recent_avatar_interaction_id_set
            and not ingress_reserved
        ):
            logger.debug("[%s] handle_avatar_interaction: duplicate interaction_id=%s", self.lanlan_name, interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "duplicate")
            return {"accepted": False, "reason": "duplicate", "interaction_id": interaction_id}

        if not ingress_reserved:
            self.note_user_engagement(
                at=self._avatar_interaction_ingress_time(payload)
            )

        # Serialize the cooldown decision with strict local-resource
        # verification and cooldown commit. A failed verification releases the
        # gate without consuming the next valid interaction's cooldown slot.
        #
        # Cooldown hits are frequent during repeated clicks, and acknowledgments use WebSocket. Previously awaiting them
        # inside the gate let downstream backpressure queue every subsequent interaction, including
        # the first eligible interaction after cooldown. Keep decision/dedup registration atomic inside the lock,
        # but send acknowledgments outside it.
        cooldown_hit = False
        gate_rejection: tuple[str, str] | None = None
        async with self._avatar_interaction_gate_lock:
            now_ms = int(time.time() * 1000)
            if now_ms - self._last_avatar_interaction_at < self.avatar_interaction_cooldown_ms:
                self._remember_avatar_interaction_id(interaction_id)
                cooldown_hit = True

            # Only an event that can pass the duplicate/cooldown gates pays the
            # full resource-digest cost. Re-read and resolve from the verified
            # record so prompt data never comes from the lightweight first read.
            if not cooldown_hit and local_store is not None:
                try:
                    local_record = await asyncio.to_thread(
                        local_store.read_record,
                        raw["tool_id"],
                        verify_resources=True,
                    )
                    if raw["tool_revision"] != local_store.record_revision(local_record):
                        gate_rejection = (raw_interaction_id, "stale_tool_revision")
                    else:
                        local_prompt_record = self._resolve_local_avatar_tool_prompt_record(
                            raw, local_record
                        )
                except (
                    AvatarToolStoreError,
                    MaintenanceModeError,
                    OSError,
                    KeyError,
                    TypeError,
                    ValueError,
                ):
                    logger.debug(
                        "[%s] handle_avatar_interaction: local tool changed or failed resource verification=%s",
                        self.lanlan_name,
                        raw["tool_id"],
                    )
                    gate_rejection = (raw_interaction_id, "invalid_payload")

            if not cooldown_hit and gate_rejection is None and (local_store is None or local_prompt_record is not None):
                self._remember_avatar_interaction_id(interaction_id)
                self._last_avatar_interaction_at = now_ms

        # No awaits inside the gate: rejected acknowledgments use WebSocket, so downstream backpressure would block
        # every later interaction on this lock. Keep decisions/dedup atomic inside, sending all receipts afterward.
        if gate_rejection is not None:
            rejected_id, reason = gate_rejection
            await self.send_avatar_interaction_ack(rejected_id, False, reason)
            return {"accepted": False, "reason": reason}

        if cooldown_hit:
            logger.debug("[%s] handle_avatar_interaction: cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "cooldown")
            return {"accepted": False, "reason": "cooldown", "interaction_id": interaction_id}

        if local_store is not None and local_prompt_record is None:
            await self.send_avatar_interaction_ack(interaction_id, False, "no_meaning")
            return {"accepted": False, "reason": "no_meaning", "interaction_id": interaction_id}

        if self.is_active and isinstance(self.session, OmniRealtimeClient):
            logger.debug("[%s] handle_avatar_interaction: voice session active, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "voice_session_active")
            return {"accepted": False, "reason": "voice_session_active", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if not self._has_connected_websocket():
                logger.warning("[%s] handle_avatar_interaction: no connected websocket, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "no_websocket")
                return {"accepted": False, "reason": "no_websocket", "interaction_id": interaction_id}
            try:
                logger.info("[%s] handle_avatar_interaction: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except asyncio.CancelledError as exc:
                if not self._consume_start_retirement_cancellation(exc):
                    raise
                logger.info("[%s] handle_avatar_interaction: auto start_session cancelled", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "session_start_cancelled")
                return {"accepted": False, "reason": "session_start_cancelled", "interaction_id": interaction_id}
            except Exception as e:
                logger.warning("[%s] handle_avatar_interaction: auto start_session failed: %s", self.lanlan_name, e)
                await self.send_avatar_interaction_ack(interaction_id, False, "session_start_failed")
                return {"accepted": False, "reason": "session_start_failed", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            logger.warning("[%s] handle_avatar_interaction: session is not text mode after start, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "not_text_session")
            return {"accepted": False, "reason": "not_text_session", "interaction_id": interaction_id}

        instruction = _build_avatar_interaction_instruction(
            getattr(self, "user_language", None),
            self.lanlan_name,
            self.master_name,
            raw,
            local_prompt_record,
        )
        memory_meta = _build_avatar_interaction_memory_meta(
            getattr(self, "user_language", None),
            raw,
            self.master_name,
            local_prompt_record,
        )
        memory_note = memory_meta["memory_note"]
        delivered = False

        async with self._proactive_write_lock:
            if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
                await self.send_avatar_interaction_ack(interaction_id, False, "session_changed")
                return {"accepted": False, "reason": "session_changed", "interaction_id": interaction_id}
            # A guard pause drops _is_responding while its reply is still live;
            # prompt_ephemeral would decline to start over it, after the speech
            # id below had already been rotated under that reply.
            if session_reply_in_progress(self.session):
                logger.debug("[%s] handle_avatar_interaction: text session busy, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "busy")
                return {"accepted": False, "reason": "busy", "interaction_id": interaction_id}
            speak_now_ms = int(time.time() * 1000)
            if speak_now_ms - self._last_avatar_interaction_speak_at < self.avatar_interaction_speak_cooldown_ms:
                logger.debug("[%s] handle_avatar_interaction: speak cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
                await self.send_avatar_interaction_ack(interaction_id, False, "speak_cooldown")
                return {"accepted": False, "reason": "speak_cooldown", "interaction_id": interaction_id}

            async with self.lock:
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False

            if hasattr(self.session, 'update_max_response_length'):
                self.session.update_max_response_length(self._get_text_guard_max_length())

            # Backend tagging: attach avatar-interaction metadata to the session manager,
            # delivering atomically with turn end when prompt_ephemeral triggers handle_response_complete.
            # Avoid separate sync_message_queue control messages, whose ordering relative to turn end
            # could misclassify this turn as proactive.
            turn_meta = self._pending_turn_meta = {
                "kind": "avatar_interaction",
                "interaction_id": interaction_id,
                "memory_note": memory_note,
                "memory_dedupe_key": memory_meta["memory_dedupe_key"],
                "memory_dedupe_rank": memory_meta["memory_dedupe_rank"],
            }

            current_turn_id = self.current_speech_id
            # Avatar/tool text must stay excluded even if a reset precedes its turn end.
            mark_turn_origin(self, current_turn_id, 'avatar')
            # This reply's snapshot (see _shared._ReplyTurn): completion uses its own metadata,
            # never shared fields that may belong to another turn by then.
            reply_turn = self._begin_reply_turn(
                speech_id=current_turn_id,
                meta=self._pending_turn_meta,
            )

            async def response_done_callback() -> None:
                await self.handle_response_complete(reply_turn=reply_turn)

            # Proactive race guard: new user input during prompt_ephemeral changes
            # current_speech_id and clears TTS queues; text deltas from this path
            # must match _proactive_expected_sid in handle_text_data/handle_output_transcript,
            # discarding mismatches with the same protection as trigger_agent_callbacks.
            _sid_token = _proactive_expected_sid.set(current_turn_id)
            try:
                try:
                    reply_turn.session = self.session
                    delivered = await self.session.prompt_ephemeral(
                        instruction,
                        completion_mode="response",
                        persist_response=False,
                        response_done_callback=response_done_callback,
                        reply_owner=reply_turn,
                    )
                except Exception as e:
                    logger.exception(
                        "[%s] handle_avatar_interaction: prompt_ephemeral failed interaction_id=%s: %s",
                        self.lanlan_name,
                        interaction_id,
                        e,
                    )
                    # prompt_ephemeral exceptions do not trigger handle_response_complete,
                    # so explicitly clear metadata to avoid leaking into the next turn.
                    self._pending_turn_meta = None
                    await self.send_avatar_interaction_ack(interaction_id, False, "error")
                    return {"accepted": False, "reason": "error", "interaction_id": interaction_id}
            finally:
                _proactive_expected_sid.reset(_sid_token)
                self._end_reply_turn(reply_turn)

            # If current_speech_id changed while the prompt ran because the user took over,
            # this avatar response is undelivered: never attach its metadata to the user's new turn end,
            # and acknowledge interrupted rather than delivered.
            interrupted = self.current_speech_id != current_turn_id
            accepted = bool(delivered) and not interrupted
            if interrupted:
                self._pending_turn_meta = None
            # Still ours after the call: neither handle_response_complete nor an
            # interrupted or displaced turn's close consumed it, so no reply ran
            # (prompt_ephemeral declines to start over another reply still in
            # progress). Drop it here, or it lands on the next, unrelated turn end.
            if self._pending_turn_meta is turn_meta:
                self._pending_turn_meta = None
            if accepted:
                self._last_avatar_interaction_speak_at = int(time.time() * 1000)
            ack_reason = "delivered" if accepted else ("interrupted" if interrupted else "empty_response")
            await self.send_avatar_interaction_ack(
                interaction_id,
                accepted,
                ack_reason,
                turn_id=current_turn_id if accepted else "",
            )

        # Without acceptance, handle_response_complete may not run, or interruption may already clear it on the user's
        # new turn; residual metadata could then be consumed by the next
        # turn end. Clear defensively here. With accepted=True, handle_response_complete
        # already consumed metadata, making this an idempotent no-op.
        if not accepted:
            self._pending_turn_meta = None

        if accepted:
            if "action_id" in raw:
                logger.info(
                    "[%s] handle_avatar_interaction: delivered interaction_id=%s tool=%s action=%s",
                    self.lanlan_name,
                    interaction_id,
                    raw["tool_id"],
                    raw["action_id"],
                )
            else:
                logger.info(
                    "[%s] handle_avatar_interaction: delivered interaction_id=%s tool=%s round=%s/%s result=%s",
                    self.lanlan_name,
                    interaction_id,
                    raw["tool_id"],
                    raw["user_gesture"],
                    raw["avatar_gesture"],
                    raw["round_result"],
                )
            return {"accepted": True, "interaction_id": interaction_id}

        logger.debug(
            "[%s] handle_avatar_interaction: not accepted interaction_id=%s reason=%s",
            self.lanlan_name, interaction_id, ack_reason,
        )
        return {"accepted": False, "reason": ack_reason, "interaction_id": interaction_id}

    async def trigger_greeting(self, *, render_language: str | None = None) -> None:
        """On first connect or character switch, trigger a proactive greeting based on the gap since the last conversation.

        Flow: query memory_server for the gap → build the guiding prompt → proactively start a text session → deliver.
        """
        greeting_name = self.lanlan_name
        greeting_memory_server_port = self.memory_server_port
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── Guard: skip greeting while a voice session starts or remains active ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        # ── Guard: skip greeting during takeover ──
        # Symmetric with trigger_voice_proactive_nudge / trigger_agent_callbacks.
        # Ordinary-chat output is muted by handlers during takeover, so greetings would only
        # waste holiday budgets and save LLM replies that can never reach the user.
        if self._takeover_active:
            logger.info("[%s] trigger_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # Reuse internal_http_client on session startup, avoiding AsyncClient construction overhead
        # of 157ms on idle Windows or up to 1.1s under event-loop load; see utils/internal_http_client.py
        try:
            from utils.internal_http_client import get_internal_http_client
            _mem_client = get_internal_http_client()
            resp = await _mem_client.get(
                f"http://127.0.0.1:{greeting_memory_server_port}/last_conversation_gap/{greeting_name}",
                timeout=2.0,
            )
            if not resp.is_success:
                logger.warning("[%s] trigger_greeting: memory server returned %s", self.lanlan_name, resp.status_code)
                return
            gap_seconds = resp.json().get("gap_seconds", -1)
        except Exception as e:
            logger.warning("[%s] trigger_greeting: failed to query gap: %s", self.lanlan_name, e)
            return

        if gap_seconds < _STARTUP_GREETING_MIN_GAP_SECONDS:  # Below 15 minutes: do not trigger
            logger.debug("[%s] trigger_greeting: gap %.0fs < 15min, skipping", self.lanlan_name, gap_seconds)
            return

        # Normal anti-repeat foreground TTL is 10 minutes, shorter than greeting's 15-minute threshold,
        # so it cannot rotate same-day startup greetings. Preload dedicated committed history here; only delivered
        # text is recorded by callbacks below. Read the entire 3-day recall window once, splitting in memory into
        # a strong 1-day layer and weaker 1–3-day layer, avoiding two disk reads.
        greeting_history = get_startup_greeting_history()
        anti_repeat_corpus = get_anti_repeat_corpus()
        observed_at = time.time()
        try:
            await asyncio.gather(
                greeting_history.apreload(greeting_name),
                anti_repeat_corpus.apreload(greeting_name),
            )
            recall_greetings = greeting_history.recent(
                greeting_name,
                now=observed_at,
                max_age_seconds=_STARTUP_GREETING_RECALL_SECONDS,
            )
        except Exception as e:
            logger.debug(
                "[%s] trigger_greeting: startup history unavailable: %s",
                self.lanlan_name,
                e,
            )
            recall_greetings = []
        recent_greetings, earlier_greetings = split_startup_history_windows(
            recall_greetings,
            observed_at=observed_at,
        )

        # Speak at most once per logical startup burst. The gap gate already covers 15 minutes; conservatively
        # cover repeated startup/multiwindow reconnects over 15–30 minutes here. Atomic reservation before delivery
        # rechecks committed history, preventing staggered startup tasks sharing the same empty snapshot.
        burst_age = _startup_greeting_burst_age(
            recall_greetings,
            observed_at=observed_at,
            last_user_engagement_at=getattr(
                self, "last_user_engagement_time", None
            ),
        )
        if burst_age is not None:
            logger.info(
                "[%s] trigger_greeting: startup burst suppressed (age=%.0fs)",
                self.lanlan_name,
                burst_age,
            )
            return

        # ── Recheck after awaiting: users may activate the microphone during memory lookup ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session appeared during gap query, skipping", self.lanlan_name)
            return

        _lang, _holiday_lang = self._greeting_locale_keys(
            self.user_language or render_language
        )
        from config.prompts.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
        from config.prompts.prompts_proactive import get_startup_greeting_guidance
        from utils.time_format import format_elapsed as _format_elapsed
        from utils.holiday_cache import preview_holiday_or_weekend_hint, commit_holiday_or_weekend_hint
        # Keep the region for startup prompt selection so Traditional Chinese
        # reaches the dedicated zh-TW templates. Formatting helpers that only
        # support short codes continue to use ``_lang``.
        _prompt_lang = _holiday_lang
        template = get_greeting_prompt(gap_seconds, _prompt_lang)
        if not template:
            return

        # Confirm delivery availability before consuming holiday budgets, avoiding wasted counts on failed startup
        # An existing text session is reused even while a reply is in progress
        # on it (live, guard-paused or awaiting its completion): the claim
        # below (try_start_proactive) refuses then, while start_session would
        # end that session and cut its reply.
        if not isinstance(self.session, OmniOfflineClient):
            # No session or nontext session → start one proactively
            # ── Recheck before startup to avoid competing with imminent voice sessions ──
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except asyncio.CancelledError as exc:
                if not self._consume_start_retirement_cancellation(exc):
                    raise
                return
            except Exception as e:
                logger.warning("[%s] trigger_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # Reflection endpoint is read-only: selection does not start synthesis and
        # does not consume cooldown.  We mark one candidate surfaced only from the
        # committed-text callback below.  Topic keys are held off for the whole
        # 3-day recall window — re-raising the same remembered topic reads as far
        # more repetitive than reusing a generic opening shape, which only rotates
        # against the 1-day strict layer.
        recently_used_topic_keys = {
            record.topic_key for record in recall_greetings if record.topic_key
        }
        startup_followup = None
        memory_variant_available = all(
            record.variant_key != _STARTUP_GREETING_VARIANT_MEMORY
            for record in recent_greetings
        )
        if memory_variant_available:
            try:
                followup_resp = await _mem_client.get(
                    f"http://127.0.0.1:{greeting_memory_server_port}/followup_topics/{greeting_name}",
                    timeout=5.0,
                )
                if followup_resp.is_success:
                    startup_followup = _select_startup_followup(
                        followup_resp.json().get("topics", []),
                        recently_used_topic_keys=recently_used_topic_keys,
                    )
                else:
                    logger.debug(
                        "[%s] trigger_greeting: followup topics returned %s",
                        greeting_name,
                        followup_resp.status_code,
                    )
            except Exception as e:
                # Memory enrichment is optional; a transient reflection failure must
                # never suppress a safe ordinary greeting.
                logger.debug(
                    "[%s] trigger_greeting: followup topics unavailable: %s",
                    greeting_name,
                    e,
                )

        startup_variant = _select_startup_greeting_variant(
            recent_greetings,
            has_followup=startup_followup is not None,
        )
        if startup_variant != _STARTUP_GREETING_VARIANT_MEMORY:
            startup_followup = None
        surfaced_topic_key = startup_followup[0] if startup_followup else None
        startup_memory_cue = startup_followup[1] if startup_followup else ""

        # Delivery ready: build instructions, previewing holiday budgets without consumption
        elapsed = _format_elapsed(_lang, gap_seconds)
        time_hint = get_time_of_day_hint(_prompt_lang).format(master=self.master_name)

        _holiday_token = None
        try:
            holiday_hint_text, _holiday_token = await preview_holiday_or_weekend_hint(
                _holiday_lang,
                self.lanlan_name,
            )
        except Exception as e:
            logger.debug("[%s] trigger_greeting: holiday hint failed: %s", self.lanlan_name, e)
            holiday_hint_text = None
        holiday_hint = (holiday_hint_text + '\n') if holiday_hint_text else ''

        instruction = template.format(
            elapsed=elapsed, name=greeting_name, master=self.master_name,
            time_hint=time_hint, holiday_hint=holiday_hint,
        )
        instruction += "\n" + get_startup_greeting_guidance(
            gap_seconds,
            _prompt_lang,
            variant_key=startup_variant,
            master=self.master_name,
            memory_cue=startup_memory_cue,
            recent_openings=tuple(
                record.text
                for record in recent_greetings[:_STARTUP_GREETING_STRICT_SAMPLES]
            ),
            earlier_openings=tuple(
                record.text
                for record in earlier_greetings[:_STARTUP_GREETING_EARLIER_SAMPLES]
            ),
        )

        async def _record_committed_side_effects() -> None:
            if surfaced_topic_key:
                try:
                    surfaced_resp = await _mem_client.post(
                        f"http://127.0.0.1:{greeting_memory_server_port}/record_surfaced/{greeting_name}",
                        json={"reflection_ids": [surfaced_topic_key]},
                        timeout=5.0,
                    )
                    if not surfaced_resp.is_success:
                        logger.debug(
                            "[%s] trigger_greeting: record_surfaced returned %s",
                            self.lanlan_name,
                            surfaced_resp.status_code,
                        )
                except Exception as e:
                    logger.debug(
                        "[%s] trigger_greeting: record_surfaced failed: %s",
                        self.lanlan_name,
                        e,
                    )
            if _holiday_token is not None:
                try:
                    await asyncio.to_thread(
                        commit_holiday_or_weekend_hint,
                        greeting_name,
                        _holiday_token,
                    )
                except Exception as e:
                    logger.debug(
                        "[%s] trigger_greeting: holiday commit failed: %s",
                        self.lanlan_name,
                        e,
                    )

        greeting_commit_seen = False
        greeting_reservation_token = None
        greeting_published_text_chunks: list[str] = []

        def _on_greeting_committed(committed_text: str) -> None:
            nonlocal greeting_commit_seen
            if greeting_commit_seen:
                return
            published_text = _strip_nonverbal_directives(
                "".join(greeting_published_text_chunks)
            ).strip()
            # prompt_ephemeral can still build a local assistant_message after a
            # user turn has stolen the speech id; the transport drops those deltas.
            # A prefix recorded at send_lanlan_response's sync-queue boundary is
            # irrevocable commit evidence even if the final sid was preempted.
            if (
                greeting_reservation_token is None
                or (
                    not published_text
                    and (
                        self.state.is_proactive_preempted()
                        or self.current_speech_id != proactive_sid
                    )
                )
            ):
                logger.info(
                    "[%s] trigger_greeting: committed-text bookkeeping rejected "
                    "after sid preemption",
                    self.lanlan_name,
                )
                return
            greeting_commit_seen = True
            bookkeeping_text = published_text or committed_text

            # Both in-memory stages happen before prompt_ephemeral emits its
            # terminal callback.  Disk writes are detached so a cancellation after
            # visible text cannot make the greeting eligible again.
            try:
                staged_greeting = greeting_history.stage_committed(
                    greeting_name,
                    bookkeeping_text,
                    variant_key=startup_variant,
                    topic_key=surfaced_topic_key,
                    reservation_token=greeting_reservation_token,
                )
                greeting_history.flush_staged_detached(staged_greeting)
            except Exception as e:
                logger.debug(
                    "[%s] trigger_greeting: startup history commit skipped: %s",
                    self.lanlan_name,
                    e,
                )
            try:
                staged_anti_repeat = anti_repeat_corpus.stage_output(
                    greeting_name,
                    bookkeeping_text,
                    is_proactive=True,
                )
                anti_repeat_corpus.flush_staged_detached(staged_anti_repeat)
            except Exception as e:
                logger.debug(
                    "[%s] trigger_greeting: anti-repeat commit skipped: %s",
                    self.lanlan_name,
                    e,
                )

            if surfaced_topic_key or _holiday_token is not None:
                self._fire_task(_record_committed_side_effects())

        logger.debug(
            "[%s] trigger_greeting: instruction built "
            "(len=%d variant=%s has_memory_cue=%s strict=%d earlier=%d)",
            greeting_name,
            len(instruction),
            startup_variant,
            bool(startup_memory_cue),
            min(len(recent_greetings), _STARTUP_GREETING_STRICT_SAMPLES),
            min(len(earlier_greetings), _STARTUP_GREETING_EARLIER_SAMPLES),
        )
        logger.info("[%s] trigger_greeting: gap=%.0fs elapsed=%s, delivering", greeting_name, gap_seconds, elapsed)

        # ── Final pre-delivery check: voice may take over during instruction construction and holiday-hint awaits ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # Atomic SM claim, mutually exclusive with trigger_agent_callbacks / /api/proactive_chat
        # and rejecting replies already in progress (session_reply_in_progress)
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                # Recheck after locking: voice may start while awaiting _proactive_write_lock
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    # Sticky-preemption recheck: USER_INPUT atomically changes the flag and user sid within
                    # this lock. preempt=True means the user already claimed the turn,
                    # so current_speech_id must not be overwritten with a proactive sid.
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                if (
                    self.state.is_proactive_preempted()
                    or self.current_speech_id != proactive_sid
                ):
                    logger.info(
                        "[%s] trigger_greeting: preempted after phase claim, skipping",
                        self.lanlan_name,
                    )
                    return
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                _published_text_token = _proactive_published_text_chunks.set(
                    greeting_published_text_chunks
                )
                try:
                    # Guard stale sessions: after start_session at 4429, several awaits occurred
                    # (holiday hint / try_start_proactive /
                    # _proactive_write_lock / self.lock / state.fire ×2），
                    # while cleanup / disconnected_by_server / voice-switch reconstruction
                    # may set self.session=None or replace it with OmniRealtimeClient.
                    # Direct self.session.prompt_ephemeral can raise AttributeError,
                    # killing the whole trigger_greeting task (see the crash trace during concurrent
                    # session reconstruction after voice switching, where trigger_greeting encountered self.session=None).
                    # Capture a local reference and type-check first; stale sessions
                    # silently skip, while outer finally emits PROACTIVE_DONE so the SM
                    # cannot remain stuck in PHASE2 / CLAIM.
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    greeting_reservation_token = greeting_history.try_reserve(
                        greeting_name,
                        now=time.time(),
                        burst_seconds=_STARTUP_GREETING_BURST_SECONDS,
                        last_user_engagement_at=getattr(
                            self, "last_user_engagement_time", None
                        ),
                    )
                    if greeting_reservation_token is None:
                        logger.info(
                            "[%s] trigger_greeting: startup reservation denied",
                            self.lanlan_name,
                        )
                        return
                    try:
                        delivered = await session_ref.prompt_ephemeral(
                            instruction,
                            on_committed_text=_on_greeting_committed,
                        )
                    finally:
                        greeting_history.release_reservation(
                            greeting_name, greeting_reservation_token
                        )
                finally:
                    _proactive_published_text_chunks.reset(_published_text_token)
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_cat_greeting(
        self,
        duration_seconds: float,
        tier: str,
        was_auto: bool,
        episode: dict | None = None,
        *,
        render_language: str | None = None,
    ) -> None:
        """When transforming back from cat form to catgirl (asking her back), trigger one dedicated greeting based on "behavior (tier) × time spent as a cat".

        Dual of trigger_greeting, but with independent timing: it doesn't query
        last_conversation_gap, instead using the server-observed cat-dwell
        duration passed in by the websocket router (the datetime gap is "since the last
        conversation", this is "how long she stayed a cat" — two clocks that don't
        interfere). A valid episode has already passed the router enum
        allowlist and remains request-local; it becomes the factual cat-form
        scene for this one prompt without altering guards or persistent state.
        Cat Mind activity may enrich a return after the normal dwell threshold,
        but it never shortens or bypasses that threshold.
        Flow: pick the behavior/duration tier → build the guiding prompt →
        proactively start a text session → deliver.
        """
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_cat_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── Guard: skip while voice sessions start/are active, symmetric with trigger_greeting ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        if self._takeover_active:
            logger.info("[%s] trigger_cat_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # Tier → behavior: cat1=awake / cat2=dozing / cat3=asleep.
        behavior = {"cat1": "awake", "cat2": "nap", "cat3": "sleep"}.get(str(tier or "").strip().lower(), "awake")

        from config.prompts.prompts_proactive import (
            CAT_GREETING_SILENT_BELOW_SECONDS,
            get_cat_greeting_episode_prompt, get_cat_greeting_episode_scene,
            get_cat_greeting_prompt,
            get_cat_greeting_reason_hint,
            normalize_proactive_prompt_locale,
        )
        # All four cat-greeting tables are in prompts_proactive with zh-TW entries; short codes collapse zh-TW
        # to zh, making those entries unreachable (issue #2500).
        _lang = normalize_proactive_prompt_locale(
            self.user_language or render_language or get_global_language_full()
        )
        from utils.time_format import format_elapsed as _format_elapsed
        episode_scene = get_cat_greeting_episode_scene(episode, _lang)
        if duration_seconds < CAT_GREETING_SILENT_BELOW_SECONDS:
            logger.debug(
                "[%s] trigger_cat_greeting: duration %.0fs below unified threshold, skipping",
                self.lanlan_name,
                duration_seconds,
            )
            return
        if episode_scene:
            template = get_cat_greeting_episode_prompt(
                behavior,
                duration_seconds,
                _lang,
            )
        else:
            template = get_cat_greeting_prompt(behavior, duration_seconds, _lang)
        if not template:
            logger.debug("[%s] trigger_cat_greeting: duration %.0fs below threshold, skipping", self.lanlan_name, duration_seconds)
            return

        # Delivery: reuse existing text sessions, otherwise start one, symmetric with trigger_greeting.
        # Reused even while a reply is in progress on it, as in trigger_greeting:
        # the claim below refuses then; start_session would cut that reply.
        if not isinstance(self.session, OmniOfflineClient):
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_cat_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_cat_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_cat_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except asyncio.CancelledError as exc:
                if not self._consume_start_retirement_cancellation(exc):
                    raise
                return
            except Exception as e:
                logger.warning("[%s] trigger_cat_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_cat_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # Format reason_hint's {master} before inserting it into the cat-return template.
        reason_hint = get_cat_greeting_reason_hint(was_auto, _lang).format(master=self.master_name)
        elapsed = _format_elapsed(_lang, duration_seconds)
        # Cat return is a closed experience prompt. Do not import the general
        # proactive time-of-day hint here: its meal/late-night suggestions can
        # replace the actual cat-form episode with an unrelated greeting.
        # Legacy cat templates still accept this placeholder for compatibility,
        # but it is deliberately empty on this path.
        time_hint = ""

        instruction = template.format(
            reason_hint=reason_hint, elapsed=elapsed, name=self.lanlan_name,
            master=self.master_name, time_hint=time_hint,
            cat_form_scene=episode_scene,
        )
        print(f"[trigger_cat_greeting] instruction:\n{instruction}")
        episode_marker = "-"
        if isinstance(episode, dict):
            episode_kind = episode.get("kind")
            episode_highlight = episode.get("highlight")
            if episode_kind in ("activity", "rest_after_activity", "rested"):
                episode_marker = str(episode_kind)
                if episode_highlight in ("played_yarn", "ate_snack", "small_move", "social_ping"):
                    episode_marker += ":" + str(episode_highlight)
        logger.info(
            "[%s] trigger_cat_greeting: behavior=%s duration=%.0fs was_auto=%s "
            "elapsed=%s episode=%s, delivering",
            self.lanlan_name,
            behavior,
            duration_seconds,
            was_auto,
            elapsed,
            episode_marker,
        )

        # ── Final pre-delivery check: voice may take over during instruction construction ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # Atomic SM claim, mutually exclusive with trigger_greeting / trigger_agent_callbacks / proactive_chat
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_cat_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_cat_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_cat_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    # Stale-session defense: same captured reference/type check as trigger_greeting.
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_cat_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    delivered = await session_ref.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_cat_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_new_character_greeting(
        self,
        *,
        render_language: str | None = None,
    ) -> None:
        from config.prompts.prompts_proactive import (
            get_new_character_greeting_prompt,
            normalize_proactive_prompt_locale,
        )
        from utils.new_character_greeting_state import has_pending, remove_pending

        config_manager = get_config_manager()
        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: no pending intent", self.lanlan_name)
            return

        if self.is_goodbye_silent():
            logger.info("[%s] trigger_new_character_greeting: goodbye silent, skipping", self.lanlan_name)
            return

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session active/starting, skipping", self.lanlan_name)
            return

        # Like trigger_cat_greeting, icebreaker greeting templates live in prompts_proactive and require a prompt
        # key preserving zh-TW. Fall back to global language only for empty user_language (issue #2500).
        _lang = normalize_proactive_prompt_locale(
            getattr(self, 'user_language', '')
            or render_language
            or get_global_language_full()
        )
        template = get_new_character_greeting_prompt(_lang)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session check, skipping", self.lanlan_name)
            return

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            if not self._has_connected_websocket():
                logger.warning("[%s] trigger_new_character_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_new_character_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except asyncio.CancelledError as exc:
                if not self._consume_start_retirement_cancellation(exc):
                    raise
                return
            except Exception as e:
                logger.warning("[%s] trigger_new_character_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_new_character_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: pending intent already consumed", self.lanlan_name)
            return

        instruction = template.format(name=self.lanlan_name, master=self.master_name)
        print(f"[trigger_new_character_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_new_character_greeting: delivering", self.lanlan_name)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_new_character_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        delivered = False
        proactive_sid = None
        history_len = None
        appended_snapshot = None
        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_new_character_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_new_character_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                history = getattr(self.session, "_conversation_history", None)
                if isinstance(history, list):
                    history_len = len(history)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    delivered = await self.session.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                if history_len is not None and isinstance(history, list) and len(history) > history_len:
                    appended_snapshot = list(history[history_len:])
                logger.info("[%s] trigger_new_character_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            try:
                interrupted = bool(proactive_sid) and self.current_speech_id != proactive_sid
                if (not delivered or interrupted) and history_len is not None:
                    history = getattr(self.session, "_conversation_history", None)
                    if isinstance(history, list) and appended_snapshot:
                        suffix_len = len(appended_snapshot)
                        if suffix_len <= len(history) and history[-suffix_len:] == appended_snapshot:
                            del history[-suffix_len:]
                if delivered and not interrupted:
                    try:
                        await remove_pending(config_manager, self.lanlan_name)
                    except Exception as exc:
                        logger.warning("[%s] trigger_new_character_greeting: remove pending failed: %s", self.lanlan_name, exc)
            finally:
                await self.state.fire(SessionEvent.PROACTIVE_DONE)
