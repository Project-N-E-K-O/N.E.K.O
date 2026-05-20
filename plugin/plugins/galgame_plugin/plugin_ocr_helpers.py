from __future__ import annotations

from typing import Any

from .models import (
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_NONE,
    DATA_SOURCE_OCR_READER,
    MODE_COMPANION,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    OCR_TRIGGER_MODES,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    READER_MODE_OCR,
    READER_MODES,
    json_copy,
)
from .ocr_reader import utc_now_iso


def _normalize_ocr_trigger_mode(value: str | None) -> str:
    normalized = str(value or OCR_TRIGGER_MODE_INTERVAL).strip().lower()
    if normalized not in OCR_TRIGGER_MODES:
        raise ValueError(f"invalid OCR trigger_mode: {value!r}")
    return normalized


def _normalize_reader_mode(value: str | None) -> str:
    normalized = str(READER_MODE_AUTO if value is None else value).strip().lower()
    if normalized not in READER_MODES:
        raise ValueError(f"invalid reader_mode: {value!r}")
    return normalized


def _session_candidate_has_text(candidate: Any) -> bool:
    session = getattr(candidate, "session", {})
    if not isinstance(session, dict):
        return False
    state = session.get("state", {})
    if not isinstance(state, dict):
        return False
    if str(state.get("text") or "").strip():
        return True
    choices = state.get("choices", [])
    return isinstance(choices, list) and bool(choices)


def _pending_data_source_for_reader_mode(
    reader_mode: str,
    *,
    memory_reader_allowed: bool,
    ocr_reader_allowed: bool,
    memory_reader_candidate_available: bool,
) -> str:
    if reader_mode == READER_MODE_MEMORY:
        return DATA_SOURCE_MEMORY_READER
    if reader_mode == READER_MODE_OCR:
        return DATA_SOURCE_OCR_READER
    if reader_mode == READER_MODE_AUTO:
        if memory_reader_candidate_available and memory_reader_allowed:
            return DATA_SOURCE_MEMORY_READER
        if ocr_reader_allowed:
            return DATA_SOURCE_OCR_READER
    return DATA_SOURCE_NONE


_AFTER_ADVANCE_SCREEN_REFRESH_STAGES = {
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
}


def _after_advance_screen_refresh_needed(
    *,
    local: dict[str, Any],
    ocr_reader_runtime: dict[str, Any],
    ocr_reader_allowed: bool,
    ocr_trigger_mode: str,
) -> bool:
    if ocr_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
        return False
    if not ocr_reader_allowed:
        return False
    if str(local.get("active_data_source") or "") != DATA_SOURCE_OCR_READER:
        return False
    if str(ocr_reader_runtime.get("status") or "") != "active":
        return False
    context_state = str(ocr_reader_runtime.get("ocr_context_state") or "")
    detail = str(ocr_reader_runtime.get("detail") or "")
    snapshot = local.get("latest_snapshot")
    snapshot_obj = snapshot if isinstance(snapshot, dict) else {}
    screen_type_source = (
        snapshot_obj.get("screen_type")
        if "screen_type" in snapshot_obj
        else local.get("screen_type")
    )
    screen_type = str(screen_type_source or "")
    context_is_screen_classified = (
        context_state == "screen_classified" or detail == "screen_classified"
    )
    if not context_is_screen_classified:
        return False
    screen_confidence_source = (
        snapshot_obj.get("screen_confidence")
        if "screen_confidence" in snapshot_obj
        else local.get("screen_confidence", 0.0)
    )
    try:
        screen_confidence = float(screen_confidence_source)
    except (TypeError, ValueError):
        screen_confidence = 0.0
    if screen_confidence < 0.45:
        return False
    if screen_type == OCR_CAPTURE_PROFILE_STAGE_MENU:
        choices = snapshot_obj.get("choices")
        return (
            not bool(snapshot_obj.get("is_menu_open"))
            and not (choices if isinstance(choices, list) else [])
        )
    return screen_type in _AFTER_ADVANCE_SCREEN_REFRESH_STAGES


