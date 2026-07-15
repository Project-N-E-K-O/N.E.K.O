"""Streaming Silero VAD and provider-neutral speech activity gating."""

from __future__ import annotations

import math
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

import numpy as np

from .contracts import SmartTurnConfig, SpeechActivityEvent
from .onnx_runtime import OnnxModelRuntime, RuntimeInferenceError


class SileroVad(OnnxModelRuntime):
    """Silero v5/v6 ONNX wrapper for continuous 16 kHz PCM16 streams."""

    asset_filenames = ("silero_vad.onnx",)
    SAMPLE_RATE = 16_000
    WINDOW_SAMPLES = 512
    CONTEXT_SAMPLES = 64

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sample_rate = np.asarray(self.SAMPLE_RATE, dtype=np.int64)
        self._stream_lock = Lock()
        self.reset_stream()

    def _load_verified(self, paths: dict[str, Path], manifest: Any, ort: Any) -> None:
        self._session = self._make_session(paths["silero_vad.onnx"], ort)

    def reset_stream(self) -> None:
        with getattr(self, "_stream_lock", Lock()):
            self._lstm_state = np.zeros((2, 1, 128), dtype=np.float32)
            self._context = np.zeros(self.CONTEXT_SAMPLES, dtype=np.float32)
            self._pending = np.empty(0, dtype=np.float32)

    def process_pcm16(self, pcm16_le: bytes) -> list[float]:
        if len(pcm16_le) % 2:
            raise ValueError("Silero input must contain complete PCM16 samples")
        if not pcm16_le or not self.is_ready:
            return []
        samples = np.frombuffer(pcm16_le, dtype="<i2").astype(np.float32) / 32768.0
        probabilities: list[float] = []
        with self._stream_lock:
            self._pending = np.concatenate((self._pending, samples))
            while self._pending.size >= self.WINDOW_SAMPLES:
                window = self._pending[: self.WINDOW_SAMPLES]
                self._pending = self._pending[self.WINDOW_SAMPLES :]
                model_input = np.concatenate((self._context, window))[None].astype(np.float32)
                outputs = self._run_session(
                    None,
                    {
                        "input": model_input,
                        "state": self._lstm_state,
                        "sr": self._sample_rate,
                    },
                    validate_outputs=self._validate_outputs,
                )
                probability = float(np.asarray(outputs[0]).reshape(-1)[0])
                self._lstm_state = np.asarray(outputs[1], dtype=np.float32)
                self._context = window[-self.CONTEXT_SAMPLES :].copy()
                probabilities.append(probability)
        return probabilities

    @staticmethod
    def _validate_outputs(outputs: Any) -> None:
        probability = float(np.asarray(outputs[0]).reshape(-1)[0])
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise RuntimeInferenceError("Silero returned a probability outside [0, 1]")


class SileroActivityGate:
    """Map Silero probabilities to activity events without committing a turn."""

    def __init__(self, vad: SileroVad, config: SmartTurnConfig) -> None:
        self._vad = vad
        self._config = config
        window_ms = 1000 * SileroVad.WINDOW_SAMPLES / SileroVad.SAMPLE_RATE
        self._minimum_speech_windows = max(
            1, math.ceil(config.minimum_speech_ms / window_ms)
        )
        self._candidate_silence_windows = max(
            1, math.ceil(config.candidate_silence_ms / window_ms)
        )
        self.reset()

    def reset(self) -> None:
        self._vad.reset_stream()
        self._speech_windows = 0
        self._silence_windows = 0
        self._speech_confirmed = False
        self._candidate_emitted = False

    def feed(self, pcm16_le: bytes) -> SpeechActivityEvent:
        return self.process_probabilities(self._vad.process_pcm16(pcm16_le))

    def process_probabilities(self, probabilities: Iterable[float]) -> SpeechActivityEvent:
        event = SpeechActivityEvent.NONE
        for probability in probabilities:
            if probability >= self._config.onset_probability:
                was_paused = self._candidate_emitted
                self._speech_windows += 1
                self._silence_windows = 0
                self._candidate_emitted = False
                if not self._speech_confirmed and self._speech_windows >= self._minimum_speech_windows:
                    self._speech_confirmed = True
                    event = SpeechActivityEvent.SPEECH_STARTED
                elif self._speech_confirmed and was_paused:
                    event = SpeechActivityEvent.SPEECH_RESUMED
            elif probability < self._config.offset_probability:
                if not self._speech_confirmed:
                    self._speech_windows = 0
                    continue
                self._silence_windows += 1
                if (
                    not self._candidate_emitted
                    and self._silence_windows >= self._candidate_silence_windows
                ):
                    self._candidate_emitted = True
                    event = SpeechActivityEvent.CANDIDATE_PAUSE
        return event
