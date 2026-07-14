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
    LLMStreamChunk,
    List,
    OnToolCallCallback,
    Optional,
    ToolCall,
    ToolDefinition,
    ToolLeakFilter,
    ToolResult,
    log_tool_leak_filtered,
    logger,
    parse_arguments_json,
    strip_thinking_segments,
)

from ._genai_support import (
    _GenaiToolsUnsupported,
)

class _ToolingMixin:
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

    def has_tools(self) -> bool:
        return bool(self._tool_definitions) and self.on_tool_call is not None

    def _openai_tools_payload(self) -> Optional[List[dict]]:
        """OpenAI Chat Completions ``tools`` param — nested under
        ``function``. Returns ``None`` when the caller hasn't enabled
        tools, so ``_params`` skips both ``tools`` and ``tool_choice``."""
        if not self.has_tools():
            return None
        return [t.to_openai_chat() for t in self._tool_definitions]

    async def _execute_and_append_openai_tool_calls(
        self,
        messages,
        calls,
        assistant_text: str = "",
        assistant_reasoning: str = "",
    ) -> None:
        """Run each tool call through ``on_tool_call`` and mutate
        ``messages`` in place: append one assistant turn announcing all
        tool calls, then one tool-role message per call carrying the
        result JSON. Both shapes follow the OpenAI Chat Completions spec
        so the next astream invocation sees a valid history.

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
        # 防御性过滤：``ChatOpenAI.collect_tool_calls`` 已会丢弃空 name 槽位，
        # 但万一调用方直接构造（或上游聚合实现替换），这里再兜一层 ——
        # tool_calls 历史中混入空 name 会被下一轮 server schema reject，
        # 整条会话连带挂掉。
        calls = [c for c in calls if (getattr(c, "name", "") or "").strip()]
        if not calls:
            return
        assistant_turn = {
            "role": "assistant",
            "content": assistant_text or "",
            "tool_calls": [
                {
                    "id": c.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": c.arguments or "{}",
                    },
                }
                for i, c in enumerate(calls)
            ],
        }
        if assistant_reasoning:
            assistant_turn["reasoning_content"] = assistant_reasoning
        messages.append(assistant_turn)
        for i, c in enumerate(calls):
            tool_call = ToolCall(
                name=c.name,
                arguments=parse_arguments_json(c.arguments),
                call_id=c.id or f"call_{i}",
                raw_arguments=c.arguments or "",
            )
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
                    result = await handler(tool_call)
                except Exception as e:
                    logger.exception("OmniOfflineClient: on_tool_call '%s' raised", c.name)
                    result = ToolResult(
                        call_id=tool_call.call_id, name=tool_call.name,
                        output={"error": f"{type(e).__name__}: {e}"},
                        is_error=True, error_message=str(e),
                    )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.call_id,
                # 写入 ``name`` 让 Gemini 路径能直接用（FunctionResponse.name
                # 必须与原 function_call name 完全一致）。OpenAI-compat 不需要
                # 这个字段也不会因此报错——它只用 tool_call_id 关联。
                "name": tool_call.name,
                "content": result.output_as_json_string(),
            })

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
          OpenAI-compat path (where tools won't work — that's the
          documented lanlan.app/free trade-off).
        - Otherwise: ``_astream_openai_with_tools``.
        """
        tool_leak_filter = overrides.pop("_tool_leak_filter", None)
        tool_leak_provider = overrides.pop("_tool_leak_provider", None)
        if self._use_genai_sdk and not self._genai_tools_unsupported:
            # 跟踪本轮 Gemini 路径是否已经把 text chunk yield 给上游。如果
            # 已经吐过文本，再 fallback 到 OpenAI-compat 会让用户在同一轮
            # 看到"半截 Gemini 文本 + 一份 OpenAI 重新生成的文本"拼接，
            # 必须把异常向上 raise，让 stream_text 的 retry/discard 流程
            # 触发"清空气泡 + 通知 response_discarded"的标准处理。
            genai_emitted_text = False
            try:
                async for chunk in self._astream_genai_with_tools(
                    messages,
                    _tool_leak_filter=tool_leak_filter,
                    _tool_leak_provider=tool_leak_provider,
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
                    # 已吐文本：保留永久禁用旗标，但本轮不静默拼接，
                    # 让上游 retry 路径基于 attempt+1 重新走（下次会直接
                    # 进 OpenAI-compat，因为 _genai_tools_unsupported=True）。
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
                    # 同上：已吐过文本不能再静默 fallback，向上 raise 让 retry
                    # 流程清空气泡后基于 attempt+1 重试（下一次仍会先尝试
                    # genai，因为 transient 不翻 _genai_tools_unsupported）。
                    raise
                if tool_leak_filter is not None:
                    tool_leak_filter.reset()
        async for chunk in self._astream_openai_with_tools(
            messages,
            _tool_leak_filter=tool_leak_filter,
            _tool_leak_provider=tool_leak_provider,
            **overrides,
        ):
            yield chunk

    async def _astream_visible_with_tools(self, messages, **overrides):
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
                messages, _tool_leak_filter=leak_filter, _tool_leak_provider=provider, **overrides
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
        tools_payload = self._openai_tools_payload()
        if tools_payload:
            overrides.setdefault("tools", tools_payload)
        else:
            # Belt-and-suspenders: never leak tool_choice without tools.
            overrides.pop("tool_choice", None)
            overrides.pop("tools", None)

        for tool_iter in range(self.max_tool_iterations):
            deltas_per_chunk: list = []
            finish_reason: Optional[str] = None
            # 累积本轮已 yield 给上游的 text，下面 finish_reason=tool_calls
            # 时一起写进 assistant 历史。OpenAI Chat Completions 协议允许同
            # 一 turn 既有 content 又有 tool_calls；某些兼容 provider 真会
            # 先吐文字再进 tool_calls。和 Gemini 路径完全对偶。
            streamed_text_buffer = ""
            # Thinking 模型本轮的推理链：finish_reason=tool_calls 时必须随
            # assistant tool_calls turn 一起回填，否则部分 provider 下一轮报
            # 400（reasoning_content must be passed back）。普通端点恒为空。
            streamed_reasoning_buffer = ""
            async for chunk in self.llm.astream(messages, **overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
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
                if chunk.tool_call_deltas:
                    deltas_per_chunk.append(chunk.tool_call_deltas)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                # Empty-completion 诊断：记最新的 finish_reason 和 prompt_tokens，
                # 给上层 stream_text / prompt_ephemeral 的兜底 warning 用。
                # usage chunk（terminal）才带 prompt_tokens；前面 text chunk 不带。
                if chunk.usage_metadata:
                    pt = chunk.usage_metadata.get("prompt_tokens")
                    if pt:
                        self._last_prompt_tokens = pt
                # 纯 reasoning chunk（thinking 模型先吐推理链，content 为空、无
                # tool delta / finish / usage）只在上面累积进 buffer，不向下游
                # 转发：``stream_text`` 在首个 yield 的 chunk 上记 TTFT，放行
                # reasoning-only 会把"首推理 token"误当首 token，拉低延迟埋点。
                if (
                    getattr(chunk, "reasoning_content", None)
                    and not getattr(chunk, "content", None)
                    and not chunk.tool_call_deltas
                    and not chunk.finish_reason
                    and not chunk.usage_metadata
                ):
                    # Pure reasoning-only chunk: already pulsed above; drop it so
                    # the "first token" TTFT埋点 isn't fooled by a reasoning token.
                    continue
                # 永远 yield 文本 chunk —— 即便是 tool-only turn 也可能在
                # finish_reason=tool_calls 之前 emit usage chunk 和空 content。
                yield chunk
            # 记录本次 attempt 的最终 finish_reason，供上层 empty-completion
            # 兜底警告引用（"safety" / "length" / "content_filter" / "stop" 都
            # 可能在 content 为空时出现，是诊断 Gemini-via-OpenAI-compat 静默
            # empty 的关键线索）。
            self._last_finish_reason = finish_reason
            if (
                not streamed_text_buffer
                and not deltas_per_chunk
                and finish_reason != "tool_calls"
            ):
                # 单独一行 INFO：empty completion 落地证据。tool_iter / model 一起
                # 打出来，配合上层 warning 可以拼出"哪一轮哪个 attempt 被 safety
                # 拦了 / 被 length 截了"。getattr 防御：测试桩可能 __new__ 绕过
                # __init__，所以 model / _last_prompt_tokens 字段都用 getattr 兜底。
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
                # ChatOpenAI is the right import even though we're outside
                # ChatOpenAI — `collect_tool_calls` is a staticmethod.
                from utils.llm_client import ChatOpenAI as _ChatOpenAI
                from utils.llm_client import LLMStreamChunk as _LLMStreamChunk
                calls = _ChatOpenAI.collect_tool_calls(deltas_per_chunk)
                await self._execute_and_append_openai_tool_calls(
                    messages, calls,
                    # Strip any leaked <think> CoT before it lands in history:
                    # the streaming guard (ThinkingStreamStripper) only protects
                    # TTS/UI; this assembled pre-tool text is persisted raw to the
                    # assistant tool-call turn, so a leak-prone Focus turn would
                    # otherwise carry CoT into the next turn's context. No-op on
                    # clean replies (no think tag present).
                    assistant_text=strip_thinking_segments(streamed_text_buffer),
                    assistant_reasoning=streamed_reasoning_buffer,
                )
                # 通知上游 ``stream_text``：本轮的 pre-tool text + tool_calls
                # 已经写进 history（assistant turn）。stream_text 据此清空
                # final-segment buffer，避免之后 append 的 final AIMessage
                # 把同一段 pre-tool 文本第二次写进 history。
                yield _LLMStreamChunk(content="", tool_round_persisted=True)
                continue
            return
        logger.warning(
            "OmniOfflineClient: tool iteration cap %d reached; forcing final answer without tools",
            self.max_tool_iterations,
        )
        # Forced-finalize：工具轮次封顶后，去掉 tools 再调一次，逼模型基于已
        # 积累的 tool 结果给出最终文本。否则弱模型在 finish_reason=tool_calls
        # 上死循环到封顶后整轮静默，上游只能报"未产生文本回复"，用户那边就
        # 表现为不回话。去掉 tools 后模型无法再发起调用，必须输出文本。
        final_overrides = {
            k: v for k, v in overrides.items() if k not in ("tools", "tool_choice")
        }
        final_finish_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        async for chunk in self.llm.astream(messages, **final_overrides):  # noqa: LLM_INPUT_BUDGET  # dialog messages bounded by SESSION_ARCHIVE_TRIGGER_TOKENS + RECENT_PER_MESSAGE_MAX_TOKENS truncation; output budget set per-call via overrides.
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
            # 与常规 tool-loop 路径一致：不向下游转发 thinking 模型的纯
            # reasoning chunk（有 reasoning_content、无 content / tool delta /
            # finish / usage）。stream_text 在首个 yield 的 chunk 上记 TTFT，
            # 放行 reasoning-only 会把"首推理 token"误当首 token，污染封顶轮延迟埋点。
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
        # prompt_tokens 走局部变量、流结束后无条件回填（与 genai 路径同口径）：这次
        # forced-finalize 没给 usage 时写回 None，而非沿用上一轮 tool-iteration 的旧
        # 值，避免上层 empty-completion 诊断串台。
        self._last_finish_reason = final_finish_reason
        self._last_prompt_tokens = final_prompt_tokens
