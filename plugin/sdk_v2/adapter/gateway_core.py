"""Adapter-facing gateway core facade for SDK v2."""

from __future__ import annotations

from time import perf_counter
from typing import cast

from plugin.sdk_v2.shared.models import Err, Result
from plugin.sdk_v2.shared.models.exceptions import GatewayErrorLike, SdkError, TransportError

from .gateway_contracts import PluginInvoker, PolicyEngine, RequestNormalizer, ResponseSerializer, RouteEngine, TransportAdapter
from .gateway_models import ExternalRequest, GatewayError, GatewayResponse

GatewayRuntimeError = GatewayErrorLike


class AdapterGatewayCore:
    """Stable adapter-facing gateway orchestrator."""

    def __init__(self, transport: TransportAdapter, normalizer: RequestNormalizer, policy: PolicyEngine, router: RouteEngine, invoker: PluginInvoker, serializer: ResponseSerializer) -> None:
        self.transport = transport
        self.normalizer = normalizer
        self.policy = policy
        self.router = router
        self.invoker = invoker
        self.serializer = serializer

    async def start(self) -> Result[None, GatewayErrorLike]:
        return cast(Result[None, GatewayErrorLike], await self.transport.start())

    async def stop(self) -> Result[None, GatewayErrorLike]:
        return cast(Result[None, GatewayErrorLike], await self.transport.stop())

    async def run_once(self) -> Result[GatewayResponse, GatewayErrorLike]:
        try:
            incoming = await self.transport.recv()
        except Exception as error:
            return Err(TransportError(str(error), op_name="gateway.run_once.recv"))
        if isinstance(incoming, Err):
            return cast(Result[GatewayResponse, GatewayErrorLike], incoming)
        return await self.handle_request(incoming.value)

    async def _build_error_response(
        self,
        request,
        *,
        code: str,
        message: str,
        latency_ms: float,
    ) -> Result[GatewayResponse, GatewayErrorLike]:
        try:
            return cast(
                Result[GatewayResponse, GatewayErrorLike],
                await self.serializer.build_error_response(
                    request,
                    GatewayError(code=code, message=message),
                    latency_ms,
                ),
            )
        except Exception as error:
            return Err(TransportError(str(error), op_name="gateway.handle_request.serialize_error"))

    async def handle_request(self, incoming: ExternalRequest) -> Result[GatewayResponse, GatewayErrorLike]:
        started = perf_counter()
        try:
            normalized = await self.normalizer.normalize(incoming)
        except Exception as error:
            return Err(TransportError(str(error), op_name="gateway.handle_request.normalize"))
        if isinstance(normalized, Err):
            return Err(normalized.error if isinstance(normalized.error, SdkError) else TransportError(str(normalized.error), op_name="gateway.handle_request.normalize"))
        request = normalized.value
        latency_ms = lambda: (perf_counter() - started) * 1000.0
        try:
            authorized = await self.policy.authorize(request)
            if isinstance(authorized, Err):
                return await self._build_error_response(
                    request,
                    code="policy_denied",
                    message=str(authorized.error),
                    latency_ms=latency_ms(),
                )

            decision = await self.router.decide(request)
            if isinstance(decision, Err):
                return await self._build_error_response(
                    request,
                    code="route_failed",
                    message=str(decision.error),
                    latency_ms=latency_ms(),
                )

            invoked = await self.invoker.invoke(request, decision.value)
            if isinstance(invoked, Err):
                return await self._build_error_response(
                    request,
                    code="invoke_failed",
                    message=str(invoked.error),
                    latency_ms=latency_ms(),
                )

            return cast(
                Result[GatewayResponse, GatewayErrorLike],
                await self.serializer.build_success_response(request, invoked.value, latency_ms()),
            )
        except Exception as error:
            return await self._build_error_response(
                request,
                code="internal_error",
                message=str(error),
                latency_ms=latency_ms(),
            )


__all__ = ["AdapterGatewayCore"]
