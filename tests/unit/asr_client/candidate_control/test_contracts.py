import pytest

from main_logic.asr_client.candidate_control import (
    CandidateRejectionOutcome,
    CandidateRejectionRequest,
)
from main_logic.asr_client.endpointing.detector import DetectorCandidateKey


def test_candidate_rejection_request_preserves_all_fences() -> None:
    candidate = DetectorCandidateKey(7, 9)
    request = CandidateRejectionRequest(
        session_epoch=1,
        audio_generation=2,
        transport_generation=3,
        turn_id=4,
        candidate=candidate,
        activation_generation="profile-1",
    )

    assert request.candidate is candidate
    assert request.activation_generation == "profile-1"
    assert CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED.value == (
        "applied_cleanup_degraded"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_epoch", -1),
        ("audio_generation", True),
        ("transport_generation", -1),
        ("turn_id", 1.5),
    ],
)
def test_candidate_rejection_request_rejects_invalid_numeric_fence(
    field: str,
    value: object,
) -> None:
    kwargs = {
        "session_epoch": 1,
        "audio_generation": 2,
        "transport_generation": 3,
        "turn_id": 4,
        "candidate": DetectorCandidateKey(7, 9),
        "activation_generation": "profile-1",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        CandidateRejectionRequest(**kwargs)


def test_candidate_rejection_request_rejects_invalid_authority() -> None:
    with pytest.raises(TypeError, match="candidate"):
        CandidateRejectionRequest(1, 2, 3, 4, object(), "profile-1")
    with pytest.raises(ValueError, match="activation_generation"):
        CandidateRejectionRequest(1, 2, 3, 4, DetectorCandidateKey(7, 9), " ")
