from __future__ import annotations

from plugin.logging_config import get_logger
from plugin.server.application.plugins.dispatch_service import PluginDispatchService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import (
    coerce_string_key_mapping,
    normalize_non_empty_str,
    resolve_common_fields,
)
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.plugin_to_plugin")
plugin_dispatch_service = PluginDispatchService()


async def handle_plugin_to_plugin(request: dict[str, object], send_response: SendResponse) -> None:
    context = resolve_common_fields(request, timeout_default=10.0, timeout_max=60.0)
    if context is None:
        return

    from_plugin, request_id, timeout = context
    to_plugin_obj = request.get("to_plugin")
    event_type_obj = request.get("event_type")
    event_id_obj = request.get("event_id")
    args = coerce_string_key_mapping(request.get("args", {})) or {}

    to_plugin = normalize_non_empty_str(to_plugin_obj) or ""
    event_type = normalize_non_empty_str(event_type_obj) or ""
    event_id = normalize_non_empty_str(event_id_obj) or ""

    if not to_plugin:
        send_response(from_plugin, request_id, None, "to_plugin is required", timeout=timeout)
        return
    if not event_type:
        send_response(from_plugin, request_id, None, "event_type is required", timeout=timeout)
        return
    if not event_id:
        send_response(from_plugin, request_id, None, "event_id is required", timeout=timeout)
        return

    logger.info(
        "routing plugin event: from_plugin={}, to_plugin={}, event_type={}, event_id={}, request_id={}",
        from_plugin,
        to_plugin,
        event_type,
        event_id,
        request_id,
    )
    try:
        result = await plugin_dispatch_service.trigger_custom_event(
            to_plugin=to_plugin,
            event_type=event_type,
            event_id=event_id,
            args=args,
            timeout=timeout,
        )
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except ServerDomainError as exc:
        logger.warning(
            "PLUGIN_TO_PLUGIN failed: to_plugin={}, event_type={}, event_id={}, code={}, message={}",
            to_plugin,
            event_type,
            event_id,
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)
