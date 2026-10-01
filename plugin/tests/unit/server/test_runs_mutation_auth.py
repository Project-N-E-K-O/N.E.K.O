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
async def test_non_loopback_origin_and_host_are_rejected(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_run = AsyncMock(return_value={"run_id": "r1", "status": "queued"})
    monkeypatch.setattr(runs_route_module.run_service, "create_run", create_run)

    async with _client(app, peer="192.168.1.10", headers=_valid_headers()) as client:
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
