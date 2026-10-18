"""Deterministic durable timing tests; no account, app or network is used."""

from contextlib import closing

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.runtime import ChatSyncServiceError
from services.chat_sync.scheduler import BODY_INTERVAL_MS, ChatSyncScheduler, body_eligible, body_eligible_at


def queue(**values):
    """Represent new originals using arrival clocks, never occurrence metadata."""
    return {"pendingOriginals": 1, "pendingBytes": 10, "oldestEnqueuedAt": 1000,
            "lastEnqueuedAt": 1000, "legacyPending": False, "hasSealedBatch": False,
            "pendingDeletions": 0, **values}


@pytest.mark.parametrize('channel', ['pull', 'profile'])
@pytest.mark.parametrize('attempts', [1, 8])
def test_new_transport_retry_uses_three_second_cap_without_periodic_poll(tmp_path, channel, attempts):
    """Pure transport failures retry after jitter without the ordinary poll due."""
    clock = [1000]
    scheduler = ChatSyncScheduler(tmp_path / 'scheduler.db', clock=lambda: clock[0], uniform=lambda lo, hi: hi)
    assert scheduler.claim('a', channel, 1, 'session')
    for _ in range(attempts):
        scheduler.failure('a', channel, 1, 'session', ValueError('chat_sync_unavailable'))
    clock[0] += 2999
    assert not scheduler.ready('a', channel, 1, 'session')
    clock[0] += 1
    assert scheduler.ready('a', channel, 1, 'session')


@pytest.mark.parametrize("attempts", [1, 8])
def test_pull_timeout_retry_and_safe_error_survive_restart(tmp_path, attempts):
    """A bounded download timeout remains a three-second retry after restart."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda lo, hi: hi)
    assert scheduler.claim("a", "pull", 3, "session")
    for _ in range(attempts):
        scheduler.failure("a", "pull", 3, "session", ValueError("chat_sync_pull_timeout"))
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    with closing(scheduler._connect()) as conn:
        task = conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone()
    assert task["retry_at"] == 4000 and task["failures"] == attempts
    assert task["due_at"] == 0
    assert task["blocked_generation"] is None and task["blocked_session"] is None
    assert scheduler.last_error("a") == "chat_sync_pull_timeout"
    clock[0] = 3999
    assert not scheduler.ready("a", "pull", 3, "session", manual=True)
    clock[0] += 1
    assert scheduler.ready("a", "pull", 3, "session")


def test_notification_and_verified_login_cannot_bypass_pull_timeout_retry(tmp_path):
    """Both forms of notification wake retain the timeout's durable retry fence."""
    clock = [1000]
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: clock[0], uniform=lambda *_: 0)
    assert scheduler.claim("a", "pull", 3, "session")
    scheduler.failure("a", "pull", 3, "session", ValueError("chat_sync_pull_timeout"))
    with closing(scheduler._connect()) as conn:
        before = dict(conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone())
    scheduler.request_check("a", "pull")
    scheduler.request_check("a", "pull", verified_login=(3, "session"))
    with closing(scheduler._connect()) as conn:
        after = dict(conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone())
    assert after == before
    clock[0] = 3999
    assert not scheduler.claim("a", "pull", 3, "session")
    assert not scheduler.claim("a", "pull", 3, "session", manual=True)
    clock[0] += 1
    assert scheduler.claim("a", "pull", 3, "session")


