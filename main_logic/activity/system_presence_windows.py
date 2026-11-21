# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Windows notification source for ``system_presence`` (Windows only).

Imported lazily by ``system_presence.ensure_system_presence_watcher_started``
and only when ``sys.platform == 'win32'``.

A daemon thread owns a hidden top-level window and a message loop, and
subscribes to three notifications for *this* session only:

* display on/off: ``RegisterPowerSettingNotification`` with
  ``GUID_SESSION_DISPLAY_STATUS`` (per-session; Windows sends the current
  value right after registration);
* lock/unlock and connect/disconnect: ``WTSRegisterSessionNotification``
  with ``NOTIFY_FOR_THIS_SESSION``; the initial lock state comes from
  ``WTSQuerySessionInformationW(WTSSessionInfoEx)``;
* sleep/resume: ``WM_POWERBROADCAST`` (``PBT_APMSUSPEND`` /
  ``PBT_APMRESUMEAUTOMATIC`` / ``PBT_APMRESUMESUSPEND``), which is broadcast
  to top-level windows, hence a hidden top-level window rather than a
  message-only one.

Any failure marks the state as failed (allow) and logs once. On interpreter
exit the window is closed, which unregisters both notifications.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import threading
from ctypes import wintypes

from main_logic.activity.system_presence import (
    PBT_POWERSETTINGCHANGE,
    SystemPresenceState,
    apply_power_broadcast,
    apply_session_change,
)

logger = logging.getLogger(__name__)

# Window messages
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

NOTIFY_FOR_THIS_SESSION = 0
DEVICE_NOTIFY_WINDOW_HANDLE = 0
WTS_CURRENT_SERVER_HANDLE = None
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_SESSION_INFO_EX = 25  # WTS_INFO_CLASS.WTSSessionInfoEx
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', wintypes.DWORD),
        ('Data2', wintypes.WORD),
        ('Data3', wintypes.WORD),
        ('Data4', ctypes.c_ubyte * 8),
    ]


GUID_SESSION_DISPLAY_STATUS = GUID(
    0x2B84C20E, 0xAD23, 0x4DDF,
    (ctypes.c_ubyte * 8)(0x93, 0xDB, 0x05, 0xFF, 0xBD, 0x7E, 0xFC, 0xA5),
)


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ('PowerSetting', GUID),
        ('DataLength', wintypes.DWORD),
        ('Data', ctypes.c_ubyte * 1),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT),
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
        ('hIconSm', wintypes.HICON),
    ]


class WTSINFOEX_LEVEL1_W(ctypes.Structure):
    _fields_ = [
        ('SessionId', wintypes.ULONG),
        ('SessionState', ctypes.c_int),
        ('SessionFlags', wintypes.LONG),
        ('WinStationName', wintypes.WCHAR * 33),
        ('UserName', wintypes.WCHAR * 21),
        ('DomainName', wintypes.WCHAR * 18),
        ('LogonTime', wintypes.LARGE_INTEGER),
        ('ConnectTime', wintypes.LARGE_INTEGER),
        ('DisconnectTime', wintypes.LARGE_INTEGER),
        ('LastInputTime', wintypes.LARGE_INTEGER),
        ('CurrentTime', wintypes.LARGE_INTEGER),
        ('IncomingBytes', wintypes.DWORD),
        ('OutgoingBytes', wintypes.DWORD),
        ('IncomingFrames', wintypes.DWORD),
        ('OutgoingFrames', wintypes.DWORD),
        ('IncomingCompressedBytes', wintypes.DWORD),
        ('OutgoingCompressedBytes', wintypes.DWORD),
    ]


class WTSINFOEX_LEVEL_W(ctypes.Union):
    _fields_ = [('Level1', WTSINFOEX_LEVEL1_W)]


class WTSINFOEXW(ctypes.Structure):
    _fields_ = [('Level', wintypes.DWORD), ('Data', WTSINFOEX_LEVEL_W)]


def _guid_equal(a: GUID, b: GUID) -> bool:
    return bytes(a) == bytes(b)


