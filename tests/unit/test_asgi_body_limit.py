# -*- coding: utf-8 -*-
"""Tests for the global inbound body-size guard (issue #1586).

The middleware caps oversized request bodies before they reach any router, by
inspecting ``Content-Length`` and by counting bytes read from the ASGI receive
stream. Known multipart upload routes use a larger finite cap; ordinary routes
cannot bypass the guard by spoofing a multipart content type.
"""
from __future__ import annotations

import asyncio
import json

from utils.asgi_body_limit import (
    DEFAULT_MAX_INBOUND_BODY_BYTES,
    DEFAULT_TRUSTED_MULTIPART_BODY_BYTES,
    InboundBodySizeLimitMiddleware,
)


def _run(coro):
    return asyncio.run(coro)


def _http_scope(headers, *, path="/x"):
    return {"type": "http", "method": "POST", "path": path, "headers": list(headers)}


async def _drive(middleware, scope, receive_messages=None, *, read_body=False):
    """Run the middleware once; return (downstream_called, sent_messages)."""
    called = {"hit": False}
    messages = list(receive_messages or [{"type": "http.request", "body": b"", "more_body": False}])

    async def downstream(_scope, receive, _send):
        called["hit"] = True
        if read_body:
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body"):
                    break
        # A real app would respond; emit a trivial 200 so send() is exercised.
        await _send({"type": "http.response.start", "status": 200, "headers": []})
        await _send({"type": "http.response.body", "body": b"ok"})

    middleware.app = downstream

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return called["hit"], sent


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


def test_trusted_multipart_upload_uses_larger_cap():
    """Known upload routes keep accepting legitimate large multipart bodies."""
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"100000000"),
            (b"content-type", b"multipart/form-data; boundary=----abc"),
        ],
        path="/api/model/vrm/upload",
    )
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_workshop_reference_audio_upload_uses_larger_multipart_cap():
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"100000000"),
            (b"content-type", b"multipart/form-data; boundary=----abc"),
        ],
        path="/api/steam/workshop/upload-reference-audio",
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
        ],
        path="/api/model/mmd/upload",
    )
    hit, _sent = _run(_drive(mw, scope))
    assert hit is True


def test_trusted_dynamic_multipart_upload_path_uses_larger_cap():
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"100000000"),
            (b"content-type", b"multipart/form-data; boundary=xyz"),
        ],
        path="/api/characters/catgirl/YUI/card-face",
    )
    hit, sent = _run(_drive(mw, scope))
    assert hit is True
    assert sent[0]["status"] == 200


def test_untrusted_multipart_content_length_over_limit_rejected():
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [
            (b"content-length", b"65"),
            (b"content-type", b"multipart/form-data; boundary=spoof"),
        ],
        path="/api/memory/recent_file/save",
    )
    hit, sent = _run(_drive(mw, scope))
    assert hit is False
    assert sent[0]["status"] == 413


def test_untrusted_multipart_streaming_over_limit_rejected():
    mw = _make(max_bytes=64)
    scope = _http_scope(
        [(b"content-type", b"multipart/form-data; boundary=spoof")],
        path="/api/memory/recent_file/save",
    )
    hit, sent = _run(_drive(
        mw,
        scope,
        [{"type": "http.request", "body": b"x" * 65, "more_body": False}],
        read_body=True,
    ))
    assert hit is True, "downstream starts reading, but must not reach its 200 response"
    assert sent[0]["status"] == 413


def test_missing_content_length_passes_through():
    """Small chunked / unknown-length requests are allowed."""
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-type", b"application/json")])
    hit, sent = _run(_drive(
        mw,
        scope,
        [{"type": "http.request", "body": b"small", "more_body": False}],
        read_body=True,
    ))
    assert hit is True
    assert sent[0]["status"] == 200


def test_missing_content_length_over_limit_rejected_while_streaming():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-type", b"application/json")])
    hit, sent = _run(_drive(
        mw,
        scope,
        [{"type": "http.request", "body": b"x" * 65, "more_body": False}],
        read_body=True,
    ))
    assert hit is True, "downstream starts reading, but must not reach its 200 response"
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


def test_chunked_body_rejected_when_accumulated_size_exceeds_limit():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-type", b"application/json")])
    hit, sent = _run(_drive(
        mw,
        scope,
        [
            {"type": "http.request", "body": b"x" * 40, "more_body": True},
            {"type": "http.request", "body": b"x" * 25, "more_body": False},
        ],
        read_body=True,
    ))
    assert hit is True
    assert sent[0]["status"] == 413


def test_declared_under_limit_but_streamed_body_over_limit_rejected():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-length", b"10"), (b"content-type", b"application/json")])
    hit, sent = _run(_drive(
        mw,
        scope,
        [{"type": "http.request", "body": b"x" * 65, "more_body": False}],
        read_body=True,
    ))
    assert hit is True
    assert sent[0]["status"] == 413


def test_streaming_overflow_is_not_swallowed_by_broad_exception_handler():
    mw = _make(max_bytes=64)
    scope = _http_scope([(b"content-type", b"application/json")])
    messages = [{"type": "http.request", "body": b"x" * 65, "more_body": False}]

    async def downstream(_scope, receive, send):
        try:
            await receive()
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"swallowed"})

    mw.app = downstream
    sent = []

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    _run(mw(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


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


def test_trusted_multipart_cap_matches_largest_upload_with_envelope_slack():
    assert DEFAULT_TRUSTED_MULTIPART_BODY_BYTES == 10 * 1024 * 1024 * 1024 + DEFAULT_MAX_INBOUND_BODY_BYTES
