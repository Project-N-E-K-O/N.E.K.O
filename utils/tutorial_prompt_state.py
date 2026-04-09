from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.prompt_state_core import (
    DEFAULT_PROMPT_FLOW_STATE,
    FAILURE_COOLDOWN_MS,
    MAX_ALLOWED_FAILURE_COOLDOWN_MS,
    MAX_ALLOWED_LATER_COOLDOWN_MS,
    MAX_ALLOWED_MAX_PROMPT_SHOWS,
    MAX_ALLOWED_PROMPT_FOREGROUND_MS,
    MAX_COUNTER_DELTA,
    MAX_FOREGROUND_DELTA_MS,
    MAX_PROMPT_SHOWS,
    MIN_ALLOWED_FAILURE_COOLDOWN_MS,
    MIN_ALLOWED_LATER_COOLDOWN_MS,
    MIN_ALLOWED_MAX_PROMPT_SHOWS,
    PROMPT_PENDING_GUARD_MS,
    PROMPT_FUNNEL_KEYS,
    SCHEMA_VERSION,
    ack_prompt_token_if_needed as _ack_prompt_token_if_needed_core,
    apply_completed_state as _apply_completed_state_core,
    apply_started_state as _apply_started_state,
    build_prompt_flow_snapshot,
    build_public_prompt_flow_snapshot,
    clamp_int as _clamp_int,
    clean_str as _clean_str,
    clear_active_prompt_token as _clear_active_prompt_token,
    clear_started_via_prompt_state as _clear_started_via_prompt_state,
    ensure_active_prompt_token as _ensure_active_prompt_token,
    increment_funnel_count as _increment_funnel_count,
    is_prompt_decision_replayed as _is_prompt_decision_replayed,
    load_state_file as _load_state_file,
    mark_prompt_decision_token as _mark_prompt_decision_token,
    normalize_prompt_flow_state,
    now_ms as _now_ms,
)

TUTORIAL_PROMPT_CONFIG_FILENAME = "tutorial_prompt_config.json"
TUTORIAL_PROMPT_STATE_KIND = "tutorial_prompt"

MIN_PROMPT_FOREGROUND_MS = 15 * 1000
LATER_COOLDOWN_MS = 24 * 60 * 60 * 1000

MIN_ALLOWED_PROMPT_FOREGROUND_MS = 15 * 1000

DEFAULT_TUTORIAL_PROMPT_RUNTIME_CONFIG = {
    "min_prompt_foreground_ms": MIN_PROMPT_FOREGROUND_MS,
    "later_cooldown_ms": LATER_COOLDOWN_MS,
    "failure_cooldown_ms": FAILURE_COOLDOWN_MS,
    "max_prompt_shows": MAX_PROMPT_SHOWS,
}

VALID_USER_COHORTS = {
    "unknown",
    "new",
    "existing",
}

VALID_TUTORIAL_EVENT_SOURCES = {
    "manual",
    "idle_prompt",
}

TUTORIAL_PROMPT_FUNNEL_KEYS = PROMPT_FUNNEL_KEYS
TUTORIAL_PROMPT_SNAPSHOT_EXTRA_FIELDS = (
    "home_tutorial_completed",
    "manual_home_tutorial_viewed",
    "manual_home_tutorial_viewed_at",
    "user_cohort",
    "cohort_decided_at",
    "cohort_reason",
    "active_tutorial_run_token",
    "active_tutorial_run_source",
    "active_tutorial_run_started_at",
)
TUTORIAL_PROMPT_PUBLIC_EXTRA_FIELDS = tuple(
    field
    for field in TUTORIAL_PROMPT_SNAPSHOT_EXTRA_FIELDS
    if not field.startswith("active_tutorial_run_")
)

