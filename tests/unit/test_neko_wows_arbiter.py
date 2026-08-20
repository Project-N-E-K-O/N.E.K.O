"""Arbitration: ranking, TTL, cooldowns, coalescing, preemption, rollback."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.domain.catalog import (
    BATTLE_ENDED,
    BATTLE_STARTED,
    BOUNDARY_RISK,
    DAMAGE_MILESTONE,
    DEVASTATING_STRIKE,
    ENEMY_CLOSING,
    ENEMY_SUNK,
    HIGH_DAMAGE,
    LOCALLY_ISOLATED,
    LOW_HP_TARGET,
    LOW_HEALTH,
    MULTI_DIRECTION_THREAT,
    OWN_BROADSIDE_EXPOSED,
    OWN_SHIP_SUNK,
    OUTNUMBERED,
    POST_BATTLE_SUMMARY,
    PRIORITY_TARGET,
    RAPID_DAMAGE,
    SITUATION_ADVICE,
    spec_for,
)
from plugin.plugins.neko_wows.domain.contracts import (
    CATEGORY_GEOMETRY,
    INTRUSION_NO_INTERRUPT,
    LANE_NORMAL,
    LANE_URGENT,
    WowsConfig,
)
from plugin.plugins.neko_wows.policy.arbiter import (
    Arbiter,
    REASON_ATTACHED,
    REASON_CHOSEN,
    REASON_COALESCED,
    REASON_COOLDOWN,
    REASON_EXPIRED,
    REASON_LANE_GAP,
    REASON_ONCE_PER_BATTLE,
    REASON_PAUSED,
    REASON_PREEMPTED,
    REASON_QUIET_WINDOW,
)
from plugin.plugins.neko_wows.policy.tactic_policy import AdviceCandidate

CFG = WowsConfig()


def candidate(
    event_id,
    *,
    at=100.0,
    priority=None,
    severity=50,
    ttl=None,
    seq=1,
    detail=None,
):
    spec = spec_for(event_id)
    lane_ttl = ttl if ttl is not None else CFG.ttl_for(spec.lane)
    return AdviceCandidate(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority if priority is None else priority,
        severity=severity,
        at=at,
        seq=seq,
        battle_id="b-1",
        summary=spec.summary,
        detail=dict(detail or {}),
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


def test_only_one_candidate_is_primary_per_round():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide(
        [candidate(LOW_HEALTH), candidate(ENEMY_CLOSING)], 100.0)
    assert decision.chosen.event_id == LOW_HEALTH
    assert tuple(item.event_id for item in decision.attached) == (
        ENEMY_CLOSING,
    )
    assert decision.queued == 0


def test_post_battle_summary_is_bundled_with_the_terminal_cue():
    decision = Arbiter(CFG).decide([
        candidate(BATTLE_ENDED),
        candidate(POST_BATTLE_SUMMARY),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        BATTLE_ENDED,
        POST_BATTLE_SUMMARY,
    )
    assert decision.queued == 0


def test_decide_bundles_an_eligible_event_from_a_recent_queue_round():
    arbiter = Arbiter(CFG)
    arbiter.submit([candidate(BOUNDARY_RISK, at=100.0)], 100.0)

    decision = arbiter.decide(
        [candidate(DEVASTATING_STRIKE, at=101.0)], 101.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        DEVASTATING_STRIKE,
        BOUNDARY_RISK,
    )
    assert REASON_ATTACHED in outcomes(decision, BOUNDARY_RISK)
    assert decision.queued == 0


def test_attach_window_includes_fifteen_and_excludes_sixteen():
    decision = Arbiter(CFG).decide([
        candidate(BOUNDARY_RISK, priority=70),
        candidate(BATTLE_STARTED, priority=55),
        candidate(HIGH_DAMAGE, priority=54),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        BOUNDARY_RISK,
        BATTLE_STARTED,
    )
    assert decision.queued == 1


def test_decide_caps_the_bundle_at_four_in_stable_rank_order():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([
        candidate(BOUNDARY_RISK, priority=80, severity=60, at=100.0),
        candidate(BATTLE_ENDED, priority=75, severity=40, at=100.0),
        candidate(LOCALLY_ISOLATED, priority=75, severity=90, at=100.0),
        candidate(HIGH_DAMAGE, priority=70, severity=50, at=102.0),
        candidate(LOW_HP_TARGET, priority=70, severity=50, at=101.0),
    ], 103.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        BOUNDARY_RISK,
        LOCALLY_ISOLATED,
        BATTLE_ENDED,
        LOW_HP_TARGET,
    )
    assert decision.queued == 1


def test_ranking_is_reproducible_regardless_of_input_order():
    events = [DAMAGE_MILESTONE, ENEMY_CLOSING, LOW_HEALTH, PRIORITY_TARGET]
    first = Arbiter(CFG).decide([candidate(e) for e in events], 100.0)
    second = Arbiter(CFG).decide([candidate(e) for e in reversed(events)], 100.0)
    assert tuple(item.event_id for item in first.candidates) == tuple(
        item.event_id for item in second.candidates
    )


def test_devastating_is_urgent_and_outranks_geometry():
    assert spec_for(DEVASTATING_STRIKE).lane == LANE_URGENT
    assert spec_for(DEVASTATING_STRIKE).preempt is True
    assert spec_for(DEVASTATING_STRIKE).priority > spec_for(BOUNDARY_RISK).priority
    assert spec_for(DEVASTATING_STRIKE).priority < spec_for(RAPID_DAMAGE).priority
    assert spec_for(HIGH_DAMAGE).priority > spec_for(DAMAGE_MILESTONE).priority


def test_devastating_is_not_held_by_the_normal_lane_gap():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(BATTLE_STARTED, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    decision = arbiter.decide(
        [candidate(DEVASTATING_STRIKE, at=105.0)], 105.0)
    assert decision.chosen.event_id == DEVASTATING_STRIKE


def test_devastating_attaches_to_rapid_damage_instead_of_being_dropped():
    decision = Arbiter(CFG).decide([
        candidate(RAPID_DAMAGE, at=100.0),
        candidate(DEVASTATING_STRIKE, at=100.0),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        RAPID_DAMAGE,
        DEVASTATING_STRIKE,
    )
    assert REASON_ATTACHED in outcomes(decision, DEVASTATING_STRIKE)
    assert decision.queued == 0


def test_enemy_sunk_is_a_high_priority_normal_praise_event():
    spec = spec_for(ENEMY_SUNK)
    assert spec.lane == LANE_NORMAL
    assert spec.coalesce_key == "wows_praise"
    assert spec.attach_group == spec_for(HIGH_DAMAGE).attach_group
    assert spec.attach_group == spec_for(DEVASTATING_STRIKE).attach_group
    assert spec.attach_group
    assert spec.priority >= 65
    assert spec.preempt is False


def test_enemy_sunk_attaches_high_damage_outside_the_default_window():
    decision = Arbiter(CFG).decide([
        candidate(ENEMY_SUNK),
        candidate(HIGH_DAMAGE),
    ], 100.0)

    assert decision.chosen.event_id == ENEMY_SUNK
    assert tuple(item.event_id for item in decision.attached) == (HIGH_DAMAGE,)
    assert REASON_ATTACHED in outcomes(decision, HIGH_DAMAGE)


def test_enemy_sunk_attaches_to_an_urgent_devastating_strike():
    decision = Arbiter(CFG).decide([
        candidate(ENEMY_SUNK),
        candidate(DEVASTATING_STRIKE),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        DEVASTATING_STRIKE,
        ENEMY_SUNK,
    )


def test_high_damage_coalesces_with_devastating_without_a_sink():
    decision = Arbiter(CFG).decide([
        candidate(DEVASTATING_STRIKE),
        candidate(HIGH_DAMAGE),
    ], 100.0)

    assert decision.chosen.event_id == DEVASTATING_STRIKE
    assert decision.attached == ()
    assert REASON_COALESCED in outcomes(decision, HIGH_DAMAGE)
    assert decision.queued == 0


def test_progress_bursts_on_different_targets_do_not_coalesce():
    decision = Arbiter(CFG).decide([
        candidate(DEVASTATING_STRIKE, detail={"target_name": "Dev"}),
        candidate(HIGH_DAMAGE, detail={"target_name": "High"}),
    ], 100.0)

    assert decision.chosen.event_id == DEVASTATING_STRIKE
    assert decision.attached == ()
    assert REASON_COALESCED not in outcomes(decision, HIGH_DAMAGE)
    assert REASON_PREEMPTED in outcomes(decision, HIGH_DAMAGE)
    assert decision.queued == 0


def test_same_spoken_name_on_different_ships_does_not_coalesce():
    decision = Arbiter(CFG).decide([
        candidate(
            DEVASTATING_STRIKE,
            detail={"target_name": "Zao", "target_id": 3002},
        ),
        candidate(
            HIGH_DAMAGE,
            detail={"target_name": "Zao", "target_id": 3003},
        ),
    ], 100.0)

    assert decision.chosen.event_id == DEVASTATING_STRIKE
    assert decision.attached == ()
    assert REASON_COALESCED not in outcomes(decision, HIGH_DAMAGE)
    assert REASON_PREEMPTED in outcomes(decision, HIGH_DAMAGE)
    assert decision.queued == 0


def test_enemy_sunk_attaches_to_devastating_while_a_weaker_burst_is_preempted():
    decision = Arbiter(CFG).decide([
        candidate(ENEMY_SUNK, detail={"target_name": "Dev"}),
        candidate(DEVASTATING_STRIKE, detail={"target_name": "Dev"}),
        candidate(HIGH_DAMAGE, detail={"target_name": "High"}),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        DEVASTATING_STRIKE,
        ENEMY_SUNK,
    )
    assert REASON_PREEMPTED in outcomes(decision, HIGH_DAMAGE)


def test_enemy_sunk_does_not_coalesce_away_a_damage_burst():
    arbiter = Arbiter(CFG)
    steps = arbiter.submit(
        [candidate(ENEMY_SUNK), candidate(HIGH_DAMAGE)], 100.0)
    coalesced = [step for step in steps if step.outcome == REASON_COALESCED]
    assert coalesced == []
    assert arbiter.stats()["queued"] == 2



def test_situation_advice_and_outnumbered_priorities_are_swapped():
    assert spec_for(SITUATION_ADVICE).priority == 50
    assert spec_for(OUTNUMBERED).priority == 25


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


def test_cancel_events_removes_only_matching_queued_candidates():
    arbiter = Arbiter(CFG)
    arbiter.submit([
        candidate(BATTLE_ENDED, at=100.0),
        candidate(DAMAGE_MILESTONE, at=100.0),
    ], 100.0)

    assert arbiter.cancel_events({BATTLE_ENDED}) == 1
    decision = arbiter.decide([], 100.0)
    assert decision.chosen.event_id == DAMAGE_MILESTONE


def test_apply_config_drops_queued_candidates_for_disabled_category():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
        "urgent_ttl_seconds": 120.0,
        "normal_ttl_seconds": 120.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    # BOUNDARY_RISK has no preempt, so the normal-lane sibling stays queued.
    held = arbiter.decide([
        candidate(BOUNDARY_RISK, at=101.0, ttl=120.0),
        candidate(DAMAGE_MILESTONE, at=101.0, ttl=120.0),
    ], 101.0)
    assert held.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(held, BOUNDARY_RISK)
    assert arbiter.stats()["queued"] == 2

    cfg.disabled_categories = (CATEGORY_GEOMETRY,)
    arbiter.apply_config(cfg)

    assert arbiter.stats()["queued"] == 1
    released = arbiter.decide([], 200.0)
    assert released.chosen is not None
    assert released.chosen.event_id == DAMAGE_MILESTONE


def test_apply_config_drops_queued_candidates_for_disabled_lane():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
        "urgent_ttl_seconds": 120.0,
        "normal_ttl_seconds": 120.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    held = arbiter.decide([
        candidate(BOUNDARY_RISK, at=101.0, ttl=120.0),
        candidate(DAMAGE_MILESTONE, at=101.0, ttl=120.0),
    ], 101.0)
    assert held.chosen is None
    assert arbiter.stats()["queued"] == 2

    cfg.disabled_lanes = (LANE_URGENT,)
    arbiter.apply_config(cfg)

    assert arbiter.stats()["queued"] == 1
    released = arbiter.decide([], 200.0)
    assert released.chosen is not None
    assert released.chosen.event_id == DAMAGE_MILESTONE
    assert released.chosen.lane == LANE_NORMAL


def test_lane_ttls_come_from_config():
    assert CFG.ttl_for(LANE_URGENT) == 12.0
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


def test_devastating_cooldown_is_per_target():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([
        candidate(DEVASTATING_STRIKE, at=100.0, detail={"victim_id": 3002}),
    ], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    later = 100.0 + CFG.urgent_min_gap_seconds + 0.1
    other = arbiter.decide([
        candidate(DEVASTATING_STRIKE, at=later, detail={"victim_id": 3003}),
    ], later)
    assert other.chosen.event_id == DEVASTATING_STRIKE

    same = arbiter.decide([
        candidate(DEVASTATING_STRIKE, at=later, detail={"victim_id": 3002}),
    ], later)
    assert same.chosen is None
    assert REASON_COOLDOWN in outcomes(same, DEVASTATING_STRIKE)


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


def test_a_blocked_candidate_is_not_attached_and_keeps_its_audit_reason():
    arbiter = Arbiter(CFG)
    first = arbiter.decide(
        [candidate(DEVASTATING_STRIKE, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    later = 100.0 + CFG.urgent_min_gap_seconds + 0.1
    decision = arbiter.decide([
        candidate(BOUNDARY_RISK, at=later),
        candidate(DEVASTATING_STRIKE, at=later),
    ], later)

    assert tuple(item.event_id for item in decision.candidates) == (
        BOUNDARY_RISK,
    )
    assert REASON_COOLDOWN in outcomes(decision, DEVASTATING_STRIKE)


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


def test_reset_battle_clears_the_lane_gap():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(DAMAGE_MILESTONE, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    held = arbiter.decide([candidate(BATTLE_STARTED, at=101.0)], 101.0)
    assert held.chosen is None
    assert REASON_LANE_GAP in outcomes(held, BATTLE_STARTED)

    arbiter.reset_battle("b-2")
    again = arbiter.decide([candidate(BATTLE_STARTED, at=101.0)], 101.0)
    assert again.chosen.event_id == BATTLE_STARTED


def test_reset_battle_clears_the_quiet_window():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    held = arbiter.decide([candidate(DAMAGE_MILESTONE, at=101.0)], 101.0)
    assert held.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(held, DAMAGE_MILESTONE)

    arbiter.reset_battle("b-2")
    again = arbiter.decide([candidate(DAMAGE_MILESTONE, at=101.0)], 101.0)
    assert again.chosen.event_id == DAMAGE_MILESTONE


# --- failure rollback ----------------------------------------------------

@pytest.mark.parametrize(("reason", "committed"), [
    ("delivered", True),
    ("dry_run", True),
    ("failed", False),
    ("paused", False),
    ("expired", False),
])
def test_commit_reports_whether_the_detector_may_latch(reason, committed):
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([candidate(BATTLE_STARTED)], 100.0)
    assert arbiter.commit(
        decision.chosen, 100.0, outcome_reason=reason) is committed


def test_commit_applies_the_delivery_outcome_to_every_bundled_event():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([
        candidate(BOUNDARY_RISK, at=100.0),
        candidate(BATTLE_STARTED, at=100.0),
    ], 100.0)

    assert arbiter.commit(
        decision.candidates,
        100.0,
        outcome_reason="delivered",
    ) is True
    assert arbiter.stats()["cooldowns"] == 2
    assert arbiter.stats()["fired_once_per_battle"] == [BATTLE_STARTED]
    assert arbiter.stats()["lanes"][LANE_URGENT] == 100.0
    assert arbiter.stats()["lanes"][LANE_NORMAL] == 100.0


def test_failed_bundle_applies_and_resume_clears_every_failure_cooldown():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([
        candidate(BOUNDARY_RISK, at=100.0),
        candidate(BATTLE_STARTED, at=100.0),
    ], 100.0)

    assert arbiter.commit(
        decision.candidates,
        100.0,
        outcome_reason="failed",
    ) is False
    assert arbiter.stats()["cooldowns"] == 2
    assert arbiter.stats()["fired_once_per_battle"] == []
    assert arbiter.stats()["lanes"][LANE_URGENT] == 0.0
    assert arbiter.stats()["lanes"][LANE_NORMAL] == 0.0

    arbiter.resume()
    assert arbiter.stats()["cooldowns"] == 0


@pytest.mark.parametrize("reason", ["paused", "expired"])
def test_suppressed_delivery_does_not_start_event_cooldown(reason):
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason=reason)

    retry = arbiter.decide([candidate(LOW_HEALTH, at=101.0)], 101.0)
    assert retry.chosen is not None


def test_resume_clears_failure_cooldown_but_keeps_delivery_cooldown():
    arbiter = Arbiter(CFG)
    failed = arbiter.decide([candidate(LOW_HEALTH, at=100.0)], 100.0)
    arbiter.commit(failed.chosen, 100.0, outcome_reason="failed")

    delivered = arbiter.decide(
        [candidate(DAMAGE_MILESTONE, at=100.0)], 100.0)
    arbiter.commit(delivered.chosen, 100.0, outcome_reason="delivered")

    arbiter.pause()
    arbiter.resume()

    assert arbiter.decide(
        [candidate(LOW_HEALTH, at=101.0)], 101.0).chosen is not None
    still_cooled = arbiter.decide(
        [candidate(DAMAGE_MILESTONE, at=101.0)], 101.0)
    assert still_cooled.chosen is None
    assert REASON_COOLDOWN in outcomes(still_cooled, DAMAGE_MILESTONE)


def test_a_failed_delivery_still_takes_the_cooldown():
    """A failure must not become a tight retry loop."""
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

def test_same_round_coalescing_keeps_the_strongest_candidate():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([
        candidate(HIGH_DAMAGE, at=100.0, severity=55),
        candidate(DAMAGE_MILESTONE, at=100.0, severity=25),
    ], 100.0)

    assert decision.chosen.event_id == HIGH_DAMAGE
    assert REASON_COALESCED in outcomes(decision, DAMAGE_MILESTONE)


def test_expired_strong_candidate_cannot_hide_an_eligible_sibling():
    arbiter = Arbiter(CFG)
    decision = arbiter.decide([
        candidate(HIGH_DAMAGE, at=100.0, severity=55, ttl=-1.0),
        candidate(DAMAGE_MILESTONE, at=100.0, severity=25),
    ], 100.0)

    assert decision.chosen.event_id == DAMAGE_MILESTONE
    assert REASON_EXPIRED in outcomes(decision, HIGH_DAMAGE)


def test_a_newer_weaker_candidate_does_not_replace_a_queued_sibling():
    arbiter = Arbiter(CFG)
    arbiter.submit([candidate(DEVASTATING_STRIKE, at=100.0)], 100.0)
    steps = arbiter.submit([candidate(HIGH_DAMAGE, at=101.0)], 101.0)
    assert REASON_COALESCED in [step.outcome for step in steps]
    assert arbiter.stats()["queued"] == 1
    decision = arbiter.decide([], 101.0)
    assert decision.chosen.event_id == DEVASTATING_STRIKE


def test_a_newer_stronger_candidate_replaces_a_queued_sibling():
    arbiter = Arbiter(CFG)
    arbiter.submit([candidate(HIGH_DAMAGE, at=100.0)], 100.0)
    steps = arbiter.submit([candidate(DEVASTATING_STRIKE, at=101.0)], 101.0)
    assert REASON_COALESCED in [step.outcome for step in steps]
    decision = arbiter.decide([], 101.0)
    assert decision.chosen.event_id == DEVASTATING_STRIKE


def test_a_cooled_preemptor_does_not_drop_the_queue():
    arbiter = Arbiter(CFG)
    first = arbiter.decide([candidate(RAPID_DAMAGE, at=100.0)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    decision = arbiter.decide([
        candidate(RAPID_DAMAGE, at=101.0),
        candidate(HIGH_DAMAGE, at=101.0),
    ], 101.0)

    assert decision.chosen.event_id == HIGH_DAMAGE
    assert REASON_COOLDOWN in outcomes(decision, RAPID_DAMAGE)
    assert REASON_PREEMPTED not in outcomes(decision, HIGH_DAMAGE)


def test_a_preempting_event_clears_lower_priority_queue_entries():
    arbiter = Arbiter(CFG)
    arbiter.submit([candidate(DAMAGE_MILESTONE, at=100.0)], 100.0)
    arbiter.submit([candidate(PRIORITY_TARGET, at=100.0)], 100.0)
    steps = arbiter.submit([candidate(OWN_SHIP_SUNK, at=101.0)], 101.0)
    assert REASON_PREEMPTED in [step.outcome for step in steps]
    assert arbiter.stats()["queued"] == 1


def test_a_preempting_event_keeps_candidates_inside_the_attach_window():
    arbiter = Arbiter(CFG)
    arbiter.submit([
        candidate(OWN_BROADSIDE_EXPOSED, at=100.0),
        candidate(DEVASTATING_STRIKE, at=100.0),
        candidate(BATTLE_ENDED, at=100.0),
    ], 100.0)

    decision = arbiter.decide(
        [candidate(MULTI_DIRECTION_THREAT, at=101.0)], 101.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        MULTI_DIRECTION_THREAT,
        DEVASTATING_STRIKE,
        OWN_BROADSIDE_EXPOSED,
    )
    assert REASON_PREEMPTED in outcomes(decision, BATTLE_ENDED)


def test_same_round_preemption_drops_a_later_outside_window_candidate():
    arbiter = Arbiter(CFG)

    decision = arbiter.decide([
        candidate(OWN_SHIP_SUNK, at=100.0),
        candidate(DAMAGE_MILESTONE, at=100.0),
    ], 100.0)

    assert tuple(item.event_id for item in decision.candidates) == (
        OWN_SHIP_SUNK,
    )
    assert REASON_PREEMPTED in outcomes(decision, DAMAGE_MILESTONE)
    assert decision.queued == 0


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
