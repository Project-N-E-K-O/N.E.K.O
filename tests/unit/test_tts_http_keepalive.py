"""Per-sentence HTTP TTS workers keep their connection between replies.

httpx drops an idle connection after 5s by default, so a reply starting more
than 5s after the previous sentence paid a new TCP + TLS handshake on its
first sentence. The workers now keep idle connections longer, and retry a
request once when the connection it went out on turns out to be dead, which
the longer window makes more likely and httpx does not retry by itself.
"""

import asyncio
import queue
import threading
import time

import httpx
import pytest

from main_logic.tts_client import _http_pool
from main_logic.tts_client._http_pool import (
    TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    install_stale_connection_retry,
    make_tts_http_client,
)
from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL

pytestmark = pytest.mark.unit


# ───────────────── a server that drops a kept-alive connection ─────────────────


async def _read_request(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length:
        await reader.readexactly(length)


async def _serve_then_drop_on_reuse():
    """Answer the first request, then close as the second one lands on that
    connection (the race a server idle timeout produces); every later
    connection is answered normally."""
    connections = []

    async def handle(reader, writer):
        connections.append(writer)
        try:
            await _read_request(reader)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            if len(connections) == 1:
                await _read_request(reader)
                writer.close()
                return
            while True:
                await _read_request(reader)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}/tts", connections


def _two_requests(client_factory):
    async def scenario():
        server, url, connections = await _serve_then_drop_on_reuse()
        client = client_factory()
        try:
            first = await client.post(url, json={"text": "one"})
            second = await client.post(url, json={"text": "two"})
            return first, second, len(connections)
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()

    return asyncio.run(scenario())


def test_plain_httpx_does_not_retry_a_request_on_a_dropped_connection():
    # Why the retry exists: httpx/httpcore surface this instead of retrying.
    with pytest.raises(httpx.RemoteProtocolError):
        _two_requests(lambda: httpx.AsyncClient(trust_env=False))


def test_request_on_a_dropped_connection_is_retried_once_on_a_new_one():
    first, second, connection_count = _two_requests(
        lambda: make_tts_http_client(trust_env=False)
    )

    assert (first.status_code, first.text) == (200, "ok")
    assert (second.status_code, second.text) == (200, "ok")
    assert connection_count == 2


# ───────────────────────────── retry scope ─────────────────────────────


class _ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        await request.aread()  # like a real send, consumes a streamed body
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _post(transport, **kwargs):
    async def scenario():
        client = install_stale_connection_retry(httpx.AsyncClient(transport=transport))
        try:
            return await client.post("https://tts.test/speech", **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(scenario())


@pytest.mark.parametrize("error", [
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.ReadError("[WinError 10054] connection reset"),
    httpx.WriteError("broken pipe"),
])
def test_dead_connection_errors_are_retried_once(error):
    transport = _ScriptedTransport(error, httpx.Response(200, content=b"pcm"))

    response = _post(transport, json={"text": "hi"})

    assert response.content == b"pcm"
    assert transport.calls == 2


def test_a_second_failure_is_not_retried_again():
    error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    transport = _ScriptedTransport(error, error, httpx.Response(200))

    with pytest.raises(httpx.RemoteProtocolError):
        _post(transport, json={"text": "hi"})
    assert transport.calls == 2


@pytest.mark.parametrize("error", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.ConnectTimeout("slow"),
])
def test_other_failures_are_not_retried(error):
    transport = _ScriptedTransport(error, httpx.Response(200))

    with pytest.raises(type(error)):
        _post(transport, json={"text": "hi"})
    assert transport.calls == 1


def test_a_streamed_body_is_not_sent_twice():
    error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    transport = _ScriptedTransport(error, httpx.Response(200))

    async def body():
        yield b"chunk"

    with pytest.raises(httpx.RemoteProtocolError):
        _post(transport, content=body())
    assert transport.calls == 1


def test_a_failure_after_the_response_started_is_not_retried():
    class _BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"half"
            raise httpx.ReadError("connection reset mid-body")

    transport = _ScriptedTransport(
        httpx.Response(200, stream=_BrokenBody()),
        httpx.Response(200, content=b"again"),
    )

    with pytest.raises(httpx.ReadError):
        _post(transport, json={"text": "hi"})
    assert transport.calls == 1


