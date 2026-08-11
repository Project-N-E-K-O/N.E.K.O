from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.galgame_plugin.ocr_capture_backends import _helpers
from plugin.plugins.galgame_plugin.ocr_capture_backends import dxcam as dxcam_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import mss as mss_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import pyautogui as pyautogui_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import win32 as win32_backend
from plugin.plugins.galgame_plugin.ocr_reader import (
    DetectedGameWindow,
    OcrCaptureProfile,
    Win32CaptureBackend,
)


pytestmark = pytest.mark.plugin_unit

_PRE_CAPTURE_MARKER = "target_not_foreground_for_screen_capture"
_POST_CAPTURE_MARKER = "foreground_changed_during_screen_capture"


def _target(*, hwnd: int = 101, pid: int = 77, foreground: bool = True):
    return DetectedGameWindow(
        hwnd=hwnd,
        pid=pid,
        title="Demo",
        process_name="DemoGame.exe",
        width=20,
        height=20,
        is_foreground=foreground,
    )


def test_win32_occlusion_scan_preserves_pointer_sized_hwnds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large_hwnd = 0x1234567887654321
    visible_candidates: list[int] = []

    class _Win32Function:
        def __init__(self, callback) -> None:
            self._callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self._callback(*args)

    def _get_window(hwnd: int, _command: int) -> int:
        return large_hwnd if int(hwnd) == 101 else 0

    def _get_pid(hwnd: int, pid_pointer) -> int:
        pid_pointer._obj.value = 77 if int(hwnd) == 101 else 88
        return 1

    def _is_visible(hwnd: int) -> int:
        visible_candidates.append(int(hwnd))
        return 1

    def _get_rect(_hwnd: int, rect_pointer) -> int:
        rect_pointer._obj.left = 0
        rect_pointer._obj.top = 0
        rect_pointer._obj.right = 20
        rect_pointer._obj.bottom = 20
        return 1

    user32 = SimpleNamespace(
        IsWindow=_Win32Function(lambda _hwnd: 1),
        GetWindowThreadProcessId=_Win32Function(_get_pid),
        GetWindow=_Win32Function(_get_window),
        IsWindowVisible=_Win32Function(_is_visible),
        IsIconic=_Win32Function(lambda _hwnd: 0),
        GetWindowRect=_Win32Function(_get_rect),
    )
    monkeypatch.setattr(
        win32_backend.ctypes,
        "windll",
        SimpleNamespace(user32=user32),
        raising=False,
    )
    monkeypatch.setattr(
        win32_backend,
        "_capture_region_rect",
        lambda _target_value, _profile: (0, 0, 20, 20),
    )

    assert win32_backend._win32_capture_region_occluded(
        _target(),
        OcrCaptureProfile(),
    ) is True
    assert user32.GetWindow.restype is win32_backend.ctypes.wintypes.HWND
    assert user32.GetWindow.argtypes == [
        win32_backend.ctypes.wintypes.HWND,
        win32_backend.ctypes.wintypes.UINT,
    ]
    assert visible_candidates == [large_hwnd]


def _install_foreground_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    foreground_hwnd: int,
    roots: dict[int, int],
) -> None:
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=2))
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            GetForegroundWindow=lambda: foreground_hwnd,
            GetAncestor=lambda hwnd, _kind: roots.get(int(hwnd), 0),
        ),
    )


def test_screen_capture_foreground_guard_accepts_identical_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_foreground_api(monkeypatch, foreground_hwnd=101, roots={})

    _helpers._require_foreground_screen_capture_target_win32(
        _target(hwnd=101),
        backend_kind="dxcam",
        failure_marker=_PRE_CAPTURE_MARKER,
    )


def test_screen_capture_foreground_guard_accepts_shared_root_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_foreground_api(
        monkeypatch,
        foreground_hwnd=102,
        roots={101: 100, 102: 100},
    )

    _helpers._require_foreground_screen_capture_target_win32(
        _target(hwnd=101),
        backend_kind="mss",
        failure_marker=_PRE_CAPTURE_MARKER,
    )


def test_screen_capture_foreground_guard_rejects_same_pid_different_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_foreground_api(
        monkeypatch,
        foreground_hwnd=202,
        roots={101: 100, 202: 200},
    )

    with pytest.raises(RuntimeError, match=_PRE_CAPTURE_MARKER):
        _helpers._require_foreground_screen_capture_target_win32(
            _target(hwnd=101, pid=77),
            backend_kind="pyautogui",
            failure_marker=_PRE_CAPTURE_MARKER,
        )


def test_screen_capture_foreground_guard_fails_closed_for_zero_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_foreground_api(monkeypatch, foreground_hwnd=0, roots={101: 100})

    with pytest.raises(RuntimeError, match=_PRE_CAPTURE_MARKER):
        _helpers._require_foreground_screen_capture_target_win32(
            _target(),
            backend_kind="dxcam",
            failure_marker=_PRE_CAPTURE_MARKER,
        )


