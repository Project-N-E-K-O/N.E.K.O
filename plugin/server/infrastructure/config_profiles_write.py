from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_paths import get_plugin_config_path
from plugin.server.infrastructure.config_profiles import (
    get_profile_config,
    get_profiles_state,
    resolve_profile_path,
)

logger = get_logger("server.infrastructure.config_profiles_write")

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

try:
    import tomli_w
except ImportError:
    tomli_w = None


_profile_update_locks: dict[str, threading.Lock] = {}
_profile_update_locks_guard = threading.Lock()


def _get_plugin_lock(plugin_id: str) -> threading.Lock:
    with _profile_update_locks_guard:
        lock = _profile_update_locks.get(plugin_id)
        if lock is None:
            lock = threading.Lock()
            _profile_update_locks[plugin_id] = lock
        return lock


def _require_toml_read_write() -> None:
    if tomllib is None or tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")


def _load_profiles_file_for_update(*, plugin_id: str, profiles_path: Path, op: str) -> dict[str, object]:
    if not profiles_path.exists():
        return {}
    if tomllib is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    try:
        with profiles_path.open("rb") as profile_file:
            data = tomllib.load(profile_file)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(
            "Plugin {}: failed to load profiles.toml from {} for {}: {}",
            plugin_id,
            profiles_path,
            op,
            str(exc),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to load profiles.toml: {str(exc)}",
        ) from exc

    if isinstance(data, dict):
        normalized_data: dict[str, object] = {}
        for key_obj, value in data.items():
            if isinstance(key_obj, str):
                normalized_data[key_obj] = value
        return normalized_data
    return {}


def _normalize_profiles_config(payload: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    profiles_cfg_obj = payload.get("config_profiles")
    profiles_cfg: dict[str, object]
    if isinstance(profiles_cfg_obj, dict):
        profiles_cfg = {str(key): value for key, value in profiles_cfg_obj.items()}
    else:
        profiles_cfg = {}

    files_map_obj = profiles_cfg.get("files")
    files_map: dict[str, str] = {}
    if isinstance(files_map_obj, dict):
        for key_obj, value in files_map_obj.items():
            if isinstance(key_obj, str) and isinstance(value, str):
                files_map[key_obj] = value
    profiles_cfg["files"] = files_map
    return profiles_cfg, files_map


def _fsync_parent_dir(path: Path) -> None:
    try:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        return
    finally:
        os.close(dir_fd)


def _atomic_dump_toml(*, target_path: Path, payload: dict[str, object], prefix: str) -> None:
    if tomli_w is None:
        raise HTTPException(status_code=500, detail="TOML library not available")

    try:
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".toml",
            prefix=prefix,
            dir=str(target_path.parent),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create temporary file for {target_path}",
        ) from exc

    temp_file_path = Path(temp_path)
    try:
        with os.fdopen(temp_fd, "wb") as temp_file:
            tomli_w.dump(payload, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_file_path, target_path)
        _fsync_parent_dir(target_path)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        try:
            if temp_file_path.exists():
                temp_file_path.unlink()
        except OSError:
            logger.debug(
                "failed to cleanup temp profile file: {}",
                str(temp_file_path),
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist {target_path.name}",
        ) from exc


def upsert_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
    config: dict[str, object],
    make_active: bool | None,
) -> dict[str, object]:
    _require_toml_read_write()
    if "plugin" in config:
        raise HTTPException(
            status_code=400,
            detail="Profile config must not define top-level 'plugin' section.",
        )
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    lock = _get_plugin_lock(plugin_id)
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        data = _load_profiles_file_for_update(
            plugin_id=plugin_id,
            profiles_path=profiles_path,
            op="upsert",
        )
        profiles_cfg, files_map = _normalize_profiles_config(data)

        raw_path = files_map.get(profile_name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = f"profiles/{profile_name}.toml"
            files_map[profile_name] = raw_path

        if make_active or ("active" not in profiles_cfg or not profiles_cfg.get("active")):
            profiles_cfg["active"] = profile_name

        data["config_profiles"] = profiles_cfg

        profile_path = resolve_profile_path(raw_path, base_dir)
        if profile_path is None:
            raise HTTPException(status_code=400, detail="Invalid profile path")

        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create profile directory: {profile_path.parent}",
            ) from exc

        _atomic_dump_toml(
            target_path=profile_path,
            payload=config,
            prefix=".profile_",
        )
        _atomic_dump_toml(
            target_path=profiles_path,
            payload=data,
            prefix=".profiles_",
        )

        return get_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
            config_path=config_path,
        )


def delete_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
) -> dict[str, object]:
    _require_toml_read_write()
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    lock = _get_plugin_lock(plugin_id)
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        profiles_path = config_path.parent / "profiles.toml"
        if not profiles_path.exists():
            return {
                "plugin_id": plugin_id,
                "profile": profile_name,
                "removed": False,
            }

        data = _load_profiles_file_for_update(
            plugin_id=plugin_id,
            profiles_path=profiles_path,
            op="delete",
        )
        profiles_cfg, files_map = _normalize_profiles_config(data)

        removed = False
        if profile_name in files_map:
            del files_map[profile_name]
            removed = True

        active_obj = profiles_cfg.get("active")
        if isinstance(active_obj, str) and active_obj == profile_name:
            profiles_cfg.pop("active", None)

        data["config_profiles"] = profiles_cfg
        _atomic_dump_toml(
            target_path=profiles_path,
            payload=data,
            prefix=".profiles_",
        )

        return {
            "plugin_id": plugin_id,
            "profile": profile_name,
            "removed": removed,
        }


def set_active_profile(
    *,
    plugin_id: str,
    profile_name: str,
) -> dict[str, object]:
    _require_toml_read_write()
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    lock = _get_plugin_lock(plugin_id)
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        profiles_path = config_path.parent / "profiles.toml"
        if not profiles_path.exists():
            raise HTTPException(status_code=404, detail="profiles.toml not found")

        data = _load_profiles_file_for_update(
            plugin_id=plugin_id,
            profiles_path=profiles_path,
            op="set active",
        )
        profiles_cfg, files_map = _normalize_profiles_config(data)
        if profile_name not in files_map:
            raise HTTPException(status_code=404, detail="profile not found in config_profiles.files")

        profiles_cfg["active"] = profile_name
        data["config_profiles"] = profiles_cfg
        _atomic_dump_toml(
            target_path=profiles_path,
            payload=data,
            prefix=".profiles_",
        )

        return get_profiles_state(
            plugin_id=plugin_id,
            config_path=config_path,
        )
