"""数据层告警 flag 名 → 我们事件的集中映射（接缝集中点）。

⚠️ 这是与数据层契约最易错的接缝：若真实 /api/processed.flags 的 code 拼写/分级
与此不符，改这一处即可（不动逻辑）。来源：data_layer/data process/后端接口文档.md
的「告警代码」表 + wt_processor.py。

每个连续事件 → 一组 (warning_code, critical_code)；多组表示"任一成立即触发"
（如失速可由速度过低 stall_* 或迎角过大 aoa_* 触发）。critical 优先于 warning。
"""

from __future__ import annotations

# event_id -> [(warning_flag, critical_flag), ...]
# Keep aoa_* separate from stall_risk. Data layer treats high AoA as a distinct fact;
# mapping it to stall caused high-G/loss-of-control cases to be spoken as stall.
CONDITION_FLAG_GROUPS: dict[str, list[tuple[str, str]]] = {
    "stall_risk": [("stall_warning", "stall_critical")],
    "high_aoa": [("aoa_high", "aoa_critical")],
    "over_g": [("over_g", "over_g_critical")],
    "overheat": [("engine_overheat", "engine_overheat_critical"), ("oil_overheat", "oil_overheat_critical")],
    "low_fuel": [("fuel_low", "fuel_critical")],
    "low_alt_danger": [("altitude_low", "altitude_critical")],
    "ground_crew_loss": [("crew_loss", "crew_critical")],
    "ground_gunner_disabled": [("gunner_disabled", "")],
    "ground_driver_disabled": [("driver_disabled", "")],
    "ground_ammo_low": [("ammo_low", "")],
    "ground_ammo_empty": [("ammo_empty", "")],
    "ground_laser_warning": [("", "laser_warning")],
    # overspeed：数据层 v1.6 已提供 warning/critical flag；插件侧只消费，不自行算阈值。
    "overspeed": [("overspeed_warn", "overspeed_critical")],
}
