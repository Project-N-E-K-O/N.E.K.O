"""Broadcast preferences: category and lane switches, and the quiet window."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.detectors._base import GameEvent
from plugin.plugins.neko_wows.domain.catalog import (
    DAMAGE_MILESTONE,
    LOW_HEALTH,
    OWN_SHIP_SUNK,
    PRIORITY_TARGET,
    spec_for,
)
from plugin.plugins.neko_wows.domain.contracts import (
    ALL_CATEGORIES,
    ALL_INTRUSION_MODES,
    CATEGORY_PROGRESS,
    CATEGORY_SURVIVAL,
    INTRUSION_ALLOW_INTERRUPT,
    INTRUSION_CRITICAL_ONLY,
    INTRUSION_NO_INTERRUPT,
    LANE_NORMAL,
    LANE_URGENT,
    WowsConfig,
)
from plugin.plugins.neko_wows.domain.facts import WowsFacts
from plugin.plugins.neko_wows.policy.arbiter import (
    Arbiter,
    REASON_QUIET_WINDOW,
)
from plugin.plugins.neko_wows.policy.tactic_policy import (
    AdviceCandidate,
    WowsTacticPolicy,
)


def facts(at=100.0):
    return WowsFacts(seq=1, at=at, battle_id="b-1", own_hp_ratio=0.4)


def event(event_id, at=100.0):
    return GameEvent(
        event_id=event_id, severity=70, at=at, seq=1, battle_id="b-1", detail={})


def candidate(event_id, *, at=100.0, cfg=None):
    settings = cfg or WowsConfig()
    spec = spec_for(event_id)
    return AdviceCandidate(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority,
        severity=70,
        at=at,
        seq=1,
        battle_id="b-1",
        summary=spec.summary,
        expires_at=at + settings.ttl_for(spec.lane),
    )


def outcomes(decision, event_id):
    return [step.outcome for step in decision.chain if step.event_id == event_id]


# --- category and lane switches ------------------------------------------

def test_every_catalog_category_is_a_known_preference():
    """A category the panel cannot show is a category nobody can turn off."""
    from plugin.plugins.neko_wows.domain.catalog import EVENT_CATALOG

    used = {spec.coalesce_key for spec in EVENT_CATALOG.values()}
    assert used <= set(ALL_CATEGORIES)


def test_a_disabled_category_never_becomes_a_candidate():
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_SURVIVAL]})
    policy = WowsTacticPolicy(cfg)
    candidates = policy.expand(
        [event(LOW_HEALTH), event(DAMAGE_MILESTONE)], facts())
    assert [item.event_id for item in candidates] == [DAMAGE_MILESTONE]


def test_disabling_one_category_leaves_the_others_alone():
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_PROGRESS]})
    candidates = WowsTacticPolicy(cfg).expand(
        [event(LOW_HEALTH), event(DAMAGE_MILESTONE)], facts())
    assert [item.event_id for item in candidates] == [LOW_HEALTH]


def test_a_disabled_lane_drops_every_event_in_it():
    cfg = WowsConfig.from_mapping({"disabled_lanes": [LANE_URGENT]})
    candidates = WowsTacticPolicy(cfg).expand(
        [event(OWN_SHIP_SUNK), event(LOW_HEALTH), event(PRIORITY_TARGET)], facts())
    assert [item.event_id for item in candidates] == [PRIORITY_TARGET]


def test_disabled_events_do_not_consume_a_cooldown_slot():
    """Filtering in policy, not delivery, is what makes this true."""
    cfg = WowsConfig.from_mapping({"disabled_categories": [CATEGORY_SURVIVAL]})
    policy = WowsTacticPolicy(cfg)
    arbiter = Arbiter(cfg)

    decision = arbiter.decide(policy.expand([event(LOW_HEALTH)], facts()), 100.0)
    assert decision.chosen is None
    assert arbiter.stats()["queued"] == 0
    assert arbiter.stats()["cooldowns"] == 0


def test_unknown_preference_names_are_ignored():
    cfg = WowsConfig.from_mapping({
        "disabled_categories": ["not_a_category", CATEGORY_SURVIVAL],
        "disabled_lanes": ["sideways", LANE_NORMAL],
    })
    assert cfg.disabled_categories == (CATEGORY_SURVIVAL,)
    assert cfg.disabled_lanes == (LANE_NORMAL,)


def test_a_non_list_preference_disables_nothing():
    cfg = WowsConfig.from_mapping({"disabled_categories": "wows_survival"})
    assert cfg.disabled_categories == ()


def test_lane_and_category_helpers_agree_with_the_config():
    cfg = WowsConfig.from_mapping({
        "disabled_categories": [CATEGORY_SURVIVAL],
        "disabled_lanes": [LANE_URGENT],
    })
    assert cfg.category_enabled(CATEGORY_SURVIVAL) is False
    assert cfg.category_enabled(CATEGORY_PROGRESS) is True
    assert cfg.lane_enabled(LANE_URGENT) is False
    assert cfg.lane_enabled(LANE_NORMAL) is True


# --- intrusion policy ----------------------------------------------------

def test_the_default_policy_is_critical_only():
    assert WowsConfig().dialogue_intrusion_mode == INTRUSION_CRITICAL_ONLY


def test_an_unknown_mode_falls_back_to_critical_only():
    cfg = WowsConfig.from_mapping({"dialogue_intrusion_mode": "whatever"})
    assert cfg.dialogue_intrusion_mode == INTRUSION_CRITICAL_ONLY


def test_all_modes_are_accepted():
    for mode in ALL_INTRUSION_MODES:
        cfg = WowsConfig.from_mapping({"dialogue_intrusion_mode": mode})
        assert cfg.dialogue_intrusion_mode == mode


def test_no_interrupt_holds_back_even_an_urgent_call_out():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    decision = arbiter.decide([candidate(OWN_SHIP_SUNK, at=110.0, cfg=cfg)], 110.0)
    assert decision.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(decision, OWN_SHIP_SUNK)


def test_critical_only_lets_urgent_through_but_holds_normal():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_CRITICAL_ONLY,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    normal = arbiter.decide([candidate(DAMAGE_MILESTONE, at=110.0, cfg=cfg)], 110.0)
    assert normal.chosen is None
    assert REASON_QUIET_WINDOW in outcomes(normal, DAMAGE_MILESTONE)

    urgent = arbiter.decide([candidate(LOW_HEALTH, at=111.0, cfg=cfg)], 111.0)
    assert urgent.chosen is not None
    assert urgent.chosen.lane == LANE_URGENT


def test_allow_interrupt_ignores_the_window_entirely():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_ALLOW_INTERRUPT,
        "user_chat_quiet_window_seconds": 600.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    decision = arbiter.decide([candidate(DAMAGE_MILESTONE, at=101.0, cfg=cfg)], 101.0)
    assert decision.chosen is not None


def test_the_window_expires_on_its_own():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 30.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    blocked = arbiter.decide([candidate(LOW_HEALTH, at=120.0, cfg=cfg)], 120.0)
    assert blocked.chosen is None

    later = 100.0 + 31.0
    reopened = arbiter.decide([candidate(LOW_HEALTH, at=later, cfg=cfg)], later)
    assert reopened.chosen is not None


def test_a_zero_length_window_never_suppresses():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 0.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    assert arbiter.decide(
        [candidate(LOW_HEALTH, at=100.0, cfg=cfg)], 100.0).chosen is not None


def test_the_reason_says_it_was_the_plugin_that_suppressed():
    """The host has its own gate; the two must be distinguishable."""
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    decision = arbiter.decide([candidate(LOW_HEALTH, at=110.0, cfg=cfg)], 110.0)
    detail = next(
        step.detail for step in decision.chain
        if step.outcome == REASON_QUIET_WINDOW)
    assert "插件静默窗口" in detail
    assert INTRUSION_NO_INTERRUPT in detail


def test_clearing_the_window_reopens_output_immediately():
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 600.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    arbiter.clear_quiet_window()
    assert arbiter.decide(
        [candidate(LOW_HEALTH, at=101.0, cfg=cfg)], 101.0).chosen is not None


def test_the_window_is_reported_for_the_panel():
    cfg = WowsConfig.from_mapping({"user_chat_quiet_window_seconds": 45.0})
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)
    stats = arbiter.stats()
    assert stats["quiet_until"] == pytest.approx(145.0)
    assert stats["intrusion_mode"] == cfg.dialogue_intrusion_mode


def test_a_held_candidate_stays_queued_for_when_the_window_closes():
    """Suppression is not a drop: the queue keeps it until its TTL runs out."""
    cfg = WowsConfig.from_mapping({
        "dialogue_intrusion_mode": INTRUSION_NO_INTERRUPT,
        "user_chat_quiet_window_seconds": 4.0,
        "urgent_ttl_seconds": 60.0,
    })
    arbiter = Arbiter(cfg)
    arbiter.note_user_activity(100.0)

    held = arbiter.decide([candidate(LOW_HEALTH, at=101.0, cfg=cfg)], 101.0)
    assert held.chosen is None
    assert arbiter.stats()["queued"] == 1

    released = arbiter.decide([], 106.0)
    assert released.chosen is not None
    assert released.chosen.event_id == LOW_HEALTH


# --- timing overrides ----------------------------------------------------

def test_timing_overrides_take_effect_through_the_shared_config():
    cfg = WowsConfig()
    arbiter = Arbiter(cfg)
    first = arbiter.decide([candidate(LOW_HEALTH, at=100.0, cfg=cfg)], 100.0)
    arbiter.commit(first.chosen, 100.0, outcome_reason="delivered")

    # Default urgent gap is 6s, so this would normally be blocked.
    cfg.urgent_min_gap_seconds = 0.0
    arbiter.apply_config(cfg)
    from plugin.plugins.neko_wows.domain.catalog import ENEMY_CLOSING

    reopened = arbiter.decide([candidate(ENEMY_CLOSING, at=101.0, cfg=cfg)], 101.0)
    assert reopened.chosen is not None
