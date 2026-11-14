"""Authoring workbench projects: scaffold creation and the persistent project list.

Registered projects are the external plugin sources of the runtime: discovery
loads them like source plugins but never starts them automatically. Listed
attributes are read from each project's ``plugin.toml`` on demand; only
identities are persisted. Every failure leaves this module as a
``ServerDomainError`` with a ``ProjectErrorCode``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import tomli_w

from utils.file_utils import atomic_write_text

from plugin.neko_plugin_cli.core.project import (
    ProjectCreationError,
    create_project,
    prepare_project,
)
from plugin.neko_plugin_cli.core.toml_utils import load_toml
from plugin import settings
from plugin.core.entry_points import plugin_package_matches_directory
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugin_project_commands import project_operation, run_cli
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    serialized_plugin_operation,
)
from plugin.server.application.plugins.project_sources import (
    inspect_source_directory,
    project_source_dir,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from utils import native_directory

logger = get_logger("server.application.plugin_projects")

PluginType = Literal["plugin", "adapter"]
ProjectStatus = Literal["ok", "missing", "invalid"]
# What keeps a readable project from working. Not loaded: its plugin ID belongs
# to another plugin, the directory fails the project-source rules, the runtime
# refuses its manifest, the plugin system is not running (an embedded server
# before user plugins are switched on), or it is running but has not picked the
# project up. Loaded: the registry marked its code unloadable (missing
# dependency, entry or syntax error), the last automatic reload stopped it and
# could not start it again, or it runs with a failed startup hook.
ProjectProblem = Literal[
    "id_taken",
    "source_rejected",
    "config_rejected",
    "runtime_inactive",
    "not_registered",
    "load_failed",
    "reload_failed",
    "startup_degraded",
]


@dataclass(frozen=True)
class ProjectIdentity:
    plugin_id: str
    name: str
    description: str
    author: str
    plugin_type: PluginType


@dataclass(frozen=True)
class ProjectMetadata:
    name: str
    version: str
    author: str
    description: str


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
    # English diagnostic for ``invalid`` and for a ``problem``, when one is
    # known; the UI localizes by ``status`` and ``problem``.
    error: str | None
    plugin_id: str | None
    name: str | None
    description: str | None
    author: str | None
    plugin_type: str | None
    version: str | None
    entry: str | None
    # Whether the runtime currently provides the plugin from this project.
    loaded: bool
    running: bool
    # Set only for a readable (``ok``) project.
    problem: ProjectProblem | None


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
    _require_available_plugin_id(spec.plugin_id, target)
    try:
        files = prepare_project(spec, target, repo_root=source_repo_root())
    except ProjectCreationError as exc:
        raise _creation_error(exc) from exc
    return _preview(spec, target, files)


def _create_files(parent_dir: str, identity: ProjectIdentity) -> ProjectPreview:
    spec, target = _target(parent_dir, identity)
    _require_available_plugin_id(spec.plugin_id, target)
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


@dataclass(frozen=True)
class _RuntimeState:
    loaded: bool
    running: bool
    problem: ProjectProblem | None = None
    error: str | None = None


def _loaded_state(plugin_id: str, meta: dict) -> _RuntimeState:
    """The runtime facts the plugin pages also show, read from the same registry entry."""
    from plugin.server.application.plugins.lifecycle_service import (
        plugin_is_running_sync,
        plugin_needs_hot_reload_recovery,
    )

    running = plugin_is_running_sync(plugin_id)
    if meta.get("runtime_load_state") == "failed":
        return _RuntimeState(
            loaded=True,
            running=running,
            problem="load_failed",
            error=_optional_string(meta.get("runtime_load_error_message")),
        )
    if not running and plugin_needs_hot_reload_recovery(plugin_id):
        # The watcher keeps no reason; the plugin log has it.
        return _RuntimeState(loaded=True, running=False, problem="reload_failed")
    if running and meta.get("runtime_startup_state") == "degraded":
        return _RuntimeState(
            loaded=True,
            running=True,
            problem="startup_degraded",
            error=_optional_string(meta.get("runtime_startup_error")),
        )
    return _RuntimeState(loaded=True, running=running)


def _runtime_state(record: store.ProjectRecord, plugin_id: str) -> _RuntimeState:
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
    if (
        project_source_dir(meta) == record.source_dir
        and isinstance(meta, dict)
        and meta.get("project_id") == record.project_id
    ):
        return _loaded_state(plugin_id, meta)
    # Discovery skips a project for one of these reasons, in this order
    # (``registry_service._discover_registry_snapshot_sync``).
    if isinstance(meta, dict):
        owner = project_source_dir(meta) or meta.get("config_path") or "another plugin"
        return _RuntimeState(
            loaded=False,
            running=False,
            problem="id_taken",
            error=f"Plugin ID '{plugin_id}' is provided by {owner}",
        )
    try:
        inspect_source_directory(record.source_dir)
    except ValueError as exc:
        return _RuntimeState(loaded=False, running=False, problem="source_rejected", error=str(exc))
    manifest_error = _manifest_error(record.source_dir, plugin_id)
    if manifest_error is not None:
        return _RuntimeState(
            loaded=False, running=False, problem="config_rejected", error=manifest_error
        )
    # The manifest is valid, so only an absent registry explains it: an
    # embedded plugin server answers HTTP before its plugin system starts
    # (agent_server starts it when user plugins are switched on).
    from plugin.server.lifecycle import lifecycle_running

    if not lifecycle_running():
        return _RuntimeState(loaded=False, running=False, problem="runtime_inactive")
    return _RuntimeState(loaded=False, running=False, problem="not_registered")


class _CapturingLogger:
    """Collects what the runtime's manifest parser reports, instead of logging it."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _keep(self, message: object, *args: object, **_kwargs: object) -> None:
        try:
            self.messages.append(str(message).format(*args))
        except (IndexError, KeyError, ValueError):
            self.messages.append(" ".join(str(item) for item in (message, *args)))

    error = warning = _keep

    def _drop(self, *_args: object, **_kwargs: object) -> None:
        return None

    debug = info = exception = _drop

    def __getattr__(self, _name: str):
        # Any other logging call the parser makes is dropped, never an AttributeError.
        return self._drop


