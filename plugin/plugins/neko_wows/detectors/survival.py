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
from ..domain.snapshot import (
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
    STATUS_ENDED,
)
from ._base import Detector, DetectorContext, GameEvent


class SinkingDetector(Detector):
    name = "own_ship_sunk"
    events = (OWN_SHIP_SUNK,)
    delivery_managed_events = events
    required = (DOMAIN_SELF,)

    def reset(self) -> None:
        self._announced = False
        self._pending = False

    def reset_for_discontinuity(self, snapshot, facts) -> None:
        """Keep delivery state across a temporary loss of own-ship data."""
        del facts
        if snapshot.status == STATUS_ENDED:
            self._pending = False

    def observe(self, snapshot, facts) -> None:
        # Null health is a missing reading, not recovery. Keep pending so a
        # partial self payload cannot cancel a queued sinking call-out.
        if self._pending and (
            snapshot.status == STATUS_ENDED
            or _own_afloat(facts) is True
        ):
            self._pending = False

    def acknowledge_delivery(self, event_id, detail) -> None:
        del detail
        if event_id == OWN_SHIP_SUNK:
            self._announced = True
            self._pending = False

    def pending_delivery_events(self) -> tuple[str, ...]:
        return (OWN_SHIP_SUNK,) if self._pending else ()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _previous_snapshot, previous_facts = previous
        _snapshot, facts = current
        if self._announced:
            return ()
        # A null current reading is unknown, not death. Keep any pending latch
        # so delivery can retry once health returns.
        current = _own_afloat(facts)
        previous = _own_afloat(previous_facts)
        if current is None:
            return ()
        if self._pending and current is True:
            self._pending = False
        if not self._pending and previous is True and current is False:
            self._pending = True
        if not self._pending or current is True:
            return ()
        # Own hull is usually alive=False on this frame, so team_counts_confirmed
        # (which requires our own object still lit-and-alive) is false. Visible
        # force counts for the rest of the field remain trustworthy when present.
        detail = {}
        if (
            facts.confirmed_visible_allies is not None
            and facts.confirmed_visible_enemies is not None
        ):
            detail = {
                "confirmed_visible_allies": facts.confirmed_visible_allies,
                "confirmed_visible_enemies": facts.confirmed_visible_enemies,
            }
        return (self._event(
            OWN_SHIP_SUNK,
            severity=100,
            facts=facts,
            detail=detail,
        ),)


class LowHealthDetector(Detector):
    name = "low_health"
    events = (LOW_HEALTH,)
    delivery_managed_events = events
    required = (DOMAIN_SELF,)

    def reset(self) -> None:
        self._announced: set[float] = set()
        self._pending: float | None = None

    def reset_for_discontinuity(self, snapshot, facts) -> None:
        """Keep delivery state across a temporary loss of own-ship data."""
        del facts
        if snapshot.status == STATUS_ENDED:
            self._pending = None

    def observe(self, snapshot, facts) -> None:
        threshold = self._pending
        if threshold is None:
            return
        ratio = facts.own_hp_ratio
        # Null ratio is a missing reading, not repair. Keep pending across it.
        if (
            snapshot.status == STATUS_ENDED
            or (ratio is not None and ratio <= 0.0)
            or (ratio is not None and ratio > threshold)
        ):
            self._pending = None

    def acknowledge_delivery(self, event_id, detail) -> None:
        if event_id != LOW_HEALTH:
            return
        threshold = detail.get("threshold")
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
        ):
            return
        threshold = float(threshold)
        self._announced.update(
            band for band in self.cfg.low_health_ratios if band >= threshold)
        if self._pending == threshold:
            self._pending = None

    def pending_delivery_events(self) -> tuple[str, ...]:
        return (LOW_HEALTH,) if self._pending is not None else ()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _previous_snapshot, previous_facts = previous
        _snapshot, facts = current
        ratio = facts.own_hp_ratio
        before = previous_facts.own_hp_ratio
        if ratio is None:
            return ()
        if ratio <= 0.0:
            self._pending = None
            return ()

        if self._pending is not None and ratio > self._pending:
            self._pending = None
        if before is not None:
            crossed = [
                threshold
                for threshold in self.cfg.low_health_ratios
                if threshold not in self._announced and before > threshold >= ratio
            ]
            if crossed:
                crossed_threshold = min(crossed)
                if self._pending is None or crossed_threshold < self._pending:
                    self._pending = crossed_threshold

        threshold = self._pending
        if threshold is None or ratio > threshold:
            return ()
        # Prefer the lowest band: that is the most urgent situation reached.
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
        self._prune_history(facts.at)

    def _prune_history(self, at: float) -> None:
        cutoff = at - self.cfg.rapid_damage_window_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        ratio = facts.own_hp_ratio
        if ratio is None:
            return ()
        # feed() compares before observe() records this frame, so stale
        # samples must be dropped against the current timestamp first.
        self._prune_history(facts.at)
        if not self._history:
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
    # Speak from currently lit, explicitly-alive hulls only. Dark last-known /
    # roster upper bounds stay on the panel and must not drive this claim.
    required = (DOMAIN_OBJECTS,)
    optional = (DOMAIN_ROSTER,)

    def reset(self) -> None:
        self._announced_gap = 0

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        allies = facts.confirmed_visible_allies
        enemies = facts.confirmed_visible_enemies
        if not facts.team_counts_confirmed or allies is None or enemies is None:
            return ()
        gap = enemies - allies
        if gap < self.cfg.outnumbered_margin or gap <= self._announced_gap:
            return ()
        self._announced_gap = gap
        return (self._event(
            OUTNUMBERED,
            severity=int(10 + min(15, gap * 3)),
            facts=facts,
            detail={
                "confirmed_visible_allies": allies,
                "confirmed_visible_enemies": enemies,
                "gap": gap,
            },
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


def _own_afloat(facts) -> bool | None:
    """Whether the player's hull is still afloat.

    `own_alive` already combines 3D self.health with the avatar object. Fall
    back to a raw health reading only when that combined flag is missing.
    """
    if facts.own_alive is not None:
        return facts.own_alive
    if facts.own_health is not None:
        return facts.own_health > 0
    return None


def build_survival_detectors(cfg) -> tuple[Detector, ...]:
    return (
        SinkingDetector(cfg),
        LowHealthDetector(cfg),
        RapidDamageDetector(cfg),
        OutnumberedDetector(cfg),
        IsolationDetector(cfg),
    )
