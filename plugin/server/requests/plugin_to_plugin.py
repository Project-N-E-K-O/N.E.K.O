from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from plugin.core.state import state


logger = logger.bind(component="router")


async def handle_plugin_to_plugin(request: Dict[str, Any], send_response) -> None:

    from_plugin = request.get("from_plugin")
    to_plugin = request.get("to_plugin")
    event_type = request.get("event_type")
    event_id = request.get("event_id")
    args = request.get("args", {})
    request_id = request.get("request_id")
    timeout = request.get("timeout", 10.0)

    logger.info(
        f"[PluginRouter] Routing request: {from_plugin} -> {to_plugin}, "
        f"event={event_type}.{event_id}, req_id={request_id}"
    )
    # 使用缓存快照避免锁竞争
    hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
    host = hosts_snapshot.get(to_plugin)
    if not host:
        error_msg = f"Plugin '{to_plugin}' not found"
        logger.error(f"[PluginRouter] {error_msg}")
        send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
        return

    try:
        health = host.health_check()
        if not health.alive:
            error_msg = f"Plugin '{to_plugin}' process is not alive"
            logger.error(f"[PluginRouter] {error_msg}")
            send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
            return
    except Exception as e:
        error_msg = f"Health check failed for plugin '{to_plugin}': {e}"
        logger.error(f"[PluginRouter] {error_msg}")
        send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
        return

    try:
        result = await host.trigger_custom_event(
            event_type=event_type,
            event_id=event_id,
            args=args,
            timeout=timeout,
        )
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"[PluginRouter] Error triggering custom event: {e}")
        send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
