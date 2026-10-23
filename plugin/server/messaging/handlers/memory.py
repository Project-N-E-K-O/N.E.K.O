from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.memory_query_service import MemoryQueryService
from plugin.server.messaging.handlers.common import resolve_common_fields

logger = get_logger("server.messaging.handlers.memory")
memory_query_service = MemoryQueryService()
_RUNTIME_ERRORS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    OSError,
    TimeoutError,
)


async def handle_memory_query(request: dict[str, object]) -> object:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields
    payload = await memory_query_service.query_memory(
        lanlan_name=request.get("lanlan_name"),
        query=request.get("query"),
        timeout=timeout,
    )
    return payload
