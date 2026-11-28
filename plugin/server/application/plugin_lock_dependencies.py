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
    locked_wheel_sizes,
    parse_install_plan,
    read_lock,
    uv_export_command,
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
    "canceled", "internal_error",
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

    def as_dict(self) -> dict[str, object]:
        sizes = [package.size for package in self.packages]
        known = [size for size in sizes if size is not None]
        return {
            "plugin_id": self.plugin_id,
            "state": self.state,
            "phase": self.phase,
            "lock_sha256": self.lock_sha256,
            "packages": [
                {"name": package.name, "version": package.version, "size": package.size}
                for package in self.packages
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


_tasks: dict[str, _Task] = {}
_tasks_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Plugin lookup and the dependency check


def _plugin_dir(plugin_id: str) -> Path:
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
    if builtin:
        raise ServerDomainError(
            code="PLUGIN_DEPENDENCIES_NOT_APPLICABLE",
            message=f"Plugin '{plugin_id}' is built in; its dependencies are not installed here",
            status_code=400, details={"plugin_id": plugin_id},
        )
    return plugin_dir


def missing_requirements(plugin_dir: Path) -> list[str]:
    """Declared third-party requirements that vendor/ does not satisfy (the start gate's view)."""
    requirements = collect_project_python_requirements(load_pyproject_toml(plugin_dir))
    external, _host = split_host_provided_requirements(requirements)
    vendor_dir = plugin_dir / VENDOR_DIR_NAME
    return find_missing_python_requirements(external, search_paths=[vendor_dir] if vendor_dir.is_dir() else [])


def dependencies_pending(plugin_dir: Path) -> bool:
    """Whether this plugin's missing dependencies can be installed from its uv.lock."""
    try:
        return lock_install_applies(plugin_dir) and bool(missing_requirements(plugin_dir))
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
    if any(term in lowered for term in (
        "failed to fetch", "dns error", "error sending request", "connection", "timed out", "tls",
        "certificate", "network",
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


def _export(task: _Task, runtime: ToolRuntime, work_dir: Path, deadline: float) -> Path:
    task.phase = "resolving"
    result = _run(task, runtime, uv_export_command(runtime.uv, task.plugin_dir), "uv export", deadline)
    if result.returncode:
        raise _Failure("lock_invalid", _detail(result))
    requirements = work_dir / "requirements.txt"
    requirements.write_text(result.stdout, encoding="utf-8")
    return requirements


def _plan_sync(task: _Task, deadline: float) -> None:
    if not lock_install_applies(task.plugin_dir):
        raise _Failure("not_applicable", "The plugin has no uv.lock, or ships its own vendor/")
    try:
        digest = lock_sha256(task.plugin_dir)
        lock_data = read_lock(task.plugin_dir)
    except (OSError, ValueError) as exc:
        raise _Failure("lock_invalid", f"{LOCK_FILE_NAME} cannot be read: {exc}") from exc
    runtime = _prepare_runtime(task, deadline)
    with tempfile.TemporaryDirectory(prefix="neko-lock-plan-") as temporary:
        work_dir = Path(temporary)
        requirements = _export(task, runtime, work_dir, deadline)
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
    runtime = _prepare_runtime(task, deadline)
    staging = task.plugin_dir / f"{VENDOR_SYNC_STAGING_PREFIX}{secrets.token_hex(4)}"
    try:
        with tempfile.TemporaryDirectory(prefix="neko-lock-install-") as temporary:
            requirements = _export(task, runtime, Path(temporary), deadline)
            task.phase = "installing"
            result = _run(
                task, runtime, uv_install_command(runtime.uv, runtime.python, requirements, staging),
                "uv pip install", deadline,
            )
        if result.returncode:
            raise _Failure(_classify((result.stderr or "") + "\n" + (result.stdout or "")), _detail(result))
        staging.mkdir(exist_ok=True)
        assert task.lock_sha256 is not None
        write_lock_marker(staging, task.lock_sha256)
        return staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


async def _commit(task: _Task, staging: Path) -> None:
    from plugin.server.application.plugins.operation_lock import serialized_plugin_operation

    @serialized_plugin_operation
    async def commit() -> None:
        def swap() -> None:
            # The plugin may have been upgraded or removed while uv ran.
            if not task.plugin_dir.is_dir() or not lock_install_applies(task.plugin_dir):
                raise _Failure("lock_changed", "The plugin changed while its dependencies were installed")
            if lock_sha256(task.plugin_dir) != task.lock_sha256:
                raise _Failure("lock_changed", "uv.lock changed while its dependencies were installed")
            _swap_in(task.plugin_dir, staging)

        await asyncio.to_thread(swap)

    try:
        await commit()
    finally:
        await asyncio.to_thread(shutil.rmtree, staging, True)


async def _refresh_registry(plugin_id: str) -> None:
    from plugin.server.application.plugins.registry_service import PluginRegistryService

    try:
        await PluginRegistryService().refresh_plugin(plugin_id)
    except ServerDomainError as exc:
        # The next start refreshes again; the install itself succeeded.
        logger.warning("registry refresh after dependency install failed plugin_id={} code={}", plugin_id, exc.code)


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
        still_missing = await asyncio.to_thread(missing_requirements, task.plugin_dir)
        await _refresh_registry(task.plugin_id)
        if still_missing:
            raise _Failure(
                "lock_mismatch",
                f"uv.lock does not provide what pyproject.toml declares: {', '.join(still_missing)}",
            )
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
    return await asyncio.to_thread(_status_for, plugin_id, plugin_dir)


async def request_dependency_install(plugin_id: str, *, confirm_lock_sha256: str | None = None) -> dict[str, object]:
    """Plan (and, with the confirmed lock digest, install) the plugin's locked dependencies.

    Without ``confirm_lock_sha256`` this computes the plan and waits for the
    user. With it, the install proceeds when the lock is still the one the
    user saw; a different lock is planned again and shown again. Calling it
    after a failure retries. A running task is returned as it is.
    """
    plugin_dir = await asyncio.to_thread(_plugin_dir, plugin_id)
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
