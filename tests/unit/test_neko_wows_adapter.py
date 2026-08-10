"""Schema adaptation and frame ordering for the World of Warships companion."""

from __future__ import annotations

import asyncio
import threading

import pytest

from plugin.plugins.neko_wows.adapters.schema_adapter import (
    BW_TO_METERS,
    LEGACY_STALE_SECONDS,
    UnexpectedServiceIdentity,
    UnsupportedApiVersion,
    WowsSchemaAdapter,
)
from plugin.plugins.neko_wows.domain.facts import FactBuilder
from plugin.plugins.neko_wows.adapters.transport import (
    DROP_DUPLICATE_SEQ,
    DROP_MALFORMED,
    DROP_STALE_EPOCH,
    STALL_FAILURES,
    STALL_INTERVAL_SECONDS,
    CursorGate,
    TelemetryTransport,
)
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.domain.snapshot import (
    AVAIL_AVAILABLE,
    AVAIL_STALE,
    AVAIL_UNKNOWN,
    AVAIL_UNSUPPORTED,
    DOMAIN_BALLISTICS,
    DOMAIN_DAMAGE,
    DOMAIN_MAP_BOUNDS,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
    STATUS_ENDED,
    STATUS_LIVE,
    STATUS_STALE,
    STATUS_WAITING,
)


# --- payload builders ----------------------------------------------------

def flat_body(**overrides):
    """The pre-envelope `schema: 1` body, as the legacy service emits it."""
    body = {
        "schema": 1,
        "active": True,
        "ts": 12.5,
        "battleType": "RandomBattle",
        "gameMode": "Domination",
        "map": {"name": "New Dawn", "id": "13_OC_new_dawn"},
        # Wire shape matches 8111_for_wows: BigWorld units (×30 → metres).
        "bounds": [-700.0, 700.0, -700.0, 700.0],
        "boundsSource": "table",
        "self": {
            "playerId": 2000, "teamId": 0, "health": 40000.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
            "position": [100.0, 0.0, -200.0],
        },
        "objects": [
            {
                "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
                "type": "Battleship", "name": "Yamato", "playerName": "Master",
                "tier": 10, "alive": True, "visible": True,
                "x": 100.0, "z": -200.0, "yaw": 0.0,
                "health": 40000.0, "maxHealth": 80000.0, "hpRatio": 0.5,
            },
            {
                "uiId": 2, "playerId": 3000, "teamId": 1, "relation": 2,
                "type": "Destroyer", "name": "Shimakaze", "playerName": "Foe",
                "tier": 10, "alive": True, "visible": True,
                "x": 1100.0, "z": -200.0, "yaw": 1.57,
                "health": 10000.0, "maxHealth": 20000.0, "hpRatio": 0.5,
            },
        ],
        "roster": [
            {"playerId": 2000, "teamId": 0, "relation": 0, "name": "Master",
             "shipName": "Yamato", "shipType": "Battleship", "shipTier": 10},
        ],
        "damage": {"inflicted": {"2000": 15000.0}, "received": {}, "teamTotal": {}},
        "ballistics": {"available": True, "ammoType": "AP", "penetration": 650},
        "diag": {},
    }
    body.update(overrides)
    return body


def v1_payload(*, seq=1, instance_id="inst-a", battle_id="b-1",
               status=STATUS_LIVE, availability=None, **overrides):
    payload = flat_body(**overrides)
    payload.update({
        "serviceId": "8111_for_wows",
        "apiVersion": "1.0",
        "instanceId": instance_id,
        "seq": seq,
        "battleId": battle_id,
        "source": {"kind": "mod-file-bridge", "mode": "live",
                   "status": status, "updatedAt": 1700.0},
        "capabilities": {
            name: {"supported": True, "version": "1.0"}
            for name in ("self", "objects", "roster", "damage",
                         "ballistics", "mapBounds")
        },
        "availability": availability if availability is not None else {
            "self": AVAIL_AVAILABLE,
            "objects": AVAIL_AVAILABLE,
            "roster": AVAIL_AVAILABLE,
            "damage": AVAIL_AVAILABLE,
            "ballistics": AVAIL_AVAILABLE,
            "mapBounds": AVAIL_AVAILABLE,
            "kills": AVAIL_UNSUPPORTED,
            "capturePoints": AVAIL_UNSUPPORTED,
            "torpedoes": AVAIL_UNSUPPORTED,
            "consumables": AVAIL_UNSUPPORTED,
        },
        "extensions": {},
    })
    return payload


# --- v1 parsing ----------------------------------------------------------

