"""Battle start, battle end and the post-battle summary.

These read the source status rather than live ship data, so `needs_live` is
False: the final frame of a battle is inactive by definition, and that is exactly
the frame the end events have to fire on.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.catalog import BATTLE_ENDED, BATTLE_STARTED, POST_BATTLE_SUMMARY
from ..domain.snapshot import (
    DOMAIN_DAMAGE,
    DOMAIN_ROSTER,
    STATUS_ENDED,
    STATUS_LIVE,
)
from ._base import Detector, DetectorContext, GameEvent


class BattleLifecycleDetector(Detector):
    name = "battle_lifecycle"
    events = (BATTLE_STARTED, BATTLE_ENDED, POST_BATTLE_SUMMARY)
    needs_live = False

    def reset(self) -> None:
        self._announced_start = False
        self._announced_end = False
        self._peak_damage: float | None = None

    def observe(self, snapshot, facts) -> None:
        # Damage is reported per-frame and the final inactive frame drops it, so
        # the summary uses the highest value seen during the battle.
        if facts.damage_inflicted is not None:
            if self._peak_damage is None or facts.damage_inflicted > self._peak_damage:
                self._peak_damage = facts.damage_inflicted

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        previous_snapshot, _previous_facts = previous
        snapshot, facts = current
        events: list[GameEvent] = []

        if (
            not self._announced_start
            and snapshot.status == STATUS_LIVE
            and previous_snapshot.status != STATUS_LIVE
        ):
            self._announced_start = True
            events.append(self._event(
                BATTLE_STARTED,
                severity=40,
                facts=facts,
                detail={
                    "battle_type": snapshot.battle_type,
                    "game_mode": snapshot.game_mode,
                    "map_name": snapshot.map_name,
                    "own_ship": snapshot.own_ship_name,
                    "ship_type": snapshot.own_ship_type,
                    "allies": facts.alive_allies,
                    "enemies": facts.alive_enemies,
                },
            ))

        if (
            not self._announced_end
            and snapshot.status == STATUS_ENDED
            and previous_snapshot.status != STATUS_ENDED
        ):
            self._announced_end = True
            events.append(self._event(
                BATTLE_ENDED,
                severity=45,
                facts=facts,
                detail={
                    "battle_type": snapshot.battle_type,
                    "map_name": snapshot.map_name,
                },
            ))
            events.append(self._event(
                POST_BATTLE_SUMMARY,
                severity=20,
                facts=facts,
                detail={
                    "damage_inflicted": self._peak_damage,
                    "damage_available": snapshot.supports(DOMAIN_DAMAGE),
                    "roster_available": snapshot.supports(DOMAIN_ROSTER),
                    # The service cannot attribute kills yet; saying anything
                    # about them would be invention.
                    "outcome": "unsupported",
                },
            ))

        return events


def build_lifecycle_detectors(cfg) -> tuple[Detector, ...]:
    return (BattleLifecycleDetector(cfg),)
