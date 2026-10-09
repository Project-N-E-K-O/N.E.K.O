"""``/market/knowledge/*``: same-origin bridge from the manager page to Main."""

import types

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.server.routes import knowledge_bridge
from plugin.server.routes.market_bridge import get_bridge_token


pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def bridge(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    fake_httpx = types.SimpleNamespace(
        AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
        Timeout=httpx.Timeout,
        TimeoutException=httpx.TimeoutException,
        HTTPError=httpx.HTTPError,
    )

    async def ensure_httpx():
        return fake_httpx

    monkeypatch.setattr(knowledge_bridge, "ensure_httpx", ensure_httpx)
    app = FastAPI()
    app.include_router(knowledge_bridge.router)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client, seen


def test_requires_the_bridge_token(bridge):
    client, seen = bridge
    assert client.get("/market/knowledge/status", params={"token": "wrong"}).status_code == 403
    assert seen == []


def test_only_allowlisted_paths_are_forwarded(bridge):
    client, seen = bridge
    token = get_bridge_token()
    assert client.get("/market/knowledge/query", params={"token": token}).status_code == 404
    assert client.post("/market/knowledge/status", params={"token": token}, json={}).status_code == 404
    assert seen == []


def test_reads_drop_the_token_and_reach_main(bridge):
    client, seen = bridge
    response = client.get("/market/knowledge/entries", params={"token": get_bridge_token(), "query": "猫"})
    assert response.json() == {"ok": True, "path": "/api/public-knowledge/entries"}
    assert "token" not in seen[0].url.params
    assert seen[0].url.params["query"] == "猫"


def test_writes_carry_main_csrf_and_stream_the_body(bridge, monkeypatch):
    import config

    monkeypatch.setattr(config, "AUTOSTART_CSRF_TOKEN", "csrf-for-test", raising=False)
    client, seen = bridge
    body = b'{"schema_version": 1}'
    response = client.post(
        "/market/knowledge/packs/import",
        params={"token": get_bridge_token()},
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    forwarded = seen[0]
    assert forwarded.headers["X-CSRF-Token"] == "csrf-for-test"
    assert forwarded.headers["Origin"].startswith("http://127.0.0.1:")
    assert forwarded.content == body


def test_oversized_bodies_are_refused(bridge, monkeypatch):
    client, seen = bridge
    monkeypatch.setitem(knowledge_bridge._WRITE_PATHS, "packs/remove", 8)
    response = client.post(
        "/market/knowledge/packs/remove", params={"token": get_bridge_token()}, content=b"x" * 64
    )
    assert response.status_code == 413
    assert seen == []
