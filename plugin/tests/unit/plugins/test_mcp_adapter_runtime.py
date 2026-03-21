from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from plugin.plugins.mcp_adapter import MCPAdapterPlugin
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
