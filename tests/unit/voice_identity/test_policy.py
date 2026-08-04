from __future__ import annotations

import pytest

from main_logic.voice_identity.policy import (
    OwnerVoiceBetaPolicy,
    OwnerVoicePolicyDecision,
)


def _observe(
    policy: OwnerVoiceBetaPolicy,
    checkpoint_ms: int,
    similarity: float,
    *,
    detector_epoch: int = 8,
    candidate_generation: int = 3,
    profile_generation: str = "profile-7",
    active_detector_epoch: int = 8,
    active_candidate_generation: int = 3,
    active_profile_generation: str = "profile-7",
    enabled: bool = True,
):
    return policy.observe(
        detector_epoch=detector_epoch,
        candidate_generation=candidate_generation,
        profile_generation=profile_generation,
        active_detector_epoch=active_detector_epoch,
        active_candidate_generation=active_candidate_generation,
        active_profile_generation=active_profile_generation,
        checkpoint_ms=checkpoint_ms,
        similarity=similarity,
        enabled=enabled,
    )


def test_beta_v1_requires_two_strictly_low_observations() -> None:
    policy = OwnerVoiceBetaPolicy()

    first = _observe(policy, 1_500, 0.20)
    second = _observe(policy, 3_000, 0.39)

    assert first.decision == OwnerVoicePolicyDecision.FORWARD
    assert first.reason == "awaiting_second_low_observation"
    assert second.decision == OwnerVoicePolicyDecision.HYPOTHETICAL_REJECT
    assert second.reason == "stable_clear_mismatch"
    assert second.policy_version == "beta-v1"
    assert policy.pending_candidate_count == 0


@pytest.mark.parametrize(
    ("first_score", "second_score"),
    (
        (0.20, 0.60),
        (0.60, 0.20),
        (0.40, 0.20),
        (0.20, 0.40),
    ),
)
def test_beta_v1_forwards_uncertain_boundary_and_fluctuating_scores(
    first_score: float,
    second_score: float,
) -> None:
    policy = OwnerVoiceBetaPolicy()

    _observe(policy, 1_500, first_score)
    result = _observe(policy, 3_000, second_score)

    assert result.decision == OwnerVoicePolicyDecision.FORWARD


@pytest.mark.parametrize(
    "overrides",
    (
        {"active_detector_epoch": 9},
        {"active_candidate_generation": 4},
        {"active_profile_generation": "profile-8"},
        {"enabled": False},
    ),
)
def test_beta_v1_forwards_disabled_and_stale_generations(overrides) -> None:
    policy = OwnerVoiceBetaPolicy()

    result = _observe(policy, 1_500, 0.10, **overrides)

    assert result.decision == OwnerVoicePolicyDecision.FORWARD
    assert policy.pending_candidate_count == 0


@pytest.mark.parametrize(
    ("checkpoint_ms", "similarity"),
    (
        (1_499, 0.10),
        (1_501, 0.10),
        (3_001, 0.10),
        (1_500, float("nan")),
        (1_500, float("inf")),
        (1_500, -1.01),
        (1_500, 1.01),
    ),
)
def test_beta_v1_forwards_invalid_or_non_checkpoint_observations(
    checkpoint_ms: int,
    similarity: float,
) -> None:
    result = _observe(OwnerVoiceBetaPolicy(), checkpoint_ms, similarity)

    assert result.decision == OwnerVoicePolicyDecision.FORWARD
    assert result.reason == "invalid_observation"


def test_beta_v1_requires_the_first_checkpoint() -> None:
    result = _observe(OwnerVoiceBetaPolicy(), 3_000, 0.10)

    assert result.decision == OwnerVoicePolicyDecision.FORWARD


def test_beta_v1_does_not_join_checkpoints_across_detector_epochs() -> None:
    policy = OwnerVoiceBetaPolicy()

    first = _observe(
        policy,
        1_500,
        0.10,
        detector_epoch=8,
        active_detector_epoch=8,
        candidate_generation=0,
        active_candidate_generation=0,
    )
    second = _observe(
        policy,
        3_000,
        0.10,
        detector_epoch=9,
        active_detector_epoch=9,
        candidate_generation=0,
        active_candidate_generation=0,
    )

    assert first.decision == OwnerVoicePolicyDecision.FORWARD
    assert second.decision == OwnerVoicePolicyDecision.FORWARD


def test_beta_v1_candidate_cache_is_bounded_and_evicts_the_oldest() -> None:
    policy = OwnerVoiceBetaPolicy(candidate_capacity=2)
    for generation in (1, 2, 3):
        _observe(
            policy,
            1_500,
            0.10,
            candidate_generation=generation,
            active_candidate_generation=generation,
        )

    oldest = _observe(
        policy,
        3_000,
        0.10,
        candidate_generation=1,
        active_candidate_generation=1,
    )
    newest = _observe(
        policy,
        3_000,
        0.10,
        candidate_generation=3,
        active_candidate_generation=3,
    )

    assert oldest.decision == OwnerVoicePolicyDecision.FORWARD
    assert newest.decision == OwnerVoicePolicyDecision.HYPOTHETICAL_REJECT


def test_beta_v1_forget_and_reset_only_drop_policy_state() -> None:
    policy = OwnerVoiceBetaPolicy()
    _observe(policy, 1_500, 0.10)
    policy.forget_candidate(
        detector_epoch=8,
        candidate_generation=3,
        profile_generation="profile-7",
    )
    assert policy.pending_candidate_count == 0

    _observe(policy, 1_500, 0.10)
    policy.reset()
    assert policy.pending_candidate_count == 0


def test_beta_v1_rejects_unbounded_cache_configuration() -> None:
    with pytest.raises(ValueError, match="candidate_capacity"):
        OwnerVoiceBetaPolicy(candidate_capacity=0)
