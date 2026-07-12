"""Safe display labels for plugin reports and Hosted UI summaries."""

from __future__ import annotations


_EVENT_LABELS: dict[str, str] = {
    "stall_risk": "失速风险",
    "high_aoa": "攻角过大",
    "over_g": "过载过大",
    "low_alt_danger": "低空危险",
    "overspeed": "超速风险",
    "overheat": "过热风险",
    "low_fuel": "低油量",
    "ground_laser_warning": "激光告警",
    "ground_crew_loss": "车组受损",
    "ground_gunner_disabled": "炮手失能",
    "ground_driver_disabled": "驾驶员失能",
    "ground_ammo_empty": "一级弹药耗尽",
    "ground_ammo_low": "一级弹药偏少",
    "ground_target_nearby": "任务目标接近",
    "enemy_nearby": "敌方接近",
    "air_threat_nearby": "空中威胁接近",
    "enemy_on_six": "后方威胁",
    "tailing_risk": "持续尾随风险",
    "player_radio_command": "玩家无线电",
    "you_killed": "击杀确认",
    "you_died": "被击毁",
    "spawn": "出场",
    "battle_end": "战局结束",
}


def display_event_id(event_id: str) -> str:
    """Return a user-facing label while keeping unknown ids debuggable."""

    return _EVENT_LABELS.get(event_id, event_id)


def display_event_key(event_key: str) -> str:
    """Format keys such as ``low_alt_danger/critical`` for reports."""

    event_id, sep, suffix = event_key.partition("/")
    label = display_event_id(event_id)
    return f"{label} / {suffix}" if sep else label
