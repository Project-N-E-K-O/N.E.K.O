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

_GENAI_AVAILABLE: bool | None = None  # None = 尚未尝试导入

def _ensure_genai() -> bool:
    """Import google-genai on first call and cache the result (success or failure).

    Returns whether the SDK is available. Under a concurrent race the worst case is one
    duplicate import; Python's module cache makes it idempotent with no side effects.
    """
    global _genai, _genai_types, _GENAI_AVAILABLE
    # 显式强制不可用优先级最高（测试用它当强制降级开关）→ 即便对象已塞进全局也降级。
    if _GENAI_AVAILABLE is False:
        return False
    # 对象已就位（真 import 过 / 测试注入了 mock）→ 直接信任，不重导入。
    if _genai is not None and _genai_types is not None:
        _GENAI_AVAILABLE = True
        return True
    try:
        from google import genai as genai_mod
        from google.genai import types as genai_types_mod
        # 只补缺失的，保住测试可能注入的 _genai mock。
        if _genai is None:
            _genai = genai_mod
        if _genai_types is None:
            _genai_types = genai_types_mod
        _GENAI_AVAILABLE = True
    except Exception:  # pragma: no cover — environment-specific
        # 不覆盖外部强制设过的可用性标志；也不清空可能被测试注入的 _genai/_genai_types
        # （只补缺失原则——导入失败时保留已注入的部分 mock）。
        if _GENAI_AVAILABLE is None:
            _GENAI_AVAILABLE = False
    # 只有可用标志为真且对象确实就位才算可用——避免 forced True 但 import 失败时
    # 谎报可用、让调用点在 None 上解引用 _genai_types。
    return bool(_GENAI_AVAILABLE) and _genai is not None and _genai_types is not None

class _GenaiToolsUnsupported(Exception):
    """Raised by the genai SDK path when tool support is unavailable
    (SDK missing, model rejected, etc.) so the caller can fall back to
    the OpenAI-compat path with tools silently disabled."""


# Gemini 的 thought_signature 在两条路径上形态不同：native SDK 给 ``Part
# .thought_signature`` 的裸 bytes，OpenAI-compat 端点给
# ``tool_calls[].extra_content.google.thought_signature`` 的 base64 字符串。
# 统一工具调用历史（两条路径共用的 dict）一律按后者存：JSON 可序列化、能
# 直接落盘、且换一条路线继续同一段历史时也能原样回传。
_EXTRA_CONTENT_VENDOR_KEY = "google"
_THOUGHT_SIGNATURE_KEY = "thought_signature"


def _extra_content_from_thought_signature(signature: Any) -> Optional[dict]:
    """Wrap a native ``Part.thought_signature`` into the shared history shape.

    Returns ``None`` when the part carries no signature, so ordinary
    non-thinking turns never grow an ``extra_content`` key."""
    if not signature:
        return None
    if isinstance(signature, str):
        encoded = signature  # 已是 base64（部分 SDK 版本直接给 str）
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
    # 只有我们自己写的那半边保证是标准字母表 + padding；另一半是 compat
    # 端点原样存下来的串，字母表和 padding 由 Google 说了算。这里两种字母表
    # 都收、缺 padding 也补，别让一个纯编码约定差异把签名静默丢掉——那等于
    # 把本要修的 400 又放回来。真正的垃圾串仍被 validate=True 挡住。
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
                    # 同 turn text + function_call 并存的场景：``content``
                    # 是 _astream_genai_with_tools 写进来的 streamed_text_buffer，
                    # 表示模型在调工具前已经先吐给用户的话。这条 text 必须
                    # 跟 function_call parts 一起回放给 Gemini，否则下一轮
                    # generate_content_stream 看到的历史依然缺前半句，模型
                    # 还是会重复 / 改口（这正是上一条修复的对偶点）。
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
                            # thought_signature 必须跟着它那条 function_call
                            # 一起回放，否则 Gemini 思考模型下一轮直接 400。
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
                # Gemini FunctionResponse.name 必须与原 function_call.name 完全
                # 一致，否则 server 把这条 tool 结果当成无主消息丢弃。
                # 现在 ``_execute_and_append_openai_tool_calls`` 已写入 ``name``，
                # 但历史里若有旧条目（或外部传入的 messages）没带 name，仍要
                # 反查前面 assistant 的 tool_calls 找匹配 tool_call_id。绝不能
                # fallback 到 tool_call_id 自身——那是 "call_xxx" 格式不是函数名。
                fn_name = msg.get("name") or ""
                if not fn_name:
                    tcid = msg.get("tool_call_id") or ""
                    if tcid:
                        # 反向扫前面的 assistant tool_calls
                        for prev in reversed(messages[: messages.index(msg)] if msg in messages else []):
                            prev_calls = (prev.get("tool_calls") or []) if isinstance(prev, dict) else []
                            for tc in prev_calls:
                                if tc.get("id") == tcid:
                                    fn_name = (tc.get("function") or {}).get("name") or ""
                                    break
                            if fn_name:
                                break
                if not fn_name:
                    # 实在找不到 —— 一个不匹配原 function_call.name 的占位
                    # （比如 "unknown_tool"）只会让 Gemini 拿到一个永远找不到
                    # 对应 function_call 的孤儿 tool result，效果跟不发一样
                    # 还要白费一轮 token。直接跳过这条 malformed message。
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
    # 先做便宜的字符串判断：只有路由确实指向 native Gemini 时才去 import SDK。
    # 这样常规 OpenAI-compat 端点（含 greeting）构造 client 时不会触发 genai
    # 这条重 import；而真要走 genai 的用户，本来下一步就得用到它。
    bl = (base_url or "").lower()
    ml = (model or "").lower()
    native = (
        any(h in bl for h in _GENAI_NATIVE_BASE_URL_HINTS)
        or (not bl and any(h in ml for h in _GENAI_NATIVE_MODEL_HINTS))
    )
    if not native:
        return False
    # 路由判断只需"是否可用"这个布尔；尊重已知/被强制的标志（测试会 force
    # _GENAI_AVAILABLE 而不装 google-genai），只有真未知时才去付 lazy import。
    if _GENAI_AVAILABLE is None:
        return _ensure_genai()
    return bool(_GENAI_AVAILABLE)


