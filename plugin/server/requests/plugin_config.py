from __future__ import annotations

from typing import Any, Dict


async def handle_plugin_config_get(request: Dict[str, Any], send_response) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    target_plugin_id = request.get("plugin_id") or from_plugin
    if target_plugin_id != from_plugin:
        send_response(
            from_plugin,
            request_id,
            None,
            "Permission denied: can only read own config",
            timeout=timeout,
        )
        return

    try:
        from plugin.server.config_service import load_plugin_config

        data = load_plugin_config(target_plugin_id)
        send_response(from_plugin, request_id, data, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_update(request: Dict[str, Any], send_response) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    target_plugin_id = request.get("plugin_id") or from_plugin
    if target_plugin_id != from_plugin:
        send_response(
            from_plugin,
            request_id,
            None,
            "Permission denied: can only update own config",
            timeout=timeout,
        )
        return

    updates = request.get("updates")
    if not isinstance(updates, dict):
        send_response(from_plugin, request_id, None, "Invalid updates: must be a dict", timeout=timeout)
        return

    try:
        from plugin.server.config_service import update_plugin_config

        result = update_plugin_config(target_plugin_id, updates)
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
