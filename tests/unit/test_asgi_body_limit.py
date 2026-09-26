# -*- coding: utf-8 -*-
"""Tests for the inbound body-size guard (issue #1586).

The middleware caps oversized *non-multipart* request bodies before they reach
any router, by inspecting only ``Content-Length``. Applications can opt a
specific multipart route into preflight and streamed aggregate limits.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading

import pytest

import utils.asgi_body_limit as body_limit_module

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from utils.asgi_body_limit import (
    DEFAULT_MAX_INBOUND_BODY_BYTES,
    InboundBodySizeLimitMiddleware,
)


KNOWLEDGE_SUBSCRIPTION_PATHS = (
    "/api/public-knowledge/subscriptions/apply",
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


async def _drive_chunks(middleware, scope, chunks):
    called = {"hit": False, "body": b""}

    async def downstream(_scope, receive, send):
        called["hit"] = True
        parts = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            parts.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        called["body"] = b"".join(parts)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware.app = downstream
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return called, sent


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


def test_main_server_guards_only_internal_knowledge_subscription_apply():
    from app.main_server import app
    from knowledge.limits import MAX_SUBSCRIPTION_ENVELOPE_BYTES

    body_guard = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls is InboundBodySizeLimitMiddleware
    )

    assert body_guard.kwargs["streamed_path_limits"] == {
        path: MAX_SUBSCRIPTION_ENVELOPE_BYTES
        for path in KNOWLEDGE_SUBSCRIPTION_PATHS
    }


@pytest.mark.parametrize("path", KNOWLEDGE_SUBSCRIPTION_PATHS)
def test_exact_streamed_path_rejects_declared_oversized_multipart(path):
    mw = InboundBodySizeLimitMiddleware(None, streamed_path_limits={path: 64})
    scope = _http_scope(
        [(b"content-length", b"65"), (b"content-type", b"multipart/form-data")]
    )
    scope["path"] = path

    hit, sent = _run(_drive(mw, scope))

    assert hit is False
    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload == {
        "ok": False,
        "error_code": "knowledge_request_too_large",
        "max_bytes": 64,
        "error": "请求体超过允许的体积上限。",
    }


@pytest.mark.parametrize("path", KNOWLEDGE_SUBSCRIPTION_PATHS)
def test_exact_streamed_path_rejects_actual_oversized_chunked_multipart(path):
    mw = InboundBodySizeLimitMiddleware(None, streamed_path_limits={path: 64})
    scope = _http_scope([(b"content-type", b"multipart/form-data")])
    scope["path"] = path

    called, sent = _run(_drive_chunks(mw, scope, [b"a" * 40, b"b" * 25]))

    assert called["hit"] is False
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["error"] == "请求体超过允许的体积上限。"


@pytest.mark.parametrize("path", KNOWLEDGE_SUBSCRIPTION_PATHS)
def test_exact_streamed_path_replays_valid_body_without_changing_bytes(path):
    mw = InboundBodySizeLimitMiddleware(None, streamed_path_limits={path: 64})
    scope = _http_scope([(b"content-type", b"multipart/form-data")])
    scope["path"] = path

    called, sent = _run(_drive_chunks(mw, scope, [b"first", b"second"]))

    assert called == {"hit": True, "body": b"firstsecond"}
    assert sent[0]["status"] == 200


def test_streamed_spool_file_io_runs_off_the_event_loop(monkeypatch):
    event_thread = threading.get_ident()
    real_spooled_temporary_file = tempfile.SpooledTemporaryFile
    calls: dict[str, list[int]] = {
        "write": [],
        "seek": [],
        "read": [],
        "close": [],
    }

    class RecordingSpool:
        def __init__(self):
            self._spool = real_spooled_temporary_file(max_size=1)

        def write(self, body):
            calls["write"].append(threading.get_ident())
            return self._spool.write(body)

        def seek(self, offset):
            calls["seek"].append(threading.get_ident())
            return self._spool.seek(offset)

        def read(self, size):
            calls["read"].append(threading.get_ident())
            return self._spool.read(size)

        def close(self):
            calls["close"].append(threading.get_ident())
            return self._spool.close()

    monkeypatch.setattr(
        body_limit_module.tempfile,
        "SpooledTemporaryFile",
        lambda **_kwargs: RecordingSpool(),
    )
    path = "/api/public-knowledge/subscriptions/apply"
    middleware = InboundBodySizeLimitMiddleware(
        None,
        streamed_path_limits={path: 128},
    )
    scope = _http_scope([(b"content-type", b"multipart/form-data")])
    scope["path"] = path

    called, _sent = _run(_drive_chunks(middleware, scope, [b"a" * 64, b"b"]))

    assert called["body"] == b"a" * 64 + b"b"
    assert all(calls.values())
    assert all(
        thread_id != event_thread
        for method_calls in calls.values()
        for thread_id in method_calls
    )


def test_streamed_spool_closes_when_receive_is_cancelled(monkeypatch):
    close_threads = []

    class RecordingSpool:
        def close(self):
            close_threads.append(threading.get_ident())

    spool = RecordingSpool()
    monkeypatch.setattr(
        body_limit_module.tempfile,
        "SpooledTemporaryFile",
        lambda **_kwargs: spool,
    )

    async def receive():
        raise asyncio.CancelledError

    event_thread = threading.get_ident()
    with pytest.raises(asyncio.CancelledError):
        _run(
            InboundBodySizeLimitMiddleware._spool_bounded_body(
                receive,
                max_bytes=128,
            )
        )

    assert close_threads and close_threads[0] != event_thread


def test_streamed_spool_closes_and_preserves_write_error(monkeypatch):
    close_threads = []

    class FailingSpool:
        def write(self, _body):
            raise OSError("spool write failed")

        def close(self):
            close_threads.append(threading.get_ident())

    spool = FailingSpool()
    monkeypatch.setattr(
        body_limit_module.tempfile,
        "SpooledTemporaryFile",
        lambda **_kwargs: spool,
    )

    async def receive():
        return {"type": "http.request", "body": b"body", "more_body": False}

    event_thread = threading.get_ident()
    with pytest.raises(OSError, match="spool write failed"):
        _run(
            InboundBodySizeLimitMiddleware._spool_bounded_body(
                receive,
                max_bytes=128,
            )
        )

    assert close_threads and close_threads[0] != event_thread


# --- 回归面：配置了 bounded multipart 之后，不匹配的请求必须原样走旧逻辑 ---


def _scope(method, path, headers):
    return {"type": "http", "method": method, "path": path, "headers": list(headers)}


def test_bounded_config_leaves_other_multipart_routes_exempt():
    """A multipart upload elsewhere keeps the pre-existing exemption."""
    middleware = _make_bounded(max_bytes=64)
    hit, sent = _run(_drive(middleware, _scope("POST", "/api/memory", [
        (b"content-type", b"multipart/form-data; boundary=x"),
        (b"content-length", b"999999"),
    ])))
    assert hit is True
    assert sent[0]["status"] == 200


def test_bounded_prefix_does_not_capture_sibling_paths():
    """Prefix matching must not swallow /api/avatar-toolsX or /api/avatar-tools-foo."""
    for path in ("/api/avatar-toolsX", "/api/avatar-tools-foo", "/api/avatar-toolsX/create"):
        middleware = _make_bounded(max_bytes=64)
        hit, sent = _run(_drive(middleware, _scope("POST", path, [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", b"999999"),
        ])))
        assert hit is True, f"{path} was wrongly treated as the bounded route"
        assert sent[0]["status"] == 200


def test_bounded_route_ignores_methods_outside_the_configured_set():
    """Only POST/PUT are bounded; other verbs keep the multipart exemption."""
    for method in ("GET", "DELETE", "PATCH"):
        middleware = _make_bounded(max_bytes=64)
        hit, sent = _run(_drive(middleware, _scope(method, "/api/avatar-tools", [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", b"999999"),
        ])))
        assert hit is True, f"{method} was wrongly bounded"
        assert sent[0]["status"] == 200


def test_bounded_route_still_uses_the_global_cap_when_not_multipart():
    """A JSON body on the bounded path is capped by the global limit, not the multipart one."""
    middleware = _make_bounded(max_bytes=10_000_000)
    hit, sent = _run(_drive(middleware, _scope("POST", "/api/avatar-tools", [
        (b"content-type", b"application/json"),
        (b"content-length", b"2048"),  # > global 1024, < multipart cap
    ])))
    assert hit is False
    assert sent[0]["status"] == 413


def test_preflight_is_not_invoked_outside_the_configured_route():
    """The preflight hook must never run for another path or an unguarded method."""
    calls = []

    def preflight(scope):
        calls.append(scope.get("path"))
        return None

    for method, path, content_type in (
        ("POST", "/api/memory", b"multipart/form-data; boundary=x"),
        ("GET", "/api/avatar-tools", b"multipart/form-data; boundary=x"),
        ("DELETE", "/api/avatar-tools/local-1", b"multipart/form-data; boundary=x"),
    ):
        middleware = _make_bounded(max_bytes=64, preflight=preflight)
        _run(_drive(middleware, _scope(method, path, [
            (b"content-type", content_type),
            (b"content-length", b"8"),
        ])))
    assert calls == [], f"preflight leaked outside the configured route: {calls}"


def test_preflight_runs_on_the_configured_route_whatever_the_content_type():
    """These handlers declare Form/File, so FastAPI parses the body before the handler.

    Gating the preflight on ``multipart/`` let a cross-origin client switch content
    type and repeatedly force that parse before the in-handler check rejected it.
    """
    for content_type in (
        b"application/x-www-form-urlencoded",
        b"application/json",
        b"text/plain",
        b"",
    ):
        calls = []

        def preflight(scope):
            calls.append(scope.get("path"))
            return _forbidden

        hit, sent = _run(_drive(_make_bounded(max_bytes=10_000_000, preflight=preflight),
                                _scope("POST", "/api/avatar-tools", [
                                    (b"content-type", content_type),
                                    (b"content-length", b"8"),
                                ])))
        assert calls == ["/api/avatar-tools"], content_type
        # 被 preflight 拒掉的请求绝不能到达下游，否则 body 还是会被解析。
        assert hit is False, content_type
        assert sent[0]["status"] == 403, content_type


async def _forbidden(scope, receive, send):
    await send({"type": "http.response.start", "status": 403, "headers": []})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def test_unbounded_requests_get_the_untouched_receive_and_send():
    """Non-bounded traffic must not be wrapped, so streaming behaviour is unchanged."""
    seen = {}

    async def downstream(_scope, receive, send):
        seen["receive"] = receive
        seen["send"] = send
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = _make_bounded(max_bytes=64)
    middleware.app = downstream

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    _run(middleware(_scope("POST", "/api/memory", [
        (b"content-type", b"application/json"),
        (b"content-length", b"8"),
    ]), receive, send))

    assert seen["receive"] is receive, "receive was wrapped for an unbounded request"
    assert seen["send"] is send, "send was wrapped for an unbounded request"


def test_default_construction_never_bounds_any_multipart():
    """Without the opt-in config the middleware behaves exactly as before."""
    middleware = _make(max_bytes=64)
    assert middleware.max_multipart_body_bytes is None
    hit, sent = _run(_drive(middleware, _scope("POST", "/api/avatar-tools", [
        (b"content-type", b"multipart/form-data; boundary=x"),
        (b"content-length", b"999999"),
    ])))
    assert hit is True
    assert sent[0]["status"] == 200
