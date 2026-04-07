from __future__ import annotations

import threading
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
    ack_prompt_token_if_needed,
    apply_completed_state,
    apply_started_state,
    build_prompt_flow_snapshot,
    build_public_prompt_flow_snapshot,
    clamp_int,
    clean_str,
    clear_active_prompt_token,
    clear_started_via_prompt_state,
    ensure_active_prompt_token,
    increment_funnel_count,
    is_prompt_decision_replayed,
    load_state_file,
    mark_prompt_decision_token,
    normalize_prompt_flow_state,
    reset_successful_prompt_flow_state,
    now_ms as core_now_ms,
)


AUTOSTART_PROMPT_CONFIG_FILENAME = "autostart_prompt_config.json"
AUTOSTART_PROMPT_STATE_FILENAME = "autostart_prompt_state.json"
AUTOSTART_PROMPT_LEGACY_STATE_FILENAME = "autostart_prompt.json"
AUTOSTART_PROMPT_STATE_KIND = "autostart_prompt"
AUTOSTART_MIN_PROMPT_FOREGROUND_MS = 15 * 60 * 1000
AUTOSTART_LATER_COOLDOWN_MS = 24 * 60 * 60 * 1000

AUTOSTART_PROMPT_EXTRA_FIELDS = (
    "autostart_enabled",
    "enabled_at",
)

DEFAULT_AUTOSTART_PROMPT_STATE = {
    **deepcopy(DEFAULT_PROMPT_FLOW_STATE),
    "prompt_kind": AUTOSTART_PROMPT_STATE_KIND,
    "autostart_enabled": False,
    "enabled_at": 0,
    "enabled_provider": "",
}

_AUTOSTART_STATE_LOCK = threading.RLock()


def get_autostart_prompt_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path(AUTOSTART_PROMPT_STATE_FILENAME))


def get_legacy_autostart_prompt_state_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path(AUTOSTART_PROMPT_LEGACY_STATE_FILENAME))


def get_autostart_prompt_config_path(config_manager=None) -> Path:
    config_manager = config_manager or get_config_manager()
    return Path(config_manager.get_config_path(AUTOSTART_PROMPT_CONFIG_FILENAME))


def _looks_like_autostart_prompt_state(raw_state: Any) -> bool:
    if not isinstance(raw_state, dict):
        return False

    prompt_kind = clean_str(raw_state.get("prompt_kind"), limit=64).lower()
    if prompt_kind:
        return prompt_kind == AUTOSTART_PROMPT_STATE_KIND

    return any((
        "autostart_enabled" in raw_state,
        clamp_int(raw_state.get("enabled_at")) > 0,
        bool(clean_str(raw_state.get("enabled_provider"), limit=64)),
    ))


def _normalize_autostart_prompt_state(raw_state: Any) -> dict[str, Any]:
    def _normalize_extra(state: dict[str, Any]) -> None:
        state["autostart_enabled"] = bool(state.get("autostart_enabled"))
        state["enabled_at"] = clamp_int(state.get("enabled_at"))
        state["enabled_provider"] = clean_str(state.get("enabled_provider"), limit=64).lower()

    def _resolve_status(state: dict[str, Any]) -> None:
        if state["never_remind"]:
            state["status"] = "never"
        if state["autostart_enabled"] or state["completed_at"] > 0:
            state["autostart_enabled"] = True
            if state["enabled_at"] <= 0 and state["completed_at"] > 0:
                state["enabled_at"] = state["completed_at"]
            state["status"] = "completed"
        elif state["started_at"] > 0:
            state["status"] = "started"

    return normalize_prompt_flow_state(
        raw_state,
        defaults=DEFAULT_AUTOSTART_PROMPT_STATE,
        extra_normalizer=_normalize_extra,
        status_resolver=_resolve_status,
    )


def load_autostart_prompt_runtime_config(config_manager=None) -> dict[str, int]:
    raw_config = load_state_file(get_autostart_prompt_config_path(config_manager)) or {}

    return {
        "min_prompt_foreground_ms": clamp_int(
            raw_config.get("min_prompt_foreground_ms"),
            default=AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
            minimum=AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
            maximum=MAX_ALLOWED_PROMPT_FOREGROUND_MS,
        ),
        "later_cooldown_ms": clamp_int(
            raw_config.get("later_cooldown_ms"),
            default=AUTOSTART_LATER_COOLDOWN_MS,
            minimum=MIN_ALLOWED_LATER_COOLDOWN_MS,
            maximum=MAX_ALLOWED_LATER_COOLDOWN_MS,
        ),
        "failure_cooldown_ms": clamp_int(
            raw_config.get("failure_cooldown_ms"),
            default=FAILURE_COOLDOWN_MS,
            minimum=MIN_ALLOWED_FAILURE_COOLDOWN_MS,
            maximum=MAX_ALLOWED_FAILURE_COOLDOWN_MS,
        ),
        "max_prompt_shows": clamp_int(
            raw_config.get("max_prompt_shows"),
            default=MAX_PROMPT_SHOWS,
            minimum=MIN_ALLOWED_MAX_PROMPT_SHOWS,
            maximum=MAX_ALLOWED_MAX_PROMPT_SHOWS,
        ),
    }


