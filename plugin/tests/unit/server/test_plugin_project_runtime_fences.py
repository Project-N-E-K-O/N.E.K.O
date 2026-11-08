"""Exercise authoring leases through the public lifecycle entry points."""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import CancelledError as BuildCancelled
from types import SimpleNamespace

import pytest

from plugin import settings
from plugin.core.state import state
from plugin.neko_plugin_cli.core.project import create_project
from plugin.neko_plugin_cli.templates.generator import PluginSpec
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application import plugin_project_python_packages as packages
from plugin.server.application import plugin_projects as projects
from plugin.server.application import plugin_publish_tasks as publish
from plugin.server.application import plugin_repo_tasks as repositories
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.plugin_cli.source_resolver import ResolvedPluginSource
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
@pytest.mark.parametrize("operation", ["precheck", "create-repo", "remove", "save", "build", "publish"])
async def test_authoring_reservations_wait_for_lifecycle_lock(project, monkeypatch, operation):
    """Every lease entry waits before claiming, including the CLI build worker."""
    entered = asyncio.Event()

    async def run_cli(*args, **kwargs):
        assert not operation_lock._operation_lock_is_held_by_current_task()
        return subprocess.CompletedProcess([], 0, '{"passed": true}', "")

    async def create_repository(*args, **kwargs):
        assert not operation_lock._operation_lock_is_held_by_current_task()
        return {"status": "created"}

    async def publish_worker(task, *args):
        assert not operation_lock._operation_lock_is_held_by_current_task()
        task.status = "succeeded"
        commands.release_project(task.project_id)

    monkeypatch.setattr(projects, "source_repo_root", lambda: project.source_dir.parent)
    monkeypatch.setattr(projects, "run_cli", run_cli)
    monkeypatch.setattr(repositories, "_run", create_repository)
    monkeypatch.setattr(publish, "_run_task", publish_worker)
    builder = PluginCliService()
    monkeypatch.setattr(builder, "_resolve_plugin_sources", lambda **kwargs: ([SimpleNamespace(
        root_id="project", plugin_dir=project.source_dir,
    )], []))

    def build_worker(*, prepared, **kwargs):
        assert commands.project_operation_name(project.project_id) == "build"
        for project_id in prepared.claimed_projects:
            commands.release_project(project_id)
        return {"ok": True}

    monkeypatch.setattr(builder, "_build_sync", build_worker)
    before = packages.read_packages(project.project_id)

    async def invoke():
        entered.set()
        if operation == "precheck":
            return await projects.precheck_project(project.project_id)
        if operation == "create-repo":
            return await repositories.create_repository(project.project_id)
        if operation == "remove":
            return await projects.remove_project(project.project_id)
        if operation == "save":
            return await packages.save_project_requirements(project.project_id, [], before["revision"])
        if operation == "build":
            return await builder.build(mode="all")
        task = publish._Task("runtime-fence-publish", project.project_id, "github")
        gate = publish._Gate(project.source_dir.parent, project.source_dir, "runtime_demo", "owner/repo", None, "v1")
        return await publish._queue_publish(task, [], gate, 30)

    request = None
    try:
        async with operation_lock.plugin_operation_lock.hold():
            request = asyncio.create_task(invoke())
            await asyncio.wait_for(entered.wait(), 3)
            # Give the attempted request time to reach the held lifecycle lock.
            await asyncio.sleep(0.05)
            assert commands.project_operation_name(project.project_id) is None
            assert not request.done()
        await asyncio.wait_for(request, 5)
        workers = list(publish._workers.values())
        if workers:
            await asyncio.gather(*workers)
        assert commands.project_operation_name(project.project_id) is None
    finally:
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        publish._tasks.clear()
        publish._active.clear()
        publish._latest.clear()


@pytest.mark.asyncio
async def test_canceling_queued_reservation_does_not_release_another_owner(project):
    async with operation_lock.plugin_operation_lock.hold():
        request = asyncio.create_task(commands.acquire_project(project.project_id, "precheck"))
        await asyncio.sleep(0.05)
        commands.claim_project(project.project_id, "existing-operation")
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert commands.project_operation_name(project.project_id) == "existing-operation"


