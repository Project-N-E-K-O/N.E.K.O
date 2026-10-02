"""Stable fingerprints for resolved plugin configuration values.

The fingerprint is deliberately calculated from an already resolved value.  This
module does not read files, inspect plugin metadata, or emit configuration
contents, so it can be used by both the server resolver and a plugin process
after it has loaded its effective configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Final


_TYPE_KEY: Final = "__neko_config_value_type__"


def _canonicalize_mapping(value: Mapping[object, object]) -> dict[str, object]:
    """Return a recursively normalized mapping with deterministic key order.

    TOML tables use string keys.  The tagged fallback keeps this helper
    deterministic for callers that pass an unusual mapping with non-string
    keys without stringifying keys or leaking their values into diagnostics.
    """

    string_keys = all(isinstance(key, str) for key in value)
    if string_keys:
        return {
            key: canonicalize_config(value[key])
            for key in sorted(value)
            if isinstance(key, str)
        }

    items = [
        [canonicalize_config(key), canonicalize_config(item)]
        for key, item in value.items()
    ]
    items.sort(key=lambda item: _canonical_json(item[0]))
    return {_TYPE_KEY: "mapping", "items": items}


def canonicalize_config(value: object) -> object:
    """Normalize a TOML-compatible configuration into JSON-safe data.

    Mapping keys are sorted recursively and array order is preserved.  TOML's
    date/time scalar types are tagged so they cannot collide with strings, and
    non-finite floats are represented by their exact hexadecimal spelling so
    JSON serialization remains strict and deterministic.
    """

    if isinstance(value, Mapping):
        return _canonicalize_mapping(value)
    if isinstance(value, list):
        return [canonicalize_config(item) for item in value]
    if isinstance(value, tuple):
        return {
            _TYPE_KEY: "tuple",
            "items": [canonicalize_config(item) for item in value],
        }
    if isinstance(value, datetime):
        return {_TYPE_KEY: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_TYPE_KEY: "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {_TYPE_KEY: "time", "value": value.isoformat()}
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {_TYPE_KEY: "float", "value": value.hex()}
    if isinstance(value, bytes):
        return {_TYPE_KEY: "bytes", "value": value.hex()}
    raise TypeError(f"Unsupported configuration value type: {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_config(config: object) -> str:
    """Return a content fingerprint for an already resolved config value."""

    serialized = _canonical_json(canonicalize_config(config)).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


__all__ = ["canonicalize_config", "fingerprint_config"]
