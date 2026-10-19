"""Synthetic-only recovery, durable accounting and rollback preservation."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore


OWNER = {"key": "a", "user_id": "synthetic-a", "community": "https://community.example"}
OTHER = {**OWNER, "key": "b", "user_id": "synthetic-b"}
ANONYMOUS = {"key": "anonymous", "user_id": None, "community": None}
TRACK = {"name": "Fixture", "artist": "Fixture", "url": "https://media.example/test"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def receipt(batch):
    return {"schema_version": batch["schema_version"], "source_id": batch["source_id"],
            "batch_id": batch["batch_id"], "batch_hash": digest(batch),
            "results": [{"kind": item["kind"], "id": item["id"], "status": "stored",
                         "payload_hash": digest(item["payload"])} for item in batch["items"]]}


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(tmp_path / "facts.sqlite3")


def recommend(store, owner=OWNER):
    return store.create(owner, "Fixture", "fixture-turn", TRACK, {})["id"]


def blocked(store):
    recommend(store)
    batch = store.prepare_batch(OWNER)
    store.mark_batch_error(OWNER, batch["batch_id"], "remote_409", permanent=True)
    return batch


def test_resume_keeps_original_body_members_attempts_and_requires_explicit_cas(store):
    batch = blocked(store)
    recommend(store)  # This later item must not be inserted in the frozen batch.
    restarted = RecommendationStore(store.path)
    assert restarted.prepare_batch(OWNER) is None
    status = restarted.sync_status(OWNER)
    assert status["batch_hash"] == digest(batch)
    assert restarted.resume_blocked_batch(OWNER, batch["batch_id"], digest(batch))
    assert restarted.prepare_batch(OWNER) == batch
    assert restarted.sync_status(OWNER)["attempts"] == 1
    assert restarted.sync_status(OWNER)["last_error"] == ""
    with pytest.raises(FeedbackConflict, match="resume_conflict"):
        restarted.resume_blocked_batch(OWNER, batch["batch_id"], digest(batch))
    assert restarted.acknowledge_batch(OWNER, receipt(batch))
    assert len(restarted.prepare_batch(OWNER)["items"]) == 1


@pytest.mark.parametrize("damage", ["owner", "identity", "batch", "hash", "anonymous"])
def test_resume_wrong_snapshot_never_changes_frozen_operation(store, damage):
    batch = blocked(store)
    owner, batch_id, batch_hash = OWNER, batch["batch_id"], digest(batch)
    if damage == "owner":
        owner = OTHER
    elif damage == "identity":
        owner = {**OWNER, "user_id": "wrong-user"}
    elif damage == "anonymous":
        owner = ANONYMOUS
    elif damage == "batch":
        batch_id = "wrong-batch"
    else:
        batch_hash = "0" * 64
    with pytest.raises(FeedbackConflict):
        store.resume_blocked_batch(owner, batch_id, batch_hash)
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["blocked"]
    with sqlite3.connect(store.path) as db:
        assert json.loads(db.execute("SELECT body FROM feedback_sync_batches").fetchone()[0]) == batch


def test_late_ack_after_resume_or_before_resume_never_resurrects_batch(store):
    batch = blocked(store)
    store.acknowledge_batch(OWNER, receipt(batch))
    with pytest.raises(FeedbackConflict, match="resume_conflict"):
        store.resume_blocked_batch(OWNER, batch["batch_id"], digest(batch))
    assert store.prepare_batch(OWNER) is None
    second = blocked(store)
    store.resume_blocked_batch(OWNER, second["batch_id"], digest(second))
    assert store.acknowledge_batch(OWNER, receipt(second))
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["pending"] == 0


def test_concurrent_resume_and_ack_are_serialized_without_replacement(store):
    batch = blocked(store)
    start = Barrier(2)

    def resume():
        start.wait(timeout=5)
        try:
            return store.resume_blocked_batch(OWNER, batch["batch_id"], digest(batch))
        except FeedbackConflict:
            return False  # ACK already committed, which is also correct.

    def ack():
        start.wait(timeout=5)
        return store.acknowledge_batch(OWNER, receipt(batch))

    with ThreadPoolExecutor(2) as pool:
        resumed, acknowledged = pool.submit(resume), pool.submit(ack)
        assert acknowledged.result(timeout=10)
        assert resumed.result(timeout=10) in {True, False}
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["capacity"]["recommendations"]["awaiting_ack"] == 0


def test_ack_releases_quota_without_deleting_playback_dependencies_or_deduplication(store):
    store.MAX_RECOMMENDATIONS = store.MAX_EVENTS = 1
    store.MAX_PENDING_UPLOADS = 2
    first = recommend(store)
    store.record(OWNER, first, {"event_id": "accept", "kind": "accepted"})
    batch = store.prepare_batch(OWNER)
    with pytest.raises(FeedbackConflict, match="capacity"):
        recommend(store)
    store.acknowledge_batch(OWNER, receipt(batch))
    assert store.acknowledge_batch(OWNER, receipt(batch))  # Does not add another credit.
    later = recommend(store)
    assert later != first
    # Parent/acceptance stay local after ACK and grant a legitimate later media event.
    assert store.record(OWNER, first, {"event_id": "play", "kind": "playing", "playback_id": "p"})["applied"]
    with pytest.raises(FeedbackConflict, match="capacity"):
        store.record(OWNER, first, {"event_id": "pause", "kind": "paused", "playback_id": "p"})
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM recommendations").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM recommendation_events").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM feedback_sync_receipts").fetchone()[0] == 1
    assert not store.record(OWNER, first, {"event_id": "accept", "kind": "accepted"})["applied"]
    usage = store.sync_status(OWNER)["capacity"]
    assert (usage["recommendations"]["retained"], usage["recommendations"]["awaiting_ack"]) == (2, 1)
    assert (usage["events"]["retained"], usage["events"]["awaiting_ack"]) == (2, 1)
    assert usage["automatic_gc"] is False and usage["local_history_retained"] is True


def test_capacity_failure_and_failed_commit_roll_back_counters_and_decision(store):
    first = recommend(store)
    store.MAX_PENDING_UPLOADS = 1
    before = store.sync_status(OWNER)["capacity"]
    with pytest.raises(FeedbackConflict, match="capacity"):
        store.record(OWNER, first, {"event_id": "accept", "kind": "accepted"})
    assert store.sync_status(OWNER)["capacity"]["events"] == before["events"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state FROM recommendations").fetchone()[0] == "pending"
        db.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON feedback_sync_receipts
                   BEGIN SELECT RAISE(ABORT,'synthetic failed commit'); END""")
    batch = store.prepare_batch(OWNER)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
        store.acknowledge_batch(OWNER, receipt(batch))
    assert store.prepare_batch(OWNER) == batch
    assert store.sync_status(OWNER)["capacity"]["recommendations"]["awaiting_ack"] == 1
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_receipt")
    assert store.acknowledge_batch(OWNER, receipt(batch))
    assert store.sync_status(OWNER)["capacity"]["recommendations"]["awaiting_ack"] == 0


