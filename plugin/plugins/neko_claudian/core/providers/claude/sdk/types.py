"""
1:1 ported from claudian/src/providers/claude/sdk/types.ts

SDK 内容块 / 通用类型。

Original Claudian 注释（保留）：
> Anthropic Agent SDK 的内容块类型，跨所有消息类型使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# ContentBlock — Claude API 内容块
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    """文本块。"""
    type: str = "text"
    text: str = ""


@dataclass
class ThinkingBlock:
    """思考块（extended thinking）。"""
    type: str = "thinking"
    thinking: str = ""
    signature: Optional[str] = None


@dataclass
class ToolUseBlock:
    """工具调用块。"""
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    """工具结果块。"""
    type: str = "tool_result"
    tool_use_id: str = ""
    content: Union[str, List[Dict[str, Any]], None] = None
    is_error: Optional[bool] = None


@dataclass
class ImageBlock:
    """图像块（base64 编码）。"""
    type: str = "image"
    source: Optional[Dict[str, Any]] = None


ContentBlock = Union[TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock, ImageBlock]


# ---------------------------------------------------------------------------
# 助手消息内容
# ---------------------------------------------------------------------------

AssistantContent = Union[str, List[Dict[str, Any]]]


# ---------------------------------------------------------------------------
# 模型信息
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """模型元信息。"""
    id: str
    display_name: str = ""
    provider: str = "anthropic"
    supports_thinking: bool = True
    supports_vision: bool = True
    max_tokens: int = 200000


# ---------------------------------------------------------------------------
# Token 使用统计
# ---------------------------------------------------------------------------

@dataclass
class UsageInfo:
    """Token 使用情况（与 claudian usageInfo.ts 对齐）。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    service_tier: Optional[str] = None
    context_window: int = 200000

    def total_input(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "service_tier": self.service_tier,
            "context_window": self.context_window,
        }


# ---------------------------------------------------------------------------
# 权限模式（与 Claudian PermissionMode 对齐）
# ---------------------------------------------------------------------------

PERMISSION_MODE_DEFAULT = "default"
PERMISSION_MODE_ACCEPT_EDITS = "acceptEdits"
PERMISSION_MODE_BYPASS = "bypassPermissions"
PERMISSION_MODE_PLAN = "plan"

PERMISSION_MODES = (
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_ACCEPT_EDITS,
    PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_PLAN,
)


# ---------------------------------------------------------------------------
# 错误信息
# ---------------------------------------------------------------------------

@dataclass
class SDKErrorInfo:
    """SDK 错误结构。"""
    message: str = ""
    code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "code": self.code,
            "details": self.details,
        }
