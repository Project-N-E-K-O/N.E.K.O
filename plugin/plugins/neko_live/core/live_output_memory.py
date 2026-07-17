"""Recent-output memory helpers for NEKO Live prompt contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


RECENT_REPLY_AVOIDANCE_SIZE = 6
RECENT_REPLY_AVOIDANCE_TEXT_LIMIT = 24


def coerce_recent_reply_values(recent_live_replies: Any) -> list[str]:
    if not recent_live_replies:
        return []
    if isinstance(recent_live_replies, Mapping):
        source = recent_live_replies.values()
    else:
        try:
            source = list(recent_live_replies)
        except TypeError:
            source = [recent_live_replies]
    values: list[str] = []
    for reply in source:
        text = str(reply or "").strip()
        if text:
            values.append(text)
    return values


def compact_recent_reply_values(
    recent_live_replies: Any,
    *,
    limit: int = RECENT_REPLY_AVOIDANCE_SIZE,
    text_limit: int = RECENT_REPLY_AVOIDANCE_TEXT_LIMIT,
) -> list[str]:
    values = coerce_recent_reply_values(recent_live_replies)
    if not values:
        return []
    recent: list[str] = []
    seen: set[str] = set()
    for reply in reversed(values):
        text = str(reply or "").strip().replace("\n", " ")
        if not text:
            continue
        normalized = " ".join(text.casefold().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        if len(text) > text_limit:
            text = text[:text_limit].rstrip() + "..."
        recent.append(text)
        if len(recent) >= limit:
            break
    recent.reverse()
    return recent


def render_compact_recent_reply_avoidance(recent_live_replies: list[str] | None) -> str:
    recent_reply_values = compact_recent_reply_values(recent_live_replies)
    if not recent_reply_values:
        return ""
    return f" Avoid recent: {' / '.join(recent_reply_values)}."


def render_recent_reply_avoidance(recent_live_replies: list[str] | None) -> list[str]:
    recent_reply_values = compact_recent_reply_values(recent_live_replies)
    if not recent_reply_values:
        return []
    lines = [
        "- Recent NEKO Live outputs below are negative examples; do not continue or paraphrase them.",
    ]
    for reply in recent_reply_values:
        text = str(reply or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append(f"  - Avoid repeating: {text}")
    if len(lines) == 1:
        return []
    lines.append("- Answer the current live event from a fresh angle even if the topic is similar.")
    return lines
