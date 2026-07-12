"""共享数据契约（v0.2 · 消费数据层 /api/telemetry）。

设计依据：docs/D-B1（Scenario）/ D-B2（BattleEvent 字典）/ D-B5（事件→数据层来源映射）。
- 数据层负责"事实 + 逐机阈值告警 flags"；本插件只消费，不重算阈值。
- BattleState = 我们对一帧 /api/telemetry 的归一化只读视图 + 派生（scenario）。
- BattleEvent = 理解层唯一对外语义事件；severity/priority/category/preempt 全查 EVENT_CATALOG。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Scenario（D-B1 轻量 phase 机）
# ---------------------------------------------------------------------------

OUT_OF_BATTLE = "OUT_OF_BATTLE"
SPAWNING = "SPAWNING"
IN_FLIGHT = "IN_FLIGHT"
COMBAT_STRESS = "COMBAT_STRESS"
CRITICAL_RISK = "CRITICAL_RISK"
DEAD = "DEAD"
BATTLE_ENDED = "BATTLE_ENDED"

ALL_SCENARIOS = (
    OUT_OF_BATTLE,
    SPAWNING,
    IN_FLIGHT,
    COMBAT_STRESS,
    CRITICAL_RISK,
    DEAD,
    BATTLE_ENDED,
)

# ---------------------------------------------------------------------------
# 事件类别（D-B1 第 4 节门控矩阵的列）
# ---------------------------------------------------------------------------

CAT_LIFECYCLE = "lifecycle"            # spawn / you_died / battle_end
CAT_SAFETY_CRITICAL = "safety_critical"      # stall / aoa / over-g / low_alt / overspeed（触发 CRITICAL_RISK + 可抢占）
CAT_SAFETY_IMPORTANT = "safety_important"    # overheat（不触发 CRITICAL_RISK、不抢占）
CAT_SAFETY_MINOR = "safety_minor"            # low_fuel
CAT_COMBAT_KILL = "combat_kill"              # you_killed
CAT_MAP_AWARENESS = "map_awareness"          # proximity / situation awareness（v2，低优先级）
CAT_PLAYER_COMMAND = "player_command"        # 玩家主动固定无线电指令
CAT_CHATTER = "chatter"                      # 陪伴闲聊（v1 暂不主动产）

SEV_WARNING = 2
SEV_IMPORTANT = 6
SEV_CRITICAL = 8
SEV_LIFECYCLE = 1

# 危急集合：触发 CRITICAL_RISK 的安全事件 + you_died。抢占资格 = 属此集合 且 数据层报 critical。
CRITICAL_EVENT_IDS = frozenset({"stall_risk", "high_aoa", "over_g", "low_alt_danger", "overspeed"})
PREEMPT_ELIGIBLE_IDS = CRITICAL_EVENT_IDS | {"you_died"}
CRITICAL_FLAG_CODES = frozenset(
    {"stall_critical", "aoa_critical", "over_g_critical", "altitude_critical", "overspeed_critical"}
)


@dataclass(frozen=True)
class EventSpec:
    """单个事件的静态策略（D-B2）。运行时数值仍为草稿，待真机校准。"""

    event_id: str
    category: str
    priority: int
    preempt: bool
    cooldown_seconds: float          # <0 表示"每局/每次一次"，由 Detector/Arbiter 用语义去重
    severity_warning: int
    severity_critical: int
    blocked: bool = False            # 历史兼容字段；v1.6 已接通的事件不得再标为 blocked

    def severity_for(self, level: str) -> int:
        return self.severity_critical if level == "critical" else self.severity_warning


# 事件目录（D-B2 总览矩阵）。cooldown<0 = 一次性（spawn/battle_end/you_died/low_fuel 每局少量）。
EVENT_CATALOG: dict[str, EventSpec] = {
    "stall_risk":     EventSpec("stall_risk", CAT_SAFETY_CRITICAL, 9, True, 15, SEV_WARNING, SEV_CRITICAL),
    "high_aoa":       EventSpec("high_aoa", CAT_SAFETY_CRITICAL, 9, True, 10, SEV_WARNING, SEV_CRITICAL),
    "over_g":         EventSpec("over_g", CAT_SAFETY_CRITICAL, 9, True, 10, SEV_WARNING, SEV_CRITICAL),
    "low_alt_danger": EventSpec("low_alt_danger", CAT_SAFETY_CRITICAL, 9, True, 10, 7, 9),
    "overspeed":      EventSpec("overspeed", CAT_SAFETY_CRITICAL, 8, True, 15, 6, 7),
    "overheat":       EventSpec("overheat", CAT_SAFETY_IMPORTANT, 6, False, 30, 5, SEV_IMPORTANT),
    "low_fuel":       EventSpec("low_fuel", CAT_SAFETY_MINOR, 4, False, -1, 3, 4),
    "ground_laser_warning": EventSpec("ground_laser_warning", CAT_SAFETY_IMPORTANT, 7, False, 10, 6, 7),
    "ground_crew_loss": EventSpec("ground_crew_loss", CAT_SAFETY_IMPORTANT, 6, False, 20, 5, 7),
    "ground_gunner_disabled": EventSpec("ground_gunner_disabled", CAT_SAFETY_IMPORTANT, 6, False, 20, 5, 5),
    "ground_driver_disabled": EventSpec("ground_driver_disabled", CAT_SAFETY_IMPORTANT, 6, False, 20, 5, 5),
    "ground_ammo_empty": EventSpec("ground_ammo_empty", CAT_SAFETY_IMPORTANT, 5, False, 25, 5, 5),
    "ground_ammo_low": EventSpec("ground_ammo_low", CAT_SAFETY_MINOR, 3, False, 45, 2, 2),
    "ground_target_nearby": EventSpec("ground_target_nearby", CAT_MAP_AWARENESS, 2, False, 35, 2, 2),
    "enemy_nearby":   EventSpec("enemy_nearby", CAT_MAP_AWARENESS, 2, False, 25, 2, 2),
    "air_threat_nearby": EventSpec("air_threat_nearby", CAT_MAP_AWARENESS, 3, False, 20, 3, 3),
    "enemy_on_six":   EventSpec("enemy_on_six", CAT_MAP_AWARENESS, 4, False, 20, 4, 4),
    "tailing_risk":   EventSpec("tailing_risk", CAT_MAP_AWARENESS, 5, False, 25, 5, 5),
    "free_text_activity": EventSpec("free_text_activity", CAT_MAP_AWARENESS, 1, False, 25, 1, 1),
    "player_radio_command": EventSpec("player_radio_command", CAT_PLAYER_COMMAND, 3, False, 10, 2, 2),
    "you_killed":     EventSpec("you_killed", CAT_COMBAT_KILL, 5, False, 8, 3, 3),
    "you_died":       EventSpec("you_died", CAT_LIFECYCLE, 10, True, -1, SEV_CRITICAL, SEV_CRITICAL),
    "spawn":          EventSpec("spawn", CAT_LIFECYCLE, 5, False, -1, SEV_LIFECYCLE, SEV_LIFECYCLE),
    "battle_end":     EventSpec("battle_end", CAT_LIFECYCLE, 6, False, -1, SEV_LIFECYCLE, SEV_LIFECYCLE),
}

# D-B1 第 4 节门控矩阵：scenario -> 允许的事件类别集合（其余抑制）。
SCENARIO_GATING: dict[str, frozenset[str]] = {
    OUT_OF_BATTLE: frozenset({CAT_CHATTER}),
    SPAWNING:      frozenset({CAT_LIFECYCLE, CAT_COMBAT_KILL}),       # 放 spawn/death + 明确归属击杀；安全事件仍受 grace 抑制
    IN_FLIGHT:     frozenset({CAT_LIFECYCLE, CAT_SAFETY_CRITICAL, CAT_SAFETY_IMPORTANT, CAT_SAFETY_MINOR, CAT_COMBAT_KILL, CAT_MAP_AWARENESS, CAT_PLAYER_COMMAND, CAT_CHATTER}),
    COMBAT_STRESS: frozenset({CAT_LIFECYCLE, CAT_SAFETY_CRITICAL, CAT_SAFETY_IMPORTANT, CAT_COMBAT_KILL, CAT_MAP_AWARENESS, CAT_PLAYER_COMMAND}),
    CRITICAL_RISK: frozenset({CAT_LIFECYCLE, CAT_SAFETY_CRITICAL}),  # 只放危急本身 + 死亡
    DEAD:          frozenset({CAT_LIFECYCLE, CAT_CHATTER}),
    BATTLE_ENDED:  frozenset({CAT_LIFECYCLE, CAT_CHATTER}),
}


def category_allowed(scenario: str, category: str) -> bool:
    return category in SCENARIO_GATING.get(scenario, frozenset())


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def _clamp(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _dialogue_intrusion_mode(value: Any) -> str:
    mode = str(value or "").strip()
    aliases = {
        "avoid_interrupt": "no_interrupt",
        "protect_chat": "critical_only",
        "balanced": "critical_only",
        "immediate": "allow_interrupt",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in {"no_interrupt", "critical_only", "allow_interrupt"} else "critical_only"


@dataclass
class WtConfig:
    enabled: bool = True
    dry_run: bool = True
    data_layer_url: str = "http://127.0.0.1:8112"
    data_layer_auto_start: bool = True
    data_layer_startup_timeout_seconds: float = 3.0
    data_layer_shutdown_timeout_seconds: float = 3.0
    poll_interval_seconds: float = 0.4
    http_timeout_seconds: float = 1.5
    global_rate_limit_seconds: float = 12.0
    critical_preempt_cooldown_seconds: float = 5.0
    output_backpressure_seconds: float = 20.0
    output_event_max_age_seconds: float = 8.0
    dialogue_intrusion_mode: str = "critical_only"
    user_chat_quiet_window_seconds: float = 60.0
    battle_output_quiet_window_seconds: float = 30.0
    kill_coalesce_window_seconds: float = 6.0
    spawn_grace_seconds: float = 6.0
    takeoff_low_alt_grace_seconds: float = 45.0
    takeoff_radio_altitude_enter_m: float = 10.0
    takeoff_radio_altitude_exit_m: float = 40.0
    queue_limit: int = 5
    safety_auto_stop_enabled: bool = True
    safety_window_seconds: float = 60.0
    safety_failure_limit: int = 5
    player_name: str = ""
    target_lanlan: str = ""
    plugin_reply_hint_enabled: bool = True
    plugin_owned_battle_output_enabled: bool = False
    plugin_owned_urgent_output_enabled: bool = True
    plugin_owned_blind_output_enabled: bool = False
    observability_enabled: bool = False
    observability_max_events: int = 100
    observability_include_prompt_preview: bool = False
    v2_live_verified_real_output_enabled: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "WtConfig":
        raw = dict(data or {})
        return cls(
            enabled=bool(raw.get("enabled", True)),
            dry_run=bool(raw.get("dry_run", True)),
            data_layer_url=str(raw.get("data_layer_url") or "http://127.0.0.1:8112").rstrip("/"),
            data_layer_auto_start=bool(raw.get("data_layer_auto_start", True)),
            data_layer_startup_timeout_seconds=_clamp(raw.get("data_layer_startup_timeout_seconds"), 3.0, 0.0, 30.0),
            data_layer_shutdown_timeout_seconds=_clamp(raw.get("data_layer_shutdown_timeout_seconds"), 3.0, 0.1, 30.0),
            poll_interval_seconds=_clamp(raw.get("poll_interval_seconds"), 0.4, 0.05, 5.0),
            http_timeout_seconds=_clamp(raw.get("http_timeout_seconds"), 1.5, 0.2, 10.0),
            global_rate_limit_seconds=_clamp(raw.get("global_rate_limit_seconds"), 12.0, 0.0, 600.0),
            critical_preempt_cooldown_seconds=_clamp(raw.get("critical_preempt_cooldown_seconds"), 5.0, 0.0, 120.0),
            output_backpressure_seconds=_clamp(raw.get("output_backpressure_seconds"), 20.0, 0.0, 300.0),
            output_event_max_age_seconds=_clamp(raw.get("output_event_max_age_seconds"), 8.0, 0.0, 120.0),
            dialogue_intrusion_mode=_dialogue_intrusion_mode(raw.get("dialogue_intrusion_mode")),
            user_chat_quiet_window_seconds=_clamp(raw.get("user_chat_quiet_window_seconds"), 60.0, 0.0, 300.0),
            battle_output_quiet_window_seconds=_clamp(raw.get("battle_output_quiet_window_seconds"), 30.0, 0.0, 300.0),
            kill_coalesce_window_seconds=_clamp(raw.get("kill_coalesce_window_seconds"), 6.0, 0.0, 30.0),
            spawn_grace_seconds=_clamp(raw.get("spawn_grace_seconds"), 6.0, 0.0, 60.0),
            takeoff_low_alt_grace_seconds=_clamp(raw.get("takeoff_low_alt_grace_seconds"), 45.0, 0.0, 120.0),
            takeoff_radio_altitude_enter_m=_clamp(raw.get("takeoff_radio_altitude_enter_m"), 10.0, 0.0, 100.0),
            takeoff_radio_altitude_exit_m=_clamp(raw.get("takeoff_radio_altitude_exit_m"), 40.0, 0.0, 300.0),
            queue_limit=int(_clamp(raw.get("queue_limit"), 5, 1, 100)),
            safety_auto_stop_enabled=bool(raw.get("safety_auto_stop_enabled", True)),
            safety_window_seconds=_clamp(raw.get("safety_window_seconds"), 60.0, 5.0, 3600.0),
            safety_failure_limit=int(_clamp(raw.get("safety_failure_limit"), 5, 1, 100)),
            player_name=str(raw.get("player_name") or "").strip(),
            target_lanlan=str(raw.get("target_lanlan") or raw.get("lanlan_name") or "").strip(),
            plugin_reply_hint_enabled=bool(raw.get("plugin_reply_hint_enabled", True)),
            plugin_owned_battle_output_enabled=bool(raw.get("plugin_owned_battle_output_enabled", False)),
            plugin_owned_urgent_output_enabled=bool(raw.get("plugin_owned_urgent_output_enabled", True)),
            plugin_owned_blind_output_enabled=bool(raw.get("plugin_owned_blind_output_enabled", False)),
            observability_enabled=bool(raw.get("observability_enabled", False)),
            observability_max_events=int(_clamp(raw.get("observability_max_events"), 100, 1, 1000)),
            observability_include_prompt_preview=bool(raw.get("observability_include_prompt_preview", False)),
            v2_live_verified_real_output_enabled=bool(raw.get("v2_live_verified_real_output_enabled", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# BattleState（一帧 /api/telemetry 的归一化只读视图 + 派生）
# ---------------------------------------------------------------------------


@dataclass
class BattleState:
    # 可用性 / 元
    connected: bool = False                 # /api/telemetry 是否拉到
    conn_state: str = "offline"             # offline / not_in_battle / in_battle（数据层 state）
    in_battle: bool = False
    replay: bool = False                    # data-layer replay degrade mode; suppress real battle events
    dead: bool = False                      # data-layer dead/spectating hold; data layer suppresses flags while true
    vehicle_valid: bool = False             # vehicle.valid：在战且有载具遥测=存活（出生/死亡判定用）
    indicators_valid: bool = False          # /indicators valid; ground/naval may not have vehicle telemetry
    has_player: bool = False                # map situation found the player marker
    domain: str = "unknown"                 # air / heli / ground / naval / menu / unknown
    domain_label: str | None = None
    vehicle_type: str | None = None
    profile_matched: bool | None = None
    profile_source: str | None = None
    profile_family: str | None = None
    timestamp: float = 0.0                  # 数据层 fast 帧时间戳
    age_seconds: float | None = None        # 距数据层最近更新

    # 告警 flags（数据层 processed.flags：code -> bool）
    flags: dict[str, bool] = field(default_factory=dict)
    level: str = "info"                     # info / warning / critical

    # 飞行派生量（数据层 processed/* 直供，仅作 payload 上下文）
    ias_kmh: float | None = None
    aoa_deg: float | None = None
    altitude_m: float | None = None
    radio_altitude_m: float | None = None
    climb_ms: float | None = None
    mach: float | None = None
    g_now: float | None = None
    fuel_fraction: float | None = None
    fuel_remaining_sec: float | None = None
    water_temp_c: float | None = None
    head_temp_c: float | None = None
    turbine_temp_c: float | None = None
    oil_temp_c: float | None = None
    crew_current: float | None = None
    crew_total: float | None = None
    ammo_first_stage: float | None = None
    gun_stabilizer: bool | None = None
    gear_position: int | None = None
    gunner_state: float | None = None
    driver_state: float | None = None

    # 离散来源
    hud_events: list[dict[str, Any]] = field(default_factory=list)
    chat: list[dict[str, Any]] = field(default_factory=list)
    hud_notices: list[dict[str, Any]] = field(default_factory=list)
    combat: dict[str, Any] = field(default_factory=dict)
    proximity: dict[str, Any] = field(default_factory=dict)
    proximity_events: list[dict[str, Any]] = field(default_factory=list)
    situation: dict[str, Any] = field(default_factory=dict)
    mission_status: str | None = None

    # 派生（本插件算）
    scenario: str = OUT_OF_BATTLE

    # 原始帧（兜底/调试，不直接喂 LLM）
    raw: dict[str, Any] = field(default_factory=dict)

    def flag(self, code: str) -> bool:
        return bool(self.flags.get(code))

    def any_critical_flag(self) -> bool:
        """危急集合对应的数据层 critical 级 flag 是否激活（驱动 CRITICAL_RISK）。"""
        return any(self.flag(code) for code in CRITICAL_FLAG_CODES)

    def is_alive(self) -> bool:
        if not self.in_battle or self.dead:
            return False
        if self.vehicle_valid:
            return True
        domain = (self.domain or "").lower()
        if domain in {"ground", "naval"} and self.indicators_valid:
            return bool(self.has_player or self.vehicle_type)
        return False


@dataclass
class BattleEvent:
    """理解层唯一对外语义事件（D-B2）。"""

    event_id: str
    edge: str = "enter"                     # enter / recovery
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    level: str = "warning"                  # warning / critical（来自数据层 flag 档）

    @property
    def spec(self) -> EventSpec:
        return EVENT_CATALOG[self.event_id]

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def priority(self) -> int:
        return self.spec.priority

    @property
    def severity(self) -> int:
        return self.spec.severity_for(self.level)

    @property
    def preempt_eligible(self) -> bool:
        return self.event_id in PREEMPT_ELIGIBLE_IDS and self.level == "critical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "edge": self.edge,
            "level": self.level,
            "category": self.category,
            "priority": self.priority,
            "severity": self.severity,
            "preempt": self.preempt_eligible,
            "payload": dict(self.payload),
            "ts": self.ts,
        }
