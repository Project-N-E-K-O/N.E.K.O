from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugin.server.application.plugins import development as store
from plugin.server.application.plugins import development_service as service
from plugin.server.domain.errors import ServerDomainError


@pytest.fixture(autouse=True)
def isolated_development_store(monkeypatch, tmp_path):
    from plugin.server.infrastructure import autostart_approvals

    # The data root is per-test, but the approval cache is process-global.
    # Installation cases must not leave later discovery cases pending approval.
    monkeypatch.setattr(autostart_approvals, "_cache", set())
    monkeypatch.setattr(autostart_approvals, "_load_failed", False)
    roots = (tmp_path / "installed", tmp_path / "builtin")
    for root in roots:
        root.mkdir()
    monkeypatch.setattr(store.settings, "PLUGIN_CONFIG_ROOTS", roots)
    monkeypatch.setattr(store.settings, "BUILTIN_PLUGIN_CONFIG_ROOT", roots[1])
    monkeypatch.setattr(store.settings, "get_plugin_state_root", lambda: tmp_path / "state" / "plugins")
    from plugin.server.application.plugins import operation_lock
    monkeypatch.setattr(operation_lock, "_operation_file_lock_path", lambda: tmp_path / "operation.lock")
    from plugin.server.application.plugins import registry_service
    monkeypatch.setattr(registry_service, "PLUGIN_CONFIG_ROOTS", roots)
    monkeypatch.setattr(service.state, "plugins", {})
    monkeypatch.setattr(service.state, "plugin_hosts", {})
    monkeypatch.setattr(service.state, "event_handlers", {})


def _source(root: Path, name="demo", plugin_id="demo", entry=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.toml").write_text(
        '[plugin]\n' + f'id="{plugin_id}"\nname="Demo"\nversion="1.2.3"\n'
        + f'entry="{entry or f"plugins.{name}:Demo"}"\n', encoding="utf-8",
    )
    (directory / "__init__.py").write_text("class Demo: pass\n", encoding="utf-8")
    return directory


def _register(tmp_path):
    store.set_enabled_sync(True)
    return store.register_directory_sync(str(_source(tmp_path / "中文 developer folder")))


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["source", "development_ref"])
@pytest.mark.parametrize("action", ["start", "stop", "reload", "refresh"])
async def test_removed_registration_cannot_downgrade_cached_development(tmp_path, monkeypatch, marker, action):
    from fastapi import FastAPI
    import httpx
    from plugin.server.application.plugins import registry_service
    from plugin.server.routes import plugins as routes

    record = _register(tmp_path)
    registry = registry_service.PluginRegistryService()
    registry._refresh_plugin_sync(record.plugin_id)
    meta = service.state.plugins[record.plugin_id]
    meta.pop("development_ref" if marker == "source" else "source")
    # Another worker has removed the persisted association, leaving this cache.
    store.remove_registration_sync(record)
    ordinary_action = AsyncMock(return_value={"success": True})
    target = routes.registry_service if action == "refresh" else routes.lifecycle_service
    monkeypatch.setattr(target, f"{action}_plugin", ordinary_action)
    monkeypatch.setattr(routes, "ensure_plugin_messaging_started", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.168.1.2", 1234)),
                                 base_url="http://127.0.0.1") as http:
        response = await http.post(f"/plugin/{record.plugin_id}/{action}")
    assert response.status_code == 409, response.text
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
    ordinary_action.assert_not_awaited()
    # The registry's real external-path fallback must also preserve provenance.
    with pytest.raises(ServerDomainError) as failure:
        registry._refresh_plugin_sync(record.plugin_id)
    assert failure.value.code == "DEVELOPMENT_STALE"
    assert service.state.plugins[record.plugin_id] is meta
    assert (record.source_dir / "plugin.toml").exists()


def test_unregistered_ordinary_external_source_remains_refreshable(tmp_path):
    from plugin.server.application.plugins import registry_service

    source = _source(tmp_path / "legacy")
    service.state.plugins["demo"] = {"id": "demo", "config_path": str(source / "plugin.toml")}
    assert store.registration_for_plugin_sync("demo") is None
    registry_service.PluginRegistryService()._refresh_plugin_sync("demo")
    assert service.state.plugins["demo"].get("source") != "development"


@pytest.mark.asyncio
async def test_degraded_startup_error_survives_view_refresh_and_stop(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service

    record = _register(tmp_path)

    class WarnHost(_SourceHost):
        def __init__(self, **kwargs):
            super().__init__(kwargs["config_path"])

        async def start(self, **kwargs):
            assert kwargs["startup_failure"] == "warn"
            return {"startup_error": "startup hook failed"}

    monkeypatch.setattr(lifecycle_service, "PluginProcessHost", WarnHost)
    result = await lifecycle_service.PluginLifecycleService().start_plugin(record.plugin_id)
    assert result["startup_degraded"] is True
    service._record_runtime_failure_sync(record, None)
    for view in (service.registration_view_sync(record), service.development_view_sync()["registrations"][0]):
        assert view["error"] == "startup hook failed"
        assert view["runtime_alive"] is True
    host = service.state.plugin_hosts[record.plugin_id]
    result = await service.development_lifecycle_action(record.plugin_id, "stop", record.registration_id, record.revision)
    assert result["success"]
    assert host.stops == 1
    assert service.registration_view_sync(record)["runtime_alive"] is False
    updated = store.rebind_registration_sync(record, str(_source(tmp_path / "replacement")))
    assert service.registration_view_sync(updated)["error"] is None


@pytest.mark.asyncio
async def test_extra_registration_field_preserves_ordinary_startup(tmp_path, monkeypatch):
    _register(tmp_path)
    data = json.loads(store._store_path().read_text(encoding="utf-8"))
    data["registrations"][0]["future_setting"] = {"enabled": True}
    corruption = json.dumps(data)
    await test_corrupt_store_preserves_ordinary_startup_discovery(tmp_path, monkeypatch, corruption)


def test_extra_registration_field_is_rejected_before_mutation(tmp_path):
    record = _register(tmp_path)
    data = json.loads(store._store_path().read_text(encoding="utf-8"))
    data["registrations"][0]["future_setting"] = "private value"
    original = json.dumps(data)
    store._store_path().write_text(original, encoding="utf-8")
    for operation in (
        store.list_registration_records_sync,
        lambda: store.set_enabled_sync(False),
        lambda: store.remove_registration_sync(record),
    ):
        with pytest.raises(ServerDomainError) as error:
            operation()
        assert error.value.code == "DEVELOPMENT_STORE_INVALID"
        assert "private value" not in error.value.message
        assert store._store_path().read_text(encoding="utf-8") == original


class _SourceHost:
    def __init__(self, config_path):
        self.config_path = config_path
        self.stops = 0

    async def start(self, **kwargs):
        pass

    async def shutdown(self, **kwargs):
        self.stops += 1

    def is_alive(self):
        return self.stops == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path_kind", ["missing", "external", "invalid"])
async def test_unconfirmed_host_source_retains_development_association(tmp_path, path_kind):
    record = _register(tmp_path)
    paths = {"missing": None, "external": tmp_path / "other" / "plugin.toml", "invalid": object()}
    host = _SourceHost(paths[path_kind])
    service.state.plugin_hosts[record.plugin_id] = host
    with pytest.raises(ServerDomainError) as error:
        await service.remove_development(record.registration_id, record.revision)
    assert error.value.code == "DEVELOPMENT_STOP_FAILED"
    assert host.stops == 0
    assert service.state.plugin_hosts[record.plugin_id] is host
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
async def test_waiting_removal_rechecks_host_source_after_lock(tmp_path, monkeypatch):
    from plugin.server.application.plugins import lifecycle_service
    from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
    record = _register(tmp_path)
    service.state.plugin_hosts[record.plugin_id] = _SourceHost(record.source_dir / "plugin.toml")
    replacement = _SourceHost(store.settings.PLUGIN_CONFIG_ROOTS[0] / "demo" / "plugin.toml")
    entered, release = asyncio.Event(), asyncio.Event()
    @serialized_plugin_operation
    async def switch_source():
        entered.set()
        await release.wait()
        service.state.plugin_hosts[record.plugin_id] = replacement
    monkeypatch.setattr(lifecycle_service, "clear_plugin_llm_tools", AsyncMock())
    switch_task = asyncio.create_task(switch_source())
    await asyncio.wait_for(entered.wait(), 3)
    remove_task = asyncio.create_task(service.remove_development(record.registration_id, record.revision))
    try:
        await asyncio.sleep(0)
        assert not remove_task.done()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(switch_task, remove_task), 3)
    assert replacement.stops == 0
    assert service.state.plugin_hosts[record.plugin_id] is replacement
    assert store.list_registration_records_sync() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["remove", "disable", "stop", "rebind"])
