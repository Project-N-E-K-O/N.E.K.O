from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.plugins.router_query_service import (
    PluginRouterQueryService,
)
from plugin.server.messaging.handlers.common import (
    coerce_string_key_mapping,
    resolve_common_fields,
)

logger = get_logger("server.messaging.handlers.plugin_query")
plugin_router_query_service = PluginRouterQueryService()


async def handle_plugin_query(request: dict[str, object]) -> object:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    filters = coerce_string_key_mapping(request.get("filters"))
    plugins = await plugin_router_query_service.query_plugins(filters=filters)
    return {"plugins": plugins}
