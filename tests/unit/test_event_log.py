# -*- coding: utf-8 -*-
"""
Unit tests for memory.event_log.EventLog.

P2.a.1: pure infrastructure tests. No production wiring is touched.
Covers the resilience guarantees described in RFC §3.4 / §3.5 / §3.6.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _fresh_log(tmpdir: str):
    from memory.event_log import EventLog

    mock_cm = MagicMock()
    mock_cm.memory_dir = tmpdir
    with patch("memory.event_log.get_config_manager", return_value=mock_cm):
        log = EventLog()
    log._config_manager = mock_cm
    return log


def _fresh_reconciler(tmpdir: str):
    from memory.event_log import EventLog, Reconciler

    log = _fresh_log(tmpdir)
    return log, Reconciler(log)


# ── append / read_since ──────────────────────────────────────────────


def test_append_returns_unique_event_ids(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id1 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    id2 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f2"})
    assert id1 != id2
    # Real UUIDs (parseable)
    uuid.UUID(id1)
    uuid.UUID(id2)


def test_read_since_returns_events_after_sentinel(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id1 = log.append("小天", EVT_FACT_ADDED, {"i": 1})
    id2 = log.append("小天", EVT_FACT_ADDED, {"i": 2})
    id3 = log.append("小天", EVT_FACT_ADDED, {"i": 3})

    # After id1 → returns [event2, event3]
    tail = log.read_since("小天", id1)
    assert [r["event_id"] for r in tail] == [id2, id3]


def test_read_since_null_sentinel_returns_all(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id1 = log.append("小天", EVT_FACT_ADDED, {"i": 1})
    id2 = log.append("小天", EVT_FACT_ADDED, {"i": 2})
    assert [r["event_id"] for r in log.read_since("小天", None)] == [id1, id2]


def test_read_since_unknown_sentinel_falls_back_to_full_replay(tmp_path):
    """RFC §3.5 safe default: sentinel points to a compacted-away event →
    replay everything currently in the body."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id1 = log.append("小天", EVT_FACT_ADDED, {"i": 1})
    id2 = log.append("小天", EVT_FACT_ADDED, {"i": 2})

    bogus_sentinel = str(uuid.uuid4())
    tail = log.read_since("小天", bogus_sentinel)
    assert [r["event_id"] for r in tail] == [id1, id2]


def test_corrupt_line_skipped_with_warning(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id1 = log.append("小天", EVT_FACT_ADDED, {"i": 1})
    path = log._events_path("小天")
    with open(path, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
    id2 = log.append("小天", EVT_FACT_ADDED, {"i": 2})

    tail = log.read_since("小天", None)
    assert [r["event_id"] for r in tail] == [id1, id2]


def test_record_missing_event_id_skipped(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    log.append("小天", EVT_FACT_ADDED, {"i": 1})
    # Hand-craft a record without event_id
    path = log._events_path("小天")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "fact.added", "ts": "2026-01-01"}) + "\n")
    log.append("小天", EVT_FACT_ADDED, {"i": 2})

    tail = log.read_since("小天", None)
    assert len(tail) == 2


# ── sentinel ─────────────────────────────────────────────────────────


def test_read_sentinel_returns_none_when_missing(tmp_path):
    log = _fresh_log(str(tmp_path))
    assert log.read_sentinel("小天") is None


def test_advance_sentinel_roundtrip(tmp_path):
    log = _fresh_log(str(tmp_path))
    eid = str(uuid.uuid4())
    log.advance_sentinel("小天", eid)
    assert log.read_sentinel("小天") == eid


