"""Bound native inference and give interactive memory calls priority over builds."""

import asyncio
from contextlib import asynccontextmanager


class InferenceBusy(RuntimeError):
    """Reject excess work without treating admission pressure as model failure."""


class InferenceQueue:
    """Run one native call with separate bounded foreground/background waiters.

    A running native call cannot be preempted. At its next boundary foreground
    work overtakes queued builds, with a bounded foreground burst to prevent
    a build from starving forever. The owner must drain its native thread before
    leaving ``slot``; cancellation while waiting removes only that waiter.
    """

    def __init__(self, *, max_foreground: int = 32, max_background: int = 4,
                 foreground_burst: int = 8):
        self._condition = asyncio.Condition()
        self._active = False
        self._waiting = {"foreground": 0, "background": 0}
        self._limits = {"foreground": max_foreground, "background": max_background}
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
            if self._waiting[priority] >= self._limits[priority]:
                raise InferenceBusy("embedding_busy")
            self._waiting[priority] += 1
            try:
                await self._condition.wait_for(lambda: self._turn(priority))
                self._active = True
                self._foreground_streak = self._foreground_streak + 1 if priority == "foreground" else 0
            finally:
                self._waiting[priority] -= 1
                self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                self._active = False
                self._condition.notify_all()
