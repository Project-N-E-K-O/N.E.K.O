import pytest
from dataclasses import FrozenInstanceError

from main_logic.voice_turn.contracts import (
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    EvaluationStatus,
    TurnDecision,
    TurnEvaluation,
    VoiceIngressToken,
    VoicePartialEvent,
    VoiceTurnToken,
)


def _turn_token(*, session_epoch: int = 1, turn_id: int = 2) -> VoiceTurnToken:
    return VoiceTurnToken(
        ingress=VoiceIngressToken(
            session_epoch=session_epoch,
            connection_id="connection",
            lease_generation=3,
            route_generation=4,
            audio_generation=5,
        ),
        turn_id=turn_id,
    )


def test_unavailable_carries_no_decision():
    evaluation = TurnEvaluation(
        status=EvaluationStatus.UNAVAILABLE,
        decision=None,
        probability=None,
        generation=1,
        activity_seq=2,
        reason="model_missing",
    )
    assert evaluation.decision is None


def test_ok_evaluation_requires_probability_and_decision():
    with pytest.raises(ValueError):
        TurnEvaluation(EvaluationStatus.OK, TurnDecision.COMPLETE, None, 0, 0)


def test_non_ok_evaluation_rejects_probability():
    with pytest.raises(ValueError):
        TurnEvaluation(EvaluationStatus.ERROR, None, 0.4, 0, 0)


@pytest.mark.parametrize(
    "event",
    [
        VoicePartialEvent(turn_token=_turn_token(), text="hello"),
        AsrStatusEvent(code="ASR_READY", provider="qwen"),
        AsrLifecycleNotification(
            state="local_listen",
            provider="qwen",
            session_epoch=1,
        ),
        AsrSubmitResult(status=AsrSubmitStatus.ACCEPTED),
    ],
)
def test_cross_layer_asr_events_are_immutable(event):
    with pytest.raises(FrozenInstanceError):
        event.__setattr__(next(iter(event.__dataclass_fields__)), object())


def test_partial_event_exposes_read_only_epoch_from_full_turn_identity() -> None:
    token = _turn_token(session_epoch=7, turn_id=11)

    event = VoicePartialEvent(turn_token=token, text="draft")

    assert event.turn_token is token
    assert event.session_epoch == 7
