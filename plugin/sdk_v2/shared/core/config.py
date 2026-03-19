"""Config facade for SDK v2 shared core.

All methods raise ValidationError or TransportError on failure (no Result monad).
"""

from __future__ import annotations

from collections.abc import Mapping

from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models.exceptions import (
    ConfigPathError,
    ConfigProfileError,
    ConfigValidationError,
    InvalidArgumentError,
    PluginConfigError,
    TransportError,
    ValidationError,
)
from .context import ensure_sdk_context
from .types import JsonObject, JsonValue, LoggerLike, PluginContextProtocol


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def unwrap_config_payload(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"expected dict payload, got {type(value)!r}")
    data = value.get("data")
    if isinstance(data, dict):
        value = data
    config = value.get("config")
    if config is None:
        return value
    if not isinstance(config, dict):
        raise TypeError(f"expected dict config, got {type(config)!r}")
    return config


def unwrap_profiles_state(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"expected dict profiles state, got {type(value)!r}")
    if isinstance(value.get("data"), dict):
        value = value["data"]
    return value


def unwrap_profile_payload(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"expected dict profile payload, got {type(value)!r}")
    if isinstance(value.get("data"), dict):
        value = value["data"]
    cfg = value.get("config")
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise TypeError(f"expected dict profile config, got {type(cfg)!r}")
    return cfg


def validate_profile_name(profile_name: str) -> str:
    if not isinstance(profile_name, str) or profile_name.strip() == "":
        raise ValueError("profile_name must be non-empty")
    return profile_name.strip()


def extract_profiles_config(state: JsonObject) -> JsonObject:
    profiles_cfg = state.get("config_profiles")
    return profiles_cfg if isinstance(profiles_cfg, dict) else {}


def get_active_profile_name(state: JsonObject) -> str | None:
    profiles_cfg = extract_profiles_config(state)
    active = profiles_cfg.get("active")
    return active.strip() if isinstance(active, str) and active.strip() else None


def get_profile_names(state: JsonObject) -> list[str]:
    profiles_cfg = extract_profiles_config(state)
    files = profiles_cfg.get("files")
    if not isinstance(files, dict):
        return []
    return sorted(str(name) for name in files.keys() if isinstance(name, str))


def deep_merge_config(base: JsonObject, patch: Mapping[str, JsonValue]) -> JsonObject:
    merged: JsonObject = dict(base)
    for key, value in patch.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_by_path(data: JsonObject, path: str) -> JsonValue:
    if path == "":
        return data
    current: object = data
    for part in path.split("."):
        if not isinstance(current, dict):
            raise ValidationError(f"invalid path: {path!r}")
        if part not in current:
            raise ValidationError(f"path not found: {path!r}")
        current = current[part]
    return current  # type: ignore[return-value]


def _set_by_path(data: JsonObject, path: str, value: JsonValue) -> JsonObject:
    if path == "":
        if isinstance(value, dict):
            return value
        raise ValidationError("root path requires object value")
    parts = path.split(".")
    current: JsonObject = data
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value
    return data


async def _fetch_ctx(ctx: PluginContextProtocol, getter_name: str, timeout: float, arg: str | None = None) -> object:
    """Fetch from context, raising TransportError on failure."""
    if timeout <= 0:
        raise ValidationError("timeout must be > 0")
    try:
        getter = getattr(ctx, getter_name)
        return await getter(timeout=timeout) if arg is None else await getter(arg, timeout=timeout)
    except AttributeError:
        raise TransportError(f"ctx.{getter_name} is not available")
    except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
        raise TransportError(f"failed to fetch {getter_name}: {error}") from error


# ---------------------------------------------------------------------------
# PluginConfig — single flat class
# ---------------------------------------------------------------------------

