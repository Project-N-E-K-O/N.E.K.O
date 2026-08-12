from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Mapping

from plugin.config.plugin_toml_semantics import PluginConfigWarning, collect_plugin_toml_semantic_warnings
from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_merge import deep_merge
from plugin.server.infrastructure.config_paths import ensure_plugin_runtime_config, get_plugin_manifest_path
from plugin.server.infrastructure.config_profiles import apply_user_config_profiles, get_profiles_state
from plugin.server.infrastructure.config_toml import load_toml_from_file

logger = get_logger("server.infrastructure.config_resolver")

_SCHEMA_VALIDATION_ENABLED = os.getenv("NEKO_CONFIG_SCHEMA_VALIDATION", "true").lower() in {
    "true",
    "1",
    "yes",
    "on",
}


def _validate_config_schema(config_data: dict[str, object], plugin_id: str) -> list[dict[str, object]]:
    try:
        from plugin.server.config_schema import ConfigValidationError, validate_plugin_config
    except ImportError:
        logger.debug(
            "Plugin {}: config_schema module not available, skip validation",
            plugin_id,
        )
        return []

    try:
        validate_plugin_config(config_data)
        return []
    except ConfigValidationError as exc:
        if isinstance(exc.details, list):
            normalized: list[dict[str, object]] = []
            for item in exc.details:
                if isinstance(item, dict):
                    normalized.append({str(key): value for key, value in item.items()})
            return normalized
        return [{"msg": exc.message, "field": exc.field}]


def _schema_warning_items(validation_errors: list[dict[str, object]]) -> list[PluginConfigWarning]:
    warnings: list[PluginConfigWarning] = []
    for item in validation_errors:
        msg = item.get("msg")
        field = item.get("field") or item.get("loc")
        if isinstance(msg, str) and msg:
            warnings.append(
                {
                    "code": "PLUGIN_SCHEMA_VALIDATION",
                    "field": field if isinstance(field, str) and field else None,
                    "message": msg,
                    "severity": "warning",
                    "source": "schema",
                }
            )
    return warnings


def _resolve_plugin_config_core(
    plugin_id: str,
    *,
    config_path: Path,
    manifest_path: Path,
    manifest_config: dict[str, object],
    base_config: dict[str, object],
    include_effective_config: bool,
    validate_schema: bool,
) -> dict[str, object]:
    manifest_plugin = manifest_config.get("plugin")
    effective_config = deep_merge(manifest_config, base_config)
    if isinstance(manifest_plugin, Mapping):
        effective_config["plugin"] = dict(manifest_plugin)
    if include_effective_config:
        effective_config = apply_user_config_profiles(
            plugin_id=plugin_id,
            base_config=effective_config,
            config_path=manifest_path,
        )

    if isinstance(manifest_plugin, Mapping):
        effective_config = dict(effective_config)
        effective_config["plugin"] = dict(manifest_plugin)

    semantic_warnings = collect_plugin_toml_semantic_warnings(effective_config, toml_path=manifest_path)
    schema_validation_errors = (
        _validate_config_schema(effective_config, plugin_id)
        if validate_schema and _SCHEMA_VALIDATION_ENABLED
        else []
    )
    schema_warnings = _schema_warning_items(schema_validation_errors)

    profiles_state = get_profiles_state(
        plugin_id=plugin_id,
        config_path=manifest_path,
    )
    stat = config_path.stat()

    return {
        "plugin_id": plugin_id,
        "config_path": str(config_path),
        "manifest_path": str(manifest_path),
        "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "base_config": base_config,
        "effective_config": effective_config,
        "profiles_state": profiles_state,
        "warnings": [*schema_warnings, *semantic_warnings],
        "schema_validation_errors": schema_validation_errors,
    }


def resolve_plugin_config_from_path(
    plugin_id: str,
    *,
    config_path: Path,
    base_config: dict[str, object] | None = None,
    include_effective_config: bool = True,
    validate_schema: bool = True,
) -> dict[str, object]:
    manifest_path = config_path.resolve(strict=False)
    manifest_config = base_config if isinstance(base_config, dict) else load_toml_from_file(manifest_path)
    runtime_config_path = ensure_plugin_runtime_config(plugin_id, manifest_path=manifest_path)
    runtime_config = load_toml_from_file(runtime_config_path)
    return _resolve_plugin_config_core(
        plugin_id,
        config_path=runtime_config_path,
        manifest_path=manifest_path,
        manifest_config=manifest_config,
        base_config=runtime_config,
        include_effective_config=include_effective_config,
        validate_schema=validate_schema,
    )


def resolve_plugin_config(
    plugin_id: str,
    *,
    include_effective_config: bool = True,
    validate_schema: bool = True,
) -> dict[str, object]:
    manifest_path = get_plugin_manifest_path(plugin_id)
    return resolve_plugin_config_from_path(
        plugin_id,
        config_path=manifest_path,
        include_effective_config=include_effective_config,
        validate_schema=validate_schema,
    )


__all__ = ["resolve_plugin_config", "resolve_plugin_config_from_path"]
