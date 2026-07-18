"""Serialized Core-side delivery for identity-scoped detector events."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from main_logic.asr_client.detector_contracts import DetectorEvent


@dataclass(frozen=True, slots=True)
class CoreDetectorEventEnvelope:
    event: DetectorEvent
    detector_ref: object
    lifecycle_ref: object
    session_epoch: int


class AsrDetectorDispatcher:
    def __init__(
        self,
        handler: Callable[[CoreDetectorEventEnvelope], Awaitable[None]],
        *,
        max_pending: int = 32,
    ) -> None:
        if max_pending <= 0:
            raise ValueError("detector event capacity must be positive")
        self._handler = handler
        self._queue: asyncio.Queue[tuple[int, CoreDetectorEventEnvelope]] = (
            asyncio.Queue(maxsize=max_pending)
        )
        self._generation = 0
        self._worker: asyncio.Task[None] | None = None

    def submit_nowait(self, envelope: CoreDetectorEventEnvelope) -> bool:
        self._ensure_worker()
        try:
            self._queue.put_nowait((self._generation, envelope))
        except asyncio.QueueFull:
            return False
        return True

    def invalidate_all(self) -> None:
        self._generation += 1
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        self.invalidate_all()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(), name="core-asr-detector-dispatcher"
            )

    async def _run(self) -> None:
        while True:
            generation, envelope = await self._queue.get()
            try:
                if generation == self._generation:
                    await self._handler(envelope)
            finally:
                self._queue.task_done()
