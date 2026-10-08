import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    create_pending_storage_migration,
    get_storage_migration_path,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    load_storage_migration,
    run_pending_storage_migration,
    save_storage_migration,
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


def _write_memory_tree(root: Path, *, marker: str = "source") -> None:
    memory_root = root / "memory"
    (memory_root / "Alice" / "facts").mkdir(parents=True, exist_ok=True)
    (memory_root / "Alice" / "recent.json").write_text(marker, encoding="utf-8")
    (memory_root / "Alice" / "facts" / "facts.json").write_text(
        marker, encoding="utf-8"
    )
    (memory_root / ".staging" / "job-1").mkdir(parents=True)
    (memory_root / ".staging" / "job-1" / "state.json").write_text(
        marker, encoding="utf-8"
    )
    (memory_root / "empty-dir").mkdir()


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
    assert payload["status"] == "pending"
    assert is_storage_migration_pending(payload) is True


@pytest.mark.unit
def test_is_storage_migration_pending_ignores_terminal_status():
    payload = {
        "status": STORAGE_MIGRATION_STATUS_FAILED,
        "source_root": "/tmp/source",
        "target_root": "/tmp/target",
    }

    assert is_storage_migration_pending(payload) is False


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
def test_staging_prefix_keeps_windows_paths_short():
    """Staging must not push a file that fits at its final path past MAX_PATH.

    Before staging, a migrated file only needed to fit at ``<target>/<entry>/...``.
    The transaction layer stages it at ``<target>/<tx dir>/<txid>/stage/<entry>/...``
    first; with the original 71-character prefix, an avatar-tool record under a
    normal pytest temp root already crossed 260 characters on Windows.
    """
    import uuid

    from utils.storage import migration as migration_module

    target_root = Path("T")
    staged = migration_module._transaction_path(target_root, uuid.uuid4().hex) / "stage"
    overhead = len(str(staged)) - len(str(target_root))
    assert overhead <= 32, staged


