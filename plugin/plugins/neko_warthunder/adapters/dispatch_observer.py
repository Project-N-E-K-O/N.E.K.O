"""Structured observability for the dispatcher boundary."""

from __future__ import annotations

from typing import Any

from ..core.contracts import BattleEvent
from .runtime_timeline import RuntimeTimeline


class DispatchObserver:
    """Record dispatcher event stages without coupling delivery policy to timeline fields."""

    def __init__(self, timeline: RuntimeTimeline | None) -> None:
        self.timeline = timeline

    def record_event(
        self,
        event: BattleEvent,
        *,
        stage: str,
        outcome: str,
        reason: str,
        dry_run: bool,
        ai_behavior: str = "respond",
        pushed: bool = False,
        **metadata: Any,
    ) -> None:
        if self.timeline is None:
            return
        details: dict[str, Any] = {
            "event_id": event.event_id,
            "edge": event.edge,
            "level": event.level,
            "priority": event.priority,
            "dry_run": dry_run,
            "kind": "event",
            "ai_behavior": ai_behavior,
            "pushed": pushed,
            "safe_summary": f"{event.event_id}/{event.edge}/{event.level}",
        }
        details.update(metadata)
        self.timeline.record_stage(
            stage=stage,
            outcome=outcome,
            reason=reason,
            **details,
        )
