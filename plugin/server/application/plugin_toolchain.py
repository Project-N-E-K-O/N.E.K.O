"""The tools publishing needs on this machine, read without network access.

The publish page lists them before the first step: git (the plugin's own
repository), uv (``uvx`` runs the pinned ruff in checks and ``publish``), the
GitHub CLI ``gh`` (repository creation and authenticated GitHub reads) and a
signed-in GitHub account. Every read is local: ``gh auth token`` and
``gh config get`` read gh's own files, so a slow GitHub cannot stall the page.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict

from plugin.server.application.plugin_project_commands import cli_child_env
from plugin.server.application.plugin_projects import source_repo_root
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store

_TIMEOUT_SECONDS = 10
_VERSION_PATTERN = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


class ToolState(TypedDict):
    installed: bool
    version: str | None


class GitState(ToolState):
    # Commits need an author; None when git is missing or the read failed.
    user_name: str | None
    user_email: str | None


class GhState(ToolState):
    # A token is stored for github.com (``gh auth login`` was run).
    authenticated: bool
    login: str | None


class Toolchain(TypedDict):
    git: GitState
    uv: ToolState
    gh: GhState
    # Checks, repository creation and publishing run the CLI from a N.E.K.O source checkout.
    source_checkout: bool


def _run(*argv: str) -> str | None:
    """stdout of a local command, or None when it is missing, fails or times out."""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=cli_child_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _version(output: str | None) -> str | None:
    match = _VERSION_PATTERN.search(output or "")
    return match.group(1) if match else None


def _git(project_dir: Path | None) -> GitState:
    if shutil.which("git") is None:
        return {"installed": False, "version": None, "user_name": None, "user_email": None}
    # The identity a commit in the plugin's own folder gets: its repository
    # config, then the global and system ones. Without a folder, global only;
    # this server's own checkout must not stand in for the plugin's.
    scope = ("-C", str(project_dir), "config") if project_dir else ("config", "--global")
    with ThreadPoolExecutor(max_workers=3) as pool:
        version = pool.submit(_run, "git", "--version")
        name = pool.submit(_run, "git", *scope, "user.name")
        email = pool.submit(_run, "git", *scope, "user.email")
    return {
        "installed": True,
        "version": _version(version.result()),
        "user_name": name.result() or None,
        "user_email": email.result() or None,
    }


def _uv() -> ToolState:
    # ``uvx`` ships with uv; the checks call it, so it is the one that must resolve.
    if shutil.which("uv") is None or shutil.which("uvx") is None:
        return {"installed": False, "version": None}
    return {"installed": True, "version": _version(_run("uv", "--version"))}


def _gh() -> GhState:
    if shutil.which("gh") is None:
        return {"installed": False, "version": None, "authenticated": False, "login": None}
    with ThreadPoolExecutor(max_workers=3) as pool:
        version = pool.submit(_run, "gh", "--version")
        token = pool.submit(_run, "gh", "auth", "token", "--hostname", "github.com")
        login = pool.submit(_run, "gh", "config", "get", "--host", "github.com", "user")
    authenticated = bool(token.result())
    return {
        "installed": True,
        "version": _version(version.result()),
        "authenticated": authenticated,
        "login": (login.result() or None) if authenticated else None,
    }


def read_toolchain(project_dir: Path | None = None) -> Toolchain:
    """Describe the publishing tools of this machine. Called in a thread.

    ``project_dir`` is the plugin folder whose commits need a git identity.
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        git = pool.submit(_git, project_dir if project_dir and project_dir.is_dir() else None)
        uv = pool.submit(_uv)
        gh = pool.submit(_gh)
    return {
        "git": git.result(),
        "uv": uv.result(),
        "gh": gh.result(),
        "source_checkout": source_repo_root() is not None,
    }


def read_project_toolchain(project_id: str) -> Toolchain:
    """The toolchain as seen from a registered project's folder."""
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    return read_toolchain(record.source_dir)
