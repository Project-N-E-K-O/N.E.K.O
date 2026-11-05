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
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_publish_tasks as tasks
from plugin.server.application.plugin_publish_status import LocalStatus
from plugin.server.application.plugins import operation_lock
from plugin.server.domain.errors import ServerDomainError
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
    commands._busy.clear()
    yield state_dir
    with tasks._lock:
        tasks._tasks.clear()
        tasks._active.clear()
        tasks._latest.clear()
        tasks._workers.clear()
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


_READY = dict(
    git_available=True,
    is_repository=True,
    tag="v0.1.0",
    origin_url="https://github.com/owner/repo.git",
    github_repository="owner/repo",
    branch="main",
    upstream="origin/main",
    head="a" * 40,
    head_pushed=True,
    worktree_clean=True,
    tag_exists=False,
    tag_at_head=None,
)
_REMOTE = {
    "rate_limited": False,
    "reachable": True,
    "visibility": "public",
    "verify": "success",
    "release": {"state": "missing", "missing_assets": []},
    "tag": "missing",
}


def _ready_gate(monkeypatch, tmp_path, *, actions=None, remote=None, **state):
    """Stub the gate's inputs so a plain directory looks publishable."""
    monkeypatch.setattr(tasks, "source_repo_root", lambda: tmp_path)
    local = LocalStatus(
        tmp_path,
        "demo",
        "0.1.0",
        RepoState(**{**_READY, **state}),
        {".github/workflows/release.yml": "CURRENT"} if actions is None else actions,
    )
    monkeypatch.setattr(tasks, "read_local_status", lambda _id: local)
    reads = []

    async def read_remote(*args, fresh=False):
        reads.append((args, fresh))
        return {**_REMOTE, **(remote or {})}

    monkeypatch.setattr(tasks, "read_remote_github", read_remote)
    return reads


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
        commands, "psutil", SimpleNamespace(Process=missing, Error=psutil.Error)
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
    _ready_gate(monkeypatch, tmp_path, is_repository=False, github_repository=None)
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

    async def remote_missing(*_args, fresh=False):
        return {
            **_REMOTE,
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

    async def remote_published(*_args, fresh=False):
        return {
            **_REMOTE,
            "release": {"state": "published", "missing_assets": []},
            "tag": "at_head",
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



@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            {"worktree_clean": False, "dirty_paths": ("helper.py", "notes.txt")},
            "uncommitted changes: helper.py, notes.txt",
        ),
        ({"upstream": None, "head_pushed": None}, "run git push -u origin main"),
        (
            {"branch": None, "upstream": None, "detached": True, "head_pushed": None},
            "HEAD is detached",
        ),
        ({"upstream": "fork/main", "head_pushed": False}, "tracks fork/main, which is not on origin"),
        ({"head_pushed": False}, "HEAD is not pushed to origin/main; run git push"),
        ({"tag_exists": True, "tag_at_head": False}, "tag v0.1.0 already points at another commit"),
    ],
)
def test_the_gate_names_what_publish_github_would_refuse(monkeypatch, tmp_path, state, expected):
    _ready_gate(monkeypatch, tmp_path, **state)
    with pytest.raises(ServerDomainError) as caught:
        tasks._local_gate("p", "github")
    assert caught.value.code == "PUBLISH_NOT_READY"
    assert expected in caught.value.message


def test_the_gate_requires_the_current_release_workflow(monkeypatch, tmp_path):
    _ready_gate(monkeypatch, tmp_path, actions={".github/workflows/release.yml": "UPGRADE"})
    with pytest.raises(ServerDomainError) as caught:
        tasks._local_gate("p", "github_and_market")
    assert "release.yml is UPGRADE" in caught.value.message
    assert "--upgrade-github-actions" in caught.value.message


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ({"visibility": "private"}, "is private"),
        ({"visibility": "not_found"}, "not visible on GitHub"),
        ({"tag": "elsewhere"}, "remote tag v0.1.0 already points at another commit"),
    ],
)
async def test_github_publish_refuses_what_github_already_rules_out(
    app, tmp_path, monkeypatch, remote, expected
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path, remote=remote)
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
    assert response.status_code == 409
    assert expected in response.json()["detail"]["message"]