def load_autostart_prompt_state(config_manager=None) -> dict[str, Any]:
    data = load_state_file(get_autostart_prompt_state_path(config_manager))
    should_migrate_legacy_state = False

    if data is None:
        legacy_data = load_state_file(get_legacy_autostart_prompt_state_path(config_manager))
        if legacy_data is None or not _looks_like_autostart_prompt_state(legacy_data):
            return deepcopy(DEFAULT_AUTOSTART_PROMPT_STATE)
        data = legacy_data
        should_migrate_legacy_state = True

    normalized = _normalize_autostart_prompt_state(data)
    if should_migrate_legacy_state:
        save_autostart_prompt_state(normalized, config_manager)
    return normalized


def save_autostart_prompt_state(state: dict[str, Any], config_manager=None) -> dict[str, Any]:
    normalized = _normalize_autostart_prompt_state(state)
    path = get_autostart_prompt_state_path(config_manager)
    atomic_write_json(path, normalized, ensure_ascii=False, indent=2)
    return normalized


def build_autostart_prompt_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_autostart_prompt_state(state)
    return build_prompt_flow_snapshot(
        normalized,
        extra_fields=AUTOSTART_PROMPT_EXTRA_FIELDS,
    )


def build_public_autostart_prompt_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_autostart_prompt_state(state)
    return build_public_prompt_flow_snapshot(
        normalized,
        extra_fields=AUTOSTART_PROMPT_EXTRA_FIELDS,
    )


def _compute_autostart_prompt_eligibility(
    state: dict[str, Any],
    *,
    now_ms_value: int,
    min_prompt_foreground_ms: int,
    max_prompt_shows: int,
) -> tuple[bool, str]:
    if state["autostart_enabled"] or state["status"] == "completed":
        return False, "autostart_enabled"
    if state["status"] == "started":
        return False, "autostart_pending"
    if state["never_remind"] or state["status"] == "never":
        return False, "never_remind"
    if state["shown_count"] >= max_prompt_shows:
        return False, "show_limit_reached"
    if (
        state["status"] == "prompted"
        and state["last_shown_at"] > 0
        and (now_ms_value - state["last_shown_at"]) < PROMPT_PENDING_GUARD_MS
    ):
        return False, "prompt_pending"
    if state["deferred_until"] > now_ms_value:
        return False, "cooldown_active"
    if state["foreground_ms"] < min_prompt_foreground_ms:
        return False, "foreground_insufficient"
    return True, "usage_timeout"


def _mark_autostart_enabled(
    state: dict[str, Any],
    *,
    enabled_at: int,
    provider: str = "",
) -> bool:
    changed = False
    if not state["autostart_enabled"]:
        state["autostart_enabled"] = True
        changed = True
    if state["enabled_at"] <= 0:
        state["enabled_at"] = enabled_at
        changed = True
    provider_name = clean_str(provider, limit=64).lower()
    if provider_name and state.get("enabled_provider") != provider_name:
        state["enabled_provider"] = provider_name
        changed = True
    return changed


def _apply_autostart_enabled_completion(
    state: dict[str, Any],
    *,
    enabled_at: int,
    provider: str = "",
) -> bool:
    completed_before = state["completed_at"] > 0 or state["autostart_enabled"]
    changed = _mark_autostart_enabled(
        state,
        enabled_at=enabled_at,
        provider=provider,
    )
    changed |= apply_completed_state(state, enabled_at)
    if state["started_via_prompt"] and not completed_before:
        changed |= increment_funnel_count(state, "completed")
    return changed


def _apply_autostart_accept_failure(
    state: dict[str, Any],
    *,
    now_ms_value: int,
    runtime_config: dict[str, int],
    error_code: str,
    preserve_prompt_attribution: bool,
) -> bool:
    changed = False
    if preserve_prompt_attribution and not state["started_via_prompt"]:
        state["started_via_prompt"] = True
        changed = True
    if not preserve_prompt_attribution:
        changed |= clear_started_via_prompt_state(state)
    if state["status"] != "error":
        state["status"] = "error"
        changed = True
    deferred_until = now_ms_value + runtime_config["failure_cooldown_ms"]
    if state["deferred_until"] != deferred_until:
        state["deferred_until"] = deferred_until
        changed = True
    if state["last_error"] != error_code:
        state["last_error"] = error_code
        changed = True
    changed |= increment_funnel_count(state, "failed")
    return changed


