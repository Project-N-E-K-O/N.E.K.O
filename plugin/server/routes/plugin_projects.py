"""Local-only scaffold preview and creation. No plugin registration or execution."""

from __future__ import annotations

import asyncio
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from plugin.neko_plugin_cli.core.project import PLUGIN_ID_PATTERN, ProjectCreationError
from plugin.server.application.plugin_projects import create_or_preview
from plugin.server.infrastructure.development_access import require_development_access
from plugin.server.infrastructure.native_directory import (
    DirectoryPickerCancelled,
    NativeDirectoryError,
    open_directory,
    pick_directory,
)

router = APIRouter(
    prefix="/plugin-projects", dependencies=[Depends(require_development_access)]
)


class ProjectIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    plugin_id: str = Field(pattern=PLUGIN_ID_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=250)
    author: str = Field(default="", max_length=80)
    plugin_type: Literal["plugin", "adapter"] = "plugin"


class ProjectRequest(ProjectIdentity):
    parent_dir: str = Field(min_length=1, max_length=4096)


def _coded_error(status: int, code: str, exc: Exception) -> NoReturn:
    # The UI localizes by ``code``; ``message`` is an English diagnostic.
    raise HTTPException(status, {"code": code, "message": str(exc)}) from exc


async def _project(body: ProjectRequest, *, create: bool):
    try:
        return await asyncio.to_thread(
            create_or_preview,
            create=create,
            **body.model_dump(),
        )
    except FileExistsError as exc:
        _coded_error(409, "PROJECT_EXISTS", exc)
    except ProjectCreationError as exc:
        _coded_error(409 if exc.code == "PROJECT_EXISTS" else 400, exc.code, exc)
    except OSError as exc:
        _coded_error(400, "PROJECT_IO_FAILED", exc)


@router.post("/preview")
async def preview_project(body: ProjectRequest):
    return await _project(body, create=False)


@router.post("/create", status_code=201)
async def create_plugin_project(body: ProjectRequest):
    return await _project(body, create=True)


class DirectoryRequest(BaseModel):
    path: str = Field(default="", max_length=4096)


class PickDirectoryRequest(BaseModel):
    start_path: str = Field(default="", max_length=4096)
    title: str = Field(default="", max_length=200)


@router.post("/pick-directory")
async def pick_project_directory(body: PickDirectoryRequest):
    # The dialog opens on the machine running this server; development access
    # is limited to local requests, so that is the user's own desktop.
    try:
        selected = await asyncio.to_thread(
            pick_directory, start_path=body.start_path, title=body.title
        )
    except DirectoryPickerCancelled:
        return {"ok": True, "cancelled": True, "selected_root": ""}
    except NativeDirectoryError as exc:
        _coded_error(400, exc.code, exc)
    return {"ok": True, "cancelled": False, "selected_root": selected}


@router.post("/open-directory")
async def open_project_directory(body: DirectoryRequest):
    try:
        await asyncio.to_thread(open_directory, body.path)
    except NativeDirectoryError as exc:
        _coded_error(400, exc.code, exc)
    return {"ok": True}