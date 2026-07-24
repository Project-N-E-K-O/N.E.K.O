import pytest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from main_logic.voice_turn.contracts import (
    AsrTurnCapabilities,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    EvaluationStatus,
    SmartTurnConfig,
    TurnDecision,
    TurnEvaluation,
    VoicePartialEvent,
    build_turn_detector_if_required,
    requires_external_turn_detector,
)


def test_semantic_endpoint_provider_does_not_require_smart_turn():
    assert requires_external_turn_detector(AsrTurnCapabilities(semantic_endpoint=True)) is False
    assert requires_external_turn_detector(AsrTurnCapabilities(semantic_endpoint=False)) is True


def test_semantic_endpoint_provider_never_constructs_smart_turn_runtime():
    factory = Mock()
    detector = build_turn_detector_if_required(
        AsrTurnCapabilities(semantic_endpoint=True), factory
    )
    assert detector is None
    factory.assert_not_called()


def test_unavailable_is_not_an_incomplete_decision():
    evaluation = TurnEvaluation(
        status=EvaluationStatus.UNAVAILABLE,
        decision=None,
        probability=None,
        generation=1,
        activity_seq=2,
        reason="model_missing",
    )
    assert evaluation.decision is not TurnDecision.INCOMPLETE


def test_ok_evaluation_requires_probability_and_decision():
    with pytest.raises(ValueError):
        TurnEvaluation(EvaluationStatus.OK, TurnDecision.COMPLETE, None, 0, 0)


def test_non_ok_evaluation_rejects_probability():
    with pytest.raises(ValueError):
        TurnEvaluation(EvaluationStatus.ERROR, None, 0.4, 0, 0)


def test_config_rejects_missing_vad_hysteresis():
    with pytest.raises(ValueError):
        SmartTurnConfig(onset_probability=0.4, offset_probability=0.4)


@pytest.mark.parametrize(
    "event",
    [
        VoicePartialEvent(text="hello", session_epoch=1),
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
