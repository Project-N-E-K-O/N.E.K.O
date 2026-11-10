"""Recent plugin context must agree across local and session-event delivery."""

from __future__ import annotations

import asyncio
import importlib
import time
from types import SimpleNamespace

import pytest

from app.agent_server import api_runtime
from main_logic import agent_event_bus
from main_logic.core.turn import TurnMixin
from plugin.core.bus.memory import MemoryRecord
from plugin.core.state import state
from plugin.sdk.shared.core.bus_context import SdkBusMemoryRecord
from plugin.server.messaging import plane_bridge
from tests.fake_clock import patch_module_clock

state_module = importlib.import_module("plugin.core.state")


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(state, "_user_context_store", {})
    monkeypatch.setattr(state, "_user_context_default_maxlen", 3)
    monkeypatch.setattr(state, "_user_context_ttl_seconds", 3600)
    monkeypatch.setattr(
        agent_event_bus, "_user_utterance_sinks", [state.add_user_context_event]
    )
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(plane_bridge, "publish_record", lambda **kwargs: False)
    return state


def turn_event(*, event_id="event-1", timestamp=None, character="YUI", voice=True):
    return {
        "event_type": "conversation_turn_observed",
        "event_id": event_id,
        "lanlan_name": character,
        "turn_type": "user_message",
        "content": "SYNTHETIC_USER_CONTEXT",
        "source": "main_logic.core",
        "ts": time.time() if timestamp is None else timestamp,
        "metadata": {"role": "master", "is_voice": voice},
    }


@pytest.mark.parametrize("voice", [True, False])
def test_session_event_projects_user_context_even_when_plane_refuses(context, voice):
    event = turn_event(voice=voice)
    asyncio.run(api_runtime._on_session_event(event))
    for bucket in ("default", "YUI"):
        records = context.get_user_context(bucket)
        assert len(records) == 1
        record = SdkBusMemoryRecord.from_raw(
            MemoryRecord.from_raw(records[0], bucket_id=bucket)
        ).dump()
        assert record["type"] == "user_message"
        assert record["is_voice"] is voice
        assert record["lanlan"] == "YUI"
        assert record["_ts"] == event["ts"]
    assert context.get_user_context("other-character") == []


@pytest.mark.parametrize("character", ["YUI", "default"])
def test_local_and_replayed_forward_share_identity_and_time(
    context, monkeypatch, character
):
    captured = []
    tasks = []

    class Bridge:
        async def publish_session_event_threadsafe(self, event):
            captured.append(event)
            return True

    monkeypatch.setattr(agent_event_bus, "_main_bridge_ref", Bridge())

    async def scenario():
        core = SimpleNamespace(
            lanlan_name=character,
            _fire_task=lambda coro: tasks.append(asyncio.create_task(coro)),
        )
        timestamp = time.time() - 15
        # Equal words in different utterances must not be content-deduplicated.
        for _ in range(2):
            TurnMixin._publish_user_utterance_to_plugin_bus(
                core, "SYNTHETIC", is_voice_source=True, ts=timestamp
            )
        await asyncio.gather(*tasks)
        for event in captured * 2:
            await api_runtime._on_session_event(event)
        return timestamp

    timestamp = asyncio.run(scenario())
    assert len(captured) == 2 and captured[0]["event_id"] != captured[1]["event_id"]
    for bucket in {"default", character}:
        records = context.get_user_context(bucket)
        assert len(records) == 2
        assert {item["event_id"] for item in records} == {
            item["event_id"] for item in captured
        }
        assert all(item["_ts"] == timestamp for item in records)


def test_out_of_order_delivery_keeps_latest_record_and_bounded_capacity(
    context, monkeypatch
):
    patch_module_clock(monkeypatch, state_module, time=lambda: 10000.0)
    for event_id, timestamp in [
        ("latest", 9999),
        ("old", 9990),
        ("middle", 9995),
        ("older", 9980),
    ]:
        context.add_user_context_event("YUI", {"event_id": event_id, "_ts": timestamp})
    assert [item["event_id"] for item in context.get_user_context("YUI")] == [
        "old",
        "middle",
        "latest",
    ]
    assert context.get_user_context("YUI", limit=1)[0]["event_id"] == "latest"