async def test_stale_association_does_not_stop_installed_host(tmp_path, monkeypatch, operation):
    from plugin.server.application.plugins import lifecycle_service, registry_service
    record = _register(tmp_path)
    installed = _source(store.settings.PLUGIN_CONFIG_ROOTS[0])
    await registry_service.PluginRegistryService().refresh_registry()
    metadata = dict(service.state.plugins[record.plugin_id])
    assert Path(metadata["config_path"]) == installed / "plugin.toml"
    host = _SourceHost(installed / "plugin.toml")
    service.state.plugin_hosts[record.plugin_id] = host
    handler = object()
    service.state.event_handlers[record.plugin_id] = handler
    cleanup = AsyncMock()
    monkeypatch.setattr(lifecycle_service, "clear_plugin_llm_tools", cleanup)
    if operation == "remove":
        assert (await service.remove_development(record.registration_id, record.revision))["success"]
        assert store.list_registration_records_sync() == []
    elif operation == "disable":
        await service.set_development_enabled(False)
        assert not store.development_enabled_sync()
        assert len(store.list_registration_records_sync()) == 1
    elif operation == "stop":
        assert (await service.development_lifecycle_action(record.plugin_id, "stop", record.registration_id, record.revision))["success"]
    else:
        with pytest.raises(ServerDomainError) as error:
            await service.rebind_development(record.registration_id, record.revision, str(_source(tmp_path / "replacement")))
        assert error.value.code == "DEVELOPMENT_CONFLICT"
        assert store.list_registration_records_sync() == [record]
    assert host.stops == 0 and host.is_alive()
    assert service.state.plugin_hosts[record.plugin_id] is host
    assert service.state.event_handlers[record.plugin_id] is handler
    assert service.state.plugins[record.plugin_id]["config_path"] == metadata["config_path"]
    assert service.state.plugins[record.plugin_id].get("source") != "development"
    cleanup.assert_not_awaited()
    assert (record.source_dir / "plugin.toml").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_owned_development_host_is_stopped_even_with_missing_source(tmp_path, monkeypatch, missing):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    host = _SourceHost(record.source_dir / "plugin.toml")
    service.state.plugin_hosts[record.plugin_id] = host
    if missing:
        record.source_dir.rename(record.source_dir.with_name("moved"))
    monkeypatch.setattr(lifecycle_service, "clear_plugin_llm_tools", AsyncMock())
    assert (await service.remove_development(record.registration_id, record.revision))["success"]
    assert host.stops == 1
    assert record.plugin_id not in service.state.plugin_hosts
    assert store.list_registration_records_sync() == []


@pytest.mark.asyncio
async def test_restored_association_does_not_stop_ordinary_host_started_during_store_failure(tmp_path, monkeypatch):
    from plugin.server.application.plugins import lifecycle_service, registry_service
    from plugin.server.application.plugins.metadata_scanner import IsolatedPluginMetadata
    record = _register(tmp_path)
    saved = store._store_path().read_bytes()
    installed = _source(store.settings.PLUGIN_CONFIG_ROOTS[0])
    registry = registry_service.PluginRegistryService()
    lifecycle = lifecycle_service.PluginLifecycleService()
    await registry.refresh_registry()
    # Discovery preference alone does not bypass the valid-registration fence.
    with pytest.raises(ServerDomainError) as error:
        await lifecycle.start_plugin(record.plugin_id, refresh_registry=False)
    assert error.value.code == "DEVELOPMENT_CONFLICT"

    class Host(_SourceHost):
        def __init__(self, plugin_id, entry_point, config_path):
            super().__init__(config_path)
            self.started = False
            self.process = SimpleNamespace(is_alive=self.is_alive, exitcode=None)

        async def start(self, **kwargs):
            self.started = True

        def is_alive(self):
            return self.started and super().is_alive()

    monkeypatch.setattr(lifecycle_service, "PluginProcessHost", Host)
    monkeypatch.setattr(lifecycle_service, "scan_plugin_metadata_isolated", lambda **kwargs: IsolatedPluginMetadata(
        entries_preview=[], handlers={}, entry_methods={}))
    cleanup = AsyncMock()
    monkeypatch.setattr(lifecycle_service, "clear_plugin_llm_tools", cleanup)
    store._store_path().write_text("{", encoding="utf-8")
    await registry.refresh_registry()
    assert (await lifecycle.start_plugin(record.plugin_id, refresh_registry=False))["success"]
    host = service.state.plugin_hosts[record.plugin_id]
    assert host.is_alive() and host.config_path == installed / "plugin.toml"
    # Restoring the optional file must not grant the old association this host.
    store._store_path().write_bytes(saved)
    await service.remove_development(record.registration_id, record.revision)
    assert host.is_alive() and host.stops == 0
    assert service.state.plugin_hosts[record.plugin_id] is host
    cleanup.assert_not_awaited()
    assert store.list_registration_records_sync() == []


