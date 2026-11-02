"""Local-only scaffold preview and creation. No plugin registration or execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from plugin.neko_plugin_cli.core.project import ProjectCreationError
from plugin.server.application.plugin_projects import browser_project, create_or_preview
from plugin.server.infrastructure.development_access import require_development_access

router = APIRouter(
    prefix="/plugin-projects", dependencies=[Depends(require_development_access)]
)


class ProjectIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=250)
    author: str = Field(default="", max_length=80)
    plugin_type: Literal["plugin", "adapter"] = "plugin"


class ProjectRequest(ProjectIdentity):
    parent_dir: str = Field(min_length=1, max_length=4096)


async def _project(body: ProjectIdentity, *, create: bool, browser: bool = False):
    try:
        return await asyncio.to_thread(
            browser_project if browser else create_or_preview,
            create=create,
            **body.model_dump(),
        )
    except FileExistsError as exc:
        raise HTTPException(
            409, {"code": "PROJECT_EXISTS", "message": str(exc)}
        ) from exc
    except ProjectCreationError as exc:
        raise HTTPException(
            409 if exc.code == "PROJECT_EXISTS" else 400,
            {"code": exc.code, "message": str(exc)},
        ) from exc
    except OSError as exc:
        raise HTTPException(
            400, {"code": "PROJECT_IO_FAILED", "message": str(exc)}
        ) from exc


@router.post("/preview")
async def preview_project(body: ProjectRequest):
    return await _project(body, create=False)


@router.post("/create", status_code=201)
async def create_plugin_project(body: ProjectRequest):
    return await _project(body, create=True)


@router.post("/browser/preview")
async def preview_browser_project(body: ProjectIdentity):
    return await _project(body, create=False, browser=True)


@router.post("/browser/bundle")
async def bundle_browser_project(body: ProjectIdentity):
    return await _project(body, create=True, browser=True)


class DirectoryRequest(BaseModel):
    path: str = Field(default="", max_length=4096)


@router.post("/open-directory")
async def open_project_directory(body: DirectoryRequest):
    from utils.directory_opener import DirectoryOpenError, open_directory

    path = Path(body.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(
            400, {"code": "INVALID_PARENT", "message": "Use an absolute directory"}
        )
    try:
        await asyncio.to_thread(open_directory, path)
        return {"ok": True}
    except DirectoryOpenError as exc:
        raise HTTPException(
            400, {"code": exc.error_code, "message": exc.message}
        ) from exc
