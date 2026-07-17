"""Provider-neutral contracts for continuous independent-ASR lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VoiceRouteMode(Enum):
    """The microphone either belongs to independent ASR or is fail-closed."""

    INDEPENDENT = "independent"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class VoiceIngressToken:
    """Identity shared by microphone frames before they belong to a turn."""

    session_epoch: int
    connection_id: str
    lease_generation: int
    route_generation: int
    audio_generation: int


@dataclass(frozen=True, slots=True)
class VoiceTurnToken:
    """A candidate/turn identity that preserves pre-roll across confirmation."""

    ingress: VoiceIngressToken
    turn_id: int


@dataclass(frozen=True, slots=True)
class VoiceTransportToken:
    """Bind provider I/O and callbacks to one transport attempt."""

    turn: VoiceTurnToken
    transport_generation: int


@dataclass(frozen=True, slots=True)
class FinalKey:
    """Logical final identity; transport retries do not create a new turn."""

    session_epoch: int
    connection_id: str
    lease_generation: int
    route_generation: int
    turn_id: int

    @classmethod
    def from_turn(cls, token: VoiceTurnToken) -> "FinalKey":
        ingress = token.ingress
        return cls(
            session_epoch=ingress.session_epoch,
            connection_id=ingress.connection_id,
            lease_generation=ingress.lease_generation,
            route_generation=ingress.route_generation,
            turn_id=token.turn_id,
        )


class VoiceLifecycleState(Enum):
    OFF = "off"
    LOCAL_LISTEN = "local_listen"
    PREWARMING = "prewarming"
    ACTIVE = "active"
    DRAINING = "draining"
    WARM_IDLE = "warm_idle"
    DEEP_SLEEP = "deep_sleep"
    BACKOFF = "backoff"
    BLOCKED = "blocked"
    SUSPENDED = "suspended"


class VoiceLifecycleEvent(Enum):
    MIC_OPENED = "mic_opened"
    SOFT_WAKE = "soft_wake"
    SPEECH_CONFIRMED = "speech_confirmed"
    CONNECT_FAILED = "connect_failed"
    RETRY = "retry"
    RETRIES_EXHAUSTED = "retries_exhausted"
    TURN_SEALED = "turn_sealed"
    # 兼容旧调用方；新代码应使用 TURN_SEALED，明确区分语义端点和 provider final。
    TURN_ENDPOINTED = "turn_endpointed"
    PROVIDER_FINAL = "provider_final"
    WARM_EXPIRED = "warm_expired"
    RECOVERED = "recovered"
    GAME_TAKEOVER = "game_takeover"
    GAME_RELEASED = "game_released"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class VoiceLifecycleConfig:
    pre_roll_ms: int = 700
    confirm_speech_ms: int = 240
    candidate_pause_ms: int = 320
    trailing_audio_ms: int = 400
    pending_audio_ms: int = 8_000
    default_warm_transport_ms: int = 25_000
    smart_turn_warm_ms: int = 60_000

    def __post_init__(self) -> None:
        for field_name in (
            "pre_roll_ms",
            "confirm_speech_ms",
            "candidate_pause_ms",
            "trailing_audio_ms",
            "pending_audio_ms",
            "smart_turn_warm_ms",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.default_warm_transport_ms < 0:
            raise ValueError("default_warm_transport_ms must not be negative")
        if self.pre_roll_ms > self.pending_audio_ms:
            raise ValueError("pre_roll_ms cannot exceed pending_audio_ms")


@dataclass(frozen=True, slots=True)
class VoiceLifecycleSnapshot:
    state: VoiceLifecycleState
    route_mode: VoiceRouteMode
    route_generation: int
    transport_generation: int
    turn_id: int


_TRANSITIONS: dict[
    tuple[VoiceLifecycleState, VoiceLifecycleEvent], VoiceLifecycleState
] = {
    (VoiceLifecycleState.OFF, VoiceLifecycleEvent.MIC_OPENED): VoiceLifecycleState.LOCAL_LISTEN,
    (VoiceLifecycleState.LOCAL_LISTEN, VoiceLifecycleEvent.SOFT_WAKE): VoiceLifecycleState.PREWARMING,
    (VoiceLifecycleState.PREWARMING, VoiceLifecycleEvent.SPEECH_CONFIRMED): VoiceLifecycleState.ACTIVE,
    (VoiceLifecycleState.PREWARMING, VoiceLifecycleEvent.CONNECT_FAILED): VoiceLifecycleState.BACKOFF,
    (VoiceLifecycleState.ACTIVE, VoiceLifecycleEvent.TURN_SEALED): VoiceLifecycleState.DRAINING,
    (VoiceLifecycleState.ACTIVE, VoiceLifecycleEvent.TURN_ENDPOINTED): VoiceLifecycleState.DRAINING,
    (VoiceLifecycleState.DRAINING, VoiceLifecycleEvent.PROVIDER_FINAL): VoiceLifecycleState.WARM_IDLE,
    (VoiceLifecycleState.WARM_IDLE, VoiceLifecycleEvent.SOFT_WAKE): VoiceLifecycleState.PREWARMING,
    (VoiceLifecycleState.WARM_IDLE, VoiceLifecycleEvent.SPEECH_CONFIRMED): VoiceLifecycleState.ACTIVE,
    (VoiceLifecycleState.WARM_IDLE, VoiceLifecycleEvent.WARM_EXPIRED): VoiceLifecycleState.DEEP_SLEEP,
    (VoiceLifecycleState.DEEP_SLEEP, VoiceLifecycleEvent.SOFT_WAKE): VoiceLifecycleState.PREWARMING,
    (VoiceLifecycleState.BACKOFF, VoiceLifecycleEvent.RETRY): VoiceLifecycleState.PREWARMING,
    (VoiceLifecycleState.BACKOFF, VoiceLifecycleEvent.RETRIES_EXHAUSTED): VoiceLifecycleState.BLOCKED,
    (VoiceLifecycleState.BLOCKED, VoiceLifecycleEvent.RECOVERED): VoiceLifecycleState.LOCAL_LISTEN,
    (VoiceLifecycleState.LOCAL_LISTEN, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.PREWARMING, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.ACTIVE, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.DRAINING, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.WARM_IDLE, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.DEEP_SLEEP, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.BACKOFF, VoiceLifecycleEvent.GAME_TAKEOVER): VoiceLifecycleState.SUSPENDED,
    (VoiceLifecycleState.SUSPENDED, VoiceLifecycleEvent.GAME_RELEASED): VoiceLifecycleState.LOCAL_LISTEN,
}


def next_lifecycle_state(
    state: VoiceLifecycleState,
    event: VoiceLifecycleEvent,
) -> VoiceLifecycleState:
    """Apply one explicit transition; user stop wins from every live state."""

    if event is VoiceLifecycleEvent.STOPPED and state is not VoiceLifecycleState.OFF:
        return VoiceLifecycleState.OFF
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as exc:
        raise RuntimeError(
            "VOICE_LIFECYCLE_INVALID_TRANSITION: "
            f"{state.value} + {event.value}"
        ) from exc
