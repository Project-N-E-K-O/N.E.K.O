"""Default gateway components implementation for SDK v2 adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from plugin.sdk_v2.shared.models import Err, Ok, Result

from .gateway_models import ExternalEnvelope, GatewayAction, GatewayError, GatewayRequest, GatewayResponse, RouteDecision, RouteMode


class DefaultRequestNormalizer:
    async def normalize(self, env: ExternalEnvelope) -> Result[GatewayRequest, Exception]:
        try:
            action = GatewayAction(env.action) if env.action in GatewayAction._value2member_map_ else GatewayAction.EVENT_PUSH
            return Ok(GatewayRequest(request_id=env.request_id, protocol=env.protocol, action=action, source_app=env.connection_id, trace_id=env.request_id, params=dict(env.payload), metadata=dict(env.metadata)))
        except Exception as error:
            return Err(error)


@dataclass(slots=True)
class DefaultPolicyEngine:
    allowed_plugin_ids: set[str] | None = None
    max_params_bytes: int = 256 * 1024

    async def authorize(self, request: GatewayRequest) -> Result[None, Exception]:
        if self.allowed_plugin_ids and request.target_plugin_id and request.target_plugin_id not in self.allowed_plugin_ids:
            return Err(RuntimeError("target plugin is not allowed"))
        return Ok(None)


class DefaultRouteEngine:
    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, Exception]:
        if request.target_plugin_id and request.target_entry_id:
            return Ok(RouteDecision(mode=RouteMode.PLUGIN, plugin_id=request.target_plugin_id, entry_id=request.target_entry_id, reason="explicit-target"))
        return Ok(RouteDecision(mode=RouteMode.SELF, reason="default-self"))


class DefaultResponseSerializer:
    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, Exception]:
        return Ok(GatewayResponse(request_id=request.request_id, success=True, data=result if isinstance(result, (dict, list, str, int, float, bool)) or result is None else {"result": str(result)}, latency_ms=latency_ms))

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, Exception]:
        return Ok(GatewayResponse(request_id=request.request_id, success=False, error=error, latency_ms=latency_ms))


@dataclass(slots=True)
class CallablePluginInvoker:
    invoke_fn: Callable[[GatewayRequest, RouteDecision], object]

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, Exception]:
        try:
            result = self.invoke_fn(request, decision)
            if hasattr(result, "__await__"):
                result = await result
            return Ok(result)
        except Exception as error:
            return Err(error)


__all__ = ["DefaultRequestNormalizer", "DefaultPolicyEngine", "DefaultRouteEngine", "DefaultResponseSerializer", "CallablePluginInvoker"]
