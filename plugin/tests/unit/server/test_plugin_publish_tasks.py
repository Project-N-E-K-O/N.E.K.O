"""Background publish tasks: local gate, task lifecycle, routes."""

from __future__ import annotations

import asyncio
import io
import sys
import threading
import time
from pathlib import Path
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
from plugin.server.application.plugin_tool_environment import (
    ToolEnvironment,
    ToolEnvironmentError,
)
from plugin.server.application.plugins import operation_lock
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.mutation_auth import csrf_token, require_plugin_mutation_access
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
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token()},
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
    "release": {"state": "missing", "missing_assets": [], 'workflow': 'unknown', 'ready': False},
    "tag": "missing",
}


def _ready_gate(monkeypatch, tmp_path, *, actions=None, remote=None, **state):
    """Stub the gate's inputs so a plain directory looks publishable."""
    monkeypatch.setattr(
        tasks,
        "tool_environment",
        lambda: ToolEnvironment(Path(sys.executable), tmp_path, {}),
    )
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
            self.stdout = io.StringIO("\n".join(lines) + "\n")
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

        def readline(self, size=-1):
            if self._stop.wait(10):
                return ""
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


async def test_canceling_the_worker_before_stream_start_prevents_cli_spawn(tmp_path, monkeypatch):
    task = tasks._Task("delayed", "delayed-project", "github")
    gate = tasks._Gate(
        ToolEnvironment(Path(sys.executable), tmp_path, {}),
        tmp_path, "demo", "owner/repo", "a" * 40, "v0.1.0",
    )
    entered, release = threading.Event(), threading.Event()
    terminated = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = tasks._stream
    original_terminate = tasks._terminate
    process = _scripted_process(["must not run"])
    monkeypatch.setattr(tasks.subprocess, "Popen", process)

    def delayed(*args):
        entered.set()
        release.wait(10)
        return original(*args)

    def terminate(task):
        original_terminate(task)
        loop.call_soon_threadsafe(terminated.set)

    monkeypatch.setattr(tasks, "_stream", delayed)
    monkeypatch.setattr(tasks, "_terminate", terminate)
    commands.claim_project(task.project_id, "publish")
    worker = asyncio.create_task(tasks._run_task(task, ["ignored"], gate, 30))
    assert await asyncio.to_thread(entered.wait, 5)
    worker.cancel()
    await asyncio.wait_for(terminated.wait(), 5)
    assert task.cancel_requested
    assert task.project_id in commands._busy
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert process.calls == []
    assert task.status == "canceled"
    assert task.project_id not in commands._busy


async def test_publish_needs_the_plugin_tool_environment(app, tmp_path, monkeypatch):
    record = store.add_record(tmp_path)
    def _unavailable():
        raise ToolEnvironmentError("prepare the plugin tool environment first")

    monkeypatch.setattr(tasks, "tool_environment", _unavailable)
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
            "release": {"state": "missing", "missing_assets": [], 'workflow': 'unknown', 'ready': False},
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
            "release": {"state": "published", "missing_assets": [], 'workflow': 'success', 'ready': True},
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
    assert fake.calls[0][-4:] == [
        "market",
        "https://github.com/owner/repo/releases/tag/v0.1.0",
        "--market-wait",
        "1800",
    ]


@pytest.mark.parametrize(
    ("lines", "returncode", "status", "market_status", "candidate"),
    [
        (["[OK] GitHub Release ready", "[MARKET] status=published"], 0, "succeeded", "published", None),
        (["[MARKET] status=pending_review candidate=17"], 0, "succeeded", "pending_review", 17),
        (["[MARKET] status=rejected candidate=9"], 1, "failed", "rejected", 9),
        (["[OK] GitHub Release ready: https://github.com/owner/repo/releases/tag/v0.1.0", "[MARKET] status=rejected candidate=9"], 1, "failed", "rejected", 9),
        (["[OK] done"], 0, "succeeded", None, None),
    ],
)
async def test_the_task_reports_the_market_status_line(
    app, tmp_path, monkeypatch, no_psutil, lines, returncode, status, market_status, candidate
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks.subprocess, "Popen", _scripted_process(lines, returncode))
    async with client(app) as http:
        await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github_and_market"}
        )
        await _wait_terminal(record.project_id)
        latest = (await http.get(f"/plugin-projects/{record.project_id}/publish-task")).json()
    assert latest["status"] == status
    assert latest["market_status"] == market_status
    assert latest["market_candidate_id"] == candidate
    if any("https://github.com/owner/repo/releases/tag/v0.1.0" in line for line in lines):
        assert latest["release_url"] == "https://github.com/owner/repo/releases/tag/v0.1.0"
    if returncode:
        # A rejection keeps the existing failure message; there is no [FAIL] line here.
        assert latest["message"] == "publish exited with code 1"


