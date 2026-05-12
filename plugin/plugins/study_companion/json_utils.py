from __future__ import annotations

from typing import Any


def json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    if isinstance(value, tuple):
        return [json_copy(item) for item in value]
    return value
