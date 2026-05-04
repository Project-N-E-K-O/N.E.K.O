from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

from .game_llm_agent import GameLLMAgent
from .host_agent_adapter import HostAgentAdapter
from .llm_gateway import LLMGateway
from .memory_reader import MemoryReaderManager
from .ocr_reader import OcrReaderManager, utc_now_iso
from .dxcam_support import install_dxcam
from .models import (
    ADVANCE_SPEEDS,
    ADVANCE_SPEED_MEDIUM,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_NONE,
    DATA_SOURCE_OCR_READER,
    MODE_CHOICE_ADVISOR,
    MODE_COMPANION,
    MODES,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_SAVE_SCOPES,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGES,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    OCR_TRIGGER_MODES,
    parse_ocr_capture_profile_bucket_key,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    READER_MODE_OCR,
    READER_MODES,
    STATE_ACTIVE,
    STATE_ERROR,
    STORE_BOUND_GAME_ID,
    STORE_ADVANCE_SPEED,
    STORE_DEDUPE_WINDOW,
    STORE_EVENTS_BYTE_OFFSET,
    STORE_EVENTS_FILE_SIZE,
    STORE_LAST_ERROR,
    STORE_LAST_SEQ,
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_MEMORY_READER_TARGET,
    STORE_MODE,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_OCR_WINDOW_TARGET,
    STORE_PUSH_NOTIFICATIONS,
    STORE_READER_MODE,
    STORE_SESSION_ID,
    json_copy,
    make_error,
)
from .reader import tail_events_jsonl, warmup_replay_events
from .service import (
    apply_event_to_histories,
    apply_event_to_snapshot,
    apply_input_degraded_result,
    build_active_session_meta,
    build_config,
    build_explain_degraded_result,
    build_explain_context,
    build_history_payload,
    build_ocr_context_diagnostic,
    build_ocr_background_status,
    build_primary_diagnosis,
    build_snapshot_payload,
    build_status_payload,
    build_suggest_context,
    build_suggest_degraded_result,
    build_summarize_degraded_result,
    build_summarize_context,
    choose_candidate,
    clear_install_inspection_cache,
    derive_connection_state,
    filter_memory_reader_candidates,
    filter_ocr_reader_candidates,
    mode_allows_agent_actuation,
    next_poll_interval_for_state,
    rebuild_histories_from_events,
    scan_session_candidates,
)
from .rapidocr_support import install_rapidocr
from .state import GalgameSharedState, build_initial_state
from .store import GalgameStore
from .tesseract_support import install_tesseract
from .textractor_support import install_textractor
from .ui_api import build_open_ui_payload
from .screen_classifier import classify_screen_from_ocr, normalize_screen_type
from .screen_awareness_training import (
    evaluate_screen_awareness_model,
    train_screen_awareness_model,
)


def _format_install_entry_error(label: str, exc: Exception) -> str:
    message = str(exc or "").strip()
    prefix = f"{label} 安装失败"
    if not message:
        return prefix
    if message.startswith(prefix):
        return message
    return f"{prefix}：{message}"


def _log_plugin_noncritical(logger: Any, level: str, message: str, *args: Any) -> None:
    log_fn = getattr(logger, level, None)
    if not callable(log_fn):
        return
    try:
        log_fn(message, *args)
    except Exception:
        return


_OCR_BACKEND_SELECTIONS = {"auto", "rapidocr", "tesseract"}
_OCR_CAPTURE_BACKEND_SELECTIONS = {"auto", "smart", "dxcam", "imagegrab", "printwindow"}
_BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS = 45.0
_BRIDGE_TICK_INTERVAL_SECONDS = 1.0
# Foreground refresh TTL: repeated calls within two seconds return early so
# bridge_tick, advance monitor, and status payload refreshes stay idempotent.
_OCR_FOREGROUND_REFRESH_TTL_SECONDS = 2.0
_LATENCY_SAMPLE_LIMIT = 120
_LATENCY_MIN_SAMPLES_FOR_P95 = 5
_OCR_POLL_P95_DEGRADE_THRESHOLD_SECONDS = 3.0


def _duration_percentile(samples: list[float], percentile: float) -> float:
    values = sorted(float(item) for item in samples if float(item) >= 0.0)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * max(0.0, min(1.0, percentile))
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _duration_summary(samples: list[float]) -> dict[str, Any]:
    values = [float(item) for item in samples if float(item) >= 0.0]
    return {
        "sample_count": len(values),
        "p50_seconds": _duration_percentile(values, 0.50),
        "p95_seconds": _duration_percentile(values, 0.95),
    }
_OCR_FOREGROUND_ADVANCE_MONITOR_INTERVAL_SECONDS = 0.05
_OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS = 0.15
_OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS = 0.15
_OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS = 2.0


def _open_url_in_browser(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)
    elif sys.platform == "darwin":
        subprocess.run(["open", url], check=True)
    else:
        subprocess.run(["xdg-open", url], check=True)


def _normalize_ocr_trigger_mode(value: str | None) -> str:
    normalized = str(value or OCR_TRIGGER_MODE_INTERVAL).strip().lower()
    if normalized not in OCR_TRIGGER_MODES:
        raise ValueError(f"invalid OCR trigger_mode: {value!r}")
    return normalized


def _normalize_reader_mode(value: str | None) -> str:
    normalized = str(value or READER_MODE_AUTO).strip().lower()
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
    screen_type = str(snapshot_obj.get("screen_type") or local.get("screen_type") or "")
    context_is_screen_classified = (
        context_state == "screen_classified" or detail == "screen_classified"
    )
    if not context_is_screen_classified:
        return False
    try:
        screen_confidence = float(
            snapshot_obj.get("screen_confidence")
            or local.get("screen_confidence")
            or 0.0
        )
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


def _normalize_ocr_capture_profile_stage(stage: str | None) -> str:
    normalized = str(stage or OCR_CAPTURE_PROFILE_STAGE_DEFAULT).strip().lower()
    if normalized not in OCR_CAPTURE_PROFILE_STAGES:
        raise ValueError(f"invalid OCR capture profile stage: {stage!r}")
    return normalized


def _normalize_ocr_capture_profile_save_scope(save_scope: str | None) -> str:
    normalized = str(save_scope or "").strip().lower()
    if not normalized:
        return ""
    if normalized not in OCR_CAPTURE_PROFILE_SAVE_SCOPES:
        raise ValueError(f"invalid OCR capture profile save_scope: {save_scope!r}")
    return normalized


def _is_ratio_profile_payload(value: object) -> bool:
    return isinstance(value, dict) and all(key in value for key in OCR_CAPTURE_PROFILE_RATIO_KEYS)


def _normalize_ocr_capture_profile_payload(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("capture_profile must be an object")
    normalized: dict[str, float] = {}
    for key in OCR_CAPTURE_PROFILE_RATIO_KEYS:
        raw = value.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be a number")
        try:
            parsed = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if parsed < 0.0 or parsed >= 1.0:
            raise ValueError(f"{key} must be >= 0.0 and < 1.0")
        normalized[key] = parsed
    if normalized["left_inset_ratio"] + normalized["right_inset_ratio"] >= 1.0:
        raise ValueError("left_inset_ratio + right_inset_ratio must be < 1.0")
    if normalized["top_ratio"] + normalized["bottom_inset_ratio"] >= 1.0:
        raise ValueError("top_ratio + bottom_inset_ratio must be < 1.0")
    return normalized


def _capture_profile_entry_to_stage_map(value: object) -> dict[str, dict[str, float]]:
    if _is_ratio_profile_payload(value):
        return {OCR_CAPTURE_PROFILE_STAGE_DEFAULT: json_copy(value)}
    raw = value if isinstance(value, dict) else {}
    stage_map: dict[str, dict[str, float]] = {}
    for stage_name, profile in raw.items():
        normalized_stage_name = str(stage_name or "").strip().lower()
        if (
            not normalized_stage_name
            or normalized_stage_name == OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY
            or not _is_ratio_profile_payload(profile)
        ):
            continue
        stage_map[normalized_stage_name] = json_copy(profile)
    return stage_map


def _capture_profile_bucket_entry_to_stage_map(value: object) -> dict[str, dict[str, float]]:
    raw = value if isinstance(value, dict) else {}
    stage_map: dict[str, dict[str, float]] = {}
    raw_stages = raw.get("stages")
    if not isinstance(raw_stages, dict):
        return stage_map
    for stage_name, profile in raw_stages.items():
        normalized_stage_name = str(stage_name or "").strip().lower()
        if not normalized_stage_name or not _is_ratio_profile_payload(profile):
            continue
        stage_map[normalized_stage_name] = json_copy(profile)
    return stage_map


def _capture_profile_entry_to_window_bucket_map(value: object) -> dict[str, dict[str, Any]]:
    raw = value if isinstance(value, dict) else {}
    raw_buckets = raw.get(OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY)
    if not isinstance(raw_buckets, dict):
        return {}
    bucket_map: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_value in raw_buckets.items():
        normalized_bucket_key = str(bucket_key or "").strip().lower()
        parsed_dimensions = parse_ocr_capture_profile_bucket_key(normalized_bucket_key)
        if not normalized_bucket_key or parsed_dimensions is None or not isinstance(bucket_value, dict):
            continue
        try:
            width = int(bucket_value.get("width") or parsed_dimensions[0])
            height = int(bucket_value.get("height") or parsed_dimensions[1])
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        try:
            aspect_ratio = float(
                bucket_value.get("aspect_ratio") or compute_ocr_window_aspect_ratio(width, height)
            )
        except (TypeError, ValueError):
            aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
        stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_value)
        if not stage_map:
            continue
        bucket_map[normalized_bucket_key] = {
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "stages": stage_map,
        }
    return bucket_map


