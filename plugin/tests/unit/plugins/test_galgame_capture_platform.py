"""Tests for capture_platform.py — platform detection and dispatch.

These tests are intentionally platform-aware: they verify the dispatch
contract on whichever platform pytest runs, then use @pytest.mark.skipif
to gate platform-specific paths.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plugin.plugins.galgame_plugin import capture_platform as cp
from plugin.plugins.galgame_plugin.ocr_reader import DetectedGameWindow


# ─── Platform detection ──────────────────────────────────────────────────


def test_is_windows_returns_bool() -> None:
    assert isinstance(cp.is_windows(), bool)


def test_is_macos_returns_bool() -> None:
    assert isinstance(cp.is_macos(), bool)


def test_is_linux_returns_bool() -> None:
    assert isinstance(cp.is_linux(), bool)


def test_platform_mutually_exclusive_when_known() -> None:
    """Exactly one of is_windows/is_macos/is_linux is True on supported
    hosts. On exotic platforms (BSD, etc.) all three may be False; in that
    case the sum is 0 and platform_name() must say 'unknown'."""
    flags = [cp.is_windows(), cp.is_macos(), cp.is_linux()]
    flag_sum = sum(flags)
    if flag_sum == 0:
        assert cp.platform_name() == "unknown"
    else:
        assert flag_sum == 1


def test_platform_name_in_expected_set() -> None:
    assert cp.platform_name() in ("windows", "macos", "linux", "unknown")


def test_platform_supports_dxcam_iff_windows() -> None:
    assert cp.platform_supports_dxcam() == cp.is_windows()


def test_platform_supports_printwindow_iff_windows() -> None:
    assert cp.platform_supports_printwindow() == cp.is_windows()


# ─── Backend kind classification ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("dxcam", True),
        ("printwindow", True),
        ("mss", False),
        ("pyautogui", False),
        ("electron", False),
        ("", False),
        ("unknown", False),
    ],
)
def test_is_win32_only_backend_kind(kind: str, expected: bool) -> None:
    assert cp.is_win32_only_backend_kind(kind) is expected


# ─── scan_windows() dispatch ─────────────────────────────────────────────


def test_scan_windows_returns_list() -> None:
    result = cp.scan_windows()
    assert isinstance(result, list)


def test_scan_windows_elements_are_detected_windows() -> None:
    """Every element returned by scan_windows must be a DetectedGameWindow.

    The platform-specific scanners run in their actual environment here;
    on unsupported hosts they return [], which still satisfies the type
    invariant.
    """
    result = cp.scan_windows()
    for window in result:
        assert isinstance(window, DetectedGameWindow)


def test_scan_windows_unknown_platform_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exotic platform (none of windows / macos / linux) must return []
    without raising."""
    monkeypatch.setattr(cp.sys, "platform", "haiku")
    assert cp.is_windows() is False
    assert cp.is_macos() is False
    assert cp.is_linux() is False
    assert cp.platform_name() == "unknown"
    assert cp.scan_windows() == []


# ─── Platform-specific scanners (gated) ──────────────────────────────────


@pytest.mark.skipif(sys.platform != "linux", reason="linux only")
def test_linux_window_scanner_returns_list() -> None:
    from plugin.plugins.galgame_plugin.window_scanner_linux import (
        _scan_windows_linux,
    )

    result = _scan_windows_linux()
    assert isinstance(result, list)
    for window in result:
        assert isinstance(window, DetectedGameWindow)


@pytest.mark.skipif(sys.platform != "darwin", reason="macos only")
def test_macos_window_scanner_returns_list() -> None:
    from plugin.plugins.galgame_plugin.window_scanner_macos import (
        _scan_windows_macos,
    )

    result = _scan_windows_macos()
    assert isinstance(result, list)
    for window in result:
        assert isinstance(window, DetectedGameWindow)


def test_macos_window_scanner_returns_empty_when_quartz_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin.window_scanner_macos import (
        _scan_windows_macos,
    )

    fake_quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        CGWindowListCopyWindowInfo=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)

    assert _scan_windows_macos() == []


def test_macos_target_window_rect_prefers_window_number_over_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    windows = [
        {
            "kCGWindowNumber": 11,
            "kCGWindowOwnerPID": 123,
            "kCGWindowName": "Launcher",
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 300, "Height": 200},
        },
        {
            "kCGWindowNumber": 22,
            "kCGWindowOwnerPID": 123,
            "kCGWindowName": "Game",
            "kCGWindowBounds": {"X": 100, "Y": 200, "Width": 800, "Height": 600},
        },
    ]
    fake_quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGNullWindowID=0,
        kCGWindowNumber="kCGWindowNumber",
        kCGWindowOwnerPID="kCGWindowOwnerPID",
        kCGWindowName="kCGWindowName",
        kCGWindowBounds="kCGWindowBounds",
        CGWindowListCopyWindowInfo=lambda *_args: windows,
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    target = DetectedGameWindow(hwnd=22, title="Game", pid=123, width=800, height=600)

    assert ocr_reader._target_window_rect_macos(target) == (100, 200, 900, 800)


