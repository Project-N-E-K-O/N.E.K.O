from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import aiohttp

from plugin.plugins.mcp_adapter import MCPAdapterPlugin, MCPClient, MCPServerConfig
from plugin.plugins.mcp_adapter.invoker import MCPPluginInvoker
from plugin.plugins.mcp_adapter.normalizer import MCPRequestNormalizer
from plugin.plugins.mcp_adapter.router import MCPRouteEngine
from plugin.plugins.mcp_adapter.serializer import MCPResponseSerializer
from plugin.sdk.adapter import Err, GatewayAction, GatewayError, GatewayRequest, Ok, RouteDecision, RouteMode
from plugin.sdk.adapter.gateway_models import ExternalRequest


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "mcp_adapter"
    metadata = {}
    bus = None

    def __init__(self) -> None:
        self.logger = _Logger()
        self.config_path = Path(tempfile.mkdtemp()) / "plugin.toml"
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }

    async def trigger_plugin_event(self, **kwargs):
        return {"ok": True, "kwargs": kwargs}

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._effective_config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        merged = dict(self._effective_config)
        if "mcp_servers" in updates and isinstance(updates["mcp_servers"], dict):
            current_servers = dict(merged.get("mcp_servers", {}))
            for name, value in updates["mcp_servers"].items():
                if value == "__DELETE__":
                    current_servers.pop(name, None)
                else:
                    current_servers[name] = value
            merged["mcp_servers"] = current_servers
        self._effective_config = merged
        return {"config": merged}


