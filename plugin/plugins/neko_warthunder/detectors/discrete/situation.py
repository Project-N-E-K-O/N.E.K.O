"""Discrete detectors for safe data-layer situation summaries.

The data layer already separates mission ground targets from combat enemies and
also provides continuous air-enemy geometry in ``situation.enemies``. The plugin
consumes only safe metadata from those summaries; raw labels or player text must
not enter event payloads.
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector

_DEFAULT_TARGET_DISTANCE_M = 3000.0
_DEFAULT_AIR_THREAT_DISTANCE_M = 5000.0
_DEFAULT_REAR_THREAT_DISTANCE_M = 5000.0
_DEFAULT_TAIL_DISTANCE_M = 1500.0
_BEHIND_CLOCKS = {5, 6, 7}


class AirSituationDetector(DiscreteDetector):
    id = "air_situation"

    def __init__(
        self,
        *,
        air_distance_m: float = _DEFAULT_AIR_THREAT_DISTANCE_M,
        rear_distance_m: float = _DEFAULT_REAR_THREAT_DISTANCE_M,
        tail_distance_m: float = _DEFAULT_TAIL_DISTANCE_M,
        tail_window_seconds: float = 5.0,
        tail_confirm_frames: int = 2,
    ) -> None:
        self.air_distance_m = max(0.0, float(air_distance_m))
        self.rear_distance_m = max(0.0, float(rear_distance_m))
        self.tail_distance_m = max(100.0, float(tail_distance_m))
        self.tail_window_seconds = max(1.0, float(tail_window_seconds))
        self.tail_confirm_frames = max(2, int(tail_confirm_frames))
        self._last_key: tuple[str, int, int] | None = None
        self._tail_hits: list[float] = []

    def reset(self) -> None:
        self._last_key = None
        self._tail_hits.clear()

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            self.reset()
            return None
        if cur.domain not in {"air", "heli"}:
            self.reset()
            return None

        situation = cur.situation if isinstance(cur.situation, dict) else {}
        candidates = _air_enemy_candidates(situation)
        if not candidates:
            self._tail_hits.clear()
            return None

        rear = _nearest_by_distance(
            [item for item in candidates if _is_rear(item) and _within_distance(item, self.rear_distance_m)]
        )
        nearest = rear or _nearest_by_distance(
            [item for item in candidates if _within_distance(item, self.air_distance_m)]
        )
        if nearest is None:
            self._tail_hits.clear()
            return None

        distance = _as_float(nearest.get("distance_m")) or 0.0
        event_id = "air_threat_nearby"
        if rear is not None:
            event_id = "enemy_on_six"
            if distance <= self.tail_distance_m and self._record_tail_hit(cur.timestamp or 0.0):
                event_id = "tailing_risk"
        else:
            self._tail_hits.clear()

        key = _air_key(event_id, nearest)
        if key == self._last_key:
            return None
        self._last_key = key

        payload = _air_payload(nearest)
        payload["domain"] = cur.domain
        return BattleEvent(
            event_id,
            payload=payload,
            ts=cur.timestamp or 0.0,
            level="warning",
        )

    def _record_tail_hit(self, now: float) -> bool:
        self._tail_hits = [ts for ts in self._tail_hits if now - ts <= self.tail_window_seconds]
        if not self._tail_hits or self._tail_hits[-1] != now:
            self._tail_hits.append(now)
        return len(self._tail_hits) >= self.tail_confirm_frames


class GroundTargetDetector(DiscreteDetector):
    id = "ground_target_nearby"

    def __init__(self, *, distance_m: float = _DEFAULT_TARGET_DISTANCE_M) -> None:
        self.distance_m = max(0.0, float(distance_m))
        self._last_key: tuple[str, str, int] | None = None

    def reset(self) -> None:
        self._last_key = None

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            self.reset()
            return None
        if cur.domain not in {"air", "heli"}:
            self.reset()
            return None

        targets = cur.situation.get("ground_targets") if isinstance(cur.situation, dict) else None
        if not isinstance(targets, list):
            return None

        nearest = _nearest_target(targets, self.distance_m)
        if nearest is None:
            return None

        key = _target_key(nearest)
        if key == self._last_key:
            return None
        self._last_key = key

        payload = _payload(nearest)
        payload["domain"] = cur.domain
        return BattleEvent(
            "ground_target_nearby",
            payload=payload,
            ts=cur.timestamp or 0.0,
            level="warning",
        )


def _air_enemy_candidates(situation: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    nearest = situation.get("nearest_air_threat")
    if isinstance(nearest, dict) and _is_air_enemy(nearest):
        candidates.append(nearest)
    enemies = situation.get("enemies")
    if isinstance(enemies, list):
        candidates.extend(item for item in enemies if isinstance(item, dict) and _is_air_enemy(item))
    return candidates


def _nearest_by_distance(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_distance = float("inf")
    for item in items:
        distance = _as_float(item.get("distance_m"))
        if distance is None:
            continue
        if distance < best_distance:
            best = item
            best_distance = distance
    return best


def _within_distance(item: dict[str, Any], max_distance_m: float) -> bool:
    distance = _as_float(item.get("distance_m"))
    return distance is not None and distance <= max_distance_m


def _is_air_enemy(item: dict[str, Any]) -> bool:
    if item.get("is_air") is True:
        return True
    type_text = str(item.get("type") or "").strip().lower()
    if type_text in {"aircraft", "air", "helicopter"}:
        return True
    icon = str(item.get("icon") or "").strip().lower()
    if icon in {"fighter", "bomber", "assault", "attacker", "helicopter"}:
        return True
    return False


def _is_rear(item: dict[str, Any]) -> bool:
    clock = _as_int(item.get("clock"))
    if clock in _BEHIND_CLOCKS:
        return True
    rel = _as_float(item.get("relative_deg"))
    return rel is not None and abs(rel) >= 135.0


def _air_key(event_id: str, item: dict[str, Any]) -> tuple[str, int, int]:
    distance = _as_float(item.get("distance_m")) or 0.0
    clock = _as_int(item.get("clock"))
    if clock is None:
        rel = _as_float(item.get("relative_deg")) or 0.0
        clock = int((rel + 180.0) // 30.0)
    return event_id, clock, int(distance // 500.0)


def _air_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "source": "situation",
        "kind": _safe_short_text(item.get("kind")),
        "target_type": _safe_short_text(item.get("type")),
        "category": _safe_short_text(item.get("category")),
        "is_air": True,
        "distance_m": _as_float(item.get("distance_m")),
        "bearing_deg": _as_float(item.get("bearing_deg")),
        "clock": _as_int(item.get("clock")),
        "relative_deg": _as_float(item.get("relative_deg")),
    }
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def _nearest_target(targets: list[Any], max_distance_m: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_distance = float("inf")
    for item in targets:
        if not isinstance(item, dict):
            continue
        distance = _as_float(item.get("distance_m"))
        if distance is None or distance > max_distance_m:
            continue
        if distance < best_distance:
            best = item
            best_distance = distance
    return best


def _target_key(item: dict[str, Any]) -> tuple[str, str, int]:
    kind = _safe_short_text(item.get("kind")) or "target"
    grid = _safe_short_text(item.get("grid")) or ""
    distance = _as_float(item.get("distance_m")) or 0.0
    return kind, grid, int(distance // 500)


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "target_kind": _safe_short_text(item.get("kind")),
        "grid": _safe_short_text(item.get("grid")),
        "distance_m": _as_float(item.get("distance_m")),
        "bearing_deg": _as_float(item.get("bearing_deg")),
        "relative_deg": _as_float(item.get("relative_deg")),
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
