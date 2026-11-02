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
and sync of tools, the builtin ``recall_memory`` tool, and tool-call
dispatch.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import asyncio
import os

from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic import public_knowledge
from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
from services.chat_sync.source import mark_tool_turn
from config.prompts.prompts_sys import _loc
from config.turn_context_settings import (
    RECALL_MEMORY_TOOL_BUDGET_MS,
    RECALL_MEMORY_TOOL_TIMEOUT_SECONDS,
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

    # ------------------------------------------------------------------
    # Tool calling — public API for agent_server / plugins
    # ------------------------------------------------------------------

    def register_tool(self, tool: ToolDefinition, *, replace: bool = True) -> None:
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
        """
        self.tool_registry.register(tool, replace=replace)
        self._fire_task(self._sync_tools_to_active_session())

    async def register_tool_and_sync(self, tool: ToolDefinition, *, replace: bool = True) -> None:
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
        """
        self.tool_registry.register(tool, replace=replace)
        await self._sync_tools_to_active_session(raise_on_failure=True)

    def unregister_tool(self, name: str) -> bool:
        holder = self._public_knowledge_holder()
        existed = self.tool_registry.unregister(name)
        if existed:
            self._refill_vacated_builtins(holder)
            self._fire_task(self._sync_tools_to_active_session())
        return existed

    async def unregister_tool_and_sync(self, name: str) -> bool:
        holder = self._public_knowledge_holder()
        existed = self.tool_registry.unregister(name)
        if existed:
            self._refill_vacated_builtins(holder)
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return existed

    def list_tools(self) -> list[str]:
        return self.tool_registry.names()

    def clear_tools(self, *, source: str | None = None) -> int:
        holder = self._public_knowledge_holder()
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._refill_vacated_builtins(holder)
            self._fire_task(self._sync_tools_to_active_session())
        return n

    async def clear_tools_and_sync(self, *, source: str | None = None) -> int:
        holder = self._public_knowledge_holder()
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._refill_vacated_builtins(holder)
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return n

    def _make_tool_call_handler(self, session):
        if isinstance(session, OmniRealtimeClient):
            # Accepting a provider call and executing it are different phases.
            # The source marker must reach the queue before any terminal, even
            # if the asynchronous handler has not received CPU yet.
            session.on_tool_call_accepted = lambda call: self._note_realtime_tool_source(
                call, invoking_session=session,
            )
        async def _handle(call: ToolCall) -> ToolResult:
            return await self._on_tool_call(call, invoking_session=session)

        return _handle

    def _note_realtime_tool_source(self, call: ToolCall, *, invoking_session) -> None:
        owner = call.source_owner
        if (owner is None or invoking_session is not getattr(self, "session", None)
            or not invoking_session._tool_task_owner_is_current(owner)):
            return
        # Never borrow current_speech_id: the provider owner was frozen before
        # scheduling and a terminal may already have rotated the live host ID.
        speech_id = owner.host_turn_id
        if call.provider_meta.get("chat_original_source") is not None:
            return
        mark_tool_turn(self, speech_id)
        proof = getattr(self, "_chat_original_turns", {}).get(str(speech_id or ""))
        if proof is not None:
            # Reuse the fixed proof in existing per-call host metadata. The
            # legacy async fallback must not requeue a completed reply's marker.
            call.provider_meta["chat_original_source"] = dict(proof)
        if not getattr(invoking_session, "_is_gemini", False):
            # Raw continuations use the existing user scope. Gemini instead
            # carries its explicit dispatch/tool-response source tokens.
            self._chat_original_tool_owner = (invoking_session, owner, speech_id)

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
        # Tool evidence belongs to the invoking reply, before execution can
        # yield to another turn. A visible preface must stay in the same local
        # record as the later result; never relabel a newer reply on completion.
        owner = call.reply_owner
        if owner is not None:
            if not getattr(owner, "turn_ended", False):
                mark_tool_turn(self, getattr(owner, "speech_id", None))
        elif call.source_owner is not None:
            # Retain provenance for direct/legacy handler calls as well as the
            # synchronous acceptance observer; both enforce the frozen owner.
            self._note_realtime_tool_source(call, invoking_session=invoking_session)
        if call.reply_owner is not None:
            from main_logic.reply_tail import reply_tail_registry

            tool = self.tool_registry.get(call.name)
            if tool is not None and tool.handler is None and tool.metadata.get("reply_tail") is True:
                call.host_reply = reply_tail_registry.tool_context(
                    self, call.reply_owner, call.call_id, str(tool.metadata.get("source", "")),
                )
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

    def _public_knowledge_holder(self) -> ToolDefinition | None:
        """The tool holding the ``query_public_knowledge`` name, before a removal."""
        return self.tool_registry.get(public_knowledge.TOOL_NAME)

    def _refill_vacated_builtins(self, previous_holder: ToolDefinition | None) -> None:
        """Take the builtin's name back from another source's tool just removed.

        Only for optional builtins that yield to same-name tools of other
        sources (``query_public_knowledge``): when such a tool goes away the
        availability flag does not change, so no availability callback would
        restore the builtin. Removing the builtin itself (e.g. clearing the
        "builtin" source) is respected: nothing is put back.
        """
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            return
        if previous_holder is None or public_knowledge.is_builtin_definition(previous_holder):
            return
        if self.tool_registry.get(public_knowledge.TOOL_NAME) is None and public_knowledge.tool_available():
            self._register_public_knowledge_tool()

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
          window where the new catgirl is built but not yet formally swapped).
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
