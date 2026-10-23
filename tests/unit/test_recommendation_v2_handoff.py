"""Real Store/SQLite v2 handoff, with synthetic accounts and clocks only."""
import copy
import sqlite3
import json
import subprocess
import sys
import base64
import hashlib

import pytest

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore
from services.proactive_feedback.protocol import fact_hash_v2, payload_hash


OWNER = {"key": "synthetic-owner", "user_id": "synthetic-user", "community": "https://community.example"}
OTHER = {**OWNER, "key": "other-owner", "user_id": "other-user"}
TRACK = {"name": "Fixture", "artist": "Fixture", "url": "https://media.example/fixture"}


def control(generation=0, revision=1):
    return {"version": "desktop-feedback-capture-v2", "owner_key": OWNER["key"],
            "account_incarnation": "incarnation", "clear_generation": generation,
            "registration_id": "registration", "registration_version": 1, "binding_revision": revision,
            "capture": {"clock_session_id": "session", "tick_ms": 1000, "anchor_id": "anchor"},
            "time_proof": {"anchor_token": "synthetic-token", "send_tick_ms": 900, "receive_tick_ms": 1000}}


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(tmp_path / "facts.sqlite3", protocol_version=2)


def offer(store, snapshot=None):
    return store.create(OWNER, "Fixture", "turn", TRACK, {}, control=snapshot or control())


def event(card, kind="shown", event_id="shown", snapshot=None, **extras):
    return {"event_id": event_id, "kind": kind, "occurred_at": 100,
            "offer_context": copy.deepcopy(card["offer_context"]),
            "context": copy.deepcopy(snapshot or control()), **extras}


def answer(batch, statuses=None):
    statuses = statuses or ["stored"] * len(batch["items"])
    return {key: batch[key] for key in ("schema_version", "batch_id", "source_id", "binding")} | {
        "batch_hash": payload_hash(batch), "results": [
            {"id": item["id"], "kind": item["kind"], "payload_hash": fact_hash_v2(item, batch["binding"]), "status": status}
            for item, status in zip(batch["items"], statuses)]}


@pytest.mark.parametrize("kind", ["measurement", "playing", "paused", "ended", "stopped", "failed"])
@pytest.mark.parametrize("patch", [
    {"version": "media-played-union-v1"}, {"epoch_id": ""},
    {"epoch_id": "x" * 101}, {"epoch_id": "bad\x00epoch"},
    {"range_start_ms": True}, {"range_end_ms": 1.5},
    {"range_start_ms": -1}, {"range_start_ms": 10},
    {"range_start_ms": 11}, {"range_end_ms": 86400001}, {"range_end_ms": 2**53},
])
def test_malformed_measurement_cannot_change_business_or_enter_spool(store, kind, patch):
    store.apply_control(OWNER, control())
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accepted"), control=control())
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT decision,state,playback_id FROM recommendations WHERE id=?", (card["id"],)).fetchone()
    pending = store.sync_status(OWNER)["pending"]
    measurement = {"version": "playback_elapsed_observed_v2", "epoch_id": "epoch",
                   "range_start_ms": 0, "range_end_ms": 10, "origin_context": control(), **patch}
    fact = event(card, kind, "malformed", playback_id="playback", measurement=measurement)
    original = copy.deepcopy(fact)
    with pytest.raises(FeedbackConflict, match="invalid_feedback_measurement"):
        store.record(OWNER, card["id"], fact, control=control())
    assert fact == original
    assert store.sync_status(OWNER)["pending"] == pending
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT decision,state,playback_id FROM recommendations WHERE id=?", (card["id"],)).fetchone() == before


@pytest.mark.parametrize("kind", ["shown", "accepted", "skipped"])
def test_nonplayback_measurement_is_rejected_before_action(store, kind):
    store.apply_control(OWNER, control())
    card = offer(store)
    measurement = {"version": "playback_elapsed_observed_v2", "epoch_id": "epoch",
                   "range_start_ms": 0, "range_end_ms": 10, "origin_context": control()}
    with pytest.raises(FeedbackConflict, match="invalid_observation_fields"):
        store.record(OWNER, card["id"], event(card, kind, "invalid-action", measurement=measurement), control=control())
    assert store.sync_status(OWNER)["pending"] == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT decision FROM recommendations WHERE id=?", (card["id"],)).fetchone() == (None,)


def test_anonymous_v2_events_and_settings_never_consume_shared_upload_capacity(store):
    anonymous = {"key": "anonymous-fixture", "user_id": None, "community": None}
    card = store.create(anonymous, "Fixture", "turn", TRACK, {})
    result = store.record(anonymous, card["id"], {"event_id": "anonymous-shown", "kind": "shown",
                         "context": {"version": "desktop-feedback-capture-v2"}})
    setting = {"revision": 1, "origin": "user", "occurred_at": 100, "previous": {},
               "settings": {"proactiveMusicEnabled": True}}
    custody = store.enqueue_settings(anonymous, "anonymous-setting", setting,
                                    context={"version": "desktop-feedback-capture-v2"})
    assert result["applied"]
    assert result["telemetry"]["status"] == custody["status"] == "discarded"
    assert result["telemetry"]["reason"] == custody["reason"] == "anonymous_not_uploadable"
    assert store.sync_status(anonymous)["capacity"]["pending_rows"] == 0
    assert store.prepare_batch(anonymous) is None


