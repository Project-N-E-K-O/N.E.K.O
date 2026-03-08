"""Default gateway component contracts for SDK v2 adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from plugin.sdk_v2.shared.models import Result

from .gateway_models import ExternalEnvelope, GatewayError, GatewayRequest, GatewayResponse, RouteDecision


class DefaultRequestNormalizer:
    async def normalize(self, env: ExternalEnvelope) -> Result[GatewayRequest, Exception]:
        raise NotImplementedError("sdk_v2 contract-only facade: adapter.gateway_defaults not implemented")


@dataclass(slots=True)
class DefaultPolicyEngine:
    allowed_plugin_ids: set[str] | None = None
    max_params_bytes: int = 256 * 1024

    async def authorize(self, request: GatewayRequest) -> Result[None, Exception]:
        raise NotImplementedError


class DefaultRouteEngine:
    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, Exception]:
        raise NotImplementedError


class DefaultResponseSerializer:
    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, Exception]:
        raise NotImplementedError

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, Exception]:
        raise NotImplementedError


@dataclass(slots=True)
class CallablePluginInvoker:
    invoke_fn: Callable[[GatewayRequest, RouteDecision], object]

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, Exception]:
        raise NotImplementedError


__all__ = [
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
]
