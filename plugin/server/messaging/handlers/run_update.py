from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.runs.ipc_service import RunIpcService
from plugin.server.messaging.handlers.common import resolve_common_fields

logger = get_logger("server.messaging.handlers.run_update")
run_ipc_service = RunIpcService()


async def handle_run_update(request: dict[str, object]) -> object:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields
    result = run_ipc_service.update_run(from_plugin=from_plugin, payload=request)
    return result
