"""Rank proactive links from all providers before conversational selection."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from utils.proactive_preferences.preference_model import (
    Profile,
    normalize,
    valid_feature,
)

from .state import _get_source_history_entry, _source_hash


_HOUR = 3600
_WEEK = 7 * 24 * _HOUR
_TOPIC_REVISIT_WINDOW = 8 * _HOUR
_PERSONAL_MATCH_THRESHOLD = 1.5


def _resource_key(link: dict) -> str:
    if key := link.get("dedupe_key"):
        return key
    url = link.get("url", "")
    try:
        address = urlsplit(url)
        host = (address.hostname or "").removeprefix("www.")
    except ValueError:
        return url
    if host == "store.steampowered.com":
        if match := re.match(r"/app/([0-9]+)(?:/|$)", address.path):
            return f"steam:app:{match[1]}"
    if host in {"bilibili.com", "m.bilibili.com"}:
        if match := re.match(r"/video/(BV[0-9A-Za-z]+)(?:/|$)", address.path):
            return f"bilibili:video:{match[1]}"
    if host in {"youtube.com", "m.youtube.com", "youtu.be"}:
        if host == "youtu.be":
            video_id = address.path.strip("/")
        elif address.path.startswith("/shorts/"):
            video_id = address.path.removeprefix("/shorts/").rstrip("/")
        elif address.path == "/watch":
            video_id = parse_qs(address.query).get("v", [""])[0]
        else:
            return url
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return f"youtube:video:{video_id}"
    return url


def _work_keys(link: dict) -> set[str]:
    title = str(link.get("phase1_canonical_title") or link.get("title") or "")[:200]
    names = re.findall(r"《([^《》]{2,80})》", title)
    if _resource_key(link).startswith("steam:app:"):
        names.append(title)
    works = {normalize(name).strip(" 《》") for name in names}
    return {f"work:{name}" for name in works if len(name) >= 3}


def link_cooldown_key(link: dict) -> str:
    url = link.get("dedupe_key") or link["url"]
    keys = {_source_hash(key) for key in ({url, _resource_key(link)} | _work_keys(link)) if key}
    records = [
        (entry.get("ts", 0), key) for key in keys
        if (entry := _get_source_history_entry(key))
    ]
    return max(records, default=(0, _source_hash(url)))[1]


def _topic_keys(link: dict) -> set[str]:
    tags = {
        normalize(tag)
        for field in ("tags", "local_features", "phase1_interests")
        for tag in (link.get(field) or [])
        if isinstance(tag, str)
    }
    return set(sorted(tag for tag in tags if valid_feature(tag))[:3])


def history_aliases(link: dict) -> dict[str, str]:
    """Store URL variants, work identities and short-lived topic memory together."""
    return {
        **{key: "web" for key in _work_keys(link)},
        **{f"topic:{topic}": "web_topic" for topic in _topic_keys(link)},
        **({_resource_key(link): "web"} if link.get("url") else {}),
    }


def _recent_topic(topic: str, now: float) -> bool:
    entry = _get_source_history_entry(_source_hash(f"topic:{topic}"))
    return bool(entry and now - entry.get("ts", 0) < _TOPIC_REVISIT_WINDOW)


def _published_freshness(link: dict, now: float) -> float:
    value = link.get("published_at")
    if isinstance(value, (int, float)) and 0 < value <= now:
        return 0.10 * max(0.0, 1.0 - (now - value) / _WEEK)
    return 0.0


@dataclass
class _Candidate:
    link: dict[str, Any]
    identities: set[str]
    topics: set[str]
    score: float
    priority: bool
    focused: bool
    recent_topic: bool
    trend_rank: int | None


def rank_proactive_links(
    modes: list[str],
    sources: dict[str, Any],
    *,
    total: int,
    profile: Profile | None,
    source_weights: dict[str, float],
    should_skip: Callable[[str], bool],
    prefer_trending: bool = False,
) -> list[dict[str, Any]]:
    """Select a diverse shortlist and occasionally feature a baseline trend."""
    profile = profile if profile is not None else Profile()
    now = time.time()
    candidates: list[_Candidate] = []
    cooldown: dict[str, bool] = {}
    has_personal_match = False
    for mode in modes:
        source = sources.get(mode, {})
        trend_ranks = source.get("trend_ranks", {})
        for index, original in enumerate(source.get("links") or []):
            if not str(original.get("title") or "").strip() or not original.get("url"):
                continue
            matched = profile.match(mode, original)
            affinity = profile.recommendation_score(mode, matched)
            if affinity is None:
                continue
            priority = affinity >= _PERSONAL_MATCH_THRESHOLD
            focused = priority and any(
                profile.focus_weight(mode, topic) > 0
                and (profile.score(mode, {topic}) or 0) > 0
                for topic in matched
            )
            link = {
                **original,
                "mode": mode,
                "phase1_priority": priority,
                "phase1_interests": sorted(matched),
            }
            link.pop("phase1_featured_trend", None)
            url = original.get("dedupe_key") or original["url"]
            identities = {url, _resource_key(link)} | _work_keys(link)
            keys = {_source_hash(identity) for identity in identities if identity}
            if not original.get("verse_selected"):
                cooldown_key = link_cooldown_key(link)
                if cooldown_key not in cooldown:
                    cooldown[cooldown_key] = should_skip(cooldown_key)
                if cooldown[cooldown_key]:
                    continue
            has_personal_match = has_personal_match or affinity > 0
            topics = _topic_keys(link)
            recent_topic = any(_recent_topic(topic, now) for topic in topics)
            trend_rank = (
                trend_ranks.get(
                    original.get("dedupe_key") or original["url"].rstrip("/")
                )
                if not matched and not recent_topic
                else None
            )
            relative_weight = source_weights.get(
                mode, 1 / max(1, len(source_weights))
            ) * len(source_weights)
            score = (
                0.36 / (1 + 0.20 * index)
                + 0.18 * affinity
                + 0.10 * bool(link.get("description_hint"))
                + _published_freshness(link, now)
                + 0.04 * (mode == "personal")
                - 0.16 * max(0, 1 - relative_weight)
                - 0.24 * recent_topic
            )
            candidates.append(
                _Candidate(
                    link,
                    keys,
                    topics,
                    score,
                    priority,
                    focused,
                    recent_topic,
                    trend_rank,
                )
            )

    featured_trend = None
    if prefer_trending and has_personal_match:
        featured_trend = min(
            (item for item in candidates if item.trend_rank is not None),
            key=lambda item: (item.trend_rank, -item.score),
            default=None,
        )

    selected: list[dict] = []
    used: set[str] = set()
    by_mode: Counter[str] = Counter()
    by_topic: Counter[str] = Counter()
    while len(selected) < total:
        eligible = [
            candidate for candidate in candidates if not candidate.identities & used
        ]
        if not eligible:
            break
        winner = max(
            eligible,
            key=lambda item: (
                not selected and item is featured_trend,
                not selected and item.priority and not item.recent_topic,
                not selected and item.focused and not item.recent_topic,
                not selected and item.priority,
                item.score
                - 0.20 * by_mode[item.link["mode"]]
                - 0.22 * max((by_topic[topic] for topic in item.topics), default=0),
            ),
        )
        if winner is featured_trend:
            winner.link["phase1_featured_trend"] = True
        selected.append(winner.link)
        used.update(winner.identities)
        by_mode[winner.link["mode"]] += 1
        by_topic.update(winner.topics)
    return selected