def test_v1_envelope_is_read_verbatim():
    snapshot = WowsSchemaAdapter().parse(v1_payload(seq=7))
    assert snapshot.legacy is False
    assert snapshot.instance_id == "inst-a"
    assert snapshot.seq == 7
    assert snapshot.battle_id == "b-1"
    assert snapshot.status == STATUS_LIVE
    assert snapshot.cursor == ("inst-a", 7)


def test_published_8111_for_wows_snapshot_identity_is_accepted():
    payload = v1_payload()
    payload["serviceId"] = "8111_for_wows"

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.service_id == "8111_for_wows"


def test_v1_game_version_is_preserved_when_service_supplies_it():
    payload = v1_payload() | {"gameVersion": "15.6.0.0.12830008"}

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.game_version == "15.6.0.0.12830008"


def test_v1_game_version_rejects_non_string_values():
    snapshot = WowsSchemaAdapter().parse(v1_payload() | {"gameVersion": 156})

    assert snapshot.game_version == ""


def test_v1_body_is_normalized():
    snapshot = WowsSchemaAdapter().parse(v1_payload())
    assert snapshot.self_ship is not None
    assert snapshot.self_ship.hp_ratio == pytest.approx(0.5)
    assert snapshot.map_name == "New Dawn"
    assert snapshot.bounds == (-21000.0, 21000.0, -21000.0, 21000.0)
    assert snapshot.damage_inflicted == pytest.approx(15000.0)
    assert len(snapshot.ships) == 2
    assert snapshot.enemies()[0].name == "Shimakaze"


def test_current_8111_damage_selects_only_the_local_attacker():
    payload = v1_payload(damage={
        "inflicted": {
            "2000": {
                "total": 25_000,
                "byVictim": {"3000": 21_000, "3001": 4_000},
            },
            "9999": {
                "total": 80_000,
                "byVictim": {"3000": 80_000},
            },
        },
        "received": {},
        "teamTotal": {"0": 105_000},
    })

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.damage_inflicted == pytest.approx(25_000)
    assert snapshot.damage_inflicted_by_victim == {
        3000: 21_000.0,
        3001: 4_000.0,
    }
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.damage_inflicted_by_victim == {
        3000: 21_000.0,
        3001: 4_000.0,
    }


def test_flat_inflicted_table_keeps_only_the_local_scalar():
    """`{playerId: amount}` must not fold teammates into our total."""
    payload = v1_payload(damage={
        "inflicted": {
            "2000": 15_000.0,
            "9999": 80_000.0,
        },
        "received": {},
        "teamTotal": {},
    })

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.damage_inflicted == pytest.approx(15_000.0)
    assert snapshot.damage_inflicted_by_victim == {}


def test_nested_damage_without_local_identity_is_not_guessed():
    payload = v1_payload(
        self=None,
        damage={
            "inflicted": {
                "9999": {
                    "total": 80_000,
                    "byVictim": {"3000": 80_000},
                },
            },
            "received": {},
            "teamTotal": {},
        },
    )

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.damage_inflicted is None
    assert snapshot.damage_inflicted_by_victim == {}


def test_nested_damage_rejects_bad_victim_values():
    payload = v1_payload(damage={
        "inflicted": {
            "2000": {
                "total": 22_000,
                "byVictim": {
                    "3000": 20_000,
                    "bad": 1,
                    "3001": -2,
                    "3002": float("inf"),
                },
            },
        },
        "received": {},
        "teamTotal": {},
    })

    snapshot = WowsSchemaAdapter().parse(payload)

    assert snapshot.damage_inflicted_by_victim == {3000: 20_000.0}