class _AsyncLineStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _Response:
    def __init__(
        self,
        *,
        status: int,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        lines: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.content = _AsyncLineStream(lines or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    def release(self) -> None:
        return None


@pytest.mark.asyncio
async def test_mcp_normalizer_returns_result_and_preserves_plugin_target() -> None:
    normalizer = MCPRequestNormalizer()
    normalized = await normalizer.normalize(
        ExternalRequest(
            protocol="mcp",
            connection_id="conn",
            request_id="req-1",
            action="tool_call",
            payload={
                "name": "demo_tool",
                "arguments": {"a": 1},
                "target_plugin_id": "demo.plugin",
                "timeout_s": 12,
            },
        )
    )
    assert isinstance(normalized, Ok)
    assert normalized.value.target_entry_id == "demo_tool"
    assert normalized.value.target_plugin_id == "demo.plugin"
    assert normalized.value.timeout_s == 12.0


@pytest.mark.asyncio
async def test_mcp_router_returns_result() -> None:
    logger = _Logger()
    engine = MCPRouteEngine(mcp_clients={}, logger=logger)
    decision = await engine.decide(
        GatewayRequest(
            request_id="r",
            protocol="mcp",
            action=GatewayAction.TOOL_CALL,
            source_app="src",
            trace_id="t",
            params={},
            target_plugin_id="plugin.x",
            target_entry_id="entry.y",
        )
    )
    assert isinstance(decision, Ok)
    assert decision.value.mode is RouteMode.PLUGIN


@pytest.mark.asyncio
async def test_mcp_router_register_server_tools_skips_tool_when_callback_rejects() -> None:
    logger = _Logger()

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name
            self.description = ""
            self.input_schema = {}

    client = type("Client", (), {"tools": [_Tool("demo_tool")]})()
    engine = MCPRouteEngine(
        mcp_clients={},
        logger=logger,
        on_tool_register=lambda *args, **kwargs: _reject_register(),
    )

    async def _reject_register() -> bool:
        return False

    count = await engine.register_server_tools("srv", client)

    assert count == 0
    assert engine.get_tool_server("mcp_srv_demo_tool") is None


@pytest.mark.asyncio
async def test_mcp_router_unregister_server_tools_keeps_index_when_callback_rejects() -> None:
    logger = _Logger()

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name
            self.description = ""
            self.input_schema = {}

    client = type("Client", (), {"tools": [_Tool("demo_tool")]})()

    async def _accept_register(*args, **kwargs) -> bool:
        return True

    async def _reject_unregister(*args, **kwargs) -> bool:
        return False

    engine = MCPRouteEngine(
        mcp_clients={},
        logger=logger,
        on_tool_register=_accept_register,
        on_tool_unregister=_reject_unregister,
    )

    count = await engine.register_server_tools("srv", client)
    assert count == 1

    removed = await engine.unregister_server_tools("srv")

    assert removed == 0
    assert engine.get_tool_server("mcp_srv_demo_tool") == "srv"


@pytest.mark.asyncio
async def test_mcp_invoker_returns_err_for_drop() -> None:
    invoker = MCPPluginInvoker(mcp_clients={}, plugin_call_fn=None, logger=_Logger())
    result = await invoker.invoke(
        GatewayRequest(
            request_id="r",
            protocol="mcp",
            action=GatewayAction.TOOL_CALL,
            source_app="src",
            trace_id="t",
            params={},
        ),
        RouteDecision(mode=RouteMode.DROP, reason="missing"),
    )
    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_mcp_serializer_implements_gateway_contract() -> None:
    serializer = MCPResponseSerializer()
    request = GatewayRequest(
        request_id="r",
        protocol="mcp",
        action=GatewayAction.TOOL_CALL,
        source_app="src",
        trace_id="t",
        params={},
    )
    success = await serializer.build_success_response(request, {"ok": True}, 1.0)
    failure = await serializer.build_error_response(request, GatewayError(code="E", message="boom"), 2.0)
    assert isinstance(success, Ok)
    assert isinstance(failure, Ok)
    assert success.value.success is True
    assert failure.value.success is False


@pytest.mark.asyncio
async def test_mcp_gateway_invoke_uses_handle_request() -> None:
    plugin = MCPAdapterPlugin(_Ctx())

    class _Gateway:
        async def handle_request(self, incoming):
            assert incoming.payload["name"] == "demo_tool"
            return Ok(type("Resp", (), {"request_id": "r1", "success": True, "data": {"x": 1}, "latency_ms": 3.0, "error": None})())

    plugin._gateway_core = _Gateway()
    result = await plugin.gateway_invoke(tool_name="demo_tool", arguments={"x": 1})
    assert isinstance(result, Ok)
    assert result.value["result"] == {"x": 1}


@pytest.mark.asyncio
async def test_mcp_http_response_reader_supports_sse_payload() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )

    class _Response:
        headers = {"Content-Type": "text/event-stream"}
        content = _AsyncLineStream([
            b"event: message\n",
            b"data: {\"jsonrpc\":\"2.0\",\"id\":9,\"result\":{\"ok\":true}}\n",
            b"\n",
        ])

    payload = await client._read_http_response_payload(_Response(), expected_id=9)

    assert payload == {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}


@pytest.mark.asyncio
async def test_mcp_tool_register_uses_extended_dynamic_entry_timeout() -> None:
    plugin = MCPAdapterPlugin(_Ctx())
    plugin._tool_timeout = 60.0
    plugin._clients["fetch"] = type("Client", (), {})()

    captured: dict[str, object] = {}

    def _register_dynamic_entry(**kwargs):
        captured.update(kwargs)
        return True

    plugin.register_dynamic_entry = _register_dynamic_entry  # type: ignore[method-assign]

    ok = await plugin._on_tool_register(
        "mcp_fetch_fetch",
        "[fetch] fetch",
        "Fetch URL",
        {"type": "object"},
    )

    assert ok is True
    assert captured["timeout"] == 65.0


@pytest.mark.asyncio
async def test_mcp_http_call_tool_passes_timeout_to_http_request() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )
    client.connected = True

    observed: dict[str, object] = {}

    async def _send_request(method: str, params, *, timeout: float = 60.0):
        observed["method"] = method
        observed["params"] = params
        observed["timeout"] = timeout
        return {"result": {"ok": True}}

    client._send_request = _send_request  # type: ignore[method-assign]

    result = await client.call_tool("demo_tool", {"q": 1}, timeout=12.5)

    assert result == {"result": {"ok": True}}
    assert observed == {
        "method": "tools/call",
        "params": {"name": "demo_tool", "arguments": {"q": 1}},
        "timeout": 12.5,
    }


