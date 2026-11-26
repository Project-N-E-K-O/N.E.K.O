from __future__ import annotations

from collections.abc import Mapping
import os
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_merge import deep_merge, strip_reserved_sections
from plugin.server.infrastructure.config_paths import ensure_plugin_runtime_config, get_plugin_config_path
from plugin.server.infrastructure.config_profiles import (
    _resolve_active_profile_name,
    _resolve_profiles_cfg,
    get_profile_config,
    get_profiles_state,
    resolve_profile_path,
)
from plugin.server.infrastructure.config_locking import get_plugin_update_lock, plugin_config_file_lock
from plugin.server.infrastructure.config_storage import ConfigCommitCancelled, ConfigCommitGuard

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


def _get_plugin_lock(plugin_id: str) -> threading.RLock:
    """Return the shared per-plugin lock used by all config reads and writes."""
    return get_plugin_update_lock(plugin_id)


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


def _write_temp_toml(*, target_path: Path, payload: dict[str, object], prefix: str) -> Path:
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
    return temp_file_path


def _cleanup_temp_files(*paths: Path) -> None:
    for temp_file_path in paths:
        try:
            if temp_file_path.exists():
                temp_file_path.unlink()
        except OSError:
            logger.debug(
                "failed to cleanup temp profile file: {}",
                str(temp_file_path),
            )


def _atomic_dump_toml(
    *,
    target_path: Path,
    payload: dict[str, object],
    prefix: str,
    commit_guard: ConfigCommitGuard | None = None,
) -> None:
    temp_file_path = _write_temp_toml(
        target_path=target_path,
        payload=payload,
        prefix=prefix,
    )
    try:
        if commit_guard is None:
            os.replace(temp_file_path, target_path)
        else:
            commit_guard.commit(lambda: os.replace(temp_file_path, target_path))
        _fsync_parent_dir(target_path)
    except ConfigCommitCancelled:
        _cleanup_temp_files(temp_file_path)
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        _cleanup_temp_files(temp_file_path)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist {target_path.name}",
        ) from exc


def _write_temp_bytes(*, target_path: Path, payload: bytes, prefix: str) -> Path:
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
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        _cleanup_temp_files(temp_file_path)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist {target_path.name}",
        ) from exc
    return temp_file_path


def _atomic_dump_toml_pair(
    *,
    first_path: Path,
    first_payload: dict[str, object],
    first_prefix: str,
    second_path: Path,
    second_payload: dict[str, object],
    second_prefix: str,
    commit_guard: ConfigCommitGuard,
) -> None:
    first_temp = _write_temp_toml(
        target_path=first_path,
        payload=first_payload,
        prefix=first_prefix,
    )
    try:
        second_temp = _write_temp_toml(
            target_path=second_path,
            payload=second_payload,
            prefix=second_prefix,
        )
    except BaseException:
        _cleanup_temp_files(first_temp)
        raise

    try:
        had_first = first_path.exists()
        old_first_bytes = first_path.read_bytes() if had_first else None
    except OSError as exc:
        _cleanup_temp_files(first_temp, second_temp)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to snapshot existing {first_path.name}",
        ) from exc

    def _commit_pair() -> None:
        os.replace(first_temp, first_path)
        try:
            os.replace(second_temp, second_path)
        except BaseException:
            try:
                if had_first and old_first_bytes is not None:
                    rollback_temp = _write_temp_bytes(
                        target_path=first_path,
                        payload=old_first_bytes,
                        prefix=".rollback_",
                    )
                    os.replace(rollback_temp, first_path)
                else:
                    first_path.unlink(missing_ok=True)
            except BaseException as rollback_exc:
                logger.warning(
                    "Failed to rollback {} after second-file replace error: {}",
                    first_path,
                    str(rollback_exc),
                )
            raise

    try:
        commit_guard.commit(_commit_pair)
        _fsync_parent_dir(first_path)
        _fsync_parent_dir(second_path)
    except ConfigCommitCancelled:
        _cleanup_temp_files(first_temp, second_temp)
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        _cleanup_temp_files(first_temp, second_temp)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist {first_path.name} / {second_path.name}",
        ) from exc


def _validate_profile_name(profile_name: object) -> str:
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise HTTPException(status_code=400, detail="profile_name is required")
    normalized = profile_name.strip()
    if "/" in normalized or "\\" in normalized or "\x00" in normalized or normalized in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid profile_name")
    return normalized


