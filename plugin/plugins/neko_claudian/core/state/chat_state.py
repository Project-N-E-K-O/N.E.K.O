# Ported from claudian/src/features/chat/state/ChatState.ts
# Original author: Claudian contributors
# License: MIT

"""
ChatState — Central state management for chat UI.

Manages messages, streaming state, tool tracking, usage, and UI elements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ThinkingBlockState:
    """State for a thinking block."""
    content: str = ""
    is_visible: bool = False
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "isVisible": self.is_visible,
            "durationSeconds": self.duration_seconds,
        }


@dataclass
class TodoItem:
    """Todo item from TodoWrite tool."""
    id: str = ""
    subject: str = ""
    description: str = ""
    status: str = "pending"  # "pending" | "in_progress" | "completed" | "deleted"
    active_form: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
        }
        if self.active_form:
            out["activeForm"] = self.active_form
        return out


@dataclass
class QueuedMessage:
    """A message queued for sending."""
    text: str = ""
    images: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "images": self.images,
            "metadata": self.metadata,
        }


@dataclass
class PendingToolCall:
    """A pending tool call awaiting completion."""
    tool_use_id: str = ""
    name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # "pending" | "running" | "completed" | "error"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "toolUseId": self.tool_use_id,
            "name": self.name,
            "input": self.input,
            "status": self.status,
        }


@dataclass
class WriteEditState:
    """State for Write/Edit tool rendering."""
    file_path: str = ""
    is_expanded: bool = False
    diff_lines: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filePath": self.file_path,
            "isExpanded": self.is_expanded,
            "diffLines": self.diff_lines,
            "stats": self.stats,
        }


@dataclass
class ChatMessage:
    """Chat message in state."""
    id: str = ""
    role: str = "user"
    content: str = ""
    timestamp: float = 0.0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    thinking: str = ""
    is_streaming: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "toolCalls": self.tool_calls,
            "thinking": self.thinking,
        }


@dataclass
class ChatStateCallbacks:
    """Callbacks for ChatState events."""
    on_messages_changed: Optional[Callable] = None
    on_streaming_state_changed: Optional[Callable[[bool], None]] = None
    on_conversation_changed: Optional[Callable[[Optional[str]], None]] = None
    on_usage_changed: Optional[Callable] = None
    on_todos_changed: Optional[Callable] = None
    on_attention_changed: Optional[Callable[[bool], None]] = None
    on_auto_scroll_changed: Optional[Callable[[bool], None]] = None


@dataclass
class ChatStateData:
    """Internal state data."""
    messages: List[ChatMessage] = field(default_factory=list)
    is_streaming: bool = False
    cancel_requested: bool = False
    stream_generation: int = 0
    is_creating_conversation: bool = False
    is_switching_conversation: bool = False
    has_pending_conversation_save: bool = False
    current_conversation_id: Optional[str] = None
    queued_message: Optional[QueuedMessage] = None
    current_text_content: str = ""
    current_thinking_state: Optional[ThinkingBlockState] = None
    tool_call_elements: Dict[str, Any] = field(default_factory=dict)
    write_edit_states: Dict[str, WriteEditState] = field(default_factory=dict)
    pending_tools: Dict[str, PendingToolCall] = field(default_factory=dict)
    usage: Optional[Dict[str, Any]] = None
    ignore_usage_updates: bool = False
    current_todos: Optional[List[TodoItem]] = None
    needs_attention: bool = False
    auto_scroll_enabled: bool = True
    response_start_time: Optional[float] = None
    pending_new_session_plan: Optional[str] = None
    plan_file_path: Optional[str] = None
    pre_plan_permission_mode: Optional[str] = None


class ChatState:
    """Central state management for chat UI.

    Ported from claudian/src/features/chat/state/ChatState.ts
    """

    def __init__(self, callbacks: Optional[ChatStateCallbacks] = None):
        self._state = ChatStateData()
        self._callbacks = callbacks or ChatStateCallbacks()

    @property
    def callbacks(self) -> ChatStateCallbacks:
        return self._callbacks

    @callbacks.setter
    def callbacks(self, value: ChatStateCallbacks):
        self._callbacks = value

    # ============================================
    # Messages
    # ============================================

    @property
    def messages(self) -> List[ChatMessage]:
        return list(self._state.messages)

    @messages.setter
    def messages(self, value: List[ChatMessage]):
        self._state.messages = value
        if self._callbacks.on_messages_changed:
            self._callbacks.on_messages_changed()

    def add_message(self, msg: ChatMessage) -> None:
        """Add a message to the state."""
        self._state.messages.append(msg)
        if self._callbacks.on_messages_changed:
            self._callbacks.on_messages_changed()

    def clear_messages(self) -> None:
        """Clear all messages."""
        self._state.messages = []
        if self._callbacks.on_messages_changed:
            self._callbacks.on_messages_changed()

    def truncate_at(self, message_id: str) -> int:
        """Truncate messages at the given message ID. Returns number of removed messages."""
        idx = next((i for i, m in enumerate(self._state.messages) if m.id == message_id), -1)
        if idx == -1:
            return 0
        removed = len(self._state.messages) - idx
        self._state.messages = self._state.messages[:idx]
        if self._callbacks.on_messages_changed:
            self._callbacks.on_messages_changed()
        return removed

    # ============================================
    # Streaming Control
    # ============================================

    @property
    def is_streaming(self) -> bool:
        return self._state.is_streaming

    @is_streaming.setter
    def is_streaming(self, value: bool):
        self._state.is_streaming = value
        if self._callbacks.on_streaming_state_changed:
            self._callbacks.on_streaming_state_changed(value)

    @property
    def cancel_requested(self) -> bool:
        return self._state.cancel_requested

    @cancel_requested.setter
    def cancel_requested(self, value: bool):
        self._state.cancel_requested = value

    @property
    def stream_generation(self) -> int:
        return self._state.stream_generation

    def bump_stream_generation(self) -> int:
        """Increment and return stream generation."""
        self._state.stream_generation += 1
        return self._state.stream_generation

    @property
    def is_creating_conversation(self) -> bool:
        return self._state.is_creating_conversation

    @is_creating_conversation.setter
    def is_creating_conversation(self, value: bool):
        self._state.is_creating_conversation = value

    @property
    def is_switching_conversation(self) -> bool:
        return self._state.is_switching_conversation

    @is_switching_conversation.setter
    def is_switching_conversation(self, value: bool):
        self._state.is_switching_conversation = value

    @property
    def has_pending_conversation_save(self) -> bool:
        return self._state.has_pending_conversation_save

    @has_pending_conversation_save.setter
    def has_pending_conversation_save(self, value: bool):
        self._state.has_pending_conversation_save = value

    # ============================================
    # Conversation
    # ============================================

    @property
    def current_conversation_id(self) -> Optional[str]:
        return self._state.current_conversation_id

    @current_conversation_id.setter
    def current_conversation_id(self, value: Optional[str]):
        self._state.current_conversation_id = value
        if self._callbacks.on_conversation_changed:
            self._callbacks.on_conversation_changed(value)

    # ============================================
    # Queued Message
    # ============================================

    @property
    def queued_message(self) -> Optional[QueuedMessage]:
        return self._state.queued_message

    @queued_message.setter
    def queued_message(self, value: Optional[QueuedMessage]):
        self._state.queued_message = value

    # ============================================
    # Streaming Text State
    # ============================================

    @property
    def current_text_content(self) -> str:
        return self._state.current_text_content

    @current_text_content.setter
    def current_text_content(self, value: str):
        self._state.current_text_content = value

    @property
    def current_thinking_state(self) -> Optional[ThinkingBlockState]:
        return self._state.current_thinking_state

    @current_thinking_state.setter
    def current_thinking_state(self, value: Optional[ThinkingBlockState]):
        self._state.current_thinking_state = value

    # ============================================
    # Tool Tracking Maps (mutable references)
    # ============================================

    @property
    def tool_call_elements(self) -> Dict[str, Any]:
        return self._state.tool_call_elements

    @property
    def write_edit_states(self) -> Dict[str, WriteEditState]:
        return self._state.write_edit_states

    @property
    def pending_tools(self) -> Dict[str, PendingToolCall]:
        return self._state.pending_tools

    # ============================================
    # Usage State
    # ============================================

    @property
    def usage(self) -> Optional[Dict[str, Any]]:
        return self._state.usage

    @usage.setter
    def usage(self, value: Optional[Dict[str, Any]]):
        self._state.usage = value
        if self._callbacks.on_usage_changed:
            self._callbacks.on_usage_changed(value)

    @property
    def ignore_usage_updates(self) -> bool:
        return self._state.ignore_usage_updates

    @ignore_usage_updates.setter
    def ignore_usage_updates(self, value: bool):
        self._state.ignore_usage_updates = value

    # ============================================
    # Current Todos
    # ============================================

    @property
    def current_todos(self) -> Optional[List[TodoItem]]:
        return list(self._state.current_todos) if self._state.current_todos else None

    @current_todos.setter
    def current_todos(self, value: Optional[List[TodoItem]]):
        normalized = value if value and len(value) > 0 else None
        self._state.current_todos = normalized
        if self._callbacks.on_todos_changed:
            self._callbacks.on_todos_changed(normalized)

    # ============================================
    # Attention State
    # ============================================

    @property
    def needs_attention(self) -> bool:
        return self._state.needs_attention

    @needs_attention.setter
    def needs_attention(self, value: bool):
        self._state.needs_attention = value
        if self._callbacks.on_attention_changed:
            self._callbacks.on_attention_changed(value)

    # ============================================
    # Auto-Scroll Control
    # ============================================

    @property
    def auto_scroll_enabled(self) -> bool:
        return self._state.auto_scroll_enabled

    @auto_scroll_enabled.setter
    def auto_scroll_enabled(self, value: bool):
        changed = self._state.auto_scroll_enabled != value
        self._state.auto_scroll_enabled = value
        if changed and self._callbacks.on_auto_scroll_changed:
            self._callbacks.on_auto_scroll_changed(value)

    # ============================================
    # Response Timer State
    # ============================================

    @property
    def response_start_time(self) -> Optional[float]:
        return self._state.response_start_time

    @response_start_time.setter
    def response_start_time(self, value: Optional[float]):
        self._state.response_start_time = value

    @property
    def pending_new_session_plan(self) -> Optional[str]:
        return self._state.pending_new_session_plan

    @pending_new_session_plan.setter
    def pending_new_session_plan(self, value: Optional[str]):
        self._state.pending_new_session_plan = value

    @property
    def plan_file_path(self) -> Optional[str]:
        return self._state.plan_file_path

    @plan_file_path.setter
    def plan_file_path(self, value: Optional[str]):
        self._state.plan_file_path = value

    @property
    def pre_plan_permission_mode(self) -> Optional[str]:
        return self._state.pre_plan_permission_mode

    @pre_plan_permission_mode.setter
    def pre_plan_permission_mode(self, value: Optional[str]):
        self._state.pre_plan_permission_mode = value

    # ============================================
    # Reset Methods
    # ============================================

    def reset_streaming_state(self) -> None:
        """Reset all streaming-related state."""
        self._state.current_text_content = ""
        self._state.current_thinking_state = None
        self._state.is_streaming = False
        self._state.cancel_requested = False
        self._state.response_start_time = None

    def clear_maps(self) -> None:
        """Clear all tracking maps."""
        self._state.tool_call_elements.clear()
        self._state.write_edit_states.clear()
        self._state.pending_tools.clear()

    def reset_for_new_conversation(self) -> None:
        """Reset state for a new conversation."""
        self.clear_messages()
        self.reset_streaming_state()
        self.clear_maps()
        self._state.queued_message = None
        self.usage = None
        self.current_todos = None
        self.auto_scroll_enabled = True

    def get_persisted_messages(self) -> List[ChatMessage]:
        """Get messages for persistence."""
        return self._state.messages

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state to dict."""
        return {
            "messages": [m.to_dict() for m in self._state.messages],
            "isStreaming": self._state.is_streaming,
            "currentConversationId": self._state.current_conversation_id,
            "usage": self._state.usage,
            "currentTodos": [t.to_dict() for t in self._state.current_todos] if self._state.current_todos else None,
            "needsAttention": self._state.needs_attention,
            "autoScrollEnabled": self._state.auto_scroll_enabled,
        }
