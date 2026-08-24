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
            "captureDesktopRegionAsDataUrl": True,
        },
    }


@pytest.mark.unit
def test_region_capability_is_required_separately_from_window_capture():
    sock = _Sock()
    payload = _payload(True)
    payload["capabilities"]["captureDesktopRegionAsDataUrl"] = False
    capture_bridge.mark_capture_client("neko", sock, payload)

    assert capture_bridge.has_capture_client() is True
    assert capture_bridge.has_region_capture_client() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_only_renderer_does_not_accept_window_capture_requests():
    sock = _Sock()
    payload = _payload(True)
    payload["capabilities"]["getSources"] = False
    payload["capabilities"]["captureSourceAsDataUrl"] = False
    capture_bridge.mark_capture_client("neko", sock, payload)

    assert capture_bridge.has_capture_client() is False
    assert capture_bridge.has_region_capture_client() is True
    with pytest.raises(capture_bridge.CaptureBridgeError, match="no renderer available"):
        await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"}, timeout=0.1
        )
    assert sock.sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_only_newest_renderer_does_not_shadow_window_capture_client():
    window_sock = _Sock()
    region_sock = _Sock()
    capture_bridge.mark_capture_client("window", window_sock, _payload(True))
    region_payload = _payload(True)
    region_payload["capabilities"]["getSources"] = False
    region_payload["capabilities"]["captureSourceAsDataUrl"] = False
    capture_bridge.mark_capture_client("region", region_sock, region_payload)

    async def _replier():
        await window_sock.send_event.wait()
        import json

        request = json.loads(window_sock.sent[-1])
        capture_bridge.resolve_capture_response(
            "window",
            {
                "request_id": request["request_id"],
                "success": True,
                "image": "data:image/jpeg;base64,YQ==",
            },
        )

    reply_task = asyncio.create_task(_replier())
    result = await capture_bridge.request_capture_screenshot(
        {"target_id": "1", "pid": 100, "title": "t"}, timeout=1.0
    )
    await reply_task

    assert result["success"] is True
    assert region_sock.sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_window_only_newest_renderer_does_not_shadow_region_capture_client():
    region_sock = _Sock()
    window_sock = _Sock()
    capture_bridge.mark_capture_client("region", region_sock, _payload(True))
    window_payload = _payload(True)
    window_payload["capabilities"]["captureDesktopRegionAsDataUrl"] = False
    capture_bridge.mark_capture_client("window", window_sock, window_payload)

    assert capture_bridge.has_region_capture_client() is True

    async def _replier():
        await region_sock.send_event.wait()
        import json

        request = json.loads(region_sock.sent[-1])
        capture_bridge.resolve_capture_response(
            "region",
            {"request_id": request["request_id"], "success": False, "canceled": True},
        )

    reply_task = asyncio.create_task(_replier())
    result = await capture_bridge.request_capture_region({}, timeout=1.0)
    await reply_task

    assert result["canceled"] is True
    assert window_sock.sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_request_uses_distinct_message_and_preserves_cancel():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))

    async def _replier():
        await sock.send_event.wait()
        import json
        request = json.loads(sock.sent[-1])
        assert request["type"] == "capture_bridge_region_request"
        assert request["selection_only"] is True
        assert request["copy_to_clipboard"] is False
        assert request["session_timeout_ms"] == 45000
        capture_bridge.resolve_capture_response(
            "neko",
            {"request_id": request["request_id"], "success": False, "canceled": True},
        )

    reply_task = asyncio.create_task(_replier())
    result = await capture_bridge.request_capture_region(
        {
            "selection_only": True,
            "copy_to_clipboard": False,
            "session_timeout_ms": 45000,
        },
        timeout=1.0,
    )
    await reply_task

    assert result["success"] is False
    assert result["canceled"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_cancel_response_discards_untrusted_extra_fields():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))

    async def _replier():
        await sock.send_event.wait()
        import json

        request = json.loads(sock.sent[-1])
        capture_bridge.resolve_capture_response(
            "neko",
            {
                "request_id": request["request_id"],
                "success": False,
                "canceled": True,
                "image": "untrusted-large-payload",
                "unexpected": "value",
            },
        )

    reply_task = asyncio.create_task(_replier())
    result = await capture_bridge.request_capture_region({}, timeout=1.0)
    await reply_task

    assert result == {"success": False, "canceled": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_request_targets_the_explicit_lanlan_renderer():
    requesting_sock = _Sock()
    newest_sock = _Sock()
    capture_bridge.mark_capture_client("requesting", requesting_sock, _payload(True))
    newest_payload = _payload(True)
    newest_payload["capabilities"]["captureDesktopRegionAsDataUrl"] = False
    capture_bridge.mark_capture_client("newest", newest_sock, newest_payload)

    assert capture_bridge.has_region_capture_client() is True
    assert capture_bridge.has_region_capture_client("requesting") is True

    async def _replier():
        await requesting_sock.send_event.wait()
        import json

        request = json.loads(requesting_sock.sent[-1])
        capture_bridge.resolve_capture_response(
            "requesting",
            {"request_id": request["request_id"], "success": False, "canceled": True},
        )

    reply_task = asyncio.create_task(_replier())
    result = await capture_bridge.request_capture_region(
        {"lanlan_name": "requesting"}, timeout=1.0
    )
    await reply_task

    assert result["canceled"] is True
    assert newest_sock.sent == []


@pytest.mark.unit
def test_region_client_lookup_normalizes_explicit_lanlan_name():
    capture_bridge.mark_capture_client("requesting", _Sock(), _payload(True))

    assert capture_bridge.has_region_capture_client("  requesting  ") is True
    assert capture_bridge._pick_client("  requesting  ") is not None
    assert capture_bridge.has_region_capture_client("   ") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_region_request_does_not_fallback_for_missing_target_lanlan():
    capture_bridge.mark_capture_client("other", _Sock(), _payload(True))

    with pytest.raises(capture_bridge.CaptureBridgeError, match="no renderer available"):
        await capture_bridge.request_capture_region(
            {"lanlan_name": "missing"}, timeout=0.1
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_region_and_window_capture_fail_fast_while_region_active():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    region_task = asyncio.create_task(
        capture_bridge.request_capture_region(
            {
                "selection_only": True,
                "copy_to_clipboard": False,
                "session_timeout_ms": 45000,
            },
            timeout=0.2,
        )
    )
    await sock.send_event.wait()

    with pytest.raises(capture_bridge.CaptureBridgeError, match="capture_busy"):
        await capture_bridge.request_capture_region({}, timeout=0.1)
    with pytest.raises(capture_bridge.CaptureBridgeError, match="interactive_capture_busy"):
        await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"}, timeout=0.1
        )

    with pytest.raises(capture_bridge.CaptureBridgeError, match="timeout"):
        await region_task

    assert capture_bridge._snapshot_for_tests()["interactive_capture_active"] is True
    with pytest.raises(capture_bridge.CaptureBridgeError, match="capture_busy"):
        await capture_bridge.request_capture_region({}, timeout=0.1)
    with pytest.raises(capture_bridge.CaptureBridgeError, match="interactive_capture_busy"):
        await capture_bridge.request_capture_screenshot(
            {"target_id": "1", "pid": 100, "title": "t"}, timeout=0.1
        )

    import json

    request = json.loads(sock.sent[-1])
    capture_bridge.resolve_capture_response(
        "neko",
        {"request_id": request["request_id"], "success": False, "canceled": True},
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert capture_bridge._snapshot_for_tests()["interactive_capture_active"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_region_wait_stays_busy_until_renderer_reply():
    sock = _Sock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    region_task = asyncio.create_task(
        capture_bridge.request_capture_region(
            {"session_timeout_ms": 45000},
            timeout=1.0,
        )
    )
    await sock.send_event.wait()

    region_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await region_task

    assert capture_bridge._snapshot_for_tests()["interactive_capture_active"] is True

    import json

    request = json.loads(sock.sent[-1])
    capture_bridge.resolve_capture_response(
        "neko",
        {"request_id": request["request_id"], "success": False, "canceled": True},
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert capture_bridge._snapshot_for_tests()["interactive_capture_active"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_region_send_stays_busy_until_renderer_reply():
    class _BlockingSendSock(_Sock):
        async def send_text(self, payload: str) -> None:
            self.sent.append(payload)
            self.send_event.set()
            await asyncio.Event().wait()

    sock = _BlockingSendSock()
    capture_bridge.mark_capture_client("neko", sock, _payload(True))
    region_task = asyncio.create_task(
        capture_bridge.request_capture_region(
            {"session_timeout_ms": 45000},
            timeout=1.0,
        )
    )
    await sock.send_event.wait()

    region_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await region_task

    snapshot = capture_bridge._snapshot_for_tests()
    assert snapshot["interactive_capture_active"] is True
    assert snapshot["pending_counts"]["neko"] == 1

    import json

    request = json.loads(sock.sent[-1])
    capture_bridge.resolve_capture_response(
        "neko",
        {"request_id": request["request_id"], "success": False, "canceled": True},
    )
    for _ in range(3):
        await asyncio.sleep(0)
    snapshot = capture_bridge._snapshot_for_tests()
    assert snapshot["interactive_capture_active"] is False
    assert snapshot["pending_counts"]["neko"] == 0


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
