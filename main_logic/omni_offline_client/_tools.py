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
from ._lifecycle import _suspend_dialog_slop

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
        return [t.to_openai_chat() for t in self._tool_definitions]

    async def _execute_and_append_openai_tool_calls(
        self,
        messages,
        calls,
        assistant_text: str = "",
        assistant_reasoning: str = "",
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
        # 防御性过滤：``ChatOpenAI.collect_tool_calls`` 已会丢弃空 name 槽位，
        # 但万一调用方直接构造（或上游聚合实现替换），这里再兜一层 ——
        # tool_calls 历史中混入空 name 会被下一轮 server schema reject，
        # 整条会话连带挂掉。
        calls = [c for c in calls if (getattr(c, "name", "") or "").strip()]
        if not calls:
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
                    with _suspend_dialog_slop():
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
            self._append_tool_result_images(messages, result)
        return len(calls)

    # ------------------------------------------------------------------
    # Tool image channel
    # ------------------------------------------------------------------
    #
    # A tool result carries pixels in ``ToolResult.images`` when — and only
    # when — ``LLMSessionManager._route_tool_images`` decided the current
    # model can read them (text-only models and every realtime session get a
    # transcription folded into the output instead, and an empty list here).
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

    _TOOL_IMAGE_DEFAULT_CAPTION = "（工具返回的画面）"

    def _append_tool_result_images(self, messages, result) -> None:
        """Append one multimodal user turn carrying every image in ``result``.

        No-op when the tool returned none, which is the overwhelmingly common
        case — nothing is allocated and no slot is recorded.
        """
        images = getattr(result, "images", None)
        if not images:
            return

        content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime};base64,{img.data_b64}"},
            }
            for img in images
        ]
        # Always caption: several providers reject a content array that is
        # nothing but image parts.
        caption = next(
            (img.vision_prompt.strip() for img in images if img.vision_prompt.strip()),
            self._TOOL_IMAGE_DEFAULT_CAPTION,
        )
        content.append({"type": "text", "text": caption})

        message = {"role": "user", "content": content}
        messages.append(message)

        # Remember the list too: ``prompt_ephemeral`` runs the tool loop over
        # a scratch list rather than ``_conversation_history``, so an index
        # alone would point into the wrong history.
        slots = getattr(self, "_pending_tool_image_slots", None)
        if slots is None:
            slots = []
            self._pending_tool_image_slots = slots
        output = result.output if isinstance(result.output, dict) else {}
        shot_id = output.get("shot_id")
        recall_hint = output.get("recall_hint")
        recall_suffix = ""
        if isinstance(shot_id, str) and shot_id.strip():
            recall_suffix = f"；句柄 {shot_id.strip()}"
            if isinstance(recall_hint, str) and recall_hint.strip():
                recall_suffix += f"；{recall_hint.strip()}"
        slots.append((
            messages,
            len(messages) - 1,
            message,
            f"[工具 {result.name} 返回的画面已从上下文移除；"
            f"图片只在产生它的那一轮可见{recall_suffix}]",
        ))

    def _release_tool_image_slots(self) -> None:
        """Swap every injected image turn for its text placeholder.

        Called from the exit of both tool loops (``finally``, so an abandoned
        generator still cleans up). Identity is re-checked before writing:
        another path may have rebuilt or truncated the history underneath us,
        and a blind index write would corrupt an unrelated message.
        """
        slots = getattr(self, "_pending_tool_image_slots", None)
        if not slots:
            return
        for messages, index, message, placeholder in slots:
            try:
                if 0 <= index < len(messages) and messages[index] is message:
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
        finally:
            # Every model call that needed the pixels has happened by now: this
            # is the join point of the genai and OpenAI-compat tool loops, and
            # of both callers (``stream_text`` and ``prompt_ephemeral``). In a
            # ``finally`` so an abandoned generator (GeneratorExit) still drops
            # the base64 out of history.
            self._release_tool_image_slots()

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

        # 跨迭代累计真正执行过的 tool call 数：0 与非 0 在封顶日志里是两种
        # 性质（"进了 tool 分支但全是无名分片" vs 正常耗尽预算）。
        executed_tool_calls = 0
        # 是否因"零执行轮且已经流过文本"提前跳出循环（与 genai 路径对偶）：
        # 封顶日志据此别谎称迭代被耗尽。
        zero_exec_break = False
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
                # 本轮已确认是 tool 轮：先给缓冲型调用方（QQ 插件）一个丢弃
                # pre-tool 文本的锚点。必须在执行器之前、且不依赖 handler 被
                # 真正调用——无名分片会让下面的 calls 过滤后为空、handler 一次
                # 都不跑，只挂在 handler 入口的清理在那条路径上永不发生。
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
                )
                executed_tool_calls += executed_this_round
                if executed_this_round:
                    # 通知上游 ``stream_text``：本轮的 pre-tool text + tool_calls
                    # 已经写进 history（assistant turn）。stream_text 据此清空
                    # final-segment buffer，避免之后 append 的 final AIMessage
                    # 把同一段 pre-tool 文本第二次写进 history。
                    #
                    # 零执行轮（全是无名分片，什么都没写进 history）不发：
                    # sentinel 的契约是"pre-tool 文本已持久化"，此时为假——
                    # 发了会让这段文本既不在 tool_calls 行、又被 final
                    # AIMessage 跳过，从历史里彻底消失。
                    yield _LLMStreamChunk(content="", tool_round_persisted=True)
                elif (
                    streamed_text_buffer
                    and getattr(self, "on_tool_round_start", None) is None
                ):
                    # 零执行轮：messages 一个字都没变，重来一轮不会有新信息，
                    # 只会让模型把同样的 pre-tool 文本再流一遍给用户。已经流
                    # 过文本就跳出循环去 forced-finalize（forced-finalize 与
                    # 封顶日志都保留，被砍掉的只是没有意义的重试）；什么都
                    # 没流出去的零执行轮仍允许再试一轮——重试没有用户可见
                    # 代价，而 provider 抖动确实可能下一轮就正常。
                    #
                    # 装了 round-start 回调的调用方是缓冲型的（回调体就是
                    # 丢弃 pre-tool 文本），本轮那截文本根本没送到用户手里，
                    # "重放"无从谈起——那种情况仍然重试，别白丢一次本可恢复
                    # 的工具调用。与 genai 路径对偶。
                    zero_exec_break = True
                    break
                continue
            return
        if executed_tool_calls == 0:
            # 进过 tool 分支却一次都没执行成（provider 流出的 tool_call 分片
            # 始终没带 name，collect_tool_calls 全部丢弃）：这是最值得排查的
            # 形态，不能伪装成普通封顶日志。
            logger.warning(
                "OmniOfflineClient: %s with 0 executed tool calls (provider "
                "streamed nameless tool_call fragments); forcing final answer "
                "without tools",
                "zero-execution round after streaming text" if zero_exec_break
                else f"tool iteration cap {self.max_tool_iterations} reached",
            )
        elif zero_exec_break:
            # 前面几轮真的执行过 tool，最后一轮零执行且已流过文本：循环没被
            # 耗尽，别打成 runaway 封顶（与 genai 路径对偶）。
            logger.warning(
                "OmniOfflineClient: zero-execution round after streaming text "
                "(provider streamed nameless tool_call fragments) with %d "
                "executed tool calls so far; forcing final answer without tools",
                executed_tool_calls,
            )
        elif self.max_tool_iterations == 1:
            # cap=1 是调用方的设计内单轮预算（QQ 插件：一轮一召回）：一次
            # 成功的 tool 轮必然耗尽循环，这不是 runaway，降为 INFO——否则
            # 每次正常召回都打 WARNING，真正的封顶信号被淹没。
            logger.info(
                "OmniOfflineClient: single tool round budget spent; "
                "forcing final answer without tools",
            )
        else:
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
