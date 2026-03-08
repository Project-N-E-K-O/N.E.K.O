"""Config contract for SDK v2 shared core."""

from __future__ import annotations

from typing import Mapping

from plugin.sdk_v2.shared.models import Err, Ok, Result
from .types import JsonObject, JsonValue, PluginContextProtocol


class PluginConfigError(RuntimeError):
    """Config API contract violation / transport error."""


class ConfigPathError(PluginConfigError):
    """Invalid or missing config path."""


class ConfigValidationError(PluginConfigError):
    """Config payload failed schema validation."""


class PluginConfig:
    """Async-only config contract."""

    _MISSING = object()

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return Ok(None)

    @staticmethod
    def _unwrap_payload(value: object) -> Result[JsonObject, Exception]:
        if not isinstance(value, dict):
            return Err(ConfigValidationError(f"expected dict payload, got {type(value)!r}"))
        data = value.get("data")
        if isinstance(data, dict):
            value = data
        config = value.get("config")
        if config is None:
            return Ok(value)
        if not isinstance(config, dict):
            return Err(ConfigValidationError(f"expected dict config, got {type(config)!r}"))
        return Ok(config)

    @staticmethod
    def _get_by_path(data: JsonObject, path: str) -> Result[JsonValue, Exception]:
        if path == "":
            return Ok(data)

        current: object = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return Err(ConfigPathError(f"invalid path: {path!r}"))
            if part not in current:
                return Err(ConfigPathError(f"path not found: {path!r}"))
            current = current[part]
        return Ok(current)

    @staticmethod
    def _set_by_path(data: JsonObject, path: str, value: JsonValue) -> Result[JsonObject, Exception]:
        if path == "":
            if isinstance(value, dict):
                return Ok(value)
            return Err(ConfigValidationError("root path requires object value"))

        parts = path.split(".")
        current: JsonObject = data
        for part in parts[:-1]:
            nxt = current.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                current[part] = nxt
            current = nxt
        current[parts[-1]] = value
        return Ok(data)

    async def dump(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        """Return effective config snapshot.

        Boundary constraints:
        - `timeout > 0`
        - transport/timeout errors are surfaced as `Err(Exception)`
        """
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            raw = await self._ctx.get_own_config(timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to fetch config: {error}"))
        return self._unwrap_payload(raw)

    async def get(self, path: str, default: JsonValue | None = None, *, timeout: float = 5.0) -> Result[JsonValue | None, Exception]:
        """Read value by dotted path."""
        dumped = await self.dump(timeout=timeout)
        if isinstance(dumped, Err):
            return dumped
        got = self._get_by_path(dumped.value, path)
        if isinstance(got, Ok):
            return got
        if default is None:
            return got
        return Ok(default)

    async def require(self, path: str, *, timeout: float = 5.0) -> Result[JsonValue, Exception]:
        """Read path and fail when missing."""
        dumped = await self.dump(timeout=timeout)
        if isinstance(dumped, Err):
            return dumped
        return self._get_by_path(dumped.value, path)

    async def set(self, path: str, value: JsonValue, *, timeout: float = 5.0) -> Result[None, Exception]:
        """Write value by dotted path."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        dumped = await self.dump(timeout=timeout)
        if isinstance(dumped, Err):
            return dumped
        updated = self._set_by_path(dict(dumped.value), path, value)
        if isinstance(updated, Err):
            return updated
        try:
            result = await self._ctx.update_own_config(updated.value, timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to update config: {error}"))
        unwrapped = self._unwrap_payload(result)
        if isinstance(unwrapped, Err):
            return unwrapped
        return Ok(None)

    async def update(self, patch: Mapping[str, JsonValue], *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        """Apply partial update and return updated snapshot."""
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            raw = await self._ctx.update_own_config(dict(patch), timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to update config: {error}"))
        return self._unwrap_payload(raw)

    async def get_section(self, path: str, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        """Read subtree as object."""
        required = await self.require(path, timeout=timeout)
        if isinstance(required, Err):
            return required
        if not isinstance(required.value, dict):
            return Err(ConfigValidationError(f"section at {path!r} is not object"))
        return Ok(required.value)


__all__ = ["PluginConfig", "PluginConfigError", "ConfigPathError", "ConfigValidationError"]
