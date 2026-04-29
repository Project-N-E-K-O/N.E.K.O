import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from main_routers import storage_location_router as storage_location_router_module
from main_routers.shared_state import init_shared_state
from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY
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
from utils.storage_policy import get_storage_policy_path, load_storage_policy, save_storage_policy
from utils.file_utils import atomic_write_json


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
def test_storage_location_pick_directory_uses_windows_native_picker_first(tmp_path):
    with patch.object(storage_location_router_module.sys, "platform", "win32"):
        with patch.object(
            storage_location_router_module,
            "_pick_directory_via_powershell",
            return_value=str((tmp_path / "picked-win").resolve()),
        ) as powershell_picker:
            with patch.object(storage_location_router_module, "_pick_directory_via_tkinter") as tkinter_picker:
                selected_root = storage_location_router_module._pick_storage_location_directory(start_path=str(tmp_path))

    assert selected_root == str((tmp_path / "picked-win").resolve())
    powershell_picker.assert_called_once()
    tkinter_picker.assert_not_called()


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
def test_storage_location_pick_directory_falls_back_to_tkinter_when_linux_native_dialog_unavailable(tmp_path):
    with patch.object(storage_location_router_module.sys, "platform", "linux"):
        with patch.object(
            storage_location_router_module,
            "_pick_directory_via_linux_dialog",
            side_effect=storage_location_router_module._DirectoryPickerUnavailable(
                "directory_picker_unavailable",
                "native picker unavailable",
            ),
        ) as linux_picker:
            with patch.object(
                storage_location_router_module,
                "_pick_directory_via_tkinter",
                return_value=str((tmp_path / "picked-linux").resolve()),
            ) as tkinter_picker:
                selected_root = storage_location_router_module._pick_storage_location_directory(start_path=str(tmp_path))

    assert selected_root == str((tmp_path / "picked-linux").resolve())
    linux_picker.assert_called_once()
    tkinter_picker.assert_called_once()


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
def test_storage_location_restart_rebind_rolls_back_state_when_shutdown_fails(tmp_path, monkeypatch):
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
    assert payload["error_code"] == "restart_schedule_failed"
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

    assert source_root.exists()
    assert not (source_root / "config").exists()
    assert not (source_root / "memory").exists()
    assert (source_root / "state" / "storage_migration.json").exists()
    assert (source_root / "cloudsave" / "manifest.json").read_text(encoding="utf-8") == "{}"

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
