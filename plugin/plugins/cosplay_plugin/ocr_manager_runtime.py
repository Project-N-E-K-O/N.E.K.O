from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Protocol
from uuid import uuid4

from .models import (
    ADVANCE_SPEED_FAST,
    ADVANCE_SPEED_MEDIUM,
    ADVANCE_SPEED_SLOW,
    ADVANCE_SPEEDS,
    DATA_SOURCE_OCR_READER,
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    GalgameConfig,
    MENU_PREFIX_RE as _MENU_PREFIX_RE,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_RATIO_KEYS,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    READER_MODE_AUTO,
    READER_MODE_MEMORY,
    build_ocr_capture_profile_bucket_key,
    compute_ocr_window_aspect_ratio,
    json_copy,
    sanitize_screen_ui_elements,
    parse_ocr_capture_profile_bucket_key,
)
from .ocr_chrome_noise import (
    looks_like_temperature_status_line as _looks_like_temperature_status_line,
    looks_like_window_title_line as _looks_like_window_title_line,
)
from .aihong_state import (
    AIHONG_CHOICES_REGION_PRESET as _AIHONG_CHOICES_REGION_PRESET,
    AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET as _AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET,
    AIHONG_DIALOGUE_STAGE as _AIHONG_DIALOGUE_STAGE,
    AIHONG_MENU_CAPTURE_PROFILE_PRESET as _AIHONG_MENU_CAPTURE_PROFILE_PRESET,
    AIHONG_MENU_MAX_LINES as _AIHONG_MENU_MAX_LINES,
    AIHONG_MENU_MAX_SIGNIFICANT_CHARS as _AIHONG_MENU_MAX_SIGNIFICANT_CHARS,
    AIHONG_MENU_STAGE as _AIHONG_MENU_STAGE,
    coerce_aihong_menu_choices as _coerce_aihong_menu_choices,
    levenshtein_distance as _levenshtein_distance,
    looks_like_aihong_menu_status_only_text as _looks_like_aihong_menu_status_only_text,
    matches_aihong_target as _matches_aihong_target_info,
    normalize_aihong_choice_box_text as _normalize_aihong_choice_box_text,
)
from plugin.plugins._shared.rapidocr.rapidocr_support import (
    inspect_rapidocr_installation,
    load_rapidocr_runtime,
)
from .reader import normalize_text
from .screen_classifier import (
    ScreenClassification,
    classify_screen_awareness_model,
    classify_screen_from_ocr,
    normalize_screen_type,
)
from .screen_classifier import analyze_screen_visual_features

try:
    from PIL import Image as _PIL_IMAGE_MODULE

    _PIL_RESAMPLING = getattr(_PIL_IMAGE_MODULE, "Resampling", None)
except ImportError:  # pragma: no cover - optional in non-visual test environments.
    _PIL_RESAMPLING = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .ocr_runtime_types import *
from .ocr_backend_interface import *

from .ocr_capture_backends import *

from .ocr_rapidocr_backend import *
from .ocr_input_hooks import *
from .ocr_bridge_writer import *
from . import ocr_reader as _ocr_reader_module
from .ocr_reader import (
    _foreground_matches_target,
)

def _foreground_window_handle() -> int:
    return _ocr_reader_module._foreground_window_handle()


