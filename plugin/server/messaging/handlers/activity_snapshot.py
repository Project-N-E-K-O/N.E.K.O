from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.activity_snapshot_service import (
    ActivitySnapshotService,
)
from plugin.server.messaging.handlers.common import resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.activity_snapshot")
activity_snapshot_service = ActivitySnapshotService()
_RUNTIME_ERRORS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    OSError,
    TimeoutError,
)


async def handle_activity_snapshot_get(
    request: dict[str, object],
    send_response: SendResponse,
) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields
    try:
        payload = await activity_snapshot_service.get_activity_snapshot(
            lanlan_name=request.get("lanlan_name"),
            include_enrichment=request.get("include_enrichment", False),
        )
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except _RUNTIME_ERRORS as error:
        logger.error(
            "ACTIVITY_SNAPSHOT_GET unexpected failure: err_type={}, err={}",
            type(error).__name__,
            str(error),
        )
        send_response(
            from_plugin,
            request_id,
            None,
            "Internal server error",
            timeout=timeout,
        )
