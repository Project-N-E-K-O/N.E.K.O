"""Per-victim outgoing-damage bursts from 8111 telemetry."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.detectors._base import DetectorRegistry
from plugin.plugins.neko_wows.detectors.damage import DamageBurstDetector
from plugin.plugins.neko_wows.domain.catalog import (
    DEVASTATING_STRIKE,
    HIGH_DAMAGE,
)
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.domain.facts import FactBuilder
from plugin.plugins.neko_wows.domain.snapshot import (
    AVAIL_AVAILABLE,
    AVAIL_UNKNOWN,
    AVAIL_UNSUPPORTED,
    CORE_DOMAINS,
    DOMAIN_OBJECTS,
    FUTURE_DOMAINS,
    STATUS_LIVE,
    SelfShip,
    Ship,
    WowsSnapshot,
)


def enemy(
    *,
    player_id: int = 3002,
    alive: bool = True,
    max_health: float | None = 40_000.0,
    name: str = "Zao",
) -> Ship:
    health = max_health if alive and max_health is not None else 0.0
    return Ship(
        ui_id=player_id,
        player_id=player_id,
        team_id=1,
        relation=2,
        ship_type="Cruiser",
        name=name,
        tier=10,
        alive=alive,
        visible=True,
        x=6000.0,
        z=0.0,
        yaw=0.0,
        health=health,
        max_health=max_health,
        hp_ratio=(1.0 if alive and max_health else 0.0),
    )


def frame(
    seq: int,
    at: float,
    damage_by_victim: dict[int, float],
    *ships: Ship,
    battle_id: str = "b-1",
    objects_available: bool = True,
) -> WowsSnapshot:
    availability = {domain: AVAIL_AVAILABLE for domain in CORE_DOMAINS}
    availability.update({domain: AVAIL_UNSUPPORTED for domain in FUTURE_DOMAINS})
    if not objects_available:
        availability[DOMAIN_OBJECTS] = AVAIL_UNKNOWN
    return WowsSnapshot(
        service_id="8111_for_wows",
        api_version="1.0",
        instance_id="inst-a",
        seq=seq,
        battle_id=battle_id,
        status=STATUS_LIVE,
        capabilities={domain: True for domain in CORE_DOMAINS}
        | {domain: False for domain in FUTURE_DOMAINS},
        availability=availability,
        active=True,
        self_ship=SelfShip(
            player_id=2000,
            team_id=0,
            health=80_000.0,
            max_health=80_000.0,
        ),
        ships=tuple(ships),
        damage_inflicted=sum(damage_by_victim.values()),
        damage_inflicted_by_victim=dict(damage_by_victim),
        received_at=at,
        transport="ws",
        epoch=1,
    )


def run_frames(*snapshots: WowsSnapshot, cfg: WowsConfig | None = None):
    effective = cfg or WowsConfig()
    registry = DetectorRegistry((DamageBurstDetector(effective),))
    builder = FactBuilder(effective)
    previous = None
    results = []
    for snapshot in snapshots:
        current = (snapshot, builder.build(snapshot))
        results.append(registry.feed(previous, current, cfg=effective))
        previous = current
    return results


def emitted(results, event_id: str | None = None):
    events = [event for result in results for event in result.events]
    if event_id is None:
        return events
    return next(event for event in events if event.event_id == event_id)


def event_ids(results) -> list[str]:
    return [event.event_id for event in emitted(results)]


def test_absolute_high_damage_waits_until_five_seconds_after_last_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 20_000}, target),
        frame(3, 105.9, {3002: 20_000}, target),
        frame(4, 106.0, {3002: 20_000}, target),
    )

    assert event_ids(results[:-1]) == []
    assert event_ids(results[-1:]) == [HIGH_DAMAGE]
    event = emitted(results, HIGH_DAMAGE)
    assert event.detail["window_damage"] == 20_000
    assert event.detail["window_seconds"] == pytest.approx(5.0)
    assert event.detail["target_sunk"] is False


def test_ratio_threshold_is_or_with_absolute_threshold():
    target = enemy(max_health=40_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 10_000}, target),
        frame(3, 106.0, {3002: 10_000}, target),
    )

    assert event_ids(results) == [HIGH_DAMAGE]
    assert emitted(results, HIGH_DAMAGE).detail["damage_ratio"] == pytest.approx(0.25)


def test_damage_below_both_thresholds_stays_silent():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 19_999}, target),
        frame(3, 106.0, {3002: 19_999}, target),
    )

    assert event_ids(results) == []


def test_sink_upgrades_the_same_burst_to_devastating_strike_only():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(2, 101.0, {3002: 20_000}, enemy(max_health=40_000.0)),
        frame(
            3,
            102.0,
            {3002: 20_000},
            enemy(alive=False, max_health=40_000.0),
        ),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    event = emitted(results, DEVASTATING_STRIKE)
    assert event.detail["window_damage"] == 20_000
    assert event.detail["damage_ratio"] == pytest.approx(0.5)
    assert event.detail["target_sunk"] is True
    assert event.detail["classification"] == "telemetry_estimate"


def test_sunk_below_half_can_emit_high_damage_but_not_devastating():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=100_000.0)),
        frame(2, 101.0, {3002: 20_000}, enemy(max_health=100_000.0)),
        frame(
            3,
            102.0,
            {3002: 20_000},
            enemy(alive=False, max_health=100_000.0),
        ),
    )

    assert event_ids(results) == [HIGH_DAMAGE]
    assert emitted(results, HIGH_DAMAGE).detail["target_sunk"] is True


def test_unknown_max_health_allows_absolute_high_but_not_devastating():
    target = enemy(max_health=None)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 20_000}, target),
        frame(3, 102.0, {3002: 20_000}, enemy(alive=False, max_health=None)),
        frame(4, 106.0, {3002: 20_000}, enemy(alive=False, max_health=None)),
    )

    assert event_ids(results) == [HIGH_DAMAGE]


def test_counter_rollback_discards_the_pending_burst():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 20_000}, target),
        frame(3, 102.0, {3002: 5_000}, target),
        frame(4, 107.0, {3002: 5_000}, target),
    )

    assert event_ids(results) == []


def test_new_victim_row_is_a_baseline_not_replayed_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {}, target),
        frame(2, 101.0, {3002: 50_000}, target),
        frame(3, 106.0, {3002: 50_000}, target),
    )

    assert event_ids(results) == []


def test_victim_table_disappearance_discards_the_pending_burst():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 20_000}, target),
        frame(3, 102.0, {}, target),
        frame(4, 103.0, {3002: 20_000}, target),
        frame(5, 108.0, {3002: 20_000}, target),
    )

    assert event_ids(results) == []


def test_object_domain_gap_blocks_retroactive_devastating_strike():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(2, 101.0, {3002: 20_000}, enemy(max_health=40_000.0)),
        frame(3, 101.5, {3002: 20_000}, objects_available=False),
        frame(
            4,
            102.0,
            {3002: 20_000},
            enemy(alive=False, max_health=40_000.0),
        ),
        frame(
            5,
            106.0,
            {3002: 20_000},
            enemy(alive=False, max_health=40_000.0),
        ),
    )

    assert DEVASTATING_STRIKE not in event_ids(results)
    assert event_ids(results) == [HIGH_DAMAGE]


def test_battle_switch_does_not_replay_existing_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target, battle_id="b-1"),
        frame(2, 101.0, {3002: 20_000}, target, battle_id="b-1"),
        frame(3, 102.0, {3002: 20_000}, target, battle_id="b-2"),
        frame(4, 107.0, {3002: 20_000}, target, battle_id="b-2"),
    )

    assert event_ids(results) == []


def test_multiple_resolved_victims_choose_devastating_and_consume_the_rest():
    alive_high = enemy(player_id=3002, max_health=100_000.0, name="High")
    alive_dev = enemy(player_id=3003, max_health=40_000.0, name="Dev")
    sunk_high = enemy(
        player_id=3002,
        alive=False,
        max_health=100_000.0,
        name="High",
    )
    sunk_dev = enemy(
        player_id=3003,
        alive=False,
        max_health=40_000.0,
        name="Dev",
    )
    results = run_frames(
        frame(1, 100.0, {3002: 0, 3003: 0}, alive_high, alive_dev),
        frame(
            2,
            101.0,
            {3002: 20_000, 3003: 20_000},
            alive_high,
            alive_dev,
        ),
        frame(
            3,
            102.0,
            {3002: 20_000, 3003: 20_000},
            sunk_high,
            sunk_dev,
        ),
        frame(
            4,
            107.0,
            {3002: 20_000, 3003: 20_000},
            sunk_high,
            sunk_dev,
        ),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    assert emitted(results, DEVASTATING_STRIKE).detail["target_name"] == "Dev"
