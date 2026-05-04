"""Tests for ``LLMSessionManager.passthrough_to_chat_bubble`` and the
``main_server`` proactive_message → passthrough wiring.

Background — see PR-1110 (squashed ``c49d6fe89``) and PR-4 brief:

The plugin v2 schema (``plugin/sdk/shared/core/push_message_schema.py``)
defines ``visibility=["chat"]`` + ``ai_behavior="blind"`` to mean
"render verbatim into the chat bubble, but never feed to the LLM."
PR-4 implements that path — distinct from PR-1110's mirror channel,
which DOES enter chat history as an ``AIMessage``.

Two distinguishing assertions matter:

* mirror writes to ``sync_message_queue`` (cross_server picks it up
  and may inject into chat history).
* passthrough does NOT — frontend sees the bubble, LLM never does.

We construct the manager via ``__new__`` (skipping the heavy real
``__init__`` that needs a config_manager) and stub only the attributes
``passthrough_to_chat_bubble`` reads: ``websocket``, ``lanlan_name``,
``sync_message_queue``.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager  # noqa: E402


class _ClientState:
    """Stand-in for FastAPI's ``WebSocketState`` enum.

    The production code in ``passthrough_to_chat_bubble`` does:
        ws.client_state == ws.client_state.CONNECTED
    so the actual *value* of ``client_state`` must expose an attribute
    ``CONNECTED`` that compares equal to itself when ``client_state`` is
    in the connected state, and not equal when disconnected.
    """

    def __init__(self, name: str):
        self._name = name

    # Class-level ``CONNECTED`` doesn't work for the production check
    # because the check reads it off the *instance*, not the class.
    @property
    def CONNECTED(self):
        return _ClientState._connected_singleton

    def __eq__(self, other):
        return isinstance(other, _ClientState) and other._name == self._name

    def __hash__(self):
        return hash(self._name)


_ClientState._connected_singleton = _ClientState("CONNECTED")
_DISCONNECTED_STATE = _ClientState("DISCONNECTED")


class _FakeWebsocket:
    """Minimal websocket stub that mimics FastAPI's WebSocket.client_state."""

    def __init__(self, connected: bool = True):
        self.client_state = _ClientState._connected_singleton if connected else _DISCONNECTED_STATE
        self.send_json = AsyncMock()


def _make_mgr(websocket=None, sync_queue=None) -> LLMSessionManager:
    """Build a minimal LLMSessionManager that exposes only the attributes
    ``passthrough_to_chat_bubble`` reads."""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.websocket = websocket
    # passthrough_to_chat_bubble must NOT touch sync_message_queue;
    # we wire one up so we can later assert it stays untouched.
    mgr.sync_message_queue = sync_queue if sync_queue is not None else MagicMock()
    return mgr


# ──────────────────────────────────────────────────────────────────────
# Unit: passthrough_to_chat_bubble
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_passthrough_writes_to_websocket_with_passthrough_metadata():
    """Connected websocket + non-empty text → send_json invoked once with
    type=gemini_response, metadata.passthrough=True, source preserved."""
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    await mgr.passthrough_to_chat_bubble(
        "hello world",
        request_id="req-1",
        turn_id="turn-1",
        source="plugin",
    )

    assert ws.send_json.await_count == 1
    payload = ws.send_json.await_args.args[0]
    assert payload["type"] == "gemini_response"
    assert payload["text"] == "hello world"
    assert payload["isNewMessage"] is True
    assert payload["turn_id"] == "turn-1"
    assert payload["request_id"] == "req-1"
    assert payload["metadata"] == {"source": "plugin", "passthrough": True}