def test_generation_is_not_a_display_and_same_offer_reopening_counts_once(store):
    cards = [offer(store) for _ in range(3)]
    assert store.prepare_batch(OWNER) is None
    first = store.record(OWNER, cards[0]["id"], event(cards[0]), control=control())
    assert first["applied"] and first["telemetry"]["status"] == "durable"
    assert not store.record(OWNER, cards[0]["id"], event(cards[0], event_id="reopened"), control=control())["applied"]
    batch = store.prepare_batch(OWNER)
    assert len(batch["items"]) == 1
    assert batch["items"][0]["payload"]["kind"] == "shown"


@pytest.mark.parametrize("first,opposite", [("accepted", "skipped"), ("skipped", "accepted")])
@pytest.mark.parametrize("acked", [False, True])
def test_opposite_decision_conflicts_without_changing_original_custody_or_dispatch(store, first, opposite, acked):
    card = offer(store)
    original = event(card, first, "original-decision")
    assert store.record(OWNER, card["id"], original, control=control())["applied"]
    batch = store.prepare_batch(OWNER)
    if acked:
        assert store.acknowledge_batch(OWNER, answer(batch))
    rejected = event(card, opposite, "opposite-decision")
    with pytest.raises(FeedbackConflict, match="recommendation_decision_conflict") as failure:
        store.record(OWNER, card["id"], rejected, control=control())
    assert failure.value.telemetry == {"event_id": rejected["event_id"],
        "payload_hash": store.event_identity(rejected)[2], "status": "discarded", "reason": "decision_conflict"}
    remaining = store.prepare_batch(OWNER)
    assert remaining == (None if acked else batch)
    retried = store.record(OWNER, card["id"], original, control=control())
    assert not retried["applied"] and retried["state"] == first
    repeated = store.record(OWNER, card["id"], event(card, first, "same-choice-new-id"), control=control())
    assert not repeated["applied"] and repeated["telemetry"]["reason"] == "already_applied"
    if first == "accepted":
        assert store.claim_dispatch(OWNER, card["id"], "original-decision")
        assert not store.claim_dispatch(OWNER, card["id"], "original-decision")
    else:
        with pytest.raises(FeedbackConflict, match="feedback_dispatch_owner_changed"):
            store.claim_dispatch(OWNER, card["id"], "original-decision")


def test_full_telemetry_keeps_normal_decision_and_never_backfills_skipped_first_display(store):
    card = offer(store)
    store.MAX_PENDING_UPLOADS = 0
    shown = store.record(OWNER, card["id"], event(card), control=control())
    accepted = store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    assert shown["telemetry"]["reason"] == "capacity_reached"
    assert accepted["playback_grant"] and accepted["state"] == "accepted"
    assert accepted["telemetry"]["status"] == "discarded"
    store.MAX_PENDING_UPLOADS = 100
    assert not store.record(OWNER, card["id"], event(card, event_id="reopen"), control=control())["applied"]
    assert not store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())["applied"]
    assert store.prepare_batch(OWNER) is None
    assert store.claim_dispatch(OWNER, card["id"], "accept")
    assert not RecommendationStore(store.path, protocol_version=2).claim_dispatch(OWNER, card["id"], "accept")


def test_durable_held_survives_restart_and_response_loss_with_full_original_hash(store):
    card = offer(store)
    partial = {"version": "desktop-feedback-capture-v2", "owner_key": OWNER["key"],
               "capture": {"clock_session_id": "old-session", "tick_ms": 10}}
    original = event(card, "accepted", "accept", snapshot=partial)
    result = store.record(OWNER, card["id"], original)
    assert result["telemetry"]["status"] == "durable"
    reopened = RecommendationStore(store.path, protocol_version=2)
    retry = reopened.record(OWNER, card["id"], original, control=control())
    assert not retry["applied"] and retry["telemetry"] == result["telemetry"]
    assert reopened.sync_status(OWNER)["held"] == 1
    assert reopened.prepare_batch(OWNER) is None
    changed = copy.deepcopy(original)
    changed["occurred_at"] = 999
    with pytest.raises(FeedbackConflict, match="collision"):
        reopened.record(OWNER, card["id"], changed, control=control())


def test_actual_sql_failure_rolls_back_business_and_custody_together(store):
    card = offer(store)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_intent BEFORE INSERT ON feedback_v2_spool BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT decision FROM recommendations").fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM feedback_v2_spool").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_intent")
    assert store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())["applied"]


def test_clear_and_late_receipt_do_not_touch_new_generation(store):
    store.apply_control(OWNER, control())
    old = offer(store)
    store.record(OWNER, old["id"], event(old), control=control())
    previous = store.prepare_batch(OWNER)
    store.apply_control(OWNER, control(1, 2))
    # The same real normal action still succeeds, while its old captured stats
    # are explicitly discarded under the approved clear propagation rule.
    accepted = store.record(OWNER, old["id"], event(old, "accepted", "old-accept"), control=control(1, 2))
    assert accepted["playback_grant"] and accepted["telemetry"]["status"] == "discarded"
    new = offer(store, control(1, 2))
    store.record(OWNER, new["id"], event(new, event_id="new", snapshot=control(1, 2)), control=control(1, 2))
    current = store.prepare_batch(OWNER)
    assert not store.acknowledge_batch(OWNER, answer(previous))
    assert store.prepare_batch(OWNER) == current
    assert store.acknowledge_batch(OWNER, answer(current))
    assert store.sync_status(OWNER)["pending"] == 0
    minimal = store.sync_status(OWNER)["last_acknowledged_batch"]
    assert "results" not in minimal and "receipt" not in minimal
    assert minimal["terminal_count"] == 1 and minimal["batch_id"] == current["batch_id"]


