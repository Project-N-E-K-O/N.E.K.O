# Ported from claudian/src/core/types/chat.ts
# Original author: Claudian contributors
# License: MIT

"""
Chat type definitions — messages, conversations, stream chunks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from .tools import SubagentMode, ToolCallInfo


@dataclass
class ForkSource:
    """Fork origin reference: identifies the source session and checkpoint."""
    session_id: str = ""
    resume_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"sessionId": self.session_id, "resumeAt": self.resume_at}


# View type identifier (for N.E.K.O UI)
VIEW_TYPE_NEKO_CLAUDIAN = "neko-claudian-view"


class ImageMediaType(str, Enum):
    """Supported image media types for attachments."""
    JPEG = "image/jpeg"
    PNG = "image/png"
    GIF = "image/gif"
    WEBP = "image/webp"


@dataclass
class ImageAttachment:
    """Image attachment metadata."""
    id: str = ""
    name: str = ""
    media_type: str = "image/png"
    data: str = ""  # Base64 encoded
    width: Optional[int] = None
    height: Optional[int] = None
    size: int = 0
    source: str = "file"  # "file" | "paste" | "drop"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "mediaType": self.media_type,
            "data": self.data,
            "size": self.size,
            "source": self.source,
        }
        if self.width is not None:
            out["width"] = self.width
        if self.height is not None:
            out["height"] = self.height
        return out


@dataclass
class ContentBlock:
    """Content block for preserving streaming order in messages."""
    type: str = "text"  # "text" | "tool_use" | "thinking" | "subagent" | "context_compacted"
    content: str = ""
    tool_id: Optional[str] = None
    subagent_id: Optional[str] = None
    subagent_mode: Optional[SubagentMode] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type}
        if self.type == "text" or self.type == "thinking":
            out["content"] = self.content
            if self.duration_seconds is not None:
                out["durationSeconds"] = self.duration_seconds
        elif self.type == "tool_use":
            out["toolId"] = self.tool_id
        elif self.type == "subagent":
            out["subagentId"] = self.subagent_id
            if self.subagent_mode:
                out["mode"] = self.subagent_mode.value
        return out


@dataclass
class ChatMessage:
    """Chat message with content, tool calls, and attachments."""
    id: str = ""
    role: str = "user"  # "user" | "assistant"
    content: str = ""
    display_content: Optional[str] = None
    timestamp: float = 0.0
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    content_blocks: List[ContentBlock] = field(default_factory=list)
    current_note: Optional[str] = None
    images: List[ImageAttachment] = field(default_factory=list)
    is_interrupt: bool = False
    is_rebuilt_context: bool = False
    duration_seconds: Optional[float] = None
    duration_flavor_word: Optional[str] = None
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.display_content:
            out["displayContent"] = self.display_content
        if self.tool_calls:
            out["toolCalls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.content_blocks:
            out["contentBlocks"] = [cb.to_dict() for cb in self.content_blocks]
        if self.images:
            out["images"] = [img.to_dict() for img in self.images]
        if self.is_interrupt:
            out["isInterrupt"] = True
        if self.duration_seconds is not None:
            out["durationSeconds"] = self.duration_seconds
        if self.user_message_id:
            out["userMessageId"] = self.user_message_id
        if self.assistant_message_id:
            out["assistantMessageId"] = self.assistant_message_id
        return out


@dataclass
class UsageInfo:
    """Context window usage information."""
    model: Optional[str] = None
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    context_window: int = 200000
    context_window_is_authoritative: bool = False
    context_tokens: int = 0
    percentage: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "inputTokens": self.input_tokens,
            "contextWindow": self.context_window,
            "contextTokens": self.context_tokens,
            "percentage": self.percentage,
        }
        if self.model:
            out["model"] = self.model
        if self.cache_creation_input_tokens:
            out["cacheCreationInputTokens"] = self.cache_creation_input_tokens
        if self.cache_read_input_tokens:
            out["cacheReadInputTokens"] = self.cache_read_input_tokens
        if self.context_window_is_authoritative:
            out["contextWindowIsAuthoritative"] = True
        return out


@dataclass
class Conversation:
    """Persisted conversation with messages and session state."""
    id: str = ""
    provider_id: str = "claude"
    title: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    last_response_at: Optional[float] = None
    session_id: Optional[str] = None
    provider_state: Optional[Dict[str, Any]] = None
    messages: List[ChatMessage] = field(default_factory=list)
    current_note: Optional[str] = None
    external_context_paths: Optional[List[str]] = None
    usage: Optional[UsageInfo] = None
    title_generation_status: Optional[str] = None  # "pending" | "success" | "failed"
    enabled_mcp_servers: Optional[List[str]] = None
    resume_at_message_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "providerId": self.provider_id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
        }
        if self.last_response_at:
            out["lastResponseAt"] = self.last_response_at
        if self.session_id:
            out["sessionId"] = self.session_id
        if self.provider_state:
            out["providerState"] = self.provider_state
        if self.current_note:
            out["currentNote"] = self.current_note
        if self.external_context_paths:
            out["externalContextPaths"] = self.external_context_paths
        if self.usage:
            out["usage"] = self.usage.to_dict()
        if self.title_generation_status:
            out["titleGenerationStatus"] = self.title_generation_status
        if self.enabled_mcp_servers:
            out["enabledMcpServers"] = self.enabled_mcp_servers
        if self.resume_at_message_id:
            out["resumeAtMessageId"] = self.resume_at_message_id
        return out


@dataclass
class ConversationMeta:
    """Lightweight conversation metadata for the history dropdown."""
    id: str = ""
    provider_id: str = "claude"
    title: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    last_response_at: Optional[float] = None
    message_count: int = 0
    preview: str = ""
    title_generation_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "providerId": self.provider_id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "messageCount": self.message_count,
            "preview": self.preview,
        }
        if self.last_response_at:
            out["lastResponseAt"] = self.last_response_at
        if self.title_generation_status:
            out["titleGenerationStatus"] = self.title_generation_status
        return out


@dataclass
class SessionMetadata:
    """Session metadata overlay for provider-native storage."""
    id: str = ""
    provider_id: Optional[str] = None
    title: str = ""
    title_generation_status: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    last_response_at: Optional[float] = None
    session_id: Optional[str] = None
    provider_state: Optional[Dict[str, Any]] = None
    current_note: Optional[str] = None
    external_context_paths: Optional[List[str]] = None
    enabled_mcp_servers: Optional[List[str]] = None
    usage: Optional[UsageInfo] = None
    resume_at_message_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if self.provider_id:
            out["providerId"] = self.provider_id
        if self.title_generation_status:
            out["titleGenerationStatus"] = self.title_generation_status
        if self.last_response_at:
            out["lastResponseAt"] = self.last_response_at
        if self.session_id is not None:
            out["sessionId"] = self.session_id
        if self.provider_state:
            out["providerState"] = self.provider_state
        if self.current_note:
            out["currentNote"] = self.current_note
        if self.external_context_paths:
            out["externalContextPaths"] = self.external_context_paths
        if self.enabled_mcp_servers:
            out["enabledMcpServers"] = self.enabled_mcp_servers
        if self.usage:
            out["usage"] = self.usage.to_dict()
        if self.resume_at_message_id:
            out["resumeAtMessageId"] = self.resume_at_message_id
        return out


# StreamChunk is a union type in TypeScript; in Python we use a dataclass with type discriminator
@dataclass
class StreamChunk:
    """Normalized stream chunk emitted by the active provider runtime."""
    type: str = ""  # "text" | "thinking" | "tool_use" | "tool_result" | "error" | "done" | "usage" | ...
    content: str = ""
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    is_error: bool = False
    usage: Optional[UsageInfo] = None
    session_id: Optional[str] = None
    item_id: Optional[str] = None
    level: Optional[str] = None  # "info" | "warning"
    agent_id: Optional[str] = None
    status: Optional[str] = None  # "completed" | "error"
    result: Optional[str] = None
    subagent_id: Optional[str] = None
    tool_use_result: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type}
        if self.content:
            out["content"] = self.content
        if self.id:
            out["id"] = self.id
        if self.name:
            out["name"] = self.name
        if self.input:
            out["input"] = self.input
        if self.is_error:
            out["isError"] = True
        if self.usage:
            out["usage"] = self.usage.to_dict()
        if self.session_id is not None:
            out["sessionId"] = self.session_id
        if self.item_id:
            out["itemId"] = self.item_id
        if self.level:
            out["level"] = self.level
        if self.agent_id:
            out["agentId"] = self.agent_id
        if self.status:
            out["status"] = self.status
        if self.result:
            out["result"] = self.result
        if self.subagent_id:
            out["subagentId"] = self.subagent_id
        return out
