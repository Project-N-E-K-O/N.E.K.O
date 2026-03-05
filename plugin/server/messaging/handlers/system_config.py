from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.admin import AdminQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.system_config")
admin_query_service = AdminQueryService()

async def handle_plugin_system_config_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields
    try:
        payload = await admin_query_service.get_system_config()
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning(
            "PLUGIN_SYSTEM_CONFIG_GET failed: code={}, message={}",
            error.code,
            error.message,
        )
        send_response(from_plugin, request_id, None, error.message, timeout=timeout)
