from dataclasses import replace

import pytest

from main_logic.asr_client.speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowObservation,
)
from main_logic.voice_identity_service.pvad_policy import (
    PvadEvidenceKind,
    classify_pvad_observation,
    observe_pvad_score,
)


@pytest.mark.parametrize("score", [0.0, 0.1, 0.4, 0.499999])
def test_absent_activity_never_becomes_negative_identity_evidence(score):
    evidence = observe_pvad_score(score, input_sample_count=3_200)
    assert evidence.kind is PvadEvidenceKind.INSUFFICIENT
    assert evidence.reason == "pvad_no_validated_negative_evidence"
    assert evidence.target_activity_score == score


@pytest.mark.parametrize("count", [3_200, 3_201, 3_359, 12_800, 23_999])
def test_activity_coverage_excludes_partial_frame_tail(count):
    evidence = observe_pvad_score(0.8, input_sample_count=count)
    assert evidence.kind is PvadEvidenceKind.OWNER_ACTIVITY
    assert evidence.input_sample_count == count
    assert evidence.covered_start_sample == 0
    assert evidence.covered_end_sample == count // 160 * 160
    assert evidence.uncovered_tail_samples == count % 160


@pytest.mark.parametrize("count", [0, 3_199, 24_000, 24_001, True, 3_200.0])
def test_sample_domain_is_checked_without_display_milliseconds(count):
    evidence = observe_pvad_score(0.9, input_sample_count=count)
    assert evidence.kind is PvadEvidenceKind.UNAVAILABLE
    assert evidence.covered_end_sample is None


@pytest.mark.parametrize(
    "score", [float("nan"), float("inf"), -0.1, 1.1, None, True, "0.1"]
)
def test_invalid_model_output_is_not_negative_evidence(score):
    evidence = observe_pvad_score(score, input_sample_count=3_200)
    assert evidence.kind is PvadEvidenceKind.UNAVAILABLE
    assert evidence.target_activity_score is None


@pytest.mark.parametrize("quality", [{"rms": 0.001}, {"clipping": 0.1}])
def test_unusable_audio_is_insufficient_even_with_high_activity(quality):
    evidence = observe_pvad_score(0.99, input_sample_count=6_400, **quality)
    assert evidence.kind is PvadEvidenceKind.INSUFFICIENT
    assert evidence.reason == "pvad_audio_quality"


@pytest.mark.parametrize(
    "quality", [{"rms": float("nan")}, {"clipping": -1}, {"rms": True}]
)
def test_invalid_quality_is_unavailable(quality):
    assert observe_pvad_score(0.1, **quality).kind is PvadEvidenceKind.UNAVAILABLE


@pytest.mark.parametrize("scope", ["provider_candidate", "smart_turn_turn"])
def test_legacy_callback_does_not_invent_sample_coverage(scope):
    observation = SpeakerShadowObservation(
        SpeakerShadowCandidateKey(1, 1, scope),
        0.8,
        (),
        1_499,
        observation_kind="terminal_short",
        sequence_no=1,
    )
    evidence = classify_pvad_observation(observation)
    assert evidence.kind is PvadEvidenceKind.OWNER_ACTIVITY
    assert evidence.input_sample_count is None
    assert evidence.covered_start_sample is None
    assert evidence.covered_end_sample is None
    assert evidence.uncovered_tail_samples is None
    # Display precision cannot extend a prefix into an identity range.
    assert classify_pvad_observation(replace(observation, audio_ms=1_500)) == evidence
    assert (
        classify_pvad_observation(
            replace(observation, observation_kind="checkpoint", checkpoint_ms=1_500)
        ).kind
        is PvadEvidenceKind.UNAVAILABLE
    )
    assert (
        classify_pvad_observation(
            replace(observation, evidence_available=False, unavailable_reason="failure")
        ).kind
        is PvadEvidenceKind.UNAVAILABLE
    )


def test_non_observation_is_unavailable():
    assert classify_pvad_observation(None).kind is PvadEvidenceKind.UNAVAILABLE