def test_clock_rollback_does_not_reorder_producer_timestamps(context, monkeypatch):
    # The producer clock was ahead of the receiver after a wall-clock rollback.
    patch_module_clock(monkeypatch, state_module, time=lambda: 1000.0)
    context.add_user_context_event("YUI", {"event_id": "newer", "_ts": 1100.0})
    patch_module_clock(monkeypatch, state_module, time=lambda: 1010.0)
    context.add_user_context_event("YUI", {"event_id": "older", "_ts": 1050.0})
    records = context.get_user_context("YUI")
    assert [record["event_id"] for record in records] == ["older", "newer"]
    assert records[-1]["_ts"] == 1100.0
    assert context.get_user_context("YUI", limit=1)[0]["event_id"] == "newer"


def test_clock_rollback_at_capacity_keeps_newest_occurrences(context, monkeypatch):
    patch_module_clock(monkeypatch, state_module, time=lambda: 1000.0)
    for index in range(3):
        context.add_user_context_event(
            "YUI", {"event_id": f"future-{index}", "_ts": 1100.0 + index}
        )
    # Arrival order must not evict a newer occurrence, including at capacity.
    context.add_user_context_event("YUI", {"event_id": "late-old", "_ts": 1050.0})
    records = context.get_user_context("YUI")
    assert [record["event_id"] for record in records] == [
        "future-0",
        "future-1",
        "future-2",
    ]
    assert [record["_ts"] for record in records] == [1100.0, 1101.0, 1102.0]
    # A genuinely newer occurrence still replaces the oldest retained record.
    context.add_user_context_event("YUI", {"event_id": "newest", "_ts": 1103.0})
    assert [record["event_id"] for record in context.get_user_context("YUI")] == [
        "future-1",
        "future-2",
        "newest",
    ]


def test_expired_replay_does_not_renew_activity(context, monkeypatch):
    patch_module_clock(monkeypatch, state_module, time=lambda: 10000.0)
    event = turn_event(timestamp=9990)
    asyncio.run(api_runtime._on_session_event(event))
    patch_module_clock(monkeypatch, state_module, time=lambda: 14000.0)
    asyncio.run(api_runtime._on_session_event(event))
    assert context.get_user_context("YUI") == []
    assert context.get_user_context("default") == []


@pytest.mark.parametrize(
    "timestamp", ["invalid", None, float("nan"), float("inf"), -1.0]
)
def test_invalid_or_expired_context_timestamp_is_not_retained(context, timestamp):
    context.add_user_context_event("YUI", {"_ts": timestamp})
    assert context.get_user_context("YUI") == []


@pytest.mark.parametrize(
    "change",
    [
        {"turn_type": "assistant_message"},
        {"turn_type": "proactive_reply"},
        {"metadata": {}},
        {"metadata": {"is_voice": "true"}},
        {"content": "  "},
    ],
)
def test_non_user_or_incomplete_turns_do_not_create_context(context, change):
    asyncio.run(api_runtime._on_session_event({**turn_event(), **change}))
    assert context.get_user_context("default") == []


def test_disabled_plugins_do_not_retain_forwarded_context(context, monkeypatch):
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: False)
    asyncio.run(api_runtime._on_session_event(turn_event()))
    assert context.get_user_context("default") == []


@pytest.mark.parametrize("timestamp", [None, True, "bad", float("nan"), float("inf")])
def test_invalid_forwarded_timestamp_does_not_become_fresh_activity(context, timestamp):
    asyncio.run(api_runtime._on_session_event({**turn_event(), "ts": timestamp}))
    assert context.get_user_context("default") == []


def test_projection_does_not_redispatch_other_utterance_sinks(context, monkeypatch):
    def unrelated_sink(*args):
        pytest.fail("Agent projection repeated a chat-side persistence sink")

    monkeypatch.setattr(agent_event_bus, "_user_utterance_sinks", [unrelated_sink])
    asyncio.run(api_runtime._on_session_event(turn_event()))
    assert len(context.get_user_context("YUI")) == 1
