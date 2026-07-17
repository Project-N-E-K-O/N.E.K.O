"""Serialize accepted independent-ASR text without blocking ASR lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from main_logic.asr_client.lifecycle_contracts import FinalKey, VoiceTurnToken


@dataclass(frozen=True, slots=True)
class TranscriptEnvelope:
    turn_token: VoiceTurnToken
    core_session_ref: object
    provider: str
    text: str

    @property
    def final_key(self) -> FinalKey:
        return FinalKey.from_turn(self.turn_token)


class CoreTranscriptDispatcher:
    """Own bounded Core delivery slots and one serial dispatch worker."""

    def __init__(
        self,
        dispatch: Callable[[TranscriptEnvelope], Awaitable[None]],
        *,
        capacity: int = 8,
    ) -> None:
        if capacity <= 0:
            raise ValueError("dispatcher capacity must be positive")
        self._dispatch = dispatch
        self._capacity = capacity
        self._queue: asyncio.Queue[TranscriptEnvelope] = asyncio.Queue(
            maxsize=capacity
        )
        self._reservations: set[FinalKey] = set()
        self._worker: asyncio.Task[None] | None = None
        self._active: TranscriptEnvelope | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    def try_reserve(self, key: FinalKey) -> bool:
        if key in self._reservations:
            return True
        occupied = (
            len(self._reservations)
            + self._queue.qsize()
            + int(self._active is not None)
        )
        if occupied >= self._capacity:
            return False
        self._reservations.add(key)
        return True

    def release(self, key: FinalKey) -> None:
        self._reservations.discard(key)
        self._set_idle_if_empty()

    def submit(self, envelope: TranscriptEnvelope) -> None:
        key = envelope.final_key
        if key not in self._reservations:
            raise RuntimeError("ASR_TRANSCRIPT_SLOT_NOT_RESERVED")
        self._reservations.remove(key)
        self._queue.put_nowait(envelope)
        self._idle.clear()
        self._ensure_worker()

    def invalidate_all(self) -> None:
        """Synchronously cancel active/queued Core work at an identity barrier."""

        self._reservations.clear()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        worker, self._worker = self._worker, None
        if worker is not None and not worker.done():
            worker.cancel()
        self._active = None
        self._idle.set()

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def _ensure_worker(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(
            self._run(),
            name="independent-asr-core-transcript-dispatcher",
        )

    async def _run(self) -> None:
        try:
            while True:
                envelope = await self._queue.get()
                self._active = envelope
                try:
                    await self._dispatch(envelope)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Dispatch callbacks own status reporting. Keep the serial
                    # worker alive if a defensive caller still leaks an error.
                    pass
                finally:
                    self._active = None
                    self._queue.task_done()
                    self._set_idle_if_empty()
        except asyncio.CancelledError:
            return

    def _set_idle_if_empty(self) -> None:
        if self._queue.empty() and self._active is None:
            self._idle.set()
