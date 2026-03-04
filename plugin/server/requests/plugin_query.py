from __future__ import annotations

from collections.abc import Mapping

from plugin.logging_config import get_logger
from plugin.server.application.plugins.router_query_service import PluginRouterQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.plugin_query")
plugin_router_query_service = PluginRouterQueryService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


def _coerce_filters(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            normalized[key] = item
    return normalized


async def handle_plugin_query(request: dict[str, object], send_response: SendResponse) -> None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return

    from_plugin = from_plugin_obj
    request_id = request_id_obj
    filters = _coerce_filters(request.get("filters"))

    try:
        plugins = await plugin_router_query_service.query_plugins(filters=filters)
        send_response(
            from_plugin,
            request_id,
            {"plugins": plugins},
            None,
            timeout=timeout,
        )
    except ServerDomainError as exc:
        logger.warning(
            "PLUGIN_QUERY failed: code={}, message={}",
            exc.code,
            exc.message,
        )
        send_response(from_plugin, request_id, None, exc.message, timeout=timeout)
