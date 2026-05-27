"""Unit tests for the generic ProactiveDeliveryManager front stage.

Covers the behaviours the manager adds in front of the existing enqueue/
trigger delivery core: priority ordering (lower = more urgent, unspecified
normalised to a neutral band), OPT-IN coalescing, the playback gate (don't
release while audio plays), BATCHED release (cues piled up while speaking go
out together in one turn), min-gap pacing, and drain-on-teardown (cues are
handed back, never silently dropped).
"""
import asyncio

import pytest

from main_logic.proactive_delivery import (
    NEUTRAL_PRIORITY,
    ProactiveDeliveryManager,
    effective_priority,
)

pytestmark = pytest.mark.unit


def _make(delivered, **kw):
    async def deliver(batch):
        # deliver receives the WHOLE batch (list of callbacks) per release.
        delivered.extend(batch)
    kw.setdefault("min_gap_s", 0.0)
    kw.setdefault("inflight_timeout_s", 0.05)
    return ProactiveDeliveryManager(deliver=deliver, **kw)


async def _settle():
    # Let scheduled call_later(0)/create_task work run.
    for _ in range(5):
        await asyncio.sleep(0.01)


def test_effective_priority_normalisation():
    assert effective_priority(1) == 1
    assert effective_priority(9) == 9
    # Unspecified (0) and junk fall to the neutral band, BELOW any explicit
    # urgency — so a cue that set any priority outranks one that didn't.
    assert effective_priority(0) == NEUTRAL_PRIORITY
    assert effective_priority(None) == NEUTRAL_PRIORITY
    assert effective_priority("x") == NEUTRAL_PRIORITY
    assert effective_priority(2) < effective_priority(0)


async def test_batch_released_together_in_priority_order():
    # Cues that pile up while she's speaking are released as ONE batch when
    # the gate opens, sorted by priority (lower = more urgent first).
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "keep_going"}, priority=4, coalesce_key="a")
    mgr.submit({"id": "alert"}, priority=1, coalesce_key="b")
    mgr.submit({"id": "unspecified"}, priority=0, coalesce_key="c")
    await _settle()
    assert delivered == []  # nothing released while playing
    mgr.on_playback_end()   # gate opens → whole batch released at once
    await _settle()
    assert [c["id"] for c in delivered] == ["alert", "keep_going", "unspecified"]


async def test_coalescing_is_opt_in():
    # Same explicit key → newest replaces older.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "old"}, priority=2, coalesce_key="dup")
    mgr.submit({"id": "new"}, priority=2, coalesce_key="dup")
    await _settle()
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["new"]


async def test_no_coalesce_key_never_collapses():
    # Unset key → unique → both delivered (no silent drop). This is the
    # non-regression guarantee for plugins that didn't opt in.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "a"}, priority=2)
    mgr.submit({"id": "b"}, priority=2)
    await _settle()
    mgr.on_playback_end()
    await _settle()
    assert sorted(c["id"] for c in delivered) == ["a", "b"]


async def test_playback_gate_holds_until_end():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "x"}, priority=1)
    await _settle()
    assert delivered == []          # gate closed while playing
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["x"]


async def test_second_batch_waits_for_next_play_end():
    # After one batch is released (in-flight), cues that arrive during its
    # playback must wait for the NEXT voice_play_end, not pile on immediately.
    delivered = []
    mgr = _make(delivered, inflight_timeout_s=5.0)
    mgr.submit({"id": "a"}, priority=1)
    await _settle()
    assert [c["id"] for c in delivered] == ["a"]   # first batch out (gate open)
    mgr.on_playback_start()                         # a is now playing
    mgr.submit({"id": "b"}, priority=1)             # arrives mid-playback
    await _settle()
    assert [c["id"] for c in delivered] == ["a"]   # b held, not delivered
    mgr.on_playback_end()
    await _settle()
    assert [c["id"] for c in delivered] == ["a", "b"]


async def test_min_gap_delays_release():
    delivered = []
    mgr = _make(delivered, min_gap_s=0.2)
    mgr.on_playback_start()
    mgr.submit({"id": "x"}, priority=1)
    mgr.on_playback_end()           # records last_play_end; gap not elapsed
    await asyncio.sleep(0.05)
    assert delivered == []          # still inside min-gap
    await asyncio.sleep(0.3)
    assert [c["id"] for c in delivered] == ["x"]


async def test_playing_watchdog_recovers_missing_play_end():
    # voice_play_start with no matching voice_play_end (frontend disconnect)
    # must not wedge the queue forever — the max_play watchdog re-opens it.
    delivered = []
    mgr = _make(delivered, max_play_s=0.1)
    mgr.on_playback_start()          # ...and voice_play_end never arrives
    mgr.submit({"id": "x"}, priority=1)
    await asyncio.sleep(0.05)
    assert delivered == []           # still within max_play window
    await asyncio.sleep(0.2)         # exceed watchdog
    assert [c["id"] for c in delivered] == ["x"]


async def test_drain_pending_returns_queue_without_delivering():
    # Teardown path: drain_pending hands queued cues back (for the caller to
    # move into pending_agent_callbacks) instead of dropping them.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()          # gate closed → cues queue up
    # Submit out of priority order; drain must return priority-asc (FIFO ties)
    # so redelivery preserves ordering.
    mgr.submit({"id": "b"}, priority=2)
    mgr.submit({"id": "a"}, priority=1)
    drained = mgr.drain_pending()
    assert [c["id"] for c in drained] == ["a", "b"]
    await _settle()
    assert delivered == []           # drained, not delivered by the manager


async def test_reset_gate_clears_gate_but_not_dropped_queue():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()          # gate closed
    mgr.reset_gate()                 # clears playing/inflight (queue untouched)
    mgr.submit({"id": "c"}, priority=1)
    await _settle()
    # Gate cleared → not playing → the new cue delivers immediately.
    assert [c["id"] for c in delivered] == ["c"]


async def test_stale_cue_dropped_by_ttl():
    delivered = []
    mgr = _make(delivered, ttl_s=0.05)
    mgr.on_playback_start()         # gate closed so the cue waits and ages
    mgr.submit({"id": "stale"}, priority=1)
    await asyncio.sleep(0.1)        # exceed ttl
    mgr.on_playback_end()
    await _settle()
    assert delivered == []          # dropped as stale, never spoken