def test_installing_twice_still_retries_only_once():
    error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    transport = _ScriptedTransport(error, error, httpx.Response(200))

    async def scenario():
        client = httpx.AsyncClient(transport=transport)
        install_stale_connection_retry(install_stale_connection_retry(client))
        try:
            return await client.post("https://tts.test/speech", json={})
        finally:
            await client.aclose()

    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(scenario())
    assert transport.calls == 2


# ──────────────────────────── client settings ────────────────────────────


def test_tts_client_keeps_idle_connections_longer_than_httpx_default():
    assert httpx.Limits().keepalive_expiry == 5.0
    assert TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS >= 30

    async def scenario():
        client = make_tts_http_client(timeout=10)
        try:
            pool = client._transport._pool
            return pool._keepalive_expiry, pool._max_connections, pool._max_keepalive_connections
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == (TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS, 4, 2)


def test_environment_proxies_still_apply_and_are_covered(monkeypatch):
    # A custom transport would make httpx ignore HTTP(S)_PROXY; the retry is
    # installed on the transports httpx built, proxy mounts included.
    for bypass in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(bypass, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    async def scenario():
        client = make_tts_http_client()
        try:
            mounted = [t for t in client._mounts.values() if t is not None]
            return mounted
        finally:
            await client.aclose()

    mounted = asyncio.run(scenario())
    assert mounted, "the environment proxy was dropped"
    assert all(
        getattr(t.handle_async_request, "_neko_stale_retry", False) for t in mounted
    )


# ─────────────────────── every sentence worker uses it ───────────────────────


@pytest.mark.parametrize("module_name, worker_name, args, kwargs", [
    ("gemini", "gemini_tts_worker", ("gemini-key", "Kore"), {}),
    ("minimax", "minimax_tts_worker", ("mm-key", "voice"), {"base_url": "https://api.minimaxi.com"}),
    ("cogtts", "cogtts_tts_worker", ("glm-key", "tongtong"), {}),
    ("doubao", "doubao_tts_worker", ("db-key", "zh_female_voice"), {}),
    ("qwen_token_plan", "qwen_token_plan_tts_worker", ("sk-sp-key", "longanlingxin"), {}),
    ("mimo", "mimo_tts_worker", ("mimo-key", "mimo_default"), {}),
])
def test_sentence_workers_build_their_client_with_the_tts_pool(
    monkeypatch, module_name, worker_name, args, kwargs,
):
    import importlib

    module = importlib.import_module(f"main_logic.tts_client.workers.{module_name}")
    created = []
    real_client = httpx.AsyncClient

    def recording_client(**client_kwargs):
        client = real_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
            **client_kwargs,
        )
        created.append((client_kwargs, client))
        return client

    monkeypatch.setattr(httpx, "AsyncClient", recording_client)
    requests = queue.Queue()
    responses = queue.Queue()
    worker = getattr(module, worker_name)
    thread = threading.Thread(
        target=worker, args=(requests, responses, *args), kwargs=kwargs, daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        isinstance(item, tuple) and item[0] == "__ready__" for item in list(responses.queue)
    ):
        time.sleep(0.01)
    requests.put((TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=5)

    assert ("__ready__", True) in list(responses.queue)
    kept = [
        (client_kwargs, client) for client_kwargs, client in created
        if getattr(client_kwargs.get("limits"), "keepalive_expiry", None)
        == TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS
    ]
    assert len(kept) == 1, f"{module_name}: persistent client without the TTS pool: {created}"
    assert getattr(kept[0][1]._transport.handle_async_request, "_neko_stale_retry", False)
    # A connection a middlebox dropped silently answers nothing; with idle
    # connections now kept for 55s, no client may wait for it forever.
    assert kept[0][1].timeout.read is not None, f"{module_name}: no read timeout"


def test_pool_module_names_its_window_once():
    # One constant for every worker; a provider needing another value should
    # say so where it builds its client.
    assert _http_pool.tts_http_limits().keepalive_expiry == TTS_HTTP_KEEPALIVE_EXPIRY_SECONDS
