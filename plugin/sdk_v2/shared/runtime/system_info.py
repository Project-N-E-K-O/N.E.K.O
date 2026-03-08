"""System info contracts for SDK v2 shared runtime."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result


class SystemInfo:
    """Async-only system info contract."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.system_info not implemented")

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        raise NotImplementedError

    async def get_python_env(self) -> Result[JsonObject, Exception]:
        raise NotImplementedError


__all__ = ["SystemInfo"]
