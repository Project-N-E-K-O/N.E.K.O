"""Adapter-facing base facade for SDK v2.

This module keeps the adapter-side import surface explicit and IDE-friendly.
Concrete behavior lives in `public.adapter.base`, while this facade preserves a
stable method surface and reserves room for adapter-specific semantics.
"""

from __future__ import annotations

from plugin.sdk_v2.public.adapter.base import (
    AdapterBase as _ImplAdapterBase,
    AdapterConfig as _ImplAdapterConfig,
    AdapterContext as _ImplAdapterContext,
    AdapterMode,
)
from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result
from .gateway_contracts import LoggerLike


class AdapterConfig(_ImplAdapterConfig):
    """Adapter-facing configuration model."""

    @classmethod
    def from_dict(cls, raw: JsonObject) -> "AdapterConfig":
        base = _ImplAdapterConfig.from_dict(raw)
        return cls(mode=base.mode, protocols=base.protocols, routes=base.routes, priority=base.priority)


class AdapterContext(_ImplAdapterContext):
    """Adapter-facing runtime context facade."""

    def __init__(
        self,
        adapter_id: str,
        config: AdapterConfig,
        logger: LoggerLike,
        plugin_ctx: PluginContextProtocol | None = None,
    ) -> None:
        super().__init__(adapter_id=adapter_id, config=config, logger=logger, plugin_ctx=plugin_ctx)

    def register_event_handler(self, event_type: str, handler: object) -> None:
        super().register_event_handler(event_type, handler)

    def get_event_handlers(self, event_type: str) -> list[object]:
        return super().get_event_handlers(event_type)

    async def call_plugin(
        self,
        plugin_id: str,
        entry: str,
        payload: JsonObject,
        timeout: float = 30.0,
    ) -> Result[JsonObject | None, Exception]:
        return await super().call_plugin(plugin_id, entry, payload, timeout=timeout)

    async def broadcast_event(self, event_type: str, payload: JsonObject) -> Result[list[JsonObject], Exception]:
        return await super().broadcast_event(event_type, payload)


class AdapterBase(_ImplAdapterBase):
    """Adapter-facing base class facade."""

    def __init__(self, config: AdapterConfig, ctx: AdapterContext):
        super().__init__(config=config, ctx=ctx)

    @property
    def adapter_id(self) -> str:
        return super().adapter_id

    @property
    def mode(self) -> AdapterMode:
        return super().mode

    async def on_message(self, message: JsonObject) -> Result[JsonObject | None, Exception]:
        return await super().on_message(message)

    async def on_startup(self) -> Result[None, Exception]:
        return await super().on_startup()

    async def on_shutdown(self) -> Result[None, Exception]:
        return await super().on_shutdown()


__all__ = ["AdapterMode", "AdapterConfig", "AdapterContext", "AdapterBase"]