def test_adapter_converts_bigworld_wire_coords_to_meters():
    """8111_for_wows emits BigWorld units (1 BW = 30 m); facts must be metres.

    A raw 192 BW gap used to be labelled ``distance_m: 192`` and spoken as
    "192 metres". After conversion it is 5.76 km.
    """
    own = {
        "playerId": 2000, "teamId": 0, "health": 40000.0,
        "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
        "position": [0.0, 0.0, 0.0],
    }
    enemy = {
        "uiId": 2, "playerId": 3000, "teamId": 1, "relation": 2,
        "type": "Battleship", "name": "Yamato", "playerName": "Foe",
        "tier": 10, "alive": True, "visible": True,
        "x": 192.0, "z": 0.0, "yaw": 1.57,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    payload = v1_payload(
        bounds=[-800.0, 800.0, -800.0, 800.0],
        self=own,
        objects=[
            {
                "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
                "type": "Battleship", "name": "OwnShip", "playerName": "Master",
                "tier": 10, "alive": True, "visible": True,
                "x": 0.0, "z": 0.0, "yaw": 0.0,
                "health": 40000.0, "maxHealth": 80000.0, "hpRatio": 0.5,
            },
            enemy,
        ],
    )

    snapshot = WowsSchemaAdapter().parse(payload)
    assert snapshot.bounds == (
        -800.0 * BW_TO_METERS,
        800.0 * BW_TO_METERS,
        -800.0 * BW_TO_METERS,
        800.0 * BW_TO_METERS,
    )
    assert snapshot.self_ship.x == pytest.approx(0.0)
    assert snapshot.self_ship.z == pytest.approx(0.0)
    assert snapshot.enemies()[0].x == pytest.approx(192.0 * BW_TO_METERS)

    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.nearest_enemy is not None
    assert facts.nearest_enemy.distance_m == pytest.approx(192.0 * BW_TO_METERS)
    assert facts.distance_to_boundary_m == pytest.approx(800.0 * BW_TO_METERS)


def test_not_confirmed_sunk_enemies_keep_last_known_ships_after_they_go_dark():
    """Dark last-known ships still count toward not-confirmed-sunk, but not visible."""
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 40000.0, "maxHealth": 80000.0, "hpRatio": 0.5,
    }
    spotted = {
        "uiId": 2, "playerId": 3001, "teamId": 1, "relation": 2,
        "type": "Destroyer", "name": "Shimakaze", "playerName": "FoeA",
        "tier": 10, "alive": True, "visible": True,
        "x": 100.0, "z": 0.0, "yaw": 1.57,
        "health": 10000.0, "maxHealth": 20000.0, "hpRatio": 0.5,
    }
    dark = {
        "uiId": 3, "playerId": 3002, "teamId": 1, "relation": 2,
        "type": "Cruiser", "name": "Zao", "playerName": "FoeB",
        "tier": 10, "alive": True, "visible": False,
        "x": 200.0, "z": 50.0, "yaw": 0.0, "staleSeconds": 12.0,
        "health": 30000.0, "maxHealth": 40000.0, "hpRatio": 0.75,
    }
    snapshot = WowsSchemaAdapter().parse(v1_payload(
        self={
            "playerId": 2000, "teamId": 0, "health": 40000.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
            "position": [0.0, 0.0, 0.0],
        },
        objects=[own, spotted, dark],
    ))
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.enemies_not_confirmed_sunk == 2
    assert facts.confirmed_visible_enemies == 1
    assert facts.team_counts_confirmed is True
    assert facts.visible_enemies == 1


def test_threat_geometry_uses_visible_enemies_not_dark_last_known():
    """Alive counts may include dark ships; threat geometry uses only lit ones."""
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 40000.0, "maxHealth": 80000.0, "hpRatio": 0.5,
    }
    # Lit but farther.
    spotted = {
        "uiId": 2, "playerId": 3001, "teamId": 1, "relation": 2,
        "type": "Destroyer", "name": "Shimakaze", "playerName": "FoeA",
        "tier": 10, "alive": True, "visible": True,
        "x": 200.0, "z": 0.0, "yaw": 1.57,
        "health": 10000.0, "maxHealth": 20000.0, "hpRatio": 0.5,
    }
    # Dark last-known, much closer — must not become nearest_enemy.
    dark = {
        "uiId": 3, "playerId": 3002, "teamId": 1, "relation": 2,
        "type": "Cruiser", "name": "Zao", "playerName": "FoeB",
        "tier": 10, "alive": True, "visible": False,
        "x": 20.0, "z": 0.0, "yaw": 0.0, "staleSeconds": 12.0,
        "health": 30000.0, "maxHealth": 40000.0, "hpRatio": 0.75,
    }
    snapshot = WowsSchemaAdapter().parse(v1_payload(
        self={
            "playerId": 2000, "teamId": 0, "health": 40000.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
            "position": [0.0, 0.0, 0.0],
        },
        objects=[own, spotted, dark],
    ))
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.enemies_not_confirmed_sunk == 2
    assert facts.visible_enemies == 1
    assert facts.nearest_enemy is not None
    assert facts.nearest_enemy.ship.player_id == 3001
    assert len(facts.threats_in_scan_range) == 1


