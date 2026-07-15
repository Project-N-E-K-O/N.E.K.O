import numpy as np
import pytest

from main_logic.voice_turn.onnx_runtime import RuntimeInferenceError, RuntimeState
from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3


class _Session:
    def __init__(self, output):
        self.output = output
        self.seen = None

    def run(self, output_names, inputs):
        self.seen = inputs
        return [np.asarray([[self.output]], dtype=np.float32)]


def _runtime(output):
    runtime = SmartTurnV3(enabled=True)
    runtime._session = _Session(output)
    runtime._state = RuntimeState.READY
    return runtime


def test_predict_uses_expected_feature_contract():
    runtime = _runtime(0.75)
    probability = runtime.predict_probability(np.zeros(16_000, dtype=np.float32))
    assert probability == pytest.approx(0.75)
    assert runtime._session.seen["input_features"].shape == (1, 80, 800)
    assert runtime._session.seen["input_features"].dtype == np.float32


@pytest.mark.parametrize("output", [-0.1, 1.1, float("nan")])
def test_predict_rejects_invalid_model_probability(output):
    runtime = _runtime(output)
    with pytest.raises(RuntimeInferenceError):
        runtime.predict_probability(np.zeros(16_000, dtype=np.float32))


def test_invalid_probabilities_open_shared_inference_circuit():
    runtime = _runtime(float("nan"))
    for expected_state in (RuntimeState.DEGRADED, RuntimeState.DEGRADED):
        with pytest.raises(RuntimeInferenceError):
            runtime.predict_probability(np.zeros(16_000, dtype=np.float32))
        assert runtime.state is expected_state
    with pytest.raises(RuntimeInferenceError):
        runtime.predict_probability(np.zeros(16_000, dtype=np.float32))
    assert runtime.state is RuntimeState.UNAVAILABLE
    assert runtime.unavailable_reason == "inference_circuit_open:RuntimeInferenceError"
