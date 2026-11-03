from __future__ import annotations

from typing import Any

from .time_utils import now_iso


def parse_bool_config(value: Any, default: bool = True) -> bool:
    """Parse a configuration value as bool, supporting bool / str / None.

    Supported strings: \"true\"/\"yes\"/\"on\"/\"1\" → True, \"false\"/\"no\"/\"off\"/\"0\" → False (case-insensitive).
    Return *default* for other types or unrecognized strings.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    if value is None:
        return default
    return default


__all__ = ["now_iso", "parse_bool_config"]