def test_roster_stub_does_not_resurrect_ship_after_corpse_leaves_objects():
    """After a corpse leaves objects, a roster stub must not revive that enemy."""
    adapter = WowsSchemaAdapter()
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    dead = {
        "uiId": 2, "playerId": 3001, "teamId": 1, "relation": 2,
        "type": "Destroyer", "name": "Shimakaze", "playerName": "FoeA",
        "tier": 10, "alive": False, "visible": False,
        "x": 100.0, "z": 0.0, "yaw": 1.57,
        "health": 0.0, "maxHealth": 20000.0, "hpRatio": 0.0,
    }
    roster = [
        {"playerId": 2000, "teamId": 0, "relation": 0, "name": "Master",
         "shipName": "OwnShip", "shipType": "Battleship", "shipTier": 10},
        {"playerId": 3001, "teamId": 1, "relation": 2, "name": "FoeA",
         "shipName": "Shimakaze", "shipType": "Destroyer", "shipTier": 10},
        {"playerId": 3002, "teamId": 1, "relation": 2, "name": "FoeB",
         "shipName": "Zao", "shipType": "Cruiser", "shipTier": 10},
    ]
    self_body = {
        "playerId": 2000, "teamId": 0, "health": 80000.0,
        "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
        "position": [0.0, 0.0, 0.0],
    }
    seen_dead = adapter.parse(v1_payload(
        self=self_body, objects=[own, dead], roster=roster))
    assert FactBuilder(WowsConfig()).build(seen_dead).enemies_not_confirmed_sunk == 1

    corpse_gone = adapter.parse(v1_payload(
        seq=2, self=self_body, objects=[own], roster=roster))
    facts = FactBuilder(WowsConfig()).build(corpse_gone)
    assert facts.enemies_not_confirmed_sunk == 1
    assert {s.player_id for s in corpse_gone.enemies(visible_only=False)} == {3002}


def test_roster_only_counts_are_not_presented_as_confirmed_alive():
    """Roster can cap not-confirmed-sunk counts but does not prove ships are alive."""
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    snapshot = WowsSchemaAdapter().parse(v1_payload(
        self={
            "playerId": 2000, "teamId": 0, "health": 80000.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
            "position": [0.0, 0.0, 0.0],
        },
        objects=[own],
        roster=[
            {"playerId": 2000, "teamId": 0, "relation": 0, "name": "Master",
             "shipName": "OwnShip", "shipType": "Battleship", "shipTier": 10},
            {"playerId": 3001, "teamId": 1, "relation": 2, "name": "FoeA",
             "shipName": "Shimakaze", "shipType": "Destroyer", "shipTier": 10},
            {"playerId": 3002, "teamId": 1, "relation": 2, "name": "FoeB",
             "shipName": "Zao", "shipType": "Cruiser", "shipTier": 10},
        ],
    ))
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.enemies_not_confirmed_sunk == 2
    assert facts.confirmed_visible_enemies == 0
    assert facts.team_counts_confirmed is True
    assert facts.visible_enemies == 0


def test_full_roster_with_explicit_alive_flags_confirms_visible_team_counts():
    payload = v1_payload(roster=[
        {"playerId": 2000, "teamId": 0, "relation": 0, "name": "Master",
         "shipName": "Yamato", "shipType": "Battleship", "shipTier": 10},
        {"playerId": 3000, "teamId": 1, "relation": 2, "name": "Foe",
         "shipName": "Shimakaze", "shipType": "Destroyer", "shipTier": 10},
    ])

    facts = FactBuilder(WowsConfig()).build(WowsSchemaAdapter().parse(payload))

    assert facts.allies_not_confirmed_sunk == 1
    assert facts.enemies_not_confirmed_sunk == 1
    assert facts.confirmed_visible_allies == 1
    assert facts.confirmed_visible_enemies == 1
    assert facts.team_counts_confirmed is True


def test_spoken_ship_name_humanizes_wire_indexes():
    from plugin.plugins.neko_wows.domain.snapshot import Ship, spoken_ship_name

    indexed = Ship(
        name="PJSB018_Yamato_1944", ship_type="Battleship", tier=10,
        player_name="Foe",
    )
    friendly = Ship(name="Zao", ship_type="Cruiser", tier=10)
    class_only = Ship(
        name="PRSB999_UnknownHull_1999", ship_type="Battleship", tier=9,
    )

    assert spoken_ship_name(indexed) == "Yamato"
    assert spoken_ship_name(friendly) == "Zao"
    assert spoken_ship_name(Ship(name="PRSB999_UnknownHull_1999")) == "UnknownHull"
    assert spoken_ship_name(Ship(ship_type="Cruiser", tier=8)) == "Cruiser 8级"
    assert class_only.spoken_name == "UnknownHull"


def test_dead_object_enemy_is_not_revived_by_roster_entry():
    """An enemy marked dead in objects must stay dead even if still on the roster."""
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    dead = {
        "uiId": 2, "playerId": 3001, "teamId": 1, "relation": 2,
        "type": "Destroyer", "name": "Shimakaze", "playerName": "FoeA",
        "tier": 10, "alive": False, "visible": False,
        "x": 100.0, "z": 0.0, "yaw": 1.57,
        "health": 0.0, "maxHealth": 20000.0, "hpRatio": 0.0,
    }
    snapshot = WowsSchemaAdapter().parse(v1_payload(
        self={
            "playerId": 2000, "teamId": 0, "health": 80000.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
            "position": [0.0, 0.0, 0.0],
        },
        objects=[own, dead],
        roster=[
            {"playerId": 2000, "teamId": 0, "relation": 0, "name": "Master",
             "shipName": "OwnShip", "shipType": "Battleship", "shipTier": 10},
            {"playerId": 3001, "teamId": 1, "relation": 2, "name": "FoeA",
             "shipName": "Shimakaze", "shipType": "Destroyer", "shipTier": 10},
        ],
    ))
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert facts.enemies_not_confirmed_sunk == 0
    assert facts.visible_enemies == 0


