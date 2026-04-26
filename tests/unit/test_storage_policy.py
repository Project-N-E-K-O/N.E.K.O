from pathlib import Path

import pytest

from utils.storage_policy import (
    CLOUDSAVE_STRATEGY_FIXED_ANCHOR,
    StorageSelectionValidationError,
    get_storage_policy_path,
    load_storage_policy,
    save_storage_policy,
    validate_selected_root,
)


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]


@pytest.mark.unit
def test_save_storage_policy_writes_stable_layout_under_anchor_state(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    payload = save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )

    policy_path = get_storage_policy_path(config_manager)
    assert policy_path == tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_policy.json"
    assert policy_path.is_file()

    reloaded_payload = load_storage_policy(config_manager)
    assert reloaded_payload == payload
    assert payload["anchor_root"] == str(tmp_path / "anchor-base" / "N.E.K.O")
    assert payload["selected_root"] == str(config_manager.app_docs_dir)
    assert payload["cloudsave_strategy"] == CLOUDSAVE_STRATEGY_FIXED_ANCHOR
    assert payload["selection_source"] == "user_selected"
    assert payload["first_run_completed"] is True


@pytest.mark.unit
def test_validate_selected_root_rejects_anchor_reserved_state_directory(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    invalid_target = tmp_path / "anchor-base" / "N.E.K.O" / "state" / "nested"

    with pytest.raises(StorageSelectionValidationError) as exc_info:
        validate_selected_root(config_manager, invalid_target)

    assert "锚点目录保留区域" in str(exc_info.value)


@pytest.mark.unit
def test_validate_selected_root_appends_app_folder_for_custom_parent_directory(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-parent"
    selected_parent.mkdir()

    normalized = validate_selected_root(
        config_manager,
        selected_parent,
        selection_source="custom",
    )

    assert normalized == selected_parent / "N.E.K.O"


@pytest.mark.unit
def test_validate_selected_root_keeps_custom_app_folder_when_already_selected(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "custom-parent" / "N.E.K.O"

    normalized = validate_selected_root(
        config_manager,
        selected_root,
        selection_source="custom",
    )

    assert normalized == selected_root
