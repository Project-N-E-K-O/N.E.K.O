from __future__ import annotations

from plugin.core.rpc import RpcRemoteError
from plugin.logging_config import get_logger
from plugin.server.application.messages.context_query_service import (
    UserContextQueryService,
)
from plugin.server.messaging.handlers.common import (
    normalize_non_empty_str,
    resolve_common_fields,
)

logger = get_logger("server.messaging.handlers.user_context")
user_context_query_service = UserContextQueryService()


async def handle_user_context_get(request: dict[str, object]) -> object:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    bucket_id = normalize_non_empty_str(request.get("bucket_id"))
    if bucket_id is None:
        raise RpcRemoteError("Invalid bucket_id")
        return
    limit = request.get("limit", 20)
    payload = await user_context_query_service.get_user_context(
        bucket_id=bucket_id, limit=limit
    )
    return payload
