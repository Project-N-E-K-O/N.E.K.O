# -- coding: utf-8 --
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

from utils.screen_comment_guard import project_screen_history, strip_screen_labels

from ._turn_context import _TurnContextMixin, is_turn_context_message
from ._shared import (
    _answered_chunk,
    _generation_check,
    _find_by_identity,
    _same_route,
    HumanMessage,
    LLMStreamChunk,
    List,
    OnToolCallCallback,
    Optional,
    ToolCall,
    ToolDefinition,
    ToolLeakFilter,
    ToolResult,
    asyncio,
    log_tool_leak_filtered,
    logger,
    parse_arguments_json,
    strip_thinking_segments,
)

from ._genai_support import (
    _GenaiToolsUnsupported,
    _should_use_genai_sdk,
)
from ._lifecycle import _suspend_dialog_slop
from main_logic.tool_calling import (
    _TOOL_IMAGE_TURN_MAX_B64_BYTES,
    _TOOL_IMAGE_TURN_MAX_COUNT,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_tool import (
    TOOL_IMAGE_CAPTION,
    TOOL_IMAGE_DEFAULT_CAPTION,
    TOOL_IMAGE_HISTORY_PLACEHOLDER,
    TOOL_IMAGE_OMITTED_WARNING,
    TOOL_IMAGE_RECALL_HANDLE,
    TOOL_IMAGE_RECALL_HINT,
    TOOL_ROUND_PROMPT_PLACEHOLDER,
    normalize_tool_image_locale,
)


# Phrasing in rejected-tools errors indicating only this request's combination is unsupported.
_TOOLS_REFUSAL_REQUEST_QUALIFIERS = (
    "with image",
    "with vision",
    "with audio",
    "in combination with",
    "together with",
    "when using",
    "for this request",
)


class _ToolingMixin(_TurnContextMixin):
    # The request view below splices the session's resident turn context
    # (``_turn_context``), so every client that builds one carries it.
    def _dialog_messages_for_provider(self, messages, *, instruction=None, own_rounds=None):
        """Build the request view of ``messages``; the saved history is untouched.

        ``instruction`` is a ``prompt_ephemeral`` turn's message, which the
        request carries but history never saves. It goes right before the
        first of ``own_rounds`` (that turn's tool rounds, by identity) still
        in ``messages``, so each round follows the message it answers, or at
        the end while the turn has none.

        Then tool-call bookkeeping the provider would reject is dropped, on
        a copy: a round that is still executing (or was cancelled mid-batch)
        sits in the shared history while another turn builds its request, and
        an ``assistant(tool_calls)`` without its tool replies is a 400. This
        comes before the screen projection so that it judges the message order
        the provider receives: a dropped tool reply no longer separates the
        assistant messages around it.

        Then screen-comment chains in assistant history are cut back to their
        first comment, without source labels (``utils.screen_comment_guard``).
        No user wording restores the removed comments.

        Only history up to the current turn's user message is projected. What
        this turn added after it (text already streamed to the user with its
        tool calls, tool results, tool images) is not cut, so the model sees
        every comment it really said and does not repeat one; only the source
        labels are removed from it. A retry re-sends those messages too.

        Last, a tool round left with no user turn before it gets a stand-in
        (``_seat_tool_rounds``).

        Before all of this, the session's resident turn context (knowledge
        kept beside the history, ``_turn_context``) goes back right after the
        user messages it answered, so every request of the session carries it
        at the same place.
        """
        context_ids = self._turn_context_message_ids(instruction)
        messages = self._with_resident_context(messages)
        if instruction is not None:
            messages = self._with_instruction(messages, instruction, own_rounds)
        # The synthetic context blocks are not the user's turn: the screen
        # projection still cuts its head right after the real user message.
        projected = self._projected_dialog_messages(messages, context_ids=context_ids)
        return self._seat_tool_rounds(projected)

    def _turn_request_view(self, overrides):
        """Pop a tool loop's turn parameters from ``overrides``.

        Returns ``(tool_rounds, instruction, request_view)``. The turn's rounds
        place its instruction in every request view, so a loop called without
        the caller's list still keeps its own.
        """
        tool_rounds = overrides.pop("_tool_rounds", None)
        if tool_rounds is None:
            tool_rounds = []
        instruction = overrides.pop("_instruction", None)

        def request_view(messages):
            return self._dialog_messages_for_provider(
                messages, instruction=instruction, own_rounds=tool_rounds,
            )
        return tool_rounds, instruction, request_view

    def _proactive_round_generation(self, message):
        """The generation of the ``prompt_ephemeral`` reply that saved the
        tool round ``message``; None when no recent one did.

        The request view rewrites some rounds into copies (screen labels cut
        from their text) that keep the saved round's ``tool_calls`` list, so
        that list identifies a round as well as the round itself.
        """
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        for generation, rounds in getattr(self, "_proactive_turn_rounds", ()):
            if any(
                round_ is message
                or (calls is not None and round_.get("tool_calls") is calls)
                for round_ in rounds
            ):
                return generation
        return None

    @staticmethod
    def _with_instruction(messages, instruction, own_rounds):
        """``messages`` with ``instruction`` before the first of ``own_rounds``
        still in it, or at the end; a new list either way."""
        positions = [
            index for index in (
                _find_by_identity(messages, -1, round_) for round_ in (own_rounds or ())
            ) if index >= 0
        ]
        at = min(positions) if positions else len(messages)
        return [*messages[:at], instruction, *messages[at:]]

    def _seat_tool_rounds(self, messages):
        """Put a stand-in user turn before each tool round that follows
        neither a user turn nor a tool reply.

        A ``prompt_ephemeral`` turn saves its tool rounds but never its
        instruction, so in history the round follows an assistant message, or
        the tool reply of an earlier turn that ended on a round: the first
        round of every such turn gets one (``_proactive_round_generation``)
        unless its own instruction or a user turn is right before it; later
        rounds of the same turn answer its own tool replies.
        Gemini, natively and behind OpenAI-compatible gateways, rejects a
        function call turn that does not come right after a user turn or a
        function response; other providers read the stand-in as one more
        user line. Returns ``messages`` itself when no round needs one.
        """
        seated: list = []
        stand_in = None
        previous_turn = None
        for index, message in enumerate(messages):
            before = messages[index - 1] if index else None
            if (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and message.get("tool_calls")
            ):
                turn = self._proactive_round_generation(message)
                first_of_turn = turn is not None and turn != previous_turn
                previous_turn = turn
                if first_of_turn:
                    # Only its own (unsaved) instruction, or a real user turn,
                    # prompted it: a tool reply or a tool image placeholder
                    # before it belongs to an earlier turn.
                    prompted = isinstance(before, HumanMessage)
                else:
                    prompted = isinstance(before, HumanMessage) or (
                        isinstance(before, dict) and before.get("role") in ("user", "tool")
                    )
                if not prompted:
                    if stand_in is None:
                        stand_in = _loc(TOOL_ROUND_PROMPT_PLACEHOLDER, self._tool_image_locale())
                    seated.append({"role": "user", "content": stand_in})
            seated.append(message)
        return messages if stand_in is None else seated

    def _projected_dialog_messages(self, messages, *, context_ids=frozenset()):
        """Pair tool rounds and project screen comments (see
        ``_dialog_messages_for_provider``). ``context_ids`` are synthetic
        turn-context messages, which never count as the turn's user message.
        """
        paired = self._paired_tool_rounds(messages)
        turn_start = next(
            (index + 1 for index in range(len(paired) - 1, -1, -1)
             if getattr(paired[index], "type", None) == "human"
             and id(paired[index]) not in context_ids
             and not is_turn_context_message(paired[index])),
            len(paired),
        )
        hits: dict = {}
        if turn_start < len(paired):
            head = paired[:turn_start]
            projected_head = project_screen_history(head, hits=hits)
            tail = paired[turn_start:]
            projected_tail = strip_screen_labels(tail)
            if projected_tail is not tail:
                hits["label"] = hits.get("label", 0) + 1
            projected = (
                paired if projected_head is head and projected_tail is tail
                else list(projected_head) + list(projected_tail)
            )
        else:
            projected = project_screen_history(paired, hits=hits)
        if projected is not paired:
            # The history is re-projected on every provider call; log a
            # rewrite once, not on every later request that repeats it. The
            # signature is taken over the saved messages, whose identities
            # hold across calls (the pairing above builds fresh copies).
            kept = {id(message) for message in projected}
            signature = tuple(id(message) for message in messages if id(message) not in kept)
            log = (
                logger.debug
                if signature == getattr(self, "_screen_quarantine_signature", None)
                else logger.info
            )
            self._screen_quarantine_signature = signature
            log(
                "OmniOfflineClient: screen-chain request view rewrote "
                "%d message(s) with an in-message chain, %d in a cross-message run, "
                "%d more for labels alone",
                hits.get("message", 0),
                hits.get("run", 0),
                hits.get("label", 0),
            )
        return projected

    @staticmethod
    def _paired_tool_rounds(messages):
        """Keep only tool calls that have replies, and replies that have calls.

        A call is answered when a ``role=tool`` message with its id follows
        in the run of tool messages right after its assistant turn. An
        assistant turn left with no answered call keeps its text as a plain
        assistant message, or goes if it has none. Returns ``messages`` itself
        when nothing needs repair.
        """
        repaired = []
        changed = False
        index = 0
        while index < len(messages):
            message = messages[index]
            if isinstance(message, dict) and message.get("role") == "tool":
                # A reply no preceding assistant turn claims.
                changed = True
                index += 1
                continue
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            is_call_turn = (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and bool(calls)
            )
            if not is_call_turn:
                repaired.append(message)
                index += 1
                continue
            end = index + 1
            while (
                end < len(messages)
                and isinstance(messages[end], dict)
                and messages[end].get("role") == "tool"
            ):
                end += 1
            call_ids = [call.get("id") for call in calls if isinstance(call, dict)]
            replies = [
                reply for reply in messages[index + 1:end]
                if reply.get("tool_call_id") in call_ids
            ]
            answered = {reply.get("tool_call_id") for reply in replies}
            kept_calls = [call for call in calls if isinstance(call, dict) and call.get("id") in answered]
            if len(kept_calls) == len(calls) and len(replies) == end - index - 1:
                repaired.extend(messages[index:end])
            else:
                changed = True
                if kept_calls:
                    repaired.append({**message, "tool_calls": kept_calls})
                    repaired.extend(replies)
                else:
                    plain = {k: v for k, v in message.items() if k != "tool_calls"}
                    if str(plain.get("content") or "").strip():
                        repaired.append(plain)
            index = end
        return repaired if changed else messages

    def set_tools(self, tool_definitions: Optional[List[ToolDefinition]]) -> None:
        """Replace the active tool list. Takes effect on the next
        ``stream_text`` / ``prompt_ephemeral`` call. Pass ``None`` or
        ``[]`` to disable tools entirely.

        ⚠️ Also clears ``_genai_tools_unsupported``: once that flag is
        flipped to ``True`` because the old tool set triggered a
        ``GenerateContentConfig rejected`` / similar unsupported exception,
        the rest of the session would never try the native genai path
        again. Since the caller has swapped the tool list (typical case:
        hot-unloading a tool with a broken schema), the genai path deserves
        a fresh chance — otherwise it could only recover at the next
        ``connect()`` / ``switch_model()`` reset.
        """
        self._tool_definitions = list(tool_definitions or [])
        self._genai_tools_unsupported = False

    def set_tool_call_handler(self, handler: Optional[OnToolCallCallback]) -> None:
        """Plug in (or replace) the callback that executes tool calls."""
        self.on_tool_call = handler

    def set_tool_round_start_callback(self, handler) -> None:
        """Plug in (or clear with ``None``) the tool-round-start callback.

        Fires once per LLM iteration that enters a tool round, before any
        handler runs — including rounds where every collected call is
        dropped (nameless fragments) and no handler ever runs. See the
        ``on_tool_round_start`` note in ``_client``."""
        self.on_tool_round_start = handler

    async def _notify_tool_round_start(self) -> None:
        """Best-effort fire of ``on_tool_round_start``; a callback failure
        must never disturb the stream. getattr default guards ``__new__``
        test stubs that bypass ``__init__``."""
        cb = getattr(self, "on_tool_round_start", None)
        if cb is None:
            return
        try:
            await cb()
        except Exception as e:
            logger.debug("on_tool_round_start callback failed (ignored): %s", e)

    def has_tools(self) -> bool:
        return bool(self._tool_definitions) and self.on_tool_call is not None

    def _openai_tools_payload(self) -> Optional[List[dict]]:
        """OpenAI Chat Completions ``tools`` param — nested under
        ``function``. Returns ``None`` when the caller hasn't enabled
        tools, so ``_params`` skips both ``tools`` and ``tool_choice``."""
        if not self.has_tools():
            return None
        if getattr(self, "_openai_tools_unsupported", False):
            return None
        return [t.to_openai_chat() for t in self._tool_definitions]

    @staticmethod
    def _messages_carry_images(messages) -> bool:
        """Whether any message in ``messages`` has an image content part."""
        for msg in messages or []:
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
        return False

    @staticmethod
    def _classify_openai_tools_refusal(exc: BaseException) -> Optional[str]:
        """How an endpoint refused the ``tools`` parameter, if it did.

        ``"model"``: the model has no tool support at all (Ollama vision models:
        400 "... does not support tools"), so every later request would fail
        the same way. ``"request"``: tools were refused for something specific
        to this request (e.g. "tool use is not supported with images"); plain
        text turns may still use them. ``None``: not a tools refusal.
        """
        msg = str(exc or "").lower()
        model_wide = "does not support tools" in msg or "does not support function" in msg
        loose = ("tools" in msg and "not support" in msg) or (
            "tool use" in msg and ("unsupported" in msg or "not supported" in msg)
        )
        if not (model_wide or loose):
            return None
        # Check request conditions first: with images / in combination with … limits rejection to that
        # combination, even with does not support tools or this model. Text-only turns
        # still support tools, so never persist session-wide rejection.
        if any(qualifier in msg for qualifier in _TOOLS_REFUSAL_REQUEST_QUALIFIERS):
            return "request"
        # Explicit model-focused wording (... not supported by / for this model) is also
        # model-wide, avoiding rejection/resending on every subsequent turn.
        if model_wide or "this model" in msg:
            return "model"
        return "request"

    async def _astream_declining_tools(
        self,
        messages,
        overrides: dict,
        response_generation: Optional[int] = None,
    ):
        """``self.llm.astream`` that survives an endpoint rejecting ``tools``.

        If the request fails before the first chunk because the endpoint
        refused ``tools``, strip ``tools`` / ``tool_choice`` from ``overrides``
        in place (so later rounds of the caller's loop and its forced-finalize
        call go without them too) and re-issue the same request once. Only a
        model-wide refusal also marks the session, so later turns stop sending
        tools; a refusal tied to this request leaves later turns alone. Any
        other failure, or one after a chunk was already received, propagates
        unchanged.
        """
        generation_is_active = _generation_check(self, response_generation)

        if not generation_is_active():
            return
        received_any = False
        try:
            async for chunk in self.llm.astream(messages, **overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
                received_any = True
                # No cancellation check here: the caller publishes what this
                # request delivered on its first chunk, then checks.
                yield chunk
            return
        except Exception as exc:
            refusal = None
            if not received_any and "tools" in overrides:
                refusal = self._classify_openai_tools_refusal(exc)
            if refusal is None:
                raise
            logger.warning(
                "OpenAI-compat model %s declined tools (%s); retrying this "
                "request without tools%s",
                getattr(self, "model", None), exc,
                " and disabling them for the session" if refusal == "model" else "",
            )
        if refusal == "model":
            self._openai_tools_unsupported = True
        elif self._messages_carry_images(messages):
            # Rejected with images: images remain in history, causing repeated rejections while present.
            # Remember and omit tools on image-bearing requests; text-only history stays unaffected.
            self._openai_tools_unsupported_with_images = True
        overrides.pop("tools", None)
        overrides.pop("tool_choice", None)
        if not generation_is_active():
            return
        async for chunk in self.llm.astream(messages, **overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
            # Same rule as the first attempt: the caller publishes what this
            # request delivered on its first chunk, then checks.
            yield chunk

    @staticmethod
    def _settle_unfinished_tool_round(messages, assistant_turn, tool_results) -> int:
        """Keep the calls that already ran; drop the round only if none did.

        A handler that returned has already had its side effects (a sent
        message, a written file). Deleting its record would let the next
        turn ask for it again, so the assistant turn is trimmed to the calls
        that have results and stays paired with them. With no result at all
        the whole round goes. Owned messages are matched by identity and put
        back contiguously at the assistant turn's position, so a message
        another turn appended meanwhile is neither lost nor left between a
        ``tool_calls`` turn and its replies. An assistant turn that is no
        longer in ``messages`` was removed with its turn by whoever trimmed
        the history in place; put back at the end, the round would follow
        the newer turns, so it goes with its turn. Returns the calls kept.
        """
        owned_ids = {id(assistant_turn)} | {id(message) for message in tool_results}
        position = _find_by_identity(messages, -1, assistant_turn)
        rebuilt = [message for message in messages if id(message) not in owned_ids]
        kept = len(tool_results) if position >= 0 else 0
        if kept:
            # Calls run in order, so the results are a prefix of tool_calls.
            assistant_turn["tool_calls"] = assistant_turn["tool_calls"][:kept]
            rebuilt[position:position] = [assistant_turn, *tool_results]
        messages[:] = rebuilt
        return kept

    async def _execute_and_append_openai_tool_calls(
        self,
        messages,
        calls,
        assistant_text: str = "",
        assistant_reasoning: str = "",
        tool_image_slots=None,
        tool_bus_frames=None,
        generation_is_active=lambda: True,
        tool_rounds=None,
    ) -> int:
        """Run each tool call through ``on_tool_call`` and mutate
        ``messages`` in place: append one assistant turn announcing all
        tool calls, then one tool-role message per call carrying the
        result JSON. Both shapes follow the OpenAI Chat Completions spec
        so the next astream invocation sees a valid history.

        Returns the number of calls actually executed — 0 when every slot
        was a nameless fragment and nothing was appended. The caller uses
        that to decide whether the round-persisted sentinel may be emitted
        (its contract is "the pre-tool text IS in history now") and to
        grade the iteration-cap log.

        ``assistant_text`` is written into the assistant turn's ``content``.
        The OpenAI Chat Completions protocol allows a turn to carry both
        ``content`` and ``tool_calls``, and some OpenAI-compat providers
        "emit text first, then enter tool_calls". Like the Gemini path's
        streamed_text_buffer, this text must be written into the history
        too, otherwise the next turn's context loses the prefix and the
        model repeats itself / backtracks.

        ``assistant_reasoning`` is the thinking model's reasoning chain for
        this turn (``reasoning_content``). Endpoints like DeepSeek-R /
        Qwen / GLM thinking require the ``reasoning_content`` of the
        assistant message that initiated the tool_calls to be passed back
        verbatim in multi-turn tool calling, otherwise the next turn fails
        with 400 "The `reasoning_content` in the thinking mode must be
        passed back to the API.". Non-thinking endpoints always leave it
        empty, in which case the field is omitted to avoid polluting
        normal conversations.
        """
        # Defensive filtering: ChatOpenAI.collect_tool_calls already drops empty-name slots,
        # but directly constructed calls or replaced upstream aggregation need a second guard:
        # empty names in tool_calls history trigger next-turn server schema rejection,
        # breaking the whole session.
        calls = [c for c in calls if (getattr(c, "name", "") or "").strip()]
        if not calls or not generation_is_active():
            return 0
        tool_calls_dict = []
        for i, c in enumerate(calls):
            entry = {
                "id": c.id or f"call_{i}",
                "type": "function",
                "function": {
                    "name": c.name,
                    "arguments": c.arguments or "{}",
                },
            }
            # Provider-owned blob that came down with this call (Gemini's
            # OpenAI-compat ``thought_signature``). Round-tripped verbatim:
            # Gemini rejects the follow-up request when a function call in
            # history lost its signature. Only attached when the provider
            # actually sent one, so ordinary endpoints keep a clean history.
            extra_content = getattr(c, "extra_content", None)
            if extra_content:
                entry["extra_content"] = extra_content
            tool_calls_dict.append(entry)
        assistant_turn = {
            "role": "assistant",
            "content": assistant_text or "",
            "tool_calls": tool_calls_dict,
        }
        if assistant_reasoning:
            assistant_turn["reasoning_content"] = assistant_reasoning
        # Built before the round: a call that cannot even be constructed
        # stops the turn before any tool has had a side effect.
        tool_calls = [
            ToolCall(
                name=c.name,
                arguments=parse_arguments_json(c.arguments),
                call_id=c.id or f"call_{i}",
                raw_arguments=c.arguments or "",
            )
            for i, c in enumerate(calls)
        ]
        executed, _live = await self._run_tool_round(
            messages,
            assistant_turn,
            tool_calls,
            tool_image_slots=tool_image_slots,
            tool_bus_frames=tool_bus_frames,
            generation_is_active=generation_is_active,
            log_prefix="OmniOfflineClient",
            tool_rounds=tool_rounds,
        )
        return executed

    async def _run_tool_round(
        self,
        messages,
        assistant_turn,
        tool_calls,
        *,
        tool_image_slots,
        tool_bus_frames,
        generation_is_active,
        log_prefix: str,
        tool_rounds=None,
    ):
        """Execute one tool round for either provider path.

        Appends ``assistant_turn``, then runs each of ``tool_calls`` (built
        by the caller, each provider path with its own ``ToolCall``) and
        appends its ``tool`` reply. Cancellation is
        checked before each call, never between a handler and its record:
        once a handler returned, its side effects happened. An unfinished
        round (cancelled or raising) is settled by
        ``_settle_unfinished_tool_round``. Image turns are appended only for a
        round that finished while its turn was still live, after every reply,
        because OpenAI-compat providers reject assistant(tool_calls) -> tool
        -> user(image) -> tool.

        Returns ``(calls executed, finished while live)``. A round keeps
        those calls in history unless its assistant turn left the history
        meanwhile (``_settle_unfinished_tool_round``); then it is not live
        either, though its calls still count: its text went with its turn.

        ``tool_rounds``, when the caller passes one, gets ``assistant_turn``
        as it is appended: the caller's own rounds, found by identity.
        """
        messages.append(assistant_turn)
        if tool_rounds is not None:
            tool_rounds.append(assistant_turn)
        tool_results: list = []
        image_results: list = []
        round_complete = False
        try:
            for tool_call in tool_calls:
                if not generation_is_active():
                    break
                handler = self.on_tool_call
                if handler is None:
                    # No handler — surface a structured error back so the
                    # model can apologize / abort gracefully.
                    result = ToolResult(
                        call_id=tool_call.call_id, name=tool_call.name,
                        output={"error": "no on_tool_call handler bound"},
                        is_error=True, error_message="no on_tool_call handler bound",
                    )
                else:
                    try:
                        # No await between the generation check and snapshot.
                        # A slow handler must keep this owner after a new turn.
                        tool_call.reply_owner = getattr(self, "_active_reply_owner", None)
                        with _suspend_dialog_slop():
                            result = await handler(tool_call)
                    except Exception as e:
                        logger.exception("%s: on_tool_call '%s' raised", log_prefix, tool_call.name)
                        result = ToolResult(
                            call_id=tool_call.call_id, name=tool_call.name,
                            output={"error": f"{type(e).__name__}: {e}"},
                            is_error=True, error_message=str(e),
                        )
                tool_result_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.call_id,
                    # Store name so Gemini can use it directly: FunctionResponse.name
                    # must exactly match original function_call names. OpenAI compatibility needs no
                    # such field but tolerates it, associating through tool_call_id only.
                    "name": tool_call.name,
                    "content": result.output_as_json_string(),
                }
                messages.append(tool_result_message)
                tool_results.append(tool_result_message)
                if getattr(result, "images", None):
                    image_results.append((result, tool_result_message))
            else:
                round_complete = True
        finally:
            # A round that completed while its reply was live is settled too:
            # its results may have landed after a message another turn added
            # meanwhile, and the request view would otherwise drop the call
            # and its result for good. Already contiguous, it stays as is.
            kept = len(tool_results)
            if not round_complete or not generation_is_active() or tool_results:
                kept = self._settle_unfinished_tool_round(messages, assistant_turn, tool_results)
        # A round that left history with its turn (kept nothing) is not live:
        # its images would be orphaned at the end of history.
        live = round_complete and generation_is_active() and (kept or not tool_results)
        if live:
            # Right after the round's last reply, not at the end: a user
            # message another turn saved meanwhile must not come between the
            # round and its images. Never between two replies either, which
            # OpenAI-compat providers reject.
            after = tool_results[-1] if tool_results else None
            for result, tool_result_message in image_results:
                added = self._append_tool_result_images(
                    messages,
                    result,
                    slots=tool_image_slots,
                    tool_result_message=tool_result_message,
                    bus_frames=tool_bus_frames,
                    insert_after=after,
                )
                if added is not None:
                    after = added
        return len(tool_results), live

    # ------------------------------------------------------------------
    # Tool image channel
    # ------------------------------------------------------------------
    #
    # A tool result carries pixels in ``ToolResult.images`` when -- and only
    # when -- ``LLMSessionManager._route_tool_images`` got a session that can
    # look at them; a session that cannot arrives here with an empty list and
    # an ``_image_warnings`` entry already telling the model it did not see.
    #
    # The picture rides a synthetic user turn appended right after the tool
    # result, because the ``role: tool`` message body must stay a string.
    # That turn is ONE-SHOT: it exists for the follow-up model call inside
    # the tool loop and is swapped for a text placeholder on the way out.
    #
    # It has to be one-shot. ``messages`` here is usually
    # ``_conversation_history`` itself, which has no image eviction, and
    # ``llm_prompt_audit`` renders an image part as a short ``[image]``
    # placeholder when counting tokens — so a frame left behind would be
    # re-uploaded on every later request while looking free to the
    # truncation logic.

    async def prepare_for_tool_images(self) -> bool:
        """Point this session at a model that can read a picture.

        Returns whether the session can show the model a frame at all. Called
        by ``LLMSessionManager._route_tool_images`` while a tool result is
        being assembled, so it runs from inside a live tool loop.

        The move is the one ``stream_text`` makes for a dragged-in screenshot:
        a configured vision model wins the rest of the session, because the
        frames stay in history and there is no way back.

        It is refused when it would change which SDK the running loop speaks.
        ``_astream_with_tools`` picks genai or OpenAI-compat once, at entry,
        and then re-invokes the model for the tool results without asking
        again -- flipping the answer underneath it would post genai contents
        to an OpenAI-compat endpoint, or the reverse. Refusing degrades to
        "she could not see it", which the caller already reports to the model.
        """
        vision_model = getattr(self, "vision_model", "") or ""
        if not vision_model:
            return False
        # Identical model IDs count as already vision-selected only with matching URL/key/protocol
        # routing too. Different vision routes still require switching below.
        if vision_model == self.model and _same_route(
            getattr(self, "vision_base_url", None),
            getattr(self, "vision_api_key", None),
            getattr(self, "vision_provider_type", None),
            getattr(self, "base_url", None),
            getattr(self, "api_key", None),
            getattr(self, "provider_type", None),
        ):
            return True
        on_genai = bool(
            getattr(self, "_use_genai_sdk", False)
            and not getattr(self, "_genai_tools_unsupported", False)
        )
        if _should_use_genai_sdk(vision_model, self.vision_base_url) != on_genai:
            logger.warning(
                "Tool image: not switching to vision model %s mid tool loop, "
                "it sits on the other transport (current=%s)",
                vision_model,
                "genai" if on_genai else "openai-compat",
            )
            return False
        try:
            await self.switch_model(vision_model, use_vision_config=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The conversation model is mid-turn waiting for this tool result.
            # A vision endpoint that will not come up has to become something
            # she can say, not an exception that turns a tool call that
            # actually succeeded into an error result.
            logger.warning(
                "Tool image: switching to vision model %s failed: %s: %s",
                vision_model, type(e).__name__, e,
            )
            return False
        return True

    def _tool_image_locale(self) -> str:
        """Resolve the locale for one injected tool-image turn.

        The session locale is only reachable from an instance: it arrives as
        the ``user_language_provider`` callable the manager hands to
        ``OmniOfflineClient.__init__``, which is why the default caption is no
        longer a class attribute. Same guarded call as
        ``utils.slop_filter.resolve_dialog_slop_lang`` -- a provider that
        raises must not take down a tool loop that otherwise succeeded, and
        ``normalize_tool_image_locale`` resolves ``None`` to the app's global
        language.
        """
        provider = getattr(self, "_user_language_provider", None)
        user_language = None
        if callable(provider):
            try:
                user_language = provider()
            except Exception as e:
                logger.debug(
                    "Tool image: user language provider failed (%s: %s); "
                    "falling back to the global language",
                    type(e).__name__, e,
                )
        return normalize_tool_image_locale(user_language)

    def _append_tool_result_images(
        self,
        messages,
        result,
        *,
        slots=None,
        bus_frames=None,
        tool_result_message=None,
        insert_after=None,
    ):
        """Append one multimodal user turn carrying every image in ``result``.

        No-op when the tool returned none, which is the overwhelmingly common
        case — nothing is allocated and no slot is recorded. With
        ``insert_after`` (a message in ``messages``, found by identity) the
        turn goes right after it instead of at the end. Returns the turn, or
        None when nothing was added.
        """
        images = getattr(result, "images", None)
        if not images:
            return None

        # Once per turn: every string below has to come out in one language,
        # and the provider is a live callable that could answer differently
        # between the caption and the placeholder built from the same result.
        lang = self._tool_image_locale()

        if slots is None:
            slots = getattr(self, "_pending_tool_image_slots", None)
            if slots is None:
                slots = []
                self._pending_tool_image_slots = slots

        used_count = 0
        used_b64_bytes = 0
        for _messages, _index, image_message, _placeholder in slots:
            for part in image_message.get("content", []):
                if part.get("type") != "image_url":
                    continue
                url = part.get("image_url", {}).get("url", "")
                if not isinstance(url, str):
                    continue
                used_count += 1
                used_b64_bytes += len(url.rsplit(",", 1)[-1])

        content = []
        omitted_count = 0
        for img in images:
            image_b64_bytes = len(img.data_b64)
            if (
                used_count >= _TOOL_IMAGE_TURN_MAX_COUNT
                or used_b64_bytes + image_b64_bytes
                > _TOOL_IMAGE_TURN_MAX_B64_BYTES
            ):
                logger.warning(
                    "Dropping tool image beyond turn budget: tool=%s, "
                    "used_count=%d, used_b64_bytes=%d",
                    result.name,
                    used_count,
                    used_b64_bytes,
                )
                omitted_count += 1
                continue
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime};base64,{img.data_b64}"},
            })
            # Staged, not published: this only means the pixels are in the
            # outgoing list. The tool loop publishes them once the provider
            # answers the request that carries them. Staged HERE rather than
            # over ``result.images`` so the turn budget's drops never reach
            # the bus -- an omitted image was never sent.
            if bus_frames is not None:
                bus_frames.append(
                    (img.data_b64, img.mime, str(result.name or "unknown"))
                )
            # Keep each instruction adjacent to the image it describes.
            # Always caption: several providers reject bare image parts.
            instruction = (
                img.vision_prompt.strip()
                or _loc(TOOL_IMAGE_DEFAULT_CAPTION, lang)
            )
            tool_name = str(result.name or "unknown")
            call_id = str(result.call_id or "unknown")
            caption = _loc(TOOL_IMAGE_CAPTION, lang).format(
                tool_name=tool_name,
                call_id=call_id,
                instruction=instruction,
            )
            content.append({"type": "text", "text": caption})
            used_count += 1
            used_b64_bytes += image_b64_bytes

        if omitted_count:
            result.add_image_warnings(
                _loc(TOOL_IMAGE_OMITTED_WARNING, lang).format(
                    count=omitted_count
                )
            )
            # Tool results are serialized before image turns so that all
            # role=tool messages stay adjacent. Refresh the matching message
            # after annotating the result to make the omission model-visible.
            if isinstance(tool_result_message, dict):
                tool_result_message["content"] = result.output_as_json_string()

        if not content:
            return None

        message = {"role": "user", "content": content}
        index = len(messages)
        if insert_after is not None:
            after = _find_by_identity(messages, -1, insert_after)
            if after >= 0:
                index = after + 1
        messages.insert(index, message)

        # Remember the list too: a ``prompt_ephemeral`` reply that is not kept
        # runs the tool loop over a copy rather than ``_conversation_history``,
        # so an index alone would point into the wrong history.
        output = result.output if isinstance(result.output, dict) else {}
        shot_id = output.get("shot_id")
        recall_hint = output.get("recall_hint")
        recall_suffix = ""
        if isinstance(shot_id, str) and shot_id.strip():
            recall_suffix = _loc(TOOL_IMAGE_RECALL_HANDLE, lang).format(
                shot_id=shot_id.strip()
            )
            if isinstance(recall_hint, str) and recall_hint.strip():
                recall_suffix += _loc(TOOL_IMAGE_RECALL_HINT, lang).format(
                    recall_hint=recall_hint.strip()
                )
        slots.append((
            messages,
            index,
            message,
            _loc(TOOL_IMAGE_HISTORY_PLACEHOLDER, lang).format(
                tool_name=result.name,
                recall_suffix=recall_suffix,
            ),
        ))
        return message

    def _release_tool_image_slots(self, slots=None) -> None:
        """Swap every injected image turn for its text placeholder.

        Called from the exit of both tool loops (``finally``, so an abandoned
        generator still cleans up). Identity is re-checked before writing:
        another path may have rebuilt or truncated the history underneath us,
        and a blind index write would corrupt an unrelated message.
        """
        if slots is None:
            slots = getattr(self, "_pending_tool_image_slots", None)
        if not slots:
            return
        for messages, index, message, placeholder in slots:
            try:
                # A settled concurrent tool round or a cancelled reply commit
                # may have shifted the turn; identity finds it again.
                index = _find_by_identity(messages, index, message)
                if index >= 0:
                    messages[index] = {"role": "user", "content": placeholder}
            except Exception as e:
                logger.warning("Releasing a tool image slot failed (ignored): %s", e)
        slots.clear()

    async def _notify_reasoning_active(self) -> None:
        """Tell the host that the model is emitting reasoning / thinking chunks, so
        the chat can show a thinking-dots bubble even on a non-Focus turn whose
        provider reasons internally. The reasoning TEXT is still filtered out at the
        call site — only this boolean pulse escapes. Pulses once per stream: it
        records the current stream's seq as the pulse owner and no-ops while that
        same seq still owns the pulse, so the three filter points can call it
        blindly. Best-effort: a callback failure must never disturb the stream.

        getattr defaults guard ``__new__`` test stubs that bypass ``__init__``."""
        cur = getattr(self, "_reasoning_stream_seq", 0)
        if getattr(self, "_reasoning_active_pulse_seq", None) == cur:
            return  # already pulsed for THIS stream
        self._reasoning_active_pulse_seq = cur
        cb = getattr(self, "on_thinking_active", None)
        if cb is None:
            return
        try:
            await cb(True)
        except Exception as e:
            logger.debug("on_thinking_active(True) callback failed (ignored): %s", e)

    def _begin_reasoning_stream(self) -> int:
        """Open a new reasoning-pulse scope for one stream and return its ownership
        token. Bumps the seq so this stream's first reasoning chunk re-pulses and
        so an older interleaving stream's clear can't fire for this scope. Crucially
        does NOT touch ``_reasoning_active_pulse_seq`` — that single source of truth
        stays owned by whoever last lit the bubble, so a preempted older stream can
        still clear its own pulse (Codex P2). Called at the top of both stream entry
        points (stream_text and prompt_ephemeral)."""
        self._reasoning_stream_seq = getattr(self, "_reasoning_stream_seq", 0) + 1
        return self._reasoning_stream_seq

    async def _notify_reasoning_done(self, owner_seq: Optional[int] = None) -> None:
        """Symmetric clear for ``_notify_reasoning_active``: push the bubble back to
        False when THIS stream still owns the active pulse. Required for callers
        without an external unconditional clear — ``prompt_ephemeral``'s proactive /
        greeting / avatar turns clear the bubble only when a visible token reaches
        ``send_lanlan_response``; a turn that reasons but commits no text (safety /
        empty / tool-only) would otherwise leave the bubble stuck on (Codex P2).
        ``stream_text``'s Focus path is cleared by core's own unconditional finally
        instead (it must also clear the Focus pre-pulse, which fires with no
        reasoning chunk), so this is wired into ``prompt_ephemeral``'s finally only.

        ``owner_seq`` is the token from this stream's ``_begin_reasoning_stream``.
        The clear fires only when ``_reasoning_active_pulse_seq`` still equals it:
          - a NEWER stream that already re-pulsed took ownership (seq differs) → we
            must NOT clear the bubble it is reasoning under;
          - but if the newer stream merely STARTED (bumped seq) without pulsing yet,
            ownership is still ours, so we correctly clear our own pulse rather than
            leaking it (the bug a shared per-stream boolean would have caused).
        Idempotent; getattr defaults guard ``__new__`` test stubs."""
        active = getattr(self, "_reasoning_active_pulse_seq", None)
        if active is None:
            return
        if owner_seq is not None and active != owner_seq:
            return
        self._reasoning_active_pulse_seq = None
        cb = getattr(self, "on_thinking_active", None)
        if cb is None:
            return
        try:
            await cb(False)
        except Exception as e:
            logger.debug("on_thinking_active(False) clear failed (ignored): %s", e)

    async def _astream_with_tools(self, messages, **overrides):
        """Polymorphic streaming entry point. Yields ``LLMStreamChunk``
        objects (text + finish_reason); tool calls are intercepted and
        executed transparently — caller never sees ``tool_call_deltas``.

        Routing:
        - Native Gemini (``_use_genai_sdk``): dispatches to
          ``_astream_genai_with_tools`` and on tools-related failures sets
          ``_genai_tools_unsupported`` so subsequent calls degrade to the
          OpenAI-compat path, which carries ``tools`` too.
        - Otherwise: ``_astream_openai_with_tools``.
        """
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        tool_image_slots = overrides.pop("_tool_image_slots", None)
        tool_bus_frames = overrides.pop("_tool_bus_frames", None)
        tool_frames_turn_id = overrides.pop("_tool_frames_turn_id", None)
        tool_rounds = overrides.pop("_tool_rounds", None)
        response_generation = overrides.pop("_response_generation", None)
        instruction = overrides.pop("_instruction", None)
        if self._use_genai_sdk and not self._genai_tools_unsupported:
            # Track whether Gemini yielded text upstream this turn. After text appears,
            # falling back to OpenAI compatibility would show users concatenated partial Gemini
            # output + regenerated OpenAI output in one turn.
            # Raise upstream so stream_text retries/discards through standard bubble-clearing /
            # response_discarded notification.
            genai_emitted_text = False
            try:
                async for chunk in self._astream_genai_with_tools(
                    messages,
                    _tool_leak_filter=tool_leak_filter,
                    _tool_leak_provider=tool_leak_provider,
                    _tool_image_slots=tool_image_slots,
                    _tool_bus_frames=tool_bus_frames,
                    _tool_frames_turn_id=tool_frames_turn_id,
                    _tool_rounds=tool_rounds,
                    _response_generation=response_generation,
                    _instruction=instruction,
                    **overrides,
                ):
                    if getattr(chunk, "content", None):
                        genai_emitted_text = True
                    yield chunk
                return
            except _GenaiToolsUnsupported as e:
                logger.warning(
                    "genai SDK declined tools (%s) — falling back to OpenAI-compat (tools disabled)",
                    e,
                )
                self._genai_tools_unsupported = True
                if genai_emitted_text:
                    # Text already emitted: retain permanent-disable flags but never silently concatenate;
                    # upstream retries with attempt+1, next time entering
                    # OpenAI compatibility directly because _genai_tools_unsupported=True.
                    raise
                if tool_leak_filter is not None:
                    tool_leak_filter.reset()
            except Exception as e:
                # Don't break user requests on transient genai SDK errors —
                # log loudly and fall through. ``_genai_tools_unsupported``
                # stays False so the next turn retries genai (transient
                # 5xx / 429 shouldn't permanently downgrade).
                logger.error("genai SDK path errored, falling back this turn: %s", e)
                if genai_emitted_text:
                    # Likewise emitted text forbids silent fallback; raise for retries to
                    # clear bubbles and restart at attempt+1. Next attempt still tries
                    # genai because transient failures do not flip _genai_tools_unsupported.
                    raise
                if tool_leak_filter is not None:
                    tool_leak_filter.reset()
        async for chunk in self._astream_openai_with_tools(
            messages,
            _tool_leak_filter=tool_leak_filter,
            _tool_leak_provider=tool_leak_provider,
            _tool_image_slots=tool_image_slots,
            _tool_bus_frames=tool_bus_frames,
            _tool_frames_turn_id=tool_frames_turn_id,
            _tool_rounds=tool_rounds,
            _response_generation=response_generation,
            _instruction=instruction,
            **overrides,
        ):
            yield chunk

    async def _astream_visible_with_tools(self, messages, **overrides):
        # Callers may own slots, essential rather than optional tidiness: outer retry ladders (stream_text /
        # prompt_ephemeral) reuse _conversation_history across attempts, while finally below
        # replaces image turns with text placeholders. After retryable failure, assistant
        # tool_calls and results remain but pixels disappear, making successful retries
        # assume the model already saw images.
        #
        # Callers supplying slots release them in **their own** finally; others retain
        # existing local create/clear ownership.
        owned_tool_image_slots = overrides.pop("_tool_image_slots", None)
        tool_image_slots = (
            [] if owned_tool_image_slots is None else owned_tool_image_slots
        )
        # Same lifetime/path/**ownership** as slots: retaining slots across attempts without this buffer means
        # successful retries deliver pixels to providers but no bus copies;
        # attempt-1 frames disappear with that call, while attempt-2 tool loops usually do not
        # repeat because tool_calls/results already exist and models answer directly.
        #
        # Never attach to self: stream_text and prompt_ephemeral tool loops may coexist;
        # session-shared lists let B requests falsely confirm delivery of
        # A images.
        owned_tool_bus_frames = overrides.pop("_tool_bus_frames", None)
        tool_bus_frames = (
            [] if owned_tool_bus_frames is None else owned_tool_bus_frames
        )
        tool_frames_turn_id = overrides.pop("_tool_frames_turn_id", None)
        tool_names = {
            tool.name for tool in getattr(self, "_tool_definitions", [])
            if getattr(tool, "name", None)
        }
        leak_filter = ToolLeakFilter(tool_names=tool_names)
        provider = getattr(self, "base_url", None) or getattr(self, "model", None)

        def _finalize_filter_chunk():
            visible, event = leak_filter.finalize()
            if event:
                log_tool_leak_filtered(event, provider=provider)
            if not visible:
                return None
            chunk = LLMStreamChunk(content=visible)
            setattr(chunk, "_tool_leak_filtered", True)
            return chunk

        try:
            async for chunk in self._astream_with_tools(
                messages,
                _tool_leak_filter=leak_filter,
                _tool_leak_provider=provider,
                _tool_image_slots=tool_image_slots,
                _tool_bus_frames=tool_bus_frames,
                _tool_frames_turn_id=tool_frames_turn_id,
                **overrides,
            ):
                if getattr(chunk, "_tool_leak_filtered", False):
                    yield chunk
                    continue
                content = getattr(chunk, "content", None)
                if content:
                    chunk.content = self._filter_tool_leak_content(content, leak_filter, provider=provider)
                    setattr(chunk, "_tool_leak_filtered", True)
                yield chunk
        except Exception:
            chunk = _finalize_filter_chunk()
            if chunk is not None:
                yield chunk
            raise
        finally:
            # Every model call that needed the pixels has happened by now: this
            # is the join point of the genai and OpenAI-compat tool loops, and
            # of both callers (``stream_text`` and ``prompt_ephemeral``). In a
            # ``finally`` so an abandoned generator (GeneratorExit) still drops
            # the base64 out of history.
            #
            # Skipped when the caller owns the list: for them "every model call
            # that needed the pixels" is not true yet -- their next attempt is
            # one, and it will read this same history. They release in their own
            # finally, which is equally GeneratorExit-proof and one scope wider.
            if owned_tool_image_slots is None:
                self._release_tool_image_slots(tool_image_slots)

        chunk = _finalize_filter_chunk()
        if chunk is not None:
            yield chunk

    def _filter_tool_leak_content(
        self,
        content: str,
        leak_filter: ToolLeakFilter,
        *,
        provider: str | None = None,
    ) -> str:
        visible, event = leak_filter.feed(content)
        if event:
            log_tool_leak_filtered(event, provider=provider)
        return visible

    async def _astream_openai_with_tools(self, messages, **overrides):
        """OpenAI Chat Completions tool loop. Streams text chunks; on
        ``finish_reason == "tool_calls"`` runs the tools, appends the
        results to ``messages``, and re-invokes — up to
        ``self.max_tool_iterations`` total LLM calls."""
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        tool_image_slots = overrides.pop("_tool_image_slots", None)
        tool_bus_frames = overrides.pop("_tool_bus_frames", None)
        tool_frames_turn_id = overrides.pop("_tool_frames_turn_id", None)
        tool_rounds, instruction, request_view = self._turn_request_view(overrides)
        response_generation = overrides.pop("_response_generation", None)

        generation_is_active = _generation_check(self, response_generation)

        if not generation_is_active():
            return
        tools_payload = self._openai_tools_payload()
        if (
            tools_payload
            and getattr(self, "_openai_tools_unsupported_with_images", False)
            # A proactive turn's images ride its instruction, which history
            # does not hold.
            and self._messages_carry_images(
                [*messages, *([instruction] if instruction is not None else [])]
            )
        ):
            tools_payload = None
        if tools_payload:
            overrides.setdefault("tools", tools_payload)
        else:
            # Belt-and-suspenders: never leak tool_choice without tools.
            overrides.pop("tool_choice", None)
            overrides.pop("tools", None)

        # Accumulate actually executed tool-call counts across iterations: zero differs from nonzero in cap logs,
        # distinguishing entirely unnamed fragments from ordinary budget exhaustion.
        executed_tool_calls = 0
        # Whether zero-execution with already-streamed text exited early, mirroring genai:
        # cap logs must not falsely claim iterations exhausted.
        zero_exec_break = False
        for tool_iter in range(self.max_tool_iterations):
            if not generation_is_active():
                return
            deltas_per_chunk: list = []
            finish_reason: Optional[str] = None
            # Accumulate text yielded upstream this round for assistant history alongside
            # finish_reason=tool_calls. OpenAI Chat Completions permits one
            # turn with both content and tool_calls, and compatible providers actually emit
            # text before tools. Fully symmetric with Gemini.
            streamed_text_buffer = ""
            # This thinking model's reasoning chain must accompany assistant tool_calls
            # history on finish_reason=tool_calls, or some providers return next-turn
            # 400 (reasoning_content must be passed back). Always empty on ordinary endpoints.
            streamed_reasoning_buffer = ""
            # Tool images injected last round are confirmed delivered only this round.
            tool_frames_published = False
            async for chunk in self._astream_declining_tools(
                # Built here rather than inside the helper so both the first
                # attempt and the retry-after-tools-refusal get the same view
                # (screen-projected, tool rounds repaired).
                request_view(messages),
                overrides,
                response_generation=response_generation,
            ):
                if not tool_frames_published:
                    # Any chunk counts, not only content-bearing chunks: astream is lazy,
                    # sending only on first __anext__. Receiving a chunk proves
                    # providers accepted requests carrying previous-round tool images.
                    tool_frames_published = True
                    self._publish_pending_tool_frames(
                        tool_bus_frames, turn_id=tool_frames_turn_id
                    )
                if not generation_is_active():
                    # The provider answered the request, so what it carried
                    # was delivered even though the turn is cancelled: the
                    # frames are published above, and callers get one empty
                    # chunk so they publish their own pending bus copies.
                    yield _answered_chunk()
                    return
                if getattr(chunk, "content", None):
                    if tool_leak_filter is not None:
                        chunk.content = self._filter_tool_leak_content(
                            chunk.content, tool_leak_filter, provider=tool_leak_provider
                        )
                        setattr(chunk, "_tool_leak_filtered", True)
                    streamed_text_buffer += chunk.content
                if getattr(chunk, "reasoning_content", None):
                    streamed_reasoning_buffer += chunk.reasoning_content
                    # Pulse the thinking bubble on ANY chunk carrying reasoning,
                    # BEFORE the pure-reasoning skip below — a thinking provider
                    # can pack reasoning_content onto the SAME delta as a
                    # tool_call_delta / finish_reason (the OpenAI adapter keeps
                    # them in one LLMStreamChunk), and a reasoning tool-call turn
                    # has no visible token to show feedback otherwise (Codex P2).
                    await self._notify_reasoning_active()
                    if not generation_is_active():
                        # Reasoning chunks are never yielded, so callers may
                        # not have seen this answered request yet: hand them
                        # the empty chunk they publish on (see above).
                        yield _answered_chunk()
                        return
                if chunk.tool_call_deltas:
                    deltas_per_chunk.append(chunk.tool_call_deltas)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                # Empty-completion diagnostics: record latest finish_reason and prompt_tokens
                # for upper stream_text / prompt_ephemeral fallback warnings.
                # Only terminal usage chunks contain prompt_tokens, not earlier text chunks.
                if chunk.usage_metadata:
                    pt = chunk.usage_metadata.get("prompt_tokens")
                    if pt:
                        self._last_prompt_tokens = pt
                # Reasoning-only chunks, emitted first by thinking models without content,
                # tool deltas / finish / usage, accumulate above without downstream forwarding:
                # stream_text records TTFT on the first yielded chunk, so forwarding
                # reasoning-only mislabels first reasoning tokens as first text, skewing latency telemetry downward.
                if (
                    getattr(chunk, "reasoning_content", None)
                    and not getattr(chunk, "content", None)
                    and not chunk.tool_call_deltas
                    and not chunk.finish_reason
                    and not chunk.usage_metadata
                ):
                    # Pure reasoning-only chunk: already pulsed above; drop it so
                    # the first-token TTFT telemetry is not fooled by reasoning tokens.
                    continue
                # Always yield text chunks: even tool-only turns may emit usage chunks and empty
                # content before finish_reason=tool_calls.
                yield chunk
            if not generation_is_active():
                return
            # Record this attempt's final finish_reason for upstream empty-completion
            # fallback warnings: safety / length / content_filter / stop can all
            # accompany empty content, crucial evidence for Gemini-via-OpenAI-compat silent
            # completions.
            self._last_finish_reason = finish_reason
            if "tools" not in overrides:
                # Endpoint rejected tools and request was resent without them; never enter tool handling again.
                tools_payload = None
            if (
                not streamed_text_buffer
                and not deltas_per_chunk
                and finish_reason != "tool_calls"
            ):
                # Dedicated INFO line supplies empty-completion evidence, including tool_iter/model;
                # combined with warnings, reconstruct which turn/attempt safety-blocked or
                # length-truncated. Defensive getattr supports __new__ test stubs bypassing
                # __init__, supplying model / _last_prompt_tokens defaults.
                logger.info(
                    "OmniOfflineClient(openai): empty completion finish_reason=%s "
                    "tool_iter=%d model=%s prompt_tokens=%s",
                    finish_reason, tool_iter,
                    getattr(self, "model", None),
                    getattr(self, "_last_prompt_tokens", None),
                )
            if (
                finish_reason == "tool_calls"
                and deltas_per_chunk
                and tools_payload
                and self.on_tool_call is not None
            ):
                if tool_leak_filter is not None:
                    tail, event = tool_leak_filter.finalize()
                    if event:
                        log_tool_leak_filtered(event, provider=tool_leak_provider)
                    if tail:
                        streamed_text_buffer += tail
                        tail_chunk = LLMStreamChunk(content=tail)
                        setattr(tail_chunk, "_tool_leak_filtered", True)
                        yield tail_chunk
                    tool_leak_filter.reset()
                # Confirmed tool turn: signal buffered callers such as QQ plugins to discard
                # pre-tool text before execution, independent of handler invocation.
                # Unnamed fragments may filter calls to empty and never invoke handlers,
                # so handler-entry-only cleanup misses that path entirely.
                await self._notify_tool_round_start()
                # ChatOpenAI is the right import even though we're outside
                # ChatOpenAI — `collect_tool_calls` is a staticmethod.
                from utils.llm_client import ChatOpenAI as _ChatOpenAI
                from utils.llm_client import LLMStreamChunk as _LLMStreamChunk
                calls = _ChatOpenAI.collect_tool_calls(deltas_per_chunk)
                executed_this_round = await self._execute_and_append_openai_tool_calls(
                    messages, calls,
                    # Strip any leaked <think> CoT before it lands in history:
                    # the streaming guard (ThinkingStreamStripper) only protects
                    # TTS/UI; this assembled pre-tool text is persisted raw to the
                    # assistant tool-call turn, so a leak-prone Focus turn would
                    # otherwise carry CoT into the next turn's context. No-op on
                    # clean replies (no think tag present).
                    assistant_text=strip_thinking_segments(streamed_text_buffer),
                    assistant_reasoning=streamed_reasoning_buffer,
                    tool_image_slots=tool_image_slots,
                    tool_bus_frames=tool_bus_frames,
                    generation_is_active=generation_is_active,
                    tool_rounds=tool_rounds,
                )
                if not generation_is_active():
                    # Calls that ran before the cancellation stay in history
                    # with their results; tell the caller the pre-tool text
                    # is persisted so it is not committed a second time.
                    if executed_this_round:
                        yield _LLMStreamChunk(content="", tool_round_persisted=True)
                    return
                executed_tool_calls += executed_this_round
                if executed_this_round:
                    # Notify upstream stream_text that pre-tool text + tool_calls
                    # persisted in assistant history, allowing final-segment
                    # buffer clearing so final AIMessage append does not duplicate
                    # pre-tool text.
                    #
                    # Zero-execution turns without history writes emit no sentinel:
                    # it promises pre-tool text persisted, false here;
                    # emitting would leave text absent from tool_calls and skipped by final
                    # AIMessage, losing it from history entirely.
                    yield _LLMStreamChunk(content="", tool_round_persisted=True)
                elif (
                    streamed_text_buffer
                    and getattr(self, "on_tool_round_start", None) is None
                ):
                    # Zero execution leaves messages unchanged, so another iteration adds no information,
                    # only replaying identical pre-tool text to users. If text already streamed,
                    # leave for forced finalization, retaining forced-finalize and
                    # cap logs while removing meaningless retries. With no streamed text,
                    # zero-execution turns may retry, imposing no visible
                    # cost while provider fluctuations may recover next iteration.
                    #
                    # Callers with round-start callbacks buffer output and discard
                    # pre-tool text, so this segment never reached users and cannot
                    # replay. Still retry rather than lose recoverable tool calls,
                    # matching genai.
                    zero_exec_break = True
                    break
                continue
            return
        if not generation_is_active():
            return
        if executed_tool_calls == 0:
            # Entered tools but never executed (provider fragments always unnamed,
            # all dropped by collect_tool_calls): particularly diagnostic,
            # never disguise as ordinary cap logs.
            logger.warning(
                "OmniOfflineClient: %s with 0 executed tool calls (provider "
                "streamed nameless tool_call fragments); forcing final answer "
                "without tools",
                "zero-execution round after streaming text" if zero_exec_break
                else f"tool iteration cap {self.max_tool_iterations} reached",
            )
        elif zero_exec_break:
            # Earlier rounds executed tools, final zero-execution round already streamed text: loop did not
            # exhaust, so avoid runaway cap reporting, mirroring genai.
            logger.warning(
                "OmniOfflineClient: zero-execution round after streaming text "
                "(provider streamed nameless tool_call fragments) with %d "
                "executed tool calls so far; forcing final answer without tools",
                executed_tool_calls,
            )
        elif self.max_tool_iterations == 1:
            # cap=1 is an intentional caller budget (QQ: one recall per turn); one
            # successful tool round necessarily exhausts iterations without runaway. INFO avoids
            # ordinary recalls flooding warnings and drowning real cap signals.
            logger.info(
                "OmniOfflineClient: single tool round budget spent; "
                "forcing final answer without tools",
            )
        else:
            logger.warning(
                "OmniOfflineClient: tool iteration cap %d reached; forcing final answer without tools",
                self.max_tool_iterations,
            )
        # Forced finalization: after tool iteration caps, remove tools and call once more for final
        # text based on accumulated results. Otherwise weak models loop tool_calls
        # until capped and go silent, leaving upstream no-text errors and users
        # receiving no response. Without tools, models must output text.
        # Loop-exit cancellation checks already guard this point; only synchronous logs intervene.
        final_overrides = {
            k: v for k, v in overrides.items() if k not in ("tools", "tool_choice")
        }
        final_finish_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        # This final capped request still carries unreleased tool images, replaced with placeholders only in outer
        # finally, so it is a real delivery/copy point too. Omitting it produces model-saw/plugin-missed
        # images exactly when tool rounds hit their limits.
        tool_frames_published = False
        async for chunk in self.llm.astream(request_view(messages), **final_overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
            if not tool_frames_published:
                tool_frames_published = True
                self._publish_pending_tool_frames(
                    tool_bus_frames, turn_id=tool_frames_turn_id
                )
            if not generation_is_active():
                # Answered, then cancelled: publish (above), then let callers
                # publish their pending bus copies on one empty chunk.
                yield _answered_chunk()
                return
            if chunk.finish_reason:
                final_finish_reason = chunk.finish_reason
            if chunk.usage_metadata:
                pt = chunk.usage_metadata.get("prompt_tokens")
                if pt:
                    final_prompt_tokens = pt
            # Pulse on ANY reasoning chunk (incl. reasoning bundled with a tool
            # delta / finish_reason on one delta), before the pure-reasoning skip
            # below — same fix as the main loop (Codex P2).
            if getattr(chunk, "reasoning_content", None):
                await self._notify_reasoning_active()
                if not generation_is_active():
                    # Reasoning is never yielded: see the tool-loop stream.
                    yield _answered_chunk()
                    return
            # Like normal tool loops, never forward thinking-model reasoning-only
            # chunks with reasoning_content but no content / tool deltas /
            # finish / usage. stream_text records first-yield TTFT,
            # and reasoning-only forwarding pollutes capped-round latency telemetry.
            if (
                getattr(chunk, "reasoning_content", None)
                and not getattr(chunk, "content", None)
                and not chunk.tool_call_deltas
                and not chunk.finish_reason
                and not chunk.usage_metadata
            ):
                continue
            if getattr(chunk, "content", None) and tool_leak_filter is not None:
                chunk.content = self._filter_tool_leak_content(
                    chunk.content, tool_leak_filter, provider=tool_leak_provider
                )
                setattr(chunk, "_tool_leak_filtered", True)
            yield chunk
        # Keep prompt_tokens local and always write back after stream completion, matching genai: absent
        # usage on this forced finalization writes None, never stale previous-iteration
        # values that confuse upstream empty-completion diagnostics.
        self._last_finish_reason = final_finish_reason
        self._last_prompt_tokens = final_prompt_tokens