def test_mixed_receipt_is_complete_defer_releases_transport_and_proof_changes_only_batch(store):
    first, second = offer(store), offer(store)
    first_event, second_event = event(first), event(second, event_id="second")
    store.record(OWNER, first["id"], first_event, control=control())
    store.record(OWNER, second["id"], second_event, control=control())
    frozen = store.prepare_batch(OWNER)
    malformed = answer(frozen)
    malformed["results"].pop()
    with pytest.raises(FeedbackConflict):
        store.acknowledge_batch(OWNER, malformed)
    assert store.prepare_batch(OWNER) == frozen
    assert store.acknowledge_batch(OWNER, answer(frozen, ["time_deferred", "stored"]))
    store.apply_control(OWNER, control())
    store.apply_control(OWNER, control())
    assert store.prepare_batch(OWNER) is None
    third = offer(store)
    store.record(OWNER, third["id"], event(third, event_id="third"), control=control())
    normal = store.prepare_batch(OWNER)
    assert [item["id"] for item in normal["items"]] == ["third"]
    store.acknowledge_batch(OWNER, answer(normal))
    local_hash = store.event_identity(first_event)[2]
    proof = {**control()["time_proof"], "anchor_token": "refined-proof"}
    assert store.update_time_proof(OWNER, "shown", local_hash, proof, control=control())
    retry = store.prepare_batch(OWNER)
    assert retry["batch_id"] != frozen["batch_id"]
    assert fact_hash_v2(retry["items"][0], retry["binding"]) == fact_hash_v2(frozen["items"][0], frozen["binding"])
    assert payload_hash(retry) != payload_hash(frozen)


def test_deferred_retry_is_bounded_fair_and_cannot_change_an_inflight_batch(store, monkeypatch):
    import time
    from types import SimpleNamespace
    from main_logic import recommendation_feedback as module
    store.apply_control(OWNER, control())
    for index in range(3):
        card = offer(store)
        store.record(OWNER, card["id"], event(card, event_id=f"held-{index}"), control=control())
    original = store.prepare_batch(OWNER)
    store.acknowledge_batch(OWNER, answer(original, ["dependency_deferred"] * 3))
    assert store.retry_deferred(OWNER) == 0
    later = time.time() + 61
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: later))
    seen = []
    for _ in range(3):
        assert store.retry_deferred(OWNER, limit=1, now=later) == 1
        frozen = store.prepare_batch(OWNER)
        seen.append(frozen["items"][0]["id"])
        assert store.retry_deferred(OWNER, limit=1, now=later) == 0
        assert store.prepare_batch(OWNER) == frozen
        store.acknowledge_batch(OWNER, answer(frozen, ["dependency_deferred"]))
    assert seen == [item["id"] for item in original["items"]]
    store.apply_control(OWNER, control(1, 2))
    assert store.retry_deferred(OWNER, now=later + 60) == 0
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["pending"] == 0


def test_account_and_offer_context_cannot_be_rebound(store):
    card = offer(store)
    with pytest.raises(FeedbackConflict, match="not_found"):
        store.record(OTHER, card["id"], event(card, "accepted", "accept"), control=control())
    changed = event(card)
    changed["offer_context"]["owner_key"] = OTHER["key"]
    with pytest.raises(FeedbackConflict, match="context_changed"):
        store.record(OWNER, card["id"], changed, control=control())


