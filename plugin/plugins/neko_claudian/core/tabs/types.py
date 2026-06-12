# Ported from claudian/src/features/chat/tabs/types.ts
# Original author: Claudian contributors
# License: MIT

"""
Tab type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# Constants
MIN_TABS = 1
MAX_TABS = 10
DEFAULT_MAX_TABS = 5

# Tab ID type
TabId = str


@dataclass
class TabData:
    """Data for a single tab."""
    id: str = ""
    conversation_id: Optional[str] = None
    provider_id: str = "claude"
    draft_model: Optional[str] = None
    lifecycle_state: str = "blank"  # "blank" | "active" | "inactive"
    state: Any = None  # ChatState instance
    service: Any = None  # ChatRuntime instance
    service_initialized: bool = False
    controllers: Dict[str, Any] = field(default_factory=dict)
    ui: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "providerId": self.provider_id,
            "draftModel": self.draft_model,
            "lifecycleState": self.lifecycle_state,
        }


@dataclass
class TabBarItem:
    """Data for rendering a tab bar item."""
    id: str = ""
    index: int = 0
    title: str = ""
    provider_id: str = "claude"
    is_active: bool = False
    is_streaming: bool = False
    needs_attention: bool = False
    can_close: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "title": self.title,
            "providerId": self.provider_id,
            "isActive": self.is_active,
            "isStreaming": self.is_streaming,
            "needsAttention": self.needs_attention,
            "canClose": self.can_close,
        }


@dataclass
class PersistedTabState:
    """Persisted state for a single tab."""
    tab_id: str = ""
    conversation_id: Optional[str] = None
    draft_model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"tabId": self.tab_id}
        if self.conversation_id:
            out["conversationId"] = self.conversation_id
        if self.draft_model:
            out["draftModel"] = self.draft_model
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PersistedTabState:
        return cls(
            tab_id=data.get("tabId", ""),
            conversation_id=data.get("conversationId"),
            draft_model=data.get("draftModel"),
        )


@dataclass
class PersistedTabManagerState:
    """Persisted state for the tab manager."""
    open_tabs: List[PersistedTabState] = field(default_factory=list)
    active_tab_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "openTabs": [t.to_dict() for t in self.open_tabs],
            "activeTabId": self.active_tab_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PersistedTabManagerState:
        return cls(
            open_tabs=[PersistedTabState.from_dict(t) for t in data.get("openTabs", [])],
            active_tab_id=data.get("activeTabId"),
        )


@dataclass
class TabManagerCallbacks:
    """Callbacks for tab manager events."""
    on_tab_created: Optional[Callable[[TabData], None]] = None
    on_tab_closed: Optional[Callable[[TabId], None]] = None
    on_tab_switched: Optional[Callable[[Optional[TabId], TabId], None]] = None
    on_tab_streaming_changed: Optional[Callable[[TabId, bool], None]] = None
    on_tab_title_changed: Optional[Callable[[TabId, str], None]] = None
    on_tab_attention_changed: Optional[Callable[[TabId, bool], None]] = None
    on_tab_conversation_changed: Optional[Callable[[TabId, Optional[str]], None]] = None
    on_tab_provider_changed: Optional[Callable[[TabId, str], None]] = None


@dataclass
class ForkContext:
    """Context for forking a conversation."""
    provider_id: str = "claude"
    source_session_id: Optional[str] = None
    resume_at: Optional[str] = None
    source_provider_state: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    source_title: Optional[str] = None
    fork_at_user_message: Optional[int] = None
    current_note: Optional[str] = None
