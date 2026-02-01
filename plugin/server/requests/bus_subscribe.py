from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

from plugin.core.state import state
from plugin.server.messaging.bus_subscriptions import new_sub_id
from plugin.settings import PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS
from plugin.server.requests.typing import SendResponse


logger = logger.bind(component="router")


def _norm_bus(v: Any) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    return s if s in ("messages", "events", "lifecycle", "runs", "export") else None


async def handle_bus_subscribe(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    bus = _norm_bus(request.get("bus"))
    if bus is None:
        send_response(from_plugin, request_id, None, "bus is required", timeout=float(timeout))
        return

    deliver = str(request.get("deliver") or "delta").strip()
    if deliver != "delta":
        send_response(from_plugin, request_id, None, "Only deliver=delta is supported", timeout=float(timeout))
        return

    rules = request.get("rules")
    if isinstance(rules, str):
        rules_list = [rules]
    elif isinstance(rules, list):
        rules_list = [str(x) for x in rules]
    else:
        rules_list = ["add"]

    plan = request.get("plan")
    debounce_ms = request.get("debounce_ms")

    sub_id = new_sub_id()
    info: Dict[str, Any] = {
        "from_plugin": from_plugin,
        "bus": bus,
        "rules": rules_list,
        "deliver": "delta",
        "plan": plan,
        "debounce_ms": debounce_ms,
        "timeout": float(timeout),
    }

    try:
        state.add_bus_subscription(bus, sub_id, info)
        if PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS:
            logger.info("[PluginRouter] BUS_SUBSCRIBE ok: from_plugin=%s bus=%s sub_id=%s", from_plugin, bus, sub_id)
        cur_rev = None
        try:
            cur_rev = int(state.get_bus_rev(bus))
        except Exception:
            cur_rev = None
        send_response(
            from_plugin,
            request_id,
            {"ok": True, "sub_id": sub_id, "bus": bus, "rev": cur_rev},
            None,
            timeout=float(timeout),
        )
    except Exception as e:
        logger.exception("[PluginRouter] Error handling BUS_SUBSCRIBE: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=float(timeout))


async def handle_bus_unsubscribe(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    bus = _norm_bus(request.get("bus"))
    sub_id = request.get("sub_id")
    if bus is None or not isinstance(sub_id, str) or not sub_id:
        send_response(from_plugin, request_id, None, "bus and sub_id are required", timeout=float(timeout))
        return

    try:
        ok = state.remove_bus_subscription(bus, sub_id)
        if PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS:
            logger.info(
                "[PluginRouter] BUS_UNSUBSCRIBE: from_plugin=%s bus=%s sub_id=%s ok=%s",
                from_plugin,
                bus,
                sub_id,
                bool(ok),
            )
        send_response(from_plugin, request_id, {"ok": bool(ok), "sub_id": sub_id, "bus": bus}, None, timeout=float(timeout))
    except Exception as e:
        logger.exception("[PluginRouter] Error handling BUS_UNSUBSCRIBE: %s", e)
        send_response(from_plugin, request_id, None, str(e), timeout=float(timeout))
