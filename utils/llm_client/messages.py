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
"""Message models, serialization helpers, and response value objects."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "system": "system"}

_ROLE_TO_TYPE = {"user": "human", "assistant": "ai", "system": "system"}


@dataclass
class BaseMessage:
    content: Any
    type: str = ""
    additional_kwargs: dict[str, Any] = field(default_factory=dict)
    # Persist and route only inside N.E.K.O; to_openai explicitly strips this before sending to model providers.
    # Program semantics such as theater provenance need not masquerade as body tags or pollute ordinary model protocols.
    metadata: dict[str, Any] = field(default_factory=dict)

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


def message_metadata(message: Any) -> dict[str, Any]:
    """Read internal metadata from in-memory, OpenAI, or persisted messages."""  # noqa: DOCSTRING_CJK

    if isinstance(message, BaseMessage):
        return message.metadata if isinstance(message.metadata, dict) else {}
    if not isinstance(message, dict):
        return {}
    direct = message.get("metadata")
    if isinstance(direct, dict):
        return direct
    data = message.get("data")
    if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
        return data["metadata"]
    return {}


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

def messages_to_dict(messages: list) -> list[dict]:
    """Serialize message objects to the on-disk format.

    Output format per element::

        {"type": "human", "data": {"content": "hello"}}

    Backward-compatible with files written by the old langchain serializer.
    """
    result: list[dict] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            data = {"content": msg.content}
            if msg.additional_kwargs:
                data["additional_kwargs"] = dict(msg.additional_kwargs)
            if msg.metadata:
                data["metadata"] = dict(msg.metadata)
            result.append({"type": msg.type, "data": data})
        elif isinstance(msg, dict):
            if "type" in msg and "data" in msg:
                result.append(msg)
            elif "role" in msg:
                t = _ROLE_TO_TYPE.get(msg["role"], msg["role"])
                data = {"content": msg.get("content", "")}
                if isinstance(msg.get("additional_kwargs"), dict) and msg["additional_kwargs"]:
                    data["additional_kwargs"] = dict(msg["additional_kwargs"])
                if isinstance(msg.get("metadata"), dict) and msg["metadata"]:
                    data["metadata"] = dict(msg["metadata"])
                result.append({"type": t, "data": data})
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
            data = d["data"]
            content = data.get("content", "") if isinstance(data, dict) else data
            additional_kwargs = data.get("additional_kwargs", {}) if isinstance(data, dict) else {}
            metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
            result.append(cls(
                content=content,
                additional_kwargs=(dict(additional_kwargs) if isinstance(additional_kwargs, dict) else {}),
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
            ))
        elif "role" in d and "content" in d:
            cls = _ROLE_CLS.get(d["role"], HumanMessage)
            additional_kwargs = d.get("additional_kwargs", {})
            metadata = d.get("metadata", {})
            result.append(cls(
                content=d["content"],
                additional_kwargs=(dict(additional_kwargs) if isinstance(additional_kwargs, dict) else {}),
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
            ))
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
    # A fragment may additionally carry ``extra_content`` — the
    # non-standard, provider-owned blob (Gemini's OpenAI-compat surface
    # puts ``thought_signature`` there) that must be echoed back verbatim
    # on the assistant turn; see ``ToolCallAggregate.extra_content``.
    tool_call_deltas: list[dict] | None = None
    # Reason the model finished this stream segment: ``"stop"`` / ``"length"`` /
    # ``"tool_calls"`` / ``"content_filter"`` / None. ``"tool_calls"`` signals
    # the caller should run the tool then continue the conversation.
    finish_reason: str | None = None
    # Reasoning-chain text streamed separately in ``delta`` by thinking-mode models (DeepSeek-R / Qwen / GLM thinking and other
    # OpenAI-compatible endpoints). Ordinary chat does not need it, but during **multi-round
    # tool calling**, these providers require the assistant message initiating tool_calls to include
    # ``reasoning_content`` unchanged, or the next round returns 400 "The `reasoning_content`
    # in the thinking mode must be passed back to the API.". Tool loops accumulate this
    # field and write it back into assistant history.
    reasoning_content: str | None = None
    # ``OmniOfflineClient`` tool-loop sentinel: after ``_astream_*_with_tools`` writes
    # the current tool round (assistant tool_calls + tool result) inline into history,
    # it yields ``LLMStreamChunk(content="", tool_round_persisted=True)``.
    # ``stream_text`` clears its final-segment buffer on receiving it, preventing a later
    # ``_conversation_history.append(AIMessage(content=...))`` from writing pre-tool
    # text into history twice (it already exists in ``assistant.tool_calls.content``).
    #
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
    # Provider-owned passthrough blob attached to THIS call, e.g.
    # ``{"google": {"thought_signature": "<base64>"}}`` from Gemini's
    # OpenAI-compat endpoint. Opaque to us: the tool loop writes it back
    # onto the assistant ``tool_calls`` entry unchanged, because Gemini
    # rejects a follow-up request whose function call lost its signature
    # (400 INVALID_ARGUMENT). ``None`` on providers that don't send one.
    extra_content: dict | None = None

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
                # metadata is only for internal N.E.K.O persistence/routing, outside provider message protocols.
                out.append({key: value for key, value in msg.items() if key != "metadata"})
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
