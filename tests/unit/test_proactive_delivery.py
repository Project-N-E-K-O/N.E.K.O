"""Unit tests for the generic ProactiveDeliveryManager front stage.

Covers the behaviours the manager adds in front of the existing enqueue/
trigger delivery core: priority ordering (HIGHER = more important, unspecified
0 = least), OPT-IN coalescing, the playback gate (don't release while audio
plays), BATCHED release (cues piled up while speaking go out together in one
turn), min-gap pacing, and drain-on-teardown (cues are handed back, never
silently dropped).
"""
import asyncio

import pytest

import main_logic.core as core_module
from main_logic.proactive_delivery import (
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
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
    # HIGHER = more important; unspecified / invalid → 0 (least important).
    assert effective_priority(1) == 1
    assert effective_priority(9) == 9
    assert effective_priority(0) == 0
    assert effective_priority(None) == 0
    assert effective_priority("x") == 0
    # A cue that set any positive priority outranks an unspecified one.
    assert effective_priority(2) > effective_priority(0)


async def test_batch_released_together_in_priority_order():
    # Cues that pile up while she's speaking are released as ONE batch when
    # the gate opens, sorted by importance DESC (higher first), unspecified last.
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    mgr.submit({"id": "keep_going"}, priority=3, coalesce_key="a")
    mgr.submit({"id": "alert"}, priority=9, coalesce_key="b")
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


async def test_coalescing_resolves_dropped_delivery_ack_false():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    old_future = asyncio.get_running_loop().create_future()
    mgr.submit({"id": "old", DELIVERY_ACK_FUTURE_KEY: old_future}, priority=2, coalesce_key="dup")
    mgr.submit({"id": "new"}, priority=2, coalesce_key="dup")

    assert old_future.done()
    assert old_future.result() is False


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


async def test_noop_release_frees_inflight_slot_immediately():
    # A release that delivers nothing (e.g. every cue dropped at a release-time
    # gate) emits no playback signal, so without an explicit release the slot
    # would stay armed for the whole inflight timeout and hold back the next
    # cue. release_inflight_noop frees it so the follow-up cue goes out promptly.
    delivered = []
    mgr = None

    async def deliver(batch):
        delivered.append([c for c in batch])
        if len(delivered) == 1:
            mgr.release_inflight_noop()  # simulate the gate-drop no-op release

    mgr = ProactiveDeliveryManager(deliver=deliver, min_gap_s=0.0, inflight_timeout_s=5.0)
    mgr.submit({"id": "dropped"}, priority=1)
    await _settle()
    assert len(delivered) == 1  # first batch released, delivered nothing

    mgr.submit({"id": "second"}, priority=1)
    await _settle()
    # slot was freed immediately, so 'second' is delivered within _settle
    # rather than after the 5s inflight timeout.
    assert [batch[0]["id"] for batch in delivered] == ["dropped", "second"]


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
    # Submit out of priority order; drain must return importance-DESC (FIFO
    # ties) so redelivery preserves ordering.
    mgr.submit({"id": "a"}, priority=1)
    mgr.submit({"id": "b"}, priority=2)
    drained = mgr.drain_pending()
    assert [c["id"] for c in drained] == ["b", "a"]
    await _settle()
    assert delivered == []           # drained, not delivered by the manager
    # And the queue really is empty now: opening the gate releases nothing.
    mgr.on_playback_end()
    await _settle()
    assert delivered == []


async def test_drain_pending_keeps_delivery_ack_pending_for_redelivery():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()
    future = asyncio.get_running_loop().create_future()
    mgr.submit({"id": "queued", DELIVERY_ACK_FUTURE_KEY: future}, priority=2)

    drained = mgr.drain_pending()

    assert [c["id"] for c in drained] == ["queued"]
    assert not future.done()


async def test_reset_gate_clears_gate_but_keeps_queue():
    delivered = []
    mgr = _make(delivered)
    mgr.on_playback_start()          # gate closed → cue queues up
    mgr.submit({"id": "queued"}, priority=2)
    mgr.reset_gate()                 # clears playing/inflight; queue PRESERVED
    await _settle()
    # reset_gate alone does NOT release (it cancels the pump, no auto-pump).
    assert delivered == []
    # Next submit re-opens the pump; the preserved cue rides out in the SAME
    # batch (importance order), proving reset_gate didn't drop the queue.
    mgr.submit({"id": "c"}, priority=1)
    await _settle()
    assert [c["id"] for c in delivered] == ["queued", "c"]


async def test_stale_cue_dropped_by_ttl():
    delivered = []
    mgr = _make(delivered, ttl_s=0.05)
    mgr.on_playback_start()         # gate closed so the cue waits and ages
    mgr.submit({"id": "stale"}, priority=1)
    await asyncio.sleep(0.1)        # exceed ttl
    mgr.on_playback_end()
    await _settle()
    assert delivered == []          # dropped as stale, never spoken


# ── enqueue_agent_callback path (passive / ai_behavior="read") ────────────────
# The ProactiveDeliveryManager above only governs proactive ("respond") cues.
# Passive/read cues bypass it and land directly in pending_agent_callbacks; the
# same OPT-IN coalesce_key semantics apply there so a rapid read-stream can
# dedup queued snapshots by key instead of piling up until the flood guard.


class _FakeAckFuture:
    """Minimal delivery-ack future stand-in (no event loop needed)."""

    def __init__(self):
        self._done = False
        self.result = None

    def done(self):
        return self._done

    def set_result(self, value):
        self._done = True
        self.result = value


def _make_session_mgr():
    mgr = core_module.LLMSessionManager.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    # Identity normalizer: isolate the per-source token-budget path, which is
    # irrelevant to coalescing and pulls in config/budget dependencies.
    mgr._normalize_context_text_for_source = lambda _src, text: text
    return mgr


def _passive_cb(summary, *, coalesce_key="", **extra):
    cb = {
        "event": "agent_task_callback",
        "origin": "event",
        "summary": summary,
        "detail": summary,
        "status": "completed",
        "coalesce_key": coalesce_key,
    }
    cb.update(extra)
    return cb


def test_enqueue_coalesce_same_key_newest_replaces():
    # Same explicit key → newest collapses the older queued cue, on BOTH the
    # LLM-inject queue and its voice-mode mirror (which drift, so the mirror is
    # evicted by delivery_id, not by position).
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("old snapshot", coalesce_key="gamestate"))
    mgr.enqueue_agent_callback(_passive_cb("new snapshot", coalesce_key="gamestate"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new snapshot"]
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["new snapshot"]


def test_enqueue_coalesce_empty_key_never_collapses():
    # Unset / explicit-empty key never coalesces — the non-regression guarantee
    # for read-cues that didn't opt in.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("a"))                    # no key
    mgr.enqueue_agent_callback(_passive_cb("b"))                    # no key
    mgr.enqueue_agent_callback(_passive_cb("c", coalesce_key=""))   # explicit empty
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["a", "b", "c"]
    assert len(mgr.pending_extra_replies) == 3


def test_enqueue_coalesce_distinct_keys_independent():
    # Only the matching key collapses; a different key is untouched.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("x1", coalesce_key="x"))
    mgr.enqueue_agent_callback(_passive_cb("y1", coalesce_key="y"))
    mgr.enqueue_agent_callback(_passive_cb("x2", coalesce_key="x"))
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["y1", "x2"]
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["y1", "x2"]


def test_enqueue_coalesce_resolves_superseded_ack_false():
    # A superseded cue's delivery-ack future resolves False immediately so a
    # waiter unblocks instead of stalling until timeout (parity with the
    # manager path).
    mgr = _make_session_mgr()
    fut = _FakeAckFuture()
    old = _passive_cb("old", coalesce_key="k")
    old[DELIVERY_ACK_FUTURE_KEY] = fut
    mgr.enqueue_agent_callback(old)
    mgr.enqueue_agent_callback(_passive_cb("new", coalesce_key="k"))
    assert fut.done() and fut.result is False
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new"]


def test_enqueue_coalesce_marks_superseded_retracted():
    # A superseded cue must be FLAGGED retracted, not merely dropped: a voice
    # delivery already in flight snapshots pending_agent_callbacks before its
    # await and re-filters that snapshot only by DELIVERY_RETRACTED_KEY. Without
    # the flag the captured stale cue is still spoken even though its ack was
    # resolved False.
    mgr = _make_session_mgr()
    old = _passive_cb("old", coalesce_key="k")
    mgr.enqueue_agent_callback(old)
    mgr.enqueue_agent_callback(_passive_cb("new", coalesce_key="k"))
    assert old.get(DELIVERY_RETRACTED_KEY) is True
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["new"]


def test_enqueue_coalesce_older_manager_release_loses_to_newer_read():
    # Cross-path newest-wins: a respond cue held in ProactiveDeliveryManager
    # (submission seq stamped at submit_proactive_callback) that is RELEASED into
    # enqueue AFTER a newer same-key read cue was direct-queued must NOT overwrite
    # the newer read cue. The submission seq lets enqueue tell the late manager
    # release from a genuinely newer cue.
    mgr = _make_session_mgr()
    # A respond cue stamped early, then held by the manager during playback.
    respond = _passive_cb("respond held", coalesce_key="k")
    respond["_coalesce_submit_seq"] = 1
    # A newer read cue enqueued directly gets a later seq.
    mgr._coalesce_seq_counter = 5  # next direct-enqueue seq = 6 > 1
    mgr.enqueue_agent_callback(_passive_cb("newer read", coalesce_key="k"))
    # The manager now releases the OLDER respond cue into the same queue.
    mgr.enqueue_agent_callback(respond)
    # Newer read cue survives; the stale respond is dropped AND retracted (so any
    # in-flight snapshot that captured it discards it too).
    assert [c["summary"] for c in mgr.pending_agent_callbacks] == ["newer read"]
    assert respond.get(DELIVERY_RETRACTED_KEY) is True


def test_enqueue_coalesce_guards_legacy_string_extra():
    # pending_extra_replies may hold a legacy plain-string entry that the render
    # path tolerates. A keyed enqueue must not raise on it (the broad except in
    # enqueue_agent_callback would otherwise swallow the error and silently drop
    # the new callback).
    mgr = _make_session_mgr()
    mgr.pending_extra_replies.append("legacy plain string")  # non-dict entry
    mgr.enqueue_agent_callback(_passive_cb("fresh", coalesce_key="k"))
    assert "fresh" in [
        c["summary"] for c in mgr.pending_agent_callbacks
    ]  # new callback survived, not swallowed
    assert "legacy plain string" in mgr.pending_extra_replies  # legacy left intact


def test_enqueue_coalesce_evicts_drained_extras_orphan():
    # After a text user turn, drain_agent_callbacks_for_llm clears the callback
    # side but KEEPS the paired voice mirror in pending_extra_replies. A later
    # same-key cue has no callback half to match by id, so eviction must be by
    # the stamped coalesce_key — otherwise hot-swap injects BOTH snapshots.
    mgr = _make_session_mgr()
    mgr.enqueue_agent_callback(_passive_cb("old snapshot", coalesce_key="gs"))
    mgr.pending_agent_callbacks.clear()  # simulate drain (callback side only)
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["old snapshot"]
    mgr.enqueue_agent_callback(_passive_cb("new snapshot", coalesce_key="gs"))
    assert [r["summary"] for r in mgr.pending_extra_replies] == ["new snapshot"]
