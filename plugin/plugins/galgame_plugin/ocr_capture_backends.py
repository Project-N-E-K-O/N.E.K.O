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

__all__ = [
    "CaptureBackend",
    "DxcamCaptureBackend",
    "MssCaptureBackend",
    "OcrBackend",
    "PrintWindowCaptureBackend",
    "PyAutoGuiCaptureBackend",
    "Win32CaptureBackend",
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

class CaptureBackend(Protocol):
    def is_available(self) -> bool: ...

    def describe_target(self, target: DetectedGameWindow) -> str: ...

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any: ...


class OcrBackend(Protocol):
    def is_available(self) -> bool: ...

    def extract_text(self, image: Any) -> str: ...


def _target_window_rect(target: DetectedGameWindow) -> tuple[int, int, int, int]:
    from .capture_platform import is_linux, is_macos, is_windows  # noqa: PLC0415

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
    from .capture_platform import is_windows  # noqa: PLC0415

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
    from .capture_platform import is_windows  # noqa: PLC0415

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


class MssCaptureBackend:
    kind = _CAPTURE_BACKEND_MSS

    def __init__(self, *, logger=None) -> None:
        self._logger = logger
        self._sct = None
        self._sct_lock = threading.RLock()

    def is_available(self) -> bool:
        try:
            import mss
            return bool(mss)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def _sct_instance(self):
        with self._sct_lock:
            if self._sct is not None:
                return self._sct
            import mss

            self._sct = mss.mss()
            return self._sct

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        left, top, right, bottom = rect
        monitor = {
            "left": int(left),
            "top": int(top),
            "width": int(right - left),
            "height": int(bottom - top),
        }
        with self._sct_lock:
            sct = self._sct_instance()
            shot = sct.grab(monitor)
        # mss returns BGRA; convert to RGB via PIL.
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )


class PyAutoGuiCaptureBackend:
    """Cross-platform fallback in the spirit of pyautogui's screenshot path.

    Functionally similar to MssCaptureBackend on Windows (both go through GDI),
    kept as a defense-in-depth fallback in case mss fails (e.g. handle
    exhaustion).

    Internally we call PIL ImageGrab.grab() directly with all_screens=True
    instead of pyautogui.screenshot(), because pyautogui 0.9.54 wraps
    ImageGrab without exposing all_screens — its capture silently truncates
    to the primary monitor on multi-display setups, which would corrupt
    OCR for any galgame window placed on a secondary screen or at negative
    coordinates. The is_available() probe still gates on `import pyautogui`
    so the backend's lifecycle still tracks the user-facing PyAutoGUI label.
    """

    kind = _CAPTURE_BACKEND_PYAUTOGUI

    def __init__(self, *, logger=None) -> None:
        self._logger = logger

    def is_available(self) -> bool:
        # `import pyautogui` can throw beyond ImportError in headless / WSL /
        # missing-DISPLAY environments — pyautogui's mouse module touches
        # platform display state at import time and may raise KeyError /
        # RuntimeError. Catch broadly so backend probing degrades cleanly to
        # "unavailable" instead of bubbling up and aborting capture preflight.
        try:
            import pyautogui  # noqa: F401 — gate on user-facing label
            from PIL import ImageGrab  # noqa: F401 — actual capture mechanism
            return True
        except Exception:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import ImageGrab

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        left, top, right, bottom = rect
        # all_screens=True is Windows-only in Pillow but harmlessly ignored
        # on macOS/Linux — covers multi-monitor layouts including secondary
        # displays at negative coordinates relative to the primary screen.
        image = ImageGrab.grab(
            bbox=(int(left), int(top), int(right), int(bottom)),
            all_screens=True,
        )
        if image.mode != "RGB":
            image = image.convert("RGB")
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )


class PrintWindowCaptureBackend:
    kind = _CAPTURE_BACKEND_PRINTWINDOW

    def __init__(self, *, logger=None) -> None:
        self._logger = logger

    def is_available(self) -> bool:
        try:
            import win32gui
            import win32ui
            import win32con
            return bool(win32gui and win32ui and win32con)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        _require_visible_capture_target(target, backend_kind=self.kind)
        try:
            rect = _target_screen_capture_rect(target)
        except Exception:
            rect = _target_content_rect(target)
        image = self._capture_full_window(target.hwnd, rect)
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected_legacy_fallback",
        )

    @staticmethod
    def _capture_full_window(hwnd: int, rect: tuple[int, int, int, int]) -> Any:
        import win32gui
        import win32ui
        import win32con
        from PIL import Image

        width = int(rect[2] - rect[0])
        height = int(rect[3] - rect[1])
        hdc = win32gui.GetWindowDC(hwnd)
        if not hdc:
            raise RuntimeError("Failed to get window DC")

        bmp = None
        mem_dc = None
        hdc_mem = None
        try:
            hdc_mem = win32ui.CreateDCFromHandle(hdc)
            mem_dc = hdc_mem.CreateCompatibleDC()

            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(hdc_mem, width, height)
            mem_dc.SelectObject(bmp)

            # Try PrintWindow with PW_RENDERFULLCONTENT (3) for better game capture
            # Only available on Windows 8.1+ (version 6.3+)
            PW_RENDERFULLCONTENT = 3
            success = False
            ver = sys.getwindowsversion()
            if ver.major > 6 or (ver.major == 6 and ver.minor >= 3):
                success = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            if not success:
                mem_dc.BitBlt((0, 0), (width, height), hdc_mem, (0, 0), win32con.SRCCOPY)

            bmp_info = bmp.GetInfo()
            bmp_str = bmp.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                bmp_str,
                "raw",
                "BGRX",
                0,
                1,
            )
        finally:
            if mem_dc is not None:
                mem_dc.DeleteDC()
            if hdc_mem is not None:
                hdc_mem.DeleteDC()
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
            win32gui.ReleaseDC(hwnd, hdc)
        return image


class DxcamCaptureBackend:
    kind = _CAPTURE_BACKEND_DXCAM
    _MAX_CONSECUTIVE_FAILURES = 3
    _FAILURE_COOLDOWN_SECONDS = 30.0

    def __init__(self, *, logger=None) -> None:
        self._logger = logger
        self._camera = None
        self._camera_lock = threading.RLock()
        self._last_create_error = ""
        self._consecutive_failures = 0
        self._last_failure_time = 0.0

    def is_available(self) -> bool:
        try:
            import dxcam
            return bool(dxcam)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def _camera_instance(self):
        with self._camera_lock:
            if self._camera is not None:
                return self._camera
            import dxcam

            last_exc = None
            for _attempt in range(3):
                try:
                    self._camera = dxcam.create(output_color="RGB")
                except Exception as exc:
                    last_exc = exc
                    self._last_create_error = str(exc)
                    time.sleep(0.5)
                    continue
                if self._camera is not None:
                    return self._camera
                time.sleep(0.5)
            if last_exc is not None:
                raise RuntimeError(f"dxcam_create_failed: {last_exc}") from last_exc
            raise RuntimeError("dxcam_create_failed: returned None after retries")

    def _reset_camera(self) -> None:
        with self._camera_lock:
            camera = self._camera
            self._camera = None
            stop = getattr(camera, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    _LOGGER.warning("ocr_reader camera stop() failed", exc_info=True)

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        frame = None
        with self._camera_lock:
            now = time.monotonic()
            if (
                self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES
                and now - self._last_failure_time < self._FAILURE_COOLDOWN_SECONDS
            ):
                raise RuntimeError(
                    f"dxcam rate limited after {self._consecutive_failures} consecutive failures"
                )
            for attempt in range(_DXCAM_GRAB_RETRY_ATTEMPTS + 1):
                camera = self._camera_instance()
                frame = camera.grab(region=rect)
                if frame is not None:
                    self._consecutive_failures = 0
                    break
                self._reset_camera()
                self._consecutive_failures += 1
                self._last_failure_time = time.monotonic()
                if attempt < _DXCAM_GRAB_RETRY_ATTEMPTS:
                    time.sleep(_DXCAM_GRAB_RETRY_DELAY_SECONDS)
            if frame is None:
                raise RuntimeError(
                    f"dxcam_grab_returned_none_after_{_DXCAM_GRAB_RETRY_ATTEMPTS + 1}_attempts"
                )
        image = Image.fromarray(frame).convert("RGB")
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )


class Win32CaptureBackend:
    def __init__(self, *, logger=None, selection: str = _CAPTURE_BACKEND_AUTO) -> None:
        self._logger = logger
        self.selection = str(selection or _CAPTURE_BACKEND_AUTO).strip().lower()
        # Legacy "imagegrab" selection migrates to MSS (same GDI capability, faster + cross-platform).
        if self.selection == _CAPTURE_BACKEND_IMAGEGRAB:
            self.selection = _CAPTURE_BACKEND_MSS
        if self.selection not in {
            _CAPTURE_BACKEND_AUTO,
            _CAPTURE_BACKEND_SMART,
            _CAPTURE_BACKEND_DXCAM,
            _CAPTURE_BACKEND_MSS,
            _CAPTURE_BACKEND_PYAUTOGUI,
            _CAPTURE_BACKEND_PRINTWINDOW,
        }:
            self.selection = _CAPTURE_BACKEND_AUTO
        self._mss_backend = MssCaptureBackend(logger=self._logger)
        self._pyautogui_backend = PyAutoGuiCaptureBackend(logger=self._logger)
        self._printwindow_backend = PrintWindowCaptureBackend(logger=self._logger)
        self._dxcam_backend = DxcamCaptureBackend(logger=self._logger)
        # Linux-only: HTTP bridge to Electron desktopCapturer for Wayland
        # (and as XWayland/X11 fallback). Lazy import to avoid pulling
        # httpx on platforms that don't need it.
        from .capture_platform import is_linux  # noqa: PLC0415

        if is_linux():
            from .electron_capture import ElectronCaptureBackend  # noqa: PLC0415

            self._electron_backend: CaptureBackend | None = ElectronCaptureBackend(
                logger=self._logger
            )
        else:
            self._electron_backend = None
        self._backends = self._build_backends()
        self._last_backend_lock = threading.RLock()
        self._last_backend_kind = ""
        self._last_backend_detail = ""
        self._logged_fallback_details: set[str] = set()

    @property
    def last_backend_kind(self) -> str:
        with self._last_backend_lock:
            return self._last_backend_kind

    @property
    def last_backend_detail(self) -> str:
        with self._last_backend_lock:
            return self._last_backend_detail

    def _set_last_backend(self, *, kind: str, detail: str) -> None:
        with self._last_backend_lock:
            self._last_backend_kind = kind
            self._last_backend_detail = detail

    def _build_backends(self) -> list[CaptureBackend]:
        # Default fallback chain: dxcam → mss → pyautogui (cross-platform GDI
        # progression). PrintWindow is intentionally NOT in the default chain
        # because it's a "render to DC" mechanism that often produces stale
        # frames on DirectX/Unity games and is slower than BitBlt-based
        # backends. It's still reachable as an explicit user selection
        # (mainly for capturing occluded windows) and as the Smart-mode
        # background-target backend.
        from .capture_platform import (  # noqa: PLC0415
            is_linux,
            is_linux_wayland_session,
            is_win32_only_backend_kind,
            is_windows,
        )

        def _filter(backends: list[CaptureBackend]) -> list[CaptureBackend]:
            """Remove backends that require Win32 APIs on non-Windows hosts,
            and append the Electron HTTP bridge as a last-resort entry on Linux
            (needed for pure Wayland where MSS/PyAutoGUI return blank frames).
            """
            if is_windows():
                return list(backends)
            cross_platform = [
                b
                for b in backends
                if not is_win32_only_backend_kind(str(getattr(b, "kind", "")))
            ]
            if is_linux() and self._electron_backend is not None:
                if (
                    is_linux_wayland_session()
                    and self.selection
                    in {_CAPTURE_BACKEND_AUTO, _CAPTURE_BACKEND_SMART}
                ):
                    # On Wayland, MSS/PyAutoGUI can import successfully yet
                    # return black frames. Prefer Electron's portal-backed
                    # path for automatic selections instead of treating those
                    # import probes as capture viability.
                    return [self._electron_backend]
                # X11/XWayland: MSS/PyAutoGUI work; Electron is a tail fallback.
                cross_platform.append(self._electron_backend)
            return cross_platform

        if self.selection == _CAPTURE_BACKEND_DXCAM:
            return _filter([self._dxcam_backend, self._mss_backend, self._pyautogui_backend])
        if self.selection == _CAPTURE_BACKEND_MSS:
            return _filter([self._mss_backend, self._dxcam_backend, self._pyautogui_backend])
        if self.selection == _CAPTURE_BACKEND_PYAUTOGUI:
            return _filter([self._pyautogui_backend, self._dxcam_backend, self._mss_backend])
        if self.selection == _CAPTURE_BACKEND_PRINTWINDOW:
            return _filter(
                [
                    self._printwindow_backend,
                    self._dxcam_backend,
                    self._mss_backend,
                    self._pyautogui_backend,
                ]
            )
        if self.selection == _CAPTURE_BACKEND_SMART:
            return _filter(
                [
                    self._dxcam_backend,
                    self._mss_backend,
                    self._pyautogui_backend,
                    self._printwindow_backend,
                ]
            )
        return _filter([self._dxcam_backend, self._mss_backend, self._pyautogui_backend])

    def _ordered_backends_for_target(self, target: DetectedGameWindow) -> list[CaptureBackend]:
        from .capture_platform import is_linux, is_linux_wayland_session, is_windows  # noqa: PLC0415

        is_windows_host = is_windows()
        is_linux_host = is_linux()
        is_wayland_host = is_linux_wayland_session() if is_linux_host else False
        window_level_backends = [
            backend
            for backend in self._backends
            if str(getattr(backend, "kind", "")) == "electron"
        ]
        pixel_backends = [
            backend
            for backend in self._backends
            if str(getattr(backend, "kind", "")) not in {"electron", _CAPTURE_BACKEND_PRINTWINDOW}
        ]
        if self.selection == _CAPTURE_BACKEND_PRINTWINDOW:
            if not (
                is_windows_host and self._printwindow_backend in self._backends
            ):
                return list(self._backends)
            # Explicit PrintWindow selection: user is opting into the only
            # backend that can capture occluded / background windows. If we
            # silently fell through to dxcam/mss/pyautogui on a background
            # target after PrintWindow failed, those backends read whatever
            # is on screen — usually the occluding window — and OCR would
            # produce confident garbage from the wrong source. Match Smart
            # mode's strictness for background targets here.
            if bool(getattr(target, "is_minimized", False)):
                raise RuntimeError("printwindow: target_window_minimized_for_capture")
            if bool(getattr(target, "is_foreground", False)):
                # Foreground: other backends would also see the right window,
                # so falling through after PrintWindow failure is safe.
                return list(self._backends)
            return [self._printwindow_backend]
        if self.selection != _CAPTURE_BACKEND_SMART:
            return list(self._backends)
        if bool(getattr(target, "is_minimized", False)):
            raise RuntimeError("smart: target_window_minimized_for_capture")
        if not is_windows_host:
            if not is_linux_host:
                return window_level_backends + pixel_backends
            if bool(getattr(target, "is_foreground", False)):
                return window_level_backends + pixel_backends
            if not is_wayland_host:
                return window_level_backends + pixel_backends
            if window_level_backends:
                return window_level_backends
            raise RuntimeError("smart: background_capture_requires_window_backend")
        if bool(getattr(target, "is_foreground", False)):
            foreground_backends = {
                id(self._dxcam_backend),
                id(self._mss_backend),
                id(self._pyautogui_backend),
            }
            return [
                backend
                for backend in self._backends
                if id(backend) in foreground_backends
            ]
        # Background target: PrintWindow is the only backend that can plausibly
        # capture occluded windows (others read screen pixels and would grab
        # the overlapping window). Quality is unreliable; ocr_reader emits
        # `backend_not_suitable_for_background` warning when it returns empty.
        return [self._printwindow_backend]

    def is_available(self) -> bool:
        return any(backend.is_available() for backend in self._backends)

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        errors: list[str] = []
        backends = self._ordered_backends_for_target(target)
        selected_kind = (
            str(getattr(backends[0], "kind", self.selection))
            if backends
            else self.selection
        )
        for backend in backends:
            kind = str(getattr(backend, "kind", backend.__class__.__name__))
            if not backend.is_available():
                errors.append(f"{kind}_unavailable")
                continue
            try:
                frame = backend.capture_frame(target, profile)
                frame_info = getattr(frame, "info", None)
                frame_backend_detail = (
                    str(frame_info.get("galgame_capture_backend_detail") or "")
                    if isinstance(frame_info, dict)
                    else ""
                )
                fallback_detail = (
                    f"{selected_kind}_unavailable_fallback"
                    if kind != selected_kind and f"{selected_kind}_unavailable" in errors
                    else f"{selected_kind}_failed_fallback"
                    if kind != selected_kind
                    and any(error.startswith(f"{selected_kind}_failed:") for error in errors)
                    else ""
                )
                last_backend_detail = fallback_detail or frame_backend_detail or (
                    "dxcam_unavailable_fallback"
                    if kind != _CAPTURE_BACKEND_DXCAM and "dxcam_unavailable" in errors
                    else "dxcam_failed_fallback"
                    if kind != _CAPTURE_BACKEND_DXCAM
                    and any(error.startswith("dxcam_failed:") for error in errors)
                    else "selected"
                )
                self._set_last_backend(kind=kind, detail=last_backend_detail)
                if isinstance(frame_info, dict):
                    frame_info["galgame_capture_backend_kind"] = kind
                    frame_info["galgame_capture_backend_detail"] = last_backend_detail
                if fallback_detail:
                    self._warn_fallback_once(selected_kind, kind, fallback_detail)
                return frame
            except Exception as exc:
                errors.append(f"{kind}_failed:{exc}")
                if any(
                    marker in str(exc)
                    for marker in (
                        "target_window_not_resolved_for_capture",
                        "target_window_invalid_for_capture",
                        "target_window_not_visible_for_capture",
                        "target_window_minimized_for_capture",
                    )
                ):
                    raise
                continue
        if self.selection == _CAPTURE_BACKEND_SMART and not bool(
            getattr(target, "is_foreground", False)
        ):
            self._set_last_backend(kind=_CAPTURE_BACKEND_SMART, detail="background_requires_printwindow")
            raise RuntimeError(
                "smart: background_capture_requires_printwindow"
                + (f": {'; '.join(errors)}" if errors else "")
            )
        if self.selection != _CAPTURE_BACKEND_AUTO:
            raise RuntimeError(
                f"{self.selection}: capture_backend_unavailable"
                + (f": {'; '.join(errors)}" if errors else "")
            )
        raise RuntimeError("; ".join(errors) or "capture_backend_unavailable")

    def _warn_fallback_once(self, selected_kind: str, actual_kind: str, detail: str) -> None:
        if detail in self._logged_fallback_details:
            return
        self._logged_fallback_details.add(detail)
        if self._logger is None:
            return
        try:
            self._logger.warning(
                "ocr_reader capture backend {} unavailable/failed; falling back to {} ({})",
                selected_kind,
                actual_kind,
                detail,
            )
        except Exception:
            pass

