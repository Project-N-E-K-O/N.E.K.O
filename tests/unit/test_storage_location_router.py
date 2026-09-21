import asyncio


import json


import os


import sys


import threading


import time


from pathlib import Path


from unittest.mock import patch


import pytest


from fastapi import FastAPI, Response


from starlette.requests import Request


from fastapi.testclient import TestClient


from main_routers import storage_location_router as storage_location_router_module


from main_routers.shared_state import init_shared_state


from utils.cloudsave_runtime import (
    CLOUDSAVE_DISABLED_ENV,
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
)


from utils import storage_location_bootstrap as storage_location_bootstrap_module


from utils.config_manager import ConfigManager


from utils.storage_layout import resolve_storage_layout


from utils.storage_migration import (
    StorageMigrationError,
    create_pending_storage_migration,
    get_storage_migration_path,
    load_storage_migration,
    replace_storage_migration_if_unchanged,
    run_pending_storage_migration,
    save_storage_migration,
)


from utils.storage_policy import get_storage_policy_path, load_storage_policy, save_storage_policy


from utils.file_utils import atomic_write_json


from config import AUTOSTART_CSRF_TOKEN


from tests.fake_clock import patch_module_clock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_polled_storage_status_builds_off_the_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    observed_threads = []
    config_manager = object()
    monkeypatch.setattr(
        storage_location_router_module,
        "_get_storage_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setattr(
        storage_location_router_module,
        "_build_status_payload",
        lambda observed: observed_threads.append(threading.get_ident()) or {
            "ok": observed is config_manager,
        },
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/storage/location/status",
        "headers": [],
        "query_string": b"",
    })

    payload = await storage_location_router_module.get_storage_location_status(
        Response(),
        request,
    )

    assert payload["ok"] is True
    assert observed_threads and observed_threads[0] != main_thread


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
        logger=None,
        request_app_shutdown=request_app_shutdown,
        release_storage_startup_barrier=release_storage_startup_barrier,
    )
    app = FastAPI()
    app.include_router(storage_location_router_module.router)
    return TestClient(
        app,
        base_url="http://localhost",
        headers={
            "Origin": "http://localhost",
            "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
        },
    )


def _build_mutation_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 12345),
            "path": "/api/storage/location/test",
            "headers": [
                (b"origin", b"http://localhost"),
                (b"x-csrf-token", AUTOSTART_CSRF_TOKEN.encode("utf-8")),
            ],
        }
    )


