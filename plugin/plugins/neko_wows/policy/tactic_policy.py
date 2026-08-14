"""Expands detected events into advice candidates.

The candidate is the last deterministic step: it carries the lane, the ranking
inputs, the facts to quote, and the explicit constraints on what may be said.
Only the final wording is left to the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.catalog import (
    AMMO_RECHECK_HINT,
    BATTLE_ENDED,
    BATTLE_STARTED,
    DEVASTATING_STRIKE,
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

# Constraints attached per event so the prompt cannot drift into claims the
# telemetry does not support. Keyed by event id.
#
# Only what is specific to this event belongs here. Rules that hold for the whole
# battle -- consumable state, relative sectors, how to read the count fields --
# are injected once with the scene instead. Repeating them on every call-out made
# them the bulk of the message, and the model started reciting them as content.
_CLAIM_LIMITS: dict[str, tuple[str, ...]] = {
    HIGH_DAMAGE: (
        "只能说同一目标在短时间内承受了较高伤害；不能说成一发、单轮齐射、特定弹种或击杀。",
    ),
    DEVASTATING_STRIKE: (
        "只能说达到毁灭打击级别；不能说成一发或单轮齐射，不能声称游戏已授予毁灭打击成就、勋带或奖章，也不能虚构武器来源。",
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
        "有伤害数字就可以用自己的语气点评一下，没有就别编战果。",
        "不要描述小地图、点亮数、方位或还在视野里的船。",
        "不要沿用上一次发言。",
    ),
    BATTLE_STARTED: (
        "用陪玩语气打招呼，可以起哄、打气，地图和自己的船能带就带；不要念成“战斗开始，在某图玩某船”。",
        "不要描述小地图、点亮数、方位、距离或还在视野里的船。",
        "不要沿用上一次发言。",
    ),
    BATTLE_ENDED: (
        "用陪玩语气收束，可以松口气或调侃，不必只报“对局结束”。",
        "不要描述小地图、点亮数、方位、距离或还在视野里的船。",
        "不要沿用上一次发言。",
    ),
}


def _cooldown_identity(detail: Mapping[str, Any]) -> str | None:
    """Stable per-target suffix so two ships do not share one cooldown slot."""
    for key in ("victim_id", "player_id", "ui_id", "target_id"):
        value = detail.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            continue
        if isinstance(value, int) and value < 0:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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

    @property
    def cooldown_key(self) -> str:
        identity = _cooldown_identity(self.detail)
        if identity is None:
            return self.event_id
        return f"{self.event_id}:{identity}"

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
            context = (
                self._shared_context(facts) if spec.include_frame_context else {}
            )
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
                context=context,
                claim_limits=_CLAIM_LIMITS.get(event.event_id, ()),
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