def _manifest_error(source_dir: Path, plugin_id: str) -> str | None:
    """Why the runtime would refuse ``plugin.toml``, with its own words; None when it accepts it."""
    from plugin.core.registry import _parse_single_plugin_config
    from plugin.server.application.plugins.registry_service import (
        _build_discovery_record_from_context,
    )

    captured = _CapturingLogger()
    try:
        ctx = _parse_single_plugin_config(
            source_dir / "plugin.toml", set(), captured, materialize_runtime_config=False
        )
        if ctx is not None and ctx.pid == plugin_id:
            _build_discovery_record_from_context(ctx)
    except Exception as exc:  # noqa: BLE001 - any refusal is reported, never raised
        return f"{type(exc).__name__}: {exc}"
    if ctx is None:
        return "; ".join(captured.messages) or "plugin.toml could not be parsed or validated"
    if ctx.pid != plugin_id:
        return f"plugin.toml declares id '{ctx.pid}', expected '{plugin_id}'"
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _view(record: store.ProjectRecord) -> AuthoringProject:
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    common = {
        "project_id": record.project_id,
        "source_dir": str(record.source_dir),
        "added_at": record.added_at,
    }
    empty = {
        **dict.fromkeys(
            ("plugin_id", "name", "description", "author", "plugin_type", "version", "entry")
        ),
        "loaded": False,
        "running": False,
        "problem": None,
    }
    if not record.source_dir.is_dir():
        return AuthoringProject(**common, **empty, status="missing", error=None)
    try:
        source = load_plugin_source(record.source_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return AuthoringProject(**common, **empty, status="invalid", error=str(exc))
    table = source.plugin_toml.get("plugin")
    table = table if isinstance(table, dict) else {}
    author = table.get("author")
    runtime = _runtime_state(record, source.plugin_id)
    return AuthoringProject(
        **common,
        loaded=runtime.loaded,
        running=runtime.running,
        problem=runtime.problem,
        status="ok",
        error=runtime.error,
        plugin_id=source.plugin_id,
        name=source.name,
        description=_optional_string(table.get("description")),
        author=_optional_string(author.get("name") if isinstance(author, dict) else author),
        plugin_type=source.package_type,
        version=source.version,
        entry=_optional_string(table.get("entry")),
    )


def list_projects_sync() -> list[AuthoringProject]:
    return [_view(record) for record in store.list_records()]


def _update_metadata_sync(
    project_id: str, metadata: ProjectMetadata
) -> tuple[store.ProjectRecord, str]:
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, "Project is not in the workbench", 404)
    try:
        source = load_plugin_source(record.source_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    table = source.plugin_toml["plugin"]
    table.update(name=metadata.name, version=metadata.version, description=metadata.description)
    # Keep contact fields when the manifest uses an author table.
    if isinstance(table.get("author"), dict):
        table["author"]["name"] = metadata.author
    else:
        table["author"] = metadata.author
    try:
        atomic_write_text(source.plugin_toml_path, tomli_w.dumps(source.plugin_toml))
    except OSError as exc:
        raise project_error(Code.PROJECT_IO_FAILED, f"Cannot save plugin.toml: {exc}", 500) from exc
    return record, source.plugin_id


@serialized_plugin_operation
async def _update_metadata_locked(project_id: str, metadata: ProjectMetadata) -> AuthoringProject:
    record, plugin_id = await asyncio.to_thread(_update_metadata_sync, project_id, metadata)
    from plugin.server.application.plugins.registry_service import PluginRegistryService

    try:
        await PluginRegistryService().refresh_plugin(plugin_id)
    except ServerDomainError as exc:
        logger.warning("project metadata saved but registry refresh failed: {}", exc.message)
    return await asyncio.to_thread(_view, record)


async def update_project_metadata(project_id: str, metadata: ProjectMetadata) -> AuthoringProject:
    try:
        return await _update_metadata_locked(project_id, metadata)
    except PluginOperationBusy as exc:
        raise _busy_error(exc) from exc


def _installed_directory_owns_id(candidate: Path, plugin_id: str) -> bool:
    """Whether an installed plugin directory already belongs to ``plugin_id``.

    The directory name is matched with the same rule the loader uses (a kebab-case
    directory may stand for a snake-case package, and the spelling can mix both,
    e.g. ``foo-bar_baz``). The manifest decides: a directory that merely shares the
    spelling but declares another id belongs to that other plugin, so it does not
    reserve this one. An unreadable manifest fails closed and counts as an owner.
    """
    if not plugin_package_matches_directory(plugin_id, candidate.name):
        return False
    manifest = candidate / "plugin.toml"
    if not manifest.is_file():
        return False
    try:
        data = load_toml(manifest)
    except (OSError, ValueError):
        return True
    section = data.get("plugin")
    declared = section.get("id") if isinstance(section, dict) else None
    if not isinstance(declared, str) or not declared.strip():
        return True
    return declared.strip() == plugin_id


def _installed_root_owns_id(root: Path, plugin_id: str) -> bool:
    """Scan one installed-plugin root for a directory that already owns the id."""
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    return any(
        child.is_dir() and _installed_directory_owns_id(child, plugin_id)
        for child in children
    )


def _require_available_plugin_id(plugin_id: str, source_dir: Path) -> None:
    """A plugin ID has one owner: an installed/built-in plugin or one project."""
    # Scan the physical directories instead of guessing spellings: a cloned plugin
    # may sit in `foo-bar_baz/` while its id is `foo_bar_baz` (#3724).
    for root in settings.PLUGIN_CONFIG_ROOTS:
        if _installed_root_owns_id(Path(root), plugin_id):
            raise project_error(
                Code.PROJECT_PLUGIN_ID_CONFLICT,
                f"Plugin ID '{plugin_id}' is already used by an installed plugin",
                409,
            )
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
    if meta is not None and project_source_dir(meta) is None:
        raise project_error(
            Code.PROJECT_PLUGIN_ID_CONFLICT,
            f"Plugin ID '{plugin_id}' is already used by an installed plugin",
            409,
        )
    # Directory names are compared through the same rule the loader uses, so the
    # same ID cannot be registered twice via `foo-bar/` and `foo_bar/` (#3724).
    owner = next(
        (item for item in store.list_records()
         if item.source_dir != source_dir
         and plugin_package_matches_directory(plugin_id, item.source_dir.name)),
        None,
    )
    if owner is not None:
        raise project_error(
            Code.PROJECT_PLUGIN_ID_CONFLICT,
            f"Plugin ID '{plugin_id}' is already provided by {owner.source_dir}",
            409,
        )


def _add_sync(source_dir: str) -> tuple[store.ProjectRecord, str]:
    directory = _existing_directory(
        source_dir, Code.INVALID_PROJECT_DIRECTORY, "project directory"
    )
    try:
        metadata = inspect_source_directory(directory)
    except ValueError as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    _require_available_plugin_id(metadata.plugin_id, directory)
    return store.add_record(directory), metadata.plugin_id


@serialized_plugin_operation
async def _register_locked(source_dir: str) -> AuthoringProject:
    from plugin.server.application.plugins.registry_service import PluginRegistryService

    record, plugin_id = await asyncio.to_thread(_add_sync, source_dir)
    try:
        await PluginRegistryService().refresh_plugin(plugin_id)
    except ServerDomainError as exc:
        # The project is registered; its listed state shows that it did not load.
        logger.warning("registered project did not load: plugin_id={}, error={}", plugin_id, exc.message)
    return await asyncio.to_thread(_view, record)


def _loaded_plugin_id(project_id: str) -> str | None:
    """Runtime ID loaded from the project; the caller holds a plugins lock."""
    return next(
        (
            plugin_id
            for plugin_id, meta in state.plugins.items()
            if isinstance(meta, dict) and meta.get("project_id") == project_id
        ),
        None,
    )


def _project_plugin_id_sync(project_id: str) -> str | None:
    with state.acquire_plugins_read_lock():
        return _loaded_plugin_id(project_id)


def _remove_project_entry_sync(project_id: str) -> str | None:
    failure: ServerDomainError | None = None
    plugin_id = None
    with state.acquire_plugins_write_lock():
        try:
            record = store.remove_record(project_id)
        except ServerDomainError as exc:
            # The generator-based state lock cannot rethrow frozen domain errors.
            failure = exc
        else:
            plugin_id = _loaded_plugin_id(record.project_id)
            if plugin_id is not None:
                state.plugins.pop(plugin_id, None)
    if failure is not None:
        raise failure
    state.invalidate_snapshot_cache("plugins")
    return plugin_id


@serialized_plugin_operation
async def _remove_locked(project_id: str) -> None:
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
        plugin_is_running_sync,
        revoke_hot_reload_recovery,
    )

    plugin_id = await asyncio.to_thread(_project_plugin_id_sync, project_id)
    if plugin_id is not None and await asyncio.to_thread(plugin_is_running_sync, plugin_id):
        try:
            result = await PluginLifecycleService().stop_plugin(plugin_id)
        except ServerDomainError as exc:
            raise project_error(Code.PROJECT_STOP_FAILED, exc.message, 409) from exc
        if not result.get("success"):
            raise project_error(
                Code.PROJECT_STOP_FAILED, f"Plugin '{plugin_id}' did not stop", 409
            )
    forgotten = await asyncio.to_thread(_remove_project_entry_sync, project_id)
    if forgotten is not None:
        revoke_hot_reload_recovery(forgotten)


