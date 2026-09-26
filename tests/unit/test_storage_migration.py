import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_KNOWLEDGE_REPAIR_REQUIRED,
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


def _write_knowledge_tree(
    root: Path, *, marker: str = "source", wal: bool = False
) -> None:
    knowledge_root = root / "knowledge"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(knowledge_root / "knowledge.db")) as connection:
        if wal:
            # KnowledgeStore opens every writable connection in WAL mode, so a
            # production database carries WAL in its header.
            connection.execute("PRAGMA journal_mode=WAL")
        with connection:
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata VALUES ('schema_version', '7')")
            connection.execute("PRAGMA user_version = 7")
    (knowledge_root / "packs.json").write_text(
        '{"schema_version":1,"packs":{}}', encoding="utf-8"
    )
    (knowledge_root / "catalog_overrides.json").write_text("{}", encoding="utf-8")
    (knowledge_root / ".staging" / "job-1").mkdir(parents=True)
    (knowledge_root / ".staging" / "job-1" / "state.json").write_text(
        marker, encoding="utf-8"
    )
    (knowledge_root / ".staging" / "activation_commits.json").write_text(
        marker, encoding="utf-8"
    )
    (knowledge_root / ".staging" / "remove-intent.json").write_text(
        marker, encoding="utf-8"
    )


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
def test_storage_migration_copies_complete_knowledge_tree_with_digest_proof(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    proof = result["payload"]["copied_entries"]["knowledge"]
    assert proof["source_manifest"] == proof["target_manifest"]
    assert proof["source_manifest"]["manifest_digest"]
    assert (target_root / "knowledge" / "knowledge.db").is_file()
    assert (target_root / "knowledge" / "packs.json").is_file()
    assert (target_root / "knowledge" / "catalog_overrides.json").is_file()
    assert (
        target_root / "knowledge" / ".staging" / "job-1" / "state.json"
    ).read_text(encoding="utf-8") == "source"
    assert (
        target_root / "knowledge" / ".staging" / "activation_commits.json"
    ).is_file()
    assert (
        target_root / "knowledge" / ".staging" / "remove-intent.json"
    ).is_file()


def test_storage_migration_verifies_a_wal_knowledge_database_without_touching_it(
    tmp_path,
):
    """Read-only verification of a WAL database leaves -wal/-shm behind.

    Those sidecars land in the directory whose manifest was already taken, so
    verifying in place made every production (WAL) knowledge migration fail
    its digest proof.
    """
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root, wal=True)
    source_files = sorted(p.name for p in (source_root / "knowledge").iterdir())

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True, result.get("error_code")
    proof = result["payload"]["copied_entries"]["knowledge"]
    assert proof["source_manifest"] == proof["target_manifest"]
    assert sorted(p.name for p in (source_root / "knowledge").iterdir()) == source_files


@pytest.mark.unit
def test_v1_completed_migration_repairs_missing_knowledge_before_cleanup(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root)
    (target_root / "config").mkdir(parents=True)
    (target_root / "config" / "existing.json").write_text("{}", encoding="utf-8")
    save_storage_migration(
        config_manager,
        {
            "version": 1,
            "txid": "legacy-v1",
            "status": STORAGE_MIGRATION_STATUS_COMPLETED,
            "source_root": str(source_root),
            "target_root": str(target_root),
            "selection_source": "legacy",
            "retained_source_root": str(source_root),
        },
    )

    assert is_storage_migration_pending(load_storage_migration(config_manager)) is True
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert result["payload"]["version"] == 2
    assert "knowledge" in result["payload"]["copied_entries"]
    assert (target_root / "config" / "existing.json").is_file()
    assert (target_root / "knowledge" / "knowledge.db").is_file()


@pytest.mark.unit
def test_v1_completed_migration_blocks_conflicting_knowledge_repair(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root, marker="source")
    _write_knowledge_tree(target_root, marker="target")
    save_storage_migration(
        config_manager,
        {
            "version": 1,
            "txid": "legacy-v1-conflict",
            "status": STORAGE_MIGRATION_STATUS_COMPLETED,
            "source_root": str(source_root),
            "target_root": str(target_root),
            "selection_source": "legacy",
            "retained_source_root": str(source_root),
        },
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "knowledge_migration_repair_required"
    assert (
        result["payload"]["status"]
        == STORAGE_MIGRATION_STATUS_KNOWLEDGE_REPAIR_REQUIRED
    )
    assert (
        target_root / "knowledge" / ".staging" / "job-1" / "state.json"
    ).read_text(encoding="utf-8") == "target"

    retry = run_pending_storage_migration(config_manager)
    assert retry["completed"] is False
    assert retry["error_code"] == "knowledge_migration_repair_required"
    assert "knowledge" not in retry["payload"].get("copied_entries", {})


@pytest.mark.unit
def test_existing_target_content_does_not_skip_missing_source_knowledge(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root)
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
    assert (target_root / "knowledge" / "knowledge.db").is_file()
    assert "knowledge" in result["payload"]["copied_entries"]


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
    (source_root / "knowledge").mkdir(parents=True)
    try:
        os.symlink(external, source_root / "knowledge" / "linked", target_is_directory=True)
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
    assert not (target_root / "knowledge").exists()


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
def test_storage_migration_busy_knowledge_barrier_stays_retryable(tmp_path, monkeypatch):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    _write_knowledge_tree(source_root)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    def _busy_barrier(_root):
        raise TimeoutError("busy")

    monkeypatch.setattr("utils.storage_migration.knowledge_root_barrier", _busy_barrier)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "knowledge_mutation_busy"
    assert result["payload"]["status"] == "pending"
    assert load_storage_policy(config_manager) is None
    assert not (target_root / "knowledge").exists()


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
    original_replace = storage_migration_module.os.replace

    def _crash_after_publish(source, target):
        original_replace(source, target)
        if Path(source).name == "config" and Path(source).parent.name == "stage":
            raise KeyboardInterrupt("simulated process loss")

    monkeypatch.setattr(storage_migration_module.os, "replace", _crash_after_publish)
    with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
        run_pending_storage_migration(config_manager)
    monkeypatch.setattr(storage_migration_module.os, "replace", original_replace)

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