@pytest.mark.parametrize("single", [False, True])
def test_discovery_rejects_manifest_id_changed_between_reads(tmp_path, monkeypatch, single):
    from plugin.server.application.plugins import registry_service as registry
    record = _register(tmp_path)
    original_view = registry.registration_view_sync

    def replace_manifest_after_validation(registration):
        view = original_view(registration)
        manifest = registration.source_dir / "plugin.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('id="demo"', 'id="unexpected"'), encoding="utf-8")
        return view

    monkeypatch.setattr(registry, "registration_view_sync", replace_manifest_after_validation)
    manager = registry.PluginRegistryService()
    if single:
        manager._refresh_plugin_sync(record.plugin_id)
    else:
        manager._refresh_registry_sync()
    assert "unexpected" not in service.state.plugins
    meta = service.state.plugins[record.plugin_id]
    assert meta["runtime_load_state"] == "failed"
    assert "ID changed" in meta["runtime_load_error_message"]
    assert meta["development_ref"]["registration_id"] == record.registration_id


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ['{', '{"enabled":true,"registrations":{}}'])
async def test_corrupt_store_preserves_ordinary_startup_discovery(tmp_path, monkeypatch, corruption):
    from plugin.server.application.plugins import registry_service as registry
    from plugin.server.lifecycle import ServerLifecycleService
    source = _source(store.settings.PLUGIN_CONFIG_ROOTS[0], "ordinary", "ordinary")
    store._store_path().parent.mkdir(parents=True, exist_ok=True)
    store._store_path().write_text(corruption, encoding="utf-8")
    manager = registry.PluginRegistryService()
    result = await manager.refresh_registry()
    assert "ordinary" in service.state.plugins
    assert result["failed"] and "Cannot read development registrations" in result["failed"][0]["error"]
    assert store.registration_for_plugin_sync("ordinary") is None
    assert "ordinary" in await manager.list_autostart_plugin_ids()
    # Execute startup orchestration with real discovery and autostart selection.
    started = []
    async def start(plugin_id, **kwargs):
        assert store.registration_for_plugin_sync(plugin_id) is None
        started.append(plugin_id)
    lifecycle = ServerLifecycleService()
    lifecycle._plugin_registry_service = manager
    lifecycle._plugin_lifecycle_service = SimpleNamespace(start_plugin=start)
    await lifecycle._refresh_registry_and_start_autostart_plugins()
    assert started == ["ordinary"]
    assert store._store_path().read_text(encoding="utf-8") == corruption
    assert (source / "plugin.toml").exists()
    with pytest.raises(ServerDomainError, match="Cannot read development registrations"):
        store.development_view_sync()


@pytest.mark.parametrize("kind", ["external", "unknown", "development"])
def test_corrupt_store_does_not_downgrade_untrusted_sources(tmp_path, monkeypatch, kind):
    store._store_path().parent.mkdir(parents=True, exist_ok=True)
    store._store_path().write_text('{', encoding="utf-8")
    if kind != "unknown":
        root = tmp_path / "external" if kind == "external" else store.settings.PLUGIN_CONFIG_ROOTS[0]
        source = _source(root)
        monkeypatch.setitem(service.state.plugins, "demo", {"config_path": str(source / "plugin.toml"), "source": kind})
    with pytest.raises(ServerDomainError) as error:
        store.registration_for_plugin_sync("demo")
    assert error.value.code == "DEVELOPMENT_STORE_INVALID"


@pytest.mark.asyncio
async def test_ordinary_lifecycle_can_start_after_corrupt_development_store(tmp_path, monkeypatch):
    from plugin.server.application.plugins import lifecycle_service as lifecycle, registry_service as registry
    from plugin.server.application.plugins.metadata_scanner import IsolatedPluginMetadata
    _source(store.settings.PLUGIN_CONFIG_ROOTS[0], "ordinary", "ordinary")
    store._store_path().parent.mkdir(parents=True, exist_ok=True)
    store._store_path().write_text('{', encoding="utf-8")
    await registry.PluginRegistryService().refresh_registry()
    hosts = []

    class Host:
        def __init__(self, plugin_id, entry_point, config_path):
            self.process = SimpleNamespace(is_alive=lambda: True, exitcode=None)
            self.started = False
            hosts.append(self)

        async def start(self, message_target_queue, startup_timeout=None):
            self.started = True

        async def shutdown(self, timeout=None):
            pass

        def is_alive(self):
            return self.started

    monkeypatch.setattr(lifecycle, "PluginProcessHost", Host)
    monkeypatch.setattr(lifecycle, "scan_plugin_metadata_isolated", lambda **kwargs: IsolatedPluginMetadata(
        entries_preview=[], handlers={}, entry_methods={}))
    result = await lifecycle.PluginLifecycleService().start_plugin("ordinary", refresh_registry=False)
    assert result["success"]
    assert hosts[0].started
    assert service.state.plugin_hosts["ordinary"] is hosts[0]


@pytest.mark.parametrize("failure", ["missing_directory", "invalid_manifest", "changed_id"])
def test_registration_keeps_live_process_visible_when_source_invalid(tmp_path, monkeypatch, failure):
    record = _register(tmp_path)
    alive = True
    monkeypatch.setitem(service.state.plugin_hosts, record.plugin_id, SimpleNamespace(is_alive=lambda: alive))
    if failure == "missing_directory":
        record.source_dir.rename(record.source_dir.with_name("moved"))
    elif failure == "invalid_manifest":
        (record.source_dir / "plugin.toml").write_text("[invalid", encoding="utf-8")
    else:
        manifest = record.source_dir / "plugin.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('id="demo"', 'id="changed"'), encoding="utf-8")
    view = service.development_view_sync()["registrations"][0]
    assert view["error"]
    assert view["runtime_alive"] is True
    assert view["revision"] == record.revision
    alive = False
    assert service.registration_view_sync(record)["runtime_alive"] is False


@pytest.mark.parametrize("count,shutdown,lock_wait", [(0, 1.5, 20), (20, 1.5, 20), (3, 300, 90)])
def test_disable_wait_estimate_covers_registered_stops(tmp_path, monkeypatch, count, shutdown, lock_wait):
    store.set_enabled_sync(True)
    monkeypatch.setattr(service, "PLUGIN_SHUTDOWN_TIMEOUT", shutdown)
    monkeypatch.setattr(service, "PROCESS_TERMINATE_TIMEOUT", 2.0)
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", str(lock_wait))
    for index in range(count):
        plugin_id = f"demo_{index}"
        store.register_directory_sync(str(_source(tmp_path, plugin_id, plugin_id)))
    # Stopped and missing-source registrations still count: they may retain a host.
    if count:
        (tmp_path / "demo_0").rename(tmp_path / "moved")
    result = service.development_view_sync()
    assert len(result["registrations"]) == count
    assert 300_000 <= result["disable_timeout_ms"] <= 2_147_483_647
    assert result["disable_timeout_ms"] > 1000 * (lock_wait + count * (shutdown + 4.0))
    assert store.development_enabled_sync()


def test_registration_is_persistent_idempotent_and_does_not_write_source(tmp_path):
    source = _source(tmp_path / "中文 developer folder")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    with pytest.raises(ServerDomainError) as error:
        store.register_directory_sync(str(source))
    assert error.value.code == "DEVELOPMENT_DISABLED"
    store.set_enabled_sync(True)
    record = store.register_directory_sync(str(source))
    assert store.register_directory_sync(str(source / ".")) == record
    assert store.list_registration_records_sync() == [record]
    persisted = json.loads(store._store_path().read_text(encoding="utf-8"))
    assert persisted["registrations"] == [{"registration_id": record.registration_id, "revision": 1,
                                          "plugin_id": "demo", "source_dir": str(source.resolve())}]
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert store.development_view_sync()["registrations"][0]["name"] == "Demo"


@pytest.mark.parametrize("problem", ["missing_manifest", "invalid_toml", "wrong_directory", "relative", "managed"])
def test_invalid_directories_are_rejected_without_partial_registration(tmp_path, problem):
    store.set_enabled_sync(True)
    source = _source(tmp_path / "external")
    if problem == "missing_manifest":
        (source / "plugin.toml").unlink()
    elif problem == "invalid_toml":
        (source / "plugin.toml").write_text("[broken", encoding="utf-8")
    elif problem == "wrong_directory":
        source = _source(tmp_path / "mismatch", entry="plugins.other:Demo")
    elif problem == "relative":
        source = Path("relative")
    else:
        source = _source(store.settings.PLUGIN_CONFIG_ROOTS[0])
    with pytest.raises(ServerDomainError) as error:
        store.register_directory_sync(str(source))
    assert error.value.code == "DEVELOPMENT_INVALID"
    assert store.list_registration_records_sync() == []


