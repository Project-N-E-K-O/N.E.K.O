# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The commission ledger: tables, the one status transition, events, outbox."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest

from nekoclaw.host.ledger import ALLOWED_TRANSITIONS, STATUSES, Ledger
from nekoclaw.host.outbox import OutboxSender, retry_delay


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def ledger(tmp_path: Any) -> Any:
    clock = Clock()
    book = Ledger(tmp_path / "nekoclaw" / "ledger.db", clock=clock)
    book.clock = clock  # type: ignore[attr-defined]
    yield book
    await book.close()


async def _file(ledger: Ledger, **values: Any) -> Any:
    fields = {"lanlan_name": "Lan", "title": "weather", "dispatch_text": "check the weather", "source": "dispatch"}
    fields.update(values)
    commission, created = await ledger.create(**fields)
    assert created
    return commission


async def _walk(ledger: Ledger, commission_id: str, status: str) -> None:
    """Bring a queued commission to ``status`` by allowed moves."""
    paths = {
        "queued": [],
        "working": ["working"],
        "waiting_user": ["working", "waiting_user"],
        "done": ["working", "done"],
        "blocked": ["working", "blocked"],
        "failed": ["failed"],
        "cancelled": ["cancelled"],
    }
    for step in paths[status]:
        assert (await ledger.transition(commission_id, step)).applied


# ---------------------------------------------------------------------------
# File and tables
# ---------------------------------------------------------------------------


async def test_file_is_created_on_first_use_not_before(tmp_path):
    path = tmp_path / "nekoclaw" / "ledger.db"
    ledger = Ledger(path)
    try:
        assert not path.exists()
        assert not ledger.opened
        await ledger.select()
        assert path.exists()
    finally:
        await ledger.close()


async def test_call_budget_preserves_active_descendants_and_can_finish_in_place(ledger, monkeypatch):
    from nekoclaw.host import call_audit

    monkeypatch.setattr(call_audit, "BUDGET_BYTES", call_audit.RECORD_BYTES * 3)
    ref = "memory_message:" + "a" * 32
    root = await _file(ledger, source_message_ref=ref)
    child = await _file(ledger, parent_id=root.id, source="task_agent")
    await _walk(ledger, root.id, "done")
    async with call_audit.call(ledger, child.id, "device", "builtin:browser:open"):
        with pytest.raises(call_audit.AuditFull):
            async with call_audit.call(ledger, child.id, "device", "builtin:browser:open"):
                pytest.fail("nothing can execute without a record")
    pending = await _file(ledger, source_message_ref="conversation_input:" + "b" * 32)
    assert (await ledger.events(pending.id))[0].payload["audit"] == call_audit.SOURCE_NOT_RECORDED
    ledger.clock.now += call_audit.RETENTION + 1
    data = await ledger.run(lambda db: call_audit.read_trace(db, child.id, now=ledger.clock()))
    assert len(data) == 3 and data[-1]["status"] == "completed"
    assert data[0]["source_message_ref"] == ref and data[1]["parent_call_id"] == root.id
    await _walk(ledger, child.id, "done")
    # Only after both commissions are over can this entire chain be reclaimed.
    async with call_audit.call(ledger, pending.id, "device", "browser"):
        pass
    pending_records = await ledger.run(lambda db: call_audit.read_trace(db, pending.id, now=ledger.clock()))
    assert pending_records[0]["reason"] == call_audit.SOURCE_NOT_RECORDED
    assert await ledger.run(lambda db: call_audit.read_trace(db, child.id, now=ledger.clock())) == []
    assert (await ledger.get(pending.id)).status == "queued"


@pytest.mark.parametrize("status", ["failed", "blocked"])
async def test_resuming_an_expired_commission_starts_with_no_old_calls(ledger, status):
    from nekoclaw.host import call_audit

    item = await _file(ledger, source_message_ref="conversation_input:" + "a" * 32)
    await _walk(ledger, item.id, status)
    ledger.clock.now += call_audit.RETENTION
    assert (await ledger.transition(item.id, "working")).applied
    async with call_audit.call(ledger, item.id, "device", "browser"):
        pass
    data = await ledger.run(lambda db: call_audit.read_trace(db, item.id, now=ledger.clock()))
    assert len(data) == 2 and data[0]["source_message_ref"] is None


@pytest.mark.parametrize("next_status", ["failed", "cancelled"])
async def test_ending_an_expired_blocked_commission_again_does_not_restore_old_calls(ledger, next_status):
    from nekoclaw.host import call_audit

    item = await _file(ledger, source_message_ref="conversation_input:" + "a" * 32)
    await _walk(ledger, item.id, "blocked")
    ledger.clock.now += call_audit.RETENTION
    def read(db):
        return call_audit.read_trace(db, item.id, now=ledger.clock())

    assert await ledger.run(read) == []
    assert (await ledger.transition(item.id, next_status)).applied
    assert await ledger.run(read) == []


async def test_losing_the_ledger_after_execution_preserves_the_action_result(ledger, caplog):
    from nekoclaw.host import call_audit

    item = await _file(ledger)
    async with call_audit.call(ledger, item.id, "plugin", "weather") as attempt:
        await ledger.close()
    assert f"could not close host call {attempt.call_id} (RuntimeError)" in caplog.text


async def test_blocked_chain_expires_and_releases_its_budget(ledger, monkeypatch):
    from nekoclaw.host import call_audit

    monkeypatch.setattr(call_audit, "BUDGET_BYTES", 3 * call_audit.RECORD_BYTES)
    root = await _file(ledger)
    child = await _file(ledger, parent_id=root.id, source="task_agent")
    async with call_audit.call(ledger, child.id, "device", "browser"):
        pass
    await _walk(ledger, child.id, "blocked")
    with pytest.raises(call_audit.CommissionEnded):
        async with call_audit.call(ledger, child.id, "device", "browser"):
            pytest.fail("blocked work must first be resumed")
    await _walk(ledger, root.id, "done")
    ledger.clock.now += call_audit.RETENTION
    assert await ledger.run(lambda db: call_audit.read_trace(db, child.id, now=ledger.clock())) == []
    fresh = await _file(ledger)
    async with call_audit.call(ledger, fresh.id, "device", "browser"):
        pass
    assert await ledger.run(lambda db: db.execute("SELECT DISTINCT trace_id FROM host_calls").fetchone()[0]) == fresh.id


async def test_expired_trace_hidden_before_maintenance_and_unknown_source_stays_unknown(ledger):
    from nekoclaw.host import call_audit

    item = await _file(ledger, source_message_ref="private text masquerading as an ID")
    async with call_audit.call(ledger, item.id, "gateway", "example"):
        pass
    await ledger.update(item.id, session_id="later-session")
    await _walk(ledger, item.id, "done")
    data = await ledger.run(lambda db: call_audit.read_trace(db, item.id, now=ledger.clock()))
    assert (data[0]["source_message_ref"], data[0]["session_id"], data[0]["status"]) == (None, "later-session", "done")
    assert "private text" not in str(data)
    ledger.clock.now += call_audit.RETENTION
    assert await ledger.run(lambda db: call_audit.read_trace(db, item.id, now=ledger.clock())) == []
    await ledger.run(lambda db: call_audit.reclaim(db, ledger.clock()))
    assert await ledger.run(lambda db: db.execute("SELECT records FROM host_call_budget").fetchone()[0]) == 0


async def test_tables_and_fields_of_the_design(ledger, tmp_path):
    await ledger.open()
    with sqlite3.connect(ledger.path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commissions)")}
    conn.close()
    assert {"commissions", "events", "outbox", "preferences"} <= tables
    # Design section 5: number, owner, title, her words, the user's words,
    # source, route, project, working directory, status, runtime, start and
    # end, result summary, pending question, delivery, parent.
    assert {
        "id", "lanlan_name", "title", "dispatch_text", "user_text", "source", "route", "project",
        "workdir", "status", "runtime", "started_at", "ended_at", "result_summary",
        "pending_question", "delivery", "parent_id",
    } <= columns


