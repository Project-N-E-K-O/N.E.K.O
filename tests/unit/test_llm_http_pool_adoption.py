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
    monkeypatch.setenv("https_proxy", "")
    fp_empty = proxy_env_fingerprint()
    assert fp1 != fp_empty
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    fp2 = proxy_env_fingerprint()
    assert fp_empty != fp2
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
    requests_received = 0
    responses_sent = 0
    stale_socket_reused = False

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections, requests_received, responses_sent, stale_socket_reused
        connections += 1
        conn_id = connections
        try:
            await _read_http_request(reader)
            requests_received += 1
            writer.write(_json_http_response(_openai_chat_payload(f"c{conn_id}"), keep_alive=True))
            await writer.drain()
            responses_sent += 1
            if conn_id == 1:
                # Drop as soon as the second request hits the stale keep-alive
                # socket before the server reads the full request or replies.
                try:
                    first_byte = await reader.read(1)
                except ConnectionError:
                    # Peer closed first during teardown.
                    return
                if first_byte:
                    stale_socket_reused = True
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
        assert stale_socket_reused is True
        assert connections == 2
        assert requests_received == 2
        assert responses_sent == 2
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
        http_pool._http_pools[loop] = OrderedDict([(("sh1",), http_pool._SharedPoolEntry(sh))])

    # Sequential closing would deadlock on the barrier; concurrent gather completes immediately.
    await asyncio.wait_for(warm_pool.close_warm_chat_llms(), timeout=1.5)
    assert set(closed) == {"warm-1", "warm-2", "shared-1"}


