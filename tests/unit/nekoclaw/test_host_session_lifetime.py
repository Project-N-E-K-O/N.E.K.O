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

"""How long a session of one commission lives.

A session of kind ``single`` ends on the eighth day after the last commission
in it ended: nothing is written to it, asked of it, woken in it or taken up
again in it any more, and it is not summed up. Everything that would give a
session new work asks ``Ledger.session_alive``; these tests go through each of
them. A session of a project does not end this way.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import config.nekoclaw_settings as settings
from config.prompts import prompts_nekoclaw_sessions as words
from nekoclaw.host.ledger import Ledger
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
    wait_until,
)

DAY = 86400.0
LIFETIME = settings.SESSION_SINGLE_LIFETIME_DAYS * DAY


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
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


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


@pytest.fixture(autouse=True)
def _quick(monkeypatch: Any) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WAKE_COALESCE_SECONDS", 0.0)
    monkeypatch.setattr("config.nekoclaw_settings.WAKE_MIN_DELAY_SECONDS", 0.05)
    monkeypatch.setattr("config.nekoclaw_settings.WAKE_HELD_RETRY_SECONDS", 0.05)
    monkeypatch.setattr("config.nekoclaw_settings.SESSION_MESSAGE_TARGET_INTERVAL_SECONDS", 0.0)


def _host(hosts: list[Any], tmp_path: Path, llm: Any, **kwargs: Any) -> Any:
    pick = llm if callable(llm) else (lambda spec: llm)
    kwargs.setdefault("task_agent_model", fake_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    return await host.dispatch(text, **kwargs)


def _report(status: str = "ready", summary: str = "done") -> list[Any]:
    return [call("report", {"status": status, "summary": summary}), finish("tool_calls")]


def _gate() -> tuple[asyncio.Event, Any]:
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, wait


def _k(text: str) -> str:
    """The key of the script of a commission she dispatched with ``text``: found in what opens the commission only."""
    return f"What the user said: {text}"


async def _let_go(host: Any) -> None:
    await wait_until(lambda: host.task_agents._held_by == {}, what="every session being let go")


def _days_later(host: Any, monkeypatch: Any, days: float) -> None:
    """Let days pass for the ledger and everything that reads its clock."""
    real = host.ledger._clock
    monkeypatch.setattr(host.ledger, "_clock", lambda: real() + days * DAY)
    # The rounds of wake requests took the clock when they were made, and are not asked what it is now.
    monkeypatch.setattr(host.wakes, "_clock", host.ledger._clock)


async def _filed(ledger: Ledger, kind: str, **values: Any) -> Any:
    fields = {"lanlan_name": "Lan", "title": "weather", "dispatch_text": "check the weather", "source": "dispatch"}
    fields.update(values)
    commission, created = await ledger.create(route="task_agent", **fields)
    assert created
    commission, session = await ledger.give_session(commission.id, kind=kind, name=commission.title)
    return commission, session


# ---------------------------------------------------------------------------
# The ledger says whether a session is gone on with
# ---------------------------------------------------------------------------


async def test_a_session_of_one_commission_ends_when_its_last_commission_has_been_over_for_seven_days(ledger):
    assert settings.SESSION_SINGLE_LIFETIME_DAYS == 7
    commission, session = await _filed(ledger, "single")
    await ledger.transition(commission.id, "working")
    ledger.clock.now += 60.0
    await ledger.transition(commission.id, "done")
    ended_at = ledger.clock.now
    session = await ledger.get_session(session.id)
    assert session.last_active_at == ended_at

    # Counted from the moment the last commission ended: the seventh day is gone on with, the eighth is not.
    assert await ledger.session_alive(session)
    ledger.clock.now = ended_at + LIFETIME - 1.0
    assert await ledger.session_alive(session)
    ledger.clock.now = ended_at + LIFETIME
    assert not await ledger.session_alive(session)
    # Asked about another moment, the answer is for that one.
    assert await ledger.session_alive(session, now=ended_at + LIFETIME - 1.0)
    assert not await ledger.session_alive(session, now=ended_at + 100 * DAY)
    # No session is not alive.
    assert not await ledger.session_alive(None)


@pytest.mark.parametrize("kind", ["project", "child", "fork"])
async def test_only_a_session_of_one_commission_ends_by_its_age(ledger, kind):
    commission, session = await _filed(ledger, kind)
    await ledger.transition(commission.id, "working")
    await ledger.transition(commission.id, "done")
    ledger.clock.now += 365 * DAY
    assert await ledger.session_alive(await ledger.get_session(session.id))


@pytest.mark.parametrize("stands", ["queued", "working", "waiting_user", "resting"])
async def test_a_session_with_a_commission_under_way_does_not_end(ledger, stands):
    commission, session = await _filed(ledger, "single")
    if stands != "queued":
        await ledger.transition(commission.id, "working")
    if stands == "waiting_user":
        await ledger.transition(commission.id, "waiting_user")
    if stands == "resting":
        wake, _refused = await ledger.wake_add(
            session_id=session.id,
            commission_id=commission.id,
            note="look again",
            due_at=ledger.clock.now + 30 * DAY,
            pending_max=5,
            daily_max=5,
        )
        assert wake is not None and await ledger.rest(commission.id) is not None
        assert (await ledger.get(commission.id)).resting
    session = await ledger.get_session(session.id)
    ledger.clock.now += 30 * DAY
    assert await ledger.session_alive(session)


@pytest.mark.parametrize("ends", ["done", "failed", "blocked", "cancelled"])
async def test_a_commission_that_is_over_or_stuck_does_not_keep_its_session_alive(ledger, ends):
    commission, session = await _filed(ledger, "single")
    await ledger.transition(commission.id, "working")
    await ledger.transition(commission.id, ends)
    session = await ledger.get_session(session.id)
    ledger.clock.now += LIFETIME + 1.0
    assert not await ledger.session_alive(session)


async def test_a_session_that_looks_old_is_looked_at_again_with_what_is_under_way_in_it(ledger):
    commission, session = await _filed(ledger, "single")
    await ledger.transition(commission.id, "working")
    await ledger.transition(commission.id, "done")
    stale = await ledger.get_session(session.id)
    ledger.clock.now += LIFETIME + 1.0
    assert not await ledger.session_alive(stale)
    # Another commission is given the session, and goes to work, after whoever asks read it.
    again, _created = await ledger.create(
        lanlan_name="Lan", title="again", dispatch_text="again", source="dispatch", route="task_agent"
    )
    await ledger.update(again.id, session_id=session.id)
    await ledger.transition(again.id, "working")
    assert await ledger.session_alive(stale)


async def test_a_summary_made_leaves_the_length_of_the_conversation_unknown_until_a_request_says(ledger):
    commission, session = await _filed(ledger, "project", title="Budget app")
    await ledger.transition(commission.id, "working")
    await ledger.transition(commission.id, "done")
    await ledger.add_usage(session_id=session.id, context_tokens=150_000)
    await ledger.settle_session(session.id, commission.id)
    # Not summed up: it stays as long as it was.
    assert await ledger.begin_compacting(session.id)
    await ledger.end_compacting(session.id, compacted=False)
    kept = await ledger.get_session(session.id)
    assert (kept.status, kept.compacted_at, kept.context_tokens) == ("closed", None, 150_000)
    # Summed up: how long it is is not known until the next request says.
    assert await ledger.begin_compacting(session.id)
    ledger.clock.now += 10.0
    await ledger.end_compacting(session.id, compacted=True)
    kept = await ledger.get_session(session.id)
    assert (kept.status, kept.compacted_at, kept.context_tokens) == ("closed", ledger.clock.now, 0)


# ---------------------------------------------------------------------------
# Everything that would give a session new work asks
# ---------------------------------------------------------------------------


async def _two_sessions(hosts, tmp_path, monkeypatch, *, days: float = 8.0):
    """A session of one commission and one of a project, both over; ``days`` pass; and a commission at work elsewhere."""
    release, wait = _gate()
    scripts = {
        _k("paint the fence"): ScriptedLLM([_report("ready", "painted")]),
        _k("sort the budget"): ScriptedLLM([_report("ready", "sorted")]),
        _k("build the site"): ScriptedLLM([[wait, *_report("ready", "built")]]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    single = await wait_status(host, (await _dispatch(host, "paint the fence")).commission_id, "done")
    project = await wait_status(host, (await _dispatch(host, "sort the budget in the budget app")).commission_id, "done")
    await _let_go(host)
    working = await _dispatch(host, "build the site")
    await wait_until(lambda: len(scripts[_k("build the site")].requests) == 1, what="the run under way")
    sender = await host.ledger.get(working.commission_id)
    assert (await host.ledger.get_session(single.session_id)).kind == "single"
    assert (await host.ledger.get_session(project.session_id)).kind == "project"
    _days_later(host, monkeypatch, days)
    return host, single, project, sender, release


async def test_nothing_is_written_to_a_session_that_has_ended_and_what_it_left_can_be_read(hosts, tmp_path, monkeypatch):
    host, single, project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch)
    ended_name = (await host.ledger.get_session(single.session_id)).name
    send = host.task_agents._cb_session_send

    with pytest.raises(RuntimeError) as refused:
        await send(sender.session_id, {"to": ended_name, "message": "paint it again"})
    assert str(refused.value) == words.MESSAGE_REFUSALS["ended"].format(
        name=ended_name, days=settings.SESSION_SINGLE_LIFETIME_DAYS
    )
    assert "7 days" in str(refused.value) and "read_session" in str(refused.value)
    # Nothing was written down, and nobody was woken.
    assert await host.ledger.wakes_pending(session_id=single.session_id) == []
    # The same age in a project does not end it: it is written to.
    project_name = (await host.ledger.get_session(project.session_id)).name
    answer = await send(sender.session_id, {"to": project_name, "message": "sort it again"})
    assert answer["message"].startswith(f'Queued for "{project_name}"')
    # And what the ended one left is read all the same.
    read = await host.task_agents._cb_session_read(sender.session_id, {"session": ended_name, "what": "results"})
    assert "painted" in read["text"]
    host.brain.drop_pending_dispatches()
    release.set()


async def test_a_session_that_has_ended_is_not_asked_and_is_listed_as_ended(hosts, tmp_path, monkeypatch):
    host, single, project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch)
    ended_name = (await host.ledger.get_session(single.session_id)).name

    with pytest.raises(RuntimeError) as refused:
        await host.task_agents._cb_session_ask(sender.session_id, {"session": ended_name, "question": "what colour?"})
    assert str(refused.value) == words.ASK_REFUSALS["ended"].format(
        name=ended_name, days=settings.SESSION_SINGLE_LIFETIME_DAYS
    )
    listed = (await host.task_agents._cb_session_list(sender.session_id, {}))["text"]
    [ended_row] = [line for line in listed.splitlines() if f'"{ended_name}"' in line]
    assert words.SESSION_STATE_WORDS["ended"] in ended_row
    project_name = (await host.ledger.get_session(project.session_id)).name
    [project_row] = [line for line in listed.splitlines() if f'"{project_name}"' in line]
    assert words.SESSION_STATE_WORDS["ended"] not in project_row and words.SESSION_STATE_WORDS["closed"] in project_row
    release.set()


async def test_the_same_session_is_written_to_and_listed_as_open_on_the_seventh_day(hosts, tmp_path, monkeypatch):
    host, single, _project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch, days=0.0)
    # The ledger's clock is the real one here, a little after the commission ended; six days and
    # most of the seventh later it is still gone on with.
    _days_later(host, monkeypatch, settings.SESSION_SINGLE_LIFETIME_DAYS - 0.1)
    name = (await host.ledger.get_session(single.session_id)).name
    answer = await host.task_agents._cb_session_send(sender.session_id, {"to": name, "message": "once more"})
    assert answer["message"].startswith(f'Queued for "{name}"')
    host.brain.drop_pending_dispatches()
    release.set()


async def test_a_note_that_comes_due_after_the_session_ended_is_dropped_and_starts_nothing(hosts, tmp_path, monkeypatch):
    host, single, _project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch)
    before = await host.ledger.select(session_id=single.session_id)
    # Written to the ledger as it was while the session was alive (the ledger itself takes any note).
    from nekoclaw.host.ledger import MessageLimits

    message, refusal, _wait = await host.ledger.message_add(
        from_session=sender.session_id,
        from_commission=sender.id,
        to_session=single.session_id,
        body="paint it again",
        depth=1,
        notify_when_done=False,
        notice_after=3600.0,
        limits=MessageLimits(target_interval=0.0, per_hour=10, queue_max=5, dedup_seconds=600.0),
    )
    assert message is not None and refusal == ""
    await wait_until(
        lambda: _status_of(host, message.id, ("void", "dropped", "failed")), what="the note being given up"
    )
    wake = (await host.ledger.run(lambda conn: [dict(row) for row in conn.execute("SELECT * FROM wake_requests")]))[0]
    assert wake["status"] == "void" and wake["status_reason"] == "its session has ended"
    # No commission was filed for it, and nothing started.
    assert await host.ledger.select(session_id=single.session_id) == before
    assert host.task_agents._held_by.get(single.session_id) is None
    host.brain.drop_pending_dispatches()
    release.set()


async def _status_of(host: Any, message_id: int, wanted: tuple[str, ...]) -> bool:
    message = await host.ledger.message_get(message_id)
    return message is not None and message.status in wanted


async def test_a_commission_that_got_stuck_is_not_taken_up_again_in_a_session_that_has_ended(hosts, tmp_path, monkeypatch):
    stuck = [call("report", {"status": "blocked", "summary": "which fence?"}, "call-b"), finish("tool_calls")]
    scripts = {
        _k("paint the fence"): ScriptedLLM([stuck]),
        _k("sort the budget"): ScriptedLLM([stuck]),
    }
    host = _host(hosts, tmp_path, scripts_by_commission(scripts))
    await host.space.touch_project("Budget app")
    single = await wait_status(host, (await _dispatch(host, "paint the fence")).commission_id, "blocked")
    project = await wait_status(host, (await _dispatch(host, "sort the budget in the budget app")).commission_id, "blocked")
    await _let_go(host)
    _days_later(host, monkeypatch, 8.0)

    # What is said of it is not passed on: it is work of its own (``TaskBrain`` files one when it is not delivered).
    assert await host.tell(single.id, "the fence at the back") is False
    assert (await host.ledger.get(single.id)).status == "blocked"
    assert len(scripts[_k("paint the fence")].requests) == 1
    # In a project's session the same is taken up again, however long ago it got stuck.
    scripts[_k("sort the budget")].add(*_report("ready", "sorted"))
    assert await host.tell(project.id, "the budget of 2026")
    assert (await wait_status(host, project.id, "done")).result_summary == "sorted"


async def test_a_session_whose_commission_is_under_way_is_gone_on_with_however_old_it_is(hosts, tmp_path, monkeypatch):
    host, _single, _project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch, days=30.0)
    name = (await host.ledger.get_session(sender.session_id)).name
    # Not the sender itself (a session writes to itself no more than it asks itself); its state is at work.
    state = await host.task_agents._state_of(await host.ledger.get_session(sender.session_id))
    assert state == words.SESSION_STATE_WORDS["at_work"] and name
    assert await host.ledger.session_alive(await host.ledger.get_session(sender.session_id))
    host.brain.drop_pending_dispatches()
    release.set()


async def test_a_sentence_that_adds_to_a_commission_does_not_go_on_in_a_session_that_has_ended(hosts, tmp_path, monkeypatch):
    # However far the guess by words reaches, it does not reach a session that is over.
    monkeypatch.setattr(settings, "FOLLOW_UP_RECENT_SECONDS", 30 * DAY)
    host, single, project, _sender, release = await _two_sessions(hosts, tmp_path, monkeypatch)
    recent = await host.brain._recently_over("Lan")
    assert {item.id for item in recent} == {project.id}
    # Within the seven days it is among them.
    _days_later(host, monkeypatch, -7.0)
    again = await host.brain._recently_over("Lan")
    assert single.id in {item.id for item in again}
    host.brain.drop_pending_dispatches()
    release.set()


async def test_no_commission_is_filed_for_a_note_in_a_session_that_ended_while_it_waited(hosts, tmp_path, monkeypatch):
    from nekoclaw.host.ledger import MessageLimits

    host, single, _project, sender, release = await _two_sessions(hosts, tmp_path, monkeypatch, days=0.0)
    before = await host.ledger.select(session_id=single.session_id)
    # Nothing hands notes over by itself in this test: ``_file_for_notes`` is asked directly, with the
    # session as it was read when the note was handed to it, and by the time it looks again it is over.
    host.wakes._closed = True
    read_before = await host.ledger.get_session(single.session_id)
    assert await host.ledger.session_alive(read_before)
    message, refusal, _wait = await host.ledger.message_add(
        from_session=sender.session_id,
        from_commission=sender.id,
        to_session=single.session_id,
        body="paint it again",
        depth=1,
        notify_when_done=False,
        notice_after=3600.0,
        limits=MessageLimits(target_interval=0.0, per_hour=10, queue_max=5, dedup_seconds=600.0),
    )
    assert message is not None and refusal == ""
    wakes = await host.ledger.wakes_pending(session_id=single.session_id)
    _days_later(host, monkeypatch, 8.0)

    handed = await host.task_agents._file_for_notes(read_before, wakes, None)

    assert handed.outcome == "dropped"
    assert await host.ledger.select(session_id=single.session_id) == before
    assert (await host.ledger.message_get(message.id)).status == "void"
    assert [wake.status for wake in await host.ledger.wakes_pending(session_id=single.session_id)] == []
    host.brain.drop_pending_dispatches()
    release.set()
