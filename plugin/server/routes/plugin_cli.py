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


class PluginCliPluginRef(BaseModel):
    root_id: str = Field(pattern="^(builtin|user)$")
    directory_name: str


class PluginCliPluginRefResponse(PluginCliPluginRef):
    plugin_id: str = ""
    label: str = ""


class PluginCliBuildRequest(BaseModel):
    mode: str = Field(default="selected", pattern="^(selected|single|bundle|all)$")
    plugin: str | None = None
    plugins: list[str] = Field(default_factory=list)
    plugin_ref: PluginCliPluginRef | None = None
    plugin_refs: list[PluginCliPluginRef] = Field(default_factory=list)
    out: str | None = None
    target_dir: str | None = None
    keep_staging: bool = False
    bundle_id: str | None = None
    package_name: str | None = None
    package_description: str | None = None
    version: str | None = None

    @model_validator(mode="after")
    def _validate_mode_payload(self) -> "PluginCliBuildRequest":
        if self.mode == "single" and not (self.plugin_ref or self.plugin):
            raise ValueError("plugin_ref or plugin is required when mode=single")
        if self.mode in {"selected", "bundle"} and not (self.plugin_refs or self.plugins):
            raise ValueError("plugin_refs or plugins is required when mode=selected or mode=bundle")
        return self


class PluginCliPackageRequest(BaseModel):
    package: str


class PluginCliInstallRequest(BaseModel):
    package: str
    plugins_root: str | None = None
    profiles_root: str | None = None
    on_conflict: str = Field(default="fail", pattern="^fail$")


class PluginCliAnalyzeRequest(BaseModel):
    plugins: list[str] = Field(default_factory=list)
    plugin_refs: list[PluginCliPluginRef] = Field(default_factory=list)
    current_sdk_version: str | None = None


class PluginCliPluginListResponse(BaseModel):
    plugins: list[str]
    plugin_refs: list[PluginCliPluginRefResponse] = Field(default_factory=list)
    count: int


class PluginCliLocalPackageItem(BaseModel):
    name: str
    path: str
    suffix: str
    size_bytes: int
    modified_at: str


class PluginCliPackageListResponse(BaseModel):
    packages: list[PluginCliLocalPackageItem]
    count: int
    target_dir: str


class PluginCliBuildFailure(BaseModel):
    plugin: str
    error: str


class PluginCliBuildResultResponse(BaseModel):
    plugin_id: str
    package_type: str
    plugin_ids: list[str]
    package_name: str = ""
    version: str = ""
    package_path: str
    staging_dir: str | None = None
    profile_files: list[str]
    staged_files: list[str]
    payload_hash: str
    package_size_bytes: int
    staged_file_count: int
    profile_file_count: int
    plugin_count: int


class PluginCliBuildResponse(BaseModel):
    built: list[PluginCliBuildResultResponse]
    built_count: int
    failed: list[PluginCliBuildFailure]
    failed_count: int
    ok: bool


class PluginCliInspectedPluginResponse(BaseModel):
    plugin_id: str
    archive_path: str
    has_plugin_toml: bool


class PluginCliDependencyPluginResponse(BaseModel):
    plugin_id: str
    python_requirements: list[str]
    host_python_requirements: list[str]
    plugin_dependencies: list[str]
    advanced_plugin_dependencies: list[dict[str, object]]
    vendor_path: str = ""
    vendor_present: bool = False


class PluginCliDependencySummaryResponse(BaseModel):
    schema_version: str = ""
    plugins: list[PluginCliDependencyPluginResponse]
    plugin_count: int


class PluginCliInspectResponse(BaseModel):
    package_path: str
    package_type: str
    package_id: str
    schema_version: str = ""
    package_name: str = ""
    package_description: str = ""
    version: str = ""
    metadata_found: bool
    payload_hash: str = ""
    payload_hash_verified: bool | None = None
    plugins: list[PluginCliInspectedPluginResponse]
    profile_names: list[str]
    plugin_count: int
    profile_count: int
    dependencies: PluginCliDependencySummaryResponse | None = None


class PluginCliVerifyResponse(PluginCliInspectResponse):
    ok: bool


class PluginCliInstalledPluginResponse(BaseModel):
    source_folder: str
    target_plugin_id: str
    target_dir: str
    renamed: bool


class PluginCliInstallResponse(BaseModel):
    package_path: str
    package_type: str
    package_id: str
    plugins_root: str
    profiles_root: str | None = None
    installed_plugins: list[PluginCliInstalledPluginResponse]
    profile_dir: str | None = None
    metadata_found: bool
    payload_hash: str = ""
    payload_hash_verified: bool | None = None
    conflict_strategy: str
    installed_plugin_count: int


class PluginCliSharedDependencyResponse(BaseModel):
    name: str
    plugin_ids: list[str]
    requirement_texts: dict[str, str]
    plugin_count: int


class PluginCliBundleSdkAnalysisResponse(BaseModel):
    kind: str
    plugin_specifiers: dict[str, str]
    has_overlap: bool
    matching_versions: list[str]
    current_sdk_version: str = ""
    current_sdk_supported_by_all: bool | None = None


class PluginCliAnalyzeResponse(BaseModel):
    plugin_ids: list[str]
    shared_dependencies: list[PluginCliSharedDependencyResponse]
    common_dependencies: list[PluginCliSharedDependencyResponse]
    sdk_supported_analysis: PluginCliBundleSdkAnalysisResponse | None = None
    sdk_recommended_analysis: PluginCliBundleSdkAnalysisResponse | None = None
    plugin_count: int


