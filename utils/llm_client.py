"""Lightweight replacements for langchain_openai and langchain_core.messages.

Provides ChatOpenAI / OpenAIEmbeddings / message-class interfaces using the
``openai`` SDK directly, eliminating the heavy langchain dependency chain
(langchain-core, langchain-openai, langchain-community, pydantic v1 compat,
jsonpatch, tenacity …).

Drop-in compatible: callers only need to change their import path.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Union

from openai import AsyncOpenAI, OpenAI

# ────────────────────────────────────────────────────────────────
# Message classes (replace langchain_core.messages)
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
# Serialization helpers (replace langchain messages_to_dict etc.)
# ────────────────────────────────────────────────────────────────


def messages_to_dict(messages: list) -> list[dict]:
    """Serialize message objects to the on-disk format used by langchain.

    Output format per element::

        {"type": "human", "data": {"content": "hello"}}

    This is backward-compatible with files written by ``langchain_core.messages.messages_to_dict``.
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

    Accepts both langchain format (``type``/``data``) and OpenAI format
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
    as well as the langchain on-disk format.
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


@dataclass
class LLMStreamChunk:
    content: str


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
# ChatOpenAI replacement (langchain_openai.ChatOpenAI)
# ────────────────────────────────────────────────────────────────

class ChatOpenAI:
    """Drop-in replacement for ``langchain_openai.ChatOpenAI``."""

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 1.0,
        streaming: bool = False,
        max_retries: int = 2,
        extra_body: dict | None = None,
        max_completion_tokens: int | None = None,
        max_tokens: int | None = None,
        model_kwargs: dict | None = None,
        timeout: float | None = None,
        request_timeout: float | None = None,
        **_kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.extra_body: dict = extra_body or {}
        self.max_completion_tokens = max_completion_tokens
        self.max_tokens = max_tokens

        if model_kwargs and "extra_body" in model_kwargs:
            self.extra_body = {**self.extra_body, **model_kwargs["extra_body"]}

        _api_key = api_key or "sk-placeholder"
        _timeout = timeout or request_timeout
        client_kw: dict[str, Any] = dict(base_url=base_url, api_key=_api_key, max_retries=max_retries)
        if _timeout is not None:
            client_kw["timeout"] = _timeout
        self._aclient = AsyncOpenAI(**client_kw)
        self._client = OpenAI(**client_kw)

    def _params(self, messages: Any, *, stream: bool = False) -> dict:
        p: dict[str, Any] = {
            "model": self.model,
            "messages": _normalize_messages(messages),
            "temperature": self.temperature,
            "stream": stream,
        }
        if self.max_completion_tokens:
            p["max_completion_tokens"] = self.max_completion_tokens
        elif self.max_tokens:
            p["max_tokens"] = self.max_tokens
        if self.extra_body:
            p["extra_body"] = self.extra_body
        return p

    # --- sync / async invoke ---

    async def ainvoke(self, messages: Any) -> LLMResponse:
        resp = await self._aclient.chat.completions.create(**self._params(messages))
        content = resp.choices[0].message.content if resp.choices else ""
        return LLMResponse(content=content or "")

    def invoke(self, messages: Any) -> LLMResponse:
        resp = self._client.chat.completions.create(**self._params(messages))
        content = resp.choices[0].message.content if resp.choices else ""
        return LLMResponse(content=content or "")

    # --- async streaming ---

    async def astream(self, messages: Any) -> AsyncIterator[LLMStreamChunk]:
        stream = await self._aclient.chat.completions.create(**self._params(messages, stream=True))
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            content = delta.content if delta and delta.content else ""
            if content:
                yield LLMStreamChunk(content=content)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._aclient.close()
        self._client.close()


# ────────────────────────────────────────────────────────────────
# OpenAIEmbeddings replacement (langchain_openai.OpenAIEmbeddings)
# ────────────────────────────────────────────────────────────────

class OpenAIEmbeddings:
    """Drop-in replacement for ``langchain_openai.OpenAIEmbeddings``."""

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
# SQLChatMessageHistory replacement
# (langchain_community.chat_message_histories.SQLChatMessageHistory)
# ────────────────────────────────────────────────────────────────

class SQLChatMessageHistory:
    """Minimal replacement that preserves the table-creation side-effect
    and the ``add_message`` / ``add_messages`` interface used by
    ``memory/timeindex.py``.

    Table schema (matches langchain's default)::

        id          INTEGER PRIMARY KEY AUTOINCREMENT
        session_id  TEXT
        message     TEXT   -- JSON-serialized {"type": ..., "data": {"content": ...}}
    """

    def __init__(self, connection_string: str, session_id: str, table_name: str = "message_store"):
        from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine

        self.session_id = session_id
        self.table_name = table_name
        self._engine = create_engine(connection_string)

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
