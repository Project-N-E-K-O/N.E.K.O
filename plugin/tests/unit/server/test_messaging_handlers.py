from __future__ import annotations

import pytest

from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import domain_error_payload, resolve_common_fields


@pytest.mark.plugin_unit
def test_resolve_common_fields_validation() -> None:
    assert resolve_common_fields({}) is None
    assert resolve_common_fields({"from_plugin": " ", "request_id": "r1"}) is None
    assert resolve_common_fields({"from_plugin": "p1", "request_id": " "}) is None

    got = resolve_common_fields({"from_plugin": " p1 ", "request_id": " r1 ", "timeout": 1.5})
    assert got == ("p1", "r1", 1.5)


@pytest.mark.plugin_unit
def test_domain_error_payload_with_and_without_details() -> None:
    e1 = ServerDomainError(code="E1", message="m1", status_code=400, details={})
    assert domain_error_payload(e1) == {"code": "E1", "message": "m1"}

    e2 = ServerDomainError(code="E2", message="m2", status_code=500, details={"x": 1})
    assert domain_error_payload(e2) == {"code": "E2", "message": "m2", "details": {"x": 1}}



import pytest

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

    monkeypatch.setattr(
        bus_subscribe_module.bus_subscription_service,
        "subscribe",
        lambda **_: {"ok": True, "sub_id": "s1"},
    )
    await bus_subscribe_module.handle_bus_subscribe(
        {"from_plugin": "p1", "request_id": "r1", "bus": "events"},
        send,
    )
    assert send.calls[-1][2] == {"ok": True, "sub_id": "s1"}

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

