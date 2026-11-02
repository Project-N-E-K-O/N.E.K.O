from __future__ import annotations

from collections.abc import Callable

from plugin.core.rpc import RpcContext, RpcRemoteError
from plugin.server.application.bus.mutation_service import BusMutationService
from plugin.server.messaging.handlers.common import normalize_non_empty_str

bus_mutation_service = BusMutationService()


def _handle_delete(
    *,
    request: dict[str, object],
    field_name: str,
    delete_fn: Callable[[str], bool],
) -> object:
    identifier = normalize_non_empty_str(request.get(field_name))
    if identifier is None:
        raise RpcRemoteError(
            {"code": "INVALID_ARGUMENT", "message": f"{field_name} is required"}
        )
    deleted = delete_fn(identifier)
    return {"deleted": deleted, field_name: identifier}


async def handle_message_del(ctx: RpcContext, request: dict[str, object]) -> object:
    return _handle_delete(
        request=request,
        field_name="message_id",
        delete_fn=bus_mutation_service.delete_message,
    )


async def handle_event_del(ctx: RpcContext, request: dict[str, object]) -> object:
    return _handle_delete(
        request=request,
        field_name="event_id",
        delete_fn=bus_mutation_service.delete_event,
    )


async def handle_lifecycle_del(ctx: RpcContext, request: dict[str, object]) -> object:
    return _handle_delete(
        request=request,
        field_name="lifecycle_id",
        delete_fn=bus_mutation_service.delete_lifecycle,
    )
