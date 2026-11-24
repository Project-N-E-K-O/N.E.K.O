"""Regression coverage for unresolved model-workshop PR review feedback."""

import json
import shutil
from contextlib import contextmanager
from dataclasses import replace

import pytest

from services.model_workshop import registry as registry_module, runtime
from services.model_workshop.bundle import BundleLimits
from services.model_workshop.formats import validate_model_bundle
from services.model_workshop.paths import WorkshopError
from tests.unit.test_model_workshop_install import (
    bundle_path, config as config, service as service, install,
)
from tests.unit.test_model_workshop_runtime import owned as owned
from tests.unit.test_model_workshop_legacy import legacy as legacy
from tests.unit import test_model_workshop_market as launch_fixtures

launch_config = launch_fixtures.config


def test_registry_read_uses_the_admitted_compact_manifest_size(tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    bundle = validate_model_bundle(path)
    record = registry_module.make_record(bundle, "a" * 32)
    manifest = {key: record[key] for key in ("model_format", "entrypoint", "display_name", "attribution", "license")}
    manifest.update(schema="neko.model.package.v1", files=[
        {key: item[key] for key in ("path", "byte_size", "sha256")} for item in record["files"]])
    size = len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode())
    monkeypatch.setattr(registry_module, "BundleLimits", lambda: replace(BundleLimits(), manifest_bytes=size))
    assert registry_module.validate_record(record) == record


@pytest.mark.parametrize("phase", ["queued", "validating"])
def test_maintenance_failure_always_terminates_and_releases_admission(service, tmp_path, monkeypatch, phase):
    from utils import cloudsave_runtime
    blocked = False

    @contextmanager
    def fence(*args, **kwargs):
        if blocked:
            raise cloudsave_runtime.MaintenanceModeError("maintenance")
        yield

    original_phase = service.installer.phase

    def entering(journal, value):
        nonlocal blocked
        if value == phase:
            blocked = True
        return original_phase(journal, value)

    monkeypatch.setattr(cloudsave_runtime, "cloudsave_writable_transaction", fence)
    monkeypatch.setattr(service.installer, "phase", entering)
    path, _ = bundle_path(tmp_path)
    with path.open("rb") as source:
        try:
            attempt = service.receive(source)
        except WorkshopError:
            attempt = next(iter(service._tasks))
    task = service._tasks[attempt]
    assert task.done.wait(2)
    assert task.phase == "failed"
    assert task.error
    blocked = False
    monkeypatch.setattr(service.installer, "phase", original_phase)
    assert install(service, path)["phase"] == "completed"


