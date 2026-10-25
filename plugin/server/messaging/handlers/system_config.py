from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.application.admin import AdminQueryService

admin_query_service = AdminQueryService()


async def handle_plugin_system_config_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    return await admin_query_service.get_system_config()
