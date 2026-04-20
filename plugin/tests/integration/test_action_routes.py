"""Integration tests for /chat/actions routes.

Uses httpx + ASGI transport to test the full request/response cycle
through FastAPI, including error mapping and serialization.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.domain.action_models import ActionDescriptor, ActionExecuteResponse
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import verify_admin_code
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.actions import router


# ── Fakes ────────────────────────────────────────────────────────────

_FAKE_ACTIONS: list[ActionDescriptor] = [
    ActionDescriptor(
        action_id="system:demo:toggle",
        type="instant",
        label="Demo",
        category="系统",
        plugin_id="demo",
        control="plugin_lifecycle",
        current_value=True,
    ),
    ActionDescriptor(
        action_id="demo:settings:volume",
        type="instant",
        label="Volume",
        category="Demo",
        plugin_id="demo",
        control="slider",
        current_value=50,
        min=0,
        max=100,
    ),
    ActionDescriptor(
        action_id="demo:greet",
        type="chat_inject",
        label="Greet",
        category="Demo",
        plugin_id="demo",
        inject_text="@Demo /greet",
    ),
]


class _FakeAggregationService:
    async def aggregate_actions(self, plugin_id: str | None = None) -> list[ActionDescriptor]:
        if plugin_id:
            return [a for a in _FAKE_ACTIONS if a.plugin_id == plugin_id]
        return list(_FAKE_ACTIONS)


class _FakeExecutionService:
    async def execute(self, action_id: str, value: object = None) -> ActionExecuteResponse:
        if action_id == "system:demo:toggle":
            new_val = not bool(value) if isinstance(value, bool) else True
            return ActionExecuteResponse(
                success=True,
                action=ActionDescriptor(
                    action_id=action_id,
                    type="instant",
                    label="Demo",
                    category="系统",
                    plugin_id="demo",
                    control="plugin_lifecycle",
                    current_value=new_val,
                ),
                message="toggled",
            )
        if action_id == "bad:action":
            raise ServerDomainError(
                code="ACTION_NOT_FOUND",
                message="not found",
                status_code=404,
                details={"action_id": action_id},
            )
        return ActionExecuteResponse(success=True, message="ok")


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def actions_test_app() -> FastAPI:
    app = FastAPI(title="actions-test")
    register_exception_handlers(app)
    app.dependency_overrides[verify_admin_code] = lambda: "test"
    app.include_router(router)
    return app


@pytest.fixture
async def client(actions_test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    import plugin.server.routes.actions as route_module

    monkeypatch.setattr(route_module, "aggregation_service", _FakeAggregationService())
    monkeypatch.setattr(route_module, "execution_service", _FakeExecutionService())

    transport = ASGITransport(app=actions_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ── GET /chat/actions ────────────────────────────────────────────────

@pytest.mark.plugin_integration
@pytest.mark.asyncio
class TestGetChatActions:
    async def test_returns_all_actions(self, client: AsyncClient) -> None:
        resp = await client.get("/chat/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["actions"]) == 3
        assert isinstance(data["categories"], list)
        assert "系统" in data["categories"]

    async def test_filter_by_plugin_id(self, client: AsyncClient) -> None:
        resp = await client.get("/chat/actions", params={"plugin_id": "demo"})
        assert resp.status_code == 200
        data = resp.json()
        assert all(a["plugin_id"] == "demo" for a in data["actions"])

    async def test_action_fields_serialized(self, client: AsyncClient) -> None:
        resp = await client.get("/chat/actions")
        data = resp.json()
        lifecycle = next(a for a in data["actions"] if a["control"] == "plugin_lifecycle")
        assert lifecycle["current_value"] is True
        assert lifecycle["action_id"] == "system:demo:toggle"

        slider = next(a for a in data["actions"] if a["control"] == "slider")
        assert slider["min"] == 0
        assert slider["max"] == 100

        inject = next(a for a in data["actions"] if a["type"] == "chat_inject")
        assert inject["inject_text"] == "@Demo /greet"

    async def test_none_fields_excluded(self, client: AsyncClient) -> None:
        resp = await client.get("/chat/actions")
        data = resp.json()
        lifecycle = next(a for a in data["actions"] if a["control"] == "plugin_lifecycle")
        assert "inject_text" not in lifecycle
        assert "target" not in lifecycle


# ── POST /chat/actions/{action_id}/execute ───────────────────────────

@pytest.mark.plugin_integration
@pytest.mark.asyncio
class TestExecuteChatAction:
    async def test_execute_toggle(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/chat/actions/system:demo:toggle/execute",
            json={"value": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["action"]["control"] == "plugin_lifecycle"
        assert data["message"] == "toggled"

    async def test_execute_with_null_value(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/chat/actions/demo:settings:volume/execute",
            json={"value": None},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_execute_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/chat/actions/bad:action/execute",
            json={"value": None},
        )
        assert resp.status_code == 404

    async def test_execute_empty_body(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/chat/actions/system:demo:toggle/execute",
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
