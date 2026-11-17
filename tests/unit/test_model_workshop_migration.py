"""Main migration coordination with real storage fences and isolated model data."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from main_routers import storage_location_router as storage_router
from services.model_workshop import runtime
from services.model_workshop.paths import WorkshopError
from tests.unit.test_model_workshop_install import bundle_path, install
from tests.unit.test_storage_location_router import _build_client, _make_real_config_manager
from utils import storage_location_bootstrap
from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY
from utils.root_state_lock import root_state_transaction
from utils.storage_migration import get_storage_migration_path
from utils.storage_policy import save_storage_policy


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """Use actual ConfigManager gates; all roots and state files stay in tmp_path."""
    monkeypatch.setattr(storage_location_bootstrap, "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION", False)
    manager = _make_real_config_manager(tmp_path)
    manager.app_docs_dir.mkdir(parents=True, exist_ok=True)
    save_storage_policy(manager, selected_root=manager.app_docs_dir,
                        selection_source="current", anchor_root=manager.anchor_root)
    monkeypatch.setattr(runtime, "_services", {})
    monkeypatch.setattr(runtime, "_stopped", False)
    service = runtime.get_service(manager)
    try:
        yield service
    finally:
        # Rollback may have allowed a fresh idle owner; drain both instances.
        for value in {service, *runtime._services.values()}:
            value.close()


def selection(tmp_path):
    return {"selected_root": str(tmp_path / "target"), "selection_source": "custom"}


def state_bytes(root):
    """Windows locks deny reading writer.lock; compare its identity without opening it."""
    result = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == "model_workshop/locks/writer.lock":
            info = path.lstat()
            result[relative] = (info.st_dev, info.st_ino, info.st_size)
        else:
            result[relative] = path.read_bytes()
    return result


@pytest.mark.parametrize("route", ["preflight", "select"])
def test_readonly_preflight_keeps_idle_owner_and_every_state_byte(owner, tmp_path, monkeypatch, route):
    path, _ = bundle_path(tmp_path)
    install(owner, path)
    root = owner.paths.root
    before = state_bytes(root)
    monkeypatch.setattr(owner, "recover", lambda: pytest.fail("preflight must not recover"))
    with _build_client(owner.config) as client:
        response = client.post("/api/storage/location/" + route, json=selection(tmp_path))
    assert response.status_code == 200, response.text
    assert owner._writer is not None and not owner._writer.closed
    assert state_bytes(root) == before
    assert not get_storage_migration_path(owner.config).exists()


def test_restart_quiesces_idle_owner_before_checkpoint_and_maintenance(owner, tmp_path):
    path, _ = bundle_path(tmp_path)
    row = install(owner, path)["result"]
    called = []
    with _build_client(owner.config, request_app_shutdown=lambda **kwargs: called.append(True)) as client:
        response = client.post("/api/storage/location/restart", json=selection(tmp_path))
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "restart_initiated"
    assert called == [True]
    assert owner._writer is None and str(owner.paths.root) not in runtime._services
    assert get_storage_migration_path(owner.config).is_file()
    assert owner.config.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert (owner.paths.root / row["local_entrypoint"]).is_file()
    with pytest.raises(WorkshopError, match="storage_blocked"):
        runtime.get_service(owner.config)


@pytest.mark.parametrize("route", ["preflight", "select", "restart"])
def test_active_legacy_mutation_returns_409_without_scheduling_or_closing(owner, tmp_path, route):
    entered, release = threading.Event(), threading.Event()

    def active():
        with owner.activity():
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(active)
        assert entered.wait(5)
        try:
            with _build_client(owner.config, request_app_shutdown=lambda: pytest.fail("must not shut down")) as client:
                response = client.post("/api/storage/location/" + route, json=selection(tmp_path))
            assert response.status_code == 409, response.text
            assert response.json()["error_code"] == "model_workshop_busy"
            assert not get_storage_migration_path(owner.config).exists()
            assert owner._writer is not None and not owner._writer.closed
        finally:
            release.set()
        task.result(5)


@pytest.mark.parametrize("fault", ["pending", "invalid_row", "unknown"])
def test_preflight_refuses_uncertain_metadata_without_recovery_or_unlock(owner, tmp_path, monkeypatch, fault):
    if fault == "pending":
        (owner.paths.private / "transactions/unknown.json").write_text("{}")
    elif fault == "invalid_row":
        (owner.paths.private / "installed.json").write_text(
            '{"schema":"neko.model.installed.v1","installed":{"invalid":{}},"attempts":{}}'
        )
    else:
        (owner.paths.private / "unknown.bin").write_bytes(b"preserve")
    before = state_bytes(owner.paths.root)
    monkeypatch.setattr(owner, "recover", lambda: pytest.fail("preflight must not recover"))
    with _build_client(owner.config) as client:
        response = client.post("/api/storage/location/preflight", json=selection(tmp_path))
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] in ("model_workshop_pending", "model_workshop_state_unreadable")
    assert owner._writer is not None and not owner._writer.closed
    assert state_bytes(owner.paths.root) == before


def test_final_write_rechecks_after_early_preflight_passed(owner, tmp_path, monkeypatch):
    original = storage_router._apply_storage_mutation_writes_or_rollback

    async def change_between_phases(*args, **kwargs):
        (owner.paths.private / "transactions/unknown.json").write_text("{}")
        return await original(*args, **kwargs)

    monkeypatch.setattr(storage_router, "_apply_storage_mutation_writes_or_rollback", change_between_phases)
    with _build_client(owner.config, request_app_shutdown=lambda: pytest.fail("must not shut down")) as client:
        response = client.post("/api/storage/location/restart", json=selection(tmp_path))
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "model_workshop_pending"
    assert not get_storage_migration_path(owner.config).exists()


def test_failed_checkpoint_transaction_can_restart_a_fresh_owner(owner, tmp_path, monkeypatch):
    before = owner.config.load_raw_root_state()

    def fail_maintenance(*args, **kwargs):
        raise OSError("injected maintenance persistence failure")

    monkeypatch.setattr(storage_router, "set_root_mode", fail_maintenance)
    with _build_client(owner.config, request_app_shutdown=lambda: pytest.fail("must not shut down")) as client:
        response = client.post("/api/storage/location/restart", json=selection(tmp_path))
    assert response.status_code == 500
    assert not get_storage_migration_path(owner.config).exists()
    assert owner.config.load_raw_root_state() == before
    replacement = runtime.get_service(owner.config)
    assert replacement is not owner and replacement._writer is not None


def test_root_writer_waiting_on_storage_fence_cannot_deadlock_preflight(owner, tmp_path, monkeypatch):
    owner.close()
    runtime._services.clear()
    entered = threading.Event()
    original = runtime.WorkshopTasks.start

    def starting(service):
        entered.set()  # get_service already holds _gate while start waits for root state.
        return original(service)

    monkeypatch.setattr(runtime.WorkshopTasks, "start", starting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with root_state_transaction():
            waiting = pool.submit(runtime.get_service, owner.config)
            assert entered.wait(5)
            with pytest.raises(WorkshopError, match="busy"):
                runtime.check_migration_ready(owner.paths.root, tmp_path / "target")
        waiting.result(5)


def test_same_thread_active_operation_is_not_misclassified_as_idle(owner, tmp_path):
    # RLock reentrancy alone is insufficient proof that no mutation is active.
    with owner.activity():
        with pytest.raises(WorkshopError, match="busy"):
            runtime.check_migration_ready(owner.paths.root, tmp_path / "target")


def test_new_owner_stays_blocked_until_the_failed_write_has_rolled_back(owner, tmp_path, monkeypatch):
    entered, release, attempting = (threading.Event() for _ in range(3))
    restore = storage_router._restore_storage_mutation_state

    def hold_rollback(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return restore(*args, **kwargs)

    def fail_maintenance(*args, **kwargs):
        raise OSError("injected maintenance persistence failure")

    def request_restart():
        with _build_client(owner.config, request_app_shutdown=lambda: None) as client:
            return client.post("/api/storage/location/restart", json=selection(tmp_path))

    def new_owner():
        attempting.set()
        return runtime.get_service(owner.config)

    monkeypatch.setattr(storage_router, "set_root_mode", fail_maintenance)
    monkeypatch.setattr(storage_router, "_restore_storage_mutation_state", hold_rollback)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(request_restart)
        assert entered.wait(5)
        replacement = pool.submit(new_owner)
        assert attempting.wait(5)
        try:
            with pytest.raises(TimeoutError):
                replacement.result(0.05)
        finally:
            release.set()
        assert request.result(5).status_code == 500
        assert replacement.result(5) is not owner
    assert not get_storage_migration_path(owner.config).exists()


def test_unreadable_private_lookup_is_not_treated_as_absence(owner, tmp_path, monkeypatch):
    from pathlib import Path
    from utils.model_workshop_storage import ModelWorkshopStorageError, assert_model_workshop_stopped

    lstat = Path.lstat

    def deny_private(path, *args, **kwargs):
        if path == owner.paths.private:
            raise PermissionError("private lookup unavailable")
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", deny_private)
    with pytest.raises(ModelWorkshopStorageError, match="state_unreadable"):
        assert_model_workshop_stopped(owner.paths.root)


@pytest.mark.parametrize("mutation", ["delete", "json", "blob"])
def test_actual_mutation_waiting_for_storage_fence_is_rejected_without_deadlock(owner, tmp_path, monkeypatch, mutation):
    path, _ = bundle_path(tmp_path)
    row = install(owner, path)["result"]
    target = owner.paths.root / row["target"]
    entered = threading.Event()
    original = owner.installer.mutation

    @contextmanager
    def waiting_for_storage():
        entered.set()
        with original():
            yield

    monkeypatch.setattr(owner.installer, "mutation", waiting_for_storage)

    def mutate():
        if mutation == "delete":
            return runtime.remove_managed_model(owner.config, row)
        if mutation == "json":
            return runtime.write_managed_json(owner.config, target / "parameters.json", {"scale": 2})
        return runtime.write_managed_blob(owner.config, target / "extra.motion3.json", b"{}")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with root_state_transaction():
            pending = pool.submit(mutate)
            assert entered.wait(5)
            with pytest.raises(WorkshopError, match="busy"):
                runtime.check_migration_ready(owner.paths.root, tmp_path / "target")
        pending.result(5)
    runtime.check_migration_ready(owner.paths.root, tmp_path / "target")


def test_recovery_in_progress_blocks_migration_without_cancellation(owner, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = owner.installer.recover

    def recovering(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(owner.installer, "recover", recovering)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(owner.recover)
        assert entered.wait(5)
        try:
            with pytest.raises(WorkshopError, match="busy"):
                runtime.check_migration_ready(owner.paths.root, tmp_path / "target")
        finally:
            release.set()
        pending.result(5)
    runtime.check_migration_ready(owner.paths.root, tmp_path / "target")


def test_receiving_accepted_bytes_blocks_migration_before_a_worker_is_queued(owner, tmp_path):
    path, _ = bundle_path(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class Receiving:
        sent = False

        def read(self, count):
            if self.sent:
                return b""
            self.sent = True
            entered.set()
            assert release.wait(5)
            return path.read_bytes()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(owner.receive, Receiving())
        assert entered.wait(5)
        try:
            with pytest.raises(WorkshopError, match="busy"):
                runtime.check_migration_ready(owner.paths.root, tmp_path / "target")
        finally:
            release.set()
        attempt = pending.result(5)
    assert owner.wait(attempt, 5)["phase"] == "completed"
    runtime.check_migration_ready(owner.paths.root, tmp_path / "target")


@pytest.mark.parametrize("phase", ["admission", "streaming"])
def test_http_upload_reservation_blocks_migration_and_survives_cancellation(owner, tmp_path, monkeypatch, phase):
    from main_routers import model_market_router as api
    from main_routers.system_router import _shared
    from tests.unit.test_model_workshop_api import AUTH, asgi_call

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", AUTH["X-CSRF-Token"])
    begin = owner.begin_upload

    def admitted():
        begin()
        if phase == "admission":
            entered.set()
            assert release.wait(5)

    async def consuming(scope, receive, send):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(owner, "begin_upload", admitted)
    guard = api.ModelImportBodyGuard(consuming, config_getter=lambda: owner.config)

    async def run():
        request = asyncio.create_task(asgi_call(guard, b"body", headers={
            **AUTH, "Content-Type": "multipart/form-data; boundary=test"
        }))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            with pytest.raises(WorkshopError, match="busy"):
                await asyncio.to_thread(runtime.check_migration_ready, owner.paths.root, tmp_path / "target")
            request.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.to_thread(runtime.check_migration_ready, owner.paths.root, tmp_path / "target")

    asyncio.run(run())
    assert owner._uploads == 0 and not guard._slots.locked()


@pytest.mark.parametrize("code,key", [
    ("model_workshop_busy", "modelWorkshopBusy"),
    ("model_workshop_pending", "modelWorkshopPending"),
    ("model_workshop_state_unreadable", "modelWorkshopUnavailable"),
    ("model_workshop_root_conflict", "modelWorkshopRootConflict"),
])
def test_migration_error_codes_are_translated_in_all_eight_locales(code, key):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = (root / "static/app/app-storage-location.js").read_text()
    assert "case '" + code + "':" in script
    assert "'storage." + key + "'" in script
    for locale in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
        assert json.loads((root / "static/locales" / (locale + ".json")).read_text())["storage"][key]
