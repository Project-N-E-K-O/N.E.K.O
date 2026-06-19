# -*- coding: utf-8 -*-
"""Focus (thinking-on) -> frontend indicator bridge.

``LLMSessionManager._on_focus_transition`` is the SM subscriber that mirrors a
Focus enter/exit to the frontend as an ephemeral ``focus_state`` message (the
subtle breathing badge). It pushes on/off only — no episode payload — and must
not raise even with no live websocket.
"""
import asyncio
import os
import sys
import types

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.session_state import SessionEvent


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def _bound_handler():
    stub = types.SimpleNamespace(
        sync_message_queue=_RecordingQueue(),
        websocket=None,            # no live ws → ws branch is skipped, never raises
        websocket_lock=None,
        lanlan_name="测试娘",
    )
    return stub, LLMSessionManager._on_focus_transition.__get__(stub, LLMSessionManager)


def test_enter_then_exit_pushes_active_on_off():
    stub, handler = _bound_handler()
    asyncio.run(handler(SessionEvent.FOCUS_ENTER, {"episode_id": "e1", "score": 0.9}))
    asyncio.run(handler(SessionEvent.FOCUS_EXIT, {"episode_id": "e1", "reason": "topic_switch"}))

    datas = [m["data"] for m in stub.sync_message_queue.items if m.get("type") == "json"]
    assert datas == [
        {"type": "focus_state", "active": True},
        {"type": "focus_state", "active": False},
    ]


def test_payload_is_ignored_only_on_off_carried():
    # The badge needs on/off only; the episode payload (used by memory) must not
    # leak into the frontend message.
    stub, handler = _bound_handler()
    asyncio.run(handler(SessionEvent.FOCUS_ENTER, {"episode_id": "secret", "charge": 1.4}))
    msg = stub.sync_message_queue.items[0]["data"]
    assert set(msg.keys()) == {"type", "active"}
    assert msg["active"] is True
