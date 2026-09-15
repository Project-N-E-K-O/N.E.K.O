import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    create_pending_storage_migration,
    get_storage_migration_path,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    is_storage_migration_rollback_required,
    load_storage_migration,
    run_pending_storage_migration,
    StorageMigrationError,
)
from utils.storage_policy import load_storage_policy


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]


def _make_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

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
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=standard_root),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


@pytest.mark.unit
def test_create_pending_storage_migration_writes_anchor_checkpoint(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="recommended",
    )

    checkpoint_path = get_storage_migration_path(config_manager)
    assert checkpoint_path == tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_migration.json"
    assert checkpoint_path.is_file()

    reloaded_payload = load_storage_migration(config_manager)
    assert reloaded_payload == payload
    assert payload["source_root"] == str(config_manager.app_docs_dir)
    assert payload["target_root"] == str(target_root.resolve())
    assert payload["selection_source"] == "recommended"
    assert payload["version"] == 2
    assert payload["status"] == "pending"
    assert payload["migration_mode"] == "copy"
    assert payload["target_baseline"] == {}
    assert is_storage_migration_pending(payload) is True


@pytest.mark.unit
def test_storage_migration_defaults_to_configured_anchor_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    configured_anchor_root = tmp_path / "owner-exported-anchor" / "N.E.K.O"
    config_manager.anchor_root = configured_anchor_root
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )

    assert get_storage_migration_path(config_manager) == (
        configured_anchor_root / "state" / "storage_migration.json"
    )
    assert load_storage_migration(config_manager) == payload
    assert not (
        tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_migration.json"
    ).exists()


