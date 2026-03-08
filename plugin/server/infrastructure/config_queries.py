from __future__ import annotations

import os
from datetime import datetime

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_paths import get_plugin_config_path
from plugin.server.infrastructure.config_profiles import apply_user_config_profiles
from plugin.server.infrastructure.config_protected import validate_protected_fields_unchanged
from plugin.server.infrastructure.config_toml import (
    load_toml_from_file,
    parse_toml_text,
    render_toml_text,
)

logger = get_logger("server.infrastructure.config_queries")

_schema_validation_enabled = os.getenv("NEKO_CONFIG_SCHEMA_VALIDATION", "true").lower() in {
    "true",
    "1",
    "yes",
    "on",
}


def _validate_config_schema(config_data: dict[str, object], plugin_id: str) -> list[dict[str, object]] | None:
    try:
        from plugin.server.config_schema import ConfigValidationError, validate_plugin_config
    except ImportError:
        logger.debug(
            "Plugin {}: config_schema module not available, skip validation",
            plugin_id,
        )
        return None

    try:
        validate_plugin_config(config_data)
        return None
    except ConfigValidationError as exc:
        if isinstance(exc.details, list):
            normalized: list[dict[str, object]] = []
            for item in exc.details:
                if isinstance(item, dict):
                    normalized.append({str(key): value for key, value in item.items()})
            return normalized
        return [{"msg": exc.message, "field": exc.field}]


def load_plugin_base_config(plugin_id: str) -> dict[str, object]:
    config_path = get_plugin_config_path(plugin_id)
    config_data = load_toml_from_file(config_path)
    stat = config_path.stat()
    return {
        "plugin_id": plugin_id,
        "config": config_data,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "config_path": str(config_path),
    }


def load_plugin_config(plugin_id: str, *, validate: bool = True) -> dict[str, object]:
    config_path = get_plugin_config_path(plugin_id)
    base_config = load_toml_from_file(config_path)

    if validate and _schema_validation_enabled:
        validation_errors = _validate_config_schema(base_config, plugin_id)
        if validation_errors:
            logger.warning(
                "Plugin {}: config schema validation warnings: {}",
                plugin_id,
                validation_errors,
            )

    merged_config = apply_user_config_profiles(
        plugin_id=plugin_id,
        base_config=base_config,
        config_path=config_path,
    )
    stat = config_path.stat()
    return {
        "plugin_id": plugin_id,
        "config": merged_config,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "config_path": str(config_path),
    }


def load_plugin_config_toml(plugin_id: str) -> dict[str, object]:
    config_path = get_plugin_config_path(plugin_id)
    try:
        toml_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(exc)}",
        ) from exc

    stat = config_path.stat()
    return {
        "plugin_id": plugin_id,
        "toml": toml_text,
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "config_path": str(config_path),
    }


def parse_toml_to_config(plugin_id: str, toml_text: str) -> dict[str, object]:
    if toml_text is None:
        raise HTTPException(status_code=400, detail="toml_text cannot be None")

    parsed = parse_toml_text(toml_text, context=f"{plugin_id}.toml")
    current_payload = load_plugin_config(plugin_id)
    current_config_obj = current_payload.get("config")
    if not isinstance(current_config_obj, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Plugin '{plugin_id}' config payload has invalid shape",
        )

    validate_protected_fields_unchanged(
        current_config=current_config_obj,
        new_config=parsed,
    )
    return {"plugin_id": plugin_id, "config": parsed}


def render_config_to_toml(plugin_id: str, config: dict[str, object]) -> dict[str, object]:
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    current_payload = load_plugin_config(plugin_id)
    current_config_obj = current_payload.get("config")
    if not isinstance(current_config_obj, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Plugin '{plugin_id}' config payload has invalid shape",
        )

    validate_protected_fields_unchanged(
        current_config=current_config_obj,
        new_config=config,
    )
    return {
        "plugin_id": plugin_id,
        "toml": render_toml_text(config),
    }
