"""Target selection, damage progress and the ammunition hint.

The ammunition hint is deliberately the weakest claim in the whole plugin: the
service reports the shell currently selected, but not reload state and not what
the player is aiming at. So it may only ever suggest a re-check, never announce
that a shell swap is correct or that a reload has finished.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.catalog import (
    AMMO_RECHECK_HINT,
    DAMAGE_MILESTONE,
    LOW_HP_TARGET,
    PRIORITY_TARGET,
    SITUATION_ADVICE,
)
from ..domain.snapshot import (
    DOMAIN_BALLISTICS,
    DOMAIN_DAMAGE,
    DOMAIN_OBJECTS,
    DOMAIN_ROSTER,
    DOMAIN_SELF,
)
from ._base import Detector, DetectorContext, GameEvent

# Shell types whose value depends strongly on what is being shot at.
_AP = "AP"
_HE = "HE"

# Ship classes where high-explosive is usually the safer default because armour
# penetration is unreliable. Only used to suggest a re-check, never a decision.
_HE_FRIENDLY_CLASSES = frozenset({"Destroyer", "AirCarrier", "Auxiliary"})
_AP_FRIENDLY_CLASSES = frozenset({"Battleship"})


class PriorityTargetDetector(Detector):
    name = "priority_target"
    events = (PRIORITY_TARGET,)
    required = (DOMAIN_SELF, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._current: int | None = None

    def observe(self, snapshot, facts) -> None:
        best = facts.best_target
        self._current = best.ship.ui_id if best is not None else None

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        best = facts.best_target
        if best is None or best.ship.ui_id == self._current:
            return ()
        return (self._event(
            PRIORITY_TARGET,
            severity=30,
            facts=facts,
            detail={
                "ship_name": best.ship.name,
                "ship_type": best.ship.ship_type,
                "tier": best.ship.tier,
                "distance_m": round(best.distance_m),
                "bearing_deg": round(best.bearing_deg),
                "hp_ratio": round(best.ship.hp_ratio, 3) if best.ship.hp_ratio else None,
                # A suggestion built from distance and health only.
                "kind": "candidate",
            },
        ),)


class LowHpTargetDetector(Detector):
    name = "low_hp_target"
    events = (LOW_HP_TARGET,)
    required = (DOMAIN_OBJECTS,)

    def reset(self) -> None:
        self._announced: set[int] = set()

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        target = facts.lowest_hp_target
        if target is None or target.ship.hp_ratio is None:
            return ()
        if target.ship.hp_ratio > self.cfg.low_hp_target_ratio:
            return ()
        key = target.ship.ui_id
        if key is None or key in self._announced:
            return ()
        self._announced.add(key)
        return (self._event(
            LOW_HP_TARGET,
            severity=int(45 + (self.cfg.low_hp_target_ratio - target.ship.hp_ratio) * 60),
            facts=facts,
            detail={
                "ship_name": target.ship.name,
                "ship_type": target.ship.ship_type,
                "hp_ratio": round(target.ship.hp_ratio, 3),
                "distance_m": round(target.distance_m),
                "bearing_deg": round(target.bearing_deg),
            },
        ),)


class DamageMilestoneDetector(Detector):
    name = "damage_milestone"
    events = (DAMAGE_MILESTONE,)
    required = (DOMAIN_DAMAGE,)

    def reset(self) -> None:
        self._last_milestone = 0.0

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        damage = facts.damage_inflicted
        if damage is None:
            return ()
        step = self.cfg.damage_milestone_step
        reached = int(damage // step) * step
        if reached <= self._last_milestone or reached <= 0:
            return ()
        self._last_milestone = reached
        return (self._event(
            DAMAGE_MILESTONE,
            severity=25,
            facts=facts,
            detail={
                "damage_inflicted": round(damage),
                "milestone": round(reached),
            },
        ),)


class AmmoRecheckDetector(Detector):
    name = "ammo_recheck_hint"
    events = (AMMO_RECHECK_HINT,)
    required = (DOMAIN_BALLISTICS, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._last_hint: tuple[str, str] | None = None

    def _mismatch(self, facts) -> tuple[str, str] | None:
        ammo = facts.ammo_type
        target = facts.best_target
        if not ammo or target is None:
            return None
        target_class = target.ship.ship_type
        if not target_class:
            return None
        mismatched = (
            (ammo.upper() == _AP and target_class in _HE_FRIENDLY_CLASSES)
            or (ammo.upper() == _HE and target_class in _AP_FRIENDLY_CLASSES)
        )
        return (ammo.upper(), target_class) if mismatched else None

    def observe(self, snapshot, facts) -> None:
        self._last_hint = self._mismatch(facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        _snapshot, facts = current
        signature = self._mismatch(facts)
        if signature is None or signature == self._last_hint:
            return ()
        ammo = facts.ammo_type
        target = facts.best_target
        target_class = target.ship.ship_type

        return (self._event(
            AMMO_RECHECK_HINT,
            severity=35,
            facts=facts,
            detail={
                "selected_ammo": ammo,
                "penetration_mm": facts.penetration_mm,
                "target_class": target_class,
                "target_name": target.ship.name,
                "distance_m": round(target.distance_m),
                # No reload timer and no confirmed aim point exist in the data,
                # so this can only ever be a prompt to look, not a conclusion.
                "claim": "recheck_only",
                "reload_state": "unsupported",
                "confirmed_aim": "unsupported",
            },
        ),)


class SituationAdviceDetector(Detector):
    """A low-priority nudge when the shape of the battle changes.

    Fires on class, map, mode or force-balance changes rather than on a timer, so
    it stays quiet in a stable game.
    """

    name = "situation_advice"
    events = (SITUATION_ADVICE,)
    required = (DOMAIN_SELF,)
    optional = (DOMAIN_ROSTER, DOMAIN_OBJECTS)

    def reset(self) -> None:
        self._signature: tuple | None = None

    @staticmethod
    def _shape(snapshot, facts) -> tuple:
        balance = None
        if facts.alive_allies is not None and facts.alive_enemies is not None:
            balance = facts.alive_enemies - facts.alive_allies
        return (snapshot.own_ship_type, snapshot.map_name,
                snapshot.game_mode, balance)

    def observe(self, snapshot, facts) -> None:
        self._signature = self._shape(snapshot, facts)

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        snapshot, facts = current
        signature = self._shape(snapshot, facts)
        # Nothing to compare against yet, so the very first shape is not a change.
        if self._signature is None or signature == self._signature:
            return ()
        own_class, _map_name, _game_mode, balance = signature
        return (self._event(
            SITUATION_ADVICE,
            severity=20,
            facts=facts,
            detail={
                "own_class": own_class,
                "map_name": snapshot.map_name,
                "game_mode": snapshot.game_mode,
                "force_balance": balance,
                "hp_ratio": round(facts.own_hp_ratio, 3) if facts.own_hp_ratio else None,
            },
        ),)


def build_targeting_detectors(cfg) -> tuple[Detector, ...]:
    return (
        PriorityTargetDetector(cfg),
        LowHpTargetDetector(cfg),
        DamageMilestoneDetector(cfg),
        AmmoRecheckDetector(cfg),
        SituationAdviceDetector(cfg),
    )
