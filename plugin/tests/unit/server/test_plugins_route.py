from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes import plugins as route_module


pytestmark = pytest.mark.plugin_unit


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
    async def _refresh_registry() -> dict[str, object]:
        return {"success": True, "added": ["demo"], "updated": [], "removed": []}

    async def _refresh_plugin(plugin_id: str) -> dict[str, object]:
        return {"success": True, "plugin_id": plugin_id, "status": "updated"}

    monkeypatch.setattr(route_module.registry_service, "refresh_registry", _refresh_registry)
    monkeypatch.setattr(route_module.registry_service, "refresh_plugin", _refresh_plugin)

    transport = ASGITransport(app=plugin_route_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        all_response = await client.post("/plugins/refresh")
        assert all_response.status_code == 200
        assert all_response.json()["added"] == ["demo"]

        one_response = await client.post("/plugin/demo/refresh")
        assert one_response.status_code == 200
        assert one_response.json()["plugin_id"] == "demo"
