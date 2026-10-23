from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.admin import AdminQueryService
from plugin.server.messaging.handlers.common import resolve_common_fields

logger = get_logger("server.messaging.handlers.system_config")
admin_query_service = AdminQueryService()


async def handle_plugin_system_config_get(request: dict[str, object]) -> object:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields
    payload = await admin_query_service.get_system_config()
    return payload
