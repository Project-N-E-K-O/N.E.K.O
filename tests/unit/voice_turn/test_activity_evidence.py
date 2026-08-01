from __future__ import annotations

import pytest

from main_logic.voice_turn.activity_evidence import RnnoiseEvidence


def test_unavailable_rnnoise_evidence_cannot_carry_probabilities() -> None:
    with pytest.raises(
        ValueError,
        match="unavailable RNNoise evidence cannot carry probabilities",
    ):
        RnnoiseEvidence(False, 1, 0.5, 0.5, 0.5, 0.5)


def test_empty_rnnoise_chunk_cannot_reuse_prior_probabilities() -> None:
    with pytest.raises(
        ValueError,
        match="empty RNNoise chunk cannot reuse prior probabilities",
    ):
        RnnoiseEvidence(True, 0, 0.5, 0.5, 0.5, 0.5)


def test_legacy_probability_conversion_is_explicit_and_bounded() -> None:
    evidence = RnnoiseEvidence.from_legacy_probability(0.6, available=True)

    assert evidence == RnnoiseEvidence(True, 1, 0.6, 0.6, 0.6, 0.6)
    assert RnnoiseEvidence.from_legacy_probability(
        None,
        available=True,
    ) == RnnoiseEvidence.unavailable()


def test_rnnoise_baseline_returns_a_new_validated_value() -> None:
    evidence = RnnoiseEvidence(True, 3, 0.9, 0.6, 0.2, 0.55)

    assert evidence.with_baseline(0.4).baseline == 0.4
    assert evidence.baseline is None
