from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class PlannedOperation:
    action_type: str
    kwargs: dict[str, Any]
    confidence: float
    source: str
    reason: str = ""
    action_id: str = ""
    category: str = "unknown"
    candidate_actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "kwargs": dict(self.kwargs),
            "confidence": float(self.confidence),
            "source": self.source,
            "reason": self.reason,
            "action_id": self.action_id,
            "category": self.category,
            "candidate_actions": list(self.candidate_actions),
        }


class PlannerContext(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...


class Planner(Protocol):
    def plan(self, context: PlannerContext) -> PlannedOperation | None: ...


class NullPlanner:
    def plan(self, context: PlannerContext) -> PlannedOperation | None:
        return None


__all__ = ["PlannedOperation", "PlannerContext", "Planner", "NullPlanner"]
