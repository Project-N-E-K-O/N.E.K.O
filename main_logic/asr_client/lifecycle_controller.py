"""State owner for throttled, continuous independent-ASR voice input."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .audio_ring_buffer import AudioRingBuffer
from .lifecycle_contracts import (
    VoiceLifecycleConfig,
    VoiceLifecycleEvent,
    VoiceLifecycleSnapshot,
    VoiceLifecycleState,
    VoiceRouteMode,
    next_lifecycle_state,
)
from .metrics import VoiceLifecycleMetrics
from .provider_policy import AsrProviderPolicy


class AudioDisposition(Enum):
    FORWARD = "forward"
    FORWARD_WITH_PRE_ROLL = "forward_with_pre_roll"
    BUFFER = "buffer"
    SUPPRESS = "suppress"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class VoiceAsyncIdentity:
    route_generation: int
    transport_generation: int
    turn_id: int


@dataclass(frozen=True, slots=True)
class AudioDecision:
    disposition: AudioDisposition
    pre_roll: bytes = b""
    shadow_disposition: AudioDisposition | None = None


class VoiceInputLifecycleController:
    """Keep routing, lifecycle, and audio gating as separate decisions."""

    def __init__(
        self,
        *,
        provider_policy: AsrProviderPolicy,
        config: VoiceLifecycleConfig | None = None,
        shadow_mode: bool = True,
    ) -> None:
        self.provider_policy = provider_policy
        self.config = config or VoiceLifecycleConfig()
        self.shadow_mode = bool(shadow_mode)
        self.metrics = VoiceLifecycleMetrics()
        self._state = VoiceLifecycleState.OFF
        self._route_mode = VoiceRouteMode.BLOCKED
        self._route_generation = 0
        self._transport_generation = 0
        self._turn_id = 1
        self._pre_roll = AudioRingBuffer(
            capacity_ms=self.config.pre_roll_ms,
            sample_rate_hz=16_000,
        )
        self._pre_roll_sent_for_turn = False
        self._independent_asr_fail_open = False

    @property
    def snapshot(self) -> VoiceLifecycleSnapshot:
        return VoiceLifecycleSnapshot(
            state=self._state,
            route_mode=self._route_mode,
            route_generation=self._route_generation,
            transport_generation=self._transport_generation,
            turn_id=self._turn_id,
        )

    @property
    def identity(self) -> VoiceAsyncIdentity:
        return VoiceAsyncIdentity(
            route_generation=self._route_generation,
            transport_generation=self._transport_generation,
            turn_id=self._turn_id,
        )

    @property
    def pre_roll_bytes(self) -> int:
        return len(self._pre_roll.peek())

    def open(self, *, route_mode: VoiceRouteMode) -> None:
        if self._state is not VoiceLifecycleState.OFF:
            raise RuntimeError("VOICE_LIFECYCLE_ALREADY_OPEN")
        self._route_generation += 1
        self._route_mode = route_mode
        self._state = next_lifecycle_state(
            self._state,
            VoiceLifecycleEvent.MIC_OPENED,
        )
        if route_mode is VoiceRouteMode.BLOCKED:
            self._state = VoiceLifecycleState.BLOCKED

    def transition(self, event: VoiceLifecycleEvent) -> VoiceLifecycleState:
        self._state = next_lifecycle_state(self._state, event)
        if event is VoiceLifecycleEvent.SOFT_WAKE:
            self.metrics.wake_candidate_count += 1
        elif event is VoiceLifecycleEvent.SPEECH_CONFIRMED:
            self.metrics.wake_confirmed_count += 1
        elif event is VoiceLifecycleEvent.CONNECT_FAILED:
            self._transport_generation += 1
        elif event is VoiceLifecycleEvent.PROVIDER_FINAL:
            self._turn_id += 1
            self._pre_roll_sent_for_turn = False
            self._pre_roll.clear()
        elif event is VoiceLifecycleEvent.GAME_TAKEOVER:
            self._turn_id += 1
            self._pre_roll_sent_for_turn = False
            self._pre_roll.clear()
        return self._state

    def accept_audio(self, pcm16: bytes, *, sample_rate_hz: int) -> AudioDecision:
        if not isinstance(pcm16, bytes):
            raise TypeError("PCM16 audio must be bytes")
        if len(pcm16) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        if sample_rate_hz != 16_000:
            raise ValueError("lifecycle controller requires normalized 16 kHz PCM16")
        duration_ms = len(pcm16) * 1_000 // (sample_rate_hz * 2)
        self.metrics.add_local_audio(duration_ms)

        if (
            self._route_mode is VoiceRouteMode.BLOCKED
            or self._state
            in {
                VoiceLifecycleState.OFF,
                VoiceLifecycleState.BLOCKED,
                VoiceLifecycleState.SUSPENDED,
            }
        ):
            return AudioDecision(AudioDisposition.BLOCK)

        target = self._target_disposition()
        if target is AudioDisposition.BUFFER:
            dropped = self._pre_roll.append(pcm16, sample_rate_hz=sample_rate_hz)
            if dropped:
                self.metrics.buffer_overflow_count += 1
            if self.shadow_mode:
                self.metrics.add_cloud_audio(duration_ms)
                self.metrics.add_suppressed_audio(duration_ms, shadow=True)
                return AudioDecision(
                    AudioDisposition.FORWARD,
                    shadow_disposition=AudioDisposition.BUFFER,
                )
            self.metrics.add_suppressed_audio(duration_ms)
            return AudioDecision(AudioDisposition.BUFFER)

        if target is AudioDisposition.FORWARD and not self._pre_roll_sent_for_turn:
            self._pre_roll.append(pcm16, sample_rate_hz=sample_rate_hz)
            pre_roll = self._pre_roll.drain()
            self._pre_roll_sent_for_turn = True
            pre_roll_ms = len(pre_roll) * 1_000 // (sample_rate_hz * 2)
            self.metrics.add_cloud_audio(pre_roll_ms)
            return AudioDecision(
                AudioDisposition.FORWARD_WITH_PRE_ROLL,
                pre_roll=pre_roll,
            )

        self.metrics.add_cloud_audio(duration_ms)
        return AudioDecision(AudioDisposition.FORWARD)

    def matches(self, identity: VoiceAsyncIdentity) -> bool:
        matches = identity == self.identity
        if not matches:
            self.metrics.stale_callback_count += 1
        return matches

    def stop(self) -> None:
        if self._state is not VoiceLifecycleState.OFF:
            self._state = next_lifecycle_state(
                self._state,
                VoiceLifecycleEvent.STOPPED,
            )
        self._route_mode = VoiceRouteMode.BLOCKED
        self._route_generation += 1
        self._transport_generation += 1
        self._turn_id += 1
        self._pre_roll_sent_for_turn = False
        self._pre_roll.clear()

    def enable_independent_asr_fail_open(self) -> None:
        """Disable throttling while preserving the hard independent route."""

        if self._route_mode is VoiceRouteMode.INDEPENDENT:
            self._independent_asr_fail_open = True

    def _target_disposition(self) -> AudioDisposition:
        if self._independent_asr_fail_open:
            return AudioDisposition.FORWARD
        if self._state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.PREWARMING,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
            VoiceLifecycleState.BACKOFF,
        }:
            return AudioDisposition.BUFFER
        if self._state in {
            VoiceLifecycleState.ACTIVE,
            VoiceLifecycleState.DRAINING,
        }:
            return AudioDisposition.FORWARD
        return AudioDisposition.BLOCK
