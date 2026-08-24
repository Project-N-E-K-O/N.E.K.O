"""Lightweight scenario resolver.

The resolver only provides output gating context. It consumes data-layer facts
and safe summaries; it does not parse raw combat text or infer battle outcomes.
"""

from __future__ import annotations

from .contracts import (
    BATTLE_ENDED,
    COMBAT_STRESS,
    CRITICAL_RISK,
    DEAD,
    IN_FLIGHT,
    OUT_OF_BATTLE,
    SPAWNING,
    BattleState,
    is_battle_end_status,
)

_STRESS_G_THRESHOLD = 5.0
_AIR_STRESS_WINDOW_SECONDS = 8.0
_GROUND_CONTACT_WINDOW_SECONDS = 10.0
_NAVAL_CONTACT_WINDOW_SECONDS = 20.0
_GROUND_CONTACT_FALLBACK_M = 800.0
_NAVAL_CONTACT_FALLBACK_M = 2500.0
_AIR_CONTACT_FALLBACK_M = 5000.0
_DOMAIN_STRESS_REASONS = frozenset({"maneuver", "air_contact", "surface_contact"})


class ScenarioResolver:
    def __init__(self) -> None:
        self._prev_alive: bool = False
        self._spawn_at: float = 0.0
        self._stress_until: float = 0.0
        self._stress_reason_until: dict[str, float] = {}
        self._last_hud_id: int = -1
        self._stress_domain: str | None = None

    def reset(self) -> None:
        self._prev_alive = False
        self._spawn_at = 0.0
        self._stress_until = 0.0
        self._stress_reason_until.clear()
        self._last_hud_id = -1
        self._stress_domain = None

    def resolve(self, state: BattleState, now: float, grace_seconds: float) -> str:
        scenario = self._classify(state, now, grace_seconds)
        self._prev_alive = self._is_alive(state)
        return scenario

    def seconds_since_spawn(self, now: float) -> float | None:
        if self._spawn_at <= 0:
            return None
        return max(0.0, now - self._spawn_at)

    def current_stress_reasons(self, now: float | None = None) -> frozenset[str]:
        if now is not None:
            self._expire_stress_reasons(now)
        return frozenset(self._stress_reason_until)

    def _classify(self, state: BattleState, now: float, grace_seconds: float) -> str:
        if not state.connected or state.conn_state == "offline":
            self._clear_runtime_stress()
            return OUT_OF_BATTLE

        if is_battle_end_status(state.mission_status):
            return BATTLE_ENDED

        if not state.in_battle:
            self._clear_runtime_stress()
            return OUT_OF_BATTLE

        domain = _normalize_stress_domain(state.domain)
        self._handle_domain_change(domain)

        if state.dead:
            return DEAD

        alive = self._is_alive(state)
        if not alive:
            return DEAD if self._prev_alive else SPAWNING

        if not self._prev_alive:
            self._spawn_at = now
        if now - self._spawn_at < grace_seconds:
            return SPAWNING

        if state.any_critical_flag():
            return CRITICAL_RISK

        if self._combat_stress(state, now):
            return COMBAT_STRESS

        return IN_FLIGHT

    def _is_alive(self, state: BattleState) -> bool:
        return state.is_alive()

    def _clear_runtime_stress(self) -> None:
        self._stress_until = 0.0
        self._stress_reason_until.clear()
        self._last_hud_id = -1
        self._stress_domain = None

    def _combat_stress(self, state: BattleState, now: float) -> bool:
        domain = _normalize_stress_domain(state.domain)
        if domain == "air":
            if state.g_now is not None and abs(state.g_now) >= _STRESS_G_THRESHOLD:
                self._extend_stress("maneuver", now, _AIR_STRESS_WINDOW_SECONDS)
            if _has_close_air_contact(state):
                self._extend_stress("air_contact", now, _AIR_STRESS_WINDOW_SECONDS)
        elif domain == "ground":
            if _has_close_surface_contact(state, _GROUND_CONTACT_FALLBACK_M):
                self._extend_stress("surface_contact", now, _GROUND_CONTACT_WINDOW_SECONDS)
        elif domain == "naval":
            if _has_close_surface_contact(state, _NAVAL_CONTACT_FALLBACK_M):
                self._extend_stress("surface_contact", now, _NAVAL_CONTACT_WINDOW_SECONDS)

        # 交火压力只认可归属到本机的战斗条目。
        # state.hud_events 是未过滤的全场战斗日志（数据层原样 append 全部 HUD 消息），
        # 多人对局里击杀榜近乎连续，按它计压力会让玩家独自巡航时也长期停在
        # COMBAT_STRESS，进而压掉 low_fuel 与陪伴类输出。combat.feed 是同一条 damage
        # 流的已解析版本，且带 is_my_kill / is_my_death 归属标记。
        max_dmg_id: int | None = None
        for item in _owned_combat_feed_items(state):
            try:
                eid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if max_dmg_id is None or eid > max_dmg_id:
                max_dmg_id = eid
        if max_dmg_id is not None:
            if max_dmg_id < self._last_hud_id:
                self._last_hud_id = -1
            if max_dmg_id > self._last_hud_id:
                self._extend_stress("damage", now, _damage_stress_window_seconds(domain))
                self._last_hud_id = max_dmg_id

        self._expire_stress_reasons(now)
        return now < self._stress_until

    def _handle_domain_change(self, domain: str) -> None:
        if not domain:
            return
        if self._stress_domain is not None and domain != self._stress_domain:
            for reason in _DOMAIN_STRESS_REASONS:
                self._stress_reason_until.pop(reason, None)
            self._stress_until = max(self._stress_reason_until.values(), default=0.0)
        self._stress_domain = domain

    def _extend_stress(self, reason: str, now: float, seconds: float) -> None:
        until = now + seconds
        self._stress_until = max(self._stress_until, until)
        self._stress_reason_until[reason] = max(self._stress_reason_until.get(reason, 0.0), until)

    def _expire_stress_reasons(self, now: float) -> None:
        expired = [reason for reason, until in self._stress_reason_until.items() if now >= until]
        for reason in expired:
            self._stress_reason_until.pop(reason, None)


