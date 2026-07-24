"""Viewer identity and profile contracts for NEKO Live memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts_public import public_bool, public_int, public_text
from .contracts_types import utc_now_iso


@dataclass
class ViewerIdentity:
    uid: str
    nickname: str
    name: str = ""
    email: str = ""
    avatar_url: str = ""
    avatar_bytes: bytes | None = None
    avatar_mime: str = ""
    source_url: str = ""
    fetched: bool = False
    error: str = ""
    # Avatar metadata helps modules avoid inventing visual details.
    is_default_avatar: bool = False
    is_animated_avatar: bool = False
    pendant: str = ""

    @property
    def avatar_vision_ok(self) -> bool:
        """Whether an actual avatar frame is available for vision input."""
        return bool(self.avatar_bytes)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "uid": public_text(self.uid),
            "nickname": public_text(self.nickname),
            "name": public_text(self.name) or public_text(self.nickname),
            "avatar_url": public_text(self.avatar_url),
            "avatar_mime": public_text(self.avatar_mime),
            "has_avatar": bool(self.avatar_bytes),
            "is_default_avatar": public_bool(self.is_default_avatar),
            "is_animated_avatar": public_bool(self.is_animated_avatar),
            "pendant": public_text(self.pendant),
            "source_url": public_text(self.source_url),
            "fetched": public_bool(self.fetched),
            "error": public_text(self.error),
        }


@dataclass
class ViewerProfile:
    uid: str
    nickname: str
    avatar_url: str = ""
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_seen_at: str = field(default_factory=utc_now_iso)
    roast_count: int = 0
    last_roast_at: str = ""
    last_result: str = ""
    danmaku_count: int = 0
    preference_tags: dict[str, int] = field(default_factory=dict)
    favorite_topics: dict[str, int] = field(default_factory=dict)
    running_jokes: dict[str, int] = field(default_factory=dict)
    interaction_style: str = ""
    response_preference: str = ""
    last_interaction_summary: str = ""
    impression_summary: str = ""
    avoid_guidance: str = ""
    last_interaction_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": public_text(self.uid),
            "nickname": public_text(self.nickname),
            "avatar_url": public_text(self.avatar_url),
            "first_seen_at": public_text(self.first_seen_at),
            "last_seen_at": public_text(self.last_seen_at),
            "roast_count": public_int(self.roast_count, default=0, minimum=0),
            "last_roast_at": public_text(self.last_roast_at),
            "last_result": public_text(self.last_result),
            "danmaku_count": public_int(self.danmaku_count, default=0, minimum=0),
            "preference_tags": {
                public_text(key, max_len=48): public_int(value, default=0, minimum=0)
                for key, value in self.preference_tags.items()
                if public_text(key, max_len=48)
            },
            "favorite_topics": {
                public_text(key, max_len=48): public_int(value, default=0, minimum=0)
                for key, value in self.favorite_topics.items()
                if public_text(key, max_len=48)
            },
            "running_jokes": {
                public_text(key, max_len=48): public_int(value, default=0, minimum=0)
                for key, value in self.running_jokes.items()
                if public_text(key, max_len=48)
            },
            "interaction_style": public_text(self.interaction_style, max_len=48),
            "response_preference": public_text(self.response_preference, max_len=180),
            "last_interaction_summary": public_text(self.last_interaction_summary, max_len=160),
            "impression_summary": public_text(self.impression_summary, max_len=180),
            "avoid_guidance": public_text(self.avoid_guidance, max_len=180),
            "last_interaction_at": public_text(self.last_interaction_at),
        }
