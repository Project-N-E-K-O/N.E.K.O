from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from plugin.core.state import state
from plugin.server.application.plugins import development as store, operation_lock, registry_service
from plugin.server.routes import config as routes


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(store, "_store_path", lambda: tmp_path / "development.json")
    monkeypatch.setattr(store.settings, "PLUGIN_CONFIG_ROOTS", (root,))
    monkeypatch.setattr(registry_service, "PLUGIN_CONFIG_ROOTS", (root,))
    monkeypatch.setattr(operation_lock, "_operation_file_lock_path", lambda: tmp_path / "operation.lock")
    for field in ("plugins", "plugin_hosts", "event_handlers"):
        monkeypatch.setattr(state, field, {})
    source = tmp_path / "demo"
    source.mkdir()
    (source / "plugin.toml").write_text('[plugin]\nid="demo"\nentry="plugins.demo:Demo"\n', encoding="utf-8")
    (source / "__init__.py").write_text('class Demo: pass\n', encoding="utf-8")
    (source / "profiles").mkdir()
    (source / "profiles/default.toml").write_text('[settings]\nvalue="original"\n', encoding="utf-8")
    (source / "profiles.toml").write_text('[config_profiles]\nactive="default"\n[config_profiles.files]\ndefault="profiles/default.toml"\n', encoding="utf-8")
    store.set_enabled_sync(True)
    record = store.register_directory_sync(str(source))
    registry_service.PluginRegistryService()._refresh_plugin_sync("demo")
    app = FastAPI()
    app.include_router(routes.router)
    return app, record


def client(app, *, peer="127.0.0.1", headers=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                            base_url="http://127.0.0.1", headers=headers or {})


def source_bytes(path):
    return {item.relative_to(path): item.read_bytes() for item in path.rglob("*") if item.is_file()}


def conversion_body(operation, plugin_id="demo"):
    config = {"plugin": {"id": plugin_id, "entry": f"plugins.{plugin_id}:Demo"}}
    if operation == "parse_toml":
        return {"toml": f'[plugin]\nid="{plugin_id}"\nentry="plugins.{plugin_id}:Demo"\n'}
    return {"config": config}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["parse_toml", "render_toml"])
@pytest.mark.parametrize("ordinary", [False, True])
async def test_toml_conversion_retains_authorized_behavior(workspace, operation, ordinary):
    app, record = workspace
    plugin_id = "ordinary" if ordinary else "demo"
    params = {} if ordinary else {"registration_id": record.registration_id, "revision": record.revision}
    if ordinary:
        directory = store.settings.PLUGIN_CONFIG_ROOTS[0] / plugin_id
        directory.mkdir()
        manifest = directory / "plugin.toml"
        manifest.write_text('[plugin]\nid="ordinary"\nentry="plugins.ordinary:Demo"\n')
        state.plugins[plugin_id] = {"config_path": str(manifest)}

    async with client(app, peer="192.168.1.2" if ordinary else "127.0.0.1",
                      headers={} if ordinary else {"X-Neko-Development": "1"}) as http:
        async def convert():
            return await http.post(f"/plugin/{plugin_id}/config/{operation}",
                                   params=params, json=conversion_body(operation, plugin_id))
        if ordinary:
            # The ordinary dispatcher must still bypass the lifecycle lock.
            async with operation_lock.plugin_operation_lock.hold():
                response = await asyncio.wait_for(convert(), 3)
        else:
            response = await convert()
    assert response.status_code == 200, response.text
    assert response.json()["plugin_id"] == plugin_id
    if operation == "parse_toml":
        assert response.json()["config"]["plugin"]["id"] == plugin_id
    else:
        assert plugin_id in response.json()["toml"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["parse_toml", "render_toml"])
async def test_toml_conversion_rechecks_revision_after_lock_wait(workspace, operation):
    app, record = workspace
    before = source_bytes(record.source_dir)
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        async with operation_lock.plugin_operation_lock.hold():
            pending = asyncio.create_task(http.post(f"/plugin/demo/config/{operation}",
                params={"registration_id": record.registration_id, "revision": record.revision},
                json=conversion_body(operation)))
            await asyncio.sleep(0.05)
            assert not pending.done()
            await asyncio.to_thread(store.rebind_registration_sync, record, str(record.source_dir))
        response = await asyncio.wait_for(pending, 3)
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
    assert source_bytes(record.source_dir) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["parse_toml", "render_toml"])
