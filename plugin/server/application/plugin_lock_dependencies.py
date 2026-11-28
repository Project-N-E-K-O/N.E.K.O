"""Install an installed plugin's locked Python dependencies into its vendor/.

A plugin package may ship ``pyproject.toml`` + ``uv.lock`` instead of a
``vendor/``. Starting such a plugin is refused with
``PLUGIN_PYTHON_DEPENDENCIES_PENDING``; the plugin manager then asks this
module for the install plan (package names, versions and sizes), shows it to
the user and, once confirmed, has it installed:

1. ``prepare_tool_runtime`` provides uv and the Python the plugin runs on.
2. ``uv export`` turns the lock into a hashed requirements list and
   ``uv pip install --dry-run`` says what this Python needs from it.
3. ``uv pip install`` installs into a staging directory beside vendor/,
   which is swapped in under the plugin operation lock; on any failure the
   staging directory is removed and the previous state is left as it was.

uv does the downloading, hash checking and installing; the command lines are
shared with the developer CLI (``plugin.neko_plugin_cli.core.uv_lock_install``).
One task per plugin lives in memory; whether a plugin still needs its
dependencies is always read from disk, so nothing is lost on restart.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from typing import Literal

from plugin.core.python_dependencies import (
    collect_project_python_requirements,
    find_missing_python_requirements,
    load_pyproject_toml,
    split_host_provided_requirements,
)
from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.core.uv_lock_install import (
    DEFAULT_PYPI_INDEX_URL,
    LOCK_FILE_NAME,
    VENDOR_DIR_NAME,
    LockedPackage,
    lock_install_applies,
    lock_install_env,
    lock_sha256,
    lock_vendor_stale,
    locked_wheel_sizes,
    parse_install_plan,
    read_lock,
    runtime_python_tag,
    uv_export_command,
    unpinned_lock_sources,
    unpinned_requirements,
    uv_install_command,
    write_lock_marker,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_tool_runtime import (
    ProgressEvent,
    ToolRuntime,
    ToolRuntimeError,
    prepare_tool_runtime,
    run_tool_command,
)
from plugin.utils.source_paths import VENDOR_SYNC_BACKUP_PREFIX, VENDOR_SYNC_STAGING_PREFIX

logger = get_logger("server.application.plugin_lock_dependencies")

TaskState = Literal["planning", "awaiting_confirmation", "installing", "completed", "failed"]
ErrorCode = Literal[
    "not_applicable", "tools_failed", "lock_invalid", "network", "not_on_index", "no_wheel",
    "hash_mismatch", "unpinned_source", "lock_changed", "lock_mismatch", "install_failed",
    "canceled", "busy", "internal_error",
]

# Preparing uv and Python on first use downloads tens of megabytes; installing
# large dependencies may take a while on a slow connection.
_PLAN_TIMEOUT_SECONDS = 30 * 60.0
_INSTALL_TIMEOUT_SECONDS = 60 * 60.0
_DETAIL_LIMIT = 600
_CREDENTIALS_RE = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


def plugin_dependency_index_url() -> str:
    """The package index locked dependencies are downloaded from.

    The single place to plug in the user's PyPI mirror choice (requirement
    #410); until then the official index.
    """
    return DEFAULT_PYPI_INDEX_URL


@dataclass
class _Task:
    plugin_id: str
    plugin_dir: Path
    state: TaskState = "planning"
    phase: str | None = None
    lock_sha256: str | None = None
    packages: list[LockedPackage] = field(default_factory=list)
    error_code: ErrorCode | None = None
    error_detail: str | None = None
    download: tuple[int | None, int | None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    runner: asyncio.Task | None = None
    # The lock the user confirmed; a plan for the same lock proceeds without asking again.
    confirmed_lock_sha256: str | None = None
    # Written into this task's staging directory; a staging directory at the
    # same path without it belongs to a plugin directory that replaced ours.
    staging_token: str | None = None

    def as_dict(self) -> dict[str, object]:
        # State before packages: the worker publishes packages (and the lock
        # digest) first, then the state, so a state read here never comes
        # with an older, empty list. Each field is read once.
        state, phase = self.state, self.phase
        packages = self.packages
        sizes = [package.size for package in packages]
        known = [size for size in sizes if size is not None]
        return {
            "plugin_id": self.plugin_id,
            "state": state,
            "phase": phase,
            "lock_sha256": self.lock_sha256,
            "packages": [
                {"name": package.name, "version": package.version, "size": package.size}
                for package in packages
            ],
            "total_size": sum(known),
            "size_complete": len(known) == len(sizes),
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


class _Failure(Exception):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ErrorCode = code
        self.detail = detail


_STAGING_TOKEN_NAME = ".neko-staging-id"

_tasks: dict[str, _Task] = {}
_tasks_lock = threading.Lock()


def _plugin_running(plugin_id: str) -> bool:
    from plugin.core.state import state

    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


# ---------------------------------------------------------------------------
# Plugin lookup and the dependency check


def _plugin_dir(plugin_id: str) -> Path | None:
    """The installed plugin's directory; None for a built-in plugin, which never installs here."""
    from plugin.server.application.plugins.lifecycle_service import (
        _get_plugin_meta_sync,
        _resolve_plugin_config_path_sync,
    )
    from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT

    config_path = _resolve_plugin_config_path_sync(plugin_id, _get_plugin_meta_sync(plugin_id))
    if config_path is None:
        raise ServerDomainError(
            code="PLUGIN_CONFIG_NOT_FOUND", message=f"Plugin '{plugin_id}' configuration not found",
            status_code=404, details={"plugin_id": plugin_id},
        )
    plugin_dir = config_path.parent
    try:
        builtin = plugin_dir.resolve().is_relative_to(Path(BUILTIN_PLUGIN_CONFIG_ROOT).resolve())
    except OSError:
        builtin = False
    return None if builtin else plugin_dir


