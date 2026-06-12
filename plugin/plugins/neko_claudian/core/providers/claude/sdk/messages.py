"""
1:1 ported from claudian/src/providers/claude/sdk/messages.ts

SDK 消息类（与 @anthropic-ai/claude-agent SDK 协议对齐）。

每条消息都带一个 `type` discriminator：
- system    : 系统消息（init / status / hook_response / compact_boundary）
- assistant : 助手消息（content blocks）
- user      : 用户消息（含 tool_result）
- result    : 最终结果（success / error）
- stream_event : 流事件（message_start / content_block_start / ... deltas / stop）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .types import (
    AssistantContent,
    SDKErrorInfo,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    ImageBlock,
    UsageInfo,
)


# ---------------------------------------------------------------------------
# System 消息子类型
# ---------------------------------------------------------------------------

@dataclass
class SystemInitMessage:
    """session 初始化消息。"""
    type: str = "system"
    subtype: str = "init"
    session_id: str = ""
    tools: List[str] = field(default_factory=list)
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    cwd: str = ""
    permission_mode: str = "default"
    apiKeySource: Optional[str] = None
    claude_code_version: Optional[str] = None


@dataclass
class SystemStatusMessage:
    """系统状态变化。"""
    type: str = "system"
    subtype: str = "status"
    status: str = ""  # "compacting" / "compact_boundary" / etc
    session_id: str = ""
    uuid: str = ""


@dataclass
class CompactBoundaryMessage:
    """上下文压缩边界。"""
    type: str = "system"
    subtype: str = "compact_boundary"
    trigger: str = ""  # "auto" / "manual"
    pre_tokens: int = 0


SystemMessage = Union[SystemInitMessage, SystemStatusMessage, CompactBoundaryMessage, dict]


# ---------------------------------------------------------------------------
# Assistant 消息
# ---------------------------------------------------------------------------

@dataclass
class AssistantMessage:
    """助手消息 — 多个 content block。"""
    type: str = "assistant"
    message: Dict[str, Any] = field(default_factory=dict)  # 完整 message dict
    parent_tool_use_id: Optional[str] = None
    session_id: str = ""
    uuid: str = ""

    def content_blocks(self) -> List[Dict[str, Any]]:
        return list(self.message.get("content", []) or [])

    def text(self) -> str:
        parts: List[str] = []
        for blk in self.content_blocks():
            if blk.get("type") == "text":
                parts.append(blk.get("text", ""))
        return "".join(parts)

    def model(self) -> str:
        return self.message.get("model", "")

    def usage(self) -> Optional[UsageInfo]:
        u = self.message.get("usage")
        if not u:
            return None
        return UsageInfo(
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
        )


# ---------------------------------------------------------------------------
# User 消息
# ---------------------------------------------------------------------------

@dataclass
class UserMessage:
    """用户消息 — content 列表通常含 tool_result。"""
    type: str = "user"
    message: Dict[str, Any] = field(default_factory=dict)
    parent_tool_use_id: Optional[str] = None
    session_id: str = ""
    uuid: str = ""

    def content_blocks(self) -> List[Dict[str, Any]]:
        return list(self.message.get("content", []) or [])

    def is_tool_result(self) -> bool:
        for blk in self.content_blocks():
            if blk.get("type") == "tool_result":
                return True
        return False


# ---------------------------------------------------------------------------
# Result 消息
# ---------------------------------------------------------------------------

@dataclass
class ResultSuccess:
    subtype: str = "success"
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    num_turns: int = 0
    result: str = ""
    usage: Optional[Dict[str, Any]] = None


@dataclass
class ResultError:
    subtype: str = "error"
    error: SDKErrorInfo = field(default_factory=SDKErrorInfo)
    duration_ms: int = 0


@dataclass
class ResultMessage:
    """最终结果。"""
    type: str = "result"
    subtype: str = "success"
    is_error: bool = False
    duration_ms: int = 0
    duration_api_ms: int = 0
    num_turns: int = 0
    total_cost_usd: float = 0.0
    usage: Optional[Dict[str, Any]] = None
    result: str = ""
    error: Optional[SDKErrorInfo] = None
    session_id: str = ""
    uuid: str = ""


# ---------------------------------------------------------------------------
# Stream Event（流事件，仿 Anthropic Messages API 流式）
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    """流事件 message_start / content_block_start / delta / content_block_stop / message_stop。"""
    type: str = "stream_event"
    event: Dict[str, Any] = field(default_factory=dict)
    parent_tool_use_id: Optional[str] = None
    session_id: str = ""
    uuid: str = ""

    def event_type(self) -> str:
        return self.event.get("type", "")


# ---------------------------------------------------------------------------
# 联合类型
# ---------------------------------------------------------------------------

SDKMessage = Union[
    SystemMessage,
    AssistantMessage,
    UserMessage,
    ResultMessage,
    StreamEvent,
    Dict[str, Any],
]


def message_to_dict(msg: Any) -> Dict[str, Any]:
    """把 SDKMessage 序列化为 dict（与 JSONL 协议对齐）。"""
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "to_dict"):
        return msg.to_dict()
    if hasattr(msg, "__dict__"):
        return {k: v for k, v in msg.__dict__.items() if v is not None}
    return {"data": str(msg)}
