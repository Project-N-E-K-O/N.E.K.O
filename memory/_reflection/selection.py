"""Pure selection and mention-suppression rules for reflections."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any


def in_window(timestamp: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(timestamp) >= cutoff
    except (ValueError, TypeError):
        return False


def record_mentions(
    reflections: list[dict],
    response_text: str,
    *,
    stop_names: list[str] | None,
    now: datetime,
    window_hours: int,
    mention_limit: int,
    is_mentioned: Callable[..., bool],
    is_in_window: Callable[[str, datetime], bool] = in_window,
) -> bool:
    """Apply mention counters and suppression to confirmed reflections."""
    now_iso = now.isoformat()
    cutoff = now - timedelta(hours=window_hours)
    changed = False
    for reflection in reflections:
        if not isinstance(reflection, dict) or reflection.get("status") != "confirmed":
            continue
        if not is_mentioned(
            reflection.get("text", ""), response_text, stop_names=stop_names
        ):
            continue
        mentions = reflection.get("recent_mentions", [])
        mentions.append(now_iso)
        mentions = [
            timestamp for timestamp in mentions if is_in_window(timestamp, cutoff)
        ]
        reflection["recent_mentions"] = mentions
        if not reflection.get("suppress") and len(mentions) > mention_limit:
            reflection["suppress"] = True
            reflection["suppressed_at"] = now_iso
        changed = True
    return changed


def update_suppressions(
    reflections: list[dict],
    *,
    now: datetime,
    window_hours: int,
    cooldown_hours: int,
    on_bad_timestamp: Callable[[str, Exception], None],
    is_in_window: Callable[[str, datetime], bool] = in_window,
) -> bool:
    """Prune mention windows and lift expired suppression flags."""
    cutoff = now - timedelta(hours=window_hours)
    changed = False
    for reflection in reflections:
        if not isinstance(reflection, dict):
            continue
        mentions = reflection.get("recent_mentions", [])
        cleaned = [
            timestamp for timestamp in mentions if is_in_window(timestamp, cutoff)
        ]
        if len(cleaned) != len(mentions):
            reflection["recent_mentions"] = cleaned
            changed = True
        if not reflection.get("suppress"):
            continue
        suppressed_at = reflection.get("suppressed_at")
        if not suppressed_at:
            continue
        try:
            hours_since = (
                now - datetime.fromisoformat(suppressed_at)
            ).total_seconds() / 3600
        except (ValueError, TypeError) as error:
            on_bad_timestamp(suppressed_at, error)
            continue
        if hours_since >= cooldown_hours:
            reflection["suppress"] = False
            reflection["suppressed_at"] = None
            reflection["recent_mentions"] = []
            changed = True
    return changed


def filter_active_confirmed(
    reflections: list[dict],
    *,
    now: datetime,
    score: Callable[[dict, datetime], float],
) -> list[dict]:
    return [
        reflection
        for reflection in reflections
        if reflection.get("status") == "confirmed"
        and not reflection.get("suppress")
        and score(reflection, now) > 0
    ]


def followup_render_key(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) > 120:
        return text[:120].rstrip() + "..."
    return text


def filter_followup_candidates(
    pending: list[dict],
    *,
    now: datetime,
    score: Callable[[dict, datetime], float],
    render_key: Callable[[Any], str],
    weighted: bool,
    top_k: int,
    weight_base: float,
    sample: Callable[[list[dict], list[float], int], list[dict]],
) -> list[dict]:
    if not pending:
        return []
    eligible: list[dict] = []
    seen_text_keys: set[str] = set()
    for reflection in pending:
        next_eligible = reflection.get("next_eligible_at")
        if next_eligible:
            try:
                if datetime.fromisoformat(next_eligible) > now:
                    continue
            except (ValueError, TypeError):
                # Invalid cooldown metadata is treated as eligible, matching
                # the legacy fail-open behavior for hand-edited data.
                pass
        if score(reflection, now) < 0:
            continue
        text_key = render_key(reflection.get("text"))
        if not text_key or text_key in seen_text_keys:
            continue
        seen_text_keys.add(text_key)
        eligible.append(reflection)
    if not weighted or len(eligible) <= top_k:
        return eligible[:top_k]
    weights = [max(score(item, now), 0.0) + weight_base for item in eligible]
    return sample(eligible, weights, top_k)
