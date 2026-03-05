from __future__ import annotations

from collections.abc import Callable

from plugin.logging_config import get_logger
from plugin.server.application.bus.mutation_service import BusMutationService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import normalize_non_empty_str, resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.bus_delete")
bus_mutation_service = BusMutationService()


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
    context = resolve_common_fields(request)
    if context is None:
        return
    from_plugin, request_id, timeout = context

    identifier = normalize_non_empty_str(request.get(field_name))
    if identifier is None:
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=f"{field_name} is required",
        )
        return

    try:
        deleted = delete_fn(identifier)
    except ServerDomainError as exc:
        logger.warning(
            "{} failed: code={}, message={}, {}={}",
            op_name,
            exc.code,
            exc.message,
            field_name,
            identifier,
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
        field_value=identifier,
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