def test_staging_failure_removes_partial_transaction(monkeypatch, tmp_path):
    from utils.storage import migration as migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("{}", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    transaction_root = migration_module._transaction_path(target_root, pending["txid"])

    def fail_mid_copy(_source_entry, staged_entry):
        staged_entry.mkdir(parents=True)
        (staged_entry / "partial.tmp").write_text("partial", encoding="utf-8")
        raise OSError("fixture staging failure")

    monkeypatch.setattr(migration_module, "_copy_runtime_entry", fail_mid_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert not transaction_root.exists()


@pytest.mark.unit
def test_run_pending_storage_migration_commits_policy_and_copies_runtime_entries(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    theater_files = ["numeric_v2/packages/story.json", "numeric_v2/sessions/session.json",
                     "numeric_v2/end_receipts/receipt.json", "numeric_v2/public_archives/archive.json",
                     "workshop/projects/author.json", "numeric_v2/forget_transactions/pending.json"]
    for relative in theater_files:
        path = source_root / "theater" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "memory" / "A").mkdir(parents=True, exist_ok=True)
    (source_root / "card_faces").mkdir(parents=True, exist_ok=True)
    (source_root / "avatar_tools" / "local-12345678-1234-4123-8123-123456789abc").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    plugin_models = '{"schema_version":1,"slots":{"slot_a":{"api_key":"test-only"}},"bindings":{}}'
    (source_root / "config" / "plugin_models.json").write_text(plugin_models, encoding="utf-8")
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
    for relative in theater_files:
        assert (target_root / "theater" / relative).read_text(encoding="utf-8") == relative
        assert (source_root / "theater" / relative).read_text(encoding="utf-8") == relative
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"current":"A"}'
    assert (target_root / "config" / "plugin_models.json").read_text(encoding="utf-8") == plugin_models
    assert (source_root / "config" / "plugin_models.json").read_text(encoding="utf-8") == plugin_models
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
def test_storage_migration_copies_complete_tree_with_digest_proof(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_memory_tree(source_root)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    proof = result["payload"]["copied_entries"]["memory"]
    assert proof["source_manifest"] == proof["target_manifest"]
    assert proof["source_manifest"]["manifest_digest"]
    assert proof["source_manifest"]["file_count"] == 3
    assert (target_root / "memory" / "Alice" / "recent.json").read_text(
        encoding="utf-8"
    ) == "source"
    assert (target_root / "memory" / "Alice" / "facts" / "facts.json").is_file()
    assert (
        target_root / "memory" / ".staging" / "job-1" / "state.json"
    ).read_text(encoding="utf-8") == "source"
    assert (target_root / "memory" / "empty-dir").is_dir()
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_existing_target_content_does_not_skip_missing_source_entry(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_memory_tree(source_root)
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "existing.json").write_text("{}", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert (target_root / "config" / "existing.json").is_file()
    assert (target_root / "memory" / "Alice" / "recent.json").is_file()
    assert "memory" in result["payload"]["copied_entries"]


@pytest.mark.unit
def test_storage_migration_manifest_detects_equal_size_content_changes(tmp_path):
    from utils.storage_migration import _snapshot_path

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "same.bin").write_bytes(b"AAAA")
    (right / "same.bin").write_bytes(b"BBBB")

    left_manifest = _snapshot_path(left)
    right_manifest = _snapshot_path(right)

    assert left_manifest["file_count"] == right_manifest["file_count"] == 1
    assert left_manifest["total_bytes"] == right_manifest["total_bytes"] == 4
    assert left_manifest["manifest_digest"] != right_manifest["manifest_digest"]


@pytest.mark.unit
def test_storage_migration_rejects_nested_links_without_following(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel.txt").write_text("keep", encoding="utf-8")
    (source_root / "memory").mkdir(parents=True)
    try:
        os.symlink(external, source_root / "memory" / "linked", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_link_unsupported"
    assert (external / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_migration_requires_confirmation_when_target_only_contains_theater(tmp_path):
    from utils.cloudsave_runtime import runtime_root_has_user_content
    config = _make_config_manager(tmp_path)
    config.app_docs_dir.mkdir(parents=True)
    target = tmp_path / "target" / "N.E.K.O"
    saved = target / "theater" / "numeric_v2" / "sessions" / "existing.json"
    saved.parent.mkdir(parents=True)
    saved.write_text('{"existing": true}', encoding="utf-8")
    assert runtime_root_has_user_content(target, config_manager=config)
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target, selection_source="custom")
    result = run_pending_storage_migration(config)
    assert result["error_code"] == "target_confirmation_required"
    assert saved.read_text(encoding="utf-8") == '{"existing": true}'


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
def test_interrupted_publish_restores_existing_target_before_retry(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text("healthy", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        if Path(staged).name == "config":
            raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)

    def _stop_after_recovery(*_args, **_kwargs):
        raise StorageMigrationError("stop_after_recovery", "inspect restored target")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _stop_after_recovery)
    retry = run_pending_storage_migration(config_manager)

    assert retry["completed"] is False
    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_committed_policy_recovers_missing_completion_checkpoint(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload
    failed_once = False

    def _fail_completed_checkpoint(*args, **kwargs):
        nonlocal failed_once
        if kwargs.get("status") == STORAGE_MIGRATION_STATUS_COMPLETED and not failed_once:
            failed_once = True
            raise OSError("simulated checkpoint loss")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        _fail_completed_checkpoint,
    )
    first = run_pending_storage_migration(config_manager)

    assert first["completed"] is False
    assert first["error_code"] == "migration_commit_pending"
    assert load_storage_policy(config_manager)["selected_root"] == str(target_root.resolve())
    assert load_storage_migration(config_manager)["status"] == "committing"

    # The committed target already matches the checkpoint evidence, so the
    # retry only finishes the checkpoint: it must not roll back and copy again.
    def _no_second_copy(*_args, **_kwargs):
        raise AssertionError("committed migration was copied again")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _no_second_copy)
    second = run_pending_storage_migration(config_manager)
    assert second["completed"] is True
    assert second["payload"]["status"] == STORAGE_MIGRATION_STATUS_COMPLETED
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


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


def _overwrite_migration(tmp_path: Path):
    """Source config ``new`` over an existing target config ``healthy``."""
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text("healthy", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    return config_manager, source_root, target_root


def _fail_committing_checkpoint(monkeypatch, storage_migration_module):
    original_persist = storage_migration_module._persist_migration_payload

    def _persist(*args, **kwargs):
        if kwargs.get("status") == "committing":
            raise OSError("simulated checkpoint write failure")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _persist)


def _stop_after_recovery(monkeypatch, storage_migration_module):
    def _stop(*_args, **_kwargs):
        raise StorageMigrationError("stop_after_recovery", "inspect restored target")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _stop)


@pytest.mark.unit
def test_failed_committing_checkpoint_rolls_the_publish_back(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    _fail_committing_checkpoint(monkeypatch, storage_migration_module)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"
    # No original target entry is left behind in a transaction backup.
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_failed_rollback_stays_retryable_and_keeps_the_backup(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    _fail_committing_checkpoint(monkeypatch, storage_migration_module)
    original_replace = storage_migration_module.os.replace

    def _replace(source, target):
        if Path(source).parent.name == "backup":
            raise OSError("simulated locked file during rollback")
        return original_replace(source, target)

    monkeypatch.setattr(storage_migration_module.os, "replace", _replace)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "migration_rollback_required"
    assert result["payload"]["status"] == "rollback_required"
    assert is_storage_migration_pending(load_storage_migration(config_manager))
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]

    monkeypatch.undo()
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_rollback_interrupted_after_restoring_resumes(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_replace = storage_migration_module.os.replace
    original_persist = storage_migration_module._persist_migration_payload

    def _crash_once_published(*args, **kwargs):
        # Every entry is recorded as published by the time COMMITTING is written.
        if kwargs.get("status") == "committing":
            raise KeyboardInterrupt("simulated process loss after publish")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module, "_persist_migration_payload", _crash_once_published
    )
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    assert load_storage_migration(config_manager)["published_entries"] == ["config"]
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", original_persist)

    def _crash_after_restore(source, target):
        original_replace(source, target)
        if Path(source).parent.name == "backup":
            raise KeyboardInterrupt("simulated process loss during rollback")

    monkeypatch.setattr(storage_migration_module.os, "replace", _crash_after_restore)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"

    monkeypatch.setattr(storage_migration_module.os, "replace", original_replace)
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    # The restored entry is recognised instead of reported as a lost backup.
    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_committed_target_written_since_is_never_rolled_back(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload
    failed_once = False

    def _fail_completed_checkpoint(*args, **kwargs):
        nonlocal failed_once
        if kwargs.get("status") == STORAGE_MIGRATION_STATUS_COMPLETED and not failed_once:
            failed_once = True
            raise OSError("simulated checkpoint loss")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        _fail_completed_checkpoint,
    )
    first = run_pending_storage_migration(config_manager)
    assert first["error_code"] == "migration_commit_pending"

    # The launcher starts services on the committed target regardless.
    (target_root / "config" / "characters.json").write_text("edited", encoding="utf-8")
    (target_root / "config" / "written_by_service.json").write_text("{}", encoding="utf-8")

    second = run_pending_storage_migration(config_manager)

    assert second["completed"] is True
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "edited"
    assert (target_root / "config" / "written_by_service.json").is_file()


@pytest.mark.unit
def test_torn_config_copy_fails_staging_verification(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("complete", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_copy = storage_migration_module._copy_runtime_entry

    def _torn_copy(source_path, target_path):
        original_copy(source_path, target_path)
        if Path(target_path).name == "config":
            # The source changed between its manifest and the copy.
            (Path(target_path) / "characters.json").write_text("torn", encoding="utf-8")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _torn_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "verification_failed"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_storage_migration_keeps_directory_metadata(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    private_dir = source_root / "memory" / "private"
    private_dir.mkdir(parents=True)
    (private_dir / "notes.json").write_text("{}", encoding="utf-8")
    old_time = 1_000_000_000
    os.utime(private_dir, (old_time, old_time))
    os.utime(source_root / "memory", (old_time, old_time))
    if os.name == "posix":
        private_dir.chmod(0o700)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    assert run_pending_storage_migration(config_manager)["completed"] is True

    copied_private = target_root / "memory" / "private"
    assert int(copied_private.stat().st_mtime) == old_time
    assert int((target_root / "memory").stat().st_mtime) == old_time
    if os.name == "posix":
        assert copied_private.stat().st_mode & 0o777 == 0o700


@pytest.mark.unit
def test_storage_migration_refuses_a_linked_transaction_directory(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    target_root.mkdir(parents=True)
    try:
        os.symlink(external, target_root / ".smtx", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_link_unsupported"
    assert list(external.iterdir()) == []
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_target_entry_appearing_after_staging_survives_rollback(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _persist(*args, **kwargs):
        if kwargs.get("status") == "verifying":
            # Staging recorded no target config; one appears before publish.
            (target_root / "config").mkdir(parents=True, exist_ok=True)
            (target_root / "config" / "late.json").write_text("late", encoding="utf-8")
        if kwargs.get("status") == "committing":
            raise KeyboardInterrupt("simulated process loss after publish")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _persist)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", original_persist)

    # The next start recovers from the checkpoint on disk alone. The restored
    # target now holds data, so the retry stops to ask before overwriting it.
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "target_confirmation_required"
    assert (target_root / "config" / "late.json").read_text(encoding="utf-8") == "late"
    assert not (target_root / "config" / "characters.json").exists()


@pytest.mark.unit
def test_v1_commit_pending_checkpoint_stays_classified_as_legacy(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module
    from utils.storage_migration import is_legacy_unproven_checkpoint

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _fail_completed_checkpoint(*args, **kwargs):
        if kwargs.get("status") == STORAGE_MIGRATION_STATUS_COMPLETED:
            raise OSError("simulated checkpoint loss")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module, "_persist_migration_payload", _fail_completed_checkpoint
    )
    assert run_pending_storage_migration(config_manager)["error_code"] == "migration_commit_pending"
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", original_persist)

    # Rewrite it as the COMMITTING checkpoint a v1 build left behind.
    v1_payload = dict(load_storage_migration(config_manager))
    v1_payload["version"] = 1
    for key in (
        "copied_entries",
        "published_entries",
        "original_target_entries",
        "publishing_entry",
        "publishing_target_existed",
        "restoring_entries",
    ):
        v1_payload.pop(key, None)
    save_storage_migration(config_manager, v1_payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert result["payload"]["version"] == 1
    assert is_legacy_unproven_checkpoint(result["payload"])


@pytest.mark.unit
def test_leftover_completed_transaction_is_removed_on_next_launch(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_remove = storage_migration_module._remove_transaction

    def _locked(_transaction_root):
        raise OSError("simulated locked backup file")

    monkeypatch.setattr(storage_migration_module, "_remove_transaction", _locked)
    assert run_pending_storage_migration(config_manager)["completed"] is True
    assert list((target_root / ".smtx").glob("*/backup/config/characters.json"))

    monkeypatch.setattr(storage_migration_module, "_remove_transaction", original_remove)
    later = run_pending_storage_migration(config_manager)

    assert later["attempted"] is False
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_unreadable_policy_during_committing_is_not_rolled_back(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module
    from utils.storage_policy import get_storage_policy_path

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)

    def _lost_during_policy_commit(*_args, **_kwargs):
        raise KeyboardInterrupt("simulated process loss while committing the policy")

    monkeypatch.setattr(storage_migration_module, "save_storage_policy", _lost_during_policy_commit)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()
    assert load_storage_migration(config_manager)["status"] == "committing"

    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("{not json", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "migration_commit_ambiguous"
    assert result["payload"]["status"] == "committing"
    # Nothing was rolled back while the policy could not be read.
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]


@pytest.mark.unit
def test_malformed_transaction_id_never_reaches_the_filesystem(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    target_root.mkdir(parents=True)
    victim = target_root.parent / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    payload = dict(load_storage_migration(config_manager))
    payload["txid"] = "../../victim"
    save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_id_invalid"
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("attributes", "reparse_tag", "is_link"),
    (
        # OneDrive Files On-Demand placeholder: reparse attribute, lstat
        # traverses it so no tag is reported.
        (0x400 | 0x20, 0, False),
        # App execution alias (IO_REPARSE_TAG_APPEXECLINK) as lstat reports it.
        (0x420, 0x8000001B, False),
        # Dedup file (IO_REPARSE_TAG_DEDUP).
        (0x420, 0x80000013, False),
        # Junction (IO_REPARSE_TAG_MOUNT_POINT) and symlink: name surrogates.
        (0x410, 0xA0000003, True),
        (0x410, 0xA000000C, True),
        (0x20, 0, False),
    ),
)
def test_only_name_surrogate_reparse_points_count_as_links(attributes, reparse_tag, is_link):
    from types import SimpleNamespace

    from utils import storage_migration as storage_migration_module

    path_stat = SimpleNamespace(st_file_attributes=attributes, st_reparse_tag=reparse_tag)

    assert storage_migration_module._stat_is_reparse(path_stat) is is_link


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_storage_migration_rejects_a_real_junction(tmp_path):
    import subprocess

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel.txt").write_text("keep", encoding="utf-8")
    (source_root / "memory").mkdir(parents=True)
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(source_root / "memory" / "linked"), str(external)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stderr}")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_link_unsupported"
    assert (external / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="directory write bits only gate rename/removal on POSIX")
def test_storage_migration_publishes_a_read_only_directory(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    read_only_dir = source_root / "memory" / "frozen"
    read_only_dir.mkdir(parents=True)
    (read_only_dir / "notes.json").write_text("{}", encoding="utf-8")
    read_only_dir.chmod(0o555)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    try:
        result = run_pending_storage_migration(config_manager)
    finally:
        read_only_dir.chmod(0o755)

    assert result["completed"] is True, result
    copied = target_root / "memory" / "frozen"
    try:
        assert (copied / "notes.json").is_file()
        # Write access was only borrowed for the move; the source mode is back.
        assert copied.stat().st_mode & 0o777 == 0o555
        assert not (target_root / ".smtx").exists()
    finally:
        copied.chmod(0o755)


@pytest.mark.unit
def test_completed_overwrite_removes_a_read_only_backup(tmp_path):
    """The overwritten target goes to the transaction backup; a read-only file
    in it (Windows) must not leave that whole copy behind under .smtx."""
    import stat

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    locked = target_root / "config" / "locked.json"
    locked.write_text("old", encoding="utf-8")
    locked.chmod(stat.S_IREAD)

    try:
        result = run_pending_storage_migration(config_manager)
    finally:
        for leftover in (target_root / ".smtx").glob("*/backup/config/locked.json"):
            leftover.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert result["completed"] is True, result
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_target_entry_appearing_at_publish_is_never_overwritten(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _persist(*args, **kwargs):
        result = original_persist(*args, **kwargs)
        if kwargs.get("publishing_entry") == "config" and kwargs.get("publishing_target_existed") is False:
            # Another process creates the entry between the check and publish.
            (target_root / "config").mkdir(parents=True, exist_ok=True)
            (target_root / "config" / "late.json").write_text("late", encoding="utf-8")
        return result

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _persist)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_changed_during_migration"
    assert (target_root / "config" / "late.json").read_text(encoding="utf-8") == "late"
    assert not (target_root / "config" / "characters.json").exists()
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_reused_target_emptied_during_staging_is_not_committed(tmp_path, monkeypatch):
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("source", encoding="utf-8")
    _write_memory_tree(source_root)
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text("target", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    original_copy = storage_migration_module._copy_runtime_entry

    def _copy_then_lose_target(source_path, target_path):
        original_copy(source_path, target_path)
        if Path(target_path).name == "memory":
            # The target's own config -- skipped by staging -- disappears.
            shutil.rmtree(target_root / "config")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _copy_then_lose_target)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_missing_runtime"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_committed_migration_finishes_without_the_retained_source(tmp_path, monkeypatch):
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _fail_completed_checkpoint(*args, **kwargs):
        if kwargs.get("status") == STORAGE_MIGRATION_STATUS_COMPLETED:
            raise OSError("simulated checkpoint loss")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _fail_completed_checkpoint)
    assert run_pending_storage_migration(config_manager)["error_code"] == "migration_commit_pending"
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", original_persist)
    # The retained source is deleted (or its drive unplugged) before relaunch.
    shutil.rmtree(source_root)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert load_storage_policy(config_manager)["selected_root"] == str(target_root.resolve())
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_interrupted_publish_is_kept_while_the_source_is_missing(tmp_path, monkeypatch):
    """Without the source, the published and staged copies may be the only
    ones left: keep everything and roll back once the source is back."""
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss after publish")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)
    parked_source = tmp_path / "parked-source"
    shutil.move(str(source_root), str(parked_source))

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert result["payload"]["status"] == "rollback_required"
    assert is_storage_migration_pending(load_storage_migration(config_manager))
    # Nothing was rolled back or removed.
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]

    # Once the source is back, the next start rolls the publish back.
    shutil.move(str(parked_source), str(source_root))
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_read_only_retry_reraises_failures_that_are_not_removals(tmp_path):
    """rmtree also reports failed os.open/os.scandir calls; retrying those
    with a bare path raised TypeError instead of the real error."""
    from utils import storage_migration as storage_migration_module

    original = PermissionError(13, "cannot list directory")
    with pytest.raises(PermissionError, match="cannot list directory"):
        storage_migration_module._retry_after_clearing_read_only(
            os.open, str(tmp_path / "unlistable"), (PermissionError, original, None)
        )


@pytest.mark.unit
def test_interrupted_staging_is_kept_while_the_source_is_missing(tmp_path, monkeypatch):
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    _write_memory_tree(source_root)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_copy = storage_migration_module._copy_runtime_entry

    def _crash_after_first_copy(source_path, target_path):
        original_copy(source_path, target_path)
        raise KeyboardInterrupt("simulated process loss while staging")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _crash_after_first_copy)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)
    assert load_storage_migration(config_manager)["status"] == "copying"
    shutil.move(str(source_root), str(tmp_path / "parked-source"))

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    staged = list((target_root / ".smtx").glob("*/stage/*"))
    assert staged, "the staged copy must survive while the source is missing"


@pytest.mark.unit
def test_empty_target_directory_appearing_at_publish_is_kept(tmp_path, monkeypatch):
    """rename(2) would silently replace an empty directory on POSIX."""
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _persist(*args, **kwargs):
        result = original_persist(*args, **kwargs)
        if kwargs.get("publishing_entry") == "config" and kwargs.get("publishing_target_existed") is False:
            (target_root / "config").mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                (target_root / "config").chmod(0o700)
        return result

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _persist)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_changed_during_migration"
    assert (target_root / "config").is_dir()
    assert list((target_root / "config").iterdir()) == []
    if os.name == "posix":
        assert (target_root / "config").stat().st_mode & 0o777 == 0o700


@pytest.mark.unit
def test_interrupted_publish_is_kept_when_a_source_entry_is_gone(tmp_path, monkeypatch):
    """The source root itself (often the anchor root) survives; the user only
    deleted the entries that already showed up in the new directory."""
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss after publish")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)
    parked_entry = tmp_path / "parked-config"
    shutil.move(str(source_root / "config"), str(parked_entry))
    assert source_root.is_dir()

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert "config" in result["error_message"]
    assert result["payload"]["status"] == "rollback_required"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]

    shutil.move(str(parked_entry), str(source_root / "config"))
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_staged_copy_is_kept_when_its_source_entry_is_gone(tmp_path, monkeypatch):
    """An entry that was only staged (never published) can still be the last copy."""
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    _write_memory_tree(source_root)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_copy = storage_migration_module._copy_runtime_entry

    def _crash_after_first_copy(source_path, target_path):
        original_copy(source_path, target_path)
        raise KeyboardInterrupt("simulated process loss while staging")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _crash_after_first_copy)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)
    staged = list((target_root / ".smtx").glob("*/stage/config/characters.json"))
    assert len(staged) == 1
    shutil.rmtree(source_root / "config")
    assert source_root.is_dir()

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert staged[0].read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_target_recreated_after_backup_keeps_both_copies(tmp_path, monkeypatch):
    """The original went to the backup, then something recreated the target:
    rolling back would delete the newcomer, so keep both and wait."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _recreate_then_publish(staged, target):
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "newcomer.json").write_text("newcomer", encoding="utf-8")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _recreate_then_publish)
    result = run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)

    assert result["error_code"] == "migration_publish_conflict"
    assert result["payload"]["status"] == "rollback_required"
    assert (target_root / "config" / "newcomer.json").read_text(encoding="utf-8") == "newcomer"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]

    # A later start must not resolve the conflict by deleting either copy.
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_publish_conflict"
    assert (target_root / "config" / "newcomer.json").read_text(encoding="utf-8") == "newcomer"
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="the hard-link fallback is the POSIX file path")
def test_file_publish_without_hard_links_refuses_an_existing_target(tmp_path, monkeypatch):
    import errno

    from utils import storage_migration as storage_migration_module

    staged = tmp_path / "staged.json"
    staged.write_text("staged", encoding="utf-8")
    target = tmp_path / "target.json"
    target.write_text("newcomer", encoding="utf-8")

    def _no_hard_links(_source, _destination):
        raise OSError(errno.EPERM, "hard links unsupported")

    monkeypatch.setattr(storage_migration_module.os, "link", _no_hard_links)
    with pytest.raises(FileExistsError):
        storage_migration_module._publish_without_overwrite(staged, target)

    assert target.read_text(encoding="utf-8") == "newcomer"
    assert staged.read_text(encoding="utf-8") == "staged"

    target.unlink()
    storage_migration_module._publish_without_overwrite(staged, target)
    assert target.read_text(encoding="utf-8") == "staged"
    assert not staged.exists()


@pytest.mark.unit
def test_interrupted_v1_copy_is_overwritten_not_reused(tmp_path):
    """A v1 run stopped in COPYING left a partial copy in the target; with a
    legacy selection that partial copy must not become the new data."""
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("complete", encoding="utf-8")
    (source_root / "config" / "core_config.json").write_text("{}", encoding="utf-8")
    # What v1 managed to copy before it stopped.
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text("comp", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    v1_payload = dict(load_storage_migration(config_manager))
    v1_payload["version"] = 1
    v1_payload["status"] = "copying"
    for key in ("copied_entries", "published_entries", "publishing_entry", "publishing_target_existed", "restoring_entries"):
        v1_payload.pop(key, None)
    save_storage_migration(config_manager, v1_payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "complete"
    assert (target_root / "config" / "core_config.json").is_file()
    assert "config" in result["payload"]["copied_entries"]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="directory write bits only gate rename on POSIX")
def test_overwriting_a_read_only_target_directory_succeeds(tmp_path):
    """A previous migration published a read-only directory; moving it into
    the backup must not fail every later migration onto that target."""
    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    (target_root / "config").chmod(0o555)

    try:
        result = run_pending_storage_migration(config_manager)
    finally:
        for leftover in (target_root / ".smtx").glob("*/backup/config"):
            leftover.chmod(0o755)
        if (target_root / "config").exists():
            (target_root / "config").chmod(0o755)

    assert result["completed"] is True, result
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_publish_conflict_marker_does_not_outlive_its_transaction(tmp_path, monkeypatch):
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _recreate_then_publish(staged, target):
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "newcomer.json").write_text("newcomer", encoding="utf-8")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _recreate_then_publish)
    assert run_pending_storage_migration(config_manager)["error_code"] == "migration_publish_conflict"
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)
    # The user sorts the conflict out by removing the transaction directory.
    shutil.rmtree(target_root / ".smtx")

    # The fresh attempt is interrupted after publishing ...
    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss after publish")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)

    # ... and is rolled back as usual, not mistaken for the old conflict.
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "newcomer.json").read_text(encoding="utf-8") == "newcomer"


@pytest.mark.unit
def test_v1_copy_marker_survives_a_second_interruption(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("complete", encoding="utf-8")
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "characters.json").write_text("comp", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    v1_payload = dict(load_storage_migration(config_manager))
    v1_payload["version"] = 1
    v1_payload["status"] = "copying"
    save_storage_migration(config_manager, v1_payload)
    original_copy = storage_migration_module._copy_runtime_entry

    def _interrupt(source_path, target_path):
        original_copy(source_path, target_path)
        raise KeyboardInterrupt("simulated process loss while staging")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)
    interrupted = load_storage_migration(config_manager)
    assert interrupted["version"] == 2 and interrupted["status"] == "copying"

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "complete"


@pytest.mark.unit
def test_interrupted_publish_is_kept_when_a_file_inside_the_source_entry_is_gone(tmp_path, monkeypatch):
    """source/config still exists, but the file that was copied out of it is
    gone: the published copy is the last complete one."""
    from utils import storage_migration as storage_migration_module

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss after publish")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)
    (source_root / "config" / "characters.json").unlink()
    assert (source_root / "config").is_dir()

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in backups] == ["healthy"]


@pytest.mark.unit
def test_staged_copy_is_kept_when_its_source_entry_was_edited(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("complete", encoding="utf-8")
    _write_memory_tree(source_root)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_copy = storage_migration_module._copy_runtime_entry
    copies = 0

    def _crash_on_second_copy(source_path, target_path):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise KeyboardInterrupt("simulated process loss while staging")
        return original_copy(source_path, target_path)

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _crash_on_second_copy)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)
    staged = list((target_root / ".smtx").glob("*/stage/config/characters.json"))
    assert [path.read_text(encoding="utf-8") for path in staged] == ["complete"]
    (source_root / "config" / "characters.json").write_text("trunc", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert staged[0].read_text(encoding="utf-8") == "complete"


@pytest.mark.unit
def test_reused_identical_config_gets_its_workshop_paths_rebased(tmp_path):
    import json

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    workshop = {"user_mod_folder": str(source_root / "mods")}
    for root in (source_root, target_root):
        (root / "config").mkdir(parents=True)
        (root / "config" / "workshop_config.json").write_text(json.dumps(workshop), encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    rebased = json.loads((target_root / "config" / "workshop_config.json").read_text(encoding="utf-8"))
    assert rebased["user_mod_folder"] == str((target_root / "mods").resolve())


@pytest.mark.unit
def test_fresh_attempt_drops_publish_records_of_a_removed_transaction(tmp_path, monkeypatch):
    """A conflict was resolved by deleting .smtx; the checkpoint still lists
    entries that transaction had published. A new attempt interrupted while
    staging must not try to restore backups that never existed."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    stale = dict(load_storage_migration(config_manager))
    stale.update(
        {
            "published_entries": ["config"],
            "original_target_entries": ["config"],
            "publishing_entry": "memory",
            "publishing_target_existed": True,
            "restoring_entries": ["config"],
        }
    )
    save_storage_migration(config_manager, stale)
    original_copy = storage_migration_module._copy_runtime_entry

    def _interrupt(source_path, target_path):
        original_copy(source_path, target_path)
        raise KeyboardInterrupt("simulated process loss while staging")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_unreadable_source_during_recovery_stays_retryable(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss after publish")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)
    original_snapshot = storage_migration_module._snapshot_path

    def _locked_source(path):
        if Path(path) == source_root / "config":
            raise PermissionError(32, "the file is being used by another process")
        return original_snapshot(path)

    monkeypatch.setattr(storage_migration_module, "_snapshot_path", _locked_source)
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_source_missing"
    assert result["payload"]["status"] == "rollback_required"
    assert load_storage_policy(config_manager) is None
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_status_migration_payload_carries_what_the_maintenance_view_reads(tmp_path, monkeypatch):
    """The maintenance view builds its paused-migration hints from the status
    payload's ``migration``; every field it reads must actually be there."""
    from utils import storage_migration as storage_migration_module
    from utils.storage.location_bootstrap import _build_migration_payload

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _recreate_then_publish(staged, target):
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "newcomer.json").write_text("newcomer", encoding="utf-8")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _recreate_then_publish)
    assert run_pending_storage_migration(config_manager)["error_code"] == "migration_publish_conflict"
    checkpoint = load_storage_migration(config_manager)

    migration = _build_migration_payload(checkpoint, "")

    assert migration["status"] == "rollback_required"
    assert migration["error_code"] == "migration_publish_conflict"
    assert migration["source_root"] and migration["target_root"]
    assert migration["txid"] == checkpoint["txid"]
    # The prefix the view shows is the transaction directory that exists.
    assert (target_root / ".smtx" / migration["txid"][:12] / "backup").is_dir()


@pytest.mark.unit
def test_failed_staging_leftover_is_removed_on_next_launch(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("new", encoding="utf-8")
    _write_memory_tree(source_root)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_copy = storage_migration_module._copy_runtime_entry
    original_remove = storage_migration_module._remove_transaction
    copies = 0

    def _fail_second_copy(source_path, target_path):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise StorageMigrationError("copy_failed", "simulated copy failure")
        return original_copy(source_path, target_path)

    def _locked(_transaction_root):
        raise OSError("simulated locked staged file")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _fail_second_copy)
    monkeypatch.setattr(storage_migration_module, "_remove_transaction", _locked)
    result = run_pending_storage_migration(config_manager)
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert list((target_root / ".smtx").glob("*/stage/config"))

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", original_copy)
    monkeypatch.setattr(storage_migration_module, "_remove_transaction", original_remove)
    later = run_pending_storage_migration(config_manager)

    assert later["attempted"] is False
    assert not (target_root / ".smtx").exists()


@pytest.mark.unit
def test_failed_checkpoint_never_drops_a_non_empty_backup(tmp_path):
    from utils.storage_migration import _remove_completed_transaction_leftover

    target_root = tmp_path / "target-selected" / "N.E.K.O"
    txid = "0123456789abcdef0123456789abcdef"
    backup_entry = target_root / ".smtx" / txid[:12] / "backup" / "config"
    backup_entry.mkdir(parents=True)
    (backup_entry / "characters.json").write_text("original", encoding="utf-8")

    _remove_completed_transaction_leftover(
        {"status": "failed", "target_root": str(target_root), "txid": txid}
    )

    assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_storage_migration_moves_pngtuber_watch_together_and_runtimes(tmp_path):
    """#3336: these held user data / downloads but were left in the old root."""
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "pngtuber" / "Alice").mkdir(parents=True)
    (source_root / "pngtuber" / "Alice" / "idle.png").write_bytes(b"png")
    (source_root / "watch_together" / "objects").mkdir(parents=True)
    (source_root / "watch_together" / "library.sqlite3").write_bytes(b"sqlite")
    (source_root / "runtimes" / "galgame_plugin" / "RapidOCR").mkdir(parents=True)
    (source_root / "runtimes" / "galgame_plugin" / "RapidOCR" / "model.onnx").write_bytes(b"onnx")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert (target_root / "pngtuber" / "Alice" / "idle.png").read_bytes() == b"png"
    assert (target_root / "watch_together" / "library.sqlite3").read_bytes() == b"sqlite"
    assert (target_root / "runtimes" / "galgame_plugin" / "RapidOCR" / "model.onnx").is_file()
    assert {"pngtuber", "watch_together", "runtimes"} <= set(result["payload"]["copied_entries"])


