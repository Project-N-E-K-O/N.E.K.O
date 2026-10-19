"""Durable local handoff and exact receipts, using synthetic identities only."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore


OWNER = {"key": "owner-a", "user_id": "user-a", "community": "https://community.example"}
OTHER = {**OWNER, "key": "owner-b", "user_id": "user-b"}
ANONYMOUS = {"key": "anonymous", "user_id": None, "community": None}
TRACK = {"name": "Synthetic song", "artist": "Test artist", "url": "https://media.example/song?token=SECRET"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def receipt(batch):
    return {"schema_version": batch["schema_version"], "source_id": batch["source_id"],
            "batch_id": batch["batch_id"], "batch_hash": digest(batch),
            "results": [{"kind": item["kind"], "id": item["id"], "status": "stored",
                         "payload_hash": digest(item["payload"])} for item in batch["items"]]}


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(tmp_path / "isolated.sqlite3")


def recommend(store, owner=OWNER, **kwargs):
    return store.create(owner, "Private character name", "private-turn", TRACK,
                        {"proactiveMusicEnabled": True, "token": "PRIVATE"}, **kwargs)["id"]


def settings(revision=1):
    return {"revision": revision, "origin": "user", "occurred_at": 100.0,
            "previous": {"proactiveMusicEnabled": False}, "settings": {"proactiveMusicEnabled": True}}


def test_registration_without_exposure_is_enqueued_and_private_fields_excluded(store):
    rec = recommend(store)
    assert store.events(OWNER, 0, 100)["events"] == []
    batch = store.prepare_batch(OWNER)
    assert [(x["kind"], x["id"]) for x in batch["items"]] == [("recommendation", rec)]
    raw = json.dumps(batch)
    assert all(secret not in raw for secret in ("SECRET", "PRIVATE", "Private character", "private-turn", "https://"))
    assert batch["items"][0]["payload"]["settings"] == {"proactiveMusicEnabled": True}


def test_restart_retries_exact_batch_and_new_facts_wait_for_next_batch(store):
    rec = recommend(store)
    batch = store.prepare_batch(OWNER)
    store.record(OWNER, rec, {"event_id": "not-a-uuid", "kind": "shown"})
    reopened = RecommendationStore(store.path)
    assert reopened.prepare_batch(OWNER) == batch
    assert reopened.acknowledge_batch(OWNER, receipt(batch))
    later = reopened.prepare_batch(OWNER)
    assert later["batch_id"] != batch["batch_id"]
    assert [x["kind"] for x in later["items"]] == ["event"]
    assert later["items"][0]["id"] == "not-a-uuid"
    # Replaying an old acknowledgment cannot clear a later batch.
    assert reopened.acknowledge_batch(OWNER, receipt(batch))
    assert reopened.prepare_batch(OWNER) == later


@pytest.mark.parametrize("damage", ["hash", "source", "batch", "missing", "duplicate", "status", "payload", "extra"])
def test_inexact_receipts_never_acknowledge_any_member(store, damage):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "show", "kind": "shown"})
    batch = store.prepare_batch(OWNER)
    answer = receipt(batch)
    if damage == "hash":
        answer["batch_hash"] = "0" * 64
    elif damage == "source":
        answer["source_id"] = "another-source"
    elif damage == "batch":
        answer["batch_id"] = "another-batch"
    elif damage == "missing":
        answer["results"].pop()
    elif damage == "duplicate":
        answer["results"][-1] = answer["results"][0]
    elif damage == "status":
        answer["results"][0]["status"] = "retryable"
    elif damage == "payload":
        answer["results"][0]["payload_hash"] = "0" * 64
    else:
        answer["unexpected"] = True
    with pytest.raises(FeedbackConflict):
        store.acknowledge_batch(OWNER, answer)
    assert store.sync_status(OWNER)["pending"] == 2
    assert store.sync_status(OWNER)["acked"] == 0
    assert store.prepare_batch(OWNER) == batch


def test_account_isolation_and_owner_identity_collision(store):
    recommend(store)
    batch = store.prepare_batch(OWNER)
    assert store.prepare_batch(OTHER) is None
    with pytest.raises(FeedbackConflict):
        store.acknowledge_batch(OTHER, receipt(batch))
    with pytest.raises(FeedbackConflict, match="owner_conflict"):
        store.prepare_batch({**OWNER, "user_id": "another-user"})
    assert store.sync_status(OWNER)["pending"] == 1


def test_anonymous_records_stay_local_and_never_join_later_login(store):
    recommend(store, ANONYMOUS)
    store.enqueue_settings(ANONYMOUS, "anonymous-operation", settings())
    assert store.prepare_batch(ANONYMOUS) is None
    assert store.prepare_batch(OWNER) is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM feedback_settings_evidence").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM feedback_sync_outbox").fetchone()[0] == 0


def test_settings_import_is_idempotent_and_same_id_changed_body_conflicts(store):
    store.enqueue_settings(OWNER, "operation", settings())
    store.enqueue_settings(OWNER, "operation", settings())
    with pytest.raises(FeedbackConflict, match="collision"):
        store.enqueue_settings(OWNER, "operation", settings(2))
    batch = store.prepare_batch(OWNER)
    assert len(batch["items"]) == 1
    assert batch["items"][0]["payload"] == settings()


def test_new_outbox_capacity_rolls_back_decision_and_new_recommendation(store):
    rec = recommend(store)
    store.MAX_PENDING_UPLOADS = 1
    with pytest.raises(FeedbackConflict, match="capacity"):
        store.record(OWNER, rec, {"event_id": "accept", "kind": "accepted"})
    assert store.events(OWNER, 0, 100)["events"] == []
    with pytest.raises(FeedbackConflict, match="capacity"):
        recommend(store)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM recommendations").fetchone()[0] == 1
        assert db.execute("SELECT state FROM recommendations WHERE id=?", (rec,)).fetchone()[0] == "pending"


def test_ttl_does_not_expire_previously_recorded_facts_or_regenerate_payloads(store):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "accept", "kind": "accepted"})
    batch = store.prepare_batch(OWNER)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE recommendations SET expires_at=0,settings='{}'")
    assert RecommendationStore(store.path).prepare_batch(OWNER) == batch
    assert store.acknowledge_batch(OWNER, receipt(batch))


def test_event_uses_first_committed_timestamp_and_sanitizes_failure_details(store):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "accept", "kind": "accepted", "occurred_at": 100.0})
    store.record(OWNER, rec, {"event_id": "accept", "kind": "accepted", "occurred_at": 999.0})
    store.record(OWNER, rec, {"event_id": "failure", "kind": "failed", "playback_id": "p",
                            "reason": "https://media.example?token=SECRET"})
    batch = store.prepare_batch(OWNER)
    events = [i["payload"] for i in batch["items"] if i["kind"] == "event"]
    assert events[0]["occurred_at"] == 100.0
    assert events[1]["reason"] == "media_error"
    assert "SECRET" not in json.dumps(batch)


def test_vision_never_exports_screen_text_or_shared_empty_hash(store):
    recommend(store, category="vision")
    content = store.prepare_batch(OWNER)["items"][0]["payload"]["content"]
    assert all(content[key] == "" for key in ("name", "artist", "source", "content_id"))


def test_concurrent_batch_preparation_freezes_one_operation(store):
    recommend(store)
    with ThreadPoolExecutor(4) as pool:
        batches = list(pool.map(lambda _: store.prepare_batch(OWNER), range(4)))
    assert all(batch == batches[0] for batch in batches)


def test_permanent_failure_retains_frozen_data_and_blocks_restart_retry(store):
    recommend(store)
    batch = store.prepare_batch(OWNER)
    store.mark_batch_error(OWNER, batch["batch_id"], "remote_409", permanent=True)
    reopened = RecommendationStore(store.path)
    assert reopened.prepare_batch(OWNER) is None
    status = reopened.sync_status(OWNER)
    assert status["pending"] == 1 and status["blocked"] and status["attempts"] == 1
    assert status["last_error"] == "remote_409"
    # A real receipt for the frozen batch still acknowledges a previously
    # committed request whose response arrived late.
    assert reopened.acknowledge_batch(OWNER, receipt(batch))


def test_transient_failure_retains_identical_batch(store):
    recommend(store)
    batch = store.prepare_batch(OWNER)
    store.mark_batch_error(OWNER, batch["batch_id"], "network_timeout")
    assert store.prepare_batch(OWNER) == batch


def test_batch_item_and_byte_limits_and_bad_settings_do_not_lose_pending(store):
    for _ in range(3):
        recommend(store)
    batch = store.prepare_batch(OWNER, limit=1)
    assert len(batch["items"]) == 1
    with pytest.raises(FeedbackConflict, match="limit"):
        store.prepare_batch(OWNER, limit=0)
    with pytest.raises(FeedbackConflict, match="settings"):
        store.enqueue_settings(OWNER, "bad", {**settings(), "settings": {"api_key": "SECRET"}})
    assert store.acknowledge_batch(OWNER, receipt(batch))
    store.MAX_BATCH_BYTES = 5
    with pytest.raises(FeedbackConflict, match="large"):
        store.prepare_batch(OWNER)
    assert store.sync_status(OWNER)["pending"] == 2


def test_v1_migration_preserves_existing_facts_without_silent_history_upload(store):
    rec = recommend(store)
    with sqlite3.connect(store.path) as db:
        for table in ("feedback_sync_sources", "feedback_sync_outbox", "feedback_sync_batches",
                      "feedback_sync_receipts", "feedback_settings_evidence"):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=1")
    migrated = RecommendationStore(store.path)
    assert migrated.prepare_batch(OWNER) is None
    # A new event on an old recommendation registers that parent, not every old record.
    migrated.record(OWNER, rec, {"event_id": "new-show", "kind": "shown"})
    assert [i["kind"] for i in migrated.prepare_batch(OWNER)["items"]] == ["recommendation", "event"]


def test_v1_music_continuation_carries_original_accepted_dependency(store):
    rec = recommend(store)
    store.record(OWNER, rec, {"event_id": "original-accept", "kind": "accepted", "occurred_at": 123.0})
    store.record(OWNER, rec, {"event_id": "old-playing", "kind": "playing", "playback_id": "p"})
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM feedback_sync_outbox")
    store.record(OWNER, rec, {"event_id": "new-paused", "kind": "paused", "playback_id": "p", "listened_seconds": 30})
    items = store.prepare_batch(OWNER)["items"]
    assert [i["id"] for i in items] == [rec, "original-accept", "new-paused"]
    assert items[1]["payload"]["occurred_at"] == 123.0
    assert "old-playing" not in [i["id"] for i in items]


@pytest.mark.parametrize("invalid", [{"proactiveMusicEnabled": 1}, {"proactiveChatInterval": True},
                                    {"proactiveVisionInterval": 0}, {"proactiveChatInterval": 3601}])
def test_settings_type_mismatches_cannot_poison_immutable_uploads(store, invalid):
    with pytest.raises(FeedbackConflict, match="settings"):
        store.enqueue_settings(OWNER, "invalid", {**settings(), "settings": invalid})


@pytest.mark.parametrize("field,value", [
    ("origin", []), ("origin", {}), ("origin", None),
    ("revision", []), ("revision", True), ("revision", 0),
    ("occurred_at", []), ("occurred_at", True),
    ("occurred_at", float("nan")), ("occurred_at", float("inf")),
    ("occurred_at", 10 ** 400),
])
def test_deeply_invalid_settings_are_typed_conflicts_without_committing(store, field, value):
    recommend(store)
    frozen = store.prepare_batch(OWNER)
    with pytest.raises(FeedbackConflict, match="invalid_settings_evidence"):
        store.enqueue_settings(OWNER, "invalid", {**settings(), field: value})
    assert store.prepare_batch(OWNER) == frozen
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM feedback_settings_evidence").fetchone()[0] == 0


@pytest.mark.parametrize("owner,entry_id", [
    ({**OWNER, "key": []}, "entry"), ({"key": "missing-fields"}, "entry"),
    ({**OWNER, "user_id": []}, "entry"), ({**OWNER, "community": {}}, "entry"),
    (OWNER, []),
])
def test_invalid_settings_identity_is_a_conflict_before_sqlite(store, owner, entry_id):
    with pytest.raises(FeedbackConflict, match="invalid_settings_evidence"):
        store.enqueue_settings(owner, entry_id, settings())
    assert not store.path.exists()


@pytest.mark.parametrize("occurred_at", [0, 100, 100.5])
def test_settings_numeric_timestamps_and_existing_non_uuid_ids_remain_compatible(store, occurred_at):
    payload = {**settings(), "occurred_at": occurred_at}
    store.enqueue_settings(OWNER, "settings-1", payload)
    item, = store.prepare_batch(OWNER)["items"]
    assert item["id"] == "settings-1" and item["payload"] == payload


@pytest.mark.parametrize("kind", ["shown", "accepted", "skipped"])
@pytest.mark.parametrize("fields", [{"playback_id": "bad"}, {"listened_seconds": 1}, {"position_seconds": 1}])
def test_non_playback_fields_are_rejected_before_local_commit(store, kind, fields):
    rec = recommend(store)
    with pytest.raises(FeedbackConflict, match="observation_fields"):
        store.record(OWNER, rec, {"event_id": "bad", "kind": kind, **fields})
    assert store.events(OWNER, 0, 100)["events"] == []
    assert store.sync_status(OWNER)["pending"] == 1