def test_pull_timeout_retry_keeps_other_roles_origin_retry_after(tmp_path):
    """The shorter timeout retry cannot shorten an origin-wide 429 deadline."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.failure("b", "body", 3, "session", ChatSyncServiceError(429, 240000))
    scheduler.failure("a", "pull", 3, "session", ValueError("chat_sync_pull_timeout"))
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.arm_wake()
    scheduler.request_check("a", "pull", verified_login=(3, "session"))
    for now in (4000, 240999):
        clock[0] = now
        assert not scheduler.origin_ready()
        for domain in ("a", "b"):
            for channel in ("body", "deletion", "pull", "profile"):
                assert not scheduler.ready(domain, channel, 3, "session", queue=queue(), manual=True)
    clock[0] += 1
    assert scheduler.origin_ready()
    assert scheduler.ready("a", "pull", 3, "session")
    assert scheduler.ready("b", "body", 3, "session", queue=queue(), manual=True)


def test_old_transport_deadline_is_not_shortened_by_restart_wake_or_notification(tmp_path):
    """Previously persisted long transport retries retain their original deadline."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert scheduler.claim("a", "pull", 3, "session")
    # Recreate a retry persisted before the shorter transport cap was introduced.
    with closing(scheduler._connect()) as conn, conn:
        conn.execute("UPDATE scheduler_tasks SET retry_at=601000,failures=8,last_error='chat_sync_unavailable' "
                     "WHERE domain='a' AND channel='pull'")
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.arm_wake()
    scheduler.request_check("a", "pull", verified_login=(3, "session"))
    with closing(scheduler._connect()) as conn:
        task = conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone()
    assert task["retry_at"] == 601000 and task["failures"] == 8
    assert task["last_error"] == "chat_sync_unavailable" and task["due_at"] == 0
    for now in (4000, 600999):
        clock[0] = now
        assert not scheduler.ready("a", "pull", 3, "session", manual=True)
    clock[0] += 1
    assert scheduler.ready("a", "pull", 3, "session")


def test_short_idle_and_bounded_oldest_replace_minute_eligibility():
    """Small batches wait 200ms; continuous arrivals wait at most 500ms."""
    assert not body_eligible(queue(), 1199)
    assert body_eligible(queue(), 1200)
    assert body_eligible(queue(lastEnqueuedAt=1499), 1500)


def test_body_deadline_distinguishes_no_work_from_batched_and_urgent_work():
    """Only a future batch deadline asks the runtime to shorten its local tick."""
    assert body_eligible_at(queue(pendingOriginals=0), 1000) is None
    assert body_eligible_at(queue(), 1000) == 1200
    assert body_eligible_at(queue(lastEnqueuedAt=1499), 1000) == 1500
    assert body_eligible_at(queue(pendingOriginals=20), 1000) == 1000
    assert body_eligible_at(queue(oldestEnqueuedAt=None, lastEnqueuedAt=None), 1000) is None


def test_notification_due_wake_preserves_retry_auth_and_origin_fences(tmp_path):
    """Hints only invalidate periodic due time, never HTTP rejection deadlines."""
    clock = [1000]
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: clock[0], uniform=lambda *_: 0)
    assert scheduler.claim("a", "pull", 1, "session")
    scheduler.success("a", "pull")
    assert not scheduler.ready("a", "pull", 1, "session")
    scheduler.request_check("a", "pull")
    assert scheduler.ready("a", "pull", 1, "session")
    scheduler.failure("a", "pull", 1, "session", ChatSyncServiceError(503))
    scheduler.request_check("a", "pull")
    assert not scheduler.ready("a", "pull", 1, "session")
    clock[0] += 5000
    assert scheduler.ready("a", "pull", 1, "session")
    scheduler.failure("a", "pull", 1, "session", ChatSyncServiceError(403))
    scheduler.request_check("a", "pull")
    assert not scheduler.ready("a", "pull", 1, "session")
    scheduler.failure("b", "body", 1, "session", ChatSyncServiceError(429, 240000))
    scheduler.request_check("a", "profile")
    assert not scheduler.ready("a", "profile", 1, "session")


