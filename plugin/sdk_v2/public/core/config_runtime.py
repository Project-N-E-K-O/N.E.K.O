"""Internal runtime helpers for shared.core.config."""

from __future__ import annotations

from collections.abc import Mapping

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue


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


async def fetch_ctx_payload(
    ctx: object,
    *,
    getter_name: str,
    timeout: float,
    arg: str | None = None,
) -> object:
    getter = getattr(ctx, getter_name, None)
    if getter is None:
        raise AttributeError(f"ctx.{getter_name} is not available")
    if arg is None:
        return await getter(timeout=timeout)
    return await getter(arg, timeout=timeout)


__all__ = [
    "unwrap_config_payload",
    "unwrap_profiles_state",
    "unwrap_profile_payload",
    "validate_profile_name",
    "extract_profiles_config",
    "get_active_profile_name",
    "get_profile_names",
    "deep_merge_config",
    "fetch_ctx_payload",
]
