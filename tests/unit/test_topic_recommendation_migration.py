"""Recommendation state participates in existing nested-entry migration only."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage import migration

CAT = "character_" + "a" * 32
ENTRY = "state/recommendation"


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


def make_manager(tmp_path):
    from utils.config_manager import ConfigManager
    standard = tmp_path / "anchor"
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime"), \
         patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard]):
        manager = ConfigManager("N.E.K.O")
    manager._get_standard_data_directory_candidates = lambda: [standard]
    return manager


def write_state(root, text="recommendation only"):
    state = root / ENTRY / CAT / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(text, encoding="utf-8")
    return state


def test_nested_registry_keeps_anchor_and_v1_history_separate():
    assert ENTRY in migration.MIGRATED_RUNTIME_ENTRY_NAMES
    assert "state" not in migration.MIGRATED_RUNTIME_ENTRY_NAMES
    assert ENTRY not in migration.V1_MIGRATED_RUNTIME_ENTRY_NAMES
    assert ENTRY in migration.v1_catch_up_unfinished_entries({"version": 1, "status": "completed"})
    assert migration.copy_evidence_entries({"state/game_scores": {"kind": "dir"}}) == {
        "state/game_scores": {"kind": "dir"}}


def test_empty_state_is_not_user_data_but_only_recommendation_is(tmp_path):
    manager = make_manager(tmp_path)
    only = tmp_path / "only-state"
    (only / "state").mkdir(parents=True)
    assert not migration.root_has_migrated_entry_content(only)
    assert not migration.root_has_user_content(only, config_manager=manager)
    write_state(only)
    assert migration.root_has_migrated_entry_content(only)
    assert migration.root_has_user_content(only, config_manager=manager)


def test_actual_nested_migration_copies_and_proves_recommendation(tmp_path):
    manager = make_manager(tmp_path)
    source = manager.app_docs_dir
    saved = write_state(source)
    target = tmp_path / "target" / "N.E.K.O"
    migration.create_pending_storage_migration(manager, source_root=source,
                                               target_root=target, selection_source="custom")
    result = migration.run_pending_storage_migration(manager)
    assert result["completed"], result
    checkpoint = migration.load_storage_migration(manager)
    proof = migration.copy_evidence_entries(checkpoint["copied_entries"])[ENTRY]
    assert proof["target_manifest"]["kind"] == "dir"
    assert (target / ENTRY / CAT / "state.json").read_text() == saved.read_text()
    assert saved.exists(), "retained source is not deleted during copy"
    assert not (target / "state" / "root_state.json").exists()
    assert manager.root_state_path.exists()


def test_recommendation_only_target_is_not_empty_and_cannot_be_overwritten(tmp_path):
    manager = make_manager(tmp_path)
    source = manager.app_docs_dir
    write_state(source, "source")
    target = tmp_path / "target" / "N.E.K.O"
    existing = write_state(target, "target")
    migration.create_pending_storage_migration(manager, source_root=source,
                                               target_root=target, selection_source="custom")
    result = migration.run_pending_storage_migration(manager)
    assert not result["completed"]
    assert existing.read_text() == "target"
    assert (source / ENTRY / CAT / "state.json").read_text() == "source"


def test_nested_parent_validation_and_private_leftover_registry(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    assert migration.entry_parents_are_real_directories(tmp_path, ENTRY)
    private = migration.private_cleanup_name(ENTRY, "a" * 12)
    assert migration.private_cleanup_entry_name(private) == ENTRY
    assert migration.private_cleanup_entry_name(private + "bad") is None


def test_existing_size_estimate_includes_only_recommendation_bytes(tmp_path):
    from main_routers.storage_location_router import _estimate_runtime_payload_bytes
    raw = b"only recommendation"
    state = write_state(tmp_path)
    state.write_bytes(raw)
    # An anchor-local file must remain outside the payload estimate.
    (tmp_path / "state" / "root_state.json").write_text(json.dumps({"mode": "normal"}))
    assert _estimate_runtime_payload_bytes(tmp_path) == len(raw)


def test_local_recommendation_not_in_cloudsave_managed_memory_contract():
    from utils.cloudsave_runtime._shared import MANAGED_MEMORY_FILENAMES
    assert "recommendation" not in MANAGED_MEMORY_FILENAMES
    assert "state.json" not in MANAGED_MEMORY_FILENAMES


@pytest.mark.parametrize("legacy", [False, True])
def test_old_checkpoint_without_recommendation_proof_preserves_retained_state(tmp_path, legacy):
    from main_routers.storage_location_router import _cleanup_retained_runtime_root
    source, target, anchor = (tmp_path / name for name in ("source", "target", "anchor"))
    anchor.mkdir()
    for root in (source, target):
        (root / "config").mkdir(parents=True)
        (root / "config" / "characters.json").write_text("{}", encoding="utf-8")
        write_state(root, "same state, no checkpoint evidence")
    proof = {"config": {"source_manifest": migration.snapshot_runtime_entry(source / "config"),
                         "target_manifest": migration.snapshot_runtime_entry(target / "config")}}
    remaining, retained = _cleanup_retained_runtime_root(
        source, current_root=target, anchor_root=anchor, target_root=target,
        copied_entries=proof, legacy_checkpoint=legacy)
    assert ENTRY in remaining
    assert retained
    assert (source / ENTRY / CAT / "state.json").exists()
    assert (target / ENTRY / CAT / "state.json").exists()


def test_new_checkpoint_proof_allows_retained_recommendation_cleanup(tmp_path):
    from main_routers.storage_location_router import _cleanup_retained_runtime_root
    source, target, anchor = (tmp_path / name for name in ("source", "target", "anchor"))
    anchor.mkdir()
    write_state(source)
    write_state(target)
    proof = {ENTRY: {"source_manifest": migration.snapshot_runtime_entry(source / ENTRY),
                     "target_manifest": migration.snapshot_runtime_entry(target / ENTRY)}}
    remaining, retained = _cleanup_retained_runtime_root(
        source, current_root=target, anchor_root=anchor, target_root=target, copied_entries=proof)
    assert not remaining
    assert not retained
    assert not source.exists()
    assert (target / ENTRY / CAT / "state.json").exists()


def test_v1_catch_up_copies_registered_recommendation_and_records_proof(tmp_path):
    from utils.storage.policy import save_storage_policy
    manager = make_manager(tmp_path)
    source, target = tmp_path / "source", tmp_path / "target"
    write_state(source)
    (target / "config").mkdir(parents=True)
    (target / "config" / "characters.json").write_text("{}", encoding="utf-8")
    save_storage_policy(manager, selected_root=target, selection_source="custom",
                        anchor_root=manager.anchor_root)
    migration.save_storage_migration(manager, {
        "version": 1, "status": "completed", "source_root": str(source),
        "target_root": str(target), "retained_source_root": str(source),
        "retained_source_mode": "manual_retention"})
    copied = migration.catch_up_v1_migration(manager, anchor_root=manager.anchor_root)
    assert ENTRY in copied
    assert (target / ENTRY / CAT / "state.json").read_text() == "recommendation only"
    checkpoint = migration.load_storage_migration(manager)
    assert ENTRY in migration.copy_evidence_entries(checkpoint["copied_entries"])
    assert checkpoint["version"] == 1, "historical checkpoint version is not rewritten"
    assert (source / ENTRY / CAT / "state.json").exists()