def _owned_combat_feed_items(state: BattleState):
    """combat.feed 中归属本机的条目（致命与非致命交火）。

    身份未确定时数据层不会置 involves_me，此时不产生 damage 压力——宁可少压制，
    也不要把全场活跃度误判成本机在交火。
    """
    feed = state.combat.get("feed") if isinstance(state.combat, dict) else None
    if not isinstance(feed, list):
        return []
    return [
        item
        for item in feed
        if isinstance(item, dict)
        and (
            item.get("involves_me") is True
            or item.get("is_my_kill") is True
            or item.get("is_my_death") is True
        )
    ]


def _damage_stress_window_seconds(domain: str) -> float:
    if domain == "ground":
        return _GROUND_CONTACT_WINDOW_SECONDS
    if domain == "naval":
        return _NAVAL_CONTACT_WINDOW_SECONDS
    return _AIR_STRESS_WINDOW_SECONDS


def _normalize_stress_domain(domain: str | None) -> str:
    normalized = (domain or "").lower()
    if normalized == "heli":
        return "air"
    return normalized if normalized in {"air", "ground", "naval"} else ""


def _has_close_air_contact(state: BattleState) -> bool:
    situation = state.situation if isinstance(state.situation, dict) else {}
    nearest = situation.get("nearest_air_threat")
    if not isinstance(nearest, dict):
        nearest = _nearest_enemy_of_type(situation, "aircraft")
    distance = _distance_m(nearest)
    if distance is None:
        return False
    threshold = _proximity_threshold(state, "vs_air") or _AIR_CONTACT_FALLBACK_M
    return distance <= threshold


def _has_close_surface_contact(state: BattleState, fallback_m: float) -> bool:
    situation = state.situation if isinstance(state.situation, dict) else {}
    nearest = _nearest_surface_enemy(situation)
    distance = _distance_m(nearest)
    if distance is None:
        return False
    threshold = _proximity_threshold(state, "vs_ground") or fallback_m
    return distance <= threshold


def _nearest_surface_enemy(situation: dict) -> dict | None:
    enemies = situation.get("enemies")
    if not isinstance(enemies, list):
        nearest = situation.get("nearest_enemy")
        return nearest if isinstance(nearest, dict) and nearest.get("type") != "aircraft" else None
    for item in enemies:
        if isinstance(item, dict) and item.get("type") != "aircraft":
            return item
    return None


def _nearest_enemy_of_type(situation: dict, enemy_type: str) -> dict | None:
    enemies = situation.get("enemies")
    if not isinstance(enemies, list):
        return None
    for item in enemies:
        if isinstance(item, dict) and item.get("type") == enemy_type:
            return item
    return None


def _distance_m(item: dict | None) -> float | None:
    if not isinstance(item, dict):
        return None
    try:
        value = float(item.get("distance_m"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _proximity_threshold(state: BattleState, key: str) -> float | None:
    proximity = state.proximity if isinstance(state.proximity, dict) else {}
    thresholds = proximity.get("thresholds_m")
    if not isinstance(thresholds, dict):
        return None
    try:
        value = float(thresholds.get(key))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
