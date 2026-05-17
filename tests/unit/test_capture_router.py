# -*- coding: utf-8 -*-
"""Unit tests for ``main_routers/capture_router.py``.

Covers contracts agreed in
``md/当前方案/cross-platform-capture-phase5-bridge-plan.md`` §7:

* /api/capture/health 503 with no renderer, 200 with renderer, 503 after
  renderer disconnects (registry cleanup).
* /api/capture/screenshot rejects non-loopback requests.
* Invalid / oversized target_id returns 422.
* Renderer success path returns ``{image}``.
* Renderer timeout returns 502 / 504 and clears pending futures.
* Oversized image payload is rejected without logging the bytes.
* Unknown ``request_id`` produces no side-effect.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import capture_bridge, capture_router as capture_router_module


CAPTURE_HEALTH = "/api/capture/health"
CAPTURE_SHOT = "/api/capture/screenshot"
APP_WEBSOCKET_JS = Path(__file__).resolve().parents[2] / "static" / "app-websocket.js"


@pytest.fixture(autouse=True)
def _reset_capture_bridge():
    capture_bridge._reset_for_tests()
    yield
    capture_bridge._reset_for_tests()


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch):
    """FastAPI TestClient uses ``testclient`` as the client host, which the
    real ``_is_loopback_request`` rejects. Force it to allow for tests that
    don't explicitly assert non-loopback rejection."""
    monkeypatch.setattr(capture_router_module, "_is_loopback_request", lambda _r: True)
    yield


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(capture_router_module.router)
    return TestClient(app)


def test_capture_bridge_renderer_ignores_placeholder_target_id_before_source_match():
    source = APP_WEBSOCKET_JS.read_text(encoding="utf-8")
    placeholder_guard = ("targetId === '0'", "targetId === '<target_id>'")
    guard_index = source.index(placeholder_guard[0])
    placeholder_index = source.index(placeholder_guard[1], guard_index)
    reset_index = source.index("targetId = '';", placeholder_index)
    match_index = source.index("if (targetId) {", reset_index)

    assert guard_index < placeholder_index < reset_index < match_index


class _DummyWebSocket:
    """Minimal WebSocket double for capture_bridge.send_text()."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def _register_dummy_renderer(lanlan_name: str = "neko") -> _DummyWebSocket:
    ws = _DummyWebSocket()
    capture_bridge.mark_capture_client(
        lanlan_name,
        ws,
        {
            "available": True,
            "capabilities": {
                "getSources": True,
                "captureSourceAsDataUrl": True,
                "captureSourceWithoutNeko": True,
            },
        },
    )
    return ws


@pytest.mark.unit
def test_health_503_without_renderer():
    with _build_client() as client:
        resp = client.get(CAPTURE_HEALTH)
    assert resp.status_code == 503
    body = resp.json()
    assert body["success"] is False
    assert body["available"] is False


@pytest.mark.unit
def test_health_200_when_renderer_registered():
    _register_dummy_renderer()
    with _build_client() as client:
        resp = client.get(CAPTURE_HEALTH)
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "available": True}


@pytest.mark.unit
def test_health_returns_503_after_renderer_disconnect():
    _register_dummy_renderer("neko")
    capture_bridge.unmark_capture_client("neko")
    with _build_client() as client:
        resp = client.get(CAPTURE_HEALTH)
    assert resp.status_code == 503


@pytest.mark.unit
def test_screenshot_rejects_non_loopback(monkeypatch):
    monkeypatch.setattr(capture_router_module, "_is_loopback_request", lambda _r: False)
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "x", "pid": 1, "title": "t"})
    assert resp.status_code == 403


@pytest.mark.unit
def test_screenshot_validates_pid_negative():
    _register_dummy_renderer()
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "abc", "pid": -1, "title": "t"})
    assert resp.status_code == 422


@pytest.mark.unit
def test_screenshot_validates_target_id_too_long():
    _register_dummy_renderer()
    too_long = "a" * (capture_bridge.MAX_TARGET_ID_LEN + 1)
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": too_long, "pid": 1, "title": "t"})
    assert resp.status_code == 422


@pytest.mark.unit
def test_screenshot_503_without_renderer():
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "123", "pid": 1, "title": "t"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "no_renderer"


@pytest.mark.unit
def test_screenshot_success(monkeypatch):
    _register_dummy_renderer()
    image_data_url = "data:image/jpeg;base64,AAAA"

    async def _fake_request(payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
        # Router must normalise target_id to str before handing off.
        assert isinstance(payload["target_id"], str)
        return {"success": True, "image": image_data_url, "width": 1920, "height": 1080, "source_id": "window:9:0"}

    monkeypatch.setattr(capture_router_module.capture_bridge, "request_capture_screenshot", _fake_request)

    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": 12345, "pid": 100, "title": "Game"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["image"] == image_data_url
    assert body["width"] == 1920
    assert body["height"] == 1080
    assert body["source_id"] == "window:9:0"


@pytest.mark.unit
def test_screenshot_timeout_returns_504(monkeypatch):
    _register_dummy_renderer()

    async def _timeout(payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
        raise capture_bridge.CaptureBridgeError("renderer response timeout")

    monkeypatch.setattr(capture_router_module.capture_bridge, "request_capture_screenshot", _timeout)
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "1", "pid": 1, "title": "t"})
    assert resp.status_code == 504
    assert resp.json()["error"] == "renderer_timeout"


@pytest.mark.unit
def test_screenshot_source_not_found_returns_502(monkeypatch):
    _register_dummy_renderer()

    async def _missing(payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
        raise capture_bridge.CaptureBridgeError("source_not_found")

    monkeypatch.setattr(capture_router_module.capture_bridge, "request_capture_screenshot", _missing)
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "1", "pid": 1, "title": "t"})
    assert resp.status_code == 502
    assert resp.json()["error"] == "source_not_found"


@pytest.mark.unit
def test_screenshot_oversized_image_rejected(monkeypatch):
    _register_dummy_renderer()

    async def _too_big(payload: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
        raise capture_bridge.CaptureBridgeError("image exceeds size limit")

    monkeypatch.setattr(capture_router_module.capture_bridge, "request_capture_screenshot", _too_big)
    with _build_client() as client:
        resp = client.post(CAPTURE_SHOT, json={"target_id": "1", "pid": 1, "title": "t"})
    assert resp.status_code == 502
    assert resp.json()["error"] == "bridge_error"


@pytest.mark.unit
def test_screenshot_unknown_request_id_no_effect():
    _register_dummy_renderer("neko")
    # Pre-condition: registry has no pending requests; resolve must be a no-op.
    capture_bridge.resolve_capture_response("neko", {"request_id": "does-not-exist", "success": True})
    snap = capture_bridge._snapshot_for_tests()
    assert snap["pending_counts"].get("neko", 0) == 0
