import json
import os
import shutil
import stat
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_PREFLIGHT,
    STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
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
def test_owned_transaction_cleanup_removes_read_only_tree(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    locked_dir = transaction_root / "backup" / "config"
    locked_dir.mkdir(parents=True)
    locked_file = locked_dir / "characters.json"
    locked_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(locked_file, stat.S_IRUSR)
    # No execute permission means the rmtree callback cannot even lstat a
    # child until it repairs the owned parent directory first.
    os.chmod(locked_dir, stat.S_IRUSR)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
    finally:
        if locked_dir.exists():
            os.chmod(locked_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        if locked_file.exists():
            os.chmod(locked_file, stat.S_IRUSR | stat.S_IWUSR)

    assert not transaction_root.exists()


@pytest.mark.unit
def test_owned_transaction_cleanup_repairs_nested_unsearchable_directories(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    upper = transaction_root / "backup" / "locked-upper"
    lower = upper / "locked-lower"
    lower.mkdir(parents=True)
    locked_file = lower / "characters.json"
    locked_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(locked_file, stat.S_IRUSR)
    os.chmod(lower, stat.S_IRUSR)
    os.chmod(upper, stat.S_IRUSR)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
    finally:
        for candidate in (upper, lower):
            if candidate.exists():
                os.chmod(candidate, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        if locked_file.exists():
            os.chmod(locked_file, stat.S_IRUSR | stat.S_IWUSR)

    assert not transaction_root.exists()


@pytest.mark.unit
def test_owned_transaction_cleanup_never_chmods_a_link_target(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    external_file = external / "keep.txt"
    external_file.write_text("keep", encoding="utf-8")
    original_mode = stat.S_IRUSR | stat.S_IXUSR
    os.chmod(external, original_mode)
    observed_mode = stat.S_IMODE(external.stat().st_mode)
    (transaction_root / "external-link").symlink_to(external, target_is_directory=True)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
        assert stat.S_IMODE(external.stat().st_mode) == observed_mode
        assert external_file.read_text(encoding="utf-8") == "keep"
    finally:
        os.chmod(external, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@pytest.mark.unit
def test_directory_publish_never_replaces_a_late_empty_directory(tmp_path):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged"
    target = tmp_path / "published"
    source.mkdir()
    (source / "data.json").write_text('{"source": true}', encoding="utf-8")
    target.mkdir()
    target_identity = target.stat()

    with pytest.raises(OSError):
        storage_migration_module._durable_publish_without_replacing(source, target)

    current_identity = target.stat()
    assert (current_identity.st_dev, current_identity.st_ino) == (
        target_identity.st_dev,
        target_identity.st_ino,
    )
    assert list(target.iterdir()) == []
    assert (source / "data.json").is_file()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX exclusive rename contract")
def test_posix_file_publish_is_one_atomic_no_replace_rename(tmp_path):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged.json"
    target = tmp_path / "published.json"
    source.write_text("SOURCE", encoding="utf-8")

    storage_migration_module._durable_publish_without_replacing(source, target)

    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "SOURCE"

    contender = tmp_path / "contender.json"
    contender.write_text("CONTENDER", encoding="utf-8")
    with pytest.raises(OSError):
        storage_migration_module._durable_publish_without_replacing(contender, target)
    assert contender.read_text(encoding="utf-8") == "CONTENDER"
    assert target.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="legacy POSIX hard-link recovery")
@pytest.mark.parametrize("original_target", (False, True))
def test_rollback_recovers_legacy_file_publish_link_unlink_crash(
    tmp_path,
    original_target,
):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "target"
    transaction_root = target_root / ".neko-storage-migration-test"
    staged_path = transaction_root / "staged" / "config"
    backup_path = transaction_root / "backup" / "config"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_text("SOURCE", encoding="utf-8")
    publish_snapshot = storage_migration_module._snapshot_path(staged_path)
    target_root.mkdir(exist_ok=True)
    os.link(staged_path, target_root / "config")

    original_entries = []
    target_baseline = {}
    if original_target:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text("TARGET", encoding="utf-8")
        original_entries = ["config"]
        target_baseline = {
            "config": storage_migration_module._snapshot_path(backup_path)
        }

    storage_migration_module._rollback_published_entries(
        target_root,
        transaction_root,
        original_entries,
        ["config"],
        target_baseline,
        {"config": publish_snapshot},
    )

    assert staged_path.read_text(encoding="utf-8") == "SOURCE"
    if original_target:
        assert (target_root / "config").read_text(encoding="utf-8") == "TARGET"
    else:
        assert not (target_root / "config").exists()


@pytest.mark.unit
def test_rollback_rejects_equal_file_content_at_different_inode(tmp_path):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "target"
    transaction_root = target_root / ".neko-storage-migration-test"
    staged_path = transaction_root / "staged" / "config"
    target_path = target_root / "config"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_text("SOURCE", encoding="utf-8")
    target_root.mkdir(exist_ok=True)
    target_path.write_text("SOURCE", encoding="utf-8")
    publish_snapshot = storage_migration_module._snapshot_path(staged_path)

    with pytest.raises(StorageMigrationError, match="未记录"):
        storage_migration_module._rollback_published_entries(
            target_root,
            transaction_root,
            [],
            ["config"],
            {},
            {"config": publish_snapshot},
        )

    assert staged_path.read_text(encoding="utf-8") == "SOURCE"
    assert target_path.read_text(encoding="utf-8") == "SOURCE"


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
        "_fsync_migration_directory",
        lambda path: calls.append(("fsync", Path(path))),
    )

    storage_migration_module._durable_replace(source, target)

    assert calls == [
        ("replace", source, target),
        ("fsync", target.parent),
        ("fsync", source.parent),
    ]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barrier")
def test_durable_publish_propagates_a_directory_flush_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged" / "config"
    target = tmp_path / "target" / "config"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_text("SOURCE", encoding="utf-8")
    real_fsync = storage_migration_module._fsync_migration_directory

    def fail_target_directory(path):
        if Path(path) == target.parent:
            raise StorageMigrationError(
                "target_flush_failed",
                "simulated target directory flush failure",
            )
        return real_fsync(Path(path))

    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        fail_target_directory,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._durable_publish_without_replacing(source, target)

    assert caught.value.error_code == "target_flush_failed"
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "SOURCE"


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
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barrier")
def test_delete_storage_migration_propagates_checkpoint_unlink_flush_failure(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _DummyConfigManager(tmp_path)
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=tmp_path / "target" / "N.E.K.O",
        selection_source="custom",
    )
    migration_path = get_storage_migration_path(config_manager)
    real_fsync = storage_migration_module.os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("checkpoint unlink flush failed")
        return real_fsync(fd)

    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module.delete_storage_migration(config_manager)

    assert caught.value.error_code == "target_flush_failed"
    assert not migration_path.exists()


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
@pytest.mark.skipif(os.name == "nt", reason="POSIX file and directory fsync semantics")
def test_fsync_staged_tree_flushes_nested_directories_from_leaf_to_root(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    nested_root = staged_root / "state" / "game_scores"
    nested_root.mkdir(parents=True)
    (staged_root / "top-level-state.json").write_text("{}", encoding="utf-8")
    (nested_root / "scores.db").write_bytes(b"score")
    flushed_directories = []

    monkeypatch.setattr(storage_migration_module.sys, "platform", "linux")
    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        lambda path: flushed_directories.append(Path(path)),
    )

    storage_migration_module._fsync_staged_tree(staged_root)

    assert flushed_directories == [nested_root, staged_root / "state", staged_root]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_posix_fsync_staged_tree_propagates_directory_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    staged_root.mkdir(parents=True)

    def fail_fsync(_fd):
        raise OSError("directory flush failed")

    monkeypatch.setattr(storage_migration_module.sys, "platform", "linux")
    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_fsync)

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._fsync_staged_tree(staged_root)

    assert exc_info.value.error_code == "target_flush_failed"


@pytest.mark.unit
def test_new_target_root_flushes_every_created_parent_boundary(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "new-volume-folder" / "nested" / "N.E.K.O"
    flushed = []
    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        lambda path: flushed.append(Path(path)),
    )

    storage_migration_module._ensure_target_root_writable(target_root)

    assert target_root.is_dir()
    assert flushed == [
        target_root.parent,
        target_root.parent.parent,
        tmp_path,
    ]


@pytest.mark.unit
def test_staged_copy_flushes_before_and_after_restoring_open_file_metadata(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source" / "readonly.json"
    staged_file = tmp_path / "transaction" / "staged" / "config" / "readonly.json"
    source_file.parent.mkdir(parents=True)
    staged_file.parent.mkdir(parents=True)
    source_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(source_file, stat.S_IRUSR)
    source_mode = stat.S_IMODE(source_file.stat().st_mode)
    real_fsync = os.fsync
    real_copy_metadata = storage_migration_module._copy_open_file_metadata
    events = []

    def record_windows_style_fsync(fd):
        os.write(fd, b"")
        events.append("fsync")
        real_fsync(fd)

    def record_copy_metadata(source_fd, target_fd, metadata):
        events.append("metadata")
        return real_copy_metadata(source_fd, target_fd, metadata)

    monkeypatch.setattr(storage_migration_module.os, "fsync", record_windows_style_fsync)
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        record_copy_metadata,
    )

    storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert events == ["fsync", "metadata", "fsync"]
    assert stat.S_IMODE(staged_file.stat().st_mode) == source_mode
    assert staged_file.read_text(encoding="utf-8") == '{"preserved": true}'


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX name replacement injection")
def test_staged_copy_metadata_never_follows_a_replaced_target_name(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.json"
    staged_file = tmp_path / "staged.json"
    external_file = tmp_path / "external.json"
    source_file.write_text("source", encoding="utf-8")
    external_file.write_text("external", encoding="utf-8")
    os.chmod(source_file, 0o600)
    os.chmod(external_file, 0o644)
    external_mode = stat.S_IMODE(external_file.stat().st_mode)
    real_copy_metadata = storage_migration_module._copy_open_file_metadata

    def replace_name_then_copy_metadata(source_fd, target_fd, metadata):
        staged_file.unlink()
        staged_file.symlink_to(external_file)
        return real_copy_metadata(source_fd, target_fd, metadata)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        replace_name_then_copy_metadata,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert caught.value.error_code == "staging_entry_changed"
    assert staged_file.is_symlink()
    assert external_file.read_text(encoding="utf-8") == "external"
    assert stat.S_IMODE(external_file.stat().st_mode) == external_mode


@pytest.mark.unit
@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX unlink permits a stable replacement while the original handle remains open",
)
def test_staged_copy_failure_preserves_a_late_target_competitor(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.json"
    staged_file = tmp_path / "staged.json"
    source_file.write_text("source", encoding="utf-8")

    def replace_target_then_fail(_source_fd, _target_fd, _metadata):
        staged_file.unlink()
        staged_file.write_text("late-winner", encoding="utf-8")
        raise OSError("metadata restore failed")

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        replace_target_then_fail,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert caught.value.error_code == "target_flush_failed"
    assert staged_file.read_text(encoding="utf-8") == "late-winner"


@pytest.mark.unit
def test_staged_copy_rejects_fifo_without_blocking(tmp_path):
    from utils import storage_migration as storage_migration_module

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    source_fifo = tmp_path / "source.fifo"
    staged_file = tmp_path / "staged.bin"
    os.mkfifo(source_fifo)
    outcome = []

    def copy_fifo():
        try:
            storage_migration_module._copy_staged_file_durably(source_fifo, staged_file)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=copy_fifo, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive(), "FIFO copy must fail instead of blocking maintenance"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StorageMigrationError)
    assert outcome[0].error_code == "path_type_unsupported"
    assert not staged_file.exists()


@pytest.mark.unit
def test_staged_copy_does_not_block_when_source_becomes_fifo_before_open(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    if os.name == "nt" or not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO creation is unavailable")
    source_file = tmp_path / "source.bin"
    staged_file = tmp_path / "staged.bin"
    source_file.write_bytes(b"regular-before-open")
    real_open = storage_migration_module.os.open
    opened_flags = []
    replaced = False

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if Path(path) == source_file and not replaced:
            replaced = True
            source_file.unlink()
            os.mkfifo(source_file)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", replace_with_fifo_before_open)
    outcome = []

    def copy_raced_source():
        try:
            storage_migration_module._copy_staged_file_durably(source_file, staged_file)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=copy_raced_source, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Make a regressed blocking reader finish so it cannot leak into later tests.
        writer_fd = real_open(source_file, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    assert not worker.is_alive(), "opening a raced FIFO must be non-blocking"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StorageMigrationError)
    assert outcome[0].error_code == "path_type_unsupported"
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW
    assert not staged_file.exists()


@pytest.mark.unit
def test_staged_copy_rejects_source_symlink_without_copying_target(tmp_path):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source-link"
    external_file = tmp_path / "external.bin"
    staged_file = tmp_path / "staged.bin"
    external_file.write_bytes(b"external")
    try:
        source_file.symlink_to(external_file)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert exc_info.value.error_code == "path_symlink_unsupported"
    assert not staged_file.exists()


@pytest.mark.unit
def test_staged_copy_exclusive_target_preserves_a_late_winner(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.bin"
    staged_file = tmp_path / "staged.bin"
    source_file.write_bytes(b"source")
    real_open = storage_migration_module.os.open
    occupied = False
    target_flags_seen = []

    def occupy_target_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal occupied
        if Path(path) == staged_file and flags & os.O_CREAT and not occupied:
            occupied = True
            target_flags_seen.append(flags)
            staged_file.write_bytes(b"late-winner")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", occupy_target_before_open)

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert exc_info.value.error_code == "staging_entry_exists"
    assert target_flags_seen and target_flags_seen[0] & os.O_RDWR
    assert staged_file.read_bytes() == b"late-winner"


@pytest.mark.unit
def test_windows_fsync_staged_tree_does_not_reopen_durable_copies_read_only(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    staged_root.mkdir(parents=True)
    staged_file = staged_root / "readonly.json"
    staged_file.write_text("{}", encoding="utf-8")
    os.chmod(staged_file, stat.S_IRUSR)
    flushed_directories = []

    monkeypatch.setattr(storage_migration_module.sys, "platform", "win32")
    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        lambda _fd: pytest.fail("Windows must not reopen a copied file read-only for fsync"),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "fsync_directory_best_effort",
        lambda path: flushed_directories.append(Path(path)),
    )

    storage_migration_module._fsync_staged_tree(staged_root)

    assert flushed_directories == [staged_root]


@pytest.mark.unit
def test_run_pending_storage_migration_flushes_staged_root_before_verifying_checkpoint(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "state" / "game_scores" / "scores.db"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"score")
    events = []
    real_persist = storage_migration_module._persist_migration_payload

    def record_staged_flush(path):
        events.append(("flush", Path(path)))

    def record_persist(*args, **kwargs):
        events.append(("checkpoint", kwargs.get("status")))
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_fsync_staged_tree", record_staged_flush)
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", record_persist)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    flush_events = [event for event in events if event[0] == "flush"]
    assert len(flush_events) == 1
    assert flush_events[0][1].name == "staged"
    assert events.index(flush_events[0]) < events.index(
        ("checkpoint", storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING)
    )


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_posix_directory_flush_failure_never_reaches_verifying_or_publishing(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "state" / "game_scores" / "scores.db"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"source-score")
    persisted_statuses = []
    real_fsync = storage_migration_module.os.fsync
    real_persist = storage_migration_module._persist_migration_payload

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory flush failed")
        return real_fsync(fd)

    def record_persist(*args, **kwargs):
        if kwargs.get("status"):
            persisted_statuses.append(kwargs["status"])
        return real_persist(*args, **kwargs)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_directory_fsync)
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", record_persist)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_flush_failed"
    assert storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING not in persisted_statuses
    assert storage_migration_module.STORAGE_MIGRATION_STATUS_PUBLISHING not in persisted_statuses
    assert source_file.read_bytes() == b"source-score"
    assert not (target_root / "state").exists()


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
def test_checkpoint_bound_transaction_path_without_marker_preserves_late_occupant(
    tmp_path,
):
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
    payload["transaction_root"] = str(transaction_root)
    storage_migration_module.save_storage_migration(config_manager, payload)

    transaction_root.mkdir(parents=True)
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_completed_checkpoint_preserves_unmarked_transaction_path(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
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
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    payload.update(
        {
            "status": "completed",
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is False
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
@pytest.mark.parametrize("marker_state", ("mismatch", "symlink"))
def test_checkpoint_bound_transaction_rejects_untrusted_marker(
    tmp_path,
    marker_state,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
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
    marker = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    if marker_state == "mismatch":
        marker.write_text(
            json.dumps(
                {
                    "version": 1,
                    "txid": payload["txid"],
                    "owner_token": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
    else:
        external = tmp_path / "external-marker.json"
        external.write_text("{}", encoding="utf-8")
        try:
            marker.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("marker symlinks are unavailable")
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    payload["transaction_root"] = str(transaction_root)
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_transaction_marker_write_failure_never_publishes_unowned_root(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
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
    monkeypatch.setattr(
        storage_migration_module,
        "_write_transaction_owner_marker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker denied")),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert not transaction_root.exists()
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_windows_reparse_metadata_is_never_treated_as_owned_file():
    from utils import storage_migration as storage_migration_module

    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
    )

    assert storage_migration_module._is_link_like_metadata(metadata) is True


@pytest.mark.unit
def test_windows_open_file_metadata_never_combines_normal_with_readonly():
    from utils import storage_migration as storage_migration_module

    readonly = 0x00000001
    normal = 0x00000080
    archive = 0x00000020

    assert storage_migration_module._windows_copied_file_attributes(normal, readonly) == readonly
    assert storage_migration_module._windows_copied_file_attributes(normal, 0) == normal
    assert storage_migration_module._windows_copied_file_attributes(
        archive | normal,
        readonly,
    ) == archive | readonly


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
        if str(payload.get("status") or "") in {
            "failed",
            "recovery_required",
            "rollback_required",
        }:
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
    source_runtime_baseline = storage_migration_module._snapshot_runtime_entries(
        source_root
    )
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
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
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
            "source_runtime_baseline": source_runtime_baseline,
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
    source_runtime_baseline = storage_migration_module._snapshot_runtime_entries(
        source_root
    )
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
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
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
            "source_runtime_baseline": source_runtime_baseline,
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)
    return config_manager, target_root, target_file, transaction_root, backup_entry, payload


@pytest.mark.unit
@pytest.mark.parametrize("marker_state", ("missing", "mismatch", "symlink"))
def test_interrupted_publish_with_untrusted_marker_stays_rollback_required(
    tmp_path,
    marker_state,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, target_file, transaction_root, backup_entry, payload = (
        _prepare_interrupted_publish(tmp_path)
    )
    marker = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    if marker_state == "missing":
        marker.unlink()
    elif marker_state == "mismatch":
        marker.write_text(
            json.dumps(
                {
                    "version": 1,
                    "txid": payload["txid"],
                    "owner_token": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
    else:
        marker.unlink()
        external = tmp_path / "external-owner-marker.json"
        external.write_text("{}", encoding="utf-8")
        try:
            marker.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("marker symlinks are unavailable")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists()


@pytest.mark.unit
@pytest.mark.parametrize("crash_step", ("published_to_staged", "backup_to_target"))
def test_interrupted_publish_rollback_rename_steps_are_crash_recoverable(
    tmp_path,
    monkeypatch,
    crash_step,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, target_root, target_file, transaction_root, backup_entry, _ = (
        _prepare_interrupted_publish(tmp_path)
    )
    staged_entry = transaction_root / "staged" / "config"
    original_publish = storage_migration_module._durable_publish_without_replacing
    interrupted = {"value": False}

    def _interrupt_rollback_rename(source, target):
        original_publish(source, target)
        is_selected_step = (
            crash_step == "published_to_staged"
            and source == target_root / "config"
            and target == staged_entry
        ) or (
            crash_step == "backup_to_target"
            and source == backup_entry
            and target == target_root / "config"
        )
        if is_selected_step and not interrupted["value"]:
            interrupted["value"] = True
            raise SimulatedProcessLoss

    monkeypatch.setattr(
        storage_migration_module,
        "_durable_publish_without_replacing",
        _interrupt_rollback_rename,
    )
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    assert load_storage_migration(config_manager)["status"] == "publishing"
    assert staged_entry.exists()
    if crash_step == "published_to_staged":
        assert not target_file.exists()
        assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    else:
        assert target_file.read_text(encoding="utf-8") == "TARGET"
        assert not backup_entry.exists()

    monkeypatch.setattr(
        storage_migration_module,
        "_durable_publish_without_replacing",
        original_publish,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after rollback")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "crash_step",
    ("before_transaction_cleanup", "during_transaction_cleanup", "after_cleanup"),
)
def test_rollback_completion_checkpoint_precedes_transaction_cleanup(
    tmp_path,
    monkeypatch,
    crash_step,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    original_remove = storage_migration_module._remove_transaction_root_if_owned
    original_remove_existing = storage_migration_module._remove_existing_path
    original_persist = storage_migration_module._persist_migration_payload
    interrupted = {"value": False}
    preflight_writes = {"count": 0}

    if crash_step == "before_transaction_cleanup":
        def _interrupt_before_cleanup(*args, **kwargs):
            if not interrupted["value"]:
                interrupted["value"] = True
                raise SimulatedProcessLoss
            return original_remove(*args, **kwargs)

        monkeypatch.setattr(
            storage_migration_module,
            "_remove_transaction_root_if_owned",
            _interrupt_before_cleanup,
        )
    elif crash_step == "during_transaction_cleanup":
        def _interrupt_during_cleanup(path):
            quarantine = storage_migration_module._private_directory_quarantine_path(
                transaction_root
            )
            if (
                Path(path).parent == quarantine
                and Path(path).name
                != storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
                and not interrupted["value"]
            ):
                interrupted["value"] = True
                raise SimulatedProcessLoss
            return original_remove_existing(path)

        monkeypatch.setattr(
            storage_migration_module,
            "_remove_existing_path",
            _interrupt_during_cleanup,
        )
    else:
        def _interrupt_after_cleanup(*args, **kwargs):
            if kwargs.get("status") == STORAGE_MIGRATION_STATUS_PREFLIGHT:
                preflight_writes["count"] += 1
                if preflight_writes["count"] == 2:
                    raise SimulatedProcessLoss
            return original_persist(*args, **kwargs)

        monkeypatch.setattr(
            storage_migration_module,
            "_persist_migration_payload",
            _interrupt_after_cleanup,
        )

    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    checkpoint = load_storage_migration(config_manager)
    assert checkpoint["status"] == STORAGE_MIGRATION_STATUS_PREFLIGHT
    assert checkpoint["publish_entry_names"] == []
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists() is (crash_step == "before_transaction_cleanup")
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    assert quarantine.exists() is (crash_step == "during_transaction_cleanup")

    monkeypatch.setattr(
        storage_migration_module,
        "_remove_transaction_root_if_owned",
        original_remove,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        original_persist,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_remove_existing_path",
        original_remove_existing,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after rollback")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not quarantine.exists()


@pytest.mark.unit
def test_transaction_cleanup_recovers_crash_after_owner_marker_is_deleted(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, _, target_file, transaction_root, _, _ = (
        _prepare_interrupted_publish(tmp_path)
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    quarantine_marker = (
        quarantine / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    )
    real_rmdir = Path.rmdir

    def _interrupt_empty_quarantine_rmdir(path):
        if path == quarantine and not quarantine_marker.exists():
            raise SimulatedProcessLoss
        return real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", _interrupt_empty_quarantine_rmdir)
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    checkpoint = load_storage_migration(config_manager)
    assert checkpoint["status"] == STORAGE_MIGRATION_STATUS_PREFLIGHT
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []

    monkeypatch.setattr(Path, "rmdir", real_rmdir)
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after quarantine recovery")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not quarantine.exists()


@pytest.mark.unit
@pytest.mark.parametrize("replacement_kind", ("nonempty_directory", "symlink"))
def test_transaction_cleanup_preserves_unowned_markerless_quarantine(
    tmp_path,
    replacement_kind,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    if replacement_kind == "nonempty_directory":
        quarantine.mkdir(parents=True)
        sentinel = quarantine / "third-party.txt"
    else:
        external = tmp_path / "external-third-party"
        external.mkdir()
        sentinel = external / "third-party.txt"
        try:
            quarantine.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable")
    sentinel.write_text("KEEP", encoding="utf-8")

    removed = storage_migration_module._remove_transaction_root_if_owned(
        payload,
        transaction_root,
        payload["txid"],
    )

    assert removed is False
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert quarantine.exists() or quarantine.is_symlink()


@pytest.mark.unit
def test_transaction_cleanup_preserves_name_replacement_race(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
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
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    owned_aside = target_root / "owned-aside"
    replacement = target_root / "third-party"
    replacement.mkdir()
    sentinel = replacement / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    original_remove_private = (
        storage_migration_module._remove_private_directory_via_quarantine
    )

    def _swap_before_quarantine(path, expected_identity, **kwargs):
        path.rename(owned_aside)
        replacement.rename(path)
        return original_remove_private(path, expected_identity, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_remove_private_directory_via_quarantine",
        _swap_before_quarantine,
    )

    removed = storage_migration_module._remove_transaction_root_if_owned(
        payload,
        transaction_root,
        payload["txid"],
    )

    assert removed is False
    assert (transaction_root / sentinel.name).read_text(encoding="utf-8") == "KEEP"
    assert owned_aside.exists()


@pytest.mark.unit
def test_transaction_cleanup_restores_replacement_symlink_without_following_it(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    owned_identity = transaction_root.lstat()
    owned_aside = tmp_path / "owned-aside"
    transaction_root.rename(owned_aside)
    external = tmp_path / "external.txt"
    external.write_text("KEEP", encoding="utf-8")
    try:
        transaction_root.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks are unavailable")

    removed = storage_migration_module._remove_private_directory_via_quarantine(
        transaction_root,
        owned_identity,
    )

    assert removed is False
    assert transaction_root.is_symlink()
    assert os.path.samefile(transaction_root, external)
    assert external.read_text(encoding="utf-8") == "KEEP"
    assert owned_aside.is_dir()


@pytest.mark.unit
def test_prepared_transaction_identity_replacement_is_preserved(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    original_write_marker = storage_migration_module._write_transaction_owner_marker
    replaced = {}

    def _replace_prepared_root(marker_payload, prepared_root, txid):
        owned_aside = prepared_root.with_name(f"{prepared_root.name}.owned-aside")
        prepared_root.rename(owned_aside)
        prepared_root.mkdir()
        sentinel = prepared_root / "third-party.txt"
        sentinel.write_text("KEEP", encoding="utf-8")
        replaced.update(root=prepared_root, aside=owned_aside, sentinel=sentinel)
        original_write_marker(marker_payload, prepared_root, txid)

    monkeypatch.setattr(
        storage_migration_module,
        "_write_transaction_owner_marker",
        _replace_prepared_root,
    )

    with pytest.raises(OSError, match="identity changed"):
        storage_migration_module._create_owned_transaction_root(
            payload,
            transaction_root,
            payload["txid"],
        )

    assert not transaction_root.exists()
    assert replaced["root"].is_dir()
    assert replaced["aside"].is_dir()
    assert replaced["sentinel"].read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("terminal_status", "attempted", "completed"),
    (
        (STORAGE_MIGRATION_STATUS_COMPLETED, False, False),
        (STORAGE_MIGRATION_STATUS_FAILED, True, True),
    ),
)
def test_terminal_checkpoint_recovers_interrupted_transaction_quarantine(
    tmp_path,
    terminal_status,
    attempted,
    completed,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    config_manager.app_docs_dir.mkdir(parents=True, exist_ok=True)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir()
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    storage_migration_module._durable_rename_without_replacing(
        transaction_root,
        quarantine,
    )
    payload.update(
        status=terminal_status,
        transaction_root=str(transaction_root),
        source_runtime_baseline={},
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is attempted
    assert result["completed"] is completed, result
    assert not transaction_root.exists()
    assert not quarantine.exists()


@pytest.mark.unit
@pytest.mark.parametrize("detached", (False, True))
@pytest.mark.parametrize(
    ("source_loss", "expected_error"),
    (
        ("root_missing", "source_root_missing"),
        ("entry_missing", "source_recovery_unverifiable"),
    ),
)
def test_copying_checkpoint_preserves_staged_data_when_source_disappears(
    tmp_path,
    detached,
    source_loss,
    expected_error,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    source_baseline = storage_migration_module._snapshot_runtime_entries(source_root)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
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
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    staged_file.parent.mkdir(parents=True)
    staged_file.write_text("ONLY-COPY", encoding="utf-8")
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    payload.update(
        status="copying",
        transaction_root=str(transaction_root),
        source_runtime_baseline=source_baseline,
    )
    storage_migration_module.save_storage_migration(config_manager, payload)
    evidence_root = transaction_root
    if detached:
        evidence_root = storage_migration_module._private_directory_quarantine_path(
            transaction_root
        )
        storage_migration_module._durable_rename_without_replacing(
            transaction_root,
            evidence_root,
        )
    if source_loss == "root_missing":
        shutil.rmtree(source_root)
    else:
        source_file.unlink()

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == expected_error
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert (evidence_root / "staged" / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "ONLY-COPY"

    # Legacy builds wrote this evidence-owning state as terminal ``failed``.
    # The next launcher must upgrade it back into an active recovery attempt.
    legacy_payload = dict(load_storage_migration(config_manager))
    legacy_payload["status"] = STORAGE_MIGRATION_STATUS_FAILED
    storage_migration_module.save_storage_migration(config_manager, legacy_payload)

    # The active recovery checkpoint still cannot prove this copy is redundant.
    retry = run_pending_storage_migration(config_manager)
    assert retry["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert (evidence_root / "staged" / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "ONLY-COPY"

    # Once the original source is restored byte-for-byte, the next launcher can
    # securely retire the owned transaction and retry the migration.
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    recovered = run_pending_storage_migration(config_manager)
    assert recovered["completed"] is True
    assert not evidence_root.exists()
    assert (target_root / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "SOURCE"


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
