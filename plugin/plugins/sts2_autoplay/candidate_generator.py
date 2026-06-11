from __future__ import annotations

from typing import Any

from .decision_payload import DecisionCandidate
from .planner_interface import PlannedOperation


class STS2CandidateGenerator:
    def __init__(self, planner: Any | None = None) -> None:
        self._planner = planner

    def generate(self, context: dict[str, Any], *, mode: str, limit: int = 5) -> list[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        action_registry = snapshot.get("action_registry") if isinstance(snapshot.get("action_registry"), list) else []
        candidates: list[DecisionCandidate] = []

        planned = self._planner.plan(context) if self._planner is not None else None
        if isinstance(planned, PlannedOperation):
            candidates.append(self._from_planned(planned, priority=max(float(planned.confidence or 0.0), 0.9)))

        for index, action in enumerate(action_registry):
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "")
            action_type = str(action.get("type") or "")
            if not action_id or not action_type:
                continue
            if any(existing.action_id == action_id for existing in candidates):
                continue
            reasons = [f"registry_rank_{index}"]
            category = str(action.get("category") or "")
            if category:
                reasons.append(f"category:{category}")
            default_kwargs = dict(action.get("default_kwargs") if isinstance(action.get("default_kwargs"), dict) else {})
            priority = max(0.0, 0.8 - index * 0.08)
            candidates.append(
                DecisionCandidate(
                    action_id=action_id,
                    action_type=action_type,
                    kwargs=default_kwargs,
                    priority=priority,
                    source="registry_candidate",
                    reasons=reasons,
                )
            )
            if len(candidates) >= limit:
                break

        if not candidates:
            for index, action in enumerate(snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []):
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("type") or "")
                if not action_type:
                    continue
                candidates.append(
                    DecisionCandidate(
                        action_id=f"raw:{action_type}:{index}",
                        action_type=action_type,
                        kwargs={},
                        priority=max(0.0, 0.4 - index * 0.05),
                        source="raw_fallback_candidate",
                        reasons=["raw_available_action"],
                    )
                )
                if len(candidates) >= limit:
                    break

        return [candidate.as_dict() for candidate in candidates[:limit]]

    def _from_planned(self, planned: PlannedOperation, *, priority: float) -> DecisionCandidate:
        return DecisionCandidate(
            action_id=str(planned.action_id or planned.action_type),
            action_type=planned.action_type,
            kwargs=dict(planned.kwargs),
            priority=priority,
            source=str(planned.source or "planned_candidate"),
            reasons=[str(planned.reason or "planned")],
        )


__all__ = ["STS2CandidateGenerator"]
