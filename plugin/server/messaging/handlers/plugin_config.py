from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from plugin.core.rpc import RpcContext, RpcRemoteError, owns_deadline
from plugin.logging_config import get_logger
from plugin.server.application.config import ConfigCommandService, ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_storage import (
    ConfigCommitCancelled,
    ConfigCommitGuard,
)

logger = get_logger("server.messaging.handlers.plugin_config")
config_query_service = ConfigQueryService()
config_command_service = ConfigCommandService()
_CONFIG_WRITE_PERSIST_BUDGET_SECONDS = 4.0
# Leave time for the degraded reply to reach the caller before its deadline.
_CONFIG_WRITE_RESPONSE_GRACE_SECONDS = 0.25


def _consume_config_write_task(task: asyncio.Task[dict[str, object]]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _persist_before_request_deadline(
    *,
    request_deadline: float,
    persist: Callable[[ConfigCommitGuard], Coroutine[Any, Any, dict[str, object]]],
) -> dict[str, object] | None:
    now = time.monotonic()
    remaining = max(0.0, request_deadline - now)
    response_grace = min(_CONFIG_WRITE_RESPONSE_GRACE_SECONDS, remaining * 0.2)
    commit_deadline = min(
        request_deadline - response_grace, now + _CONFIG_WRITE_PERSIST_BUDGET_SECONDS
    )
    persist_budget = commit_deadline - now
    if persist_budget <= 0:
        return None
    commit_guard = ConfigCommitGuard(deadline=commit_deadline)
    task = asyncio.create_task(persist(commit_guard))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=persist_budget)
    except asyncio.CancelledError:
        cancelled = commit_guard.cancel_if_pending()
        if cancelled:
            task.add_done_callback(_consume_config_write_task)
        else:
            await asyncio.shield(task)
        raise
    except ConfigCommitCancelled:
        return None
    except TimeoutError:
        cancelled = commit_guard.cancel_if_pending()
        if not cancelled:
            return await task
        task.add_done_callback(_consume_config_write_task)
        return None


def _resolve_target_plugin_id(
    *, request: Mapping[str, object], from_plugin: str
) -> str:
    target_plugin_id_obj = request.get("plugin_id")
    if target_plugin_id_obj is None:
        return from_plugin
    if not isinstance(target_plugin_id_obj, str) or not target_plugin_id_obj.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="Invalid plugin_id",
            status_code=400,
            details={},
        )
    return target_plugin_id_obj.strip()


def _normalize_updates_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="Invalid updates: must be a dict",
            status_code=400,
            details={},
        )
    normalized: dict[str, object] = {}
    for key_obj, item in value.items():
        if not isinstance(key_obj, str):
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="Invalid updates: keys must be strings",
                status_code=400,
                details={},
            )
        normalized[key_obj] = item
    return normalized


def _normalize_profile_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="Invalid profile_name",
            status_code=400,
            details={},
        )
    return value.strip()


def _ensure_own_plugin_scope(
    *,
    from_plugin: str,
    target_plugin_id: str,
    message: str = "Permission denied: can only access own config",
) -> None:
    if target_plugin_id != from_plugin:
        raise ServerDomainError(
            code="PERMISSION_DENIED", message=message, status_code=403, details={}
        )


def _own_target_plugin_id(
    ctx: RpcContext,
    request: Mapping[str, object],
    message: str = "Permission denied: can only access own config",
) -> str:
    target_plugin_id = _resolve_target_plugin_id(
        request=request, from_plugin=ctx.from_plugin
    )
    _ensure_own_plugin_scope(
        from_plugin=ctx.from_plugin, target_plugin_id=target_plugin_id, message=message
    )
    return target_plugin_id


async def handle_plugin_config_get(ctx: RpcContext, request: dict[str, object]) -> object:
    target_plugin_id = _own_target_plugin_id(ctx, request)
    return await config_query_service.get_plugin_config(plugin_id=target_plugin_id)


async def handle_plugin_config_base_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(ctx, request)
    return await config_query_service.get_plugin_base_config(plugin_id=target_plugin_id)


async def handle_plugin_config_profiles_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(ctx, request)
    return await config_query_service.get_plugin_profiles_state(
        plugin_id=target_plugin_id
    )


async def handle_plugin_config_profile_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(ctx, request)
    profile_name = _normalize_profile_name(request.get("profile_name"))
    payload = await config_query_service.get_plugin_profile_config(
        plugin_id=target_plugin_id, profile_name=profile_name
    )
    return payload


async def handle_plugin_config_effective_get(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(ctx, request)
    raw_profile_name = request.get("profile_name")
    profile_name: str | None
    if raw_profile_name is None:
        profile_name = None
    else:
        profile_name = _normalize_profile_name(raw_profile_name)
    payload = await config_query_service.get_plugin_effective_config(
        plugin_id=target_plugin_id, profile_name=profile_name
    )
    return payload


@owns_deadline
async def handle_plugin_config_update(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(
        ctx, request, "Permission denied: can only update own config"
    )
    updates = _normalize_updates_payload(request.get("updates"))
    payload = await _persist_before_request_deadline(
        request_deadline=ctx.deadline,
        persist=lambda commit_guard: config_command_service.update_plugin_config(
            plugin_id=target_plugin_id, updates=updates, commit_guard=commit_guard
        ),
    )
    if payload is None:
        logger.warning(
            "PLUGIN_CONFIG_UPDATE persistence cancelled before request deadline: plugin_id={}, req_id={}",
            target_plugin_id,
            ctx.request_id,
        )
        payload = {
            "success": False,
            "plugin_id": target_plugin_id,
            "config": updates,
            "requires_reload": False,
            "persisted": False,
            "message": "Config persistence timed out; update is applied in plugin memory only",
        }
    return payload


@owns_deadline
async def handle_plugin_config_replace(
    ctx: RpcContext, request: dict[str, object]
) -> object:
    target_plugin_id = _own_target_plugin_id(
        ctx, request, "Permission denied: can only replace own config"
    )
    config = _normalize_updates_payload(request.get("config"))
    payload = await _persist_before_request_deadline(
        request_deadline=ctx.deadline,
        persist=lambda commit_guard: config_command_service.replace_plugin_config(
            plugin_id=target_plugin_id, config=config, commit_guard=commit_guard
        ),
    )
    if payload is None:
        logger.warning(
            "PLUGIN_CONFIG_REPLACE persistence cancelled before request deadline: plugin_id={}, req_id={}",
            target_plugin_id,
            ctx.request_id,
        )
        raise RpcRemoteError(
            {
                "code": "PERSISTENCE_TIMEOUT",
                "message": "Config persistence timed out; replacement was not applied",
            }
        )
    return payload