def test_macos_target_window_rect_fails_when_quartz_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    fake_quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGNullWindowID=0,
        CGWindowListCopyWindowInfo=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    target = DetectedGameWindow(hwnd=22, title="Game", pid=123, width=800, height=600)

    with pytest.raises(RuntimeError, match="macos_target_window_rect_unavailable"):
        ocr_reader._target_window_rect_macos(target)


def test_macos_target_window_rect_falls_back_to_pid_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    windows = [
        {
            "kCGWindowNumber": 11,
            "kCGWindowOwnerPID": 123,
            "kCGWindowName": "Settings",
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 300, "Height": 200},
        },
        {
            "kCGWindowNumber": 33,
            "kCGWindowOwnerPID": 123,
            "kCGWindowName": "  GAME  ",
            "kCGWindowBounds": {"X": 40, "Y": 50, "Width": 640, "Height": 480},
        },
    ]
    fake_quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGNullWindowID=0,
        kCGWindowNumber="kCGWindowNumber",
        kCGWindowOwnerPID="kCGWindowOwnerPID",
        kCGWindowName="kCGWindowName",
        kCGWindowBounds="kCGWindowBounds",
        CGWindowListCopyWindowInfo=lambda *_args: windows,
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    target = DetectedGameWindow(hwnd=0, title="game", pid=123, width=640, height=480)

    assert ocr_reader._target_window_rect_macos(target) == (40, 50, 680, 530)


def test_macos_target_window_rect_fails_when_target_is_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    windows = [
        {
            "kCGWindowNumber": 11,
            "kCGWindowOwnerPID": 321,
            "kCGWindowName": "Other",
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 300, "Height": 200},
        },
    ]
    fake_quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGNullWindowID=0,
        kCGWindowNumber="kCGWindowNumber",
        kCGWindowOwnerPID="kCGWindowOwnerPID",
        kCGWindowName="kCGWindowName",
        kCGWindowBounds="kCGWindowBounds",
        CGWindowListCopyWindowInfo=lambda *_args: windows,
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    target = DetectedGameWindow(hwnd=22, title="Game", pid=123, width=800, height=600)

    with pytest.raises(RuntimeError, match="macos_target_window_rect_unavailable"):
        ocr_reader._target_window_rect_macos(target)


def test_linux_target_window_rect_closes_xlib_display_on_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    class _FakeRoot:
        def get_full_property(self, *_args):
            return SimpleNamespace(value=[222])

    root = _FakeRoot()

    class _FakeWindow:
        def get_geometry(self):
            return SimpleNamespace(x=12, y=34, width=640, height=480)

        def query_tree(self):
            return SimpleNamespace(parent=root)

    created_displays: list["_FakeDisplay"] = []

    class _FakeDisplay:
        def __init__(self) -> None:
            self.closed = False
            created_displays.append(self)

        def screen(self):
            return SimpleNamespace(root=root)

        def intern_atom(self, name: str) -> str:
            return name

        def create_resource_object(self, _kind: str, _wid: int):
            return _FakeWindow()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "Xlib",
        SimpleNamespace(display=SimpleNamespace(Display=_FakeDisplay)),
    )
    target = DetectedGameWindow(hwnd=222, width=640, height=480)

    assert ocr_reader._target_window_rect_linux(target) == (12, 34, 652, 514)
    assert created_displays
    assert created_displays[0].closed is True


def test_linux_target_window_rect_fails_when_geometry_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import ocr_reader

    monkeypatch.setitem(sys.modules, "Xlib", None)
    monkeypatch.setattr(ocr_reader.shutil, "which", lambda _name: None)
    target = DetectedGameWindow(hwnd=222, width=640, height=480)

    with pytest.raises(RuntimeError, match="linux_target_window_rect_unavailable"):
        ocr_reader._target_window_rect_linux(target)


