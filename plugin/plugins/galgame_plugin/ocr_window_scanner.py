from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .ocr_runtime_types import *
from .ocr_capture_backends import *


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
