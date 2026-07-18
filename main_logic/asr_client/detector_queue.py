"""Duration-bounded ordered queue for detector audio and control work."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

from .detector_contracts import DetectorAudioItem


_ControlItem = TypeVar("_ControlItem")
_AudioItem = TypeVar("_AudioItem")


@dataclass(frozen=True, slots=True)
class _QueuedAudio(Generic[_AudioItem]):
    value: _AudioItem
    duration_us: int


class DetectorDurationQueue(Generic[_AudioItem, _ControlItem]):
    """Preserve enqueue order while reserving capacity for control work.

    Audio is bounded by duration and frame count. Control items share ordering
    with audio but do not consume either audio budget, so an overflow result or
    invalidation barrier can always be delivered.
    """

    def __init__(self, *, capacity_us: int = 1_000_000, max_frames: int = 128) -> None:
        if capacity_us <= 0 or max_frames <= 0:
            raise ValueError("detector queue limits must be positive")
        self.capacity_us = capacity_us
        self.max_frames = max_frames
        self._audio_duration_us = 0
        self._audio_frames = 0
        self._items: deque[_QueuedAudio[_AudioItem] | _ControlItem] = deque()
        self._available = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._unfinished_tasks = 0

    @property
    def audio_duration_us(self) -> int:
        return self._audio_duration_us

    @property
    def audio_frames(self) -> int:
        return self._audio_frames

    def qsize(self) -> int:
        return len(self._items)

    def empty(self) -> bool:
        return not self._items

    def can_accept_audio(self, duration_us: int) -> bool:
        return bool(
            self._audio_frames < self.max_frames
            and self._audio_duration_us + duration_us <= self.capacity_us
        )

    def put_audio_nowait(
        self,
        item: _AudioItem,
        *,
        duration_us: int | None = None,
    ) -> None:
        if duration_us is None:
            if not isinstance(item, DetectorAudioItem):
                raise TypeError("duration_us is required for custom audio items")
            duration_us = item.duration_us
        if duration_us <= 0:
            raise ValueError("audio duration must be positive")
        if not self.can_accept_audio(duration_us):
            raise asyncio.QueueFull
        self._items.append(_QueuedAudio(item, duration_us))
        self._audio_frames += 1
        self._audio_duration_us += duration_us
        self._unfinished_tasks += 1
        self._idle.clear()
        self._available.set()

    def put_control_nowait(self, item: _ControlItem, *, priority: bool = False) -> None:
        if isinstance(item, (DetectorAudioItem, _QueuedAudio)):
            raise TypeError("audio must use put_audio_nowait")
        if priority:
            self._items.appendleft(item)
        else:
            self._items.append(item)
        self._unfinished_tasks += 1
        self._idle.clear()
        self._available.set()

    async def get(self) -> _AudioItem | _ControlItem:
        while not self._items:
            self._available.clear()
            if self._items:
                break
            await self._available.wait()
        queued = self._items.popleft()
        if isinstance(queued, _QueuedAudio):
            self._audio_frames -= 1
            self._audio_duration_us -= queued.duration_us
            item: _AudioItem | _ControlItem = queued.value
        else:
            item = queued
        if not self._items:
            self._available.clear()
        return item

    def get_nowait(self) -> _AudioItem | _ControlItem:
        if not self._items:
            raise asyncio.QueueEmpty
        queued = self._items.popleft()
        if isinstance(queued, _QueuedAudio):
            self._audio_frames -= 1
            self._audio_duration_us -= queued.duration_us
            item: _AudioItem | _ControlItem = queued.value
        else:
            item = queued
        if not self._items:
            self._available.clear()
        return item

    def task_done(self) -> None:
        if self._unfinished_tasks <= 0:
            raise ValueError("task_done() called too many times")
        self._unfinished_tasks -= 1
        if self._unfinished_tasks == 0:
            self._idle.set()

    async def join(self) -> None:
        if self._unfinished_tasks:
            await self._idle.wait()

    def discard_audio(self) -> int:
        """Discard queued PCM while preserving control barriers and results."""

        kept: deque[_QueuedAudio[_AudioItem] | _ControlItem] = deque()
        discarded = 0
        for item in self._items:
            if isinstance(item, _QueuedAudio):
                discarded += 1
            else:
                kept.append(item)
        self._items = kept
        self._audio_frames = 0
        self._audio_duration_us = 0
        self._unfinished_tasks -= discarded
        if self._unfinished_tasks == 0:
            self._idle.set()
        if self._items:
            self._available.set()
        else:
            self._available.clear()
        return discarded
