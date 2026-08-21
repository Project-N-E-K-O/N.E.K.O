"""Provider-neutral contracts for observation-only speaker scoring."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

SPEAKER_SHADOW_SAMPLE_RATE_HZ = 16_000
MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS = 4_000
MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES = (
    SPEAKER_SHADOW_SAMPLE_RATE_HZ
    * MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS
    // 1_000
    * 2
)
# Lifecycle pre-roll may arrive as one payload, so its per-submit ceiling must
# match the candidate ceiling. The runtime still truncates to the configured
# candidate duration before retaining PCM.
MAX_SPEAKER_SHADOW_FRAME_AUDIO_MS = MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS
MAX_SPEAKER_SHADOW_FRAME_PCM_BYTES = MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES
MAX_SPEAKER_SHADOW_THRESHOLDS = 16
MAX_SPEAKER_SHADOW_CHECKPOINTS = 16
MAX_SPEAKER_SHADOW_QUEUE_CAPACITY = 512
MAX_SPEAKER_SHADOW_BUFFERED_CANDIDATES = 32
MAX_SPEAKER_SHADOW_FINALIZED_CANDIDATES = 4_096
# This independent global budget keeps aggregate retained PCM below 8 MiB even
# when individual queue items contain the full four-second pre-roll payload.
MAX_SPEAKER_SHADOW_RETAINED_PCM_BYTES = (
    8 * 1024 * 1024 - MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES
)
MAX_SPEAKER_SHADOW_BACKEND_LOAD_SECONDS = 30.0
MAX_SPEAKER_SHADOW_BACKEND_SCORE_SECONDS = 5.0
MAX_SPEAKER_SHADOW_BACKEND_CLOSE_SECONDS = 2.0
MAX_SPEAKER_SHADOW_PROCESS_TERMINATE_SECONDS = 2.0
MAX_SPEAKER_SHADOW_SHUTDOWN_GRACE_SECONDS = 2.0
MAX_SPEAKER_SHADOW_CALLBACK_TIMEOUT_SECONDS = 2.0

SpeakerShadowScope = Literal["provider_candidate", "smart_turn_turn"]
SpeakerShadowTerminalReason = Literal[
    "scored",
    "insufficient",
    "dropped",
    "failed",
]


class SpeakerShadowBackend(Protocol):
    """Blocking model adapter run exclusively outside the event loop."""

    def load(self) -> bool: ...

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float: ...

    def close(self) -> None: ...


# A callable factory must be spawn-pickleable. Callable objects may expose an
# idempotent, non-blocking ``close()`` that wipes parent-owned profile material;
# the runtime invokes it exactly once because closing the spawned copy cannot
# clear the original object in parent memory.
SpeakerShadowBackendFactory = Callable[[], SpeakerShadowBackend]


@dataclass(frozen=True, slots=True)
class SpeakerShadowCandidateKey:
    """Identity private to the observer; it has no ASR execution authority."""

    detector_epoch: int
    shadow_generation: int
    scope: SpeakerShadowScope

    def __post_init__(self) -> None:
        if type(self.detector_epoch) is not int or self.detector_epoch < 0:
            raise ValueError("detector_epoch must be a non-negative integer")
        if type(self.shadow_generation) is not int or self.shadow_generation < 0:
            raise ValueError("shadow_generation must be a non-negative integer")
        if self.scope not in ("provider_candidate", "smart_turn_turn"):
            raise ValueError("scope must be a supported speaker-shadow scope")


@dataclass(frozen=True, slots=True)
class SpeakerShadowConfig:
    """Resource limits and evaluation thresholds for the shadow runtime."""

    enabled: bool = False
    similarity_thresholds: tuple[float, ...] = (0.40, 0.44, 0.48, 0.52, 0.55)
    minimum_audio_ms: int = 1_500
    maximum_audio_ms: int = 4_000
    observation_checkpoints_ms: tuple[int, ...] | None = None
    idle_unload_seconds: float = 60.0
    # A four-second candidate can contribute roughly 400 ten-millisecond
    # frames. Keep them queued while a checkpoint waits on a cold backend;
    # the independent retained-PCM budget remains the memory authority.
    queue_capacity: int = MAX_SPEAKER_SHADOW_QUEUE_CAPACITY
    buffered_candidate_capacity: int = 32
    finalized_candidate_capacity: int = 1_024
    load_retry_initial_seconds: float = 5.0
    load_retry_max_seconds: float = 60.0
    shutdown_grace_seconds: float = 0.1
    callback_timeout_seconds: float = 0.1
    backend_load_timeout_seconds: float = 15.0
    backend_score_timeout_seconds: float = 2.0
    backend_close_timeout_seconds: float = 1.0
    process_terminate_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if (
            not self.similarity_thresholds
            or len(self.similarity_thresholds) > MAX_SPEAKER_SHADOW_THRESHOLDS
            or any(
                not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0
                for threshold in self.similarity_thresholds
            )
            or any(
                left >= right
                for left, right in zip(
                    self.similarity_thresholds,
                    self.similarity_thresholds[1:],
                )
            )
        ):
            raise ValueError(
                "similarity_thresholds must contain at most "
                f"{MAX_SPEAKER_SHADOW_THRESHOLDS} finite, unique, increasing "
                "values within [0, 1]"
            )
        if self.minimum_audio_ms <= 0:
            raise ValueError("minimum_audio_ms must be positive")
        if self.maximum_audio_ms < self.minimum_audio_ms:
            raise ValueError("maximum_audio_ms must be at least minimum_audio_ms")
        if self.maximum_audio_ms > MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS:
            raise ValueError(
                "maximum_audio_ms cannot exceed "
                f"{MAX_SPEAKER_SHADOW_CANDIDATE_AUDIO_MS}"
            )
        checkpoints = self.observation_checkpoints_ms
        if checkpoints is not None and (
            type(checkpoints) is not tuple
            or not checkpoints
            or len(checkpoints) > MAX_SPEAKER_SHADOW_CHECKPOINTS
            or any(
                type(checkpoint) is not int
                or checkpoint < self.minimum_audio_ms
                or checkpoint > self.maximum_audio_ms
                for checkpoint in checkpoints
            )
            or any(
                left >= right
                for left, right in zip(checkpoints, checkpoints[1:])
            )
        ):
            raise ValueError(
                "observation_checkpoints_ms must contain at most "
                f"{MAX_SPEAKER_SHADOW_CHECKPOINTS} unique, increasing integer "
                "values within [minimum_audio_ms, maximum_audio_ms]"
            )
        if not math.isfinite(self.idle_unload_seconds) or self.idle_unload_seconds <= 0:
            raise ValueError("idle_unload_seconds must be positive")
        if not 0 < self.queue_capacity <= MAX_SPEAKER_SHADOW_QUEUE_CAPACITY:
            raise ValueError(
                "queue_capacity must be within "
                f"[1, {MAX_SPEAKER_SHADOW_QUEUE_CAPACITY}]"
            )
        if not (
            0
            < self.buffered_candidate_capacity
            <= MAX_SPEAKER_SHADOW_BUFFERED_CANDIDATES
        ):
            raise ValueError(
                "buffered_candidate_capacity must be within "
                f"[1, {MAX_SPEAKER_SHADOW_BUFFERED_CANDIDATES}]"
            )
        if self.finalized_candidate_capacity < self.queue_capacity:
            raise ValueError(
                "finalized_candidate_capacity must be at least queue_capacity"
            )
        if self.finalized_candidate_capacity > MAX_SPEAKER_SHADOW_FINALIZED_CANDIDATES:
            raise ValueError(
                "finalized_candidate_capacity cannot exceed "
                f"{MAX_SPEAKER_SHADOW_FINALIZED_CANDIDATES}"
            )
        if (
            not math.isfinite(self.load_retry_initial_seconds)
            or self.load_retry_initial_seconds <= 0
        ):
            raise ValueError("load_retry_initial_seconds must be positive")
        if (
            not math.isfinite(self.load_retry_max_seconds)
            or self.load_retry_max_seconds < self.load_retry_initial_seconds
        ):
            raise ValueError(
                "load_retry_max_seconds must be at least load_retry_initial_seconds"
            )
        self._validate_timeout(
            "shutdown_grace_seconds",
            self.shutdown_grace_seconds,
            MAX_SPEAKER_SHADOW_SHUTDOWN_GRACE_SECONDS,
        )
        self._validate_timeout(
            "callback_timeout_seconds",
            self.callback_timeout_seconds,
            MAX_SPEAKER_SHADOW_CALLBACK_TIMEOUT_SECONDS,
        )
        self._validate_timeout(
            "backend_load_timeout_seconds",
            self.backend_load_timeout_seconds,
            MAX_SPEAKER_SHADOW_BACKEND_LOAD_SECONDS,
        )
        self._validate_timeout(
            "backend_score_timeout_seconds",
            self.backend_score_timeout_seconds,
            MAX_SPEAKER_SHADOW_BACKEND_SCORE_SECONDS,
        )
        self._validate_timeout(
            "backend_close_timeout_seconds",
            self.backend_close_timeout_seconds,
            MAX_SPEAKER_SHADOW_BACKEND_CLOSE_SECONDS,
        )
        self._validate_timeout(
            "process_terminate_timeout_seconds",
            self.process_terminate_timeout_seconds,
            MAX_SPEAKER_SHADOW_PROCESS_TERMINATE_SECONDS,
        )

    @staticmethod
    def _validate_timeout(name: str, value: float, maximum: float) -> None:
        if not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError(f"{name} must be finite and within (0, {maximum}]")


@dataclass(frozen=True, slots=True)
class SpeakerShadowObservation:
    """Ephemeral score delivered only to an in-memory observer callback."""

    candidate: SpeakerShadowCandidateKey
    similarity: float
    would_block: tuple[tuple[float, bool], ...]
    audio_ms: int
    checkpoint_ms: int | None = None


ObservationCallback = Callable[
    [SpeakerShadowObservation],
    Awaitable[None],
]


@dataclass(slots=True)
class SpeakerShadowMetrics:
    """Aggregate counters only; no identity, PCM, embedding, or score data."""

    submitted_frame_count: int = 0
    submitted_audio_ms: int = 0
    started_candidate_count: int = 0
    finished_candidate_count: int = 0
    scored_candidate_count: int = 0
    insufficient_candidate_count: int = 0
    dropped_candidate_count: int = 0
    failed_candidate_count: int = 0
    evaluated_candidate_count: int = 0
    would_block_count: int = 0
    dropped_frame_count: int = 0
    dropped_audio_ms: int = 0
    stale_result_count: int = 0
    load_count: int = 0
    unload_count: int = 0
    load_failure_count: int = 0
    unload_failure_count: int = 0
    backend_timeout_count: int = 0
    backend_process_termination_count: int = 0
    inference_failure_count: int = 0
    callback_failure_count: int = 0
    load_retry_suppressed_count: int = 0
    worker_start_failure_count: int = 0
    shutdown_timeout_count: int = 0
    load_ms: int = 0
    inference_ms: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


class SpeakerShadowObserver(Protocol):
    """Non-authoritative interface consumed by endpointing."""

    @property
    def enabled(self) -> bool: ...

    def submit(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: SpeakerShadowCandidateKey,
    ) -> bool: ...

    def finish_candidate(self, candidate: SpeakerShadowCandidateKey) -> bool: ...

    def snapshot(self) -> dict[str, int]: ...

    async def reset(self) -> None: ...

    async def close(self) -> None: ...
