# -*- coding: utf-8 -*-
"""
EventLog.compact_applied_history: bounded retention for events.ndjson.

The journal used to grow forever (writers only append, the old seed-based
compaction was never wired and was unsafe). The invariant pinned here is
the one every reader depends on: before and after a compaction,
read_since(read_sentinel(name)) returns the same events, the sentinel file
is untouched, and every skip leaves the journal byte-for-byte as it was.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from memory import event_log as event_log_module
from memory.event_log import (
    EVT_LONG_TERM_MEMORY_REPLACED,
    EVT_PERSONA_EVIDENCE_UPDATED,
    EVT_REFLECTION_EVIDENCE_UPDATED,
    EVT_REFLECTION_STATE_CHANGED,
    EventLog,
    Reconciler,
)

NAME = "小天"
NOW = datetime(2026, 10, 30, 12, 0, 0)
SEPARATORS = (" ", " ", "\x85")


# ── helpers ──────────────────────────────────────────────────────────


def _fresh_log(memory_dir: str) -> EventLog:
    cm = MagicMock()
    cm.memory_dir = memory_dir
    cm.load_root_state.return_value = {"mode": "normal"}
    with patch("memory.event_log.get_config_manager", return_value=cm):
        log = EventLog()
    log._config_manager = cm
    return log


@contextmanager
def _knobs(*, trigger_bytes: int = 1, days: int = 0, cap: int = 0):
    with patch.object(event_log_module, "_COMPACT_TRIGGER_BYTES", trigger_bytes), \
            patch.object(event_log_module, "_RETAIN_APPLIED_DAYS", days), \
            patch.object(event_log_module, "_RETAIN_APPLIED_MAX_BYTES", cap):
        yield


def _char_dir(root) -> str:
    return os.path.join(str(root), NAME)


def _events_path(root) -> str:
    return os.path.join(_char_dir(root), "events.ndjson")


def _sentinel_path(root) -> str:
    return os.path.join(_char_dir(root), "events_applied.json")


def _line(event_id, *, days_ago: float = 40.0, type_: str = EVT_REFLECTION_EVIDENCE_UPDATED,
          payload: dict | None = None, ts: str | None = None) -> str:
    return json.dumps({
        "event_id": event_id,
        "type": type_,
        "ts": ts if ts is not None else (NOW - timedelta(days=days_ago)).isoformat(),
        "payload": payload if payload is not None else {"pad": "x" * 40},
    }, ensure_ascii=False)


def _write_journal(root, lines: list[str], *, final_newline: bool = True) -> None:
    os.makedirs(_char_dir(root), exist_ok=True)
    body = "\n".join(lines) + ("\n" if final_newline else "")
    with open(_events_path(root), "wb") as f:
        f.write(body.encode("utf-8"))


def _write_sentinel(root, event_id) -> None:
    os.makedirs(_char_dir(root), exist_ok=True)
    with open(_sentinel_path(root), "w", encoding="utf-8") as f:
        json.dump({"last_applied_event_id": event_id, "ts": "t"}, f)


def _read_bytes(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _journal_lines(root) -> list[str]:
    with open(_events_path(root), encoding="utf-8") as f:
        return [line[:-1] if line.endswith("\n") else line for line in f]


def _tail_ids(log: EventLog) -> list:
    return [r["event_id"] for r in log.read_since(NAME, log.read_sentinel(NAME))]


def _record(log: EventLog, payload: dict, name: str = NAME) -> str:
    view: dict = {}
    return log.record_and_save(
        name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
        sync_load_view=lambda n: view,
        sync_mutate_view=lambda v: None,
        sync_save_view=lambda n, v: None,
    )


def test_retention_defaults_are_the_documented_values():
    """The owner decision is still pending; these are the shipped defaults."""
    assert event_log_module._RETAIN_APPLIED_DAYS == 7
    assert event_log_module._RETAIN_APPLIED_MAX_BYTES == 256 * 1024
    assert event_log_module._COMPACT_TRIGGER_BYTES == 512 * 1024
    assert event_log_module._COMPACT_TRIGGER_AGE_DAYS == 30


# ── basic path ───────────────────────────────────────────────────────


def test_compaction_keeps_only_the_anchor_when_the_window_is_zero(tmp_path):
    log = _fresh_log(str(tmp_path))
    for i in range(12):
        _record(log, {"reflection_id": f"r{i}", "reinforcement": float(i)})
    last_line = _journal_lines(tmp_path)[-1]
    sentinel_before = _read_bytes(_sentinel_path(tmp_path))
    sentinel_mtime = os.stat(_sentinel_path(tmp_path)).st_mtime_ns
    assert _tail_ids(log) == []

    with _knobs():
        result = log.compact_applied_history(NAME)

    assert result.outcome == "compacted"
    assert result.dropped_lines == 11
    assert result.dropped_bytes > 0
    assert _journal_lines(tmp_path) == [last_line], "锚点那一行必须逐字保留"
    assert _read_bytes(_sentinel_path(tmp_path)) == sentinel_before
    assert os.stat(_sentinel_path(tmp_path)).st_mtime_ns == sentinel_mtime, \
        "events_applied.json 被重写了"
    assert _tail_ids(log) == []


def test_the_tail_after_the_anchor_survives_verbatim(tmp_path):
    """Corrupt and blank lines after the anchor stay, the order stays, and a
    missing final newline is added (so the next append starts a new line)."""
    log = _fresh_log(str(tmp_path))
    ids = [str(uuid.uuid4()) for _ in range(5)]
    tail = [
        _line(ids[1], days_ago=40),
        "{corrupt",
        "",
        _line(ids[2], days_ago=1),
        "[1, 2]",
        '{"no_event_id": true}',
        _line(ids[3], days_ago=0.1),
    ]
    _write_journal(tmp_path, [_line(ids[0], days_ago=50)] + tail, final_newline=False)
    _write_sentinel(tmp_path, ids[1])
    before = _tail_ids(log)
    assert before == [ids[2], ids[3]]

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert result.pending_tail == 2
    assert _journal_lines(tmp_path) == tail
    assert _read_bytes(_events_path(tmp_path)).endswith(os.linesep.encode())
    assert _tail_ids(log) == before


def test_a_crlf_journal_from_windows_compacts_like_an_lf_one(tmp_path):
    """Every journal written on Windows is CRLF-terminated (text-mode append),
    and a cloud save can carry one to another OS where new appends are LF.
    The anchor match, the tail, the self-check and the next append must not
    depend on the terminator. CI runs on Linux, where the writer never
    produces CRLF, so the bytes are written by hand here."""
    log = _fresh_log(str(tmp_path))
    ids = [str(uuid.uuid4()) for _ in range(6)]
    head = [_line(ids[0], days_ago=60), _line(ids[1], days_ago=55)]
    tail = [
        _line(ids[2], days_ago=50),
        "{corrupt",
        _line(ids[3], days_ago=2),
        _line(ids[4], days_ago=1),
    ]
    os.makedirs(_char_dir(tmp_path), exist_ok=True)
    crlf_part = "\r\n".join(head + tail[:3]) + "\r\n"
    lf_part = tail[3] + "\n"
    with open(_events_path(tmp_path), "wb") as f:
        f.write((crlf_part + lf_part).encode("utf-8"))
    _write_sentinel(tmp_path, ids[2])
    sentinel_bytes = _read_bytes(_sentinel_path(tmp_path))
    before = _tail_ids(log)
    assert before == [ids[3], ids[4]]

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert result.dropped_lines == len(head)
    assert result.pending_tail == 2
    assert _journal_lines(tmp_path) == tail
    body = _read_bytes(_events_path(tmp_path))
    assert b"\r\r" not in body
    assert body.endswith(os.linesep.encode())
    assert _read_bytes(_sentinel_path(tmp_path)) == sentinel_bytes
    assert _tail_ids(log) == before

    new_id = log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"reflection_id": "r"})
    assert _tail_ids(log) == before + [new_id]


# ── skip cases leave the journal untouched ───────────────────────────


def _skip_case_journal(tmp_path) -> list[str]:
    # A record whose event_id is null sits in the middle: an implementation
    # that read the sentinel leniently (None) would anchor on it and cut.
    lines = [
        _line("e1", days_ago=60),
        _line(None, days_ago=55),
        _line("e2", days_ago=50),
        _line("e3", days_ago=45),
    ]
    _write_journal(tmp_path, lines)
    return lines


@pytest.mark.parametrize("case, expected", [
    ("absent", "sentinel_absent"),
    ("open_raises", "sentinel_unreadable"),
    ("bad_utf8", "sentinel_unreadable"),
    ("corrupt_json", "sentinel_malformed"),
    ("not_a_dict", "sentinel_malformed"),
    ("id_not_str", "sentinel_malformed"),
    ("null", "sentinel_null"),
    ("not_in_body", "sentinel_not_found"),
])
def test_unusable_sentinel_never_touches_the_journal(tmp_path, monkeypatch, case, expected):
    log = _fresh_log(str(tmp_path))
    _skip_case_journal(tmp_path)
    sp = _sentinel_path(tmp_path)
    if case == "open_raises":
        _write_sentinel(tmp_path, "e3")
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith("events_applied.json"):
                raise OSError("simulated transient read failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(event_log_module, "open", fake_open, raising=False)
    elif case == "bad_utf8":
        with open(sp, "wb") as f:
            f.write(b'{"last_applied_event_id": "\xff\xfe"}')
    elif case == "corrupt_json":
        with open(sp, "w", encoding="utf-8") as f:
            f.write("{not json")
    elif case == "not_a_dict":
        with open(sp, "w", encoding="utf-8") as f:
            f.write('["e3"]')
    elif case == "id_not_str":
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"last_applied_event_id": 3}, f)
    elif case == "null":
        _write_sentinel(tmp_path, None)
    elif case == "not_in_body":
        _write_sentinel(tmp_path, "ghost")
    body_before = _read_bytes(_events_path(tmp_path))
    sentinel_before = _read_bytes(sp)

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == expected
    assert _read_bytes(_events_path(tmp_path)) == body_before
    assert _read_bytes(sp) == sentinel_before


def test_duplicate_ids_anchor_on_the_first_occurrence_like_read_since(tmp_path):
    log = _fresh_log(str(tmp_path))
    lines = [
        _line("old", days_ago=60),
        _line("A", days_ago=50),
        _line("B", days_ago=40),
        _line("A", days_ago=30),
        _line("C", days_ago=20),
    ]
    _write_journal(tmp_path, lines)
    _write_sentinel(tmp_path, "A")
    before = _tail_ids(log)
    assert before == ["B", "A", "C"]

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert _journal_lines(tmp_path) == lines[1:]
    assert _tail_ids(log) == before


# ── a reconciliation round in flight ─────────────────────────────────


@pytest.mark.asyncio
async def test_compaction_never_cuts_under_a_reconciliation_round(tmp_path):
    """Regression for research case S2 (events lost without the round guard).

    A round releases the character lock between events. A live write that
    lands between two of them moves the sentinel to the journal end, so the
    round's next compare-and-set fails and it re-reads the journal from its
    OWN last-applied event. If compaction had dropped that line in between,
    the re-read would fall back to a full replay of what is left and the
    events the round still owed (3 and 4) would never be applied.
    """
    log = _fresh_log(str(tmp_path))
    rec = Reconciler(log)
    ids = [log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": i}) for i in range(5)]
    log.advance_sentinel(NAME, ids[0])
    applied: list[int] = []

    def handler(_name, payload):
        applied.append(payload["i"])
        return True

    rec.register(EVT_REFLECTION_EVIDENCE_UPDATED, handler)
    real_apply = log.aapply_and_advance
    compactions: list = []
    view: dict = {}

    async def apply_then_interleave(name, event_id, apply_fn, *, expected_sentinel, advance=True):
        changed = await real_apply(
            name, event_id, apply_fn, expected_sentinel=expected_sentinel, advance=advance,
        )
        if event_id == ids[2] and not compactions:
            def live_write_then_compact():
                log.record_and_save(
                    name, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": 99},
                    sync_load_view=lambda n: view,
                    sync_mutate_view=lambda v: None,
                    sync_save_view=lambda n, v: None,
                )
                with _knobs():
                    return log.compact_applied_history(name)

            compactions.append(await asyncio.to_thread(live_write_then_compact))
        return changed

    with patch.object(log, "aapply_and_advance", apply_then_interleave):
        await asyncio.wait_for(rec.areconcile(NAME), 10)

    # Loss first: without the guard this is the assertion that fails
    # (applied == [1, 2, 99]; events 3 and 4 are gone for good).
    assert applied == [1, 2, 3, 4, 99], f"本轮欠下的事件丢了: {applied}"
    assert compactions and compactions[0].outcome == "replay_in_progress"
    assert not log._replay_round_active(NAME)


# ── a round that paused (H4) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_paused_round_keeps_the_character_out_of_compaction(tmp_path):
    """Pause, then a live write orphans the paused tail (known sentinel
    limitation). This process must not delete the orphan; a later process
    (fresh EventLog) compacts normally, which removes it."""
    log = _fresh_log(str(tmp_path))
    rec = Reconciler(log)
    rec.register(EVT_REFLECTION_EVIDENCE_UPDATED, lambda n, p: True)
    _write_journal(tmp_path, [
        _line("old", days_ago=60),
        _line("anchor", days_ago=50),
        _line("unhandled", days_ago=49, type_=EVT_PERSONA_EVIDENCE_UPDATED),
    ])
    _write_sentinel(tmp_path, "anchor")

    assert await rec.areconcile(NAME) == 0
    assert log.replay_paused_in_process(NAME)
    _record(log, {"i": "live"})  # sentinel jumps past the unapplied event
    assert _tail_ids(log) == []
    body = _read_bytes(_events_path(tmp_path))

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "replay_paused"
    assert _read_bytes(_events_path(tmp_path)) == body

    # Sticky: a later round that drains does not clear it.
    assert await rec.areconcile(NAME) == 0
    assert log.replay_paused_in_process(NAME)

    next_process = _fresh_log(str(tmp_path))
    with _knobs():
        assert next_process.compact_applied_history(NAME, now=NOW).outcome == "compacted"
    assert not any('"unhandled"' in line for line in _journal_lines(tmp_path))


@pytest.mark.asyncio
async def test_drained_rounds_do_not_mark_and_failed_rounds_do(tmp_path):
    log = _fresh_log(str(tmp_path))
    rec = Reconciler(log)
    rec.register(EVT_REFLECTION_EVIDENCE_UPDATED, lambda n, p: True)
    log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": 1})
    assert await rec.areconcile(NAME) == 1
    assert not log.replay_paused_in_process(NAME)

    async def boom(name, after):
        raise OSError("journal unreadable")

    with patch.object(log, "aread_since", boom):
        with pytest.raises(OSError):
            await rec.areconcile(NAME)
    assert log.replay_paused_in_process(NAME), "抛异常的一轮必须记为暂停"
    assert not log._replay_round_active(NAME), "计数没有归零"


@pytest.mark.asyncio
async def test_a_handler_failure_marks_the_round_paused(tmp_path):
    log = _fresh_log(str(tmp_path))
    rec = Reconciler(log)

    def failing(_n, _p):
        raise RuntimeError("view drifted")

    rec.register(EVT_REFLECTION_EVIDENCE_UPDATED, failing)
    log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": 1})
    assert await rec.areconcile(NAME) == 0
    assert log.replay_paused_in_process(NAME)


# ── concurrency ──────────────────────────────────────────────────────


def test_compaction_under_concurrent_writers_never_loses_the_anchor(tmp_path):
    log = _fresh_log(str(tmp_path))
    written: list[str] = []
    written_lock = threading.Lock()
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer(k: int):
        n = 0
        while not stop.is_set():
            try:
                eid = _record(log, {"writer": k, "n": n, "pad": "z" * 300})
            except Exception as e:  # noqa: BLE001 - surfaced by the assert below
                errors.append(e)
                return
            with written_lock:
                written.append(eid)
            n += 1

    threads = [threading.Thread(target=writer, args=(k,), daemon=True) for k in range(3)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5.0
    while not written and time.monotonic() < deadline:
        time.sleep(0.001)
    assert written, "写线程 5s 内一条都没写出，并发前提不成立"

    outcomes: dict[str, int] = {}
    mark = 0
    end = time.monotonic() + 3.0
    try:
        with _knobs():
            while time.monotonic() < end:
                result = log.compact_applied_history(NAME, now=datetime.now() + timedelta(days=60))
                outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
                with written_lock:
                    mark = len(written)
                with log._get_lock(NAME):
                    sentinel = log.read_sentinel(NAME)
                    body_ids = [r["event_id"] for r in log._read_all_records(_events_path(tmp_path))]
                assert sentinel in body_ids, "压缩后哨兵指向的事件不在正文里"
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=10)

    assert not errors, errors[:3]
    assert outcomes.get("compacted", 0) > 0, outcomes
    with log._get_lock(NAME):
        body_ids = [r["event_id"] for r in log._read_all_records(_events_path(tmp_path))]
        sentinel = log.read_sentinel(NAME)
    assert body_ids[-1] == sentinel
    missing = [eid for eid in written[mark:] if eid not in set(body_ids)]
    assert not missing, f"最后一次压缩之后写入的事件丢了 {len(missing)} 条"


# ── property test ────────────────────────────────────────────────────


def _random_text(rng: random.Random) -> str:
    chunks = []
    for _ in range(rng.randint(0, 4)):
        chunks.append("y" * rng.randint(0, 60))
        if rng.random() < 0.5:
            chunks.append(rng.choice(SEPARATORS))
    return "".join(chunks)


def _oracle_records(lines: list[str]) -> list[dict]:
    """Records as replay defines them, parsed from the generated lines --
    independent of how the file is split when it is read back."""
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(rec, dict) and "event_id" in rec:
            out.append(rec)
    return out


def _random_case(rng: random.Random, root: str, name: str, now: datetime):
    char_dir = os.path.join(root, name)
    os.makedirs(char_dir)
    lines: list[str] = []
    ids: list[str] = []
    for _ in range(rng.randint(1, 40)):
        if rng.random() < 0.1:
            lines.append(rng.choice([
                "{corrupt", "", "   ", "[1,2]", '{"no_id": 1}', '{"event_id": "torn',
            ]))
            continue
        eid = rng.choice(ids) if ids and rng.random() < 0.05 else str(uuid.uuid4())
        ids.append(eid)
        rec = {
            "event_id": eid,
            "type": rng.choice([
                EVT_REFLECTION_EVIDENCE_UPDATED, EVT_PERSONA_EVIDENCE_UPDATED,
                EVT_LONG_TERM_MEMORY_REPLACED, EVT_REFLECTION_STATE_CHANGED,
            ]),
            "ts": (now - timedelta(days=rng.uniform(0, 60))).isoformat(),
            "payload": {"x": _random_text(rng), "pin": rng.random() < 0.1},
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
    body = "\n".join(lines) + ("\n" if rng.random() < 0.8 else "")
    with open(os.path.join(char_dir, "events.ndjson"), "wb") as f:
        f.write(body.encode("utf-8"))

    sentinel_path = os.path.join(char_dir, "events_applied.json")
    mode = rng.random()
    state, sid = "ok", None
    if mode < 0.06 or not ids:
        state = "absent"
    elif mode < 0.10:
        state = "malformed"
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("{bad")
    elif mode < 0.14:
        state = "null"
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump({"last_applied_event_id": None}, f)
    elif mode < 0.18:
        sid = str(uuid.uuid4())
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump({"last_applied_event_id": sid}, f)
    else:
        sid = rng.choice(ids)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump({"last_applied_event_id": sid}, f)
    return lines, state, sid


def _expected_tail(lines: list[str], state: str, sid) -> list:
    records = _oracle_records(lines)
    all_ids = [r["event_id"] for r in records]
    if state != "ok" or sid not in all_ids:
        return all_ids
    first = all_ids.index(sid)
    return all_ids[first + 1:]


def _pin_flag(record: dict) -> bool:
    payload = record.get("payload")
    return isinstance(payload, dict) and payload.get("pin") is True


def test_property_compaction_preserves_the_replay_tail(tmp_path):
    """>= 1000 random journals: corrupt lines, duplicate ids, missing final
    newline, every sentinel state, and U+2028 / U+2029 / U+0085 inside
    payload strings on tail lines and on the anchor line."""
    rng = random.Random(20261030)
    root = str(tmp_path)
    log = _fresh_log(root)
    outcomes: dict[str, int] = {}
    anchor_with_sep = 0
    tail_with_sep = 0
    for k in range(1000):
        name = f"p{k}"
        now = NOW
        lines, state, sid = _random_case(rng, root, name, now)
        char_dir = os.path.join(root, name)
        ev = os.path.join(char_dir, "events.ndjson")
        sp = os.path.join(char_dir, "events_applied.json")
        expected = _expected_tail(lines, state, sid)
        if state == "ok" and sid in [r["event_id"] for r in _oracle_records(lines)]:
            recs = _oracle_records(lines)
            first = [r["event_id"] for r in recs].index(sid)
            if any(s in recs[first]["payload"]["x"] for s in SEPARATORS):
                anchor_with_sep += 1
            if any(any(s in r["payload"]["x"] for s in SEPARATORS) for r in recs[first + 1:]):
                tail_with_sep += 1

        def tail_now():
            return [r["event_id"] for r in log.read_since(name, log.read_sentinel(name))]

        assert tail_now() == expected, (k, "读取路径本身就和独立解析的结果不一致")
        sentinel_before = _read_bytes(sp)
        body_before = _read_bytes(ev)
        with _knobs(trigger_bytes=0, days=rng.choice([0, 7, 14]),
                    cap=rng.choice([0, 500, 256 * 1024])):
            result = log.compact_applied_history(name, needs_journal_copy=_pin_flag, now=now)
            outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
            assert tail_now() == expected, (k, result)
            assert _read_bytes(sp) == sentinel_before, k
            if result.outcome != "compacted":
                assert _read_bytes(ev) == body_before, (k, result)
            body_once = _read_bytes(ev)
            again = log.compact_applied_history(name, needs_journal_copy=_pin_flag, now=now)
            assert again.outcome != "compacted", (k, result, again)
            assert _read_bytes(ev) == body_once, k
            assert tail_now() == expected, k
        shutil.rmtree(char_dir)

    assert outcomes.get("compacted", 0) > 300, outcomes
    for skip in ("sentinel_absent", "sentinel_null", "sentinel_malformed", "sentinel_not_found"):
        assert outcomes.get(skip, 0) > 10, outcomes
    assert anchor_with_sep >= 50 and tail_with_sep >= 50, (anchor_with_sep, tail_with_sep)


def test_line_separators_inside_payloads_do_not_split_records(tmp_path):
    """Deterministic H5 case: U+2028 on the anchor, U+2029 / U+0085 on tail lines."""
    log = _fresh_log(str(tmp_path))
    lines = [
        _line("old", days_ago=60, payload={"text": "a b"}),
        _line("anchor", days_ago=50, payload={"text": "before after"}),
        _line("t1", days_ago=40, payload={"text": "x y"}),
        _line("t2", days_ago=30, payload={"text": "p\x85q"}),
    ]
    _write_journal(tmp_path, lines)
    _write_sentinel(tmp_path, "anchor")
    assert _tail_ids(log) == ["t1", "t2"]

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert result.pending_tail == 2
    assert _journal_lines(tmp_path) == lines[1:]
    assert _tail_ids(log) == ["t1", "t2"]


# ── retention window ─────────────────────────────────────────────────


def _window_journal(tmp_path) -> dict[str, str]:
    aware_recent = (NOW - timedelta(days=1)).astimezone().astimezone(timezone.utc).isoformat()
    aware_old = (NOW - timedelta(days=8)).astimezone().astimezone(timezone.utc).isoformat()
    entries = {
        "d40": _line("d40", days_ago=40),
        "d8": _line("d8", days_ago=8),
        "aware_old": _line("aware_old", ts=aware_old),
        "d6": _line("d6", days_ago=6),
        "lt_ref": _line("lt_ref", days_ago=1, type_=EVT_LONG_TERM_MEMORY_REPLACED,
                        payload={"characterSyncId": "u", "inboxSha256": "0" * 64}),
        "lt_head": _line("lt_head", days_ago=1, type_=EVT_LONG_TERM_MEMORY_REPLACED,
                         payload={"head": {"completedAt": "x"}, "binding": {}, "epoch": [1, 2]}),
        "bad_ts": _line("bad_ts", ts="not-a-date"),
        "no_ts": json.dumps({"event_id": "no_ts", "type": EVT_REFLECTION_EVIDENCE_UPDATED,
                             "payload": {}}),
        "aware_recent": _line("aware_recent", ts=aware_recent),
        "d1": _line("d1", days_ago=1),
        "anchor": _line("anchor", days_ago=0.5),
    }
    _write_journal(tmp_path, list(entries.values()))
    _write_sentinel(tmp_path, "anchor")
    return entries


def _kept_ids(tmp_path) -> list[str]:
    return [json.loads(line)["event_id"] for line in _journal_lines(tmp_path) if line.strip()]


def test_default_window_keeps_seven_days_and_never_long_term_snapshots(tmp_path):
    log = _fresh_log(str(tmp_path))
    _window_journal(tmp_path)
    with patch.object(event_log_module, "_COMPACT_TRIGGER_BYTES", 1):
        result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "compacted"
    assert _kept_ids(tmp_path) == ["d6", "aware_recent", "d1", "anchor"]
    assert result.kept_before_anchor == 3


def test_window_byte_cap_evicts_the_oldest_first(tmp_path):
    log = _fresh_log(str(tmp_path))
    entries = _window_journal(tmp_path)
    cap = (len(entries["aware_recent"].encode()) + 1) + (len(entries["d1"].encode()) + 1)
    with _knobs(days=7, cap=cap):
        result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "compacted"
    assert _kept_ids(tmp_path) == ["aware_recent", "d1", "anchor"]


def test_zero_window_keeps_only_pinned_lines_and_the_anchor(tmp_path):
    log = _fresh_log(str(tmp_path))
    _window_journal(tmp_path)
    with _knobs(days=0, cap=0):
        result = log.compact_applied_history(
            NAME, now=NOW, needs_journal_copy=lambda rec: rec.get("event_id") == "d40",
        )
    assert result.outcome == "compacted"
    assert result.pinned == 1 and result.pinned_event_ids == ("d40",)
    assert _kept_ids(tmp_path) == ["d40", "anchor"], "钉住行必须排在锚点之前、保持原顺序"


def test_needs_journal_copy_errors_propagate_and_nothing_is_written(tmp_path):
    log = _fresh_log(str(tmp_path))
    _window_journal(tmp_path)
    body = _read_bytes(_events_path(tmp_path))

    def boom(_rec):
        raise RuntimeError("index failure")

    with _knobs(), pytest.raises(RuntimeError):
        log.compact_applied_history(NAME, now=NOW, needs_journal_copy=boom)
    assert _read_bytes(_events_path(tmp_path)) == body


# ── directories, fence, file identity, busy ──────────────────────────


def test_missing_character_dir_is_not_recreated(tmp_path):
    log = _fresh_log(str(tmp_path))
    with _knobs():
        result = log.compact_applied_history("ghost")
    assert result.outcome == "no_character_dir"
    assert not os.path.exists(os.path.join(str(tmp_path), "ghost"))


def test_character_dir_removed_just_before_the_write_is_not_recreated(tmp_path):
    """Workshop unsubscribe takes no lease hold: the directory can vanish at
    any time. The final write must not bring it back."""
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    real_write = event_log_module.atomic_write_text

    def remove_dir_then_write(path, content, **kwargs):
        shutil.rmtree(_char_dir(tmp_path))
        return real_write(path, content, **kwargs)

    with _knobs(), patch.object(event_log_module, "atomic_write_text", remove_dir_then_write):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "no_character_dir"
    assert not os.path.exists(_char_dir(tmp_path)), "压缩把已删除的角色目录建回来了"


def test_old_history_is_not_left_in_a_directory_recreated_during_the_write(tmp_path):
    """Unsubscribe removes the directory and a late writer (a reflection task,
    /cache) recreates it before the replace: the old journal must not end up
    in the new directory, where a same-named role would inherit it."""
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    real_write = event_log_module.atomic_write_text

    def remove_recreate_then_write(path, content, **kwargs):
        shutil.rmtree(_char_dir(tmp_path))
        os.makedirs(_char_dir(tmp_path))
        with open(os.path.join(_char_dir(tmp_path), "recent.json"), "w", encoding="utf-8") as f:
            f.write("[]")
        return real_write(path, content, **kwargs)

    with _knobs(), patch.object(event_log_module, "atomic_write_text", remove_recreate_then_write):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "no_character_dir"
    assert sorted(os.listdir(_char_dir(tmp_path))) == ["recent.json"], "旧日志被写进了重建的角色目录"


def test_write_fence_blocks_compaction(tmp_path):
    from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY

    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    body = _read_bytes(_events_path(tmp_path))
    log._config_manager.load_root_state.return_value = {"mode": ROOT_MODE_MAINTENANCE_READONLY}

    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "write_blocked"
    assert _read_bytes(_events_path(tmp_path)) == body


@contextmanager
def _fence_spy(on_enter=None):
    """Replace the cloud-save write fence with one that records when it is held."""
    held = {"now": False, "entered": 0}

    @contextmanager
    def fake_transaction(*_args, **_kwargs):
        held["entered"] += 1
        held["now"] = True
        try:
            if on_enter is not None:
                on_enter()
            yield
        finally:
            held["now"] = False

    with patch("utils.cloudsave_runtime.cloudsave_writable_transaction", fake_transaction):
        yield held


def test_archive_shards_are_read_before_the_write_fence_is_taken(tmp_path):
    """Shard reads happen in the first pass; under the fence the cached index answers."""
    from memory import archive_shards
    from memory.evidence_handlers import make_archive_copy_check

    log = _fresh_log(str(tmp_path))
    archived = _line("archived", days_ago=60, type_="reflection.state_changed", payload={
        "reflection_id": "r1", "from": "confirmed", "to": "archived",
        "archive_shard_path": "2026-09-01_aaaaaaaa.json",
        "entry_snapshot": {"id": "r1", "archived_at": "2026-09-01T10:00:00"}})
    _write_journal(tmp_path, [archived, _line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    real_index = archive_shards.shard_entry_index_sync
    reads = []
    with _knobs(), _fence_spy() as held:
        def spy_index(archive_dir):
            reads.append(held["now"])
            return real_index(archive_dir)
        with patch.object(archive_shards, "shard_entry_index_sync", spy_index):
            result = log.compact_applied_history(
                NAME, now=NOW, needs_journal_copy=make_archive_copy_check(_char_dir(tmp_path)))
    assert result.outcome == "compacted" and result.pinned == 1
    assert reads == [False] and held["entered"] == 1


def _archived_with_copy(tmp_path) -> str:
    """An applied archive event whose copy sits in the reflection shards (so it may go)."""
    from memory.archive_shards import REFLECTION_ARCHIVE_DIRNAME

    snapshot = {"id": "r1", "archived_at": "2026-09-01T10:00:00"}
    shard_dir = os.path.join(_char_dir(tmp_path), REFLECTION_ARCHIVE_DIRNAME)
    os.makedirs(shard_dir, exist_ok=True)
    with open(os.path.join(shard_dir, "2026-09-01_aaaaaaaa.json"), "w", encoding="utf-8") as f:
        json.dump([snapshot], f)
    return _line("archived", days_ago=60, type_="reflection.state_changed", payload={
        "reflection_id": "r1", "from": "confirmed", "to": "archived",
        "archive_shard_path": "2026-09-01_aaaaaaaa.json", "entry_snapshot": snapshot})


@pytest.mark.parametrize("swap", ["journal_restored", "shards_restored"])
def test_a_restore_between_the_passes_never_trusts_the_stale_archive_index(tmp_path, swap):
    """A restore after the first pass may bring back an archive event without its shard copy."""
    from memory.archive_shards import REFLECTION_ARCHIVE_DIRNAME
    from memory.evidence_handlers import make_archive_copy_check

    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_archived_with_copy(tmp_path), _line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    journal = _events_path(tmp_path)
    shard = os.path.join(_char_dir(tmp_path), REFLECTION_ARCHIVE_DIRNAME, "2026-09-01_aaaaaaaa.json")

    def restore():
        if swap == "journal_restored":
            # A restore copies files back: same bytes, a new file.
            with open(journal + ".restored", "wb") as f:
                f.write(_read_bytes(journal))
            os.replace(journal + ".restored", journal)
        else:
            # The restored shards predate the archive append.
            with open(shard, "w", encoding="utf-8") as f:
                json.dump([], f)

    check = make_archive_copy_check(_char_dir(tmp_path))
    with _knobs(), _fence_spy(on_enter=restore):
        result = log.compact_applied_history(
            NAME, now=NOW, needs_journal_copy=check, sources_unchanged=check.sources_unchanged)
    assert result.outcome == ("journal_changed" if swap == "journal_restored" else "archives_changed")
    # The archive event, the only copy once the shard lost it, is still in the journal.
    assert b'"archived"' in _read_bytes(journal)


@pytest.mark.parametrize("change", ["append", "sentinel_moved", "replay_started", "replay_paused"])
def test_changes_while_the_write_fence_is_taken_are_rescanned(tmp_path, change):
    """The fenced pass plans again, so writes that landed meanwhile are kept or stop it."""
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50), _line("tail", days_ago=40)])
    _write_sentinel(tmp_path, "anchor")
    rounds = []

    def move():
        if change == "append":
            with open(_events_path(tmp_path), "a", encoding="utf-8") as f:
                f.write(_line("late", days_ago=0) + "\n")
        elif change == "sentinel_moved":
            _write_sentinel(tmp_path, "tail")
        elif change == "replay_started":
            rounds.append(log.replay_round(NAME))
            rounds[-1].__enter__()
        else:
            log._record_replay_outcome(NAME, drained=False)

    with _knobs(), _fence_spy(on_enter=move):
        body = _read_bytes(_events_path(tmp_path))
        result = log.compact_applied_history(NAME, now=NOW)
    for active in rounds:
        active.__exit__(None, None, None)
    after = _read_bytes(_events_path(tmp_path))
    ids = [json.loads(raw)["event_id"] for raw in after.decode("utf-8").splitlines() if raw]
    if change == "append":
        # The late line is part of the tail the fenced pass read.
        assert result.outcome == "compacted" and ids == ["anchor", "tail", "late"]
    elif change == "sentinel_moved":
        # The fenced pass anchors on the sentinel it reads now.
        assert result.outcome == "compacted" and ids == ["tail"]
    else:
        expected = {"replay_started": "replay_in_progress", "replay_paused": "replay_paused"}[change]
        assert result.outcome == expected and after == body


def test_journal_changed_under_the_scan_is_left_alone(tmp_path, monkeypatch):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    body = _read_bytes(_events_path(tmp_path))
    target = os.path.normcase(os.path.abspath(_events_path(tmp_path)))
    real_stat = os.stat
    calls = {"n": 0}

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if os.path.normcase(os.path.abspath(str(path))) != target:
            return st
        calls["n"] += 1
        if calls["n"] < 5:  # trigger, first-pass st0, fenced swap check, fenced-pass st0
            return st
        return SimpleNamespace(st_ino=st.st_ino, st_size=st.st_size + 1,
                               st_mtime_ns=st.st_mtime_ns)

    monkeypatch.setattr(event_log_module.os, "stat", fake_stat)
    with _knobs():
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "journal_changed"
    assert calls["n"] == 5
    assert _read_bytes(_events_path(tmp_path)) == body


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics")
def test_open_handle_makes_compaction_busy_on_windows(tmp_path):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    body = _read_bytes(_events_path(tmp_path))

    with open(_events_path(tmp_path), encoding="utf-8") as held, _knobs():
        held.read(1)
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "busy"
    assert _read_bytes(_events_path(tmp_path)) == body
    assert not [n for n in os.listdir(_char_dir(tmp_path)) if n.endswith(".tmp")]


def test_failed_self_check_writes_nothing(tmp_path):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [
        _line("old", days_ago=60), _line("anchor", days_ago=50), _line("t1", days_ago=40),
    ])
    _write_sentinel(tmp_path, "anchor")
    body = _read_bytes(_events_path(tmp_path))

    def lossy(kept_lines):
        return "\n".join(kept_lines[:-1]) + "\n"

    with _knobs(), patch.object(event_log_module, "_assemble_compacted_body", lossy):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "self_check_failed"
    assert _read_bytes(_events_path(tmp_path)) == body


# ── trigger ──────────────────────────────────────────────────────────


def test_small_fresh_journal_is_not_due_and_not_scanned(tmp_path):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("a", days_ago=2), _line("b", days_ago=1)])
    _write_sentinel(tmp_path, "b")
    scan = MagicMock(side_effect=AssertionError("full scan on a journal that is not due"))
    with patch.object(event_log_module, "_iter_journal_lines", scan):
        result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "not_due"
    scan.assert_not_called()


def test_old_first_line_triggers_but_a_lone_anchor_is_left_alone(tmp_path):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("anchor", days_ago=40)])
    _write_sentinel(tmp_path, "anchor")
    mtime = os.stat(_events_path(tmp_path)).st_mtime_ns
    result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "nothing_to_drop"
    assert os.stat(_events_path(tmp_path)).st_mtime_ns == mtime


def test_size_trigger_alone_is_enough(tmp_path):
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=0.5), _line("anchor", days_ago=0.1)])
    _write_sentinel(tmp_path, "anchor")
    with patch.object(event_log_module, "_COMPACT_TRIGGER_BYTES", 1), \
            patch.object(event_log_module, "_RETAIN_APPLIED_DAYS", 0):
        result = log.compact_applied_history(NAME, now=NOW)
    assert result.outcome == "compacted"
    assert _kept_ids(tmp_path) == ["anchor"]


@pytest.mark.parametrize("ts", [
    "1969-07-20T20:17:00+00:00",  # before the epoch: OSError on Windows
    "0001-01-01T00:00:00+14:00",  # OverflowError on every platform
    "9999-12-31T23:59:59-12:00",
])
@pytest.mark.parametrize("position", ["first_line", "before_anchor"])
def test_an_aware_ts_the_platform_cannot_localize_counts_as_old(tmp_path, ts, position):
    """One hand-edited ts must not make every compaction of this journal
    raise; the journal would then never shrink again. It counts as old,
    like any other unusable ts."""
    log = _fresh_log(str(tmp_path))
    lines = [_line("old", days_ago=60), _line("anchor", days_ago=50)]
    lines.insert(0 if position == "first_line" else 1, _line("weird", ts=ts))
    _write_journal(tmp_path, lines)
    _write_sentinel(tmp_path, "anchor")

    with patch.object(event_log_module, "_RETAIN_APPLIED_MAX_BYTES", 1 << 20):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert _kept_ids(tmp_path) == ["anchor"]


# ── an interrupted write ─────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["before_replace", "after_replace", "fsync_fails"])
def test_an_interrupted_write_leaves_a_body_with_the_same_tail(tmp_path, stage):
    """Crash points inside the one write: before the replace the old body
    stays, after it the new one is in place, and either way replay sees the
    same tail, the sentinel is untouched and no temp file is left behind.
    The next run converges."""
    from utils import file_utils

    log = _fresh_log(str(tmp_path))
    lines = [
        _line("old1", days_ago=60), _line("old2", days_ago=55),
        _line("anchor", days_ago=50), _line("t1", days_ago=40), "{corrupt",
        _line("t2", days_ago=30),
    ]
    _write_journal(tmp_path, lines)
    _write_sentinel(tmp_path, "anchor")
    tail_before = _tail_ids(log)
    assert tail_before == ["t1", "t2"]
    body_before = _read_bytes(_events_path(tmp_path))
    sentinel_before = _read_bytes(_sentinel_path(tmp_path))
    real_replace = file_utils._replace_with_busy_retry
    real_fsync = file_utils.os.fsync

    def interrupted_replace(temp_path, target_path):
        if stage == "after_replace":
            real_replace(temp_path, target_path)
        raise KeyboardInterrupt

    def failing_fsync(fd):
        raise OSError(5, "simulated I/O error")

    with _knobs():
        if stage == "fsync_fails":
            with patch.object(file_utils.os, "fsync", failing_fsync):
                result = log.compact_applied_history(NAME, now=NOW)
            assert result.outcome == "write_failed"
        else:
            with patch.object(file_utils, "_replace_with_busy_retry", interrupted_replace), \
                    pytest.raises(KeyboardInterrupt):
                log.compact_applied_history(NAME, now=NOW)
    assert real_fsync is file_utils.os.fsync

    assert _read_bytes(_sentinel_path(tmp_path)) == sentinel_before
    assert _tail_ids(log) == tail_before
    if stage == "after_replace":
        assert _journal_lines(tmp_path) == lines[2:]
    else:
        assert _read_bytes(_events_path(tmp_path)) == body_before
    assert sorted(os.listdir(_char_dir(tmp_path))) == ["events.ndjson", "events_applied.json"]
    assert not log._replay_round_active(NAME)

    with _knobs():
        again = log.compact_applied_history(NAME, now=NOW)
    assert again.outcome == ("nothing_to_drop" if stage == "after_replace" else "compacted")
    assert _journal_lines(tmp_path) == lines[2:]
    assert _tail_ids(log) == tail_before


# ── real threads: replay, live writes and compaction at once ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(3))
async def test_concurrent_replay_live_writes_and_compaction_lose_nothing(tmp_path, seed):
    """No scripted interleaving: a replay round with a slow handler, a live
    writer moving the sentinel under it (so the round freezes and rescans
    from its own last-applied event), and a compactor hammering the same
    journal. Every event the round owed must still be applied. Removing
    the replay-round guard turns this red (the rescan anchor is cut away).
    """
    log = _fresh_log(str(tmp_path))
    rec = Reconciler(log)
    for i in range(30):
        log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": f"h{i}"})
    log.advance_sentinel(NAME, log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": "anchor"}))
    owed = {f"t{i}" for i in range(25)}
    for i in range(25):
        log.append(NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": f"t{i}"})
    applied: list[str] = []
    applied_lock = threading.Lock()
    rng = random.Random(seed)
    delays = {f"t{i}": rng.choice([0.0, 0.001, 0.002]) for i in range(25)}

    def handler(_name, payload):
        time.sleep(delays.get(payload["i"], 0.0))
        with applied_lock:
            applied.append(payload["i"])
        return True

    rec.register(EVT_REFLECTION_EVIDENCE_UPDATED, handler)
    stop = threading.Event()
    outcomes: dict[str, int] = {}
    errors: list[BaseException] = []
    view: dict = {}

    def writer():
        n = 0
        while not stop.is_set():
            try:
                log.record_and_save(
                    NAME, EVT_REFLECTION_EVIDENCE_UPDATED, {"i": f"live{n}"},
                    sync_load_view=lambda _n: view,
                    sync_mutate_view=lambda _v: None,
                    sync_save_view=lambda _n, _v: None,
                )
            except Exception as e:  # noqa: BLE001 - surfaced by the assert below
                errors.append(e)
                return
            n += 1
            time.sleep(0.003)

    def compactor():
        far_future = datetime.now() + timedelta(days=90)
        while not stop.is_set():
            try:
                result = log.compact_applied_history(NAME, now=far_future)
            except Exception as e:  # noqa: BLE001 - surfaced by the assert below
                errors.append(e)
                return
            outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
            time.sleep(0.0005)

    threads = [threading.Thread(target=writer, daemon=True),
               threading.Thread(target=compactor, daemon=True)]
    with _knobs():
        for t in threads:
            t.start()
        try:
            await asyncio.wait_for(rec.areconcile(NAME), 30)
        finally:
            stop.set()
            for t in threads:
                t.join(10)

        assert not errors, errors[:3]
        assert outcomes.get("replay_in_progress", 0) > 0, outcomes
        missing = sorted(owed - set(applied))
        assert not missing, f"本轮欠下的事件丢了 {len(missing)} 条: {missing[:5]} {outcomes}"

        before = _tail_ids(log)
        log.compact_applied_history(NAME, now=datetime.now() + timedelta(days=90))
        assert _tail_ids(log) == before


# ── order of what is kept, first-line trigger, a dir lost before the checks ──


def test_pinned_and_window_lines_keep_their_journal_order(tmp_path):
    """Lines kept in front of the anchor stay in journal order, pinned and
    window lines interleaved as they were. Replay never reads them while
    the sentinel holds, but the full-replay fallback (sentinel lost) and the
    age trigger (first line) both do, and an archive event must not jump
    ahead of the evidence updates written before it."""
    log = _fresh_log(str(tmp_path))
    lines = [
        _line("w1", days_ago=6),
        _line("p1", days_ago=60),
        _line("w2", days_ago=5),
        _line("drop", days_ago=40),
        _line("p2", days_ago=50),
        _line("w3", days_ago=4),
        _line("anchor", days_ago=3),
        _line("t1", days_ago=2),
    ]
    _write_journal(tmp_path, lines)
    _write_sentinel(tmp_path, "anchor")

    with _knobs(days=7, cap=1 << 20):
        result = log.compact_applied_history(
            NAME, now=NOW, needs_journal_copy=lambda rec: rec.get("event_id") in {"p1", "p2"},
        )

    assert result.outcome == "compacted"
    assert result.pinned == 2
    assert _kept_ids(tmp_path) == ["w1", "p1", "w2", "p2", "w3", "anchor", "t1"]


@pytest.mark.parametrize("first_line", [
    "{corrupt",
    json.dumps({"event_id": "no_ts", "type": EVT_REFLECTION_EVIDENCE_UPDATED, "payload": {}}),
    "[1, 2]",
])
def test_an_unusable_first_line_makes_a_small_journal_due(tmp_path, first_line):
    """The age trigger cannot read an age off this line, so it counts as
    old; otherwise a small journal starting with one would only ever be
    reached by the size trigger."""
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [first_line, _line("recent", days_ago=1), _line("anchor", days_ago=0.5)])
    _write_sentinel(tmp_path, "anchor")

    with patch.object(event_log_module, "_RETAIN_APPLIED_DAYS", 0):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "compacted"
    assert _kept_ids(tmp_path) == ["anchor"]


def test_character_dir_removed_before_the_prewrite_checks(tmp_path):
    """Unsubscribe can remove the directory while the body is being built.
    The pre-write check reports it as such, writes nothing and creates
    nothing."""
    log = _fresh_log(str(tmp_path))
    _write_journal(tmp_path, [_line("old", days_ago=60), _line("anchor", days_ago=50)])
    _write_sentinel(tmp_path, "anchor")
    real_check = event_log_module._body_keeps_tail

    def check_then_remove_dir(body, sentinel, old_tail_ids):
        ok = real_check(body, sentinel, old_tail_ids)
        shutil.rmtree(_char_dir(tmp_path))
        return ok

    with _knobs(), patch.object(event_log_module, "_body_keeps_tail", check_then_remove_dir):
        result = log.compact_applied_history(NAME, now=NOW)

    assert result.outcome == "no_character_dir"
    assert not os.path.exists(_char_dir(tmp_path))