@pytest.mark.parametrize("installed", [False, True])
def test_same_id_conflicts_report_source_and_preserve_registration(tmp_path, installed):
    existing = _register(tmp_path)
    conflicting = _source(store.settings.PLUGIN_CONFIG_ROOTS[0] if installed else tmp_path / "second")
    with pytest.raises(ServerDomainError) as error:
        if installed:
            store.resolve_development_ref_sync(existing.registration_id, existing.revision)
        else:
            store.register_directory_sync(str(conflicting))
    assert error.value.code == "DEVELOPMENT_CONFLICT"
    assert str(conflicting if installed else existing.source_dir) in error.value.message
    assert store.list_registration_records_sync() == [existing]


@pytest.mark.parametrize("owner", ["broken_manifest", "external_registry", "host_only"])
def test_new_registration_cannot_claim_runtime_id(tmp_path, owner):
    store.set_enabled_sync(True)
    source = _source(tmp_path / "development")
    if owner == "host_only":
        host = SimpleNamespace(is_alive=lambda: True)
        service.state.plugin_hosts["demo"] = host
    else:
        existing = _source(store.settings.PLUGIN_CONFIG_ROOTS[0] if owner == "broken_manifest" else tmp_path / "external")
        service.state.plugins["demo"] = {"config_path": str(existing / "plugin.toml"), "source": "user"}
        if owner == "broken_manifest":
            (existing / "plugin.toml").write_text("[invalid", encoding="utf-8")
    before = store._store_path().read_bytes()
    for operation in (store.inspect_directory_sync, store.register_directory_sync):
        with pytest.raises(ServerDomainError) as error:
            operation(str(source))
        assert error.value.code == "DEVELOPMENT_CONFLICT"
        assert store._store_path().read_bytes() == before
    assert store.registration_for_plugin_sync("demo") is None
    if owner == "host_only":
        assert service.state.plugin_hosts["demo"] is host
    else:
        assert service.state.plugins["demo"]["config_path"] == str(existing / "plugin.toml")


@pytest.mark.parametrize("entry", [
    "plugins.my-plugin.bad-name:Demo",
    "plugins.my-plugin.123child:Demo",
    "plugins.my-plugin:Bad-Class",
])
def test_package_id_relaxation_keeps_submodule_and_class_validation(tmp_path, entry):
    store.set_enabled_sync(True)
    source = _source(tmp_path, name="my-plugin", plugin_id="my-plugin", entry=entry)
    for name in ("bad-name.py", "123child.py"):
        (source / name).write_text("class Demo: pass\n", encoding="utf-8")
    with pytest.raises(ServerDomainError) as error:
        store.register_directory_sync(str(source))
    assert error.value.code == "DEVELOPMENT_INVALID"
    assert store.list_registration_records_sync() == []


def test_missing_directory_remains_visible_and_rebind_fences_old_operations(tmp_path):
    record = _register(tmp_path)
    moved = record.source_dir.with_name("moved")
    record.source_dir.rename(moved)
    view = store.development_view_sync()["registrations"][0]
    assert view["registration_id"] == record.registration_id
    assert view["error"]
    replacement = _source(tmp_path / "replacement")
    current = store.rebind_registration_sync(record, str(replacement))
    assert current.registration_id == record.registration_id
    assert current.revision == record.revision + 1
    assert store.registration_view_sync(current)["error"] is None
    for operation in (store.require_registration_sync, store.resolve_development_ref_sync):
        with pytest.raises(ServerDomainError) as error:
            operation(record.registration_id, record.revision)
        assert error.value.code == "DEVELOPMENT_STALE"
    with pytest.raises(ServerDomainError):
        store.remove_registration_sync(record)
    assert store.list_registration_records_sync() == [current]


def test_manifest_id_change_requires_new_registration(tmp_path):
    record = _register(tmp_path)
    _source(record.source_dir.parent, plugin_id="changed")
    assert "ID changed" in store.registration_view_sync(record)["error"]
    with pytest.raises(ServerDomainError):
        store.register_directory_sync(str(record.source_dir))
    assert store.list_registration_records_sync() == [record]