def _window_bucket_map_to_capture_profile_payload(
    bucket_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_value in bucket_map.items():
        normalized_bucket_key = str(bucket_key or "").strip().lower()
        if not normalized_bucket_key or not isinstance(bucket_value, dict):
            continue
        try:
            width = int(bucket_value.get("width") or 0)
            height = int(bucket_value.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        try:
            aspect_ratio = float(
                bucket_value.get("aspect_ratio") or compute_ocr_window_aspect_ratio(width, height)
            )
        except (TypeError, ValueError):
            aspect_ratio = compute_ocr_window_aspect_ratio(width, height)
        stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_value)
        if not stage_map:
            continue
        payload[normalized_bucket_key] = {
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "stages": {
                stage_name: json_copy(profile)
                for stage_name, profile in stage_map.items()
            },
        }
    return payload


def _capture_profile_components_to_entry(
    stage_map: dict[str, dict[str, float]],
    window_bucket_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not window_bucket_map and len(stage_map) == 1 and OCR_CAPTURE_PROFILE_STAGE_DEFAULT in stage_map:
        return json_copy(stage_map[OCR_CAPTURE_PROFILE_STAGE_DEFAULT])
    payload = {stage_name: json_copy(profile) for stage_name, profile in stage_map.items()}
    bucket_payload = _window_bucket_map_to_capture_profile_payload(window_bucket_map)
    if bucket_payload:
        payload[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY] = bucket_payload
    return payload


class GalgamePluginConfigService:
    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    def persist_preferences(
        self,
        *,
        bound_game_id: str,
        mode: str,
        push_notifications: bool,
        advance_speed: str,
    ) -> None:
        self._plugin._persist.persist_preferences(
            bound_game_id=bound_game_id,
            mode=mode,
            push_notifications=push_notifications,
            advance_speed=advance_speed,
        )

    def persist_ocr_backend_selection(
        self,
        *,
        backend_selection: str | None,
        capture_backend: str | None,
    ) -> None:
        if backend_selection is not None:
            self._plugin._persist.persist_config_override(
                STORE_OCR_BACKEND_SELECTION,
                backend_selection,
            )
        if capture_backend is not None:
            self._plugin._persist.persist_config_override(
                STORE_OCR_CAPTURE_BACKEND,
                capture_backend,
            )

    def persist_reader_mode(self, *, reader_mode: str) -> None:
        self._plugin._persist.persist_config_override(STORE_READER_MODE, reader_mode)

    def persist_ocr_timing(
        self,
        *,
        poll_interval_seconds: float,
        trigger_mode: str,
        fast_loop_enabled: bool | None = None,
    ) -> None:
        self._plugin._persist.persist_config_override(
            STORE_OCR_POLL_INTERVAL_SECONDS,
            poll_interval_seconds,
        )
        self._plugin._persist.persist_config_override(
            STORE_OCR_TRIGGER_MODE,
            trigger_mode,
        )
        if fast_loop_enabled is not None:
            self._plugin._persist.persist_config_override(
                STORE_OCR_FAST_LOOP_ENABLED,
                bool(fast_loop_enabled),
            )

    def persist_llm_vision(
        self,
        *,
        vision_enabled: bool,
        vision_max_image_px: int,
    ) -> None:
        self._plugin._persist.persist_config_override(
            STORE_LLM_VISION_ENABLED,
            bool(vision_enabled),
        )
        self._plugin._persist.persist_config_override(
            STORE_LLM_VISION_MAX_IMAGE_PX,
            int(vision_max_image_px),
        )

    def persist_ocr_screen_templates(self, templates: list[dict[str, Any]]) -> None:
        self._plugin._persist.persist_config_override(
            STORE_OCR_SCREEN_TEMPLATES,
            json_copy(templates),
        )

    def persist_runtime_state(self, payload: dict[str, Any]) -> None:
        def _payload_int(key: str) -> int:
            try:
                return int(payload.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        dedupe_window = payload.get("dedupe_window")
        last_error = payload.get("last_error")
        self._plugin._persist.persist_runtime(
            session_id=str(payload.get("active_session_id") or ""),
            events_byte_offset=_payload_int("events_byte_offset"),
            events_file_size=_payload_int("events_file_size"),
            last_seq=_payload_int("last_seq"),
            dedupe_window=list(dedupe_window) if isinstance(dedupe_window, list) else [],
            last_error=dict(last_error) if isinstance(last_error, dict) else {},
        )


@neko_plugin
class GalgamePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._state_lock = threading.Lock()
        self._poll_bridge_locks: dict[int, asyncio.Lock] = {}
        self._poll_bridge_thread_lock = threading.Lock()
        self._bridge_poll_task_lock = threading.RLock()
        self._textractor_install_lock = threading.Lock()
        self._tesseract_install_lock = threading.Lock()
        self._rapidocr_install_lock = threading.Lock()
        self._dxcam_install_lock = threading.Lock()
        self._cfg = None
        self._state = build_initial_state(
            mode=MODE_COMPANION,
            push_notifications=True,
            advance_speed=ADVANCE_SPEED_MEDIUM,
        )
        self._persist = GalgameStore(self.store, self.logger)
        self._config_service = GalgamePluginConfigService(self)
        self._host_agent_adapter: HostAgentAdapter | None = None
        self._llm_gateway: LLMGateway | None = None
        self._game_agent: GameLLMAgent | None = None
        self._memory_reader_manager: MemoryReaderManager | None = None
        self._ocr_reader_manager: OcrReaderManager | None = None
        self._ocr_foreground_advance_monitor_task: asyncio.Task[None] | None = None
        self._ocr_fast_loop_task: asyncio.Task[None] | None = None
        self._ocr_fast_loop_started_at = 0.0
        self._ocr_fast_loop_last_duration_seconds = 0.0
        self._ocr_fast_loop_last_run_at = 0.0
        self._ocr_fast_loop_iteration_count = 0
        self._fast_loop_auto_enabled = False
        self._fast_loop_consecutive_errors = 0
        self._ocr_reader_tick_lock = threading.Lock()
        self._ocr_poll_duration_samples: deque[float] = deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        self._bridge_poll_duration_samples: deque[float] = deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        self._ocr_auto_degrade_reason = ""
        self._ocr_auto_degrade_at = ""
        self._ocr_auto_degrade_count = 0
        self._bridge_poll_task: asyncio.Task[None] | Future[None] | None = None
        self._bridge_poll_loop: asyncio.AbstractEventLoop | None = None
        self._bridge_poll_thread: threading.Thread | None = None
        self._bridge_poll_thread_stop = threading.Event()
        self._bridge_poll_started_at = 0.0
        self._bridge_poll_finished_at = 0.0
        self._last_bridge_poll_duration_seconds = 0.0
        self._last_bridge_poll_launch_at = 0.0
        self._bridge_poll_launch_count = 0
        self._last_agent_tick_at = 0.0
        self._bridge_tick_last_started_at = 0.0
        self._bridge_tick_last_finished_at = 0.0
        self._bridge_tick_last_duration_seconds = 0.0
        self._bridge_tick_last_error = ""
        self._bridge_tick_launch_count = 0
        self._bridge_tick_shutdown_requested = False
        self._pending_ocr_advance_captures = 0
        self._last_ocr_advance_capture_requested_at = 0.0
        self._last_ocr_advance_capture_reason = ""
        self._last_ocr_foreground_refresh_at = 0.0
        self._last_memory_reader_text_game_id = ""
        self._last_memory_reader_text_session_id = ""
        self._last_memory_reader_text_seq = 0
        self._last_memory_reader_text_seen_at_monotonic = 0.0
        self._ocr_capture_profile_auto_apply_enabled = False
        self._ocr_capture_profile_pending_rollback: dict[str, Any] = {}
        self._ocr_capture_profile_last_rollback_reason = ""
        self._state_dirty = True
        self._cached_snapshot: dict[str, Any] | None = None

    def _update_memory_reader_text_freshness(
        self,
        runtime: dict[str, Any],
        *,
        now_monotonic: float,
    ) -> bool:
        if self._cfg is None:
            return False
        status = str(runtime.get("status") or "")
        game_id = str(runtime.get("game_id") or "")
        session_id = str(runtime.get("session_id") or "")
        try:
            last_text_seq = int(runtime.get("last_text_seq") or 0)
        except (TypeError, ValueError):
            last_text_seq = 0
        received_text_this_tick = (
            str(runtime.get("detail") or "") == "receiving_text" and last_text_seq > 0
        )
        try:
            threshold = max(
                0.0,
                float(self._cfg.ocr_reader_no_text_takeover_after_seconds),
            )
        except (TypeError, ValueError):
            threshold = 0.0

        with self._state_lock:
            if status not in {"attaching", "active"} or not game_id or not session_id:
                self._last_memory_reader_text_game_id = ""
                self._last_memory_reader_text_session_id = ""
                self._last_memory_reader_text_seq = 0
                self._last_memory_reader_text_seen_at_monotonic = 0.0
                runtime["last_text_recent"] = False
                runtime["last_text_age_seconds"] = 0.0
                return False

            tracked_changed = (
                game_id != self._last_memory_reader_text_game_id
                or session_id != self._last_memory_reader_text_session_id
                or last_text_seq < self._last_memory_reader_text_seq
            )
            if tracked_changed:
                self._last_memory_reader_text_game_id = game_id
                self._last_memory_reader_text_session_id = session_id
                self._last_memory_reader_text_seq = last_text_seq
                self._last_memory_reader_text_seen_at_monotonic = (
                    now_monotonic if received_text_this_tick else 0.0
                )
            elif last_text_seq > self._last_memory_reader_text_seq:
                self._last_memory_reader_text_seq = last_text_seq
                self._last_memory_reader_text_seen_at_monotonic = now_monotonic

            last_seen = self._last_memory_reader_text_seen_at_monotonic
            recent = (
                last_text_seq > 0
                and last_seen > 0.0
                and now_monotonic - last_seen <= threshold
            )
            runtime["last_text_recent"] = recent
            runtime["last_text_age_seconds"] = (
                max(0.0, now_monotonic - last_seen) if last_seen > 0.0 else 0.0
            )
            return recent

    def should_request_ocr_after_advance_capture(self) -> bool:
        return (
            self._cfg is not None
            and bool(self._cfg.ocr_reader_enabled)
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )

    def request_ocr_after_advance_capture(self, *, reason: str = "agent_advance") -> None:
        self._request_ocr_after_advance_capture_at(
            requested_at_monotonic=time.monotonic(),
            reason=reason,
        )

    def _request_ocr_after_advance_capture_for_event_age(
        self,
        *,
        event_age_seconds: float,
        reason: str,
        coalesced_count: int = 0,
    ) -> None:
        try:
            event_age = max(0.0, float(event_age_seconds or 0.0))
        except (TypeError, ValueError):
            event_age = 0.0
        self._request_ocr_after_advance_capture_at(
            requested_at_monotonic=time.monotonic() - event_age,
            reason=reason,
            coalesced_count=coalesced_count,
        )

    def _request_ocr_after_advance_capture_at(
        self,
        *,
        requested_at_monotonic: float,
        reason: str,
        coalesced_count: int = 0,
    ) -> None:
        del coalesced_count
        if not self.should_request_ocr_after_advance_capture():
            return
        with self._state_lock:
            self._pending_ocr_advance_captures = min(
                self._pending_ocr_advance_captures + 1,
                8,
            )
            self._last_ocr_advance_capture_requested_at = float(
                requested_at_monotonic or time.monotonic()
            )
            self._last_ocr_advance_capture_reason = str(reason or "agent_advance")
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None
        # _state_lock → _bridge_poll_task_lock 路径；反向路径在
        # _start_background_bridge_poll:2215-2218。均运行在 asyncio 单线程下安全，
        # 新增后台线程代码路径时需审计锁序。
        self._start_background_bridge_poll()

    def latest_ocr_vision_snapshot(self) -> dict[str, Any]:
        if self._ocr_reader_manager is None:
            return {}
        snapshot_getter = getattr(self._ocr_reader_manager, "latest_vision_snapshot", None)
        if not callable(snapshot_getter):
            return {}
        return snapshot_getter()

    def _has_pending_ocr_advance_capture(self) -> bool:
        with self._state_lock:
            return self._pending_ocr_advance_captures > 0

    def _pending_ocr_advance_capture_delay_remaining(self) -> float:
        with self._state_lock:
            if self._pending_ocr_advance_captures <= 0:
                return 0.0
            requested_at = float(self._last_ocr_advance_capture_requested_at or 0.0)
        if requested_at <= 0.0:
            return 0.0
        elapsed = max(0.0, time.monotonic() - requested_at)
        return max(0.0, _OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS - elapsed)

    def _pending_ocr_advance_capture_age(self) -> float:
        with self._state_lock:
            if self._pending_ocr_advance_captures <= 0:
                return 0.0
            requested_at = float(self._last_ocr_advance_capture_requested_at or 0.0)
        if requested_at <= 0.0:
            return 0.0
        return max(0.0, time.monotonic() - requested_at)

    def _consume_ocr_advance_capture(self) -> None:
        with self._state_lock:
            if self._pending_ocr_advance_captures > 0:
                self._pending_ocr_advance_captures -= 1

    def _clear_pending_ocr_advance_captures_locked(self) -> None:
        self._pending_ocr_advance_captures = 0
        self._last_ocr_advance_capture_requested_at = 0.0
        self._last_ocr_advance_capture_reason = ""

    def _clear_pending_ocr_advance_captures(self) -> None:
        with self._state_lock:
            self._clear_pending_ocr_advance_captures_locked()

    def _snapshot_state(self, *, fresh: bool = False) -> dict[str, Any]:
        with self._state_lock:
            if not fresh and not self._state_dirty and self._cached_snapshot is not None:
                return self._cached_snapshot
            state = self._state
            raw = {
                "bound_game_id": state.bound_game_id,
                "available_game_ids": list(state.available_game_ids),
                "mode": state.mode,
                "push_notifications": state.push_notifications,
                "advance_speed": state.advance_speed,
                "active_game_id": state.active_game_id,
                "active_session_id": state.active_session_id,
                "active_session_meta": dict(state.active_session_meta),
                "active_data_source": state.active_data_source,
                "latest_snapshot": dict(state.latest_snapshot),
                "history_events": list(state.history_events),
                "history_lines": list(state.history_lines),
                "history_observed_lines": list(state.history_observed_lines),
                "history_choices": list(state.history_choices),
                "screen_type": state.screen_type,
                "screen_ui_elements": list(state.screen_ui_elements),
                "screen_confidence": state.screen_confidence,
                "screen_debug": dict(state.screen_debug),
                "dedupe_window": list(state.dedupe_window),
                "line_buffer": state.line_buffer,
                "stream_reset_pending": state.stream_reset_pending,
                "last_error": dict(state.last_error),
                "next_poll_at_monotonic": state.next_poll_at_monotonic,
                "current_connection_state": state.current_connection_state,
                "events_byte_offset": state.events_byte_offset,
                "events_file_size": state.events_file_size,
                "last_seq": state.last_seq,
                "last_seen_data_monotonic": state.last_seen_data_monotonic,
                "warmup_session_id": state.warmup_session_id,
                "memory_reader_runtime": dict(state.memory_reader_runtime),
                "memory_reader_target": dict(state.memory_reader_target),
                "ocr_reader_runtime": dict(state.ocr_reader_runtime),
                "ocr_capture_profiles": dict(state.ocr_capture_profiles),
                "ocr_window_target": dict(state.ocr_window_target),
                "plugin_error": state.plugin_error,
            }
            should_cache = not fresh
            if should_cache:
                self._state_dirty = False
                self._cached_snapshot = None
        snap = {
            "bound_game_id": raw["bound_game_id"],
            "available_game_ids": raw["available_game_ids"],
            "mode": raw["mode"],
            "push_notifications": raw["push_notifications"],
            "advance_speed": raw["advance_speed"],
            "active_game_id": raw["active_game_id"],
            "active_session_id": raw["active_session_id"],
            "active_session_meta": json_copy(raw["active_session_meta"]),
            "active_data_source": raw["active_data_source"],
            "latest_snapshot": json_copy(raw["latest_snapshot"]),
            "history_events": json_copy(raw["history_events"]),
            "history_lines": json_copy(raw["history_lines"]),
            "history_observed_lines": json_copy(raw["history_observed_lines"]),
            "history_choices": json_copy(raw["history_choices"]),
            "screen_type": raw["screen_type"],
            "screen_ui_elements": json_copy(raw["screen_ui_elements"]),
            "screen_confidence": raw["screen_confidence"],
            "screen_debug": json_copy(raw["screen_debug"]),
            "dedupe_window": json_copy(raw["dedupe_window"]),
            "line_buffer": raw["line_buffer"],
            "stream_reset_pending": raw["stream_reset_pending"],
            "last_error": json_copy(raw["last_error"]),
            "next_poll_at_monotonic": raw["next_poll_at_monotonic"],
            "current_connection_state": raw["current_connection_state"],
            "events_byte_offset": raw["events_byte_offset"],
            "events_file_size": raw["events_file_size"],
            "last_seq": raw["last_seq"],
            "last_seen_data_monotonic": raw["last_seen_data_monotonic"],
            "warmup_session_id": raw["warmup_session_id"],
            "memory_reader_runtime": json_copy(raw["memory_reader_runtime"]),
            "memory_reader_target": json_copy(raw["memory_reader_target"]),
            "ocr_reader_runtime": json_copy(raw["ocr_reader_runtime"]),
            "ocr_capture_profiles": json_copy(raw["ocr_capture_profiles"]),
            "ocr_window_target": json_copy(raw["ocr_window_target"]),
            "plugin_error": raw["plugin_error"],
        }
        if should_cache:
            with self._state_lock:
                if not self._state_dirty:
                    self._cached_snapshot = snap
            return snap
        return snap

    def _mark_state_dirty(self) -> None:
        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

    @staticmethod
    def _ocr_capture_scope_label(save_scope: str) -> str:
        if save_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            return "当前窗口分辨率"
        return "进程通用回退"

    @staticmethod
    def _ocr_capture_stage_label(stage: str) -> str:
        labels = {
            OCR_CAPTURE_PROFILE_STAGE_DEFAULT: "通用区域",
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: "对白区",
            OCR_CAPTURE_PROFILE_STAGE_MENU: "菜单区",
            OCR_CAPTURE_PROFILE_STAGE_TITLE: "标题/主菜单",
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD: "存读档",
            OCR_CAPTURE_PROFILE_STAGE_CONFIG: "设置",
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION: "转场",
            OCR_CAPTURE_PROFILE_STAGE_GALLERY: "回想/鉴赏",
            OCR_CAPTURE_PROFILE_STAGE_MINIGAME: "小游戏",
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER: "Game Over",
        }
        return labels.get(stage, stage)

    @staticmethod
    def _process_name_matches(left: str, right: str) -> bool:
        return bool(left.strip()) and left.strip().lower() == right.strip().lower()

    def _resolve_ocr_capture_profile_save_context(
        self,
        *,
        process_name: str,
        save_scope: str | None,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        runtime_process_name = str(runtime.get("process_name") or "").strip()
        runtime_width = max(0, int(runtime.get("width") or 0))
        runtime_height = max(0, int(runtime.get("height") or 0))
        resolved_width = max(0, int(width or runtime_width))
        resolved_height = max(0, int(height or runtime_height))
        normalized_scope = _normalize_ocr_capture_profile_save_scope(save_scope)
        if not normalized_scope:
            explicit_window_size = int(width or 0) > 0 and int(height or 0) > 0
            normalized_scope = (
                OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
                if explicit_window_size
                and self._process_name_matches(process_name, runtime_process_name)
                and resolved_width > 0
                and resolved_height > 0
                else OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK
            )
        bucket_key = ""
        aspect_ratio = 0.0
        if normalized_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            if resolved_width <= 0 or resolved_height <= 0:
                raise ValueError("当前没有可用的 OCR 窗口尺寸，无法保存到当前窗口分辨率")
            bucket_key = build_ocr_capture_profile_bucket_key(resolved_width, resolved_height).lower()
            aspect_ratio = compute_ocr_window_aspect_ratio(resolved_width, resolved_height)
        return {
            "save_scope": normalized_scope,
            "width": resolved_width,
            "height": resolved_height,
            "bucket_key": bucket_key,
            "aspect_ratio": aspect_ratio,
            "runtime": runtime,
        }

    async def _save_ocr_capture_profile_payload(
        self,
        *,
        process_name: str,
        stage: str,
        capture_profile: dict[str, float] | None,
        clear: bool,
        save_scope: str | None,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        normalized_process_name = str(process_name or "").strip()
        if not normalized_process_name:
            raise ValueError("process_name is required")
        normalized_stage = _normalize_ocr_capture_profile_stage(stage)
        context = self._resolve_ocr_capture_profile_save_context(
            process_name=normalized_process_name,
            save_scope=save_scope,
            width=width,
            height=height,
        )
        with self._state_lock:
            profiles = json_copy(self._state.ocr_capture_profiles)
        existing_entry = profiles.get(normalized_process_name)
        process_stage_map = _capture_profile_entry_to_stage_map(existing_entry)
        window_bucket_map = _capture_profile_entry_to_window_bucket_map(existing_entry)
        normalized_profile = json_copy(capture_profile or {})
        resolved_scope = str(context["save_scope"] or OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK)
        bucket_key = str(context.get("bucket_key") or "")
        if resolved_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK:
            target_stage_map = process_stage_map
        else:
            bucket_entry = window_bucket_map.get(bucket_key) or {
                "width": int(context.get("width") or 0),
                "height": int(context.get("height") or 0),
                "aspect_ratio": float(context.get("aspect_ratio") or 0.0),
                "stages": {},
            }
            target_stage_map = _capture_profile_bucket_entry_to_stage_map(bucket_entry)
        if clear:
            target_stage_map.pop(normalized_stage, None)
        else:
            target_stage_map[normalized_stage] = normalized_profile
        if resolved_scope == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            if target_stage_map:
                window_bucket_map[bucket_key] = {
                    "width": int(context.get("width") or 0),
                    "height": int(context.get("height") or 0),
                    "aspect_ratio": float(context.get("aspect_ratio") or 0.0),
                    "stages": target_stage_map,
                }
            else:
                window_bucket_map.pop(bucket_key, None)
        if not process_stage_map and not window_bucket_map:
            profiles.pop(normalized_process_name, None)
        else:
            profiles[normalized_process_name] = _capture_profile_components_to_entry(
                process_stage_map,
                window_bucket_map,
            )
        self._persist.persist_ocr_capture_profiles(profiles)
        with self._state_lock:
            self._state.ocr_capture_profiles = json_copy(profiles)
            self._state_dirty = True
            self._cached_snapshot = None
        if self._ocr_reader_manager is not None:
            self._ocr_reader_manager.update_capture_profiles(profiles)
            try:
                refreshed_runtime = (
                    self._ocr_reader_manager.refresh_runtime_capture_profile_selection()
                )
            except Exception as exc:
                self.logger.warning(
                    "galgame_plugin failed to refresh OCR runtime after saving capture profile: {}",
                    exc,
                )
            else:
                with self._state_lock:
                    self._state.ocr_reader_runtime = (
                        _merge_ocr_runtime_preserving_bridge_diagnostics(
                            refreshed_runtime,
                            self._state.ocr_reader_runtime,
                        )
                    )
                    self._state_dirty = True
                    self._cached_snapshot = None
        payload = {
            "process_name": normalized_process_name,
            "stage": normalized_stage,
            "capture_profile": normalized_profile if not clear else {},
            "cleared": bool(clear),
            "save_scope": resolved_scope,
            "bucket_key": bucket_key,
            "window_width": int(context.get("width") or 0),
            "window_height": int(context.get("height") or 0),
        }
        scope_label = self._ocr_capture_scope_label(resolved_scope)
        stage_label = self._ocr_capture_stage_label(normalized_stage)
        if clear:
            payload["summary"] = (
                f"OCR 截图校准已清空：{normalized_process_name} / {stage_label} / {scope_label}"
                + (f" / {bucket_key}" if bucket_key else "")
            )
        else:
            payload["summary"] = (
                f"OCR 截图校准已保存：{normalized_process_name} / {stage_label} / {scope_label}"
                + (f" / {bucket_key}" if bucket_key else "")
            )
        payload["status"] = await self._build_status_payload_async()
        return payload

    def _ocr_screen_template_runtime_context(self) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
            screen_type = str(self._state.screen_type or "")
            ui_elements = json_copy(self._state.screen_ui_elements)
        return runtime, screen_type, ui_elements if isinstance(ui_elements, list) else []

    @staticmethod
    def _ocr_template_keyword_candidates(
        runtime: dict[str, Any],
        ui_elements: list[dict[str, Any]],
    ) -> list[str]:
        candidates: list[str] = []
        for element in ui_elements[:10]:
            if not isinstance(element, dict):
                continue
            text = str(element.get("text") or "").strip()
            if text:
                candidates.append(text)
        raw_text = str(runtime.get("last_raw_ocr_text") or "")
        for line in raw_text.splitlines():
            text = line.strip()
            if text:
                candidates.append(text)
        result: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text = re.sub(r"\s+", " ", item).strip()
            if len(text) < 2 or len(text) > 48:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= 8:
                break
        return result

    @staticmethod
    def _ocr_screen_template_id(
        *,
        process_name: str,
        stage: str,
        width: int,
        height: int,
    ) -> str:
        stem = Path(process_name).stem if process_name else "current"
        base = f"{stem}-{stage}"
        if width > 0 and height > 0:
            base = f"{base}-{width}x{height}"
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-").lower()
        return (normalized or "screen-template")[:80]

    @staticmethod
    def _normalize_ocr_template_region_payload(region: object) -> dict[str, Any]:
        if not isinstance(region, dict):
            return {}
        try:
            left = float(region.get("left"))
            top = float(region.get("top"))
            right = float(region.get("right"))
            bottom = float(region.get("bottom"))
        except (TypeError, ValueError):
            return {}
        left = max(0.0, min(left, 1.0))
        top = max(0.0, min(top, 1.0))
        right = max(0.0, min(right, 1.0))
        bottom = max(0.0, min(bottom, 1.0))
        if right <= left or bottom <= top:
            return {}
        return {
            "id": str(region.get("id") or "visual-region-1").strip()[:80],
            "role": str(region.get("role") or "ui_region").strip()[:40],
            "left": round(left, 4),
            "top": round(top, 4),
            "right": round(right, 4),
            "bottom": round(bottom, 4),
            "min_overlap": 0.35,
        }

    def _build_ocr_screen_template_draft_payload(
        self,
        *,
        stage: str | None = None,
        region: object = None,
    ) -> dict[str, Any]:
        runtime, screen_type, ui_elements = self._ocr_screen_template_runtime_context()
        process_name = str(
            runtime.get("process_name")
            or runtime.get("effective_process_name")
            or ""
        ).strip()
        window_title = str(
            runtime.get("window_title")
            or runtime.get("effective_window_title")
            or ""
        ).strip()
        try:
            width = int(runtime.get("width") or 0)
            height = int(runtime.get("height") or 0)
        except (TypeError, ValueError):
            width = 0
            height = 0
        resolved_stage = normalize_screen_type(stage)
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = normalize_screen_type(screen_type)
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = normalize_screen_type(runtime.get("capture_stage"))
        if not resolved_stage or resolved_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            resolved_stage = OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
        keywords = self._ocr_template_keyword_candidates(runtime, ui_elements)
        normalized_region = self._normalize_ocr_template_region_payload(region)
        draft: dict[str, Any] = {
            "id": self._ocr_screen_template_id(
                process_name=process_name,
                stage=resolved_stage,
                width=width,
                height=height,
            ),
            "stage": resolved_stage,
            "priority": 100,
            "keywords": keywords,
            "min_keyword_hits": 1 if keywords else 0,
        }
        if normalized_region:
            draft["regions"] = [normalized_region]
            draft["min_region_hits"] = 1
        if process_name:
            draft["process_names"] = [process_name]
        if window_title:
            draft["window_title_contains"] = [window_title[:80]]
        if width > 0 and height > 0:
            draft["width"] = width
            draft["height"] = height
            draft["resolution_tolerance"] = 8
        if not keywords:
            draft["match_without_keywords"] = True
        sanitized = build_config({"ocr_reader": {"screen_templates": [draft]}}).ocr_reader_screen_templates
        if sanitized:
            draft = sanitized[0]
        return {
            "template": draft,
            "context": {
                "process_name": process_name,
                "window_title": window_title,
                "width": width,
                "height": height,
                "screen_type": screen_type,
                "capture_stage": str(runtime.get("capture_stage") or ""),
            },
        }

    def _resolve_screen_awareness_data_path(
        self,
        raw_path: str,
        *,
        default_filename: str,
    ) -> Path:
        if self._cfg is None:
            raise ValueError("galgame_plugin is not configured")
        raw = str(raw_path or "").strip()
        if raw:
            path = Path(os.path.expandvars(os.path.expanduser(raw)))
            if not path.is_absolute():
                path = Path(self._cfg.bridge_root) / path
            return path
        sample_path = ""
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        if isinstance(runtime, dict):
            sample_path = str(runtime.get("screen_awareness_sample_last_path") or "")
        if sample_path:
            return Path(sample_path)
        return Path(self._cfg.bridge_root) / "_screen_awareness_samples" / default_filename

    def _current_ocr_screen_template_validation_payload(
        self,
        screen_templates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sanitized = build_config(
            {"ocr_reader": {"screen_templates": screen_templates}}
        ).ocr_reader_screen_templates
        runtime, _screen_type, _ui_elements = self._ocr_screen_template_runtime_context()
        context = {
            "process_name": str(
                runtime.get("process_name")
                or runtime.get("effective_process_name")
                or ""
            ),
            "window_title": str(
                runtime.get("window_title")
                or runtime.get("effective_window_title")
                or ""
            ),
            "width": int(runtime.get("width") or 0),
            "height": int(runtime.get("height") or 0),
            "game_id": str(runtime.get("game_id") or ""),
        }
        classification = classify_screen_from_ocr(
            str(runtime.get("last_raw_ocr_text") or ""),
            screen_templates=sanitized,
            template_context=context,
        )
        return {
            "screen_templates": json_copy(sanitized),
            "classification": classification.to_payload(),
            "context": context,
            "summary": (
                f"OCR screen templates validated={len(sanitized)} "
                f"result={classification.screen_type} "
                f"confidence={classification.confidence:.2f}"
            ),
        }

    def _saved_ocr_capture_profile_payload(
        self,
        *,
        process_name: str,
        stage: str,
        save_scope: str,
        width: int = 0,
        height: int = 0,
    ) -> dict[str, Any]:
        normalized_process_name = str(process_name or "").strip()
        normalized_stage = _normalize_ocr_capture_profile_stage(stage)
        context = self._resolve_ocr_capture_profile_save_context(
            process_name=normalized_process_name,
            save_scope=save_scope,
            width=width,
            height=height,
        )
        with self._state_lock:
            profiles = json_copy(self._state.ocr_capture_profiles)
        existing_entry = profiles.get(normalized_process_name)
        if context["save_scope"] == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET:
            bucket = _capture_profile_entry_to_window_bucket_map(existing_entry).get(
                str(context.get("bucket_key") or "")
            )
            stage_map = _capture_profile_bucket_entry_to_stage_map(bucket)
        else:
            stage_map = _capture_profile_entry_to_stage_map(existing_entry)
        profile = stage_map.get(normalized_stage)
        return {
            "profile": json_copy(profile) if isinstance(profile, dict) else {},
            "exists": isinstance(profile, dict),
            "context": context,
        }

    def _recommended_ocr_capture_profile_from_runtime(
        self,
        runtime: dict[str, Any],
        *,
        allow_manual_override: bool,
    ) -> dict[str, Any]:
        profile = runtime.get("recommended_capture_profile")
        if not isinstance(profile, dict) or not profile:
            profile = (runtime.get("profile") or {}).get("recommended_capture_profile") if isinstance(runtime.get("profile"), dict) else {}
        normalized_profile = _normalize_ocr_capture_profile_payload(profile)
        manual_present = bool(
            runtime.get("recommended_capture_profile_manual_present")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_manual_present")
                if isinstance(runtime.get("profile"), dict)
                else False
            )
        )
        if manual_present and not allow_manual_override:
            raise ValueError("当前已有手动 OCR 截图校准；推荐不会自动覆盖手动 profile")
        stage = str(
            runtime.get("recommended_capture_profile_stage")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_stage")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
        )
        save_scope = str(
            runtime.get("recommended_capture_profile_save_scope")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_save_scope")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
        )
        process_name = str(
            runtime.get("recommended_capture_profile_process_name")
            or (
                (runtime.get("profile") or {}).get("recommended_capture_profile_process_name")
                if isinstance(runtime.get("profile"), dict)
                else ""
            )
            or runtime.get("process_name")
            or runtime.get("effective_process_name")
            or ""
        ).strip()
        if not process_name:
            raise ValueError("当前推荐缺少 process_name")
        return {
            "process_name": process_name,
            "stage": _normalize_ocr_capture_profile_stage(stage),
            "save_scope": _normalize_ocr_capture_profile_save_scope(save_scope)
            or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
            "capture_profile": normalized_profile,
            "width": int(runtime.get("width") or 0),
            "height": int(runtime.get("height") or 0),
            "reason": str(runtime.get("recommended_capture_profile_reason") or ""),
            "confidence": float(runtime.get("recommended_capture_profile_confidence") or 0.0),
        }

    async def _apply_recommended_ocr_capture_profile_payload(
        self,
        runtime: dict[str, Any],
        *,
        allow_manual_override: bool,
        reason: str,
    ) -> dict[str, Any]:
        recommendation = self._recommended_ocr_capture_profile_from_runtime(
            runtime,
            allow_manual_override=allow_manual_override,
        )
        previous = self._saved_ocr_capture_profile_payload(
            process_name=recommendation["process_name"],
            stage=recommendation["stage"],
            save_scope=recommendation["save_scope"],
            width=int(recommendation["width"] or 0),
            height=int(recommendation["height"] or 0),
        )
        payload = await self._save_ocr_capture_profile_payload(
            process_name=recommendation["process_name"],
            stage=recommendation["stage"],
            capture_profile=dict(recommendation["capture_profile"]),
            clear=False,
            save_scope=recommendation["save_scope"],
            width=int(recommendation["width"] or 0),
            height=int(recommendation["height"] or 0),
        )
        self._ocr_capture_profile_pending_rollback = {
            "process_name": recommendation["process_name"],
            "stage": recommendation["stage"],
            "save_scope": recommendation["save_scope"],
            "width": int(recommendation["width"] or 0),
            "height": int(recommendation["height"] or 0),
            "previous_profile": json_copy(previous["profile"]),
            "previous_exists": bool(previous["exists"]),
            "applied_profile": json_copy(recommendation["capture_profile"]),
            "applied_at": time.monotonic(),
            "failure_count": 0,
            "reason": reason or recommendation["reason"] or "recommended_capture_profile",
        }
        self._ocr_capture_profile_last_rollback_reason = ""
        payload["rollback_pending"] = True
        payload["auto_apply_enabled"] = bool(self._ocr_capture_profile_auto_apply_enabled)
        payload["summary"] = (
            f"OCR 推荐截图校准已应用：{recommendation['process_name']} / "
            f"{self._ocr_capture_stage_label(recommendation['stage'])}"
        )
        return payload

    async def _rollback_pending_ocr_capture_profile(
        self,
        *,
        reason: str,
    ) -> dict[str, Any] | None:
        pending = dict(self._ocr_capture_profile_pending_rollback or {})
        if not pending:
            self._ocr_capture_profile_last_rollback_reason = reason or "no_pending_rollback"
            return None
        previous_profile = pending.get("previous_profile")
        previous_exists = bool(pending.get("previous_exists"))
        payload = await self._save_ocr_capture_profile_payload(
            process_name=str(pending.get("process_name") or ""),
            stage=str(pending.get("stage") or OCR_CAPTURE_PROFILE_STAGE_DIALOGUE),
            capture_profile=dict(previous_profile or {}) if previous_exists else None,
            clear=not previous_exists,
            save_scope=str(pending.get("save_scope") or OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET),
            width=int(pending.get("width") or 0),
            height=int(pending.get("height") or 0),
        )
        self._ocr_capture_profile_pending_rollback = {}
        self._ocr_capture_profile_last_rollback_reason = reason or "recommended_profile_rollback"
        payload["rollback_reason"] = self._ocr_capture_profile_last_rollback_reason
        payload["summary"] = f"OCR 推荐截图校准已回滚：{payload['rollback_reason']}"
        return payload

    async def _maybe_auto_apply_recommended_ocr_capture_profile(
        self,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._ocr_capture_profile_auto_apply_enabled:
            return runtime
        if self._ocr_capture_profile_pending_rollback:
            return runtime
        profile = runtime.get("recommended_capture_profile")
        if not isinstance(profile, dict) or not profile:
            return runtime
        if bool(runtime.get("recommended_capture_profile_manual_present")):
            return runtime
        confidence = float(runtime.get("recommended_capture_profile_confidence") or 0.0)
        if confidence < 0.65:
            return runtime
        try:
            payload = await self._apply_recommended_ocr_capture_profile_payload(
                runtime,
                allow_manual_override=False,
                reason="auto_apply_recommended_capture_profile",
            )
        except Exception as exc:
            self._ocr_capture_profile_last_rollback_reason = f"auto_apply_failed: {exc}"
            return runtime
        status = payload.get("status")
        if isinstance(status, dict) and isinstance(status.get("ocr_reader_runtime"), dict):
            return json_copy(status["ocr_reader_runtime"])
        return runtime

    async def _update_ocr_capture_profile_rollback_state(
        self,
        runtime: dict[str, Any],
    ) -> dict[str, Any]:
        pending = self._ocr_capture_profile_pending_rollback
        if not pending:
            return runtime
        detail = str(runtime.get("detail") or "")
        diagnostic_required = bool(runtime.get("ocr_capture_diagnostic_required"))
        consecutive_no_text = int(runtime.get("consecutive_no_text_polls") or 0)
        stable_line = runtime.get("last_stable_line")
        observed_line = runtime.get("last_observed_line")
        has_text = (
            isinstance(stable_line, dict)
            and bool(str(stable_line.get("text") or "").strip())
        ) or (
            isinstance(observed_line, dict)
            and bool(str(observed_line.get("text") or "").strip())
        )
        if detail == "receiving_text" and has_text and consecutive_no_text <= 0:
            self._ocr_capture_profile_pending_rollback = {}
            self._ocr_capture_profile_last_rollback_reason = "recommended_profile_confirmed"
            return runtime
        failed = (
            detail == "capture_failed"
            or diagnostic_required
            or consecutive_no_text >= 3
        )
        if not failed:
            return runtime
        pending["failure_count"] = int(pending.get("failure_count") or 0) + 1
        if int(pending["failure_count"]) < 2:
            return runtime
        payload = await self._rollback_pending_ocr_capture_profile(
            reason=f"recommended_profile_failed:{detail or 'no_text'}",
        )
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, dict) and isinstance(status.get("ocr_reader_runtime"), dict):
                return json_copy(status["ocr_reader_runtime"])
        return runtime

    def _commit_state(self, payload: dict[str, Any]) -> None:
        with self._state_lock:
            state = self._state
            changed = False

            def assign(name: str, value: Any) -> None:
                nonlocal changed
                if getattr(state, name) != value:
                    setattr(state, name, value)
                    changed = True

            def assign_json(name: str, value: Any) -> None:
                nonlocal changed
                if getattr(state, name) != value:
                    setattr(state, name, json_copy(value))
                    changed = True

            commit_base = payload.get("_commit_base")
            if not isinstance(commit_base, dict):
                commit_base = {}

            def live_changed_since_snapshot(name: str) -> bool:
                return name in commit_base and getattr(state, name) != commit_base.get(name)

            def assign_if_live_unchanged(name: str, value: Any) -> None:
                if live_changed_since_snapshot(name):
                    return
                assign(name, value)

            def assign_json_if_live_unchanged(name: str, value: Any) -> None:
                if live_changed_since_snapshot(name):
                    return
                assign_json(name, value)

            assign_if_live_unchanged("bound_game_id", str(payload["bound_game_id"]))
            assign("available_game_ids", list(payload["available_game_ids"]))
            # Preferences can be changed through plugin entries while a bridge poll is in
            # flight. Keep the live values instead of restoring the poll's stale snapshot.
            if not live_changed_since_snapshot("mode"):
                assign("mode", state.mode if state.mode in MODES else str(payload["mode"]))
            if not live_changed_since_snapshot("push_notifications"):
                assign("push_notifications", bool(state.push_notifications))
            if not live_changed_since_snapshot("advance_speed"):
                assign("advance_speed", (
                    state.advance_speed
                    if state.advance_speed in ADVANCE_SPEEDS
                    else str(payload.get("advance_speed") or ADVANCE_SPEED_MEDIUM)
                ))
            assign("active_game_id", str(payload["active_game_id"]))
            assign("active_session_id", str(payload["active_session_id"]))
            assign_json("active_session_meta", payload["active_session_meta"])
            assign_if_live_unchanged("active_data_source", str(payload["active_data_source"]))
            assign_json("latest_snapshot", payload["latest_snapshot"])
            snapshot_obj = payload.get("latest_snapshot")
            snapshot_state = snapshot_obj if isinstance(snapshot_obj, dict) else {}
            assign("screen_type", str(snapshot_state.get("screen_type") or ""))
            assign_json(
                "screen_ui_elements",
                snapshot_state.get("screen_ui_elements") if isinstance(snapshot_state.get("screen_ui_elements"), list) else [],
            )
            try:
                screen_confidence = float(snapshot_state.get("screen_confidence") or 0.0)
            except (TypeError, ValueError):
                screen_confidence = 0.0
            assign("screen_confidence", screen_confidence)
            assign_json(
                "screen_debug",
                snapshot_state.get("screen_debug") if isinstance(snapshot_state.get("screen_debug"), dict) else {},
            )
            assign_json("history_events", payload["history_events"])
            assign_json("history_lines", payload["history_lines"])
            assign_json("history_observed_lines", payload.get("history_observed_lines", []))
            assign_json("history_choices", payload["history_choices"])
            assign_json("dedupe_window", payload["dedupe_window"])
            assign("line_buffer", payload["line_buffer"])
            assign("stream_reset_pending", bool(payload["stream_reset_pending"]))
            assign_json("last_error", payload["last_error"])
            assign("next_poll_at_monotonic", float(payload["next_poll_at_monotonic"]))
            assign("current_connection_state", str(payload["current_connection_state"]))
            assign("events_byte_offset", int(payload["events_byte_offset"]))
            assign("events_file_size", int(payload["events_file_size"]))
            assign("last_seq", int(payload["last_seq"]))
            assign("last_seen_data_monotonic", float(payload["last_seen_data_monotonic"]))
            assign("warmup_session_id", str(payload["warmup_session_id"]))
            assign_json("memory_reader_runtime", payload["memory_reader_runtime"])
            assign_json_if_live_unchanged("memory_reader_target", payload["memory_reader_target"])
            assign_json("ocr_reader_runtime", payload["ocr_reader_runtime"])
            assign_json_if_live_unchanged("ocr_capture_profiles", payload["ocr_capture_profiles"])
            assign_json_if_live_unchanged("ocr_window_target", payload["ocr_window_target"])
            assign("plugin_error", str(payload["plugin_error"]))
            if changed:
                self._state_dirty = True
                self._cached_snapshot = None

    def _record_error(self, error: dict[str, Any]) -> None:
        with self._state_lock:
            self._state.last_error = json_copy(error)
            self._state_dirty = True
            self._cached_snapshot = None

    def _record_ocr_poll_duration(self, runtime: dict[str, Any]) -> None:
        try:
            duration = float(runtime.get("last_poll_duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0.0:
            return
        with self._state_lock:
            self._ocr_poll_duration_samples.append(duration)
        self._maybe_auto_degrade_screen_awareness()

    def _record_bridge_poll_duration(self, duration: float) -> None:
        if duration <= 0.0:
            return
        with self._state_lock:
            self._bridge_poll_duration_samples.append(float(duration))

    def _maybe_auto_degrade_screen_awareness(self) -> None:
        if self._cfg is None:
            return
        mode = str(
            getattr(self._cfg, "ocr_reader_screen_awareness_latency_mode", "balanced")
            or "balanced"
        ).strip().lower()
        if mode == "aggressive":
            mode = "full"
        if mode != "full":
            return
        with self._state_lock:
            samples = list(self._ocr_poll_duration_samples)
        if len(samples) < _LATENCY_MIN_SAMPLES_FOR_P95:
            return
        p95 = _duration_percentile(samples, 0.95)
        if p95 <= _OCR_POLL_P95_DEGRADE_THRESHOLD_SECONDS:
            return
        reason = (
            "ocr_poll_p95_exceeded_3s; "
            f"p95={p95:.2f}s; screen_awareness_latency_mode full->balanced"
        )
        self._cfg.ocr_reader_screen_awareness_latency_mode = "balanced"
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._record_error(
                    make_error(
                        f"apply OCR latency auto-degrade failed: {exc}",
                        source="ocr_reader",
                        kind="warning",
                    )
                )
                return
        with self._state_lock:
            self._ocr_auto_degrade_reason = reason
            self._ocr_auto_degrade_at = utc_now_iso()
            self._ocr_auto_degrade_count += 1

    def _bridge_poll_debug_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._bridge_poll_task_lock:
            with self._state_lock:
                bridge_poll_task = self._bridge_poll_task
                bridge_poll_started_at = float(self._bridge_poll_started_at or 0.0)
                last_bridge_poll_duration_seconds = float(
                    self._last_bridge_poll_duration_seconds or 0.0
                )
                last_agent_tick_at = float(self._last_agent_tick_at or 0.0)
                bridge_tick_last_started_at = float(self._bridge_tick_last_started_at or 0.0)
                bridge_tick_last_finished_at = float(self._bridge_tick_last_finished_at or 0.0)
                bridge_tick_last_duration_seconds = float(
                    self._bridge_tick_last_duration_seconds or 0.0
                )
                bridge_tick_last_error = str(self._bridge_tick_last_error or "")
                bridge_tick_launch_count = int(self._bridge_tick_launch_count or 0)
                last_bridge_poll_launch_at = float(self._last_bridge_poll_launch_at or 0.0)
                bridge_poll_launch_count = int(self._bridge_poll_launch_count or 0)
                next_poll_at = float(self._state.next_poll_at_monotonic or 0.0)
                pending_ocr_advance_captures = int(self._pending_ocr_advance_captures or 0)
                ocr_fast_loop_task = self._ocr_fast_loop_task
                ocr_fast_loop_started_at = float(self._ocr_fast_loop_started_at or 0.0)
                ocr_fast_loop_last_duration_seconds = float(
                    self._ocr_fast_loop_last_duration_seconds or 0.0
                )
                ocr_fast_loop_last_run_at = float(self._ocr_fast_loop_last_run_at or 0.0)
                ocr_fast_loop_iteration_count = int(
                    self._ocr_fast_loop_iteration_count or 0
                )
                ocr_poll_duration_summary = _duration_summary(
                    list(self._ocr_poll_duration_samples)
                )
                bridge_poll_duration_summary = _duration_summary(
                    list(self._bridge_poll_duration_samples)
                )
                ocr_auto_degrade_reason = str(self._ocr_auto_degrade_reason or "")
                ocr_auto_degrade_at = str(self._ocr_auto_degrade_at or "")
                ocr_auto_degrade_count = int(self._ocr_auto_degrade_count or 0)
                last_ocr_advance_capture_requested_at = float(
                    self._last_ocr_advance_capture_requested_at or 0.0
                )
                last_ocr_advance_capture_reason = str(
                    self._last_ocr_advance_capture_reason or ""
                )
        poll_running = bridge_poll_task is not None and not bridge_poll_task.done()
        ocr_foreground_advance_monitor_running = (
            self._ocr_foreground_advance_monitor_task is not None
            and not self._ocr_foreground_advance_monitor_task.done()
        )
        ocr_fast_loop_running = (
            ocr_fast_loop_task is not None and not ocr_fast_loop_task.done()
        )
        ocr_fast_loop_inflight_seconds = (
            max(0.0, now - ocr_fast_loop_started_at)
            if ocr_fast_loop_running and ocr_fast_loop_started_at > 0.0
            else 0.0
        )
        inflight_seconds = (
            max(0.0, now - bridge_poll_started_at)
            if poll_running and bridge_poll_started_at > 0.0
            else 0.0
        )
        next_poll_in_seconds = max(0.0, next_poll_at - now) if next_poll_at > 0.0 else 0.0
        last_agent_tick_age_seconds = (
            max(0.0, now - last_agent_tick_at) if last_agent_tick_at > 0.0 else 0.0
        )
        bridge_tick_last_age_seconds = (
            max(0.0, now - bridge_tick_last_started_at)
            if bridge_tick_last_started_at > 0.0
            else 0.0
        )
        pending_ocr_advance_capture_age_seconds = (
            max(0.0, now - last_ocr_advance_capture_requested_at)
            if pending_ocr_advance_captures > 0
            and last_ocr_advance_capture_requested_at > 0.0
            else 0.0
        )
        pending_ocr_delay_remaining = (
            max(0.0, _OCR_AFTER_ADVANCE_CAPTURE_DELAY_SECONDS - pending_ocr_advance_capture_age_seconds)
            if pending_ocr_advance_captures > 0
            else 0.0
        )
        pending_manual_foreground_ocr_capture = (
            pending_ocr_advance_captures > 0
            and last_ocr_advance_capture_reason
            in {"manual_foreground_advance", "foreground_target_activated"}
        )
        return {
            "bridge_poll_running": poll_running,
            "bridge_poll_inflight_seconds": inflight_seconds,
            "last_bridge_poll_duration_seconds": last_bridge_poll_duration_seconds,
            "next_bridge_poll_in_seconds": next_poll_in_seconds,
            "last_agent_tick_at": last_agent_tick_at,
            "last_agent_tick_age_seconds": last_agent_tick_age_seconds,
            "bridge_tick_last_started_at": bridge_tick_last_started_at,
            "bridge_tick_last_finished_at": bridge_tick_last_finished_at,
            "bridge_tick_last_duration_seconds": bridge_tick_last_duration_seconds,
            "bridge_tick_last_error": bridge_tick_last_error,
            "bridge_tick_launch_count": bridge_tick_launch_count,
            "bridge_tick_auto_running": (
                bridge_tick_launch_count > 0
                and not bridge_tick_last_error
                and bridge_tick_last_age_seconds < 5.0
            ),
            "bridge_tick_last_age_seconds": bridge_tick_last_age_seconds,
            "last_bridge_poll_launch_at": last_bridge_poll_launch_at,
            "bridge_poll_launch_count": bridge_poll_launch_count,
            "ocr_foreground_advance_monitor_running": ocr_foreground_advance_monitor_running,
            "ocr_fast_loop_enabled": bool(
                self._cfg is not None
                and getattr(self._cfg, "ocr_reader_fast_loop_enabled", False)
            ),
            "ocr_fast_loop_running": ocr_fast_loop_running,
            "ocr_fast_loop_inflight_seconds": ocr_fast_loop_inflight_seconds,
            "ocr_fast_loop_last_duration_seconds": ocr_fast_loop_last_duration_seconds,
            "ocr_fast_loop_last_run_at": ocr_fast_loop_last_run_at,
            "ocr_fast_loop_iteration_count": ocr_fast_loop_iteration_count,
            "ocr_poll_latency": ocr_poll_duration_summary,
            "ocr_poll_latency_sample_count": ocr_poll_duration_summary["sample_count"],
            "ocr_poll_duration_p50_seconds": ocr_poll_duration_summary["p50_seconds"],
            "ocr_poll_duration_p95_seconds": ocr_poll_duration_summary["p95_seconds"],
            "bridge_poll_latency": bridge_poll_duration_summary,
            "bridge_poll_latency_sample_count": bridge_poll_duration_summary["sample_count"],
            "bridge_poll_duration_p50_seconds": bridge_poll_duration_summary["p50_seconds"],
            "bridge_poll_duration_p95_seconds": bridge_poll_duration_summary["p95_seconds"],
            "ocr_auto_degrade_reason": ocr_auto_degrade_reason,
            "ocr_auto_degrade_at": ocr_auto_degrade_at,
            "ocr_auto_degrade_count": ocr_auto_degrade_count,
            "pending_ocr_advance_captures": pending_ocr_advance_captures,
            "pending_ocr_advance_capture": pending_ocr_advance_captures > 0,
            "pending_manual_foreground_ocr_capture": pending_manual_foreground_ocr_capture,
            "pending_ocr_delay_remaining": pending_ocr_delay_remaining,
            "pending_ocr_advance_capture_age_seconds": pending_ocr_advance_capture_age_seconds,
            "pending_ocr_advance_reason": last_ocr_advance_capture_reason,
            "last_ocr_advance_capture_reason": last_ocr_advance_capture_reason,
        }

    def _clear_completed_background_bridge_poll(
        self,
        completed_task: asyncio.Task[None] | Future[None] | None = None,
    ) -> None:
        with self._bridge_poll_task_lock:
            task = self._bridge_poll_task
            if task is None or not task.done():
                return
            if completed_task is not None and task is not completed_task:
                return
            self._bridge_poll_task = None
        if task.cancelled():
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
        except Exception as exc:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            self._record_error(
                make_error(
                    f"bridge background poll failed after completion: {exc}",
                    source="bridge_reader",
                    kind="error",
                )
            )

    def _ensure_bridge_poll_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._bridge_poll_loop
        thread = self._bridge_poll_thread
        if loop is not None and thread is not None and thread.is_alive() and not loop.is_closed():
            return loop

        ready = threading.Event()
        holder: dict[str, asyncio.AbstractEventLoop] = {}
        self._bridge_poll_thread_stop.clear()

        def _run_loop() -> None:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            holder["loop"] = worker_loop
            ready.set()
            try:
                worker_loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(worker_loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    worker_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                worker_loop.close()

        thread = threading.Thread(
            target=_run_loop,
            name="galgame-bridge-poll",
            daemon=True,
        )
        thread.start()
        if not ready.wait(timeout=2.0):
            self._record_error(
                make_error(
                    "bridge background poll loop failed to start",
                    source="bridge_reader",
                    kind="error",
                )
            )
            return None
        self._bridge_poll_loop = holder.get("loop")
        self._bridge_poll_thread = thread
        return self._bridge_poll_loop

    async def _cancel_bridge_poll_loop_tasks_before_stop(self) -> None:
        current_task = asyncio.current_task()
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not current_task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def _stop_bridge_poll_loop(self) -> None:
        loop = self._bridge_poll_loop
        thread = self._bridge_poll_thread
        loop_key = id(loop) if loop is not None else None
        self._bridge_poll_loop = None
        self._bridge_poll_thread = None
        self._bridge_poll_thread_stop.set()
        if loop is not None and not loop.is_closed():
            try:
                stop_future = asyncio.run_coroutine_threadsafe(
                    self._cancel_bridge_poll_loop_tasks_before_stop(),
                    loop,
                )
                stop_future.result(timeout=2.0)
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge poll loop graceful stop failed: {}",
                    exc,
                )
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
        if loop_key is not None:
            with self._bridge_poll_task_lock:
                self._poll_bridge_locks.pop(loop_key, None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
            if thread.is_alive():
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge poll loop thread did not stop within timeout",
                )

    def _background_bridge_poll_stale_timeout_seconds(self) -> float:
        if self._cfg is None:
            return _BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS
        interval = max(
            float(self._cfg.active_poll_interval_seconds),
            float(self._cfg.idle_poll_interval_seconds),
            float(self._cfg.ocr_reader_poll_interval_seconds),
            1.0,
        )
        return max(_BACKGROUND_BRIDGE_POLL_MIN_STALE_SECONDS, interval * 12.0)

    def _add_bridge_poll_debug_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        enriched.update(self._bridge_poll_debug_payload())
        runtime = dict(enriched.get("ocr_reader_runtime") or {})
        if runtime:
            pending_count = int(enriched.get("pending_ocr_advance_captures") or 0)
            pending_reason = str(enriched.get("pending_ocr_advance_reason") or "")
            runtime.update(
                {
                    "pending_ocr_advance_capture": pending_count > 0,
                    "pending_manual_foreground_ocr_capture": bool(
                        enriched.get("pending_manual_foreground_ocr_capture")
                    ),
                    "pending_ocr_delay_remaining": float(
                        enriched.get("pending_ocr_delay_remaining") or 0.0
                    ),
                    "pending_ocr_advance_capture_age_seconds": float(
                        enriched.get("pending_ocr_advance_capture_age_seconds") or 0.0
                    ),
                    "pending_ocr_advance_reason": pending_reason,
                }
            )
            enriched["pending_ocr_advance_capture"] = pending_count > 0
            enriched["pending_manual_foreground_ocr_capture"] = bool(
                enriched.get("pending_manual_foreground_ocr_capture")
            )
            enriched["pending_ocr_delay_remaining"] = float(
                enriched.get("pending_ocr_delay_remaining") or 0.0
            )
            enriched["pending_ocr_advance_reason"] = pending_reason
            pending_rollback = dict(self._ocr_capture_profile_pending_rollback or {})
            runtime["capture_profile_auto_apply_enabled"] = bool(
                self._ocr_capture_profile_auto_apply_enabled
            )
            runtime["capture_profile_pending_rollback"] = bool(pending_rollback)
            runtime["capture_profile_rollback_failure_count"] = int(
                pending_rollback.get("failure_count") or 0
            )
            runtime["capture_profile_last_rollback_reason"] = str(
                self._ocr_capture_profile_last_rollback_reason or ""
            )
            runtime["capture_profile_pending_rollback_reason"] = str(
                pending_rollback.get("reason") or ""
            )
            enriched["ocr_reader_runtime"] = runtime
            context_state = str(runtime.get("ocr_context_state") or "")
            poll_running = bool(enriched.get("bridge_poll_running"))
            has_capture_attempt = bool(str(runtime.get("last_capture_attempt_at") or ""))
            if context_state == "capture_pending" and not poll_running and not has_capture_attempt:
                runtime["ocr_context_state"] = "poll_not_running"
                enriched["ocr_reader_runtime"] = runtime
                enriched["ocr_capture_diagnostic_required"] = True
                enriched["ocr_capture_diagnostic"] = (
                    "OCR 轮询未继续执行，尚未完成首次截图；请检查插件 timer、后端重载状态或刷新运行中的插件。"
                )
            enriched["ocr_context_state"] = str(runtime.get("ocr_context_state") or context_state)
        ocr_background_status = build_ocr_background_status(enriched)
        enriched["ocr_background_status"] = ocr_background_status
        enriched["ocr_background_state"] = str(ocr_background_status.get("state") or "")
        enriched["ocr_background_message"] = str(ocr_background_status.get("message") or "")
        enriched["ocr_background_polling"] = bool(
            ocr_background_status.get("background_polling")
        )
        enriched["ocr_foreground_resume_pending"] = bool(
            ocr_background_status.get("foreground_resume_pending")
        )
        enriched["ocr_capture_backend_blocked"] = bool(
            ocr_background_status.get("capture_backend_blocked")
        )
        enriched["primary_diagnosis"] = build_primary_diagnosis(enriched)
        return enriched

    def _start_background_bridge_poll(self) -> bool:
        if self._cfg is None:
            return False
        self._clear_completed_background_bridge_poll()
        with self._bridge_poll_task_lock:
            if self._bridge_poll_task is not None:
                if not self._bridge_poll_task.done():
                    # 此处为 _bridge_poll_task_lock → _state_lock 路径；
                    # 反向路径 (_state_lock → _bridge_poll_task_lock) 在
                    # _request_ocr_after_advance_capture_at:920。asyncio 单线程下安全。
                    with self._state_lock:
                        bridge_poll_started_at = float(self._bridge_poll_started_at or 0.0)
                    inflight_seconds = (
                        max(0.0, time.monotonic() - bridge_poll_started_at)
                        if bridge_poll_started_at > 0.0
                        else 0.0
                    )
                    if inflight_seconds >= self._background_bridge_poll_stale_timeout_seconds():
                        self._record_error(
                            make_error(
                                (
                                    "bridge background poll timed out; canceling stale OCR poll "
                                    f"after {inflight_seconds:.1f}s"
                                ),
                                source="bridge_reader",
                                kind="warning",
                            )
                        )
                        self._bridge_poll_task.cancel()
                        with self._state_lock:
                            self._clear_pending_ocr_advance_captures_locked()
                            self._state.next_poll_at_monotonic = 0.0
                            self._state_dirty = True
                            self._cached_snapshot = None
                    return False
                self._bridge_poll_task = None
            started_at = time.monotonic()
            with self._state_lock:
                self._bridge_poll_started_at = started_at
                self._last_bridge_poll_launch_at = started_at
                self._bridge_poll_launch_count += 1
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not None and not running_loop.is_closed():
                task = running_loop.create_task(self._run_background_bridge_poll())
                self._bridge_poll_task = task
                task.add_done_callback(
                    lambda completed: self._clear_completed_background_bridge_poll(completed)
                )
                return True
            loop = self._ensure_bridge_poll_loop()
            if loop is None:
                return False
            task = asyncio.run_coroutine_threadsafe(self._run_background_bridge_poll(), loop)
            self._bridge_poll_task = task
            task.add_done_callback(
                lambda completed: self._clear_completed_background_bridge_poll(completed)
            )
            return True

    async def _run_background_bridge_poll(self) -> None:
        task_started_at = time.monotonic()
        with self._state_lock:
            self._bridge_poll_started_at = task_started_at
        try:
            while not self._bridge_poll_thread_stop.is_set():
                poll_started_at = time.monotonic()
                with self._state_lock:
                    self._bridge_poll_started_at = poll_started_at
                await self._poll_bridge(force=False)
                poll_finished_at = time.monotonic()
                with self._state_lock:
                    self._bridge_poll_started_at = 0.0
                    self._bridge_poll_finished_at = poll_finished_at
                    self._last_bridge_poll_duration_seconds = max(
                        0.0,
                        poll_finished_at - poll_started_at,
                    )
                if self._bridge_poll_thread_stop.is_set():
                    break
                delay = self._background_bridge_poll_sleep_seconds()
                if delay is None:
                    break
                await asyncio.sleep(delay)
        except Exception as exc:
            with self._state_lock:
                self._state.next_poll_at_monotonic = 0.0
                self._state_dirty = True
                self._cached_snapshot = None
            self._record_error(
                make_error(
                    f"bridge background poll failed: {exc}",
                    source="bridge_reader",
                    kind="error",
                )
            )
        finally:
            finished_at = time.monotonic()
            with self._state_lock:
                self._bridge_poll_started_at = 0.0
                self._bridge_poll_finished_at = finished_at
                if self._last_bridge_poll_duration_seconds <= 0.0:
                    self._last_bridge_poll_duration_seconds = max(
                        0.0,
                        finished_at - task_started_at,
                    )

    def _background_bridge_poll_sleep_seconds(self) -> float | None:
        if self._has_pending_ocr_advance_capture():
            delay = self._pending_ocr_advance_capture_delay_remaining()
            if delay <= 0.0:
                delay = _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS
            return min(delay, _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS)
        if self._cfg is None or not self._cfg.ocr_reader_enabled:
            return None
        if self._cfg.ocr_reader_trigger_mode != OCR_TRIGGER_MODE_INTERVAL:
            return None
        with self._state_lock:
            active_data_source = str(self._state.active_data_source or "")
            ocr_reader_runtime = json_copy(self._state.ocr_reader_runtime)
            next_poll_at = float(self._state.next_poll_at_monotonic or 0.0)
        if active_data_source != DATA_SOURCE_OCR_READER:
            return None
        if str(ocr_reader_runtime.get("status") or "") not in {"starting", "active"}:
            return None
        if next_poll_at <= 0.0:
            return 0.0
        return max(0.0, next_poll_at - time.monotonic())

    def _ocr_fast_loop_should_run(self) -> bool:
        return (
            self._cfg is not None
            and self._ocr_reader_manager is not None
            and bool(getattr(self._cfg, "ocr_reader_fast_loop_enabled", False))
            and bool(getattr(self._cfg, "ocr_reader_enabled", False))
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
        )

    def _start_ocr_fast_loop(self) -> bool:
        # 读-检查-写 _ocr_fast_loop_task 无锁保护。调用者均在主 asyncio 线程，
        # 协作式调度下无竞态。若改为多线程或拆分 await 点，需加锁。
        if not self._ocr_fast_loop_should_run():
            return False
        task = self._ocr_fast_loop_task
        if task is not None and not task.done():
            return True
        try:
            task = asyncio.create_task(self._run_ocr_fast_loop())
        except RuntimeError:
            return False
        self._ocr_fast_loop_task = task
        return True

    async def _cancel_ocr_fast_loop(self) -> None:
        task = self._ocr_fast_loop_task
        if task is None:
            return
        self._ocr_fast_loop_task = None
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame OCR fast loop cancellation failed: {}",
                exc,
            )

    async def _acquire_ocr_tick_lock(self, *, wait: bool) -> bool:
        if self._ocr_reader_tick_lock.acquire(blocking=False):
            return True
        if not wait:
            return False
        while not self._ocr_reader_tick_lock.acquire(blocking=False):
            await asyncio.sleep(0.05)
        return True

    def _ocr_fast_loop_sleep_seconds(self, *, elapsed_seconds: float) -> float:
        if self._cfg is None:
            return 1.0
        interval = max(0.1, float(self._cfg.ocr_reader_poll_interval_seconds or 0.5))
        return max(0.0, interval - max(0.0, elapsed_seconds))

    def _ocr_fast_loop_capture_allowed_snapshot(self, state_snapshot: dict[str, Any]) -> bool:
        if self._cfg is None:
            return False
        reader_mode = _normalize_reader_mode(getattr(self._cfg, "reader_mode", READER_MODE_AUTO))
        if reader_mode == READER_MODE_OCR:
            return True
        active_data_source = str(state_snapshot.get("active_data_source") or "")
        runtime = state_snapshot.get("ocr_reader_runtime")
        runtime_obj = runtime if isinstance(runtime, dict) else {}
        runtime_status = str(runtime_obj.get("status") or "")
        if active_data_source == DATA_SOURCE_OCR_READER:
            return True
        return runtime_status in {"starting", "active"}

    async def _run_ocr_fast_loop_once(self) -> bool:
        if not self._ocr_fast_loop_should_run() or self._ocr_reader_manager is None:
            return False
        if not await self._acquire_ocr_tick_lock(wait=False):
            return False
        started_at = time.monotonic()
        with self._state_lock:
            self._ocr_fast_loop_started_at = started_at
        should_start_bridge_poll = False
        try:
            state_snapshot = self._snapshot_state(fresh=True)
            if not self._ocr_fast_loop_capture_allowed_snapshot(state_snapshot):
                return False
            self._ocr_reader_manager.update_config(self._cfg)
            update_advance_speed = getattr(
                self._ocr_reader_manager,
                "update_advance_speed",
                None,
            )
            if callable(update_advance_speed):
                update_advance_speed(str(state_snapshot.get("advance_speed") or ADVANCE_SPEED_MEDIUM))
            memory_reader_runtime = json_copy(
                state_snapshot.get("memory_reader_runtime") or {}
            )
            bridge_sdk_available = (
                str(state_snapshot.get("active_data_source") or "") == DATA_SOURCE_BRIDGE_SDK
            )
            tick = await self._ocr_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_available,
                memory_reader_runtime=memory_reader_runtime,
            )
            self._record_ocr_poll_duration(tick.runtime)
            ocr_reader_runtime = await self._update_ocr_capture_profile_rollback_state(
                tick.runtime
            )
            ocr_reader_runtime = await self._maybe_auto_apply_recommended_ocr_capture_profile(
                ocr_reader_runtime
            )
            resolved_window_target = self._ocr_reader_manager.current_window_target()
            with self._state_lock:
                self._state.ocr_reader_runtime = (
                    _merge_ocr_runtime_preserving_bridge_diagnostics(
                        ocr_reader_runtime,
                        self._state.ocr_reader_runtime,
                    )
                )
                if resolved_window_target != json_copy(self._state.ocr_window_target):
                    self._state.ocr_window_target = json_copy(resolved_window_target)
                    try:
                        self._persist.persist_ocr_window_target(resolved_window_target)
                    except Exception as exc:
                        self._state.last_error = make_error(
                            f"persist OCR window target failed: {exc}",
                            source="ocr_reader",
                            kind="warning",
                        )
                if tick.should_rescan or tick.stable_event_emitted:
                    self._state.next_poll_at_monotonic = 0.0
                    should_start_bridge_poll = True
                self._state_dirty = True
                self._cached_snapshot = None
            self._fast_loop_consecutive_errors = 0
            return True
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader fast loop failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            self._fast_loop_consecutive_errors += 1
            if self._fast_loop_consecutive_errors >= 5:
                self.logger.warning(
                    f"ocr fast loop paused after {self._fast_loop_consecutive_errors} consecutive errors"
                )
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = False
                self._fast_loop_auto_enabled = False
            return False
        finally:
            finished_at = time.monotonic()
            with self._state_lock:
                self._ocr_fast_loop_started_at = 0.0
                self._ocr_fast_loop_last_run_at = finished_at
                self._ocr_fast_loop_last_duration_seconds = max(0.0, finished_at - started_at)
                self._ocr_fast_loop_iteration_count += 1
            self._ocr_reader_tick_lock.release()
            if should_start_bridge_poll:
                self._start_background_bridge_poll()

    async def _run_ocr_fast_loop(self) -> None:
        try:
            while self._ocr_fast_loop_should_run():
                started_at = time.monotonic()
                await self._run_ocr_fast_loop_once()
                if not self._ocr_fast_loop_should_run():
                    break
                await asyncio.sleep(
                    self._ocr_fast_loop_sleep_seconds(
                        elapsed_seconds=time.monotonic() - started_at
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader fast loop stopped: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )

    async def _cancel_background_bridge_poll(self) -> None:
        with self._bridge_poll_task_lock:
            task = self._bridge_poll_task
            if task is None:
                return
            self._bridge_poll_task = None
        if not task.done():
            try:
                if isinstance(task, Future):
                    wrapped = asyncio.wrap_future(task)
                    wrapped.cancel()
                    await wrapped
                else:
                    task.cancel()
                    await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame bridge background poll cancellation failed: {}",
                    exc,
                )
        self._stop_bridge_poll_loop()

    def _ocr_foreground_advance_monitor_should_run(self) -> bool:
        return (
            self._cfg is not None
            and self._ocr_reader_manager is not None
            and bool(self._cfg.ocr_reader_enabled)
            and getattr(self._cfg, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )

    def _ocr_foreground_advance_monitor_active(self) -> bool:
        task = self._ocr_foreground_advance_monitor_task
        return (
            self._ocr_foreground_advance_monitor_should_run()
            and task is not None
            and not task.done()
        )

    async def _run_ocr_foreground_advance_monitor(self) -> None:
        try:
            while self._ocr_foreground_advance_monitor_should_run():
                self._refresh_ocr_foreground_state()
                self._trigger_ocr_for_manual_foreground_advance()
                await asyncio.sleep(_OCR_FOREGROUND_ADVANCE_MONITOR_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground advance async monitor failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )

    async def _ensure_ocr_foreground_advance_monitor(self) -> bool:
        task = self._ocr_foreground_advance_monitor_task
        if task is not None and task.done():
            self._ocr_foreground_advance_monitor_task = None
        if not self._ocr_foreground_advance_monitor_should_run():
            await self._cancel_ocr_foreground_advance_monitor()
            return False
        task = self._ocr_foreground_advance_monitor_task
        if task is not None and not task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._ocr_foreground_advance_monitor_task = loop.create_task(
            self._run_ocr_foreground_advance_monitor()
        )
        return True

    async def _cancel_ocr_foreground_advance_monitor(self) -> None:
        task = self._ocr_foreground_advance_monitor_task
        self._ocr_foreground_advance_monitor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame OCR foreground advance monitor cancellation failed: {}",
                exc,
            )

    def _set_runtime_from_store(self, restored: dict[str, Any], warnings: list[str]) -> None:
        with self._state_lock:
            self._state = build_initial_state(
                mode=str(restored.get(STORE_MODE, MODE_COMPANION)),
                push_notifications=bool(restored.get(STORE_PUSH_NOTIFICATIONS, True)),
                advance_speed=str(restored.get(STORE_ADVANCE_SPEED, ADVANCE_SPEED_MEDIUM)),
            )
            self._state.bound_game_id = str(restored.get(STORE_BOUND_GAME_ID, ""))
            self._state.active_session_id = str(restored.get(STORE_SESSION_ID, ""))
            self._state.events_byte_offset = int(restored.get(STORE_EVENTS_BYTE_OFFSET, 0))
            self._state.events_file_size = int(restored.get(STORE_EVENTS_FILE_SIZE, 0))
            self._state.last_seq = int(restored.get(STORE_LAST_SEQ, 0))
            self._state.dedupe_window = json_copy(restored.get(STORE_DEDUPE_WINDOW, []))
            self._state.last_error = json_copy(restored.get(STORE_LAST_ERROR, {}))
            self._state.active_data_source = DATA_SOURCE_NONE
            self._state.memory_reader_runtime = {}
            self._state.memory_reader_target = json_copy(
                restored.get(STORE_MEMORY_READER_TARGET, {})
            )
            self._state.ocr_reader_runtime = {}
            self._state.ocr_capture_profiles = json_copy(
                restored.get(STORE_OCR_CAPTURE_PROFILES, {})
            )
            self._state.ocr_window_target = json_copy(restored.get(STORE_OCR_WINDOW_TARGET, {}))
            if warnings and not self._state.last_error:
                self._state.last_error = make_error(
                    "; ".join(warnings),
                    source="store",
                    kind="warning",
                )
            self._state_dirty = True
            self._cached_snapshot = None

        self._apply_config_overrides_from_store()

    def _apply_config_overrides_from_store(self) -> None:
        if self._cfg is None:
            return
        overrides = self._persist.load_config_overrides()

        value = overrides.get(STORE_READER_MODE)
        if value is not None and value in READER_MODES:
            self._cfg.reader.reader_mode = value

        value = overrides.get(STORE_OCR_BACKEND_SELECTION)
        if value is not None and value in _OCR_BACKEND_SELECTIONS:
            self._cfg.ocr_reader.ocr_reader_backend_selection = value

        value = overrides.get(STORE_OCR_CAPTURE_BACKEND)
        if value is not None and value in _OCR_CAPTURE_BACKEND_SELECTIONS:
            self._cfg.ocr_reader.ocr_reader_capture_backend = value

        value = overrides.get(STORE_OCR_POLL_INTERVAL_SECONDS)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_poll_interval_seconds = value

        value = overrides.get(STORE_OCR_TRIGGER_MODE)
        if value is not None and value in OCR_TRIGGER_MODES:
            self._cfg.ocr_reader.ocr_reader_trigger_mode = value

        value = overrides.get(STORE_OCR_FAST_LOOP_ENABLED)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_fast_loop_enabled = bool(value)

        value = overrides.get(STORE_LLM_VISION_ENABLED)
        if value is not None:
            self._cfg.llm.llm_vision_enabled = bool(value)

        value = overrides.get(STORE_LLM_VISION_MAX_IMAGE_PX)
        if value is not None:
            self._cfg.llm.llm_vision_max_image_px = value

        value = overrides.get(STORE_OCR_SCREEN_TEMPLATES)
        if value is not None:
            self._cfg.ocr_reader.ocr_reader_screen_templates = json_copy(value)

    def _current_status_payload(self) -> dict[str, Any]:
        if self._cfg is None:
            return self._add_bridge_poll_debug_payload({
                "connection_state": "error",
                "mode": MODE_COMPANION,
                "push_notifications": True,
                "bound_game_id": "",
                "available_game_ids": [],
                "active_session_id": "",
                "active_data_source": DATA_SOURCE_NONE,
                "stream_reset_pending": False,
                "last_seq": 0,
                "last_error": {},
                "summary": "config_not_loaded",
                "phase": "phase_1",
                "memory_reader_enabled": False,
                "memory_reader_runtime": {},
                "memory_reader_target": {},
                "ocr_reader_enabled": False,
                "ocr_reader_runtime": {},
                "ocr_capture_profiles": {},
                "dxcam": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "package_name": "dxcam",
                    "target_dir": "",
                    "detail": "config_not_loaded",
                    "runtime_error": "",
                },
                "rapidocr_enabled": False,
                "rapidocr": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "target_dir": "",
                    "runtime_dir": "",
                    "site_packages_dir": "",
                    "model_cache_dir": "",
                    "selected_model": "",
                    "engine_type": "",
                    "lang_type": "",
                    "model_type": "",
                    "ocr_version": "",
                    "detail": "config_not_loaded",
                    "runtime_error": "",
                },
                "tesseract": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "target_dir": "",
                    "expected_executable_path": "",
                    "tessdata_dir": "",
                    "required_languages": [],
                    "missing_languages": [],
                    "detail": "config_not_loaded",
                },
                "textractor": {
                    "install_supported": False,
                    "installed": False,
                    "can_install": False,
                    "detected_path": "",
                    "target_dir": "",
                    "expected_executable_path": "",
                    "detail": "config_not_loaded",
                },
            })
        state_snapshot = self._snapshot_state()
        state = SimpleNamespace(**state_snapshot)
        return self._add_bridge_poll_debug_payload(
            build_status_payload(state, config=self._cfg, state_is_snapshot=True)
        )

    async def _build_status_payload_async(self) -> dict[str, Any]:
        if self._cfg is None:
            return self._current_status_payload()
        self._refresh_ocr_foreground_state()
        state_snapshot = self._snapshot_state()
        config = self._cfg
        state = SimpleNamespace(**state_snapshot)
        payload = await asyncio.to_thread(
            build_status_payload,
            state,
            config=config,
            state_is_snapshot=True,
        )
        payload = self._add_bridge_poll_debug_payload(payload)
        if self._game_agent is not None:
            try:
                agent_payload = await self._game_agent.peek_status(state_snapshot)
                payload["agent"] = json_copy(agent_payload)
                payload["agent_status"] = str(agent_payload.get("status") or "")
                payload["agent_user_status"] = str(agent_payload.get("agent_user_status") or "")
                payload["agent_pause_kind"] = str(agent_payload.get("agent_pause_kind") or "")
                payload["agent_pause_message"] = str(
                    agent_payload.get("agent_pause_message") or ""
                )
                payload["agent_can_resume_by_button"] = bool(
                    agent_payload.get("agent_can_resume_by_button")
                )
                payload["agent_can_resume_by_focus"] = bool(
                    agent_payload.get("agent_can_resume_by_focus")
                )
                payload["agent_activity"] = str(agent_payload.get("activity") or "")
                payload["agent_reason"] = str(agent_payload.get("reason") or "")
                payload["agent_error"] = str(agent_payload.get("error") or "")
                payload["agent_inbound_queue_size"] = int(
                    agent_payload.get("inbound_queue_size") or 0
                )
                payload["agent_outbound_queue_size"] = int(
                    agent_payload.get("outbound_queue_size") or 0
                )
                payload["agent_last_interruption"] = json_copy(
                    agent_payload.get("last_interruption") or {}
                )
                payload["agent_last_outbound_message"] = json_copy(
                    agent_payload.get("last_outbound_message") or {}
                )
                agent_debug = agent_payload.get("debug")
                agent_diagnostic = (
                    str(
                        (agent_debug or {}).get("target_window_diagnostic")
                        or (agent_debug or {}).get("ocr_capture_diagnostic")
                        or ""
                    )
                    if isinstance(agent_debug, dict)
                    else ""
                )
                payload["agent_diagnostic"] = agent_diagnostic
                payload["agent_diagnostic_required"] = bool(
                    agent_diagnostic
                    or payload["agent_reason"]
                    in {
                        "ocr_context_unavailable",
                        "input_advance_unconfirmed",
                        "target_window_not_foreground",
                        "hard_error",
                    }
                )
            except Exception as exc:
                payload["agent_status"] = "unknown"
                payload["agent_user_status"] = "error"
                payload["agent_pause_kind"] = "none"
                payload["agent_pause_message"] = ""
                payload["agent_can_resume_by_button"] = False
                payload["agent_can_resume_by_focus"] = False
                payload["agent_activity"] = ""
                payload["agent_reason"] = "agent_status_unavailable"
                payload["agent_error"] = str(exc)
                payload["agent_diagnostic"] = f"agent_status_unavailable: {exc}"
                payload["agent_diagnostic_required"] = True
        payload["primary_diagnosis"] = build_primary_diagnosis(payload)
        return payload

    def _resolve_current_run_id(self) -> str:
        return str(getattr(self.ctx, "run_id", "") or "").strip()

    def _resolve_install_progress_callback(self, current_run_id: str):
        async def _progress_update(event: dict[str, Any]) -> None:
            if not current_run_id:
                return
            await self.run_update(
                run_id=current_run_id,
                status="running",
                progress=float(event.get("progress") or 0.0),
                stage=str(event.get("phase") or ""),
                message=str(event.get("message") or ""),
                metrics={
                    "phase": str(event.get("phase") or ""),
                    "downloaded_bytes": int(event.get("downloaded_bytes") or 0),
                    "total_bytes": int(event.get("total_bytes") or 0),
                    "resume_from": int(event.get("resume_from") or 0),
                    "asset_name": str(event.get("asset_name") or ""),
                    "release_name": str(event.get("release_name") or ""),
                },
            )

        return _progress_update

    async def _load_config(self) -> None:
        raw = await self.config.dump(timeout=5.0)
        raw_config = raw if isinstance(raw, dict) else {}
        self._cfg = build_config(raw_config)

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            await self._load_config()
        except Exception as exc:
            self._record_error(
                make_error(f"load config failed: {exc}", source="config", kind="error")
            )
            return Err(SdkError(f"failed to load galgame_plugin config: {exc}"))

        try:
            restored, warnings = self._persist.load()
            self._set_runtime_from_store(restored, warnings)
        except Exception as exc:
            self._record_error(
                make_error(f"restore store failed: {exc}", source="store", kind="error")
            )
            return Err(SdkError(f"failed to restore galgame_plugin store: {exc}"))

        self._host_agent_adapter = HostAgentAdapter(self.logger)
        self._llm_gateway = LLMGateway(self, self.logger, self._cfg)
        self._game_agent = GameLLMAgent(
            plugin=self,
            logger=self.logger,
            llm_gateway=self._llm_gateway,
            host_adapter=self._host_agent_adapter,
            config=self._cfg,
        )
        self._memory_reader_manager = MemoryReaderManager(
            logger=self.logger,
            config=self._cfg,
        )
        self._memory_reader_manager.update_process_target(self._state.memory_reader_target)
        self._ocr_reader_manager = OcrReaderManager(
            logger=self.logger,
            config=self._cfg,
        )
        self._ocr_reader_manager.update_capture_profiles(self._state.ocr_capture_profiles)
        self._ocr_reader_manager.update_window_target(self._state.ocr_window_target)

        self.register_static_ui("static")
        self.set_list_actions(
            [
                {
                    "id": "open_ui",
                    "kind": "ui",
                    "target": f"/plugin/{self.plugin_id}/ui/",
                    "open_in": "new_tab",
                }
            ]
        )

        if self._cfg.bridge.auto_open_ui:
            port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "48916")
            url = f"http://127.0.0.1:{port}/plugin/{self.plugin_id}/ui/"
            try:
                await asyncio.to_thread(_open_url_in_browser, url)
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame auto-open UI failed: {}",
                    exc,
                )

        await self._poll_bridge(force=True)
        self._start_ocr_fast_loop()
        await self._ensure_ocr_foreground_advance_monitor()
        return Ok({"status": "ready", "result": await self._build_status_payload_async()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._bridge_tick_shutdown_requested = True
        await self._cancel_ocr_fast_loop()
        await self._cancel_ocr_foreground_advance_monitor()
        await self._cancel_background_bridge_poll()
        if self._memory_reader_manager is not None:
            try:
                await self._memory_reader_manager.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame memory reader shutdown failed: {}",
                    exc,
                )
        if self._ocr_reader_manager is not None:
            try:
                await self._ocr_reader_manager.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame OCR reader shutdown failed: {}",
                    exc,
                )
        if self._game_agent is not None:
            try:
                await self._game_agent.drain_summary_tasks(timeout=5.0)
                await self._game_agent.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame agent shutdown failed: {}",
                    exc,
                )
        if self._llm_gateway is not None:
            try:
                await self._llm_gateway.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame LLM gateway shutdown failed: {}",
                    exc,
                )
        if self._host_agent_adapter is not None:
            try:
                await self._host_agent_adapter.shutdown()
            except Exception as exc:
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame host agent adapter shutdown failed: {}",
                    exc,
                )
        try:
            await self.store.close()
        except Exception as exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame store shutdown failed: {}",
                exc,
            )
        return Ok({"status": "stopped"})

    @timer_interval(id="bridge_tick", seconds=1, auto_start=True)
    async def bridge_tick(self, **_):
        if self._bridge_tick_shutdown_requested:
            return Ok({"status": "stopped"})
        tick_started_at = time.monotonic()
        with self._state_lock:
            self._bridge_tick_last_started_at = tick_started_at
            self._bridge_tick_launch_count += 1
            self._bridge_tick_last_error = ""
        try:
            self._clear_completed_background_bridge_poll()
            self._refresh_ocr_foreground_state()
            if not self._ocr_foreground_advance_monitor_active():
                self._trigger_ocr_for_manual_foreground_advance()
            if self._game_agent is not None:
                with self._state_lock:
                    self._last_agent_tick_at = time.monotonic()
                try:
                    await self._game_agent.tick(self._snapshot_state())
                    await self._game_agent.drain_summary_tasks(
                        timeout=self._bridge_tick_summary_drain_timeout_seconds()
                    )
                except Exception as exc:
                    with self._state_lock:
                        self._bridge_tick_last_error = f"game_agent_tick_failed: {exc}"
                    self._record_error(
                        make_error(
                            f"game agent tick failed: {exc}",
                            source="game_agent",
                            kind="error",
                        )
                    )
            self._start_background_bridge_poll()
            self._start_ocr_fast_loop()
            await asyncio.sleep(0)
            return Ok({"status": "tick"})
        except Exception as exc:
            with self._state_lock:
                self._bridge_tick_last_error = str(exc)
            raise
        finally:
            tick_finished_at = time.monotonic()
            with self._state_lock:
                self._bridge_tick_last_finished_at = tick_finished_at
                self._bridge_tick_last_duration_seconds = max(
                    0.0,
                    tick_finished_at - tick_started_at,
                )

    def _bridge_tick_summary_drain_timeout_seconds(self) -> float:
        if self._cfg is None:
            return 30.0
        try:
            configured = float(getattr(self._cfg, "llm_call_timeout_seconds", 30.0) or 30.0)
        except (TypeError, ValueError):
            configured = 30.0
        return max(1.0, configured + 2.0)

    def _refresh_ocr_foreground_state(self, *, force: bool = False) -> None:
        if self._cfg is None or not self._cfg.ocr_reader_enabled:
            return
        if getattr(self._cfg, "reader_mode", READER_MODE_AUTO) == READER_MODE_MEMORY:
            return
        if self._ocr_reader_manager is None:
            return
        refresh = getattr(self._ocr_reader_manager, "refresh_foreground_state", None)
        if not callable(refresh):
            return
        now = time.monotonic()
        # TTL gate: prevent bridge_tick, advance monitor, and build_status_payload
        # paths from refreshing foreground state repeatedly in a short window.
        if (
            not force
            and self._last_ocr_foreground_refresh_at > 0.0
            and now - self._last_ocr_foreground_refresh_at < _OCR_FOREGROUND_REFRESH_TTL_SECONDS
        ):
            return
        try:
            runtime = refresh()
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground refresh failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            return
        self._last_ocr_foreground_refresh_at = now
        with self._state_lock:
            self._state.ocr_reader_runtime = (
                _merge_ocr_runtime_preserving_bridge_diagnostics(
                    runtime,
                    self._state.ocr_reader_runtime,
                )
            )
            self._state_dirty = True
            self._cached_snapshot = None

    def _trigger_ocr_for_manual_foreground_advance(self) -> None:
        if self._cfg is None or self._ocr_reader_manager is None:
            return
        if not self._cfg.ocr_reader_enabled:
            return
        if getattr(self._cfg, "reader_mode", READER_MODE_AUTO) == READER_MODE_MEMORY:
            return
        if self._cfg.ocr_reader_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            return
        consume = getattr(self._ocr_reader_manager, "consume_foreground_advance_inputs", None)
        structured_result = True
        if not callable(consume):
            consume = getattr(self._ocr_reader_manager, "consume_foreground_advance_input", None)
            structured_result = False
        if not callable(consume):
            return
        try:
            consume_result = consume()
            should_capture = (
                bool(getattr(consume_result, "triggered", False))
                if structured_result
                else bool(consume_result)
            )
        except Exception as exc:
            self._record_error(
                make_error(
                    f"ocr_reader foreground advance monitor failed: {exc}",
                    source="ocr_reader",
                    kind="warning",
                )
            )
            return
        runtime_getter = getattr(self._ocr_reader_manager, "runtime", None)
        if callable(runtime_getter):
            try:
                runtime = runtime_getter()
            except Exception as exc:
                self._record_error(
                    make_error(
                        f"ocr_reader foreground advance runtime sync failed: {exc}",
                        source="ocr_reader",
                        kind="warning",
                    )
                )
            else:
                if isinstance(runtime, dict):
                    with self._state_lock:
                        self._state.ocr_reader_runtime = (
                            _merge_ocr_runtime_preserving_bridge_diagnostics(
                                runtime,
                                self._state.ocr_reader_runtime,
                            )
                        )
                        self._state_dirty = True
                        self._cached_snapshot = None
        if should_capture:
            event_age_seconds = (
                getattr(consume_result, "last_event_age_seconds", 0.0)
                if structured_result
                else 0.0
            )
            coalesced_count = (
                getattr(consume_result, "coalesced_count", 0)
                if structured_result
                else 0
            )
            self._request_ocr_after_advance_capture_for_event_age(
                event_age_seconds=event_age_seconds,
                reason="manual_foreground_advance",
                coalesced_count=int(coalesced_count or 0),
            )

    def _poll_bridge_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        loop_key = id(loop)
        with self._bridge_poll_task_lock:
            lock = self._poll_bridge_locks.get(loop_key)
            if lock is None:
                lock = asyncio.Lock()
                self._poll_bridge_locks[loop_key] = lock
            return lock

    async def _poll_bridge(self, *, force: bool) -> None:
        if self._cfg is None:
            return

        poll_started_at = time.monotonic()
        async with self._poll_bridge_async_lock():
            while not self._poll_bridge_thread_lock.acquire(blocking=False):
                await asyncio.sleep(0.05)
            try:
                await self._poll_bridge_locked(force=force)
            finally:
                self._poll_bridge_thread_lock.release()
                poll_finished_at = time.monotonic()
                duration = max(0.0, poll_finished_at - poll_started_at)
                with self._state_lock:
                    self._last_bridge_poll_duration_seconds = duration
                self._record_bridge_poll_duration(duration)

    async def _scan_candidates(self) -> tuple[list[str], dict[str, Any], list[str]]:
        if self._cfg is None:
            return [], {}, []
        return await asyncio.to_thread(scan_session_candidates, self._cfg.bridge_root)

    def _commit_bridge_scan_failure(
        self,
        local: dict[str, Any],
        *,
        now_monotonic: float,
        exc: Exception,
    ) -> None:
        if self._cfg is None:
            return
        local["plugin_error"] = f"scan bridge root failed: {exc}"
        local["available_game_ids"] = []
        local["current_connection_state"] = STATE_ERROR
        local["last_error"] = make_error(
            local["plugin_error"], source="bridge_scan", kind="error"
        )
        interval = next_poll_interval_for_state(
            local["current_connection_state"],
            stream_reset_pending=bool(local["stream_reset_pending"]),
            config=self._cfg,
        )
        local["next_poll_at_monotonic"] = now_monotonic + interval
        self._commit_state(local)
        try:
            self._config_service.persist_runtime_state(local)
        except Exception as persist_exc:
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame persist runtime state after bridge scan failure failed: {}",
                persist_exc,
            )

    async def _tick_memory_reader_for_poll(
        self,
        *,
        memory_reader_allowed: bool,
        bridge_sdk_candidate_available: bool,
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any], dict[str, Any], list[str]]:
        warnings: list[str] = []
        if (
            self._cfg is None
            or self._memory_reader_manager is None
            or not memory_reader_allowed
        ):
            return raw_available_game_ids, raw_candidates, memory_reader_runtime, warnings
        self._memory_reader_manager.update_config(self._cfg)
        try:
            memory_reader_tick = await self._memory_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_candidate_available,
            )
            warnings.extend(memory_reader_tick.warnings)
            memory_reader_runtime = memory_reader_tick.runtime
            if memory_reader_tick.should_rescan:
                (
                    raw_available_game_ids,
                    raw_candidates,
                    rescan_warnings,
                ) = await self._scan_candidates()
                warnings.extend(rescan_warnings)
        except Exception as exc:
            warnings.append(f"memory_reader tick failed: {exc}")
        return raw_available_game_ids, raw_candidates, memory_reader_runtime, warnings

    async def _refresh_ocr_foreground_for_poll(
        self,
        *,
        ocr_reader_runtime: dict[str, Any],
        ocr_reader_allowed: bool,
        ocr_trigger_mode: str,
        pending_ocr_advance_capture: bool,
        pending_ocr_delay_remaining: float,
    ) -> tuple[dict[str, Any], bool, float, list[str]]:
        warnings: list[str] = []
        foreground_refresh_skipped_reason = ""
        if self._ocr_reader_manager is None:
            foreground_refresh_skipped_reason = "ocr_reader_manager_missing"
        elif not ocr_reader_allowed:
            foreground_refresh_skipped_reason = "ocr_reader_not_allowed"
        elif ocr_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            foreground_refresh_skipped_reason = "trigger_mode_not_after_advance"
        if foreground_refresh_skipped_reason:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": False,
                    "foreground_refresh_skipped_reason": foreground_refresh_skipped_reason,
                }
            )
            return (
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                pending_ocr_delay_remaining,
                warnings,
            )

        was_foreground = bool(ocr_reader_runtime.get("target_is_foreground"))
        refresh_foreground_state = getattr(
            self._ocr_reader_manager,
            "refresh_foreground_state",
            None,
        )
        if not callable(refresh_foreground_state):
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": False,
                    "foreground_refresh_skipped_reason": "refresh_method_missing",
                }
            )
            return (
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                pending_ocr_delay_remaining,
                warnings,
            )

        try:
            refreshed_runtime = await asyncio.to_thread(refresh_foreground_state)
            if isinstance(refreshed_runtime, dict):
                ocr_reader_runtime = json_copy(refreshed_runtime)
                ocr_reader_runtime.update(
                    {
                        "foreground_refresh_attempted": True,
                        "foreground_refresh_skipped_reason": "",
                    }
                )
                if (
                    not was_foreground
                    and bool(ocr_reader_runtime.get("target_is_foreground"))
                ):
                    if not self._has_pending_ocr_advance_capture():
                        with self._state_lock:
                            self._pending_ocr_advance_captures = min(
                                self._pending_ocr_advance_captures + 1,
                                8,
                            )
                            self._last_ocr_advance_capture_requested_at = time.monotonic()
                            self._last_ocr_advance_capture_reason = "foreground_target_activated"
                            self._state.next_poll_at_monotonic = 0.0
                    pending_ocr_advance_capture = True
                    pending_ocr_delay_remaining = 0.0
        except Exception as exc:
            warnings.append(f"ocr_reader foreground refresh failed: {exc}")
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            ocr_reader_runtime.update(
                {
                    "foreground_refresh_attempted": True,
                    "foreground_refresh_skipped_reason": "refresh_failed",
                }
            )
        return (
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            pending_ocr_delay_remaining,
            warnings,
        )

    async def _tick_ocr_reader_for_poll(
        self,
        *,
        local: dict[str, Any],
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
        ocr_reader_runtime: dict[str, Any],
        bridge_sdk_candidate_available: bool,
        ocr_tick_allowed: bool,
        pending_manual_foreground_ocr_capture: bool,
        pending_ocr_advance_capture: bool,
        force: bool,
    ) -> tuple[list[str], dict[str, Any], dict[str, Any], bool, bool, list[str]]:
        warnings: list[str] = []
        ocr_reader_stable_event_emitted = False
        tick_execution_diagnostics: dict[str, Any] = {
            "ocr_tick_entered": False,
            "ocr_tick_lock_acquired": False,
            "ocr_fast_loop_delegated": False,
            "ocr_tick_skipped_reason": "",
        }
        if self._cfg is None:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "plugin_config_missing"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if self._ocr_reader_manager is None:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "ocr_reader_manager_missing"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if not ocr_tick_allowed:
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "tick_gate_closed"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if self._ocr_fast_loop_should_run() and not force:
            self._start_ocr_fast_loop()
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics.update(
                {
                    "ocr_fast_loop_delegated": True,
                    "ocr_tick_skipped_reason": "ocr_fast_loop_started",
                }
            )
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )
        if not await self._acquire_ocr_tick_lock(wait=force):
            warnings.append("ocr_reader tick skipped: previous OCR tick is still running")
            ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
            tick_execution_diagnostics["ocr_tick_skipped_reason"] = "ocr_tick_lock_busy"
            ocr_reader_runtime.update(tick_execution_diagnostics)
            return (
                raw_available_game_ids,
                raw_candidates,
                ocr_reader_runtime,
                pending_ocr_advance_capture,
                ocr_reader_stable_event_emitted,
                warnings,
            )

        ocr_reader_tick = None
        tick_execution_diagnostics.update(
            {
                "ocr_tick_entered": True,
                "ocr_tick_lock_acquired": True,
            }
        )
        try:
            self._ocr_reader_manager.update_config(self._cfg)
            update_advance_speed = getattr(
                self._ocr_reader_manager,
                "update_advance_speed",
                None,
            )
            if callable(update_advance_speed):
                update_advance_speed(str(local.get("advance_speed") or ADVANCE_SPEED_MEDIUM))
            ocr_memory_reader_runtime = (
                {}
                if pending_manual_foreground_ocr_capture
                else memory_reader_runtime
            )
            ocr_reader_tick = await self._ocr_reader_manager.tick(
                bridge_sdk_available=bridge_sdk_candidate_available,
                memory_reader_runtime=ocr_memory_reader_runtime,
            )
            self._record_ocr_poll_duration(ocr_reader_tick.runtime)
            warnings.extend(ocr_reader_tick.warnings)
            ocr_reader_runtime = ocr_reader_tick.runtime
            ocr_reader_runtime = await self._update_ocr_capture_profile_rollback_state(
                ocr_reader_runtime
            )
            ocr_reader_runtime = await self._maybe_auto_apply_recommended_ocr_capture_profile(
                ocr_reader_runtime
            )
            if ocr_reader_tick.should_rescan:
                (
                    raw_available_game_ids,
                    raw_candidates,
                    rescan_warnings,
                ) = await self._scan_candidates()
                warnings.extend(rescan_warnings)
            resolved_window_target = self._ocr_reader_manager.current_window_target()
            if resolved_window_target != json_copy(local.get("ocr_window_target") or {}):
                local["ocr_window_target"] = json_copy(resolved_window_target)
                try:
                    self._persist.persist_ocr_window_target(resolved_window_target)
                except Exception as exc:
                    warnings.append(f"persist OCR window target failed: {exc}")
        except Exception as exc:
            warnings.append(f"ocr_reader tick failed: {exc}")
        finally:
            pending_capture_settled = bool(
                ocr_reader_tick is not None
                and getattr(ocr_reader_tick, "stable_event_emitted", False)
            )
            ocr_reader_stable_event_emitted = pending_capture_settled
            ocr_reader_capture_failed = bool(
                ocr_reader_tick is not None
                and isinstance(getattr(ocr_reader_tick, "runtime", None), dict)
                and str(ocr_reader_tick.runtime.get("detail") or "") == "capture_failed"
            )
            pending_capture_expired = (
                self._pending_ocr_advance_capture_age()
                >= _OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS
            )
            if pending_ocr_advance_capture and pending_capture_expired:
                self._clear_pending_ocr_advance_captures()
                pending_ocr_advance_capture = False
            elif (
                pending_ocr_advance_capture
                and not ocr_reader_capture_failed
                and (force or pending_capture_settled)
            ):
                self._consume_ocr_advance_capture()
                pending_ocr_advance_capture = self._has_pending_ocr_advance_capture()
            self._ocr_reader_tick_lock.release()
        ocr_reader_runtime = json_copy(ocr_reader_runtime or {})
        ocr_reader_runtime.update(tick_execution_diagnostics)
        return (
            raw_available_game_ids,
            raw_candidates,
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            ocr_reader_stable_event_emitted,
            warnings,
        )

    def _filter_candidates_for_reader_mode(
        self,
        *,
        raw_available_game_ids: list[str],
        raw_candidates: dict[str, Any],
        memory_reader_runtime: dict[str, Any],
        ocr_reader_runtime: dict[str, Any],
        reader_mode: str,
    ) -> tuple[list[str], dict[str, Any]]:
        available_game_ids, candidates = filter_memory_reader_candidates(
            raw_available_game_ids,
            raw_candidates,
            runtime=memory_reader_runtime,
        )
        available_game_ids, candidates = filter_ocr_reader_candidates(
            available_game_ids,
            candidates,
            runtime=ocr_reader_runtime,
        )
        if reader_mode == READER_MODE_MEMORY:
            candidates = {
                game_id: candidate
                for game_id, candidate in candidates.items()
                if candidate.data_source != DATA_SOURCE_OCR_READER
            }
            available_game_ids = [game_id for game_id in available_game_ids if game_id in candidates]
        elif reader_mode == READER_MODE_OCR:
            candidates = {
                game_id: candidate
                for game_id, candidate in candidates.items()
                if candidate.data_source != DATA_SOURCE_MEMORY_READER
            }
            available_game_ids = [game_id for game_id in available_game_ids if game_id in candidates]
        return available_game_ids, candidates

    def _finalize_bridge_poll_state(
        self,
        local: dict[str, Any],
        *,
        warnings: list[str],
        now_monotonic: float,
        ocr_trigger_mode: str,
        ocr_reader_runtime: dict[str, Any],
        after_advance_screen_refresh_needed: bool,
        companion_after_advance_ocr_refresh_needed: bool,
    ) -> None:
        if self._cfg is None:
            return
        if warnings:
            local["last_error"] = make_error(
                "; ".join(warnings[:3]),
                source="bridge_reader",
                kind="warning",
            )
        elif (
            isinstance(local.get("last_error"), dict)
            and str(local["last_error"].get("kind") or "") == "warning"
            and not str(local.get("plugin_error") or "")
        ):
            local["last_error"] = {}

        local["current_connection_state"] = derive_connection_state(
            bridge_root=self._cfg.bridge_root,
            plugin_error=str(local["plugin_error"]),
            active_session_id=str(local["active_session_id"]),
            last_seen_data_monotonic=float(local["last_seen_data_monotonic"]),
            now_monotonic=now_monotonic,
            stale_after_seconds=self._cfg.stale_after_seconds,
            stream_reset_pending=bool(local["stream_reset_pending"]),
        )
        interval = next_poll_interval_for_state(
            local["current_connection_state"],
            stream_reset_pending=bool(local["stream_reset_pending"]),
            config=self._cfg,
        )
        if (
            self._cfg.ocr_reader_enabled
            and ocr_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
            and str(ocr_reader_runtime.get("status") or "") in {"starting", "active"}
            and str(local.get("active_data_source") or "") == DATA_SOURCE_OCR_READER
        ):
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif (
            self._cfg.ocr_reader_enabled
            and ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and str(ocr_reader_runtime.get("status") or "") == "starting"
        ):
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif self._cfg.ocr_reader_enabled and after_advance_screen_refresh_needed:
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        elif self._cfg.ocr_reader_enabled and companion_after_advance_ocr_refresh_needed:
            interval = min(interval, float(self._cfg.ocr_reader_poll_interval_seconds))
        if self._has_pending_ocr_advance_capture():
            next_pending_delay = self._pending_ocr_advance_capture_delay_remaining()
            interval = min(
                interval,
                next_pending_delay
                if next_pending_delay > 0.0
                else _OCR_AFTER_ADVANCE_SETTLE_POLL_SECONDS,
            )
        local["next_poll_at_monotonic"] = now_monotonic + interval
        self._commit_state(local)

        try:
            self._config_service.persist_runtime_state(local)
        except Exception as exc:
            self._record_error(
                make_error(
                    f"persist runtime failed: {exc}",
                    source="store",
                    kind="error",
                )
            )

    async def _apply_bridge_candidate_session(
        self,
        *,
        local: dict[str, Any],
        candidate: Any,
        warnings: list[str],
        now_monotonic: float,
    ) -> None:
        if self._cfg is None:
            return

        session = candidate.session
        session_id = str(session.get("session_id") or "")
        session_changed = (
            candidate.game_id != local["active_game_id"]
            or session_id != local["active_session_id"]
        )
        restore_cursor = (
            not session_changed
            and local["events_byte_offset"] > 0
            and local["active_session_id"] == session_id
        )
        warmup_needed = session_id != local["warmup_session_id"] or session_changed

        local["active_game_id"] = candidate.game_id
        local["active_session_id"] = session_id
        local["active_session_meta"] = build_active_session_meta(candidate)
        local["active_data_source"] = candidate.data_source
        local["latest_snapshot"] = json_copy(session.get("state", {}))

        if warmup_needed:
            end_offset = int(local["events_byte_offset"]) if restore_cursor else None
            warmup_events = await asyncio.to_thread(
                warmup_replay_events,
                candidate.events_path,
                bytes_limit=self._cfg.warmup_replay_bytes_limit,
                events_limit=self._cfg.warmup_replay_events_limit,
                end_offset=end_offset,
            )
            base_dedupe = list(local["dedupe_window"]) if restore_cursor else []
            (
                local["history_events"],
                local["history_lines"],
                local["history_observed_lines"],
                local["history_choices"],
                local["dedupe_window"],
                local["latest_snapshot"],
            ) = rebuild_histories_from_events(
                events=warmup_events,
                snapshot=local["latest_snapshot"],
                dedupe_window=base_dedupe,
                config=self._cfg,
                game_id=candidate.game_id,
            )
            try:
                file_size = await asyncio.to_thread(lambda: candidate.events_path.stat().st_size)
            except OSError:
                file_size = 0
            if restore_cursor and int(local["events_byte_offset"]) <= file_size:
                local["events_file_size"] = file_size
                local["last_seq"] = int(local["last_seq"])
            else:
                local["events_byte_offset"] = file_size
                local["events_file_size"] = file_size
                local["last_seq"] = max(
                    int(session.get("last_seq") or 0),
                    max((int(event.get("seq") or 0) for event in warmup_events), default=0),
                )
            local["line_buffer"] = b""
            local["stream_reset_pending"] = False
            local["warmup_session_id"] = session_id
            local["last_seen_data_monotonic"] = now_monotonic

        if int(session.get("last_seq") or 0) > int(local["last_seq"]):
            local["last_seen_data_monotonic"] = now_monotonic

        read_offset = 0 if local["stream_reset_pending"] else int(local["events_byte_offset"])
        read_buffer = b"" if local["stream_reset_pending"] else bytes(local["line_buffer"])
        tail = await asyncio.to_thread(
            tail_events_jsonl,
            candidate.events_path,
            offset=read_offset,
            line_buffer=read_buffer,
        )
        warnings.extend(tail.errors)

        if tail.reset_detected:
            local["stream_reset_pending"] = True
            local["line_buffer"] = b""
            local["events_file_size"] = tail.file_size
            return

        confirm_reset = False
        if local["stream_reset_pending"] and tail.events:
            first = tail.events[0]
            first_seq = int(first.get("seq") or 0)
            first_session_id = str(first.get("session_id") or "")
            confirm_reset = first_seq == 1 and (
                first_session_id != local["active_session_id"]
                or int(local["last_seq"]) > 0
            )

        if confirm_reset:
            local["history_events"] = []
            local["history_lines"] = []
            local["history_observed_lines"] = []
            local["history_choices"] = []
            local["dedupe_window"] = []
            local["line_buffer"] = b""
            local["events_byte_offset"] = 0
            local["last_seq"] = 0
            local["stream_reset_pending"] = False

        if local["stream_reset_pending"]:
            return

        for event in tail.events:
            if str(event.get("session_id") or "") != local["active_session_id"]:
                continue
            seq = int(event.get("seq") or 0)
            if seq <= int(local["last_seq"]):
                continue
            apply_event_to_histories(
                history_events=local["history_events"],
                history_lines=local["history_lines"],
                history_observed_lines=local["history_observed_lines"],
                history_choices=local["history_choices"],
                dedupe_window=local["dedupe_window"],
                event=event,
                config=self._cfg,
                game_id=candidate.game_id,
            )
            local["latest_snapshot"] = apply_event_to_snapshot(
                local["latest_snapshot"], event
            )
            local["last_seq"] = seq
            local["last_seen_data_monotonic"] = now_monotonic

        local["events_byte_offset"] = tail.next_offset
        local["events_file_size"] = tail.file_size
        local["line_buffer"] = tail.line_buffer

    def _clear_bridge_candidate_session(
        self,
        *,
        local: dict[str, Any],
        reader_mode: str,
        memory_reader_allowed: bool,
        ocr_reader_allowed: bool,
        memory_reader_candidate_available: bool,
    ) -> None:
        local["active_data_source"] = _pending_data_source_for_reader_mode(
            reader_mode,
            memory_reader_allowed=memory_reader_allowed,
            ocr_reader_allowed=ocr_reader_allowed,
            memory_reader_candidate_available=memory_reader_candidate_available,
        )
        if not local["bound_game_id"]:
            local["active_game_id"] = ""
            local["active_session_id"] = ""
            local["active_session_meta"] = {}
        local["line_buffer"] = b""

    async def _poll_bridge_locked(self, *, force: bool) -> None:
        if self._cfg is None:
            return

        now_monotonic = time.monotonic()
        local = self._snapshot_state(fresh=True)
        local["_commit_base"] = {
            "bound_game_id": str(local.get("bound_game_id") or ""),
            "mode": str(local.get("mode") or ""),
            "push_notifications": bool(local.get("push_notifications")),
            "advance_speed": str(local.get("advance_speed") or ""),
            "active_data_source": str(local.get("active_data_source") or ""),
            "ocr_capture_profiles": json_copy(local.get("ocr_capture_profiles") or {}),
            "ocr_window_target": json_copy(local.get("ocr_window_target") or {}),
        }
        next_poll_at = float(local["next_poll_at_monotonic"])
        max_reasonable_interval = max(
            float(self._cfg.active_poll_interval_seconds),
            float(self._cfg.idle_poll_interval_seconds),
            float(self._cfg.ocr_reader_poll_interval_seconds),
            1.0,
        ) * 5.0
        if not force and next_poll_at > now_monotonic + max_reasonable_interval:
            local["next_poll_at_monotonic"] = 0.0
            next_poll_at = 0.0
        if not force and now_monotonic < next_poll_at:
            return

        warnings: list[str] = []
        raw_available_game_ids: list[str] = []
        raw_candidates: dict[str, Any] = {}
        memory_reader_runtime = json_copy(local.get("memory_reader_runtime") or {})
        ocr_reader_runtime = json_copy(local.get("ocr_reader_runtime") or {})
        reader_mode = _normalize_reader_mode(getattr(self._cfg, "reader_mode", READER_MODE_AUTO))
        memory_reader_allowed = reader_mode in {READER_MODE_AUTO, READER_MODE_MEMORY}
        ocr_reader_allowed = reader_mode in {READER_MODE_AUTO, READER_MODE_OCR}
        ocr_reader_allowed_block_reason = (
            "reader_mode_memory_only" if reader_mode == READER_MODE_MEMORY else ""
        )

        try:
            raw_available_game_ids, raw_candidates, scan_warnings = await self._scan_candidates()
            warnings.extend(scan_warnings)
        except Exception as exc:
            self._commit_bridge_scan_failure(
                local,
                now_monotonic=now_monotonic,
                exc=exc,
            )
            return

        memory_reader_candidate_available = any(
            candidate.data_source == DATA_SOURCE_MEMORY_READER
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        bridge_sdk_candidate_available = any(
            candidate.data_source == DATA_SOURCE_BRIDGE_SDK
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        ocr_trigger_mode = str(
            getattr(self._cfg, "ocr_reader_trigger_mode", OCR_TRIGGER_MODE_INTERVAL)
            or OCR_TRIGGER_MODE_INTERVAL
        )
        ocr_context_state = str(ocr_reader_runtime.get("ocr_context_state") or "")
        ocr_bootstrap_capture_needed = (
            ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and (
                ocr_context_state in {"", "capture_pending", "observed"}
                or (
                    ocr_context_state == "no_text"
                    and int(ocr_reader_runtime.get("consecutive_no_text_polls") or 0) < 3
                )
            )
        )
        pending_ocr_advance_capture = self._has_pending_ocr_advance_capture()
        with self._state_lock:
            pending_ocr_advance_reason = str(self._last_ocr_advance_capture_reason or "")
        pending_manual_foreground_ocr_capture = (
            pending_ocr_advance_capture
            and ocr_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            and pending_ocr_advance_reason in {
                "manual_foreground_advance",
                "foreground_target_activated",
            }
        )
        pending_ocr_delay_remaining = (
            self._pending_ocr_advance_capture_delay_remaining()
            if pending_ocr_advance_capture and not force
            else 0.0
        )
        (
            raw_available_game_ids,
            raw_candidates,
            memory_reader_runtime,
            memory_tick_warnings,
        ) = await self._tick_memory_reader_for_poll(
            memory_reader_allowed=memory_reader_allowed,
            bridge_sdk_candidate_available=bridge_sdk_candidate_available,
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
        )
        warnings.extend(memory_tick_warnings)
        current_memory_target = (
            getattr(self._memory_reader_manager, "current_process_target", None)
            if self._memory_reader_manager is not None
            else None
        )
        if callable(current_memory_target):
            resolved_memory_target = current_memory_target()
            if resolved_memory_target != json_copy(local.get("memory_reader_target") or {}):
                local["memory_reader_target"] = json_copy(resolved_memory_target)
                try:
                    self._persist.persist_memory_reader_target(resolved_memory_target)
                except Exception as exc:
                    warnings.append(f"persist memory reader target failed: {exc}")
        memory_reader_candidate_available = any(
            candidate.data_source == DATA_SOURCE_MEMORY_READER
            and _session_candidate_has_text(candidate)
            for candidate in raw_candidates.values()
        )
        if memory_reader_allowed:
            memory_reader_recent_text_available = self._update_memory_reader_text_freshness(
                memory_reader_runtime,
                now_monotonic=now_monotonic,
            )
        else:
            self._update_memory_reader_text_freshness({}, now_monotonic=now_monotonic)
            memory_reader_recent_text_available = False
        ocr_reader_explicitly_configured = bool(
            (
                bool(getattr(self._cfg, "ocr_reader_enabled", False))
                and bool(getattr(self._cfg, "ocr_reader_enabled_explicit", False))
            )
            or str(getattr(self._cfg, "ocr_reader_tesseract_path", "") or "").strip()
            or str(getattr(self._cfg, "ocr_reader_install_target_dir", "") or "").strip()
            or str(getattr(self._cfg, "rapidocr_install_target_dir", "") or "").strip()
            or (
                bool(getattr(self._cfg, "rapidocr_enabled", False))
                and bool(getattr(self._cfg, "rapidocr_enabled_explicit", False))
            )
            or (
                bool(getattr(self._cfg, "ocr_reader_backend_selection_explicit", False))
                and str(getattr(self._cfg, "ocr_reader_backend_selection", "") or "")
                .strip()
                .lower()
                in {"rapidocr", "tesseract"}
            )
            or (
                bool(getattr(self._cfg, "ocr_reader_capture_backend_explicit", False))
                and str(getattr(self._cfg, "ocr_reader_capture_backend", "") or "")
                .strip()
                .lower()
                in {"smart", "dxcam", "imagegrab", "printwindow"}
            )
        )
        memory_reader_default_is_unavailable = (
            reader_mode == READER_MODE_AUTO
            and memory_reader_allowed
            and bool(getattr(self._cfg, "memory_reader_enabled", False))
            and not memory_reader_candidate_available
            and str(memory_reader_runtime.get("status") or "") in {"idle", "backoff"}
            and str(memory_reader_runtime.get("detail") or "")
            in {"invalid_textractor_path", "no_detected_game_process"}
            and not ocr_reader_explicitly_configured
            and not pending_manual_foreground_ocr_capture
            and not (
                str(local.get("active_data_source") or "") == DATA_SOURCE_OCR_READER
                and bool(str(local.get("active_session_id") or ""))
            )
        )
        if memory_reader_default_is_unavailable:
            ocr_reader_allowed = False
            ocr_reader_allowed_block_reason = _ocr_reader_allowed_block_reason(
                reader_mode=reader_mode,
                memory_reader_default_is_unavailable=memory_reader_default_is_unavailable,
                memory_reader_recent_text_available=False,
            )
        if (
            reader_mode == READER_MODE_AUTO
            and memory_reader_recent_text_available
            and not pending_manual_foreground_ocr_capture
        ):
            ocr_reader_allowed = False
            ocr_reader_allowed_block_reason = _ocr_reader_allowed_block_reason(
                reader_mode=reader_mode,
                memory_reader_default_is_unavailable=memory_reader_default_is_unavailable,
                memory_reader_recent_text_available=True,
            )
            with self._state_lock:
                self._clear_pending_ocr_advance_captures_locked()

        ocr_reader_stable_event_emitted = False
        (
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            pending_ocr_delay_remaining,
            foreground_refresh_warnings,
        ) = await self._refresh_ocr_foreground_for_poll(
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            pending_ocr_delay_remaining=pending_ocr_delay_remaining,
        )
        warnings.extend(foreground_refresh_warnings)
        after_advance_screen_refresh_tick_needed = _after_advance_screen_refresh_needed(
            local=local,
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
        )
        companion_after_advance_ocr_refresh_tick_needed = (
            _companion_after_advance_ocr_refresh_needed(
                local=local,
                ocr_reader_runtime=ocr_reader_runtime,
                ocr_reader_allowed=ocr_reader_allowed,
                ocr_trigger_mode=ocr_trigger_mode,
            )
        )
        ocr_tick_gate_allowed = (
            ocr_reader_allowed
            and (
                ocr_trigger_mode == OCR_TRIGGER_MODE_INTERVAL
                or force
                or ocr_bootstrap_capture_needed
                or after_advance_screen_refresh_tick_needed
                or companion_after_advance_ocr_refresh_tick_needed
                or (pending_ocr_advance_capture and pending_ocr_delay_remaining <= 0.0)
                or str(ocr_reader_runtime.get("status") or "") not in {"active"}
                or str(local.get("active_data_source") or "") != DATA_SOURCE_OCR_READER
            )
        )
        ocr_tick_allowed = bool(ocr_tick_gate_allowed and self._ocr_reader_manager is not None)
        ocr_tick_block_reason = _ocr_tick_block_reason(
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_reader_manager_available=self._ocr_reader_manager is not None,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_reader_allowed_block_reason=ocr_reader_allowed_block_reason,
            ocr_trigger_mode=ocr_trigger_mode,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            pending_ocr_delay_remaining=pending_ocr_delay_remaining,
            ocr_bootstrap_capture_needed=ocr_bootstrap_capture_needed,
            after_advance_screen_refresh_needed=after_advance_screen_refresh_tick_needed,
            companion_after_advance_ocr_refresh_needed=(
                companion_after_advance_ocr_refresh_tick_needed
            ),
            ocr_reader_runtime=ocr_reader_runtime,
            active_data_source=str(local.get("active_data_source") or ""),
            mode=str(local.get("mode") or ""),
        )
        pending_ocr_advance_clear_reason = ""
        pending_ocr_advance_age = self._pending_ocr_advance_capture_age()
        ocr_reader_capture_failed_pending = (
            str(ocr_reader_runtime.get("detail") or "") == "capture_failed"
            or str(ocr_reader_runtime.get("ocr_context_state") or "") == "capture_failed"
        )
        if (
            pending_ocr_advance_capture
            and not ocr_tick_allowed
            and not ocr_reader_capture_failed_pending
            and pending_ocr_advance_age >= _OCR_AFTER_ADVANCE_MAX_SETTLE_SECONDS
        ):
            self._clear_pending_ocr_advance_captures()
            pending_ocr_advance_capture = False
            pending_manual_foreground_ocr_capture = False
            pending_ocr_delay_remaining = 0.0
            pending_ocr_advance_clear_reason = "tick_gate_timeout"
            pending_ocr_advance_reason = ""
        ocr_tick_gate_diagnostics = {
            "ocr_tick_gate_allowed": bool(ocr_tick_gate_allowed),
            "ocr_reader_manager_available": self._ocr_reader_manager is not None,
            "pending_ocr_advance_capture": bool(pending_ocr_advance_capture),
            "pending_manual_foreground_ocr_capture": bool(
                pending_manual_foreground_ocr_capture
            ),
            "pending_ocr_advance_reason": str(pending_ocr_advance_reason or ""),
            "pending_ocr_delay_remaining": float(pending_ocr_delay_remaining or 0.0),
            "pending_ocr_advance_capture_age_seconds": float(
                pending_ocr_advance_age or 0.0
            ),
            "pending_ocr_advance_clear_reason": pending_ocr_advance_clear_reason,
            "ocr_bootstrap_capture_needed": bool(ocr_bootstrap_capture_needed),
            "after_advance_screen_refresh_tick_needed": bool(
                after_advance_screen_refresh_tick_needed
            ),
            "companion_after_advance_ocr_refresh_tick_needed": bool(
                companion_after_advance_ocr_refresh_tick_needed
            ),
            "ocr_runtime_status": str(ocr_reader_runtime.get("status") or ""),
            "active_data_source": str(local.get("active_data_source") or ""),
            "mode": str(local.get("mode") or ""),
            "foreground_refresh_attempted": bool(
                ocr_reader_runtime.get("foreground_refresh_attempted")
            ),
            "foreground_refresh_skipped_reason": str(
                ocr_reader_runtime.get("foreground_refresh_skipped_reason") or ""
            ),
        }

        (
            raw_available_game_ids,
            raw_candidates,
            ocr_reader_runtime,
            pending_ocr_advance_capture,
            ocr_reader_stable_event_emitted,
            ocr_tick_warnings,
        ) = await self._tick_ocr_reader_for_poll(
            local=local,
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
            ocr_reader_runtime=ocr_reader_runtime,
            bridge_sdk_candidate_available=bridge_sdk_candidate_available,
            ocr_tick_allowed=ocr_tick_allowed,
            pending_manual_foreground_ocr_capture=pending_manual_foreground_ocr_capture,
            pending_ocr_advance_capture=pending_ocr_advance_capture,
            force=force,
        )
        warnings.extend(ocr_tick_warnings)
        if not pending_ocr_advance_capture and not pending_ocr_advance_clear_reason:
            pending_ocr_advance_reason = ""
            pending_ocr_delay_remaining = 0.0
        ocr_tick_gate_diagnostics.update(
            {
                "pending_ocr_advance_capture": bool(pending_ocr_advance_capture),
                "pending_manual_foreground_ocr_capture": bool(
                    pending_manual_foreground_ocr_capture
                    and pending_ocr_advance_capture
                ),
                "pending_ocr_advance_reason": str(pending_ocr_advance_reason or ""),
                "pending_ocr_delay_remaining": float(pending_ocr_delay_remaining or 0.0),
                "ocr_runtime_status": str(ocr_reader_runtime.get("status") or ""),
                "foreground_refresh_attempted": bool(
                    ocr_reader_runtime.get("foreground_refresh_attempted")
                ),
                "foreground_refresh_skipped_reason": str(
                    ocr_reader_runtime.get("foreground_refresh_skipped_reason") or ""
                ),
            }
        )
        ocr_emit_block_reason = _ocr_emit_block_reason(
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_reader_stable_event_emitted=ocr_reader_stable_event_emitted,
            ocr_reader_runtime=ocr_reader_runtime,
        )
        ocr_reader_runtime = _apply_ocr_decision_diagnostics(
            ocr_reader_runtime,
            ocr_tick_allowed=ocr_tick_allowed,
            ocr_tick_block_reason=ocr_tick_block_reason,
            ocr_emit_block_reason=ocr_emit_block_reason,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_reader_allowed_block_reason=ocr_reader_allowed_block_reason,
            ocr_trigger_mode=ocr_trigger_mode,
            active_data_source=str(local.get("active_data_source") or ""),
            ocr_tick_gate_diagnostics=ocr_tick_gate_diagnostics,
        )

        local["memory_reader_runtime"] = memory_reader_runtime
        local["ocr_reader_runtime"] = ocr_reader_runtime
        available_game_ids, candidates = self._filter_candidates_for_reader_mode(
            raw_available_game_ids=raw_available_game_ids,
            raw_candidates=raw_candidates,
            memory_reader_runtime=memory_reader_runtime,
            ocr_reader_runtime=ocr_reader_runtime,
            reader_mode=reader_mode,
        )
        local["available_game_ids"] = available_game_ids
        candidate_reader_mode = reader_mode
        if (
            reader_mode == READER_MODE_AUTO
            and pending_manual_foreground_ocr_capture
            and ocr_reader_stable_event_emitted
            and not bridge_sdk_candidate_available
        ):
            candidate_reader_mode = READER_MODE_OCR
        elif (
            reader_mode == READER_MODE_AUTO
            and not memory_reader_recent_text_available
            and not bridge_sdk_candidate_available
            and any(
                candidate.data_source == DATA_SOURCE_OCR_READER
                for candidate in candidates.values()
            )
        ):
            candidate_reader_mode = READER_MODE_OCR

        keep_current = (
            not local["bound_game_id"]
            and local["current_connection_state"] == STATE_ACTIVE
            and bool(local["active_game_id"])
        )
        candidate = choose_candidate(
            candidates,
            bound_game_id=str(local["bound_game_id"]),
            current_game_id=str(local["active_game_id"]),
            keep_current=keep_current,
            reader_mode=candidate_reader_mode,
        )

        if candidate is not None:
            await self._apply_bridge_candidate_session(
                local=local,
                candidate=candidate,
                warnings=warnings,
                now_monotonic=now_monotonic,
            )
        else:
            self._clear_bridge_candidate_session(
                local=local,
                reader_mode=reader_mode,
                memory_reader_allowed=memory_reader_allowed,
                ocr_reader_allowed=ocr_reader_allowed,
                memory_reader_candidate_available=memory_reader_recent_text_available,
            )

        after_advance_screen_refresh_schedule_needed = _after_advance_screen_refresh_needed(
            local=local,
            ocr_reader_runtime=ocr_reader_runtime,
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode=ocr_trigger_mode,
        )
        companion_after_advance_ocr_refresh_schedule_needed = (
            _companion_after_advance_ocr_refresh_needed(
                local=local,
                ocr_reader_runtime=ocr_reader_runtime,
                ocr_reader_allowed=ocr_reader_allowed,
                ocr_trigger_mode=ocr_trigger_mode,
            )
        )
        self._finalize_bridge_poll_state(
            local,
            warnings=warnings,
            now_monotonic=now_monotonic,
            ocr_trigger_mode=ocr_trigger_mode,
            ocr_reader_runtime=ocr_reader_runtime,
            after_advance_screen_refresh_needed=after_advance_screen_refresh_schedule_needed,
            companion_after_advance_ocr_refresh_needed=(
                companion_after_advance_ocr_refresh_schedule_needed
            ),
        )

    @plugin_entry(
        id="galgame_get_status",
        name="获取 galgame 插件状态",
        description="返回当前 bridge 连接状态、绑定游戏、最近错误与模式。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def galgame_get_status(self, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        return Ok(await self._build_status_payload_async())

    @plugin_entry(
        id="galgame_install_textractor",
        name="安装 Textractor",
        description="检测并下载安装 TextractorCLI.exe，随后刷新 galgame_plugin 的桥接与读内存状态。",
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=180.0,
        llm_result_fields=["summary"],
    )
    async def galgame_install_textractor(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not self._textractor_install_lock.acquire(blocking=False):
            return Err(SdkError("Textractor install is already in progress"))
        current_run_id = self._resolve_current_run_id()
        progress_callback = self._resolve_install_progress_callback(current_run_id)
        try:
            install_result = await install_textractor(
                logger=self.logger,
                configured_path=self._cfg.memory_reader_textractor_path,
                install_target_dir_raw=self._cfg.memory_reader_install_target_dir,
                release_api_url=self._cfg.memory_reader_install_release_api_url,
                timeout_seconds=self._cfg.memory_reader_install_timeout_seconds,
                force=bool(force),
                task_id=current_run_id or None,
                progress_callback=progress_callback,
            )
            clear_install_inspection_cache()
            await self._poll_bridge(force=True)
            return Ok(
                {
                    "summary": str(install_result.get("summary") or "Textractor 安装完成"),
                    "install_result": install_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(_format_install_entry_error("Textractor", exc)))
        finally:
            self._textractor_install_lock.release()

    @plugin_entry(
        id="galgame_install_tesseract",
        name="安装 Tesseract",
        description="检测并下载安装本地 Tesseract OCR，随后刷新 galgame_plugin 的 OCR 状态。",
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=300.0,
        llm_result_fields=["summary"],
    )
    async def galgame_install_tesseract(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not self._tesseract_install_lock.acquire(blocking=False):
            return Err(SdkError("Tesseract install is already in progress"))
        current_run_id = self._resolve_current_run_id()
        progress_callback = self._resolve_install_progress_callback(current_run_id)
        try:
            install_result = await install_tesseract(
                logger=self.logger,
                configured_path=self._cfg.ocr_reader_tesseract_path,
                install_target_dir_raw=self._cfg.ocr_reader_install_target_dir,
                manifest_url=self._cfg.ocr_reader_install_manifest_url,
                timeout_seconds=self._cfg.ocr_reader_install_timeout_seconds,
                languages=self._cfg.ocr_reader_languages,
                force=bool(force),
                task_id=current_run_id or None,
                progress_callback=progress_callback,
            )
            clear_install_inspection_cache()
            await self._poll_bridge(force=True)
            return Ok(
                {
                    "summary": str(install_result.get("summary") or "Tesseract 安装完成"),
                    "install_result": install_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(_format_install_entry_error("Tesseract", exc)))
        finally:
            self._tesseract_install_lock.release()

    @plugin_entry(
        id="galgame_install_rapidocr",
        name="安装 RapidOCR",
        description="检测并下载安装插件隔离的 RapidOCR 运行时，随后刷新 galgame_plugin 的 OCR 状态。",
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=300.0,
        llm_result_fields=["summary"],
    )
    async def galgame_install_rapidocr(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not self._rapidocr_install_lock.acquire(blocking=False):
            return Err(SdkError("RapidOCR install is already in progress"))
        current_run_id = self._resolve_current_run_id()
        progress_callback = self._resolve_install_progress_callback(current_run_id)
        try:
            install_result = await install_rapidocr(
                logger=self.logger,
                install_target_dir_raw=self._cfg.rapidocr_install_target_dir,
                manifest_url=self._cfg.rapidocr_install_manifest_url,
                timeout_seconds=self._cfg.rapidocr_install_timeout_seconds,
                engine_type=self._cfg.rapidocr_engine_type,
                lang_type=self._cfg.rapidocr_lang_type,
                model_type=self._cfg.rapidocr_model_type,
                ocr_version=self._cfg.rapidocr_ocr_version,
                force=bool(force),
                task_id=current_run_id or None,
                progress_callback=progress_callback,
            )
            clear_install_inspection_cache()
            await self._poll_bridge(force=True)
            return Ok(
                {
                    "summary": str(install_result.get("summary") or "RapidOCR 安装完成"),
                    "install_result": install_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(_format_install_entry_error("RapidOCR", exc)))
        finally:
            self._rapidocr_install_lock.release()

    @plugin_entry(
        id="galgame_install_dxcam",
        name="安装 DXcam",
        description="检测并安装 DXcam 截图依赖，随后刷新 galgame_plugin 的 OCR 截图后端状态。",
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=180.0,
        llm_result_fields=["summary"],
    )
    async def galgame_install_dxcam(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not self._dxcam_install_lock.acquire(blocking=False):
            return Err(SdkError("DXcam install is already in progress"))
        current_run_id = self._resolve_current_run_id()
        progress_callback = self._resolve_install_progress_callback(current_run_id)
        try:
            install_result = await install_dxcam(
                logger=self.logger,
                timeout_seconds=self._cfg.ocr_reader_install_timeout_seconds,
                force=bool(force),
                task_id=current_run_id or None,
                progress_callback=progress_callback,
            )
            clear_install_inspection_cache()
            await self._poll_bridge(force=True)
            return Ok(
                {
                    "summary": str(install_result.get("summary") or "DXcam 安装完成"),
                    "install_result": install_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(_format_install_entry_error("DXcam", exc)))
        finally:
            self._dxcam_install_lock.release()

    @plugin_entry(
        id="galgame_get_snapshot",
        name="获取 galgame 快照",
        description="返回当前游戏快照和 stale 状态。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["snapshot"],
    )
    async def galgame_get_snapshot(self, **_):
        state_snapshot = self._snapshot_state()
        payload = build_snapshot_payload(SimpleNamespace(**state_snapshot))
        return Ok(payload)

    @plugin_entry(
        id="galgame_get_history",
        name="获取 galgame 历史",
        description="返回最近事件、稳定台词历史和选项历史。",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50, "minimum": 1},
                "include_events": {"type": "boolean", "default": True},
            },
        },
        llm_result_fields=["stable_lines", "observed_lines", "choices"],
    )
    async def galgame_get_history(self, limit: int = 50, include_events: bool = True, **_):
        with self._state_lock:
            payload = build_history_payload(
                self._state,
                limit=max(1, int(limit)),
                include_events=bool(include_events),
            )
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_mode",
        name="设置 galgame 模式",
        description="设置 silent / companion / choice_advisor 模式，并可选更新通知开关。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": sorted(MODES)},
                "push_notifications": {"type": "boolean"},
                "advance_speed": {"type": "string", "enum": sorted(ADVANCE_SPEEDS)},
                "reader_mode": {"type": "string", "enum": sorted(READER_MODES)},
            },
            "required": ["mode"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_mode(
        self,
        mode: str,
        push_notifications: bool | None = None,
        advance_speed: str | None = None,
        reader_mode: str | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if mode not in MODES:
            return Err(SdkError(f"invalid galgame mode: {mode!r}"))
        if advance_speed is not None and advance_speed not in ADVANCE_SPEEDS:
            return Err(SdkError(f"invalid advance speed: {advance_speed!r}"))
        try:
            normalized_reader_mode = _normalize_reader_mode(reader_mode or self._cfg.reader_mode)
        except ValueError as exc:
            return Err(SdkError(str(exc)))

        with self._state_lock:
            current_mode = str(self._state.mode or "")
            current_push_notifications = bool(self._state.push_notifications)
            current_advance_speed = str(self._state.advance_speed or ADVANCE_SPEED_MEDIUM)
        current_reader_mode = self._cfg.reader_mode
        requested_push_notifications = (
            bool(push_notifications)
            if push_notifications is not None
            else current_push_notifications
        )
        requested_advance_speed = (
            str(advance_speed)
            if advance_speed is not None
            else current_advance_speed
        )
        if (
            mode == current_mode
            and requested_push_notifications == current_push_notifications
            and requested_advance_speed == current_advance_speed
            and normalized_reader_mode == current_reader_mode
        ):
            return Ok(
                {
                    "mode": current_mode,
                    "push_notifications": current_push_notifications,
                    "advance_speed": current_advance_speed,
                    "reader_mode": current_reader_mode,
                    "summary": (
                        f"mode={current_mode} "
                        f"push_notifications={current_push_notifications} "
                        f"advance_speed={current_advance_speed} "
                        f"reader_mode={current_reader_mode}"
                    ),
                    "skipped": True,
                    "skip_reason": "already_applied",
                }
            )

        with self._state_lock:
            old_mode = str(self._state.mode or "")
            old_push_notifications = bool(self._state.push_notifications)
            old_advance_speed = str(self._state.advance_speed or ADVANCE_SPEED_MEDIUM)
            old_active_data_source = str(self._state.active_data_source or "")
            old_next_poll_at_monotonic = float(self._state.next_poll_at_monotonic or 0.0)
            old_pending_ocr_advance_captures = int(self._pending_ocr_advance_captures or 0)
            old_last_ocr_advance_capture_requested_at = float(
                self._last_ocr_advance_capture_requested_at or 0.0
            )
            old_last_ocr_advance_capture_reason = str(
                self._last_ocr_advance_capture_reason or ""
            )
        old_reader_mode = self._cfg.reader_mode

        def _restore_mode_runtime_state() -> None:
            self._cfg.reader_mode = old_reader_mode
            with self._state_lock:
                self._state.mode = old_mode
                self._state.push_notifications = old_push_notifications
                self._state.advance_speed = old_advance_speed
                self._state.active_data_source = old_active_data_source
                self._state.next_poll_at_monotonic = old_next_poll_at_monotonic
                self._pending_ocr_advance_captures = old_pending_ocr_advance_captures
                self._last_ocr_advance_capture_requested_at = (
                    old_last_ocr_advance_capture_requested_at
                )
                self._last_ocr_advance_capture_reason = old_last_ocr_advance_capture_reason
                self._state_dirty = True
                self._cached_snapshot = None
            for manager, label in (
                (self._memory_reader_manager, "memory reader"),
                (self._ocr_reader_manager, "OCR reader"),
            ):
                if manager is None:
                    continue
                try:
                    manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame {} mode rollback update_config failed: {}",
                        label,
                        rollback_exc,
                    )

        # galgame_set_mode runs in the plugin's asyncio flow; simple config field
        # assignment is atomic here and readers use getattr fallbacks.
        self._cfg.reader_mode = normalized_reader_mode
        try:
            if self._memory_reader_manager is not None:
                self._memory_reader_manager.update_config(self._cfg)
            if self._ocr_reader_manager is not None:
                self._ocr_reader_manager.update_config(self._cfg)
        except Exception as exc:
            _restore_mode_runtime_state()
            return Err(SdkError(f"apply mode failed: {exc}"))
        with self._state_lock:
            self._state.mode = mode
            if push_notifications is not None:
                self._state.push_notifications = bool(push_notifications)
            if advance_speed is not None:
                self._state.advance_speed = advance_speed
            if normalized_reader_mode == READER_MODE_MEMORY:
                self._clear_pending_ocr_advance_captures_locked()
            if not self._state.active_session_id:
                self._state.active_data_source = _pending_data_source_for_reader_mode(
                    normalized_reader_mode,
                    memory_reader_allowed=normalized_reader_mode in {READER_MODE_AUTO, READER_MODE_MEMORY},
                    ocr_reader_allowed=normalized_reader_mode in {READER_MODE_AUTO, READER_MODE_OCR},
                    memory_reader_candidate_available=False,
                )
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None
            payload = {
                "mode": self._state.mode,
                "push_notifications": self._state.push_notifications,
                "advance_speed": self._state.advance_speed,
                "reader_mode": self._cfg.reader_mode,
                "summary": (
                    f"mode={self._state.mode} "
                    f"push_notifications={self._state.push_notifications} "
                    f"advance_speed={self._state.advance_speed} "
                    f"reader_mode={self._cfg.reader_mode}"
                ),
            }
            bound_game_id = self._state.bound_game_id
            persist_push = self._state.push_notifications
            persist_advance_speed = self._state.advance_speed

        try:
            self._config_service.persist_preferences(
                bound_game_id=bound_game_id,
                mode=mode,
                push_notifications=persist_push,
                advance_speed=persist_advance_speed,
            )
            self._config_service.persist_reader_mode(reader_mode=normalized_reader_mode)
        except Exception as exc:
            _restore_mode_runtime_state()
            return Err(SdkError(f"persist mode failed: {exc}"))
        await self._ensure_ocr_foreground_advance_monitor()
        if (
            mode_allows_agent_actuation(old_mode)
            and not mode_allows_agent_actuation(mode)
        ):
            self.request_ocr_after_advance_capture(reason="mode_change_to_read_only")
        self._start_background_bridge_poll()

        # 进入 choice_advisor 时默认启用 OCR fast loop；离开时仅关闭自动开启的。
        if mode == MODE_CHOICE_ADVISOR and old_mode != MODE_CHOICE_ADVISOR:
            if not self._ocr_fast_loop_should_run():
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = True
                self._fast_loop_auto_enabled = True
                self._start_ocr_fast_loop()
        elif old_mode == MODE_CHOICE_ADVISOR and mode != MODE_CHOICE_ADVISOR:
            if self._fast_loop_auto_enabled:
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = False
                self._fast_loop_auto_enabled = False
                await self._cancel_ocr_fast_loop()

        if self._game_agent is not None and not mode_allows_agent_actuation(mode):
            try:
                agent_payload = await self._game_agent.apply_mode_change(self._snapshot_state())
                payload["agent"] = json_copy(agent_payload)
            except Exception as exc:
                payload["agent_warning"] = f"apply_mode_change failed: {exc}"
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_ocr_backend",
        name="设置 OCR / 截图后端",
        description="切换 OCR 文本识别后端和截图后端。只影响 OCR 读取，不改变 Agent 点击安全策略。",
        input_schema={
            "type": "object",
            "properties": {
                "backend_selection": {
                    "type": "string",
                    "enum": sorted(_OCR_BACKEND_SELECTIONS),
                },
                "capture_backend": {
                    "type": "string",
                    "enum": sorted(_OCR_CAPTURE_BACKEND_SELECTIONS),
                },
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_backend(
        self,
        backend_selection: str | None = None,
        capture_backend: str | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        normalized_backend = str(backend_selection or "").strip().lower() or None
        normalized_capture = str(capture_backend or "").strip().lower() or None
        if normalized_backend is None and normalized_capture is None:
            return Err(SdkError("backend_selection or capture_backend is required"))
        if normalized_backend is not None and normalized_backend not in _OCR_BACKEND_SELECTIONS:
            return Err(SdkError(f"invalid OCR backend: {backend_selection!r}"))
        if normalized_capture is not None and normalized_capture not in _OCR_CAPTURE_BACKEND_SELECTIONS:
            return Err(SdkError(f"invalid OCR capture backend: {capture_backend!r}"))

        old_backend = self._cfg.ocr_reader_backend_selection
        old_capture = self._cfg.ocr_reader_capture_backend
        old_backend_explicit = bool(
            getattr(self._cfg, "ocr_reader_backend_selection_explicit", False)
        )
        old_capture_explicit = bool(
            getattr(self._cfg, "ocr_reader_capture_backend_explicit", False)
        )
        backend_changed = normalized_backend is not None and normalized_backend != old_backend
        capture_changed = normalized_capture is not None and normalized_capture != old_capture
        if normalized_backend is not None:
            self._cfg.ocr_reader_backend_selection = normalized_backend
            self._cfg.ocr_reader_backend_selection_explicit = True
        if normalized_capture is not None:
            self._cfg.ocr_reader_capture_backend = normalized_capture
            self._cfg.ocr_reader_capture_backend_explicit = True
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                if normalized_backend is not None:
                    self._cfg.ocr_reader_backend_selection = old_backend
                    self._cfg.ocr_reader_backend_selection_explicit = old_backend_explicit
                if normalized_capture is not None:
                    self._cfg.ocr_reader_capture_backend = old_capture
                    self._cfg.ocr_reader_capture_backend_explicit = old_capture_explicit
                return Err(SdkError(f"apply OCR backend failed: {exc}"))

        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_backend_selection(
                backend_selection=normalized_backend,
                capture_backend=normalized_capture,
            )
        except Exception as exc:
            self._cfg.ocr_reader_backend_selection = old_backend
            self._cfg.ocr_reader_capture_backend = old_capture
            self._cfg.ocr_reader_backend_selection_explicit = old_backend_explicit
            self._cfg.ocr_reader_capture_backend_explicit = old_capture_explicit
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR backend rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR backend failed: {exc}"))

        if self._ocr_reader_manager is not None and (backend_changed or capture_changed):
            reset_capture_runtime = getattr(
                self._ocr_reader_manager,
                "reset_capture_runtime_diagnostics",
                None,
            )
            if callable(reset_capture_runtime):
                try:
                    reset_capture_runtime()
                except Exception as exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR backend switch diagnostic reset failed: {}",
                        exc,
                    )

        self._start_background_bridge_poll()
        payload = {
            "backend_selection": self._cfg.ocr_reader_backend_selection,
            "capture_backend": self._cfg.ocr_reader_capture_backend,
            "summary": (
                f"OCR backend={self._cfg.ocr_reader_backend_selection} "
                f"capture_backend={self._cfg.ocr_reader_capture_backend}"
            ),
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_ocr_timing",
        name="设置 OCR 识别时机",
        description="设置 OCR Reader 触发模式与轮询间隔；DXcam 截图后端会随 OCR 触发。",
        input_schema={
            "type": "object",
            "properties": {
                "poll_interval_seconds": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 10.0,
                },
                "trigger_mode": {
                    "type": "string",
                    "enum": ["interval", "after_advance"],
                    "default": "interval",
                },
                "fast_loop_enabled": {"type": "boolean"},
            },
            "required": ["poll_interval_seconds"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_timing(
        self,
        poll_interval_seconds: float,
        trigger_mode: str | None = None,
        fast_loop_enabled: bool | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        try:
            normalized_interval = float(poll_interval_seconds)
        except (TypeError, ValueError):
            return Err(SdkError("poll_interval_seconds must be a number"))
        if normalized_interval < 0.5 or normalized_interval > 10.0:
            return Err(SdkError("poll_interval_seconds must be between 0.5 and 10.0"))
        try:
            normalized_trigger_mode = _normalize_ocr_trigger_mode(
                trigger_mode or self._cfg.ocr_reader_trigger_mode
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))

        old_interval = self._cfg.ocr_reader_poll_interval_seconds
        old_trigger_mode = self._cfg.ocr_reader_trigger_mode
        old_fast_loop = self._cfg.ocr_reader_fast_loop_enabled
        old_fast_loop_auto_enabled = self._fast_loop_auto_enabled
        if fast_loop_enabled is not None:
            self._fast_loop_auto_enabled = False
        self._cfg.ocr_reader_poll_interval_seconds = normalized_interval
        self._cfg.ocr_reader_trigger_mode = normalized_trigger_mode
        self._cfg.ocr_reader_fast_loop_enabled = (
            bool(fast_loop_enabled)
            if fast_loop_enabled is not None
            else old_fast_loop
        )
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.ocr_reader_poll_interval_seconds = old_interval
                self._cfg.ocr_reader_trigger_mode = old_trigger_mode
                self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
                self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
                return Err(SdkError(f"apply OCR timing failed: {exc}"))

        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_timing(
                poll_interval_seconds=normalized_interval,
                trigger_mode=normalized_trigger_mode,
                fast_loop_enabled=(
                    fast_loop_enabled
                    if fast_loop_enabled is not None
                    else old_fast_loop
                ),
            )
        except Exception as exc:
            self._cfg.ocr_reader_poll_interval_seconds = old_interval
            self._cfg.ocr_reader_trigger_mode = old_trigger_mode
            self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
            self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR timing rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR timing failed: {exc}"))

        if normalized_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            self._clear_pending_ocr_advance_captures()
        if fast_loop_enabled is not None:
            try:
                if bool(fast_loop_enabled) and not old_fast_loop:
                    self._start_ocr_fast_loop()
                elif not bool(fast_loop_enabled) and old_fast_loop:
                    await self._cancel_ocr_fast_loop()
            except Exception as exc:
                self._cfg.ocr_reader_poll_interval_seconds = old_interval
                self._cfg.ocr_reader_trigger_mode = old_trigger_mode
                self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
                self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
                if self._ocr_reader_manager is not None:
                    try:
                        self._ocr_reader_manager.update_config(self._cfg)
                    except Exception as rollback_exc:
                        _log_plugin_noncritical(
                            self.logger,
                            "warning",
                            "galgame OCR timing rollback update_config failed: {}",
                            rollback_exc,
                        )
                try:
                    self._config_service.persist_ocr_timing(
                        poll_interval_seconds=old_interval,
                        trigger_mode=old_trigger_mode,
                        fast_loop_enabled=old_fast_loop,
                    )
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR fast loop rollback persist failed: {}",
                        rollback_exc,
                    )
                return Err(SdkError(f"apply fast_loop_enabled failed: {exc}"))
        await self._ensure_ocr_foreground_advance_monitor()
        self._start_background_bridge_poll()
        trigger_mode_label = (
            "点击对白后识别"
            if self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            else "按间隔识别"
        )
        payload = {
            "poll_interval_seconds": self._cfg.ocr_reader_poll_interval_seconds,
            "trigger_mode": self._cfg.ocr_reader_trigger_mode,
            "fast_loop_enabled": self._cfg.ocr_reader_fast_loop_enabled,
            "summary": (
                f"OCR/DXcam {trigger_mode_label}；间隔="
                f"{self._cfg.ocr_reader_poll_interval_seconds:.1f}s；"
                f"Fast Loop={'开启' if self._cfg.ocr_reader_fast_loop_enabled else '关闭'}"
            ),
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_llm_vision",
        name="设置 LLM 视觉辅助",
        description="切换 OCR Agent 低置信度场景的截图直传，并设置图片最大边长。",
        input_schema={
            "type": "object",
            "properties": {
                "vision_enabled": {"type": "boolean"},
                "vision_max_image_px": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 2048,
                    "default": 768,
                },
            },
            "required": ["vision_enabled"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_llm_vision(
        self,
        vision_enabled: bool,
        vision_max_image_px: int | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        try:
            normalized_max_px = int(
                vision_max_image_px
                if vision_max_image_px is not None
                else self._cfg.llm_vision_max_image_px
            )
        except (TypeError, ValueError):
            return Err(SdkError("vision_max_image_px must be an integer"))
        if normalized_max_px < 64 or normalized_max_px > 2048:
            return Err(SdkError("vision_max_image_px must be between 64 and 2048"))

        old_enabled = self._cfg.llm_vision_enabled
        old_max_px = self._cfg.llm_vision_max_image_px
        self._cfg.llm_vision_enabled = bool(vision_enabled)
        self._cfg.llm_vision_max_image_px = normalized_max_px
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.llm_vision_enabled = old_enabled
                self._cfg.llm_vision_max_image_px = old_max_px
                return Err(SdkError(f"apply LLM vision failed: {exc}"))

        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_llm_vision(
                vision_enabled=self._cfg.llm_vision_enabled,
                vision_max_image_px=self._cfg.llm_vision_max_image_px,
            )
        except Exception as exc:
            self._cfg.llm_vision_enabled = old_enabled
            self._cfg.llm_vision_max_image_px = old_max_px
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame LLM vision rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist LLM vision failed: {exc}"))

        payload = {
            "vision_enabled": self._cfg.llm_vision_enabled,
            "vision_max_image_px": self._cfg.llm_vision_max_image_px,
            "summary": (
                f"LLM vision enabled={self._cfg.llm_vision_enabled} "
                f"max_image_px={self._cfg.llm_vision_max_image_px}"
            ),
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_ocr_screen_templates",
        name="设置 OCR 屏幕模板",
        description="保存 OCR 屏幕分类模板；模板仅影响 OCR Reader，不影响 Bridge SDK / Memory Reader。",
        input_schema={
            "type": "object",
            "properties": {
                "screen_templates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["screen_templates"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_screen_templates(
        self,
        screen_templates: list[dict[str, Any]] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not isinstance(screen_templates, list):
            return Err(SdkError("screen_templates must be an array"))
        sanitized = build_config(
            {"ocr_reader": {"screen_templates": screen_templates}}
        ).ocr_reader_screen_templates
        old_templates = json_copy(self._cfg.ocr_reader_screen_templates)
        self._cfg.ocr_reader_screen_templates = json_copy(sanitized)
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.ocr_reader_screen_templates = old_templates
                return Err(SdkError(f"apply OCR screen templates failed: {exc}"))

        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_screen_templates(
                self._cfg.ocr_reader_screen_templates
            )
        except Exception as exc:
            self._cfg.ocr_reader_screen_templates = old_templates
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR screen template rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR screen templates failed: {exc}"))

        payload = {
            "screen_templates": json_copy(self._cfg.ocr_reader_screen_templates),
            "summary": f"OCR screen templates={len(self._cfg.ocr_reader_screen_templates)}",
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_build_ocr_screen_template_draft",
        name="生成 OCR 屏幕模板草稿",
        description="根据当前 OCR 运行时、窗口信息和最近识别文本生成可编辑的屏幕模板草稿。",
        input_schema={
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_STAGES),
                },
                "region": {"type": "object"},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_build_ocr_screen_template_draft(
        self,
        stage: str | None = None,
        region: dict[str, Any] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        try:
            payload = self._build_ocr_screen_template_draft_payload(
                stage=stage,
                region=region,
            )
        except Exception as exc:
            return Err(SdkError(f"build OCR screen template draft failed: {exc}"))
        payload["summary"] = (
            f"OCR screen template draft stage={payload['template'].get('stage')} "
            f"id={payload['template'].get('id')}"
        )
        return Ok(payload)

    @plugin_entry(
        id="galgame_validate_ocr_screen_templates",
        name="验证 OCR 屏幕模板",
        description="用当前 OCR 运行时和最近文本回放验证屏幕模板命中结果。",
        input_schema={
            "type": "object",
            "properties": {
                "screen_templates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["screen_templates"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_validate_ocr_screen_templates(
        self,
        screen_templates: list[dict[str, Any]] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not isinstance(screen_templates, list):
            return Err(SdkError("screen_templates must be an array"))
        try:
            payload = self._current_ocr_screen_template_validation_payload(screen_templates)
        except Exception as exc:
            return Err(SdkError(f"validate OCR screen templates failed: {exc}"))
        return Ok(payload)

    @plugin_entry(
        id="galgame_get_ocr_screen_awareness_snapshot",
        name="获取 OCR 屏幕感知截图",
        description="返回最近一次 OCR 屏幕感知截图；仅在 Vision 显式开启且短期缓存未过期时可用。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def galgame_get_ocr_screen_awareness_snapshot(self, **_):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        snapshot = self.latest_ocr_vision_snapshot()
        if not isinstance(snapshot, dict) or not snapshot.get("vision_image_base64"):
            return Err(SdkError("no OCR screen awareness snapshot is available; enable Vision and wait for a full-frame OCR capture"))
        payload = {
            "snapshot": snapshot,
            "summary": (
                f"OCR screen awareness snapshot "
                f"{int(snapshot.get('width') or 0)}x{int(snapshot.get('height') or 0)}"
            ),
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_train_ocr_screen_awareness_model",
        name="训练 OCR 屏幕感知模型",
        description="从已标注 JSONL 样本训练轻量原型分类器，并导出可部署 JSON 模型。",
        input_schema={
            "type": "object",
            "properties": {
                "sample_path": {"type": "string", "default": ""},
                "output_path": {"type": "string", "default": "screen_awareness_model.json"},
                "allow_rule_labels": {"type": "boolean", "default": False},
                "validation_ratio": {"type": "number", "default": 0.2},
                "min_samples_per_stage": {"type": "integer", "default": 2},
                "min_confidence": {"type": "number", "default": 0.55},
            },
        },
        timeout=120.0,
        llm_result_fields=["summary"],
    )
    async def galgame_train_ocr_screen_awareness_model(
        self,
        sample_path: str = "",
        output_path: str = "screen_awareness_model.json",
        allow_rule_labels: bool = False,
        validation_ratio: float = 0.2,
        min_samples_per_stage: int = 2,
        min_confidence: float = 0.55,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        try:
            resolved_samples = self._resolve_screen_awareness_data_path(
                sample_path,
                default_filename="samples.jsonl",
            )
            resolved_output = self._resolve_screen_awareness_data_path(
                output_path,
                default_filename="screen_awareness_model.json",
            )
            result = await asyncio.to_thread(
                train_screen_awareness_model,
                resolved_samples,
                resolved_output,
                allow_rule_labels=bool(allow_rule_labels),
                validation_ratio=float(validation_ratio),
                min_samples_per_stage=int(min_samples_per_stage),
                min_confidence=float(min_confidence),
            )
        except Exception as exc:
            return Err(SdkError(f"train OCR screen awareness model failed: {exc}"))
        payload = {
            "output_path": str(result.get("output_path") or resolved_output),
            "evaluation": json_copy(result.get("evaluation") or {}),
            "model": json_copy(result.get("model") or {}),
            "summary": str(result.get("summary") or "OCR screen awareness model trained"),
        }
        return Ok(payload)

    @plugin_entry(
        id="galgame_evaluate_ocr_screen_awareness_model",
        name="评估 OCR 屏幕感知模型",
        description="用已标注 JSONL 样本评估轻量屏幕感知模型，并可输出评估报告。",
        input_schema={
            "type": "object",
            "properties": {
                "sample_path": {"type": "string", "default": ""},
                "model_path": {"type": "string", "default": "screen_awareness_model.json"},
                "report_path": {"type": "string", "default": ""},
                "allow_rule_labels": {"type": "boolean", "default": False},
                "min_confidence": {"type": "number", "default": 0.55},
            },
        },
        timeout=120.0,
        llm_result_fields=["summary"],
    )
    async def galgame_evaluate_ocr_screen_awareness_model(
        self,
        sample_path: str = "",
        model_path: str = "screen_awareness_model.json",
        report_path: str = "",
        allow_rule_labels: bool = False,
        min_confidence: float = 0.55,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        try:
            resolved_samples = self._resolve_screen_awareness_data_path(
                sample_path,
                default_filename="samples.jsonl",
            )
            resolved_model = self._resolve_screen_awareness_data_path(
                model_path,
                default_filename="screen_awareness_model.json",
            )
            resolved_report = (
                self._resolve_screen_awareness_data_path(
                    report_path,
                    default_filename="screen_awareness_evaluation.json",
                )
                if str(report_path or "").strip()
                else None
            )
            result = await asyncio.to_thread(
                evaluate_screen_awareness_model,
                resolved_samples,
                resolved_model,
                allow_rule_labels=bool(allow_rule_labels),
                min_confidence=float(min_confidence),
                report_path=resolved_report,
            )
        except Exception as exc:
            return Err(SdkError(f"evaluate OCR screen awareness model failed: {exc}"))
        return Ok(json_copy(result))

    @plugin_entry(
        id="galgame_bind_game",
        name="绑定 galgame 游戏",
        description="绑定指定 game_id；传空字符串清除手动绑定并恢复自动选择。",
        input_schema={
            "type": "object",
            "properties": {"game_id": {"type": "string", "default": ""}},
            "required": ["game_id"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_bind_game(self, game_id: str, **_):
        normalized = game_id.strip()
        with self._state_lock:
            available_game_ids = list(self._state.available_game_ids)
        if normalized and normalized not in available_game_ids:
            return Err(SdkError(f"unknown game_id: {normalized!r}"))

        with self._state_lock:
            self._state.bound_game_id = normalized
            self._state_dirty = True
            self._cached_snapshot = None
            bound_game_id = self._state.bound_game_id
            mode = self._state.mode
            push_notifications = self._state.push_notifications
            advance_speed = self._state.advance_speed

        try:
            self._config_service.persist_preferences(
                bound_game_id=bound_game_id,
                mode=mode,
                push_notifications=push_notifications,
                advance_speed=advance_speed,
            )
        except Exception as exc:
            return Err(SdkError(f"persist binding failed: {exc}"))

        await self._poll_bridge(force=True)
        with self._state_lock:
            payload = {
                "bound_game_id": self._state.bound_game_id,
                "active_session_id": self._state.active_session_id,
                "summary": f"bound_game_id={self._state.bound_game_id or '(auto)'} active_session_id={self._state.active_session_id}",
            }
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_ocr_capture_profile",
        name="设置 OCR 截图校准",
        description="按进程名保存或清除 OCR Reader 的截图裁剪配置。",
        input_schema={
            "type": "object",
            "properties": {
                "process_name": {"type": "string", "default": ""},
                "stage": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_STAGES),
                    "default": OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
                },
                "save_scope": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_SAVE_SCOPES),
                },
                "left_inset_ratio": {"type": "number", "default": 0.05},
                "right_inset_ratio": {"type": "number", "default": 0.05},
                "top_ratio": {"type": "number", "default": 0.3},
                "bottom_inset_ratio": {"type": "number", "default": 0.3},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_capture_profile(
        self,
        process_name: str = "",
        stage: str = OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
        left_inset_ratio: float = 0.05,
        right_inset_ratio: float = 0.05,
        top_ratio: float = 0.3,
        bottom_inset_ratio: float = 0.3,
        save_scope: str | None = None,
        clear: bool = False,
        **_,
    ):
        def _parse_ratio(name: str, value: float) -> float:
            if isinstance(value, bool):
                raise ValueError(f"{name} must be a number")
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a number") from exc
            if parsed < 0.0 or parsed >= 1.0:
                raise ValueError(f"{name} must be >= 0.0 and < 1.0")
            return parsed

        with self._state_lock:
            runtime_process_name = str(
                (self._state.ocr_reader_runtime or {}).get("process_name") or ""
            ).strip()
        normalized_process_name = str(process_name or "").strip() or runtime_process_name
        if not normalized_process_name:
            return Err(SdkError("process_name is required"))

        if clear:
            normalized_profile: dict[str, float] | None = None
        else:
            try:
                normalized_profile = {
                    "left_inset_ratio": _parse_ratio("left_inset_ratio", left_inset_ratio),
                    "right_inset_ratio": _parse_ratio("right_inset_ratio", right_inset_ratio),
                    "top_ratio": _parse_ratio("top_ratio", top_ratio),
                    "bottom_inset_ratio": _parse_ratio(
                        "bottom_inset_ratio",
                        bottom_inset_ratio,
                    ),
                }
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            if (
                normalized_profile["left_inset_ratio"]
                + normalized_profile["right_inset_ratio"]
            ) >= 1.0:
                return Err(SdkError("left_inset_ratio + right_inset_ratio must be < 1.0"))
            if (
                normalized_profile["top_ratio"]
                + normalized_profile["bottom_inset_ratio"]
            ) >= 1.0:
                return Err(SdkError("top_ratio + bottom_inset_ratio must be < 1.0"))
        try:
            payload = await self._save_ocr_capture_profile_payload(
                process_name=normalized_process_name,
                stage=stage,
                capture_profile=normalized_profile,
                clear=bool(clear),
                save_scope=save_scope,
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:
            return Err(SdkError(f"persist OCR capture profile failed: {exc}"))
        return Ok(payload)

    @plugin_entry(
        id="galgame_auto_recalibrate_ocr_dialogue_profile",
        name="自动重新校准 OCR 对白区",
        description="对当前已附着 OCR 目标窗口自动重校准对白区，并保存到当前窗口分辨率。",
        input_schema={"type": "object", "properties": {}},
        timeout=120.0,
        llm_result_fields=["summary", "sample_text"],
    )
    async def galgame_auto_recalibrate_ocr_dialogue_profile(self, **_):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))
        try:
            recalibrated = await asyncio.to_thread(
                self._ocr_reader_manager.auto_recalibrate_dialogue_profile
            )
            payload = await self._save_ocr_capture_profile_payload(
                process_name=str(recalibrated.get("process_name") or ""),
                stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
                capture_profile=dict(recalibrated.get("capture_profile") or {}),
                clear=False,
                save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
                width=int(recalibrated.get("window_width") or 0),
                height=int(recalibrated.get("window_height") or 0),
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:
            return Err(SdkError(f"auto recalibrate OCR dialogue profile failed: {exc}"))
        payload.update(
            {
                "sample_text": str(recalibrated.get("sample_text") or ""),
                "save_scope": OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
                "bucket_key": str(recalibrated.get("bucket_key") or payload.get("bucket_key") or ""),
                "window_width": int(
                    recalibrated.get("window_width") or payload.get("window_width") or 0
                ),
                "window_height": int(
                    recalibrated.get("window_height") or payload.get("window_height") or 0
                ),
                "summary": str(recalibrated.get("summary") or payload.get("summary") or ""),
            }
        )
        return Ok(payload)

    @plugin_entry(
        id="galgame_apply_recommended_ocr_capture_profile",
        name="应用推荐 OCR 截图校准",
        description="在用户确认后应用当前 OCR Reader 推荐的截图 profile，并记录回滚点。",
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "default": False},
                "enable_auto_apply": {"type": "boolean", "default": False},
                "allow_manual_override": {"type": "boolean", "default": False},
            },
            "required": ["confirm"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_apply_recommended_ocr_capture_profile(
        self,
        confirm: bool = False,
        enable_auto_apply: bool = False,
        allow_manual_override: bool = False,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not bool(confirm):
            return Err(SdkError("confirm=true is required before applying a recommended OCR profile"))
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        self._ocr_capture_profile_auto_apply_enabled = bool(enable_auto_apply)
        try:
            payload = await self._apply_recommended_ocr_capture_profile_payload(
                runtime,
                allow_manual_override=bool(allow_manual_override),
                reason="manual_apply_recommended_capture_profile",
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:
            return Err(SdkError(f"apply recommended OCR capture profile failed: {exc}"))
        return Ok(payload)

    @plugin_entry(
        id="galgame_rollback_ocr_capture_profile",
        name="回滚 OCR 推荐截图校准",
        description="回滚最近一次由推荐 profile 应用产生的 OCR 截图校准。",
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["confirm"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_rollback_ocr_capture_profile(
        self,
        confirm: bool = False,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError("galgame_plugin is not configured"))
        if not bool(confirm):
            return Err(SdkError("confirm=true is required before rolling back OCR profile"))
        try:
            payload = await self._rollback_pending_ocr_capture_profile(
                reason="manual_rollback_recommended_capture_profile"
            )
        except Exception as exc:
            return Err(SdkError(f"rollback OCR capture profile failed: {exc}"))
        if payload is None:
            return Err(SdkError("no pending recommended OCR capture profile rollback"))
        return Ok(payload)

    @plugin_entry(
        id="galgame_list_memory_reader_processes",
        name="列出 Memory Reader 候选进程",
        description="返回 Memory Reader 可选进程，包含 exe 路径、检测到的引擎和识别原因。",
        input_schema={
            "type": "object",
            "properties": {
                "include_unknown": {"type": "boolean", "default": True},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_list_memory_reader_processes(
        self,
        include_unknown: bool = True,
        **_,
    ):
        if self._memory_reader_manager is None:
            return Err(SdkError("memory_reader manager is not initialized"))
        try:
            payload = await asyncio.to_thread(
                self._memory_reader_manager.list_processes_snapshot,
                include_unknown=bool(include_unknown),
            )
        except Exception as exc:
            return Err(SdkError(f"list Memory Reader processes failed: {exc}"))
        payload["summary"] = (
            f"processes={int(payload.get('candidate_count') or 0)} "
            f"mode={payload.get('target_selection_mode') or 'auto'}"
        )
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_memory_reader_target",
        name="设置 Memory Reader 目标进程",
        description="锁定或清除 Memory Reader 的手动进程目标。",
        input_schema={
            "type": "object",
            "properties": {
                "process_key": {"type": "string", "default": ""},
                "pid": {"type": "integer", "default": 0},
                "exe_path": {"type": "string", "default": ""},
                "process_name": {"type": "string", "default": ""},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_memory_reader_target(
        self,
        process_key: str = "",
        pid: int = 0,
        exe_path: str = "",
        process_name: str = "",
        clear: bool = False,
        **_,
    ):
        if self._memory_reader_manager is None:
            return Err(SdkError("memory_reader manager is not initialized"))

        if clear:
            target_payload = {
                "mode": "auto",
                "process_key": "",
                "process_name": "",
                "exe_path": "",
                "pid": 0,
                "engine": "",
                "detected_engine": "",
                "detection_reason": "",
                "create_time": 0.0,
                "selected_at": "",
            }
            summary = "Memory Reader process target cleared; using auto detection"
        else:
            try:
                target_payload = await asyncio.to_thread(
                    self._memory_reader_manager.resolve_manual_process_target,
                    process_key=process_key,
                    pid=pid,
                    exe_path=exe_path,
                    process_name=process_name,
                )
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            except Exception as exc:
                return Err(SdkError(f"resolve Memory Reader process target failed: {exc}"))
            summary = (
                f"Memory Reader target locked to {target_payload.get('process_name') or '(unknown)'}"
            )

        try:
            self._persist.persist_memory_reader_target(target_payload)
        except Exception as exc:
            return Err(SdkError(f"persist Memory Reader target failed: {exc}"))

        self._memory_reader_manager.update_process_target(target_payload)
        with self._state_lock:
            self._state.memory_reader_target = json_copy(target_payload)
            self._state_dirty = True
            self._cached_snapshot = None
        background_poll_started = self._start_background_bridge_poll()
        return Ok(
            {
                "process_target": json_copy(target_payload),
                "cleared": bool(clear),
                "summary": summary,
                "background_poll_started": background_poll_started,
            }
        )

    @plugin_entry(
        id="galgame_list_ocr_windows",
        name="列出 OCR 候选窗口",
        description="返回当前 OCR Reader 的可选窗口，可选包含只读排除列表。",
        input_schema={
            "type": "object",
            "properties": {
                "include_excluded": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_list_ocr_windows(
        self,
        include_excluded: bool = False,
        force: bool = False,
        **_,
    ):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))
        try:
            payload = await asyncio.to_thread(
                self._ocr_reader_manager.list_windows_snapshot,
                include_excluded=bool(include_excluded),
                force=bool(force),
            )
        except Exception as exc:
            return Err(SdkError(f"list OCR windows failed: {exc}"))
        payload["summary"] = (
            f"eligible={int(payload.get('candidate_count') or 0)} "
            f"excluded={int(payload.get('excluded_candidate_count') or 0)} "
            f"mode={payload.get('target_selection_mode') or 'auto'}"
        )
        return Ok(payload)

    @plugin_entry(
        id="galgame_set_ocr_window_target",
        name="设置 OCR 目标窗口",
        description="锁定或清除 OCR Reader 的手动目标窗口。",
        input_schema={
            "type": "object",
            "properties": {
                "window_key": {"type": "string", "default": ""},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_window_target(
        self,
        window_key: str = "",
        clear: bool = False,
        **_,
    ):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))

        if clear:
            target_payload = {
                "mode": "auto",
                "window_key": "",
                "process_name": "",
                "normalized_title": "",
                "pid": 0,
                "last_known_hwnd": 0,
                "selected_at": "",
            }
            summary = "OCR window target cleared; waiting for manual lock"
        else:
            try:
                target_payload = await asyncio.to_thread(
                    self._ocr_reader_manager.resolve_manual_window_target,
                    window_key,
                )
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            except Exception as exc:
                return Err(SdkError(f"resolve OCR window target failed: {exc}"))
            summary = (
                f"OCR window target locked to {target_payload.get('process_name') or '(unknown)'}"
            )

        try:
            self._persist.persist_ocr_window_target(target_payload)
        except Exception as exc:
            return Err(SdkError(f"persist OCR window target failed: {exc}"))

        with self._state_lock:
            self._state.ocr_window_target = json_copy(target_payload)
            self._state_dirty = True
            self._cached_snapshot = None
        self._ocr_reader_manager.update_window_target(target_payload)
        background_poll_started = self._start_background_bridge_poll()
        return Ok(
            {
                "window_target": json_copy(target_payload),
                "cleared": bool(clear),
                "summary": summary,
                "background_poll_started": background_poll_started,
            }
        )

    @plugin_entry(
        id="galgame_open_ui",
        name="打开 galgame UI",
        description="返回 galgame_plugin 静态 UI 的访问路径。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["message"],
    )
    async def galgame_open_ui(self, **_):
        payload = build_open_ui_payload(
            plugin_id=self.plugin_id,
            available=self.get_static_ui_config() is not None,
        )
        return Ok(payload)

    @plugin_entry(
        id="galgame_explain_line",
        name="解释当前或指定台词",
        description="对当前快照或指定 line_id 对应的台词进行解释。",
        input_schema={
            "type": "object",
            "properties": {"line_id": {"type": "string", "default": ""}},
        },
        timeout=45.0,
        llm_result_fields=["explanation", "diagnostic"],
    )
    async def galgame_explain_line(self, line_id: str = "", **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state()
        try:
            context = build_explain_context(local, line_id=line_id.strip())
        except ValueError as exc:
            context = {
                "line_id": "",
                "speaker": "",
                "text": "",
                "scene_id": "",
                "route_id": "",
                "evidence": [],
            }
            return Ok(
                build_explain_degraded_result(
                    context,
                    diagnostic=str(exc) or build_ocr_context_diagnostic(local),
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.explain_line(context),
            context=context,
        )
        payload["line_id"] = str(context.get("line_id") or "")
        payload["speaker"] = str(context.get("speaker") or "")
        payload["text"] = str(context.get("text") or "")
        return Ok(payload)

    @plugin_entry(
        id="galgame_summarize_scene",
        name="总结当前场景",
        description="总结当前场景或指定 scene_id 的最近剧情进展。",
        input_schema={
            "type": "object",
            "properties": {"scene_id": {"type": "string", "default": ""}},
        },
        timeout=45.0,
        llm_result_fields=["summary", "diagnostic"],
    )
    async def galgame_summarize_scene(self, scene_id: str = "", **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state()
        context = build_summarize_context(local, scene_id=scene_id.strip())
        snapshot = context.get("current_snapshot") if isinstance(context.get("current_snapshot"), dict) else {}
        if not list(context.get("recent_lines") or []) and not str(snapshot.get("text") or ""):
            return Ok(
                build_summarize_degraded_result(
                    context,
                    diagnostic=build_ocr_context_diagnostic(local),
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.summarize_scene(context),
            context=context,
        )
        payload["scene_id"] = str(context.get("scene_id") or "")
        return Ok(payload)

    @plugin_entry(
        id="galgame_suggest_choice",
        name="建议当前选项",
        description="对当前可见选项给出推荐顺位与理由。",
        input_schema={"type": "object", "properties": {}},
        timeout=45.0,
        llm_result_fields=["choices", "diagnostic"],
    )
    async def galgame_suggest_choice(self, **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state()
        context = build_suggest_context(local)
        if not context["visible_choices"]:
            return Ok(
                apply_input_degraded_result(
                    build_suggest_degraded_result(
                        context,
                        diagnostic="gateway_unavailable: no visible choices",
                    ),
                    context=context,
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.suggest_choice(context),
            context=context,
        )
        payload["scene_id"] = str(context.get("scene_id") or "")
        return Ok(payload)

    @plugin_entry(
        id="galgame_agent_command",
        name="向 Game LLM Agent 发送指令",
        description="查询 Agent 状态、上下文、发送消息或控制待机。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "query_status",
                        "query_context",
                        "send_message",
                        "set_standby",
                        "list_messages",
                        "ack_message",
                    ],
                },
                "message": {"type": "string", "default": ""},
                "context_query": {"type": "string", "default": ""},
                "message_id": {"type": "string", "default": ""},
                "direction": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 50},
                "standby": {"type": "boolean"},
            },
            "required": ["action"],
        },
        timeout=45.0,
        llm_result_fields=["result", "status"],
    )
    async def galgame_agent_command(
        self,
        action: str,
        message: str = "",
        context_query: str = "",
        message_id: str = "",
        direction: str = "",
        limit: int = 50,
        standby: bool | None = None,
        **_,
    ):
        if self._game_agent is None:
            return Err(SdkError("galgame_plugin game agent is not initialized"))
        local = self._snapshot_state()
        if action == "query_status":
            return Ok(await self._game_agent.query_status(local))
        if action == "query_context":
            if not context_query.strip():
                return Err(SdkError("context_query is required for query_context"))
            return Ok(
                await self._game_agent.query_context(
                    local,
                    context_query=context_query.strip(),
                )
            )
        if action == "send_message":
            if not message.strip():
                return Err(SdkError("message is required for send_message"))
            return Ok(
                await self._game_agent.send_message(
                    local,
                    message=message.strip(),
                )
            )
        if action == "set_standby":
            if standby is None:
                return Err(SdkError("standby is required for set_standby"))
            return Ok(await self._game_agent.set_standby(local, standby=bool(standby)))
        if action == "list_messages":
            return Ok(
                await self._game_agent.list_messages(
                    local,
                    direction=direction,
                    limit=int(limit or 50),
                )
            )
        if action == "ack_message":
            if not message_id.strip():
                return Err(SdkError("message_id is required for ack_message"))
            return Ok(
                await self._game_agent.ack_message(
                    local,
                    message_id=message_id.strip(),
                )
            )
        return Err(SdkError(f"unsupported agent action: {action!r}"))

    @plugin_entry(
        id="galgame_continue_auto_advance",
        name="继续自动推进 galgame 剧情",
        description="切换到自动推进模式，并向 Game LLM Agent 发送继续推进消息。",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "default": "继续推进剧情"},
            },
        },
        timeout=45.0,
        llm_result_fields=[
            "result",
            "status",
            "mode",
            "mode_result",
            "agent_result",
            "diagnostic",
        ],
    )
    async def galgame_continue_auto_advance(
        self,
        message: str = "继续推进剧情",
        **_,
    ):
        normalized_message = str(message or "").strip() or "继续推进剧情"
        mode_res = await self.galgame_set_mode(
            mode="choice_advisor",
            push_notifications=True,
        )
        if isinstance(mode_res, Err):
            return mode_res
        mode_payload = json_copy(mode_res.value or {})

        agent_res = await self.galgame_agent_command(
            action="send_message",
            message=normalized_message,
        )
        if isinstance(agent_res, Err):
            return Err(
                SdkError(
                    f"continue auto advance send_message failed: {agent_res.error}",
                    details={
                        "mode_result": mode_payload,
                        "message": normalized_message,
                    },
                    mode_result=mode_payload,
                )
            )
        agent_payload = json_copy(agent_res.value or {})
        status = (
            json_copy(agent_payload.get("status"))
            if isinstance(agent_payload, dict)
            else {}
        )
        result_text = (
            str(agent_payload.get("result") or "")
            if isinstance(agent_payload, dict)
            else ""
        )
        diagnostic = (
            str(agent_payload.get("diagnostic") or "")
            if isinstance(agent_payload, dict)
            else ""
        )
        return Ok(
            {
                "action": "continue_auto_advance",
                "message": normalized_message,
                "mode": "choice_advisor",
                "mode_result": {
                    "success": True,
                    "mode": "choice_advisor",
                    "push_notifications": True,
                    "result": mode_payload,
                },
                "agent_result": agent_payload,
                "status": status,
                "result": result_text,
                "degraded": False,
                "diagnostic": diagnostic,
            }
        )


GalgameBridgePlugin = GalgamePlugin
