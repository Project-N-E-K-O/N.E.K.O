from __future__ import annotations

import asyncio

import pytest

from plugin.server.application.bus import subscription_service as bus_subscription_service_module
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers import bus_delete as bus_delete_module
from plugin.server.messaging.handlers import bus_subscribe as bus_subscribe_module
from plugin.server.messaging.handlers import plugin_config as plugin_config_module


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, object, float]] = []

    def __call__(
        self,
        to_plugin: str,
        request_id: str,
        result: object,
        error: object,
        timeout: float = 10.0,
    ) -> None:
        self.calls.append((to_plugin, request_id, result, error, timeout))


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bus_subscribe_and_unsubscribe_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()
    subscribe_calls: list[dict[str, object]] = []

    def _subscribe(**kwargs: object) -> dict[str, object]:
        subscribe_calls.append(dict(kwargs))
        return {"ok": True, "sub_id": "s1"}

    monkeypatch.setattr(
        bus_subscribe_module.bus_subscription_service,
        "subscribe",
        _subscribe,
    )
    await bus_subscribe_module.handle_bus_subscribe(
        {
            "from_plugin": "p1",
            "request_id": "r1",
            "bus": "events",
            "plan": {"kind": "get"},
        },
        send,
    )
    assert send.calls[-1][2] == {"ok": True, "sub_id": "s1"}
    assert "plan" not in subscribe_calls[-1]

    def _raise_subscribe(**_: object) -> object:
        raise ServerDomainError(code="E", message="subscribe failed", status_code=400, details={})

    monkeypatch.setattr(
        bus_subscribe_module.bus_subscription_service,
        "subscribe",
        _raise_subscribe,
    )
    await bus_subscribe_module.handle_bus_subscribe(
        {"from_plugin": "p1", "request_id": "r2", "bus": "events"},
        send,
    )
    assert send.calls[-1][3] == "subscribe failed"

    monkeypatch.setattr(
        bus_subscribe_module.bus_subscription_service,
        "unsubscribe",
        lambda **_: {"ok": True},
    )
    await bus_subscribe_module.handle_bus_unsubscribe(
        {"from_plugin": "p1", "request_id": "r3", "bus": "events", "sub_id": "s1"},
        send,
    )
    assert send.calls[-1][2] == {"ok": True}


@pytest.mark.plugin_unit
def test_bus_subscription_service_does_not_store_replay_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _capture(bus: str, sub_id: str, info: dict[str, object]) -> None:
        captured.update(bus=bus, sub_id=sub_id, info=info)

    monkeypatch.setattr(bus_subscription_service_module, "new_sub_id", lambda: "s1")
    monkeypatch.setattr(bus_subscription_service_module.state, "add_bus_subscription", _capture)
    monkeypatch.setattr(bus_subscription_service_module.state, "get_bus_rev", lambda _bus: 7)

    result = bus_subscription_service_module.BusSubscriptionService().subscribe(
        from_plugin="p1",
        bus="events",
        deliver="delta",
        rules=["add", "change"],
        debounce_ms=25,
        timeout=2.0,
    )

    assert result == {"ok": True, "sub_id": "s1", "bus": "events", "rev": 7}
    assert captured["info"] == {
        "from_plugin": "p1",
        "bus": "events",
        "rules": ["add", "change"],
        "deliver": "delta",
        "debounce_ms": 25,
        "timeout": 2.0,
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bus_delete_validation_and_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    await bus_delete_module.handle_message_del(
        {"from_plugin": "p1", "request_id": "r1"},
        send,
    )
    assert send.calls[-1][3] == "message_id is required"

    def _raise_delete(_: str) -> bool:
        raise ServerDomainError(code="E", message="delete failed", status_code=500, details={})

    monkeypatch.setattr(
        bus_delete_module.bus_mutation_service,
        "delete_event",
        _raise_delete,
    )
    await bus_delete_module.handle_event_del(
        {"from_plugin": "p1", "request_id": "r2", "event_id": "e1"},
        send,
    )
    assert send.calls[-1][3] == "delete failed"

    monkeypatch.setattr(
        bus_delete_module.bus_mutation_service,
        "delete_lifecycle",
        lambda _: True,
    )
    await bus_delete_module.handle_lifecycle_del(
        {"from_plugin": "p1", "request_id": "r3", "lifecycle_id": "l1"},
        send,
    )
    assert send.calls[-1][2] == {"deleted": True, "lifecycle_id": "l1"}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_config_scope_and_payload_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    await plugin_config_module.handle_plugin_config_get(
        {"from_plugin": "p1", "request_id": "r1", "plugin_id": "p2"},
        send,
    )
    assert send.calls[-1][3] == "Permission denied: can only access own config"

    await plugin_config_module.handle_plugin_config_update(
        {"from_plugin": "p1", "request_id": "r2", "updates": "bad"},
        send,
    )
    assert send.calls[-1][3] == "Invalid updates: must be a dict"

    await plugin_config_module.handle_plugin_config_effective_get(
        {"from_plugin": "p1", "request_id": "r3", "profile_name": " "},
        send,
    )
    assert send.calls[-1][3] == "Invalid profile_name"

    async def _get_config(*, plugin_id: str) -> dict[str, object]:
        assert plugin_id == "p1"
        return {"config": {"k": 1}}

    monkeypatch.setattr(
        plugin_config_module.config_query_service,
        "get_plugin_config",
        _get_config,
    )
    await plugin_config_module.handle_plugin_config_get(
        {"from_plugin": "p1", "request_id": "r4"},
        send,
    )
    assert send.calls[-1][2] == {"config": {"k": 1}}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_config_update_returns_memory_only_payload_before_persistence_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = _Recorder()
    started = asyncio.Event()

    async def _stuck_update(*, plugin_id: str, updates: object) -> dict[str, object]:
        assert plugin_id == "p1"
        assert updates == {"feature": {"enabled": True}}
        started.set()
        await asyncio.sleep(10)
        return {"config": {"feature": {"enabled": True}}}

    monkeypatch.setattr(
        plugin_config_module.config_command_service,
        "update_plugin_config",
        _stuck_update,
    )

    await asyncio.wait_for(
        plugin_config_module.handle_plugin_config_update(
            {
                "from_plugin": "p1",
                "request_id": "r-timeout",
                "updates": {"feature": {"enabled": True}},
                "timeout": 0.05,
            },
            send,
        ),
        timeout=0.5,
    )

    assert started.is_set()
    to_plugin, request_id, result, error, timeout = send.calls[-1]
    assert to_plugin == "p1"
    assert request_id == "r-timeout"
    assert error is None
    assert timeout == 0.05
    assert result == {
        "success": False,
        "plugin_id": "p1",
        "config": {"feature": {"enabled": True}},
        "requires_reload": False,
        "persisted": False,
        "message": "Config persistence timed out; update is applied in plugin memory only",
    }
