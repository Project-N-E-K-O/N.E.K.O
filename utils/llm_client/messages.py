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