class RuntimeMixin:
    """Runtime 状态聚合、to_dict、backend plan、screen awareness"""

    def runtime(self) -> dict[str, Any]:
        return self._runtime.to_dict()


    def refresh_runtime_capture_profile_selection(self) -> dict[str, Any]:
        target = self._attached_window
        if target is None:
            return self._runtime.to_dict()

        if target.width <= 0 and self._runtime.width > 0:
            target.width = int(self._runtime.width)
        if target.height <= 0 and self._runtime.height > 0:
            target.height = int(self._runtime.height)
        resolved_aspect_ratio = float(target.aspect_ratio or self._runtime.aspect_ratio)
        if resolved_aspect_ratio <= 0.0 and target.width > 0 and target.height > 0:
            resolved_aspect_ratio = compute_ocr_window_aspect_ratio(target.width, target.height)

        capture_stage = str(self._runtime.capture_stage or "").strip().lower()
        if not capture_stage or capture_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            capture_stage = (
                self._aihong_stage
                if self._should_use_aihong_two_stage(target)
                else OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            )
        capture_profile_selection = self._capture_profile_selection_for_target(
            target,
            stage=capture_stage,
        )
        self._runtime.process_name = str(target.process_name or self._runtime.process_name)
        self._runtime.pid = int(target.pid or self._runtime.pid)
        self._runtime.window_title = str(target.title or self._runtime.window_title)
        self._runtime.width = int(target.width or self._runtime.width)
        self._runtime.height = int(target.height or self._runtime.height)
        self._runtime.aspect_ratio = resolved_aspect_ratio
        self._runtime.capture_stage = capture_stage
        self._runtime.capture_profile = capture_profile_selection.profile.to_dict()
        self._runtime.capture_profile_match_source = capture_profile_selection.match_source
        self._runtime.capture_profile_bucket_key = capture_profile_selection.bucket_key
        self._runtime.consecutive_no_text_polls = max(0, int(self._consecutive_no_text_polls or 0))
        self._runtime.last_observed_at = str(self._last_observed_at or self._runtime.last_observed_at)
        self._runtime.last_capture_stage = capture_stage
        self._runtime.last_capture_profile = capture_profile_selection.profile.to_dict()
        self._runtime.ocr_capture_diagnostic_required = self._ocr_capture_diagnostic_required()
        self._runtime.ocr_context_state = self._ocr_context_state_for_detail(
            status=self._runtime.status,
            detail=self._runtime.detail,
        )
        self._runtime.last_capture_attempt_at = str(
            self._last_capture_attempt_at or self._runtime.last_capture_attempt_at
        )
        self._runtime.last_capture_completed_at = str(
            self._last_capture_completed_at or self._runtime.last_capture_completed_at
        )
        self._runtime.last_capture_error = str(
            self._last_capture_error or self._runtime.last_capture_error
        )
        self._runtime.last_raw_ocr_text = str(
            self._last_raw_ocr_text or self._runtime.last_raw_ocr_text
        )
        self._runtime.last_rejected_ocr_text = str(
            self._last_rejected_ocr_text or self._runtime.last_rejected_ocr_text
        )
        self._runtime.last_rejected_ocr_reason = str(
            self._last_rejected_ocr_reason or self._runtime.last_rejected_ocr_reason
        )
        self._runtime.last_rejected_ocr_at = str(
            self._last_rejected_ocr_at or self._runtime.last_rejected_ocr_at
        )
        self._runtime.last_rejected_capture_backend = str(
            self._last_rejected_capture_backend
            or self._runtime.last_rejected_capture_backend
        )
        self._runtime.last_observed_line = dict(
            self._last_observed_line or self._runtime.last_observed_line
        )
        self._runtime.last_stable_line = dict(
            self._last_stable_line or self._runtime.last_stable_line
        )
        self._runtime.effective_window_key = str(target.window_key or self._runtime.effective_window_key)
        self._runtime.effective_window_title = str(target.title or self._runtime.effective_window_title)
        self._runtime.effective_process_name = str(
            target.process_name or self._runtime.effective_process_name
        )
        foreground_hwnd = _foreground_window_handle()
        self._runtime.target_is_foreground = _foreground_matches_target(
            foreground_hwnd,
            target,
        )[0]
        self._runtime.foreground_hwnd = max(0, int(foreground_hwnd or 0))
        self._runtime.target_hwnd = max(0, int(target.hwnd or 0))
        return self._runtime.to_dict()


    @staticmethod
    def _backend_plan_config_key(config: GalgameConfig) -> tuple[str, ...]:
        return (
            str(config.ocr_reader_backend_selection or "").strip().lower(),
            str(config.ocr_reader_install_target_dir or "").strip(),
            str(config.ocr_reader_languages or "").strip().lower(),
            str(bool(config.rapidocr_enabled)),
            str(bool(getattr(config, "rapidocr_auto_detect_lang", False))),
            *_rapidocr_runtime_cache_key(
                install_target_dir_raw=config.rapidocr_install_target_dir,
                engine_type=config.rapidocr_engine_type,
                lang_type=config.rapidocr_lang_type,
                model_type=config.rapidocr_model_type,
                ocr_version=config.rapidocr_ocr_version,
            ),
        )


    def _resolve_backend_plan(self) -> SelectedOcrBackendPlan:
        now = self._time_fn()
        cache_key = self._backend_plan_config_key(self._config)
        if (
            self._backend_plan_cache_key == cache_key
            and self._backend_plan_cache is not None
            and now - float(self._backend_plan_cache_at or 0.0) < _BACKEND_PLAN_CACHE_TTL_SECONDS
        ):
            return self._backend_plan_cache
        selection = self._configured_backend_selection()
        rapidocr_inspection = _ocr_reader_module.inspect_rapidocr_installation(
            install_target_dir_raw=self._config.rapidocr_install_target_dir,
            engine_type=self._config.rapidocr_engine_type,
            lang_type=self._config.rapidocr_lang_type,
            model_type=self._config.rapidocr_model_type,
            ocr_version=self._config.rapidocr_ocr_version,
        )
        rapidocr = self._rapidocr_descriptor(
            rapidocr_inspection,
            enabled=bool(self._config.rapidocr_enabled),
        )
        plan = SelectedOcrBackendPlan(
            selection=selection,
            rapidocr_inspection=rapidocr_inspection,
        )

        if rapidocr.available:
            rapidocr.detail = "selected_primary"
        plan.primary = rapidocr
        self._backend_plan_cache_key = cache_key
        self._backend_plan_cache_at = now
        self._backend_plan_cache = plan
        return plan


    def _custom_ocr_backend_plan(self) -> SelectedOcrBackendPlan:
        backend = self._ocr_backend
        kind = str(
            self._runtime.backend_kind
            or getattr(backend, "kind", "")
            or backend.__class__.__name__
            or "custom"
        )
        detail = str(
            self._runtime.backend_detail
            or getattr(backend, "detail", "")
            or "custom_backend"
        )
        available = False
        try:
            is_available = getattr(backend, "is_available", None)
            if callable(is_available):
                available = bool(is_available())
            else:
                availability = getattr(backend, "availability", None)
                available = bool(availability() if callable(availability) else availability)
        except Exception as exc:  # noqa: BLE001 - custom backend status must not crash preflight
            detail = f"availability_error:{type(exc).__name__}"
            available = False
        if not available and detail == "custom_backend":
            detail = "custom_backend_unavailable"
        return SelectedOcrBackendPlan(
            selection="custom",
            primary=OcrBackendDescriptor(
                kind=kind,
                backend=backend,
                detail=detail,
                available=available,
            ),
        )


    def _backend_unavailable_detail(self, plan: SelectedOcrBackendPlan) -> str:
        if plan.primary.kind == "rapidocr":
            return plan.primary.detail or "missing"
        return plan.primary.detail or f"{plan.primary.kind or 'custom'}_unavailable"


    def _backend_unavailable_warnings(self, plan: SelectedOcrBackendPlan) -> list[str]:
        warnings: list[str] = []
        if plan.selection == "rapidocr" or plan.primary.kind == "rapidocr":
            warnings.append(f"ocr_reader RapidOCR is unavailable: {plan.primary.detail or 'missing'}")
            return warnings
        if plan.primary.kind:
            warnings.append(
                f"ocr_reader {plan.primary.kind} is unavailable: {plan.primary.detail or 'unavailable'}"
            )
            return warnings
        rapid_detail = str(plan.rapidocr_inspection.get("detail") or "")
        if rapid_detail and rapid_detail != "installed":
            warnings.append(f"ocr_reader RapidOCR status: {rapid_detail}")
        else:
            warnings.append("ocr_reader RapidOCR is missing or not configured")
        return warnings


    def _build_runtime(
        self,
        *,
        status: str,
        detail: str,
        plan: SelectedOcrBackendPlan,
        active_backend: OcrBackendDescriptor | None = None,
        backend_detail_override: str = "",
        target: DetectedGameWindow | None = None,
        capture_stage: str = "",
        capture_profile: dict[str, float] | None = None,
        capture_profile_selection: ResolvedOcrCaptureSelection | None = None,
        selection: WindowSelectionResult | None = None,
        takeover_reason: str = "",
        game_id: str = "",
        session_id: str = "",
        last_seq: int | None = None,
        last_event_ts: str = "",
    ) -> OcrReaderRuntime:
        backend = active_backend if active_backend and active_backend.kind else plan.primary
        attached_target = target or self._attached_window
        selection_state = selection or self._last_selection
        effective_target = selection_state.target or attached_target
        manual_target = (
            selection_state.manual_target.to_dict()
            if isinstance(selection_state.manual_target, OcrWindowTarget)
            else self._manual_target.to_dict()
        )
        resolved_last_seq = (
            int(last_seq)
            if last_seq is not None
            else int(self._writer.last_seq or self._runtime.last_seq)
        )
        foreground_advance_enabled = self._foreground_advance_monitor_enabled()
        foreground_advance_last_seq = (
            max(
                int(self._wheel_monitor.last_seq() or 0),
                int(self._runtime.foreground_advance_last_seq or 0),
            )
            if foreground_advance_enabled
            else 0
        )
        capture_timing = dict(self._last_capture_timing)
        vision_snapshot = self._vision_snapshot_runtime_status()
        recommendation = dict(self._recommended_capture_profile or {})
        target_is_foreground = (
            bool(effective_target.is_foreground) if effective_target is not None else False
        )
        (
            target_window_visible,
            target_window_minimized,
            ocr_window_capture_eligible,
            ocr_window_capture_block_reason,
        ) = _target_window_capture_state(effective_target)
        last_capture_error = str(self._last_capture_error or self._runtime.last_capture_error)
        last_raw_ocr_text = str(self._last_raw_ocr_text or self._runtime.last_raw_ocr_text)
        last_stable_line = dict(self._last_stable_line or self._runtime.last_stable_line)
        has_recent_capture_result = bool(
            self._last_capture_completed_at
            or self._runtime.last_capture_completed_at
            or last_raw_ocr_text
            or str(last_stable_line.get("text") or "")
        )
        stale_capture_backend = bool(
            self._stale_capture_backend or self._runtime.stale_capture_backend
        )
        if ocr_window_capture_eligible and stale_capture_backend:
            ocr_window_capture_block_reason = "stale_capture_backend"
        elif ocr_window_capture_eligible and last_capture_error:
            ocr_window_capture_block_reason = "capture_failed"
        ocr_window_capture_available = bool(
            ocr_window_capture_eligible
            and has_recent_capture_result
            and not last_capture_error
            and not stale_capture_backend
        )
        input_target_block_reason = (
            ""
            if target_is_foreground
            else (
                "target_missing"
                if effective_target is None or not int(effective_target.hwnd or 0)
                else "target_not_foreground"
            )
        )

        def _timing_float(key: str, fallback: float) -> float:
            if key in capture_timing:
                return float(capture_timing.get(key) or 0.0)
            return float(fallback or 0.0)

        return OcrReaderRuntime(
            enabled=bool(self._config.ocr_reader_enabled),
            status=status,
            detail=detail,
            process_name=str((attached_target.process_name if attached_target is not None else self._runtime.process_name) or ""),
            pid=int((attached_target.pid if attached_target is not None else self._runtime.pid) or 0),
            window_title=str((attached_target.title if attached_target is not None else self._runtime.window_title) or ""),
            width=int((attached_target.width if attached_target is not None else self._runtime.width) or 0),
            height=int((attached_target.height if attached_target is not None else self._runtime.height) or 0),
            aspect_ratio=float(
                (
                    attached_target.aspect_ratio
                    if attached_target is not None
                    else self._runtime.aspect_ratio
                )
                or 0.0
            ),
            game_id=str(game_id or self._writer.game_id or self._runtime.game_id),
            session_id=str(session_id or self._writer.session_id or self._runtime.session_id),
            last_seq=resolved_last_seq,
            last_event_ts=str(last_event_ts or self._writer.last_event_ts or self._runtime.last_event_ts),
            capture_stage=str(capture_stage or self._runtime.capture_stage),
            capture_profile=dict(capture_profile or self._runtime.capture_profile),
            capture_profile_match_source=str(
                (
                    capture_profile_selection.match_source
                    if capture_profile_selection is not None
                    else self._runtime.capture_profile_match_source
                )
                or ""
            ),
            capture_profile_bucket_key=str(
                (
                    capture_profile_selection.bucket_key
                    if capture_profile_selection is not None
                    else self._runtime.capture_profile_bucket_key
                )
                or ""
            ),
            recommended_capture_profile=dict(recommendation.get("capture_profile") or {}),
            recommended_capture_profile_process_name=str(recommendation.get("process_name") or ""),
            recommended_capture_profile_stage=str(recommendation.get("stage") or ""),
            recommended_capture_profile_save_scope=str(recommendation.get("save_scope") or ""),
            recommended_capture_profile_reason=str(recommendation.get("reason") or ""),
            recommended_capture_profile_confidence=float(recommendation.get("confidence") or 0.0),
            recommended_capture_profile_sample_text=str(recommendation.get("sample_text") or ""),
            recommended_capture_profile_bucket_key=str(recommendation.get("bucket_key") or ""),
            recommended_capture_profile_manual_present=bool(
                recommendation.get("manual_profile_present")
            ),
            languages=self._config.ocr_reader_languages,
            takeover_reason=takeover_reason or self._runtime.takeover_reason,
            backend_kind=str(backend.kind or ""),
            backend_detail=str(backend_detail_override or backend.detail or ""),
            backend_path=str(backend.path or ""),
            backend_model=str(backend.model or ""),
            target_selection_mode=str(selection_state.selection_mode or self._manual_target.mode or "auto"),
            target_selection_detail=str(selection_state.selection_detail or self._runtime.target_selection_detail),
            effective_window_key=str(effective_target.window_key if effective_target is not None else ""),
            effective_window_title=str(effective_target.title if effective_target is not None else ""),
            effective_process_name=str(effective_target.process_name if effective_target is not None else ""),
            target_is_foreground=target_is_foreground,
            target_window_visible=target_window_visible,
            target_window_minimized=target_window_minimized,
            ocr_window_capture_eligible=ocr_window_capture_eligible,
            ocr_window_capture_available=ocr_window_capture_available,
            ocr_window_capture_block_reason=ocr_window_capture_block_reason,
            input_target_foreground=target_is_foreground,
            input_target_block_reason=input_target_block_reason,
            manual_target=manual_target,
            locked_target=self._locked_target.to_dict() if self._has_locked_target() else {},
            candidate_count=max(0, int(selection_state.candidate_count or 0)),
            excluded_candidate_count=max(0, int(selection_state.excluded_candidate_count or 0)),
            last_exclude_reason=str(selection_state.last_exclude_reason or self._runtime.last_exclude_reason),
            consecutive_no_text_polls=max(0, int(self._consecutive_no_text_polls or 0)),
            last_observed_at=str(self._last_observed_at or self._runtime.last_observed_at),
            last_capture_profile=dict(capture_profile or self._runtime.capture_profile),
            last_capture_stage=str(capture_stage or self._runtime.capture_stage),
            ocr_capture_diagnostic_required=self._ocr_capture_diagnostic_required(),
            ocr_context_state=self._ocr_context_state_for_detail(status=status, detail=detail),
            last_capture_attempt_at=str(
                self._last_capture_attempt_at or self._runtime.last_capture_attempt_at
            ),
            last_capture_completed_at=str(
                self._last_capture_completed_at or self._runtime.last_capture_completed_at
            ),
            last_capture_error=last_capture_error,
            last_raw_ocr_text=last_raw_ocr_text,
            last_rejected_ocr_text=str(
                self._last_rejected_ocr_text or self._runtime.last_rejected_ocr_text
            ),
            last_rejected_ocr_reason=str(
                self._last_rejected_ocr_reason or self._runtime.last_rejected_ocr_reason
            ),
            last_rejected_ocr_at=str(
                self._last_rejected_ocr_at or self._runtime.last_rejected_ocr_at
            ),
            last_rejected_capture_backend=str(
                self._last_rejected_capture_backend
                or self._runtime.last_rejected_capture_backend
            ),
            ocr_capture_content_trusted=bool(self._ocr_capture_content_trusted),
            ocr_capture_rejected_reason=str(
                self._ocr_capture_rejected_reason
                or self._runtime.ocr_capture_rejected_reason
            ),
            last_observed_line=dict(self._last_observed_line or self._runtime.last_observed_line),
            last_stable_line=last_stable_line,
            stable_ocr_last_raw_text=str(self._default_ocr_state.last_raw_text or ""),
            stable_ocr_repeat_count=max(
                0,
                int(self._default_ocr_state.repeat_count or 0),
            ),
            stable_ocr_stable_text=str(self._default_ocr_state.stable_text or ""),
            stable_ocr_block_reason=str(self._default_ocr_state.last_block_reason or ""),
            capture_backend_kind=str(
                self._capture_backend_kind
                or self._runtime.capture_backend_kind
                or getattr(self._capture_backend, "last_backend_kind", "")
                or getattr(self._capture_backend, "selection", "")
            ),
            capture_backend_detail=str(
                self._capture_backend_detail
                or self._runtime.capture_backend_detail
                or getattr(self._capture_backend, "last_backend_detail", "")
                or ""
            ),
            last_capture_image_hash=str(
                self._last_capture_image_hash or self._runtime.last_capture_image_hash
            ),
            last_capture_source_size=dict(
                self._last_capture_source_size or self._runtime.last_capture_source_size
            ),
            last_capture_rect=dict(
                self._last_capture_rect or self._runtime.last_capture_rect
            ),
            last_capture_window_rect=dict(
                self._last_capture_window_rect or self._runtime.last_capture_window_rect
            ),
            consecutive_same_capture_frames=max(
                0,
                int(
                    self._consecutive_same_capture_frames
                    or self._runtime.consecutive_same_capture_frames
                    or 0
                ),
            ),
            stale_capture_backend=stale_capture_backend,
            foreground_advance_monitor_running=(
                foreground_advance_enabled and self._wheel_monitor.is_running()
            ),
            foreground_advance_last_seq=foreground_advance_last_seq,
            foreground_advance_consumed_seq=int(
                self._runtime.foreground_advance_consumed_seq or self._last_consumed_wheel_seq
            ),
            foreground_advance_last_kind=str(self._runtime.foreground_advance_last_kind or ""),
            foreground_advance_last_delta=int(self._runtime.foreground_advance_last_delta or 0),
            foreground_advance_last_matched=bool(self._runtime.foreground_advance_last_matched),
            foreground_advance_last_match_reason=str(
                self._runtime.foreground_advance_last_match_reason or ""
            ),
            foreground_advance_consumed_count=int(
                self._runtime.foreground_advance_consumed_count or 0
            ),
            foreground_advance_matched_count=int(
                self._runtime.foreground_advance_matched_count or 0
            ),
            foreground_advance_coalesced_count=int(
                self._runtime.foreground_advance_coalesced_count or 0
            ),
            foreground_advance_first_event_ts=float(
                self._runtime.foreground_advance_first_event_ts or 0.0
            ),
            foreground_advance_last_event_ts=float(
                self._runtime.foreground_advance_last_event_ts or 0.0
            ),
            foreground_advance_detected_at=float(
                self._runtime.foreground_advance_detected_at or 0.0
            ),
            foreground_advance_last_event_age_seconds=float(
                self._runtime.foreground_advance_last_event_age_seconds or 0.0
            ),
            last_capture_total_duration_seconds=float(
                _timing_float(
                    "total_duration_seconds",
                    self._runtime.last_capture_total_duration_seconds,
                )
            ),
            last_capture_frame_duration_seconds=float(
                _timing_float(
                    "capture_frame_duration_seconds",
                    self._runtime.last_capture_frame_duration_seconds,
                )
            ),
            last_capture_background_duration_seconds=float(
                _timing_float(
                    "background_hash_duration_seconds",
                    self._runtime.last_capture_background_duration_seconds,
                )
            ),
            last_capture_image_hash_duration_seconds=float(
                _timing_float(
                    "capture_image_hash_duration_seconds",
                    self._runtime.last_capture_image_hash_duration_seconds,
                )
            ),
            last_ocr_extract_duration_seconds=float(
                _timing_float(
                    "ocr_extract_duration_seconds",
                    self._runtime.last_ocr_extract_duration_seconds,
                )
            ),
            last_backend_plan_duration_seconds=float(
                _timing_float(
                    "backend_plan_duration_seconds",
                    self._runtime.last_backend_plan_duration_seconds,
                )
            ),
            last_window_scan_duration_seconds=float(
                _timing_float(
                    "window_scan_duration_seconds",
                    self._runtime.last_window_scan_duration_seconds,
                )
            ),
            last_capture_background_hash_skipped=(
                bool(capture_timing["background_hash_skipped"])
                if "background_hash_skipped" in capture_timing
                else bool(self._runtime.last_capture_background_hash_skipped)
            ),
            screen_awareness_last_skip_reason=str(
                capture_timing.get("screen_awareness_skip_reason")
                or self._runtime.screen_awareness_last_skip_reason
            ),
            screen_awareness_last_region_count=max(
                0,
                int(
                    float(
                        capture_timing.get(
                            "screen_awareness_region_count",
                            self._runtime.screen_awareness_last_region_count,
                        )
                        or 0
                    )
                ),
            ),
            screen_awareness_last_capture_duration_seconds=float(
                _timing_float(
                    "screen_awareness_capture_duration_seconds",
                    self._runtime.screen_awareness_last_capture_duration_seconds,
                )
            ),
            screen_awareness_last_ocr_duration_seconds=float(
                _timing_float(
                    "screen_awareness_ocr_duration_seconds",
                    self._runtime.screen_awareness_last_ocr_duration_seconds,
                )
            ),
            scene_ordering_diagnostic=str(
                self._scene_ordering_diagnostic
                or self._runtime.scene_ordering_diagnostic
                or "none"
            ),
            vision_snapshot_available=bool(vision_snapshot.get("available")),
            vision_snapshot_captured_at=str(vision_snapshot.get("captured_at") or ""),
            vision_snapshot_expires_at=str(vision_snapshot.get("expires_at") or ""),
            vision_snapshot_source=str(vision_snapshot.get("source") or ""),
            vision_snapshot_width=int(vision_snapshot.get("width") or 0),
            vision_snapshot_height=int(vision_snapshot.get("height") or 0),
            vision_snapshot_byte_size=int(vision_snapshot.get("byte_size") or 0),
            screen_awareness_sample_collection_enabled=bool(
                self._config.ocr_reader_screen_awareness_sample_collection_enabled
            ),
            screen_awareness_sample_count=int(self._screen_awareness_sample_count or 0),
            screen_awareness_sample_last_path=str(self._screen_awareness_sample_last_path or ""),
            screen_awareness_sample_last_error=str(self._screen_awareness_sample_last_error or ""),
            screen_awareness_model_enabled=bool(
                self._config.ocr_reader_screen_awareness_model_enabled
            ),
            screen_awareness_model_available=self._screen_awareness_model_payload is not None,
            screen_awareness_model_path=str(
                self._config.ocr_reader_screen_awareness_model_path or ""
            ),
            screen_awareness_model_detail=str(self._screen_awareness_model_detail or ""),
            screen_awareness_model_last_stage=str(self._screen_awareness_model_last_stage or ""),
            screen_awareness_model_last_confidence=float(
                self._screen_awareness_model_last_confidence or 0.0
            ),
            screen_awareness_model_last_latency_seconds=float(
                self._screen_awareness_model_last_latency_seconds or 0.0
            ),
            vision_classifier_enabled=bool(
                getattr(self._config, "vision_classifier_enabled", False)
            ),
            vision_classifier_available=getattr(self, "vision_classifier", None) is not None,
            vision_classifier_detail=str(
                getattr(self, "_vision_classifier_detail", "") or ""
            ),
            vision_classifier_last_label=str(
                getattr(self, "_vision_classifier_last_label", "") or ""
            ),
            vision_classifier_last_confidence=float(
                getattr(self, "_vision_classifier_last_confidence", 0.0) or 0.0
            ),
            vision_classifier_last_latency_ms=float(
                getattr(self, "_vision_classifier_last_latency_ms", 0.0) or 0.0
            ),
        )


    def _screen_awareness_latency_mode(self) -> str:
        mode = str(
            getattr(
                self._config,
                "ocr_reader_screen_awareness_latency_mode",
                _SCREEN_AWARENESS_LATENCY_MODE_BALANCED,
            )
            or _SCREEN_AWARENESS_LATENCY_MODE_BALANCED
        ).strip().lower()
        if mode == _SCREEN_AWARENESS_LATENCY_MODE_AGGRESSIVE:
            return _SCREEN_AWARENESS_LATENCY_MODE_FULL
        if mode not in _SCREEN_AWARENESS_LATENCY_MODES:
            return _SCREEN_AWARENESS_LATENCY_MODE_BALANCED
        return mode


    def _screen_awareness_skip_reason(
        self,
        extraction: OcrExtractionResult,
        *,
        now: float,
    ) -> str:
        if (
            not bool(self._config.ocr_reader_screen_awareness_full_frame_ocr)
            and not bool(self._config.ocr_reader_screen_awareness_multi_region_ocr)
            and not bool(self._config.ocr_reader_screen_awareness_visual_rules)
            and not bool(self._config.llm_vision_enabled)
        ):
            return "disabled"
        mode = self._screen_awareness_latency_mode()
        if mode == _SCREEN_AWARENESS_LATENCY_MODE_OFF:
            return "latency_mode_off"
        text = str(extraction.text or "").strip()
        if text and _looks_like_self_ui_text(text):
            return "rejected_primary_text"
        if text and _looks_like_ocr_dialogue_text(text):
            return "primary_dialogue"
        if mode != _SCREEN_AWARENESS_LATENCY_MODE_FULL:
            minimum_interval = max(
                0.0,
                float(
                    getattr(
                        self._config,
                        "ocr_reader_screen_awareness_min_interval_seconds",
                        2.0,
                    )
                    or 0.0
                ),
            )
            if (
                self._last_screen_awareness_capture_at > 0.0
                and now - float(self._last_screen_awareness_capture_at or 0.0)
                < minimum_interval
            ):
                return "min_interval"
            if not text and int(self._consecutive_no_text_polls or 0) < 1:
                return "waiting_for_consecutive_no_text"
            if (
                text
                and not _looks_like_game_overlay_text(text)
                and not _looks_like_ocr_dialogue_text(text)
            ):
                return "primary_non_dialogue_text"
        elif now - float(self._last_screen_awareness_capture_at or 0.0) < 0.75 and text:
            return "min_interval"
        return ""


    def _augment_extraction_with_screen_awareness(
        self,
        extraction: OcrExtractionResult,
        *,
        target: DetectedGameWindow,
        primary_profile: OcrCaptureProfile,
        plan: SelectedOcrBackendPlan,
        now: float,
    ) -> None:
        skip_reason = self._screen_awareness_skip_reason(extraction, now=now)
        if skip_reason:
            extraction.timing["screen_awareness_skipped"] = True
            extraction.timing["screen_awareness_skip_reason"] = skip_reason
            extraction.timing["screen_awareness_region_count"] = 0.0
            extraction.timing["screen_awareness_capture_duration_seconds"] = 0.0
            extraction.timing["screen_awareness_ocr_duration_seconds"] = 0.0
            return
        extraction.timing["screen_awareness_skipped"] = False
        extraction.timing["screen_awareness_skip_reason"] = ""
        capture_duration = 0.0
        ocr_duration = 0.0
        regions: list[dict[str, Any]] = []
        visual_features: dict[str, Any] = {}
        seen_profiles = {self._capture_profile_key(primary_profile)}

        requests: list[tuple[str, OcrCaptureProfile, bool, bool]] = []
        full_profile = self._full_window_profile()
        if (
            bool(self._config.ocr_reader_screen_awareness_full_frame_ocr)
            or bool(self._config.ocr_reader_screen_awareness_visual_rules)
            or bool(self._config.llm_vision_enabled)
        ):
            requests.append(
                (
                    "full_frame",
                    full_profile,
                    bool(self._config.ocr_reader_screen_awareness_full_frame_ocr),
                    True,
                )
            )
            seen_profiles.add(self._capture_profile_key(full_profile))
        if bool(self._config.ocr_reader_screen_awareness_multi_region_ocr):
            for source, profile in (
                ("menu_region", self._menu_region_profile()),
                ("top_region", self._top_region_profile()),
            ):
                key = self._capture_profile_key(profile)
                if key in seen_profiles:
                    continue
                requests.append((source, profile, True, False))
                seen_profiles.add(key)

        for source, profile, extract_text, collect_visual in requests:
            try:
                capture_started_at = self._time_fn()
                frame = self._capture_backend.capture_frame(target, profile)
                capture_duration += max(0.0, self._time_fn() - capture_started_at)
                metadata = _frame_choice_bounds_metadata(frame, text_source=source)
                if source == "full_frame":
                    self._remember_vision_snapshot(frame, source=source, now=now)
                if collect_visual and bool(self._config.ocr_reader_screen_awareness_visual_rules):
                    visual_features.update(
                        analyze_screen_visual_features(
                            frame,
                            boxes=[],
                            bounds_metadata=metadata,
                        )
                    )
                if not extract_text:
                    continue
                ocr_started_at = self._time_fn()
                region_extraction = self._extract_text_from_image(frame, plan=plan)
                ocr_duration += max(0.0, self._time_fn() - ocr_started_at)
                region_extraction.text_source = source
                regions.append(
                    {
                        "source": source,
                        "text": region_extraction.text,
                        "boxes": list(region_extraction.boxes),
                        "bounds_metadata": metadata,
                        "ocr_confidence": region_extraction.ocr_confidence,
                    }
                )
                extraction.warnings.extend(region_extraction.warnings)
            except Exception as exc:
                extraction.warnings.append(f"screen awareness {source} skipped: {exc}")
                log_debug = getattr(self, "_log_debug", None)
                if callable(log_debug):
                    log_debug("ocr_reader screen awareness {} skipped: {}", source, exc)

        if regions or visual_features:
            self._last_screen_awareness_capture_at = now
        extraction.screen_ocr_regions = regions
        extraction.screen_visual_features = visual_features
        extraction.timing["screen_awareness_capture_duration_seconds"] = capture_duration
        extraction.timing["screen_awareness_ocr_duration_seconds"] = ocr_duration
        extraction.timing["screen_awareness_region_count"] = float(len(regions))


    def _should_emit_dialogue_screen_transition(
        self,
        classification: ScreenClassification,
    ) -> bool:
        if not bool(getattr(self._config, "ocr_reader_screen_type_transition_emit", True)):
            return False
        current_type = str((self._writer.current_state or {}).get("screen_type") or "")
        if not current_type:
            return False
        if current_type in _DIALOGUE_LIKE_CLASSIFICATION_TYPES:
            return False
        return str(classification.screen_type or "") in _DIALOGUE_LIKE_CLASSIFICATION_TYPES


    def _apply_screen_awareness_model(
        self,
        extraction: OcrExtractionResult,
        *,
        classification: ScreenClassification,
        target: DetectedGameWindow,
    ) -> ScreenClassification:
        self._screen_awareness_model_last_stage = ""
        self._screen_awareness_model_last_confidence = 0.0
        self._screen_awareness_model_last_latency_seconds = 0.0
        if not bool(self._config.ocr_reader_screen_awareness_model_enabled):
            self._screen_awareness_model_detail = "disabled"
            return classification
        if _matches_aihong_target(target):
            self._screen_awareness_model_detail = "skipped_aihong_target"
            return classification
        if (
            classification.screen_type != OCR_CAPTURE_PROFILE_STAGE_DEFAULT
            and float(classification.confidence or 0.0) >= 0.45
        ):
            self._screen_awareness_model_detail = "skipped_high_confidence_rule"
            return classification
        features = self._screen_awareness_model_features(extraction, classification)
        if not features:
            self._screen_awareness_model_detail = "no_features"
            return classification
        model_payload = self._load_screen_awareness_model()
        if model_payload is None:
            return classification

        started_at = self._time_fn()
        prediction = classify_screen_awareness_model(
            features,
            model_payload,
            min_confidence=float(
                self._config.ocr_reader_screen_awareness_model_min_confidence or 0.55
            ),
        )
        self._screen_awareness_model_last_latency_seconds = max(
            0.0,
            self._time_fn() - started_at,
        )
        if prediction is None:
            self._screen_awareness_model_detail = "no_match"
            return classification
        self._screen_awareness_model_last_stage = str(prediction.get("stage") or "")
        self._screen_awareness_model_last_confidence = float(prediction.get("confidence") or 0.0)
        if (
            classification.screen_type != OCR_CAPTURE_PROFILE_STAGE_DEFAULT
            and self._screen_awareness_model_last_confidence
            <= float(classification.confidence or 0.0) + 0.04
        ):
            self._screen_awareness_model_detail = "rule_confidence_wins"
            return classification
        result_debug = dict(classification.debug)
        result_debug["reason"] = "screen_awareness_model"
        result_debug["model"] = json_copy(prediction)
        self._screen_awareness_model_detail = "matched"
        return ScreenClassification(
            screen_type=str(prediction.get("stage") or OCR_CAPTURE_PROFILE_STAGE_DEFAULT),
            confidence=round(
                max(0.0, min(float(prediction.get("confidence") or 0.0), 0.99)),
                2,
            ),
            ui_elements=list(classification.ui_elements),
            raw_ocr_text=list(classification.raw_ocr_text),
            debug=result_debug,
        )


    def _screen_awareness_model_features(
        self,
        extraction: OcrExtractionResult,
        classification: ScreenClassification,
    ) -> dict[str, Any]:
        features = dict(extraction.screen_visual_features or {})
        debug = classification.debug if isinstance(classification.debug, dict) else {}
        layout = debug.get("layout")
        if isinstance(layout, dict):
            for key, value in layout.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    features[str(key)] = float(value)
        features["line_count"] = len(classification.raw_ocr_text)
        features["ui_element_count"] = len(classification.ui_elements)
        return {
            key: value
            for key, value in features.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }


    def _screen_awareness_model_path(self) -> Path | None:
        raw = str(self._config.ocr_reader_screen_awareness_model_path or "").strip()
        if not raw:
            self._screen_awareness_model_detail = "model_path_empty"
            return None
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not path.is_absolute():
            path = Path(self._config.bridge_root) / path
        return path


    def _load_screen_awareness_model(self) -> dict[str, Any] | None:
        path = self._screen_awareness_model_path()
        if path is None:
            self._screen_awareness_model_payload = None
            self._screen_awareness_model_cache_key = None
            return None
        try:
            stat = path.stat()
        except OSError as exc:
            self._screen_awareness_model_detail = f"model_unavailable: {exc}"
            self._screen_awareness_model_payload = None
            self._screen_awareness_model_cache_key = None
            return None
        cache_key = (str(path), float(stat.st_mtime))
        if (
            self._screen_awareness_model_cache_key == cache_key
            and self._screen_awareness_model_payload is not None
        ):
            return self._screen_awareness_model_payload
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._screen_awareness_model_detail = f"model_load_failed: {exc}"
            self._screen_awareness_model_payload = None
            self._screen_awareness_model_cache_key = None
            return None
        if not isinstance(payload, dict):
            self._screen_awareness_model_detail = "model_payload_not_object"
            self._screen_awareness_model_payload = None
            self._screen_awareness_model_cache_key = None
            return None
        prototypes = payload.get("prototypes") or payload.get("labels") or []
        prototype_count = len(prototypes) if isinstance(prototypes, list) else 0
        if prototype_count <= 0:
            self._screen_awareness_model_detail = "model_has_no_prototypes"
            self._screen_awareness_model_payload = None
            self._screen_awareness_model_cache_key = None
            return None
        self._screen_awareness_model_payload = payload
        self._screen_awareness_model_cache_key = cache_key
        self._screen_awareness_model_detail = f"loaded:{prototype_count}"
        return payload


    def _screen_awareness_sample_file_path(self) -> Path:
        raw = str(self._config.ocr_reader_screen_awareness_sample_dir or "").strip()
        sample_dir = (
            Path(os.path.expandvars(os.path.expanduser(raw)))
            if raw
            else Path(self._config.bridge_root) / "_screen_awareness_samples"
        )
        if not sample_dir.is_absolute():
            sample_dir = Path(self._config.bridge_root) / sample_dir
        sample_dir.mkdir(parents=True, exist_ok=True)
        return sample_dir / "samples.jsonl"


    def _collect_screen_awareness_sample(
        self,
        extraction: OcrExtractionResult,
        *,
        classification: ScreenClassification,
        target: DetectedGameWindow,
        now: float,
    ) -> None:
        if not bool(self._config.ocr_reader_screen_awareness_sample_collection_enabled):
            self._screen_awareness_sample_last_error = ""
            return
        try:
            sample_path = self._screen_awareness_sample_file_path()
            image_path = ""
            image_save_error = ""
            capture_image = getattr(extraction, "captured_image", None)
            if hasattr(capture_image, "save"):
                try:
                    image_dir = sample_path.parent / "images"
                    image_dir.mkdir(parents=True, exist_ok=True)
                    screen_type = re.sub(
                        r"[^a-zA-Z0-9_.-]+",
                        "_",
                        str(classification.screen_type or "unknown"),
                    ).strip("_")[:48] or "unknown"
                    hash_part = str(extraction.capture_image_hash or "").strip()[:12]
                    if not hash_part:
                        hash_part = uuid4().hex[:12]
                    image_file = image_dir / f"{int(now * 1000)}_{screen_type}_{hash_part}.png"
                    capture_image.save(image_file)
                    image_path = str(image_file.relative_to(sample_path.parent))
                except Exception as exc:
                    image_save_error = str(exc)
            regions: list[dict[str, Any]] = []
            for region in list(extraction.screen_ocr_regions or [])[:8]:
                if not isinstance(region, dict):
                    continue
                region_text = str(region.get("text") or "")
                regions.append(
                    {
                        "source": str(region.get("source") or ""),
                        "ocr_lines": _stripped_ocr_lines(region_text)[:20],
                        "ocr_confidence": float(region.get("ocr_confidence") or 0.0),
                        "bounds_metadata": json_copy(region.get("bounds_metadata") or {}),
                    }
                )
            record = {
                "version": 1,
                "sampled_at": utc_now_iso(now),
                "process_name": str(target.process_name or ""),
                "window_title": str(target.title or ""),
                "width": int(target.width or 0),
                "height": int(target.height or 0),
                "image_path": image_path,
                "image_save_error": image_save_error,
                "ocr_lines": _stripped_ocr_lines(extraction.text)[:20],
                "ocr_regions": regions,
                "visual_features": json_copy(extraction.screen_visual_features or {}),
                "screen_type": str(classification.screen_type or ""),
                "screen_confidence": float(classification.confidence or 0.0),
                "screen_reason": str((classification.debug or {}).get("reason") or ""),
                "screen_ui_elements": json_copy(classification.ui_elements[:10]),
            }
            with sample_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._screen_awareness_sample_count += 1
            self._screen_awareness_sample_last_path = str(sample_path)
            self._screen_awareness_sample_last_error = ""
        except Exception as exc:
            self._screen_awareness_sample_last_error = str(exc)
            log_debug = getattr(self, "_log_debug", None)
            if callable(log_debug):
                log_debug("ocr_reader screen awareness sample skipped: {}", exc)


    def _screen_templates_for_target(self, target: DetectedGameWindow) -> list[dict[str, Any]]:
        if _matches_aihong_target(target):
            return []
        templates = self._config.ocr_reader_screen_templates
        return list(templates or []) if isinstance(templates, list) else []


    def _screen_template_context(self, target: DetectedGameWindow) -> dict[str, Any]:
        return {
            "process_name": str(target.process_name or ""),
            "window_title": str(target.title or ""),
            "width": int(target.width or 0),
            "height": int(target.height or 0),
            "game_id": str(self._writer.game_id or ""),
        }


    def _apply_screen_classification_stability(
        self,
        classification: ScreenClassification,
    ) -> ScreenClassification:
        screen_type = str(classification.screen_type or "")
        if not screen_type or screen_type == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
            self._last_screen_classification_type = screen_type
            self._last_screen_classification_streak = 0
            return classification
        if screen_type == self._last_screen_classification_type:
            self._last_screen_classification_streak += 1
        else:
            self._last_screen_classification_type = screen_type
            self._last_screen_classification_streak = 1
        bonus = min(max(self._last_screen_classification_streak - 1, 0) * 0.04, 0.12)
        if bonus <= 0.0:
            classification.debug = {
                **dict(classification.debug or {}),
                "stability_streak": self._last_screen_classification_streak,
                "stability_bonus": 0.0,
            }
            return classification
        classification.confidence = round(
            max(0.0, min(float(classification.confidence or 0.0) + bonus, 0.99)),
            2,
        )
        classification.debug = {
            **dict(classification.debug or {}),
            "stability_streak": self._last_screen_classification_streak,
            "stability_bonus": round(bonus, 2),
        }
        return classification


    def _should_skip_dialogue_for_screen_classification(
        self,
        classification: ScreenClassification,
    ) -> bool:
        # This threshold is higher than _screen_classification_is_known (0.45):
        # skipping dialogue needs stronger confidence to avoid false non-dialogue gates.
        if float(classification.confidence or 0.0) < 0.5:
            return False
        if (
            str(classification.screen_type or "") == self._known_screen_skip_bypass_type
            and self._time_fn() <= float(self._known_screen_skip_bypass_until or 0.0)
        ):
            classification.debug = {
                **dict(classification.debug or {}),
                "skip_dialogue_bypassed": True,
                "skip_dialogue_bypass_reason": "known_screen_timeout_rescan",
            }
            return False
        return classification.screen_type in {
            OCR_CAPTURE_PROFILE_STAGE_TITLE,
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
            OCR_CAPTURE_PROFILE_STAGE_CONFIG,
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
        }


    @staticmethod
    def _screen_classification_is_known(classification: ScreenClassification) -> bool:
        # This threshold is lower than _should_skip_dialogue_for_screen_classification
        # (0.5): known screen tracking can accept weaker evidence than dialogue gating.
        if float(classification.confidence or 0.0) < 0.45:
            return False
        return classification.screen_type in {
            OCR_CAPTURE_PROFILE_STAGE_TITLE,
            OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
            OCR_CAPTURE_PROFILE_STAGE_CONFIG,
            OCR_CAPTURE_PROFILE_STAGE_MENU,
            OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
            OCR_CAPTURE_PROFILE_STAGE_GALLERY,
            OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
            OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
        }
