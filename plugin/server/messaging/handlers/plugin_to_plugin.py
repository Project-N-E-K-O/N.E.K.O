from __future__ import annotations

from plugin.core.rpc import RpcContext, RpcRemoteError
from plugin.logging_config import get_logger
from plugin.server.application.plugins.dispatch_service import PluginDispatchService
from plugin.server.messaging.handlers.common import (
    coerce_string_key_mapping,
    normalize_non_empty_str,
)

logger = get_logger("server.messaging.handlers.plugin_to_plugin")
plugin_dispatch_service = PluginDispatchService()


async def handle_plugin_to_plugin(ctx: RpcContext, request: dict[str, object]) -> object:
    to_plugin_obj = request.get("to_plugin")
    event_type_obj = request.get("event_type")
    event_id_obj = request.get("event_id")
    args = coerce_string_key_mapping(request.get("args", {})) or {}
    to_plugin = normalize_non_empty_str(to_plugin_obj) or ""
    event_type = normalize_non_empty_str(event_type_obj) or ""
    event_id = normalize_non_empty_str(event_id_obj) or ""
    if not to_plugin:
        raise RpcRemoteError(
            {"code": "INVALID_ARGUMENT", "message": "to_plugin is required"}
        )
    if not event_type:
        raise RpcRemoteError(
            {"code": "INVALID_ARGUMENT", "message": "event_type is required"}
        )
    if not event_id:
        raise RpcRemoteError(
            {"code": "INVALID_ARGUMENT", "message": "event_id is required"}
        )
    logger.info(
        "routing plugin event: from_plugin={}, to_plugin={}, event_type={}, event_id={}, request_id={}",
        ctx.from_plugin,
        to_plugin,
        event_type,
        event_id,
        ctx.request_id,
    )
    result = await plugin_dispatch_service.trigger_custom_event(
        to_plugin=to_plugin,
        event_type=event_type,
        event_id=event_id,
        args=args,
        timeout=ctx.remaining(),
    )
    return result
