from __future__ import annotations

import asyncio
import functools

from plugin.core.rpc import RpcContext
from plugin.server.application.runs.ipc_service import RunIpcService

run_ipc_service = RunIpcService()


async def handle_export_push(ctx: RpcContext, request: dict[str, object]) -> object:
    return await asyncio.to_thread(
        functools.partial(
            run_ipc_service.push_export, from_plugin=ctx.from_plugin, payload=request
        )
    )
