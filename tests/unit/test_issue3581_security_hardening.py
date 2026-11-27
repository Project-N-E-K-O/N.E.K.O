# -*- coding: utf-8 -*-
"""Security regression tests for issue #3581 fixes (M-002, L-001, L-002, L-003, I-001)."""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# M-002 (+ I-001): main_routers/system_router/meme_proxy.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: ssl.SSLError("handshake failed SECRET_CERT_PATH=/etc/ssl/private"),
        lambda: httpx.ConnectError("SSL certificate verify failed SECRET_CERT_PATH=/etc/ssl/private"),
    ],
)
async def test_meme_proxy_never_downgrades_tls_or_retries_on_ssl_error(monkeypatch, exc_factory):
    from main_routers.system_router import meme_proxy

    url = "https://i.imgflip.com/test_m002.jpg"
    saved_cache = dict(meme_proxy.MEME_PROXY_CACHE)
    meme_proxy.MEME_PROXY_CACHE.clear()

    client_inits: list[dict] = []
    stream_calls = 0
    real_async_client = httpx.AsyncClient

    class _SpyAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            client_inits.append(dict(kwargs))
            super().__init__(*args, **kwargs)

        def stream(self, *args, **kwargs):
            nonlocal stream_calls
            stream_calls += 1
            raise exc_factory()

    monkeypatch.setattr(meme_proxy.httpx, "AsyncClient", _SpyAsyncClient)
    try:
        resp = await meme_proxy.fetch_meme_image_response(url, write_cache=True)
    finally:
        meme_proxy.MEME_PROXY_CACHE.clear()
        meme_proxy.MEME_PROXY_CACHE.update(saved_cache)

    assert resp.status_code == 502
    body = json.loads(resp.body)
    assert body["success"] is False
    assert "SECRET_CERT_PATH" not in resp.body.decode("utf-8")
    assert len(client_inits) == 1
    assert "verify" not in client_inits[0]
    assert stream_calls == 1
    assert url not in meme_proxy.MEME_PROXY_CACHE


