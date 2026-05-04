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

    assert (
        await mgr.passthrough_to_chat_bubble(
            "hello world",
            request_id="req-1",
            turn_id="turn-1",
            source="plugin",
        )
        is True
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

    assert await mgr.passthrough_to_chat_bubble("", request_id="r") is False
    assert (
        await mgr.passthrough_to_chat_bubble("   \n\t  ", request_id="r") is False
    )
    assert (
        await mgr.passthrough_to_chat_bubble(None, request_id="r")  # type: ignore[arg-type]
        is False
    )

    ws.send_json.assert_not_called()


@pytest.mark.unit
async def test_passthrough_handles_disconnected_websocket_gracefully():
    """Disconnected websocket → send_json NOT called, no exception raised."""
    ws = _FakeWebsocket(connected=False)
    mgr = _make_mgr(websocket=ws)

    # Should not raise; should not call send_json (gate guards it).
    assert (
        await mgr.passthrough_to_chat_bubble("hello", request_id="r") is False
    )
    ws.send_json.assert_not_called()


@pytest.mark.unit
async def test_passthrough_handles_missing_websocket():
    """websocket=None → silently no-op, no AttributeError."""
    mgr = _make_mgr(websocket=None)
    # Should not raise.
    assert (
        await mgr.passthrough_to_chat_bubble("hello", request_id="r") is False
    )


@pytest.mark.unit
async def test_passthrough_send_failure_is_logged_not_raised():
    """If send_json raises (transient WS error), passthrough logs + swallows."""
    ws = _FakeWebsocket(connected=True)
    ws.send_json = AsyncMock(side_effect=RuntimeError("ws boom"))
    mgr = _make_mgr(websocket=ws)

    # Should not propagate the RuntimeError.
    assert (
        await mgr.passthrough_to_chat_bubble("hello", request_id="r") is False
    )
    # send_json was attempted exactly once.
    assert ws.send_json.await_count == 1


@pytest.mark.unit
async def test_passthrough_preserves_verbatim_whitespace_in_payload():
    """Receiver-side verbatim contract: the WS payload's ``text`` field
    must equal the input EXACTLY — leading/trailing whitespace, embedded
    newlines, and indentation must all survive.

    Codex PR #1128 r3182348366: prior code did ``clean = text.strip()``
    and forwarded ``clean``, defeating the caller-side fix in
    ``main_server`` (commit 0ac9e8881) that took care to pass raw_text.
    Stripping must happen ONLY in the empty-check, never in the send path.
    """
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    raw = "  hello\n\n"
    await mgr.passthrough_to_chat_bubble(raw, request_id="r", source="plugin")

    assert ws.send_json.await_count == 1
    payload = ws.send_json.await_args.args[0]
    assert payload["text"] == raw, (
        f"passthrough must NOT strip text — got {payload['text']!r}, "
        f"expected {raw!r}"
    )


@pytest.mark.unit
async def test_passthrough_synthesizes_turn_id_when_missing():
    """When neither turn_id nor request_id is provided, the method must
    synthesize a turn_id so the frontend can group chunks into one bubble.
    """
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    assert await mgr.passthrough_to_chat_bubble("hi", source="plugin") is True

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
async def test_main_server_proactive_chat_blind_preserves_verbatim_whitespace(monkeypatch):
    """Verbatim contract: passthrough must receive the RAW event text with
    leading/trailing whitespace intact, even though the empty-check / log /
    callback paths in _handle_agent_event use a stripped local. CodeRabbit
    PR #1128 r3182231689 — pre-fix the call shared the stripped local and
    silently swallowed surrounding whitespace/newlines."""
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
        "text": "  hello world\n\n",
        "channel": "plugin:foo",
        "task_id": "task-verbatim",
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
    assert call.args[0] == "  hello world\n\n"


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


# ──────────────────────────────────────────────────────────────────────
# HUD visibility-gating contract (codex P2 — PR #1128)
# ──────────────────────────────────────────────────────────────────────
#
# These tests verify the v2 visibility contract for the HUD
# ``agent_notification`` send path. Why they need their own block: prior
# tests forced the websocket to disconnected so HUD never fired anyway —
# they couldn't tell whether HUD was suppressed by visibility or by the
# socket being down. Here we attach a real (mocked) websocket and assert
# on send_json calls.


def _hud_event(visibility, ai_behavior="blind", text="hud-or-chat line"):
    """Build a proactive_message event with the requested visibility/behavior."""
    return {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": text,
        "channel": "plugin:foo",
        "task_id": "task-vis",
        "delivery_mode": "silent",
        "ai_behavior": ai_behavior,
        "visibility": visibility,
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }


def _hud_fake_mgr():
    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = MagicMock()
    fake_mgr.websocket.send_json = AsyncMock()
    fake_mgr._pending_agent_callback_task = None
    return fake_mgr


def _patch_main_server(monkeypatch, fake_mgr):
    import main_server  # noqa: F401  (imported by callers)

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: True)


