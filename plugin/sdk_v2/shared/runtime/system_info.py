"""Shared facade for system-info runtime."""

from plugin.sdk_v2.public.runtime.system_info import SystemInfo as _ImplSystemInfo
from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result


class SystemInfo:
    """Async-first system-info facade."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._impl = _ImplSystemInfo(_ctx)

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self._impl.get_system_config(timeout=timeout)

    async def get_python_env(self) -> Result[JsonObject, Exception]:
        return await self._impl.get_python_env()


__all__ = ["SystemInfo"]
