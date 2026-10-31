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
    monkeypatch.setattr(lifecycle, "get_conversation_service", lambda: object())
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


def test_disabled_or_not_ready_fact_does_not_enter_subscription_queue(forwarding, monkeypatch):
    api, facts, _ = forwarding
    from plugin.server import lifecycle

    event = {"lanlan_name": "YUI", "fact": "input.submitted", "payload": {}}
    monkeypatch.setattr(lifecycle, "get_conversation_service", lambda: None)
    assert api._forward_conversation_fact(event) is False
    monkeypatch.setattr(api, "_user_plugins_enabled", lambda: False)
    assert api._forward_conversation_fact(event) is False
    assert facts == []
