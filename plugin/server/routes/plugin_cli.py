from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from plugin.logging_config import get_logger
from plugin.server.application.plugin_cli import PluginCliService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.plugin_cli")
service = PluginCliService()


class PluginCliPackRequest(BaseModel):
    mode: str = Field(default="selected", pattern="^(selected|single|bundle|all)$")
    plugin: str | None = None
    plugins: list[str] = Field(default_factory=list)
    out: str | None = None
    target_dir: str | None = None
    keep_staging: bool = False
    bundle_id: str | None = None
    package_name: str | None = None
    package_description: str | None = None
    version: str | None = None

    @model_validator(mode="after")
    def _validate_mode_payload(self) -> "PluginCliPackRequest":
        if self.mode == "single" and not self.plugin:
            raise ValueError("plugin is required when mode=single")
        if self.mode in {"selected", "bundle"} and not self.plugins:
            raise ValueError("plugins is required when mode=selected or mode=bundle")
        return self


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
            mode=payload.mode,
            plugin=payload.plugin,
            plugins=payload.plugins,
            out=payload.out,
            target_dir=payload.target_dir,
            keep_staging=payload.keep_staging,
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


# ── Upload & Download ──────────────────────────────────────────────────


@router.post("/plugin-cli/upload")
async def plugin_cli_upload(
    file: UploadFile = File(...),
    _: str = require_admin,
) -> dict[str, object]:
    """Upload a plugin package file (.neko-plugin / .neko-bundle) to the server.

    The file is saved to the packages target directory and can subsequently be
    passed to ``/plugin-cli/unpack`` or ``/plugin-cli/inspect``.
    """
    try:
        content = await file.read()
        return await service.save_uploaded_package(
            filename=file.filename or "unknown.neko-plugin",
            content=content,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    except Exception:
        logger.exception("Unexpected error during plugin package upload")
        raise HTTPException(status_code=500, detail="Internal server error during upload")


@router.post("/plugin-cli/upload-and-unpack")
async def plugin_cli_upload_and_unpack(
    file: UploadFile = File(...),
    on_conflict: str = Query(default="rename", pattern="^(rename|fail)$"),
    _: str = require_admin,
) -> dict[str, object]:
    """Upload a plugin package and immediately unpack (install) it.

    Combines upload + unpack into a single request for convenience.
    """
    try:
        content = await file.read()
        return await service.upload_and_unpack(
            filename=file.filename or "unknown.neko-plugin",
            content=content,
            on_conflict=on_conflict,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    except Exception:
        logger.exception("Unexpected error during plugin package upload-and-unpack")
        raise HTTPException(status_code=500, detail="Internal server error during upload-and-unpack")


@router.get("/plugin-cli/download")
async def plugin_cli_download(
    package: str = Query(..., description="Package filename or path within the target directory"),
    _: str = require_admin,
) -> FileResponse:
    """Download a plugin package file from the server."""
    try:
        resolved = service.resolve_download_path(package)
        return FileResponse(
            str(resolved),
            filename=resolved.name,
            media_type="application/octet-stream",
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
