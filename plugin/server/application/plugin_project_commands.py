"""Run ``neko-plugin`` commands for an authoring project.

Precheck, create-repo and publish all run the CLI as a child process against
the same source tree (``vendor/`` sync, git, tags). This module holds what
they share: one operation per project at a time, a UTF-8 child environment,
and termination of the whole process tree (git, uvx, pytest grandchildren),
not just the direct child.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO

import portalocker
import psutil

from plugin.logging_config import get_logger
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.utils.asyncio_utils import await_cancellation_safe
from utils import process_creation

logger = get_logger("server.application.plugin_project_commands")

_TERMINATE_GRACE_SECONDS = 15.0

_busy_lock = threading.Lock()
# project_id -> name of the operation holding it.
_busy: dict[str, str] = {}
_project_leases: dict[str, BinaryIO] = {}


def _project_lease_path(project_id: str) -> Path:
    from plugin.settings import get_plugin_state_root

    # The caller may not have resolved the record yet. Never use an API-supplied
    # project ID as a filesystem path component.
    key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return get_plugin_state_root().parent / "project-operations" / f"{key}.lock"


def _drop_inherited_project_leases() -> None:
    try:
        for handle in _project_leases.values():
            # A forked plugin must not retain its parent's lease. Close its copy
            # without LOCK_UN: an explicit unlock also unlocks the parent's flock.
            handle.close()
        _project_leases.clear()
        _busy.clear()
    finally:
        _busy_lock.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_busy_lock.acquire,
        after_in_parent=_busy_lock.release,
        after_in_child=_drop_inherited_project_leases,
    )


def claim_project(project_id: str, operation: str) -> None:
    """Mark ``project_id`` busy with ``operation``; refuse when another holds it."""
    with _busy_lock:
        holder = _busy.get(project_id)
        if holder is not None:
            raise project_error(
                Code.PLUGIN_OPERATION_BUSY,
                f"{holder} is already running for project {project_id}",
                409,
            )
        path = _project_lease_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.AlreadyLocked:
            handle.close()
            raise project_error(
                Code.PLUGIN_OPERATION_BUSY,
                f"Another process is operating on project {project_id}",
                409,
            ) from None
        except BaseException:
            handle.close()
            raise
        _project_leases[project_id] = handle
        _busy[project_id] = operation


def release_project(project_id: str) -> None:
    with _busy_lock:
        handle = _project_leases.pop(project_id, None)
        if handle is not None:
            try:
                portalocker.unlock(handle)
            finally:
                handle.close()
        _busy.pop(project_id, None)


def project_operation_name(project_id: str) -> str | None:
    with _busy_lock:
        holder = _busy.get(project_id)
        if holder is not None:
            return holder
        try:
            handle = _project_lease_path(project_id).open("r+b")
        except FileNotFoundError:
            return None
        with handle:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.AlreadyLocked:
                return "Another process"
            portalocker.unlock(handle)
        return None


def require_project_available(project_id: str) -> None:
    holder = project_operation_name(project_id)
    if holder is not None:
        raise project_error(
            Code.PLUGIN_OPERATION_BUSY,
            f"{holder} is already running for project {project_id}",
            409,
        )


def require_plugin_project_available(plugin_id: str) -> None:
    """Fence runtime operations while an authoring operation changes its source."""
    from plugin.core.state import state

    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
        project_id = meta.get("project_id") if isinstance(meta, dict) else None
        source_dir = meta.get("source_dir") if isinstance(meta, dict) else None
    if not isinstance(project_id, str):
        from plugin.server.infrastructure import authoring_project_store as store

        records = store.list_records()
        if isinstance(source_dir, str):
            project_id = next((
                item.project_id for item in records if str(item.source_dir) == source_dir
            ), None)
        elif meta is None and records:
            # A registered project can be started before registry discovery has
            # populated state.plugins. Resolve its identity without importing it.
            from plugin.server.application.plugins.project_sources import list_project_sources_sync

            sources, _failures = list_project_sources_sync()
            project_id = next((
                item.project_id for item in sources if item.plugin_id == plugin_id
            ), None)
    if isinstance(project_id, str):
        require_project_available(project_id)


class project_operation:
    """Hold ``project_id`` for one operation.

    A class, not ``@contextmanager``: the generator form rewrites the
    traceback of an exception raised inside the block, which
    ``ServerDomainError`` does not allow (see ``bounded_operation_wait``).
    """

    def __init__(self, project_id: str, operation: str) -> None:
        self._project_id = project_id
        self._operation = operation

    def __enter__(self) -> None:
        claim_project(self._project_id, self._operation)

    def __exit__(self, *_exc: object) -> None:
        release_project(self._project_id)


def cli_child_env() -> dict[str, str]:
    # UTF-8 mode also makes the CLI decode its own git/ruff/pytest pipes as
    # UTF-8, whatever the Windows code page is.
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }


def start_cli(
    argv: list[str], *, cwd: Path, merge_stderr: bool,
    env: dict[str, str] | None = None,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> subprocess.Popen[str]:
    return process_creation.popen(
        argv,
        cwd=cwd,
        env={**cli_child_env(), **(env or {})},
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        deadline=deadline,
        cancel_event=cancel_event,
    )


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill the CLI and everything it spawned (git, uvx, build children)."""
    if process.poll() is not None:
        return
    parent: psutil.Process | None = None
    try:
        parent = psutil.Process(process.pid)
    except psutil.Error:
        pass
    children: list[psutil.Process] = []
    if parent is not None:
        try:
            children = parent.children(recursive=True)
        except psutil.Error:
            children = []

    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - escalation best-effort
            logger.warning("CLI subprocess did not exit after kill: pid={}", process.pid)
    # Children may outlive a parent that exits promptly on terminate.
    if not children:
        return
    _, alive = psutil.wait_procs(children, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=5)


