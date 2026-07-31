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
    # 锁一拆，record_and_save 收尾那句哨兵落盘也跟着失去串行化，5 个线程会同时
    # os.replace 到同一个 events_applied.json —— Windows 上直接 WinError 5。哨兵
    # 落盘不是本用例的被测对象（上一条正向 oracle 才覆盖它），所以给每个线程一个
    # 独立的哨兵文件名：真实写盘路径照跑，只是不再互抢同一个目标。
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
    # 不能用固定 sleep 猜「线程起来了没」：线程启动延迟不受控，而 Windows 上
    # sleep 的实际时长又是 15.6ms 粒度的抛硬币；睡短了 compact 会在第一次并发
    # append 之前就跑完，用例静默退化成非并发、断言恒真。改成轮询到确实已经
    # 写出一条事件，并发前提才是确定的。
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
        # list.append 在 GIL 下是原子的，两个线程共写一条时间线是安全的
        timeline.append((real_threading.current_thread().name, step))

    import memory.event_log as event_log_module
    real_write_json = event_log_module.atomic_write_json

    def tracking_write_json(*args, **kwargs):
        # 哨兵写是两条路径的最后一步，必须也落在各自的临界区里
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

    import memory.event_log as event_log_module
    real_write_json = event_log_module.atomic_write_json

    def tracking_write_json(*args, **kwargs):
        mark("sentinel")
        return real_write_json(*args, **kwargs)

    record_inside = real_threading.Event()

    def load(name):
        mark("record_load")
        record_inside.set()
        # 临界区故意撑开 0.3s：无锁的 advance_sentinel 会在这段里插进来
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

    # 非空过证明：ADVANCE 确实是在 RECORD 还没写完 view 的时候就开跑了
    assert timeline.index(("ADVANCE", "advance_enter")) < \
        timeline.index(("RECORD", "record_save")), \
        f"ADVANCE 没赶在 RECORD 临界区里启动，测试是空过的: {timeline}"
    # 互斥证明：它的哨兵写仍然排在 RECORD 自己的哨兵写之后
    record_sentinel = [i for i, e in enumerate(timeline) if e == ("RECORD", "sentinel")]
    advance_sentinel = [i for i, e in enumerate(timeline) if e == ("ADVANCE", "sentinel")]
    assert len(record_sentinel) == 1 and len(advance_sentinel) == 1, f"哨兵写次数不对: {timeline}"
    assert advance_sentinel[0] > record_sentinel[0], \
        f"advance_sentinel 插进了 record_and_save 的临界区: {timeline}"
    assert log.read_sentinel("小天") == "evt-from-other-writer"


def test_apply_and_advance_refuses_to_move_the_sentinel_backwards(tmp_path):
    """A replay must never overwrite a sentinel that someone else moved.

    event_id is a uuid4, so "is the new id newer" cannot be answered by
    comparing ids; the replay carries the sentinel value it started from and
    the write is a compare-and-set against it. Rolling backwards would return
    the other writer's events to the tail, and any of them without a
    registered handler would then pause replay on every future boot.
    """
    from memory.event_log import SentinelConflictError

    log = _fresh_log(str(tmp_path))
    log.advance_sentinel("小天", "evt-live-writer")

    applied = {"n": 0}

    def apply_fn() -> bool:
        applied["n"] += 1
        return True

    with pytest.raises(SentinelConflictError):
        log.apply_and_advance(
            "小天", "evt-replayed", apply_fn, expected_sentinel="evt-stale",
        )

    assert applied["n"] == 1, "冲突判定必须在 handler 之后（handler 是幂等的，白跑无害）"
    assert log.read_sentinel("小天") == "evt-live-writer", "哨兵被写回了旧位置"


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
async def test_reconciler_freezes_instead_of_rewinding_a_moved_sentinel(tmp_path):
    """Regression: a rewound sentinel can wedge a character's replay forever.

    The reconciler reads its tail, then a live writer appends an event this
    binary has no handler for and advances the sentinel onto it. Writing our
    own (older) event_id back would put that event into the tail again, and
    every future boot would stop on "unregistered event type" before applying
    anything — the character never reconciles again.

    Freezing, not aborting: the rest of the tail is still applied. Those are
    crash repairs, and the sentinel is now ahead of them, so skipping them
    would lose them for good.
    """
    from memory.event_log import EVT_FACT_ADDED, EVT_FACT_ABSORBED

    log, rec = _fresh_reconciler(str(tmp_path))
    eid1 = log.append("小天", EVT_FACT_ADDED, {"fact_id": "f1"})
    log.append("小天", EVT_FACT_ADDED, {"fact_id": "f2"})

    live_event_id: dict[str, str] = {}
    real_aread_since = log.aread_since

    async def _tail_then_live_write(name, after_event_id):
        # 读完尾巴之后、apply 之前插一个 live writer：追加一条本进程没注册
        # handler 的事件并把哨兵推过去（record_and_save 的收尾就是这两步）。
        tail = await real_aread_since(name, after_event_id)
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
    # 卡死判据：尾巴必须是空的，否则下一次开机会撞未注册事件类型并原地不动。
    assert log.read_since("小天", log.read_sentinel("小天")) == []
    assert await rec.areconcile("小天") == 0


@pytest.mark.asyncio
async def test_reconciler_blames_the_sentinel_write_not_the_handler(tmp_path):
    """A sentinel write failure must not be logged as a handler failure.

    On disk the two are different states — "view unchanged, retry next boot"
    versus "view already repaired, only the sentinel is behind" — and the
    warning is the only signal an operator gets.
    """
    import memory.event_log as event_log_module
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
