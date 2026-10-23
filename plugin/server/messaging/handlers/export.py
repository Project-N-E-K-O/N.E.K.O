from __future__ import annotations

import asyncio
import functools

from plugin.logging_config import get_logger
from plugin.server.application.runs.ipc_service import RunIpcService
from plugin.server.messaging.handlers.common import resolve_common_fields

logger = get_logger("server.messaging.handlers.export")
run_ipc_service = RunIpcService()


async def handle_export_push(request: dict[str, object]) -> object:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields
    result = await asyncio.to_thread(
        functools.partial(
            run_ipc_service.push_export, from_plugin=from_plugin, payload=request
        )
    )
    return result
