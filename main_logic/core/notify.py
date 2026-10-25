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
"""Frontend/status notifications and prompt assembly for
``LLMSessionManager``: ``send_*`` status pushes, initial prompt build,
topic hints, and user-language switching.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).

Delivery contract -- there are TWO planes and they are not interchangeable
=========================================================================

``self.websocket`` is the DISPLAY plane. ``websocket_router`` reassigns it
to EVERY newly accepted socket, so it means "the newest window for this
character", NOT "the user" and NOT "the window that is recording". A window
holding the microphone is superseded the moment a chat window opens.

``_send_to_voice_owner`` is the MICROPHONE CONTROL plane. It targets the
socket holding the voice lease -- the window with the live hardware.

**Any notification whose effect is "stop or change the microphone" must
follow the LEASE, not the newest socket.** Send it to the display plane
alone and the recording window never learns its route died: the hardware
mic stays open and keeps uploading into a dead route, with no state and no
recovery path short of the user toggling the mic by hand. Today that class
is ``session_ended_by_server``, ``auto_close_mic``, and the text-mode
``session_started`` ack; ``tests/unit/test_voice_control_plane_contract.py``
fails if a new one is added without routing to the voice owner.

Two corollaries, each of which was a separate bug before it was written
down here:

* There is NO broadcast to fall back on. ``sync_message_queue`` is not a
  per-window fan-out: it runs ``character_runtime`` -> ``cross_server``
  -> ``app/monitor.py`` ``/sync/{name}`` and feeds MONITOR VIEWER clients on
  ``MONITOR_SERVER_PORT`` (desktop pet, subtitle windows). No app window
  ever connects there -- the app always builds a same-origin ``/ws/<name>``.
* The two planes are INDEPENDENT best-effort sends. Never let one failure
  short-circuit the other: give each its own ``try``, and never leave the
  lease-holder send inside a guard on the display socket's liveness.

Ordering matters too, on the way out: a revoke clears both
``_voice_input_websocket`` and the lease id, and ``_voice_owner_socket()``
returns None on either, so the notice must be delivered BEFORE the lease is
released. ``AsrRuntimeMixin._fail_closed_voice_route`` owns that order for
the fail-closed route exits.
"""

import asyncio
import hashlib
import json
from typing import Optional
from fastapi import WebSocketDisconnect
from config import TOOL_SERVER_PORT
from config.prompts.prompts_sys import (
    _loc,
    SESSION_INIT_PROMPT,
    SESSION_INIT_PROMPT_AGENT,
    AGENT_TASK_STATUS_RUNNING,
    AGENT_TASK_STATUS_QUEUED,
    AGENT_TASKS_HEADER,
    AGENT_TASKS_NOTICE,
)
from utils.language_utils import normalize_language_code, is_supported_language_code
from utils.llm_tool_leak_filter import strip_tool_call_leaks
from utils.screen_comment_guard import screen_guard_enabled, screen_history_rewrites
from ._shared import logger


