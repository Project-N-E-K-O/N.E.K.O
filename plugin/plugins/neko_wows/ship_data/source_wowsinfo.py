"""Convert the fixed wowsinfo JSON schema into the local ship catalog."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .module_selection import (
    ModuleGraphError,
    ModuleNode,
    select_terminal_modules,
)
from .models import (
    CatalogMeta,
    CatalogShip,
    ShipCounts,
    ShipProfile,
    ShipResolution,
)
from .renderer import ShipReferenceRenderer
from .store import (
    CATALOG_SCHEMA_VERSION,
    MANIFEST_NAME,
    MANIFEST_VERSION,
    PROFILE_SCHEMA_VERSION,
    create_catalog_schema,
    file_sha256,
)

SOURCE_REPO = "https://github.com/wowsinfo/data"
SOURCE_PATHS = (
    "live/app/data/wowsinfo.json",
    "live/app/lang/lang.json",
)
BUILDER_VERSION = "1"
PRIMARY_TEXT_PART_MAX_BYTES = 240 * 1024

LANGUAGE_KEYS = {
    "en": "en",
    "ja": "ja",
    "zh-CN": "zh_sg",
    "zh-TW": "zh_tw",
}

SLOT_NAMES = {
    "_Hull": "hull",
    "_Artillery": "artillery",
    "_Engine": "engine",
    "_Suo": "fire_control",
    "_Torpedoes": "torpedoes",
    "_Atba": "secondary_battery",
    "_AirDefense": "anti_air",
    "_FlightControl": "flight_control",
    "_DiveBomber": "dive_bomber",
    "_TorpedoBomber": "torpedo_bomber",
    "_Fighter": "fighter",
    "_SkipBomber": "skip_bomber",
    "_Sonar": "sonar",
}

_STRICT_COMPONENT_KINDS = frozenset({
    "hull",
    "artillery",
    "atba",
    "torpedoes",
    "depthCharges",
    "airSupport",
    "pinger",
})

_AIRCRAFT_COMPONENT_ROLES = {
    "fighter": "fighter",
    "torpedoBomber": "torpedo_bomber",
    "diveBomber": "dive_bomber",
    "skipBomber": "skip_bomber",
}


class SourceValidationError(ValueError):
    """The upstream data is incomplete or structurally unsafe to activate."""


@dataclass(frozen=True)
class SourceAlias:
    alias_norm: str
    alias: str
    language: str
    alias_kind: str


@dataclass(frozen=True)
class BuiltModuleSelection:
    slot: str
    module_key: str
    module_index: int
    selection_kind: str
    component_ids: tuple[str, ...]


@dataclass(frozen=True)
class BuiltProfile:
    profile_id: str
    ship_id: int
    configuration: str
    variant_key: str
    is_primary: bool
    data: Mapping[str, Any]
    profile_sha256: str
    selections: tuple[BuiltModuleSelection, ...]


@dataclass(frozen=True)
class BuiltShip:
    ship: CatalogShip
    aliases: tuple[SourceAlias, ...]
    profiles: tuple[BuiltProfile, ...]

    @property
    def primary(self) -> BuiltProfile:
        return next(profile for profile in self.profiles if profile.is_primary)


@dataclass(frozen=True)
class ConvertedCatalog:
    game_version: str
    content_sha256: str
    ships: Mapping[int, BuiltShip]


@dataclass(frozen=True)
class CatalogBuildResult:
    database_path: Path
    manifest_path: Path
    catalog_version: str
    game_version: str
    content_sha256: str
    ship_count: int
    profile_count: int


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return " ".join(normalized.split())


def _stable_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _number(value: Any, *, low: float | None = None,
            high: float | None = None) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SourceValidationError("invalid boolean numeric value")
    if not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise SourceValidationError("invalid numeric value") from exc
    if not math.isfinite(number):
        raise SourceValidationError("invalid non-finite numeric value")
    if low is not None and number < low:
        raise SourceValidationError("numeric value is below the allowed range")
    if high is not None and number > high:
        raise SourceValidationError("numeric value is above the allowed range")
    rounded = round(number, 6)
    return int(rounded) if rounded.is_integer() else rounded


def _ratio(value: Any) -> int | float | None:
    number = _number(value, low=0.0)
    if number is None:
        return None
    ratio = float(number)
    if ratio > 1.0:
        ratio /= 100.0
    return _number(ratio, low=0.0, high=1.0)


def _scaled(value: Any, factor: float, *, low: float = 0.0) -> int | float | None:
    number = _number(value, low=low)
    return None if number is None else _number(float(number) * factor, low=low)


def _without_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if item is not None and item != () and item != [] and item != {}
    }


class WowsInfoSourceAdapter:
    """Strict adapter for wowsinfo's generated live JSON files."""

    def __init__(self, *, minimum_ship_count: int = 500,
                 maximum_ship_count: int = 5_000,
                 default_language: str = "zh-CN") -> None:
        self.minimum_ship_count = max(1, int(minimum_ship_count))
        self.maximum_ship_count = max(self.minimum_ship_count, int(maximum_ship_count))
        self.default_language = (
            default_language if default_language in LANGUAGE_KEYS else "zh-CN")

    def convert(self, wowsinfo: Mapping[str, Any],
                lang: Mapping[str, Any]) -> ConvertedCatalog:
        root = _mapping(wowsinfo)
        languages = _mapping(lang)
        game_version = _text(root.get("version"))
        raw_ships = _mapping(root.get("ships"))
        projectiles = _mapping(root.get("projectiles"))
        abilities = _mapping(root.get("abilities"))
        aircrafts = _mapping(root.get("aircrafts"))
        if not game_version:
            raise SourceValidationError("missing game version")
        if not (self.minimum_ship_count <= len(raw_ships) <= self.maximum_ship_count):
            raise SourceValidationError(
                f"ship count {len(raw_ships)} outside "
                f"{self.minimum_ship_count}..{self.maximum_ship_count}")
        if not projectiles:
            raise SourceValidationError("projectiles table is empty")
        if not abilities:
            raise SourceValidationError("abilities table is empty")
        for upstream_key in set(LANGUAGE_KEYS.values()):
            if not isinstance(languages.get(upstream_key), Mapping):
                raise SourceValidationError(f"missing language table {upstream_key}")

        upstream_aliases = _mapping(root.get("alias"))
        converted: dict[int, BuiltShip] = {}
        indexes: set[str] = set()
        for raw_key in sorted(raw_ships, key=str):
            raw_ship = _mapping(raw_ships[raw_key])
            ship_id = self._ship_id(raw_key, raw_ship)
            tier = raw_ship.get("tier")
            if not isinstance(tier, int) or isinstance(tier, bool) or not 1 <= tier <= 20:
                continue
            built = self._convert_ship(
                ship_id, raw_ship, languages, projectiles, abilities, aircrafts,
                _mapping(upstream_aliases.get(str(ship_id))),
            )
            if built.ship.ship_index in indexes:
                raise SourceValidationError(
                    f"duplicate ship index {built.ship.ship_index}")
            if ship_id in converted:
                raise SourceValidationError(f"duplicate ship id {ship_id}")
            indexes.add(built.ship.ship_index)
            converted[ship_id] = built
        if len(converted) < self.minimum_ship_count:
            raise SourceValidationError(
                f"usable ship count {len(converted)} below {self.minimum_ship_count}")

        logical = {
            "game_version": game_version,
            "ships": [self._logical_ship(converted[key]) for key in sorted(converted)],
        }
        return ConvertedCatalog(
            game_version=game_version,
            content_sha256=_sha256_text(logical),
            ships=converted,
        )

    @staticmethod
    def _ship_id(raw_key: Any, ship: Mapping[str, Any]) -> int:
        value = ship.get("id", raw_key)
        if isinstance(value, bool):
            raise SourceValidationError(f"invalid ship id {value!r}")
        try:
            ship_id = int(value)
        except (TypeError, ValueError) as exc:
            raise SourceValidationError(f"invalid ship id {value!r}") from exc
        if ship_id <= 0:
            raise SourceValidationError(f"invalid ship id {ship_id}")
        return ship_id

    def _convert_ship(
        self,
        ship_id: int,
        raw: Mapping[str, Any],
        languages: Mapping[str, Any],
        projectiles: Mapping[str, Any],
        abilities: Mapping[str, Any],
        aircrafts: Mapping[str, Any],
        upstream_alias: Mapping[str, Any],
    ) -> BuiltShip:
        index = _text(raw.get("index"))
        name_key = _text(raw.get("name"))
        ship_class = _text(raw.get("type"))
        nation = _text(raw.get("region"))
        if not index or not name_key or not ship_class or not nation:
            raise SourceValidationError(f"ship {ship_id} is missing identity fields")
        display_name = self._localized(languages, name_key, self.default_language)
        display_name = display_name or self._localized(languages, name_key, "en") or index
        group = _text(raw.get("group"))
        ship = CatalogShip(
            ship_id=ship_id,
            ship_index=index,
            name_key=name_key,
            display_name=display_name,
            nation=nation,
            ship_class=ship_class,
            tier=int(raw["tier"]),
            is_premium=bool(_number(raw.get("costGold"), low=0.0)),
            is_special=group not in ("", "upgradeable", "preserved"),
            is_paper=raw.get("paperShip") is True,
            availability_group=group,
        )
        aliases = self._aliases(
            ship, languages, _text(upstream_alias.get("alias")))
        profiles = self._profiles(
            ship_id, raw, projectiles, abilities, aircrafts, languages)
        if not profiles or sum(profile.is_primary for profile in profiles) != 1:
            raise SourceValidationError(f"ship {ship_id} has no unique primary profile")
        return BuiltShip(ship=ship, aliases=aliases, profiles=profiles)

    @staticmethod
    def _localized(languages: Mapping[str, Any], key: str, language: str) -> str:
        upstream = LANGUAGE_KEYS.get(language, language)
        value = _mapping(languages.get(upstream)).get(key)
        return _text(value)

    def _aliases(
        self,
        ship: CatalogShip,
        languages: Mapping[str, Any],
        upstream_alias: str,
    ) -> tuple[SourceAlias, ...]:
        candidates: list[tuple[str, str, str]] = []
        for public_language, upstream_language in LANGUAGE_KEYS.items():
            value = _text(_mapping(languages.get(upstream_language)).get(ship.name_key))
            if value:
                candidates.append((value, public_language, "localized_name"))
        if upstream_alias:
            candidates.append((upstream_alias, "zh-CN", "localized_name"))
        candidates.extend((
            (ship.ship_index, "und", "ship_index"),
            (ship.name_key, "und", "name_key"),
        ))
        aliases: dict[str, SourceAlias] = {}
        for value, language, kind in candidates:
            normalized = normalize_alias(value)
            if normalized and normalized not in aliases:
                aliases[normalized] = SourceAlias(normalized, value, language, kind)
        return tuple(aliases[key] for key in sorted(aliases))

    def _profiles(
        self,
        ship_id: int,
        ship: Mapping[str, Any],
        projectiles: Mapping[str, Any],
        abilities: Mapping[str, Any],
        aircrafts: Mapping[str, Any],
        languages: Mapping[str, Any],
    ) -> tuple[BuiltProfile, ...]:
        slots: dict[str, tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]] = {}
        for raw_slot, raw_modules in _mapping(ship.get("modules")).items():
            slot = SLOT_NAMES.get(str(raw_slot), self._normalize_slot(str(raw_slot)))
            raw_candidates = _sequence(raw_modules)
            if not raw_candidates:
                if raw_modules not in ((), []):
                    raise SourceValidationError(
                        f"invalid {slot} module candidates")
                continue
            if any(not isinstance(item, Mapping) for item in raw_candidates):
                raise SourceValidationError(
                    f"invalid {slot} module candidate element")
            modules = tuple(raw_candidates)
            try:
                terminals = select_terminal_modules(
                    (self._module_node(module, slot) for module in modules),
                    infer_adjacent_indexes=True,
                )
            except ModuleGraphError as exc:
                raise SourceValidationError(
                    f"invalid {slot} module graph: {exc}") from exc
            ordered = tuple(node.payload for node in terminals)
            slots[slot] = (ordered[0], ordered[1:])

        if "hull" not in slots:
            raise SourceValidationError(
                "hull module candidates are missing or empty")
        primary_selection = {slot: values[0] for slot, values in slots.items()}
        variants: list[tuple[str, dict[str, Mapping[str, Any]], str, str]] = [
            ("primary", primary_selection, "", "")
        ]
        for slot in sorted(slots):
            for alternative in slots[slot][1]:
                selection = dict(primary_selection)
                selection[slot] = alternative
                module_key = self._module_key(alternative, slot)
                variants.append((f"{slot}:{module_key}", selection, slot, module_key))

        profiles: list[BuiltProfile] = []
        for variant_key, selected, alternative_slot, alternative_key in variants:
            data = self._profile_data(
                ship, selected, projectiles, abilities, aircrafts, languages)
            profile_id = f"{ship_id}:reference_top:{variant_key}"
            selections: list[BuiltModuleSelection] = []
            for slot in sorted(selected):
                module = selected[slot]
                module_key = self._module_key(module, slot)
                has_sidegrades = bool(slots[slot][1])
                if slot == alternative_slot and module_key == alternative_key:
                    kind = "sidegrade_alternative"
                elif has_sidegrades:
                    kind = "sidegrade_primary"
                else:
                    kind = "terminal"
                component_ids = tuple(
                    component_id
                    for values in _mapping(module.get("components")).values()
                    for component_id in _sequence(values)
                    if isinstance(component_id, str)
                )
                selections.append(BuiltModuleSelection(
                    slot=slot,
                    module_key=module_key,
                    module_index=self._module_index(module),
                    selection_kind=kind,
                    component_ids=component_ids,
                ))
            profiles.append(BuiltProfile(
                profile_id=profile_id,
                ship_id=ship_id,
                configuration="reference_top",
                variant_key=variant_key,
                is_primary=variant_key == "primary",
                data=data,
                profile_sha256=_sha256_text(data),
                selections=tuple(selections),
            ))
        return tuple(profiles)

    @staticmethod
    def _module_index(module: Mapping[str, Any]) -> int:
        if "index" not in module:
            return 0
        value = module.get("index")
        if isinstance(value, bool) or not isinstance(value, int):
            raise SourceValidationError("invalid module index")
        return value

    def _module_node(self, module: Mapping[str, Any], slot: str) -> ModuleNode:
        cost = _mapping(module.get("cost"))
        next_refs, has_next = self._module_references(
            module, ("next_modules", "nextModules"))
        predecessor_refs, has_predecessor = self._module_references(
            module,
            ("predecessor", "predecessors", "previous_modules"),
        )
        return ModuleNode(
            stable_key=self._module_key(module, slot),
            payload=module,
            index=self._module_index(module),
            xp=cost.get("costXP", 0),
            credits=cost.get("costCR", 0),
            explicit_top=any(
                module.get(field) is True
                for field in ("top", "isTop", "is_top")
            ),
            next_refs=next_refs,
            predecessor_refs=predecessor_refs,
            has_explicit_graph=has_next or has_predecessor,
        )

    @staticmethod
    def _module_references(
        module: Mapping[str, Any],
        fields: tuple[str, ...],
    ) -> tuple[tuple[str, ...], bool]:
        references: list[str] = []
        present = False
        for field in fields:
            if field not in module:
                continue
            present = True
            raw = module.get(field)
            values = (raw,) if isinstance(raw, str) else _sequence(raw)
            if not isinstance(raw, str) and not values:
                if raw not in ((), []):
                    raise SourceValidationError(
                        f"invalid module graph field {field}")
                continue
            for value in values:
                reference = _text(value)
                if not reference:
                    raise SourceValidationError(
                        f"invalid module graph reference in {field}")
                references.append(reference)
        return tuple(references), present

    @staticmethod
    def _normalize_slot(value: str) -> str:
        text = re.sub(r"(?<!^)(?=[A-Z])", "_", value.lstrip("_")).lower()
        return text or "unknown"

    @staticmethod
    def _module_key(module: Mapping[str, Any], slot: str) -> str:
        name = _text(module.get("name"))
        if name:
            return name
        component_ids = sorted(
            component_id
            for values in _mapping(module.get("components")).values()
            for component_id in _sequence(values)
            if isinstance(component_id, str)
        )
        return component_ids[0] if component_ids else f"{slot}:{module.get('index', 0)}"

    def _profile_data(
        self,
        ship: Mapping[str, Any],
        selected: Mapping[str, Mapping[str, Any]],
        projectiles: Mapping[str, Any],
        abilities: Mapping[str, Any],
        aircrafts: Mapping[str, Any],
        languages: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_components = _mapping(ship.get("components"))
        refs: dict[str, tuple[str, ...]] = {}
        ordered_slots = sorted(selected, key=lambda slot: (slot != "hull", slot))
        for slot in ordered_slots:
            for kind, values in _mapping(selected[slot].get("components")).items():
                ids = tuple(value for value in _sequence(values) if isinstance(value, str))
                if ids:
                    refs[str(kind)] = ids

        def component(kind: str) -> Mapping[str, Any]:
            missing = tuple(
                component_id for component_id in refs.get(kind, ())
                if component_id not in raw_components
            )
            if missing and kind in _STRICT_COMPONENT_KINDS:
                raise SourceValidationError(
                    f"missing {kind} component {missing[0]}")
            for component_id in refs.get(kind, ()):
                value = raw_components.get(component_id)
                if isinstance(value, Mapping):
                    return value
            return {}

        profile: dict[str, Any] = {}
        hull = component("hull")
        if hull:
            survivability = _without_none({
                "hit_points": _number(hull.get("health"), low=0.0),
                "torpedo_protection_ratio": _ratio(hull.get("protection")),
            })
            if survivability:
                profile["survivability"] = survivability
            mobility = _mapping(hull.get("mobility"))
            normalized_mobility = _without_none({
                "max_speed_knots": _number(mobility.get("speed"), low=0.0),
                "underwater_speed_knots": _number(
                    mobility.get("speedUnderwater"), low=0.0),
                "turning_radius_m": _number(mobility.get("turningRadius"), low=0.0),
                "rudder_shift_s": _number(mobility.get("rudderTime"), low=0.0),
            })
            if normalized_mobility:
                profile["mobility"] = normalized_mobility
            visibility = _mapping(hull.get("visibility"))
            normalized_visibility = _without_none({
                "surface_detect_m": _scaled(visibility.get("sea"), 1000.0),
                "air_detect_m": _scaled(visibility.get("plane"), 1000.0),
                "surface_detect_in_smoke_m": _scaled(
                    visibility.get("seaInSmoke"), 1000.0),
                "air_detect_in_smoke_m": _scaled(
                    visibility.get("planeInSmoke"), 1000.0),
                "periscope_detect_m": _scaled(
                    visibility.get("submarine"), 1000.0),
            })
            if normalized_visibility:
                profile["concealment"] = normalized_visibility
            battery = _mapping(hull.get("submarineBattery"))
            normalized_battery = _without_none({
                "dive_capacity_s": _number(battery.get("capacity"), low=0.0),
                "dive_capacity_recharge_per_s": _number(
                    battery.get("regen"), low=0.0),
            })
            if normalized_battery:
                profile["submarine"] = normalized_battery

        artillery = component("artillery")
        fire_control = component("fireControl")
        if artillery:
            profile["main_battery"] = self._battery(
                artillery, projectiles, range_coefficient=(
                    _number(fire_control.get("maxDistCoef"), low=0.0) or 1.0),
                sigma_coefficient=(
                    _number(fire_control.get("sigmaCountCoef"), low=0.0) or 1.0),
            )
        secondaries = component("atba")
        if secondaries:
            profile["secondary_battery"] = self._battery(secondaries, projectiles)
        torpedoes = component("torpedoes")
        if torpedoes:
            normalized_torpedoes = self._torpedoes(torpedoes, projectiles)
            if normalized_torpedoes:
                profile["torpedoes"] = normalized_torpedoes

        air_defense = component("airDefense")
        if air_defense:
            normalized_aa = self._anti_air(air_defense)
            if normalized_aa:
                profile["anti_air"] = normalized_aa
        depth_charges = component("depthCharges")
        if depth_charges:
            profile["asw"] = self._simple_timed_weapon(depth_charges, projectiles)
        air_support = component("airSupport")
        if air_support:
            profile["air_support"] = _without_none({
                "reload_s": _number(air_support.get("reload"), low=0.0),
                "range_m": _scaled(air_support.get("range"), 1000.0),
                "charges_count": _number(air_support.get("chargesNum"), low=0.0),
            })
        pinger = component("pinger")
        if pinger:
            normalized_submarine = dict(_mapping(profile.get("submarine")))
            normalized_submarine.update(_without_none({
                "ping_reload_s": _number(pinger.get("reload"), low=0.0),
                "ping_range_m": _number(pinger.get("range"), low=0.0),
                "ping_speed_mps": _number(pinger.get("speed"), low=0.0),
                "first_ping_lifetime_s": _number(
                    pinger.get("lifeTime1"), low=0.0),
                "double_ping_lifetime_s": _number(
                    pinger.get("lifeTime2"), low=0.0),
            }))
            if normalized_submarine:
                profile["submarine"] = normalized_submarine
        normalized_aircraft = self._aircraft(
            refs,
            raw_components,
            aircrafts,
            projectiles,
            languages,
        )
        if normalized_aircraft:
            profile["aircraft"] = normalized_aircraft
        consumables = self._consumables(
            ship.get("consumables"), abilities, languages)
        if consumables:
            profile["consumables"] = consumables
        return profile

    def _battery(
        self,
        raw: Mapping[str, Any],
        projectiles: Mapping[str, Any],
        *,
        range_coefficient: int | float = 1.0,
        sigma_coefficient: int | float = 1.0,
    ) -> dict[str, Any]:
        mounts: list[dict[str, Any]] = []
        ammo_keys: list[str] = []
        for gun in _sequence(raw.get("guns")):
            if not isinstance(gun, Mapping):
                continue
            mount = _without_none({
                "mount_count": _number(gun.get("count"), low=0.0),
                "barrels_per_mount": _number(gun.get("each"), low=0.0),
                "reload_s": _number(gun.get("reload"), low=0.0),
                "rotation_180_s": _number(gun.get("rotation"), low=0.0),
                "vertical_sector_deg": _number(gun.get("vertSector"), low=0.0),
            })
            if mount:
                mounts.append(mount)
            for ammo in _sequence(gun.get("ammo")):
                if isinstance(ammo, str) and ammo not in ammo_keys:
                    ammo_keys.append(ammo)
        normalized_projectiles = tuple(
            value for key in ammo_keys
            if (value := self._projectile(key, _mapping(projectiles.get(key))))
        )
        first_mount = mounts[0] if mounts else {}
        return _without_none({
            "range_m": _scaled(raw.get("range"), float(range_coefficient)),
            "sigma": _scaled(raw.get("sigma"), float(sigma_coefficient)),
            "reload_s": first_mount.get("reload_s"),
            "rotation_180_s": first_mount.get("rotation_180_s"),
            "mounts": tuple(mounts),
            "projectiles": normalized_projectiles,
        })

    def _projectile(self, key: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not raw:
            if key:
                raise SourceValidationError(f"missing projectile {key}")
            return {}
        kind = _text(raw.get("type"))
        result: dict[str, Any] = {
            "projectile_key": key,
            "type": kind,
        }
        if kind == "Torpedo":
            result.update({
                "speed_knots": _number(raw.get("speed"), low=0.0),
                "detectability_m": _scaled(raw.get("visibility"), 1000.0),
                # wowsinfo stores torpedo range in 1/30 km units.
                "range_m": _scaled(raw.get("range"), 30.0),
                "max_damage": _number(
                    raw.get("alphaDamage", raw.get("damage")), low=0.0),
                "deep_water": raw.get("deepWater") is True,
            })
        else:
            ap = _mapping(raw.get("ap"))
            result.update({
                "ammo_type": _text(raw.get("ammoType")),
                "max_damage": _number(raw.get("damage"), low=0.0),
                "fire_chance_ratio": _ratio(raw.get("burnChance")),
                "initial_velocity_mps": _number(raw.get("speed"), low=0.0),
                "mass_kg": _number(raw.get("weight"), low=0.0),
                "caliber_mm": _scaled(raw.get("diameter"), 1000.0),
                "he_penetration_mm": _number(raw.get("penHE"), low=0.0),
                "sap_penetration_mm": _number(raw.get("penSAP"), low=0.0),
                "ricochet_start_deg": _number(raw.get("ricochetAngle"), low=0.0),
                "ricochet_always_deg": _number(raw.get("ricochetAlways"), low=0.0),
                "fuse_s": _number(raw.get("fuseTime"), low=0.0),
                "drag_coefficient": _number(ap.get("drag"), low=0.0),
                "krupp": _number(ap.get("krupp"), low=0.0),
            })
        return _without_none(result)

    def _torpedoes(self, raw: Mapping[str, Any],
                   projectiles: Mapping[str, Any]) -> dict[str, Any]:
        launchers: list[dict[str, Any]] = []
        ammo_keys: list[str] = []
        for launcher in _sequence(raw.get("launchers")):
            if not isinstance(launcher, Mapping):
                continue
            launchers.append(_without_none({
                "launcher_count": _number(launcher.get("count"), low=0.0),
                "tubes_per_launcher": _number(launcher.get("each"), low=0.0),
                "reload_s": _number(launcher.get("reload"), low=0.0),
                "rotation_180_s": _number(launcher.get("rotation"), low=0.0),
            }))
            for ammo in _sequence(launcher.get("ammo")):
                if isinstance(ammo, str) and ammo not in ammo_keys:
                    ammo_keys.append(ammo)
        return _without_none({
            "single_launch": raw.get("singleShot") is True,
            "launchers": tuple(item for item in launchers if item),
            "projectiles": tuple(
                value for key in ammo_keys
                if (value := self._projectile(key, _mapping(projectiles.get(key))))
            ),
        })

    @staticmethod
    def _anti_air(raw: Mapping[str, Any]) -> dict[str, Any]:
        auras: list[dict[str, Any]] = []
        order = {"near": 0, "medium": 1, "far": 2}
        for band in sorted(order, key=order.get):
            for aura in _sequence(raw.get(band)):
                if not isinstance(aura, Mapping):
                    continue
                value = _without_none({
                    "band": band,
                    "min_range_m": _scaled(aura.get("minRange"), 1000.0),
                    "max_range_m": _scaled(aura.get("maxRange"), 1000.0),
                    "hit_chance_ratio": _ratio(aura.get("hitChance")),
                    "continuous_dps": _number(aura.get("dps"), low=0.0),
                })
                if value:
                    auras.append(value)
        bubbles = _mapping(raw.get("bubbles"))
        flak = _without_none({
            "inner_count": _number(bubbles.get("inner"), low=0.0),
            "outer_count": _number(bubbles.get("outer"), low=0.0),
            "min_range_m": _scaled(bubbles.get("minRange"), 1000.0),
            "max_range_m": _scaled(bubbles.get("maxRange"), 1000.0),
            "damage": _number(bubbles.get("damage"), low=0.0),
        })
        return _without_none({"auras": tuple(auras), "flak": flak})

    def _simple_timed_weapon(self, raw: Mapping[str, Any],
                             projectiles: Mapping[str, Any]) -> dict[str, Any]:
        ammo = _text(raw.get("ammo"))
        return _without_none({
            "reload_s": _number(raw.get("reload"), low=0.0),
            "bombs_count": _number(raw.get("bombs"), low=0.0),
            "groups_count": _number(raw.get("groups"), low=0.0),
            "projectile": self._projectile(ammo, _mapping(projectiles.get(ammo)))
            if ammo else None,
        })

    def _consumables(
        self,
        raw_slots: Any,
        abilities: Mapping[str, Any],
        languages: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        slots: list[dict[str, Any]] = []
        for ordinal, raw_options in enumerate(_sequence(raw_slots), start=1):
            options: list[dict[str, Any]] = []
            for raw_option in _sequence(raw_options):
                if not isinstance(raw_option, Mapping):
                    continue
                key = _text(raw_option.get("name"))
                ability = _mapping(abilities.get(key))
                if key and not ability:
                    raise SourceValidationError(f"missing ability {key}")
                name_key = _text(ability.get("name"))
                display_name = (
                    self._localized(languages, name_key, self.default_language)
                    if name_key else ""
                )
                variants = _mapping(ability.get("abilities"))
                first_variant = next(
                    (value for value in variants.values() if isinstance(value, Mapping)),
                    {},
                )
                raw_charges = first_variant.get("numConsumables")
                unlimited_charges = raw_charges == -1
                options.append(_without_none({
                    "ability_key": key,
                    "variant": _text(raw_option.get("type")),
                    "display_name": display_name,
                    "duration_s": _number(first_variant.get("workTime"), low=0.0),
                    "cooldown_s": _number(first_variant.get("reloadTime"), low=0.0),
                    "charges_count": (
                        None if unlimited_charges
                        else _number(raw_charges, low=0.0)
                    ),
                    "unlimited_charges": unlimited_charges or None,
                }))
            if options:
                slots.append({"slot": ordinal, "options": tuple(options)})
        return tuple(slots)

    def _aircraft(
        self,
        refs: Mapping[str, tuple[str, ...]],
        components: Mapping[str, Any],
        aircrafts: Mapping[str, Any],
        projectiles: Mapping[str, Any],
        languages: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for component_kind, role in _AIRCRAFT_COMPONENT_ROLES.items():
            for component_id in refs.get(component_kind, ()):
                if component_id not in components:
                    raise SourceValidationError(
                        f"missing {component_kind} component {component_id}")
                aircraft_ids = tuple(
                    value for value in _sequence(components.get(component_id))
                    if isinstance(value, str) and value
                )
                for aircraft_id in aircraft_ids:
                    identity = (role, aircraft_id)
                    if identity in seen:
                        continue
                    raw = _mapping(aircrafts.get(aircraft_id))
                    if not raw:
                        raise SourceValidationError(
                            f"missing aircraft {aircraft_id}")
                    seen.add(identity)
                    details = _mapping(raw.get("aircraft"))
                    speed = _number(raw.get("speed"), low=0.0)
                    max_factor = _number(details.get("maxSpeed"), low=0.0)
                    min_factor = _number(details.get("minSpeed"), low=0.0)
                    weapon_key = _text(details.get("bombName"))
                    name_key = _text(raw.get("name"))
                    display_name = (
                        self._localized(languages, name_key, self.default_language)
                        if name_key else ""
                    )
                    display_name = display_name or (
                        self._localized(languages, name_key, "en")
                        if name_key else ""
                    )
                    item = _without_none({
                        "aircraft_key": aircraft_id,
                        "role": role,
                        "display_name": display_name,
                        "hit_points": _number(raw.get("health"), low=0.0),
                        "cruise_speed_knots": speed,
                        "min_speed_knots": (
                            _number(float(speed) * float(min_factor), low=0.0)
                            if speed is not None and min_factor is not None else None
                        ),
                        "max_speed_knots": (
                            _number(float(speed) * float(max_factor), low=0.0)
                            if speed is not None and max_factor is not None else None
                        ),
                        "detectability_m": _scaled(raw.get("visibility"), 1000.0),
                        "squadron_size": _number(raw.get("totalPlanes"), low=0.0),
                        "attack_group_size": _number(
                            details.get("attacker"), low=0.0),
                        "payload_per_aircraft": _number(
                            details.get("attackCount"), low=0.0),
                        "deck_reserve": _number(
                            details.get("maxAircraft"), low=0.0),
                        "restoration_s": _number(
                            details.get("restoreTime"), low=0.0),
                        "attack_cooldown_s": _number(
                            details.get("cooldown"), low=0.0),
                        "boost_duration_s": _number(
                            details.get("boostTime"), low=0.0),
                        "boost_reload_s": _number(
                            details.get("boostReload"), low=0.0),
                        "weapon": (
                            self._projectile(
                                weapon_key,
                                _mapping(projectiles.get(weapon_key)),
                            )
                            if weapon_key else None
                        ),
                    })
                    if item:
                        normalized.append(item)
        return tuple(normalized)

    @staticmethod
    def _logical_ship(value: BuiltShip) -> dict[str, Any]:
        ship = value.ship
        return {
            "ship": {
                "ship_id": ship.ship_id,
                "ship_index": ship.ship_index,
                "name_key": ship.name_key,
                "display_name": ship.display_name,
                "nation": ship.nation,
                "ship_class": ship.ship_class,
                "tier": ship.tier,
                "is_premium": ship.is_premium,
                "is_special": ship.is_special,
                "is_paper": ship.is_paper,
                "availability_group": ship.availability_group,
            },
            "aliases": [alias.__dict__ for alias in value.aliases],
            "profiles": [
                {
                    "profile_id": profile.profile_id,
                    "variant_key": profile.variant_key,
                    "is_primary": profile.is_primary,
                    "data": profile.data,
                    "selections": [selection.__dict__ for selection in profile.selections],
                }
                for profile in value.profiles
            ],
        }


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceValidationError(f"cannot read {path.name}: {type(exc).__name__}") from exc
    if not isinstance(value, Mapping):
        raise SourceValidationError(f"{path.name} must contain a JSON object")
    return value


def _safe_version(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return safe[:80] or "unknown"


def _source_path_channel_hint(path: Path) -> str | None:
    normalized = path.as_posix().casefold()
    parts = {part.casefold() for part in path.parts}
    if (
        any(
            marker in normalized
            for marker in ("public_test", "public-test", "publictest", "public test")
        )
        or "pt" in parts
    ):
        return "public_test"
    if "live" in parts:
        return "live"
    return None


def _validated_source_channel(
    value: str | None,
    wowsinfo_path: Path,
    lang_path: Path,
) -> str:
    channel = _text(value)
    if not channel:
        raise SourceValidationError("source channel must be explicitly declared")
    if channel != "live":
        raise SourceValidationError(f"unsupported source channel: {channel}")
    hints = {
        hint
        for hint in (
            _source_path_channel_hint(wowsinfo_path),
            _source_path_channel_hint(lang_path),
        )
        if hint is not None
    }
    if len(hints) > 1:
        raise SourceValidationError("mixed live and public test source paths")
    if "public_test" in hints:
        raise SourceValidationError("public test source paths cannot be activated as live")
    return channel


def _validate_primary_renders(
    converted: ConvertedCatalog,
    *,
    catalog_version: str,
    source_commit: str,
    source_channel: str,
    default_language: str,
) -> None:
    """Render every primary profile before any catalog file can be activated."""
    profile_count = sum(len(ship.profiles) for ship in converted.ships.values())
    meta = CatalogMeta(
        schema_version=CATALOG_SCHEMA_VERSION,
        catalog_version=catalog_version,
        game_version=converted.game_version,
        channel=source_channel,
        source_repo=SOURCE_REPO,
        source_commit=source_commit,
        content_sha256=converted.content_sha256,
        default_language=default_language,
        ship_count=len(converted.ships),
        profile_count=profile_count,
    )
    renderer = ShipReferenceRenderer()
    counts = ShipCounts()
    for ship_id in sorted(converted.ships):
        built = converted.ships[ship_id]
        primary = built.primary
        profile = ShipProfile(
            profile_id=primary.profile_id,
            ship_id=primary.ship_id,
            configuration=primary.configuration,
            variant_key=primary.variant_key,
            is_primary=primary.is_primary,
            profile_schema_version=PROFILE_SCHEMA_VERSION,
            data=primary.data,
            profile_sha256=primary.profile_sha256,
        )
        resolution = ShipResolution(
            query=built.ship.display_name,
            normalized_query=normalize_alias(built.ship.display_name),
            reason="resolved",
            meta=meta,
            ship=built.ship,
            profile=profile,
        )
        try:
            rendered = renderer.render(
                resolution,
                counts,
                version_status="match",
            )
        except Exception as exc:
            raise SourceValidationError(
                f"cannot render primary profile for ship {ship_id}: "
                f"{type(exc).__name__}"
            ) from exc
        rendered_bytes = len(rendered.encode("utf-8"))
        if rendered_bytes > PRIMARY_TEXT_PART_MAX_BYTES:
            raise SourceValidationError(
                f"rendered primary profile for ship {ship_id} exceeds "
                f"text-part limit: {rendered_bytes} > "
                f"{PRIMARY_TEXT_PART_MAX_BYTES} bytes"
            )


def build_catalog(
    wowsinfo_path: str | Path,
    lang_path: str | Path,
    output_dir: str | Path,
    *,
    source_commit: str = "local",
    source_channel: str | None = None,
    minimum_ship_count: int = 500,
    default_language: str = "zh-CN",
) -> CatalogBuildResult:
    """Build and atomically activate one immutable catalog."""
    wowsinfo_file = Path(wowsinfo_path)
    lang_file = Path(lang_path)
    root = Path(output_dir)
    channel = _validated_source_channel(
        source_channel,
        wowsinfo_file,
        lang_file,
    )
    wowsinfo = _read_json(wowsinfo_file)
    lang = _read_json(lang_file)
    converted = WowsInfoSourceAdapter(
        minimum_ship_count=minimum_ship_count,
        default_language=default_language,
    ).convert(wowsinfo, lang)
    source_hashes = {
        SOURCE_PATHS[0]: file_sha256(wowsinfo_file),
        SOURCE_PATHS[1]: file_sha256(lang_file),
    }
    commit = _text(source_commit) or "local"
    catalog_version = (
        f"{converted.game_version}:{commit[:12]}:v{CATALOG_SCHEMA_VERSION}")
    filename = (
        f"ship-catalog-{_safe_version(converted.game_version)}-"
        f"{_safe_version(commit[:8])}-{converted.content_sha256[:8]}-"
        f"v{CATALOG_SCHEMA_VERSION}.sqlite3"
    )
    _validate_primary_renders(
        converted,
        catalog_version=catalog_version,
        source_commit=commit,
        source_channel=channel,
        default_language=default_language,
    )
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".ship-catalog-", suffix=".tmp", dir=root)
    os.close(fd)
    temporary_path = Path(temporary_name)
    target = root / filename
    profile_count = sum(len(ship.profiles) for ship in converted.ships.values())
    try:
        conn = sqlite3.connect(temporary_path)
        try:
            create_catalog_schema(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO catalog_meta ("
                "id, schema_version, catalog_version, game_version, channel, "
                "source_repo, source_commit, source_paths_json, source_sha256_json, "
                "generated_at_utc, builder_version, content_sha256, default_language, "
                "ship_count, profile_count"
                ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    CATALOG_SCHEMA_VERSION,
                    catalog_version,
                    converted.game_version,
                    channel,
                    SOURCE_REPO,
                    commit,
                    _stable_json(SOURCE_PATHS),
                    _stable_json(source_hashes),
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    BUILDER_VERSION,
                    converted.content_sha256,
                    default_language,
                    len(converted.ships),
                    profile_count,
                ),
            )
            for ship_id in sorted(converted.ships):
                built = converted.ships[ship_id]
                ship = built.ship
                conn.execute(
                    "INSERT INTO ships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ship.ship_id, ship.ship_index, ship.name_key,
                        ship.display_name, ship.nation, ship.ship_class, ship.tier,
                        int(ship.is_premium), int(ship.is_special), int(ship.is_paper),
                        ship.availability_group,
                    ),
                )
                for alias in built.aliases:
                    conn.execute(
                        "INSERT INTO ship_aliases VALUES (?, ?, ?, ?, ?)",
                        (
                            alias.alias_norm, ship.ship_id, alias.alias,
                            alias.language, alias.alias_kind,
                        ),
                    )
                for profile in built.profiles:
                    conn.execute(
                        "INSERT INTO ship_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            profile.profile_id, profile.ship_id, profile.configuration,
                            profile.variant_key, int(profile.is_primary),
                            PROFILE_SCHEMA_VERSION, _stable_json(profile.data),
                            profile.profile_sha256,
                        ),
                    )
                    for selection in profile.selections:
                        conn.execute(
                            "INSERT INTO module_selections VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                profile.profile_id, selection.slot,
                                selection.module_key, selection.module_index,
                                selection.selection_kind,
                                _stable_json(selection.component_ids),
                            ),
                        )
            foreign_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_errors:
                raise SourceValidationError("catalog foreign-key check failed")
            conn.commit()
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise SourceValidationError("catalog integrity check failed")
        finally:
            conn.close()

        if target.exists():
            existing = sqlite3.connect(target)
            try:
                row = existing.execute(
                    "SELECT content_sha256 FROM catalog_meta WHERE id = 1").fetchone()
            finally:
                existing.close()
            if row is None or row[0] != converted.content_sha256:
                raise SourceValidationError("catalog filename collision")
            temporary_path.unlink()
        else:
            os.replace(temporary_path, target)

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "active_file": target.name,
            "sha256": file_sha256(target),
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": catalog_version,
            "game_version": converted.game_version,
        }
        manifest_path = root / MANIFEST_NAME
        manifest_temp = root / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
        with manifest_temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(manifest_temp, manifest_path)
        return CatalogBuildResult(
            database_path=target,
            manifest_path=manifest_path,
            catalog_version=catalog_version,
            game_version=converted.game_version,
            content_sha256=converted.content_sha256,
            ship_count=len(converted.ships),
            profile_count=profile_count,
        )
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


__all__ = [
    "BUILDER_VERSION",
    "CatalogBuildResult",
    "ConvertedCatalog",
    "SOURCE_PATHS",
    "SOURCE_REPO",
    "SourceValidationError",
    "WowsInfoSourceAdapter",
    "build_catalog",
    "normalize_alias",
]