@pytest.mark.unit
@pytest.mark.parametrize("entry_name", ["pngtuber", "watch_together", "runtimes", "embedding_models"])
def test_migration_requires_confirmation_when_target_only_holds_entries_added_later(tmp_path, entry_name):
    """The cloud-save probe does not know these entries; the target is still not empty."""
    config = _make_config_manager(tmp_path)
    config.app_docs_dir.mkdir(parents=True)
    target = tmp_path / "target" / "N.E.K.O"
    saved = target / entry_name / "existing.bin"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"existing")
    create_pending_storage_migration(config, source_root=config.app_docs_dir, target_root=target, selection_source="custom")

    result = run_pending_storage_migration(config)

    assert result["error_code"] == "target_confirmation_required"
    assert saved.read_bytes() == b"existing"


def _start_migration_into_empty_target(tmp_path, *, memory_as_file=False):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_root.mkdir(parents=True)
    if memory_as_file:
        (source_root / "memory").write_bytes(b"memory file")
    else:
        (source_root / "memory").mkdir()
        (source_root / "memory" / "facts.json").write_bytes(b"{}")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    return config_manager, target_root


def _crash_while_publishing(monkeypatch, config_manager, reserve):
    from utils import storage_migration as storage_migration_module

    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_reserving(staged, target):
        if Path(staged).name == "memory":
            reserve(Path(staged), Path(target))
            raise KeyboardInterrupt("simulated process loss")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_reserving)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", original_publish)


