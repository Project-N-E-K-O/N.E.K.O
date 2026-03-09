"""Adapter-facing default gateway component facades for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.public.adapter.gateway_defaults import (
    CallablePluginInvoker as _ImplCallablePluginInvoker,
    DefaultPolicyEngine as _ImplDefaultPolicyEngine,
    DefaultRequestNormalizer as _ImplDefaultRequestNormalizer,
    DefaultResponseSerializer as _ImplDefaultResponseSerializer,
    DefaultRouteEngine as _ImplDefaultRouteEngine,
)
from plugin.sdk_v2.shared.models import Result

from .gateway_models import ExternalEnvelope, GatewayError, GatewayRequest, GatewayResponse, RouteDecision


class DefaultRequestNormalizer(_ImplDefaultRequestNormalizer):
    """Stable adapter-facing request normalizer facade."""

    async def normalize(self, env: ExternalEnvelope) -> Result[GatewayRequest, Exception]:
        return await super().normalize(env)


class DefaultPolicyEngine(_ImplDefaultPolicyEngine):
    """Stable adapter-facing policy engine facade."""

    async def authorize(self, request: GatewayRequest) -> Result[None, Exception]:
        return await super().authorize(request)


class DefaultRouteEngine(_ImplDefaultRouteEngine):
    """Stable adapter-facing route engine facade."""

    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, Exception]:
        return await super().decide(request)


class DefaultResponseSerializer(_ImplDefaultResponseSerializer):
    """Stable adapter-facing response serializer facade."""

    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, Exception]:
        return await super().ok(request, result, latency_ms)

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, Exception]:
        return await super().fail(request, error, latency_ms)


class CallablePluginInvoker(_ImplCallablePluginInvoker):
    """Stable adapter-facing invoker facade."""

    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, Exception]:
        return await super().invoke(request, decision)


__all__ = [
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
]
