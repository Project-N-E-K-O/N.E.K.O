"""Capability gating, baselines and battle switches must not fabricate edges."""

from __future__ import annotations

import math

import pytest

from plugin.plugins.neko_wows.detectors._base import DetectorRegistry
from plugin.plugins.neko_wows.detectors.geometry import build_geometry_detectors
from plugin.plugins.neko_wows.detectors.lifecycle import build_lifecycle_detectors
from plugin.plugins.neko_wows.detectors.survival import build_survival_detectors
from plugin.plugins.neko_wows.detectors.targeting import build_targeting_detectors
from plugin.plugins.neko_wows.detectors.threat import build_threat_detectors
from plugin.plugins.neko_wows.domain.catalog import (
    BATTLE_ENDED,
    BATTLE_STARTED,
    BOUNDARY_RISK,
    DAMAGE_MILESTONE,
    ENEMY_CLOSING,
    EVENT_CATALOG,
    LOW_HEALTH,
    LOW_HP_TARGET,
    MULTI_DIRECTION_THREAT,
    OWN_BROADSIDE_EXPOSED,
    OWN_SHIP_SUNK,
    POST_BATTLE_SUMMARY,
    RAPID_DAMAGE,
)
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.domain.facts import FactBuilder
from plugin.plugins.neko_wows.domain.snapshot import (
    AVAIL_AVAILABLE,
    AVAIL_STALE,
    AVAIL_UNKNOWN,
    AVAIL_UNSUPPORTED,
    CORE_DOMAINS,
    FUTURE_DOMAINS,
    STATUS_ENDED,
    STATUS_LIVE,
    STATUS_STALE,
    STATUS_WAITING,
    SelfShip,
    Ship,
    WowsSnapshot,
)

CFG = WowsConfig()
BUILDER = FactBuilder(CFG)


def all_detectors(cfg=CFG):
    return (
        *build_lifecycle_detectors(cfg),
        *build_survival_detectors(cfg),
        *build_threat_detectors(cfg),
        *build_geometry_detectors(cfg),
        *build_targeting_detectors(cfg),
    )


def availability(**overrides):
    table = {domain: AVAIL_AVAILABLE for domain in CORE_DOMAINS}
    table.update({domain: AVAIL_UNSUPPORTED for domain in FUTURE_DOMAINS})
    table.update(overrides)
    return table


def enemy(*, ui_id=2, x=6000.0, z=0.0, yaw=0.0, hp_ratio=0.8,
          ship_type="Cruiser", name="Zao"):
    return Ship(
        ui_id=ui_id, player_id=3000 + ui_id, team_id=1, relation=2,
        ship_type=ship_type, name=name, tier=10, alive=True, visible=True,
        x=x, z=z, yaw=yaw, health=40000.0 * hp_ratio, max_health=40000.0,
        hp_ratio=hp_ratio,
    )


def ally(*, ui_id=10, x=500.0, z=0.0):
    return Ship(
        ui_id=ui_id, player_id=2100 + ui_id, team_id=0, relation=1,
        ship_type="Cruiser", name="Ally", tier=10, alive=True, visible=True,
        x=x, z=z, yaw=0.0, health=30000.0, max_health=40000.0, hp_ratio=0.75,
    )


def frame(
    *,
    seq=1,
    at=100.0,
    status=STATUS_LIVE,
    battle_id="b-1",
    instance_id="inst-a",
    hp_ratio=1.0,
    ships=(),
    yaw=0.0,
    x=0.0,
    z=0.0,
    damage=None,
    ballistics=None,
    bounds=(-21000.0, 21000.0, -21000.0, 21000.0),
    self_present=True,
    avail=None,
):
    own = None
    if self_present:
        own = SelfShip(
            player_id=2000, team_id=0, health=80000.0 * hp_ratio,
            max_health=80000.0, yaw=yaw, speed=25.0, x=x, z=z,
        )
    return WowsSnapshot(
        service_id="8111-for-wows",
        api_version="1.0",
        instance_id=instance_id,
        seq=seq,
        battle_id=battle_id,
        status=status,
        capabilities={d: True for d in CORE_DOMAINS}
        | {d: False for d in FUTURE_DOMAINS},
        availability=avail if avail is not None else availability(),
        active=status == STATUS_LIVE,
        ts=float(seq),
        battle_type="RandomBattle",
        game_mode="Domination",
        map_name="New Dawn",
        bounds=bounds,
        self_ship=own,
        ships=tuple(ships),
        damage_inflicted=damage,
        ballistics=ballistics or {"available": False},
        received_at=at,
        transport="ws",
        epoch=1,
    )