def missing_requirements(plugin_dir: Path, vendor_dir: Path | None = None) -> list[str]:
    """Declared third-party requirements that vendor/ (or ``vendor_dir``) does not satisfy.

    The start gate's view; lock_install_applies() already excludes plugins
    declaring dependencies in the legacy requirements.txt, so pyproject.toml
    is the whole declaration here.
    """
    requirements = collect_project_python_requirements(load_pyproject_toml(plugin_dir))
    external, _host = split_host_provided_requirements(requirements)
    vendor_dir = plugin_dir / VENDOR_DIR_NAME if vendor_dir is None else vendor_dir
    return find_missing_python_requirements(external, search_paths=[vendor_dir] if vendor_dir.is_dir() else [])


def dependencies_pending(plugin_dir: Path) -> bool:
    """Whether this plugin's dependencies need installing from its uv.lock.

    Missing ones, or a lock-installed vendor/ made for another Python or
    platform (the host was updated): its compiled wheels may not load.
    """
    try:
        return lock_install_applies(plugin_dir) and (
            lock_vendor_stale(plugin_dir) or bool(missing_requirements(plugin_dir))
        )
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# uv


def _redact(text: str) -> str:
    return _CREDENTIALS_RE.sub(r"\1***@", text)


def _detail(result) -> str:
    lines = [line.strip() for line in ((result.stderr or "") + "\n" + (result.stdout or "")).splitlines()]
    text = " ".join(line for line in lines if line)
    return _redact(text)[-_DETAIL_LIMIT:]


def _classify(output: str) -> ErrorCode:
    """Map uv's error text to what the user is told. Unknown text stays generic."""
    lowered = output.lower()
    if "must have a hash" in lowered or "direct url" in lowered or "editable" in lowered:
        return "unpinned_source"
    if "hash mismatch" in lowered or "hashes do not match" in lowered:
        return "hash_mismatch"
    if "no usable wheels" in lowered or "building from source is disabled" in lowered or (
        "wheel" in lowered and "platform" in lowered and "no solution" in lowered
    ):
        return "no_wheel"
    if "not found in the package registry" in lowered or "was not found in the" in lowered:
        return "not_on_index"
    # Phrases from uv / reqwest error chains, not bare words that package
    # names can contain (networkx, tlslite...).
    if any(term in lowered for term in (
        "failed to fetch", "dns error", "error sending request", "connection refused", "connection reset",
        "connection closed", "operation timed out", "request timed out", "network is unreachable",
        "tls handshake", "certificate verify failed", "invalid peer certificate",
    )):
        return "network"
    if "no solution found" in lowered:
        return "not_on_index"
    return "install_failed"


def _run(task: _Task, runtime: ToolRuntime, command: list[str], label: str, deadline: float):
    env = lock_install_env(runtime.env, plugin_dependency_index_url())
    try:
        return run_tool_command(command, label=label, env=env, cancel=task.cancel, deadline=deadline)
    except ToolRuntimeError as exc:
        raise _Failure("canceled" if exc.code == "canceled" else "install_failed", exc.detail) from exc
    except OSError as exc:
        raise _Failure("tools_failed", f"Could not run uv: {exc}") from exc


