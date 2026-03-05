from __future__ import annotations

import asyncio
from collections.abc import Mapping
from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.application.config.hot_update_service import (
    hot_update_plugin_config as application_hot_update_plugin_config,
)
from plugin.server.application.config.validation import validate_config_updates
from plugin.server.domain import RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_updates import (
    replace_plugin_config as infrastructure_replace_plugin_config,
)
from plugin.server.infrastructure.config_updates import (
    update_plugin_config as infrastructure_update_plugin_config,
)
from plugin.server.infrastructure.config_updates import (
    update_plugin_config_toml as infrastructure_update_plugin_config_toml,
)
from plugin.server.infrastructure.config_profiles_write import (
    delete_profile_config as infrastructure_delete_profile_config,
)
from plugin.server.infrastructure.config_profiles_write import (
    set_active_profile as infrastructure_set_active_profile,
)
from plugin.server.infrastructure.config_profiles_write import (
    upsert_profile_config as infrastructure_upsert_profile_config,
)

logger = get_logger("server.application.config.command")

def _to_message(detail: object, *, fallback: str) -> str:
    if isinstance(detail, str) and detail:
        return detail
    return fallback


def _from_http_exception(
    error: HTTPException,
    *,
    code: str,
    fallback_message: str,
) -> ServerDomainError:
    status_code = error.status_code if isinstance(error.status_code, int) else 500
    return ServerDomainError(
        code=code,
        message=_to_message(error.detail, fallback=fallback_message),
        status_code=status_code,
        details={},
    )


def _normalize_profile_name(profile_name: object) -> str:
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="profile_name is required",
            status_code=400,
            details={},
        )
    return profile_name.strip()


def _normalize_mode(mode: object) -> str:
    if not isinstance(mode, str):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="mode must be 'temporary' or 'permanent'",
            status_code=400,
            details={},
        )
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"temporary", "permanent"}:
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="mode must be 'temporary' or 'permanent'",
            status_code=400,
            details={},
        )
    return normalized_mode


def _normalize_config_updates(payload: object) -> dict[str, object]:
    return validate_config_updates(updates=payload)


def _normalize_make_active(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ServerDomainError(
        code="INVALID_ARGUMENT",
        message="make_active must be a boolean",
        status_code=400,
        details={},
    )


def _normalize_payload(payload: object, *, context: str) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ServerDomainError(
            code="INVALID_DATA_SHAPE",
            message=f"{context} returned invalid payload",
            status_code=500,
            details={"payload_type": type(payload).__name__},
        )

    normalized: dict[str, object] = {}
    for key_obj, value in payload.items():
        if not isinstance(key_obj, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} returned non-string key",
                status_code=500,
                details={"key_type": type(key_obj).__name__},
            )
        normalized[key_obj] = value
    return normalized


