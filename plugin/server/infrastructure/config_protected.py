from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException


def _get_plugin_field(config: Mapping[str, object], key: str) -> object:
    plugin_section = config.get("plugin")
    if not isinstance(plugin_section, Mapping):
        return None
    return plugin_section.get(key)


def validate_protected_fields_unchanged(
    *,
    current_config: Mapping[str, object],
    new_config: Mapping[str, object],
) -> None:
    current_id = _get_plugin_field(current_config, "id")
    current_entry = _get_plugin_field(current_config, "entry")
    new_id = _get_plugin_field(new_config, "id")
    new_entry = _get_plugin_field(new_config, "entry")

    if new_id is not None and current_id is not None and new_id != current_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify critical field 'plugin.id'. This field is protected.",
        )
    if new_entry is not None and current_entry is not None and new_entry != current_entry:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify critical field 'plugin.entry'. This field is protected.",
        )
