"""Stable, prompt-safe rendering of canonical ship reference profiles."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .models import ShipCounts, ShipResolution

_VERSION_STATUSES = frozenset({"match", "mismatch", "unknown"})
_CLASS_LABELS = {
    "Battleship": "战列舰",
    "Cruiser": "巡洋舰",
    "Destroyer": "驱逐舰",
    "AirCarrier": "航空母舰",
    "Submarine": "潜艇",
}
_ROMAN_TIERS = (
    "",
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
)
_SPACE_RE = re.compile(r"\s+")


def _safe_text(value: Any, *, maximum: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    value = _SPACE_RE.sub(" ", value).strip()
    value = value.replace("<", "＜").replace(">", "＞")
    return value[:maximum]


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _plain(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    if isinstance(number, int):
        return str(number)
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _seconds(value: Any) -> str | None:
    number = _number(value)
    return None if number is None else f"{float(number):.1f} s"


def _distance(value: Any) -> str | None:
    number = _plain(value)
    return None if number is None else f"{number} m"


def _ratio(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{float(number) * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _append(parts: list[str], label: str, value: str | None) -> None:
    if value:
        parts.append(f"{label} {value}")


class ShipReferenceRenderer:
    """Render only approved canonical keys in a fixed section order."""

    def render(
        self,
        resolution: ShipResolution,
        counts: ShipCounts,
        *,
        version_status: str,
    ) -> str:
        ship, profile, catalog_version = self._validated(
            resolution, version_status)
        data = _mapping(profile.data)
        tier = (
            _ROMAN_TIERS[ship.tier]
            if 0 < ship.tier < len(_ROMAN_TIERS)
            else str(ship.tier)
        )
        ship_class = _CLASS_LABELS.get(ship.ship_class, _safe_text(ship.ship_class))
        lines = [
            "<<<WOWS_SHIP_REFERENCE>>>",
            f"catalog_version={catalog_version}",
            f"version_status={version_status}",
            f"configuration={_safe_text(profile.configuration)}",
            "notice=这是离线顶配参考，不代表玩家实际配装或实时增益",
            "",
            (
                f"舰船：{_safe_text(ship.display_name)} | {tier}级 | {ship_class} | "
                f"{self._counts(counts)}"
            ),
        ]

        self._render_survivability(lines, _mapping(data.get("survivability")))
        self._render_mobility(lines, _mapping(data.get("mobility")))
        self._render_concealment(lines, _mapping(data.get("concealment")))
        self._render_battery(lines, "主炮", _mapping(data.get("main_battery")))
        self._render_battery(lines, "副炮", _mapping(data.get("secondary_battery")))
        self._render_torpedoes(lines, _mapping(data.get("torpedoes")))
        self._render_anti_air(lines, _mapping(data.get("anti_air")))
        self._render_asw(lines, _mapping(data.get("asw")))
        self._render_air_support(lines, _mapping(data.get("air_support")))
        self._render_submarine(lines, _mapping(data.get("submarine")))
        self._render_aircraft(lines, data.get("aircraft"))
        self._render_consumables(lines, data.get("consumables"))
        lines.append("<<<END_WOWS_SHIP_REFERENCE>>>")
        return "\n".join(lines)

    def render_count_update(
        self,
        resolution: ShipResolution,
        counts: ShipCounts,
        *,
        version_status: str,
    ) -> str:
        ship, profile, catalog_version = self._validated(
            resolution, version_status)
        return "\n".join((
            "<<<WOWS_SHIP_COUNT_UPDATE>>>",
            f"catalog_version={catalog_version}",
            f"version_status={version_status}",
            f"configuration={_safe_text(profile.configuration)}",
            f"舰船：{_safe_text(ship.display_name)} | {self._counts(counts)}",
            "<<<END_WOWS_SHIP_COUNT_UPDATE>>>",
        ))

    @staticmethod
    def _validated(resolution: ShipResolution, version_status: str):
        if version_status not in _VERSION_STATUSES:
            raise ValueError("invalid version_status")
        if not resolution.resolved or resolution.ship is None or resolution.profile is None:
            raise ValueError("ship resolution must be resolved")
        meta = resolution.meta
        catalog_version = _safe_text(meta.catalog_version if meta is not None else "unknown")
        return resolution.ship, resolution.profile, catalog_version

    @staticmethod
    def _counts(counts: ShipCounts) -> str:
        return (
            f"自身{counts.self_count} 友军{counts.ally_count} "
            f"敌军{counts.enemy_count}"
        )

    @staticmethod
    def _render_survivability(lines: list[str], raw: Mapping[str, Any]) -> None:
        parts: list[str] = []
        _append(parts, "HP", _plain(raw.get("hit_points")))
        _append(parts, "鱼雷防护", _ratio(raw.get("torpedo_protection_ratio")))
        if parts:
            lines.append("生存：" + "；".join(parts))

    @staticmethod
    def _render_mobility(lines: list[str], raw: Mapping[str, Any]) -> None:
        parts: list[str] = []
        speed = _plain(raw.get("max_speed_knots"))
        underwater = _plain(raw.get("underwater_speed_knots"))
        _append(parts, "最大航速", f"{speed} kn" if speed else None)
        _append(parts, "水下航速", f"{underwater} kn" if underwater else None)
        _append(parts, "转向半径", _distance(raw.get("turning_radius_m")))
        _append(parts, "转舵", _seconds(raw.get("rudder_shift_s")))
        if parts:
            lines.append("机动：" + "；".join(parts))

    @staticmethod
    def _render_concealment(lines: list[str], raw: Mapping[str, Any]) -> None:
        parts: list[str] = []
        for key, label in (
            ("surface_detect_m", "水面发现"),
            ("air_detect_m", "空中发现"),
            ("surface_detect_in_smoke_m", "烟中水面发现"),
            ("air_detect_in_smoke_m", "烟中空中发现"),
            ("periscope_detect_m", "潜望镜发现"),
        ):
            _append(parts, label, _distance(raw.get(key)))
        if parts:
            lines.append("隐蔽：" + "；".join(parts))

    def _render_battery(
        self, lines: list[str], label: str, raw: Mapping[str, Any]
    ) -> None:
        if not raw:
            return
        parts: list[str] = []
        mounts = _sequence(raw.get("mounts"))
        mount_labels: list[str] = []
        for item in mounts:
            mount = _mapping(item)
            count = _plain(mount.get("mount_count"))
            barrels = _plain(mount.get("barrels_per_mount"))
            if count and barrels:
                mount_labels.append(f"{count}×{barrels}")
        projectiles = _sequence(raw.get("projectiles"))
        caliber = next((
            _plain(_mapping(item).get("caliber_mm"))
            for item in projectiles
            if _plain(_mapping(item).get("caliber_mm")) is not None
        ), None)
        if mount_labels:
            layout = "+".join(mount_labels)
            parts.append(f"{layout} {caliber} mm" if caliber else layout)
        _append(parts, "射程", _distance(raw.get("range_m")))
        _append(parts, "装填", _seconds(raw.get("reload_s")))
        _append(parts, "转炮180°", _seconds(raw.get("rotation_180_s")))
        _append(parts, "sigma", _plain(raw.get("sigma")))
        if parts:
            lines.append(f"{label}：" + "；".join(parts))
        for ordinal, projectile in enumerate(projectiles, start=1):
            rendered = self._projectile(_mapping(projectile))
            if rendered:
                lines.append(f"{label}弹药{ordinal}：{rendered}")

    def _render_torpedoes(self, lines: list[str], raw: Mapping[str, Any]) -> None:
        if not raw:
            return
        parts: list[str] = []
        layouts: list[str] = []
        for item in _sequence(raw.get("launchers")):
            launcher = _mapping(item)
            count = _plain(launcher.get("launcher_count"))
            tubes = _plain(launcher.get("tubes_per_launcher"))
            if count and tubes:
                layouts.append(f"{count}×{tubes}")
            _append(parts, "装填", _seconds(launcher.get("reload_s")))
            _append(parts, "转管180°", _seconds(launcher.get("rotation_180_s")))
        if layouts:
            parts.insert(0, "+".join(layouts))
        if raw.get("single_launch") is True:
            parts.append("支持单发")
        if parts:
            lines.append("鱼雷：" + "；".join(parts))
        for ordinal, projectile in enumerate(_sequence(raw.get("projectiles")), start=1):
            rendered = self._projectile(_mapping(projectile))
            if rendered:
                lines.append(f"鱼雷弹药{ordinal}：{rendered}")

    @staticmethod
    def _projectile(raw: Mapping[str, Any]) -> str:
        parts: list[str] = []
        ammo_type = _safe_text(raw.get("ammo_type") or raw.get("type"), maximum=24)
        if ammo_type:
            parts.append(ammo_type)
        for key, label, formatter in (
            ("caliber_mm", "口径", lambda value: f"{_plain(value)} mm" if _plain(value) else None),
            ("max_damage", "最大伤害", _plain),
            ("fire_chance_ratio", "点火率", _ratio),
            ("initial_velocity_mps", "初速", lambda value: f"{_plain(value)} m/s" if _plain(value) else None),
            ("mass_kg", "弹重", lambda value: f"{_plain(value)} kg" if _plain(value) else None),
            ("he_penetration_mm", "HE穿深", lambda value: f"{_plain(value)} mm" if _plain(value) else None),
            ("sap_penetration_mm", "SAP穿深", lambda value: f"{_plain(value)} mm" if _plain(value) else None),
            ("ricochet_start_deg", "跳弹起始", lambda value: f"{_plain(value)}°" if _plain(value) else None),
            ("ricochet_always_deg", "必跳角", lambda value: f"{_plain(value)}°" if _plain(value) else None),
            ("fuse_s", "引信", _seconds),
            ("drag_coefficient", "阻力", _plain),
            ("krupp", "Krupp", _plain),
            ("speed_knots", "航速", lambda value: f"{_plain(value)} kn" if _plain(value) else None),
            ("detectability_m", "发现", _distance),
            ("range_m", "射程", _distance),
        ):
            _append(parts, label, formatter(raw.get(key)))
        if raw.get("deep_water") is True:
            parts.append("深水")
        return "；".join(parts)

    @staticmethod
    def _render_anti_air(lines: list[str], raw: Mapping[str, Any]) -> None:
        if not raw:
            return
        for ordinal, item in enumerate(_sequence(raw.get("auras")), start=1):
            aura = _mapping(item)
            parts: list[str] = []
            band = _safe_text(aura.get("band"), maximum=16)
            if band:
                parts.append(band)
            _append(parts, "内圈", _distance(aura.get("min_range_m")))
            _append(parts, "外圈", _distance(aura.get("max_range_m")))
            _append(parts, "命中率", _ratio(aura.get("hit_chance_ratio")))
            _append(parts, "持续DPS", _plain(aura.get("continuous_dps")))
            if parts:
                lines.append(f"防空光环{ordinal}：" + "；".join(parts))
        flak = _mapping(raw.get("flak"))
        parts = []
        _append(parts, "内爆数量", _plain(flak.get("inner_count")))
        _append(parts, "外爆数量", _plain(flak.get("outer_count")))
        _append(parts, "最小射程", _distance(flak.get("min_range_m")))
        _append(parts, "最大射程", _distance(flak.get("max_range_m")))
        _append(parts, "伤害", _plain(flak.get("damage")))
        if parts:
            lines.append("防空黑云：" + "；".join(parts))

    def _render_asw(self, lines: list[str], raw: Mapping[str, Any]) -> None:
        if not raw:
            return
        parts: list[str] = []
        _append(parts, "装填", _seconds(raw.get("reload_s")))
        _append(parts, "每组弹数", _plain(raw.get("bombs_count")))
        _append(parts, "组数", _plain(raw.get("groups_count")))
        projectile = self._projectile(_mapping(raw.get("projectile")))
        if projectile:
            parts.append(projectile)
        if parts:
            lines.append("反潜：" + "；".join(parts))

    @staticmethod
    def _render_air_support(lines: list[str], raw: Mapping[str, Any]) -> None:
        parts: list[str] = []
        _append(parts, "装填", _seconds(raw.get("reload_s")))
        _append(parts, "射程", _distance(raw.get("range_m")))
        _append(parts, "次数", _plain(raw.get("charges_count")))
        if parts:
            lines.append("空中支援：" + "；".join(parts))

    @staticmethod
    def _render_submarine(lines: list[str], raw: Mapping[str, Any]) -> None:
        parts: list[str] = []
        _append(parts, "潜航容量", _seconds(raw.get("dive_capacity_s")))
        recharge = _plain(raw.get("dive_capacity_recharge_per_s"))
        _append(parts, "容量恢复", f"{recharge}/s" if recharge else None)
        _append(parts, "声呐装填", _seconds(raw.get("ping_reload_s")))
        _append(parts, "声呐射程", _distance(raw.get("ping_range_m")))
        speed = _plain(raw.get("ping_speed_mps"))
        _append(parts, "声呐速度", f"{speed} m/s" if speed else None)
        if parts:
            lines.append("潜艇：" + "；".join(parts))

    @classmethod
    def _render_aircraft(cls, lines: list[str], raw: Any) -> None:
        for ordinal, value in enumerate(_sequence(raw), start=1):
            aircraft = _mapping(value)
            parts: list[str] = []
            role = _safe_text(aircraft.get("role"), maximum=32)
            name = _safe_text(aircraft.get("display_name"), maximum=80)
            if role:
                parts.append(role)
            if name:
                parts.append(name)
            _append(parts, "单机HP", _plain(aircraft.get("hit_points")))
            for key, label in (
                ("cruise_speed_knots", "巡航"),
                ("min_speed_knots", "最低航速"),
                ("max_speed_knots", "最高航速"),
            ):
                speed = _plain(aircraft.get(key))
                _append(parts, label, f"{speed} kn" if speed else None)
            _append(parts, "发现", _distance(aircraft.get("detectability_m")))
            _append(parts, "编队", _plain(aircraft.get("squadron_size")))
            _append(parts, "攻击组", _plain(aircraft.get("attack_group_size")))
            _append(parts, "单机投射", _plain(aircraft.get("payload_per_aircraft")))
            _append(parts, "甲板储备", _plain(aircraft.get("deck_reserve")))
            _append(parts, "整备", _seconds(aircraft.get("restoration_s")))
            _append(parts, "攻击间隔", _seconds(aircraft.get("attack_cooldown_s")))
            _append(parts, "加力持续", _seconds(aircraft.get("boost_duration_s")))
            _append(parts, "加力恢复", _seconds(aircraft.get("boost_reload_s")))
            weapon = cls._projectile(_mapping(aircraft.get("weapon")))
            if weapon:
                parts.append("武器 " + weapon)
            if parts:
                lines.append(f"航空兵{ordinal}：" + "；".join(parts))

    @staticmethod
    def _render_consumables(lines: list[str], raw: Any) -> None:
        for slot_value in _sequence(raw):
            slot = _mapping(slot_value)
            slot_number = _plain(slot.get("slot"))
            for option in _sequence(slot.get("options")):
                ability = _mapping(option)
                parts: list[str] = []
                name = _safe_text(ability.get("display_name"), maximum=80)
                if name:
                    parts.append(name)
                key = _safe_text(ability.get("ability_key"), maximum=80)
                if not name and key:
                    parts.append(key)
                variant = _safe_text(ability.get("variant"), maximum=40)
                if variant:
                    parts.append(variant)
                _append(parts, "持续", _seconds(ability.get("duration_s")))
                _append(parts, "冷却", _seconds(ability.get("cooldown_s")))
                _append(parts, "次数", _plain(ability.get("charges_count")))
                if ability.get("unlimited_charges") is True:
                    parts.append("无限次数")
                if parts:
                    label = f"消耗品槽{slot_number}" if slot_number else "消耗品"
                    lines.append(label + "：" + "；".join(parts))


__all__ = ["ShipReferenceRenderer"]
