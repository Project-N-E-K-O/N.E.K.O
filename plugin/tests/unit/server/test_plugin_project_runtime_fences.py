"""Exercise authoring leases through the public lifecycle entry points."""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import subprocess
import sys

import pytest

from plugin import settings
from plugin.core.state import state
from plugin.neko_plugin_cli.core.project import create_project
from plugin.neko_plugin_cli.templates.generator import PluginSpec
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_project_python_packages as packages
from plugin.server.application.plugins import lifecycle_service as lifecycle
from plugin.server.application.plugins import operation_lock
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import authoring_project_store as store
from utils import process_creation


@pytest.fixture
def project(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: tmp_path / "state" / "plugins")
    monkeypatch.setattr(operation_lock, "_operation_file_lock_path", lambda: tmp_path / "operation.lock")
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(state, "plugin_hosts", {})
    for project_id in list(commands._busy):
        commands.release_project(project_id)
    directory = tmp_path / "runtime_demo"
    create_project(PluginSpec("runtime_demo", quick_start=True), directory, repo_root=None)
    record = store.add_record(directory)
    yield record
    for project_id in list(commands._busy):
        commands.release_project(project_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start_plugin", "reload_plugin"])
@pytest.mark.parametrize("registered", [False, True])
async def test_busy_project_rejected_before_refresh_or_preflight(project, monkeypatch, operation, registered):
    if registered:
        state.plugins["runtime_demo"] = {"project_id": project.project_id}
    commands.claim_project(project.project_id, "python-packages-sync")

    async def unexpected(*args, **kwargs):
        pytest.fail("busy lifecycle operation reached registry refresh or preflight")

    monkeypatch.setattr(lifecycle.plugin_registry_service, "refresh_plugin", unexpected)
    monkeypatch.setattr(lifecycle, "_preflight_project_source", unexpected)
    with pytest.raises(ServerDomainError) as error:
        await getattr(lifecycle.PluginLifecycleService(), operation)("runtime_demo")
    assert error.value.code == "PLUGIN_OPERATION_BUSY"


@pytest.mark.asyncio
async def test_start_rechecks_project_after_refresh_changes_identity(project, monkeypatch):
    commands.claim_project(project.project_id, "python-packages-sync")

    async def refresh(plugin_id):
        assert plugin_id == "old_identity"
        state.plugins["runtime_demo"] = {"project_id": project.project_id}
        return {"plugin_id": "runtime_demo"}

    monkeypatch.setattr(lifecycle.plugin_registry_service, "refresh_plugin", refresh)
    with pytest.raises(ServerDomainError) as error:
        await lifecycle.PluginLifecycleService().start_plugin("old_identity")
    assert error.value.code == "PLUGIN_OPERATION_BUSY"


def test_busy_project_resolved_from_source_directory_before_project_discovery(project):
    state.plugins["old_identity"] = {"source_dir": str(project.source_dir)}
    commands.claim_project(project.project_id, "python-packages-sync")
    with pytest.raises(ServerDomainError) as error:
        commands.require_plugin_project_available("old_identity")
    assert error.value.code == "PLUGIN_OPERATION_BUSY"


@pytest.mark.asyncio
async def test_dependency_check_cannot_reserve_between_reload_stop_and_start(project, monkeypatch):
    state.plugins["runtime_demo"] = {"project_id": project.project_id}
    before = packages.read_packages(project.project_id)
    attempted = asyncio.Event()
    prepare = packages._prepare_task
    requests = []
    sequence = []

    async def tracked_prepare(*args, **kwargs):
        attempted.set()
        return await prepare(*args, **kwargs)

    async def preflight(plugin_id):
        requests.append(asyncio.create_task(packages.start_task(
            project.project_id, "check", before["revision"],
        )))
        await asyncio.wait_for(attempted.wait(), 3)

    async def stop(plugin_id):
        sequence.append("stop")

    async def start(plugin_id, **kwargs):
        commands.require_plugin_project_available(plugin_id)
        sequence.append("start")
        return {"success": True}

    monkeypatch.setattr(packages, "_prepare_task", tracked_prepare)
    monkeypatch.setattr(packages, "_execute", lambda task: {})
    monkeypatch.setattr(lifecycle, "_preflight_project_source", preflight)
    monkeypatch.setattr(lifecycle, "_plugin_is_running_sync", lambda plugin_id: True)
    service = lifecycle.PluginLifecycleService()
    monkeypatch.setattr(service, "stop_plugin", stop)
    monkeypatch.setattr(service, "start_plugin", start)
    try:
        response = await service.reload_plugin("runtime_demo")
        assert response["success"] is True
        assert sequence == ["stop", "start"]
    finally:
        for request in requests:
            task = await request
            worker = packages._workers.get(task["task_id"])
            if worker is not None:
                await worker
    assert commands.project_operation_name(project.project_id) is None


def test_reading_operation_does_not_create_lease_files(project):
    path = commands._project_lease_path(project.project_id)
    assert commands.project_operation_name(project.project_id) is None
    assert not path.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminate", [False, True])
async def test_project_lease_fences_another_server_and_releases_on_exit(project, terminate):
    script = """
import sys
from pathlib import Path
from plugin import settings
from plugin.server.application import plugin_project_commands as commands
settings.get_plugin_state_root = lambda: Path(sys.argv[1])
commands.claim_project(sys.argv[2], 'python-packages-sync')
print('ready', flush=True)
sys.stdin.read(1)
commands.release_project(sys.argv[2])
"""
    child = process_creation.popen(
        [sys.executable, "-c", script, str(settings.get_plugin_state_root()), project.project_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 10) == "ready\n"
        assert commands.project_operation_name(project.project_id) is not None
        with pytest.raises(ServerDomainError) as error:
            commands.claim_project(project.project_id, "build")
        assert error.value.code == "PLUGIN_OPERATION_BUSY"
        with pytest.raises(ServerDomainError) as error:
            await lifecycle.PluginLifecycleService().start_plugin("runtime_demo")
        assert error.value.code == "PLUGIN_OPERATION_BUSY"
        if terminate:
            child.terminate()
            await asyncio.to_thread(child.communicate, timeout=10)
        else:
            await asyncio.to_thread(child.communicate, "x", timeout=10)
            assert child.returncode == 0
        assert commands.project_operation_name(project.project_id) is None
        commands.claim_project(project.project_id, "build")
        commands.release_project(project.project_id)
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.communicate, timeout=10)


def _inspect_forked_lease(connection, project_id):
    connection.send((bool(commands._project_leases), bool(commands._busy),
                     commands.project_operation_name(project_id)))
    connection.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Windows does not inherit forked file handles")
def test_forked_plugin_drops_lease_handles_without_unlocking_parent(project):
    commands.claim_project(project.project_id, "python-packages-sync")
    context = multiprocessing.get_context("fork")
    received, sent = context.Pipe(duplex=False)
    process = context.Process(target=_inspect_forked_lease, args=(sent, project.project_id))
    try:
        process_creation.start(process)
        sent.close()
        assert received.poll(5)
        inherited_handles, inherited_busy, parent_operation = received.recv()
        assert not inherited_handles
        assert not inherited_busy
        assert parent_operation is not None
        process.join(5)
        assert process.exitcode == 0
    finally:
        received.close()
        sent.close()
        if process.is_alive():
            process.terminate()
            process.join(5)
