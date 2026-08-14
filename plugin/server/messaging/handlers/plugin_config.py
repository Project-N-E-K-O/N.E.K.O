from __future__ import annotations

import asyncio
import math
import time

from collections.abc import Awaitable, Callable, Mapping

from plugin.logging_config import get_logger
from plugin.server.application.config import ConfigCommandService, ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_storage import ConfigCommitCancelled, ConfigCommitGuard
from plugin.server.messaging.handlers.common import resolve_common_fields
from plugin.server.messaging.handlers.typing import SendResponse

logger = get_logger("server.messaging.handlers.plugin_config")
config_query_service = ConfigQueryService()
config_command_service = ConfigCommandService()
_CONFIG_WRITE_PERSIST_BUDGET_SECONDS = 4.0
_CONFIG_WRITE_RESPONSE_GRACE_SECONDS = 0.25


def _consume_config_write_task(task: asyncio.Task[dict[str, object]]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _persist_before_request_deadline(
    *,
    request_deadline: float,
    persist: Callable[[ConfigCommitGuard], Awaitable[dict[str, object]]],
) -> dict[str, object] | None:
    now = time.monotonic()
    remaining = max(0.0, request_deadline - now)
    response_grace = min(
        _CONFIG_WRITE_RESPONSE_GRACE_SECONDS,
        remaining * 0.2,
    )
    commit_deadline = min(
        request_deadline - response_grace,
        now + _CONFIG_WRITE_PERSIST_BUDGET_SECONDS,
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


def _resolve_config_write_deadline(
    *,
    request: Mapping[str, object],
    timeout: float,
) -> float:
    now = time.monotonic()
    relative_deadline = now + timeout
    deadline_obj = request.get("_request_deadline_monotonic")
    if isinstance(deadline_obj, (int, float)) and not isinstance(deadline_obj, bool):
        deadline = float(deadline_obj)
        if math.isfinite(deadline):
            # Never allow a caller-supplied deadline to extend the normalized
            # server-side timeout; it may only account for time already spent
            # in the request queue.
            return min(deadline, relative_deadline)
    return relative_deadline


def _resolve_target_plugin_id(
    *,
    request: Mapping[str, object],
    from_plugin: str,
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


def _send_error(
    *,
    send_response: SendResponse,
    from_plugin: str,
    request_id: str,
    timeout: float,
    message: str,
) -> None:
    send_response(from_plugin, request_id, None, message, timeout=timeout)


def _ensure_own_plugin_scope(
    *,
    from_plugin: str,
    target_plugin_id: str,
    message: str = "Permission denied: can only access own config",
) -> None:
    if target_plugin_id != from_plugin:
        raise ServerDomainError(
            code="PERMISSION_DENIED",
            message=message,
            status_code=403,
            details={},
        )


async def handle_plugin_config_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(from_plugin=from_plugin, target_plugin_id=target_plugin_id)
        payload = await config_query_service.get_plugin_config(plugin_id=target_plugin_id)
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_GET failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_base_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(from_plugin=from_plugin, target_plugin_id=target_plugin_id)
        payload = await config_query_service.get_plugin_base_config(plugin_id=target_plugin_id)
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_BASE_GET failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_profiles_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(from_plugin=from_plugin, target_plugin_id=target_plugin_id)
        payload = await config_query_service.get_plugin_profiles_state(plugin_id=target_plugin_id)
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_PROFILES_GET failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_profile_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(from_plugin=from_plugin, target_plugin_id=target_plugin_id)
        profile_name = _normalize_profile_name(request.get("profile_name"))
        payload = await config_query_service.get_plugin_profile_config(
            plugin_id=target_plugin_id,
            profile_name=profile_name,
        )
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_PROFILE_GET failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_effective_get(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(from_plugin=from_plugin, target_plugin_id=target_plugin_id)

        raw_profile_name = request.get("profile_name")
        profile_name: str | None
        if raw_profile_name is None:
            profile_name = None
        else:
            profile_name = _normalize_profile_name(raw_profile_name)

        payload = await config_query_service.get_plugin_effective_config(
            plugin_id=target_plugin_id,
            profile_name=profile_name,
        )
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_EFFECTIVE_GET failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_update(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(
            from_plugin=from_plugin,
            target_plugin_id=target_plugin_id,
            message="Permission denied: can only update own config",
        )
        updates = _normalize_updates_payload(request.get("updates"))
        payload = await _persist_before_request_deadline(
            request_deadline=_resolve_config_write_deadline(
                request=request,
                timeout=timeout,
            ),
            persist=lambda commit_guard: config_command_service.update_plugin_config(
                plugin_id=target_plugin_id,
                updates=updates,
                commit_guard=commit_guard,
            ),
        )
        if payload is None:
            logger.warning(
                "PLUGIN_CONFIG_UPDATE persistence cancelled before request deadline: plugin_id={}, req_id={}",
                target_plugin_id,
                request_id,
            )
            payload = {
                "success": False,
                "plugin_id": target_plugin_id,
                "config": updates,
                "requires_reload": False,
                "persisted": False,
                "message": "Config persistence timed out; update is applied in plugin memory only",
            }
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_UPDATE failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )


async def handle_plugin_config_replace(request: dict[str, object], send_response: SendResponse) -> None:
    common_fields = resolve_common_fields(request)
    if common_fields is None:
        return
    from_plugin, request_id, timeout = common_fields

    try:
        target_plugin_id = _resolve_target_plugin_id(request=request, from_plugin=from_plugin)
        _ensure_own_plugin_scope(
            from_plugin=from_plugin,
            target_plugin_id=target_plugin_id,
            message="Permission denied: can only replace own config",
        )
        config = _normalize_updates_payload(request.get("config"))
        payload = await _persist_before_request_deadline(
            request_deadline=_resolve_config_write_deadline(
                request=request,
                timeout=timeout,
            ),
            persist=lambda commit_guard: config_command_service.replace_plugin_config(
                plugin_id=target_plugin_id,
                config=config,
                commit_guard=commit_guard,
            ),
        )
        if payload is None:
            logger.warning(
                "PLUGIN_CONFIG_REPLACE persistence cancelled before request deadline: plugin_id={}, req_id={}",
                target_plugin_id,
                request_id,
            )
            _send_error(
                send_response=send_response,
                from_plugin=from_plugin,
                request_id=request_id,
                timeout=timeout,
                message="Config persistence timed out; replacement was not applied",
            )
            return
        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except ServerDomainError as error:
        logger.warning("PLUGIN_CONFIG_REPLACE failed: code={}, message={}", error.code, error.message)
        _send_error(
            send_response=send_response,
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            message=error.message,
        )