@pytest.mark.unit
def test_recovery_keeps_what_was_written_into_an_interrupted_reservation(tmp_path, monkeypatch):
    """A crash between reserving the name and the move leaves a visible empty
    directory; whatever is written into it afterwards is not ours to delete."""
    config_manager, target_root = _start_migration_into_empty_target(tmp_path)
    _crash_while_publishing(monkeypatch, config_manager, lambda staged, target: os.mkdir(target))
    (target_root / "memory" / "written-later.json").write_bytes(b"keep")

    retry = run_pending_storage_migration(config_manager)

    assert retry["completed"] is False
    assert (target_root / "memory" / "written-later.json").read_bytes() == b"keep"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="Windows publishes without a reservation or link")
def test_recovery_removes_its_own_empty_reservation(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, target_root = _start_migration_into_empty_target(tmp_path)
    _crash_while_publishing(monkeypatch, config_manager, lambda staged, target: os.mkdir(target))

    def _stop_after_recovery(*_args, **_kwargs):
        raise StorageMigrationError("stop_after_recovery", "inspect rolled back target")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _stop_after_recovery)
    stopped = run_pending_storage_migration(config_manager)

    assert stopped["error_code"] == "stop_after_recovery"
    assert not os.path.lexists(target_root / "memory")


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="Windows publishes without a reservation or link")
def test_recovery_removes_its_own_hard_link_to_the_staged_file(tmp_path, monkeypatch):
    """A file is published by linking it in, then unlinking the staged name."""
    config_manager, target_root = _start_migration_into_empty_target(tmp_path, memory_as_file=True)
    _crash_while_publishing(monkeypatch, config_manager, os.link)

    retry = run_pending_storage_migration(config_manager)

    assert retry["completed"] is True, retry
    assert (target_root / "memory").read_bytes() == b"memory file"


