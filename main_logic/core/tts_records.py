"""Provider-neutral ownership records for a main TTS worker and its queues."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


# Bound a single frame while preserving its header/payload ordering on cancel.
TTS_FRAME_WRITE_TIMEOUT_SECONDS = 5.0


class TtsCapacityError(RuntimeError):
    """The bounded main TTS worker pool cannot admit another runtime."""


@dataclass(eq=False)
class TtsRuntimeRecord:
    thread: Any
    request_queue: Any
    response_queue: Any
    handler: asyncio.Task | None = None
    retired: bool = False
    shutdown_sent: bool = False
    supports_runtime_overlap: bool = True
    cleanup_task: asyncio.Task | None = None
    cleanup_complete: asyncio.Event = field(default_factory=asyncio.Event)
    handoff_safe: asyncio.Event = field(default_factory=asyncio.Event)


# Task-local identity also follows notifications scheduled by a handler. A late
# task cannot acquire the replacement runtime simply by rereading the manager.
tts_output_runtime: ContextVar[TtsRuntimeRecord | None] = ContextVar(
    "tts_output_runtime", default=None
)
