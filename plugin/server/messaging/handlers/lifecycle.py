from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.bus.query_service import BusQueryService
from plugin.server.messaging.handlers.common import (
    coerce_bool,
    coerce_optional_float,
    coerce_optional_int,
    coerce_string_key_mapping,
    resolve_common_fields,
    resolve_wildcard_scope_id,
)

logger = get_logger("server.messaging.handlers.lifecycle")
bus_query_service = BusQueryService()


async def handle_lifecycle_get(request: dict[str, object]) -> object:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    plugin_id = resolve_wildcard_scope_id(
        value=request.get("plugin_id"), fallback=from_plugin
    )
    max_count = coerce_optional_int(request.get("max_count", request.get("limit")))
    since_ts = coerce_optional_float(request.get("since_ts"))
    strict = coerce_bool(request.get("strict", True), default=True)
    filter_data = coerce_string_key_mapping(request.get("filter"))
    lifecycle_records = await bus_query_service.get_lifecycle(
        plugin_id=plugin_id,
        max_count=max_count,
        filter_data=filter_data,
        strict=strict,
        since_ts=since_ts,
    )
    return {"plugin_id": plugin_id or "*", "events": lifecycle_records}
