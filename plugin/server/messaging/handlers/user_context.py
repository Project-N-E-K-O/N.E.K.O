from __future__ import annotations

from plugin.core.rpc import RpcContext, RpcRemoteError
from plugin.server.application.messages.context_query_service import (
    UserContextQueryService,
)
from plugin.server.messaging.handlers.common import normalize_non_empty_str

user_context_query_service = UserContextQueryService()


async def handle_user_context_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    bucket_id = normalize_non_empty_str(request.get("bucket_id"))
    if bucket_id is None:
        raise RpcRemoteError({"code": "INVALID_ARGUMENT", "message": "Invalid bucket_id"})
    return await user_context_query_service.get_user_context(
        bucket_id=bucket_id, limit=request.get("limit", 20)
    )