def _crash_while_moving_read_only_target(monkeypatch, tmp_path, *, after_move):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    (target_root / "config").chmod(0o555)
    read_only_mode = stat.S_IMODE((target_root / "config").stat().st_mode)

    def _crash_mid_move(source, destination):
        mode = stat.S_IMODE(Path(source).lstat().st_mode)
        os.chmod(source, mode | stat.S_IWUSR)
        if after_move:
            os.replace(source, destination)
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_move_entry_keeping_mode", _crash_mid_move)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()
    _stop_after_recovery(monkeypatch, storage_migration_module)
    try:
        retry = run_pending_storage_migration(config_manager)
        restored_mode = stat.S_IMODE((target_root / "config").stat().st_mode)
    finally:
        for leftover in [target_root / "config", *(target_root / ".smtx").glob("*/backup/config")]:
            if leftover.exists():
                leftover.chmod(0o755)
    return retry, restored_mode, read_only_mode


@pytest.mark.unit
@pytest.mark.parametrize("after_move", [False, True], ids=["before-move", "after-move"])
def test_rollback_restores_the_mode_of_a_read_only_target_directory(tmp_path, monkeypatch, after_move):
    """The move into the backup widens a read-only directory; a process exit
    before the mode is put back must not leave the original widened."""
    retry, restored_mode, read_only_mode = _crash_while_moving_read_only_target(
        monkeypatch, tmp_path, after_move=after_move
    )

    assert retry["error_code"] == "stop_after_recovery"
    assert restored_mode == read_only_mode


