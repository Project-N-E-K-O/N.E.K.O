"""The repo-create server task maps the CLI's exit codes to UI outcomes."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_projects
from plugin.server.application import plugin_repo_tasks as repo_tasks
from plugin.server.application.plugin_tool_environment import ToolEnvironment
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit


def _completed(returncode: int, stdout: str):
    async def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    return run


def _patch(monkeypatch, *, returncode: int, stdout: str, root: Path | None = Path("/repo")):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: root)
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())
    monkeypatch.setattr(repo_tasks, "run_cli", _completed(returncode, stdout))


def _run(monkeypatch, *, private: bool = False):
    return asyncio.run(repo_tasks._run(Path("/plugins/demo"), private=private))


def test_exit_zero_is_created_and_extracts_the_repository_url(monkeypatch):
    _patch(
        monkeypatch,
        returncode=0,
        stdout="[OK] repository ready: https://github.com/octocat/n.e.k.o_plugin_demo / 已就绪",
    )
    result = _run(monkeypatch)
    assert result["status"] == "created"
    assert result["repository_url"] == "https://github.com/octocat/n.e.k.o_plugin_demo"


def test_exit_three_is_manual_and_keeps_the_steps_in_the_log(monkeypatch):
    _patch(
        monkeypatch,
        returncode=3,
        stdout="[MANUAL] GitHub CLI (gh) is not available\n  1. Create a GitHub repository named n.e.k.o_plugin_demo",
    )
    result = _run(monkeypatch)
    assert result["status"] == "manual"
    assert result["repository_url"] == ""
    assert any("[MANUAL]" in line for line in result["log"])


def test_other_exit_codes_are_failed(monkeypatch):
    _patch(monkeypatch, returncode=1, stdout="[FAIL] a GitHub repository named octocat/x already exists")
    result = _run(monkeypatch)
    assert result["status"] == "failed"


def test_a_packaged_build_without_a_checkout_is_refused(monkeypatch):
    _patch(monkeypatch, returncode=0, stdout="", root=None)
    with pytest.raises(ServerDomainError) as excinfo:
        _run(monkeypatch)
    assert excinfo.value.code == "REPO_CREATE_UNAVAILABLE"


def test_a_timeout_is_a_failed_domain_error(monkeypatch):
    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())

    async def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="create-repo", timeout=180)

    monkeypatch.setattr(repo_tasks, "run_cli", _timeout)
    with pytest.raises(ServerDomainError) as excinfo:
        _run(monkeypatch)
    assert excinfo.value.code == "REPO_CREATE_FAILED"


def test_private_flag_is_forwarded_to_the_cli(monkeypatch):
    captured = {}

    async def _capture(argv, **kwargs):
        captured["argv"] = argv
        captured["merge_stderr"] = kwargs.get("merge_stderr")
        return subprocess.CompletedProcess(
            argv, 0, "[OK] repository ready: https://github.com/o/n.e.k.o_plugin_demo", ""
        )

    monkeypatch.setattr(plugin_projects, "source_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())
    monkeypatch.setattr(repo_tasks, "run_cli", _capture)
    _run(monkeypatch, private=True)
    assert "--private" in captured["argv"]
    assert "create-repo" in captured["argv"]


def test_a_packaged_build_uses_the_prepared_venv(monkeypatch):
    captured = {}

    async def _capture(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            argv, 0, "[OK] repository ready: https://github.com/o/n.e.k.o_plugin_demo", ""
        )

    venv = Path("/tools/env")
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    env = {"NEKO_PLUGIN_TEST_CONFIG": "/tools/pytest.ini"}
    monkeypatch.setattr(
        repo_tasks, "tool_environment", lambda: ToolEnvironment(python, venv, dict(env))
    )
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())
    monkeypatch.setattr(repo_tasks, "run_cli", _capture)
    _run(monkeypatch)
    assert captured["argv"][:3] == [str(python), "-m", "plugin.neko_plugin_cli.cli"]
    assert captured["argv"][3] == "create-repo"
    assert captured["cwd"] == venv
    assert captured["env"] == env


def test_create_repo_is_refused_while_the_project_is_busy(monkeypatch):
    _patch(monkeypatch, returncode=0, stdout="")
    monkeypatch.setattr(repo_tasks, "_record_source_dir", lambda _id: Path("/plugins/demo"))
    commands.claim_project("p1", "publish")
    try:
        with pytest.raises(ServerDomainError) as excinfo:
            asyncio.run(repo_tasks.create_repository("p1"))
    finally:
        commands.release_project("p1")
    assert excinfo.value.code == "PLUGIN_OPERATION_BUSY"


def test_create_repo_releases_the_project_when_done(monkeypatch):
    _patch(monkeypatch, returncode=0, stdout="")
    monkeypatch.setattr(repo_tasks, "_record_source_dir", lambda _id: Path("/plugins/demo"))
    assert asyncio.run(repo_tasks.create_repository("p1"))["status"] == "created"
    assert "p1" not in commands._busy
