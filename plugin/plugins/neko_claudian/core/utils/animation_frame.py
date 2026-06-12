# Ported from claudian/src/utils/animationFrame.ts
# Original author: Claudian contributors
# License: MIT

"""
Animation frame utilities (adapted for Python/asyncio).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


class ScheduledAnimationFrame:
    """A scheduled animation frame (asyncio adaptation)."""

    def __init__(self, callback: Callable, delay: float = 0):
        self._callback = callback
        self._delay = delay
        self._task: Optional[asyncio.Task] = None
        self._cancelled = False

    async def _run(self):
        """Run the callback after delay."""
        await asyncio.sleep(self._delay)
        if not self._cancelled:
            self._callback()

    def schedule(self):
        """Schedule the frame."""
        self._task = asyncio.create_task(self._run())

    def cancel(self):
        """Cancel the frame."""
        self._cancelled = True
        if self._task:
            self._task.cancel()


def schedule_animation_frame(
    callback: Callable,
    window: Any = None,
    delay: float = 0,
) -> ScheduledAnimationFrame:
    """Schedule an animation frame."""
    frame = ScheduledAnimationFrame(callback, delay)
    frame.schedule()
    return frame


def cancel_scheduled_animation_frame(frame: ScheduledAnimationFrame) -> None:
    """Cancel a scheduled animation frame."""
    frame.cancel()
