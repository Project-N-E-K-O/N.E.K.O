from __future__ import annotations

from collections.abc import Mapping

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


def replace_plugin_config(plugin_id: str, new_config: dict[str, object]) -> dict[str, object]:
    normalized_new_config = _ensure_string_key_mapping(new_config, field="config")
    lock = get_plugin_update_lock(plugin_id)
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        with config_path.open("r+b") as file_obj:
            with file_lock(file_obj):
                current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                validate_protected_fields_unchanged(
                    current_config=current_config,
                    new_config=normalized_new_config,
                )
                completed_config = _fill_plugin_protected_fields(
                    current_config=current_config,
                    incoming_config=normalized_new_config,
                )
                payload = dump_toml_bytes(completed_config)
                atomic_write_bytes(
                    target=config_path,
                    payload=payload,
                    prefix=".plugin_config_",
                )

    updated = load_plugin_config(plugin_id)
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
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        with config_path.open("r+b") as file_obj:
            with file_lock(file_obj):
                current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                merged = deep_merge(current_config, normalized_updates)
                payload = dump_toml_bytes(merged)
                atomic_write_bytes(
                    target=config_path,
                    payload=payload,
                    prefix=".plugin_config_",
                )

    updated = load_plugin_config(plugin_id)
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
    with lock:
        config_path = get_plugin_config_path(plugin_id)
        with config_path.open("r+b") as file_obj:
            with file_lock(file_obj):
                current_config = load_toml_from_stream(file_obj, context=f"{plugin_id}.plugin.toml")
                validate_protected_fields_unchanged(
                    current_config=current_config,
                    new_config=parsed_new,
                )
                atomic_write_text(
                    target=config_path,
                    text=toml_text,
                    prefix=".plugin_config_",
                )

    updated = load_plugin_config(plugin_id)
    logger.info("Updated TOML config for plugin {}", plugin_id)
    return {
        "success": True,
        "plugin_id": plugin_id,
        "config": updated["config"],
        "requires_reload": True,
        "message": "Config updated successfully",
    }
