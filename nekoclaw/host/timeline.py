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

"""What happened between sessions, read from the ledger: a timeline and who waits for whom.

Nothing here is stored of its own. The ledger already has it all: what
happened to a commission (``events``), what one session wrote to another
(``messages``), what is to wake whom (``wake_requests``), which commission
started which (``commissions.parent_id``). These functions read it in two
shapes, for a screen that draws the task agents of a project as a group:

* ``read_timeline`` - entries in the order they happened, a page at a
  time, for a stream of posts and for replaying it;
* ``read_waits`` - who waits for whom right now, for the lines between
  them.

Both only read, and run on the ledger's thread (``Ledger.timeline``,
``Ledger.waits``). No entry carries what a tool was called with or gave
back, the content of a file, or anything of the settings: every field of
an entry is picked here by name, never copied from what was recorded. The
steps of a task agent (its tool calls, what it wrote as it went, each
model request) are not entries either: there are far too many of them.

Kinds of entries and of waits are added as the host learns to do more
(a line of work brought back, a team), so whoever reads them must pass
over a kind it does not know.

The cursor. Entries come from two tables, ``events`` and ``messages``,
each numbered in the order its rows were written: ``messages.id`` counts
up, and so does the ``rowid`` of ``events``, which is written by one
thread only and from which nothing is ever deleted, so a number is never
given out twice nor a smaller one later. A cursor is the pair of numbers
read up to, ``e<events rowid>.m<messages id>``. A page takes the rows of
each table after its number, in that order, and merges the two by time
(by table, then number, where the time is the same). Each page continues
exactly where the last stopped in both tables, so no entry comes twice
and none is passed over, whatever the times say; what is written later
has larger numbers and comes after.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

# Kinds of timeline entries. More are added later; a reader passes over
# one it does not know.
TIMELINE_KINDS: tuple[str, ...] = (
    "filed",  # a commission was filed
    "filed_under_project",  # a commission filed with no project was put under one
    "queued",  # it waits for its turn or for a free place
    "started",  # it is being worked on (again)
    "asked_user",  # it waits for the user's answer
    "answered",  # the user answered
    "told",  # the user said more while it was under way
    "reported",  # the task agent reported (progress, a question, its result)
    "ended",  # done, failed, stopped, or stuck
    "child_started",  # it started a task agent of its own
    "child_returned",  # a task agent another one started is over
    "ask_answered",  # the copy that answered a question one session asked another is over
    "into_copy",  # it went on in a copy of its session
    "handed_back",  # the copy handed it back to the session it was copied from
    "copy_called_off",  # it was taken back from a copy before it began there
    "back_from_copy",  # taken up again, it went back from its copy to the session
    "copies_reported",  # a session was told what came of the work in its copies
    "resting",  # it rests until a time
    "rest_over",  # its rest is over
    "wake_set",  # it set itself a time to be woken at
    "woken",  # it was woken (by a note, a time, a notice)
    "message",  # a session wrote to another
    "done_notice",  # a session is told that the turn its note began is over
    "asked_session",  # a session asked another one a question
)

# Kinds of waits. More are added later.
WAIT_KINDS: tuple[str, ...] = (
    "queue",  # queued behind another commission of its session
    "place",  # queued for a free place
    "user",  # waits for the user's answer
    "notice",  # wrote a note and asked to be told when it is dealt with
    "child",  # a task agent waits for one it started
    "ask",  # a session asked another a question that is not answered yet
)

_EVENT_TYPES: tuple[str, ...] = (
    "created",
    "filed_under_project",
    "queued",
    "status",
    "answered",
    "told",
    "report",
    "subagent_started",
    "carried_on_in_copy",
    "handed_back",
    "not_carried_on",
    "back_from_copy",
    "told_of_copies",
    "resting",
    "rest_over",
    "wake_set",
    "woken",
)
_MESSAGE_KINDS: dict[str, str] = {"note": "message", "done": "done_notice", "ask": "asked_session"}
_UNDER_WAY: tuple[str, ...] = ("queued", "working", "waiting_user")
_CURSOR = re.compile(r"e(\d+)\.m(\d+)")
_EVENTS, _MESSAGES = 0, 1
# The largest number SQLite gives a row.
_LARGEST_ROW = 2**63 - 1


def cursor(events: int, messages: int) -> str:
    """Return the cursor that stands after these rows of ``events`` (rowid) and ``messages`` (id)."""
    return f"e{int(events)}.m{int(messages)}"


def parse_cursor(value: str | None) -> tuple[int, int]:
    """Return the two numbers of a cursor; ``(0, 0)`` for none (from the start)."""
    if value is None or value == "":
        return 0, 0
    found = _CURSOR.fullmatch(str(value))
    if found is None:
        raise ValueError(f"not a timeline cursor: {value!r}")
    events, messages = int(found.group(1)), int(found.group(2))
    if max(events, messages) > _LARGEST_ROW:
        raise ValueError(f"not a timeline cursor: {value!r}")
    return events, messages


def _load(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _session(session_id: Any, commission_id: Any = None) -> dict[str, Any]:
    return {
        "type": "session",
        "session_id": session_id if isinstance(session_id, str) else None,
        "commission_id": commission_id if isinstance(commission_id, str) else None,
    }


_USER: dict[str, Any] = {"type": "user", "session_id": None, "commission_id": None}


def question_text(pending: str | None) -> tuple[str, str]:
    """Return what a pending question asks, and its kind; never what it would do or with what."""
    question = _load(pending)
    asked = _text(question.get("question"))
    if not asked:
        items = question.get("questions")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            asked = _text(items[0].get("question"))
    if not asked:
        # A request for approval says what kind of thing it may do (and, of
        # an MCP server, which), never what with: that is its ``detail``.
        asked = ":".join(part for part in (_text(question.get("category")), _text(question.get("key"))) if part)
    # As text SQLite can store: half of a surrogate pair, escaped in the
    # JSON, would fail the write of the move it is kept with.
    return _storable(asked), _storable(_text(question.get("kind")))


def _storable(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8")


def _filters(project: str | None, lanlan_name: str | None, project_col: str, owner_col: str) -> tuple[str, list[Any]]:
    clauses, values = [], []
    if project is not None:
        clauses.append(f"{project_col} = ?")
        values.append(project)
    if lanlan_name is not None:
        clauses.append(f"{owner_col} = ?")
        values.append(lanlan_name)
    return "".join(f" AND {clause}" for clause in clauses), values


def _event_rows(
    conn: sqlite3.Connection, after: int, count: int, project: str | None, lanlan_name: str | None
) -> list[sqlite3.Row]:
    where, values = _filters(project, lanlan_name, "c.project", "c.lanlan_name")
    types = ", ".join("?" for _ in _EVENT_TYPES)
    return conn.execute(
        "SELECT e.rowid AS number, e.commission_id, e.seq, e.ts, e.type, e.payload, "
        "c.session_id, c.project, c.lanlan_name, c.parent_id, c.source, c.title "
        "FROM events e JOIN commissions c ON c.id = e.commission_id "
        f"WHERE e.rowid > ? AND e.type IN ({types}) "
        # A question one session asks another is told by its message.
        "AND NOT (e.type = 'subagent_started' AND "
        "CASE WHEN json_valid(e.payload) THEN json_extract(e.payload, '$.asks') END IS NOT NULL) "
        # Back to waiting for the user with nothing asked: an answer that was
        # not taken is being handed over again (``_answer_untaken``).
        "AND NOT COALESCE(e.type = 'status' AND CASE WHEN json_valid(e.payload) THEN "
        "json_extract(e.payload, '$.to') = 'waiting_user' AND json_type(e.payload, '$.question') IS NULL END, 0)"
        f"{where} ORDER BY e.rowid LIMIT ?",
        [after, *_EVENT_TYPES, *values, count],
    ).fetchall()


def _message_rows(
    conn: sqlite3.Connection, after: int, count: int, project: str | None, lanlan_name: str | None
) -> list[sqlite3.Row]:
    clauses, values = [], []
    if project is not None:
        clauses.append("(sf.project = ? OR st.project = ?)")
        values += [project, project]
    if lanlan_name is not None:
        clauses.append("(sf.lanlan_name = ? OR st.lanlan_name = ?)")
        values += [lanlan_name, lanlan_name]
    where = "".join(f" AND {clause}" for clause in clauses)
    return conn.execute(
        "SELECT m.id AS number, m.from_session, m.from_commission, m.to_session, m.kind, m.body, "
        "m.notify_when_done, m.status, m.created_at, m.delivered_at, m.delivered_to, "
        "sf.project AS from_project, sf.lanlan_name AS from_owner, "
        "st.project AS to_project, st.lanlan_name AS to_owner "
        "FROM messages m LEFT JOIN sessions sf ON sf.id = m.from_session "
        "LEFT JOIN sessions st ON st.id = m.to_session WHERE m.id > ? "
        # A question that was not asked after all (its copy could not be made).
        f"AND NOT (m.kind = 'ask' AND m.status = 'void'){where} ORDER BY m.id LIMIT ?",
        [after, *values, count],
    ).fetchall()


def _latest(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        "SELECT (SELECT COALESCE(MAX(rowid), 0) FROM events) AS events, "
        "(SELECT COALESCE(MAX(id), 0) FROM messages) AS messages"
    ).fetchone()
    return int(row["events"]), int(row["messages"])


def _merge(events: list[sqlite3.Row], messages: list[sqlite3.Row], limit: int) -> list[tuple[int, sqlite3.Row]]:
    """Take up to ``limit`` rows of the two, each table in its own order, the earlier of the two heads first."""
    taken: list[tuple[int, sqlite3.Row]] = []
    at_event = at_message = 0
    while len(taken) < limit and (at_event < len(events) or at_message < len(messages)):
        if at_message >= len(messages):
            source = _EVENTS
        elif at_event >= len(events):
            source = _MESSAGES
        else:
            # On the same time, the event first: tables in a fixed order.
            source = _EVENTS if events[at_event]["ts"] <= messages[at_message]["created_at"] else _MESSAGES
        if source == _EVENTS:
            taken.append((_EVENTS, events[at_event]))
            at_event += 1
        else:
            taken.append((_MESSAGES, messages[at_message]))
            at_message += 1
    return taken


def _sessions_of(conn: sqlite3.Connection, commission_ids: set[str]) -> dict[str, str | None]:
    ids = sorted(commission_ids)
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(f"SELECT id, session_id FROM commissions WHERE id IN ({marks})", ids).fetchall()
    return {row["id"]: row["session_id"] for row in rows}


def _wake_reasons(conn: sqlite3.Connection, wake_ids: set[int]) -> dict[int, str]:
    ids = sorted(wake_ids)
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(f"SELECT id, reason FROM wake_requests WHERE id IN ({marks})", ids).fetchall()
    return {int(row["id"]): str(row["reason"]) for row in rows}


def _event_entry(
    row: sqlite3.Row,
    payload: dict[str, Any],
    sessions: dict[str, str | None],
    reasons: dict[int, str],
) -> dict[str, Any]:
    kind, text, peer, detail = row["type"], "", None, {}
    if kind == "created":
        kind, text, detail = "filed", _text(row["title"]), {"source": row["source"]}
        if row["parent_id"]:
            peer = _session(sessions.get(row["parent_id"]), row["parent_id"])
    elif kind == "filed_under_project":
        text, detail = _text(row["title"]), {"project": _text(payload.get("project"))}
    elif kind == "queued":
        behind = payload.get("behind") if isinstance(payload.get("behind"), str) else None
        detail = {"from": None, "ahead": _number(payload.get("ahead")), "behind": behind}
        if behind:
            peer = _session(sessions.get(behind), behind)
    elif kind == "status":
        to, came_from = _text(payload.get("to")), _text(payload.get("from"))
        if to == "working":
            kind, detail = "started", {"from": came_from}
        elif to == "queued":
            kind, detail = "queued", {"from": came_from, "ahead": None, "behind": None}
        elif to == "waiting_user":
            # What it asked was put with the move (``Ledger._transition``).
            kind, text, peer = "asked_user", _text(payload.get("question")), dict(_USER)
            detail = {"question_kind": _text(payload.get("question_kind"))}
        else:
            kind, text, detail = "ended", _text(payload.get("reason")), {"outcome": to}
            if row["source"] in ("task_agent", "ask"):
                kind = "child_returned" if row["source"] == "task_agent" else "ask_answered"
                peer = _session(sessions.get(row["parent_id"]), row["parent_id"])
    elif kind in ("answered", "told"):
        text, peer = _text(payload.get("text")), dict(_USER)
    elif kind == "report":
        kind, text, detail = "reported", _text(payload.get("summary")), {"status": _text(payload.get("status"))}
    elif kind == "subagent_started":
        child = payload.get("commission_id")
        kind, text = "child_started", _text(payload.get("name"))
        peer = _session(sessions.get(child) if isinstance(child, str) else None, child)
        # Its preset (the role it was started in), when one was chosen.
        detail = {"preset": _text(payload.get("preset")) or None}
    elif kind == "carried_on_in_copy":
        kind, peer = "into_copy", _session(payload.get("of"))
        detail = {"copy_session_id": payload.get("session_id") if isinstance(payload.get("session_id"), str) else None}
    elif kind == "handed_back":
        peer = _session(payload.get("to"))
        detail = {"copy_session_id": payload.get("copy") if isinstance(payload.get("copy"), str) else None}
    elif kind in ("not_carried_on", "back_from_copy"):
        main = payload.get("of") if kind == "not_carried_on" else payload.get("to")
        kind, peer = "copy_called_off" if kind == "not_carried_on" else kind, _session(main)
        detail = {"copy_session_id": payload.get("copy") if isinstance(payload.get("copy"), str) else None}
    elif kind == "told_of_copies":
        # What it was told is the host's framing for the model, not a line to show.
        kind = "copies_reported"
    elif kind == "resting":
        detail = {"until": _number(payload.get("until"))}
    elif kind == "wake_set":
        text, detail = _text(payload.get("note")), {"due_at": _number(payload.get("due_at"))}
    elif kind == "woken":
        ids = _wake_ids(payload)
        detail = {"count": len(ids), "reasons": sorted({reasons[item] for item in ids if item in reasons})}
    return {
        "kind": kind,
        "at": row["ts"],
        "lanlan_name": row["lanlan_name"],
        "project": row["project"],
        "session_id": row["session_id"],
        "commission_id": row["commission_id"],
        "peer": peer,
        "text": text,
        "detail": detail,
    }


def _wake_ids(payload: dict[str, Any]) -> list[int]:
    items = payload.get("wake_ids")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, int) and not isinstance(item, bool) and 0 < item <= _LARGEST_ROW]


def _message_entry(row: sqlite3.Row) -> dict[str, Any]:
    sender_known = row["from_owner"] is not None
    return {
        "kind": _MESSAGE_KINDS.get(row["kind"], row["kind"]),
        "at": row["created_at"],
        "lanlan_name": row["from_owner"] if sender_known else row["to_owner"],
        "project": row["from_project"] if sender_known else row["to_project"],
        "session_id": row["from_session"],
        "commission_id": row["from_commission"],
        "peer": _session(row["to_session"], row["delivered_to"]),
        "text": _text(row["body"]),
        "detail": {
            "status": row["status"],
            "delivered_at": row["delivered_at"],
            "notify_when_done": bool(row["notify_when_done"]),
        },
    }


def read_timeline(
    conn: sqlite3.Connection,
    *,
    project: str | None,
    lanlan_name: str | None,
    after: str | None,
    limit: int,
) -> dict[str, Any]:
    """Return a page of the timeline after the cursor ``after``; see the module.

    ``{"entries": [...], "next": cursor, "more": bool, "latest": cursor}``.
    ``next`` is to be passed as ``after`` for the page after this one;
    ``more`` says whether one with entries is there now; ``latest`` stands
    after everything on record now, for a reader that wants only what
    comes from here on. Each entry has its own ``cursor``: the page after
    it starts with the entry that follows it. Texts are as recorded: the
    caller shortens them, off the ledger's thread.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    event_after, message_after = parse_cursor(after)
    latest = _latest(conn)
    # One more than a page of each: what is left over says there is more.
    events = _event_rows(conn, event_after, limit + 1, project, lanlan_name)
    messages = _message_rows(conn, message_after, limit + 1, project, lanlan_name)
    taken = _merge(events, messages, limit)
    event_rows = [row for source, row in taken if source == _EVENTS]
    payloads = {int(row["number"]): _load(row["payload"]) for row in event_rows}
    named: set[str] = set()
    wakes: set[int] = set()
    for row in event_rows:
        payload = payloads[int(row["number"])]
        for key in ("behind", "commission_id"):
            if isinstance(payload.get(key), str):
                named.add(payload[key])
        if row["parent_id"]:
            named.add(row["parent_id"])
        wakes.update(_wake_ids(payload))
    sessions = _sessions_of(conn, named)
    reasons = _wake_reasons(conn, wakes)
    entries: list[dict[str, Any]] = []
    for source, row in taken:
        if source == _EVENTS:
            event_after = int(row["number"])
            entry = _event_entry(row, payloads[event_after], sessions, reasons)
        else:
            message_after = int(row["number"])
            entry = _message_entry(row)
        entries.append({"cursor": cursor(event_after, message_after), **entry})
    used_events = len(event_rows)
    used_messages = len(taken) - used_events
    # A table read to its end within this page: nothing that is to be shown
    # is left in it up to its last row now, so the next page need not read
    # those rows again (a filter may pass over many of them).
    if used_events == len(events):
        event_after = max(event_after, latest[0])
    if used_messages == len(messages):
        message_after = max(message_after, latest[1])
    return {
        "entries": entries,
        "next": cursor(event_after, message_after),
        "more": used_events < len(events) or used_messages < len(messages),
        "latest": cursor(*latest),
    }


