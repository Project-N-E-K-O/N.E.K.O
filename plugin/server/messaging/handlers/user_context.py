from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.context_query_service import UserContextQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import normalize_non_empty_str, resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.user_context")
user_context_query_service = UserContextQueryService()


async def handle_user_context_get(request: dict[str, object], send_response: SendResponse) -> None:
    context = resolve_common_fields(request)
    if context is None:
        return

    from_plugin, request_id, timeout = context
    bucket_id = normalize_non_empty_str(request.get("bucket_id"))
    if bucket_id is None:
        send_response(from_plugin, request_id, None, "Invalid bucket_id", timeout=timeout)
        return

    limit = request.get("limit", 20)
    try:
        payload = await user_context_query_service.get_user_context(bucket_id=bucket_id, limit=limit)
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as exc:
        logger.warning(
            "USER_CONTEXT_GET failed: bucket_id={}, code={}, message={}",
            bucket_id,
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)