def test_linux_xlib_scanner_marks_hidden_windows_minimized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import window_scanner_linux

    atoms = {
        "_NET_CLIENT_LIST": 1,
        "_NET_WM_NAME": 2,
        "_NET_WM_VISIBLE_NAME": 3,
        "_NET_WM_PID": 4,
        "_NET_WM_STATE": 5,
        "_NET_WM_STATE_HIDDEN": 6,
        "WM_STATE": 7,
    }

    class _Prop:
        def __init__(self, value):
            self.value = value

    class _FakeRoot:
        def get_full_property(self, atom, _type):
            if atom == atoms["_NET_CLIENT_LIST"]:
                return _Prop([111, 222])
            return None

    root = _FakeRoot()

    class _FakeWindow:
        def __init__(self, wid: int) -> None:
            self.wid = wid

        def get_geometry(self):
            return SimpleNamespace(width=640, height=480)

        def get_full_property(self, atom, _type):
            if atom in {atoms["_NET_WM_VISIBLE_NAME"], atoms["_NET_WM_NAME"]}:
                return _Prop(f"Game {self.wid}")
            if atom == atoms["_NET_WM_PID"]:
                return _Prop([1234])
            if atom == atoms["_NET_WM_STATE"] and self.wid == 222:
                return _Prop([atoms["_NET_WM_STATE_HIDDEN"]])
            if atom == atoms["WM_STATE"]:
                return _Prop([1])
            return None

    class _FakeDisplay:
        def screen(self):
            return SimpleNamespace(root=root)

        def intern_atom(self, name: str) -> int:
            return atoms[name]

        def create_resource_object(self, _kind: str, wid: int):
            return _FakeWindow(int(wid))

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "Xlib",
        SimpleNamespace(
            X=SimpleNamespace(AnyPropertyType=0),
            display=SimpleNamespace(Display=_FakeDisplay),
        ),
    )

    result = window_scanner_linux._scan_windows_linux_xlib()
    by_hwnd = {window.hwnd: window for window in result}

    assert by_hwnd[111].is_minimized is False
    assert by_hwnd[222].is_minimized is True


# ─── ElectronCaptureBackend smoke tests ──────────────────────────────────


