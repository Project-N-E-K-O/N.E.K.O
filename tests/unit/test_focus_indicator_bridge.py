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
        state=types.SimpleNamespace(mode=mode),
    )
    # _on_focus_transition / _reconcile delegate to _push_focus_indicator via
    # self — attach it so the delegation resolves on the bare stub.
    stub._push_focus_indicator = LLMSessionManager._push_focus_indicator.__get__(
        stub, LLMSessionManager
    )
    return stub


def _bind(stub, name):
    return getattr(LLMSessionManager, name).__get__(stub, LLMSessionManager)


def _pushed(stub):
    return [m["data"] for m in stub.sync_message_queue.items if m.get("type") == "json"]


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
    asyncio.run(reconcile())                      # mode still FOCUS → no-op
    assert _pushed(stub) == []
    stub.state.mode = CognitionMode.REGULAR       # silent clear happened
    asyncio.run(reconcile())
    assert _pushed(stub) == [{"type": "focus_state", "active": False}]
