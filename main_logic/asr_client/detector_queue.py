"""Duration-bounded ordered queue for detector audio and control work."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Generic, TypeVar

from .detector_contracts import DetectorAudioItem


_ControlItem = TypeVar("_ControlItem")


class DetectorDurationQueue(Generic[_ControlItem]):
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
        self._items: deque[DetectorAudioItem | _ControlItem] = deque()
        self._available = asyncio.Event()

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

    def can_accept_audio(self, item: DetectorAudioItem) -> bool:
        return bool(
            self._audio_frames < self.max_frames
            and self._audio_duration_us + item.duration_us <= self.capacity_us
        )

    def put_audio_nowait(self, item: DetectorAudioItem) -> None:
        if not self.can_accept_audio(item):
            raise asyncio.QueueFull
        self._items.append(item)
        self._audio_frames += 1
        self._audio_duration_us += item.duration_us
        self._available.set()

    def put_control_nowait(self, item: _ControlItem, *, priority: bool = False) -> None:
        if isinstance(item, DetectorAudioItem):
            raise TypeError("audio must use put_audio_nowait")
        if priority:
            self._items.appendleft(item)
        else:
            self._items.append(item)
        self._available.set()

    async def get(self) -> DetectorAudioItem | _ControlItem:
        while not self._items:
            self._available.clear()
            if self._items:
                break
            await self._available.wait()
        item = self._items.popleft()
        if isinstance(item, DetectorAudioItem):
            self._audio_frames -= 1
            self._audio_duration_us -= item.duration_us
        if not self._items:
            self._available.clear()
        return item

    def get_nowait(self) -> DetectorAudioItem | _ControlItem:
        if not self._items:
            raise asyncio.QueueEmpty
        item = self._items.popleft()
        if isinstance(item, DetectorAudioItem):
            self._audio_frames -= 1
            self._audio_duration_us -= item.duration_us
        if not self._items:
            self._available.clear()
        return item

    def discard_audio(self) -> int:
        """Discard queued PCM while preserving control barriers and results."""

        kept: deque[DetectorAudioItem | _ControlItem] = deque()
        discarded = 0
        for item in self._items:
            if isinstance(item, DetectorAudioItem):
                discarded += 1
            else:
                kept.append(item)
        self._items = kept
        self._audio_frames = 0
        self._audio_duration_us = 0
        if self._items:
            self._available.set()
        else:
            self._available.clear()
        return discarded
