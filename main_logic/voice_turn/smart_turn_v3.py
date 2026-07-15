"""Smart Turn v3.2 CPU ONNX semantic endpoint runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .onnx_runtime import OnnxModelRuntime, RuntimeInferenceError
from .whisper_features import WhisperFeatureExtractor


class SmartTurnV3(OnnxModelRuntime):
    asset_filenames = ("smart_turn_v3.onnx",)

    def __init__(self, **kwargs: Any) -> None:
        # Two CPU threads met the <=30 ms local inference target on the Windows
        # reference machine without enabling the higher-RSS CPU arena.
        kwargs.setdefault("intra_op_threads", 2)
        super().__init__(**kwargs)
        self._features = WhisperFeatureExtractor()
        self._input_name = "input_features"

    def _load_verified(self, paths: dict[str, Path], manifest: Any, ort: Any) -> None:
        self._session = self._make_session(paths["smart_turn_v3.onnx"], ort)
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("Smart Turn model must expose exactly one input")
        self._input_name = inputs[0].name

    def predict_probability(self, audio: np.ndarray) -> float:
        if np.asarray(audio).size == 0:
            raise ValueError("Smart Turn requires non-empty audio")
        features = self._features.extract(np.asarray(audio))[None].astype(np.float32)
        outputs = self._run_session(
            None,
            {self._input_name: features},
            validate_outputs=self._validate_outputs,
        )
        probability = float(np.asarray(outputs[0]).reshape(-1)[0])
        return probability

    @staticmethod
    def _validate_outputs(outputs: Any) -> None:
        probability = float(np.asarray(outputs[0]).reshape(-1)[0])
        if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise RuntimeInferenceError("Smart Turn returned an invalid probability")
