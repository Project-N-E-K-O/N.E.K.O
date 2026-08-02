"""Own-ship survival: sinking, low health, incoming damage, being outmatched.

Wording discipline is enforced here, not in the prompt. A single ship's health
dropping is only ever "taking damage fast" -- calling it focused fire would
require knowing who is shooting, which the telemetry does not say.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

from ..domain.catalog import (
    LOCALLY_ISOLATED,
    LOW_HEALTH,
    OUTNUMBERED,
    OWN_SHIP_SUNK,
    RAPID_DAMAGE,
)
from ..domain.snapshot import DOMAIN_OBJECTS, DOMAIN_ROSTER, DOMAIN_SELF
from ._base import Detector, DetectorContext, GameEvent


class SinkingDetector(Detector):
    name = "own_ship_sunk"
    events = (OWN_SHIP_SUNK,)
    required = (DOMAIN_SELF,)

    def reset(self) -> None:
        self._fired = False

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _previous_snapshot, previous_facts = previous
        _snapshot, facts = current
        if self._fired:
            return ()
        # Both readings must exist: `self` going absent is a missing domain, not
        # a death, and the registry already blocks us in that case.
        if previous_facts.own_health is None or facts.own_health is None:
            return ()
        if previous_facts.own_health <= 0 or facts.own_health > 0:
            return ()
        self._fired = True
        return (self._event(
            OWN_SHIP_SUNK,
            severity=100,
            facts=facts,
            detail={
                "allies_left": facts.alive_allies,
                "enemies_left": facts.alive_enemies,
            },
        ),)


class LowHealthDetector(Detector):
    name = "low_health"
    events = (LOW_HEALTH,)
    required = (DOMAIN_SELF,)

    def reset(self) -> None:
        self._crossed: set[float] = set()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _previous_snapshot, previous_facts = previous
        _snapshot, facts = current
        ratio = facts.own_hp_ratio
        before = previous_facts.own_hp_ratio
        if ratio is None or before is None or ratio <= 0.0:
            return ()

        for threshold in self.cfg.low_health_ratios:
            if threshold in self._crossed:
                continue
            if before > threshold >= ratio:
                self._crossed.add(threshold)
                # A lower threshold means a more urgent situation; scale the
                # severity so the arbiter prefers the worse one.
                severity = int(60 + (1.0 - threshold) * 35)
                return (self._event(
                    LOW_HEALTH,
                    severity=severity,
                    facts=facts,
                    detail={
                        "hp_ratio": round(ratio, 3),
                        "threshold": threshold,
                        "health": facts.own_health,
                        "max_health": facts.own_max_health,
                        "nearest_enemy_m": _nearest_distance(facts),
                    },
                ),)
        return ()


class RapidDamageDetector(Detector):
    name = "rapid_damage"
    events = (RAPID_DAMAGE,)
    required = (DOMAIN_SELF,)

    def reset(self) -> None:
        self._history: deque[tuple[float, float]] = deque()

    def observe(self, snapshot, facts) -> None:
        if facts.own_hp_ratio is None:
            return
        self._history.append((facts.at, facts.own_hp_ratio))
        cutoff = facts.at - self.cfg.rapid_damage_window_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        ratio = facts.own_hp_ratio
        if ratio is None or not self._history:
            return ()
        window_start_ratio = self._history[0][1]
        drop = window_start_ratio - ratio
        if drop < self.cfg.rapid_damage_ratio:
            return ()
        return (self._event(
            RAPID_DAMAGE,
            severity=int(70 + min(25.0, drop * 100.0)),
            facts=facts,
            detail={
                "hp_ratio": round(ratio, 3),
                "drop_ratio": round(drop, 3),
                "window_seconds": self.cfg.rapid_damage_window_seconds,
                # Deliberately named for what we can see. Attributing this to
                # several attackers would need per-source damage data.
                "phrasing": "taking_damage_fast",
                "attacker_count": "unsupported",
            },
        ),)


class OutnumberedDetector(Detector):
    name = "outnumbered"
    events = (OUTNUMBERED,)
    required = (DOMAIN_OBJECTS,)
    optional = (DOMAIN_ROSTER,)

    def reset(self) -> None:
        self._announced_gap = 0

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        allies, enemies = facts.alive_allies, facts.alive_enemies
        if allies is None or enemies is None:
            return ()
        gap = enemies - allies
        if gap < self.cfg.outnumbered_margin or gap <= self._announced_gap:
            return ()
        self._announced_gap = gap
        return (self._event(
            OUTNUMBERED,
            severity=int(40 + min(30, gap * 6)),
            facts=facts,
            detail={"allies": allies, "enemies": enemies, "gap": gap},
        ),)


class IsolationDetector(Detector):
    name = "locally_isolated"
    events = (LOCALLY_ISOLATED,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._isolated = False

    def _is_isolated(self, facts) -> bool:
        ally_distance = facts.nearest_ally_distance_m
        nearest = facts.nearest_enemy
        # No ally position at all is unknown, not "alone".
        if ally_distance is None or nearest is None:
            return False
        return (
            ally_distance > self.cfg.isolation_ally_range_m
            and nearest.distance_m < self.cfg.isolation_enemy_range_m
        )

    def observe(self, snapshot, facts) -> None:
        self._isolated = self._is_isolated(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        if self._isolated or not self._is_isolated(facts):
            return ()
        ally_distance = facts.nearest_ally_distance_m
        nearest = facts.nearest_enemy
        return (self._event(
            LOCALLY_ISOLATED,
            severity=55,
            facts=facts,
            detail={
                "nearest_ally_m": round(ally_distance),
                "nearest_enemy_m": round(nearest.distance_m),
                "nearest_enemy_type": nearest.ship.ship_type,
            },
        ),)


def _nearest_distance(facts) -> float | None:
    return round(facts.nearest_enemy.distance_m) if facts.nearest_enemy else None


def build_survival_detectors(cfg) -> tuple[Detector, ...]:
    return (
        SinkingDetector(cfg),
        LowHealthDetector(cfg),
        RapidDamageDetector(cfg),
        OutnumberedDetector(cfg),
        IsolationDetector(cfg),
    )
