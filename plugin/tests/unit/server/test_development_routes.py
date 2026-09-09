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
    action = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(routes.lifecycle_service, "reload_all_plugins", action)
    async with client(app) as http:
        assert (await http.post("/plugins/reload")).status_code == 403
    action.assert_not_awaited()
    async with client(app, headers={"X-Neko-Development": "1"}) as http:
        assert (await http.post("/plugins/reload")).status_code == 200
    action.assert_awaited_once()
