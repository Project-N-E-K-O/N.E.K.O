from pathlib import Path

import pytest

from utils import storage_location_bootstrap as storage_location_bootstrap_module
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload
from utils.storage_policy import save_storage_policy


class _DummyConfigManager:
    def __init__(self, tmp_path: Path, *, root_mode: str = "normal"):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._root_mode = root_mode

        legacy_root = tmp_path / "legacy" / self.app_name
        (legacy_root / "config").mkdir(parents=True, exist_ok=True)
        (legacy_root / "config" / "user_preferences.json").write_text("{}", encoding="utf-8")
        self._legacy_root = legacy_root

    def _get_standard_data_directory_candidates(self):
        return [self.app_docs_dir.parent]

    def get_legacy_app_root_candidates(self):
        return [self._legacy_root]

    def load_root_state(self):
        return {
            "mode": self._root_mode,
            "last_known_good_root": str(self.app_docs_dir),
            "last_migration_result": "",
        }


@pytest.mark.unit
def test_storage_location_bootstrap_payload_exposes_stage2_web_fields(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["current_root"] == str(config_manager.app_docs_dir)
    assert payload["recommended_root"] == str(config_manager.app_docs_dir)
    assert payload["anchor_root"] == str(config_manager.app_docs_dir)
    assert payload["cloudsave_root"] == str(config_manager.app_docs_dir / "cloudsave")
    assert payload["legacy_sources"] == [str(config_manager._legacy_root)]
    assert payload["selection_required"] is True
    assert payload["migration_pending"] is False
    assert payload["recovery_required"] is False
    assert payload["stage"] == "stage2_web_selection"


@pytest.mark.unit
def test_storage_location_bootstrap_payload_uses_storage_policy_when_dev_override_disabled(tmp_path, monkeypatch):
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

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["selection_required"] is False


@pytest.mark.unit
def test_storage_location_bootstrap_payload_marks_recovery_state_even_when_first_run_selection_is_not_required(
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

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["selection_required"] is False
    assert payload["recovery_required"] is True
