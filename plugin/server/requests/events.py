from __future__ import annotations

import asyncio
from typing import Any, Dict

from loguru import logger

from plugin.server.requests.typing import SendResponse
from plugin.server.services import get_events_from_queue


logger = logger.bind(component="router")


async def handle_event_get(request: Dict[str, Any], send_response: SendResponse) -> None:
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
    since_ts = request.get("since_ts", None)
    flt = request.get("filter", None)
    strict = request.get("strict", True)

    try:
        events = await asyncio.to_thread(
            get_events_from_queue,
            plugin_id=plugin_id,
            max_count=int(max_count) if max_count is not None else None,
            filter=dict(flt) if isinstance(flt, dict) else None,
            strict=bool(strict),
            since_ts=float(since_ts) if since_ts is not None else None,
        )
        send_response(from_plugin, request_id, {"plugin_id": plugin_id or "*", "events": events}, None, timeout=timeout)
    except Exception as e:
        logger.exception("[PluginRouter] Error handling EVENT_GET: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
