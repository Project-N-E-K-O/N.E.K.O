import errno


import json


import os


import shutil


import stat


import sys


import threading


from pathlib import Path


from types import SimpleNamespace


from unittest.mock import patch


import pytest


from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COPYING,
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_PREFLIGHT,
    STORAGE_MIGRATION_STATUS_PUBLISHING,
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


def _run_fifo_open_replacement(monkeypatch, storage_migration_module, victim, operation):
    real_open = storage_migration_module.os.open
    opened_flags = []
    replaced = False
    results = []
    errors = []

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if (
            not replaced
            and (
                Path(path) == victim
                or (dir_fd is not None and path == victim.name)
            )
        ):
            replaced = True
            victim.unlink()
            os.mkfifo(victim)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        storage_migration_module.os,
        "open",
        replace_with_fifo_before_open,
    )

    def run_operation():
        try:
            results.append(operation())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_operation, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        writer_fd = real_open(victim, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    return worker, replaced, opened_flags, results, errors


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
@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("version", True),
        ("version", 0),
        ("version", 3),
        ("version", "2"),
        ("status", "future_status"),
        ("txid", "not-a-transaction-id"),
        ("source_root", "relative/source"),
        ("target_root", ""),
        ("selection_source", ""),
        ("migration_mode", "adopt"),
        ("confirmed_existing_target_content", "false"),
        ("confirmed_existing_target_content", 1),
        ("confirmed_existing_target_content", None),
    ),
)
def test_load_storage_migration_rejects_invalid_checkpoint_schema(
    tmp_path,
    field_name,
    invalid_value,
):
    config_manager = _DummyConfigManager(tmp_path)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=tmp_path / "target" / "N.E.K.O",
        selection_source="custom",
    )
    payload[field_name] = invalid_value
    get_storage_migration_path(config_manager).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert caught.value.error_code == "migration_checkpoint_malformed"


@pytest.mark.unit
def test_load_storage_migration_accepts_complete_legacy_v1_checkpoint(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_file = config_manager.app_docs_dir / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_root = tmp_path / "target" / "N.E.K.O"
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    payload["version"] = 1
    for field_name in (
        "migration_mode",
        "transaction_owner_token",
        "transaction_cleanup_pending",
        "target_baseline",
        "original_target_entries",
        "publish_entry_names",
    ):
        payload.pop(field_name, None)
    get_storage_migration_path(config_manager).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    assert load_storage_migration(config_manager) == payload

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    upgraded = load_storage_migration(config_manager)
    assert upgraded["version"] == 2
    assert len(upgraded["transaction_owner_token"]) == 64
    assert (target_root / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "SOURCE"


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
def test_run_pending_storage_migration_copies_community_state_with_runtime_root(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    records = {
        "community_auth.json": {"access_token": "access"},
        "social_session.json": {"token": "session"},
        "community_oauth_pending.json": {"state": "oauth"},
        "community_steam_pending.json": {"state": "steam"},
    }
    source_root.mkdir(parents=True, exist_ok=True)
    for filename, payload in records.items():
        (source_root / filename).write_text(json.dumps(payload), encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    for filename, payload in records.items():
        assert json.loads((target_root / filename).read_text(encoding="utf-8")) == payload
    assert all((source_root / filename).exists() for filename in records)


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

    def corrupt_copy(source_path, target_path, **kwargs):
        original_copy(source_path, target_path, **kwargs)
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
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert "transaction_root" not in result["payload"]
    assert load_storage_migration(config_manager) == result["payload"]
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
    payload.update(
        status=STORAGE_MIGRATION_STATUS_PREFLIGHT,
        transaction_root=str(transaction_root),
        source_runtime_baseline=storage_migration_module._snapshot_runtime_entries(
            source_root
        ),
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


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