def test_roster_counts_survive_when_objects_domain_is_stale():
    """Live meta keeps roster available after objects go stale; counts must remain."""
    roster = [
        {"playerId": 2000, "teamId": 1, "relation": 1, "name": "Master",
         "shipName": "OwnShip", "shipType": "Battleship", "shipTier": 10},
        {"playerId": 2101, "teamId": 1, "relation": 1, "name": "AllyA",
         "shipName": "AllyCA", "shipType": "Cruiser", "shipTier": 10},
        {"playerId": 3001, "teamId": 0, "relation": 2, "name": "FoeA",
         "shipName": "Shimakaze", "shipType": "Destroyer", "shipTier": 10},
        {"playerId": 3002, "teamId": 0, "relation": 2, "name": "FoeB",
         "shipName": "Zao", "shipType": "Cruiser", "shipTier": 10},
    ]
    snapshot = WowsSchemaAdapter().parse(v1_payload(
        self={
            "playerId": 2000, "teamId": 1, "health": 0.0,
            "maxHealth": 80000.0, "yaw": 0.0, "speed": 0.0,
            "position": [0.0, 0.0, 0.0],
        },
        objects=[],
        roster=roster,
        availability={
            "self": AVAIL_STALE,
            "objects": AVAIL_STALE,
            "roster": AVAIL_AVAILABLE,
            "damage": AVAIL_STALE,
            "ballistics": AVAIL_STALE,
            "mapBounds": AVAIL_AVAILABLE,
            "kills": AVAIL_UNSUPPORTED,
            "capturePoints": AVAIL_UNSUPPORTED,
            "torpedoes": AVAIL_UNSUPPORTED,
            "consumables": AVAIL_UNSUPPORTED,
        },
    ))
    facts = FactBuilder(WowsConfig()).build(snapshot)
    assert snapshot.is_available(DOMAIN_OBJECTS) is False
    assert snapshot.is_available(DOMAIN_ROSTER) is True
    assert facts.allies_not_confirmed_sunk == 2
    assert facts.enemies_not_confirmed_sunk == 2
    assert facts.visible_enemies is None


def test_distant_ally_flicker_does_not_drop_alive_count_without_roster():
    """A distant ally briefly dropping from objects must not be treated as sunk."""
    adapter = WowsSchemaAdapter()
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    ally = {
        "uiId": 2, "playerId": 2101, "teamId": 0, "relation": 1,
        "type": "Cruiser", "name": "AllyCA", "playerName": "AllyA",
        "tier": 10, "alive": True, "visible": True,
        "x": 500.0, "z": 0.0, "yaw": 0.0,
        "health": 30000.0, "maxHealth": 40000.0, "hpRatio": 0.75,
    }
    self_body = {
        "playerId": 2000, "teamId": 0, "health": 80000.0,
        "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
        "position": [0.0, 0.0, 0.0],
    }
    seen = adapter.parse(v1_payload(
        self=self_body, objects=[own, ally], roster=[]))
    assert FactBuilder(WowsConfig()).build(seen).allies_not_confirmed_sunk == 2

    flickered = adapter.parse(v1_payload(
        seq=2, self=self_body, objects=[own], roster=[]))
    facts = FactBuilder(WowsConfig()).build(flickered)
    assert facts.allies_not_confirmed_sunk == 2
    assert {s.player_id for s in flickered.own_side(visible_only=False)} == {2000, 2101}
    assert {s.player_id for s in flickered.allies(visible_only=True)} == set()


