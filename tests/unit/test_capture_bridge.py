# -*- coding: utf-8 -*-
"""Unit tests for ``utils/capture_bridge.py``.

Covers the contracts from
``md/当前方案/cross-platform-capture-phase5-bridge-plan.md`` §7:

* mark_capture_client(available=True) registers, available=False unregisters
* unmark_capture_client cleans pending futures
* concurrent request serialisation via Semaphore(1)
* pending request timeout cleans up state (no leaked Futures)
* duplicate mark updates capability timestamp
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from utils import capture_bridge


@pytest.fixture(autouse=True)
def _reset():
    capture_bridge._reset_for_tests()
    yield
    capture_bridge._reset_for_tests()


class _Sock:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.send_event = asyncio.Event()

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)
        self.send_event.set()


def _payload(available: bool = True) -> dict[str, Any]:
    return {
        "available": available,
        "capabilities": {
            "getSources": True,
            "captureSourceAsDataUrl": True,
            "captureSourceWithoutNeko": True,
        },
    }


@pytest.mark.unit
def test_mark_available_true_then_has_client():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    assert capture_bridge.has_capture_client() is True


@pytest.mark.unit
def test_mark_available_false_unregisters():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    capture_bridge.mark_capture_client("neko", sock, _payload(False))
    assert capture_bridge.has_capture_client() is False


@pytest.mark.unit
def test_unmark_clears_registry():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    capture_bridge.unmark_capture_client("neko")
    assert capture_bridge.has_capture_client() is False


@pytest.mark.unit
def test_unmark_with_stale_websocket_does_not_clear_new_registration():
    old_sock = _Sock()
    new_sock = _Sock()
    capture_bridge.mark_capture_client("neko", old_sock, _payload(True))
    capture_bridge.mark_capture_client("neko", new_sock, _payload(True))

    capture_bridge.unmark_capture_client("neko", expected_websocket=old_sock)

    assert capture_bridge.has_capture_client() is True
    assert capture_bridge._clients["neko"].websocket is new_sock


@pytest.mark.unit
def test_mark_unavailable_from_stale_websocket_does_not_clear_new_registration():
    old_sock = _Sock()
    new_sock = _Sock()
    capture_bridge.mark_capture_client("neko", old_sock, _payload(True))
    capture_bridge.mark_capture_client("neko", new_sock, _payload(True))

    capture_bridge.mark_capture_client("neko", old_sock, _payload(False))

    assert capture_bridge.has_capture_client() is True
    assert capture_bridge._clients["neko"].websocket is new_sock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_same_websocket_under_new_name_drops_old_registration():
    sock = _Sock()
    capture_bridge.mark_capture_client("old-neko", sock, _payload(True))

    task = asyncio.create_task(
        capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"},
            timeout=5.0,
        )
    )
    await sock.send_event.wait()

    capture_bridge.mark_capture_client("new-neko", sock, _payload(True))

    with pytest.raises(capture_bridge.CaptureBridgeError) as exc_info:
        await task
    assert "was replaced by new renderer" in str(exc_info.value)
    assert list(capture_bridge._clients) == ["new-neko"]
    assert "old-neko" not in capture_bridge._pending_by_client
    assert capture_bridge._clients["new-neko"].websocket is sock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unmark_resolves_pending_futures_with_error():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))

    async def _slow_request():
        return await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"},
            timeout=5.0,
        )

    task = asyncio.create_task(_slow_request())
    # Wait until the bridge sends the request payload (i.e. future is pending).
    await sock.send_event.wait()
    capture_bridge.unmark_capture_client("neko")
    with pytest.raises(capture_bridge.CaptureBridgeError) as exc_info:
        await task
    assert "disconnected" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_semaphore_serialises_concurrent_requests():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))

    started = []

    async def _one(req_id: str):
        started.append(req_id)
        return await capture_bridge.request_capture_screenshot(
            {"target_id": req_id, "pid": 100, "title": "t"},
            timeout=0.2,
        )

    tasks = [asyncio.create_task(_one("a")), asyncio.create_task(_one("b"))]
    started_at = time.monotonic()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - started_at
    # Both expected to time out (no renderer replies), but they must not run
    # concurrently — sock.sent should only contain 1 payload at any given time.
    # The total number of sends equals the number of tasks that managed to
    # get past the semaphore before being cancelled by timeout.
    assert len(sock.sent) == 2
    assert elapsed >= 0.35
    assert all(isinstance(r, capture_bridge.CaptureBridgeError) for r in results)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_cleans_up_pending_future():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    with pytest.raises(capture_bridge.CaptureBridgeError):
        await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"},
            timeout=0.05,
        )
    snap = capture_bridge._snapshot_for_tests()
    assert snap["pending_counts"].get("neko", 0) == 0


@pytest.mark.unit
def test_duplicate_mark_refreshes_timestamp():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    snap1 = capture_bridge._snapshot_for_tests()
    previous_registered_at = capture_bridge._clients["neko"].registered_at
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    snap2 = capture_bridge._snapshot_for_tests()
    current_registered_at = capture_bridge._clients["neko"].registered_at
    assert snap1["clients"] == ["neko"]
    assert snap2["clients"] == ["neko"]
    assert current_registered_at > previous_registered_at
    # Internal registered_at must have advanced. Re-fetch via private field
    # since snapshot doesn't expose timestamp.
    client = capture_bridge._clients["neko"]
    assert client.websocket is sock


@pytest.mark.unit
def test_target_id_int_is_accepted_and_stringified():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    # _validate_target_id is exercised via direct call (router does normalise
    # before passing in, but the bridge itself must also accept int as a
    # belt-and-braces guard).
    assert capture_bridge._validate_target_id(123) == "123"
    assert capture_bridge._validate_target_id("abc") == "abc"
    with pytest.raises(capture_bridge.CaptureBridgeError):
        capture_bridge._validate_target_id("")
    with pytest.raises(capture_bridge.CaptureBridgeError):
        capture_bridge._validate_target_id("x" * (capture_bridge.MAX_TARGET_ID_LEN + 1))
    with pytest.raises(capture_bridge.CaptureBridgeError):
        capture_bridge._validate_target_id(None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_with_unknown_request_id_is_noop():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    capture_bridge.resolve_capture_response("neko", {"request_id": "missing", "success": True})
    snap = capture_bridge._snapshot_for_tests()
    assert snap["pending_counts"].get("neko", 0) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversized_image_rejected_without_logging_bytes():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    big = "data:image/jpeg;base64," + "A" * (capture_bridge.MAX_IMAGE_BASE64_BYTES + 1)

    async def _replier():
        await sock.send_event.wait()
        # The bridge sent the request; reply with an oversized image.
        sock.send_event.clear()
        msg = capture_bridge._clients["neko"].websocket.sent[-1]
        import json
        request_id = json.loads(msg)["request_id"]
        capture_bridge.resolve_capture_response(
            "neko",
            {"request_id": request_id, "success": True, "image": big},
        )

    reply_task = asyncio.create_task(_replier())
    with pytest.raises(capture_bridge.CaptureBridgeError) as exc_info:
        await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"},
            timeout=1.0,
        )
    assert "size limit" in str(exc_info.value)
    await reply_task
