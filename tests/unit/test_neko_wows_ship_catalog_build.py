"""Conversion from pinned wowsinfo JSON into the immutable catalog."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from plugin.plugins.neko_wows.ship_data import source_wowsinfo
from plugin.plugins.neko_wows.ship_data.source_wowsinfo import (
    SourceValidationError,
    WowsInfoSourceAdapter,
    build_catalog,
)
from plugin.plugins.neko_wows.ship_data.store import ShipCatalogStore


@pytest.fixture
def source_payloads():
    wowsinfo = {
        "version": "15.6.0.0.12830008",
        "ships": {
            "4276041424": {
                "id": 4276041424,
                "index": "PJSB018",
                "name": "IDS_PJSB018",
                "tier": 10,
                "region": "Japan",
                "type": "Battleship",
                "group": "special",
                "paperShip": False,
                "costGold": 0,
                "modules": {
                    "_Hull": [
                        {
                            "index": 0,
                            "cost": {"costXP": 0, "costCR": 0},
                            "name": "IDS_HULL_A",
                            "components": {"hull": ["A_Hull"]},
                        },
                        {
                            "index": 1,
                            "cost": {"costXP": 50_000, "costCR": 5_000_000},
                            "name": "IDS_HULL_B",
                            "components": {
                                "hull": ["B_Hull"],
                                "airDefense": ["B_AirDefense"],
                            },
                        },
                    ],
                    "_Artillery": [
                        {
                            "index": 1,
                            "cost": {"costXP": 20_000, "costCR": 2_000_000},
                            "name": "IDS_ARTILLERY_A",
                            "components": {"artillery": ["A_Artillery"]},
                        },
                        {
                            "index": 1,
                            "cost": {"costXP": 10_000, "costCR": 1_000_000},
                            "name": "IDS_ARTILLERY_B",
                            "components": {"artillery": ["B_Artillery"]},
                        },
                    ],
                    "_Suo": [
                        {
                            "index": 0,
                            "cost": {"costXP": 0, "costCR": 0},
                            "name": "IDS_SUO_A",
                            "components": {"fireControl": ["A_FireControl"]},
                        }
                    ],
                },
                "components": {
                    "A_Hull": {
                        "health": 90_000,
                        "visibility": {"sea": 18.0, "plane": 11.0},
                        "mobility": {
                            "speed": 26.0,
                            "turningRadius": 950.0,
                            "rudderTime": 25.0,
                        },
                    },
                    "B_Hull": {
                        "health": 97_200,
                        "protection": 55.0,
                        "visibility": {
                            "sea": 17.5,
                            "plane": 10.0,
                            "seaInSmoke": 19.3,
                            "submarine": 10.0,
                        },
                        "mobility": {
                            "speed": 27.0,
                            "turningRadius": 900.0,
                            "rudderTime": 22.1,
                        },
                    },
                    "A_Artillery": {
                        "range": 26_630.0,
                        "sigma": 2.1,
                        "guns": [{
                            "reload": 30.0,
                            "rotation": 60.0,
                            "each": 3,
                            "count": 3,
                            "vertSector": 45.0,
                            "ammo": ["YAMATO_HE", "YAMATO_AP"],
                        }],
                    },
                    "B_Artillery": {
                        "range": 24_000.0,
                        "sigma": 2.0,
                        "guns": [{
                            "reload": 28.0,
                            "rotation": 55.0,
                            "each": 3,
                            "count": 3,
                            "ammo": ["YAMATO_HE", "YAMATO_AP"],
                        }],
                    },
                    "A_FireControl": {"maxDistCoef": 1.0, "sigmaCountCoef": 1.0},
                    "B_AirDefense": {
                        "far": [{
                            "minRange": 0.1,
                            "maxRange": 5.8,
                            "hitChance": 0.75,
                            "dps": 147.0,
                        }]
                    },
                },
                "consumables": [[
                    {"name": "PCY001_CrashCrew", "type": "Default"}
                ]],
            }
        },
        "projectiles": {
            "YAMATO_HE": {
                "type": "Artillery",
                "ammoType": "HE",
                "damage": 7_300,
                "burnChance": 0.35,
                "speed": 805.0,
                "weight": 1_360.0,
                "diameter": 0.46,
                "penHE": 77.0,
            },
            "YAMATO_AP": {
                "type": "Artillery",
                "ammoType": "AP",
                "damage": 14_800,
                "speed": 780.0,
                "weight": 1_460.0,
                "diameter": 0.46,
                "ap": {"drag": 0.292, "krupp": 2574.0},
                "fuseTime": 0.033,
            },
        },
        "abilities": {
            "PCY001_CrashCrew": {
                "name": "IDS_DOCK_CONSUME_TITLE_PCY001_CRASHCREW",
                "abilities": {"default": {"workTime": 15.0, "reloadTime": 80.0}},
            }
        },
        "alias": {"4276041424": {"alias": "大和号"}},
    }
    lang = {
        "en": {
            "IDS_PJSB018": "Yamato",
            "IDS_DOCK_CONSUME_TITLE_PCY001_CRASHCREW": "Damage Control Party",
        },
        "zh_sg": {
            "IDS_PJSB018": "大和",
            "IDS_DOCK_CONSUME_TITLE_PCY001_CRASHCREW": "损害管制小组",
        },
        "zh_tw": {"IDS_PJSB018": "大和"},
        "ja": {"IDS_PJSB018": "大和"},
    }
    return wowsinfo, lang


def write_sources(root: Path, payloads) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    wowsinfo, lang = payloads
    wowsinfo_path = root / "wowsinfo.json"
    lang_path = root / "lang.json"
    wowsinfo_path.write_text(
        json.dumps(wowsinfo, ensure_ascii=False), encoding="utf-8")
    lang_path.write_text(json.dumps(lang, ensure_ascii=False), encoding="utf-8")
    return wowsinfo_path, lang_path


def load_build_cli():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "build_neko_wows_ship_catalog.py"
    spec = importlib.util.spec_from_file_location("neko_wows_build_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_builds_terminal_primary_profile_and_aliases(source_payloads):
    catalog = WowsInfoSourceAdapter(minimum_ship_count=1).convert(*source_payloads)

    yamato = catalog.ships[4276041424]
    assert yamato.ship.display_name == "大和"
    assert yamato.primary.data["survivability"]["hit_points"] == 97_200
    assert yamato.primary.data["survivability"]["torpedo_protection_ratio"] == 0.55
    assert yamato.primary.data["main_battery"]["range_m"] == 26_630
    assert yamato.primary.data["main_battery"]["reload_s"] == 30.0
    assert yamato.primary.data["main_battery"]["sigma"] == 2.1
    aliases = {alias.alias for alias in yamato.aliases}
    assert {"Yamato", "大和", "大和号", "PJSB018", "IDS_PJSB018"} <= aliases


def test_adapter_preserves_sidegrade_with_deterministic_primary(source_payloads):
    catalog = WowsInfoSourceAdapter(minimum_ship_count=1).convert(*source_payloads)

    profiles = catalog.ships[4276041424].profiles
    assert [profile.variant_key for profile in profiles] == [
        "primary", "artillery:IDS_ARTILLERY_B"]
    assert profiles[0].profile_id == "4276041424:reference_top:primary"
    assert profiles[0].data["main_battery"]["reload_s"] == 30.0
    assert profiles[1].data["main_battery"]["reload_s"] == 28.0
    assert any(
        selection.slot == "hull" and selection.module_key == "IDS_HULL_B"
        for selection in profiles[0].selections
    )


def test_adapter_falls_back_to_adjacent_index_layers_without_graph_fields(
    source_payloads,
):
    catalog = WowsInfoSourceAdapter(minimum_ship_count=1).convert(*source_payloads)

    profiles = catalog.ships[4276041424].profiles
    primary_keys = {
        selection.slot: selection.module_key
        for selection in profiles[0].selections
    }

    assert primary_keys["hull"] == "IDS_HULL_B"
    assert [profile.variant_key for profile in profiles] == [
        "primary",
        "artillery:IDS_ARTILLERY_B",
    ]


def test_adapter_preserves_lower_index_explicit_branch_terminal_as_sidegrade(
    source_payloads,
):
    wowsinfo, lang = deepcopy(source_payloads)
    hulls = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"]
    hulls[0]["next_modules"] = ["IDS_HULL_B"]
    hulls[1]["index"] = 2
    hulls[1]["next_modules"] = []
    hulls.append({
        "index": 1,
        "cost": {"costXP": 10_000, "costCR": 1_000_000},
        "name": "IDS_HULL_C",
        "components": {"hull": ["B_Hull"]},
        "next_modules": [],
    })

    profiles = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].profiles

    primary_hull = next(
        selection for selection in profiles[0].selections
        if selection.slot == "hull"
    )
    assert primary_hull.module_key == "IDS_HULL_B"
    assert "hull:IDS_HULL_C" in {
        profile.variant_key for profile in profiles
    }


def test_adapter_prefers_explicit_top_lower_index_branch_terminal(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hulls = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"]
    hulls[0]["next_modules"] = ["IDS_HULL_B"]
    hulls[1]["index"] = 2
    hulls[1]["next_modules"] = []
    hulls.append({
        "index": 1,
        "cost": {"costXP": 10_000, "costCR": 1_000_000},
        "name": "IDS_HULL_C",
        "components": {"hull": ["B_Hull"]},
        "next_modules": [],
        "top": True,
    })

    profiles = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].profiles

    primary_hull = next(
        selection for selection in profiles[0].selections
        if selection.slot == "hull"
    )
    assert primary_hull.module_key == "IDS_HULL_C"
    assert "hull:IDS_HULL_B" in {
        profile.variant_key for profile in profiles
    }


def test_adapter_rejects_explicit_module_cycle(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hulls = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"]
    hulls[0]["next_modules"] = ["IDS_HULL_B"]
    hulls[1]["next_modules"] = ["IDS_HULL_A"]

    with pytest.raises(SourceValidationError, match="cycle"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


@pytest.mark.parametrize(("field", "reference"), [
    ("next_modules", "MISSING_NEXT"),
    ("predecessor", ["MISSING_PREDECESSOR"]),
])
def test_adapter_rejects_dangling_explicit_module_reference(
    source_payloads,
    field,
    reference,
):
    wowsinfo, lang = deepcopy(source_payloads)
    hull = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"][0]
    hull[field] = reference

    with pytest.raises(SourceValidationError, match="dangling"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_rejects_none_for_explicit_module_graph_field(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hull = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"][0]
    hull["next_modules"] = None

    with pytest.raises(SourceValidationError, match="invalid module graph field"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_rejects_uncovered_nodes_in_a_mixed_module_graph(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hulls = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"]
    hulls[1]["next_modules"] = []

    with pytest.raises(SourceValidationError, match="uncovered"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_rejects_duplicate_module_stable_key(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hulls = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"]
    hulls[1]["name"] = hulls[0]["name"]

    with pytest.raises(SourceValidationError, match="duplicate"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_orders_terminals_by_top_index_xp_credits_then_key(
    source_payloads,
):
    wowsinfo, lang = deepcopy(source_payloads)
    artillery = wowsinfo["ships"]["4276041424"]["modules"]["_Artillery"]

    def terminal(name, index, xp, credits, *, top=False):
        return {
            "index": index,
            "cost": {"costXP": xp, "costCR": credits},
            "name": name,
            "components": {"artillery": ["A_Artillery"]},
            "next_modules": [],
            "top": top,
        }

    artillery[:] = [
        terminal("IDS_ARTILLERY_LEX_B", 3, 50, 500),
        terminal("IDS_ARTILLERY_XP", 3, 100, 0),
        terminal("IDS_ARTILLERY_TOP", 0, 0, 0, top=True),
        terminal("IDS_ARTILLERY_INDEX", 4, 0, 0),
        terminal("IDS_ARTILLERY_CREDITS", 3, 50, 1_000),
        terminal("IDS_ARTILLERY_LEX_A", 3, 50, 500),
    ]

    profiles = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].profiles

    assert [profile.variant_key for profile in profiles] == [
        "primary",
        "artillery:IDS_ARTILLERY_INDEX",
        "artillery:IDS_ARTILLERY_XP",
        "artillery:IDS_ARTILLERY_CREDITS",
        "artillery:IDS_ARTILLERY_LEX_A",
        "artillery:IDS_ARTILLERY_LEX_B",
    ]
    primary_artillery = next(
        selection for selection in profiles[0].selections
        if selection.slot == "artillery"
    )
    assert primary_artillery.module_key == "IDS_ARTILLERY_TOP"


@pytest.mark.parametrize(
    "bad_value",
    [True, float("nan"), float("inf"), 10 ** 400],
)
def test_adapter_rejects_invalid_module_rank_numbers(source_payloads, bad_value):
    wowsinfo, lang = deepcopy(source_payloads)
    hull = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"][1]
    hull["cost"]["costXP"] = bad_value

    with pytest.raises(SourceValidationError, match="invalid xp"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


@pytest.mark.parametrize("bad_index", [True, 1.5, float("nan"), 10 ** 400])
def test_adapter_rejects_invalid_module_indexes(source_payloads, bad_index):
    wowsinfo, lang = deepcopy(source_payloads)
    hull = wowsinfo["ships"]["4276041424"]["modules"]["_Hull"][1]
    hull["index"] = bad_index

    with pytest.raises(SourceValidationError, match="invalid module index|invalid index"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_normalizes_projectiles_and_aa_units(source_payloads):
    catalog = WowsInfoSourceAdapter(minimum_ship_count=1).convert(*source_payloads)
    profile = catalog.ships[4276041424].primary.data

    he, ap = profile["main_battery"]["projectiles"]
    assert he["ammo_type"] == "HE"
    assert he["caliber_mm"] == 460.0
    assert he["fire_chance_ratio"] == 0.35
    assert ap["krupp"] == 2574.0
    assert profile["anti_air"]["auras"][0]["max_range_m"] == 5_800


def test_adapter_normalizes_carrier_aircraft_and_weapon(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    ship = wowsinfo["ships"]["4276041424"]
    ship["modules"]["_Fighter"] = [{
        "index": 0,
        "cost": {"costXP": 0, "costCR": 0},
        "name": "IDS_FIGHTER_TOP",
        "components": {"fighter": ["FighterComponent"]},
    }]
    ship["components"]["FighterComponent"] = ["YAMATO_FIGHTER"]
    wowsinfo["aircrafts"] = {
        "YAMATO_FIGHTER": {
            "name": "IDS_YAMATO_FIGHTER",
            "type": "Fighter",
            "health": 2_060,
            "speed": 128.0,
            "totalPlanes": 9,
            "visibility": 10.0,
            "aircraft": {
                "attacker": 3,
                "attackCount": 2,
                "maxAircraft": 14,
                "restoreTime": 84.0,
                "cooldown": 9.0,
                "maxSpeed": 1.25,
                "minSpeed": 0.8,
                "boostTime": 20.0,
                "boostReload": 40.0,
                "bombName": "YAMATO_HE",
            },
        }
    }
    lang["en"]["IDS_YAMATO_FIGHTER"] = "Yamato fighter"
    lang["zh_sg"]["IDS_YAMATO_FIGHTER"] = "大和战斗机"

    profile = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].primary.data

    aircraft = profile["aircraft"][0]
    assert aircraft["role"] == "fighter"
    assert aircraft["display_name"] == "大和战斗机"
    assert aircraft["hit_points"] == 2_060
    assert aircraft["squadron_size"] == 9
    assert aircraft["attack_group_size"] == 3
    assert aircraft["restoration_s"] == 84.0
    assert aircraft["max_speed_knots"] == 160
    assert aircraft["weapon"]["ammo_type"] == "HE"
    assert aircraft["weapon"]["max_damage"] == 7_300


def test_adapter_preserves_submarine_dive_capacity(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    hull = wowsinfo["ships"]["4276041424"]["components"]["B_Hull"]
    hull["submarineBattery"] = {"capacity": 240.0, "regen": 1.2}

    profile = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].primary.data

    assert profile["submarine"]["dive_capacity_s"] == 240
    assert profile["submarine"]["dive_capacity_recharge_per_s"] == 1.2


def test_adapter_preserves_unlimited_consumable_sentinel(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    ability = wowsinfo["abilities"]["PCY001_CrashCrew"]["abilities"]["default"]
    ability["numConsumables"] = -1

    profile = WowsInfoSourceAdapter(minimum_ship_count=1).convert(
        wowsinfo, lang).ships[4276041424].primary.data

    option = profile["consumables"][0]["options"][0]
    assert option["unlimited_charges"] is True
    assert "charges_count" not in option


@pytest.mark.parametrize("missing_kind", ["hull", "projectile", "ability", "aircraft"])
def test_adapter_rejects_missing_selected_references(source_payloads, missing_kind):
    wowsinfo, lang = deepcopy(source_payloads)
    ship = wowsinfo["ships"]["4276041424"]
    if missing_kind == "hull":
        ship["modules"]["_Hull"][1]["components"]["hull"] = ["MISSING_HULL"]
    elif missing_kind == "projectile":
        ship["components"]["A_Artillery"]["guns"][0]["ammo"] = [
            "MISSING_PROJECTILE"]
    elif missing_kind == "ability":
        ship["consumables"][0][0]["name"] = "MISSING_ABILITY"
    else:
        ship["modules"]["_Fighter"] = [{
            "index": 0,
            "name": "IDS_FIGHTER_TOP",
            "components": {"fighter": ["FighterComponent"]},
        }]
        ship["components"]["FighterComponent"] = ["MISSING_AIRCRAFT"]
        wowsinfo["aircrafts"] = {}

    with pytest.raises(SourceValidationError, match="missing"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_rejects_an_implausibly_small_source(source_payloads):
    with pytest.raises(SourceValidationError, match="ship count"):
        WowsInfoSourceAdapter(minimum_ship_count=2).convert(*source_payloads)


@pytest.mark.parametrize("hull_modules", [None, []])
def test_adapter_rejects_missing_or_empty_hull_module_candidates(
    source_payloads,
    hull_modules,
):
    wowsinfo, lang = deepcopy(source_payloads)
    modules = wowsinfo["ships"]["4276041424"]["modules"]
    if hull_modules is None:
        del modules["_Hull"]
    else:
        modules["_Hull"] = hull_modules

    with pytest.raises(SourceValidationError, match="hull module candidates"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_adapter_rejects_malformed_module_candidate_elements(source_payloads):
    wowsinfo, lang = deepcopy(source_payloads)
    wowsinfo["ships"]["4276041424"]["modules"]["_Artillery"].append(None)

    with pytest.raises(SourceValidationError, match="artillery module candidate"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


@pytest.mark.parametrize(
    "bad_value",
    [-1.0, True, float("nan"), float("inf"), 10 ** 400],
)
def test_adapter_rejects_invalid_combat_numbers(source_payloads, bad_value):
    wowsinfo, lang = deepcopy(source_payloads)
    wowsinfo["ships"]["4276041424"]["components"][
        "A_Artillery"]["guns"][0]["reload"] = bad_value

    with pytest.raises(SourceValidationError, match="numeric"):
        WowsInfoSourceAdapter(minimum_ship_count=1).convert(wowsinfo, lang)


def test_build_writes_queryable_catalog_and_atomic_manifest(tmp_path, source_payloads):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "catalog"
    wowsinfo_path, lang_path = write_sources(source_dir, source_payloads)

    result = build_catalog(
        wowsinfo_path,
        lang_path,
        output_dir,
        source_commit="f" * 40,
        source_channel="live",
        minimum_ship_count=1,
    )

    assert result.database_path.is_file()
    assert result.manifest_path == output_dir / "active.json"
    snapshot = ShipCatalogStore(output_dir).snapshot()
    try:
        assert snapshot.meta is not None
        assert snapshot.meta.content_sha256 == result.content_sha256
        assert snapshot.primary_profile(4276041424) is not None
    finally:
        snapshot.close()


def test_build_requires_explicit_live_source_channel(tmp_path, source_payloads):
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)

    with pytest.raises(SourceValidationError, match="source channel"):
        build_catalog(
            wowsinfo_path,
            lang_path,
            tmp_path / "catalog",
            source_commit="f" * 40,
            minimum_ship_count=1,
        )


def test_build_rejects_public_test_paths_labeled_as_live(tmp_path, source_payloads):
    wowsinfo_path, lang_path = write_sources(
        tmp_path / "public_test" / "source",
        source_payloads,
    )

    with pytest.raises(SourceValidationError, match="public test"):
        build_catalog(
            wowsinfo_path,
            lang_path,
            tmp_path / "catalog",
            source_commit="f" * 40,
            source_channel="live",
            minimum_ship_count=1,
        )


def test_build_rejects_public_test_marker_in_source_filename(
    tmp_path,
    source_payloads,
):
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)
    marked_path = wowsinfo_path.with_name("public_test-wowsinfo.json")
    wowsinfo_path.rename(marked_path)

    with pytest.raises(SourceValidationError, match="public test"):
        build_catalog(
            marked_path,
            lang_path,
            tmp_path / "catalog",
            source_commit="f" * 40,
            source_channel="live",
            minimum_ship_count=1,
        )


def test_build_rejects_mixed_live_and_public_test_paths(tmp_path, source_payloads):
    live_wowsinfo, _ = write_sources(tmp_path / "live", source_payloads)
    _, public_test_lang = write_sources(tmp_path / "public_test", source_payloads)

    with pytest.raises(SourceValidationError, match="mixed"):
        build_catalog(
            live_wowsinfo,
            public_test_lang,
            tmp_path / "catalog",
            source_commit="f" * 40,
            source_channel="live",
            minimum_ship_count=1,
        )


def test_repeated_build_has_same_logical_content_digest(tmp_path, source_payloads):
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)

    first = build_catalog(
        wowsinfo_path, lang_path, tmp_path / "one",
        source_commit="f" * 40, source_channel="live", minimum_ship_count=1)
    second = build_catalog(
        wowsinfo_path, lang_path, tmp_path / "two",
        source_commit="f" * 40, source_channel="live", minimum_ship_count=1)

    assert first.content_sha256 == second.content_sha256


def test_failed_build_keeps_existing_manifest_bytes(tmp_path, source_payloads):
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)
    output_dir = tmp_path / "catalog"
    build_catalog(
        wowsinfo_path, lang_path, output_dir,
        source_commit="f" * 40, source_channel="live", minimum_ship_count=1)
    before = (output_dir / "active.json").read_bytes()
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{}", encoding="utf-8")

    with pytest.raises(SourceValidationError):
        build_catalog(
            malformed, lang_path, output_dir,
            source_commit="f" * 40, source_channel="live", minimum_ship_count=1)

    assert (output_dir / "active.json").read_bytes() == before


def test_oversized_primary_render_keeps_existing_manifest_bytes(
    tmp_path,
    source_payloads,
    monkeypatch,
):
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)
    output_dir = tmp_path / "catalog"
    build_catalog(
        wowsinfo_path, lang_path, output_dir,
        source_commit="f" * 40, source_channel="live", minimum_ship_count=1)
    before = (output_dir / "active.json").read_bytes()
    monkeypatch.setattr(
        source_wowsinfo,
        "PRIMARY_TEXT_PART_MAX_BYTES",
        64,
        raising=False,
    )

    with pytest.raises(SourceValidationError, match="rendered primary profile"):
        build_catalog(
            wowsinfo_path, lang_path, output_dir,
            source_commit="f" * 40, source_channel="live", minimum_ship_count=1)

    assert (output_dir / "active.json").read_bytes() == before


def test_build_cli_accepts_local_sources_without_network(tmp_path, source_payloads):
    root = Path(__file__).resolve().parents[2]
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)
    output_dir = tmp_path / "catalog"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "build_neko_wows_ship_catalog.py"),
            "--wowsinfo-json", str(wowsinfo_path),
            "--lang-json", str(lang_path),
            "--output-dir", str(output_dir),
            "--source-commit", "f" * 40,
            "--source-channel", "live",
            "--minimum-ship-count", "1",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["game_version"] == "15.6.0.0.12830008"
    assert summary["ship_count"] == 1
    assert (output_dir / "active.json").is_file()


def test_build_cli_requires_source_channel_for_local_files(
    tmp_path,
    source_payloads,
    capsys,
):
    cli = load_build_cli()
    wowsinfo_path, lang_path = write_sources(tmp_path / "source", source_payloads)

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "--wowsinfo-json", str(wowsinfo_path),
            "--lang-json", str(lang_path),
            "--output-dir", str(tmp_path / "catalog"),
            "--minimum-ship-count", "1",
        ])

    assert exc_info.value.code == 2
    assert "--source-channel" in capsys.readouterr().err


def test_pinned_download_uses_only_fixed_repository_paths(tmp_path, source_payloads):
    cli = load_build_cli()
    wowsinfo, lang = source_payloads
    payload_by_suffix = {
        "/live/app/data/wowsinfo.json": json.dumps(wowsinfo).encode("utf-8"),
        "/live/app/lang/lang.json": json.dumps(lang).encode("utf-8"),
    }
    seen: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.close()

    def opener(url, *, timeout):
        assert timeout == 30.0
        seen.append(url)
        suffix = next(suffix for suffix in payload_by_suffix if url.endswith(suffix))
        return Response(payload_by_suffix[suffix])

    revision = "a" * 40
    wowsinfo_path, lang_path = cli.download_pinned_sources(
        revision, tmp_path, opener=opener)

    assert seen == [
        f"https://raw.githubusercontent.com/wowsinfo/data/{revision}"
        "/live/app/data/wowsinfo.json",
        f"https://raw.githubusercontent.com/wowsinfo/data/{revision}"
        "/live/app/lang/lang.json",
    ]
    assert json.loads(wowsinfo_path.read_text(encoding="utf-8"))["version"]
    assert "zh_sg" in json.loads(lang_path.read_text(encoding="utf-8"))


def test_pinned_download_rejects_branch_names_and_url_input(tmp_path):
    cli = load_build_cli()

    with pytest.raises(ValueError, match="40-character commit SHA"):
        cli.download_pinned_sources("main", tmp_path)
    with pytest.raises(ValueError, match="40-character commit SHA"):
        cli.download_pinned_sources("https://evil.example/data", tmp_path)
