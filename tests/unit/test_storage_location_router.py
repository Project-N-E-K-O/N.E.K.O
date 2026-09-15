import asyncio
import json
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
def test_storage_location_preflight_rejects_cross_origin_before_write_probe(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    probe_calls = []
    monkeypatch.setattr(
        storage_location_router_module,
        "_build_restart_preflight",
        lambda *_args, **_kwargs: probe_calls.append("probe"),
    )

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/preflight",
            headers={
                "Origin": "https://attacker.example",
                "X-CSRF-Token": "wrong-token",
            },
            json={
                "selected_root": str(tmp_path / "target" / "N.E.K.O"),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    assert probe_calls == []


@pytest.mark.unit
def test_restart_operation_cancel_closes_never_started_unknown_outcome(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "target" / "N.E.K.O"
    shutdown_calls = []
    storage_location_router_module._storage_restart_operations.clear()
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

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        preflight = client.post(
            "/api/storage/location/preflight",
            json={"selected_root": str(target_root), "selection_source": "custom"},
        )
        operation_id = preflight.json()["restart_operation_id"]
        prepared = client.get(
            "/api/storage/location/status",
            params={"restart_operation_id": operation_id},
        ).json()["restart_operation"]
        cancelled = client.post(
            "/api/storage/location/restart/cancel",
            json={"restart_operation_id": operation_id},
        ).json()["restart_operation"]
        rejected_restart = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "custom",
                "restart_operation_id": operation_id,
            },
        )

    assert prepared["state"] == "prepared"
    assert cancelled["state"] == "cancelled"
    assert rejected_restart.status_code == 409
    assert rejected_restart.json()["error_code"] == "restart_operation_cancelled"
    assert shutdown_calls == []
    assert load_storage_migration(config_manager) is None


@pytest.mark.unit
def test_restart_operation_tracks_accepted_response_by_preflight_id(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "target" / "N.E.K.O"
    storage_location_router_module._storage_restart_operations.clear()
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

    with _build_client(config_manager, request_app_shutdown=lambda: None) as client:
        preflight = client.post(
            "/api/storage/location/preflight",
            json={"selected_root": str(target_root), "selection_source": "custom"},
        )
        operation_id = preflight.json()["restart_operation_id"]
        restart = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "custom",
                "restart_operation_id": operation_id,
            },
        )

    assert restart.status_code == 200
    assert restart.json()["result"] == "restart_initiated"
    assert restart.json()["restart_operation"]["state"] == "accepted"
    assert load_storage_migration(config_manager)["status"] == "pending"


@pytest.mark.unit
def test_restart_operation_cancel_cannot_cancel_in_flight_request(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "target" / "N.E.K.O"
    storage_location_router_module._storage_restart_operations.clear()
    operation_id = storage_location_router_module._prepare_storage_restart_operation(target_root)
    assert storage_location_router_module._begin_storage_restart_operation(
        operation_id,
        target_root,
    ) == ""

    with _build_client(config_manager) as client:
        response = client.post(
            "/api/storage/location/restart/cancel",
            json={"restart_operation_id": operation_id},
        )

    assert response.status_code == 200
    assert response.json()["restart_operation"]["state"] == "in_flight"


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
def test_storage_location_select_same_path_never_releases_services_before_phase0(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    release_calls = []
    shutdown_calls = []

    async def release_storage_startup_barrier(*, reason: str):
        release_calls.append(reason)

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
        release_storage_startup_barrier=release_storage_startup_barrier,
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )
        select_payload = select_response.json()
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
                "restart_operation_id": select_payload["restart_operation_id"],
            },
        )

    assert select_response.status_code == 200
    assert select_payload["result"] == "restart_required"
    assert response.status_code == 200
    assert response.json()["result"] == "restart_initiated"
    assert release_calls == []
    assert shutdown_calls == ["shutdown"]


@pytest.mark.unit
def test_storage_location_select_same_path_requires_restart_owner_before_writes(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    previous_root_state = config_manager.load_root_state()

    with _build_client(config_manager) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )

    assert select_response.status_code == 503
    assert select_response.json()["error_code"] == "restart_unavailable"
    assert load_storage_policy(config_manager) is None
    assert config_manager.load_root_state() == previous_root_state


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
def test_runtime_release_failure_keeps_status_blocked_and_allows_safe_exit(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    shutdown_calls = []
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )
    storage_location_bootstrap_module.set_runtime_storage_blocking_reason(
        "startup_release_failed"
    )
    try:
        with _build_client(
            config_manager,
            request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
        ) as client:
            status_response = client.get("/api/storage/location/status")
            exit_response = client.post(
                "/api/storage/location/exit",
                headers={"X-Neko-Storage-Action": "exit"},
            )
    finally:
        storage_location_bootstrap_module.clear_runtime_storage_blocking_reason()

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["ready"] is False
    assert status_payload["lifecycle_state"] == "recovery_required"
    assert status_payload["blocking_reason"] == "startup_release_failed"
    assert status_payload["storage"]["recovery_required"] is True
    assert exit_response.status_code == 200
    assert exit_response.json()["result"] == "shutdown_initiated"
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
def test_storage_location_mutation_routes_reject_cloudsave_disabled_without_root_state_read(monkeypatch, tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    def fail_root_state_read():
        raise AssertionError("cloudsave disabled storage mutation routes should not read root_state")

    config_manager.load_root_state = fail_root_state_read
    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")

    shutdown_calls = []

    with _build_client(config_manager, request_app_shutdown=lambda: shutdown_calls.append("shutdown")) as client:
        exit_response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )
        responses = [
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
    assert exit_response.status_code == 200
    assert exit_response.json()["result"] == "shutdown_initiated"
    assert shutdown_calls == ["shutdown"]


@pytest.mark.unit
def test_storage_location_malformed_migration_checkpoint_allows_safe_exit_without_rewrite(
    tmp_path,
):
    config_manager = _DummyConfigManager(tmp_path)
    migration_path = get_storage_migration_path(config_manager)
    migration_path.parent.mkdir(parents=True, exist_ok=True)
    malformed = '{"status":'
    migration_path.write_text(malformed, encoding="utf-8")
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        bootstrap_response = client.get("/api/storage/location/bootstrap")
        status_response = client.get("/api/storage/location/status")
        response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    bootstrap_payload = bootstrap_response.json()
    status_payload = status_response.json()
    assert bootstrap_response.status_code == 200
    assert bootstrap_payload["lifecycle_state"] == "storage_status_unavailable"
    assert bootstrap_payload["error_code"] == "migration_checkpoint_malformed"
    assert status_response.status_code == 200
    assert status_payload["lifecycle_state"] == "storage_status_unavailable"
    assert status_payload["error_code"] == "migration_checkpoint_malformed"
    assert status_payload["autostart_csrf_token"]
    assert response.status_code == 200
    assert response.json()["result"] == "shutdown_initiated"
    assert shutdown_calls == ["shutdown"]
    assert migration_path.read_text(encoding="utf-8") == malformed


@pytest.mark.unit
@pytest.mark.parametrize(
    "recovery_mode",
    ["storage_status_unavailable", "storage_policy_unavailable"],
)
def test_launcher_unavailable_generation_reports_blocked_and_allows_safe_exit_without_state_reads(
    monkeypatch,
    tmp_path,
    recovery_mode,
):
    config_manager = _DummyConfigManager(tmp_path)
    config_manager.load_root_state = lambda: (_ for _ in ()).throw(
        AssertionError("unavailable generation must not re-read persisted root state")
    )
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", recovery_mode)
    shutdown_calls = []
    target_root = tmp_path / "replacement" / "N.E.K.O"

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        bootstrap_response = client.get("/api/storage/location/bootstrap")
        status_response = client.get("/api/storage/location/status")
        mutation_responses = [
            client.post(
                f"/api/storage/location/{route}",
                json={"selected_root": str(target_root), "selection_source": "custom"},
            )
            for route in ("select", "preflight", "restart")
        ]
        mutation_responses.append(
            client.post("/api/storage/location/retained-source/cleanup", json={})
        )
        exit_response = client.post(
            "/api/storage/location/exit",
            headers={"X-Neko-Storage-Action": "exit"},
        )

    bootstrap_payload = bootstrap_response.json()
    status_payload = status_response.json()
    assert bootstrap_response.status_code == 200
    assert bootstrap_payload["blocking_reason"] == recovery_mode
    assert bootstrap_payload["recovery_action"] == "safe_exit"
    assert status_response.status_code == 200
    assert status_payload["ready"] is False
    assert status_payload["blocking_reason"] == recovery_mode
    assert status_payload["recovery_action"] == "safe_exit"
    assert all(response.status_code == 503 for response in mutation_responses)
    assert all(response.json()["error_code"] == recovery_mode for response in mutation_responses)
    assert exit_response.status_code == 200
    assert exit_response.json()["result"] == "shutdown_initiated"
    assert shutdown_calls == ["shutdown"]


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
def test_storage_location_select_same_path_rolls_back_when_restart_request_fails(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    previous_root_state = config_manager.load_root_state()

    async def request_app_shutdown():
        raise RuntimeError("shutdown failed")

    with _build_client(
        config_manager,
        request_app_shutdown=request_app_shutdown,
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
            },
        )
        select_payload = select_response.json()
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "current",
                "restart_operation_id": select_payload["restart_operation_id"],
            },
        )

    assert select_response.status_code == 200
    assert select_payload["result"] == "restart_required"
    assert response.status_code == 500
    payload = response.json()
    assert payload["error_code"] == "restart_schedule_failed"
    policy = load_storage_policy(config_manager)
    assert policy is None
    assert config_manager.load_root_state() == previous_root_state


