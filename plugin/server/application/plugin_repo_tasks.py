"""Create a plugin project's GitHub repository from the workbench.

Like publishing, the server never talks to GitHub itself: it runs
``neko-plugin create-repo`` as a subprocess, so the CLI (and the user's own
``gh`` login) stays the only code that creates a repository. The command is
short-lived, so this runs it inline and returns a single structured outcome
rather than registering a long-lived task like ``plugin_publish_tasks``.

Outcomes mirror the CLI's exit codes:
  - ``created``  -- the repository now exists and ``origin`` is wired (exit 0)
  - ``manual``   -- ``gh`` is unavailable or not signed in; the log carries the
                    steps to do it by hand (exit 3)
  - ``failed``   -- anything else (name taken, foreign origin, git error)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from plugin.logging_config import get_logger
from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.server.application.plugin_project_commands import project_operation, run_cli
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store

logger = get_logger("server.application.plugin_repo_tasks")

# Exit code the CLI uses for "gh cannot do this; here are the manual steps".
_MANUAL_FALLBACK_EXIT = 3
_LOG_LINES = 100
_LOG_LINE_CHARS = 400

RepoCreateStatus = Literal["created", "manual", "failed"]


def _record_source_dir(project_id: str) -> Path:
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    return record.source_dir


def _clip(text: str) -> list[str]:
    lines = [line[:_LOG_LINE_CHARS] for line in text.splitlines() if line.strip()]
    return lines[-_LOG_LINES:]


async def _run(source_dir: Path, *, private: bool) -> dict[str, object]:
    root = source_repo_root()
    if root is None:
        raise project_error(
            Code.REPO_CREATE_UNAVAILABLE,
            "Creating a repository needs a source checkout; it is unavailable in a packaged build.",
            503,
        )
    # Fail fast with a clear local code if the project cannot even be read.
    load_plugin_source(source_dir)

    argv = [sys.executable, "-m", "plugin.neko_plugin_cli.cli", "create-repo", str(source_dir)]
    if private:
        argv.append("--private")
    budget = env_seconds("NEKO_PLUGIN_REPO_CREATE_BUDGET", 180.0)
    try:
        completed = await run_cli(argv, cwd=root, timeout=budget, merge_stderr=True)
    except subprocess.TimeoutExpired as exc:
        raise project_error(
            Code.REPO_CREATE_FAILED,
            f"creating the repository timed out after {budget:.0f} seconds",
            504,
        ) from exc
    except OSError as exc:
        raise project_error(
            Code.REPO_CREATE_FAILED, f"create-repo command could not start: {exc}", 500
        ) from exc

    log = _clip(completed.stdout or "")
    if completed.returncode == 0:
        status: RepoCreateStatus = "created"
    elif completed.returncode == _MANUAL_FALLBACK_EXIT:
        status = "manual"
    else:
        status = "failed"
    repository_url = _first_https_github_url(log)
    logger.info(
        "repo create finished: project_dir={} status={} rc={}",
        source_dir,
        status,
        completed.returncode,
    )
    return {
        "status": status,
        "repository_url": repository_url,
        "log": log,
        "finished_at": time.time(),
    }


def _first_https_github_url(log: list[str]) -> str:
    marker = "https://github.com/"
    for line in log:
        index = line.find(marker)
        if index != -1:
            return line[index:].split()[0].rstrip(".,")
    return ""


async def create_repository(project_id: str, *, private: bool = False) -> dict[str, object]:
    """Run ``neko-plugin create-repo`` for a project and return its outcome."""
    source_dir = _record_source_dir(project_id)
    with project_operation(project_id, "create-repo"):
        return await _run(source_dir, private=private)
