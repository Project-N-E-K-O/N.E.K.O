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
    # Group-memory policy at receipt time: retroactive review replays this
    # message later, and consent belongs to when it was SAID — a message
    # ignored during an opted-out era must not enter scoped history just
    # because the toggle is ON at replay time. Default False fails closed
    # for legacy rows that predate the field.
    group_memory_enabled_at_receipt: bool = False
    # 合成事件标记（与 pipeline 的 _synthetic_source 同源，如
    # group_join_notice）：名义 sender 并没有真的说话。读侧"最近发言人"
    # 名单靠它排除事件关联用户。默认空串 = 真实发言（legacy 行同判）。
    synthetic_source: str = ""
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
