# -*- coding: utf-8 -*-
"""Focus (thinking-on) -> frontend indicator bridge.

The cognition badge is driven by ``LLMSessionManager._push_focus_indicator``
(idempotent on a cached on/off state). Two layers feed it: the
``FOCUS_ENTER``/``FOCUS_EXIT`` subscription (``_on_focus_transition``, immediate)
and a per-turn ``_reconcile_focus_indicator`` that mirrors ``state.mode`` so a
silent clear (clear_focus / master-switch self-clear, which emit no FOCUS_EXIT)
can't leave the badge stuck on. It pushes on/off only — no episode payload — and
must not raise with no live websocket.
"""
import asyncio
import os
import sys
import types

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.session_state import CognitionMode, SessionEvent


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _stub(mode=CognitionMode.REGULAR):
    stub = types.SimpleNamespace(
        sync_message_queue=_RecordingQueue(),
        websocket=None,            # no live ws → ws branch is skipped, never raises
        websocket_lock=None,
        lanlan_name="测试娘",
        _focus_indicator_active=False,
        # snapshot feeds _push_focus_charge (reconcile also re-pushes the glow).
        state=types.SimpleNamespace(
            mode=mode,
            snapshot=lambda: {"focus_charge": 0.0, "focus_charge_at": 0.0},
        ),
    )
    # _on_focus_transition / _reconcile delegate to these via self — attach so
    # the delegation resolves on the bare stub.
    stub._push_focus_indicator = LLMSessionManager._push_focus_indicator.__get__(
        stub, LLMSessionManager
    )
    stub._push_focus_charge = LLMSessionManager._push_focus_charge.__get__(
        stub, LLMSessionManager
    )
    stub._focus_thinking_active = False
    stub._push_focus_thinking = LLMSessionManager._push_focus_thinking.__get__(
        stub, LLMSessionManager
    )
    # _reconcile also calls _maybe_purge_focus_artifacts (history cleanup on a
    # silent Focus exit). Not armed here → it's a no-op, but must resolve on the
    # bare stub. session absent → the no-op returns before touching it.
    stub._focus_artifacts_pending = False
    stub.session = None
    stub._maybe_purge_focus_artifacts = (
        LLMSessionManager._maybe_purge_focus_artifacts.__get__(stub, LLMSessionManager)
    )
    return stub


def _bind(stub, name):
    return getattr(LLMSessionManager, name).__get__(stub, LLMSessionManager)


def _pushed(stub):
    return [m["data"] for m in stub.sync_message_queue.items if m.get("type") == "json"]


def _pushed_states(stub):
    """Only the binary focus_state messages — reconcile also emits focus_charge."""
    return [m for m in _pushed(stub) if m.get("type") == "focus_state"]


def test_enter_then_exit_pushes_active_on_off():
    stub = _stub()
    handler = _bind(stub, "_on_focus_transition")
    asyncio.run(handler(SessionEvent.FOCUS_ENTER, {"episode_id": "e1", "score": 0.9}))
    asyncio.run(handler(SessionEvent.FOCUS_EXIT, {"episode_id": "e1", "reason": "topic_switch"}))
    assert _pushed(stub) == [
        {"type": "focus_state", "active": True},
        {"type": "focus_state", "active": False},
    ]


def test_payload_is_ignored_only_on_off_carried():
    stub = _stub()
    handler = _bind(stub, "_on_focus_transition")
    asyncio.run(handler(SessionEvent.FOCUS_ENTER, {"episode_id": "secret", "charge": 1.4}))
    msg = stub.sync_message_queue.items[0]["data"]
    assert set(msg.keys()) == {"type", "active"}
    assert msg["active"] is True


def test_push_is_idempotent_on_cached_state():
    stub = _stub()
    push = _bind(stub, "_push_focus_indicator")
    asyncio.run(push(True))
    asyncio.run(push(True))   # no change → no second push
    asyncio.run(push(False))
    asyncio.run(push(False))  # no change → no second push
    assert _pushed(stub) == [
        {"type": "focus_state", "active": True},
        {"type": "focus_state", "active": False},
    ]