def test_running_host_matches_source_alias_and_ignores_missing_old_path(project, tmp_path, monkeypatch):
    alias = tmp_path / "source-alias"
    try:
        alias.symlink_to(project.source_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")
    monkeypatch.setattr(state, "plugins", {"old_identity": {"source_dir": str(alias)}})
    monkeypatch.setattr(state, "plugin_hosts", {"old_identity": SimpleNamespace(is_alive=lambda: True)})
    assert packages._running(project.source_dir) is True
    commands.claim_project(project.project_id, "python-packages-sync")
    with pytest.raises(ServerDomainError) as error:
        commands.require_plugin_project_available("old_identity")
    assert error.value.code == "PLUGIN_OPERATION_BUSY"
    alias.unlink()
    # No directory identity survives in an invalid pathname. Do not pretend to
    # recover a moved host from its string, or count an unrelated live host.
    state.plugins["unrelated"] = {"source_dir": str(tmp_path)}
    state.plugin_hosts["unrelated"] = SimpleNamespace(is_alive=lambda: True)
    assert packages._running(project.source_dir) is False


@pytest.mark.asyncio
async def test_cancelled_build_stops_before_remaining_projects_and_releases_leases(project, tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.core import build

    second_dir = tmp_path / "runtime_second"
    create_project(PluginSpec("runtime_second", quick_start=True), second_dir, repo_root=None)
    second = store.add_record(second_dir)
    sources = [ResolvedPluginSource("project", item.source_dir.name, item.source_dir.name, item.source_dir)
               for item in (project, second)]
    entered = threading.Event()
    finish = threading.Event()
    signals = []
    built = []

    class Builder:
        def __init__(self, **kwargs):
            pass

        def build_plugin(self, directory, *args, **kwargs):
            built.append(directory)
            if len(built) == 1:
                entered.set()
                assert finish.wait(5)
            return SimpleNamespace(model_dump=lambda **kwargs: {})

    service = PluginCliService()
    original = service._build_sync

    def worker(**kwargs):
        signals.append(kwargs["cancelled"])
        return original(**kwargs)

    monkeypatch.setattr(service, "_path_policy", lambda: SimpleNamespace(package_artifacts_root=tmp_path / "artifacts"))
    monkeypatch.setattr(service, "_resolve_plugin_sources", lambda **kwargs: (sources, []))
    monkeypatch.setattr(service, "_build_sync", worker)
    monkeypatch.setattr(build, "PluginBuilder", Builder)
    request = asyncio.create_task(service.build(mode="all"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        request.cancel()
        assert await asyncio.to_thread(signals[0].wait, 3)
        assert commands.project_operation_name(project.project_id) == "build"
        assert commands.project_operation_name(second.project_id) == "build"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert built == [project.source_dir]
        assert commands.project_operation_name(project.project_id) is None
        assert commands.project_operation_name(second.project_id) is None
    finally:
        finish.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("bundle", [False, True])
@pytest.mark.parametrize("keep_staging", [False, True])
def test_builder_cancellation_preserves_staging_policy_and_stops_next_stage(
    project, tmp_path, monkeypatch, bundle, keep_staging,
):
    from plugin.neko_plugin_cli.core import build

    canceled = threading.Event()
    builder = build.PluginBuilder(cancelled=canceled.is_set)
    paths = []
    copied = []
    probed = []
    original_create = build.BuildPaths.create
    original_copy = builder.copy_plugin_runtime_files

    def create(cls, **kwargs):
        result = original_create(**kwargs)
        paths.append(result.staging_root)
        return result

    def copy(source_dir, *args, **kwargs):
        result = original_copy(source_dir, *args, **kwargs)
        copied.append(source_dir)
        canceled.set()
        return result

    monkeypatch.setattr(build.BuildPaths, "create", classmethod(create))
    monkeypatch.setattr(builder, "copy_plugin_runtime_files", copy)
    monkeypatch.setattr(build, "write_packaged_metadata", lambda **kwargs: probed.append(kwargs))
    artifact = tmp_path / "canceled.neko-plugin"
    with pytest.raises(BuildCancelled):
        if bundle:
            second_dir = tmp_path / "runtime_second"
            create_project(PluginSpec("runtime_second", quick_start=True), second_dir, repo_root=None)
            builder.build_bundle([project.source_dir, second_dir], artifact, keep_staging=keep_staging)
        else:
            builder.build_plugin(project.source_dir, artifact, keep_staging=keep_staging)
    assert copied == [project.source_dir]
    assert probed == []
    try:
        assert len(paths) == 1 and paths[0].exists() == keep_staging
        assert not artifact.exists()
    finally:
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create-repo", "precheck", "publish"])
async def test_removed_project_is_rechecked_after_waiting_for_lease(project, monkeypatch, operation):
    entered = asyncio.Event()
    runs = []

    async def run_cli(*args, **kwargs):
        runs.append(operation)
        return subprocess.CompletedProcess([], 0, '{"passed": true}', "")

    async def repository_worker(*args, **kwargs):
        runs.append(operation)
        return {"status": "created"}

    async def publish_worker(task, *args):
        runs.append(operation)
        task.status = "succeeded"
        commands.release_project(task.project_id)

    monkeypatch.setattr(projects, "source_repo_root", lambda: project.source_dir.parent)
    monkeypatch.setattr(projects, "run_cli", run_cli)
    monkeypatch.setattr(repositories, "_run", repository_worker)
    monkeypatch.setattr(publish, "_run_task", publish_worker)

    async def invoke():
        entered.set()
        if operation == "create-repo":
            return await repositories.create_repository(project.project_id)
        if operation == "precheck":
            return await projects.precheck_project(project.project_id)
        task = publish._Task("removed-project-publish", project.project_id, "github")
        gate = publish._Gate(project.source_dir.parent, project.source_dir, "runtime_demo", "owner/repo", None, "v1")
        return await publish._queue_publish(task, [], gate, 30)

    request = None
    try:
        async with operation_lock.plugin_operation_lock.hold():
            request = asyncio.create_task(invoke())
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.sleep(0.05)
            store.remove_record(project.project_id)
        with pytest.raises(ServerDomainError) as error:
            await request
        assert error.value.code == "PROJECT_NOT_FOUND"
        assert runs == []
        assert commands.project_operation_name(project.project_id) is None
    finally:
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        if publish._workers:
            await asyncio.gather(*publish._workers.values())
        publish._tasks.clear()
        publish._active.clear()
        publish._latest.clear()


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
