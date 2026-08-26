import math

import pytest

from main_logic.asr_client.speaker_shadow.contracts import SpeakerShadowCandidateKey
from main_logic.voice_identity_service.policy import (
    OwnerVoiceDecision,
    OwnerVoicePolicy,
)


def _candidate(generation: int = 1) -> SpeakerShadowCandidateKey:
    return SpeakerShadowCandidateKey(2, generation, "provider_candidate")


def test_policy_requires_two_strictly_low_checkpoints_to_reject() -> None:
    policy = OwnerVoicePolicy()
    candidate = _candidate()

    first = policy.observe(
        candidate=candidate,
        checkpoint_ms=1_500,
        similarity=0.39,
        enforce=True,
    )
    second = policy.observe(
        candidate=candidate,
        checkpoint_ms=3_000,
        similarity=0.39,
        enforce=True,
    )

    assert first.decision is OwnerVoiceDecision.FORWARD
    assert second.decision is OwnerVoiceDecision.REJECT
    assert policy.pending_candidate_count == 0


@pytest.mark.parametrize(
    "first,second",
    [(0.40, 0.39), (0.39, 0.40), (0.9, 0.1), (0.1, 0.9)],
)
def test_policy_forwards_owner_or_uncertain_observations(
    first: float,
    second: float,
) -> None:
    policy = OwnerVoicePolicy()
    candidate = _candidate()

    policy.observe(
        candidate=candidate,
        checkpoint_ms=1_500,
        similarity=first,
        enforce=True,
    )
    result = policy.observe(
        candidate=candidate,
        checkpoint_ms=3_000,
        similarity=second,
        enforce=True,
    )

    assert result.decision is OwnerVoiceDecision.FORWARD


def test_policy_shadow_mode_never_rejects() -> None:
    policy = OwnerVoicePolicy()
    candidate = _candidate()
    policy.observe(
        candidate=candidate,
        checkpoint_ms=1_500,
        similarity=-0.5,
        enforce=False,
    )

    result = policy.observe(
        candidate=candidate,
        checkpoint_ms=3_000,
        similarity=-0.5,
        enforce=False,
    )

    assert result.decision is OwnerVoiceDecision.FORWARD
    assert result.reason == "shadow_only"


@pytest.mark.parametrize(
    "checkpoint,similarity",
    [(None, 0.1), (2_000, 0.1), (1_500, math.nan), (1_500, math.inf)],
)
def test_policy_invalid_or_missing_observation_fails_open(
    checkpoint: int | None,
    similarity: float,
) -> None:
    result = OwnerVoicePolicy().observe(
        candidate=_candidate(),
        checkpoint_ms=checkpoint,
        similarity=similarity,
        enforce=True,
    )

    assert result.decision is OwnerVoiceDecision.FORWARD
    assert result.reason == "invalid_observation"


def test_policy_bounds_and_can_forget_candidate_state() -> None:
    policy = OwnerVoicePolicy(candidate_capacity=2)
    candidates = [_candidate(index) for index in range(3)]
    for candidate in candidates:
        policy.observe(
            candidate=candidate,
            checkpoint_ms=1_500,
            similarity=0.1,
            enforce=True,
        )

    assert policy.pending_candidate_count == 2
    policy.forget(candidates[-1])
    assert policy.pending_candidate_count == 1
    policy.reset()
    assert policy.pending_candidate_count == 0
