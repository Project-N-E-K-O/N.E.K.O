from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.infrastructure.mutation_auth import AUTOSTART_CSRF_TOKEN, MAIN_SERVER_PORT
from plugin.server.routes import plugins as route_module


pytestmark = pytest.mark.plugin_unit


def _mutation_headers() -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{MAIN_SERVER_PORT}",
        "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
    }


@pytest.fixture
def plugin_route_test_app() -> FastAPI:
    app = FastAPI(title="plugin-route-test-app")
    register_exception_handlers(app)
    app.include_router(route_module.router)
    return app


@pytest.mark.asyncio
async def test_plugins_refresh_routes_delegate_to_registry_service(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls: list[str] = []

    async def _refresh_registry() -> dict[str, object]:
        refresh_calls.append("all")
        return {"success": True, "added": ["demo"], "updated": [], "removed": []}

    async def _refresh_plugin(plugin_id: str) -> dict[str, object]:
        refresh_calls.append(plugin_id)
        return {"success": True, "plugin_id": plugin_id, "status": "updated"}

    monkeypatch.setattr(route_module.registry_service, "refresh_registry", _refresh_registry)
    monkeypatch.setattr(route_module.registry_service, "refresh_plugin", _refresh_plugin)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:48916",
        headers=_mutation_headers(),
    ) as client:
        all_response = await client.post("/plugins/refresh")
        assert all_response.status_code == 200
        assert all_response.json()["added"] == ["demo"]
        # force 参数没有了：刷新不再有缓存可绕过，每次都重读盘面。签名里留一个
        # 恒为真的开关只会让人以为还存在一条"不重读"的路。
        assert refresh_calls == ["all"], f"刷新路由没有调到注册表：{refresh_calls}"

        one_response = await client.post("/plugin/demo/refresh")
        assert one_response.status_code == 200
        assert one_response.json()["plugin_id"] == "demo"
        assert refresh_calls == ["all", "demo"], (
            f"单插件刷新没有把插件 id 传下去：{refresh_calls}"
        )


@pytest.mark.asyncio
async def test_plugins_list_route_forwards_summary_query(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, bool]] = []

    async def _list_plugins(*, locale: str | None = None, summary: bool = False) -> dict[str, object]:
        calls.append((locale, summary))
        return {"plugins": [], "message": ""}

    monkeypatch.setattr(route_module.query_service, "list_plugins", _list_plugins)
    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugins?locale=ja&summary=true")
    assert response.status_code == 200
    assert calls == [("ja", True)]