@pytest.mark.unit
async def test_passthrough_skips_sync_message_queue():
    """KEY contract: passthrough does NOT enqueue onto sync_message_queue.

    This is what distinguishes passthrough from
    ``mirror_assistant_output`` — the latter calls
    ``send_lanlan_response`` which writes to ``sync_message_queue``,
    causing cross_server to add an ``AIMessage`` to chat history.
    Passthrough must keep the LLM blind.
    """
    ws = _FakeWebsocket(connected=True)
    sync_queue = MagicMock()
    mgr = _make_mgr(websocket=ws, sync_queue=sync_queue)

    await mgr.passthrough_to_chat_bubble("hello", request_id="r", source="plugin")

    sync_queue.put.assert_not_called()
    sync_queue.put_nowait.assert_not_called()


@pytest.mark.unit
async def test_passthrough_handles_empty_text_no_op():
    """Empty / whitespace-only text → no websocket call, no exception."""
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    await mgr.passthrough_to_chat_bubble("", request_id="r")
    await mgr.passthrough_to_chat_bubble("   \n\t  ", request_id="r")
    await mgr.passthrough_to_chat_bubble(None, request_id="r")  # type: ignore[arg-type]

    ws.send_json.assert_not_called()


@pytest.mark.unit
async def test_passthrough_handles_disconnected_websocket_gracefully():
    """Disconnected websocket → send_json NOT called, no exception raised."""
    ws = _FakeWebsocket(connected=False)
    mgr = _make_mgr(websocket=ws)

    # Should not raise; should not call send_json (gate guards it).
    await mgr.passthrough_to_chat_bubble("hello", request_id="r")
    ws.send_json.assert_not_called()


@pytest.mark.unit
async def test_passthrough_handles_missing_websocket():
    """websocket=None → silently no-op, no AttributeError."""
    mgr = _make_mgr(websocket=None)
    # Should not raise.
    await mgr.passthrough_to_chat_bubble("hello", request_id="r")


@pytest.mark.unit
async def test_passthrough_send_failure_is_logged_not_raised():
    """If send_json raises (transient WS error), passthrough logs + swallows."""
    ws = _FakeWebsocket(connected=True)
    ws.send_json = AsyncMock(side_effect=RuntimeError("ws boom"))
    mgr = _make_mgr(websocket=ws)

    # Should not propagate the RuntimeError.
    await mgr.passthrough_to_chat_bubble("hello", request_id="r")
    # send_json was attempted exactly once.
    assert ws.send_json.await_count == 1


@pytest.mark.unit
async def test_passthrough_synthesizes_turn_id_when_missing():
    """When neither turn_id nor request_id is provided, the method must
    synthesize a turn_id so the frontend can group chunks into one bubble.
    """
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    await mgr.passthrough_to_chat_bubble("hi", source="plugin")

    payload = ws.send_json.await_args.args[0]
    assert isinstance(payload["turn_id"], str)
    assert len(payload["turn_id"]) > 0


# ──────────────────────────────────────────────────────────────────────
# Integration-ish: main_server proactive_message → passthrough wiring
# ──────────────────────────────────────────────────────────────────────
#
# Verifies that the visibility=["chat"] + ai_behavior="blind" branch in
# ``_handle_agent_event`` actually invokes ``passthrough_to_chat_bubble``
# on the resolved manager. We don't run main_server.py wholesale —
# we extract the function under test and call it with a stubbed event +
# a stubbed manager.


@pytest.mark.unit
async def test_main_server_proactive_chat_blind_invokes_passthrough(monkeypatch):
    """main_server's _handle_agent_event with visibility=["chat"] +
    ai_behavior="blind" must call mgr.passthrough_to_chat_bubble exactly
    once with the event's text, source_kind, and task_id."""
    # Late import: main_server is heavy; only import when needed.
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None  # disable HUD send for cleanliness
    fake_mgr._pending_agent_callback_task = None

    # Force the manager resolution helpers in main_server to find our fake.
    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    # Also bypass ``_is_websocket_connected`` so HUD path is skipped.
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "verbatim line",
        "summary": "verbatim line",
        "detail": "verbatim line",
        "channel": "plugin:foo",
        "task_id": "task-42",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    call = fake_mgr.passthrough_to_chat_bubble.await_args
    # text positional arg
    assert call.args[0] == "verbatim line"
    # request_id from task_id, source from source_kind
    assert call.kwargs.get("request_id") == "task-42"
    assert call.kwargs.get("source") == "plugin"
    # silent + blind → LLM channel NOT engaged
    fake_mgr.enqueue_agent_callback.assert_not_called()


