"""Windows watcher (``main_logic/activity/system_presence_windows.py``) with fake Win32 APIs.

The real module is imported; only the DLL entry points are replaced (and, off
Windows, ``ctypes.WINFUNCTYPE`` with ``CFUNCTYPE``), so these tests cover the
registration order, initial lock query decoding, message decoding, failure
handling and cleanup. They do not prove the Win32 calls themselves are right;
that still needs a real Windows machine.
"""

from __future__ import annotations

import ctypes
import importlib
import sys

import pytest

from main_logic.activity import system_presence as sp

MODULE = "main_logic.activity.system_presence_windows"


class FakeFn:
    def __init__(self, dll, name):
        self.dll, self.name = dll, name
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.dll.calls.append(self.name)
        impl = self.dll.impl.get(self.name)
        return impl(*args) if impl else self.dll.defaults.get(self.name, 1)


class FakeDLL:
    def __init__(self, calls, impl, defaults):
        self.calls, self.impl, self.defaults = calls, impl, defaults

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        fn = FakeFn(self, name)
        object.__setattr__(self, name, fn)
        return fn


@pytest.fixture
def win(monkeypatch):
    """Import the real module against fake DLLs; yields (module, calls, impl)."""
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    calls: list[str] = []
    impl: dict = {}
    defaults = {"GetMessageW": 0, "GetModuleHandleW": 1, "CreateWindowExW": 0x1234,
                "RegisterPowerSettingNotification": 0x5678, "WTSQuerySessionInformationW": 0,
                # Distinct from TRUE so tests can tell a handled message from a pass-through.
                "DefWindowProcW": 0x77}
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: FakeDLL(calls, impl, defaults), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    package = sys.modules["main_logic.activity"]
    previous = getattr(package, "system_presence_windows", None)
    module = importlib.import_module(MODULE)
    monkeypatch.setattr(module.atexit, "register", lambda fn: None)
    yield module, calls, impl
    # importlib also bound the fake-DLL module on the package; drop it so it never
    # leaks into system_presence's lazy ``from main_logic.activity import ...``.
    sys.modules.pop(MODULE, None)
    if previous is None:
        if getattr(package, "system_presence_windows", None) is module:
            delattr(package, "system_presence_windows")
    else:
        package.system_presence_windows = previous


def _unlocked_session_info(module, flags):
    info = module.WTSINFOEXW()
    info.Level = 1
    info.Data.Level1.SessionFlags = flags

    def query(_server, _session, _klass, buf_ref, size_ref):
        buf_ref._obj.value = ctypes.addressof(info)
        size_ref._obj.value = ctypes.sizeof(info)
        return 1

    return info, query


def test_registration_order_and_cleanup(win):
    module, calls, impl = win
    info, impl["WTSQuerySessionInformationW"] = _unlocked_session_info(module, module.WTS_SESSIONSTATE_UNLOCK)
    state = sp.SystemPresenceState()
    module._Watcher(state)._run()

    order = [c for c in calls if c in (
        "RegisterClassExW", "CreateWindowExW", "WTSRegisterSessionNotification",
        "WTSQuerySessionInformationW", "RegisterPowerSettingNotification", "GetMessageW",
        "UnregisterPowerSettingNotification", "WTSUnRegisterSessionNotification", "UnregisterClassW",
    )]
    assert order == [
        "RegisterClassExW", "CreateWindowExW",
        # Lock state is read after session notifications are live, before the display registration.
        "WTSRegisterSessionNotification", "WTSQuerySessionInformationW", "RegisterPowerSettingNotification",
        "GetMessageW",
        "UnregisterPowerSettingNotification", "WTSUnRegisterSessionNotification", "UnregisterClassW",
    ]
    assert state._seen_unlocked is True
    # The message loop ended, so nothing keeps the state current: allow from now on.
    assert state._failed is True


@pytest.mark.parametrize("flags, expected", [(0, True), (1, False), (-1, None)])
def test_initial_lock_query_decoding(win, flags, expected):
    module, _calls, impl = win
    info, query = _unlocked_session_info(module, flags)
    impl["WTSQuerySessionInformationW"] = query
    wtsapi32 = ctypes.WinDLL("wtsapi32")
    assert module.query_session_locked(wtsapi32) is expected


def test_lock_query_failure_is_unknown(win):
    module, _calls, _impl = win
    assert module.query_session_locked(ctypes.WinDLL("wtsapi32")) is None


