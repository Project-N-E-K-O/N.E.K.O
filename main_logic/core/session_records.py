"""Owned session records, task-local start identity, and phase fencing."""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from functools import wraps


_start_context = contextvars.ContextVar("session_start_operation", default=None)


@dataclass(eq=False)
class StartOperation:
    manager: object
    generation: int
    websocket: object
    request_id: object
    input_mode: str
    deadline: float
    task: asyncio.Task
    valid: bool = True
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    children: set = field(default_factory=set)


@dataclass(eq=False)
class ConnectionRecord:
    session: object
    close: object
    operation: StartOperation | None = None
    retired: bool = False
    closed: bool = False
    close_task: asyncio.Task | None = None
    callbacks: set = field(default_factory=set)
    connect_task: asyncio.Task | None = None


@dataclass(eq=False)
class Retirement:
    generation: int
    operation: StartOperation | None
    session: object
    websocket: object
    listener: object
    tts: object
    initiating_task: object
    was_active: bool
    handoff_safe: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_complete: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    memory_completion: object = None
    memory_callback: object = None
    resets_operation: bool = True
    preparation: object = None
    swap: object = None


def start_phase(method):
    """Fence phase entry/exit and retain children spawned by gather."""
    @wraps(method)
    async def run(self, *args, **kwargs):
        operation = self._current_start_request()
        self._check_start_operation()
        task = asyncio.current_task()
        if operation is not None and task is not operation.task:
            operation.children.add(task)
        try:
            result = await method(self, *args, **kwargs)
            self._check_start_operation()
            return result
        finally:
            if operation is not None:
                operation.children.discard(task)
    return run
