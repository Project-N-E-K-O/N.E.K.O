"""Optional JSON Schema annotations for the plugin configuration editor.

This is a bounded, local-only UI contract, not runtime configuration validation.
The schema belongs to the installed payload, never to a writable profile.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

_MAX_SCHEMA_BYTES = 256 * 1024
_MAX_SCHEMA_DEPTH = 32


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite JSON number')
    return result


def _check_node(node: object, depth: int = 0) -> None:
    if depth > _MAX_SCHEMA_DEPTH or not isinstance(node, dict):
        raise ValueError("Invalid configuration schema node")
    if "type" in node:
        declared = node["type"]
        allowed = {"object", "array", "string", "number", "integer", "boolean"}
        if not isinstance(declared, str) or declared not in allowed:
            raise ValueError("Invalid schema type")
    for key in ("title", "description"):
        if key in node and not isinstance(node[key], str):
            raise ValueError("Invalid schema annotation")
    for key in ("x-title-i18n", "x-description-i18n"):
        if key in node:
            translations = node[key]
            if not isinstance(translations, dict) or not all(
                isinstance(value, str) for value in translations.values()
            ):
                raise ValueError("Invalid schema translations")
    if "properties" in node:
        properties = node["properties"]
        if not isinstance(properties, dict):
            raise ValueError("Invalid schema properties")
        for child in properties.values():
            _check_node(child, depth + 1)
    if "items" in node:
        _check_node(node["items"], depth + 1)
    if "enum" in node:
        values = node["enum"]
        if not isinstance(values, list) or not values or not all(
            isinstance(value, (str, bool))
            or (isinstance(value, (int, float)) and math.isfinite(value))
            for value in values
        ):
            raise ValueError("Invalid schema enum")
    for key in ("minimum", "maximum"):
        if key in node:
            value = node[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError("Invalid schema bound")
    if "minimum" in node and "maximum" in node and node["minimum"] > node["maximum"]:
        raise ValueError("Invalid schema range")
    if "maxLength" in node:
        value = node["maxLength"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("Invalid schema string length")
    if "readOnly" in node and not isinstance(node["readOnly"], bool):
        raise ValueError("Invalid schema readOnly")


def load_config_editor_schema(manifest_path: Path) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Return presentation metadata; broken optional schemas must not block editing."""
    try:
        root = manifest_path.parent.resolve()
        path = root / "config.schema.json"
        if not path.exists():
            return None, []
        if not path.resolve().is_relative_to(root):
            raise ValueError("Schema must stay inside the plugin directory")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_SCHEMA_BYTES + 1)
        if len(raw) > _MAX_SCHEMA_BYTES:
            raise ValueError("Schema too large")
        schema = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant, parse_float=_finite_float)
        _check_node(schema)
        if schema.get("type") != "object":
            raise ValueError("Schema root must have type object")
        return schema, []
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError, OverflowError):
        # Never echo schema contents, paths, or parsing errors: annotations can
        # accidentally contain credentials. Existing config warnings expose this.
        return None, [{
            "code": "PLUGIN_CONFIG_EDITOR_SCHEMA_INVALID",
            "field": None,
            "message": "Invalid config.schema.json; using the generic configuration editor.",
            "severity": "warning",
            "source": "schema",
        }]
