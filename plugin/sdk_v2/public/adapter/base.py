"""Adapter base contracts for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result

from .gateway_contracts import LoggerLike
from .types import RouteRule


class AdapterMode(str, Enum):
    GATEWAY = "gateway"
    ROUTER = "router"
    BRIDGE = "bridge"
    HYBRID = "hybrid"


@dataclass(slots=True)
class AdapterConfig:
    mode: AdapterMode = AdapterMode.HYBRID
    protocols: dict[str, JsonObject] = field(default_factory=dict)
    routes: list[RouteRule] = field(default_factory=list)
    priority: int = 0


class AdapterContext:
    def __init__(
        self,
        adapter_id: str,
        config: AdapterConfig,
        logger: LoggerLike,
        plugin_ctx: PluginContextProtocol | None = None,
    ) -> None:
        raise NotImplementedError("sdk_v2 contract-only facade: adapter.base not implemented")

    async def call_plugin(
        self,
        plugin_id: str,
        entry: str,
        payload: JsonObject,
        timeout: float = 30.0,
    ) -> Result[JsonObject | None, Exception]:
        raise NotImplementedError

    async def broadcast_event(self, event_type: str, payload: JsonObject) -> Result[list[JsonObject], Exception]:
        raise NotImplementedError


class AdapterBase:
    def __init__(self, config: AdapterConfig, ctx: AdapterContext):
        raise NotImplementedError("sdk_v2 contract-only facade: adapter.base not implemented")

    async def on_startup(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def on_shutdown(self) -> Result[None, Exception]:
        raise NotImplementedError


__all__ = ["AdapterMode", "AdapterConfig", "AdapterContext", "AdapterBase"]
