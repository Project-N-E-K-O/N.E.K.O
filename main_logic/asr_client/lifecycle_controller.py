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
        # turn_id 在确认一轮新语音时分配，不再由上一轮 final 推进。
        self._turn_id = 0
        self._completed_turn_id = -1
        self._pre_roll = AudioRingBuffer(
            capacity_ms=self.config.pre_roll_ms,
            sample_rate_hz=16_000,
        )
        self._pre_roll_sent_for_turn = False
        self._pending_turn = AudioRingBuffer(
            capacity_ms=self.config.pending_audio_ms,
            sample_rate_hz=16_000,
        )
        self._pending_turn_speech = False
        self._pending_connect = AudioRingBuffer(
            capacity_ms=self.config.pending_audio_ms,
            sample_rate_hz=16_000,
        )
        self._active_start_audio = b""
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

    @property
    def pending_turn_bytes(self) -> int:
        return len(self._pending_turn.peek())

    @property
    def pending_connect_bytes(self) -> int:
        return len(self._pending_connect.peek())

    @property
    def has_pending_turn(self) -> bool:
        return self._pending_turn_speech and self.pending_turn_bytes > 0

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
            existing_pre_roll = self._pre_roll.drain()
            if existing_pre_roll:
                self._pending_connect.append(existing_pre_roll)
        elif event is VoiceLifecycleEvent.SPEECH_CONFIRMED:
            self._turn_id += 1
            self.metrics.wake_confirmed_count += 1
            if self._pre_roll.peek():
                self._pending_connect.append(self._pre_roll.drain())
            self._active_start_audio = self._pending_connect.drain()
        elif event is VoiceLifecycleEvent.CONNECT_FAILED:
            self._transport_generation += 1
        elif event is VoiceLifecycleEvent.PROVIDER_FINAL:
            self._completed_turn_id = self._turn_id
            self._pre_roll_sent_for_turn = False
            self._pre_roll.clear()
        elif event is VoiceLifecycleEvent.GAME_TAKEOVER:
            self._turn_id += 1
            self._completed_turn_id = self._turn_id
            self._pre_roll_sent_for_turn = False
            self._pre_roll.clear()
            self._pending_turn.clear()
            self._pending_turn_speech = False
            self._pending_connect.clear()
            self._active_start_audio = b""
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

        # 已 seal 的旧轮次绝不能再接收麦克风音频。DRAINING 期间到达的
        # PCM 只进入下一轮的有界内存缓冲，等待旧 final 后再启动新 turn。
        if self._state is VoiceLifecycleState.DRAINING:
            dropped = self._pending_turn.append(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )
            if dropped:
                self.metrics.buffer_overflow_count += 1
            self.metrics.add_suppressed_audio(duration_ms)
            return AudioDecision(AudioDisposition.BUFFER)

        target = self._target_disposition()
        if target is AudioDisposition.BUFFER:
            target_buffer = (
                self._pending_connect
                if self._state
                in {
                    VoiceLifecycleState.PREWARMING,
                    VoiceLifecycleState.BACKOFF,
                }
                else self._pre_roll
            )
            dropped = target_buffer.append(pcm16, sample_rate_hz=sample_rate_hz)
            if dropped:
                self.metrics.buffer_overflow_count += 1
            if self.shadow_mode:
                self.metrics.add_suppressed_audio(duration_ms, shadow=True)
                return AudioDecision(
                    AudioDisposition.FORWARD,
                    shadow_disposition=AudioDisposition.BUFFER,
                )
            self.metrics.add_suppressed_audio(duration_ms)
            return AudioDecision(AudioDisposition.BUFFER)

        if target is AudioDisposition.FORWARD and not self._pre_roll_sent_for_turn:
            self._pre_roll.append(pcm16, sample_rate_hz=sample_rate_hz)
            pre_roll = self._active_start_audio + self._pre_roll.drain()
            self._active_start_audio = b""
            self._pre_roll_sent_for_turn = True
            return AudioDecision(
                AudioDisposition.FORWARD_WITH_PRE_ROLL,
                pre_roll=pre_roll,
            )

        return AudioDecision(AudioDisposition.FORWARD)

    def drain_active_start_audio(self) -> bytes:
        """建连成功后立即取出 pre-roll + pending-connect，不等待下一帧。"""

        if self._state is not VoiceLifecycleState.ACTIVE:
            return b""
        payload, self._active_start_audio = self._active_start_audio, b""
        if not payload:
            return b""
        self._pre_roll_sent_for_turn = True
        return payload

    def record_provider_wire_audio(self, duration_ms: int) -> None:
        """仅由 transport 在实际进入 provider 请求边界后记账。"""

        self.metrics.add_provider_wire_audio(duration_ms)

    def matches(self, identity: VoiceAsyncIdentity) -> bool:
        matches = (
            identity == self.identity
            and identity.turn_id > self._completed_turn_id
        )
        if not matches:
            self.metrics.stale_callback_count += 1
        return matches

    def mark_pending_turn_speech(self) -> None:
        """记录 DRAINING 期间已确认的新一轮语音，不触碰旧 transport。"""

        if self._state is not VoiceLifecycleState.DRAINING:
            raise RuntimeError("VOICE_PENDING_TURN_REQUIRES_DRAINING")
        self._pending_turn_speech = True

    def begin_pending_turn(self) -> bytes:
        """在旧 final 后原子地激活并取出下一轮待发送音频。"""

        if self._state is not VoiceLifecycleState.WARM_IDLE:
            raise RuntimeError("VOICE_PENDING_TURN_REQUIRES_WARM_IDLE")
        if not self.has_pending_turn:
            return b""
        payload = self._pending_turn.drain()
        self._pending_turn_speech = False
        self.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
        self._pre_roll_sent_for_turn = True
        return payload

    def discard_unconfirmed_pending_audio(self) -> None:
        """丢弃 seal 后仅有环境音、但没有确认新语音的暂存。"""

        if self._pending_turn_speech:
            return
        self._pending_turn.clear()

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
        self._pending_turn.clear()
        self._pending_turn_speech = False
        self._pending_connect.clear()
        self._active_start_audio = b""

    def enable_independent_asr_fail_open(self) -> None:
        """Disable throttling while preserving the hard independent route."""

        if self._route_mode is VoiceRouteMode.INDEPENDENT:
            self._independent_asr_fail_open = True

    def invalidate_transport(self) -> None:
        """仅推进 transport generation，不终止本地语音监听。"""

        self._transport_generation += 1

    def invalidate_audio(self) -> None:
        """硬静音/Focus 抑制使所有旧 PCM 和 turn 身份立即失效。"""

        self._turn_id += 1
        self._completed_turn_id = self._turn_id
        self._pre_roll_sent_for_turn = False
        self._pre_roll.clear()
        self._pending_connect.clear()
        self._pending_turn.clear()
        self._pending_turn_speech = False
        self._active_start_audio = b""
        if self._state not in {
            VoiceLifecycleState.OFF,
            VoiceLifecycleState.BLOCKED,
            VoiceLifecycleState.SUSPENDED,
        }:
            self._state = VoiceLifecycleState.LOCAL_LISTEN

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
        }:
            return AudioDisposition.FORWARD
        return AudioDisposition.BLOCK