def test_atomic_replace_failure_preserves_previous_file_and_cleans_temp(monkeypatch, tmp_path):
    record = _register(tmp_path)
    previous = store._store_path().read_bytes()
    def fail_replace(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        store.remove_registration_sync(record)
    assert store._store_path().read_bytes() == previous
    assert list(store._store_path().parent.glob(".plugin-development-*")) == []


def test_corrupt_store_is_not_silently_overwritten(tmp_path):
    _register(tmp_path)
    store._store_path().write_text('{"enabled":true,"registrations":[{}]}', encoding="utf-8")
    before = store._store_path().read_bytes()
    with pytest.raises(ServerDomainError) as error:
        store.set_enabled_sync(False)
    assert error.value.code == "DEVELOPMENT_STORE_INVALID"
    assert store._store_path().read_bytes() == before


def test_mode_switch_retains_records_and_invalidates_snapshots(tmp_path):
    record = _register(tmp_path)
    store.set_enabled_sync(False)
    disabled = store.list_registration_records_sync()[0]
    assert disabled.plugin_id == record.plugin_id
    assert disabled.revision == record.revision + 1
    assert store.list_development_snapshots_sync() == []
    with pytest.raises(ServerDomainError) as error:
        store.resolve_development_ref_sync(disabled.registration_id, disabled.revision)
    assert error.value.code == "DEVELOPMENT_DISABLED"
    store.set_enabled_sync(True)
    current = store.list_registration_records_sync()[0]
    assert current.revision == disabled.revision + 1
    with pytest.raises(ServerDomainError):
        store.validate_development_snapshot_sync(record)
    with store.development_snapshot_guard_sync([current]):
        assert store.resolve_development_ref_sync(current.registration_id, current.revision) == current
    with pytest.raises(ServerDomainError):
        store.validate_development_snapshot_sync(replace(current, source_dir=tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["remove", "disable", "rebind"])
async def test_stop_failure_retains_association_and_mode(monkeypatch, tmp_path, operation):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    host = _SourceHost(record.source_dir / "plugin.toml")
    monkeypatch.setattr(lifecycle_service, "_get_plugin_host_sync", lambda plugin_id: host)
    stop = AsyncMock(return_value={"success": False})
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    with pytest.raises(ServerDomainError) as error:
        if operation == "remove":
            await service.remove_development(record.registration_id, record.revision)
        elif operation == "disable":
            await service.set_development_enabled(False)
        else:
            await service.rebind_development(record.registration_id, record.revision, str(_source(tmp_path / "replacement")))
    assert error.value.code == "DEVELOPMENT_STOP_FAILED"
    stop.assert_awaited_once_with(record.plugin_id)
    assert store.list_registration_records_sync() == [record]
    assert store.development_enabled_sync()


@pytest.mark.asyncio
async def test_remove_keeps_source_and_running_data(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    data = store.settings.get_plugin_state_root() / record.plugin_id / "data" / "keep.txt"
    data.parent.mkdir(parents=True)
    data.write_text("retained", encoding="utf-8")
    monkeypatch.setattr(lifecycle_service, "_get_plugin_host_sync", lambda plugin_id: None)
    result = await service.remove_development(record.registration_id, record.revision)
    assert result["success"]
    assert store.list_registration_records_sync() == []
    assert (record.source_dir / "plugin.toml").is_file()
    assert data.read_text(encoding="utf-8") == "retained"


@pytest.mark.asyncio
async def test_start_failure_retains_registered_card(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service, registry_service
    store.set_enabled_sync(True)
    monkeypatch.setattr(registry_service.PluginRegistryService, "refresh_plugin", AsyncMock())
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "start_plugin", AsyncMock(side_effect=RuntimeError("broken startup")))
    result = await service.register_development(str(_source(tmp_path / "external")))
    assert result["error"] == "broken startup"
    assert store.list_registration_records_sync()[0].registration_id == result["registration_id"]
    assert service.development_view_sync()["registrations"][0]["error"] == "broken startup"


@pytest.mark.asyncio
async def test_remove_waits_for_inflight_reload_and_rejects_late_action(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def reload_plugin(self, plugin_id):
        entered.set()
        await release.wait()
        assert store.registration_for_plugin_sync(plugin_id) == record
        return {"success": True}
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "reload_plugin", reload_plugin)
    monkeypatch.setattr(lifecycle_service, "_get_plugin_host_sync", lambda plugin_id: None)
    reload_task = asyncio.create_task(service.development_lifecycle_action(record.plugin_id, "reload", record.registration_id, record.revision))
    await asyncio.wait_for(entered.wait(), 3)
    remove_task = asyncio.create_task(service.remove_development(record.registration_id, record.revision))
    await asyncio.sleep(0)
    assert not remove_task.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(reload_task, remove_task), 3)
    assert store.list_registration_records_sync() == []
    with pytest.raises(ServerDomainError) as error:
        await service.development_lifecycle_action(record.plugin_id, "start", record.registration_id, record.revision)
    assert error.value.code == "DEVELOPMENT_STALE"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
async def test_waiting_remove_is_bounded_and_does_not_mutate_registration(monkeypatch, tmp_path, cancel):
    from plugin.server.application.plugins import lifecycle_service
    from plugin.server.application.plugins.operation_lock import bounded_operation_wait, PluginOperationBusy
    record = _register(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def reload_plugin(self, plugin_id):
        entered.set()
        await release.wait()
        return {"success": True}
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "reload_plugin", reload_plugin)
    monkeypatch.setattr(lifecycle_service, "_get_plugin_host_sync", lambda plugin_id: None)
    task = asyncio.create_task(service.development_lifecycle_action(record.plugin_id, "reload", record.registration_id, record.revision))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        if cancel:
            waiting = asyncio.create_task(service.remove_development(record.registration_id, record.revision))
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        else:
            with bounded_operation_wait(0.01), pytest.raises(PluginOperationBusy):
                await service.remove_development(record.registration_id, record.revision)
        assert store.list_registration_records_sync() == [record]
    finally:
        release.set()
        await asyncio.wait_for(task, 3)
    # A timed-out/cancelled waiter must not strand the lock for later requests.
    assert (await service.remove_development(record.registration_id, record.revision))["success"]


def test_preflight_reports_syntax_error_before_lifecycle_mutation(tmp_path):
    record = _register(tmp_path)
    (record.source_dir / "child.py").write_text("def invalid(\n", encoding="utf-8")
    with pytest.raises(ServerDomainError) as error:
        service.preflight_development_sync(record)
    assert "child.py" in error.value.message
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["syntax", "dependency"])
async def test_rebind_preflight_preserves_owned_host_and_registration(tmp_path, invalid):
    record = _register(tmp_path)
    replacement = _source(tmp_path / "replacement")
    if invalid == "syntax":
        (replacement / "child.py").write_text("def invalid(\n", encoding="utf-8")
    else:
        (replacement / "pyproject.toml").write_text(
            '[project]\ndependencies=["neko_missing_review_dependency_123"]\n', encoding="utf-8")
    host = _SourceHost(str(record.source_dir / "plugin.toml"))
    service.state.plugin_hosts[record.plugin_id] = host
    service.state.event_handlers[record.plugin_id] = {"sentinel": True}
    original = store._store_path().read_bytes()

    with pytest.raises(ServerDomainError) as error:
        await service.rebind_development(record.registration_id, record.revision, str(replacement))

    assert error.value.code == "DEVELOPMENT_INVALID"
    assert ("child.py" if invalid == "syntax" else "neko_missing_review_dependency_123") in error.value.message
    assert service.state.plugin_hosts[record.plugin_id] is host
    assert host.is_alive() and host.stops == 0
    assert service.state.event_handlers[record.plugin_id] == {"sentinel": True}
    assert store._store_path().read_bytes() == original
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
async def test_rebind_preflights_replacement_without_requiring_old_source(tmp_path):
    record = _register(tmp_path)
    replacement = _source(tmp_path / "replacement")
    # Preflight must compile without executing plugin code.
    (replacement / "__init__.py").write_text('raise RuntimeError("must not import")\nclass Demo: pass\n')
    host = _SourceHost(str(record.source_dir / "plugin.toml"))
    service.state.plugin_hosts[record.plugin_id] = host
    (record.source_dir / "plugin.toml").unlink()

    result = await service.rebind_development(record.registration_id, record.revision, str(replacement))

    assert result["revision"] == record.revision + 1
    assert result["source_dir"] == str(replacement.resolve())
    assert host.stops == 1
    assert record.plugin_id not in service.state.plugin_hosts
    assert store.list_registration_records_sync() == [replace(record, revision=2, source_dir=replacement.resolve())]


@pytest.mark.asyncio
async def test_rebind_without_host_can_repair_source_before_dependencies(tmp_path):
    record = _register(tmp_path)
    replacement = _source(tmp_path / "replacement")
    (replacement / "pyproject.toml").write_text(
        '[project]\ndependencies=["neko_missing_review_dependency_123"]\n', encoding="utf-8")
    (record.source_dir / "plugin.toml").unlink()
    result = await service.rebind_development(record.registration_id, record.revision, str(replacement))
    assert result["revision"] == record.revision + 1
    assert result["source_dir"] == str(replacement.resolve())
    assert record.plugin_id not in service.state.plugin_hosts


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["manifest", "syntax", "dependency"])
async def test_bulk_reload_preserves_invalid_development_and_reloads_healthy(monkeypatch, tmp_path, invalid):
    from plugin.server.application.plugins import lifecycle_service as lifecycle
    record = _register(tmp_path)
    healthy = store.register_directory_sync(str(_source(tmp_path / "healthy", "healthy", "healthy")))
    if invalid == "manifest":
        (record.source_dir / "plugin.toml").write_text("[invalid", encoding="utf-8")
    elif invalid == "syntax":
        (record.source_dir / "child.py").write_text("def invalid(\n", encoding="utf-8")
    else:
        (record.source_dir / "pyproject.toml").write_text(
            '[project]\ndependencies = ["neko_missing_review_dependency_123"]\n', encoding="utf-8",
        )
    running = {record.plugin_id, healthy.plugin_id, "ordinary"}
    stopped, started = [], []
    monkeypatch.setattr(lifecycle, "_list_running_plugin_ids_sync", lambda: sorted(running))
    monkeypatch.setattr(lifecycle.plugin_registry_service, "refresh_registry", AsyncMock())
    monkeypatch.setattr(lifecycle.plugin_registry_service, "order_plugin_ids", AsyncMock(side_effect=lambda ids: ids))
    async def stop(plugin_id, **kwargs):
        stopped.append(plugin_id)
        running.remove(plugin_id)
        return lifecycle._ReloadOutcome(plugin_id=plugin_id, success=True)
    async def start(plugin_id, **kwargs):
        started.append(plugin_id)
        running.add(plugin_id)
        return lifecycle._ReloadOutcome(plugin_id=plugin_id, success=True)
    manager = lifecycle.PluginLifecycleService()
    monkeypatch.setattr(manager, "_safe_stop_for_reload", stop)
    monkeypatch.setattr(manager, "_safe_start_for_reload", start)
    result = await manager.reload_all_plugins()
    assert record.plugin_id not in stopped
    assert record.plugin_id not in started
    assert record.plugin_id in running
    assert set(result["reloaded"]) == {"healthy", "ordinary"}
    assert [item["plugin_id"] for item in result["failed"]] == [record.plugin_id]
    assert not result["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rebind_after_preflight", [False, True])
