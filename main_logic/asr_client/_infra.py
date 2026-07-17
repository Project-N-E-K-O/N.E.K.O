# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared contracts and lifecycle machinery for realtime ASR workers."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

import numpy as np
import soxr

from .provider_policy import AsrProviderPolicy
from .segment_aggregator import SegmentAggregator


logger = logging.getLogger(__name__)

_READY_TIMEOUT_SECONDS = 10.0
_CALLBACK_DRAIN_TIMEOUT_SECONDS = 5.0
_WORKER_CLOSE_TIMEOUT_SECONDS = 5.0
# 活跃音频队列按时长限额；chunk 数量只保留为兼容常量，不参与容量判断。
_REQUEST_QUEUE_SIZE = 64
_ACTIVE_QUEUE_MAX_AUDIO_MS = 2_000
_REQUEST_BACKPRESSURE_TIMEOUT_SECONDS = 2.0
_RESPONSE_QUEUE_SIZE = 128
_CALLBACK_QUEUE_SIZE = 64

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_OMNI_ONLY_FIELDS = frozenset(
    {
        "audio",
        "beta_fields",
        "enable_search",
        "input_audio_format",
        "input_audio_noise_reduction",
        "input_audio_transcription",
        "instructions",
        "language_code",
        "modalities",
        "model",
        "output_audio_format",
        "output_modalities",
        "repetition_penalty",
        "session",
        "temperature",
        "tool_choice",
        "tools",
        "turn_detection",
        "type",
        "voice",
    }
)

_RequestKind: TypeAlias = Literal["audio", "commit", "clear", "shutdown"]
_EventKind: TypeAlias = Literal[
    "ready",
    "utterance_started",
    "partial",
    "final",
    "error",
    "closed",
]
_UtteranceKey: TypeAlias = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class AsrSessionConfig:
    """Provider-neutral ASR settings frozen when the worker connects."""

    language: str = "zh"
    input_sample_rate_hz: Literal[16000, 48000] = 16000
    endpointing_mode: Literal["manual", "provider"] = "manual"

    def __post_init__(self) -> None:
        language = str(self.language).strip()
        if language.lower() == "auto":
            normalized_language = "auto"
        elif not _LANGUAGE_RE.fullmatch(language):
            raise ValueError("ASR_INVALID_CONFIG: invalid language")
        else:
            parts = language.split("-")
            normalized_parts = [parts[0].lower()]
            for part in parts[1:]:
                if len(part) == 2 and part.isalpha():
                    normalized_parts.append(part.upper())
                elif len(part) == 4 and part.isalpha():
                    normalized_parts.append(part.title())
                else:
                    normalized_parts.append(part)
            normalized_language = "-".join(normalized_parts)

        if self.input_sample_rate_hz not in (16000, 48000):
            raise ValueError(
                "ASR_INVALID_CONFIG: input_sample_rate_hz must be 16000 or 48000"
            )
        if self.endpointing_mode not in ("manual", "provider"):
            raise ValueError(
                "ASR_INVALID_CONFIG: endpointing_mode must be 'manual' or 'provider'"
            )
        object.__setattr__(self, "language", normalized_language)