def test_first_upgrade_drops_v1_pending_but_preserves_current_operational_state(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = RecommendationStore(path)
    card = legacy.create(OWNER, "Fixture", "turn", TRACK, {"proactiveMusicEnabled": True})
    old_batch = legacy.prepare_batch(OWNER)
    current = RecommendationStore(path, protocol_version=2)
    assert current.prepare_batch(OWNER) is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT settings FROM recommendations WHERE id=?", (card["id"],)).fetchone()[0]
    assert not current.acknowledge_batch(OWNER, {"batch_id": old_batch["batch_id"]})
    assert current.storage_incarnation() == RecommendationStore(path, protocol_version=2).storage_incarnation()


def test_measurement_is_separate_from_business_playing_and_prefix_survives_restart(store):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    metric = {"version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": 0,
              "range_end_ms": 1000, "origin_context": control()}
    first = event(card, "measurement", "metric", playback_id="playback", measurement=metric)
    assert store.record(OWNER, card["id"], first, control=control())["state"] == "accepted"
    reopened = RecommendationStore(store.path, protocol_version=2)
    assert not reopened.record(OWNER, card["id"], first, control=control())["applied"]
    second = event(card, "measurement", "metric-next", playback_id="playback",
                   measurement={**metric, "range_start_ms": 1000, "range_end_ms": 2000})
    result = reopened.record(OWNER, card["id"], second, control=control())
    assert not result["applied"] and result["telemetry"]["status"] == "durable"
    out_of_order = reopened.record(OWNER, card["id"], event(card, "measurement", "gap", playback_id="playback",
        measurement={**metric, "range_start_ms": 3000, "range_end_ms": 4000}), control=control())
    assert out_of_order["telemetry"]["status"] == "durable"
    batch = reopened.prepare_batch(OWNER)
    measured = [item["payload"] for item in batch["items"] if item["payload"].get("measurement") is not None]
    assert {payload["kind"] for payload in measured} == {"measurement"}
    assert all(payload["measurement_version"] == "playback_elapsed_observed_v2" for payload in measured)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT meter_prefix FROM feedback_v2_offers").fetchone()[0] == 2000


@pytest.mark.parametrize("kind", ["playing", "paused", "ended", "stopped", "failed"])
def test_playback_tail_wire_version_matches_measurement_and_frozen_retry(store, kind):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    metric = {"version": "playback_elapsed_observed_v2", "epoch_id": "epoch",
              "range_start_ms": 40, "range_end_ms": 60, "origin_context": control()}
    tail = event(card, kind, "tail", playback_id="playback", measurement=metric)
    assert store.record(OWNER, card["id"], tail, control=control())["telemetry"]["status"] == "durable"
    batch = store.prepare_batch(OWNER)
    payloads = {item["id"]: item["payload"] for item in batch["items"]}
    assert payloads["tail"]["measurement_version"] == "playback_elapsed_observed_v2"
    assert payloads["tail"]["measurement"] == metric
    assert payloads["accept"]["measurement_version"] == "media-played-union-v1"
    reopened = RecommendationStore(store.path, protocol_version=2)
    assert reopened.prepare_batch(OWNER) == batch


@pytest.mark.parametrize("timestamp", [None, "omitted"])
def test_optional_event_timestamp_uses_first_local_receipt_without_changing_original(store, monkeypatch, timestamp):
    from types import SimpleNamespace
    from main_logic import recommendation_feedback as module
    clock = [1000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    card = offer(store)
    value = event(card)
    if timestamp == "omitted":
        value.pop("occurred_at")
    else:
        value["occurred_at"] = timestamp
    original = copy.deepcopy(value)
    first = store.record(OWNER, card["id"], value, control=control())
    batch = store.prepare_batch(OWNER)
    item = batch["items"][0]
    assert item["payload"]["occurred_at"] == 1000.0
    assert value == original
    clock[0] += 100
    reopened = RecommendationStore(store.path, protocol_version=2)
    assert reopened.prepare_batch(OWNER) == batch
    assert reopened.acknowledge_batch(OWNER, answer(batch))
    assert reopened.record(OWNER, card["id"], value, control=control())["telemetry"] == first["telemetry"]
    assert reopened.prepare_batch(OWNER) is None
    assert reopened.events(OWNER, 0, 100)["events"][0]["original_event"] == store.event_identity(original)[0]


@pytest.mark.parametrize("area,key,value", [
    ("time_proof", "anchor_token", None),
    ("time_proof", "anchor_token", ["not-a-token"]),
    ("time_proof", "send_tick_ms", None),
    ("time_proof", "receive_tick_ms", None),
    ("time_proof", "send_tick_ms", True),
    ("time_proof", "send_tick_ms", -1),
    ("time_proof", "send_tick_ms", 1001),
    ("time_proof", "receive_tick_ms", "1000"),
    ("time_proof", "receive_tick_ms", 2**53),
    ("capture", "tick_ms", 10**400),
    ("capture", "tick_ms", 2**53),
    ("capture", "clock_session_id", ""),
    ("capture", "anchor_id", ["not-an-id"]),
])
def test_incomplete_time_evidence_is_held_without_blocking_healthy_facts(store, area, key, value):
    store.apply_control(OWNER, control())
    card = offer(store)
    context = control()
    if value is None:
        context[area].pop(key)
    else:
        context[area][key] = value
    original = event(card, "accepted", "unqualified", snapshot=context)
    before = copy.deepcopy(original)
    result = store.record(OWNER, card["id"], original, control=control())
    assert result["playback_grant"] and result["telemetry"]["status"] == "durable"
    assert result["telemetry"]["reason"] == "time_unproven"
    assert original == before
    healthy = offer(store)
    store.record(OWNER, healthy["id"], event(healthy, event_id="healthy"), control=control())
    batch = store.prepare_batch(OWNER)
    assert [item["id"] for item in batch["items"]] == ["healthy"]
    assert store.acknowledge_batch(OWNER, answer(batch))
    assert store.sync_status(OWNER)["held"] == 1


def test_partial_time_proof_can_be_completed_without_rebinding_original(store):
    store.apply_control(OWNER, control())
    card = offer(store)
    context = control()
    context["time_proof"].pop("send_tick_ms")
    original = event(card, snapshot=context)
    saved, _, _ = store.event_identity(original)
    first = store.record(OWNER, card["id"], original, control=control())
    assert first["telemetry"]["reason"] == "time_unproven"
    assert store.prepare_batch(OWNER) is None
    assert store.update_time_proof(OWNER, original["event_id"], first["telemetry"]["payload_hash"], control()["time_proof"])
    batch = store.prepare_batch(OWNER)
    assert batch["items"][0]["capture"] == context["capture"]
    assert batch["items"][0]["time_proof"] == control()["time_proof"]
    assert store.event_identity(original)[0] == saved
    assert store.acknowledge_batch(OWNER, answer(batch))
    assert store.prepare_batch(OWNER) is None


@pytest.mark.parametrize("proof", [None, {}, {**control()["time_proof"], "extra": 1}])
def test_store_reproof_cannot_queue_incomplete_or_extra_time_fields(store, proof):
    card = offer(store)
    original = event(card)
    first = store.record(OWNER, card["id"], original, control=None)
    if proof is None:
        incomplete = control()
        incomplete["time_proof"] = None
        original = event(card, "accepted", "null-proof", snapshot=incomplete)
        assert store.record(OWNER, card["id"], original, control=control())["telemetry"]["reason"] == "time_unproven"
    else:
        assert store.update_time_proof(OWNER, original["event_id"], first["telemetry"]["payload_hash"], proof, control=control())
    assert store.prepare_batch(OWNER) is None


def test_measurement_range_accepts_actual_peer_upper_bound(store):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    original = event(card, "measurement", "one-day", playback_id="playback", measurement={
        "version": "playback_elapsed_observed_v2", "epoch_id": "x" * 100, "range_start_ms": 86400000 - 1,
        "range_end_ms": 86400000, "origin_context": control()})
    assert store.record(OWNER, card["id"], original, control=control())["telemetry"]["status"] == "durable"
    assert store.prepare_batch(OWNER)["items"][-1]["payload"]["measurement"] == original["measurement"]


@pytest.mark.parametrize("area,key,value", [
    (None, "version", "unknown"), (None, "owner_key", []),
    (None, "account_incarnation", ""), (None, "clear_generation", True),
    (None, "registration_id", "r" * 101), (None, "registration_version", True),
    (None, "binding_revision", -1),
    ("capture", "clock_session_id", "s" * 129),
    ("capture", "tick_ms", 10**400), ("capture", "anchor_id", ["bad"]),
    ("time_proof", "anchor_token", ["bad"]),
    ("time_proof", "send_tick_ms", "wrong"), ("time_proof", "receive_tick_ms", True),
])
@pytest.mark.parametrize("container", ["offer_context", "measurement"])
def test_invalid_origin_scalar_is_held_without_poisoning_healthy_batch(store, container, area, key, value):
    origin = control()
    (origin if area is None else origin[area])[key] = value
    card = offer(store, snapshot=origin if container == "offer_context" else None)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    original = event(card, "measurement", "invalid-origin", playback_id="playback", measurement={
        "version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": 0,
        "range_end_ms": 1, "origin_context": origin if container == "measurement" else control()})
    before = copy.deepcopy(original)
    result = store.record(OWNER, card["id"], original, control=control())
    assert result["telemetry"]["status"] == "durable" and result["telemetry"]["reason"] == "time_unproven"
    assert original == before
    healthy = offer(store)
    store.record(OWNER, healthy["id"], event(healthy, event_id="healthy"), control=control())
    batch = store.prepare_batch(OWNER)
    assert "healthy" in [item["id"] for item in batch["items"]]
    assert "invalid-origin" not in [item["id"] for item in batch["items"]]
    assert store.acknowledge_batch(OWNER, answer(batch))
    assert store.sync_status(OWNER)["held"] >= 1


@pytest.mark.parametrize("origin", [None, {}, {"capture": None, "time_proof": None}, {
    "version": None, "owner_key": None, "account_incarnation": None, "clear_generation": None,
    "registration_id": None, "registration_version": None, "binding_revision": None,
    "capture": {"clock_session_id": None, "tick_ms": None, "anchor_id": None},
    "time_proof": {"anchor_token": None, "send_tick_ms": None, "receive_tick_ms": None}},
    {"capture": {"tick_ms": 1}}, {"time_proof": {"send_tick_ms": 1000, "receive_tick_ms": 900}},
])
def test_schema_compatible_partial_origin_is_preserved_for_server_per_item_defer(store, origin):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    original = event(card, "measurement", "partial-origin", playback_id="playback", measurement={
        "version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": 0,
        "range_end_ms": 1, "origin_context": origin})
    assert store.record(OWNER, card["id"], original, control=control())["telemetry"]["reason"] == ""
    payload = store.prepare_batch(OWNER)["items"][-1]["payload"]
    expected = copy.deepcopy(origin)
    if isinstance(expected, dict) and not expected.get("time_proof"):
        expected.pop("time_proof", None)
    assert payload["measurement"]["origin_context"] == expected


@pytest.mark.parametrize("context", [["bad"], "bad", 1, {"capture": ["bad"]}, {"time_proof": ["bad"]}])
def test_settings_invalid_context_is_typed_conflict_without_poisoning_journal_handoff(store, context):
    payload = {"revision": 1, "origin": "user", "occurred_at": 100, "previous": {}, "settings": {}}
    with pytest.raises(FeedbackConflict, match="invalid_feedback_context"):
        store.enqueue_settings(OWNER, "bad-setting", payload, context=context)
    assert store.sync_status(OWNER)["pending"] == 0
    assert store.enqueue_settings(OWNER, "partial-setting", payload, context={"version": "desktop-feedback-capture-v2"})["status"] == "durable"
    assert store.sync_status(OWNER)["held"] == 1


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_full_spool_stops_new_raw_history_while_actions_and_exact_existing_facts_continue(store, budget):
    store.apply_control(OWNER, control())
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "shown", "shown"), control=control())
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    frozen = store.prepare_batch(OWNER)
    before = store.events(OWNER, 0, 100)["events"]
    if budget == "rows":
        store.MAX_PENDING_UPLOADS = 1
    else:
        store.MAX_V2_PENDING_BYTES = store.sync_status(OWNER)["capacity"]["pending_bytes"]
    discarded = []
    for index in range(8):
        value = event(card, "measurement", f"discarded-{index}", playback_id="playback", measurement={
            "version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": index,
            "range_end_ms": index + 1, "origin_context": control()})
        discarded.append(value)
        assert store.record(OWNER, card["id"], value, control=control())["telemetry"]["reason"] == "capacity_reached"
    other = offer(store)
    action = event(other, "accepted", "other-accept")
    result = store.record(OWNER, other["id"], action, control=control())
    assert result["playback_grant"] and result["telemetry"]["reason"] == "capacity_reached"
    assert store.claim_dispatch(OWNER, other["id"], action["event_id"])
    assert not store.claim_dispatch(OWNER, other["id"], action["event_id"])
    assert store.events(OWNER, 0, 100)["events"] == before
    assert store.prepare_batch(OWNER) == frozen
    assert store.sync_status(OWNER)["statistics_incomplete"]
    assert store.acknowledge_batch(OWNER, answer(frozen))
    assert store.prepare_batch(OWNER) is None  # No automatic reconstruction of discarded facts.
    # Reply loss leaves the renderer owning this exact real intent; a retry may
    # acquire custody for the first time, with its original capture unchanged.
    retried = store.record(OWNER, card["id"], discarded[-1], control=control())
    assert retried["telemetry"]["status"] == "durable"
    retry_batch = store.prepare_batch(OWNER)
    assert [item["id"] for item in retry_batch["items"]] == [discarded[-1]["event_id"]]
    assert retry_batch["items"][0]["capture"] == discarded[-1]["context"]["capture"]