async def test_cancelled_toml_conversion_fences_runtime_initialization(workspace, monkeypatch, operation):
    from plugin.server.infrastructure import config_queries

    app, record = workspace
    original = config_queries.load_plugin_base_config
    entered, release = threading.Event(), threading.Event()

    def blocked_load(plugin_id):
        entered.set()
        assert release.wait(5)
        return original(plugin_id)

    monkeypatch.setattr(config_queries, "load_plugin_base_config", blocked_load)

    @operation_lock.serialized_plugin_operation
    async def rebind():
        return await asyncio.to_thread(store.rebind_registration_sync, record, str(record.source_dir))

    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        pending = asyncio.create_task(http.post(f"/plugin/demo/config/{operation}",
            params={"registration_id": record.registration_id, "revision": record.revision},
            json=conversion_body(operation)))
        writer = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending.cancel()
            writer = asyncio.create_task(rebind())
            await asyncio.sleep(0.05)
            assert not pending.done()
            assert not writer.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 3)
            if writer is not None:
                updated = await asyncio.wait_for(writer, 3)
    assert updated.revision == record.revision + 1


@pytest.mark.asyncio
async def test_ordinary_config_does_not_wait_for_development_operation(workspace, monkeypatch):
    app, _ = workspace
    ordinary = store.settings.PLUGIN_CONFIG_ROOTS[0] / "ordinary"
    ordinary.mkdir()
    (ordinary / "plugin.toml").write_text('[plugin]\nid="ordinary"\n', encoding="utf-8")
    state.plugins["ordinary"] = {"config_path": str(ordinary / "plugin.toml")}
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", "1")
    async with client(app, peer="192.168.1.2") as http:
        async with operation_lock.plugin_operation_lock.hold():
            response = await asyncio.wait_for(http.put("/plugin/ordinary/config/profiles/default",
                json={"config": {"settings": {"value": "ordinary"}}}), 3)
            assert response.status_code == 200, response.text
            response = await asyncio.wait_for(http.get("/plugin/ordinary/config/profiles/default"), 3)
            assert response.status_code == 200, response.text
            assert response.json()["config"]["settings"]["value"] == "ordinary"
    assert 'ordinary' in (ordinary / "profiles/default.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("hot_update", [False, True])
async def test_ordinary_request_keeps_source_and_host_after_metadata_takeover(workspace, monkeypatch, hot_update):
    from types import SimpleNamespace

    app, record = workspace
    ordinary = store.settings.PLUGIN_CONFIG_ROOTS[0] / "ordinary"
    ordinary.mkdir()
    manifest = ordinary / "plugin.toml"
    manifest.write_text('[plugin]\nid="ordinary"\n', encoding="utf-8")
    state.plugins["ordinary"] = {"config_path": str(manifest)}
    old_host = SimpleNamespace(config_path=str(manifest), send_config_update=AsyncMock(return_value={"handler_called": True}))
    new_host = SimpleNamespace(config_path=str(record.source_dir / "plugin.toml"), send_config_update=AsyncMock())
    state.plugin_hosts["ordinary"] = old_host
    before = source_bytes(record.source_dir)
    entered, release = asyncio.Event(), asyncio.Event()
    method = "hot_update_plugin_config" if hot_update else "upsert_plugin_profile_config"
    original = getattr(routes.config_command_service, method)

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(routes.config_command_service, method, delayed)
    async with client(app, peer="192.168.1.2") as http:
        async with operation_lock.plugin_operation_lock.hold():
            request = http.post("/plugin/ordinary/config/hot-update",
                json={"config": {"settings": {"value": "captured"}}, "mode": "permanent"}) if hot_update else http.put(
                    "/plugin/ordinary/config/profiles/default", json={"config": {"settings": {"value": "captured"}}})
            pending = asyncio.create_task(request)
            try:
                await asyncio.wait_for(entered.wait(), 3)
                state.plugins["ordinary"] = {"source": "development", "config_path": str(record.source_dir / "plugin.toml")}
                state.plugin_hosts["ordinary"] = new_host
            finally:
                release.set()
            response = await asyncio.wait_for(pending, 3)
            assert response.status_code == 200, response.text
        # The request-local binding must not authorize the next request.
        # Cached development metadata without a matching registration is stale.
        denied = await http.get("/plugin/ordinary/config")
        assert denied.status_code == 409, denied.text
        assert denied.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
    new_host.send_config_update.assert_not_awaited()
    if hot_update:
        old_host.send_config_update.assert_awaited_once()
        assert old_host.send_config_update.call_args.kwargs["config"]["settings"]["value"] == "captured"
    else:
        assert 'captured' in (ordinary / "profiles/default.toml").read_text(encoding="utf-8")
    assert source_bytes(record.source_dir) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None), ("GET", "/toml", None), ("GET", "/base", None),
    ("GET", "/base/effective", None), ("GET", "/profiles", None),
    ("GET", "/profiles/default", None),
    ("POST", "/parse_toml", {"toml": '[plugin]\nid="demo"\nentry="plugins.demo:Demo"\n'}),
    ("POST", "/render_toml", {"config": {"plugin": {"id": "demo", "entry": "plugins.demo:Demo"}}}),
    ("PUT", "", {"config": {"settings": {"value": "changed"}}}),
    ("PUT", "/toml", {"toml": '[settings]\nvalue="changed"\n'}),
    ("PUT", "/profiles/default", {"config": {"settings": {"value": "changed"}}}),
    ("DELETE", "/profiles/default", None),
    ("POST", "/profiles/default/activate", None),
    ("POST", "/hot-update", {"config": {"settings": {"value": "changed"}}}),
])
@pytest.mark.parametrize("access", ["lan", "missing_header", "stale"])
async def test_development_config_requires_local_current_reference(workspace, method, suffix, body, access):
    app, record = workspace
    before = source_bytes(record.source_dir)
    params = {"registration_id": record.registration_id, "revision": record.revision + (access == "stale")}
    async with client(app, peer="192.168.1.2" if access == "lan" else "127.0.0.1",
                      headers={} if access == "missing_header" else {"X-Neko-Development": "1"}) as http:
        response = await http.request(method, "/plugin/demo/config" + suffix, params=params, json=body)
    assert response.status_code == (409 if access == "stale" else 403), response.text
    assert response.headers["X-Error-Code"] == ("DEVELOPMENT_STALE" if access == "stale" else "DEVELOPMENT_ACCESS_DENIED")
    assert source_bytes(record.source_dir) == before


