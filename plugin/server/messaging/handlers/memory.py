from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.application.messages.memory_query_service import MemoryQueryService

memory_query_service = MemoryQueryService()


async def handle_memory_query(ctx: RpcContext, request: dict[str, object]) -> object:
    return await memory_query_service.query_memory(
        lanlan_name=request.get("lanlan_name"),
        query=request.get("query"),
        timeout=ctx.remaining(),
    )

async def handle_memory_search(ctx: RpcContext, request: dict[str, object]) -> object:
    from plugin.server.application.memory import search
    return await search(ctx, request)


async def handle_memory_persona_get(ctx: RpcContext, request: dict[str, object]) -> object:
    from plugin.server.application.memory import get_persona
    return await get_persona(ctx, request)
