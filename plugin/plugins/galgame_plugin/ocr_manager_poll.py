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
    _is_legacy_geometryless_auto_window,
)

def _foreground_window_handle() -> int:
    return _ocr_reader_module._foreground_window_handle()


class PollMixin:
    """tick 循环、poll 调度、生命周期管理"""

    def _reset_default_ocr_state(self) -> None:
        self._default_ocr_state.reset()
        self._consecutive_no_text_polls = 0
        self._last_capture_error = ""
        self._last_raw_ocr_text = ""
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""
        self._last_observed_line = {}
        self._last_stable_line = {}
        self._last_capture_image_hash = ""
        self._last_capture_source_size = {}
        self._last_capture_rect = {}
        self._last_capture_window_rect = {}
        self._last_capture_timing = {}
        self._consecutive_same_capture_frames = 0
        self._stale_capture_backend = False
        self._last_background_hash = ""
        self._last_background_hash_capture_at = 0.0
        self._pending_background_hash = ""
        self._pending_background_change_count = 0
        self._pending_visual_scene_hash = ""
        self._pending_visual_scene_at = 0.0
        self._pending_visual_scene_distance = 0
        self._pending_visual_scene_commit_diagnostic = ""
        self._pending_background_candidate_hash = ""
        self._pending_background_candidate_at = 0.0
        self._pending_background_candidate_distance = 0
        self._pending_background_candidate_base_hash = ""
        self._pending_background_candidate_used = False
        self._scene_ordering_diagnostic = "none"
        self._background_capture_pause_until = 0.0
        self._background_capture_pause_reason = ""
        self._last_scene_change_committed_ts = 0.0


    def _reset_aihong_menu_state(self) -> None:
        was_menu_state = (
            self._aihong_stage == _AIHONG_MENU_STAGE
            or bool(str(self._aihong_menu_ocr_state.last_raw_text or "").strip())
        )
        self._aihong_menu_ocr_state.reset()
        self._aihong_stage = _AIHONG_DIALOGUE_STAGE
        self._aihong_dialogue_idle_polls = 0
        self._aihong_menu_missing_polls = 0
        if was_menu_state:
            self._consecutive_no_text_polls = 0


    def _handle_background_capture_backend_unsuitable(
        self,
        extraction: OcrExtractionResult,
        *,
        now: float,
        result: OcrReaderTickResult,
    ) -> bool:
        if not bool(extraction.timing.get("background_capture_backend_unsuitable")):
            return False
        reason = str(
            extraction.timing.get("capture_quality_detail")
            or extraction.capture_backend_detail
            or "invalid_background_frame"
        )
        self._pause_background_capture_backend(reason=reason, now=now)
        self._record_capture_error(
            now=now,
            error=RuntimeError(f"backend_not_suitable_for_background: {reason}"),
        )
        self._record_rejected_ocr_text(
            extraction.text,
            reason=f"background_capture_backend_unsuitable:{reason}",
            now=now,
            capture_backend_kind=extraction.capture_backend_kind,
        )
        self._default_ocr_state.reset()
        self._ocr_lang_detector.reset()
        self._reset_aihong_menu_state()
        result.warnings.append(
            f"ocr_reader background capture backend not suitable: {reason}"
        )
        return True


    async def shutdown(self) -> None:
        self._stop_foreground_advance_monitor()
        self._shutdown_capture_worker()
        self._release_rapidocr_backend()
        classifier = self.vision_classifier
        self.vision_classifier = None
        close_classifier = getattr(classifier, "close", None)
        if callable(close_classifier):
            close_classifier()
        if self._writer.session_id:
            self._writer.end_session(ts=utc_now_iso(self._time_fn()))
            self._ocr_lang_detector.reset(clear_switch_time=True)
        self._attached_window = None


    async def _tick_preflight(
        self,
        *,
        now: float,
        bridge_sdk_available: bool,
        memory_reader_runtime: dict[str, Any],
        result: OcrReaderTickResult,
    ) -> _TickPreflightResult:
        if not self._config.ocr_reader_enabled:
            self._runtime = OcrReaderRuntime(enabled=False, status="disabled", detail="disabled_by_config")
            await self._end_session_if_needed(now)
            result.runtime = self._runtime.to_dict()
            return _TickPreflightResult(result=result, should_return=True)

        if not self._platform_fn():
            # macOS / Linux: only refuse if the capture backend really
            # has no live sub-backend. Win32CaptureBackend filters out
            # dxcam/printwindow on these platforms and appends the
            # Electron HTTP bridge on Linux, so is_available() reflects
            # the actual cross-platform capability. Pure Wayland with
            # no Electron endpoint reachable returns False here, and the
            # user sees the documented unsupported_platform path.
            backend = self._capture_backend
            backend_alive = False
            try:
                backend_alive = bool(await asyncio.to_thread(backend.is_available))
            except Exception:
                backend_alive = False
            if not backend_alive:
                self._runtime = self._build_runtime(
                    status="idle",
                    detail="unsupported_platform",
                    plan=SelectedOcrBackendPlan(),
                )
                await self._end_session_if_needed(now)
                result.warnings.append(
                    "ocr_reader: no capture backend available on this platform"
                )
                result.runtime = self._runtime.to_dict()
                return _TickPreflightResult(result=result, should_return=True)

        backend_plan_started_at = self._time_fn()
        if self._custom_ocr_backend:
            backend_plan = self._custom_ocr_backend_plan()
        else:
            backend_plan = await asyncio.to_thread(self._resolve_backend_plan)
        backend_plan_duration = max(0.0, self._time_fn() - backend_plan_started_at)
        if not backend_plan.primary.available:
            self._runtime = self._build_runtime(
                status="idle",
                detail=self._backend_unavailable_detail(backend_plan),
                plan=backend_plan,
            )
            await self._end_session_if_needed(now)
            result.warnings.extend(self._backend_unavailable_warnings(backend_plan))
            result.runtime = self._runtime.to_dict()
            return _TickPreflightResult(
                result=result,
                backend_plan=backend_plan,
                backend_plan_duration=backend_plan_duration,
                should_return=True,
            )

        if bridge_sdk_available:
            self._reset_memory_reader_text_progress_tracking()
            self._runtime = self._build_runtime(
                status="idle",
                detail="bridge_sdk_available",
                plan=backend_plan,
            )
            await self._end_session_if_needed(now)
            result.runtime = self._runtime.to_dict()
            return _TickPreflightResult(
                result=result,
                backend_plan=backend_plan,
                backend_plan_duration=backend_plan_duration,
                should_return=True,
            )

        memory_reader_has_recent_text = self._observe_memory_reader_text_progress(
            memory_reader_runtime,
            now=now,
        )
        if memory_reader_has_recent_text:
            self._runtime = self._build_runtime(
                status="idle",
                detail="memory_reader_active",
                plan=backend_plan,
            )
            result.runtime = self._runtime.to_dict()
            return _TickPreflightResult(
                result=result,
                backend_plan=backend_plan,
                backend_plan_duration=backend_plan_duration,
                should_return=True,
            )

        if self._last_memory_reader_text_at > 0:
            elapsed = now - self._last_memory_reader_text_at
            threshold = float(self._config.ocr_reader_no_text_takeover_after_seconds)
            if elapsed < threshold:
                self._runtime = self._build_runtime(
                    status="idle",
                    detail="waiting_for_takeover_window",
                    plan=backend_plan,
                )
                result.runtime = self._runtime.to_dict()
                return _TickPreflightResult(
                    result=result,
                    backend_plan=backend_plan,
                    backend_plan_duration=backend_plan_duration,
                    should_return=True,
                )

        try:
            capture_backend_available = await asyncio.to_thread(
                self._capture_backend.is_available
            )
        except Exception:
            capture_backend_available = False
        if not capture_backend_available:
            self._runtime = self._build_runtime(
                status="candidate",
                detail="capture_backend_unavailable",
                plan=backend_plan,
                takeover_reason="capture_backend_not_available",
            )
            await self._end_session_if_needed(now)
            result.warnings.append("ocr_reader capture backend is not available")
            result.runtime = self._runtime.to_dict()
            return _TickPreflightResult(
                result=result,
                backend_plan=backend_plan,
                backend_plan_duration=backend_plan_duration,
                should_return=True,
            )

        return _TickPreflightResult(
            result=result,
            backend_plan=backend_plan,
            backend_plan_duration=backend_plan_duration,
        )


    async def _prepare_tick_target_context(
        self,
        *,
        now: float,
        backend_plan: SelectedOcrBackendPlan,
        memory_reader_runtime: dict[str, Any],
        result: OcrReaderTickResult,
    ) -> _TickTargetContext:
        use_windows_foreground_api = bool(self._platform_fn())
        foreground_hwnd_for_scan = (
            _foreground_window_handle() if use_windows_foreground_api else 0
        )
        force_window_scan = (
            self._last_selection.selection_detail == "locked_target_unavailable"
            or (
                self._attached_window is not None
                and use_windows_foreground_api
                and foreground_hwnd_for_scan > 0
                and not _foreground_matches_target(
                    foreground_hwnd_for_scan,
                    self._attached_window,
                )[0]
            )
        )
        window_scan_started_at = self._time_fn()
        scanned_windows = await asyncio.to_thread(
            self._scan_raw_windows_cached,
            force=force_window_scan,
        )
        window_scan_duration = max(0.0, self._time_fn() - window_scan_started_at)
        eligible_windows, excluded_windows = self._prepare_window_inventory(scanned_windows)
        selection = self._select_target_window(
            eligible_windows,
            excluded_windows=excluded_windows,
            memory_reader_runtime=memory_reader_runtime,
        )
        self._last_selection = selection
        target = selection.target
        if target is None:
            self._runtime = self._build_runtime(
                status="idle",
                detail="waiting_for_valid_window",
                plan=backend_plan,
                selection=selection,
            )
            await self._end_session_if_needed(now)
            result.runtime = self._runtime.to_dict()
            return _TickTargetContext(
                result=result,
                selection=selection,
                window_scan_duration=window_scan_duration,
                now=now,
                should_return=True,
            )

        legacy_geometryless_auto_target = (
            selection.selection_detail == "single_geometryless_candidate"
            or (
                _is_legacy_geometryless_auto_window(target)
                and not bool(target.is_foreground)
            )
        )
        aihong_two_stage_enabled = self._should_use_aihong_two_stage(target)
        if not aihong_two_stage_enabled:
            self._reset_aihong_menu_state()
        profile_stage = self._aihong_stage if aihong_two_stage_enabled else _AIHONG_DIALOGUE_STAGE
        capture_profile_selection = self._capture_profile_selection_for_target(
            target,
            stage=profile_stage,
        )
        profile = capture_profile_selection.profile

        started_session = False
        if (
            self._attached_window is None
            or self._attached_window.pid != target.pid
            or not self._writer.session_id
        ):
            if (
                not self._writer.session_id
                or self._writer.game_id != _ocr_game_id_from_process(target.process_name or target.title)
            ):
                self._writer.start_session(target)
                started_session = True
                if legacy_geometryless_auto_target:
                    self._writer.keep_unknown_scene_until_visual_scene()
                now = max(now, self._time_fn())
                result.should_rescan = True
            self._attached_window = target
            self._last_heartbeat_at = now
            self._ocr_lang_detector.reset(clear_switch_time=True)
            self._reset_default_ocr_state()
            self._reset_aihong_menu_state()
            startup_profile_stage = (
                self._aihong_stage if aihong_two_stage_enabled else OCR_CAPTURE_PROFILE_STAGE_DEFAULT
            )
            startup_profile_selection = self._capture_profile_selection_for_target(
                target,
                stage=(
                    self._aihong_stage
                    if aihong_two_stage_enabled
                    else _AIHONG_DIALOGUE_STAGE
                ),
            )
            self._runtime = self._build_runtime(
                status="starting",
                detail="starting_capture",
                plan=backend_plan,
                target=target,
                capture_stage=startup_profile_stage,
                capture_profile=startup_profile_selection.profile.to_dict(),
                capture_profile_selection=startup_profile_selection,
                selection=selection,
                game_id=self._writer.game_id,
                session_id=self._writer.session_id,
                last_seq=self._writer.last_seq,
                last_event_ts=self._writer.last_event_ts,
            )

        if self._attached_window is not None:
            self._attached_window = target
        self._remember_locked_target(target)

        return _TickTargetContext(
            result=result,
            target=target,
            selection=selection,
            profile=profile,
            capture_profile_selection=capture_profile_selection,
            legacy_geometryless_auto_target=legacy_geometryless_auto_target,
            aihong_two_stage_enabled=aihong_two_stage_enabled,
            started_session=started_session,
            window_scan_duration=window_scan_duration,
            now=now,
        )


    def _finalize_tick_result(
        self,
        *,
        result: OcrReaderTickResult,
        now: float,
        poll_started_at: float,
        backend_plan: SelectedOcrBackendPlan,
        active_backend: OcrBackendDescriptor,
        backend_detail_override: str,
        target: DetectedGameWindow,
        aihong_two_stage_enabled: bool,
        runtime_profile: OcrCaptureProfile,
        runtime_capture_profile_selection: ResolvedOcrCaptureSelection,
        selection: WindowSelectionResult,
        emitted: bool,
        guard_blocked: bool,
        screen_classification: ScreenClassification,
        screen_event_emitted: bool,
        capture_attempted: bool,
        capture_completed: bool,
        capture_error: bool,
        text_event_seq_before_capture: int,
        foreground_advance_stable_grace_active: bool,
    ) -> OcrReaderTickResult:
        if (
            self._pending_visual_scene_hash
            and self._pending_visual_scene_at > 0
            and now - self._pending_visual_scene_at > _PENDING_VISUAL_SCENE_MAX_SECONDS
        ):
            self._commit_pending_visual_scene(
                now=now,
                diagnostic="pending_scene_committed_by_timeout",
            )

        if bool(getattr(self, "_visual_scene_committed", False)):
            result.should_rescan = True
            self._visual_scene_committed = False

        status = self._runtime.status
        detail = self._runtime.detail
        observed_or_stable_emitted = int(self._writer.last_seq or 0) > text_event_seq_before_capture
        known_screen_classified = self._screen_classification_is_known(screen_classification)

        if emitted:
            self._reset_known_screen_stuck_tracking()
            if foreground_advance_stable_grace_active:
                self._foreground_advance_stable_until = 0.0
            result.stable_event_emitted = True
            result.should_rescan = True
            self._mark_observed_progress(now=now)
            self._last_heartbeat_at = now
            status = "active"
            detail = "receiving_text"
        elif observed_or_stable_emitted:
            self._reset_known_screen_stuck_tracking()
            result.should_rescan = True
            self._mark_observed_progress(now=now)
            self._last_heartbeat_at = now
            if status == "starting":
                status = "active"
            detail = "receiving_observed_text"
        elif guard_blocked:
            self._reset_known_screen_stuck_tracking()
            if status == "starting":
                status = "active"
            detail = "self_ui_guard_blocked"
        elif capture_error:
            self._reset_known_screen_stuck_tracking()
            if status == "starting":
                status = "active"
            detail = "capture_failed"
        elif screen_event_emitted or known_screen_classified:
            self._consecutive_no_text_polls = 0
            if status == "starting":
                status = "active"
            if known_screen_classified and self._record_known_screen_classification(
                screen_classification,
                now=now,
                result=result,
            ):
                detail = "screen_classified_timeout_rescan"
            else:
                if not known_screen_classified:
                    self._reset_known_screen_stuck_tracking()
                detail = "screen_classified"
        elif capture_completed:
            self._reset_known_screen_stuck_tracking()
            self._mark_no_text_poll()
            if self._writer.session_id and now - self._last_heartbeat_at >= float(
                self._config.ocr_reader_poll_interval_seconds
            ):
                if self._writer.emit_heartbeat(ts=utc_now_iso(now)):
                    result.should_rescan = True
                    self._last_heartbeat_at = now
            if status == "starting":
                status = "active"
            detail = (
                "ocr_capture_diagnostic_required"
                if self._ocr_capture_diagnostic_required()
                else "attached_no_text_yet"
            )
        elif capture_attempted:
            self._reset_known_screen_stuck_tracking()
            if status == "starting":
                status = "active"
            detail = "capture_failed"
        elif self._writer.session_id and now - self._last_heartbeat_at >= float(
            self._config.ocr_reader_poll_interval_seconds
        ):
            if self._writer.emit_heartbeat(ts=utc_now_iso(now)):
                result.should_rescan = True
                self._last_heartbeat_at = now
            if status == "starting":
                status = "active"
            if detail == "starting_capture":
                detail = "attached_no_text_yet"

        self._runtime = self._build_runtime(
            status=status,
            detail=detail,
            plan=backend_plan,
            active_backend=active_backend,
            backend_detail_override=backend_detail_override,
            target=target,
            capture_stage=(
                self._aihong_stage if aihong_two_stage_enabled else OCR_CAPTURE_PROFILE_STAGE_DEFAULT
            ),
            capture_profile=runtime_profile.to_dict(),
            capture_profile_selection=runtime_capture_profile_selection,
            selection=selection,
            game_id=self._writer.game_id,
            session_id=self._writer.session_id,
            last_seq=self._writer.last_seq,
            last_event_ts=self._writer.last_event_ts,
        )
        self._set_poll_completed(
            poll_started_at,
            emitted=bool(emitted or observed_or_stable_emitted or screen_event_emitted),
        )
        result.runtime = self._runtime.to_dict()
        return result


    def _set_poll_completed(self, poll_started_at: float, *, emitted: bool = False) -> None:
        poll_completed_at = self._time_fn()
        self._runtime.last_poll_started_at = utc_now_iso(poll_started_at)
        self._runtime.last_poll_completed_at = utc_now_iso(poll_completed_at)
        self._runtime.last_poll_duration_seconds = max(0.0, poll_completed_at - poll_started_at)
        self._runtime.last_poll_emitted_event = bool(emitted)


    async def tick(
        self,
        *,
        bridge_sdk_available: bool,
        memory_reader_runtime: dict[str, Any],
    ) -> OcrReaderTickResult:
        now = self._time_fn()
        poll_started_at = now
        backend_plan_duration = 0.0
        window_scan_duration = 0.0
        result = OcrReaderTickResult(runtime=self._runtime.to_dict())
        self._visual_scene_committed = False
        self._scene_ordering_diagnostic = "none"
        self._runtime.scene_ordering_diagnostic = "none"

        preflight = await self._tick_preflight(
            now=now,
            bridge_sdk_available=bridge_sdk_available,
            memory_reader_runtime=memory_reader_runtime,
            result=result,
        )
        if preflight.should_return:
            self._set_poll_completed(poll_started_at)
            preflight.result.runtime = self._runtime.to_dict()
            return preflight.result
        result = preflight.result
        backend_plan = preflight.backend_plan
        backend_plan_duration = preflight.backend_plan_duration

        target_context = await self._prepare_tick_target_context(
            now=now,
            backend_plan=backend_plan,
            memory_reader_runtime=memory_reader_runtime,
            result=result,
        )
        if target_context.should_return:
            self._set_poll_completed(poll_started_at)
            target_context.result.runtime = self._runtime.to_dict()
            return target_context.result
        result = target_context.result
        target = target_context.target
        assert target is not None
        selection = target_context.selection
        profile = target_context.profile
        capture_profile_selection = target_context.capture_profile_selection
        legacy_geometryless_auto_target = target_context.legacy_geometryless_auto_target
        aihong_two_stage_enabled = target_context.aihong_two_stage_enabled
        started_session_this_tick = target_context.started_session
        window_scan_duration = target_context.window_scan_duration
        now = target_context.now

        emitted = False
        guard_blocked = False
        screen_classification = ScreenClassification()
        screen_event_emitted = False
        active_backend = backend_plan.primary
        backend_detail_override = ""
        runtime_profile = profile
        runtime_capture_profile_selection = capture_profile_selection
        event_seq_before_capture = int(self._writer.last_seq or 0)
        text_event_seq_before_capture = event_seq_before_capture

        def _should_discard_failed_session() -> bool:
            if started_session_this_tick:
                return int(self._writer.last_seq or 0) <= event_seq_before_capture
            return int(self._writer.last_seq or 0) <= 1

        after_advance_trigger_mode = (
            str(self._config.ocr_reader_trigger_mode or "").strip().lower()
            == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )
        foreground_advance_stable_grace_active = (
            after_advance_trigger_mode
            and float(self._foreground_advance_stable_until or 0.0) >= now
        )
        high_confidence_interval_capture = (
            not after_advance_trigger_mode
            and not legacy_geometryless_auto_target
            and str(selection.selection_detail or "")
            in {
                "foreground_window",
                "single_confident_candidate",
                "single_configured_profile_candidate",
            }
        )
        emit_observed_lines = self._should_emit_observed_lines_for_capture(
            after_advance_trigger_mode=after_advance_trigger_mode
        )
        line_repeat_threshold = (
            1
            if (
                (
                    after_advance_trigger_mode
                    and (
                        foreground_advance_stable_grace_active
                        or not legacy_geometryless_auto_target
                    )
                )
                or high_confidence_interval_capture
            )
            else None
        )
        choice_repeat_threshold = (
            1
            if (
                after_advance_trigger_mode
                and (
                    foreground_advance_stable_grace_active
                    or not legacy_geometryless_auto_target
                )
            )
            else 2
        )
        background_confirm_polls = 1 if after_advance_trigger_mode else _BACKGROUND_SCENE_CHANGE_CONFIRM_POLLS
        self._last_capture_timing = {
            "backend_plan_duration_seconds": backend_plan_duration,
            "window_scan_duration_seconds": window_scan_duration,
        }
        capture_attempted = False
        capture_completed = False
        capture_error = False
        try:
            capture_attempted = True
            self._record_capture_attempt(now=now)
            pause_error = self._background_capture_pause_error(target, now=now)
            # pause_error is a RuntimeError handled by the outer capture exception path;
            # that path records capture_error and resets transient Aihong menu state.
            if pause_error is not None:
                raise pause_error
            extraction = await self._capture_and_extract_text_with_timeout(
                target,
                profile,
                backend_plan,
                True,
                not after_advance_trigger_mode,
            )
            self._last_capture_timing.update(extraction.timing)
            capture_completed = True
            self._record_capture_completed(
                now=now,
                raw_text=extraction.text,
                image_hash=extraction.capture_image_hash,
            )
            self._record_capture_geometry(extraction)
            self._capture_backend_kind = extraction.capture_backend_kind
            self._capture_backend_detail = extraction.capture_backend_detail
            active_backend = extraction.backend if extraction.backend.kind else backend_plan.primary
            backend_detail_override = extraction.backend_detail
            result.warnings.extend(extraction.warnings)
            if self._handle_background_capture_backend_unsuitable(
                extraction,
                now=now,
                result=result,
            ):
                capture_error = True
            elif self._observe_background_hash(
                extraction.background_hash,
                now=now,
                confirm_polls=background_confirm_polls,
                defer_scene_emit=after_advance_trigger_mode,
            ):
                result.should_rescan = True
            if capture_error:
                pass
            elif extraction.text and _looks_like_self_ui_text(extraction.text):
                guard_blocked = True
                self._record_rejected_ocr_text(
                    extraction.text,
                    reason="self_ui_guard",
                    now=now,
                    capture_backend_kind=extraction.capture_backend_kind,
                )
                result.warnings.append("ocr_reader ignored text that looks like the N.E.K.O plugin UI")
                self._default_ocr_state.reset()
                self._ocr_lang_detector.reset()
                self._reset_aihong_menu_state()
                if (
                    not legacy_geometryless_auto_target
                    and _should_discard_failed_session()
                ):
                    self._writer.discard_session()
                    self._ocr_lang_detector.reset(clear_switch_time=True)
            else:
                self._record_accepted_ocr_text(extraction.text)
                screen_classification, screen_event_emitted = self._emit_screen_classification_from_extraction(
                    extraction,
                    target=target,
                    now=now,
                    image=getattr(extraction, "captured_image", None),
                )
                if screen_event_emitted:
                    result.should_rescan = True
                text_event_seq_before_capture = int(self._writer.last_seq or 0)
                if self._should_skip_dialogue_for_screen_classification(screen_classification):
                    self._default_ocr_state.reset()
                    self._reset_aihong_menu_state()
                elif aihong_two_stage_enabled:
                    if self._aihong_stage == _AIHONG_MENU_STAGE:
                        menu_result = self._consume_aihong_menu_stage_text(
                            extraction.text,
                            now=now,
                            boxes=extraction.boxes,
                            choice_bounds_metadata=_extraction_choice_bounds_metadata(extraction),
                            choice_repeat_threshold=choice_repeat_threshold,
                        )
                        emitted = bool(menu_result.emitted_kind)
                        if menu_result.has_menu_candidate:
                            self._aihong_menu_missing_polls = 0
                        else:
                            self._aihong_menu_missing_polls += 1
                            if (
                                extraction.text
                                and not _looks_like_noise_ocr_text(extraction.text)
                            ):
                                self._aihong_menu_ocr_state.reset()
                                self._reset_aihong_menu_state()
                            elif self._aihong_menu_missing_polls >= 2:
                                self._aihong_menu_ocr_state.reset()
                                self._reset_aihong_menu_state()
                    else:
                        dialogue_menu_choices = _coerce_aihong_menu_choices(
                            _stripped_ocr_lines(extraction.text)
                        )
                        dialogue_text_is_menu_status = _looks_like_aihong_menu_status_only_text(
                            extraction.text
                        )
                        dialogue_emitted = False
                        if dialogue_menu_choices:
                            dialogue_emitted = bool(
                                self._emit_choices_from_candidates(
                                    dialogue_menu_choices,
                                    now=now,
                                    state=self._aihong_menu_ocr_state,
                                    repeat_threshold=choice_repeat_threshold,
                                    choice_bounds=_aihong_choice_boxes(
                                        dialogue_menu_choices,
                                        extraction.boxes,
                                    ),
                                    choice_bounds_metadata=_extraction_choice_bounds_metadata(
                                        extraction
                                    ),
                                )
                            )
                            if not dialogue_emitted:
                                self._aihong_stage = _AIHONG_MENU_STAGE
                        elif not dialogue_text_is_menu_status:
                            dialogue_emitted = bool(
                                self._consume_ocr_text(
                                    extraction.text,
                                    now=now,
                                    state=self._default_ocr_state,
                                    allow_choices=False,
                                    emit_observed=emit_observed_lines,
                                    line_repeat_threshold=line_repeat_threshold,
                                    ocr_confidence=extraction.ocr_confidence,
                                    text_source=extraction.text_source,
                                    rapidocr_active=extraction.backend.kind == "rapidocr",
                                )
                            )
                        if (
                            not dialogue_emitted
                            and not dialogue_text_is_menu_status
                            and not dialogue_menu_choices
                            and self._should_attempt_followup_confirm(
                                extraction.text,
                                state=self._default_ocr_state,
                            )
                        ):
                            followup_extraction = await self._capture_followup_text(
                                target,
                                profile,
                                backend_plan,
                                elapsed_since_capture=extraction.timing.get("total_duration_seconds", 0.0),
                                collect_background_hash=True,
                                allow_separate_background_capture=not after_advance_trigger_mode,
                            )
                            self._last_capture_timing.update(followup_extraction.timing)
                            self._record_capture_completed(
                                now=self._time_fn(),
                                raw_text=followup_extraction.text,
                                image_hash=followup_extraction.capture_image_hash,
                            )
                            self._record_capture_geometry(followup_extraction)
                            self._capture_backend_kind = followup_extraction.capture_backend_kind
                            self._capture_backend_detail = followup_extraction.capture_backend_detail
                            active_backend = (
                                followup_extraction.backend
                                if followup_extraction.backend.kind
                                else active_backend
                            )
                            backend_detail_override = (
                                followup_extraction.backend_detail or backend_detail_override
                            )
                            result.warnings.extend(followup_extraction.warnings)
                            followup_now = self._time_fn()
                            if self._handle_background_capture_backend_unsuitable(
                                followup_extraction,
                                now=followup_now,
                                result=result,
                            ):
                                capture_error = True
                            elif followup_extraction.text and _looks_like_self_ui_text(followup_extraction.text):
                                guard_blocked = True
                                self._record_rejected_ocr_text(
                                    followup_extraction.text,
                                    reason="self_ui_guard",
                                    now=followup_now,
                                    capture_backend_kind=followup_extraction.capture_backend_kind,
                                )
                                self._default_ocr_state.reset()
                                self._ocr_lang_detector.reset()
                                self._reset_aihong_menu_state()
                                result.warnings.append(
                                    "ocr_reader ignored text that looks like the N.E.K.O plugin UI"
                                )
                            else:
                                self._record_accepted_ocr_text(followup_extraction.text)
                                if self._observe_followup_background_hash(
                                    followup_extraction,
                                    now=followup_now,
                                    confirm_polls=background_confirm_polls,
                                    defer_scene_emit=after_advance_trigger_mode,
                                ):
                                    result.should_rescan = True
                                dialogue_emitted = bool(
                                    self._consume_ocr_text(
                                        followup_extraction.text,
                                        now=followup_now,
                                        state=self._default_ocr_state,
                                        allow_choices=False,
                                        emit_observed=emit_observed_lines,
                                        line_repeat_threshold=line_repeat_threshold,
                                        ocr_confidence=followup_extraction.ocr_confidence,
                                        text_source=followup_extraction.text_source,
                                        rapidocr_active=followup_extraction.backend.kind == "rapidocr",
                                    )
                                )
                                if dialogue_emitted:
                                    now = followup_now
                        emitted = dialogue_emitted
                        if capture_error:
                            emitted = False
                        elif dialogue_emitted:
                            self._aihong_dialogue_idle_polls = 0
                            self._aihong_menu_missing_polls = 0
                            if dialogue_menu_choices:
                                self._aihong_stage = _AIHONG_MENU_STAGE
                            else:
                                self._aihong_menu_ocr_state.reset()
                        elif int(self._writer.last_seq or 0) > event_seq_before_capture:
                            self._aihong_dialogue_idle_polls = 0
                            self._aihong_menu_missing_polls = 0
                            self._aihong_menu_ocr_state.reset()
                        else:
                            if dialogue_text_is_menu_status or dialogue_menu_choices:
                                self._aihong_dialogue_idle_polls = max(
                                    self._aihong_dialogue_idle_polls,
                                    1,
                                )
                            else:
                                self._aihong_dialogue_idle_polls += 1
                            if (
                                not dialogue_menu_choices
                                and
                                (
                                    not after_advance_trigger_mode
                                    or legacy_geometryless_auto_target
                                    or dialogue_text_is_menu_status
                                )
                                and (
                                    dialogue_text_is_menu_status
                                    or self._aihong_dialogue_idle_polls
                                    >= (
                                        1
                                        if (
                                            after_advance_trigger_mode
                                            and not legacy_geometryless_auto_target
                                        )
                                        else 2
                                    )
                                )
                            ):
                                menu_profile_selection = self._capture_profile_selection_for_target(
                                    target,
                                    stage=_AIHONG_MENU_STAGE,
                                )
                                menu_profile = menu_profile_selection.profile
                                menu_extraction = await self._capture_and_extract_text_with_timeout(
                                    target,
                                    menu_profile,
                                    backend_plan,
                                    True,
                                    not after_advance_trigger_mode,
                                )
                                self._last_capture_timing.update(menu_extraction.timing)
                                self._record_capture_completed(
                                    now=self._time_fn(),
                                    raw_text=menu_extraction.text,
                                    image_hash=menu_extraction.capture_image_hash,
                                )
                                self._record_capture_geometry(menu_extraction)
                                self._capture_backend_kind = menu_extraction.capture_backend_kind
                                self._capture_backend_detail = menu_extraction.capture_backend_detail
                                active_backend = (
                                    menu_extraction.backend
                                    if menu_extraction.backend.kind
                                    else active_backend
                                )
                                backend_detail_override = (
                                    menu_extraction.backend_detail or backend_detail_override
                                )
                                result.warnings.extend(menu_extraction.warnings)
                                menu_now = self._time_fn()
                                if self._handle_background_capture_backend_unsuitable(
                                    menu_extraction,
                                    now=menu_now,
                                    result=result,
                                ):
                                    capture_error = True
                                elif menu_extraction.text and _looks_like_self_ui_text(menu_extraction.text):
                                    guard_blocked = True
                                    self._record_rejected_ocr_text(
                                        menu_extraction.text,
                                        reason="self_ui_guard",
                                        now=menu_now,
                                        capture_backend_kind=menu_extraction.capture_backend_kind,
                                    )
                                    self._default_ocr_state.reset()
                                    self._ocr_lang_detector.reset()
                                    self._reset_aihong_menu_state()
                                    result.warnings.append(
                                        "ocr_reader ignored text that looks like the N.E.K.O plugin UI"
                                    )
                                else:
                                    self._record_accepted_ocr_text(menu_extraction.text)
                                    menu_result = self._consume_aihong_menu_stage_text(
                                        menu_extraction.text,
                                        now=now,
                                        boxes=menu_extraction.boxes,
                                        choice_bounds_metadata=_extraction_choice_bounds_metadata(
                                            menu_extraction
                                        ),
                                        choice_repeat_threshold=choice_repeat_threshold,
                                    )
                                    if menu_result.has_menu_candidate:
                                        self._aihong_menu_missing_polls = 0
                                        runtime_profile = menu_profile
                                        runtime_capture_profile_selection = menu_profile_selection
                                    else:
                                        if (
                                            menu_extraction.text
                                            and not _looks_like_noise_ocr_text(menu_extraction.text)
                                        ):
                                            self._aihong_menu_ocr_state.reset()
                                    if menu_result.emitted_kind == "choices":
                                        emitted = True
                                        self._aihong_stage = _AIHONG_MENU_STAGE
                                        self._aihong_menu_missing_polls = 0
                                        runtime_profile = menu_profile
                                        runtime_capture_profile_selection = menu_profile_selection
                                    elif menu_result.has_menu_candidate:
                                        self._aihong_stage = _AIHONG_MENU_STAGE
                else:
                    emitted = bool(
                        self._consume_ocr_text(
                            extraction.text,
                            now=now,
                            emit_observed=emit_observed_lines,
                            line_repeat_threshold=line_repeat_threshold,
                            ocr_confidence=extraction.ocr_confidence,
                            text_source=extraction.text_source,
                            rapidocr_active=extraction.backend.kind == "rapidocr",
                        )
                    )
                    if (
                        not emitted
                        and self._should_attempt_followup_confirm(
                            extraction.text,
                            state=self._default_ocr_state,
                        )
                    ):
                        followup_extraction = await self._capture_followup_text(
                            target,
                            profile,
                            backend_plan,
                            elapsed_since_capture=extraction.timing.get("total_duration_seconds", 0.0),
                            collect_background_hash=True,
                            allow_separate_background_capture=not after_advance_trigger_mode,
                        )
                        self._last_capture_timing.update(followup_extraction.timing)
                        self._record_capture_completed(
                            now=self._time_fn(),
                            raw_text=followup_extraction.text,
                            image_hash=followup_extraction.capture_image_hash,
                        )
                        self._record_capture_geometry(followup_extraction)
                        self._capture_backend_kind = followup_extraction.capture_backend_kind
                        self._capture_backend_detail = followup_extraction.capture_backend_detail
                        active_backend = (
                            followup_extraction.backend
                            if followup_extraction.backend.kind
                            else active_backend
                        )
                        backend_detail_override = (
                            followup_extraction.backend_detail or backend_detail_override
                        )
                        result.warnings.extend(followup_extraction.warnings)
                        followup_now = self._time_fn()
                        if self._handle_background_capture_backend_unsuitable(
                            followup_extraction,
                            now=followup_now,
                            result=result,
                        ):
                            capture_error = True
                        elif followup_extraction.text and _looks_like_self_ui_text(followup_extraction.text):
                            guard_blocked = True
                            self._record_rejected_ocr_text(
                                followup_extraction.text,
                                reason="self_ui_guard",
                                now=followup_now,
                                capture_backend_kind=followup_extraction.capture_backend_kind,
                            )
                            self._default_ocr_state.reset()
                            self._ocr_lang_detector.reset()
                            self._reset_aihong_menu_state()
                            result.warnings.append(
                                "ocr_reader ignored text that looks like the N.E.K.O plugin UI"
                            )
                        else:
                            self._record_accepted_ocr_text(followup_extraction.text)
                            if self._observe_followup_background_hash(
                                followup_extraction,
                                now=followup_now,
                                confirm_polls=background_confirm_polls,
                                defer_scene_emit=after_advance_trigger_mode,
                            ):
                                result.should_rescan = True
                            emitted = bool(
                                self._consume_ocr_text(
                                    followup_extraction.text,
                                    now=followup_now,
                                    emit_observed=emit_observed_lines,
                                    line_repeat_threshold=line_repeat_threshold,
                                    ocr_confidence=followup_extraction.ocr_confidence,
                                    text_source=followup_extraction.text_source,
                                    rapidocr_active=followup_extraction.backend.kind == "rapidocr",
                                )
                            )
                            if emitted:
                                now = followup_now
        except _CaptureStillRunning as exc:
            self._logger.debug("ocr_reader tick skipped (backpressure): {}", exc)
            result.warnings.append(f"ocr_reader tick skipped: {exc}")
            self._last_capture_error = ""
            self._runtime.last_capture_error = ""
            self._runtime.detail = "capture_backpressure"
            capture_attempted = False
        except _CaptureTimedOut as exc:
            self._logger.warning("ocr_reader capture/OCR timed out: {}", exc)
            capture_error = True
            self._record_capture_error(now=now, error=exc)
            self._ocr_lang_detector.reset()
            self._reset_aihong_menu_state()
            if _should_discard_failed_session():
                self._writer.discard_session()
                self._ocr_lang_detector.reset(clear_switch_time=True)
            result.warnings.append(f"ocr_reader capture timed out: {exc}")
        except Exception as exc:
            self._logger.warning("ocr_reader capture/OCR failed: {}", exc)
            capture_error = True
            self._record_capture_error(now=now, error=exc)
            self._ocr_lang_detector.reset()
            self._reset_aihong_menu_state()
            if _should_discard_failed_session():
                self._writer.discard_session()
                self._ocr_lang_detector.reset(clear_switch_time=True)
            result.warnings.append(f"ocr_reader capture failed: {exc}")

        return self._finalize_tick_result(
            result=result,
            now=now,
            poll_started_at=poll_started_at,
            backend_plan=backend_plan,
            active_backend=active_backend,
            backend_detail_override=backend_detail_override,
            target=target,
            aihong_two_stage_enabled=aihong_two_stage_enabled,
            runtime_profile=runtime_profile,
            runtime_capture_profile_selection=runtime_capture_profile_selection,
            selection=selection,
            emitted=emitted,
            guard_blocked=guard_blocked,
            screen_classification=screen_classification,
            screen_event_emitted=screen_event_emitted,
            capture_attempted=capture_attempted,
            capture_completed=capture_completed,
            capture_error=capture_error,
            text_event_seq_before_capture=text_event_seq_before_capture,
            foreground_advance_stable_grace_active=foreground_advance_stable_grace_active,
        )


    async def _end_session_if_needed(self, now: float) -> None:
        if self._writer.session_id:
            self._writer.end_session(ts=utc_now_iso(now))
            self._attached_window = None
            self._ocr_lang_detector.reset(clear_switch_time=True)
            self._reset_default_ocr_state()
            self._reset_aihong_menu_state()