def _clear_stale_autostart_enabled_state(
    state: dict[str, Any],
    *,
    current_provider: str = "",
) -> bool:
    has_success_state = bool(state["autostart_enabled"]) or state["completed_at"] > 0
    if not has_success_state:
        return False

    provider_name = clean_str(current_provider, limit=64).lower()
    previous_provider = clean_str(state.get("enabled_provider"), limit=64).lower()
    reset_prompt_history = bool(provider_name) and provider_name != previous_provider

    changed = reset_successful_prompt_flow_state(
        state,
        reset_prompt_history=reset_prompt_history,
    )

    if state["autostart_enabled"]:
        state["autostart_enabled"] = False
        changed = True
    if state["enabled_at"] != 0:
        state["enabled_at"] = 0
        changed = True
    if state.get("enabled_provider"):
        state["enabled_provider"] = ""
        changed = True

    return changed


def process_autostart_prompt_heartbeat(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms_value = clamp_int(now_ms if now_ms is not None else core_now_ms())
    runtime_config = load_autostart_prompt_runtime_config(config_manager)

    foreground_delta = clamp_int(
        payload.get("foreground_ms_delta"),
        minimum=0,
        maximum=MAX_FOREGROUND_DELTA_MS,
    )
    home_interactions_delta = clamp_int(
        payload.get("home_interactions_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    chat_turns_delta = clamp_int(
        payload.get("chat_turns_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    voice_sessions_delta = clamp_int(
        payload.get("voice_sessions_delta"),
        minimum=0,
        maximum=MAX_COUNTER_DELTA,
    )
    autostart_enabled = bool(payload.get("autostart_enabled"))
    autostart_supported = payload.get("autostart_supported")
    if autostart_supported is None:
        autostart_supported = True
    else:
        autostart_supported = bool(autostart_supported)
    autostart_status_authoritative = bool(payload.get("autostart_status_authoritative"))
    autostart_provider = clean_str(
        payload.get("autostart_provider") or payload.get("provider"),
        limit=64,
    ).lower()

    with _AUTOSTART_STATE_LOCK:
        state = load_autostart_prompt_state(config_manager)
        changed = False

        if state["first_seen_at"] <= 0:
            state["first_seen_at"] = now_ms_value
            changed = True

        if foreground_delta:
            state["foreground_ms"] += foreground_delta
            changed = True
        if home_interactions_delta:
            state["home_interactions"] += home_interactions_delta
            changed = True
        if chat_turns_delta:
            state["chat_turns"] += chat_turns_delta
            changed = True
        if voice_sessions_delta:
            state["voice_sessions"] += voice_sessions_delta
            changed = True
        if autostart_status_authoritative:
            if autostart_enabled:
                changed |= _apply_autostart_enabled_completion(
                    state,
                    enabled_at=now_ms_value,
                    provider=autostart_provider,
                )
            else:
                changed |= _clear_stale_autostart_enabled_state(
                    state,
                    current_provider=autostart_provider,
                )
        elif autostart_enabled and not state["autostart_enabled"]:
            changed |= _apply_autostart_enabled_completion(
                state,
                enabled_at=now_ms_value,
                provider=autostart_provider,
            )

        if autostart_status_authoritative and not autostart_supported and not autostart_enabled:
            should_prompt = False
            prompt_reason = "autostart_unsupported"
        else:
            should_prompt, prompt_reason = _compute_autostart_prompt_eligibility(
                state,
                now_ms_value=now_ms_value,
                min_prompt_foreground_ms=runtime_config["min_prompt_foreground_ms"],
                max_prompt_shows=runtime_config["max_prompt_shows"],
            )
        prompt_token = ""

        if should_prompt:
            prompt_token, token_changed = ensure_active_prompt_token(state, now_ms_value)
            changed |= token_changed
            if token_changed:
                changed |= increment_funnel_count(state, "issued")
        else:
            changed |= clear_active_prompt_token(state)

        if changed:
            state = save_autostart_prompt_state(state, config_manager)

    return {
        "ok": True,
        "should_prompt": should_prompt,
        "prompt_reason": prompt_reason,
        "prompt_mode": "autostart",
        "prompt_token": prompt_token or None,
        "state": build_autostart_prompt_snapshot(state),
    }


def record_autostart_prompt_shown(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms_value = clamp_int(now_ms if now_ms is not None else core_now_ms())
    prompt_token = clean_str(payload.get("prompt_token") or payload.get("token"), limit=128)
    runtime_config = load_autostart_prompt_runtime_config(config_manager)

    with _AUTOSTART_STATE_LOCK:
        state = load_autostart_prompt_state(config_manager)
        state, changed, already_acknowledged = ack_prompt_token_if_needed(
            state,
            prompt_token,
            now_ms_value,
            normalizer=_normalize_autostart_prompt_state,
            max_prompt_shows=runtime_config["max_prompt_shows"],
        )
        if changed:
            state = save_autostart_prompt_state(state, config_manager)

    return {
        "ok": True,
        "already_acknowledged": already_acknowledged,
        "state": build_autostart_prompt_snapshot(state),
    }


def record_autostart_prompt_decision(
    payload: dict[str, Any] | None,
    *,
    config_manager=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    now_ms_value = clamp_int(now_ms if now_ms is not None else core_now_ms())
    runtime_config = load_autostart_prompt_runtime_config(config_manager)
    decision = clean_str(payload.get("decision") or payload.get("action"), limit=32).lower()
    result = clean_str(payload.get("result"), limit=32).lower()
    error = clean_str(payload.get("error"))
    prompt_token = clean_str(payload.get("prompt_token") or payload.get("token"), limit=128)
    autostart_provider = clean_str(
        payload.get("autostart_provider") or payload.get("provider"),
        limit=64,
    ).lower()

    if decision not in {"accept", "later", "never"}:
        raise ValueError("invalid decision")
    if not prompt_token:
        raise ValueError("invalid prompt_token")

    with _AUTOSTART_STATE_LOCK:
        state = load_autostart_prompt_state(config_manager)
        if is_prompt_decision_replayed(state, prompt_token):
            return {
                "ok": True,
                "state": build_autostart_prompt_snapshot(state),
            }
        state, changed, _ = ack_prompt_token_if_needed(
            state,
            prompt_token,
            now_ms_value,
            normalizer=_normalize_autostart_prompt_state,
            max_prompt_shows=runtime_config["max_prompt_shows"],
        )

        if decision == "never":
            changed |= clear_started_via_prompt_state(state)
            state["never_remind"] = True
            state["status"] = "never"
            state["deferred_until"] = 0
            state["last_error"] = ""
            changed |= increment_funnel_count(state, "never")
        elif decision == "later":
            changed |= clear_started_via_prompt_state(state)
            state["status"] = "deferred"
            state["deferred_until"] = now_ms_value + runtime_config["later_cooldown_ms"]
            state["last_error"] = ""
            changed |= increment_funnel_count(state, "later")
        else:
            accepted_before = state["accepted_at"] > 0
            started_before = state["started_at"] > 0

            if state["accepted_at"] <= 0:
                state["accepted_at"] = now_ms_value
                changed = True
            if not accepted_before:
                changed |= increment_funnel_count(state, "accept")

            if result in {"enabled", "autostart_enabled"}:
                if not state["started_via_prompt"]:
                    state["started_via_prompt"] = True
                    changed = True
                changed |= apply_started_state(state, now_ms_value)
                if not started_before:
                    changed |= increment_funnel_count(state, "started")
                changed |= _apply_autostart_enabled_completion(
                    state,
                    enabled_at=now_ms_value,
                    provider=autostart_provider,
                )
            elif result in {"", "accepted"}:
                changed |= _apply_autostart_accept_failure(
                    state,
                    now_ms_value=now_ms_value,
                    runtime_config=runtime_config,
                    error_code="autostart_enable_unconfirmed",
                    preserve_prompt_attribution=True,
                )
            else:
                changed |= _apply_autostart_accept_failure(
                    state,
                    now_ms_value=now_ms_value,
                    runtime_config=runtime_config,
                    error_code=error or "autostart_enable_failed",
                    preserve_prompt_attribution=False,
                )

        changed |= mark_prompt_decision_token(state, prompt_token)
        if changed:
            state = save_autostart_prompt_state(state, config_manager)

    return {
        "ok": True,
        "state": build_autostart_prompt_snapshot(state),
    }


def get_autostart_prompt_state_response(*, config_manager=None) -> dict[str, Any]:
    with _AUTOSTART_STATE_LOCK:
        state = load_autostart_prompt_state(config_manager)
    return {
        "ok": True,
        "prompt_mode": "autostart",
        "state": build_public_autostart_prompt_snapshot(state),
    }
