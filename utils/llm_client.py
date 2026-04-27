"""Lightweight LLM client layer using the ``openai`` SDK directly.

Provides:
  - Message classes (SystemMessage, HumanMessage, AIMessage) compatible with
    the old langchain interface
  - ChatOpenAI wrapper with streaming, invoke, and resource management
  - ``create_chat_llm()`` factory that auto-resolves provider-specific config
  - Serialization helpers (messages_to_dict, messages_from_dict, convert_to_messages)
  - OpenAIEmbeddings / SQLChatMessageHistory for memory subsystem
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Union

from openai import AsyncOpenAI, OpenAI

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


# ────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────

def _normalize_messages(messages: Any) -> list[dict]:
    """Convert various message formats to openai-compatible dicts."""
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
            out.append(msg.to_openai())
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
        **_kwargs: Any,
    ):
        self.model = model
        self.base_url = base_url
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
        self._aclient = AsyncOpenAI(**client_kw)
        self._client = OpenAI(**client_kw)

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
        if stream:
            p["stream_options"] = {"include_usage": True}
        # Anything else the caller passed (e.g. timeout, logit_bias) goes
        # straight through to the SDK call.
        p.update(overrides)

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
        content = resp.choices[0].message.content if resp.choices else ""
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(content=content or "", response_metadata={"token_usage": usage_dict})

    def invoke(self, messages: Any, **overrides: Any) -> LLMResponse:
        """Sync twin of ``ainvoke``. See its docstring for ``overrides``."""
        resp = self._client.chat.completions.create(**self._params(messages, **overrides))
        content = resp.choices[0].message.content if resp.choices else ""
        usage_dict = resp.usage.model_dump() if resp.usage else {}
        return LLMResponse(content=content or "", response_metadata={"token_usage": usage_dict})

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

    async def astream(self, messages: Any) -> AsyncIterator[LLMStreamChunk]:
        stream = await self._aclient.chat.completions.create(**self._params(messages, stream=True))
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            content = delta.content if delta and delta.content else ""
            if content:
                yield LLMStreamChunk(content=content)
            # Terminal chunk with usage info (stream_options={"include_usage": True})
            if chunk.usage is not None:
                usage_dict = chunk.usage.model_dump()
                yield LLMStreamChunk(
                    content="",
                    usage_metadata=usage_dict,
                    response_metadata={"token_usage": usage_dict},
                )

    # --- resource management ---

    async def aclose(self) -> None:
        """Close underlying httpx clients (async path)."""
        await self._aclient.close()
        self._client.close()

    def close(self) -> None:
        """Close underlying httpx clients (sync path)."""
        self._client.close()

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
        **cache_kw,
        **kw,
    )


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
