"""Backup and compatible rollback drills use temporary synthetic databases only."""
import hashlib
import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from main_logic.recommendation_feedback import RecommendationStore
from scripts import backup_proactive_feedback as backup
from tests.fake_clock import patch_module_clock


OWNER = {"key": "a", "user_id": "synthetic-a", "community": "https://community.example"}
TRACK = {"name": "Fixture", "artist": "Fixture", "url": "https://media.example/test"}


def create_store(tmp_path):
    store = RecommendationStore(tmp_path / "original.sqlite3")
    store.create(OWNER, "Fixture", "turn", TRACK, {})
    return store


def test_backup_live_wal_has_committed_snapshot_and_does_not_modify_original(tmp_path):
    store = create_store(tmp_path)
    batch = store.prepare_batch(OWNER)
    store.mark_batch_error(OWNER, batch["batch_id"], "remote_409", permanent=True)
    with sqlite3.connect(store.path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE feedback_sync_batches SET attempts=7")
        writer.commit()
        writer.execute("UPDATE feedback_sync_batches SET attempts=999")  # Uncommitted.
        output = tmp_path / "verified"
        manifest = backup.backup_feedback(store.path, output)
        assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == manifest
        assert manifest["sqlite_schema_version"] == 2
        assert manifest["tables"]["feedback_sync_outbox"] == 1
        assert hashlib.sha256((output / "feedback.sqlite3").read_bytes()).hexdigest() == manifest["sha256"]
        with sqlite3.connect(output / "feedback.sqlite3") as snapshot:
            assert snapshot.execute("SELECT attempts FROM feedback_sync_batches").fetchone()[0] == 7
            assert json.loads(snapshot.execute("SELECT body FROM feedback_sync_batches").fetchone()[0]) == batch
        assert writer.execute("SELECT attempts FROM feedback_sync_batches").fetchone()[0] == 999
        writer.rollback()
    assert store.sync_status(OWNER)["attempts"] == 7
    assert store.prepare_batch(OWNER) is None


def test_backup_can_be_opened_only_in_separate_v2_rollback_copy_without_losing_outbox(tmp_path):
    store = create_store(tmp_path)
    batch = store.prepare_batch(OWNER)
    output = tmp_path / "verified"
    manifest = backup.backup_feedback(store.path, output)
    # A rollback drill never writes into the preservation snapshot or replaces live files.
    rollback_copy = tmp_path / "separate-v2-rollback.sqlite3"
    shutil.copyfile(output / "feedback.sqlite3", rollback_copy)
    compatible_release = RecommendationStore(rollback_copy)
    assert compatible_release.prepare_batch(OWNER) == batch
    assert compatible_release.sync_status(OWNER)["pending"] == 1
    assert store.prepare_batch(OWNER) == batch
    assert hashlib.sha256((output / "feedback.sqlite3").read_bytes()).hexdigest() == manifest["sha256"]
    assert "does not downgrade" in manifest["application_rollback"]


def test_existing_destination_is_never_overwritten(tmp_path):
    store = create_store(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    artifact = output / "feedback.sqlite3"
    artifact.write_bytes(b"irreplaceable")
    with pytest.raises(FileExistsError):
        backup.backup_feedback(store.path, output)
    assert artifact.read_bytes() == b"irreplaceable"
    assert store.sync_status(OWNER)["pending"] == 1


def test_timeout_preserves_owned_partial_directory_without_claiming_success(tmp_path, monkeypatch):
    store = create_store(tmp_path)
    times = iter([0, 0, 100])
    patch_module_clock(monkeypatch, backup, monotonic=lambda: next(times))
    output = tmp_path / "timed-out"
    with pytest.raises(TimeoutError):
        backup.backup_feedback(store.path, output, timeout_seconds=1)
    assert (output / "backup-in-progress.json").is_file()
    assert (output / "feedback.sqlite3").exists()
    assert not (output / "manifest.json").exists()
    assert store.sync_status(OWNER)["pending"] == 1


def test_deadline_interrupts_real_sql_verification_after_backup(tmp_path, monkeypatch):
    store = create_store(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TABLE synthetic_verification_work(value TEXT)")
        db.executemany("INSERT INTO synthetic_verification_work VALUES (?)", [(str(i),) for i in range(10_000)])
    clock = [0]
    interrupts = []
    patch_module_clock(monkeypatch, backup, monotonic=lambda: clock[0])

    class DeadlineConnection(sqlite3.Connection):
        def set_progress_handler(self, callback, instructions):
            if callback is None:
                return super().set_progress_handler(None, 0)

            def expire_during_real_query():
                interrupts.append(True)
                clock[0] = 100
                return callback()

            return super().set_progress_handler(expire_during_real_query, instructions)

    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=DeadlineConnection)

    monkeypatch.setattr(backup, "sqlite3", SimpleNamespace(connect=connect, OperationalError=sqlite3.OperationalError))
    output = tmp_path / "sql-deadline"
    with pytest.raises(TimeoutError):
        backup.backup_feedback(store.path, output, timeout_seconds=1)
    assert interrupts  # Actual SQLite VM work was interrupted, not a mocked PRAGMA result.
    assert not (output / "manifest.json").exists()
    with sqlite3.connect(output / "feedback.sqlite3") as snapshot:
        assert snapshot.execute("SELECT count(*) FROM synthetic_verification_work").fetchone()[0] == 10_000
        assert snapshot.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert (output / "backup-in-progress.json").exists()
    assert store.sync_status(OWNER)["pending"] == 1


def test_hash_deadline_does_not_publish_complete_manifest(tmp_path, monkeypatch):
    store = create_store(tmp_path)
    clock = [0]
    patch_module_clock(monkeypatch, backup, monotonic=lambda: clock[0])
    real_sha = hashlib.sha256

    class SlowHash:
        def __init__(self):
            self.actual = real_sha()

        def update(self, data):
            self.actual.update(data)
            clock[0] = 100

        def hexdigest(self):
            return self.actual.hexdigest()

    monkeypatch.setattr(backup, "hashlib", SimpleNamespace(sha256=SlowHash))
    output = tmp_path / "hash-deadline"
    with pytest.raises(TimeoutError):
        backup.backup_feedback(store.path, output, timeout_seconds=1)
    assert not (output / "manifest.json").exists()
    assert (output / "feedback.sqlite3").exists()
    assert (output / "backup-in-progress.json").exists()
    assert store.sync_status(OWNER)["pending"] == 1


def test_manifest_fsync_expiring_deadline_preserves_staging_without_publication(tmp_path, monkeypatch):
    store = create_store(tmp_path)
    clock = [0]
    patch_module_clock(monkeypatch, backup, monotonic=lambda: clock[0])
    write_json = backup._write_json_exclusive

    def slow_manifest(path, value):
        write_json(path, value)
        if path.name == "manifest.json.tmp":
            clock[0] = 100

    monkeypatch.setattr(backup, "_write_json_exclusive", slow_manifest)
    output = tmp_path / "manifest-deadline"
    with pytest.raises(TimeoutError):
        backup.backup_feedback(store.path, output, timeout_seconds=1)
    assert not (output / "manifest.json").exists()
    assert (output / "manifest.json.tmp").exists()
    assert (output / "feedback.sqlite3").exists()
    assert store.sync_status(OWNER)["pending"] == 1


def test_manifest_publication_failure_preserves_valid_snapshot_and_staging(tmp_path, monkeypatch):
    store = create_store(tmp_path)

    def fail_replace(*args):
        raise PermissionError("synthetic publication failure")

    monkeypatch.setattr(backup, "os", SimpleNamespace(fsync=backup.os.fsync, replace=fail_replace))
    output = tmp_path / "publish-failed"
    with pytest.raises(PermissionError):
        backup.backup_feedback(store.path, output)
    assert (output / "backup-in-progress.json").is_file()
    assert (output / "manifest.json.tmp").is_file()
    assert not (output / "manifest.json").exists()
    with sqlite3.connect(output / "feedback.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("SELECT count(*) FROM feedback_sync_outbox").fetchone()[0] == 1


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_invalid_deadline_never_creates_destination(tmp_path, timeout):
    store = create_store(tmp_path)
    with pytest.raises(ValueError):
        backup.backup_feedback(store.path, tmp_path / "unused", timeout_seconds=timeout)
    assert not (tmp_path / "unused").exists()


def test_cli_missing_input_is_safe_and_reports_failed_output(tmp_path, capsys):
    output = tmp_path / "not-created"
    assert backup.main(["--database", str(tmp_path / "missing.sqlite"), "--output-dir", str(output)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not output.exists()


def test_cli_publishes_verified_artifact(tmp_path, capsys):
    store = create_store(tmp_path)
    output = tmp_path / "cli-backup"
    assert backup.main(["--database", str(store.path), "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (output / "manifest.json").exists()
