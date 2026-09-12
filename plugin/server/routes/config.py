"""
配置管理路由
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from plugin.logging_config import get_logger
from plugin.core.state import state
from plugin import settings
from plugin.server.application.config import ConfigCommandService, ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.config_paths import get_plugin_config_path
from plugin.server.infrastructure.config_access import ConfigAccessSnapshot, bind_config_access
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.infrastructure.development_access import require_development_access
from plugin.server.application.plugins.development import registration_for_plugin_sync
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy, bounded_operation_wait, serialized_plugin_operation,
)

router = APIRouter()
logger = get_logger("server.routes.config")
config_query_service = ConfigQueryService()
config_command_service = ConfigCommandService()


def _ordinary_config_snapshot_sync(plugin_id: str) -> ConfigAccessSnapshot | None:
    if registration_for_plugin_sync(plugin_id) is not None:
        return None
    path = get_plugin_config_path(plugin_id).resolve()
    if not any(path.parent.parent == Path(root).resolve() for root in settings.PLUGIN_CONFIG_ROOTS):
        return None
    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(plugin_id)
        if host is not None and Path(getattr(host, "config_path", "")).resolve() != path:
            return None
    return ConfigAccessSnapshot(plugin_id=plugin_id, manifest_path=path, host=host)


@serialized_plugin_operation
async def _dispatch_config_locked(request: Request, action: Callable[..., Awaitable[dict[str, object]]],
                                  *, plugin_id: str, **kwargs: object) -> dict[str, object]:
    # Fence source selection and the complete operation against rebinding,
    # including config writers that finish in a worker after cancellation.
    registration = await asyncio.to_thread(registration_for_plugin_sync, plugin_id)
    supplied_id = request.query_params.get("registration_id")
    require_development_access(request)
    if (registration is None or registration.registration_id != supplied_id
            or str(registration.revision) != request.query_params.get("revision")):
        raise ServerDomainError(code="DEVELOPMENT_STALE", status_code=409,
            message="Development registration changed; refresh and retry")
    source_matches = await asyncio.to_thread(
        lambda: get_plugin_config_path(plugin_id) == (registration.source_dir / "plugin.toml").resolve()
    )
    if not source_matches:
        raise ServerDomainError(code="DEVELOPMENT_STALE", status_code=409,
            message="Development metadata changed; refresh and retry")
    return await action(plugin_id=plugin_id, **kwargs)


async def _dispatch_config(request: Request, action: Callable[..., Awaitable[dict[str, object]]],
                           *, plugin_id: str, **kwargs: object) -> dict[str, object]:
    if request.query_params.get("registration_id") is None:
        snapshot = await asyncio.to_thread(_ordinary_config_snapshot_sync, plugin_id)
        if snapshot is not None:
            # Keep every worker on this managed source/host even if an uninstall
            # and development registration reuse the ID across an await.
            with bind_config_access(snapshot):
                return await action(plugin_id=plugin_id, **kwargs)
    try:
        with bounded_operation_wait(env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)):
            return await _dispatch_config_locked(request, action, plugin_id=plugin_id, **kwargs)
    except PluginOperationBusy as exc:
        raise HTTPException(409, "Another plugin operation is in progress; retry shortly",
                            headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"}) from exc


class ConfigUpdateRequest(BaseModel):
    config: dict[str, object]


class ConfigTomlUpdateRequest(BaseModel):
    toml: str


class ConfigTomlParseRequest(BaseModel):
    toml: str


class ConfigTomlRenderRequest(BaseModel):
    config: dict[str, object]


class ProfileConfigUpsertRequest(BaseModel):
    config: dict[str, object]
    make_active: bool | None = None


class HotUpdateConfigRequest(BaseModel):
    config: dict[str, object]
    mode: str = "temporary"
    profile: str | None = None


@router.get("/plugin/{plugin_id}/config")
async def get_plugin_config_endpoint(plugin_id: str, request: Request, _: str = require_admin) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_config, plugin_id=plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/{plugin_id}/config/toml")
async def get_plugin_config_toml_endpoint(plugin_id: str, request: Request, _: str = require_admin) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_config_toml, plugin_id=plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.put("/plugin/{plugin_id}/config")
async def update_plugin_config_endpoint(
    plugin_id: str,
    payload: ConfigUpdateRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.replace_plugin_config,
            plugin_id=plugin_id,
            config=payload.config,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/config/parse_toml")
async def parse_toml_to_config_endpoint(
    plugin_id: str,
    payload: ConfigTomlParseRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.parse_toml_to_config,
            plugin_id=plugin_id,
            toml=payload.toml,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/config/render_toml")
async def render_config_to_toml_endpoint(
    plugin_id: str,
    payload: ConfigTomlRenderRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.render_config_to_toml,
            plugin_id=plugin_id,
            config=payload.config,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.put("/plugin/{plugin_id}/config/toml")
async def update_plugin_config_toml_endpoint(
    plugin_id: str,
    payload: ConfigTomlUpdateRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.update_plugin_config_toml,
            plugin_id=plugin_id,
            toml=payload.toml,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/{plugin_id}/config/base")
async def get_plugin_base_config_endpoint(plugin_id: str, request: Request, _: str = require_admin) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_base_config, plugin_id=plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/{plugin_id}/config/base/effective")
async def get_plugin_effective_base_config_endpoint(
    plugin_id: str,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    """Return manifest defaults merged with runtime config, without a profile overlay."""
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_effective_base_config, plugin_id=plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/{plugin_id}/config/profiles")
async def get_plugin_profiles_state_endpoint(plugin_id: str, request: Request, _: str = require_admin) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_profiles_state, plugin_id=plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def get_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_query_service.get_plugin_profile_config,
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.put("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def upsert_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    payload: ProfileConfigUpsertRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.upsert_plugin_profile_config,
            plugin_id=plugin_id,
            profile_name=profile_name,
            config=payload.config,
            make_active=payload.make_active,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.delete("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def delete_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.delete_plugin_profile_config,
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/config/profiles/{profile_name}/activate")
async def set_plugin_active_profile_endpoint(
    plugin_id: str,
    profile_name: str,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.set_plugin_active_profile,
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/config/hot-update")
async def hot_update_plugin_config_endpoint(
    plugin_id: str,
    payload: HotUpdateConfigRequest,
    request: Request,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await _dispatch_config(request, config_command_service.hot_update_plugin_config,
            plugin_id=plugin_id,
            updates=payload.config,
            mode=payload.mode,
            profile=payload.profile,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
