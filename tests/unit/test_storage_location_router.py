from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
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
)
from utils.storage_policy import get_storage_policy_path, load_storage_policy


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
@pytest.mark.asyncio
async def test_storage_location_select_same_path_releases_limited_startup_barrier(tmp_path):
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

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cleaned_root"] == str(source_root.resolve())
    assert not source_root.exists()

    migration_payload = load_storage_migration(reloaded_manager)
    assert migration_payload["backup_root"] == ""
    assert migration_payload["retained_source_root"] == ""
    assert migration_payload["retained_source_mode"] == "cleaned"

    root_state = reloaded_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is False
    assert root_state["last_migration_backup"] == ""