class NotifyMixin:
    """Notification and prompt-assembly methods (see module docstring)."""

    def _has_connected_websocket(self) -> bool:
        websocket = self.websocket
        if not websocket or not hasattr(websocket, "client_state"):
            return False
        try:
            return websocket.client_state == websocket.client_state.CONNECTED
        except Exception:
            return False

    def _should_suppress_activity_narration(self) -> bool:
        """Whether the activity_guess emotion-tier narration has no live consumer.

        Injected into the tracker as the narration suppressed-check (see where
        ``set_narration_suppressed_check`` is wired). The narration only feeds
        proactive Phase 2's state_section, and Phase 2 is a no-op in two cases —
        paying for the LLM call then is pure idle burn:

          * ``is_goodbye_silent()`` — cat-mode silence; Phase 2 bails at its
            goodbye guard.
          * no connected WebSocket — after a plain disconnect / End Session the
            tracker heartbeat keeps ticking (it outlives the session so the
            rule-based break-reminder / context-prompt logic still runs), but a
            proactive turn has no client to reach. Without this, closing the page
            leaves the loop re-narrating at the backoff cap (~900s) all night.

        Both conditions recover on their own: the per-signature narration cache
        stays warm across the suppressed window, so reconnecting (or leaving
        goodbye-silence) resumes narration once that signature's backoff interval
        elapses — on the next tick if a new turn advanced conv_seq or the interval
        already passed during the gap, otherwise after the remaining interval.
        """
        return self.is_goodbye_silent() or not self._has_connected_websocket()

    async def send_user_activity(self, interrupted_speech_id: Optional[str] = None):
        """Send the user-activity signal, attaching the interrupted speech_id for precise interruption control"""
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                if interrupted_speech_id is None:
                    interrupted_speech_id = self.current_speech_id
                message = {
                    "type": "user_activity",
                    "interrupted_speech_id": interrupted_speech_id,  # Tell frontend which speech_id to discard.
                }
                await self.websocket.send_json(message)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send User Activity Error: {e}")

    def _convert_cache_to_str(self, cache, preceding=()):
        """[Hot-swap related] Convert the cache to a string.

        This text is primed into the next session's system prompt, where the
        offline client's request-view projection never sees it, so the
        character's lines pass the same screen-chain rewrite here, with the
        same rules (a chain inside one line, or spread over the run of
        character lines that ends the cache and follows a master line). The
        next thing the new session sees is the user speaking, hence
        ``trailing_turn``. A line left with nothing is not rendered.

        Pass every slice that ends up adjacent in one prompt in one call:
        judged apart, a chain split across two slices is missed. A slice
        appended after text that was already primed passes that text as
        ``preceding``; it is judged with the slice but not rendered again.

        Known boundary: the memory server's recent history (rendered by
        ``/new_dialog`` just before these lines) is judged on its own, so a
        chain split between memory's last replies and the cache's first ones
        is not joined.

        Tool-call markup a character line spoke instead of calling the tool
        is cut first (``_without_tool_call_leaks``): primed into the next
        session, it reads as an example to copy.
        """
        # Resolved on the class, so an owner that is not a full manager works.
        tool_names = NotifyMixin._registered_tool_names(self)
        preceding = NotifyMixin._without_tool_call_leaks(preceding, self.lanlan_name, tool_names)
        entries = preceding + NotifyMixin._without_tool_call_leaks(
            cache, self.lanlan_name, tool_names,
        )
        rewrites = {}
        if screen_guard_enabled():
            roles = {
                self.lanlan_name: "assistant",
                getattr(self, "master_name", None): "user",
            }
            rewrites = screen_history_rewrites(
                [
                    {
                        "role": roles.get(i['role'], "system"),
                        "content": i['text'],
                        # Proactive deliveries are cached as their own
                        # entries (send_lanlan_response); keep them apart.
                        "additional_kwargs": {"dialog_source": i['source']}
                        if i.get('source') else {},
                    }
                    for i in entries
                ],
                trailing_turn=True,
            )
        res = ""
        for index in range(len(preceding), len(entries)):
            i = entries[index]
            text = rewrites.get(index, i['text'])
            if text is None:
                continue
            res += f"{i['role']} | {text}\n"
        return res

    @staticmethod
    def _registered_tool_names(owner) -> list:
        """Names of the tools ``owner`` has registered now; empty without a registry."""
        try:
            return list(owner.list_tools())
        except Exception:
            return []

    @staticmethod
    def _without_tool_call_leaks(entries, speaker, tool_names) -> list:
        """Cache entries with tool-call markup cut from ``speaker``'s lines.

        With the registered ``tool_names`` a bare ``name(param=...)`` counts
        too. Entries keep their shape (``role`` / ``text`` / ``source``); a
        line that was nothing but markup is left out.
        """
        cleaned = []
        for entry in entries:
            text = entry.get('text') if isinstance(entry, dict) else None
            if not isinstance(text, str) or entry.get('role') != speaker:
                cleaned.append(entry)
                continue
            stripped = strip_tool_call_leaks(text, tool_names=tool_names)
            if stripped == text:
                cleaned.append(entry)
            elif stripped.strip():
                cleaned.append({**entry, 'text': stripped})
        return cleaned

    async def _build_initial_prompt(self) -> str:
        """Build the system prompt and inject active task summary when agent is enabled."""
        _lang = normalize_language_code(self.user_language, format="short")
        if self._is_agent_enabled():
            # Keep the current wrapper structure but revert prompt semantics:
            # do not distinguish browser/computer/plugin in the initial capability text.
            # Historical dynamic capability block kept for rollback:
            # capability_parts = []
            # if self.agent_flags.get('computer_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_COMPUTER_USE, _lang))
            # if self.agent_flags.get('browser_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_BROWSER_USE, _lang))
            # if self.agent_flags.get('user_plugin_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_USER_PLUGIN_USE, _lang))
            # caps_text = (
            #     _loc(AGENT_CAPABILITY_SEPARATOR, _lang).join(capability_parts)
            #     if capability_parts else _loc(AGENT_CAPABILITY_GENERIC, _lang)
            # )
            # prompt = _loc(SESSION_INIT_PROMPT_AGENT_DYNAMIC, _lang).format(
            #     name=self.lanlan_name,
            #     capabilities=caps_text,
            # ) + self.lanlan_prompt
            prompt = (
                _loc(SESSION_INIT_PROMPT_AGENT, _lang).format(name=self.lanlan_name)
                + self.lanlan_prompt
            )
        else:
            prompt = (
                _loc(SESSION_INIT_PROMPT, _lang).format(name=self.lanlan_name)
                + self.lanlan_prompt
            )
        if self._is_agent_enabled():
            # Plugin summary (with plugin ids) is intentionally disabled to avoid
            # exposing implementation identifiers in the general agent prompt.
            # Keep method call removed here for deterministic prompt content.
            # Historical prompt merge kept for rollback:
            # plugin_prompt, active_tasks_prompt = await asyncio.gather(
            #     self._fetch_plugin_summary_prompt(),
            #     self._fetch_active_agent_tasks_prompt(),
            # )
            # prompt += plugin_prompt
            active_tasks_prompt = await self._fetch_active_agent_tasks_prompt()
            prompt += active_tasks_prompt

        # Record/query key: empty lanlan_name uses default, matching sinks
        # that write directives to default buckets for empty/default lanlan;
        # readers need the same key or user bans never arrive.
        # system prompt，codex P2）。
        _directives_key = self.lanlan_name or "default"

        # ── Explicit user topic-ban injection ──
        # Users' past-three-day 别再提 X / stop saying X instructions are already visible
        # this turn, but next session restart loses originals through compress_history;
        # assemble active terms into system prompts to remind models.
        # Extraction/persistence use memory.user_directives' user_utterance sink;
        # read-only here. Empty render_prompt_block returns "", adding no prompt length.
        try:
            from memory.user_directives import get_user_directives_manager

            prompt += get_user_directives_manager().render_prompt_block(
                _directives_key,
                _lang,
            )
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[UserDirectives] prompt injection skipped: %s",
                _exc,
            )

        # ── Anti-repeat soft-hint injection ──
        # List recent high-BM25 topics to remind models they already discussed them. Soft
        # constraints apply to **all paths**, unlike explicit user bans requiring strong
        # constraints. Proactive Phase-2 exits in system_router also enforce
        # BM25 total thresholds with regeneration/drop, while regular replies rely only on prompts.
        # Empty corpora/new characters' first turns render "" without side effects.
        try:
            from memory.anti_repeat import get_anti_repeat_corpus
            from config.prompts.prompts_directives import render_recent_topics_block

            anti_repeat_corpus = get_anti_repeat_corpus()
            await anti_repeat_corpus.apreload(_directives_key)
            topics = anti_repeat_corpus.top_recent_topics(_directives_key)
            topic_block = render_recent_topics_block(topics, _lang)
            prompt += topic_block
            if topic_block:
                from memory.anti_repeat_effects import (
                    record_anti_repeat_soft_hint,
                )

                record_anti_repeat_soft_hint(_directives_key)
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[AntiRepeat] soft hint injection skipped: %s",
                _exc,
            )

        return prompt

    async def _inject_pending_user_directives(self) -> None:
        """Carry freshly recorded ban-topic directives into the NEXT session.

        ``_build_initial_prompt`` reads the directive store once per session,
        and for a hot swap that read happens during background warm-up
        (``lifecycle._background_prepare_pending_session``) — a full user turn
        before the swap actually lands, because the final swap waits for the
        next turn-end (``turn.py`` step 4). A directive recorded inside that
        gap misses the very session it was about to configure: the new session
        starts with a fresh ``_conversation_history`` (the user's original
        words are gone with the old one) and a system prompt that predates the
        directive, so nothing carries it for up to another archive cycle.

        Writing it into the next-session cache closes exactly that gap. It is
        deliberately NOT injected into the live session — see the ``lifetime``
        comment below.

        Called from both user-utterance entry points (text and voice), right
        after the plugin-bus publish that drives the recording sink.
        """
        # Layering: memory L3 cannot import main_logic L4,
        # so sinks persist/set pending markers, while L4 injects here.
        # Same reason as sink registration in app/runtime_bindings.py, complementary halves.
        try:
            from memory.user_directives import get_user_directives_manager
            manager = get_user_directives_manager()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[UserDirectives] manager unavailable: %s", exc)
            return
        # Match _build_initial_prompt keys: sinks map empty/default lanlan
        # to default buckets.
        key = self.lanlan_name or "default"
        try:
            if not manager.take_pending_injection(key):
                return
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[UserDirectives] pending flag read failed: %s", exc)
            return
        try:
            _lang = normalize_language_code(self.user_language, format='short')
            # Render the **entire active list**, not newly extracted terms only, matching
            # _build_initial_prompt injection. Incremental-only injection makes session bans depend on
            # which were just uttered, differing from
            # rebuilt-session ban sets.
            active_terms = manager.get_active_terms(key)
            block = manager.render_prompt_block(key, _lang)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[UserDirectives] mid-session render failed: %s", exc)
            return
        block = block.strip()
        if not block:
            return
        # lifetime='next_session' writes **only** next-session caches, never the current session.
        # Text/audio paths converge to one value without session-type branches.
        #
        # Current sessions do not need it. _conversation_history is append-only during sessions,
        # except repetition recovery in omni_offline_client/
        # _streaming.py resets, leaving original 别再提 X messages
        # visible every turn. Initial _build_initial_prompt already injected
        # the **complete** active list; true increments are newly extracted instructions,
        # already known from originals. Cross-session missing history is the real
        # gap this cache addresses.
        #
        # Current-session writes have costs: append_context renders role='system' as
        # HumanMessage("system: ..."), persisting **user-shaped messages** in history.
        # Other sources, game results/proactive material, supply new information; this repeats
        # user wording just seen, inviting acknowledgment such as 好的我
        # 不会再提了 while users often want to discuss their sentence's latter half.
        #
        # Realtime audio cannot accept it anyway: sessions without
        # _conversation_history fall back from append_context to prime_context, while Gemini
        # send_client_content(turn_complete=True) creates another user
        # turn and sets _skip_until_next_response, swallowing all current
        # output_transcription/audio deltas. Characters go silent immediately after users state bans,
        # failing exactly when this feature matters most.
        lifetime = "next_session"
        try:
            # request_id enables append_context deduplication: **repeated** instructions
            # extend TTL by hit_count in design part E and also mark injection pending.
            # Without IDs every repetition appends another identical next-session block,
            # duplicating bans repeatedly at the next session start.
            #
            # Dedup window is **120 seconds**, _CONTEXT_APPEND_DEDUP_TTL_SECONDS,
            # append_context's existing contract, never permanent. Do not read this as once per term
            # set forever, an overstrong earlier comment flagged by codex.
            # This window fits: consecutive repetition is one statement and should deduplicate; repetition half an hour
            # later often signals another model misstep, where **reinjection reinforces** the ban appropriately.
            # Do not invent permanent deduplication here.
            #
            # Fingerprint **normalized term sets**, never rendered blocks. Terms sort by last_seen_at
            # descending, so repeating older entries moves them first,
            # changing bytes without changing sets, giving new IDs and duplicate whole blocks.
            # Ordinary TTL extension therefore bypasses deduplication where most needed
            # (codex P2). Sorting + casefold creates new IDs only for actual set changes.
            # Serialize JSON arrays, **never delimiter joins**: _normalize_term
            # only strips/checks length, permitting arbitrary characters. Joining with " | " makes
            # ["aa | bb", "cc"] and ["aa", "bb | cc"] share fingerprints,
            # hence request_id, skipping new ban sets as duplicates in append_context,
            # preventing this session's update (coderabbit). JSON quotes/escaping remove ambiguity.
            fingerprint = json.dumps(
                sorted(t.casefold() for t in active_terms if t),
                ensure_ascii=False,
            )
            request_id = (
                f"user_directives:{key}:"
                f"{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"
            )
            # timing='when_ready' queues unfinished-session updates instead of dropping them.
            result = await self.append_context(
                source="user_directives",
                role="system",
                text=block,
                audience="model",
                timing="when_ready",
                lifetime=lifetime,
                request_id=request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[UserDirectives] mid-session inject failed: %s", exc)
            return
        if getattr(result, "appended", False):
            logger.info(
                "[%s] user directives injected (lifetime=%s targets=%s)",
                self.lanlan_name, lifetime, getattr(result, "targets", ()),
            )

    def _is_agent_enabled(self):
        try:
            gate_ok, _ = self._config_manager.is_agent_api_ready()
        except Exception:
            gate_ok = False
        return (
            gate_ok
            and self.agent_flags["agent_enabled"]
            and (
                self.agent_flags["computer_use_enabled"]
                or self.agent_flags.get("browser_use_enabled", False)
                or self.agent_flags.get("user_plugin_enabled", False)
                or self.agent_flags.get("openclaw_enabled", False)
            )
        )

    async def _fetch_plugin_summary_prompt(self) -> str:
        """Plugin prompt segment is intentionally disabled for chat prompt minimalism."""
        # This hook is kept for compatibility with older call sites.
        # Disabled by product decision: do not include plugin IDs in agent prompt.
        # Historical implementation kept for rollback:
        # if not (self._is_agent_enabled() and self.agent_flags.get('user_plugin_enabled')):
        #     return ""
        # _lang = normalize_language_code(self.user_language, format='short')
        # header = _loc(AGENT_PLUGINS_HEADER, _lang)
        # count_tmpl = _loc(AGENT_PLUGINS_COUNT, _lang)
        # try:
        #     async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0), proxy=None, trust_env=False) as client:
        #         r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
        #         if r.status_code != 200:
        #             return ""
        #         data = r.json()
        #         plugins = data.get("plugins", []) if isinstance(data, dict) else []
        #         if not plugins:
        #             return ""
        #         if len(plugins) <= 5:
        #             lines = []
        #             for p in plugins:
        #                 if not isinstance(p, dict):
        #                     continue
        #                 pid = p.get("id", "")
        #                 if pid:
        #                     lines.append(f"  - {pid}")
        #             if lines:
        #                 return header + "\n".join(lines) + "\n"
        #         else:
        #             return count_tmpl.format(count=len(plugins))
        # except Exception as e:
        #     logger.debug(f"Plugin summary retrieval failed; ignored: {e}")
        return ""

    async def _fetch_active_agent_tasks_prompt(self) -> str:
        """Query agent server for active tasks and return a prompt snippet."""
        if not self._is_agent_enabled():
            return ""
        # Reuse internal_http_client singleton: agent-mode session initialization uses this path,
        # and TOOL_SERVER_PORT is another internal 127.0.0.1 service.
        try:
            from utils.internal_http_client import get_internal_http_client

            client = get_internal_http_client()
            resp = await client.get(
                f"http://127.0.0.1:{TOOL_SERVER_PORT}/tasks",
                timeout=1.5,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            tasks = data.get("tasks", [])
            active = [t for t in tasks if t.get("status") in ("running", "queued")]
            if not active:
                return ""
            _lang = normalize_language_code(self.user_language, format="short")
            lines = []
            for t in active:
                params = t.get("params") or {}
                desc = (
                    params.get("query")
                    or params.get("instruction")
                    or t.get("original_query")
                    or t.get("id", "")[:8]
                )
                status = (
                    _loc(AGENT_TASK_STATUS_RUNNING, _lang)
                    if t.get("status") == "running"
                    else _loc(AGENT_TASK_STATUS_QUEUED, _lang)
                )
                lines.append(f"  - [{status}] {desc}")
            if len(lines) > 0:
                return (
                    _loc(AGENT_TASKS_HEADER, _lang)
                    + "\n".join(lines)
                    + _loc(AGENT_TASKS_NOTICE, _lang)
                )
            else:
                return ""
        except Exception:
            return ""

    def _get_translation_service(self):
        """Get the translation service instance (lazily initialized)"""
        if self._translation_service is None:
            from utils.language_utils import get_translation_service

            self._translation_service = get_translation_service(self._config_manager)
        return self._translation_service

    def set_user_language(self, language: str):
        """
        Set the user language (reuses normalize_language_code for normalization)

        Supported normalization rules:
        - 'zh', 'zh-CN', 'zh-TW' and anything starting with 'zh' → 'zh-CN'
        - 'en', 'en-US', 'en-GB' and anything starting with 'en' → 'en'
        - 'ja', 'ja-JP' and anything starting with 'ja' → 'ja'
        - other languages unsupported for now, stays at the default 'zh-CN'
        """
        if not language:
            logger.warning(f"语言参数为空，保持当前语言: {self.user_language}")
            return

        # Validate raw input: normalize_language_code defaults unknown values to en,
        # so external ws message['language'] from corrupt localStorage or
        # third-party garbage undefined / null / estonian
        # silently becomes en, overriding correct session locales. Filter through shared allowlists first.
        if not is_supported_language_code(language):
            logger.warning(
                f"语言参数不支持: {language!r}，保持当前语言: {self.user_language}"
            )
            return

        # Normalize language codes with shared functions.
        normalized_lang = normalize_language_code(language, format="full")

        self.user_language = normalized_lang
        self._user_language_explicit = True
        self._conversation_turn_language = normalized_lang
        self._set_conversation_turn_language(normalized_lang)
        if normalized_lang != language:
            logger.info(f"用户语言已归一化: {language} → {normalized_lang}")
        else:
            logger.info(f"用户语言已设置为: {normalized_lang}")

        # Text mode needs no extra rewrite-prompt language synchronization; rewrite logic removed.

        # Built-in tool descriptions/parameter documentation render by user_language;
        # reregister on language changes, replacing old registry descriptions, then fire-and-forget
        # to active/pending session wires. OmniRealtimeClient supports
        # session.update with new tools; OmniOfflineClient's next stream_text
        # automatically uses latest _tool_definitions.
        self._register_builtin_tools()
        self._fire_task(self._sync_tools_to_active_session())

    def set_render_language(self, language: str):
        """Apply a request/UI fallback without marking it as durable preference."""
        if not language or not is_supported_language_code(language):
            return
        normalized_lang = normalize_language_code(language, format="full")
        self._conversation_render_language = normalized_lang
        if getattr(self, "_user_language_explicit", False):
            return
        # Deliberately unconditional, mirroring set_user_language.  A "skip the
        # repeat" optimisation was tried and removed: the fields are assigned
        # before the registry call and the wire push is fire-and-forget with
        # suppressed errors, so no cheap local check can prove the tools were
        # actually applied -- and a wrong skip strands stale tool definitions
        # with no way back.  The call sites (ws language_update, request
        # absorption) are low frequency, so the redundant work is not worth that
        # class of bug.
        self.user_language = normalized_lang
        self._conversation_turn_language = normalized_lang
        self._set_conversation_turn_language(normalized_lang)
        self._register_builtin_tools()
        self._fire_task(self._sync_tools_to_active_session())

    def clear_user_language_preference(
        self,
        render_language: Optional[str] = None,
    ) -> None:
        """Clear durable language evidence and optionally apply a UI fallback.

        A render locale by itself must never revoke an explicit preference.  The
        caller therefore has to use this separate operation when it has positive
        evidence that the preference was cleared.  Once the explicit marker is
        gone, ``set_render_language`` updates the user/turn/tool state together.
        """
        self._user_language_explicit = False
        fallback_language = render_language
        if not is_supported_language_code(fallback_language):
            fallback_language = getattr(self, "_conversation_render_language", None)
        if is_supported_language_code(fallback_language):
            self.set_render_language(fallback_language)
            return

        # Do not leave the former explicit value behind as a non-explicit
        # fallback when no render locale is available.
        self.user_language = None
        self._conversation_render_language = None
        self._conversation_turn_language = None
        self._set_conversation_turn_language(None)
        self._register_builtin_tools()
        self._fire_task(self._sync_tools_to_active_session())

    def _voice_owner_socket(self):
        """Return the socket holding the voice lease, when it is not the current one.

        ``self.websocket`` is reassigned to every newly accepted socket, so the
        window that is actually recording can be superseded by a newer chat
        window while keeping the microphone. Mic control-plane messages have to
        reach that window or its teardown never runs. Returns None whenever the
        lease holder IS the current socket, so single-window behaviour is
        bit-identical.

        Note ``sync_message_queue`` is NOT an alternative: it feeds the monitor
        process (desktop pet / subtitle viewers) over a separate port, and no
        app window ever connects there.
        """

        socket = getattr(self, "_voice_input_websocket", None)
        if socket is None or socket is self.websocket:
            return None
        if not getattr(self, "_voice_lease_connection_id", ""):
            return None
        state = getattr(socket, "client_state", None)
        if state is None or state != socket.client_state.CONNECTED:
            return None
        return socket

    async def _send_to_voice_owner(self, payload: dict):
        """Best-effort push to the voice-lease holder; never raises.

        Returns the socket it actually reached, or None. Callers that need to
        avoid a second copy must dedupe against THIS, not against a fresh
        ``_voice_owner_socket()`` read: the send below is an await, and a lease
        takeover inside it makes the second read a DIFFERENT socket, leaving the
        one that just got the payload absent from the "already sent" set.
        """

        socket = self._voice_owner_socket()
        if socket is None:
            return None
        try:
            await socket.send_text(json.dumps(payload))
        except WebSocketDisconnect:
            return None
        except Exception as e:
            logger.error(f"💥 WS Send To Voice Owner Error: {e}")
            return None
        return socket

    async def send_status(self, message: str) -> bool:
        """Send a status message to the frontend. message should be a JSON string {"code": "XXX", "details": {...}}, translated by the frontend via i18next.

        The return value answers one question only: did the FRONTEND socket
        take this status. The monitor-process mirror below is a separate,
        best-effort plane and must never be able to un-deliver it -- callers
        keep a per-plane retry ledger, so reporting a delivered status as
        failed leaves their episode uncommitted and re-sends it on every
        later microphone frame.
        """
        delivered = False
        _, _, check_start = self._start_notification_context()
        check_tts = getattr(self, "_tts_output_is_current", None)
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                data = json.dumps({"type": "status", "message": message})
                check_start()
                if callable(check_tts) and not check_tts():
                    return False
                await self.websocket.send_text(data)
                delivered = True

                # Synchronize to the synchronization server.
                try:
                    check_start()
                except asyncio.CancelledError:
                    # Ownership changed after the display write committed.
                    # Stop mirroring, but preserve the caller's delivery receipt.
                    return delivered
                if callable(check_tts) and not check_tts():
                    return delivered
                self.sync_message_queue.put(
                    {"type": "json", "data": {"type": "status", "message": message}}
                )
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            return delivered
        except Exception as e:
            logger.error(f"💥 WS Send Status Error: {e}")
            return delivered
        return delivered

    async def send_topic_hint(self, *, turn_id: Optional[str] = None) -> bool:
        """Show a frontend-only teaser bubble right before she opens a deep-topic hook.

        Deliberately does NOT touch ``sync_message_queue`` / chat memory — the
        teaser is pure frontend display (rendered by react-neko-chat's dedicated
        topic-hint component) and must never enter the chat-LLM context, the
        same isolation as :meth:`passthrough_to_chat_bubble`. The frontend
        renders the localized copy itself; we only hand it the character name.
        """
        if not (
            self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json(
                {
                    "type": "topic_hint",
                    "author": self.lanlan_name,
                    "turn_id": str(turn_id or ""),
                }
            )
            return True
        except WebSocketDisconnect:
            return False
        except Exception as e:
            logger.warning("[%s] send_topic_hint failed: %s", self.lanlan_name, e)
            return False

    async def send_cancel_topic_hint(self, *, turn_id: Optional[str] = None) -> bool:
        """Retract a previously sent topic-hint teaser (matched by ``turn_id``).

        Used when the opener fails before any committed output, so the frontend
        removes the dangling teaser instead of leaving an orphan bubble. Like
        :meth:`send_topic_hint`, this stays off ``sync_message_queue`` entirely.
        """
        if not (
            self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json(
                {
                    "type": "cancel_topic_hint",
                    "turn_id": str(turn_id or ""),
                }
            )
            return True
        except WebSocketDisconnect:
            return False
        except Exception as e:
            logger.warning(
                "[%s] send_cancel_topic_hint failed: %s", self.lanlan_name, e
            )
            return False

    def _start_notification_context(self, request_id=None, also_notify=None, *, enforce_start_guard=True):
        """Capture one operation before a notification crosses a socket await."""
        current_request = getattr(self, "_current_start_request", None)
        operation = current_request() if callable(current_request) else None
        if operation is not None:
            request_id = request_id or operation.request_id
            if also_notify is None:
                also_notify = operation.websocket

        def check():
            guard = getattr(self, "_check_start_operation", None)
            if enforce_start_guard and callable(guard) and operation is not None:
                guard(operation)

        check()
        return request_id, also_notify, check

    async def send_session_preparing(
        self, input_mode: str, *, request_id=None, also_notify=None
    ):  # Notify frontend of session preparation/silent period.
        payload = {"type": "session_preparing", "input_mode": input_mode}
        request_id, also_notify, check = self._start_notification_context(request_id, also_notify)
        if request_id:
            payload["request_id"] = request_id
        delivered_to = []
        display_socket = self.websocket
        try:
            if (
                display_socket
                and hasattr(display_socket, "client_state")
                and display_socket.client_state == display_socket.client_state.CONNECTED
            ):
                try:
                    check()
                    await display_socket.send_text(json.dumps(payload))
                    delivered_to.append(display_socket)
                except WebSocketDisconnect:
                    # Isolated like the sibling senders: a display socket dying
                    # between the CONNECTED check and the send must not skip the
                    # lease-holder copy below.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Preparing Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # Completes the set with session_started / session_failed: the
                # window that asked for an audio session is the LEASE holder,
                # while self.websocket is whichever socket connected most
                # recently. This one only drives the "preparing" banner, so
                # losing it is cosmetic rather than a stuck microphone -- but a
                # requester that never sees "preparing" and then never sees the
                # ack has no feedback at all for the whole start.
                #
                # No-op for a single window: _voice_owner_socket returns None
                # when the lease holder IS the current socket.
                check()
                owner_socket = await self._send_to_voice_owner(dict(payload))
                if owner_socket is not None:
                    delivered_to.append(owner_socket)
            check()
            await self._send_to_socket_if_new(also_notify, payload, delivered_to)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Preparing Error: {e}")

    async def send_session_started(
        self,
        input_mode: str,
        *,
        request_id: str | None = None,
        also_notify=None,
        microphone_route_override: str | None = None,
    ):  # Notify frontend session started.
        request_id, also_notify, check = self._start_notification_context(request_id, also_notify)
        # Carry the SETTLED microphone route on the ack itself (Codex P2).
        #
        # The route verdict otherwise travels only as an ASR_INDEPENDENT_*
        # status on the mic control plane, which reaches the lease holder and
        # the current display socket -- and there are paths where it reaches
        # neither. The load-bearing one: a second window claiming the voice
        # lease while _asr_runtime.start() is still running bumps the ASR start
        # generation, so the failing start's own terminal status is fenced off
        # and NEVER EMITTED, leaving the route pinned "blocked" with no verdict
        # delivered anywhere. Both windows' fail-closed latches stay false, the
        # ack below says "started", and the microphone opens onto a route that
        # discards every frame -- no status, no recovery, the user just talks
        # into nothing.
        #
        # Qualifying the ack fixes every emitter at once: the ordinary one in
        # _start_session_activate, the in-flight dedupe re-ack in
        # _start_session_handle_inflight (which re-decides a blocked route
        # first, via _rerun_route_for_deduped_start, precisely so the route it
        # reports here is the requester's own), and the stale-start case above
        # that no status-based fix can reach. Suppressing the ack instead would
        # be worse -- the dedupe re-ack exists precisely so the requester is not
        # stranded on its 15s timeout, whose end_session tears down the session
        # that did start.
        #
        # ``request_id`` names WHICH start this acks. Without it an ack is
        # anonymous, and a window with its own start pending settles on the
        # first same-mode ack that reaches it -- including one fanned out for
        # somebody else's start. That is exactly what happens when a second
        # window claims the microphone mid-start: the claim moves the voice
        # socket, so the in-flight start's ack lands on the claimant, whose
        # frontend clears its timeout, resolves, reads the blocked route it
        # carries and aborts its microphone flow outright. The claimant's own
        # ack -- the one carrying the re-decided route -- arrives to a flow that
        # has already given up. Tagging the ack lets the frontend ignore acks
        # that are not answering its request.
        payload = {"type": "session_started", "input_mode": input_mode}
        if request_id:
            payload["request_id"] = request_id
        #
        # ``microphone_route_override`` exists for one caller: the dedupe re-ack
        # whose requester has since LOST the voice lease. The live route belongs
        # to the new holder by then and may well be healthy, but reporting it
        # would open a microphone on a window whose PCM the server now discards
        # as superseded. It overrides rather than suppresses so the requester
        # still gets an ack and settles its start instead of hanging.
        #
        # It applies to ``also_notify`` ONLY, never to the fan-out (Codex P2).
        # "This route is unusable" is true of the superseded requester, not of
        # the session: the new lease holder is on the very same fan-out, and a
        # window with no start pending latches any blocked verdict it sees --
        # so broadcasting the override would fail-close the microphone of the
        # window that legitimately owns it.
        route_mode = str(getattr(self, "_asr_route_mode", "") or "")
        if route_mode:
            # Omitted when unknown rather than defaulted: a manager without the
            # ASR mixin should keep today's behaviour, not have every audio
            # start refuse the microphone.
            payload["microphone_route"] = route_mode
        # The sockets this call actually delivered to, recorded as it goes. Every
        # membership below has to be answered from here rather than by re-reading
        # self.websocket / _voice_owner_socket(): both can point somewhere else
        # by the time the addressed send runs, and a socket that already got the
        # payload would then look unserved (CodeRabbit).
        delivered_to = []

        def _addressed(base: dict) -> dict:
            # The requester's copy, carrying the override when there is one.
            copy = dict(base)
            if microphone_route_override:
                copy["microphone_route"] = microphone_route_override
            return copy

        display_socket = self.websocket
        try:
            if (
                display_socket
                and hasattr(display_socket, "client_state")
                and display_socket.client_state == display_socket.client_state.CONNECTED
            ):
                # The requester can BE the display socket (it is simply the
                # newest connection), and then this is its only copy -- so the
                # override has to travel on this plane too when it is the one
                # carrying it. Every other window on this plane gets the real
                # route.
                data = json.dumps(
                    _addressed(payload)
                    if also_notify is not None and display_socket is also_notify
                    else payload
                )
                try:
                    check()
                    await display_socket.send_text(data)
                    delivered_to.append(display_socket)
                except WebSocketDisconnect:
                    # Isolated for the same reason as
                    # send_session_ended_by_server: the CONNECTED check and the
                    # send are separated by an await, so the display socket can
                    # die in between. Letting that reach the outer handler would
                    # skip the text fan-out below -- the notice that tells a
                    # recorder superseded by THIS chat window that its route has
                    # gone blocked, without which it keeps a live hardware
                    # microphone feeding a route that discards every frame.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Started Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # A text session pins the microphone route to "blocked", so the
                # window still holding the mic has to hear about it or it keeps
                # uploading into a route that discards everything.
                #
                # Audio is here too, and the "would flip voiceChatActive in an
                # unrelated window" reasoning this used to carry does not hold
                # for it (Codex P2). The lease holder is not an unrelated
                # window: for a user-initiated audio start the router claims the
                # lease for the REQUESTING socket synchronously
                # (_claim_voice_input_connection) BEFORE firing start_session,
                # so the lease holder IS the window that asked. Meanwhile
                # ``self.websocket`` is reassigned to every newly accepted
                # socket, and a whole session start (TTS + LLM + independent
                # ASR) sits between that reassignment and this ack -- seconds,
                # against a 15s frontend deadline. Any second window opening in
                # that interval used to take the ack, and the window that
                # actually asked sat on ``sessionStartPromise`` until it timed
                # out and never called startMicCapture: the user clicks the mic
                # and simply never gets a microphone, while the backend audio
                # session stays up with no recording client.
                #
                # This is a no-op for the single-window case:
                # ``_voice_owner_socket`` returns None whenever the lease holder
                # IS the current socket, so the fan-out fires only in exactly
                # that race.
                #
                # Game owner exempt, matching send_session_ended_by_server and
                # _fail_closed_voice_route. The galgame gate owns the mic
                # through the built-in game consumer route and tears down via
                # GAME_ROUTE_ENDED, and websocket_router acknowledges a text
                # entry during an active game route with a bare
                # send_session_started("text") -- no ordinary text session, no
                # blocked route. Fanning that ack out anyway reaches the game
                # window, whose session_started(text) handler calls
                # stopRecording({notifyServer:false}) on any window with
                # isRecording true (which a game STT gate requires), releasing
                # the game lease and closing hardware the text entry never
                # meant to touch.
                check()
                owner_socket = await self._send_to_voice_owner(dict(payload))
                if owner_socket is not None:
                    delivered_to.append(owner_socket)
            if also_notify is not None:
                # Addressed delivery for a requester that neither plane is
                # guaranteed to reach (Codex P2). The dedupe re-ack is the case:
                # its own re-decision can fail closed, and _fail_closed_voice_route
                # REVOKES the lease -- clearing _voice_lease_connection_id and the
                # voice socket -- before this ack goes out. With a newer window as
                # self.websocket, the requester is then on neither plane, sits on
                # its promise until the 15s timeout, and that timeout's end_session
                # tears down the session that just started.
                check()
                await self._send_to_socket_if_new(
                    also_notify, _addressed(payload), delivered_to
                )
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Started Error: {e}")

    async def _send_to_socket_if_new(self, socket, payload: dict, already_sent) -> None:
        """Best-effort push to ``socket`` unless it already got this payload.

        Identity comparison, not equality: the planes above hold the very same
        socket objects, and a second copy of an ack is not harmless -- the
        frontend's start resolver is one-shot but the handler around it runs
        again in full (microphone teardown, composer visibility).
        """
        if socket is None or any(socket is sent for sent in already_sent):
            return
        state = getattr(socket, "client_state", None)
        if state is None or state != socket.client_state.CONNECTED:
            return
        try:
            await socket.send_text(json.dumps(payload))
        except WebSocketDisconnect:
            # The CONNECTED check above and this send are separated by an await,
            # so the requester can disconnect in between. A window that is gone
            # has no start left to settle -- swallow it like the sibling planes
            # do, rather than fail an ack the other windows still need.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Addressed Ack Error: {e}")

    async def send_session_failed(self, input_mode: str, *, request_id=None, also_notify=None, allow_retired_operation=False):  # Notify frontend session startup failed.
        """Notify the frontend that session start failed, so it hides the preparing banner and resets state"""
        payload = {"type": "session_failed", "input_mode": input_mode}
        request_id, also_notify, check = self._start_notification_context(
            request_id, also_notify, enforce_start_guard=not allow_retired_operation,
        )
        if request_id:
            payload["request_id"] = request_id
        delivered_to = []
        display_socket = self.websocket
        try:
            if (
                display_socket
                and hasattr(display_socket, "client_state")
                and display_socket.client_state == display_socket.client_state.CONNECTED
            ):
                try:
                    check()
                    await display_socket.send_text(json.dumps(payload))
                    delivered_to.append(display_socket)
                except WebSocketDisconnect:
                    # Isolated like the sibling senders: a display socket dying
                    # between the CONNECTED check and the send must not skip the
                    # lease-holder copy below.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Failed Error: {e}")
            if getattr(self, "_voice_lease_owner", "none") != "game":
                # Same reasoning as send_session_started (Codex P2). The window
                # that asked for an audio session is the LEASE holder -- the
                # router claims the lease for the requesting socket before
                # firing start_session -- while self.websocket is reassigned to
                # every newly accepted socket. A second window opening during a
                # start that then FAILS took this notice, and the window that
                # asked sat on sessionStartPromise until its 15s deadline
                # instead of failing fast; the timeout path then sends
                # end_session, tearing down whatever did get built.
                #
                # No-op for a single window: _voice_owner_socket returns None
                # when the lease holder IS the current socket. Game owner
                # exempt, matching the other senders.
                check()
                owner_socket = await self._send_to_voice_owner(dict(payload))
                if owner_socket is not None:
                    delivered_to.append(owner_socket)
            check()
            await self._send_to_socket_if_new(also_notify, payload, delivered_to)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Failed Error: {e}")

    async def send_avatar_interaction_ack(
        self, interaction_id: str, accepted: bool, reason: str = "", turn_id: str = ""
    ):
        """Acknowledge to the frontend the delivery result of an avatar-tap interaction, enabling retry and state wrap-up on the frontend."""
        if not interaction_id:
            return
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                await self.websocket.send_json(
                    {
                        "type": "avatar_interaction_ack",
                        "interaction_id": interaction_id,
                        "accepted": bool(accepted),
                        "reason": str(reason or ""),
                        "turn_id": str(turn_id or ""),
                    }
                )
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Avatar Interaction Ack Error: {e}")

    async def send_session_ended_by_server(self):  # Notify frontend server terminated session.
        """Notify the frontend that the session was terminated server-side (e.g. API disconnect), so it resets the session state"""
        payload = {"type": "session_ended_by_server", "input_mode": self.input_mode}
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                try:
                    await self.websocket.send_text(json.dumps(payload))
                except WebSocketDisconnect:
                    # Isolated on purpose: the CONNECTED check above and the
                    # send are separated by an await, so the display socket
                    # can die in between. Letting that reach the outer handler
                    # would skip the lease holder's copy below -- the one send
                    # that stops a live hardware microphone.
                    pass
                except Exception as e:
                    logger.error(f"💥 WS Send Session Ended By Server Error: {e}")
            # The terminal event is also the recorder's microphone teardown, and
            # the window holding the hardware is not necessarily the current
            # socket. Outside the guard on purpose: a dead current socket must
            # not swallow the lease holder's copy. Game owner exempt -- the
            # galgame gate owns the mic through the built-in game consumer and
            # tears down via GAME_ROUTE_ENDED.
            if getattr(self, "_voice_lease_owner", "none") != "game":
                await self._send_to_voice_owner(payload)
        except WebSocketDisconnect:
            # Client disconnected mid-send; this push is best-effort.
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Ended By Server Error: {e}")
