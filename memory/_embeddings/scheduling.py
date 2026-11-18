"""Admit all interactive requests while bounding queued background work."""

import asyncio
from contextlib import asynccontextmanager


class InferenceBusy(RuntimeError):
    """Reject excess work without treating admission pressure as model failure."""


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
                 foreground_burst: int = 8):
        self._condition = asyncio.Condition()
        self._active = False
        self._waiting = {"foreground": 0, "background": 0}
        self._max_background = max_background
        self._foreground_streak = 0
        self._foreground_burst = foreground_burst

    def _turn(self, priority: str) -> bool:
        if self._active:
            return False
        if priority == "background":
            return not self._waiting["foreground"] or self._foreground_streak >= self._foreground_burst
        return not self._waiting["background"] or self._foreground_streak < self._foreground_burst

    @asynccontextmanager
    async def slot(self, priority: str = "foreground"):
        """Hold actual execution ownership, keeping overload out of native workers."""
        if priority not in self._waiting:
            raise ValueError("invalid_inference_priority")
        async with self._condition:
            # Interactive requests always enter the queue; only deferred work
            # can receive a retryable busy result before owning a native slot.
            if priority == "background" and self._waiting[priority] >= self._max_background:
                raise InferenceBusy("embedding_busy")
            if priority == "background" and not self._waiting["background"]:
                # A newly waiting build cannot inherit a burst from work that
                # ran while there was no build to starve.
                self._foreground_streak = 0
            self._waiting[priority] += 1
            try:
                await self._condition.wait_for(lambda: self._turn(priority))
                self._active = True
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
                self._condition.notify_all()