class PluginCliUploadResponse(BaseModel):
    name: str
    path: str
    size_bytes: int
    modified_at: str


class PluginCliUploadAndInstallResponse(BaseModel):
    upload: PluginCliUploadResponse
    install: PluginCliInstallResponse
    install_source_warning: str | None = None


@router.get("/plugin-cli/plugins", response_model=PluginCliPluginListResponse)
async def list_plugin_cli_plugins(_: str = require_admin) -> dict[str, object]:
    try:
        return await service.list_local_plugins()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin-cli/packages", response_model=PluginCliPackageListResponse)
async def list_plugin_cli_packages(_: str = require_admin) -> dict[str, object]:
    try:
        return await service.list_local_packages()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/build", response_model=PluginCliBuildResponse)
async def plugin_cli_build(
    payload: PluginCliBuildRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.build(
            mode=payload.mode,
            plugin=payload.plugin,
            plugins=payload.plugins,
            plugin_ref=payload.plugin_ref.model_dump() if payload.plugin_ref else None,
            plugin_refs=[item.model_dump() for item in payload.plugin_refs],
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


@router.post("/plugin-cli/inspect", response_model=PluginCliInspectResponse)
async def plugin_cli_inspect(
    payload: PluginCliPackageRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.inspect(package=payload.package)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/verify", response_model=PluginCliVerifyResponse)
async def plugin_cli_verify(
    payload: PluginCliPackageRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.verify(package=payload.package)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/install", response_model=PluginCliInstallResponse)
async def plugin_cli_install(
    payload: PluginCliInstallRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.install(
            package=payload.package,
            plugins_root=payload.plugins_root,
            profiles_root=payload.profiles_root,
            on_conflict=payload.on_conflict,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin-cli/analyze", response_model=PluginCliAnalyzeResponse)
async def plugin_cli_analyze(
    payload: PluginCliAnalyzeRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await service.analyze(
            plugins=payload.plugins,
            plugin_refs=[item.model_dump() for item in payload.plugin_refs],
            current_sdk_version=payload.current_sdk_version,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


# ── Upload & Download ──────────────────────────────────────────────────


@router.post("/plugin-cli/upload", response_model=PluginCliUploadResponse)
async def plugin_cli_upload(
    file: UploadFile = File(...),
    _: str = require_admin,
) -> dict[str, object]:
    """Upload a plugin package file (.neko-plugin / .neko-bundle) to the server.

    The file is saved to the packages target directory and can subsequently be
    passed to ``/plugin-cli/install`` or ``/plugin-cli/inspect``.
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


@router.post("/plugin-cli/upload-and-install", response_model=PluginCliUploadAndInstallResponse)
async def plugin_cli_upload_and_install(
    file: UploadFile = File(...),
    on_conflict: str = Query(default="fail", pattern="^fail$"),
    _: str = require_admin,
) -> dict[str, object]:
    """Upload a plugin package and immediately install it.

    Combines upload + install into a single request for convenience.
    """
    try:
        content = await file.read()
        return await service.upload_and_install(
            filename=file.filename or "unknown.neko-plugin",
            content=content,
            on_conflict=on_conflict,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    except Exception:
        logger.exception("Unexpected error during plugin package upload-and-install")
        raise HTTPException(status_code=500, detail="Internal server error during upload-and-install")


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


# ── Legacy route aliases (backward compatibility with existing frontend) ──


@router.post("/plugin-cli/pack", include_in_schema=False)
async def plugin_cli_pack_legacy(
    payload: PluginCliBuildRequest,
    _: str = require_admin,
) -> dict[str, object]:
    """Legacy alias for /plugin-cli/build. Translates response keys."""
    result = await plugin_cli_build(payload, _)
    # Translate new keys to legacy keys expected by frontend
    if isinstance(result, dict):
        translated = dict(result)
        if "built" in translated:
            translated["packed"] = translated.pop("built")
        if "built_count" in translated:
            translated["packed_count"] = translated.pop("built_count")
        return translated
    return result


@router.post("/plugin-cli/unpack", include_in_schema=False)
async def plugin_cli_unpack_legacy(
    payload: PluginCliInstallRequest,
    _: str = require_admin,
) -> dict[str, object]:
    """Legacy alias for /plugin-cli/install. Translates response keys."""
    result = await plugin_cli_install(payload, _)
    # Translate new keys to legacy keys expected by frontend
    if isinstance(result, dict):
        translated = dict(result)
        if "installed_plugins" in translated:
            translated["unpacked_plugins"] = translated.pop("installed_plugins")
        if "installed_plugin_count" in translated:
            translated["unpacked_plugin_count"] = translated.pop("installed_plugin_count")
        return translated
    return result


@router.post("/plugin-cli/upload-and-unpack", include_in_schema=False)
async def plugin_cli_upload_and_unpack_legacy(
    file: UploadFile = File(...),
    on_conflict: str = Query(default="fail", pattern="^fail$"),
    _: str = require_admin,
) -> dict[str, object]:
    """Legacy alias for /plugin-cli/upload-and-install. Translates response keys."""
    result = await plugin_cli_upload_and_install(file, on_conflict=on_conflict, _=_)
    # Translate nested install keys
    if isinstance(result, dict) and isinstance(result.get("install"), dict):
        install = dict(result["install"])
        if "installed_plugins" in install:
            install["unpacked_plugins"] = install.pop("installed_plugins")
        if "installed_plugin_count" in install:
            install["unpacked_plugin_count"] = install.pop("installed_plugin_count")
        result = {key: value for key, value in result.items() if key != "install"}
        result["unpack"] = install
    return result
