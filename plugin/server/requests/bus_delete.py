from __future__ import annotations

from collections.abc import Callable

from plugin.logging_config import get_logger
from plugin.server.application.bus.mutation_service import BusMutationService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.bus_delete")
bus_mutation_service = BusMutationService()


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return 5.0
    if isinstance(value, (int, float)):
        timeout = float(value)
        return timeout if timeout > 0 else 5.0
    return 5.0


def _resolve_common_context(request: dict[str, object]) -> tuple[str, str, float] | None:
    from_plugin_obj = request.get("from_plugin")
    request_id_obj = request.get("request_id")
    timeout = _coerce_timeout(request.get("timeout", 5.0))

    if not isinstance(from_plugin_obj, str) or not from_plugin_obj:
        return None
    if not isinstance(request_id_obj, str) or not request_id_obj:
        return None
    return from_plugin_obj, request_id_obj, timeout


def _send_error(
    *,
    send_response: SendResponse,
    from_plugin: str,
    request_id: str,
    timeout: float,
    message: str,
) -> None:
    send_response(from_plugin, request_id, None, message, timeout=timeout)


def _send_success(
    *,
    send_response: SendResponse,
    from_plugin: str,
    request_id: str,
    timeout: float,
    field_name: str,
    field_value: str,
    deleted: bool,
) -> None:
    send_response(
        from_plugin,
        request_id,
        {"deleted": deleted, field_name: field_value},
        None,
        timeout=timeout,
    )


def _handle_delete(
    *,
    request: dict[str, object],
    send_response: SendResponse,
    field_name: str,
    delete_fn: Callable[[str], bool],
    op_name: str,
) -> None:
    context = _resolve_common_context(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context

    identifier_obj = request.get(field_name)
    if not isinstance(identifier_obj, str) or not identifier_obj:
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=f"{field_name} is required",
        )
        return

    try:
        deleted = delete_fn(identifier_obj)
    except ServerDomainError as exc:
        logger.warning(
            "{} failed: code={}, message={}, {}={}",
            op_name,
            exc.code,
            exc.message,
            field_name,
            identifier_obj,
        )
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=exc.message,
        )
        return

    _send_success(
        send_response=send_response,
        from_plugin=from_plugin,
        request_id=request_id,
        timeout=timeout,
        field_name=field_name,
        field_value=identifier_obj,
        deleted=deleted,
    )


async def handle_message_del(request: dict[str, object], send_response: SendResponse) -> None:
    _handle_delete(
        request=request,
        send_response=send_response,
        field_name="message_id",
        delete_fn=bus_mutation_service.delete_message,
        op_name="MESSAGE_DEL",
    )


async def handle_event_del(request: dict[str, object], send_response: SendResponse) -> None:
    _handle_delete(
        request=request,
        send_response=send_response,
        field_name="event_id",
        delete_fn=bus_mutation_service.delete_event,
        op_name="EVENT_DEL",
    )


async def handle_lifecycle_del(request: dict[str, object], send_response: SendResponse) -> None:
    _handle_delete(
        request=request,
        send_response=send_response,
        field_name="lifecycle_id",
        delete_fn=bus_mutation_service.delete_lifecycle,
        op_name="LIFECYCLE_DEL",
    )