def feed(registry, snapshots, cfg=CFG):
    """Run a sequence of snapshots and return each frame's result."""
    results = []
    previous = None
    for snapshot in snapshots:
        current = (snapshot, BUILDER.build(snapshot))
        results.append(registry.feed(previous, current, cfg=cfg))
        previous = current
    return results


def fired(results):
    return [event.event_id for result in results for event in result.events]


# --- catalog integrity ---------------------------------------------------

def test_every_detector_event_exists_in_the_catalog():
    for detector in all_detectors():
        for event_id in detector.events:
            assert event_id in EVENT_CATALOG, f"{detector.name} -> {event_id}"


def test_catalog_required_domains_match_the_detector():
    for detector in all_detectors():
        for event_id in detector.events:
            spec = EVENT_CATALOG[event_id]
            assert set(spec.required) == set(detector.required), event_id


# --- capability gating ---------------------------------------------------

@pytest.mark.parametrize("detector", all_detectors(), ids=lambda d: d.name)
def test_detector_is_blocked_when_a_required_domain_is_missing(detector):
    if not detector.required:
        pytest.skip(f"{detector.name} has no required domains")
    for domain in detector.required:
        registry = DetectorRegistry((detector,))
        blocked_avail = availability(**{domain: AVAIL_UNKNOWN})
        results = feed(registry, [
            frame(seq=1, at=100.0, avail=blocked_avail),
            frame(seq=2, at=101.0, avail=blocked_avail, hp_ratio=0.1),
        ])
        names = [entry.detector for result in results for entry in result.blocked]
        assert detector.name in names, f"{detector.name} ran without {domain}"
        assert not fired(results)


@pytest.mark.parametrize("detector", all_detectors(), ids=lambda d: d.name)
def test_stale_domain_blocks_just_like_a_missing_one(detector):
    if not detector.required:
        pytest.skip(f"{detector.name} has no required domains")
    domain = detector.required[0]
    registry = DetectorRegistry((detector,))
    stale_avail = availability(**{domain: AVAIL_STALE})
    results = feed(registry, [
        frame(seq=1, at=100.0, avail=stale_avail),
        frame(seq=2, at=101.0, avail=stale_avail, hp_ratio=0.1),
    ])
    names = [entry.detector for result in results for entry in result.blocked]
    assert detector.name in names


def test_blocked_record_names_the_missing_domains():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, avail=availability(self=AVAIL_UNKNOWN)),
        frame(seq=2, at=101.0, avail=availability(self=AVAIL_UNKNOWN)),
    ])
    missing = {
        entry.detector: entry.missing
        for result in results for entry in result.blocked
    }
    assert missing["low_health"] == ("self",)


# --- baselines and discontinuities --------------------------------------

def test_first_frame_only_builds_a_baseline():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [frame(seq=1, hp_ratio=0.1)])
    assert not fired(results)
    assert results[0].baseline_only is True


def test_recovery_from_stale_does_not_replay_the_whole_change():
    """The gap across a stale patch must not look like one huge instant drop."""
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, hp_ratio=0.95),
        frame(seq=3, at=102.0, status=STATUS_STALE, hp_ratio=0.95,
              avail=availability(self=AVAIL_STALE, objects=AVAIL_STALE)),
        # First live frame after recovery: health is far lower, but this pair is
        # not comparable, so nothing may fire.
        frame(seq=4, at=140.0, hp_ratio=0.2),
    ])
    assert not fired(results)
    assert results[-1].baseline_only is True


def test_events_resume_on_the_second_frame_after_recovery():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, status=STATUS_STALE, hp_ratio=1.0,
              avail=availability(self=AVAIL_STALE)),
        frame(seq=3, at=140.0, hp_ratio=1.0),
        frame(seq=4, at=141.0, hp_ratio=0.30),
    ])
    assert LOW_HEALTH in fired(results)


