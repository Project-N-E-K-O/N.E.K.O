from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.memory_query_service import MemoryQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.common import resolve_common_fields
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.memory")
memory_query_service = MemoryQueryService()

async def handle_memory_query(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return

    from_plugin, request_id, timeout = common_fields
    try:
        payload = await memory_query_service.query_memory(
            lanlan_name=request.get("lanlan_name"),
            query=request.get("query"),
            timeout=timeout,
        )
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning(
            "MEMORY_QUERY failed: code={}, message={}",
            error.code,
            error.message,
        )
        send_response(from_plugin, request_id, None, error.message, timeout=timeout)
