from __future__ import annotations

import asyncio
import copy

import pytest

from plugin.server import lifecycle as module


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_ensure_plugin_messaging_started_initializes_response_map_and_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _State:
        @property
        def plugin_response_map(self) -> dict[str, object]:
            calls.append("response_map")
            return {}

    async def _start_router() -> None:
        calls.append("router_start")

    monkeypatch.setattr(module, "state", _State())
    monkeypatch.setattr(module.plugin_router, "start", _start_router)

    ensure = getattr(module, "ensure_plugin_messaging_started", None)
    assert callable(ensure)

    await ensure()

    assert calls == ["response_map", "router_start"]


@pytest.mark.asyncio
async def test_ensure_plugin_messaging_started_starts_router_when_response_map_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _State:
        @property
        def plugin_response_map(self) -> dict[str, object]:
            calls.append("response_map")
            raise RuntimeError("response map unavailable")

    async def _start_router() -> None:
        calls.append("router_start")

    warnings: list[tuple[str, str, str]] = []

    class _Logger:
        def warning(self, message: str, err_type: str, err: str) -> None:
            warnings.append((message, err_type, err))

        def debug(self, *_args: object, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(module, "state", _State())
    monkeypatch.setattr(module.plugin_router, "start", _start_router)
    monkeypatch.setattr(module, "logger", _Logger())

    await module.ensure_plugin_messaging_started()

    assert calls == ["response_map", "router_start"]
    assert warnings == [
        (
            "failed to initialize plugin response map early: err_type={}, err={}",
            "RuntimeError",
            "response map unavailable",
        )
    ]


@pytest.mark.asyncio
async def test_startup_uses_registry_refresh_then_autostart(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    calls: list[tuple[str, str]] = []

    async def _noop_async(*args, **kwargs):
        return None

    try:
        service = module.ServerLifecycleService()

        monkeypatch.setattr(module.ServerLifecycleService, "_clear_runtime_state", staticmethod(lambda: None))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)
        monkeypatch.setattr(module.plugin_router, "start", _noop_async)
        monkeypatch.setattr(service, "_start_message_plane", _noop_async)
        monkeypatch.setattr(module.bus_subscription_manager, "start", _noop_async)
        monkeypatch.setattr(module.status_manager, "start_status_consumer", _noop_async)
        monkeypatch.setattr(module.metrics_collector, "start", _noop_async)
        monkeypatch.setattr(module, "start_bridge", lambda: None)
        monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)

        async def _retry_deferred_profile_cleanup() -> int:
            calls.append(("profile_cleanup", "retry"))
            return 0

        monkeypatch.setattr(
            service._plugin_lifecycle_service,
            "retry_deferred_profile_cleanup",
            _retry_deferred_profile_cleanup,
        )

        async def _refresh_registry() -> dict[str, object]:
            calls.append(("registry", "refresh"))
            with module.state.acquire_plugins_write_lock():
                module.state.plugins.clear()
                module.state.plugins.update(
                    {
                        "auto_plugin": {
                            "id": "auto_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": True,
                        },
                        "manual_plugin": {
                            "id": "manual_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": False,
                        },
                        "failed_plugin": {
                            "id": "failed_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": True,
                            "runtime_load_state": "failed",
                        },
                    }
                )
            return {"success": True, "added": ["auto_plugin"], "updated": [], "removed": [], "failed": []}

        async def _start_plugin(plugin_id: str, restore_state: bool = False, *, refresh_registry: bool = True) -> dict[str, object]:
            _ = restore_state
            calls.append(("start", f"{plugin_id}:{refresh_registry}"))
            return {"success": True, "plugin_id": plugin_id}

        monkeypatch.setattr(service._plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(service._plugin_lifecycle_service, "start_plugin", _start_plugin)

        async def _revoke_plugin_permissions(plugin_id: str) -> None:
            calls.append(("revoke", plugin_id))

        monkeypatch.setattr(
            service._plugin_lifecycle_service,
            "revoke_plugin_permissions",
            _revoke_plugin_permissions,
            raising=False,
        )

        await service.startup()

        assert calls == [
            ("profile_cleanup", "retry"),
            ("registry", "refresh"),
            ("revoke", "auto_plugin"),
            ("revoke", "failed_plugin"),
            ("revoke", "manual_plugin"),
            ("start", "auto_plugin:False"),
        ]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_startup_skips_autostart_when_permission_revoke_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    started: list[str] = []
    service = module.ServerLifecycleService()

    async def _refresh_registry() -> dict[str, object]:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(
                {
                    "blocked_plugin": {"id": "blocked_plugin", "type": "plugin"},
                    "ready_plugin": {"id": "ready_plugin", "type": "plugin"},
                }
            )
        return {"added": [], "updated": [], "removed": [], "failed": []}

    async def _revoke(plugin_id: str) -> bool:
        return plugin_id != "blocked_plugin"

    async def _list_autostart() -> list[str]:
        return ["blocked_plugin", "ready_plugin"]

    async def _start(plugin_id: str, **_kwargs: object) -> dict[str, object]:
        started.append(plugin_id)
        return {"success": True, "plugin_id": plugin_id}

    try:
        monkeypatch.setattr(
            service._plugin_registry_service,
            "refresh_registry",
            _refresh_registry,
        )
        monkeypatch.setattr(
            service._plugin_registry_service,
            "list_autostart_plugin_ids",
            _list_autostart,
        )
        monkeypatch.setattr(
            service._plugin_lifecycle_service,
            "revoke_plugin_permissions",
            _revoke,
        )
        monkeypatch.setattr(
            service._plugin_lifecycle_service,
            "start_plugin",
            _start,
        )

        await service._refresh_registry_and_start_autostart_plugins()

        assert started == ["ready_plugin"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)


@pytest.mark.asyncio
async def test_shutdown_hosts_revokes_permissions_even_when_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class _Host:
        async def start(self, _message_target_queue: object) -> None:
            return None

        async def shutdown(self, timeout: float) -> None:
            calls.append(("shutdown", str(timeout)))
            raise RuntimeError("shutdown failed")

    service = module.ServerLifecycleService()
    monkeypatch.setattr(
        service,
        "_get_plugin_hosts_snapshot",
        lambda: {"demo_plugin": _Host()},
    )

    async def _revoke_plugin_permissions(
        plugin_id: str,
        *,
        timeout: float = 3.0,
    ) -> None:
        calls.append(("revoke", f"{plugin_id}:{timeout}"))

    monkeypatch.setattr(
        service._plugin_lifecycle_service,
        "revoke_plugin_permissions",
        _revoke_plugin_permissions,
        raising=False,
    )

    assert await service._shutdown_hosts() is True
    assert calls[-1] == ("revoke", "demo_plugin:0.4")


@pytest.mark.asyncio
async def test_shutdown_hosts_revokes_permissions_for_invalid_stale_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[str] = []
    service = module.ServerLifecycleService()
    monkeypatch.setattr(
        service,
        "_get_plugin_hosts_snapshot",
        lambda: {"demo_plugin": object()},
    )

    async def _revoke_plugin_permissions(
        plugin_id: str,
        *,
        timeout: float = 3.0,
    ) -> bool:
        revoked.append(f"{plugin_id}:{timeout}")
        return True

    monkeypatch.setattr(
        service._plugin_lifecycle_service,
        "revoke_plugin_permissions",
        _revoke_plugin_permissions,
    )

    assert await service._shutdown_hosts() is False
    assert revoked == ["demo_plugin:0.4"]


@pytest.mark.asyncio
async def test_shutdown_timeout_still_retries_host_permission_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[str] = []
    service = module.ServerLifecycleService()

    async def _never_finishes() -> module._ShutdownResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def _revoke_plugin_permissions(
        plugin_id: str,
        *,
        timeout: float = 3.0,
    ) -> bool:
        revoked.append(f"{plugin_id}:{timeout}")
        return True

    monkeypatch.setattr(module, "PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", 0.01)
    monkeypatch.setattr(service, "_shutdown_internal", _never_finishes)
    monkeypatch.setattr(
        service,
        "_get_plugin_hosts_snapshot",
        lambda: {"demo_plugin": object()},
    )
    monkeypatch.setattr(
        service._plugin_lifecycle_service,
        "revoke_plugin_permissions",
        _revoke_plugin_permissions,
    )
    monkeypatch.setattr(module.state, "close_plugin_resources", lambda: None)

    await service.shutdown()

    assert revoked == ["demo_plugin:0.4"]
