# Ported from claudian/src/core/runtime/ChatRuntime.ts
# Original author: Claudian contributors
# License: MIT

"""
ChatRuntime — Abstract runtime interface for chat providers.

Defines the protocol that all chat runtimes must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Protocol, Set, Tuple

# Re-export types from providers/claude/runtime/types
from .providers.claude.runtime.types import (
    ApprovalCallback,
    AskUserQuestionCallback,
    AutoTurnCallback,
    ChatRewindMode,
    ChatRewindResult,
    ChatRuntimeConversationState,
    ChatRuntimeQueryOptions,
    ChatTurnMetadata,
    ChatTurnRequest,
    ExitPlanModeCallback,
    PreparedChatTurn,
    QueuedTurn,
    SessionUpdateResult,
    StreamChunk,
    SubagentRuntimeState,
)


@dataclass
class ProviderCapabilities:
    """Capabilities of a provider."""
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_images: bool = True
    supports_thinking: bool = True
    supports_mcp: bool = True
    supports_rewind: bool = True
    supports_plan_mode: bool = True
    max_context_tokens: int = 200000
    supported_models: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "supports_images": self.supports_images,
            "supports_thinking": self.supports_thinking,
            "supports_mcp": self.supports_mcp,
            "supports_rewind": self.supports_rewind,
            "supports_plan_mode": self.supports_plan_mode,
            "max_context_tokens": self.max_context_tokens,
            "supported_models": self.supported_models,
        }


@dataclass
class SlashCommand:
    """Slash command definition."""
    name: str = ""
    description: str = ""
    usage: str = ""
    is_builtin: bool = True
    hidden: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "usage": self.usage,
            "is_builtin": self.is_builtin,
            "hidden": self.hidden,
        }


@dataclass
class ChatMessage:
    """Chat message (re-exported for convenience)."""
    id: str = ""
    role: str = "user"
    content: str = ""
    timestamp: float = 0.0
    tool_calls: List[Any] = field(default_factory=list)
    thinking: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass
class Conversation:
    """Conversation (re-exported for convenience)."""
    id: str = ""
    title: str = ""
    session_id: Optional[str] = None
    messages: List[ChatMessage] = field(default_factory=list)
    model: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class ToolCallInfo:
    """Tool call info (re-exported for convenience)."""
    id: str = ""
    name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False
    status: str = "pending"


class ChatRuntime(Protocol):
    """Abstract chat runtime protocol.

    All chat runtimes must implement this interface.
    Ported from claudian/src/core/runtime/ChatRuntime.ts
    """

    @property
    def provider_id(self) -> str:
        """Provider identifier."""
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        """Get provider capabilities."""
        ...

    def prepare_turn(self, request: ChatTurnRequest) -> PreparedChatTurn:
        """Prepare a turn for submission."""
        ...

    def on_ready_state_change(self, listener: Callable[[bool], None]) -> Callable:
        """Register a listener for ready state changes. Returns unsubscribe function."""
        ...

    def set_resume_checkpoint(self, checkpoint_id: Optional[str]) -> None:
        """Set the resume checkpoint ID."""
        ...

    def sync_conversation_state(
        self,
        conversation: Optional[ChatRuntimeConversationState],
        external_context_paths: Optional[List[str]] = None
    ) -> None:
        """Sync conversation state with the runtime."""
        ...

    async def reload_mcp_servers(self) -> None:
        """Reload MCP server configurations."""
        ...

    async def ensure_ready(self, options: Optional[Dict[str, Any]] = None) -> bool:
        """Ensure the runtime is ready for queries."""
        ...

    async def query(
        self,
        turn: PreparedChatTurn,
        conversation_history: Optional[List[ChatMessage]] = None,
        query_options: Optional[ChatRuntimeQueryOptions] = None
    ) -> AsyncGenerator[StreamChunk, None]:
        """Execute a query and yield stream chunks."""
        ...

    async def steer(self, turn: PreparedChatTurn) -> bool:
        """Steer the current conversation."""
        ...

    def cancel(self) -> None:
        """Cancel the current operation."""
        ...

    def reset_session(self) -> None:
        """Reset the current session."""
        ...

    def get_session_id(self) -> Optional[str]:
        """Get the current session ID."""
        ...

    def consume_session_invalidation(self) -> bool:
        """Check and consume session invalidation flag."""
        ...

    def is_ready(self) -> bool:
        """Check if the runtime is ready."""
        ...

    async def get_supported_commands(self) -> List[SlashCommand]:
        """Get supported slash commands."""
        ...

    def get_auxiliary_model(self) -> Optional[str]:
        """Get auxiliary model name if available."""
        ...

    def cleanup(self) -> None:
        """Clean up resources."""
        ...

    async def rewind(
        self,
        user_message_id: str,
        assistant_message_id: str,
        mode: str = "conversation"
    ) -> ChatRewindResult:
        """Rewind the conversation."""
        ...

    def set_approval_callback(self, callback: Optional[ApprovalCallback]) -> None:
        """Set the approval callback."""
        ...

    def set_approval_dismisser(self, dismisser: Optional[Callable]) -> None:
        """Set the approval dismisser."""
        ...

    def set_ask_user_question_callback(self, callback: Optional[AskUserQuestionCallback]) -> None:
        """Set the ask user question callback."""
        ...

    def set_exit_plan_mode_callback(self, callback: Optional[ExitPlanModeCallback]) -> None:
        """Set the exit plan mode callback."""
        ...

    def set_permission_mode_sync_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set the permission mode sync callback."""
        ...

    def set_subagent_hook_provider(self, get_state: Callable[[], SubagentRuntimeState]) -> None:
        """Set the subagent hook provider."""
        ...

    def set_auto_turn_callback(self, callback: Optional[AutoTurnCallback]) -> None:
        """Set the auto turn callback."""
        ...

    def consume_turn_metadata(self) -> ChatTurnMetadata:
        """Consume and return turn metadata."""
        ...

    def build_session_updates(self, params: Dict[str, Any]) -> SessionUpdateResult:
        """Build session updates."""
        ...

    def resolve_session_id_for_fork(self, conversation: Optional[Conversation]) -> Optional[str]:
        """Resolve session ID for forking."""
        ...

    async def load_subagent_tool_calls(self, agent_id: str) -> List[ToolCallInfo]:
        """Load tool calls for a subagent."""
        ...

    async def load_subagent_final_result(self, agent_id: str) -> Optional[str]:
        """Load final result for a subagent."""
        ...
