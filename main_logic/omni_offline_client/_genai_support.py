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

from ._shared import (
    _answered_chunk,
    _generation_check,
    AIMessage,
    Any,
    HumanMessage,
    LLMStreamChunk,
    Optional,
    SystemMessage,
    ToolCall,
    ToolResult,
    _GENAI_NATIVE_BASE_URL_HINTS,
    _GENAI_NATIVE_MODEL_HINTS,
    json,
    log_tool_leak_filtered,
    logger,
    strip_thinking_segments,
)
from ._lifecycle import _slop_reduced_for_genai, _suspend_dialog_slop

_genai = None

_genai_types = None

_GENAI_AVAILABLE: bool | None = None  # None = import not yet attempted

def _ensure_genai() -> bool:
    """Import google-genai on first call and cache the result (success or failure).

    Returns whether the SDK is available. Under a concurrent race the worst case is one
    duplicate import; Python's module cache makes it idempotent with no side effects.
    """
    global _genai, _genai_types, _GENAI_AVAILABLE
    # Explicit unavailability outranks even injected globals, serving as tests' forced-fallback switch.
    if _GENAI_AVAILABLE is False:
        return False
    # An existing object (real import or test-injected mock) is trusted directly without reimporting.
    if _genai is not None and _genai_types is not None:
        _GENAI_AVAILABLE = True
        return True
    try:
        from google import genai as genai_mod
        from google.genai import types as genai_types_mod
        # Fill only missing components, preserving injected _genai mocks.
        if _genai is None:
            _genai = genai_mod
        if _genai_types is None:
            _genai_types = genai_types_mod
        _GENAI_AVAILABLE = True
    except Exception:  # pragma: no cover — environment-specific
        # Never overwrite externally forced availability or clear test-injected _genai/_genai_types;
        # failed imports retain partial mocks under the fill-missing-only rule.
        if _GENAI_AVAILABLE is None:
            _GENAI_AVAILABLE = False
    # Available requires both a true flag and present objects, avoiding forced True plus failed imports
    # falsely advertising availability and dereferencing absent _genai_types.
    return bool(_GENAI_AVAILABLE) and _genai is not None and _genai_types is not None

class _GenaiToolsUnsupported(Exception):
    """Raised by the genai SDK path when tool support is unavailable
    (SDK missing, model rejected, etc.) so the caller can fall back to
    the OpenAI-compat path with tools silently disabled."""


# Gemini thought_signature differs by route: native SDK exposes raw bytes through Part
# .thought_signature, while OpenAI-compatible APIs expose base64 strings through
# tool_calls[].extra_content.google.thought_signature.
# Store shared tool-history dicts in the latter JSON-serializable form, suitable for disk
# persistence and unchanged replay when switching routes midhistory.
_EXTRA_CONTENT_VENDOR_KEY = "google"
_THOUGHT_SIGNATURE_KEY = "thought_signature"


def _extra_content_from_thought_signature(signature: Any) -> Optional[dict]:
    """Wrap a native ``Part.thought_signature`` into the shared history shape.

    Returns ``None`` when the part carries no signature, so ordinary
    non-thinking turns never grow an ``extra_content`` key."""
    if not signature:
        return None
    if isinstance(signature, str):
        encoded = signature  # Already base64; some SDK versions return str directly.
    else:
        import base64 as _b64
        try:
            encoded = _b64.b64encode(bytes(signature)).decode("ascii")
        except (TypeError, ValueError) as e:
            logger.debug("genai: unusable thought_signature dropped: %s", e)
            return None
    return {_EXTRA_CONTENT_VENDOR_KEY: {_THOUGHT_SIGNATURE_KEY: encoded}}


def _thought_signature_from_extra_content(extra_content: Any) -> Optional[bytes]:
    """Inverse of ``_extra_content_from_thought_signature``: pull the base64
    signature out of a history entry and decode it back to the bytes the
    native SDK wants. ``None`` when absent or unparseable."""
    if not isinstance(extra_content, dict):
        return None
    vendor = extra_content.get(_EXTRA_CONTENT_VENDOR_KEY)
    if not isinstance(vendor, dict):
        return None
    encoded = vendor.get(_THOUGHT_SIGNATURE_KEY)
    if not encoded or not isinstance(encoded, str):
        return None
    import base64 as _b64
    # Only locally written signatures guarantee standard alphabets/padding; compatibility
    # endpoint strings inherit Google's choices. Accept both alphabets and restore
    # missing padding instead of silently losing signatures over encoding conventions,
    # reintroducing the very 400 being fixed. validate=True still rejects genuine garbage.
    normalized = encoded.replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    try:
        return _b64.b64decode(normalized, validate=True)
    except (ValueError, TypeError) as e:
        logger.warning("genai: malformed thought_signature in history dropped: %s", e)
        return None


