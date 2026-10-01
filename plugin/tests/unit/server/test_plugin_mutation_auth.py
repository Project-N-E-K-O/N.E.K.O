from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from plugin.server.infrastructure import mutation_auth
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes import plugins as route_module
from plugin.server.routes.security import router as security_router


pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Small app that uses the production routes and the real access guard."""
    monkeypatch.setattr(route_module, "registration_for_plugin_sync", lambda _plugin_id: None)
    monkeypatch.setattr(route_module, "list_registration_records_sync", lambda: [])
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(route_module.router)
    app.include_router(security_router)
    return app


def _client(
    app: FastAPI,
    *,
    peer: str = "127.0.0.1",
    host: str = "127.0.0.1:48916",
    headers: dict[str, str] | None = None,
):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url=f"http://{host}",
        headers=headers or {},
    )


def _valid_headers() -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}",
        "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
    }


def test_non_ascii_token_is_rejected_without_compare_digest_error() -> None:
    request = SimpleNamespace(headers={"X-CSRF-Token": "é"})
    assert mutation_auth._valid_token(request) is False


LIFECYCLE_MUTATIONS = [
    ("post", "/plugin/demo/start"),
    ("post", "/plugin/demo/stop"),
    ("post", "/plugin/demo/refresh"),
    ("post", "/plugin/demo/reload"),
    ("delete", "/plugin/demo"),
    ("post", "/plugins/refresh"),
    ("post", "/plugins/reload"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", LIFECYCLE_MUTATIONS)
async def test_foreign_origin_is_rejected_before_lifecycle_side_effects(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(route_module, "ensure_plugin_messaging_started", ensure)
    for service in (route_module.lifecycle_service, route_module.registry_service):
        for name in (
            "start_plugin", "stop_plugin", "reload_plugin", "delete_plugin",
            "refresh_plugin", "refresh_registry", "reload_all_plugins",
        ):
            if hasattr(service, name):
                monkeypatch.setattr(service, name, AsyncMock())

    headers = {**_valid_headers(), "Origin": "https://evil.example"}
    async with _client(app, headers=headers) as client:
        response = await getattr(client, method)(path)

    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "csrf_validation_failed"
    ensure.assert_not_awaited()
    for service in (route_module.lifecycle_service, route_module.registry_service):
        for name in (
            "start_plugin", "stop_plugin", "reload_plugin", "delete_plugin",
            "refresh_plugin", "refresh_registry", "reload_all_plugins",
        ):
            candidate = getattr(service, name, None)
            if isinstance(candidate, AsyncMock):
                candidate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {"Origin": "not a URL", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {"Origin": "http://127.0.0.1:49999", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {
            "Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}/path",
            "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
        },
        {"Referer": "https://evil.example/page", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
    ],
)
async def test_invalid_browser_provenance_is_rejected(app: FastAPI, headers: dict[str, str]) -> None:
    async with _client(app, headers=headers) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "csrf_validation_failed"
    assert response.json()["detail"]["error_code"] == "csrf_validation_failed"


@pytest.mark.asyncio
async def test_valid_origin_and_token_reach_lifecycle_service(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    monkeypatch.setattr(route_module, "ensure_plugin_messaging_started", ensure)
    async with _client(app, headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_bootstrap_is_uncached_and_rejects_foreign_origin(app: FastAPI) -> None:
    async with _client(app, headers={"Origin": "https://evil.example"}) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 403

    async with _client(app, headers={"Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}"}) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 200
    assert response.json()["csrf_token"] == mutation_auth.AUTOSTART_CSRF_TOKEN
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_token_bootstrap_accepts_same_origin_referer_with_path(app: FastAPI) -> None:
    async with _client(
        app,
        headers={"Referer": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}/ui/plugins"},
    ) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 200
    assert response.json()["csrf_token"] == mutation_auth.AUTOSTART_CSRF_TOKEN


@pytest.mark.asyncio
async def test_token_bootstrap_rejects_referer_only_foreign_request(app: FastAPI) -> None:
    async with _client(
        app,
        headers={"Referer": "https://evil.example/page"},
    ) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, mutation_auth.AUTOSTART_CSRF_TOKEN])
async def test_originless_loopback_native_call_with_optional_token_remains_supported(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    headers = {} if token is None else {"X-CSRF-Token": token}
    async with _client(
        app,
        headers=headers,
    ) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)


@pytest.mark.asyncio
async def test_originless_loopback_native_call_without_browser_metadata_is_supported(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    async with _client(app) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)


@pytest.mark.asyncio
async def test_loopback_and_host_are_required(app: FastAPI) -> None:
    async with _client(app, peer="192.168.1.10", headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403

    async with _client(app, host="example.test:48916", headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403