async def test_path_may_be_computed_late(tmp_path):
    asked: list[int] = []

    def where() -> Any:
        asked.append(1)
        return tmp_path / "late" / "ledger.db"

    ledger = Ledger(where)
    try:
        assert asked == []
        await ledger.select()
        assert asked == [1]
        assert (tmp_path / "late" / "ledger.db").exists()
    finally:
        await ledger.close()


async def test_closed_ledger_cannot_be_used(ledger):
    await ledger.select()
    await ledger.close()

    with pytest.raises(RuntimeError):
        await ledger.select()


# ---------------------------------------------------------------------------
# Commissions
# ---------------------------------------------------------------------------


async def test_new_commission_is_queued_with_its_texts(ledger):
    commission = await _file(ledger, user_text="how is the weather", conversation_id="c1", lang="zh")

    assert commission.status == "queued"
    assert commission.route is None
    assert commission.dispatch_text == "check the weather"
    assert commission.user_text == "how is the weather"
    assert commission.created_at == commission.updated_at == 1000.0
    assert commission.started_at is None and commission.ended_at is None
    assert (await ledger.get(commission.id)) == commission


async def test_unknown_fields_source_and_status_are_rejected(ledger):
    with pytest.raises(ValueError):
        await ledger.create(lanlan_name="Lan", title="t", dispatch_text="d", source="dispatch", bogus=1)
    with pytest.raises(ValueError):
        await ledger.create(lanlan_name="Lan", title="t", dispatch_text="d", source="elsewhere")
    with pytest.raises(ValueError):
        await ledger.create(lanlan_name="Lan", title="t", dispatch_text="d", source="dispatch", status="paused")


async def test_select_filters_by_owner_status_and_parent(ledger):
    first = await _file(ledger, title="one")
    ledger.clock.now += 1
    second = await _file(ledger, title="two", lanlan_name="Other")
    ledger.clock.now += 1
    child = await _file(ledger, title="child", parent_id=first.id, source="task_agent")
    await _walk(ledger, first.id, "done")

    assert [item.id for item in await ledger.select()] == [child.id, second.id, first.id]
    assert [item.id for item in await ledger.select(oldest_first=True)] == [first.id, second.id, child.id]
    assert [item.id for item in await ledger.select(lanlan_name="Other")] == [second.id]
    assert [item.id for item in await ledger.select(statuses={"done"})] == [first.id]
    assert [item.id for item in await ledger.select(parent_id=first.id)] == [child.id]
    assert [item.id for item in await ledger.select(limit=1)] == [child.id]


async def test_create_unless_returns_the_existing_commission(ledger):
    existing = await _file(ledger)

    commission, created = await ledger.create(
        unless=lambda active: active[0] if active else None,
        lanlan_name="Lan",
        title="again",
        dispatch_text="check the weather",
        source="dispatch",
    )

    assert not created
    assert commission.id == existing.id
    assert len(await ledger.select()) == 1


async def test_two_concurrent_creates_of_the_same_request_file_one(ledger):
    def same(active: list[Any]) -> Any:
        return next((item for item in active if item.dispatch_text == "check the weather"), None)

    results = await asyncio.gather(
        *(
            ledger.create(unless=same, lanlan_name="Lan", title="w", dispatch_text="check the weather", source="dispatch")
            for _ in range(8)
        )
    )

    assert sum(1 for _commission, created in results if created) == 1
    assert len({commission.id for commission, _created in results}) == 1
    assert len(await ledger.select()) == 1


async def test_create_unless_only_sees_the_owners_active_commissions(ledger):
    other = await _file(ledger, lanlan_name="Other")
    finished = await _file(ledger)
    await _walk(ledger, finished.id, "done")
    seen: list[list[str]] = []

    def record(active: list[Any]) -> None:
        seen.append([item.id for item in active])
        return None

    await ledger.create(unless=record, lanlan_name="Lan", title="t", dispatch_text="d", source="dispatch")

    assert seen == [[]]
    assert other.id not in seen[0]


# ---------------------------------------------------------------------------
# The transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("target", STATUSES)
async def test_every_pair_of_statuses_follows_the_table(ledger, current, target):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, current)

    result = await ledger.transition(commission.id, target, reason="test")

    allowed = target in ALLOWED_TRANSITIONS[current]
    assert result.applied is allowed
    assert result.previous == current
    assert (await ledger.get(commission.id)).status == (target if allowed else current)
    if not allowed:
        assert result.refusal


def test_the_transition_table_is_the_intended_one():
    assert ALLOWED_TRANSITIONS == {
        "queued": {"working", "failed", "cancelled"},
        # Queued when its idle session was closed and a task agent it started reports back.
        "working": {"waiting_user", "queued", "done", "blocked", "failed", "cancelled"},
        # Queued when its closed session's answer finds no free place.
        "waiting_user": {"working", "queued", "failed", "cancelled"},
        # Taken up again, at once or queued when no task agent place is free.
        "blocked": {"working", "queued", "failed", "cancelled"},
        "failed": {"working", "queued"},
        "done": set(),
        "cancelled": set(),
    }


@pytest.mark.parametrize("late", ["done", "failed", "working", "blocked", "waiting_user", "queued"])
async def test_a_stopped_commission_cannot_be_overwritten(ledger, late):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "working")
    assert (await ledger.transition(commission.id, "cancelled", reason="stopped by the user")).applied

    result = await ledger.transition(commission.id, late, reason="late result", result_summary="should not land")

    assert result.applied is False
    after = await ledger.get(commission.id)
    assert after.status == "cancelled"
    assert after.status_reason == "stopped by the user"
    assert after.result_summary is None
    refused = [event for event in await ledger.events(commission.id) if event.type == "transition_refused"]
    assert refused[-1].payload == {"from": "cancelled", "to": late, "reason": "late result"}


async def test_transition_records_times_reason_and_fields(ledger):
    commission = await _file(ledger)
    ledger.clock.now = 1010.0

    working = await ledger.transition(commission.id, "working", reason="started", route="plugin", plugin_id="weather")

    assert working.applied
    assert (working.commission.started_at, working.commission.ended_at) == (1010.0, None)
    assert (working.commission.route, working.commission.plugin_id) == ("plugin", "weather")
    ledger.clock.now = 1020.0
    done = await ledger.transition(commission.id, "done", reason="ok", result_summary="sunny")
    assert (done.commission.started_at, done.commission.ended_at) == (1010.0, 1020.0)
    assert (done.commission.status_reason, done.commission.result_summary) == ("ok", "sunny")
    statuses = [event.payload for event in await ledger.events(commission.id) if event.type == "status"]
    assert statuses == [
        {"from": "queued", "to": "working", "reason": "started"},
        {"from": "working", "to": "done", "reason": "ok"},
    ]


async def test_pending_question_lives_only_while_waiting(ledger):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "working")

    waiting = await ledger.transition(commission.id, "waiting_user", pending_question='{"kind":"questions"}')
    assert waiting.commission.question() == {"kind": "questions"}

    back = await ledger.transition(commission.id, "working")
    assert back.commission.pending_question is None


async def test_taking_a_failed_commission_up_again_clears_its_end(ledger):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "failed")
    assert (await ledger.get(commission.id)).ended_at is not None

    again = await ledger.transition(commission.id, "working", reason="taken up again")

    assert again.applied and again.commission.ended_at is None


async def test_transition_of_an_unknown_commission_or_status(ledger):
    result = await ledger.transition("nope", "working")
    assert (result.applied, result.commission, result.refusal) == (False, None, "unknown commission")

    commission = await _file(ledger)
    with pytest.raises(ValueError):
        await ledger.transition(commission.id, "paused")
    # Further columns can ride along, but never a second status.
    with pytest.raises(TypeError):
        await ledger.transition(commission.id, "working", status="done")
    with pytest.raises(ValueError):
        await ledger.transition(commission.id, "working", id="other")