async def test_bulk_reload_preflights_once_and_fences_snapshot(monkeypatch, tmp_path, rebind_after_preflight):
    from plugin.server.application.plugins import lifecycle_service as lifecycle

    record = _register(tmp_path)
    monkeypatch.setattr(lifecycle, "_list_running_plugin_ids_sync", lambda: [record.plugin_id])
    monkeypatch.setattr(lifecycle.plugin_registry_service, "refresh_registry", AsyncMock())
    monkeypatch.setattr(lifecycle.plugin_registry_service, "order_plugin_ids", AsyncMock(side_effect=lambda ids: ids))
    original = service.preflight_development_sync
    calls = []

    def preflight(snapshot):
        calls.append(snapshot)
        original(snapshot)
        if rebind_after_preflight:
            store.rebind_registration_sync(snapshot, str(snapshot.source_dir))

    monkeypatch.setattr(service, "preflight_development_sync", preflight)
    manager = lifecycle.PluginLifecycleService()
    stop, start = AsyncMock(), AsyncMock()
    monkeypatch.setattr(manager, "stop_plugin", stop)
    monkeypatch.setattr(manager, "start_plugin", start)
    result = await manager.reload_all_plugins()
    assert calls == [record]
    if rebind_after_preflight:
        stop.assert_not_awaited()
        start.assert_not_awaited()
        assert not result["success"]
    else:
        stop.assert_awaited_once()
        start.assert_awaited_once()
        assert result["reloaded"] == [record.plugin_id]


@pytest.mark.asyncio
async def test_bundle_package_id_does_not_conflict_with_development_plugin(monkeypatch, tmp_path):
    from plugin.neko_plugin_cli.public.build import build_bundle
    from plugin.server.application.plugin_cli import service as cli
    from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
    record = _register(tmp_path)
    incoming = _source(tmp_path / "incoming", "other", "other")
    second = _source(tmp_path / "incoming", "second", "second")
    packages = tmp_path / "packages"
    packages.mkdir()
    package = packages / "incoming.neko-bundle"
    await asyncio.to_thread(build_bundle, [incoming, second], package, bundle_id=record.plugin_id)
    policy = PluginCliPathPolicy(store.settings.PLUGIN_CONFIG_ROOTS[1], store.settings.PLUGIN_CONFIG_ROOTS[0],
                                 packages, tmp_path / "profiles", store.settings.get_plugin_state_root())
    monkeypatch.setattr(cli.PluginCliService, "_path_policy", staticmethod(lambda: policy))
    monkeypatch.setattr(cli, "get_install_source_manager", lambda: SimpleNamespace())
    plan = await cli.PluginCliService().plan_install(package=str(package))
    assert plan["action"] != "blocked", plan
    assert set(plan["bundle_plugin_ids"]) == {"other", "second"}


def test_preflight_prunes_dependency_directories_before_visiting(monkeypatch, tmp_path):
    import os
    record = _register(tmp_path)
    excluded = {"vendor", ".venv", ".git", "__pycache__"}
    for name in excluded:
        directory = record.source_dir / name / "deep"
        directory.mkdir(parents=True)
        (directory / "invalid.py").write_text("broken syntax !", encoding="utf-8")
    original = os.scandir
    def checked_scandir(path):
        relative = Path(path).relative_to(record.source_dir) if Path(path).is_relative_to(record.source_dir) else Path()
        assert not excluded.intersection(relative.parts), f"Visited excluded directory: {relative}"
        return original(path)
    monkeypatch.setattr(os, "scandir", checked_scandir)
    service.preflight_development_sync(record)


def test_preflight_still_checks_runtime_source_excluded_from_packaging(tmp_path):
    record = _register(tmp_path)
    (record.source_dir / "pyproject.toml").write_text('[tool.neko.build]\nexclude = ["generated/**"]\n', encoding="utf-8")
    generated = record.source_dir / "generated"
    generated.mkdir()
    (generated / "module.py").write_text("broken syntax !", encoding="utf-8")
    with pytest.raises(ServerDomainError, match="invalid syntax"):
        service.preflight_development_sync(record)


