from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.application.bus.subscription_service import BusSubscriptionService

bus_subscription_service = BusSubscriptionService()


async def handle_bus_subscribe(ctx: RpcContext, request: dict[str, object]) -> object:
    return bus_subscription_service.subscribe(
        from_plugin=ctx.from_plugin,
        bus=request.get("bus"),
        deliver=request.get("deliver"),
        rules=request.get("rules"),
        debounce_ms=request.get("debounce_ms"),
        timeout=ctx.remaining(),
    )


async def handle_bus_unsubscribe(ctx: RpcContext, request: dict[str, object]) -> object:
    return bus_subscription_service.unsubscribe(
        from_plugin=ctx.from_plugin, bus=request.get("bus"), sub_id=request.get("sub_id")
    )
