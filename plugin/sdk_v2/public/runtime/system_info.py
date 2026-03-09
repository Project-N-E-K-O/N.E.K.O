"""Internal system-info runtime implementation for SDK v2."""

from __future__ import annotations

import platform
import sys

from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result


class SystemInfo:
    """Async-first system info client."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        try:
            getter = getattr(self._ctx, "get_system_config", None)
            if getter is None:
                return Err(RuntimeError("ctx.get_system_config is not available"))
            result = await getter(timeout=timeout)
            if not isinstance(result, dict):
                return Ok({"result": result})
            return Ok(result)
        except Exception as error:
            return Err(error)

    async def get_server_settings(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        try:
            config = await self.get_system_config(timeout=timeout)
            if isinstance(config, Err):
                return config
            payload = config.value
            if isinstance(payload.get("data"), dict):
                payload = payload["data"]
            settings = payload.get("config") if isinstance(payload, dict) else None
            return Ok(settings if isinstance(settings, dict) else {})
        except Exception as error:
            return Err(error)

    async def get_python_env(self) -> Result[JsonObject, Exception]:
        try:
            try:
                uname = platform.uname()
            except Exception:
                uname = None
            try:
                arch = platform.architecture()
            except Exception:
                arch = None
            return Ok(
                {
                    "python": {
                        "version": sys.version,
                        "version_info": {
                            "major": sys.version_info.major,
                            "minor": sys.version_info.minor,
                            "micro": sys.version_info.micro,
                            "releaselevel": sys.version_info.releaselevel,
                            "serial": sys.version_info.serial,
                        },
                        "implementation": platform.python_implementation(),
                        "executable": sys.executable,
                        "prefix": sys.prefix,
                        "base_prefix": getattr(sys, "base_prefix", None),
                    },
                    "os": {
                        "platform": sys.platform,
                        "platform_str": platform.platform(),
                        "system": getattr(uname, "system", None),
                        "release": getattr(uname, "release", None),
                        "version": getattr(uname, "version", None),
                        "machine": getattr(uname, "machine", None),
                        "processor": getattr(uname, "processor", None),
                        "architecture": {
                            "bits": arch[0] if isinstance(arch, (tuple, list)) and len(arch) > 0 else None,
                            "linkage": arch[1] if isinstance(arch, (tuple, list)) and len(arch) > 1 else None,
                        },
                    },
                }
            )
        except Exception as error:
            return Err(error)


__all__ = ["SystemInfo"]