def _last_of(conn: sqlite3.Connection, ids: list[str], kind: str) -> dict[str, tuple[int, float]]:
    """Return the number and time of the last event of a kind of each of these commissions.

    The events of each are gone through once, for the last number, and
    that row is then read by its key.
    """
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT e.commission_id, e.seq, e.ts FROM (SELECT commission_id, MAX(seq) AS seq FROM events "
        f"WHERE type = ? AND commission_id IN ({marks}) GROUP BY commission_id) last "
        "JOIN events e ON e.commission_id = last.commission_id AND e.seq = last.seq",
        [kind, *ids],
    ).fetchall()
    return {row["commission_id"]: (int(row["seq"]), float(row["ts"])) for row in rows}


def _wait(
    kind: str,
    waiter: sqlite3.Row | dict[str, Any],
    target: dict[str, Any],
    since: float,
    text: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "waiter": _session(waiter["session_id"], waiter["id"]),
        "target": target,
        "since": since,
        "text": text,
        "lanlan_name": waiter["lanlan_name"],
        "projects": sorted({value for value in (waiter["project"], target.pop("project", None)) if value}),
        **extra,
    }


def read_waits(
    conn: sqlite3.Connection,
    *,
    project: str | None,
    lanlan_name: str | None,
    limit: int,
) -> dict[str, Any]:
    """Return who waits for whom now; see the module.

    ``{"waits": [...], "more": bool}``, the longest waiting first, at most
    ``limit`` of them; ``more`` says some were left out. Texts are as
    recorded, as in ``read_timeline``.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    marks = ", ".join("?" for _ in _UNDER_WAY)
    under_way = conn.execute(
        "SELECT id, session_id, lanlan_name, project, status, resting_since, parent_id, source, title, "
        f"dispatch_text, pending_question, created_at FROM commissions WHERE status IN ({marks})",
        _UNDER_WAY,
    ).fetchall()
    by_id = {row["id"]: row for row in under_way}
    waits: list[dict[str, Any]] = []

    queued = [row["id"] for row in under_way if row["status"] == "queued"]
    # Back to waiting for the user with nothing asked is an answer that was
    # not taken being handed over again: nobody is asked anything.
    asking = [row["id"] for row in under_way if row["status"] == "waiting_user" and row["pending_question"]]
    status_of = _last_of(conn, queued + asking, "status")
    queued_of = _last_of(conn, queued, "queued")
    # Who keeps each session now: a commission worked on in it, or waiting
    # for the user there. One that rests, or is queued itself, let it go.
    # (Whom one was queued behind may be over, and another took the session.)
    holders: dict[str, list[sqlite3.Row]] = {}
    for row in under_way:
        if row["session_id"] and row["status"] != "queued" and row["resting_since"] is None:
            holders.setdefault(row["session_id"], []).append(row)
    for commission_id in queued:
        row = by_id[commission_id]
        last_status = status_of.get(commission_id)
        last_queued = queued_of.get(commission_id)
        holder = min(holders.get(row["session_id"], []), key=lambda item: (item["created_at"], item["id"]), default=None)
        # Since the last of the two that was written (not the latest time:
        # the clock may have gone back), or since it was filed.
        last = max((item for item in (last_status, last_queued) if item is not None), key=lambda item: item[0], default=None)
        since = last[1] if last is not None else row["created_at"]
        if holder is not None:
            target = {**_session(holder["session_id"], holder["id"]), "project": holder["project"]}
            waits.append(_wait("queue", row, target, since, _text(holder["title"])))
        else:
            waits.append(_wait("place", row, {"type": "place", "session_id": None, "commission_id": None}, since, ""))
    for commission_id in asking:
        row = by_id[commission_id]
        asked, question_kind = question_text(row["pending_question"])
        since = status_of[commission_id][1] if commission_id in status_of else row["created_at"]
        waits.append(_wait("user", row, dict(_USER), since, asked, question_kind=question_kind))

    copies: set[str] = {row["session_id"] for row in under_way if row["source"] == "ask" and row["session_id"]}
    ids = sorted(copies)
    found = conn.execute(f"SELECT id, forked_from FROM sessions WHERE id IN ({', '.join('?' for _ in ids)})", ids)
    forked_from: dict[str, str | None] = {item["id"]: item["forked_from"] for item in found.fetchall()}
    for row in under_way:
        parent = by_id.get(row["parent_id"])
        if parent is None:
            continue
        if row["source"] == "ask":
            target = {**_session(forked_from.get(row["session_id"])), "project": row["project"]}
            waits.append(
                _wait("ask", parent, target, row["created_at"], _text(row["dispatch_text"]), copy_session_id=row["session_id"])
            )
        else:
            # A task agent started by another one (only those and questions have a parent).
            target = {**_session(row["session_id"], row["id"]), "project": row["project"]}
            waits.append(_wait("child", parent, target, row["created_at"], _text(row["title"])))

    notes = conn.execute(
        "SELECT m.id, m.from_session, m.from_commission, m.to_session, m.delivered_to, m.body, m.created_at, "
        "w.due_at, st.project AS to_project FROM messages m "
        "JOIN wake_requests w ON w.message_id = m.id AND w.reason = 'notice_expiry' AND w.status = 'pending' "
        "LEFT JOIN sessions st ON st.id = m.to_session "
        "WHERE m.notified_at IS NULL AND m.status != 'void'"
    ).fetchall()
    for note in notes:
        sender = by_id.get(note["from_commission"])
        if sender is None:
            continue
        target = {**_session(note["to_session"], note["delivered_to"]), "project": note["to_project"]}
        waits.append(
            _wait("notice", sender, target, note["created_at"], _text(note["body"]), message_id=note["id"], expires_at=note["due_at"])
        )

    if project is not None:
        waits = [item for item in waits if project in item["projects"]]
    if lanlan_name is not None:
        waits = [item for item in waits if item["lanlan_name"] == lanlan_name]
    waits.sort(key=lambda item: (item["since"], WAIT_KINDS.index(item["kind"]), item["waiter"]["commission_id"]))
    return {"waits": waits[:limit], "more": len(waits) > limit}
