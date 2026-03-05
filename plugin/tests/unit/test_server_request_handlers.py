from __future__ import annotations


import pytest

from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests import events as events_module
from plugin.server.requests import export as export_module
from plugin.server.requests import lifecycle as lifecycle_module
from plugin.server.requests import memory as memory_module
from plugin.server.requests import plugin_query as plugin_query_module
from plugin.server.requests import plugin_to_plugin as p2p_module
from plugin.server.requests import registry as registry_module
from plugin.server.requests import run_update as run_update_module
from plugin.server.requests import system_config as system_config_module
from plugin.server.requests import user_context as user_context_module


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
async def test_export_and_run_update_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    monkeypatch.setattr(export_module.run_ipc_service, "push_export", lambda **kwargs: {"ok": True})
    await export_module.handle_export_push({"from_plugin": "p", "request_id": "1", "timeout": 3}, send)
    assert send.calls[-1][2] == {"ok": True}

    def _raise_domain(**kwargs):
        raise ServerDomainError(code="E", message="bad", status_code=400, details={"x": 1})

    monkeypatch.setattr(export_module.run_ipc_service, "push_export", _raise_domain)
    await export_module.handle_export_push({"from_plugin": "p", "request_id": "2"}, send)
    assert isinstance(send.calls[-1][3], dict)

    monkeypatch.setattr(run_update_module.run_ipc_service, "update_run", lambda **kwargs: {"updated": True})
    await run_update_module.handle_run_update({"from_plugin": "p", "request_id": "3"}, send)
    assert send.calls[-1][2] == {"updated": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_system_config_and_memory_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    async def _get_config() -> dict[str, object]:
        return {"config": {"A": 1}}

    monkeypatch.setattr(system_config_module.admin_query_service, "get_system_config", _get_config)
    await system_config_module.handle_plugin_system_config_get({"from_plugin": "p", "request_id": "1"}, send)
    assert send.calls[-1][2] == {"config": {"A": 1}}

    async def _fail_config() -> dict[str, object]:
        raise ServerDomainError(code="E", message="config failed", status_code=500, details={})

    monkeypatch.setattr(system_config_module.admin_query_service, "get_system_config", _fail_config)
    await system_config_module.handle_plugin_system_config_get({"from_plugin": "p", "request_id": "2"}, send)
    assert send.calls[-1][3] == "config failed"

    async def _query_memory(**kwargs) -> dict[str, object]:
        return {"result": "ok"}

    monkeypatch.setattr(memory_module.memory_query_service, "query_memory", _query_memory)
    await memory_module.handle_memory_query({"from_plugin": "p", "request_id": "3"}, send)
    assert send.calls[-1][2] == {"result": "ok"}

    async def _fail_memory(**kwargs) -> dict[str, object]:
        raise KeyError("boom")

    monkeypatch.setattr(memory_module.memory_query_service, "query_memory", _fail_memory)
    await memory_module.handle_memory_query({"from_plugin": "p", "request_id": "4"}, send)
    assert send.calls[-1][3] == "Internal server error"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_user_context_and_plugin_query_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    async def _get_context(**kwargs) -> dict[str, object]:
        return {"history": []}

    monkeypatch.setattr(user_context_module.user_context_query_service, "get_user_context", _get_context)
    await user_context_module.handle_user_context_get(
        {"from_plugin": "p", "request_id": "1", "bucket_id": "b1", "limit": 5},
        send,
    )
    assert send.calls[-1][2] == {"history": []}

    await user_context_module.handle_user_context_get({"from_plugin": "p", "request_id": "2"}, send)
    assert send.calls[-1][3] == "Invalid bucket_id"

    async def _query_plugins(**kwargs) -> list[dict[str, object]]:
        return [{"plugin_id": "a"}]

    monkeypatch.setattr(plugin_query_module.plugin_router_query_service, "query_plugins", _query_plugins)
    await plugin_query_module.handle_plugin_query(
        {"from_plugin": "p", "request_id": "3", "filters": {"k": 1}},
        send,
    )
    assert send.calls[-1][2] == {"plugins": [{"plugin_id": "a"}]}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_to_plugin_and_bus_get_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Recorder()

    async def _trigger_custom_event(**kwargs) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(p2p_module.plugin_dispatch_service, "trigger_custom_event", _trigger_custom_event)
    await p2p_module.handle_plugin_to_plugin(
        {
            "from_plugin": "p",
            "request_id": "1",
            "to_plugin": "q",
            "event_type": "custom",
            "event_id": "run",
            "args": {"x": 1},
        },
        send,
    )
    assert send.calls[-1][2] == {"ok": True}

    await p2p_module.handle_plugin_to_plugin(
        {"from_plugin": "p", "request_id": "2", "event_type": "custom", "event_id": "run"},
        send,
    )
    assert send.calls[-1][3] == "to_plugin is required"

    async def _events(**kwargs) -> list[dict[str, object]]:
        return [{"id": "e1"}]

    async def _lifecycle(**kwargs) -> list[dict[str, object]]:
        return [{"id": "l1"}]

    monkeypatch.setattr(events_module.bus_query_service, "get_events", _events)
    monkeypatch.setattr(lifecycle_module.bus_query_service, "get_lifecycle", _lifecycle)

    await events_module.handle_event_get(
        {"from_plugin": "p", "request_id": "3", "strict": "false", "plugin_id": "*"},
        send,
    )
    assert send.calls[-1][2] == {"plugin_id": "*", "events": [{"id": "e1"}]}

    await lifecycle_module.handle_lifecycle_get(
        {"from_plugin": "p", "request_id": "4", "plugin_id": "*", "filter": {"x": 1}},
        send,
    )
    assert send.calls[-1][2] == {"plugin_id": "*", "events": [{"id": "l1"}]}


@pytest.mark.plugin_unit
def test_registry_build_request_handlers_and_messages_module() -> None:
    handlers = registry_module.build_request_handlers()
    required = {
        "PLUGIN_TO_PLUGIN",
        "PLUGIN_QUERY",
        "PLUGIN_CONFIG_GET",
        "PLUGIN_SYSTEM_CONFIG_GET",
        "MEMORY_QUERY",
        "USER_CONTEXT_GET",
        "EXPORT_PUSH",
        "RUN_UPDATE",
        "EVENT_GET",
        "LIFECYCLE_GET",
        "BUS_SUBSCRIBE",
        "BUS_UNSUBSCRIBE",
    }
    assert required.issubset(set(handlers.keys()))

    import plugin.server.requests.messages as messages_module

    assert messages_module is not None
