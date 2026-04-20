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
    time.sleep(0.02)  # let appender get going

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