async def run_cli(
    argv: list[str], *, cwd: Path, timeout: float, merge_stderr: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run the CLI to completion.

    Raises ``OSError`` when it cannot start and ``subprocess.TimeoutExpired``
    once the tree was killed after ``timeout``. Cancelling the caller kills
    the tree too, so a server shutdown leaves nothing running.
    """
    deadline = time.monotonic() + timeout
    cancel_event = threading.Event()
    creation = asyncio.create_task(asyncio.to_thread(
        start_cli, argv, cwd=cwd, merge_stderr=merge_stderr, deadline=deadline,
        cancel_event=cancel_event,
    ))
    try:
        process = await asyncio.shield(creation)
    except asyncio.CancelledError:
        cancel_event.set()
        # Threaded OS creation cannot be undone by cancelling its awaiter. Keep
        # ownership until it returns, then reap any process it allocated.
        async def reap_creation() -> None:
            try:
                created = await creation
            except Exception:
                return
            output = asyncio.create_task(asyncio.to_thread(created.communicate))
            await _terminate_and_drain(created, output)

        await await_cancellation_safe(asyncio.create_task(reap_creation()))
        raise
    except process_creation.ProcessCreationTimeout:
        raise subprocess.TimeoutExpired(argv, timeout) from None
    communicate = asyncio.ensure_future(asyncio.to_thread(process.communicate))
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.shield(communicate), max(0.0, deadline - time.monotonic())
        )
    except TimeoutError:
        await await_cancellation_safe(asyncio.create_task(_terminate_and_drain(process, communicate)))
        raise subprocess.TimeoutExpired(argv, timeout) from None
    except asyncio.CancelledError:
        await await_cancellation_safe(asyncio.create_task(_terminate_and_drain(process, communicate)))
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr or "")


async def _terminate_and_drain(
    process: subprocess.Popen[str], communicate: asyncio.Task,
) -> None:
    await asyncio.to_thread(terminate_process_tree, process)
    try:
        await asyncio.wait_for(asyncio.shield(communicate), _TERMINATE_GRACE_SECONDS)
    except TimeoutError:  # pragma: no cover - a killed tree closes its pipes
        logger.warning("CLI pipes stayed open after terminate: pid={}", process.pid)