@pytest.mark.unit
def test_storage_location_runtime_release_failure_restarts_without_rewriting_policy(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    release_reasons = []
    shutdown_calls = []
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    original_policy = load_storage_policy(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    storage_location_bootstrap_module.set_runtime_storage_blocking_reason(
        "startup_release_failed"
    )

    async def release_storage_startup_barrier(*, reason: str):
        release_reasons.append(reason)

    try:
        with _build_client(
            config_manager,
            request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
            release_storage_startup_barrier=release_storage_startup_barrier,
        ) as client:
            select_response = client.post(
                "/api/storage/location/select",
                json={
                    "selected_root": str(config_manager.app_docs_dir),
                    "selection_source": "current",
                },
            )
            select_payload = select_response.json()
            response = client.post(
                "/api/storage/location/restart",
                json={
                    "selected_root": str(config_manager.app_docs_dir),
                    "selection_source": "current",
                    "restart_operation_id": select_payload["restart_operation_id"],
                },
            )
    finally:
        storage_location_bootstrap_module.clear_runtime_storage_blocking_reason()

    assert select_response.status_code == 200
    assert select_payload["result"] == "restart_required"
    assert response.status_code == 200
    assert response.json()["result"] == "restart_initiated"
    assert release_reasons == []
    assert shutdown_calls == ["shutdown"]
    assert load_storage_policy(
        config_manager,
        anchor_root=config_manager.anchor_root,
    ) == original_policy


@pytest.mark.unit
@pytest.mark.asyncio
async def test_storage_mutation_write_failure_restores_all_persisted_facts(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
        anchor_root=config_manager.anchor_root,
    )
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
        anchor_root=config_manager.anchor_root,
    )
    previous_root_state = config_manager.load_root_state()
    previous_policy = load_storage_policy(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )
    previous_migration = load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    )

    def _partially_write_then_fail():
        storage_location_router_module.delete_storage_migration(
            config_manager,
            anchor_root=config_manager.anchor_root,
        )
        save_storage_policy(
            config_manager,
            selected_root=target_root,
            selection_source="custom",
            anchor_root=config_manager.anchor_root,
        )
        raise OSError("root state write failed")

    snapshot: dict = {}
    with pytest.raises(OSError, match="root state write failed"):
        await storage_location_router_module._apply_storage_mutation_writes(
            config_manager,
            anchor_root=config_manager.anchor_root,
            snapshot_out=snapshot,
            write=_partially_write_then_fail,
        )

    assert config_manager.load_root_state() == previous_root_state
    assert load_storage_policy(
        config_manager,
        anchor_root=config_manager.anchor_root,
    ) == previous_policy
    assert load_storage_migration(
        config_manager,
        anchor_root=config_manager.anchor_root,
    ) == previous_migration


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
def test_storage_location_preflight_requires_safety_margin(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    estimated_bytes = 1024
    monkeypatch.setattr(
        storage_location_router_module.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": estimated_bytes})(),
    )

    payload = storage_location_router_module._build_restart_preflight(
        config_manager.app_docs_dir,
        tmp_path / "target" / "N.E.K.O",
        config_manager=config_manager,
        estimated_required_bytes=estimated_bytes,
    )

    assert payload["safety_margin_bytes"] == 64 * 1024 * 1024
    assert payload["estimated_required_with_margin_bytes"] == estimated_bytes + 64 * 1024 * 1024
    assert payload["blocking_error_code"] == "insufficient_space"


@pytest.mark.unit
def test_storage_location_preflight_blocks_when_free_space_cannot_be_read(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)

    def fail_disk_usage(_path):
        raise OSError("volume unavailable")

    monkeypatch.setattr(storage_location_router_module.shutil, "disk_usage", fail_disk_usage)
    payload = storage_location_router_module._build_restart_preflight(
        config_manager.app_docs_dir,
        tmp_path / "target" / "N.E.K.O",
        config_manager=config_manager,
        estimated_required_bytes=1,
    )

    assert payload["disk_space_available"] is False
    assert payload["blocking_error_code"] == "disk_space_unavailable"


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
        "last_migration_result": "maintenance:test",
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
def test_restart_confirmation_retry_preserves_correlated_operation(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-storage-parent"
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append("shutdown"),
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={"selected_root": str(selected_parent), "selection_source": "custom"},
        )
        selection = select_response.json()
        operation_id = selection["restart_operation_id"]
        selected_root = Path(selection["selected_root"])

        (selected_root / "config").mkdir(parents=True)
        (selected_root / "config" / "characters.json").write_text(
            '{"appeared_after_preflight": true}',
            encoding="utf-8",
        )
        first_restart = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(selected_root),
                "selection_source": "custom",
                "restart_operation_id": operation_id,
            },
        )
        confirmed_restart = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(selected_root),
                "selection_source": "custom",
                "confirm_existing_target_content": True,
                "restart_operation_id": operation_id,
            },
        )

    assert first_restart.status_code == 409
    assert first_restart.json()["error_code"] == "target_confirmation_required"
    assert first_restart.json()["restart_operation"]["state"] == "prepared"
    assert shutdown_calls == ["shutdown"]
    assert confirmed_restart.status_code == 200
    assert confirmed_restart.json()["restart_operation"]["state"] == "accepted"
    assert load_storage_migration(config_manager)["confirmed_existing_target_content"] is True


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
    assert payload["autostart_csrf_token"] == AUTOSTART_CSRF_TOKEN


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
    assert {
        "pngtuber",
        "card_faces",
        "jukebox",
        "game_scores",
        "embedding_models",
        "runtimes",
        "plugin_runtime",
    }.issubset(payload["runtime_entries"])


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
    selected_root = str((tmp_path / "中文-🐈-picked-win").resolve())

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
    assert "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)" in script
    assert "$owner.TopMost = $true" in script
    assert "$owner.Activate()" in script
    assert "$owner.BringToFront()" in script
    assert "[System.Windows.Forms.Application]::DoEvents()" in script
    assert "$result = $dialog.ShowDialog($owner)" in script
    assert run_mock.call_args.kwargs["encoding"] == "utf-8"
    assert run_mock.call_args.kwargs["errors"] == "strict"


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
def test_linux_directory_picker_candidates_share_one_timeout_budget(monkeypatch):
    observed_timeouts = []

    monkeypatch.setattr(
        storage_location_router_module,
        "_resolve_executable_name",
        lambda *_candidates: _candidates[-1],
    )
    monkeypatch.setattr(
        storage_location_router_module.shutil,
        "which",
        lambda candidate: f"/usr/bin/{candidate}",
    )
    monotonic_values = iter((100.0, 100.0, 130.0, 221.0))
    patch_module_clock(
        monkeypatch,
        storage_location_router_module,
        monotonic=lambda: next(monotonic_values),
    )

    def _failed_picker(*_args, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        return storage_location_router_module.subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="failed",
        )

    monkeypatch.setattr(
        storage_location_router_module.subprocess,
        "run",
        _failed_picker,
    )

    with pytest.raises(storage_location_router_module._DirectoryPickerUnavailable):
        storage_location_router_module._pick_directory_via_linux_dialog(start_path="")

    assert observed_timeouts == [120.0, 90.0]


