from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import system_router as system_router_module
from main_routers.shared_state import init_shared_state


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_root = tmp_path / "legacy" / self.app_name
        (self._legacy_root / "config").mkdir(parents=True, exist_ok=True)
        (self._legacy_root / "config" / "user_preferences.json").write_text("{}", encoding="utf-8")

    def get_legacy_app_root_candidates(self):
        return [self._legacy_root]

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
    app.include_router(system_router_module.router)
    return TestClient(app)


@pytest.mark.unit
def test_system_status_reports_migration_required_when_storage_selection_is_blocking(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with _build_client(config_manager) as client:
        response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "migration_required"
    assert payload["ready"] is False
    assert payload["storage"]["selection_required"] is True
