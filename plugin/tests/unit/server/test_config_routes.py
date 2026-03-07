from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import plugin.server.routes.config as config_route_module
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.exceptions import register_exception_handlers


@pytest.fixture
def config_routes_app() -> FastAPI:
    app = FastAPI(title="config-routes-unit-app")
    register_exception_handlers(app)
    app.include_router(config_route_module.router)
    return app


@pytest.fixture
async def config_routes_client(config_routes_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=config_routes_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload,service_obj,service_method",
    [
        ("GET", "/plugin/demo/config", None, "config_query_service", "get_plugin_config"),
        ("GET", "/plugin/demo/config/toml", None, "config_query_service", "get_plugin_config_toml"),
        ("PUT", "/plugin/demo/config", {"config": {}}, "config_command_service", "replace_plugin_config"),
        ("POST", "/plugin/demo/config/parse_toml", {"toml": "x=1"}, "config_query_service", "parse_toml_to_config"),
        ("POST", "/plugin/demo/config/render_toml", {"config": {}}, "config_query_service", "render_config_to_toml"),
        ("PUT", "/plugin/demo/config/toml", {"toml": "x=1"}, "config_command_service", "update_plugin_config_toml"),
        ("GET", "/plugin/demo/config/base", None, "config_query_service", "get_plugin_base_config"),
        ("GET", "/plugin/demo/config/profiles", None, "config_query_service", "get_plugin_profiles_state"),
        ("GET", "/plugin/demo/config/profiles/dev", None, "config_query_service", "get_plugin_profile_config"),
        ("PUT", "/plugin/demo/config/profiles/dev", {"config": {}, "make_active": True}, "config_command_service", "upsert_plugin_profile_config"),
        ("DELETE", "/plugin/demo/config/profiles/dev", None, "config_command_service", "delete_plugin_profile_config"),
        ("POST", "/plugin/demo/config/profiles/dev/activate", None, "config_command_service", "set_plugin_active_profile"),
        ("POST", "/plugin/demo/config/hot-update", {"config": {}, "mode": "temporary", "profile": None}, "config_command_service", "hot_update_plugin_config"),
    ],
)
async def test_config_routes_success_and_domain_error_mapping(
    config_routes_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    service_obj: str,
    service_method: str,
) -> None:
    service = getattr(config_route_module, service_obj)

    async def _ok(**kwargs):  # noqa: ANN003
        return {"ok": True}

    monkeypatch.setattr(service, service_method, _ok)
    resp_ok = await config_routes_client.request(method, path, json=payload)
    assert resp_ok.status_code == 200
    assert resp_ok.json() == {"ok": True}

    async def _fail(**kwargs):  # noqa: ANN003
        raise ServerDomainError(code="E", message="boom", status_code=409, details={})

    monkeypatch.setattr(service, service_method, _fail)
    resp_fail = await config_routes_client.request(method, path, json=payload)
    assert resp_fail.status_code == 409
    assert resp_fail.json()["detail"] == "boom"
