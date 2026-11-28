"""Unit tests for transparent loop-scoped LLM HTTP pooling and concurrent cleanup."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from utils.http.pool_utils import (
    DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
    DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
    credential_fingerprint,
    http_origin_key,
    proxy_env_fingerprint,
)

pytestmark = pytest.mark.unit


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    content_length = 0
    for line in head.decode("latin1").split("\r\n")[1:]:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    body = await reader.readexactly(content_length) if content_length else b""
    return head, body


def _json_http_response(payload: dict, *, keep_alive: bool = True) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    conn = "keep-alive" if keep_alive else "close"
    return (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: {conn}\r\n\r\n"
    ).encode("ascii") + body


def _openai_chat_payload(text: str) -> dict:
    return {
        "id": "chatcmpl-pool-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }


def _anthropic_msg_payload(text: str) -> dict:
    return {
        "id": "msg-pool-test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


# ─────────────────────────── pool key helpers ───────────────────────────


def test_http_origin_and_fingerprint_helpers(monkeypatch):
    assert http_origin_key(None) == ("", "", None)
    assert http_origin_key("https://API.Example.Test/v1") == ("https", "api.example.test", 443)
    assert http_origin_key("https://api.example.test:443/v2/chat") == ("https", "api.example.test", 443)
    assert http_origin_key("http://127.0.0.1/v1") == ("http", "127.0.0.1", 80)
    assert http_origin_key("http://127.0.0.1:48911/v1") == ("http", "127.0.0.1", 48911)
    assert http_origin_key("https://example.test:invalid/v1") == ("https", "example.test", 443)

    _clear_proxy_env(monkeypatch)
    fp1 = proxy_env_fingerprint()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    fp2 = proxy_env_fingerprint()
    assert fp1 != fp2

    assert credential_fingerprint("a", "bc") != credential_fingerprint("ab", "c")
    assert credential_fingerprint("secret") == credential_fingerprint("secret")


# ─────────────────────────── real loopback TCP reuse ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [None, "anthropic"])
async def test_create_chat_llm_async_reuses_single_tcp_connection_across_aclose(
    monkeypatch,
    provider_type,
):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    connections = 0
    posts = 0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections, posts
        connections += 1
        try:
            while True:
                try:
                    await _read_http_request(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    # Peer closed the keep-alive socket during teardown.
                    break
                posts += 1
                payload = (
                    _anthropic_msg_payload(f"ok-{posts}")
                    if provider_type == "anthropic"
                    else _openai_chat_payload(f"ok-{posts}")
                )
                writer.write(_json_http_response(payload, keep_alive=True))
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/v1"

    try:
        llm1 = await create_chat_llm_async(
            "test-model",
            base_url,
            "test-key-1",
            max_completion_tokens=64,
            timeout=15.0,
            max_retries=0,
            provider_type=provider_type,
        )
        shared_http = llm1._aclient._client
        assert llm1._borrowed_async_http is True
        assert shared_http._transport._pool._keepalive_expiry == DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
        resp1 = await llm1.ainvoke([{"role": "user", "content": "hi 1"}])
        assert resp1.content == "ok-1"
        await llm1.aclose()
        assert llm1.is_closed is True
        assert shared_http.is_closed is False

        with pytest.raises(RuntimeError, match="closed"):
            await llm1.ainvoke([{"role": "user", "content": "after close"}])

        llm2 = await create_chat_llm_async(
            "test-model-2",
            base_url,
            "test-key-1",
            max_completion_tokens=64,
            timeout=15.0,
            max_retries=0,
            provider_type=provider_type,
        )
        assert llm2._aclient._client is shared_http
        resp2 = await llm2.ainvoke([{"role": "user", "content": "hi 2"}])
        assert resp2.content == "ok-2"
        await llm2.aclose()

        assert posts == 2
        assert connections == 1

        await close_warm_chat_llms()
        assert shared_http.is_closed is True
    finally:
        await close_warm_chat_llms()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_shared_http_pool_retries_dead_reused_socket_once(monkeypatch):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    connections = 0
    posts = 0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections, posts
        connections += 1
        conn_id = connections
        try:
            await _read_http_request(reader)
            posts += 1
            writer.write(_json_http_response(_openai_chat_payload(f"c{conn_id}"), keep_alive=True))
            await writer.drain()
            if conn_id == 1:
                # Wait for the second request to hit the pooled socket, then drop
                # before sending any response headers to simulate a stale socket.
                try:
                    await _read_http_request(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    # Peer closed first during teardown.
                    return
                writer.close()
                return
        except (asyncio.IncompleteReadError, ConnectionError):
            # Expected disconnect on loopback teardown.
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/v1"

    try:
        llm1 = await create_chat_llm_async(
            "test-model",
            base_url,
            "retry-key",
            max_completion_tokens=32,
            timeout=15.0,
            max_retries=0,
        )
        r1 = await llm1.ainvoke([{"role": "user", "content": "first"}])
        await llm1.aclose()
        assert r1.content == "c1"

        llm2 = await create_chat_llm_async(
            "test-model",
            base_url,
            "retry-key",
            max_completion_tokens=32,
            timeout=15.0,
            max_retries=0,
        )
        r2 = await llm2.ainvoke([{"role": "user", "content": "second"}])
        await llm2.aclose()
        assert r2.content == "c2"
        assert connections == 2
        assert posts == 2
    finally:
        await close_warm_chat_llms()
        server.close()
        await server.wait_closed()


# ─────────────────────────── isolation & opt-out ───────────────────────────


@pytest.mark.asyncio
async def test_shared_http_pool_isolates_credentials_headers_proxy_and_opt_out(monkeypatch):
    from utils.llm_client import ChatAnthropic, ChatOpenAI, create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    base_url = "https://pool-isolation.example.test/v1"

    try:
        a = await create_chat_llm_async("m", base_url, "key-a", max_completion_tokens=16, timeout=10.0)
        b = await create_chat_llm_async("m", base_url, "key-b", max_completion_tokens=16, timeout=10.0)
        assert a._aclient._client is not b._aclient._client

        # Anthropic-via-header path uses a placeholder api_key and real x-api-key header.
        h1 = await create_chat_llm_async(
            "m", base_url, "placeholder", default_headers={"x-api-key": "anth-1"}, max_completion_tokens=16, timeout=10.0
        )
        h2 = await create_chat_llm_async(
            "m", base_url, "placeholder", default_headers={"x-api-key": "anth-2"}, max_completion_tokens=16, timeout=10.0
        )
        assert h1._aclient._client is not h2._aclient._client

        # Proxy env change creates a distinct pooled client.
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
        a_proxy = await create_chat_llm_async("m", base_url, "key-a", max_completion_tokens=16, timeout=10.0)
        assert a_proxy._aclient._client is not a._aclient._client
        _clear_proxy_env(monkeypatch)

        # Explicit keepalive_expiry or use_shared_http_pool=False builds an owned client.
        owned_ka = await create_chat_llm_async(
            "m", base_url, "key-a", max_completion_tokens=16, timeout=10.0, keepalive_expiry=30.0
        )
        owned_opt_out = await create_chat_llm_async(
            "m", base_url, "key-a", max_completion_tokens=16, timeout=10.0, use_shared_http_pool=False
        )
        assert owned_ka._borrowed_async_http is False
        assert owned_opt_out._borrowed_async_http is False
        assert owned_ka._aclient._client is not a._aclient._client
        assert owned_opt_out._aclient._client is not a._aclient._client

        # Closing owned clients closes their underlying httpx client; shared client stays open.
        await owned_ka.aclose()
        await owned_opt_out.aclose()
        assert owned_ka._aclient._client.is_closed is True
        assert owned_opt_out._aclient._client.is_closed is True
        assert a._aclient._client.is_closed is False

        # Direct sync construction does not borrow from the async shared pool.
        direct_oa = ChatOpenAI("m", base_url, "key-a", max_completion_tokens=16, timeout=10.0)
        direct_an = ChatAnthropic("m", base_url, "key-a", max_completion_tokens=16, timeout=10.0)
        assert direct_oa._borrowed_async_http is False
        assert direct_an._borrowed_async_http is False
        await direct_oa.aclose()
        await direct_an.aclose()

        for client in (a, b, h1, h2, a_proxy):
            await client.aclose()
    finally:
        await close_warm_chat_llms()


@pytest.mark.asyncio
async def test_create_chat_llm_async_preserves_monkeypatched_factory_signature(monkeypatch):
    from utils import llm_client

    seen: list[tuple[tuple, dict]] = []

    def fake_create(*args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(aclose=AsyncMock())

    monkeypatch.setattr(llm_client, "create_chat_llm", fake_create)
    res = await llm_client.create_chat_llm_async(
        "gpt-test",
        "https://api.example.test/v1",
        "k",
        max_completion_tokens=32,
        timeout=12.0,
    )
    assert res is not None
    assert seen == [
        (
            ("gpt-test", "https://api.example.test/v1", "k"),
            {"max_completion_tokens": 32, "timeout": 12.0},
        )
    ]


@pytest.mark.asyncio
async def test_borrowed_finalizer_does_not_close_shared_http_client(monkeypatch):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    try:
        llm_oa = await create_chat_llm_async(
            "m", "https://finalizer.example.test/v1", "k", max_completion_tokens=16, timeout=10.0
        )
        llm_an = await create_chat_llm_async(
            "m",
            "https://finalizer.example.test/v1",
            "k",
            max_completion_tokens=16,
            timeout=10.0,
            provider_type="anthropic",
        )
        oa_http = llm_oa._aclient._client
        an_http = llm_an._aclient._client
        # Invoke finalizers directly as GC would when callers forget aclose().
        llm_oa._client_finalizer()
        llm_an._client_finalizer()
        await asyncio.sleep(0)
        assert oa_http.is_closed is False
        assert an_http.is_closed is False
    finally:
        await close_warm_chat_llms()


# ─────────────────────────── concurrent close & per-call timeout ───────────────────────────


@pytest.mark.asyncio
async def test_close_warm_chat_llms_closes_warm_and_shared_pools_concurrently():
    from collections import OrderedDict

    from utils.llm_client import http_pool, warm_pool

    loop = asyncio.get_running_loop()
    client1_entered = asyncio.Event()
    client2_entered = asyncio.Event()
    shared_entered = asyncio.Event()
    closed: list[str] = []

    class _BarrierClient:
        def __init__(self, name: str, own_event: asyncio.Event, peer_events: list[asyncio.Event]):
            self.name = name
            self.own_event = own_event
            self.peer_events = peer_events

        async def aclose(self) -> None:
            self.own_event.set()
            for evt in self.peer_events:
                await asyncio.wait_for(evt.wait(), timeout=1.0)
            closed.append(self.name)

    c1 = _BarrierClient("warm-1", client1_entered, [client2_entered, shared_entered])
    c2 = _BarrierClient("warm-2", client2_entered, [client1_entered, shared_entered])
    sh = _BarrierClient("shared-1", shared_entered, [client1_entered, client2_entered])

    warm_pool._pools[loop] = OrderedDict([(("k1",), c1), (("k2",), c2)])
    with http_pool._POOL_LOCK:
        http_pool._http_pools[loop] = OrderedDict([(("sh1",), sh)])

    # Sequential closing would deadlock on the barrier; concurrent gather completes immediately.
    await asyncio.wait_for(warm_pool.close_warm_chat_llms(), timeout=1.5)
    assert set(closed) == {"warm-1", "warm-2", "shared-1"}


def test_per_call_timeout_splitting_symmetry_openai_and_anthropic():
    from utils.llm_client import ChatAnthropic, ChatOpenAI

    oa = ChatOpenAI("m", "https://api.example.test/v1", "k", timeout=60.0)
    an = ChatAnthropic("m", "https://api.example.test/v1", "k", timeout=60.0)
    try:
        expected_long = httpx.Timeout(
            connect=DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
            read=120.0,
            write=DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
            pool=DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
        )
        expected_short = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
        explicit = httpx.Timeout(connect=2.0, read=25.0, write=8.0, pool=3.0)

        assert oa._params([{"role": "user", "content": "hi"}], timeout=120.0)["timeout"] == expected_long
        assert an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": 120.0})["timeout"] == expected_long

        assert oa._params([{"role": "user", "content": "hi"}], timeout=5.0)["timeout"] == expected_short
        assert an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": 5.0})["timeout"] == expected_short

        assert oa._params([{"role": "user", "content": "hi"}], timeout=explicit)["timeout"] is explicit
        assert an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": explicit})["timeout"] is explicit

        for disabled in (False, 0, -3.0, None):
            assert "timeout" not in oa._params([{"role": "user", "content": "hi"}], timeout=disabled)
            assert "timeout" not in an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": disabled})
    finally:
        oa.close()
        an.close()
