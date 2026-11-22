"""Security regression tests for R1111-01 remote/proxy loopback tunnel bypass and internal service proof."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from utils.http.internal_client import aclose_internal_http_client_current_loop, get_internal_http_client
from utils.instance_access import (
    INTERNAL_SERVICE_HEADER,
    InstanceAccessMiddleware,
    _local_native,
    internal_service_proof,
)

KEY = "test-only-r1111-key-" + "k" * 40


def _build_guarded_app() -> FastAPI:
    app = FastAPI()

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        return {"status": "ok"}

    @app.websocket("/health")
    async def health_ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ws-health")
        await socket.close()

    @app.get("/private")
    async def private_get():
        return {"ok": True}

    @app.post("/private")
    async def private_post():
        return {"ok": True}

    @app.websocket("/socket")
    async def ws_endpoint(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"ok": True})
        await socket.close()

    app.add_middleware(InstanceAccessMiddleware)
    return app


@pytest.mark.parametrize(
    ("env_name", "env_val"),
    [
        ("NEKO_BEHIND_PROXY", "true"),
        ("NEKO_ACTIVITY_TRACKER_REMOTE", "true"),
    ],
)
def test_remote_or_proxy_mode_closes_headerless_loopback_tunnel_bypass(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_val: str,
):
    """R1111-01: headerless loopback requests must be rejected on private HTTP and WS routes in proxy/remote mode."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv(env_name, env_val)

    client = TestClient(_build_guarded_app(), base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))

    # Headerless HTTP routes (GET and POST) must require credentials (401).
    assert client.get("/private").status_code == 401
    assert client.post("/private").status_code == 401

    # Headerless WebSocket on loopback (including /health) must also be rejected.
    with pytest.raises(Exception):
        with client.websocket_connect("ws://127.0.0.1:48911/socket"):
            pass
    with pytest.raises(Exception):
        with client.websocket_connect("ws://127.0.0.1:48911/health"):
            pass

    # Local unauthenticated GET/HEAD /health remains allowed without forwarding/browser headers.
    assert client.get("/health").status_code == 200
    assert client.head("/health").status_code == 200

    # Forwarded /health must still be rejected.
    assert client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401


@pytest.mark.parametrize(
    ("env_name", "env_val"),
    [
        ("NEKO_BEHIND_PROXY", "true"),
        ("NEKO_ACTIVITY_TRACKER_REMOTE", "true"),
    ],
)
async def test_remote_or_proxy_mode_closes_raw_asgi_websocket_health_without_upgrade_header(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_val: str,
):
    """R1111-01: raw ASGI WebSocket scopes on /health without Upgrade header must close with 4401."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv(env_name, env_val)

    called = False

    async def inner_app(_scope, _receive, send):
        nonlocal called
        called = True
        await send({"type": "websocket.accept"})

    middleware = InstanceAccessMiddleware(inner_app)
    sent = []
    scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 48911),
        "client": ("127.0.0.1", 51234),
        "headers": [(b"host", b"127.0.0.1:48911")],
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    assert called is False
    assert sent == [{"type": "websocket.close", "code": 4401}]


async def test_internal_http_client_attaches_signed_proof_in_remote_mode_and_validates_in_middleware(
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: internal HTTP client injects X-Neko-Internal-Service in proxy/remote mode and passes middleware."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    internal_client = get_internal_http_client()
    try:
        # Exercise the registered request event hook directly against an ASGI app.
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        hooked_client = httpx.AsyncClient(
            transport=transport,
            event_hooks=internal_client.event_hooks,
        )
        async with hooked_client:
            resp = await hooked_client.post("http://127.0.0.1:48911/private")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
    finally:
        await aclose_internal_http_client_current_loop()


def test_internal_service_proof_rejects_forged_expired_wrong_path_and_forwarded_requests(
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: X-Neko-Internal-Service proof must reject tampering, expiry, wrong method/path, proxy headers, and remote peers."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    local_client = TestClient(app, base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))
    remote_client = TestClient(app, base_url="http://127.0.0.1:48911", client=("203.0.113.9", 50000))

    valid_proof = internal_service_proof(KEY, "GET", "/private")
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 200

    # Wrong method or wrong path fails.
    assert local_client.post("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 401
    wrong_path_proof = internal_service_proof(KEY, "GET", "/other")
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: wrong_path_proof}).status_code == 401

    # Expired proof fails.
    expired_proof = internal_service_proof(KEY, "GET", "/private", expires=int(time.time()) - 10)
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: expired_proof}).status_code == 401

    # Forwarding headers or non-loopback peer fail even with a valid proof.
    assert local_client.get(
        "/private",
        headers={INTERNAL_SERVICE_HEADER: valid_proof, "X-Forwarded-For": "127.0.0.1"},
    ).status_code == 401
    assert remote_client.get("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 401


def test_desktop_mode_keeps_zero_step_loopback_without_touching_instance_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """R1111-01: desktop mode (no proxy/remote env) preserves zero-step loopback and creates no instance_access.key."""
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))

    client = TestClient(_build_guarded_app(), base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))
    assert client.get("/private").status_code == 200
    assert client.post("/private").status_code == 200
    with client.websocket_connect("ws://127.0.0.1:48911/socket") as ws:
        assert ws.receive_json() == {"ok": True}
    assert not (tmp_path / "instance_access.key").exists()
    assert callable(_local_native)
