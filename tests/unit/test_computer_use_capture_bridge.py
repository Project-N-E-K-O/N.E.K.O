"""ComputerUse prefers the renderer frame and falls back to native capture."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import threading
import time

import httpx
import pytest
from PIL import Image

from brain import computer_use


ROOT = Path(__file__).resolve().parents[2]


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response: _Response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url):
        assert url.endswith("/api/capture/computer-use")
        return self.response


@pytest.mark.unit
def test_computer_use_decodes_renderer_frame_before_native_capture(monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (32, 24), "red").save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    monkeypatch.setattr(computer_use.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        computer_use.httpx, "Client", lambda **_kwargs: _Client(_Response(200, {"image": data_url}))
    )
    monkeypatch.setattr(
        computer_use, "capture_desktop_screenshot",
        lambda: pytest.fail("native screenshot should not run after renderer success"),
    )
    image = computer_use._capture_computer_use_frame()
    assert image.size == (32, 24)
    assert image.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.unit
def test_computer_use_falls_back_when_renderer_is_unavailable(monkeypatch):
    native = Image.new("RGB", (16, 12), "blue")
    monkeypatch.setattr(computer_use.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        computer_use.httpx, "Client", lambda **_kwargs: _Client(_Response(503, {"error": "no_renderer"}))
    )
    monkeypatch.setattr(computer_use, "capture_desktop_screenshot", lambda: native)
    assert computer_use._capture_computer_use_frame() is native


@pytest.mark.unit
def test_computer_use_timeout_temporarily_skips_unresponsive_bridge(monkeypatch):
    native = Image.new("RGB", (16, 12), "blue")
    calls = []

    class _TimeoutClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url):
            calls.append("post")
            raise httpx.ReadTimeout("renderer did not answer")

    monkeypatch.setattr(computer_use.platform, "system", lambda: "Linux")
    monkeypatch.setattr(computer_use.httpx, "Client", lambda **_kwargs: _TimeoutClient())
    monkeypatch.setattr(computer_use, "capture_desktop_screenshot", lambda: native)
    monkeypatch.setattr(computer_use, "_CAPTURE_BRIDGE_BACKOFF_UNTIL", 0.0)
    assert computer_use._capture_computer_use_frame() is native
    assert computer_use._CAPTURE_BRIDGE_BACKOFF_UNTIL > computer_use.time.monotonic()
    assert computer_use._capture_computer_use_frame() is native
    assert calls == ["post"]


@pytest.mark.unit
def test_cancelling_a_stalled_bridge_does_not_wait_for_http_timeout(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    cancel = threading.Event()

    class _StalledClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url):
            entered.set()
            release.wait(timeout=5)
            return _Response(503, {"error": "no_renderer"})

        def close(self):
            closed.set()
            release.set()

    monkeypatch.setattr(computer_use.platform, "system", lambda: "Linux")
    monkeypatch.setattr(computer_use.httpx, "Client", lambda **_kwargs: _StalledClient())
    monkeypatch.setattr(
        computer_use,
        "capture_desktop_screenshot",
        lambda: pytest.fail("cancelled capture must not start the native fallback"),
    )
    monkeypatch.setattr(computer_use, "_CAPTURE_BRIDGE_BACKOFF_UNTIL", 0.0)

    def cancel_after_bridge_starts():
        assert entered.wait(timeout=2)
        cancel.set()

    canceller = threading.Thread(target=cancel_after_bridge_starts)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError, match="Task cancelled by user"):
            computer_use._capture_computer_use_frame(cancel)
        assert time.monotonic() - started < 2
        assert closed.is_set()
    finally:
        release.set()
        canceller.join(timeout=2)


@pytest.mark.unit
def test_wayland_agent_never_reopens_portal_for_each_frame():
    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    request = source.split("response.type === 'capture_bridge_computer_use_request'", 1)[1].split(
        "response.type === 'capture_bridge_region_request'", 1
    )[0]
    portal_guard = request.index("if (dc.sourceEnumerationMayPrompt === true")
    one_shot = request.index("dc, 'captureComputerUseScreen'")
    assert portal_guard < one_shot
    assert "error: 'SCREEN_STREAM_REQUIRED'" in request


@pytest.mark.unit
def test_both_agent_toggles_wait_for_capture_permission_before_enabling():
    modern = (ROOT / "static/js/agent_ui_v2.js").read_text(encoding="utf-8")
    legacy = (ROOT / "static/app/app-agent.js").read_text(encoding="utf-8")
    assert modern.index("await capturePreparation") < modern.index("await sendCommand('set_flag'")
    legacy_toggle = legacy.split("const capturePreparation = flagKey === 'computer_use_enabled'", 1)[1]
    assert legacy_toggle.index("await capturePreparation") < legacy_toggle.index("fetch('/api/agent/flags'")


@pytest.mark.unit
def test_reopening_capture_does_not_reuse_invalid_pending_permission():
    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    release = source.split("function releaseComputerUseCapture()", 1)[1].split(
        "async function captureComputerUseLiveStream(provider)", 1
    )[0]
    prepare = source.split("window.prepareComputerUseCapture = function ()", 1)[1].split(
        "window.releaseComputerUseCapture", 1
    )[0]
    assert "_computerUseStreamPending = null" in release
    assert "if (_computerUseStreamPending === pending) _computerUseStreamPending = null" in prepare


@pytest.mark.unit
def test_computer_use_stream_owner_is_brokered_across_chat_and_pet():
    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    assert "onComputerUseFrameRequest(async function ()" in source
    assert "setComputerUseStreamOwner(true, ownerToken)" in source
    assert "setComputerUseStreamOwner(false, _computerUseStreamOwnerToken)" in source
    assert "dc.computerUseSharedStreamBroker === true" in source
    assert "dc.computerUseNeedsStream === true" in source
    assert "COMPUTER_USE_STREAM_IDLE_MS" not in source


@pytest.mark.unit
def test_screen_share_chooser_timeout_can_use_native_fallback_without_hanging_toggle():
    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    modern = (ROOT / "static/js/agent_ui_v2.js").read_text(encoding="utf-8")
    legacy = (ROOT / "static/app/app-agent.js").read_text(encoding="utf-8")
    assert "requestTimedOut = true" in source
    assert "stream.getTracks().forEach(function (track) { track.stop(); });" in source
    assert "window.computerUseNativeCaptureAvailable = async function ()" in source
    for toggle in (modern, legacy):
        assert "await window.computerUseNativeCaptureAvailable()" in toggle


@pytest.mark.unit
def test_active_task_cards_reconcile_with_backend_terminal_state():
    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    reconcile = source.split("function scheduleAgentTaskReconciliation()", 1)[1].split(
        "window.computerUseNeedsCaptureStream", 1
    )[0]
    assert "fetch('/api/agent/tasks', { cache: 'no-store' })" in reconcile
    assert "['completed', 'failed', 'cancelled']" in reconcile
    assert "terminal_at: terminalAt" in reconcile
    assert "taskMap = window._agentTaskMap" in reconcile


@pytest.mark.unit
def test_screen_share_required_message_exists_in_every_locale():
    import json

    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        data = json.loads((ROOT / f"static/locales/{locale}.json").read_text(encoding="utf-8"))
        assert data["agent"]["status"]["screenShareRequired"]
