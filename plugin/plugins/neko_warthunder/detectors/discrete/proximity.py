"""Discrete detectors for data-layer proximity edge events.

The data layer already performs map tracking and emits ``proximity.events`` as
edge-triggered facts. The plugin only consumes those facts, deduplicates by
event id, and promotes safe metadata into low-priority awareness events.
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector


_BEHIND_CLOCKS = {5, 6, 7}


class ProximityDetector(DiscreteDetector):
    id = "proximity"

    def __init__(
        self,
        *,
        tail_window_seconds: float = 8.0,
        tail_confirm_events: int = 2,
        tail_distance_m: float = 900.0,
    ) -> None:
        self._last_id: int = -1
        self._tail_window_seconds = max(1.0, float(tail_window_seconds))
        self._tail_confirm_events = max(2, int(tail_confirm_events))
        self._tail_distance_m = max(100.0, float(tail_distance_m))
        self._tail_hits: list[tuple[float, int]] = []

    def reset(self) -> None:
        self._last_id = -1
        self._tail_hits.clear()

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            self._tail_hits.clear()
            return None

        events = [item for item in cur.proximity_events if isinstance(item, dict)]
        ids = [_event_id(item) for item in events]
        ids = [eid for eid in ids if eid is not None]
        if not ids:
            return None
        max_id = max(ids)
        if max_id < self._last_id:
            self._last_id = -1
            self._tail_hits.clear()

        newest: dict[str, Any] | None = None
        newest_rank: tuple[int, int] = (-1, -1)
        for item in events:
            eid = _event_id(item)
            if eid is None or eid <= self._last_id:
                continue
            event_id = _awareness_event_id(item, cur.domain)
            rank = (_event_priority(event_id), eid)
            if rank > newest_rank:
                newest = item
                newest_rank = rank

        self._last_id = max(self._last_id, max_id)
        if newest is None:
            return None

        event_id = _awareness_event_id(newest, cur.domain)
        if event_id == "enemy_on_six" and self._record_tail_hit(newest, cur.timestamp or 0.0):
            event_id = "tailing_risk"
        payload = _payload(newest)
        if cur.domain and cur.domain != "unknown":
            payload["domain"] = cur.domain
        return BattleEvent(event_id, payload=payload, ts=cur.timestamp or 0.0, level="warning")

    def _record_tail_hit(self, item: dict[str, Any], now: float) -> bool:
        eid = _event_id(item)
        distance = _as_float(item.get("distance_m"))
        self._tail_hits = [
            (ts, hit_id)
            for ts, hit_id in self._tail_hits
            if now - ts <= self._tail_window_seconds and hit_id != eid
        ]
        if eid is None or distance is None or distance > self._tail_distance_m:
            return False

        self._tail_hits.append((now, eid))
        return len(self._tail_hits) >= self._tail_confirm_events


def _event_id(item: dict[str, Any]) -> int | None:
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return None


def _awareness_event_id(item: dict[str, Any], domain: str) -> str:
    if (domain or "").lower() in {"air", "heli"} and _is_behind(item):
        return "enemy_on_six"
    if item.get("is_air") is True:
        return "air_threat_nearby"
    return "enemy_nearby"


def _is_behind(item: dict[str, Any]) -> bool:
    clock = _as_int(item.get("clock"))
    if clock in _BEHIND_CLOCKS:
        return True
    rel = _as_float(item.get("relative_deg"))
    return rel is not None and abs(rel) >= 135.0


def _event_priority(event_id: str) -> int:
    if event_id == "tailing_risk":
        return 4
    if event_id == "enemy_on_six":
        return 3
    if event_id == "air_threat_nearby":
        return 2
    return 1


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "kind": _safe_short_text(item.get("kind")),
        "target_type": _safe_short_text(item.get("type")),
        "category": _safe_short_text(item.get("category")),
        "is_air": bool(item.get("is_air", False)),
        "distance_m": _as_float(item.get("distance_m")),
        "bearing_deg": _as_float(item.get("bearing_deg")),
        "compass": _safe_short_text(item.get("compass")),
        "clock": _as_int(item.get("clock")),
        "relative_deg": _as_float(item.get("relative_deg")),
        "threshold_m": _as_float(item.get("threshold_m")),
    }
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_short_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 32:
        return None
    return text
