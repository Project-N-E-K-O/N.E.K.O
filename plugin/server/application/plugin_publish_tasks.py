"""Background publication tasks for authoring workbench projects.

One task runs ``neko-plugin publish`` as a subprocess, exactly like
``precheck`` runs the CI gates: the CLI stays the only code that pushes a
tag or notifies the Market, and the workbench can never drift from it. The
task registry is in memory — authoritative progress lives on GitHub, and the
read-only publish status reconciles against it after a server restart.
"""

from __future__ import annotations

import asyncio
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

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.repo_state import RepoState
from plugin.server.application.plugin_project_commands import (
    claim_project,
    release_project,
    start_cli,
    terminate_process_tree,
)
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.application.plugin_publish_status import (
    LocalStatus,
    RemoteGithubState,
    read_local_status,
    read_remote_github,
)
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error

logger = get_logger("server.application.plugin_publish_tasks")

PublishMode = Literal["github_and_market", "github", "market_notify"]
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled"})
_LOG_LINES = 200
_LOG_LINE_CHARS = 400
_MAX_TASKS = 64
_TERMINATE_GRACE_SECONDS = 15.0
# Part of the server budget kept for sync, release checks, ruff and the tag
# push before the CLI starts waiting for the GitHub Release.
_PRE_RELEASE_RESERVE_SECONDS = 300.0
_MIN_RELEASE_WAIT_SECONDS = 60.0
_RELEASE_URL_PATTERN = re.compile(r"GitHub Release ready: (\S+)")
# The CLI's one-line report of the Market's answer to the release notification.
_MARKET_STATUS_PATTERN = re.compile(r"^\[MARKET\] status=([a-z_]+)(?: candidate=([0-9]+))?$")
_RELEASE_WORKFLOW = ".github/workflows/release.yml"
# Lines the CLI starts itself; anything else after ``[FAIL]`` continues it.
_MARKER_PATTERN = re.compile(r"^\[[^\]]+\]")
_CAUSE_PATTERN = re.compile(r"^\s*(fatal:|error:|! \[)", re.IGNORECASE)


@dataclass
class _Task:
    task_id: str
    project_id: str
    mode: PublishMode
    status: str = "queued"
    message: str = ""
    release_url: str = ""
    market_status: str | None = None
    market_candidate_id: int | None = None
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
            "market_status": task.market_status,
            "market_candidate_id": task.market_candidate_id,
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


def _push_blockers(state: RepoState) -> list[str]:
    branch = state.branch
    if state.head is None:
        return ["the repository has no commits yet"]
    if state.detached:
        return ["HEAD is detached; check out the branch to publish from"]
    if state.upstream is None:
        return [f"branch {branch} has no upstream; run git push -u origin {branch}"]
    if not state.upstream.startswith("origin/"):
        return [
            f"branch {branch} tracks {state.upstream}, which is not on origin; "
            f"run git push -u origin {branch}"
        ]
    if state.head_pushed is not True:
        return [f"HEAD is not pushed to {state.upstream}; run git push"]
    return []


def _release_blockers(local: LocalStatus) -> list[str]:
    """What ``publish github`` would refuse before it pushes a tag."""
    state = local.repository
    reasons: list[str] = []
    if state.worktree_clean is None:
        reasons.append("git status could not be read")
    elif not state.worktree_clean:
        reasons.append(
            "the git working tree has uncommitted changes: " + ", ".join(state.dirty_paths)
        )
    reasons.extend(_push_blockers(state))
    from plugin.neko_plugin_cli.repo_action_migration import ActionFileStatus

    workflow = local.actions.get(_RELEASE_WORKFLOW)
    if workflow != ActionFileStatus.CURRENT:
        reasons.append(
            f"the standard release workflow {_RELEASE_WORKFLOW} is {workflow or 'missing'}; "
            f"run neko-plugin setup-repo {local.source_dir} --upgrade-github-actions"
        )
    if state.tag_exists and state.tag_at_head is False:
        reasons.append(
            f"tag {state.tag} already points at another commit; bump the version in plugin.toml"
        )
    return reasons


def _local_gate(project_id: str, mode: PublishMode) -> _Gate:
    """Refuse to start unless the CLI's own local preconditions already hold."""
    root = source_repo_root()
    if root is None:
        raise project_error(
            Code.PUBLISH_UNAVAILABLE,
            "Publishing needs a source checkout; it is unavailable in a packaged build.",
            503,
        )
    local = read_local_status(project_id)
    state = local.repository
    reasons: list[str] = []
    if not state.git_available:
        reasons.append("git is not available")
    elif not state.is_repository:
        reasons.append("the project directory does not have its own git repository")
    if not state.github_repository:
        reasons.append("git remote origin does not point to a GitHub repository")
    # Notifying the Market only names an existing release; local edits do not matter.
    if state.is_repository and mode != "market_notify":
        reasons.extend(_release_blockers(local))
    if reasons:
        raise project_error(Code.PUBLISH_NOT_READY, "; ".join(reasons), 409)
    assert state.github_repository is not None  # the check above raised without it
    return _Gate(
        root=root,
        source_dir=local.source_dir,
        plugin_id=local.plugin_id,
        repository=state.github_repository,
        head=state.head,
        tag=state.tag,
    )