@pytest.mark.asyncio
async def test_bulk_reload_rechecks_registration_after_stop(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service as lifecycle
    record = _register(tmp_path)
    replacement = _source(tmp_path / "replacement")
    monkeypatch.setattr(lifecycle, "_list_running_plugin_ids_sync", lambda: [record.plugin_id])
    monkeypatch.setattr(lifecycle.plugin_registry_service, "refresh_registry", AsyncMock())
    monkeypatch.setattr(lifecycle.plugin_registry_service, "order_plugin_ids", AsyncMock(side_effect=lambda ids: ids))
    async def stop(plugin_id, **kwargs):
        store.rebind_registration_sync(record, str(replacement))
        return lifecycle._ReloadOutcome(plugin_id=plugin_id, success=True)
    manager = lifecycle.PluginLifecycleService()
    monkeypatch.setattr(manager, "_safe_stop_for_reload", stop)
    start = AsyncMock()
    monkeypatch.setattr(manager, "_safe_start_for_reload", start)
    result = await manager.reload_all_plugins()
    start.assert_not_awaited()
    assert not result["success"]
    assert result["failed"][0]["plugin_id"] == record.plugin_id


@pytest.mark.parametrize("missing", [False, True], ids=["disabled", "missing"])
def test_registry_refresh_preserves_development_identity_and_no_autostart(tmp_path, missing):
    from plugin.server.application.plugins import registry_service
    record = _register(tmp_path)
    if missing:
        record.source_dir.rename(record.source_dir.with_name("moved"))
    else:
        store.set_enabled_sync(False)
        record = store.list_registration_records_sync()[0]
    registry_service.PluginRegistryService()._refresh_registry_sync()
    meta = service.state.plugins[record.plugin_id]
    assert meta["source"] == "development"
    assert meta["development_ref"] == {"registration_id": record.registration_id, "revision": record.revision}
    assert meta["runtime_auto_start"] is False
    if missing:
        assert meta["runtime_source_missing"] is True
        assert meta["runtime_load_state"] == "failed"
    assert record.plugin_id not in registry_service._get_autostart_plugin_ids_sync()
    registry_service.PluginRegistryService()._refresh_plugin_sync(record.plugin_id)
    assert service.state.plugins[record.plugin_id]["source"] == "development"


@pytest.mark.asyncio
async def test_reload_preflight_failure_does_not_stop_old_instance(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    (record.source_dir / "child.py").write_text("broken syntax !\n", encoding="utf-8")
    stop = AsyncMock()
    start = AsyncMock()
    monkeypatch.setattr(lifecycle_service, "_plugin_is_running_sync", lambda plugin_id: True)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "start_plugin", start)
    with pytest.raises(ServerDomainError):
        await lifecycle_service.PluginLifecycleService().reload_plugin(record.plugin_id)
    stop.assert_not_called()
    start.assert_not_called()


@pytest.mark.asyncio
async def test_reload_reads_new_entry_before_stop_and_restarts(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    _source(record.source_dir.parent, entry="plugins.demo.changed:Demo")
    (record.source_dir / "changed.py").write_text("class Demo: pass\n", encoding="utf-8")
    order = []
    async def stop(self, plugin_id, **kwargs):
        order.append("stop")
        return {"success": True}
    async def start(self, plugin_id, **kwargs):
        order.append("start")
        assert store.validate_directory_sync(record.source_dir)["entry"] == "plugins.demo.changed:Demo"
        return {"success": True}
    monkeypatch.setattr(lifecycle_service, "_plugin_is_running_sync", lambda plugin_id: True)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "start_plugin", start)
    assert (await lifecycle_service.PluginLifecycleService().reload_plugin(record.plugin_id))["success"]
    assert order == ["stop", "start"]


@pytest.mark.asyncio
async def test_normal_delete_cannot_delete_development_source(tmp_path):
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    record = _register(tmp_path)
    with pytest.raises(ServerDomainError) as error:
        await PluginLifecycleService().delete_plugin(record.plugin_id)
    assert error.value.status_code == 409
    assert (record.source_dir / "plugin.toml").is_file()
    assert store.list_registration_records_sync() == [record]


def test_runtime_error_overlay_is_fenced_and_survives_registry_refresh(tmp_path):
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    record = _register(tmp_path)
    PluginRegistryService()._refresh_plugin_sync(record.plugin_id)
    service._record_runtime_failure_sync(record, RuntimeError("failed start"))
    PluginRegistryService()._refresh_plugin_sync(record.plugin_id)
    assert service.development_view_sync()["registrations"][0]["error"] == "failed start"
    service._record_runtime_failure_sync(record, None)
    assert service.development_view_sync()["registrations"][0]["error"] is None
    updated = store.rebind_registration_sync(record, str(_source(tmp_path / "new-source")))
    service._record_runtime_failure_sync(record, RuntimeError("late old failure"))
    assert service.registration_view_sync(updated)["error"] is None


@pytest.mark.asyncio
async def test_start_integrates_source_policy_and_does_not_write_metadata_to_source(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    before = {str(p.relative_to(record.source_dir)): p.read_bytes() for p in record.source_dir.rglob("*") if p.is_file()}
    calls = {}
    class Host:
        def __init__(self, **kwargs):
            calls["host"] = kwargs
            self.process = SimpleNamespace(is_alive=lambda: True, exitcode=None)
        async def start(self, message_target_queue, startup_timeout=None, startup_failure=None):
            calls["started"] = True
        async def shutdown(self, timeout=None):
            pass
        def is_alive(self):
            return True
    scanner = lifecycle_service.scan_plugin_metadata_isolated
    def scan(**kwargs):
        calls["scanner"] = kwargs
        return scanner(**kwargs)
    def forbid_packaged_metadata(*args, **kwargs):
        raise AssertionError("Development plugins must scan their current source")
    monkeypatch.setattr(lifecycle_service, "PluginProcessHost", Host)
    monkeypatch.setattr(lifecycle_service, "scan_plugin_metadata_isolated", scan)
    monkeypatch.setattr(lifecycle_service, "_read_packaged_isolated_metadata", forbid_packaged_metadata)
    result = await lifecycle_service.PluginLifecycleService().start_plugin(record.plugin_id)
    assert result["success"] and calls["started"]
    assert calls["host"]["source_only"] is True
    assert calls["scanner"]["source_only"] is True
    assert service.state.plugins[record.plugin_id]["source"] == "development"
    assert {str(p.relative_to(record.source_dir)): p.read_bytes() for p in record.source_dir.rglob("*") if p.is_file()} == before


@pytest.mark.asyncio
async def test_rebind_known_conflict_does_not_stop_current_instance(monkeypatch, tmp_path):
    from plugin.server.application.plugins import lifecycle_service
    record = _register(tmp_path)
    _source(store.settings.PLUGIN_CONFIG_ROOTS[0])
    replacement = _source(tmp_path / "replacement")
    stop = AsyncMock()
    monkeypatch.setattr(lifecycle_service, "_get_plugin_host_sync", lambda plugin_id: object())
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    with pytest.raises(ServerDomainError) as error:
        await service.rebind_development(record.registration_id, record.revision, str(replacement))
    assert error.value.code == "DEVELOPMENT_CONFLICT"
    stop.assert_not_called()
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle", [False, True], ids=["single", "bundle"])
async def test_install_package_cannot_claim_registered_development_id(monkeypatch, tmp_path, bundle):
    from plugin.neko_plugin_cli.public.build import build_plugin, build_bundle
    from plugin.server.application.plugin_cli import service as cli
    from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
    record = _register(tmp_path)
    incoming = _source(tmp_path / "incoming")
    packages = tmp_path / "packages"
    packages.mkdir()
    package = packages / ("incoming.neko-bundle" if bundle else "incoming.neko-plugin")
    if bundle:
        second = _source(tmp_path / "incoming", "other", "other")
        await asyncio.to_thread(build_bundle, [incoming, second], package, bundle_id="different_bundle_id")
    else:
        await asyncio.to_thread(build_plugin, incoming, package)
    # Disabled or temporarily missing development sources still own their identity.
    store.set_enabled_sync(False)
    record.source_dir.rename(record.source_dir.with_name("moved"))
    policy = PluginCliPathPolicy(store.settings.PLUGIN_CONFIG_ROOTS[1], store.settings.PLUGIN_CONFIG_ROOTS[0],
                                 packages, tmp_path / "profiles", store.settings.get_plugin_state_root())
    monkeypatch.setattr(cli.PluginCliService, "_path_policy", staticmethod(lambda: policy))
    monkeypatch.setattr(cli, "get_install_source_manager", lambda: SimpleNamespace())
    manager = cli.PluginCliService()
    plan = await manager.plan_install(package=str(package))
    assert plan["action"] == "blocked"
    assert plan["reason"] == "development_registration_conflict"
    assert plan["development_sources"] == [str(record.source_dir)]
    with pytest.raises(ServerDomainError) as error:
        await manager.install(package=str(package))
    assert error.value.code == "PLUGIN_INSTALL_BLOCKED"
    assert list(policy.user_plugins_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle", [False, True], ids=["single", "bundle"])
@pytest.mark.parametrize("corruption", ["truncated", "invalid_shape", "unknown_field"])
async def test_corrupt_development_store_preserves_managed_install(monkeypatch, tmp_path, bundle, corruption):
    from plugin.neko_plugin_cli.public.build import build_plugin, build_bundle
    from plugin.server.application.plugin_cli import service as cli
    from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy

    record = _register(tmp_path)
    source_before = {path.name: path.read_bytes() for path in record.source_dir.iterdir()}
    healthy_store = store._store_path().read_bytes()
    invalid = json.loads(healthy_store)
    invalid["registrations"][0]["unexpected"] = "private value"
    damaged = {"truncated": b'{', "invalid_shape": b'{"enabled":true,"registrations":{}}',
               "unknown_field": json.dumps(invalid).encode()}[corruption]
    store._store_path().write_bytes(damaged)
    incoming = _source(tmp_path / "incoming", "ordinary", "ordinary")
    packages = tmp_path / "packages"
    packages.mkdir()
    package = packages / ("incoming.neko-bundle" if bundle else "incoming.neko-plugin")
    ids = ["ordinary", "second"] if bundle else ["ordinary"]
    if bundle:
        second = _source(tmp_path / "incoming", "second", "second")
        await asyncio.to_thread(build_bundle, [incoming, second], package, bundle_id="ordinary_bundle")
    else:
        await asyncio.to_thread(build_plugin, incoming, package)
    policy = PluginCliPathPolicy(store.settings.PLUGIN_CONFIG_ROOTS[1], store.settings.PLUGIN_CONFIG_ROOTS[0],
                                 packages, tmp_path / "profiles", store.settings.get_plugin_state_root())
    monkeypatch.setattr(cli.PluginCliService, "_path_policy", staticmethod(lambda: policy))
    monkeypatch.setattr(cli, "get_install_source_manager", lambda: SimpleNamespace())
    manager = cli.PluginCliService()
    plan = await manager.plan_install(package=str(package))
    assert plan["action"] == "install"
    assert not list(policy.user_plugins_root.iterdir())
    result = await manager.install(package=str(package))
    assert {item["target_plugin_id"] for item in result["installed_plugins"]} == set(ids)
    for plugin_id in ids:
        assert (policy.user_plugins_root / plugin_id / "__init__.py").read_bytes() == (incoming / "__init__.py").read_bytes()
        assert not cli.is_autostart_approved(plugin_id)
    assert store._store_path().read_bytes() == damaged
    assert {path.name: path.read_bytes() for path in record.source_dir.iterdir()} == source_before
    # Installation does not silently repair, reset, or erase optional registrations.
    store._store_path().write_bytes(healthy_store)
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
async def test_install_plan_preserves_other_development_errors(monkeypatch, tmp_path):
    from plugin.neko_plugin_cli.public.build import build_plugin
    from plugin.server.application.plugin_cli import service as cli
    from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy

    incoming = _source(tmp_path / "incoming", "ordinary", "ordinary")
    packages = tmp_path / "packages"
    packages.mkdir()
    package = packages / "incoming.neko-plugin"
    await asyncio.to_thread(build_plugin, incoming, package)
    policy = PluginCliPathPolicy(store.settings.PLUGIN_CONFIG_ROOTS[1], store.settings.PLUGIN_CONFIG_ROOTS[0],
                                 packages, tmp_path / "profiles", store.settings.get_plugin_state_root())
    monkeypatch.setattr(cli.PluginCliService, "_path_policy", staticmethod(lambda: policy))
    monkeypatch.setattr(cli, "get_install_source_manager", lambda: SimpleNamespace())

    def conflict():
        raise ServerDomainError(code="DEVELOPMENT_CONFLICT", message="conflict", status_code=409)

    monkeypatch.setattr(store, "list_registration_records_sync", conflict)
    manager = cli.PluginCliService()
    for operation in (manager.plan_install, manager.install):
        with pytest.raises(ServerDomainError) as error:
            await operation(package=str(package))
        assert error.value.code == "DEVELOPMENT_CONFLICT"
    assert not list(policy.user_plugins_root.iterdir())


@pytest.mark.asyncio
async def test_shutdown_returning_with_live_process_retains_host_and_association(tmp_path):
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    record = _register(tmp_path)
    class StillAliveHost:
        async def start(self, **kwargs):
            pass
        async def shutdown(self, **kwargs):
            pass
        def is_alive(self):
            return True
    host = StillAliveHost()
    service.state.plugin_hosts[record.plugin_id] = host
    with pytest.raises(ServerDomainError) as error:
        await PluginLifecycleService().stop_plugin(record.plugin_id)
    assert error.value.code == "PLUGIN_STOP_FAILED"
    assert service.state.plugin_hosts[record.plugin_id] is host
    with pytest.raises(ServerDomainError):
        await service.remove_development(record.registration_id, record.revision)
    assert store.list_registration_records_sync() == [record]
    assert service.state.plugin_hosts[record.plugin_id] is host


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True], ids=["ordinary", "development"])
async def test_legacy_admin_stop_requires_reference_for_development_only(tmp_path, registered):
    from plugin.server.application.admin.command_service import AdminCommandService
    if registered:
        _register(tmp_path)
    stop = AsyncMock(return_value={"success": True})
    command = AdminCommandService(lifecycle_service=SimpleNamespace(stop_plugin=stop))
    if registered:
        with pytest.raises(ServerDomainError) as error:
            await command.execute(method="plugin.stop", raw_params={"plugin_id": "demo"})
        assert error.value.code == "DEVELOPMENT_REFERENCE_REQUIRED"
        stop.assert_not_called()
    else:
        assert await command.execute(method="plugin.stop", raw_params={"plugin_id": "demo"}) == {"success": True}
        stop.assert_awaited_once_with("demo")


@pytest.mark.asyncio
async def test_legacy_stop_rechecks_identity_after_waiting_for_registration(tmp_path):
    from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
    entered, release = asyncio.Event(), asyncio.Event()
    @serialized_plugin_operation
    async def register():
        entered.set()
        await release.wait()
        return await asyncio.to_thread(_register, tmp_path)
    registration_task = asyncio.create_task(register())
    await asyncio.wait_for(entered.wait(), 3)
    stop = AsyncMock()
    old_request = asyncio.create_task(service.stop_ordinary_plugin("demo", lifecycle_service=SimpleNamespace(stop_plugin=stop)))
    await asyncio.sleep(0)
    release.set()
    record = await asyncio.wait_for(registration_task, 3)
    with pytest.raises(ServerDomainError) as error:
        await asyncio.wait_for(old_request, 3)
    assert error.value.code == "DEVELOPMENT_REFERENCE_REQUIRED"
    stop.assert_not_called()
    assert store.list_registration_records_sync() == [record]


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True], ids=["ordinary", "development"])
async def test_websocket_stop_returns_versioned_api_error(monkeypatch, tmp_path, registered):
    from plugin.server.websocket import admin
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    if registered:
        _register(tmp_path)
    stop = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginLifecycleService, "stop_plugin", stop)
    class Socket:
        sent = []
        delivered = False
        async def accept(self):
            pass
        async def close(self, **kwargs):
            pass
        async def send_text(self, text):
            self.sent.append(json.loads(text))
        async def receive_text(self):
            if self.delivered:
                raise RuntimeError("test disconnect")
            self.delivered = True
            return json.dumps({"type": "req", "id": "stop", "method": "plugin.stop", "params": {"plugin_id": "demo"}})
    monkeypatch.setattr(admin, "ws_admin_hub", SimpleNamespace(start=AsyncMock(), register=AsyncMock(), unregister=AsyncMock()))
    socket = Socket()
    await asyncio.wait_for(admin.ws_admin_endpoint(socket), 3)
    response = next(item for item in socket.sent if item.get("id") == "stop")
    if registered:
        assert response["ok"] is False
        assert "versioned HTTP" in response["error"]
        stop.assert_not_called()
    else:
        assert response["ok"] is True
        stop.assert_awaited_once_with("demo")
