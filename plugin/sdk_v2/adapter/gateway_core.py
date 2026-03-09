"""Adapter-facing gateway core facade for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.public.adapter.gateway_core import AdapterGatewayCore as _ImplAdapterGatewayCore
from plugin.sdk_v2.shared.models import Result

from .gateway_contracts import PluginInvoker, PolicyEngine, RequestNormalizer, ResponseSerializer, RouteEngine, TransportAdapter
from .gateway_models import ExternalEnvelope, GatewayResponse


class AdapterGatewayCore(_ImplAdapterGatewayCore):
    """Stable adapter-facing gateway orchestrator facade."""

    def __init__(
        self,
        transport: TransportAdapter,
        normalizer: RequestNormalizer,
        policy: PolicyEngine,
        router: RouteEngine,
        invoker: PluginInvoker,
        serializer: ResponseSerializer,
    ) -> None:
        super().__init__(transport=transport, normalizer=normalizer, policy=policy, router=router, invoker=invoker, serializer=serializer)

    async def start(self) -> Result[None, Exception]:
        return await super().start()

    async def stop(self) -> Result[None, Exception]:
        return await super().stop()

    async def run_once(self) -> Result[GatewayResponse, Exception]:
        return await super().run_once()

    async def handle_envelope(self, envelope: ExternalEnvelope) -> Result[GatewayResponse, Exception]:
        return await super().handle_envelope(envelope)


__all__ = ["AdapterGatewayCore"]
