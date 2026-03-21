"""Default gateway components for SDK v2 adapter."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, cast

from plugin.sdk.shared.core.types import JsonObject, JsonValue
from plugin.sdk.shared.models import Err, Ok, Result
from plugin.sdk.shared.models.exceptions import AuthorizationError, CapabilityUnavailableError, GatewayErrorLike, InvalidArgumentError, PluginCallError, TransportError

from .gateway_models import ExternalRequest, GatewayAction, GatewayError, GatewayRequest, GatewayResponse, RouteDecision, RouteMode

GatewayComponentError = AuthorizationError | TransportError


def _is_json_serializable(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return False
    return True


def _coerce_json_response_data(value: object) -> JsonValue | JsonObject:
    if _is_json_serializable(value):
        return cast(JsonValue | JsonObject, value)
    return {"result": str(value)}


class DefaultRequestNormalizer:
    """Normalize an incoming external request into `GatewayRequest`."""

    async def normalize(self, incoming: ExternalRequest) -> Result[GatewayRequest, TransportError]:
        try:
            try:
                action = GatewayAction(incoming.action)
            except ValueError:
                return Err(TransportError(f"unsupported gateway action: {incoming.action}", op_name="gateway.request_normalizer.normalize"))
            return Ok(GatewayRequest(request_id=incoming.request_id, protocol=incoming.protocol, action=action, source_app=incoming.connection_id, trace_id=incoming.request_id, params=dict(incoming.payload), metadata=dict(incoming.metadata)))
        except Exception as error:
            return Err(error if isinstance(error, TransportError) else TransportError(str(error), op_name="gateway.request_normalizer.normalize"))


@dataclass(slots=True)
class DefaultPolicyEngine:
    """Authorize a normalized request before routing and invocation."""

    allowed_plugin_ids: set[str] | None = None
    max_params_bytes: int = 256 * 1024

    async def authorize(self, request: GatewayRequest) -> Result[None, GatewayComponentError]:
        try:
            params_bytes = len(
                json.dumps(
                    request.params,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except Exception as error:
            return Err(AuthorizationError(f"request params are not serializable: {error}"))
        if params_bytes > self.max_params_bytes:
            return Err(AuthorizationError("request params exceed max_params_bytes"))
        if self.allowed_plugin_ids is not None and request.target_plugin_id and request.target_plugin_id not in self.allowed_plugin_ids:
            return Err(AuthorizationError("target plugin is not allowed"))
        return Ok(None)


class DefaultRouteEngine:
    """Choose how an authorized request should be dispatched."""

    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, TransportError]:
        has_plugin = bool(request.target_plugin_id)
        has_entry = bool(request.target_entry_id)
        if has_plugin and has_entry:
            return Ok(RouteDecision(mode=RouteMode.PLUGIN, plugin_id=request.target_plugin_id, entry_id=request.target_entry_id, reason="explicit-target"))
        if has_plugin or has_entry:
            return Err(
                TransportError(
                    "explicit routing requires both target_plugin_id and target_entry_id",
                    op_name="gateway.route_engine.decide",
                    plugin_id=request.target_plugin_id or None,
                    entry_ref=f"{request.target_plugin_id or ''}:{request.target_entry_id or ''}",
                    code="gateway.partial_target",
                )
            )
        return Ok(RouteDecision(mode=RouteMode.SELF, reason="default-self"))


class DefaultResponseSerializer:
    """Build transport-facing success and error responses."""

    async def build_success_response(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, TransportError]:
        return Ok(
            GatewayResponse(
                request_id=request.request_id,
                success=True,
                data=_coerce_json_response_data(result),
                latency_ms=latency_ms,
            )
        )

    async def build_error_response(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, TransportError]:
        return Ok(GatewayResponse(request_id=request.request_id, success=False, error=error, latency_ms=latency_ms))


@dataclass(slots=True)
class CallablePluginInvoker:
    """Invoke plugin-side logic from a plain callable."""

    invoke_fn: Callable[[GatewayRequest, RouteDecision], object | Awaitable[object]]

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, GatewayErrorLike]:
        try:
            result = self.invoke_fn(request, decision)
            if inspect.isawaitable(result):
                result = await result
            return Ok(result)
        except Exception as error:
            if isinstance(error, (AuthorizationError, InvalidArgumentError, CapabilityUnavailableError, PluginCallError, TransportError)):
                return Err(error)
            return Err(
                PluginCallError(
                    str(error),
                    op_name="gateway.plugin_invoker.invoke",
                    plugin_id=decision.plugin_id,
                    entry_ref=f"{decision.plugin_id}:{decision.entry_id}" if decision.plugin_id and decision.entry_id else None,
                )
            )


__all__ = [
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
    "GatewayComponentError",
]
