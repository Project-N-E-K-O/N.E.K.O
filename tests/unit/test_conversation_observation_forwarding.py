"""Agent observations only enqueue facts and preserve explicit producer fields."""
from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture
def forwarding(monkeypatch):
    api = importlib.import_module("app.agent_server.api_runtime")
    from plugin.server import lifecycle
    from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
    from plugin.server.messaging import plane_bridge

    facts, records = [], []
    monkeypatch.setattr(api, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(lifecycle, "get_conversation_service", lambda *, require_ready=True: object())
    monkeypatch.setattr(
        bus_subscription_manager, "accept_raw_conversation_fact",
        lambda fact, payload: facts.append((fact, payload)),
    )
    monkeypatch.setattr(plane_bridge, "publish_record", lambda **value: records.append(value))
    return api, facts, records


def test_fact_ingress_defers_details_and_does_not_mutate_source(forwarding):
    api, facts, _ = forwarding
    payload = {"input_id": "input-1", "text": "raw submission", "input_source": "text"}
    before = time.time()
    assert api._forward_conversation_fact({
        "event_type": "conversation_fact_observed", "lanlan_name": "YUI",
        "fact": "input.submitted", "payload": payload,
    })
    assert "character" not in payload and "observed_at" not in payload
    fact, accepted = facts[0]
    assert fact == "input.submitted"
    assert accepted["character"] == "YUI" and accepted["input_id"] == "input-1"
    assert before <= accepted["observed_at"] <= time.time()


def test_record_keeps_identity_and_association_without_inventing_producer_time(forwarding):
    api, _, records = forwarding
    origin = {"kind": "agent", "is_proactive": True, "plugin_id": None, "task_id": None}
    assert api._forward_conversation_turn({
        "event_id": "message-1", "lanlan_name": "YUI", "content": "answer",
        "turn_type": "assistant_message", "conversation_id": "old-association",
        "metadata": {"reply_id": "reply-1", "input_id": "input-1", "origin": origin},
    })
    record = records[0]["record"]
    assert record["id"] == "message-1"
    assert record["reply_id"] == "reply-1" and record["input_id"] == "input-1"
    assert record["origin"] == origin
    assert record["metadata"]["conversation_id"] == "old-association"
    assert "occurred_at" not in record
    assert isinstance(record["metadata"]["ts"], float)


@pytest.mark.parametrize("producer_time,expected", [(90.0, 90.0), (True, None), ("nan", None)])
def test_only_finite_explicit_producer_time_is_exposed(forwarding, producer_time, expected):
    api, _, records = forwarding
    assert api._forward_conversation_turn({
        "event_id": "m", "lanlan_name": "YUI", "content": "hello",
        "turn_type": "user_message", "ts": producer_time,
    })
    assert records[0]["record"].get("occurred_at") == expected


def test_disabled_or_absent_service_fact_does_not_enter_subscription_queue(forwarding, monkeypatch):
    api, facts, _ = forwarding
    from plugin.server import lifecycle

    event = {"lanlan_name": "YUI", "fact": "input.submitted", "payload": {}}
    monkeypatch.setattr(lifecycle, "get_conversation_service", lambda *, require_ready=True: None)
    assert api._forward_conversation_fact(event) is False
    assert facts == []
    monkeypatch.setattr(lifecycle, "get_conversation_service", lambda *, require_ready=True: object())
    monkeypatch.setattr(api, "_user_plugins_enabled", lambda: False)
    assert api._forward_conversation_fact(event) is False
    assert facts == []


@pytest.mark.asyncio
async def test_fact_ingress_retains_reply_boundaries_while_details_are_not_ready(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from plugin.server import lifecycle
    from plugin.server.application.conversations import ConversationService
    from plugin.server.messaging import bus_subscriptions as bus_module
    from plugin.server.messaging.bus_subscriptions import BusSubscriptionManager
    from plugin.tests.unit.test_conversations_v2_host import FakeState

    api = importlib.import_module("app.agent_server.api_runtime")
    monkeypatch.setattr(api, "_user_plugins_enabled", lambda: True)
    manager = BusSubscriptionManager()
    monkeypatch.setattr(bus_module, "state", FakeState())
    monkeypatch.setattr(bus_module, "bus_subscription_manager", manager)
    monkeypatch.setattr(lifecycle, "bus_subscription_manager", manager)
    service = lifecycle.ServerLifecycleService()
    retained = ConversationService(lambda _: None)
    service._conversation_service = retained
    monkeypatch.setattr(lifecycle, "_service", service)
    projected = asyncio.Event()

    def accept(fact, payload):
        result = retained.accept_fact(fact, payload)
        if fact == "reply.closed":
            projected.set()
        return result

    manager.set_conversation_observers(on_fact=accept)
    await manager.start()
    try:
        assert lifecycle.get_conversation_service() is None
        for fact, text in (("reply.started", "prefix"), ("reply.closed", "complete")):
            assert api._forward_conversation_fact({
                "lanlan_name": "YUI", "fact": fact,
                "payload": {"reply_id": "r", "text": text, "close_reason": "completed"},
            })
        await asyncio.wait_for(projected.wait(), 1)
        assert lifecycle.get_conversation_service() is None
        service._message_plane_runner = SimpleNamespace(
            is_alive=lambda: True, health_check_async=AsyncMock(return_value=True),
        )
        assert await service._check_message_plane_health()
        reply = lifecycle.get_conversation_service().get_reply("r")
        assert reply["text_state"] == "closed" and reply["close_reason"] == "completed"
        assert reply["text"] == "complete"
    finally:
        await manager.stop()