def _genai_function_call_part(types, *, call_id: str, name: str, args: dict, signature: Optional[bytes]):
    """Build the ``Part(function_call=...)`` for a replayed tool call, carrying
    ``thought_signature`` when history has one.

    Gemini rejects a follow-up request whose function-call history lost the
    signature (400 INVALID_ARGUMENT), so this is what makes multi-round tool
    calling work at all on thinking models. Falls back to a signature-less
    part if the installed SDK's ``Part`` doesn't accept the field — an old
    SDK is better served by the pre-existing (broken-for-Gemini-3) behaviour
    than by a hard crash."""
    fc = types.FunctionCall(id=call_id, name=name, args=args)
    if signature:
        try:
            return types.Part(function_call=fc, thought_signature=signature)
        except Exception as e:  # pragma: no cover — old google-genai only
            logger.warning(
                "genai: Part rejected thought_signature (%s); replaying without it",
                e,
            )
    return types.Part(function_call=fc)

def _genai_messages_to_contents(
    messages: list,
) -> tuple[Optional[str], list]:
    """Translate this client's ``_conversation_history`` into the
    ``(system_instruction, contents)`` tuple expected by google-genai
    ``generate_content_stream``.

    - SystemMessage → goes to ``system_instruction`` (genai keeps it
      out of ``contents``; first-system-message wins).
    - HumanMessage / AIMessage / dicts (assistant w/ tool_calls, tool
      role) → ``Content`` entries.

    Plain dicts with role=assistant + tool_calls are translated to
    ``Content(role="model", parts=[Part(function_call=...)])``; role=tool
    becomes ``Content(role="user", parts=[Part(function_response=...)])``.
    """
    if not _ensure_genai():
        raise _GenaiToolsUnsupported("google-genai SDK not importable")
    types = _genai_types
    system_instruction: Optional[str] = None
    contents: list = []

    for msg in messages:
        # ---- BaseMessage objects (existing path) --------------------
        if isinstance(msg, SystemMessage):
            if system_instruction is None:
                system_instruction = msg.content if isinstance(msg.content, str) else str(msg.content)
            else:
                system_instruction += "\n" + (msg.content if isinstance(msg.content, str) else str(msg.content))
            continue
        if isinstance(msg, HumanMessage):
            parts = _genai_parts_from_content(msg.content)
            contents.append(types.Content(role="user", parts=parts))
            continue
        if isinstance(msg, AIMessage):
            parts = _genai_parts_from_content(msg.content)
            contents.append(types.Content(role="model", parts=parts))
            continue
        # ---- Plain dict path (tool-calling history) -----------------
        if isinstance(msg, dict):
            role = msg.get("role")
            if role == "system":
                txt = msg.get("content", "")
                if isinstance(txt, str):
                    system_instruction = (system_instruction + "\n" + txt) if system_instruction else txt
                continue
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    # Mixed same-turn text/function_call: content holds
                    # _astream_genai_with_tools' streamed_text_buffer, representing words
                    # already shown before tool calls. Replay this text alongside
                    # function_call parts or next-turn generate_content_stream
                    # sees incomplete prior speech and repeats/reverses it,
                    # the symmetric counterpart of the preceding fix.
                    parts = []
                    text_content = msg.get("content")
                    if isinstance(text_content, str) and text_content.strip():
                        parts.append(types.Part(text=text_content))
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        try:
                            args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                        except json.JSONDecodeError:
                            args = {"_raw": fn.get("arguments") or ""}
                        parts.append(_genai_function_call_part(
                            types,
                            call_id=tc.get("id") or "",
                            name=fn.get("name") or "",
                            args=args,
                            # Replay thought_signature with its own function_call,
                            # or Gemini thinking models return 400 next turn.
                            signature=_thought_signature_from_extra_content(
                                tc.get("extra_content")
                            ),
                        ))
                    contents.append(types.Content(role="model", parts=parts))
                else:
                    parts = _genai_parts_from_content(msg.get("content", ""))
                    contents.append(types.Content(role="model", parts=parts))
                continue
            if role == "tool":
                # Best-effort: parse the JSON content back into a dict for
                # ``response`` since genai expects a structured response.
                raw_out = msg.get("content", "")
                try:
                    parsed = json.loads(raw_out) if isinstance(raw_out, str) else raw_out
                except json.JSONDecodeError:
                    parsed = {"result": raw_out}
                if not isinstance(parsed, dict):
                    parsed = {"result": parsed}
                # Gemini FunctionResponse.name must exactly match the original function_call.name,
                # or servers discard results as ownerless messages.
                # _execute_and_append_openai_tool_calls now writes name, but older/external
                # history may omit it. Search earlier assistant tool_calls for matching
                # tool_call_id; never substitute the ID itself,
                # whose call_xxx format is not a function name.
                fn_name = msg.get("name") or ""
                if not fn_name:
                    tcid = msg.get("tool_call_id") or ""
                    if tcid:
                        # Search preceding assistant tool_calls backward.
                        for prev in reversed(messages[: messages.index(msg)] if msg in messages else []):
                            prev_calls = (prev.get("tool_calls") or []) if isinstance(prev, dict) else []
                            for tc in prev_calls:
                                if tc.get("id") == tcid:
                                    fn_name = (tc.get("function") or {}).get("name") or ""
                                    break
                            if fn_name:
                                break
                if not fn_name:
                    # No match: placeholder names such as unknown_tool cannot match
                    # original function calls and create permanently orphaned
                    # tool results, as useless as not sending them,
                    # while wasting tokens. Skip malformed messages directly.
                    logger.warning(
                        "genai message conversion: dropping tool message with no "
                        "resolvable function name, tool_call_id=%s",
                        msg.get("tool_call_id"),
                    )
                    continue
                contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=fn_name, response=parsed)
                ]))
                continue
            if role == "user":
                parts = _genai_parts_from_content(msg.get("content", ""))
                contents.append(types.Content(role="user", parts=parts))
                continue
        # Fallback: stringify
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=str(getattr(msg, "content", msg)))],
        ))
    return system_instruction, contents