def _bind_apis():
    """Load DLLs and declare signatures (64-bit safe handle types)."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    wtsapi32 = ctypes.WinDLL('wtsapi32', use_last_error=True)

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
    user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
    user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
    user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL

    wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
    wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
    wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
    wtsapi32.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wtsapi32.WTSFreeMemory.restype = None
    return user32, kernel32, wtsapi32


def query_session_locked(wtsapi32) -> bool | None:
    """Current lock state of this session, ``None`` when unknown."""
    buf = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTS_SESSION_INFO_EX,
        ctypes.byref(buf), ctypes.byref(size),
    ):
        return None
    try:
        if not buf.value or size.value < ctypes.sizeof(WTSINFOEXW):
            return None
        info = ctypes.cast(buf, ctypes.POINTER(WTSINFOEXW)).contents
        if info.Level != 1:
            return None
        flags = info.Data.Level1.SessionFlags
        if flags == WTS_SESSIONSTATE_LOCK:
            return True
        if flags == WTS_SESSIONSTATE_UNLOCK:
            return False
        return None
    finally:
        wtsapi32.WTSFreeMemory(buf)


class _Watcher:
    CLASS_NAME = 'NekoSystemPresenceWatcher'

    def __init__(self, state: SystemPresenceState) -> None:
        self._state = state
        self._hwnd = None
        self._power_notify = None
        self._session_registered = False
        self._class_registered = False
        self._hinstance = None
        self._wndproc = WNDPROC(self._window_proc)  # keep a reference alive
        self._apis = None
        self._error_logged = False
        self.thread = threading.Thread(
            target=self._run, name='neko-system-presence', daemon=True,
        )

    def _fail(self, message: str, exc: BaseException | None = None) -> None:
        self._state.mark_failed()
        if not self._error_logged:
            self._error_logged = True
            logger.warning('System presence watcher stopped, proactive chat is no longer gated by it: %s%s',
                           message, f' ({exc})' if exc else '')

    def _query_lock_state(self) -> bool | None:
        return query_session_locked(self._apis[2])

    def _refresh_lock_state(self) -> None:
        locked = self._query_lock_state()
        if locked is not None:
            self._state.on_session_lock_state(locked)

    def _window_proc(self, hwnd, msg, wparam, lparam):
        user32 = self._apis[0]
        try:
            if msg == WM_POWERBROADCAST:
                display_status = None
                if wparam == PBT_POWERSETTINGCHANGE and lparam:
                    setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    if (
                        _guid_equal(setting.PowerSetting, GUID_SESSION_DISPLAY_STATUS)
                        and setting.DataLength >= 4
                    ):
                        display_status = ctypes.cast(
                            ctypes.addressof(setting) + POWERBROADCAST_SETTING.Data.offset,
                            ctypes.POINTER(wintypes.DWORD),
                        ).contents.value
                apply_power_broadcast(self._state, wparam, display_status)
                return 1  # TRUE
            if msg == WM_WTSSESSION_CHANGE:
                apply_session_change(self._state, wparam, self._query_lock_state)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._unregister()
                user32.PostQuitMessage(0)
                return 0
        except Exception as exc:  # noqa: BLE001 - never raise into the OS callback
            self._fail('notification handling failed', exc)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _unregister(self) -> None:
        if not self._apis:
            return
        user32, _kernel32, wtsapi32 = self._apis
        if self._power_notify:
            try:
                user32.UnregisterPowerSettingNotification(self._power_notify)
            except Exception:  # noqa: BLE001
                pass
            self._power_notify = None
        if self._session_registered and self._hwnd:
            try:
                wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
            except Exception:  # noqa: BLE001
                pass
            self._session_registered = False

    def _run(self) -> None:
        try:
            self._apis = _bind_apis()
            user32, kernel32, wtsapi32 = self._apis
            hinstance = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinstance
            wc.lpszClassName = self.CLASS_NAME
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                self._fail(f'RegisterClassExW error {ctypes.get_last_error()}')
                return
            self._class_registered = True
            self._hinstance = hinstance
            # Hidden (never shown) top-level window: broadcast WM_POWERBROADCAST
            # is not delivered to message-only windows.
            self._hwnd = user32.CreateWindowExW(
                0, self.CLASS_NAME, self.CLASS_NAME, 0,
                0, 0, 0, 0, None, None, hinstance, None,
            )
            if not self._hwnd:
                self._fail(f'CreateWindowExW error {ctypes.get_last_error()}')
                return
            if not wtsapi32.WTSRegisterSessionNotification(self._hwnd, NOTIFY_FOR_THIS_SESSION):
                self._fail(f'WTSRegisterSessionNotification error {ctypes.get_last_error()}')
                user32.DestroyWindow(self._hwnd)
                return
            self._session_registered = True
            # Read the initial lock state only after session notifications are
            # registered: a lock in between is then either seen by the query or
            # queued as a notification, never lost. Read it before registering
            # the display notification so the initial display report (sent on
            # registration) meets a known lock state.
            self._refresh_lock_state()
            self._power_notify = user32.RegisterPowerSettingNotification(
                self._hwnd, ctypes.byref(GUID_SESSION_DISPLAY_STATUS), DEVICE_NOTIFY_WINDOW_HANDLE,
            )
            if not self._power_notify:
                self._fail(f'RegisterPowerSettingNotification error {ctypes.get_last_error()}')
                user32.DestroyWindow(self._hwnd)
                return
            atexit.register(self.close)
            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:  # WM_QUIT
                    break
                if result == -1:
                    self._fail(f'GetMessageW error {ctypes.get_last_error()}')
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001 - must fail open
            self._fail('watcher thread crashed', exc)
        finally:
            self._unregister()
            if self._class_registered:
                try:
                    self._apis[0].UnregisterClassW(self.CLASS_NAME, self._hinstance)
                except Exception:  # noqa: BLE001
                    pass
            # The loop is gone; nothing keeps the state current any more.
            self._state.mark_failed()

    def close(self) -> None:
        """Ask the watcher thread to tear down. Non-blocking beyond a short join."""
        hwnd = self._hwnd
        if not hwnd or not self._apis or not self.thread.is_alive():
            return
        try:
            self._apis[0].PostMessageW(hwnd, WM_CLOSE, 0, 0)
            self.thread.join(timeout=1.0)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass


def start_watcher(state: SystemPresenceState) -> _Watcher:
    watcher = _Watcher(state)
    watcher.thread.start()
    return watcher
