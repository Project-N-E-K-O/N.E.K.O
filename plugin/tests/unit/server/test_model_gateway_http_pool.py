"""Unit tests for ModelGatewayService HTTP connection pooling, retries, and cleanup."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from types import SimpleNamespace

import httpx
import pytest

from plugin.server.application.model_gateway_service import ModelGatewayService
from plugin.server.domain.model_config import ModelSlot
from utils.http.pool_utils import (
    DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
    DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
)

pytestmark = pytest.mark.plugin_unit


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


def _make_slot(base_url: str, *, protocol: str = "openai_chat", api_key: str = "gw-secret", timeout_seconds: int = 120):
    return ModelSlot.model_validate({
        "name": "Gateway pool test",
        "model": "upstream-model",
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "timeout_seconds": timeout_seconds,
        "capabilities": ["text", "streaming"],
    })


def _request_body():
    return {"model": "analysis", "messages": [{"role": "user", "content": "hello"}]}


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await reader.readuntil(b"\r\n\r\n")
    content_length = 0
    for line in head.decode("latin1").split("\r\n")[1:]:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
    body = await reader.readexactly(content_length) if content_length else b""
    return head, body


def _json_http_response(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return (
        f"HTTP/1.1 200 OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: keep-alive\r\n\r\n"
    ).encode("ascii") + body


def _openai_payload(text: str) -> dict:
    return {
        "id": "chatcmpl-gw",
        "object": "chat.completion",
        "created": 1,
        "model": "upstream-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


@pytest.mark.asyncio
async def test_gateway_reuses_single_tcp_connection_and_applies_per_request_split_timeout(monkeypatch):
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
                    # Peer closed keep-alive connection during teardown.
                    break
                posts += 1
                writer.write(_json_http_response(_openai_payload(f"gw-{posts}")))
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/v1"
    gateway = ModelGatewayService()

    seen_timeouts: list[dict] = []
    try:
        slot_long = _make_slot(base_url, timeout_seconds=120)
        slot_short = _make_slot(base_url, timeout_seconds=6)

        pooled = await gateway._get_pooled_client(slot_long)
        assert pooled._transport._pool._keepalive_expiry == DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
        orig_send = pooled.send

        async def spy_send(request, *args, **kwargs):
            seen_timeouts.append(dict(request.extensions.get("timeout", {})))
            return await orig_send(request, *args, **kwargs)

        monkeypatch.setattr(pooled, "send", spy_send)

        res1 = await gateway.complete(slot_long, _request_body())
        res2 = await gateway.complete(slot_short, _request_body())

        assert res1["choices"][0]["message"]["content"] == "gw-1"
        assert res2["choices"][0]["message"]["content"] == "gw-2"
        assert posts == 2
        assert connections == 1
        assert pooled.is_closed is False
        assert seen_timeouts == [
            {
                "connect": DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
                "read": 120.0,
                "write": DEFAULT_HTTP_WRITE_TIMEOUT_SECONDS,
                "pool": DEFAULT_HTTP_POOL_TIMEOUT_SECONDS,
            },
            {
                "connect": 6.0,
                "read": 6.0,
                "write": 6.0,
                "pool": 6.0,
            },
        ]

        await gateway.aclose()
        assert pooled.is_closed is True
    finally:
        await gateway.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_gateway_pooled_client_retries_dead_reused_socket_once(monkeypatch):
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
            writer.write(_json_http_response(_openai_payload(f"c{conn_id}")))
            await writer.drain()
            if conn_id == 1:
                try:
                    await _read_http_request(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    # Client closed first during teardown.
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
    gateway = ModelGatewayService()

    try:
        slot = _make_slot(base_url, timeout_seconds=30)
        res1 = await gateway.complete(slot, _request_body())
        res2 = await gateway.complete(slot, _request_body())
        assert res1["choices"][0]["message"]["content"] == "c1"
        assert res2["choices"][0]["message"]["content"] == "c2"
        assert connections == 2
        assert posts == 2
    finally:
        await gateway.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_gateway_pool_isolates_credentials_protocols_and_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    gateway = ModelGatewayService()
    try:
        c1 = await gateway._get_pooled_client(_make_slot("https://gw.example.test/v1", api_key="k1"))
        c2 = await gateway._get_pooled_client(_make_slot("https://gw.example.test/v1", api_key="k2"))
        c3 = await gateway._get_pooled_client(
            _make_slot("https://gw.example.test/v1", protocol="anthropic_messages", api_key="k1")
        )
        assert c1 is not c2
        assert c1 is not c3

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
        c4 = await gateway._get_pooled_client(_make_slot("https://gw.example.test/v1", api_key="k1"))
        assert c4 is not c1
    finally:
        await gateway.aclose()


@pytest.mark.asyncio
async def test_gateway_aclose_runs_concurrently_and_is_wired_to_close_model_executor(monkeypatch):
    from plugin.server.routes import model_gateway as routes

    loop = asyncio.get_running_loop()
    evt1 = asyncio.Event()
    evt2 = asyncio.Event()
    closed: list[str] = []

    class _BarrierClient:
        def __init__(self, name: str, own: asyncio.Event, peer: asyncio.Event):
            self.name = name
            self.own = own
            self.peer = peer

        async def aclose(self) -> None:
            self.own.set()
            await asyncio.wait_for(self.peer.wait(), timeout=1.0)
            closed.append(self.name)

    gateway = ModelGatewayService()
    gateway._pools[loop] = OrderedDict([
        (("a",), _BarrierClient("a", evt1, evt2)),
        (("b",), _BarrierClient("b", evt2, evt1)),
    ])
    monkeypatch.setattr(routes, "gateway_service", gateway)

    app = SimpleNamespace(state=SimpleNamespace(model_executor=None))
    await asyncio.wait_for(routes.close_model_executor(app), timeout=1.5)
    assert set(closed) == {"a", "b"}


def test_model_gateway_service_keeps_pool_utils_import_lazy():
    import ast
    from pathlib import Path

    source = Path("plugin/server/application/model_gateway_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.add(node.module)
    assert "utils.http.pool_utils" not in top_level_imports
    assert "httpx" not in top_level_imports
