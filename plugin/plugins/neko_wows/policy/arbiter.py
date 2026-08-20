"""Picks one call-out containing up to four ranked events, and explains why.

The queue is the only place preemption happens. Once a candidate has been handed
to the host, this module makes no claim about being able to take it back -- the
generation or the voice line may already be underway.

Outcome handling is asymmetric on purpose:

* a failed delivery starts a per-event cooldown so it cannot become a tight
  retry loop, but an explicit resume releases that failure-only cooldown;
* paused or expired delivery attempts do not consume a cooldown;
* `once_per_battle` and the lane gap advance only when output was committed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain.catalog import ENEMY_SUNK
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
REASON_ATTACHED = "attached"

ATTACH_PRIORITY_WINDOW = 15
MAX_DECISION_EVENTS = 4


def _coalesce_identity(candidate: AdviceCandidate) -> tuple[str, str | int] | None:
    """Collapse siblings that share a category and, when present, a target.

    Broadcast categories still group the panel switch, but two progress bursts
    on different ships must both be allowed to reach attach. Spoken hull names
    collide (two Zaos), so a numeric target_id wins when the detector stamped one.
    """
    key = candidate.coalesce_key
    if not key:
        return None
    target_id = candidate.detail.get("target_id")
    if isinstance(target_id, int) and not isinstance(target_id, bool):
        return (key, target_id)
    target = candidate.detail.get("target_name")
    if isinstance(target, str) and target:
        return (key, target)
    return (key, "")

# Dispatcher outcomes that consumed the candidate. Anything else leaves
# `once_per_battle` unspent and the lane gap untouched.
COMMITTED_REASONS = frozenset({"delivered", "dry_run"})
COOLDOWN_REASONS = frozenset({*COMMITTED_REASONS, "failed"})


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
    attached: tuple[AdviceCandidate, ...] = ()

    @property
    def candidates(self) -> tuple[AdviceCandidate, ...]:
        if self.chosen is None:
            return ()
        return (self.chosen, *self.attached)

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
        self._failure_cooldowns: set[str] = set()
        self._fired_once: set[str] = set()
        self._lanes: dict[str, _LaneState] = {lane: _LaneState() for lane in ALL_LANES}
        self._paused = False
        self._battle_id: str | None = None
        # Monotonic deadline until which the user is considered mid-conversation.
        self._quiet_until = 0.0

    # ------------------------------------------------------------------
    def apply_config(self, cfg) -> None:
        self.cfg = cfg
        # Preferences filter new candidates in TacticPolicy; already-queued
        # items must be dropped here or a just-disabled category/lane can still
        # deliver after a quiet window or lane gap clears.
        self._queue = [
            candidate
            for candidate in self._queue
            if (
                self.cfg.lane_enabled(candidate.lane)
                and self.cfg.category_enabled(candidate.coalesce_key)
            )
        ]

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        for event_id in self._failure_cooldowns:
            self._cooldowns.pop(event_id, None)
        self._failure_cooldowns.clear()

    def note_user_activity(self, at: float) -> None:
        """Start the quiet window; the user just said something."""
        self._quiet_until = at + max(0.0, self.cfg.user_chat_quiet_window_seconds)

    def clear_quiet_window(self) -> None:
        self._quiet_until = 0.0

    def clear_pending(self) -> int:
        """Drop candidates that have not reached the host yet."""
        count = len(self._queue)
        self._queue.clear()
        return count

    def quiet_until(self) -> float:
        return self._quiet_until

    def reset_battle(self, battle_id: str | None) -> None:
        self._battle_id = battle_id
        self._queue.clear()
        self._fired_once.clear()
        self._cooldowns.clear()
        self._failure_cooldowns.clear()
        self._quiet_until = 0.0
        for lane in self._lanes.values():
            lane.last_output_at = 0.0

    def clear_shadow_state(self) -> None:
        """Drop cooldowns accumulated while dry-run was suppressing output.

        Shadow cooldowns exist so the dry-run chain looks realistic. They must
        not carry over into the first real round, or switching output on would
        start behind a wall of throttles nobody ever heard.
        """
        self._queue.clear()
        self._cooldowns.clear()
        self._failure_cooldowns.clear()
        self._fired_once.clear()
        for lane in self._lanes.values():
            lane.last_output_at = 0.0

    def cancel_events(self, event_ids: Iterable[str]) -> int:
        """Remove queued candidates whose detector-side state is no longer valid."""
        cancelled = frozenset(event_ids)
        if not cancelled:
            return 0
        before = len(self._queue)
        self._queue = [
            candidate
            for candidate in self._queue
            if candidate.event_id not in cancelled
        ]
        return before - len(self._queue)

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
        eligible: list[AdviceCandidate] = []
        for candidate in candidates:
            if candidate.is_expired(now):
                steps.append(DecisionStep(
                    candidate.event_id, candidate.lane, REASON_EXPIRED,
                    "expired before it reached the queue"))
                continue
            eligible.append(candidate)

        incoming, collapsed = self._collapse_incoming(eligible)
        steps.extend(collapsed)
        active_preemptor: AdviceCandidate | None = None
        for candidate in incoming:
            blocked = self._blocked_reason(candidate, now)
            if (
                active_preemptor is not None
                and candidate.priority
                < active_preemptor.priority - ATTACH_PRIORITY_WINDOW
            ):
                steps.append(DecisionStep(
                    candidate.event_id,
                    candidate.lane,
                    REASON_PREEMPTED,
                    f"preempted by {active_preemptor.event_id}",
                ))
                continue

            identity = _coalesce_identity(candidate)
            if identity:
                siblings = [
                    queued
                    for queued in self._queue
                    if _coalesce_identity(queued) == identity
                ]
                if siblings:
                    best_queued = min(siblings, key=self._coalesce_rank)
                    if self._coalesce_rank(candidate) > self._coalesce_rank(best_queued):
                        steps.append(DecisionStep(
                            candidate.event_id,
                            candidate.lane,
                            REASON_COALESCED,
                            f"queued sibling kept {best_queued.event_id}",
                        ))
                        continue
                    self._queue = [
                        queued
                        for queued in self._queue
                        if _coalesce_identity(queued) != identity
                    ]
                    for old in siblings:
                        steps.append(DecisionStep(
                            old.event_id, old.lane, REASON_COALESCED,
                            f"replaced by stronger {candidate.event_id}"))

            if candidate.spec.preempt and blocked is None:
                if (
                    active_preemptor is None
                    or candidate.rank < active_preemptor.rank
                ):
                    active_preemptor = candidate
                attach_floor = candidate.priority - ATTACH_PRIORITY_WINDOW
                dropped = [c for c in self._queue if c.priority < attach_floor]
                if dropped:
                    self._queue = [c for c in self._queue if c.priority >= attach_floor]
                    for old in dropped:
                        steps.append(DecisionStep(
                            old.event_id, old.lane, REASON_PREEMPTED,
                            f"preempted by {candidate.event_id}"))

            self._queue.append(candidate)
            steps.append(DecisionStep(candidate.event_id, candidate.lane, REASON_QUEUED))
        return steps

    @staticmethod
    def _collapse_incoming(
        candidates: Sequence[AdviceCandidate],
    ) -> tuple[tuple[AdviceCandidate, ...], list[DecisionStep]]:
        """Keep the strongest sibling inside one arbitration round.

        Later rounds still replace older queued siblings in ``submit`` below;
        this pre-pass only prevents a weaker item later in one already-ranked
        batch from overwriting the stronger item that preceded it.
        """
        indexed = tuple(enumerate(candidates))
        groups: dict[tuple[str, str | int], list[tuple[int, AdviceCandidate]]] = {}
        for index, candidate in indexed:
            identity = _coalesce_identity(candidate)
            if identity:
                groups.setdefault(identity, []).append(
                    (index, candidate))

        discarded: set[int] = set()
        steps: list[DecisionStep] = []
        for siblings in groups.values():
            if len(siblings) < 2:
                continue
            winner_index, winner = min(
                siblings,
                key=lambda entry: (
                    *Arbiter._coalesce_rank(entry[1]),
                    entry[0],
                ),
            )
            for index, candidate in siblings:
                if index == winner_index:
                    continue
                discarded.add(index)
                steps.append(DecisionStep(
                    candidate.event_id,
                    candidate.lane,
                    REASON_COALESCED,
                    f"same-round sibling kept {winner.event_id}",
                ))

        retained = tuple(
            candidate for index, candidate in indexed if index not in discarded)
        return retained, steps

    @staticmethod
    def _coalesce_rank(candidate: AdviceCandidate) -> tuple[int, int, float, str]:
        return (
            -candidate.priority,
            -candidate.severity,
            -candidate.at,
            candidate.event_id,
        )

    def decide(
        self,
        candidates: Sequence[AdviceCandidate],
        now: float,
    ) -> ArbiterDecision:
        """Submit, then return one primary and nearby eligible attachments."""
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

        ranked = sorted(self._queue, key=lambda c: c.rank)
        for candidate in ranked:
            blocked = self._blocked_reason(candidate, now)
            if blocked is None:
                self._queue.remove(candidate)
                steps.append(DecisionStep(
                    candidate.event_id, candidate.lane, REASON_CHOSEN))
                attached: list[AdviceCandidate] = []
                for sibling in ranked:
                    if sibling is candidate:
                        continue
                    if sibling.rank < candidate.rank:
                        continue
                    in_window = (
                        sibling.priority
                        >= candidate.priority - ATTACH_PRIORITY_WINDOW
                    )
                    same_group = bool(
                        candidate.spec.attach_group
                        and sibling.spec.attach_group
                        == candidate.spec.attach_group
                        and ENEMY_SUNK in (candidate.event_id, sibling.event_id)
                    )
                    if not in_window and not same_group:
                        continue
                    if len(attached) >= MAX_DECISION_EVENTS - 1:
                        break
                    sibling_blocked = self._blocked_reason(sibling, now)
                    if sibling_blocked is not None:
                        steps.append(DecisionStep(
                            sibling.event_id,
                            sibling.lane,
                            sibling_blocked[0],
                            sibling_blocked[1],
                        ))
                        continue
                    self._queue.remove(sibling)
                    attached.append(sibling)
                    steps.append(DecisionStep(
                        sibling.event_id, sibling.lane, REASON_ATTACHED,
                        f"attached to {candidate.event_id}"))
                return ArbiterDecision(
                    candidate,
                    tuple(steps),
                    queued=len(self._queue),
                    attached=tuple(attached),
                )
            steps.append(DecisionStep(
                candidate.event_id, candidate.lane, blocked[0], blocked[1]))

        return ArbiterDecision(None, tuple(steps), queued=len(self._queue))

    # ------------------------------------------------------------------
    def commit(
        self,
        candidate: AdviceCandidate | Sequence[AdviceCandidate],
        now: float,
        *,
        outcome_reason: str,
    ) -> bool:
        """Record one bundled attempt and report whether output was committed.

        A dry-run counts as committed so the shadow chain throttles exactly like
        the real one; `clear_shadow_state` wipes that when output is turned on.
        """
        committed = outcome_reason in COMMITTED_REASONS
        candidates = (
            (candidate,)
            if isinstance(candidate, AdviceCandidate)
            else tuple(candidate)
        )
        for item in candidates:
            spec = item.spec
            if outcome_reason in COOLDOWN_REASONS:
                self._cooldowns[item.cooldown_key] = now + spec.cooldown_seconds
            if outcome_reason == "failed":
                self._failure_cooldowns.add(item.cooldown_key)
            elif committed:
                self._failure_cooldowns.discard(item.cooldown_key)

            if not committed:
                continue
            if spec.once_per_battle:
                self._fired_once.add(item.event_id)
            lane = self._lanes.setdefault(item.lane, _LaneState())
            lane.last_output_at = now
        return bool(candidates) and committed

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

        until = self._cooldowns.get(candidate.cooldown_key, 0.0)
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

        The host applies its own short activity gate independently. This
        user-tunable layer overlaps it by default, and names itself in the
        reason so the two cannot be confused when nothing gets said.
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
    "COOLDOWN_REASONS",
    "REASON_CHOSEN",
    "REASON_ATTACHED",
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
