from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any

from .models import DATA_SOURCE_OCR_READER, SharedStatePayload
from ._win32_input_types import (
    PROCESS_QUERY_LIMITED_INFORMATION,
    SW_RESTORE,
    TOKEN_QUERY,
    TokenElevation,
)


_LAST_FOCUS_WINDOW_DIAGNOSTIC = ""
_LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK = threading.Lock()
_WAIT_EVENT = threading.Event()
_LOGGER = logging.getLogger(__name__)


def _warn_input_exception(message: str, exc: Exception) -> None:
    _LOGGER.warning("%s: %s", message, exc, exc_info=True)


def _wait_seconds(delay: float) -> None:
    _WAIT_EVENT.wait(max(0.0, float(delay or 0.0)))


def _set_last_focus_window_diagnostic(value: str) -> None:
    global _LAST_FOCUS_WINDOW_DIAGNOSTIC
    with _LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK:
        _LAST_FOCUS_WINDOW_DIAGNOSTIC = str(value or "")


def _get_last_focus_window_diagnostic() -> str:
    with _LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK:
        return str(_LAST_FOCUS_WINDOW_DIAGNOSTIC or "")


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = (("TokenIsElevated", wintypes.DWORD),)


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    )


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    )


def _runtime_target(shared: SharedStatePayload) -> dict[str, Any]:
    active_source = str(shared.get("active_data_source") or "").strip()
    if active_source == DATA_SOURCE_OCR_READER:
        preferred_keys = ("ocr_reader_runtime", "memory_reader_runtime")
    else:
        preferred_keys = ("memory_reader_runtime", "ocr_reader_runtime")

    fallback: dict[str, Any] = {}
    for key in preferred_keys:
        runtime = shared.get(key)
        if isinstance(runtime, dict):
            pid = int(runtime.get("pid") or 0)
            process_name = str(
                runtime.get("effective_process_name") or runtime.get("process_name") or ""
            ).strip()
            if pid > 0 or process_name:
                target = {
                    "pid": pid,
                    "process_name": process_name,
                    "window_title": str(
                        runtime.get("effective_window_title") or runtime.get("window_title") or ""
                    ).strip(),
                }
                if pid > 0 and process_name:
                    return target
                if not fallback:
                    fallback = target
    return fallback or {"pid": 0, "process_name": "", "window_title": ""}


def _find_window_for_pid(pid: int) -> tuple[int, tuple[int, int, int, int]]:
    try:
        import win32gui
        import win32process
    except ImportError:
        win32gui = None
        win32process = None

    if win32gui is not None and win32process is not None:
        matches: list[tuple[int, int, tuple[int, int, int, int]]] = []

        def _pywin_callback(hwnd: int, _lparam: int) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.IsIconic(hwnd):
                return
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if int(window_pid) != int(pid):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = int(right - left)
            height = int(bottom - top)
            if width < 160 or height < 120:
                return
            matches.append((width * height, int(hwnd), (int(left), int(top), int(right), int(bottom))))

        win32gui.EnumWindows(_pywin_callback, None)
        if matches:
            matches.sort(reverse=True)
            _, hwnd, rect = matches[0]
            return hwnd, rect

    user32 = ctypes.windll.user32
    matches: list[tuple[int, int, tuple[int, int, int, int]]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) != int(pid):
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 160 or height < 120:
            return True
        area = width * height
        matches.append((area, int(hwnd), (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))))
        return True

    user32.EnumWindows(enum_proc_type(_callback), 0)
    if not matches:
        return 0, (0, 0, 0, 0)
    matches.sort(reverse=True)
    _, hwnd, rect = matches[0]
    return hwnd, rect


def _window_text(hwnd: int) -> str:
    try:
        import win32gui
    except ImportError:
        win32gui = None
    if win32gui is not None:
        try:
            return str(win32gui.GetWindowText(hwnd) or "")
        except Exception as exc:
            _warn_input_exception("local input window text lookup failed via pywin32", exc)
            return ""
    user32 = ctypes.windll.user32
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return str(buffer.value or "")
    except Exception as exc:
        _warn_input_exception("local input window text lookup failed via user32", exc)
        return ""


def _root_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2))
        return root or int(hwnd)
    except Exception as exc:
        _warn_input_exception("local input root window lookup failed", exc)
        return int(hwnd)


def _window_process_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception as exc:
        _warn_input_exception("local input window process lookup failed", exc)
        return 0


def _foreground_matches_target_window(foreground_hwnd: int, target_hwnd: int, target_pid: int) -> bool:
    if not foreground_hwnd or not target_hwnd:
        return False
    if int(foreground_hwnd) == int(target_hwnd):
        return True
    foreground_root = _root_window_handle(int(foreground_hwnd))
    target_root = _root_window_handle(int(target_hwnd))
    if foreground_root and target_root and foreground_root == target_root:
        return True
    foreground_pid = _window_process_id(int(foreground_hwnd)) or _window_process_id(foreground_root)
    return bool(foreground_pid and target_pid and foreground_pid == int(target_pid))


def _focus_window(hwnd: int) -> bool:
    _set_last_focus_window_diagnostic("")
    user32 = ctypes.windll.user32
    target_pid = _window_process_id(hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(-1)
    except Exception as exc:
        _warn_input_exception("local input AllowSetForegroundWindow failed", exc)
        _set_last_focus_window_diagnostic(f"AllowSetForegroundWindow failed: {exc}")
    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached_foreground = False
    attached_target = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception as exc:
        _warn_input_exception("local input SetForegroundWindow sequence failed", exc)
        _set_last_focus_window_diagnostic(f"SetForegroundWindow failed: {exc}")
    finally:
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
    _wait_seconds(0.12)
    try:
        foreground_hwnd = int(user32.GetForegroundWindow())
        focused = _foreground_matches_target_window(
            foreground_hwnd,
            int(hwnd),
            int(target_pid),
        )
        if focused:
            _set_last_focus_window_diagnostic("")
            return True
        if foreground_hwnd != int(hwnd):
            if not _get_last_focus_window_diagnostic():
                fg_pid = _window_process_id(foreground_hwnd) or 0
                _set_last_focus_window_diagnostic(
                    f"foreground_mismatch: fg_hwnd={foreground_hwnd} fg_pid={fg_pid} "
                    f"target_hwnd={hwnd} target_pid={target_pid}"
                )
        elif not _get_last_focus_window_diagnostic():
            _set_last_focus_window_diagnostic("foreground window did not match target")
        return False
    except Exception as exc:
        _warn_input_exception("local input foreground verification failed", exc)
        _set_last_focus_window_diagnostic(f"foreground verification failed: {exc}")
        return False


def _is_current_process_elevated() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:
        _warn_input_exception("local input current elevation lookup failed", exc)
        return None


def _is_process_elevated(pid: int) -> bool | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    process = None
    token = wintypes.HANDLE()
    try:
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            return None
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        elevation = TOKEN_ELEVATION()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            return None
        return bool(elevation.TokenIsElevated)
    except Exception as exc:
        _warn_input_exception("local input target elevation lookup failed", exc)
        return None
    finally:
        try:
            if token:
                kernel32.CloseHandle(token)
        except Exception as exc:
            _warn_input_exception("local input token handle close failed", exc)
        try:
            if process:
                kernel32.CloseHandle(process)
        except Exception as exc:
            _warn_input_exception("local input process handle close failed", exc)
