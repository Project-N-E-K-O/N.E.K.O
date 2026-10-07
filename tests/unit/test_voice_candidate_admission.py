from dataclasses import FrozenInstanceError

import pytest

from main_logic.voice_turn.admission import (
    AdmissionConfig,
    AdmissionDecision,
    CandidateAdmission,
)


def feed(candidate, probabilities, start=0):
    snapshot = None
    for i, probability in enumerate(probabilities):
        snapshot = candidate.observe(
            start + i * 512, start + (i + 1) * 512, probability
        )
    return snapshot


def test_ordinary_confirmation_is_224ms_and_sealed_successor_needs_fresh_evidence():
    candidate = CandidateAdmission("session-1")
    assert feed(candidate, [0.9] * 6).decision is AdmissionDecision.PENDING
    first = feed(candidate, [0.9], 3072)
    assert first.decision is AdmissionDecision.ADMIT
    assert first.voiced_audio_ms == 224
    feed(candidate, [0.1] * 10, 3584)
    sealed = candidate.seal()
    successor = feed(candidate, [0.9], 8704)
    assert successor.decision is AdmissionDecision.PENDING
    assert successor.candidate_id != sealed.candidate_id
    assert sealed.voiced_audio_ms == 224
    with pytest.raises(FrozenInstanceError):
        sealed.voiced_samples = 0


def test_gray_intervals_cannot_accumulate_indefinitely():
    candidate = CandidateAdmission("session-1")
    decisions = [
        candidate.observe(i * 512, (i + 1) * 512, p).decision
        for i, p in enumerate([0.9] + [0.4] * 4 + [0.9] * 6)
    ]
    assert AdmissionDecision.ADMIT not in decisions
    assert AdmissionDecision.REJECT in decisions


def test_same_utterance_pause_does_not_revoke_admission():
    candidate = CandidateAdmission("session-1")
    first = feed(candidate, [0.8] * 7)
    last = feed(candidate, [0.1] * 100, 3584)
    assert last.decision is AdmissionDecision.ADMIT
    assert last.candidate_id == first.candidate_id
    assert last.voiced_samples == first.voiced_samples


def test_replay_never_counts_twice_and_waiting_does_not_count_as_speech():
    candidate = CandidateAdmission("session-1")
    first = feed(candidate, [0.9])
    with pytest.raises(ValueError, match="overlapping"):
        candidate.observe(0, 512, 0.9)
    assert candidate.snapshot == first
    after_gap = candidate.observe(16000, 16512, 0.9)
    assert after_gap.observed_audio_ms == 64
    assert after_gap.decision is AdmissionDecision.REJECT


def test_occupancy_rejects_scattered_evidence_but_recovers_for_continuous_speech():
    candidate = CandidateAdmission("session-1")
    weak = feed(candidate, [0.8, 0.4] * 11)
    assert weak.decision is not AdmissionDecision.ADMIT
    recovered = feed(candidate, [0.8] * 7, 22 * 512)
    assert recovered.decision is AdmissionDecision.ADMIT


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_probability_is_not_evidence(probability):
    candidate = CandidateAdmission("session-1")
    with pytest.raises(ValueError):
        candidate.observe(0, 512, probability)


def test_no_candidate_for_silence_and_rejected_candidate_stays_frozen():
    candidate = CandidateAdmission("session-1")
    assert feed(candidate, [0.1] * 7) is None
    rejected = feed(candidate, [0.9] + [0.1] * 4, 3584)
    assert rejected.decision is AdmissionDecision.REJECT
    assert feed(candidate, [0.1] * 10, 6144) == rejected


def test_short_gaps_and_low_occupancy_expire_instead_of_infinite_wait():
    candidate = CandidateAdmission("session-1")
    result = feed(candidate, [0.9, 0.4, 0.4] * 7)
    assert result.decision is AdmissionDecision.REJECT
    assert result.reason == "candidate_timeout"


@pytest.mark.parametrize(
    "values",
    [
        {"sample_rate": 48000},
        {"minimum_voiced_ms": 0},
        {"minimum_voiced_ms": 700},
        {"maximum_gap_ms": 0},
        {"maximum_gap_ms": 700},
        {"minimum_occupancy": 0},
        {"onset_probability": 2},
    ],
)
def test_config_rejects_invalid_bounds(values):
    with pytest.raises(ValueError):
        AdmissionConfig(**values)


@pytest.mark.parametrize("start,end", [(-1, 512), (0, 0), (512, 0)])
def test_rejects_invalid_sample_intervals(start, end):
    with pytest.raises(ValueError):
        CandidateAdmission("session-1").observe(start, end, 0.9)
