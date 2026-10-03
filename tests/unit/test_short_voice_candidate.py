"""Experimental policy examples; synthetic scores do not calibrate a model."""

import pytest

from main_logic.voice_turn.admission import (
    AdmissionConfig,
    AdmissionDecision,
    CandidateAdmission,
)


def observe(candidate, probabilities, start=0):
    snapshots = []
    for index, probability in enumerate(probabilities):
        snapshots.append(
            candidate.observe(
                start + index * 512, start + (index + 1) * 512, probability
            )
        )
    return snapshots


def experimental(**kwargs):
    return CandidateAdmission(
        "session", AdmissionConfig(experimental_short_speech=True, **kwargs)
    )


def test_high_quality_192ms_requires_end_and_keeps_complete_core():
    candidate = experimental()
    values = observe(candidate, [0.95] * 6 + [0.1] * 4)
    assert all(value.decision is AdmissionDecision.PENDING for value in values[:-1])
    result = values[-1]
    assert result.decision is AdmissionDecision.ADMIT
    assert result.reason == "short_speech_end"
    assert result.admission_path == "short"
    assert (result.audio_start_sample, result.audio_end_sample) == (0, 5120)
    assert result.voiced_samples == result.core_samples == 3072
    assert result.core_probability_mean == pytest.approx(0.95)
    assert result.trailing_low_samples == 2048
    assert result.internal_gap_samples == 0
    assert result.terminal_audio_end_sample == 5120


def test_legacy_default_still_rejects_the_same_short_candidate():
    value = observe(CandidateAdmission("legacy"), [0.95] * 6 + [0.1] * 4)[-1]
    assert value.decision is AdmissionDecision.REJECT
    assert value.reason == "speech_gap"


@pytest.mark.parametrize("minimum,windows", [(128, 4), (160, 5), (192, 6)])
def test_offline_trial_durations_need_end_observation(minimum, windows):
    values = observe(
        experimental(short_minimum_voiced_ms=minimum), [0.95] * windows + [0.1] * 4
    )
    assert values[windows - 1].decision is AdmissionDecision.PENDING
    assert values[-1].decision is AdmissionDecision.ADMIT


def test_one_high_probability_pulse_is_rejected_and_never_revived():
    candidate = experimental()
    rejected = observe(candidate, [0.99] + [0.1] * 4)[-1]
    assert rejected.decision is AdmissionDecision.REJECT
    assert observe(candidate, [0.1] * 2, 5 * 512)[-1] == rejected
    successor = observe(candidate, [0.95] * 6 + [0.1] * 4, 7 * 512)[-1]
    assert successor.decision is AdmissionDecision.ADMIT
    assert successor.candidate_id != rejected.candidate_id
    assert successor.audio_start_sample == 7 * 512
    assert rejected.audio_end_sample == 5 * 512


def test_one_large_model_observation_does_not_substitute_for_multiple_windows():
    candidate = experimental()
    candidate.observe(0, 3072, 0.99)
    result = observe(candidate, [0.1] * 4, 3072)[-1]
    assert result.decision is AdmissionDecision.REJECT


def test_ordinary_224ms_admission_has_no_added_tail_delay():
    values = observe(experimental(), [0.95] * 7)
    assert values[-2].decision is AdmissionDecision.PENDING
    assert values[-1].decision is AdmissionDecision.ADMIT
    assert values[-1].admission_path == "ordinary"
    assert values[-1].audio_end_sample == 3584


def test_uncertain_transition_is_not_counted_as_voice_or_removed_from_core():
    candidate = experimental()
    result = observe(candidate, [0.95] * 3 + [0.4] + [0.95] * 3 + [0.1] * 4)[-1]
    assert result.decision is AdmissionDecision.ADMIT
    assert result.admission_path == "short"
    assert result.voiced_samples == 3072
    assert result.uncertain_samples == result.internal_gap_samples == 512
    assert result.core_samples == 3584
    assert result.core_probability_mean == pytest.approx((0.95 * 6 + 0.4) / 7)


