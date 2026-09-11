from __future__ import annotations

from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI

from plugin.server.routes import plugins as routes
from plugin.server.application.plugins import development as store
from plugin.server.application.plugins import operation_lock


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(store.settings, "get_plugin_state_root", lambda: tmp_path / "state" / "plugins")
    monkeypatch.setattr(store.settings, "PLUGIN_CONFIG_ROOTS", (tmp_path / "installed",))
    monkeypatch.setattr(operation_lock, "_operation_file_lock_path", lambda: tmp_path / "operation.lock")
    monkeypatch.setattr(routes, "ensure_plugin_messaging_started", AsyncMock())
    app = FastAPI()
    app.include_router(routes.router)
    return app


def client(app, *, peer="127.0.0.1", host="127.0.0.1", headers=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                            base_url=f"http://{host}", headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_registered", [False, True])
async def test_public_ui_metadata_hides_development_directory(app, tmp_path, monkeypatch, runtime_registered):
    from types import SimpleNamespace
    from plugin.core.state import state
    from plugin.core.communication import PluginCommunicationResourceManager
    from plugin.logging_config import get_logger
    from plugin.server.routes import plugin_ui

    source = tmp_path / "private checkout" / "demo"
    static = source / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<p>hello</p>", encoding="utf-8")
    meta = {"id": "demo", "name": "Demo", "source": "development",
            "config_path": str(source / "plugin.toml")}
    ordinary = {"id": "ordinary", "name": "Ordinary", "config_path": str(source / "plugin.toml")}
    monkeypatch.setattr(state, "plugins", {"demo": meta, "ordinary": ordinary})
    monkeypatch.setattr(state, "plugin_hosts", {})
    monkeypatch.setattr(state, "event_handlers", {})
    if runtime_registered:
        manager = SimpleNamespace(plugin_id="demo", logger=get_logger("test.static-ui"))
        await PluginCommunicationResourceManager._handle_static_ui_register(manager,
            {"config": {"enabled": True, "directory": str(static), "index_file": "index.html"}})
    state.invalidate_snapshot_cache("plugins")
    app.include_router(plugin_ui.router)
    async with client(app, peer="192.168.1.2") as http:
        cards = (await http.get("/plugins")).json()["plugins"]
        card = next(item for item in cards if item["id"] == "demo")
        assert "static_ui_config" not in card
        info = await http.get("/plugin/demo/ui-info")
        assert info.status_code == 200
        assert info.json()["has_ui"] is True
        assert info.json()["static_dir"] is None
        assert info.json()["static_files"] == ["index.html"]
        assert "private checkout" not in info.text
        ordinary_info = await http.get("/plugin/ordinary/ui-info")
        assert ordinary_info.json()["static_dir"] == str(static)
    assert meta["config_path"] == str(source / "plugin.toml")
    if runtime_registered:
        assert meta["static_ui_config"]["directory"] == str(static)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_static_dir", "get_static_ui_config", "get_ui_info"])
async def test_static_ui_metadata_read_failure_does_not_expose_source(method, monkeypatch):
    from plugin.server.application.plugins import ui_query_service
    from plugin.server.domain.errors import ServerDomainError

    def unavailable(_plugin_id):
        raise OSError("metadata unavailable at /private/developer/checkout")

    monkeypatch.setattr(ui_query_service, "_get_plugin_meta_sync", unavailable)
    service = ui_query_service.PluginUiQueryService()
    with pytest.raises(ServerDomainError) as failure:
        await getattr(service, method)("demo")
    assert failure.value.code == "PLUGIN_UI_QUERY_FAILED"
    assert failure.value.status_code == 500
    assert "/private" not in failure.value.message
    assert "/private" not in str(failure.value.details)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_static_dir", "get_static_ui_config", "get_ui_info"])
async def test_static_ui_queries_reject_malformed_metadata_without_echoing_it(method, monkeypatch):
    from plugin.core.state import state
    from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
    from plugin.server.domain.errors import ServerDomainError

    monkeypatch.setattr(state, "plugins", {"demo": {42: "/private/developer/checkout"}})
    with pytest.raises(ServerDomainError) as failure:
        await getattr(PluginUiQueryService(), method)("demo")
    assert failure.value.code == "INVALID_DATA_SHAPE"
    assert "/private" not in failure.value.message
    assert "/private" not in str(failure.value.details)


@pytest.mark.asyncio
async def test_development_static_files_keep_internal_source_resolution(app, tmp_path, monkeypatch):
    from plugin.core.state import state
    from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
    from plugin.server.routes import plugin_ui

    source = tmp_path / "private checkout" / "demo"
    static = source / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<p>development UI</p>", encoding="utf-8")
    monkeypatch.setattr(state, "plugins", {"demo": {"id": "demo", "source": "development", "config_path": str(source / "plugin.toml")}})
    service = PluginUiQueryService()
    assert await service.get_static_dir("demo") == static
    assert (await service.get_static_ui_config("demo"))["directory"] == str(static)
    assert await service.get_static_dir("unknown") is None
    assert await service.get_static_ui_config("unknown") is None
    app.include_router(plugin_ui.router)
    async with client(app, peer="192.168.1.2") as http:
        response = await http.get("/plugin/demo/ui/index.html")
        assert response.status_code == 200, response.text
        assert "development UI" in response.text
        assert (await http.get("/plugin/unknown/ui-info")).status_code == 404
    # No UI directory is also an ordinary, non-error result.
    monkeypatch.setattr(state, "plugins", {"demo": {"source": "development"}})
    assert await service.get_static_dir("demo") is None
    assert await service.get_static_ui_config("demo") is None


@pytest.mark.asyncio
async def test_public_list_omits_development_provenance_but_local_details_remain(app, tmp_path, monkeypatch):
    from plugin.core.state import state
    from plugin.server.application.plugins import registry_service as registry
    source = tmp_path / "private source" / "demo"
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text('[plugin]\nid="demo"\nname="Demo"\nentry="plugins.demo:Demo"\n', encoding="utf-8")
    (source / "__init__.py").write_text('class Demo: pass\n', encoding="utf-8")
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    monkeypatch.setattr(state, "event_handlers", {})
    store.set_enabled_sync(True)
    record = store.register_directory_sync(str(source))
    registry.PluginRegistryService()._refresh_plugin_sync("demo")
    meta = state.plugins["demo"]
    meta.update(runtime_load_error_message=f"Failed at {source}", runtime_startup_error=f"Error in {source}")
    state.plugins["ordinary"] = {"id": "ordinary", "name": "Ordinary", "config_path": "ordinary.toml", "source": "user"}
    state.invalidate_snapshot_cache("plugins")
    async with client(app, peer="192.168.1.2") as http:
        response = await http.get("/plugins")
        assert response.status_code == 200
        cards = {item["id"]: item for item in response.json()["plugins"]}
        assert cards["demo"]["name"] == "Demo"
        assert cards["demo"]["source"] == "development"
        assert "entries" in cards["demo"] and "status" in cards["demo"]
        for field in ("source_dir", "config_path", "development_ref", "runtime_load_error_message", "runtime_startup_error"):
            assert field not in cards["demo"]
        assert cards["ordinary"]["config_path"] == "ordinary.toml"
        assert record.registration_id not in response.text
        assert "private source" not in response.text
        assert (await http.get("/plugins/development")).status_code == 403
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        details = (await http.get("/plugins/development")).json()["registrations"][0]
        assert details["source_dir"] == str(source.resolve())
        assert details["registration_id"] == record.registration_id
    assert meta["development_ref"]["registration_id"] == record.registration_id
    assert meta["config_path"] == str(source / "plugin.toml")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["invalid_key", "io"])
