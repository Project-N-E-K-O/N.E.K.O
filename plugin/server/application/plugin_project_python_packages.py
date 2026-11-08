"""Project-local Python dependency editing and observable installation tasks.

The shared Python package module owns declaration and vendor semantics. This
layer adds project identity, operation leases, runtime fencing and task lifetime.
All filesystem work and child output reads run outside the server event loop.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import CancelledError as ToolPreparationCanceled
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
    start_cli,
    terminate_process_tree,
)
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
from plugin.server.application.plugins.project_sources import inspect_source_directory
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.python_package_tools import prepare_package_tools, read_package_tools
from utils import process_creation

logger = get_logger("server.application.plugin_project_python_packages")
Operation = Literal["sync", "check"]
_TERMINAL = frozenset({"succeeded", "failed", "canceled"})
_MAX_TASKS = 64
_LOG_LINES = 200
_LOG_CHARS = 500


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


def _running(plugin_id: str, source_dir: Path) -> bool:
    # A project whose ID collides with an installed plugin does not own its host.
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
        owns = isinstance(meta, dict) and meta.get("source_dir") == str(source_dir)
    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(plugin_id)
    return bool(owns and host is not None and host.is_alive())


def _snapshot(task: _Task) -> dict:
    with _lock:
        return {
            "task_id": task.task_id, "project_id": task.project_id,
            "operation": task.operation, "status": task.status, "phase": task.phase,
            "message": task.message, "log": list(task.log),
            "started_at": task.started_at, "updated_at": task.updated_at,
            "finished_at": task.finished_at, "result": task.result,
        }


def read_packages(project_id: str) -> dict:
    directory, plugin_id = _project(project_id)
    try:
        result = inspect_python_packages(directory)
    except PythonPackagesError as exc:
        raise _domain_error(exc) from exc
    with _lock:
        task = _tasks.get(_latest.get(project_id, ""))
        current = _snapshot(task) if task else None
    return {
        **result, "vendor_exists": (directory / "vendor").is_dir(),
        "running": _running(plugin_id, directory), "environment": read_package_tools(),
        "task": current, "operation": project_operation_name(project_id),
    }


def save_requirements(project_id: str, requirements: list[str], revision: str) -> dict:
    with project_operation(project_id, "python-packages-save"):
        directory, _plugin_id = _project(project_id)
        try:
            save_python_requirements(directory, requirements, revision)
        except PythonPackagesError as exc:
            raise _domain_error(exc) from exc
    return read_packages(project_id)


@serialized_plugin_operation
async def _prepare_task(project_id: str, operation: Operation, revision: str) -> _Task:
    before = await asyncio.to_thread(read_packages, project_id)
    if before["revision"] != revision:
        raise ServerDomainError(
            code="PYTHON_PACKAGES_REVISION_CONFLICT",
            message="pyproject.toml changed; refresh the dependencies before continuing",
            status_code=409,
        )
    directory, plugin_id = await asyncio.to_thread(_project, project_id)
    if operation == "sync" and before["running"]:
        raise project_error(
            Code.PYTHON_PACKAGES_RUNNING,
            "Stop the project's plugin before synchronizing its Python packages", 409,
        )
    before = {key: value for key, value in before.items() if key not in {
        "task", "operation", "environment", "running", "vendor_exists",
    }}
    return _Task(uuid4().hex, project_id, operation, directory, plugin_id, before)


async def start_task(project_id: str, operation: Operation, revision: str) -> dict:
    # Own the lease outside the cancellation-shielded global operation. If the
    # HTTP request goes away while waiting/preparing, its caller still releases it.
    claim_project(project_id, f"python-packages-{operation}")
    try:
        task = await _prepare_task(project_id, operation, revision)
    except BaseException:
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


def _redact(message: str) -> str:
    # Installation diagnostics can include an authenticated private index URL.
    return re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[redacted]@", message)


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
    _check_stop(task)
    _log(task, label)
    try:
        process = start_cli(
            argv, cwd=task.source_dir, merge_stderr=True, env=env,
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
    # The timer also ends a silent installer whose stdout reader is blocked.
    timer = threading.Timer(max(0.01, task.deadline - time.monotonic()), terminate_process_tree, [process])
    timer.daemon = True
    timer.start()
    output: deque[str] = deque(maxlen=_LOG_LINES)
    try:
        assert process.stdout is not None
        while line := process.stdout.readline(_LOG_CHARS + 1):
            output.append(line[:_LOG_CHARS])
            _log(task, line)
            # Bound retained memory even when an installer writes a huge line.
            while line and not line.endswith("\n"):
                line = process.stdout.readline(_LOG_CHARS + 1)
        code = process.wait()
        _check_stop(task)
        return subprocess.CompletedProcess(argv, code, "".join(output), "")
    finally:
        timer.cancel()
        if process.poll() is None:
            terminate_process_tree(process)
        if process.stdout is not None:
            process.stdout.close()
        with _lock:
            task.process = None


def _changes(before: dict, after: dict) -> list[dict]:
    old = {item["name"]: item["version"] for item in before["installed"]}
    new = {item["name"]: item["version"] for item in after["installed"]}
    return [
        {"name": name, "before": old.get(name), "after": new.get(name)}
        for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)
    ]


def _execute(task: _Task) -> dict:
    _check_stop(task)
    if task.operation == "sync":
        runner = lambda argv, **kwargs: _command(task, argv, **kwargs)
        has_external = any(row["status"] != "host_provided" for row in task.before["requirements"])
        options = {}
        if has_external:
            tools = prepare_package_tools(runner, lambda line: _log(task, line), task.cancel_event)
            options = {
                "python": tools.python, "uv_executable": tools.uv_executable,
                "allow_pip_fallback": tools.allow_pip_fallback,
                "installer_runner": lambda argv, **kwargs: runner(argv, env=tools.env, **kwargs),
            }
        current = inspect_python_packages(task.source_dir)
        if current["revision"] != task.before["revision"]:
            raise RuntimeError("pyproject.toml changed while preparing the task; refresh and retry")
        _phase(task, "installing")
        task.vendor_touched = True
        code = sync_vendor(
            task.source_dir, project_root=source_repo_root(), clean=True, **options,
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
    return {"before": task.before, "after": after, "changes": _changes(task.before, after)}


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
    except (_Canceled, ToolPreparationCanceled) as exc:
        with _lock:
            task.message = str(exc)
        outcome = "canceled"
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
                    task.result = {"before": task.before, "after": after, "changes": _changes(task.before, after)}
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