def _remote_release_blockers(gate: _Gate, remote: RemoteGithubState) -> list[str]:
    """GitHub facts that make a new release pointless; unknown facts are left to the CLI."""
    reasons: list[str] = []
    if remote["visibility"] == "private":
        reasons.append(
            f"repository {gate.repository} is private; the Market needs a public repository"
        )
    elif remote["visibility"] == "not_found":
        reasons.append(
            f"repository {gate.repository} is not visible on GitHub (missing or private); "
            "the Market needs a public repository"
        )
    if remote["tag"] == "elsewhere":
        reasons.append(
            f"remote tag {gate.tag} already points at another commit; "
            "bump the version in plugin.toml"
        )
    return reasons


def _verified_release_url(gate: _Gate, remote: RemoteGithubState) -> str:
    # The Market rejects releases it cannot verify; check before asking.
    if remote["visibility"] in {"private", "not_found"}:
        raise project_error(
            Code.PUBLISH_NOT_READY, "The Market requires a public GitHub repository", 409
        )
    if remote["rate_limited"]:
        raise project_error(
            Code.PUBLISH_RATE_LIMITED,
            "GitHub rate limit reached; the release cannot be verified right now.",
            429,
        )
    if not remote["reachable"]:
        raise project_error(
            Code.PUBLISH_NOT_READY,
            "GitHub could not be reached; the release cannot be verified right now",
            409,
        )
    release = remote["release"]
    if release["state"] == "unknown":
        raise project_error(
            Code.PUBLISH_NOT_READY, f"release {gate.tag} could not be read from GitHub", 409
        )
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
    if not release["ready"]:
        raise project_error(
            Code.PUBLISH_NOT_READY,
            f"release workflow for {gate.tag} is {release['workflow']}; it must complete successfully",
            409,
        )
    return f"https://github.com/{gate.repository}/releases/tag/{quote(gate.tag, safe='')}"


