"""Local rejection behavior through real SQLite, without external services."""
from __future__ import annotations

import copy
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from threading import Event
from unittest.mock import AsyncMock

import pytest

import main_logic.recommendation_feedback as feedback_module
from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore
from services.proactive_feedback.rules import (
    COOLDOWN_SECONDS, DECLINE_HALF_LIFE_SECONDS, MIN_PREFERENCE_FACTOR,
    POLICY_RETENTION_SECONDS, apply_policy_decision, canonical_category,
    cooldown_active, decayed_decline_count, preference_factor,
)


OWNER = {"key": "fixture-owner", "user_id": "fixture-user", "community": "https://fixture.example"}
OTHER = {**OWNER, "key": "fixture-other"}
UID = "a" * 32
OTHER_UID = "b" * 32
TRACK = {"name": "Fixture", "artist": "Fixture source", "url": "https://media.example/item"}


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=1_000_000.0)
    monkeypatch.setattr(feedback_module, "time", SimpleNamespace(time=lambda: value.now))
    return value


@pytest.fixture(params=[1, 2])
def store(request, tmp_path, clock):
    return RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=request.param)


def _control(*, incarnation="fixture-incarnation", generation=0, revision=1):
    return {"version": "desktop-feedback-capture-v2", "owner_key": OWNER["key"],
            "account_incarnation": incarnation, "clear_generation": generation,
            "registration_id": "fixture-registration", "registration_version": 1,
            "binding_revision": revision,
            "capture": {"clock_session_id": "fixture-clock", "tick_ms": 10, "anchor_id": "fixture-anchor"},
            "time_proof": {"anchor_token": "fixture-token", "send_tick_ms": 0, "receive_tick_ms": 10}}


def _offer(store, *, uid=UID, owner=OWNER, category="news", name="Fixture", control=None):
    return store.create(owner, name, "fixture-turn", TRACK, {}, category=category,
                        character_uid=uid, control=control)


def _event(card, kind="skipped", identity="fixture-no", **extra):
    value = {"event_id": identity, "kind": kind, **extra}
    if "offer_context" in card:
        value["offer_context"] = copy.deepcopy(card["offer_context"])
    return value


def _rows(store, query, args=()):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(query, args).fetchall()


@pytest.mark.parametrize("value,expected", [
    ("home", "web"), ("window", "window"), ("music", "music"),
    ("reminiscence", "reminiscence"), ("both", None), ("unexpected", None),
    (None, None), ([], None),
])
def test_category_alias_has_no_guessed_fallback(value, expected):
    assert canonical_category(value) == expected


def test_first_rejection_is_hard_cooldown_only_and_decays_without_mutating():
    first = apply_policy_decision(None, "skipped", now=100.0, offer_revision=0)
    assert first == {"revision": 1, "cooldown_until": 1300.0, "decline_count": 1.0, "updated_at": 100.0}
    assert cooldown_active(first, 1299.0)
    assert not cooldown_active(first, 1300.0)
    assert preference_factor(first, 100.0) == 1.0
    second = apply_policy_decision(first, "skipped", now=100.0, offer_revision=0)
    original = dict(second)
    assert preference_factor(second, 100.0) == pytest.approx(0.8)
    assert preference_factor(second, 100.0 + DECLINE_HALF_LIFE_SECONDS) == 1.0
    assert decayed_decline_count(second, 99.0) == 2.0
    assert second == original
    assert not cooldown_active(None, 100.0)
    assert preference_factor(None, 100.0) == 1.0


def test_repeated_declines_are_bounded_and_manual_recovery_keeps_hard_cooldown():
    state = None
    for _ in range(100):
        state = apply_policy_decision(state, "skipped", now=100.0, offer_revision=0)
    assert state["decline_count"] == 32.0
    assert preference_factor(state, 100.0) == MIN_PREFERENCE_FACTOR
    recovered = apply_policy_decision(state, "accepted", now=100.0, offer_revision=state["revision"])
    assert recovered["decline_count"] == 31.0
    assert recovered["cooldown_until"] == state["cooldown_until"]
    assert apply_policy_decision(state, "accepted", now=100.0, offer_revision=0) is None
    assert apply_policy_decision(state, "accepted", now=100.0,
                                 offer_revision=state["revision"], reason="countdown_elapsed") is None
    assert apply_policy_decision(None, "accepted", now=100.0, offer_revision=0) is None


