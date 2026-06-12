"""
1:1 ported from claudian/src/providers/claude/runtime/types.ts

Runtime 内部类型 — InputController / StreamController / ChatRuntime 接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union, Awaitable

from ..sdk.types import UsageInfo


# ---------------------------------------------------------------------------
# 流块（StreamChunk）— 内部事件类型
# ---------------------------------------------------------------------------

class ChunkType(str, Enum):
    """与 claudian core/types/chat.ts StreamChunk 一致。"""
    SESSION_INFO = "session_info"
    USER = "user"
    ASSISTANT = "assistant"
    THINKING = "thinking"
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TOOL_INPUT_PROGRESS = "tool_input_progress"
    USAGE = "usage"
    ERROR = "error"
    CONTROL_REQUEST = "control_request"
    CONTROL_RESPONSE = "control_response"
    REWIND = "rewind"
    COMPACT = "compact"
    DONE = "done"
    STATUS = "status"
    STREAM_EVENT = "stream_event"


@dataclass
class StreamChunk:
    """内部流块（前端会订阅的最小单元）。"""
    type: str  # ChunkType 值
    tab_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "tab_id": self.tab_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# 消息 / 对话
# ---------------------------------------------------------------------------

@dataclass
class ImageAttachment:
    """图像附件。"""
    media_type: str = ""
    data: str = ""  # base64
    url: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"mediaType": self.media_type, "data": self.data}
        if self.url:
            out["url"] = self.url
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class ToolCallInfo:
    """工具调用信息（前端展示用）。"""
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False
    status: str = "pending"  # pending / running / success / error
    started_at: float = 0.0
    finished_at: float = 0.0
    parent_tool_use_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "input": self.input,
            "result": self.result,
            "is_error": self.is_error,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "parent_tool_use_id": self.parent_tool_use_id,
        }


@dataclass
class ChatMessage:
    """对话中的一条消息。"""
    id: str = ""
    role: str = "user"  # user / assistant / system
    content: str = ""
    images: List[ImageAttachment] = field(default_factory=list)
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    thinking: str = ""
    timestamp: float = 0.0
    parent_tool_use_id: Optional[str] = None
    is_meta: bool = False
    uuid: str = ""
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "images": [img.to_dict() for img in self.images],
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "thinking": self.thinking,
            "timestamp": self.timestamp,
            "parent_tool_use_id": self.parent_tool_use_id,
            "is_meta": self.is_meta,
            "uuid": self.uuid,
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Conversation（会话）
# ---------------------------------------------------------------------------

@dataclass
class Conversation:
    """一个 Tab 对应一个 Conversation。"""
    id: str = ""
    title: str = ""
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None  # 用于 fork
    cwd: str = ""
    model: str = ""
    permission_mode: str = "default"
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    is_fork: bool = False
    fork_point_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "cwd": self.cwd,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_fork": self.is_fork,
            "fork_point_uuid": self.fork_point_uuid,
        }


# ---------------------------------------------------------------------------
# 权限 / 计划 / AskUser / ExitPlan 决策
# ---------------------------------------------------------------------------

@dataclass
class ApprovalDecision:
    """canUseTool 决策。"""
    decision: str  # "allow" / "allow-always" / "deny"
    reason: str = ""


@dataclass
class ApprovalCallbackOptions:
    """canUseTool 回调参数。"""
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


# ApprovalCallback = Callable[[ApprovalCallbackOptions], Awaitable[ApprovalDecision]]


@dataclass
class AskUserQuestionOption:
    label: str = ""
    description: str = ""


@dataclass
class AskUserQuestionRequest:
    question: str = ""
    header: str = ""
    options: List[AskUserQuestionOption] = field(default_factory=list)
    multi_select: bool = False
    request_id: str = ""


@dataclass
class AskUserQuestionResponse:
    request_id: str = ""
    selected: List[str] = field(default_factory=list)
    cancelled: bool = False


# ---------------------------------------------------------------------------
# ChatTurn / PreparedChatTurn
# ---------------------------------------------------------------------------

@dataclass
class ChatTurnMetadata:
    """turn 元信息。"""
    turn_id: str = ""
    source: str = "user"  # "user" / "auto" / "steer"
    requested_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatTurnRequest:
    """用户输入 turn。"""
    text: str = ""
    images: List[ImageAttachment] = field(default_factory=list)
    metadata: ChatTurnMetadata = field(default_factory=ChatTurnMetadata)
    session_id: Optional[str] = None
    fork_session: bool = False
    resume_at: Optional[str] = None


@dataclass
class PreparedChatTurn:
    """编码后的 turn（已经转为 SDK 消息）。"""
    request: ChatTurnRequest = field(default_factory=ChatTurnRequest)
    encoded_message: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------

ApprovalCallback = Any  # Callable[[ApprovalCallbackOptions], "Awaitable[ApprovalDecision]"]
AskUserQuestionCallback = Any  # Callable[[AskUserQuestionRequest], "Awaitable[AskUserQuestionResponse]"]
ExitPlanModeCallback = Any  # Callable[[Dict[str, Any]], "Awaitable[Dict[str, Any]]"]
AutoTurnCallback = Any  # Callable[[List[StreamChunk]], "Awaitable[None]"]


@dataclass
class ChatRewindResult:
    """Rewind 结果。"""
    success: bool = False
    rewound_to: Optional[str] = None  # user message uuid
    removed_messages: int = 0
    error: Optional[str] = None


@dataclass
class ChatRuntimeQueryOptions:
    """冷启动查询选项。"""
    model: Optional[str] = None
    permission_mode: Optional[str] = None
    cwd: Optional[str] = None
    system_prompt: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    resume: Optional[str] = None
    fork_session: bool = False
    max_thinking_tokens: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatRuntimeConversationState:
    """当前会话状态。"""
    session_id: Optional[str] = None
    model: str = ""
    cwd: str = ""
    is_streaming: bool = False
    num_turns: int = 0
    total_cost_usd: float = 0.0
    last_error: Optional[str] = None


@dataclass
class SessionUpdateResult:
    """更新结果。"""
    success: bool = False
    error: Optional[str] = None
    new_session_id: Optional[str] = None


@dataclass
class ChatRewindMode:
    """rewind 模式。"""
    mode: str = "user_only"  # "user_only" / "all"
    target_uuid: Optional[str] = None


# ---------------------------------------------------------------------------
# Persistent Query Config
# ---------------------------------------------------------------------------

@dataclass
class PersistentQueryConfig:
    """持久查询配置（与 Claudian PersistentQueryConfig 对齐）。"""
    model: str = ""
    cwd: str = ""
    permission_mode: str = "default"
    system_prompt: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    disallowed_tools: Optional[List[str]] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    resume: Optional[str] = None
    max_thinking_tokens: Optional[int] = None
    effort: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Response Handler（consumeResponses 内部用）
# ---------------------------------------------------------------------------

@dataclass
class ResponseHandlerContext:
    """ResponseHandler 上下文。"""
    chunk: StreamChunk
    config: PersistentQueryConfig
    is_turn_complete: bool = False


ResponseHandler = Any  # Callable[[ResponseHandlerContext], "Awaitable[None]"]


@dataclass
class ClosePersistentQueryOptions:
    options: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# QueuedTurn — 消息队列中的待处理 turn
# ---------------------------------------------------------------------------

@dataclass
class QueuedTurn:
    """消息队列中的待处理 turn。

    Ported from claudian/src/core/runtime/QueuedTurn.ts
    """
    id: str = ""
    request: ChatTurnRequest = field(default_factory=ChatTurnRequest)
    options: ChatRuntimeQueryOptions = field(default_factory=ChatRuntimeQueryOptions)
    created_at: float = 0.0
    priority: int = 0  # 0 = normal, 1 = high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request.to_dict() if hasattr(self.request, 'to_dict') else {},
            "created_at": self.created_at,
            "priority": self.priority,
        }


# ---------------------------------------------------------------------------
# Transform state — stream transform 内部状态
# ---------------------------------------------------------------------------

@dataclass
class TransformStreamState:
    """Stream transform 内部状态。

    Ported from claudian/src/providers/claude/stream/transformClaudeMessage.ts
    """
    current_tool_use_id: Optional[str] = None
    current_tool_name: Optional[str] = None
    current_tool_input: str = ""
    thinking_buffer: str = ""
    text_buffer: str = ""
    is_collecting_tool_input: bool = False
    is_collecting_thinking: bool = False
    is_collecting_text: bool = False

    def reset(self) -> None:
        """Reset all state."""
        self.current_tool_use_id = None
        self.current_tool_name = None
        self.current_tool_input = ""
        self.thinking_buffer = ""
        self.text_buffer = ""
        self.is_collecting_tool_input = False
        self.is_collecting_thinking = False
        self.is_collecting_text = False


@dataclass
class TransformUsageState:
    """Usage tracking state for stream transform.

    Ported from claudian/src/providers/claude/stream/transformClaudeMessage.ts
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0

    def add(self, usage: Dict[str, Any]) -> None:
        """Add usage data from a chunk."""
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cache_creation_input_tokens += usage.get("cache_creation_input_tokens", 0)
        self.cache_read_input_tokens += usage.get("cache_read_input_tokens", 0)
        if "total_cost_usd" in usage:
            self.total_cost_usd = usage["total_cost_usd"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "total_cost_usd": self.total_cost_usd,
        }


