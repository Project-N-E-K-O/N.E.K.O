"""Versioned, provider-independent contracts for optional target extraction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contracts import SpeakerModelIdentity

SAMPLE_RATE = 16000
EMBEDDING_DIM = 192
BLOCK_SAMPLES = 640
STATE_SHAPE = (6, 1, 32, 256)
RESOURCE_REVISION = "real-tse-causal-onnx-v1"
PREPROCESSING_REVISION = "wespeaker-kaldi-fbank-v1"
REFERENCE_METHOD = "mean_raw_3_segments_v1"
TSE_ENCODER_IDENTITY = SpeakerModelIdentity(
    "real-tse-wespeaker-ecapa",
    "6eb9e96eed042cc59b875631deb448ea8b11a7b40918a8424d0aa0161be70e99",
    EMBEDDING_DIM,
)
TSE_PREPROCESSING_REVISION = PREPROCESSING_REVISION
TSE_REFERENCE_METHOD = REFERENCE_METHOD


class TseModelError(RuntimeError):
    """The model cannot safely supply enhanced audio for this stream."""


def pcm_float32(pcm: np.ndarray) -> np.ndarray:
    """Validate finite, mono normalized PCM without changing its time axis."""
    values = np.asarray(pcm)
    if values.ndim != 1 or np.iscomplexobj(values):
        raise ValueError("TSE requires one-dimensional real PCM")
    values = np.ascontiguousarray(values, dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("TSE PCM must be finite")
    return values


def reference_float32(embedding: np.ndarray) -> np.ndarray:
    """Copy the raw ECAPA reference; never normalize the speaker space."""
    values = np.asarray(embedding)
    if values.shape not in ((EMBEDDING_DIM,), (1, EMBEDDING_DIM)):
        raise ValueError("TSE reference must have 192 dimensions")
    if np.iscomplexobj(values):
        raise ValueError("TSE reference must be real")
    result = np.array(values, dtype=np.float32, copy=True).reshape(1, EMBEDDING_DIM)
    if not np.isfinite(result).all() or not np.any(result):
        raise ValueError("TSE reference must be finite and nonzero")
    return result


@dataclass(frozen=True)
class TseAudioChunk:
    """Audio covering exactly [start_sample, end_sample) on the raw PCM axis."""

    start_sample: int
    end_sample: int
    pcm: np.ndarray

    def __post_init__(self) -> None:
        if type(self.start_sample) is not int or type(self.end_sample) is not int:
            raise ValueError("sample positions must be integers")
        audio = pcm_float32(self.pcm).copy()
        if self.start_sample < 0 or self.end_sample - self.start_sample != audio.size:
            raise ValueError("TSE sample range does not match PCM")
        audio.flags.writeable = False
        object.__setattr__(self, "pcm", audio)
