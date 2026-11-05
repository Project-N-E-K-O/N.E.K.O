"""Background publication tasks for authoring workbench projects.

One task runs ``neko-plugin publish`` as a subprocess, exactly like
``precheck`` runs the CI gates: the CLI stays the only code that pushes a
tag or notifies the Market, and the workbench can never drift from it. The
task registry is in memory — authoritative progress lives on GitHub, and the
read-only publish status reconciles against it after a server restart.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import quote
from uuid import uuid4

import psutil

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.neko_plugin_cli.repo_state import read_repo_state
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.application.plugin_publish_status import read_remote_github
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store

logger = get_logger("server.application.plugin_publish_tasks")

PublishMode = Literal["github_and_market", "github", "market_notify"]
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})
_LOG_LINES = 200
_LOG_LINE_CHARS = 400
_MAX_TASKS = 64
_TERMINATE_GRACE_SECONDS = 15.0
_RELEASE_URL_PATTERN = re.compile(r"GitHub Release ready: (\S+)")


@dataclass
class _Task:
    task_id: str
    project_id: str
    mode: PublishMode
    status: str = "queued"
    message: str = ""
    release_url: str = ""
    log: deque[str] = field(default_factory=lambda: deque(maxlen=_LOG_LINES))
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    process: subprocess.Popen[str] | None = None


# Reentrant: snapshots are taken while the registry lock is already held.
_lock = threading.RLock()
_tasks: dict[str, _Task] = {}
_active: dict[str, str] = {}
_latest: dict[str, str] = {}
_workers: dict[str, asyncio.Task[None]] = {}


def _snapshot(task: _Task) -> dict[str, object]:
    with _lock:
        return {
            "task_id": task.task_id,
            "project_id": task.project_id,
            "mode": task.mode,
            "status": task.status,
            "message": task.message,
            "release_url": task.release_url,
            "log": list(task.log),
            "started_at": task.started_at,
            "updated_at": task.updated_at,
            "finished_at": task.finished_at,
        }


class _Gate(NamedTuple):
    """Everything ``start_publish`` needs after the local preconditions pass."""

    root: Path
    source_dir: Path
    plugin_id: str
    repository: str
    head: str | None
    tag: str


def _local_gate(project_id: str) -> _Gate:
    """Refuse to start unless the CLI's own local preconditions already hold."""
    root = source_repo_root()
    if root is None:
        raise project_error(
            Code.PUBLISH_UNAVAILABLE,
            "Publishing needs a source checkout; it is unavailable in a packaged build.",
            503,
        )
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    try:
        source = load_plugin_source(record.source_dir)
    except (OSError, TypeError, KeyError, ValueError) as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    state = read_repo_state(record.source_dir, version=source.version)
    reasons: list[str] = []
    if not state.git_available:
        reasons.append("git is not available")
    elif not state.is_repository:
        reasons.append("the project directory does not have its own git repository")
    if not state.github_repository:
        reasons.append("git remote origin does not point to a GitHub repository")
    if state.worktree_clean is not True:
        reasons.append("the git working tree has uncommitted changes")
    if state.head_pushed is not True:
        reasons.append("HEAD is not pushed to its origin branch")
    if reasons:
        raise project_error(Code.PUBLISH_NOT_READY, "; ".join(reasons), 409)
    assert state.github_repository is not None  # the check above raised without it
    return _Gate(
        root=root,
        source_dir=record.source_dir,
        plugin_id=source.plugin_id,
        repository=state.github_repository,
        head=state.head,
        tag=state.tag,
    )


async def start_publish(project_id: str, mode: PublishMode) -> dict[str, object]:
    gate = await asyncio.to_thread(_local_gate, project_id)
    release_url = ""
    if mode == "market_notify":
        # The Market rejects releases it cannot verify; check before asking.
        remote = await read_remote_github(gate.repository, gate.head, gate.tag, gate.plugin_id)
        if remote["rate_limited"]:
            raise project_error(
                Code.PUBLISH_RATE_LIMITED,
                "GitHub rate limit reached; the release cannot be verified right now.",
                429,
            )
        release = remote["release"]
        if release["state"] != "published":
            raise project_error(
                Code.PUBLISH_NOT_READY, f"release {gate.tag} is not published yet", 409
            )
        if release["missing_assets"]:
            raise project_error(
                Code.PUBLISH_NOT_READY,
                f"release assets are incomplete: {', '.join(release['missing_assets'])}",
                409,
            )
        release_url = (
            f"https://github.com/{gate.repository}/releases/tag/{quote(gate.tag, safe='')}"
        )

    argv = [sys.executable, "-m", "plugin.neko_plugin_cli.cli", "publish"]
    if mode == "github":
        argv += ["github", str(gate.source_dir)]
    elif mode == "market_notify":
        argv += ["market", release_url]
    else:
        argv += [str(gate.source_dir)]

    budget = env_seconds("NEKO_PLUGIN_PUBLISH_BUDGET", 1200.0)
    task = _Task(
        task_id=uuid4().hex, project_id=project_id, mode=mode, release_url=release_url
    )
    with _lock:
        active_id = _active.get(project_id)
        active = _tasks.get(active_id) if active_id else None
        if active is not None and active.status not in _TERMINAL_STATUSES:
            raise project_error(
                Code.PUBLISH_TASK_ACTIVE,
                f"a publish task is already running for project {project_id}",
                409,
            )
        _prune()
        _tasks[task.task_id] = task
        _active[project_id] = task.task_id
        _latest[project_id] = task.task_id
        snapshot = _snapshot(task)
    worker = asyncio.create_task(
        _run_task(task, argv, gate.root, budget), name=f"plugin-publish-{task.task_id}"
    )
    _workers[task.task_id] = worker
    worker.add_done_callback(lambda _done, task_id=task.task_id: _workers.pop(task_id, None))
    return snapshot


