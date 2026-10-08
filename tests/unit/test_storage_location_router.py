import asyncio
import concurrent.futures
import builtins
import json
import os
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from main_routers import storage_location_router as storage_location_router_module
from main_routers.shared_state import init_shared_state
from utils.cloudsave_runtime import CLOUDSAVE_DISABLED_ENV, ROOT_MODE_MAINTENANCE_READONLY
from utils import storage_location_bootstrap as storage_location_bootstrap_module
from utils.config_manager import ConfigManager
from utils.storage_layout import resolve_storage_layout
from utils.storage_migration import (
    create_pending_storage_migration,
    get_storage_migration_path,
    load_storage_migration,
    run_pending_storage_migration,
    save_storage_migration,
)
from utils.storage_policy import (
    compute_anchor_root,
    get_storage_policy_path,
    load_storage_policy,
    normalize_runtime_root,
    save_storage_policy,
)
from utils import file_utils as file_utils_module
from utils.file_utils import atomic_write_json


@pytest.mark.unit
def test_rollback_does_not_compare_recovery_overlay_as_disk_state(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    before = manager.load_root_state()
    before["mode"] = "deferred_init"
    manager.save_root_state(before)
    snapshot = storage_location_router_module._snapshot_storage_mutation_state(
        manager, anchor_root=manager.anchor_root,
    )
    manager.save_root_state({**before, "mode": "normal", "current_root": "changed"})
    monkeypatch.setattr(manager, "load_root_state", lambda: dict(before))
    monkeypatch.setattr(manager, "_has_selected_root_unavailable_recovery_override", lambda: True)

    storage_location_router_module._restore_storage_mutation_state(
        manager, snapshot, anchor_root=manager.anchor_root,
    )

    assert json.loads(manager.root_state_path.read_text(encoding="utf-8")) == before


@pytest.mark.unit
@pytest.mark.parametrize("persist_recovery", [False, True])
async def test_recovery_overlay_unchanged_disk_skips_unwritable_root_restore(tmp_path, monkeypatch, persist_recovery):
    manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(manager, "_has_selected_root_unavailable_recovery_override", lambda: True)
    before = manager.load_root_state()
    manager.save_root_state(before if persist_recovery else manager.load_raw_root_state())
    root_bytes = manager.root_state_path.read_bytes()
    restores = []
    def deny_root_write(*args, **kwargs):
        restores.append(1)
        raise PermissionError("state directory unwritable")
    def deny_policy_write():
        raise PermissionError("state directory unwritable")
    monkeypatch.setattr(manager, "save_root_state", deny_root_write)
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=deny_policy_write,
    )
    assert error["error_code"] == "storage_policy_write_failed"
    assert restores == []
    assert manager.root_state_path.read_bytes() == root_bytes


@pytest.mark.unit
async def test_root_state_snapshot_read_error_is_classified_without_writes(tmp_path, monkeypatch):
    manager = _DummyConfigManager(tmp_path)
    def deny_read():
        raise PermissionError("private-path")
    monkeypatch.setattr(manager, "load_root_state_with_raw", deny_read)
    writes = []
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: writes.append(1),
    )
    assert error["error_code"] == "storage_state_unreadable"
    assert "private-path" not in error["error"]
    assert writes == []


@pytest.mark.unit
@pytest.mark.parametrize("cancel", [False, True])
async def test_cancelled_failed_worker_is_not_rolled_back_twice(tmp_path, monkeypatch, cancel):
    manager = _DummyConfigManager(tmp_path)
    restores = []
    async def failed_write(*args, snapshot_out, **kwargs):
        snapshot_out.update({"root_state": manager.load_root_state(), "_write_outcome": "rolled_back"})
        if cancel:
            raise asyncio.CancelledError
        raise OSError("write failed")
    monkeypatch.setattr(storage_location_router_module, "_apply_storage_mutation_writes", failed_write)
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", lambda *a, **k: restores.append(1))
    call = storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: None,
    )
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        _, error = await call
        assert error["error_code"] == "storage_policy_write_failed"
    assert restores == []


@pytest.mark.unit
async def test_unknown_write_outcome_does_not_claim_recovery(tmp_path, monkeypatch):
    manager = _DummyConfigManager(tmp_path)
    async def unknown_write(*args, snapshot_out, **kwargs):
        snapshot_out["root_state"] = manager.load_root_state()
        raise OSError("unknown outcome")
    monkeypatch.setattr(storage_location_router_module, "_apply_storage_mutation_writes", unknown_write)
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: None,
    )
    assert error["error_code"] == "storage_policy_rollback_failed"


@pytest.mark.unit
async def test_snapshot_busy_read_retries_in_worker(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_bytes(b'{invalid json retained verbatim')
    reads = []
    original_read = Path.read_bytes
    def busy_once(state_path):
        reads.append(1)
        if len(reads) == 1:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error
        return original_read(state_path)
    monkeypatch.setattr(Path, "read_bytes", busy_once)
    preimage = await asyncio.to_thread(storage_location_router_module._read_state_file_preimage, path)
    assert preimage == {"existed": True, "bytes": b'{invalid json retained verbatim'}
    assert reads == [1, 1]


@pytest.mark.unit
async def test_executor_failure_is_not_diagnosed_as_unwritable_directory(tmp_path, monkeypatch):
    manager = _DummyConfigManager(tmp_path)
    async def reject_job(job):
        raise RuntimeError("cannot schedule new futures after shutdown")
    monkeypatch.setattr(storage_location_router_module, "_run_locked_storage_job", reject_job)
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: None,
    )
    assert error["error_code"] == "storage_operation_failed"
    assert "可写" not in error["error"]
    assert "已恢复" not in error["error"]


@pytest.mark.unit
@pytest.mark.parametrize("restore_fails", [False, True])
async def test_barrier_rollback_cancellation_is_propagated(tmp_path, monkeypatch, restore_fails):
    manager = _DummyConfigManager(tmp_path)
    started = threading.Event()
    finish = threading.Event()
    finished = threading.Event()
    async def fail_release(**kwargs):
        raise RuntimeError("barrier release failed")
    def restore(*args, **kwargs):
        started.set()
        try:
            assert finish.wait(3)
            if restore_fails:
                raise RuntimeError("rollback failed")
        finally:
            finished.set()
    monkeypatch.setattr(storage_location_router_module, "_release_storage_startup_barrier_if_needed", fail_release)
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", restore)
    task = asyncio.create_task(storage_location_router_module._release_storage_startup_barrier_result(
        manager, snapshot={"root_state": manager.load_root_state()}, anchor_root=manager.anchor_root, reason="test",
    ))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.unit
def test_corrupt_root_state_remains_logged(tmp_path, caplog):
    manager = _make_real_config_manager(tmp_path)
    manager.root_state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.root_state_path.write_text("{truncated", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        manager.load_root_state()
    assert "加载 JSON 文件失败" in caplog.text
    assert "root_state.json" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize("error_type", [AttributeError, KeyError])
async def test_root_snapshot_programming_error_is_not_reported_as_unreadable(tmp_path, monkeypatch, error_type):
    manager = _DummyConfigManager(tmp_path)
    def broken_loader():
        raise error_type("private-programming-error")
    monkeypatch.setattr(manager, "load_root_state_with_raw", broken_loader)
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: None,
    )
    assert error["error_code"] == "storage_operation_failed"
    assert "private-" not in error["error"]


@pytest.mark.unit
def test_unavailable_root_recovery_ignores_untouched_unreadable_migration(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    unavailable = tmp_path / "offline" / "N.E.K.O"
    save_storage_policy(manager, selected_root=unavailable, selection_source="custom")
    manager = _make_real_config_manager(tmp_path)
    path = get_storage_migration_path(manager, anchor_root=_route_anchor_root(manager))
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage_location_bootstrap_module, "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION", False)
    with _build_client(manager) as client:
        response = client.post("/api/storage/location/select", json={
            "selected_root": str(manager.app_docs_dir), "selection_source": "current",
        })
    assert response.status_code == 200
    assert response.json()["result"] == "continue_current_session"
    assert path.is_dir()


@pytest.mark.unit
def test_restart_restore_unchanged_root_skips_write(tmp_path, monkeypatch):
    manager = _DummyConfigManager(tmp_path)
    writes = []
    def deny_write(*args):
        writes.append(1)
        raise PermissionError("directory unwritable")
    before = manager.load_root_state()
    monkeypatch.setattr(manager, "save_root_state", deny_write)
    storage_location_router_module._restore_storage_mutation_state(
        manager, {"include_policy": False, "root_state": before, "migration_preimage": {"existed": False, "bytes": None}},
        anchor_root=manager.anchor_root,
    )
    assert writes == []


@pytest.mark.unit
@pytest.mark.parametrize("unexpected", [False, True])
def test_restart_reports_partial_rollback_failure(tmp_path, monkeypatch, unexpected):
    manager = _DummyConfigManager(tmp_path)
    def shutdown():
        raise RuntimeError("shutdown failed")
    def deny_restore(*args, **kwargs):
        if unexpected:
            raise RuntimeError("private-worker-error")
        raise storage_location_router_module._StorageRollbackPartialError([
            ("root_state", manager.anchor_root / "state" / "root_state.json", PermissionError("private-path")),
        ])
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", deny_restore)
    with _build_client(manager, request_app_shutdown=shutdown) as client:
        response = client.post("/api/storage/location/restart", json={
            "selected_root": str(tmp_path / "new-storage" / "N.E.K.O"), "selection_source": "custom",
        })
    assert response.status_code == 500
    assert response.json()["error_code"] == "restart_rollback_failed"
    assert "private-path" not in response.json()["error"]
    assert response.json()["restart_mode"] == "migrate_after_shutdown"
    assert "private-worker-error" not in response.json()["error"]


@pytest.mark.unit
@pytest.mark.parametrize("raw", [b"null", b"[]", b"{truncated"])
async def test_invalid_root_state_aborts_before_writes(tmp_path, monkeypatch, raw):
    manager = _make_real_config_manager(tmp_path)
    manager.root_state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.root_state_path.write_bytes(raw)
    writes = []
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=lambda: writes.append(1),
    )
    assert error["error_code"] == "storage_state_invalid"
    assert writes == []
    assert manager.root_state_path.read_bytes() == raw


