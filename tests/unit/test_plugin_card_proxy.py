import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from main_routers import plugin_card_router as cards


@pytest.mark.asyncio
async def test_proxy_rejects_cross_site_action_before_internal_post(monkeypatch):
    app = FastAPI()
    app.include_router(cards.router)
    posted = False

    class Client:
        async def post(self, *args, **kwargs):
            nonlocal posted
            posted = True
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(cards, "get_internal_http_client", lambda: Client())
    # The shared helper owns the token; patch its source rather than relying
    # on process-level configuration in this route-level test.
    from main_routers.system_router import _shared
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-token")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48911",
    ) as client:
        response = await client.post(
            "/api/plugin-cards/demo/action/play",
            headers={
                "Origin": "https://attacker.example",
                "X-CSRF-Token": "test-token",
            },
            json={"card_id": "one", "target_lanlan": "Alice"},
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    assert posted is False


@pytest.mark.parametrize("presentation", ["chat", "agent"])
def test_proxy_forwards_to_actual_plugin_server_and_preserves_errors(monkeypatch, presentation):
    captured = []

    class Client:
        async def post(self, url, **kwargs):
            captured.append((url, kwargs))
            return httpx.Response(409, json={"detail": {"message": "Plugin stopped"}})

    monkeypatch.setattr(cards, "resolve_user_plugin_base", lambda: "http://127.0.0.1:59999")
    monkeypatch.setattr(cards, "get_internal_http_client", lambda: Client())
    response = asyncio.run(cards.card_action("demo", "play", cards.CardActionRequest(
        card_id="one", target_lanlan="Alice", args={"id": 1}, presentation=presentation,
    )))
    assert response.status_code == 409
    assert json.loads(response.body) == {"detail": {"message": "Plugin stopped"}}
    assert captured[0][0] == "http://127.0.0.1:59999/plugin/demo/chat-card/action/play"
    assert captured[0][1]["json"]["target_lanlan"] == "Alice"
    assert captured[0][1]["json"]["presentation"] == presentation


@pytest.mark.parametrize("failure, status, code", [
    (httpx.ReadTimeout("timeout"), 504, "plugin_card_action_timeout"),
    (httpx.ConnectError("connection refused"), 502, "plugin_card_server_unavailable"),
])
def test_proxy_returns_localizable_error_codes(monkeypatch, failure, status, code):
    class Client:
        async def post(self, *args, **kwargs):
            raise failure

    monkeypatch.setattr(cards, "get_internal_http_client", lambda: Client())
    with pytest.raises(cards.HTTPException) as error:
        asyncio.run(cards.card_action("demo", "play", cards.CardActionRequest(card_id="one", target_lanlan="Alice")))
    assert error.value.status_code == status
    assert error.value.detail == {"code": code}
