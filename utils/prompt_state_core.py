from __future__ import annotations

import json
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 3

FAILURE_COOLDOWN_MS = 2 * 60 * 60 * 1000
PROMPT_PENDING_GUARD_MS = 5 * 60 * 1000
MAX_PROMPT_SHOWS = 2

MAX_FOREGROUND_DELTA_MS = 30 * 60 * 1000
MAX_COUNTER_DELTA = 20

MAX_ALLOWED_PROMPT_FOREGROUND_MS = 12 * 60 * 60 * 1000
MIN_ALLOWED_LATER_COOLDOWN_MS = 5 * 60 * 1000
MAX_ALLOWED_LATER_COOLDOWN_MS = 30 * 24 * 60 * 60 * 1000
MIN_ALLOWED_FAILURE_COOLDOWN_MS = 1 * 60 * 1000
MAX_ALLOWED_FAILURE_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000
MIN_ALLOWED_MAX_PROMPT_SHOWS = 1
MAX_ALLOWED_MAX_PROMPT_SHOWS = 10

VALID_PROMPT_STATUSES = {
    "observing",
    "prompted",
    "deferred",
    "started",
    "completed",
    "never",
    "error",
}

PROMPT_FUNNEL_KEYS = (
    "issued",
    "shown",
    "later",
    "never",
    "accept",
    "started",
    "completed",
    "failed",
)

DEFAULT_PROMPT_FUNNEL = {
    key: 0 for key in PROMPT_FUNNEL_KEYS
}

DEFAULT_PROMPT_FLOW_STATE = {
    "schema_version": SCHEMA_VERSION,
    "prompt_kind": "",
    "first_seen_at": 0,
    "foreground_ms": 0,
    "home_interactions": 0,
    "last_weak_home_interaction_at": 0,
    "chat_turns": 0,
    "voice_sessions": 0,
    "status": "observing",
    "shown_count": 0,
    "last_shown_at": 0,
    "active_prompt_token": "",
    "active_prompt_issued_at": 0,
    "last_acknowledged_prompt_token": "",
    "last_decision_prompt_token": "",
    "deferred_until": 0,
    "never_remind": False,
    "accepted_at": 0,
    "started_at": 0,
    "started_via_prompt": False,
    "completed_at": 0,
    "last_error": "",
    "funnel_counts": DEFAULT_PROMPT_FUNNEL,
}

PROMPT_FLOW_SNAPSHOT_FIELDS = (
    "schema_version",
    "prompt_kind",
    "first_seen_at",
    "foreground_ms",
    "home_interactions",
    "last_weak_home_interaction_at",
    "chat_turns",
    "voice_sessions",
    "status",
    "shown_count",
    "last_shown_at",
    "active_prompt_token",
    "active_prompt_issued_at",
    "last_acknowledged_prompt_token",
    "last_decision_prompt_token",
    "deferred_until",
    "never_remind",
    "accepted_at",
    "started_at",
    "started_via_prompt",
    "completed_at",
    "last_error",
    "funnel_counts",
)

