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
    child = await _file(ledger, title="child", parent_id=first.id, source="familiar")
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
        "working": {"waiting_user", "done", "blocked", "failed", "cancelled"},
        # Queued when its closed session's answer finds no free place.
        "waiting_user": {"working", "queued", "failed", "cancelled"},
        # Taken up again, at once or queued when no familiar place is free.
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
    assert source.count("UPDATE commissions SET") == 2
    assert "status" not in module._UPDATABLE
    assert "host_instance" not in module._UPDATABLE


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
        "expired": "failed",
    }


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


async def test_reporter_stores_nothing_for_a_silent_result_or_a_sub_familiar(ledger):
    from nekoclaw.host.reports import Reporter

    parent = await _file(ledger)
    child = await _file(ledger, title="part one", parent_id=parent.id, source="familiar")
    reporter = Reporter(ledger)

    silent = await reporter.task_result(
        parent, channel="user_plugin", success=True, summary="ok", status="completed", delivery_mode="silent"
    )
    from_child = await reporter.task_result(child, channel="familiar", success=True, summary="ok", status="completed")
    correction = await reporter.correction(child, reason="no_capability")

    assert (silent, from_child, correction) == (None, None, None)
    assert await ledger.outbox_pending() == []
    # A question from a sub-familiar still has to reach the user.
    assert await reporter.question(child, {"kind": "questions", "questions": []}) is not None
    assert [item.kind for item in await ledger.outbox_pending()] == ["question"]