@pytest.mark.parametrize("channel", ["pull", "profile"])
def test_saved_session_validation_retry_does_not_wait_for_the_periodic_poll(tmp_path, channel):
    """A temporary resolver outage keeps its five-second retry without a two-minute delay."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert scheduler.claim("a", channel, 3, "session")
    with closing(scheduler._connect()) as conn:
        due = conn.execute("SELECT due_at FROM scheduler_tasks WHERE domain='a' AND channel=?", (channel,)).fetchone()[0]
    assert due > clock[0] + 5000
    scheduler.failure("a", channel, 3, "session", ValueError("chat_sync_session_unavailable"))
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    clock[0] += 4999
    assert not scheduler.ready("a", channel, 3, "session")
    clock[0] += 1
    assert scheduler.ready("a", channel, 3, "session")


@pytest.mark.parametrize("channel", ["pull", "profile"])
def test_verified_login_releases_only_exact_local_login_blocker_after_restart(tmp_path, channel):
    """A successful subscription can repair the old local resolver classification."""
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: 1000, uniform=lambda *_: 0)
    other_channel = "profile" if channel == "pull" else "pull"
    for domain, lane in (("a", channel), ("a", other_channel), ("b", channel)):
        scheduler.failure(domain, lane, 3, "session", ValueError("chat_sync_login_required"))
    scheduler = ChatSyncScheduler(path, clock=lambda: 1000, uniform=lambda *_: 0)
    scheduler.request_check("a", channel, verified_login=(3, "session"))
    assert scheduler.ready("a", channel, 3, "session")
    assert scheduler.last_error("a") == "chat_sync_login_required"
    assert not scheduler.ready("a", other_channel, 3, "session")
    assert not scheduler.ready("b", channel, 3, "session")
    with closing(scheduler._connect()) as conn:
        repaired = conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel=?", (channel,)).fetchone()
    assert repaired["blocked_generation"] is None and repaired["blocked_session"] is None
    assert repaired["last_error"] is None


@pytest.mark.parametrize("domain,channel,verified", [
    ("b", "pull", (3, "session")), ("a", "profile", (3, "session")),
    ("a", "pull", (4, "session")), ("a", "pull", (3, "replacement")),
    ("a", "pull", None),
])
def test_verified_login_cannot_release_another_domain_channel_generation_or_session(tmp_path, domain, channel, verified):
    """Only the exact captured grant may repair its own local login blocker."""
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: 1000, uniform=lambda *_: 0)
    scheduler.failure("a", "pull", 3, "session", ValueError("chat_sync_login_required"))
    scheduler.request_check(domain, channel, verified_login=verified)
    assert not scheduler.ready("a", "pull", 3, "session")
    assert scheduler.last_error("a") == "chat_sync_login_required"


@pytest.mark.parametrize("failure", [
    ChatSyncServiceError(401), ChatSyncServiceError(403), ChatSyncServiceError(429, 240000),
    ChatSyncServiceError(503), ValueError("invalid_chat_sync_response"), ValueError("chat_sync_session_changed"),
])
def test_verified_login_preserves_http_auth_schema_session_and_retry_fences(tmp_path, failure):
    """A subscription is not authority to forgive a different HTTP/error result."""
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: 1000, uniform=lambda *_: 0)
    scheduler.failure("a", "pull", 3, "session", failure)
    with closing(scheduler._connect()) as conn:
        before = dict(conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone())
        origin_before = conn.execute("SELECT origin_retry_at FROM scheduler_device WHERE singleton=1").fetchone()[0]
    scheduler.request_check("a", "pull", verified_login=(3, "session"))
    with closing(scheduler._connect()) as conn:
        after = dict(conn.execute("SELECT * FROM scheduler_tasks WHERE domain='a' AND channel='pull'").fetchone())
        origin_after = conn.execute("SELECT origin_retry_at FROM scheduler_device WHERE singleton=1").fetchone()[0]
    assert after == {**before, "due_at": 0}
    assert origin_after == origin_before
    assert not scheduler.ready("a", "pull", 3, "session", manual=True)


def test_verified_login_repair_keeps_origin_retry_after(tmp_path):
    """Repairing local login state never bypasses another lane's origin throttle."""
    clock = [1000]
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.failure("a", "pull", 3, "session", ValueError("chat_sync_login_required"))
    scheduler.failure("b", "body", 3, "session", ChatSyncServiceError(429, 240000))
    scheduler.request_check("a", "pull", verified_login=(3, "session"))
    assert scheduler.last_error("a") is None
    assert not scheduler.ready("a", "pull", 3, "session", manual=True)
    clock[0] += 240000
    assert scheduler.ready("a", "pull", 3, "session")


