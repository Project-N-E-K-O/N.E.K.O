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