@pytest.mark.asyncio
async def test_local_profile_writes_use_current_registration(workspace):
    app, record = workspace
    params = {"registration_id": record.registration_id, "revision": record.revision}
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        assert (await http.put("/plugin/demo/config/profiles/default", json={"config": {}})).status_code == 409
        response = await http.put("/plugin/demo/config/profiles/default", params=params,
                                  json={"config": {"settings": {"value": "changed"}}})
        assert response.status_code == 200, response.text
        response = await http.get("/plugin/demo/config/profiles/default", params=params)
        assert response.status_code == 200
    assert 'changed' in (record.source_dir / "profiles/default.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_config_request_rechecks_registration_after_waiting(workspace):
    app, record = workspace
    entered, release = asyncio.Event(), asyncio.Event()

    @operation_lock.serialized_plugin_operation
    async def rebind():
        entered.set()
        await release.wait()
        return store.rebind_registration_sync(record, str(record.source_dir))

    writer = asyncio.create_task(rebind())
    await asyncio.wait_for(entered.wait(), 3)
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        pending = asyncio.create_task(http.put("/plugin/demo/config/profiles/default",
            params={"registration_id": record.registration_id, "revision": record.revision},
            json={"config": {"settings": {"value": "stale"}}}))
        await asyncio.sleep(0)
        release.set()
        await writer
        response = await asyncio.wait_for(pending, 3)
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
    assert 'original' in (record.source_dir / "profiles/default.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ordinary_config_remains_available_with_corrupt_store(workspace, monkeypatch):
    app, record = workspace
    ordinary = store.settings.PLUGIN_CONFIG_ROOTS[0] / "ordinary"
    ordinary.mkdir()
    (ordinary / "plugin.toml").write_text('[plugin]\nid="ordinary"\n', encoding="utf-8")
    state.plugins["ordinary"] = {"config_path": str(ordinary / "plugin.toml")}
    store._store_path().write_text('{', encoding="utf-8")
    action = AsyncMock(return_value={"config": {}})
    monkeypatch.setattr(routes.config_query_service, "get_plugin_config", action)
    async with client(app, peer="192.168.1.2") as http:
        assert (await http.get("/plugin/ordinary/config")).status_code == 200
        assert (await http.get("/plugin/demo/config")).status_code == 500
    action.assert_awaited_once_with(plugin_id="ordinary")
    assert store._store_path().read_text(encoding="utf-8") == '{'


@pytest.mark.asyncio
async def test_current_reference_cannot_write_stale_metadata_path(workspace):
    app, record = workspace
    replacement = record.source_dir.parent / "replacement" / "demo"
    replacement.mkdir(parents=True)
    (replacement / "plugin.toml").write_bytes((record.source_dir / "plugin.toml").read_bytes())
    (replacement / "__init__.py").write_bytes((record.source_dir / "__init__.py").read_bytes())
    updated = await asyncio.to_thread(store.rebind_registration_sync, record, str(replacement))
    before = source_bytes(record.source_dir)
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        response = await http.put("/plugin/demo/config/profiles/default",
            params={"registration_id": updated.registration_id, "revision": updated.revision},
            json={"config": {"settings": {"value": "wrong directory"}}})
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
    assert source_bytes(record.source_dir) == before
    assert not (replacement / "profiles.toml").exists()


@pytest.mark.asyncio
async def test_cancelled_config_write_finishes_before_rebind(workspace, monkeypatch):
    from plugin.server.application.config import command_service

    app, record = workspace
    entered, release = threading.Event(), threading.Event()
    original = command_service.infrastructure_upsert_profile_config

    def slow_write(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(command_service, "infrastructure_upsert_profile_config", slow_write)
    rebound = asyncio.Event()

    @operation_lock.serialized_plugin_operation
    async def rebind():
        updated = await asyncio.to_thread(store.rebind_registration_sync, record, str(record.source_dir))
        rebound.set()
        return updated

    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        pending = asyncio.create_task(http.put("/plugin/demo/config/profiles/default",
            params={"registration_id": record.registration_id, "revision": record.revision},
            json={"config": {"settings": {"value": "completed"}}}))
        writer = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending.cancel()
            writer = asyncio.create_task(rebind())
            await asyncio.sleep(0.05)
            assert not pending.done()
            assert not rebound.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 3)
            if writer is not None:
                updated = await asyncio.wait_for(writer, 3)
    assert updated.revision == record.revision + 1
    assert 'completed' in (record.source_dir / "profiles/default.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_config_wait_timeout_or_cancellation_never_writes_late(workspace, monkeypatch, cancel):
    app, record = workspace
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", "1")
    before = source_bytes(record.source_dir)
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        async with operation_lock.plugin_operation_lock.hold():
            pending = asyncio.create_task(http.put("/plugin/demo/config/profiles/default",
                params={"registration_id": record.registration_id, "revision": record.revision},
                json={"config": {"settings": {"value": "late"}}}))
            if cancel:
                await asyncio.sleep(0.05)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 3)
            else:
                response = await asyncio.wait_for(pending, 3)
                assert response.status_code == 409
                assert response.headers["X-Error-Code"] == "PLUGIN_OPERATION_BUSY"
        # A subsequent request can acquire the lock after the abandoned waiter.
        response = await http.get("/plugin/demo/config/profiles/default",
            params={"registration_id": record.registration_id, "revision": record.revision})
        assert response.status_code == 200
    assert source_bytes(record.source_dir) == before
