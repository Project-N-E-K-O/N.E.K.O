"""Config facade for SDK v2 shared core."""

from __future__ import annotations

from collections.abc import Mapping

from plugin.sdk_v2.public.core.config_runtime import (
    deep_merge_config,
    fetch_ctx_payload,
    get_active_profile_name,
    get_profile_names,
    unwrap_config_payload,
    unwrap_profile_payload,
    unwrap_profiles_state,
    validate_profile_name,
)
from plugin.sdk_v2.shared.core._facade import AsyncResultFacadeTemplate
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Err, Ok, Result
from .types import JsonObject, JsonValue, PluginContextProtocol


def _is_json_object(value: JsonValue | None) -> bool:
    return isinstance(value, dict)


def _is_json_list(value: JsonValue | None) -> bool:
    return isinstance(value, list)


def _is_json_bool(value: JsonValue | None) -> bool:
    return isinstance(value, bool)


def _is_json_int(value: JsonValue | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_json_str(value: JsonValue | None) -> bool:
    return isinstance(value, str)


class PluginConfigError(RuntimeError):
    """Config API contract violation / transport error."""


class ConfigPathError(PluginConfigError):
    """Invalid or missing config path."""


class ConfigValidationError(PluginConfigError):
    """Config payload failed schema validation."""


class ConfigProfileError(PluginConfigError):
    """Profile-related config operation failed."""


class _ConfigFacadeBase(AsyncResultFacadeTemplate):
    def __init__(self, _ctx: PluginContextProtocol):
        plugin_id = str(getattr(_ctx, "plugin_id", "unknown"))
        super().__init__(logger=getattr(_ctx, "logger", None) or get_plugin_logger(plugin_id, "config"))
        self._ctx = _ctx

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return _OK_NONE

    @staticmethod
    def _unwrap_payload(value: object) -> Result[JsonObject, Exception]:
        try:
            return Ok(unwrap_config_payload(value))
        except TypeError as error:
            return Err(ConfigValidationError(str(error)))

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

    async def _fetch(self, *, getter_name: str, timeout: float, error_label: str, arg: str | None = None) -> Result[object, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            raw = await fetch_ctx_payload(self._ctx, getter_name=getter_name, timeout=timeout, arg=arg)
            return Ok(raw)
        except AttributeError:
            return self._err(getter_name, PluginConfigError(f"ctx.{getter_name} is not available"))
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return self._err(getter_name, PluginConfigError(f"failed to fetch {error_label}: {error}"))


class PluginConfigBaseView(_ConfigFacadeBase):
    """Read-only view of base config."""

    async def dump(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        raw = await self._fetch(getter_name="get_own_base_config", timeout=timeout, error_label="base config")
        if isinstance(raw, Err):
            return raw
        return self._unwrap_payload(raw.value)

    async def get(self, path: str, default: JsonValue | None = None, *, timeout: float = 5.0) -> Result[JsonValue | None, Exception]:
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
        dumped = await self.dump(timeout=timeout)
        if isinstance(dumped, Err):
            return dumped
        return self._get_by_path(dumped.value, path)

    async def get_bool(self, path: str, default: bool | None = None, *, timeout: float = 5.0) -> Result[bool | None, Exception]:
        return await self._get_typed(path, default, _is_json_bool, "bool", timeout=timeout)

    async def get_int(self, path: str, default: int | None = None, *, timeout: float = 5.0) -> Result[int | None, Exception]:
        return await self._get_typed(path, default, _is_json_int, "int", timeout=timeout)

    async def get_str(self, path: str, default: str | None = None, *, timeout: float = 5.0) -> Result[str | None, Exception]:
        return await self._get_typed(path, default, _is_json_str, "str", timeout=timeout)

    async def get_list(self, path: str, default: list[JsonValue] | None = None, *, timeout: float = 5.0) -> Result[list[JsonValue] | None, Exception]:
        return await self._get_typed(path, default, _is_json_list, "list", timeout=timeout)

    async def get_object(self, path: str, default: JsonObject | None = None, *, timeout: float = 5.0) -> Result[JsonObject | None, Exception]:
        return await self._get_typed(path, default, _is_json_object, "object", timeout=timeout)

    async def _get_typed(
        self,
        path: str,
        default: JsonValue | None,
        predicate,
        expected_type: str,
        *,
        timeout: float,
    ) -> Result[JsonValue | None, Exception]:
        value = await self.get(path, default=default, timeout=timeout)
        if isinstance(value, Err):
            return value
        if value.value is None:
            return value
        if predicate(value.value):
            return value
        return Err(ConfigValidationError(f"value at {path!r} is not {expected_type}"))

    async def get_section(self, path: str, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        required = await self.require(path, timeout=timeout)
        if isinstance(required, Err):
            return required
        if not isinstance(required.value, dict):
            return Err(ConfigValidationError(f"section at {path!r} is not object"))
        return Ok(required.value)


class PluginConfigProfiles(_ConfigFacadeBase):
    """Profile management and profile-aware config reads."""

    async def state(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        raw = await self._fetch(getter_name="get_own_profiles_state", timeout=timeout, error_label="profiles state")
        if isinstance(raw, Err):
            return raw
        try:
            return Ok(unwrap_profiles_state(raw.value))
        except TypeError as error:
            return Err(ConfigValidationError(str(error)))

    async def list(self, *, timeout: float = 5.0) -> Result[list[str], Exception]:
        state = await self.state(timeout=timeout)
        if isinstance(state, Err):
            return state
        return Ok(get_profile_names(state.value))

    async def active(self, *, timeout: float = 5.0) -> Result[str | None, Exception]:
        state = await self.state(timeout=timeout)
        if isinstance(state, Err):
            return state
        return Ok(get_active_profile_name(state.value))

    async def require_active(self, *, timeout: float = 5.0) -> Result[str, Exception]:
        active = await self.active(timeout=timeout)
        if isinstance(active, Err):
            return active
        if active.value is None:
            return Err(ConfigProfileError("no active profile; call config.profiles.activate(name) or create(name, ..., make_active=True) first"))
        return Ok(active.value)

    async def current(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        active = await self.require_active(timeout=timeout)
        if isinstance(active, Err):
            return active
        overlay = await self.get(active.value, timeout=timeout)
        if isinstance(overlay, Err):
            return overlay
        effective = await self.effective(active.value, timeout=timeout)
        if isinstance(effective, Err):
            return effective
        return Ok({"name": active.value, "overlay": overlay.value, "effective": effective.value})

    async def ensure_active(
        self,
        profile_name: str,
        initial: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Result[str, Exception]:
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        current = await self.active(timeout=timeout)
        if isinstance(current, Err):
            return current
        if current.value is not None:
            return Ok(current.value)
        exists = await self.exists(normalized, timeout=timeout)
        if isinstance(exists, Err):
            return exists
        if not exists.value:
            created = await self.create(normalized, initial, make_active=True, timeout=timeout)
            if isinstance(created, Err):
                return created
            return Ok(normalized)
        activated = await self.activate(normalized, timeout=timeout)
        if isinstance(activated, Err):
            return activated
        if not activated.value:
            return Err(ConfigProfileError(f"failed to activate profile: {normalized}"))
        return Ok(normalized)

    async def exists(self, profile_name: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        listed = await self.list(timeout=timeout)
        if isinstance(listed, Err):
            return listed
        return Ok(normalized in listed.value)

    async def get(self, profile_name: str, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        raw = await self._fetch(getter_name="get_own_profile_config", timeout=timeout, error_label="profile", arg=normalized)
        if isinstance(raw, Err):
            return raw
        try:
            return Ok(unwrap_profile_payload(raw.value))
        except TypeError as error:
            return Err(ConfigValidationError(str(error)))

    async def effective(self, profile_name: str | None = None, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        if profile_name is None:
            raw = await self._fetch(getter_name="get_own_config", timeout=timeout, error_label="config")
            if isinstance(raw, Err):
                return raw
            return self._unwrap_payload(raw.value)
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        raw = await self._fetch(getter_name="get_own_effective_config", timeout=timeout, error_label="effective config", arg=normalized)
        if isinstance(raw, Err):
            return raw
        return self._unwrap_payload(raw.value)

    async def create(
        self,
        profile_name: str,
        initial: Mapping[str, JsonValue] | None = None,
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> Result[JsonObject, Exception]:
        return await self.replace(profile_name, dict(initial or {}), make_active=make_active, timeout=timeout)

    async def replace(
        self,
        profile_name: str,
        config: JsonObject,
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> Result[JsonObject, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        if not isinstance(config, dict):
            return Err(ConfigValidationError("profile config must be an object"))
        updater = getattr(self._ctx, "upsert_own_profile_config", None)
        if updater is None:
            return Err(PluginConfigError("ctx.upsert_own_profile_config is not available"))
        try:
            raw = await updater(normalized, dict(config), make_active=make_active, timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to upsert profile: {error}"))
        try:
            return Ok(unwrap_profile_payload(raw))
        except TypeError as error:
            return Err(ConfigValidationError(str(error)))

    async def update(self, profile_name: str, patch: Mapping[str, JsonValue], *, timeout: float = 10.0) -> Result[JsonObject, Exception]:
        current = await self.get(profile_name, timeout=timeout)
        if isinstance(current, Err):
            return current
        if not isinstance(patch, Mapping):
            return Err(ConfigValidationError("patch must be an object"))
        merged = deep_merge_config(current.value, patch)
        return await self.replace(profile_name, merged, timeout=timeout)

    async def set(self, profile_name: str, path: str, value: JsonValue, *, timeout: float = 10.0) -> Result[JsonObject, Exception]:
        current = await self.get(profile_name, timeout=timeout)
        if isinstance(current, Err):
            return current
        updated = self._set_by_path(dict(current.value), path, value)
        if isinstance(updated, Err):
            return updated
        return await self.replace(profile_name, updated.value, timeout=timeout)

    async def delete(self, profile_name: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        deleter = getattr(self._ctx, "delete_own_profile_config", None)
        if deleter is None:
            return Err(PluginConfigError("ctx.delete_own_profile_config is not available"))
        try:
            raw = await deleter(normalized, timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to delete profile: {error}"))
        if not isinstance(raw, dict):
            return Err(ConfigValidationError(f"expected dict delete payload, got {type(raw)!r}"))
        return Ok(bool(raw.get("removed")))

    async def activate(self, profile_name: str, *, timeout: float = 10.0) -> Result[bool, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        try:
            normalized = validate_profile_name(profile_name)
        except ValueError as error:
            return Err(ConfigProfileError(str(error)))
        activator = getattr(self._ctx, "set_own_active_profile", None)
        if activator is None:
            return Err(PluginConfigError("ctx.set_own_active_profile is not available"))
        try:
            raw = await activator(normalized, timeout=timeout)
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            return Err(PluginConfigError(f"failed to activate profile: {error}"))
        try:
            state = unwrap_profiles_state(raw)
        except TypeError as error:
            return Err(ConfigValidationError(str(error)))
        return Ok(get_active_profile_name(state) == normalized)


class PluginConfig(_ConfigFacadeBase):
    """Primary plugin-facing config view.

    - reads target the current effective config
    - writes target the active profile overlay when profile APIs are available
    - advanced reads live under `.base` and `.profiles`
    """

    def __init__(self, _ctx: PluginContextProtocol):
        super().__init__(_ctx)
        self.base = PluginConfigBaseView(_ctx)
        self.profiles = PluginConfigProfiles(_ctx)

    async def dump(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self.profiles.effective(timeout=timeout)

    async def get(self, path: str, default: JsonValue | None = None, *, timeout: float = 5.0) -> Result[JsonValue | None, Exception]:
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
        dumped = await self.dump(timeout=timeout)
        if isinstance(dumped, Err):
            return dumped
        return self._get_by_path(dumped.value, path)

    async def set(self, path: str, value: JsonValue, *, timeout: float = 5.0) -> Result[None, Exception]:
        active = await self.profiles.require_active(timeout=timeout)
        if isinstance(active, Err):
            return active
        result = await self.profiles.set(active.value, path, value, timeout=timeout)
        if isinstance(result, Err):
            return result
        return Ok(None)

    async def update(self, patch: Mapping[str, JsonValue], *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        active = await self.profiles.require_active(timeout=timeout)
        if isinstance(active, Err):
            return active
        return await self.profiles.update(active.value, patch, timeout=timeout)

    async def get_bool(self, path: str, default: bool | None = None, *, timeout: float = 5.0) -> Result[bool | None, Exception]:
        return await self._get_typed(path, default, _is_json_bool, "bool", timeout=timeout)

    async def get_int(self, path: str, default: int | None = None, *, timeout: float = 5.0) -> Result[int | None, Exception]:
        return await self._get_typed(path, default, _is_json_int, "int", timeout=timeout)

    async def get_str(self, path: str, default: str | None = None, *, timeout: float = 5.0) -> Result[str | None, Exception]:
        return await self._get_typed(path, default, _is_json_str, "str", timeout=timeout)

    async def get_list(self, path: str, default: list[JsonValue] | None = None, *, timeout: float = 5.0) -> Result[list[JsonValue] | None, Exception]:
        return await self._get_typed(path, default, _is_json_list, "list", timeout=timeout)

    async def get_object(self, path: str, default: JsonObject | None = None, *, timeout: float = 5.0) -> Result[JsonObject | None, Exception]:
        return await self._get_typed(path, default, _is_json_object, "object", timeout=timeout)

    async def _get_typed(
        self,
        path: str,
        default: JsonValue | None,
        predicate,
        expected_type: str,
        *,
        timeout: float,
    ) -> Result[JsonValue | None, Exception]:
        value = await self.get(path, default=default, timeout=timeout)
        if isinstance(value, Err):
            return value
        if value.value is None:
            return value
        if predicate(value.value):
            return value
        return Err(ConfigValidationError(f"value at {path!r} is not {expected_type}"))

    async def get_section(self, path: str, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        required = await self.require(path, timeout=timeout)
        if isinstance(required, Err):
            return required
        if not isinstance(required.value, dict):
            return Err(ConfigValidationError(f"section at {path!r} is not object"))
        return Ok(required.value)

    # Compatibility aliases
    async def dump_base(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self.base.dump(timeout=timeout)

    async def get_profiles_state(self, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self.profiles.state(timeout=timeout)

    async def get_profile(self, profile_name: str, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self.profiles.get(profile_name, timeout=timeout)

    async def dump_effective(self, profile_name: str | None = None, *, timeout: float = 5.0) -> Result[JsonObject, Exception]:
        return await self.profiles.effective(profile_name, timeout=timeout)


_OK_NONE = Ok(None)

__all__ = [
    "PluginConfigError",
    "ConfigPathError",
    "ConfigValidationError",
    "ConfigProfileError",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "PluginConfig",
]
