"""Adapter gateway core implementation for SDK v2."""

from __future__ import annotations

from time import perf_counter

from plugin.sdk_v2.shared.models import Err, Result

from .gateway_contracts import PluginInvoker, PolicyEngine, RequestNormalizer, ResponseSerializer, RouteEngine, TransportAdapter
from .gateway_models import ExternalEnvelope, GatewayError, GatewayResponse


class AdapterGatewayCore:
    def __init__(self, transport: TransportAdapter, normalizer: RequestNormalizer, policy: PolicyEngine, router: RouteEngine, invoker: PluginInvoker, serializer: ResponseSerializer) -> None:
        self.transport = transport
        self.normalizer = normalizer
        self.policy = policy
        self.router = router
        self.invoker = invoker
        self.serializer = serializer

    async def start(self) -> Result[None, Exception]:
        return await self.transport.start()

    async def stop(self) -> Result[None, Exception]:
        return await self.transport.stop()

    async def run_once(self) -> Result[GatewayResponse, Exception]:
        envelope = await self.transport.recv()
        if isinstance(envelope, Err):
            return envelope
        return await self.handle_envelope(envelope.value)

    async def handle_envelope(self, envelope: ExternalEnvelope) -> Result[GatewayResponse, Exception]:
        started = perf_counter()
        normalized = await self.normalizer.normalize(envelope)
        if isinstance(normalized, Err):
            return Err(normalized.error)
        request = normalized.value
        authorized = await self.policy.authorize(request)
        if isinstance(authorized, Err):
            error = GatewayError(code="policy_denied", message=str(authorized.error))
            return await self.serializer.fail(request, error, (perf_counter() - started) * 1000.0)
        decision = await self.router.decide(request)
        if isinstance(decision, Err):
            error = GatewayError(code="route_failed", message=str(decision.error))
            return await self.serializer.fail(request, error, (perf_counter() - started) * 1000.0)
        invoked = await self.invoker.invoke(request, decision.value)
        if isinstance(invoked, Err):
            error = GatewayError(code="invoke_failed", message=str(invoked.error))
            return await self.serializer.fail(request, error, (perf_counter() - started) * 1000.0)
        return await self.serializer.ok(request, invoked.value, (perf_counter() - started) * 1000.0)


__all__ = ["AdapterGatewayCore"]
