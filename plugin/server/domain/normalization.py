from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime

from plugin.server.domain.errors import ServerDomainError

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 60.0


def _coerce_positive_finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (OverflowError, ValueError):
            return None
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def coerce_timeout(
    value: object,
    *,
    default: float = DEFAULT_TIMEOUT_SECONDS,
    max_seconds: float = MAX_TIMEOUT_SECONDS,
) -> float:
    normalized_default = _coerce_positive_finite(default) or DEFAULT_TIMEOUT_SECONDS
    normalized_max = _coerce_positive_finite(max_seconds) or max(normalized_default, MAX_TIMEOUT_SECONDS)
    parsed = _coerce_positive_finite(value)
    if parsed is None:
        return normalized_default
    return min(parsed, normalized_max)


def coerce_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        try:
            return int(value)
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def coerce_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (OverflowError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = float(stripped)
        except (OverflowError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def coerce_string_key_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            normalized[key] = item
    return normalized


def normalize_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def resolve_wildcard_scope_id(*, value: object, fallback: str, wildcard: str = "*") -> str | None:
    normalized = normalize_non_empty_str(value)
    if normalized is None:
        return fallback
    if normalized == wildcard:
        return None
    return normalized


def normalize_pagination_limit(
    value: object,
    *,
    default: int,
    min_value: int = 1,
    max_value: int | None = None,
) -> int:
    normalized_default = max(min_value, int(default))
    parsed = coerce_optional_int(value)
    resolved = parsed if parsed is not None else normalized_default
    resolved = max(min_value, resolved)
    if max_value is not None:
        resolved = min(resolved, int(max_value))
    return resolved


def normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def normalize_mapping_list(raw_items: list[object], *, context: str) -> list[dict[str, object]]:
    normalized_items: list[dict[str, object]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} item is not an object",
                status_code=500,
                details={"index": index, "item_type": type(item).__name__},
            )
        normalized_items.append(normalize_mapping(item, context=f"{context}[{index}]"))
    return normalized_items


def normalize_optional_iso_datetime(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message=f"{field} must be a valid ISO datetime string",
            status_code=400,
            details={"field": field},
        )
    stripped = value.strip()
    if not stripped:
        return None
    try:
        datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message=f"{field} must be a valid ISO datetime string",
            status_code=400,
            details={"field": field},
        ) from exc
    return stripped