async def test_update_cannot_change_the_status(ledger):
    commission = await _file(ledger)

    with pytest.raises(ValueError):
        await ledger.update(commission.id, status="done")

    updated = await ledger.update(commission.id, title="new title", delivery="passive")
    assert (updated.status, updated.title, updated.delivery) == ("queued", "new title", "passive")


async def test_there_is_no_other_way_to_write_a_status():
    # The source must hold exactly one UPDATE that can write the status column:
    # the one inside the transition. ``update`` builds its SET list from
    # validated names, which exclude it.
    import inspect

    from nekoclaw.host import ledger as module

    source = inspect.getsource(module)
    # A third one gives the commissions of an earlier build their sessions,
    # and names the one column it writes. A fourth notes that a commission
    # rests, and a fifth takes that back; each names the two it writes. So
    # does the one that writes how much a commission may use, which names its
    # one. The seventh adds up what was used: its SET list is built from the
    # columns that are counted, and from nothing else.
    assert source.count("UPDATE commissions SET") == 7
    assert source.count("UPDATE commissions SET session_id = ? WHERE id = ?") == 1
    assert source.count("UPDATE commissions SET resting_since = ?, updated_at = ? WHERE id = ?") == 1
    assert source.count("UPDATE commissions SET resting_since = NULL, updated_at = ? ") == 1
    assert source.count("UPDATE commissions SET usage_allowed = ? WHERE id = ?") == 1
    assert source.count('f"UPDATE commissions SET {assignments} WHERE id = ?"') == 3
    assert source.count("for name, amount in zip(_USAGE_COLUMNS, amounts, strict=True)") == 1
    assert "status" not in module._UPDATABLE
    assert "host_instance" not in module._UPDATABLE
    assert "resting_since" not in module._UPDATABLE
    assert not {"status", "host_instance"} & set(module._USAGE_COLUMNS)
    # What was used and what may be are written by their own methods only.
    assert not ({"usage_allowed", *module._USAGE_COLUMNS} & module._UPDATABLE)


# ---------------------------------------------------------------------------
# What an earlier process left behind
# ---------------------------------------------------------------------------


async def test_unfinished_commissions_of_an_earlier_process_fail_on_open(tmp_path):
    path = tmp_path / "ledger.db"
    clock = Clock()
    first = Ledger(path, clock=clock)
    await _file(first, title="queued")
    working = await _file(first, title="working")
    waiting = await _file(first, title="waiting")
    blocked = await _file(first, title="blocked")
    done = await _file(first, title="done")
    deferred = await _file(first, title="deferred")
    expired = await _file(first, title="expired")
    await _walk(first, working.id, "working")
    await _walk(first, waiting.id, "waiting_user")
    await _walk(first, blocked.id, "blocked")
    await _walk(first, done.id, "done")
    await _walk(first, deferred.id, "working")
    await first.update(deferred.id, deferred_until=clock.now + 500)
    await _walk(first, expired.id, "working")
    await first.update(expired.id, deferred_until=clock.now - 1)
    await first.close()

    second = Ledger(path, clock=clock)
    try:
        statuses = {item.title: item.status for item in await second.select()}
    finally:
        await second.close()

    assert statuses == {
        "queued": "failed",
        "working": "failed",
        "waiting": "failed",
        # Can be taken up again, so it stays as it is.
        "blocked": "blocked",
        "done": "done",
        # The plugin may still call back before the deadline.
        "deferred": "working",
        # Past its deadline it is left to the task brain's sweep, which
        # reports the timeout the way it does while the host runs.
        "expired": "working",
    }


@pytest.mark.parametrize("leaving_to", ["cancelled", "working", "failed", "queued"])
async def test_an_undelivered_question_is_dropped_once_its_commission_stops_waiting(ledger, leaving_to):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "waiting_user")
    question = await ledger.outbox_add(commission.id, "question", {"question": "allow?"})
    result = await ledger.outbox_add(commission.id, "task_result", {"summary": "later"})

    # Stopped, answered on the board, failed or queued again: it waits no longer.
    assert (await ledger.transition(commission.id, leaving_to, reason="test")).applied

    pending = [item.id for item in await ledger.outbox_pending()]
    assert question not in pending
    # Other reports of it are untouched.
    assert result in pending


async def test_a_move_limited_to_some_statuses_is_refused_from_others(ledger):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "working")
    await ledger.transition(commission.id, "failed", reason="test")

    # failed -> working is allowed in general, but not when only waiting may move.
    moved = await ledger.transition(commission.id, "working", reason="answered", only_from=("waiting_user",))

    assert not moved.applied
    assert (await ledger.get(commission.id)).status == "failed"
    assert (await ledger.transition(commission.id, "working", reason="taken up")).applied


async def test_reopening_in_the_same_process_object_is_refused(ledger):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "working")

    # Its own rows are never mistaken for an earlier process's.
    assert (await ledger.get(commission.id)).status == "working"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_events_are_numbered_per_commission(ledger):
    first = await _file(ledger)
    second = await _file(ledger)

    assert await ledger.append_event(first.id, "note", {"n": 1}) == 2
    assert await ledger.append_event(second.id, "note", {"n": 1}) == 2
    await ledger.append_events(first.id, [("a", {"x": 1}), ("b", {"y": "é"})])

    events = await ledger.events(first.id)
    assert [(event.seq, event.type) for event in events] == [(1, "created"), (2, "note"), (3, "a"), (4, "b")]
    assert events[3].payload == {"y": "é"}
    assert [event.seq for event in await ledger.events(first.id, after=2)] == [3, 4]
    assert [event.seq for event in await ledger.events(first.id, limit=2)] == [1, 2]


