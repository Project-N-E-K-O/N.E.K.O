"""
配置管理路由
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plugin.logging_config import get_logger
from plugin.server.application.config import ConfigCommandService, ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin

router = APIRouter()
logger = get_logger("server.routes.config")
config_query_service = ConfigQueryService()
config_command_service = ConfigCommandService()


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


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/plugin/{plugin_id}/config")
async def get_plugin_config_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await config_query_service.get_plugin_config(plugin_id=plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/{plugin_id}/config/toml")
async def get_plugin_config_toml_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await config_query_service.get_plugin_config_toml(plugin_id=plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.put("/plugin/{plugin_id}/config")
async def update_plugin_config_endpoint(
    plugin_id: str,
    payload: ConfigUpdateRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.replace_plugin_config(
            plugin_id=plugin_id,
            config=payload.config,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/plugin/{plugin_id}/config/parse_toml")
async def parse_toml_to_config_endpoint(
    plugin_id: str,
    payload: ConfigTomlParseRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_query_service.parse_toml_to_config(
            plugin_id=plugin_id,
            toml=payload.toml,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/plugin/{plugin_id}/config/render_toml")
async def render_config_to_toml_endpoint(
    plugin_id: str,
    payload: ConfigTomlRenderRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_query_service.render_config_to_toml(
            plugin_id=plugin_id,
            config=payload.config,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.put("/plugin/{plugin_id}/config/toml")
async def update_plugin_config_toml_endpoint(
    plugin_id: str,
    payload: ConfigTomlUpdateRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.update_plugin_config_toml(
            plugin_id=plugin_id,
            toml=payload.toml,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/{plugin_id}/config/base")
async def get_plugin_base_config_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await config_query_service.get_plugin_base_config(plugin_id=plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/{plugin_id}/config/profiles")
async def get_plugin_profiles_state_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await config_query_service.get_plugin_profiles_state(plugin_id=plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def get_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_query_service.get_plugin_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.put("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def upsert_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    payload: ProfileConfigUpsertRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.upsert_plugin_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
            config=payload.config,
            make_active=payload.make_active,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.delete("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def delete_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.delete_plugin_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/plugin/{plugin_id}/config/profiles/{profile_name}/activate")
async def set_plugin_active_profile_endpoint(
    plugin_id: str,
    profile_name: str,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.set_plugin_active_profile(
            plugin_id=plugin_id,
            profile_name=profile_name,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/plugin/{plugin_id}/config/hot-update")
async def hot_update_plugin_config_endpoint(
    plugin_id: str,
    payload: HotUpdateConfigRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await config_command_service.hot_update_plugin_config(
            plugin_id=plugin_id,
            updates=payload.config,
            mode=payload.mode,
            profile=payload.profile,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)