DEFAULT_TUTORIAL_PROMPT_STATE = {
    **deepcopy(DEFAULT_PROMPT_FLOW_STATE),
    "prompt_kind": TUTORIAL_PROMPT_STATE_KIND,
    "home_tutorial_completed": False,
    "manual_home_tutorial_viewed": False,
    "manual_home_tutorial_viewed_at": 0,
    "user_cohort": "unknown",
    "cohort_decided_at": 0,
    "cohort_reason": "",
    "active_tutorial_run_token": "",
    "active_tutorial_run_source": "",
    "active_tutorial_run_started_at": 0,
}

_STATE_LOCK = threading.RLock()


def _normalize_state(raw_state: Any) -> dict[str, Any]:
    raw_state = raw_state if isinstance(raw_state, dict) else {}

    def _normalize_extra(state: dict[str, Any]) -> None:
        state["home_tutorial_completed"] = bool(state.get("home_tutorial_completed"))
        state["manual_home_tutorial_viewed"] = bool(state.get("manual_home_tutorial_viewed"))
        state["manual_home_tutorial_viewed_at"] = _clamp_int(state.get("manual_home_tutorial_viewed_at"))

        if state["completed_at"] <= 0:
            state["completed_at"] = _clamp_int(raw_state.get("completed_timestamp"))

        legacy_completed_status = _clean_str(raw_state.get("completed_status"), limit=32).lower()
        if not state["home_tutorial_completed"] and (state["completed_at"] > 0 or legacy_completed_status):
            state["home_tutorial_completed"] = True

        cohort = _clean_str(state.get("user_cohort"), limit=32).lower()
        state["user_cohort"] = cohort if cohort in VALID_USER_COHORTS else "unknown"
        state["cohort_decided_at"] = _clamp_int(state.get("cohort_decided_at"))
        state["cohort_reason"] = _clean_str(state.get("cohort_reason"))

        source = _clean_str(state.get("active_tutorial_run_source"), limit=64).lower()
        state["active_tutorial_run_token"] = _clean_str(state.get("active_tutorial_run_token"), limit=128)
        state["active_tutorial_run_source"] = source if source in VALID_TUTORIAL_EVENT_SOURCES else ""
        state["active_tutorial_run_started_at"] = _clamp_int(state.get("active_tutorial_run_started_at"))

    def _resolve_status(state: dict[str, Any]) -> None:
        if state["never_remind"]:
            state["status"] = "never"
        if state["home_tutorial_completed"] or state["completed_at"] > 0:
            state["status"] = "completed"
        elif state["manual_home_tutorial_viewed"] or state["started_at"] > 0:
            state["status"] = "started"

    return normalize_prompt_flow_state(
        raw_state,
        defaults=DEFAULT_TUTORIAL_PROMPT_STATE,
        extra_normalizer=_normalize_extra,
        status_resolver=_resolve_status,
    )


def get_tutorial_prompt_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path("tutorial_prompt.json"))


def get_tutorial_prompt_config_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path(TUTORIAL_PROMPT_CONFIG_FILENAME))


def get_legacy_autostart_prompt_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path("autostart_prompt.json"))


def load_tutorial_prompt_runtime_config(config_manager=None) -> dict[str, int]:
    raw_config = _load_state_file(get_tutorial_prompt_config_path(config_manager)) or {}

    return {
        "min_prompt_foreground_ms": _clamp_int(
            raw_config.get("min_prompt_foreground_ms"),
            default=MIN_PROMPT_FOREGROUND_MS,
            minimum=MIN_ALLOWED_PROMPT_FOREGROUND_MS,
            maximum=MAX_ALLOWED_PROMPT_FOREGROUND_MS,
        ),
        "later_cooldown_ms": _clamp_int(
            raw_config.get("later_cooldown_ms"),
            default=LATER_COOLDOWN_MS,
            minimum=MIN_ALLOWED_LATER_COOLDOWN_MS,
            maximum=MAX_ALLOWED_LATER_COOLDOWN_MS,
        ),
        "failure_cooldown_ms": _clamp_int(
            raw_config.get("failure_cooldown_ms"),
            default=FAILURE_COOLDOWN_MS,
            minimum=MIN_ALLOWED_FAILURE_COOLDOWN_MS,
            maximum=MAX_ALLOWED_FAILURE_COOLDOWN_MS,
        ),
        "max_prompt_shows": _clamp_int(
            raw_config.get("max_prompt_shows"),
            default=MAX_PROMPT_SHOWS,
            minimum=MIN_ALLOWED_MAX_PROMPT_SHOWS,
            maximum=MAX_ALLOWED_MAX_PROMPT_SHOWS,
        ),
    }


