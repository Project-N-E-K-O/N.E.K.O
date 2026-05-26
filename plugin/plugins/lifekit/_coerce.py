"""Small coercion helpers for LifeKit entry inputs."""

from __future__ import annotations

import math
from typing import Any


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def clean_text(value: Any, default: str = "") -> str:
    return as_text(value, default).strip()


def finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    return max(minimum, min(result, maximum))