def _prepare_runtime(task: _Task, deadline: float) -> ToolRuntime:
    def progress(event: ProgressEvent) -> None:
        task.phase = f"tools_{event.phase}"
        task.download = (event.downloaded_bytes, event.total_bytes)

    task.phase = "tools"
    try:
        return prepare_tool_runtime(cancel=task.cancel, deadline=deadline, progress=progress)
    except ToolRuntimeError as exc:
        raise _Failure("canceled" if exc.code == "canceled" else "tools_failed", _redact(exc.detail)) from exc


def _snapshot(plugin_dir: Path, work_dir: Path) -> tuple[Path, str]:
    """Copy pyproject.toml and uv.lock into ``work_dir``; (project copy, digest of the copied lock).

    uv exports from the copy, so what is installed and what the marker says
    come from the same lock bytes even if the plugin's file changes meanwhile.
    """
    project = work_dir / "project"
    project.mkdir()
    try:
        lock_bytes = (plugin_dir / LOCK_FILE_NAME).read_bytes()
        (project / "pyproject.toml").write_bytes((plugin_dir / "pyproject.toml").read_bytes())
    except OSError as exc:
        raise _Failure("lock_invalid", f"{LOCK_FILE_NAME} or pyproject.toml cannot be read: {exc}") from exc
    (project / LOCK_FILE_NAME).write_bytes(lock_bytes)
    return project, hashlib.sha256(lock_bytes).hexdigest()


def _export(task: _Task, runtime: ToolRuntime, project: Path, work_dir: Path, deadline: float) -> Path:
    task.phase = "resolving"
    requirements = work_dir / "requirements.txt"
    result = _run(task, runtime, uv_export_command(runtime.uv, project, requirements), "uv export", deadline)
    if result.returncode:
        raise _Failure("lock_invalid", _detail(result))
    try:
        exported = requirements.read_text(encoding="utf-8")
    except OSError as exc:
        raise _Failure("lock_invalid", f"uv export wrote no requirements: {exc}") from exc
    offending = unpinned_requirements(exported)
    if offending:
        # Rejected before uv sees them: planning would fetch these URLs before the user confirmed.
        raise _Failure("unpinned_source", _redact("Not from the package index: " + ", ".join(offending))[:_DETAIL_LIMIT])
    return requirements


def _checked_lock(project: Path) -> dict[str, object]:
    """Parse the snapshot's uv.lock and refuse non-index sources, before any uv command runs."""
    try:
        lock_data = read_lock(project)
    except (OSError, ValueError) as exc:
        raise _Failure("lock_invalid", f"{LOCK_FILE_NAME} cannot be read: {exc}") from exc
    offending = unpinned_lock_sources(lock_data)
    if offending:
        raise _Failure("unpinned_source", _redact("Not from the package index: " + ", ".join(offending))[:_DETAIL_LIMIT])
    return lock_data


def _plan_sync(task: _Task, deadline: float) -> None:
    if not lock_install_applies(task.plugin_dir):
        raise _Failure("not_applicable", "The plugin has no uv.lock, or ships its own vendor/")
    with tempfile.TemporaryDirectory(prefix="neko-lock-plan-") as temporary:
        work_dir = Path(temporary)
        project, digest = _snapshot(task.plugin_dir, work_dir)
        lock_data = _checked_lock(project)
        runtime = _prepare_runtime(task, deadline)
        requirements = _export(task, runtime, project, work_dir, deadline)
        result = _run(
            task, runtime,
            uv_install_command(runtime.uv, runtime.python, requirements, work_dir / "target", dry_run=True),
            "uv pip install --dry-run", deadline,
        )
    output = (result.stderr or "") + "\n" + (result.stdout or "")
    if result.returncode:
        raise _Failure(_classify(output), _detail(result))
    task.packages = locked_wheel_sizes(lock_data, parse_install_plan(output))
    task.lock_sha256 = digest


