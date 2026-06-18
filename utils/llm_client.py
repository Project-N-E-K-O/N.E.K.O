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

"""Lightweight LLM client layer using the ``openai`` SDK directly.

Provides:
  - Message classes (SystemMessage, HumanMessage, AIMessage) compatible with
    the old langchain interface
  - ChatOpenAI wrapper with streaming, invoke, and resource management
  - ``create_chat_llm()`` factory that auto-resolves provider-specific config
  - ``create_chat_llm_async()`` async factory for offloaded client construction
  - Serialization helpers (messages_to_dict, messages_from_dict, convert_to_messages)
  - OpenAIEmbeddings / SQLChatMessageHistory for memory subsystem
"""
from __future__ import annotations

import asyncio
import contextvars
import json as _json
import os
import re
import ssl
import threading
import weakref
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Union

from openai import AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, OpenAI


# ────────────────────────────────────────────────────────────────
# Reasoning-trace stripping (non-streaming defensive cleanup)
# ────────────────────────────────────────────────────────────────
# Well-formed <think>...</think> / <thinking>...</thinking> blocks.
_THINK_PAIRED_RE = re.compile(r"<think(?:ing)?\s*>.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
# A *dangling* close tag with no matching open. This is the Qwen3.5/3.6
# OpenAI-compat leak shape: unlike qwen3-vl-* (which route reasoning to the
# ``reasoning_content`` field), the 3.5/3.6 hybrid models never populate
# ``reasoning_content`` — the whole chain-of-thought lands in ``content`` with
# only a lone ``</think>`` (implicit open) separating it from the real answer.
# A paired-tag regex alone can't catch this; we strip everything up to and
# including the first unmatched close tag.
_THINK_DANGLING_CLOSE_RE = re.compile(r"^.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)
_THINK_ANY_CLOSE_RE = re.compile(r"</think(?:ing)?\s*>", re.IGNORECASE)


def strip_thinking_segments(text: str | None) -> str:
    """Remove leaked chain-of-thought from a *non-streaming* model reply.

    Handles two shapes:
      1. Well-formed ``<think>...</think>`` blocks (any count).
      2. Qwen3.5/3.6 leak: reasoning dumped into ``content`` with only a
         dangling ``</think>`` (no opening tag) before the answer.

    Conservative — only acts when a think tag is present, so clean replies
    (qwen3-vl-*, gpt, claude, etc.) pass through untouched. Streaming is *not*
    covered here on purpose: when the chain-of-thought arrives token-by-token
    in ``delta.content`` with no delimiter there's nothing reliable to strip.
    """
    if not text:
        return text or ""
    s = str(text)
    # 1) drop well-formed blocks first
    s = _THINK_PAIRED_RE.sub("", s)
    # 2) any close tag still present is unmatched → preceding text is thinking
    if _THINK_ANY_CLOSE_RE.search(s):
        s = _THINK_DANGLING_CLOSE_RE.sub("", s, count=1)
    return s.strip()


# ────────────────────────────────────────────────────────────────
# Active-character context — used by ChatOpenAI._params to substitute
# ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders that originated from
# plugin-supplied prompt fragments (the dialog LLM path already substitutes
# at ``main_logic.core._render_callback_inner_item``; brain pipeline LLM
# calls — analyzer / plugin LLM — used to leak the literal placeholder
# all the way to the wire, surfacing as a ``llm_prompt_leak_check``
# WARNING. Setting this contextvar at the brain entry point bridges the
# gap.) ContextVar is async-safe — values inherit through ``asyncio.gather``
# children automatically and don't bleed across unrelated tasks.
# ────────────────────────────────────────────────────────────────

_active_character: "contextvars.ContextVar[tuple[str, str] | None]" = contextvars.ContextVar(
    "_neko_active_character_master_lanlan", default=None
)

_DEFAULT_SSL_CONTEXT: ssl.SSLContext | None = None
_DEFAULT_SSL_CONTEXT_LOCK = threading.Lock()
_PENDING_CLIENT_CLOSE_TASKS: set[asyncio.Task[None]] = set()


def _create_httpx_default_ssl_context() -> ssl.SSLContext:
    """Create the same default verify context httpx uses without its deprecated helper."""
    import certifi

    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
    if os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context(capath=os.environ["SSL_CERT_DIR"])
    return ssl.create_default_context(cafile=certifi.where())


def _get_default_ssl_context() -> ssl.SSLContext:
    """Return the process-wide default TLS context for short-lived LLM clients."""
    global _DEFAULT_SSL_CONTEXT
    if _DEFAULT_SSL_CONTEXT is not None:
        return _DEFAULT_SSL_CONTEXT

    with _DEFAULT_SSL_CONTEXT_LOCK:
        if _DEFAULT_SSL_CONTEXT is None:
            _DEFAULT_SSL_CONTEXT = _create_httpx_default_ssl_context()
        return _DEFAULT_SSL_CONTEXT


async def _close_async_openai_client_best_effort(aclient: AsyncOpenAI) -> None:
    try:
        await aclient.close()
    except Exception:
        # Finalizer-triggered cleanup must never surface async close failures.
        pass


def _schedule_async_openai_client_close_best_effort(
    aclient: AsyncOpenAI,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    close_coro = _close_async_openai_client_best_effort(aclient)
    try:
        task = loop.create_task(close_coro)
    except Exception:
        close_coro.close()
        # The loop may be closing; explicit aclose() remains the deterministic path.
    else:
        _PENDING_CLIENT_CLOSE_TASKS.add(task)
        task.add_done_callback(_PENDING_CLIENT_CLOSE_TASKS.discard)


def _close_async_openai_client_from_sync_best_effort(aclient: AsyncOpenAI) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(_close_async_openai_client_best_effort(aclient))
        except Exception:
            pass
        return
    _schedule_async_openai_client_close_best_effort(aclient, loop=loop)


def _close_chat_openai_clients_best_effort(client: OpenAI, aclient: AsyncOpenAI) -> None:
    try:
        client.close()
    except Exception:
        # Destructors/finalizers must never raise during GC or interpreter shutdown.
        pass
    _schedule_async_openai_client_close_best_effort(aclient)


def set_active_character(master_name: str, lanlan_name: str) -> "contextvars.Token":
    """Set ``(master_name, lanlan_name)`` on the active async context so
    subsequent ``ChatOpenAI._params`` invocations on this task substitute
    ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders in messages before
    the leak check + wire send. Returns a token; pass to
    ``reset_active_character`` to restore the previous value.

    Empty strings are tolerated (skipped at substitution time) so callers
    that only know one of the two can still set partial context.
    """
    return _active_character.set((master_name or "", lanlan_name or ""))


def reset_active_character(token: "contextvars.Token") -> None:
    _active_character.reset(token)


def _substitute_character_placeholders(messages: list, master: str, lanlan: str) -> list:
    """Return a NEW messages list with ``{MASTER_NAME}`` / ``{LANLAN_NAME}``
    replaced in every text-bearing field. Defensive copy — does not
    mutate the input. ``str.replace`` (not ``.format``) so JSON fragments
    or other braces in user content don't trigger KeyError.
    """
    if not master and not lanlan:
        return messages

    def _swap(text: str) -> str:
        if master:
            text = text.replace("{MASTER_NAME}", master)
        if lanlan:
            text = text.replace("{LANLAN_NAME}", lanlan)
        return text

    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, str):
            new_content: Any = _swap(content)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    new_parts.append({**part, "text": _swap(part["text"])})
                else:
                    new_parts.append(part)
            new_content = new_parts
        else:
            new_content = content
        out.append({**m, "content": new_content})
    return out

# ────────────────────────────────────────────────────────────────
# Message classes
# ────────────────────────────────────────────────────────────────

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "system": "system"}
_ROLE_TO_TYPE = {"user": "human", "assistant": "ai", "system": "system"}


@dataclass
class BaseMessage:
    content: Any
    type: str = ""

    @property
    def role(self) -> str:
        return _TYPE_TO_ROLE.get(self.type, self.type)

    def to_openai(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class SystemMessage(BaseMessage):
    type: str = field(default="system", init=False)


@dataclass
class HumanMessage(BaseMessage):
    type: str = field(default="human", init=False)


@dataclass
class AIMessage(BaseMessage):
    type: str = field(default="ai", init=False)


_TYPE_CLS: dict[str, type[BaseMessage]] = {
    "human": HumanMessage,
    "ai": AIMessage,
    "system": SystemMessage,
}
_ROLE_CLS: dict[str, type[BaseMessage]] = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}

# ────────────────────────────────────────────────────────────────
# Serialization helpers
# ────────────────────────────────────────────────────────────────


def messages_to_dict(messages: list) -> list[dict]:
    """Serialize message objects to the on-disk format.

    Output format per element::

        {"type": "human", "data": {"content": "hello"}}

    Backward-compatible with files written by the old langchain serializer.
    """
    result: list[dict] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            result.append({"type": msg.type, "data": {"content": msg.content}})
        elif isinstance(msg, dict):
            if "type" in msg and "data" in msg:
                result.append(msg)
            elif "role" in msg:
                t = _ROLE_TO_TYPE.get(msg["role"], msg["role"])
                result.append({"type": t, "data": {"content": msg.get("content", "")}})
            else:
                result.append(msg)
        else:
            t = getattr(msg, "type", "human")
            result.append({"type": t, "data": {"content": getattr(msg, "content", str(msg))}})
    return result


def messages_from_dict(dicts: list[dict]) -> list[BaseMessage]:
    """Deserialize on-disk dicts back to message objects.

    Accepts both legacy format (``type``/``data``) and OpenAI format
    (``role``/``content``) for robustness.
    """
    result: list[BaseMessage] = []
    for d in dicts:
        if "data" in d and "type" in d:
            cls = _TYPE_CLS.get(d["type"], HumanMessage)
            content = d["data"].get("content", "") if isinstance(d["data"], dict) else d["data"]
            result.append(cls(content=content))
        elif "role" in d and "content" in d:
            cls = _ROLE_CLS.get(d["role"], HumanMessage)
            result.append(cls(content=d["content"]))
        else:
            result.append(HumanMessage(content=str(d)))
    return result


def convert_to_messages(data: Any) -> list[BaseMessage]:
    """Convert various serialized formats to message objects.

    Handles the OpenAI dict format sent over HTTP from cross_server
    as well as the legacy on-disk format.
    """
    if isinstance(data, list):
        return messages_from_dict(data)
    return []


# ────────────────────────────────────────────────────────────────
# LLM response wrappers
# ────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    response_metadata: dict = field(default_factory=dict)


@dataclass
class LLMStreamChunk:
    content: str
    usage_metadata: dict | None = None
    response_metadata: dict | None = None
    # Streamed tool_calls fragment (OpenAI Chat Completions schema):
    # ``[{"index": 0, "id": "...", "type": "function",
    #     "function": {"name": "...", "arguments": "<json fragment>"}}]``
    # Multiple chunks may carry the same ``index`` — callers must
    # accumulate ``function.arguments`` strings before JSON-parsing.
    tool_call_deltas: list[dict] | None = None
    # Reason the model finished this stream segment: ``"stop"`` / ``"length"`` /
    # ``"tool_calls"`` / ``"content_filter"`` / None. ``"tool_calls"`` signals
    # the caller should run the tool then continue the conversation.
    finish_reason: str | None = None
    # Thinking-mode 模型（DeepSeek-R 系 / Qwen / GLM thinking 等 OpenAI-compat
    # 端点）在 ``delta`` 里单独流出的推理链文本。普通对话用不到，但 **多轮
    # tool calling** 时这些 provider 要求把发起 tool_calls 那条 assistant 消息的
    # ``reasoning_content`` 原样回填，否则下一轮报 400 "The `reasoning_content`
    # in the thinking mode must be passed back to the API."。tool 循环靠累积此
    # 字段把它写回 assistant 历史。
    reasoning_content: str | None = None
    # ``OmniOfflineClient`` tool-loop sentinel：``_astream_*_with_tools`` 在
    # 把当前 tool 轮（assistant tool_calls + tool result）inline 写进 history
    # 后会 yield 一个 ``LLMStreamChunk(content="", tool_round_persisted=True)``。
    # ``stream_text`` 看到就把 final-segment buffer 清掉，避免之后
    # ``_conversation_history.append(AIMessage(content=...))`` 把 pre-tool
    # 文本第二次写进 history（pre-tool 文本已经在 ``assistant.tool_calls.content``
    # 里了）。
    tool_round_persisted: bool = False


@dataclass
class ToolCallAggregate:
    """Fully-assembled tool call after streaming finished.

    Built by ``ChatOpenAI.collect_tool_calls()`` from the per-index
    fragments yielded across ``LLMStreamChunk.tool_call_deltas``."""

    index: int
    id: str
    name: str
    arguments: str  # JSON string; caller decides whether to ``json.loads``


# ────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────

def _normalize_messages(messages: Any) -> list[dict]:
    """Convert various message formats to openai-compatible dicts.

    ``BaseMessage`` subclasses pass their ``tool_calls`` / ``tool_call_id`` fields (when
    present) through to OpenAI Chat Completions — both fields are required when
    backfilling multi-turn tool-calling conversations: the assistant role carries
    tool_calls + the tool role carries tool_call_id."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    out: list[dict] = []
    for msg in messages:
        if isinstance(msg, dict):
            if "role" in msg:
                out.append(msg)
            elif "type" in msg and "data" in msg:
                role = _TYPE_TO_ROLE.get(msg["type"], msg["type"])
                content = msg["data"].get("content", "") if isinstance(msg["data"], dict) else msg["data"]
                out.append({"role": role, "content": content})
            else:
                out.append(msg)
        elif isinstance(msg, BaseMessage):
            base = msg.to_openai()
            # Tool-calling round-trip fields — only attach when present so we
            # don't pollute non-tool conversations with empty arrays.
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                base["tool_calls"] = tool_calls
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                base["tool_call_id"] = tool_call_id
            out.append(base)
        elif hasattr(msg, "type") and hasattr(msg, "content"):
            role = _TYPE_TO_ROLE.get(msg.type, msg.type)
            out.append({"role": role, "content": msg.content})
        else:
            out.append({"role": "user", "content": str(msg)})
    return out


# ────────────────────────────────────────────────────────────────
# ChatOpenAI — lightweight OpenAI-compatible LLM client
# ────────────────────────────────────────────────────────────────

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
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """Sync twin of ``ainvoke``. See its docstring for ``overrides``."""
        resp = self._client.chat.completions.create(**self._params(messages, **overrides))
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None
        content = strip_thinking_segments(getattr(msg, "content", None))
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(content=content, response_metadata={"token_usage": usage_dict})

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


# ────────────────────────────────────────────────────────────────
# create_chat_llm — factory with automatic provider config
# ────────────────────────────────────────────────────────────────

_SENTINEL = object()


def create_chat_llm(
    model: str,
    base_url: str | None,
    api_key: str | None,
    *,
    temperature: float | None = None,
    streaming: bool = False,
    max_retries: int = 2,
    max_completion_tokens: int | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    extra_body: Any = _SENTINEL,
    model_kwargs: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    **kw: Any,
) -> ChatOpenAI:
    """Create a ChatOpenAI with automatic provider-specific configuration.

    Provider cache headers and extra_body (thinking-disable etc.) are resolved
    automatically from ``config.providers``.  Pass ``extra_body=None`` to
    explicitly skip the auto-resolved extra_body (e.g. when thinking should
    remain enabled).

    Args:
        model: Model name (e.g. "qwen-flash", "gpt-4.1-mini").
        base_url: Provider API base URL.
        api_key: API key.
        extra_body: Override auto-resolved extra_body.  ``_SENTINEL`` (default)
            means "auto-resolve from model name"; ``None`` means "no extra_body".
        **kw: Forwarded to ChatOpenAI.__init__.
    """
    from config.providers import get_cache_kwargs, get_extra_body

    cache_kw = get_cache_kwargs(base_url)

    if extra_body is _SENTINEL:
        resolved = get_extra_body(model)
        extra_body = resolved or None

    # Anthropic API 使用 x-api-key 而非 Bearer token，需要注入专用 headers
    _api_key = api_key
    if base_url and "api.anthropic.com" in base_url:
        anthropic_headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
        # 合并 cache_kw / kw / anthropic 的 default_headers，避免重复关键字
        merged_headers = {
            **cache_kw.pop("default_headers", {}),
            **kw.pop("default_headers", {}),
            **anthropic_headers,
        }
        kw["default_headers"] = merged_headers
        # OpenAI SDK 要求 api_key 非空，给占位值（实际鉴权走 x-api-key header）
        _api_key = "anthropic-via-header"

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=_api_key,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
        max_completion_tokens=max_completion_tokens,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_body=extra_body,
        model_kwargs=model_kwargs,
        tools=tools,
        tool_choice=tool_choice,
        **cache_kw,
        **kw,
    )


async def create_chat_llm_async(*args: Any, **kwargs: Any) -> ChatOpenAI:
    """Create a ChatOpenAI without blocking the running event loop."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(asyncio.to_thread(create_chat_llm, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(
            lambda done: loop.create_task(_close_cancelled_chat_llm_result(done))
        )
        raise


