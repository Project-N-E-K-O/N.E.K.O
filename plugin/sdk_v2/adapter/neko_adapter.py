"""Adapter-facing Neko adapter facade for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.public.adapter.neko_adapter import NekoAdapterPlugin as _ImplNekoAdapterPlugin
from plugin.sdk_v2.shared.models import Result

from .base import AdapterConfig, AdapterContext, AdapterMode
from .types import RouteRule


class NekoAdapterPlugin(_ImplNekoAdapterPlugin):
    """Stable adapter-capable plugin facade."""

    @property
    def adapter_config(self) -> AdapterConfig:
        return super().adapter_config

    @property
    def adapter_context(self) -> AdapterContext:
        return super().adapter_context

    @property
    def adapter_mode(self) -> AdapterMode:
        return super().adapter_mode

    @property
    def adapter_id(self) -> str:
        return super().adapter_id

    async def adapter_startup(self) -> Result[None, Exception]:
        return await super().adapter_startup()

    async def adapter_shutdown(self) -> Result[None, Exception]:
        return await super().adapter_shutdown()

    async def register_adapter_tool_as_entry(
        self,
        name: str,
        handler: object,
        display_name: str = "",
        description: str = "",
    ) -> Result[bool, Exception]:
        return await super().register_adapter_tool_as_entry(name, handler, display_name=display_name, description=description)

    async def unregister_adapter_tool_entry(self, name: str) -> Result[bool, Exception]:
        return await super().unregister_adapter_tool_entry(name)

    def list_adapter_routes(self) -> list[RouteRule]:
        return super().list_adapter_routes()


__all__ = ["NekoAdapterPlugin"]