def test_electron_backend_is_available_when_endpoint_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no Electron HTTP endpoint is reachable, is_available must
    return False rather than raising."""
    import httpx

    from plugin.plugins.galgame_plugin.electron_capture import (
        ElectronCaptureBackend,
    )

    def _raise_connect_error(*args, **kwargs):
        del args, kwargs
        raise httpx.ConnectError("endpoint down")

    monkeypatch.setattr(httpx, "request", _raise_connect_error)
    backend = ElectronCaptureBackend(base_url="http://127.0.0.1:1")
    assert backend.is_available() is False


def test_electron_backend_resolves_default_url_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import electron_capture

    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "65432")
    url = electron_capture._resolve_default_base_url()
    assert url == "http://127.0.0.1:65432"
    # API URL convention: no trailing slash
    assert not url.endswith("/")


def test_electron_backend_kind_is_electron() -> None:
    from plugin.plugins.galgame_plugin.electron_capture import (
        ElectronCaptureBackend,
    )

    backend = ElectronCaptureBackend(base_url="http://127.0.0.1:1")
    assert backend.kind == "electron"


def test_electron_backend_rejects_invalid_target_id() -> None:
    from plugin.plugins.galgame_plugin.electron_capture import (
        ElectronCaptureBackend,
    )

    backend = ElectronCaptureBackend(base_url="http://127.0.0.1:1")
    target = DetectedGameWindow(
        hwnd=0,
        title="Game",
        process_name="game.exe",
        pid=123,
        class_name="",
        exe_path="",
        width=640,
        height=480,
        area=640 * 480,
    )
    with pytest.raises(RuntimeError, match="invalid_target_id"):
        backend.capture_frame(target, object())


def test_electron_backend_redacts_long_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin.electron_capture import (
        ElectronCaptureBackend,
    )

    def _fake_request(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status_code=500, text="secret-token-" + ("x" * 300))

    monkeypatch.setattr("httpx.request", _fake_request)
    backend = ElectronCaptureBackend(base_url="http://127.0.0.1:1")
    target = DetectedGameWindow(
        hwnd=42,
        title="Game",
        process_name="game.exe",
        pid=123,
        class_name="",
        exe_path="",
        width=640,
        height=480,
        area=640 * 480,
    )

    with pytest.raises(RuntimeError) as excinfo:
        backend.capture_frame(target, object())

    message = str(excinfo.value)
    assert "http_status_500" in message
    assert "secret-token" not in message
    assert len(message) < 180


def test_electron_backend_applies_capture_profile_crop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import io

    from PIL import Image

    from plugin.plugins.galgame_plugin.electron_capture import (
        ElectronCaptureBackend,
    )
    from plugin.plugins.galgame_plugin.ocr_reader import OcrCaptureProfile

    source = Image.new("RGB", (100, 80), "white")
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    def _fake_request(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(status_code=200, json=lambda: {"image": encoded})

    monkeypatch.setattr("httpx.request", _fake_request)
    backend = ElectronCaptureBackend(base_url="http://127.0.0.1:1")
    target = DetectedGameWindow(
        hwnd=42,
        title="Game",
        process_name="game.exe",
        pid=123,
        width=100,
        height=80,
        area=100 * 80,
    )
    profile = OcrCaptureProfile(
        left_inset_ratio=0.10,
        right_inset_ratio=0.20,
        top_ratio=0.25,
        bottom_inset_ratio=0.25,
    )

    cropped = backend.capture_frame(target, profile)

    assert cropped.size == (70, 40)
    assert cropped.info["galgame_capture_backend_kind"] == "electron"
    assert cropped.info["galgame_capture_rect"] == {
        "left": 10.0,
        "top": 20.0,
        "right": 80.0,
        "bottom": 60.0,
    }


def test_linux_wmctrl_scanner_uses_resolved_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import window_scanner_linux

    captured: dict[str, object] = {}

    monkeypatch.setattr(window_scanner_linux.shutil, "which", lambda name: "/usr/bin/wmctrl")
    monkeypatch.setattr(
        window_scanner_linux,
        "_linux_process_name_from_pid",
        lambda pid: "Game.exe" if pid == 4321 else "",
    )

    def _fake_check_output(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("cmd")
        captured["cmd"] = cmd
        captured["text"] = kwargs.get("text")
        captured["timeout"] = kwargs.get("timeout")
        return "0x01200007  0 4321 10 20 640 480 host Game Window\n"

    monkeypatch.setattr(window_scanner_linux.subprocess, "check_output", _fake_check_output)

    result = window_scanner_linux._scan_windows_linux_wmctrl()

    assert captured["cmd"] == ["/usr/bin/wmctrl", "-lpG"]
    assert len(result) == 1
    assert result[0].hwnd == int("0x01200007", 16)
    assert result[0].pid == 4321
    assert result[0].process_name == "Game.exe"


# ─── Win32CaptureBackend filtering on non-Windows ────────────────────────


def _make_backend(kind: str):
    class _Stub:
        def __init__(self, k: str) -> None:
            self.kind = k

        def is_available(self) -> bool:  # pragma: no cover - not used here
            return True

    return _Stub(kind)


class _StubElectronCaptureBackend:
    kind = "electron"

    def __init__(self, **_kwargs) -> None:
        pass

    def is_available(self) -> bool:  # pragma: no cover - not used here
        return True


@pytest.mark.parametrize("is_linux_host", [False, True])
def test_build_backends_filters_win32_only_on_non_windows(is_linux_host: bool) -> None:
    """On macOS/Linux, _build_backends() must drop dxcam/printwindow."""
    from plugin.plugins.galgame_plugin import ocr_reader as ocr_reader_mod
    from plugin.plugins.galgame_plugin.ocr_reader import Win32CaptureBackend

    with patch.object(ocr_reader_mod, "_is_windows_platform", return_value=False), patch(
        "plugin.plugins.galgame_plugin.capture_platform.is_windows",
        return_value=False,
    ), patch(
        "plugin.plugins.galgame_plugin.capture_platform.is_linux",
        return_value=is_linux_host,
    ):
        backend = Win32CaptureBackend(selection="auto")
        chain = backend._build_backends()

    kinds = {str(getattr(b, "kind", "")) for b in chain}
    assert "dxcam" not in kinds
    assert "printwindow" not in kinds


def test_build_backends_keeps_win32_only_on_windows() -> None:
    """On Windows, the default three-backend win32 chain remains reachable."""
    from plugin.plugins.galgame_plugin.ocr_reader import Win32CaptureBackend

    backend = Win32CaptureBackend(selection="auto")
    with patch(
        "plugin.plugins.galgame_plugin.capture_platform.is_windows",
        return_value=True,
    ):
        chain = backend._build_backends()
    kinds = {str(getattr(b, "kind", "")) for b in chain}
    # default chain includes dxcam, mss, pyautogui
    assert "dxcam" in kinds
    assert "mss" in kinds
    assert "pyautogui" in kinds


@pytest.mark.parametrize("selection", ["auto", "smart"])
def test_build_backends_prefers_electron_on_linux_wayland_auto_modes(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    """Wayland import probes are not enough; prefer portal-backed capture."""
    from plugin.plugins.galgame_plugin import electron_capture
    from plugin.plugins.galgame_plugin.ocr_reader import Win32CaptureBackend

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(
        electron_capture,
        "ElectronCaptureBackend",
        _StubElectronCaptureBackend,
    )

    backend = Win32CaptureBackend(selection=selection)

    assert [str(getattr(b, "kind", "")) for b in backend._backends] == ["electron"]


def test_build_backends_keeps_electron_tail_fallback_on_linux_x11(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin import electron_capture
    from plugin.plugins.galgame_plugin.ocr_reader import Win32CaptureBackend

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        electron_capture,
        "ElectronCaptureBackend",
        _StubElectronCaptureBackend,
    )

    backend = Win32CaptureBackend(selection="auto")

    assert [str(getattr(b, "kind", "")) for b in backend._backends] == [
        "mss",
        "pyautogui",
        "electron",
    ]