def _looks_like_tutorial_prompt_state(raw_state: dict[str, Any]) -> bool:
    if not isinstance(raw_state, dict):
        return False

    prompt_kind = _clean_str(raw_state.get("prompt_kind"), limit=64).lower()
    if prompt_kind:
        return prompt_kind == TUTORIAL_PROMPT_STATE_KIND

    # 仅在确实看到 tutorial 专属痕迹时才迁移旧文件，避免把 autostart 状态误导入 tutorial。
    has_autostart_markers = any((
        "autostart_enabled" in raw_state,
        _clamp_int(raw_state.get("enabled_at")) > 0,
        bool(_clean_str(raw_state.get("enabled_provider"), limit=64)),
    ))
    has_legacy_completion_markers = any((
        bool(raw_state.get("home_tutorial_completed")),
        bool(_clean_str(raw_state.get("completed_status"), limit=32)) and not has_autostart_markers,
        (
            (
                _clamp_int(raw_state.get("completed_at")) > 0
                or _clamp_int(raw_state.get("completed_timestamp")) > 0
            )
            and not has_autostart_markers
        ),
    ))
    return any((
        has_legacy_completion_markers,
        bool(raw_state.get("manual_home_tutorial_viewed")),
        _clamp_int(raw_state.get("manual_home_tutorial_viewed_at")) > 0,
        _clamp_int(raw_state.get("cohort_decided_at")) > 0,
        _clean_str(raw_state.get("cohort_reason")),
        _clean_str(raw_state.get("user_cohort"), limit=32).lower() in {"new", "existing"},
        _clean_str(raw_state.get("active_tutorial_run_token"), limit=128),
        _clean_str(raw_state.get("active_tutorial_run_source"), limit=64),
        _clamp_int(raw_state.get("active_tutorial_run_started_at")) > 0,
        _clamp_int(raw_state.get("last_weak_home_interaction_at")) > 0,
    ))


def load_tutorial_prompt_state(config_manager=None) -> dict[str, Any]:
    path = get_tutorial_prompt_state_path(config_manager)
    data = _load_state_file(path)
    if data is not None:
        return _normalize_state(data)

    legacy_path = get_legacy_autostart_prompt_state_path(config_manager)
    legacy_data = _load_state_file(legacy_path)
    if legacy_data is None or not _looks_like_tutorial_prompt_state(legacy_data):
        return deepcopy(DEFAULT_TUTORIAL_PROMPT_STATE)

    normalized = _normalize_state(legacy_data)
    save_tutorial_prompt_state(normalized, config_manager)
    return normalized


def save_tutorial_prompt_state(state: dict[str, Any], config_manager=None) -> dict[str, Any]:
    normalized = _normalize_state(state)
    path = get_tutorial_prompt_state_path(config_manager)
    atomic_write_json(path, normalized, ensure_ascii=False, indent=2)
    return normalized


def build_tutorial_prompt_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_state(state)
    return build_prompt_flow_snapshot(
        normalized,
        extra_fields=TUTORIAL_PROMPT_SNAPSHOT_EXTRA_FIELDS,
    )


def build_public_tutorial_prompt_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_state(state)
    snapshot = build_public_prompt_flow_snapshot(normalized)
    for field in TUTORIAL_PROMPT_PUBLIC_EXTRA_FIELDS:
        value = normalized.get(field)
        snapshot[field] = deepcopy(value) if isinstance(value, (dict, list)) else value
    return snapshot


