"""Unit tests for the voice hot-swap passive-callback prime ride-along.

Passive (``delivery_mode="passive"`` / ai_behavior="read") callbacks never
mirror into ``pending_extra_replies`` (PR #2469), so a pure voice session has
no user-turn drain to carry them. Their delivery point is the next
NATURALLY-occurring hot swap: ``_select_passive_callbacks_for_swap_prime``
folds them into the new session's prime text as background context (PASSIVE
templates, never flipping ``skipped``), removal is deferred to promote
success (``_remove_swap_delivered_passive_cbs``), and the post-promote death
exits restore what was removed (``_restore_undelivered_swap_passive_cbs``).
"""
import pytest

import main_logic.core as core_module
from main_logic.proactive_delivery import (
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
    SWAP_PRIME_DELIVERY_CLAIM_KEY,
)

pytestmark = pytest.mark.unit


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
    mgr.master_name = "Master"
    mgr.user_language = "zh"
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    return mgr


def _passive_cb(summary, *, coalesce_key="", **extra):
    cb = {
        "event": "agent_task_callback",
        "origin": "event",
        "summary": summary,
        "detail": summary,
        "status": "completed",
        "delivery_mode": "passive",
        "coalesce_key": coalesce_key,
    }
    cb.update(extra)
    return cb


def _proactive_cb(summary, *, coalesce_key="", **extra):
    cb = _passive_cb(summary, coalesce_key=coalesce_key, **extra)
    cb["delivery_mode"] = "proactive"
    return cb


# ---------------------------------------------------------------------------
# _select_passive_callbacks_for_swap_prime
# ---------------------------------------------------------------------------

def test_select_picks_only_passive_and_keeps_queue_intact():
    mgr = _make_session_mgr()
    passive = _passive_cb("game state snapshot")
    proactive = _proactive_cb("respond now")
    mgr.pending_agent_callbacks = [passive, proactive]

    selected, text = mgr._select_passive_callbacks_for_swap_prime()

    assert selected == [passive]
    assert "game state snapshot" in text
    assert "respond now" not in text
    # Selection must NOT drain: removal is deferred to promote success.
    assert mgr.pending_agent_callbacks == [passive, proactive]
    assert passive.get(SWAP_PRIME_DELIVERY_CLAIM_KEY) is True


def test_select_returns_empty_when_no_passive():
    mgr = _make_session_mgr()
    mgr.pending_agent_callbacks = [_proactive_cb("respond now")]
    assert mgr._select_passive_callbacks_for_swap_prime() == ([], "")


def test_select_excludes_topic_hook_snapshots():
    mgr = _make_session_mgr()
    hook = _passive_cb("hook cue", channel="topic_hook")
    mgr.pending_agent_callbacks = [hook]
    selected, text = mgr._select_passive_callbacks_for_swap_prime()
    assert selected == [] and text == ""
    assert mgr.pending_agent_callbacks == [hook]


def test_select_retracts_stale_coalesced_and_acks_false():
    # Same-key superseded passive cue: dropped from selection, purged from
    # the live queue, and its ack resolved False — same hygiene as the
    # text-mode drain's delivery point.
    mgr = _make_session_mgr()
    stale_ack = _FakeAckFuture()
    stale = _passive_cb("old snapshot", coalesce_key="gs")
    stale["_coalesce_submit_seq"] = 1
    stale[DELIVERY_ACK_FUTURE_KEY] = stale_ack
    fresh = _passive_cb("new snapshot", coalesce_key="gs")
    fresh["_coalesce_submit_seq"] = 5
    mgr.pending_agent_callbacks = [stale, fresh]
    mgr._coalesce_latest = {"gs": 5}

    selected, text = mgr._select_passive_callbacks_for_swap_prime()

    assert selected == [fresh]
    assert "new snapshot" in text and "old snapshot" not in text
    assert mgr.pending_agent_callbacks == [fresh]
    assert stale_ack.done() and stale_ack.result is False


def test_select_shares_token_budget_with_extras(monkeypatch):
    # The extras selected for this swap consume the shared budget first;
    # passive only takes what is left over (here: nothing).
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_TOTAL_MAX_TOKENS", 60)
    mgr = _make_session_mgr()
    passive = _passive_cb("context cue")
    mgr.pending_agent_callbacks = [passive]
    extras = [{"summary": "announced cue", "detail": "announced cue"}]

    selected, text = mgr._select_passive_callbacks_for_swap_prime(
        extras_selected=extras,
    )

    assert selected == [] and text == ""
    # Without extras the same budget admits the passive cue.
    selected, text = mgr._select_passive_callbacks_for_swap_prime()
    assert selected == [passive]
    assert "context cue" in text