async def test_a_running_task_has_no_market_status_yet(app, tmp_path, monkeypatch, no_psutil):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks.subprocess, "Popen", _blocking_process())
    async with client(app) as http:
        started = (
            await http.post(f"/plugin-projects/{record.project_id}/publish", json={"mode": "github"})
        ).json()
        assert started["market_status"] is None
        assert started["market_candidate_id"] is None
        await http.post(
            f"/plugin-projects/{record.project_id}/publish-task/{started['task_id']}/cancel"
        )
    await _wait_terminal(record.project_id)


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
    assert "apply the workflow update in the plugin workbench" in caught.value.message


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
        remote={"release": {"state": "published", "missing_assets": [], 'workflow': 'success', 'ready': True}},
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
    _ready_gate(monkeypatch, tmp_path, remote={"reachable": False, "release": {"state": "unknown", "missing_assets": [], 'workflow': 'unknown', 'ready': False}})
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
    assert response.status_code == 409
    assert "could not be reached" in response.json()["detail"]["message"]


@pytest.mark.parametrize("workflow", ["running", "failure", "unknown", "not_run"])
async def test_market_notify_waits_for_a_successful_release_workflow(app, tmp_path, monkeypatch, workflow):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path, remote={"release": {
        "state": "published", "missing_assets": [], "workflow": workflow, "ready": False,
    }})
    async with client(app) as http:
        response = await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"}
        )
    assert response.status_code == 409
    assert "must complete successfully" in response.json()["detail"]["message"]


@pytest.mark.parametrize("visibility", ["private", "not_found"])
async def test_market_notify_rejects_non_public_repositories(app, tmp_path, monkeypatch, visibility):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path, remote={"visibility": visibility})
    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/publish", json={"mode": "market_notify"})
    assert response.status_code == 409
    assert "public GitHub repository" in response.json()["detail"]["message"]


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
    assert "--market-wait" not in argv


async def test_publish_to_the_market_gives_the_cli_its_market_wait(
    app, tmp_path, monkeypatch, no_psutil
):
    record = store.add_record(tmp_path)
    _ready_gate(monkeypatch, tmp_path)
    monkeypatch.setenv("NEKO_PLUGIN_MARKET_WAIT", "600")
    fake = _scripted_process(["[MARKET] status=published", "[OK] done"])
    monkeypatch.setattr(tasks.subprocess, "Popen", fake)
    async with client(app) as http:
        await http.post(
            f"/plugin-projects/{record.project_id}/publish", json={"mode": "github_and_market"}
        )
    await _wait_terminal(record.project_id)
    argv = fake.calls[0]
    assert argv[argv.index("--market-wait") + 1] == "600"


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


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_slow_spawn_does_not_block_polling_and_termination_reaps_the_child(tmp_path, monkeypatch, stop):
    task = tasks._Task("slow-spawn", "project", "github")
    entered, release = threading.Event(), threading.Event()
    child = _scripted_process([])(["ignored"])
    terminated = []

    def start(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return child

    monkeypatch.setattr(tasks, "start_cli", start)
    monkeypatch.setattr(tasks, "terminate_process_tree", terminated.append)
    with tasks._lock:
        tasks._tasks[task.task_id] = task
        tasks._latest[task.project_id] = task.task_id
    streaming = asyncio.create_task(
        asyncio.to_thread(
            tasks._stream, task, ["ignored"], ToolEnvironment(Path(sys.executable), tmp_path, {})
        )
    )
    assert await asyncio.to_thread(entered.wait, 2)
    try:
        snapshot = await asyncio.wait_for(asyncio.to_thread(tasks.latest_publish_task, task.project_id), 1)
        assert snapshot["status"] == "queued"
        if stop == "cancel":
            await tasks.cancel_publish(task.project_id, task.task_id)
        else:
            await asyncio.to_thread(tasks._terminate, task)
    finally:
        release.set()
        result = await streaming
    assert result == (None, "")
    assert terminated == [child]
    assert task.process is None
