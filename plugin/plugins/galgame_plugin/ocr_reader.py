from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
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
    DEFAULT_VISION_CLASSIFIER_MODEL_DIR,
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
    inspect_rapidocr_installation as _inspect_rapidocr_installation,
    load_rapidocr_runtime as _load_rapidocr_runtime,
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

from .ocr_runtime_types import *
from .ocr_backend_interface import *

from .ocr_capture_backends import *
from .ocr_rapidocr_backend import *
from .ocr_input_hooks import *
from .ocr_window_scanner import (
    _classify_window_candidate, _default_window_scanner, _foreground_matches_target,
    _foreground_window_handle, _is_confident_auto_window, _is_legacy_geometryless_auto_window,
    _is_windows_platform, _platform_scan_windows, _root_window_handle, _window_handle_from_point,
    _window_process_id, _window_process_name, _window_sort_key,
)

from .ocr_bridge_writer import *
from .ocr_manager_capture import CaptureMixin
from .ocr_manager_text import TextMixin
from .ocr_manager_poll import PollMixin
from .ocr_manager_observe import ObserveMixin
from .ocr_manager_runtime import RuntimeMixin


def inspect_rapidocr_installation(**kwargs):
    kwargs.setdefault("plugin_id", "galgame_plugin")
    return _inspect_rapidocr_installation(**kwargs)


def load_rapidocr_runtime(**kwargs):
    kwargs.setdefault("plugin_id", "galgame_plugin")
    return _load_rapidocr_runtime(**kwargs)


def _coerce_vision_input_size(value: object) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            width = max(16, int(value[0]))
            height = max(16, int(value[1]))
            return width, height
        except (TypeError, ValueError):
            pass
    return 224, 224


