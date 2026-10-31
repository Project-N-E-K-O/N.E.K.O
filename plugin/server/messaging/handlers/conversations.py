"""Thin control-RPC access to retained conversation details."""
from __future__ import annotations

from plugin.core.rpc import RpcContext
from plugin.server.domain.errors import ServerDomainError


def _service():
    from plugin.server.lifecycle import get_conversation_service

    service = get_conversation_service()
    if service is None:
        raise ServerDomainError(
            code="CONVERSATIONS_NOT_READY",
            message="Conversation observations are not ready",
            status_code=503,
        )
    return service


def _identity(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 256
        or any(ord(character) < 32 for character in value)
    ):
        raise ServerDomainError(
            code="INVALID_ARGUMENT", message=f"{key} is required", status_code=400,
        )
    return value


async def handle_conversation_reply_get(ctx: RpcContext, request: dict[str, object]) -> object:
    return _service().get_reply(_identity(request, "reply_id"))


async def handle_conversation_interaction_get(ctx: RpcContext, request: dict[str, object]) -> object:
    return _service().get_interaction(_identity(request, "input_id"))