@pytest.mark.unit
def test_retained_root_cleanup_removes_only_migrated_entries(tmp_path):
    retained_root = tmp_path / "retained"
    (retained_root / "memory").mkdir(parents=True)
    (retained_root / "memory" / "history.json").write_text("{}", encoding="utf-8")
    (retained_root / "community_auth.json").write_text("{}", encoding="utf-8")
    (retained_root / "notes.txt").write_text("keep", encoding="utf-8")

    storage_location_router_module._cleanup_retained_runtime_root(
        retained_root,
        current_root=tmp_path / "current",
        anchor_root=tmp_path / "anchor",
        target_root=tmp_path / "target",
    )

    assert not (retained_root / "memory").exists()
    assert not (retained_root / "community_auth.json").exists()
    assert (retained_root / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert retained_root.is_dir()


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
            storage_location_router_module.post_storage_location_select(
                payload,
                _build_mutation_request(),
                Response(),
            )
        )
        await asyncio.wait_for(first_call_entered.wait(), timeout=1.0)

        restart_task = asyncio.create_task(
            storage_location_router_module.post_storage_location_restart(
                payload,
                _build_mutation_request(),
                Response(),
            )
        )
        cleanup_task = asyncio.create_task(
            storage_location_router_module.post_storage_location_retained_source_cleanup(
                storage_location_router_module.StorageLocationCleanupRequest(),
                _build_mutation_request(),
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
def test_storage_location_restart_rejects_cross_origin_request_before_writes(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "target" / "N.E.K.O"
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        response = client.post(
            "/api/storage/location/restart",
            headers={
                "Origin": "https://attacker.example",
                "X-CSRF-Token": "wrong-token",
            },
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    assert shutdown_calls == []
    assert not get_storage_migration_path(config_manager).exists()


@pytest.mark.unit
def test_storage_location_select_same_path_defers_writes_until_correlated_restart(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )
        select_payload = select_response.json()
        assert select_payload["result"] == "restart_required"
        assert load_storage_policy(config_manager) is None
        assert shutdown_calls == []
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
                "restart_operation_id": select_payload["restart_operation_id"],
            },
        )

    assert select_response.status_code == 200
    assert select_payload["restart_mode"] == "rebind_only"
    assert select_payload["selected_root"] == str(config_manager.app_docs_dir)
    assert response.status_code == 200
    assert response.json()["result"] == "restart_initiated"

    policy_path = get_storage_policy_path(config_manager)
    assert policy_path.is_file()

    policy_payload = load_storage_policy(config_manager)
    assert policy_payload["selected_root"] == str(config_manager.app_docs_dir)
    assert policy_payload["selection_source"] == "user_selected"
    root_state = config_manager.load_root_state()
    assert root_state["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert root_state["last_migration_result"].startswith("restart_rebind:")
    assert shutdown_calls == ["shutdown"]


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
    assert payload["estimated_required_bytes"] == 0
    assert payload["estimated_required_bytes_available"] is False
    assert isinstance(payload["target_free_bytes"], int)
    assert payload["permission_ok"] is True
    assert payload["warning_codes"] == []
    assert payload["blocking_error_code"] == ""
    assert payload["blocking_error_message"] == ""

    assert not get_storage_policy_path(config_manager).exists()


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
def test_storage_location_restart_restores_previous_migration_when_shutdown_fails(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    previous_migration = save_storage_migration(
        config_manager,
        {
            "version": 1,
            "txid": "a" * 32,
            "status": "completed",
            "source_root": str(tmp_path / "old-source" / "N.E.K.O"),
            "target_root": str(config_manager.app_docs_dir),
            "selection_source": "custom",
            "confirmed_existing_target_content": False,
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
def test_storage_location_status_reports_pending_checkpoint_as_maintenance(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    monkeypatch.setattr(storage_location_router_module.config_module, "INSTANCE_ID", "storage-generation")
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
    assert payload["instance_id"] == "storage-generation"
    assert payload["autostart_csrf_token"] == AUTOSTART_CSRF_TOKEN
    assert payload["ready"] is False
    assert payload["lifecycle_state"] == "maintenance"
    assert payload["blocking_reason"] == "migration_pending"
    assert payload["migration_stage"] == "pending"
    assert payload["poll_interval_ms"] == 1200
    assert payload["storage"]["migration_pending"] is True


@pytest.mark.unit
def test_storage_location_status_reports_completed_migration_notice(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    retained_root = tmp_path / "old-storage" / "N.E.K.O"
    (retained_root / "memory").mkdir(parents=True)
    (retained_root / "memory" / "history.json").write_text("{}", encoding="utf-8")
    save_storage_migration(
        config_manager,
        {
            "version": 1,
            "txid": "a" * 32,
            "status": "completed",
            "source_root": str(retained_root),
            "target_root": str(config_manager.app_docs_dir),
            "selection_source": "custom",
            "confirmed_existing_target_content": False,
            "backup_root": str(retained_root),
            "retained_source_root": str(retained_root),
            "retained_source_mode": "manual_retention",
            "completed_at": "2026-09-21T00:00:00Z",
        },
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
    assert payload["completion_notice"] == {
        "completed": True,
        "selection_source": "custom",
        "source_root": str(retained_root),
        "target_root": str(config_manager.app_docs_dir),
        "retained_root": str(retained_root),
        "retained_root_exists": True,
        "cleanup_available": True,
        "completed_at": "2026-09-21T00:00:00Z",
        "message": "存储位置迁移已完成，旧数据目录当前仍保留，需手动清理。",
    }


@pytest.mark.unit
def test_storage_location_rollback_required_is_explicit_and_cannot_be_replaced(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "unfinished-target" / "N.E.K.O"
    replacement_root = tmp_path / "replacement-target" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="recommended",
    )
    checkpoint = load_storage_migration(config_manager)
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        checkpoint["txid"],
    )
    storage_migration_module._write_transaction_owner_marker(
        checkpoint,
        transaction_root,
        checkpoint["txid"],
    )
    save_storage_migration(
        config_manager,
        {
            **checkpoint,
            "status": "rollback_required",
            "transaction_root": str(transaction_root),
            "source_runtime_baseline": storage_migration_module._snapshot_runtime_entries(
                config_manager.app_docs_dir
            ),
            "original_target_entries": [],
            "publish_entry_names": [],
            "publish_entry_snapshots": {},
            "error_code": "rollback_failed",
            "error_message": "mock rollback failure",
        },
    )
    config_manager.save_root_state(
        {
            "mode": "deferred_init",
            "current_root": str(config_manager.app_docs_dir),
            "last_known_good_root": str(config_manager.app_docs_dir),
            "last_migration_result": "failed:rollback_failed",
            "last_migration_source": str(config_manager.app_docs_dir),
        }
    )
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        status_response = client.get("/api/storage/location/status")
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(replacement_root),
                "selection_source": "custom",
            },
        )
        restart_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(replacement_root),
                "selection_source": "custom",
            },
        )

    status_payload = status_response.json()
    assert status_payload["ready"] is False
    assert status_payload["lifecycle_state"] == "rollback_required"
    assert status_payload["migration_stage"] == "rollback_required"
    assert status_payload["storage"]["rollback_required"] is True
    assert status_payload["storage"]["migration_pending"] is True
    assert select_response.status_code == 409
    assert select_response.json()["error_code"] == "storage_rollback_required"
    assert restart_response.status_code == 409
    assert restart_response.json()["error_code"] == "storage_rollback_required"
    assert shutdown_calls == []
    assert load_storage_migration(config_manager)["status"] == "rollback_required"


@pytest.mark.unit
def test_storage_location_malformed_policy_blocks_mutations_but_allows_safe_exit(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text('{"selected_root":', encoding="utf-8")
    shutdown_calls = []
    monkeypatch.setattr(storage_location_router_module.config_module, "INSTANCE_ID", "policy-generation")

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        status_response = client.get("/api/storage/location/status")
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(tmp_path / "replacement" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )
        restart_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(tmp_path / "replacement" / "N.E.K.O"),
                "selection_source": "custom",
            },
        )
        exit_response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    status_payload = status_response.json()
    assert status_response.status_code == 200
    assert status_payload["instance_id"] == "policy-generation"
    assert status_payload["ready"] is False
    assert status_payload["lifecycle_state"] == "storage_policy_unavailable"
    assert status_payload["error_code"] == "storage_policy_unavailable"
    assert select_response.status_code == 503
    assert select_response.json()["error_code"] == "storage_policy_unavailable"
    assert restart_response.status_code == 503
    assert restart_response.json()["error_code"] == "storage_policy_unavailable"
    assert exit_response.status_code == 200
    assert exit_response.json()["result"] == "shutdown_initiated"
    assert shutdown_calls == ["shutdown"]
    assert policy_path.read_text(encoding="utf-8") == '{"selected_root":'
    assert not get_storage_migration_path(config_manager).exists()