@pytest.mark.parametrize("field,value", [("event_id", "bad\x00id"), ("playback_id", "bad\x00id")])
def test_nul_wire_identity_is_rejected_before_business_or_batch_creation(store, field, value):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    original = event(card, "playing", "playing", playback_id="playback")
    original[field] = value
    with pytest.raises(FeedbackConflict):
        store.record(OWNER, card["id"], original, control=control())
    assert [item["id"] for item in store.prepare_batch(OWNER)["items"]] == ["accept"]


def test_lost_renderer_reply_after_cloud_ack_does_not_recreate_measurement(store):
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    fact = event(card, "measurement", "meter", playback_id="playback", measurement={
        "version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": 0,
        "range_end_ms": 20, "origin_context": control()})
    first = store.record(OWNER, card["id"], fact, control=control())
    batch = store.prepare_batch(OWNER)
    assert store.acknowledge_batch(OWNER, answer(batch))
    reopened = RecommendationStore(store.path, protocol_version=2)
    retry = reopened.record(OWNER, card["id"], fact, control=control())
    assert retry["telemetry"] == first["telemetry"] and not retry["applied"]
    assert reopened.prepare_batch(OWNER) is None
    changed = copy.deepcopy(fact)
    changed["measurement"]["range_end_ms"] = 21
    with pytest.raises(FeedbackConflict, match="collision"):
        reopened.record(OWNER, card["id"], changed, control=control())