def _swap_in(plugin_dir: Path, staging: Path) -> None:
    """Replace vendor/ with staging; vendor/ is back as it was if this fails."""
    vendor = plugin_dir / VENDOR_DIR_NAME
    backup: Path | None = None
    if vendor.exists():
        backup = plugin_dir / f"{VENDOR_SYNC_BACKUP_PREFIX}{secrets.token_hex(4)}"
        vendor.rename(backup)
    try:
        staging.rename(vendor)
    except BaseException:
        if backup is not None:
            backup.rename(vendor)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def _install_sync(task: _Task, deadline: float) -> Path:
    """Install into a staging directory beside vendor/; returns it, swap not done yet."""
    staging = task.plugin_dir / f"{VENDOR_SYNC_STAGING_PREFIX}{secrets.token_hex(4)}"
    try:
        # Created with a token first: if the plugin directory is replaced while
        # uv runs (an upgrade renames it away), uv's later writes land in a
        # staging directory of the new tree that lacks the token, and the swap
        # refuses it instead of installing half the packages.
        task.staging_token = secrets.token_hex(16)
        try:
            staging.mkdir()
        except FileNotFoundError as exc:
            raise _Failure("lock_changed", "The plugin was removed before its dependencies were installed") from exc
        (staging / _STAGING_TOKEN_NAME).write_text(task.staging_token, encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="neko-lock-install-") as temporary:
            work_dir = Path(temporary)
            project, digest = _snapshot(task.plugin_dir, work_dir)
            if digest != task.lock_sha256:
                raise _Failure("lock_changed", "uv.lock changed after the user confirmed it")
            _checked_lock(project)
            runtime = _prepare_runtime(task, deadline)
            requirements = _export(task, runtime, project, work_dir, deadline)
            task.phase = "installing"
            result = _run(
                task, runtime, uv_install_command(runtime.uv, runtime.python, requirements, staging),
                "uv pip install", deadline,
            )
            if result.returncode:
                raise _Failure(_classify((result.stderr or "") + "\n" + (result.stdout or "")), _detail(result))
            staging.mkdir(exist_ok=True)
            # Checked before the swap, against the snapshot uv installed from,
            # so a lock that does not cover pyproject.toml never replaces a working vendor/.
            still_missing = missing_requirements(project, staging)
        if still_missing:
            raise _Failure(
                "lock_mismatch",
                f"uv.lock does not provide what pyproject.toml declares: {', '.join(still_missing)}",
            )
        write_lock_marker(staging, digest, python=runtime_python_tag())
        return staging
    except BaseException:
        _discard_staging(task.plugin_dir, staging)
        raise


def _discard_staging(plugin_dir: Path, staging: Path) -> None:
    """Remove the staging directory, and a plugin directory uv recreated just for it.

    When the plugin is uninstalled while uv runs, ``--target`` may create the
    staging path (and so the plugin directory) again; an empty leftover would
    later read as an installed plugin.
    """
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if not (plugin_dir / "plugin.toml").exists() and not any(plugin_dir.iterdir()):
            plugin_dir.rmdir()
    except OSError:
        pass  # Gone already, or not empty: not ours to remove.


async def _commit(task: _Task, staging: Path) -> None:
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy, serialized_plugin_operation

    @serialized_plugin_operation
    async def commit() -> None:
        def swap() -> None:
            # The plugin may have been upgraded or removed while uv ran.
            if not task.plugin_dir.is_dir() or not lock_install_applies(task.plugin_dir):
                raise _Failure("lock_changed", "The plugin changed while its dependencies were installed")
            if lock_sha256(task.plugin_dir) != task.lock_sha256:
                raise _Failure("lock_changed", "uv.lock changed while its dependencies were installed")
            token_file = staging / _STAGING_TOKEN_NAME
            try:
                token = token_file.read_text(encoding="utf-8")
            except OSError:
                token = None
            if token is None or token != task.staging_token:
                raise _Failure("lock_changed", "The plugin directory was replaced while its dependencies were installed")
            token_file.unlink()
            if _plugin_running(task.plugin_id):
                raise _Failure("lock_changed", "The plugin was started while its dependencies were installed")
            _swap_in(task.plugin_dir, staging)

        await asyncio.to_thread(swap)

    try:
        await commit()
    except PluginOperationBusy as exc:
        raise _Failure("busy", "Another plugin operation held the lock; try again") from exc
    except OSError as exc:
        # The swap itself failed (the staging directory moved with an upgrade, a locked file...).
        raise _Failure("lock_changed", f"vendor/ could not be replaced: {exc}") from exc
    finally:
        await asyncio.to_thread(_discard_staging, task.plugin_dir, staging)


async def _refresh_registry(plugin_id: str) -> None:
    from plugin.server.application.plugins.registry_service import PluginRegistryService

    try:
        await PluginRegistryService().refresh_plugin(plugin_id)
    except Exception as exc:  # e.g. ServerDomainError, PluginOperationBusy
        # Best effort: vendor/ is already in place and the next start refreshes again.
        logger.warning(
            "registry refresh after dependency install failed plugin_id={} err_type={}", plugin_id, type(exc).__name__,
        )


