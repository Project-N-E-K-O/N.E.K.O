from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.application.plugins.router_query_service import (
    PluginRouterQueryService,
)
from plugin.server.messaging.handlers.common import coerce_string_key_mapping

plugin_router_query_service = PluginRouterQueryService()


async def handle_plugin_query(ctx: RpcContext, request: dict[str, object]) -> object:
    filters = coerce_string_key_mapping(request.get("filters"))
    plugins = await plugin_router_query_service.query_plugins(filters=filters)
    return {"plugins": plugins}
