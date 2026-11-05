"""The repo-create server task maps the CLI's exit codes to UI outcomes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from plugin.server.application import plugin_repo_tasks as repo_tasks
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit


class _Completed:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def _patch(monkeypatch, *, returncode: int, stdout: str, root: Path | None = Path("/repo")):
    monkeypatch.setattr(repo_tasks, "source_repo_root", lambda: root)
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())
    monkeypatch.setattr(
        repo_tasks.subprocess,
        "run",
        lambda *a, **k: _Completed(returncode, stdout),
    )


def _run(monkeypatch, *, private: bool = False):
    return repo_tasks._run(Path("/plugins/demo"), private=private)


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
    monkeypatch.setattr(repo_tasks, "source_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="create-repo", timeout=180)

    monkeypatch.setattr(repo_tasks.subprocess, "run", _timeout)
    with pytest.raises(ServerDomainError) as excinfo:
        _run(monkeypatch)
    assert excinfo.value.code == "REPO_CREATE_FAILED"


def test_private_flag_is_forwarded_to_the_cli(monkeypatch):
    captured = {}

    def _capture(argv, **kwargs):
        captured["argv"] = argv
        return _Completed(0, "[OK] repository ready: https://github.com/o/n.e.k.o_plugin_demo")

    monkeypatch.setattr(repo_tasks, "source_repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(repo_tasks, "load_plugin_source", lambda _dir: object())
    monkeypatch.setattr(repo_tasks.subprocess, "run", _capture)
    _run(monkeypatch, private=True)
    assert "--private" in captured["argv"]
    assert "create-repo" in captured["argv"]