@pytest.mark.parametrize("source_marker", ["source", "development_ref", None])
async def test_public_list_keeps_install_source_and_isolates_broken_development_metadata(app, monkeypatch, failure_kind, source_marker):
    from types import SimpleNamespace
    from plugin.core.state import state
    from plugin.server.application.plugins import query_service as query
    from plugin.server.application.install_source import LockEntry, SourceDetailImported

    installed = LockEntry(
        root_id="user", directory_name="ordinary", plugin_id="ordinary", channel="imported",
        reason="user_requested", installed_at="2026-09-09T00:00:00Z",
        updated_at="2026-09-09T00:00:00Z", last_seen_at="2026-09-09T00:00:00Z",
        source_detail=SourceDetailImported(package_filename="ordinary.neko-plugin", package_sha256="a" * 64),
    )
    manager = SimpleNamespace(snapshot=lambda: SimpleNamespace(entries=(installed,)))
    monkeypatch.setattr(query, "get_install_source_manager", lambda: manager)
    broken = {"id": "demo", "name": "Demo", "source_dir": "/private/source",
              "config_path": "/private/source/plugin.toml"}
    if source_marker == "source":
        broken["source"] = "development"
    elif source_marker == "development_ref":
        broken["development_ref"] = {"registration_id": "private-registration"}
    if failure_kind == "invalid_key":
        broken[42] = "invalid metadata key"
    else:
        original_loader = query.load_plugin_i18n_from_meta

        def load_metadata(meta):
            if meta.get("id") == "demo":
                raise OSError("Cannot read /private/source/plugin.toml")
            return original_loader(meta)

        monkeypatch.setattr(query, "load_plugin_i18n_from_meta", load_metadata)
    monkeypatch.setattr(state, "plugins", {"demo": broken, "ordinary": {"id": "ordinary", "name": "Ordinary"}})
    monkeypatch.setattr(state, "plugin_hosts", {})
    monkeypatch.setattr(state, "event_handlers", {})
    state.invalidate_snapshot_cache("plugins")
    async with client(app, peer="192.168.1.2") as http:
        response = await http.get("/plugins")
    assert response.status_code == 200
    cards = {item["id"]: item for item in response.json()["plugins"]}
    expected = {"id": "demo", "name": "Demo", "description": "", "entries": []}
    if source_marker is not None:
        expected["source"] = "development"
    assert cards["demo"] == expected
    assert cards["ordinary"]["status"] == "stopped"
    assert cards["ordinary"]["install_source"] == {
        "source": "imported", "reason": "user_requested", "installed_at": "2026-09-09T00:00:00Z",
        "source_detail": {"package_filename": "ordinary.neko-plugin", "package_sha256": "a" * 64},
    }
    assert "/private/source" not in response.text
    assert "private-registration" not in response.text


