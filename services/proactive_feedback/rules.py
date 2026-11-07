"""Pure local recommendation preference rules, independent of application wiring."""
from __future__ import annotations

import math
from collections.abc import Mapping


COOLDOWN_SECONDS = 20 * 60
DECLINE_HALF_LIFE_SECONDS = 24 * 60 * 60
MIN_PREFERENCE_FACTOR = 0.2
MAX_DECLINE_COUNT = 32.0
POLICY_RETENTION_SECONDS = 30 * 24 * 60 * 60
POLICY_CATEGORIES = frozenset({
    "music", "news", "community", "video", "personal", "meme", "vision",
    "window", "chat", "web", "work_break", "anti_slack", "reminiscence",
})


def canonical_category(mode: object) -> str | None:
    """Resolve the one legacy source alias without inventing unknown categories."""
    if not isinstance(mode, str):
        return None
    category = "web" if mode == "home" else mode
    return category if category in POLICY_CATEGORIES else None


def decayed_decline_count(state: Mapping | None, now: float) -> float:
    """A first refusal changes timing only; repeated refusals gradually fade."""
    if not state:
        return 0.0
    elapsed = max(0.0, now - state["updated_at"])
    return state["decline_count"] * math.exp2(-elapsed / DECLINE_HALF_LIFE_SECONDS)


def cooldown_active(state: Mapping | None, now: float) -> bool:
    return bool(state and now < state["cooldown_until"])


def preference_factor(state: Mapping | None, now: float) -> float:
    count = decayed_decline_count(state, now)
    return max(MIN_PREFERENCE_FACTOR, 1.0 - 0.2 * max(0.0, count - 1.0))


def apply_policy_decision(
    state: Mapping | None, kind: str, *, now: float, offer_revision: int,
    reason: str = "",
) -> dict | None:
    """Return a new projection only for a newly committed qualifying decision.

    Callers own first-decision admission and atomic persistence. An old offer's
    acceptance never overwrites a refusal it could not have observed.
    """
    revision = state["revision"] if state else 0
    count = decayed_decline_count(state, now)
    deadline = state["cooldown_until"] if state else 0.0
    if kind == "skipped":
        count = min(MAX_DECLINE_COUNT, count + 1.0)
        deadline = max(deadline, now + COOLDOWN_SECONDS)
    elif (
        kind == "accepted" and reason != "countdown_elapsed"
        and offer_revision == revision and count > 0.0
    ):
        count = max(0.0, count - 1.0)
    else:
        return None
    return {
        "revision": revision + 1,
        "cooldown_until": deadline,
        "decline_count": count,
        "updated_at": now,
    }
