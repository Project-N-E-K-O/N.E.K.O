"""Workbench package routes, leases and task lifetime without network installs."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import threading
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.core.state import state
from plugin.neko_plugin_cli.core.project import create_project
from plugin.neko_plugin_cli.templates.generator import PluginSpec
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_project_python_packages as packages
from plugin.server.application.plugins import operation_lock
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.mutation_auth import csrf_token, require_plugin_mutation_access
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: tmp_path / "state" / "plugins")
    monkeypatch.setattr(operation_lock, "_operation_file_lock_path", lambda: tmp_path / "operation.lock")
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    commands._busy.clear()
    packages._tasks.clear()
    packages._latest.clear()
    packages._workers.clear()
    yield
    commands._busy.clear()


@pytest.fixture
def record(tmp_path):
    directory = tmp_path / "package_demo"
    create_project(PluginSpec("package_demo", quick_start=True), directory, repo_root=None)
    return store.add_record(directory)


@pytest.fixture
def app():
    result = FastAPI()
    result.include_router(router)
    result.dependency_overrides[require_plugin_mutation_access] = lambda: None
    return result


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url=ORIGIN, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token()},
    )


async def finish(task):
    worker = packages._workers.get(task["task_id"])
    if worker is not None:
        await worker
    return packages.read_task(task["project_id"], task["task_id"])


@pytest.mark.asyncio
async def test_routes_read_save_revision_and_check(app, record):
    base = f"/plugin-projects/{record.project_id}/python-packages"
    async with client(app) as http:
        response = await http.get(base)
        assert response.status_code == 200, response.text
        initial = response.json()
        assert initial["requirements"] == []
        assert initial["task"] is None
        response = await http.put(base + "/requirements", json={
            "requirements": ["httpx>=0.27"], "revision": initial["revision"],
        })
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["requirements"][0]["status"] == "missing"
        stale = await http.put(base + "/requirements", json={
            "requirements": [], "revision": initial["revision"],
        })
        assert stale.status_code == 409
        response = await http.post(base + "/tasks", json={
            "operation": "check", "revision": saved["revision"],
        })
        assert response.status_code == 202, response.text
        task = await finish(response.json())
        assert task["status"] == "succeeded", task
        assert task["result"]["after"]["issues"]
        snapshot = (await http.get(base)).json()
        assert snapshot["task"]["task_id"] == task["task_id"]
        assert snapshot["operation"] is None


@pytest.mark.asyncio
async def test_tasks_revision_and_running_fences(app, record, monkeypatch):
    snapshot = packages.read_packages(record.project_id)
    with pytest.raises(ServerDomainError) as error:
        await packages.start_task(record.project_id, "sync", "stale")
    assert error.value.status_code == 409
    assert commands.project_operation_name(record.project_id) is None
    monkeypatch.setattr(state, "plugins", {"package_demo": {
        "project_id": record.project_id, "source_dir": str(record.source_dir), "source": "project",
    }})
    monkeypatch.setattr(state, "plugin_hosts", {"package_demo": SimpleNamespace(is_alive=lambda: True)})
    with pytest.raises(ServerDomainError) as error:
        await packages.start_task(record.project_id, "sync", snapshot["revision"])
    assert error.value.code == "PYTHON_PACKAGES_RUNNING"
    assert commands.project_operation_name(record.project_id) is None


@pytest.mark.asyncio
async def test_sync_uses_injected_tools_and_shared_transaction(record, monkeypatch):
    before = packages.read_packages(record.project_id)
    before = packages.save_requirements(record.project_id, ["httpx"], before["revision"])
    seen = []
    monkeypatch.setattr(packages, "source_repo_root", lambda: None)
    monkeypatch.setattr(packages, "prepare_package_tools", lambda *args: SimpleNamespace(
        python="managed-python", uv_executable="managed-uv", allow_pip_fallback=False, env={},
    ))

    def sync(directory, **kwargs):
        seen.append((directory, kwargs))
        return 0

    monkeypatch.setattr(packages, "sync_vendor", sync)
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    async def refresh(_self, _plugin_id):
        return {}
    monkeypatch.setattr(PluginRegistryService, "refresh_plugin", refresh)
    task = await packages.start_task(record.project_id, "sync", before["revision"])
    result = await finish(task)
    assert result["status"] == "succeeded", result
    assert seen[0][1]["python"] == "managed-python"
    assert seen[0][1]["project_root"] is None
    assert seen[0][1]["clean"] is True
    assert seen[0][1]["allow_pip_fallback"] is False
    assert result["result"]["changes"] == []


@pytest.mark.asyncio
async def test_busy_save_precheck_remove_and_runtime_fences(record, monkeypatch):
    from plugin.server.application import plugin_projects

    snapshot = packages.read_packages(record.project_id)
    commands.claim_project(record.project_id, "python-packages-sync")
    monkeypatch.setattr(state, "plugins", {"package_demo": {"project_id": record.project_id}})
    for operation in (
        lambda: packages.save_requirements(record.project_id, [], snapshot["revision"]),
        lambda: commands.require_plugin_project_available("package_demo"),
    ):
        with pytest.raises(ServerDomainError) as error:
            operation()
        assert error.value.code == "PLUGIN_OPERATION_BUSY"
    with pytest.raises(ServerDomainError) as error:
        await plugin_projects.remove_project(record.project_id)
    assert error.value.code == "PLUGIN_OPERATION_BUSY"
    with pytest.raises(ServerDomainError) as error:
        await plugin_projects.precheck_project(record.project_id)
    assert error.value.code == "PLUGIN_OPERATION_BUSY"


@pytest.mark.asyncio
async def test_cancel_holds_lease_until_worker_exits(record, monkeypatch):
    ready = threading.Event()
    exited = threading.Event()
    def execute(task):
        ready.set()
        task.cancel_event.wait(5)
        exited.set()
        packages._check_stop(task)
    monkeypatch.setattr(packages, "_execute", execute)
    snapshot = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "sync", snapshot["revision"])
    assert await asyncio.to_thread(ready.wait, 3)
    assert commands.project_operation_name(record.project_id) == "python-packages-sync"
    await packages.cancel_task(record.project_id, task["task_id"])
    result = await finish(task)
    assert result["status"] == "canceled"
    assert exited.is_set()
    assert commands.project_operation_name(record.project_id) is None


@pytest.mark.asyncio
async def test_shutdown_before_worker_runs_releases_lease(record):
    snapshot = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "check", snapshot["revision"])
    await packages.shutdown_tasks()
    assert packages.read_task(record.project_id, task["task_id"])["status"] == "canceled"
    assert commands.project_operation_name(record.project_id) is None


@pytest.mark.asyncio
async def test_mutation_guard_and_project_identity(app, record):
    app.dependency_overrides.pop(require_plugin_mutation_access)
    base = f"/plugin-projects/{record.project_id}/python-packages"
    async with client(app) as http:
        initial = (await http.get(base)).json()
        response = await http.put(base + "/requirements", json={
            "requirements": [], "revision": initial["revision"]
        }, headers={"Origin": "https://untrusted.invalid"})
        assert response.status_code == 403
        response = await http.get("/plugin-projects/missing/python-packages")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_task_identity_cannot_cross_projects(record, tmp_path):
    snapshot = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "check", snapshot["revision"])
    await finish(task)
    other = tmp_path / "another_demo"
    create_project(PluginSpec("another_demo", quick_start=True), other, repo_root=None)
    another = store.add_record(other)
    with pytest.raises(ServerDomainError) as error:
        packages.read_task(another.project_id, task["task_id"])
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_offline_wheel_install_through_task_and_cleanup(record, tmp_path, monkeypatch):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("offline wheel integration requires uv")
    wheel = tmp_path / "workbench_demo-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("workbench_demo/__init__.py", "VALUE = 'installed-from-wheel'\n")
        archive.writestr("workbench_demo-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: workbench-demo\nVersion: 1.0.0\n")
        archive.writestr("workbench_demo-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("workbench_demo-1.0.0.dist-info/RECORD", "")
    pyproject = record.source_dir / "pyproject.toml"
    pyproject.write_text(f'[project]\nname="package_demo"\nversion="0.1.0"\ndependencies=["workbench-demo @ {wheel.as_uri()}"]\n')
    monkeypatch.setattr(packages, "prepare_package_tools", lambda *args: SimpleNamespace(
        python=sys.executable, uv_executable=uv, allow_pip_fallback=False, env={"UV_OFFLINE": "1"},
    ))
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    async def refresh(_self, _plugin_id):
        return {}
    monkeypatch.setattr(PluginRegistryService, "refresh_plugin", refresh)
    before = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "sync", before["revision"])
    result = await finish(task)
    assert result["status"] == "succeeded", result
    assert result["result"]["changes"] == [{"name": "workbench-demo", "before": None, "after": "1.0.0"}]
    imported = await asyncio.to_thread(subprocess.run, [
        sys.executable, "-c", f"import sys; sys.path.insert(0, {str(record.source_dir / 'vendor')!r}); import workbench_demo; print(workbench_demo.VALUE)",
    ], capture_output=True, text=True, check=True)
    assert imported.stdout.strip() == "installed-from-wheel"
    # Removing the final dependency must clean vendor without preparing any tool.
    before = packages.save_requirements(record.project_id, [], packages.read_packages(record.project_id)["revision"])
    def no_tools(*args):
        raise AssertionError("An empty dependency sync should need no installer")
    monkeypatch.setattr(packages, "prepare_package_tools", no_tools)
    result = await finish(await packages.start_task(record.project_id, "sync", before["revision"]))
    assert result["status"] == "succeeded", result
    assert packages.read_packages(record.project_id)["installed"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
async def test_real_child_is_terminated_and_credentials_are_redacted(record, monkeypatch, stop):
    def execute(task):
        packages._command(task, [
            sys.executable, "-u", "-c",
            "import time; print('https://private:secret@index.example/simple', flush=True); time.sleep(60)",
        ], label="test installer")
        return {}
    monkeypatch.setattr(packages, "_execute", execute)
    before = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "sync", before["revision"])
    for _ in range(100):
        current = packages.read_task(record.project_id, task["task_id"])
        if any("index.example" in line for line in current["log"]):
            break
        await asyncio.sleep(0.01)
    else:
        await packages.shutdown_tasks()
        pytest.fail("Installer did not produce its first log line")
    process = packages._tasks[task["task_id"]].process
    assert process is not None
    if stop == "cancel":
        await packages.cancel_task(record.project_id, task["task_id"])
        result = await finish(task)
    else:
        await packages.shutdown_tasks()
        result = packages.read_task(record.project_id, task["task_id"])
    assert result["status"] == "canceled", result
    assert process.poll() is not None
    assert all("private:secret" not in line for line in result["log"])
    assert any("[redacted]@index.example" in line for line in result["log"])
    assert commands.project_operation_name(record.project_id) is None


@pytest.mark.asyncio
async def test_request_cancellation_during_preparation_releases_lease(record, monkeypatch):
    before = packages.read_packages(record.project_id)
    entered = threading.Event()
    unblock = threading.Event()
    original = packages.read_packages
    def blocked(project_id):
        entered.set()
        assert unblock.wait(5)
        return original(project_id)
    monkeypatch.setattr(packages, "read_packages", blocked)
    request = asyncio.create_task(packages.start_task(record.project_id, "check", before["revision"]))
    assert await asyncio.to_thread(entered.wait, 3)
    request.cancel()
    unblock.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert commands.project_operation_name(record.project_id) is None
    assert packages._workers == {}


@pytest.mark.asyncio
async def test_cancel_after_vendor_commit_still_refreshes_actual_state(record, monkeypatch):
    before = packages.read_packages(record.project_id)
    before = packages.save_requirements(record.project_id, ["commit-demo"], before["revision"])
    monkeypatch.setattr(packages, "prepare_package_tools", lambda *args: SimpleNamespace(
        python=sys.executable, uv_executable="uv", allow_pip_fallback=False, env={},
    ))
    def sync(directory, **kwargs):
        metadata = directory / "vendor" / "commit_demo-1.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: commit-demo\nVersion: 1.0\n")
        packages._tasks[packages._latest[record.project_id]].cancel_event.set()
        return 0
    monkeypatch.setattr(packages, "sync_vendor", sync)
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    refreshed = []
    async def refresh(_self, plugin_id):
        refreshed.append(plugin_id)
        return {}
    monkeypatch.setattr(PluginRegistryService, "refresh_plugin", refresh)
    task = await packages.start_task(record.project_id, "sync", before["revision"])
    result = await finish(task)
    assert result["status"] == "canceled"
    assert result["result"]["changes"] == [{"name": "commit-demo", "before": None, "after": "1.0"}]
    assert refreshed == ["package_demo"]
    assert commands.project_operation_name(record.project_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_task_queued_for_host_process_creation_retires_without_spawning(record, monkeypatch, stop):
    from utils import process_creation

    creation_lock = threading.Lock()
    creation_lock.acquire()
    monkeypatch.setattr(process_creation, "_creation_lock", creation_lock)
    entered = threading.Event()
    attempted = []
    original_start = packages.start_cli

    def start(*args, **kwargs):
        entered.set()
        return original_start(*args, **kwargs)

    def forbidden_spawn(*args, **kwargs):
        attempted.append(args)
        raise AssertionError("A retired creation request must not allocate a process")

    def execute(task):
        packages._command(task, [sys.executable, "-c", "pass"], label="Queued installer")
        return {}

    monkeypatch.setattr(packages, "start_cli", start)
    monkeypatch.setattr(packages, "_execute", execute)
    monkeypatch.setattr(process_creation.subprocess, "Popen", forbidden_spawn)
    if stop == "deadline":
        monkeypatch.setattr(packages, "env_seconds", lambda *args: 0.2)
    before = packages.read_packages(record.project_id)
    task = await packages.start_task(record.project_id, "sync", before["revision"])
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if stop == "cancel":
            await packages.cancel_task(record.project_id, task["task_id"])
        # The worker must retire while the host's creation lock is still held.
        result = await asyncio.wait_for(finish(task), timeout=3)
        assert result["status"] == ("canceled" if stop == "cancel" else "failed"), result
        if stop == "deadline":
            assert "time budget" in result["message"]
        assert attempted == []
        assert commands.project_operation_name(record.project_id) is None
    finally:
        creation_lock.release()
        await packages.shutdown_tasks()


@pytest.mark.asyncio
async def test_cancel_during_os_spawn_reaps_process_and_closes_output(record, monkeypatch):
    original_start = packages.start_cli
    processes = []
    def start(*args, **kwargs):
        process = original_start(*args, **kwargs)
        processes.append(process)
        kwargs["cancel_event"].set()
        return process
    def execute(task):
        packages._command(task, [sys.executable, "-c", "import time; time.sleep(60)"], label="Installer")
        return {}
    monkeypatch.setattr(packages, "start_cli", start)
    monkeypatch.setattr(packages, "_execute", execute)
    before = packages.read_packages(record.project_id)
    result = await finish(await packages.start_task(record.project_id, "sync", before["revision"]))
    assert result["status"] == "canceled"
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed
    assert packages._tasks[result["task_id"]].process is None
    assert commands.project_operation_name(record.project_id) is None