def test_decision_persists_scope_and_exact_twenty_minutes_across_restart(store, clock):
    card = _offer(store)
    result = store.record(OWNER, card["id"], _event(card, occurred_at=0.0))
    state = result["policy"]["news"]
    assert result["policy_character_uid"] == UID
    assert state["revision"] == state["decline_count"] == 1
    assert state["updated_at"] == clock.now
    assert state["cooldown_until"] == clock.now + COOLDOWN_SECONDS
    reopened = RecommendationStore(store.path, protocol_version=store.protocol_version)
    assert reopened.get_policy(OWNER, UID) == result["policy"]
    assert reopened.get_policy(OTHER, UID) == {}
    assert reopened.get_policy(OWNER, OTHER_UID) == {}
    clock.now += 100
    retry = reopened.record(OWNER, card["id"], _event(card, occurred_at=0.0))
    assert not retry["applied"] and retry["policy"] == result["policy"]
    assert reopened.get_policy(OWNER, UID) == result["policy"]


def test_old_offer_acceptance_cannot_reverse_a_newer_decline(store, clock):
    old = _offer(store)
    new = _offer(store)
    rejected = store.record(OWNER, new["id"], _event(new, identity="new-no"))
    clock.now += 1
    result = store.record(OWNER, old["id"], _event(old, "accepted", "old-yes"))
    assert result["applied"] and result["state"] == "accepted"
    assert result["policy"] == rejected["policy"]


def test_explicit_current_acceptance_recovers_but_automatic_acceptance_does_not(store, clock):
    for index in range(2):
        card = _offer(store)
        store.record(OWNER, card["id"], _event(card, identity=f"no-{index}"))
    before = store.get_policy(OWNER, UID)["news"]
    automatic = _offer(store)
    auto = store.record(OWNER, automatic["id"], _event(automatic, "accepted", "auto", reason="countdown_elapsed"))
    assert auto["policy"]["news"] == before
    explicit = _offer(store)
    result = store.record(OWNER, explicit["id"], _event(explicit, "accepted", "explicit"))
    after = result["policy"]["news"]
    assert after["decline_count"] == before["decline_count"] - 1
    assert after["revision"] == before["revision"] + 1
    assert after["cooldown_until"] == before["cooldown_until"]


def test_legacy_cards_unknown_categories_and_shown_are_not_negative_preferences(store):
    legacy = _offer(store, uid=None)
    assert "policy" not in store.record(OWNER, legacy["id"], _event(legacy, identity="legacy"))
    unknown = _offer(store, category="fixture-unrecognized")
    assert "policy" not in store.record(OWNER, unknown["id"], _event(unknown, identity="unknown"))
    shown = _offer(store)
    assert store.record(OWNER, shown["id"], _event(shown, "shown", "shown"))["policy"] == {}
    assert store.get_policy(OWNER, UID) == {}


def test_role_rename_preserves_uid_and_same_name_recreation_does_not_inherit(store):
    card = _offer(store, name="Original")
    store.record(OWNER, card["id"], _event(card))
    renamed = _offer(store, name="Renamed")
    original = store.get_policy(OWNER, UID)
    assert _rows(store, "SELECT policy_revision FROM recommendation_policy_offers WHERE recommendation_id=?",
                 (renamed["id"],)) == [(1,)]
    recreated = _offer(store, uid=OTHER_UID, name="Original")
    assert store.record(OWNER, recreated["id"], _event(recreated, "shown", "new-role"))["policy"] == {}
    assert store.get_policy(OWNER, UID) == original


