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
"""OpenAI-compatible chat client."""

from __future__ import annotations
import weakref
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    pass

from .cache_control import _inject_cache_control
from .lifecycle import (
    _active_character, _dialog_slop_lang, _close_async_openai_client_from_sync_best_effort,
    _close_chat_openai_clients_best_effort, _get_default_ssl_context,
    _substitute_character_placeholders,
)
from .messages import LLMResponse, LLMStreamChunk, ToolCallAggregate, _normalize_messages
from .thinking import strip_thinking_segments

class ChatOpenAI:
    """OpenAI-compatible chat client with streaming, invoke, and resource management."""

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        streaming: bool = False,
        max_retries: int = 2,
        extra_body: dict | None = None,
        max_completion_tokens: int | None = None,
        max_tokens: int | None = None,
        model_kwargs: dict | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
        default_headers: dict | None = None,
        enable_cache_control: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        **_kwargs: Any,
    ):
        self.model = model
        self.base_url = base_url
        # Tool-calling defaults baked into instance; per-call ``overrides``
        # in ``_params()`` can still substitute a different list (e.g. when
        # the caller wants to suppress tools mid-conversation).
        self.tools = list(tools) if tools else None
        self.tool_choice = tool_choice
        # 项目级硬性约定：不再下发 temperature。default=None → 不写进请求体，
        # 由模型端自定。o1/o3/gpt-5-thinking/Claude extended-thinking 等拒绝该
        # 参数的模型可以直通；普通模型也走它们自己的默认值，避免不同 task 之间
        # 因为温度数值漂移引入难复现的回归。callers 不应再传 temperature=
        # （CI 由 scripts/check_no_temperature.py 守门）。
        self.temperature = temperature
        self.extra_body: dict = extra_body or {}
        self.max_completion_tokens = max_completion_tokens
        self.max_tokens = max_tokens
        # When True, _params() stamps a body-level Anthropic-style
        # cache_control marker onto the cache breakpoint message. Set by
        # create_chat_llm via config.providers.get_cache_kwargs for providers
        # with requires_body_flag (Anthropic direct / Anthropic-compat). Header
        # cache providers (DashScope) leave this False — their header does the job.
        self.enable_cache_control = enable_cache_control

        if model_kwargs and "extra_body" in model_kwargs:
            self.extra_body = {**self.extra_body, **model_kwargs["extra_body"]}

        _api_key = api_key or "sk-placeholder"
        _timeout = timeout or request_timeout
        client_kw: dict[str, Any] = dict(base_url=base_url, api_key=_api_key, max_retries=max_retries)
        if _timeout is not None:
            client_kw["timeout"] = _timeout
        if default_headers:
            client_kw["default_headers"] = default_headers
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI

        ssl_context = _get_default_ssl_context()
        client_kw["http_client"] = DefaultAsyncHttpxClient(verify=ssl_context)
        self._aclient = AsyncOpenAI(**client_kw)
        client_kw["http_client"] = DefaultHttpxClient(verify=ssl_context)
        self._client = OpenAI(**client_kw)
        self._client_finalizer = weakref.finalize(
            self,
            _close_chat_openai_clients_best_effort,
            self._client,
            self._aclient,
        )

    def _is_anthropic(self) -> bool:
        return bool(self.base_url) and "api.anthropic.com" in str(self.base_url)

    def _params(self, messages: Any, *, stream: bool = False, **overrides: Any) -> dict:
        """Build the request body. ``overrides`` lets per-call invokers
        substitute ``max_completion_tokens`` / ``max_tokens`` / ``extra_body``
        (and any other SDK-accepted kwarg) without mutating the instance —
        critical when a single ChatOpenAI is shared across concurrent code
        paths (e.g. background ping vs. main task in computer_use)."""
        p: dict[str, Any] = {
            "model": self.model,
            "messages": _normalize_messages(messages),
            "stream": stream,
        }
        # 项目级约定：default=None → 不写 temperature 进请求体。本分支保留是
        # 为了向后兼容显式 `temperature=0` 这类 case（0.0 合法，所以判 None 而
        # 不是 truthy），实际 callers 不应再传该参数。
        if self.temperature is not None:
            p["temperature"] = self.temperature
        # Provider-aware routing of token-limit field:
        #   Anthropic SDK / Anthropic-compat endpoints → max_tokens
        #   Everyone else (OpenAI / OpenAI-compat / Gemini-compat / etc.) → max_completion_tokens
        # Per-call overrides take precedence over instance attrs so concurrent
        # callers on the same client don't corrupt each other's budgets.
        token_limit = overrides.pop("max_completion_tokens", None)
        if token_limit is None:
            token_limit = overrides.pop("max_tokens", None)
        if token_limit is None:
            token_limit = self.max_completion_tokens or self.max_tokens
        limit_field: str | None = None
        limit_value: int | None = None
        if token_limit:
            limit_field = "max_tokens" if self._is_anthropic() else "max_completion_tokens"
            limit_value = int(token_limit)
            p[limit_field] = limit_value
        extra_body = overrides.pop("extra_body", self.extra_body)
        if extra_body:
            p["extra_body"] = extra_body
        # Tool calling: per-call overrides take priority over instance default
        # so callers can disable tools (``tools=[]`` 或 ``tools=None``) for
        # special turns（如 prompt_ephemeral 中明确不要工具）。
        tools = overrides.pop("tools", self.tools)
        if tools:
            p["tools"] = tools
            tool_choice = overrides.pop("tool_choice", self.tool_choice)
            if tool_choice is not None:
                p["tool_choice"] = tool_choice
        else:
            # Don't leak tool_choice without tools — some endpoints 400 on it.
            overrides.pop("tool_choice", None)
        if stream:
            p["stream_options"] = {"include_usage": True}
        # Anything else the caller passed (e.g. timeout, logit_bias) goes
        # straight through to the SDK call.
        p.update(overrides)

        # Resolve {MASTER_NAME}/{LANLAN_NAME} placeholders that arrived
        # from plugin-supplied prompt fragments (cue text / nudge prompt /
        # plugin descriptions etc.) before the leak check and wire send.
        # The dialog LLM path resolves these in
        # ``main_logic.core._render_callback_inner_item``; brain pipeline
        # LLM calls don't go through that renderer, so without this step
        # the literal placeholder leaks all the way through.
        # ``set_active_character`` sets the contextvar at brain entry;
        # this reads + substitutes. No-op if no character is active
        # (e.g. callers outside the brain pipeline) — the leak check
        # below will then fire its WARNING the same as before.
        active = _active_character.get()
        if active is not None:
            master, lanlan = active
            if master or lanlan:
                p["messages"] = _substitute_character_placeholders(
                    p["messages"], master, lanlan
                )

        slop_lang = _dialog_slop_lang.get()
        if slop_lang:
            try:
                from utils.slop_filter import apply_slop_reduction
                p["messages"] = apply_slop_reduction(p["messages"], slop_lang)
            except Exception:
                # Prompt-only rewriting is best effort and must not break an LLM call.
                pass

        # Body-level cache flag: stamp an Anthropic-style cache_control marker
        # onto the cache breakpoint. Gated on the provider opting in via
        # requires_body_flag (no live provider does today, so this is a no-op
        # for current traffic) — header-cache providers leave this False. Runs
        # after character substitution so the marked text is already resolved,
        # and before the leak check (which transparently scans list-of-parts
        # content) so a promoted string still gets audited for placeholder leaks.
        if self.enable_cache_control:
            p["messages"] = _inject_cache_control(p["messages"])

        # Catch prompt-template leaks: literal {placeholder} that should have
        # been .format()-ed before reaching the wire. See
        # utils/llm_prompt_leak_check.py for the rationale and severity
        # contract (raise in tests, warn in prod).
        try:
            from utils import llm_prompt_leak_check
            llm_prompt_leak_check.check_messages_for_leaks(
                p["messages"], context=f"ChatOpenAI._params model={self.model}"
            )
        except AssertionError:
            raise
        except Exception:
            # Detector bugs must never break the LLM call itself.
            pass

        # TEMPORARY: prompt audit log (env NEKO_LLM_PROMPT_AUDIT=1). Remove with
        # utils/llm_prompt_audit.py once budget tuning is done.
        try:
            from utils import llm_prompt_audit
            if llm_prompt_audit.is_enabled():
                llm_prompt_audit.record_llm_request(
                    model=self.model,
                    base_url=self.base_url,
                    params=p,
                    field_name=limit_field,
                    field_value=limit_value,
                )
        except Exception:
            # Audit hook is debug-only and must never bubble up — a broken
            # logger should not break LLM calls. Intentionally swallowed;
            # this whole try/except disappears when the audit module is
            # removed.
            pass
        return p

    # --- sync / async invoke ---

    async def ainvoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """``overrides`` (e.g. ``max_completion_tokens=1100``) flow through
        ``_params()`` so concurrent callers on the same client don't
        clobber each other's budgets. See ``ainvoke_raw`` for details."""
        resp = await self._aclient.chat.completions.create(**self._params(messages, **overrides))
        # 防御性读取：部分上游（如 free-agent-model）会返回 choices 非空但
        # message=None 的合法响应，直接 .message.content 会 NoneType 崩溃。
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = strip_thinking_segments(getattr(msg, "content", None))
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(
            content=content,
            response_metadata={
                "token_usage": usage_dict,
                "finish_reason": getattr(choice, "finish_reason", None) if choice else None,
            },
        )

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """Sync twin of ``ainvoke``. See its docstring for ``overrides``."""
        resp = self._client.chat.completions.create(**self._params(messages, **overrides))
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = strip_thinking_segments(getattr(msg, "content", None))
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(
            content=content,
            response_metadata={
                "token_usage": usage_dict,
                "finish_reason": getattr(choice, "finish_reason", None) if choice else None,
            },
        )

    # --- raw-resp invoke (for callers needing reasoning_content / raw choices) ---

    async def ainvoke_raw(self, messages: Any, **overrides: Any):
        """Async invoke that returns the underlying SDK ChatCompletion
        response. Parameter routing still flows through `_params()`
        (Anthropic → max_tokens, others → max_completion_tokens). Use only
        when you need fields beyond `LLMResponse` (e.g. thinking models'
        ``reasoning_content``); prefer `ainvoke` otherwise.

        ``overrides`` lets the caller provide per-call values for
        ``max_completion_tokens`` / ``max_tokens`` / ``extra_body`` / SDK
        kwargs like ``timeout`` without touching ``self.*`` — required when
        a single client is shared across concurrent code paths."""
        return await self._aclient.chat.completions.create(
            **self._params(messages, **overrides)
        )

    def invoke_raw(self, messages: Any, **overrides: Any):
        """Sync twin of `ainvoke_raw`. See its docstring for ``overrides``."""
        return self._client.chat.completions.create(
            **self._params(messages, **overrides)
        )

    # --- async streaming ---

    async def astream(self, messages: Any, **overrides: Any) -> AsyncIterator[LLMStreamChunk]:
        """Stream chunks. Yields:

        - text-content chunks (``content`` non-empty)
        - tool_calls fragments (``tool_call_deltas`` non-None) — caller
          accumulates ``[].function.arguments`` per ``index`` until the
          chunk with ``finish_reason == "tool_calls"`` arrives, then runs
          the tools and appends a fresh assistant + tool turn before
          calling ``astream`` again.
        - terminal usage chunk
        """
        stream = await self._aclient.chat.completions.create(
            **self._params(messages, stream=True, **overrides)
        )
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None
            content = delta.content if delta and delta.content else ""
            tool_call_deltas: list[dict] | None = None
            if delta is not None:
                raw_tool_calls = getattr(delta, "tool_calls", None) or []
                if raw_tool_calls:
                    tool_call_deltas = []
                    for tc in raw_tool_calls:
                        # SDK objects → plain dicts. ``index`` ties fragments
                        # of the same call together across chunks.
                        fn = getattr(tc, "function", None)
                        tool_call_deltas.append({
                            "index": getattr(tc, "index", 0),
                            "id": getattr(tc, "id", "") or "",
                            "type": getattr(tc, "type", "function") or "function",
                            "function": {
                                "name": getattr(fn, "name", "") if fn else "",
                                "arguments": getattr(fn, "arguments", "") if fn else "",
                            },
                        })
            # Thinking 模型把推理链放在非标准的 ``delta.reasoning_content``
            # 字段（openai-python 的 BaseModel extra=allow，未知字段照样可
            # getattr）。普通端点没有这个字段，getattr 返回 None。
            reasoning_content = (
                getattr(delta, "reasoning_content", None) if delta else None
            )
            finish_reason = getattr(choice, "finish_reason", None) if choice else None
            if content or tool_call_deltas or finish_reason or reasoning_content:
                yield LLMStreamChunk(
                    content=content,
                    tool_call_deltas=tool_call_deltas,
                    finish_reason=finish_reason,
                    reasoning_content=reasoning_content,
                )
            # Terminal chunk with usage info (stream_options={"include_usage": True})
            if chunk.usage is not None:
                usage_dict = chunk.usage.model_dump()
                yield LLMStreamChunk(
                    content="",
                    usage_metadata=usage_dict,
                    response_metadata={"token_usage": usage_dict},
                )

    @staticmethod
    def collect_tool_calls(deltas_per_chunk: list[list[dict] | None]) -> list[ToolCallAggregate]:
        """Combine per-chunk tool_call deltas (in arrival order) into the
        final list of completed tool calls. Caller passes the
        ``tool_call_deltas`` field of every chunk in the order yielded.

        Multiple parallel calls are kept distinct via ``index`` (the OpenAI
        Chat Completions schema guarantees one ``index`` per call).

        ⚠️ Aggregation slots with an empty ``name`` are dropped — broken fragments
        produced by SDK bugs / prematurely interrupted streams / some small models.
        Written into the ``tool_calls`` history unfiltered, the next round's call
        would be rejected by the server as schema invalid. Dropping here lets the
        upper layer take the normal "the model called no tool this round" branch.
        """
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        merged: dict[int, dict] = {}
        for fragments in deltas_per_chunk:
            if not fragments:
                continue
            for frag in fragments:
                idx = int(frag.get("index", 0))
                slot = merged.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if frag.get("id"):
                    slot["id"] = frag["id"]
                fn = frag.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        out: list[ToolCallAggregate] = []
        for idx in sorted(merged.keys()):
            slot = merged[idx]
            if not slot["name"]:
                _logger.warning(
                    "ChatOpenAI.collect_tool_calls: dropping fragment with empty name "
                    "(idx=%d, id=%r) — likely a streaming SDK glitch",
                    idx, slot.get("id"),
                )
                continue
            out.append(ToolCallAggregate(
                index=idx,
                id=slot["id"],
                name=slot["name"],
                arguments=slot["arguments"],
            ))
        return out

    # --- resource management ---

    async def aclose(self) -> None:
        """Close underlying httpx clients (async path)."""
        await self._aclient.close()
        self._client.close()
        self._client_finalizer.detach()

    def close(self) -> None:
        """Close underlying httpx clients (sync path)."""
        self._client.close()
        _close_async_openai_client_from_sync_best_effort(self._aclient)
        self._client_finalizer.detach()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
