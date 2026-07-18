"""Identity and event contracts for asynchronous endpoint detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

from main_logic.voice_turn.contracts import SpeechActivityEvent, TurnEvaluation

from .lifecycle_contracts import VoiceIngressToken, VoiceTurnToken


@dataclass(frozen=True, slots=True)
class DetectorIngressIdentity:
    """Identity assigned when a normalized PCM frame enters the detector."""

    ingress_token: VoiceIngressToken
    detector_epoch: int
    sequence_no: int


@dataclass(frozen=True, slots=True)
class DetectorCandidateKey:
    """Detector-worker-owned candidate identity within one external epoch."""

    detector_epoch: int
    candidate_generation: int


@dataclass(frozen=True, slots=True)
class BoundDetectorTurn:
    """One-time binding between a detector candidate and a logical ASR turn."""

    candidate: DetectorCandidateKey
    turn_token: VoiceTurnToken


@dataclass(frozen=True, slots=True)
class DetectorActivityEvent:
    ingress: DetectorIngressIdentity
    candidate: DetectorCandidateKey
    activity: SpeechActivityEvent


@dataclass(frozen=True, slots=True)
class DetectorTurnEvent:
    ingress: DetectorIngressIdentity
    bound_turn: BoundDetectorTurn
    kind: Literal["complete", "inference_failed"]
    evaluation: TurnEvaluation | None = None


@dataclass(frozen=True, slots=True)
class DetectorRuntimeEvent:
    ingress: DetectorIngressIdentity
    candidate: DetectorCandidateKey | None
    kind: Literal[
        "prepare_failed",
        "throttle_unavailable",
        "audio_backpressure",
        "control_lane_failed",
    ]


DetectorEvent: TypeAlias = (
    DetectorActivityEvent | DetectorTurnEvent | DetectorRuntimeEvent
)


@dataclass(frozen=True, slots=True)
class DetectorAudioItem:
    identity: DetectorIngressIdentity
    pcm16: bytes
    duration_us: int

    @classmethod
    def from_pcm16(
        cls,
        pcm16: bytes,
        *,
        identity: DetectorIngressIdentity,
        sample_rate_hz: int,
    ) -> "DetectorAudioItem":
        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DETECTOR_INVALID_PCM: complete PCM16 bytes required")
        if not pcm16:
            raise ValueError("DETECTOR_INVALID_PCM: empty audio item")
        if sample_rate_hz <= 0:
            raise ValueError("DETECTOR_INVALID_SAMPLE_RATE")
        samples = len(pcm16) // 2
        duration_us = (
            samples * 1_000_000 + sample_rate_hz - 1
        ) // sample_rate_hz
        return cls(identity=identity, pcm16=pcm16, duration_us=duration_us)


@dataclass(frozen=True, slots=True)
class DetectorEvaluationResultItem:
    ingress: DetectorIngressIdentity
    candidate: DetectorCandidateKey
    coordinator_generation: int
    activity_seq: int
    result: TurnEvaluation


class DetectorSubmitStatus(Enum):
    ACCEPTED = "accepted"
    SKIPPED_QUIET = "skipped_quiet"
    BACKPRESSURE = "backpressure"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DetectorSubmitResult:
    status: DetectorSubmitStatus
    throttle_available: bool
    endpointing_available: bool
    identity: DetectorIngressIdentity | None


DetectorQueueItem: TypeAlias = DetectorAudioItem | DetectorEvaluationResultItem | object