@pytest.mark.parametrize("changes,now", [
    ({}, 61_000), ({"pendingOriginals": 20}, 1000), ({"pendingBytes": 64 * 1024}, 1000),
    ({"lastEnqueuedAt": 300_999}, 301_000), ({"legacyPending": True, "oldestEnqueuedAt": None,
                                           "lastEnqueuedAt": None}, 1000),
    ({"hasSealedBatch": True}, 1000),
])
def test_automatic_body_eligibility(changes, now):
    """Each approved threshold independently admits work, including old pending."""
    assert body_eligible(queue(**changes), now)


def test_new_messages_reset_idle_but_do_not_reset_oldest_wait():
    """Continuous arrivals cannot postpone the oldest original past 500ms."""
    waiting = queue(lastEnqueuedAt=1499)
    assert not body_eligible(waiting, 1499)
    assert body_eligible(waiting, 1500)
    assert not body_eligible(queue(pendingOriginals=0, legacyPending=True), 999_999)


def test_device_body_interval_fairness_and_attempt_survive_restart(tmp_path):
    """Fair role rotation survives restart without an extra normal body cooldown."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert scheduler.claim("a", "body", 1, "session", queue=queue(), manual=True)
    restarted = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert restarted.ordered(["a", "b"]) == ["b", "a"]
    assert BODY_INTERVAL_MS == 0
    assert restarted.claim("b", "body", 1, "session", queue=queue(), manual=True)
    assert restarted.claim("a", "deletion", 1, "session")
    assert not restarted.claim("b", "body", 1, "session", queue=queue(), manual=True)


def test_startup_periodic_stagger_and_retry_are_persisted(tmp_path):
    """Startup is prompt; restart does not bypass periodic fallback or retry."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda lo, hi: hi)
    scheduler.arm_wake()
    restarted = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    restarted.arm_wake()
    assert restarted.claim("a", "pull", 1, "session")
    restarted.success("a", "pull")
    assert restarted.claim("a", "deletion", 1, "session")
    clock[0] = 120_999
    assert not restarted.ready("a", "pull", 1, "session")
    clock[0] = 121_000
    assert restarted.claim("a", "pull", 1, "session")
    restarted.failure("a", "pull", 1, "session", ValueError("chat_sync_unavailable"))
    clock[0] += 1000
    assert not ChatSyncScheduler(path, clock=lambda: clock[0]).ready("a", "pull", 1, "session", manual=True)


def test_origin_retry_after_covers_all_channels_roles_and_manual(tmp_path):
    """A response without throttle scope gates every request to the pinned origin."""
    clock = [1000]
    scheduler = ChatSyncScheduler(tmp_path / "scheduler.db", clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.failure("a", "body", 1, "session", ChatSyncServiceError(429, 240_000))
    for channel in ("body", "deletion", "pull"):
        assert not scheduler.claim("b", channel, 1, "session", queue=queue(), manual=True)
    clock[0] += 240_000
    assert scheduler.origin_ready()
    assert scheduler.claim("b", "deletion", 1, "session")


def test_fixed_error_fences_survive_restart_and_do_not_store_arbitrary_text(tmp_path):
    """Only a new login or explicit consent generation resumes blocked work."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.failure("a", "pull", 1, "old", ChatSyncServiceError(401))
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert not scheduler.ready("a", "pull", 1, "old", manual=True)
    assert scheduler.ready("a", "pull", 1, "new", manual=True)
    scheduler.failure("b", "pull", 1, "old", ValueError("chat_sync_private user text"))
    assert scheduler.last_error("b") == "chat_sync_unavailable"
    assert not scheduler.ready("b", "pull", 1, "new", manual=True)
    assert scheduler.ready("b", "pull", 2, "new", manual=True)


@pytest.mark.parametrize("status", [408, 425, 500, 503])
def test_temporary_http_failures_retry_instead_of_requiring_new_consent(tmp_path, status):
    """Timeout, early data and server failures share a bounded durable backoff."""
    clock = [1000]
    path = tmp_path / "scheduler.db"
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    scheduler.failure("a", "deletion", 1, "session", ChatSyncServiceError(status))
    scheduler = ChatSyncScheduler(path, clock=lambda: clock[0], uniform=lambda *_: 0)
    assert not scheduler.ready("a", "deletion", 1, "session", manual=True)
    clock[0] += 5000
    assert scheduler.ready("a", "deletion", 1, "session", manual=True)