@pytest.mark.asyncio
async def test_corrupt_store_reload_all_reloads_only_verifiable_hosts(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from plugin.core.state import state
    from plugin.server.application.plugins import registry_service as registry, lifecycle_service as lifecycle
    root = store.settings.PLUGIN_CONFIG_ROOTS[0]
    monkeypatch.setattr(registry, "PLUGIN_CONFIG_ROOTS", (root,))
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    monkeypatch.setattr(state, "event_handlers", {})
    ordinary = root / "ordinary"
    external = tmp_path / "external" / "demo"
    for directory in (ordinary, external):
        directory.mkdir(parents=True)
        (directory / "plugin.toml").write_text(f'[plugin]\nid="{directory.name}"\nentry="plugins.{directory.name}:Demo"\n', encoding="utf-8")
        (directory / "__init__.py").write_text('class Demo: pass\n', encoding="utf-8")
    store.set_enabled_sync(True)
    store.register_directory_sync(str(external))
    await registry.PluginRegistryService().refresh_registry()
    for plugin_id in ("ordinary", "demo"):
        state.plugin_hosts[plugin_id] = SimpleNamespace(is_alive=lambda: True)
    store._store_path().write_text('{', encoding="utf-8")
    stopped, started = [], []

    async def stop(plugin_id, **kwargs):
        stopped.append(plugin_id)
        return lifecycle._ReloadOutcome(plugin_id=plugin_id, success=True)

    async def start(plugin_id, **kwargs):
        started.append(plugin_id)
        return lifecycle._ReloadOutcome(plugin_id=plugin_id, success=True)

    monkeypatch.setattr(routes.lifecycle_service, "_safe_stop_for_reload", stop)
    monkeypatch.setattr(routes.lifecycle_service, "_safe_start_for_reload", start)
    async with client(app, peer="192.168.1.2", headers={"X-Neko-Development": "1"}) as http:
        assert (await http.post("/plugins/reload")).status_code == 403
    async with client(app) as http:
        assert (await http.post("/plugins/reload")).status_code == 403
    assert not stopped and not started
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        response = await http.post("/plugins/reload")
        assert response.status_code == 200
        assert response.json()["reloaded"] == ["ordinary"]
        assert any(item["plugin_id"] == "demo" for item in response.json()["failed"])
    assert stopped == started == ["ordinary"]
    assert state.plugin_hosts["demo"].is_alive()
    assert store._store_path().read_text(encoding="utf-8") == '{'


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,headers", [
    ("192.168.1.2", {"X-Neko-Development": "1"}),
    ("127.0.0.1", {}),
    ("127.0.0.1", {"X-Neko-Development": "1", "Origin": "https://evil.example"}),
])
async def test_refresh_development_requires_local_provenance(app, tmp_path, monkeypatch, peer, headers):
    from types import SimpleNamespace
    record = SimpleNamespace(plugin_id="demo", registration_id="reg", revision=1)
    monkeypatch.setattr(routes, "registration_for_plugin_sync", lambda _: record)
    monkeypatch.setattr(routes, "list_registration_records_sync", lambda: [record])
    one, all_plugins = AsyncMock(), AsyncMock()
    monkeypatch.setattr(routes.registry_service, "refresh_plugin", one)
    monkeypatch.setattr(routes.registry_service, "refresh_registry", all_plugins)
    async with client(app, peer=peer, headers=headers) as http:
        assert (await http.post("/plugin/demo/refresh", params={"registration_id": "reg", "revision": 1})).status_code == 403
        assert (await http.post("/plugins/refresh")).status_code == 403
    one.assert_not_awaited()
    all_plugins.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_checks_revision_after_waiting_for_registration_operation(app, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    record = SimpleNamespace(plugin_id="demo", registration_id="reg", revision=1)
    monkeypatch.setattr(routes, "registration_for_plugin_sync", lambda _: record)
    refresh = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(routes.registry_service, "refresh_plugin", refresh)
    entered, release = asyncio.Event(), asyncio.Event()

    @operation_lock.serialized_plugin_operation
    async def rebind():
        entered.set()
        await release.wait()
        record.revision = 2

    writer = asyncio.create_task(rebind())
    await asyncio.wait_for(entered.wait(), 3)
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        waiting = asyncio.create_task(http.post("/plugin/demo/refresh", params={"registration_id": "reg", "revision": 1}))
        await asyncio.sleep(0)
        assert not waiting.done()
        release.set()
        await writer
        response = await asyncio.wait_for(waiting, 3)
        assert response.status_code == 409
        assert response.headers["X-Error-Code"] == "DEVELOPMENT_STALE"
        refresh.assert_not_awaited()
        assert (await http.post("/plugin/demo/refresh")).status_code == 409
        assert (await http.post("/plugin/demo/refresh", params={"registration_id": "reg", "revision": 2})).status_code == 200
        refresh.assert_awaited_once_with("demo")


@pytest.mark.asyncio
async def test_ordinary_refresh_still_allows_lan_without_development_records(app, monkeypatch):
    monkeypatch.setattr(routes, "registration_for_plugin_sync", lambda _: None)
    monkeypatch.setattr(routes, "list_registration_records_sync", lambda: [])
    monkeypatch.setattr(routes.registry_service, "refresh_plugin", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(routes.registry_service, "refresh_registry", AsyncMock(return_value={"success": True}))
    async with client(app, peer="192.168.1.2") as http:
        assert (await http.post("/plugin/ordinary/refresh")).status_code == 200
        assert (await http.post("/plugins/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_corrupt_store_bulk_refresh_reports_failure_but_keeps_ordinary_plugins(app, tmp_path, monkeypatch):
    from plugin.core.state import state
    from plugin.server.application.plugins import registry_service as registry
    root = store.settings.PLUGIN_CONFIG_ROOTS[0]
    monkeypatch.setattr(registry, "PLUGIN_CONFIG_ROOTS", (root,))
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    directory = root / "ordinary"
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text('[plugin]\nid="ordinary"\nentry="plugins.ordinary:Demo"\n', encoding="utf-8")
    (directory / "__init__.py").write_text('class Demo: pass\n', encoding="utf-8")
    store._store_path().parent.mkdir(parents=True, exist_ok=True)
    store._store_path().write_text('{', encoding="utf-8")
    async with client(app, peer="192.168.1.2") as http:
        response = await http.post("/plugins/refresh")
        assert response.status_code == 403
        assert response.headers["X-Error-Code"] == "DEVELOPMENT_ACCESS_DENIED"
        assert str(store._store_path()) not in response.text
        assert "ordinary" not in state.plugins
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        response = await http.post("/plugins/refresh")
        assert response.status_code == 200
        assert response.json()["success"] is False
        assert response.json()["failed"]
        assert "ordinary" in state.plugins
    async with client(app, peer="192.168.1.2") as http:
        assert (await http.post("/plugin/ordinary/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_refresh_lock_timeout_does_not_publish_late(app, monkeypatch):
    import asyncio
    monkeypatch.setattr(routes, "_OPERATION_WAIT_BUDGET_SECONDS", 0.01)
    refresh = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(routes.registry_service, "refresh_registry", refresh)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        async with operation_lock.plugin_operation_lock.hold():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), 3)
    try:
        async with client(app) as http:
            response = await http.post("/plugins/refresh")
            assert response.status_code == 409
            assert response.headers["X-Error-Code"] == "PLUGIN_OPERATION_BUSY"
    finally:
        release.set()
        await holder
    refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,host,headers", [
    ("127.0.0.1", "127.0.0.1", {}),
    ("192.168.1.2", "127.0.0.1", {"X-Neko-Development": "1"}),
    ("127.0.0.1", "evil.example", {"X-Neko-Development": "1"}),
    ("127.0.0.1", "127.0.0.1", {"X-Neko-Development": "1", "Origin": "https://evil.example"}),
    ("127.0.0.1", "127.0.0.1", {"X-Neko-Development": "1", "Origin": "null"}),
    ("127.0.0.1", "127.0.0.1", {"X-Neko-Development": "1", "Origin": "https://localhost.evil.example"}),
])
async def test_development_denies_nonlocal_and_cross_site_requests(app, peer, host, headers):
    async with client(app, peer=peer, host=host, headers=headers) as http:
        for method, path, body in [
            ("GET", "/plugins/development", None),
            ("PUT", "/plugins/development/settings", {"enabled": True}),
            ("POST", "/plugins/development/registrations", {"source_dir": "C:/external/demo"}),
        ]:
            response = await http.request(method, path, json=body)
            assert response.status_code == 403
            assert response.headers["X-Error-Code"] == "DEVELOPMENT_ACCESS_DENIED"
    assert not store.development_enabled_sync()


@pytest.mark.asyncio
async def test_local_page_can_persist_settings_and_preview_without_loading(app, tmp_path):
    source = tmp_path / "中文 source" / "demo"
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text('[plugin]\nid="demo"\nname="Demo"\nentry="plugins.demo:Demo"\n', encoding="utf-8")
    (source / "__init__.py").write_text('raise RuntimeError("preview must not execute source")\nclass Demo: pass\n')
    async with client(app, headers={"X-Neko-Development": "1", "Origin": "http://localhost:48911"}) as http:
        response = await http.put("/plugins/development/settings", json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        response = await http.post("/plugins/development/registrations", json={"source_dir": str(source), "preview": True})
        assert response.status_code == 200
        assert response.json()["plugin_id"] == "demo"
        assert store.list_registration_records_sync() == []


@pytest.mark.asyncio
async def test_development_lifecycle_requires_current_reference_and_local_guard(app, tmp_path, monkeypatch):
    source = tmp_path / "demo"
    source.mkdir()
    (source / "plugin.toml").write_text('[plugin]\nid="demo"\nentry="plugins.demo:Demo"\n')
    (source / "__init__.py").write_text("class Demo: pass\n")
    store.set_enabled_sync(True)
    record = store.register_directory_sync(str(source))
    action = AsyncMock(return_value={"success": True})
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    monkeypatch.setattr(PluginLifecycleService, "start_plugin", action)
    async with client(app) as http:
        response = await http.post("/plugin/demo/start")
        assert response.status_code == 403
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        assert (await http.post("/plugin/demo/start")).status_code == 409
        query = {"registration_id": record.registration_id, "revision": record.revision + 1}
        assert (await http.post("/plugin/demo/start", params=query)).status_code == 409
        action.assert_not_awaited()
        query["revision"] = record.revision
        assert (await http.post("/plugin/demo/start", params=query)).status_code == 200
        action.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_all_cannot_bypass_development_origin_guard(app, monkeypatch):
    store.set_enabled_sync(True)
    from types import SimpleNamespace
    monkeypatch.setattr(routes, "list_registration_records_sync", lambda: [SimpleNamespace(plugin_id="demo")])
    action = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(routes.lifecycle_service, "reload_all_plugins", action)
    async with client(app) as http:
        assert (await http.post("/plugins/reload")).status_code == 403
    action.assert_not_awaited()
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        assert (await http.post("/plugins/reload")).status_code == 200
    action.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_ordinary_bulk_reload_remains_available_remotely(app, monkeypatch, registered):
    from types import SimpleNamespace
    store.set_enabled_sync(True)
    monkeypatch.setattr(routes, "list_registration_records_sync",
                        lambda: [SimpleNamespace(plugin_id="stopped_development")] if registered else [])
    action = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(routes.lifecycle_service, "reload_all_plugins", action)
    async with client(app, peer="192.168.1.2") as http:
        response = await http.post("/plugins/reload")
        assert response.status_code == (403 if registered else 200)
    if registered:
        action.assert_not_awaited()
    else:
        action.assert_awaited_once()


@pytest.mark.asyncio
async def test_development_routes_reuse_administrator_dependency(app):
    from fastapi import HTTPException
    from plugin.server.infrastructure.auth import verify_admin_code
    async def denied():
        raise HTTPException(status_code=403, detail="admin authorization required")
    app.dependency_overrides[verify_admin_code] = denied
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        response = await http.get("/plugins/development")
        assert response.status_code == 403
        assert response.json()["detail"] == "admin authorization required"
        response = await http.put("/plugins/development/settings", json={"enabled": True})
        assert response.status_code == 403
    assert not store.development_enabled_sync()


@pytest.mark.asyncio
async def test_remote_reload_cannot_publish_stopped_development_metadata(app, tmp_path, monkeypatch):
    from plugin.core.state import state
    from plugin.server.application.plugins import registry_service

    monkeypatch.setattr(registry_service, "PLUGIN_CONFIG_ROOTS", store.settings.PLUGIN_CONFIG_ROOTS)
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    source = tmp_path / "external" / "demo"
    source.mkdir(parents=True)
    manifest = source / "plugin.toml"
    manifest.write_text('[plugin]\nid="demo"\nname="Before"\nentry="plugins.demo:Demo"\n', encoding="utf-8")
    (source / "__init__.py").write_text("class Demo: pass\n", encoding="utf-8")
    store.set_enabled_sync(True)
    store.register_directory_sync(str(source))
    await registry_service.PluginRegistryService().refresh_registry()
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("Before", "After"), encoding="utf-8")

    async with client(app, peer="192.168.1.2", headers={"X-Neko-Development": "1"}) as http:
        response = await http.post("/plugins/reload")
    assert response.status_code == 403
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_ACCESS_DENIED"
    assert state.plugins["demo"]["name"] == "Before"
    # Exercise the actual lifecycle refresh, with no host stop/start mocks.
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        response = await http.post("/plugins/reload")
    assert response.status_code == 200, response.text
    assert response.json()["reloaded"] == []
    assert state.plugins["demo"]["name"] == "After"
