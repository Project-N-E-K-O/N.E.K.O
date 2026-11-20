"""Local ranking features derived from proactive recommendation feedback."""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from unicodedata import normalize

from .preference_model import (
    SCOPES,
    SYNC_SNAPSHOT_LIMIT,
    PreferenceEvent,
    Profile,
    applicable_scopes,
    normalize as normalize_feature,
)
from .search_policy import LOCAL_ONLY_SCOPES, SearchPermission, may_search_phrase

CHAT_INTEREST_KINDS = frozenset({"explicit", "inferred", "exclude", "allow"})
CHAT_INTEREST_SCOPES = SCOPES - LOCAL_ONLY_SCOPES


def normalize_labels(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            label
            for value in values[:12]
            if isinstance(value, str)
            if (
                label := normalize("NFKC", value[:120])
                .strip()
                .lstrip("#")
                .strip()
                .casefold()
            )
        )
    )


@dataclass(frozen=True)
class ChatPreferenceEvidence:
    at: int
    events: list[PreferenceEvent]
    permissions: dict[str, SearchPermission]


@dataclass
class LocalPreferences:
    community_tags: dict[str, float] = field(default_factory=dict)
    music_artists: dict[str, float] = field(default_factory=dict)
    profile: Profile = field(default_factory=Profile)
    search_permissions: dict[tuple[str, str], SearchPermission] = field(
        default_factory=dict
    )
    explicit_topics: set[tuple[str, str]] = field(default_factory=set)
    analyzed_chat_sources: frozenset[str] = frozenset()

    def search_permission(self, scope: str, feature: str) -> SearchPermission | None:
        permissions = [
            permission
            for category in applicable_scopes(scope)
            if (permission := self.search_permissions.get((category, feature)))
            is not None
        ]
        if any(not permission.public for permission in permissions):
            return SearchPermission(False)
        return permissions[-1] if permissions else None

    @classmethod
    def from_observations(
        cls, observations: Iterable[tuple[dict[str, Any], str, float, float]]
    ) -> "LocalPreferences":
        tags: defaultdict[str, float] = defaultdict(float)
        artists: defaultdict[str, float] = defaultdict(float)
        for track, decision, listened_seconds, age_days in observations:
            if decision == "accepted":
                weight = (2 if listened_seconds >= 30 else 1) * 0.5 ** (age_days / 14)
            else:
                weight = -0.5 * 0.5 ** (age_days / 2)
            if track.get("category", "music") == "music":
                for artist in normalize_labels([track.get("artist")]):
                    artists[artist] += weight
            elif track.get("category") == "community":
                for tag in normalize_labels(track.get("local_tags") or []):
                    tags[tag] += weight
        return cls(dict(tags), dict(artists))

    def rank_community(self, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def score(post: dict[str, Any]) -> float:
            return sum(
                self.community_tags.get(tag, 0)
                for tag in normalize_labels(post.get("tags") or [])
            )

        if self.profile.features("community"):
            return self.profile.rank("community", posts)
        return sorted(posts, key=score, reverse=True)

    def rank_music(self, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def score(track: dict[str, Any]) -> float:
            return sum(
                self.music_artists.get(artist, 0)
                for artist in normalize_labels([track.get("artist")])
            )

        if self.profile.features("music"):
            return self.profile.rank("music", tracks)
        return sorted(tracks, key=score, reverse=True)


@dataclass
class RecommendationContext:
    preferences: LocalPreferences = field(default_factory=LocalPreferences)
    community: str = ""
    bearer: str = field(default="", repr=False)
    account_id: str = field(default="", repr=False)
    community_identity_known: bool = True
    save_chat: Callable[[dict[str, ChatPreferenceEvidence]], Awaitable[None]] | None = (
        field(default=None, repr=False)
    )
    load_chat_sources: Callable[[str], Awaitable[dict[str, str]]] | None = field(
        default=None, repr=False
    )

    @property
    def allows_guest_community(self) -> bool:
        return self.community_identity_known and not (self.community or self.bearer)


@dataclass(frozen=True)
class SyncedChatInterest:
    event: PreferenceEvent
    game: bool


def parse_synced_chat_interests(payload: object, now: int) -> list[SyncedChatInterest]:
    if not isinstance(payload, list) or len(payload) > SYNC_SNAPSHOT_LIMIT:
        raise ValueError("invalid_chat_interests")
    events = []
    fields = ("id", "feature", "scope", "kind", "direction", "at")
    required = set(fields)
    for value in payload:
        if not isinstance(value, dict) or set(value) - {"game"} != required:
            raise ValueError("invalid_chat_interest")
        event_id, feature, scope, kind, direction, at = (value[key] for key in fields)
        game = value.get("game", False)
        if (
            not all(isinstance(item, str) for item in (event_id, feature, scope, kind))
            or len(event_id) != 64
            or any(char not in "0123456789abcdef" for char in event_id)
            or not may_search_phrase(feature)
            or feature != normalize_feature(feature)
            or scope not in CHAT_INTEREST_SCOPES
            or kind not in CHAT_INTEREST_KINDS
            or type(direction) is not int
            or direction not in {-1, 1}
            or (kind == "allow" and direction != 1)
            or (kind == "exclude" and direction != -1)
            or type(at) is not int
            or not 0 <= at <= now + 300
            or type(game) is not bool
        ):
            raise ValueError("invalid_chat_interest")
        events.append(
            SyncedChatInterest(
                PreferenceEvent(event_id, feature, scope, kind, direction, at),
                game,
            )
        )
    return events
