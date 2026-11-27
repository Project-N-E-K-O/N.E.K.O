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
"""Tool-calling surface for ``LLMSessionManager``: register/unregister
and sync of tools, the builtin tools (``recall_memory``, public knowledge,
and the two task tools ``dispatch_task`` / ``query_tasks``), and tool-call
dispatch.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import os

from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic import public_knowledge
from main_logic import nekopower_enable
from main_logic.activity.system_signals import is_remote_backend_deployment
from main_logic.core._shared import _reply_observation_owner
from main_logic.tool_calling import ToolCall, ToolDefinition, ToolOwnerCheck, ToolResult
from config.prompts.prompts_sys import _loc
from config.turn_context_settings import (
    RECALL_MEMORY_TOOL_BUDGET_MS,
    RECALL_MEMORY_TOOL_TIMEOUT_SECONDS,
)
from config.prompts.prompts_task_tools import (
    DISPATCH_TOOL_DESCRIPTION,
    DISPATCH_TOOL_NAME,
    DISPATCH_TOOL_TASK_DESCRIPTION,
    QUERY_TOOL_DESCRIPTION,
    QUERY_TOOL_NAME,
    QUERY_TOOL_QUESTION_DESCRIPTION,
    TASK_TOOL_EMPTY,
    TASK_TOOL_QUERY_UNREACHABLE,
    TASK_TOOL_UNCERTAIN,
    TASK_TOOL_UNREACHABLE,
)
from config.prompts.prompts_nekopower_enable import (
    ENABLE_ALREADY_ON,
    ENABLE_ASKED,
    ENABLE_COOLDOWN,
    ENABLE_FAILURE_REASONS,
    ENABLE_NEKOPOWER_TOOL_DESCRIPTION,
    ENABLE_NEKOPOWER_TOOL_NAME,
    ENABLE_REPORT_ACCEPTED,
    ENABLE_REPORT_ACCEPTED_NOT_RENEWED,
    ENABLE_REPORT_DECLINED,
    ENABLE_REPORT_DECLINED_COOLDOWN,
    ENABLE_REPORT_EXPIRED,
    ENABLE_REPORT_FAILED,
    ENABLE_REPORT_NOTE,
    ENABLE_REPORT_OFF_AGAIN,
    ENABLE_REPORT_WORDS,
    ENABLE_REMOTE,
    ENABLE_UNAVAILABLE,
    ENABLE_WAITING,
)
from config.prompts.prompts_memory import (
    RECALL_MEMORY_TOOL_DESCRIPTION,
    RECALL_MEMORY_TOOL_QUERY_DESCRIPTION,
    RECALL_MEMORY_TOOL_TIME_DESCRIPTION,
    RECALL_MEMORY_TOOL_NO_RESULT,
    RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN,
    RECALL_MEMORY_TOOL_FOUND_HEADER,
    _normalize_memory_prompt_lang,
)
from ._shared import logger


class ToolCallingMixin:
    """Tool-calling surface methods (see module docstring)."""

    def _only_looks_up(self, tool_name: str) -> bool:
        """Whether a tool is one of the built-in ones that only look something up.

        A turn in which she called nothing but these has not acted, so what
        she said in it is still checked afterwards (see ``cross_server``); any
        other tool call is an action whose result is the one judgement of
        that turn. A tool of another source that holds one of these names is
        not known to only look up.
        """
        if tool_name not in ("recall_memory", public_knowledge.TOOL_NAME, QUERY_TOOL_NAME):
            return False
        held = self.tool_registry.get(tool_name)
        return held is None or held.metadata.get("source") == "builtin"

    # ------------------------------------------------------------------
    # Tool calling — public API for agent_server / plugins
    # ------------------------------------------------------------------

    def register_tool(
        self,
        tool: ToolDefinition,
        *,
        replace: bool = True,
        owner_check: ToolOwnerCheck | None = None,
    ) -> None:
        """Register a tool with the unified registry.

        - ``tool.handler`` is an in-process callable (recommended) — same-process
          agent_bridge / built-in features take this path.
        - When ``tool.handler is None``, calls are routed to ``ToolRegistry``'s
          ``remote_dispatcher``, used for cross-process plugins / agent_server.
          The latter is attached by main_server at startup (HTTP forwarding to
          the corresponding plugin).

        ⚠️ This is the **synchronous** entry: it only updates registry state;
        session sync runs fire-and-forget via ``_fire_task``. If the caller needs
        to wait until "the tool is genuinely live on the wire" before returning,
        use ``await register_tool_and_sync(...)`` instead (the HTTP
        /api/tools/register endpoint already uses that path automatically).

        ``owner_check`` makes the write conditional on who holds the name; see
        ``ToolRegistry.register`` (raises ``ToolOwnershipError`` on refusal,
        nothing is synced then).
        """
        self.tool_registry.register(tool, replace=replace, owner_check=owner_check)
        self._fire_task(self._sync_tools_to_active_session())

    async def register_tool_and_sync(
        self,
        tool: ToolDefinition,
        *,
        replace: bool = True,
        owner_check: ToolOwnerCheck | None = None,
    ) -> None:
        """The awaitable version of ``register_tool``: registers, then waits for the session sync push to finish.

        For remote entries like HTTP `/api/tools/register` — by the time the
        caller gets the response, the tools on the active/pending sessions are
        already up to date, with no "returned ok but the next model call still
        can't see the tool" window. Serialization is guaranteed by
        ``_tool_sync_lock``: multiple concurrent registers can't put the wire's
        session.update out of order.

        ⚠️ ``raise_on_failure=True``: if the session.update genuinely fails on the
        wire, propagate the exception upward, so HTTP /api/tools doesn't return a
        false ok=true.

        ``owner_check``: as in ``register_tool``.
        """
        self.tool_registry.register(tool, replace=replace, owner_check=owner_check)
        await self._sync_tools_to_active_session(raise_on_failure=True)

    def unregister_tool(self, name: str, *, owner_check: ToolOwnerCheck | None = None) -> bool:
        """Remove a tool; ``owner_check`` makes it conditional, see ``ToolRegistry.unregister``."""
        holders = self._yielding_builtin_holders()
        existed = self.tool_registry.unregister(name, owner_check=owner_check)
        if existed:
            self._refill_vacated_builtins(holders)
            self._fire_task(self._sync_tools_to_active_session())
        return existed

    async def unregister_tool_and_sync(self, name: str, *, owner_check: ToolOwnerCheck | None = None) -> bool:
        """The awaitable version of ``unregister_tool``."""
        holders = self._yielding_builtin_holders()
        existed = self.tool_registry.unregister(name, owner_check=owner_check)
        if existed:
            self._refill_vacated_builtins(holders)
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return existed

    def list_tools(self) -> list[str]:
        return self.tool_registry.names()

    def clear_tools(self, *, source: str | None = None) -> int:
        holders = self._yielding_builtin_holders()
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._refill_vacated_builtins(holders)
            self._fire_task(self._sync_tools_to_active_session())
        return n

    async def clear_tools_and_sync(self, *, source: str | None = None) -> int:
        holders = self._yielding_builtin_holders()
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._refill_vacated_builtins(holders)
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return n

    def _make_tool_call_handler(self, session):
        async def _handle(call: ToolCall) -> ToolResult:
            return await self._on_tool_call(call, invoking_session=session)

        return _handle

    async def _on_tool_call(
        self,
        call: ToolCall,
        *,
        invoking_session=None,
    ) -> ToolResult:
        """Bridge invoked by both clients when the model emits a tool
        call. Forwards to the registry (process-global, outliving any
        single session), then routes any images the tool returned.
        """
        if not self._only_looks_up(call.name):
            self._turn_acted_through_tools = True
        light_bubble = getattr(self, "_push_focus_thinking", None)
        if light_bubble is not None and not getattr(self, "_turn_has_spoken", False):
            # She called a tool before saying anything this turn: the chat
            # shows the "…" bubble until her first words arrive
            # (send_lanlan_response clears it) or the turn ends.
            self._tool_wait_bubble = True
            try:
                await light_bubble(True)
            except Exception:
                logger.debug("[%s] lighting the tool-wait bubble failed", self.lanlan_name, exc_info=True)
        if call.reply_owner is not None:
            from main_logic.reply_tail import reply_tail_registry

            tool = self.tool_registry.get(call.name)
            if tool is not None and tool.handler is None and tool.metadata.get("reply_tail") is True:
                call.host_reply = reply_tail_registry.tool_context(
                    self, call.reply_owner, call.call_id, str(tool.metadata.get("source", "")),
                )
        if call.name == DISPATCH_TOOL_NAME:
            # The reply that emitted the call may finish after a new user input.
            # A provider without reply ownership has no attributable input here.
            owner_token = _reply_observation_owner.set(call.reply_owner)
            try:
                result = await self.tool_registry.execute(call)
            finally:
                _reply_observation_owner.reset(owner_token)
        else:
            result = await self.tool_registry.execute(call)
        if result.images:
            await self._route_tool_images(
                result, invoking_session=invoking_session,
            )
        return result

    async def _route_tool_images(
        self,
        result: ToolResult,
        *,
        invoking_session=None,
    ) -> None:
        """Get a tool's pictures in front of the model, or say they never got there.

        One choke point for both clients, because the answer depends on the
        session the call arrived on -- which a plugin cannot see, and which
        changes when the user swaps voice for text mid-conversation.

        An offline session is asked to make itself able to look
        (``prepare_for_tool_images``): the same permanent switch to the
        configured vision model that a dragged-in screenshot triggers in
        ``stream_text``, so a tool frame and a user frame reach the model by
        one route rather than two. A realtime session has no such route at
        all -- its ``function_call_output`` item carries a string.

        When the pixels cannot go, they are dropped here so nothing
        downstream has to re-check, and the drop is stated back to the model
        in ``_image_warnings``. Dropping them silently would be worse than
        either: she would answer as though she had looked.
        """
        session = invoking_session if invoking_session is not None else self.session
        if isinstance(session, OmniRealtimeClient):
            reason = "a realtime session can only hand a tool result back as text"
        elif not isinstance(session, OmniOfflineClient):
            reason = f"session type {type(session).__name__} has no image channel"
        elif await session.prepare_for_tool_images():
            # The client owns that call: only it knows which endpoint and SDK
            # it is currently speaking, and therefore whether it can reach a
            # vision model without breaking the tool loop that is running.
            return
        else:
            reason = "this session cannot reach a vision model"

        skipped = len(result.images)
        result.images = []
        logger.warning(
            "Tool '%s': %d image(s) not shown to the model (%s)",
            result.name, skipped, reason,
        )
        result.add_image_warnings(
            f"{skipped} tool image(s) were not shown to the model: {reason}"
        )

    # ------------------------------------------------------------------
    # Built-in pseudo tool: recall_memory
    # ------------------------------------------------------------------
    # Mechanism-level placeholder: first allow both offline and realtime paths to register, send
    # description / parameters on the wire, receive a model tool call, and return a result.
    # The handler currently always returns "No relevant memories found"; when real memory retrieval is wired up, only
    # replace ``_handle_recall_memory_call`` without changing registration / synchronization.

    def _register_builtin_tools(self) -> None:
        """Re-register the built-in tools, with description / parameter docs in the current
        ``user_language``. Calls ``tool_registry.register(replace=True)`` directly
        rather than the public ``register_tool``, to avoid firing unnecessary
        ``_sync_tools_to_active_session`` on hot paths like __init__ /
        start_session — this method's callers decide whether to sync.

        Kill-switch: set ``NEKO_DISABLE_BUILTIN_TOOLS=1`` to make this method
        return early without writing any builtin into the registry. Intended for
        A/B debugging of "suspected tool-schema-induced voice stream stutter /
        StepFun-proxy compatibility issues" — flip the switch → restart → the same
        frontend code runs in a "no builtin tools at all" state, comparing which
        baseline (with vs. without) misbehaves. Effective when the value is
        ``1`` / ``true`` / ``yes``.
        """
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            logger.info(
                "[builtin tools] NEKO_DISABLE_BUILTIN_TOOLS set — skipping recall_memory registration"
            )
            return
        _lang = _normalize_memory_prompt_lang(self.user_language)
        recall_tool = ToolDefinition(
            name="recall_memory",
            description=_loc(RECALL_MEMORY_TOOL_DESCRIPTION, _lang),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_QUERY_DESCRIPTION, _lang),
                    },
                    "time": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_TIME_DESCRIPTION, _lang),
                    },
                },
                # At least one of query / time is required: time alone performs time-based recall (independent of
                # content); query alone performs semantic retrieval. If both are empty, the handler returns early with
                # "No relevant memories found." Keep required empty and let the handler validate.
                "required": [],
            },
            handler=self._handle_recall_memory_call,
            metadata={"source": "builtin"},
        )
        self.tool_registry.register(recall_tool, replace=True)
        self._register_public_knowledge_tool()
        self._register_task_tools()

    # ------------------------------------------------------------------
    # Built-in tools: dispatch_task / query_tasks
    # ------------------------------------------------------------------
    # Her two task tools. They exist only while NekoPower is switched on for
    # this session (``_is_agent_enabled``), so a session without it does not
    # carry their definitions. Both take one sentence and no identifier: the
    # task brain in the agent process keeps the record and works out which
    # task a sentence is about. Proactive generation builds its own request
    # without any tool, so these never ride on it.

    def _register_task_tools(self) -> bool:
        """Register or remove the two task tools to match the switches. Returns whether anything changed."""
        enabled = False
        try:
            enabled = bool(self._is_agent_enabled())
        except Exception:
            enabled = False
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            # The kill-switch of ``_register_builtin_tools`` holds on this
            # path too (a switch update calls this directly).
            enabled = False
        changed = self._register_enable_nekopower_tool()
        if not enabled:
            for name in (DISPATCH_TOOL_NAME, QUERY_TOOL_NAME):
                current = self.tool_registry.get(name)
                if current is not None and current.metadata.get("source") == "builtin":
                    self.tool_registry.unregister(name)
                    changed = True
            return changed
        _lang = _normalize_memory_prompt_lang(self.user_language)
        definitions = (
            ToolDefinition(
                name=DISPATCH_TOOL_NAME,
                description=_loc(DISPATCH_TOOL_DESCRIPTION, _lang),
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": _loc(DISPATCH_TOOL_TASK_DESCRIPTION, _lang)},
                    },
                    "required": ["task"],
                },
                handler=self._handle_dispatch_task_call,
                metadata={"source": "builtin"},
            ),
            ToolDefinition(
                name=QUERY_TOOL_NAME,
                description=_loc(QUERY_TOOL_DESCRIPTION, _lang),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": _loc(QUERY_TOOL_QUESTION_DESCRIPTION, _lang)},
                    },
                },
                handler=self._handle_query_tasks_call,
                metadata={"source": "builtin"},
            ),
        )
        for definition in definitions:
            current = self.tool_registry.get(definition.name)
            if current is not None and current.metadata.get("source") != "builtin":
                # A tool of another source holds the name; it is left alone.
                continue
            if current is not None and current.description == definition.description:
                # Registered already, in this language: nothing to do. Every
                # status push calls this, so it must not register (and log) again.
                continue
            changed = True
            self.tool_registry.register(definition, replace=True)
        return changed

    # ------------------------------------------------------------------
    # Built-in tool: enable_nekopower (main_logic/nekopower_enable.py)
    # ------------------------------------------------------------------
    # Offered while the NekoPower master switch is off: she asks the user, in a
    # NekoPower question card, to let her switch it on. The tool answers at
    # once; the user's answer reaches her later as a report. On "yes" the main
    # server switches NekoPower on as the panel switch does and the session is
    # renewed, so the new one opens with the NekoPower prompt and the task tools
    # (owner, 2026-11-27).

    def _nekopower_master_on(self) -> bool:
        return bool((getattr(self, "agent_flags", None) or {}).get("agent_enabled", False))

    def _enable_nekopower_lang(self) -> str:
        return _normalize_memory_prompt_lang(self.user_language)

    def _register_enable_nekopower_tool(self) -> bool:
        """Offer ``enable_nekopower`` while the master switch is off, take it back once it is on.

        Returns whether anything changed.
        """
        offered = not self._nekopower_master_on()
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            offered = False
        current = self.tool_registry.get(ENABLE_NEKOPOWER_TOOL_NAME)
        if current is not None and current.metadata.get("source") != "builtin":
            # A tool of another source holds the name; it is left alone.
            return False
        if not offered:
            # Switched on otherwise (the panel) while a card waits: the card goes too.
            withdrawn = nekopower_enable.withdraw(getattr(self, "lanlan_name", "")) if self._nekopower_master_on() else None
            if withdrawn:
                self._fire_task(self._close_enable_nekopower_card(withdrawn))
            if current is None:
                return False
            self.tool_registry.unregister(ENABLE_NEKOPOWER_TOOL_NAME)
            return True
        definition = ToolDefinition(
            name=ENABLE_NEKOPOWER_TOOL_NAME,
            description=_loc(ENABLE_NEKOPOWER_TOOL_DESCRIPTION, self._enable_nekopower_lang()),
            parameters={"type": "object", "properties": {}},
            handler=self._handle_enable_nekopower_call,
            metadata={"source": "builtin"},
        )
        if current is not None and current.description == definition.description:
            return False
        self.tool_registry.register(definition, replace=True)
        return True

    async def _handle_enable_nekopower_call(self, arguments: dict) -> object:
        lang = self._enable_nekopower_lang()
        if self._nekopower_master_on():
            return _loc(ENABLE_ALREADY_ON, lang)
        if is_remote_backend_deployment():
            # The main server turns agent commands down here (501): the card could not be answered.
            return {"output": _loc(ENABLE_REMOTE, lang), "is_error": True}
        websocket = getattr(self, "websocket", None)
        if websocket is None:
            return {"output": _loc(ENABLE_UNAVAILABLE, lang), "is_error": True}
        outcome, question_id = nekopower_enable.ask(self.lanlan_name, lang=lang, name=self.lanlan_name)
        if outcome == nekopower_enable.COOLING_DOWN:
            minutes = nekopower_enable.cooldown_minutes_left(self.lanlan_name)
            return {"output": _loc(ENABLE_COOLDOWN, lang).format(minutes=minutes), "is_error": True}
        if outcome == nekopower_enable.WAITING:
            return {"output": _loc(ENABLE_WAITING, lang), "is_error": True}
        sent = False
        try:
            await websocket.send_json(nekopower_enable.card_frame(self.lanlan_name))
            sent = True
        except Exception as e:
            logger.warning("[%s] enable_nekopower: card not sent: %s", self.lanlan_name, type(e).__name__)
            return {"output": _loc(ENABLE_UNAVAILABLE, lang), "is_error": True}
        finally:
            if not sent:
                # Taken down whatever stopped the sending, a cancelled tool call too (the user
                # speaking over her): a card left up with no expiry would block her for good.
                nekopower_enable.withdraw(self.lanlan_name, question_id)
        self._fire_task(self._expire_enable_nekopower_card(question_id))
        return _loc(ENABLE_ASKED, lang)

    async def _close_enable_nekopower_card(self, question_id: str) -> None:
        """Take the card down on the page (every window of it): answered, expired, or no longer needed."""
        websocket = getattr(self, "websocket", None)
        if websocket is None:
            return
        try:
            await websocket.send_json(nekopower_enable.closed_frame(self.lanlan_name, question_id))
        except Exception as e:
            # The page is gone: it reads the cards that wait when it comes back (/api/agent/questions).
            logger.debug("[%s] enable_nekopower: card not taken down on the page: %s", self.lanlan_name, type(e).__name__)

    async def _expire_enable_nekopower_card(self, question_id: str) -> None:
        await asyncio.sleep(nekopower_enable.CARD_SECONDS)
        if nekopower_enable.withdraw(self.lanlan_name, question_id) is None:
            return
        await self._close_enable_nekopower_card(question_id)
        self._report_enable_nekopower(_loc(ENABLE_REPORT_EXPIRED, self._enable_nekopower_lang()))

    def _report_enable_nekopower(self, text: str, note: str = "") -> None:
        """Hand her a report; what the user typed beside a click (``note``) goes with it."""
        lang = self._enable_nekopower_lang()
        if (note or "").strip():
            text = text + _loc(ENABLE_REPORT_NOTE, lang).format(text=note.strip())
        self.submit_proactive_callback(nekopower_enable.report(self.lanlan_name, text, lang=lang))

    def _renew_for_nekopower_on(self) -> "asyncio.Task":
        """The one renewal for NekoPower coming on: the panel switch and the card share it.

        ``update_agent_flags`` starts it as the master switch comes on mid-session and
        counts each time it comes on; ``after_nekopower_enabled`` waits for the renewal of
        the same coming on (under way or done) instead of renewing again.
        """
        count = getattr(self, "_nekopower_on_count", 0)
        held = getattr(self, "_nekopower_renewal", None)
        if held is not None and held[0] == count:
            return held[1]
        task = self._fire_task(self._renew_once_dropped())
        self._nekopower_renewal = (count, task)
        return task

    async def _renew_once_dropped(self) -> bool:
        """Renew, once an earlier renewal's dropping is over: its cleanup must not take this one's preparation."""
        dropping = getattr(self, "_nekopower_dropping", None)
        if dropping is not None and not dropping.done():
            await asyncio.wait({dropping})
        return await self.renew_session_now()

    def _drop_nekopower_renewal(self) -> None:
        """NekoPower went off again (a command taken back, the panel): a renewal under way for it is dropped.

        The session it prepared goes too, or the end of a turn would swap it in with
        the NekoPower opening while NekoPower is off.
        """
        held = getattr(self, "_nekopower_renewal", None)
        if held is None:
            return
        self._nekopower_renewal = None
        renewal = held[1]
        if renewal.done():
            # Swapped in already: nothing of it is left to drop. Not swapped in (it gave up
            # waiting): the session it prepared may still be swapped in later, and goes.
            swapped = not renewal.cancelled() and renewal.exception() is None and renewal.result() is True
            if swapped:
                return
        else:
            renewal.cancel()
        self._nekopower_dropping = self._fire_task(self._drop_prepared_session())

    async def _drop_prepared_session(self) -> None:
        swap = getattr(self, "final_swap_task", None)
        if getattr(self, "is_hot_swap_imminent", False) or (swap is not None and not swap.done()):
            # A swap under way may have closed the old connection already: cut short, it
            # could leave no session at all. It is let finish.
            return
        await self._cleanup_pending_session_resources()
        await self._reset_preparation_state()

    async def answer_enable_nekopower_card(self, question_id: str, *, choice: str = "", text: str = "") -> str:
        """The user answered the card.

        On "yes" the caller switches NekoPower on, then calls ``after_nekopower_enabled``.
        """
        outcome = nekopower_enable.answer(self.lanlan_name, question_id, choice=choice, text=text)
        if outcome == nekopower_enable.STALE:
            return outcome
        await self._close_enable_nekopower_card(question_id)
        lang = self._enable_nekopower_lang()
        if outcome == nekopower_enable.DECLINED:
            # The second "no" in a row: she is told she may not ask again for a while.
            cooled = nekopower_enable.cooling_down(self.lanlan_name)
            self._report_enable_nekopower(
                _loc(ENABLE_REPORT_DECLINED_COOLDOWN if cooled else ENABLE_REPORT_DECLINED, lang), note=text,
            )
        elif outcome == nekopower_enable.WORDS:
            self._report_enable_nekopower(_loc(ENABLE_REPORT_WORDS, lang).format(text=(text or "").strip()))
        return outcome

    async def after_nekopower_enabled(self, failure: str = "", *, note: str = "") -> None:
        """After a "yes": renew the session so it opens with NekoPower on, then tell her (owner, 2026-11-27).

        ``failure`` says why switching it on failed (a key of ``ENABLE_FAILURE_REASONS``);
        empty when it came on. ``note``: what the user typed beside the click.
        """
        lang = self._enable_nekopower_lang()
        if failure:
            reason = _loc(ENABLE_FAILURE_REASONS.get(failure) or ENABLE_FAILURE_REASONS["unknown"], lang)
            self._report_enable_nekopower(_loc(ENABLE_REPORT_FAILED, lang).format(reason=reason), note=note)
            return
        if not self._nekopower_master_on():
            # Switched off again on the panel while the command was on its way: the newer
            # switch stands (_Unconfirmed), and she is not told it is on.
            self._report_enable_nekopower(_loc(ENABLE_REPORT_OFF_AGAIN, lang), note=note)
            return
        renewal = self._renew_for_nekopower_on()
        # Waited for without taking on its fate: a renewal dropped because NekoPower went off
        # again is not this report's cancellation.
        await asyncio.wait({renewal})
        renewed = False
        if not renewal.cancelled():
            if renewal.exception() is not None:
                logger.warning(
                    "[%s] enable_nekopower: session not renewed: %s",
                    self.lanlan_name, type(renewal.exception()).__name__,
                )
            else:
                renewed = bool(renewal.result())
        if not self._nekopower_master_on():
            # Switched off while the session was being renewed: as above.
            self._report_enable_nekopower(_loc(ENABLE_REPORT_OFF_AGAIN, lang), note=note)
            return
        if not renewed:
            # The tools reached the live session all the same (update_agent_flags), save on
            # Gemini Live, which takes them at connect: she is told to look before she acts.
            logger.info("[%s] enable_nekopower: reporting without a renewed session", self.lanlan_name)
        self._report_enable_nekopower(
            _loc(ENABLE_REPORT_ACCEPTED if renewed else ENABLE_REPORT_ACCEPTED_NOT_RENEWED, lang), note=note,
        )

    async def _post_to_task_system(self, path: str, body: dict) -> tuple[dict | None, bool]:
        """POST one request of a task tool to the agent process.

        Returns ``(answer, may_have_arrived)``. ``answer`` is None when no
        usable answer came back. ``may_have_arrived`` then tells the two ways
        of failing apart: False when the request was never delivered or was
        turned down before anything was done with it (no connection, a 4xx
        status), True when it went out and the answer did not come back in
        time, could not be read, or was an error of the agent process's own
        (any other status). In the second case the agent process may be
        carrying it out all the same.

        ``TASK_TOOL_TIMEOUT_SECONDS`` is the limit on the whole request. The
        HTTP client applies its timeout to connecting, sending and reading
        each on their own, which could add up to several times that.
        """
        import httpx

        from config import TOOL_SERVER_PORT
        from config.nekopower_settings import TASK_TOOL_TIMEOUT_SECONDS
        from utils.internal_http_client import InternalServiceSigningError, get_internal_http_client

        try:
            client = get_internal_http_client()
            resp = await asyncio.wait_for(
                client.post(
                    f"http://127.0.0.1:{TOOL_SERVER_PORT}{path}",
                    json=body,
                    timeout=TASK_TOOL_TIMEOUT_SECONDS,
                ),
                TASK_TOOL_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            InternalServiceSigningError,
        ) as exc:
            logger.warning("[task tools] %s not delivered (%s)", path, type(exc).__name__)
            return None, False
        except Exception as exc:
            # Running out of the overall limit lands here as well: which
            # phase it was in is not known, so neither is whether it arrived.
            logger.warning("[task tools] %s sent, no answer (%s)", path, type(exc).__name__)
            return None, True
        if not resp.is_success:
            logger.warning("[task tools] %s returned status=%s", path, resp.status_code)
            # A 4xx is the agent process turning the request down before it
            # did anything with it. Any other status can come after the task
            # was taken on (an error while its receipt was being written).
            return None, not 400 <= resp.status_code < 500
        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("[task tools] %s answered with something unreadable (%s)", path, type(exc).__name__)
            return None, True
        if not isinstance(data, dict):
            return None, True
        return data, True

    async def _handle_dispatch_task_call(self, arguments: dict) -> str:
        """Handler for ``dispatch_task``: hand her sentence to the task brain and return its receipt.

        The receipt is the conclusion of the task brain's first round (already
        under way / nothing can do this / accepted, or what became of a stop,
        an addition or an answer). It does not wait for the work itself.

        Logging follows ``recall_memory``: INFO carries names and outcome
        only, never what she or the user said.
        """
        _lang = _normalize_memory_prompt_lang(self.user_language)
        args_dict = arguments if isinstance(arguments, dict) else {}
        task = args_dict.get("task")
        task = task.strip() if isinstance(task, str) else ""
        if not task:
            return _loc(TASK_TOOL_EMPTY, _lang)
        owner = _reply_observation_owner.get()
        input_id = getattr(owner, "observation_input_id", None)
        current_turn = str(getattr(self, "current_speech_id", "") or "")
        dispatch_turn = getattr(owner, "speech_id", None) or current_turn
        user_text = getattr(self, "_latest_user_utterance", "") if dispatch_turn == current_turn else ""
        data, may_have_arrived = await self._post_to_task_system(
            "/nekopower/dispatch",
            {
                "text": task,
                "lanlan_name": self.lanlan_name,
                # The user's own words of this turn, as far as they are known
                # by now; plugins get them as ``_ctx.latest_user_request``.
                "user_text": user_text or "",
                **({"source_message_ref": f"conversation_input:{input_id}"} if input_id else {}),
                "conversation_id": dispatch_turn,
                "language": self.user_language,
            },
        )
        text = str((data or {}).get("text") or "").strip()
        logger.info(
            "[dispatch_task] called by name=%s mode=%s lang=%s → kind=%s",
            self.lanlan_name, self.input_mode, _lang,
            (data or {}).get("kind") or ("uncertain" if may_have_arrived else "unreachable"),
        )
        if text:
            return text
        return _loc(TASK_TOOL_UNCERTAIN if may_have_arrived else TASK_TOOL_UNREACHABLE, _lang)

    async def _handle_query_tasks_call(self, arguments: dict) -> str:
        """Handler for ``query_tasks``: ask the task brain what is under way or whether something can be done."""
        _lang = _normalize_memory_prompt_lang(self.user_language)
        args_dict = arguments if isinstance(arguments, dict) else {}
        question = args_dict.get("question")
        question = question.strip() if isinstance(question, str) else ""
        data, _may_have_arrived = await self._post_to_task_system(
            "/nekopower/query",
            {"text": question, "lanlan_name": self.lanlan_name, "language": self.user_language},
        )
        text = str((data or {}).get("text") or "").strip()
        logger.info(
            "[query_tasks] called by name=%s mode=%s lang=%s → %s",
            self.lanlan_name, self.input_mode, _lang, "answered" if text else "unreachable",
        )
        return text or _loc(TASK_TOOL_QUERY_UNREACHABLE, _lang)

    # ------------------------------------------------------------------
    # Built-in tool: query_public_knowledge
    # ------------------------------------------------------------------
    # Public knowledge lives in Memory Server (/internal/knowledge/*); Main only provides the HTTP
    # client. Register this tool only when a usable knowledge package exists, so users who imported none do not need to
    # include its schema in every turn. Availability is cached in main_logic.public_knowledge; when it changes, callback _on_public_knowledge_availability re-registers and syncs it to the session.
    #

    def _register_public_knowledge_tool(self) -> None:
        # This optional tool must not affect recall_memory registration if anything goes wrong.
        try:
            public_knowledge.add_availability_listener(self)
            port = getattr(self, "memory_server_port", None)
            if port is not None:
                public_knowledge.schedule_availability_refresh(port)
            # If a plugin or other source registered a tool with the same name, do not overwrite or delete it.
            current = self.tool_registry.get(public_knowledge.TOOL_NAME)
            ours = current is None or public_knowledge.is_builtin_definition(current)
            if not ours:
                return
            if public_knowledge.tool_available():
                self.tool_registry.register(
                    public_knowledge.build_tool_definition(
                        self.user_language, self._handle_public_knowledge_call,
                    ),
                    replace=True,
                )
            elif current is not None:
                self.tool_registry.unregister(public_knowledge.TOOL_NAME)
        except Exception as e:
            logger.warning(
                "[public-knowledge] builtin tool registration failed: %s",
                type(e).__name__,
            )

    def _yielding_builtin_holders(self) -> dict[str, ToolDefinition | None]:
        """The tools holding the names of the builtins that yield to other sources, before a removal."""
        return {
            name: self.tool_registry.get(name)
            for name in (public_knowledge.TOOL_NAME, DISPATCH_TOOL_NAME, QUERY_TOOL_NAME, ENABLE_NEKOPOWER_TOOL_NAME)
        }

    def _refill_vacated_builtins(self, previous_holders: dict[str, ToolDefinition | None] | None) -> None:
        """Take a builtin's name back from another source's tool just removed.

        Only for builtins that yield to same-name tools of other sources
        (``query_public_knowledge``, the two task tools and ``enable_nekopower``): when such a tool
        goes away neither the availability flag nor a switch changes, so
        nothing else would restore the builtin. Removing the builtin itself
        (e.g. clearing the "builtin" source) is respected: nothing is put back.
        """
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            return
        previous_holders = previous_holders or {}

        def vacated(name: str) -> bool:
            held = previous_holders.get(name)
            return (
                held is not None
                and (held.metadata or {}).get("source") != "builtin"
                and self.tool_registry.get(name) is None
            )

        if vacated(public_knowledge.TOOL_NAME) and public_knowledge.tool_available():
            self._register_public_knowledge_tool()
        if vacated(DISPATCH_TOOL_NAME) or vacated(QUERY_TOOL_NAME) or vacated(ENABLE_NEKOPOWER_TOOL_NAME):
            # Registers what the switches call for and leaves the rest alone
            # (enable_nekopower is registered on the same path).
            self._register_task_tools()

    def _on_public_knowledge_availability(self) -> None:
        """Availability flipped: re-register and push to live sessions."""
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            return
        self._register_public_knowledge_tool()
        self._fire_task(self._sync_tools_to_active_session())

    async def _handle_public_knowledge_call(self, arguments: dict) -> str:
        return await public_knowledge.query_public_knowledge(
            arguments,
            memory_server_port=self.memory_server_port,
            language=self.user_language,
        )

    async def _handle_recall_memory_call(self, arguments: dict) -> str:
        """Handler for ``recall_memory`` — calls memory_server's
        ``/query_memory/{lanlan_name}`` over HTTP to run hybrid BM25 + cosine
        recall, formats the results as markdown bullets and returns them to the
        model.

        Logging is split into two tiers (privacy):
        - **INFO**: only reports name / mode / session / lang / hit count /
          elapsed ms. *No raw query text / no raw recalled text*.
          INFO persists to ``D:/Documents/N.E.K.O/logs/N.E.K.O_Main_<date>.log``
          and may be bundled and shipped out; raw memory text (containing user
          privacy) must not appear there.
        - **DEBUG**: only here do the raw query / recalled id list / full args
          land. DEBUG is hidden from the console by default and only goes to the
          project-dir _debug_ file, never shipped out.

        Failure fallback: if HTTP dies at any stage → return "no relevant
        memories found" as an empty result, letting the model continue the
        conversation flow. Never raise to the upstream wire, or one failed tool
        call would stall the model's whole turn.
        """
        _lang = _normalize_memory_prompt_lang(self.user_language)
        args_dict = arguments if isinstance(arguments, dict) else {}
        query = ""
        raw_query = args_dict.get("query")
        if isinstance(raw_query, str):
            query = raw_query.strip()
        time_arg = ""
        raw_time = args_dict.get("time")
        if isinstance(raw_time, str):
            time_arg = raw_time.strip()
        session_kind = type(self.session).__name__ if self.session is not None else "no-session"

        # Return early on empty input: the model sometimes sends empty args to "check whether the tool is available," avoiding an HTTP call.
        # But input with time (time-based recall does not require query) is not empty.
        if not query and not time_arg:
            logger.info(
                "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
                "→ empty query, no fetch",
                self.lanlan_name, self.input_mode, session_kind, _lang,
            )
            logger.debug("[recall_memory] empty-query args=%s", args_dict)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # POST to memory_server. Always forward query unchanged; do not clear it when time is present
        # — downstream routing: query + time → hybrid_recall(query, time_window=...) performs
        # joint "semantic + temporal" retrieval (rank by query within the window while retaining semantic matches); time alone
        # → pure time-proximity recall; if time parsing fails, query still enables semantic-retrieval fallback.
        post_body = {
            "query": query,
            "language": self.user_language,
            # If the server exceeds this budget, use keyword results only instead of timing out completely and reporting "not found."
            "budget_ms": RECALL_MEMORY_TOOL_BUDGET_MS,
        }
        if time_arg:
            post_body["time"] = time_arg
        result_payload: dict = {}
        recall_request_ok = False  # Set true only when Memory Server actually returns successfully
        try:
            from utils.internal_http_client import get_internal_http_client
            client = get_internal_http_client()
            resp = await client.post(
                f"http://127.0.0.1:{self.memory_server_port}/query_memory/{self.lanlan_name}",
                json=post_body,
                timeout=RECALL_MEMORY_TOOL_TIMEOUT_SECONDS,
            )
            if not resp.is_success:
                # WARNING logs include only status + body length (non-sensitive metadata); the raw body
                # is a string returned across a process boundary and may include echoed query / error details
                # or other contextual content, so log it at DEBUG under the privacy-tier policy established by PR #1384.
                body_text = resp.text or ""
                logger.warning(
                    "[recall_memory] memory_server returned status=%s body_len=%d",
                    resp.status_code, len(body_text),
                )
                logger.debug(
                    "[recall_memory] non-success response body=%r",
                    body_text[:500],
                )
            else:
                result_payload = resp.json()
                recall_request_ok = True
        except Exception as exc:
            logger.warning(
                "[recall_memory] memory_server call failed (%s: %s); "
                "returning empty result",
                type(exc).__name__, exc,
            )

        results = result_payload.get("results") if isinstance(result_payload, dict) else None
        results = results if isinstance(results, list) else []
        elapsed_ms = result_payload.get("elapsed_ms", 0) if isinstance(result_payload, dict) else 0

        # INFO records only has_time (boolean), never the original time_arg — time_arg is raw user
        # input, and under the privacy-tier policy documented in this function's docstring (INFO may be packaged and sent externally)
        # The raw text goes only to DEBUG below.
        logger.info(
            "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
            "has_time=%s → hits=%d elapsed=%.0fms",
            self.lanlan_name, self.input_mode, session_kind, _lang,
            bool(time_arg), len(results), elapsed_ms,
        )
        logger.debug(
            "[recall_memory] args=%s query=%r time=%r ids=%s",
            args_dict, query, time_arg,
            [r.get("id") for r in results],
        )

        if not results:
            # Both query and time were provided but there were zero hits: likely the filters were too narrow together
            # (no semantically matching entries inside the time window). Do not simply report "no memories found" and make the model give up;
            # prompt it to broaden the search — retry with only time or only query.
            # Show the broader-search hint only when the request **actually succeeded**: non-2xx / exceptions also become
            # results=[], but that indicates a temporary Memory Server failure; do not mislead the model into "retrying with different filters"
            # and wasting the tool-iteration budget that was just tightened.
            if recall_request_ok and query and time_arg:
                return _loc(RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN, _lang).format(query=query)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # Render the entire section in a worker thread: truncate_to_tokens encodes the **pre-truncation**
        # raw text, and this path exists specifically because "upstream may return one extremely long merged
        # reflection." tiktoken has quadratic fallback on chunks it cannot split; running it synchronously on
        # the event loop could also stall voice sessions. Keep rendering itself a synchronous function (matching
        # memory_bridge.render_relevant_memory on the plugin side and directly testable); offload
        # only at this sole async call site.
        return await asyncio.to_thread(self._render_recall_block, results, _lang)

    @staticmethod
    def _render_recall_block(results: list, _lang: str) -> str:
        """Render recall hits through the shared entry point, with a header.

        Format: ``1. [fact/about user] text  (2026-05-01, 23 days ago)``, under an
        i18n overview line.

        No line building happens here. ``memory.recall_render`` is the one
        place recall results become prompt text — it owns the label table,
        the time anchor and the token budgets, and it owns them once so
        this side and the QQ plugin's ``render_relevant_memory`` cannot
        drift apart (issue #2588; the two used to be hand-written twins).
        This method supplies the locale and the overview line, which the
        entry point charges to the same budget because it lands in the same
        string the model reads.

        Entry count here comes from hybrid_recall's fusion cap rather than
        the plugin's limit=5, so this is the side where the block cap
        actually binds.

        Deliberately synchronous, and called through ``asyncio.to_thread``
        — see the caller.
        """  # noqa: DOCSTRING_CJK
        from config import RECALL_RENDER_TOTAL_MAX_TOKENS
        from memory.recall_render import render_recall_block

        block = render_recall_block(
            results, _lang,
            header_template=_loc(RECALL_MEMORY_TOOL_FOUND_HEADER, _lang),
        )
        if block.dropped:
            logger.info(
                "[recall_memory] rendered block over %d tok budget; dropped "
                "trailing %d entries", RECALL_RENDER_TOTAL_MAX_TOKENS, block.dropped,
            )
        return block.text

    async def _sync_tools_to_active_session(self, *, raise_on_failure: bool = False) -> None:
        """Sync the registry's current state to all active clients.

        Covers:
        - ``self.session``: the currently active main session
        - ``self.pending_session``: the session prewarming during hot-swap (the
          window where the new neko is built but not yet formally swapped).
          Without syncing it, tools registered via register_tool before
          pending_session takes over would be lost after the hot-swap completes.

        ``apply_tools_to_session`` is only meaningful for ``OmniRealtimeClient``
        instances with a live ws connection; offline clients just rely on
        ``set_tools`` picking up the new snapshot at the next ``stream_text``.

        ⚠️ Serialization: ``_tool_sync_lock`` guarantees that concurrent calls
        push session.update one by one in call order. Otherwise the wire events
        from back-to-back ``register_tool / unregister_tool / clear_tools`` could
        arrive out of order, and the last snapshot might not match the registry's
        final state.
        """
        async with self._tool_sync_lock:
            # Read registry only inside the lock to ensure the snapshot is the actual value while the lock is held
            # (not the stale value from when the item was queued).
            defs = self.tool_registry.all()
            targets = []
            if self.session is not None:
                targets.append(self.session)
            if self.pending_session is not None and self.pending_session is not self.session:
                targets.append(self.pending_session)
            if not targets:
                return
            errors: list[str] = []
            for sess in targets:
                role = "pending" if sess is self.pending_session else "active"
                try:
                    if hasattr(sess, "set_tools"):
                        sess.set_tools(defs)
                    if hasattr(sess, "set_tool_call_handler"):
                        sess.set_tool_call_handler(
                            self._make_tool_call_handler(sess)
                        )
                    if isinstance(sess, OmniRealtimeClient) and sess.ws is not None:
                        await sess.apply_tools_to_session()
                except Exception as e:
                    err_text = f"{role}: {type(e).__name__}: {e}"
                    logger.warning("⚠️ Tool sync to %s session failed: %s", role, e)
                    errors.append(err_text)
            if errors and raise_on_failure:
                # Give ``*_and_sync`` callers an explicit signal that the change did not take effect on the wire,
                # so HTTP /api/tools does not return a false ok=true success.
                raise RuntimeError("tool sync failed: " + "; ".join(errors))