@pytest.mark.unit
def test_completed_notice_skips_owner_probe_when_secure_cleanup_is_unsupported(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    (retained_root / "social_session.json.lock").write_text(
        "unclassified",
        encoding="utf-8",
    )
    observed = []
    real_probe = storage_location_router_module.probe_retained_community_state

    def _probe(path, **kwargs):
        observed.append(kwargs.get("classify_social_lock_process"))
        return real_probe(path, **kwargs)

    monkeypatch.setattr(
        storage_location_router_module,
        "_secure_retained_cleanup_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        storage_location_router_module,
        "probe_retained_community_state",
        _probe,
    )

    notice = storage_location_router_module._build_completed_migration_notice(
        config_manager,
        bootstrap_payload={
            "migration": {
                "status": "completed",
                "source_root": str(retained_root),
                "target_root": str(config_manager.app_docs_dir),
                "retained_source_root": str(retained_root),
                "retained_source_mode": "manual_retention",
            }
        },
    )

    assert notice["completed"] is True
    assert notice["cleanup_available"] is False
    assert observed == [False]


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
def test_storage_location_restart_uses_configured_anchor_instead_of_platform_default(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    configured_anchor_root = tmp_path / "owner-exported-anchor" / "N.E.K.O"
    configured_anchor_root.mkdir(parents=True)
    config_manager.anchor_root = configured_anchor_root
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    with _build_client(config_manager, request_app_shutdown=lambda: None) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )

    assert response.status_code == 200
    configured_checkpoint = get_storage_migration_path(
        config_manager,
        anchor_root=configured_anchor_root,
    )
    platform_default_checkpoint = (
        config_manager._standard_root
        / config_manager.app_name
        / "state"
        / "storage_migration.json"
    )
    assert configured_checkpoint.is_file()
    assert not platform_default_checkpoint.exists()
    migration_payload = load_storage_migration(
        config_manager,
        anchor_root=configured_anchor_root,
    )
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
def test_storage_location_status_marks_accepted_but_still_online_restart_as_awaiting_shutdown(
    tmp_path,
):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    with _build_client(config_manager, request_app_shutdown=lambda: None) as client:
        restart_response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(target_root),
                "selection_source": "recommended",
            },
        )
        status_response = client.get("/api/storage/location/status")

    assert restart_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["ready"] is False
    assert status_payload["lifecycle_state"] == "maintenance"
    assert status_payload["migration_phase"] == "awaiting_shutdown"
    assert status_payload["shutdown_retry_allowed"] is True
    assert status_payload["recovery_action"] == "retry_safe_exit"
    assert status_payload["storage"]["migration_pending"] is True


@pytest.mark.unit
def test_restart_shutdown_and_checkpoint_restore_failure_keeps_recoverable_pending_intent(
    tmp_path,
):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    def request_app_shutdown():
        raise RuntimeError("shutdown failed")

    with patch.object(
        storage_location_router_module,
        "delete_storage_migration",
        side_effect=OSError("checkpoint restore failed"),
    ):
        with _build_client(config_manager, request_app_shutdown=request_app_shutdown) as client:
            restart_response = client.post(
                "/api/storage/location/restart",
                json={
                    "selected_root": str(target_root),
                    "selection_source": "recommended",
                },
            )
            status_response = client.get("/api/storage/location/status")

    assert restart_response.status_code == 500
    restart_payload = restart_response.json()
    assert restart_payload["result"] == "result_unknown"
    assert restart_payload["error_code"] == "restart_schedule_rollback_failed"
    assert restart_payload["migration_phase"] == "awaiting_shutdown"
    assert restart_payload["shutdown_retry_allowed"] is True
    pending = load_storage_migration(config_manager)
    assert pending["status"] == "pending"
    assert pending["target_root"] == str(target_root.resolve())
    assert config_manager.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    status_payload = status_response.json()
    assert status_payload["migration_phase"] == "awaiting_shutdown"
    assert status_payload["shutdown_retry_allowed"] is True