async def test_failing_work_is_rolled_back(ledger):
    commission = await _file(ledger)

    def work(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE commissions SET title = 'changed' WHERE id = ?", (commission.id,))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await ledger.run(work)

    assert (await ledger.get(commission.id)).title == "weather"
    # And the ledger still works afterwards.
    assert await ledger.append_event(commission.id, "note") == 2


# ---------------------------------------------------------------------------
# Outbox
# ---------------------------------------------------------------------------


async def test_report_is_stored_before_anything_is_sent(ledger):
    commission = await _file(ledger)

    item_id = await ledger.outbox_add(commission.id, "task_result", {"summary": "done"})

    (item,) = await ledger.outbox_pending()
    assert (item.id, item.kind, item.payload, item.attempts, item.sent_at) == (
        item_id,
        "task_result",
        {"summary": "done"},
        0,
        None,
    )


async def test_sender_delivers_and_marks_sent(ledger):
    delivered: list[str] = []

    async def send(item: Any) -> bool:
        delivered.append(item.payload["summary"])
        return True

    sender = OutboxSender(ledger, send, clock=ledger.clock)
    await ledger.outbox_add(None, "task_result", {"summary": "one"})
    await ledger.outbox_add(None, "task_result", {"summary": "two"})

    assert await sender.flush() == 2
    assert delivered == ["one", "two"]
    assert await ledger.outbox_pending() == []
    assert await sender.flush() == 0


@pytest.mark.parametrize("failure", ["returns False", "raises"])
async def test_failed_send_is_kept_and_retried_with_growing_delay(ledger, failure):
    attempts: list[float] = []
    working = False

    async def send(item: Any) -> bool:
        attempts.append(ledger.clock.now)
        if working:
            return True
        if failure == "raises":
            raise ConnectionError("main server unreachable")
        return False

    sender = OutboxSender(ledger, send, clock=ledger.clock)
    await ledger.outbox_add(None, "task_result", {"summary": "one"})

    assert await sender.flush() == 0
    (item,) = await ledger.outbox_pending()
    assert item.attempts == 1 and item.next_attempt_at == ledger.clock.now + 1.0
    assert item.last_error
    # Not due yet: nothing is attempted.
    assert await sender.flush() == 0
    assert len(attempts) == 1

    ledger.clock.now += 1.0
    assert await sender.flush() == 0
    (item,) = await ledger.outbox_pending()
    assert item.attempts == 2 and item.next_attempt_at == ledger.clock.now + 2.0

    ledger.clock.now += 2.0
    working = True
    assert await sender.flush() == 1
    assert await ledger.outbox_pending() == []
    assert len(attempts) == 3


def test_retry_delay_doubles_up_to_five_minutes():
    assert [retry_delay(n) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert retry_delay(20) == 300.0
    assert retry_delay(0) == 1.0


async def test_without_a_sender_reports_wait_in_the_table(ledger):
    sender = OutboxSender(ledger, None, clock=ledger.clock)
    await ledger.outbox_add(None, "task_result", {"summary": "one"})

    sender.kick()
    assert await sender.flush() == 0

    assert len(await ledger.outbox_pending()) == 1
    assert sender._task is None


async def test_kick_sends_in_the_background_and_attach_starts_it(ledger):
    delivered = asyncio.Event()

    async def send(item: Any) -> bool:
        delivered.set()
        return True

    sender = OutboxSender(ledger, None)
    await ledger.outbox_add(None, "task_result", {"summary": "one"})
    sender.kick()
    assert not delivered.is_set()

    sender.attach(send)
    await asyncio.wait_for(delivered.wait(), 5)
    for _ in range(200):
        if not await ledger.outbox_pending():
            break
        await asyncio.sleep(0.01)
    await sender.close()

    assert await ledger.outbox_pending() == []


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


async def test_reporter_writes_the_fields_of_a_task_result_within_their_budgets(ledger):
    from config import TASK_ERROR_MAX_TOKENS, TASK_LARGE_DETAIL_MAX_TOKENS, TASK_SUMMARY_MAX_TOKENS
    from nekoclaw.host.reports import Reporter
    from utils.tokenize import count_tokens

    commission = await _file(ledger)
    long = "word " * 5000

    await Reporter(ledger).task_result(
        commission,
        channel="user_plugin",
        success=False,
        summary=long,
        detail=long,
        error_message=long,
        status="failed",
        source_kind="plugin",
        source_name="weather",
    )

    (item,) = await ledger.outbox_pending()
    payload = item.payload
    assert (item.kind, item.commission_id) == ("task_result", commission.id)
    assert count_tokens(payload["summary"]) <= TASK_SUMMARY_MAX_TOKENS
    assert count_tokens(payload["detail"]) <= TASK_LARGE_DETAIL_MAX_TOKENS
    assert count_tokens(payload["error_message"]) <= TASK_ERROR_MAX_TOKENS
    assert payload["text"] == payload["summary"]
    assert (payload["task_id"], payload["lanlan_name"], payload["channel"]) == (commission.id, "Lan", "user_plugin")
    assert (payload["delivery_mode"], payload["result_kind"], payload["expires_in_s"]) == ("proactive", "task_result", None)


async def test_reporter_stores_nothing_for_a_silent_result_or_a_child_task_agent(ledger):
    from nekoclaw.host.reports import Reporter

    parent = await _file(ledger)
    child = await _file(ledger, title="part one", parent_id=parent.id, source="task_agent")
    reporter = Reporter(ledger)

    silent = await reporter.task_result(
        parent, channel="user_plugin", success=True, summary="ok", status="completed", delivery_mode="silent"
    )
    from_child = await reporter.task_result(child, channel="task_agent", success=True, summary="ok", status="completed")
    correction = await reporter.correction(child, reason="no_capability")

    assert (silent, from_child, correction) == (None, None, None)
    assert await ledger.outbox_pending() == []
    # A question from a child task agent still has to reach the user.
    assert await reporter.question(child, {"kind": "questions", "questions": []}) is not None
    assert [item.kind for item in await ledger.outbox_pending()] == ["question"]


# ---------------------------------------------------------------------------
# Listeners and the report of what an earlier process left unfinished
# ---------------------------------------------------------------------------


async def test_listener_is_told_of_filings_and_status_changes_after_they_are_stored(ledger):
    seen: list[tuple[str, str, Any]] = []

    def listener(commission: Any, previous: Any) -> None:
        seen.append((commission.id, commission.status, previous))

    ledger.subscribe(listener)
    commission = await _file(ledger)
    await ledger.transition(commission.id, "working")
    await ledger.transition(commission.id, "done")

    assert seen == [
        (commission.id, "queued", None),
        (commission.id, "working", "queued"),
        (commission.id, "done", "working"),
    ]


async def test_listener_is_not_told_of_a_refused_move_or_of_a_filing_that_did_not_happen(ledger):
    commission = await _file(ledger)
    await _walk(ledger, commission.id, "cancelled")
    seen: list[Any] = []
    ledger.subscribe(lambda commission, previous: seen.append((commission.status, previous)))

    refused = await ledger.transition(commission.id, "working")
    same, created = await ledger.create(
        unless=lambda active: commission,
        lanlan_name="Lan",
        title="weather",
        dispatch_text="check the weather",
        source="dispatch",
    )

    assert not refused.applied and not created and same.id == commission.id
    assert seen == []


async def test_a_listener_that_raises_does_not_undo_the_change_or_silence_the_others(ledger):
    seen: list[str] = []

    def broken(commission: Any, previous: Any) -> None:
        raise RuntimeError("listener bug")

    ledger.subscribe(broken)
    ledger.subscribe(lambda commission, previous: seen.append(commission.status))

    commission = await _file(ledger)
    moved = await ledger.transition(commission.id, "working")

    assert moved.applied
    assert (await ledger.get(commission.id)).status == "working"
    assert seen == ["queued", "working"]


async def test_unsubscribing_stops_the_calls(ledger):
    seen: list[str] = []
    unsubscribe = ledger.subscribe(lambda commission, previous: seen.append(commission.status))
    commission = await _file(ledger)

    unsubscribe()
    unsubscribe()
    await ledger.transition(commission.id, "working")

    assert seen == ["queued"]


async def test_what_an_earlier_process_left_unfinished_is_reported_with_its_failure(tmp_path):
    path = tmp_path / "nekoclaw" / "ledger.db"
    first = Ledger(path)
    working = await _file(first, title="resize the photos")
    await first.transition(working.id, "working")
    blocked = await _file(first, title="book a table")
    await _walk(first, blocked.id, "blocked")
    await first.close()

    asked: list[str] = []

    def report(commission: Any) -> Any:
        asked.append(commission.id)
        return "task_result", {"task_id": commission.id, "status": commission.status, "delivery_mode": "passive"}

    second = Ledger(path, reconcile_report=report)
    try:
        rows = await second.outbox_pending()
        assert asked == [working.id]
        assert [(row.commission_id, row.kind, row.payload) for row in rows] == [
            (working.id, "task_result", {"task_id": working.id, "status": "failed", "delivery_mode": "passive"})
        ]
        assert (await second.get(working.id)).status == "failed"
        assert (await second.get(blocked.id)).status == "blocked"
    finally:
        await second.close()


async def test_no_report_is_stored_when_the_hook_gives_none(tmp_path):
    path = tmp_path / "nekoclaw" / "ledger.db"
    first = Ledger(path)
    commission = await _file(first)
    await first.transition(commission.id, "working")
    await first.close()

    second = Ledger(path, reconcile_report=lambda commission: None)
    try:
        assert await second.outbox_pending() == []
        assert (await second.get(commission.id)).status == "failed"
    finally:
        await second.close()


# ---------------------------------------------------------------------------
# Columns added after the first schema
# ---------------------------------------------------------------------------


_FIRST_SCHEMA_COLUMNS = (
    "id TEXT PRIMARY KEY, lanlan_name TEXT NOT NULL, title TEXT NOT NULL, dispatch_text TEXT NOT NULL, "
    "user_text TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, route TEXT, project TEXT, workdir TEXT, "
    "status TEXT NOT NULL, status_reason TEXT NOT NULL DEFAULT '', runtime TEXT, created_at REAL NOT NULL, "
    "updated_at REAL NOT NULL, started_at REAL, ended_at REAL, result_summary TEXT, pending_question TEXT, "
    "delivery TEXT, parent_id TEXT, conversation_id TEXT, lang TEXT, plugin_id TEXT, entry_id TEXT, name TEXT, "
    "deferred_until REAL, host_instance TEXT"
)


async def test_a_ledger_made_before_a_column_was_added_is_given_it_when_it_is_opened(tmp_path):
    from nekoclaw.host.ledger import Ledger

    path = tmp_path / "ledger.db"
    # As a build from before the session directory was recorded left it.
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE commissions ({_FIRST_SCHEMA_COLUMNS})")
    conn.execute(
        "INSERT INTO commissions (id, lanlan_name, title, dispatch_text, source, status, created_at, updated_at, workdir) "
        "VALUES ('c1', 'Lan', 'sort the photos', 'sort the photos', 'dispatch', 'blocked', 1.0, 2.0, 'D:/old/work')"
    )
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()

    ledger = Ledger(path)
    try:
        await ledger.open()
        kept = await ledger.get("c1")
        # What it held is all there, and the new column reads as nothing known.
        assert (kept.title, kept.status, kept.workdir, kept.session_dir) == ("sort the photos", "blocked", "D:/old/work", None)
        updated = await ledger.update("c1", session_dir="D:/old/sessions/c1")
        assert updated.session_dir == "D:/old/sessions/c1"
    finally:
        await ledger.close()

    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(commissions)")]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert columns.count("session_dir") == 1 and version == 6
    # It has every column a new ledger has. A column put into the table and the commission but left out of
    # ``_ADDED_COLUMNS`` shows here: the ledgers there are could then no longer be read.
    from nekoclaw.host.ledger import _COMMISSION_COLUMNS

    assert set(columns) == set(_COMMISSION_COLUMNS)

    # Opened again, nothing is added a second time.
    again = Ledger(path)
    try:
        await again.open()
        assert (await again.get("c1")).session_dir == "D:/old/sessions/c1"
    finally:
        await again.close()


async def test_a_new_ledger_has_the_added_columns_from_the_start(ledger):
    from nekoclaw.host.ledger import _ADDED_COLUMNS, _COMMISSION_COLUMNS

    await ledger.open()
    with sqlite3.connect(ledger.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commissions)")}
    conn.close()
    # Every field of a commission is a column, the ones added later included.
    assert set(_COMMISSION_COLUMNS) == columns
    assert {name for name, _kind in _ADDED_COLUMNS} <= columns


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_a_session_is_a_row_of_its_own_with_a_name_no_other_has(ledger):
    first = await ledger.create_session(lanlan_name="Lan", name="tidy the desk", workdir="D:/work")
    second = await ledger.create_session(lanlan_name="Lan", name="tidy the desk")
    third = await ledger.create_session(lanlan_name="Lan", name="tidy the desk")
    other = await ledger.create_session(lanlan_name="Mio", name="tidy the desk")

    assert (first.kind, first.status, first.workdir) == ("single", "closed", "D:/work")
    assert (first.created_at, first.last_active_at) == (1000.0, 1000.0)
    # A name that is taken gets a number; another character's names are its own.
    assert [first.name, second.name, third.name, other.name] == [
        "tidy the desk",
        "tidy the desk-2",
        "tidy the desk-3",
        "tidy the desk",
    ]
    assert len({first.id, second.id, third.id, other.id}) == 4
    assert await ledger.get_session(first.id) == first
    assert await ledger.get_session("nowhere") is None and await ledger.get_session(None) is None
    # A name that says nothing is still a name.
    assert (await ledger.create_session(lanlan_name="Lan", name="   ")).name == "session"


@pytest.mark.parametrize(
    "values",
    [{"status": "asleep"}, {"kind": "team"}, {"lanlan_name": ""}, {"colour": "red"}],
)
async def test_a_session_that_makes_no_sense_is_not_made(ledger, values):
    fields = {"lanlan_name": "Lan", "name": "x", **values}
    with pytest.raises(ValueError):
        await ledger.create_session(**fields)


async def test_updating_a_session_writes_what_may_change_and_nothing_else(ledger):
    session = await ledger.create_session(lanlan_name="Lan", name="a")
    await ledger.create_session(lanlan_name="Lan", name="b")
    ledger.clock.now = 2000.0

    updated = await ledger.update_session(session.id, status="running", runtime="builtin", session_dir="D:/s", active=True)
    assert (updated.status, updated.runtime, updated.session_dir, updated.last_active_at) == (
        "running",
        "builtin",
        "D:/s",
        2000.0,
    )
    # Without ``active`` the time stays.
    ledger.clock.now = 3000.0
    assert (await ledger.update_session(session.id, status="closed")).last_active_at == 2000.0
    # Renamed to a name that is taken, it gets a number; to its own name, nothing changes.
    assert (await ledger.update_session(session.id, name="b")).name == "b-2"
    assert (await ledger.update_session(session.id, name="b-2")).name == "b-2"

    with pytest.raises(ValueError):
        await ledger.update_session(session.id, status="asleep")
    for fixed in ("id", "lanlan_name", "kind", "created_at"):
        with pytest.raises(ValueError):
            await ledger.update_session(session.id, **{fixed: "x"})
    assert await ledger.update_session("nowhere", status="closed") is None


async def test_a_project_has_one_session_for_each_character(ledger):
    first, made = await ledger.project_session("Lan", "ledger app", workdir="D:/apps/ledger")
    again, made_again = await ledger.project_session("Lan", "ledger app", workdir="D:/elsewhere")
    other, made_other = await ledger.project_session("Mio", "ledger app")
    another, _ = await ledger.project_session("Lan", "blog")

    assert (made, made_again, made_other) == (True, False, True)
    assert again == first and other.id != first.id and another.id != first.id
    assert (first.kind, first.project, first.name, first.workdir) == ("project", "ledger app", "ledger app", "D:/apps/ledger")
    # A session of the same project that is not the project's own is never taken for it.
    single = await ledger.create_session(lanlan_name="Lan", name="one thing", project="blog")
    assert (await ledger.project_session("Lan", "blog"))[0].id == another.id != single.id


async def test_two_commissions_of_a_project_handed_over_together_get_one_session(ledger):
    found = await asyncio.gather(*(ledger.project_session("Lan", "ledger app") for _ in range(8)))
    assert len({session.id for session, _made in found}) == 1
    assert [made for _session, made in found].count(True) == 1


async def test_a_session_is_as_old_as_the_last_change_of_a_commission_in_it(ledger):
    session = await ledger.create_session(lanlan_name="Lan", name="a")
    other = await ledger.create_session(lanlan_name="Lan", name="b")
    commission = await _file(ledger, route="task_agent", session_id=session.id)
    loose = await _file(ledger, title="no session")

    ledger.clock.now = 2000.0
    assert (await ledger.transition(commission.id, "working")).applied
    assert (await ledger.transition(loose.id, "working")).applied
    assert (await ledger.get_session(session.id)).last_active_at == 2000.0
    assert (await ledger.get_session(other.id)).last_active_at == 1000.0

    # A refused move is not a change.
    ledger.clock.now = 3000.0
    assert not (await ledger.transition(commission.id, "queued", only_from=("done",))).applied
    assert (await ledger.get_session(session.id)).last_active_at == 2000.0

    # A commission given its session with the move counts for that session.
    late = await _file(ledger, title="late")
    assert (await ledger.transition(late.id, "working", session_id=other.id)).applied
    assert (await ledger.get_session(other.id)).last_active_at == 3000.0


async def test_where_a_session_stands_is_written_by_what_its_commission_is_then(ledger):
    session = await ledger.create_session(lanlan_name="Lan", name="a")
    commission = await _file(ledger, route="task_agent", session_id=session.id)

    async def stands() -> str:
        return (await ledger.get_session(session.id)).status

    # At work, which was written with the commission's status: a look that began before it
    # was taken up again (the user answered) writes nothing over that.
    assert (await ledger.transition(commission.id, "working")).applied
    await ledger.settle_session(session.id, commission.id)
    assert await stands() == "running"

    # It waits for the user, its runtime session closed.
    assert (await ledger.transition(commission.id, "waiting_user")).applied
    await ledger.update_session(session.id, status="running")
    await ledger.settle_session(session.id, commission.id)
    assert await stands() == "waiting"

    # Queued to be taken up again: not written over either.
    assert (await ledger.transition(commission.id, "queued")).applied
    await ledger.update_session(session.id, status="running")
    await ledger.settle_session(session.id, commission.id)
    assert await stands() == "running"

    # Over.
    assert (await ledger.transition(commission.id, "working")).applied
    assert (await ledger.transition(commission.id, "done")).applied
    await ledger.settle_session(session.id, commission.id)
    assert await stands() == "closed"

    # What was said in it was removed since, being old, and the next commission of it failed
    # before a runtime had the session open: nothing is in it, which is what it says.
    await ledger.mark_session_cleared(session.id)
    late = await _file(ledger, title="late", route="task_agent", session_id=session.id)
    assert (await ledger.transition(late.id, "failed")).applied
    await ledger.settle_session(session.id, late.id)
    assert await stands() == "cleared"
    # A commission nobody knows of is over as well.
    await ledger.update_session(session.id, status="running")
    await ledger.settle_session(session.id, "no-such-commission")
    assert await stands() == "closed"


async def test_commissions_are_found_by_their_session(ledger):
    session = await ledger.create_session(lanlan_name="Lan", name="a")
    first = await _file(ledger, title="first", session_id=session.id)
    second = await _file(ledger, title="second", session_id=session.id, continues=first.id)
    await _file(ledger, title="elsewhere")

    found = await ledger.select(session_id=session.id, oldest_first=True)
    assert [item.id for item in found] == [first.id, second.id]
    assert found[1].continues == first.id and found[0].continues is None
    assert await ledger.select(session_id="nowhere") == []


async def test_a_commission_is_given_a_session_once_and_a_session_made_for_it_has_its_id(ledger):
    first = await _file(ledger, title="sort the photos", route="task_agent")

    given, session = await ledger.give_session(first.id, kind="single", name="sort the photos")
    again, same = await ledger.give_session(first.id, kind="single", name="another name")

    # The session a commission opens has the commission's id, as every session had.
    assert (session.id, session.kind, session.name, session.lanlan_name) == (first.id, "single", "sort the photos", "Lan")
    assert given.session_id == first.id and again.session_id == first.id and same == session
    # A task agent started by another: a session of its own, whose parent is that one's.
    child = await _file(ledger, title="count the files", route="task_agent", parent_id=first.id, name="agent-1")
    _given, child_session = await ledger.give_session(child.id, kind="child", name="sort the photos/agent-1", parent_id=session.id)
    assert (child_session.id, child_session.kind, child_session.parent_id) == (child.id, "child", first.id)

    # A commission that was filed into a session another one opened keeps that session:
    # none is made under its own id.
    follow = await _file(ledger, title="also rename them", route="task_agent", session_id=first.id, continues=first.id)
    joined, kept = await ledger.give_session(follow.id, kind="single", name="also rename them")
    assert kept == session and joined.session_id == first.id
    assert await ledger.get_session(follow.id) is None

    with pytest.raises(ValueError):
        await ledger.give_session("no-such-commission", kind="single", name="x")
    with pytest.raises(ValueError):
        await ledger.give_session(first.id, kind="team", name="x")


async def test_the_commissions_of_a_project_are_given_the_one_session_the_first_of_them_opened(ledger):
    first = await _file(ledger, title="add an export", route="task_agent", project="ledger app")
    second = await _file(ledger, title="add a chart", route="task_agent", project="ledger app")
    other = await _file(ledger, title="add a chart", route="task_agent", project="ledger app", lanlan_name="Mio")
    loose = await _file(ledger, title="tidy the desk", route="task_agent")

    opened, session = await ledger.give_session(first.id, kind="project", name="ledger app")
    joined, same = await ledger.give_session(second.id, kind="project", name="ledger app")
    _mio, another = await ledger.give_session(other.id, kind="project", name="ledger app")
    # A commission without a project gets a session of its own whatever kind was asked for.
    _loose, own = await ledger.give_session(loose.id, kind="project", name="tidy the desk")

    assert (session.id, session.kind, session.project) == (first.id, "project", "ledger app")
    assert same == session and (opened.session_id, joined.session_id) == (first.id, first.id)
    # Another character's commission of the project opens that character's session of it.
    assert (another.id, another.lanlan_name) == (other.id, "Mio")
    assert own.id == loose.id
    assert [item.id for item in await ledger.select(session_id=first.id, oldest_first=True)] == [first.id, second.id]
    # Handed over at the same moment, the commissions of a project still get one session.
    more = [await _file(ledger, title=f"task {n}", route="task_agent", project="blog") for n in range(6)]
    given = await asyncio.gather(*(ledger.give_session(item.id, kind="project", name="blog") for item in more))
    assert len({session.id for _commission, session in given}) == 1


async def test_a_commission_that_lost_its_session_id_finds_the_session_of_its_id_again(ledger):
    commission = await _file(ledger, title="sort the photos", route="task_agent")
    _given, session = await ledger.give_session(commission.id, kind="single", name="sort the photos")
    await ledger.update(commission.id, session_id=None)

    given, found = await ledger.give_session(commission.id, kind="single", name="sort the photos")

    assert found == session and given.session_id == commission.id


async def test_an_event_of_a_kind_is_looked_for_in_one_commission(ledger):
    first = await _file(ledger, title="first")
    second = await _file(ledger, title="second")
    await ledger.append_event(first.id, "opened", {})

    assert await ledger.has_event(first.id, "opened") is True
    assert await ledger.has_event(first.id, "closed") is False
    assert await ledger.has_event(second.id, "opened") is False


async def test_her_commissions_that_ended_lately_are_chosen_and_ordered_before_they_are_counted(ledger):
    async def ended(title: str, status: str = "done", **values: Any) -> Any:
        values.setdefault("route", "task_agent")
        commission = await _file(ledger, title=title, **values)
        await ledger.update(commission.id, session_id=values.get("session_id", commission.id))
        await _walk(ledger, commission.id, status)
        return commission

    # Filed first, and long at work.
    long = await _file(ledger, title="reorganise the archive", route="task_agent")
    await ledger.update(long.id, session_id=long.id)
    await _walk(ledger, long.id, "working")
    ledger.clock.now += 10
    filed_later = [await ended(f"small job {index}") for index in range(4)]
    stopped = await ended("stopped one", "cancelled")
    failed = await ended("failed one", "failed")
    # None of these can be gone on with: a plugin's, a task agent's own, one without a session,
    # one that is stuck, another character's.
    await ended("by a plugin", route="plugin")
    child = await ended("a part", parent_id=long.id)
    bare = await _file(ledger, title="never given a session", route="task_agent")
    await _walk(ledger, bare.id, "done")
    await ended("stuck", "blocked")
    await ended("hers", lanlan_name="Mio")
    ledger.clock.now += 10
    assert (await ledger.transition(long.id, "done")).applied

    lately = await ledger.ended_lately("Lan", since=0.0, limit=3)

    # The one that ended last comes first, although six were filed after it; and the limit
    # counts what can be gone on with, not what was filed.
    assert [item.id for item in lately] == [long.id, failed.id, stopped.id]
    everything = await ledger.ended_lately("Lan", since=0.0, limit=50)
    assert [item.id for item in everything] == [long.id, failed.id, stopped.id, *[item.id for item in reversed(filed_later)]]
    assert child.id not in [item.id for item in everything]
    # Only what ended since.
    assert [item.id for item in await ledger.ended_lately("Lan", since=ledger.clock.now, limit=50)] == [long.id]
    assert await ledger.ended_lately("Nobody", since=0.0, limit=50) == []


async def test_a_commission_of_this_build_left_without_a_session_is_not_given_one_on_opening(tmp_path):
    path = tmp_path / "nekoclaw" / "ledger.db"
    ledger = Ledger(path)
    # As a process that ended between filing it for a task agent and giving it its session
    # leaves it; it belongs to a project, whose session it is to be worked on in.
    left = await _file(ledger, title="add a chart", route="task_agent", project="Budget app")
    await ledger.close()

    ledger = Ledger(path)
    try:
        again = await ledger.get(left.id)
        assert again.session_id is None and await ledger.get_session(left.id) is None
        # Given one when it is taken up: its project's.
        given, session = await ledger.give_session(left.id, kind="project", name="Budget app")
        assert (session.kind, session.project, given.session_id) == ("project", "Budget app", session.id)
    finally:
        await ledger.close()


async def test_the_commission_whose_opening_message_a_session_took_last(ledger):
    first = await _file(ledger, title="add a CSV export", session_id="s1")
    second = await _file(ledger, title="add a chart", session_id="s1")
    elsewhere = await _file(ledger, title="sort the photos", session_id="s2")
    assert await ledger.opened_last("s1") is None

    await ledger.append_event(first.id, "opened", {})
    await ledger.append_event(first.id, "started", {})
    assert await ledger.opened_last("s1") == first.id

    # The session goes on with the next one; what another session takes is none of its business,
    # and neither are the other events of a commission.
    await ledger.append_event(second.id, "opened", {})
    await ledger.append_event(elsewhere.id, "opened", {})
    await ledger.append_event(first.id, "tool_start", {})
    assert await ledger.opened_last("s1") == second.id
    assert await ledger.opened_last("s2") == elsewhere.id

    # Back at the first: opened a second time.
    await ledger.append_event(first.id, "opened", {})
    assert await ledger.opened_last("s1") == first.id
    assert await ledger.opened_last("no-such-session") is None


def _old_ledger(path: Any, rows: list[tuple[Any, ...]], events: list[tuple[Any, ...]] = ()) -> None:
    """Write a ledger as a build from before sessions left it: the tables of then, at version 2."""
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE commissions ({_FIRST_SCHEMA_COLUMNS}, session_dir TEXT)")
    conn.execute(
        "CREATE TABLE events (commission_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL, "
        "type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (commission_id, seq))"
    )
    conn.executemany(
        "INSERT INTO commissions (id, lanlan_name, title, dispatch_text, source, route, status, created_at, "
        "updated_at, project, workdir, session_dir, runtime, parent_id) VALUES (?, 'Lan', ?, ?, 'dispatch', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany("INSERT INTO events (commission_id, seq, ts, type, payload) VALUES (?, ?, ?, ?, '{}')", events)
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()


async def test_commissions_of_an_earlier_build_are_given_the_sessions_they_had(tmp_path):
    path = tmp_path / "ledger.db"
    _old_ledger(
        path,
        [
            # id, title, dispatch, route, status, created, updated, project, workdir, session_dir, runtime, parent
            ("a1", "sort photos", "sort photos", "task_agent", "done", 10.0, 20.0, "album", "D:/w/a1", "D:/s/a1", "builtin", None),
            ("a2", "part two", "part two", "task_agent", "blocked", 11.0, 21.0, "album", "D:/w/a1/part", None, "dsh", "a1"),
            ("a3", "old one", "old one", "task_agent", "failed", 12.0, 22.0, None, None, "D:/s/a3", "builtin", None),
            ("a4", "taken up", "taken up", "task_agent", "failed", 13.0, 40.0, None, None, "D:/s/a4", "builtin", None),
            ("p1", "weather", "weather", "plugin", "done", 14.0, 24.0, None, None, None, None, None),
            ("u1", "undecided", "undecided", None, "failed", 15.0, 25.0, None, None, None, None, None),
        ],
        # a3 was cleaned after it ended; a4 was cleaned and then taken up again.
        events=[("a3", 1, 30.0, "session_cleaned"), ("a4", 1, 30.0, "session_cleaned")],
    )

    ledger = Ledger(path)
    try:
        await ledger.open()
        first = await ledger.get_session("a1")
        child = await ledger.get_session("a2")
        cleaned = await ledger.get_session("a3")
        resumed = await ledger.get_session("a4")
        # The session has the commission's id and what the commission had on record: nothing on the disk moves.
        assert (first.kind, first.project, first.workdir, first.session_dir, first.runtime, first.status) == (
            "single",
            "album",
            "D:/w/a1",
            "D:/s/a1",
            "builtin",
            "closed",
        )
        assert (first.created_at, first.last_active_at, first.lanlan_name) == (10.0, 20.0, "Lan")
        assert (child.kind, child.parent_id, child.runtime) == ("child", "a1", "dsh")
        assert (cleaned.status, resumed.status) == ("cleared", "closed")
        for commission_id in ("a1", "a2", "a3", "a4"):
            assert (await ledger.get(commission_id)).session_id == commission_id
        # A commission no task agent ever had has no session.
        for commission_id in ("p1", "u1"):
            assert (await ledger.get(commission_id)).session_id is None
            assert await ledger.get_session(commission_id) is None
        # Not one of them is taken for the session of the project.
        project, made = await ledger.project_session("Lan", "album")
        assert made and project.id not in ("a1", "a2")
        names = [first.name, child.name, cleaned.name, resumed.name]
        assert len(set(names)) == 4 and first.name == "sort photos-a1"
    finally:
        await ledger.close()

    # Opened again, nothing is given a second time and what was changed since stays.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE sessions SET status = 'cleared', name = 'renamed' WHERE id = 'a1'")
        before = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 6
    again = Ledger(path)
    try:
        await again.open()
        kept = await again.get_session("a1")
        assert (kept.status, kept.name) == ("cleared", "renamed")
    finally:
        await again.close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before
    conn.close()


async def test_sessions_an_earlier_process_left_open_are_closed_on_open(tmp_path):
    path = tmp_path / "ledger.db"
    first = Ledger(path)
    try:
        sessions = {
            status: await first.create_session(lanlan_name="Lan", name=status, status=status)
            for status in ("idle", "running", "waiting", "closed", "cleared")
        }
    finally:
        await first.close()

    second = Ledger(path)
    try:
        found = {status: (await second.get_session(session.id)).status for status, session in sessions.items()}
        # What was said in them is on the disk; no runtime of this process has one open.
        assert found == {
            "idle": "closed",
            "running": "closed",
            "waiting": "closed",
            "closed": "closed",
            "cleared": "cleared",
        }
        # One this process opens stays as it says.
        mine = await second.create_session(lanlan_name="Lan", name="mine", status="running")
        assert (await second.get_session(mine.id)).status == "running"
    finally:
        await second.close()


# ---------------------------------------------------------------------------
# What model requests use, and sessions made from others
# ---------------------------------------------------------------------------


async def test_what_requests_use_is_added_up_with_the_session_and_up_the_line_of_commissions(ledger):
    parent = await _file(ledger, title="sort the photos")
    child = await _file(ledger, title="count", source="task_agent", parent_id=parent.id)
    other = await _file(ledger, title="tidy")
    session = await ledger.create_session(lanlan_name="Lan", name="photos")
    assert (session.requests, session.tokens, session.context_tokens) == (0, 0, 0)
    assert (parent.requests, parent.tokens, parent.usage_allowed) == (0, 0, None)

    await ledger.add_usage(
        session_id=session.id, commission_id=child.id, tokens_in=100, tokens_out=20, cache_read=300, cache_write=40,
        context_tokens=460,
    )
    await ledger.add_usage(session_id=session.id, commission_id=child.id, tokens_in=7, context_tokens=None)
    # For no commission: the session's alone. Two requests at once, and nothing below nought.
    await ledger.add_usage(session_id=session.id, requests=2, tokens_in=-5, tokens_out=1)
    # For no session: a commission's alone.
    await ledger.add_usage(commission_id=parent.id, tokens_out=3)

    session = await ledger.get_session(session.id)
    assert (session.requests, session.tokens_in, session.tokens_out, session.tokens_cache_read, session.tokens_cache_write) == (
        4, 107, 21, 300, 40,
    )
    # The length of the conversation is what the last request that told it said.
    assert (session.tokens, session.context_tokens) == (468, 460)
    child, parent, other = [await ledger.get(item.id) for item in (child, parent, other)]
    assert (child.requests, child.tokens) == (2, 467)
    # What a task agent it started used is the commission's own as well.
    assert (parent.requests, parent.tokens_in, parent.tokens_out, parent.tokens) == (3, 107, 23, 470)
    assert (other.requests, other.tokens) == (0, 0)
    # Nothing else of a commission changes with it, and nobody is told.
    assert parent.updated_at == other.updated_at and parent.status == "queued"

    # How much the work a commission belongs to has used and may: told of the one no task agent started.
    for asked in (child.id, parent.id):
        standing = await ledger.usage_standing(asked)
        assert (standing.root, standing.used, standing.allowed) == (parent.id, 470, None)
    assert await ledger.usage_standing("nobody") is None and await ledger.allow_usage("nobody", 5) is None
    # Allowed more: so much on top of what it has used by now, written with that one.
    allowed = await ledger.allow_usage(child.id, 1_000)
    assert (allowed.root, allowed.used, allowed.allowed) == (parent.id, 470, 1_470)
    assert (await ledger.get(parent.id)).usage_allowed == 1_470 and (await ledger.get(child.id)).usage_allowed is None
    events = [(event.type, event.payload) for event in await ledger.events(parent.id) if event.type == "usage_allowed"]
    assert events == [("usage_allowed", {"used": 470, "allowed": 1_470})]
    # Neither is written by an update of other columns.
    for values in ({"usage_allowed": 5}, {"requests": 9}, {"tokens_in": 9}):
        with pytest.raises(ValueError):
            await ledger.update(parent.id, **values)
    with pytest.raises(ValueError):
        await ledger.update_session(session.id, tokens_out=9)
    with pytest.raises(ValueError):
        await ledger.update_session(session.id, context_tokens=9)


async def test_a_session_made_from_another_names_it_and_only_a_closed_one_is_marked_as_being_summed_up(ledger):
    source = await ledger.create_session(lanlan_name="Lan", name="photos", kind="project", project="photos")
    copy = await ledger.create_session(
        lanlan_name="Lan", name="photos/fork", kind="fork", project="photos", forked_from=source.id, forked_at="e42"
    )
    again = await ledger.create_session(lanlan_name="Lan", name="photos/fork", kind="fork", forked_from=source.id)
    await ledger.create_session(lanlan_name="Lan", name="elsewhere", kind="fork", forked_from=copy.id)
    assert (copy.kind, copy.forked_from, copy.forked_at, copy.status) == ("fork", source.id, "e42", "closed")
    assert again.name == "photos/fork-2" and again.forked_at is None
    assert [item.id for item in await ledger.forks_of(source.id)] == [copy.id, again.id]
    assert await ledger.forks_of("nobody") == []
    # A copy is not taken for the session of the project it was copied from.
    found, made = await ledger.project_session("Lan", "photos")
    assert found.id == source.id and not made
    # Where a copy came from is said when it is made, and not changed afterwards.
    with pytest.raises(ValueError):
        await ledger.update_session(copy.id, forked_from="other")
    with pytest.raises(ValueError):
        await ledger.update_session(copy.id, forked_at="e1")

    # Only a session nothing has open is marked as being summed up, and only one so marked is closed again by that.
    assert await ledger.begin_compacting(source.id) is True
    assert (await ledger.get_session(source.id)).status == "compacting"
    assert await ledger.begin_compacting(source.id) is False
    for status in ("running", "waiting", "cleared"):
        await ledger.update_session(copy.id, status=status)
        assert await ledger.begin_compacting(copy.id) is False
        assert (await ledger.get_session(copy.id)).status == status
    assert await ledger.begin_compacting("nobody") is False
    # A commission got to work in it meanwhile: where it stands by that is not written over.
    await ledger.update_session(source.id, status="running")
    await ledger.end_compacting(source.id, compacted=False)
    kept = await ledger.get_session(source.id)
    assert (kept.status, kept.compacted_at) == ("running", None)
    await ledger.update_session(source.id, status="compacting")
    ledger.clock.now = 2_000.0
    await ledger.end_compacting(source.id, compacted=True)
    kept = await ledger.get_session(source.id)
    assert (kept.status, kept.compacted_at) == ("closed", 2_000.0)


async def test_a_session_left_as_being_summed_up_by_an_earlier_process_is_closed_on_opening(tmp_path):
    path = tmp_path / "ledger.db"
    first = Ledger(path)
    session = await first.create_session(lanlan_name="Lan", name="photos", kind="project", project="photos")
    assert await first.begin_compacting(session.id)
    await first.close()

    second = Ledger(path)
    try:
        assert (await second.get_session(session.id)).status == "closed"
    finally:
        await second.close()


async def test_a_ledger_with_sessions_of_the_build_before_gets_the_new_columns_and_keeps_its_rows(tmp_path):
    path = tmp_path / "ledger.db"
    first = Ledger(path)
    session = await first.create_session(lanlan_name="Lan", name="photos", kind="project", project="photos")
    commission = await _file(first, title="sort the photos")
    await first.update(commission.id, session_id=session.id)
    await first.close()
    # As the build before left it: sessions as rows of their own, and nothing of what requests use,
    # of where a copy was made, or of how much a commission may use.
    conn = sqlite3.connect(path)
    try:
        # Named here, not taken from the list the ledger adds columns from: a column that went
        # missing from that list would otherwise be missing from this as well, and not be missed.
        usage = ("requests", "tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write")
        for name in ("forked_at", *usage, "context_tokens"):
            conn.execute(f"ALTER TABLE sessions DROP COLUMN {name}")
        for name in (*usage, "usage_allowed"):
            conn.execute(f"ALTER TABLE commissions DROP COLUMN {name}")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        assert "requests" not in {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    finally:
        conn.close()

    second = Ledger(path)
    try:
        kept = await second.get_session(session.id)
        assert (kept.name, kept.kind, kept.project, kept.status) == ("photos", "project", "photos", "closed")
        assert (kept.requests, kept.tokens, kept.context_tokens, kept.forked_at) == (0, 0, 0, None)
        old = await second.get(commission.id)
        assert (old.session_id, old.requests, old.tokens, old.usage_allowed) == (session.id, 0, 0, None)
        # And is written like one that was made now.
        await second.add_usage(session_id=session.id, commission_id=commission.id, tokens_in=100, tokens_out=20, context_tokens=120)
        assert ((await second.get_session(session.id)).tokens, (await second.get(commission.id)).tokens) == (120, 120)
        assert (await second.allow_usage(commission.id, 1_000)).allowed == 1_120
        copy = await second.create_session(
            lanlan_name="Lan", name="photos/fork", kind="fork", project="photos", forked_from=session.id, forked_at="e7"
        )
        assert [(item.id, item.forked_at) for item in await second.forks_of(session.id)] == [(copy.id, "e7")]
        assert await second.begin_compacting(session.id) is True
        await second.end_compacting(session.id, compacted=True)
        assert (await second.get_session(session.id)).compacted_at is not None
    finally:
        await second.close()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        conn.close()
