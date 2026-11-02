import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from main_logic.recommendation_feedback import FeedbackConflict, RecommendationStore


OWNER = {"key": "account-a", "user_id": "a", "community": "https://community.example"}
OTHER = {**OWNER, "key": "account-b", "user_id": "b"}
TRACK = {"name": "A song", "artist": "Artist", "url": "https://example.com/song.mp3", "cover": ""}


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(tmp_path / "evidence.sqlite3")


def create(store):
    return store.create(OWNER, "Neko", "turn-1", TRACK, {"proactiveMusicEnabled": True})["id"]


def event(kind, event_id=None, **kwargs):
    return {"event_id": event_id or kind, "kind": kind, **kwargs}


def test_evidence_persists_and_exports_account_settings_and_actual_playback(store):
    rec = create(store)
    assert store.events(OWNER, 0, 100)["events"] == []
    store.record(OWNER, rec, event("shown"))
    assert store.record(OWNER, rec, event("accepted"))["playback_grant"]
    for kind, seconds in [("playing", 0), ("paused", 10), ("playing", 10), ("ended", 30)]:
        store.record(OWNER, rec, event(kind, f"{kind}-{seconds}", playback_id="play-1", listened_seconds=seconds))
    reopened = RecommendationStore(store.path)
    batch = reopened.events(OWNER, 0, 100)
    assert [row["kind"] for row in batch["events"]] == ["shown", "accepted", "playing", "paused", "playing", "ended"]
    assert batch["events"][-1]["listened_seconds"] == 30
    assert batch["events"][-1]["settings"] == {"proactiveMusicEnabled": True}
    assert batch["events"][-1]["user_id"] == "a"
    assert reopened.events(OTHER, 0, 100)["events"] == []
    assert reopened.events(OWNER, batch["next_cursor"], 100)["events"] == []


def test_two_windows_make_exactly_one_decision_and_retries_are_idempotent(store):
    rec = create(store)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda kind: store.record(OWNER, rec, event(kind)), ["accepted", "skipped"]))
    assert sum(result["applied"] for result in results) == 1
    rows = store.events(OWNER, 0, 100)["events"]
    assert len(rows) == 1
    winner = rows[0]
    retried = store.record(OWNER, rec, event(winner["kind"]))
    assert not retried["applied"]
    assert retried["playback_grant"] == (winner["kind"] == "accepted")


def test_account_switch_cannot_steal_recommendation(store):
    rec = create(store)
    with pytest.raises(FeedbackConflict, match="recommendation_not_found"):
        store.record(OTHER, rec, event("accepted"))
    assert store.record(OWNER, rec, event("skipped"))["state"] == "skipped"
    with pytest.raises(FeedbackConflict, match="not_accepted"):
        store.record(OWNER, rec, event("playing", playback_id="p"))


def test_skip_unanswered_and_failure_are_separate_signals(store):
    create(store)  # No operation is not a negative sample.
    skipped = create(store)
    store.record(OWNER, skipped, event("skipped"))
    failed = create(store)
    store.record(OWNER, failed, event("accepted"))
    store.record(OWNER, failed, event("failed", playback_id="p", reason="media_error"))
    assert [row["kind"] for row in store.events(OWNER, 0, 100)["events"]] == ["skipped", "accepted", "failed"]


def test_expiry_collision_capacity_and_unknown_schema_fail_without_overwriting(store):
    rec = create(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE recommendations SET expires_at=0")
    with pytest.raises(FeedbackConflict, match="expired"):
        store.record(OWNER, rec, event("accepted"))
    store.record(OWNER, rec, event("shown"))
    other = create(store)
    with pytest.raises(FeedbackConflict, match="collision"):
        store.record(OWNER, other, event("shown"))
    store.MAX_RECOMMENDATIONS = 2
    with pytest.raises(FeedbackConflict, match="capacity"):
        create(store)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(FeedbackConflict, match="unsupported_feedback_schema"):
        store.events(OWNER, 0, 100)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "12", 1e100])
def test_bad_durations_are_rejected(store, value):
    rec = create(store)
    with pytest.raises(FeedbackConflict, match="duration"):
        store.record(OWNER, rec, event("shown", listened_seconds=value))


def test_terminal_event_fences_late_play_and_wrong_playback(store):
    rec = create(store)
    store.record(OWNER, rec, event("accepted"))
    store.record(OWNER, rec, event("playing", playback_id="p"))
    assert not store.record(OWNER, rec, event("accepted"))["playback_grant"]
    with pytest.raises(FeedbackConflict, match="owner_changed"):
        store.record(OWNER, rec, event("paused", playback_id="other"))
    store.record(OWNER, rec, event("stopped", playback_id="p"))
    assert not store.record(OWNER, rec, event("playing", "late", playback_id="p"))["applied"]
    assert store.events(OWNER, 0, 100)["events"][-1]["kind"] == "stopped"


def test_event_capacity_rolls_back_decision_and_evidence_excludes_signed_urls(store):
    rec = store.create(OWNER, "Neko", "turn", {**TRACK, "url": TRACK["url"] + "?token=secret"}, {})["id"]
    store.MAX_EVENTS = 0
    with pytest.raises(FeedbackConflict, match="capacity"):
        store.record(OWNER, rec, event("accepted"))
    store.MAX_EVENTS = 100
    assert store.record(OWNER, rec, event("accepted"))["playback_grant"]
    with pytest.raises(FeedbackConflict, match="missing_playback"):
        store.record(OWNER, rec, event("playing"))
    exported = str(store.events(OWNER, 0, 100))
    assert "secret" not in exported and "token=" not in exported
    assert "content_id" in exported


@pytest.mark.parametrize('category', ['news', 'community', 'video', 'personal', 'meme', 'vision', 'window', 'chat', 'work_break', 'anti_slack'])
def test_content_decisions_are_categorized_and_never_grant_media_playback(store, category):
    rec = store.create(OWNER, 'Neko', 'content-turn', TRACK, {}, category=category)['id']
    store.record(OWNER, rec, event('shown'))
    accepted = store.record(OWNER, rec, event('accepted'))
    assert accepted['action_grant'] and not accepted['playback_grant']
    assert not store.record(OWNER, rec, event('accepted', 'other-window'))['action_grant']
    with pytest.raises(FeedbackConflict, match='invalid_feedback_kind'):
        store.record(OWNER, rec, event('playing', playback_id='fake-media'))
    rows = store.events(OWNER, 0, 100)['events']
    assert [row['category'] for row in rows] == [category, category]
    assert [row['kind'] for row in rows] == ['shown', 'accepted']


def test_content_permissions_are_checked_in_the_decision_transaction(store):
    rec = store.create(OWNER, 'Neko', 'turn', TRACK, {}, category='news')['id']
    with pytest.raises(FeedbackConflict, match='recommendation_disabled'):
        store.record(OWNER, rec, event('accepted'), disabled_categories=frozenset({'news'}))
    assert store.events(OWNER, 0, 100)['events'] == []
    assert store.record(OWNER, rec, event('skipped'), disabled_categories=frozenset({'news'}))['state'] == 'skipped'