def test_completed_residual_journal_allows_delete_and_reinstall(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    original = service.installer.cleanup_private_stage
    monkeypatch.setattr(service.installer, "cleanup_private_stage", lambda *_: (_ for _ in ()).throw(OSError("busy")))
    first = install(service, path)
    assert first["phase"] == "completed"
    shutil.rmtree(service.paths.root / first["result"]["target"])
    monkeypatch.setattr(service.installer, "cleanup_private_stage", original)
    second = install(service, path)
    assert second["phase"] == "completed"
    assert second["result"]["install_id"] != first["result"]["install_id"]


def test_readonly_journal_scan_retries_concurrent_replacement(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    install(service, path)
    registry = service.installer.registry
    original, calls = registry.journals, 0

    def changed():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkshopError("model_workshop_state_unreadable")
        return original()

    monkeypatch.setattr(registry, "journals", changed)
    assert registry.unavailable() == set()
    assert calls == 2


@pytest.mark.parametrize("prefix,tail", [
    ("/static/vrm/%2e%2e/%2e%2e/user_vrm/", "pending.vrm"),
    ("/static/%2e%2e/user_live2d_local/", "Pending/Avatar.model3.json"),
    ("/workshop/123/%2e%2e/%2e%2e/user_mmd/", "pending.pmx"),
])
def test_other_allowed_prefixes_cannot_hide_encoded_local_traversal(owned, prefix, tail):
    with pytest.raises(WorkshopError, match="unsafe_path"):
        runtime.require_url(owned.config, prefix + tail, for_binding=True)


@pytest.mark.parametrize("name", ["100%FF", "100%20", "100#model", "100?model"])
def test_raw_catalog_paths_keep_literal_filesystem_names(owned, monkeypatch, name):
    expected = owned.paths.live2d / name / "Avatar.model3.json"
    seen = []
    monkeypatch.setattr(runtime.Snapshot, "require", lambda self, path: seen.append(path))
    item = {"name": name, "path": "/user_live2d/" + name + "/Avatar.model3.json"}
    assert runtime.filter_catalog(owned.config, [item], url_encoded=False) == [item]
    assert seen == [expected]


@pytest.mark.parametrize("recover", [False, True])
def test_full_resource_hash_never_holds_the_shared_storage_fence(service, tmp_path, monkeypatch, recover):
    path, _ = bundle_path(tmp_path)
    original = service.installer.verify_resources
    seen = []

    def verify(*args):
        enters = sum(event == "enter" for event, _ in service.config.transactions)
        exits = sum(event == "exit" for event, _ in service.config.transactions)
        seen.append(enters - exits)
        return original(*args)

    monkeypatch.setattr(service.installer, "verify_resources", verify)
    if recover:
        from tests.unit.test_model_workshop_install import fail_registration
        save = fail_registration(service, monkeypatch)
        first = install(service, path)
        assert first["phase"] == "failed"
        monkeypatch.setattr(service.installer.registry, "save", save)
        assert service.installer.recover()[first["task_id"]] == "completed"
    else:
        assert install(service, path)["phase"] == "completed"
    assert seen and set(seen) == {0}


def test_resource_change_after_hash_cannot_be_registered(service, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    original = service.installer.verify_resources

    def changed(record, target):
        versions = original(record, target)
        file = service.paths.root / record["local_entrypoint"]
        file.write_bytes(file.read_bytes() + b"changed after verification")
        return versions

    monkeypatch.setattr(service.installer, "verify_resources", changed)
    assert install(service, path)["error"] == "model_workshop_recovery_conflict"
    assert service.installer.registry.load() == {}


@pytest.mark.parametrize("name", ["100%FF", "100%20"])
def test_literal_percent_names_do_not_break_legacy_model_catalogs(legacy, name):
    from services.model_workshop.catalog import find_models
    client, config, _service = legacy
    live = config.live2d_dir / name
    live.mkdir()
    (live / "Avatar.model3.json").write_text("{}")
    mmd = config.mmd_dir / name
    mmd.mkdir()
    (mmd / "Avatar.pmx").write_bytes(b"fixture")
    normal = config.mmd_dir / "Ordinary"
    normal.mkdir()
    (normal / "Ordinary.pmx").write_bytes(b"fixture")
    response = client.get("/api/model/mmd/models")
    assert response.status_code == 200
    assert {item["name"] for item in response.json()["models"]} >= {"Avatar", "Ordinary"}
    assert name in {item["name"] for item in find_models()}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["drain", "close"])
async def test_anyio_cancelled_native_waits_do_not_spin(monkeypatch, operation):
    import asyncio
    import threading
    import time
    import anyio
    from services.model_workshop.tasks import _drain_sync
    from services.model_workshop.download import ConnectedDownloads

    started, finished = asyncio.Event(), threading.Event()
    loop, original = asyncio.get_running_loop(), asyncio.shield
    calls = 0

    def shield(future):
        nonlocal calls
        calls += 1
        return original(future)

    def work():
        loop.call_soon_threadsafe(started.set)
        time.sleep(0.03)
        finished.set()

    async def job():
        started.set()
        await asyncio.sleep(0.03)
        finished.set()

    async def drain():
        await _drain_sync(work)

    monkeypatch.setattr(asyncio, "shield", shield)
    if operation == "close":
        driver = ConnectedDownloads()
        driver.jobs[("r", "o")] = asyncio.create_task(job())
        wait = driver.close
    else:
        wait = drain
    async with anyio.create_task_group() as group:
        group.start_soon(wait)
        await started.wait()
        group.cancel_scope.cancel()
    assert finished.is_set()
    assert calls <= 2


def test_expired_installed_connections_release_launch_capacity(launch_config, monkeypatch):
    from services.model_workshop import market
    from tests.fake_clock import patch_module_clock
    launches = market.Launches()
    rows = [launches.create(launch_config) for _ in range(market.MAX_LAUNCHES)]
    for row in rows:
        row.phase = "installed"
    patch_module_clock(monkeypatch, market, monotonic=lambda: rows[0].expires + 1)
    fresh = launches.create(launch_config)
    assert list(launches.items) == [fresh.request_id]


@pytest.mark.parametrize("running", [False, True])
def test_expired_installed_connection_only_blocks_while_its_worker_runs(launch_config, monkeypatch, running):
    from services.model_workshop import market
    from tests.fake_clock import patch_module_clock
    now = [0]
    patch_module_clock(monkeypatch, market, monotonic=lambda: now[0])
    launches = market.Launches()
    first = launches.create(launch_config)
    launches.select(launch_config, first.request_id, 1, "a" * 64, 1, "1" * 32)
    first.phase, first.connection.running = "installed", running
    now[0] = market.TTL - 1
    second = launches.create(launch_config)
    now[0] += 2
    if running:
        with pytest.raises(WorkshopError, match="busy"):
            launches.select(launch_config, second.request_id, 2, "b" * 64, 1, "2" * 32)
    else:
        assert launches.select(launch_config, second.request_id, 2, "b" * 64, 1, "2" * 32)[1]


@pytest.mark.parametrize("end", ["chunk", "eof"])
def test_download_deadline_is_rechecked_after_a_blocking_read(monkeypatch, end):
    import hashlib
    import threading
    from services.model_workshop import market
    from tests.fake_clock import patch_module_clock
    now = [0]
    patch_module_clock(monkeypatch, market, monotonic=lambda: now[0])

    def chunks():
        if end == "eof":
            yield b"a"
        now[0] = 601
        if end == "chunk":
            yield b"a"

    stream = market.PackageStream(chunks(), hashlib.sha256(b"a").hexdigest(), 1, threading.Event())
    if end == "eof":
        assert stream.read(1024) == b"a"
    with pytest.raises(WorkshopError, match="timeout"):
        stream.read(1024)


@pytest.mark.parametrize("end", ["chunk", "eof"])
def test_download_cancel_during_read_is_checked_before_install_eof(end):
    import hashlib
    import threading
    from services.model_workshop import market
    cancelled = threading.Event()

    def chunks():
        if end == "eof":
            yield b"a"
        cancelled.set()
        if end == "chunk":
            yield b"a"

    stream = market.PackageStream(chunks(), hashlib.sha256(b"a").hexdigest(), 1, cancelled)
    if end == "eof":
        assert stream.read(1024) == b"a"
    with pytest.raises(WorkshopError, match="cancelled"):
        stream.read(1024)


@pytest.mark.parametrize("change", ["removed_journal", "replaced_journal", "registry_retry"])
def test_transient_metadata_changes_do_not_poison_a_stable_mutation_snapshot(owned, tmp_path, monkeypatch, change):
    path, _ = bundle_path(tmp_path)
    cleanup = owned.installer.cleanup_private_stage

    def leave_receipt(_journal):
        raise OSError("temporary cleanup failure")

    monkeypatch.setattr(owned.installer, "cleanup_private_stage", leave_receipt)
    result = install(owned, path)
    assert result["phase"] == "completed"
    log = owned.installer.journal_path(result["task_id"])
    changed = False
    module = registry_module if change == "registry_retry" else runtime
    original = module.read_json
    target = owned.paths.private / "installed.json" if change == "registry_retry" else log

    def reading(candidate, *args):
        nonlocal changed
        if candidate == target and not changed:
            changed = True
            if change == "removed_journal":
                data = original(log, registry_module.STATE_BYTES)
                with owned.installer.mutation():
                    cleanup(data)
            else:
                raise WorkshopError("model_workshop_state_unreadable")
        return original(candidate, *args)

    monkeypatch.setattr(module, "read_json", reading)
    state = runtime.snapshot_for_mutation(owned.config)
    assert changed and not state.errors
    assert state.record_for(owned.paths.root / result["result"]["local_entrypoint"]) is not None


def test_task_finishing_during_recovery_scan_is_retried_on_the_next_maintenance(owned, tmp_path, monkeypatch):
    import threading
    path, _ = bundle_path(tmp_path, "vrm")
    entered, release = threading.Event(), threading.Event()
    save = owned.installer.registry.save

    def failed_save(rows, attempts=None):
        if rows:
            entered.set()
            assert release.wait(5)
            raise OSError("registration interrupted")
        return save(rows, attempts)

    monkeypatch.setattr(owned.installer.registry, "save", failed_save)
    with path.open("rb") as source:
        attempt = owned.receive(source)
    assert entered.wait(5)
    recover = owned.installer.recover

    def scan_then_finish(active):
        assert attempt in active
        result = recover(active)
        release.set()
        assert owned.wait(attempt, 5)["phase"] == "failed"
        monkeypatch.setattr(owned.installer.registry, "save", save)
        return result

    monkeypatch.setattr(owned.installer, "recover", scan_then_finish)
    try:
        runtime.maintain(owned.config)
        monkeypatch.setattr(owned.installer, "recover", recover)
        assert runtime.maintain(owned.config)[attempt] == "completed"
        assert owned.installer.registry.installed()[0]["status"] == "installed"
    finally:
        release.set()


def test_persistent_bad_journal_still_blocks_mutation_with_bounded_retries(owned, tmp_path, monkeypatch):
    path, _ = bundle_path(tmp_path)
    result = install(owned, path)
    corrupt = owned.paths.private / "transactions" / ("e" * 32 + ".json")
    corrupt.write_text("{")
    calls = 0
    original = runtime.read_json

    def reading(candidate, *args):
        nonlocal calls
        if candidate == corrupt:
            calls += 1
        return original(candidate, *args)

    monkeypatch.setattr(runtime, "read_json", reading)
    state = runtime.snapshot(owned.config)
    assert state.errors and 0 < calls <= 3
    assert state.record_for(owned.paths.root / result["result"]["local_entrypoint"]) is not None
    # Existing ordinary reads remain tolerant, while new writes stay closed.
    state.require(owned.paths.live2d / "ordinary" / "Avatar.model3.json")
    with pytest.raises(WorkshopError, match="unavailable"):
        runtime.snapshot_for_mutation(owned.config)
    assert calls <= 6