def test_reconcile_clears_badge_after_silent_focus_drop():
    # Badge is on; a silent clear (clear_focus) drops state.mode to REGULAR
    # without a FOCUS_EXIT event. The per-turn reconcile must push it off.
    stub = _stub(mode=CognitionMode.FOCUS)
    stub._focus_indicator_active = True
    reconcile = _bind(stub, "_reconcile_focus_indicator")
    asyncio.run(reconcile())                      # mode still FOCUS → badge no-op
    assert _pushed_states(stub) == []             # (charge is re-pushed separately)
    stub.state.mode = CognitionMode.REGULAR       # silent clear happened
    asyncio.run(reconcile())
    assert _pushed_states(stub) == [{"type": "focus_state", "active": False}]


def _pushed_thinking(stub):
    return [m for m in _pushed(stub) if m.get("type") == "focus_thinking"]


def test_thinking_pulse_on_off_and_idempotent():
    # _push_focus_thinking mirrors a transient "model is thinking" pulse and is
    # idempotent on its cached state, so per-chunk callers can clear blindly.
    stub = _stub()
    push = _bind(stub, "_push_focus_thinking")
    asyncio.run(push(True))
    asyncio.run(push(True))   # no change → no second push
    asyncio.run(push(False))
    asyncio.run(push(False))  # no change → no second push
    assert _pushed_thinking(stub) == [
        {"type": "focus_thinking", "active": True},
        {"type": "focus_thinking", "active": False},
    ]


def test_thinking_message_shape_only_type_and_active():
    stub = _stub()
    asyncio.run(_bind(stub, "_push_focus_thinking")(True))
    msg = _pushed_thinking(stub)[0]
    assert set(msg.keys()) == {"type", "active"}
    assert msg["active"] is True


def test_handle_thinking_active_pulses_bubble_on():
    # handle_thinking_active is the session callback fired when the model emits a
    # reasoning chunk on ANY turn (Focus or not). It pulses the bubble True via the
    # idempotent _push_focus_thinking — decoupled from the Focus inline decision.
    stub = _stub()
    stub.handle_thinking_active = _bind(stub, "handle_thinking_active")
    asyncio.run(stub.handle_thinking_active())
    assert _pushed_thinking(stub) == [{"type": "focus_thinking", "active": True}]


def test_handle_thinking_active_is_idempotent_within_turn():
    # Multiple reasoning chunks in one turn must not spam the bubble — the cached
    # state in _push_focus_thinking collapses repeated True pulses to one push.
    stub = _stub()
    handler = _bind(stub, "handle_thinking_active")
    asyncio.run(handler())
    asyncio.run(handler())
    assert _pushed_thinking(stub) == [{"type": "focus_thinking", "active": True}]


def test_thinking_callback_scoped_to_live_session():
    # _make_thinking_active_callback binds the pulse to ONE session so a stale /
    # pending OmniOfflineClient can't drive the current window's bubble: only the
    # client that is self.session forwards to _push_focus_thinking (CodeRabbit).
    stub = _stub()
    stub.handle_thinking_active = _bind(stub, "handle_thinking_active")
    make_cb = _bind(stub, "_make_thinking_active_callback")

    live = object()
    stale = object()
    stub.session = live

    # Callback bound to the live session forwards.
    asyncio.run(make_cb(live)(True))
    assert _pushed_thinking(stub) == [{"type": "focus_thinking", "active": True}]

    # Callback bound to a non-current session is a silent no-op (no extra push).
    asyncio.run(make_cb(stale)(False))
    assert _pushed_thinking(stub) == [{"type": "focus_thinking", "active": True}]


def test_handle_thinking_active_false_clears_bubble():
    # The same callback clears the bubble (active=False) — this is the end-of-stream
    # clear prompt_ephemeral fires when a proactive/greeting turn reasons but commits
    # no visible text, so the bubble can't get stuck on (Codex P2).
    stub = _stub()
    handler = _bind(stub, "handle_thinking_active")
    asyncio.run(handler(True))
    asyncio.run(handler(False))
    assert _pushed_thinking(stub) == [
        {"type": "focus_thinking", "active": True},
        {"type": "focus_thinking", "active": False},
    ]


def test_thinking_force_re_pushes_for_new_window():
    # resync_focus_for_new_window replays the thinking pulse with force=True so a
    # window opened mid-thinking lands on the current bubble — the idempotent
    # guard must NOT swallow the re-push even though the cached state is unchanged.
    stub = _stub()
    push = _bind(stub, "_push_focus_thinking")
    asyncio.run(push(True))            # mid-thinking
    asyncio.run(push(True, force=True))  # new window connects → forced replay
    assert _pushed_thinking(stub) == [
        {"type": "focus_thinking", "active": True},
        {"type": "focus_thinking", "active": True},
    ]