@runtime_checkable
class RealtimeAsrSession(Protocol):
    """Stable session surface used by audio-producing callers."""

    @property
    def is_ready(self) -> bool: ...

    async def connect(
        self,
        instructions: str = "",
        native_audio: bool = False,
    ) -> None: ...

    async def update_session(self, config: Mapping[str, Any]) -> None: ...

    async def stream_audio(
        self,
        audio_chunk: bytes,
        *,
        sample_rate_hz: int | None = None,
    ) -> None: ...

    async def signal_user_activity_end(self) -> None: ...

    async def clear_audio_buffer(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _AsrWorkerRequest:
    """One normalized command sent from a session to its selected worker."""

    kind: _RequestKind
    generation: int
    buffer_epoch: int = 0
    utterance_id: int | None = None
    audio: bytes = b""


@dataclass(frozen=True, slots=True)
class _AsrWorkerEvent:
    """One provider-neutral event returned by an ASR worker."""

    kind: _EventKind
    generation: int
    buffer_epoch: int = 0
    utterance_id: int | None = None
    text: str = ""
    error_code: str = ""
    error_message: str = ""


AsrWorkerFn: TypeAlias = Callable[
    [
        asyncio.Queue[_AsrWorkerRequest],
        asyncio.Queue[_AsrWorkerEvent],
        str,
        AsrSessionConfig,
    ],
    Awaitable[None],
]


class _VoiceTurnAdapterProtocol(Protocol):
    async def start(self) -> None: ...

    async def push_audio(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        pcm16: bytes,
    ) -> None: ...

    async def reset(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None: ...

    async def wait_failure(self) -> Any: ...

    async def close(self) -> None: ...


VoiceTurnFactory: TypeAlias = Callable[
    [Callable[[int, int, int], Awaitable[None]]],
    _VoiceTurnAdapterProtocol,
]


class _SessionState(Enum):
    NEW = "new"
    CONNECTING = "connecting"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _CallbackItem:
    text: str
    generation: int
    buffer_epoch: int


class _RealtimeAsrSessionImpl:
    """Default asyncio session implementation shared by all ASR workers."""

    def __init__(
        self,
        *,
        worker_fn: AsrWorkerFn,
        api_key: str,
        config: AsrSessionConfig,
        on_input_transcript: Callable[[str], Awaitable[None]],
        on_connection_error: Callable[[str], Awaitable[None]],
        on_status_message: Callable[[str], Awaitable[None]] | None = None,
        on_turn_endpointed: Callable[[], Awaitable[None]] | None = None,
        voice_turn_factory: VoiceTurnFactory | None = None,
        provider_policy: AsrProviderPolicy | None = None,
    ) -> None:
        if not isinstance(config, AsrSessionConfig):
            raise TypeError("ASR_INVALID_CONFIG: config must be AsrSessionConfig")
        if not callable(worker_fn):
            raise TypeError("ASR_INVALID_CONFIG: worker_fn must be callable")
        if not callable(on_input_transcript) or not callable(on_connection_error):
            raise TypeError("ASR_INVALID_CONFIG: callbacks must be callable")
        if on_status_message is not None and not callable(on_status_message):
            raise TypeError("ASR_INVALID_CONFIG: status callback must be callable")
        if on_turn_endpointed is not None and not callable(on_turn_endpointed):
            raise TypeError("ASR_INVALID_CONFIG: endpoint callback must be callable")

        self._worker_fn = worker_fn
        self._api_key = api_key
        self._config = config
        self._on_input_transcript = on_input_transcript
        self._on_connection_error = on_connection_error
        self._on_status_message = on_status_message
        self._on_turn_endpointed = on_turn_endpointed
        self._voice_turn_factory = voice_turn_factory
        self._provider_policy = provider_policy
        self._voice_turn_adapter: _VoiceTurnAdapterProtocol | None = None
        self._voice_turn_watch_task: asyncio.Task[None] | None = None
        self._voice_turn_reset_task: asyncio.Task[None] | None = None

        self._state = _SessionState.NEW
        self._generation = 0
        self._buffer_epoch = 0
        self._utterance_id = 1
        self._utterance_has_audio = False
        self._input_sample_rate_hz: int | None = None
        self._resampler: soxr.ResampleStream | None = None
        self._active_utterance_keys: set[_UtteranceKey] = set()
        self._committed_utterance_keys: set[_UtteranceKey] = set()
        self._endpointed_turn_keys: set[_UtteranceKey] = set()
        self._utterance_order: deque[_UtteranceKey] = deque()
        self._pending_finals: dict[_UtteranceKey, str] = {}
        self._logical_turn_id = 1
        self._physical_segment_audio_bytes = 0
        self._provider_wire_audio_bytes = 0
        self._segment_aggregator = SegmentAggregator()
        self._segment_aggregator.begin_turn(self._logical_turn_id)

        self._request_queue: asyncio.Queue[_AsrWorkerRequest] | None = None
        self._response_queue: asyncio.Queue[_AsrWorkerEvent] | None = None
        self._callback_queue: asyncio.Queue[_CallbackItem] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._response_task: asyncio.Task[None] | None = None
        self._callback_task: asyncio.Task[None] | None = None
        self._callback_close_waiter: asyncio.Task[Any] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._ready_future: asyncio.Future[None] | None = None

        self._operation_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._closing_event = asyncio.Event()
        self._callback_close_event = asyncio.Event()
        self._connection_error_reported = False

    @property
    def is_ready(self) -> bool:
        return self._state is _SessionState.READY

    @property
    def provider_wire_audio_ms(self) -> int:
        """Count audio that crossed the provider request boundary."""

        return self._provider_wire_audio_bytes * 1_000 // (16_000 * 2)

    async def connect(
        self,
        instructions: str = "",
        native_audio: bool = False,
    ) -> None:
        # Compatibility-only Omni arguments deliberately never reach a worker.
        _ = (instructions, native_audio)
        async with self._connect_lock:
            if self._state is _SessionState.READY:
                return
            if self._state is not _SessionState.NEW:
                raise RuntimeError(
                    f"ASR_SESSION_NOT_READY: cannot connect a {self._state.value} session"
                )

            self._state = _SessionState.CONNECTING
            self._request_queue = asyncio.Queue()
            self._response_queue = asyncio.Queue(maxsize=_RESPONSE_QUEUE_SIZE)
            self._callback_queue = asyncio.Queue(maxsize=_CALLBACK_QUEUE_SIZE)
            self._ready_future = asyncio.get_running_loop().create_future()
            self._callback_task = asyncio.create_task(
                self._dispatch_callbacks(), name="asr-callback-dispatch"
            )
            self._response_task = asyncio.create_task(
                self._consume_responses(), name="asr-response-consumer"
            )
            self._worker_task = asyncio.create_task(
                self._run_worker(), name="asr-worker"
            )

            await self._emit_status("ASR_CONNECTING")
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._ready_future),
                    timeout=_READY_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                if self._ready_future is not None and not self._ready_future.done():
                    self._ready_future.cancel()
                await self.close()
                raise
            except asyncio.TimeoutError as exc:
                if self._ready_future is not None and not self._ready_future.done():
                    self._ready_future.cancel()
                await self._fail(
                    "ASR_CONNECT_TIMEOUT",
                    "worker did not become ready within 10 seconds",
                )
                raise RuntimeError(
                    "ASR_CONNECT_TIMEOUT: worker did not become ready within 10 seconds"
                ) from exc
            except Exception:
                if self._state in (_SessionState.CLOSING, _SessionState.CLOSED):
                    raise
                if self._state is not _SessionState.FAILED:
                    await self._fail(
                        "ASR_WORKER_FAILED", "worker failed during connect"
                    )
                raise

            worker_task = self._worker_task
            if self._state is not _SessionState.READY or worker_task is None:
                raise RuntimeError("ASR_WORKER_FAILED: worker exited during connect")
            if worker_task.done():
                await self._fail(
                    "ASR_WORKER_FAILED",
                    "worker exited immediately after becoming ready",
                )
                raise RuntimeError("ASR_WORKER_FAILED: worker exited during connect")
            if self._voice_turn_factory is not None:
                adapter: _VoiceTurnAdapterProtocol | None = None
                try:
                    adapter = self._voice_turn_factory(self._handle_voice_turn_commit)
                    await adapter.start()
                except Exception as exc:
                    if adapter is not None:
                        await self._close_voice_turn_instance(
                            adapter,
                            context="after start failure",
                        )
                    await self._fail(
                        "ASR_VOICE_TURN_START_FAILED",
                        "voice turn adapter failed to start",
                    )
                    raise RuntimeError(
                        "ASR_VOICE_TURN_START_FAILED: "
                        "voice turn adapter failed to start"
                    ) from exc
                if self._state is not _SessionState.READY:
                    await self._close_voice_turn_instance(
                        adapter,
                        context="after concurrent session failure",
                    )
                    raise RuntimeError(
                        "ASR_WORKER_FAILED: worker failed while voice turn started"
                    )
                self._voice_turn_adapter = adapter
                self._voice_turn_watch_task = asyncio.create_task(
                    self._watch_voice_turn_failure(adapter),
                    name="asr-voice-turn-watch",
                )

    async def update_session(self, config: Mapping[str, Any]) -> None:
        if not isinstance(config, Mapping):
            raise TypeError("ASR_INVALID_CONFIG: session update must be a mapping")

        unknown_fields = (
            set(config)
            - {
                "language",
                "input_sample_rate_hz",
            }
            - _OMNI_ONLY_FIELDS
        )
        if unknown_fields:
            names = ", ".join(sorted(map(str, unknown_fields)))
            raise ValueError(f"ASR_INVALID_CONFIG: unknown session field(s): {names}")

        updates = {
            key: config[key]
            for key in ("language", "input_sample_rate_hz")
            if key in config
        }
        if not updates:
            return
        if self._state is not _SessionState.NEW:
            raise RuntimeError(
                "ASR_SESSION_CONFIG_LOCKED: session is already connecting"
            )
        self._config = replace(self._config, **updates)

    async def stream_audio(
        self,
        audio_chunk: bytes,
        *,
        sample_rate_hz: int | None = None,
    ) -> None:
        if not isinstance(audio_chunk, bytes):
            raise TypeError("ASR_INVALID_PCM: audio_chunk must be bytes")
        if not audio_chunk:
            return

        async with self._operation_lock:
            if self._state is not _SessionState.READY:
                raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")
            if len(audio_chunk) % 2:
                raise ValueError("ASR_INVALID_PCM: PCM16LE data has an odd byte length")

            effective_rate = (
                sample_rate_hz
                if sample_rate_hz is not None
                else self._config.input_sample_rate_hz
            )
            if effective_rate not in (16000, 48000):
                raise ValueError(
                    "ASR_INVALID_CONFIG: sample rate must be 16000 or 48000"
                )
            if len(audio_chunk) > effective_rate * 2:
                raise ValueError(
                    "ASR_AUDIO_CHUNK_TOO_LARGE: one chunk may contain at most one second"
                )
            if self._input_sample_rate_hz is None:
                self._input_sample_rate_hz = effective_rate
                self._resampler = self._make_resampler()
            elif effective_rate != self._input_sample_rate_hz:
                raise ValueError(
                    "ASR_SAMPLE_RATE_CHANGED: a session cannot mix input sample rates"
                )

            normalized_audio = self._convert_audio(audio_chunk)
            if normalized_audio and self._uses_segment_aggregation:
                await self._append_segmented_audio_locked(normalized_audio)
            elif normalized_audio:
                await self._enqueue_request(
                    _AsrWorkerRequest(
                        kind="audio",
                        generation=self._generation,
                        buffer_epoch=self._buffer_epoch,
                        utterance_id=self._utterance_id,
                        audio=normalized_audio,
                    )
                )
                self._provider_wire_audio_bytes += len(normalized_audio)
                await self._push_voice_turn_audio(
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                    pcm16=normalized_audio,
                )
            # Even if soxr is still buffering, valid input belongs to this turn.
            if not self._uses_segment_aggregation or not normalized_audio:
                self._utterance_has_audio = True

    async def signal_user_activity_end(self) -> None:
        async with self._operation_lock:
            if self._state is not _SessionState.READY:
                raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")
            if not self._utterance_has_audio and not (
                self._uses_segment_aggregation
                and self._segment_aggregator.has_segments(self._logical_turn_id)
            ):
                return
            await self._commit_current_utterance_locked()

    async def clear_audio_buffer(self) -> None:
        async with self._operation_lock:
            if self._state is not _SessionState.READY:
                raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")
            self._buffer_epoch += 1
            self._utterance_id += 1
            self._utterance_has_audio = False
            self._active_utterance_keys.clear()
            self._committed_utterance_keys.clear()
            self._endpointed_turn_keys.clear()
            self._utterance_order.clear()
            self._pending_finals.clear()
            self._logical_turn_id += 1
            self._clear_segment_aggregation_state()
            self._reset_resampler()
            await self._enqueue_request(
                _AsrWorkerRequest(
                    kind="clear",
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                )
            )
            if self._voice_turn_adapter is not None:
                await self._reset_voice_turn_adapter(
                    self._voice_turn_adapter,
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                )

    async def close(self) -> None:
        current = asyncio.current_task()
        if current is self._callback_task:
            self._callback_close_waiter = current
            self._callback_close_event.set()

        if self._state is _SessionState.CLOSED:
            return

        close_task = self._close_task
        if close_task is None:
            # The state transition is synchronous so cancellation cannot leave
            # a READY session with a permanently-set closing event. Shielding
            # lets cleanup continue if the caller cancels its own wait.
            self._closing_event.set()
            self._state = _SessionState.CLOSING
            self._generation += 1
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_exception(
                    RuntimeError("ASR_SESSION_NOT_READY: session was closed")
                )
            close_task = asyncio.create_task(
                self._close_impl(), name="asr-session-close"
            )
            self._close_task = close_task

        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        async with self._operation_lock:
            self._utterance_has_audio = False
            self._active_utterance_keys.clear()
            self._committed_utterance_keys.clear()
            self._endpointed_turn_keys.clear()
            self._utterance_order.clear()
            self._pending_finals.clear()
            self._clear_segment_aggregation_state()
            self._reset_resampler()

            await self._unload_voice_turn_adapter(context="during close")

            if self._request_queue is not None:
                request = _AsrWorkerRequest(
                    kind="shutdown",
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                )
                try:
                    await asyncio.wait_for(
                        self._request_queue.put(request),
                        timeout=_WORKER_CLOSE_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning("ASR shutdown command timed out")

            if self._worker_task is not None and not self._worker_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._worker_task),
                        timeout=_WORKER_CLOSE_TIMEOUT_SECONDS,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    self._worker_task.cancel()

            if self._callback_queue is not None:
                drain_task = asyncio.create_task(self._callback_queue.join())
                callback_close_task = asyncio.create_task(
                    self._callback_close_event.wait()
                )
                done, pending = await asyncio.wait(
                    {drain_task, callback_close_task},
                    timeout=_CALLBACK_DRAIN_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    logger.warning("ASR callback drain timed out")
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            await self._shutdown()
            self._state = _SessionState.CLOSED
            await self._emit_status("ASR_CLOSED")

    async def _close_voice_turn_instance(
        self,
        adapter: _VoiceTurnAdapterProtocol,
        *,
        context: str,
    ) -> None:
        try:
            await adapter.close()
        except Exception:
            logger.exception("ASR voice turn adapter failed to close %s", context)

    async def _fail_voice_turn_operation(self, operation: str) -> RuntimeError:
        logger.warning("ASR voice turn %s failed", operation)
        await self._fail(
            "ASR_ENDPOINTING_FAILED",
            "required voice turn endpointing failed",
        )
        return RuntimeError(
            "ASR_ENDPOINTING_FAILED: required voice turn endpointing failed"
        )

    async def _push_voice_turn_audio(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        pcm16: bytes,
    ) -> None:
        adapter = self._voice_turn_adapter
        if adapter is None:
            return
        try:
            await adapter.push_audio(
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
                pcm16=pcm16,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = await self._fail_voice_turn_operation("audio push")
            raise failure from exc

    async def _reset_voice_turn_adapter(
        self,
        adapter: _VoiceTurnAdapterProtocol,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None:
        try:
            await adapter.reset(
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = await self._fail_voice_turn_operation("reset")
            raise failure from exc

    def _schedule_voice_turn_reset(
        self,
        adapter: _VoiceTurnAdapterProtocol,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        name: str,
    ) -> None:
        previous = self._voice_turn_reset_task

        async def run_reset() -> None:
            try:
                if previous is not None and previous is not asyncio.current_task():
                    await previous
                if (
                    adapter is not self._voice_turn_adapter
                    or self._state is not _SessionState.READY
                ):
                    return
                await self._reset_voice_turn_adapter(
                    adapter,
                    generation=generation,
                    buffer_epoch=buffer_epoch,
                    utterance_id=utterance_id,
                )
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                # _reset_voice_turn_adapter already moved the session to FAILED.
                return

        task = asyncio.create_task(run_reset(), name=name)
        self._voice_turn_reset_task = task

        def clear_finished_reset(finished: asyncio.Task[None]) -> None:
            if self._voice_turn_reset_task is finished:
                self._voice_turn_reset_task = None

        task.add_done_callback(clear_finished_reset)

    async def _unload_voice_turn_adapter(self, *, context: str) -> None:
        adapter, self._voice_turn_adapter = self._voice_turn_adapter, None
        watch_task, self._voice_turn_watch_task = (
            self._voice_turn_watch_task,
            None,
        )
        current_task = asyncio.current_task()
        reset_task, self._voice_turn_reset_task = (
            self._voice_turn_reset_task,
            None,
        )
        if reset_task is not None and reset_task is not current_task:
            try:
                await asyncio.wait_for(
                    asyncio.shield(reset_task),
                    timeout=_CALLBACK_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                reset_task.cancel()
                await asyncio.gather(reset_task, return_exceptions=True)
        if watch_task is not None and watch_task is not current_task:
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)
        if adapter is not None:
            await self._close_voice_turn_instance(adapter, context=context)

    async def _watch_voice_turn_failure(
        self,
        adapter: _VoiceTurnAdapterProtocol,
    ) -> None:
        try:
            failure = await adapter.wait_failure()
        except asyncio.CancelledError:
            return
        except Exception:
            failure = None
        if (
            adapter is not self._voice_turn_adapter
            or self._state is not _SessionState.READY
        ):
            return
        kind = getattr(failure, "kind", "runtime_error")
        stage = getattr(failure, "stage", "consumer")
        logger.warning(
            "ASR voice turn endpointing failed kind=%s stage=%s",
            kind if kind in ("unavailable", "runtime_error") else "runtime_error",
            (
                stage
                if stage in ("vad_load", "vad_feed", "smart_turn", "consumer")
                else "consumer"
            ),
        )
        await self._fail(
            "ASR_ENDPOINTING_FAILED",
            "required voice turn endpointing failed",
        )

    async def _handle_voice_turn_commit(
        self,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None:
        async with self._operation_lock:
            if (
                self._state is not _SessionState.READY
                or generation != self._generation
                or buffer_epoch != self._buffer_epoch
                or utterance_id
                != (
                    self._logical_turn_id
                    if self._uses_segment_aggregation
                    else self._utterance_id
                )
                or (
                    not self._utterance_has_audio
                    and not self._segment_aggregator.has_segments(
                        self._logical_turn_id
                    )
                )
            ):
                return
            await self._notify_turn_endpointed_locked(
                (generation, buffer_epoch, utterance_id)
            )
            await self._commit_current_utterance_locked()

    async def _notify_turn_endpointed_locked(self, key: _UtteranceKey) -> None:
        """Publish one semantic seal event before transport commit or final."""

        if key in self._endpointed_turn_keys:
            return
        self._endpointed_turn_keys.add(key)
        callback = self._on_turn_endpointed
        if callback is None:
            return
        try:
            await callback()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ASR turn endpoint callback failed")

    async def _commit_current_utterance_locked(self) -> None:
        if self._uses_segment_aggregation:
            await self._complete_segmented_logical_turn_locked()
            return
        generation = self._generation
        buffer_epoch = self._buffer_epoch
        utterance_id = self._utterance_id
        tail = self._flush_resampler()
        if tail:
            await self._enqueue_request(
                _AsrWorkerRequest(
                    kind="audio",
                    generation=generation,
                    buffer_epoch=buffer_epoch,
                    utterance_id=utterance_id,
                    audio=tail,
                )
            )
            self._provider_wire_audio_bytes += len(tail)
            await self._push_voice_turn_audio(
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
                pcm16=tail,
            )
        if self._config.endpointing_mode == "provider":
            self._utterance_has_audio = False
            self._reset_resampler()
            return
        await self._enqueue_request(
            _AsrWorkerRequest(
                kind="commit",
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
            )
        )
        self._utterance_id += 1
        self._utterance_has_audio = False
        self._reset_resampler()
        if self._voice_turn_adapter is not None:
            adapter = self._voice_turn_adapter
            next_generation = self._generation
            next_buffer_epoch = self._buffer_epoch
            next_utterance_id = self._utterance_id
            self._schedule_voice_turn_reset(
                adapter,
                generation=next_generation,
                buffer_epoch=next_buffer_epoch,
                utterance_id=next_utterance_id,
                name="asr-voice-turn-reset-after-commit",
            )
            # Let reset enqueue behind the audio that belongs to the completed
            # utterance without waiting for model/VAD work to drain. This keeps
            # explicit commit responsive while preserving FIFO identity order.
            await asyncio.sleep(0)

    @property
    def _uses_segment_aggregation(self) -> bool:
        policy = self._provider_policy
        return bool(
            policy is not None
            and policy.transport == "segmented"
            and policy.max_segment_ms is not None
        )

    @property
    def _segmented_max_audio_bytes(self) -> int:
        policy = self._provider_policy
        if policy is None or policy.max_segment_ms is None:
            return 0
        return (policy.max_segment_ms * 16_000 * 2 // 1_000) & ~1

    async def _append_segmented_audio_locked(self, audio: bytes) -> None:
        """Append PCM16 without allowing a physical request to exceed policy."""

        max_bytes = self._segmented_max_audio_bytes
        if max_bytes <= 0:
            raise RuntimeError("ASR_INVALID_CONFIG: segmented audio limit is invalid")
        offset = 0
        while offset < len(audio):
            remaining = max_bytes - self._physical_segment_audio_bytes
            if remaining <= 0:
                await self._commit_physical_segment_locked(logical_complete=False)
                remaining = max_bytes
            part_size = min(len(audio) - offset, remaining) & ~1
            if part_size <= 0:
                raise ValueError("ASR_INVALID_PCM: segmented PCM must be 2-byte aligned")
            part = audio[offset : offset + part_size]
            await self._enqueue_request(
                _AsrWorkerRequest(
                    kind="audio",
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                    audio=part,
                )
            )
            await self._push_voice_turn_audio(
                generation=self._generation,
                buffer_epoch=self._buffer_epoch,
                utterance_id=self._logical_turn_id,
                pcm16=part,
            )
            self._physical_segment_audio_bytes += part_size
            self._utterance_has_audio = True
            offset += part_size
            if self._physical_segment_audio_bytes == max_bytes:
                await self._commit_physical_segment_locked(logical_complete=False)

    async def _commit_physical_segment_locked(
        self,
        *,
        logical_complete: bool,
    ) -> None:
        logical_turn_id = self._logical_turn_id
        generation = self._generation
        buffer_epoch = self._buffer_epoch
        utterance_id = self._utterance_id
        if logical_complete:
            tail = self._flush_resampler()
            if tail:
                await self._append_segmented_audio_locked(tail)
        if not self._utterance_has_audio:
            if logical_complete:
                self._segment_aggregator.complete_turn(logical_turn_id)
            return
        utterance_id = self._utterance_id
        key = (generation, buffer_epoch, utterance_id)
        await self._enqueue_request(
            _AsrWorkerRequest(
                kind="commit",
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
            )
        )
        self._provider_wire_audio_bytes += self._physical_segment_audio_bytes
        if not self._segment_aggregator.register_segment(logical_turn_id, key):
            raise RuntimeError("ASR_SEGMENT_STATE_INVALID: duplicate physical segment")
        if logical_complete:
            self._segment_aggregator.complete_turn(logical_turn_id)
            self._reset_resampler()
        self._utterance_id += 1
        self._utterance_has_audio = False
        self._physical_segment_audio_bytes = 0

    async def _complete_segmented_logical_turn_locked(self) -> None:
        completed_turn_id = self._logical_turn_id
        await self._commit_physical_segment_locked(logical_complete=True)
        if not self._segment_aggregator.has_segments(completed_turn_id):
            return
        self._segment_aggregator.complete_turn(completed_turn_id)
        ready_texts = self._collect_ready_segmented_transcripts_locked()
        if ready_texts:
            assert self._callback_queue is not None
            for ready_text in ready_texts:
                await self._callback_queue.put(
                    _CallbackItem(
                        text=ready_text,
                        generation=self._generation,
                        buffer_epoch=self._buffer_epoch,
                    )
                )
        self._logical_turn_id += 1
        self._segment_aggregator.begin_turn(self._logical_turn_id)
        if self._voice_turn_adapter is None:
            return
        adapter = self._voice_turn_adapter
        next_generation = self._generation
        next_buffer_epoch = self._buffer_epoch
        next_logical_turn_id = self._logical_turn_id
        self._schedule_voice_turn_reset(
            adapter,
            generation=next_generation,
            buffer_epoch=next_buffer_epoch,
            utterance_id=next_logical_turn_id,
            name="asr-voice-turn-reset-after-logical-commit",
        )
        await asyncio.sleep(0)

    def _collect_ready_segmented_transcripts_locked(self) -> list[str]:
        ready_transcripts = self._segment_aggregator.collect_ready()
        for transcript in ready_transcripts:
            for key in transcript.segment_ids:
                self._active_utterance_keys.discard(key)
                self._committed_utterance_keys.discard(key)
                self._pending_finals.pop(key, None)
                try:
                    self._utterance_order.remove(key)
                except ValueError:
                    pass
        return [
            transcript.text for transcript in ready_transcripts if transcript.text
        ]

    def _clear_segment_aggregation_state(self) -> None:
        self._physical_segment_audio_bytes = 0
        self._segment_aggregator.clear(next_turn_id=self._logical_turn_id)
        self._segment_aggregator.begin_turn(self._logical_turn_id)

    async def _run_worker(self) -> None:
        assert self._request_queue is not None
        assert self._response_queue is not None
        try:
            await self._worker_fn(
                self._request_queue,
                self._response_queue,
                self._api_key,
                self._config,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._state in (
                _SessionState.CLOSING,
                _SessionState.CLOSED,
                _SessionState.FAILED,
            ):
                return
            await self._response_queue.put(
                _AsrWorkerEvent(
                    kind="error",
                    generation=self._generation,
                    buffer_epoch=self._buffer_epoch,
                    utterance_id=self._utterance_id,
                    error_code="ASR_WORKER_FAILED",
                    error_message="worker raised an unexpected exception",
                )
            )
        else:
            # Workers normally emit ``closed`` themselves. This synthetic
            # event makes an accidental bare return terminal as well; a
            # duplicate closed event during shutdown is harmless.
            await self._response_queue.put(
                _AsrWorkerEvent(kind="closed", generation=self._generation)
            )

    async def _consume_responses(self) -> None:
        assert self._response_queue is not None
        while True:
            event = await self._response_queue.get()
            try:
                should_stop = await self._handle_event(event)
            finally:
                self._response_queue.task_done()
            if should_stop:
                return

    async def _dispatch_callbacks(self) -> None:
        assert self._callback_queue is not None
        while True:
            item = await self._callback_queue.get()
            try:
                if (
                    item.generation == self._generation
                    and item.buffer_epoch == self._buffer_epoch
                ):
                    await self._on_input_transcript(item.text)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ASR transcript callback failed")
            finally:
                self._callback_queue.task_done()
            if self._state is _SessionState.CLOSED:
                return

    async def _handle_event(self, event: _AsrWorkerEvent) -> bool:
        if not isinstance(event, _AsrWorkerEvent):
            await self._fail("ASR_WORKER_FAILED", "worker returned an invalid event")
            return True

        if event.kind == "ready":
            if (
                self._state is not _SessionState.CONNECTING
                or event.generation != self._generation
            ):
                return False
            self._state = _SessionState.READY
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_result(None)
            await self._emit_status("ASR_READY")
            return False

        if event.generation != self._generation:
            return False
        if event.kind == "utterance_started":
            if (
                self._config.endpointing_mode != "provider"
                or event.utterance_id is None
            ):
                return False
            key = (event.generation, event.buffer_epoch, event.utterance_id)
            async with self._operation_lock:
                if (
                    self._state is not _SessionState.READY
                    or event.generation != self._generation
                    or event.buffer_epoch != self._buffer_epoch
                ):
                    return False
                # Repeated provider speech-start notifications for one item
                # are idempotent. The worker owns provider-item -> internal-ID
                # mapping, so no provider identifier leaks into this layer.
                if key not in self._active_utterance_keys:
                    self._active_utterance_keys.add(key)
                    self._utterance_order.append(key)
            return False
        if event.kind == "partial":
            callback = getattr(self, "_on_partial_transcript", None)
            text = event.text.strip()
            if (
                callback is not None
                and text
                and self._state is _SessionState.READY
                and event.buffer_epoch == self._buffer_epoch
            ):
                try:
                    await callback(text)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("ASR partial callback failed")
            return False
        if event.kind == "final":
            if event.utterance_id is None:
                return False
            text = event.text.strip()
            if not text:
                return False
            key = (event.generation, event.buffer_epoch, event.utterance_id)
            async with self._operation_lock:
                if (
                    self._state is not _SessionState.READY
                    or event.generation != self._generation
                    or event.buffer_epoch != self._buffer_epoch
                ):
                    return False
                if key in self._pending_finals:
                    logger.warning(
                        "ASR worker returned a duplicate or conflicting final"
                    )
                    return False
                valid_keys = (
                    self._active_utterance_keys
                    if self._config.endpointing_mode == "provider"
                    else self._committed_utterance_keys
                )
                if key not in valid_keys:
                    logger.warning(
                        "ASR worker returned a final for an inactive utterance"
                    )
                    return False
                if self._config.endpointing_mode == "provider":
                    await self._notify_turn_endpointed_locked(key)
                if self._uses_segment_aggregation:
                    if not self._segment_aggregator.record_transcript(key, text):
                        logger.warning(
                            "ASR worker returned a duplicate or conflicting final"
                        )
                        return False
                    ready_texts = self._collect_ready_segmented_transcripts_locked()
                    ready_items = [
                        _CallbackItem(
                            text=ready_text,
                            generation=event.generation,
                            buffer_epoch=event.buffer_epoch,
                        )
                        for ready_text in ready_texts
                    ]
                else:
                    self._pending_finals[key] = text
                    ready_items = []
                    while (
                        self._utterance_order
                        and self._utterance_order[0] in self._pending_finals
                    ):
                        ready_key = self._utterance_order.popleft()
                        ready_items.append(
                            _CallbackItem(
                                text=self._pending_finals.pop(ready_key),
                                generation=ready_key[0],
                                buffer_epoch=ready_key[1],
                            )
                        )
                        self._active_utterance_keys.discard(ready_key)
                        self._committed_utterance_keys.discard(ready_key)
            assert self._callback_queue is not None
            for ready_item in ready_items:
                await self._callback_queue.put(ready_item)
            return False
        if event.kind == "error":
            if (
                event.utterance_id is not None
                and event.buffer_epoch != self._buffer_epoch
            ):
                return False
            await self._fail(
                event.error_code or "ASR_WORKER_FAILED",
                event.error_message or "worker reported a provider error",
            )
            return True
        if event.kind == "closed":
            if self._state is _SessionState.CLOSING:
                return True
            await self._fail("ASR_WORKER_FAILED", "worker closed unexpectedly")
            return True

        await self._fail("ASR_WORKER_FAILED", "worker returned an unknown event")
        return True

    async def _fail(self, error_code: str, message: str) -> None:
        if self._state in (_SessionState.FAILED, _SessionState.CLOSED):
            return
        self._state = _SessionState.FAILED
        self._generation += 1
        self._closing_event.set()
        await self._unload_voice_turn_adapter(context="during failure")
        self._active_utterance_keys.clear()
        self._committed_utterance_keys.clear()
        self._utterance_order.clear()
        self._pending_finals.clear()
        self._clear_segment_aggregation_state()
        safe_code = (
            error_code
            if re.fullmatch(r"ASR_[A-Z0-9_]+", error_code or "")
            else "ASR_WORKER_FAILED"
        )
        safe_message = self._sanitize_error(message)
        error = f"{safe_code}: {safe_message}"
        if self._ready_future is not None and not self._ready_future.done():
            self._ready_future.set_exception(RuntimeError(error))
        if (
            self._worker_task is not None
            and self._worker_task is not asyncio.current_task()
        ):
            self._worker_task.cancel()
        try:
            await self._emit_connection_error_once(error)
        finally:
            if self._callback_queue is not None:
                try:
                    await asyncio.wait_for(
                        self._callback_queue.join(),
                        timeout=_CALLBACK_DRAIN_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning("ASR callback drain timed out after failure")
            await self._shutdown()

    def _queued_audio_ms(self) -> int:
        queue = self._request_queue
        if queue is None:
            return 0
        # asyncio.Queue 没有公开快照接口；这里仅在 session 自己的 operation
        # lock 内读取其 deque，不修改内部结构。
        queued = getattr(queue, "_queue", ())
        audio_bytes = sum(
            len(item.audio)
            for item in queued
            if isinstance(item, _AsrWorkerRequest) and item.kind == "audio"
        )
        return audio_bytes * 1_000 // (16_000 * 2)

    async def _wait_for_audio_queue_capacity(
        self,
        request: _AsrWorkerRequest,
    ) -> None:
        request_ms = len(request.audio) * 1_000 // (16_000 * 2)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _REQUEST_BACKPRESSURE_TIMEOUT_SECONDS
        while self._queued_audio_ms() + request_ms > _ACTIVE_QUEUE_MAX_AUDIO_MS:
            if self._closing_event.is_set() or self._state is not _SessionState.READY:
                raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    "ASR_STREAM_BACKPRESSURE: active audio queue exceeded "
                    f"{_ACTIVE_QUEUE_MAX_AUDIO_MS}ms"
                )
            try:
                await asyncio.wait_for(
                    self._closing_event.wait(),
                    timeout=min(0.01, remaining),
                )
            except asyncio.TimeoutError:
                continue
            raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")

    async def _enqueue_request(self, request: _AsrWorkerRequest) -> None:
        worker_task = self._worker_task
        if (
            self._state is not _SessionState.READY
            or self._request_queue is None
            or self._closing_event.is_set()
            or worker_task is None
            or worker_task.done()
        ):
            raise RuntimeError("ASR_SESSION_NOT_READY: session is not ready")

        if request.kind == "audio":
            await self._wait_for_audio_queue_capacity(request)

        key: _UtteranceKey | None = None
        added_active = False
        added_committed = False
        added_order = False
        if request.utterance_id is not None and request.kind in ("audio", "commit"):
            key = (
                request.generation,
                request.buffer_epoch,
                request.utterance_id,
            )
            if (
                self._config.endpointing_mode == "manual"
                and request.kind == "audio"
                and key not in self._active_utterance_keys
            ):
                self._active_utterance_keys.add(key)
                added_active = True
            if request.kind == "commit" and key not in self._committed_utterance_keys:
                self._committed_utterance_keys.add(key)
                added_committed = True
                self._utterance_order.append(key)
                added_order = True

        put_task = asyncio.create_task(self._request_queue.put(request))
        closing_task = asyncio.create_task(self._closing_event.wait())
        watched: set[asyncio.Task[Any]] = {put_task, closing_task, worker_task}
        try:
            done, _ = await asyncio.wait(
                watched,
                timeout=_REQUEST_BACKPRESSURE_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            put_task.cancel()
            closing_task.cancel()
            await asyncio.gather(put_task, closing_task, return_exceptions=True)
            if key is not None:
                if added_active:
                    self._active_utterance_keys.discard(key)
                if added_committed:
                    self._committed_utterance_keys.discard(key)
                if added_order:
                    try:
                        self._utterance_order.remove(key)
                    except ValueError:
                        pass
            raise

        if not done:
            put_task.cancel()
            closing_task.cancel()
            await asyncio.gather(put_task, closing_task, return_exceptions=True)
            if key is not None:
                if added_active:
                    self._active_utterance_keys.discard(key)
                if added_committed:
                    self._committed_utterance_keys.discard(key)
                if added_order:
                    try:
                        self._utterance_order.remove(key)
                    except ValueError:
                        pass
            raise RuntimeError(
                "ASR_STREAM_BACKPRESSURE: provider request queue remained full"
            )

        if put_task in done:
            try:
                await put_task
            except BaseException:
                closing_task.cancel()
                await asyncio.gather(closing_task, return_exceptions=True)
                if key is not None:
                    if added_active:
                        self._active_utterance_keys.discard(key)
                    if added_committed:
                        self._committed_utterance_keys.discard(key)
                    if added_order:
                        try:
                            self._utterance_order.remove(key)
                        except ValueError:
                            pass
                raise
            closing_task.cancel()
            await asyncio.gather(closing_task, return_exceptions=True)
            return

        put_task.cancel()
        closing_task.cancel()
        await asyncio.gather(put_task, closing_task, return_exceptions=True)
        if key is not None:
            if added_active:
                self._active_utterance_keys.discard(key)
            if added_committed:
                self._committed_utterance_keys.discard(key)
            if added_order:
                try:
                    self._utterance_order.remove(key)
                except ValueError:
                    pass
        raise RuntimeError("ASR_SESSION_NOT_READY: worker is no longer running")

    def _make_resampler(self) -> soxr.ResampleStream | None:
        if self._input_sample_rate_hz != 48000:
            return None
        return soxr.ResampleStream(
            48000,
            16000,
            1,
            dtype="float32",
            quality="HQ",
        )

    def _convert_audio(self, audio_chunk: bytes) -> bytes:
        if self._resampler is None:
            return audio_chunk
        samples = np.frombuffer(audio_chunk, dtype="<i2").astype(np.float32)
        samples /= 32768.0
        output = self._resampler.resample_chunk(samples)
        if len(output) == 0:
            return b""
        return (output * 32768.0).clip(-32768, 32767).astype("<i2").tobytes()

    def _flush_resampler(self) -> bytes:
        if self._resampler is None:
            return b""
        output = self._resampler.resample_chunk(
            np.empty(0, dtype=np.float32),
            last=True,
        )
        if len(output) == 0:
            return b""
        return (output * 32768.0).clip(-32768, 32767).astype("<i2").tobytes()

    def _reset_resampler(self) -> None:
        if self._resampler is not None:
            self._resampler.clear()
        self._resampler = self._make_resampler()

    async def _emit_status(self, status: str) -> None:
        if self._on_status_message is None:
            return
        try:
            await self._on_status_message(status)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ASR status callback failed")

    async def _emit_connection_error_once(self, error: str) -> None:
        if self._connection_error_reported:
            return
        self._connection_error_reported = True
        try:
            await self._on_connection_error(error)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ASR connection error callback failed")

    async def _shutdown(self) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in (
                self._worker_task,
                self._response_task,
                self._callback_task,
            )
            if (
                task is not None
                and task is not current
                and task is not self._callback_close_waiter
                and not task.done()
            )
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _validate_language(self, language: str) -> str:
        # Kept as a private seam for future worker-specific language mapping.
        return AsrSessionConfig(language=language).language

    def _sanitize_error(self, message: str) -> str:
        safe = str(message or "worker failed")
        if self._api_key:
            safe = safe.replace(self._api_key, "[REDACTED]")
        safe = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", safe)
        safe = re.sub(r"([?&](?:api_?key|token|key)=)[^&\s]+", r"\1[REDACTED]", safe)
        safe = re.sub(r"https?://[^\s?#]+[?][^\s]+", "[REDACTED_URL]", safe)
        safe = " ".join(safe.split())
        return safe[:300] or "worker failed"