def _recreate_target_at_publish(monkeypatch, storage_migration_module, *, then):
    original_publish = storage_migration_module._publish_without_overwrite

    def _recreate_then_publish(staged, target):
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "newcomer.json").write_text("newcomer", encoding="utf-8")
        if then == "crash":
            raise KeyboardInterrupt("simulated process loss")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _recreate_then_publish)


@pytest.mark.unit
def test_publish_conflict_survives_a_failed_conflict_checkpoint_write(tmp_path, monkeypatch):
    """Rollback must not restore the backup over a newcomer just because the
    conflict could not be written down."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    _recreate_target_at_publish(monkeypatch, storage_migration_module, then="publish")
    original_persist = storage_migration_module._persist_migration_payload

    def _persist(*args, **kwargs):
        if kwargs.get("publish_conflict_entry"):
            raise OSError("state directory full")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _persist)

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "migration_publish_conflict"
    assert (target_root / "config" / "newcomer.json").read_text(encoding="utf-8") == "newcomer"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [backup.read_text(encoding="utf-8") for backup in backups] == ["healthy"]


@pytest.mark.unit
def test_publish_conflict_is_found_again_after_a_process_exit(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    _recreate_target_at_publish(monkeypatch, storage_migration_module, then="crash")
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_publish_conflict"
    assert (target_root / "config" / "newcomer.json").read_text(encoding="utf-8") == "newcomer"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [backup.read_text(encoding="utf-8") for backup in backups] == ["healthy"]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="the hard-link fallback is the POSIX file path")
def test_file_publish_without_hard_links_prefers_the_no_replace_rename(tmp_path, monkeypatch):
    """Without hard links, an O_EXCL reservation is replaceable by rename(2)
    after another process has written into it; use the atomic no-replace
    rename whenever the system offers one."""
    import errno

    from utils import storage_migration as storage_migration_module

    staged = tmp_path / "staged.json"
    staged.write_text("staged", encoding="utf-8")
    target = tmp_path / "target.json"
    calls = []

    def _no_hard_links(_source, _destination):
        raise OSError(errno.EPERM, "hard links unsupported")

    def _no_replace(source, destination):
        calls.append((Path(source), Path(destination)))
        os.rename(source, destination)
        return True

    def _no_reservation(*_args, **_kwargs):
        raise AssertionError("the O_EXCL reservation is only the last resort")

    monkeypatch.setattr(storage_migration_module.os, "link", _no_hard_links)
    monkeypatch.setattr(storage_migration_module, "_rename_no_replace", _no_replace)
    monkeypatch.setattr(storage_migration_module.os, "open", _no_reservation)

    storage_migration_module._publish_without_overwrite(staged, target)

    assert calls == [(staged, target)]
    assert target.read_text(encoding="utf-8") == "staged"


@pytest.mark.unit
@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="renameat2/renamex_np")
def test_native_no_replace_rename_refuses_an_existing_target(tmp_path):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "source.json"
    source.write_text("source", encoding="utf-8")
    target = tmp_path / "target.json"
    target.write_text("newcomer", encoding="utf-8")

    try:
        storage_migration_module._rename_no_replace(source, target)
    except FileExistsError:
        pass
    else:
        if storage_migration_module._native_no_replace_rename() is None:
            pytest.skip("no native no-replace rename in this libc")
        raise AssertionError("an existing target must be refused")
    assert target.read_text(encoding="utf-8") == "newcomer"

    target.unlink()
    assert storage_migration_module._rename_no_replace(source, target) is True
    assert target.read_text(encoding="utf-8") == "source"
    assert not source.exists()


@pytest.mark.unit
def test_no_replace_rename_reports_unsupported_systems(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    monkeypatch.setattr(storage_migration_module, "_native_no_replace_rename", lambda: None)
    source = tmp_path / "source.json"
    source.write_text("source", encoding="utf-8")

    assert storage_migration_module._rename_no_replace(source, tmp_path / "target.json") is False
    assert source.read_text(encoding="utf-8") == "source"


def _link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        import subprocess

        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if created.returncode != 0:
            pytest.skip(f"junction creation is unavailable: {created.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.unit
@pytest.mark.parametrize("linked_dir", ["backup", "stage", "transaction"])
def test_recovery_refuses_linked_transaction_directories(tmp_path, monkeypatch, linked_dir):
    """Rollback must not move or delete entries through a linked transaction directory."""
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    (transaction_root,) = (target_root / ".smtx").iterdir()
    external = tmp_path / "external"
    real = transaction_root if linked_dir == "transaction" else transaction_root / linked_dir
    shutil.copytree(real, external)
    (external / "config").mkdir(exist_ok=True)
    (external / "config" / "sentinel.txt").write_text("keep", encoding="utf-8")
    shutil.rmtree(real)
    _link_directory(real, external)

    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_rollback_required"
    assert (external / "config" / "sentinel.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.unit
def test_read_only_workshop_config_is_rebased(tmp_path):
    """The staged copy keeps the read-only mode, and Windows refuses to
    replace a read-only file: the rewrite must lift it and put it back."""
    import json

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    workshop_file = source_root / "config" / "workshop_config.json"
    workshop_file.write_text(json.dumps({"user_mod_folder": str(source_root / "mods")}), encoding="utf-8")
    workshop_file.chmod(stat.S_IREAD)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    try:
        result = run_pending_storage_migration(config_manager)
        published = target_root / "config" / "workshop_config.json"
        published_writable = bool(published.exists() and published.stat().st_mode & stat.S_IWUSR)
    finally:
        for path in (workshop_file, target_root / "config" / "workshop_config.json"):
            if path.exists():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert result["completed"] is True, result
    rebased = json.loads(published.read_text(encoding="utf-8"))
    assert rebased["user_mod_folder"] == str((target_root / "mods").resolve())
    assert published_writable is False


def _reuse_target_whose_config_differs(tmp_path, *, target_points_into_source):
    import json

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    for root in (source_root, target_root):
        (root / "workshop" / "mods").mkdir(parents=True)
        (root / "workshop" / "mods" / "item.txt").write_bytes(b"same")
        (root / "config").mkdir(parents=True)
    (source_root / "config" / "workshop_config.json").write_text(
        json.dumps({"user_mod_folder": str(source_root / "workshop" / "mods")}), encoding="utf-8"
    )
    kept_folder = source_root / "workshop" / "mods" if target_points_into_source else tmp_path / "elsewhere"
    (target_root / "config" / "workshop_config.json").write_text(
        json.dumps({"user_mod_folder": str(kept_folder), "kept": True}), encoding="utf-8"
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    return run_pending_storage_migration(config_manager)


@pytest.mark.unit
def test_kept_target_config_pointing_into_the_source_withholds_cleanup_evidence(tmp_path):
    """The target's own config is kept as it is; while it still points into
    the source's workshop, that source copy must not become deletable."""
    result = _reuse_target_whose_config_differs(tmp_path, target_points_into_source=True)

    assert result["completed"] is True, result
    assert "workshop" not in result["payload"]["copied_entries"]


@pytest.mark.unit
def test_kept_target_config_pointing_elsewhere_keeps_cleanup_evidence(tmp_path):
    result = _reuse_target_whose_config_differs(tmp_path, target_points_into_source=False)

    assert result["completed"] is True, result
    assert "workshop" in result["payload"]["copied_entries"]


@pytest.mark.unit
def test_config_pointing_at_the_source_root_references_every_entry(tmp_path):
    import json

    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source" / "N.E.K.O"
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "workshop_config.json").write_text(
        json.dumps({"default_workshop_folder": str(source_root)}), encoding="utf-8"
    )

    referenced = storage_migration_module._source_entries_referenced_by_config(
        config_root=tmp_path / "config", source_root=source_root
    )

    assert referenced == set(storage_migration_module.MIGRATED_RUNTIME_ENTRY_NAMES)


@pytest.mark.unit
def test_unreadable_config_references_every_entry(tmp_path):
    from utils import storage_migration as storage_migration_module

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "workshop_config.json").write_bytes(b"{not json")

    referenced = storage_migration_module._source_entries_referenced_by_config(
        config_root=tmp_path / "config", source_root=tmp_path / "source" / "N.E.K.O"
    )

    assert referenced == set(storage_migration_module.MIGRATED_RUNTIME_ENTRY_NAMES)