def test_lost_spot_enemy_flicker_keeps_alive_but_not_visible():
    """A lost-spot enemy leaving objects stays alive but drops to zero visible."""
    adapter = WowsSchemaAdapter()
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    enemy = {
        "uiId": 2, "playerId": 3001, "teamId": 1, "relation": 2,
        "type": "Destroyer", "name": "Shimakaze", "playerName": "FoeA",
        "tier": 10, "alive": True, "visible": True,
        "x": 100.0, "z": 0.0, "yaw": 1.57,
        "health": 10000.0, "maxHealth": 20000.0, "hpRatio": 0.5,
    }
    self_body = {
        "playerId": 2000, "teamId": 0, "health": 80000.0,
        "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
        "position": [0.0, 0.0, 0.0],
    }
    seen = adapter.parse(v1_payload(
        self=self_body, objects=[own, enemy], roster=[]))
    assert FactBuilder(WowsConfig()).build(seen).enemies_not_confirmed_sunk == 1

    lost = adapter.parse(v1_payload(
        seq=2, self=self_body, objects=[own], roster=[]))
    facts = FactBuilder(WowsConfig()).build(lost)
    assert facts.enemies_not_confirmed_sunk == 1
    assert facts.visible_enemies == 0
    assert facts.nearest_enemy is None


def test_sticky_memory_does_not_block_death_without_roster():
    """Without roster: sticky memory, then death, then corpse leave — alive drops."""
    adapter = WowsSchemaAdapter()
    own = {
        "uiId": 1, "playerId": 2000, "teamId": 0, "relation": 0,
        "type": "Battleship", "name": "OwnShip", "playerName": "Master",
        "tier": 10, "alive": True, "visible": True,
        "x": 0.0, "z": 0.0, "yaw": 0.0,
        "health": 80000.0, "maxHealth": 80000.0, "hpRatio": 1.0,
    }
    ally = {
        "uiId": 2, "playerId": 2101, "teamId": 0, "relation": 1,
        "type": "Cruiser", "name": "AllyCA", "playerName": "AllyA",
        "tier": 10, "alive": True, "visible": True,
        "x": 500.0, "z": 0.0, "yaw": 0.0,
        "health": 30000.0, "maxHealth": 40000.0, "hpRatio": 0.75,
    }
    self_body = {
        "playerId": 2000, "teamId": 0, "health": 80000.0,
        "maxHealth": 80000.0, "yaw": 0.0, "speed": 25.0,
        "position": [0.0, 0.0, 0.0],
    }
    builder = FactBuilder(WowsConfig())

    seen = adapter.parse(v1_payload(
        self=self_body, objects=[own, ally], roster=[]))
    assert builder.build(seen).allies_not_confirmed_sunk == 2

    sticky = adapter.parse(v1_payload(
        seq=2, self=self_body, objects=[own], roster=[]))
    assert builder.build(sticky).allies_not_confirmed_sunk == 2

    dead = {**ally, "alive": False, "visible": False, "health": 0.0, "hpRatio": 0.0}
    seen_dead = adapter.parse(v1_payload(
        seq=3, self=self_body, objects=[own, dead], roster=[]))
    assert builder.build(seen_dead).allies_not_confirmed_sunk == 1

    corpse_gone = adapter.parse(v1_payload(
        seq=4, self=self_body, objects=[own], roster=[]))
    facts = builder.build(corpse_gone)
    assert facts.allies_not_confirmed_sunk == 1
    assert {s.player_id for s in corpse_gone.own_side(visible_only=False)} == {2000}

    # Missing alive on a known corpse must not re-seed sticky memory.
    null_alive_corpse = {**dead}
    null_alive_corpse.pop("alive")
    flickered_corpse = adapter.parse(v1_payload(
        seq=5, self=self_body, objects=[own, null_alive_corpse], roster=[]))
    assert builder.build(flickered_corpse).allies_not_confirmed_sunk == 1
    assert 2101 not in adapter._known_ships

    still_gone = adapter.parse(v1_payload(
        seq=6, self=self_body, objects=[own], roster=[]))
    assert builder.build(still_gone).allies_not_confirmed_sunk == 1


def test_unknown_major_version_is_refused():
    adapter = WowsSchemaAdapter()
    with pytest.raises(UnsupportedApiVersion):
        adapter.parse(v1_payload() | {"apiVersion": "2.0"})


@pytest.mark.parametrize("service_id", ["", "8111-for-war-thunder", "other"])
def test_v1_foreign_or_missing_service_identity_is_refused(service_id):
    adapter = WowsSchemaAdapter()
    with pytest.raises(UnexpectedServiceIdentity):
        adapter.parse(v1_payload() | {"serviceId": service_id})


def test_unsupported_domains_stay_unsupported():
    snapshot = WowsSchemaAdapter().parse(v1_payload())
    for domain in ("kills", "capturePoints", "torpedoes", "consumables"):
        assert snapshot.availability_of(domain) == AVAIL_UNSUPPORTED
        assert snapshot.is_available(domain) is False


def test_stale_domain_is_not_treated_as_available():
    payload = v1_payload(availability={"self": AVAIL_STALE})
    snapshot = WowsSchemaAdapter().parse(payload)
    assert snapshot.availability_of(DOMAIN_SELF) == AVAIL_STALE
    assert snapshot.is_available(DOMAIN_SELF) is False
    assert snapshot.missing_domains((DOMAIN_SELF,)) == (DOMAIN_SELF,)


