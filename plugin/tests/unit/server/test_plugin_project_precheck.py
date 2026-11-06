"""Unit tests for the workbench pre-release check endpoint."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import psutil
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.neko_plugin_cli.core import project as creator
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_projects as service
from plugin.core.state import state as runtime_state
from plugin.server.application.plugins import operation_lock
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"
REPORT = {
    "plugin_id": "pre_demo",
    "version": "0.0.1",
    "plugin_dir": "D:/demo",
    "passed": False,
    "ruff_version": "0.12.4",
    "environment_issues": [["warning", "git working tree has uncommitted changes"]],
    "stages": [{"name": "lint", "passed": True, "output": ""}],
}


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: state / "plugins")
    monkeypatch.setattr(
        operation_lock, "_operation_file_lock_path", lambda: state / "operation.lock"
    )
    monkeypatch.setattr(runtime_state, "plugins", {})
    commands._busy.clear()
    yield state
    commands._busy.clear()


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_plugin_mutation_access] = lambda: None
    return application


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )


def _scaffold(parent: Path, plugin_id: str) -> Path:
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    target = parent / plugin_id
    creator.create_project(PluginSpec(plugin_id, quick_start=True), target, repo_root=None)
    return target


def _stub_cli(monkeypatch, stdout: str, returncode: int = 0):
    calls: list[list[str]] = []

    async def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(service, "run_cli", fake_run)
    return calls


@pytest.mark.asyncio
async def test_precheck_returns_the_cli_report_verbatim(app, tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "pre_demo"))
    calls = _stub_cli(monkeypatch, json.dumps(REPORT))
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/precheck", json={})
    assert response.status_code == 200, response.text
    assert response.json() == REPORT
    argv = calls[0]
    assert argv[-1] == "--json"
    assert "precheck" in argv
    assert str(record.source_dir) in argv


@pytest.mark.asyncio
async def test_precheck_unknown_project_is_not_found(app, monkeypatch):
    _stub_cli(monkeypatch, json.dumps(REPORT))
    async with client(app) as http:
        response = await http.post("/plugin-projects/gone/precheck", json={})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_precheck_unavailable_outside_a_source_checkout(app, tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "pre_demo"))
    monkeypatch.setattr(service, "source_repo_root", lambda: None)
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/precheck", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PRECHECK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_precheck_without_a_report_is_a_server_failure(app, tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "pre_demo"))
    _stub_cli(monkeypatch, "", returncode=1)
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/precheck", json={})
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "PRECHECK_FAILED_TO_RUN"


@pytest.mark.asyncio
async def test_precheck_is_refused_while_the_project_is_publishing(app, tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "pre_demo"))
    calls = _stub_cli(monkeypatch, json.dumps(REPORT))
    commands.claim_project(record.project_id, "publish")
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/precheck", json={})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PLUGIN_OPERATION_BUSY"
    assert "publish" in detail["message"]
    assert calls == []


@pytest.mark.asyncio
async def test_precheck_timeout_is_reported_and_releases_the_project(app, tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "pre_demo"))

    async def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 600)

    monkeypatch.setattr(service, "run_cli", timeout)
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/precheck", json={})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "PRECHECK_FAILED_TO_RUN"
    assert record.project_id not in commands._busy


def test_cli_children_run_in_utf8_mode(monkeypatch):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    env = commands.cli_child_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_terminating_cli_waits_for_children_even_when_parent_exits(monkeypatch):
    from types import SimpleNamespace

    killed = []
    child = SimpleNamespace(terminate=lambda: None, kill=lambda: killed.append("child"))
    waits = []

    def wait_procs(children, timeout):
        waits.append(children)
        return ([], children) if len(waits) == 1 else (children, [])

    monkeypatch.setattr(commands, "psutil", SimpleNamespace(
        Process=lambda _pid: SimpleNamespace(children=lambda recursive: [child]),
        Error=psutil.Error, wait_procs=wait_procs,
    ))
    parent = SimpleNamespace(poll=lambda: None, pid=123, terminate=lambda: None, wait=lambda timeout: 0)
    commands.terminate_process_tree(parent)
    assert killed == ["child"]
    assert waits == [[child], [child]]



@pytest.mark.asyncio
async def test_run_cli_decodes_utf8_output(tmp_path):
    printed = await commands.run_cli(
        [sys.executable, "-c", "print('\\u63d2\\u4ef6\\u53d1\\u5e03')"], cwd=tmp_path, timeout=30
    )
    assert printed.stdout.strip() == "\u63d2\u4ef6\u53d1\u5e03"


@pytest.mark.asyncio
async def test_run_cli_timeout_kills_grandchildren_too(tmp_path):
    pid_file = tmp_path / "pid.txt"
    # The CLI stand-in starts a grandchild that a direct-child kill would orphan.
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        await commands.run_cli([sys.executable, "-c", script], cwd=tmp_path, timeout=5)
    grandchild = int(pid_file.read_text())
    _, alive = psutil.wait_procs(
        [process for process in [_process_or_none(grandchild)] if process], timeout=5
    )
    assert alive == []


def _process_or_none(pid):
    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None
