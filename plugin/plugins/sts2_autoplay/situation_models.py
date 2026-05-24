from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class SituationSnapshotSummary:
    screen: str = "unknown"
    summary_kind: str = "general"
    floor: int | None = None
    act: int | None = None
    turn: int | None = None
    in_combat: bool = False
    player: dict[str, Any] = field(default_factory=dict)
    hand: dict[str, Any] = field(default_factory=dict)
    enemies: dict[str, Any] = field(default_factory=dict)
    available_actions: list[str] = field(default_factory=list)
    captured_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "screen": self.screen,
            "summary_kind": self.summary_kind,
            "floor": self.floor,
            "act": self.act,
            "turn": self.turn,
            "in_combat": self.in_combat,
            "player": dict(self.player),
            "hand": dict(self.hand),
            "enemies": dict(self.enemies),
            "available_actions": list(self.available_actions),
            "captured_at": self.captured_at,
        }


@dataclass(slots=True)
class SituationDelta:
    source: str = "continuous_snapshot"
    screen_change: dict[str, Any] = field(default_factory=dict)
    player_changes: dict[str, Any] = field(default_factory=dict)
    enemy_changes: dict[str, Any] = field(default_factory=dict)
    hand_changes: dict[str, Any] = field(default_factory=dict)
    notable_events: list[str] = field(default_factory=list)
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "screen_change": dict(self.screen_change),
            "player_changes": dict(self.player_changes),
            "enemy_changes": dict(self.enemy_changes),
            "hand_changes": dict(self.hand_changes),
            "notable_events": list(self.notable_events),
            "text": self.text,
        }


@dataclass(slots=True)
class SituationFrame:
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, Any] = field(default_factory=dict)
    action_type: str = ""
    action_kwargs: dict[str, Any] = field(default_factory=dict)
    decision_source: str = ""
    decision_reason: str = ""
    step_count: int = 0
    created_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "before": dict(self.before),
            "after": dict(self.after),
            "delta": dict(self.delta),
            "action_type": self.action_type,
            "action_kwargs": dict(self.action_kwargs),
            "decision_source": self.decision_source,
            "decision_reason": self.decision_reason,
            "step_count": self.step_count,
            "created_at": self.created_at,
        }


__all__ = [
    "SituationSnapshotSummary",
    "SituationDelta",
    "SituationFrame",
]