def test_missing_availability_entry_defaults_to_unknown():
    snapshot = WowsSchemaAdapter().parse(v1_payload(availability={}))
    assert snapshot.availability_of(DOMAIN_OBJECTS) == AVAIL_UNKNOWN


# --- legacy parsing ------------------------------------------------------

def test_legacy_payload_gets_a_derived_envelope():
    adapter = WowsSchemaAdapter()
    snapshot = adapter.parse(flat_body(), received_at=100.0)
    assert snapshot.legacy is True
    assert snapshot.api_version == ""
    assert snapshot.instance_id.startswith("legacy-")
    assert snapshot.seq == 1
    assert snapshot.battle_id is not None
    assert snapshot.status == STATUS_LIVE


def test_legacy_availability_is_inferred_from_present_fields():
    snapshot = WowsSchemaAdapter().parse(flat_body(), received_at=100.0)
    for domain in (DOMAIN_SELF, DOMAIN_OBJECTS, DOMAIN_ROSTER,
                   DOMAIN_DAMAGE, DOMAIN_BALLISTICS, DOMAIN_MAP_BOUNDS):
        assert snapshot.is_available(domain), domain


def test_legacy_absent_domain_is_unknown_not_false():
    snapshot = WowsSchemaAdapter().parse(
        flat_body(self=None, objects=[], ballistics={"available": False}),
        received_at=100.0)
    assert snapshot.availability_of(DOMAIN_SELF) == AVAIL_UNKNOWN
    assert snapshot.availability_of(DOMAIN_OBJECTS) == AVAIL_UNKNOWN
    assert snapshot.availability_of(DOMAIN_BALLISTICS) == AVAIL_UNKNOWN


def test_legacy_seq_only_advances_when_content_changes():
    adapter = WowsSchemaAdapter()
    first = adapter.parse(flat_body(), received_at=100.0)
    repeat = adapter.parse(flat_body(), received_at=100.1)
    moved = adapter.parse(flat_body(ts=13.0), received_at=100.2)
    assert repeat.seq == first.seq
    assert moved.seq == first.seq + 1


def test_legacy_battle_id_is_stable_then_changes_between_battles():
    adapter = WowsSchemaAdapter()
    first = adapter.parse(flat_body(), received_at=100.0)
    same = adapter.parse(flat_body(ts=13.0), received_at=100.5)
    assert same.battle_id == first.battle_id

    ended = adapter.parse(
        {"schema": 1, "active": False, "ts": 20.0, "objects": []},
        received_at=101.0)
    assert ended.status == STATUS_ENDED
    assert ended.battle_id == first.battle_id  # the final frame stays attributable

    next_battle = adapter.parse(flat_body(ts=1.0), received_at=102.0)
    assert next_battle.battle_id != first.battle_id


def test_legacy_inactive_frame_before_any_battle_is_waiting():
    adapter = WowsSchemaAdapter()
    snapshot = adapter.parse({"schema": 1, "active": False}, received_at=100.0)
    assert snapshot.status == STATUS_WAITING


def test_legacy_frames_go_stale_when_content_stops_changing():
    adapter = WowsSchemaAdapter()
    adapter.parse(flat_body(), received_at=100.0)
    still_live = adapter.parse(flat_body(), received_at=100.0 + LEGACY_STALE_SECONDS - 0.1)
    gone_stale = adapter.parse(flat_body(), received_at=100.0 + LEGACY_STALE_SECONDS + 0.5)
    assert still_live.status == STATUS_LIVE
    assert gone_stale.status == STATUS_STALE
    # Staleness must invalidate live domains but leave meta domains alone.
    assert gone_stale.availability_of(DOMAIN_SELF) == AVAIL_STALE
    assert gone_stale.availability_of(DOMAIN_MAP_BOUNDS) == AVAIL_AVAILABLE


def test_legacy_never_reports_ended_from_a_dead_stream():
    adapter = WowsSchemaAdapter()
    adapter.parse(flat_body(), received_at=100.0)
    for offset in (5.0, 60.0, 600.0):
        assert adapter.parse(
            flat_body(), received_at=100.0 + offset).status == STATUS_STALE


def test_torn_damage_table_does_not_invent_a_total():
    snapshot = WowsSchemaAdapter().parse(
        flat_body(damage={"inflicted": "garbage"}), received_at=100.0)
    assert snapshot.damage_inflicted is None
    assert snapshot.availability_of(DOMAIN_DAMAGE) == AVAIL_UNKNOWN


# --- cursor gate ---------------------------------------------------------