PUBLIC_PROMPT_FLOW_SNAPSHOT_FIELDS = (
    "status",
    "shown_count",
    "deferred_until",
    "never_remind",
    "accepted_at",
    "started_at",
    "completed_at",
    "last_error",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def clamp_int(value: Any, *, default: int = 0, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def clean_str(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > limit:
        return text[:limit]
    return text


def load_state_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None
    return data


def normalize_funnel_counts(raw_counts: Any) -> dict[str, int]:
    normalized = deepcopy(DEFAULT_PROMPT_FUNNEL)
    if not isinstance(raw_counts, dict):
        return normalized

    for key in PROMPT_FUNNEL_KEYS:
        normalized[key] = clamp_int(raw_counts.get(key))
    return normalized


def normalize_prompt_flow_state(
    raw_state: Any,
    *,
    defaults: dict[str, Any],
    extra_normalizer: Callable[[dict[str, Any]], None] | None = None,
    status_resolver: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    state = deepcopy(defaults)
    if isinstance(raw_state, dict):
        for key in state:
            if key in raw_state:
                state[key] = raw_state[key]

    state["schema_version"] = SCHEMA_VERSION
    default_prompt_kind = clean_str(defaults.get("prompt_kind"), limit=64).lower()
    prompt_kind = clean_str(state.get("prompt_kind"), limit=64).lower()
    state["prompt_kind"] = prompt_kind or default_prompt_kind
    state["first_seen_at"] = clamp_int(state.get("first_seen_at"))
    state["foreground_ms"] = clamp_int(state.get("foreground_ms"))
    state["home_interactions"] = clamp_int(state.get("home_interactions"))
    state["last_weak_home_interaction_at"] = clamp_int(state.get("last_weak_home_interaction_at"))
    state["chat_turns"] = clamp_int(state.get("chat_turns"))
    state["voice_sessions"] = clamp_int(state.get("voice_sessions"))
    state["shown_count"] = clamp_int(state.get("shown_count"))
    state["last_shown_at"] = clamp_int(state.get("last_shown_at"))
    state["active_prompt_token"] = clean_str(state.get("active_prompt_token"), limit=128)
    state["active_prompt_issued_at"] = clamp_int(state.get("active_prompt_issued_at"))
    state["last_acknowledged_prompt_token"] = clean_str(
        state.get("last_acknowledged_prompt_token"),
        limit=128,
    )
    state["last_decision_prompt_token"] = clean_str(
        state.get("last_decision_prompt_token"),
        limit=128,
    )
    state["deferred_until"] = clamp_int(state.get("deferred_until"))
    state["never_remind"] = bool(state.get("never_remind"))
    state["accepted_at"] = clamp_int(state.get("accepted_at"))
    state["started_at"] = clamp_int(state.get("started_at"))
    state["started_via_prompt"] = bool(state.get("started_via_prompt"))
    state["completed_at"] = clamp_int(state.get("completed_at"))
    state["last_error"] = clean_str(state.get("last_error"))
    state["funnel_counts"] = normalize_funnel_counts(state.get("funnel_counts"))

    status = clean_str(state.get("status"), limit=32).lower()
    default_status = clean_str(defaults.get("status"), limit=32).lower() or "observing"
    state["status"] = status if status in VALID_PROMPT_STATUSES else default_status

    if extra_normalizer:
        extra_normalizer(state)
    if status_resolver:
        status_resolver(state)
    return state


def build_prompt_flow_snapshot(normalized_state: dict[str, Any], *, extra_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for field in (*PROMPT_FLOW_SNAPSHOT_FIELDS, *extra_fields):
        value = normalized_state.get(field)
        snapshot[field] = deepcopy(value) if isinstance(value, (dict, list)) else value
    return snapshot


def build_public_prompt_flow_snapshot(
    normalized_state: dict[str, Any],
    *,
    extra_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for field in (*PUBLIC_PROMPT_FLOW_SNAPSHOT_FIELDS, *extra_fields):
        value = normalized_state.get(field)
        snapshot[field] = deepcopy(value) if isinstance(value, (dict, list)) else value
    return snapshot


def increment_funnel_count(state: dict[str, Any], key: str, amount: int = 1) -> bool:
    if key not in PROMPT_FUNNEL_KEYS or amount <= 0:
        return False

    counts = state.get("funnel_counts")
    if not isinstance(counts, dict):
        counts = normalize_funnel_counts(counts)
        state["funnel_counts"] = counts

    old_value = clamp_int(counts.get(key))
    new_value = old_value + amount
    if old_value == new_value:
        return False
    counts[key] = new_value
    return True


def clear_active_prompt_token(state: dict[str, Any]) -> bool:
    changed = False
    if state.get("active_prompt_token"):
        state["active_prompt_token"] = ""
        changed = True
    if clamp_int(state.get("active_prompt_issued_at")) != 0:
        state["active_prompt_issued_at"] = 0
        changed = True
    return changed


def clear_started_via_prompt_state(state: dict[str, Any]) -> bool:
    changed = False
    if state.get("started_via_prompt"):
        state["started_via_prompt"] = False
        changed = True
    if "started_via_prompt_at" in state and clamp_int(state.get("started_via_prompt_at")) != 0:
        state["started_via_prompt_at"] = 0
        changed = True
    return changed


def ensure_active_prompt_token(state: dict[str, Any], now_ms_value: int) -> tuple[str, bool]:
    current_token = clean_str(state.get("active_prompt_token"), limit=128)
    current_issued_at = clamp_int(state.get("active_prompt_issued_at"))

    if (
        current_token
        and current_issued_at > 0
        and (now_ms_value - current_issued_at) < PROMPT_PENDING_GUARD_MS
    ):
        return current_token, False

    state["active_prompt_token"] = uuid.uuid4().hex
    state["active_prompt_issued_at"] = now_ms_value
    return state["active_prompt_token"], True


def apply_prompt_shown_state(
    state: dict[str, Any],
    prompt_token: str,
    now_ms_value: int,
    *,
    max_prompt_shows: int,
) -> bool:
    changed = False
    if clamp_int(state.get("shown_count")) < max_prompt_shows:
        state["shown_count"] = clamp_int(state.get("shown_count")) + 1
        changed = True
    if clamp_int(state.get("last_shown_at")) != now_ms_value:
        state["last_shown_at"] = now_ms_value
        changed = True
    if clean_str(state.get("status"), limit=32).lower() != "prompted":
        state["status"] = "prompted"
        changed = True
    if clean_str(state.get("last_error")):
        state["last_error"] = ""
        changed = True
    if clean_str(state.get("last_acknowledged_prompt_token"), limit=128) != prompt_token:
        state["last_acknowledged_prompt_token"] = prompt_token
        changed = True
    changed |= increment_funnel_count(state, "shown")
    changed |= clear_active_prompt_token(state)
    return changed


def ack_prompt_token_if_needed(
    state: dict[str, Any],
    prompt_token: str,
    now_ms_value: int,
    *,
    normalizer: Callable[[Any], dict[str, Any]],
    max_prompt_shows: int,
) -> tuple[dict[str, Any], bool, bool]:
    normalized = normalizer(state)
    token = clean_str(prompt_token, limit=128)
    if not token:
        raise ValueError("invalid prompt_token")

    if token == normalized["last_acknowledged_prompt_token"]:
        return normalized, False, True

    if token != normalized["active_prompt_token"]:
        raise ValueError("invalid prompt_token")

    changed = apply_prompt_shown_state(
        normalized,
        token,
        now_ms_value,
        max_prompt_shows=max_prompt_shows,
    )
    return normalized, changed, False


def apply_started_state(state: dict[str, Any], now_ms_value: int) -> bool:
    changed = False
    if clamp_int(state.get("started_at")) <= 0:
        state["started_at"] = now_ms_value
        changed = True
    if clean_str(state.get("status"), limit=32).lower() != "started":
        state["status"] = "started"
        changed = True
    if clamp_int(state.get("deferred_until")) != 0:
        state["deferred_until"] = 0
        changed = True
    if clean_str(state.get("last_error")):
        state["last_error"] = ""
        changed = True
    changed |= clear_active_prompt_token(state)
    return changed


def apply_completed_state(state: dict[str, Any], now_ms_value: int) -> bool:
    changed = False
    if clamp_int(state.get("completed_at")) <= 0:
        state["completed_at"] = now_ms_value
        changed = True
    if clean_str(state.get("status"), limit=32).lower() != "completed":
        state["status"] = "completed"
        changed = True
    if clamp_int(state.get("deferred_until")) != 0:
        state["deferred_until"] = 0
        changed = True
    if clean_str(state.get("last_error")):
        state["last_error"] = ""
        changed = True
    changed |= clear_active_prompt_token(state)
    return changed


def reset_successful_prompt_flow_state(
    state: dict[str, Any],
    *,
    reset_prompt_history: bool = False,
) -> bool:
    changed = False

    for field in ("accepted_at", "started_at", "completed_at"):
        if clamp_int(state.get(field)) != 0:
            state[field] = 0
            changed = True

    changed |= clear_started_via_prompt_state(state)

    if clean_str(state.get("status"), limit=32).lower() in {"started", "completed"}:
        state["status"] = "observing"
        changed = True

    if clamp_int(state.get("deferred_until")) != 0:
        state["deferred_until"] = 0
        changed = True

    if clean_str(state.get("last_error")):
        state["last_error"] = ""
        changed = True

    changed |= clear_active_prompt_token(state)

    if reset_prompt_history:
        if clamp_int(state.get("shown_count")) != 0:
            state["shown_count"] = 0
            changed = True
        if clamp_int(state.get("last_shown_at")) != 0:
            state["last_shown_at"] = 0
            changed = True
        if clean_str(state.get("last_acknowledged_prompt_token"), limit=128):
            state["last_acknowledged_prompt_token"] = ""
            changed = True
        if clean_str(state.get("last_decision_prompt_token"), limit=128):
            state["last_decision_prompt_token"] = ""
            changed = True

    return changed


def is_prompt_decision_replayed(state: dict[str, Any], prompt_token: str) -> bool:
    token = clean_str(prompt_token, limit=128)
    if not token:
        raise ValueError("invalid prompt_token")
    return token == clean_str(state.get("last_decision_prompt_token"), limit=128)


def mark_prompt_decision_token(state: dict[str, Any], prompt_token: str) -> bool:
    token = clean_str(prompt_token, limit=128)
    if not token:
        raise ValueError("invalid prompt_token")
    if clean_str(state.get("last_decision_prompt_token"), limit=128) == token:
        return False
    state["last_decision_prompt_token"] = token
    return True