def _hud_send_count(fake_mgr) -> int:
    """Return count of agent_notification frames sent on the websocket."""
    return sum(
        1
        for c in fake_mgr.websocket.send_json.await_args_list
        if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "agent_notification"
    )


@pytest.mark.unit
async def test_visibility_chat_blind_chat_fires_hud_does_not(monkeypatch):
    """visibility=["chat"] + blind → chat passthrough fires, HUD does NOT.

    This is the codex P2 regression: prior code fired HUD unconditionally,
    double-rendering chat-only events as both bubble and toast.
    """
    import main_server

    fake_mgr = _hud_fake_mgr()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event(["chat"]))

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    assert _hud_send_count(fake_mgr) == 0


@pytest.mark.unit
async def test_visibility_hud_blind_hud_fires_chat_does_not(monkeypatch):
    """visibility=["hud"] + blind → HUD toast fires, chat passthrough does NOT."""
    import main_server

    fake_mgr = _hud_fake_mgr()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event(["hud"]))

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    assert _hud_send_count(fake_mgr) == 1


@pytest.mark.unit
async def test_visibility_chat_and_hud_blind_both_fire(monkeypatch):
    """visibility=["chat","hud"] + blind → BOTH chat passthrough and HUD fire."""
    import main_server

    fake_mgr = _hud_fake_mgr()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event(["chat", "hud"]))

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    assert _hud_send_count(fake_mgr) == 1


@pytest.mark.unit
async def test_visibility_empty_explicit_blind_suppresses_hud(monkeypatch):
    """visibility=[] (explicit v2 empty) + blind → neither chat nor HUD fires.

    Per the v2 schema (`plugin/sdk/shared/core/push_message_schema.py`)
    an explicit empty list means "no verbatim user-facing render".
    """
    import main_server

    fake_mgr = _hud_fake_mgr()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event([]))

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    assert _hud_send_count(fake_mgr) == 0


@pytest.mark.unit
async def test_visibility_absent_field_legacy_fires_hud(monkeypatch):
    """visibility field ABSENT (legacy emitter, no v2 plumbing) → HUD fires.

    Why: pre-v2 emitters that never learned about the visibility axis must
    keep their original "fire HUD by default" behavior. We distinguish
    "field absent" from "field == []" so v2 callers can opt out via [].
    """
    import main_server

    fake_mgr = _hud_fake_mgr()
    _patch_main_server(monkeypatch, fake_mgr)

    legacy_event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "legacy notice",
        "channel": "plugin:foo",
        "task_id": "task-legacy",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        # NB: no "visibility" key at all
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(legacy_event)

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    assert _hud_send_count(fake_mgr) == 1


# ──────────────────────────────────────────────────────────────────────
# Turn-end emission after chat-blind passthrough (codex P2 — PR #1128)
# ──────────────────────────────────────────────────────────────────────
#
# Background: ``passthrough_to_chat_bubble`` sends a ``gemini_response``
# frame, which on the frontend opens an assistant turn lifecycle via
# ``ensureAssistantTurnStarted``. Without a matching ``turn end`` /
# ``turn end agent_callback`` system event, the assistant bubble stays
# "in-progress" and proactive rescheduling never fires. The canonical
# helper that emits this turn-end is
# :py:meth:`LLMSessionManager.handle_proactive_complete`; the direct
# task_result reply path at main_server.py:714 already calls it. The
# chat-blind passthrough branch must do the same.
#
# The HUD-only branch (agent_notification) does NOT open an assistant
# turn lifecycle on the frontend, so it does NOT need turn-end. This
# means the dedup strategy is "emit once iff chat passthrough fired",
# not "per-sink emit with explicit dedup".


@pytest.mark.unit
async def test_chat_blind_passthrough_emits_turn_end_via_proactive_complete(monkeypatch):
    """visibility=["chat"] + blind → handle_proactive_complete called
    exactly once after passthrough_to_chat_bubble returns. This is the
    codex P2 regression: prior code dispatched the gemini_response bubble
    but never closed the assistant turn lifecycle on the frontend.
    """
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock()
    fake_mgr.handle_proactive_complete = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "blind chat line",
        "channel": "plugin:foo",
        "task_id": "task-blind-chat",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }

    await main_server._handle_agent_event(event)

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    fake_mgr.handle_proactive_complete.assert_awaited_once()


@pytest.mark.unit
async def test_chat_and_hud_blind_emits_turn_end_exactly_once(monkeypatch):
    """visibility=["chat","hud"] + blind → BOTH chat passthrough AND HUD
    fire (per the existing visibility contract), but turn-end must be
    emitted EXACTLY ONCE. The HUD branch doesn't open an assistant turn,
    so a single emit gated on the chat passthrough is correct.
    """
    import main_server

    fake_mgr = _hud_fake_mgr()
    fake_mgr.handle_proactive_complete = AsyncMock()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event(["chat", "hud"]))

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    assert _hud_send_count(fake_mgr) == 1
    fake_mgr.handle_proactive_complete.assert_awaited_once()


