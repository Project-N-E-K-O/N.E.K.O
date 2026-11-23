from __future__ import annotations

from collections.abc import Mapping

DELETE_MARKER = "__DELETE__"

# Top-level plugin.toml tables that are manifest metadata rather than plugin
# settings. They are read from plugin.toml directly and never belong to the
# effective config, whatever the runtime config or a profile contains.
RESERVED_TOP_LEVEL_SECTIONS: frozenset[str] = frozenset({"permissions"})


def strip_reserved_sections(config: Mapping[str, object]) -> dict[str, object]:
    """Return a shallow copy of ``config`` without the reserved top-level tables."""
    return {
        key: value
        for key, value in config.items()
        if key not in RESERVED_TOP_LEVEL_SECTIONS
    }


def _to_string_key_mapping(raw: Mapping[object, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key_obj, value in raw.items():
        if isinstance(key_obj, str):
            normalized[key_obj] = value
    return normalized


def deep_merge(
    base: dict[str, object],
    updates: Mapping[object, object],
) -> dict[str, object]:
    result = dict(base)
    for key_obj, value in updates.items():
        if not isinstance(key_obj, str):
            continue
        key = key_obj

        if value == DELETE_MARKER:
            result.pop(key, None)
            continue

        if isinstance(value, Mapping):
            value_mapping = _to_string_key_mapping(value)
            if value_mapping.get("__replace__") is True:
                result[key] = {
                    nested_key: nested_value
                    for nested_key, nested_value in value_mapping.items()
                    if nested_key != "__replace__"
                }
                continue

            current_obj = result.get(key)
            if isinstance(current_obj, Mapping):
                current_mapping = _to_string_key_mapping(current_obj)
                if len(value_mapping) == 0:
                    result[key] = value_mapping
                else:
                    result[key] = deep_merge(current_mapping, value_mapping)
                continue

            result[key] = value_mapping
            continue

        result[key] = value
    return result
