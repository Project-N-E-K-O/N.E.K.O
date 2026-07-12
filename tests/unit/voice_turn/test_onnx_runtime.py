import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from main_logic.voice_turn import onnx_runtime
from main_logic.voice_turn.asset_manifest import AssetManifestError
from main_logic.voice_turn.onnx_runtime import (
    OnnxModelRuntime,
    RuntimeInferenceError,
    RuntimeState,
)


class _Runtime(OnnxModelRuntime):
    asset_filenames = ("model.onnx",)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loads = 0
        self._count_lock = threading.Lock()

    def _load_verified(self, paths, manifest, ort):
        with self._count_lock:
            self.loads += 1
        time.sleep(0.02)
        self._session = object()


def test_runtime_is_disabled_by_default():
    runtime = _Runtime()
    assert runtime.load() is False
    assert runtime.state is RuntimeState.UNAVAILABLE
    assert runtime.unavailable_reason == "disabled_by_config"


def test_concurrent_load_is_single_flight(monkeypatch, tmp_path):
    monkeypatch.setattr(
        onnx_runtime,
        "resolve_verified_assets",
        lambda filenames, override: (tmp_path, object(), {"model.onnx": tmp_path / "model.onnx"}),
    )
    runtime = _Runtime(enabled=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda _: runtime.load(), range(8)))
    assert runtime.loads == 1
    assert runtime.state is RuntimeState.READY


def test_close_is_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        onnx_runtime,
        "resolve_verified_assets",
        lambda filenames, override: (tmp_path, object(), {"model.onnx": tmp_path / "model.onnx"}),
    )
    runtime = _Runtime(enabled=True)
    assert runtime.load()
    runtime.close()
    assert runtime.state is RuntimeState.CLOSED
    assert runtime.load() is False


def test_missing_assets_make_runtime_unavailable(monkeypatch):
    def fail_resolve(filenames, override):
        raise AssetManifestError("missing model")

    monkeypatch.setattr(onnx_runtime, "resolve_verified_assets", fail_resolve)
    runtime = _Runtime(enabled=True)
    assert runtime.load() is False
    assert runtime.state is RuntimeState.UNAVAILABLE
    assert runtime.unavailable_reason == "assetmanifesterror"


class _FailingSession:
    def run(self, output_names, inputs):
        raise RuntimeError("inference failed")


def test_inference_errors_open_circuit_breaker():
    runtime = _Runtime(enabled=True, inference_error_limit=3)
    runtime._session = _FailingSession()
    runtime._state = RuntimeState.READY

    for expected_state in (RuntimeState.DEGRADED, RuntimeState.DEGRADED):
        with pytest.raises(RuntimeInferenceError):
            runtime._run_session(None, {})
        assert runtime.state is expected_state

    with pytest.raises(RuntimeInferenceError):
        runtime._run_session(None, {})
    assert runtime.state is RuntimeState.UNAVAILABLE
    assert runtime.unavailable_reason == "inference_circuit_open:RuntimeError"


def test_successful_inference_clears_degraded_state():
    class SuccessfulSession:
        def run(self, output_names, inputs):
            return ["ok"]

    runtime = _Runtime(enabled=True)
    runtime._session = SuccessfulSession()
    runtime._state = RuntimeState.DEGRADED
    runtime._reason = "previous_error"
    runtime._consecutive_errors = 1
    assert runtime._run_session(None, {}) == ["ok"]
    assert runtime.state is RuntimeState.READY
    assert runtime.unavailable_reason is None
