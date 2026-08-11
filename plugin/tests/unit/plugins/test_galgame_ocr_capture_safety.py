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
