"""Remote desktop API relay keeps cloud credentials and account streams local."""

import asyncio
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import community_remote_proxy as P
from utils.instance_access import InstanceAccessMiddleware


@pytest.fixture
def proxy(monkeypatch):
    key = "test-instance-key-" + "k" * 40
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", key)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()
    app.include_router(P.router)
    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://instance.example")
    client.headers["Authorization"] = "Bearer " + key
    return client


def test_anonymous_never_opens_upstream(proxy, monkeypatch):
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", lambda: pytest.fail("Anonymous account read"))
    proxy.headers.pop("Authorization")
    assert proxy.post("/api/forge/credits/grant", content="invalid").status_code == 401


def test_desktop_simple_cross_site_post_cannot_use_cloud_account(monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "false")
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    app = FastAPI()
    app.include_router(P.router)
    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))

    async def forbidden():
        pytest.fail("Cross-site request must not read credentials or contact cloud")

    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", forbidden)
    for path in ("grant", "drop-events/claim", "drop-events/event/ack"):
        response = client.post("/api/forge/credits/" + path, content="{}",
                               headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
        assert response.status_code == 403


def test_relay_uses_saved_cloud_token_and_fixed_origin(proxy, monkeypatch):
    seen = []
    snapshot = {"access_token": "cloud-only-secret", "local_user_id": "owner"}

    async def status():
        return {"snapshot": snapshot}

    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"credits": 2})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_desktop_session_snapshot", lambda: snapshot)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(P.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    response = proxy.get("/api/forge/credits?url=https://evil.example")
    assert response.json() == {"credits": 2}
    assert seen[0].url.host == "community.example"
    assert seen[0].url.path == "/api/forge/credits"
    assert seen[0].headers["Authorization"] == "Bearer cloud-only-secret"
    assert "cloud-only-secret" not in response.text
    assert proxy.get("/api/arbitrary-proxy").status_code == 404
    assert proxy.post("/api/forge/credits/drop-events/a.b/ack").status_code == 400


def test_account_switch_stops_old_notification_stream(proxy, monkeypatch):
    current = {"access_token": "old-secret", "local_user_id": "old-owner"}
    snapshot = dict(current)

    async def status():
        return {"snapshot": snapshot}

    class Events(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: before\n\n"
            current.update(local_user_id="new-owner", access_token="new-secret")
            await asyncio.sleep(1.05)  # Account revocation is checked at most once/second.
            yield b"data: old-owner-private\n\n"

    async def upstream(_request):
        return httpx.Response(200, stream=Events())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_desktop_session_snapshot", lambda: current)
    monkeypatch.setattr(P.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    response = proxy.get("/api/notifications/stream")
    assert response.text == "data: before\n\n"
