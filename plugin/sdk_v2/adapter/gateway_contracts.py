"""Gateway protocols for SDK v2 adapter."""

from __future__ import annotations

from typing import Protocol

from plugin.sdk_v2.shared.core.types import LoggerLike
from plugin.sdk_v2.shared.models import Result
from plugin.sdk_v2.shared.models.exceptions import GatewayErrorLike

from .gateway_models import ExternalRequest, GatewayError, GatewayRequest, GatewayResponse, RouteDecision


class TransportAdapter(Protocol):
    protocol_name: str

    async def start(self) -> Result[None, GatewayErrorLike]: ...

    async def stop(self) -> Result[None, GatewayErrorLike]: ...

    async def recv(self) -> Result[ExternalRequest, GatewayErrorLike]: ...

    async def send(self, response: GatewayResponse) -> Result[None, GatewayErrorLike]: ...


class RequestNormalizer(Protocol):
    async def normalize(self, incoming: ExternalRequest) -> Result[GatewayRequest, GatewayErrorLike]: ...


class PolicyEngine(Protocol):
    async def authorize(self, request: GatewayRequest) -> Result[None, GatewayErrorLike]: ...


class RouteEngine(Protocol):
    async def decide(self, request: GatewayRequest) -> Result[RouteDecision, GatewayErrorLike]: ...


class PluginInvoker(Protocol):
    async def invoke(self, request: GatewayRequest, decision: RouteDecision) -> Result[object, GatewayErrorLike]: ...


class ResponseSerializer(Protocol):
    async def build_success_response(self, request: GatewayRequest, result: object, latency_ms: float) -> Result[GatewayResponse, GatewayErrorLike]: ...

    async def build_error_response(self, request: GatewayRequest, error: GatewayError, latency_ms: float) -> Result[GatewayResponse, GatewayErrorLike]: ...


__all__ = [
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
]