class OcrReaderManager(
    CaptureMixin,
    TextMixin,
    PollMixin,
    ObserveMixin,
    RuntimeMixin,
):
    def __init__(
        self,
        *,
        logger,
        config: GalgameConfig,
        time_fn: Callable[[], float] | None = None,
        platform_fn: Callable[[], bool] | None = None,
        window_scanner: Callable[[], list[DetectedGameWindow]] | None = None,
        capture_backend: CaptureBackend | None = None,
        ocr_backend: OcrBackend | None = None,
        writer: OcrReaderBridgeWriter | None = None,
        rapidocr_lang_changed_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._logger = logger
        self._config = config
        self._time_fn = time_fn or time.time
        platform_checker = platform_fn or _is_windows_platform
        is_windows_runtime = bool(platform_checker())
        self._platform_fn = platform_checker
        # Windows keeps _default_window_scanner (identity-preserving for tests).
        # macOS/Linux use the cross-platform dispatcher in capture_platform.
        self._window_scanner = window_scanner or (
            _default_window_scanner if is_windows_runtime else _platform_scan_windows
        )
        self._custom_capture_backend = capture_backend is not None
        # Win32CaptureBackend works on all platforms: its _build_backends()
        # filters out dxcam/printwindow on non-Windows. Linux X11 keeps the
        # screen-pixel chain plus the optional Electron bridge fallback, while
        # Wayland automatic selections use the Electron portal path directly.
        self._capture_backend = capture_backend or Win32CaptureBackend(
            logger=logger,
            selection=config.ocr_reader_capture_backend,
        )
        self._ocr_backend = ocr_backend
        self._custom_ocr_backend = ocr_backend is not None
        self._writer = writer or OcrReaderBridgeWriter(
            bridge_root=config.bridge_root,
            time_fn=self._time_fn,
            logger=logger,
        )
        self._rapidocr_lang_changed_callback = rapidocr_lang_changed_callback
        self._runtime = OcrReaderRuntime(enabled=config.ocr_reader_enabled)
        self._capture_profiles: dict[str, ParsedOcrCaptureProcessConfig] = {}
        self._last_memory_reader_text_at = 0.0
        self._last_seen_memory_reader_game_id = ""
        self._last_seen_memory_reader_session_id = ""
        self._last_seen_memory_reader_text_seq = 0
        self._last_heartbeat_at = 0.0
        self._attached_window: DetectedGameWindow | None = None
        self._default_ocr_state = _StableOcrTextState()
        self._aihong_menu_ocr_state = _StableOcrTextState()
        self._aihong_stage = _AIHONG_DIALOGUE_STAGE
        self._aihong_dialogue_idle_polls = 0
        self._aihong_menu_missing_polls = 0
        self._manual_target = OcrWindowTarget()
        self._locked_target = OcrWindowTarget()
        self._last_detected_windows: list[DetectedGameWindow] = []
        self._last_eligible_windows: list[DetectedGameWindow] = []
        self._last_excluded_windows: list[DetectedGameWindow] = []
        self._last_selection = WindowSelectionResult(manual_target=self._manual_target)
        self._advance_speed = ADVANCE_SPEED_MEDIUM
        self._consecutive_no_text_polls = 0
        self._last_observed_at = ""
        self._last_capture_attempt_at = ""
        self._last_capture_completed_at = ""
        self._last_capture_error = ""
        self._last_raw_ocr_text = ""
        self._last_rejected_ocr_text = ""
        self._last_rejected_ocr_reason = ""
        self._last_rejected_ocr_at = ""
        self._last_rejected_capture_backend = ""
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""
        self._last_observed_line: dict[str, Any] = {}
        self._last_stable_line: dict[str, Any] = {}
        self._last_capture_image_hash = ""
        self._last_capture_source_size: dict[str, float] = {}
        self._last_capture_rect: dict[str, float] = {}
        self._last_capture_window_rect: dict[str, float] = {}
        self._last_capture_timing: dict[str, Any] = {}
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
        self._last_scene_change_committed_ts: float = 0.0
        self._visual_scene_committed = False
        self._scene_ordering_diagnostic = "none"
        self._background_capture_pause_until = 0.0
        self._background_capture_pause_reason = ""
        self._recommended_capture_profile = {}
        self._clear_vision_snapshot()
        self._last_screen_classification_type = ""
        self._last_screen_classification_streak = 0
        self._known_screen_stuck_since: float | None = None
        self._last_known_screen_type = ""
        self._known_screen_skip_bypass_until = 0.0
        self._known_screen_skip_bypass_type = ""
        self._last_screen_awareness_capture_at = 0.0
        self._screen_awareness_sample_count = 0
        self._screen_awareness_sample_last_path = ""
        self._screen_awareness_sample_last_error = ""
        self._screen_awareness_model_cache_key: tuple[str, float] | None = None
        self._screen_awareness_model_payload: dict[str, Any] | None = None
        self._screen_awareness_model_detail = "disabled"
        self._screen_awareness_model_last_stage = ""
        self._screen_awareness_model_last_confidence = 0.0
        self._screen_awareness_model_last_latency_seconds = 0.0
        self._latest_vision_snapshot: dict[str, Any] = {}
        self._latest_vision_snapshot_base64 = ""
        self._recommended_capture_profile: dict[str, Any] = {}
        self._wheel_monitor = _MouseWheelMonitor(
            time_fn=self._time_fn,
            logger=self._logger,
        )
        self._last_consumed_wheel_seq = 0
        self._foreground_advance_stable_until = 0.0
        if self._foreground_advance_monitor_should_autostart():
            self.start_foreground_advance_monitor()
        self._capture_backend_kind = str(getattr(self._capture_backend, "selection", "custom"))
        self._capture_backend_detail = ""
        self._rapidocr_backend_cache_key: tuple[str, str, str, str, str] | None = None
        self._rapidocr_backend_cache: RapidOcrBackend | None = None
        self._ocr_lang_detector = _OcrLangDetector()
        self._ocr_lang_cooldown_seconds = 60.0
        self.vision_classifier = None
        self._vision_classifier_detail = "disabled"
        self._vision_classifier_last_label = ""
        self._vision_classifier_last_confidence = 0.0
        self._vision_classifier_last_latency_ms = 0.0
        self._vision_classifier_tick_count = 0
        self._initialize_vision_classifier()
        self._backend_plan_cache_key: tuple[str, ...] | None = None
        self._backend_plan_cache_at = 0.0
        self._backend_plan_cache: SelectedOcrBackendPlan | None = None
        self._capture_worker_lock = threading.Lock()
        self._capture_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="galgame-ocr-capture",
        )
        self._capture_future: Future[OcrExtractionResult] | None = None
        self._capture_future_started_at = 0.0
        self._capture_future_timed_out = False
        self._abandoned_capture_workers: list[
            tuple[ThreadPoolExecutor, Future[OcrExtractionResult]]
        ] = []
        self._window_inventory_cache_at = 0.0
        self._window_inventory_cache: list[DetectedGameWindow] = []

    def _initialize_vision_classifier(self) -> None:
        self.vision_classifier = None
        self._vision_classifier_detail = "disabled"
        self._vision_classifier_last_label = ""
        self._vision_classifier_last_confidence = 0.0
        self._vision_classifier_last_latency_ms = 0.0
        self._vision_classifier_tick_count = 0
        if not bool(getattr(self._config, "vision_classifier_enabled", False)):
            return
        try:
            from .core.vision import VisionModelLoader, VisionScreenClassifier

            model_dir = self._resolve_vision_model_dir(
                str(getattr(self._config, "vision_classifier_model_dir", "") or "")
            )
            input_size = _coerce_vision_input_size(
                getattr(self._config, "vision_classifier_input_size", [224, 224])
            )
            classifier = VisionScreenClassifier(
                VisionModelLoader(model_dir),
                input_size=input_size,
                latency_check_ms=float(
                    getattr(self._config, "vision_classifier_inference_timeout_ms", 200.0)
                    or 200.0
                ),
            )
            model_name = str(
                getattr(self._config, "vision_classifier_model_name", "v1_galgame")
                or "v1_galgame"
            )
            if classifier.load(model_name):
                self.vision_classifier = classifier
                self._vision_classifier_detail = "loaded"
                self._log_info(
                    "galgame vision classifier loaded: model_dir={} model_name={}",
                    str(model_dir),
                    model_name,
                )
            else:
                self._vision_classifier_detail = "model_unavailable"
                self._log_warning(
                    "galgame vision classifier unavailable: model_dir={} model_name={}",
                    str(model_dir),
                    model_name,
                )
        except ImportError as exc:
            self._vision_classifier_detail = "dependency_unavailable"
            self._log_warning("galgame vision classifier dependency unavailable: {}", exc)
        except Exception as exc:
            self._vision_classifier_detail = "load_failed"
            self._log_warning("galgame vision classifier failed to load: {}", exc)

    @staticmethod
    def _vision_classifier_config_key(config: GalgameConfig) -> tuple[bool, str, str, tuple[int, int], float]:
        return (
            bool(getattr(config, "vision_classifier_enabled", False)),
            str(getattr(config, "vision_classifier_model_dir", "") or ""),
            str(getattr(config, "vision_classifier_model_name", "") or ""),
            _coerce_vision_input_size(getattr(config, "vision_classifier_input_size", [224, 224])),
            float(getattr(config, "vision_classifier_inference_timeout_ms", 200.0) or 200.0),
        )

    @staticmethod
    def _resolve_vision_model_dir(raw_path: str) -> Path:
        path = Path(raw_path or DEFAULT_VISION_CLASSIFIER_MODEL_DIR).expanduser()
        if path.is_absolute():
            return path
        repo_root = Path(__file__).resolve().parents[3]
        return (repo_root / path).resolve()

    def close(self) -> None:
        self._release_rapidocr_backend()
        classifier = self.vision_classifier
        self.vision_classifier = None
        close_classifier = getattr(classifier, "close", None)
        if callable(close_classifier):
            close_classifier()
        try:
            self._stop_foreground_advance_monitor(join_timeout=1.0)
        except Exception as exc:
            warning = getattr(getattr(self, "_logger", None), "warning", None)
            if callable(warning):
                try:
                    warning("ocr_reader foreground advance monitor shutdown failed: {}", exc)
                except Exception:
                    pass
        try:
            self._shutdown_capture_worker()
        except Exception as exc:
            warning = getattr(getattr(self, "_logger", None), "warning", None)
            if callable(warning):
                try:
                    warning("ocr_reader capture worker shutdown failed: {}", exc)
                except Exception:
                    pass

    def __enter__(self) -> "OcrReaderManager":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False

    def _foreground_advance_monitor_enabled(self) -> bool:
        return (
            bool(self._config.ocr_reader_enabled)
            and self._platform_fn()
            and getattr(self._config, "reader_mode", READER_MODE_AUTO) != READER_MODE_MEMORY
            and self._config.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
        )

    def _foreground_advance_monitor_should_autostart(self) -> bool:
        return (
            self._foreground_advance_monitor_enabled()
            and not self._custom_capture_backend
            and not self._custom_ocr_backend
        )

    def _stop_foreground_advance_monitor(self, *, join_timeout: float = 1.0) -> None:
        stop = getattr(self._wheel_monitor, "stop", None)
        if callable(stop):
            try:
                stop(join_timeout=join_timeout)
            except TypeError:
                raise
        self._runtime.foreground_advance_monitor_running = False
        self._runtime.foreground_advance_last_seq = 0

    def _reset_memory_reader_text_progress_tracking(self) -> None:
        self._last_memory_reader_text_at = 0.0
        self._last_seen_memory_reader_game_id = ""
        self._last_seen_memory_reader_session_id = ""
        self._last_seen_memory_reader_text_seq = 0

    def start_foreground_advance_monitor(self) -> bool:
        if not self._foreground_advance_monitor_should_autostart():
            self._stop_foreground_advance_monitor()
            return False
        started = bool(self._wheel_monitor.start())
        self._runtime.foreground_advance_monitor_running = self._wheel_monitor.is_running()
        self._runtime.foreground_advance_last_seq = self._wheel_monitor.last_seq()
        return started

    def update_config(self, config: GalgameConfig) -> None:
        old_backend_plan_key = self._backend_plan_config_key(self._config)
        old_auto_detect_lang = bool(getattr(self._config, "rapidocr_auto_detect_lang", False))
        old_vision_key = self._vision_classifier_config_key(self._config)
        self._config = config
        self._runtime.enabled = config.ocr_reader_enabled
        if old_vision_key != self._vision_classifier_config_key(config):
            classifier = self.vision_classifier
            close_classifier = getattr(classifier, "close", None)
            if callable(close_classifier):
                close_classifier()
            self._initialize_vision_classifier()
        if not bool(config.llm_vision_enabled):
            self._clear_vision_snapshot()
        if float(getattr(config, "ocr_reader_known_screen_timeout_seconds", 0.0) or 0.0) <= 0.0:
            self._reset_known_screen_stuck_tracking()
        backend_plan_key = self._backend_plan_config_key(config)
        if old_backend_plan_key != backend_plan_key or self._backend_plan_cache_key != backend_plan_key:
            self._backend_plan_cache_key = None
            self._backend_plan_cache_at = 0.0
            self._backend_plan_cache = None
            self._release_rapidocr_backend()
            self._ocr_lang_detector.reset(clear_switch_time=True)
        elif old_auto_detect_lang != bool(getattr(config, "rapidocr_auto_detect_lang", False)):
            self._ocr_lang_detector.reset(clear_switch_time=True)
        if not self._custom_capture_backend:
            current_selection = str(getattr(self._capture_backend, "selection", "") or "")
            if current_selection != config.ocr_reader_capture_backend:
                self._capture_backend = Win32CaptureBackend(
                    logger=self._logger,
                    selection=config.ocr_reader_capture_backend,
                )
                self._capture_backend_kind = str(
                    getattr(self._capture_backend, "selection", "custom")
                )
                self._capture_backend_detail = ""
                self.reset_capture_runtime_diagnostics()
        if self._foreground_advance_monitor_should_autostart():
            self.start_foreground_advance_monitor()
        else:
            self._stop_foreground_advance_monitor()
        if self._writer.bridge_root != config.bridge_root:
            self._writer = OcrReaderBridgeWriter(
                bridge_root=config.bridge_root,
                time_fn=self._time_fn,
            )

    def update_advance_speed(self, advance_speed: str) -> None:
        normalized = str(advance_speed or "").strip().lower()
        self._advance_speed = normalized if normalized in ADVANCE_SPEEDS else ADVANCE_SPEED_MEDIUM

    def _reset_known_screen_stuck_tracking(self) -> None:
        self._known_screen_stuck_since = None
        self._last_known_screen_type = ""
        self._known_screen_skip_bypass_until = 0.0
        self._known_screen_skip_bypass_type = ""

    def _record_known_screen_classification(
        self,
        classification: ScreenClassification,
        *,
        now: float,
        result: OcrReaderTickResult,
    ) -> bool:
        timeout_seconds = float(
            getattr(self._config, "ocr_reader_known_screen_timeout_seconds", 0.0) or 0.0
        )
        if timeout_seconds <= 0.0:
            self._reset_known_screen_stuck_tracking()
            return False

        current_type = str(classification.screen_type or "")
        if not current_type:
            self._reset_known_screen_stuck_tracking()
            return False

        if current_type == self._last_known_screen_type:
            if self._known_screen_stuck_since is None:
                self._known_screen_stuck_since = now
                return False
            if now - self._known_screen_stuck_since < timeout_seconds:
                return False

            result.should_rescan = True
            self._known_screen_stuck_since = None
            self._last_known_screen_type = ""
            if current_type == OCR_CAPTURE_PROFILE_STAGE_TITLE:
                self._known_screen_skip_bypass_until = now + _KNOWN_SCREEN_SKIP_BYPASS_SECONDS
                self._known_screen_skip_bypass_type = current_type
            else:
                self._known_screen_skip_bypass_until = 0.0
                self._known_screen_skip_bypass_type = ""
            return True

        self._last_known_screen_type = current_type
        self._known_screen_stuck_since = now
        self._known_screen_skip_bypass_until = 0.0
        self._known_screen_skip_bypass_type = ""
        return False

    @staticmethod
    def _is_supported_foreground_advance_event(event: _MouseWheelEvent) -> bool:
        kind = str(getattr(event, "kind", "") or "")
        if kind == "wheel" and int(getattr(event, "delta", 0) or 0) >= 0:
            return False
        if kind == "key":
            return int(getattr(event, "key_code", 0) or 0) in _KEYBOARD_ADVANCE_VK_CODES
        return kind in {"wheel", "left_click"}

    def _target_from_foreground_advance_events(
        self,
        events: list[_MouseWheelEvent],
    ) -> tuple[DetectedGameWindow | None, str]:
        if not any(self._is_supported_foreground_advance_event(event) for event in events):
            return None, "no_supported_event"
        eligible_windows, _excluded_windows = self._scan_window_inventory()
        if not eligible_windows:
            return None, "no_eligible_window"
        for event in events:
            if not self._is_supported_foreground_advance_event(event):
                continue
            for source, hwnd in (
                ("foreground", int(getattr(event, "foreground_hwnd", 0) or 0)),
                ("point", int(getattr(event, "point_hwnd", 0) or 0)),
            ):
                if not hwnd:
                    continue
                for candidate in eligible_windows:
                    matched, reason = _foreground_matches_target(hwnd, candidate)
                    if matched:
                        return candidate, f"event_{source}_{reason}"
        return None, "event_background"

    def consume_foreground_advance_inputs(self) -> ForegroundAdvanceConsumeResult:
        if not self._foreground_advance_monitor_enabled():
            self._stop_foreground_advance_monitor()
            return ForegroundAdvanceConsumeResult()
        self._wheel_monitor.ensure_running()
        self._runtime.foreground_advance_monitor_running = self._wheel_monitor.is_running()
        self._runtime.foreground_advance_last_seq = self._wheel_monitor.last_seq()
        self._runtime.foreground_advance_consumed_seq = self._last_consumed_wheel_seq
        events = self._wheel_monitor.events_after(self._last_consumed_wheel_seq)
        self._runtime.foreground_advance_monitor_running = self._wheel_monitor.is_running()
        self._runtime.foreground_advance_last_seq = self._wheel_monitor.last_seq()
        if not events:
            return ForegroundAdvanceConsumeResult()
        target, _detail = self._foreground_refresh_target()
        if target is None:
            target = self._attached_window
        if target is None and (
            self._runtime.target_hwnd
            or self._runtime.pid
            or self._runtime.effective_process_name
            or self._runtime.process_name
        ):
            target = DetectedGameWindow(
                hwnd=int(self._runtime.target_hwnd or 0),
                title=str(self._runtime.effective_window_title or self._runtime.window_title or ""),
                process_name=str(
                    self._runtime.effective_process_name
                    or self._runtime.process_name
                    or ""
                ),
                pid=int(self._runtime.pid or 0),
                width=int(self._runtime.width or 0),
                height=int(self._runtime.height or 0),
            )
        if target is None:
            target, _detail = self._target_from_foreground_advance_events(events)
        if target is None:
            return ForegroundAdvanceConsumeResult()
        triggered = False
        max_seq = self._last_consumed_wheel_seq
        last_kind = ""
        last_delta = 0
        last_matched = False
        last_match_reason = ""
        matched_count = 0
        first_event_ts = float(getattr(events[0], "ts", 0.0) or 0.0)
        last_event_ts = float(getattr(events[-1], "ts", 0.0) or 0.0)
        for event in events:
            max_seq = max(max_seq, int(event.seq or 0))
            last_kind = str(event.kind or "")
            last_delta = int(event.delta or 0)
            if event.kind == "wheel" and event.delta >= 0:
                if not last_matched:
                    last_match_reason = "ignored_wheel_up"
                continue
            if event.kind == "key" and int(getattr(event, "key_code", 0) or 0) not in _KEYBOARD_ADVANCE_VK_CODES:
                if not last_matched:
                    last_match_reason = "ignored_key"
                continue
            if event.kind not in {"wheel", "left_click", "key"}:
                if not last_matched:
                    last_match_reason = "ignored_event_kind"
                continue
            is_target_foreground, foreground_reason = _foreground_matches_target(
                event.foreground_hwnd,
                target,
            )
            is_target_under_pointer, point_reason = _foreground_matches_target(
                event.point_hwnd,
                target,
            )
            if is_target_foreground or is_target_under_pointer:
                triggered = True
                matched_count += 1
                last_matched = True
                last_match_reason = (
                    f"foreground_{foreground_reason}"
                    if is_target_foreground
                    else f"point_{point_reason}"
                )
            else:
                if not last_matched:
                    last_match_reason = f"background:{foreground_reason}/{point_reason}"
        self._last_consumed_wheel_seq = max_seq
        self._runtime.foreground_advance_consumed_seq = self._last_consumed_wheel_seq
        self._runtime.foreground_advance_last_kind = last_kind
        self._runtime.foreground_advance_last_delta = last_delta
        self._runtime.foreground_advance_last_matched = last_matched
        self._runtime.foreground_advance_last_match_reason = last_match_reason
        detected_at = self._time_fn()
        last_event_age_seconds = (
            max(0.0, detected_at - last_event_ts) if last_event_ts > 0.0 else 0.0
        )
        coalesced_count = max(0, matched_count - 1)
        self._runtime.foreground_advance_consumed_count = len(events)
        self._runtime.foreground_advance_matched_count = matched_count
        self._runtime.foreground_advance_coalesced_count = coalesced_count
        self._runtime.foreground_advance_first_event_ts = first_event_ts
        self._runtime.foreground_advance_last_event_ts = last_event_ts
        self._runtime.foreground_advance_detected_at = detected_at
        self._runtime.foreground_advance_last_event_age_seconds = last_event_age_seconds
        if triggered:
            self._remember_locked_target(target)
            self._foreground_advance_stable_until = max(
                float(self._foreground_advance_stable_until or 0.0),
                detected_at + _FOREGROUND_ADVANCE_STABLE_GRACE_SECONDS,
            )
        return ForegroundAdvanceConsumeResult(
            triggered=triggered,
            matched_count=matched_count,
            consumed_count=len(events),
            first_event_ts=first_event_ts,
            last_event_ts=last_event_ts,
            detected_at=detected_at,
            last_event_age_seconds=last_event_age_seconds,
            last_kind=last_kind,
            last_delta=last_delta,
            last_matched=last_matched,
            last_match_reason=last_match_reason,
            coalesced=coalesced_count > 0,
            coalesced_count=coalesced_count,
        )

    def consume_foreground_advance_input(self) -> bool:
        return self.consume_foreground_advance_inputs().triggered

    def consume_foreground_wheel_down(self) -> bool:
        return self.consume_foreground_advance_input()
