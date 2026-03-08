from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.runs.ipc_service import RunIpcService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import domain_error_payload, resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.run_update")
run_ipc_service = RunIpcService()

async def handle_run_update(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields

    try:
        result = run_ipc_service.update_run(from_plugin=from_plugin, payload=request)
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning(
            "RUN_UPDATE failed: code={}, message={}, plugin_id={}",
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