def _normalize_tutorial_event_payload(payload: dict[str, Any] | None) -> tuple[str, str, str]:
    payload = payload or {}
    page = _clean_str(payload.get("page") or "home", limit=64).lower() or "home"
    source = _clean_str(payload.get("source") or "manual", limit=64).lower() or "manual"
    prompt_token = _clean_str(payload.get("prompt_token") or payload.get("token"), limit=128)
    return page, source, prompt_token


def _get_tutorial_run_token(payload: dict[str, Any] | None) -> str:
    payload = payload or {}
    return _clean_str(
        payload.get("tutorial_run_token") or payload.get("run_token"),
        limit=128,
    )


def _validate_tutorial_event_source(source: str) -> str:
    normalized = _clean_str(source or "manual", limit=64).lower() or "manual"
    if normalized not in VALID_TUTORIAL_EVENT_SOURCES:
        raise ValueError("invalid source")
    return normalized


def _apply_weak_home_interaction(state: dict[str, Any], delta: int, now_ms: int) -> bool:
    if delta <= 0:
        return False

    changed = False
    state["home_interactions"] += delta
    changed = True
    if state["foreground_ms"] != 0:
        state["foreground_ms"] = 0
    if state["last_weak_home_interaction_at"] != now_ms:
        state["last_weak_home_interaction_at"] = now_ms
    return changed


def _apply_completed_state(state: dict[str, Any], now_ms: int) -> bool:
    changed = _apply_completed_state_core(state, now_ms)
    changed |= _clear_tutorial_run_token(state)
    return changed


def _clear_tutorial_run_token(state: dict[str, Any]) -> bool:
    changed = False
    if state.get("active_tutorial_run_token"):
        state["active_tutorial_run_token"] = ""
        changed = True
    if state.get("active_tutorial_run_source"):
        state["active_tutorial_run_source"] = ""
        changed = True
    if _clamp_int(state.get("active_tutorial_run_started_at")) != 0:
        state["active_tutorial_run_started_at"] = 0
        changed = True
    return changed


def _clear_started_via_prompt_state(state: dict[str, Any]) -> bool:
    changed = False
    if state.get("started_via_prompt"):
        state["started_via_prompt"] = False
        changed = True
    if "started_via_prompt_at" in state and _clamp_int(state.get("started_via_prompt_at")) != 0:
        state["started_via_prompt_at"] = 0
        changed = True
    return changed


def _ensure_tutorial_run_token(
    state: dict[str, Any],
    *,
    source: str,
    now_ms: int,
) -> tuple[str, bool]:
    current_token = _clean_str(state.get("active_tutorial_run_token"), limit=128)
    current_source = _clean_str(state.get("active_tutorial_run_source"), limit=64).lower()
    current_started_at = _clamp_int(state.get("active_tutorial_run_started_at"))

    if current_token and current_source == source and current_started_at > 0:
        return current_token, False

    state["active_tutorial_run_token"] = uuid.uuid4().hex
    state["active_tutorial_run_source"] = source
    state["active_tutorial_run_started_at"] = now_ms
    return state["active_tutorial_run_token"], True


def _ack_prompt_token_if_needed(
    state: dict[str, Any],
    prompt_token: str,
    now_ms: int,
    *,
    max_prompt_shows: int,
) -> tuple[dict[str, Any], bool, bool]:
    return _ack_prompt_token_if_needed_core(
        state,
        prompt_token,
        now_ms,
        normalizer=_normalize_state,
        max_prompt_shows=max_prompt_shows,
    )


def _get_user_config_dir(config_manager=None) -> Path:
    config_dir = getattr(config_manager, "config_dir", None)
    if config_dir:
        return Path(config_dir)
    return get_tutorial_prompt_state_path(config_manager).parent


def _get_user_memory_dir(config_manager=None) -> Path:
    memory_dir = getattr(config_manager, "memory_dir", None)
    if memory_dir:
        return Path(memory_dir)
    return _get_user_config_dir(config_manager).parent / "memory"


