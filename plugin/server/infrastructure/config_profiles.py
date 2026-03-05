from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_merge import deep_merge

logger = get_logger("server.infrastructure.config_profiles")

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


def _to_string_key_mapping(raw: Mapping[object, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key_obj, value in raw.items():
        if isinstance(key_obj, str):
            normalized[key_obj] = value
    return normalized

def resolve_profile_path(path_str: str, base_dir: Path) -> Path | None:
    try:
        expanded = os.path.expandvars(os.path.expanduser(path_str))
        path_obj = Path(expanded)
        if not path_obj.is_absolute():
            path_obj = base_dir / path_obj
        resolved_base = base_dir.resolve()
        resolved_path = path_obj.resolve()
        try:
            resolved_path.relative_to(resolved_base)
        except ValueError:
            logger.warning(
                "Rejected profile path outside base_dir: path={}, base_dir={}",
                resolved_path,
                resolved_base,
            )
            return None
        return resolved_path
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "Failed to resolve user profile path {!r} for base_dir {}",
            path_str,
            base_dir,
        )
        return None


def load_profiles_cfg_from_file(
    plugin_id: str,
    config_path: Path,
) -> dict[str, object] | None:
    base_dir = config_path.parent
    profiles_path = base_dir / "profiles.toml"

    if not profiles_path.exists():
        return None

    if tomllib is None:
        logger.warning(
            "Plugin {}: TOML library not available; cannot load profiles.toml",
            plugin_id,
        )
        return None

    try:
        with profiles_path.open("rb") as profile_file:
            data = tomllib.load(profile_file)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "Plugin {}: failed to load profiles.toml from {}: {}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
            exc,
        )
        return None

    if not isinstance(data, Mapping):
        logger.warning(
            "Plugin {}: profiles.toml at {} is not a TOML table at root; got {!r}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
            type(data).__name__,
        )
        return None

    profiles_cfg_obj = data.get("config_profiles")
    if not isinstance(profiles_cfg_obj, Mapping):
        logger.warning(
            "Plugin {}: 'config_profiles' table not found or invalid in profiles.toml at {}; falling back to plugin.config_profiles",
            plugin_id,
            profiles_path,
        )
        return None

    logger.info(
        "Plugin {}: using profiles.toml at {} for config_profiles; plugin.toml [plugin.config_profiles] will be ignored",
        plugin_id,
        profiles_path,
    )
    return _to_string_key_mapping(profiles_cfg_obj)


def _extract_profiles_cfg_from_base_config(base_config: Mapping[object, object]) -> dict[str, object] | None:
    plugin_section_obj = base_config.get("plugin")
    if not isinstance(plugin_section_obj, Mapping):
        return None
    plugin_section = _to_string_key_mapping(plugin_section_obj)
    config_profiles_obj = plugin_section.get("config_profiles")
    if not isinstance(config_profiles_obj, Mapping):
        return None
    return _to_string_key_mapping(config_profiles_obj)


def _load_base_config_from_file(config_path: Path) -> dict[str, object] | None:
    if tomllib is None:
        return None
    try:
        with config_path.open("rb") as file_obj:
            base_obj = tomllib.load(file_obj)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "Failed to load base config {} for profile fallback: err_type={}, err={}",
            config_path,
            type(exc).__name__,
            str(exc),
        )
        return None
    if not isinstance(base_obj, Mapping):
        logger.warning(
            "Base config {} is not a TOML table at root for profile fallback: got {!r}",
            config_path,
            type(base_obj).__name__,
        )
        return None
    return _to_string_key_mapping(base_obj)


def _resolve_profiles_cfg(
    *,
    plugin_id: str,
    config_path: Path,
    base_config: Mapping[object, object] | None = None,
) -> dict[str, object] | None:
    profiles_cfg = load_profiles_cfg_from_file(plugin_id, config_path)
    if profiles_cfg is not None:
        return profiles_cfg

    candidate_base = base_config if isinstance(base_config, Mapping) else _load_base_config_from_file(config_path)
    if not isinstance(candidate_base, Mapping):
        return None
    return _extract_profiles_cfg_from_base_config(candidate_base)


def _resolve_active_profile_name(plugin_id: str, profiles_cfg: Mapping[object, object]) -> str | None:
    active_name: str | None = None
    raw_active = profiles_cfg.get("active")
    if isinstance(raw_active, str):
        active_name = raw_active.strip() or None

    env_key = f"NEKO_PLUGIN_{plugin_id.upper()}_PROFILE"
    env_override = os.getenv(env_key)
    if isinstance(env_override, str) and env_override.strip():
        active_name = env_override.strip()
    return active_name


