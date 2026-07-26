import pytest

from plugin.plugins.neko_live.core.host_turn import HostTurnSignal
from plugin.plugins.neko_live.core.live_interaction_policy import (
    ALL_CO_STREAM_POLICY_REASONS,
    LiveInteractionCandidate,
    LiveInteractionDecision,
    LiveInteractionPolicy,
)


def _signal(
    state: str,
    *,
    reliability: str = "reliable",
    confidence: float = 1.0,
) -> HostTurnSignal:
    return HostTurnSignal(
        state=state,  # type: ignore[arg-type]
        confidence=confidence,
        reliability=reliability,  # type: ignore[arg-type]
        observed_at=42.0,
        source="host_runtime",
    )


@pytest.mark.parametrize(
    ("signal", "activation"),
    [
        (_signal("speaking"), "off"),
        (_signal("yielded"), "conditional_auto"),
        (_signal("unknown", reliability="unavailable", confidence=0.0), "off"),
    ],
)
def test_solo_stream_always_passes_through_without_policy_changes(
    signal: HostTurnSignal,
    activation: str,
) -> None:
    decision = LiveInteractionPolicy().decide(
        LiveInteractionCandidate(
            live_mode="solo_stream",
            capability_id="active_engagement",
            activation=activation,  # type: ignore[arg-type]
            requested_level="L3",
            priority=12,
        ),
        signal,
    )

    assert decision == LiveInteractionDecision(
        decision="allow",
        participation_level="L3",
        reason_code="co_stream.policy.solo_passthrough",
        activation=activation,
        priority=9,
    )


@pytest.mark.parametrize(
    ("candidate", "signal", "expected"),
    [
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="verified_support_reply",
                activation="off",
                requested_level="L2",
                priority=5,
            ),
            _signal("yielded"),
            ("skip", "L2", "co_stream.policy.capability_off"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="named_reply",
                activation="conditional_auto",
                requested_level="L3",
                priority=8,
            ),
            _signal("speaking"),
            ("defer", "L3", "co_stream.policy.host_speaking"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="named_reply",
                activation="conditional_auto",
                requested_level="L3",
                priority=8,
            ),
            _signal("likely_holding"),
            ("defer", "L3", "co_stream.policy.host_holding"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="named_reply",
                activation="conditional_auto",
                requested_level="L3",
                priority=8,
            ),
            _signal("yielded"),
            ("allow", "L3", "co_stream.policy.turn_yielded"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="named_reply",
                activation="conditional_auto",
                requested_level="L3",
                priority=8,
            ),
            _signal("unknown", reliability="unavailable", confidence=0.0),
            ("downgrade", "L2", "co_stream.policy.turn_unknown"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="host_support",
                activation="conditional_auto",
                requested_level="L2",
                priority=-4,
            ),
            _signal("unknown", reliability="unavailable", confidence=0.0),
            ("allow", "L2", "co_stream.policy.host_support_only"),
        ),
        (
            LiveInteractionCandidate(
                live_mode="co_stream",
                capability_id="visual_reaction",
                activation="conditional_auto",
                requested_level="L1",
                priority=3,
            ),
            _signal("unknown", reliability="unavailable", confidence=0.0),
            ("allow", "L1", "co_stream.policy.nonverbal_safe"),
        ),
    ],
)
def test_co_stream_policy_matrix(
    candidate: LiveInteractionCandidate,
    signal: HostTurnSignal,
    expected: tuple[str, str, str],
) -> None:
    decision = LiveInteractionPolicy().decide(candidate, signal)

    assert (
        decision.decision,
        decision.participation_level,
        decision.reason_code,
    ) == expected
    assert 0 <= decision.priority <= 9


def test_reason_codes_are_a_stable_complete_contract() -> None:
    assert ALL_CO_STREAM_POLICY_REASONS == frozenset(
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


def test_shared_contract_facade_exports_policy_value_types() -> None:
    from plugin.plugins.neko_live.core.contracts import (
        HostTurnSignal as FacadeHostTurnSignal,
        LiveInteractionCandidate as FacadeCandidate,
        LiveInteractionDecision as FacadeDecision,
    )

    assert FacadeHostTurnSignal is HostTurnSignal
    assert FacadeCandidate is LiveInteractionCandidate
    assert FacadeDecision is LiveInteractionDecision
