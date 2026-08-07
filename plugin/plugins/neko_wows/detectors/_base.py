"""Detector base class and the registry that gates them.

Three invariants live here rather than in each detector, because getting them
wrong once is enough to produce a phantom call-out:

1. **A missing required domain blocks the detector.** `unknown` and `stale` are
   not `false`, so nothing is emitted and the reason is recorded.
2. **The first comparable frame after any discontinuity only builds a baseline.**
   Reconnects, staleness recovery and battle switches all count. Without this a
   resumed stream looks like every value changed at once.
3. **A new `(instanceId, battleId)` resets every detector.** State from the
   previous battle must never leak into the next one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.catalog import EventSpec, spec_for
from ..domain.facts import WowsFacts
from ..domain.snapshot import WowsSnapshot


@dataclass(frozen=True)
class GameEvent:
    """One thing worth saying, with the facts that justify it."""

    event_id: str
    severity: int
    at: float
    seq: int
    battle_id: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> EventSpec:
        return spec_for(self.event_id)


@dataclass(frozen=True)
class BlockedDetector:
    detector: str
    missing: tuple[str, ...]
    events: tuple[str, ...]


@dataclass(frozen=True)
class DetectorContext:
    now: float
    baseline_only: bool
    identity_reset: bool
    cfg: Any


@dataclass(frozen=True)
class FeedResult:
    events: tuple[GameEvent, ...] = ()
    blocked: tuple[BlockedDetector, ...] = ()
    baseline_only: bool = False
    identity_reset: bool = False

    @property
    def reason(self) -> str:
        if self.identity_reset:
            return "identity_reset"
        if self.baseline_only:
            return "baseline"
        return "evaluated"


class Detector:
    """Template: `detect` proposes events, `observe` remembers the frame.

    Subclasses never have to handle the baseline case themselves -- `feed`
    routes a baseline frame to `observe` only.
    """

    name: str = "detector"
    events: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    # False for detectors driven by the source status rather than live ship data,
    # so they still run on the final inactive frame.
    needs_live: bool = True

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        """Drop all per-battle memory."""

    def observe(self, snapshot: WowsSnapshot, facts: WowsFacts) -> None:
        """Record whatever `detect` will compare against next frame."""

    def detect(
        self,
        previous: tuple[WowsSnapshot, WowsFacts],
        current: tuple[WowsSnapshot, WowsFacts],
        context: DetectorContext,
    ) -> Sequence[GameEvent]:
        return ()

    def feed(self, previous, current, context: DetectorContext) -> Sequence[GameEvent]:
        snapshot, facts = current
        if context.baseline_only or previous is None:
            self.observe(snapshot, facts)
            return ()
        events = tuple(self.detect(previous, current, context))
        self.observe(snapshot, facts)
        return events

    # -- helpers for subclasses ---------------------------------------
    def _event(
        self,
        event_id: str,
        *,
        severity: int,
        facts: WowsFacts,
        detail: dict[str, Any] | None = None,
    ) -> GameEvent:
        return GameEvent(
            event_id=event_id,
            severity=max(0, min(100, int(severity))),
            at=facts.at,
            seq=facts.seq,
            battle_id=facts.battle_id,
            detail=dict(detail or {}),
        )


class DetectorRegistry:
    """Runs detectors under the gating rules described at module level."""

    def __init__(self, detectors: Sequence[Detector]) -> None:
        self.detectors = tuple(detectors)
        self._identity: tuple[str, str | None] | None = None
        self._live_baseline_ready = False
        self._blocked_detectors: set[str] = set()

    def reset(self) -> None:
        for detector in self.detectors:
            detector.reset()
        self._identity = None
        self._live_baseline_ready = False
        self._blocked_detectors.clear()

    def feed(
        self,
        previous: tuple[WowsSnapshot, WowsFacts] | None,
        current: tuple[WowsSnapshot, WowsFacts],
        *,
        cfg,
    ) -> FeedResult:
        snapshot, facts = current
        identity_reset = self._identity is not None and snapshot.identity != self._identity
        first_frame = self._identity is None
        if identity_reset or first_frame:
            for detector in self.detectors:
                detector.reset()
            self._identity = snapshot.identity
            self._live_baseline_ready = False
            self._blocked_detectors.clear()

        # A comparable pair needs two consecutive live frames with the same
        # identity. Anything else can only be used to prime state.
        comparable = (
            previous is not None
            and not identity_reset
            and not first_frame
            and previous[0].is_live
            and snapshot.is_live
            and self._live_baseline_ready
        )
        if snapshot.is_live:
            self._live_baseline_ready = True

        events: list[GameEvent] = []
        blocked: list[BlockedDetector] = []
        recovered_any = False
        for detector in self.detectors:
            missing = snapshot.missing_domains(detector.required)
            if missing:
                if detector.name not in self._blocked_detectors:
                    detector.reset()
                    self._blocked_detectors.add(detector.name)
                blocked.append(BlockedDetector(
                    detector=detector.name,
                    missing=missing,
                    events=detector.events,
                ))
                continue

            recovered = detector.name in self._blocked_detectors
            if recovered:
                self._blocked_detectors.remove(detector.name)
                recovered_any = True
            baseline_only = detector.needs_live and (not comparable or recovered)
            context = DetectorContext(
                now=facts.at,
                baseline_only=baseline_only,
                identity_reset=identity_reset or first_frame,
                cfg=cfg,
            )
            events.extend(detector.feed(previous, current, context))

        return FeedResult(
            events=tuple(events),
            blocked=tuple(blocked),
            baseline_only=not comparable or recovered_any,
            identity_reset=identity_reset,
        )


__all__ = [
    "BlockedDetector",
    "Detector",
    "DetectorContext",
    "DetectorRegistry",
    "FeedResult",
    "GameEvent",
]
