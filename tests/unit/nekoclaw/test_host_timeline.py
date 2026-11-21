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

"""What happened between sessions, read from the ledger: the timeline and the waits."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from utils.tokenize import count_tokens

from nekoclaw.host.ledger import Ledger, MessageLimits
from nekoclaw.host.timeline import TIMELINE_KINDS, WAIT_KINDS, cursor, parse_cursor, question_text

LIMITS = MessageLimits(target_interval=0.0, per_hour=100, queue_max=100, dedup_seconds=0.0)
SECRET = "sk-SECRET-0123456789"


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


def tick(ledger: Ledger, seconds: float = 1.0) -> None:
    ledger.clock.now += seconds  # type: ignore[attr-defined]


async def _session(ledger: Ledger, name: str, *, project: str | None = "alpha", lanlan: str = "Lan", **more: Any) -> Any:
    kind = more.pop("kind", "project" if project else "single")
    return await ledger.create_session(name=name, lanlan_name=lanlan, kind=kind, project=project, status="idle", **more)


async def _file(ledger: Ledger, session: Any, title: str = "job", **more: Any) -> Any:
    values = {
        "lanlan_name": session.lanlan_name,
        "title": title,
        "dispatch_text": f"do {title}",
        "source": "dispatch",
        "route": "task_agent",
        "project": session.project,
        "session_id": session.id,
    }
    values.update(more)
    commission, created = await ledger.create(**values)
    assert created
    return commission


async def _working(ledger: Ledger, session: Any, title: str = "job", **more: Any) -> Any:
    commission = await _file(ledger, session, title, **more)
    assert (await ledger.transition(commission.id, "working")).applied
    return await ledger.get(commission.id)


async def _note(ledger: Ledger, sender: Any, to: str, body: str = "hello", **more: Any) -> Any:
    values = {
        "from_session": sender.session_id,
        "from_commission": sender.id,
        "to_session": to,
        "body": body,
        "depth": 1,
        "notify_when_done": False,
        "notice_after": 3600.0,
        "limits": LIMITS,
    }
    values.update(more)
    message, refusal, _wait = await ledger.message_add(**values)
    assert refusal == "", refusal
    return message


async def _all(ledger: Ledger, **criteria: Any) -> list[dict[str, Any]]:
    page = await ledger.timeline(limit=10_000, **criteria)
    assert not page["more"]
    return page["entries"]


def _of(entries: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry["kind"] == kind]


async def _waits(ledger: Ledger, **criteria: Any) -> list[dict[str, Any]]:
    result = await ledger.waits(limit=10_000, **criteria)
    assert not result["more"]
    return result["waits"]


# ---------------------------------------------------------------------------
# The kinds of entries
# ---------------------------------------------------------------------------


async def test_a_commission_filed_queued_started_and_ended(ledger):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    tick(ledger)
    queued = await _file(ledger, main, "second")
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})
    tick(ledger)
    await ledger.append_event(holder.id, "queued", {"ahead": 2})
    tick(ledger)
    assert (await ledger.transition(holder.id, "done", reason="all good")).applied

    entries = await _all(ledger)

    filed = _of(entries, "filed")
    assert [entry["text"] for entry in filed] == ["first", "second"]
    assert filed[0]["detail"] == {"source": "dispatch"} and filed[0]["peer"] is None
    assert (filed[0]["session_id"], filed[0]["project"], filed[0]["lanlan_name"]) == (main.id, "alpha", "Lan")
    behind, alone = _of(entries, "queued")
    assert behind["commission_id"] == queued.id
    assert behind["detail"] == {"from": None, "ahead": 0, "behind": holder.id}
    assert behind["peer"] == {"type": "session", "session_id": main.id, "commission_id": holder.id}
    assert alone["detail"] == {"from": None, "ahead": 2, "behind": None} and alone["peer"] is None
    [started] = _of(entries, "started")
    assert (started["commission_id"], started["detail"]) == (holder.id, {"from": "queued"})
    [ended] = _of(entries, "ended")
    assert (ended["detail"], ended["text"], ended["peer"]) == ({"outcome": "done"}, "all good", None)


async def test_queued_again_by_a_status_change_names_where_from(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    assert (await ledger.transition(commission.id, "queued", reason="no place")).applied

    [queued] = _of(await _all(ledger), "queued")

    assert queued["detail"] == {"from": "working", "ahead": None, "behind": None}


async def test_every_way_a_commission_ends_is_an_end(ledger):
    main = await _session(ledger, "main")
    outcomes = []
    for status in ("done", "failed", "cancelled", "blocked"):
        commission = await _working(ledger, main, status)
        assert (await ledger.transition(commission.id, status)).applied
        outcomes.append(status)

    ended = _of(await _all(ledger), "ended")

    assert [entry["detail"]["outcome"] for entry in ended] == outcomes


async def test_every_question_asked_is_shown_and_no_more_than_what_it_asks(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    first = {"kind": "report", "question": "which branch?", "details": SECRET, "id": "q1"}
    assert (await ledger.transition(commission.id, "waiting_user", pending_question=json.dumps(first))).applied
    tick(ledger)
    await ledger.append_event(commission.id, "answered", {"text": "main"})
    assert (await ledger.transition(commission.id, "working")).applied
    tick(ledger)
    # As ``PermissionAsk.to_json`` writes it.
    second = {
        "kind": "permission",
        "category": "mcp_server",
        "key": "github",
        "detail": {"args": {"token": SECRET}},
        "options": ["once", "deny"],
        "id": "q2",
    }
    assert (await ledger.transition(commission.id, "waiting_user", pending_question=json.dumps(second))).applied

    entries = await _all(ledger)

    earlier, now = _of(entries, "asked_user")
    assert (earlier["text"], earlier["detail"], earlier["peer"]["type"]) == ("which branch?", {"question_kind": "report"}, "user")
    assert (now["text"], now["detail"]) == ("mcp_server:github", {"question_kind": "permission"})
    [answered] = _of(entries, "answered")
    assert (answered["text"], answered["peer"]["type"]) == ("main", "user")
    assert SECRET not in json.dumps(entries)

    # Answered, the commission has no question any more; the replay still does.
    assert (await ledger.transition(commission.id, "working")).applied
    assert [entry["text"] for entry in _of(await _all(ledger), "asked_user")] == ["which branch?", "mcp_server:github"]
    # What the move keeps of it, and no more.
    moves = [event.payload for event in await ledger.events(commission.id) if event.type == "status"]
    asked = [move for move in moves if move["to"] == "waiting_user"]
    assert [(move["question"], move["question_kind"]) for move in asked] == [
        ("which branch?", "report"),
        ("mcp_server:github", "permission"),
    ]
    assert all("question" not in move for move in moves if move["to"] != "waiting_user")
    assert SECRET not in json.dumps(moves)


async def test_back_to_waiting_for_the_user_with_nothing_asked_is_no_question(ledger):
    # As ``_answer_untaken`` hands an answer that was not taken over again.
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    assert (await ledger.transition(commission.id, "waiting_user")).applied

    assert _of(await _all(ledger), "asked_user") == []
    assert await _waits(ledger) == []
    [move] = [event.payload for event in await ledger.events(commission.id) if event.type == "status"][-1:]
    assert move == {"from": "working", "to": "waiting_user", "reason": ""}
    assert (await ledger.transition(commission.id, "working")).applied
    assert _of(await _all(ledger), "started")[-1]["detail"] == {"from": "waiting_user"}


async def test_a_question_too_deep_to_read_still_moves_the_commission(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    deep = "[" * 100_000 + "]" * 100_000

    assert (await ledger.transition(commission.id, "waiting_user", pending_question=deep)).applied

    [asked] = _of(await _all(ledger), "asked_user")
    assert (asked["text"], asked["detail"]) == ("", {"question_kind": ""})
    [wait] = await _waits(ledger)
    assert (wait["kind"], wait["text"]) == ("user", "")


async def test_a_question_of_several_shows_the_first(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    question = {"kind": "questions", "questions": [{"question": "colour?"}, {"question": "size?"}]}
    assert (await ledger.transition(commission.id, "waiting_user", pending_question=json.dumps(question))).applied

    [asked] = _of(await _all(ledger), "asked_user")

    assert (asked["text"], asked["detail"]) == ("colour?", {"question_kind": "questions"})


async def test_what_the_user_said_and_what_the_task_agent_reported(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    await ledger.append_event(commission.id, "told", {"text": "use the blue one"})
    await ledger.append_event(
        commission.id, "report", {"status": "done", "summary": "built it", "details": SECRET, "files": [SECRET]}
    )

    entries = await _all(ledger)

    [told] = _of(entries, "told")
    assert (told["text"], told["peer"]) == ("use the blue one", {"type": "user", "session_id": None, "commission_id": None})
    [reported] = _of(entries, "reported")
    assert (reported["text"], reported["detail"]) == ("built it", {"status": "done"})
    assert SECRET not in json.dumps(entries)


async def test_a_child_started_and_returned(ledger):
    main = await _session(ledger, "main")
    parent = await _working(ledger, main)
    child_session = await _session(ledger, "kid", kind="child")
    child = await _working(ledger, child_session, "part", source="task_agent", parent_id=parent.id, name="agent-1")
    await ledger.append_event(parent.id, "subagent_started", {"name": "agent-1", "commission_id": child.id})
    assert (await ledger.transition(child.id, "failed", reason="no luck")).applied

    entries = await _all(ledger)

    top, kid = _of(entries, "filed")
    assert top["peer"] is None
    assert kid["peer"] == {"type": "session", "session_id": main.id, "commission_id": parent.id}
    assert kid["detail"] == {"source": "task_agent"}
    [started] = _of(entries, "child_started")
    assert (started["commission_id"], started["text"]) == (parent.id, "agent-1")
    assert started["peer"] == {"type": "session", "session_id": child_session.id, "commission_id": child.id}
    [returned] = _of(entries, "child_returned")
    assert (returned["commission_id"], returned["detail"], returned["text"]) == (child.id, {"outcome": "failed"}, "no luck")
    assert returned["peer"] == {"type": "session", "session_id": main.id, "commission_id": parent.id}
    assert _of(entries, "ended") == []


async def test_a_question_to_another_session_asked_and_answered(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    asker = await _working(ledger, main)
    await _working(ledger, other, "theirs")
    asked, refusal, _wait = await ledger.ask_add(
        from_session=main.id, from_commission=asker.id, to_session=other.id, body="where is the config?", asks_per_hour=10
    )
    assert refusal == ""
    copy = await _session(ledger, "other-copy", kind="fork", forked_from=other.id)
    answer = await _working(ledger, copy, "ask other", source="ask", parent_id=asker.id, name="ask-1")
    # The ask's own copy is told as a question, not as a child.
    await ledger.append_event(asker.id, "subagent_started", {"name": "ask-1", "commission_id": answer.id, "asks": other.id})
    assert (await ledger.transition(answer.id, "done")).applied

    entries = await _all(ledger)

    [question] = _of(entries, "asked_session")
    assert (question["session_id"], question["commission_id"], question["text"]) == (main.id, asker.id, "where is the config?")
    assert question["peer"]["session_id"] == other.id
    assert question["detail"] == {"status": "delivered", "delivered_at": 1000.0, "notify_when_done": False}
    assert _of(entries, "child_started") == []
    [answered] = _of(entries, "ask_answered")
    assert answered["commission_id"] == answer.id
    assert answered["peer"] == {"type": "session", "session_id": main.id, "commission_id": asker.id}


async def test_notes_between_sessions_and_the_word_that_their_turn_is_over(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other", project="beta")
    sender = await _working(ledger, main)
    await _note(ledger, sender, other.id, "the build is at dist/", notify_when_done=True)
    taker = await _working(ledger, other, "theirs")
    [wake] = await ledger.wakes_pending(session_id=other.id)
    await ledger.wakes_fire([wake.id], into=taker.id)
    await ledger.wakes_taken([wake.id])
    tick(ledger)
    assert await ledger.turn_over(taker.id, word=lambda _note: "they are through") == 1

    entries = await _all(ledger)

    [message] = _of(entries, "message")
    assert (message["session_id"], message["commission_id"], message["text"]) == (main.id, sender.id, "the build is at dist/")
    assert message["peer"] == {"type": "session", "session_id": other.id, "commission_id": taker.id}
    assert message["detail"] == {"status": "delivered", "delivered_at": 1000.0, "notify_when_done": True}
    assert (message["project"], message["at"]) == ("alpha", 1000.0)
    [notice] = _of(entries, "done_notice")
    assert (notice["session_id"], notice["commission_id"], notice["text"]) == (other.id, taker.id, "they are through")
    assert notice["peer"]["session_id"] == main.id and notice["project"] == "beta"


async def test_a_copy_carries_on_and_hands_back(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    await ledger.append_event(commission.id, "carried_on_in_copy", {"session_id": "copy-1", "of": main.id, "behind": "x"})
    await ledger.append_event(commission.id, "handed_back", {"tool": "write_file", "to": main.id})

    entries = await _all(ledger)

    [into] = _of(entries, "into_copy")
    assert into["peer"] == {"type": "session", "session_id": main.id, "commission_id": None}
    assert into["detail"] == {"copy_session_id": "copy-1"}
    [back] = _of(entries, "handed_back")
    assert back["peer"]["session_id"] == main.id and back["detail"] == {}
    assert "write_file" not in json.dumps(entries)


async def test_taken_back_from_a_copy_and_copies_reporting(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    await ledger.append_event(commission.id, "not_carried_on", {"copy": "copy-1", "of": main.id})
    await ledger.append_event(commission.id, "back_from_copy", {"copy": "copy-2", "to": main.id})
    await ledger.append_event(commission.id, "back_from_copy", {"copy": 3, "to": [main.id]})
    await ledger.append_event(commission.id, "told_of_copies", {"text": f"<frame {SECRET}>"})

    entries = await _all(ledger)

    [called_off] = _of(entries, "copy_called_off")
    assert called_off["peer"] == {"type": "session", "session_id": main.id, "commission_id": None}
    assert called_off["detail"] == {"copy_session_id": "copy-1"}
    back, odd = _of(entries, "back_from_copy")
    assert back["peer"]["session_id"] == main.id and back["detail"] == {"copy_session_id": "copy-2"}
    assert odd["peer"]["session_id"] is None and odd["detail"] == {"copy_session_id": None}
    [told] = _of(entries, "copies_reported")
    assert (told["text"], told["peer"], told["detail"], told["commission_id"]) == ("", None, {}, commission.id)
    assert SECRET not in json.dumps(entries)


async def test_resting_set_times_and_being_woken(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    wake, refusal = await ledger.wake_add(
        session_id=main.id, commission_id=commission.id, note="look at the build again", due_at=1600.0,
        pending_max=5, daily_max=5,
    )
    assert refusal == ""
    resting = await ledger.rest(commission.id)
    assert await ledger.rest_over(commission.id, resting.since)
    assert await ledger.wakes_fire([wake.id], into=commission.id) == [wake.id]

    entries = await _all(ledger)

    [wake_set] = _of(entries, "wake_set")
    assert (wake_set["text"], wake_set["detail"]) == ("look at the build again", {"due_at": 1600.0})
    [rest] = _of(entries, "resting")
    assert rest["detail"] == {"until": 1600.0}
    [over] = _of(entries, "rest_over")
    assert over["detail"] == {}
    [woken] = _of(entries, "woken")
    assert woken["detail"] == {"count": 1, "reasons": ["timer"]}


async def test_every_kind_is_in_the_list_and_steps_are_left_out(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    for kind, payload in (
        ("tool_start", {"tool_name": "shell", "args": {"cmd": f"curl -H {SECRET}"}}),
        ("tool_update", {"text": SECRET}),
        ("tool_end", {"tool_name": "shell", "text": SECRET}),
        ("text", {"text": SECRET}),
        ("usage", {"input_tokens": 5}),
        ("opened", {"session_id": main.id}),
        ("capability_denied", {"capability": "x", "reason": SECRET}),
    ):
        await ledger.append_event(commission.id, kind, payload)
    await ledger.append_event(commission.id, "created", {"title": "x", "extra": SECRET})
    await ledger.append_event(commission.id, "queued", {"ahead": 1, "behind": 7, "args": SECRET})
    await ledger.append_event(commission.id, "woken", {"wake_ids": [True, "3", 999, 0, -1, 2**63]})

    entries = await _all(ledger)

    assert {entry["kind"] for entry in entries} <= set(TIMELINE_KINDS)
    assert [entry["kind"] for entry in entries] == ["filed", "started", "filed", "queued", "woken"]
    assert SECRET not in json.dumps(entries)
    assert _of(entries, "queued")[0]["detail"] == {"from": None, "ahead": 1, "behind": None}
    assert _of(entries, "woken")[0]["detail"] == {"count": 1, "reasons": []}
    assert len(set(TIMELINE_KINDS)) == len(TIMELINE_KINDS) == 24


async def _raw_event(ledger: Ledger, commission_id: str, kind: str, payload: str) -> None:
    def work(conn: Any) -> None:
        seq = conn.execute("SELECT MAX(seq) + 1 FROM events WHERE commission_id = ?", (commission_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO events (commission_id, seq, ts, type, payload) VALUES (?, ?, 1000.0, ?, ?)",
            (commission_id, seq, kind, payload),
        )

    await ledger.run(work)


async def test_a_malformed_record_does_not_stop_the_timeline(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    await _raw_event(ledger, commission.id, "subagent_started", "not json")
    await _raw_event(ledger, commission.id, "told", "[1]")
    await _raw_event(ledger, commission.id, "status", "not json")
    await ledger.append_event(commission.id, "told", {"text": 5})
    await ledger.append_event(commission.id, "subagent_started", {"name": ["x"], "commission_id": [1]})
    await ledger.append_event(commission.id, "queued", {"ahead": True, "behind": [1]})
    await ledger.append_event(commission.id, "queued", {"ahead": "3"})
    await ledger.append_event(commission.id, "carried_on_in_copy", {"session_id": 7, "of": [main.id]})
    await ledger.run(
        lambda conn: conn.execute(
            "INSERT INTO messages (from_session, to_session, kind, body, body_hash, status, created_at) "
            "VALUES (?, ?, 'gossip', 'psst', '', 'queued', 1000.0)",
            (main.id, main.id),
        )
    )

    entries = await _all(ledger)

    nobody = {"type": "session", "session_id": None, "commission_id": None}
    first, second = _of(entries, "child_started")
    assert first["peer"] == second["peer"] == nobody and second["text"] == ""
    assert [entry["text"] for entry in _of(entries, "told")] == ["", ""]
    [unreadable] = _of(entries, "ended")
    assert unreadable["detail"] == {"outcome": ""}
    assert [entry["detail"]["ahead"] for entry in _of(entries, "queued")] == [None, None]
    assert [entry["peer"] for entry in _of(entries, "queued")] == [None, None]
    [into] = _of(entries, "into_copy")
    assert into["peer"] == nobody and into["detail"] == {"copy_session_id": None}
    # A kind not known yet comes as it is, for the reader to pass over.
    [gossip] = _of(entries, "gossip")
    assert gossip["text"] == "psst"


# ---------------------------------------------------------------------------
# Pages: in order, none twice, none passed over
# ---------------------------------------------------------------------------


async def _mixed(ledger: Ledger) -> None:
    """Events and notes, several at one time, the two tables in turn."""
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    sender = await _working(ledger, main, "a")
    for round_ in range(4):
        tick(ledger)
        await ledger.append_event(sender.id, "told", {"text": f"told {round_}"})
        await _note(ledger, sender, other.id, f"note {round_}")
        await ledger.append_event(sender.id, "told", {"text": f"again {round_}"})
        if round_ % 2:
            tick(ledger)
            await _note(ledger, sender, other.id, f"later {round_}")


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
async def test_pages_of_any_size_add_up_to_the_whole(ledger, size):
    await _mixed(ledger)
    whole = await _all(ledger)

    paged: list[dict[str, Any]] = []
    after = None
    for _ in range(len(whole) + 2):
        page = await ledger.timeline(after=after, limit=size)
        assert len(page["entries"]) <= size
        assert page["next"] != after
        paged += page["entries"]
        after = page["next"]
        if not page["more"]:
            break
    else:
        pytest.fail("the pages did not come to an end")

    # The same entries; a cursor may stand further on where a table was read
    # to its end (past rows not shown), and goes on the same.
    assert [{**entry, "cursor": None} for entry in paged] == [{**entry, "cursor": None} for entry in whole]
    assert after == page["latest"]


async def test_the_order_is_by_time_then_table_then_number(ledger):
    await _mixed(ledger)

    entries = await _all(ledger)

    times = [entry["at"] for entry in entries]
    assert times == sorted(times)
    # At one time: the events first, in the order written, then the notes.
    first = [(entry["kind"], entry["text"]) for entry in entries if entry["at"] == 1001.0]
    assert first == [("told", "told 0"), ("told", "again 0"), ("message", "note 0")]


async def test_an_entry_cursor_goes_on_after_that_entry(ledger):
    await _mixed(ledger)
    whole = await _all(ledger)

    for index, entry in enumerate(whole):
        page = await ledger.timeline(after=entry["cursor"], limit=10_000)
        assert page["entries"] == whole[index + 1 :]


async def test_what_is_written_later_comes_after_even_with_an_earlier_time(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    first = await ledger.timeline(limit=100)
    # The clock went back.
    tick(ledger, -500)
    await ledger.append_event(commission.id, "told", {"text": "late"})

    page = await ledger.timeline(after=first["next"], limit=100)

    assert [entry["text"] for entry in page["entries"]] == ["late"]
    again = await ledger.timeline(after=page["next"], limit=100)
    assert again["entries"] == [] and not again["more"]


@pytest.mark.parametrize("size", [1, 2])
async def test_pages_with_the_clock_going_back_give_each_entry_once(ledger, size):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    sender = await _working(ledger, main)
    expected = set()
    # Written in turn, each later one at an earlier time than the one before.
    for round_, offset in enumerate((0, -10, -20, 5, -30)):
        tick(ledger, offset)
        await ledger.append_event(sender.id, "told", {"text": f"event {round_}"})
        tick(ledger, -1)
        await _note(ledger, sender, other.id, f"note {round_}")
        expected |= {f"event {round_}", f"note {round_}"}

    seen: list[str] = []
    after = None
    for _ in range(40):
        page = await ledger.timeline(after=after, limit=size)
        assert page["next"] != after
        seen += [entry["text"] for entry in page["entries"] if entry["kind"] in ("told", "message")]
        after = page["next"]
        if not page["more"]:
            break
    else:
        pytest.fail("the pages did not come to an end")

    assert sorted(seen) == sorted(expected)


async def test_only_what_is_new_from_the_latest(ledger):
    await _mixed(ledger)
    page = await ledger.timeline(limit=0)
    assert page["entries"] == [] and page["more"]
    main = (await ledger.sessions_named("main"))[0]
    [sender] = await ledger.select(session_id=main.id)
    tick(ledger, 60)
    await ledger.append_event(sender.id, "told", {"text": "news"})

    new = await ledger.timeline(after=page["latest"], limit=100)

    assert [entry["text"] for entry in new["entries"]] == ["news"]
    assert new["next"] == new["latest"]


async def test_one_put_under_a_project_after_it_was_read_past_reaches_the_projects_reader(ledger):
    daily = await _session(ledger, "daily", project=None)
    commission = await _file(ledger, daily, "add a CSV export", project=None)
    page = await ledger.timeline(project="alpha", limit=100)
    assert page["entries"] == []
    filed = await ledger.file_under_project(commission.id, "alpha", workdir="D:/work/alpha")
    assert (filed.project, filed.workdir) == ("alpha", "D:/work/alpha")

    later = await ledger.timeline(project="alpha", after=page["next"], limit=100)

    [entry] = later["entries"]
    assert (entry["kind"], entry["text"], entry["detail"]) == ("filed_under_project", "add a CSV export", {"project": "alpha"})
    assert entry["commission_id"] == commission.id and entry["project"] == "alpha"


async def test_putting_under_a_project_is_on_record_whole_or_not_at_all(ledger, monkeypatch):
    daily = await _session(ledger, "daily", project=None)
    commission = await _file(ledger, daily, "add a CSV export", project=None, workdir="D:/own")

    def broken(*_args: Any) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ledger, "_event", broken)
    with pytest.raises(sqlite3.OperationalError):
        await ledger.file_under_project(commission.id, "alpha")
    monkeypatch.undo()

    assert (await ledger.get(commission.id)).project is None
    assert await ledger.file_under_project("no-such-commission", "alpha") is None
    assert await ledger.events("no-such-commission") == []
    kept = await ledger.file_under_project(commission.id, "alpha")
    assert (kept.project, kept.workdir) == ("alpha", "D:/own")
    assert [event.type for event in await ledger.events(commission.id)][-1] == "filed_under_project"


async def test_a_filtered_page_read_to_the_end_moves_past_what_it_passed_over(ledger):
    mine = await _session(ledger, "mine")
    theirs = await _session(ledger, "theirs", project="beta")
    await _working(ledger, mine, "a")
    other = await _working(ledger, theirs, "b")
    for round_ in range(5):
        await ledger.append_event(other.id, "told", {"text": "not mine"})
        await _note(ledger, other, theirs.id, f"not mine either {round_}")

    page = await ledger.timeline(project="alpha", limit=100)

    assert {entry["project"] for entry in page["entries"]} == {"alpha"}
    assert page["next"] == page["latest"] and not page["more"]


async def test_a_page_cut_short_stops_at_its_last_entry(ledger):
    await _mixed(ledger)

    page = await ledger.timeline(limit=3)

    assert page["more"] and page["next"] == page["entries"][-1]["cursor"]
    assert page["next"] != page["latest"]


async def test_cursors_are_checked(ledger):
    with pytest.raises(ValueError):
        await ledger.timeline(after="12", limit=5)
    with pytest.raises(ValueError):
        await ledger.timeline(after="e1.m2 ", limit=5)
    with pytest.raises(ValueError):
        await ledger.timeline(limit=-1)
    assert parse_cursor(None) == parse_cursor("") == (0, 0)
    assert parse_cursor(cursor(12, 3)) == (12, 3)
    assert parse_cursor(f"e{2**63 - 1}.m0") == (2**63 - 1, 0)
    for too_large in (f"e{2**63}.m0", f"e0.m{2**63}"):
        with pytest.raises(ValueError):
            await ledger.timeline(after=too_large, limit=5)
    page = await ledger.timeline(after="e999.m999", limit=5)
    assert page["entries"] == [] and page["next"] == "e999.m999"


# ---------------------------------------------------------------------------
# By project, by character
# ---------------------------------------------------------------------------


async def test_kept_to_a_project_or_a_character(ledger):
    alpha = await _session(ledger, "alpha-main")
    beta = await _session(ledger, "beta-main", project="beta")
    solo = await _session(ledger, "solo", project=None, lanlan="Mio")
    in_alpha = await _working(ledger, alpha, "a")
    in_beta = await _working(ledger, beta, "b")
    await _working(ledger, solo, "c")
    await _note(ledger, in_beta, alpha.id, "beta to alpha")
    await _note(ledger, in_alpha, alpha.id, "alpha to itself")
    await ledger.ask_add(from_session=beta.id, from_commission=in_beta.id, to_session=beta.id, body="q", asks_per_hour=9)

    alpha_texts = [entry["text"] for entry in await _all(ledger, project="alpha")]
    assert alpha_texts == ["a", "", "beta to alpha", "alpha to itself"]
    beta_texts = [entry["text"] for entry in await _all(ledger, project="beta")]
    assert beta_texts == ["b", "", "beta to alpha", "q"]
    assert {entry["lanlan_name"] for entry in await _all(ledger, lanlan_name="Mio")} == {"Mio"}
    assert len(await _all(ledger, lanlan_name="Mio")) == 2
    assert [entry["text"] for entry in await _all(ledger, lanlan_name="Lan", project="beta")] == beta_texts
    assert await _all(ledger, lanlan_name="Mio", project="alpha") == []
    assert len(await _all(ledger)) == 9


async def test_a_note_of_a_session_of_another_character_is_kept_to_either(ledger):
    lan = await _session(ledger, "lan-main")
    mio = await _session(ledger, "mio-main", lanlan="Mio", project="gamma")
    sender = await _working(ledger, mio, "m")
    await _note(ledger, sender, lan.id, "hi Lan")

    [note] = _of(await _all(ledger, lanlan_name="Lan"), "message")

    assert (note["text"], note["lanlan_name"], note["project"]) == ("hi Lan", "Mio", "gamma")
    lan_sender = await _working(ledger, lan, "l")
    await _note(ledger, lan_sender, mio.id, "hi Mio")
    assert [entry["text"] for entry in _of(await _all(ledger, lanlan_name="Mio"), "message")] == ["hi Lan", "hi Mio"]
    await ledger.run(lambda conn: conn.execute("DELETE FROM sessions WHERE id = ?", (mio.id,)))
    note, _back = _of(await _all(ledger, lanlan_name="Lan"), "message")
    # Of a sender no longer known: the side that is.
    assert (note["lanlan_name"], note["project"]) == ("Lan", "alpha")


# ---------------------------------------------------------------------------
# Waits
# ---------------------------------------------------------------------------


async def test_queued_behind_a_commission_of_its_session(ledger):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    tick(ledger)
    queued = await _file(ledger, main, "second")
    tick(ledger)
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})

    [wait] = await _waits(ledger)

    assert wait["kind"] == "queue"
    assert wait["waiter"] == {"type": "session", "session_id": main.id, "commission_id": queued.id}
    assert wait["target"] == {"type": "session", "session_id": main.id, "commission_id": holder.id}
    assert (wait["since"], wait["text"], wait["projects"], wait["lanlan_name"]) == (1002.0, "first", ["alpha"], "Lan")

    # Once the one it was behind is over, it waits for a place.
    assert (await ledger.transition(holder.id, "done")).applied
    [wait] = await _waits(ledger)
    assert (wait["kind"], wait["target"]["type"], wait["text"]) == ("place", "place", "")


@pytest.mark.parametrize("let_go", ["rests", "queued"])
async def test_queued_behind_one_that_let_its_session_go_waits_for_a_place(ledger, let_go):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    queued = await _file(ledger, main, "second")
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})
    if let_go == "rests":
        await ledger.wake_add(
            session_id=main.id, commission_id=holder.id, note="later", due_at=2000.0, pending_max=5, daily_max=5
        )
        assert await ledger.rest(holder.id) is not None
    else:
        assert (await ledger.transition(holder.id, "queued")).applied

    kinds = {wait["waiter"]["commission_id"]: wait["kind"] for wait in await _waits(ledger)}

    assert kinds[queued.id] == "place"


async def test_queued_behind_one_waiting_for_the_user_waits_for_it(ledger):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    queued = await _file(ledger, main, "second")
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})
    assert (await ledger.transition(holder.id, "waiting_user")).applied

    kinds = {wait["waiter"]["commission_id"]: wait["kind"] for wait in await _waits(ledger)}

    assert kinds[queued.id] == "queue"


async def test_queued_behind_after_it_was_at_work_before(ledger):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    queued = await _working(ledger, main, "second")
    assert (await ledger.transition(queued.id, "queued")).applied
    tick(ledger)
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["target"]["commission_id"], wait["since"]) == ("queue", holder.id, 1001.0)


async def test_queued_since_the_last_it_was_queued_though_the_clock_went_back(ledger):
    main = await _session(ledger, "main")
    queued = await _working(ledger, main)
    tick(ledger, 100)
    assert (await ledger.transition(queued.id, "queued")).applied
    tick(ledger, -200)
    await ledger.append_event(queued.id, "queued", {"ahead": 0})

    [wait] = await _waits(ledger)

    # The queued event was written last, at an earlier time than the status.
    assert (wait["kind"], wait["since"]) == ("place", 900.0)


async def test_queued_for_a_place(ledger):
    main = await _session(ledger, "main")
    queued = await _file(ledger, main)
    tick(ledger, 5)
    await ledger.append_event(queued.id, "queued", {"ahead": 3})

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["since"]) == ("place", 1005.0)
    assert wait["target"] == {"type": "place", "session_id": None, "commission_id": None}


async def test_queued_again_waits_for_whoever_keeps_its_session(ledger):
    main = await _session(ledger, "main")
    holder = await _working(ledger, main, "first")
    queued = await _file(ledger, main, "second")
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": holder.id})
    tick(ledger)
    assert (await ledger.transition(queued.id, "working")).applied
    tick(ledger)
    assert (await ledger.transition(queued.id, "queued")).applied

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["target"]["commission_id"], wait["since"]) == ("queue", holder.id, 1002.0)


async def test_queued_behind_one_that_is_over_waits_for_the_one_that_took_the_session(ledger):
    main = await _session(ledger, "main")
    first = await _working(ledger, main, "first")
    second = await _file(ledger, main, "second")
    third = await _file(ledger, main, "third")
    for queued in (second, third):
        await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": first.id})
    assert (await ledger.transition(first.id, "done")).applied
    assert (await ledger.transition(second.id, "working")).applied

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["waiter"]["commission_id"]) == ("queue", third.id)
    assert (wait["target"]["commission_id"], wait["text"]) == (second.id, "second")


async def test_commissions_with_no_session_keep_none_for_one_another(ledger):
    main = await _session(ledger, "main")
    await _working(ledger, main, "working", session_id=None)
    queued = await _file(ledger, main, "queued", session_id=None)

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["waiter"]["commission_id"]) == ("place", queued.id)


async def test_a_session_kept_by_two_is_waited_for_on_the_first_that_took_it(ledger):
    main = await _session(ledger, "main")
    earlier = await _working(ledger, main, "earlier", id="b-earlier")
    tick(ledger)
    await _working(ledger, main, "later", id="a-later")
    queued = await _file(ledger, main, "next")

    [wait] = await _waits(ledger)

    assert (wait["waiter"]["commission_id"], wait["target"]["commission_id"]) == (queued.id, earlier.id)


def test_what_a_question_asks():
    assert question_text(None) == ("", "")
    assert question_text("not json") == ("", "")
    assert question_text('{"kind": "questions", "questions": []}') == ("", "questions")
    assert question_text('{"questions": ["colour?"]}') == ("", "")
    assert question_text('{"question": 5, "questions": [{"question": "size?"}], "kind": 3}') == ("size?", "")
    assert question_text('{"question": "colour?", "questions": [{"question": "size?"}]}') == ("colour?", "")
    assert question_text('{"questions": {"0": {"question": "size?"}}}') == ("", "")
    assert question_text('{"kind": "permission", "category": "file_write", "key": null}') == ("file_write", "permission")
    assert question_text('{"kind": "permission", "category": "file_write", "question": "ok?"}') == ("ok?", "permission")
    assert question_text('{"kind": "permission", "key": 5, "detail": {"path": "x"}}') == ("", "permission")


async def test_filed_waiting_for_the_user_it_waits_since_it_was_filed(ledger):
    main = await _session(ledger, "main")
    await _file(ledger, main, status="waiting_user", pending_question='{"question": "ok?"}')

    [wait] = await _waits(ledger)

    assert (wait["kind"], wait["since"], wait["text"]) == ("user", 1000.0, "ok?")


async def test_queued_behind_what_is_not_a_commission_waits_for_a_place(ledger):
    main = await _session(ledger, "main")
    queued = await _file(ledger, main)
    await ledger.append_event(queued.id, "queued", {"ahead": 0, "behind": [queued.id]})

    [wait] = await _waits(ledger)

    assert wait["kind"] == "place"


async def test_waiting_for_the_user(ledger):
    main = await _session(ledger, "main")
    commission = await _working(ledger, main)
    tick(ledger, 3)
    question = {"kind": "confirmation", "question": "go ahead?", "detail": {"args": SECRET}}
    assert (await ledger.transition(commission.id, "waiting_user", pending_question=json.dumps(question))).applied
    tick(ledger, 3)
    await ledger.update(commission.id, title="renamed")

    [wait] = await _waits(ledger)

    assert wait["kind"] == "user" and wait["target"] == {"type": "user", "session_id": None, "commission_id": None}
    assert (wait["since"], wait["text"], wait["question_kind"]) == (1003.0, "go ahead?", "confirmation")
    assert SECRET not in json.dumps(wait)


async def test_waiting_for_word_of_a_note(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other", project="beta")
    sender = await _working(ledger, main)
    note = await _note(ledger, sender, other.id, "please build", notify_when_done=True, notice_after=600.0)
    await _note(ledger, sender, other.id, "no word wanted")

    [wait] = await _waits(ledger)

    assert wait["kind"] == "notice"
    assert wait["waiter"] == {"type": "session", "session_id": main.id, "commission_id": sender.id}
    assert wait["target"] == {"type": "session", "session_id": other.id, "commission_id": None}
    assert (wait["since"], wait["text"], wait["message_id"], wait["expires_at"]) == (1000.0, "please build", note.id, 1600.0)
    assert wait["projects"] == ["alpha", "beta"]
    assert [item["kind"] for item in await _waits(ledger, project="beta")] == ["notice"]

    # Told it: nothing waits.
    await ledger.run(lambda conn: conn.execute("UPDATE messages SET notified_at = 1 WHERE id = ?", (note.id,)))
    assert await _waits(ledger) == []


async def test_word_of_a_note_is_not_waited_for_once_it_was_dropped_or_came_too_late(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    sender = await _working(ledger, main)
    dropped = await _note(ledger, sender, other.id, "one", notify_when_done=True)
    expired = await _note(ledger, sender, other.id, "two", notify_when_done=True)
    await ledger.message_void(dropped.id, "stopped")
    await ledger.run(
        lambda conn: conn.execute(
            "UPDATE wake_requests SET status = 'fired' WHERE message_id = ? AND reason = 'notice_expiry'", (expired.id,)
        )
    )

    assert await _waits(ledger) == []


async def test_word_of_a_note_of_a_commission_that_is_over_is_not_waited_for(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    sender = await _working(ledger, main)
    await _note(ledger, sender, other.id, "one", notify_when_done=True)
    # As if its end had left the request pending.
    await ledger.run(lambda conn: conn.execute("UPDATE commissions SET status = 'done' WHERE id = ?", (sender.id,)))

    assert await _waits(ledger) == []


async def test_a_parent_waits_for_its_child(ledger):
    main = await _session(ledger, "main")
    parent = await _working(ledger, main)
    kid_session = await _session(ledger, "kid", kind="child")
    tick(ledger)
    child = await _working(ledger, kid_session, "part", source="task_agent", parent_id=parent.id)

    [wait] = await _waits(ledger)

    assert wait["kind"] == "child"
    assert wait["waiter"] == {"type": "session", "session_id": main.id, "commission_id": parent.id}
    assert wait["target"] == {"type": "session", "session_id": kid_session.id, "commission_id": child.id}
    assert (wait["since"], wait["text"]) == (1001.0, "part")

    assert (await ledger.transition(child.id, "done")).applied
    assert await _waits(ledger) == []


async def test_a_child_of_a_parent_that_is_over_is_not_waited_for(ledger):
    main = await _session(ledger, "main")
    parent = await _working(ledger, main)
    kid_session = await _session(ledger, "kid", kind="child")
    await _working(ledger, kid_session, "part", source="task_agent", parent_id=parent.id)
    assert (await ledger.transition(parent.id, "blocked")).applied

    assert await _waits(ledger) == []


async def test_a_session_waits_for_the_answer_to_its_question(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other", project="beta")
    asker = await _working(ledger, main)
    copy = await _session(ledger, "other-copy", kind="fork", forked_from=other.id, project="beta")
    tick(ledger)
    answer = await _working(ledger, copy, "ask other", source="ask", parent_id=asker.id, dispatch_text="where is it?")

    [wait] = await _waits(ledger)

    assert wait["kind"] == "ask"
    assert wait["waiter"] == {"type": "session", "session_id": main.id, "commission_id": asker.id}
    assert wait["target"] == {"type": "session", "session_id": other.id, "commission_id": None}
    assert (wait["since"], wait["text"], wait["copy_session_id"]) == (1001.0, "where is it?", copy.id)
    assert wait["projects"] == ["alpha", "beta"]

    assert (await ledger.transition(answer.id, "cancelled")).applied
    assert await _waits(ledger) == []


async def test_a_question_whose_copy_is_not_known_waits_on_nobody_known(ledger):
    main = await _session(ledger, "main")
    other = await _session(ledger, "other")
    copy = await _session(ledger, "other-copy", kind="fork", forked_from=other.id)
    asker = await _working(ledger, main)
    await _working(ledger, main, "ask", source="ask", parent_id=asker.id, session_id=None)
    await _working(ledger, copy, "ask other", source="ask", parent_id=asker.id)

    unknown, known = await _waits(ledger)

    assert (unknown["kind"], unknown["target"]["session_id"], unknown["copy_session_id"]) == ("ask", None, None)
    assert (known["target"]["session_id"], known["copy_session_id"]) == (other.id, copy.id)


async def test_waits_kept_to_a_project_or_character_longest_first_and_capped(ledger):
    alpha = await _session(ledger, "alpha-main")
    mio = await _session(ledger, "mio-main", project="beta", lanlan="Mio")
    tick(ledger, 10)
    late = await _file(ledger, alpha, "late")
    await ledger.append_event(late.id, "queued", {"ahead": 1})
    tick(ledger, -5)
    early = await _file(ledger, mio, "early")
    tick(ledger, -5)
    asking = await _working(ledger, mio, "asking")
    assert (await ledger.transition(asking.id, "waiting_user", pending_question="{}")).applied

    waits = await _waits(ledger)

    assert [(item["kind"], item["waiter"]["commission_id"]) for item in waits] == [
        ("user", asking.id),
        ("queue", early.id),
        ("place", late.id),
    ]
    assert [item["waiter"]["commission_id"] for item in await _waits(ledger, project="alpha")] == [late.id]
    assert [item["waiter"]["commission_id"] for item in await _waits(ledger, lanlan_name="Mio")] == [asking.id, early.id]
    assert await _waits(ledger, lanlan_name="Mio", project="alpha") == []
    with pytest.raises(ValueError):
        await ledger.waits(limit=-1)
    assert (await ledger.waits(limit=0)) == {"waits": [], "more": True}
    capped = await ledger.waits(limit=2)
    assert capped["more"] and [item["kind"] for item in capped["waits"]] == ["user", "queue"]
    assert set(WAIT_KINDS) == {"queue", "place", "user", "notice", "child", "ask"}


async def test_waits_at_one_time_are_in_the_order_of_their_kinds(ledger):
    main = await _session(ledger, "main")
    # Named so that the waiters' names alone would put the child first.
    parent = await _working(ledger, main, id="a-parent")
    kid_session = await _session(ledger, "kid", kind="child")
    await _working(ledger, kid_session, "part", source="task_agent", parent_id=parent.id)
    queued = await _file(ledger, main, "next", id="z-queued")

    waits = await _waits(ledger)

    assert [item["kind"] for item in waits] == ["queue", "child"]
    assert waits[0]["waiter"]["commission_id"] == queued.id


# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------


async def test_the_host_cuts_texts_and_pages(tmp_path, monkeypatch):
    from config import nekoclaw_settings
    from tests.unit.nekoclaw.host_fakes import make_host

    monkeypatch.setattr(nekoclaw_settings, "TIMELINE_TEXT_MAX_TOKENS", 3)
    monkeypatch.setattr(nekoclaw_settings, "TIMELINE_PAGE_ROWS", 2)
    monkeypatch.setattr(nekoclaw_settings, "TIMELINE_PAGE_MAX_ROWS", 3)
    monkeypatch.setattr(nekoclaw_settings, "WAITS_MAX_ROWS", 1)
    host = make_host(tmp_path)
    try:
        session = await _session(host.ledger, "main")
        long = " ".join(["word"] * 50)
        commission = await _working(host.ledger, session, long)
        for _ in range(4):
            await host.ledger.append_event(commission.id, "told", {"text": long})
        await _file(host.ledger, session, "two")
        await _file(host.ledger, session, "three")

        page = await host.timeline()
        assert [entry["kind"] for entry in page["entries"]] == ["filed", "started"] and page["more"]
        assert 0 < count_tokens(page["entries"][0]["text"]) <= 3
        # The ledger gives the texts as recorded: they are cut off its thread.
        assert (await host.ledger.timeline(limit=1))["entries"][0]["text"] == long
        assert len((await host.timeline(limit=0))["entries"]) == 1
        assert len((await host.timeline(limit=-5))["entries"]) == 1
        assert len((await host.timeline(limit=50))["entries"]) == 3
        assert (await host.timeline(project="nowhere"))["entries"] == []
        assert len((await host.timeline(lanlan_name="Lan", after=page["next"], limit=100))["entries"]) == 3

        waits = await host.waits()
        assert len(waits["waits"]) == 1 and waits["more"]
        assert (await host.waits(project="nowhere"))["waits"] == []
        assert (await host.waits(lanlan_name="Mio"))["waits"] == []
        with pytest.raises(ValueError):
            await host.timeline(after="bad")
    finally:
        await host.shutdown()


async def test_the_host_cuts_the_texts_of_waits(tmp_path, monkeypatch):
    from config import nekoclaw_settings
    from tests.unit.nekoclaw.host_fakes import make_host

    monkeypatch.setattr(nekoclaw_settings, "TIMELINE_TEXT_MAX_TOKENS", 3)
    host = make_host(tmp_path)
    try:
        session = await _session(host.ledger, "main")
        commission = await _working(host.ledger, session)
        question = json.dumps({"question": " ".join(["word"] * 50)})
        assert (await host.ledger.transition(commission.id, "waiting_user", pending_question=question)).applied

        [wait] = (await host.waits())["waits"]

        assert wait["kind"] == "user" and 0 < count_tokens(wait["text"]) <= 3
        assert (await host.ledger.waits(limit=5))["waits"][0]["text"] == " ".join(["word"] * 50)
    finally:
        await host.shutdown()