def _get_user_chara_dir(config_manager=None) -> Path:
    chara_dir = getattr(config_manager, "chara_dir", None)
    if chara_dir:
        return Path(chara_dir)
    return _get_user_config_dir(config_manager).parent / "character_cards"


def _iter_meaningful_files(base_dir: Path):
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for path in base_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name == ".gitkeep" or name.startswith("."):
            continue
        yield path


def _has_meaningful_memory_history(config_manager=None) -> bool:
    return any(True for _ in _iter_meaningful_files(_get_user_memory_dir(config_manager)))


def _has_custom_character_cards(config_manager=None) -> bool:
    return any(True for _ in _iter_meaningful_files(_get_user_chara_dir(config_manager)))


def _token_usage_indicates_existing_user(config_manager=None) -> bool:
    token_usage_path = _get_user_config_dir(config_manager) / "token_usage.json"
    if not token_usage_path.exists():
        return False

    try:
        with token_usage_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(data, dict):
        return False

    daily_stats = data.get("daily_stats")
    if not isinstance(daily_stats, dict):
        daily_stats = {}

    for day_stats in daily_stats.values():
        if not isinstance(day_stats, dict):
            continue
        if any(_clamp_int(day_stats.get(key)) > 0 for key in (
            "total_prompt_tokens",
            "total_completion_tokens",
            "total_tokens",
            "cached_tokens",
        )):
            return True

        by_call_type = day_stats.get("by_call_type") or {}
        if not isinstance(by_call_type, dict):
            continue
        for call_type, bucket in by_call_type.items():
            if _clean_str(call_type, limit=64) == "app_start":
                continue
            if not isinstance(bucket, dict):
                continue
            if any(_clamp_int(bucket.get(key)) > 0 for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_tokens",
                "call_count",
            )):
                return True

    recent_records = data.get("recent_records")
    if not isinstance(recent_records, list):
        recent_records = []

    for record in recent_records:
        if not isinstance(record, dict):
            continue
        if _clean_str(record.get("type"), limit=64) != "app_start":
            return True
        if any(_clamp_int(record.get(key)) > 0 for key in ("pt", "ct", "tt", "cch")):
            return True

    return False


def detect_tutorial_prompt_user_cohort(config_manager=None) -> tuple[str, str]:
    if _has_meaningful_memory_history(config_manager):
        return "existing", "memory_history"
    if _has_custom_character_cards(config_manager):
        return "existing", "character_cards"
    if _token_usage_indicates_existing_user(config_manager):
        return "existing", "token_usage"
    return "new", "no_prior_usage"