@pytest.mark.asyncio
async def test_mcp_call_tool_treats_is_error_payload_as_error() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )
    client.connected = True

    async def _send_request(method: str, params, *, timeout: float = 60.0):
        return {
            "result": {
                "content": [
                    {"type": "text", "text": "Input validation error: '' should be non-empty"}
                ],
                "isError": True,
            }
        }

    client._send_request = _send_request  # type: ignore[method-assign]

    result = await client.call_tool("demo_tool", {"q": ""}, timeout=12.5)

    assert result["error"] == "Input validation error: '' should be non-empty"
    assert result["result"]["isError"] is True


@pytest.mark.asyncio
async def test_mcp_call_tool_treats_error_tag_in_success_payload_as_error() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="demo",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )
    client.connected = True

    async def _send_request(method: str, params, *, timeout: float = 60.0):
        return {
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Contents of https://example.com:\n<error>Page failed to be simplified from HTML</error>",
                    }
                ],
                "isError": False,
            }
        }

    client._send_request = _send_request  # type: ignore[method-assign]

    result = await client.call_tool("demo_tool", {"url": "https://example.com"}, timeout=12.5)

    assert result["error"] == "Page failed to be simplified from HTML"
    assert result["result"]["isError"] is False


@pytest.mark.asyncio
async def test_mcp_call_tool_recovers_html_via_generic_raw_mode_support() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="demo",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )
    client.connected = True
    client.tools = [
        type(
            "Tool",
            (),
            {
                "name": "demo_tool",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "raw": {"type": "boolean"},
                    },
                },
                "server_name": "demo",
            },
        )()
    ]

    seen_arguments: list[dict[str, object]] = []

    async def _send_request(method: str, params, *, timeout: float = 60.0):
        seen_arguments.append(dict(params["arguments"]))
        if len(seen_arguments) == 1:
            return {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Contents of https://example.com:\n<error>Page failed to be simplified from HTML</error>",
                        }
                    ],
                    "isError": False,
                }
            }
        return {
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Content type text/html cannot be simplified to markdown, but here is the raw content:\nContents of https://example.com:\n\n<!DOCTYPE html><html><body><main><h1>Example</h1><p>Hello world</p></main></body></html>",
                    }
                ],
                "isError": False,
            }
        }

    client._send_request = _send_request  # type: ignore[method-assign]

    result = await client.call_tool("demo_tool", {"url": "https://example.com"}, timeout=12.5)

    assert result["result"]["isError"] is False
    rendered = result["result"]["content"][0]["text"]
    assert "Contents of https://example.com:" in rendered
    assert "# Example" in rendered
    assert "Hello world" in rendered
    assert seen_arguments == [
        {"url": "https://example.com"},
        {"url": "https://example.com", "raw": True},
    ]


@pytest.mark.asyncio
async def test_mcp_connect_http_rejects_tools_list_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="streamable-http",
            url="https://example.com/mcp",
        ),
        logger=_Logger(),
    )

    class _Session:
        def __init__(self, *args, **kwargs):
            self._responses = [
                _Response(status=200, payload={"result": {}}, headers={"mcp-session-id": "sid"}),
                _Response(status=200, payload={}),
                _Response(status=200, payload={"error": {"message": "tools unavailable"}}),
            ]

        def post(self, *args, **kwargs):
            return self._responses.pop(0)

        async def close(self):
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)

    ok = await client.connect(timeout=5.0)

    assert ok is False
    assert client.connected is False
    assert client.tools == []


