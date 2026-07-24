"""Pure participation policy for NEKO Live co-stream interactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts_types import LiveMode
from .host_turn import HostTurnSignal

ActivationMode = Literal["off", "conditional_auto"]
ParticipationLevel = Literal["L0", "L1", "L2", "L3"]
InteractionDecisionKind = Literal["allow", "defer", "skip", "downgrade"]

ALL_CO_STREAM_POLICY_REASONS = frozenset(
    {
        "co_stream.policy.solo_passthrough",
        "co_stream.policy.capability_off",
        "co_stream.policy.host_speaking",
        "co_stream.policy.host_holding",
        "co_stream.policy.turn_yielded",
        "co_stream.policy.turn_unknown",
        "co_stream.policy.host_support_only",
        "co_stream.policy.nonverbal_safe",
    }
)


@dataclass(frozen=True)
class LiveInteractionCandidate:
    live_mode: LiveMode
    capability_id: str
    activation: ActivationMode
    requested_level: ParticipationLevel
    priority: int


@dataclass(frozen=True)
class LiveInteractionDecision:
    decision: InteractionDecisionKind
    participation_level: ParticipationLevel
    reason_code: str
    activation: ActivationMode
    priority: int


class LiveInteractionPolicy:
    """Decide participation without reading config or producing output."""

    def decide(
        self,
        candidate: LiveInteractionCandidate,
        host_turn: HostTurnSignal,
    ) -> LiveInteractionDecision:
        priority = max(0, min(9, candidate.priority))

        if candidate.live_mode == "solo_stream":
            return self._result(
                candidate,
                priority=priority,
                decision="allow",
                level=candidate.requested_level,
                reason="co_stream.policy.solo_passthrough",
            )

        if candidate.activation == "off":
            return self._result(
                candidate,
                priority=priority,
                decision="skip",
                level=candidate.requested_level,
                reason="co_stream.policy.capability_off",
            )

        if candidate.requested_level in {"L0", "L1"}:
            return self._result(
                candidate,
                priority=priority,
                decision="allow",
                level=candidate.requested_level,
                reason="co_stream.policy.nonverbal_safe",
            )

        if candidate.requested_level == "L2":
            return self._result(
                candidate,
                priority=priority,
                decision="allow",
                level="L2",
                reason="co_stream.policy.host_support_only",
            )

        if host_turn.reliability != "reliable":
            return self._unknown_turn_result(candidate, priority=priority)
        if host_turn.state == "speaking":
            return self._result(
                candidate,
                priority=priority,
                decision="defer",
                level="L3",
                reason="co_stream.policy.host_speaking",
            )
        if host_turn.state == "likely_holding":
            return self._result(
                candidate,
                priority=priority,
                decision="defer",
                level="L3",
                reason="co_stream.policy.host_holding",
            )
        if host_turn.state == "yielded":
            return self._result(
                candidate,
                priority=priority,
                decision="allow",
                level="L3",
                reason="co_stream.policy.turn_yielded",
            )
        return self._unknown_turn_result(candidate, priority=priority)

    @classmethod
    def _unknown_turn_result(
        cls,
        candidate: LiveInteractionCandidate,
        *,
        priority: int,
    ) -> LiveInteractionDecision:
        return cls._result(
            candidate,
            priority=priority,
            decision="downgrade",
            level="L2",
            reason="co_stream.policy.turn_unknown",
        )

    @staticmethod
    def _result(
        candidate: LiveInteractionCandidate,
        *,
        priority: int,
        decision: InteractionDecisionKind,
        level: ParticipationLevel,
        reason: str,
    ) -> LiveInteractionDecision:
        return LiveInteractionDecision(
            decision=decision,
            participation_level=level,
            reason_code=reason,
            activation=candidate.activation,
            priority=priority,
        )


__all__ = [
    "ALL_CO_STREAM_POLICY_REASONS",
    "ActivationMode",
    "InteractionDecisionKind",
    "LiveInteractionCandidate",
    "LiveInteractionDecision",
    "LiveInteractionPolicy",
    "ParticipationLevel",
]
