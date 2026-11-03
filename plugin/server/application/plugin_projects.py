"""Authoring workbench projects: scaffold creation and the persistent project list.

Creation never loads a project into the runtime. Listed attributes are read from
each project's ``plugin.toml`` on demand; only identities are persisted. Every
failure leaves this module as a ``ServerDomainError`` with a ``ProjectErrorCode``.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from plugin.neko_plugin_cli.core.project import (
    ProjectCreationError,
    create_project,
    prepare_project,
)
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    serialized_plugin_operation,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure import native_directory

PluginType = Literal["plugin", "adapter"]
ProjectStatus = Literal["ok", "missing", "invalid"]
# ``mode_disabled``: registered for development, but development mode is off,
# so the source is not running.
DevelopmentState = Literal["unmounted", "mounted", "mode_disabled"]


@dataclass(frozen=True)
class ProjectIdentity:
    plugin_id: str
    name: str
    description: str
    author: str
    plugin_type: PluginType


@dataclass(frozen=True)
class ProjectPreview:
    plugin_id: str
    name: str
    description: str
    author: str
    plugin_type: PluginType
    version: str
    entry: str
    project_dir: str
    files: list[str]


@dataclass(frozen=True)
class CreatedProject(ProjectPreview):
    project_id: str


@dataclass(frozen=True)
class AuthoringProject:
    project_id: str
    source_dir: str
    added_at: str
    status: ProjectStatus
    # English diagnostic for ``invalid``; the UI localizes by ``status``.
    error: str | None
    plugin_id: str | None
    name: str | None
    description: str | None
    author: str | None
    plugin_type: str | None
    version: str | None
    entry: str | None
    # None when development registrations cannot be read.
    development: DevelopmentState | None


def source_repo_root() -> Path | None:
    # Frozen executables are not Python interpreters or source checkouts.
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return None
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() else None


def _creation_error(exc: ProjectCreationError) -> ServerDomainError:
    status = 409 if exc.code == Code.PROJECT_EXISTS else 400
    return project_error(Code(exc.code), str(exc), status)


def _busy_error(exc: PluginOperationBusy) -> ServerDomainError:
    return project_error(
        Code.PLUGIN_OPERATION_BUSY, f"Another plugin operation is in progress: {exc}", 409
    )


def _existing_directory(path: str, code: Code, label: str) -> Path:
    directory = Path(path).expanduser()
    if not directory.is_absolute():
        raise project_error(code, f"Select an absolute {label} on this machine", 400)
    try:
        directory = directory.resolve(strict=True)
    except OSError as exc:
        raise project_error(code, f"The {label} is unavailable", 400) from exc
    if not directory.is_dir():
        raise project_error(code, f"The {label} must be a directory", 400)
    return directory


def _spec(identity: ProjectIdentity):
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    return PluginSpec(
        plugin_id=identity.plugin_id,
        name=identity.name,
        description=identity.description,
        author_name=identity.author,
        plugin_type=identity.plugin_type,
        quick_start=True,
        features=["entry_point"],
        create_github_actions=True,
    )


def _target(parent_dir: str, identity: ProjectIdentity):
    parent = _existing_directory(parent_dir, Code.INVALID_PARENT, "parent directory")
    spec = _spec(identity)
    # The caller supplies a parent, never an executable entry or output filename.
    return spec, parent / spec.plugin_id


def _preview(spec, target: Path, files) -> ProjectPreview:
    return ProjectPreview(
        plugin_id=spec.plugin_id,
        name=spec.name,
        description=spec.description,
        author=spec.author_name,
        plugin_type=spec.plugin_type,
        version=spec.version,
        entry=spec.entry_point,
        project_dir=str(target),
        files=[Path(path).as_posix() for path in files] + [".git/"],
    )


def preview_project(parent_dir: str, identity: ProjectIdentity) -> ProjectPreview:
    spec, target = _target(parent_dir, identity)
    try:
        files = prepare_project(spec, target, repo_root=source_repo_root())
    except ProjectCreationError as exc:
        raise _creation_error(exc) from exc
    return _preview(spec, target, files)


def _create_files(parent_dir: str, identity: ProjectIdentity) -> ProjectPreview:
    spec, target = _target(parent_dir, identity)
    try:
        written = create_project(spec, target, repo_root=source_repo_root())
    except ProjectCreationError as exc:
        raise _creation_error(exc) from exc
    except FileExistsError as exc:
        raise project_error(Code.PROJECT_EXISTS, str(exc), 409) from exc
    except OSError as exc:
        raise project_error(Code.PROJECT_IO_FAILED, str(exc), 400) from exc
    return _preview(spec, target, [path.relative_to(target) for path in written])


async def create_and_register_project(
    parent_dir: str, identity: ProjectIdentity
) -> CreatedProject:
    created = await asyncio.to_thread(_create_files, parent_dir, identity)
    try:
        project = await _register_locked(created.project_dir)
    except (ServerDomainError, PluginOperationBusy) as exc:
        # The project stays on disk; the user decides how to proceed.
        raise project_error(
            Code.PROJECT_REGISTER_FAILED,
            f"Project was created at {created.project_dir} but could not be added "
            f"to the workbench: {exc}",
            500,
        ) from exc
    return CreatedProject(**asdict(created), project_id=project.project_id)


def _development_sources() -> tuple[bool, set[Path]] | None:
    from plugin.server.application.plugins import development

    try:
        with development.development_registry_lock:
            enabled = development.development_enabled_sync()
            sources = {
                record.source_dir for record in development.list_registration_records_sync()
            }
    except ServerDomainError:
        return None
    return enabled, sources


def _development_state(
    source_dir: Path, sources: tuple[bool, set[Path]] | None
) -> DevelopmentState | None:
    if sources is None:
        return None
    enabled, registered = sources
    if source_dir not in registered:
        return "unmounted"
    return "mounted" if enabled else "mode_disabled"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _view(
    record: store.ProjectRecord, sources: tuple[bool, set[Path]] | None
) -> AuthoringProject:
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    common = {
        "project_id": record.project_id,
        "source_dir": str(record.source_dir),
        "added_at": record.added_at,
        "development": _development_state(record.source_dir, sources),
    }
    empty = dict.fromkeys(
        ("plugin_id", "name", "description", "author", "plugin_type", "version", "entry")
    )
    if not record.source_dir.is_dir():
        return AuthoringProject(**common, **empty, status="missing", error=None)
    try:
        source = load_plugin_source(record.source_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return AuthoringProject(**common, **empty, status="invalid", error=str(exc))
    table = source.plugin_toml.get("plugin")
    table = table if isinstance(table, dict) else {}
    author = table.get("author")
    return AuthoringProject(
        **common,
        status="ok",
        error=None,
        plugin_id=source.plugin_id,
        name=source.name,
        description=_optional_string(table.get("description")),
        author=_optional_string(author.get("name") if isinstance(author, dict) else author),
        plugin_type=source.package_type,
        version=source.version,
        entry=_optional_string(table.get("entry")),
    )


def list_projects_sync() -> list[AuthoringProject]:
    records = store.list_records()
    sources = _development_sources() if records else (False, set())
    return [_view(record, sources) for record in records]


def _validated_project_directory(source_dir: str) -> Path:
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    directory = _existing_directory(
        source_dir, Code.INVALID_PROJECT_DIRECTORY, "project directory"
    )
    try:
        load_plugin_source(directory)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    return directory


def _register_sync(source_dir: str) -> AuthoringProject:
    record = store.add_record(_validated_project_directory(source_dir))
    return _view(record, _development_sources())


@serialized_plugin_operation
async def _register_locked(source_dir: str) -> AuthoringProject:
    return await asyncio.to_thread(_register_sync, source_dir)


@serialized_plugin_operation
async def _remove_locked(project_id: str) -> None:
    await asyncio.to_thread(store.remove_record, project_id)


async def register_project(source_dir: str) -> AuthoringProject:
    try:
        return await _register_locked(source_dir)
    except PluginOperationBusy as exc:
        raise _busy_error(exc) from exc


async def remove_project(project_id: str) -> None:
    """Forget the project. Its files and any development mount are untouched."""
    try:
        await _remove_locked(project_id)
    except PluginOperationBusy as exc:
        raise _busy_error(exc) from exc


def pick_project_directory(start_path: str, title: str) -> str | None:
    """Return the selected absolute directory, or None when the user cancels."""
    try:
        return native_directory.pick_directory(start_path=start_path, title=title)
    except native_directory.DirectoryPickerCancelled:
        return None
    except native_directory.NativeDirectoryError as exc:
        raise project_error(Code(exc.code), str(exc), 400) from exc


def open_project_directory(path: str) -> None:
    try:
        native_directory.open_directory(path)
    except native_directory.NativeDirectoryError as exc:
        raise project_error(Code(exc.code), str(exc), 400) from exc