def _companion_after_advance_ocr_refresh_needed(
    *,
    local: dict[str, Any],
    ocr_reader_runtime: dict[str, Any],
    ocr_reader_allowed: bool,
    ocr_trigger_mode: str,
) -> bool:
    return (
        ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        and ocr_reader_allowed
        and str(local.get("mode") or "") == MODE_COMPANION
        and str(local.get("active_data_source") or "") == DATA_SOURCE_OCR_READER
        and str(ocr_reader_runtime.get("status") or "") in {"starting", "active"}
    )


def _ocr_reader_allowed_block_reason(
    *,
    reader_mode: str,
    memory_reader_default_is_unavailable: bool,
    memory_reader_recent_text_available: bool,
) -> str:
    if reader_mode == READER_MODE_MEMORY:
        return "reader_mode_memory_only"
    if memory_reader_recent_text_available:
        return "memory_reader_recent_text"
    if memory_reader_default_is_unavailable:
        return "memory_reader_default_unavailable"
    return ""


def _ocr_tick_block_reason(
    *,
    ocr_tick_allowed: bool,
    ocr_reader_manager_available: bool,
    ocr_reader_allowed: bool,
    ocr_reader_allowed_block_reason: str,
    ocr_trigger_mode: str,
    pending_ocr_advance_capture: bool,
    pending_ocr_delay_remaining: float,
    ocr_bootstrap_capture_needed: bool,
    after_advance_screen_refresh_needed: bool,
    companion_after_advance_ocr_refresh_needed: bool,
    ocr_reader_runtime: dict[str, Any],
    active_data_source: str,
    mode: str,
) -> str:
    if ocr_tick_allowed:
        return ""
    if not ocr_reader_allowed:
        return ocr_reader_allowed_block_reason or "ocr_reader_not_allowed"
    if not ocr_reader_manager_available:
        return "ocr_reader_unavailable"
    if pending_ocr_advance_capture and pending_ocr_delay_remaining > 0.0:
        return "waiting_pending_advance_delay"
    if ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE:
        runtime_status = str(ocr_reader_runtime.get("status") or "")
        if (
            not ocr_bootstrap_capture_needed
            and not after_advance_screen_refresh_needed
            and not companion_after_advance_ocr_refresh_needed
            and not pending_ocr_advance_capture
            and runtime_status == "active"
            and active_data_source == DATA_SOURCE_OCR_READER
            and mode != MODE_COMPANION
        ):
            return "trigger_mode_after_advance_waiting_for_input"
        return "trigger_mode_after_advance_waiting_for_refresh"
    return "tick_gate_closed"


def _ocr_emit_block_reason(
    *,
    ocr_tick_allowed: bool,
    ocr_reader_stable_event_emitted: bool,
    ocr_reader_runtime: dict[str, Any],
) -> str:
    if not ocr_tick_allowed or ocr_reader_stable_event_emitted:
        return ""
    context_state = str(ocr_reader_runtime.get("ocr_context_state") or "")
    detail = str(ocr_reader_runtime.get("detail") or "")
    stable_block_reason = str(ocr_reader_runtime.get("stable_ocr_block_reason") or "")
    last_raw_text = str(ocr_reader_runtime.get("last_raw_ocr_text") or "").strip()
    if context_state == "capture_failed" or detail == "capture_failed":
        return "capture_failed"
    if bool(ocr_reader_runtime.get("stale_capture_backend")) or context_state == "stale_capture_backend":
        return "stale_capture_backend"
    if context_state == "screen_classified" or detail == "screen_classified":
        return "screen_classification_skipped_dialogue"
    if stable_block_reason:
        return stable_block_reason
    if detail == "receiving_observed_text" or context_state == "observed":
        return "waiting_for_repeat"
    if context_state in {"no_text", "diagnostic_required"} or detail in {
        "attached_no_text_yet",
        "self_ui_guard_blocked",
        "ocr_capture_diagnostic_required",
    }:
        return "no_dialogue_text"
    if last_raw_text:
        return "no_dialogue_text"
    return ""