@pytest.mark.unit
async def test_main_server_proactive_chat_respond_does_not_invoke_passthrough(monkeypatch):
    """When ai_behavior != "blind", the passthrough branch must NOT fire
    even if visibility includes "chat" — non-blind ai_behavior already
    enqueues the LLM callback, and the AI's own response is what fills
    the chat bubble.
    """
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "tell the user something",
        "channel": "plugin:foo",
        "task_id": "task-43",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    # respond → LLM callback enqueued
    fake_mgr.enqueue_agent_callback.assert_called_once()


@pytest.mark.unit
async def test_blind_with_proactive_delivery_mode_does_not_enqueue_callback(monkeypatch):
    """Defensive contract: ``ai_behavior="blind"`` MUST never reach the
    LLM channel, even if the upstream emitter sets ``delivery_mode`` to
    "proactive" or "passive". The plugin ``proactive_bridge`` already
    maps blind→silent, but that's an indirect translation contract — a
    future direct emitter (or another bridge) could violate it. The host
    side must enforce the invariant locally.

    This test deliberately constructs a malformed-from-the-bridge event
    (blind + proactive) that today the bridge wouldn't produce, to lock
    in the host-side defense.
    """
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "blind text",
        "channel": "plugin:foo",
        "task_id": "task-blind-proactive",
        # Bridge contract says blind→silent; we deliberately violate it
        # here to exercise the defensive host-side check.
        "delivery_mode": "proactive",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    # Host-side defense must downgrade delivery_mode to silent and skip
    # the LLM enqueue path even though the event arrived as "proactive".
    fake_mgr.enqueue_agent_callback.assert_not_called()
    fake_mgr.trigger_agent_callbacks.assert_not_called()
    # Chat passthrough still fires (visibility includes "chat", behavior is blind).
    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()


@pytest.mark.unit
async def test_blind_with_passive_delivery_mode_does_not_enqueue_callback(monkeypatch):
    """Symmetric to the proactive case: blind + passive must also be
    forced to silent on the host side."""
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "blind passive text",
        "channel": "plugin:foo",
        "task_id": "task-blind-passive",
        "delivery_mode": "passive",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.enqueue_agent_callback.assert_not_called()
    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()


@pytest.mark.unit
async def test_passthrough_uses_resolved_source_kind_from_channel(monkeypatch):
    """When the event omits ``source_kind`` but the channel implies one
    (e.g. ``computer_use`` → ``cu``), the passthrough call must use the
    locally-resolved ``source_kind`` rather than the raw event field
    with a "plugin" default — otherwise non-plugin sources get
    mislabeled as ``plugin`` in the chat bubble metadata.
    """
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "computer-use blind line",
        # No source_kind on event — must be derived from channel.
        "channel": "computer_use",
        "task_id": "task-cu-1",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    call = fake_mgr.passthrough_to_chat_bubble.await_args
    # Channel "computer_use" must resolve to source_kind="cu", NOT "plugin".
    assert call.kwargs.get("source") == "cu"


@pytest.mark.unit
async def test_main_server_proactive_hud_only_blind_does_not_invoke_passthrough(monkeypatch):
    """visibility=["hud"] + ai_behavior="blind" → HUD-only toast path,
    passthrough must NOT fire (no "chat" in visibility)."""
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "hud notice",
        "channel": "plugin:foo",
        "task_id": "task-44",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["hud"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    fake_mgr.enqueue_agent_callback.assert_not_called()
