"""Outgoing damage bursts derived from cumulative per-victim telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
from typing import Sequence

from ..domain.catalog import DEVASTATING_STRIKE, HIGH_DAMAGE
from ..domain.snapshot import DOMAIN_DAMAGE, DOMAIN_OBJECTS, DOMAIN_SELF
from ._base import Detector, DetectorContext, GameEvent


@dataclass
class _Burst:
    samples: deque[tuple[float, float]] = field(default_factory=deque)
    rolling_damage: float = 0.0
    peak_damage: float = 0.0
    last_damage_at: float = 0.0


@dataclass(frozen=True)
class _ResolvedBurst:
    event_id: str
    victim_id: int
    damage: float
    ratio: float | None
    sunk: bool | None


class DamageBurstDetector(Detector):
    """Classify large five-second damage windows without replaying counters."""

    name = "damage_burst"
    events = (HIGH_DAMAGE, DEVASTATING_STRIKE)
    required = (DOMAIN_SELF, DOMAIN_DAMAGE)
    optional = (DOMAIN_OBJECTS,)

    def reset(self) -> None:
        self._last_totals: dict[int, float] = {}
        self._rebaseline_victims: set[int] = set()
        self._bursts: dict[int, _Burst] = {}
        self._target_names: dict[int, str] = {}
        self._target_max_health: dict[int, float] = {}
        self._target_health: dict[int, float] = {}
        self._explicit_alive: dict[int, bool] = {}
        self._pending_sinks: dict[int, float] = {}
        self._objects_ready = False

    def observe(self, snapshot, facts) -> None:
        self._last_totals = self._sticky_totals(facts.damage_inflicted_by_victim)
        if not snapshot.is_available(DOMAIN_OBJECTS):
            self._explicit_alive.clear()
            self._objects_ready = False
            return

        self._cache_target_metadata(snapshot)
        self._cache_target_health(snapshot)
        for ship in snapshot.ships:
            player_id = ship.player_id
            if player_id is None or not isinstance(ship.alive, bool):
                continue
            self._explicit_alive[player_id] = ship.alive
        self._objects_ready = True

    def detect(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        snapshot, facts = current
        previous_snapshot, previous_facts = previous
        window = self.cfg.damage_burst_window_seconds
        if (
            snapshot.epoch != previous_snapshot.epoch
            or facts.at < previous_facts.at
            or facts.at - previous_facts.at > window
        ):
            self.reset()
            return ()

        now = facts.at
        raw_totals = dict(facts.damage_inflicted_by_victim)
        current_totals = self._sticky_totals(raw_totals)
        resolved: list[_ResolvedBurst] = []

        objects_available = snapshot.is_available(DOMAIN_OBJECTS)
        if objects_available:
            self._cache_target_metadata(snapshot)
        sunk_ids = self._sunk_targets(snapshot) if objects_available else set()
        for victim_id in sunk_ids:
            self._pending_sinks.setdefault(victim_id, now)
        for victim_id, died_at in tuple(self._pending_sinks.items()):
            if now - died_at > window:
                self._pending_sinks.pop(victim_id, None)

        for victim_id in self._last_totals.keys() - raw_totals.keys():
            if victim_id in self._bursts:
                continue
            self._rebaseline_victims.add(victim_id)

        for victim_id in sorted(current_totals):
            current_total = self._valid_total(current_totals[victim_id])
            previous_total = self._valid_total(self._last_totals.get(victim_id))
            if current_total is None:
                self._bursts.pop(victim_id, None)
                self._rebaseline_victims.add(victim_id)
                continue
            if previous_total is None:
                if victim_id in self._rebaseline_victims:
                    self._rebaseline_victims.discard(victim_id)
                    continue
                previous_total = 0.0
            if current_total < previous_total:
                self._bursts.pop(victim_id, None)
                continue
            delta = current_total - previous_total
            if delta <= 0:
                continue

            burst = self._bursts.get(victim_id)
            if burst is not None and now - burst.last_damage_at > window:
                old = self._close_burst(victim_id, burst, now, snapshot)
                if old is not None:
                    resolved.append(old)
                self._bursts.pop(victim_id, None)
            self._record_damage(victim_id, now, delta, window)
            burst = self._bursts[victim_id]
            closed = self._resolve_devastating(
                victim_id,
                burst,
                now,
                snapshot,
                sunk=self._is_sunk(victim_id),
                tick_damage=delta,
            )
            if closed is not None:
                resolved.append(closed)
                self._consume_burst(victim_id, raw_totals)

        for victim_id in sorted(sunk_ids | self._pending_sinks.keys()):
            burst = self._bursts.get(victim_id)
            if burst is None:
                continue
            closed = self._close_burst(victim_id, burst, now, snapshot, sunk=True)
            if closed is not None:
                resolved.append(closed)
                self._consume_burst(victim_id, raw_totals)

        for victim_id, burst in tuple(self._bursts.items()):
            if now - burst.last_damage_at < window:
                continue
            closed = self._close_burst(victim_id, burst, now, snapshot)
            self._consume_burst(victim_id, raw_totals)
            if closed is not None:
                resolved.append(closed)

        if not resolved:
            return ()
        chosen = min(resolved, key=self._resolved_rank)
        return (self._to_event(chosen, facts),)

    @staticmethod
    def _valid_total(value) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        total = float(value)
        if not math.isfinite(total) or total < 0:
            return None
        return total

    def _sticky_totals(self, totals: dict[int, float]) -> dict[int, float]:
        merged = dict(totals)
        for victim_id in self._bursts:
            if victim_id not in merged and victim_id in self._last_totals:
                merged[victim_id] = self._last_totals[victim_id]
        return merged

    def _consume_burst(self, victim_id: int, raw_totals: dict[int, float]) -> None:
        self._bursts.pop(victim_id, None)
        self._pending_sinks.pop(victim_id, None)
        if victim_id not in raw_totals:
            self._rebaseline_victims.add(victim_id)

    def _close_burst(
        self,
        victim_id: int,
        burst: _Burst,
        now: float,
        snapshot,
        *,
        sunk: bool | None = None,
    ) -> _ResolvedBurst | None:
        is_sunk = self._is_sunk(victim_id) if sunk is None else sunk
        devastating = self._resolve_devastating(
            victim_id, burst, now, snapshot, sunk=is_sunk)
        if devastating is not None:
            return devastating
        return self._resolve_high(
            victim_id,
            burst,
            sunk=True if is_sunk else self._known_sunk_state(snapshot, victim_id),
        )

    def _is_sunk(self, victim_id: int) -> bool:
        return victim_id in self._pending_sinks

    def _resolve_devastating(
        self,
        victim_id: int,
        burst: _Burst,
        now: float,
        snapshot,
        *,
        sunk: bool,
        tick_damage: float = 0.0,
    ) -> _ResolvedBurst | None:
        damage = self._window_damage(
            burst, now, self.cfg.damage_burst_window_seconds)
        maximum = self._target_max_health.get(victim_id)
        # In-game Devastating Strike is window damage / max HP, not remaining HP.
        ratio = damage / maximum if maximum else None
        if ratio is None or ratio < self.cfg.devastating_strike_ratio_threshold:
            return None
        remaining = self._target_health.get(victim_id)
        # Last-tick remaining already includes earlier salvos in this window, so
        # only this tick can prove a finishing blow. A still-visible positive
        # HP bar means the hull is afloat regardless of the rolling total.
        lethal = (
            remaining is not None
            and remaining > 0
            and tick_damage >= remaining
            and not self._snapshot_shows_positive_health(snapshot, victim_id)
        )
        if not (sunk or lethal):
            return None
        return _ResolvedBurst(
            event_id=DEVASTATING_STRIKE,
            victim_id=victim_id,
            damage=damage,
            ratio=ratio,
            sunk=True,
        )

    def _snapshot_shows_positive_health(self, snapshot, victim_id: int) -> bool:
        if snapshot is None or not snapshot.is_available(DOMAIN_OBJECTS):
            return False
        for ship in snapshot.ships:
            if ship.player_id != victim_id:
                continue
            health = self._valid_total(ship.health)
            if health is not None:
                return health > 0
            return ship.alive is not False
        return False

    def _record_damage(
        self,
        victim_id: int,
        at: float,
        damage: float,
        window: float,
    ) -> None:
        burst = self._bursts.setdefault(victim_id, _Burst())
        self._prune(burst, at, window)
        burst.samples.append((at, damage))
        burst.rolling_damage += damage
        burst.peak_damage = max(burst.peak_damage, burst.rolling_damage)
        burst.last_damage_at = at

    @staticmethod
    def _prune(burst: _Burst, now: float, window: float) -> None:
        cutoff = now - window
        while burst.samples and burst.samples[0][0] < cutoff:
            _at, damage = burst.samples.popleft()
            burst.rolling_damage -= damage

    def _window_damage(self, burst: _Burst, now: float, window: float) -> float:
        self._prune(burst, now, window)
        return max(0.0, burst.rolling_damage)

    def _resolve_high(
        self,
        victim_id: int,
        burst: _Burst,
        *,
        sunk: bool | None,
    ) -> _ResolvedBurst | None:
        damage = burst.peak_damage
        maximum = self._target_max_health.get(victim_id)
        ratio = damage / maximum if maximum else None
        qualifies = damage >= self.cfg.high_damage_absolute_threshold
        if ratio is not None:
            qualifies = qualifies or ratio >= self.cfg.high_damage_ratio_threshold
        if not qualifies:
            return None
        return _ResolvedBurst(
            event_id=HIGH_DAMAGE,
            victim_id=victim_id,
            damage=damage,
            ratio=ratio,
            sunk=sunk,
        )

    def _cache_target_metadata(self, snapshot) -> None:
        for ship in snapshot.ships:
            player_id = ship.player_id
            if player_id is None:
                continue
            spoken_name = ship.spoken_name
            if spoken_name:
                self._target_names[player_id] = spoken_name
            maximum = self._valid_total(ship.max_health)
            if maximum:
                self._target_max_health[player_id] = maximum

    def _cache_target_health(self, snapshot) -> None:
        for ship in snapshot.ships:
            player_id = ship.player_id
            if player_id is None:
                continue
            health = self._valid_total(ship.health)
            if health:
                self._target_health[player_id] = health
            elif health == 0.0:
                self._target_health.pop(player_id, None)

    def _sunk_targets(self, snapshot) -> set[int]:
        if not self._objects_ready:
            return set()
        sunk: set[int] = set()
        for ship in snapshot.ships:
            player_id = ship.player_id
            if player_id is None:
                continue
            if (
                ship.alive is False
                and self._explicit_alive.get(player_id) is True
            ):
                sunk.add(player_id)
                continue
            health = self._valid_total(ship.health)
            if health == 0.0 and self._target_health.get(player_id):
                sunk.add(player_id)
        return sunk

    @staticmethod
    def _known_sunk_state(snapshot, victim_id: int) -> bool | None:
        if not snapshot.is_available(DOMAIN_OBJECTS):
            return None
        for ship in snapshot.ships:
            if ship.player_id == victim_id and isinstance(ship.alive, bool):
                return not ship.alive
        return None

    @staticmethod
    def _resolved_rank(item: _ResolvedBurst) -> tuple[int, float, float, int]:
        event_rank = 0 if item.event_id == DEVASTATING_STRIKE else 1
        ratio = item.ratio if item.ratio is not None else -1.0
        return (event_rank, -ratio, -item.damage, item.victim_id)

    def _to_event(self, item: _ResolvedBurst, facts) -> GameEvent:
        detail = {
            "window_damage": round(item.damage),
            "window_seconds": self.cfg.damage_burst_window_seconds,
        }
        if item.sunk is not None:
            detail["target_sunk"] = item.sunk
        detail["victim_id"] = item.victim_id
        target_name = self._target_names.get(item.victim_id)
        if target_name:
            detail["target_name"] = target_name
        maximum = self._target_max_health.get(item.victim_id)
        if maximum:
            detail["target_max_health"] = round(maximum)
        if item.ratio is not None:
            detail["damage_ratio"] = round(item.ratio, 3)
        if item.event_id == DEVASTATING_STRIKE:
            detail["classification"] = "telemetry_estimate"
        return self._event(
            item.event_id,
            severity=80 if item.event_id == DEVASTATING_STRIKE else 55,
            facts=facts,
            detail=detail,
        )


def build_damage_detectors(cfg) -> tuple[Detector, ...]:
    return (DamageBurstDetector(cfg),)


__all__ = ["DamageBurstDetector", "build_damage_detectors"]
