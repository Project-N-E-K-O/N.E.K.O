from __future__ import annotations

from collections.abc import Mapping

from plugin.server.domain.errors import ServerDomainError


def coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        if timeout > 0:
            return timeout
        return 5.0
    return 5.0


def resolve_common_fields(request: Mapping[str, object]) -> tuple[str, str, float] | None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return None
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return None
    return from_plugin_obj, request_id_obj, timeout


def domain_error_payload(error: ServerDomainError) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": error.code,
        "message": error.message,
    }
    if error.details:
        payload["details"] = error.details
    return payload
