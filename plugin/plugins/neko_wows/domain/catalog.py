"""The event catalog: one row per thing the companion may say.

Everything the arbiter needs to rank, throttle, collapse and gate an event lives
here rather than being spread across detectors, so a timing change is a data
edit and the ordering stays reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import LANE_NORMAL, LANE_URGENT
from .snapshot import (
    DOMAIN_BALLISTICS,
    DOMAIN_DAMAGE,
    DOMAIN_MAP_BOUNDS,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
)


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    lane: str
    priority: int
    summary: str
    cooldown_seconds: float
    coalesce_key: str
    once_per_battle: bool = False
    preempt: bool = False
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    ttl_seconds: float | None = None


# --- lifecycle -----------------------------------------------------------
BATTLE_STARTED = "battle_started"
BATTLE_ENDED = "battle_ended"
POST_BATTLE_SUMMARY = "post_battle_summary"

# --- survival ------------------------------------------------------------
OWN_SHIP_SUNK = "own_ship_sunk"
LOW_HEALTH = "low_health"
RAPID_DAMAGE = "rapid_damage"
OUTNUMBERED = "outnumbered"
LOCALLY_ISOLATED = "locally_isolated"

# --- threat --------------------------------------------------------------
ENEMY_CLOSING = "enemy_closing"
MULTI_DIRECTION_THREAT = "multi_direction_threat"

# --- geometry ------------------------------------------------------------
BOUNDARY_RISK = "boundary_risk"
OWN_BROADSIDE_EXPOSED = "own_broadside_exposed"
TARGET_BROADSIDE_WINDOW = "target_broadside_window"

# --- targeting -----------------------------------------------------------
PRIORITY_TARGET = "priority_target"
LOW_HP_TARGET = "low_hp_target"
DAMAGE_MILESTONE = "damage_milestone"
AMMO_RECHECK_HINT = "ammo_recheck_hint"

# --- situation -----------------------------------------------------------
SITUATION_ADVICE = "situation_advice"


_SPECS: tuple[EventSpec, ...] = (
    # Lifecycle events read the source status, not live ship data, so they stay
    # available on the final inactive frame.
    EventSpec(
        event_id=BATTLE_STARTED,
        lane=LANE_NORMAL,
        priority=60,
        summary="开局",
        cooldown_seconds=60.0,
        coalesce_key="wows_lifecycle",
        once_per_battle=True,
    ),
    EventSpec(
        event_id=BATTLE_ENDED,
        lane=LANE_NORMAL,
        priority=65,
        summary="终局",
        cooldown_seconds=60.0,
        coalesce_key="wows_lifecycle",
        once_per_battle=True,
    ),
    EventSpec(
        event_id=POST_BATTLE_SUMMARY,
        lane=LANE_NORMAL,
        priority=40,
        summary="战后摘要",
        cooldown_seconds=120.0,
        coalesce_key="wows_summary",
        once_per_battle=True,
        optional=(DOMAIN_DAMAGE, DOMAIN_ROSTER),
    ),

    EventSpec(
        event_id=OWN_SHIP_SUNK,
        lane=LANE_URGENT,
        priority=95,
        summary="沉没",
        cooldown_seconds=30.0,
        coalesce_key="wows_survival",
        once_per_battle=True,
        preempt=True,
        required=(DOMAIN_SELF,),
    ),
    EventSpec(
        event_id=LOW_HEALTH,
        lane=LANE_URGENT,
        priority=90,
        summary="低血量",
        cooldown_seconds=20.0,
        coalesce_key="wows_survival",
        preempt=True,
        required=(DOMAIN_SELF,),
    ),
    EventSpec(
        event_id=RAPID_DAMAGE,
        lane=LANE_URGENT,
        priority=85,
        summary="快速受伤",
        cooldown_seconds=12.0,
        coalesce_key="wows_survival",
        preempt=True,
        required=(DOMAIN_SELF,),
    ),
    EventSpec(
        event_id=OUTNUMBERED,
        lane=LANE_NORMAL,
        priority=50,
        summary="人数劣势",
        cooldown_seconds=45.0,
        coalesce_key="wows_situation",
        required=(DOMAIN_OBJECTS,),
        optional=(DOMAIN_ROSTER,),
    ),
    EventSpec(
        event_id=LOCALLY_ISOLATED,
        lane=LANE_NORMAL,
        priority=55,
        summary="局部孤立",
        cooldown_seconds=40.0,
        coalesce_key="wows_situation",
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),

    EventSpec(
        event_id=ENEMY_CLOSING,
        lane=LANE_URGENT,
        priority=80,
        summary="敌舰逼近",
        cooldown_seconds=15.0,
        coalesce_key="wows_threat",
        preempt=True,
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),
    EventSpec(
        event_id=MULTI_DIRECTION_THREAT,
        lane=LANE_URGENT,
        priority=82,
        summary="多方向威胁",
        cooldown_seconds=20.0,
        coalesce_key="wows_threat",
        preempt=True,
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),

    EventSpec(
        event_id=BOUNDARY_RISK,
        lane=LANE_URGENT,
        priority=70,
        summary="边界风险",
        cooldown_seconds=25.0,
        coalesce_key="wows_geometry",
        required=(DOMAIN_SELF, DOMAIN_MAP_BOUNDS),
    ),
    EventSpec(
        event_id=OWN_BROADSIDE_EXPOSED,
        lane=LANE_URGENT,
        priority=75,
        summary="露侧风险",
        cooldown_seconds=18.0,
        coalesce_key="wows_geometry",
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),
    EventSpec(
        event_id=TARGET_BROADSIDE_WINDOW,
        lane=LANE_NORMAL,
        priority=45,
        summary="目标露侧窗口",
        cooldown_seconds=20.0,
        coalesce_key="wows_targeting",
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),

    EventSpec(
        event_id=PRIORITY_TARGET,
        lane=LANE_NORMAL,
        priority=35,
        summary="目标优先候选",
        cooldown_seconds=30.0,
        coalesce_key="wows_targeting",
        required=(DOMAIN_SELF, DOMAIN_OBJECTS),
    ),
    EventSpec(
        event_id=LOW_HP_TARGET,
        lane=LANE_NORMAL,
        priority=48,
        summary="残血目标",
        cooldown_seconds=25.0,
        coalesce_key="wows_targeting",
        required=(DOMAIN_OBJECTS,),
    ),
    EventSpec(
        event_id=DAMAGE_MILESTONE,
        lane=LANE_NORMAL,
        priority=30,
        summary="伤害里程碑",
        cooldown_seconds=30.0,
        coalesce_key="wows_progress",
        required=(DOMAIN_DAMAGE,),
    ),
    EventSpec(
        event_id=AMMO_RECHECK_HINT,
        lane=LANE_NORMAL,
        priority=38,
        summary="弹药复核提示",
        cooldown_seconds=35.0,
        coalesce_key="wows_targeting",
        # Needs the actual selected shell; without it there is no honest way to
        # say anything about ammunition.
        required=(DOMAIN_BALLISTICS, DOMAIN_OBJECTS),
    ),

    EventSpec(
        event_id=SITUATION_ADVICE,
        lane=LANE_NORMAL,
        priority=25,
        summary="局势建议",
        cooldown_seconds=90.0,
        coalesce_key="wows_situation",
        required=(DOMAIN_SELF,),
        optional=(DOMAIN_ROSTER, DOMAIN_OBJECTS),
    ),
)

EVENT_CATALOG: dict[str, EventSpec] = {spec.event_id: spec for spec in _SPECS}


def spec_for(event_id: str) -> EventSpec:
    try:
        return EVENT_CATALOG[event_id]
    except KeyError as exc:  # pragma: no cover - guards catalog/detector drift
        raise KeyError(f"event {event_id!r} is not in EVENT_CATALOG") from exc
