from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.bus.subscription_service import BusSubscriptionService
from plugin.server.messaging.handlers.common import resolve_common_fields

logger = get_logger("server.messaging.handlers.bus_subscribe")
bus_subscription_service = BusSubscriptionService()


async def handle_bus_subscribe(request: dict[str, object]) -> object:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    result = bus_subscription_service.subscribe(
        from_plugin=from_plugin,
        bus=request.get("bus"),
        deliver=request.get("deliver"),
        rules=request.get("rules"),
        debounce_ms=request.get("debounce_ms"),
        timeout=timeout,
    )
    return result


async def handle_bus_unsubscribe(request: dict[str, object]) -> object:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    result = bus_subscription_service.unsubscribe(
        from_plugin=from_plugin, bus=request.get("bus"), sub_id=request.get("sub_id")
    )
    return result
