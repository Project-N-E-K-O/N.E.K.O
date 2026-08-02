"""Picks at most one call-out per round, and explains why.

The queue is the only place preemption happens. Once a candidate has been handed
to the host, this module makes no claim about being able to take it back -- the
generation or the voice line may already be underway.

Failure handling is asymmetric on purpose:

* the per-event cooldown is recorded even when delivery failed, so a failure
  cannot turn into a retry loop (each candidate is attempted exactly once);
* `once_per_battle` is only marked as spent when something was actually said, so
  a swallowed sinking call-out can still fire later in the same battle;
* the lane gap is *not* advanced on failure, because its whole purpose is to
  pace what the user hears and nothing was heard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.contracts import (
    ALL_LANES,
    INTRUSION_ALLOW_INTERRUPT,
    INTRUSION_NO_INTERRUPT,
    LANE_URGENT,
)
from .tactic_policy import AdviceCandidate

REASON_CHOSEN = "chosen"
REASON_EXPIRED = "expired"
REASON_COOLDOWN = "cooldown"
REASON_ONCE_PER_BATTLE = "once_per_battle"
REASON_LANE_GAP = "lane_gap"
REASON_QUEUED = "queued"
REASON_COALESCED = "coalesced"
REASON_PREEMPTED = "preempted"
REASON_PAUSED = "paused"
REASON_EMPTY = "no_candidates"
REASON_QUIET_WINDOW = "quiet_window"

# Dispatcher outcomes that consumed the candidate. Anything else leaves
# `once_per_battle` unspent and the lane gap untouched.
COMMITTED_REASONS = frozenset({"delivered", "dry_run"})


@dataclass(frozen=True)
class DecisionStep:
    """One line of the audit trail shown in the panel timeline."""

    event_id: str
    lane: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class ArbiterDecision:
    chosen: AdviceCandidate | None
    chain: tuple[DecisionStep, ...] = ()
    queued: int = 0

    @property
    def reason(self) -> str:
        if self.chosen is not None:
            return REASON_CHOSEN
        for step in reversed(self.chain):
            if step.outcome != REASON_QUEUED:
                return step.outcome
        return REASON_EMPTY


@dataclass
class _LaneState:
    last_output_at: float = 0.0


class Arbiter:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._queue: list[AdviceCandidate] = []
        self._cooldowns: dict[str, float] = {}
        self._fired_once: set[str] = set()
        self._lanes: dict[str, _LaneState] = {lane: _LaneState() for lane in ALL_LANES}
        self._paused = False
        self._battle_id: str | None = None
        # Monotonic deadline until which the user is considered mid-conversation.
        self._quiet_until = 0.0

    # ------------------------------------------------------------------
    def apply_config(self, cfg) -> None:
        self.cfg = cfg

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def note_user_activity(self, at: float) -> None:
        """Start the quiet window; the user just said something."""
        self._quiet_until = at + max(0.0, self.cfg.user_chat_quiet_window_seconds)

    def clear_quiet_window(self) -> None:
        self._quiet_until = 0.0

    def quiet_until(self) -> float:
        return self._quiet_until

    def reset_battle(self, battle_id: str | None) -> None:
        self._battle_id = battle_id
        self._queue.clear()
        self._fired_once.clear()
        self._cooldowns.clear()

    def clear_shadow_state(self) -> None:
        """Drop cooldowns accumulated while dry-run was suppressing output.

        Shadow cooldowns exist so the dry-run chain looks realistic. They must
        not carry over into the first real round, or switching output on would
        start behind a wall of throttles nobody ever heard.
        """
        self._queue.clear()
        self._cooldowns.clear()
        self._fired_once.clear()
        for lane in self._lanes.values():
            lane.last_output_at = 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "queued": len(self._queue),
            "paused": self._paused,
            "cooldowns": len(self._cooldowns),
            "fired_once_per_battle": sorted(self._fired_once),
            "lanes": {
                lane: round(state.last_output_at, 2)
                for lane, state in self._lanes.items()
            },
            "quiet_until": round(self._quiet_until, 2),
            "intrusion_mode": self.cfg.dialogue_intrusion_mode,
        }

    # ------------------------------------------------------------------
    def submit(self, candidates: Sequence[AdviceCandidate], now: float) -> list[DecisionStep]:
        """Queue new candidates, collapsing by coalesce key and honouring preempt."""
        steps: list[DecisionStep] = []
        for candidate in candidates:
            if candidate.is_expired(now):
                steps.append(DecisionStep(
                    candidate.event_id, candidate.lane, REASON_EXPIRED,
                    "expired before it reached the queue"))
                continue

            key = candidate.coalesce_key
            if key:
                superseded = [c for c in self._queue if c.coalesce_key == key]
                if superseded:
                    self._queue = [c for c in self._queue if c.coalesce_key != key]
                    for old in superseded:
                        steps.append(DecisionStep(
                            old.event_id, old.lane, REASON_COALESCED,
                            f"replaced by newer {candidate.event_id}"))

            if candidate.spec.preempt:
                dropped = [c for c in self._queue if c.priority < candidate.priority]
                if dropped:
                    self._queue = [c for c in self._queue if c.priority >= candidate.priority]
                    for old in dropped:
                        steps.append(DecisionStep(
                            old.event_id, old.lane, REASON_PREEMPTED,
                            f"preempted by {candidate.event_id}"))

            self._queue.append(candidate)
            steps.append(DecisionStep(candidate.event_id, candidate.lane, REASON_QUEUED))
        return steps

    def decide(
        self,
        candidates: Sequence[AdviceCandidate],
        now: float,
    ) -> ArbiterDecision:
        """Submit, then return the single best eligible candidate, if any."""
        steps = list(self.submit(candidates, now))

        if self._paused:
            return ArbiterDecision(None, tuple(steps + [
                DecisionStep("", "", REASON_PAUSED, "output paused")
            ]), queued=len(self._queue))

        expired = [c for c in self._queue if c.is_expired(now)]
        if expired:
            self._queue = [c for c in self._queue if not c.is_expired(now)]
            for candidate in expired:
                steps.append(DecisionStep(
                    candidate.event_id, candidate.lane, REASON_EXPIRED,
                    "TTL elapsed while queued"))

        for candidate in sorted(self._queue, key=lambda c: c.rank):
            blocked = self._blocked_reason(candidate, now)
            if blocked is None:
                self._queue.remove(candidate)
                steps.append(DecisionStep(
                    candidate.event_id, candidate.lane, REASON_CHOSEN))
                return ArbiterDecision(candidate, tuple(steps), queued=len(self._queue))
            steps.append(DecisionStep(
                candidate.event_id, candidate.lane, blocked[0], blocked[1]))

        return ArbiterDecision(None, tuple(steps), queued=len(self._queue))

    # ------------------------------------------------------------------
    def commit(self, candidate: AdviceCandidate, now: float, *, outcome_reason: str) -> None:
        """Record the consequences of an attempt. See the module docstring.

        A dry-run counts as committed so the shadow chain throttles exactly like
        the real one; `clear_shadow_state` wipes that when output is turned on.
        """
        spec = candidate.spec
        self._cooldowns[candidate.event_id] = now + spec.cooldown_seconds
        if outcome_reason not in COMMITTED_REASONS:
            return
        if spec.once_per_battle:
            self._fired_once.add(candidate.event_id)
        lane = self._lanes.setdefault(candidate.lane, _LaneState())
        lane.last_output_at = now

    # ------------------------------------------------------------------
    def _blocked_reason(
        self,
        candidate: AdviceCandidate,
        now: float,
    ) -> tuple[str, str] | None:
        spec = candidate.spec
        quiet = self._quiet_window_block(candidate, now)
        if quiet is not None:
            return quiet

        if spec.once_per_battle and candidate.event_id in self._fired_once:
            return REASON_ONCE_PER_BATTLE, "already said once this battle"

        until = self._cooldowns.get(candidate.event_id, 0.0)
        if now < until:
            return REASON_COOLDOWN, f"{until - now:.1f}s remaining"

        lane = self._lanes.setdefault(candidate.lane, _LaneState())
        gap = self.cfg.min_gap_for(candidate.lane)
        if lane.last_output_at and (now - lane.last_output_at) < gap:
            waited = now - lane.last_output_at
            return REASON_LANE_GAP, f"{gap - waited:.1f}s until the {candidate.lane} lane reopens"
        return None

    def _quiet_window_block(
        self,
        candidate: AdviceCandidate,
        now: float,
    ) -> tuple[str, str] | None:
        """Hold back a call-out while the user is mid-conversation.

        The host applies its own short activity gate independently. This is the
        longer, user-tunable layer, and it names itself in the reason so the two
        cannot be confused when nothing gets said.
        """
        mode = self.cfg.dialogue_intrusion_mode
        if mode == INTRUSION_ALLOW_INTERRUPT:
            return None
        if not self._quiet_until or now >= self._quiet_until:
            return None
        if mode != INTRUSION_NO_INTERRUPT and candidate.lane == LANE_URGENT:
            return None
        remaining = self._quiet_until - now
        return (
            REASON_QUIET_WINDOW,
            f"插件静默窗口还有 {remaining:.1f}s（插话策略 {mode}）",
        )


__all__ = [
    "COMMITTED_REASONS",
    "REASON_CHOSEN",
    "REASON_COALESCED",
    "REASON_COOLDOWN",
    "REASON_EMPTY",
    "REASON_EXPIRED",
    "REASON_LANE_GAP",
    "REASON_ONCE_PER_BATTLE",
    "REASON_PAUSED",
    "REASON_PREEMPTED",
    "REASON_QUEUED",
    "REASON_QUIET_WINDOW",
    "Arbiter",
    "ArbiterDecision",
    "DecisionStep",
]