def _profile_affects_active(
    *,
    base_dir: Path,
    files_map: Mapping[str, str],
    profile_name: str,
    profile_path: Path,
    active_name: str | None,
) -> bool:
    if active_name is None:
        return False
    if active_name == profile_name:
        return True
    active_raw = files_map.get(active_name)
    if not isinstance(active_raw, str) or not active_raw.strip():
        return False
    active_path = resolve_profile_path(active_raw, base_dir)
    return active_path is not None and active_path == profile_path


def upsert_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
    config: dict[str, object],
    make_active: bool | None,
    commit_guard: ConfigCommitGuard | None = None,
) -> dict[str, object]:
    _require_toml_read_write()
    if "plugin" in config:
        raise HTTPException(
            status_code=400,
            detail="Profile config must not define top-level 'plugin' section.",
        )
    # Reserved tables are read from plugin.toml only and are ignored in a
    # profile; drop them so a save does not keep dead settings around.
    config = strip_reserved_sections(config)
    profile_name = _validate_profile_name(profile_name)

    lock = _get_plugin_lock(plugin_id)
    config_path = get_plugin_config_path(plugin_id)
    with lock, plugin_config_file_lock(ensure_plugin_runtime_config(plugin_id, manifest_path=config_path)):
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        data = _load_profiles_file_for_update(
            plugin_id=plugin_id,
            profiles_path=profiles_path,
            op="upsert",
        )
        profiles_cfg, files_map = _normalize_profiles_config(data)
        previous_active = _resolve_active_profile_name(plugin_id, profiles_cfg)

        raw_path = files_map.get(profile_name)
        synthesized_default_path = False
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = f"profiles/{profile_name}.toml"
            files_map[profile_name] = raw_path
            synthesized_default_path = True

        if make_active or ("active" not in profiles_cfg or not profiles_cfg.get("active")):
            profiles_cfg["active"] = profile_name

        data["config_profiles"] = profiles_cfg

        profile_path = resolve_profile_path(raw_path, base_dir)
        if profile_path is None:
            raise HTTPException(status_code=400, detail="Invalid profile path")
        if synthesized_default_path and profile_path.parent != (base_dir / "profiles").resolve():
            raise HTTPException(status_code=400, detail="Invalid profile path")

        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create profile directory: {profile_path.parent}",
            ) from exc

        if commit_guard is None:
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
        else:
            _atomic_dump_toml_pair(
                first_path=profile_path,
                first_payload=config,
                first_prefix=".profile_",
                second_path=profiles_path,
                second_payload=data,
                second_prefix=".profiles_",
                commit_guard=commit_guard,
            )

        active_name = _resolve_active_profile_name(plugin_id, profiles_cfg)
        affected_active = (previous_active != active_name) or _profile_affects_active(
            base_dir=base_dir,
            files_map=files_map,
            profile_name=profile_name,
            profile_path=profile_path,
            active_name=active_name,
        )
        result = get_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
            config_path=config_path,
        )
        result["active_profile"] = active_name
        result["affected_active"] = affected_active
        return result


