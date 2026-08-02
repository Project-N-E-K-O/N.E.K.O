"""Positional risk: map edges and hull angling.

Every event here needs both a position and a heading. Without a heading there is
no honest way to talk about showing your side, so the detector stays silent
rather than guessing from position alone.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.catalog import (
    BOUNDARY_RISK,
    OWN_BROADSIDE_EXPOSED,
    TARGET_BROADSIDE_WINDOW,
)
from ..domain.snapshot import DOMAIN_MAP_BOUNDS, DOMAIN_OBJECTS, DOMAIN_SELF
from ._base import Detector, DetectorContext, GameEvent


class BoundaryRiskDetector(Detector):
    name = "boundary_risk"
    events = (BOUNDARY_RISK,)
    required = (DOMAIN_SELF, DOMAIN_MAP_BOUNDS)

    def reset(self) -> None:
        self._warned = False

    def _at_risk(self, facts) -> bool:
        distance = facts.distance_to_boundary_m
        if distance is None or distance > self.cfg.boundary_margin_m:
            return False
        # Being near the edge is only worth mentioning while still heading out.
        return facts.heading_towards_boundary is True

    def observe(self, snapshot, facts) -> None:
        self._warned = self._at_risk(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        if self._warned or not self._at_risk(facts):
            return ()
        distance = facts.distance_to_boundary_m
        return (self._event(
            BOUNDARY_RISK,
            severity=int(50 + max(0.0, 1.0 - distance / max(
                1.0, self.cfg.boundary_margin_m)) * 25),
            facts=facts,
            detail={
                "distance_m": round(distance),
                "heading_deg": round(facts.own_heading_deg) if facts.own_heading_deg else None,
                "speed": facts.own_speed,
            },
        ),)


class OwnBroadsideDetector(Detector):
    name = "own_broadside_exposed"
    events = (OWN_BROADSIDE_EXPOSED,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._exposed = False

    @property
    def _limit(self) -> float:
        return 90.0 - self.cfg.broadside_angle_deg

    def _is_exposed(self, facts) -> bool:
        angle = facts.own_broadside_angle_deg
        nearest = facts.nearest_enemy
        # Without a heading there is no honest way to talk about hull angling.
        if angle is None or nearest is None:
            return False
        if nearest.distance_m > self.cfg.enemy_close_range_m:
            return False
        return angle >= self._limit

    def observe(self, snapshot, facts) -> None:
        self._exposed = self._is_exposed(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        if self._exposed or not self._is_exposed(facts):
            return ()
        angle = facts.own_broadside_angle_deg
        nearest = facts.nearest_enemy
        limit = self._limit
        return (self._event(
            OWN_BROADSIDE_EXPOSED,
            severity=int(55 + (angle - limit) * 0.8),
            facts=facts,
            detail={
                "broadside_angle_deg": round(angle),
                "enemy_distance_m": round(nearest.distance_m),
                "enemy_bearing_deg": round(nearest.bearing_deg),
                "enemy_type": nearest.ship.ship_type,
                "geometry_only": True,
            },
        ),)


class TargetBroadsideDetector(Detector):
    name = "target_broadside_window"
    events = (TARGET_BROADSIDE_WINDOW,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._current_target: int | None = None

    def _exposed_target(self, facts):
        target = facts.exposed_target
        angle = facts.exposed_target_angle_deg
        if target is None or angle is None:
            return None
        if angle < (90.0 - self.cfg.broadside_angle_deg):
            return None
        return target

    def observe(self, snapshot, facts) -> None:
        target = self._exposed_target(facts)
        self._current_target = target.ship.ui_id if target is not None else None

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        target = self._exposed_target(facts)
        if target is None or target.ship.ui_id == self._current_target:
            return ()
        angle = facts.exposed_target_angle_deg
        return (self._event(
            TARGET_BROADSIDE_WINDOW,
            severity=int(35 + angle * 0.2),
            facts=facts,
            detail={
                "broadside_angle_deg": round(angle),
                "distance_m": round(target.distance_m),
                "ship_type": target.ship.ship_type,
                "ship_name": target.ship.name,
                "hp_ratio": round(target.ship.hp_ratio, 3) if target.ship.hp_ratio else None,
            },
        ),)


def build_geometry_detectors(cfg) -> tuple[Detector, ...]:
    return (
        BoundaryRiskDetector(cfg),
        OwnBroadsideDetector(cfg),
        TargetBroadsideDetector(cfg),
    )