@pytest.mark.parametrize("failing", [
    "RegisterClassExW", "CreateWindowExW", "WTSRegisterSessionNotification", "RegisterPowerSettingNotification",
])
def test_registration_failure_fails_open_and_logs_once(win, failing, caplog):
    module, calls, impl = win
    impl[failing] = lambda *a: 0
    state = sp.SystemPresenceState()
    state.on_session_lock_state(False)
    state.on_display_state(True)
    state.on_display_state(False)
    with caplog.at_level("WARNING", logger=module.__name__):
        module._Watcher(state)._run()
    assert state.inactive_reason() is None
    assert "GetMessageW" not in calls
    assert len([r for r in caplog.records if "watcher stopped" in r.getMessage()]) == 1


def test_message_loop_error_fails_open(win):
    module, _calls, impl = win
    impl["GetMessageW"] = lambda *a: -1
    state = sp.SystemPresenceState()
    module._Watcher(state)._run()
    assert state._failed is True


def test_ctypes_error_in_thread_fails_open(win):
    module, _calls, impl = win

    def boom(*a):
        raise OSError("access violation")

    impl["CreateWindowExW"] = boom
    state = sp.SystemPresenceState()
    module._Watcher(state)._run()  # must not raise
    assert state._failed is True


def _display_setting(module, value):
    size = ctypes.sizeof(module.POWERBROADCAST_SETTING) + 8
    buf = (ctypes.c_ubyte * size)()
    setting = module.POWERBROADCAST_SETTING.from_buffer(buf)
    setting.PowerSetting = module.GUID_SESSION_DISPLAY_STATUS
    setting.DataLength = 4
    module.wintypes.DWORD.from_buffer(buf, module.POWERBROADCAST_SETTING.Data.offset).value = value
    return buf


def test_window_proc_decodes_notifications(win):
    module, _calls, _impl = win
    state = sp.SystemPresenceState()
    watcher = module._Watcher(state)
    watcher._apis = module._bind_apis()
    watcher._query_lock_state = lambda: False

    state.on_session_lock_state(False)
    on, off = _display_setting(module, 1), _display_setting(module, 0)
    assert watcher._window_proc(None, module.WM_POWERBROADCAST, sp.PBT_POWERSETTINGCHANGE, ctypes.addressof(on)) == 1
    assert watcher._window_proc(None, 0x0400, 0, 0) == 0x77  # unrelated message: DefWindowProcW
    assert state.inactive_reason() is None
    watcher._window_proc(None, module.WM_POWERBROADCAST, sp.PBT_POWERSETTINGCHANGE, ctypes.addressof(off))
    assert state.inactive_reason() == sp.REASON_DISPLAY_OFF
    watcher._window_proc(None, module.WM_POWERBROADCAST, sp.PBT_POWERSETTINGCHANGE, ctypes.addressof(on))

    watcher._window_proc(None, module.WM_WTSSESSION_CHANGE, sp.WTS_SESSION_LOCK, 0)
    assert state.inactive_reason() == sp.REASON_SESSION_LOCKED
    watcher._window_proc(None, module.WM_POWERBROADCAST, sp.PBT_APMSUSPEND, 0)
    assert state.inactive_reason() == sp.REASON_SUSPENDED
    watcher._window_proc(None, module.WM_WTSSESSION_CHANGE, sp.WTS_REMOTE_DISCONNECT, 0)
    assert state.inactive_reason() is None


def test_window_proc_ignores_other_power_settings(win):
    module, _calls, _impl = win
    state = sp.SystemPresenceState()
    state.on_session_lock_state(False)
    state.on_display_state(True)
    watcher = module._Watcher(state)
    watcher._apis = module._bind_apis()
    other = _display_setting(module, 0)
    module.POWERBROADCAST_SETTING.from_buffer(other).PowerSetting.Data1 = 0x12345678
    watcher._window_proc(None, module.WM_POWERBROADCAST, sp.PBT_POWERSETTINGCHANGE, ctypes.addressof(other))
    assert state.inactive_reason() is None


def test_window_proc_exception_fails_open(win):
    module, _calls, _impl = win
    state = sp.SystemPresenceState()
    watcher = module._Watcher(state)
    watcher._apis = module._bind_apis()

    def boom():
        raise OSError("WTSQuerySessionInformationW crashed")

    watcher._query_lock_state = boom
    # Must not raise into the OS callback; falls through to DefWindowProcW.
    watcher._window_proc(None, module.WM_WTSSESSION_CHANGE, sp.WTS_CONSOLE_CONNECT, 0)
    assert state._failed is True
