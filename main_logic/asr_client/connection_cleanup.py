"""Owned, bounded retirement of physical ASR transports.

Protocol ``closed`` events aren't proof that a socket or its tasks retired.
The request queue carries this resource-only registry across worker cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

CLOSE_TIMEOUT_SECONDS = 2.0
TASK_EXIT_TIMEOUT_SECONDS = 1.0
logger = logging.getLogger(__name__)


class ConnectionRetirementError(RuntimeError):
    code = "ASR_CONNECTION_RETIRE_FAILED"


@dataclass(frozen=True)
class CleanupOutcome:
    released: bool
    graceful_close_error: str | None = None
    operations_exited: bool = True

    @property
    def retired(self) -> bool:
        return self.released and self.operations_exited


def _consume_task(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class ConnectionRetirement:
    def __init__(self, connection: Any, worker_identity: str):
        self.connection = connection
        self.worker_identity = worker_identity
        self.connection_identity = id(connection)
        self.cleanup_task: asyncio.Task[CleanupOutcome] | None = None
        self.cleanup_outcome: CleanupOutcome | None = None
        self.deadline: float | None = None
        self._operations: set[asyncio.Task] = set()

    def start(self) -> asyncio.Task[CleanupOutcome]:
        if self.cleanup_task is None:
            self.deadline = asyncio.get_running_loop().time() + CLOSE_TIMEOUT_SECONDS
            self.cleanup_task = asyncio.create_task(
                self._close(), name="asr-connection-retirement"
            )
        return self.cleanup_task

    async def retire(self) -> CleanupOutcome:
        outcome = await asyncio.shield(self.start())
        if not outcome.retired:
            raise ConnectionRetirementError("ASR physical connection retirement failed")
        return outcome

    async def _bounded(self, awaitable, deadline: float) -> tuple[bool, str | None]:
        task = asyncio.ensure_future(awaitable)
        self._operations.add(task)
        done, _ = await asyncio.wait(
            {task}, timeout=max(0.0, deadline - asyncio.get_running_loop().time())
        )
        if not done:
            task.cancel()
            task.add_done_callback(_consume_task)
            return False, "timeout"
        if task.cancelled():
            return False, "cancelled"
        error = task.exception()
        return error is None, type(error).__name__ if error else None

    async def _close(self) -> CleanupOutcome:
        assert self.deadline is not None
        # Reserve time to abort and observe connection_lost after graceful close.
        graceful_deadline = self.deadline - CLOSE_TIMEOUT_SECONDS / 4
        try:
            closed, error = await self._bounded(
                self.connection.close(), graceful_deadline
            )
        except Exception as exc:
            closed, error = False, type(exc).__name__
        wait_closed = getattr(self.connection, "wait_closed", None)
        released = False
        if closed and callable(wait_closed):
            try:
                released, proof_error = await self._bounded(
                    wait_closed(), graceful_deadline
                )
                error = error or proof_error
            except Exception as exc:
                error = type(exc).__name__
        elif closed:
            released = self._released_without_wait()
        if not released:
            transport = getattr(self.connection, "transport", None)
            abort = getattr(transport, "abort", None)
            if callable(abort):
                try:
                    abort()
                except Exception:
                    pass
        if not released and callable(wait_closed):
            try:
                released, _ = await self._bounded(wait_closed(), self.deadline)
            except Exception:
                released = False
        elif not released:
            released = self._released_without_wait()
        # A cancelled close coroutine that ignored cancellation remains unsafe,
        # even if the transport's own connection_lost signal already arrived.
        pending = {task for task in self._operations if not task.done()}
        if pending:
            _, pending = await asyncio.wait(
                pending,
                timeout=max(0.0, self.deadline - asyncio.get_running_loop().time()),
            )
        self.cleanup_outcome = CleanupOutcome(released, error, not pending)
        if error is not None or not self.cleanup_outcome.retired:
            logger.warning(
                "ASR retirement worker=%s connection=%s released=%s close_error=%s pending_tasks=%s",
                self.worker_identity,
                self.connection_identity,
                released,
                error,
                len(pending),
            )
        return self.cleanup_outcome

    def _released_without_wait(self) -> bool:
        if hasattr(self.connection, "closed"):
            return self.connection.closed is True
        return (
            getattr(getattr(self.connection, "state", None), "name", None) == "CLOSED"
        )


class ConnectionRetirementRegistry:
    def __init__(self):
        self.connections: list[ConnectionRetirement] = []
        self.tasks: set[asyncio.Task] = set()
        self.failure: ConnectionRetirementError | None = None
        self.sealed = False

    def register(
        self, connection: Any, *, worker_identity: str
    ) -> ConnectionRetirement:
        for owner in self.connections:
            if owner.connection is connection:
                return owner
        owner = ConnectionRetirement(connection, worker_identity)
        if self.sealed or self.failure is not None:
            self.connections.append(owner)
            owner.start()
            error = ConnectionRetirementError(
                "ASR connection arrived after retirement began"
            )
            # A worker may translate this exception into a protocol error and
            # return normally. Preserve failed ownership independently of that
            # callback; an earlier retirement snapshot cannot prove this socket.
            self.record_failure(error)
            raise error
        self.tasks = {task for task in self.tasks if not task.done()}
        if not self.tasks:
            self.connections = [
                item
                for item in self.connections
                if item.cleanup_outcome is None or not item.cleanup_outcome.retired
            ]
        self.connections.append(owner)
        return owner

    def register_tasks(self, *tasks: asyncio.Task | None) -> None:
        self.tasks.update(task for task in tasks if task is not None)

    def record_failure(self, error: BaseException) -> None:
        if self.failure is None:
            self.failure = (
                error
                if isinstance(error, ConnectionRetirementError)
                else ConnectionRetirementError(type(error).__name__)
            )

    def raise_if_failed(self) -> None:
        if self.failure is not None:
            raise self.failure

    def start_all(self) -> None:
        self.sealed = True
        for owner in self.connections:
            owner.start()

    async def retire_all(self) -> None:
        self.start_all()
        for owner in self.connections:
            try:
                await owner.retire()
            except ConnectionRetirementError as exc:
                self.record_failure(exc)
        self.raise_if_failed()

    async def join_tasks(self) -> None:
        pending = {task for task in self.tasks if not task.done()}
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=TASK_EXIT_TIMEOUT_SECONDS)
        if pending:
            self.record_failure(
                ConnectionRetirementError("ASR transport task retirement timed out")
            )
        for task in self.tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if isinstance(error, ConnectionRetirementError):
                    self.record_failure(error)
        self.raise_if_failed()


def connection_registry(request_queue: Any) -> ConnectionRetirementRegistry:
    registry = getattr(request_queue, "_connection_retirement_registry", None)
    if registry is None:
        registry = ConnectionRetirementRegistry()
        request_queue._connection_retirement_registry = registry
    return registry