@pytest.mark.unit
def test_restart_double_restore_failure_never_flips_online_status_back_to_ready(
    tmp_path,
):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"
    real_save_root_state = config_manager.save_root_state
    root_state_write_count = 0

    def fail_root_state_restore(data):
        nonlocal root_state_write_count
        root_state_write_count += 1
        if root_state_write_count == 1:
            return real_save_root_state(data)
        raise OSError("root state restore failed")

    config_manager.save_root_state = fail_root_state_restore

    with patch.object(
        storage_location_router_module,
        "save_storage_migration",
        side_effect=OSError("recovery checkpoint save failed"),
    ):
        with _build_client(
            config_manager,
            request_app_shutdown=lambda: (_ for _ in ()).throw(RuntimeError("shutdown failed")),
        ) as client:
            restart_response = client.post(
                "/api/storage/location/restart",
                json={
                    "selected_root": str(target_root),
                    "selection_source": "recommended",
                },
            )
            status_response = client.get("/api/storage/location/status")

    assert restart_response.status_code == 500
    assert restart_response.json()["error_code"] == "restart_schedule_rollback_failed"
    assert load_storage_migration(config_manager) is None
    assert config_manager.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert config_manager.load_root_state()["last_migration_result"].startswith("restart_pending:")
    status_payload = status_response.json()
    assert status_payload["ready"] is False
    assert status_payload["lifecycle_state"] == "maintenance"
    assert status_payload["migration_phase"] == "awaiting_shutdown"
    assert status_payload["shutdown_retry_allowed"] is True
    assert status_payload["recovery_action"] == "retry_safe_exit"


@pytest.mark.unit
def test_checkpointless_restart_recovery_can_reselect_current_root(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    current_root = config_manager.app_docs_dir.resolve()
    save_storage_policy(
        config_manager,
        selected_root=current_root,
        selection_source="current",
    )
    root_state = config_manager.load_root_state()
    root_state.update(
        {
            "mode": ROOT_MODE_MAINTENANCE_READONLY,
            "last_known_good_root": str(current_root),
            "last_migration_result": f"restart_pending:{current_root}",
        }
    )
    config_manager.save_root_state(root_state)
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "recovery_required")
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager, request_app_shutdown=lambda: None) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(current_root),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == "restart_initiated"
    assert load_storage_migration(config_manager) is None
    recovered_state = config_manager.load_root_state()
    assert recovered_state["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert recovered_state["last_migration_result"].startswith("restart_rebind:")


@pytest.mark.unit
def test_checkpointless_restart_recovery_preserves_completed_retention_metadata(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    current_root = config_manager.app_docs_dir.resolve()
    retained_root = (tmp_path / "retained" / "N.E.K.O").resolve()
    retained_file = retained_root / "config" / "characters.json"
    retained_file.parent.mkdir(parents=True)
    retained_file.write_text("{}", encoding="utf-8")
    save_storage_policy(
        config_manager,
        selected_root=current_root,
        selection_source="current",
    )
    completed_checkpoint = save_storage_migration(
        config_manager,
        {
            "version": 2,
            "txid": "a" * 32,
            "status": "completed",
            "source_root": str(retained_root),
            "target_root": str(current_root),
            "selection_source": "custom",
            "migration_mode": "copy",
            "retained_source_root": str(retained_root),
            "retained_source_mode": "manual_retention",
            "completed_at": "2026-09-15T00:00:00Z",
        },
    )
    root_state = config_manager.load_root_state()
    root_state.update(
        {
            "mode": ROOT_MODE_MAINTENANCE_READONLY,
            "last_known_good_root": str(current_root),
            "last_migration_result": f"restart_pending:{current_root}",
            "legacy_cleanup_pending": True,
        }
    )
    config_manager.save_root_state(root_state)
    monkeypatch.setenv("NEKO_STORAGE_RECOVERY_MODE", "recovery_required")
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager, request_app_shutdown=lambda: None) as client:
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(current_root),
                "selection_source": "current",
            },
        )

    assert response.status_code == 200
    assert load_storage_migration(config_manager) == completed_checkpoint
    assert config_manager.load_root_state()["legacy_cleanup_pending"] is True


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
        logger=None,
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
        "save_storage_migration",
        side_effect=OSError("restore failed"),
    ), patch.object(
        storage_location_router_module,
        "delete_storage_migration",
    ) as delete_migration, patch.object(
        storage_location_router_module.logger,
        "exception",
    ) as log_exception:
        storage_location_router_module._restore_restart_schedule_state(
            config_manager,
            {"migration": previous_migration, "root_state": None},
            anchor_root=config_manager.anchor_root,
        )

    delete_migration.assert_not_called()
    log_exception.assert_called_once()


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
def test_storage_location_rollback_required_is_explicit_and_cannot_be_replaced(tmp_path, monkeypatch):
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
    save_storage_migration(
        config_manager,
        {
            **checkpoint,
            "status": "rollback_required",
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

    with _build_client(
        reloaded_manager,
        request_app_shutdown=lambda: None,
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(reloaded_manager.anchor_root),
                "selection_source": "recommended",
            },
        )
        select_payload = select_response.json()
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(reloaded_manager.anchor_root),
                "selection_source": "recommended",
                "restart_operation_id": select_payload["restart_operation_id"],
            },
        )

    assert select_response.status_code == 200
    assert select_payload["result"] == "restart_required"
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_initiated"
    assert payload["selected_root"] == str(reloaded_manager.anchor_root)

    policy_payload = load_storage_policy(reloaded_manager, anchor_root=reloaded_manager.anchor_root)
    assert policy_payload["selected_root"] == str(reloaded_manager.anchor_root)
    assert reloaded_manager.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY


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

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: None,
    ) as client:
        select_response = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
            },
        )
        select_payload = select_response.json()
        response = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(config_manager.app_docs_dir),
                "selection_source": "recovered",
                "restart_operation_id": select_payload["restart_operation_id"],
            },
        )

    assert select_response.status_code == 200
    assert select_payload["result"] == "restart_required"
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"] == "restart_initiated"
    assert payload["selected_root"] == str(config_manager.app_docs_dir)
    assert load_storage_migration(config_manager) is None

    policy_payload = load_storage_policy(config_manager, anchor_root=config_manager.anchor_root)
    assert policy_payload["selected_root"] == str(config_manager.app_docs_dir)
    root_state = config_manager.load_root_state()
    assert root_state["mode"] == ROOT_MODE_MAINTENANCE_READONLY
    assert root_state["last_migration_result"].startswith("restart_rebind:")