def apply_user_config_profiles(
    *,
    plugin_id: str,
    base_config: dict[str, object],
    config_path: Path,
) -> dict[str, object]:
    if not isinstance(base_config, Mapping):
        return base_config

    profiles_cfg = _resolve_profiles_cfg(
        plugin_id=plugin_id,
        config_path=config_path,
        base_config=base_config,
    )
    if profiles_cfg is None:
        return base_config
    active_name = _resolve_active_profile_name(plugin_id, profiles_cfg)

    if not active_name:
        return base_config

    files_map_obj = profiles_cfg.get("files")
    if not isinstance(files_map_obj, Mapping):
        logger.warning(
            "Plugin {}: [plugin.config_profiles.files] must be a table mapping profile names to paths; got {!r}",
            plugin_id,
            type(files_map_obj).__name__ if files_map_obj is not None else None,
        )
        return base_config
    files_map = _to_string_key_mapping(files_map_obj)

    raw_path_obj = files_map.get(active_name)
    if (not isinstance(raw_path_obj, str) or not raw_path_obj.strip()) and active_name.isdigit():
        raw_path_obj = files_map.get(str(int(active_name)))
    if not isinstance(raw_path_obj, str) or not raw_path_obj.strip():
        logger.warning(
            "Plugin {}: active profile '{}' not found in [plugin.config_profiles.files]",
            plugin_id,
            active_name,
        )
        return base_config
    raw_path = raw_path_obj

    profile_path = resolve_profile_path(raw_path, config_path.parent)
    if profile_path is None:
        return base_config

    if not profile_path.exists():
        logger.warning(
            "Plugin {}: user profile file '{}' (resolved: {}) does not exist; using base config only",
            plugin_id,
            raw_path,
            profile_path,
        )
        return base_config

    if tomllib is None:
        logger.warning(
            "Plugin {}: TOML library not available; cannot load user profile {}",
            plugin_id,
            profile_path,
        )
        return base_config

    try:
        with profile_path.open("rb") as profile_file:
            overlay_obj = tomllib.load(profile_file)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "Plugin {}: failed to load user profile {}: {}; using base config only",
            plugin_id,
            profile_path,
            exc,
        )
        return base_config

    if not isinstance(overlay_obj, Mapping):
        logger.warning(
            "Plugin {}: user profile {} is not a TOML table at root; got {!r}",
            plugin_id,
            profile_path,
            type(overlay_obj).__name__,
        )
        return base_config
    overlay = _to_string_key_mapping(overlay_obj)

    if "plugin" in overlay:
        logger.error(
            "Plugin {}: user profile {} attempts to override [plugin] section; rejecting config",
            plugin_id,
            profile_path,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"User profile for plugin '{plugin_id}' must not define a top-level 'plugin' section; "
                f"found in {profile_path}"
            ),
        )

    merged = dict(base_config)
    for key, value in overlay.items():
        if key == "plugin":
            continue
        if isinstance(value, Mapping):
            value_mapping = _to_string_key_mapping(value)
            current_obj = merged.get(key)
            if isinstance(current_obj, Mapping):
                current_mapping = _to_string_key_mapping(current_obj)
                merged[key] = deep_merge(current_mapping, value_mapping)
            else:
                merged[key] = value_mapping
        else:
            merged[key] = value

    logger.info(
        "Plugin {}: applied user config profile '{}' from {}",
        plugin_id,
        active_name,
        profile_path,
    )
    return merged


def get_profiles_state(
    *,
    plugin_id: str,
    config_path: Path,
) -> dict[str, object]:
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    base_dir = config_path.parent
    profiles_path = base_dir / "profiles.toml"
    profiles_cfg = _resolve_profiles_cfg(plugin_id=plugin_id, config_path=config_path)

    active_name: str | None = None
    files_info: dict[str, object] = {}
    if isinstance(profiles_cfg, Mapping):
        active_name = _resolve_active_profile_name(plugin_id, profiles_cfg)

        files_map_obj = profiles_cfg.get("files")
        if isinstance(files_map_obj, Mapping):
            files_map = _to_string_key_mapping(files_map_obj)
            for name, raw_path_obj in files_map.items():
                if not isinstance(raw_path_obj, str):
                    continue
                resolved = resolve_profile_path(raw_path_obj, base_dir)
                files_info[name] = {
                    "path": raw_path_obj,
                    "resolved_path": str(resolved) if resolved is not None else None,
                    "exists": bool(resolved is not None and resolved.exists()),
                }

    return {
        "plugin_id": plugin_id,
        "profiles_path": str(profiles_path),
        "profiles_exists": profiles_path.exists(),
        "config_profiles": {
            "active": active_name,
            "files": files_info,
        }
        if profiles_cfg is not None
        else None,
    }


def get_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
    config_path: Path,
) -> dict[str, object]:
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    base_dir = config_path.parent
    profiles_cfg = _resolve_profiles_cfg(plugin_id=plugin_id, config_path=config_path)

    raw_path: str | None = None
    if isinstance(profiles_cfg, Mapping):
        files_map_obj = profiles_cfg.get("files")
        if isinstance(files_map_obj, Mapping):
            files_map = _to_string_key_mapping(files_map_obj)
            value = files_map.get(profile_name)
            if isinstance(value, str) and value.strip():
                raw_path = value

    if raw_path is None:
        if (
            "/" in profile_name
            or "\\" in profile_name
            or profile_name in {".", ".."}
        ):
            raise HTTPException(status_code=400, detail="Invalid profile_name")
        raw_path = f"profiles/{profile_name}.toml"

    profile_path = resolve_profile_path(raw_path, base_dir)
    resolved_str: str | None = None
    exists = False
    config: dict[str, object] = {}

    if profile_path is not None:
        resolved_str = str(profile_path)
        exists = profile_path.exists()
        if exists:
            try:
                with profile_path.open("rb") as profile_file:
                    data = tomllib.load(profile_file)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Plugin {}: failed to load profile {} at {}: {}",
                    plugin_id,
                    profile_name,
                    profile_path,
                    exc,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse profile '{profile_name}'",
                ) from exc
            except (OSError, RuntimeError) as exc:
                logger.warning(
                    "Plugin {}: failed to read profile {} at {}: {}",
                    plugin_id,
                    profile_name,
                    profile_path,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to read profile '{profile_name}'",
                ) from exc
            if isinstance(data, Mapping):
                config = _to_string_key_mapping(data)

    return {
        "plugin_id": plugin_id,
        "profile": {
            "name": profile_name,
            "path": raw_path,
            "resolved_path": resolved_str,
            "exists": exists,
        },
        "config": config,
    }
