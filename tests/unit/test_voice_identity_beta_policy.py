from __future__ import annotations

import pytest

from main_logic.voice_identity.beta_policy import (
    OwnerVoiceBetaConfig,
    OwnerVoiceBetaDecision,
    OwnerVoiceBetaPolicy,
    OwnerVoiceCandidateIdentity,
)
from main_logic.voice_identity.contracts import SpeakerShadowObservation


def _identity(*, revision: int = 7) -> OwnerVoiceCandidateIdentity:
    return OwnerVoiceCandidateIdentity(
        session_id="voice-session-1",
        detector_epoch=4,
        candidate_generation=9,
        candidate_scope="provider_pause",
        profile_revision=revision,
    )


def _observation(
    audio_ms: int,
    similarity: float,
    *,
    would_block: tuple[tuple[float, bool], ...] = (),
) -> SpeakerShadowObservation:
    identity = _identity()
    candidate = type(
        "Candidate",
        (),
        {
            "detector_epoch": identity.detector_epoch,
            "shadow_generation": identity.candidate_generation,
            "scope": identity.candidate_scope,
        },
    )()
    return SpeakerShadowObservation(
        candidate=candidate,
        similarity=similarity,
        would_block=would_block,
        audio_ms=audio_ms,
    )


def test_beta_policy_requires_two_stable_low_observations() -> None:
    policy = OwnerVoiceBetaPolicy()
    identity = _identity()

    first = policy.observe(
        _observation(1_500, 0.20),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )
    second = policy.observe(
        _observation(3_000, 0.25),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )

    assert first.decision is OwnerVoiceBetaDecision.FORWARD
    assert first.reason == "awaiting_second_low_observation"
    assert second.decision is OwnerVoiceBetaDecision.REJECT_CURRENT_CANDIDATE
    assert second.reason == "stable_clear_mismatch"
    assert policy.snapshot()["hypothetical_reject_count"] == 1


@pytest.mark.parametrize(
    ("first_score", "second_score"),
    (
        (0.20, 0.60),
        (0.60, 0.20),
        (0.40, 0.20),
        (0.20, 0.40),
    ),
)
def test_beta_policy_forwards_owner_uncertain_and_fluctuating_scores(
    first_score: float,
    second_score: float,
) -> None:
    policy = OwnerVoiceBetaPolicy()
    identity = _identity()

    policy.observe(
        _observation(1_500, first_score),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )
    decision = policy.observe(
        _observation(3_000, second_score),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )

    assert decision.decision is OwnerVoiceBetaDecision.FORWARD


def test_beta_policy_forwards_short_disabled_and_stale_profile_observations() -> None:
    policy = OwnerVoiceBetaPolicy()
    identity = _identity()

    short = policy.observe(
        _observation(1_499, 0.10),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )
    disabled = policy.observe(
        _observation(1_500, 0.10),
        identity=identity,
        enabled=False,
        active_profile_revision=7,
    )
    stale = policy.observe(
        _observation(3_000, 0.10),
        identity=identity,
        enabled=True,
        active_profile_revision=8,
    )

    assert short.decision is OwnerVoiceBetaDecision.FORWARD
    assert short.reason == "insufficient_audio"
    assert disabled.decision is OwnerVoiceBetaDecision.FORWARD
    assert disabled.reason == "filter_disabled"
    assert stale.decision is OwnerVoiceBetaDecision.FORWARD
    assert stale.reason == "profile_revision_changed"
    assert policy.snapshot()["hypothetical_reject_count"] == 0


def test_beta_policy_does_not_treat_legacy_would_block_as_authority() -> None:
    policy = OwnerVoiceBetaPolicy()
    identity = _identity()

    first = policy.observe(
        _observation(1_500, 0.90, would_block=((0.40, True),)),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )
    second = policy.observe(
        _observation(3_000, 0.90, would_block=((0.40, True),)),
        identity=identity,
        enabled=True,
        active_profile_revision=7,
    )

    assert first.decision is OwnerVoiceBetaDecision.FORWARD
    assert second.decision is OwnerVoiceBetaDecision.FORWARD
    assert policy.snapshot()["hypothetical_reject_count"] == 0


def test_beta_policy_config_is_versioned_and_bounded() -> None:
    config = OwnerVoiceBetaConfig()

    assert config.version == "beta-v1"
    assert config.similarity_threshold == pytest.approx(0.40)
    assert config.first_observation_ms == 1_500
    assert config.second_observation_ms == 3_000

    with pytest.raises(ValueError, match="second_observation_ms"):
        OwnerVoiceBetaConfig(second_observation_ms=1_500)
    with pytest.raises(ValueError, match="similarity_threshold"):
        OwnerVoiceBetaConfig(similarity_threshold=1.1)
