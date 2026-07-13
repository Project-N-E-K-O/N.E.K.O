"""连续派生检测器（电平 flag → 边沿）：stall / overheat / low_fuel / low_alt / overspeed。

flag 名来自 core/flag_codes.py（接缝集中）。payload 取数据层已派生的数值，仅作"事实行"上下文。
overspeed 消费数据层 v1.6 的 overspeed_warn / overspeed_critical，不在插件侧重算阈值。
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleState
from ...core.flag_codes import CONDITION_FLAG_GROUPS
from .._base import ConditionDetector


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _is_fixed_wing_air(s: BattleState) -> bool:
    return (s.domain or "").lower() == "air"


def _is_ground(s: BattleState) -> bool:
    return (s.domain or "").lower() == "ground"


def _pl_stall(s: BattleState) -> dict[str, Any]:
    return _drop_none(
        {
            "domain": s.domain,
            "ias_kmh": s.ias_kmh,
            "aoa_deg": s.aoa_deg,
            "altitude_m": s.altitude_m,
            "radio_altitude_m": s.radio_altitude_m,
        }
    )


def _pl_high_aoa(s: BattleState) -> dict[str, Any]:
    return _drop_none({"domain": s.domain, "aoa_deg": s.aoa_deg, "ias_kmh": s.ias_kmh, "g_now": s.g_now})


def _pl_over_g(s: BattleState) -> dict[str, Any]:
    return _drop_none({"domain": s.domain, "g_now": s.g_now, "ias_kmh": s.ias_kmh, "aoa_deg": s.aoa_deg})


def _pl_overheat(s: BattleState) -> dict[str, Any]:
    temperatures = (
        ("water_temp_c", s.water_temp_c),
        ("head_temp_c", s.head_temp_c),
        ("turbine_temp_c", s.turbine_temp_c),
        ("oil_temp_c", s.oil_temp_c),
    )
    source, temp = next(((name, value) for name, value in temperatures if value is not None), (None, None))
    return _drop_none({"domain": s.domain, "temp_c": temp, "temp_source": source})


def _pl_low_fuel(s: BattleState) -> dict[str, Any]:
    return _drop_none({"domain": s.domain, "fuel_fraction": s.fuel_fraction})


def _pl_low_alt(s: BattleState) -> dict[str, Any]:
    return _drop_none(
        {
            "domain": s.domain,
            "altitude_m": s.altitude_m,
            "radio_altitude_m": s.radio_altitude_m,
            "climb_ms": s.climb_ms,
            "ias_kmh": s.ias_kmh,
        }
    )


def _pl_overspeed(s: BattleState) -> dict[str, Any]:
    return _drop_none({"domain": s.domain, "ias_kmh": s.ias_kmh, "mach": s.mach})


def _pl_ground_laser(s: BattleState) -> dict[str, Any]:
    return _drop_none({"domain": s.domain})


def build_condition_detectors() -> list[ConditionDetector]:
    g = CONDITION_FLAG_GROUPS
    return [
        ConditionDetector(
            "stall_risk",
            g["stall_risk"],
            confirm_enter=2,
            confirm_exit=3,
            payload_fn=_pl_stall,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector(
            "high_aoa",
            g["high_aoa"],
            confirm_enter=1,
            confirm_exit=2,
            payload_fn=_pl_high_aoa,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector(
            "over_g",
            g["over_g"],
            confirm_enter=1,
            confirm_exit=2,
            payload_fn=_pl_over_g,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector(
            "low_alt_danger",
            g["low_alt_danger"],
            confirm_enter=2,
            confirm_exit=2,
            payload_fn=_pl_low_alt,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector(
            "overspeed",
            g["overspeed"],
            confirm_enter=2,
            confirm_exit=3,
            payload_fn=_pl_overspeed,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector("overheat", g["overheat"], confirm_enter=3, confirm_exit=4, payload_fn=_pl_overheat),
        ConditionDetector(
            "low_fuel",
            g["low_fuel"],
            confirm_enter=1,
            confirm_exit=2,
            payload_fn=_pl_low_fuel,
            predicate=_is_fixed_wing_air,
        ),
        ConditionDetector(
            "ground_laser_warning",
            g["ground_laser_warning"],
            confirm_enter=1,
            confirm_exit=2,
            payload_fn=_pl_ground_laser,
            predicate=_is_ground,
        ),
    ]
