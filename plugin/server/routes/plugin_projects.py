"""Plugin workbench: create and register the external source projects of the runtime."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from plugin.neko_plugin_cli.core.project import PLUGIN_ID_PATTERN
from plugin.server.application import plugin_projects as service
from plugin.server.application import plugin_publish_status as publish_status
from plugin.server.application import plugin_publish_tasks as publish_tasks
from plugin.server.application import plugin_repo_tasks as repo_tasks
from plugin.server.application import plugin_toolchain as toolchain
from plugin.server.application.plugin_projects import (
    AuthoringProject,
    CreatedProject,
    ProjectIdentity,
    ProjectMetadata,
    ProjectPreview,
)
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import bounded_operation_wait
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.mutation_auth import PluginMutationGuardedRoute

router = APIRouter(prefix="/plugin-projects", dependencies=[require_admin])
mutation_router = APIRouter(route_class=PluginMutationGuardedRoute)


class ProjectIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    plugin_id: str = Field(pattern=PLUGIN_ID_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=250)
    author: str = Field(default="", max_length=80)
    plugin_type: Literal["plugin", "adapter"] = "plugin"

    def identity(self) -> ProjectIdentity:
        return ProjectIdentity(
            plugin_id=self.plugin_id,
            name=self.name,
            description=self.description,
            author=self.author,
            plugin_type=self.plugin_type,
        )


class ProjectRequest(ProjectIdentityRequest):
    parent_dir: str = Field(min_length=1, max_length=4096)


class RegisterRequest(BaseModel):
    source_dir: str = Field(min_length=1, max_length=4096)


class ProjectMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=64)
    author: str = Field(max_length=80)
    description: str = Field(max_length=250)


class DirectoryRequest(BaseModel):
    path: str = Field(default="", max_length=4096)


class PickDirectoryRequest(BaseModel):
    start_path: str = Field(default="", max_length=4096)
    title: str = Field(default="", max_length=200)


class PickDirectoryResponse(BaseModel):
    ok: bool = True
    cancelled: bool
    selected_root: str


class OkResponse(BaseModel):
    ok: bool = True


class PublishRequest(BaseModel):
    """Destination of one background publish run; the server enforces readiness."""

    mode: publish_tasks.PublishMode


class CreateRepoRequest(BaseModel):
    """Options for creating the project's GitHub repository through the CLI."""

    model_config = ConfigDict(extra="forbid")
    private: bool = False


async def _call(awaitable):
    # The UI localizes by ``code``; ``message`` is an English diagnostic.
    try:
        with bounded_operation_wait(
            env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)
        ):
            return await awaitable
    except ServerDomainError as exc:
        raise HTTPException(
            exc.status_code, {"code": exc.code, "message": exc.message}
        ) from exc


@router.get("", response_model=list[AuthoringProject])
async def list_plugin_projects():
    return await _call(asyncio.to_thread(service.list_projects_sync))


@mutation_router.post("/preview", response_model=ProjectPreview)
async def preview_project(body: ProjectRequest):
    return await _call(
        asyncio.to_thread(service.preview_project, body.parent_dir, body.identity())
    )


@mutation_router.post("/create", status_code=201, response_model=CreatedProject)
async def create_plugin_project(body: ProjectRequest):
    return await _call(
        service.create_and_register_project(body.parent_dir, body.identity())
    )


@mutation_router.post("/register", status_code=201, response_model=AuthoringProject)
async def register_plugin_project(body: RegisterRequest):
    return await _call(service.register_project(body.source_dir))


@mutation_router.put("/{project_id}/metadata", response_model=AuthoringProject)
async def update_project_metadata(project_id: str, body: ProjectMetadataRequest):
    return await _call(service.update_project_metadata(project_id, ProjectMetadata(**body.model_dump())))


@mutation_router.post("/pick-directory", response_model=PickDirectoryResponse)
async def pick_project_directory(body: PickDirectoryRequest):
    # The dialog opens on the desktop of the machine running this server.
    selected = await _call(
        asyncio.to_thread(service.pick_project_directory, body.start_path, body.title)
    )
    return PickDirectoryResponse(cancelled=selected is None, selected_root=selected or "")


@mutation_router.post("/open-directory", response_model=OkResponse)
async def open_project_directory(body: DirectoryRequest):
    await _call(asyncio.to_thread(service.open_project_directory, body.path))
    return OkResponse()


@mutation_router.post("/{project_id}/precheck")
async def precheck_plugin_project(project_id: str):
    # The CLI runs the Market CI gates; the report passes through verbatim.
    return await _call(service.precheck_project(project_id))


@router.get("/{project_id}/toolchain")
async def plugin_project_toolchain(project_id: str):
    # Read-only and local: which of git, uv, gh are installed, the git identity
    # a commit in this project gets, and whether gh is signed in. Never returns
    # a token, only whether one is stored.
    return await _call(asyncio.to_thread(toolchain.read_project_toolchain, project_id))


@router.get("/{project_id}/publish-status")
async def plugin_project_publish_status(project_id: str, fresh: bool = False):
    # Read-only: local git facts plus public GitHub reads; nothing is pushed.
    # `fresh=1` skips the cached GitHub answer (an explicit Refresh).
    return await _call(publish_status.get_publish_status(project_id, fresh=fresh))


@mutation_router.post("/{project_id}/create-repo")
async def create_plugin_project_repo(project_id: str, body: CreateRepoRequest, response: Response):
    # Runs `neko-plugin create-repo` (gh); on no gh the result carries manual steps.
    # 201 only when a repository was actually created; a `manual` or `failed`
    # outcome stays 200 so its structured body (status + log) still reaches the
    # UI instead of being turned into an error with no detail.
    result = await _call(repo_tasks.create_repository(project_id, private=body.private))
    if isinstance(result, dict) and result.get("status") == "created":
        response.status_code = 201
    return result


@mutation_router.post("/{project_id}/publish", status_code=202)
async def start_plugin_project_publish(project_id: str, body: PublishRequest):
    # Background task running `neko-plugin publish`; poll publish-task for progress.
    return await _call(publish_tasks.start_publish(project_id, body.mode))


@router.get("/{project_id}/publish-task")
async def latest_plugin_project_publish_task(project_id: str):
    return await _call(asyncio.to_thread(publish_tasks.latest_publish_task, project_id))


@mutation_router.post("/{project_id}/publish-task/{task_id}/cancel")
async def cancel_plugin_project_publish_task(project_id: str, task_id: str):
    return await _call(publish_tasks.cancel_publish(project_id, task_id))


@mutation_router.delete("/{project_id}", response_model=OkResponse)
async def remove_plugin_project(project_id: str):
    await _call(service.remove_project(project_id))
    return OkResponse()


router.include_router(mutation_router)
