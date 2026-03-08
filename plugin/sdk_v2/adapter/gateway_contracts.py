"""Gateway protocols for SDK v2 adapter."""

from __future__ import annotations

from typing import Protocol

from plugin.sdk_v2.shared.core.types import LoggerLike
from plugin.sdk_v2.shared.models import Result

from .gateway_models import ExternalEnvelope, GatewayError, GatewayRequest, GatewayResponse, RouteDecision


class TransportAdapter(Protocol):
    protocol_name: str

    async def start(self) -> Result[None, Exception]: ...

    async def stop(self) -> Result[None, Exception]: ...

    async def recv(self) -> Result[ExternalEnvelope, Exception]: ...

    async def send(self, response: GatewayResponse) -> Result[None, Exception]: ...


class RequestNormalizer(Protocol):
    async def normalize(self, env: ExternalEnvelope) -> Result[GatewayRequest, Exception]: ...


class PolicyEngine(Protocol):
    async def authorize(self, request: GatewayRequest) -> Result[None, Exception]: ...


class RouteEngine(Protocol):
    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, Exception]: ...


class PluginInvoker(Protocol):
    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, Exception]: ...


class ResponseSerializer(Protocol):
    async def ok(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, Exception]: ...

    async def fail(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, Exception]: ...


__all__ = [
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
]
