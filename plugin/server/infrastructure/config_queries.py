from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_protected import validate_protected_fields_unchanged
from plugin.server.infrastructure.config_resolver import resolve_plugin_config
from plugin.server.infrastructure.config_toml import (
    parse_toml_text,
    render_toml_text,
)

logger = get_logger("server.infrastructure.config_queries")


def load_plugin_base_config(plugin_id: str) -> dict[str, object]:
    resolved = resolve_plugin_config(
        plugin_id,
        include_effective_config=False,
        validate_schema=True,
    )
    return {
        "plugin_id": plugin_id,
        "config": resolved["base_config"],
        "last_modified": resolved["last_modified"],
        "config_path": resolved["config_path"],
        "profiles_state": resolved["profiles_state"],
        "warnings": resolved["warnings"],
    }


def load_plugin_config(plugin_id: str, *, validate: bool = True) -> dict[str, object]:
    resolved = resolve_plugin_config(
        plugin_id,
        include_effective_config=True,
        validate_schema=validate,
    )
    validation_errors = resolved.get("schema_validation_errors")
    if isinstance(validation_errors, list) and validation_errors:
        logger.warning(
            "Plugin {}: config schema validation warnings: {}",
            plugin_id,
            validation_errors,
        )
    return {
        "plugin_id": plugin_id,
        "config": resolved["effective_config"],
        "base_config": resolved["base_config"],
        "last_modified": resolved["last_modified"],
        "config_path": resolved["config_path"],
        "profiles_state": resolved["profiles_state"],
        "warnings": resolved["warnings"],
    }


def load_plugin_config_toml(plugin_id: str) -> dict[str, object]:
    resolved = resolve_plugin_config(
        plugin_id,
        include_effective_config=False,
        validate_schema=True,
    )
    config_path = Path(str(resolved["config_path"]))
    try:
        toml_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(exc)}",
        ) from exc

    return {
        "plugin_id": plugin_id,
        "toml": toml_text,
        "last_modified": resolved["last_modified"],
        "config_path": resolved["config_path"],
        "profiles_state": resolved["profiles_state"],
        "warnings": resolved["warnings"],
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
