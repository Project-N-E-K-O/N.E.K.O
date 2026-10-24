"""Run a plugin host request handler through RpcDispatcher, as the request router does."""

from plugin.core.rpc import RpcDispatcher
from plugin.server.messaging.request_router import _domain_error


async def invoke_handler(handler, request, recorder):
    dispatcher = RpcDispatcher({"test": handler}, error_mapper=_domain_error)
    reply = await dispatcher.dispatch(dict(request, type="test"))
    recorder(
        reply["to_plugin"],
        reply["request_id"],
        reply["result"],
        reply["error"],
        timeout=request.get("timeout", 10.0),
    )
