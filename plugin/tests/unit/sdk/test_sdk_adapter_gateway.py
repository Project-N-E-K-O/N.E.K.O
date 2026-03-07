from __future__ import annotations

from dataclasses import dataclass

import pytest

from plugin.sdk.adapter.gateway_core import AdapterGatewayCore
from plugin.sdk.adapter.gateway_defaults import (
    CallablePluginInvoker,
    DefaultPolicyEngine,
    DefaultRequestNormalizer,
    DefaultResponseSerializer,
    DefaultRouteEngine,
    _to_gateway_action,
)
from plugin.sdk.adapter.gateway_models import (
    ExternalEnvelope,
    GatewayAction,
    GatewayError,
    GatewayErrorException,
    GatewayRequest,
    RouteDecision,
    RouteMode,
)


@dataclass
class _Transport:
    protocol_name: str = "mcp"
    sent: object | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def recv(self):
        return ExternalEnvelope(
            protocol="mcp",
            connection_id="c1",
            request_id="r1",
            action="tool_call",
            payload={"target_plugin_id": "p1", "target_entry_id": "e1", "params": {"x": 1}},
        )

    async def send(self, response):
        self.sent = response


class _Policy:
    async def authorize(self, request: GatewayRequest) -> None:
        return None


class _Router:
    async def decide(self, request: GatewayRequest) -> RouteDecision:
        return RouteDecision(mode=RouteMode.PLUGIN, plugin_id="p1", entry_id="e1")


class _Invoker:
    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> object:
        return {"ok": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_gateway_defaults_behaviors() -> None:
    assert _to_gateway_action("tool_call") == GatewayAction.TOOL_CALL
    with pytest.raises(GatewayErrorException):
        _to_gateway_action("bad")

    normalizer = DefaultRequestNormalizer()
    env = ExternalEnvelope(
        protocol="mcp",
        connection_id="c1",
        request_id="r1",
        action="tool_call",
        payload={"target_plugin_id": "p", "target_entry_id": "run", "params": {"q": 1}},
    )
    req = await normalizer.normalize(env)
    assert req.target_entry_id == "run"

    policy = DefaultPolicyEngine(allowed_plugin_ids={"p"}, max_params_bytes=1024)
    await policy.authorize(req)

    with pytest.raises(GatewayErrorException):
        await DefaultPolicyEngine(allowed_plugin_ids={"x"}).authorize(req)

    route = await DefaultRouteEngine().decide(req)
    assert route.mode == RouteMode.PLUGIN

    serializer = DefaultResponseSerializer()
    ok_res = await serializer.ok(request=req, result={"v": 1}, latency_ms=1.0)
    assert ok_res.success is True
    fail_res = await serializer.fail(
        request=req,
        error=GatewayError(code="E", message="e"),
        latency_ms=2.0,
    )
    assert fail_res.success is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_callable_plugin_invoker_modes() -> None:
    invoker = CallablePluginInvoker(invoke_fn=lambda request, decision: {"ok": True})
    req = GatewayRequest(
        request_id="r1",
        protocol="mcp",
        action=GatewayAction.TOOL_CALL,
        source_app="src",
        trace_id="t1",
        params={},
        target_plugin_id="p1",
        target_entry_id="e1",
    )
    out = await invoker.invoke(req, RouteDecision(mode=RouteMode.PLUGIN, plugin_id="p1", entry_id="e1"))
    assert out == {"ok": True}

    with pytest.raises(GatewayErrorException):
        await invoker.invoke(req, RouteDecision(mode=RouteMode.DROP, reason="none"))


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_adapter_gateway_core_success_and_error_paths() -> None:
    transport = _Transport()
    core = AdapterGatewayCore(
        transport=transport,
        normalizer=DefaultRequestNormalizer(),
        policy=_Policy(),
        router=_Router(),
        invoker=_Invoker(),
        serializer=DefaultResponseSerializer(),
    )

    with pytest.raises(RuntimeError):
        await core.run_once()

    await core.start()
    await core.run_once()
    assert transport.sent is not None
    assert getattr(transport.sent, "success") is True
    await core.stop()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_adapter_gateway_core_gateway_error_exception_path() -> None:
    class _ErrorPolicy:
        async def authorize(self, request: GatewayRequest) -> None:
            raise GatewayErrorException(GatewayError(code="FORBIDDEN", message="no"))

    transport = _Transport()
    core = AdapterGatewayCore(
        transport=transport,
        normalizer=DefaultRequestNormalizer(),
        policy=_ErrorPolicy(),
        router=_Router(),
        invoker=_Invoker(),
        serializer=DefaultResponseSerializer(),
    )
    await core.start()
    await core.run_once()
    assert getattr(transport.sent, "success") is False
    assert getattr(transport.sent, "error").code == "FORBIDDEN"
    await core.stop()