def ensure_tutorial_prompt_user_cohort(
    state: dict[str, Any],
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> tuple[dict[str, Any], bool]:
    normalized = _normalize_state(state)
    if normalized["user_cohort"] in {"new", "existing"}:
        return normalized, False

    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    cohort, reason = detect_tutorial_prompt_user_cohort(config_manager)

    changed = False
    if normalized["user_cohort"] != cohort:
        normalized["user_cohort"] = cohort
        changed = True
    if normalized["cohort_reason"] != reason:
        normalized["cohort_reason"] = reason
        changed = True
    if normalized["cohort_decided_at"] <= 0:
        normalized["cohort_decided_at"] = now_ms
        changed = True

    return normalized, changed


def _compute_prompt_eligibility(
    state: dict[str, Any],
    *,
    now_ms: int,
    min_prompt_foreground_ms: int,
    max_prompt_shows: int,
) -> tuple[bool, str]:
    if state["user_cohort"] == "existing":
        return False, "existing_user"
    if state["home_tutorial_completed"] or state["status"] == "completed":
        return False, "tutorial_completed"
    if state["manual_home_tutorial_viewed"] or state["status"] == "started":
        return False, "tutorial_started"
    if state["chat_turns"] > 0 or state["voice_sessions"] > 0:
        return False, "meaningful_action_taken"
    if state["never_remind"] or state["status"] == "never":
        return False, "never_remind"
    if state["shown_count"] >= max_prompt_shows:
        return False, "show_limit_reached"
    if (
        state["status"] == "prompted"
        and state["last_shown_at"] > 0
        and (now_ms - state["last_shown_at"]) < PROMPT_PENDING_GUARD_MS
    ):
        return False, "prompt_pending"
    if state["deferred_until"] > now_ms:
        return False, "cooldown_active"
    if state["foreground_ms"] < min_prompt_foreground_ms:
        return False, "foreground_insufficient"
    return True, "idle_timeout"


def process_tutorial_prompt_heartbeat(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    runtime_config = load_tutorial_prompt_runtime_config(config_manager)

    foreground_delta = _clamp_int(
        payload.get("foreground_ms_delta"),
        minimum=0,
        maximum=MAX_FOREGROUND_DELTA_MS,
    )
    home_interactions_delta = _clamp_int(
        payload.get("home_interactions_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    chat_turns_delta = _clamp_int(
        payload.get("chat_turns_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    voice_sessions_delta = _clamp_int(
        payload.get("voice_sessions_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    home_tutorial_completed = bool(payload.get("home_tutorial_completed"))
    manual_home_tutorial_viewed = bool(payload.get("manual_home_tutorial_viewed"))

    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        changed = False
        state, cohort_changed = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
            now_ms=now_ms,
        )
        changed |= cohort_changed

        if state["first_seen_at"] <= 0:
            state["first_seen_at"] = now_ms
            changed = True

        if foreground_delta:
            state["foreground_ms"] += foreground_delta
            changed = True
        if home_interactions_delta:
            changed |= _apply_weak_home_interaction(state, home_interactions_delta, now_ms)
        if chat_turns_delta:
            state["chat_turns"] += chat_turns_delta
            changed = True
        if voice_sessions_delta:
            state["voice_sessions"] += voice_sessions_delta
            changed = True
        if manual_home_tutorial_viewed and not state["manual_home_tutorial_viewed"]:
            changed |= _clear_started_via_prompt_state(state)
            state["manual_home_tutorial_viewed"] = True
            if state["manual_home_tutorial_viewed_at"] <= 0:
                state["manual_home_tutorial_viewed_at"] = now_ms
            changed = True
            changed |= _apply_started_state(state, now_ms)
        if home_tutorial_completed and not state["home_tutorial_completed"]:
            if manual_home_tutorial_viewed or state["manual_home_tutorial_viewed"]:
                changed |= _clear_started_via_prompt_state(state)
            state["home_tutorial_completed"] = True
            changed = True
            changed |= _apply_completed_state(state, now_ms)
            if state["started_via_prompt"]:
                changed |= _increment_funnel_count(state, "completed")

        should_prompt, prompt_reason = _compute_prompt_eligibility(
            state,
            now_ms=now_ms,
            min_prompt_foreground_ms=runtime_config["min_prompt_foreground_ms"],
            max_prompt_shows=runtime_config["max_prompt_shows"],
        )
        prompt_token = ""

        if should_prompt:
            prompt_token, token_changed = _ensure_active_prompt_token(state, now_ms)
            changed |= token_changed
            if token_changed:
                changed |= _increment_funnel_count(state, "issued")
        else:
            changed |= _clear_active_prompt_token(state)

        if changed:
            state = save_tutorial_prompt_state(state, config_manager)

    return {
        "ok": True,
        "should_prompt": should_prompt,
        "prompt_reason": prompt_reason,
        "prompt_mode": "tutorial",
        "prompt_token": prompt_token or None,
        "state": build_tutorial_prompt_snapshot(state),
    }


def record_tutorial_prompt_shown(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    prompt_token = _clean_str(payload.get("prompt_token") or payload.get("token"), limit=128)
    runtime_config = load_tutorial_prompt_runtime_config(config_manager)

    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        state, _ = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
            now_ms=now_ms,
        )
        state, changed, already_acknowledged = _ack_prompt_token_if_needed(
            state,
            prompt_token,
            now_ms,
            max_prompt_shows=runtime_config["max_prompt_shows"],
        )
        if changed:
            state = save_tutorial_prompt_state(state, config_manager)

    return {
        "ok": True,
        "already_acknowledged": already_acknowledged,
        "state": build_tutorial_prompt_snapshot(state),
    }


def record_tutorial_prompt_decision(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    runtime_config = load_tutorial_prompt_runtime_config(config_manager)
    decision = _clean_str(payload.get("decision") or payload.get("action"), limit=32).lower()
    result = _clean_str(payload.get("result"), limit=32).lower()
    error = _clean_str(payload.get("error"))
    prompt_token = _clean_str(payload.get("prompt_token") or payload.get("token"), limit=128)

    if decision not in {"accept", "later", "never"}:
        raise ValueError("invalid decision")
    if not prompt_token:
        raise ValueError("invalid prompt_token")

    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        state, _ = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
            now_ms=now_ms,
        )
        if _is_prompt_decision_replayed(state, prompt_token):
            return {
                "ok": True,
                "state": build_tutorial_prompt_snapshot(state),
            }

        state, token_changed, _ = _ack_prompt_token_if_needed(
            state,
            prompt_token,
            now_ms,
            max_prompt_shows=runtime_config["max_prompt_shows"],
        )

        if decision == "never":
            state["never_remind"] = True
            state["status"] = "never"
            state["deferred_until"] = 0
            state["last_error"] = ""
            token_changed |= _increment_funnel_count(state, "never")
        elif decision == "later":
            state["status"] = "deferred"
            state["deferred_until"] = now_ms + runtime_config["later_cooldown_ms"]
            state["last_error"] = ""
            token_changed |= _increment_funnel_count(state, "later")
        else:
            accepted_before = state["accepted_at"] > 0
            if state["accepted_at"] <= 0:
                state["accepted_at"] = now_ms
                token_changed = True
            if not accepted_before:
                token_changed |= _increment_funnel_count(state, "accept")
            if result and result in {"accepted", "started", "tutorial_started"}:
                started_before = state["started_at"] > 0
                if not state["started_via_prompt"]:
                    state["started_via_prompt"] = True
                    token_changed = True
                token_changed |= _apply_started_state(state, now_ms)
                if not started_before:
                    token_changed |= _increment_funnel_count(state, "started")
            else:
                token_changed |= _clear_started_via_prompt_state(state)
                state["status"] = "error"
                state["deferred_until"] = now_ms + runtime_config["failure_cooldown_ms"]
                state["last_error"] = error or "tutorial_start_failed"
                token_changed |= _increment_funnel_count(state, "failed")

        token_changed |= _mark_prompt_decision_token(state, prompt_token)
        state = save_tutorial_prompt_state(state, config_manager)

    return {
        "ok": True,
        "state": build_tutorial_prompt_snapshot(state),
    }


def record_tutorial_started(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    page, source, prompt_token = _normalize_tutorial_event_payload(payload)
    source = _validate_tutorial_event_source(source)
    runtime_config = load_tutorial_prompt_runtime_config(config_manager)

    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        state, _ = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
            now_ms=now_ms,
        )

        if page != "home":
            return {
                "ok": True,
                "ignored": True,
                "state": build_public_tutorial_prompt_snapshot(state),
            }

        changed = False
        started_before = state["started_at"] > 0
        is_prompt_source = source == "idle_prompt"
        tutorial_run_token = ""

        if is_prompt_source:
            if not prompt_token:
                raise ValueError("invalid prompt_token")
            state, ack_changed, _ = _ack_prompt_token_if_needed(
                state,
                prompt_token,
                now_ms,
                max_prompt_shows=runtime_config["max_prompt_shows"],
            )
            changed |= ack_changed
            if state["accepted_at"] <= 0:
                state["accepted_at"] = now_ms
                changed = True
                changed |= _increment_funnel_count(state, "accept")
            if not state["started_via_prompt"]:
                state["started_via_prompt"] = True
                changed = True
        else:
            changed |= _clear_started_via_prompt_state(state)
            if not state["manual_home_tutorial_viewed"]:
                state["manual_home_tutorial_viewed"] = True
                changed = True
            if state["manual_home_tutorial_viewed_at"] <= 0:
                state["manual_home_tutorial_viewed_at"] = now_ms
                changed = True

        changed |= _apply_started_state(state, now_ms)
        if not started_before:
            changed |= _increment_funnel_count(state, "started")
        tutorial_run_token, run_token_changed = _ensure_tutorial_run_token(
            state,
            source=source,
            now_ms=now_ms,
        )
        changed |= run_token_changed

        if changed:
            state = save_tutorial_prompt_state(state, config_manager)

    return {
        "ok": True,
        "ignored": False,
        "tutorial_run_token": tutorial_run_token,
        "state": build_public_tutorial_prompt_snapshot(state),
    }


def record_tutorial_completed(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = _clamp_int(now_ms if now_ms is not None else _now_ms())
    page, source, _prompt_token = _normalize_tutorial_event_payload(payload)
    source = _validate_tutorial_event_source(source)
    tutorial_run_token = _get_tutorial_run_token(payload)

    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        state, _ = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
            now_ms=now_ms,
        )

        if page != "home":
            return {
                "ok": True,
                "ignored": True,
                "state": build_public_tutorial_prompt_snapshot(state),
            }

        changed = False
        started_before = state["started_at"] > 0
        completed_before = state["completed_at"] > 0
        active_run_token = _clean_str(state.get("active_tutorial_run_token"), limit=128)
        active_run_source = _clean_str(state.get("active_tutorial_run_source"), limit=64).lower()
        active_run_started_at = _clamp_int(state.get("active_tutorial_run_started_at"))

        if not tutorial_run_token or tutorial_run_token != active_run_token:
            raise ValueError("invalid tutorial_run_token")
        if active_run_source and active_run_source != source:
            raise ValueError("invalid tutorial_run_token")

        if source == "idle_prompt":
            if state["accepted_at"] <= 0:
                state["accepted_at"] = now_ms
                changed = True
                changed |= _increment_funnel_count(state, "accept")
            if not state["started_via_prompt"]:
                state["started_via_prompt"] = True
                changed = True
        else:
            changed |= _clear_started_via_prompt_state(state)
            if not state["manual_home_tutorial_viewed"]:
                state["manual_home_tutorial_viewed"] = True
                changed = True
            if state["manual_home_tutorial_viewed_at"] <= 0:
                state["manual_home_tutorial_viewed_at"] = now_ms
                changed = True

        if not started_before:
            changed |= _apply_started_state(state, active_run_started_at or now_ms)
            changed |= _increment_funnel_count(state, "started")

        if not state["home_tutorial_completed"]:
            state["home_tutorial_completed"] = True
            changed = True

        changed |= _apply_completed_state(state, now_ms)
        if not completed_before and state["started_via_prompt"]:
            changed |= _increment_funnel_count(state, "completed")

        if changed:
            state = save_tutorial_prompt_state(state, config_manager)

    return {
        "ok": True,
        "ignored": False,
        "state": build_public_tutorial_prompt_snapshot(state),
    }


def get_tutorial_prompt_state_response(*, config_manager=None) -> dict[str, Any]:
    with _STATE_LOCK:
        state = load_tutorial_prompt_state(config_manager)
        state, changed = ensure_tutorial_prompt_user_cohort(
            state,
            config_manager=config_manager,
        )
        if changed:
            state = save_tutorial_prompt_state(state, config_manager)
    return {
        "ok": True,
        "state": build_public_tutorial_prompt_snapshot(state),
    }
