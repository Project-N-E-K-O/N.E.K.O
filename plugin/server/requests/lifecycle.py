from __future__ import annotations

from collections.abc import Mapping

from plugin.logging_config import get_logger
from plugin.server.application.bus.query_service import BusQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.lifecycle")
bus_query_service = BusQueryService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


def _coerce_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _coerce_filter_data(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            normalized[key] = item
    return normalized


def _resolve_plugin_id(*, request: Mapping[str, object], from_plugin: str) -> str | None:
    plugin_id_obj = request.get("plugin_id")
    if isinstance(plugin_id_obj, str) and plugin_id_obj.strip():
        if plugin_id_obj.strip() == "*":
            return None
        return plugin_id_obj
    return from_plugin


def _send_error(
    *,
    send_response: SendResponse,
    from_plugin: str,
    request_id: str,
    timeout: float,
    message: str,
) -> None:
    send_response(from_plugin, request_id, None, message, timeout=timeout)


async def handle_lifecycle_get(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    plugin_id = _resolve_plugin_id(request=request, from_plugin=from_plugin)
    max_count = _coerce_optional_int(request.get("max_count", request.get("limit")))
    since_ts = _coerce_optional_float(request.get("since_ts"))
    strict = bool(request.get("strict", True))
    filter_data = _coerce_filter_data(request.get("filter"))

    try:
        lifecycle_records = await bus_query_service.get_lifecycle(
            plugin_id=plugin_id,
            max_count=max_count,
            filter_data=filter_data,
            strict=strict,
            since_ts=since_ts,
        )
        send_response(
            from_plugin,
            request_id,
            {"plugin_id": plugin_id or "*", "events": lifecycle_records},
            None,
            timeout=timeout,
        )
    except ServerDomainError as exc:
        logger.warning(
            "LIFECYCLE_GET failed: plugin_id={}, code={}, message={}",
            plugin_id,
            exc.code,
            exc.message,
        )
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=exc.message,
        )
