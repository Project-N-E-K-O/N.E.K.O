import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.server.routes import theater_market as bridge
from plugin.server.routes.market_bridge import get_bridge_token


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bridge, "MARKET_ORIGINS", ["https://market.example.test"])
    app = FastAPI()
    app.include_router(bridge.router)
    return TestClient(app)


def test_bridge_requires_pairing_and_limits_operations(client):
    path = "/market/theater/launch/selection"
    assert client.get(path).status_code == 403
    headers = {"Authorization": "Bearer " + get_bridge_token(), "Origin": "https://market.example.test"}
    assert client.post(path + "/complete", headers=headers, json={}).status_code == 404
    assert client.get(path, headers={**headers, "Origin": "https://untrusted.example"}).status_code == 403
    assert client.post(path + "/import", headers={**headers, "Content-Length": str(8 * 1024 * 1024 + 1)}, content=b"{}").status_code == 413


def test_bridge_preserves_download_bytes_without_forwarding_market_credentials(client, monkeypatch):
    seen = {}
    class LocalClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, method, url, *, content, headers, params):
            seen.update(method=method, url=url, headers=headers, params=params,
                        raw=b"".join([part async for part in content]))
            return httpx.Response(200, json={"ok": True, "phase": "imported"})
    monkeypatch.setattr(httpx, "AsyncClient", LocalClient)
    raw = b'{ "schema" : "neko.story.numeric.v2" }\n'
    response = client.post("/market/theater/launch/selection/import?share_id=12", content=raw,
        headers={"Authorization": "Bearer " + get_bridge_token(), "Origin": "https://market.example.test"})
    assert response.status_code == 200
    assert seen["raw"] == raw
    assert seen["url"].endswith("/api/theater-market/launch/selection/import")
    assert seen["params"] == {"share_id": "12"}
    assert "Authorization" not in seen["headers"]
    assert "X-CSRF-Token" in seen["headers"]
