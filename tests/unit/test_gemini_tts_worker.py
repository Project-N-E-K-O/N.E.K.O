"""Security and error-reporting tests for Gemini TTS worker (F0013)."""

from __future__ import annotations

import base64
import queue

import httpx
import pytest

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import gemini as gemini_worker_mod


def _drain_queue(q: queue.Queue) -> list:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_gemini_tts_worker_sends_key_in_header_not_url_and_redacts_errors(monkeypatch, caplog):
    """F0013: API key must travel in x-goog-api-key header, never in URLs, logs, or error payloads."""
    secret_key = "AIzaSySecretTestKey-DO-NOT-LEAK-98765"
    captured_requests: list[httpx.Request] = []

    pcm_bytes = b"\x01\x00\x02\x00" * 10
    ok_json = {
        "candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(pcm_bytes).decode()}}]}}]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(500, text=f"upstream warmup error for {secret_key}")
        return httpx.Response(200, json=ok_json)

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    req_q.put(("sp1", "Hello world."))
    req_q.put((None, None))
    req_q.put((TTS_SHUTDOWN_SENTINEL, None))

    with caplog.at_level("WARNING"):
        gemini_worker_mod.gemini_tts_worker(req_q, resp_q, f" {secret_key} ", "Leda")

    assert len(captured_requests) == 2
    for req in captured_requests:
        assert "key" not in req.url.params
        assert secret_key not in str(req.url)
        assert req.headers.get("x-goog-api-key") == secret_key

    out = _drain_queue(resp_q)
    assert any(isinstance(item, bytes) for item in out)
    assert all(secret_key not in str(rec.message) for rec in caplog.records)


@pytest.mark.parametrize(
    ("status_code", "expected_snippet"),
    [
        (401, "401"),
        (429, "429"),
    ],
)
def test_gemini_tts_worker_preserves_status_code_without_leaking_key_or_body(
    monkeypatch,
    caplog,
    status_code: int,
    expected_snippet: str,
):
    """Sanitized errors must preserve HTTP 401/429 classification signals while never exposing key or body."""
    secret_key = "AIzaSySecretKey-401-429-XYZ"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={})
        return httpx.Response(
            status_code,
            text=f"upstream rejected key={secret_key} with sensitive details",
        )

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    req_q.put(("sp1", "Hello world."))
    req_q.put((None, None))
    req_q.put((TTS_SHUTDOWN_SENTINEL, None))

    with caplog.at_level("WARNING"):
        gemini_worker_mod.gemini_tts_worker(req_q, resp_q, secret_key, "Leda")

    out = _drain_queue(resp_q)
    errors = [item[1] for item in out if isinstance(item, tuple) and item[0] == "__error__"]
    assert len(errors) == 1
    assert expected_snippet in errors[0]
    assert secret_key not in errors[0]
    assert "sensitive details" not in errors[0]
    assert all(secret_key not in rec.getMessage() for rec in caplog.records)
    assert all("sensitive details" not in rec.getMessage() for rec in caplog.records)


def test_gemini_tts_worker_sanitizes_transport_exception(monkeypatch, caplog):
    """Non-HTTP exceptions (ConnectError etc.) must not leak URL/headers/secrets."""
    secret_key = "AIzaSySecretConnectError-999"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={})
        raise httpx.ConnectError(f"failed talking to {request.url} with {secret_key}", request=request)

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    req_q.put(("sp1", "Hello world."))
    req_q.put((None, None))
    req_q.put((TTS_SHUTDOWN_SENTINEL, None))

    with caplog.at_level("WARNING"):
        gemini_worker_mod.gemini_tts_worker(req_q, resp_q, secret_key, "Leda")

    out = _drain_queue(resp_q)
    errors = [item[1] for item in out if isinstance(item, tuple) and item[0] == "__error__"]
    assert len(errors) == 1
    assert "ConnectError" in errors[0]
    assert secret_key not in errors[0]
    assert all(secret_key not in rec.getMessage() for rec in caplog.records)


def test_gemini_tts_worker_warmup_connect_error_redacts_secret_and_synthesizes(monkeypatch, caplog):
    """Warmup GET ConnectError must be logged without secret leakage while subsequent POST synthesis succeeds."""
    secret_key = "AIzaSySecretWarmupConnect-777"
    captured_methods: list[str] = []

    pcm_bytes = b"\x01\x00\x02\x00" * 10
    ok_json = {
        "candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(pcm_bytes).decode()}}]}}]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured_methods.append(request.method)
        if request.method == "GET":
            raise httpx.ConnectError(f"warmup connect failed for {secret_key}", request=request)
        return httpx.Response(200, json=ok_json)

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: orig_client(*a, transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    req_q.put(("sp1", "Hello world."))
    req_q.put((None, None))
    req_q.put((TTS_SHUTDOWN_SENTINEL, None))

    with caplog.at_level("WARNING"):
        gemini_worker_mod.gemini_tts_worker(req_q, resp_q, secret_key, "Leda")

    assert captured_methods == ["GET", "POST"]
    out = _drain_queue(resp_q)
    assert any(isinstance(item, bytes) and len(item) > 0 for item in out)
    assert not any(isinstance(item, tuple) and item[0] == "__error__" for item in out)
    assert any("ConnectError" in rec.getMessage() for rec in caplog.records)
    assert all(secret_key not in rec.getMessage() for rec in caplog.records)
    assert all(secret_key not in repr(item) for item in out)
