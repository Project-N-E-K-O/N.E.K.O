from __future__ import annotations

from collections.abc import Mapping

from plugin.logging_config import get_logger
from plugin.server.application.config import ConfigCommandService, ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.requests.common import resolve_common_fields
from plugin.server.requests.typing import SendResponse

logger = get_logger("server.requests.plugin_config")
config_query_service = ConfigQueryService()
config_command_service = ConfigCommandService()

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
        payload = await config_command_service.update_plugin_config(
            plugin_id=target_plugin_id,
            updates=updates,
        )
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