def test_failure_rolls_back_decision_evidence_and_policy_together(store):
    card = _offer(store)
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("CREATE TRIGGER reject_policy BEFORE INSERT ON recommendation_policy BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        store.record(OWNER, card["id"], _event(card))
    assert store.get_policy(OWNER, UID) == {}
    assert _rows(store, "SELECT decision FROM recommendations WHERE id=?", (card["id"],)) == [(None,)]
    assert store.events(OWNER, 0, 100)["events"] == []
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TRIGGER reject_policy")
        db.commit()
    assert store.record(OWNER, card["id"], _event(card))["policy"]["news"]["decline_count"] == 1


def test_concurrent_windows_and_retries_count_one_decision(store):
    card = _offer(store)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda index: store.record(OWNER, card["id"],
            _event(card, identity=f"no-{index}")), range(2)))
    assert sum(result["applied"] for result in results) == 1
    assert store.get_policy(OWNER, UID)["news"]["decline_count"] == 1
    assert len(store.events(OWNER, 0, 100)["events"]) == 1


def test_expired_first_attempt_never_becomes_a_new_rejection(store, clock):
    card = _offer(store)
    clock.now += store.TTL_SECONDS + 1
    with pytest.raises(FeedbackConflict, match="recommendation_expired"):
        store.record(OWNER, card["id"], _event(card))
    assert store.get_policy(OWNER, UID) == {}


@pytest.mark.parametrize("category", ["news", "music", "meme", "home"])
def test_rejection_only_changes_its_canonical_category(store, category):
    card = _offer(store, category=category)
    result = store.record(OWNER, card["id"], _event(card))
    assert set(result["policy"]) == {canonical_category(category)}


def test_invalid_uid_rolls_back_card_creation(store):
    store.get_policy(OWNER, UID)
    with pytest.raises(FeedbackConflict, match="invalid_character_uid"):
        _offer(store, uid="fixture-not-a-stable-id")
    assert _rows(store, "SELECT count(*) FROM recommendations") == [(0,)]


def test_v2_capacity_and_offer_gc_do_not_cancel_local_cooldown(tmp_path, clock):
    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    control = _control()
    card = _offer(store, control=control)
    store.MAX_PENDING_UPLOADS = 0
    result = store.record(OWNER, card["id"], {**_event(card), "context": control}, control=control)
    assert result["telemetry"]["reason"] == "capacity_reached"
    assert store.events(OWNER, 0, 100)["events"] == []
    assert result["policy"]["news"]["decline_count"] == 1
    # Force only the unrelated card's retention deadline to expire while its
    # real policy cooldown remains active.
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE recommendations SET expires_at=0")
        db.execute("UPDATE feedback_v2_offers SET expires_at=0")
        db.commit()
    _offer(store)
    assert _rows(store, "SELECT id FROM recommendations WHERE id=?", (card["id"],)) == []
    assert store.get_policy(OWNER, UID) == result["policy"]


def test_v2_statistics_clear_preserves_policy_but_new_verified_incarnation_retires_it(tmp_path, clock):
    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    card = _offer(store)
    previous = store.record(OWNER, card["id"], _event(card))["policy"]
    store.apply_control(OWNER, _control())
    assert store.get_policy(OWNER, UID) == previous  # First enrollment is not a reset.
    store.apply_control(OWNER, _control(generation=1, revision=2))
    assert store.get_policy(OWNER, UID) == previous
    old_pending = _offer(store)
    store.apply_control(OWNER, _control(incarnation="replacement-account", revision=3))
    assert store.get_policy(OWNER, UID) == {}
    result = store.record(OWNER, old_pending["id"], _event(old_pending, identity="late-old-no"))
    assert result["applied"] and "policy" not in result
    assert store.get_policy(OWNER, UID) == {}


def test_anonymous_policy_never_needs_upload_or_an_account_incarnation(tmp_path, clock):
    owner = {"key": "fixture-anonymous", "user_id": None, "community": None}
    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    card = _offer(store, owner=owner)
    result = store.record(owner, card["id"], _event(card))
    assert result["telemetry"]["reason"] == "anonymous_not_uploadable"
    assert store.get_policy(owner, UID) == result["policy"]
    assert store.get_policy(OWNER, UID) == {}
    assert store.prepare_batch(owner) is None


