from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.runs.ipc_service import RunIpcService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import domain_error_payload, resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.export")
run_ipc_service = RunIpcService()

async def handle_export_push(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields

    try:
        result = run_ipc_service.push_export(from_plugin=from_plugin, payload=request)
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning(
            "EXPORT_PUSH failed: code={}, message={}, plugin_id={}",
            error.code,
            error.message,
            from_plugin,
        )
        send_response(
            from_plugin,
            request_id,
            None,
            domain_error_payload(error),
            timeout=timeout,
        )