async def start_publish(project_id: str, mode: PublishMode) -> dict[str, object]:
    gate = await asyncio.to_thread(_local_gate, project_id, mode)
    budget = env_seconds("NEKO_PLUGIN_PUBLISH_BUDGET", 1200.0)
    argv = [sys.executable, "-m", "plugin.neko_plugin_cli.cli", "publish"]
    release_url = ""
    if mode == "market_notify":
        remote = await read_remote_github(
            gate.repository, gate.head, gate.tag, gate.plugin_id, fresh=True
        )
        release_url = _verified_release_url(gate, remote)
        argv += ["market", release_url]
    else:
        remote = await read_remote_github(gate.repository, gate.head, gate.tag, gate.plugin_id)
        reasons = _remote_release_blockers(gate, remote)
        if reasons:
            raise project_error(Code.PUBLISH_NOT_READY, "; ".join(reasons), 409)
        if mode == "github":
            argv += ["github"]
        # The CLI's own wait must end before the server stops it.
        release_wait = max(budget - _PRE_RELEASE_RESERVE_SECONDS, _MIN_RELEASE_WAIT_SECONDS)
        argv += [str(gate.source_dir), "--timeout", f"{release_wait:.0f}"]
    if mode != "github":
        # The CLI retries while the Market verifies the release; the server
        # budget covers that wait on top of the release wait.
        market_wait = env_seconds("NEKO_PLUGIN_MARKET_WAIT", 1800.0)
        argv += ["--market-wait", f"{market_wait:.0f}"]
        budget += market_wait

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
        # Shared with precheck and create-repo: they touch the same tree.
        claim_project(project_id, "publish")
        _prune()
        _tasks[task.task_id] = task
        _active[project_id] = task.task_id
        _latest[project_id] = task.task_id
        snapshot = _snapshot(task)
    worker = asyncio.create_task(
        _run_task(task, argv, gate, budget), name=f"plugin-publish-{task.task_id}"
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
    with _lock:
        if task.cancel_requested:
            return None, ""
        try:
            process = start_cli(argv, cwd=cwd, merge_stderr=True)
        except OSError as exc:
            return None, f"publish command could not start: {exc}"
        task.process = process
        task.status = "running"
        task.started_at = time.time()
        task.updated_at = task.started_at
    assert process.stdout is not None
    while line := process.stdout.readline(_LOG_LINE_CHARS + 1):
        with _lock:
            task.log.append(line.rstrip("\n")[:_LOG_LINE_CHARS])
            task.updated_at = time.time()
        # Retain one bounded entry per line, discarding oversized tails in chunks.
        while line and not line.endswith("\n"):
            line = process.stdout.readline(_LOG_LINE_CHARS + 1)
    return process.wait(), ""


def _terminate(task: _Task) -> None:
    with _lock:
        process = task.process
    if process is not None:
        terminate_process_tree(process)


def _failure_message(task: _Task, returncode: int | None) -> str:
    """The last ``[FAIL]`` line, completed by the cause a multi-line error carries.

    A git error prints its first stderr line on the ``[FAIL]`` line (often
    ``To https://...``); the actual ``fatal:`` / ``! [rejected]`` line follows.
    """
    with _lock:
        lines = list(task.log)
    starts = [index for index, line in enumerate(lines) if "[FAIL]" in line]
    if not starts:
        if returncode is None:
            return "the publish task was interrupted before completion"
        return f"publish exited with code {returncode}"
    start = starts[-1]
    continuation: list[str] = []
    for line in lines[start + 1 :]:
        if _MARKER_PATTERN.match(line):
            break
        if line.strip():
            continuation.append(line.strip())
    causes = [line for line in continuation if _CAUSE_PATTERN.match(line)]
    cause = causes[-1] if causes else (continuation[-1] if continuation else "")
    message = f"{lines[start]} | {cause}" if cause else lines[start]
    return message[:_LOG_LINE_CHARS]


def _extract_release_url(task: _Task) -> str:
    with _lock:
        lines = list(task.log)
    for line in reversed(lines):
        match = _RELEASE_URL_PATTERN.search(line)
        if match:
            return match.group(1)
    return ""


def _extract_market_result(task: _Task) -> tuple[str | None, int | None]:
    """The Market status and review candidate from the CLI's ``[MARKET]`` line."""
    with _lock:
        lines = list(task.log)
    for line in reversed(lines):
        match = _MARKET_STATUS_PATTERN.match(line.strip())
        if match:
            candidate = match.group(2)
            return match.group(1), int(candidate) if candidate else None
    return None, None


async def _timeout_message(task: _Task, gate: _Gate, budget: float) -> str:
    message = f"publish timed out after {budget:.0f} seconds"
    if task.mode == "market_notify":
        return message
    remote = await read_remote_github(
        gate.repository, gate.head, gate.tag, gate.plugin_id, fresh=True
    )
    if remote["tag"] != "at_head":
        return message
    return (
        f"{message} after tag {gate.tag} was pushed; the GitHub Release may still be in "
        "progress. Notify the Market once it appears."
    )


async def _run_task(task: _Task, argv: list[str], gate: _Gate, budget: float) -> None:
    stream = asyncio.create_task(asyncio.to_thread(_stream, task, argv, gate.root))
    returncode: int | None = None
    spawn_error = ""
    timeout_message = ""
    try:
        returncode, spawn_error = await asyncio.wait_for(asyncio.shield(stream), budget)
    except TimeoutError:
        await asyncio.to_thread(_terminate, task)
        try:
            returncode, spawn_error = await asyncio.wait_for(
                asyncio.shield(stream), _TERMINATE_GRACE_SECONDS
            )
        except TimeoutError:  # pragma: no cover - a killed process reaches EOF
            logger.warning("publish stream did not end after terminate: task={}", task.task_id)
        timeout_message = await _timeout_message(task, gate, budget)
    except asyncio.CancelledError:
        # Server shutdown must not orphan a child that may still push a tag.
        with _lock:
            task.cancel_requested = True
        await asyncio.to_thread(_terminate, task)
        try:
            await asyncio.wait_for(asyncio.shield(stream), _TERMINATE_GRACE_SECONDS)
        except TimeoutError:
            logger.warning("canceled publish stream did not end: task={}", task.task_id)
        raise
    finally:
        _finalize(task, returncode, spawn_error, timeout_message)
        release_project(task.project_id)


def _finalize(
    task: _Task, returncode: int | None, spawn_error: str, timeout_message: str
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
        elif timeout_message:
            task.status = "failed"
            task.message = timeout_message
        elif returncode == 0:
            task.status = "succeeded"
        else:
            task.status = "failed"
            task.message = _failure_message(task, returncode)
        task.release_url = _extract_release_url(task) or task.release_url
        # A rejected notification exits 1, so the status is read on failure too.
        task.market_status, task.market_candidate_id = _extract_market_result(task)
        task.finished_at = time.time()
        task.updated_at = task.finished_at
        if _active.get(task.project_id) == task.task_id:
            _active.pop(task.project_id, None)
    logger.info(
        "publish task finished: task={}, mode={}, status={}", task.task_id, task.mode, task.status
    )
