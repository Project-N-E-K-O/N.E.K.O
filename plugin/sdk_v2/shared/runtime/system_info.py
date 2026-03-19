"""Shared facade for system-info runtime."""

from __future__ import annotations

import platform
import sys
from typing import cast

from plugin.sdk_v2.shared.core.context import ensure_sdk_context
from plugin.sdk_v2.shared.core.types import JsonObject, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import CapabilityUnavailableError, InvalidArgumentError, SdkError, TransportError

SystemInfoErrorLike = InvalidArgumentError | CapabilityUnavailableError | TransportError


class SystemInfo:
    """Async-first system-info facade."""

    def __init__(self, plugin_ctx: PluginContextProtocol):
        self.plugin_ctx = ensure_sdk_context(plugin_ctx)

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, InvalidArgumentError]:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        return _OK_NONE

    @staticmethod
    def _normalize_impl_error(error: Exception, *, op_name: str, timeout: float | None = None) -> SystemInfoErrorLike:
        if isinstance(error, (InvalidArgumentError, CapabilityUnavailableError, TransportError)):
            return error
        if isinstance(error, SdkError):
            return TransportError(
                str(error) or error.__class__.__name__,
                op_name=op_name,
                timeout=timeout,
                details=getattr(error, "details", None),
                code=getattr(error, "code", None),
                inner=error,
            )
        return TransportError(str(error), op_name=op_name, timeout=timeout)

    def _normalize_result(
        self,
        result: Result[JsonObject, object],
        *,
        op_name: str,
        timeout: float | None = None,
    ) -> Result[JsonObject, SystemInfoErrorLike]:
        if isinstance(result, Err):
            error = result.error
            if isinstance(error, Exception):
                return Err(self._normalize_impl_error(error, op_name=op_name, timeout=timeout))
            return Err(TransportError(str(error), op_name=op_name, timeout=timeout))
        return cast(Result[JsonObject, SystemInfoErrorLike], result)

    async def _do_get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, SystemInfoErrorLike]:
        host_ctx = getattr(self.plugin_ctx, "_host_ctx", self.plugin_ctx)
        host_getter = getattr(host_ctx, "get_system_config", None)
        getter = getattr(self.plugin_ctx, "get_system_config", None)
        has_host_getter = callable(host_getter)
        default_getter = getattr(type(self.plugin_ctx), "get_system_config", None)
        has_fallback_getter = callable(getter) and (
            host_ctx is self.plugin_ctx
            or getattr(getter, "__func__", None) is not default_getter
        )
        if not has_host_getter and not has_fallback_getter:
            return Err(
                CapabilityUnavailableError(
                    "plugin_ctx.get_system_config is not available",
                    op_name="system_info.get_system_config",
                    capability="plugin_ctx.get_system_config",
                    timeout=timeout,
                )
            )
        try:
            active_getter = host_getter if has_host_getter else getter
            result = await active_getter(timeout=timeout)
            if not isinstance(result, dict):
                return cast(Result[JsonObject, SystemInfoErrorLike], Ok({"result": result}))
            return cast(Result[JsonObject, SystemInfoErrorLike], Ok(result))
        except Exception as error:
            return cast(Result[JsonObject, SystemInfoErrorLike], Err(self._normalize_impl_error(error, op_name="system_info.get_system_config", timeout=timeout)))

    async def _do_get_server_settings(self, *, timeout: float = 5.0) -> Result[JsonObject, SystemInfoErrorLike]:
        try:
            config = await self._do_get_system_config(timeout=timeout)
            if isinstance(config, Err):
                error = config.error
                if isinstance(error, CapabilityUnavailableError):
                    return Err(
                        CapabilityUnavailableError(
                            str(error),
                            op_name="system_info.get_server_settings",
                            capability=getattr(error, "capability", None),
                            timeout=timeout,
                            details=getattr(error, "details", None),
                        )
                    )
                if isinstance(error, TransportError):
                    return Err(
                        TransportError(
                            str(error),
                            op_name="system_info.get_server_settings",
                            timeout=timeout,
                            details=getattr(error, "details", None),
                        )
                    )
                return Err(self._normalize_impl_error(error, op_name="system_info.get_server_settings", timeout=timeout))
            payload = config.value
            if isinstance(payload.get("data"), dict):
                payload = payload["data"]
            settings = payload.get("config") if isinstance(payload, dict) else None
            return Ok(settings if isinstance(settings, dict) else {})
        except Exception as error:
            return cast(Result[JsonObject, SystemInfoErrorLike], Err(self._normalize_impl_error(error, op_name="system_info.get_server_settings", timeout=timeout)))

    async def get_system_config(self, *, timeout: float = 5.0) -> Result[JsonObject, SystemInfoErrorLike]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[JsonObject, SystemInfoErrorLike], timeout_ok)
        try:
            return self._normalize_result(
                cast(Result[JsonObject, object], await self._do_get_system_config(timeout=timeout)),
                op_name="system_info.get_system_config",
                timeout=timeout,
            )
        except Exception as error:
            return cast(
                Result[JsonObject, SystemInfoErrorLike],
                Err(self._normalize_impl_error(error, op_name="system_info.get_system_config", timeout=timeout)),
            )

    async def get_server_settings(self, *, timeout: float = 5.0) -> Result[JsonObject, SystemInfoErrorLike]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[JsonObject, SystemInfoErrorLike], timeout_ok)
        try:
            return self._normalize_result(
                cast(Result[JsonObject, object], await self._do_get_server_settings(timeout=timeout)),
                op_name="system_info.get_server_settings",
                timeout=timeout,
            )
        except Exception as error:
            return cast(
                Result[JsonObject, SystemInfoErrorLike],
                Err(self._normalize_impl_error(error, op_name="system_info.get_server_settings", timeout=timeout)),
            )

    async def get_python_env(self) -> Result[JsonObject, TransportError]:
        try:
            try:
                uname = platform.uname()
            except Exception:
                uname = None
            try:
                arch = platform.architecture()
            except Exception:
                arch = None
            return cast(Result[JsonObject, TransportError], Ok(
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
            ))
        except Exception as error:
            normalized = self._normalize_impl_error(error, op_name="system_info.get_python_env")
            if isinstance(normalized, (InvalidArgumentError, CapabilityUnavailableError)):
                normalized = TransportError(
                    str(normalized),
                    op_name="system_info.get_python_env",
                    details=getattr(normalized, "details", None),
                    code=getattr(normalized, "code", None),
                    inner=normalized,
                )
            return Err(normalized)


_OK_NONE = Ok(None)

__all__ = ["SystemInfo"]