def test_live_domain_outage_recovery_does_not_reuse_rapid_damage_history():
    """A live frame can lose one domain without changing source status."""
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, hp_ratio=0.95),
        frame(seq=3, at=102.0, hp_ratio=0.95,
              avail=availability(self=AVAIL_UNKNOWN)),
        frame(seq=4, at=103.0, hp_ratio=0.20),
    ])

    assert RAPID_DAMAGE not in fired(results)
    assert results[-1].baseline_only is True


def test_battle_switch_resets_detector_state():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    first = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, hp_ratio=1.0),
        frame(seq=3, at=102.0, hp_ratio=0.30),
    ])
    assert LOW_HEALTH in fired(first)

    # Same threshold in a new battle must be reportable again.
    second = feed(registry, [
        frame(seq=4, at=200.0, battle_id="b-2", hp_ratio=1.0),
        frame(seq=5, at=201.0, battle_id="b-2", hp_ratio=1.0),
        frame(seq=6, at=202.0, battle_id="b-2", hp_ratio=0.30),
    ])
    assert second[0].identity_reset is True
    assert LOW_HEALTH in fired(second)


def test_service_restart_is_treated_as_a_discontinuity():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, hp_ratio=1.0),
        frame(seq=1, at=102.0, instance_id="inst-b", hp_ratio=0.1),
    ])
    assert results[-1].identity_reset is True
    assert not fired([results[-1]])


# --- lifecycle -----------------------------------------------------------

def test_battle_start_and_end_fire_on_status_transitions():
    registry = DetectorRegistry(build_lifecycle_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=99.0, status=STATUS_WAITING, self_present=False),
        frame(seq=2, at=100.0, status=STATUS_LIVE),
        frame(seq=3, at=200.0, status=STATUS_ENDED, self_present=False,
              avail=availability(self=AVAIL_UNKNOWN)),
    ])
    events = fired(results)
    assert events.count(BATTLE_STARTED) == 1
    assert BATTLE_ENDED in events
    assert POST_BATTLE_SUMMARY in events


def test_lifecycle_still_runs_when_live_data_is_gone():
    """The final frame is inactive by definition; end events must survive it."""
    registry = DetectorRegistry(build_lifecycle_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, status=STATUS_LIVE),
        frame(seq=2, at=200.0, status=STATUS_ENDED, self_present=False,
              avail=availability(self=AVAIL_UNKNOWN, objects=AVAIL_UNKNOWN)),
    ])
    assert BATTLE_ENDED in fired(results)


def test_post_battle_summary_reports_kills_as_unsupported():
    registry = DetectorRegistry(build_lifecycle_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, status=STATUS_LIVE, damage=45000.0),
        frame(seq=2, at=200.0, status=STATUS_ENDED, self_present=False,
              avail=availability(self=AVAIL_UNKNOWN)),
    ])
    summary = [
        event for result in results for event in result.events
        if event.event_id == POST_BATTLE_SUMMARY
    ][0]
    assert summary.detail["outcome"] == "unsupported"
    # Damage is dropped by the final frame, so the peak seen mid-battle is used.
    assert summary.detail["damage_inflicted"] == pytest.approx(45000.0)


# --- survival ------------------------------------------------------------

def test_sinking_needs_two_real_health_readings():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=0.4),
        frame(seq=2, at=101.0, hp_ratio=0.2),
        frame(seq=3, at=102.0, hp_ratio=0.0),
    ])
    assert OWN_SHIP_SUNK in fired(results)


def test_self_going_absent_is_not_a_sinking():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=0.4),
        frame(seq=2, at=101.0, hp_ratio=0.4),
        frame(seq=3, at=102.0, self_present=False,
              avail=availability(self=AVAIL_UNKNOWN)),
    ])
    assert OWN_SHIP_SUNK not in fired(results)


def test_low_health_thresholds_fire_once_each():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=101.0, hp_ratio=1.0),
        frame(seq=3, at=102.0, hp_ratio=0.30),
        frame(seq=4, at=103.0, hp_ratio=0.28),
        frame(seq=5, at=104.0, hp_ratio=0.10),
    ])
    assert fired(results).count(LOW_HEALTH) == 2


