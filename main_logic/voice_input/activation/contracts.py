"""Provider-neutral contracts for voice-session activation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ActivationState(str, Enum):
    """Lifecycle states for one microphone activation controller."""

    DISABLED = "disabled"
    PREPARING = "preparing"
    WAITING = "waiting"
    VERIFYING = "verifying"
    REPLAYING = "replaying"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"


class VerificationResultKind(str, Enum):
    """Terminal classification returned by a speaker verifier."""

    OWNER = "owner"
    NOT_OWNER = "not_owner"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


class OutputOrigin(str, Enum):
    """Why a frame was admitted to the downstream writer."""

    BYPASS = "bypass"
    REPLAY = "replay"
    LIVE = "live"


class OutputCommit(str, Enum):
    """Known delivery state after a writer attempts one output lease."""

    NOT_SENT = "not_sent"
    LOCAL_ACCEPTED = "local_accepted"
    TRANSPORT_WRITTEN = "transport_written"
    PROVIDER_CONFIRMED = "provider_confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ActivationGeneration:
    """Identity fence covering every authority that may outlive an await."""

    session_id: str
    microphone: int
    route: int
    profile: int
    permission: int
    input_owner: str


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One normalized PCM16 frame with capture-order metadata."""

    sequence: int
    sample_start: int
    sample_end: int
    captured_at: float
    sample_rate: int
    pcm: bytes
    generation: ActivationGeneration
    context: object | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("VOICE_ACTIVATION_FRAME_SEQUENCE_INVALID")
        if self.sample_start < 0 or self.sample_end <= self.sample_start:
            raise ValueError("VOICE_ACTIVATION_FRAME_SAMPLE_RANGE_INVALID")
        if self.sample_rate <= 0:
            raise ValueError("VOICE_ACTIVATION_FRAME_SAMPLE_RATE_INVALID")
        if not isinstance(self.pcm, bytes):
            raise TypeError("VOICE_ACTIVATION_FRAME_PCM_BYTES_REQUIRED")
        if len(self.pcm) != (self.sample_end - self.sample_start) * 2:
            raise ValueError("VOICE_ACTIVATION_FRAME_PCM_SIZE_MISMATCH")

    @property
    def duration_seconds(self) -> float:
        return (self.sample_end - self.sample_start) / self.sample_rate

    @property
    def captured_end_at(self) -> float:
        return self.captured_at + self.duration_seconds


@dataclass(frozen=True, slots=True)
class CandidateWindow:
    """An activity-delimited candidate offered for owner verification."""

    start_sequence: int
    end_sequence: int

    def __post_init__(self) -> None:
        if self.start_sequence < 0 or self.end_sequence < self.start_sequence:
            raise ValueError("VOICE_ACTIVATION_CANDIDATE_RANGE_INVALID")


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """Opaque scoring work identity; audio is claimed separately."""

    request_id: int
    generation: ActivationGeneration
    candidate: CandidateWindow
    replay_start_sequence: int


@dataclass(frozen=True, slots=True)
class VerificationInput:
    """Read-only normalized PCM snapshot for one live request."""

    request: VerificationRequest
    sample_rate: int
    sample_start: int
    sample_end: int
    pcm: bytes


@dataclass(frozen=True, slots=True)
class OutputLease:
    """Exclusive permission for the single writer to attempt one frame."""

    lease_id: int
    generation: ActivationGeneration
    frame: AudioFrame
    origin: OutputOrigin


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    """Observable result of one synchronous state-machine transition."""

    state: ActivationState
    reason: str
    verification_request: VerificationRequest | None = None
    output_ready: bool = False
    replay_cutoff_sequence: int | None = None


class SpeakerVerifier(Protocol):
    """Asynchronous backend boundary implemented outside this package."""

    async def verify(
        self,
        verification_input: VerificationInput,
    ) -> VerificationResultKind: ...


class AudioFrameSink(Protocol):
    """Single-writer boundary implemented by an ASR or native route."""

    async def send(self, lease: OutputLease) -> OutputCommit: ...


@dataclass(frozen=True, slots=True)
class WakeWordDetection:
    """Keyword evidence on the original PCM timeline, never speaker identity."""

    keyword: str
    generation: ActivationGeneration
    epoch: int
    sample_start: int
    sample_end: int

    def __post_init__(self) -> None:
        if not isinstance(self.keyword, str) or not self.keyword.strip():
            raise ValueError("VOICE_WAKE_WORD_KEYWORD_INVALID")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("VOICE_WAKE_WORD_EPOCH_INVALID")
        if (
            type(self.sample_start) is not int
            or type(self.sample_end) is not int
            or self.sample_start < 0
            or self.sample_end <= self.sample_start
        ):
            raise ValueError("VOICE_WAKE_WORD_SAMPLE_RANGE_INVALID")


class WakeWordDetector(Protocol):
    """Bounded asynchronous local detector with an isolated inference owner."""

    async def prepare(self) -> None: ...

    async def feed(self, frame: AudioFrame, epoch: int) -> WakeWordDetection | None: ...

    async def close(self) -> None: ...
