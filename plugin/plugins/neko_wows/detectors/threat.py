"""Incoming threat: a ship closing in, or pressure from several directions."""

from __future__ import annotations

from typing import Sequence

from ..domain.catalog import ENEMY_CLOSING, MULTI_DIRECTION_THREAT
from ..domain.snapshot import DOMAIN_OBJECTS, DOMAIN_SELF
from ._base import Detector, DetectorContext, GameEvent


class EnemyClosingDetector(Detector):
    name = "enemy_closing"
    events = (ENEMY_CLOSING,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._inside = False

    def _inside_ring(self, facts) -> bool:
        nearest = facts.nearest_enemy
        # No visible enemy is unknown, not safe -- but there is also nothing to
        # report, so the latch simply clears and a re-spot can fire again.
        return (
            nearest is not None
            and nearest.distance_m <= self.cfg.enemy_close_range_m
        )

    def observe(self, snapshot, facts) -> None:
        self._inside = self._inside_ring(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _previous_snapshot, previous_facts = previous
        _snapshot, facts = current
        # `self._inside` describes the last frame we observed, including baseline
        # frames, so this is a genuine rising edge rather than a re-spot.
        if self._inside or not self._inside_ring(facts):
            return ()

        nearest = facts.nearest_enemy
        before = previous_facts.nearest_enemy
        closing_rate = None
        if before is not None:
            elapsed = facts.at - previous_facts.at
            if elapsed > 0:
                closing_rate = round((before.distance_m - nearest.distance_m) / elapsed, 1)
        return (self._event(
            ENEMY_CLOSING,
            severity=int(60 + max(0.0, 1.0 - nearest.distance_m / max(
                1.0, self.cfg.enemy_close_range_m)) * 25),
            facts=facts,
            detail={
                "distance_m": round(nearest.distance_m),
                "bearing_deg": round(nearest.bearing_deg),
                "ship_type": nearest.ship.ship_type,
                "ship_name": nearest.ship.name,
                "tier": nearest.ship.tier,
                "closing_m_per_s": closing_rate,
            },
        ),)


class MultiDirectionThreatDetector(Detector):
    name = "multi_direction_threat"
    events = (MULTI_DIRECTION_THREAT,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._active = False

    def _spread_out(self, facts) -> bool:
        spread = facts.threat_bearing_spread_deg
        return (
            spread is not None
            and len(facts.threats_in_scan_range) >= 2
            and spread >= self.cfg.multi_direction_spread_deg
        )

    def observe(self, snapshot, facts) -> None:
        self._active = self._spread_out(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        if self._active or not self._spread_out(facts):
            return ()
        spread = facts.threat_bearing_spread_deg
        threats = facts.threats_in_scan_range
        return (self._event(
            MULTI_DIRECTION_THREAT,
            severity=int(65 + min(20.0, (spread - self.cfg.multi_direction_spread_deg) / 4.5)),
            facts=facts,
            detail={
                "spread_deg": round(spread),
                "threat_count": len(threats),
                "bearings_deg": [round(t.bearing_deg) for t in threats[:4]],
                "nearest_m": round(threats[0].distance_m),
                # Positions and bearings only. Whether these ships are actually
                # coordinating is not observable, so crossfire is never claimed.
                "crossfire": "unsupported",
            },
        ),)


def build_threat_detectors(cfg) -> tuple[Detector, ...]:
    return (
        EnemyClosingDetector(cfg),
        MultiDirectionThreatDetector(cfg),
    )
