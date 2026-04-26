from pathlib import Path

import pytest

from utils import storage_location_bootstrap as storage_location_bootstrap_module
from utils.config_manager import ConfigManager
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload
from utils.storage_migration import create_pending_storage_migration, run_pending_storage_migration
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


def _make_real_config_manager(tmp_path: Path):
    standard_root = tmp_path / "anchor-base"
    monkeypatchers = [
        pytest.MonkeyPatch(),
        pytest.MonkeyPatch(),
    ]
    monkeypatchers[0].setattr(
        ConfigManager,
        "_get_documents_directory",
        lambda self: tmp_path / "runtime-parent",
    )
    monkeypatchers[1].setattr(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        lambda self: [standard_root],
    )
    try:
        config_manager = ConfigManager("N.E.K.O")
    finally:
        monkeypatchers[0].undo()
        monkeypatchers[1].undo()
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _make_anchor_root_config_manager(tmp_path: Path):
    standard_root = tmp_path / "anchor-base"
    monkeypatchers = [
        pytest.MonkeyPatch(),
        pytest.MonkeyPatch(),
    ]
    monkeypatchers[0].setattr(
        ConfigManager,
        "_get_documents_directory",
        lambda self: standard_root,
    )
    monkeypatchers[1].setattr(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        lambda self: [standard_root],
    )
    try:
        config_manager = ConfigManager("N.E.K.O")
    finally:
        monkeypatchers[0].undo()
        monkeypatchers[1].undo()
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


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
    assert payload["blocking_reason"] == "selection_required"
    assert payload["last_error_summary"] == ""
    assert payload["poll_interval_ms"] == 1200
    assert payload["stage"] == "stage3_web_restart"


@pytest.mark.unit
def test_storage_location_bootstrap_payload_uses_configured_anchor_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    config_manager.anchor_root = tmp_path / "canonical-anchor" / "N.E.K.O"
    config_manager._get_standard_data_directory_candidates = lambda: [tmp_path / "wrong-anchor-base"]

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["recommended_root"] == str(config_manager.anchor_root.resolve())
    assert payload["anchor_root"] == str(config_manager.anchor_root.resolve())
    assert payload["cloudsave_root"] == str((config_manager.anchor_root / "cloudsave").resolve())


@pytest.mark.unit
def test_storage_location_bootstrap_legacy_sources_dedupes_actual_and_display_current_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    displayed_root = tmp_path / "offline-selected" / "N.E.K.O"
    config_manager.reported_current_root = displayed_root
    (config_manager.app_docs_dir / "config").mkdir(parents=True, exist_ok=True)
    (config_manager.app_docs_dir / "config" / "characters.json").write_text("{}", encoding="utf-8")

    config_manager.get_legacy_app_root_candidates = lambda: [
        config_manager.app_docs_dir,
        displayed_root,
        config_manager._legacy_root,
    ]

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["current_root"] == str(displayed_root.resolve())
    assert payload["legacy_sources"] == [str(config_manager._legacy_root.resolve())]


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
    assert payload["blocking_reason"] == ""


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
    assert payload["blocking_reason"] == "recovery_required"


@pytest.mark.unit
def test_storage_location_bootstrap_payload_marks_pending_migration_from_checkpoint(
    tmp_path,
    monkeypatch,
):
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

    payload = build_storage_location_bootstrap_payload(config_manager)

    assert payload["selection_required"] is False
    assert payload["migration_pending"] is True
    assert payload["blocking_reason"] == "migration_pending"
    assert payload["migration"]["status"] == "pending"


@pytest.mark.unit
def test_storage_location_bootstrap_payload_reports_unavailable_committed_root_during_recovery(
    tmp_path,
):
    config_manager = _make_real_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    reloaded_manager = _make_anchor_root_config_manager(tmp_path)
    payload = build_storage_location_bootstrap_payload(reloaded_manager)

    assert payload["current_root"] == str(unavailable_selected_root.resolve())
    assert payload["recommended_root"] == str((tmp_path / "anchor-base" / "N.E.K.O").resolve())
    assert payload["recovery_required"] is True
    assert payload["blocking_reason"] == "recovery_required"
    assert "selected_root_unavailable:" in payload["last_error_summary"]


@pytest.mark.unit
def test_storage_location_bootstrap_payload_marks_cleanup_pending_for_non_anchor_retained_root(tmp_path):
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
    payload = build_storage_location_bootstrap_payload(reloaded_manager)

    assert payload["blocking_reason"] == ""
    assert payload["legacy_cleanup_pending"] is True
    assert payload["migration"]["status"] == "completed"
    assert payload["migration"]["retained_source_root"] == str(source_root.resolve())
    assert payload["migration"]["retained_source_mode"] == "manual_retention"
    assert payload["migration"]["completed_at"]

    root_state = reloaded_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_storage_location_bootstrap_payload_marks_cleanup_pending_when_retained_root_is_anchor_root(tmp_path):
    config_manager = _make_anchor_root_config_manager(tmp_path)
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
    payload = build_storage_location_bootstrap_payload(reloaded_manager)

    assert payload["legacy_cleanup_pending"] is True
    assert payload["migration"]["retained_source_root"] == str(source_root.resolve())

    root_state = reloaded_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True
