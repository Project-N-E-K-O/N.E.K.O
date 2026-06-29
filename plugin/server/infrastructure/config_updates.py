from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_locking import file_lock, get_plugin_update_lock
from plugin.server.infrastructure.config_merge import deep_merge
from plugin.server.infrastructure.config_paths import get_plugin_config_path
from plugin.server.infrastructure.config_protected import validate_protected_fields_unchanged
from plugin.server.infrastructure.config_queries import load_plugin_config
from plugin.server.infrastructure.config_storage import atomic_write_bytes, atomic_write_text
from plugin.server.infrastructure.config_toml import (
    dump_toml_bytes,
    load_toml_from_stream,
    parse_toml_text,
)

logger = get_logger("server.infrastructure.config_updates")

_CONFIG_UPDATE_RUNTIME_ERRORS = (OSError, RuntimeError, ValueError, TypeError)


@contextmanager
def _config_write_lock(config_path: Path) -> Iterator[None]:
    lock_path = config_path.with_name(f"{config_path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        with file_lock(lock_file):
            yield


def _ensure_string_key_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=400, detail=f"{field} must be an object")
    normalized: dict[str, object] = {}
    for key_obj, item in value.items():
        if not isinstance(key_obj, str):
            raise HTTPException(status_code=400, detail=f"{field} keys must be strings")
        normalized[key_obj] = item
    return normalized

def _fill_plugin_protected_fields(
    *,
    current_config: Mapping[str, object],
    incoming_config: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = dict(incoming_config)
    plugin_section_obj = result.get("plugin")
    if isinstance(plugin_section_obj, Mapping):
        plugin_section: dict[str, object] = {}
        for key_obj, value in plugin_section_obj.items():
            if isinstance(key_obj, str):
                plugin_section[key_obj] = value
    else:
        plugin_section = {}

    current_plugin_obj = current_config.get("plugin")
    current_plugin: Mapping[str, object]
    if isinstance(current_plugin_obj, Mapping):
        current_plugin = current_plugin_obj
    else:
        current_plugin = {}

    if plugin_section.get("id") is None:
        existing_id = current_plugin.get("id")
        if existing_id is not None:
            plugin_section["id"] = existing_id
    if plugin_section.get("entry") is None:
        existing_entry = current_plugin.get("entry")
        if existing_entry is not None:
            plugin_section["entry"] = existing_entry

    result["plugin"] = plugin_section
    return result


def _probe_path_value(probe: object) -> object:
    try:
        if callable(probe):
            return probe()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return f"unknown({type(exc).__name__}: {exc})"
    return "unknown"


def _describe_config_path(config_path: Path) -> str:
    parent = config_path.parent
    return (
        f"exists={_probe_path_value(config_path.exists)}, "
        f"is_file={_probe_path_value(config_path.is_file)}, "
        f"file_writable={_probe_path_value(lambda: os.access(config_path, os.W_OK))}, "
        f"parent={parent}, "
        f"parent_exists={_probe_path_value(parent.exists)}, "
        f"parent_writable={_probe_path_value(lambda: os.access(parent, os.W_OK))}"
    )


def _log_config_update_start(*, operation: str, plugin_id: str, config_path: Path) -> None:
    logger.info(
        "Plugin config {} started: plugin_id={}, config_path={}, path_state={}",
        operation,
        plugin_id,
        config_path,
        _describe_config_path(config_path),
    )


def _log_config_path_resolution_failure(
    *,
    operation: str,
    plugin_id: str,
    exc: BaseException,
) -> None:
    logger.exception(
        "Plugin config {} failed: plugin_id={}, stage=resolve_path, err_type={}, err={}",
        operation,
        plugin_id,
        type(exc).__name__,
        str(exc),
    )


def _log_config_update_success(*, operation: str, plugin_id: str, config_path: Path) -> None:
    logger.info(
        "Plugin config {} persisted: plugin_id={}, config_path={}, path_state={}",
        operation,
        plugin_id,
        config_path,
        _describe_config_path(config_path),
    )


def _log_config_update_failure(
    *,
    operation: str,
    plugin_id: str,
    config_path: Path,
    stage: str,
    exc: BaseException,
) -> None:
    logger.exception(
        "Plugin config {} failed: plugin_id={}, config_path={}, stage={}, err_type={}, err={}, path_state={}",
        operation,
        plugin_id,
        config_path,
        stage,
        type(exc).__name__,
        str(exc),
        _describe_config_path(config_path),
    )


def _resolve_config_path_for_update(*, operation: str, plugin_id: str) -> Path:
    try:
        return get_plugin_config_path(plugin_id)
    except HTTPException as exc:
        _log_config_path_resolution_failure(operation=operation, plugin_id=plugin_id, exc=exc)
        raise
    except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
        _log_config_path_resolution_failure(operation=operation, plugin_id=plugin_id, exc=exc)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to resolve plugin config path for {plugin_id} "
                f"while {operation}: {type(exc).__name__}: {exc}"
            ),
        ) from exc


