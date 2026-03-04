from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.messages.context_query_service import UserContextQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.user_context")
user_context_query_service = UserContextQueryService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


async def handle_user_context_get(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    bucket_id_obj = request.get("bucket_id")
    if not isinstance(bucket_id_obj, str) or not bucket_id_obj:
        send_response(from_plugin, request_id, None, "Invalid bucket_id", timeout=timeout)
        return

    limit = request.get("limit", 20)
    try:
        payload = await user_context_query_service.get_user_context(bucket_id=bucket_id_obj, limit=limit)
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as exc:
        logger.warning(
            "USER_CONTEXT_GET failed: bucket_id={}, code={}, message={}",
            bucket_id_obj,
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)