def _genai_parts_from_content(content: Any) -> list:
    """Render a ``BaseMessage.content`` value as a list of
    ``types.Part``. Strings become ``Part(text=...)``; multimodal lists
    (the ``[{type:image_url, image_url:{url:"data:image/jpeg;base64,..."}}, {type:text, text:...}]``
    shape that ``stream_text`` builds for vision) become a mix of
    ``inline_data`` parts and ``text`` parts."""
    types = _genai_types
    if isinstance(content, str):
        return [types.Part(text=content)]
    if isinstance(content, list):
        parts: list = []
        for entry in content:
            if not isinstance(entry, dict):
                parts.append(types.Part(text=str(entry)))
                continue
            etype = entry.get("type")
            if etype == "text":
                parts.append(types.Part(text=entry.get("text") or ""))
            elif etype == "image_url":
                url = (entry.get("image_url") or {}).get("url") or ""
                if url.startswith("data:image/"):
                    try:
                        header, b64 = url.split(",", 1)
                        mime = header.split(";")[0].split(":", 1)[-1] or "image/jpeg"
                        import base64 as _b64
                        parts.append(types.Part.from_bytes(
                            data=_b64.b64decode(b64), mime_type=mime,
                        ))
                    except Exception:
                        parts.append(types.Part(text=f"[image dropped: {entry.get('image_url')}]"))
                else:
                    parts.append(types.Part(text=f"[image url unsupported: {url}]"))
            else:
                parts.append(types.Part(text=json.dumps(entry, ensure_ascii=False)))
        return parts or [types.Part(text="")]
    return [types.Part(text=str(content))]

def _should_use_genai_sdk(model: str, base_url: str | None) -> bool:
    """Decide whether to route this Gemini-flavoured offline call through
    the native google-genai SDK instead of the OpenAI-compat endpoint.

    Returns True only when:
      1. ``google-genai`` is importable in the running env, AND
      2. base_url points at Google's native Gemini endpoint OR
         the model name contains "gemini" AND base_url is empty/None
         (i.e. caller wants direct genai with no proxy).

    This is a transport choice, not a capability one: both paths carry
    ``tools`` to the model, so callers never have to ask which one they
    landed on before handing over a tool definition. lanlan's free proxy
    exposes only the OpenAI-compat surface and therefore stays on the
    OpenAI path — it forwards tools like any other compat endpoint.
    """
    # Cheap route-string checks precede native Gemini SDK import,
    # keeping ordinary OpenAI-compatible/greeting client construction off heavyweight genai
    # imports; actual native users need it immediately afterward anyway.
    bl = (base_url or "").lower()
    ml = (model or "").lower()
    native = (
        any(h in bl for h in _GENAI_NATIVE_BASE_URL_HINTS)
        or (not bl and any(h in ml for h in _GENAI_NATIVE_MODEL_HINTS))
    )
    if not native:
        return False
    # Route checks need only availability booleans; respect known/forced flags, including tests
    # forcing _GENAI_AVAILABLE without google-genai, and import lazily only when unknown.
    if _GENAI_AVAILABLE is None:
        return _ensure_genai()
    return bool(_GENAI_AVAILABLE)