@pytest.mark.unit
def test_durable_replace_flushes_both_directory_entries_after_rename(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged" / "config"
    target = tmp_path / "target" / "config"
    calls = []
    monkeypatch.setattr(
        storage_migration_module.os,
        "replace",
        lambda left, right: calls.append(("replace", Path(left), Path(right))),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "fsync_directory_best_effort",
        lambda path: calls.append(("fsync", Path(path))),
    )

    storage_migration_module._durable_replace(source, target)

    assert calls == [
        ("replace", source, target),
        ("fsync", source.parent),
        ("fsync", target.parent),
    ]


@pytest.mark.unit
def test_is_storage_migration_pending_ignores_terminal_status():
    payload = {
        "status": STORAGE_MIGRATION_STATUS_FAILED,
        "source_root": "/tmp/source",
        "target_root": "/tmp/target",
    }

    assert is_storage_migration_pending(payload) is False


@pytest.mark.unit
def test_rollback_required_detection_fails_closed_for_damaged_checkpoint():
    payload = {
        "status": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    }

    assert is_storage_migration_rollback_required(payload) is True
    assert is_storage_migration_pending(payload) is False


@pytest.mark.unit
def test_load_storage_migration_distinguishes_missing_from_malformed_checkpoint(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    assert load_storage_migration(config_manager) is None

    checkpoint_path = get_storage_migration_path(config_manager)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text('{"status":', encoding="utf-8")

    with pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert caught.value.error_code == "migration_checkpoint_malformed"


@pytest.mark.unit
def test_load_storage_migration_propagates_read_failure_as_fail_closed_error(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch(
        "utils.storage.migration.read_json",
        side_effect=PermissionError("checkpoint permission denied"),
    ), pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert caught.value.error_code == "migration_checkpoint_unreadable"


@pytest.mark.unit
def test_retained_root_cleanup_rejects_paths_that_contain_protected_roots(tmp_path):
    retained_root = tmp_path / "retained"
    current_root = retained_root / "current" / "N.E.K.O"
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = retained_root / "target" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    current_root.mkdir(parents=True)
    anchor_root.mkdir(parents=True)
    target_root.mkdir(parents=True)

    assert not is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
    )
    assert not is_retained_root_cleanup_available(
        tmp_path,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
    )


@pytest.mark.unit
@pytest.mark.parametrize("protected_name", ("current", "anchor", "target"))
def test_retained_root_cleanup_rejects_paths_inside_protected_roots(
    tmp_path,
    protected_name,
):
    current_root = tmp_path / "current" / "N.E.K.O"
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = tmp_path / "target" / "N.E.K.O"
    protected_roots = {
        "current": current_root,
        "anchor": anchor_root,
        "target": target_root,
    }
    retained_root = protected_roots[protected_name] / "nested-retained"
    retained_root.mkdir(parents=True)
    for root in protected_roots.values():
        root.mkdir(parents=True, exist_ok=True)

    assert not is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        allow_anchor_root=True,
    )


@pytest.mark.unit
def test_run_pending_storage_migration_commits_policy_and_copies_runtime_entries(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "memory" / "A").mkdir(parents=True, exist_ok=True)
    (source_root / "card_faces").mkdir(parents=True, exist_ok=True)
    (source_root / "avatar_tools" / "local-12345678-1234-4123-8123-123456789abc").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (source_root / "memory" / "A" / "recent.json").write_text('[{"role":"user","content":"hi"}]', encoding="utf-8")
    (source_root / "card_faces" / "YUI.png").write_bytes(b"fake-png")
    (source_root / "card_faces" / "YUI.json").write_text('{"origin":"self"}', encoding="utf-8")
    (source_root / "avatar_tools" / "local-12345678-1234-4123-8123-123456789abc" / "record.json").write_text(
        '{"recordVersion":2}',
        encoding="utf-8",
    )

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is True
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_COMPLETED
    assert result["payload"]["retained_source_root"] == str(source_root.resolve())
    assert result["payload"]["retained_source_mode"] == "manual_retention"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"current":"A"}'
    assert (target_root / "memory" / "A" / "recent.json").read_text(encoding="utf-8") == '[{"role":"user","content":"hi"}]'
    assert (target_root / "card_faces" / "YUI.png").read_bytes() == b"fake-png"
    assert (target_root / "card_faces" / "YUI.json").read_text(encoding="utf-8") == '{"origin":"self"}'
    assert (
        target_root
        / "avatar_tools"
        / "local-12345678-1234-4123-8123-123456789abc"
        / "record.json"
    ).read_text(encoding="utf-8") == '{"recordVersion":2}'

    policy_payload = load_storage_policy(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    assert policy_payload["selected_root"] == str(target_root.resolve())

    root_state = config_manager.load_root_state()
    assert root_state["current_root"] == str(target_root.resolve())
    assert root_state["last_known_good_root"] == str(target_root.resolve())
    assert root_state["last_migration_result"].startswith("completed:")
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_requires_confirmation_for_existing_target_content(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    (target_root / "config" / "characters.json").write_text('{"existing":"B"}', encoding="utf-8")
    (target_root / "notes.txt").write_text("keep me", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    missing_confirmation_result = run_pending_storage_migration(config_manager)

    assert missing_confirmation_result["completed"] is False
    assert missing_confirmation_result["error_code"] == "target_confirmation_required"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"existing":"B"}'

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    confirmed_result = run_pending_storage_migration(config_manager)

    assert confirmed_result["completed"] is True
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"current":"A"}'
    assert (target_root / "notes.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_source_and_target_paths(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = source_root / "nested-target" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "paths_nested"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_apfs_case_alias_of_source(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_root.mkdir(parents=True, exist_ok=True)
    target_alias = source_root.with_name(source_root.name.swapcase())
    if not target_alias.exists() or not target_alias.samefile(source_root):
        pytest.skip("test volume is case-sensitive")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_alias,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_matches_source"


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_target_through_apfs_case_alias(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_root.mkdir(parents=True, exist_ok=True)
    source_alias = source_root.with_name(source_root.name.swapcase())
    if not source_alias.exists() or not source_alias.samefile(source_root):
        pytest.skip("test volume is case-sensitive")
    target_root = source_alias / "nested-target" / "N.E.K.O"

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "paths_nested"


@pytest.mark.unit
def test_run_pending_storage_migration_fails_closed_when_path_identity_is_uninspectable(
    tmp_path,
    monkeypatch,
):
    from utils.storage import policy as storage_policy_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    def deny_identity(_path):
        raise storage_policy_module.PathIdentityUnavailable("permission denied")

    monkeypatch.setattr(storage_policy_module, "_existing_path_identity", deny_identity)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_identity_uninspectable"


@pytest.mark.unit
def test_run_pending_storage_migration_marks_cleanup_pending_only_for_non_anchor_retained_root(tmp_path):
    config_manager = _make_config_manager(tmp_path)
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

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    root_state = config_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_marks_cleanup_pending_when_anchor_root_retains_runtime_entries(tmp_path):
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

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    root_state = config_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_marks_failure_and_recovers_to_source_root(tmp_path, monkeypatch):
    config_manager = _make_config_manager(tmp_path)
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

    def _boom(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "simulated copy failure")

    monkeypatch.setattr("utils.storage_migration._copy_runtime_entry", _boom)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED

    policy_payload = load_storage_policy(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    assert policy_payload["selected_root"] == str(source_root.resolve())
    assert policy_payload["selection_source"] == "recovered"

    root_state = config_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == str(source_root.resolve())
    assert root_state["last_migration_result"] == "failed:copy_failed"


@pytest.mark.unit
def test_run_pending_storage_migration_failure_uses_payload_source_before_normalization(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = tmp_path / "external-source" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    payload = load_storage_migration(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    persisted_source_root = payload["source_root"]
    original_normalize_runtime_root = storage_migration_module.normalize_runtime_root

    def fail_for_payload_source(value):
        if str(value) == persisted_source_root:
            raise ValueError("simulated source path normalization failure")
        return original_normalize_runtime_root(value)

    monkeypatch.setattr(storage_migration_module, "normalize_runtime_root", fail_for_payload_source)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert result["payload"]["backup_root"] == persisted_source_root

    root_state = config_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == persisted_source_root
    assert root_state["last_known_good_root"] == persisted_source_root
    assert root_state["last_migration_source"] == persisted_source_root
    assert root_state["last_migration_backup"] == persisted_source_root


@pytest.mark.unit
def test_run_pending_storage_migration_copies_every_selected_root_data_class(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    representative_files = {
        "pngtuber/avatar.png": b"avatar",
        "jukebox/library.json": b"{}",
        "card_faces/YUI.png": b"face",
        "state/game_scores/badminton_scores.db": b"sqlite-score",
        "embedding_models/model.bin": b"embedding",
        "runtimes/rapidocr/runtime.bin": b"runtime",
        "plugin-runtime/plugin-installs/task.json": b"{}",
    }
    for relative_path, content in representative_files.items():
        source_path = source_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    for relative_path, content in representative_files.items():
        assert (target_root / relative_path).read_bytes() == content


@pytest.mark.unit
def test_run_pending_storage_migration_rechecks_space_with_safety_margin(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"source")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": source_file.stat().st_size})(),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_detects_same_size_copy_corruption(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "memory" / "history.bin"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"AAAA")
    original_copy = storage_migration_module._copy_runtime_entry

    def corrupt_copy(source_path, target_path):
        original_copy(source_path, target_path)
        (target_path / "history.bin").write_bytes(b"BBBB")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", corrupt_copy)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "verification_failed"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_source_change_after_entry_copy(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "memory" / "history.bin"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"BEFORE")

    def mutate_source_after_copy(**_kwargs):
        source_file.write_bytes(b"AFTER")

    monkeypatch.setattr(
        storage_migration_module,
        "_rewrite_migrated_runtime_config_paths",
        mutate_source_after_copy,
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "source_changed_during_migration"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_source_symlink(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external"
    external_root.mkdir()
    (external_root / "secret.txt").write_text("outside", encoding="utf-8")
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    try:
        (source_root / "config" / "linked").symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_symlink_unsupported"
    assert not (target_root / "config" / "linked" / "secret.txt").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_source_state_symlink_with_missing_leaf(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_state = tmp_path / "external-source-state"
    external_state.mkdir()
    external_marker = external_state / "keep.txt"
    external_marker.write_text("KEEP", encoding="utf-8")
    source_state = source_root / "state"
    if source_state.exists():
        shutil.rmtree(source_state)
    try:
        source_state.symlink_to(external_state, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "runtime_entry_path_unsafe"
    assert external_marker.read_text(encoding="utf-8") == "KEEP"
    assert not (external_state / "game_scores").exists()
    assert not (target_root / "state" / "game_scores").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_state_symlink_before_missing_leaf_publish(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_state = tmp_path / "external-target-state"
    source_score = source_root / "state" / "game_scores" / "score.db"
    source_score.parent.mkdir(parents=True)
    source_score.write_bytes(b"SOURCE")
    external_state.mkdir()
    external_marker = external_state / "keep.txt"
    external_marker.write_text("KEEP", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    target_root.mkdir(parents=True)
    try:
        (target_root / "state").symlink_to(external_state, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "runtime_entry_path_unsafe"
    assert external_marker.read_text(encoding="utf-8") == "KEEP"
    assert not (external_state / "game_scores").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_replaced_by_symlink(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external-target"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    external_root.mkdir()
    target_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_root.symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_path_symlink_unsupported"
    assert list(external_root.iterdir()) == []


@pytest.mark.unit
def test_run_pending_storage_migration_does_not_follow_symlinked_transaction_root(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external-transaction"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    external_root.mkdir()
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        transaction_root.symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_symlink_unsupported"
    assert transaction_root.is_symlink()
    assert list(external_root.iterdir()) == []


@pytest.mark.unit
def test_transaction_staging_lives_on_selected_target_filesystem(tmp_path):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "mounted-volume" / "N.E.K.O"
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        "a" * 32,
    )

    assert transaction_root.parent == target_root


@pytest.mark.unit
def test_pending_migration_never_deletes_unowned_transaction_path(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir(parents=True)
    sentinel = transaction_root / "user-content.txt"
    sentinel.write_text("KEEP", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_pending_migration_recovers_transaction_created_before_copying_checkpoint(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    original_persist = storage_migration_module._persist_migration_payload
    interrupted = {"value": False}

    def interrupt_before_copying_checkpoint(*args, **kwargs):
        if (
            kwargs.get("status") == storage_migration_module.STORAGE_MIGRATION_STATUS_COPYING
            and not interrupted["value"]
        ):
            interrupted["value"] = True
            raise SimulatedProcessLoss
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        interrupt_before_copying_checkpoint,
    )
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    crashed_checkpoint = load_storage_migration(config_manager)
    assert crashed_checkpoint["status"] == "preflight"
    assert crashed_checkpoint["transaction_root"] == str(transaction_root)
    assert transaction_root.is_dir()

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        original_persist,
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "SOURCE"
    assert not transaction_root.exists()


@pytest.mark.unit
def test_run_pending_storage_migration_restores_existing_target_when_commit_fails(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    monkeypatch.setattr(
        storage_migration_module,
        "save_storage_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert target_file.read_text(encoding="utf-8") == "TARGET"


@pytest.mark.unit
def test_recovery_metadata_write_failures_keep_transaction_and_force_source_layout(
    tmp_path,
    monkeypatch,
):
    from utils import cloudsave_runtime as cloudsave_runtime_module
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )

    real_save_policy = storage_migration_module.save_storage_policy
    policy_calls = 0

    def fail_recovery_policy(*args, **kwargs):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 1:
            return real_save_policy(*args, **kwargs)
        raise OSError("source policy unavailable")

    real_save_migration = storage_migration_module.save_storage_migration

    def fail_terminal_checkpoint(manager, payload, **kwargs):
        if str(payload.get("status") or "") in {"failed", "rollback_required"}:
            raise OSError("terminal checkpoint unavailable")
        return real_save_migration(manager, payload, **kwargs)

    monkeypatch.setattr(storage_migration_module, "save_storage_policy", fail_recovery_policy)
    monkeypatch.setattr(storage_migration_module, "save_storage_migration", fail_terminal_checkpoint)
    monkeypatch.setattr(
        cloudsave_runtime_module,
        "set_root_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("root state unavailable")),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["force_recovery_layout"] is True
    assert result["recovery_policy_persisted"] is False
    assert result["recovery_root_state_persisted"] is False
    assert result["recovery_checkpoint_persisted"] is False
    assert result["source_root"] == str(source_root.resolve())
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists(), "uncommitted recovery evidence must survive"
    assert load_storage_migration(config_manager)["status"] == "committing"
    assert load_storage_policy(config_manager)["selected_root"] == str(target_root.resolve())


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_changed_after_confirmation(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "jukebox").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "jukebox" / "library.json"
    target_file.write_text("BEFORE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    target_file.write_text("AFTER", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_changed_since_confirmation"
    assert target_file.read_text(encoding="utf-8") == "AFTER"


@pytest.mark.unit
def test_publish_preserves_target_created_after_final_snapshot(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    real_snapshot_entries = storage_migration_module._snapshot_runtime_entries
    target_snapshot_calls = 0
    concurrent_file = target_root / "config" / "external.json"

    def _snapshot_then_create_target(root):
        nonlocal target_snapshot_calls
        snapshot = real_snapshot_entries(root)
        if Path(root) == target_root.resolve():
            target_snapshot_calls += 1
            if target_snapshot_calls == 2:
                concurrent_file.parent.mkdir(parents=True, exist_ok=True)
                concurrent_file.write_text("EXTERNAL", encoding="utf-8")
        return snapshot

    monkeypatch.setattr(
        storage_migration_module,
        "_snapshot_runtime_entries",
        _snapshot_then_create_target,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert concurrent_file.read_text(encoding="utf-8") == "EXTERNAL"
    assert not (target_root / "config" / "characters.json").exists()


@pytest.mark.unit
def test_jukebox_only_target_requires_overwrite_confirmation(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "jukebox").mkdir(parents=True, exist_ok=True)
    (source_root / "jukebox" / "library.json").write_text("SOURCE", encoding="utf-8")
    target_file = target_root / "jukebox" / "library.json"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("TARGET", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_confirmation_required"
    assert target_file.read_text(encoding="utf-8") == "TARGET"


@pytest.mark.unit
def test_selection_source_recovered_cannot_adopt_target_instead_of_copying(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recovered",
        confirmed_existing_target_content=True,
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert target_file.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_interrupted_publish_is_rolled_back_before_retry(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    target_file = target_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    transaction_root = storage_migration_module._transaction_root_for(target_root, payload["txid"])
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    backup_entry = transaction_root / "backup" / "config"
    staged_file.parent.mkdir(parents=True)
    backup_entry.parent.mkdir(parents=True)
    shutil.copy2(source_file, staged_file)
    os.replace(target_root / "config", backup_entry)
    os.replace(transaction_root / "staged" / "config", target_root / "config")
    payload.update(
        {
            "status": "publishing",
            "original_target_entries": ["config"],
            "publish_entry_names": ["config"],
            "publish_entry_snapshots": {
                "config": storage_migration_module._snapshot_path(target_root / "config"),
            },
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after crash rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


def _prepare_interrupted_publish(tmp_path, *, legacy_transaction_layout=False):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    target_file = target_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = (
        storage_migration_module._legacy_transaction_root_for(target_root, payload["txid"])
        if legacy_transaction_layout
        else storage_migration_module._transaction_root_for(target_root, payload["txid"])
    )
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    backup_entry = transaction_root / "backup" / "config"
    staged_file.parent.mkdir(parents=True)
    backup_entry.parent.mkdir(parents=True)
    shutil.copy2(source_file, staged_file)
    os.replace(target_root / "config", backup_entry)
    os.replace(transaction_root / "staged" / "config", target_root / "config")
    payload.update(
        {
            "status": "publishing",
            "original_target_entries": ["config"],
            "publish_entry_names": ["config"],
            "publish_entry_snapshots": {
                "config": storage_migration_module._snapshot_path(target_root / "config"),
            },
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)
    return config_manager, target_root, target_file, transaction_root, backup_entry, payload


@pytest.mark.unit
def test_interrupted_publish_from_legacy_transaction_layout_is_recovered(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path,
        legacy_transaction_layout=True,
    )

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after legacy rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_missing_backup_stays_rollback_required_with_evidence(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(backup_entry)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_damaged_backup_stays_rollback_required_without_overwriting_target(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    (backup_entry / "characters.json").write_text("DAMAGED", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_concurrent_target_write_fails_final_baseline_check(tmp_path):
    config_manager, target_root, target_file, transaction_root, _, _ = _prepare_interrupted_publish(tmp_path)
    concurrent_file = target_root / "jukebox" / "library.json"
    concurrent_file.parent.mkdir(parents=True)
    concurrent_file.write_text("CONCURRENT", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert concurrent_file.read_text(encoding="utf-8") == "CONCURRENT"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_concurrent_rewrite_is_not_overwritten_by_rollback(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    target_file.write_text("CONCURRENT", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "CONCURRENT"
    assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_without_target_baseline_preserves_transaction(tmp_path):
    config_manager, _, target_file, transaction_root, _, payload = _prepare_interrupted_publish(tmp_path)
    payload.pop("target_baseline", None)
    from utils import storage_migration as storage_migration_module

    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_baseline_missing"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_with_missing_source_and_manifest_stays_recoverable(tmp_path):
    config_manager, _, target_file, transaction_root, _, payload = _prepare_interrupted_publish(tmp_path)
    payload.pop("publish_entry_snapshots", None)
    from utils import storage_migration as storage_migration_module

    storage_migration_module.save_storage_migration(config_manager, payload)
    shutil.rmtree(config_manager.app_docs_dir)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_without_transaction_evidence_stays_rollback_required(tmp_path):
    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(transaction_root)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_transaction_missing"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_interrupted_publish_accepts_already_restored_baseline_before_retry(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, target_root, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(target_root / "config")
    os.replace(backup_entry, target_root / "config")

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after verified idempotent rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()