def test_corrupt_sentinel_treated_as_missing(tmp_path):
    log = _fresh_log(str(tmp_path))
    path = log._sentinel_path("小天")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json {{{{")
    assert log.read_sentinel("小天") is None


def test_sentinel_persists_across_fresh_instances(tmp_path):
    log1 = _fresh_log(str(tmp_path))
    eid = str(uuid.uuid4())
    log1.advance_sentinel("小天", eid)

    log2 = _fresh_log(str(tmp_path))
    assert log2.read_sentinel("小天") == eid


# ── compaction (RFC §3.6) ────────────────────────────────────────────


def test_should_compact_false_when_empty(tmp_path):
    log = _fresh_log(str(tmp_path))
    assert log.should_compact("小天") is False


def test_compact_if_needed_skips_below_threshold(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    for i in range(5):
        log.append("小天", EVT_FACT_ADDED, {"i": i})
    dropped = log.compact_if_needed("小天", lambda: [])
    assert dropped == 0
    # File still has the 5 events
    tail = log.read_since("小天", None)
    assert len(tail) == 5


def test_compact_triggered_by_line_threshold(tmp_path):
    """Force threshold low via monkeypatch to avoid writing 10K lines."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    for i in range(10):
        log.append("小天", EVT_FACT_ADDED, {"i": i})

    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 5):
        # Seed provider returns 2 snapshot-start events
        seeds = [
            (EVT_FACT_ADDED, {"fact_id": "f_seed1"}),
            (EVT_FACT_ADDED, {"fact_id": "f_seed2"}),
        ]
        dropped = log.compact_if_needed("小天", lambda: seeds)

    assert dropped == 10 - 2
    tail = log.read_since("小天", None)
    assert len(tail) == 2
    assert {r["payload"]["fact_id"] for r in tail} == {"f_seed1", "f_seed2"}


def test_compact_resets_sentinel_to_null(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    eid = log.append("小天", EVT_FACT_ADDED, {"i": 1})
    log.advance_sentinel("小天", eid)
    # Force compact
    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 1):
        log.compact_if_needed("小天", lambda: [(EVT_FACT_ADDED, {"seed": True})])
    # Sentinel reset to null
    assert log.read_sentinel("小天") is None


def test_compact_atomicity_single_rename(tmp_path):
    """RFC §3.6: no intermediate events.snapshot file is ever written."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    for i in range(5):
        log.append("小天", EVT_FACT_ADDED, {"i": i})

    char_dir = os.path.join(str(tmp_path), "小天")
    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 1):
        log.compact_if_needed("小天", lambda: [(EVT_FACT_ADDED, {"seed": 1})])
    after = set(os.listdir(char_dir))

    # events.ndjson and events_applied.json — nothing else
    assert after == {"events.ndjson", "events_applied.json"}
    # No lingering tempfiles from atomic_write_text
    assert not any(name.endswith(".tmp") for name in after)
    # No events.snapshot file (deliberately eliminated in v2/v3)
    assert "events.snapshot" not in after


def test_compact_crash_between_swap_and_sentinel_reset_is_safe(tmp_path):
    """Simulate: new body swapped in, but sentinel reset 'failed' — old
    sentinel now points to an event_id that doesn't exist in the body.
    On next boot read_since falls through to full replay (seed events)."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    for i in range(5):
        log.append("小天", EVT_FACT_ADDED, {"i": i})
    stale_sentinel = log.append("小天", EVT_FACT_ADDED, {"i": 5})
    log.advance_sentinel("小天", stale_sentinel)

    # Crash simulation: body swap happens, sentinel reset does NOT (blocked
    # atomic_write_json — the only code path used for sentinel writes inside
    # compact_if_needed). Body is still rewritten via atomic_write_text.
    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 1), \
         patch("memory.event_log.atomic_write_json") as _mock_aj:
        log.compact_if_needed("小天", lambda: [(EVT_FACT_ADDED, {"seed": 1})])
    # Sentinel reset was blocked → still points to the (now-gone) old event
    assert log.read_sentinel("小天") == stale_sentinel

    # Next-boot simulation: read_since falls through to full body replay
    tail = log.read_since("小天", stale_sentinel)
    assert len(tail) == 1
    assert tail[0]["payload"] == {"seed": 1}


def test_scan_head_handles_corrupt_first_line(tmp_path):
    """RFC §3.6 edge case: corrupt first line → age threshold disabled,
    line-count threshold still works."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    path = log._events_path("小天")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("garbage not json\n")
        for i in range(3):
            rec = {"event_id": str(uuid.uuid4()), "type": EVT_FACT_ADDED,
                   "ts": datetime.now().isoformat(), "payload": {"i": i}}
            f.write(json.dumps(rec) + "\n")

    # should_compact should not crash on the corrupt head
    result = log.should_compact("小天")
    assert result is False  # 4 lines, no age info, under default threshold

    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 2):
        assert log.should_compact("小天") is True


# ── record_and_save (the core write-ordering helper) ────────────────


def test_record_and_save_runs_all_steps_in_order(tmp_path):
    """load → append → mutate → save → sentinel advance, all inside lock.

    Pins the append-first ordering: if append raises, the caller's shared
    cache (mutated by sync_mutate_view) must not be dirtied.
    """
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    call_order: list[str] = []
    the_view = {"loaded": False, "mutated": False, "saved": False}

    def load(name):
        call_order.append("load")
        the_view["loaded"] = True
        return the_view

    def mutate(view):
        call_order.append("mutate")
        view["mutated"] = True

    def save(name, view):
        call_order.append("save")
        view["saved"] = True

    real_append = log._append_unlocked

    def append_probe(name, event_type, payload):
        call_order.append("append")
        return real_append(name, event_type, payload)

    with patch.object(log, "_append_unlocked", side_effect=append_probe):
        eid = log.record_and_save(
            "小天", EVT_FACT_ADDED, {"fact_id": "f1"},
            sync_load_view=load, sync_mutate_view=mutate, sync_save_view=save,
        )

    assert call_order == ["load", "append", "mutate", "save"]
    assert the_view == {"loaded": True, "mutated": True, "saved": True}
    # Event landed
    tail = log.read_since("小天", None)
    assert len(tail) == 1 and tail[0]["event_id"] == eid
    # Sentinel advanced to this event
    assert log.read_sentinel("小天") == eid


def test_record_and_save_append_failure_skips_save_and_sentinel(tmp_path):
    """If event append raises, view mutate+save must NOT happen and sentinel
    must NOT advance — the append-first invariant keeps shared cache clean."""
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    mutate_called = [False]
    save_called = [False]

    def load(name):
        return {"x": 1}

    def mutate(view):
        mutate_called[0] = True
        view["x"] = 2

    def save(name, view):
        save_called[0] = True

    with patch.object(log, "_append_unlocked", side_effect=IOError("disk full")):
        with pytest.raises(IOError):
            log.record_and_save(
                "小天", EVT_FACT_ADDED, {"fact_id": "f1"},
                sync_load_view=load, sync_mutate_view=mutate, sync_save_view=save,
            )

    # Append-first means mutate never runs when append fails → shared cache clean.
    assert mutate_called[0] is False
    assert save_called[0] is False
    # Sentinel untouched
    assert log.read_sentinel("小天") is None


def test_record_and_save_serializes_concurrent_calls(tmp_path):
    """Per-character lock must prevent two record_and_save calls from
    interleaving their load/mutate/save sequences.

    The strongest oracle is `mutable_view["value"] == 5` — an unlocked RMW
    would lose updates when two workers both read 0 before either writes 1.
    The chunk check is a secondary diagnostic.
    """
    import time
    import threading as real_threading
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    mutable_view = {"value": 0}

    def make_callbacks(tag: str):
        def load(name):
            return mutable_view

        def mutate(view):
            current = view["value"]
            time.sleep(0.05)   # widen the race window (50ms)
            view["value"] = current + 1

        def save(name, view):
            return
        return load, mutate, save

    def worker(tag: str):
        ld, mu, sv = make_callbacks(tag)
        log.record_and_save(
            "小天", EVT_FACT_ADDED, {"worker": tag},
            sync_load_view=ld, sync_mutate_view=mu, sync_save_view=sv,
        )

    t0 = time.monotonic()
    threads = [real_threading.Thread(target=worker, args=(f"w{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0

    # Primary oracle: no lost updates despite 50ms mutation window overlap.
    # Without the lock two or more workers would observe value=0 before
    # either wrote value=1, so the final count would be < 5.
    assert mutable_view["value"] == 5
    # Secondary: wallclock >= 5 * sleep confirms the mutations actually
    # serialized (each took its full 50ms before the next could start).
    # Allow a little slack for timer resolution.
    assert elapsed >= 5 * 0.05 * 0.9, f"wallclock {elapsed:.3f}s < expected serial floor"


def test_serialization_oracle_rejects_unlocked_mode(tmp_path):
    """Diagnostic-power check: with the lock patched out, the previous
    test's oracle MUST fail — proves the assertion isn't passing by
    accident of scheduling.

    Uses a Barrier so every worker finishes its read before any worker
    writes — deterministic lost-update, no reliance on sleep timing.
    """
    import threading as real_threading
    from contextlib import nullcontext
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    # Replace per-character lock with a no-op context manager
    log._get_lock = lambda name: nullcontext()
    # Once the lock is split, the sentinel write at the end of record_and_save also loses serialization; five threads can concurrently
    # os.replace the same events_applied.json and hit WinError 5 on Windows. The sentinel
    # Persistence is not what this test is measuring (the previous positive oracle covers it), so give each thread
    # a separate sentinel filename: use the real write path but avoid contention on one target.
    _real_sentinel_path = log._sentinel_path
    log._sentinel_path = lambda name: os.path.join(
        os.path.dirname(_real_sentinel_path(name)),
        f"events_applied_{real_threading.get_ident()}.json",
    )

    num_workers = 5
    mutable_view = {"value": 0}
    read_barrier = real_threading.Barrier(num_workers)

    def mutate(view):
        current = view["value"]
        # Hold the RMW open until every worker has read the same value.
        read_barrier.wait(timeout=5)
        view["value"] = current + 1

    def worker(tag: str):
        log.record_and_save(
            "小天", EVT_FACT_ADDED, {"worker": tag},
            sync_load_view=lambda n: mutable_view,
            sync_mutate_view=mutate,
            sync_save_view=lambda n, v: None,
        )

    threads = [
        real_threading.Thread(target=worker, args=(f"w{i}",))
        for i in range(num_workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every worker read 0 before any wrote → final value is exactly 1.
    # With the lock, workers would serialize and final value would be num_workers.
    assert mutable_view["value"] < num_workers, \
        f"expected lost updates without lock, got {mutable_view['value']}"


def test_concurrent_append_during_compact_preserves_events(tmp_path):
    """Per-character lock must block append during compaction and vice-versa.
    Regression guard: catches future changes that move body-swap outside the lock."""
    import time
    import threading as real_threading
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    # Seed a handful of events so compact has something to drop
    for i in range(3):
        log.append("小天", EVT_FACT_ADDED, {"i": i, "source": "seed"})

    append_errors: list[Exception] = []
    appended_ids: list[str] = []
    stop_flag = real_threading.Event()

    def appender():
        while not stop_flag.is_set():
            try:
                appended_ids.append(log.append("小天", EVT_FACT_ADDED, {"source": "parallel"}))
            except Exception as e:
                append_errors.append(e)

    t = real_threading.Thread(target=appender, daemon=True)
    t.start()
    # Do not guess "has the thread started?" with a fixed sleep: thread startup delay is uncontrolled, and on Windows
    # sleep duration is quantized in 15.6 ms increments; if it sleeps too briefly, compact can finish before the first concurrent
    # append and the test silently degrades to sequential execution with a vacuous assertion. Poll until one event has actually
    # been written so the concurrency precondition is deterministic.
    _deadline = time.monotonic() + 5.0
    while not appended_ids and not append_errors and time.monotonic() < _deadline:
        time.sleep(0.001)
    assert appended_ids or append_errors, "appender 线程 5s 内一条都没写出，并发前提不成立"

    with patch("memory.event_log._COMPACT_LINES_THRESHOLD", 1):
        log.compact_if_needed("小天", lambda: [(EVT_FACT_ADDED, {"seed": "kept"})])

    stop_flag.set()
    t.join(timeout=2.0)

    assert append_errors == [], f"append raised under concurrent compact: {append_errors}"
    # Body is not corrupt: read_since returns valid JSON for every record
    tail = log.read_since("小天", None)
    assert all(isinstance(r.get("event_id"), str) and isinstance(r.get("type"), str)
               for r in tail)
    # At least the seed from compact must be present — appends that happened
    # AFTER the body swap land on the fresh body
    seed_ids = [r["event_id"] for r in tail if r["payload"].get("seed") == "kept"]
    assert len(seed_ids) == 1, f"expected exactly one compact seed, got {seed_ids}"


def test_apply_and_advance_does_not_interleave_with_record_and_save(tmp_path):
    """Replay-apply + sentinel-advance is one critical section, shared with
    record_and_save's.

    Both sides do read-modify-write on the same view file, so a live writer
    that starts mid-replay must not slip its own load/mutate/save between the
    handler's write and the sentinel write (or vice versa) — that is exactly
    how a replayed crash-repair gets silently overwritten while the sentinel
    marks it as applied.
    """
    import time
    import threading as real_threading
    from itertools import groupby
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    timeline: list[tuple[str, str]] = []   # (thread name, step)

    def mark(step: str) -> None:
        # list.append is atomic under the GIL, so two threads can safely write to one timeline
        timeline.append((real_threading.current_thread().name, step))

    from memory import event_log as event_log_module
    real_write_json = event_log_module.atomic_write_json

    def tracking_write_json(*args, **kwargs):
        # Sentinel writing is the final step in both paths and must also be inside each critical section
        mark("sentinel")
        return real_write_json(*args, **kwargs)

    apply_running = real_threading.Event()

    def apply_fn() -> bool:
        mark("apply_start")
        apply_running.set()
        time.sleep(0.3)
        mark("apply_end")
        return True

    def load(name):
        mark("record_load")
        return {"facts": []}

    def mutate(view):
        mark("record_mutate")

    def save(name, view):
        mark("record_save")

    with patch("memory.event_log.atomic_write_json", tracking_write_json):
        t_apply = real_threading.Thread(
            target=lambda: log.apply_and_advance(
                "小天", "evt-replayed", apply_fn, expected_sentinel=None,
            ),
            name="APPLY",
        )
        t_apply.start()
        assert apply_running.wait(5.0), "apply handler 5s 内没跑起来，并发前提不成立"
        t_record = real_threading.Thread(
            target=lambda: log.record_and_save(
                "小天", EVT_FACT_ADDED, {"fact_id": "f1"},
                sync_load_view=load, sync_mutate_view=mutate, sync_save_view=save,
            ),
            name="RECORD",
        )
        t_record.start()
        t_apply.join(20.0)
        t_record.join(20.0)

    assert not t_apply.is_alive() and not t_record.is_alive(), "线程没收干净"

    runs = [name for name, _ in groupby(name for name, _ in timeline)]
    assert runs == ["APPLY", "RECORD"], f"临界区交错了: {timeline}"
    assert [s for n, s in timeline if n == "APPLY"] == [
        "apply_start", "apply_end", "sentinel",
    ]
    assert [s for n, s in timeline if n == "RECORD"] == [
        "record_load", "record_mutate", "record_save", "sentinel",
    ]


def test_apply_and_advance_keeps_sentinel_when_apply_raises(tmp_path):
    """Sentinel must not move if the apply handler blew up — the caller's
    pause-and-retry semantics depend on the event staying in the tail."""
    log = _fresh_log(str(tmp_path))
    log.advance_sentinel("小天", "evt-previous")

    def boom() -> bool:
        raise RuntimeError("simulated apply failure")

    with pytest.raises(RuntimeError, match="simulated apply failure"):
        log.apply_and_advance(
            "小天", "evt-new", boom, expected_sentinel="evt-previous",
        )

    assert log.read_sentinel("小天") == "evt-previous"


def test_advance_sentinel_does_not_interleave_with_record_and_save(tmp_path):
    """The public sentinel writer takes the same per-character lock.

    Twin of the apply_and_advance interleaving test. advance_sentinel has no
    production caller today, so nothing else would notice if the lock were
    dropped — yet it rewrites the very file record_and_save rewrites at the
    tail of its own critical section, and a torn/overtaken sentinel write is
    how replayed events get lost or re-applied forever.
    """
    import time
    import threading as real_threading
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    timeline: list[tuple[str, str]] = []

    def mark(step: str) -> None:
        timeline.append((real_threading.current_thread().name, step))

    from memory import event_log as event_log_module
    real_write_json = event_log_module.atomic_write_json

    def tracking_write_json(*args, **kwargs):
        mark("sentinel")
        return real_write_json(*args, **kwargs)

    record_inside = real_threading.Event()

    def load(name):
        mark("record_load")
        record_inside.set()
        # Intentionally hold the critical section for 0.3s so unlocked advance_sentinel can interleave
        time.sleep(0.3)
        return {"facts": []}

    def mutate(view):
        mark("record_mutate")

    def save(name, view):
        mark("record_save")

    def _advance():
        mark("advance_enter")
        log.advance_sentinel("小天", "evt-from-other-writer")

    with patch("memory.event_log.atomic_write_json", tracking_write_json):
        t_record = real_threading.Thread(
            target=lambda: log.record_and_save(
                "小天", EVT_FACT_ADDED, {"fact_id": "f1"},
                sync_load_view=load, sync_mutate_view=mutate, sync_save_view=save,
            ),
            name="RECORD",
        )
        t_record.start()
        assert record_inside.wait(5.0), "record_and_save 5s 内没进临界区，并发前提不成立"
        t_advance = real_threading.Thread(target=_advance, name="ADVANCE")
        t_advance.start()
        t_record.join(20.0)
        t_advance.join(20.0)

    assert not t_record.is_alive() and not t_advance.is_alive(), "线程没收干净"

    # Non-empty pass proves ADVANCE started while RECORD was still writing its view
    assert timeline.index(("ADVANCE", "advance_enter")) < \
        timeline.index(("RECORD", "record_save")), \
        f"ADVANCE 没赶在 RECORD 临界区里启动，测试是空过的: {timeline}"
    # Mutual exclusion proof: its sentinel write still comes after RECORD's own sentinel write
    record_sentinel = [i for i, e in enumerate(timeline) if e == ("RECORD", "sentinel")]
    advance_sentinel = [i for i, e in enumerate(timeline) if e == ("ADVANCE", "sentinel")]
    assert len(record_sentinel) == 1 and len(advance_sentinel) == 1, f"哨兵写次数不对: {timeline}"
    assert advance_sentinel[0] > record_sentinel[0], \
        f"advance_sentinel 插进了 record_and_save 的临界区: {timeline}"
    assert log.read_sentinel("小天") == "evt-from-other-writer"


def test_apply_and_advance_detects_the_conflict_before_running_the_handler(tmp_path):
    """A replay must not APPLY an event the sentinel has already passed.

    The compare-and-set is against the sentinel value this round started
    from (event_id is a uuid4, so "which id is newer" cannot be answered by
    comparing ids). It has to run BEFORE the handler, not after. Handlers
    load the view and assign full-snapshot fields onto the target entry, so
    running one first has already written the stale payload — and the newer
    writer's event sits ahead of the sentinel, where no later boot replays
    it back over the top. Detecting afterwards only reports damage that has
    already been done.

    Nothing can move the sentinel between the check and the apply either:
    record_and_save, compact_if_needed and advance_sentinel all take the
    per-character lock, which this method holds across both steps.

    Refusing the sentinel write matters for a second reason: rolling it
    backwards would return the other writer's events to the tail, and any
    of them without a registered handler would pause replay on every
    future boot.
    """
    from memory.event_log import SentinelConflictError

    log = _fresh_log(str(tmp_path))
    log.advance_sentinel("小天", "evt-live-writer")

    # The value just written by the newer writer; a stale event payload would roll it back to archived
    view = {"status": "promoted"}
    applied = {"n": 0}

    def apply_fn() -> bool:
        applied["n"] += 1
        view["status"] = "archived"
        return True

    with pytest.raises(SentinelConflictError):
        log.apply_and_advance(
            "小天", "evt-replayed", apply_fn, expected_sentinel="evt-stale",
        )

    assert applied["n"] == 0, "冲突判定跑在 handler 之后了"
    assert view == {"status": "promoted"}, \
        "陈旧事件已经落进 view，把更新的写者刚写好的值盖掉了"
    assert log.read_sentinel("小天") == "evt-live-writer", "哨兵被写回了旧位置"


def test_apply_and_advance_without_advance_neither_checks_nor_writes(tmp_path):
    """The frozen-sentinel mode used by the post-conflict rescan.

    Once a round knows the sentinel is not its to move, it re-derives its
    work from the journal and replays it with advance=False. That mode must
    skip the compare-and-set too — the sentinel deliberately no longer
    matches what this round started from, so still checking would raise on
    every remaining event and the repairs would never land.
    """
    log = _fresh_log(str(tmp_path))
    log.advance_sentinel("小天", "evt-live-writer")

    applied = {"n": 0}

    def apply_fn() -> bool:
        applied["n"] += 1
        return True

    changed = log.apply_and_advance(
        "小天", "evt-replayed", apply_fn,
        expected_sentinel="evt-stale", advance=False,
    )

    assert changed is True
    assert applied["n"] == 1, "冻结模式下 handler 必须照跑，否则修复永远落不了盘"
    assert log.read_sentinel("小天") == "evt-live-writer", "冻结模式动了哨兵"


def test_apply_and_advance_reports_sentinel_write_failure_separately(tmp_path):
    """A failed sentinel write is not a failed handler.

    The handler already persisted its view; only the sentinel is behind. The
    caller has to be able to tell the two apart, because they mean different
    things on disk and get different log lines.
    """
    from memory.event_log import SentinelAdvanceError

    log = _fresh_log(str(tmp_path))
    applied = {"n": 0}

    def apply_fn() -> bool:
        applied["n"] += 1
        return True

    def boom_write(*args, **kwargs):
        raise OSError(13, "simulated sentinel write failure")

    with patch("memory.event_log.atomic_write_json", boom_write):
        with pytest.raises(SentinelAdvanceError) as excinfo:
            log.apply_and_advance(
                "小天", "evt-replayed", apply_fn, expected_sentinel=None,
            )

    assert applied["n"] == 1
    assert isinstance(excinfo.value.__cause__, OSError)
    assert log.read_sentinel("小天") is None


# ── Reconciler scaffolding ──────────────────────────────────────────


def test_append_rejects_unknown_event_type(tmp_path):
    """Fail-fast at the write site: typos or rolled-back-new types must not
    reach disk, otherwise a crash between event append and view save would
    leave an unreplayable record (Reconciler has no handler) and the view
    mutation would be lost forever."""
    log = _fresh_log(str(tmp_path))
    with pytest.raises(ValueError, match="unknown event type"):
        log.append("小天", "future.unknown.type", {"v": 1})
    # No file should have been created (append raised before write).
    events_path = os.path.join(str(tmp_path), "小天", "events.ndjson")
    assert not os.path.exists(events_path)


@pytest.mark.asyncio
async def test_reconciler_pauses_on_unknown_event_type(tmp_path):
    """Rollback safety: if a newer binary wrote an event type the current
    binary doesn't know, Reconciler must pause with the sentinel on the
    previous event. Advancing past would permanently lose the event and
    could silently fork the view when later known events apply mutations
    that depend on the unreplayed one."""
    from memory.event_log import EVT_FACT_ADDED

    log, rec = _fresh_reconciler(str(tmp_path))

    # First known event — gets applied.
    eid1 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})

    # Then manually inject an unknown event (append() fail-fasts on unknown
    # types, so we simulate a log written by a newer binary by appending the
    # raw ndjson line ourselves).
    events_path = os.path.join(str(tmp_path), "小天", "events.ndjson")
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event_id": str(uuid.uuid4()),
            "type": "future.unknown.type",
            "ts": datetime.now().isoformat(),
            "payload": {"v": 1},
        }) + "\n")

    # A known event AFTER the unknown one — must NOT be applied (would risk
    # view fork if the unknown event carried a prerequisite mutation).
    log.append("小天", EVT_FACT_ADDED, {"fact_id": "f2"})

    applied_calls: list[str] = []

    def handler(name, payload):
        applied_calls.append(payload.get("fact_id"))
        return True

    rec.register(EVT_FACT_ADDED, handler)

    applied = await rec.areconcile("小天")
    # Only f1 applied — unknown event paused the loop, f2 never reached.
    assert applied == 1
    assert applied_calls == ["f1"]
    # Sentinel stays on eid1 (the last known-applied event) so next boot
    # with an upgraded binary can resume from the unknown one.
    assert log.read_sentinel("小天") == eid1


@pytest.mark.asyncio
async def test_reconciler_handler_exception_preserves_sentinel(tmp_path):
    """If an apply handler raises, sentinel must NOT advance past the bad
    event so next boot retries."""
    from memory.event_log import EVT_FACT_ADDED

    log, rec = _fresh_reconciler(str(tmp_path))
    eid1 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    log.append("小天", EVT_FACT_ADDED, {"fact_id": "f2"})

    call_count = {"n": 0}

    def handler(name, payload):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return True  # first one ok
        raise RuntimeError("simulated apply failure")

    rec.register(EVT_FACT_ADDED, handler)

    applied = await rec.areconcile("小天")
    assert applied == 1
    # Sentinel is advanced only past the successful event
    assert log.read_sentinel("小天") == eid1


@pytest.mark.asyncio
async def test_reconciler_runs_handlers_off_the_loop_under_the_lock(tmp_path):
    """Apply handlers must run on a worker thread, inside the per-character lock.

    Both properties are load-bearing and neither is visible from the outside:
      - off the event loop, because handlers do blocking file IO (and
        file_utils' busy-retry backoff is deliberately disabled on the loop);
      - under the lock that record_and_save uses, because the handler and the
        sentinel write have to be one critical section against live writers.
    Asserted from inside the handler, so moving the call back onto the loop —
    or out of the lock — fails here rather than only in the integration suite
    that CI does not run.
    """
    import asyncio as real_asyncio
    import threading as real_threading
    from memory.event_log import EVT_FACT_ADDED

    log, rec = _fresh_reconciler(str(tmp_path))
    eid = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    seen: dict[str, object] = {}

    def handler(name, payload):
        seen["thread"] = real_threading.current_thread()
        seen["lock_held"] = log._get_lock(name).locked()
        try:
            real_asyncio.get_running_loop()
        except RuntimeError:
            seen["on_loop"] = False
        else:
            seen["on_loop"] = True
        return True

    rec.register(EVT_FACT_ADDED, handler)
    assert await rec.areconcile("小天") == 1

    assert seen["on_loop"] is False, "handler 跑在事件循环上（阻塞 IO + 退避失效）"
    assert seen["thread"] is not real_threading.main_thread(), \
        "handler 跑在主线程上，说明没经过 to_thread"
    assert seen["lock_held"] is True, "handler 没在 per-character 锁内跑"
    assert log.read_sentinel("小天") == eid


@pytest.mark.asyncio
async def test_reconciler_rescans_in_journal_order_after_a_sentinel_conflict(tmp_path):
    """Main regression for the conflict branch. Rules out both wrong answers.

    Setup: a crash left two unapplied events (r1 and r2 get "repair"). The
    reconciler reads that tail, and only then a live writer commits through
    record_and_save — it writes r2="live" from its pre-replay snapshot and
    claims the sentinel for its own event, which is the last line of the
    journal. Everything the reconciler is still holding is now behind that
    sentinel.

    Neither obvious branch is acceptable:
      - keep applying the list we hold: r2 is rolled back from "live" to
        "repair", and the live event is ahead of the frozen sentinel, so no
        later boot ever corrects it. Silent, permanent regression of state
        the user just produced;
      - stop the round: r1's repair is dropped, and it is behind the
        sentinel too, so it is equally unreplayable. Silent, permanent loss
        of a crash repair.

    The round instead re-reads from its own last-applied position to the end
    of the journal and replays that in journal order, so both survive: the
    repair lands, and the live event replays on top of the stale one that
    collides with it. Handler order is asserted because that IS the
    mechanism — last write wins by journal position.
    """
    from memory.event_log import EVT_REFLECTION_STATE_CHANGED

    log, rec = _fresh_reconciler(str(tmp_path))
    view_path = os.path.join(str(tmp_path), "小天", "view.json")
    os.makedirs(os.path.dirname(view_path), exist_ok=True)

    def _write_view(data: dict) -> None:
        with open(view_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read_view() -> dict:
        with open(view_path, encoding="utf-8") as f:
            return json.load(f)

    _write_view({"r1": "old", "r2": "old"})

    # Crash leftovers: neither entry has been applied to the view
    log.append("小天", EVT_REFLECTION_STATE_CHANGED, {"rid": "r1", "to": "repair"})
    log.append("小天", EVT_REFLECTION_STATE_CHANGED, {"rid": "r2", "to": "repair"})

    applied_calls: list[tuple[str, str]] = []

    def handler(name, payload):
        # Same shape as make_reflection_evidence_handler: read from disk → update target entry → write back
        applied_calls.append((payload["rid"], payload["to"]))
        data = _read_view()
        if data.get(payload["rid"]) == payload["to"]:
            return False
        data[payload["rid"]] = payload["to"]
        _write_view(data)
        return True

    rec.register(EVT_REFLECTION_STATE_CHANGED, handler)

    live_event_id: dict[str, str] = {}
    real_aread_since = log.aread_since
    live_write_done = {"yes": False}

    async def _tail_then_live_write(name, after_event_id):
        tail = await real_aread_since(name, after_event_id)
        if live_write_done["yes"]:
            # Do not write a second time during rescan: this test expects "one live write + one rescan"
            return tail
        live_write_done["yes"] = True
        # Real record_and_save: view uses the snapshot from before replay (r1 is still old),
        # and cleanup unconditionally advances the sentinel to its own event (last line in the log).
        live_event_id["id"] = log.record_and_save(
            name, EVT_REFLECTION_STATE_CHANGED, {"rid": "r2", "to": "live"},
            sync_load_view=lambda n: {"r1": "old", "r2": "live"},
            sync_mutate_view=lambda v: None,
            sync_save_view=lambda n, v: _write_view(v),
        )
        return tail

    from memory import event_log as event_log_module

    with patch.object(log, "aread_since", _tail_then_live_write), \
            patch.object(event_log_module.logger, "warning") as warn:
        await rec.areconcile("小天")

    # Attribution: the conflict is "stale pending event, switch to rescan," not "handler failed" or "sentinel write
    # failed (view was already repaired)" — those latter messages would lead operators to believe disk is in a completely different state.
    messages = [str(c.args[0]) for c in warn.call_args_list]
    assert len(messages) == 1, f"该只有一条告警: {messages}"
    assert "重扫" in messages[0], f"归因错了: {messages[0]}"
    assert "handler 失败" not in messages[0] and "哨兵写入失败" not in messages[0], \
        f"冲突被记成了 handler / 哨兵 IO 失败: {messages[0]}"

    final = _read_view()
    assert final["r2"] == "live", (
        "陈旧的 r2 事件盖掉了 live writer 刚写好的值 —— 它那条事件在哨兵前面，"
        "不会再被重放回来纠正，是静默永久回退"
    )
    assert final["r1"] == "repair", (
        "尾巴上的修复被丢掉了 —— 哨兵已经越过它，之后任何一次开机都不会再重放"
    )
    # Mechanism assertion: after rescan, replay in log order; the live event follows the stale event that conflicts with it.
    assert applied_calls == [
        ("r1", "repair"), ("r2", "repair"), ("r2", "live"),
    ], f"重放顺序不是日志顺序: {applied_calls}"
    assert log.read_sentinel("小天") == live_event_id["id"], "哨兵被写回了旧位置"


@pytest.mark.asyncio
async def test_frozen_rescan_picks_up_events_that_land_after_its_snapshot(tmp_path):
    """A writer arriving after the rescan's snapshot must not be overwritten."""
    # The previous test pins down "switch to rescan after a conflict." This one pins down the rescan boundary itself: the
    # "end of log" it reads is a snapshot, while a writer is still appending later entries.
    #
    # Timing (each step is constructed precisely by patching aread_since):
    #   1. Three crash-leftover repairs r1/r2/r3 have not been applied;
    #   2. While the reconciler reads the initial tail, writer A commits r2="live" and advances the sentinel to its own event
    #      → the first CAS conflicts, so freeze the sentinel and switch to rescan;
    #   3. **At the moment the rescan snapshot is captured**, writer B commits r3="live2", whose event falls after the snapshot;
    #   4. The stale e3 (r3="repair") earlier in the snapshot is replayed and overwrites the value B just wrote.
    #
    # The sentinel now points to B's event. At next startup, read_since begins **after** it, so B's event will
    # never be replayed to correct the value — what is lost is not this run's progress but content the user just created, permanently.
    # Therefore after each scan, probe toward the tail again until no new entries are found.
    from memory.event_log import EVT_REFLECTION_STATE_CHANGED

    log, rec = _fresh_reconciler(str(tmp_path))
    view_path = os.path.join(str(tmp_path), "小天", "view.json")
    os.makedirs(os.path.dirname(view_path), exist_ok=True)

    def _write_view(data: dict) -> None:
        with open(view_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read_view() -> dict:
        with open(view_path, encoding="utf-8") as f:
            return json.load(f)

    _write_view({"r1": "old", "r2": "old", "r3": "old"})
    for rid in ("r1", "r2", "r3"):
        log.append("小天", EVT_REFLECTION_STATE_CHANGED, {"rid": rid, "to": "repair"})

    applied_calls: list[tuple[str, str]] = []

    def handler(name, payload):
        applied_calls.append((payload["rid"], payload["to"]))
        data = _read_view()
        if data.get(payload["rid"]) == payload["to"]:
            return False
        data[payload["rid"]] = payload["to"]
        _write_view(data)
        return True

    rec.register(EVT_REFLECTION_STATE_CHANGED, handler)

    real_aread_since = log.aread_since
    written: dict[str, str] = {}
    # Insert writers only after the first two reads: the first creates the conflict; the second (rescan snapshot) creates
    # an event that lands "after the snapshot." Do not insert more during later probe reads or this run would never finish.
    pending_writers = [("A", "r2", "live"), ("B", "r3", "live2")]

    async def _tail_then_writer(name, after_event_id):
        # Read the tail before writing: the returned copy **does not include** the entry committed immediately afterward, which reproduces the required case.
        tail = await real_aread_since(name, after_event_id)
        if pending_writers:
            who, rid, to = pending_writers.pop(0)
            snapshot = _read_view()
            snapshot[rid] = to
            written[who] = log.record_and_save(
                name, EVT_REFLECTION_STATE_CHANGED, {"rid": rid, "to": to},
                sync_load_view=lambda n, s=snapshot: dict(s),
                sync_mutate_view=lambda v: None,
                sync_save_view=lambda n, v: _write_view(v),
            )
        return tail

    with patch.object(log, "aread_since", _tail_then_writer):
        await rec.areconcile("小天")

    final = _read_view()
    assert final["r3"] == "live2", (
        "写者 B 的事件在重扫取完快照之后才落地，快照里排在它前面的陈旧事件把它"
        "盖掉了；而哨兵此刻停在 B 那条上，下次开机不会重放它 —— 静默永久丢失"
    )
    assert final["r2"] == "live", "写者 A 的值也不许被陈旧事件盖掉"
    assert final["r1"] == "repair", "崩溃遗留的修复不许被丢"
    # Mechanism assertion: B's event is recovered by a later scan **after** snapshot replay, not accidentally included in the middle.
    assert applied_calls == [
        ("r1", "repair"), ("r2", "repair"), ("r3", "repair"),
        ("r2", "live"), ("r3", "live2"),
    ], f"重放顺序不对: {applied_calls}"
    assert log.read_sentinel("小天") == written["B"], "哨兵被写回了旧位置"


@pytest.mark.asyncio
async def test_frozen_rescan_stops_after_a_bounded_number_of_probes(tmp_path):
    """Endless appends must end the round loudly, not spin the boot forever."""
    # Rescan means "probe until nothing new appears," so it needs a bound: startup must not hang if writes never stop.
    # Stopping has a cost (remaining entries before the sentinel will not be replayed automatically next startup), so this also
    # pins down "must report it" — silent truncation would make it look as though reconciliation finished cleanly.
    import asyncio

    from memory.event_log import EVT_REFLECTION_STATE_CHANGED
    from memory import event_log as event_log_module

    log, rec = _fresh_reconciler(str(tmp_path))
    view_path = os.path.join(str(tmp_path), "小天", "view.json")
    os.makedirs(os.path.dirname(view_path), exist_ok=True)

    def _write_view(data: dict) -> None:
        with open(view_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    _write_view({})
    log.append("小天", EVT_REFLECTION_STATE_CHANGED, {"rid": "r0", "to": "repair"})
    rec.register(EVT_REFLECTION_STATE_CHANGED, lambda name, payload: True)

    real_aread_since = log.aread_since
    reads = {"n": 0}

    async def _tail_then_endless_writer(name, after_event_id):
        tail = await real_aread_since(name, after_event_id)
        reads["n"] += 1
        # Append after every read forever.
        log.record_and_save(
            name, EVT_REFLECTION_STATE_CHANGED, {"rid": f"w{reads['n']}", "to": "live"},
            sync_load_view=lambda n: {},
            sync_mutate_view=lambda v: None,
            sync_save_view=lambda n, v: _write_view(v),
        )
        return tail

    with patch.object(log, "aread_since", _tail_then_endless_writer), \
            patch.object(event_log_module.logger, "warning") as warn:
        await asyncio.wait_for(rec.areconcile("小天"), timeout=20)

    assert reads["n"] <= event_log_module._MAX_FROZEN_RESCANS + 3, (
        f"补扫没有上界，读了 {reads['n']} 次"
    )
    messages = [str(c.args[0]) for c in warn.call_args_list]
    assert any("停止重扫" in m and "不会自动补上" in m for m in messages), (
        f"截断了却没报出来: {messages}"
    )


@pytest.mark.asyncio
async def test_reconciler_never_rewinds_a_sentinel_onto_an_unhandled_event(tmp_path):
    """Regression: a rewound sentinel can wedge a character's replay forever.

    The reconciler reads its tail, then a live writer appends an event this
    binary has no handler for and advances the sentinel onto it. Writing our
    own (older) event_id back would put that event into the tail again, and
    every future boot would stop on "unregistered event type" before applying
    anything — the character never reconciles again.

    The post-conflict rescan reaches that unhandled event too and pauses on
    it, which is the normal forward-compat behaviour. What must not happen is
    the sentinel moving back in front of it.
    """
    from memory.event_log import EVT_FACT_ADDED, EVT_FACT_ABSORBED

    log, rec = _fresh_reconciler(str(tmp_path))
    eid1 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    log.append("小天", EVT_FACT_ADDED, {"fact_id": "f2"})

    live_event_id: dict[str, str] = {}
    real_aread_since = log.aread_since

    async def _tail_then_live_write(name, after_event_id):
        # After reading the tail but before apply, insert a live writer: append an event whose
        # handler is not registered in this process and advance the sentinel (the final steps of record_and_save).
        tail = await real_aread_since(name, after_event_id)
        if "id" not in live_event_id:
            live_event_id["id"] = log.append(name, EVT_FACT_ABSORBED, {"fact_id": "f9"})
            log.advance_sentinel(name, live_event_id["id"])
        return tail

    applied_calls: list[str] = []

    def handler(name, payload):
        applied_calls.append(payload.get("fact_id"))
        return True

    rec.register(EVT_FACT_ADDED, handler)

    with patch.object(log, "aread_since", _tail_then_live_write):
        await rec.areconcile("小天")

    assert applied_calls == ["f1", "f2"], \
        "尾巴剩下的修复也必须落盘（哨兵已经越过它们，跳过就是丢）"
    assert log.read_sentinel("小天") == live_event_id["id"], \
        f"哨兵被写回旧位置（{eid1} 那一带），没 handler 的事件会回到尾巴卡死后续 replay"
    # Hang criterion: the tail must be empty; otherwise the next startup hits an unregistered event type and stalls in place.
    assert log.read_since("小天", log.read_sentinel("小天")) == []
    assert await rec.areconcile("小天") == 0


@pytest.mark.asyncio
async def test_reconciler_blames_the_sentinel_write_not_the_handler(tmp_path):
    """A sentinel write failure must not be logged as a handler failure.

    On disk the two are different states — "view unchanged, retry next boot"
    versus "view already repaired, only the sentinel is behind" — and the
    warning is the only signal an operator gets.
    """
    from memory import event_log as event_log_module
    from memory.event_log import EVT_FACT_ADDED

    log, rec = _fresh_reconciler(str(tmp_path))
    log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})

    handler_calls = {"n": 0}

    def handler(name, payload):
        handler_calls["n"] += 1
        return True

    rec.register(EVT_FACT_ADDED, handler)

    def boom_write(*args, **kwargs):
        raise OSError(13, "simulated sentinel write failure")

    with patch("memory.event_log.atomic_write_json", boom_write), \
            patch.object(event_log_module.logger, "warning") as warn:
        applied = await rec.areconcile("小天")

    assert applied == 0
    assert handler_calls["n"] == 1, "handler 本身是成功跑完的"
    messages = [str(c.args[0]) for c in warn.call_args_list]
    assert len(messages) == 1, f"该只有一条告警: {messages}"
    assert "哨兵写入失败" in messages[0], f"归因错了: {messages[0]}"
    assert "handler 失败" not in messages[0], f"哨兵 IO 失败被记成 handler 失败: {messages[0]}"


@pytest.mark.asyncio
async def test_reconciler_no_tail_is_noop(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log, rec = _fresh_reconciler(str(tmp_path))
    eid = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    log.advance_sentinel("小天", eid)

    applied = await rec.areconcile("小天")
    assert applied == 0


# ── async duals ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_duals_mirror_sync(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    eid = await log.aappend("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    tail = await log.aread_since("小天", None)
    assert [r["event_id"] for r in tail] == [eid]
    await log.aadvance_sentinel("小天", eid)
    assert await log.aread_sentinel("小天") == eid


@pytest.mark.asyncio
async def test_arecord_and_save_roundtrip(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    stored = {"facts": []}

    def load(name):
        return stored

    def mutate(view):
        view["facts"].append("f1")

    def save(name, view):
        pass  # no-op: in-memory test

    eid = await log.arecord_and_save(
        "小天", EVT_FACT_ADDED, {"fact_id": "f1"},
        sync_load_view=load, sync_mutate_view=mutate, sync_save_view=save,
    )
    assert stored["facts"] == ["f1"]
    assert await log.aread_sentinel("小天") == eid


@pytest.mark.asyncio
async def test_unicode_payload_roundtrip(tmp_path):
    """Event payloads must preserve CJK / emoji content."""
    from memory.event_log import EVT_REFLECTION_SYNTHESIZED

    log = _fresh_log(str(tmp_path))
    payload = {"reflection_id": "ref_abc", "text_sha256": "deadbeef",
               "source_fact_ids": ["f1", "f2"], "note": "主人喜欢咖啡 ☕"}
    await log.aappend("小天", EVT_REFLECTION_SYNTHESIZED, payload)
    tail = await log.aread_since("小天", None)
    assert tail[0]["payload"] == payload


# ── per-character isolation ─────────────────────────────────────────


def test_separate_characters_dont_share_body(tmp_path):
    from memory.event_log import EVT_FACT_ADDED

    log = _fresh_log(str(tmp_path))
    id_a = log.append("小天", EVT_FACT_ADDED, {"k": "A"})
    id_b = log.append("小雪", EVT_FACT_ADDED, {"k": "B"})

    assert [r["event_id"] for r in log.read_since("小天", None)] == [id_a]
    assert [r["event_id"] for r in log.read_since("小雪", None)] == [id_b]
