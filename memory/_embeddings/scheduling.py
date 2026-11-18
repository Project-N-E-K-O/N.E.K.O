"""Admit all interactive requests while bounding queued background work."""

import asyncio
from contextlib import asynccontextmanager
from utils.embedding_contract import EmbedDeferred


class InferenceBusy(EmbedDeferred):
    """Reject excess work without treating admission pressure as model failure."""


class InferenceUnavailable(RuntimeError):
    """Release queued operations when their owning encoder is closing."""


class InferenceQueue:
    """Run one native call with unlimited foreground and bounded background waiters.

    A running native call cannot be preempted. Foreground overtakes waiting
    builds. After a bounded burst that actually delayed background work, one
    minimum background batch may run even while foreground is queued. ``slot``
    yields True for that fairness turn so the encoder can shrink its batch.
    The owner drains its native thread before leaving; cancelling a waiter
    removes only that waiter.
    """

    def __init__(self, *, max_background: int = 4,
                 foreground_burst: int = 8, background_duty: float = 1.0):
        self._condition = asyncio.Condition()
        self._active = False
        self._waiting = {"foreground": 0, "background": 0}
        self._max_background = max_background
        self._foreground_streak = 0
        self._foreground_burst = foreground_burst
        self._background_duty = background_duty
        self._background_ready_at = 0.0
        self._closed = False

    async def shutdown(self) -> None:
        """Wake waiters without releasing the slot of an active native worker."""
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _turn(self, priority: str) -> bool:
        if self._active:
            return False
        background_eligible = asyncio.get_running_loop().time() >= self._background_ready_at
        if priority == "background":
            return background_eligible and (not self._waiting["foreground"] or self._foreground_streak >= self._foreground_burst)
        # A cooling background lane must never block interactive work, even
        # after the fairness counter has reached its threshold.
        return not self._waiting["background"] or self._foreground_streak < self._foreground_burst or not background_eligible

    @asynccontextmanager
    async def slot(self, priority: str = "foreground"):
        """Hold actual execution ownership, keeping overload out of native workers."""
        if priority not in self._waiting:
            raise ValueError("invalid_inference_priority")
        async with self._condition:
            if self._closed:
                raise InferenceUnavailable()
            # Interactive requests always enter the queue; only deferred work
            # can receive a retryable busy result before owning a native slot.
            if priority == "background" and self._waiting[priority] >= self._max_background:
                raise InferenceBusy()
            if priority == "background" and not self._waiting["background"]:
                # A newly waiting build cannot inherit a burst from work that
                # ran while there was no build to starve.
                self._foreground_streak = 0
            self._waiting[priority] += 1
            try:
                while True:
                    if self._closed:
                        raise InferenceUnavailable()
                    if self._turn(priority):
                        break
                    delay = self._background_ready_at - asyncio.get_running_loop().time()
                    if priority == "background" and delay > 0:
                        # Condition.wait releases the lock, and no native slot
                        # is owned while the background duty budget cools.
                        try:
                            await asyncio.wait_for(self._condition.wait(), delay)
                        except TimeoutError:
                            pass
                    else:
                        await self._condition.wait()
                self._active = True
                started = asyncio.get_running_loop().time()
                minimum_batch = priority == "background" and self._waiting["foreground"] > 0
                self._foreground_streak = (
                    self._foreground_streak + 1
                    if priority == "foreground" and self._waiting["background"] else 0
                )
            finally:
                self._waiting[priority] -= 1
                self._condition.notify_all()
        try:
            yield minimum_batch
        finally:
            async with self._condition:
                self._active = False
                if priority == "background":
                    finished = asyncio.get_running_loop().time()
                    self._background_ready_at = finished + (finished - started) * (1 / self._background_duty - 1)
                self._condition.notify_all()
