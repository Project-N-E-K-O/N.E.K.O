"""Versioned scoring shared by the desktop and community proactive surfaces."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

VERSION = 1
DAY = 86_400
RECENT_WINDOW = 30 * DAY
_FOCUS_WINDOW_DAYS = 7
_FOCUS_HALF_LIFE_DAYS = 2
SYNC_BATCH_LIMIT = 64
SYNC_SNAPSHOT_LIMIT = 2000
DURABLE_KINDS = frozenset({"explicit", "exclude", "allow"})
SCOPES = frozenset(
    {
        "all",
        "community",
        "news",
        "video",
        "music",
        "meme",
        "home",
        "personal",
        "vision",
        "window",
        "web",
        "mini_game",
        "chat",
    }
)
Kind = Literal["explicit", "inferred", "view", "skip", "played", "exclude", "allow"]


def normalize(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", value).casefold().strip().lstrip("#").split()
    )


def valid_feature(value: str) -> bool:
    return (
        2 <= len(value) <= 48
        and len(value.split()) <= 6
        and all(char.isalnum() or char in " -_+&./" for char in value)
        and any(char.isalpha() for char in value)
    )


_PRIVATE_OR_SENSITIVE = re.compile(
    r"[@/]|\d{5,}|密码|口令|身份证|住址|病历|诊断|用药|医疗|心理咨询|心理治疗|"
    r"抑郁症|焦虑症|糖尿病|怀孕|流产|艾滋病|内部项目|客户名单|"
    r"\b(?:address|password|passport|patient|diagnosis|therapy|depression|"
    r"pregnancy|abortion|diabetes|hiv|confidential|internal project|client list)\b",
    re.IGNORECASE,
)


def may_search_phrase(phrase: str) -> bool:
    return valid_feature(phrase) and not _PRIVATE_OR_SENSITIVE.search(phrase)


@dataclass(frozen=True)
class PreferenceEvent:
    id: str
    feature: str
    scope: str
    kind: Kind
    direction: Literal[-1, 1]
    at: int


@dataclass
class Profile:
    weights: dict[tuple[str, str], float] = field(default_factory=dict)
    exclusions: set[tuple[str, str]] = field(default_factory=set)
    recent_focus: dict[tuple[str, str], float] = field(default_factory=dict)

    @classmethod
    def from_events(cls, events: Iterable[PreferenceEvent], now: int) -> Profile:
        explicit: dict[tuple[str, str], PreferenceEvent] = {}
        exclusions: dict[tuple[str, str], PreferenceEvent] = {}
        inferred: defaultdict[tuple[str, str], float] = defaultdict(float)
        behavior: defaultdict[tuple[str, str], float] = defaultdict(float)
        recent_focus: dict[tuple[str, str], float] = {}
        for event in sorted(events, key=lambda event: (event.at, event.id)):
            if event.kind not in DURABLE_KINDS and now - event.at > RECENT_WINDOW:
                continue
            key = event.scope, event.feature
            age = max(0, now - event.at) / DAY
            if event.kind == "explicit":
                explicit[key] = event
            elif event.kind in {"exclude", "allow"}:
                exclusions[key] = event
            elif event.kind == "inferred":
                inferred[key] += event.direction * 0.5 * 0.5 ** (age / 7)
                if event.direction < 0:
                    recent_focus.pop(key, None)
                elif age < _FOCUS_WINDOW_DAYS:
                    recent_focus[key] = 3 * 0.5 ** (age / _FOCUS_HALF_LIFE_DAYS)
            else:
                weight, half_life = {
                    "view": (0.5, 14),
                    "skip": (-0.25, 2),
                    "played": (1.0, 14),
                }[event.kind]
                behavior[key] += weight * 0.5 ** (age / half_life)
        return cls(
            {
                key: (3 * explicit[key].direction if key in explicit else 0)
                + max(-1, min(1, inferred[key]))
                + max(-1, min(1, behavior[key]))
                for key in explicit.keys() | inferred.keys() | behavior.keys()
            },
            {key for key, event in exclusions.items() if event.kind == "exclude"},
            recent_focus,
        )

    def features(self, scope: str) -> set[str]:
        return {
            feature
            for category, feature in self.weights.keys() | self.exclusions
            if category in applicable_scopes(scope)
        }

    def match(self, scope: str, candidate: Mapping) -> set[str]:
        return matching_features(self.features(scope), candidate)

    def score(self, scope: str, features: set[str]) -> float | None:
        scopes = applicable_scopes(scope)
        if any(
            (category, name) in self.exclusions
            for name in features
            for category in scopes
        ):
            return None
        score = sum(
            self.weights.get((category, name), 0)
            for category in scopes
            for name in sorted(features)
        )
        return max(-6, min(6, score))

    def focus_weight(self, scope: str, feature: str) -> float:
        return max(
            self.recent_focus.get((category, feature), 0)
            for category in applicable_scopes(scope)
        )

    def recommendation_score(self, scope: str, features: set[str]) -> float | None:
        affinity = self.score(scope, features)
        if affinity is None or affinity <= 0:
            return affinity
        focus = max(
            (
                self.focus_weight(scope, feature)
                for feature in features
                if (self.score(scope, {feature}) or 0) > 0
            ),
            default=0,
        )
        return affinity + focus

    def rank(self, scope: str, candidates: list[dict]) -> list[dict]:
        features = self.features(scope)
        if not features:
            return candidates
        scored = [
            (self.recommendation_score(scope, self.match(scope, item)), item)
            for item in candidates
        ]
        return [
            item
            for _, item in sorted(
                ((score, item) for score, item in scored if score is not None),
                key=lambda row: row[0],
                reverse=True,
            )
        ]


def applicable_scopes(scope: str) -> tuple[str, ...]:
    if scope in {"news", "video", "home", "community", "personal", "window"}:
        return ("all", "web", scope)
    return ("all", scope) if scope != "all" else ("all",)


def matching_features(known: Iterable[str], candidate: Mapping) -> set[str]:
    tag_fields = [
        candidate.get(field) for field in ("tags", "genres", "local_features")
    ]
    labels = {
        normalize(item)
        for values in tag_fields
        if isinstance(values, (list, tuple, set))
        for item in values
        if isinstance(item, str)
    }
    titles = [
        normalize(str(candidate.get(key) or "")[:600])
        for key in (
            "title",
            "name",
            "artist",
            "author",
            "local_author",
            "source",
            "summary",
            "description_hint",
            "content",
        )
    ]
    return {
        feature
        for feature in known
        if feature in labels
        or any(
            (
                feature in title
                if any(ord(char) >= 0x2E80 for char in feature)
                else re.search(r"(?<!\w)" + re.escape(feature) + r"(?!\w)", title)
            )
            for title in titles
            if title
        )
    }