def test_policy_read_hides_changed_verified_identity_without_deleting_it(tmp_path, clock):
    store = RecommendationStore(tmp_path / "feedback.sqlite3", protocol_version=2)
    old = _control()
    card = _offer(store, control=old)
    previous = store.record(OWNER, card["id"], _event(card))["policy"]
    replacement = _control(incarnation="replacement-account", revision=2)
    assert store.get_policy(OWNER, UID, control=replacement) == {}
    assert store.get_policy(OWNER, UID, control=old) == previous
    assert _rows(store, "SELECT account_incarnation FROM recommendation_policy_owners") == [(old["account_incarnation"],)]


def test_policy_gc_has_its_own_bounded_retention(store, clock):
    card = _offer(store)
    store.record(OWNER, card["id"], _event(card))
    clock.now += POLICY_RETENTION_SECONDS - 1
    _offer(store)
    assert store.get_policy(OWNER, UID)
    clock.now += 2
    _offer(store)
    assert store.get_policy(OWNER, UID) == {}
    assert _rows(store, "SELECT decision FROM recommendations WHERE id=?", (card["id"],)) == [("skipped",)]


def test_new_rejection_after_policy_gc_outranks_a_live_registry_snapshot(store, clock):
    from services.proactive_feedback.policy import PolicyRegistry

    registry = PolicyRegistry(wall_clock=lambda: clock.now, clock=lambda: clock.now)
    for index in range(3):
        card = _offer(store)
        result = store.record(OWNER, card["id"], _event(card, identity=f"old-{index}"))
    old_revision = result["policy"]["news"]["revision"]
    view = registry.publish(store.path, OWNER, UID, result["policy"])
    clock.now += POLICY_RETENTION_SECONDS + 1
    fresh = _offer(store)
    assert store.get_policy(OWNER, UID) == {}
    assert view.guard(["news"])()
    result = store.record(OWNER, fresh["id"], _event(fresh, identity="fresh-no"))
    assert result["policy"]["news"]["revision"] > old_revision
    registry.publish(store.path, OWNER, UID, result["policy"])
    assert not view.guard(["news"])()


def test_manual_soft_recovery_after_wall_rollback_keeps_the_existing_hard_deadline():
    from services.proactive_feedback.policy import PolicyRegistry

    clock = SimpleNamespace(wall=1_000_000.0, tick=100.0)
    registry = PolicyRegistry(wall_clock=lambda: clock.wall, clock=lambda: clock.tick)
    state = apply_policy_decision(None, "skipped", now=clock.wall, offer_revision=0)
    state = apply_policy_decision(state, "skipped", now=clock.wall, offer_revision=1)
    view = registry.publish("fixture-db", OWNER, UID, {"news": state})
    clock.tick += 900
    clock.wall -= 3600
    recovered = apply_policy_decision(state, "accepted", now=clock.wall, offer_revision=state["revision"])
    registry.publish("fixture-db", OWNER, UID, {"news": recovered})
    clock.tick += 300
    assert view.guard(["news"])()


@pytest.mark.asyncio
async def test_cancelled_http_decision_still_publishes_its_committed_rejection(store, clock, monkeypatch):
    from main_routers import recommendation_router as routes
    from services.proactive_feedback.policy import PolicyRegistry

    registry = PolicyRegistry(wall_clock=lambda: clock.now)
    monkeypatch.setattr(routes, "policy_registry", registry)
    monkeypatch.setattr(routes, "_control", lambda owner: None)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    card = _offer(store)
    view = registry.publish(store.path, OWNER, UID, {})
    committed, release, finished = Event(), Event(), Event()

    def operate(operation):
        try:
            result = operation(store, OWNER)
            committed.set()
            if not release.wait(timeout=5):
                raise AssertionError("fixture did not release the committed worker")
            return result
        finally:
            finished.set()

    monkeypatch.setattr(routes, "_operate", operate)
    value = _event(card, identity="cancelled-no")
    request = SimpleNamespace(json=AsyncMock(return_value=value), headers={})
    task = asyncio.create_task(routes.recommendation_event(request, card["id"], routes.RecommendationEvent(**value)))
    try:
        assert await asyncio.to_thread(committed.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await asyncio.to_thread(store.get_policy, OWNER, UID)
    assert not view.guard(["news"])()
