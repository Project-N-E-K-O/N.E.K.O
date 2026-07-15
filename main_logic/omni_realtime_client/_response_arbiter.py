"""Serialize client-initiated realtime responses across their full lifecycle."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


SendEvent = Callable[[dict[str, Any]], Awaitable[None]]


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
    expected_item_role: str | None = field(compare=False)
    item_ack_timeout: float = field(compare=False)
    response_started_timeout: float = field(compare=False)
    response_done_timeout: float = field(compare=False)
    cancel_timeout: float = field(compare=False)
    ticket: ResponseTicket = field(compare=False)
    item_ack: asyncio.Future[None] | None = field(default=None, compare=False)
    terminal: asyncio.Future[None] | None = field(default=None, compare=False)
    event_ids: frozenset[str] = field(default_factory=frozenset, compare=False)


class RealtimeResponseArbiter:
    """A single-consumer priority queue for every explicit ``response.create``.

    The worker does not release the lane after sending. It holds ownership until
    ``response.done`` (or a terminal error/cancellation), closing the race where
    a second caller observes ``_is_responding == False`` before the first
    ``response.created`` arrives.
    """

    def __init__(self, send_event: SendEvent) -> None:
        self._send_event = send_event
        self._queue: asyncio.PriorityQueue[_QueuedResponse] = asyncio.PriorityQueue()
        self._sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._current: _QueuedResponse | None = None
        self._server_response_active = False
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def current_source(self) -> str | None:
        return self._current.source if self._current is not None else None

    @property
    def is_busy(self) -> bool:
        return self._current is not None or self._server_response_active

    async def enqueue(
        self,
        *,
        source: str,
        events_before_response: tuple[dict[str, Any], ...] = (),
        response_event: dict[str, Any] | None = None,
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
            expected_item_role=expected_item_role,
            item_ack_timeout=item_ack_timeout,
            response_started_timeout=response_started_timeout,
            response_done_timeout=response_done_timeout,
            cancel_timeout=cancel_timeout,
            ticket=ticket,
            event_ids=frozenset(ids),
        )
        await self._queue.put(queued)
        self._ensure_worker()
        return ticket

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
        if current is not None and current.terminal is not None and not current.terminal.done():
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
        self._server_response_active = False
        current = self._current
        if current is not None:
            exc = ConnectionError(reason)
            if current.item_ack is not None and not current.item_ack.done():
                current.item_ack.set_exception(exc)
                return
            if not current.ticket.started.done():
                current.ticket.started.set_exception(exc)
                return
            if current.terminal is not None and not current.terminal.done():
                current.terminal.set_exception(exc)
        else:
            self._idle.set()

    def reset_connection_state(self) -> None:
        if self._current is None:
            self._server_response_active = False
            self._idle.set()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(), name="realtime-response-arbiter"
            )

    async def _run(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                await self._process(queued)
            finally:
                self._queue.task_done()

    async def _process(self, queued: _QueuedResponse) -> None:
        await self._idle.wait()
        self._current = queued
        self._idle.clear()
        loop = asyncio.get_running_loop()
        queued.terminal = loop.create_future()
        item_acked = queued.expected_item_role is None

        try:
            if queued.expected_item_role is not None:
                queued.item_ack = loop.create_future()
            for event in queued.events_before_response:
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

            await self._send_event(queued.response_event)
            if not queued.ticket.sent.done():
                queued.ticket.sent.set_result(None)

            await asyncio.wait_for(
                asyncio.shield(queued.ticket.started),
                queued.response_started_timeout,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(queued.terminal), queued.response_done_timeout
                )
            except asyncio.TimeoutError:
                await self._send_event({"type": "response.cancel"})
                await asyncio.wait_for(
                    asyncio.shield(queued.terminal), queued.cancel_timeout
                )

            result = ResponseDispatchResult(
                item_acknowledged=item_acked,
                context_persistence_uncertain=not item_acked,
            )
            if not queued.ticket.done.done():
                queued.ticket.done.set_result(result)
        except Exception as exc:
            for future in (queued.ticket.sent, queued.ticket.started, queued.ticket.done):
                if not future.done():
                    future.set_exception(exc)
        finally:
            self._current = None
            if not self._server_response_active:
                self._idle.set()
