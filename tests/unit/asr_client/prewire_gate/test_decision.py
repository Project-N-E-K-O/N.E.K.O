from __future__ import annotations

from dataclasses import dataclass

from main_logic.asr_client.prewire_gate.contracts import (
    PrewireDecisionState,
    SampleRange,
)
from main_logic.asr_client.prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    PrewireQualitySummary,
    PrewireScoreObservation,
    StrictPrewireEvidencePolicy,
)


@dataclass
class _Classifier:
    outcomes: dict[SampleRange, CalibratedIdentityOutcome]

    def classify(
        self, observation: PrewireScoreObservation
    ) -> CalibratedIdentityEvidence:
        return CalibratedIdentityEvidence(
            self.outcomes[observation.scoring_range],
            "test_calibration",
        )


def _observation(
    scoring_range: SampleRange,
    decision_range: SampleRange,
    *,
    continuous: bool = True,
    digest: str = "a" * 64,
) -> PrewireScoreObservation:
    return PrewireScoreObservation(
        scoring_range=scoring_range,
        decision_range=decision_range,
        raw_similarity=0.78,
        quality=PrewireQualitySummary(
            speech_samples=scoring_range.sample_count,
            rms=0.2,
            peak=0.5,
            near_silence=0.1,
            clipping=0.0,
            continuous=continuous,
        ),
        profile_generation="profile-v1",
        model_generation="campplus-v1",
        config_generation="config-v1",
        parameters_digest=digest,
    )


def test_missing_calibration_never_grants_upload() -> None:
    decision_range = SampleRange(4_000, 8_000)
    observation = _observation(SampleRange(0, 24_000), decision_range)

    decision = StrictPrewireEvidencePolicy(
        None, required_consistent_observations=2
    ).decide(
        decision_range,
        (observation,),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.UNAVAILABLE
    assert decision.reason == "calibration_package_unavailable"


def test_one_large_high_window_does_not_authorize_decision_range() -> None:
    decision_range = SampleRange(4_000, 8_000)
    first_range = SampleRange(0, 24_000)
    policy = StrictPrewireEvidencePolicy(
        _Classifier({first_range: CalibratedIdentityOutcome.OWNER}),
        required_consistent_observations=2,
    )

    decision = policy.decide(
        decision_range,
        (_observation(first_range, decision_range),),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.PENDING
    assert decision.reason == "additional_identity_evidence_required"


def test_duplicate_copy_of_one_window_is_not_independent_confirmation() -> None:
    decision_range = SampleRange(4_000, 8_000)
    scoring_range = SampleRange(0, 24_000)
    observation = _observation(scoring_range, decision_range)
    policy = StrictPrewireEvidencePolicy(
        _Classifier({scoring_range: CalibratedIdentityOutcome.OWNER}),
        required_consistent_observations=2,
    )

    decision = policy.decide(
        decision_range,
        (observation, observation),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.PENDING


def test_two_distinct_supporting_windows_can_keep_only_their_shared_range() -> None:
    decision_range = SampleRange(8_000, 16_000)
    left = SampleRange(0, 24_000)
    right = SampleRange(8_000, 32_000)
    policy = StrictPrewireEvidencePolicy(
        _Classifier(
            {
                left: CalibratedIdentityOutcome.OWNER,
                right: CalibratedIdentityOutcome.OWNER,
            }
        ),
        required_consistent_observations=2,
    )

    decision = policy.decide(
        decision_range,
        (_observation(left, decision_range), _observation(right, decision_range)),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.KEEP
    assert decision.parameters_digest == "a" * 64


def test_low_then_high_remains_uncertain_instead_of_becoming_nonowner() -> None:
    decision_range = SampleRange(8_000, 16_000)
    left = SampleRange(0, 24_000)
    right = SampleRange(8_000, 32_000)
    policy = StrictPrewireEvidencePolicy(
        _Classifier(
            {
                left: CalibratedIdentityOutcome.NONOWNER,
                right: CalibratedIdentityOutcome.OWNER,
            }
        ),
        required_consistent_observations=2,
    )

    decision = policy.decide(
        decision_range,
        (_observation(left, decision_range), _observation(right, decision_range)),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.UNCERTAIN
    assert decision.reason == "conflicting_identity_evidence"


def test_only_ended_trusted_independent_micro_event_is_discarded_by_duration() -> None:
    short_range = SampleRange(0, 3_199)
    policy = StrictPrewireEvidencePolicy(None, required_consistent_observations=2)

    dropped = policy.decide(
        short_range,
        (),
        event_ended=True,
        boundary_trusted=True,
        independent_event=True,
    )
    normal_start = policy.decide(
        short_range,
        (),
        event_ended=False,
        boundary_trusted=True,
        independent_event=False,
    )

    assert dropped.state is PrewireDecisionState.DROP
    assert dropped.used_ended_micro_event_rule
    assert normal_start.state is PrewireDecisionState.PENDING


def test_discontinuity_or_version_change_is_stale() -> None:
    decision_range = SampleRange(8_000, 16_000)
    left = SampleRange(0, 24_000)
    right = SampleRange(8_000, 32_000)
    classifier = _Classifier(
        {
            left: CalibratedIdentityOutcome.OWNER,
            right: CalibratedIdentityOutcome.OWNER,
        }
    )
    policy = StrictPrewireEvidencePolicy(classifier, required_consistent_observations=2)

    discontinuous = policy.decide(
        decision_range,
        (_observation(left, decision_range, continuous=False),),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )
    version_changed = policy.decide(
        decision_range,
        (
            _observation(left, decision_range),
            _observation(right, decision_range, digest="b" * 64),
        ),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
    )

    assert discontinuous.state is PrewireDecisionState.STALE
    assert version_changed.state is PrewireDecisionState.STALE


def test_deadline_resolves_single_observation_as_uncertain() -> None:
    decision_range = SampleRange(4_000, 8_000)
    scoring_range = SampleRange(0, 24_000)
    policy = StrictPrewireEvidencePolicy(
        _Classifier({scoring_range: CalibratedIdentityOutcome.OWNER}),
        required_consistent_observations=2,
    )

    decision = policy.decide(
        decision_range,
        (_observation(scoring_range, decision_range),),
        event_ended=False,
        boundary_trusted=False,
        independent_event=False,
        deadline_expired=True,
    )

    assert decision.state is PrewireDecisionState.UNCERTAIN


def test_ended_range_without_a_score_is_unavailable_not_a_scored_uncertain() -> None:
    decision_range = SampleRange(0, 4_000)

    decision = StrictPrewireEvidencePolicy(
        None, required_consistent_observations=2
    ).decide(
        decision_range,
        (),
        event_ended=True,
        boundary_trusted=False,
        independent_event=False,
    )

    assert decision.state is PrewireDecisionState.UNAVAILABLE
    assert decision.parameters_digest is None
