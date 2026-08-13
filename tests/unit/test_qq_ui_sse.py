"""Unit tests for the SSE communication changes (#2822).

Covers three areas:
- runs bus -> SSE bridge (plugin_ui.py): terminal run events are forwarded to
  the matching plugin's SSE queue; other plugins / non-terminal statuses are filtered
- QQAutoReplyPlugin._push_ui_event: POSTs the correct request to /ui-api/push
- QQAutoReplyPlugin._maybe_push_status_event: 2s throttle, one push per window

Follows the repo ``tests/unit`` conventions: sync tests use ``asyncio.run``,
and ``@pytest.mark.asyncio`` is used for coroutines.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---- runs bus -> SSE bridge ---------------------------------------------

def _register_sse_queue(plugin_id: str = "qq_auto_reply") -> asyncio.Queue:
    from plugin.server.routes import plugin_ui

    queue_obj: asyncio.Queue = asyncio.Queue(maxsize=8)
    plugin_ui._sse_clients = {plugin_id: [queue_obj]}
    return queue_obj


def _frame_data(frame: str) -> dict:
    # "data: {...}\n\n"
    return json.loads(frame.split(":", 1)[1].strip())


def test_runs_bridge_forwards_terminal_run_event():
    from plugin.server.routes import plugin_ui

    queue_obj = _register_sse_queue()
    plugin_ui._bridge_runs_event("change", {
        "plugin_id": "qq_auto_reply", "run_id": "r1", "status": "succeeded",
    })
    assert not queue_obj.empty()
    data = _frame_data(queue_obj.get_nowait())
    assert data["type"] == "run"
    assert data["plugin_id"] == "qq_auto_reply"
    assert data["run_id"] == "r1"
    assert data["status"] == "succeeded"


def test_runs_bridge_filters_other_plugin():
    from plugin.server.routes import plugin_ui

    queue_obj = _register_sse_queue("qq_auto_reply")
    plugin_ui._bridge_runs_event("change", {
        "plugin_id": "other_plugin", "run_id": "r9", "status": "failed",
    })
    assert queue_obj.empty()


def test_runs_bridge_ignores_non_terminal():
    from plugin.server.routes import plugin_ui

    queue_obj = _register_sse_queue()
    for status in ("queued", "running"):
        plugin_ui._bridge_runs_event("change", {
            "plugin_id": "qq_auto_reply", "run_id": "r1", "status": status,
        })
    assert queue_obj.empty()  # only terminal statuses are bridged


def test_runs_bridge_ignores_malformed_payload():
    from plugin.server.routes import plugin_ui

    queue_obj = _register_sse_queue()
    plugin_ui._bridge_runs_event("change", "not-a-dict")
    plugin_ui._bridge_runs_event("change", {"status": "succeeded"})  # missing plugin_id
    assert queue_obj.empty()


# ---- _push_ui_event -----------------------------------------------------

def _plugin_stub():
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    plugin = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    plugin.ctx = SimpleNamespace(plugin_id="qq_auto_reply")
    plugin._last_log_push_at = 0.0
    plugin._log_push_throttle_seconds = 1.5
    plugin._last_status_push_at = 0.0
    plugin._status_push_throttle_seconds = 2.0
    plugin._spawn_push_ui_event = MagicMock()
    return plugin


@pytest.mark.asyncio
async def test_push_ui_event_posts_to_push_endpoint():
    plugin = _plugin_stub()
    mock_client = SimpleNamespace(post=AsyncMock())
    with (
        patch("utils.internal_http_client.get_internal_http_client", return_value=mock_client),
        patch("config.USER_PLUGIN_BASE", "http://127.0.0.1:48916"),
    ):
        await plugin._push_ui_event("status", "status")

    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://127.0.0.1:48916/plugin/qq_auto_reply/ui-api/push"
    assert kwargs["json"] == {"type": "status", "text": "status"}


@pytest.mark.asyncio
async def test_push_ui_event_silent_on_failure():
    plugin = _plugin_stub()
    mock_client = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError("boom")))
    with (
        patch("utils.internal_http_client.get_internal_http_client", return_value=mock_client),
        patch("config.USER_PLUGIN_BASE", "http://127.0.0.1:48916"),
    ):
        await plugin._push_ui_event("status", "status")  # must not raise


# ---- throttling ---------------------------------------------------------

def test_maybe_push_status_event_throttles():
    plugin = _plugin_stub()
    # A second call inside the throttle window must not push
    plugin._maybe_push_status_event()
    plugin._maybe_push_status_event()
    assert plugin._spawn_push_ui_event.call_count == 1
    # Pushing resumes once the window expires
    plugin._last_status_push_at = 0.0
    plugin._maybe_push_status_event()
    assert plugin._spawn_push_ui_event.call_count == 2
    # And it pushes the "status" type
    assert plugin._spawn_push_ui_event.call_args.args[0] == "status"


def test_maybe_push_log_event_throttles():
    plugin = _plugin_stub()
    plugin._maybe_push_log_event()
    plugin._maybe_push_log_event()
    assert plugin._spawn_push_ui_event.call_count == 1
    assert plugin._spawn_push_ui_event.call_args.args[0] == "logs"


# ---- reply buffer: every _pending mutation pushes a status event -------------

def test_buffer_set_pending_pushes_status():
    """Inserting/updating a pending reply pushes a status event."""
    from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService

    plugin = SimpleNamespace(_maybe_push_status_event=MagicMock())
    svc = QQReplyBufferService(plugin)
    pending = SimpleNamespace(generation=0)
    svc._set_pending("g1", pending)
    assert plugin._maybe_push_status_event.call_count == 1


def test_buffer_normal_delivery_dequeue_pushes_status():
    """A normal delivery dequeue (``_detach_pending``) removes the buffer and
    pushes a status event -- otherwise a status-only frontend keeps showing the
    old buffer count until the fallback poll."""
    from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService

    plugin = SimpleNamespace(_maybe_push_status_event=MagicMock())
    svc = QQReplyBufferService(plugin)
    pending = SimpleNamespace(generation=0)
    svc._pending["g1"] = pending
    assert svc._detach_pending("g1", pending, generation=0) is True
    assert "g1" not in svc._pending
    assert plugin._maybe_push_status_event.call_count == 1