@pytest.mark.asyncio
async def test_mcp_connect_sse_uses_endpoint_event_and_loads_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="sse",
            url="https://example.com/sse",
        ),
        logger=_Logger(),
    )

    class _Session:
        def __init__(self, *args, **kwargs):
            self.posts: list[str] = []

        async def get(self, *args, **kwargs):
            return _Response(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                lines=[
                    b"event: endpoint\n",
                    b"data: /messages\n",
                    b"\n",
                ],
            )

        def post(self, url, *args, **kwargs):
            self.posts.append(url)
            payload = kwargs["json"]
            if payload.get("method") == "initialize":
                return _Response(status=200, payload={"jsonrpc": "2.0", "id": payload["id"], "result": {}})
            if payload.get("method") == "notifications/initialized":
                return _Response(status=202, payload={})
            return _Response(
                status=200,
                payload={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "fetch",
                                "description": "Fetch URL",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                },
            )

        async def close(self):
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)

    ok = await client.connect(timeout=5.0)

    assert ok is True
    assert client.connected is True
    assert client._sse_message_url == "https://example.com/messages"
    assert [tool.name for tool in client.tools] == ["fetch"]

    await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_send_sse_request_reads_direct_sse_post_response() -> None:
    client = MCPClient(
        MCPServerConfig(
            name="fetch",
            transport="sse",
            url="https://example.com/sse",
        ),
        logger=_Logger(),
    )

    class _Session:
        def post(self, *args, **kwargs):
            request_id = kwargs["json"]["id"]
            return _Response(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                lines=[
                    b"event: message\n",
                    f"data: {{\"jsonrpc\":\"2.0\",\"id\":{request_id},\"result\":{{\"ok\":true}}}}\n".encode(),
                    b"\n",
                ],
            )

    client._http_session = _Session()
    client._sse_message_url = "https://example.com/messages"

    payload = await client._send_sse_request("tools/list", {}, timeout=5.0)

    assert payload == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert client._pending_requests == {}


@pytest.mark.asyncio
async def test_mcp_add_server_persists_via_runtime_config_update() -> None:
    plugin = MCPAdapterPlugin(_Ctx())

    saved: dict[str, object] = {}

    async def _update_own_config(updates, timeout: float = 10.0):
        saved["updates"] = updates
        return {"config": {"mcp_servers": {"fetch": updates["mcp_servers"]["fetch"]}}}

    plugin.ctx.update_own_config = _update_own_config  # type: ignore[method-assign]

    result = await plugin.add_server(
        name="fetch",
        transport="streamable-http",
        url="https://example.com/mcp",
        auto_connect=False,
    )

    assert isinstance(result, Ok)
    assert saved["updates"] == {
        "mcp_servers": {
            "fetch": {
                "transport": "streamable-http",
                "enabled": True,
                "url": "https://example.com/mcp",
            }
        }
    }


@pytest.mark.asyncio
async def test_mcp_add_server_is_idempotent_for_identical_config() -> None:
    plugin = MCPAdapterPlugin(_Ctx())

    async def _dump(*, timeout: float = 5.0):
        return {
            "mcp_servers": {
                "fetch": {
                    "transport": "streamable-http",
                    "enabled": True,
                    "url": "https://example.com/mcp",
                }
            }
        }

    plugin.config.dump = _dump  # type: ignore[method-assign]

    result = await plugin.add_server(
        name="fetch",
        transport="streamable-http",
        url="https://example.com/mcp",
        auto_connect=False,
    )

    assert isinstance(result, Ok)
    assert result.value["already_exists"] is True
    assert result.value["connected"] is False


@pytest.mark.asyncio
async def test_mcp_add_server_rejects_duplicate_name_with_different_config() -> None:
    plugin = MCPAdapterPlugin(_Ctx())

    async def _dump(*, timeout: float = 5.0):
        return {
            "mcp_servers": {
                "fetch": {
                    "transport": "streamable-http",
                    "enabled": True,
                    "url": "https://example.com/old",
                }
            }
        }

    plugin.config.dump = _dump  # type: ignore[method-assign]

    result = await plugin.add_server(
        name="fetch",
        transport="streamable-http",
        url="https://example.com/new",
        auto_connect=False,
    )

    assert isinstance(result, Err)
    assert "different config" in str(result.error)