class _Frame:
    def __init__(self, instance_id, seq):
        self.instance_id = instance_id
        self.seq = seq


def test_cursor_accepts_strictly_advancing_seq():
    gate = CursorGate()
    assert gate.accept(_Frame("a", 1), 1)[0] is True
    assert gate.accept(_Frame("a", 2), 1)[0] is True


def test_cursor_drops_repeats_and_reordering():
    gate = CursorGate()
    gate.accept(_Frame("a", 5), 1)
    assert gate.accept(_Frame("a", 5), 1) == (False, DROP_DUPLICATE_SEQ)
    assert gate.accept(_Frame("a", 3), 1) == (False, DROP_DUPLICATE_SEQ)


def test_cursor_drops_frames_from_a_superseded_transport_generation():
    """A REST reply in flight when the socket returns must not win."""
    gate = CursorGate()
    gate.accept(_Frame("a", 10), 2)      # promoted to WS, epoch 2
    assert gate.accept(_Frame("a", 9), 1) == (False, DROP_STALE_EPOCH)


def test_cursor_adopts_a_restarted_service_even_though_seq_went_backwards():
    gate = CursorGate()
    gate.accept(_Frame("a", 900), 1)
    accepted, reason = gate.accept(_Frame("b", 1), 1)
    assert (accepted, reason) == (True, "")
    assert gate.cursor == ("b", 1)


def test_cursor_adopts_the_first_frame_of_a_new_epoch():
    gate = CursorGate()
    gate.accept(_Frame("a", 50), 1)
    # Same service, socket reconnected: epoch moved, seq keeps climbing.
    assert gate.accept(_Frame("a", 51), 2)[0] is True


def test_a_new_epoch_does_not_let_the_same_service_go_backwards():
    """REST→WS bumps the epoch; a buffered older frame must still lose."""
    gate = CursorGate()
    gate.accept(_Frame("a", 50), 1)
    assert gate.accept(_Frame("a", 49), 2) == (False, DROP_DUPLICATE_SEQ)
    assert gate.cursor == ("a", 50)


def test_cursor_rejects_a_frame_without_a_usable_sequence():
    gate = CursorGate()
    assert gate.accept(_Frame("a", None), 1) == (False, DROP_MALFORMED)


def test_cursor_counts_are_reported_for_the_panel():
    gate = CursorGate()
    gate.accept(_Frame("a", 1), 1)
    gate.accept(_Frame("a", 1), 1)
    stats = gate.as_dict()
    assert stats["accepted"] == 1
    assert stats["dropped"][DROP_DUPLICATE_SEQ] == 1


# --- stall detection -----------------------------------------------------

def _transport(clock):
    return TelemetryTransport(
        WowsConfig(), lambda frame: None, on_stall=lambda: None, clock=clock)


def test_one_failed_poll_is_a_hiccup_not_a_stall():
    transport = _transport(lambda: 1000.0)
    verdicts = [transport._stall_is_due() for _ in range(STALL_FAILURES)]
    assert verdicts == [False] * (STALL_FAILURES - 1) + [True]


def test_a_service_that_stays_down_is_not_relaunched_on_every_poll():
    now = {"t": 1000.0}
    transport = _transport(lambda: now["t"])
    for _ in range(STALL_FAILURES):
        transport._stall_is_due()

    assert transport._stall_is_due() is False
    now["t"] += STALL_INTERVAL_SECONDS
    assert transport._stall_is_due() is True


def test_a_successful_poll_clears_the_failure_run():
    transport = _transport(lambda: 1000.0)
    for _ in range(STALL_FAILURES - 1):
        transport._stall_is_due()
    transport._rest_failures_in_a_row = 0
    assert transport._stall_is_due() is False


def test_stop_swallows_a_closed_event_loop():
    transport = _transport(lambda: 1000.0)

    class _ClosedLoop:
        def call_soon_threadsafe(self, _cb):
            raise RuntimeError("Event loop is closed")

    transport._loop = _ClosedLoop()
    transport._thread = None
    transport.stop()


@pytest.mark.asyncio
async def test_wait_for_stop_uses_the_captured_flag_not_self():
    """A restarted transport replaces ``self._stop_flag``; old loops must ignore it."""
    transport = _transport(lambda: 1000.0)
    captured = threading.Event()
    transport._stop_flag = threading.Event()  # unset replacement — must not matter

    async def finish():
        await asyncio.sleep(0.05)
        # Replacing self mid-wait must not unblock the captured flag's waiter.
        transport._stop_flag = threading.Event()
        await asyncio.sleep(0.05)
        captured.set()

    task = asyncio.create_task(finish())
    await asyncio.wait_for(transport._wait_for_stop(captured), timeout=1.0)
    await task