def _apply_ocr_decision_diagnostics(
    ocr_reader_runtime: dict[str, Any],
    *,
    ocr_tick_allowed: bool,
    ocr_tick_block_reason: str,
    ocr_emit_block_reason: str,
    ocr_reader_allowed: bool,
    ocr_reader_allowed_block_reason: str,
    ocr_trigger_mode: str,
    active_data_source: str,
    ocr_tick_gate_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = json_copy(ocr_reader_runtime or {})
    waiting_for_advance = ocr_tick_block_reason == "trigger_mode_after_advance_waiting_for_input"
    display_source_not_ocr_reason = (
        f"active_data_source={active_data_source}"
        if active_data_source and active_data_source != DATA_SOURCE_OCR_READER
        else ""
    )
    runtime.update(
        {
            "ocr_tick_allowed": bool(ocr_tick_allowed),
            "ocr_tick_block_reason": str(ocr_tick_block_reason or ""),
            "ocr_emit_block_reason": str(ocr_emit_block_reason or ""),
            "ocr_reader_allowed": bool(ocr_reader_allowed),
            "ocr_reader_allowed_block_reason": str(ocr_reader_allowed_block_reason or ""),
            "ocr_trigger_mode_effective": str(ocr_trigger_mode or ""),
            "ocr_waiting_for_advance": waiting_for_advance,
            "ocr_waiting_for_advance_reason": (
                str(ocr_tick_block_reason or "") if waiting_for_advance else ""
            ),
            "ocr_last_tick_decision_at": utc_now_iso(),
            "display_source_not_ocr_reason": display_source_not_ocr_reason,
        }
    )
    if isinstance(ocr_tick_gate_diagnostics, dict):
        runtime.update(json_copy(ocr_tick_gate_diagnostics))
    return runtime


_OCR_BRIDGE_DIAGNOSTIC_RUNTIME_KEYS = (
    "ocr_tick_allowed",
    "ocr_tick_block_reason",
    "ocr_emit_block_reason",
    "ocr_reader_allowed",
    "ocr_reader_allowed_block_reason",
    "ocr_trigger_mode_effective",
    "ocr_waiting_for_advance",
    "ocr_waiting_for_advance_reason",
    "ocr_last_tick_decision_at",
    "display_source_not_ocr_reason",
    "ocr_tick_gate_allowed",
    "ocr_reader_manager_available",
    "pending_ocr_advance_capture",
    "pending_manual_foreground_ocr_capture",
    "pending_ocr_advance_reason",
    "pending_ocr_delay_remaining",
    "pending_ocr_advance_capture_age_seconds",
    "pending_ocr_advance_clear_reason",
    "ocr_bootstrap_capture_needed",
    "after_advance_screen_refresh_tick_needed",
    "companion_after_advance_ocr_refresh_tick_needed",
    "ocr_runtime_status",
    "active_data_source",
    "mode",
    "foreground_refresh_attempted",
    "foreground_refresh_skipped_reason",
    "ocr_tick_entered",
    "ocr_tick_lock_acquired",
    "ocr_fast_loop_delegated",
    "ocr_tick_skipped_reason",
)


def _merge_ocr_runtime_preserving_bridge_diagnostics(
    refreshed_runtime: dict[str, Any],
    previous_runtime: dict[str, Any],
) -> dict[str, Any]:
    runtime = json_copy(refreshed_runtime or {})
    previous = previous_runtime if isinstance(previous_runtime, dict) else {}
    for key in _OCR_BRIDGE_DIAGNOSTIC_RUNTIME_KEYS:
        if key not in runtime and key in previous:
            runtime[key] = json_copy(previous[key])
    return runtime