def test_anonymous_quota_is_bounded_and_never_reassigned_to_login(store):
    store.MAX_RECOMMENDATIONS = 1
    recommend(store, ANONYMOUS)
    with pytest.raises(FeedbackConflict, match="capacity"):
        recommend(store)
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["capacity"]["recommendations"]["awaiting_ack"] == 1


def test_existing_v2_upgrade_reconstructs_acked_and_legacy_usage_without_upload(store):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "shown", "kind": "shown"})
    store.acknowledge_batch(OWNER, receipt(store.prepare_batch(OWNER)))
    recommend(store, ANONYMOUS)
    with sqlite3.connect(store.path) as db:
        triggers = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'feedback_usage_%'")]
        for name in triggers:
            db.execute(f'DROP TRIGGER "{name}"')
        db.execute("DROP TABLE feedback_storage_usage")
        db.execute("ALTER TABLE feedback_sync_outbox DROP COLUMN created_at")
    reopened = RecommendationStore(store.path)
    usage = reopened.sync_status(OWNER)["capacity"]
    assert usage["recommendations"]["retained"] == 2
    assert usage["recommendations"]["awaiting_ack"] == 1
    assert usage["events"]["retained"] == 1
    assert usage["events"]["awaiting_ack"] == 0
    assert usage["uploads"]["awaiting_ack"] == 0
    assert reopened.prepare_batch(OWNER) is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_v1_database_migration_preserves_facts_without_implicitly_uploading_history(store):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "old-decision", "kind": "accepted"})
    with sqlite3.connect(store.path) as db:
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'feedback_usage_%'").fetchall():
            db.execute(f'DROP TRIGGER "{name}"')
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'feedback_%'").fetchall():
            db.execute(f'DROP TABLE "{name}"')
        db.execute("PRAGMA user_version=1")
    migrated = RecommendationStore(store.path)
    events = migrated.events(OWNER, 0, 100)["events"]
    assert len(events) == 1 and events[0]["event_id"] == "old-decision"
    assert migrated.prepare_batch(OWNER) is None
    usage = migrated.sync_status(OWNER)["capacity"]
    assert usage["recommendations"]["awaiting_ack"] == 1
    assert usage["events"]["awaiting_ack"] == 1
    assert usage["uploads"]["retained"] == 0
    # A real subsequent playback transfers only its persisted parent/acceptance dependencies.
    migrated.record(OWNER, rec, {"event_id": "continued-playback", "kind": "playing", "playback_id": "p"})
    items = migrated.prepare_batch(OWNER)["items"]
    assert [item["id"] for item in items] == [rec, "old-decision", "continued-playback"]


def test_status_reports_owner_receipt_ages_unknown_history_and_disk_warning(store):
    rec = recommend(store)
    batch = store.prepare_batch(OWNER)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE feedback_sync_outbox SET created_at=100")
        db.execute("UPDATE feedback_sync_batches SET created_at=200")
    store.HISTORY_WARNING_BYTES = 1
    status = store.sync_status(OWNER)
    assert status["oldest_pending_at"] == 100
    assert status["oldest_pending_age_seconds"] > 0
    assert status["batch_created_at"] == 200 and status["batch_age_seconds"] > 0
    assert status["capacity"]["db_bytes"] > 1 and status["capacity"]["history_warning"]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE feedback_sync_outbox SET created_at=NULL")
    assert store.sync_status(OWNER)["oldest_pending_at"] is None
    assert store.sync_status(OWNER)["pending_age_unknown_count"] == 1
    store.acknowledge_batch(OWNER, receipt(batch))
    latest = store.sync_status(OWNER)["last_acknowledged_batch"]
    assert latest["receipt"] == receipt(batch) and latest["batch_hash"] == digest(batch)
    assert latest["source_id"] == batch["source_id"]
    assert store.sync_status(OTHER)["last_acknowledged_batch"] is None
    assert store.events(OWNER, 0, 100)["events"] == []  # Registration alone is not exposure.
    assert rec


def test_steady_state_writes_use_durable_counters_not_full_history_scans(store, monkeypatch):
    rec = recommend(store)
    statements = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    store.record(OWNER, rec, {"event_id": "shown", "kind": "shown"})
    recommend(store)
    scanned = [sql for sql in statements if sql.strip().lower().startswith("select count(*) from")]
    assert scanned == []
