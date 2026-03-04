from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.bus.subscription_service import BusSubscriptionService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.bus_subscribe")
bus_subscription_service = BusSubscriptionService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


async def handle_bus_subscribe(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    try:
        result = bus_subscription_service.subscribe(
            from_plugin=from_plugin,
            bus=request.get("bus"),
            deliver=request.get("deliver"),
            rules=request.get("rules"),
            plan=request.get("plan"),
            debounce_ms=request.get("debounce_ms"),
            timeout=request.get("timeout", 5.0),
        )
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except ServerDomainError as exc:
        logger.warning(
            "BUS_SUBSCRIBE failed: from_plugin={}, code={}, message={}",
            from_plugin,
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)


async def handle_bus_unsubscribe(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    try:
        result = bus_subscription_service.unsubscribe(
            from_plugin=from_plugin,
            bus=request.get("bus"),
            sub_id=request.get("sub_id"),
        )
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except ServerDomainError as exc:
        logger.warning(
            "BUS_UNSUBSCRIBE failed: from_plugin={}, code={}, message={}",
            from_plugin,
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)
