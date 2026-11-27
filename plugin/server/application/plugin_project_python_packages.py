"""Project-local Python dependency editing and observable installation tasks.

The shared Python package module owns declaration and vendor semantics. This
layer adds project identity, operation leases, runtime fencing and task lifetime.
All filesystem work and child output reads run outside the server event loop.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import subprocess
import sys
import sysconfig
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from plugin.neko_plugin_cli.core.python_packages import (
    PythonPackagesError,
    check_python_packages,
    inspect_python_packages,
    save_python_requirements,
)
from plugin.core.state import state
from plugin.neko_plugin_cli.core.vendor_sync import sync_vendor
from plugin.logging_config import get_logger
from plugin.server.application.plugin_project_commands import (
    claim_project,
    project_operation,
    project_operation_name,
    release_project,
    require_project_available,
    start_cli,
    terminate_process_tree,
)
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
from plugin.server.application.plugins.project_sources import inspect_source_directory, same_source_directory
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.process_output import iter_process_output
from plugin.server.infrastructure.plugin_tool_runtime import (
    ProgressEvent,
    ToolRuntimeError,
    prepare_tool_runtime,
    read_tool_runtime_status,
)
from utils import process_creation

logger = get_logger("server.application.plugin_project_python_packages")
Operation = Literal["sync", "check"]
_TERMINAL = frozenset({"succeeded", "failed", "canceled"})
_MAX_TASKS = 64
_LOG_LINES = 200
_LOG_CHARS = 500
_MAX_COMMAND_LINE_CHARS = 64 * 1024
_MAX_COMMAND_OUTPUT_CHARS = 1024 * 1024


@dataclass
class _Task:
    task_id: str
    project_id: str
    operation: Operation
    source_dir: Path
    plugin_id: str
    before: dict
    status: str = "queued"
    phase: str = "preparing"
    message: str = ""
    log: deque[str] = field(default_factory=lambda: deque(maxlen=_LOG_LINES))
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[str] | None = None
    deadline: float = 0.0
    vendor_touched: bool = False
    lease_released: bool = False
    # While plugin tools are prepared: the shared layer's latest progress event.
    tool_progress: dict | None = None
    # A ToolRuntimeError code when tool preparation failed, for localized text.
    error_code: str | None = None


_lock = threading.RLock()
_tasks: dict[str, _Task] = {}
_latest: dict[str, str] = {}
_workers: dict[str, asyncio.Task] = {}


class _Canceled(Exception):
    pass


def _domain_error(exc: PythonPackagesError) -> ServerDomainError:
    status = 409 if "REVISION" in exc.code or "BUSY" in exc.code else 400
    return ServerDomainError(code=exc.code, message=exc.message, status_code=status)


def _project(project_id: str) -> tuple[Path, str]:
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    try:
        metadata = inspect_source_directory(record.source_dir)
    except ValueError as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    return record.source_dir, metadata.plugin_id


def _running(source_dir: Path) -> bool:
    # A project whose ID collides with an installed plugin does not own its host.
    with state.acquire_plugins_read_lock():
        sources = [
            (identity, meta["source_dir"]) for identity, meta in state.plugins.items()
            if isinstance(meta, dict) and isinstance(meta.get("source_dir"), str)
        ]
    identities = [identity for identity, directory in sources if same_source_directory(directory, source_dir)]
    with state.acquire_plugin_hosts_read_lock():
        hosts = [state.plugin_hosts.get(identity) for identity in identities]
    return any(host is not None and host.is_alive() for host in hosts)


def _snapshot(task: _Task) -> dict:
    with _lock:
        return {
            "task_id": task.task_id, "project_id": task.project_id,
            "operation": task.operation, "status": task.status, "phase": task.phase,
            "message": task.message, "log": list(task.log),
            "started_at": task.started_at, "updated_at": task.updated_at,
            "finished_at": task.finished_at, "result": task.result,
            "tool_progress": task.tool_progress, "error_code": task.error_code,
        }


def _environment() -> dict:
    """Installer facts for the page; reads only (no child process or download)."""
    tools = read_tool_runtime_status()
    if tools.packaged:
        # Packaged sync always installs with the prepared uv and managed Python.
        installer = "uv" if tools.state != "unsupported" else None
        reason = tools.error_code if tools.state == "unsupported" else None
    else:
        pip = importlib.util.find_spec("pip") is not None
        installer = "uv" if tools.uv else "pip" if pip else None
        reason = None if installer else "installer_missing"
    return {
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "platform": sys.platform,
        "architecture": sysconfig.get_platform().rsplit("-", 1)[-1],
        "packaged": tools.packaged,
        "installer": installer,
        "can_sync": installer is not None,
        "reason": reason,
        "tools": tools.as_dict(),
    }


def _read_project_packages(project_id: str) -> tuple[Path, str, dict]:
    directory, plugin_id = _project(project_id)
    try:
        result = inspect_python_packages(directory)
    except PythonPackagesError as exc:
        raise _domain_error(exc) from exc
    return directory, plugin_id, result


def _with_project_state(project_id: str, directory: Path, result: dict) -> dict:
    with _lock:
        task = _tasks.get(_latest.get(project_id, ""))
        current = _snapshot(task) if task else None
    return {
        **result, "vendor_exists": (directory / "vendor").is_dir(),
        "running": _running(directory), "environment": _environment(),
        "task": current, "operation": project_operation_name(project_id),
    }


def read_packages(project_id: str) -> dict:
    directory, _plugin_id, result = _read_project_packages(project_id)
    return _with_project_state(project_id, directory, result)


def save_requirements(project_id: str, requirements: list[str], revision: str) -> dict:
    with project_operation(project_id, "python-packages-save"):
        directory, _plugin_id = _project(project_id)
        try:
            result = save_python_requirements(directory, requirements, revision)
        except PythonPackagesError as exc:
            raise _domain_error(exc) from exc
    return _with_project_state(project_id, directory, result)


@serialized_plugin_operation
async def save_project_requirements(project_id: str, requirements: list[str], revision: str) -> dict:
    return await asyncio.to_thread(save_requirements, project_id, requirements, revision)


@serialized_plugin_operation
async def _prepare_task(
    project_id: str, operation: Operation, revision: str, lease_acquired: threading.Event,
) -> _Task:
    # Reserve under the same operation lock as the complete stop/start cycle.
    # Otherwise a waiting task can claim between reload's check and restart.
    await asyncio.to_thread(claim_project, project_id, f"python-packages-{operation}")
    lease_acquired.set()
    directory, plugin_id, before = await asyncio.to_thread(_read_project_packages, project_id)
    if before["revision"] != revision:
        raise ServerDomainError(
            code="PYTHON_PACKAGES_REVISION_CONFLICT",
            message="pyproject.toml changed; refresh the dependencies before continuing",
            status_code=409,
        )
    if operation == "sync" and await asyncio.to_thread(_running, directory):
        raise project_error(
            Code.PYTHON_PACKAGES_RUNNING,
            "Stop the project's plugin before synchronizing its Python packages", 409,
        )
    return _Task(uuid4().hex, project_id, operation, directory, plugin_id, before)


async def start_task(project_id: str, operation: Operation, revision: str) -> dict:
    # Reject an existing lease promptly, then reserve atomically with lifecycle
    # operations. Track actual ownership across the cancellation-shielded call.
    await asyncio.to_thread(require_project_available, project_id)
    lease_acquired = threading.Event()
    try:
        task = await _prepare_task(project_id, operation, revision, lease_acquired)
    except BaseException:
        if lease_acquired.is_set():
            release_project(project_id)
        raise
    with _lock:
        _prune()
        _tasks[task.task_id] = task
        _latest[project_id] = task.task_id
        result = _snapshot(task)
    worker = asyncio.create_task(_run(task), name=f"plugin-python-packages-{task.task_id}")
    _workers[task.task_id] = worker
    def finished(_done: asyncio.Task) -> None:
        _workers.pop(task.task_id, None)
        # Cancellation can land before the coroutine executes its first line.
        with _lock:
            if task.status not in _TERMINAL or _done.cancelled():
                task.status = "canceled"
                task.phase = "finished"
                task.finished_at = task.updated_at = time.time()
        _release_task(task)
    worker.add_done_callback(finished)
    return result


def _release_task(task: _Task) -> None:
    with _lock:
        if not task.lease_released:
            task.lease_released = True
            release_project(task.project_id)


def read_task(project_id: str, task_id: str) -> dict:
    with _lock:
        task = _tasks.get(task_id)
        if task is None or task.project_id != project_id:
            raise project_error(Code.PYTHON_PACKAGES_TASK_NOT_FOUND, "Unknown dependency task", 404)
        return _snapshot(task)


def _log(task: _Task, line: str) -> None:
    line = _redact(line)
    with _lock:
        task.log.append(line.rstrip()[:_LOG_CHARS])
        task.updated_at = time.time()


def _redact(message: str, *, incomplete: bool = False) -> str:
    # Installation diagnostics can include an authenticated private index URL.
    message = re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[redacted]@", message)
    if incomplete:
        # A bounded pipe drain may end inside userinfo, before its final @.
        message = re.sub(r"(https?://)[^\s]*$", r"\1[redacted]", message)
    return message


def _phase(task: _Task, phase: str) -> None:
    with _lock:
        task.phase = phase
        task.updated_at = time.time()


def _check_stop(task: _Task) -> None:
    if task.cancel_event.is_set():
        raise _Canceled("The dependency task was canceled")
    if time.monotonic() >= task.deadline:
        raise TimeoutError("The dependency task exceeded its time budget")


def _terminate(task: _Task) -> None:
    with _lock:
        task.cancel_event.set()
        process = task.process
    if process is not None:
        terminate_process_tree(process)


def _command(task: _Task, argv: list[str], *, label: str, env: dict[str, str] | None = None):
    """Run one installer command; ``env`` replaces the inherited environment."""
    _check_stop(task)
    _log(task, label)
    try:
        process = start_cli(
            argv, cwd=task.source_dir, merge_stderr=True, env=env, replace_env=env is not None,
            deadline=task.deadline, cancel_event=task.cancel_event,
        )
    except (process_creation.ProcessCreationCancelled, process_creation.ProcessCreationTimeout):
        _check_stop(task)
        raise
    with _lock:
        task.process = process
        canceled = task.cancel_event.is_set()
    if canceled:
        terminate_process_tree(process)
        if process.stdout is not None:
            process.stdout.close()
        with _lock:
            task.process = None
        raise _Canceled("The dependency task was canceled")
    output: deque[str] = deque()
    output_chars = 0
    pending = ""
    discarding_line = False

    def record(line: str, *, incomplete: bool = False) -> None:
        nonlocal output_chars
        # Keep complete machine-readable lines separately from the short UI
        # log. Redact before either capture or display truncates the line.
        safe = _redact(line, incomplete=incomplete)
        output.append(safe)
        output_chars += len(safe)
        while len(output) > _LOG_LINES or output_chars > _MAX_COMMAND_OUTPUT_CHARS:
            output_chars -= len(output.popleft())
        _log(task, safe)

    try:
        for chunk in iter_process_output(process, checkpoint=lambda: _check_stop(task)):
            parts = chunk.splitlines(keepends=True)
            for part in parts:
                complete = part.endswith(("\n", "\r"))
                if not discarding_line:
                    pending += part
                    if len(pending) > _MAX_COMMAND_LINE_CHARS:
                        # Never retain a prefix of an oversized URL: its @ may
                        # occur beyond the capture boundary and expose secrets.
                        pending = ""
                        discarding_line = True
                        _log(task, "A command output line exceeded the capture limit and was omitted")
                if complete:
                    if not discarding_line:
                        record(pending)
                    pending = ""
                    discarding_line = False
        if pending and not discarding_line:
            record(pending, incomplete=True)
        while process.poll() is None:
            _check_stop(task)
            task.cancel_event.wait(0.05)
        _check_stop(task)
        return subprocess.CompletedProcess(argv, process.returncode, "".join(output), "")
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        if process.stdout is not None:
            process.stdout.close()
        with _lock:
            task.process = None


def _task_result(before: dict, after: dict) -> dict:
    old = {item["name"]: item["version"] for item in before["installed"]}
    new = {item["name"]: item["version"] for item in after["installed"]}
    changes = [
        {"name": name, "before": old.get(name), "after": new.get(name)}
        for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)
    ]
    return {
        "after": {
            **{key: after[key] for key in ("revision", "installed", "issues")},
            "check_issues": after.get("check_issues", []),
        },
        "changes": changes,
    }


def _tool_progress(task: _Task, event: ProgressEvent) -> None:
    with _lock:
        changed = task.tool_progress is None or task.tool_progress["phase"] != event.phase
        task.tool_progress = {
            "phase": event.phase, "downloaded_bytes": event.downloaded_bytes, "total_bytes": event.total_bytes,
        }
        task.updated_at = time.time()
    if changed:
        _log(task, f"Preparing plugin tools: {event.phase}")


def _installer_options(task: _Task) -> dict:
    """uv and Python from the shared plugin tool layer.

    Source checkouts get the host interpreter and the developer's uv (pip
    when there is none); packaged servers get the prepared uv and managed
    CPython, downloaded on first use. Installer children run with the
    layer's environment as is. Raises ToolRuntimeError.
    """
    try:
        runtime = prepare_tool_runtime(
            cancel=task.cancel_event, deadline=task.deadline,
            progress=lambda event: _tool_progress(task, event),
        )
    finally:
        with _lock:
            task.tool_progress = None
    return {
        "python": runtime.python,
        "uv_executable": runtime.uv,
        # A packaged build has no pip of its own to fall back to.
        "allow_pip_fallback": not runtime.packaged,
        "installer_runner": lambda argv, **kwargs: _command(task, argv, env=runtime.env, **kwargs),
    }


def _execute(task: _Task) -> dict:
    _check_stop(task)
    if task.operation == "sync":
        has_external = any(row["status"] != "host_provided" for row in task.before["requirements"])
        # Only installs need tools: emptying vendor/ prepares nothing.
        options = (
            _installer_options(task) if has_external
            else {"installer_runner": lambda argv, **kwargs: _command(task, argv, **kwargs)}
        )
        _phase(task, "installing")
        task.vendor_touched = True
        code = sync_vendor(
            task.source_dir, project_root=source_repo_root(), clean=True,
            expected_revision=task.before["revision"], **options,
            report=lambda message: _log(task, message),
            progress=lambda phase: _phase(task, phase),
            check_cancelled=lambda: _check_stop(task),
        )
        if code != 0:
            raise RuntimeError("Dependency installation failed; inspect the task log")
    _check_stop(task)
    _phase(task, "checking")
    after = check_python_packages(task.source_dir)
    _check_stop(task)
    return _task_result(task.before, after)


async def _run(task: _Task) -> None:
    with _lock:
        task.status = "running"
        task.started_at = task.updated_at = time.time()
        task.deadline = time.monotonic() + env_seconds("NEKO_PLUGIN_PYTHON_PACKAGES_BUDGET", 1200.0)
    execution = asyncio.create_task(asyncio.to_thread(_execute, task))
    outcome = "failed"
    try:
        task.result = await asyncio.shield(execution)
        outcome = "succeeded"
    except asyncio.CancelledError:
        await asyncio.to_thread(_terminate, task)
        try:
            await asyncio.shield(execution)
        except Exception:
            pass
        with _lock:
            task.message = "The dependency task was canceled during server shutdown"
        outcome = "canceled"
        raise
    except _Canceled as exc:
        with _lock:
            task.message = str(exc)
        outcome = "canceled"
    except ToolRuntimeError as exc:
        with _lock:
            task.message = _redact(exc.detail)
            if exc.code == "canceled":
                outcome = "canceled"
            else:
                task.error_code = exc.code
        if outcome != "canceled":
            logger.warning("Dependency task could not prepare plugin tools: task={}, code={}", task.task_id, exc.code)
            _log(task, task.message)
    except Exception as exc:
        message = _redact(str(exc))
        logger.warning("Dependency task failed: task={}, error={}", task.task_id, message)
        with _lock:
            task.message = message
        _log(task, message)
    finally:
        # Cancellation can arrive after a successful replacement, and linked
        # vendors can change even when an installer fails. Reconcile disk state
        # before releasing the project so the next Start sees fresh metadata.
        if task.vendor_touched:
            _phase(task, "refreshing")
            try:
                if task.result is None:
                    after = await asyncio.to_thread(inspect_python_packages, task.source_dir)
                    task.result = _task_result(task.before, after)
                from plugin.server.application.plugins.registry_service import PluginRegistryService

                await PluginRegistryService().refresh_plugin(task.plugin_id)
                state.invalidate_snapshot_cache("plugins")
            except Exception as exc:
                _log(task, f"Could not refresh the plugin's dependency state: {exc}")
                if outcome == "succeeded":
                    outcome = "failed"
                    task.message = "Packages were synchronized, but plugin metadata could not be refreshed"
        with _lock:
            task.status = outcome
            task.phase = "finished"
            task.finished_at = task.updated_at = time.time()
        _release_task(task)


async def cancel_task(project_id: str, task_id: str) -> dict:
    read_task(project_id, task_id)
    with _lock:
        task = _tasks[task_id]
        terminal = task.status in _TERMINAL
    if not terminal:
        await asyncio.to_thread(_terminate, task)
    return _snapshot(task)


async def shutdown_tasks() -> None:
    """Drain workers before the server closes its runtime and executor."""
    workers = list(_workers.values())
    for worker in workers:
        worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)


def _prune() -> None:
    terminal = sorted(
        (task for task in _tasks.values() if task.status in _TERMINAL),
        key=lambda task: task.finished_at or 0,
    )
    for task in terminal[: max(0, len(_tasks) - _MAX_TASKS + 1)]:
        _tasks.pop(task.task_id, None)
        if _latest.get(task.project_id) == task.task_id:
            _latest.pop(task.project_id, None)