@pytest.mark.asyncio
async def test_mcp_remove_servers_persists_delete_marker_patch() -> None:
    plugin = MCPAdapterPlugin(_Ctx())
    plugin._servers_config = {
        "fetch": {"transport": "streamable-http", "url": "https://example.com"}
    }

    saved: dict[str, object] = {}

    async def _dump(*, timeout: float = 5.0):
        return {"mcp_servers": {"fetch": {"transport": "streamable-http", "url": "https://example.com"}}}

    plugin.config.dump = _dump  # type: ignore[method-assign]

    async def _update_own_config(updates, timeout: float = 10.0):
        saved["updates"] = updates
        return {"config": {"mcp_servers": {}}}

    plugin.ctx.update_own_config = _update_own_config  # type: ignore[method-assign]

    result = await plugin.remove_servers(["fetch"])

    assert isinstance(result, Ok)
    assert saved["updates"] == {"mcp_servers": {"fetch": "__DELETE__"}}


@pytest.mark.asyncio
async def test_mcp_reconnect_server_cleans_task_slot_on_early_return() -> None:
    plugin = MCPAdapterPlugin(_Ctx())
    plugin._reconnect_tasks["missing"] = asyncio.current_task()

    await plugin._reconnect_server("missing")

    assert "missing" not in plugin._reconnect_tasks


@pytest.mark.asyncio
async def test_mcp_disconnect_server_cancels_pending_reconnect_task() -> None:
    plugin = MCPAdapterPlugin(_Ctx())

    async def _sleep_forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_sleep_forever())
    plugin._reconnect_tasks["fetch"] = task
    plugin._clients["fetch"] = type("Client", (), {"disconnect": lambda self: asyncio.sleep(0)})()

    async def _unregister_mcp_tools(server_name: str) -> None:
        return None

    plugin._unregister_mcp_tools = _unregister_mcp_tools  # type: ignore[method-assign]

    result = await plugin.disconnect_server("fetch")

    assert isinstance(result, Ok)
    assert "fetch" not in plugin._reconnect_tasks
    await asyncio.sleep(0)
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_mcp_remove_servers_cancels_pending_reconnect_task() -> None:
    plugin = MCPAdapterPlugin(_Ctx())
    plugin._servers_config = {
        "fetch": {"transport": "streamable-http", "url": "https://example.com"}
    }

    async def _sleep_forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_sleep_forever())
    plugin._reconnect_tasks["fetch"] = task

    async def _dump(*, timeout: float = 5.0):
        return {"mcp_servers": {"fetch": {"transport": "streamable-http", "url": "https://example.com"}}}

    async def _update_own_config(updates, timeout: float = 10.0):
        return {"config": {"mcp_servers": {}}}

    plugin.config.dump = _dump  # type: ignore[method-assign]
    plugin.ctx.update_own_config = _update_own_config  # type: ignore[method-assign]

    result = await plugin.remove_servers(["fetch"])

    assert isinstance(result, Ok)
    assert "fetch" not in plugin._reconnect_tasks
    await asyncio.sleep(0)
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_mcp_connect_server_coerces_string_flags_and_rolls_back_on_tool_register_failure() -> None:
    plugin = MCPAdapterPlugin(_Ctx())
    plugin._route_engine = object()

    observed: dict[str, object] = {}

    class _Client:
        def __init__(self, config, logger=None):
            observed["enabled"] = config.enabled
            self.config = config
            self.tools = []

        def set_disconnect_callback(self, callback):
            self._callback = callback

        async def connect(self, timeout: float = 30.0) -> bool:
            observed["timeout"] = timeout
            return True

        async def disconnect(self) -> None:
            observed["disconnected"] = True

    async def _register_mcp_tools(server_name: str, client) -> None:
        raise RuntimeError("register failed")

    plugin._register_mcp_tools = _register_mcp_tools  # type: ignore[method-assign]

    from plugin.plugins import mcp_adapter as module

    original_client_cls = module.MCPClient
    try:
        module.MCPClient = _Client
        ok = await plugin._connect_server(
            "fetch",
            {
                "transport": "streamable-http",
                "url": "https://example.com/mcp",
                "enabled": "false",
            },
            timeout="12.5",  # type: ignore[arg-type]
        )
    finally:
        module.MCPClient = original_client_cls

    assert ok is False
    assert observed["enabled"] is False
    assert observed["timeout"] == 12.5
    assert observed["disconnected"] is True
    assert "fetch" not in plugin._clients