def test_v2_local_history_survives_upload_and_keeps_cursor_and_owner_isolation(store):
    card = offer(store)
    for kind in ("shown", "accepted", "playing", "ended"):
        value = event(card, kind, kind, **({"playback_id": "playback"} if kind in {"playing", "ended"} else {}))
        store.record(OWNER, card["id"], value, control=control())
    first = store.events(OWNER, 0, 2)
    assert [row["kind"] for row in first["events"]] == ["shown", "accepted"]
    batch = store.prepare_batch(OWNER)
    store.acknowledge_batch(OWNER, answer(batch))
    reopened = RecommendationStore(store.path, protocol_version=2)
    after = reopened.events(OWNER, first["next_cursor"], 100)
    assert [row["kind"] for row in after["events"]] == ["playing", "ended"]
    assert reopened.events(OTHER, 0, 100)["events"] == []
    assert len(reopened.events(OWNER, 0, 100)["events"]) == 4


def test_clearing_v2_history_clears_local_export_without_reviving_late_old_fact(store):
    store.apply_control(OWNER, control())
    card = offer(store)
    original = event(card, "accepted", "accept")
    store.record(OWNER, card["id"], original, control=control())
    replaced = {**control(0, 2), "registration_version": 2}
    store.apply_control(OWNER, replaced)
    assert len(store.events(OWNER, 0, 100)["events"]) == 1
    store.apply_control(OWNER, control(1, 3))
    assert store.events(OWNER, 0, 100)["events"] == []
    old_metric = event(card, "measurement", "late", playback_id="playback", measurement={
        "version": "playback_elapsed_observed_v2", "epoch_id": "epoch", "range_start_ms": 0,
        "range_end_ms": 10, "origin_context": control()})
    assert store.record(OWNER, card["id"], old_metric, control=control(1, 3))["telemetry"]["reason"] == "binding_changed"
    assert store.events(OWNER, 0, 100)["events"] == []
    assert store.prepare_batch(OWNER) is None


def test_existing_v2_spool_migrates_to_history_before_transport_is_retired(store):
    card = offer(store)
    original = event(card)
    first = store.record(OWNER, card["id"], original, control=control())
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM recommendation_events")
        for column in ("v2_event", "v2_hash", "v2_custody"):
            db.execute(f"ALTER TABLE recommendation_events DROP COLUMN {column}")
    reopened = RecommendationStore(store.path, protocol_version=2)
    assert [row["event_id"] for row in reopened.events(OWNER, 0, 100)["events"]] == ["shown"]
    batch = reopened.prepare_batch(OWNER)
    reopened.acknowledge_batch(OWNER, answer(batch))
    assert reopened.record(OWNER, card["id"], original, control=control())["telemetry"] == first["telemetry"]
    assert reopened.prepare_batch(OWNER) is None


