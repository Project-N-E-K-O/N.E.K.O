"""Expands detected events into advice candidates.

The candidate is the last deterministic step: it carries the lane, the ranking
inputs, the facts to quote, and the explicit constraints on what may be said.
Only the final wording is left to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.catalog import (
    AMMO_RECHECK_HINT,
    DEVASTATING_STRIKE,
    ENEMY_SUNK,
    EventSpec,
    HIGH_DAMAGE,
    MULTI_DIRECTION_THREAT,
    OUTNUMBERED,
    OWN_BROADSIDE_EXPOSED,
    POST_BATTLE_SUMMARY,
    RAPID_DAMAGE,
    TARGET_BROADSIDE_WINDOW,
    spec_for,
)
from ..domain.facts import WowsFacts
from ..detectors._base import GameEvent

# Always attached: consumable active-state and relative sectors are not in
# live telemetry, and ship-catalog Radar text must not become a call-out.
_GLOBAL_CLAIM_LIMITS: tuple[str, ...] = (
    "消耗品实时状态（雷达、水听、烟幕、损伤控制等是否开启或持续）当前不可用，不要提及；"
    "不要把小地图上敌舰被点亮说成对方开了雷达。",
    "没有给出相对方位字段时，不要说左前方、正前方、右前方。",
)

# Constraints attached per event so the prompt cannot drift into claims the
# telemetry does not support. Keyed by event id.
_CLAIM_LIMITS: dict[str, tuple[str, ...]] = {
    HIGH_DAMAGE: (
        "只能说同一目标在短时间内承受了较高伤害；不能说成一发、单轮齐射、特定弹种或击杀。",
    ),
    DEVASTATING_STRIKE: (
        "只能说达到毁灭打击级别；不能说成一发或单轮齐射，不能声称游戏已授予毁灭打击成就、勋带或奖章，也不能虚构武器来源。",
    ),
    ENEMY_SUNK: (
        "这是夸奖事件：用一两句高兴或佩服的话祝贺击沉，不要改成战术警告或复盘。",
        "可以说这艘敌舰已经沉了；遥测没有击杀归属，不要说拿到击杀数、抢人头，也不要声称游戏发了击杀勋带或成就。",
    ),
    RAPID_DAMAGE: (
        "只能说“掉血很快 / 正在快速受伤”，不能说“被集火”或推断攻击者数量。",
    ),
    OUTNUMBERED: (
        "只按事件中的 confirmed_visible_*（当前点亮且明确存活）描述视野内人数劣势；"
        "不要把未确认沉没上限说成存活数，也不能据此说团灭或全灭。",
    ),
    MULTI_DIRECTION_THREAT: (
        "只能说“威胁来自多个方向”，不能断言交叉火力或敌方在配合。",
    ),
    OWN_BROADSIDE_EXPOSED: (
        "这是基于航向与方位的几何判断，不能断言已经被瞄准或即将被命中。",
    ),
    TARGET_BROADSIDE_WINDOW: (
        "只能说对方当前航向偏向侧面，不能断言一定能打穿。",
    ),
    AMMO_RECHECK_HINT: (
        "只能提示“顺手确认一下弹种”，不能给出确定的换弹结论，也不能提装填状态。",
    ),
    POST_BATTLE_SUMMARY: (
        "击杀归属、占点与鱼雷数据当前不可用，不要提及具体击杀数。",
    ),
}


@dataclass(frozen=True)
class AdviceCandidate:
    """One rankable, expirable thing the companion could say."""

    event_id: str
    lane: str
    priority: int
    severity: int
    at: float
    seq: int
    battle_id: str | None
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    claim_limits: tuple[str, ...] = ()
    expires_at: float = 0.0

    @property
    def spec(self) -> EventSpec:
        return spec_for(self.event_id)

    @property
    def coalesce_key(self) -> str:
        return self.spec.coalesce_key

    def is_expired(self, now: float) -> bool:
        return self.expires_at > 0.0 and now >= self.expires_at

    # Fixed ordering: priority, then severity, then age, then id. The event id
    # tiebreak is what makes the whole chain reproducible in a replay.
    @property
    def rank(self) -> tuple[int, int, float, str]:
        return (-self.priority, -self.severity, self.at, self.event_id)


class WowsTacticPolicy:
    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def apply_config(self, cfg) -> None:
        self.cfg = cfg

    def expand(
        self,
        events: Sequence[GameEvent],
        facts: WowsFacts,
    ) -> tuple[AdviceCandidate, ...]:
        candidates: list[AdviceCandidate] = []
        for event in events:
            spec = event.spec
            # Broadcast preferences are applied here rather than at delivery, so
            # a disabled category never occupies the queue or a cooldown slot.
            if not self.cfg.lane_enabled(spec.lane):
                continue
            if not self.cfg.category_enabled(spec.coalesce_key):
                continue
            ttl = spec.ttl_seconds or self.cfg.ttl_for(spec.lane)
            per_event = _CLAIM_LIMITS.get(event.event_id, ())
            candidates.append(AdviceCandidate(
                event_id=event.event_id,
                lane=spec.lane,
                priority=spec.priority,
                severity=event.severity,
                at=event.at,
                seq=event.seq,
                battle_id=event.battle_id,
                summary=spec.summary,
                detail=dict(event.detail),
                context=self._shared_context(facts),
                claim_limits=_GLOBAL_CLAIM_LIMITS + per_event,
                expires_at=event.at + ttl,
            ))
        candidates.sort(key=lambda c: c.rank)
        return tuple(candidates)

    @staticmethod
    def _shared_context(facts: WowsFacts) -> dict[str, Any]:
        """Frame-level background every call-out may reference."""
        nearest = facts.nearest_enemy
        return {
            "own_hp_ratio": round(facts.own_hp_ratio, 3) if facts.own_hp_ratio else None,
            "visible_enemies": facts.visible_enemies,
            "confirmed_visible_allies": facts.confirmed_visible_allies,
            "confirmed_visible_enemies": facts.confirmed_visible_enemies,
            # Only meaningful once objects-domain lit counts exist; a bare
            # false would otherwise clutter every out-of-battle / no-objects
            # call-out.
            "team_counts_confirmed": (
                facts.team_counts_confirmed
                if facts.confirmed_visible_allies is not None
                else None
            ),
            "nearest_enemy_m": round(nearest.distance_m) if nearest else None,
            "damage_inflicted": round(facts.damage_inflicted) if facts.damage_inflicted else None,
            "sourced_domains": list(facts.sourced_domains),
        }


__all__ = ["AdviceCandidate", "WowsTacticPolicy"]
