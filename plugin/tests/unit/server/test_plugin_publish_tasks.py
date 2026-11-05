"""Background publish tasks: local gate, task lifecycle, routes."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import psutil
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.core.state import state as runtime_state
from plugin.neko_plugin_cli.repo_state import RepoState
from plugin.server.application import plugin_publish_tasks as tasks
from plugin.server.application.plugins import operation_lock
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path_factory):
    state_dir = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: state_dir / "plugins")
    monkeypatch.setattr(
        operation_lock, "_operation_file_lock_path", lambda: state_dir / "operation.lock"
    )
    monkeypatch.setattr(runtime_state, "plugins", {})
    with tasks._lock:
        tasks._tasks.clear()
        tasks._active.clear()
        tasks._latest.clear()
        tasks._workers.clear()
    yield state_dir
    with tasks._lock:
        tasks._tasks.clear()
        tasks._active.clear()
        tasks._latest.clear()
        tasks._workers.clear()


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


def _ready_gate(monkeypatch, tmp_path):
    """Stub the gate's inputs so a plain directory looks publishable."""
    monkeypatch.setattr(tasks, "source_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        tasks,
        "load_plugin_source",
        lambda _dir: SimpleNamespace(plugin_id="demo", version="0.1.0"),
    )
    monkeypatch.setattr(
        tasks,
        "read_repo_state",
        lambda _dir, *, version: RepoState(
            git_available=True,
            is_repository=True,
            tag=f"v{version}",
            origin_url="https://github.com/owner/repo.git",
            github_repository="owner/repo",
            branch="main",
            upstream="origin/main",
            head="a" * 40,
            head_pushed=True,
            worktree_clean=True,
            tag_exists=False,
            tag_at_head=None,
        ),
    )


def _scripted_process(lines, returncode=0):
    class FakePopen:
        calls: list[list[str]] = []

        def __init__(self, argv, **_kwargs):
            FakePopen.calls.append(list(argv))
            self.argv = argv
            self.pid = 0
            self.returncode = None
            self.stdout = iter([*lines, ""])
            self._returncode = returncode

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = self._returncode
            return self.returncode

    return FakePopen


def _blocking_process():
    class FakePopen:
        def __init__(self, argv, **_kwargs):
            self.argv = argv
            self.pid = 0
            self.returncode = None
            self.terminated = False
            self._stop = threading.Event()
            self.stdout = self

        def __iter__(self):
            return self

        def __next__(self):
            if self._stop.wait(10):
                raise StopIteration
            raise RuntimeError("test process was never terminated")

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15
            self._stop.set()

        def kill(self):
            self.terminate()

        def wait(self, timeout=None):
            self._stop.wait(timeout if timeout is not None else 10)
            return self.returncode

    return FakePopen