def _configured_pixel_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend_name: str,
    guard,
):
    from PIL import Image

    api_calls: list[object] = []
    module = {
        "dxcam": dxcam_backend,
        "mss": mss_backend,
        "pyautogui": pyautogui_backend,
    }[backend_name]
    monkeypatch.setattr(module, "_require_visible_capture_target", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_target_screen_capture_rect", lambda _target: (0, 0, 20, 20))
    monkeypatch.setattr(module, "_require_foreground_screen_capture_target", guard)

    if backend_name == "dxcam":
        import numpy as np

        class _Camera:
            def grab(self, *, region):
                api_calls.append(region)
                return np.zeros((20, 20, 3), dtype=np.uint8)

        backend = dxcam_backend.DxcamCaptureBackend()
        backend._camera = _Camera()
        return backend, api_calls

    if backend_name == "mss":
        class _Sct:
            def grab(self, monitor):
                api_calls.append(dict(monitor))
                return SimpleNamespace(size=(20, 20), rgb=b"\x00" * (20 * 20 * 3))

        backend = mss_backend.MssCaptureBackend()
        backend._sct = _Sct()
        return backend, api_calls

    def _screenshot(*, region):
        api_calls.append(region)
        return Image.new("RGB", (region[2], region[3]), "black")

    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(size=lambda: (1920, 1080), screenshot=_screenshot),
    )
    return pyautogui_backend.PyAutoGuiCaptureBackend(), api_calls


@pytest.mark.parametrize("backend_name", ["dxcam", "mss", "pyautogui"])
@pytest.mark.parametrize(
    ("failed_check", "expected_marker", "expected_api_calls"),
    [
        (1, _PRE_CAPTURE_MARKER, 0),
        (2, _POST_CAPTURE_MARKER, 1),
    ],
)
def test_pixel_backends_discard_untrusted_capture_before_or_after_api(
    monkeypatch: pytest.MonkeyPatch,
    backend_name: str,
    failed_check: int,
    expected_marker: str,
    expected_api_calls: int,
) -> None:
    checked_markers: list[str] = []

    def _guard(_target, *, backend_kind: str, failure_marker: str) -> None:
        checked_markers.append(failure_marker)
        if len(checked_markers) == failed_check:
            raise RuntimeError(f"{backend_kind}: {failure_marker}")

    backend, api_calls = _configured_pixel_backend(monkeypatch, backend_name, _guard)

    with pytest.raises(RuntimeError, match=expected_marker):
        backend.capture_frame(_target(), OcrCaptureProfile())

    assert len(api_calls) == expected_api_calls
    assert checked_markers == (
        [_PRE_CAPTURE_MARKER]
        if failed_check == 1
        else [_PRE_CAPTURE_MARKER, _POST_CAPTURE_MARKER]
    )


@pytest.mark.parametrize("marker", [_PRE_CAPTURE_MARKER, _POST_CAPTURE_MARKER])
def test_foreground_capture_error_terminates_pixel_backend_fallback(marker: str) -> None:
    class _RejectedBackend:
        kind = "dxcam"

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            raise RuntimeError(f"dxcam: {marker}")

    class _FallbackBackend:
        kind = "mss"

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return "unsafe-frame"

    fallback = _FallbackBackend()
    backend = Win32CaptureBackend(selection="dxcam")
    backend._backends = [_RejectedBackend(), fallback]

    with pytest.raises(RuntimeError, match=marker):
        backend.capture_frame(_target(), OcrCaptureProfile())

    assert fallback.calls == 0


