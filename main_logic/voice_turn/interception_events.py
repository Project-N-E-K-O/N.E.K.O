"""Provider-neutral, sample-scoped output and delivery evidence.

These events describe authorized output, not raw microphone frames. A gap is
an explicit discontinuity; consumers must never turn it into apparent silence
or concatenate the adjacent audio without a segment boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class InterceptionOutputKind(str, Enum):
    AUDIO = "audio"
    GAP = "gap"
    END = "end"


class InterceptionDeliveryStage(str, Enum):
    NOT_SENT = "not_sent"
    LOCAL_ACCEPTED = "local_accepted"
    QUEUED = "queued"
    TRANSPORT_WRITTEN = "transport_written"
    TRANSPORT_OWNED = "transport_owned"
    PROVIDER_CONFIRMED = "provider_confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InterceptionOutputIdentity:
    session_id: str
    ingress_generation: int
    profile_generation: str
    model_generation: str
    config_generation: str

    def __post_init__(self) -> None:
        for value in (
            self.session_id, self.profile_generation,
            self.model_generation, self.config_generation,
        ):
            if type(value) is not str or not value.strip():
                raise ValueError("interception output authority must be non-empty")
        if type(self.ingress_generation) is not int or self.ingress_generation < 0:
            raise ValueError("interception ingress generation must be nonnegative")


@dataclass(frozen=True, slots=True)
class InterceptionOutputEvent:
    identity: InterceptionOutputIdentity
    interval_id: str
    sequence: int
    kind: InterceptionOutputKind
    start_sample: int
    end_sample: int
    sample_rate_hz: int = 16_000
    asr_start_sample: int | None = None
    asr_end_sample: int | None = None
    pcm16: bytes = b""
    reason: str = ""

    def __post_init__(self) -> None:
        if type(self.identity) is not InterceptionOutputIdentity:
            raise TypeError("interception output identity required")
        if type(self.interval_id) is not str or not self.interval_id:
            raise ValueError("interception interval id required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("interception sequence must be nonnegative")
        if type(self.kind) is not InterceptionOutputKind:
            raise TypeError("interception output kind required")
        if (type(self.start_sample) is not int or type(self.end_sample) is not int
                or self.start_sample < 0 or self.end_sample < self.start_sample):
            raise ValueError("invalid original interception sample range")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise ValueError("invalid interception sample rate")
        if type(self.pcm16) is not bytes:
            raise TypeError("interception output PCM must be bytes")
        if (self.asr_start_sample is None) != (self.asr_end_sample is None):
            raise ValueError("interception ASR mapping requires both endpoints")
        if self.asr_start_sample is not None and (
            type(self.asr_start_sample) is not int
            or type(self.asr_end_sample) is not int
            or self.asr_start_sample < 0
            or self.asr_end_sample < self.asr_start_sample
        ):
            raise ValueError("invalid interception ASR sample range")
        size = self.end_sample - self.start_sample
        if self.kind is InterceptionOutputKind.AUDIO:
            if size <= 0 or len(self.pcm16) != size * 2:
                raise ValueError("interception audio must match its original range")
            if (self.asr_start_sample is not None
                    and self.asr_end_sample - self.asr_start_sample != size):
                raise ValueError("interception ASR mapping must preserve audio length")
        else:
            if self.pcm16:
                raise ValueError("interception control event cannot carry PCM")
            if self.kind is InterceptionOutputKind.END and size != 0:
                raise ValueError("interception END must be a zero-length boundary")
            if self.kind is InterceptionOutputKind.GAP and size <= 0:
                raise ValueError("interception GAP must cover a nonempty range")
            if (self.asr_start_sample is not None
                    and self.asr_end_sample != self.asr_start_sample):
                raise ValueError("control event cannot advance the ASR sample axis")


@dataclass(frozen=True, slots=True)
class InterceptionDeliveryReceipt:
    event: InterceptionOutputEvent
    stage: InterceptionDeliveryStage
    observed_at_monotonic: float
    confirmation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not InterceptionOutputEvent:
            raise TypeError("interception receipt event required")
        if type(self.stage) is not InterceptionDeliveryStage:
            raise TypeError("interception receipt stage required")
        if (isinstance(self.observed_at_monotonic, bool)
                or not isinstance(self.observed_at_monotonic, (int, float))
                or not math.isfinite(self.observed_at_monotonic)
                or self.observed_at_monotonic < 0):
            raise ValueError("interception receipt requires monotonic observation time")
        if self.stage is InterceptionDeliveryStage.PROVIDER_CONFIRMED and (
            type(self.confirmation_id) is not str or not self.confirmation_id
        ):
            raise ValueError("provider confirmation requires explicit evidence")


__all__ = [
    "InterceptionDeliveryReceipt", "InterceptionDeliveryStage",
    "InterceptionOutputEvent", "InterceptionOutputIdentity", "InterceptionOutputKind",
]
