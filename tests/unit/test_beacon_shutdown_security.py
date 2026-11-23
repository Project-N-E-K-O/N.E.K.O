# -*- coding: utf-8 -*-
"""Security tests for POST /api/beacon/shutdown (B01-003, C01-003)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit


def _csrf_token() -> str:
    from app.main_server import web_app

    return web_app.AUTOSTART_CSRF_TOKEN


@pytest.fixture
def beacon_client(monkeypatch):
    from app.main_server import web_app

    shutdown_mock = AsyncMock()
    monkeypatch.setattr(web_app.runtime, "shutdown_server_async", shutdown_mock)
    monkeypatch.setattr(
        web_app.runtime,
        "get_start_config",
        lambda: {"browser_mode_enabled": True},
    )
    # Construct TestClient without entering its lifespan context so startup
    # hooks (e.g. token_tracker.install_hooks) do not leak across xdist tests.
    client = TestClient(
        web_app.app,
        base_url="http://127.0.0.1:48911",
        client=("127.0.0.1", 50000),
    )
    try:
        yield client, shutdown_mock
    finally:
        client.close()


def test_beacon_shutdown_allows_exact_same_origin(beacon_client):
    client, shutdown_mock = beacon_client
    resp = client.post(
        "/api/beacon/shutdown",
        headers={
            "Origin": "http://127.0.0.1:48911",
            "Sec-Fetch-Site": "same-origin",
        },
        json={"action": "shutdown"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert shutdown_mock.await_count == 1


def test_beacon_shutdown_allows_same_origin_without_sec_fetch(beacon_client):
    client, shutdown_mock = beacon_client
    resp = client.post(
        "/api/beacon/shutdown",
        headers={"Origin": "http://127.0.0.1:48911"},
    )
    assert resp.status_code == 200
    assert shutdown_mock.await_count == 1


def test_beacon_shutdown_allows_headerless_with_valid_csrf(beacon_client):
    client, shutdown_mock = beacon_client
    resp = client.post(
        "/api/beacon/shutdown",
        headers={"X-CSRF-Token": _csrf_token()},
    )
    assert resp.status_code == 200
    assert shutdown_mock.await_count == 1


def test_beacon_shutdown_allows_body_csrf_fallback(beacon_client):
    client, shutdown_mock = beacon_client
    resp = client.post(
        "/api/beacon/shutdown",
        json={"_csrf_token": _csrf_token()},
    )
    assert resp.status_code == 200
    assert shutdown_mock.await_count == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:3000"},
        {"Origin": "http://localhost:3000"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/attack.html"},
        {"Referer": "http://127.0.0.1:3000/attack.html"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://:@127.0.0.1:48911"},
        {"Origin": "http://@127.0.0.1:48911"},
        {"Origin": "http://:secret@127.0.0.1:48911"},
        {"Referer": "http://:@127.0.0.1:48911/p"},
        {"Referer": "http://@127.0.0.1:48911/p"},
        {"Origin": "http://127.0.0.1:48911/evil"},
        {"Origin": "http://127.0.0.1:48911?a=1"},
        {"Origin": "http://127.0.0.1:48911#frag"},
        {"Origin": "http://127.0.0.1:48911/evil?a=1#frag"},
        {"Origin": "http://127.0.0.1:48911/?"},
        {"Origin": "http://127.0.0.1:48911/#"},
        {"Origin": "http://127.0.0.1:48911\\evil"},
        {"Origin": "", "Referer": "http://127.0.0.1:48911/p"},
        {},
    ],
)
def test_beacon_shutdown_rejects_untrusted_or_headerless_requests(beacon_client, headers):
    client, shutdown_mock = beacon_client
    resp = client.post("/api/beacon/shutdown", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "csrf_validation_failed"
    assert shutdown_mock.await_count == 0


def test_beacon_shutdown_allows_trailing_slash_origin_and_valid_referer(beacon_client):
    client, shutdown_mock = beacon_client

    r_origin_slash = client.post(
        "/api/beacon/shutdown",
        headers={"Origin": "http://127.0.0.1:48911/"},
    )
    assert r_origin_slash.status_code == 200
    assert shutdown_mock.await_count == 1

    r_referer = client.post(
        "/api/beacon/shutdown",
        headers={"Referer": "http://127.0.0.1:48911/app?foo=1#bar"},
    )
    assert r_referer.status_code == 200
    assert shutdown_mock.await_count == 2


def test_beacon_shutdown_rejects_duplicate_origin_or_referer_headers(beacon_client):
    client, shutdown_mock = beacon_client

    r_dup_origin = client.post(
        "/api/beacon/shutdown",
        headers=[
            ("origin", "http://127.0.0.1:48911"),
            ("origin", "https://evil.example"),
        ],
    )
    assert r_dup_origin.status_code == 403
    assert r_dup_origin.json()["detail"]["error_code"] == "csrf_validation_failed"

    r_dup_referer = client.post(
        "/api/beacon/shutdown",
        headers=[
            ("referer", "http://127.0.0.1:48911/app"),
            ("referer", "https://evil.example/app"),
        ],
    )
    assert r_dup_referer.status_code == 403
    assert r_dup_referer.json()["detail"]["error_code"] == "csrf_validation_failed"
    assert shutdown_mock.await_count == 0


def test_beacon_shutdown_with_remote_instance_identity(monkeypatch):
    import time
    from app.main_server import web_app
    from utils.instance_access import (
        INSECURE_COOKIE,
        InstanceAccessMiddleware,
        _signed,
    )

    shutdown_mock = AsyncMock()
    monkeypatch.setattr(web_app.runtime, "shutdown_server_async", shutdown_mock)
    monkeypatch.setattr(
        web_app.runtime,
        "get_start_config",
        lambda: {"browser_mode_enabled": True},
    )
    access_key = "test-instance-access-key-0123456789"
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")
    monkeypatch.setenv("NEKO_REQUIRE_HTTPS", "0")
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", access_key)

    expires = int(time.time()) + 3600
    session_token = _signed(access_key, "session-http", "127.0.0.1", "browser-1", expires)

    class _IdentityWrapper:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["neko.instance_identity"] = "remote-user"
            await self.app(scope, receive, send)

    client = TestClient(
        InstanceAccessMiddleware(web_app.app),
        base_url="http://127.0.0.1:48911",
        client=("127.0.0.1", 50000),
    )
    try:
        client.cookies.set(INSECURE_COOKIE, session_token)
        r_ok = client.post(
            "/api/beacon/shutdown",
            headers={
                "Origin": "http://127.0.0.1:48911",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_ok.status_code == 200
        assert shutdown_mock.await_count == 1

        r_empty_userinfo = client.post(
            "/api/beacon/shutdown",
            headers={"Origin": "http://:@127.0.0.1:48911"},
        )
        assert r_empty_userinfo.status_code == 403
        assert r_empty_userinfo.json()["detail"]["error_code"] == "csrf_validation_failed"

        r_cross_port_csrf = client.post(
            "/api/beacon/shutdown",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "X-CSRF-Token": _csrf_token(),
            },
        )
        assert r_cross_port_csrf.status_code == 403
    finally:
        client.close()

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    wrapped_client = TestClient(
        _IdentityWrapper(web_app.app),
        base_url="http://127.0.0.1:48911",
        client=("127.0.0.1", 50000),
    )
    try:
        r_referer_userinfo = wrapped_client.post(
            "/api/beacon/shutdown",
            headers={"Referer": "http://:@127.0.0.1:48911/p"},
        )
        assert r_referer_userinfo.status_code == 403
        assert r_referer_userinfo.json()["detail"]["error_code"] == "csrf_validation_failed"

        r_wrapped_cross_port = wrapped_client.post(
            "/api/beacon/shutdown",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "X-CSRF-Token": _csrf_token(),
            },
        )
        assert r_wrapped_cross_port.status_code == 403
        assert r_wrapped_cross_port.json()["detail"]["error_code"] == "csrf_validation_failed"
    finally:
        wrapped_client.close()

    assert shutdown_mock.await_count == 1


def test_beacon_shutdown_allows_authenticated_tls_proxy_origin_and_blocks_cross_port(monkeypatch):
    from app.main_server import web_app
    from utils import host_origin_guard

    shutdown_mock = AsyncMock()
    monkeypatch.setattr(web_app.runtime, "shutdown_server_async", shutdown_mock)
    monkeypatch.setattr(
        web_app.runtime,
        "get_start_config",
        lambda: {"browser_mode_enabled": True},
    )
    access_key = "k" * 40
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "1")
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", access_key)
    monkeypatch.setattr(host_origin_guard, "saved_trusted_hosts", lambda: "neko.example")
    auth = {"Authorization": f"Bearer {access_key}"}

    client = TestClient(
        web_app.app,
        base_url="http://neko.example/",
        client=("127.0.0.1", 50000),
    )
    try:
        r_proxy_https = client.post(
            "/api/beacon/shutdown",
            headers={
                **auth,
                "Origin": "https://neko.example",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_proxy_https.status_code == 200
        assert shutdown_mock.await_count == 1

        r_proxy_referer = client.post(
            "/api/beacon/shutdown",
            headers={
                **auth,
                "Referer": "https://neko.example/chat",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_proxy_referer.status_code == 200
        assert shutdown_mock.await_count == 2

        r_cross_site = client.post(
            "/api/beacon/shutdown",
            headers={
                **auth,
                "Origin": "https://neko.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert r_cross_site.status_code == 403

        r_cross_port = client.post(
            "/api/beacon/shutdown",
            headers={
                **auth,
                "Origin": "https://neko.example:8443",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_cross_port.status_code == 403

        r_evil = client.post(
            "/api/beacon/shutdown",
            headers={
                **auth,
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r_evil.status_code == 403
    finally:
        client.close()

    assert shutdown_mock.await_count == 2


def test_beacon_shutdown_treats_non_ascii_csrf_token_as_invalid(beacon_client):
    client, shutdown_mock = beacon_client

    r_no_origin = client.post(
        "/api/beacon/shutdown",
        json={"_csrf_token": "非ASCII令牌"},
    )
    assert r_no_origin.status_code == 403
    assert r_no_origin.json()["detail"]["error_code"] == "csrf_validation_failed"

    r_surrogate_no_origin = client.post(
        "/api/beacon/shutdown",
        content=b'{"_csrf_token": "\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert r_surrogate_no_origin.status_code == 403
    assert r_surrogate_no_origin.json()["detail"]["error_code"] == "csrf_validation_failed"

    r_same_origin = client.post(
        "/api/beacon/shutdown",
        json={"_csrf_token": "非ASCII令牌"},
        headers={"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-origin"},
    )
    assert r_same_origin.status_code == 200

    r_surrogate_same_origin = client.post(
        "/api/beacon/shutdown",
        content=b'{"_csrf_token": "\\ud800"}',
        headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert r_surrogate_same_origin.status_code == 200
    assert shutdown_mock.await_count == 2


def test_local_mutation_guard_treats_lone_surrogate_csrf_token_as_403():
    import json
    from starlette.requests import Request
    from main_routers.system_router._shared import _validate_local_mutation_request

    req = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/config/core",
        "scheme": "http",
        "server": ("127.0.0.1", 48911),
        "headers": [(b"origin", b"http://127.0.0.1:48911")],
    })
    rejected = _validate_local_mutation_request(req, payload={"_csrf_token": "\ud800"})
    assert rejected is not None
    assert rejected.status_code == 403
    assert json.loads(rejected.body)["error_code"] == "csrf_validation_failed"


def test_beacon_shutdown_rejects_oversized_json_body_without_buffering(beacon_client):
    import json
    from app.main_server import web_app

    client, shutdown_mock = beacon_client
    big_padding = "x" * (web_app._BEACON_SHUTDOWN_MAX_JSON_BYTES + 256)
    oversized_payload = json.dumps({
        "_csrf_token": _csrf_token(),
        "padding": big_padding,
    }).encode("utf-8")

    # 1) Explicit Content-Length above cap on headerless CSRF-fallback request -> 403.
    resp_cl = client.post(
        "/api/beacon/shutdown",
        content=oversized_payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp_cl.status_code == 403
    assert resp_cl.json()["detail"]["error_code"] == "csrf_validation_failed"

    # 2) Chunked stream without Content-Length exceeding cap -> 403.
    def _chunk_stream():
        for start in range(0, len(oversized_payload), 512):
            yield oversized_payload[start : start + 512]

    resp_stream = client.post(
        "/api/beacon/shutdown",
        content=_chunk_stream(),
        headers={"Content-Type": "application/json"},
    )
    assert resp_stream.status_code == 403
    assert resp_stream.json()["detail"]["error_code"] == "csrf_validation_failed"
    assert shutdown_mock.await_count == 0

    # 3) Small valid JSON token & empty-body same-origin beacon still succeed.
    resp_small = client.post(
        "/api/beacon/shutdown",
        json={"_csrf_token": _csrf_token()},
    )
    assert resp_small.status_code == 200
    resp_empty = client.post(
        "/api/beacon/shutdown",
        headers={"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-origin"},
    )
    assert resp_empty.status_code == 200
    assert shutdown_mock.await_count == 2


@pytest.mark.asyncio
async def test_beacon_shutdown_header_rejections_do_not_read_body(monkeypatch):
    from starlette.requests import Request
    from fastapi import HTTPException
    from app.main_server import web_app

    body_read_called = False

    async def _bomb_receive():
        nonlocal body_read_called
        body_read_called = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    for hdrs in (
        [(b"origin", b"https://evil.example"), (b"content-type", b"application/json")],
        [(b"sec-fetch-site", b"cross-site"), (b"content-type", b"application/json")],
    ):
        req = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/beacon/shutdown",
                "scheme": "http",
                "server": ("127.0.0.1", 48911),
                "headers": hdrs,
            },
            receive=_bomb_receive,
        )
        with pytest.raises(HTTPException) as exc:
            await web_app._validate_beacon_shutdown_origin(req)
        assert exc.value.status_code == 403
        assert body_read_called is False


@pytest.mark.asyncio
async def test_beacon_shutdown_stops_reading_stream_once_cap_exceeded():
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.main_server import web_app

    chunk_size = 1024
    total_chunks = 10
    chunks_read = 0

    async def _counting_receive():
        nonlocal chunks_read
        if chunks_read >= total_chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk_idx = chunks_read
        chunks_read += 1
        prefix = b'{"_csrf_token":"' if chunk_idx == 0 else b""
        payload = (prefix + b"a" * chunk_size)[:chunk_size]
        return {
            "type": "http.request",
            "body": payload,
            "more_body": chunks_read < total_chunks,
        }

    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/beacon/shutdown",
            "scheme": "http",
            "server": ("127.0.0.1", 48911),
            "headers": [
                (b"host", b"127.0.0.1:48911"),
                (b"content-type", b"application/json"),
            ],
        },
        receive=_counting_receive,
    )
    with pytest.raises(HTTPException) as exc:
        await web_app._validate_beacon_shutdown_origin(req)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "csrf_validation_failed"
    assert chunks_read == (web_app._BEACON_SHUTDOWN_MAX_JSON_BYTES // chunk_size) + 1
    assert chunks_read < total_chunks

