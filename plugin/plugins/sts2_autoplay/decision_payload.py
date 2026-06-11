from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DecisionCandidate:
    action_id: str
    action_type: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    priority: float = 0.0
    source: str = "program_candidate"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "kwargs": dict(self.kwargs),
            "priority": float(self.priority),
            "source": self.source,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class DecisionPayload:
    mode: str
    screen_type: str
    state_name: str
    summary_kind: str
    state_signature: str
    strategy_directives: dict[str, Any] = field(default_factory=dict)
    guidance: dict[str, Any] = field(default_factory=dict)
    instructions: list[dict[str, Any]] = field(default_factory=list)
    run_state: dict[str, Any] = field(default_factory=dict)
    tactical_signals: dict[str, Any] = field(default_factory=dict)
    legal_actions: list[dict[str, Any]] = field(default_factory=list)
    candidate_actions: list[dict[str, Any]] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "screen_type": self.screen_type,
            "state_name": self.state_name,
            "summary_kind": self.summary_kind,
            "state_signature": self.state_signature,
            "strategy_directives": dict(self.strategy_directives),
            "guidance": dict(self.guidance),
            "instructions": [dict(item) for item in self.instructions],
            "run_state": dict(self.run_state),
            "tactical_signals": dict(self.tactical_signals),
            "legal_actions": [dict(item) for item in self.legal_actions],
            "candidate_actions": [dict(item) for item in self.candidate_actions],
            "policy": dict(self.policy),
        }


__all__ = ["DecisionCandidate", "DecisionPayload"]