def latest_publish_task(project_id: str) -> dict[str, object]:
    with _lock:
        task = _tasks.get(_latest.get(project_id, ""))
        if task is None:
            raise project_error(
                Code.PUBLISH_TASK_NOT_FOUND, f"no publish task for project {project_id}", 404
            )
        return _snapshot(task)


async def cancel_publish(project_id: str, task_id: str) -> dict[str, object]:
    with _lock:
        task = _tasks.get(task_id)
        if task is None or task.project_id != project_id:
            raise project_error(Code.PUBLISH_TASK_NOT_FOUND, f"unknown publish task: {task_id}", 404)
        if task.status in _TERMINAL_STATUSES:
            return _snapshot(task)
        task.cancel_requested = True
    await asyncio.to_thread(_terminate, task)
    with _lock:
        return _snapshot(task)


def _prune() -> None:
    """Drop finished tasks once too many accumulated; each project keeps its latest."""
    if len(_tasks) <= _MAX_TASKS:
        return
    keep = set(_latest.values())
    finished = sorted(
        (task for task in _tasks.values() if task.status in _TERMINAL_STATUSES and task.task_id not in keep),
        key=lambda task: task.finished_at or 0.0,
    )
    for task in finished[: len(_tasks) - _MAX_TASKS]:
        _tasks.pop(task.task_id, None)
        if _active.get(task.project_id) == task.task_id:
            _active.pop(task.project_id, None)


def _stream(task: _Task, argv: list[str], cwd: Path) -> tuple[int | None, str]:
    """Run the CLI to completion, collecting its output. Called in a thread."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return None, f"publish command could not start: {exc}"
    with _lock:
        task.process = process
        cancelled = task.cancel_requested
    if cancelled:
        _terminate_process(process)
    with _lock:
        task.status = "running"
        task.started_at = time.time()
        task.updated_at = task.started_at
    assert process.stdout is not None
    for line in process.stdout:
        with _lock:
            task.log.append(line.rstrip("\n")[:_LOG_LINE_CHARS])
            task.updated_at = time.time()
    return process.wait(), ""


def _terminate_process(process: subprocess.Popen[str]) -> None:
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
        for child in children:
            try:
                child.kill()
            except psutil.Error:
                pass
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - escalation best-effort
            logger.warning("publish subprocess did not exit after kill: pid={}", process.pid)


def _terminate(task: _Task) -> None:
    with _lock:
        process = task.process
    if process is not None:
        _terminate_process(process)


def _failure_message(task: _Task, returncode: int | None) -> str:
    with _lock:
        lines = list(task.log)
    failures = [line for line in lines if "[FAIL]" in line]
    if failures:
        return failures[-1][:_LOG_LINE_CHARS]
    if returncode is None:
        return "the publish task was interrupted before completion"
    return f"publish exited with code {returncode}"


def _extract_release_url(task: _Task) -> str:
    with _lock:
        lines = list(task.log)
    for line in reversed(lines):
        match = _RELEASE_URL_PATTERN.search(line)
        if match:
            return match.group(1)
    return ""


async def _run_task(task: _Task, argv: list[str], cwd: Path, budget: float) -> None:
    stream = asyncio.create_task(asyncio.to_thread(_stream, task, argv, cwd))
    returncode: int | None = None
    spawn_error = ""
    timed_out = False
    try:
        returncode, spawn_error = await asyncio.wait_for(asyncio.shield(stream), budget)
    except TimeoutError:
        timed_out = True
        await asyncio.to_thread(_terminate, task)
        try:
            returncode, spawn_error = await asyncio.wait_for(
                asyncio.shield(stream), _TERMINATE_GRACE_SECONDS
            )
        except TimeoutError:  # pragma: no cover - a killed process reaches EOF
            logger.warning("publish stream did not end after terminate: task={}", task.task_id)
    except asyncio.CancelledError:
        # Server shutdown must not orphan a child that may still push a tag.
        _terminate(task)
        raise
    finally:
        _finalize(task, returncode, spawn_error, timed_out, budget)


def _finalize(
    task: _Task, returncode: int | None, spawn_error: str, timed_out: bool, budget: float
) -> None:
    with _lock:
        if task.status in _TERMINAL_STATUSES:
            return
        if task.cancel_requested:
            task.status = "canceled"
            task.message = "the publish task was canceled"
        elif spawn_error:
            task.status = "failed"
            task.message = spawn_error
        elif timed_out:
            task.status = "failed"
            task.message = f"publish timed out after {budget:.0f} seconds"
        elif returncode == 0:
            task.status = "succeeded"
        else:
            task.status = "failed"
            task.message = _failure_message(task, returncode)
        if task.status == "succeeded":
            task.release_url = _extract_release_url(task) or task.release_url
        task.finished_at = time.time()
        task.updated_at = task.finished_at
        if _active.get(task.project_id) == task.task_id:
            _active.pop(task.project_id, None)
    logger.info(
        "publish task finished: task={}, mode={}, status={}", task.task_id, task.mode, task.status
    )
