"""Shared facade for system-info runtime."""

from __future__ import annotations

from plugin.sdk_v2.public.runtime.system_info import SystemInfo as _ImplSystemInfo
from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result


class SystemInfo:
    """Async-first system-info facade."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx
        self._impl = _ImplSystemInfo(_ctx)

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return _OK_NONE

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            return await self._impl.get_system_config(timeout=timeout)
        except Exception as error:
            return Err(error)

    async def get_python_env(self) -> Result[JsonObject, Exception]:
        try:
            return await self._impl.get_python_env()
        except Exception as error:
            return Err(error)


_OK_NONE = Ok(None)

__all__ = ["SystemInfo"]