class _GenaiMixin:
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
        if not _ensure_genai():
            raise _GenaiToolsUnsupported("google-genai SDK not importable")
        types = _genai_types

        # Lazy client init — re-use across turns.
        if self._genai_client is None:
            try:
                self._genai_client = _genai.Client(api_key=self.api_key or None)
            except Exception as e:
                raise _GenaiToolsUnsupported(f"genai.Client init failed: {e}") from e

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

        # 跨迭代累计真正执行过的 tool call 数（与 OpenAI 路径对偶）：0 与
        # 非 0 在封顶日志里是两种性质。
        executed_tool_calls = 0
        # 是否因"零执行轮且已经流过文本"提前跳出循环：封顶日志据此别谎称
        # 迭代被耗尽（cap=3 时我们可能在第 1 轮就跳出）。
        zero_exec_break = False
        for tool_iter in range(self.max_tool_iterations):
            system_instruction, contents = _genai_messages_to_contents(
                _slop_reduced_for_genai(messages)
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
                # ⚠️ 不要把所有异常都包成 _GenaiToolsUnsupported！
                # ``_astream_with_tools`` 的 except 分支会在捕到 _GenaiToolsUnsupported
                # 时永久翻 ``_genai_tools_unsupported=True``，导致 transient
                # 错误（429/5xx/网络抖动/auth 临时失败）让整个 session 后续都
                # 退化到 OpenAI-compat（且 OpenAI-compat 不支持 Gemini 工具）。
                # 只在错误消息里明确出现 tools 相关关键字时才认定是 SDK/模型
                # 不支持工具，其余异常直接 raise 给上层 ``except Exception``
                # —— 那条分支只本轮 fallback，下一轮还会重试 genai 路径。
                err_msg = str(e).lower()
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(
                        f"generate_content_stream rejected tools: {e}"
                    ) from e
                raise

            # Per-iteration accumulators.
            # list of (id, name, args_dict, raw_args_str, extra_content|None)
            collected_tool_calls: list = []
            # 是否见过任何 function_call part（含名字为空、随后被 drop 的）：
            # "进入 tool 轮"的判据必须用它而不是 collected——全被 drop 时
            # collected 为空，但这轮确实不是普通文本轮。
            saw_tool_call_fragment = False
            had_text = False
            # 本轮真的有非空文本 yield 给用户（≠ had_text：模型吐了 text
            # part 但被 leak filter 整段抑制时，用户其实什么都没看到）。
            # 零执行轮的"要不要再试一轮"只看这个——OpenAI 路径那边天然
            # 是这个语义（它的 streamed_text_buffer 累的就是过滤后的文本）。
            visible_text = False
            # 累积本轮已经 yield 给用户的 text，下面写 assistant 历史时
            # 用作 ``content`` —— 否则下一轮 LLM 看到 ``content=""`` 会
            # 不知道自己已经说过这部分话，可能重复或改口。
            streamed_text_buffer = ""
            usage_emitted = False
            # Empty-completion 诊断：最后一次见到的 finish_reason 和 prompt_feedback.
            # block_reason。Gemini 的 SAFETY / RECITATION / MAX_TOKENS 都在这两个
            # 字段里露出来；OpenAI-compat 那条路径丢这些信息，所以这里直接从 SDK
            # 原 chunk 读。
            iter_finish_reason: Optional[str] = None
            iter_block_reason: Optional[str] = None

            try:
                async for chunk in stream:
                    # prompt_feedback.block_reason：Gemini 整段 input 被 safety
                    # 拦掉时填这个，candidate 可能根本没出现。
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
                        # Skip thinking parts (Gemini 2.5+ thinking models) — but
                        # surface the boolean "is thinking" pulse first so the
                        # bubble shows on genai reasoning turns too (dual with the
                        # OpenAI-compat reasoning_content path). The thought TEXT
                        # itself is still dropped.
                        if getattr(part, "thought", False):
                            await self._notify_reasoning_active()
                            continue
                        text = getattr(part, "text", None) or ""
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None:
                            saw_tool_call_fragment = True
                            tc_name = (getattr(fn_call, "name", "") or "").strip()
                            if not tc_name:
                                # 与 OpenAI 路径对偶：空 name 的 function_call 是
                                # SDK glitch / 流提前中断的产物，写进 messages 会
                                # 让下一轮 generate_content_stream 收到无名
                                # function_call 直接 schema reject。drop + warning。
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
                            # thought_signature 挂在 Part 上（不在 FunctionCall
                            # 里），必须在这里就抓走存进历史：下一轮回放这条
                            # function_call 时 Gemini 思考模型要求原样带回。
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
                    usage_meta = getattr(chunk, "usage_metadata", None)
                    if usage_meta is not None and not usage_emitted:
                        try:
                            usage_dict = {
                                "prompt_tokens": getattr(usage_meta, "prompt_token_count", 0) or 0,
                                "completion_tokens": getattr(usage_meta, "candidates_token_count", 0) or 0,
                                "total_tokens": getattr(usage_meta, "total_token_count", 0) or 0,
                            }
                            # Empty-completion 诊断：把 prompt_tokens 落进 self，
                            # 给上层 stream_text 兜底警告引用，跟 OpenAI 路径对偶。
                            if usage_dict["prompt_tokens"]:
                                self._last_prompt_tokens = usage_dict["prompt_tokens"]
                            yield LLMStreamChunk(
                                content="",
                                usage_metadata=usage_dict,
                                response_metadata={"token_usage": usage_dict},
                            )
                            usage_emitted = True
                        except Exception as usage_err:
                            # usage 是可选 telemetry —— SDK 版本差异 / 字段缺失 /
                            # 字段类型不符都不该打断主文本流。只 debug-log 一下，
                            # 让用户回复继续。
                            logger.debug(
                                "genai usage_metadata emit skipped: %s",
                                usage_err,
                            )
            except Exception as e:
                err_msg = str(e).lower()
                # 与 generate_content_stream 调用本身的异常处理保持一致：
                # 只有错误消息明确含 "tool/function" + "not_support/unsupported/
                # invalid" 关键字组合时才认定 tools 不被 SDK / 模型支持，永久
                # 翻盘退到 OpenAI-compat。其他流中异常（含 "function call timeout"
                # 之类的 transient）原样 raise，让上层临时 fallback，下一轮再试。
                if (
                    ("tool" in err_msg or "function" in err_msg)
                    and ("not support" in err_msg or "not_support" in err_msg or "unsupported" in err_msg or "invalid" in err_msg)
                ):
                    raise _GenaiToolsUnsupported(f"genai stream rejected tools: {e}") from e
                raise

            # Empty-completion 诊断：落 self 字段 + 单独 INFO log。和 OpenAI 路径
            # 对偶；finish_reason / block_reason 两边都有可能填，谁先填就以谁为
            # 准（stream_text/prompt_ephemeral 的兜底 warning 会读这两个字段）。
            self._last_finish_reason = iter_finish_reason
            self._last_block_reason = iter_block_reason
            if not had_text and not collected_tool_calls:
                # getattr 防御同 OpenAI 路径；__new__ 绕过 __init__ 的测试桩不会崩。
                logger.info(
                    "OmniOfflineClient(genai): empty completion finish_reason=%s "
                    "block_reason=%s tool_iter=%d model=%s prompt_tokens=%s",
                    iter_finish_reason, iter_block_reason, tool_iter,
                    getattr(self, "model", None),
                    getattr(self, "_last_prompt_tokens", None),
                )

            if saw_tool_call_fragment and self.on_tool_call is not None:
                # ⚠️ 顺序不变量（与 OpenAI 路径 _tools.py 对偶，那边是先
                # finalize/yield tail、后 notify）：leak filter 扣住的那截
                # pre-tool 文本必须在 round-start 回调**之前**吐完。回调体
                # 是缓冲型调用方的 reply_chunks.clear()，先 notify 再 yield
                # tail 等于把被扣住的半截文本吐进一个刚清空的缓冲区，它照样
                # 会外发——leak filter 白扣了。
                #
                # 本轮已确认是 tool 轮（哪怕所有 function_call 都因空 name
                # 被 drop）：round-start 挂在 collected 非空的分支里会漏掉全
                # drop 的那条路径——那正是 pre-tool 文本会被当成整条回复外发
                # 的场景。
                #
                # tail 是否进 streamed_text_buffer 按分支分：collected 非空
                # 时本轮会写一条 assistant turn 入史，tail 属于那条 turn 的
                # content；全 drop 时没有 assistant turn，tail 只 yield 给
                # 用户。
                if tool_leak_filter is not None:
                    tail, event = tool_leak_filter.finalize()
                    if event:
                        log_tool_leak_filtered(event, provider=tool_leak_provider)
                    if tail:
                        if collected_tool_calls:
                            streamed_text_buffer += tail
                        # tail 是真的流给用户的（全 drop 的那条路径不进
                        # buffer，但用户照样看得见），零执行轮的判据要算上。
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
                # 零执行轮（function_call 分片全因空 name 被 drop）：messages
                # 一个字都没变，重来一轮不会有新信息，只会让模型把同样的
                # pre-tool 文本再流一遍给用户。cap=3 的主程序上实测是
                # "我查一下我查一下我查一下最终回答"。
                #
                # 所以只在**本轮什么都没流给用户**时才允许再试一轮（重试
                # 无用户可见代价，且 SDK 抖动确实可能下一轮就正常）；已经
                # 流过文本就直接跳出循环去 forced-finalize——#2597 引入这条
                # 分支的目的（不要早 return，要走到 forced-finalize + 封顶
                # 日志）由 break 完整保留，被放大的只是重试本身。
                #
                # 判据是 visible_text 而不是 had_text：模型只吐了 tool-call
                # 标记文本、被 leak filter 整段抑制时 had_text 也是真，可用户
                # 一个字都没看到——那种轮次白白放弃重试，下一轮本来可能给出
                # 合法调用（Codex P2）。
                #
                # 装了 round-start 回调的调用方（QQ 召回）是缓冲型的：那个
                # 回调体就是 reply_chunks.clear()，而它在上面几行已经跑过，
                # 本轮的 pre-tool 文本**根本没送到用户手里**。对它们"重放"
                # 无从谈起，break 只是白白丢掉一次本可恢复的工具调用——那正
                # 是召回轮，代价是这条回复没有记忆结果（Codex）。
                if visible_text and getattr(self, "on_tool_round_start", None) is None:
                    zero_exec_break = True
                    break
                continue
            if collected_tool_calls and self.on_tool_call is not None:
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
                # 把本轮已经流给用户的 text 一起写进历史。Gemini 在同一 turn
                # 里允许 text part 与 function_call part 并存；如果这里仍写
                # ``content=""``，下一轮 LLM 看到的上下文会缺掉前半句，模型
                # 会重复前缀或改口，最终持久化历史的顺序也跟真实生成顺序对不上。
                messages.append({
                    "role": "assistant",
                    # Symmetric with the OpenAI path: strip leaked <think> CoT
                    # before persisting the pre-tool text to history (no-op on
                    # clean replies / the genai path, which routes thought out).
                    "content": strip_thinking_segments(streamed_text_buffer),
                    "tool_calls": tool_calls_dict,
                })
                executed_tool_calls += len(collected_tool_calls)
                for i, (tc_id, tc_name, tc_args, tc_raw, _tc_extra) in enumerate(collected_tool_calls):
                    tool_call = ToolCall(
                        name=tc_name,
                        arguments=tc_args,
                        call_id=tc_id or f"call_{i}",
                        raw_arguments=tc_raw,
                    )
                    try:
                        with _suspend_dialog_slop():
                            result = await self.on_tool_call(tool_call)
                    except Exception as e:
                        logger.exception("OmniOfflineClient(genai): on_tool_call '%s' raised", tc_name)
                        result = ToolResult(
                            call_id=tool_call.call_id, name=tc_name,
                            output={"error": f"{type(e).__name__}: {e}"},
                            is_error=True, error_message=str(e),
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "name": tc_name,
                        "content": result.output_as_json_string(),
                    })
                # Sentinel：与 OpenAI 路径对偶，告诉上游 stream_text 把
                # final-segment buffer 清掉（pre-tool 文本已被持久化进
                # assistant turn 的 content 字段）。
                yield LLMStreamChunk(content="", tool_round_persisted=True)
                # Loop again to let the model produce a final answer.
                if not had_text:
                    continue
                # Edge case: model emitted text AND tool calls — text already
                # streamed to the user. Continue to next iter to give the
                # model a chance to follow up after seeing tool results.
                continue
            return
        if executed_tool_calls == 0:
            # 与 OpenAI 路径对偶：进过 tool 轮却一次都没执行成（function_call
            # 分片全部因空 name 被 drop），单列一条最值得排查的日志。
            logger.warning(
                "OmniOfflineClient(genai): %s with 0 executed tool calls "
                "(dropped nameless function_call parts); forcing final answer "
                "without tools",
                "zero-execution round after streaming text" if zero_exec_break
                else f"tool iteration cap {self.max_tool_iterations} reached",
            )
        elif zero_exec_break:
            # 前面几轮真的执行过 tool，最后一轮零执行且已流过文本：循环没被
            # 耗尽，别打成 runaway 封顶。
            logger.warning(
                "OmniOfflineClient(genai): zero-execution round after streaming "
                "text (dropped nameless function_call parts) with %d executed "
                "tool calls so far; forcing final answer without tools",
                executed_tool_calls,
            )
        elif self.max_tool_iterations == 1:
            # cap=1 的设计内单轮预算（QQ 插件）：正常召回轮必然耗尽循环，
            # 降为 INFO，与 OpenAI 路径对偶。
            logger.info(
                "OmniOfflineClient(genai): single tool round budget spent; "
                "forcing final answer without tools",
            )
        else:
            logger.warning(
                "OmniOfflineClient(genai): tool iteration cap %d reached; forcing final answer without tools",
                self.max_tool_iterations,
            )
        # Forced-finalize：与 OpenAI 路径对偶。去掉 tools 再生成一次，逼模型
        # 基于已积累的 tool 结果输出最终文本，避免封顶后整轮静默。
        # 不吞异常：与 OpenAI 路径一致，让 SDK 调用失败原样向上抛，由 stream_text /
        # prompt_ephemeral 现成的 retry / 状态上报 / response_discarded 清泡泡逻辑
        # 接管。若在这里 try/except 成 warning，就把真实失败伪装成"空回复"，弱模型
        # 超限后反而可能重回静音态，与本兜底目标冲突。
        final_cfg_kw = {k: v for k, v in gen_config_kw.items() if k != "tools"}
        final_system_instruction, final_contents = _genai_messages_to_contents(
            _slop_reduced_for_genai(messages)
        )
        if final_system_instruction:
            final_cfg_kw["system_instruction"] = final_system_instruction
        final_config = types.GenerateContentConfig(**final_cfg_kw)
        final_stream = await self._genai_client.aio.models.generate_content_stream(
            model=self.model,
            contents=final_contents,
            config=final_config,
        )
        final_finish_reason: Optional[str] = None
        final_block_reason: Optional[str] = None
        final_prompt_tokens: Optional[int] = None
        final_had_text = False
        async for chunk in final_stream:
            # 与常规 genai 分支对偶地采集空回复诊断：block_reason / finish_reason /
            # prompt_tokens。否则若 forced-finalize 也被 safety / recitation /
            # max-tokens 挡住而无文本，上层只能引用上一轮 tool-iteration 的过期
            # finish_reason，诊断失真。
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
        # 统一回填本次 forced-finalize 自己的诊断值（含 prompt_tokens）。prompt_tokens
        # 走局部变量、流结束后无条件回填：若这次被挡住/没给 usage，写回 None 而非沿用
        # 上一轮 tool-iteration 的旧值，避免 INFO log / 上层 LLM_NO_RESPONSE 诊断串台。
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