@pytest.mark.unit
@pytest.mark.parametrize("restart_to_new_root", (False, True))
@pytest.mark.parametrize("checkpoint_status", ("failed", "recovery_required"))
def test_storage_restart_never_orphans_recovery_checkpoint_staged_evidence(
    tmp_path,
    restart_to_new_root,
    checkpoint_status,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_real_config_manager(tmp_path)
    current_root = config_manager.app_docs_dir
    selected_root = (
        tmp_path / "new-selection" / "N.E.K.O"
        if restart_to_new_root
        else current_root
    )
    shutdown_calls = []

    with _build_client(
        config_manager,
        request_app_shutdown=lambda: shutdown_calls.append(True),
    ) as client:
        prepared = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(selected_root),
                "selection_source": "recovered",
            },
        )
        assert prepared.status_code == 200

        failed_target = tmp_path / "failed-target" / "N.E.K.O"
        migration_payload = create_pending_storage_migration(
            config_manager,
            source_root=current_root,
            target_root=failed_target,
            selection_source="custom",
        )
        transaction_root = storage_migration_module._transaction_root_for(
            failed_target.resolve(),
            migration_payload["txid"],
        )
        staged_file = transaction_root / "staged" / "config" / "characters.json"
        staged_file.parent.mkdir(parents=True)
        staged_file.write_text("ONLY-COPY", encoding="utf-8")
        storage_migration_module._write_transaction_owner_marker(
            migration_payload,
            transaction_root,
            migration_payload["txid"],
        )
        migration_payload.update(
            status=checkpoint_status,
            transaction_root=str(transaction_root),
            error_code="source_recovery_unverifiable",
            error_message="source changed after the staged copy was created",
        )
        save_storage_migration(config_manager, migration_payload)
        checkpoint_path = get_storage_migration_path(config_manager)
        checkpoint_before = checkpoint_path.read_bytes()

        restart = client.post(
            "/api/storage/location/restart",
            json={
                "selected_root": str(selected_root),
                "selection_source": "recovered",
                "restart_operation_id": prepared.json()["restart_operation_id"],
            },
        )
        select_again = client.post(
            "/api/storage/location/select",
            json={
                "selected_root": str(selected_root),
                "selection_source": "recovered",
            },
        )

    assert restart.status_code == 409
    assert restart.json()["error_code"] == "storage_recovery_evidence_retained"
    assert select_again.status_code == 409
    assert select_again.json()["error_code"] == "storage_recovery_evidence_retained"
    assert shutdown_calls == []
    assert checkpoint_path.read_bytes() == checkpoint_before
    assert load_storage_migration(config_manager)["txid"] == migration_payload["txid"]
    assert staged_file.read_text(encoding="utf-8") == "ONLY-COPY"


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
                "restart_operation_id": select_response.json()["restart_operation_id"],
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
def test_storage_location_cleanup_moves_legacy_community_state_before_removing_root(
    tmp_path,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    auth_payload = {"access_token": "legacy-token"}
    social_payload = {"token": "legacy-token"}
    oauth_pending = {
        "state": "oauth-state",
        "code_verifier": "oauth-verifier",
        "expires_at": time.time() + 300,
    }
    steam_pending = {"state": "steam-state", "ts": 1}
    for filename, value in {
        "community_auth.json": auth_payload,
        "social_session.json": social_payload,
        "community_oauth_pending.json": oauth_pending,
        "community_steam_pending.json": steam_pending,
    }.items():
        (source_root / filename).write_text(json.dumps(value), encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert response.status_code == 200
    assert response.json()["metadata_persisted"] is True
    assert not source_root.exists()
    state_dir = reloaded_manager.local_state_dir
    assert json.loads((state_dir / "community_auth.json").read_text(encoding="utf-8")) == auth_payload
    assert json.loads((state_dir / "social_session.json").read_text(encoding="utf-8")) == social_payload
    assert json.loads((state_dir / "community_oauth_pending.json").read_text(encoding="utf-8")) == oauth_pending
    assert not (state_dir / "community_steam_pending.json").exists(), "expired PKCE state is discarded, not migrated"


@pytest.mark.unit
def test_storage_location_cleanup_reclaims_proven_orphaned_retained_social_lock(
    tmp_path,
    monkeypatch,
):
    if storage_location_router_module.os.name == "nt":
        pytest.skip("retained cleanup requires POSIX directory handles")
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    social_payload = {"token": "legacy-token"}
    (source_root / "social_session.json").write_text(
        json.dumps(social_payload),
        encoding="utf-8",
    )
    orphan_pid = 999999
    (source_root / "social_session.json.lock").write_text(
        json.dumps({"token": f"{orphan_pid}:orphan", "pid": orphan_pid}),
        encoding="utf-8",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setenv("NEKO_LAUNCHER_SINGLE_INSTANCE_PROVEN", "test-owner")
    monkeypatch.setattr(
        "utils.storage.community_private_state.probe_social_lock_process",
        lambda pid: ("orphaned", "", "") if pid == orphan_pid else ("active", "", ""),
    )
    monkeypatch.setattr(
        "main_routers.card_drop_router.classify_social_lock_owner",
        lambda owner: "orphaned" if owner and owner.get("pid") == orphan_pid else "active",
    )

    with _build_client(reloaded_manager) as client:
        before = client.get("/api/storage/location/status")
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert before.status_code == 200
    assert before.json()["completion_notice"]["cleanup_available"] is True
    assert cleanup.status_code == 200
    assert not source_root.exists()
    assert json.loads(
        (reloaded_manager.local_state_dir / "social_session.json").read_text(encoding="utf-8")
    ) == social_payload


@pytest.mark.unit
def test_storage_location_private_only_retained_root_remains_visible_and_cleanupable(
    tmp_path,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_config = source_root / "config"
    source_config.mkdir(parents=True)
    (source_config / "characters.json").write_text("{}", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    (source_config / "characters.json").unlink()
    source_config.rmdir()
    legacy_auth = {"access_token": "private-only"}
    (source_root / "community_auth.json").write_text(
        json.dumps(legacy_auth),
        encoding="utf-8",
    )
    reloaded_manager = _make_real_config_manager(tmp_path)

    with _build_client(reloaded_manager) as client:
        before = client.get("/api/storage/location/status")
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )
        after = client.get("/api/storage/location/status")

    assert before.status_code == 200
    assert before.json()["storage"]["legacy_cleanup_pending"] is True
    assert before.json()["completion_notice"]["completed"] is True
    assert before.json()["completion_notice"]["cleanup_available"] is True
    assert cleanup.status_code == 200
    assert cleanup.json()["metadata_persisted"] is True
    assert not source_root.exists()
    assert json.loads(
        (reloaded_manager.local_state_dir / "community_auth.json").read_text(encoding="utf-8")
    ) == legacy_auth
    assert after.json()["storage"]["legacy_cleanup_pending"] is False
    assert after.json()["completion_notice"]["completed"] is False


@pytest.mark.unit
def test_storage_location_anchor_with_only_private_state_is_cleanupable(tmp_path):
    if storage_location_router_module.os.name == "nt":
        pytest.skip("retained cleanup requires POSIX directory handles")

    config_manager = _make_anchor_root_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_root.mkdir(parents=True, exist_ok=True)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    legacy_auth = {"access_token": "anchor-private-only"}
    (source_root / "community_auth.json").write_text(
        json.dumps(legacy_auth),
        encoding="utf-8",
    )
    save_storage_policy(
        config_manager,
        selected_root=source_root,
        selection_source="current",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True

    reloaded_manager = _make_anchor_root_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        before = client.get("/api/storage/location/status")
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert before.status_code == 200
    assert before.json()["completion_notice"]["completed"] is True
    assert before.json()["completion_notice"]["cleanup_available"] is True
    assert cleanup.status_code == 200
    assert cleanup.json()["metadata_persisted"] is True
    assert source_root.exists(), "the fixed anchor itself must never be removed"
    assert not (source_root / "community_auth.json").exists()
    assert json.loads(
        (reloaded_manager.local_state_dir / "community_auth.json").read_text(
            encoding="utf-8"
        )
    ) == legacy_auth
    assert (source_root / "state" / "storage_migration.json").exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleaned"


@pytest.mark.unit
def test_storage_location_cleanup_checkpoint_write_failure_reconciles_from_filesystem(
    tmp_path,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    save_calls = 0

    def _fail_finalize(*args, **kwargs):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            return save_storage_migration(*args, **kwargs)
        raise OSError("checkpoint denied")

    with patch.object(
        storage_location_router_module,
        "save_storage_migration",
        side_effect=_fail_finalize,
    ):
        with _build_client(reloaded_manager) as client:
            cleanup_response = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )
            status_response = client.get("/api/storage/location/status")
            retry_response = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )

    assert cleanup_response.status_code == 200
    assert cleanup_response.json()["metadata_persisted"] is False
    assert not source_root.exists()
    status_payload = status_response.json()
    assert status_payload["storage"]["legacy_cleanup_pending"] is False
    assert status_payload["completion_notice"]["completed"] is False
    assert retry_response.status_code == 404
    stale_checkpoint = load_storage_migration(reloaded_manager)
    assert stale_checkpoint["retained_source_mode"] == "cleanup_in_progress"


@pytest.mark.unit
def test_storage_location_unknown_only_root_converges_after_both_metadata_writes_fail(
    tmp_path,
    monkeypatch,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    unknown = source_root / "personal-notes.txt"
    unknown.write_text("keep", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    save_calls = 0

    def _fail_finalize(*args, **kwargs):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            return save_storage_migration(*args, **kwargs)
        raise OSError("checkpoint denied")

    with (
        patch.object(
            storage_location_router_module,
            "save_storage_migration",
            side_effect=_fail_finalize,
        ),
        patch.object(
            reloaded_manager,
            "save_root_state",
            side_effect=OSError("root state denied"),
        ),
    ):
        with _build_client(reloaded_manager) as client:
            cleanup = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )
            status = client.get("/api/storage/location/status")
            retry = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )

    assert cleanup.status_code == 200
    assert cleanup.json()["metadata_persisted"] is False
    assert unknown.read_text(encoding="utf-8") == "keep"
    assert not (source_root / "config").exists()
    assert status.json()["storage"]["legacy_cleanup_pending"] is False
    assert status.json()["completion_notice"]["completed"] is False
    assert retry.status_code == 404
    stale_checkpoint = load_storage_migration(reloaded_manager)
    assert stale_checkpoint["retained_source_mode"] == "cleanup_in_progress"

    # Reusing the same path after cleanup must not turn unrelated future state
    # into a legacy credential merely because final metadata writes both failed.
    recreated_auth = source_root / "community_auth.json"
    recreated_auth.write_text(json.dumps({"access_token": "future-account"}), encoding="utf-8")
    import main_routers.card_drop_router as card_drop_router_module
    from utils import config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module,
        "get_config_manager",
        lambda *_args, **_kwargs: reloaded_manager,
    )
    assert card_drop_router_module._legacy_selected_roots() == []
    assert card_drop_router_module._load_auth() is None
    assert recreated_auth.exists()
    assert card_drop_router_module._clear_auth() is True
    assert recreated_auth.exists()


@pytest.mark.unit
def test_storage_location_cleanup_premark_failure_deletes_nothing(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    runtime_file = source_root / "config" / "characters.json"
    runtime_file.write_text("{}", encoding="utf-8")
    legacy_auth = source_root / "community_auth.json"
    legacy_auth.write_text(json.dumps({"access_token": "only-copy"}), encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)

    with patch.object(
        storage_location_router_module,
        "save_storage_migration",
        side_effect=OSError("premark denied"),
    ):
        with _build_client(reloaded_manager) as client:
            response = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )

    assert response.status_code == 503
    assert response.json()["error_code"] == "retained_source_cleanup_intent_failed"
    assert runtime_file.exists()
    assert legacy_auth.exists()
    assert not (reloaded_manager.local_state_dir / "community_auth.json").exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "manual_retention"


@pytest.mark.unit
def test_storage_location_direct_cleanup_preserves_conflicting_target_and_source_credentials(
    tmp_path,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    runtime_file = source_root / "config" / "characters.json"
    runtime_file.write_text("{}", encoding="utf-8")
    source_auth = source_root / "community_auth.json"
    source_auth.write_text(json.dumps({"access_token": "source-current"}), encoding="utf-8")
    target_root.mkdir(parents=True)
    target_auth = target_root / "community_auth.json"
    target_auth.write_text(json.dumps({"access_token": "target-stale"}), encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert response.status_code == 500
    assert response.json()["error_code"] == "retained_source_cleanup_failed"
    assert runtime_file.exists()
    assert json.loads(source_auth.read_text(encoding="utf-8"))["access_token"] == "source-current"
    assert json.loads(target_auth.read_text(encoding="utf-8"))["access_token"] == "target-stale"
    assert not (reloaded_manager.local_state_dir / "community_auth.json").exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleanup_in_progress"


@pytest.mark.unit
def test_storage_location_cleanup_in_progress_is_safely_retryable_after_crash(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    runtime_file = source_root / "config" / "characters.json"
    runtime_file.write_text("{}", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)

    with patch.object(
        storage_location_router_module,
        "_cleanup_retained_runtime_root",
        side_effect=OSError("simulated crash before deletion"),
    ):
        with _build_client(reloaded_manager) as client:
            interrupted = client.post(
                "/api/storage/location/retained-source/cleanup",
                json={"retained_root": str(source_root)},
            )

    assert interrupted.status_code == 500
    assert runtime_file.exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleanup_in_progress"

    with _build_client(reloaded_manager) as client:
        retry = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert retry.status_code == 200
    assert not source_root.exists()
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleaned"


@pytest.mark.unit
def test_completed_cleanup_then_logout_removes_target_witness_but_not_reused_source(
    tmp_path,
    monkeypatch,
):
    from contextlib import contextmanager

    import main_routers.card_drop_router as card_drop_router_module
    from utils import config_manager as config_manager_module

    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    source_root.mkdir(parents=True, exist_ok=True)
    target_root.mkdir(parents=True, exist_ok=True)
    auth = {"access_token": "same-token"}
    social = {"token": "same-token"}
    for root in (source_root, target_root):
        (root / "community_auth.json").write_text(json.dumps(auth), encoding="utf-8")
        (root / "social_session.json").write_text(json.dumps(social), encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)

    with _build_client(reloaded_manager) as client:
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert cleanup.status_code == 200
    assert load_storage_migration(reloaded_manager)["retained_source_mode"] == "cleaned"
    assert (target_root / "community_auth.json").exists()
    assert (target_root / "social_session.json").exists()

    # A later directory at the old source path is unrelated once the checkpoint
    # is cleaned; logout must not follow it, but must clear current target secrets.
    source_root.mkdir(parents=True)
    reused_source_auth = source_root / "community_auth.json"
    reused_source_auth.write_text(json.dumps({"access_token": "unrelated"}), encoding="utf-8")
    monkeypatch.setattr(
        config_manager_module,
        "get_config_manager",
        lambda *_args, **_kwargs: reloaded_manager,
    )
    locked_paths = []
    real_social_session_locks = card_drop_router_module._social_session_locks

    @contextmanager
    def record_social_session_locks(paths, **kwargs):
        locked_paths.extend(paths)
        with real_social_session_locks(paths, **kwargs):
            yield

    monkeypatch.setattr(
        card_drop_router_module,
        "_social_session_locks",
        record_social_session_locks,
    )
    assert card_drop_router_module._clear_auth() is True
    assert not (reloaded_manager.local_state_dir / "community_auth.json").exists()
    assert not (reloaded_manager.local_state_dir / "social_session.json").exists()
    assert not (target_root / "community_auth.json").exists()
    assert not (target_root / "social_session.json").exists()
    assert target_root / "social_session.json" in locked_paths
    assert source_root / "social_session.json" not in locked_paths
    assert reused_source_auth.exists()


@pytest.mark.unit
def test_storage_location_cleanup_preserves_everything_when_credential_anchor_write_fails(
    tmp_path,
    monkeypatch,
):
    import main_routers.card_drop_router as card_drop_router_module

    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    legacy_auth = source_root / "community_auth.json"
    legacy_auth.write_text(json.dumps({"access_token": "only-copy"}), encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(
        card_drop_router_module,
        "_write_private_json_no_replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("anchor denied")),
    )

    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert response.status_code == 500
    assert legacy_auth.exists()
    assert (source_root / "config" / "characters.json").exists()
    assert not (reloaded_manager.local_state_dir / "community_auth.json").exists()


@pytest.mark.unit
def test_storage_location_cleanup_preserves_unknown_files_in_retained_root(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    unknown_file = source_root / "personal-notes.txt"
    unknown_file.write_text("keep", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    assert run_pending_storage_migration(config_manager)["completed"] is True

    reloaded_manager = _make_real_config_manager(tmp_path)
    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert response.status_code == 200
    assert not (source_root / "config").exists()
    assert unknown_file.read_text(encoding="utf-8") == "keep"


@pytest.mark.unit
def test_storage_location_cleanup_rejects_retained_root_replaced_by_symlink(tmp_path):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    external_root = tmp_path / "external"
    retained_root.parent.mkdir(parents=True)
    external_config = external_root / "config"
    external_config.mkdir(parents=True)
    external_file = external_config / "characters.json"
    external_file.write_text("KEEP", encoding="utf-8")
    try:
        retained_root.symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(ValueError, match="符号链接"):
        storage_location_router_module._cleanup_retained_runtime_root(
            retained_root,
            current_root=tmp_path / "current" / "N.E.K.O",
            anchor_root=tmp_path / "anchor" / "N.E.K.O",
        )

    assert external_file.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_storage_location_cleanup_dirfd_does_not_follow_public_root_swap(
    tmp_path,
    monkeypatch,
):
    if not storage_location_router_module.shutil.rmtree.avoids_symlink_attacks:
        pytest.skip("stdlib does not provide symlink-safe dirfd rmtree")
    retained_root = tmp_path / "retained" / "N.E.K.O"
    moved_root = tmp_path / "retained" / "moved-original"
    original_file = retained_root / "config" / "characters.json"
    original_file.parent.mkdir(parents=True)
    original_file.write_text("ORIGINAL", encoding="utf-8")
    external_root = tmp_path / "external"
    external_file = external_root / "config" / "characters.json"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("KEEP", encoding="utf-8")
    original_remove = storage_location_router_module._secure_remove_runtime_entry
    swapped = False

    def _swap_then_remove(root_fd, entry):
        nonlocal swapped
        if not swapped:
            swapped = True
            retained_root.rename(moved_root)
            retained_root.symlink_to(external_root, target_is_directory=True)
        return original_remove(root_fd, entry)

    monkeypatch.setattr(
        storage_location_router_module,
        "_secure_remove_runtime_entry",
        _swap_then_remove,
    )
    with pytest.raises(ValueError, match="替换"):
        storage_location_router_module._cleanup_retained_runtime_root(
            retained_root,
            current_root=tmp_path / "current" / "N.E.K.O",
            anchor_root=tmp_path / "anchor" / "N.E.K.O",
        )

    assert external_file.read_text(encoding="utf-8") == "KEEP"
    assert retained_root.is_symlink()


@pytest.mark.unit
def test_storage_location_cleanup_dirfd_does_not_follow_swapped_ancestor(
    tmp_path,
    monkeypatch,
):
    if not storage_location_router_module.shutil.rmtree.avoids_symlink_attacks:
        pytest.skip("stdlib does not provide symlink-safe dirfd rmtree")
    retained_parent = tmp_path / "mounted"
    retained_root = retained_parent / "N.E.K.O"
    moved_parent = tmp_path / "moved-mounted"
    original_file = retained_root / "config" / "characters.json"
    original_file.parent.mkdir(parents=True)
    original_file.write_text("ORIGINAL", encoding="utf-8")
    external_parent = tmp_path / "external-mounted"
    external_file = external_parent / "N.E.K.O" / "config" / "characters.json"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("KEEP", encoding="utf-8")
    original_remove = storage_location_router_module._secure_remove_runtime_entry
    swapped = False

    def _swap_ancestor_then_remove(root_fd, entry):
        nonlocal swapped
        if not swapped:
            swapped = True
            retained_parent.rename(moved_parent)
            retained_parent.symlink_to(external_parent, target_is_directory=True)
        return original_remove(root_fd, entry)

    monkeypatch.setattr(
        storage_location_router_module,
        "_secure_remove_runtime_entry",
        _swap_ancestor_then_remove,
    )
    storage_location_router_module._cleanup_retained_runtime_root(
        retained_root,
        current_root=tmp_path / "current" / "N.E.K.O",
        anchor_root=tmp_path / "anchor" / "N.E.K.O",
    )

    assert external_file.read_text(encoding="utf-8") == "KEEP"
    assert retained_parent.is_symlink()


@pytest.mark.unit
def test_storage_location_cleanup_rejects_real_directory_replacement_after_premark(
    tmp_path,
    monkeypatch,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    moved_root = tmp_path / "legacy-runtime" / "original"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    original_file = source_root / "config" / "characters.json"
    original_file.write_text("ORIGINAL", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    original_cleanup = storage_location_router_module._cleanup_retained_runtime_root

    def _replace_before_cleanup(*args, **kwargs):
        source_root.rename(moved_root)
        replacement_file = source_root / "config" / "characters.json"
        replacement_file.parent.mkdir(parents=True)
        replacement_file.write_text("REPLACEMENT", encoding="utf-8")
        return original_cleanup(*args, **kwargs)

    monkeypatch.setattr(
        storage_location_router_module,
        "_cleanup_retained_runtime_root",
        _replace_before_cleanup,
    )
    with _build_client(reloaded_manager) as client:
        response = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert response.status_code == 500
    assert response.json()["error_code"] == "retained_source_cleanup_failed"
    assert (source_root / "config" / "characters.json").read_text(encoding="utf-8") == "REPLACEMENT"
    assert (moved_root / "config" / "characters.json").read_text(encoding="utf-8") == "ORIGINAL"
    checkpoint = load_storage_migration(reloaded_manager)
    assert checkpoint["retained_source_mode"] == "cleanup_in_progress"
    assert checkpoint["cleanup_root_identity"]["inode"] == moved_root.stat().st_ino


@pytest.mark.unit
def test_storage_location_cleanup_is_unavailable_without_safe_dirfd_rmtree(
    tmp_path,
    monkeypatch,
):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    runtime_file = source_root / "config" / "characters.json"
    runtime_file.write_text("KEEP", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(
        storage_location_router_module.shutil.rmtree,
        "avoids_symlink_attacks",
        False,
    )

    with _build_client(reloaded_manager) as client:
        status = client.get("/api/storage/location/status")
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(source_root)},
        )

    assert status.json()["completion_notice"]["cleanup_available"] is False
    assert cleanup.status_code == 404
    assert runtime_file.read_text(encoding="utf-8") == "KEEP"
    checkpoint = load_storage_migration(reloaded_manager)
    assert checkpoint["retained_source_mode"] == "manual_retention"
    assert "cleanup_root_identity" not in checkpoint
    with pytest.raises(ValueError, match="手动清理"):
        storage_location_router_module._cleanup_retained_runtime_root(
            source_root,
            current_root=target_root,
            anchor_root=reloaded_manager.anchor_root,
        )


@pytest.mark.unit
def test_storage_location_api_refuses_retained_root_nested_under_current_target(tmp_path):
    config_manager = _make_real_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    reloaded_manager = _make_real_config_manager(tmp_path)
    nested_retained = target_root / "nested-retained"
    (nested_retained / "config").mkdir(parents=True)
    nested_file = nested_retained / "config" / "characters.json"
    nested_file.write_text("KEEP", encoding="utf-8")
    checkpoint = load_storage_migration(reloaded_manager)
    checkpoint["retained_source_root"] = str(nested_retained)
    checkpoint["backup_root"] = str(nested_retained)
    checkpoint["retained_source_mode"] = "manual_retention"
    save_storage_migration(reloaded_manager, checkpoint)

    with _build_client(reloaded_manager) as client:
        status = client.get("/api/storage/location/status")
        cleanup = client.post(
            "/api/storage/location/retained-source/cleanup",
            json={"retained_root": str(nested_retained)},
        )

    assert status.status_code == 200
    assert status.json()["completion_notice"]["completed"] is True
    assert status.json()["completion_notice"]["cleanup_available"] is False
    assert cleanup.status_code == 404
    assert nested_file.read_text(encoding="utf-8") == "KEEP"
    unchanged = load_storage_migration(reloaded_manager)
    assert unchanged["retained_source_mode"] == "manual_retention"
    assert "cleanup_root_identity" not in unchanged


@pytest.mark.unit
def test_storage_location_cleanup_rejects_nested_state_symlink_without_deleting_external_or_local_data(
    tmp_path,
):
    retained_root = tmp_path / "retained" / "N.E.K.O"
    retained_config = retained_root / "config" / "characters.json"
    retained_config.parent.mkdir(parents=True)
    retained_config.write_text("LOCAL", encoding="utf-8")
    external_state = tmp_path / "external-state"
    external_score = external_state / "game_scores" / "score.db"
    external_score.parent.mkdir(parents=True)
    external_score.write_bytes(b"KEEP")
    try:
        (retained_root / "state").symlink_to(external_state, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(ValueError, match="安全边界"):
        storage_location_router_module._cleanup_retained_runtime_root(
            retained_root,
            current_root=tmp_path / "current" / "N.E.K.O",
            anchor_root=tmp_path / "anchor" / "N.E.K.O",
        )

    assert retained_config.read_text(encoding="utf-8") == "LOCAL"
    assert external_score.read_bytes() == b"KEEP"


@pytest.mark.unit
def test_storage_location_cleanup_retained_anchor_root_removes_runtime_entries_only(tmp_path):
    config_manager = _make_anchor_root_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (source_root / "memory" / "A").mkdir(parents=True, exist_ok=True)
    (source_root / "memory" / "A" / "recent.json").write_text("[]", encoding="utf-8")
    save_storage_policy(
        config_manager,
        selected_root=source_root,
        selection_source="current",
    )
    (source_root / "state" / "game_scores").mkdir(parents=True, exist_ok=True)
    (source_root / "state" / "game_scores" / "badminton_scores.db").write_bytes(b"score")
    (source_root / "cloudsave").mkdir(parents=True, exist_ok=True)
    (source_root / "cloudsave" / "manifest.json").write_text("{}", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    run_pending_storage_migration(config_manager)
    assert (target_root / "state" / "game_scores" / "badminton_scores.db").read_bytes() == b"score"

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
    assert not (source_root / "state" / "game_scores").exists()
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
