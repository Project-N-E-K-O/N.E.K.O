from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.application.runs.ipc_service import RunIpcService

run_ipc_service = RunIpcService()


async def handle_run_update(ctx: RpcContext, request: dict[str, object]) -> object:
    return run_ipc_service.update_run(from_plugin=ctx.from_plugin, payload=request)
