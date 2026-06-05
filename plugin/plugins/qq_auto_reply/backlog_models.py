from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal


BacklogCategory = str
ConversationType = Literal["private", "group"]
ReviewStatus = Literal["unreviewed", "reviewed"]


@dataclass(slots=True)
class QQBacklogMessage:
    conversation_key: str
    conversation_type: ConversationType
    source_id: str
    sender_id: str
    sender_name: str
    text: str
    message_id: str
    timestamp: int
    group_id: str | None = None
    group_level: str = "none"
    permission_level: str = "none"
    is_at_bot: bool = False
    category: BacklogCategory = "unknown"
    review_status: ReviewStatus = "unreviewed"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QQBacklogConversation:
    conversation_key: str
    conversation_type: ConversationType
    source_id: str
    display_name: str
    group_id: str | None = None
    unread_count: int = 0
    last_message_at: int = 0
    last_message_id: str = ""
    last_reviewed_at: int = 0
    last_reviewed_message_id: str = ""
    last_summary_at: int = 0
    last_notified_at: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QQGroupBacklog:
    group_id: str
    display_name: str
    unread_count: int = 0
    label_counts: dict[str, int] = field(default_factory=dict)
    last_message_at: int = 0
    last_message_id: str = ""
    conversation_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