class PluginConfig:
    """Plugin-facing config API.

    - `get/require/dump` read the current effective config
    - `set/update` write to the active profile overlay
    - `profile_*` methods manage profiles
    - `base_dump/base_get` read the base (non-profile) config
    """

    def __init__(self, ctx: PluginContextProtocol) -> None:
        self.ctx = ensure_sdk_context(ctx)
        self.logger: LoggerLike = self.ctx.logger or get_plugin_logger(self.ctx.plugin_id, "config")

    # --- effective config reads ---

    async def dump(self, *, timeout: float = 5.0) -> JsonObject:
        return await self.profile_effective(timeout=timeout)

    async def get(self, path: str, default: JsonValue | None = None, *, timeout: float = 5.0) -> JsonValue | None:
        data = await self.dump(timeout=timeout)
        try:
            return _get_by_path(data, path)
        except ValidationError:
            return default

    async def require(self, path: str, *, timeout: float = 5.0) -> JsonValue:
        return _get_by_path(await self.dump(timeout=timeout), path)

    async def get_bool(self, path: str, default: bool | None = None, *, timeout: float = 5.0) -> bool | None:
        value = await self.get(path, default=default, timeout=timeout)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValidationError(f"value at {path!r} is not bool")
        return value

    async def get_int(self, path: str, default: int | None = None, *, timeout: float = 5.0) -> int | None:
        value = await self.get(path, default=default, timeout=timeout)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"value at {path!r} is not int")
        return value

    async def get_str(self, path: str, default: str | None = None, *, timeout: float = 5.0) -> str | None:
        value = await self.get(path, default=default, timeout=timeout)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"value at {path!r} is not str")
        return value

    async def set(self, path: str, value: JsonValue, *, timeout: float = 5.0) -> None:
        active = await self._require_active_name(timeout=timeout)
        current = await self._fetch_profile(active, timeout=timeout)
        updated = _set_by_path(dict(current), path, value)
        await self._upsert_profile(active, updated, timeout=timeout)

    async def update(self, patch: Mapping[str, JsonValue], *, timeout: float = 5.0) -> JsonObject:
        active = await self._require_active_name(timeout=timeout)
        current = await self._fetch_profile(active, timeout=timeout)
        merged = deep_merge_config(current, patch)
        return await self._upsert_profile(active, merged, timeout=timeout)

    # --- base config reads ---

    async def base_dump(self, *, timeout: float = 5.0) -> JsonObject:
        raw = await _fetch_ctx(self.ctx, "get_own_base_config", timeout)
        return unwrap_config_payload(raw)

    async def base_get(self, path: str, default: JsonValue | None = None, *, timeout: float = 5.0) -> JsonValue | None:
        data = await self.base_dump(timeout=timeout)
        try:
            return _get_by_path(data, path)
        except ValidationError:
            return default

    # --- profile management ---

    async def profile_state(self, *, timeout: float = 5.0) -> JsonObject:
        raw = await _fetch_ctx(self.ctx, "get_own_profiles_state", timeout)
        return unwrap_profiles_state(raw)

    async def profile_list(self, *, timeout: float = 5.0) -> list[str]:
        return get_profile_names(await self.profile_state(timeout=timeout))

    async def profile_active(self, *, timeout: float = 5.0) -> str | None:
        return get_active_profile_name(await self.profile_state(timeout=timeout))

    async def profile_get(self, profile_name: str, *, timeout: float = 5.0) -> JsonObject:
        return await self._fetch_profile(validate_profile_name(profile_name), timeout=timeout)

    async def profile_effective(self, profile_name: str | None = None, *, timeout: float = 5.0) -> JsonObject:
        if profile_name is None:
            raw = await _fetch_ctx(self.ctx, "get_own_config", timeout)
            return unwrap_config_payload(raw)
        normalized = validate_profile_name(profile_name)
        raw = await _fetch_ctx(self.ctx, "get_own_effective_config", timeout, arg=normalized)
        return unwrap_config_payload(raw)

    async def profile_create(
        self,
        profile_name: str,
        initial: Mapping[str, JsonValue] | None = None,
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> JsonObject:
        return await self._upsert_profile(
            validate_profile_name(profile_name),
            dict(initial or {}),
            make_active=make_active,
            timeout=timeout,
        )

    async def profile_update(self, profile_name: str, patch: Mapping[str, JsonValue], *, timeout: float = 10.0) -> JsonObject:
        normalized = validate_profile_name(profile_name)
        current = await self._fetch_profile(normalized, timeout=timeout)
        merged = deep_merge_config(current, patch)
        return await self._upsert_profile(normalized, merged, timeout=timeout)

    async def profile_delete(self, profile_name: str, *, timeout: float = 10.0) -> bool:
        normalized = validate_profile_name(profile_name)
        if timeout <= 0:
            raise ValidationError("timeout must be > 0")
        try:
            raw = await self.ctx.delete_own_profile_config(normalized, timeout=timeout)
        except AttributeError:
            raise TransportError("ctx.delete_own_profile_config is not available")
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            raise TransportError(f"failed to delete profile: {error}") from error
        if not isinstance(raw, dict):
            raise ValidationError(f"expected dict delete payload, got {type(raw)!r}")
        return bool(raw.get("removed"))

    async def profile_activate(self, profile_name: str, *, timeout: float = 10.0) -> bool:
        normalized = validate_profile_name(profile_name)
        if timeout <= 0:
            raise ValidationError("timeout must be > 0")
        try:
            raw = await self.ctx.set_own_active_profile(normalized, timeout=timeout)
        except AttributeError:
            raise TransportError("ctx.set_own_active_profile is not available")
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            raise TransportError(f"failed to activate profile: {error}") from error
        state = unwrap_profiles_state(raw)
        return get_active_profile_name(state) == normalized

    async def profile_ensure_active(
        self,
        profile_name: str,
        initial: Mapping[str, JsonValue] | None = None,
        *,
        timeout: float = 10.0,
    ) -> str:
        normalized = validate_profile_name(profile_name)
        current = await self.profile_active(timeout=timeout)
        if current is not None:
            return current
        names = await self.profile_list(timeout=timeout)
        if normalized not in names:
            await self.profile_create(normalized, initial, make_active=True, timeout=timeout)
            return normalized
        ok = await self.profile_activate(normalized, timeout=timeout)
        if not ok:
            raise TransportError(f"failed to activate profile: {normalized}")
        return normalized

    # --- internal helpers ---

    async def _require_active_name(self, *, timeout: float) -> str:
        active = await self.profile_active(timeout=timeout)
        if active is None:
            raise ValidationError("no active profile")
        return active

    async def _fetch_profile(self, normalized: str, *, timeout: float) -> JsonObject:
        raw = await _fetch_ctx(self.ctx, "get_own_profile_config", timeout, arg=normalized)
        return unwrap_profile_payload(raw)

    async def _upsert_profile(
        self,
        normalized: str,
        config: JsonObject,
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> JsonObject:
        if timeout <= 0:
            raise ValidationError("timeout must be > 0")
        try:
            raw = await self.ctx.upsert_own_profile_config(normalized, dict(config), make_active=make_active, timeout=timeout)
        except AttributeError:
            raise TransportError("ctx.upsert_own_profile_config is not available")
        except (RuntimeError, ValueError, TimeoutError, TypeError) as error:
            raise TransportError(f"failed to upsert profile: {error}") from error
        return unwrap_profile_payload(raw)


# Backwards-compat aliases
PluginConfigBaseView = PluginConfig
PluginConfigProfiles = PluginConfig

__all__ = [
    "PluginConfig",
    "PluginConfigBaseView",
    "PluginConfigProfiles",
    "deep_merge_config",
    "get_active_profile_name",
    "get_profile_names",
    "unwrap_config_payload",
    "unwrap_profile_payload",
    "unwrap_profiles_state",
    "validate_profile_name",
]
