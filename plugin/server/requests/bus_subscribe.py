from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.bus.subscription_service import BusSubscriptionService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.common import resolve_common_fields
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.bus_subscribe")
bus_subscription_service = BusSubscriptionService()


async def handle_bus_subscribe(request: dict[str, object], send_response: SendResponse) -> None:
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context
    try:
        result = bus_subscription_service.subscribe(
            from_plugin=from_plugin,
            bus=request.get("bus"),
            deliver=request.get("deliver"),
            rules=request.get("rules"),
            plan=request.get("plan"),
            debounce_ms=request.get("debounce_ms"),
            timeout=timeout,
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
    context = resolve_common_fields(request)
    if context is None:
        return

    from_plugin, request_id, timeout = context
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
