from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.main_server import web_app


@pytest.mark.asyncio
async def test_market_proxy_preserves_query_token_and_authorization(monkeypatch):
    seen: dict[str, object] = {}
    asgi_client = httpx.AsyncClient

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, content, headers):
            seen.update(method=method, url=url, content=content, headers=headers)
            return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    app = FastAPI()
    app.add_api_route(
        "/market/{path:path}",
        web_app.proxy_user_plugin_market_bridge,
        methods=["POST"],
    )
    async with asgi_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48911",
    ) as client:
        response = await client.post(
            "/market/oauth/start?token=query-token",
            headers={"Authorization": "Bearer header-token", "Origin": "http://localhost:48911"},
            content=b"{}",
        )

    assert response.status_code == 200
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:48916/market/oauth/start?token=query-token"
    assert seen["content"] == b"{}"
    forwarded_headers = seen["headers"]
    assert isinstance(forwarded_headers, dict)
    assert forwarded_headers["authorization"] == "Bearer header-token"
    assert forwarded_headers["origin"] == "http://localhost:48911"