# ---------------------------------------------------------------------------
# _remove_swap_delivered_passive_cbs
# ---------------------------------------------------------------------------

def test_remove_at_promote_dequeues_by_identity_and_acks_true():
    mgr = _make_session_mgr()
    ack = _FakeAckFuture()
    delivered = _passive_cb("delivered")
    delivered[DELIVERY_ACK_FUTURE_KEY] = ack
    other = _passive_cb("delivered")  # equal content, different object
    mgr.pending_agent_callbacks = [delivered, other]

    removed = mgr._remove_swap_delivered_passive_cbs([delivered])

    assert removed == [delivered]
    assert mgr.pending_agent_callbacks == [other]
    assert ack.done() and ack.result is True


def test_text_drain_skips_swap_prime_claimed_callback():
    mgr = _make_session_mgr()
    ack = _FakeAckFuture()
    claimed = _passive_cb("provider already has this context")
    claimed[DELIVERY_ACK_FUTURE_KEY] = ack
    mgr.pending_agent_callbacks = [claimed]

    selected, _ = mgr._select_passive_callbacks_for_swap_prime()
    rendered = mgr.drain_agent_callbacks_for_llm()

    assert rendered == ""
    assert mgr.pending_agent_callbacks == [claimed]
    assert not ack.done()
    mgr._release_swap_prime_passive_claims(selected)


def test_claimed_same_key_rejects_older_late_arrival():
    mgr = _make_session_mgr()
    old_ack = _FakeAckFuture()
    late_ack = _FakeAckFuture()
    claimed = _passive_cb("current snapshot", coalesce_key="state")
    claimed["_coalesce_submit_seq"] = 5
    claimed[DELIVERY_ACK_FUTURE_KEY] = old_ack
    mgr.enqueue_agent_callback(claimed)
    selected, _ = mgr._select_passive_callbacks_for_swap_prime()

    late = _passive_cb("late stale snapshot", coalesce_key="state")
    late["_coalesce_submit_seq"] = 2
    late[DELIVERY_ACK_FUTURE_KEY] = late_ack
    mgr.enqueue_agent_callback(late)

    assert mgr.pending_agent_callbacks == [claimed]
    assert not old_ack.done()
    assert late_ack.done() and late_ack.result is False
    assert late.get(DELIVERY_RETRACTED_KEY) is True
    mgr._release_swap_prime_passive_claims(selected)


def test_remove_noops_for_entries_consumed_in_window():
    # A text-turn drain (or purge/cap) consumed the entry between prime and
    # promote: identity match finds nothing, nothing is acked twice.
    mgr = _make_session_mgr()
    consumed = _passive_cb("consumed")
    mgr.pending_agent_callbacks = []
    assert mgr._remove_swap_delivered_passive_cbs([consumed]) == []


# ---------------------------------------------------------------------------
# _restore_undelivered_swap_passive_cbs
# ---------------------------------------------------------------------------

def test_restore_puts_removed_cbs_back_at_queue_head():
    mgr = _make_session_mgr()
    removed = _passive_cb("lost with dead session", _callback_delivery_id="id-1")
    newer = _passive_cb("already queued", _callback_delivery_id="id-2")
    mgr.pending_agent_callbacks = [newer]

    mgr._restore_undelivered_swap_passive_cbs([removed])

    assert mgr.pending_agent_callbacks == [removed, newer]


def test_restore_skips_topic_hook_requeued_and_retracted():
    mgr = _make_session_mgr()
    hook = _passive_cb("hook", channel="topic_hook", _callback_delivery_id="id-h")
    retracted = _passive_cb("retracted", _callback_delivery_id="id-r")
    retracted[DELIVERY_RETRACTED_KEY] = True
    requeued = _passive_cb("old copy", _callback_delivery_id="id-q")
    newer_same_id = _passive_cb("new copy", _callback_delivery_id="id-q")
    mgr.pending_agent_callbacks = [newer_same_id]

    mgr._restore_undelivered_swap_passive_cbs([hook, retracted, requeued])

    assert mgr.pending_agent_callbacks == [newer_same_id]


def test_restore_respects_flood_cap(monkeypatch):
    import config

    monkeypatch.setattr(config, "AGENT_CALLBACK_QUEUE_MAX_ITEMS", 2)
    mgr = _make_session_mgr()
    queued = _passive_cb("queued", _callback_delivery_id="id-q")
    mgr.pending_agent_callbacks = [queued]
    r1 = _passive_cb("restored 1", _callback_delivery_id="id-1")
    r2 = _passive_cb("restored 2", _callback_delivery_id="id-2")

    mgr._restore_undelivered_swap_passive_cbs([r1, r2])

    # drop-oldest keeps the LAST N entries, matching enqueue's flood guard.
    assert mgr.pending_agent_callbacks == [r2, queued]