@pytest.mark.unit
def test_source_written_after_staging_is_not_published(tmp_path, monkeypatch):
    """A write to the source after its copy was taken must stop the
    migration, not publish the copy from before it."""
    from utils import storage_migration as storage_migration_module

    config_manager, source_root, target_root = _overwrite_migration(tmp_path)
    original_copy = storage_migration_module._copy_runtime_entry

    def _copy_then_source_changes(source_path, target_path):
        widened = original_copy(source_path, target_path)
        (Path(source_path) / "characters.json").write_text("newer", encoding="utf-8")
        return widened

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", _copy_then_source_changes)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "verification_failed"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"
    assert (source_root / "config" / "characters.json").read_text(encoding="utf-8") == "newer"


@pytest.mark.unit
def test_metadata_fingerprint_sees_added_and_rewritten_files(tmp_path):
    from utils import storage_migration as storage_migration_module

    entry = tmp_path / "memory"
    (entry / "nested").mkdir(parents=True)
    (entry / "nested" / "facts.json").write_bytes(b"{}")
    before = storage_migration_module._metadata_fingerprint(entry)
    assert storage_migration_module._metadata_fingerprint(entry) == before

    (entry / "nested" / "facts.json").write_bytes(b'{"a": 1}')
    rewritten = storage_migration_module._metadata_fingerprint(entry)
    (entry / "nested" / "new.json").write_bytes(b"{}")

    assert rewritten != before
    assert storage_migration_module._metadata_fingerprint(entry) != rewritten