# ---------------------------------------------------------------------------
# SDK Control Request/Response — 控制请求/响应
# ---------------------------------------------------------------------------

@dataclass
class SdkControlRequest:
    """SDK 控制请求。

    Ported from claudian/src/providers/claude/runtime/types.ts
    """
    type: str = ""  # "permission" / "ask_user" / "exit_plan_mode"
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""
    description: str = ""
    request_id: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_use_id": self.tool_use_id,
            "description": self.description,
            "request_id": self.request_id,
            "context": self.context,
        }


@dataclass
class SdkControlResponse:
    """SDK 控制响应。

    Ported from claudian/src/providers/claude/runtime/types.ts
    """
    request_id: str = ""
    decision: str = ""  # "allow" / "deny" / "cancel"
    updated_input: Optional[Dict[str, Any]] = None
    updated_permissions: Optional[list] = None
    message: Optional[str] = None
    interrupt: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "request_id": self.request_id,
            "decision": self.decision,
        }
        if self.updated_input is not None:
            out["updated_input"] = self.updated_input
        if self.updated_permissions is not None:
            out["updated_permissions"] = self.updated_permissions
        if self.message is not None:
            out["message"] = self.message
        if self.interrupt:
            out["interrupt"] = self.interrupt
        return out


# ---------------------------------------------------------------------------
# AbortSignal — 取消信号
# ---------------------------------------------------------------------------