@pytest.mark.parametrize("marker", [_PRE_CAPTURE_MARKER, _POST_CAPTURE_MARKER])
def test_smart_foreground_capture_error_reroutes_to_printwindow(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    class _RejectedBackend:
        kind = "dxcam"

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            raise RuntimeError(f"dxcam: {marker}")

    class _PrintWindowBackend:
        kind = "printwindow"

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return "printwindow-frame"

    class _SkippedPixelBackend:
        def __init__(self, kind: str) -> None:
            self.kind = kind
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return "unsafe-frame"

    monkeypatch.setattr(sys, "platform", "win32")
    rejected = _RejectedBackend()
    skipped_mss = _SkippedPixelBackend("mss")
    skipped_pyautogui = _SkippedPixelBackend("pyautogui")
    printwindow = _PrintWindowBackend()
    backend = Win32CaptureBackend(
        selection="smart",
        occlusion_checker=lambda _target, _profile: False,
    )
    backend._dxcam_backend = rejected
    backend._mss_backend = skipped_mss
    backend._pyautogui_backend = skipped_pyautogui
    backend._printwindow_backend = printwindow
    backend._backends = [rejected, skipped_mss, skipped_pyautogui, printwindow]

    frame = backend.capture_frame(_target(foreground=True), OcrCaptureProfile())

    assert frame == "printwindow-frame"
    assert rejected.calls == 1
    assert skipped_mss.calls == 0
    assert skipped_pyautogui.calls == 0
    assert printwindow.calls == 1
    assert backend.last_backend_kind == "printwindow"
    assert backend.last_capture_content_trusted is True


@pytest.mark.parametrize("selection", ["auto", "dxcam", "mss", "pyautogui"])
def test_explicit_pixel_backend_rejects_occluded_capture_region(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    class _PixelBackend:
        kind = selection

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return "unsafe-frame"

    monkeypatch.setattr(sys, "platform", "win32")
    pixel_backend = _PixelBackend()
    backend = Win32CaptureBackend(
        selection=selection,
        occlusion_checker=lambda _target, _profile: True,
    )
    backend._backends = [pixel_backend]

    with pytest.raises(RuntimeError, match="capture_region_occluded_by_other_window"):
        backend.capture_frame(_target(foreground=True), OcrCaptureProfile())

    assert pixel_backend.calls == 0
    assert backend.last_capture_region_occluded is True
    assert backend.last_capture_content_trusted is False
    assert (
        backend.last_capture_untrusted_reason
        == "capture_region_occluded_by_other_window"
    )


@pytest.mark.parametrize("selection", ["auto", "dxcam", "mss", "pyautogui"])
def test_pixel_backend_rejects_capture_region_that_becomes_occluded(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    class _PixelBackend:
        kind = "dxcam" if selection == "auto" else selection

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return "unsafe-frame"

    occlusion_checks: list[bool] = []

    def _occlusion_checker(_target, _profile) -> bool:
        occlusion_checks.append(True)
        return len(occlusion_checks) == 2

    monkeypatch.setattr(sys, "platform", "win32")
    pixel_backend = _PixelBackend()
    backend = Win32CaptureBackend(
        selection=selection,
        occlusion_checker=_occlusion_checker,
    )
    backend._backends = [pixel_backend]

    with pytest.raises(RuntimeError, match="capture_region_occluded_by_other_window"):
        backend.capture_frame(_target(foreground=True), OcrCaptureProfile())

    assert pixel_backend.calls == 1
    assert len(occlusion_checks) == 2
    assert backend.last_capture_region_occluded is True
    assert backend.last_capture_content_trusted is False
    assert (
        backend.last_capture_untrusted_reason
        == "capture_region_occluded_by_other_window"
    )


def test_smart_post_capture_occlusion_reroutes_to_printwindow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str, frame: str) -> None:
            self.kind = kind
            self.frame = frame
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return self.frame

    occlusion_checks: list[bool] = []

    def _occlusion_checker(_target, _profile) -> bool:
        occlusion_checks.append(True)
        return len(occlusion_checks) == 2

    monkeypatch.setattr(sys, "platform", "win32")
    pixel_backend = _Backend("dxcam", "unsafe-frame")
    printwindow = _Backend("printwindow", "printwindow-frame")
    backend = Win32CaptureBackend(
        selection="smart",
        occlusion_checker=_occlusion_checker,
    )
    backend._dxcam_backend = pixel_backend
    backend._printwindow_backend = printwindow
    backend._backends = [pixel_backend, printwindow]

    frame = backend.capture_frame(_target(foreground=True), OcrCaptureProfile())

    assert frame == "printwindow-frame"
    assert pixel_backend.calls == 1
    assert printwindow.calls == 1
    assert len(occlusion_checks) == 2
    assert backend.last_backend_kind == "printwindow"
    assert backend.last_capture_region_occluded is True
    assert backend.last_capture_content_trusted is True


def test_printwindow_occluded_foreground_does_not_fallback_to_pixel_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str, *, available: bool = True) -> None:
            self.kind = kind
            self.available = available
            self.calls = 0

        def is_available(self) -> bool:
            return self.available

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return f"{self.kind}-frame"

    monkeypatch.setattr(sys, "platform", "win32")
    printwindow = _Backend("printwindow", available=False)
    pixel_backends = [_Backend("dxcam"), _Backend("mss"), _Backend("pyautogui")]
    backend = Win32CaptureBackend(
        selection="printwindow",
        occlusion_checker=lambda _target, _profile: True,
    )
    backend._printwindow_backend = printwindow
    backend._backends = [printwindow, *pixel_backends]

    with pytest.raises(RuntimeError, match="printwindow_unavailable"):
        backend.capture_frame(_target(foreground=True), OcrCaptureProfile())

    assert printwindow.calls == 0
    assert all(item.calls == 0 for item in pixel_backends)


def test_smart_background_uses_only_printwindow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str) -> None:
            self.kind = kind
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, _target, _profile):
            self.calls += 1
            return f"{self.kind}-frame"

    monkeypatch.setattr(sys, "platform", "win32")
    backend = Win32CaptureBackend(selection="smart")
    printwindow = _Backend("printwindow")
    pixel_backends = [_Backend("dxcam"), _Backend("mss"), _Backend("pyautogui")]
    backend._printwindow_backend = printwindow
    backend._backends = [*pixel_backends, printwindow]

    frame = backend.capture_frame(_target(foreground=False), OcrCaptureProfile())

    assert frame == "printwindow-frame"
    assert printwindow.calls == 1
    assert all(item.calls == 0 for item in pixel_backends)


def test_plugin_default_capture_backend_is_smart() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    config = tomllib.loads(
        (repo_root / "plugin/plugins/galgame_plugin/plugin.toml").read_text(
            encoding="utf-8"
        )
    )

    assert config["ocr_reader"]["capture_backend"] == "smart"