@pytest.mark.asyncio
async def test_shared_http_pool_resolves_env_and_default_base_url_for_pool_key(monkeypatch):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    try:
        # OpenAI: base_url=None resolves OPENAI_BASE_URL (or default https://api.openai.com/v1).
        oa_default = await create_chat_llm_async("m", None, "k-same", max_completion_tokens=16, timeout=10.0)
        oa_explicit_default = await create_chat_llm_async(
            "m", "https://api.openai.com/v1", "k-same", max_completion_tokens=16, timeout=10.0
        )
        assert oa_default._aclient._client is oa_explicit_default._aclient._client

        monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-env-a.example.test/v1")
        oa_env_a = await create_chat_llm_async("m", None, "k-same", max_completion_tokens=16, timeout=10.0)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-env-b.example.test/v1")
        oa_env_b = await create_chat_llm_async("m", None, "k-same", max_completion_tokens=16, timeout=10.0)
        assert oa_env_a._aclient._client is not oa_default._aclient._client
        assert oa_env_a._aclient._client is not oa_env_b._aclient._client

        # Anthropic: base_url=None resolves ANTHROPIC_BASE_URL (or default https://api.anthropic.com).
        an_default = await create_chat_llm_async(
            "m", None, "k-same", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anth-env-a.example.test")
        an_env_a = await create_chat_llm_async(
            "m", None, "k-same", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anth-env-b.example.test")
        an_env_b = await create_chat_llm_async(
            "m", None, "k-same", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        assert an_env_a._aclient._client is not an_default._aclient._client
        assert an_env_a._aclient._client is not an_env_b._aclient._client

        for llm in (oa_default, oa_explicit_default, oa_env_a, oa_env_b, an_default, an_env_a, an_env_b):
            await llm.aclose()
    finally:
        await close_warm_chat_llms()


@pytest.mark.asyncio
async def test_shared_http_pool_eviction_closes_idle_drains_borrowed_and_cleans_on_shutdown(monkeypatch):
    from utils.llm_client import create_chat_llm_async, http_pool
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(http_pool, "SHARED_LLM_HTTP_POOL_MAX_CLIENTS", 2)
    base_url = "https://evict-llm.example.test/v1"

    try:
        llm1 = await create_chat_llm_async("m", base_url, "k1", max_completion_tokens=16, timeout=10.0)
        http1 = llm1._aclient._client

        llm2 = await create_chat_llm_async("m", base_url, "k2", max_completion_tokens=16, timeout=10.0)
        http2 = llm2._aclient._client
        await llm2.aclose()  # k2 is now idle (borrows == 0)

        # k3 evicts k1 (still borrowed): k1 must remain open until llm1 closes.
        llm3 = await create_chat_llm_async("m", base_url, "k3", max_completion_tokens=16, timeout=10.0)
        http3 = llm3._aclient._client
        await asyncio.sleep(0)
        assert http1.is_closed is False

        # k4 evicts k2 (idle): k2 must close promptly on the loop.
        llm4 = await create_chat_llm_async("m", base_url, "k4", max_completion_tokens=16, timeout=10.0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert http2.is_closed is True

        # Closing the last borrower of evicted k1 closes http1 deterministically.
        await llm1.aclose()
        assert http1.is_closed is True

        # k5 evicts k3 (still borrowed): shutdown must close draining http3 too.
        llm5 = await create_chat_llm_async("m", base_url, "k5", max_completion_tokens=16, timeout=10.0)
        assert http3.is_closed is False
        await close_warm_chat_llms()
        assert http3.is_closed is True

        for llm in (llm3, llm4, llm5):
            await llm.aclose()
    finally:
        await close_warm_chat_llms()


@pytest.mark.asyncio
async def test_shared_http_pool_rejects_late_worker_registration_after_aclose(monkeypatch):
    import threading

    from utils.llm_client import create_chat_llm_async, factory as llm_factory, http_pool
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    loop = asyncio.get_running_loop()
    worker_started = threading.Event()
    release_worker = threading.Event()
    built_llms: list = []
    real_create = llm_factory.create_chat_llm

    def gated_create(*args, **kwargs):
        worker_started.set()
        release_worker.wait(timeout=5.0)
        llm = real_create(*args, **kwargs)
        built_llms.append(llm)
        return llm

    monkeypatch.setattr(llm_factory, "create_chat_llm", gated_create)
    try:
        caller = asyncio.create_task(
            create_chat_llm_async(
                "m",
                "https://late-worker.example.test/v1",
                "k-late",
                max_completion_tokens=16,
                timeout=10.0,
            )
        )
        await asyncio.to_thread(worker_started.wait, 5.0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        # Shutdown completes while the cancelled worker thread is still blocked.
        await close_warm_chat_llms()
        release_worker.set()
        # Let the worker thread finish and _close_cancelled_chat_llm_result run.
        for _ in range(50):
            if built_llms and built_llms[0].is_closed:
                break
            await asyncio.sleep(0.02)

        assert len(built_llms) == 1
        assert built_llms[0]._borrowed_async_http is False
        assert built_llms[0]._aclient._client.is_closed is True
        with http_pool._POOL_LOCK:
            assert http_pool._http_pools.get(loop) is None

        # A fresh call on the same loop after shutdown picks up the new epoch and pools again.
        monkeypatch.setattr(llm_factory, "create_chat_llm", real_create)
        fresh = await create_chat_llm_async(
            "m",
            "https://late-worker.example.test/v1",
            "k-fresh",
            max_completion_tokens=16,
            timeout=10.0,
        )
        assert fresh._borrowed_async_http is True
        await fresh.aclose()
    finally:
        release_worker.set()
        await close_warm_chat_llms()


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

        assert oa._params([{"role": "user", "content": "hi"}], timeout=None)["timeout"] is None
        assert an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": None})["timeout"] is None

        for disabled in (False, 0, -3.0):
            assert "timeout" not in oa._params([{"role": "user", "content": "hi"}], timeout=disabled)
            assert "timeout" not in an._build_payload_for_call([{"role": "user", "content": "hi"}], {"timeout": disabled})
    finally:
        oa.close()
        an.close()


@pytest.mark.asyncio
async def test_shared_http_pool_preserves_empty_base_url_and_isolates_anthropic_auth_token(monkeypatch):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    try:
        # Empty base_url="" must NOT silently fall back to public OpenAI/Anthropic endpoints.
        oa_empty = await create_chat_llm_async("m", "", "k-empty", max_completion_tokens=16, timeout=10.0)
        assert str(oa_empty._aclient.base_url) == ""

        monkeypatch.setenv("OPENAI_BASE_URL", "")
        oa_env_empty = await create_chat_llm_async("m", None, "k-empty", max_completion_tokens=16, timeout=10.0)
        assert str(oa_env_empty._aclient.base_url) == ""

        monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
        an_env_empty = await create_chat_llm_async(
            "m", None, "k-empty", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        assert str(an_env_empty._aclient.base_url) == ""

        # Different ANTHROPIC_AUTH_TOKEN values isolate Anthropic pooled clients.
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-1")
        an_tok1 = await create_chat_llm_async(
            "m", "https://anth-tok.example.test", "k-same", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-2")
        an_tok2 = await create_chat_llm_async(
            "m", "https://anth-tok.example.test", "k-same", max_completion_tokens=16, timeout=10.0, provider_type="anthropic"
        )
        assert an_tok1._aclient._client is not an_tok2._aclient._client

        for llm in (oa_empty, oa_env_empty, an_env_empty, an_tok1, an_tok2):
            await llm.aclose()
    finally:
        await close_warm_chat_llms()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [None, "anthropic"])
async def test_shared_http_pool_releases_borrow_on_sdk_constructor_error(monkeypatch, provider_type):
    from utils.llm_client import create_chat_llm_async, http_pool
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(http_pool, "SHARED_LLM_HTTP_POOL_MAX_CLIENTS", 1)

    try:
        good = await create_chat_llm_async(
            "m",
            "https://bad-port.example.test",
            "k1",
            max_completion_tokens=16,
            timeout=10.0,
            provider_type=provider_type,
        )
        good_http = good._aclient._client
        await good.aclose()

        # Invalid port hits the same origin key (port fallback 443) then fails inside SDK.
        with pytest.raises(httpx.InvalidURL):
            await create_chat_llm_async(
                "m",
                "https://bad-port.example.test:bad",
                "k1",
                max_completion_tokens=16,
                timeout=10.0,
                provider_type=provider_type,
            )

        # Borrow count must have been released, so evicting good_http closes it promptly.
        evictor = await create_chat_llm_async(
            "m",
            "https://other.example.test",
            "k2",
            max_completion_tokens=16,
            timeout=10.0,
            provider_type=provider_type,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert good_http.is_closed is True
        await evictor.aclose()
    finally:
        await close_warm_chat_llms()


@pytest.mark.asyncio
async def test_shared_http_pool_is_stateless_and_rejects_set_cookie(monkeypatch):
    from utils.llm_client import create_chat_llm_async
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    seen_heads: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    head, _ = await _read_http_request(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                seen_heads.append(head)
                body = json.dumps(_openai_chat_payload(f"ok-{len(seen_heads)}")).encode("utf-8")
                resp = (
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Set-Cookie: session_id=leak-me; Path=/\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                ).encode("ascii") + body
                writer.write(resp)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/v1"

    try:
        llm1 = await create_chat_llm_async("m", base_url, "k-cookie", max_completion_tokens=16, timeout=10.0)
        await llm1.ainvoke([{"role": "user", "content": "one"}])
        await llm1.aclose()

        llm2 = await create_chat_llm_async("m", base_url, "k-cookie", max_completion_tokens=16, timeout=10.0)
        assert llm2._aclient._client is llm1._aclient._client
        await llm2.ainvoke([{"role": "user", "content": "two"}])
        await llm2.aclose()

        assert len(seen_heads) == 2
        assert b"cookie:" not in seen_heads[1].lower()
    finally:
        await close_warm_chat_llms()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_aclose_shared_llm_http_clients_preserves_in_flight_close_on_waiter_cancel():
    from collections import OrderedDict

    from utils.llm_client import http_pool

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    allow_finish = asyncio.Event()
    calls = 0

    class _SlowClient:
        async def aclose(self) -> None:
            nonlocal calls
            calls += 1
            started.set()
            await allow_finish.wait()

    with http_pool._POOL_LOCK:
        http_pool._http_pools[loop] = OrderedDict([(("slow",), http_pool._SharedPoolEntry(_SlowClient()))])

    first_shutdown = asyncio.create_task(http_pool.aclose_shared_llm_http_clients())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    first_shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_shutdown

    # A second shutdown awaits the exact same in-flight close task without calling aclose() twice.
    second_shutdown = asyncio.create_task(http_pool.aclose_shared_llm_http_clients())
    await asyncio.sleep(0)
    assert second_shutdown.done() is False
    allow_finish.set()
    await asyncio.wait_for(second_shutdown, timeout=1.0)
    assert calls == 1


@pytest.mark.asyncio
async def test_create_chat_llm_async_rebuilds_if_shutdown_closes_borrowed_http_mid_build(monkeypatch):
    import threading

    from utils.llm_client import create_chat_llm_async, factory as llm_factory
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    borrowed_done = threading.Event()
    release_return = threading.Event()
    real_create = llm_factory.create_chat_llm
    call_count = 0

    def slow_after_borrow(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        llm = real_create(*args, **kwargs)
        if call_count == 1:
            borrowed_done.set()
            release_return.wait(timeout=5.0)
        return llm

    monkeypatch.setattr(llm_factory, "create_chat_llm", slow_after_borrow)
    try:
        build_task = asyncio.create_task(
            create_chat_llm_async(
                "m",
                "https://mid-shutdown.example.test/v1",
                "k-mid",
                max_completion_tokens=16,
                timeout=10.0,
            )
        )
        await asyncio.to_thread(borrowed_done.wait, 5.0)
        await close_warm_chat_llms()
        release_return.set()

        delivered = await asyncio.wait_for(build_task, timeout=5.0)
        assert call_count == 2
        assert delivered._borrowed_async_http is False
        assert delivered._aclient._client.is_closed is False
        await delivered.aclose()
    finally:
        release_return.set()
        await close_warm_chat_llms()


@pytest.mark.asyncio
async def test_deferred_borrower_aclose_stays_tracked_when_cancelled(monkeypatch):
    from utils.llm_client import create_chat_llm_async, http_pool
    from utils.llm_client.warm_pool import close_warm_chat_llms

    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(http_pool, "SHARED_LLM_HTTP_POOL_MAX_CLIENTS", 1)
    started = asyncio.Event()
    allow_finish = asyncio.Event()
    close_calls = 0

    try:
        llm1 = await create_chat_llm_async(
            "m", "https://defer-cancel.example.test/v1", "k1", max_completion_tokens=16, timeout=10.0
        )
        http1 = llm1._aclient._client
        orig_aclose = http1.aclose

        async def slow_aclose():
            nonlocal close_calls
            close_calls += 1
            started.set()
            await allow_finish.wait()
            await orig_aclose()

        monkeypatch.setattr(http1, "aclose", slow_aclose)

        # Evict k1 while llm1 still holds a borrow.
        llm2 = await create_chat_llm_async(
            "m", "https://defer-cancel.example.test/v1", "k2", max_completion_tokens=16, timeout=10.0
        )

        # Cancel llm1.aclose() midway: caller receives CancelledError, but pool keeps tracking the close task.
        closer = asyncio.create_task(llm1.aclose())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closer

        # Concurrent shutdown must wait for the in-flight close task without calling aclose() twice.
        shutdown_task = asyncio.create_task(http_pool.aclose_shared_llm_http_clients())
        await asyncio.sleep(0)
        assert shutdown_task.done() is False
        allow_finish.set()
        await asyncio.wait_for(shutdown_task, timeout=1.0)
        assert close_calls == 1
        assert http1.is_closed is True
        await llm2.aclose()
    finally:
        allow_finish.set()
        await close_warm_chat_llms()


def test_snapshot_http_proxy_config_handles_mixed_case_and_shares_proxy_lock():
    from main_routers.config_router import proxy as proxy_router
    from utils.http import PROXY_ENV_LOCK
    from utils.http.pool_utils import _proxies_from_env_items, _proxy_mount_map_from_proxies

    assert proxy_router._PROXY_LOCK is PROXY_ENV_LOCK

    proxies_mixed = _proxies_from_env_items([("Https_Proxy", "http://127.0.0.1:7890")])
    assert proxies_mixed == {"https": "http://127.0.0.1:7890"}
    assert _proxy_mount_map_from_proxies(proxies_mixed) == {"https://": "http://127.0.0.1:7890"}

    proxies_cleared = _proxies_from_env_items(
        [("Https_Proxy", "http://127.0.0.1:7890"), ("https_proxy", "")]
    )
    assert proxies_cleared == {}
