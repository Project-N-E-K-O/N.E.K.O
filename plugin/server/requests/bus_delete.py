from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from plugin.server.requests.typing import SendResponse
from plugin.server.services import (
    delete_event_from_store,
    delete_lifecycle_from_store,
    delete_message_from_store,
)


logger = logger.bind(component="router")


async def handle_message_del(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    message_id = request.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        send_response(from_plugin, request_id, None, "message_id is required", timeout=timeout)
        return

    try:
        ok = delete_message_from_store(message_id)
        send_response(from_plugin, request_id, {"deleted": bool(ok), "message_id": message_id}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling MESSAGE_DEL: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_event_del(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    event_id = request.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        send_response(from_plugin, request_id, None, "event_id is required", timeout=timeout)
        return

    try:
        ok = delete_event_from_store(event_id)
        send_response(from_plugin, request_id, {"deleted": bool(ok), "event_id": event_id}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling EVENT_DEL: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_lifecycle_del(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    lifecycle_id = request.get("lifecycle_id")
    if not isinstance(lifecycle_id, str) or not lifecycle_id:
        send_response(from_plugin, request_id, None, "lifecycle_id is required", timeout=timeout)
        return

    try:
        ok = delete_lifecycle_from_store(lifecycle_id)
        send_response(from_plugin, request_id, {"deleted": bool(ok), "lifecycle_id": lifecycle_id}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling LIFECYCLE_DEL: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
