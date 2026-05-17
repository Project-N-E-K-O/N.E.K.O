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
from .rapidocr_support import (
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

def _default_window_scanner() -> list[DetectedGameWindow]:
    try:
        import win32gui
        import win32process
    except ImportError:
        return []

    window_records: list[dict[str, Any]] = []
    foreground_hwnd = _foreground_window_handle()

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        is_minimized = bool(win32gui.IsIconic(hwnd))
        rect = _run_with_thread_dpi_awareness(
            lambda: tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
        )
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        title = win32gui.GetWindowText(hwnd)
        if not title or len(title) < 2:
            return
        class_name = win32gui.GetClassName(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        area = width * height
        window_records.append(
            {
                "hwnd": hwnd,
                "title": title,
                "pid": int(pid),
                "class_name": class_name,
                "width": max(0, width),
                "height": max(0, height),
                "area": max(0, area),
                "is_minimized": is_minimized,
            }
        )

    win32gui.EnumWindows(callback, None)

    process_metadata: dict[int, tuple[str, str]] = {}
    if psutil is not None:
        for pid in sorted({int(record["pid"]) for record in window_records if int(record["pid"]) > 0}):
            try:
                proc = psutil.Process(pid)
                process_metadata[pid] = (str(proc.name() or ""), str(proc.exe() or ""))
            except Exception:
                process_metadata[pid] = ("", "")

    results: list[DetectedGameWindow] = []
    for record in window_records:
        pid = int(record["pid"])
        process_name, exe_path = process_metadata.get(pid, ("", ""))
        candidate = DetectedGameWindow(
            hwnd=int(record["hwnd"]),
            title=str(record["title"]),
            process_name=process_name,
            pid=pid,
            class_name=str(record["class_name"]),
            exe_path=exe_path,
            width=int(record["width"]),
            height=int(record["height"]),
            area=int(record["area"]),
            is_foreground=int(record["hwnd"]) == foreground_hwnd,
            is_minimized=bool(record.get("is_minimized")),
            score=float(record["area"]),
        )
        candidate.is_foreground = _foreground_matches_target(foreground_hwnd, candidate)[0]
        results.append(_classify_window_candidate(candidate))

    results.sort(key=_window_sort_key, reverse=True)
    return results


def _is_windows_platform() -> bool:
    from .capture_platform import is_windows  # noqa: PLC0415

    return is_windows()


def _platform_scan_windows() -> list[DetectedGameWindow]:
    """Cross-platform window enumeration entry point.

    Delegates to capture_platform.scan_windows() which dispatches to the
    correct backend per platform. Used as the non-Windows default for
    OcrReaderManager._window_scanner. On Windows the manager keeps using
    _default_window_scanner directly to preserve identity-based tests.
    """
    from .capture_platform import scan_windows  # noqa: PLC0415

    return scan_windows()


def _foreground_window_handle() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        _LOGGER.debug("_foreground_window_handle failed", exc_info=True)
        return 0


def _window_handle_from_point(x: int, y: int) -> int:
    if os.name != "nt":
        return 0
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [POINT]
        return int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
    except Exception:
        _LOGGER.debug("_window_handle_from_point failed", exc_info=True)
        return 0


def _root_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2))
        return root or int(hwnd)
    except Exception:
        _LOGGER.debug("_root_window_handle failed", exc_info=True)
        return int(hwnd)


def _window_process_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        _LOGGER.debug("_window_process_id failed", exc_info=True)
        return 0


def _window_process_name(pid: int) -> str:
    if not pid or psutil is None:
        return ""
    try:
        return str(psutil.Process(int(pid)).name() or "").strip()
    except Exception:
        _LOGGER.debug("_window_process_name failed", exc_info=True)
        return ""


def _foreground_matches_target(foreground_hwnd: int, target: DetectedGameWindow | None) -> tuple[bool, str]:
    if target is None or not foreground_hwnd:
        return False, "no_foreground_or_target"
    target_hwnd = int(target.hwnd or 0)
    foreground_root_hwnd = _root_window_handle(int(foreground_hwnd))
    target_root_hwnd = _root_window_handle(target_hwnd)
    if target_hwnd and int(foreground_hwnd) == target_hwnd:
        return True, "hwnd"
    if target_root_hwnd and foreground_root_hwnd and foreground_root_hwnd == target_root_hwnd:
        return True, "root_hwnd"
    foreground_pid = _window_process_id(int(foreground_hwnd)) or _window_process_id(foreground_root_hwnd)
    target_pid = int(target.pid or 0)
    if foreground_pid and target_pid and foreground_pid == target_pid:
        return True, "pid"
    target_process = str(target.process_name or "").strip().lower()
    foreground_process = _window_process_name(foreground_pid).strip().lower()
    if foreground_process and target_process and foreground_process == target_process:
        return True, "process"
    return False, "background"


def _classify_window_candidate(candidate: DetectedGameWindow) -> DetectedGameWindow:
    normalized_title = candidate.normalized_title
    lowered_process_name = str(candidate.process_name or "").strip().lower()
    lowered_class_name = str(candidate.class_name or "").strip().lower()

    if candidate.is_minimized:
        candidate.eligible = False
        candidate.exclude_reason = "excluded_minimized_window"
        candidate.category = "excluded_minimized_window"
        return candidate

    if candidate.area and candidate.area < (400 * 300):
        candidate.eligible = False
        candidate.exclude_reason = "excluded_small_or_hidden_window"
        candidate.category = "excluded_small_or_hidden_window"
        return candidate

    if _looks_like_self_window_title(candidate.title) or _looks_like_self_window_path(candidate.exe_path):
        candidate.eligible = False
        candidate.exclude_reason = "excluded_self_window"
        candidate.category = "excluded_self_window"
        return candidate

    if candidate.class_name in _HELPER_CLASS_NAMES:
        candidate.eligible = False
        candidate.exclude_reason = "excluded_helper_window"
        candidate.category = "excluded_helper_window"
        return candidate

    if any(token in normalized_title for token in _OVERLAY_WINDOW_TITLE_SUBSTRINGS):
        candidate.eligible = False
        candidate.exclude_reason = "excluded_overlay_window"
        candidate.category = "excluded_overlay_window"
        return candidate

    if lowered_process_name and any(
        token in lowered_process_name for token in _OVERLAY_PROCESS_NAME_SUBSTRINGS
    ):
        candidate.eligible = False
        candidate.exclude_reason = "excluded_overlay_window"
        candidate.category = "excluded_overlay_window"
        return candidate

    if lowered_class_name.startswith("chrome_widgetwin") and _looks_like_self_window_title(candidate.title):
        candidate.eligible = False
        candidate.exclude_reason = "excluded_self_window"
        candidate.category = "excluded_self_window"
        return candidate

    if lowered_process_name and lowered_process_name in _AUTO_TARGET_DENY_PROCESS_NAMES:
        candidate.eligible = False
        candidate.exclude_reason = "excluded_non_game_process"
        candidate.category = "excluded_non_game_process"
        return candidate

    candidate.eligible = True
    candidate.exclude_reason = ""
    candidate.category = "eligible_game_window"
    return candidate


def _is_confident_auto_window(candidate: DetectedGameWindow) -> bool:
    if _matches_aihong_target(candidate):
        return True
    process_name = str(candidate.process_name or "").strip().lower()
    class_name = str(candidate.class_name or "").strip().lower()
    if process_name in _AUTO_TARGET_DENY_PROCESS_NAMES:
        return False
    if class_name.startswith("chrome_widgetwin"):
        return False
    return bool(candidate.hwnd and candidate.eligible)


def _is_legacy_geometryless_auto_window(candidate: DetectedGameWindow) -> bool:
    if not candidate.hwnd or not candidate.eligible:
        return False
    if candidate.width or candidate.height or candidate.area:
        return False
    process_name = str(candidate.process_name or "").strip().lower()
    class_name = str(candidate.class_name or "").strip().lower()
    if process_name in _AUTO_TARGET_DENY_PROCESS_NAMES:
        return False
    if class_name.startswith("chrome_widgetwin"):
        return False
    return True


def _window_sort_key(candidate: DetectedGameWindow) -> tuple[int, int, float, str]:
    return (
        1 if candidate.eligible else 0,
        1 if candidate.is_foreground else 0,
        float(candidate.score or 0.0),
        candidate.normalized_title,
    )