@pytest.mark.unit
async def test_unchanged_preimage_busy_read_skips_unwritable_restore(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_bytes(b"unchanged")
    original_read = Path.read_bytes
    reads = []
    writes = []
    def busy_once(state_path):
        reads.append(1)
        if len(reads) == 1:
            error = PermissionError("busy")
            error.winerror = 32
            raise error
        return original_read(state_path)
    monkeypatch.setattr(Path, "read_bytes", busy_once)
    monkeypatch.setattr(file_utils_module, "atomic_write_bytes", lambda *a, **k: writes.append(1))
    await asyncio.to_thread(storage_location_router_module._restore_state_file_from_preimage,
                            path, {"existed": True, "bytes": b"unchanged"})
    assert reads == [1, 1]
    assert writes == []


@pytest.mark.unit
def test_root_snapshot_derives_recovery_from_one_raw_read(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    raw = manager.load_raw_root_state()
    reads = []
    def read_once(default_value=None, *, tolerate_replace=False):
        reads.append(1)
        return dict(raw)
    monkeypatch.setattr(manager, "load_raw_root_state", read_once)
    monkeypatch.setattr(manager, "_has_selected_root_unavailable_recovery_override", lambda: True)
    snapshot = storage_location_router_module._snapshot_storage_mutation_state(
        manager, anchor_root=manager.anchor_root,
    )
    assert reads == [1]
    assert snapshot["root_state_raw"] == raw
    assert snapshot["root_state"] == manager._build_selected_root_unavailable_recovery_state(raw)


@pytest.mark.unit
def test_barrier_release_reports_failed_rollback(tmp_path, monkeypatch):
    manager = _DummyConfigManager(tmp_path)
    def release(reason=None):
        raise RuntimeError("private-barrier-error")
    def deny_restore(*args, **kwargs):
        raise RuntimeError("private-rollback-error")
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", deny_restore)
    with _build_client(manager, release_storage_startup_barrier=release) as client:
        response = client.post("/api/storage/location/select", json={
            "selected_root": str(manager.app_docs_dir), "selection_source": "current",
        })
    assert response.status_code == 500
    assert response.json()["error_code"] == "startup_release_rollback_failed"
    assert "private-" not in response.json()["error"]
    assert response.json()["phase"] == "startup_release"


@pytest.mark.unit
async def test_root_snapshot_and_rollback_tolerate_busy_reads(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    manager.save_root_state(manager.load_raw_root_state())
    original_read = file_utils_module.read_json
    reads = []
    writes = []
    def busy_read(path, **kwargs):
        if Path(path) == manager.root_state_path:
            reads.append(1)
            if len(reads) in (1, 3):
                exc = PermissionError("sharing violation")
                exc.winerror = 32
                raise exc
        return original_read(path, **kwargs)
    def deny_root_write(*args, **kwargs):
        writes.append(1)
        raise PermissionError("directory not writable")
    def deny_mutation():
        raise PermissionError("initial write denied")
    monkeypatch.setattr(file_utils_module, "read_json", busy_read)
    monkeypatch.setattr(manager, "save_root_state", deny_root_write)
    _, error = await storage_location_router_module._apply_storage_mutation_writes_or_rollback(
        manager, anchor_root=manager.anchor_root, snapshot_out={}, write=deny_mutation,
    )
    assert error["error_code"] == "storage_policy_write_failed"
    assert reads == [1, 1, 1, 1]
    assert writes == []


@pytest.mark.unit
async def test_plain_root_read_does_not_retry_access_denied(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    before = manager.load_raw_root_state()
    manager.save_root_state(before)
    original_open = builtins.open
    reads = []
    def denied_once(path, *args, **kwargs):
        if path in (manager.root_state_path, str(manager.root_state_path)):
            reads.append(1)
            if len(reads) == 1:
                error = PermissionError("access denied")
                error.winerror = 5
                raise error
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", denied_once)
    with pytest.raises(storage_location_router_module.LocalStateDirectoryError):
        manager.load_root_state()
    assert reads == [1]
    reads.clear()
    semantic, raw = await asyncio.to_thread(manager.load_root_state_with_raw)
    assert reads == [1, 1]
    assert semantic == raw == before


@pytest.mark.unit
@pytest.mark.parametrize("retry", [False, True])
def test_json_loader_default_and_missing_contract_is_shared(tmp_path, retry):
    manager = _make_real_config_manager(tmp_path)
    reader = file_utils_module.read_json_tolerating_replace if retry else None
    missing = tmp_path / "missing-state.json"
    with pytest.raises(FileNotFoundError):
        manager._load_json_file(missing, reader=reader)
    default = {"nested": {"value": 1}}
    restored = manager._load_json_file(missing, default, reader=reader)
    restored["nested"]["value"] = 2
    assert default["nested"]["value"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("override", [False, True])
def test_raw_root_snapshot_is_not_aliased_to_semantic_state(tmp_path, monkeypatch, override):
    manager = _make_real_config_manager(tmp_path)
    manager.save_root_state({**manager.load_raw_root_state(), "nested": {"value": 1}})
    monkeypatch.setattr(manager, "_has_selected_root_unavailable_recovery_override", lambda: override)
    semantic, raw = manager.load_root_state_with_raw()
    semantic["nested"]["value"] = 2
    assert raw["nested"]["value"] == 1


@pytest.mark.unit
async def test_absent_preimage_restore_retries_busy_unlink(tmp_path, monkeypatch):
    path = tmp_path / "new-checkpoint.json"
    path.write_bytes(b"new state")
    original_unlink = Path.unlink
    attempts = []
    def busy_once(state_path, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            exc = PermissionError("sharing violation")
            exc.winerror = 32
            raise exc
        return original_unlink(state_path, **kwargs)
    monkeypatch.setattr(Path, "unlink", busy_once)
    await asyncio.to_thread(storage_location_router_module._restore_state_file_from_preimage,
                            path, {"existed": False, "bytes": None})
    assert attempts == [1, 1]
    assert not path.exists()


@pytest.mark.unit
@pytest.mark.parametrize("cancel", [False, True])
async def test_plain_selection_restores_root_modified_by_barrier_release(tmp_path, cancel):
    manager = _DummyConfigManager(tmp_path)
    before = manager.load_root_state()
    migration_path = get_storage_migration_path(manager, anchor_root=_route_anchor_root(manager))
    migration_path.mkdir(parents=True, exist_ok=True)
    async def release(reason=None):
        manager.save_root_state({**before, "last_successful_boot_at": "new-boot-marker"})
        if cancel:
            raise asyncio.CancelledError
        raise RuntimeError("initialization failed after marking startup successful")
    with _build_client(manager, release_storage_startup_barrier=release):
        call = storage_location_router_module._post_storage_location_select_locked(
            storage_location_router_module.StorageLocationSelectionRequest(
                selected_root=str(manager.app_docs_dir), selection_source="current",
            ), Response(),
        )
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await call
        else:
            result = await call
            assert result["error_code"] == "startup_release_failed"
    assert manager.load_root_state() == before
    assert migration_path.is_dir()


@pytest.mark.unit
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_restart_migration_write_failure_rolls_back_in_original_job(tmp_path, monkeypatch, rollback_fails):
    manager = _DummyConfigManager(tmp_path)
    before = manager.load_root_state()
    jobs = []
    real_run_job = storage_location_router_module._run_locked_storage_job
    async def spy_job(job):
        jobs.append(job)
        return await real_run_job(job)
    def deny_mode(*args, **kwargs):
        raise PermissionError("mode write denied after checkpoint creation")
    monkeypatch.setattr(storage_location_router_module, "_run_locked_storage_job", spy_job)
    monkeypatch.setattr(storage_location_router_module, "set_root_mode", deny_mode)
    if rollback_fails:
        def deny_restore(*args, **kwargs):
            raise PermissionError("private-restore-path")
        monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", deny_restore)
    with _build_client(manager, request_app_shutdown=lambda: None) as client:
        response = client.post("/api/storage/location/restart", json={
            "selected_root": str(tmp_path / "new-storage" / "N.E.K.O"), "selection_source": "custom",
        })
    assert response.status_code == 500
    assert response.json()["error_code"] == (
        "restart_rollback_failed" if rollback_fails else "storage_policy_write_failed"
    )
    assert response.json()["restart_mode"] == "migrate_after_shutdown"
    assert "private-" not in response.json()["error"]
    # 另一个 job 是路由的 bootstrap reconcile；没有单独的回滚 job。
    assert sum(getattr(job, "__name__", "") == "_job" for job in jobs) == 1
    assert not any(getattr(job, "func", None) in (
        storage_location_router_module._restore_storage_mutation_state,
    ) for job in jobs)
    if rollback_fails:
        return
    assert not get_storage_migration_path(manager, anchor_root=_route_anchor_root(manager)).exists()
    assert manager.load_root_state() == before


@pytest.mark.unit
async def test_restart_rebind_cancelled_write_restores_only_once(tmp_path, monkeypatch):
    manager = _make_real_config_manager(tmp_path)
    selected = tmp_path / "offline" / "N.E.K.O"
    save_storage_policy(manager, selected_root=selected, selection_source="custom")
    manager = _make_real_config_manager(tmp_path)
    selected.mkdir(parents=True, exist_ok=True)
    restores = []
    real_restore = storage_location_router_module._restore_storage_mutation_state
    def restore(*args, **kwargs):
        restores.append(1)
        return real_restore(*args, **kwargs)
    async def cancelled_write(config_manager, *, anchor_root, snapshot_out, write, include_policy=True, include_migration=True):
        snapshot_out.update(storage_location_router_module._snapshot_storage_mutation_state(
            config_manager, anchor_root=anchor_root,
        ))
        write()
        snapshot_out["_write_outcome"] = "success"
        raise asyncio.CancelledError
    monkeypatch.setattr(storage_location_router_module, "_apply_storage_mutation_writes", cancelled_write)
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", restore)
    with _build_client(manager, request_app_shutdown=lambda: None):
        with pytest.raises(asyncio.CancelledError):
            await storage_location_router_module._post_storage_location_restart_locked(
                storage_location_router_module.StorageLocationSelectionRequest(
                    selected_root=str(selected), selection_source="current",
                ), Response(),
            )
    assert restores == [1]


@pytest.mark.unit
@pytest.mark.parametrize("unreadable", [False, True])
def test_restart_preserves_raw_checkpoint_on_snapshot_or_shutdown_failure(tmp_path, monkeypatch, unreadable):
    manager = _DummyConfigManager(tmp_path)
    path = get_storage_migration_path(manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{ "status": "completed", "custom": 7 }\n'
    path.write_bytes(original)
    original_read = Path.read_bytes
    def read_bytes(state_path):
        if unreadable and state_path == path:
            raise PermissionError("private-checkpoint-path")
        return original_read(state_path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    shutdown_calls = []
    def shutdown():
        shutdown_calls.append(1)
        raise RuntimeError("private-shutdown-path")
    before = manager.load_root_state()
    with _build_client(manager, request_app_shutdown=shutdown) as client:
        response = client.post("/api/storage/location/restart", json={
            "selected_root": str(tmp_path / "new-storage" / "N.E.K.O"),
            "selection_source": "custom",
        })
    assert response.status_code == 500
    assert response.json()["error_code"] == ("storage_state_unreadable" if unreadable else "restart_schedule_failed")
    assert "private-" not in response.json()["error"]
    if unreadable:
        assert "状态文件当前无法读取" in response.json()["error"]
    else:
        assert response.json()["error"] == "受控关闭启动失败，请稍后重试。"
    assert original_read(path) == original
    assert manager.load_root_state() == before
    assert len(shutdown_calls) == (0 if unreadable else 1)


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"
        self.anchor_root = self._standard_root / self.app_name
        self.anchor_root.mkdir(parents=True, exist_ok=True)
        self.committed_selected_root = self.app_docs_dir
        self.reported_current_root = self.app_docs_dir
        self.recovery_committed_root_unavailable = False
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.plugins_dir = self.app_docs_dir / "plugins"
        self.live2d_dir = self.app_docs_dir / "live2d"
        self.vrm_dir = self.app_docs_dir / "vrm"
        self.mmd_dir = self.app_docs_dir / "mmd"
        self.workshop_dir = self.app_docs_dir / "workshop"
        self.chara_dir = self.app_docs_dir / "character_cards"
        self.avatar_tools_dir = self.app_docs_dir / "avatar_tools"
        self._readable_live2d_dir = None
        self.is_windows_cfa_fallback_active = False
        self._root_state = {
            "mode": "normal",
            "last_known_good_root": str(self.app_docs_dir),
            "last_migration_result": "",
            "last_migration_source": "",
        }

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]

    def get_legacy_app_root_candidates(self):
        return []

    @property
    def cloudsave_dir(self):
        return self.anchor_root / "cloudsave"

    @property
    def local_state_dir(self):
        return self.anchor_root / "state"

    def load_root_state(self):
        return dict(self._root_state)

    def load_raw_root_state(self, *, tolerate_replace=False):
        return dict(self._root_state)

    def load_root_state_with_raw(self):
        raw = self.load_raw_root_state()
        return dict(raw), raw

    def save_root_state(self, data):
        self._root_state = dict(data)

    def get_live2d_lookup_roots(self, *, prefer_writable: bool = True):
        ordered = [self.live2d_dir, self._readable_live2d_dir] if prefer_writable else [self._readable_live2d_dir, self.live2d_dir]
        return [path for path in ordered if path is not None]


def _make_real_config_manager(tmp_path: Path):
    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _make_anchor_root_config_manager(tmp_path: Path):
    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=standard_root),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _build_client(config_manager, *, request_app_shutdown=None, release_storage_startup_barrier=None):
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=config_manager,
        request_app_shutdown=request_app_shutdown,
        release_storage_startup_barrier=release_storage_startup_barrier,
    )
    app = FastAPI()
    app.include_router(storage_location_router_module.router)
    return TestClient(app)


@pytest.mark.unit
def test_storage_location_target_content_probe_uses_public_runtime_helper(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "target" / "N.E.K.O"

    with patch("utils.cloudsave_runtime.runtime_root_has_user_content", return_value=True) as helper:
        assert storage_location_router_module._target_root_has_user_content(target_root, config_manager) is True

    helper.assert_called_once_with(target_root, config_manager=config_manager)


@pytest.mark.unit
def test_collect_warning_codes_matches_cloud_sync_path_segments_only(tmp_path):
    current_root = tmp_path / "current" / "N.E.K.O"

    false_positive_target = tmp_path / "onedrive_backup_restore" / "N.E.K.O"
    assert "sync_folder" not in storage_location_router_module._collect_warning_codes(
        current_root,
        false_positive_target,
    )

    dropbox_backup_target = tmp_path / "dropbox_backup_restore" / "N.E.K.O"
    assert "sync_folder" not in storage_location_router_module._collect_warning_codes(
        current_root,
        dropbox_backup_target,
    )

    onedrive_target = tmp_path / "OneDrive - Example" / "N.E.K.O"
    assert "sync_folder" in storage_location_router_module._collect_warning_codes(
        current_root,
        onedrive_target,
    )

    dropbox_target = tmp_path / "Dropbox (Personal)" / "N.E.K.O"
    assert "sync_folder" in storage_location_router_module._collect_warning_codes(
        current_root,
        dropbox_target,
    )

    google_drive_target = tmp_path / "Google Drive (Acme)" / "N.E.K.O"
    assert "sync_folder" in storage_location_router_module._collect_warning_codes(
        current_root,
        google_drive_target,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_storage_location_mutation_routes_share_serialization_lock():
    payload = storage_location_router_module.StorageLocationSelectionRequest(
        selected_root="/tmp/neko-target",
        selection_source="custom",
    )
    active_calls = 0
    max_active_calls = 0
    first_call_entered = asyncio.Event()
    release_first_call = asyncio.Event()

    async def fake_select(_payload, _response):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        first_call_entered.set()
        await release_first_call.wait()
        active_calls -= 1
        return {"route": "select"}

    async def fake_restart(_payload, _response):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        active_calls -= 1
        return {"route": "restart"}

    async def fake_cleanup(_payload, _response):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        active_calls -= 1
        return {"route": "cleanup"}

    with patch.object(
        storage_location_router_module,
        "_post_storage_location_select_locked",
        side_effect=fake_select,
    ), patch.object(
        storage_location_router_module,
        "_post_storage_location_restart_locked",
        side_effect=fake_restart,
    ), patch.object(
        storage_location_router_module,
        "_post_storage_location_retained_source_cleanup_locked",
        side_effect=fake_cleanup,
    ):
        select_task = asyncio.create_task(
            storage_location_router_module.post_storage_location_select(payload, Response())
        )
        await asyncio.wait_for(first_call_entered.wait(), timeout=1.0)

        restart_task = asyncio.create_task(
            storage_location_router_module.post_storage_location_restart(payload, Response())
        )
        cleanup_task = asyncio.create_task(
            storage_location_router_module.post_storage_location_retained_source_cleanup(
                storage_location_router_module.StorageLocationCleanupRequest(),
                Response(),
            )
        )
        await asyncio.sleep(0)
        assert restart_task.done() is False
        assert cleanup_task.done() is False

        release_first_call.set()
        select_result, restart_result, cleanup_result = await asyncio.gather(select_task, restart_task, cleanup_task)

    assert select_result == {"route": "select"}
    assert restart_result == {"route": "restart"}
    assert cleanup_result == {"route": "cleanup"}
    assert max_active_calls == 1


@pytest.mark.unit
def test_storage_location_select_same_path_persists_policy_and_continues(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "continue_current_session"
    assert payload["selected_root"] == str(config_manager.app_docs_dir)

    policy_path = get_storage_policy_path(config_manager)
    assert policy_path.is_file()

    policy_payload = load_storage_policy(config_manager)
    assert policy_payload["selected_root"] == str(config_manager.app_docs_dir)
    assert policy_payload["selection_source"] == "user_selected"


@pytest.mark.unit
def test_storage_location_select_same_path_releases_limited_startup_barrier(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    release_calls = []

    async def release_storage_startup_barrier(*, reason: str):
        release_calls.append(reason)

    with _build_client(
        config_manager,
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    assert release_calls == ["storage_selection_continue_current_session"]


@pytest.mark.unit
def test_storage_location_exit_requests_application_shutdown(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []

    async def request_app_shutdown():
        shutdown_calls.append("shutdown")

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "result": "shutdown_initiated",
    }
    assert shutdown_calls == ["shutdown"]


@pytest.mark.unit
def test_storage_location_exit_reports_unavailable_without_shutdown_callback(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "restart_unavailable"


@pytest.mark.unit
def test_storage_location_exit_requires_storage_action_header(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []

    with _build_client(config_manager, request_app_shutdown=lambda: shutdown_calls.append("shutdown")) as client:
        response = client.post("/api/storage/location/exit")

    assert response.status_code == 403
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_exit_forbidden"
    assert shutdown_calls == []


@pytest.mark.unit
def test_storage_location_exit_ignores_ready_storage_state(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )

    with _build_client(config_manager, request_app_shutdown=lambda: shutdown_calls.append("shutdown")) as client:
        response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_exit_not_required"
    assert shutdown_calls == []


@pytest.mark.unit
def test_storage_location_exit_allows_maintenance_readonly_shutdown(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    config_manager.save_root_state({
        "mode": ROOT_MODE_MAINTENANCE_READONLY,
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "restart_pending:test",
        "last_migration_source": str(config_manager.app_docs_dir),
    })

    with _build_client(config_manager, request_app_shutdown=lambda: shutdown_calls.append("shutdown")) as client:
        response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "result": "shutdown_initiated",
    }
    assert shutdown_calls == ["shutdown"]


@pytest.mark.unit
def test_storage_location_mutation_routes_reject_cloudsave_disabled_without_root_state_read(monkeypatch, tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    def fail_root_state_read():
        raise AssertionError("cloudsave disabled storage mutation routes should not read root_state")

    config_manager.load_root_state = fail_root_state_read
    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")

    shutdown_calls = []

    with _build_client(config_manager, request_app_shutdown=lambda: shutdown_calls.append("shutdown")) as client:
        responses = [
            client.post(
                "/api/storage/location/exit",
                headers={"X-Neko-Storage-Action": "exit"},
            ),
            client.post(
                "/api/storage/location/select",
                json={
                    "selected_root": str(config_manager.app_docs_dir),
                    "selection_source": "current",
                },
            ),
            client.post(
                "/api/storage/location/preflight",
                json={
                    "selected_root": str(tmp_path / "target" / "N.E.K.O"),
                    "selection_source": "custom",
                },
            ),
            client.post(
                "/api/storage/location/restart",
                json={
                    "selected_root": str(tmp_path / "target" / "N.E.K.O"),
                    "selection_source": "custom",
                },
            ),
            client.post("/api/storage/location/retained-source/cleanup", json={}),
        ]

    for response in responses:
        assert response.status_code == 409
        payload = response.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "cloudsave_local_state_unavailable"
        assert payload["cloudsave_disabled"] is True
        assert payload["cloudsave_disabled_reason"] == "local_state_unavailable"
    assert shutdown_calls == []


@pytest.mark.unit
def test_storage_location_mutation_routes_do_not_reject_non_local_state_cloudsave_disabled(monkeypatch, tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "manual_disabled")

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(tmp_path / "target" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("error_code") != "cloudsave_local_state_unavailable"


@pytest.mark.unit
def test_storage_location_select_same_path_rolls_back_when_startup_release_fails(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    previous_root_state = config_manager.load_root_state()

    async def release_storage_startup_barrier(*, reason: str):
        raise RuntimeError("release failed")

    with _build_client(
        config_manager,
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error_code"] == "startup_release_failed"
    assert load_storage_policy(config_manager) is None
    assert config_manager.load_root_state() == previous_root_state


@pytest.mark.unit
def test_storage_location_select_different_path_requires_restart_without_committing_policy(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_required"
    assert payload["selected_root"] == str(target_root.resolve())
    assert payload["target_root"] == str(target_root.resolve())
    assert isinstance(payload["estimated_required_bytes"], int)
    assert isinstance(payload["target_free_bytes"], int)
    assert payload["permission_ok"] is True
    assert payload["warning_codes"] == []
    assert payload["blocking_error_code"] == ""
    assert payload["blocking_error_message"] == ""

    assert not get_storage_policy_path(config_manager).exists()


@pytest.mark.unit
def test_storage_location_select_custom_parent_targets_app_subdirectory(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-storage-parent"
    selected_parent.mkdir()
    expected_root = selected_parent / "N.E.K.O"

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(selected_parent),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_required"
    assert payload["selected_root"] == str(expected_root.resolve())
    assert payload["target_root"] == str(expected_root.resolve())
    assert payload["blocking_error_code"] == ""
    assert payload["target_has_existing_content"] is False


@pytest.mark.unit
def test_storage_location_preflight_different_path_is_side_effect_free(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    release_calls = []
    shutdown_calls = {"count": 0}
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    policy_payload = save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    previous_root_state = config_manager.load_root_state()

    async def release_storage_startup_barrier(*, reason: str):
        release_calls.append(reason)

    def request_app_shutdown():
        shutdown_calls["count"] += 1

    with _build_client(
        config_manager,
        request_app_shutdown=request_app_shutdown,
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_required"
    assert payload["restart_mode"] == "migrate_after_shutdown"
    assert payload["selected_root"] == str(target_root.resolve())
    assert payload["target_root"] == str(target_root.resolve())
    assert payload["permission_ok"] is True
    assert payload["blocking_error_code"] == ""

    assert load_storage_policy(config_manager) == policy_payload
    assert config_manager.load_root_state() == previous_root_state
    assert not get_storage_migration_path(config_manager).exists()
    assert release_calls == []
    assert shutdown_calls["count"] == 0


@pytest.mark.unit
def test_storage_location_preflight_same_path_does_not_continue_current_session(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    release_calls = []
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    policy_payload = save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    previous_root_state = config_manager.load_root_state()

    async def release_storage_startup_barrier(*, reason: str):
        release_calls.append(reason)

    with _build_client(
        config_manager,
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_not_required"
    assert payload["selected_root"] == str(config_manager.app_docs_dir.resolve())
    assert payload["target_root"] == str(config_manager.app_docs_dir.resolve())

    assert load_storage_policy(config_manager) == policy_payload
    assert config_manager.load_root_state() == previous_root_state
    assert not get_storage_migration_path(config_manager).exists()
    assert release_calls == []


@pytest.mark.unit
def test_storage_location_preflight_existing_target_content_requires_confirmation(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-storage-parent"
    target_root = selected_parent / "N.E.K.O"
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(selected_parent),
                "selection_source": "custom",
                "confirm_existing_target_content": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_required"
    assert payload["selected_root"] == str(target_root.resolve())
    assert payload["target_has_existing_content"] is True
    assert payload["requires_existing_target_confirmation"] is True
    assert "覆盖目标中的同名运行时数据目录" in payload["existing_target_confirmation_message"]
    assert not get_storage_migration_path(config_manager).exists()


@pytest.mark.unit
def test_storage_location_preflight_rejects_bootstrap_blocking_without_releasing_barrier(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    release_calls = []

    async def release_storage_startup_barrier(*, reason: str):
        release_calls.append(reason)

    with _build_client(
        config_manager,
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(tmp_path / "new-storage" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_bootstrap_blocking"
    assert payload["blocking_reason"] == "selection_required"
    assert load_storage_policy(config_manager) is None
    assert not get_storage_migration_path(config_manager).exists()
    assert release_calls == []


@pytest.mark.unit
def test_storage_location_preflight_rejects_existing_pending_migration(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    migration_payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="recommended",
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(tmp_path / "other-storage" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "migration_already_pending"
    assert payload["blocking_reason"] == "migration_pending"
    assert load_storage_migration(config_manager) == migration_payload


@pytest.mark.unit
def test_storage_location_preflight_rejects_maintenance_readonly_state(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    previous_policy = load_storage_policy(config_manager)
    config_manager.save_root_state({
        "mode": ROOT_MODE_MAINTENANCE_READONLY,
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "restart_pending:test",
        "last_migration_source": str(config_manager.app_docs_dir),
    })
    previous_root_state = config_manager.load_root_state()

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/preflight",
            json={
                "selected_root": str(tmp_path / "new-storage" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "migration_already_pending"
    assert payload["blocking_reason"] == "maintenance_readonly"
    assert load_storage_policy(config_manager) == previous_policy
    assert config_manager.load_root_state() == previous_root_state
    assert not get_storage_migration_path(config_manager).exists()


@pytest.mark.unit
def test_storage_location_existing_target_content_requires_confirmation_before_restart(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-storage-parent"
    target_root = selected_parent / "N.E.K.O"
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text('{"existing": true}', encoding="utf-8")
    shutdown_calls = {"count": 0}

    def request_app_shutdown():
        shutdown_calls["count"] += 1

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(selected_parent),
                "selection_source": "custom",
            },
        )
        restart_without_confirmation_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(selected_parent),
                "selection_source": "custom",
            },
        )
        assert not get_storage_migration_path(config_manager).exists()
        restart_with_confirmation_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(selected_parent),
                "selection_source": "custom",
                "confirm_existing_target_content": True,
            },
        )

    assert select_response.status_code == 200
    payload = select_response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_required"
    assert payload["selected_root"] == str(target_root.resolve())
    assert payload["blocking_error_code"] == ""
    assert payload["target_has_existing_content"] is True
    assert payload["requires_existing_target_confirmation"] is True
    assert "覆盖目标中的同名运行时数据目录" in payload["existing_target_confirmation_message"]

    assert restart_without_confirmation_response.status_code == 409
    missing_confirmation_payload = restart_without_confirmation_response.json()
    assert missing_confirmation_payload["error_code"] == "target_confirmation_required"
    assert missing_confirmation_payload["requires_existing_target_confirmation"] is True

    assert restart_with_confirmation_response.status_code == 200
    restart_payload = restart_with_confirmation_response.json()
    assert restart_payload["ok"] is True
    assert restart_payload["result"] == "restart_initiated"
    assert restart_payload["requires_existing_target_confirmation"] is True
    assert shutdown_calls["count"] == 1
    migration_payload = load_storage_migration(config_manager)
    assert migration_payload["target_root"] == str(target_root.resolve())
    assert migration_payload["confirmed_existing_target_content"] is True


@pytest.mark.unit
def test_storage_location_select_rejects_anchor_reserved_path(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    invalid_target = tmp_path / "anchor-base" / "N.E.K.O" / "state" / "nested"

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(invalid_target),
                "selection_source": "custom",
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "selected_root_inside_state"


@pytest.mark.unit
def test_storage_location_pick_directory_returns_selected_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = str((tmp_path / "picked" / "N.E.K.O").resolve())

    with patch.object(
        storage_location_router_module,
        "_pick_storage_location_directory",
        return_value=selected_root,
    ):
        with _build_client(config_manager) as client:
            response = client.post(
                "/api/storage/location/pick-directory",
                json={"start_path": str(tmp_path)},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cancelled"] is False
    assert payload["selected_root"] == selected_root


@pytest.mark.unit
def test_storage_location_open_current_opens_only_current_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    opened_paths = []

    def fake_open_path(path):
        opened_paths.append(Path(path))

    with patch.object(
        storage_location_router_module,
        "_open_path_in_file_manager",
        side_effect=fake_open_path,
    ):
        with _build_client(config_manager) as client:
            response = client.post("/api/storage/location/open-current")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["current_root"] == str(config_manager.app_docs_dir.resolve())
    assert opened_paths == [config_manager.app_docs_dir.resolve()]


@pytest.mark.unit
def test_storage_location_open_current_reports_unavailable(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch.object(
        storage_location_router_module,
        "_open_path_in_file_manager",
        side_effect=storage_location_router_module._OpenStorageRootUnavailable(
            "open_storage_root_unavailable",
            "当前环境暂不支持直接打开目录。",
        ),
    ):
        with _build_client(config_manager) as client:
            response = client.post("/api/storage/location/open-current")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "open_storage_root_unavailable"
    assert payload["current_root"] == str(config_manager.app_docs_dir.resolve())


@pytest.mark.unit
def test_storage_location_bootstrap_falls_back_to_runtime_config_manager_when_shared_state_is_not_ready(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch.object(
        storage_location_router_module,
        "get_config_manager",
        side_effect=RuntimeError("shared_state unavailable"),
    ), patch.object(
        storage_location_router_module,
        "get_runtime_config_manager",
        return_value=config_manager,
    ):
        with _build_client(config_manager) as client:
            response = client.get("/api/storage/location/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_root"] == str(config_manager.app_docs_dir)
    assert payload["blocking_reason"] == "selection_required"


@pytest.mark.unit
def test_storage_location_diagnostics_reports_runtime_entries_under_effective_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.get("/api/storage/location/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["layout"]["effective_root"] == str(config_manager.app_docs_dir.resolve())
    assert payload["summary"]["all_runtime_entries_read_from_effective_root_only"] is True
    assert payload["summary"]["entries_with_reads_outside_effective_root"] == []
    assert payload["summary"]["entries_reading_retained_source_root"] == []
    assert payload["runtime_entries"]["config"]["read_roots"] == [str(config_manager.config_dir.resolve())]
    assert payload["runtime_entries"]["config"]["write_root"] == str(config_manager.config_dir.resolve())
    assert payload["runtime_entries"]["config"]["reads_outside_effective_root"] == []
    assert payload["runtime_entries"]["avatar_tools"]["read_roots"] == [
        str(config_manager.avatar_tools_dir.resolve())
    ]
    assert payload["runtime_entries"]["avatar_tools"]["write_root"] == str(
        config_manager.avatar_tools_dir.resolve()
    )


@pytest.mark.unit
def test_storage_location_diagnostics_flags_live2d_fallback_reads_outside_effective_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    legacy_live2d_dir = tmp_path / "legacy-runtime" / "N.E.K.O" / "live2d"
    legacy_live2d_dir.mkdir(parents=True, exist_ok=True)
    config_manager._readable_live2d_dir = legacy_live2d_dir
    config_manager.is_windows_cfa_fallback_active = True

    with _build_client(config_manager) as client:
        response = client.get("/api/storage/location/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["all_runtime_entries_read_from_effective_root_only"] is False
    assert payload["summary"]["entries_with_reads_outside_effective_root"] == ["live2d"]
    assert payload["runtime_entries"]["live2d"]["reads_outside_effective_root"] == [str(legacy_live2d_dir.resolve())]
    assert payload["runtime_entries"]["live2d"]["notes"] == ["windows_cfa_fallback_read_enabled"]


@pytest.mark.unit
def test_storage_location_pick_directory_reports_cancelled_selection(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch.object(
        storage_location_router_module,
        "_pick_storage_location_directory",
        side_effect=storage_location_router_module._DirectoryPickerCancelled(),
    ):
        with _build_client(config_manager) as client:
            response = client.post(
                "/api/storage/location/pick-directory",
                json={"start_path": str(tmp_path)},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cancelled"] is True
    assert payload["selected_root"] == ""


@pytest.mark.unit
def test_storage_location_pick_directory_uses_windows_native_picker(tmp_path):
    with patch.object(storage_location_router_module.sys, "platform", "win32"):
        with patch.object(
            storage_location_router_module,
            "_pick_directory_via_powershell",
            return_value=str((tmp_path / "picked-win").resolve()),
        ) as powershell_picker:
            selected_root = storage_location_router_module._pick_storage_location_directory(start_path=str(tmp_path))

    assert selected_root == str((tmp_path / "picked-win").resolve())
    powershell_picker.assert_called_once()


@pytest.mark.unit
def test_windows_powershell_directory_picker_uses_topmost_owner(tmp_path):
    selected_root = str((tmp_path / "picked-win").resolve())

    with patch.object(
        storage_location_router_module,
        "_resolve_executable_name",
        return_value="powershell.exe",
    ), patch.object(
        storage_location_router_module.shutil,
        "which",
        return_value="powershell.exe",
    ), patch.object(
        storage_location_router_module.subprocess,
        "run",
        return_value=storage_location_router_module.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=selected_root + "\n",
            stderr="",
        ),
    ) as run_mock:
        result = storage_location_router_module._pick_directory_via_powershell(
            start_path=str(tmp_path)
        )

    assert result == selected_root
    command = run_mock.call_args.args[0]
    script = command[-1]
    assert "Add-Type -AssemblyName System.Drawing" in script
    assert "$owner.TopMost = $true" in script
    assert "$owner.Activate()" in script
    assert "$owner.BringToFront()" in script
    assert "[System.Windows.Forms.Application]::DoEvents()" in script
    assert "$result = $dialog.ShowDialog($owner)" in script


@pytest.mark.unit
def test_storage_location_pick_directory_propagates_native_unavailable_on_linux(tmp_path):
    """Linux native dialog 不可用时直接 raise，不再有 tkinter 兜底（项目策略：不带 tk）。"""
    with patch.object(storage_location_router_module.sys, "platform", "linux"):
        with patch.object(
            storage_location_router_module,
            "_pick_directory_via_linux_dialog",
            side_effect=storage_location_router_module._DirectoryPickerUnavailable(
                "directory_picker_unavailable",
                "native picker unavailable",
            ),
        ) as linux_picker:
            with pytest.raises(storage_location_router_module._DirectoryPickerUnavailable):
                storage_location_router_module._pick_storage_location_directory(start_path=str(tmp_path))

    linux_picker.assert_called_once()


@pytest.mark.unit
def test_storage_location_select_same_path_stays_blocked_when_pending_migration_exists(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    save_path = config_manager.app_docs_dir
    create_pending_storage_migration(
        config_manager,
        source_root=save_path,
        target_root=tmp_path / "new-storage" / "N.E.K.O",
        selection_source="recommended",
    )
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(save_path),
                "selection_source": "current",
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_bootstrap_blocking"


@pytest.mark.unit
def test_storage_location_restart_persists_checkpoint_and_requests_shutdown(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    shutdown_calls = {"count": 0}

    def request_app_shutdown():
        shutdown_calls["count"] += 1

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_initiated"
    assert payload["selected_root"] == str(target_root.resolve())
    assert payload["target_root"] == str(target_root.resolve())
    assert payload["permission_ok"] is True
    assert payload["blocking_error_code"] == ""
    assert shutdown_calls["count"] == 1

    checkpoint_path = get_storage_migration_path(config_manager)
    assert checkpoint_path.is_file()

    migration_payload = load_storage_migration(config_manager)
    assert migration_payload["source_root"] == str(config_manager.app_docs_dir)
    assert migration_payload["target_root"] == str(target_root.resolve())
    root_state = config_manager.load_root_state()
    assert root_state["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert root_state["last_migration_source"] == str(config_manager.app_docs_dir)
    assert "restart_pending:" in root_state["last_migration_result"]


@pytest.mark.unit
def test_storage_location_restart_awaits_async_shutdown_callback(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    shutdown_calls = {"count": 0}

    async def request_app_shutdown():
        await asyncio.sleep(0)
        shutdown_calls["count"] += 1

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == "restart_initiated"
    assert shutdown_calls["count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restart_keeps_checkpoint_when_cancelled_after_shutdown_is_accepted(tmp_path):
    """Late cancellation must not erase work already handed to the launcher."""
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    route_task = asyncio.current_task()
    assert route_task is not None
    shutdown_accepted = asyncio.Event()

    async def request_app_shutdown():
        shutdown_accepted.set()
        # The callback succeeds, but cancellation reaches its waiter first.
        asyncio.get_running_loop().call_soon(route_task.cancel)

    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=config_manager,
        request_app_shutdown=request_app_shutdown,
    )
    payload = storage_location_router_module.StorageLocationSelectionRequest(
        selected_root=str(target_root),
        selection_source="recommended",
    )

    with pytest.raises(asyncio.CancelledError):
        await storage_location_router_module._post_storage_location_restart_locked(
            payload,
            Response(),
        )

    assert shutdown_accepted.is_set()
    migration_payload = load_storage_migration(config_manager)
    assert migration_payload["target_root"] == str(target_root.resolve())
    assert config_manager.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY


@pytest.mark.unit
def test_storage_location_restart_restores_previous_migration_when_shutdown_fails(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    previous_migration = save_storage_migration(
        config_manager,
        {
            "version": 1,
            "status": "completed",
            "source_root": str(tmp_path / "old-source" / "N.E.K.O"),
            "target_root": str(config_manager.app_docs_dir),
            "selection_source": "custom",
            "backup_root": str(tmp_path / "old-source" / "N.E.K.O"),
            "retained_source_root": str(tmp_path / "old-source" / "N.E.K.O"),
            "retained_source_mode": "manual_retention",
        },
    )
    previous_root_state = config_manager.load_root_state()

    def request_app_shutdown():
        raise RuntimeError("shutdown failed")

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 500
    assert response.json()["error_code"] == "restart_schedule_failed"
    assert load_storage_migration(config_manager) == previous_migration
    assert config_manager.load_root_state() == previous_root_state


@pytest.mark.unit
def test_restart_rollback_never_deletes_existing_checkpoint_after_restore_failure(
    tmp_path,
):
    config_manager = _DummyConfigManager(tmp_path)
    previous_migration = {"status": "completed", "target_root": "kept"}

    with patch.object(
        storage_location_router_module,
        "_restore_state_file_from_preimage",
        side_effect=OSError("restore failed"),
    ), patch.object(
        storage_location_router_module,
        "delete_storage_migration",
    ) as delete_migration, patch.object(
        storage_location_router_module.logger,
        "exception",
    ) as log_exception:
        with pytest.raises(storage_location_router_module._StorageRollbackPartialError):
            storage_location_router_module._restore_storage_mutation_state(
                config_manager,
                {"include_policy": False, "migration_preimage": {"existed": True, "bytes": json.dumps(previous_migration).encode()}, "root_state": {"mode": "deferred_init"}},
                anchor_root=config_manager.anchor_root,
            )

    delete_migration.assert_not_called()
    log_exception.assert_called_once()
    assert config_manager.load_root_state() == {"mode": "deferred_init"}


@pytest.mark.unit
def test_storage_location_restart_rejects_existing_pending_migration(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="recommended",
    )
    shutdown_calls = {"count": 0}

    def request_app_shutdown():
        shutdown_calls["count"] += 1

    with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(tmp_path / "other-storage" / "N.E.K.O"),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "migration_already_pending"
    assert shutdown_calls["count"] == 0
    assert load_storage_migration(config_manager)["target_root"] == str(target_root.resolve())


@pytest.mark.unit
def test_storage_location_status_reports_pending_checkpoint_as_maintenance(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=tmp_path / "new-storage" / "N.E.K.O",
        selection_source="recommended",
    )
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager) as client:
        response = client.get("/api/storage/location/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ready"] is False
    assert payload["lifecycle_state"] == "maintenance"
    assert payload["blocking_reason"] == "migration_pending"
    assert payload["migration_stage"] == "pending"
    assert payload["poll_interval_ms"] == 1200
    assert payload["storage"]["migration_pending"] is True


@pytest.mark.unit
def test_storage_location_select_recovery_switch_to_recommended_root_resolves_current_session(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_policy_root = unavailable_selected_root
    from utils.storage_policy import save_storage_policy

    save_storage_policy(
        config_manager,
        selected_root=save_policy_root,
        selection_source="custom",
    )
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(reloaded_manager.anchor_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "continue_current_session"
    assert payload["selected_root"] == str(reloaded_manager.anchor_root)

    policy_payload = load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root)
    assert policy_payload["selected_root"] == str(reloaded_manager.anchor_root)
    assert reloaded_manager.load_root_state()["mode"] == "normal"


@pytest.mark.unit
def test_storage_location_select_current_root_recovers_failed_migration_checkpoint(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    target_root = tmp_path / "target-not-empty" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    save_storage_migration(
        config_manager,
        {
            "status": "failed",
            "source_root": str(config_manager.app_docs_dir),
            "target_root": str(target_root),
            "selection_source": "custom",
            "error_code": "target_not_empty",
            "error_message": "目标路径已经包含现有数据，为避免覆盖，本次迁移已停止。",
        },
    )
    config_manager.save_root_state({
        "mode": "deferred_init",
        "current_root": str(config_manager.app_docs_dir),
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "failed:target_not_empty",
        "last_migration_source": str(config_manager.app_docs_dir),
    })
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "continue_current_session"
    assert payload["selected_root"] == str(config_manager.app_docs_dir)
    assert load_storage_migration(config_manager) is None

    policy_payload = load_storage_policy(config_manager, anchor_root=config_manager.anchor_root)
    assert policy_payload["selected_root"] == str(config_manager.app_docs_dir)
    root_state = config_manager.load_root_state()
    assert root_state["mode"] == "normal"
    assert root_state["last_migration_result"] == "recovered:failed_migration:target_not_empty"


@pytest.mark.unit
def test_storage_location_select_current_root_rolls_back_when_recovery_write_fails(tmp_path, monkeypatch):
    """A failed write while recovering a failed migration checkpoint must roll back and return the new error code.

    Regression: `_recover_from_failed_migration` is a three-part write (delete
    checkpoint -> write policy -> switch root mode) with no try/except before
    this change. Any failing part -- most typically an unwritable local state
    directory, the standard symptom under a sandbox or anti-ransomware shield --
    left the checkpoint deleted while policy and root mode still said
    deferred_init, and the caller got a bare 500 with no error_code at all.
    """
    config_manager = _make_real_config_manager(tmp_path)
    target_root = tmp_path / "target-not-empty" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    save_storage_migration(
        config_manager,
        {
            "status": "failed",
            "source_root": str(config_manager.app_docs_dir),
            "target_root": str(target_root),
            "selection_source": "custom",
            "error_code": "target_not_empty",
            "error_message": "目标路径已经包含现有数据，为避免覆盖，本次迁移已停止。",
        },
    )
    config_manager.save_root_state({
        "mode": "deferred_init",
        "current_root": str(config_manager.app_docs_dir),
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "failed:target_not_empty",
        "last_migration_source": str(config_manager.app_docs_dir),
    })
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    # pre-image：回滚后这三份状态必须逐一还原
    previous_migration = load_storage_migration(config_manager)
    previous_policy = load_storage_policy(config_manager, anchor_root=config_manager.anchor_root)
    previous_root_state = config_manager.load_root_state()

    # 让三段写里的第二段（save_storage_policy）失败 —— 此时检查点已经被删掉了。
    # 打桩只影响路由模块自己的名字：bootstrap 阶段不调用 save_storage_policy，
    # 而回滚走的是 _restore_storage_mutation_state（直接 atomic_write_json 与
    # save_storage_migration），不会被这个桩误伤。
    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_policy_write_failed"

    # 回滚把被删掉的检查点、没写成的策略、没切的 root mode 全部还原
    assert load_storage_migration(config_manager) == previous_migration
    assert load_storage_policy(config_manager, anchor_root=config_manager.anchor_root) == previous_policy
    assert config_manager.load_root_state() == previous_root_state


@pytest.mark.unit
def test_storage_location_select_unavailable_selected_root_rolls_back_when_recovery_write_fails(
    tmp_path,
    monkeypatch,
):
    """A failed write while recovering an unavailable previous root must roll back and return the new error code.

    Regression: `_recover_from_unavailable_selected_root` is a two-part write
    (write policy -> switch root mode) that was called bare, with no try/except,
    before this change. Any write failure -- again typically an unwritable local
    state directory -- handed the caller a bare 500 with no error_code, leaving
    the frontend on its fallback "failed to submit the storage location
    selection, please retry later" string.
    """
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    # pre-image：回滚后策略与 root mode 必须逐一还原
    previous_policy = load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root)
    previous_root_state = reloaded_manager.load_root_state()

    # 打桩只影响路由模块自己的名字：bootstrap 阶段不调用 save_storage_policy，而回滚走的是
    # _restore_storage_mutation_state（直接 atomic_write_json 与 save_storage_migration），
    # 不会被这个桩误伤。
    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(reloaded_manager.anchor_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_policy_write_failed"

    assert load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root) == previous_policy
    assert reloaded_manager.load_root_state() == previous_root_state


@pytest.mark.unit
def test_storage_location_select_current_root_rolls_back_when_persist_write_fails(tmp_path, monkeypatch):
    """A failed write in the plain-persistence branch must roll back and return the new error code.

    Regression: `_persist_current_root_selection` was called bare before this
    change, so a write failure escaped straight out of the route -- the caller
    could not even get a 500 body -- and the frontend showed only its fallback
    string. With the shared helper it must return a 500 carrying an error_code
    and leave no half-written policy file behind.
    """
    config_manager = _DummyConfigManager(tmp_path)
    previous_policy = load_storage_policy(config_manager)
    previous_root_state = config_manager.load_root_state()

    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_policy_write_failed"
    # 响应体只给稳定文案，底层异常（含绝对路径）必须留在服务端日志里
    assert "[Errno 13]" not in payload["error"]

    # 写入一行都没落地，回滚后策略文件仍应不存在，root_state 保持原样
    assert not get_storage_policy_path(config_manager).exists()
    assert load_storage_policy(config_manager) == previous_policy
    assert config_manager.load_root_state() == previous_root_state


def _route_anchor_root(config_manager):
    """Compute anchor_root the way the route does, so assertions target the path the endpoint really uses."""
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    return compute_anchor_root(config_manager, current_root=current_root)


@pytest.mark.unit
def test_storage_location_select_keeps_corrupt_policy_bytes_after_rollback(tmp_path, monkeypatch):
    """A corrupt-but-present policy file must be replayed byte-for-byte by the rollback, never deleted.

    Regression: ``load_storage_policy`` folds both "read failed" and "file does
    not exist" into None, so ``_restore_storage_mutation_state`` unlinked the
    corrupt file and still reported "restored the previous state" -- the user
    lost a state file a human could have salvaged. With a byte pre-image the
    rollback only copies the bytes back.
    """
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager, anchor_root=_route_anchor_root(config_manager))
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b'{"selected_root": "D:\\\\half-writ'  # 写到一半被截断的 JSON
    policy_path.write_bytes(corrupt_bytes)

    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["error_code"] == "storage_policy_write_failed"
    # 盘上必须还是那份损坏内容：旧实现会把它 unlink 掉
    assert policy_path.read_bytes() == corrupt_bytes


@pytest.mark.unit
def test_storage_location_select_keeps_corrupt_migration_bytes_after_rollback(tmp_path, monkeypatch):
    """A corrupt migration checkpoint must likewise be replayed byte-for-byte, not deleted by the rollback."""
    config_manager = _DummyConfigManager(tmp_path)
    migration_path = get_storage_migration_path(config_manager, anchor_root=_route_anchor_root(config_manager))
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b'{"status": "copying"'  # 截断的检查点
    migration_path.write_bytes(corrupt_bytes)

    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    assert response.json()["error_code"] == "storage_policy_write_failed"
    assert migration_path.read_bytes() == corrupt_bytes


@pytest.mark.unit
def test_storage_location_select_reports_unreadable_policy_state(tmp_path, monkeypatch):
    """A state file that exists but cannot be read must switch error codes and write nothing at all.

    The read failure is real, not monkeypatched: the path is actually a
    directory (Windows raises PermissionError, POSIX raises IsADirectoryError,
    both OSError).

    Regression: the old implementation read this as None (i.e. "file does not
    exist") and the rollback unlinked the file. With a byte pre-image the run
    stops during the snapshot phase: write() never runs a line, and nothing
    touches the file.
    """
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager, anchor_root=_route_anchor_root(config_manager))
    policy_path.mkdir(parents=True, exist_ok=True)

    write_calls = []

    def _record_policy_write(*args, **kwargs):
        write_calls.append(args)

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _record_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_state_unreadable"
    # 响应体只给稳定文案，底层异常（含绝对路径）必须留在服务端日志里
    assert "[Errno" not in payload["error"]
    # 快照阶段就失败：写入闭包一行都没跑，那个路径（目录）也还留在原地
    assert write_calls == []
    assert policy_path.is_dir()


@pytest.mark.unit
def test_storage_location_select_policy_only_ignores_unreadable_migration_state(tmp_path, monkeypatch):
    """A policy-only write must not snapshot or restore an untouched checkpoint."""
    config_manager = _DummyConfigManager(tmp_path)
    migration_path = get_storage_migration_path(config_manager, anchor_root=_route_anchor_root(config_manager))
    migration_path.mkdir(parents=True, exist_ok=True)

    write_calls = []
    real_save_policy = storage_location_router_module.save_storage_policy

    def _record_policy_write(*args, **kwargs):
        write_calls.append(args)
        return real_save_policy(*args, **kwargs)

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _record_policy_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert len(write_calls) == 1
    assert migration_path.is_dir()


@pytest.mark.unit
def test_storage_location_select_reports_recovered_when_rollback_has_nothing_to_write(tmp_path, monkeypatch):
    """When not a single write succeeded, the rollback must not write and must not claim "could not restore".

    Regression: the old implementation could only choose between "rollback
    raised" and "rollback succeeded". When the very first write was denied the
    disk still held the pre-image -- nothing needed writing -- yet the rollback
    rewrote the snapshot and was rejected by the same unwritable directory, so
    it wrongly reported "could not restore the previous state" and sent the user
    to fix a directory that was never broken. With "skip the write when the disk
    already equals the pre-image", a rollback that does not write cannot fail
    and truthfully reports a restore.
    """
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = _route_anchor_root(config_manager)
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    original_policy = b'{"version": 1, "selected_root": "D:/N.E.K.O"}'
    policy_path.write_bytes(original_policy)

    byte_write_calls: list = []

    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    def _record_byte_write(path, *args, **kwargs):
        # 回滚一旦真的去写就会被记下来；跳过的实现下这里应该一次都不被调用
        byte_write_calls.append(path)
        raise PermissionError(13, "Permission denied")

    # 前向写策略被拒（写入的第一步就失败）；回滚若真去写，同一个不可写目录也会拒绝
    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)
    monkeypatch.setattr(file_utils_module, "atomic_write_bytes", _record_byte_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    # 盘上一字未改，回滚确实成功了，文案必须如实说「已恢复」，而不是「未能恢复」
    assert payload["error_code"] == "storage_policy_write_failed"
    assert "已恢复原有状态" in payload["error"]
    # 跳过生效：回滚没有尝试任何写入
    assert byte_write_calls == []
    # 盘上还是那份 pre-image
    assert policy_path.read_bytes() == original_policy


@pytest.mark.unit
def test_storage_location_select_reports_rollback_failure_when_rollback_write_also_fails(tmp_path, monkeypatch):
    """A failed forward write followed by a failed rollback must return an error code that does not claim a restore.

    Regression: the rollback has to write back into the same unwritable
    directory. The failed-migration branch is the ready-made example -- the
    checkpoint was deleted by ``delete_storage_migration``, the rollback must
    write it back, and that is denied too. Before this change only a log line
    was emitted while the response still said
    ``storage_policy_write_failed`` / "restored the previous state", so the user
    would simply retry while the disk actually sat in a half-state. A failed
    rollback therefore needs its own error code that promises no restore.
    """
    config_manager = _make_real_config_manager(tmp_path)
    target_root = tmp_path / "target-not-empty" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    save_storage_migration(
        config_manager,
        {
            "status": "failed",
            "source_root": str(config_manager.app_docs_dir),
            "target_root": str(target_root),
            "selection_source": "custom",
            "error_code": "target_not_empty",
            "error_message": "目标路径已经包含现有数据，为避免覆盖，本次迁移已停止。",
        },
    )
    config_manager.save_root_state({
        "mode": "deferred_init",
        "current_root": str(config_manager.app_docs_dir),
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "failed:target_not_empty",
        "last_migration_source": str(config_manager.app_docs_dir),
    })
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    previous_migration = load_storage_migration(config_manager)

    def _deny_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    # 双重失败：前向写策略被拒；回滚改成按 pre-image 原始字节还原之后，要构造"回滚也
    # 写不进去"就得拦那条真实落盘路径（atomic_write_bytes），拦 save_storage_migration
    # 已经拦不住它了（回滚不再经过 save_* 这两个 helper）。
    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_write)
    monkeypatch.setattr(file_utils_module, "atomic_write_bytes", _deny_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    # 回滚失败时不能沿用那个声称「已恢复原有状态」的错误码
    assert payload["error_code"] == "storage_policy_rollback_failed"
    # 响应体只给稳定文案，底层异常（含绝对路径）必须留在服务端日志里
    assert "[Errno 13]" not in payload["error"]

    # 回滚确实没成功：检查点没被写回去，盘上并没有回到 pre-image
    assert previous_migration
    assert not load_storage_migration(config_manager)


@pytest.mark.unit
def test_storage_location_select_reports_snapshot_failure_without_claiming_restore(tmp_path, monkeypatch):
    """A snapshot that was never taken must return an error code that does not claim a restore, and must never run the rollback.

    Regression: when ``_snapshot_storage_mutation_state`` fails to read
    root_state before the writes, ``snapshot_out`` stays empty and the write
    closure never runs at all (snapshot and writes share one job, snapshot
    first). Before this change that path fell into
    ``storage_policy_write_failed`` / "restored the previous state" -- there was
    no restore action and the disk contents were never confirmed; reaching here
    means root_state could not be read, whereas the successful-rollback path has
    positive evidence (the pre-image was written back). It needs its own stable
    message.

    An empty snapshot must also never trigger the rollback:
    ``_restore_storage_mutation_state`` would read "no migration / policy key"
    as "those two files never existed", delete the checkpoint and unlink the
    policy file, destroying files that were perfectly fine.
    """
    config_manager = _DummyConfigManager(tmp_path)

    def _deny_snapshot(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    rollback_calls = []

    def _record_rollback(*args, **kwargs):
        rollback_calls.append(args)

    monkeypatch.setattr(
        storage_location_router_module,
        "_snapshot_storage_mutation_state",
        _deny_snapshot,
    )
    monkeypatch.setattr(
        storage_location_router_module,
        "_restore_storage_mutation_state",
        _record_rollback,
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "storage_operation_failed"
    # 响应体只给稳定文案，底层异常（含绝对路径）必须留在服务端日志里
    assert "[Errno 13]" not in payload["error"]
    # 快照都没取成，绝不能跑回滚
    assert rollback_calls == []


@pytest.mark.unit
def test_storage_location_select_rollback_is_best_effort(tmp_path, monkeypatch):
    """When one rollback step fails, the remaining steps must still be attempted -- no fail-fast.

    Regression: the old implementation went migration -> policy -> root_state and
    raised on the first failure, so the other two files were never even
    attempted. Best-effort runs all three, collects the failures and raises an
    aggregate at the end; even when the migration restore fails, policy and
    root_state must still be pushed back to their pre-images.

    Uses the ``recovered`` branch: ``write()`` first calls
    ``delete_storage_migration``, then writes policy and root_state before failing, so the
    rollback has to write the migration checkpoint back -- this test denies that
    byte write to make step one fail, and checks policy and root_state are still
    restored.
    """
    config_manager = _make_real_config_manager(tmp_path)
    target_root = tmp_path / "target-not-empty" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    save_storage_migration(
        config_manager,
        {
            "status": "failed",
            "source_root": str(config_manager.app_docs_dir),
            "target_root": str(target_root),
            "selection_source": "custom",
            "error_code": "target_not_empty",
            "error_message": "目标路径已经包含现有数据，为避免覆盖，本次迁移已停止。",
        },
    )
    config_manager.save_root_state({
        "mode": "deferred_init",
        "current_root": str(config_manager.app_docs_dir),
        "last_known_good_root": str(config_manager.app_docs_dir),
        "last_migration_result": "failed:target_not_empty",
        "last_migration_source": str(config_manager.app_docs_dir),
    })
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    anchor_root = _route_anchor_root(config_manager)
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    original_policy = policy_path.read_bytes() if policy_path.exists() else None

    # policy 和 root_state 必须真的改变，再让最后一步失败。
    real_set_root_mode = storage_location_router_module.set_root_mode

    def _set_root_mode_then_fail(*args, **kwargs):
        real_set_root_mode(*args, **kwargs)
        raise PermissionError(13, "Permission denied")

    # 回滚时：migration 的字节写被拒（第一步失败），policy 路径放行
    def _selective_byte_write(path, data, *args, **kwargs):
        if Path(path).name == "storage_migration.json":
            raise PermissionError(13, "Permission denied")
        Path(path).write_bytes(data)

    monkeypatch.setattr(storage_location_router_module, "set_root_mode", _set_root_mode_then_fail)
    monkeypatch.setattr(file_utils_module, "atomic_write_bytes", _selective_byte_write)

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    # migration 还原失败 → 回滚部分失败 → 不能声称「已恢复」
    assert payload["error_code"] == "storage_policy_rollback_failed"
    # migration 没被还原（前向写入删了它，回滚写不回去），文件应不存在
    assert not migration_path.exists()
    # policy 已被还原回 pre-image（best-effort 第二步成功）
    if original_policy is not None:
        assert policy_path.read_bytes() == original_policy
    else:
        assert not policy_path.exists()
    # root_state 也被还原回 pre-image（best-effort 第三步成功）
    assert config_manager.load_root_state().get("mode") == "deferred_init"


@pytest.mark.unit
def test_storage_location_select_rollback_runs_inside_same_transaction(tmp_path, monkeypatch):
    """A failing write() must be rolled back inside the same root_state_transaction().

    Regression: the old implementation submitted the rollback as a second job
    that re-acquired the lock; the window between the two transactions let a
    third party write a new root_state that the old snapshot then overwrote in
    full. Verified by ``_run_locked_storage_job`` being called exactly once (the
    forward job) with no second rollback job.
    """
    config_manager = _DummyConfigManager(tmp_path)

    # 前向写入失败
    def _deny_policy_write(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(storage_location_router_module, "save_storage_policy", _deny_policy_write)

    # 记录 _run_locked_storage_job 被调用的次数
    job_calls = []
    original_run_locked = storage_location_router_module._run_locked_storage_job

    async def _counting_run_locked(job):
        job_calls.append(job)
        return await original_run_locked(job)

    monkeypatch.setattr(
        storage_location_router_module,
        "_run_locked_storage_job",
        _counting_run_locked,
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    # 正向写入阶段只调用一次 _run_locked_storage_job（快照 + 写入 + 同事务回滚），
    # 没有第二次回滚 job。bootstrap 也会调一次，所以这里只统计写入阶段的 _job。
    write_jobs = [j for j in job_calls if "_apply_storage_mutation_writes" in getattr(j, "__qualname__", "")]
    assert len(write_jobs) == 1
    # 没有任何 _restore_storage_mutation_state 的单独 job 调用
    rollback_jobs = [
        j for j in job_calls
        if getattr(j, "func", None) is storage_location_router_module._restore_storage_mutation_state
    ]
    assert rollback_jobs == []


@pytest.mark.unit
def test_storage_location_select_rolls_back_when_cancelled_after_writes_landed(tmp_path, monkeypatch):
    """A cancellation that lands after the writes succeeded must still roll all three files back.

    Regression: the cancellation branch used to skip the rollback whenever
    ``_write_outcome`` was "success", on the theory that a landed write should
    not be undone. But cancelling means the startup barrier is never released
    (the route never reaches ``_release_storage_startup_barrier_or_rollback``),
    so keeping the write leaves the disk saying "location chosen" while the
    session is still locked behind the barrier. The frontend overlay stops
    appearing and a page reload cannot recover -- only an app restart can. The
    write and the barrier release are one operation, so a half-done operation
    must go back to its pre-image.
    """
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = _route_anchor_root(config_manager)

    # 三个状态文件在盘上都先有一份「改动前」的内容，这样回滚是「改回去」而不是「删掉」
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    original_policy = b'{"version": 1, "selected_root": "D:/N.E.K.O"}'
    policy_path.write_bytes(original_policy)
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    original_migration = b'{"version": 1, "status": "completed"}'
    migration_path.write_bytes(original_migration)
    original_root_state = config_manager.load_root_state()

    # 模拟 _run_locked_storage_job 的既定行为：先等 worker 跑到终态（三步已落盘），
    # 再把取消送达。只在正向 job 之后抛一次，回滚 job 仍要能正常跑完。
    cancel_delivered = {"done": False}
    original_run_locked = storage_location_router_module._run_locked_storage_job

    async def _cancel_after_forward_job(job):
        result = await original_run_locked(job)
        if not cancel_delivered["done"]:
            cancel_delivered["done"] = True
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(
        storage_location_router_module,
        "_run_locked_storage_job",
        _cancel_after_forward_job,
    )

    def _write():
        # 三步写入全部成功，这正是「写已落盘、屏障却没解除」的前提
        save_storage_policy(
            config_manager,
            selected_root=config_manager.app_docs_dir,
            selection_source="current",
            anchor_root=anchor_root,
        )
        storage_location_router_module.set_root_mode(
            config_manager,
            storage_location_router_module.ROOT_MODE_NORMAL,
            current_root=str(config_manager.app_docs_dir),
            last_known_good_root=str(config_manager.app_docs_dir),
        )
        return load_storage_policy(config_manager, anchor_root=anchor_root)

    state_snapshot: dict = {}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            storage_location_router_module._apply_storage_mutation_writes_or_rollback(
                config_manager,
                anchor_root=anchor_root,
                snapshot_out=state_snapshot,
                write=_write,
            )
        )

    # 前置条件：写入确实完整落盘过，否则这个用例证明不了「取消了也要回滚」
    assert state_snapshot["_write_outcome"] == "success"
    # 取消路径必须把三个文件都退回 pre-image，不能留着「盘上已选好、屏障却还锁着」
    assert policy_path.read_bytes() == original_policy
    assert migration_path.read_bytes() == original_migration
    assert config_manager.load_root_state() == original_root_state


@pytest.mark.unit
def test_storage_location_restart_rebinds_original_root_without_creating_migration_checkpoint(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    from utils.storage_policy import save_storage_policy

    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )
    reloaded_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root.mkdir(parents=True, exist_ok=True)
    shutdown_calls = {"count": 0}
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    def request_app_shutdown():
        shutdown_calls["count"] += 1

    with _build_client(reloaded_manager, request_app_shutdown=request_app_shutdown) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(unavailable_selected_root),
                "selection_source": "current",
            },
        )
        restart_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(unavailable_selected_root),
                "selection_source": "current",
            },
        )

    assert select_response.status_code == 200
    select_payload = select_response.json()
    assert select_payload["result"] == "restart_required"
    assert select_payload["restart_mode"] == "rebind_only"
    assert select_payload["estimated_required_bytes"] == 0

    assert restart_response.status_code == 200
    restart_payload = restart_response.json()
    assert restart_payload["result"] == "restart_initiated"
    assert restart_payload["restart_mode"] == "rebind_only"
    assert shutdown_calls["count"] == 1
    assert not get_storage_migration_path(reloaded_manager).exists()
    assert reloaded_manager.load_root_state()["last_migration_result"].startswith("restart_rebind:")


@pytest.mark.unit
@pytest.mark.parametrize("failure_stage", ["shutdown", "write", "snapshot", "rollback", "write_rollback"])
def test_storage_location_restart_rebind_rolls_back_state_when_shutdown_fails(tmp_path, monkeypatch, failure_stage):
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    from utils.storage_policy import save_storage_policy

    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )
    reloaded_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root.mkdir(parents=True, exist_ok=True)
    previous_policy = load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root)
    previous_root_state = reloaded_manager.load_root_state()
    restores = []
    real_restore = storage_location_router_module._restore_storage_mutation_state
    def restore(*args, **kwargs):
        restores.append(1)
        if failure_stage in {"rollback", "write_rollback"}:
            raise RuntimeError("private-restore-worker-error")
        return real_restore(*args, **kwargs)
    monkeypatch.setattr(storage_location_router_module, "_restore_storage_mutation_state", restore)
    if failure_stage in {"write", "write_rollback"}:
        def deny_write(*args, **kwargs):
            raise PermissionError("private-write-path")
        monkeypatch.setattr(storage_location_router_module, "save_storage_policy", deny_write)
    elif failure_stage == "snapshot":
        def deny_snapshot(path):
            raise storage_location_router_module._StorageStateUnreadable(path, PermissionError("private-read-path"))
        monkeypatch.setattr(storage_location_router_module, "_read_state_file_preimage", deny_snapshot)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    def request_app_shutdown():
        raise RuntimeError("shutdown failed")

    with _build_client(reloaded_manager, request_app_shutdown=request_app_shutdown) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(unavailable_selected_root),
                "selection_source": "current",
            },
        )

    assert response.status_code == 500
    payload = response.json()
    assert payload["error_code"] == {
        "shutdown": "restart_schedule_failed", "write": "storage_policy_write_failed",
        "snapshot": "storage_state_unreadable",
        "rollback": "restart_rollback_failed",
        "write_rollback": "restart_rollback_failed",
    }[failure_stage]
    assert "private-" not in payload["error"]
    assert len(restores) == (0 if failure_stage == "snapshot" else 1)
    if failure_stage in {"rollback", "write_rollback"}:
        return
    assert payload["restart_mode"] == "rebind_only"
    assert load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root) == previous_policy
    assert reloaded_manager.load_root_state() == previous_root_state
    assert not get_storage_migration_path(reloaded_manager).exists()


@pytest.mark.unit
def test_storage_location_recovery_keeps_third_path_blocked_after_launcher_exports_anchor_runtime_layout(
    tmp_path,
    monkeypatch,
):
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    from utils.storage_policy import save_storage_policy

    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    recovery_manager = _make_real_config_manager(tmp_path)
    recovery_layout = resolve_storage_layout(recovery_manager)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", recovery_layout["selected_root"])
    monkeypatch.setenv("NEKO_STORAGE_ANCHOR_ROOT", recovery_layout["anchor_root"])
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    third_root = tmp_path / "third-path" / "N.E.K.O"

    with _build_client(reloaded_manager, request_app_shutdown=lambda: None) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(third_root),
                "selection_source": "custom",
            },
        )
        restart_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(third_root),
                "selection_source": "custom",
            },
        )

    assert select_response.status_code == 409
    assert select_response.json()["error_code"] == "recovery_source_unavailable"
    assert restart_response.status_code == 409
    assert restart_response.json()["error_code"] == "recovery_source_unavailable"


@pytest.mark.unit
def test_storage_location_status_exposes_completed_migration_notice(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    atomic_write_json(
        source_root / "config" / "workshop_config.json",
        {
            "default_workshop_folder": str(source_root / "workshop"),
            "user_workshop_folder": str(source_root / "workshop" / "cached"),
            "user_mod_folder": str(tmp_path / "external-mods"),
        },
        ensure_ascii=False,
        indent=2,
    )

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        response = client.get("/api/storage/location/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["migration_stage"] == "completed"
    assert payload["storage"]["legacy_cleanup_pending"] is True
    assert payload["migration"]["retained_source_root"] == str(source_root.resolve())
    assert payload["migration"]["retained_source_mode"] == "manual_retention"
    assert payload["migration"]["completed_at"]
    assert payload["completion_notice"]["completed"] is True
    assert payload["completion_notice"]["source_root"] == str(source_root.resolve())
    assert payload["completion_notice"]["target_root"] == str(target_root.resolve())
    assert payload["completion_notice"]["retained_root"] == str(source_root.resolve())
    assert payload["completion_notice"]["cleanup_available"] is True

    migrated_workshop_config = json.loads((target_root / "config" / "workshop_config.json").read_text(encoding="utf-8"))
    assert migrated_workshop_config["default_workshop_folder"] == str((target_root / "workshop").resolve())
    assert migrated_workshop_config["user_workshop_folder"] == str((target_root / "workshop" / "cached").resolve())
    assert migrated_workshop_config["user_mod_folder"] == str(tmp_path / "external-mods")


@pytest.mark.unit
def test_storage_location_cleanup_retained_source_removes_old_runtime_root(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)

    reloaded_manager = _make_real_config_manager(tmp_path)
    assert source_root.exists()

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
        status_response = client.get("/api/storage/location/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cleaned_root"] == str(source_root.resolve())
    assert not source_root.exists()
    status_payload = status_response.json()
    assert status_payload["storage"]["legacy_cleanup_pending"] is False
    assert status_payload["completion_notice"]["completed"] is False

    migration_payload = load_storage_migration(reloaded_manager)
    assert migration_payload["backup_root"] == ""
    assert migration_payload["retained_source_root"] == ""
    assert migration_payload["retained_source_mode"] == "cleaned"

    root_state = reloaded_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is False
    assert root_state["last_migration_backup"] == ""


@pytest.mark.unit
def test_storage_location_cleanup_rejects_changed_copy_evidence(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (source_root / "config" / "characters.json").write_text("changed!", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    # The changed entry is kept and reported, not deleted.
    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["error_code"] == "retained_source_cleanup_incomplete"
    assert cleanup_response.json()["remaining_entries"] == ["config"]
    assert (source_root / "config" / "characters.json").read_text(encoding="utf-8") == "changed!"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_location_cleanup_allows_a_target_used_since_migration(tmp_path):
    """Running the app on the new root must not make the old copy uncleanable."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (target_root / "config" / "characters.json").write_text("edited in the app", encoding="utf-8")
    (target_root / "config" / "core_config.json").write_text("{}", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert cleanup_response.json()["retained_root_kept"] is False
    assert not source_root.exists()
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "edited in the app"


def _migrate_config_then_replace_target(tmp_path, replace_target):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    shutil.rmtree(target_root / "config")
    replace_target(target_root / "config", tmp_path)

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
    return cleanup_response, source_root


def _migrate_config_and_memory(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    (source_root / "memory").mkdir(parents=True)
    (source_root / "memory" / "recent.json").write_text("[]", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    return source_root, target_root


@pytest.mark.unit
def test_storage_location_cleanup_keeps_an_entry_that_fails_to_delete(tmp_path, monkeypatch):
    """A file locked mid-delete keeps that entry; the others are still cleaned."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    original_remove = storage_location_router_module.remove_runtime_entry

    def _locked_memory(path):
        # Cleanup deletes a private rename of the entry; recognise memory by its file.
        if (Path(path) / "recent.json").exists():
            raise PermissionError(32, "the file is being used by another process")
        return original_remove(path)

    monkeypatch.setattr(storage_location_router_module, "remove_runtime_entry", _locked_memory)
    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409, cleanup_response.json()
    assert cleanup_response.json()["remaining_entries"] == ["memory"]
    assert not (source_root / "config").exists()
    assert (source_root / "memory" / "recent.json").is_file()


@pytest.mark.unit
def test_storage_location_cleanup_keeps_an_entry_that_cannot_be_read(tmp_path, monkeypatch):
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    original_snapshot = storage_location_router_module.snapshot_runtime_entry

    def _unreadable_memory(path):
        # Cleanup compares a private rename of the entry; recognise memory by its file.
        if Path(path).parent == source_root and (Path(path) / "recent.json").exists():
            raise PermissionError(32, "the file is being used by another process")
        return original_snapshot(path)

    monkeypatch.setattr(storage_location_router_module, "snapshot_runtime_entry", _unreadable_memory)
    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409, cleanup_response.json()
    assert cleanup_response.json()["remaining_entries"] == ["memory"]
    assert not (source_root / "config").exists()
    assert (source_root / "memory" / "recent.json").is_file()


@pytest.mark.unit
def test_storage_location_cleanup_removes_read_only_entries(tmp_path):
    """A read-only file (Windows) or directory (POSIX) must not stop cleanup halfway."""
    import stat

    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    frozen_dir = source_root / "config" / "frozen"
    frozen_dir.mkdir(parents=True)
    (source_root / "config" / "a_first.json").write_text("first", encoding="utf-8")
    (frozen_dir / "locked.json").write_text("locked", encoding="utf-8")
    (source_root / "config" / "z_last.json").write_text("last", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (frozen_dir / "locked.json").chmod(stat.S_IREAD)
    if os.name == "posix":
        frozen_dir.chmod(0o555)

    reloaded_manager = _make_real_config_manager(tmp_path)
    try:
        with _build_client(reloaded_manager) as client:
            cleanup_response = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )
    finally:
        if frozen_dir.exists():
            frozen_dir.chmod(0o755)
            (frozen_dir / "locked.json").chmod(stat.S_IREAD | stat.S_IWRITE)

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert not source_root.exists()


@pytest.mark.unit
def test_storage_location_cleanup_removes_regenerable_dirs_from_a_non_anchor_root(tmp_path):
    """Old logs and plugin install records must not keep the old root alive."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (source_root / "logs").mkdir()
    (source_root / "logs" / "old.log").write_text("old", encoding="utf-8")
    (source_root / "plugin-runtime" / "plugin-installs").mkdir(parents=True)
    (source_root / "plugin-runtime" / "plugin-installs" / "task.json").write_text("{}", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert cleanup_response.json()["retained_root_kept"] is False
    assert not source_root.exists()


@pytest.mark.unit
def test_storage_location_cleanup_reports_when_other_files_keep_the_retained_root(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (source_root / "my-notes.txt").write_text("not runtime data", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert cleanup_response.json()["retained_root_kept"] is True
    assert not (source_root / "config").exists()
    assert (source_root / "my-notes.txt").read_text(encoding="utf-8") == "not runtime data"


@pytest.mark.unit
def test_storage_location_cleanup_refuses_a_linked_retained_root(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    # The retained root is swapped for a link to a directory holding the
    # same content, so every per-entry check would pass through the link.
    elsewhere = tmp_path / "elsewhere"
    shutil.move(str(source_root), str(elsewhere))
    try:
        os.symlink(elsewhere, source_root, target_is_directory=True)
    except OSError as exc:
        shutil.move(str(elsewhere), str(source_root))
        pytest.skip(f"symlink creation is unavailable: {exc}")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code != 200
    assert (elsewhere / "config" / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_location_v1_cleanup_accepts_rebased_workshop_config(tmp_path):
    """v1 rewrote workshop paths into the target; that alone must not block cleanup."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    (source_root / "config" / "workshop_config.json").write_text(
        json.dumps({"user_mod_folder": str(source_root / "mods")}),
        encoding="utf-8",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    rebased = json.loads(
        (target_root / "config" / "workshop_config.json").read_text(encoding="utf-8")
    )
    assert rebased["user_mod_folder"] == str((target_root / "mods").resolve())
    _downgrade_to_v1_checkpoint(config_manager)

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert not (source_root / "config").exists()


@pytest.mark.unit
def test_storage_location_v1_cleanup_rebases_a_read_only_workshop_config(tmp_path):
    """The v1 comparison rewrites a scratch copy; a read-only original must not break it."""
    import stat

    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    workshop_config = source_root / "config" / "workshop_config.json"
    workshop_config.write_text(
        json.dumps({"user_mod_folder": str(source_root / "mods")}),
        encoding="utf-8",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)
    workshop_config.chmod(stat.S_IREAD)
    if os.name == "posix":
        (source_root / "config").chmod(0o555)

    reloaded_manager = _make_real_config_manager(tmp_path)
    try:
        with _build_client(reloaded_manager) as client:
            cleanup_response = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )
    finally:
        if (source_root / "config").exists():
            (source_root / "config").chmod(0o755)
            workshop_config.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert not (source_root / "config").exists()


@pytest.mark.unit
def test_storage_location_cleanup_keeps_source_when_target_changed_kind(tmp_path):
    def _replace_with_file(target_entry, _tmp_path):
        target_entry.write_text("not a directory any more", encoding="utf-8")

    cleanup_response, source_root = _migrate_config_then_replace_target(
        tmp_path, _replace_with_file
    )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["remaining_entries"] == ["config"]
    assert (source_root / "config" / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_location_cleanup_keeps_source_when_target_is_a_dangling_link(tmp_path):
    def _replace_with_dangling_link(target_entry, tmp_path):
        try:
            os.symlink(tmp_path / "gone", target_entry, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")

    cleanup_response, source_root = _migrate_config_then_replace_target(
        tmp_path, _replace_with_dangling_link
    )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["remaining_entries"] == ["config"]
    assert (source_root / "config" / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_location_cleanup_reports_unproved_runtime_entries(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (source_root / "memory").mkdir()
    (source_root / "memory" / "unproved.json").write_text("{}", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json() == {
        "ok": False,
        "error_code": "retained_source_cleanup_incomplete",
        "error": "旧数据目录仍含缺少复制证据的运行时条目，已保留供人工确认。",
        "retained_root": str(source_root.resolve()),
        "remaining_entries": ["memory"],
    }
    assert (source_root / "memory" / "unproved.json").is_file()
    migration_payload = load_storage_migration(reloaded_manager)
    assert migration_payload["retained_source_mode"] == "manual_retention"
    assert migration_payload["retained_source_root"] == str(source_root.resolve())


@pytest.mark.unit
@pytest.mark.parametrize("v1_checkpoint", (False, True))
def test_storage_location_cleanup_finishes_after_the_user_removes_the_rest(
    tmp_path, v1_checkpoint
):
    """A reported leftover removed by hand must not turn the retry into a 500."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    if v1_checkpoint:
        _downgrade_to_v1_checkpoint(config_manager)
    (source_root / "memory").mkdir()
    (source_root / "memory" / "unproved.json").write_text("{}", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        first = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
        assert first.status_code == 409
        assert first.json()["remaining_entries"] == ["memory"]
        assert not (source_root / "config").exists()

        shutil.rmtree(source_root / "memory")
        second = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert second.status_code == 200, second.json()
    assert not source_root.exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleaned"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "original"


def _downgrade_to_v1_checkpoint(config_manager) -> None:
    """Rewrite a completed checkpoint as one a pre-evidence (v1) build wrote."""
    migration_payload = dict(load_storage_migration(config_manager))
    migration_payload["version"] = 1
    migration_payload.pop("copied_entries", None)
    save_storage_migration(config_manager, migration_payload)


@pytest.mark.unit
def test_storage_location_cleanup_accepts_a_v1_checkpoint_without_copy_evidence(tmp_path):
    """Migrations completed before copy evidence existed must still be cleanable."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        status_payload = client.get("/api/storage/location/status").json()
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert status_payload["completion_notice"]["cleanup_available"] is True
    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert not source_root.exists()
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_location_v1_cleanup_keeps_entries_the_target_does_not_have(tmp_path):
    """Without evidence, only entries that also exist in the target may go."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)
    (source_root / "memory").mkdir()
    (source_root / "memory" / "only-here.json").write_text("{}", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "only-here.json").is_file()
    assert not (source_root / "config").exists()


@pytest.mark.unit
def test_storage_location_v1_cleanup_keeps_entries_whose_target_content_differs(tmp_path):
    """A same-named target entry is not evidence; its content must match."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    (source_root / "memory").mkdir()
    (source_root / "memory" / "recent.json").write_text("[1, 2, 3]", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)
    # The target's copy is only a leftover: the same name, none of the data.
    (target_root / "memory" / "recent.json").unlink()

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "recent.json").read_text(encoding="utf-8") == "[1, 2, 3]"
    assert not (source_root / "config").exists()


@pytest.mark.unit
def test_storage_location_cleanup_retained_anchor_root_removes_runtime_entries_only(tmp_path):
    config_manager = _make_anchor_root_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (source_root / "memory" / "A").mkdir(parents=True, exist_ok=True)
    (source_root / "memory" / "A" / "recent.json").write_text("[]", encoding="utf-8")
    (source_root / "state").mkdir(parents=True, exist_ok=True)
    (source_root / "state" / "storage_policy.json").write_text("{}", encoding="utf-8")
    (source_root / "cloudsave").mkdir(parents=True, exist_ok=True)
    (source_root / "cloudsave" / "manifest.json").write_text("{}", encoding="utf-8")
    # A run started without the launcher may still write logs here.
    (source_root / "logs").mkdir(parents=True, exist_ok=True)
    (source_root / "logs" / "current.log").write_text("live", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)

    reloaded_manager = _make_anchor_root_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        status_response = client.get("/api/storage/location/status")
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["completion_notice"]["completed"] is True
    assert status_payload["completion_notice"]["retained_root"] == str(source_root.resolve())
    assert status_payload["completion_notice"]["cleanup_available"] is True

    assert cleanup_response.status_code == 200
    cleanup_payload = cleanup_response.json()
    assert cleanup_payload["ok"] is True
    assert cleanup_payload["cleaned_root"] == str(source_root.resolve())
    # The anchor root keeps state and cloud saves; the UI must not claim it is gone.
    assert cleanup_payload["retained_root_kept"] is True

    assert source_root.exists()
    assert not (source_root / "config").exists()
    assert not (source_root / "memory").exists()
    assert (source_root / "state" / "storage_migration.json").exists()
    assert (source_root / "cloudsave" / "manifest.json").read_text(encoding="utf-8") == "{}"
    assert (source_root / "logs" / "current.log").read_text(encoding="utf-8") == "live"

    migration_payload = load_storage_migration(reloaded_manager)
    assert migration_payload["retained_source_root"] == ""
    assert migration_payload["retained_source_mode"] == "cleaned"

    root_state = reloaded_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is False
    assert root_state["last_migration_backup"] == ""


@pytest.mark.unit
def test_storage_location_cleanup_rejects_retained_root_that_contains_target_root(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    retained_root = tmp_path / "retained-root"
    target_root = retained_root / "target-selected" / "N.E.K.O"
    retained_root.mkdir(parents=True, exist_ok=True)
    target_root.mkdir(parents=True, exist_ok=True)
    (retained_root / "config").mkdir(parents=True, exist_ok=True)
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    save_storage_migration(
        config_manager,
        {
            "version": 1,
            "status": "completed",
            "source_root": str(retained_root),
            "target_root": str(target_root),
            "selection_source": "custom",
            "backup_root": str(retained_root),
            "retained_source_root": str(retained_root),
            "retained_source_mode": "manual_retention",
            "completed_at": "2026-04-25T00:00:00Z",
        },
        anchor_root=config_manager.anchor_root,
    )
    config_manager.save_root_state({
        "version": 1,
        "mode": "normal",
        "current_root": str(target_root),
        "last_known_good_root": str(target_root),
        "last_migration_source": str(retained_root),
        "last_migration_backup": str(retained_root),
        "last_migration_result": f"completed:{target_root}",
        "last_successful_boot_at": "",
        "legacy_cleanup_pending": True,
    })

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        status_response = client.get("/api/storage/location/status")
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(retained_root)},
        )

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["completion_notice"]["completed"] is True
    assert status_payload["completion_notice"]["cleanup_available"] is False
    assert cleanup_response.status_code == 404
    assert retained_root.exists()
    assert target_root.exists()
    assert (target_root / "config").exists()
    migration_payload = load_storage_migration(reloaded_manager)
    assert migration_payload["status"] == "completed"


@pytest.mark.unit
@pytest.mark.parametrize("entry_name", ["pngtuber", "watch_together", "runtimes", "embedding_models"])
def test_target_with_only_entries_added_later_counts_as_existing_content(tmp_path, entry_name):
    config_manager = _make_real_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (target_root / entry_name).mkdir(parents=True)
    (target_root / entry_name / "existing.bin").write_bytes(b"existing")

    assert storage_location_router_module._target_root_has_user_content(target_root, config_manager) is True


@pytest.mark.unit
def test_storage_location_v1_cleanup_keeps_entries_v1_never_migrated(tmp_path):
    """v1 copied only its own list; pngtuber in the old root was never copied."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)
    (source_root / "pngtuber" / "Alice").mkdir(parents=True)
    (source_root / "pngtuber" / "Alice" / "idle.png").write_bytes(b"png")
    (source_root / "logs").mkdir()
    (source_root / "logs" / "old.log").write_text("old", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 200, cleanup_response.json()
    assert cleanup_response.json()["retained_root_kept"] is True
    assert (source_root / "pngtuber" / "Alice" / "idle.png").read_bytes() == b"png"
    assert not (source_root / "config").exists()


@pytest.mark.unit
def test_storage_location_cleanup_keeps_regenerable_dirs_while_entries_remain(tmp_path):
    """Old logs stay while the user still has entries to sort out in that root."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    (source_root / "memory").mkdir()
    (source_root / "memory" / "only-here.json").write_text("{}", encoding="utf-8")
    (source_root / "logs").mkdir()
    (source_root / "logs" / "old.log").write_text("old", encoding="utf-8")

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        cleanup_response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup_response.status_code == 409
    assert cleanup_response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "logs" / "old.log").is_file()
    assert not (source_root / "config").exists()


def _cleanup_with_snapshot_hook(tmp_path, monkeypatch, hook):
    source_root, target_root = _migrate_config_and_memory(tmp_path)
    original_snapshot = storage_location_router_module.snapshot_runtime_entry

    def _snapshot(path):
        manifest = original_snapshot(path)
        if Path(path).parent == source_root and (Path(path) / "recent.json").exists():
            return hook(source_root, Path(path), manifest)
        return manifest

    monkeypatch.setattr(storage_location_router_module, "snapshot_runtime_entry", _snapshot)
    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
    return source_root, response


@pytest.mark.unit
def test_storage_location_cleanup_spares_data_written_right_after_the_check(tmp_path, monkeypatch):
    """A writer still using the old path between the check and the delete
    must not lose what it wrote."""

    def _writer_after_check(source_root, _checked_path, manifest):
        (source_root / "memory").mkdir(exist_ok=True)
        (source_root / "memory" / "written-later.json").write_text("keep", encoding="utf-8")
        return manifest

    source_root, response = _cleanup_with_snapshot_hook(tmp_path, monkeypatch, _writer_after_check)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "written-later.json").read_text(encoding="utf-8") == "keep"
    assert not (source_root / "memory" / "recent.json").exists()


@pytest.mark.unit
def test_storage_location_cleanup_reports_a_copy_it_could_not_put_back(tmp_path, monkeypatch):
    """The copy no longer matches and its name was taken meanwhile: both
    stay, the copy under its private name, and both are reported."""

    def _changed_and_name_taken(source_root, _checked_path, _manifest):
        (source_root / "memory").mkdir(exist_ok=True)
        (source_root / "memory" / "written-later.json").write_text("keep", encoding="utf-8")
        return {"kind": "changed"}

    source_root, response = _cleanup_with_snapshot_hook(tmp_path, monkeypatch, _changed_and_name_taken)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    private_copies = list(source_root.glob(".neko-cleanup-memory-*"))
    assert len(private_copies) == 1
    assert (private_copies[0] / "recent.json").is_file()
    assert (source_root / "memory" / "written-later.json").read_text(encoding="utf-8") == "keep"


def _cleanup_request(tmp_path, source_root):
    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        return client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )


@pytest.mark.unit
def test_storage_location_cleanup_puts_back_an_entry_a_stopped_cleanup_left_renamed(tmp_path):
    """A v1 cleanup stopped between renaming memory and putting it back: the
    old copy is the only one of the old content and must come back as memory."""
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("original", encoding="utf-8")
    (source_root / "memory").mkdir()
    (source_root / "memory" / "m.json").write_text("old", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True
    _downgrade_to_v1_checkpoint(config_manager)
    (target_root / "memory" / "m.json").write_text("changed since", encoding="utf-8")
    (source_root / "memory").rename(source_root / ".neko-cleanup-memory-0123456789ab")

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "m.json").read_text(encoding="utf-8") == "old"
    assert not list(source_root.glob(".neko-cleanup-*"))


@pytest.mark.unit
def test_storage_location_cleanup_finishes_an_entry_a_stopped_cleanup_left_renamed(tmp_path):
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    (source_root / "memory").rename(source_root / ".neko-cleanup-memory-0123456789ab")

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 200, response.json()
    assert not source_root.exists()


@pytest.mark.unit
def test_storage_location_cleanup_reports_a_renamed_entry_whose_name_is_taken(tmp_path):
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    leftover = source_root / ".neko-cleanup-memory-0123456789ab"
    (source_root / "memory").rename(leftover)
    (source_root / "memory").mkdir()
    (source_root / "memory" / "newer.json").write_text("newer", encoding="utf-8")

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (leftover / "recent.json").is_file()
    assert (source_root / "memory" / "newer.json").read_text(encoding="utf-8") == "newer"


@pytest.mark.unit
def test_storage_location_cleanup_leaves_links_where_they_are(tmp_path, monkeypatch):
    """A link is never renamed: putting it back could turn it into a hard
    link to whatever it points at."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    original_classify = storage_location_router_module.classify_entry_no_follow

    def _memory_is_a_link(path):
        if Path(path) == source_root / "memory":
            return None
        return original_classify(path)

    monkeypatch.setattr(storage_location_router_module, "classify_entry_no_follow", _memory_is_a_link)

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "recent.json").is_file()
    assert not list(source_root.glob(".neko-cleanup-*"))


@pytest.mark.unit
def test_storage_location_cleanup_keeps_reporting_entries_it_cannot_look_up(tmp_path, monkeypatch):
    """An entry whose lookup fails (ACL, missing execute bit) is not gone;
    reporting it as cleaned would end the cleanup with the data still there."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    blocked = source_root / "memory"
    original_lstat = os.lstat

    def _lstat(path, *args, **kwargs):
        if Path(path) == blocked:
            raise PermissionError(13, "access denied", str(path))
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _lstat)
    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
    monkeypatch.undo()

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (blocked / "recent.json").is_file()



@pytest.mark.unit
def test_storage_location_cleanup_reports_a_renamed_entry_it_cannot_put_back(tmp_path, monkeypatch):
    """Putting the entry back failed (a file in use): it is still reported,
    under its own name, so the cleanup does not end as if it were gone."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    leftover = source_root / ".neko-cleanup-memory-0123456789ab"
    (source_root / "memory").rename(leftover)

    def _in_use(_source, _target):
        raise PermissionError(32, "the file is being used by another process")

    monkeypatch.setattr(storage_location_router_module, "move_entry_without_overwrite", _in_use)

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (leftover / "recent.json").is_file()


@pytest.mark.unit
def test_storage_location_cleanup_records_its_result_when_the_request_is_cancelled(tmp_path, monkeypatch):
    """The client disconnects while the cleanup worker runs: the worker still
    finishes, and what it did must be recorded, or the checkpoint keeps
    pointing at a retained root that is already gone."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)

    async def _worker_finishes_then_request_is_cancelled(job):
        await asyncio.to_thread(job)
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        storage_location_router_module, "_run_locked_storage_job", _worker_finishes_then_request_is_cancelled
    )
    reloaded_manager = _make_real_config_manager(tmp_path)
    try:
        with _build_client(reloaded_manager) as client:
            client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )
    except (asyncio.CancelledError, concurrent.futures.CancelledError):
        pass  # the cancellation surfaces through the test client's portal

    assert not source_root.exists()
    checkpoint = load_storage_migration(_make_real_config_manager(tmp_path))
    assert checkpoint["retained_source_mode"] == "cleaned"



@pytest.mark.unit
def test_storage_location_cleanup_keeps_the_copy_when_the_target_vanishes_meanwhile(tmp_path, monkeypatch):
    """The target went away while the retained copy was being compared: the
    retained copy is then the only one left and must stay."""
    import shutil

    def _target_removed_during_comparison(source_root, _checked_path, manifest):
        target_memory = source_root.parents[1] / "target-selected" / "N.E.K.O" / "memory"
        if target_memory.exists():
            shutil.rmtree(target_memory)
        return manifest

    source_root, response = _cleanup_with_snapshot_hook(tmp_path, monkeypatch, _target_removed_during_comparison)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "recent.json").is_file()



@pytest.mark.unit
def test_storage_location_cleanup_keeps_the_copy_when_the_target_changes_kind_meanwhile(tmp_path, monkeypatch):
    """The target directory was replaced by a file while the retained copy
    was compared: that file is not the copy the evidence describes."""
    import shutil

    def _target_replaced_by_a_file(source_root, _checked_path, manifest):
        target_memory = source_root.parents[1] / "target-selected" / "N.E.K.O" / "memory"
        if target_memory.is_dir():
            shutil.rmtree(target_memory)
            target_memory.write_text("not the copy", encoding="utf-8")
        return manifest

    source_root, response = _cleanup_with_snapshot_hook(tmp_path, monkeypatch, _target_replaced_by_a_file)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "recent.json").is_file()



@pytest.mark.unit
def test_storage_location_cleanup_restores_past_an_empty_reservation(tmp_path):
    """A restore stopped between reserving the name with an empty directory
    and moving the entry in: the entry must still come back."""
    source_root, _target_root = _migrate_config_and_memory(tmp_path)
    leftover = source_root / ".neko-cleanup-memory-0123456789ab"
    (source_root / "memory").rename(leftover)
    (leftover / "recent.json").write_text("changed since", encoding="utf-8")
    (source_root / "memory").mkdir()

    response = _cleanup_request(tmp_path, source_root)

    assert response.status_code == 409, response.json()
    assert response.json()["remaining_entries"] == ["memory"]
    assert (source_root / "memory" / "recent.json").read_text(encoding="utf-8") == "changed since"
    assert not list(source_root.glob(".neko-cleanup-*"))
