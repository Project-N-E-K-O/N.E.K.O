"""Conversation-bus publishing: user utterance + AI turn + message timestamps.

Contract: user text/voice -> ``user_message``; AI turn end -> one record with
the whole sentence (``assistant_message`` / ``proactive_reply``); both carry
``ts`` so consumers can order the conversation.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from main_logic.core import turn as turn_module


class _StubManager(turn_module.TurnMixin):
    """Carries only what the publish path touches; no real session manager."""

    def __init__(self) -> None:
        self.lanlan_name = "YUI"
        self._current_ai_turn_text = ""
        self._current_ai_turn_id = ""
        self.noted: list[str | None] = []
        self._bg: list[asyncio.Task] = []

    def _fire_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._bg.append(task)
        return task

    def _note_ai_turn(self, *, text=None):
        self.noted.append(text)

    async def drain(self) -> None:
        if self._bg:
            await asyncio.gather(*self._bg, return_exceptions=True)


@pytest.fixture
def published(monkeypatch):
    calls: list[dict] = []

    async def _fake(lanlan_name, **kwargs):
        calls.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    monkeypatch.setattr(
        turn_module, "publish_conversation_turn_observed_best_effort", _fake,
    )
    return calls


def test_user_utterance_published_with_own_timestamp(published):
    async def _scenario():
        stub = _StubManager()
        before = time.time()
        turn_module.TurnMixin._publish_user_utterance_to_plugin_bus(
            stub, "  在吗  ", is_voice_source=True,
        )
        await stub.drain()
        return stub, before

    _, before = asyncio.run(_scenario())
    assert len(published) == 1
    call = published[0]
    assert call["lanlan_name"] == "YUI"
    assert call["content"] == "在吗"
    assert call["turn_type"] == "user_message"
    assert call["metadata"]["role"] == "master"
    assert call["metadata"]["is_voice"] is True
    assert before <= call["ts"] <= time.time() + 1
    assert call["metadata"]["ts"] == call["ts"]


def test_blank_user_utterance_is_not_published(published):
    async def _scenario():
        stub = _StubManager()
        turn_module.TurnMixin._publish_user_utterance_to_plugin_bus(
            stub, "   ", is_voice_source=False,
        )
        await stub.drain()

    asyncio.run(_scenario())
    assert published == []


def test_ai_turn_publishes_whole_text_once(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_text = "喵，我在的。"
        stub._current_ai_turn_id = "turn-42"
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(
            stub, turn_type="proactive_reply",
        )
        await stub.drain()
        return stub

    stub = asyncio.run(_scenario())
    assert len(published) == 1
    call = published[0]
    assert call["content"] == "喵，我在的。"
    assert call["turn_type"] == "proactive_reply"
    assert call["conversation_id"] == "turn-42"
    assert call["metadata"]["role"] == "cat"
    assert call["ts"] == call["metadata"]["ts"]
    # buffer 已清空，且 activity tracker 拿到同一份文本
    assert stub._current_ai_turn_text == ""
    assert stub.noted == ["喵，我在的。"]


def test_ai_flush_defaults_to_assistant_message(published):
    async def _scenario():
        stub = _StubManager()
        stub._current_ai_turn_text = "普通回复。"
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert published and published[0]["turn_type"] == "assistant_message"


def test_empty_ai_turn_publishes_nothing(published):
    async def _scenario():
        stub = _StubManager()
        turn_module.TurnMixin._flush_ai_turn_text_to_tracker(stub)
        await stub.drain()

    asyncio.run(_scenario())
    assert published == []


def test_publisher_event_carries_message_ts(monkeypatch):
    from main_logic import agent_event_bus as bus

    captured: list[dict] = []

    class _Bridge:
        async def publish_session_event_threadsafe(self, event):
            captured.append(event)
            return True

    monkeypatch.setattr(bus, "_main_bridge_ref", _Bridge())
    ok = asyncio.run(bus.publish_conversation_turn_observed_best_effort(
        "YUI",
        content="我在的。",
        turn_type="assistant_message",
        conversation_id="turn-1",
        source="main_logic.core",
        message_count=1,
        metadata={"role": "cat", "ts": 111.5},
        ts=111.5,
    ))
    assert ok is True
    assert captured and captured[0]["ts"] == 111.5
    assert captured[0]["metadata"]["role"] == "cat"


def test_forward_conversation_turn_keeps_producer_ts(monkeypatch):
    from app.agent_server import api_runtime
    from plugin.server.messaging import plane_bridge

    records: list[dict] = []
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(
        plane_bridge, "publish_record",
        lambda *, store, record, topic: records.append(record) or True,
    )

    assert api_runtime._forward_conversation_turn({
        "content": "我在的。",
        "ts": 222.25,
        "turn_type": "assistant_message",
        "conversation_id": "turn-2",
        "lanlan_name": "YUI",
        "source": "main_logic.core",
    }) is True
    assert records and records[0]["timestamp"] == 222.25
    assert records[0]["content"] == "我在的。"


def test_forward_conversation_turn_falls_back_to_now(monkeypatch):
    from app.agent_server import api_runtime
    from plugin.server.messaging import plane_bridge

    records: list[dict] = []
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(
        plane_bridge, "publish_record",
        lambda *, store, record, topic: records.append(record) or True,
    )
    before = time.time()
    api_runtime._forward_conversation_turn({"content": "没有 ts 的旧事件"})
    assert records and before <= records[0]["timestamp"] <= time.time() + 1
