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

from ..models import (
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
from ..ocr_chrome_noise import (
    looks_like_temperature_status_line as _looks_like_temperature_status_line,
    looks_like_window_title_line as _looks_like_window_title_line,
)
from ..aihong_state import (
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
from ..reader import normalize_text
from ..screen_classifier import (
    ScreenClassification,
    classify_screen_awareness_model,
    classify_screen_from_ocr,
    normalize_screen_type,
)
from ..screen_classifier import analyze_screen_visual_features

try:
    from PIL import Image as _PIL_IMAGE_MODULE

    _PIL_RESAMPLING = getattr(_PIL_IMAGE_MODULE, "Resampling", None)
except ImportError:  # pragma: no cover - optional in non-visual test environments.
    _PIL_RESAMPLING = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from ..ocr_runtime_types import *
__all__ = [
    "_bounding_screen_rect",
    "_crop_image_to_screen_rect",
    "_crop_window_image",
    "_intersect_screen_rect",
    "_require_visible_capture_target",
    "_require_visible_capture_target_win32",
    "_run_with_thread_dpi_awareness",
    "_target_client_rect",
    "_target_client_rect_win32",
    "_target_content_rect",
    "_target_monitor_work_rect",
    "_target_monitor_work_rects",
    "_target_screen_capture_rect",
    "_target_window_capture_state",
    "_target_window_rect",
    "_target_window_rect_linux",
    "_target_window_rect_macos",
    "_target_window_rect_win32",
    "_target_window_uses_overlapped_chrome",
    "_target_work_area_capture_rect",
    "_valid_screen_rect",
]
def _target_window_rect(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    from ..capture_platform import is_linux, is_macos, is_windows  # noqa: PLC0415

    if is_windows():
        return _target_window_rect_win32(target)
    if is_macos():
        return _target_window_rect_macos(target)
    if is_linux():
        return _target_window_rect_linux(target)
    raise RuntimeError(f"unsupported platform for window rect: {sys.platform}")


def _target_window_rect_win32(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    """Windows: win32gui.GetWindowRect (original implementation, unchanged)."""
    import win32gui

    def _read_rect() -> tuple[int, int, int, int]:
        left, top, right, bottom = win32gui.GetWindowRect(target.hwnd)
        return (int(left), int(top), int(right), int(bottom))

    rect = _run_with_thread_dpi_awareness(_read_rect)
    width = int(rect[2] - rect[0])
    height = int(rect[3] - rect[1])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid window dimensions: {width}x{height}")
    return rect


def _target_window_rect_macos(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    """macOS: CGWindowListCopyWindowInfo via pyobjc.

    Uses kCGWindowListOptionOnScreenOnly to avoid stale coordinates from
    windows on inactive Spaces (consistent with _scan_windows_macos).
    Raises if Quartz cannot resolve the real absolute window bounds.
    Returning a synthetic (0, 0, width, height) rectangle would be treated as
    screen coordinates by pixel-based backends and silently capture the wrong
    region for non-origin windows.
    """
    try:
        import Quartz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("macos_quartz_not_available") from exc

    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    if not window_list:
        raise RuntimeError("macos_target_window_rect_unavailable")

    target_window_id = max(0, int(getattr(target, "hwnd", 0) or 0))
    target_pid = max(0, int(getattr(target, "pid", 0) or 0))
    target_title = _normalize_window_title(getattr(target, "title", "") or "")
    pid_matches: list[Any] = []

    def _window_rect(window: Any) -> tuple[int, int, int, int] | None:
        bounds = window.get(Quartz.kCGWindowBounds)
        if not isinstance(bounds, dict):
            return None
        x = int(bounds.get("X", 0))
        y = int(bounds.get("Y", 0))
        w = int(bounds.get("Width", target.width))
        h = int(bounds.get("Height", target.height))
        if w <= 0 or h <= 0:
            return None
        return (x, y, x + w, y + h)

    for window in window_list:
        window_id = max(0, int(window.get(Quartz.kCGWindowNumber, 0) or 0))
        if target_window_id and window_id == target_window_id:
            rect = _window_rect(window)
            if rect is not None:
                return rect
        if target_pid and window.get(Quartz.kCGWindowOwnerPID) == target_pid:
            pid_matches.append(window)

    for window in pid_matches:
        name = _normalize_window_title(window.get(Quartz.kCGWindowName, "") or "")
        if target_title and name == target_title:
            rect = _window_rect(window)
            if rect is not None:
                return rect

    if not target_window_id and len(pid_matches) == 1:
        rect = _window_rect(pid_matches[0])
        if rect is not None:
            return rect

    raise RuntimeError("macos_target_window_rect_unavailable")


def _target_window_rect_linux(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    """Linux: window rect via python-xlib (X11) or wmctrl.

    Translates X11 relative coordinates to absolute screen coordinates
    by walking up the window tree. Falls back to wmctrl subprocess and
    raises if neither source can resolve the real absolute window bounds.
    Returning a synthetic (0, 0, width, height) rectangle would be treated as
    screen coordinates by pixel-based backends and silently capture the wrong
    region for non-origin windows.
    """
    logger = logging.getLogger(__name__)
    try:
        from Xlib import display as xdisplay  # type: ignore[import-not-found]  # noqa: PLC0415

        d = xdisplay.Display()
        try:
            root = d.screen().root
            net_client_list = d.intern_atom("_NET_CLIENT_LIST")
            raw = root.get_full_property(net_client_list, 0)
            window_ids = raw.value if raw else []
            for wid in window_ids:
                if int(wid) == int(target.hwnd):
                    window = d.create_resource_object("window", wid)
                    geom = window.get_geometry()
                    child = window
                    abs_x, abs_y = 0, 0
                    while child is not None:
                        g = child.get_geometry()
                        abs_x += g.x
                        abs_y += g.y
                        parent = child.query_tree().parent
                        child = parent if parent != root else None
                    w = max(0, int(geom.width))
                    h = max(0, int(geom.height))
                    if w > 0 and h > 0:
                        return (abs_x, abs_y, abs_x + w, abs_y + h)
        finally:
            try:
                d.close()
            except Exception as exc:
                logger.debug("linux xlib display close failed: %s", exc)
    except Exception as exc:
        logger.debug("linux xlib target rect lookup failed: %s", exc)

    try:
        import subprocess  # noqa: PLC0415

        wmctrl_path = shutil.which("wmctrl")
        if not wmctrl_path:
            raise RuntimeError("wmctrl_not_available")
        output = subprocess.check_output(
            [wmctrl_path, "-lG"], text=True, timeout=5
        )
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                if int(parts[0], 16) == int(target.hwnd):
                    x, y, w, h = (
                        int(parts[2]),
                        int(parts[3]),
                        int(parts[4]),
                        int(parts[5]),
                    )
                    if w > 0 and h > 0:
                        return (x, y, x + w, y + h)
            except (ValueError, IndexError):
                continue
    except Exception as exc:
        logger.debug("linux wmctrl target rect lookup failed: %s", exc)

    raise RuntimeError("linux_target_window_rect_unavailable")


def _valid_screen_rect(rect: tuple[int, int, int, int]) -> bool:
    return int(rect[2] - rect[0]) > 0 and int(rect[3] - rect[1]) > 0


def _intersect_screen_rect(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    left = max(int(first[0]), int(second[0]))
    top = max(int(first[1]), int(second[1]))
    right = min(int(first[2]), int(second[2]))
    bottom = min(int(first[3]), int(second[3]))
    rect = (left, top, right, bottom)
    return rect if _valid_screen_rect(rect) else None


def _bounding_screen_rect(
    rects: Iterable[tuple[int, int, int, int]],
) -> tuple[int, int, int, int] | None:
    valid_rects = [rect for rect in rects if _valid_screen_rect(rect)]
    if not valid_rects:
        return None
    return (
        min(int(rect[0]) for rect in valid_rects),
        min(int(rect[1]) for rect in valid_rects),
        max(int(rect[2]) for rect in valid_rects),
        max(int(rect[3]) for rect in valid_rects),
    )


def _target_monitor_work_rects(
    rect: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    try:
        import win32api

        enum_display_monitors = getattr(win32api, "EnumDisplayMonitors", None)
        if not callable(enum_display_monitors):
            return []
        try:
            monitors = enum_display_monitors(None, tuple(int(value) for value in rect))
        except TypeError:
            monitors = enum_display_monitors()

        work_rects: list[tuple[int, int, int, int]] = []
        for monitor_info in monitors:
            monitor = monitor_info[0]
            try:
                info = win32api.GetMonitorInfo(monitor)
            except Exception:
                continue
            work = info.get("Work") if isinstance(info, dict) else None
            if isinstance(work, tuple) and len(work) == 4:
                work_rect = tuple(int(value) for value in work)
                if _valid_screen_rect(work_rect):
                    work_rects.append(work_rect)
        return work_rects
    except Exception:
        _LOGGER.debug("failed to read target monitor work rects", exc_info=True)
        return []


def _target_monitor_work_rect(target: DetectedGameWindow) -> tuple[int, int, int, int] | None:
    try:
        import win32api

        monitor = win32api.MonitorFromWindow(int(target.hwnd), 2)
        info = win32api.GetMonitorInfo(monitor)
        work = info.get("Work") if isinstance(info, dict) else None
        if isinstance(work, tuple) and len(work) == 4:
            rect = tuple(int(value) for value in work)
            return rect if _valid_screen_rect(rect) else None
    except Exception:
        return None
    return None


def _target_work_area_capture_rect(
    target: DetectedGameWindow,
    rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    work_rects = _target_monitor_work_rects(rect)
    if not work_rects:
        work_rect = _target_monitor_work_rect(target)
        work_rects = [work_rect] if work_rect is not None else []
    intersections = (
        intersection
        for work_rect in work_rects
        if (intersection := _intersect_screen_rect(rect, work_rect)) is not None
    )
    return _bounding_screen_rect(intersections)


def _target_window_uses_overlapped_chrome(target: DetectedGameWindow) -> bool:
    try:
        import win32con
        import win32gui

        style = int(win32gui.GetWindowLong(int(target.hwnd), win32con.GWL_STYLE))
        return bool(style & (win32con.WS_CAPTION | win32con.WS_THICKFRAME))
    except Exception:
        return False


def _target_content_rect(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    try:
        rect = _target_client_rect(target)
        if _valid_screen_rect(rect):
            return rect
    except Exception:
        _LOGGER.debug("_target_content_rect client rect lookup failed", exc_info=True)
    return _target_window_rect(target)


def _target_screen_capture_rect(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    rect = _target_content_rect(target)
    if not _target_window_uses_overlapped_chrome(target):
        return rect
    clipped = _target_work_area_capture_rect(target, rect)
    return clipped or rect


def _target_window_capture_state(target: DetectedGameWindow | None) -> tuple[bool, bool, bool, str]:
    if target is None:
        return False, False, False, "target_missing"
    if not int(getattr(target, "hwnd", 0) or 0):
        return False, bool(getattr(target, "is_minimized", False)), False, "target_missing"
    try:
        import win32gui

        hwnd = int(target.hwnd or 0)
        if not win32gui.IsWindow(hwnd):
            return False, False, False, "target_missing"
        is_visible = bool(win32gui.IsWindowVisible(hwnd))
        is_minimized = bool(win32gui.IsIconic(hwnd))
    except Exception:
        _LOGGER.debug("IsWindowVisible/IsIconic failed", exc_info=True)
        is_minimized = bool(getattr(target, "is_minimized", False))
        is_visible = bool(
            not is_minimized
            and int(getattr(target, "width", 0) or 0) > 0
            and int(getattr(target, "height", 0) or 0) > 0
        )
    if is_minimized:
        return is_visible, True, False, "target_minimized"
    if not is_visible:
        return False, False, False, "target_not_visible"
    return True, False, True, ""


def _run_with_thread_dpi_awareness(fn: Callable[[], tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    user32 = getattr(ctypes, "windll", None)
    user32 = getattr(user32, "user32", None) if user32 is not None else None
    set_context = getattr(user32, "SetThreadDpiAwarenessContext", None) if user32 is not None else None
    if not callable(set_context):
        return fn()
    set_context.restype = ctypes.c_void_p
    set_context.argtypes = [ctypes.c_void_p]
    old_context = None
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. This is thread-local and
        # avoids globally changing the plugin process.
        old_context = set_context(ctypes.c_void_p(-4))
    except Exception:
        _LOGGER.warning("ocr_reader failed to set thread DPI awareness context", exc_info=True)
        old_context = None
    try:
        return fn()
    finally:
        if old_context is not None:
            try:
                set_context(old_context)
            except Exception:
                _LOGGER.warning(
                    "ocr_reader failed to restore thread DPI awareness context",
                    exc_info=True,
                )


def _target_client_rect(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    from ..capture_platform import is_windows  # noqa: PLC0415

    if is_windows():
        return _target_client_rect_win32(target)
    # macOS/Linux: most target games are borderless fullscreen or borderless
    # windowed. Do not subtract hard-coded title-bar pixels by default; the
    # capture path crops by OcrCaptureProfile ratios afterwards.
    return _target_window_rect(target)


def _target_client_rect_win32(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    """Windows: win32gui.GetClientRect + ClientToScreen (original, unchanged)."""
    import win32gui

    def _read_rect() -> tuple[int, int, int, int]:
        left, top, right, bottom = win32gui.GetClientRect(target.hwnd)
        screen_left, screen_top = win32gui.ClientToScreen(target.hwnd, (left, top))
        screen_right, screen_bottom = win32gui.ClientToScreen(target.hwnd, (right, bottom))
        return (int(screen_left), int(screen_top), int(screen_right), int(screen_bottom))

    rect = _run_with_thread_dpi_awareness(_read_rect)
    width = int(rect[2] - rect[0])
    height = int(rect[3] - rect[1])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid client dimensions: {width}x{height}")
    return rect


def _require_visible_capture_target(target: DetectedGameWindow, *, backend_kind: str) -> None:
    from ..capture_platform import is_windows  # noqa: PLC0415

    if is_windows():
        return _require_visible_capture_target_win32(target, backend_kind=backend_kind)
    # macOS/Linux: best-effort check. PyAutoGUI/MSS can still capture
    # windows that aren't strictly visible via Win32 semantics, so only
    # validate the invariants we can observe portably.
    if not target.hwnd and not target.pid:
        raise RuntimeError(f"{backend_kind}: target_window_not_resolved_for_capture")
    if getattr(target, "is_minimized", False):
        raise RuntimeError(f"{backend_kind}: target_window_minimized_for_capture")


def _require_visible_capture_target_win32(
    target: DetectedGameWindow, *, backend_kind: str
) -> None:
    """Windows: existing win32gui visibility checks (unchanged from original)."""
    if not target.hwnd:
        raise RuntimeError(f"{backend_kind}: target_window_not_resolved_for_capture")
    try:
        import win32gui

        if not win32gui.IsWindow(target.hwnd):
            raise RuntimeError(f"{backend_kind}: target_window_invalid_for_capture")
        if not win32gui.IsWindowVisible(target.hwnd):
            raise RuntimeError(f"{backend_kind}: target_window_not_visible_for_capture")
        if win32gui.IsIconic(target.hwnd):
            raise RuntimeError(f"{backend_kind}: target_window_minimized_for_capture")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"{backend_kind}: target_window_visibility_check_failed: {exc}"
        ) from exc


def _crop_window_image(
    image: Any,
    *,
    window_rect: tuple[int, int, int, int],
    profile: OcrCaptureProfile,
    backend_kind: str,
    backend_detail: str,
) -> Any:
    width = int(window_rect[2] - window_rect[0])
    height = int(window_rect[3] - window_rect[1])
    left = int(width * profile.left_inset_ratio)
    right = int(width * (1.0 - profile.right_inset_ratio))
    top = int(height * profile.top_ratio)
    bottom = int(height * (1.0 - profile.bottom_inset_ratio))

    left = max(0, min(left, width))
    right = max(left, min(right, width))
    top = max(0, min(top, height))
    bottom = max(top, min(bottom, height))

    crop_w = right - left
    crop_h = bottom - top
    if crop_w < 10 or crop_h < 10:
        raise RuntimeError(f"Crop region too small: {crop_w}x{crop_h}")

    background_bottom = max(
        0,
        min(int(height * (1.0 - _BACKGROUND_HASH_BOTTOM_INSET_RATIO)), height),
    )
    source_background_hash = ""
    if background_bottom >= 10:
        source_background_hash = _perceptual_hash_image(
            image.crop((0, 0, width, background_bottom))
        )

    cropped = image.crop((left, top, right, bottom))
    cropped.info["galgame_bounds_coordinate_space"] = "capture"
    cropped.info["galgame_source_size"] = {"width": float(crop_w), "height": float(crop_h)}
    cropped.info["galgame_full_frame_image"] = image
    cropped.info["galgame_source_background_hash"] = source_background_hash
    cropped.info["galgame_capture_rect"] = {
        "left": float(window_rect[0] + left),
        "top": float(window_rect[1] + top),
        "right": float(window_rect[0] + right),
        "bottom": float(window_rect[1] + bottom),
    }
    cropped.info["galgame_window_rect"] = {
        "left": float(window_rect[0]),
        "top": float(window_rect[1]),
        "right": float(window_rect[2]),
        "bottom": float(window_rect[3]),
    }
    cropped.info["galgame_capture_backend_kind"] = backend_kind
    cropped.info["galgame_capture_backend_detail"] = backend_detail
    return cropped


def _crop_image_to_screen_rect(
    image: Any,
    *,
    image_rect: tuple[int, int, int, int],
    crop_rect: tuple[int, int, int, int],
) -> Any:
    crop_left = max(0, int(crop_rect[0] - image_rect[0]))
    crop_top = max(0, int(crop_rect[1] - image_rect[1]))
    crop_right = min(int(image.size[0]), int(crop_rect[2] - image_rect[0]))
    crop_bottom = min(int(image.size[1]), int(crop_rect[3] - image_rect[1]))
    if crop_right <= crop_left or crop_bottom <= crop_top:
        raise RuntimeError("Crop region outside source image")
    return image.crop((crop_left, crop_top, crop_right, crop_bottom))