async def _execute(task: _Task, *, install: bool) -> None:
    try:
        if install and task.lock_sha256 is not None:
            # The lock may have changed since the user saw the plan (an upgrade): plan it again.
            try:
                unchanged = await asyncio.to_thread(lock_sha256, task.plugin_dir) == task.lock_sha256
            except OSError:
                unchanged = False
            if not unchanged:
                install = False
        if not install or task.lock_sha256 is None:
            task.state = "planning"
            await asyncio.to_thread(_plan_sync, task, time.monotonic() + _PLAN_TIMEOUT_SECONDS)
            if task.confirmed_lock_sha256 != task.lock_sha256:
                task.state, task.phase = "awaiting_confirmation", None
                return
        task.state = "installing"
        task.confirmed_lock_sha256 = task.lock_sha256
        staging = await asyncio.to_thread(_install_sync, task, time.monotonic() + _INSTALL_TIMEOUT_SECONDS)
        task.phase = "committing"
        await _commit(task, staging)
        task.phase = "verifying"
        await _refresh_registry(task.plugin_id)
        task.state, task.phase = "completed", None
    except _Failure as exc:
        task.state, task.phase = "failed", None
        task.error_code, task.error_detail = exc.code, exc.detail
        logger.warning("plugin dependency install failed plugin_id={} code={}", task.plugin_id, exc.code)
    except asyncio.CancelledError:
        task.cancel.set()
        task.state, task.phase, task.error_code, task.error_detail = "failed", None, "canceled", None
        raise
    except Exception as exc:
        task.state, task.phase = "failed", None
        task.error_code, task.error_detail = "internal_error", _redact(f"{type(exc).__name__}: {exc}")
        logger.exception("plugin dependency install failed unexpectedly plugin_id={}", task.plugin_id)


# ---------------------------------------------------------------------------
# Public API (called from the HTTP routes)


def _status_for(plugin_id: str, plugin_dir: Path) -> dict[str, object]:
    with _tasks_lock:
        task = _tasks.get(plugin_id)
    if task is not None and task.plugin_dir == plugin_dir:
        return {**task.as_dict(), "pending": dependencies_pending(plugin_dir)}
    return {"plugin_id": plugin_id, "state": None, "pending": dependencies_pending(plugin_dir)}


async def get_dependency_status(plugin_id: str) -> dict[str, object]:
    """Whether the plugin needs its locked dependencies, and the current task if any. Disk only."""
    plugin_dir = await asyncio.to_thread(_plugin_dir, plugin_id)
    if plugin_dir is None:
        return {"plugin_id": plugin_id, "state": None, "pending": False}
    return await asyncio.to_thread(_status_for, plugin_id, plugin_dir)


async def request_dependency_install(plugin_id: str, *, confirm_lock_sha256: str | None = None) -> dict[str, object]:
    """Plan (and, with the confirmed lock digest, install) the plugin's locked dependencies.

    Without ``confirm_lock_sha256`` this computes the plan and waits for the
    user. With it, the install proceeds when the lock is still the one the
    user saw; a different lock is planned again and shown again. Calling it
    after a failure retries. A running task is returned as it is.
    """
    plugin_dir = await asyncio.to_thread(_plugin_dir, plugin_id)
    if plugin_dir is None:
        raise ServerDomainError(
            code="PLUGIN_DEPENDENCIES_NOT_APPLICABLE",
            message=f"Plugin '{plugin_id}' is built in; its dependencies are not installed here",
            status_code=400, details={"plugin_id": plugin_id},
        )
    with _tasks_lock:
        task = _tasks.get(plugin_id)
        if task is not None and task.runner is not None and not task.runner.done():
            return task.as_dict()
    if await asyncio.to_thread(_plugin_running, plugin_id) or not await asyncio.to_thread(
        dependencies_pending, plugin_dir,
    ):
        # Nothing to install (or it runs on its vendor/ right now): never swap vendor/ under it.
        return {"plugin_id": plugin_id, "state": None, "pending": False}
    with _tasks_lock:
        task = _tasks.get(plugin_id)
        if task is not None and task.runner is not None and not task.runner.done():
            return task.as_dict()
        reuse = (
            task is not None and task.plugin_dir == plugin_dir and task.state == "awaiting_confirmation"
            and confirm_lock_sha256 is not None and confirm_lock_sha256 == task.lock_sha256
        )
        if reuse:
            assert task is not None
            install = True
            # Reported before the task runs, so the dialog polls instead of staying on the list.
            task.state = "installing"
            task.error_code = task.error_detail = None
            task.cancel = threading.Event()
        else:
            task = _Task(plugin_id, plugin_dir)
            install = False
        task.confirmed_lock_sha256 = confirm_lock_sha256
        if not install:
            task.lock_sha256 = None
        _tasks[plugin_id] = task
        task.runner = asyncio.create_task(_execute(task, install=install), name=f"plugin-deps-{plugin_id}")
    return task.as_dict()
