"""Serialize client-initiated realtime responses across their full lifecycle."""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


SendEvent = Callable[[dict[str, Any]], Awaitable[None]]
AbortTransport = Callable[[str], Awaitable[None]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResponseDispatchResult:
    item_acknowledged: bool
    context_persistence_uncertain: bool


@dataclass(slots=True)
class ResponseTicket:
    sent: asyncio.Future[None]
    started: asyncio.Future[None]
    done: asyncio.Future[ResponseDispatchResult]


@dataclass(order=True, slots=True)
class _QueuedResponse:
    priority: int
    sequence: int
    source: str = field(compare=False)
    events_before_response: tuple[dict[str, Any], ...] = field(compare=False)
    response_event: dict[str, Any] = field(compare=False)
    ack_expected: bool = field(compare=False)
    expected_item_id: str | None = field(compare=False)
    expected_item_role: str | None = field(compare=False)
    item_ack_timeout: float = field(compare=False)
    response_started_timeout: float = field(compare=False)
    response_done_timeout: float = field(compare=False)
    cancel_timeout: float = field(compare=False)
    ticket: ResponseTicket = field(compare=False)
    item_ack: asyncio.Future[None] | None = field(default=None, compare=False)
    terminal: asyncio.Future[None] | None = field(default=None, compare=False)
    event_ids: frozenset[str] = field(default_factory=frozenset, compare=False)
    completed: asyncio.Future[None] | None = field(default=None, compare=False)
    bypass_count: int = field(default=0, compare=False)
    interrupted: bool = field(default=False, compare=False)


class RealtimeResponseArbiter:
    """A single-consumer priority queue for every explicit ``response.create``.

    The worker does not release the lane after sending. It holds ownership until
    ``response.done`` (or a terminal error/cancellation), closing the race where
    a second caller observes ``_is_responding == False`` before the first
    ``response.created`` arrives.
    """

    def __init__(
        self,
        send_event: SendEvent,
        *,
        abort_transport: AbortTransport | None = None,
    ) -> None:
        self._send_event = send_event
        self._abort_transport = abort_transport
        self._queue: asyncio.PriorityQueue[_QueuedResponse] = asyncio.PriorityQueue()
        self._sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._current: _QueuedResponse | None = None
        self._server_response_active = False
        self._connection_available = True
        self._dispatch_allowed = asyncio.Event()
        self._dispatch_allowed.set()
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def current_source(self) -> str | None:
        return self._current.source if self._current is not None else None

    @property
    def is_busy(self) -> bool:
        return (
            self._current is not None
            or self._server_response_active
            or not self._queue.empty()
        )

    async def enqueue(
        self,
        *,
        source: str,
        events_before_response: tuple[dict[str, Any], ...] = (),
        response_event: dict[str, Any] | None = None,
        ack_expected: bool = False,
        expected_item_id: str | None = None,
        expected_item_role: str | None = None,
        priority: int = 10,
        item_ack_timeout: float = 1.5,
        response_started_timeout: float = 5.0,
        response_done_timeout: float = 60.0,
        cancel_timeout: float = 3.0,
    ) -> ResponseTicket:
        loop = asyncio.get_running_loop()
        ticket = ResponseTicket(
            sent=loop.create_future(),
            started=loop.create_future(),
            done=loop.create_future(),
        )
        if not self._connection_available:
            self._fail_ticket(ticket, ConnectionError("realtime connection is unavailable"))
            return ticket
        create_event = dict(response_event or {"type": "response.create"})
        create_event.setdefault("type", "response.create")
        ids = {
            str(event.get("event_id"))
            for event in (*events_before_response, create_event)
            if event.get("event_id")
        }
        queued = _QueuedResponse(
            priority=priority,
            sequence=next(self._sequence),
            source=source,
            events_before_response=events_before_response,
            response_event=create_event,
            ack_expected=ack_expected,
            expected_item_id=expected_item_id,
            expected_item_role=expected_item_role,
            item_ack_timeout=item_ack_timeout,
            response_started_timeout=response_started_timeout,
            response_done_timeout=response_done_timeout,
            cancel_timeout=cancel_timeout,
            ticket=ticket,
            event_ids=frozenset(ids),
            completed=loop.create_future(),
        )
        await self._queue.put(queued)
        self._ensure_worker()
        return ticket

    def pause_dispatch(self) -> None:
        """Prevent queued work from starting while a user interruption settles."""

        self._dispatch_allowed.clear()

    def resume_dispatch(self) -> None:
        if not self._connection_available:
            return
        self._dispatch_allowed.set()
        self._ensure_worker()

    async def cancel_current(self, timeout: float = 3.0) -> None:
        """Cancel only the active/pre-created request, never drain the queue."""

        current = self._current
        if current is None:
            if not self._server_response_active:
                return
            await self._send_event({"type": "response.cancel"})
            try:
                await self.wait_until_idle(timeout)
            except asyncio.TimeoutError as original_timeout:
                await self._fail_closed(
                    "response cancellation terminal event timed out"
                )
                raise original_timeout
            return

        current.interrupted = True
        if not current.ticket.sent.done():
            was_paused = not self._dispatch_allowed.is_set()
            self._wake_current_with_error(
                current,
                RuntimeError("response dispatch interrupted before response.create"),
            )
            if was_paused:
                self._dispatch_allowed.set()
        else:
            was_paused = False
            await self._send_event({"type": "response.cancel"})
        assert current.completed is not None
        try:
            await asyncio.wait_for(asyncio.shield(current.completed), timeout)
            if was_paused and self._connection_available:
                self._dispatch_allowed.clear()
        except asyncio.TimeoutError as original_timeout:
            await self._fail_closed("response cancellation terminal event timed out")
            raise original_timeout

    async def wait_until_idle(self, timeout: float | None = None) -> None:
        waiter = self._idle.wait()
        if timeout is None:
            await waiter
        else:
            await asyncio.wait_for(waiter, timeout)

    def notify_item_created(self, event: dict[str, Any]) -> None:
        current = self._current
        if current is None or current.item_ack is None or current.item_ack.done():
            return
        item = event.get("item")
        if not isinstance(item, dict):
            return
        if current.expected_item_id is None:
            return
        if item.get("id") != current.expected_item_id:
            return
        if current.expected_item_role and item.get("role") != current.expected_item_role:
            return
        current.item_ack.set_result(None)

    def notify_response_created(self, _event: dict[str, Any]) -> None:
        self._server_response_active = True
        self._idle.clear()
        current = self._current
        if current is not None and not current.ticket.started.done():
            current.ticket.started.set_result(None)

    def notify_response_terminal(self, _event: dict[str, Any] | None = None) -> None:
        self._server_response_active = False
        current = self._current
        if current is not None:
            if not current.ticket.started.done():
                current.ticket.started.set_exception(
                    RuntimeError("response terminated before response.created")
                )
            if current.terminal is not None and not current.terminal.done():
                current.terminal.set_result(None)
        elif current is None:
            self._idle.set()

    def notify_error(self, event_id: str | None, message: str) -> None:
        current = self._current
        if current is None:
            return
        if event_id and current.event_ids and event_id not in current.event_ids:
            return
        lowered = message.lower()
        if not event_id and not (
            "response_already_active" in lowered
            or ("response" in lowered and "active" in lowered)
        ):
            return
        exc = RuntimeError(message)
        if current.item_ack is not None and not current.item_ack.done():
            current.item_ack.set_exception(exc)
            return
        if not current.ticket.started.done():
            current.ticket.started.set_exception(exc)
            return
        if current.terminal is not None and not current.terminal.done():
            current.terminal.set_exception(exc)

    def notify_connection_lost(self, reason: str = "realtime connection lost") -> None:
        self._connection_available = False
        # Wake a worker parked behind the dispatch barrier so it can observe
        # the failed connection and complete its selected ticket.
        self._dispatch_allowed.set()
        self._server_response_active = False
        exc = ConnectionError(reason)
        current = self._current
        if current is not None:
            self._wake_current_with_error(current, exc)
        else:
            self._idle.set()
        self._fail_queued(exc)

    def reset_connection_state(self) -> None:
        self._connection_available = True
        self._dispatch_allowed.set()
        if self._current is None:
            self._server_response_active = False
            self._idle.set()
        self._ensure_worker()

    @staticmethod
    def _fail_ticket(ticket: ResponseTicket, exc: Exception) -> None:
        for future in (ticket.sent, ticket.started, ticket.done):
            if not future.done():
                future.set_exception(exc)

    def _wake_current_with_error(
        self, current: _QueuedResponse, exc: Exception
    ) -> None:
        if current.item_ack is not None and not current.item_ack.done():
            current.item_ack.set_exception(exc)
        if not current.ticket.started.done():
            current.ticket.started.set_exception(exc)
        if current.terminal is not None and not current.terminal.done():
            current.terminal.set_exception(exc)

    def _fail_queued(self, exc: Exception) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._fail_ticket(queued.ticket, exc)
            if queued.completed is not None and not queued.completed.done():
                queued.completed.set_result(None)
            self._queue.task_done()

    async def _fail_closed(self, reason: str) -> None:
        self.notify_connection_lost(reason)
        if self._abort_transport is None:
            return
        try:
            await self._abort_transport(reason)
        except Exception as exc:
            logger.debug(
                "response fail-close transport abort also failed: %s",
                type(exc).__name__,
            )

    async def _next_queued(self) -> _QueuedResponse:
        """Select fairly: a lower-priority item may be bypassed at most 3 times."""

        candidates = [await self._queue.get()]
        while True:
            try:
                candidates.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        starved = [candidate for candidate in candidates if candidate.bypass_count >= 3]
        if starved:
            selected = min(starved, key=lambda item: item.sequence)
        else:
            selected = min(candidates, key=lambda item: (item.priority, item.sequence))
        for candidate in candidates:
            if candidate is selected:
                continue
            candidate.bypass_count += 1
            self._queue.task_done()
            self._queue.put_nowait(candidate)
        return selected

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(), name="realtime-response-arbiter"
            )

    async def _run(self) -> None:
        while True:
            await self._dispatch_allowed.wait()
            queued = await self._next_queued()
            try:
                await self._process(queued)
            finally:
                self._queue.task_done()

    async def _process(self, queued: _QueuedResponse) -> None:
        self._current = queued
        loop = asyncio.get_running_loop()
        item_acked = not queued.ack_expected
        requeued = False

        try:
            await self._dispatch_allowed.wait()
            if not self._connection_available:
                raise ConnectionError("realtime connection is unavailable")
            if self._yield_to_higher_priority(queued):
                requeued = True
                return
            await self._idle.wait()
            self._idle.clear()
            queued.terminal = loop.create_future()
            if queued.interrupted:
                raise RuntimeError("response dispatch interrupted")
            if queued.ack_expected:
                queued.item_ack = loop.create_future()
            for event in queued.events_before_response:
                if queued.interrupted:
                    raise RuntimeError("response dispatch interrupted")
                await self._send_event(event)

            if queued.item_ack is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(queued.item_ack), queued.item_ack_timeout
                    )
                    item_acked = True
                except asyncio.TimeoutError:
                    item_acked = False
                    queued.item_ack.cancel()

            if queued.interrupted:
                raise RuntimeError("response dispatch interrupted")
            await self._send_event(queued.response_event)
            if not queued.ticket.sent.done():
                queued.ticket.sent.set_result(None)

            try:
                await asyncio.wait_for(
                    asyncio.shield(queued.ticket.started),
                    queued.response_started_timeout,
                )
            except asyncio.TimeoutError as started_timeout:
                await self._cancel_after_timeout(queued, started_timeout)
            try:
                await asyncio.wait_for(
                    asyncio.shield(queued.terminal), queued.response_done_timeout
                )
            except asyncio.TimeoutError as done_timeout:
                await self._cancel_after_timeout(queued, done_timeout)

            if queued.interrupted:
                raise RuntimeError("response dispatch interrupted")

            result = ResponseDispatchResult(
                item_acknowledged=item_acked,
                context_persistence_uncertain=not item_acked,
            )
            if not queued.ticket.done.done():
                queued.ticket.done.set_result(result)
        except Exception as exc:
            self._fail_ticket(queued.ticket, exc)
        finally:
            self._current = None
            if (
                not requeued
                and queued.completed is not None
                and not queued.completed.done()
            ):
                queued.completed.set_result(None)
            if not self._server_response_active:
                self._idle.set()

    def _yield_to_higher_priority(self, queued: _QueuedResponse) -> bool:
        """Put a pre-created request back if a user turn arrived while paused."""

        try:
            candidate = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return False
        self._queue.task_done()
        self._queue.put_nowait(candidate)
        if candidate.priority >= queued.priority:
            return False
        self._queue.put_nowait(queued)
        return True

    async def _cancel_after_timeout(
        self, queued: _QueuedResponse, original_timeout: asyncio.TimeoutError
    ) -> None:
        try:
            await self._send_event({"type": "response.cancel"})
            assert queued.terminal is not None
            await asyncio.wait_for(
                asyncio.shield(queued.terminal), queued.cancel_timeout
            )
        except Exception:
            await self._fail_closed(
                "response lifecycle could not reach a terminal state"
            )
        raise original_timeout
