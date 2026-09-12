from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from plugin.server.application.plugins import development as store
from plugin.server.application.plugins import development_service as service
from plugin.server.application.plugin_cli.development_artifacts import resolve_development_download_sync
from plugin.server.application.plugins.operation_lock import PluginOperationBusy, bounded_operation_wait
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.development_access import require_development_access
from plugin.server.infrastructure.auth import require_admin

router = APIRouter(dependencies=[Depends(require_development_access), require_admin])


class DevelopmentSettings(BaseModel):
    enabled: bool


class DevelopmentDirectory(BaseModel):
    source_dir: str = Field(min_length=1, max_length=32768)
    preview: bool = False
    registration_id: str | None = None
    revision: int | None = Field(default=None, ge=1)


class DevelopmentRebind(BaseModel):
    source_dir: str = Field(min_length=1, max_length=32768)
    revision: int = Field(ge=1)


async def _operation(awaitable):
    try:
        with bounded_operation_wait(env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)):
            return await awaitable
    except PluginOperationBusy as exc:
        raise HTTPException(409, "Another plugin operation is in progress; retry shortly",
                            headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"}) from exc
    except ServerDomainError as exc:
        raise HTTPException(exc.status_code, exc.message, headers={"X-Error-Code": exc.code}) from exc


@router.get("/plugins/development")
async def get_development():
    return await _operation(asyncio.to_thread(service.development_view_sync))


@router.get("/plugins/development/download")
async def download_development_package(package: str = Query(min_length=1)):
    resolved = await _operation(asyncio.to_thread(resolve_development_download_sync, package))
    return FileResponse(str(resolved), filename=resolved.name, media_type="application/octet-stream")


@router.put("/plugins/development/settings")
async def put_development_settings(body: DevelopmentSettings):
    return await _operation(service.set_development_enabled(body.enabled))


@router.post("/plugins/development/registrations")
async def post_development_registration(body: DevelopmentDirectory):
    if body.preview:
        return await _operation(asyncio.to_thread(service.preview_development_sync,
            body.source_dir, body.registration_id, body.revision))
    return await _operation(service.register_development(body.source_dir))


@router.patch("/plugins/development/registrations/{registration_id}")
async def patch_development_registration(registration_id: str, body: DevelopmentRebind):
    return await _operation(service.rebind_development(registration_id, body.revision, body.source_dir))


@router.delete("/plugins/development/registrations/{registration_id}")
async def delete_development_registration(registration_id: str, revision: int = Query(ge=1)):
    return await _operation(service.remove_development(registration_id, revision))
