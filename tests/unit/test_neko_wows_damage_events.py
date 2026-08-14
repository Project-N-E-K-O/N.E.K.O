"""Per-victim outgoing-damage bursts from 8111 telemetry."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.adapters.schema_adapter import WowsSchemaAdapter
from plugin.plugins.neko_wows.detectors._base import DetectorRegistry
from plugin.plugins.neko_wows.detectors.damage import (
    DamageBurstDetector,
    build_damage_detectors,
)
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
from plugin.plugins.neko_wows.policy.arbiter import Arbiter
from plugin.plugins.neko_wows.policy.tactic_policy import WowsTacticPolicy


def enemy(
    *,
    player_id: int = 3002,
    alive: bool = True,
    max_health: float | None = 40_000.0,
    health: float | None = None,
    name: str = "Zao",
) -> Ship:
    if health is None:
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
        hp_ratio=(
            max(0.0, min(1.0, health / max_health))
            if health is not None and max_health
            else 0.0
        ),
    )


def frame(
    seq: int,
    at: float,
    damage_by_victim: dict[int, float],
    *ships: Ship,
    battle_id: str = "b-1",
    objects_available: bool = True,
    epoch: int = 1,
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
        epoch=epoch,
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


def test_high_damage_omits_target_sunk_when_objects_are_unavailable():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, objects_available=False),
        frame(2, 101.0, {3002: 20_000}, objects_available=False),
        frame(3, 106.0, {3002: 20_000}, objects_available=False),
    )

    assert "target_sunk" not in emitted(results, HIGH_DAMAGE).detail


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


def test_same_frame_full_hp_one_shot_is_devastating():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(
            2,
            101.0,
            {3002: 40_000},
            enemy(alive=False, max_health=40_000.0),
        ),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    event = emitted(results, DEVASTATING_STRIKE)
    assert event.detail["window_damage"] == 40_000
    assert event.detail["damage_ratio"] == pytest.approx(1.0)
    assert event.detail["target_sunk"] is True


def test_death_flag_before_damage_counter_still_counts_as_devastating():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(2, 101.0, {3002: 0}, enemy(alive=False, max_health=40_000.0)),
        frame(3, 101.5, {3002: 40_000}, enemy(alive=False, max_health=40_000.0)),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    event = emitted(results, DEVASTATING_STRIKE)
    assert event.detail["window_damage"] == 40_000
    assert event.detail["target_sunk"] is True


def test_full_hp_burst_is_devastating_even_if_the_hull_vanishes():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(2, 101.0, {3002: 40_000}),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    event = emitted(results, DEVASTATING_STRIKE)
    assert event.detail["window_damage"] == 40_000
    assert event.detail["damage_ratio"] == pytest.approx(1.0)


def test_zero_health_without_alive_false_still_counts_as_devastating():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(
            2,
            101.0,
            {3002: 40_000},
            enemy(alive=True, max_health=40_000.0, health=0.0),
        ),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    assert emitted(results, DEVASTATING_STRIKE).detail["window_damage"] == 40_000


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
    assert event.detail["victim_id"] == 3002
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


def test_kill_below_half_max_hp_is_not_devastating_even_if_it_finishes_the_hull():
    """The in-game ribbon is damage vs max HP, not vs remaining HP.

    A 12k finishing blow on a 40k hull covers every last hit point and still
    sits at 0.30 of max — below the 0.50 devastating gate, above the 0.25
    high-damage gate.
    """
    results = run_frames(
        frame(
            1,
            100.0,
            {3002: 0},
            enemy(max_health=40_000.0, health=12_000.0),
        ),
        frame(
            2,
            101.0,
            {3002: 12_000},
            enemy(alive=False, max_health=40_000.0),
        ),
    )

    assert event_ids(results) == [HIGH_DAMAGE]
    event = emitted(results, HIGH_DAMAGE)
    assert event.detail["window_damage"] == 12_000
    assert event.detail["target_sunk"] is True
    assert event.detail["damage_ratio"] == pytest.approx(0.3)


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


def test_transport_epoch_change_rebaselines_cumulative_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target, epoch=1),
        frame(2, 101.0, {3002: 20_000}, target, epoch=2),
        frame(3, 106.0, {3002: 20_000}, target, epoch=2),
    )

    assert event_ids(results) == []


def test_received_time_rollback_rebaselines_cumulative_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 99.0, {3002: 20_000}, target),
        frame(3, 104.0, {3002: 20_000}, target),
    )

    assert event_ids(results) == []


def test_frame_gap_longer_than_window_rebaselines_cumulative_damage():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 105.1, {3002: 20_000}, target),
        frame(3, 110.1, {3002: 20_000}, target),
    )

    assert event_ids(results) == []


def test_first_victim_row_in_a_healthy_stream_counts_from_zero():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {}, target),
        frame(2, 101.0, {3002: 50_000}, target),
        frame(3, 106.0, {3002: 50_000}, target),
    )

    assert event_ids(results) == [HIGH_DAMAGE]


def test_victim_table_flicker_keeps_the_pending_burst_without_double_counting():
    target = enemy(max_health=100_000.0)
    results = run_frames(
        frame(1, 100.0, {3002: 0}, target),
        frame(2, 101.0, {3002: 20_000}, target),
        frame(3, 102.0, {}, target),
        frame(4, 103.0, {3002: 20_000}, target),
        frame(5, 108.0, {3002: 20_000}, target),
    )

    assert event_ids(results) == [HIGH_DAMAGE]
    assert emitted(results, HIGH_DAMAGE).detail["window_damage"] == 20_000


def test_victim_row_drop_on_the_kill_frame_still_counts_as_devastating():
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0)),
        frame(2, 101.0, {3002: 40_000}, enemy(max_health=40_000.0)),
        frame(3, 102.0, {}, enemy(alive=False, max_health=40_000.0)),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]
    assert emitted(results, DEVASTATING_STRIKE).detail["window_damage"] == 40_000


def test_multi_tick_half_hp_burst_on_a_living_target_is_not_devastating():
    """Window damage vs last-tick remaining is not a killing blow.

    Two salvos inside 5s on a 40k hull (15k then 10k) yield window=25k
    against remaining=25k — above the 0.50 ribbon gate, but the target
    still has 15k HP.
    """
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0, health=40_000.0)),
        frame(2, 101.0, {3002: 15_000}, enemy(max_health=40_000.0, health=25_000.0)),
        frame(3, 102.0, {3002: 25_000}, enemy(max_health=40_000.0, health=15_000.0)),
        frame(4, 107.0, {3002: 25_000}, enemy(max_health=40_000.0, health=15_000.0)),
    )

    assert DEVASTATING_STRIKE not in event_ids(results)
    assert event_ids(results) == [HIGH_DAMAGE]
    event = emitted(results, HIGH_DAMAGE)
    assert event.detail["window_damage"] == 25_000
    assert event.detail["target_sunk"] is False


def test_victim_row_returning_after_kill_does_not_replay_the_burst():
    """A byVictim flicker after the kill must not count the cumulative total again.

    The burst has to still be open on the kill frame: a same-tick one-shot
    already consumed it before the row disappeared, which skips this path.
    """
    results = run_frames(
        frame(1, 100.0, {3002: 0}, enemy(max_health=40_000.0, health=40_000.0)),
        frame(2, 101.0, {3002: 20_000}, enemy(max_health=40_000.0, health=20_000.0)),
        frame(3, 102.0, {}, enemy(alive=False, max_health=40_000.0)),
        frame(4, 103.0, {3002: 20_000}, enemy(alive=False, max_health=40_000.0)),
        frame(5, 108.0, {3002: 20_000}, enemy(alive=False, max_health=40_000.0)),
    )

    assert event_ids(results) == [DEVASTATING_STRIKE]


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


def wire_payload(*, seq: int, damage: float | None, alive: bool) -> dict:
    inflicted = {
        "9999": {
            "total": 100_000,
            "byVictim": {"3002": 100_000},
        },
    }
    if damage is not None:
        inflicted["2000"] = {
            "total": damage,
            "byVictim": {"3002": damage},
        }
    return {
        "serviceId": "8111_for_wows",
        "apiVersion": "1.0",
        "instanceId": "inst-wire",
        "seq": seq,
        "battleId": "battle-wire",
        "source": {
            "kind": "file",
            "mode": "live",
            "status": "live",
            "updatedAt": float(seq),
        },
        "capabilities": {
            domain: {"supported": True, "version": "1.0"}
            for domain in CORE_DOMAINS
        },
        "availability": {
            **{domain: AVAIL_AVAILABLE for domain in CORE_DOMAINS},
            **{domain: AVAIL_UNSUPPORTED for domain in FUTURE_DOMAINS},
        },
        "extensions": {},
        "schema": 1,
        "active": True,
        "self": {
            "playerId": 2000,
            "teamId": 0,
            "health": 80_000,
            "maxHealth": 80_000,
            "position": [0.0, 0.0, 0.0],
        },
        "objects": [{
            "uiId": 2,
            "playerId": 3002,
            "teamId": 1,
            "relation": 2,
            "type": "Cruiser",
            "name": "Zao",
            "alive": alive,
            "visible": True,
            "x": 200.0,
            "z": 0.0,
            "health": 40_000 if alive else 0,
            "maxHealth": 40_000,
        }],
        "roster": [],
        "damage": {
            "inflicted": inflicted,
            "received": {},
            "teamTotal": {},
        },
        "ballistics": {"available": False},
    }


def test_realistic_nested_8111_payload_reaches_the_arbiter_once():
    cfg = WowsConfig()
    adapter = WowsSchemaAdapter()
    builder = FactBuilder(cfg)
    registry = DetectorRegistry(build_damage_detectors(cfg))
    policy = WowsTacticPolicy(cfg)
    arbiter = Arbiter(cfg)
    previous = None
    chosen = []

    for at, payload in (
        (100.0, wire_payload(seq=1, damage=None, alive=True)),
        (101.0, wire_payload(seq=2, damage=20_000, alive=True)),
        (102.0, wire_payload(seq=3, damage=20_000, alive=False)),
    ):
        snapshot = adapter.parse(payload, transport="ws", received_at=at)
        current = (snapshot, builder.build(snapshot))
        result = registry.feed(previous, current, cfg=cfg)
        previous = current
        decision = arbiter.decide(policy.expand(result.events, current[1]), at)
        if decision.chosen is not None:
            chosen.append(decision.chosen)

    assert [candidate.event_id for candidate in chosen] == [DEVASTATING_STRIKE]
    candidate = chosen[0]
    assert candidate.lane == "urgent"
    assert candidate.detail["target_name"] == "Zao"
    assert candidate.detail["window_damage"] == 20_000
    assert candidate.detail["damage_ratio"] == pytest.approx(0.5)
    assert candidate.detail["classification"] == "telemetry_estimate"