def test_rapid_damage_is_never_described_as_focused_fire():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, hp_ratio=1.0),
        frame(seq=2, at=100.5, hp_ratio=1.0),
        frame(seq=3, at=101.0, hp_ratio=0.7),
    ])
    events = [
        event for result in results for event in result.events
        if event.event_id == RAPID_DAMAGE
    ]
    assert events, "a 30% drop inside the window should register"
    assert events[0].detail["phrasing"] == "taking_damage_fast"
    assert events[0].detail["attacker_count"] == "unsupported"


# --- threat --------------------------------------------------------------

def test_enemy_closing_needs_an_actual_approach():
    registry = DetectorRegistry(build_threat_detectors(CFG))
    inside = enemy(x=5000.0)
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=(inside,)),
        frame(seq=2, at=101.0, ships=(inside,)),
    ])
    # Already inside the ring on the baseline frame: nothing new happened.
    assert ENEMY_CLOSING not in fired(results)

    approaching = feed(registry, [
        frame(seq=3, at=102.0, ships=(enemy(x=11000.0),)),
        frame(seq=4, at=103.0, ships=(enemy(x=5000.0),)),
    ])
    assert ENEMY_CLOSING in fired(approaching)


def test_losing_sight_of_everyone_is_not_reported_as_safety():
    registry = DetectorRegistry(build_threat_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=(enemy(x=5000.0),)),
        frame(seq=2, at=101.0, ships=()),
    ])
    assert not fired(results)


def test_multi_direction_threat_never_claims_crossfire():
    registry = DetectorRegistry(build_threat_detectors(CFG))
    spread = (enemy(ui_id=2, x=5000.0, z=0.0), enemy(ui_id=3, x=0.0, z=5000.0))
    results = feed(registry, [
        # One enemy first: the spread condition has to actually become true.
        frame(seq=1, at=100.0, ships=(enemy(ui_id=2, x=5000.0, z=0.0),)),
        frame(seq=2, at=101.0, ships=(enemy(ui_id=2, x=5000.0, z=0.0),)),
        frame(seq=3, at=102.0, ships=spread),
    ])
    events = [
        event for result in results for event in result.events
        if event.event_id == MULTI_DIRECTION_THREAT
    ]
    assert events
    assert events[0].detail["crossfire"] == "unsupported"
    assert events[0].detail["spread_deg"] == 90


# --- geometry ------------------------------------------------------------

def test_boundary_risk_needs_a_heading_towards_the_edge():
    registry = DetectorRegistry(build_geometry_detectors(CFG))
    results = feed(registry, [
        # Mid-map baseline, then near the north edge but pointing south.
        frame(seq=1, at=100.0, z=10000.0, yaw=math.pi),
        frame(seq=2, at=101.0, z=20000.0, yaw=math.pi),
        frame(seq=3, at=102.0, z=20200.0, yaw=math.pi),
    ])
    assert BOUNDARY_RISK not in fired(results)

    # Same spot, now heading out.
    towards = feed(registry, [
        frame(seq=4, at=103.0, z=10000.0, yaw=0.0),
        frame(seq=5, at=104.0, z=20000.0, yaw=0.0),
    ])
    assert BOUNDARY_RISK in fired(towards)


def test_broadside_exposure_requires_a_heading():
    registry = DetectorRegistry(build_geometry_detectors(CFG))
    east = enemy(x=5000.0, z=0.0)
    exposed = feed(registry, [
        # Bow on to an enemy due east, then turning to show the side.
        frame(seq=1, at=100.0, yaw=math.pi / 2, ships=(east,)),
        frame(seq=2, at=101.0, yaw=math.pi / 2, ships=(east,)),
        frame(seq=3, at=102.0, yaw=0.0, ships=(east,)),
    ])
    assert OWN_BROADSIDE_EXPOSED in fired(exposed)

    registry.reset()
    headless = Ship(
        ui_id=2, player_id=3002, team_id=1, relation=2, ship_type="Cruiser",
        name="Zao", alive=True, visible=True, x=5000.0, z=0.0, yaw=None,
        health=30000.0, max_health=40000.0, hp_ratio=0.75,
    )
    # An enemy with no heading cannot produce a target-side-on claim.
    results = feed(registry, [
        frame(seq=3, at=102.0, yaw=math.pi / 2, ships=(headless,)),
        frame(seq=4, at=103.0, yaw=math.pi / 2, ships=(headless,)),
    ])
    assert "target_broadside_window" not in fired(results)


