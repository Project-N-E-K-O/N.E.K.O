import asyncio

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_shutdown_route_requires_owner_token(monkeypatch):
    from app.main_server import web_app

    shutdown = AsyncMock()
    monkeypatch.setenv("NEKO_RUNTIME_SHUTDOWN_TOKEN", "owner-secret")
    monkeypatch.setattr(web_app.runtime, "request_application_shutdown_async", shutdown)

    request = SimpleNamespace(
        headers={
            "x-neko-runtime-shutdown-token": "not-the-owner",
        }
    )
    response = await web_app.runtime_shutdown(request)

    assert response.status_code == 403
    shutdown.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_shutdown_route_delegates_to_application_shutdown(monkeypatch):
    from app.main_server import web_app
    from config import INSTANCE_ID

    shutdown = AsyncMock()
    monkeypatch.setenv("NEKO_RUNTIME_SHUTDOWN_TOKEN", "owner-secret")
    monkeypatch.setattr(web_app.runtime, "request_application_shutdown_async", shutdown)

    request = SimpleNamespace(
        headers={
            "x-neko-runtime-shutdown-token": "owner-secret",
            "x-neko-instance-id": str(INSTANCE_ID),
        }
    )
    response = await web_app.runtime_shutdown(request)
    await asyncio.sleep(0)

    assert response.status_code == 202
    shutdown.assert_awaited_once_with(reason="desktop_owner_exit")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_shutdown_route_rejects_different_instance(monkeypatch):
    from app.main_server import web_app

    shutdown = AsyncMock()
    monkeypatch.setenv("NEKO_RUNTIME_SHUTDOWN_TOKEN", "owner-secret")
    monkeypatch.setattr(web_app.runtime, "request_application_shutdown_async", shutdown)

    request = SimpleNamespace(
        headers={
            "x-neko-runtime-shutdown-token": "owner-secret",
            "x-neko-instance-id": "another-instance",
        }
    )
    response = await web_app.runtime_shutdown(request)

    assert response.status_code == 409
    shutdown.assert_not_called()