def update_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
    updates: dict[str, object],
) -> dict[str, object]:
    _require_toml_read_write()
    if "plugin" in updates:
        raise HTTPException(
            status_code=400,
            detail="Profile config must not define top-level 'plugin' section.",
        )
    # Reserved tables are read from plugin.toml only and are ignored in a
    # profile; drop them so a save does not keep dead settings around.
    updates = strip_reserved_sections(updates)
    profile_name = _validate_profile_name(profile_name)

    lock = _get_plugin_lock(plugin_id)
    config_path = get_plugin_config_path(plugin_id)
    with lock, plugin_config_file_lock(ensure_plugin_runtime_config(plugin_id, manifest_path=config_path)):
        base_dir = config_path.parent
        profiles_path = base_dir / "profiles.toml"

        if profiles_path.exists():
            data = _load_profiles_file_for_update(
                plugin_id=plugin_id,
                profiles_path=profiles_path,
                op="update",
            )
        else:
            data = {}

        if not isinstance(data.get("config_profiles"), dict):
            seeded_cfg = _resolve_profiles_cfg(
                plugin_id=plugin_id,
                config_path=config_path,
            )
            if isinstance(seeded_cfg, dict):
                data["config_profiles"] = dict(seeded_cfg)

        profiles_cfg, files_map = _normalize_profiles_config(data)
        raw_path = files_map.get(profile_name)
        synthesized_default_path = False
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = f"profiles/{profile_name}.toml"
            synthesized_default_path = True

        profile_path = resolve_profile_path(raw_path, base_dir)
        if profile_path is None:
            raise HTTPException(status_code=400, detail="Invalid profile path")
        if synthesized_default_path:
            if profile_path.parent != (base_dir / "profiles").resolve():
                raise HTTPException(status_code=400, detail="Invalid profile path")
            if not profile_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Profile '{profile_name}' not found",
                )
            files_map[profile_name] = raw_path

        data["config_profiles"] = profiles_cfg

        existing_profile: dict[str, object] = {}
        if profile_path.exists():
            assert tomllib is not None
            try:
                with profile_path.open("rb") as profile_file:
                    loaded = tomllib.load(profile_file)
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to parse profile '{profile_name}'",
                ) from exc
            except (OSError, RuntimeError) as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to read profile '{profile_name}'",
                ) from exc
            if isinstance(loaded, dict):
                existing_profile = {
                    k: v for k, v in loaded.items() if isinstance(k, str)
                }

        merged = deep_merge(existing_profile, updates)
        if "plugin" in merged:
            raise HTTPException(
                status_code=400,
                detail="Profile config must not define top-level 'plugin' section.",
            )

        try:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create profile directory: {profile_path.parent}",
            ) from exc

        _atomic_dump_toml(
            target_path=profile_path,
            payload=merged,
            prefix=".profile_",
        )
        _atomic_dump_toml(
            target_path=profiles_path,
            payload=data,
            prefix=".profiles_",
        )

        active_name = _resolve_active_profile_name(plugin_id, profiles_cfg)
        affected_active = _profile_affects_active(
            base_dir=base_dir,
            files_map=files_map,
            profile_name=profile_name,
            profile_path=profile_path,
            active_name=active_name,
        )
        return {
            "success": True,
            "plugin_id": plugin_id,
            "profile": profile_name,
            "active_profile": active_name,
            "affected_active": affected_active,
            "config": merged,
            "requires_reload": True,
            "message": "Config updated successfully",
        }


def delete_profile_config(
    *,
    plugin_id: str,
    profile_name: str,
    commit_guard: ConfigCommitGuard | None = None,
) -> dict[str, object]:
    _require_toml_read_write()
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    lock = _get_plugin_lock(plugin_id)
    config_path = get_plugin_config_path(plugin_id)
    with lock, plugin_config_file_lock(ensure_plugin_runtime_config(plugin_id, manifest_path=config_path)):
        profiles_path = config_path.parent / "profiles.toml"
        if not profiles_path.exists():
            return {
                "plugin_id": plugin_id,
                "profile": profile_name,
                "removed": False,
                "active_profile": None,
                "affected_active": False,
            }

        data = _load_profiles_file_for_update(
            plugin_id=plugin_id,
            profiles_path=profiles_path,
            op="delete",
        )
        profiles_cfg, files_map = _normalize_profiles_config(data)
        previous_active = _resolve_active_profile_name(plugin_id, profiles_cfg)

        removed = False
        if profile_name in files_map:
            del files_map[profile_name]
            removed = True

        active_obj = profiles_cfg.get("active")
        if isinstance(active_obj, str) and active_obj == profile_name:
            profiles_cfg.pop("active", None)

        new_active = _resolve_active_profile_name(plugin_id, profiles_cfg)
        affected_active = bool(
            (removed and previous_active == profile_name)
            or (previous_active != new_active)
        )

        data["config_profiles"] = profiles_cfg
        if commit_guard is None:
            _atomic_dump_toml(
                target_path=profiles_path,
                payload=data,
                prefix=".profiles_",
            )
        else:
            _atomic_dump_toml(
                target_path=profiles_path,
                payload=data,
                prefix=".profiles_",
                commit_guard=commit_guard,
            )

        return {
            "plugin_id": plugin_id,
            "profile": profile_name,
            "removed": removed,
            "active_profile": new_active,
            "affected_active": affected_active,
        }


def set_active_profile(
    *,
    plugin_id: str,
    profile_name: str,
    commit_guard: ConfigCommitGuard | None = None,
) -> dict[str, object]:
    _require_toml_read_write()
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    lock = _get_plugin_lock(plugin_id)
    config_path = get_plugin_config_path(plugin_id)
    with lock, plugin_config_file_lock(ensure_plugin_runtime_config(plugin_id, manifest_path=config_path)):
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
        if commit_guard is None:
            _atomic_dump_toml(
                target_path=profiles_path,
                payload=data,
                prefix=".profiles_",
            )
        else:
            _atomic_dump_toml(
                target_path=profiles_path,
                payload=data,
                prefix=".profiles_",
                commit_guard=commit_guard,
            )

        return get_profiles_state(
            plugin_id=plugin_id,
            config_path=config_path,
        )
