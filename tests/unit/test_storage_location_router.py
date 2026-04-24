from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import storage_location_router as storage_location_router_module
from main_routers.shared_state import init_shared_state
from utils.storage_policy import get_storage_policy_path, load_storage_policy


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]

    def get_legacy_app_root_candidates(self):
        return []

    def load_root_state(self):
        return {
            "mode": "normal",
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