class ConfigCommandService:
    async def replace_plugin_config(self, *, plugin_id: str, config: object) -> dict[str, object]:
        normalized_config = _normalize_config_updates(config)
        try:
            payload = await asyncio.to_thread(
                infrastructure_replace_plugin_config,
                plugin_id,
                normalized_config,
            )
            return _normalize_payload(payload, context="replace_plugin_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_REPLACE_FAILED",
                fallback_message="Failed to replace plugin config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "replace_plugin_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_REPLACE_FAILED",
                message="Failed to replace plugin config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def update_plugin_config(self, *, plugin_id: str, updates: object) -> dict[str, object]:
        normalized_updates = _normalize_config_updates(updates)
        try:
            payload = await asyncio.to_thread(
                infrastructure_update_plugin_config,
                plugin_id,
                normalized_updates,
            )
            return _normalize_payload(payload, context="update_plugin_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_UPDATE_FAILED",
                fallback_message="Failed to update plugin config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "update_plugin_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_UPDATE_FAILED",
                message="Failed to update plugin config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def update_plugin_config_toml(self, *, plugin_id: str, toml: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(
                infrastructure_update_plugin_config_toml,
                plugin_id,
                toml,
            )
            return _normalize_payload(payload, context="update_plugin_config_toml")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_TOML_UPDATE_FAILED",
                fallback_message="Failed to update plugin toml config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "update_plugin_config_toml failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_TOML_UPDATE_FAILED",
                message="Failed to update plugin toml config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def upsert_plugin_profile_config(
        self,
        *,
        plugin_id: str,
        profile_name: object,
        config: object,
        make_active: object,
    ) -> dict[str, object]:
        normalized_profile_name = _normalize_profile_name(profile_name)
        normalized_config = _normalize_config_updates(config)
        normalized_make_active = _normalize_make_active(make_active)
        try:
            payload = await asyncio.to_thread(
                infrastructure_upsert_profile_config,
                plugin_id=plugin_id,
                profile_name=normalized_profile_name,
                config=normalized_config,
                make_active=normalized_make_active,
            )
            return _normalize_payload(payload, context="upsert_plugin_profile_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_PROFILE_UPSERT_FAILED",
                fallback_message="Failed to upsert plugin profile config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "upsert_plugin_profile_config failed: plugin_id={}, profile_name={}, err_type={}, err={}",
                plugin_id,
                normalized_profile_name,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_PROFILE_UPSERT_FAILED",
                message="Failed to upsert plugin profile config",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "profile_name": normalized_profile_name,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def delete_plugin_profile_config(
        self,
        *,
        plugin_id: str,
        profile_name: object,
    ) -> dict[str, object]:
        normalized_profile_name = _normalize_profile_name(profile_name)
        try:
            payload = await asyncio.to_thread(
                infrastructure_delete_profile_config,
                plugin_id=plugin_id,
                profile_name=normalized_profile_name,
            )
            return _normalize_payload(payload, context="delete_plugin_profile_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_PROFILE_DELETE_FAILED",
                fallback_message="Failed to delete plugin profile config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "delete_plugin_profile_config failed: plugin_id={}, profile_name={}, err_type={}, err={}",
                plugin_id,
                normalized_profile_name,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_PROFILE_DELETE_FAILED",
                message="Failed to delete plugin profile config",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "profile_name": normalized_profile_name,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def set_plugin_active_profile(
        self,
        *,
        plugin_id: str,
        profile_name: object,
    ) -> dict[str, object]:
        normalized_profile_name = _normalize_profile_name(profile_name)
        try:
            payload = await asyncio.to_thread(
                infrastructure_set_active_profile,
                plugin_id=plugin_id,
                profile_name=normalized_profile_name,
            )
            return _normalize_payload(payload, context="set_plugin_active_profile")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_PROFILE_ACTIVATE_FAILED",
                fallback_message="Failed to set active profile",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "set_plugin_active_profile failed: plugin_id={}, profile_name={}, err_type={}, err={}",
                plugin_id,
                normalized_profile_name,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_PROFILE_ACTIVATE_FAILED",
                message="Failed to set active profile",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "profile_name": normalized_profile_name,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def hot_update_plugin_config(
        self,
        *,
        plugin_id: str,
        updates: object,
        mode: object,
        profile: object,
    ) -> dict[str, object]:
        normalized_updates = _normalize_config_updates(updates)
        normalized_mode = _normalize_mode(mode)
        normalized_profile: str | None
        if profile is None:
            normalized_profile = None
        else:
            normalized_profile = _normalize_profile_name(profile)

        try:
            payload = await application_hot_update_plugin_config(
                plugin_id=plugin_id,
                updates=normalized_updates,
                mode=normalized_mode,
                profile=normalized_profile,
            )
            return _normalize_payload(payload, context="hot_update_plugin_config")
        except ServerDomainError:
            raise
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_HOT_UPDATE_FAILED",
                fallback_message="Failed to hot update plugin config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "hot_update_plugin_config failed: plugin_id={}, mode={}, err_type={}, err={}",
                plugin_id,
                normalized_mode,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_HOT_UPDATE_FAILED",
                message="Failed to hot update plugin config",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "mode": normalized_mode,
                    "error_type": type(exc).__name__,
                },
            ) from exc
