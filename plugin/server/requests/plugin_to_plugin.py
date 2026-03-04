from __future__ import annotations

from collections.abc import Mapping

from plugin.logging_config import get_logger
from plugin.server.application.plugins.dispatch_service import PluginDispatchService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.plugin_to_plugin")
plugin_dispatch_service = PluginDispatchService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 10.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 10.0
    return 10.0


def _coerce_args(value: object) -> object:
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(key, str):
                normalized[key] = item
        return normalized
    return {}


async def handle_plugin_to_plugin(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    to_plugin_obj = request.get("to_plugin")
    event_type_obj = request.get("event_type")
    event_id_obj = request.get("event_id")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 10.0))
    args = _coerce_args(request.get("args", {}))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    to_plugin = to_plugin_obj.strip() if isinstance(to_plugin_obj, str) else ""
    event_type = event_type_obj.strip() if isinstance(event_type_obj, str) else ""
    event_id = event_id_obj.strip() if isinstance(event_id_obj, str) else ""

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