class AbortSignal:
    """Simple abort signal implementation.

    Ported from claudian's AbortSignal usage patterns.
    """
    def __init__(self):
        self._aborted = False
        self._reason: Optional[str] = None
        self._callbacks: list[Callable] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def abort(self, reason: str = "Aborted") -> None:
        """Abort the signal."""
        self._aborted = True
        self._reason = reason
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                pass

    def on_abort(self, callback: Callable) -> None:
        """Register an abort callback."""
        if self._aborted:
            callback()
        else:
            self._callbacks.append(callback)

    def throw_if_aborted(self) -> None:
        """Raise if aborted."""
        if self._aborted:
            raise RuntimeError(self._reason or "Aborted")


class AbortController:
    """Abort controller that creates AbortSignal."""
    def __init__(self):
        self.signal = AbortSignal()

    def abort(self, reason: str = "Aborted") -> None:
        self.signal.abort(reason)


# ---------------------------------------------------------------------------
# SubagentRuntimeState — 子代理运行状态
# ---------------------------------------------------------------------------

@dataclass
class SubagentRuntimeState:
    """子代理运行状态。

    Ported from claudian/src/core/runtime/types.ts
    """
    has_running: bool = False
    running_count: int = 0
    agent_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_running": self.has_running,
            "running_count": self.running_count,
            "agent_ids": self.agent_ids,
        }
