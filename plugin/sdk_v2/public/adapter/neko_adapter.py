"""Neko adapter plugin contract for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.base import NekoPluginBase
from plugin.sdk_v2.shared.models import Result

from .base import AdapterConfig, AdapterContext, AdapterMode
from .types import RouteRule


class NekoAdapterPlugin(NekoPluginBase):
    """Adapter-capable plugin contract."""

    @property
    def adapter_config(self) -> AdapterConfig:
        raise NotImplementedError("sdk_v2 contract-only facade: adapter.neko_adapter not implemented")

    @property
    def adapter_context(self) -> AdapterContext:
        raise NotImplementedError

    @property
    def adapter_mode(self) -> AdapterMode:
        raise NotImplementedError

    @property
    def adapter_id(self) -> str:
        raise NotImplementedError

    async def adapter_startup(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def adapter_shutdown(self) -> Result[None, Exception]:
        raise NotImplementedError

    async def register_adapter_tool_as_entry(
        self,
        name: str,
        handler: object,
        display_name: str = "",
        description: str = "",
    ) -> Result[bool, Exception]:
        raise NotImplementedError

    async def unregister_adapter_tool_entry(self, name: str) -> Result[bool, Exception]:
        raise NotImplementedError

    def list_adapter_routes(self) -> list[RouteRule]:
        raise NotImplementedError


__all__ = ["NekoAdapterPlugin"]
