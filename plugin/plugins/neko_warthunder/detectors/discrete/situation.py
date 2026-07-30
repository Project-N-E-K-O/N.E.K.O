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
from ._common import as_float as _as_float
from ._common import as_int as _as_int
from ._common import is_rear as _is_rear
from ._common import safe_short_text as _safe_short_text
from ._common import (
    SITUATION_TAIL_CONFIRM_FRAMES,
    SITUATION_TAIL_DISTANCE_M,
    SITUATION_TAIL_WINDOW_SECONDS,
)

_DEFAULT_TARGET_DISTANCE_M = 3000.0
_DEFAULT_AIR_THREAT_DISTANCE_M = 5000.0
_DEFAULT_REAR_THREAT_DISTANCE_M = 5000.0


class AirSituationDetector(DiscreteDetector):
    id = "air_situation"

    def __init__(
        self,
        *,
        air_distance_m: float = _DEFAULT_AIR_THREAT_DISTANCE_M,
        rear_distance_m: float = _DEFAULT_REAR_THREAT_DISTANCE_M,
        tail_distance_m: float = SITUATION_TAIL_DISTANCE_M,
        tail_window_seconds: float = SITUATION_TAIL_WINDOW_SECONDS,
        tail_confirm_frames: int = SITUATION_TAIL_CONFIRM_FRAMES,
    ) -> None:
        self.air_distance_m = max(0.0, float(air_distance_m))
        self.rear_distance_m = max(0.0, float(rear_distance_m))
        self.tail_distance_m = max(100.0, float(tail_distance_m))
        self.tail_window_seconds = max(1.0, float(tail_window_seconds))
        self.tail_confirm_frames = max(2, int(tail_confirm_frames))
        self._last_key: tuple[Any, ...] | None = None
        self._tail_hits: list[float] = []
        self._tail_identity: tuple[Any, ...] | None = None

    def reset(self) -> None:
        self._last_key = None
        self._tail_hits.clear()
        self._tail_identity = None

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            self.reset()
            return None
        if cur.domain not in {"air", "heli"}:
            self.reset()
            return None

        situation_valid = isinstance(cur.situation, dict)
        situation = cur.situation if situation_valid else {}
        candidates = _air_enemy_candidates(situation)
        if not candidates:
            self._tail_hits.clear()
            if situation_valid:
                self._last_key = None
            return None

        rear = _select_rear_threat(
            [item for item in candidates if _is_rear(item) and _within_distance(item, self.rear_distance_m)]
        )
        nearest = rear or _nearest_by_distance(
            [item for item in candidates if _within_distance(item, self.air_distance_m)]
        )
        if nearest is None:
            self._tail_hits.clear()
            self._last_key = None
            return None

        distance = _as_float(nearest.get("distance_m")) or 0.0
        event_id = "air_threat_nearby"
        if rear is not None:
            event_id = "enemy_on_six"
            if (
                distance <= self.tail_distance_m
                and _has_tailing_evidence(nearest)
                and self._record_tail_hit(nearest, cur.timestamp or 0.0)
            ):
                event_id = "tailing_risk"
        else:
            self._tail_hits.clear()
            self._tail_identity = None

        key = _air_key(event_id, nearest)
        if key == self._last_key:
            return None
        self._last_key = key

        payload = _air_payload(nearest)
        payload["domain"] = cur.domain
        air_threat_count = _as_int(situation.get("air_threat_count"))
        if air_threat_count is not None:
            payload["air_threat_count"] = air_threat_count
        return BattleEvent(
            event_id,
            payload=payload,
            ts=cur.timestamp or 0.0,
            level="warning",
        )

    def _record_tail_hit(self, item: dict[str, Any], now: float) -> bool:
        identity = _contact_identity(item)
        if identity != self._tail_identity:
            self._tail_hits.clear()
            self._tail_identity = identity
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
            self._last_key = None
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
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in candidates:
        identity = _contact_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


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


def _select_rear_threat(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None

    def rank(item: dict[str, Any]) -> tuple[int, float, float]:
        approaching_rank = 0 if item.get("approaching") is True else 1
        nose = _as_float(item.get("nose_to_player_deg"))
        alignment = abs(nose) if nose is not None else 181.0
        distance = _as_float(item.get("distance_m"))
        return approaching_rank, alignment, distance if distance is not None else float("inf")

    return min(items, key=rank)


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


def _has_tailing_evidence(item: dict[str, Any]) -> bool:
    if item.get("approaching") is True:
        return True
    nose = _as_float(item.get("nose_to_player_deg"))
    if nose is not None:
        return abs(nose) <= 60.0
    # Older recordings do not contain derived tracking fields. Preserve their
    # frame-persistence fallback without inventing new geometry.
    return item.get("approaching") is None


def _contact_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    track_id = _as_int(item.get("track_id"))
    if track_id is not None:
        return "track", track_id
    return (
        "legacy",
        str(item.get("type") or ""),
        str(item.get("icon") or ""),
        _as_int(item.get("clock")) or _clock_from_relative(item.get("relative_deg")),
        round(_as_float(item.get("x")) or 0.0, 3),
        round(_as_float(item.get("y")) or 0.0, 3),
    )


def _air_key(event_id: str, item: dict[str, Any]) -> tuple[Any, ...]:
    distance = _as_float(item.get("distance_m")) or 0.0
    clock = _as_int(item.get("clock"))
    if clock is None:
        clock = _clock_from_relative(item.get("relative_deg")) or 12
    # Contact identity is deliberately excluded: in dense modes the nearest
    # marker can alternate frame-to-frame even though the player-facing fact
    # (direction and distance band) has not changed.
    return event_id, clock, int(distance // 500.0)


def _air_payload(item: dict[str, Any]) -> dict[str, Any]:
    relative_deg = _as_float(item.get("relative_deg"))
    clock = _as_int(item.get("clock"))
    if clock is None:
        clock = _clock_from_relative(relative_deg)
    payload = {
        "source": "situation",
        "kind": _safe_short_text(item.get("kind")),
        "target_type": _safe_short_text(item.get("type")),
        "category": _safe_short_text(item.get("category")),
        "is_air": True,
        "distance_m": _as_float(item.get("distance_m")),
        "bearing_deg": _as_float(item.get("bearing_deg")),
        "clock": clock,
        "relative_deg": relative_deg,
        "track_id": _as_int(item.get("track_id")),
        "track_samples": _as_int(item.get("track_samples")),
        "track_age_seconds": _as_float(item.get("track_age_seconds")),
        "closing_speed_mps": _as_float(item.get("closing_speed_mps")),
        "approaching": item.get("approaching") if isinstance(item.get("approaching"), bool) else None,
        "nose_to_player_deg": _as_float(item.get("nose_to_player_deg")),
    }
    return {key: value for key, value in payload.items() if value is not None and value != ""}

def _clock_from_relative(value: Any) -> int | None:
    relative_deg = _as_float(value)
    if relative_deg is None:
        return None
    clock = round(relative_deg / 30.0) % 12
    return 12 if clock == 0 else int(clock)


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
