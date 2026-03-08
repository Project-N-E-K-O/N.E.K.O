"""Adapter gateway core contract for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.shared.models import Result

from .gateway_contracts import PluginInvoker, PolicyEngine, RequestNormalizer, ResponseSerializer, RouteEngine, TransportAdapter
from .gateway_models import ExternalEnvelope, GatewayResponse


class AdapterGatewayCore:
    """Gateway orchestrator contract."""

    def __init__(
        self,
        transport: TransportAdapter,
        normalizer: RequestNormalizer,
        policy: PolicyEngine,
        router: RouteEngine,
        invoker: PluginInvoker,
        serializer: ResponseSerializer,
    ) -> None:
        raise NotImplementedError("sdk_v2 contract-only facade: adapter.gateway_core not implemented")

    async def start(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def stop(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def run_once(self) -> Result[GatewayResponse, Exception]:
        raise NotImplementedError

    async def handle_envelope(self, envelope: ExternalEnvelope) -> Result[GatewayResponse, Exception]:
        raise NotImplementedError


__all__ = ["AdapterGatewayCore"]