@pytest.mark.asyncio
async def test_meme_proxy_blocks_non_http_redirect_and_masks_internal_errors(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from main_routers.system_router import meme_proxy

    saved_cache = dict(meme_proxy.MEME_PROXY_CACHE)
    meme_proxy.MEME_PROXY_CACHE.clear()
    try:
        # 1. Redirect to non-http(s) scheme on an allowlisted host must be rejected.
        redirect_calls = 0
        mock_302 = MagicMock()
        mock_302.status_code = 302
        mock_302.headers = {"Location": "ftp://i.imgflip.com/secret.png"}

        @asynccontextmanager
        async def _stream_redirect(*_args, **_kwargs):
            nonlocal redirect_calls
            redirect_calls += 1
            yield mock_302

        with monkeypatch.context() as mp:
            mp.setattr(httpx.AsyncClient, "stream", _stream_redirect)
            r_scheme = await meme_proxy.fetch_meme_image_response(
                "https://i.imgflip.com/redirect_scheme.jpg", write_cache=False
            )
        assert r_scheme.status_code == 403
        assert redirect_calls == 1

        # 2. HTTPStatusError must return 502 without leaking secret query/message text.
        req = httpx.Request("GET", "https://i.imgflip.com/fail.jpg?token=SECRET_UPSTREAM_TOKEN")
        upstream_resp = httpx.Response(503, request=req)

        @asynccontextmanager
        async def _stream_http_err(*_args, **_kwargs):
            raise httpx.HTTPStatusError("upstream SECRET_UPSTREAM_TOKEN", request=req, response=upstream_resp)
            yield  # pragma: no cover

        with monkeypatch.context() as mp:
            mp.setattr(httpx.AsyncClient, "stream", _stream_http_err)
            r_http_err = await meme_proxy.fetch_meme_image_response(
                "https://i.imgflip.com/fail.jpg", write_cache=False
            )
        assert r_http_err.status_code == 502
        assert "SECRET_UPSTREAM_TOKEN" not in r_http_err.body.decode("utf-8")

        # 3. Unexpected error must return 500 without leaking str(exc).
        @asynccontextmanager
        async def _stream_boom(*_args, **_kwargs):
            raise RuntimeError("INTERNAL_SECRET_XYZ")
            yield  # pragma: no cover

        with monkeypatch.context() as mp:
            mp.setattr(httpx.AsyncClient, "stream", _stream_boom)
            r_err = await meme_proxy.fetch_meme_image_response(
                "https://i.imgflip.com/boom.jpg", write_cache=False
            )
        assert r_err.status_code == 500
        assert "INTERNAL_SECRET_XYZ" not in r_err.body.decode("utf-8")
    finally:
        meme_proxy.MEME_PROXY_CACHE.clear()
        meme_proxy.MEME_PROXY_CACHE.update(saved_cache)


# ---------------------------------------------------------------------------
# L-003: main_routers/system_router/_shared.py::_get_app_root
# ---------------------------------------------------------------------------


def test_system_router_get_app_root_is_independent_of_cwd(tmp_path, monkeypatch):
    from main_routers.system_router import _shared as system_shared

    expected_root = Path(system_shared.__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)

    assert Path(system_shared._get_app_root()) == expected_root
    assert Path(system_shared._get_app_root()) != tmp_path

    # Frozen PyInstaller modes must retain _MEIPASS and executable-dir behavior.
    meipass_dir = tmp_path / "meipass"
    monkeypatch.setattr(system_shared.sys, "frozen", True, raising=False)
    monkeypatch.setattr(system_shared.sys, "_MEIPASS", str(meipass_dir), raising=False)
    assert system_shared._get_app_root() == str(meipass_dir)

    monkeypatch.delattr(system_shared.sys, "_MEIPASS", raising=False)
    exe_path = tmp_path / "bundle" / "neko.exe"
    monkeypatch.setattr(system_shared.sys, "executable", str(exe_path), raising=False)
    assert system_shared._get_app_root() == str(exe_path.parent)


def test_cwd_decoy_static_dir_rejected_by_file_and_steam_endpoints(tmp_path, monkeypatch):
    from main_routers import system_router
    from main_routers.system_router import _shared as system_shared
    from main_routers.system_router import files as files_mod
    from main_routers.system_router import steam as steam_mod

    expected_root = Path(system_shared._get_app_root()).resolve()
    decoy_static = tmp_path / "static"
    decoy_static.mkdir()
    decoy_file = decoy_static / "decoy.png"
    decoy_preview = decoy_static / "preview.png"
    png_bytes = b"\x89PNG\r\n\x1a\nok"
    decoy_file.write_bytes(png_bytes)
    decoy_preview.write_bytes(png_bytes)

    workshop_dir = tmp_path / "workshop"
    docs_dir = tmp_path / "docs"
    workshop_dir.mkdir()
    docs_dir.mkdir()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(files_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(files_mod, "get_workshop_library_roots", lambda: [])
    monkeypatch.setattr(
        files_mod,
        "_get_system_config_manager",
        lambda: SimpleNamespace(docs_dir=docs_dir, app_docs_dir=None, original_docs_dir=None),
    )
    monkeypatch.setattr(steam_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(steam_mod, "get_workshop_library_roots", lambda: [])

    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        r_file = client.get("/api/file-exists", params={"path": str(decoy_file)})
        assert r_file.status_code == 403
        assert r_file.json()["exists"] is False

        r_find = client.get("/api/find-first-image", params={"folder": str(decoy_static)})
        assert r_find.status_code == 403
        assert r_find.json()["success"] is False

        r_steam = client.get("/api/steam/proxy-image", params={"image_path": str(decoy_preview)})
        assert r_steam.status_code == 404
        assert r_steam.json()["success"] is False

        # A missing probe inside the real repo static root stays allowed (200 exists=False, not 403).
        r_repo_probe = client.get(
            "/api/file-exists",
            params={"path": str(expected_root / "static" / "__nonexistent_issue3581_probe__.txt")},
        )
        assert r_repo_probe.status_code == 200
        assert r_repo_probe.json() == {"exists": False}


# ---------------------------------------------------------------------------
# L-001: main_routers/system_router/files.py (/api/file-exists local/origin guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_exists_enforces_local_access_and_same_origin(tmp_path, monkeypatch):
    from main_routers import system_router
    from main_routers.system_router import files as files_mod

    app_root = tmp_path / "app"
    static_dir = app_root / "static"
    assets_dir = app_root / "assets"
    workshop_dir = tmp_path / "workshop"
    docs_dir = tmp_path / "docs"
    for d in (static_dir, assets_dir, workshop_dir, docs_dir):
        d.mkdir(parents=True)
    ok_file = static_dir / "ok.txt"
    ok_file.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(files_mod, "_get_app_root", lambda: str(app_root))
    monkeypatch.setattr(files_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(files_mod, "get_workshop_library_roots", lambda: [])
    monkeypatch.setattr(
        files_mod,
        "_get_system_config_manager",
        lambda: SimpleNamespace(docs_dir=docs_dir, app_docs_dir=None, original_docs_dir=None),
    )
    monkeypatch.delenv("NEKO_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    app = FastAPI()
    app.include_router(system_router.router)

    # Non-loopback peer (including Host: testserver spoofing) -> 403 local_access_required.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.50", 50000)),
        base_url="http://testserver",
    ) as remote_client:
        r_remote = await remote_client.get("/api/file-exists", params={"path": str(ok_file)})
        assert r_remote.status_code == 403
        assert r_remote.json() == {"exists": False, "error_code": "local_access_required"}

    # Loopback browser origin checks.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        base_url="http://127.0.0.1:48911",
    ) as local_client:
        bad_header_cases = [
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": "http://127.0.0.1:3000"},
            {"Referer": "https://evil.example/page"},
            [("origin", "http://127.0.0.1:48911"), ("origin", "https://evil.example")],
            {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "cross-site"},
            {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-site"},
        ]
        for headers in bad_header_cases:
            resp = await local_client.get(
                "/api/file-exists", params={"path": str(ok_file)}, headers=headers
            )
            assert resp.status_code == 403, f"headers={headers!r} got {resp.status_code}"
            assert resp.json() == {"exists": False, "error_code": "cross_origin_request_rejected"}

        # Same-origin browser request succeeds.
        r_same = await local_client.get(
            "/api/file-exists",
            params={"path": str(ok_file)},
            headers={"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-origin"},
        )
        assert r_same.status_code == 200
        assert r_same.json() == {"exists": True}

        # Native headerless loopback client succeeds.
        r_native = await local_client.get("/api/file-exists", params={"path": str(ok_file)})
        assert r_native.status_code == 200
        assert r_native.json() == {"exists": True}

        # Explicitly configured NEKO_TRUSTED_ORIGINS succeeds.
        monkeypatch.setenv("NEKO_TRUSTED_ORIGINS", "https://trusted.example")
        r_trusted = await local_client.get(
            "/api/file-exists",
            params={"path": str(ok_file)},
            headers={"Origin": "https://trusted.example", "Sec-Fetch-Site": "none"},
        )
        assert r_trusted.status_code == 200
        assert r_trusted.json() == {"exists": True}


# ---------------------------------------------------------------------------
# L-002 & I-001: main_routers/debug_router.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_health_get_enforces_origin_local_access_and_hides_log_path(tmp_path, monkeypatch):
    from main_routers import debug_router

    secret_log = tmp_path / "secret_user_dir" / "debug_health.jsonl"
    monkeypatch.setenv("NEKO_DEBUG_HEALTH_LOG", "1")
    monkeypatch.setattr(debug_router, "_resolve_log_path", lambda: secret_log)
    monkeypatch.setattr(
        debug_router,
        "_collect_snapshot",
        lambda include_deep=False, channel="watchdog": {"ts": 1.0, "asyncio_tasks": 1},
    )
    monkeypatch.delenv("NEKO_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    app = FastAPI()
    app.include_router(debug_router.router)

    # Unauthenticated non-loopback peer -> 403.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.50", 50000)),
        base_url="http://127.0.0.1:48911",
    ) as remote_client:
        r_remote = await remote_client.get("/api/debug/health")
        assert r_remote.status_code == 403
        assert r_remote.headers.get("cache-control") == "no-store"
        assert r_remote.json()["error_code"] == "local_access_required"

    # Loopback checks.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        base_url="http://127.0.0.1:48911",
    ) as local_client:
        for headers in (
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": "http://127.0.0.1:3000"},
            {"Referer": "https://evil.example/probe"},
            {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "cross-site"},
            {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-site"},
        ):
            r_bad = await local_client.get("/api/debug/health", headers=headers)
            assert r_bad.status_code == 403
            assert r_bad.headers.get("cache-control") == "no-store"
            assert r_bad.json()["error_code"] == "cross_origin_request_rejected"

        for headers in (
            {"Sec-Fetch-Site": "none"},
            {"Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-origin"},
            {},
        ):
            r_ok = await local_client.get("/api/debug/health", headers=headers)
            assert r_ok.status_code == 200
            assert r_ok.headers.get("cache-control") == "no-store"
            body = r_ok.json()
            assert body["log_enabled"] is True
            assert body["log_file"] == "debug_health.jsonl"
            assert "log_path" not in body
            assert str(tmp_path) not in r_ok.text
            assert "secret_user_dir" not in r_ok.text

    # Authenticated remote request (neko.instance_identity set by InstanceAccessMiddleware) succeeds.
    authed_app = FastAPI()
    authed_app.include_router(debug_router.router)

    async def _with_identity(scope, receive, send):
        scope["neko.instance_identity"] = "user1"
        await authed_app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_with_identity, client=("203.0.113.10", 50000)),
        base_url="https://neko.example",
    ) as authed_client:
        r_authed = await authed_client.get(
            "/api/debug/health",
            headers={"Origin": "https://neko.example", "Sec-Fetch-Site": "same-origin"},
        )
        assert r_authed.status_code == 200
        assert r_authed.json()["log_file"] == "debug_health.jsonl"


@pytest.mark.asyncio
async def test_debug_health_client_post_guards_before_body_read_and_sanitizes_errors(monkeypatch):
    from main_routers import debug_router

    saved_ring = list(debug_router._HEALTH_RING)
    debug_router._HEALTH_RING.clear()
    monkeypatch.delenv("NEKO_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    app = FastAPI()
    app.include_router(debug_router.router)

    async def _send_raw_asgi(
        headers: list[tuple[bytes, bytes]],
        *,
        client: tuple[str, int] = ("127.0.0.1", 50000),
    ) -> tuple[int, bool, bytes]:
        body_read_called = False
        status_code = 0
        response_chunks: list[bytes] = []

        async def _bomb_receive():
            nonlocal body_read_called
            body_read_called = True
            return {
                "type": "http.request",
                "body": b'{"ts": 1.0, "dom_nodes": 42}',
                "more_body": False,
            }

        async def _capture_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/debug/health/client",
            "raw_path": b"/api/debug/health/client",
            "query_string": b"",
            "headers": headers,
            "client": client,
            "server": ("127.0.0.1", 48911),
        }
        await app(scope, _bomb_receive, _capture_send)
        return status_code, body_read_called, b"".join(response_chunks)

    try:
        same_origin_headers = [
            (b"host", b"127.0.0.1:48911"),
            (b"origin", b"http://127.0.0.1:48911"),
        ]

        # Unsupported Content-Types must reject with 415 BEFORE reading the body.
        for ctype in (b"text/plain", b"multipart/form-data; boundary=---"):
            status, read_called, _ = await _send_raw_asgi(
                same_origin_headers + [(b"content-type", ctype)]
            )
            assert status == 415
            assert read_called is False

        # Cross-origin / cross-port / cross-site must reject with 403 BEFORE reading the body.
        for bad_headers in (
            [(b"host", b"127.0.0.1:48911"), (b"origin", b"https://evil.example"), (b"content-type", b"application/json")],
            [(b"host", b"127.0.0.1:48911"), (b"origin", b"http://127.0.0.1:3000"), (b"content-type", b"application/json")],
            [(b"host", b"127.0.0.1:48911"), (b"origin", b"http://127.0.0.1:48911"), (b"sec-fetch-site", b"cross-site"), (b"content-type", b"application/json")],
        ):
            status, read_called, _ = await _send_raw_asgi(bad_headers)
            assert status == 403
            assert read_called is False

        # Unauthenticated non-loopback peer must reject with 403 BEFORE reading the body.
        status_remote, read_remote, _ = await _send_raw_asgi(
            [(b"host", b"127.0.0.1:48911"), (b"content-type", b"application/json")],
            client=("192.168.1.50", 50000),
        )
        assert status_remote == 403
        assert read_remote is False
        assert len(debug_router._HEALTH_RING) == 0

        # Valid same-origin JSON POST succeeds.
        status_ok, read_ok, raw_body = await _send_raw_asgi(
            same_origin_headers
            + [(b"sec-fetch-site", b"same-origin"), (b"content-type", b"application/json")]
        )
        assert status_ok == 200
        assert read_ok is True
        assert json.loads(raw_body) == {"ok": True}
        assert len(debug_router._HEALTH_RING) == 1
        assert debug_router._HEALTH_RING[0]["client"] == {"ts": 1.0, "dom_nodes": 42}

        # Internal error in debug_health_client must return 500 without str(exc).
        monkeypatch.setattr(
            debug_router,
            "_sanitize_client_payload",
            lambda _payload: (_ for _ in ()).throw(RuntimeError("SECRET_DB_TOKEN")),
        )
        status_err, _, err_body = await _send_raw_asgi(
            same_origin_headers + [(b"content-type", b"application/json")]
        )
        assert status_err == 500
        assert json.loads(err_body) == {"ok": False, "error": "internal_error"}
        assert b"SECRET_DB_TOKEN" not in err_body

        # _collect_snapshot widget_mode error must record only error_type, not str(exc).
        from main_logic import widget_mode_runtime

        monkeypatch.setattr(
            widget_mode_runtime.widget_mode_coordinator,
            "snapshot",
            lambda: (_ for _ in ()).throw(RuntimeError("SECRET_WIDGET_PATH")),
        )
        snap = debug_router._collect_snapshot(include_deep=False, channel="endpoint")
        assert snap["widget_mode"] == {"error_type": "RuntimeError"}
        assert "SECRET_WIDGET_PATH" not in json.dumps(snap)
    finally:
        debug_router._HEALTH_RING.clear()
        debug_router._HEALTH_RING.extend(saved_ring)