async def register_project(source_dir: str) -> AuthoringProject:
    try:
        return await _register_locked(source_dir)
    except PluginOperationBusy as exc:
        raise _busy_error(exc) from exc


async def remove_project(project_id: str) -> None:
    """Stop and unload the project's plugin, then forget it. Files are kept."""
    try:
        with project_operation(project_id, "remove"):
            await _remove_locked(project_id)
    except PluginOperationBusy as exc:
        raise _busy_error(exc) from exc
    except TimeoutError as exc:
        raise project_error(Code.PLUGIN_OPERATION_BUSY, str(exc), 409) from exc


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


async def precheck_project(project_id: str) -> dict[str, object]:
    """Run the Market CI gates against a registered project's source.

    The gates live in the CLI (``neko-plugin precheck``); this only locates
    the project, runs the CLI from the source checkout and returns its JSON
    report verbatim, so the workbench can never drift from CI.
    """
    root = source_repo_root()
    if root is None:
        raise project_error(
            Code.PRECHECK_UNAVAILABLE,
            "Pre-release checks need a source checkout; they are unavailable in a packaged build.",
            503,
        )
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    budget = env_seconds("NEKO_PLUGIN_PRECHECK_BUDGET", 600.0)
    argv = [
        sys.executable,
        "-m",
        "plugin.neko_plugin_cli.cli",
        "precheck",
        str(record.source_dir),
        "--json",
    ]
    # ``sync --clean`` rewrites vendor/; never alongside a publish or create-repo.
    with project_operation(project_id, "precheck"):
        try:
            completed = await run_cli(argv, cwd=root, timeout=budget)
        except subprocess.TimeoutExpired as exc:
            raise project_error(
                Code.PRECHECK_FAILED_TO_RUN, f"precheck timed out after {budget:.0f} seconds", 504
            ) from exc
        except OSError as exc:
            raise project_error(
                Code.PRECHECK_FAILED_TO_RUN, f"precheck could not run: {exc}", 500
            ) from exc
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        tail = completed.stderr.strip()[-500:]
        raise project_error(
            Code.PRECHECK_FAILED_TO_RUN, f"precheck produced no report: {tail}", 500
        )
    try:
        report = json.loads(lines[-1])
    except ValueError as exc:
        raise project_error(
            Code.PRECHECK_FAILED_TO_RUN, f"precheck report is not valid JSON: {exc}", 500
        ) from exc
    if not isinstance(report, dict):
        raise project_error(Code.PRECHECK_FAILED_TO_RUN, "precheck report is not an object", 500)
    return report
