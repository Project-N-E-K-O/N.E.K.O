from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from plugin.logging_config import get_logger
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.plugin_cli")
service = PluginCliService()


class PluginCliPackRequest(BaseModel):
    plugin: str | None = None
    plugins: list[str] = Field(default_factory=list)
    pack_all: bool = False
    out: str | None = None
    target_dir: str | None = None
    keep_staging: bool = False
    bundle: bool = False
    bundle_id: str | None = None
    package_name: str | None = None
    package_description: str | None = None
    version: str | None = None


class PluginCliPackageRequest(BaseModel):
    package: str


class PluginCliUnpackRequest(BaseModel):
    package: str
    plugins_root: str | None = None
    profiles_root: str | None = None
    on_conflict: str = Field(default="rename", pattern="^(rename|fail)$")


class PluginCliAnalyzeRequest(BaseModel):
    plugins: list[str]
    current_sdk_version: str | None = None


@router.get("/plugin-cli/plugins")
async def list_plugin_cli_plugins(_: str = require_admin) -> dict[str, object]:
    try:
        return await service.list_local_plugins()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin-cli/packages")
async def list_plugin_cli_packages(_: str = require_admin) -> dict[str, object]:
    try:
        return await service.list_local_packages()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/pack")
async def plugin_cli_pack(
    payload: PluginCliPackRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.pack(
            plugin=payload.plugin,
            plugins=payload.plugins,
            pack_all=payload.pack_all,
            out=payload.out,
            target_dir=payload.target_dir,
            keep_staging=payload.keep_staging,
            bundle=payload.bundle,
            bundle_id=payload.bundle_id,
            package_name=payload.package_name,
            package_description=payload.package_description,
            version=payload.version,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/inspect")
async def plugin_cli_inspect(
    payload: PluginCliPackageRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.inspect(package=payload.package)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/verify")
async def plugin_cli_verify(
    payload: PluginCliPackageRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.verify(package=payload.package)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/unpack")
async def plugin_cli_unpack(
    payload: PluginCliUnpackRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.unpack(
            package=payload.package,
            plugins_root=payload.plugins_root,
            profiles_root=payload.profiles_root,
            on_conflict=payload.on_conflict,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/analyze")
async def plugin_cli_analyze(
    payload: PluginCliAnalyzeRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.analyze(
            plugins=payload.plugins,
            current_sdk_version=payload.current_sdk_version,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
