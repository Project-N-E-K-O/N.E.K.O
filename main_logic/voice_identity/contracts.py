"""Provider-neutral voice identity contracts with no audio execution authority."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Protocol, TypeAlias


class SpeakerShadowBackend(Protocol):
    """Convert candidate PCM to a similarity score without routing authority."""

    def load(self) -> bool: ...

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeakerShadowConfig:
    enabled: bool = False
    similarity_thresholds: tuple[float, ...] = (0.40, 0.44, 0.48, 0.52, 0.55)
    minimum_audio_ms: int = 1_500
    maximum_audio_ms: int = 4_000
    idle_unload_seconds: float = 60.0
    queue_capacity: int = 32
    finalized_candidate_capacity: int = 1_024
    load_retry_initial_seconds: float = 5.0
    load_retry_max_seconds: float = 60.0
    shutdown_grace_seconds: float = 0.1
    callback_timeout_seconds: float = 0.1

    def __post_init__(self) -> None:
        if (
            not self.similarity_thresholds
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
                "similarity_thresholds must be finite, unique, increasing values "
                "within [0, 1]"
            )
        if self.minimum_audio_ms <= 0:
            raise ValueError("minimum_audio_ms must be positive")
        if self.maximum_audio_ms < self.minimum_audio_ms:
            raise ValueError("maximum_audio_ms must be at least minimum_audio_ms")
        if self.idle_unload_seconds <= 0:
            raise ValueError("idle_unload_seconds must be positive")
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if self.finalized_candidate_capacity < self.queue_capacity:
            raise ValueError(
                "finalized_candidate_capacity must be at least queue_capacity"
            )
        if self.load_retry_initial_seconds <= 0:
            raise ValueError("load_retry_initial_seconds must be positive")
        if self.load_retry_max_seconds < self.load_retry_initial_seconds:
            raise ValueError(
                "load_retry_max_seconds must be at least load_retry_initial_seconds"
            )
        if self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        if self.callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SpeakerShadowObservation:
    candidate: Hashable
    similarity: float
    would_block: tuple[tuple[float, bool], ...]
    audio_ms: int


class SpeakerVerifierRuntime(Protocol):
    """Minimal adapter consumed by Independent ASR candidate lifecycle code."""

    def submit(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: Hashable,
    ) -> bool: ...

    def finish_candidate(self, candidate: Hashable) -> bool: ...

    async def reset(self) -> None: ...

    async def close(self) -> None: ...

    async def wait_closed(self) -> None: ...

    def snapshot(self) -> dict[str, int]: ...


SpeakerVerifierFactory: TypeAlias = Callable[[], SpeakerVerifierRuntime | None]
SpeakerObservationCallback: TypeAlias = Callable[
    [SpeakerShadowObservation],
    Awaitable[None] | None,
]
