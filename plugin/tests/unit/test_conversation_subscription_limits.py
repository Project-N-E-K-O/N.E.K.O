from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from plugin.message_plane.protocol import ConversationGetArgs, ConversationGetMessageArgs
from plugin.server.application.bus.subscription_service import BusSubscriptionService
from plugin.server.domain.errors import ServerDomainError


def test_subscription_admission_is_bounded_and_unsubscribe_returns_capacity(monkeypatch):
    module = importlib.import_module("plugin.core.state")
    state = module.state
    monkeypatch.setattr(module, "MAX_CONVERSATION_SUBSCRIPTIONS", 2)
    monkeypatch.setattr(module, "MAX_PLUGIN_CONVERSATION_SUBSCRIPTIONS", 1)
    monkeypatch.setattr(state, "_bus_subscriptions", {"conversations": {}, "messages": {}})
    service = BusSubscriptionService()

    def subscribe(plugin_id):
        return service.subscribe(
            from_plugin=plugin_id, bus="conversations", contract="conversations_v2",
            deliver="delta", rules=["record"], filters={}, view=False,
            debounce_ms=None, timeout=5,
        )

    first = subscribe("p1")
    with pytest.raises(ServerDomainError) as full_owner:
        subscribe("p1")
    assert full_owner.value.code == "BUSY"
    subscribe("p2")
    with pytest.raises(ServerDomainError) as full_host:
        subscribe("p3")
    assert full_host.value.code == "BUSY"
    denied = service.unsubscribe(from_plugin="p2", bus="conversations", sub_id=first["sub_id"])
    assert denied["ok"] is False
    assert first["sub_id"] in state.get_bus_subscriptions("conversations")
    with pytest.raises(ServerDomainError) as still_full:
        subscribe("p3")
    assert still_full.value.code == "BUSY"
    service.unsubscribe(from_plugin="p1", bus="conversations", sub_id=first["sub_id"])
    assert subscribe("p3")["sub_id"]
    # Existing buses do not acquire the new observation-specific limits.
    for index in range(3):
        state.add_bus_subscription("messages", str(index), {"from_plugin": "p3"})
    assert len(state.get_bus_subscriptions("messages")) == 3


def test_new_query_rejects_unrepresentable_or_unbounded_wire_values():
    with pytest.raises(ValidationError):
        ConversationGetArgs(max_count=2**100)
    with pytest.raises(ValidationError):
        ConversationGetArgs(character="x" * 513)
    with pytest.raises(ValidationError):
        ConversationGetMessageArgs(message_id="x" * 257)
    assert ConversationGetArgs(max_count=100_000).max_count == 100_000