def test_expired_held_capture_releases_global_quota_without_touching_frozen_batch(store, monkeypatch):
    from types import SimpleNamespace
    from main_logic import recommendation_feedback as module
    clock = [1000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    card = offer(store)
    partial = {"version": "desktop-feedback-capture-v2", "owner_key": OWNER["key"]}
    fact = event(card, snapshot=partial)
    assert store.record(OWNER, card["id"], fact)["telemetry"]["status"] == "durable"
    clock[0] += 7 * 86400 + 1
    store.MAX_PENDING_UPLOADS = 1
    fresh = offer(store)
    assert store.record(OWNER, fresh["id"], event(fresh, event_id="fresh"), control=control())["telemetry"]["status"] == "durable"
    batch = store.prepare_batch(OWNER)
    assert [item["id"] for item in batch["items"]] == ["fresh"]
    assert store.sync_status(OWNER)["held"] == 0
    assert store.sync_status(OWNER)["statistics_incomplete"]
    clock[0] += 7 * 86400 + 1
    assert store.prepare_batch(OWNER) == batch
    assert store.acknowledge_batch(OWNER, answer(batch))


def test_reading_status_does_not_retire_pending_captures(store, monkeypatch):
    from types import SimpleNamespace
    from main_logic import recommendation_feedback as module
    clock = [1000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    card = offer(store)
    store.record(OWNER, card["id"], event(card, snapshot={"owner_key": OWNER["key"]}))
    clock[0] += 7 * 86400 + 1
    assert store.sync_status(OWNER)["pending"] == 1
    assert store.prepare_batch(OWNER) is None
    assert store.sync_status(OWNER)["pending"] == 0


def test_upgrade_resume_cannot_release_a_different_permanent_error(store):
    card = offer(store)
    store.record(OWNER, card["id"], event(card), control=control())
    batch = store.prepare_batch(OWNER)
    assert store.mark_batch_error(OWNER, batch["batch_id"], "cloud_conflict", permanent=True)
    with pytest.raises(FeedbackConflict, match="feedback_resume_conflict"):
        store.resume_blocked_batch(OWNER, batch["batch_id"], payload_hash(batch), expected_error="upgrade_required")
    assert store.sync_status(OWNER)["blocked"]
    assert store.sync_status(OWNER)["last_error"] == "cloud_conflict"


@pytest.mark.parametrize("phase", ["before", "after"])
def test_real_store_process_exit_at_commit_has_no_handoff_gap(store, phase):
    card = offer(store)
    value = event(card, "accepted", "crash-action")
    script = r'''
import json, os, sqlite3, sys
from pathlib import Path
from main_logic.recommendation_feedback import RecommendationStore
path, phase, owner, card, event, control = sys.argv[1:]
base = sqlite3.connect
class CrashConnection(sqlite3.Connection):
    def commit(self):
        if phase == "before": os._exit(73)
        super().commit()
        os._exit(73)
sqlite3.connect = lambda *args, **kwargs: base(*args, **kwargs, factory=CrashConnection)
RecommendationStore(Path(path), protocol_version=2).record(json.loads(owner), card, json.loads(event), control=json.loads(control))
'''
    result = subprocess.run(["uv", "run", "--no-project", "--python", sys.executable, "python", "-c", script,
                             str(store.path), phase, json.dumps(OWNER), card["id"], json.dumps(value), json.dumps(control())],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        decision = db.execute("SELECT decision FROM recommendations WHERE id=?", (card["id"],)).fetchone()[0]
        count = db.execute("SELECT count(*) FROM feedback_v2_spool").fetchone()[0]
    assert (decision, count) == ((None, 0) if phase == "before" else ("accepted", 1))
    retried = RecommendationStore(store.path, protocol_version=2).record(OWNER, card["id"], value, control=control())
    assert retried["applied"] == (phase == "before")
    assert retried["telemetry"]["status"] == "durable"


def test_byte_budget_and_immutable_sql_do_not_erase_promised_held_fact(store):
    first, second = offer(store), offer(store)
    store.record(OWNER, first["id"], event(first), control=None)
    status = store.sync_status(OWNER)
    store.MAX_V2_PENDING_BYTES = status["capacity"]["pending_bytes"]
    result = store.record(OWNER, second["id"], event(second, "accepted", "second"), control=control())
    assert result["playback_grant"] and result["telemetry"]["reason"] == "capacity_reached"
    assert store.sync_status(OWNER)["held"] == 1
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE feedback_v2_spool SET event='{}'")
        assert db.execute("SELECT event_id FROM feedback_v2_spool").fetchone()[0] == "shown"


def test_v2_settings_spool_preserves_birth_and_can_freeze_without_legacy_outbox(store):
    snapshot = control()
    store.apply_control(OWNER, snapshot)
    payload = {"revision": 1, "origin": "user", "occurred_at": 100,
               "previous": {"proactiveMusicEnabled": False}, "settings": {"proactiveMusicEnabled": True}}
    result = store.enqueue_settings(OWNER, "settings-change", payload, context=snapshot)
    assert result["status"] == "durable"
    assert store.enqueue_settings(OWNER, "settings-change", payload, context=snapshot) == result
    batch = store.prepare_batch(OWNER)
    assert batch["items"][0]["kind"] == "settings"
    assert batch["items"][0]["capture"] == snapshot["capture"]
    assert batch["items"][0]["payload"] == payload
    assert store.acknowledge_batch(OWNER, answer(batch))
    legacy = store.enqueue_settings(OWNER, "legacy-change", payload)
    assert legacy["status"] == "discarded" and legacy["reason"] == "legacy_capture_missing"


def test_receipt_detail_has_bounded_retention_and_stale_provider_cannot_reopen_old_binding(store):
    old = control()
    store.apply_control(OWNER, old)
    card = offer(store)
    store.record(OWNER, card["id"], event(card), control=old)
    store.acknowledge_batch(OWNER, answer(store.prepare_batch(OWNER)))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE feedback_v2_status SET acknowledged_at=0")
    assert store.sync_status(OWNER)["last_acknowledged_batch"] is None
    store.apply_control(OWNER, control(1, 2))
    result = store.record(OWNER, card["id"], event(card, "accepted", "late"), control=old)
    assert result["playback_grant"]
    assert result["telemetry"]["reason"] == "binding_changed"
    assert store.prepare_batch(OWNER) is None


def test_offer_gc_keeps_active_and_promised_held_rows(store):
    unused, active, promised = offer(store), offer(store), offer(store)
    store.record(OWNER, active["id"], event(active, "accepted", "active"), control=control())
    store.record(OWNER, promised["id"], event(promised, event_id="promised"), control=None)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE recommendations SET expires_at=0")
        db.execute("UPDATE feedback_v2_offers SET expires_at=0")
    store.prepare_batch(OWNER)
    with sqlite3.connect(store.path) as db:
        ids = {row[0] for row in db.execute("SELECT id FROM recommendations")}
    assert unused["id"] not in ids
    assert {active["id"], promised["id"]} <= ids


def test_explicit_clear_invalidates_already_promised_partial_capture_too(store):
    store.apply_control(OWNER, control())
    card = offer(store)
    partial = {"version": "desktop-feedback-capture-v2", "owner_key": OWNER["key"],
               "capture": {"clock_session_id": "fixture", "tick_ms": 1}}
    store.record(OWNER, card["id"], event(card, snapshot=partial), control=control())
    assert store.sync_status(OWNER)["held"] == 1
    store.apply_control(OWNER, control(1, 2))
    assert store.sync_status(OWNER)["pending"] == 0


def test_renderer_canonical_header_matches_actual_js_exponent_unicode_vector():
    value = {"event_id": "handoff-vector", "kind": "paused", "position_seconds": 1e-7, "reason": "猫の歌😀"}
    header = "eyJldmVudF9pZCI6ImhhbmRvZmYtdmVjdG9yIiwia2luZCI6InBhdXNlZCIsInBvc2l0aW9uX3NlY29uZHMiOjFlLTcsInJlYXNvbiI6IueMq+OBruatjPCfmIAifQ=="
    assert RecommendationStore.local_handoff_hash(value, header) == "1fbc9e3f175305195d449388a70386b7e642edb8c3df2d2615408d82c0f4279a"
    integer = base64.b64encode(b'{"x":1}').decode()
    assert RecommendationStore.local_handoff_hash({"x": 1.0}, integer) == hashlib.sha256(b'{"x":1}').hexdigest()


@pytest.mark.parametrize("text,original", [
    ('{"x":0}', {"x": False}), ('{"x":1}', {"x": True}), ('{"x":1,"x":1}', {"x": 1}),
    ('{"x":NaN}', {"x": 1}), ('{"x":1e309}', {"x": 1}), ('{"x":2}', {"x": 1}),
])
def test_forged_ambiguous_or_nonfinite_canonical_is_rejected(text, original):
    with pytest.raises(FeedbackConflict, match="canonical"):
        RecommendationStore.local_handoff_hash(original, base64.b64encode(text.encode()).decode())


def test_terminal_rehear_new_epoch_late_old_range_and_clear_are_telemetry_only(store):
    store.apply_control(OWNER, control())
    card = offer(store)
    store.record(OWNER, card["id"], event(card, "accepted", "accept"), control=control())
    assert store.claim_dispatch(OWNER, card["id"], "accept")
    def metric(identity, epoch, start, end, snapshot=None):
        snapshot = snapshot or control()
        return event(card, "measurement", identity, snapshot=snapshot, playback_id="playback",
                     measurement={"version": "playback_elapsed_observed_v2", "epoch_id": epoch,
                                  "range_start_ms": start, "range_end_ms": end, "origin_context": snapshot})
    old = metric("old-range", "old-epoch", 0, 1000)
    store.record(OWNER, card["id"], old, control=control())
    store.record(OWNER, card["id"], event(card, "ended", "ended", playback_id="playback"), control=control())
    short = store.record(OWNER, card["id"], metric("short-rehear", "seek-epoch", 0, 250), control=control())
    assert short["state"] == "ended" and not short["applied"]
    assert short["telemetry"]["status"] == "durable"
    assert not short["playback_grant"] and not store.claim_dispatch(OWNER, card["id"], "accept")
    # ACK retires transport, not the original local handoff. A lost renderer
    # reply is confirmed without rebuilding an already accepted cloud fact.
    store.acknowledge_batch(OWNER, answer(store.prepare_batch(OWNER)))
    replay = store.record(OWNER, card["id"], old, control=control())
    tail = store.record(OWNER, card["id"], metric("seek-tail", "seek-epoch", 250, 500), control=control())
    assert replay["telemetry"]["status"] == tail["telemetry"]["status"] == "durable"
    assert not replay["applied"] and not tail["applied"]
    pending = store.prepare_batch(OWNER)
    assert {item["id"]: item["payload"]["measurement"]["epoch_id"] for item in pending["items"]} == {
        "seek-tail": "seek-epoch"}
    store.apply_control(OWNER, control(1, 2))
    cleared = store.record(OWNER, card["id"], old, control=control(1, 2))
    assert cleared["telemetry"]["status"] == "discarded"
    current = store.record(OWNER, card["id"], metric("after-clear", "clear-epoch", 0, 300, control(1, 2)), control=control(1, 2))
    assert current["state"] == "ended" and not current["applied"]
    assert current["telemetry"]["status"] == "durable"
    assert [item["id"] for item in store.prepare_batch(OWNER)["items"]] == ["after-clear"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM feedback_v2_offers").fetchone()[0] == 1
