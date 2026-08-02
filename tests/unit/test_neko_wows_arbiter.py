"""Arbitration: ranking, TTL, cooldowns, coalescing, preemption, rollback."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.domain.catalog import (
    BATTLE_STARTED,
    DAMAGE_MILESTONE,
    ENEMY_CLOSING,
    LOW_HEALTH,
    OWN_SHIP_SUNK,
    PRIORITY_TARGET,
    RAPID_DAMAGE,
    spec_for,
)
from plugin.plugins.neko_wows.domain.contracts import (
    LANE_NORMAL,
    LANE_URGENT,
    WowsConfig,
)
from plugin.plugins.neko_wows.policy.arbiter import (
    Arbiter,
    REASON_CHOSEN,
    REASON_COALESCED,
    REASON_COOLDOWN,
    REASON_EXPIRED,
    REASON_LANE_GAP,
    REASON_ONCE_PER_BATTLE,
    REASON_PAUSED,
    REASON_PREEMPTED,
)
from plugin.plugins.neko_wows.policy.tactic_policy import AdviceCandidate

CFG = WowsConfig()


def candidate(event_id, *, at=100.0, severity=50, ttl=None, seq=1):
    spec = spec_for(event_id)
    lane_ttl = ttl if ttl is not None else CFG.ttl_for(spec.lane)
    return AdviceCandidate(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority,
        severity=severity,
        at=at,
        seq=seq,
        battle_id="b-1",
        summary=spec.summary,
        expires_at=at + lane_ttl,
    )


def outcomes(decision, event_id):
    return [step.outcome for step in decision.chain if step.event_id == event_id]


# --- ranking -------------------------------------------------------------

def test_higher_priority_wins():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide(
        [candidate(DAMAGE_MILESTONE), candidate(OWN_SHIP_SUNK)], 100.0)
    assert decision.chosen.event_id == OWN_SHIP_SUNK


def test_severity_breaks_a_priority_tie():
    arbiter = Arbiter(CFG)
    quiet = candidate(LOW_HEALTH, severity=60)
    loud = candidate(LOW_HEALTH, severity=95)
    decision = arbiter.decide([quiet, loud], 100.0)
    assert decision.chosen.severity == 95


def test_only_one_candidate_is_chosen_per_round():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide(
        [candidate(LOW_HEALTH), candidate(ENEMY_CLOSING)], 100.0)
    assert decision.chosen is not None
    assert decision.queued == 1


def test_ranking_is_reproducible_regardless_of_input_order():
    events = [DAMAGE_MILESTONE, ENEMY_CLOSING, LOW_HEALTH, PRIORITY_TARGET]
    first = Arbiter(CFG).decide([candidate(e) for e in events], 100.0)
    second = Arbiter(CFG).decide([candidate(e) for e in reversed(events)], 100.0)
    assert first.chosen.event_id == second.chosen.event_id


# --- TTL -----------------------------------------------------------------

def test_a_candidate_that_expired_before_arriving_is_dropped():
    arbiter = Arbiter(CFG)
    stale = candidate(LOW_HEALTH, at=100.0)
    decision = arbiter.decide([stale], 100.0 + CFG.urgent_ttl_seconds + 1.0)
    assert decision.chosen is None
    assert REASON_EXPIRED in outcomes(decision, LOW_HEALTH)


def test_a_queued_candidate_expires_while_waiting():
    arbiter = Arbiter(CFG)
    arbiter.decide([candidate(LOW_HEALTH), candidate(ENEMY_CLOSING)], 100.0)
    later = arbiter.decide([], 100.0 + CFG.urgent_ttl_seconds + 1.0)
    assert later.chosen is None
    assert later.queued == 0


def test_lane_ttls_come_from_config():
    assert CFG.ttl_for(LANE_URGENT) == 8.0
    assert CFG.ttl_for(LANE_NORMAL) == 30.0
    assert CFG.min_gap_for(LANE_URGENT) == 6.0
    assert CFG.min_gap_for(LANE_NORMAL) == 18.0


# --- cooldown and lane pacing -------------------------------------------

def test_the_same_event_is_held_by_its_cooldown():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    again = arbiter.decide([candidate(LOW_HEALTH, at=101.0)], 101.0)
    assert again.chosen is None
    assert REASON_COOLDOWN in outcomes(again, LOW_HEALTH)


def test_a_different_event_is_held_by_the_lane_gap():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    soon = arbiter.decide([candidate(ENEMY_CLOSING, at=101.0)], 101.0)
    assert soon.chosen is None
    assert REASON_LANE_GAP in outcomes(soon, ENEMY_CLOSING)


def test_the_lane_reopens_after_the_gap():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    later = 100.0 + CFG.urgent_min_gap_seconds + 0.1
    reopened = arbiter.decide([candidate(ENEMY_CLOSING, at=later)], later)
    assert reopened.chosen.event_id == ENEMY_CLOSING


def test_lanes_pace_independently():
    arbiter = Arbiter(CFG)
    urgent = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(urgent.chosen, 100.0, outcome_reason="delivered")

    normal = arbiter.decide([candidate(DAMAGE_MILESTONE, at=101.0)], 101.0)
    assert normal.chosen.event_id == DAMAGE_MILESTONE


# --- once per battle -----------------------------------------------------

def test_once_per_battle_is_spent_after_a_delivery():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(BATTLE_STARTED, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    much_later = 100.0 + 600.0
    again = arbiter.decide([candidate(BATTLE_STARTED, at=much_later)], much_later)
    assert again.chosen is None
    assert REASON_ONCE_PER_BATTLE in outcomes(again, BATTLE_STARTED)


def test_once_per_battle_survives_a_new_battle():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(BATTLE_STARTED, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    arbiter.reset_battle("b-2")
    again = arbiter.decide([candidate(BATTLE_STARTED, at=200.0)], 200.0)
    assert again.chosen is not None


# --- failure rollback ----------------------------------------------------

def test_a_failed_delivery_still_takes_the_cooldown():
    """One attempt only: a failure must not become a retry loop."""
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="failed")

    again = arbiter.decide([candidate(LOW_HEALTH, at=101.0)], 101.0)
    assert again.chosen is None
    assert REASON_COOLDOWN in outcomes(again, LOW_HEALTH)


def test_a_failed_delivery_does_not_advance_the_lane_gap():
    """Nothing was heard, so there is nothing to pace against."""
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="failed")

    other = arbiter.decide([candidate(ENEMY_CLOSING, at=101.0)], 101.0)
    assert other.chosen.event_id == ENEMY_CLOSING


def test_a_failed_once_per_battle_event_can_fire_again():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(BATTLE_STARTED, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="failed")

    later = 100.0 + spec_for(BATTLE_STARTED).cooldown_seconds + 1.0
    again = arbiter.decide([candidate(BATTLE_STARTED, at=later)], later)
    assert again.chosen is not None


def test_a_dry_run_counts_as_committed_so_the_shadow_chain_is_realistic():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(BATTLE_STARTED, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="dry_run")

    later = 100.0 + 600.0
    again = arbiter.decide([candidate(BATTLE_STARTED, at=later)], later)
    assert again.chosen is None
    assert REASON_ONCE_PER_BATTLE in outcomes(again, BATTLE_STARTED)


def test_clearing_shadow_state_reopens_everything():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="dry_run")
    arbiter.clear_shadow_state()

    again = arbiter.decide([candidate(LOW_HEALTH, at=101.0)], 101.0)
    assert again.chosen is not None


# --- coalescing and preemption ------------------------------------------

def test_a_newer_candidate_replaces_a_queued_sibling():
    arbiter = Arbiter(CFG)
    # Both sit in the survival coalesce group; only the newest should survive.
    arbiter.submit([candidate(LOW_HEALTH, at=100.0, severity=60)], 100.0)
    steps = arbiter.submit([candidate(RAPID_DAMAGE, at=101.0)], 101.0)
    assert REASON_COALESCED in [step.outcome for step in steps]
    assert arbiter.stats()["queued"] == 1


def test_a_preempting_event_clears_lower_priority_queue_entries():
    arbiter = Arbiter(CFG)
    arbiter.submit([candidate(DAMAGE_MILESTONE, at=100.0)], 100.0)
    arbiter.submit([candidate(PRIORITY_TARGET, at=100.0)], 100.0)
    steps = arbiter.submit([candidate(OWN_SHIP_SUNK, at=101.0)], 101.0)
    assert REASON_PREEMPTED in [step.outcome for step in steps]
    assert arbiter.stats()["queued"] == 1


def test_preemption_never_touches_something_already_handed_over():
    """The queue is the only place preemption happens."""
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([candidate(DAMAGE_MILESTONE, at=100.0)], 100.0)
    assert decision.chosen.event_id == DAMAGE_MILESTONE
    arbiter.commit(decision.chosen, 100.0, outcome_reason="delivered")

    # The delivered candidate is gone from the queue entirely, so a later urgent
    # event cannot claim to have cancelled it.
    steps = arbiter.submit([candidate(OWN_SHIP_SUNK, at=101.0)], 101.0)
    assert REASON_PREEMPTED not in [step.outcome for step in steps]


# --- pausing -------------------------------------------------------------

def test_pausing_blocks_every_candidate():
    arbiter = Arbiter(CFG)
    arbiter.pause()
    decision = arbiter.decide([candidate(OWN_SHIP_SUNK, at=100.0)], 100.0)
    assert decision.chosen is None
    assert decision.reason == REASON_PAUSED


def test_resuming_lets_the_queue_drain():
    arbiter = Arbiter(CFG)
    arbiter.pause()
    arbiter.decide([candidate(OWN_SHIP_SUNK, at=100.0)], 100.0)
    arbiter.resume()
    decision = arbiter.decide([], 101.0)
    assert decision.chosen is not None
    assert decision.reason == REASON_CHOSEN


# --- audit trail ---------------------------------------------------------

def test_every_decision_records_a_reason():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide(
        [candidate(LOW_HEALTH, at=100.0), candidate(ENEMY_CLOSING, at=100.0)],
        100.0)
    assert decision.chain
    assert all(step.outcome for step in decision.chain)


def test_the_reason_explains_an_empty_round():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")
    blocked = arbiter.decide([candidate(LOW_HEALTH, at=101.0)], 101.0)
    assert blocked.reason == REASON_COOLDOWN
