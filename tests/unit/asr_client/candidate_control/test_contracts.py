from dataclasses import FrozenInstanceError

import pytest

from main_logic.asr_client.candidate_control import (
    CandidateRejectionOutcome,
    CandidateRejectionRequest,
)
from main_logic.asr_client.endpointing.detector import DetectorCandidateKey


def _request(**overrides: object) -> CandidateRejectionRequest:
    values = {
        "session_epoch": 7,
        "audio_generation": 3,
        "transport_generation": 5,
        "turn_id": 11,
        "candidate": DetectorCandidateKey(2, 13),
        "profile_generation": "profile-4",
        "filter_generation": "beta-v1",
    }
    values.update(overrides)
    return CandidateRejectionRequest(**values)  # type: ignore[arg-type]


def test_request_is_immutable_and_preserves_authoritative_candidate() -> None:
    request = _request()

    assert request.candidate == DetectorCandidateKey(2, 13)
    assert CandidateRejectionOutcome.APPLIED.value == "applied"
    with pytest.raises(FrozenInstanceError):
        request.turn_id = 12  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_epoch", -1),
        ("audio_generation", True),
        ("transport_generation", -1),
        ("turn_id", 1.5),
        ("profile_generation", " "),
        ("filter_generation", ""),
    ],
)
def test_request_rejects_invalid_fences(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _request(**{field: value})


def test_request_rejects_non_detector_candidate() -> None:
    with pytest.raises(TypeError):
        _request(candidate=object())
