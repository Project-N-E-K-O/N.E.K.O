from __future__ import annotations

from collections.abc import Mapping

from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.normalization import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    coerce_bool,
    coerce_optional_float,
    coerce_optional_int,
    coerce_string_key_mapping,
    coerce_timeout,
    normalize_non_empty_str,
    normalize_pagination_limit,
    resolve_wildcard_scope_id,
)

def resolve_common_fields(
    request: Mapping[str, object],
    *,
    timeout_default: float = DEFAULT_TIMEOUT_SECONDS,
    timeout_max: float = MAX_TIMEOUT_SECONDS,
) -> tuple[str, str, float] | None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = coerce_timeout(
        request.get("timeout", timeout_default),
        default=timeout_default,
        max_seconds=timeout_max,
    )

    from_plugin = normalize_non_empty_str(from_plugin_obj)
    if from_plugin is None:
        return None
    request_id = normalize_non_empty_str(request_id_obj)
    if request_id is None:
        return None
    return from_plugin, request_id, timeout


def domain_error_payload(error: ServerDomainError) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": error.code,
        "message": error.message,
    }
    if error.details:
        payload["details"] = error.details
    return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "coerce_timeout",
    "coerce_optional_int",
    "coerce_optional_float",
    "coerce_bool",
    "coerce_string_key_mapping",
    "normalize_non_empty_str",
    "resolve_wildcard_scope_id",
    "normalize_pagination_limit",
    "resolve_common_fields",
    "domain_error_payload",
]
