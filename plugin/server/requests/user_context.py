from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from plugin.core.state import state


logger = logger.bind(component="router")


async def handle_user_context_get(request: Dict[str, Any], send_response) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    bucket_id = request.get("bucket_id")
    limit = request.get("limit", 20)

    if not isinstance(bucket_id, str) or not bucket_id:
        send_response(from_plugin, request_id, None, "Invalid bucket_id", timeout=timeout)
        return

    try:
        history = state.get_user_context(bucket_id=bucket_id, limit=int(limit))
        send_response(from_plugin, request_id, {"bucket_id": bucket_id, "history": history}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling user context get: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