from .ocr_bridge_writer import *
class OcrReaderManager:
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
        self._start_rapidocr_warmup_if_configured()

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
                stop()
        self._runtime.foreground_advance_monitor_running = False
        self._runtime.foreground_advance_last_seq = 0

    def _reset_memory_reader_text_progress_tracking(self) -> None:
        self._last_memory_reader_text_at = 0.0
        self._last_seen_memory_reader_game_id = ""
        self._last_seen_memory_reader_session_id = ""
        self._last_seen_memory_reader_text_seq = 0

    def _observe_memory_reader_text_progress(
        self,
        memory_reader_runtime: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        status = str(memory_reader_runtime.get("status") or "")
        game_id = str(memory_reader_runtime.get("game_id") or "")
        session_id = str(memory_reader_runtime.get("session_id") or "")
        try:
            last_text_seq = int(memory_reader_runtime.get("last_text_seq") or 0)
        except (TypeError, ValueError):
            last_text_seq = 0
        received_text_this_tick = (
            str(memory_reader_runtime.get("detail") or "") == "receiving_text"
            and last_text_seq > 0
        )

        if status not in {"attaching", "active"} or not game_id or not session_id:
            self._reset_memory_reader_text_progress_tracking()
            return False

        if "last_text_recent" in memory_reader_runtime:
            self._last_seen_memory_reader_game_id = game_id
            self._last_seen_memory_reader_session_id = session_id
            self._last_seen_memory_reader_text_seq = max(0, last_text_seq)
            if bool(memory_reader_runtime.get("last_text_recent")) and last_text_seq > 0:
                self._last_memory_reader_text_at = now
                return True
            self._last_memory_reader_text_at = 0.0
            return False

        session_changed = (
            game_id != self._last_seen_memory_reader_game_id
            or session_id != self._last_seen_memory_reader_session_id
        )
        seq_reset = last_text_seq < self._last_seen_memory_reader_text_seq
        if session_changed or seq_reset:
            self._last_seen_memory_reader_game_id = game_id
            self._last_seen_memory_reader_session_id = session_id
            self._last_seen_memory_reader_text_seq = last_text_seq
            self._last_memory_reader_text_at = now if received_text_this_tick else 0.0
            return received_text_this_tick

        if last_text_seq > self._last_seen_memory_reader_text_seq:
            self._last_seen_memory_reader_text_seq = last_text_seq
            self._last_memory_reader_text_at = now
            return True
        return False

    def start_foreground_advance_monitor(self) -> bool:
        if not self._foreground_advance_monitor_should_autostart():
            self._stop_foreground_advance_monitor()
            return False
        started = bool(self._wheel_monitor.start())
        self._runtime.foreground_advance_monitor_running = self._wheel_monitor.is_running()
        self._runtime.foreground_advance_last_seq = self._wheel_monitor.last_seq()
        return started

    def reset_capture_runtime_diagnostics(self) -> None:
        self._consecutive_no_text_polls = 0
        self._last_capture_error = ""
        self._last_capture_image_hash = ""
        self._last_capture_timing = {}
        self._consecutive_same_capture_frames = 0
        self._stale_capture_backend = False
        self._last_rejected_ocr_text = ""
        self._last_rejected_ocr_reason = ""
        self._last_rejected_ocr_at = ""
        self._last_rejected_capture_backend = ""
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""
        self._runtime.last_capture_error = ""
        self._runtime.last_capture_image_hash = ""
        self._runtime.consecutive_same_capture_frames = 0
        self._runtime.stale_capture_backend = False
        self._runtime.consecutive_no_text_polls = 0
        self._runtime.ocr_capture_diagnostic_required = False
        self._runtime.last_rejected_ocr_text = ""
        self._runtime.last_rejected_ocr_reason = ""
        self._runtime.last_rejected_ocr_at = ""
        self._runtime.last_rejected_capture_backend = ""
        self._runtime.ocr_capture_content_trusted = True
        self._runtime.ocr_capture_rejected_reason = ""
        self._background_capture_pause_until = 0.0
        self._background_capture_pause_reason = ""
        self._reset_known_screen_stuck_tracking()
        if self._runtime.ocr_context_state in {
            "capture_failed",
            "diagnostic_required",
            "stale_capture_backend",
        }:
            self._runtime.ocr_context_state = ""

    def update_config(self, config: GalgameConfig) -> None:
        old_backend_plan_key = self._backend_plan_config_key(self._config)
        old_auto_detect_lang = bool(getattr(self._config, "rapidocr_auto_detect_lang", False))
        self._config = config
        self._runtime.enabled = config.ocr_reader_enabled
        if not bool(config.llm_vision_enabled):
            self._clear_vision_snapshot()
        if float(getattr(config, "ocr_reader_known_screen_timeout_seconds", 0.0) or 0.0) <= 0.0:
            self._reset_known_screen_stuck_tracking()
        backend_plan_key = self._backend_plan_config_key(config)
        if old_backend_plan_key != backend_plan_key or self._backend_plan_cache_key != backend_plan_key:
            self._backend_plan_cache_key = None
            self._backend_plan_cache_at = 0.0
            self._backend_plan_cache = None
            self._rapidocr_backend_cache_key = None
            self._rapidocr_backend_cache = None
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
        self._start_rapidocr_warmup_if_configured()

    def _rapidocr_cache_key(self) -> tuple[str, str, str, str, str]:
        return _rapidocr_runtime_cache_key(
            install_target_dir_raw=self._config.rapidocr_install_target_dir,
            engine_type=self._config.rapidocr_engine_type,
            lang_type=self._config.rapidocr_lang_type,
            model_type=self._config.rapidocr_model_type,
            ocr_version=self._config.rapidocr_ocr_version,
        )

    def _rapidocr_backend_for_config(self) -> RapidOcrBackend:
        key = self._rapidocr_cache_key()
        if self._rapidocr_backend_cache_key == key and self._rapidocr_backend_cache is not None:
            return self._rapidocr_backend_cache
        backend = RapidOcrBackend(
            install_target_dir_raw=self._config.rapidocr_install_target_dir,
            engine_type=self._config.rapidocr_engine_type,
            lang_type=self._config.rapidocr_lang_type,
            model_type=self._config.rapidocr_model_type,
            ocr_version=self._config.rapidocr_ocr_version,
        )
        self._rapidocr_backend_cache_key = key
        self._rapidocr_backend_cache = backend
        return backend

    def _start_rapidocr_warmup_if_configured(self) -> None:
        if self._custom_ocr_backend or not bool(self._config.rapidocr_enabled):
            return
        selection = self._configured_backend_selection()
        if selection not in {"auto", "rapidocr"}:
            return
        self._rapidocr_backend_for_config().warmup_async(self._logger)
        if self._writer.bridge_root != self._config.bridge_root:
            self._writer = OcrReaderBridgeWriter(
                bridge_root=self._config.bridge_root,
                time_fn=self._time_fn,
            )

    def update_advance_speed(self, advance_speed: str) -> None:
        normalized = str(advance_speed or "").strip().lower()
        self._advance_speed = normalized if normalized in ADVANCE_SPEEDS else ADVANCE_SPEED_MEDIUM

    def _line_changed_repeat_threshold(self) -> int:
        if self._advance_speed == ADVANCE_SPEED_FAST:
            return 1
        if self._advance_speed == ADVANCE_SPEED_SLOW:
            return 3
        return 2

    def _should_emit_observed_lines_for_capture(self, *, after_advance_trigger_mode: bool) -> bool:
        return True

    def _mark_observed_progress(self, *, now: float) -> None:
        self._consecutive_no_text_polls = 0
        self._last_observed_at = utc_now_iso(now)

    def _mark_no_text_poll(self) -> None:
        self._consecutive_no_text_polls += 1

    def _ocr_capture_diagnostic_required(self) -> bool:
        return self._consecutive_no_text_polls >= 3

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

    def _record_capture_attempt(self, *, now: float) -> None:
        self._last_capture_attempt_at = utc_now_iso(now)
        self._last_capture_error = ""

    def _record_capture_completed(self, *, now: float, raw_text: str = "", image_hash: str = "") -> None:
        del raw_text
        self._last_capture_completed_at = utc_now_iso(now)
        self._last_capture_error = ""
        if image_hash:
            if image_hash == self._last_capture_image_hash:
                self._consecutive_same_capture_frames += 1
            else:
                self._last_capture_image_hash = image_hash
                self._consecutive_same_capture_frames = 1
            self._stale_capture_backend = (
                self._consecutive_same_capture_frames >= _STALE_CAPTURE_FRAME_THRESHOLD
            )

    def _record_accepted_ocr_text(self, raw_text: str) -> None:
        self._last_raw_ocr_text = str(raw_text or "")
        self._ocr_capture_content_trusted = True
        self._ocr_capture_rejected_reason = ""

    def _maybe_auto_switch_rapidocr_lang(
        self,
        text: str,
        *,
        rapidocr_active: bool = False,
    ) -> None:
        if not bool(getattr(self._config, "rapidocr_auto_detect_lang", False)):
            try:
                self._logger.debug("rapidocr auto-lang skipped: auto_detect_disabled")
            except Exception:
                pass
            return
        if (
            not rapidocr_active
            or not bool(getattr(self._config, "rapidocr_enabled", False))
            or self._configured_backend_selection() not in {"auto", "rapidocr"}
        ):
            try:
                self._logger.debug("rapidocr auto-lang skipped: rapidocr_not_active")
            except Exception:
                pass
            return
        if self._custom_ocr_backend:
            try:
                self._logger.debug("rapidocr auto-lang skipped: custom_ocr_backend")
            except Exception:
                pass
            return
        now = time.monotonic()
        last_switched_at = self._ocr_lang_detector.last_switched_at
        if (
            last_switched_at is not None
            and now - last_switched_at < self._ocr_lang_cooldown_seconds
        ):
            try:
                remaining = self._ocr_lang_cooldown_seconds - (now - last_switched_at)
                self._logger.debug("rapidocr auto-lang skipped: cooldown {:.1f}s remaining", remaining)
            except Exception:
                pass
            return
        detected_lang = self._ocr_lang_detector.feed(text)
        current_lang = str(getattr(self._config, "rapidocr_lang_type", "") or "").strip()
        if not detected_lang:
            try:
                self._logger.debug("rapidocr auto-lang skipped: detection_unconfirmed")
            except Exception:
                pass
            return
        if detected_lang == current_lang:
            try:
                self._logger.debug("rapidocr auto-lang skipped: already_using {}", detected_lang)
            except Exception:
                pass
            return
        try:
            inspection = inspect_rapidocr_installation(
                install_target_dir_raw=self._config.rapidocr_install_target_dir,
                engine_type=self._config.rapidocr_engine_type,
                lang_type=detected_lang,
                model_type=self._config.rapidocr_model_type,
                ocr_version=self._config.rapidocr_ocr_version,
            )
        except Exception as exc:
            try:
                self._logger.warning("rapidocr auto-lang inspection failed: {}", exc)
            except Exception:
                pass
            return
        if not bool(inspection.get("installed")):
            try:
                self._logger.debug("rapidocr auto-lang skipped: model_missing {}", detected_lang)
            except Exception:
                pass
            return
        if not bool(getattr(self._config, "rapidocr_auto_detect_lang", False)):
            try:
                self._logger.debug("rapidocr auto-lang skipped: auto_detect_disabled_before_apply")
            except Exception:
                pass
            return

        self._config.rapidocr_lang_type = detected_lang
        self._config.rapidocr_auto_detect_last_lang = detected_lang
        self._ocr_lang_detector._switched_at = time.monotonic()
        self._backend_plan_cache_key = None
        self._backend_plan_cache_at = 0.0
        self._backend_plan_cache = None
        self._rapidocr_backend_cache_key = None
        self._rapidocr_backend_cache = None
        self._ocr_lang_detector.reset()
        callback = self._rapidocr_lang_changed_callback
        if callable(callback):
            try:
                callback(detected_lang)
            except Exception as exc:
                try:
                    self._logger.warning("rapidocr auto-lang persist callback failed: {}", exc)
                except Exception:
                    pass
        try:
            self._logger.info("RapidOCR auto-detected language switched to {}", detected_lang)
        except Exception:
            pass

    def _record_rejected_ocr_text(
        self,
        raw_text: str,
        *,
        reason: str,
        now: float,
        capture_backend_kind: str = "",
    ) -> None:
        self._last_rejected_ocr_text = str(raw_text or "")
        self._last_rejected_ocr_reason = str(reason or "")
        self._last_rejected_ocr_at = utc_now_iso(now)
        self._last_rejected_capture_backend = str(capture_backend_kind or "")
        self._ocr_capture_content_trusted = False
        self._ocr_capture_rejected_reason = str(reason or "")

    def _background_capture_pause_error(
        self,
        target: DetectedGameWindow,
        *,
        now: float,
    ) -> RuntimeError | None:
        if bool(getattr(target, "is_foreground", False)):
            self._background_capture_pause_until = 0.0
            self._background_capture_pause_reason = ""
            return None
        if self._background_capture_pause_until <= 0.0:
            return None
        if now >= self._background_capture_pause_until:
            self._background_capture_pause_until = 0.0
            self._background_capture_pause_reason = ""
            return None
        reason = self._background_capture_pause_reason or "recent_invalid_background_frame"
        remaining = max(0.0, self._background_capture_pause_until - now)
        return RuntimeError(
            f"backend_not_suitable_for_background: {reason}; paused {remaining:.1f}s"
        )

    def _pause_background_capture_backend(
        self,
        *,
        reason: str,
        now: float,
    ) -> None:
        self._background_capture_pause_until = max(
            self._background_capture_pause_until,
            now + _BACKGROUND_CAPTURE_BACKEND_PAUSE_SECONDS,
        )
        self._background_capture_pause_reason = str(reason or "invalid_background_frame")

    def _record_capture_geometry(self, extraction: OcrExtractionResult) -> None:
        self._last_capture_source_size = dict(extraction.source_size or {})
        self._last_capture_rect = dict(extraction.capture_rect or {})
        self._last_capture_window_rect = dict(extraction.window_rect or {})

    def _record_capture_error(self, *, now: float, error: Exception) -> None:
        if not self._last_capture_attempt_at:
            self._last_capture_attempt_at = utc_now_iso(now)
        self._last_capture_error = str(error)

    @staticmethod
    def _capture_image_hash(frame: Any) -> str:
        if frame is None:
            return ""
        try:
            if hasattr(frame, "resize") and hasattr(frame, "tobytes"):
                source = frame.convert("RGB") if hasattr(frame, "convert") else frame
                small = source.resize((64, 64))
                return hashlib.sha1(small.tobytes()).hexdigest()[:16]
        except (AttributeError, OSError, ValueError):
            return ""
        try:
            return hashlib.sha1(repr(frame).encode("utf-8", "ignore")).hexdigest()[:16]
        except Exception:
            return ""

    @staticmethod
    def _capture_quality_detail(frame: Any) -> str:
        if frame is None or not hasattr(frame, "convert"):
            return ""
        try:
            from PIL import Image, ImageStat

            resampling = getattr(Image, "Resampling", Image)
            image = frame.convert("L").resize((32, 32), resampling.BILINEAR)
            extrema = image.getextrema()
            if not isinstance(extrema, tuple) or len(extrema) != 2:
                return ""
            if int(extrema[1]) - int(extrema[0]) <= 2:
                return "blank_frame"
            stat = ImageStat.Stat(image)
            stddev = float((stat.stddev or [0.0])[0] or 0.0)
            if stddev < 3.0:
                return "low_information_frame"
        except (AttributeError, OSError, ValueError, TypeError):
            return ""
        return ""

    @staticmethod
    def _background_capture_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=_BACKGROUND_HASH_BOTTOM_INSET_RATIO,
        )

    @staticmethod
    def _background_perceptual_hash(frame: Any) -> str:
        return _perceptual_hash_image(frame)

    @staticmethod
    def _hash_distance(left: str, right: str) -> int:
        if not left or not right:
            return 0
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except Exception:
            return 0

    def _background_scene_change_distance(self) -> int:
        try:
            threshold = int(
                getattr(
                    self._config,
                    "ocr_reader_background_scene_change_distance",
                    _BACKGROUND_SCENE_CHANGE_DISTANCE,
                )
            )
        except (TypeError, ValueError):
            return _BACKGROUND_SCENE_CHANGE_DISTANCE
        if threshold < 18 or threshold > _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            return _BACKGROUND_SCENE_CHANGE_DISTANCE
        return threshold

    def _observe_background_hash(
        self,
        background_hash: str,
        *,
        now: float,
        confirm_polls: int = _BACKGROUND_SCENE_CHANGE_CONFIRM_POLLS,
        defer_scene_emit: bool = False,
    ) -> bool:
        if not background_hash:
            return False
        if not self._last_background_hash:
            self._last_background_hash = background_hash
            self._pending_background_hash = ""
            self._pending_background_change_count = 0
            return False
        distance = self._hash_distance(self._last_background_hash, background_hash)
        if distance < self._background_scene_change_distance():
            if self._pending_background_candidate_hash:
                self._clear_pending_background_candidate(
                    diagnostic="background_candidate_cleared_below_threshold"
                )
            self._pending_background_hash = ""
            self._pending_background_change_count = 0
            return False
        if background_hash != self._pending_background_hash:
            self._pending_background_hash = background_hash
            self._pending_background_change_count = 1
            self._record_pending_background_candidate(
                background_hash=background_hash,
                base_hash=self._last_background_hash,
                distance=distance,
                now=now,
            )
        else:
            self._pending_background_change_count += 1
        required_confirm_polls = max(1, int(confirm_polls or 1))
        if self._pending_background_change_count < required_confirm_polls:
            return False
        self._clear_pending_background_candidate(
            diagnostic="background_candidate_promoted_to_pending_visual_scene"
        )
        self._promote_background_hash_to_pending_visual_scene(
            background_hash=background_hash,
            distance=distance,
            now=now,
            diagnostic=(
                "background_hash_scene_pending"
                if not defer_scene_emit
                else "followup_background_hash_scene_pending"
            ),
        )
        return False

    def _set_scene_ordering_diagnostic(self, value: str) -> None:
        diagnostic = str(value or "").strip() or "none"
        self._scene_ordering_diagnostic = diagnostic
        try:
            self._runtime.scene_ordering_diagnostic = diagnostic
        except Exception:
            pass

    def _clear_pending_visual_scene(self, *, diagnostic: str = "") -> bool:
        if not self._pending_visual_scene_hash:
            return False
        self._pending_visual_scene_hash = ""
        self._pending_visual_scene_at = 0.0
        self._pending_visual_scene_distance = 0
        self._pending_visual_scene_commit_diagnostic = ""
        if diagnostic:
            self._set_scene_ordering_diagnostic(diagnostic)
        return True

    # -- background candidate helpers --

    def _record_pending_background_candidate(
        self,
        *,
        background_hash: str,
        base_hash: str,
        distance: int,
        now: float,
    ) -> None:
        self._pending_background_candidate_hash = background_hash
        self._pending_background_candidate_at = now
        self._pending_background_candidate_distance = distance
        self._pending_background_candidate_base_hash = base_hash
        self._pending_background_candidate_used = False

    def _clear_pending_background_candidate(self, *, diagnostic: str = "") -> None:
        self._pending_background_candidate_hash = ""
        self._pending_background_candidate_at = 0.0
        self._pending_background_candidate_distance = 0
        self._pending_background_candidate_base_hash = ""
        self._pending_background_candidate_used = False
        if diagnostic:
            self._set_scene_ordering_diagnostic(diagnostic)

    def _promote_background_hash_to_pending_visual_scene(
        self,
        *,
        background_hash: str,
        distance: int,
        now: float,
        diagnostic: str,
        set_commit_diagnostic: bool = False,
    ) -> None:
        last_observed_line = dict(self._last_observed_line or {})
        last_stable_line = dict(self._last_stable_line or {})
        consecutive_no_text_polls = int(self._consecutive_no_text_polls or 0)
        self._reset_default_ocr_state()
        self._last_observed_line = last_observed_line
        self._last_stable_line = last_stable_line
        self._consecutive_no_text_polls = consecutive_no_text_polls
        self._last_background_hash = background_hash
        self._reset_aihong_menu_state()
        self._pending_visual_scene_hash = background_hash
        self._pending_visual_scene_at = now
        self._pending_visual_scene_distance = distance
        if set_commit_diagnostic:
            self._pending_visual_scene_commit_diagnostic = diagnostic
        self._set_scene_ordering_diagnostic(diagnostic)

    def _has_early_scene_commit_signal(
        self,
        *,
        previous_line: dict[str, Any] | None,
        screen_type: str,
        has_choices: bool,
        now: float,
    ) -> bool:
        if has_choices:
            return True
        normalized = normalize_screen_type(screen_type)
        if normalized in _DIALOGUE_BOUNDARY_SCREEN_TYPES:
            return True
        if int(self._consecutive_no_text_polls or 0) >= _DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS:
            return True
        if previous_line:
            age = self._line_timestamp_age_seconds(previous_line, now=now)
            if age is not None and age >= _BACKGROUND_CANDIDATE_EARLY_COMMIT_TEXT_GAP_SECONDS:
                return True
        distance = int(self._pending_background_candidate_distance or 0)
        threshold = self._background_scene_change_distance()
        if distance >= threshold + _BACKGROUND_CANDIDATE_EARLY_COMMIT_DISTANCE_MARGIN:
            return True
        return False

    def _commit_pending_background_candidate(
        self,
        *,
        now: float,
        diagnostic: str,
    ) -> None:
        background_hash = self._pending_background_candidate_hash
        distance = int(self._pending_background_candidate_distance or 0)
        if not background_hash:
            return
        self._pending_background_candidate_used = True
        self._pending_background_hash = ""
        self._pending_background_change_count = 0
        self._promote_background_hash_to_pending_visual_scene(
            background_hash=background_hash,
            distance=distance,
            now=now,
            diagnostic=diagnostic,
            set_commit_diagnostic=True,
        )
        self._commit_pending_visual_scene(
            now=now,
            diagnostic=diagnostic,
        )
        self._clear_pending_background_candidate(diagnostic=diagnostic)

    def _resolve_pending_background_candidate_before_dialogue(
        self,
        *,
        cleaned_text: str,
        speaker: str,
        text: str,
        now: float,
    ) -> None:
        if not self._pending_background_candidate_hash:
            return
        if self._pending_background_candidate_used:
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_cleared_after_used"
            )
            return
        if now - self._pending_background_candidate_at > _BACKGROUND_CANDIDATE_EARLY_CANDIDATE_MAX_SECONDS:
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_expired"
            )
            return
        candidate_distance = int(self._pending_background_candidate_distance or 0)
        if candidate_distance >= _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            self._commit_pending_background_candidate(
                now=now,
                diagnostic="background_candidate_committed_by_force_distance",
            )
            return
        state = getattr(self._writer, "_state", {})
        screen_type = normalize_screen_type(
            str((state or {}).get("screen_type") or "")
        )
        has_choices = (
            bool((state or {}).get("choices")) if isinstance(state, dict) else False
        )
        previous_line = self._last_stable_line or self._last_observed_line
        if previous_line and self._is_dialogue_block_continuation(
            previous_line,
            text or cleaned_text,
            current_speaker=speaker,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            self._clear_pending_background_candidate(
                diagnostic="background_candidate_suppressed_by_dialogue_continuation"
            )
            return
        if not self._has_early_scene_commit_signal(
            previous_line=previous_line,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            return
        self._commit_pending_background_candidate(
            now=now,
            diagnostic="background_candidate_committed_before_observed",
        )

    def _commit_pending_visual_scene(
        self,
        *,
        now: float,
        diagnostic: str = "",
    ) -> bool:
        background_hash = str(self._pending_visual_scene_hash or "")
        if not background_hash:
            return False
        scene_at = float(self._pending_visual_scene_at or now)
        commit_diagnostic = str(
            diagnostic or self._pending_visual_scene_commit_diagnostic or ""
        )
        distance = int(self._pending_visual_scene_distance or 0)
        self._pending_visual_scene_hash = ""
        self._pending_visual_scene_at = 0.0
        self._pending_visual_scene_distance = 0
        self._pending_visual_scene_commit_diagnostic = ""
        if self._last_scene_change_committed_ts > 0:
            if now - self._last_scene_change_committed_ts < _SCENE_CHANGE_COOLDOWN_SECONDS:
                if distance < _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
                    self._clear_pending_visual_scene(
                        diagnostic="scene_change_suppressed_by_cooldown"
                    )
                    return False
        committed = bool(
            self._writer.advance_visual_scene(
                ts=utc_now_iso(scene_at if scene_at > 0 else now),
                background_hash=background_hash,
            )
        )
        if committed:
            self._last_scene_change_committed_ts = now
            if commit_diagnostic:
                self._set_scene_ordering_diagnostic(commit_diagnostic)
        return committed

    @staticmethod
    def _line_timestamp_age_seconds(line: dict[str, Any], *, now: float) -> float | None:
        raw_ts = str(line.get("ts") or "").strip()
        if not raw_ts:
            return None
        try:
            parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, float(now) - parsed.timestamp())

    @staticmethod
    def _looks_like_dialogue_boundary_title(text: str) -> bool:
        normalized = normalize_text(text).strip()
        if not normalized:
            return False
        if _DIALOGUE_BOUNDARY_TITLE_RE.match(normalized):
            return True
        significant = re.sub(r"[\s\u3000,，.。!！?？:：;；、\"'“”‘’「」『』（）()【】\[\]-]", "", normalized)
        return 1 <= len(significant) <= 8 and not _looks_like_ocr_dialogue_normalized_text(normalized)

    def _is_dialogue_block_continuation(
        self,
        previous_line: dict[str, Any],
        current_text: str,
        *,
        current_speaker: str = "",
        screen_type: str = "",
        has_choices: bool = False,
        now: float = 0.0,
    ) -> bool:
        if has_choices:
            return False
        normalized_screen_type = normalize_screen_type(screen_type)
        if normalized_screen_type in _DIALOGUE_BOUNDARY_SCREEN_TYPES:
            return False
        if normalized_screen_type not in _DIALOGUE_BLOCK_SCREEN_TYPES:
            return False
        previous_text = str(previous_line.get("text") or "").strip()
        current = str(current_text or "").strip()
        if not previous_text or not current:
            return False
        if self._looks_like_dialogue_boundary_title(current):
            return False
        if not _looks_like_ocr_dialogue_text(previous_text):
            return False
        if not _looks_like_ocr_dialogue_text(current):
            return False
        age = self._line_timestamp_age_seconds(previous_line, now=now)
        if age is not None and age > _DIALOGUE_BLOCK_CONTINUATION_MAX_SECONDS:
            return False
        del current_speaker
        return True

    def _resolve_pending_visual_scene_for_dialogue(
        self,
        *,
        cleaned_text: str,
        speaker: str,
        text: str,
        now: float,
        commit_diagnostic: str = "pending_scene_committed_by_dialogue_boundary",
    ) -> None:
        if not self._pending_visual_scene_hash:
            return
        if int(self._pending_visual_scene_distance or 0) >= _BACKGROUND_SCENE_CHANGE_FORCE_DISTANCE:
            self._commit_pending_visual_scene(
                now=now,
                diagnostic=self._pending_visual_scene_commit_diagnostic
                or "pending_scene_committed_by_force_background_distance",
            )
            return
        state = getattr(self._writer, "_state", {})
        has_choices = bool((state or {}).get("choices")) if isinstance(state, dict) else False
        screen_type = str((state or {}).get("screen_type") or "") if isinstance(state, dict) else ""
        if int(self._consecutive_no_text_polls or 0) >= _DIALOGUE_BLOCK_NO_TEXT_GAP_POLLS:
            self._commit_pending_visual_scene(
                now=now,
                diagnostic="pending_scene_committed_after_no_text_gap",
            )
            return
        previous_line = self._last_stable_line or self._last_observed_line
        if previous_line and self._is_dialogue_block_continuation(
            previous_line,
            text or cleaned_text,
            current_speaker=speaker,
            screen_type=screen_type,
            has_choices=has_choices,
            now=now,
        ):
            self._clear_pending_visual_scene(
                diagnostic="pending_scene_suppressed_by_dialogue_continuation"
            )
            return
        self._commit_pending_visual_scene(
            now=now,
            diagnostic=self._pending_visual_scene_commit_diagnostic
            or commit_diagnostic,
        )

    def _observe_followup_background_hash(
        self,
        extraction: OcrExtractionResult,
        *,
        now: float,
        confirm_polls: int,
        defer_scene_emit: bool,
    ) -> bool:
        background_hash = str(extraction.background_hash or "")
        if not background_hash:
            return False
        pending_before = str(self._pending_visual_scene_hash or "")
        emitted = self._observe_background_hash(
            background_hash,
            now=now,
            confirm_polls=confirm_polls,
            defer_scene_emit=defer_scene_emit,
        )
        if emitted:
            self._set_scene_ordering_diagnostic(
                "followup_background_hash_scene_committed"
            )
            return True
        pending_after = str(self._pending_visual_scene_hash or "")
        if pending_after and pending_after != pending_before:
            self._pending_visual_scene_commit_diagnostic = (
                "followup_background_hash_scene_committed"
            )
            self._set_scene_ordering_diagnostic("followup_background_hash_scene_pending")
        return False

    def _line_payload_from_writer(self, *, stability: str) -> dict[str, Any]:
        state = getattr(self._writer, "_state", {})
        if not isinstance(state, dict):
            return {}
        text = str(state.get("text") or "")
        if not text:
            return {}
        return {
            "line_id": str(state.get("line_id") or ""),
            "speaker": str(state.get("speaker") or ""),
            "text": text,
            "scene_id": str(state.get("scene_id") or ""),
            "route_id": str(state.get("route_id") or ""),
            "stability": stability,
            "ts": str(state.get("ts") or ""),
        }

    def _ocr_context_state_for_detail(self, *, status: str, detail: str) -> str:
        detail = str(detail or "")
        if not self._runtime.enabled and not self._config.ocr_reader_enabled:
            return "disabled"
        if detail == "starting_capture":
            return "capture_pending"
        if detail == "capture_failed":
            return "capture_failed"
        if self._stale_capture_backend:
            return "stale_capture_backend"
        if detail == "ocr_capture_diagnostic_required" or self._ocr_capture_diagnostic_required():
            return "diagnostic_required"
        if detail in {"attached_no_text_yet", "self_ui_guard_blocked"}:
            return "no_text"
        state = getattr(self._writer, "_state", {})
        stability = str(state.get("stability") or "") if isinstance(state, dict) else ""
        if stability == "choices":
            return "choices"
        if detail == "receiving_text" or stability == "stable":
            return "stable"
        if detail == "receiving_observed_text" or stability == "tentative":
            return "observed"
        if detail in {"backend_unavailable", "capture_backend_unavailable"}:
            return "capture_failed"
        if str(status or "") == "starting":
            return "capture_pending"
        return detail or str(status or "")

    def update_capture_profiles(self, profiles: dict[str, dict[str, Any]]) -> None:
        self._capture_profiles = _parse_configured_capture_profiles(profiles, self._logger)

    def update_window_target(self, target: dict[str, Any] | None) -> None:
        self._manual_target = OcrWindowTarget.from_dict(target)
        self._locked_target = OcrWindowTarget()
        self._consecutive_no_text_polls = 0
        self._last_selection = WindowSelectionResult(
            selection_mode="manual" if self._manual_target.is_manual() else "auto",
            selection_detail="manual_target_active"
            if self._manual_target.is_manual()
            else "auto_candidate_scan",
            manual_target=self._manual_target,
            candidate_count=len(self._last_eligible_windows),
            excluded_candidate_count=len(self._last_excluded_windows),
            last_exclude_reason=(
                str(self._last_excluded_windows[0].exclude_reason or "")
                if self._last_excluded_windows
                else ""
            ),
        )

    def current_window_target(self) -> dict[str, Any]:
        return self._manual_target.to_dict()

    def refresh_foreground_state(self) -> dict[str, Any]:
        if not self._config.ocr_reader_enabled or not self._platform_fn():
            return self._runtime.to_dict()
        foreground_hwnd = _foreground_window_handle()
        target, detail = self._foreground_refresh_target()
        target_hwnd = int(target.hwnd or 0) if target is not None else 0
        if target is not None:
            is_foreground, foreground_match_reason = _foreground_matches_target(
                foreground_hwnd,
                target,
            )
            (
                target_window_visible,
                target_window_minimized,
                ocr_window_capture_eligible,
                ocr_window_capture_block_reason,
            ) = _target_window_capture_state(target)
            last_capture_error = str(self._last_capture_error or self._runtime.last_capture_error)
            stale_capture_backend = bool(
                self._stale_capture_backend or self._runtime.stale_capture_backend
            )
            has_recent_capture_result = bool(
                self._last_capture_completed_at
                or self._runtime.last_capture_completed_at
                or self._last_raw_ocr_text
                or self._runtime.last_raw_ocr_text
                or str((self._last_stable_line or self._runtime.last_stable_line).get("text") or "")
            )
            if ocr_window_capture_eligible and stale_capture_backend:
                ocr_window_capture_block_reason = "stale_capture_backend"
            elif ocr_window_capture_eligible and last_capture_error:
                ocr_window_capture_block_reason = "capture_failed"
            self._runtime.target_is_foreground = is_foreground
            self._runtime.target_window_visible = target_window_visible
            self._runtime.target_window_minimized = target_window_minimized
            self._runtime.ocr_window_capture_eligible = ocr_window_capture_eligible
            self._runtime.ocr_window_capture_available = bool(
                ocr_window_capture_eligible
                and has_recent_capture_result
                and not last_capture_error
                and not stale_capture_backend
            )
            self._runtime.ocr_window_capture_block_reason = ocr_window_capture_block_reason
            self._runtime.input_target_foreground = is_foreground
            self._runtime.input_target_block_reason = (
                "" if is_foreground else "target_not_foreground"
            )
            self._runtime.effective_window_key = str(target.window_key or self._runtime.effective_window_key)
            self._runtime.effective_window_title = str(target.title or self._runtime.effective_window_title)
            self._runtime.effective_process_name = str(target.process_name or self._runtime.effective_process_name)
            if not self._runtime.process_name:
                self._runtime.process_name = str(target.process_name or "")
            if not self._runtime.window_title:
                self._runtime.window_title = str(target.title or "")
            if not self._runtime.pid:
                self._runtime.pid = int(target.pid or 0)
            detail = (
                f"{detail}:foreground_{foreground_match_reason}"
                if is_foreground
                else f"{detail}:background"
            )
        elif self._runtime.effective_window_key or self._runtime.process_name:
            self._runtime.target_is_foreground = False
            self._runtime.input_target_foreground = False
            self._runtime.input_target_block_reason = "target_missing"
            self._runtime.target_window_visible = False
            self._runtime.target_window_minimized = False
            self._runtime.ocr_window_capture_eligible = False
            self._runtime.ocr_window_capture_available = False
            self._runtime.ocr_window_capture_block_reason = "target_missing"
            detail = detail or "target_unresolved"
        else:
            self._runtime.target_is_foreground = False
            self._runtime.input_target_foreground = False
            self._runtime.input_target_block_reason = "target_missing"
            self._runtime.target_window_visible = False
            self._runtime.target_window_minimized = False
            self._runtime.ocr_window_capture_eligible = False
            self._runtime.ocr_window_capture_available = False
            self._runtime.ocr_window_capture_block_reason = "target_missing"
            detail = "no_target"
        self._runtime.foreground_refresh_at = utc_now_iso(self._time_fn())
        self._runtime.foreground_refresh_detail = detail
        self._runtime.foreground_hwnd = max(0, int(foreground_hwnd or 0))
        self._runtime.target_hwnd = max(0, int(target_hwnd or 0))
        return self._runtime.to_dict()

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

    def _foreground_refresh_target(self) -> tuple[DetectedGameWindow | None, str]:
        windows = list(self._last_detected_windows or [])
        for target, detail in (
            (self._manual_target, "manual_target"),
            (self._locked_target, "locked_target"),
        ):
            if not isinstance(target, OcrWindowTarget):
                continue
            if not (
                target.window_key
                or target.last_known_hwnd
                or target.pid
                or target.process_name
                or target.normalized_title
            ):
                continue
            for candidate in windows:
                if target.matches_exact(candidate) or target.matches_hwnd(candidate):
                    return candidate, f"{detail}_exact"
            for candidate in windows:
                if target.matches_signature(candidate):
                    return candidate, f"{detail}_rebound"
        runtime_key = str(self._runtime.effective_window_key or "").strip()
        runtime_process = str(self._runtime.effective_process_name or self._runtime.process_name or "").strip().lower()
        runtime_pid = int(self._runtime.pid or 0)
        if runtime_key:
            for candidate in windows:
                if candidate.window_key == runtime_key:
                    return candidate, "runtime_effective_key"
        if runtime_pid > 0:
            for candidate in windows:
                if candidate.pid == runtime_pid:
                    return candidate, "runtime_pid"
        if runtime_process:
            for candidate in windows:
                if candidate.process_name.strip().lower() == runtime_process:
                    return candidate, "runtime_process"
        return None, "target_unresolved"

    def _has_locked_target(self) -> bool:
        return bool(
            self._locked_target.window_key
            or self._locked_target.last_known_hwnd
            or self._locked_target.pid
            or self._locked_target.process_name
            or self._locked_target.normalized_title
        )

    def _remember_locked_target(self, target: DetectedGameWindow) -> None:
        if self._manual_target.is_manual():
            return
        self._locked_target = OcrWindowTarget(
            mode="auto",
            window_key=target.window_key,
            process_name=target.process_name,
            normalized_title=target.normalized_title,
            pid=target.pid,
            last_known_hwnd=target.hwnd,
            selected_at=utc_now_iso(self._time_fn()),
        )

    def list_windows_snapshot(
        self,
        *,
        include_excluded: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        eligible_windows, excluded_windows = self._scan_window_inventory(force=force)
        payload = {
            "target_selection_mode": self._manual_target.mode,
            "manual_target": self._manual_target.to_dict(),
            "candidate_count": len(eligible_windows),
            "excluded_candidate_count": len(excluded_windows),
            "windows": [
                candidate.to_dict(
                    is_attached=self._matches_attached_window(candidate),
                    is_manual_target=self._manual_target.is_manual()
                    and (
                        self._manual_target.matches_exact(candidate)
                        or self._manual_target.matches_signature(candidate)
                    ),
                )
                for candidate in eligible_windows
            ],
        }
        if include_excluded:
            payload["excluded_windows"] = [
                candidate.to_dict(
                    is_attached=self._matches_attached_window(candidate),
                    is_manual_target=False,
                )
                for candidate in excluded_windows
            ]
        return payload

    def latest_vision_snapshot(self) -> dict[str, Any]:
        if not bool(self._config.llm_vision_enabled):
            return {}
        snapshot = dict(self._latest_vision_snapshot or {})
        image_base64 = str(self._latest_vision_snapshot_base64 or "")
        if not snapshot or not image_base64:
            return {}
        now = self._time_fn()
        if now >= float(snapshot.get("expires_at_monotonic") or 0.0):
            self._clear_vision_snapshot()
            return {}
        payload = {
            key: json_copy(value)
            for key, value in snapshot.items()
            if key != "expires_at_monotonic"
        }
        payload["vision_image_base64"] = image_base64
        return payload

    def resolve_manual_window_target(self, window_key: str) -> dict[str, Any]:
        normalized_key = str(window_key or "").strip()
        if not normalized_key:
            raise ValueError("window_key is required")
        eligible_windows, excluded_windows = self._scan_window_inventory(force=True)
        for candidate in eligible_windows:
            if candidate.window_key == normalized_key:
                return OcrWindowTarget(
                    mode="manual",
                    window_key=candidate.window_key,
                    process_name=candidate.process_name,
                    normalized_title=candidate.normalized_title,
                    pid=candidate.pid,
                    last_known_hwnd=candidate.hwnd,
                    selected_at=utc_now_iso(self._time_fn()),
                ).to_dict()
        for candidate in excluded_windows:
            if candidate.window_key == normalized_key:
                raise ValueError("window_key points to an excluded OCR window")
        raise ValueError("window_key not found among eligible OCR windows")

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
    def _scan_ratio_values(
        current_value: float,
        *,
        delta_start: float,
        delta_end: float,
        step: float,
    ) -> list[float]:
        values: list[float] = []
        seen: set[int] = set()
        basis = 100
        start = int(round((current_value + delta_start) * basis))
        end = int(round((current_value + delta_end) * basis))
        step_value = max(1, int(round(step * basis)))
        for raw in range(start, end + 1, step_value):
            normalized = max(0.0, min(raw / basis, 0.98))
            key = int(round(normalized * basis))
            if key in seen:
                continue
            seen.add(key)
            values.append(round(normalized, 2))
        return values

    @staticmethod
    def _crop_box_for_profile_size(
        *,
        width: int,
        height: int,
        profile: OcrCaptureProfile,
    ) -> tuple[int, int, int, int]:
        left = int(width * profile.left_inset_ratio)
        right = int(width * (1.0 - profile.right_inset_ratio))
        top = int(height * profile.top_ratio)
        bottom = int(height * (1.0 - profile.bottom_inset_ratio))
        left = max(0, min(left, width))
        right = max(left, min(right, width))
        top = max(0, min(top, height))
        bottom = max(top, min(bottom, height))
        return (left, top, right, bottom)

    def auto_recalibrate_dialogue_profile(self) -> dict[str, Any]:
        if not self._config.ocr_reader_enabled:
            raise ValueError("ocr_reader 未启用，无法自动重校准对白区")
        if not self._platform_fn():
            raise ValueError("当前平台不是 Windows，无法自动重校准对白区")
        if not self._capture_backend.is_available():
            raise ValueError("当前截图后端不可用，无法自动重校准对白区")
        attached_target = self._attached_window
        if attached_target is None:
            raise ValueError("当前没有已附着的 OCR 目标窗口，无法自动重校准对白区")
        target = replace(attached_target)
        process_name = str(target.process_name or "").strip()
        if not process_name:
            raise ValueError("当前 OCR 目标缺少进程名，无法自动重校准对白区")

        full_window_profile = OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        )
        full_image = self._capture_backend.capture_frame(target, full_window_profile)
        # Verify the screen is static by capturing a second frame and comparing hashes
        time.sleep(0.15)
        verify_image = self._capture_backend.capture_frame(target, full_window_profile)
        hash_a = hashlib.blake2b(
            full_image.resize((64, 64)).tobytes() if hasattr(full_image, "resize") else b"", digest_size=16
        ).hexdigest() if full_image is not None else ""
        hash_b = hashlib.blake2b(
            verify_image.resize((64, 64)).tobytes() if hasattr(verify_image, "resize") else b"", digest_size=16
        ).hexdigest() if verify_image is not None else ""
        if hash_a != hash_b:
            raise ValueError("画面未静止，自动重校准中止（请在稳定画面重试）")
        image_size = getattr(full_image, "size", None)
        if (
            not isinstance(image_size, tuple)
            or len(image_size) < 2
            or int(image_size[0]) <= 0
            or int(image_size[1]) <= 0
            or not hasattr(full_image, "crop")
        ):
            raise ValueError("当前截图后端不支持自动重校准所需的整窗截图")

        image_width = int(image_size[0])
        image_height = int(image_size[1])
        if target.width <= 0 or target.height <= 0:
            target = replace(
                target,
                width=target.width if target.width > 0 else image_width,
                height=target.height if target.height > 0 else image_height,
            )

        base_selection = self._capture_profile_selection_for_target(
            target,
            stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        )
        base_profile = base_selection.profile
        is_aihong_target = _matches_aihong_target(target)

        def _append_ratio_values(values: list[float], additions: Iterable[float]) -> list[float]:
            merged = list(values)
            seen = {int(round(value * 100)) for value in merged}
            for raw in additions:
                normalized = round(max(0.0, min(float(raw), 0.98)), 2)
                key = int(round(normalized * 100))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
            return sorted(merged)

        horizontal_pairs: list[tuple[float, float]] = []

        def _add_horizontal_pair(left_ratio: float, right_ratio: float) -> None:
            left_ratio = round(max(0.0, min(float(left_ratio), 0.45)), 2)
            right_ratio = round(max(0.0, min(float(right_ratio), 0.45)), 2)
            if left_ratio + right_ratio >= 0.95:
                return
            pair = (left_ratio, right_ratio)
            if pair not in horizontal_pairs:
                horizontal_pairs.append(pair)

        if is_aihong_target:
            _add_horizontal_pair(0.0, 0.0)
            _add_horizontal_pair(0.02, 0.02)
            _add_horizontal_pair(0.05, 0.05)
        _add_horizontal_pair(base_profile.left_inset_ratio, base_profile.right_inset_ratio)
        if not is_aihong_target and (
            base_profile.left_inset_ratio > 0.0 or base_profile.right_inset_ratio > 0.0
        ):
            _add_horizontal_pair(
                max(0.0, base_profile.left_inset_ratio - 0.05),
                max(0.0, base_profile.right_inset_ratio - 0.05),
            )

        top_values = self._scan_ratio_values(
            base_profile.top_ratio,
            delta_start=-0.14,
            delta_end=0.08,
            step=0.02,
        )
        bottom_values = self._scan_ratio_values(
            base_profile.bottom_inset_ratio,
            delta_start=-0.04,
            delta_end=0.08,
            step=0.02,
        )
        if is_aihong_target:
            aihong_preset = OcrCaptureProfile.from_dict(_AIHONG_DIALOGUE_CAPTURE_PROFILE_PRESET)
            top_values = _append_ratio_values(
                top_values,
                self._scan_ratio_values(
                    aihong_preset.top_ratio,
                    delta_start=-0.08,
                    delta_end=0.08,
                    step=0.02,
                ),
            )
            bottom_values = _append_ratio_values(
                bottom_values,
                self._scan_ratio_values(
                    aihong_preset.bottom_inset_ratio,
                    delta_start=-0.05,
                    delta_end=0.08,
                    step=0.01,
                ),
            )
        backend_plan = None if self._custom_ocr_backend else self._resolve_backend_plan()
        if backend_plan is not None and not backend_plan.primary.available:
            raise ValueError("当前 OCR backend 不可用，无法自动重校准对白区")

        min_top_ratio = 0.04
        try:
            client_rect = _target_client_rect(target)
            client_height = int(client_rect[3] - client_rect[1])
            if client_height > 0 and image_height > client_height:
                title_bar_height = image_height - client_height
                min_top_ratio = max(
                    min_top_ratio,
                    round(title_bar_height / image_height + 0.02, 2),
                )
        except Exception:
            pass
        top_values = [value for value in top_values if value >= min_top_ratio]
        if not top_values:
            top_values = [min_top_ratio]

        best_candidate: dict[str, Any] | None = None
        current_distance_basis = (
            round(base_profile.top_ratio, 2),
            round(base_profile.bottom_inset_ratio, 2),
        )
        min_height = max(24, int(image_height * 0.08))
        max_height = max(min_height, int(image_height * 0.45))
        visited_pairs: set[tuple[float, float, float, float]] = set()

        def _consider_candidate(
            top_ratio: float,
            bottom_inset_ratio: float,
            left_inset_ratio: float,
            right_inset_ratio: float,
        ) -> None:
            nonlocal best_candidate
            key = (
                round(top_ratio, 2),
                round(bottom_inset_ratio, 2),
                round(left_inset_ratio, 2),
                round(right_inset_ratio, 2),
            )
            if key in visited_pairs:
                return
            visited_pairs.add(key)
            if top_ratio + bottom_inset_ratio >= 1.0 or left_inset_ratio + right_inset_ratio >= 1.0:
                return
            candidate_profile = OcrCaptureProfile(
                left_inset_ratio=left_inset_ratio,
                right_inset_ratio=right_inset_ratio,
                top_ratio=top_ratio,
                bottom_inset_ratio=bottom_inset_ratio,
            )
            left_px, top_px, right_px, bottom_px = self._crop_box_for_profile_size(
                width=image_width,
                height=image_height,
                profile=candidate_profile,
            )
            crop_height = bottom_px - top_px
            if crop_height < min_height or crop_height > max_height:
                return
            if right_px - left_px < 10:
                return
            extracted = self._extract_text_from_image(
                full_image.crop((left_px, top_px, right_px, bottom_px)),
                plan=backend_plan,
            )
            sample_text = str(extracted.text or "").strip()
            if not sample_text or _looks_like_self_ui_text(sample_text):
                return
            score, cjk_count, significant_chars = _score_ocr_text(sample_text)
            if significant_chars < 8 or cjk_count <= 0:
                return
            distance = abs(round(top_ratio, 2) - current_distance_basis[0]) + abs(
                round(bottom_inset_ratio, 2) - current_distance_basis[1]
            )
            width_ratio = max(0.0, 1.0 - left_inset_ratio - right_inset_ratio)
            candidate = {
                "profile": candidate_profile,
                "sample_text": sample_text,
                "score": score,
                "cjk_count": cjk_count,
                "significant_chars": significant_chars,
                "distance": distance,
                "width_ratio": width_ratio,
            }
            if best_candidate is None:
                best_candidate = candidate
                return
            if (
                (candidate["score"], candidate["cjk_count"], candidate["significant_chars"])
                > (
                    best_candidate["score"],
                    best_candidate["cjk_count"],
                    best_candidate["significant_chars"],
                )
                or (
                    (
                        candidate["score"],
                        candidate["cjk_count"],
                        candidate["significant_chars"],
                    )
                    == (
                        best_candidate["score"],
                        best_candidate["cjk_count"],
                        best_candidate["significant_chars"],
                    )
                    and (
                        candidate["width_ratio"] > best_candidate["width_ratio"]
                        or (
                            candidate["width_ratio"] == best_candidate["width_ratio"]
                            and candidate["distance"] < best_candidate["distance"]
                        )
                    )
                )
            ):
                best_candidate = candidate

        preferred_bottom_values: list[float] = []
        for delta in (0.0, 0.02, -0.02, 0.04):
            candidate_value = round(base_profile.bottom_inset_ratio + delta, 2)
            if candidate_value in bottom_values and candidate_value not in preferred_bottom_values:
                preferred_bottom_values.append(candidate_value)
        if not preferred_bottom_values:
            preferred_bottom_values = list(bottom_values)

        for top_ratio in top_values:
            for bottom_inset_ratio in preferred_bottom_values:
                for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                    _consider_candidate(
                        top_ratio,
                        bottom_inset_ratio,
                        left_inset_ratio,
                        right_inset_ratio,
                    )

        if best_candidate is not None:
            refine_top_values: list[float] = []
            best_top_ratio = round(float(best_candidate["profile"].top_ratio), 2)
            for delta in (-0.02, 0.0, 0.02):
                candidate_value = round(best_top_ratio + delta, 2)
                if candidate_value in top_values and candidate_value not in refine_top_values:
                    refine_top_values.append(candidate_value)
            for top_ratio in refine_top_values:
                for bottom_inset_ratio in bottom_values:
                    for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                        _consider_candidate(
                            top_ratio,
                            bottom_inset_ratio,
                            left_inset_ratio,
                            right_inset_ratio,
                        )
        else:
            for top_ratio in top_values:
                for bottom_inset_ratio in bottom_values:
                    for left_inset_ratio, right_inset_ratio in horizontal_pairs:
                        _consider_candidate(
                            top_ratio,
                            bottom_inset_ratio,
                            left_inset_ratio,
                            right_inset_ratio,
                        )

        if best_candidate is None:
            raise ValueError("自动重校准失败：请先停在稳定对白界面再重试")

        window_width = max(0, int(target.width or image_width))
        window_height = max(0, int(target.height or image_height))
        bucket_key = (
            build_ocr_capture_profile_bucket_key(window_width, window_height).lower()
            if window_width > 0 and window_height > 0
            else ""
        )
        capture_profile = best_candidate["profile"].to_dict()
        sample_text = str(best_candidate["sample_text"] or "")
        return {
            "process_name": process_name,
            "stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "save_scope": "window_bucket",
            "bucket_key": bucket_key,
            "window_width": window_width,
            "window_height": window_height,
            "capture_profile": capture_profile,
            "sample_text": sample_text,
            "summary": (
                f"已自动重校准对白区：{process_name}"
                + (f" / {bucket_key}" if bucket_key else "")
                + f" / 示例文本：{sample_text[:24]}"
            ),
        }

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

    def _has_manual_capture_profile(self, target: DetectedGameWindow) -> bool:
        return _uses_manual_capture_profile(self._capture_profiles, target)

    def _should_use_aihong_two_stage(self, target: DetectedGameWindow) -> bool:
        return _matches_aihong_target(target)

    @staticmethod
    def _stabilize_text_key(
        text: str,
        *,
        state: _StableOcrTextState,
        repeat_threshold: int = 2,
    ) -> bool:
        cleaned = normalize_text(text).strip()
        text_key = _ocr_stability_key(cleaned)
        if not cleaned:
            state.last_block_reason = "empty_text"
            return False
        if not text_key:
            state.last_block_reason = "empty_stability_key"
            return False
        last_key = state.last_text_key or _ocr_stability_key(state.last_raw_text)
        if _ocr_stability_keys_match(text_key, last_key):
            state.repeat_count += 1
            state.last_raw_text = _prefer_ocr_stability_text(state.last_raw_text, cleaned)
            state.last_text_key = text_key if len(text_key) >= len(last_key) else last_key
        else:
            state.repeat_count = 1
            state.last_raw_text = cleaned
            state.last_text_key = text_key
        if state.repeat_count < max(1, int(repeat_threshold)):
            state.last_block_reason = "waiting_for_repeat"
            return False
        stable_key = state.stable_text_key or _ocr_stability_key(state.stable_text)
        if _ocr_stability_keys_match(state.last_text_key, stable_key):
            state.repeat_count = 0
            state.last_block_reason = "duplicate_stable_text"
            return False
        state.stable_text = state.last_raw_text
        state.stable_text_key = state.last_text_key
        state.last_block_reason = ""
        return True

    def _ocr_window_title_for_noise_filter(self) -> str:
        return str(
            (self._attached_window.title if self._attached_window is not None else "")
            or self._runtime.effective_window_title
            or self._runtime.window_title
            or ""
        )

    def _clean_ocr_dialogue_for_emit(self, raw_text: str) -> tuple[str, str]:
        content_text = _drop_ocr_chrome_noise_lines(
            raw_text,
            window_title=self._ocr_window_title_for_noise_filter(),
        )
        cleaned_text = _clean_ocr_dialogue_text(content_text)
        cleaned_text = _fix_ocr_punctuation_confusion(cleaned_text)
        return content_text, cleaned_text

    def _emit_line_from_ocr_text(
        self,
        raw_text: str,
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        emit_observed: bool = True,
        repeat_threshold: int | None = None,
        ocr_confidence: float | None = None,
        text_source: str = "bottom_region",
        rapidocr_active: bool = False,
    ) -> bool:
        content_text, cleaned_text = self._clean_ocr_dialogue_for_emit(raw_text)
        if (
            _looks_like_noise_normalized_text(cleaned_text)
            or _looks_like_game_overlay_normalized_text(cleaned_text)
            or not _looks_like_ocr_dialogue_normalized_text(cleaned_text)
        ):
            return False
        self._record_accepted_ocr_text(content_text)
        self._maybe_auto_switch_rapidocr_lang(
            cleaned_text,
            rapidocr_active=rapidocr_active,
        )
        speaker, text = OcrReaderBridgeWriter._split_speaker_text(cleaned_text)
        had_pending_visual_scene = bool(self._pending_visual_scene_hash)
        if self._pending_visual_scene_hash:
            self._resolve_pending_visual_scene_for_dialogue(
                cleaned_text=cleaned_text,
                speaker=speaker,
                text=text,
                now=now,
                commit_diagnostic=(
                    "pending_scene_committed_before_observed"
                    if emit_observed
                    else "pending_scene_committed_before_stable"
                ),
            )
        if self._pending_background_candidate_hash and not had_pending_visual_scene:
            self._resolve_pending_background_candidate_before_dialogue(
                cleaned_text=cleaned_text,
                speaker=speaker,
                text=text,
                now=now,
            )
        if emit_observed and self._writer.emit_line_observed(
            cleaned_text,
            ts=utc_now_iso(now),
            ocr_confidence=ocr_confidence,
            text_source=text_source,
        ):
            observed = self._line_payload_from_writer(stability="tentative")
            self._last_observed_line = observed
        tracker = state or self._default_ocr_state
        effective_repeat_threshold = (
            self._line_changed_repeat_threshold()
            if repeat_threshold is None
            else repeat_threshold
        )
        if tracker.stable_text and int(effective_repeat_threshold or 1) > 1:
            cleaned_key = _ocr_stability_key(cleaned_text)
            stable_key = tracker.stable_text_key or _ocr_stability_key(tracker.stable_text)
            if (
                cleaned_key
                and stable_key
                and not _ocr_stability_keys_match(cleaned_key, stable_key)
            ):
                effective_repeat_threshold = 1
        if not self._stabilize_text_key(
            cleaned_text,
            state=tracker,
            repeat_threshold=effective_repeat_threshold,
        ):
            return False
        emitted_text = tracker.stable_text or cleaned_text
        emitted = self._writer.emit_line(
            emitted_text,
            ts=utc_now_iso(now),
            ocr_confidence=ocr_confidence,
            text_source=text_source,
        )
        if emitted:
            stable_line = self._line_payload_from_writer(stability="stable")
            self._last_stable_line = stable_line
            self._last_observed_line = stable_line
        return emitted

    def _emit_choices_from_candidates(
        self,
        choices: list[str],
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        repeat_threshold: int = 2,
        choice_bounds: list[dict[str, float] | None] | None = None,
        choice_bounds_metadata: dict[str, Any] | None = None,
    ) -> bool:
        tracker = state or self._default_ocr_state
        if not self._stabilize_text_key(
            _canonical_choice_candidate_text(choices),
            state=tracker,
            repeat_threshold=max(1, int(repeat_threshold or 1)),
        ):
            return False
        self._commit_pending_visual_scene(now=now)
        return self._writer.emit_choices(
            choices,
            ts=utc_now_iso(now),
            choice_bounds=choice_bounds,
            choice_bounds_metadata=choice_bounds_metadata,
        )

    def _should_attempt_followup_confirm(
        self,
        raw_text: str,
        *,
        state: _StableOcrTextState,
    ) -> bool:
        _, cleaned_text = self._clean_ocr_dialogue_for_emit(raw_text)
        cleaned = normalize_text(cleaned_text).strip()
        if not cleaned:
            return False
        cleaned_key = _ocr_stability_key(cleaned)
        last_key = state.last_text_key or _ocr_stability_key(state.last_raw_text)
        stable_key = state.stable_text_key or _ocr_stability_key(state.stable_text)
        return (
            bool(state.stable_text)
            and
            state.repeat_count >= 1
            and _ocr_stability_keys_match(cleaned_key, last_key)
            and not _ocr_stability_keys_match(cleaned_key, stable_key)
        )

    def _drain_completed_abandoned_capture_workers_locked(self) -> list[ThreadPoolExecutor]:
        executors: list[ThreadPoolExecutor] = []
        active: list[tuple[ThreadPoolExecutor, Future[OcrExtractionResult]]] = []
        for executor, future in self._abandoned_capture_workers:
            if future.done():
                executors.append(executor)
                try:
                    future.result()
                except Exception as exc:
                    self._logger.debug("ocr_reader abandoned timed-out capture eventually failed: {}", exc)
            else:
                active.append((executor, future))
        self._abandoned_capture_workers = active
        return executors

    def _shutdown_capture_worker(self) -> None:
        executors: list[ThreadPoolExecutor] = []
        with self._capture_worker_lock:
            future = self._capture_future
            if future is not None and not future.done():
                future.cancel()
            if self._capture_executor is not None:
                executors.append(self._capture_executor)
            for executor, abandoned_future in self._abandoned_capture_workers:
                if not abandoned_future.done():
                    abandoned_future.cancel()
                executors.append(executor)
            self._abandoned_capture_workers = []
            self._capture_executor = None
            self._capture_future = None
            self._capture_future_started_at = 0.0
            self._capture_future_timed_out = False
        for executor in executors:
            # Project requires Python 3.11; cancel_futures is available on >=3.9.
            executor.shutdown(wait=False, cancel_futures=True)

    def _clear_completed_capture_worker(self) -> None:
        future: Future[OcrExtractionResult] | None = None
        timed_out = False
        executors_to_shutdown: list[ThreadPoolExecutor] = []
        with self._capture_worker_lock:
            executors_to_shutdown.extend(self._drain_completed_abandoned_capture_workers_locked())
            current = self._capture_future
            if current is None or not current.done():
                future = None
            else:
                future = current
                timed_out = bool(self._capture_future_timed_out)
                self._capture_future = None
                self._capture_future_started_at = 0.0
                self._capture_future_timed_out = False
        if timed_out and future is not None:
            try:
                future.result()
            except Exception as exc:
                self._logger.debug("ocr_reader previous timed-out capture eventually failed: {}", exc)
        for executor in executors_to_shutdown:
            executor.shutdown(wait=False, cancel_futures=True)

    def _submit_capture_worker(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        collect_background_hash: bool,
        allow_separate_background_capture: bool,
    ) -> Future[OcrExtractionResult]:
        executors_to_shutdown: list[ThreadPoolExecutor] = []
        recovered_elapsed = 0.0
        cancel_requested = False
        timeout_error: _CaptureTimedOut | None = None
        future: Future[OcrExtractionResult] | None = None
        with self._capture_worker_lock:
            executors_to_shutdown.extend(self._drain_completed_abandoned_capture_workers_locked())
            current = self._capture_future
            if current is not None and not current.done():
                elapsed = max(0.0, time.monotonic() - float(self._capture_future_started_at or 0.0))
                if self._capture_future_timed_out:
                    timeout_seconds = float(_OCR_CAPTURE_TIMEOUT_SECONDS)
                    if timeout_seconds <= 0.0:
                        timeout_seconds = 12.0
                    recovery_after = timeout_seconds + max(timeout_seconds, 0.25)
                    if elapsed >= recovery_after:
                        cancel_requested = current.cancel()
                        executor = self._capture_executor
                        if (
                            not cancel_requested
                            and not current.done()
                            and len(self._abandoned_capture_workers)
                            >= _OCR_MAX_ABANDONED_CAPTURE_WORKERS
                        ):
                            if executor is not None:
                                executors_to_shutdown.append(executor)
                            self._capture_executor = None
                            self._capture_future = None
                            self._capture_future_started_at = 0.0
                            self._capture_future_timed_out = False
                            timeout_error = _CaptureTimedOut(
                                f"previous ocr_reader capture/OCR timed out and is still running after {elapsed:.1f}s; "
                                "stuck capture worker recovery limit reached"
                            )
                        elif executor is not None:
                            if not cancel_requested and not current.done():
                                self._abandoned_capture_workers.append((executor, current))
                            executors_to_shutdown.append(executor)
                        if timeout_error is None:
                            self._capture_executor = None
                            self._capture_future = None
                            self._capture_future_started_at = 0.0
                            self._capture_future_timed_out = False
                            recovered_elapsed = elapsed
                    else:
                        raise _CaptureStillRunning(
                            f"previous ocr_reader capture/OCR timed out and is still running after {elapsed:.1f}s; "
                            "skipping new capture to avoid accumulating blocked OCR threads"
                        )
                else:
                    raise _CaptureStillRunning(
                        f"previous ocr_reader capture/OCR is still running after {elapsed:.1f}s; "
                        "skipping new capture to avoid overlapping OCR work"
                    )
            if timeout_error is None:
                executor = self._capture_executor
                if executor is None:
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="galgame-ocr-capture")
                    self._capture_executor = executor
                future = executor.submit(
                    self._capture_and_extract_text,
                    target,
                    profile,
                    backend_plan,
                    collect_background_hash,
                    allow_separate_background_capture,
                )
                self._capture_executor = executor
                self._capture_future = future
                self._capture_future_started_at = time.monotonic()
                self._capture_future_timed_out = False
        if recovered_elapsed > 0.0:
            self._logger.warning(
                "ocr_reader rotating timed-out capture executor after {:.1f}s; cancel_requested={}",
                recovered_elapsed,
                cancel_requested,
            )
        for executor_to_shutdown in executors_to_shutdown:
            executor_to_shutdown.shutdown(wait=False, cancel_futures=True)
        if timeout_error is not None:
            raise timeout_error
        assert future is not None
        return future

    async def _capture_and_extract_text_with_timeout(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        timeout_seconds = float(_OCR_CAPTURE_TIMEOUT_SECONDS)
        if timeout_seconds <= 0.0:
            timeout_seconds = 12.0
        self._clear_completed_capture_worker()
        future = self._submit_capture_worker(
            target,
            profile,
            backend_plan,
            collect_background_hash,
            allow_separate_background_capture,
        )
        try:
            # State machine: _submit_capture_worker → wrap_future → shield → wait_for.
            #   on success: result returned, _capture_future cleared by _clear_completed_capture_worker.
            #   on timeout: _capture_future_timed_out set under lock; shield keeps
            #     the ThreadPoolExecutor future alive so later cleanup can observe completion.
            #   on cancel: shield prevents cancellation from propagating into the worker thread.
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            with self._capture_worker_lock:
                if self._capture_future is future:
                    self._capture_future_timed_out = True
            raise _CaptureTimedOut(
                f"ocr_reader capture/OCR timed out after {timeout_seconds:.1f}s"
            ) from exc
        finally:
            if future.done():
                self._clear_completed_capture_worker()

    async def _capture_followup_text(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        backend_plan: SelectedOcrBackendPlan,
        *,
        elapsed_since_capture: float = 0.0,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        remaining = _OCR_FOLLOWUP_CONFIRM_DELAY_SECONDS - elapsed_since_capture
        if remaining > 0:
            await asyncio.sleep(remaining)
        return await self._capture_and_extract_text_with_timeout(
            target,
            profile,
            backend_plan,
            collect_background_hash=collect_background_hash,
            allow_separate_background_capture=allow_separate_background_capture,
        )

    def _consume_aihong_menu_stage_text(
        self,
        raw_text: str,
        *,
        now: float,
        boxes: list[OcrTextBox] | None = None,
        choice_bounds_metadata: dict[str, Any] | None = None,
        choice_repeat_threshold: int = 2,
    ) -> _MenuConsumeResult:
        choice_boxes = list(boxes or [])
        if choice_boxes:
            source_height = _aihong_choices_region_source_height(
                choice_boxes,
                choice_bounds_metadata,
            )
            choice_boxes = _filter_boxes_to_region(
                choice_boxes,
                source_height=source_height,
                top_ratio=_AIHONG_CHOICES_REGION_PRESET["top_ratio"],
                bottom_inset_ratio=_AIHONG_CHOICES_REGION_PRESET[
                    "bottom_inset_ratio"
                ],
            )
            lines = _stripped_ocr_lines(
                "\n".join(str(getattr(box, "text", "") or "") for box in choice_boxes)
            )
        else:
            lines = _stripped_ocr_lines(raw_text)
        choices = _coerce_aihong_menu_choices(lines)
        if choices:
            return _MenuConsumeResult(
                emitted_kind="choices"
                if self._emit_choices_from_candidates(
                    choices,
                    now=now,
                    state=self._aihong_menu_ocr_state,
                    repeat_threshold=choice_repeat_threshold,
                    choice_bounds=_aihong_choice_boxes(choices, choice_boxes),
                    choice_bounds_metadata=choice_bounds_metadata,
                )
                else "",
                has_menu_candidate=True,
            )
        if _looks_like_aihong_menu_status_only_text(raw_text):
            return _MenuConsumeResult(emitted_kind="", has_menu_candidate=True)
        # Menu-stage capture intentionally scans a much larger region so option
        # OCR can find buttons anywhere on screen. Do not turn that full-screen
        # text into a dialogue line; switch back to dialogue-stage capture and
        # let the narrower profile read the next line.
        return _MenuConsumeResult(emitted_kind="", has_menu_candidate=False)

    def _matches_attached_window(self, candidate: DetectedGameWindow) -> bool:
        if self._attached_window is None:
            return False
        if candidate.hwnd and self._attached_window.hwnd and candidate.hwnd == self._attached_window.hwnd:
            return True
        return bool(candidate.pid and self._attached_window.pid and candidate.pid == self._attached_window.pid)

    def _prepare_window_inventory(
        self,
        windows: list[DetectedGameWindow],
    ) -> tuple[list[DetectedGameWindow], list[DetectedGameWindow]]:
        use_windows_foreground_api = bool(self._platform_fn())
        foreground_hwnd = (
            _foreground_window_handle() if use_windows_foreground_api else 0
        )
        prepared: list[DetectedGameWindow] = []
        for window in windows:
            candidate = replace(window)
            candidate.process_name = str(candidate.process_name or "").strip()
            candidate.title = str(candidate.title or "")
            candidate.class_name = str(candidate.class_name or "")
            candidate.exe_path = str(candidate.exe_path or "")
            candidate.pid = max(0, int(candidate.pid or 0))
            candidate.hwnd = max(0, int(candidate.hwnd or 0))
            candidate.area = max(0, int(candidate.area or 0))
            candidate.is_minimized = bool(candidate.is_minimized)
            if use_windows_foreground_api:
                foreground_match, _ = _foreground_matches_target(foreground_hwnd, candidate)
                candidate.is_foreground = foreground_match
            else:
                candidate.is_foreground = bool(candidate.is_foreground)
            candidate.score = float(max(candidate.area, 1))
            candidate = _classify_window_candidate(candidate)
            prepared.append(candidate)
        prepared.sort(key=_window_sort_key, reverse=True)
        eligible_windows = [candidate for candidate in prepared if candidate.eligible]
        excluded_windows = [candidate for candidate in prepared if not candidate.eligible]
        self._last_detected_windows = list(prepared)
        self._last_eligible_windows = list(eligible_windows)
        self._last_excluded_windows = list(excluded_windows)
        return eligible_windows, excluded_windows

    def _scan_raw_windows_cached(self, *, force: bool = False) -> list[DetectedGameWindow]:
        now = self._time_fn()
        if (
            not force
            and self._window_inventory_cache_at > 0.0
            and now - float(self._window_inventory_cache_at or 0.0) < _WINDOW_SCAN_CACHE_TTL_SECONDS
        ):
            return list(self._window_inventory_cache)
        scanned = list(self._window_scanner() or [])
        self._window_inventory_cache = list(scanned)
        self._window_inventory_cache_at = now
        return scanned

    def _scan_window_inventory(
        self,
        *,
        force: bool = False,
    ) -> tuple[list[DetectedGameWindow], list[DetectedGameWindow]]:
        # Window enumeration is now cross-platform via capture_platform.
        # The injected _window_scanner dispatches per platform; non-Windows
        # paths return [] gracefully (Wayland / missing pyobjc / etc.) so
        # we always run the scan and let the inventory cache absorb empties.
        scanned = self._scan_raw_windows_cached(force=force)
        return self._prepare_window_inventory(scanned)

    async def shutdown(self) -> None:
        self._stop_foreground_advance_monitor()
        self._shutdown_capture_worker()
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

        capture_backend_available = await asyncio.to_thread(
            self._capture_backend.is_available
        )
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
            if bool(extraction.timing.get("background_capture_backend_unsuitable")):
                reason = str(
                    extraction.timing.get("capture_quality_detail")
                    or extraction.capture_backend_detail
                    or "invalid_background_frame"
                )
                capture_error = True
                self._pause_background_capture_backend(reason=reason, now=now)
                self._record_capture_error(
                    now=now,
                    error=RuntimeError(f"backend_not_suitable_for_background: {reason}"),
                )
                result.warnings.append(
                    f"ocr_reader background capture backend not suitable: {reason}"
                )
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
                    and int(event_seq_before_capture or 0) <= 1
                ):
                    self._writer.discard_session()
                    self._ocr_lang_detector.reset(clear_switch_time=True)
            else:
                self._record_accepted_ocr_text(extraction.text)
                screen_classification, screen_event_emitted = self._emit_screen_classification_from_extraction(
                    extraction,
                    target=target,
                    now=now,
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
                            if followup_extraction.text and _looks_like_self_ui_text(followup_extraction.text):
                                guard_blocked = True
                                self._record_rejected_ocr_text(
                                    followup_extraction.text,
                                    reason="self_ui_guard",
                                    now=self._time_fn(),
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
                                followup_now = self._time_fn()
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
                        if dialogue_emitted:
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
                                if menu_extraction.text and _looks_like_self_ui_text(menu_extraction.text):
                                    guard_blocked = True
                                    self._record_rejected_ocr_text(
                                        menu_extraction.text,
                                        reason="self_ui_guard",
                                        now=self._time_fn(),
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
                        if followup_extraction.text and _looks_like_self_ui_text(followup_extraction.text):
                            guard_blocked = True
                            self._record_rejected_ocr_text(
                                followup_extraction.text,
                                reason="self_ui_guard",
                                now=self._time_fn(),
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
                            followup_now = self._time_fn()
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
            if int(self._writer.last_seq or 0) <= 1:
                self._writer.discard_session()
                self._ocr_lang_detector.reset(clear_switch_time=True)
            result.warnings.append(f"ocr_reader capture timed out: {exc}")
        except Exception as exc:
            self._logger.warning("ocr_reader capture/OCR failed: {}", exc)
            capture_error = True
            self._record_capture_error(now=now, error=exc)
            self._ocr_lang_detector.reset()
            self._reset_aihong_menu_state()
            if int(self._writer.last_seq or 0) <= 1:
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

    def _configured_backend_selection(self) -> str:
        selection = str(self._config.ocr_reader_backend_selection or "auto").strip().lower()
        if selection in {"auto", "rapidocr"}:
            return selection
        return "auto"

    def _capture_profile_selection_for_target(
        self,
        target: DetectedGameWindow,
        *,
        stage: str = _AIHONG_DIALOGUE_STAGE,
    ) -> ResolvedOcrCaptureSelection:
        configured_profile = _lookup_capture_profile(
            self._capture_profiles,
            target,
            stage=stage,
        )
        if configured_profile is not None:
            return configured_profile

        builtin_profile = _builtin_capture_profile_for_target_stage(target, stage=stage)
        if builtin_profile is not None:
            return ResolvedOcrCaptureSelection(
                profile=builtin_profile,
                match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUILTIN_PRESET,
            )

        return ResolvedOcrCaptureSelection(
            profile=OcrCaptureProfile(
                left_inset_ratio=self._config.ocr_reader_left_inset_ratio,
                right_inset_ratio=self._config.ocr_reader_right_inset_ratio,
                top_ratio=self._config.ocr_reader_top_ratio,
                bottom_inset_ratio=self._config.ocr_reader_bottom_inset_ratio,
            ),
            match_source=OCR_CAPTURE_PROFILE_MATCH_SOURCE_CONFIG_DEFAULT,
        )

    def _capture_profile_for_target(
        self,
        target: DetectedGameWindow,
        *,
        stage: str = _AIHONG_DIALOGUE_STAGE,
    ) -> OcrCaptureProfile:
        return self._capture_profile_selection_for_target(target, stage=stage).profile

    def _rapidocr_descriptor(self, inspection: dict[str, Any], *, enabled: bool) -> OcrBackendDescriptor:
        detail = str(inspection.get("detail") or "missing")
        if not enabled:
            detail = "disabled_by_config"
        available = enabled and bool(inspection.get("installed"))
        return OcrBackendDescriptor(
            kind="rapidocr",
            backend=self._rapidocr_backend_for_config(),
            path=str(inspection.get("detected_path") or ""),
            model=str(
                inspection.get("selected_model")
                or f"{self._config.rapidocr_ocr_version}/{self._config.rapidocr_lang_type}/{self._config.rapidocr_model_type}"
            ),
            detail="selected_primary" if available else detail,
            available=available,
        )

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
        rapidocr_inspection = inspect_rapidocr_installation(
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
            enabled=True,
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
        )

    def _extract_text_from_image(
        self,
        image: Any,
        *,
        plan: SelectedOcrBackendPlan | None = None,
    ) -> OcrExtractionResult:
        if plan is not None:
            resolved_plan = plan
        elif self._custom_ocr_backend:
            resolved_plan = self._custom_ocr_backend_plan()
        else:
            resolved_plan = self._resolve_backend_plan()
        if self._custom_ocr_backend:
            return OcrExtractionResult(
                text=self._ocr_backend.extract_text(image),
                backend=resolved_plan.primary,
                backend_detail=resolved_plan.primary.detail or "custom_backend",
                text_source="bottom_region",
            )
        descriptors = [resolved_plan.primary]
        if resolved_plan.fallback.available:
            descriptors.append(resolved_plan.fallback)
        warnings: list[str] = []
        last_error: Exception | None = None
        for index, descriptor in enumerate(descriptors):
            if descriptor.backend is None:
                continue
            try:
                extract_with_boxes = getattr(descriptor.backend, "extract_text_with_boxes", None)
                if callable(extract_with_boxes):
                    try:
                        text, boxes = extract_with_boxes(image)
                        if not str(text or "").strip():
                            if not isinstance(descriptor.backend, RapidOcrBackend):
                                extract_text = getattr(descriptor.backend, "extract_text", None)
                                if callable(extract_text):
                                    fallback_text = extract_text(image)
                                    if str(fallback_text or "").strip():
                                        text = fallback_text
                                        boxes = []
                            elif index == 0:
                                warnings.append(
                                    f"ocr_reader {descriptor.kind} returned empty text "
                                    "(confidence filtering may have discarded all tokens)"
                                )
                                continue
                    except Exception as boxes_exc:
                        extract_text = getattr(descriptor.backend, "extract_text", None)
                        if not callable(extract_text):
                            raise
                        warnings.append(
                            f"ocr_reader {descriptor.kind} boxes unavailable: {boxes_exc}"
                        )
                        text = extract_text(image)
                        boxes = []
                else:
                    text = descriptor.backend.extract_text(image)
                    boxes = []
                return OcrExtractionResult(
                    text=text,
                    backend=descriptor,
                    backend_detail=(
                        "fallback_after_runtime_error"
                        if index > 0
                        else (descriptor.detail or "selected_primary")
                    ),
                    warnings=warnings,
                    boxes=list(boxes),
                    ocr_confidence=_average_ocr_box_confidence(boxes),
                    text_source="bottom_region",
                )
            except Exception as exc:
                last_error = exc
                warning = f"ocr_reader {descriptor.kind} failed: {exc}"
                warnings.append(warning)
                self._logger.warning("ocr_reader backend {} failed: {}", descriptor.kind, exc)
        if last_error is not None:
            raise last_error
        return OcrExtractionResult(backend=resolved_plan.primary, warnings=warnings)

    @staticmethod
    def _full_window_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        )

    @staticmethod
    def _top_region_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.02,
            right_inset_ratio=0.02,
            top_ratio=0.0,
            bottom_inset_ratio=0.55,
        )

    @staticmethod
    def _menu_region_profile() -> OcrCaptureProfile:
        return OcrCaptureProfile(
            left_inset_ratio=0.08,
            right_inset_ratio=0.08,
            top_ratio=0.18,
            bottom_inset_ratio=0.08,
        )

    @staticmethod
    def _capture_profile_key(profile: OcrCaptureProfile) -> tuple[float, float, float, float]:
        return (
            round(float(profile.left_inset_ratio), 4),
            round(float(profile.right_inset_ratio), 4),
            round(float(profile.top_ratio), 4),
            round(float(profile.bottom_inset_ratio), 4),
        )

    def _clear_vision_snapshot(self) -> None:
        self._latest_vision_snapshot = {}
        self._latest_vision_snapshot_base64 = ""

    def _vision_snapshot_runtime_status(self) -> dict[str, Any]:
        snapshot = dict(self._latest_vision_snapshot or {})
        if not snapshot or not self._latest_vision_snapshot_base64:
            return {
                "available": False,
                "captured_at": "",
                "expires_at": "",
                "source": "",
                "width": 0,
                "height": 0,
                "byte_size": 0,
            }
        if self._time_fn() >= float(snapshot.get("expires_at_monotonic") or 0.0):
            self._clear_vision_snapshot()
            return {
                "available": False,
                "captured_at": "",
                "expires_at": "",
                "source": "",
                "width": 0,
                "height": 0,
                "byte_size": 0,
            }
        return {
            "available": True,
            "captured_at": str(snapshot.get("captured_at") or ""),
            "expires_at": str(snapshot.get("expires_at") or ""),
            "source": str(snapshot.get("source") or ""),
            "width": int(snapshot.get("width") or 0),
            "height": int(snapshot.get("height") or 0),
            "byte_size": int(snapshot.get("byte_size") or 0),
        }

    def _remember_vision_snapshot(
        self,
        frame: Any,
        *,
        source: str,
        now: float,
    ) -> None:
        if not bool(self._config.llm_vision_enabled):
            self._clear_vision_snapshot()
            return
        if frame is None or not hasattr(frame, "save"):
            return
        try:
            image = frame.convert("RGB") if hasattr(frame, "convert") else frame
            max_px = max(64, int(self._config.llm_vision_max_image_px or 768))
            width, height = image.size
            if width <= 0 or height <= 0:
                return
            scale = min(1.0, float(max_px) / float(max(width, height)))
            if scale < 1.0:
                next_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                image = image.resize(
                    next_size,
                    _PIL_RESAMPLING.LANCZOS if _PIL_RESAMPLING is not None else 1,
                )
                width, height = image.size
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=_VISION_SNAPSHOT_JPEG_QUALITY, optimize=True)
            raw = buffer.getvalue()
            if not raw:
                return
            expires_at = now + _VISION_SNAPSHOT_TTL_SECONDS
            self._latest_vision_snapshot_base64 = (
                "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            )
            self._latest_vision_snapshot = {
                "captured_at": utc_now_iso(now),
                "expires_at": utc_now_iso(expires_at),
                "expires_at_monotonic": expires_at,
                "source": source,
                "width": int(width),
                "height": int(height),
                "byte_size": len(raw),
                "ttl_seconds": _VISION_SNAPSHOT_TTL_SECONDS,
            }
        except Exception as exc:
            self._logger.debug("ocr_reader vision snapshot encoding skipped: {}", exc)

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
        if self._capture_profile_key(full_profile) not in seen_profiles and (
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
                try:
                    self._logger.debug("ocr_reader screen awareness {} skipped: {}", source, exc)
                except Exception:
                    pass

        if regions or visual_features:
            self._last_screen_awareness_capture_at = now
        extraction.screen_ocr_regions = regions
        extraction.screen_visual_features = visual_features
        extraction.timing["screen_awareness_capture_duration_seconds"] = capture_duration
        extraction.timing["screen_awareness_ocr_duration_seconds"] = ocr_duration
        extraction.timing["screen_awareness_region_count"] = float(len(regions))

    def _capture_and_extract_text(
        self,
        target: DetectedGameWindow,
        profile: OcrCaptureProfile,
        plan: SelectedOcrBackendPlan,
        collect_background_hash: bool = True,
        allow_separate_background_capture: bool = True,
    ) -> OcrExtractionResult:
        started_at = self._time_fn()
        background_hash = self._last_background_hash
        background_duration = 0.0
        background_hash_skipped = True
        capture_started_at = self._time_fn()
        frame = self._capture_backend.capture_frame(target, profile)
        capture_frame_duration = max(0.0, self._time_fn() - capture_started_at)
        frame_info = getattr(frame, "info", {}) if frame is not None else {}
        embedded_background_hash = (
            str(frame_info.get("galgame_source_background_hash") or "")
            if isinstance(frame_info, dict)
            else ""
        )
        if collect_background_hash and embedded_background_hash:
            background_hash = embedded_background_hash
            background_hash_skipped = False
            self._last_background_hash_capture_at = started_at
        hash_started_at = self._time_fn()
        capture_hash = self._capture_image_hash(frame)
        capture_hash_duration = max(0.0, self._time_fn() - hash_started_at)
        ocr_started_at = self._time_fn()
        extraction = self._extract_text_from_image(frame, plan=plan)
        ocr_duration = max(0.0, self._time_fn() - ocr_started_at)
        primary_text = str(extraction.text or "").strip()
        primary_text_is_dialogue = bool(
            primary_text and _looks_like_ocr_dialogue_text(primary_text)
        )
        background_hash_interval = (
            _BACKGROUND_HASH_DIALOGUE_SAMPLE_INTERVAL_SECONDS
            if primary_text_is_dialogue
            else _BACKGROUND_HASH_MIN_INTERVAL_SECONDS
        )
        last_background_hash_capture_at = float(self._last_background_hash_capture_at or 0.0)
        if primary_text_is_dialogue and last_background_hash_capture_at <= 0.0:
            self._last_background_hash_capture_at = started_at
            last_background_hash_capture_at = started_at
        if (
            collect_background_hash
            and not embedded_background_hash
            and allow_separate_background_capture
            and not (primary_text and _looks_like_self_ui_text(primary_text))
            and started_at - last_background_hash_capture_at >= background_hash_interval
        ):
            try:
                background_started_at = self._time_fn()
                background_frame = self._capture_backend.capture_frame(
                    target,
                    self._background_capture_profile(),
                )
                background_hash = self._background_perceptual_hash(background_frame)
                background_duration = max(0.0, self._time_fn() - background_started_at)
                background_hash_skipped = False
                self._last_background_hash_capture_at = started_at
            except Exception as exc:
                self._logger.debug("ocr_reader background scene hash skipped: {}", exc)
        extraction.capture_image_hash = capture_hash
        extraction.background_hash = background_hash
        extraction.timing = {
            "total_duration_seconds": max(0.0, self._time_fn() - started_at),
            "capture_frame_duration_seconds": capture_frame_duration,
            "background_hash_duration_seconds": background_duration,
            "capture_image_hash_duration_seconds": capture_hash_duration,
            "ocr_extract_duration_seconds": ocr_duration,
            "background_hash_skipped": background_hash_skipped,
        }
        if isinstance(frame_info, dict):
            extraction.capture_backend_kind = str(
                frame_info.get("galgame_capture_backend_kind")
                or getattr(self._capture_backend, "last_backend_kind", "")
                or getattr(self._capture_backend, "selection", "")
            )
            extraction.capture_backend_detail = str(
                frame_info.get("galgame_capture_backend_detail")
                or getattr(self._capture_backend, "last_backend_detail", "")
                or ""
            )
            extraction.bounds_coordinate_space = str(
                frame_info.get("galgame_bounds_coordinate_space") or ""
            )
            source_size = frame_info.get("galgame_source_size")
            if isinstance(source_size, dict):
                extraction.source_size = dict(source_size)
            capture_rect = frame_info.get("galgame_capture_rect")
            if isinstance(capture_rect, dict):
                extraction.capture_rect = dict(capture_rect)
            window_rect = frame_info.get("galgame_window_rect")
            if isinstance(window_rect, dict):
                extraction.window_rect = dict(window_rect)
        else:
            extraction.capture_backend_kind = str(
                getattr(self._capture_backend, "last_backend_kind", "")
                or getattr(self._capture_backend, "selection", "")
                or ""
            )
            extraction.capture_backend_detail = str(
                getattr(self._capture_backend, "last_backend_detail", "") or ""
            )
        capture_quality_detail = self._capture_quality_detail(frame)
        if (
            extraction.capture_backend_kind == _CAPTURE_BACKEND_PRINTWINDOW
            and capture_quality_detail
        ):
            extraction.warnings.append(f"printwindow capture quality: {capture_quality_detail}")
            if extraction.capture_backend_detail in {"", "selected"}:
                extraction.capture_backend_detail = capture_quality_detail
            extraction.timing["capture_quality_detail"] = capture_quality_detail
            if (
                not bool(getattr(target, "is_foreground", False))
                and not str(extraction.text or "").strip()
            ):
                extraction.warnings.append(
                    f"backend_not_suitable_for_background: {capture_quality_detail}"
                )
                extraction.capture_backend_detail = "backend_not_suitable_for_background"
                extraction.timing["background_capture_backend_unsuitable"] = True
        if extraction.text and _looks_like_self_ui_text(extraction.text):
            extraction.timing["screen_awareness_skipped"] = True
            extraction.timing["screen_awareness_skip_reason"] = "rejected_primary_text"
            extraction.timing["screen_awareness_region_count"] = 0.0
            extraction.timing["screen_awareness_capture_duration_seconds"] = 0.0
            extraction.timing["screen_awareness_ocr_duration_seconds"] = 0.0
            extraction.timing["total_duration_seconds"] = max(0.0, self._time_fn() - started_at)
            return extraction
        self._augment_extraction_with_screen_awareness(
            extraction,
            target=target,
            primary_profile=profile,
            plan=plan,
            now=started_at,
        )
        extraction.timing["total_duration_seconds"] = max(0.0, self._time_fn() - started_at)
        return extraction

    def _emit_screen_classification_from_extraction(
        self,
        extraction: OcrExtractionResult,
        *,
        target: DetectedGameWindow,
        now: float,
    ) -> tuple[ScreenClassification, bool]:
        classification = classify_screen_from_ocr(
            extraction.text,
            boxes=extraction.boxes,
            bounds_metadata=_extraction_choice_bounds_metadata(extraction),
            ocr_regions=extraction.screen_ocr_regions,
            visual_features=extraction.screen_visual_features,
            screen_templates=self._screen_templates_for_target(target),
            template_context=self._screen_template_context(target),
        )
        classification = self._apply_screen_awareness_model(
            extraction,
            classification=classification,
            target=target,
        )
        classification = self._apply_screen_classification_stability(classification)
        self._update_capture_profile_recommendation(
            extraction,
            classification=classification,
            target=target,
            now=now,
        )
        self._collect_screen_awareness_sample(
            extraction,
            classification=classification,
            target=target,
            now=now,
        )
        if classification.screen_type in {
            OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        }:
            if self._should_emit_dialogue_screen_transition(classification):
                emitted = self._writer.emit_screen_classified(
                    screen_type=classification.screen_type,
                    confidence=classification.confidence,
                    ui_elements=classification.ui_elements,
                    raw_ocr_text=classification.raw_ocr_text,
                    screen_debug=classification.debug,
                    ts=utc_now_iso(now),
                )
                return classification, emitted
            return classification, False
        emitted = self._writer.emit_screen_classified(
            screen_type=classification.screen_type,
            confidence=classification.confidence,
            ui_elements=classification.ui_elements,
            raw_ocr_text=classification.raw_ocr_text,
            screen_debug=classification.debug,
            ts=utc_now_iso(now),
        )
        return classification, emitted

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
            try:
                self._logger.debug("ocr_reader screen awareness sample skipped: {}", exc)
            except Exception:
                pass

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

    def _update_capture_profile_recommendation(
        self,
        extraction: OcrExtractionResult,
        *,
        classification: ScreenClassification,
        target: DetectedGameWindow,
        now: float,
    ) -> None:
        if (
            classification.screen_type not in {OCR_CAPTURE_PROFILE_STAGE_DEFAULT, OCR_CAPTURE_PROFILE_STAGE_DIALOGUE}
            and classification.confidence >= 0.5
        ):
            self._recommended_capture_profile = {}
            return
        if (
            classification.screen_type != OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            or classification.confidence < 0.55
            or not str(extraction.text or "").strip()
        ):
            return
        bounds: list[dict[str, float]] = []
        for element in classification.ui_elements:
            normalized = element.get("normalized_bounds")
            if not isinstance(normalized, dict):
                continue
            try:
                left = float(normalized.get("left"))
                top = float(normalized.get("top"))
                right = float(normalized.get("right"))
                bottom = float(normalized.get("bottom"))
            except (TypeError, ValueError):
                continue
            if right <= left or bottom <= top:
                continue
            bounds.append({"left": left, "top": top, "right": right, "bottom": bottom})
        if not bounds:
            return

        min_top = max(0.0, min(item["top"] for item in bounds))
        max_bottom = min(1.0, max(item["bottom"] for item in bounds))
        text_height = max_bottom - min_top
        if text_height <= 0.02:
            return
        current_selection = self._capture_profile_selection_for_target(
            target,
            stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        )
        current_profile = current_selection.profile
        top_ratio = round(max(0.0, min_top - 0.08), 2)
        bottom_inset_ratio = round(max(0.0, 1.0 - max_bottom - 0.08), 2)
        if 1.0 - top_ratio - bottom_inset_ratio < 0.16:
            top_ratio = round(max(0.0, 1.0 - bottom_inset_ratio - 0.16), 2)
        if top_ratio + bottom_inset_ratio >= 0.98:
            return
        candidate_profile = OcrCaptureProfile(
            left_inset_ratio=current_profile.left_inset_ratio,
            right_inset_ratio=current_profile.right_inset_ratio,
            top_ratio=top_ratio,
            bottom_inset_ratio=bottom_inset_ratio,
        )
        current_payload = current_profile.to_dict()
        candidate_payload = candidate_profile.to_dict()
        delta = sum(
            abs(float(candidate_payload[key]) - float(current_payload.get(key, 0.0)))
            for key in OCR_CAPTURE_PROFILE_RATIO_KEYS
        )
        if delta < 0.06:
            return
        bucket_key = (
            build_ocr_capture_profile_bucket_key(int(target.width or 0), int(target.height or 0)).lower()
            if int(target.width or 0) > 0 and int(target.height or 0) > 0
            else ""
        )
        sample_text = " ".join(_stripped_ocr_lines(extraction.text))[:120]
        self._recommended_capture_profile = {
            "process_name": str(target.process_name or ""),
            "stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "save_scope": "window_bucket",
            "bucket_key": bucket_key,
            "capture_profile": candidate_payload,
            "current_capture_profile": current_payload,
            "confidence": min(0.95, max(0.0, float(classification.confidence))),
            "reason": "dialogue_text_bounds_offset",
            "sample_text": sample_text,
            "manual_profile_present": self._has_manual_capture_profile(target),
            "created_at": utc_now_iso(now),
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

    def _select_target_window(
        self,
        windows: list[DetectedGameWindow],
        *,
        excluded_windows: list[DetectedGameWindow] | None = None,
        memory_reader_runtime: dict[str, Any] | None = None,
    ) -> WindowSelectionResult:
        excluded = list(excluded_windows or [])
        selection = WindowSelectionResult(
            selection_mode="manual" if self._manual_target.is_manual() else "auto",
            selection_detail="manual_target_active"
            if self._manual_target.is_manual()
            else "auto_candidate_scan",
            manual_target=self._manual_target,
            candidate_count=len(windows),
            excluded_candidate_count=len(excluded),
            last_exclude_reason=str(excluded[0].exclude_reason or "") if excluded else "",
        )
        preferred_pid = int((memory_reader_runtime or {}).get("pid") or 0)
        preferred_process_name = str(
            (memory_reader_runtime or {}).get("process_name") or ""
        ).strip().lower()
        memory_reader_status = str((memory_reader_runtime or {}).get("status") or "")
        prefer_memory_reader_window = (
            getattr(self._config, "reader_mode", READER_MODE_AUTO) == READER_MODE_AUTO
            and memory_reader_status in {"attaching", "active"}
            and (preferred_pid > 0 or bool(preferred_process_name))
        )

        def _matches_memory_reader_target(candidate: DetectedGameWindow) -> bool:
            if preferred_pid > 0 and candidate.pid == preferred_pid:
                return True
            return (
                bool(preferred_process_name)
                and str(candidate.process_name or "").strip().lower() == preferred_process_name
            )

        manual_target_overridden_by_memory_reader = False

        def _manual_target_allowed(candidate: DetectedGameWindow) -> bool:
            nonlocal manual_target_overridden_by_memory_reader
            if not prefer_memory_reader_window:
                return True
            if _matches_memory_reader_target(candidate):
                return True
            manual_target_overridden_by_memory_reader = True
            selection.selection_detail = "manual_target_overridden_by_memory_reader"
            return False

        def _clear_overridden_manual_target() -> None:
            if not manual_target_overridden_by_memory_reader:
                return
            self._manual_target = OcrWindowTarget()
            selection.selection_mode = "auto"
            selection.manual_target = self._manual_target

        def _memory_reader_minimized_window() -> DetectedGameWindow | None:
            if preferred_pid <= 0 and not preferred_process_name:
                return None
            for candidate in excluded:
                if str(candidate.exclude_reason or "") != "excluded_minimized_window":
                    continue
                if preferred_pid > 0 and candidate.pid == preferred_pid:
                    return candidate
                if (
                    preferred_process_name
                    and str(candidate.process_name or "").strip().lower()
                    == preferred_process_name
                ):
                    return candidate
            return None

        def _use_memory_reader_minimized_diagnostic(
            candidate: DetectedGameWindow,
        ) -> WindowSelectionResult:
            selection.selection_detail = "memory_reader_window_minimized"
            selection.last_exclude_reason = "excluded_minimized_window"
            selection.excluded_candidate_count = len(excluded)
            return selection

        if not windows:
            minimized_window = _memory_reader_minimized_window()
            if minimized_window is not None:
                foreground_hwnd = _foreground_window_handle()
                if (
                    foreground_hwnd
                    and foreground_hwnd != 0
                    and foreground_hwnd == minimized_window.hwnd
                ):
                    selection.target = minimized_window
                    selection.selection_detail = (
                        "memory_reader_minimized_overridden_by_foreground"
                    )
                    return selection
                return _use_memory_reader_minimized_diagnostic(minimized_window)
            selection.selection_detail = (
                "manual_target_unavailable_fallback_to_auto"
                if self._manual_target.is_manual()
                else "no_eligible_window"
            )
            if selection.selection_mode == "auto":
                use_windows_foreground_api = bool(self._platform_fn())
                foreground_hwnd = (
                    _foreground_window_handle() if use_windows_foreground_api else 0
                )
                for candidate in excluded:
                    candidate_foreground = (
                        bool(foreground_hwnd and candidate.hwnd == foreground_hwnd)
                        if use_windows_foreground_api
                        else bool(candidate.is_foreground)
                    )
                    if candidate_foreground:
                        selection.selection_detail = "foreground_window_needs_manual_confirmation"
                        break
            return selection

        if self._manual_target.is_manual():
            for candidate in windows:
                if (
                    (
                        self._manual_target.matches_exact(candidate)
                        or self._manual_target.matches_hwnd(candidate)
                    )
                    and _manual_target_allowed(candidate)
                ):
                    resolved_target = self._manual_target.resolved_for(candidate)
                    self._manual_target = resolved_target
                    selection.target = candidate
                    selection.selection_detail = "manual_target_exact"
                    selection.manual_target = resolved_target
                    selection.selected_by_manual = True
                    return selection
            for candidate in windows:
                if self._manual_target.matches_signature(candidate) and _manual_target_allowed(candidate):
                    resolved_target = self._manual_target.resolved_for(candidate)
                    self._manual_target = resolved_target
                    selection.target = candidate
                    selection.selection_detail = "manual_target_rebound"
                    selection.manual_target = resolved_target
                    selection.selected_by_manual = True
                    return selection
            if not manual_target_overridden_by_memory_reader:
                selection.selection_detail = "manual_target_unavailable_fallback_to_auto"

        if preferred_pid > 0:
            for candidate in windows:
                if candidate.pid == preferred_pid:
                    selection.target = candidate
                    if manual_target_overridden_by_memory_reader:
                        _clear_overridden_manual_target()
                        selection.selection_detail = "manual_target_overridden_by_memory_reader_pid"
                    elif selection.selection_mode == "auto":
                        selection.selection_detail = "memory_reader_pid"
                    return selection
        if preferred_process_name:
            for candidate in windows:
                if str(candidate.process_name or "").strip().lower() == preferred_process_name:
                    selection.target = candidate
                    if manual_target_overridden_by_memory_reader:
                        _clear_overridden_manual_target()
                        selection.selection_detail = "manual_target_overridden_by_memory_reader_process"
                    elif selection.selection_mode == "auto":
                        selection.selection_detail = "memory_reader_process"
                    return selection
        minimized_window = _memory_reader_minimized_window()
        if minimized_window is not None:
            return _use_memory_reader_minimized_diagnostic(minimized_window)
        if prefer_memory_reader_window:
            _clear_overridden_manual_target()
            selection.selection_detail = (
                "manual_target_overridden_by_memory_reader_unavailable"
                if manual_target_overridden_by_memory_reader
                else "memory_reader_target_unavailable"
            )
            return selection
        if self._attached_window is not None:
            for candidate in windows:
                if candidate.hwnd == self._attached_window.hwnd:
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "attached_hwnd"
                    return selection
            if self._attached_window.pid:
                for candidate in windows:
                    if candidate.pid == self._attached_window.pid:
                        selection.target = candidate
                        if selection.selection_mode == "auto":
                            selection.selection_detail = "attached_pid"
                        return selection
        if self._has_locked_target():
            for candidate in windows:
                if self._locked_target.matches_exact(candidate) or self._locked_target.matches_hwnd(candidate):
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "locked_target_exact"
                    return selection
            for candidate in windows:
                if self._locked_target.matches_signature(candidate):
                    selection.target = candidate
                    if selection.selection_mode == "auto":
                        selection.selection_detail = "locked_target_rebound"
                    return selection
            if selection.selection_mode == "auto":
                selection.selection_detail = "locked_target_unavailable"
            return selection
        use_windows_foreground_api = bool(self._platform_fn())
        foreground_hwnd = (
            _foreground_window_handle() if use_windows_foreground_api else 0
        )
        for candidate in windows:
            candidate_foreground = (
                bool(foreground_hwnd and candidate.hwnd == foreground_hwnd)
                if use_windows_foreground_api
                else bool(candidate.is_foreground)
            )
            if not candidate_foreground:
                continue
            if not _is_confident_auto_window(candidate):
                if selection.selection_mode == "auto":
                    selection.selection_detail = "foreground_window_needs_manual_confirmation"
                return selection
            selection.target = candidate
            if selection.selection_mode == "auto":
                selection.selection_detail = "foreground_window"
            return selection
        if len(windows) == 1:
            configured_profile = _lookup_capture_profile(
                self._capture_profiles,
                windows[0],
                stage=_AIHONG_DIALOGUE_STAGE,
            )
            if configured_profile is not None:
                selection.target = windows[0]
                if selection.selection_mode == "auto":
                    selection.selection_detail = "single_configured_profile_candidate"
                return selection
            if (
                _is_confident_auto_window(windows[0])
                and not _is_legacy_geometryless_auto_window(windows[0])
            ):
                selection.target = windows[0]
                if selection.selection_mode == "auto":
                    selection.selection_detail = "single_confident_candidate"
                return selection
        if len(windows) == 1 and _is_legacy_geometryless_auto_window(windows[0]):
            selection.target = windows[0]
            if selection.selection_mode == "auto":
                selection.selection_detail = "single_geometryless_candidate"
            return selection
        if selection.selection_mode == "auto":
            selection.selection_detail = "auto_detect_needs_manual_fallback"
        return selection

    def _consume_ocr_text(
        self,
        raw_text: str,
        *,
        now: float,
        state: _StableOcrTextState | None = None,
        allow_choices: bool = True,
        allow_plain_text_choices: bool = False,
        emit_observed: bool = True,
        line_repeat_threshold: int | None = None,
        ocr_confidence: float | None = None,
        text_source: str = "bottom_region",
        rapidocr_active: bool = False,
    ) -> bool:
        tracker = state or self._default_ocr_state
        lines = _stripped_ocr_lines(raw_text)
        if allow_choices:
            choices = _coerce_choice_lines(lines, allow_plain_text=allow_plain_text_choices)
            if choices:
                return self._emit_choices_from_candidates(
                    choices,
                    now=now,
                    state=tracker,
                    repeat_threshold=(
                        line_repeat_threshold
                        if line_repeat_threshold is not None
                        else 2
                    ),
                )
        return self._emit_line_from_ocr_text(
            raw_text,
            now=now,
            state=tracker,
            emit_observed=emit_observed,
            repeat_threshold=line_repeat_threshold,
            ocr_confidence=ocr_confidence,
            text_source=text_source,
            rapidocr_active=rapidocr_active,
        )

    async def _end_session_if_needed(self, now: float) -> None:
        if self._writer.session_id:
            self._writer.end_session(ts=utc_now_iso(now))
            self._attached_window = None
            self._ocr_lang_detector.reset(clear_switch_time=True)
            self._reset_default_ocr_state()
            self._reset_aihong_menu_state()
