# -*- coding: utf-8 -*-
"""Tests for the inbound body-size guard (issue #1586).

The middleware caps oversized *non-multipart* request bodies before they reach
any router, by inspecting only ``Content-Length``. Applications can opt a
specific multipart route into preflight and streamed aggregate limits.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from utils.asgi_body_limit import (
    DEFAULT_MAX_INBOUND_BODY_BYTES,
    InboundBodySizeLimitMiddleware,
)


def _run(coro):
    return asyncio.run(coro)


def _http_scope(headers):
    return {"type": "http", "method": "POST", "path": "/x", "headers": list(headers)}


async def _drive(middleware, scope):
    """Run the middleware once; return (downstream_called, sent_messages)."""
    called = {"hit": False}

    async def downstream(_scope, _receive, _send):
        called["hit"] = True
        # A real app would respond; emit a trivial 200 so send() is exercised.
        await _send({"type": "http.response.start", "status": 200, "headers": []})
        await _send({"type": "http.response.body", "body": b"ok"})

    middleware.app = downstream

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return called["hit"], sent


def _make(max_bytes=64):
    # Tiny cap keeps the test payloads small; the guard logic is size-agnostic.
    return InboundBodySizeLimitMiddleware(app=None, max_body_bytes=max_bytes)


def _make_bounded(max_bytes=64, preflight=None):
    return InboundBodySizeLimitMiddleware(
        app=None,
        max_body_bytes=1024,
        multipart_path_prefix="/api/avatar-tools",
        multipart_methods=("POST", "PUT"),
        max_multipart_body_bytes=max_bytes,
        multipart_preflight=preflight,
    )


def test_under_limit_passes_through():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"10"), (b"content-type", b"application/json")])
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_at_limit_passes_through():
    """Exactly at the cap is allowed (uses ``>`` not ``>=``)."""
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"64"), (b"content-type", b"application/json")])
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_over_limit_rejected_with_413():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"65"), (b"content-type", b"application/json")])
    hit, sent = _run(_drive(mw, scope))
    assert hit is False, "downstream app must not be reached"
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"].decode("utf-8"))
    assert payload["ok"] is False
    assert payload["error_code"] == "payload_too_large"
    assert payload["max_bytes"] == 64


def test_multipart_over_limit_is_exempt():
    """File uploads (multipart) are exempt — routers stream-guard them."""
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"100000000"),
            (b"content-type", b"multipart/form-data; boundary=----abc"),
        ]
    )
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_multipart_content_type_is_case_insensitive():
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"100000000"),
            (b"content-type", b"  Multipart/Form-Data; boundary=xyz"),
        ]
    )
    hit, _sent = _run(_drive(mw, scope))
    assert hit is True


def test_bounded_multipart_route_rejects_known_oversize_before_downstream():
    mw = _make_bounded(max_bytes=64)
    scope = {
        **_http_scope(
            [
                (b"content-length", b"65"),
                (b"content-type", b"multipart/form-data; boundary=abc"),
            ]
        ),
        "path": "/api/avatar-tools/local-example",
        "method": "PUT",
    }
    hit, sent = _run(_drive(mw, scope))
    assert hit is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["max_bytes"] == 64


def test_bounded_multipart_route_counts_body_without_content_length():
    mw = _make_bounded(max_bytes=5)
    scope = {
        **_http_scope([(b"content-type", b"multipart/form-data; boundary=abc")]),
        "path": "/api/avatar-tools",
    }
    chunks = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    sent = []

    async def downstream(_scope, receive, _send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    mw.app = downstream

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    _run(mw(scope, receive, send))
    assert sent[0]["status"] == 413


def test_streamed_multipart_overflow_keeps_the_outer_response_through_fastapi():
    app = FastAPI()

    @app.post("/api/avatar-tools")
    async def parse_form(request: Request):
        await request.form()
        return {"ok": True}

    guarded = InboundBodySizeLimitMiddleware(
        app,
        max_body_bytes=1024,
        multipart_path_prefix="/api/avatar-tools",
        multipart_methods=("POST",),
        max_multipart_body_bytes=32,
    )
    body = (
        b"--abc\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n"
        + b"x" * 64
        + b"\r\n--abc--\r\n"
    )

    with TestClient(guarded) as client:
        response = client.post(
            "/api/avatar-tools",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=abc",
                "Content-Length": "1",
            },
        )

    assert response.status_code == 413
    assert response.json()["error_code"] == "payload_too_large"
    assert response.headers["connection"] == "close"


def test_bounded_multipart_preflight_rejects_before_body_is_read():
    from fastapi.responses import JSONResponse

    received = {"hit": False}

    def preflight(_scope):
        return JSONResponse(status_code=403, content={"error_code": "csrf_validation_failed"})

    mw = _make_bounded(preflight=preflight)
    scope = {
        **_http_scope([(b"content-type", b"multipart/form-data; boundary=abc")]),
        "path": "/api/avatar-tools",
    }

    async def receive():
        received["hit"] = True
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    _run(mw(scope, receive, send))
    assert received["hit"] is False
    assert sent[0]["status"] == 403


def test_missing_content_length_passes_through():
    """Chunked / unknown-length requests are not rejected."""
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-type", b"application/json")])
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_malformed_content_length_passes_through():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"not-a-number"), (b"content-type", b"application/json")])
    hit, _sent = _run(_drive(mw, scope))
    assert hit is True


def test_over_limit_without_content_type_rejected():
    """No Content-Type defaults to non-multipart → still capped."""
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"65")])
    hit, sent = _run(_drive(mw, scope))
    assert hit is False
    assert sent[0]["status"] == 413


def test_websocket_scope_passes_through():
    """Non-http scopes are forwarded untouched (Pet realtime ws must survive)."""
    mw = _make(max_bytes=64)
    scope = {"type": "websocket", "path": "/ws", "headers": [(b"content-length", b"999999")]}
    hit, _sent = _run(_drive(mw, scope))
    assert hit is True


def test_default_cap_is_16_mib():
    assert DEFAULT_MAX_INBOUND_BODY_BYTES == 16 * 1024 * 1024
