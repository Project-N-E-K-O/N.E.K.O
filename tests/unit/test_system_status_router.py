from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import status as system_router_module
from main_routers.system_router import _shared as system_router_shared
from main_routers.shared_state import init_shared_state
from utils import storage_location_bootstrap as storage_location_bootstrap_module
from utils.storage_migration import create_pending_storage_migration
from utils.storage_policy import save_storage_policy


SYSTEM_STATUS_ENDPOINT = "/api/system/status"
SYSTEM_CLIENT_ID_ENDPOINT = "/api/system/client-id"
SYSTEM_SOCIAL_CONFIG_ENDPOINT = "/api/system/social/config"


@pytest.fixture(autouse=True)
def _reset_shared_state_after_test():
    yield
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=None,
        logger=None,
    )


class _DummyConfigManager:
    def __init__(self, tmp_path: Path, *, root_mode: str = "normal"):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"
        self._legacy_root = tmp_path / "legacy" / self.app_name
        (self._legacy_root / "config").mkdir(parents=True, exist_ok=True)
        (self._legacy_root / "config" / "user_preferences.json").write_text("{}", encoding="utf-8")
        self._root_mode = root_mode

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]

    def get_legacy_app_root_candidates(self):
        return [self._legacy_root]

    def load_root_state(self):
        return {
            "mode": self._root_mode,
            "last_known_good_root": str(self.app_docs_dir),
            "last_migration_result": "",
        }


def _build_client(config_manager):
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=config_manager,
        logger=None,
    )
    app = FastAPI()
    app.include_router(system_router_module.router)
    return TestClient(app)


@pytest.mark.unit
def test_system_client_id_fails_closed_when_fresh_id_cannot_be_persisted(tmp_path):
    class _UnsavableClientIdConfigManager(_DummyConfigManager):
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def load_cloudsave_local_state(self):
            return {"client_id": "volatile-client-id"}

        def build_default_cloudsave_local_state(self):
            raise AssertionError("loaded default already contains a client_id")

        def save_cloudsave_local_state(self, _state):
            raise OSError("disk unavailable")

    with _build_client(_UnsavableClientIdConfigManager(tmp_path)) as client:
        response = client.get(SYSTEM_CLIENT_ID_ENDPOINT)

    assert response.status_code == 500
    assert response.json()["ok"] is False
    assert "client_id" not in response.json()
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.unit
def test_system_client_id_persists_fresh_identity_before_returning(tmp_path):
    class _FreshClientIdConfigManager(_DummyConfigManager):
        cloudsave_local_state_path = tmp_path / "state" / "cloudsave_local_state.json"

        def __init__(self, root):
            super().__init__(root)
            self.saved_states = []

        def load_cloudsave_local_state(self):
            return {"client_id": "fresh-client-id"}

        def save_cloudsave_local_state(self, state):
            self.saved_states.append(state)

    config_manager = _FreshClientIdConfigManager(tmp_path)
    with _build_client(config_manager) as client:
        response = client.get(SYSTEM_CLIENT_ID_ENDPOINT)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "client_id": "fresh-client-id"}
    assert config_manager.saved_states == [{"client_id": "fresh-client-id"}]
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.unit
def test_system_social_config_trims_override_and_falls_back(monkeypatch, tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "  https://social.example.test/api/  ")

    with _build_client(config_manager) as client:
        configured = client.get(SYSTEM_SOCIAL_CONFIG_ENDPOINT)
        monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "   ")
        fallback = client.get(SYSTEM_SOCIAL_CONFIG_ENDPOINT)

    assert configured.status_code == 200
    assert configured.json() == {
        "ok": True,
        "social_base_url": "https://social.example.test/api",
        "enabled": True,
    }
    assert fallback.json()["social_base_url"] == "http://localhost:8080"
    assert "no-store" in configured.headers["Cache-Control"]


@pytest.mark.unit
def test_system_status_reports_migration_required_when_storage_selection_is_blocking(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "migration_required"
    assert payload["ready"] is False
    assert payload["storage"]["selection_required"] is True
    assert payload["storage"]["legacy_cleanup_pending"] is False
    assert payload["storage"]["blocking_reason"] == "selection_required"


@pytest.mark.unit
def test_system_status_uses_runtime_config_manager_fallback_when_shared_state_is_not_ready(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch.object(
        system_router_shared,
        "get_config_manager",
        side_effect=RuntimeError("shared_state unavailable"),
    ), patch.object(
        system_router_shared,
        "get_runtime_config_manager",
        return_value=config_manager,
    ):
        with _build_client(config_manager) as client:
            response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "migration_required"
    assert payload["storage"]["blocking_reason"] == "selection_required"


@pytest.mark.unit
def test_system_status_reports_ready_after_storage_policy_when_dev_override_disabled(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager) as client:
        response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["ready"] is True
    assert payload["storage"]["selection_required"] is False
    assert payload["storage"]["legacy_cleanup_pending"] is False
    assert payload["storage"]["blocking_reason"] == ""


@pytest.mark.unit
def test_system_status_reports_migration_required_for_recovery_state_even_without_first_run_selection(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path, root_mode="deferred_init")
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )
    monkeypatch.setattr(
        storage_location_bootstrap_module,
        "DEVELOPMENT_ALWAYS_REQUIRE_SELECTION",
        False,
    )

    with _build_client(config_manager) as client:
        response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "migration_required"
    assert payload["ready"] is False
    assert payload["storage"]["selection_required"] is False
    assert payload["storage"]["recovery_required"] is True
    assert payload["storage"]["blocking_reason"] == "recovery_required"


@pytest.mark.unit
def test_system_status_reports_migration_required_when_checkpoint_is_pending(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )
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
        response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "migration_required"
    assert payload["ready"] is False
    assert payload["storage"]["migration_pending"] is True
    assert payload["storage"]["blocking_reason"] == "migration_pending"


@pytest.mark.unit
def test_system_status_treats_blocking_reason_as_not_ready(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch.object(
        system_router_module,
        "build_storage_location_bootstrap_payload",
        return_value={
            "selection_required": False,
            "migration_pending": False,
            "recovery_required": False,
            "legacy_cleanup_pending": False,
            "blocking_reason": "runtime_initializing",
            "last_error_summary": "",
            "stage": "stage3_web_restart",
        },
    ):
        with _build_client(config_manager) as client:
            response = client.get(SYSTEM_STATUS_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "migration_required"
    assert payload["ready"] is False
    assert payload["storage"]["blocking_reason"] == "runtime_initializing"


@pytest.mark.unit
def test_system_status_does_not_expose_legacy_versioned_route(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.get("/api/v1/system/status")

    assert response.status_code == 404
