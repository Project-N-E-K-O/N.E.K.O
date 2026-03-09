"""Neko adapter plugin implementation for SDK v2."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.base import NekoPluginBase
from plugin.sdk_v2.shared.models import Ok, Result

from .base import AdapterConfig, AdapterContext, AdapterMode
from .types import RouteRule


class NekoAdapterPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._adapter_config = AdapterConfig()
        self._adapter_context = AdapterContext(adapter_id=str(getattr(ctx, "plugin_id", "adapter")), config=self._adapter_config, logger=self.logger, plugin_ctx=ctx)
        self._adapter_routes: list[RouteRule] = []

    @property
    def adapter_config(self) -> AdapterConfig:
        return self._adapter_config

    @property
    def adapter_context(self) -> AdapterContext:
        return self._adapter_context

    @property
    def adapter_mode(self) -> AdapterMode:
        return self._adapter_config.mode

    @property
    def adapter_id(self) -> str:
        return self._adapter_context.adapter_id

    async def adapter_startup(self) -> Result[None, Exception]:
        return Ok(None)

    async def adapter_shutdown(self) -> Result[None, Exception]:
        return Ok(None)

    async def register_adapter_tool_as_entry(self, name: str, handler: object, display_name: str = "", description: str = "") -> Result[bool, Exception]:
        if not isinstance(name, str) or name.strip() == "":
            from plugin.sdk_v2.shared.models import Err
            return Err(ValueError("name must be non-empty"))
        if hasattr(self, "register_dynamic_entry"):
            await self.register_dynamic_entry(name, handler, name=display_name or name, description=description)
        return Ok(True)

    async def unregister_adapter_tool_entry(self, name: str) -> Result[bool, Exception]:
        if hasattr(self, "unregister_dynamic_entry"):
            removed = await self.unregister_dynamic_entry(name)
            return Ok(bool(removed))
        return Ok(False)

    def list_adapter_routes(self) -> list[RouteRule]:
        return list(self._adapter_routes)


__all__ = ["NekoAdapterPlugin"]