@pytest.mark.unit
def test_rollback_leaves_an_untouched_original_target_unlisted(tmp_path, monkeypatch):
    """Stopped before the original target moved into the backup: that target
    is never touched, so it must not need to be listable (0o300, ACLs)."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)

    def _crash_before_move(_source, _destination):
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_move_entry_keeping_mode", _crash_before_move)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    def _cannot_list(*_args, **_kwargs):
        raise PermissionError("cannot list the original target")

    monkeypatch.setattr(storage_migration_module, "_holds_only_own_publish_reservation", _cannot_list)
    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"


@pytest.mark.unit
def test_no_replace_rename_blocked_by_seccomp_falls_back(tmp_path, monkeypatch):
    import ctypes
    import errno

    from utils import storage_migration as storage_migration_module

    def _blocked(_source, _target):
        ctypes.set_errno(errno.EPERM)
        return -1

    monkeypatch.setattr(storage_migration_module, "_native_no_replace_rename", lambda: _blocked)
    source = tmp_path / "source.json"
    source.write_text("source", encoding="utf-8")

    assert storage_migration_module._rename_no_replace(source, tmp_path / "target.json") is False
    assert source.read_text(encoding="utf-8") == "source"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="the reservation is the POSIX directory path")
def test_directory_publish_prefers_the_no_replace_rename(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "facts.json").write_bytes(b"{}")
    target = tmp_path / "target"

    def _no_reservation(*_args, **_kwargs):
        raise AssertionError("the mkdir reservation is only the fallback")

    def _no_replace(source, destination):
        os.rename(source, destination)
        return True

    monkeypatch.setattr(storage_migration_module, "_rename_no_replace", _no_replace)
    monkeypatch.setattr(storage_migration_module.os, "mkdir", _no_reservation)

    storage_migration_module._publish_without_overwrite(staged, target)

    assert (target / "facts.json").read_bytes() == b"{}"


@pytest.mark.unit
def test_migrated_entry_content_ignores_noise_and_empty_directories(tmp_path):
    from utils import storage_migration as storage_migration_module

    root = tmp_path / "N.E.K.O"
    (root / "pngtuber" / "Alice" / "frames").mkdir(parents=True)
    (root / "pngtuber" / ".DS_Store").write_bytes(b"finder")
    (root / "runtimes" / "__pycache__").mkdir(parents=True)
    (root / "runtimes" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
    assert storage_migration_module.root_has_migrated_entry_content(root) is False

    (root / "pngtuber" / "Alice" / "frames" / "idle.png").write_bytes(b"png")
    assert storage_migration_module.root_has_migrated_entry_content(root) is True



@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_rebased_workshop_config_keeps_its_mode(tmp_path):
    """The rewrite replaces the file with one created 0600; the published
    config must keep the mode it had."""
    import json

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    workshop_file = source_root / "config" / "workshop_config.json"
    workshop_file.write_text(json.dumps({"user_mod_folder": str(source_root / "mods")}), encoding="utf-8")
    workshop_file.chmod(0o644)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    published = target_root / "config" / "workshop_config.json"
    assert json.loads(published.read_text(encoding="utf-8"))["user_mod_folder"] == str((target_root / "mods").resolve())
    assert published.stat().st_mode & 0o777 == 0o644


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="only Windows never reserves the name")
def test_recovery_keeps_an_empty_entry_created_after_the_interruption(tmp_path, monkeypatch):
    """Windows publishes with one plain rename, so even an empty entry at the
    target after an interruption was created by someone else."""
    from utils import storage_migration as storage_migration_module

    config_manager, target_root = _start_migration_into_empty_target(tmp_path)
    _crash_while_publishing(monkeypatch, config_manager, lambda staged, target: None)
    (target_root / "memory").mkdir()

    _stop_after_recovery(monkeypatch, storage_migration_module)
    stopped = run_pending_storage_migration(config_manager)

    assert stopped["error_code"] == "stop_after_recovery"
    assert (target_root / "memory").is_dir()



def _crash_publishing_memory_after_config(tmp_path, monkeypatch):
    """Fresh target; config is published and recorded, then the process dies
    while publishing memory."""
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text("migrated", encoding="utf-8")
    (source_root / "memory").mkdir()
    (source_root / "memory" / "facts.json").write_bytes(b"{}")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_on_memory(staged, target):
        if Path(staged).name == "memory":
            raise KeyboardInterrupt("simulated process loss")
        original_publish(staged, target)

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_on_memory)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()
    return config_manager, target_root


@pytest.mark.unit
def test_recovery_keeps_a_published_target_written_since(tmp_path, monkeypatch):
    """A sync client wrote into the published config while the app was down;
    rolling back would delete that write."""
    config_manager, target_root = _crash_publishing_memory_after_config(tmp_path, monkeypatch)
    (target_root / "config" / "characters.json").write_text("written since", encoding="utf-8")

    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_publish_conflict"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "written since"


@pytest.mark.unit
def test_recovery_still_rolls_back_an_unchanged_published_target(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, target_root = _crash_publishing_memory_after_config(tmp_path, monkeypatch)

    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_recovery_stops_when_target_and_backup_are_both_gone(tmp_path, monkeypatch):
    """Moved into the backup, then the backup was lost: the original is gone
    and a retry must not treat it as never touched."""
    import shutil

    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_move = storage_migration_module._move_entry_keeping_mode

    def _crash_after_move(source, destination):
        original_move(source, destination)
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_move_entry_keeping_mode", _crash_after_move)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()
    (backup,) = (target_root / ".smtx").glob("*/backup/config")
    shutil.rmtree(backup)

    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_rollback_required"
    assert list((target_root / ".smtx").glob("*/stage/config"))


@pytest.mark.unit
def test_kept_target_config_withholds_evidence_for_an_entry_copied_into_the_target(tmp_path):
    """The target lacks workshop, so it is copied over; the kept target
    config still points at the source's workshop, which cleanup must keep."""
    import json

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "workshop" / "mods").mkdir(parents=True)
    (source_root / "workshop" / "mods" / "item.txt").write_bytes(b"mod")
    for root in (source_root, target_root):
        (root / "config").mkdir(parents=True)
    (source_root / "config" / "workshop_config.json").write_text("{}", encoding="utf-8")
    (target_root / "config" / "workshop_config.json").write_text(
        json.dumps({"user_mod_folder": str(source_root / "workshop" / "mods")}), encoding="utf-8"
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result
    assert (target_root / "workshop" / "mods" / "item.txt").read_bytes() == b"mod"
    assert "workshop" not in result["payload"]["copied_entries"]


@pytest.mark.unit
def test_migrated_entry_content_counts_a_transaction_backup(tmp_path):
    from utils import storage_migration as storage_migration_module

    root = tmp_path / "N.E.K.O"
    backup = root / "avatar_tools" / ".local-12345678-1234-4123-8123-123456789abc.backup"
    backup.mkdir(parents=True)

    assert storage_migration_module.root_has_migrated_entry_content(root, ["avatar_tools"]) is True


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="POSIX names may be any bytes")
def test_manifest_accepts_a_name_that_is_not_utf8(tmp_path):
    from utils import storage_migration as storage_migration_module

    entry = tmp_path / "memory"
    entry.mkdir()
    with open(os.path.join(os.fsencode(entry), b"\xff-imported.bin"), "wb") as stream:
        stream.write(b"asset")

    manifest = storage_migration_module.snapshot_runtime_entry(entry)

    assert manifest["file_count"] == 1
    assert storage_migration_module.snapshot_runtime_entry(entry) == manifest


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="only POSIX exposes a change time")
def test_metadata_fingerprint_sees_a_rewrite_that_restored_the_mtime(tmp_path):
    import time

    from utils import storage_migration as storage_migration_module

    entry = tmp_path / "memory"
    entry.mkdir()
    facts = entry / "facts.json"
    facts.write_bytes(b"aaaa")
    before_stat = facts.stat()
    before = storage_migration_module._metadata_fingerprint(entry)
    time.sleep(0.02)
    facts.write_bytes(b"bbbb")
    os.utime(facts, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    assert storage_migration_module._metadata_fingerprint(entry) != before


@pytest.mark.unit
def test_recovery_keeps_a_target_written_before_its_publish_was_recorded(tmp_path, monkeypatch):
    """Moved in, then written to by another program, and the process stopped
    before the publish was recorded: the staged copy is gone, so only the
    staged manifest can show the target is no longer that copy."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    original_publish = storage_migration_module._publish_without_overwrite

    def _publish_then_written_then_crash(staged, target):
        original_publish(staged, target)
        (Path(target) / "characters.json").write_text("written since", encoding="utf-8")
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _publish_then_written_then_crash)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "migration_publish_conflict"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "written since"
    backups = list((target_root / ".smtx").glob("*/backup/config/characters.json"))
    assert [backup.read_text(encoding="utf-8") for backup in backups] == ["healthy"]



@pytest.mark.unit
def test_anchor_cleanup_stays_available_for_an_entry_left_under_its_private_name(tmp_path):
    """The last migrated entry of the anchor root was renamed by a cleanup
    that stopped: the cleanup must stay available to put it back."""
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    current_root = tmp_path / "current" / "N.E.K.O"
    anchor_root.mkdir(parents=True)
    current_root.mkdir(parents=True)
    (anchor_root / "state").mkdir()

    def _available():
        return is_retained_root_cleanup_available(
            anchor_root,
            current_root=current_root,
            anchor_root=anchor_root,
            target_root=current_root,
            require_exists=True,
            allow_anchor_root=True,
        )

    assert not _available()
    (anchor_root / ".neko-cleanup-memory-0123456789ab").mkdir()
    assert _available()
    (anchor_root / ".neko-cleanup-memory-0123456789ab").rename(anchor_root / ".neko-cleanup-notanentry-0123456789ab")
    assert not _available()



@pytest.mark.unit
def test_policy_that_cannot_be_looked_up_is_not_taken_as_absent(tmp_path, monkeypatch):
    """A policy file an ACL hides is not "never written": it may already
    select the target, so the publish must not be rolled back."""
    from utils import storage_migration as storage_migration_module
    from utils.storage_policy import get_storage_policy_path

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)

    def _lost_during_policy_commit(*_args, **_kwargs):
        raise KeyboardInterrupt("simulated process loss while committing the policy")

    monkeypatch.setattr(storage_migration_module, "save_storage_policy", _lost_during_policy_commit)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    policy_path = get_storage_policy_path(config_manager)
    original_lstat = os.lstat

    def _lstat(path, *args, **kwargs):
        if Path(path) == policy_path:
            raise PermissionError(13, "access denied", str(path))
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "load_storage_policy", lambda *_a, **_k: None)
    monkeypatch.setattr(os, "lstat", _lstat)
    try:
        result = run_pending_storage_migration(config_manager)
    finally:
        monkeypatch.undo()

    assert result["error_code"] == "migration_commit_ambiguous"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "new"


@pytest.mark.unit
def test_reused_target_entry_appearing_before_publish_is_not_replaced(tmp_path, monkeypatch):
    """Reusing a target makes its entries authoritative: one a sync client
    creates after staging must not be swapped for the source copy."""
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    for root in (source_root, target_root):
        (root / "config").mkdir(parents=True)
        (root / "config" / "characters.json").write_text("same", encoding="utf-8")
    (source_root / "memory").mkdir()
    (source_root / "memory" / "facts.json").write_text("source", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _target_entry_appears_at_verifying(*args, **kwargs):
        if kwargs.get("status") == storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING:
            (target_root / "memory").mkdir(exist_ok=True)
            (target_root / "memory" / "facts.json").write_text("synced in", encoding="utf-8")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _target_entry_appears_at_verifying)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_changed_during_migration"
    assert (target_root / "memory" / "facts.json").read_text(encoding="utf-8") == "synced in"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="Windows publishes without a reservation or link")
def test_recovery_keeps_a_hard_link_written_through_before_recovery(tmp_path, monkeypatch):
    """Linked in, the staged name not yet removed, then written to through
    the target: both names show the write, so only the staged manifest can
    show the file is no longer the staged copy."""
    config_manager, target_root = _start_migration_into_empty_target(tmp_path, memory_as_file=True)
    _crash_while_publishing(monkeypatch, config_manager, os.link)
    with open(target_root / "memory", "ab") as stream:
        stream.write(b" + written since")

    run_pending_storage_migration(config_manager)

    assert (target_root / "memory").read_bytes() == b"memory file + written since"



@pytest.mark.unit
def test_v1_checkpoint_keeps_the_transaction_id_it_is_given(tmp_path, monkeypatch):
    """A v1 checkpoint has no txid. The one an upgraded attempt uses must be
    in the checkpoint before its transaction exists, or the next launch
    looks under another id and strands the first attempt's copies."""
    from utils import storage_migration as storage_migration_module

    config_manager, _source_root, target_root = _overwrite_migration(tmp_path)
    v1_payload = dict(load_storage_migration(config_manager))
    v1_payload["version"] = 1
    v1_payload.pop("txid", None)
    save_storage_migration(config_manager, v1_payload)
    original_publish = storage_migration_module._publish_without_overwrite

    def _crash_after_publish(staged, target):
        original_publish(staged, target)
        raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module, "_publish_without_overwrite", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt):
        run_pending_storage_migration(config_manager)
    monkeypatch.undo()

    txid = load_storage_migration(config_manager).get("txid")
    assert txid
    assert [path.name for path in (target_root / ".smtx").iterdir()] == [txid[:12]]

    _stop_after_recovery(monkeypatch, storage_migration_module)
    retry = run_pending_storage_migration(config_manager)

    assert retry["error_code"] == "stop_after_recovery"
    # The interrupted publish was found and rolled back: the original target is back.
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "healthy"
    assert not (target_root / ".smtx").exists()



@pytest.mark.unit
def test_reused_target_config_changed_after_staging_is_not_replaced(tmp_path, monkeypatch):
    """The reused target's config matched the source and was staged for the
    path rewrite; a sync client changing it meanwhile must stop the
    migration instead of having the change replaced by the staged copy."""
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    for root in (source_root, target_root):
        (root / "config").mkdir(parents=True)
        (root / "config" / "characters.json").write_text("same", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="legacy",
    )
    original_persist = storage_migration_module._persist_migration_payload

    def _config_changed_at_verifying(*args, **kwargs):
        if kwargs.get("status") == storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING:
            (target_root / "config" / "characters.json").write_text("synced in", encoding="utf-8")
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", _config_changed_at_verifying)

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "target_changed_during_migration"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "synced in"