def replace_plugin_config(plugin_id: str, new_config: dict[str, object]) -> dict[str, object]:
    normalized_new_config = _ensure_string_key_mapping(new_config, field="config")
    lock = get_plugin_update_lock(plugin_id)
    operation = "replace"
    with lock:
        config_path = _resolve_config_path_for_update(operation=operation, plugin_id=plugin_id)
        stage = "open"
        _log_config_update_start(operation=operation, plugin_id=plugin_id, config_path=config_path)
        try:
            with _config_write_lock(config_path):
                with config_path.open("r+b") as file_obj:
                    stage = "lock"
                    with file_lock(file_obj):
                        stage = "read"
                        current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                        stage = "validate"
                        validate_protected_fields_unchanged(
                            current_config=current_config,
                            new_config=normalized_new_config,
                        )
                        stage = "fill_protected_fields"
                        completed_config = _fill_plugin_protected_fields(
                            current_config=current_config,
                            incoming_config=normalized_new_config,
                        )
                        stage = "serialize"
                        payload = dump_toml_bytes(completed_config)
                stage = "atomic_write"
                atomic_write_bytes(
                    target=config_path,
                    payload=payload,
                    prefix=".plugin_config_",
                )
            _log_config_update_success(operation=operation, plugin_id=plugin_id, config_path=config_path)
        except HTTPException as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise
        except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to replace plugin config at {config_path} "
                    f"while {stage}: {type(exc).__name__}: {exc}"
                ),
            ) from exc

    try:
        updated = load_plugin_config(plugin_id)
    except HTTPException as exc:
        _log_config_update_failure(
            operation="replace",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise
    except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
        _log_config_update_failure(
            operation="replace",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to reload plugin config after replace at {config_path}: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc
    logger.info("Replaced config for plugin {}", plugin_id)
    return {
        "success": True,
        "plugin_id": plugin_id,
        "config": updated["config"],
        "requires_reload": True,
        "message": "Config updated successfully",
    }


def update_plugin_config(plugin_id: str, updates: dict[str, object]) -> dict[str, object]:
    normalized_updates = _ensure_string_key_mapping(updates, field="updates")
    lock = get_plugin_update_lock(plugin_id)
    operation = "update"
    with lock:
        config_path = _resolve_config_path_for_update(operation=operation, plugin_id=plugin_id)
        stage = "open"
        _log_config_update_start(operation=operation, plugin_id=plugin_id, config_path=config_path)
        try:
            with _config_write_lock(config_path):
                with config_path.open("r+b") as file_obj:
                    stage = "lock"
                    with file_lock(file_obj):
                        stage = "read"
                        current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                        stage = "merge"
                        merged = deep_merge(current_config, normalized_updates)
                        stage = "validate"
                        validate_protected_fields_unchanged(
                            current_config=current_config,
                            new_config=merged,
                        )
                        stage = "serialize"
                        payload = dump_toml_bytes(merged)
                stage = "atomic_write"
                atomic_write_bytes(
                    target=config_path,
                    payload=payload,
                    prefix=".plugin_config_",
                )
            _log_config_update_success(operation=operation, plugin_id=plugin_id, config_path=config_path)
        except HTTPException as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise
        except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to update plugin config at {config_path} "
                    f"while {stage}: {type(exc).__name__}: {exc}"
                ),
            ) from exc

    try:
        updated = load_plugin_config(plugin_id)
    except HTTPException as exc:
        _log_config_update_failure(
            operation="update",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise
    except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
        _log_config_update_failure(
            operation="update",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to reload plugin config after update at {config_path}: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc
    logger.info("Updated config for plugin {}", plugin_id)
    return {
        "success": True,
        "plugin_id": plugin_id,
        "config": updated["config"],
        "requires_reload": True,
        "message": "Config updated successfully",
    }


def update_plugin_config_toml(plugin_id: str, toml_text: str) -> dict[str, object]:
    if toml_text is None:
        raise HTTPException(status_code=400, detail="toml_text cannot be None")

    parsed_new = parse_toml_text(toml_text, context=f"{plugin_id}.plugin.toml")
    lock = get_plugin_update_lock(plugin_id)
    operation = "toml_update"
    with lock:
        config_path = _resolve_config_path_for_update(operation=operation, plugin_id=plugin_id)
        stage = "open"
        _log_config_update_start(operation=operation, plugin_id=plugin_id, config_path=config_path)
        try:
            with _config_write_lock(config_path):
                with config_path.open("r+b") as file_obj:
                    stage = "lock"
                    with file_lock(file_obj):
                        stage = "read"
                        current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                        stage = "validate"
                        validate_protected_fields_unchanged(
                            current_config=current_config,
                            new_config=parsed_new,
                        )
                stage = "atomic_write"
                atomic_write_text(
                    target=config_path,
                    text=toml_text,
                    prefix=".plugin_config_",
                )
            _log_config_update_success(operation=operation, plugin_id=plugin_id, config_path=config_path)
        except HTTPException as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise
        except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
            _log_config_update_failure(
                operation=operation,
                plugin_id=plugin_id,
                config_path=config_path,
                stage=stage,
                exc=exc,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to update plugin TOML config at {config_path} "
                    f"while {stage}: {type(exc).__name__}: {exc}"
                ),
            ) from exc

    try:
        updated = load_plugin_config(plugin_id)
    except HTTPException as exc:
        _log_config_update_failure(
            operation="toml_update",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise
    except _CONFIG_UPDATE_RUNTIME_ERRORS as exc:
        _log_config_update_failure(
            operation="toml_update",
            plugin_id=plugin_id,
            config_path=config_path,
            stage="reload",
            exc=exc,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to reload plugin config after TOML update at {config_path}: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc
    logger.info("Updated TOML config for plugin {}", plugin_id)
    return {
        "success": True,
        "plugin_id": plugin_id,
        "config": updated["config"],
        "requires_reload": True,
        "message": "Config updated successfully",
    }
