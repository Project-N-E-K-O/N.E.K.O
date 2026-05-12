from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from main_routers import actions_proxy_router as route_module


@pytest.fixture(autouse=True)
def _reset_actions_proxy_cache() -> None:
    route_module._USER_PLUGIN_BASE_CACHE = ("", 0.0)


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(route_module, "_resolve_user_plugin_base", _fake_resolve_base)
    app = FastAPI()
    app.include_router(route_module.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _fake_resolve_base() -> str:
    return "http://plugin.test"


class _FakePluginClient:
    requests: list[tuple[str, str, dict[str, Any]]] = []
    fail: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakePluginClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("plugin down")
        self.requests.append(("GET", url, kwargs))
        if url.endswith("/chat/actions/preferences"):
            return httpx.Response(200, json={"pinned": ["demo"], "hidden": [], "recent": []})
        return httpx.Response(
            200,
            json={
                "actions": [{"action_id": "demo:greet", "label": "Greet"}],
                "preferences": {"pinned": [], "hidden": [], "recent": []},
            },
        )

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("plugin down")
        self.requests.append(("POST", url, kwargs))
        if url.endswith("/execute"):
            return httpx.Response(200, json={"success": True, "message": "ok"})
        return httpx.Response(200, json=kwargs.get("json") or {})


@pytest.fixture
def fake_plugin_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakePluginClient]:
    _FakePluginClient.requests = []
    _FakePluginClient.fail = False
    monkeypatch.setattr(route_module.httpx, "AsyncClient", _FakePluginClient)
    return _FakePluginClient


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_chat_actions_forwards_plugin_filter(
    client: AsyncClient,
    fake_plugin_client: type[_FakePluginClient],
) -> None:
    response = await client.get("/chat/actions", params={"plugin_id": "demo"})

    assert response.status_code == 200
    assert response.json()["actions"][0]["action_id"] == "demo:greet"
    assert fake_plugin_client.requests == [
        ("GET", "http://plugin.test/chat/actions", {"params": {"plugin_id": "demo"}})
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preferences_route_is_not_captured_as_action_id(
    client: AsyncClient,
    fake_plugin_client: type[_FakePluginClient],
) -> None:
    response = await client.get("/chat/actions/preferences")

    assert response.status_code == 200
    assert response.json()["pinned"] == ["demo"]
    assert fake_plugin_client.requests == [
        ("GET", "http://plugin.test/chat/actions/preferences", {})
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_forwards_path_action_id(
    client: AsyncClient,
    fake_plugin_client: type[_FakePluginClient],
) -> None:
    response = await client.post(
        "/chat/actions/system:demo:toggle/execute",
        json={"value": True},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake_plugin_client.requests == [
        (
            "POST",
            "http://plugin.test/chat/actions/system:demo:toggle/execute",
            {"json": {"value": True}},
        )
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_degrades_to_empty_when_plugin_server_is_down(
    client: AsyncClient,
    fake_plugin_client: type[_FakePluginClient],
) -> None:
    fake_plugin_client.fail = True

    response = await client.get("/chat/actions")

    assert response.status_code == 200
    assert response.json() == {
        "actions": [],
        "preferences": {"pinned": [], "hidden": [], "recent": []},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_returns_bad_gateway_when_plugin_server_is_down(
    client: AsyncClient,
    fake_plugin_client: type[_FakePluginClient],
) -> None:
    fake_plugin_client.fail = True

    response = await client.post("/chat/actions/demo:greet/execute", json={})

    assert response.status_code == 502
    assert response.json()["success"] is False
