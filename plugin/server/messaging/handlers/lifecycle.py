from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.bus.query_service import BusQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import (
    coerce_bool,
    coerce_optional_float,
    coerce_optional_int,
    coerce_string_key_mapping,
    resolve_common_fields,
    resolve_wildcard_scope_id,
)
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.lifecycle")
bus_query_service = BusQueryService()


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
    context = resolve_common_fields(request)
    if context is None:
        return

    from_plugin, request_id, timeout = context
    plugin_id = resolve_wildcard_scope_id(
        value=request.get("plugin_id"),
        fallback=from_plugin,
    )
    max_count = coerce_optional_int(request.get("max_count", request.get("limit")))
    since_ts = coerce_optional_float(request.get("since_ts"))
    strict = coerce_bool(request.get("strict", True), default=True)
    filter_data = coerce_string_key_mapping(request.get("filter"))

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