async def test_market_notify_skips_local_checks_and_reads_the_release_fresh(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    reads = _ready_gate(
        monkeypatch,
        tmp_path,
        worktree_clean=False,
        dirty_paths=("wip.py",),
        head_pushed=False,
        remote={"release": {"state": "published", "missing_assets": []}},
    )
    monkeypatch.setattr(tasks.subprocess, "Popen", _scripted_process(["[OK] Market published"]))
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
    assert response.status_code == 202, response.text
    await _wait_terminal(record.project_id)
    assert reads and all(fresh for _args, fresh in reads)


async def test_market_notify_on_an_unreachable_github_says_so(app, tmp_path, monkeypatch):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path, remote={"reachable": False, "release": {"state": "unknown", "missing_assets": []}})
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
    assert response.status_code == 409
    assert "could not be reached" in response.json()["detail"]["message"]


async def test_github_publish_gives_the_cli_a_release_wait_inside_the_server_budget(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setenv("NEKO_PLUGIN_PUBLISH_BUDGET", "1000")
    fake = _scripted_process(["[OK] done"])
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)
    async with client(app) as http:
        await http.post(f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"})
    await _wait_terminal(record.project_id)
    argv = fake.calls[0]
    assert argv[argv.index("--timeout") + 1] == "700"


async def test_failure_message_carries_the_git_cause_line(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tasks.subprocess,
        "Popen",
        _scripted_process(
            [
                "[FAIL] GitHub publication failed: To https://github.com/owner/repo.git",
                " ! [rejected]        v0.1.0 -> v0.1.0 (already exists)",
                "hint: Updates were rejected because the tag already exists in the remote.",
            ],
            returncode=1,
        ),
    )
    async with client(app) as http:
        await http.post(f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"})
    finished = await _wait_terminal(record.project_id)
    assert finished["message"] == (
        "[FAIL] GitHub publication failed: To https://github.com/owner/repo.git"
        " | ! [rejected]        v0.1.0 -> v0.1.0 (already exists)"
    )


async def test_timeout_after_the_tag_was_pushed_says_the_release_may_follow(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    reads = _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setenv("NEKO_PLUGIN_PUBLISH_BUDGET", "1")
    monkeypatch.setattr(tasks.subprocess, "Popen", _blocking_process())
    async with client(app) as http:
        await http.post(f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"})

        async def tag_pushed(*args, fresh=False):
            reads.append((args, fresh))
            return {**_REMOTE, "tag": "at_head"}

        monkeypatch.setattr(tasks, "read_remote_github", tag_pushed)
    finished = await _wait_terminal(record.project_id)
    assert finished["status"] == "failed"
    assert "after tag v0.1.0 was pushed" in finished["message"]
    assert "Notify the Market once it appears" in finished["message"]
    assert reads[-1][1] is True


async def test_publish_waits_for_a_precheck_of_the_same_project(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks.subprocess, "Popen", _scripted_process(["[OK] done"]))
    commands.claim_project(record.project_id, "precheck")
    async with client(app) as http:
        busy = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
        assert busy.status_code == 409
        assert busy.json()["detail"]["code"] == "PLUGIN_OPERATION_BUSY"
        assert "precheck" in busy.json()["detail"]["message"]
        commands.release_project(record.project_id)
        started = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"}
        )
        assert started.status_code == 202
    await _wait_terminal(record.project_id)
    # The finished task hands the project back.
    for _ in range(50):
        if record.project_id not in commands._busy:
            break
        await asyncio.sleep(0.02)
    assert record.project_id not in commands._busy
