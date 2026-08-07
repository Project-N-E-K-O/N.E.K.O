"""Bounded ring buffer of what the pipeline did, for the panel.

Every stage records here, including the ones that produced nothing. A call-out
that the host declined to release looks identical to a bug unless the timeline
says which stage stopped it and why.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

STAGE_FRAME = "frame"
STAGE_DETECT = "detect"
STAGE_ARBITER = "arbiter"
STAGE_DELIVERY = "delivery"
STAGE_SERVICE = "service"
STAGE_DOCUMENTS = "documents"
STAGE_PROMPTS = "prompts"
STAGE_SHIP_CATALOG = "ship_catalog"


@dataclass(frozen=True)
class TimelineRecord:
    at: float
    stage: str
    outcome: str
    seq: int | None = None
    battle_id: str | None = None
    event_id: str = ""
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": round(self.at, 3),
            "stage": self.stage,
            "outcome": self.outcome,
            "seq": self.seq,
            "battle_id": self.battle_id,
            "event_id": self.event_id,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


class RuntimeTimeline:
    def __init__(self, max_events: int = 120) -> None:
        self._lock = threading.RLock()
        self._records: deque[TimelineRecord] = deque(maxlen=max(10, int(max_events)))

    def resize(self, max_events: int) -> None:
        with self._lock:
            existing = list(self._records)
            self._records = deque(existing, maxlen=max(10, int(max_events)))

    def record(
        self,
        stage: str,
        outcome: str,
        *,
        seq: int | None = None,
        battle_id: str | None = None,
        event_id: str = "",
        reason: str = "",
        detail: dict[str, Any] | None = None,
        at: float | None = None,
    ) -> None:
        record = TimelineRecord(
            at=time.time() if at is None else at,
            stage=stage,
            outcome=outcome,
            seq=seq,
            battle_id=battle_id,
            event_id=event_id,
            reason=reason,
            detail=dict(detail or {}),
        )
        with self._lock:
            self._records.append(record)

    def recent(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records)
        return [record.as_dict() for record in records[-max(1, limit):]][::-1]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


__all__ = [
    "STAGE_ARBITER",
    "STAGE_DELIVERY",
    "STAGE_DETECT",
    "STAGE_DOCUMENTS",
    "STAGE_FRAME",
    "STAGE_PROMPTS",
    "STAGE_SERVICE",
    "STAGE_SHIP_CATALOG",
    "RuntimeTimeline",
    "TimelineRecord",
]