async def _close_cancelled_chat_llm_result(task: asyncio.Task[ChatOpenAI]) -> None:
    try:
        llm = task.result()
    except Exception:
        return
    try:
        await llm.aclose()
    except Exception:
        return


# ────────────────────────────────────────────────────────────────
# OpenAIEmbeddings
# ────────────────────────────────────────────────────────────────

class OpenAIEmbeddings:
    """Lightweight OpenAI embeddings client."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "",
        api_key: str | None = None,
        **_kwargs: Any,
    ):
        self.model = model
        _api_key = api_key or "sk-placeholder"
        self._client = OpenAI(base_url=base_url, api_key=_api_key)
        self._aclient = AsyncOpenAI(base_url=base_url, api_key=_api_key)

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    async def aembed_query(self, text: str) -> list[float]:
        resp = await self._aclient.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding


# ────────────────────────────────────────────────────────────────
# SQLChatMessageHistory
# ────────────────────────────────────────────────────────────────

class SQLChatMessageHistory:
    """Minimal SQLite message store for memory/timeindex.py.

    Table schema::

        id          INTEGER PRIMARY KEY AUTOINCREMENT
        session_id  TEXT
        message     TEXT   -- JSON-serialized {"type": ..., "data": {"content": ...}}
    """

    _engine_cache: dict = {}

    def __init__(self, connection_string: str, session_id: str, table_name: str = "message_store"):
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine

        self.session_id = session_id
        self.table_name = table_name

        if connection_string not in self.__class__._engine_cache:
            self.__class__._engine_cache[connection_string] = create_engine(connection_string)
        self._engine = self.__class__._engine_cache[connection_string]

        metadata = MetaData()
        self._table = Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("session_id", String),
            Column("message", Text),
        )
        metadata.create_all(self._engine)

    def _serialize(self, message: Any) -> str:
        if isinstance(message, BaseMessage):
            return _json.dumps({"type": message.type, "data": {"content": message.content}}, ensure_ascii=False)
        if isinstance(message, dict):
            return _json.dumps(message, ensure_ascii=False)
        return _json.dumps({"type": "system", "data": {"content": str(message)}}, ensure_ascii=False)

    def add_message(self, message: Any) -> None:
        from sqlalchemy import insert

        with self._engine.connect() as conn:
            conn.execute(
                insert(self._table).values(
                    session_id=self.session_id,
                    message=self._serialize(message),
                )
            )
            conn.commit()

    def add_messages(self, messages: list) -> None:
        from sqlalchemy import insert

        rows = [
            {"session_id": self.session_id, "message": self._serialize(m)}
            for m in messages
        ]
        if rows:
            with self._engine.connect() as conn:
                conn.execute(insert(self._table), rows)
                conn.commit()