@pytest.mark.asyncio
async def test_single_plugin_route_delegates_to_detail_query(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def _get_plugin(plugin_id: str, *, locale: str | None = None) -> dict[str, object]:
        calls.append((plugin_id, locale))
        return {"plugin": {"id": plugin_id}}

    monkeypatch.setattr(route_module.query_service, "get_plugin", _get_plugin)
    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugins/demo?locale=zh-CN")
    assert response.status_code == 200
    assert response.json() == {"plugin": {"id": "demo"}}
    assert calls == [("demo", "zh-CN")]


@pytest.mark.asyncio
async def test_delete_plugin_route_delegates_to_lifecycle_service(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _delete_plugin(plugin_id: str) -> dict[str, object]:
        return {"success": True, "plugin_id": plugin_id, "message": "deleted"}

    monkeypatch.setattr(route_module.lifecycle_service, "delete_plugin", _delete_plugin)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:48916",
        headers=_mutation_headers(),
    ) as client:
        response = await client.delete("/plugin/demo")
        assert response.status_code == 200
        assert response.json()["plugin_id"] == "demo"


@pytest.mark.asyncio
async def test_delete_plugin_route_preserves_ownership_error_code(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _delete_plugin(_plugin_id: str) -> dict[str, object]:
        raise ServerDomainError(
            code="PLUGIN_MANUAL_NOT_MANAGED",
            message="manual plugin is not managed",
            status_code=409,
        )

    monkeypatch.setattr(route_module.lifecycle_service, "delete_plugin", _delete_plugin)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:48916",
        headers=_mutation_headers(),
    ) as client:
        response = await client.delete("/plugin/demo")

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "PLUGIN_MANUAL_NOT_MANAGED"
    assert response.json() == {"detail": "manual plugin is not managed"}


@pytest.mark.asyncio
async def test_stop_plugin_route_persists_user_intent(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    async def _stop_plugin(plugin_id: str, *, persist_user_intent: bool = False) -> dict[str, object]:
        calls.append((plugin_id, persist_user_intent))
        return {"success": True, "plugin_id": plugin_id, "message": "stopped"}

    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", _stop_plugin)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:48916",
        headers=_mutation_headers(),
    ) as client:
        response = await client.post("/plugin/demo/stop")

    assert response.status_code == 200
    assert response.json()["plugin_id"] == "demo"
    assert calls == [("demo", True)]


@pytest.mark.asyncio
async def test_auto_start_route_writes_preference_without_lifecycle_calls(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    async def _set_plugin_auto_start(plugin_id: str, auto_start: bool) -> dict[str, object]:
        calls.append((plugin_id, auto_start))
        return {"success": True, "plugin_id": plugin_id, "auto_start": auto_start, "message": "ok"}

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("auto-start route must not start or stop the plugin")

    monkeypatch.setattr(route_module, "registration_for_plugin_sync", lambda _pid: None)
    monkeypatch.setattr(route_module.lifecycle_service, "set_plugin_auto_start", _set_plugin_auto_start)
    monkeypatch.setattr(route_module.lifecycle_service, "start_plugin", _must_not_run)
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", _must_not_run)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:48916", headers=_mutation_headers()) as client:
        response = await client.put("/plugin/demo/auto-start", json={"auto_start": False})
        invalid = await client.put("/plugin/demo/auto-start", json={})

    assert response.status_code == 200
    assert response.json()["auto_start"] is False
    assert calls == [("demo", False)]
    assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": f"http://127.0.0.1:{MAIN_SERVER_PORT}"},
])
async def test_auto_start_route_rejects_untrusted_or_tokenless_browser(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("unauthorized request must not mutate auto-start")

    monkeypatch.setattr(route_module.lifecycle_service, "set_plugin_auto_start", _must_not_run)
    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:48916") as client:
        response = await client.put("/plugin/demo/auto-start", json={"auto_start": True}, headers=headers)
    assert response.status_code == 403
    assert response.headers["X-Error-Code"] == "csrf_validation_failed"


@pytest.mark.asyncio
async def test_auto_start_route_rejects_development_plugins(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("development plugins must not get an auto-start preference")

    monkeypatch.setattr(route_module, "registration_for_plugin_sync", lambda _pid: object())
    monkeypatch.setattr(route_module.lifecycle_service, "set_plugin_auto_start", _must_not_run)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:48916", headers=_mutation_headers()) as client:
        response = await client.put("/plugin/demo/auto-start", json={"auto_start": True})

    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_AUTO_START_UNSUPPORTED"


@pytest.mark.asyncio
async def test_auto_start_route_preserves_domain_error_shape(
    plugin_route_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _set_plugin_auto_start(_plugin_id: str, _auto_start: bool) -> dict[str, object]:
        raise ServerDomainError(code="PLUGIN_NOT_FOUND", message="Plugin 'demo' not found", status_code=404)

    monkeypatch.setattr(route_module, "registration_for_plugin_sync", lambda _pid: None)
    monkeypatch.setattr(route_module.lifecycle_service, "set_plugin_auto_start", _set_plugin_auto_start)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:48916", headers=_mutation_headers()) as client:
        response = await client.put("/plugin/demo/auto-start", json={"auto_start": True})

    assert response.status_code == 404
    assert response.headers["X-Error-Code"] == "PLUGIN_NOT_FOUND"
    assert response.json() == {"detail": "Plugin 'demo' not found"}
