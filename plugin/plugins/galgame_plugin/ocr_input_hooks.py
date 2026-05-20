from __future__ import annotations

import ctypes
import os
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .ocr_runtime_types import (
    _KEYBOARD_ADVANCE_VK_CODES,
    _WH_KEYBOARD_LL,
    _WH_MOUSE_LL,
    _WM_KEYDOWN,
    _WM_LBUTTONDOWN,
    _WM_LBUTTONUP,
    _WM_MOUSEWHEEL,
    _WM_SYSKEYDOWN,
)

__all__ = [
    "ForegroundAdvanceConsumeResult",
    "_MouseWheelEvent",
    "_MouseWheelMonitor",
    "_PendingMouseInputEvent",
]


# Runtime bridge to avoid circular import after file extraction.
# ocr_reader re-exports these symbols; the module is resolved lazily via
# sys.modules so that ocr_input_hooks can be imported before ocr_reader.
def _default_foreground_window_handle() -> int:
    reader_module = sys.modules.get("plugin.plugins.galgame_plugin.ocr_reader")
    reader_func = getattr(reader_module, "_foreground_window_handle", None)
    if callable(reader_func):
        try:
            return int(reader_func())
        except Exception:
            return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return 0


def _default_window_handle_from_point(x: int, y: int) -> int:
    reader_module = sys.modules.get("plugin.plugins.galgame_plugin.ocr_reader")
    reader_func = getattr(reader_module, "_window_handle_from_point", None)
    if callable(reader_func):
        try:
            return int(reader_func(int(x), int(y)))
        except Exception:
            return 0
    if os.name != "nt":
        return 0
    try:
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [POINT]
        return int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
    except Exception:
        return 0


@dataclass(slots=True)
class _MouseWheelEvent:
    seq: int
    ts: float
    delta: int
    foreground_hwnd: int
    point_hwnd: int = 0
    kind: str = "wheel"
    key_code: int = 0


@dataclass(slots=True)
class ForegroundAdvanceConsumeResult:
    triggered: bool = False
    matched_count: int = 0
    consumed_count: int = 0
    first_event_ts: float = 0.0
    last_event_ts: float = 0.0
    detected_at: float = 0.0
    last_event_age_seconds: float = 0.0
    last_kind: str = ""
    last_delta: int = 0
    last_matched: bool = False
    last_match_reason: str = ""
    coalesced: bool = False
    coalesced_count: int = 0


@dataclass(slots=True)
class _PendingMouseInputEvent:
    ts: float
    delta: int
    x: int
    y: int
    kind: str = "wheel"
    foreground_hwnd: int = 0
    point_hwnd: int = 0
    key_code: int = 0


