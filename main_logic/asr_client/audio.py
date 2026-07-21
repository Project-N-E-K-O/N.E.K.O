"""ASR microphone preprocessing, buffering, ingress queues, and dispatch."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from utils.audio_processor import AudioProcessor

if TYPE_CHECKING:
    from .lifecycle import VoiceIngressToken, VoiceTurnToken


class _AudioProcessorProtocol(Protocol):
    speech_probability: float

    def process_chunk(self, audio_bytes: bytes) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessedVoiceFrame:
    pcm16: bytes
    sample_rate_hz: int
    speech_probability: float | None
    rnnoise_available: bool = False


class VoiceInputAudioPipeline:
    """Validate PCM and normalize PC 48 kHz or mobile 16 kHz to 16 kHz."""

    def __init__(
        self,
        *,
        processor_factory: Callable[[], _AudioProcessorProtocol] | None = None,
    ) -> None:
        self._processor_factory = processor_factory or AudioProcessor
        self._processor: _AudioProcessorProtocol | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def process(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> ProcessedVoiceFrame:
        if not isinstance(pcm16, bytes):
            raise TypeError("microphone PCM must be bytes")
        if len(pcm16) % 2:
            raise ValueError("microphone PCM16 contains an incomplete sample")
        if sample_rate_hz not in (16_000, 48_000):
            raise ValueError("microphone sample rate must be 16000 or 48000")
        if self._closed:
            raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
        if not pcm16:
            return ProcessedVoiceFrame(b"", 16_000, None, False)
        if sample_rate_hz == 16_000:
            return ProcessedVoiceFrame(pcm16, 16_000, None, False)

        async with self._lock:
            if self._closed:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
            if self._processor is None:
                self._processor = self._processor_factory()
            processed = await asyncio.to_thread(self._processor.process_chunk, pcm16)
            probability = float(self._processor.speech_probability)
            rnnoise_available = bool(
                getattr(
                    self._processor,
                    "rnnoise_available",
                    getattr(self._processor, "_denoiser", None) is not None,
                )
            )
        return ProcessedVoiceFrame(
            processed,
            16_000,
            probability if rnnoise_available else None,
            rnnoise_available,
        )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            processor, self._processor = self._processor, None
            if processor is not None:
                await asyncio.to_thread(processor.close)


class AudioRingBuffer:
    """Retain the newest fixed-duration mono PCM16 audio without disk writes."""

    def __init__(self, *, capacity_ms: int, sample_rate_hz: int = 16_000) -> None:
        if capacity_ms <= 0:
            raise ValueError("capacity_ms must be positive")
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        self._sample_rate_hz = sample_rate_hz
        self._capacity_bytes = sample_rate_hz * 2 * capacity_ms // 1_000
        self._capacity_bytes -= self._capacity_bytes % 2
        if self._capacity_bytes <= 0:
            raise ValueError("capacity_ms is too small for the sample rate")
        self._audio = bytearray()

    @property
    def duration_ms(self) -> int:
        return len(self._audio) * 1_000 // (self._sample_rate_hz * 2)

    @property
    def sample_rate_hz(self) -> int:
        return self._sample_rate_hz

    def append(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int | None = None,
    ) -> bytes:
        if not isinstance(pcm16, bytes):
            raise TypeError("PCM16 audio must be bytes")
        if len(pcm16) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        effective_rate = sample_rate_hz or self._sample_rate_hz
        if effective_rate != self._sample_rate_hz:
            raise ValueError("audio sample rate does not match the ring buffer")
        if not pcm16:
            return b""

        self._audio.extend(pcm16)
        overflow = len(self._audio) - self._capacity_bytes
        if overflow <= 0:
            return b""
        overflow += overflow % 2
        dropped = bytes(self._audio[:overflow])
        del self._audio[:overflow]
        return dropped

    def peek(self) -> bytes:
        return bytes(self._audio)

    def drain(self) -> bytes:
        payload = bytes(self._audio)
        self._audio.clear()
        return payload

    def clear(self) -> None:
        self._audio.clear()


@dataclass(frozen=True, slots=True)
class HotSwapAudioFrame:
    """One normalized PCM16 frame with the identity captured at ingress."""

    pcm16: bytes
    token: VoiceIngressToken
    speech_probability: float | None = None
    rnnoise_available: bool = False


class HotSwapAudioBuffer:
    """Bound hot-swap PCM by duration without silently dropping middle audio."""

    def __init__(self, *, capacity_ms: int = 8_000) -> None:
        self._audio = AudioRingBuffer(capacity_ms=capacity_ms)
        self._frames: list[HotSwapAudioFrame] = []

    @property
    def duration_ms(self) -> int:
        return self._audio.duration_ms

    def append(self, frame: HotSwapAudioFrame) -> bool:
        """Append a frame, or clear the whole candidate when capacity overflows."""

        dropped = self._audio.append(frame.pcm16, sample_rate_hz=16_000)
        if dropped:
            self.clear()
            return False
        self._frames.append(frame)
        return True

    def drain(self) -> tuple[HotSwapAudioFrame, ...]:
        frames = tuple(self._frames)
        self._frames.clear()
        self._audio.clear()
        return frames

    def clear(self) -> None:
        self._frames.clear()
        self._audio.clear()

    def __bool__(self) -> bool:
        return bool(self._frames)

    def __len__(self) -> int:
        return len(self._frames)


@dataclass(frozen=True, slots=True)
class QueuedMicFrame:
    message: dict
    duration_us: int
    source_rate_hz: int
    token: VoiceIngressToken
    received_at: float

    @classmethod
    def from_message(
        cls,
        message: dict,
        *,
        token: VoiceIngressToken,
        received_at: float | None = None,
    ) -> "QueuedMicFrame":
        samples = message.get("data")
        if not isinstance(samples, list):
            raise ValueError("MIC_PCM_SAMPLES_REQUIRED")
        declared_rate_hz = message.get("sample_rate_hz")
        if declared_rate_hz is None:
            source_rate_hz = 48_000 if len(samples) == 480 else 16_000
        elif declared_rate_hz in {16_000, 48_000}:
            source_rate_hz = int(declared_rate_hz)
        else:
            raise ValueError("MIC_SAMPLE_RATE_UNSUPPORTED")
        duration_us = (
            len(samples) * 1_000_000 + source_rate_hz - 1
        ) // source_rate_hz
        return cls(
            message=message,
            duration_us=duration_us,
            source_rate_hz=source_rate_hz,
            token=token,
            received_at=time.monotonic() if received_at is None else received_at,
        )


class AudioDurationQueue:
    """An asyncio queue bounded by both PCM duration and frame count."""

    def __init__(self, *, capacity_us: int, max_frames: int) -> None:
        if capacity_us <= 0 or max_frames <= 0:
            raise ValueError("audio queue limits must be positive")
        self.capacity_us = capacity_us
        self.maxsize = max_frames
        self._duration_us = 0
        self._queue: asyncio.Queue[QueuedMicFrame] = asyncio.Queue(
            maxsize=max_frames
        )

    @property
    def duration_us(self) -> int:
        return self._duration_us

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def can_accept(self, frame: QueuedMicFrame) -> bool:
        return bool(
            self._queue.qsize() < self.maxsize
            and self._duration_us + frame.duration_us <= self.capacity_us
        )

    def put_nowait(self, frame: QueuedMicFrame) -> None:
        if not self.can_accept(frame):
            raise asyncio.QueueFull
        self._queue.put_nowait(frame)
        self._duration_us += frame.duration_us

    async def get(self) -> QueuedMicFrame:
        frame = await self._queue.get()
        self._duration_us -= frame.duration_us
        return frame

    def get_nowait(self) -> QueuedMicFrame:
        frame = self._queue.get_nowait()
        self._duration_us -= frame.duration_us
        return frame

    def task_done(self) -> None:
        self._queue.task_done()


@dataclass(frozen=True, slots=True)
class AsrActivateCommand:
    generation: int
    turn_token: VoiceTurnToken
    session_ref: Any
    buffered_pcm16: bytes
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class AsrAudioCommand:
    generation: int
    turn_token: VoiceTurnToken
    session_ref: Any
    sequence_no: int
    pcm16: bytes
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class AsrSealCommand:
    generation: int
    turn_token: VoiceTurnToken
    session_ref: Any
    after_sequence: int


_Command: TypeAlias = AsrActivateCommand | AsrAudioCommand | AsrSealCommand
_Validator: TypeAlias = Callable[["VoiceTurnToken", Any], bool]
_WireCallback: TypeAlias = Callable[["VoiceTurnToken", Any, int], Awaitable[None]]
_FailureCallback: TypeAlias = Callable[["VoiceTurnToken", BaseException], Awaitable[None]]


class AsrAudioDispatcher:
    """Serialize all writes for one logical turn before its seal barrier."""

    def __init__(
        self,
        *,
        validator: _Validator,
        on_wire_audio: _WireCallback,
        on_failure: _FailureCallback,
        max_commands: int = 256,
    ) -> None:
        if max_commands <= 0:
            raise ValueError("ASR audio command capacity must be positive")
        self._validator = validator
        self._on_wire_audio = on_wire_audio
        self._on_failure = on_failure
        self._queue: asyncio.Queue[_Command] = asyncio.Queue(maxsize=max_commands)
        self._worker: asyncio.Task[None] | None = None
        self._failure_tasks: set[asyncio.Task[None]] = set()
        self._generation = 0
        self._turn_token: VoiceTurnToken | None = None
        self._session_ref: Any = None
        self._state: Literal["idle", "active", "sealed", "aborted"] = "idle"
        self._last_sequence = 0
        self._enqueued_at: dict[int, float] = {}
        self.asr_audio_command_queue_ms = 0
        self.asr_abort_discarded_command_count = 0
        self.provider_wire_sequence = 0

    @property
    def active_turn(self) -> VoiceTurnToken | None:
        return self._turn_token if self._state in {"active", "sealed"} else None

    def activate(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
        buffered_pcm16: bytes,
        *,
        sample_rate_hz: int = 16_000,
    ) -> bool:
        if sample_rate_hz <= 0 or len(buffered_pcm16) % 2:
            raise ValueError("ASR_ACTIVATE_INVALID_PCM")
        self._generation += 1
        self._turn_token = turn_token
        self._session_ref = session_ref
        self._state = "active"
        self._last_sequence = 0
        return self._put(
            AsrActivateCommand(
                self._generation,
                turn_token,
                session_ref,
                buffered_pcm16,
                sample_rate_hz,
            )
        )

    def enqueue_audio(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        sequence_no: int,
    ) -> bool:
        if not pcm16:
            return True
        if len(pcm16) % 2 or sample_rate_hz <= 0 or sequence_no <= 0:
            raise ValueError("ASR_AUDIO_COMMAND_INVALID")
        if (
            self._state != "active"
            or self._turn_token != turn_token
            or self._session_ref is not session_ref
            or sequence_no <= self._last_sequence
        ):
            return False
        self._last_sequence = sequence_no
        return self._put(
            AsrAudioCommand(
                self._generation,
                turn_token,
                session_ref,
                sequence_no,
                pcm16,
                sample_rate_hz,
            )
        )

    def seal(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
        *,
        after_sequence: int,
    ) -> bool:
        if (
            self._state != "active"
            or self._turn_token != turn_token
            or self._session_ref is not session_ref
            or after_sequence < self._last_sequence
        ):
            return False
        self._state = "sealed"
        return self._put(
            AsrSealCommand(
                self._generation,
                turn_token,
                session_ref,
                after_sequence,
            )
        )

    def abort(self, turn_token: VoiceTurnToken | None = None) -> None:
        if turn_token is not None and self._turn_token != turn_token:
            return
        discarded = 0
        while True:
            try:
                command = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._enqueued_at.pop(id(command), None)
            self._queue.task_done()
            discarded += 1
        self.asr_abort_discarded_command_count += discarded
        self._generation += 1
        self._turn_token = None
        self._session_ref = None
        self._state = "aborted"
        self._last_sequence = 0

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        self.abort()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def _put(self, command: _Command) -> bool:
        self._ensure_worker()
        try:
            self._queue.put_nowait(command)
        except asyncio.QueueFull:
            self.abort(command.turn_token)
            failure_task = asyncio.create_task(
                self._on_failure(
                    command.turn_token,
                    RuntimeError("ASR_AUDIO_COMMAND_BACKPRESSURE"),
                ),
                name="asr-audio-command-backpressure",
            )
            self._failure_tasks.add(failure_task)
            failure_task.add_done_callback(self._failure_tasks.discard)
            return False
        self._enqueued_at[id(command)] = time.monotonic()
        return True

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(), name="independent-asr-audio-dispatcher"
            )

    async def _run(self) -> None:
        while True:
            command = await self._queue.get()
            try:
                queued_at = self._enqueued_at.pop(id(command), None)
                if queued_at is not None:
                    self.asr_audio_command_queue_ms = int(
                        (time.monotonic() - queued_at) * 1_000
                    )
                if not self._command_is_current(command):
                    continue
                if isinstance(command, AsrSealCommand):
                    await command.session_ref.signal_user_activity_end()
                    if self._command_is_current(command):
                        self._state = "idle"
                        self._turn_token = None
                        self._session_ref = None
                    continue
                payload = (
                    command.buffered_pcm16
                    if isinstance(command, AsrActivateCommand)
                    else command.pcm16
                )
                max_bytes = command.sample_rate_hz * 2
                for offset in range(0, len(payload), max_bytes):
                    if not self._command_is_current(command):
                        break
                    chunk = payload[offset : offset + max_bytes]
                    await command.session_ref.stream_audio(
                        chunk,
                        sample_rate_hz=command.sample_rate_hz,
                    )
                    self.provider_wire_sequence += 1
                    await self._on_wire_audio(
                        command.turn_token,
                        command.session_ref,
                        len(chunk),
                    )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self.abort(command.turn_token)
                await self._on_failure(command.turn_token, exc)
            finally:
                self._queue.task_done()

    def _command_is_current(self, command: _Command) -> bool:
        return bool(
            command.generation == self._generation
            and self._state in {"active", "sealed"}
            and self._turn_token == command.turn_token
            and self._session_ref is command.session_ref
            and self._validator(command.turn_token, command.session_ref)
        )