class _GenaiMixin:
    def _ensure_genai_client(self) -> None:
        """Build the genai client if nothing holds one right now.

        Called at every point that dereferences it, not once up front: a tool
        that hands back a picture makes the host call
        ``prepare_for_tool_images``, whose ``switch_model`` drops the client
        because the vision slot may carry its own key and endpoint. Anything
        that cached the client above the tool loop would then be holding None.
        """
        if self._genai_client is not None:
            return
        try:
            self._genai_client = _genai.Client(api_key=self.api_key or None)
        except Exception as e:
            raise _GenaiToolsUnsupported(f"genai.Client init failed: {e}") from e

    async def _astream_genai_with_tools(self, messages, **overrides):
        """google-genai streaming with tool support. Yields
        ``LLMStreamChunk``-shaped objects so the caller can be agnostic
        to which path delivered the stream.

        Tool calls (``part.function_call``) are aggregated within the
        current generation, then executed via ``on_tool_call``; the
        result is appended to ``messages`` (as a plain dict in the
        OpenAI-style "assistant w/ tool_calls" + "tool role" shape so
        the SAME history works for both genai and OpenAI-compat paths
        on subsequent turns) and ``generate_content_stream`` is
        re-invoked.

        Raises ``_GenaiToolsUnsupported`` if the SDK or this model
        cannot handle tools — caller falls back to OpenAI-compat."""
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        tool_image_slots = overrides.pop("_tool_image_slots", None)
        tool_bus_frames = overrides.pop("_tool_bus_frames", None)
        tool_frames_turn_id = overrides.pop("_tool_frames_turn_id", None)
        tool_rounds, _instruction, request_view = self._turn_request_view(overrides)
        response_generation = overrides.pop("_response_generation", None)

        generation_is_active = _generation_check(self, response_generation)

        if not generation_is_active():
            return
        if not _ensure_genai():
            raise _GenaiToolsUnsupported("google-genai SDK not importable")
        types = _genai_types

        # Build tools once per session (registry is identity-stable
        # across iterations within one stream_text call).
        tools_payload: list = []
        if self.has_tools():
            decls = [t.to_gemini_function_declaration() for t in self._tool_definitions]
            tools_payload = [types.Tool(function_declarations=decls)]

        # max_completion_tokens semantics: same intent as OpenAI path.
        gen_config_kw: dict = {}
        if self.llm is not None and self.llm.max_completion_tokens:
            gen_config_kw["max_output_tokens"] = int(self.llm.max_completion_tokens)
        if tools_payload:
            gen_config_kw["tools"] = tools_payload

        # Accumulate genuinely executed calls across iterations, symmetric with OpenAI;
        # zero versus nonzero makes cap diagnostics fundamentally different.
        executed_tool_calls = 0
        # Track early exit after zero execution with already streamed text, so logs do not
        # falsely claim iteration exhaustion when cap=3 may stop in round 1.
        zero_exec_break = False
        for tool_iter in range(self.max_tool_iterations):
            if not generation_is_active():
                return
            self._ensure_genai_client()
            system_instruction, contents = _genai_messages_to_contents(
                _slop_reduced_for_genai(request_view(messages))
            )
            cfg_kw = dict(gen_config_kw)
            if system_instruction:
                cfg_kw["system_instruction"] = system_instruction
            try:
                config = types.GenerateContentConfig(**cfg_kw)
            except Exception as e:
                raise _GenaiToolsUnsupported(f"GenerateContentConfig rejected: {e}") from e

            try:
                stream = await self._genai_client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                # Never wrap all exceptions as _GenaiToolsUnsupported.
                # Catching it in _astream_with_tools permanently sets
                # _genai_tools_unsupported=True, making transient
                # 429/5xx/network/auth failures downgrade every later session turn
                # to OpenAI compatibility, which cannot handle Gemini tools.
                # Mark SDK/model tools unsupported only when messages explicitly contain
                # tool-support keywords. Reraise others to outer generic handlers,
                # which fall back for one turn and retry native genai next turn.
                err_msg = str(e).lower()
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(
                        f"generate_content_stream rejected tools: {e}"
                    ) from e
                raise

            # No cancellation check here. With tools declared the SDK sends the
            # request during the await above, so a cancellation that landed
            # meanwhile is handled on the first chunk: publish what the request
            # carried, hand callers the answered chunk, and let the consumed
            # stream release its connection (the dual of _astream_declining_
            # tools). Without tools the SDK is lazy and the loop-top check has
            # already covered it.

            # Per-iteration accumulators.
            # list of (id, name, args_dict, raw_args_str, extra_content|None)
            collected_tool_calls: list = []
            # Whether any function_call appeared, including empty names later dropped:
            # use this, not collected, to identify tool rounds; all-dropped
            # calls leave collected empty without making this ordinary text.
            saw_tool_call_fragment = False
            had_text = False
            # Nonempty text actually yielded to users this turn differs from had_text,
            # whose text parts may be entirely suppressed by leak filters.
            # Base zero-execution retries only on visible text, matching OpenAI,
            # whose streamed_text_buffer already accumulates filtered output.
            visible_text = False
            # Accumulate user-yielded text as assistant-history content;
            # otherwise next-turn content="" leaves models unaware of
            # their own words, causing repeats/reversals.
            streamed_text_buffer = ""
            usage_emitted = False
            # Empty-completion diagnostics: latest finish_reason and prompt_feedback.
            # block_reason expose Gemini SAFETY/RECITATION/MAX_TOKENS.
            # Compatibility routes lose these details, so read directly from raw SDK
            # chunks here.
            iter_finish_reason: Optional[str] = None
            iter_block_reason: Optional[str] = None

            try:
                # Match OpenAI: previous-turn injected tool images count as delivered only when
                # the current SDK actually produces output.
                tool_frames_published = False
                async for chunk in stream:
                    if not tool_frames_published:
                        tool_frames_published = True
                        self._publish_pending_tool_frames(
                            tool_bus_frames, turn_id=tool_frames_turn_id
                        )
                    if not generation_is_active():
                        # Answered, then cancelled: what the request carried was
                        # delivered. Publish (above), then let callers publish
                        # their pending bus copies on one empty chunk.
                        yield _answered_chunk()
                        return
                    # prompt_feedback.block_reason identifies safety-blocked inputs,
                    # possibly without any candidate appearing.
                    pf = getattr(chunk, "prompt_feedback", None)
                    if pf is not None:
                        br = getattr(pf, "block_reason", None)
                        if br:
                            iter_block_reason = str(br)
                    candidates = getattr(chunk, "candidates", None) or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    fr = getattr(cand, "finish_reason", None)
                    if fr:
                        iter_finish_reason = str(fr)
                    cand_content = getattr(cand, "content", None)
                    parts = getattr(cand_content, "parts", None) or []
                    for part in parts:
                        if not generation_is_active():
                            return
                        # Skip thinking parts (Gemini 2.5+ thinking models) — but
                        # surface the boolean "is thinking" pulse first so the
                        # bubble shows on genai reasoning turns too (dual with the
                        # OpenAI-compat reasoning_content path). The thought TEXT
                        # itself is still dropped.
                        if getattr(part, "thought", False):
                            await self._notify_reasoning_active()
                            if not generation_is_active():
                                # Thought parts are never yielded, so callers
                                # may not have seen this answered request yet.
                                yield _answered_chunk()
                                return
                            continue
                        text = getattr(part, "text", None) or ""
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None:
                            saw_tool_call_fragment = True
                            tc_name = (getattr(fn_call, "name", "") or "").strip()
                            if not tc_name:
                                # Match OpenAI: empty-name function_calls result from SDK
                                # glitches/interrupted streams; storing them makes next-turn
                                # generate_content_stream reject anonymous-call schemas.
                                # Drop and warn.
                                logger.warning(
                                    "OmniOfflineClient(genai): dropping function_call "
                                    "with empty name (id=%r)",
                                    getattr(fn_call, "id", ""),
                                )
                                continue
                            args = dict(getattr(fn_call, "args", None) or {})
                            try:
                                raw_args = json.dumps(args, ensure_ascii=False)
                            except (TypeError, ValueError):
                                raw_args = "{}"
                            # thought_signature belongs to Part, not FunctionCall;
                            # capture into history here because next-turn Gemini thinking
                            # replay requires it unchanged alongside that call.
                            collected_tool_calls.append((
                                getattr(fn_call, "id", "") or "",
                                tc_name,
                                args,
                                raw_args,
                                _extra_content_from_thought_signature(
                                    getattr(part, "thought_signature", None)
                                ),
                            ))
                        elif text:
                            if tool_leak_filter is not None:
                                text = self._filter_tool_leak_content(
                                    text, tool_leak_filter, provider=tool_leak_provider,
                                )
                            had_text = True
                            if text:
                                visible_text = True
                            streamed_text_buffer += text
                            chunk_out = LLMStreamChunk(content=text)
                            if tool_leak_filter is not None:
                                setattr(chunk_out, "_tool_leak_filtered", True)
                            yield chunk_out
                    # Usage metadata may arrive on the chunk.
                    if not generation_is_active():
                        return
                    usage_meta = getattr(chunk, "usage_metadata", None)
                    if usage_meta is not None and not usage_emitted:
                        try:
                            usage_dict = {
                                "prompt_tokens": getattr(usage_meta, "prompt_token_count", 0) or 0,
                                "completion_tokens": getattr(usage_meta, "candidates_token_count", 0) or 0,
                                "total_tokens": getattr(usage_meta, "total_token_count", 0) or 0,
                            }
                            # Empty-completion diagnostics: store prompt_tokens on self,
                            # for upper stream_text fallback warnings, symmetric with OpenAI.
                            if usage_dict["prompt_tokens"]:
                                self._last_prompt_tokens = usage_dict["prompt_tokens"]
                            yield LLMStreamChunk(
                                content="",
                                usage_metadata=usage_dict,
                                response_metadata={"token_usage": usage_dict},
                            )
                            usage_emitted = True
                        except Exception as usage_err:
                            # Optional usage telemetry must not break streaming for SDK differences,
                            # missing fields, or incorrect types. Debug-log only,
                            # letting user replies continue.
                            logger.debug(
                                "genai usage_metadata emit skipped: %s",
                                usage_err,
                            )
            except Exception as e:
                err_msg = str(e).lower()
                # Match generate_content_stream's own exception handling:
                # only messages explicitly combining tool/function with not_support/unsupported/
                # invalid identify unsupported SDK/model tools and permanently
                # downgrade compatibility. Other stream exceptions, including function-call
                # timeouts, propagate for temporary fallback and retry next turn.
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(f"genai stream rejected tools: {e}") from e
                raise

            if not generation_is_active():
                return
            # Empty-completion diagnostics persist self fields and emit dedicated INFO logs,
            # symmetric with OpenAI; either finish_reason/block_reason may arrive first, and whichever
            # arrives wins; stream_text/prompt_ephemeral fallback warnings read both fields.
            self._last_finish_reason = iter_finish_reason
            self._last_block_reason = iter_block_reason
            if not had_text and not collected_tool_calls:
                # Defensive getattr matches OpenAI and supports __new__ test doubles bypassing initialization.
                logger.info(
                    "OmniOfflineClient(genai): empty completion finish_reason=%s "
                    "block_reason=%s tool_iter=%d model=%s prompt_tokens=%s",
                    iter_finish_reason, iter_block_reason, tool_iter,
                    getattr(self, "model", None),
                    getattr(self, "_last_prompt_tokens", None),
                )

            if saw_tool_call_fragment and self.on_tool_call is not None:
                # Ordering invariant, symmetric with OpenAI _tools.py's finalize/yield-tail-before-notify:
                # emit every leak-filter-held pretool fragment before
                # round-start callbacks. Buffered callers clear reply_chunks
                # there; notifying before yielding tail puts held text into
                # newly cleared buffers and still sends it externally,
                # defeating the filter's holdback.
                #
                # This is a tool round even if every empty-name call drops;
                # attaching round-start only under nonempty collected misses
                # all-dropped calls, precisely where pretool text would escape
                # as a complete reply.
                #
                # Tail enters streamed_text_buffer only for nonempty collected,
                # where an assistant history turn is written and tail belongs to
                # its content. All-dropped rounds write no assistant turn and
                # yield tail only to users.
                if tool_leak_filter is not None:
                    tail, event = tool_leak_filter.finalize()
                    if event:
                        log_tool_leak_filtered(event, provider=tool_leak_provider)
                    if tail:
                        if collected_tool_calls:
                            streamed_text_buffer += tail
                        # Tail is genuinely user-visible even without buffer insertion
                        # on all-drop paths; include it in zero-execution decisions.
                        visible_text = True
                        tail_chunk = LLMStreamChunk(content=tail)
                        setattr(tail_chunk, "_tool_leak_filtered", True)
                        yield tail_chunk
                    tool_leak_filter.reset()
                await self._notify_tool_round_start()
            if (
                saw_tool_call_fragment
                and not collected_tool_calls
                and self.on_tool_call is not None
            ):
                # Zero-execution rounds with all empty-name calls dropped leave messages
                # unchanged. Retrying provides no new information and repeats
                # the same pretool text; cap=3 produced repeated checking
                # promises before a final answer in actual main-app testing.
                #
                # Retry only if nothing reached users this round, making retries
                # visibility-free while SDK glitches may resolve next round. Otherwise
                # break toward forced-finalize. This preserves #2597's purpose:
                # avoid early returns and reach forced-finalize plus cap
                # diagnostics; only excessive retries are reduced.
                #
                # Use visible_text rather than had_text: tool-marker text entirely
                # suppressed by leak filters still sets had_text but shows users
                # nothing. Abandoning retries there discards calls that could succeed
                # next round (Codex P2).
                #
                # Round-start subscribers such as QQ recall buffer replies and
                # already cleared reply_chunks above, so none of this pretool text
                # actually reached users. Replay concerns do not apply;
                # breaking only loses recoverable tool calls and leaves recall
                # replies without memory results (Codex).
                if visible_text and getattr(self, "on_tool_round_start", None) is None:
                    zero_exec_break = True
                    break
                continue
            if collected_tool_calls and self.on_tool_call is not None:
                if not generation_is_active():
                    return
                # Execute tools, append a unified assistant + tool history (dict shape
                # accepted by both paths), then continue tool-iteration loop.
                tool_calls_dict = []
                for i, (tc_id, tc_name, _args, tc_raw, tc_extra) in enumerate(collected_tool_calls):
                    entry = {
                        "id": tc_id or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc_name, "arguments": tc_raw},
                    }
                    if tc_extra:
                        entry["extra_content"] = tc_extra
                    tool_calls_dict.append(entry)
                # Persist this turn's already-streamed text alongside history. Gemini allows
                # text and function_call parts within one turn; leaving
                # content empty hides prior words from next-turn context,
                # causing repetition/reversal and history ordering inconsistent with actual generation.
                assistant_turn = {
                    "role": "assistant",
                    # Symmetric with the OpenAI path: strip leaked <think> CoT
                    # before persisting the pre-tool text to history (no-op on
                    # clean replies / the genai path, which routes thought out).
                    "content": strip_thinking_segments(streamed_text_buffer),
                    "tool_calls": tool_calls_dict,
                }
                # Built before the round (see _run_tool_round): a call that
                # cannot be constructed stops the turn before any side effect.
                tool_calls = [
                    ToolCall(
                        name=tc_name,
                        arguments=tc_args,
                        call_id=tc_id or f"call_{i}",
                        raw_arguments=tc_raw,
                    )
                    for i, (tc_id, tc_name, tc_args, tc_raw, _tc_extra)
                    in enumerate(collected_tool_calls)
                ]
                executed, live = await self._run_tool_round(
                    messages,
                    assistant_turn,
                    tool_calls,
                    tool_image_slots=tool_image_slots,
                    tool_bus_frames=tool_bus_frames,
                    generation_is_active=generation_is_active,
                    log_prefix="OmniOfflineClient(genai)",
                    tool_rounds=tool_rounds,
                )
                executed_tool_calls += executed
                if not live:
                    # Cancelled: the kept prefix is persisted, so the caller
                    # still needs the sentinel; no image turns, no next call.
                    if executed:
                        yield LLMStreamChunk(content="", tool_round_persisted=True)
                    return
                # Sentinel mirrors OpenAI, telling upper stream_text to clear
                # final-segment buffers because pretool text now resides in
                # assistant-turn content.
                yield LLMStreamChunk(content="", tool_round_persisted=True)
                # Loop again to let the model produce a final answer. A
                # cancellation during that yield is caught at the loop top or,
                # after the last round, at the loop exit.
                if not had_text:
                    continue
                # Edge case: model emitted text AND tool calls — text already
                # streamed to the user. Continue to next iter to give the
                # model a chance to follow up after seeing tool results.
                continue
            return
        if not generation_is_active():
            return
        if executed_tool_calls == 0:
            # Match OpenAI: tool rounds executing nothing because every call has
            # empty names deserve their own high-value diagnostic.
            logger.warning(
                "OmniOfflineClient(genai): %s with 0 executed tool calls "
                "(dropped nameless function_call parts); forcing final answer "
                "without tools",
                "zero-execution round after streaming text" if zero_exec_break
                else f"tool iteration cap {self.max_tool_iterations} reached",
            )
        elif zero_exec_break:
            # Prior successful calls followed by a zero-execution visible-text round do not
            # exhaust iterations; never label them runaway caps.
            logger.warning(
                "OmniOfflineClient(genai): zero-execution round after streaming "
                "text (dropped nameless function_call parts) with %d executed "
                "tool calls so far; forcing final answer without tools",
                executed_tool_calls,
            )
        elif self.max_tool_iterations == 1:
            # cap=1 is the intended QQ single-round budget; normal recall necessarily
            # exhausts it, so log INFO, symmetric with OpenAI.
            logger.info(
                "OmniOfflineClient(genai): single tool round budget spent; "
                "forcing final answer without tools",
            )
        else:
            logger.warning(
                "OmniOfflineClient(genai): tool iteration cap %d reached; forcing final answer without tools",
                self.max_tool_iterations,
            )
        # Forced-finalize matches OpenAI: remove tools and regenerate final text
        # from accumulated results, preventing total silence at caps.
        # Propagate SDK failures unchanged to existing stream_text/prompt_ephemeral
        # retry/status/response_discarded bubble-cleanup handling, matching OpenAI.
        # Converting failures to warnings disguises them as empty replies, potentially
        # restoring postcap silence for weak models, contrary to this fallback's purpose.
        # Loop-exit checks already cover cancellation; only synchronous logging intervenes.
        final_cfg_kw = {k: v for k, v in gen_config_kw.items() if k != "tools"}
        final_system_instruction, final_contents = _genai_messages_to_contents(
            _slop_reduced_for_genai(request_view(messages))
        )
        if final_system_instruction:
            final_cfg_kw["system_instruction"] = final_system_instruction
        final_config = types.GenerateContentConfig(**final_cfg_kw)
        # Same reason as inside the loop: the forced finalize is reached after
        # the tool iterations, so a switch_model that happened in one of them
        # has already dropped the client.
        self._ensure_genai_client()
        final_stream = await self._genai_client.aio.models.generate_content_stream(
            model=self.model,
            contents=final_contents,
            config=final_config,
        )
        # Safe to return here: the forced final call carries no tools, and
        # without tools the SDK is lazy, so nothing was sent yet.
        if not generation_is_active():
            return
        final_finish_reason: Optional[str] = None
        final_block_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        final_had_text = False
        # Match OpenAI by retaining unreleased tool images for forced finalization too.
        tool_frames_published = False
        async for chunk in final_stream:
            if not tool_frames_published:
                tool_frames_published = True
                self._publish_pending_tool_frames(
                    tool_bus_frames, turn_id=tool_frames_turn_id
                )
            if not generation_is_active():
                # Answered, then cancelled: see the tool-loop stream above.
                yield _answered_chunk()
                return
            # Collect block_reason/finish_reason/prompt_tokens symmetrically with normal genai,
            # or safety/recitation/max-token suppression during forced finalization
            # makes upper diagnostics cite the previous tool iteration's
            # stale finish reason.
            pf = getattr(chunk, "prompt_feedback", None)
            if pf is not None:
                br = getattr(pf, "block_reason", None)
                if br:
                    final_block_reason = str(br)
            usage_meta = getattr(chunk, "usage_metadata", None)
            if usage_meta is not None:
                pt = getattr(usage_meta, "prompt_token_count", 0) or 0
                if pt:
                    final_prompt_tokens = pt
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                continue
            cand = candidates[0]
            fr = getattr(cand, "finish_reason", None)
            if fr:
                final_finish_reason = str(fr)
            cand_content = getattr(cand, "content", None)
            for part in (getattr(cand_content, "parts", None) or []):
                if not generation_is_active():
                    return
                if getattr(part, "thought", False):
                    continue
                text = getattr(part, "text", None) or ""
                if text:
                    if tool_leak_filter is not None:
                        text = self._filter_tool_leak_content(
                            text, tool_leak_filter, provider=tool_leak_provider,
                        )
                    final_had_text = True
                    chunk_out = LLMStreamChunk(content=text)
                    if tool_leak_filter is not None:
                        setattr(chunk_out, "_tool_leak_filtered", True)
                    yield chunk_out
        # Persist this forced-finalize call's own diagnostics, including prompt_tokens.
        # Keep prompt_tokens local and assign unconditionally after streaming; blocked/missing usage writes None,
        # never prior iteration values that contaminate INFO/LLM_NO_RESPONSE diagnostics.
        self._last_finish_reason = final_finish_reason
        self._last_block_reason = final_block_reason
        self._last_prompt_tokens = final_prompt_tokens
        if not final_had_text:
            logger.info(
                "OmniOfflineClient(genai): forced-finalize empty completion "
                "finish_reason=%s block_reason=%s model=%s prompt_tokens=%s",
                final_finish_reason, final_block_reason,
                getattr(self, "model", None),
                final_prompt_tokens,
            )