@pytest.fixture
def no_psutil(monkeypatch):
    """Scoped to this module: the fakes report pid 0, which no real psutil may see."""

    def missing(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(
        tasks, "psutil", SimpleNamespace(Process=missing, Error=psutil.Error)
    )


async def _wait_terminal(project_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    snapshot: dict = {}
    while time.monotonic() < deadline:
        snapshot = tasks.latest_publish_task(project_id)
        if snapshot["status"] in {"succeeded", "failed", "canceled"}:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"task did not finish: {snapshot}")


async def test_publish_needs_a_source_checkout(app, tmp_path, monkeypatch):
    record = store.add_record(tmp_path)
    monkeypatch.setattr(tasks, "source_repo_root", lambda: None)
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PUBLISH_UNAVAILABLE"


async def test_publish_refuses_a_project_that_fails_the_local_gate(app, tmp_path, monkeypatch):
    record = store.add_record(tmp_path / "no-git-here")
    monkeypatch.setattr(tasks, "source_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        tasks,
        "load_plugin_source",
        lambda _dir: SimpleNamespace(plugin_id="demo", version="0.1.0"),
    )
    monkeypatch.setattr(
        tasks,
        "read_repo_state",
        lambda _dir, *, version: RepoState(
            git_available=True, is_repository=False, tag=f"v{version}"
        ),
    )
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PUBLISH_NOT_READY"
    assert "git repository" in detail["message"]


async def test_publish_runs_the_cli_and_tracks_the_task(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    fake = _scripted_process(
        [
            "[1/4] syncing",
            "[OK] GitHub Release ready: https://github.com/owner/repo/releases/tag/v0.1.0",
            "[OK] Market published v0.1.0",
        ]
    )
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)
    async with client(app) as http:
        started = await http.post(
            f"/plugin-projects/{record.project_id}/publish",
            json={"mode": "github_and_market"},
        )
        assert started.status_code == 202, started.text
        payload = started.json()
        assert payload["status"] == "queued"
        assert payload["mode"] == "github_and_market"
        finished = await _wait_terminal(record.project_id)
        latest = await http.get(f"/plugin-projects/{record.project_id}/publish-task")
    assert latest.status_code == 200
    assert latest.json()["task_id"] == finished["task_id"]
    assert finished["status"] == "succeeded"
    assert finished["release_url"] == "https://github.com/owner/repo/releases/tag/v0.1.0"
    assert "[OK] Market published v0.1.0" in finished["log"]
    argv = fake.calls[0]
    assert argv[1:4] == ["-m", "plugin.neko_plugin_cli.cli", "publish"]
    assert str(record.source_dir) in argv
    assert "market" not in argv[4:]


async def test_publish_failure_reports_the_last_fail_line(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tasks.subprocess,
        "Popen",
        _scripted_process(
            ["[1/4] syncing", "[FAIL] release checks did not pass / 发布检查未通过"],
            returncode=1,
        ),
    )
    async with client(app) as http:
        await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
    finished = await _wait_terminal(record.project_id)
    assert finished["status"] == "failed"
    assert finished["message"] == "[FAIL] release checks did not pass / 发布检查未通过"
    assert finished["release_url"] == ""


async def test_second_publish_while_one_is_active_is_refused(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    fake = _blocking_process()
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)
    async with client(app) as http:
        first = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
        assert first.status_code == 202
        second = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "PUBLISH_TASK_ACTIVE"
        cancel = await http.post(
            f"/plugin-projects/{record.project_id}/publish-task/"
            f"{first.json()['task_id']}/cancel"
        )
    assert cancel.status_code == 200
    finished = await _wait_terminal(record.project_id)
    assert finished["status"] == "canceled"


async def test_market_notify_verifies_the_release_before_asking(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    fake = _scripted_process(["[OK] Market published v0.1.0"])
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)

    async def remote_missing(*_args):
        return {
            "rate_limited": False,
            "visibility": "public",
            "verify": "success",
            "release": {"state": "missing", "missing_assets": []},
        }

    monkeypatch.setattr(tasks, "read_remote_github", remote_missing)
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PUBLISH_NOT_READY"
    assert fake.calls == []

    async def remote_published(*_args):
        return {
            "rate_limited": False,
            "visibility": "public",
            "verify": "success",
            "release": {"state": "published", "missing_assets": []},
        }

    monkeypatch.setattr(tasks, "read_remote_github", remote_published)
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
        assert response.status_code == 202
    finished = await _wait_terminal(record.project_id)
    assert finished["status"] == "succeeded"
    assert fake.calls[0][-2:] == [
        "market",
        "https://github.com/owner/repo/releases/tag/v0.1.0",
    ]


async def test_publish_status_route_rejects_an_unknown_mode(app, tmp_path):
    record = store.add_record(tmp_path)
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "delete_everything"}
        )
    assert response.status_code == 422


async def test_publish_times_out_and_terminates_the_cli(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setenv("NEKO_PLUGIN_PUBLISH_BUDGET", "1")
    fake = _blocking_process()
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)
    async with client(app) as http:
        started = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
    assert started.status_code == 202
    finished = await _wait_terminal(record.project_id)
    assert finished["status"] == "failed"
    assert "timed out" in finished["message"]


async def test_task_lookup_for_a_project_that_never_published(app, tmp_path):
    record = store.add_record(tmp_path)
    async with client(app) as http:
        response = await http.get(f"/plugin-projects/{record.project_id}/publish-task")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PUBLISH_TASK_NOT_FOUND"