class _MouseWheelMonitor:
    _MAX_EVENTS = 96
    _MAX_EVENT_AGE_SECONDS = 15.0

    def __init__(
        self,
        *,
        time_fn: Callable[[], float],
        logger: Any | None = None,
        foreground_window_handle_fn: Callable[[], int] | None = None,
        window_handle_from_point_fn: Callable[[int, int], int] | None = None,
    ) -> None:
        self._foreground_window_handle_fn = (
            foreground_window_handle_fn or _default_foreground_window_handle
        )
        self._window_handle_from_point_fn = (
            window_handle_from_point_fn or _default_window_handle_from_point
        )
        self._time_fn = time_fn
        self._logger = logger
        self._post_quit_failure_logged = False
        self._callback_failure_logged = False
        self._unhook_failure_logged = False
        self._lock = threading.Lock()
        self._events: list[_MouseWheelEvent] = []
        self._pending_events: deque[_PendingMouseInputEvent] = deque(
            maxlen=self._MAX_EVENTS * 4
        )
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook_handle = 0
        self._keyboard_hook_handle = 0
        self._callback = None
        self._keyboard_callback = None
        self._stop = threading.Event()

    def _debug_once(self, flag_name: str, message: str, exc: Exception) -> None:
        if getattr(self, flag_name):
            return
        setattr(self, flag_name, True)
        if self._logger is None:
            return
        try:
            self._logger.debug(message, exc)
        except Exception:
            pass

    def start(self) -> bool:
        if os.name != "nt":
            return False
        thread = self._thread
        if thread is not None and thread.is_alive():
            if not self._stop.is_set():
                return True
            if thread is not threading.current_thread():
                thread.join(timeout=0.25)
            if thread.is_alive():
                return False
        self._thread = None
        self._hook_handle = 0
        self._keyboard_hook_handle = 0
        self._thread_id = 0
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="galgame-ocr-wheel-monitor",
            daemon=True,
        )
        self._thread.start()
        return True

    def ensure_running(self) -> bool:
        return self.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def last_seq(self) -> int:
        self._drain_pending_events()
        with self._lock:
            return int(self._seq or 0)

    def stop(self, *, join_timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if os.name == "nt" and self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    int(self._thread_id),
                    0x0012,  # WM_QUIT
                    0,
                    0,
                )
            except Exception as exc:
                self._debug_once(
                    "_post_quit_failure_logged",
                    "ocr_reader wheel monitor stop signal failed: {}",
                    exc,
                )
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(join_timeout)))
        if thread is not None and not thread.is_alive() and self._thread is thread:
            self._thread = None

    def events_after(self, seq: int) -> list[_MouseWheelEvent]:
        self.ensure_running()
        self._drain_pending_events()
        with self._lock:
            self._prune_locked()
            return [event for event in self._events if event.seq > seq]

    def _enqueue_pending_event(
        self,
        *,
        delta: int = 0,
        kind: str = "wheel",
        x: int = 0,
        y: int = 0,
        foreground_hwnd: int = 0,
        point_hwnd: int = 0,
        key_code: int = 0,
    ) -> None:
        self._pending_events.append(
            _PendingMouseInputEvent(
                ts=self._time_fn(),
                delta=int(delta),
                x=int(x),
                y=int(y),
                kind=str(kind or "wheel"),
                foreground_hwnd=max(0, int(foreground_hwnd or 0)),
                point_hwnd=max(0, int(point_hwnd or 0)),
                key_code=max(0, int(key_code or 0)),
            )
        )

    def _drain_pending_events(self) -> None:
        pending: list[_PendingMouseInputEvent] = []
        while True:
            try:
                pending.append(self._pending_events.popleft())
            except IndexError:
                break
        if not pending:
            return
        resolved: list[tuple[_PendingMouseInputEvent, int, int]] = []
        for event in pending:
            foreground_hwnd = int(event.foreground_hwnd or 0) or self._foreground_window_handle_fn()
            point_hwnd = int(event.point_hwnd or 0) or self._window_handle_from_point_fn(event.x, event.y)
            resolved.append((event, foreground_hwnd, point_hwnd))
        with self._lock:
            for event, foreground_hwnd, point_hwnd in resolved:
                self._seq += 1
                self._events.append(
                    _MouseWheelEvent(
                        seq=self._seq,
                        ts=float(event.ts),
                        delta=int(event.delta),
                        foreground_hwnd=max(0, int(foreground_hwnd or 0)),
                        point_hwnd=max(0, int(point_hwnd or 0)),
                        kind=str(event.kind or "wheel"),
                        key_code=max(0, int(event.key_code or 0)),
                    )
                )
            self._prune_locked(now=max(event.ts for event in pending))

    def _prune_locked(self, *, now: float | None = None) -> None:
        now = self._time_fn() if now is None else now
        min_ts = now - self._MAX_EVENT_AGE_SECONDS
        self._events = [
            event for event in self._events[-self._MAX_EVENTS :]
            if event.ts >= min_ts
        ]

    def _run(self) -> None:
        try:
            from ctypes import wintypes

            low_level_mouse_proc = getattr(ctypes, "WINFUNCTYPE", None)
            if low_level_mouse_proc is None:
                return
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._thread_id = int(kernel32.GetCurrentThreadId())

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("pt", POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p),
                ]

            proc_type = low_level_mouse_proc(
                ctypes.c_longlong,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            hhook_type = getattr(wintypes, "HHOOK", wintypes.HANDLE)
            hinstance_type = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
            user32.CallNextHookEx.restype = ctypes.c_longlong
            user32.CallNextHookEx.argtypes = [
                hhook_type,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]

            def mouse_callback(n_code, w_param, l_param):
                message = int(w_param)
                if n_code >= 0 and message in {_WM_MOUSEWHEEL, _WM_LBUTTONDOWN, _WM_LBUTTONUP}:
                    try:
                        payload = ctypes.cast(
                            l_param,
                            ctypes.POINTER(MSLLHOOKSTRUCT),
                        ).contents
                        if message == _WM_MOUSEWHEEL:
                            delta = ctypes.c_short((int(payload.mouseData) >> 16) & 0xFFFF).value
                            if delta:
                                self._enqueue_pending_event(
                                    delta=delta,
                                    kind="wheel",
                                    x=int(payload.pt.x),
                                    y=int(payload.pt.y),
                                    foreground_hwnd=self._foreground_window_handle_fn(),
                                    point_hwnd=self._window_handle_from_point_fn(
                                        int(payload.pt.x),
                                        int(payload.pt.y),
                                    ),
                                )
                        else:
                            self._enqueue_pending_event(
                                kind="left_click",
                                x=int(payload.pt.x),
                                y=int(payload.pt.y),
                                foreground_hwnd=self._foreground_window_handle_fn(),
                                point_hwnd=self._window_handle_from_point_fn(
                                    int(payload.pt.x),
                                    int(payload.pt.y),
                                ),
                            )
                    except Exception as exc:
                        self._debug_once(
                            "_callback_failure_logged",
                            "ocr_reader wheel monitor callback failed: {}",
                            exc,
                        )
                return user32.CallNextHookEx(
                    self._hook_handle,
                    n_code,
                    w_param,
                    l_param,
                )

            def keyboard_callback(n_code, w_param, l_param):
                message = int(w_param)
                if n_code >= 0 and message in {_WM_KEYDOWN, _WM_SYSKEYDOWN}:
                    try:
                        payload = ctypes.cast(
                            l_param,
                            ctypes.POINTER(KBDLLHOOKSTRUCT),
                        ).contents
                        key_code = int(payload.vkCode or 0)
                        if key_code in _KEYBOARD_ADVANCE_VK_CODES:
                            self._enqueue_pending_event(
                                delta=0,
                                kind="key",
                                key_code=key_code,
                                foreground_hwnd=self._foreground_window_handle_fn(),
                            )
                    except Exception as exc:
                        self._debug_once(
                            "_callback_failure_logged",
                            "ocr_reader keyboard monitor callback failed: {}",
                            exc,
                        )
                return user32.CallNextHookEx(
                    self._keyboard_hook_handle,
                    n_code,
                    w_param,
                    l_param,
                )

            self._callback = proc_type(mouse_callback)
            self._keyboard_callback = proc_type(keyboard_callback)
            user32.SetWindowsHookExW.restype = hhook_type
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                proc_type,
                hinstance_type,
                wintypes.DWORD,
            ]
            self._hook_handle = int(user32.SetWindowsHookExW(_WH_MOUSE_LL, self._callback, 0, 0))
            self._keyboard_hook_handle = int(
                user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._keyboard_callback, 0, 0)
            )
            if not self._hook_handle and not self._keyboard_hook_handle:
                return

            msg = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_handle:
                try:
                    ctypes.windll.user32.UnhookWindowsHookEx(self._hook_handle)
                except Exception as exc:
                    self._debug_once(
                        "_unhook_failure_logged",
                        "ocr_reader wheel monitor unhook failed: {}",
                        exc,
                    )
            if self._keyboard_hook_handle:
                try:
                    ctypes.windll.user32.UnhookWindowsHookEx(self._keyboard_hook_handle)
                except Exception as exc:
                    self._debug_once(
                        "_unhook_failure_logged",
                        "ocr_reader keyboard monitor unhook failed: {}",
                        exc,
                    )
            self._hook_handle = 0
            self._keyboard_hook_handle = 0
            self._thread_id = 0