def test_mixed_low_and_uncertain_transition_is_bounded_without_counting_it_as_voice():
    candidate = experimental()
    # 128 ms below onset would reject in legacy; the experimental path can
    # retain the same identity, but still needs ordinary occupancy and voice.
    values = observe(candidate, [0.9] * 3 + [0.4, 0.4, 0.1, 0.1] + [0.9] * 5)
    assert all(value.decision is AdmissionDecision.PENDING for value in values[:-1])
    assert values[-1].decision is AdmissionDecision.ADMIT
    assert values[-1].admission_path == "ordinary"
    assert values[-1].candidate_id == 1
    assert values[-1].voiced_samples == 8 * 512


@pytest.mark.parametrize(
    "probabilities",
    [
        [0.9] + [0.4] * 4,
        [0.9, 0.4, 0.4, 0.9, 0.4, 0.4, 0.9, 0.4],
    ],
)
def test_uncertain_contiguous_and_total_budgets_both_reject(probabilities):
    result = observe(experimental(), probabilities)[-1]
    assert result.decision is AdmissionDecision.REJECT
    assert result.reason == "uncertain_budget"


def test_short_path_does_not_pick_only_best_frames_or_use_one_peak():
    weak = observe(experimental(), [0.51] * 5 + [0.99] + [0.1] * 4)[-1]
    assert weak.decision is AdmissionDecision.REJECT
    interrupted = observe(
        experimental(), [0.95] * 3 + [0.1] * 2 + [0.95] * 3 + [0.1] * 4
    )[-1]
    assert interrupted.decision is AdmissionDecision.REJECT
    assert interrupted.internal_gap_samples == 1024
    assert interrupted.core_samples == 4096


def test_missing_windows_never_become_harmless_end_silence():
    candidate = experimental()
    observe(candidate, [0.95] * 6)
    result = candidate.observe(3584, 4096, 0.1)
    assert result.decision is AdmissionDecision.REJECT
    assert result.reason == "audio_range_missing"
    assert result.missing_samples == 512
    assert result.observed_samples == 7 * 512


def test_duplicate_window_does_not_mutate_candidate_evidence():
    candidate = experimental()
    before = candidate.observe(0, 512, 0.95)
    with pytest.raises(ValueError, match="overlapping"):
        candidate.observe(0, 512, 0.95)
    assert candidate.snapshot == before


def test_short_end_does_not_add_a_fresh_budget_after_candidate_deadline():
    candidate = experimental(maximum_uncertain_run_ms=600, maximum_uncertain_ms=600)
    values = observe(candidate, [0.95] * 6 + [0.4] * 15)
    assert values[-1].decision is AdmissionDecision.REJECT
    assert values[-1].reason == "candidate_timeout"


def test_short_budget_reserves_pre_roll_without_silently_truncating():
    candidate = experimental(short_end_ms=384, short_minimum_voiced_ms=160)
    result = observe(candidate, [0.95] * 6 + [0.1] * 12)[-1]
    assert result.decision is AdmissionDecision.REJECT
    assert result.reason == "short_audio_budget"
    assert (result.audio_start_sample, result.audio_end_sample) == (0, 18 * 512)


def test_acceptance_provenance_survives_tail_and_successor_seal():
    candidate = experimental()
    accepted = observe(candidate, [0.95] * 6 + [0.1] * 4)[-1]
    later = observe(candidate, [0.1] * 20, 10 * 512)[-1]
    assert later.decision is AdmissionDecision.ADMIT
    assert later.admission_path == accepted.admission_path == "short"
    assert later.terminal_audio_end_sample == accepted.audio_end_sample
    assert accepted.audio_end_sample == 5120
    candidate.seal()
    successor = observe(candidate, [0.95], 30 * 512)[-1]
    assert successor.decision is AdmissionDecision.PENDING
    assert successor.admission_path is None
    assert successor.terminal_audio_end_sample is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uncertain_probability": 0.5},
        {"maximum_uncertain_run_ms": 0},
        {"maximum_uncertain_ms": 64},
        {"short_minimum_voiced_ms": 224},
        {"short_minimum_run_ms": 224},
        {"short_end_ms": 0},
        {"short_maximum_candidate_ms": 641},
        {"short_minimum_occupancy": 0.4},
        {"short_minimum_probability": 0.4},
    ],
)
def test_experimental_config_rejects_invalid_policy_bounds(kwargs):
    with pytest.raises(ValueError):
        AdmissionConfig(experimental_short_speech=True, **kwargs)
