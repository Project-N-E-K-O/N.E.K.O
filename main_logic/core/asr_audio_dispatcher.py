"""Ordered independent-ASR audio, seal, and abort command dispatcher."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from main_logic.asr_client.lifecycle_contracts import VoiceTurnToken


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
_Validator: TypeAlias = Callable[[VoiceTurnToken, Any], bool]
_WireCallback: TypeAlias = Callable[[VoiceTurnToken, Any, int], Awaitable[None]]
_FailureCallback: TypeAlias = Callable[[VoiceTurnToken, BaseException], Awaitable[None]]


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
        self._generation = 0
        self._turn_token: VoiceTurnToken | None = None
        self._session_ref: Any = None
        self._state: Literal["idle", "active", "sealed", "aborted"] = "idle"
        self._last_sequence = 0

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
            asyncio.create_task(
                self._on_failure(
                    command.turn_token,
                    RuntimeError("ASR_AUDIO_COMMAND_BACKPRESSURE"),
                ),
                name="asr-audio-command-backpressure",
            )
            return False
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
