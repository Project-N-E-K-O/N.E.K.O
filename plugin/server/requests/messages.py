from __future__ import annotations

import logging
from typing import Any, Dict

from plugin.server.requests.typing import SendResponse
from plugin.server.services import get_messages_from_queue, push_message_to_queue, validate_and_advance_push_seq


logger = logging.getLogger("plugin.router")


async def handle_message_get(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    plugin_id = request.get("plugin_id")
    if not isinstance(plugin_id, str) or not plugin_id:
        plugin_id = from_plugin
    if isinstance(plugin_id, str) and plugin_id.strip() == "*":
        plugin_id = None

    max_count = request.get("max_count", request.get("limit", None))
    priority_min = request.get("priority_min", None)
    source = request.get("source", None)
    flt = request.get("filter", None)
    strict = request.get("strict", True)
    since_ts = request.get("since_ts", None)
    raw = request.get("raw", False)

    try:
        messages = get_messages_from_queue(
            plugin_id=plugin_id,
            max_count=int(max_count) if max_count is not None else None,
            priority_min=int(priority_min) if priority_min is not None else None,
            source=str(source) if isinstance(source, str) and source else None,
            filter=dict(flt) if isinstance(flt, dict) else None,
            strict=bool(strict),
            since_ts=float(since_ts) if since_ts is not None else None,
            raw=bool(raw),
        )
        send_response(from_plugin, request_id, {"plugin_id": plugin_id or "*", "messages": messages}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling MESSAGE_GET: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_message_push(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    source = request.get("source")
    message_type = request.get("message_type")
    description = request.get("description", "")
    priority = request.get("priority", 0)
    content = request.get("content", None)
    binary_data = request.get("binary_data", None)
    binary_url = request.get("binary_url", None)
    metadata = request.get("metadata", None)
    seq = request.get("seq", None)

    if not isinstance(source, str) or not source:
        send_response(from_plugin, request_id, None, "source is required", timeout=timeout)
        return
    if not isinstance(message_type, str) or not message_type:
        send_response(from_plugin, request_id, None, "message_type is required", timeout=timeout)
        return

    try:
        if seq is not None:
            try:
                validate_and_advance_push_seq(plugin_id=str(from_plugin), seq=int(seq))
            except Exception:
                pass
        mid = push_message_to_queue(
            plugin_id=str(from_plugin),
            source=str(source),
            message_type=str(message_type),
            description=str(description) if isinstance(description, str) else "",
            priority=int(priority) if priority is not None else 0,
            content=str(content) if isinstance(content, str) or content is None else str(content),
            binary_data=binary_data if isinstance(binary_data, (bytes, type(None))) else None,
            binary_url=str(binary_url) if isinstance(binary_url, str) and binary_url else None,
            metadata=dict(metadata) if isinstance(metadata, dict) else None,
            mode="zmq-sync",
        )
        send_response(from_plugin, request_id, {"message_id": mid}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling MESSAGE_PUSH: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