# --- targeting -----------------------------------------------------------

def test_damage_milestone_fires_once_per_step():
    registry = DetectorRegistry(build_targeting_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, damage=10000.0),
        frame(seq=2, at=101.0, damage=10000.0),
        frame(seq=3, at=102.0, damage=60000.0),
        frame(seq=4, at=103.0, damage=70000.0),
        frame(seq=5, at=104.0, damage=120000.0),
    ])
    assert fired(results).count(DAMAGE_MILESTONE) == 2


def test_damage_already_present_on_the_baseline_is_not_replayed():
    registry = DetectorRegistry(build_targeting_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, damage=60000.0),
        frame(seq=2, at=101.0, damage=60000.0),
    ])

    assert DAMAGE_MILESTONE not in fired(results)


def test_ammo_hint_stays_silent_without_ballistics():
    registry = DetectorRegistry(build_targeting_detectors(CFG))
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=(enemy(ship_type="Destroyer"),)),
        frame(seq=2, at=101.0, ships=(enemy(ship_type="Destroyer"),)),
    ])
    assert "ammo_recheck_hint" not in fired(results)


def test_ammo_hint_only_ever_suggests_a_recheck():
    registry = DetectorRegistry(build_targeting_detectors(CFG))
    ballistics = {"available": True, "ammoType": "AP", "penetration": 650}
    results = feed(registry, [
        # AP against a battleship is a sensible pairing; swapping to a destroyer
        # is what makes the hint worth raising.
        frame(seq=1, at=100.0, ballistics=ballistics,
              ships=(enemy(ui_id=2, ship_type="Battleship"),)),
        frame(seq=2, at=101.0, ballistics=ballistics,
              ships=(enemy(ui_id=2, ship_type="Battleship"),)),
        frame(seq=3, at=102.0, ballistics=ballistics,
              ships=(enemy(ui_id=3, ship_type="Destroyer"),)),
    ])
    hints = [
        event for result in results for event in result.events
        if event.event_id == "ammo_recheck_hint"
    ]
    assert hints
    assert hints[0].detail["claim"] == "recheck_only"
    assert hints[0].detail["reload_state"] == "unsupported"


def test_low_hp_target_is_reported_once_per_ship():
    registry = DetectorRegistry(build_targeting_detectors(CFG))
    wounded = enemy(ui_id=5, hp_ratio=0.1)
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=(wounded,)),
        frame(seq=2, at=101.0, ships=(wounded,)),
        frame(seq=3, at=102.0, ships=(wounded,)),
    ])
    assert fired(results).count(LOW_HP_TARGET) == 1


# --- outnumbered / isolation --------------------------------------------

def test_outnumbered_uses_visible_alive_counts():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    ships = (
        ally(ui_id=10),
        enemy(ui_id=2, x=6000.0),
        enemy(ui_id=3, x=6500.0),
        enemy(ui_id=4, x=7000.0),
    )
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=ships),
        frame(seq=2, at=101.0, ships=ships),
    ])
    assert "outnumbered" in fired(results)


def test_isolation_needs_a_known_ally_position():
    registry = DetectorRegistry(build_survival_detectors(CFG))
    # No allies reported at all: unknown, not "alone".
    results = feed(registry, [
        frame(seq=1, at=100.0, ships=(enemy(x=5000.0),)),
        frame(seq=2, at=101.0, ships=(enemy(x=5000.0),)),
    ])
    assert "locally_isolated" not in fired(results)

    registry.reset()
    near = (ally(ui_id=10, x=500.0), enemy(ui_id=2, x=5000.0))
    far = (ally(ui_id=10, x=15000.0), enemy(ui_id=2, x=5000.0))
    isolated = feed(registry, [
        frame(seq=3, at=102.0, ships=near),
        frame(seq=4, at=103.0, ships=near),
        frame(seq=5, at=104.0, ships=far),
    ])
    assert "locally_isolated" in fired(isolated)
