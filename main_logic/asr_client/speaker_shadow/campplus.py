"""Lazy, score-only CAM++ backend for the observation-only speaker shadow."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .asset_manifest import (
    CAMPPLUS_FILENAME,
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_SAMPLE_RATE_HZ,
    CAMPPLUS_SHA256,
    CAMPPLUS_SIZE_BYTES,
    CAMPPLUS_SOURCE,
    resolve_verified_campplus_asset,
)


CAMPPLUS_EMBEDDING_DIM = 192
CAMPPLUS_MINIMUM_SAMPLES = 24_000
_FRAME_LENGTH = 400
_FRAME_SHIFT = 160
_PADDED_FRAME_LENGTH = 512
_MEL_BIN_COUNT = 80
_PREEMPHASIS_COEFFICIENT = np.float32(0.97)
_FLOAT32_EPSILON = np.finfo(np.float32).eps


def _wipe_array(value: np.ndarray | None) -> None:
    """Best-effort wipe for temporary biometric arrays owned by this module."""

    if value is None or not value.flags.writeable:
        return
    try:
        value.fill(0)
    except (TypeError, ValueError):
        pass


class _ZeroizableEmbedding:
    """Pickle-safe float32 storage whose owned bytes can be overwritten."""

    def __init__(self, reference_embedding: np.ndarray) -> None:
        embedding: np.ndarray | None = None
        try:
            embedding = np.array(reference_embedding, dtype=np.float32, copy=True)
            if embedding.shape != (CAMPPLUS_EMBEDDING_DIM,):
                raise ValueError("reference_embedding_shape")
            if not np.isfinite(embedding).all():
                raise ValueError("reference_embedding_non_finite")
            norm = float(np.linalg.norm(embedding))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("reference_embedding_norm")
            embedding /= np.float32(norm)
            self._storage = bytearray(
                embedding.astype("<f4", copy=False).tobytes()
            )
        finally:
            _wipe_array(embedding)
        self._closed = False

    def copy(self) -> np.ndarray:
        if self._closed:
            raise RuntimeError("reference_embedding_closed")
        return np.frombuffer(self._storage, dtype="<f4").astype(
            np.float32,
            copy=True,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._storage[:] = b"\x00" * len(self._storage)


def _povey_window() -> np.ndarray:
    indices = np.arange(_FRAME_LENGTH, dtype=np.float64)
    window = (
        0.5 - 0.5 * np.cos(2 * np.pi * indices / (_FRAME_LENGTH - 1))
    ) ** 0.85
    return window.astype(np.float32)


def _mel_scale(frequency_hz: float | np.ndarray) -> np.ndarray:
    return 1127.0 * np.log1p(np.asarray(frequency_hz) / 700.0)


def _mel_filter_bank() -> np.ndarray:
    low_mel = float(_mel_scale(20.0))
    high_mel = float(_mel_scale(CAMPPLUS_SAMPLE_RATE_HZ / 2))
    mel_delta = (high_mel - low_mel) / (_MEL_BIN_COUNT + 1)
    frequencies = (
        np.arange(_PADDED_FRAME_LENGTH // 2 + 1, dtype=np.float64)
        * CAMPPLUS_SAMPLE_RATE_HZ
        / _PADDED_FRAME_LENGTH
    )
    mel_frequencies = _mel_scale(frequencies)
    filters = np.zeros(
        (_MEL_BIN_COUNT, _PADDED_FRAME_LENGTH // 2 + 1),
        dtype=np.float32,
    )
    segments = np.floor((mel_frequencies - low_mel) / mel_delta).astype(
        np.int32
    )
    for fft_bin, segment in enumerate(segments):
        if segment < 0 or segment > _MEL_BIN_COUNT:
            continue
        weight = (
            mel_frequencies[fft_bin] - (low_mel + segment * mel_delta)
        ) / mel_delta
        if segment < _MEL_BIN_COUNT:
            filters[segment, fft_bin] += np.float32(weight)
        if segment > 0:
            filters[segment - 1, fft_bin] += np.float32(1.0 - weight)
    return filters


_POVEY_WINDOW = _povey_window()
_MEL_FILTER_BANK = _mel_filter_bank()


def compute_campplus_features(
    pcm16: bytes,
    *,
    sample_rate_hz: int,
) -> np.ndarray:
    """Match the reviewed sherpa-onnx 3D-Speaker Kaldi fbank frontend."""

    if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
        raise ValueError("sample_rate_mismatch")
    if not isinstance(pcm16, bytes) or not pcm16:
        raise ValueError("pcm_invalid")
    if len(pcm16) % 2:
        raise ValueError("pcm_invalid")

    samples: np.ndarray | None = None
    frames: np.ndarray | None = None
    spectrum: np.ndarray | None = None
    power: np.ndarray | None = None
    mel_energies: np.ndarray | None = None
    features: np.ndarray | None = None
    try:
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        samples /= np.float32(32768.0)
        if samples.size < _FRAME_LENGTH:
            raise ValueError("pcm_too_short")
        frame_count = 1 + (samples.size - _FRAME_LENGTH) // _FRAME_SHIFT
        shape = (frame_count, _FRAME_LENGTH)
        strides = (samples.strides[0] * _FRAME_SHIFT, samples.strides[0])
        frames = np.lib.stride_tricks.as_strided(
            samples,
            shape=shape,
            strides=strides,
            writeable=False,
        ).copy()
        frames -= frames.mean(axis=1, keepdims=True, dtype=np.float32)
        frames[:, 1:] -= _PREEMPHASIS_COEFFICIENT * frames[:, :-1]
        frames[:, 0] *= np.float32(1.0) - _PREEMPHASIS_COEFFICIENT
        frames *= _POVEY_WINDOW
        spectrum = np.fft.rfft(frames, n=_PADDED_FRAME_LENGTH, axis=1)
        power = (
            spectrum.real * spectrum.real + spectrum.imag * spectrum.imag
        ).astype(np.float32)
        mel_energies = power @ _MEL_FILTER_BANK.T
        np.maximum(mel_energies, _FLOAT32_EPSILON, out=mel_energies)
        features = np.log(mel_energies, dtype=np.float32)
        features -= features.mean(axis=0, keepdims=True, dtype=np.float32)
        return np.array(features, dtype=np.float32, order="C", copy=True)
    finally:
        _wipe_array(features)
        _wipe_array(mel_energies)
        _wipe_array(power)
        _wipe_array(spectrum)
        _wipe_array(frames)
        _wipe_array(samples)


class CampPlusEmbeddingModel:
    """One lazy CPU ONNX session owned by the spawned backend process."""

    model_id = CAMPPLUS_MODEL_ID
    model_revision = CAMPPLUS_MODEL_REVISION

    def __init__(self, asset_dir: Path | None = None) -> None:
        self._asset_dir = Path(asset_dir) if asset_dir is not None else None
        self._session: Any | None = None
        self._closed = False

    @property
    def is_ready(self) -> bool:
        return self._session is not None and not self._closed

    def load(self) -> bool:
        if self._closed:
            return False
        if self._session is not None:
            return True
        session: Any | None = None
        try:
            model_path = resolve_verified_campplus_asset(self._asset_dir)
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            options.enable_cpu_mem_arena = False
            session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._validate_session_contract(session)
        except Exception:
            session = None
            return False
        self._session = session
        return True

    @staticmethod
    def _validate_session_contract(session: Any) -> None:
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1:
            raise ValueError("onnx_input_count")
        if inputs[0].name != "x":
            raise ValueError("onnx_input_name")
        if inputs[0].type != "tensor(float)":
            raise ValueError("onnx_input_type")
        if len(inputs[0].shape) != 3 or inputs[0].shape[2] != 80:
            raise ValueError("onnx_input_shape")
        if len(outputs) != 1:
            raise ValueError("onnx_output_count")
        if outputs[0].name != "embedding":
            raise ValueError("onnx_output_name")
        if outputs[0].type != "tensor(float)":
            raise ValueError("onnx_output_type")
        if (
            len(outputs[0].shape) != 2
            or outputs[0].shape[1] != CAMPPLUS_EMBEDDING_DIM
        ):
            raise ValueError("onnx_output_shape")
        metadata = session.get_modelmeta().custom_metadata_map
        expected_metadata = {
            "framework": "3d-speaker",
            "language": "Chinese-English",
            "feature_normalize_type": "global-mean",
            "sample_rate": "16000",
            "output_dim": "192",
            "normalize_samples": "1",
        }
        if any(
            metadata.get(key) != expected
            for key, expected in expected_metadata.items()
        ):
            raise ValueError("onnx_metadata")

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        session = self._session
        if self._closed or session is None:
            raise RuntimeError("model_not_loaded")
        if not isinstance(pcm16, bytes) or not pcm16 or len(pcm16) % 2:
            raise ValueError("pcm_invalid")
        if sample_rate_hz != CAMPPLUS_SAMPLE_RATE_HZ:
            raise ValueError("sample_rate_mismatch")
        if len(pcm16) // 2 < CAMPPLUS_MINIMUM_SAMPLES:
            raise ValueError("pcm_too_short")

        features: np.ndarray | None = None
        raw_embedding: np.ndarray | None = None
        result: np.ndarray | None = None
        outputs: list[Any] | None = None
        keep_result = False
        try:
            features = compute_campplus_features(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )
            outputs = session.run(
                ["embedding"],
                {"x": features[np.newaxis, :, :]},
            )
            if len(outputs) != 1:
                raise ValueError("onnx_output_count")
            raw_embedding = np.asarray(outputs[0], dtype=np.float32)
            if raw_embedding.shape != (1, CAMPPLUS_EMBEDDING_DIM):
                raise ValueError("onnx_output_shape")
            result = np.array(raw_embedding[0], dtype=np.float32, copy=True)
            if not np.isfinite(result).all():
                raise ValueError("embedding_non_finite")
            norm = float(np.linalg.norm(result))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("embedding_norm")
            result /= np.float32(norm)
            keep_result = True
            return result
        finally:
            _wipe_array(features)
            if outputs is not None:
                for output in outputs:
                    if isinstance(output, np.ndarray):
                        _wipe_array(output)
            _wipe_array(raw_embedding)
            if not keep_result:
                _wipe_array(result)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session = None


class CampPlusSpeakerShadowBackend:
    """Convert candidate PCM to one finite cosine score and retain no result."""

    def __init__(
        self,
        reference_embedding: np.ndarray,
        *,
        asset_dir: Path | None = None,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._reference = _ZeroizableEmbedding(reference_embedding)
        self._asset_dir = Path(asset_dir) if asset_dir is not None else None
        self._model_factory = model_factory
        self._model: Any | None = None
        self._closed = False

    def load(self) -> bool:
        if self._closed:
            return False
        if self._model is not None:
            return True
        model: Any | None = None
        try:
            model = (
                CampPlusEmbeddingModel(asset_dir=self._asset_dir)
                if self._model_factory is None
                else self._model_factory()
            )
            if not bool(model.load()):
                model.close()
                return False
        except Exception:
            if model is not None:
                try:
                    model.close()
                except Exception:
                    pass
            return False
        self._model = model
        return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        if self._closed:
            raise RuntimeError("backend_closed")
        model = self._model
        if model is None:
            raise RuntimeError("backend_not_loaded")

        raw_candidate: Any = None
        candidate: np.ndarray | None = None
        reference: np.ndarray | None = None
        try:
            raw_candidate = model.embedding_from_pcm16(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )
            candidate = np.array(raw_candidate, dtype=np.float32, copy=True)
            if candidate.shape != (CAMPPLUS_EMBEDDING_DIM,):
                raise ValueError("embedding_shape")
            if not np.isfinite(candidate).all():
                raise ValueError("embedding_non_finite")
            norm = float(np.linalg.norm(candidate))
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("embedding_norm")
            candidate /= np.float32(norm)
            reference = self._reference.copy()
            similarity = float(np.dot(reference, candidate))
            if not math.isfinite(similarity):
                raise ValueError("similarity_non_finite")
            return min(1.0, max(-1.0, similarity))
        finally:
            _wipe_array(reference)
            _wipe_array(candidate)
            if isinstance(raw_candidate, np.ndarray):
                _wipe_array(raw_candidate)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        model, self._model = self._model, None
        try:
            if model is not None:
                model.close()
        finally:
            self._reference.close()


class CampPlusBackendFactory:
    """Lightweight spawn-pickleable factory owning parent reference material."""

    def __init__(
        self,
        reference_embedding: np.ndarray,
        *,
        asset_dir: Path | None = None,
    ) -> None:
        self._reference = _ZeroizableEmbedding(reference_embedding)
        self._asset_dir = Path(asset_dir) if asset_dir is not None else None
        self._closed = False

    def __call__(self) -> CampPlusSpeakerShadowBackend:
        if self._closed:
            raise RuntimeError("backend_factory_closed")
        reference = self._reference.copy()
        try:
            return CampPlusSpeakerShadowBackend(
                reference,
                asset_dir=self._asset_dir,
            )
        finally:
            _wipe_array(reference)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reference.close()
