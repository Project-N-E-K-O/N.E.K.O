# -*- coding: utf-8 -*-
"""Tests for the global inbound body-size guard (issue #1586).

The middleware caps oversized *non-multipart* request bodies before they reach
any router, by inspecting only ``Content-Length``. Multipart uploads, requests
without ``Content-Length``, and non-http scopes are passed through untouched.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading

import pytest

import utils.asgi_body_limit as body_limit_module

from utils.asgi_body_limit import (
    DEFAULT_MAX_INBOUND_BODY_BYTES,
    InboundBodySizeLimitMiddleware,
)


KNOWLEDGE_SUBSCRIPTION_PATHS = (
    "/api/public-knowledge/subscriptions/apply",
    "/market/knowledge/subscriptions/apply",
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


def test_main_server_guards_both_knowledge_subscription_paths():
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