@pytest.mark.unit
async def test_hud_only_blind_does_not_emit_turn_end(monkeypatch):
    """visibility=["hud"] + blind → HUD toast fires alone. agent_notification
    on the frontend is a toast that doesn't open an assistant turn lifecycle,
    so turn-end must NOT be emitted (no lifecycle to close).
    """
    import main_server

    fake_mgr = _hud_fake_mgr()
    fake_mgr.handle_proactive_complete = AsyncMock()
    _patch_main_server(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_hud_event(["hud"]))

    fake_mgr.passthrough_to_chat_bubble.assert_not_called()
    assert _hud_send_count(fake_mgr) == 1
    fake_mgr.handle_proactive_complete.assert_not_called()


def _blind_chat_event(task_id: str) -> dict:
    return {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "blind chat line",
        "channel": "plugin:foo",
        "task_id": task_id,
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "source_kind": "plugin",
        "source_name": "foo",
        "media_parts": [],
    }


@pytest.mark.unit
async def test_chat_blind_passthrough_noop_skips_turn_end(monkeypatch):
    """Real failure semantics: ``passthrough_to_chat_bubble`` SWALLOWS
    send_json failures and is a no-op when the WS is missing/disconnected
    (see ``test_passthrough_send_failure_is_logged_not_raised`` and
    ``test_passthrough_handles_disconnected_websocket_gracefully`` above).
    In every such case the helper returns ``False`` — no
    ``gemini_response`` was actually sent — so the frontend never opened
    an assistant turn lifecycle, and we must NOT emit a stray turn-end.

    The previous version of this test mocked the helper to raise, which
    missed the most-likely-leaked path: helper returns success-shape
    (``None`` under the old contract, ``False`` under the new one) but
    never sent a frame, while ``main_server`` still emitted turn-end
    based purely on absence-of-exception.
    """
    import main_server

    fake_mgr = MagicMock()
    # Simulate a swallowed no-op (e.g. WS disconnected mid-flight): the
    # helper returns normally but reports False to indicate nothing was
    # sent.
    fake_mgr.passthrough_to_chat_bubble = AsyncMock(return_value=False)
    fake_mgr.handle_proactive_complete = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    await main_server._handle_agent_event(_blind_chat_event("task-blind-noop"))

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    fake_mgr.handle_proactive_complete.assert_not_called()


@pytest.mark.unit
async def test_chat_blind_passthrough_unexpected_raise_skips_turn_end(monkeypatch):
    """Defensive belt-and-suspenders: the production helper is supposed
    to swallow all WS failures internally (returns False, never raises),
    but ``main_server`` still wraps the call in try/except. If a future
    refactor accidentally lets an exception escape, we must still NOT
    emit a stray turn-end.
    """
    import main_server

    fake_mgr = MagicMock()
    fake_mgr.passthrough_to_chat_bubble = AsyncMock(side_effect=RuntimeError("ws boom"))
    fake_mgr.handle_proactive_complete = AsyncMock()
    fake_mgr.enqueue_agent_callback = MagicMock()
    fake_mgr.trigger_agent_callbacks = AsyncMock()
    fake_mgr.websocket = None
    fake_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: fake_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    await main_server._handle_agent_event(_blind_chat_event("task-blind-raise"))

    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    fake_mgr.handle_proactive_complete.assert_not_called()


@pytest.mark.unit
async def test_chat_blind_passthrough_real_helper_swallowed_send_skips_turn_end(monkeypatch):
    """Lower-level integration: drive the REAL ``passthrough_to_chat_bubble``
    against a websocket whose ``send_json`` raises. The helper must
    swallow the exception, return False, and ``main_server`` must NOT
    call ``handle_proactive_complete``.

    This locks the end-to-end contract that motivated the bool return:
    "send_json blew up, was swallowed; no turn was ever opened on the
    frontend; do not close a phantom turn."
    """
    import main_server

    real_mgr = _make_mgr(websocket=_FakeWebsocket(connected=True))
    # Make send_json raise so the real helper exercises the swallow path.
    real_mgr.websocket.send_json = AsyncMock(side_effect=RuntimeError("ws boom"))
    real_mgr.handle_proactive_complete = AsyncMock()
    real_mgr.enqueue_agent_callback = MagicMock()
    real_mgr.trigger_agent_callbacks = AsyncMock()
    real_mgr._pending_agent_callback_task = None

    monkeypatch.setattr("main_server._get_session_manager", lambda name: real_mgr)
    monkeypatch.setattr("main_server._is_websocket_connected", lambda ws: False)

    await main_server._handle_agent_event(_blind_chat_event("task-blind-swallowed"))

    assert real_mgr.websocket.send_json.await_count == 1
    real_mgr.handle_proactive_complete.assert_not_called()
