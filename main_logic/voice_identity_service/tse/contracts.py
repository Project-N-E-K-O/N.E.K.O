"""Versioned, provider-independent contracts for optional target extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from main_logic.voice_identity.contracts import SpeakerModelIdentity

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


class TseFailureReason(str, Enum):
    """Stable reasons that force the caller to emit a gap, never raw PCM."""

    TARGET_ABSENT = "target_absent"
    WORKER_FAILURE = "worker_failure"
    TIMEOUT = "timeout"
    INPUT_DISCONTINUITY = "input_discontinuity"
    QUEUE_FULL = "queue_full"


class TseOutputStatus(str, Enum):
    """Outcome visible to the audio delivery boundary."""

    TARGET_AUDIO = "target_audio"
    TARGET_ABSENT = "target_absent"
    WORKER_FAILURE = "worker_failure"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class TseExtractionResult:
    """A fail-closed extraction result with no implicit mixed-audio fallback."""

    status: TseOutputStatus
    chunks: tuple["TseAudioChunk", ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, TseOutputStatus):
            raise TypeError("status must be TseOutputStatus")
        chunks = tuple(self.chunks)
        if self.status is not TseOutputStatus.TARGET_AUDIO and chunks:
            raise ValueError("failure results cannot carry audio chunks")
        if self.status is TseOutputStatus.TARGET_AUDIO and not chunks:
            raise ValueError("target audio result requires extracted chunks")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        object.__setattr__(self, "chunks", chunks)

    @classmethod
    def target_audio(cls, chunks: list["TseAudioChunk"] | tuple["TseAudioChunk", ...]) -> "TseExtractionResult":
        return cls(TseOutputStatus.TARGET_AUDIO, tuple(chunks), "target_audio")

    @classmethod
    def target_absent(cls, reason: str = "target_absent") -> "TseExtractionResult":
        return cls(TseOutputStatus.TARGET_ABSENT, (), reason)

    @classmethod
    def worker_failure(cls, reason: str = "worker_failure") -> "TseExtractionResult":
        return cls(TseOutputStatus.WORKER_FAILURE, (), reason)

    @classmethod
    def timeout(cls, reason: str = "timeout") -> "TseExtractionResult":
        return cls(TseOutputStatus.TIMEOUT, (), reason)


class TseTargetAbsentError(TseModelError):
    """The extractor completed without a target-speaker segment."""

    reason = TseFailureReason.TARGET_ABSENT


class TseWorkerFailureError(TseModelError):
    """The worker failed and its output is not safe to deliver."""

    reason = TseFailureReason.WORKER_FAILURE


class TseTimeoutError(TseModelError):
    """The extractor exceeded its bounded latency budget."""

    reason = TseFailureReason.TIMEOUT


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
