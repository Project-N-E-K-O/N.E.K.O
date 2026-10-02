from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from plugin.server.infrastructure import mutation_auth
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes import runs as runs_route_module


pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(runs_route_module.router)
    return app


def _client(
    app: FastAPI,
    *,
    peer: str = "127.0.0.1",
    host: str = "127.0.0.1:48916",
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/runs"),
        ("post", "/runs/run-1/uploads"),
        ("put", "/uploads/upload-1"),
        ("post", "/runs/run-1/cancel"),
    ],
)
async def test_foreign_origin_is_rejected_before_runs_side_effects(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    create_upload = AsyncMock()
    upload_blob = AsyncMock()
    cancel_run = AsyncMock()
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)
    monkeypatch.setattr(runs_route_module.run_service, "create_upload_session", create_upload)
    monkeypatch.setattr(runs_route_module.run_service, "upload_blob", upload_blob)
    monkeypatch.setattr(runs_route_module.run_service, "cancel_run", cancel_run)

    # Invalid JSON is intentional: the pre-body guard must reject the request
    # before FastAPI tries to parse it or reaches the service.
    async with _client(
        app,
        headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
    ) as client:
        response = await getattr(client, method)(path, content=b"not-json")

    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "csrf_validation_failed"
    create_run.assert_not_awaited()
    create_upload.assert_not_awaited()
    upload_blob.assert_not_awaited()
    cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_origin_and_token_reach_create_run(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(app, headers=_valid_headers()) as client:
        response = await client.post(
            "/runs",
            json={"plugin_id": "demo", "entry_id": "run", "args": {}},
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == "r1"
    create_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_originless_loopback_native_create_remains_supported(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(app) as client:
        response = await client.post(
            "/runs",
            json={"plugin_id": "demo", "entry_id": "run", "args": {}},
        )

    assert response.status_code == 200
    create_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_originless_lan_peer_and_foreign_host_are_rejected(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    # LAN/NAS browsers must prove provenance with Origin; only loopback peers
    # may use the originless native path, even with a valid token.
    lan_headers = {"X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN}
    async with _client(app, peer="192.168.1.10", host="192.168.1.5:48916", headers=lan_headers) as client:
        response = await client.post(
            "/runs",
            json={"plugin_id": "demo", "entry_id": "run", "args": {}},
        )
    assert response.status_code == 403
    create_run.assert_not_awaited()
    async with _client(app, host="attacker.example:48916", headers=_valid_headers()) as client:
        response = await client.post(
            "/runs",
            json={"plugin_id": "demo", "entry_id": "run", "args": {}},
        )
    assert response.status_code == 403
    create_run.assert_not_awaited()


# Market-plugin compatibility contract (owner decision): published plugin
# pages post without X-CSRF-Token, so a trusted Origin alone must keep working.
# The token is an opt-in for public deployments only.
_RUN_PAYLOAD = {"plugin_id": "demo", "entry_id": "run", "args": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("peer", "host", "origin"),
    [
        ("127.0.0.1", "127.0.0.1:48916", f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}"),
        ("127.0.0.1", "127.0.0.1:48916", "http://127.0.0.1:48916"),
        ("192.168.1.10", "192.168.1.5:48916", "http://192.168.1.5:48916"),
    ],
    ids=["desktop-main-page", "desktop-plugin-page", "nas-same-origin-page"],
)
async def test_tokenless_plugin_page_from_trusted_origin_keeps_working(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, peer: str, host: str, origin: str,
) -> None:
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(app, peer=peer, host=host, headers={"Origin": origin}) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)

    assert response.status_code == 200, response.text
    create_run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["wrong", ""], ids=["wrong", "empty"])
async def test_plugin_page_supplied_invalid_token_is_rejected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, token: str,
) -> None:
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    create_run = AsyncMock()
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    headers = {"Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}", "X-CSRF-Token": token}
    async with _client(app, headers=headers) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)

    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "token"
    create_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true"])
async def test_public_deployment_can_require_plugin_page_token(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, value)
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    origin = {"Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}"}
    async with _client(app, headers=origin) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "token"
    create_run.assert_not_awaited()

    async with _client(app, headers=_valid_headers()) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 200
    create_run.assert_awaited_once()


def test_page_and_strict_guards_cover_the_intended_routes() -> None:
    """Pin which routers stay market-compatible and which require the token."""
    from plugin.server.routes import config, model_config, plugin_cli, plugin_install, plugin_ui

    page_modules = (runs_route_module, config, model_config, plugin_install, plugin_ui)
    for module in page_modules:
        guarded = [route for route in module.router.routes
                   if isinstance(route, mutation_auth.PluginMutationGuardedRoute)]
        assert guarded, module.__name__
        assert all(isinstance(route, mutation_auth.PluginPageMutationGuardedRoute) for route in guarded), module.__name__
    cli_guarded = [route for route in plugin_cli.router.routes
                   if isinstance(route, mutation_auth.PluginMutationGuardedRoute)]
    assert cli_guarded
    assert not any(isinstance(route, mutation_auth.PluginPageMutationGuardedRoute) for route in cli_guarded)


@pytest.mark.asyncio
async def test_other_port_on_same_nas_hostname_needs_the_token(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another app on the NAS passes the hostname-only fallback but cannot read
    # the token cross-origin, so tokenless writes from it must stay rejected.
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)
    other_port = {"Origin": "http://192.168.1.5:8080", "Sec-Fetch-Site": "same-site"}

    async with _client(app, peer="192.168.1.10", host="192.168.1.5:48916", headers=other_port) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "token"
    create_run.assert_not_awaited()

    with_token = {**other_port, "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN}
    async with _client(app, peer="192.168.1.10", host="192.168.1.5:48916", headers=with_token) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 200
    create_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_tokenless_page_behind_outer_tls_proxy_keeps_working(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Outer TLS termination changes scheme/port, so only the hostname matches;
    # over HTTPS the browser reports the market plugin page as same-origin.
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)
    headers = {"Origin": "https://192.168.1.5", "Sec-Fetch-Site": "same-origin"}

    async with _client(app, peer="192.168.1.10", host="192.168.1.5:48916", headers=headers) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 200, response.text
    create_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_cross_port_page_without_fetch_metadata_is_rejected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Browsers omit Sec-Fetch-Site for plain-HTTP LAN origins, so a missing
    # header must not count as same-origin on the hostname-only fallback.
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    create_run = AsyncMock()
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(
        app, peer="192.168.1.10", host="192.168.1.5:48916", headers={"Origin": "http://192.168.1.5:8080"},
    ) as client:
        response = await client.post("/runs", content=b'{"plugin_id": "demo", "entry_id": "run", "args": {}}')
    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "token"
    create_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicitly_allowed_proxy_origin_needs_no_fetch_metadata(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A custom proxy that rewrites Host can list the page origin explicitly.
    monkeypatch.delenv(mutation_auth.PAGE_MUTATION_REQUIRE_TOKEN_ENV, raising=False)
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS", "http://192.168.1.5:8080")
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(
        app, peer="192.168.1.10", host="192.168.1.5:48916", headers={"Origin": "http://192.168.1.5:8080"},
    ) as client:
        response = await client.post("/runs", json=_RUN_PAYLOAD)
    assert response.status_code == 200, response.text
    create_run.assert_awaited_once()
