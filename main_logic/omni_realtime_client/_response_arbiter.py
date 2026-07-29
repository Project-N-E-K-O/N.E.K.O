"""Serialize client-initiated realtime responses across their full lifecycle.

CPython 3.11 only, and this module is why. The queue worker dequeues
BEFORE re-checking ``_dispatch_allowed``, so a ``pause_dispatch()`` landing
between those two points is only honored because of 3.11's task-yield
ordering. On 3.12+ that ordering changes and proactive chat can jump the
queue ahead of user speech. ``pyproject.toml`` pins ``requires-python`` to
``==3.11.*`` for this reason and
``tests/unit/test_realtime_arbiter_native_path.py`` fails if the pin is
widened. The real fix is #2516: gate the dequeue on ``_dispatch_allowed``
AFTER ``_next_queued`` and requeue. Do that before touching the pin.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


SendEvent = Callable[[dict[str, Any]], Awaitable[None]]
AbortTransport = Callable[[str], Awaitable[None]]
logger = logging.getLogger(__name__)

# Server-initiated response ids are remembered so their terminal events are
# never credited to an owner whose own ``response.created`` carried no id,
# and so the lane stays closed while such a response is still live even after
# the owner's own terminal has arrived. Only the most recent ids are kept: an
# id matters solely while its response is still live, so a small bound
# prevents unbounded growth on long sessions.
_SERVER_RESPONSE_ID_LIMIT = 32

# Default running-time allowance for a single response, shared by owned
# responses (the ``enqueue`` ``response_done_timeout`` default) and
# server-initiated ones (the staleness bound below). A tracked server response
# whose terminal event never arrives must not hold the lane forever on an
# otherwise healthy connection, so past its allowance its id stops holding the
# lane and a timer re-runs the release check — but the transport forwards no
# per-response activity signal that could distinguish a lost terminal from a
# slow live response, so the bound must grant a server response at least the
# same allowance an owned response gets (it ratchets up to the largest
# per-ticket ``response_done_timeout`` this arbiter has been asked to honor).
# Evicting sooner would reopen the lane under a still-running response and the
# next queued response.create would collide with it (response_already_active).
# The residual trade-off is deliberate: a live server response outliving the
# allowance can still be presumed dead, but the alternative — holding the lane
# until a terminal that may never come — reintroduces the permanent wedge this
# bound exists to prevent, and a waiter behind a genuinely long lane hits the
# idle-wait fail-close, which tears the connection down cleanly instead of
# racing a known-live response.
_DEFAULT_RESPONSE_DONE_TIMEOUT = 60.0
# An automatic server-VAD response normally announces response.created almost
# immediately after speech_stopped. Bound the id-less correlation gap so a
# provider-side pre-creation failure cannot wedge the lane until the much
# longer running-response timeout tears down an otherwise healthy connection.
_SERVER_VAD_RESPONSE_STARTED_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class ResponseDispatchResult:
    item_acknowledged: bool
    context_persistence_uncertain: bool


@dataclass(slots=True)
class ResponseTicket:
    sent: asyncio.Future[None]
    started: asyncio.Future[None]
    done: asyncio.Future[ResponseDispatchResult]


def _retrieve_exception(future: asyncio.Future[Any]) -> None:
    """Mark a failed future's exception as observed.

    Callers commonly await only ``ticket.sent``; without this, a failed
    ``started``/``done`` future would log "exception was never retrieved"
    when garbage collected.
    """

    if not future.cancelled():
        future.exception()


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
    terminal_error: BaseException | None = field(default=None, compare=False)
    response_id: str | None = field(default=None, compare=False)
    event_ids: frozenset[str] = field(default_factory=frozenset, compare=False)
    completed: asyncio.Future[None] | None = field(default=None, compare=False)
    bypass_count: int = field(default=0, compare=False)
    response_send_started: bool = field(default=False, compare=False)
    server_vad_won_during_response_send: bool = field(
        default=False,
        compare=False,
    )
    interrupted: bool = field(default=False, compare=False)
    interrupt_event: asyncio.Event = field(
        default_factory=asyncio.Event,
        compare=False,
    )


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
        self._queued_by_ticket: dict[int, _QueuedResponse] = {}
        self._sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None
        self._current: _QueuedResponse | None = None
        self._response_owner: _QueuedResponse | None = None
        self._server_response_active = False
        # An ended server-VAD utterance identifies an automatic response that
        # may race an explicit owner's response.create.  speech_started alone
        # is not sufficient: an already-sent explicit create can still emit
        # the next response.created while the user is only beginning to speak.
        self._server_vad_response_pending = False
        self._server_vad_pending_handle: asyncio.TimerHandle | None = None
        # Bounded insertion-ordered map of live server-initiated response ids
        # to their creation loop time; see _SERVER_RESPONSE_ID_LIMIT and
        # _server_response_max_age. Created adds, terminal removes.
        self._server_response_ids: dict[str, float] = {}
        # Staleness bound for the map above. Mirrors the response_done_timeout
        # semantics applied to owned responses: starts at the enqueue default
        # and only ever ratchets up, so a server response is never presumed
        # dead on a shorter clock than any owned response is allowed to run.
        self._server_response_max_age = _DEFAULT_RESPONSE_DONE_TIMEOUT
        self._stale_release_handle: asyncio.TimerHandle | None = None
        # Best-effort ``response.cancel`` sends spawned from the synchronous
        # notify_error path; referenced here so they are not garbage-collected
        # mid-flight, and cancelled on connection loss so a task suspended
        # inside ``send_event`` (e.g. on the transport send semaphore) cannot
        # resume after a reconnect and cancel an unrelated response on the
        # replacement connection.
        self._cancel_send_tasks: set[asyncio.Task[None]] = set()
        # Monotonic counter bumped on every connection loss. A cancel-send
        # task captures it at creation and re-checks it before sending, so a
        # task that somehow outlives its cancellation never fires into a
        # newer connection.
        self._connection_generation = 0
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
            or self._response_owner is not None
            or self._server_response_active
            or self._server_vad_response_pending
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
        response_done_timeout: float = _DEFAULT_RESPONSE_DONE_TIMEOUT,
        cancel_timeout: float = 3.0,
    ) -> ResponseTicket:
        loop = asyncio.get_running_loop()
        ticket = ResponseTicket(
            sent=loop.create_future(),
            started=loop.create_future(),
            done=loop.create_future(),
        )
        for future in (ticket.sent, ticket.started, ticket.done):
            future.add_done_callback(_retrieve_exception)
        if not self._connection_available:
            self._fail_ticket(ticket, ConnectionError("realtime connection is unavailable"))
            return ticket
        create_event = dict(response_event or {"type": "response.create"})
        create_event.setdefault("type", "response.create")
        # A server response must never be presumed dead sooner than the
        # longest-running owned response is allowed to live.
        if response_done_timeout > self._server_response_max_age:
            self._server_response_max_age = response_done_timeout
        # Stamp a client event id on any event the caller left unstamped
        # BEFORE freezing the correlation set below. The transport applies
        # its fallback id only inside send_event, so an unstamped event
        # would be missing from ``event_ids`` and a provider error echoing
        # the generated id could never be matched back to this ticket in
        # notify_error — the ticket would then hang until its started/
        # terminal timeout fail-closed an otherwise usable connection.
        # Stamping the caller's dicts here keeps a single source of truth
        # (send_event's setdefault becomes a no-op for these events), and
        # uuid-based ids avoid the same-millisecond collisions the
        # transport's timestamp fallback allows, which would defeat
        # _is_late_pre_response_error's create-vs-item discrimination.
        for event in (*events_before_response, create_event):
            event.setdefault("event_id", f"event_arbiter_{uuid.uuid4().hex}")
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
        self._queued_by_ticket[id(ticket)] = queued
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
        current.interrupt_event.set()
        if not current.ticket.sent.done():
            self._wake_current_with_error(
                current,
                RuntimeError("response dispatch interrupted before response.create"),
            )
        else:
            await self._send_event({"type": "response.cancel"})
        assert current.completed is not None
        try:
            await asyncio.wait_for(asyncio.shield(current.completed), timeout)
        except asyncio.TimeoutError as original_timeout:
            await self._fail_closed("response cancellation terminal event timed out")
            raise original_timeout

    async def cancel_ticket(
        self,
        ticket: ResponseTicket,
        timeout: float = 3.0,
        *,
        wait: bool = True,
    ) -> bool:
        """Cancel one exact request; return whether cancellation was requested."""

        queued = self._queued_by_ticket.get(id(ticket))
        if queued is None:
            return False
        # The receive loop resolves ``terminal`` synchronously, while the
        # worker removes the ticket on its next turn.  Cancellation in that
        # narrow window must be a no-op: an unscoped response.cancel could
        # otherwise hit a newer server-initiated response, and marking the
        # completed ticket interrupted would turn its successful result into
        # a cancellation error.
        if queued.terminal is not None and queued.terminal.done():
            return False
        queued.interrupted = True
        queued.interrupt_event.set()
        # A ticket still waiting in the priority queue will observe the
        # interrupt before dispatch. Do not cancel the unrelated current owner.
        if queued is not self._current:
            return True
        if (
            ticket.sent.done()
            and not ticket.sent.cancelled()
            and ticket.sent.exception() is None
        ):
            await self._send_event({"type": "response.cancel"})
        if not wait:
            return True
        assert queued.completed is not None
        try:
            await asyncio.wait_for(asyncio.shield(queued.completed), timeout)
        except asyncio.TimeoutError as original_timeout:
            await self._fail_closed("targeted response cancellation timed out")
            raise original_timeout
        return True

    async def wait_until_idle(self, timeout: float | None = None) -> None:
        waiter = self._idle.wait()
        if timeout is None:
            await waiter
        else:
            await asyncio.wait_for(waiter, timeout)

    async def shutdown(self, reason: str = "response arbiter shut down") -> None:
        """Fail pending work and stop the queue consumer."""

        stale = list(self._cancel_send_tasks)
        self._mark_connection_lost(reason, fail_current_tickets=True)
        worker, self._worker = self._worker, None
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            stale.append(worker)
        if stale:
            await asyncio.gather(*stale, return_exceptions=True)

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

    @staticmethod
    def _event_response_id(event: dict[str, Any] | None) -> str | None:
        response = (event or {}).get("response")
        response_id = response.get("id") if isinstance(response, dict) else None
        return str(response_id) if response_id else None

    def _remember_server_response_id(self, response_id: str) -> None:
        self._server_response_ids.pop(response_id, None)
        self._server_response_ids[response_id] = asyncio.get_running_loop().time()
        while len(self._server_response_ids) > _SERVER_RESPONSE_ID_LIMIT:
            del self._server_response_ids[next(iter(self._server_response_ids))]
        self._arm_stale_release_timer()

    def _cancel_stale_release_timer(self) -> None:
        handle, self._stale_release_handle = self._stale_release_handle, None
        if handle is not None:
            handle.cancel()

    def _cancel_server_vad_pending_timer(self) -> None:
        handle, self._server_vad_pending_handle = (
            self._server_vad_pending_handle,
            None,
        )
        if handle is not None:
            handle.cancel()

    def _arm_server_vad_pending_timer(self) -> None:
        self._cancel_server_vad_pending_timer()
        self._server_vad_pending_handle = asyncio.get_running_loop().call_later(
            _SERVER_VAD_RESPONSE_STARTED_TIMEOUT,
            self._server_vad_pending_expired,
        )

    def _server_vad_pending_expired(self) -> None:
        self._server_vad_pending_handle = None
        if not self._server_vad_response_pending:
            return
        self._server_vad_response_pending = False
        logger.warning(
            "released pending server-VAD response with no response.created "
            "after %.1fs",
            _SERVER_VAD_RESPONSE_STARTED_TIMEOUT,
        )
        if self._response_owner is None:
            self._release_lane_if_clear()

    def _arm_stale_release_timer(self) -> None:
        """(Re)start the timer that re-checks the lane at the next staleness deadline."""

        loop = asyncio.get_running_loop()
        deadline = (
            min(self._server_response_ids.values()) + self._server_response_max_age
        )
        self._cancel_stale_release_timer()
        self._stale_release_handle = loop.call_later(
            max(deadline - loop.time(), 0.0), self._stale_release_expired
        )

    def _stale_release_expired(self) -> None:
        self._stale_release_handle = None
        if self._response_owner is not None:
            # The lane is held by an owner anyway; its own terminal path
            # re-runs the release check (and re-arms the timer if needed).
            return
        self._release_lane_if_clear()

    def _release_lane_if_clear(self) -> None:
        """Open the lane unless a live server-initiated response still holds it.

        Ids older than ``_server_response_max_age`` are dropped first: a server
        response whose terminal event was lost must not wedge the lane on a
        healthy connection, so past that bound its id stops holding the lane.
        """

        if self._server_vad_response_pending:
            # speech_stopped announces an automatic response before its
            # response.created event supplies an id. Keep dispatch closed in
            # that correlation gap; otherwise an explicit response.create can
            # steal the next created event and leave its own ticket unresolved.
            self._server_response_active = True
            self._idle.clear()
            return
        now = asyncio.get_running_loop().time()
        stale = [
            response_id
            for response_id, created_at in self._server_response_ids.items()
            if now - created_at >= self._server_response_max_age
        ]
        for response_id in stale:
            del self._server_response_ids[response_id]
        if stale:
            # A response outliving the owned-response allowance without a
            # terminal event is a protocol anomaly: either its terminal was
            # lost, or a live response ran far longer than anything this
            # arbiter is configured to wait for.
            logger.warning(
                "released %d server response id(s) with no terminal event "
                "after %.0fs: %s",
                len(stale),
                self._server_response_max_age,
                ", ".join(stale),
            )
        if self._server_response_ids:
            # A known server-initiated response is still live. Opening the
            # lane now would let the next queued response.create collide with
            # it, so keep the lane closed until its terminal arrives (or its
            # id goes stale).
            self._arm_stale_release_timer()
            return
        self._cancel_stale_release_timer()
        self._server_response_active = False
        self._idle.set()

    def notify_response_created(self, event: dict[str, Any]) -> None:
        self._server_response_active = True
        self._idle.clear()
        if self._server_vad_response_pending:
            self._cancel_server_vad_pending_timer()
            self._server_vad_response_pending = False
            response_id = self._event_response_id(event)
            if response_id is not None:
                self._remember_server_response_id(response_id)
            return
        owner = self._response_owner
        if owner is not None and not owner.ticket.started.done():
            # The first response.created after the owner's response.create is
            # credited to the owner. Remember its response id (when the
            # provider supplies one) so only that response's terminal event
            # can release the lane.
            owner.response_id = self._event_response_id(event)
            owner.ticket.started.set_result(None)
            return
        # This created event cannot be credited to a waiting owner (the owner
        # already started, or no owner is pending), so it announces a
        # server-initiated response. Remember its id so its terminal event is
        # recognized as an orphan even when the owner's own response.created
        # carried no id.
        response_id = self._event_response_id(event)
        if response_id is not None:
            self._remember_server_response_id(response_id)

    def notify_server_vad_started(self) -> None:
        """Deliberately leave ownership unchanged until speech_stopped.

        The transport keeps this explicit boundary so future callers do not
        accidentally treat speech_started as evidence of an automatic
        response; only the ended utterance below may arm that correlation.
        """

    def notify_server_vad_response_pending(self) -> None:
        """Mark a VAD response pending unless an explicit create won the race."""

        owner = self._response_owner
        if owner is not None and owner.ticket.sent.done():
            # The explicit response.create finished sending before this
            # utterance ended. Its response.created echo can still be next;
            # do not let the later VAD boundary steal that owner.
            return
        if (
            owner is not None
            and owner.response_send_started
            and not owner.ticket.sent.done()
        ):
            # The automatic user turn won while response.create was inside an
            # awaited transport write. The create may still reach the server,
            # so quarantine it as a server response after the write returns;
            # meanwhile the pending marker owns the next response.created.
            owner.server_vad_won_during_response_send = True
            owner.interrupted = True
            owner.interrupt_event.set()
        current = self._current
        if owner is None and current is not None:
            # The automatic user response won while an explicit request was
            # still persisting its pre-response item or waiting for its ack.
            # Stop that request before it can emit response.create; otherwise
            # its first response.created echo is indistinguishable from the
            # pending VAD response and can be credited to the wrong turn.
            current.interrupted = True
            current.interrupt_event.set()
            if current.item_ack is not None and not current.item_ack.done():
                current.item_ack.set_exception(
                    RuntimeError(
                        "response dispatch interrupted by pending server VAD response"
                    )
                )
        self._server_vad_response_pending = True
        self._server_response_active = True
        self._idle.clear()
        self._arm_server_vad_pending_timer()

    def notify_response_terminal(self, event: dict[str, Any] | None = None) -> None:
        owner = self._response_owner
        response_id = self._event_response_id(event)
        response = (event or {}).get("response")
        response_status = (
            str(response.get("status") or "").strip().lower()
            if isinstance(response, dict)
            else ""
        )
        terminal_error = (
            RuntimeError(f"response.done status={response_status}")
            if response_status
            and response_status not in {"completed", "success", "succeeded"}
            else None
        )
        if owner is not None and response_id is not None:
            if not owner.ticket.started.done():
                # The owner has not seen its response.created yet, and a
                # response's terminal event never precedes its created event,
                # so an id-bearing terminal here belongs to another
                # (server-initiated) response. Keep waiting for the owner's
                # own lifecycle instead of failing it.
                self._server_response_ids.pop(response_id, None)
                if not self._server_response_ids:
                    # The separately tracked server turn has ended. The
                    # pending owner still holds dispatch serialization, but a
                    # later response.create rejection must be able to detach
                    # it instead of mistaking this already-terminal turn for
                    # a live id-less response.
                    self._cancel_stale_release_timer()
                    self._server_response_active = False
                return
            if owner.response_id is not None:
                if response_id != owner.response_id:
                    # A server-initiated response finished while the owner's
                    # own response is still running. Releasing the lane here
                    # would let a queued response.create collide with it, so
                    # treat the mismatched terminal as an orphan.
                    self._server_response_ids.pop(response_id, None)
                    return
            elif response_id in self._server_response_ids:
                # The owner's response.created carried no id, but this
                # terminal matches a known server-initiated response, so it
                # cannot be the owner's own terminal.
                del self._server_response_ids[response_id]
                return
        elif response_id is not None:
            # No owner: a server-initiated response reached its terminal
            # state, so its id no longer holds the lane.
            self._server_response_ids.pop(response_id, None)
        if owner is not None:
            if not owner.ticket.started.done():
                owner.ticket.started.set_exception(
                    RuntimeError("response terminated before response.created")
                )
            if owner.terminal is not None and not owner.terminal.done():
                # A terminal event always resolves the lifecycle waiter,
                # including an acknowledged response.cancel. Keep the
                # response outcome separate so cancellation/timeout cleanup
                # does not mistake a non-success terminal for a missing
                # terminal and fail-close a healthy connection.
                owner.terminal_error = terminal_error
                owner.terminal.set_result(None)
            self._response_owner = None
        # The owner (if any) has terminated; the lane opens only once no
        # server-initiated response is still live, so a queued
        # response.create cannot overlap with one whose terminal is pending.
        self._release_lane_if_clear()

    def notify_error(self, event_id: str | None, message: str) -> None:
        current = self._current
        owner = self._response_owner
        lowered = message.lower()
        target: _QueuedResponse | None = None
        if event_id:
            for candidate in (owner, current):
                if candidate is not None and event_id in candidate.event_ids:
                    target = candidate
                    break
        elif "response_already_active" in lowered or (
            "response" in lowered and "active" in lowered
        ):
            target = owner
        if target is None:
            return
        exc = RuntimeError(message)
        if target.item_ack is not None and not target.item_ack.done():
            target.item_ack.set_exception(exc)
            return
        if not target.ticket.started.done():
            if self._is_late_pre_response_error(target, event_id):
                self._fail_owner_with_live_response(target, exc)
                return
            target.ticket.started.set_exception(exc)
            return
        if target.terminal is not None and not target.terminal.done():
            target.terminal.set_exception(exc)

    @staticmethod
    def _is_late_pre_response_error(
        target: _QueuedResponse, event_id: str | None
    ) -> bool:
        """True when an error rejects a pre-response event after the create.

        ``response.create`` is already on the wire, and the error echoes the
        event_id of one of the events sent before it (e.g. the expected
        conversation item), not the create's own event_id. Such an error is
        not proof the response was refused: the server may still accept the
        create, so the lane must not reopen on it.
        """

        if (
            not target.response_send_started
            and not target.ticket.sent.done()
        ) or not event_id:
            return False
        create_event_id = target.response_event.get("event_id")
        return event_id != (str(create_event_id) if create_event_id else None)

    def _fail_owner_with_live_response(
        self, target: _QueuedResponse, exc: Exception
    ) -> None:
        """Fail the ticket without opening the lane under a live response.

        Failing ``started`` here would unwind ``_process`` and release
        ownership even though the just-sent ``response.create`` may still be
        accepted, letting the next queued create race it. Mirror
        ``cancel_current``'s post-send path instead: surface the error on the
        ticket, best-effort cancel the possibly-live response, and keep the
        lane owned until its terminal event arrives (or the owner's
        started/terminal timeout backstop fail-closes the transport).
        """

        if not target.ticket.done.done():
            target.ticket.done.set_exception(exc)
        if target.interrupted:
            return
        target.interrupted = True
        target.interrupt_event.set()
        task = asyncio.create_task(
            self._send_cancel_best_effort(self._connection_generation)
        )
        self._cancel_send_tasks.add(task)
        task.add_done_callback(self._cancel_send_tasks.discard)

    async def _send_cancel_best_effort(self, generation: int) -> None:
        if generation != self._connection_generation:
            # The connection this cancel was aimed at is gone; sending now
            # would cancel an unrelated response on its replacement.
            return
        try:
            await self._send_event({"type": "response.cancel"})
        except Exception as exc:
            # Delivery is best-effort: if the cancel cannot reach the server,
            # the owner's started/terminal timeout still fail-closes the lane.
            logger.debug(
                "late-error response.cancel send failed: %s", type(exc).__name__
            )

    def notify_connection_lost(self, reason: str = "realtime connection lost") -> None:
        self._mark_connection_lost(reason, fail_current_tickets=True)

    def _cancel_pending_cancel_sends(self) -> None:
        """Stop best-effort cancel sends aimed at a now-dead connection.

        A cancel-send task can be suspended inside ``send_event`` (e.g. on
        the transport send semaphore) across a close/reconnect; left alone it
        would resume and send ``response.cancel`` into the new session.
        """

        tasks = list(self._cancel_send_tasks)
        self._cancel_send_tasks.clear()
        for task in tasks:
            task.cancel()

    def _mark_connection_lost(
        self,
        reason: str,
        *,
        fail_current_tickets: bool,
    ) -> None:
        self._connection_available = False
        self._connection_generation += 1
        self._cancel_pending_cancel_sends()
        # Wake a worker parked behind the dispatch barrier so it can observe
        # the failed connection and complete its selected ticket.
        self._dispatch_allowed.set()
        self._server_response_active = False
        self._server_vad_response_pending = False
        self._server_response_ids.clear()
        self._cancel_server_vad_pending_timer()
        self._cancel_stale_release_timer()
        exc = ConnectionError(reason)
        owner = self._response_owner
        current = self._current
        seen: set[int] = set()
        for target in (owner, current):
            if target is None or id(target) in seen:
                continue
            seen.add(id(target))
            target.interrupted = True
            target.interrupt_event.set()
            self._wake_current_with_error(target, exc)
            if fail_current_tickets:
                self._fail_ticket(target.ticket, exc)
        self._response_owner = None
        self._idle.set()
        self._fail_queued(exc)

    def reset_connection_state(self) -> None:
        # Defensive: a cancel send spawned against a previous connection must
        # never fire into the replacement one.
        self._cancel_pending_cancel_sends()
        self._connection_available = True
        self._dispatch_allowed.set()
        self._server_response_ids.clear()
        self._server_vad_response_pending = False
        self._cancel_server_vad_pending_timer()
        self._cancel_stale_release_timer()
        if self._current is None and self._response_owner is None:
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
            return
        if not current.ticket.started.done():
            current.ticket.started.set_exception(exc)
            return
        if current.terminal is not None and not current.terminal.done():
            current.terminal.set_exception(exc)

    def _fail_queued(self, exc: Exception) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._fail_ticket(queued.ticket, exc)
            self._queued_by_ticket.pop(id(queued.ticket), None)
            if queued.completed is not None and not queued.completed.done():
                queued.completed.set_result(None)
            self._queue.task_done()

    async def _fail_closed(self, reason: str) -> None:
        self._mark_connection_lost(reason, fail_current_tickets=False)
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
        while self._connection_available:
            await self._dispatch_allowed.wait()
            if not self._connection_available:
                return
            queued = await self._next_queued()
            try:
                await self._process(queued)
            finally:
                self._queue.task_done()

    async def _wait_for_dispatch_or_interrupt(
        self,
        queued: _QueuedResponse,
    ) -> None:
        if self._dispatch_allowed.is_set() or queued.interrupt_event.is_set():
            return
        dispatch_waiter = asyncio.create_task(self._dispatch_allowed.wait())
        interrupt_waiter = asyncio.create_task(queued.interrupt_event.wait())
        waiters = (dispatch_waiter, interrupt_waiter)
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _wait_for_idle_or_interrupt(self, queued: _QueuedResponse) -> None:
        if self._idle.is_set() or queued.interrupt_event.is_set():
            return
        idle_waiter = asyncio.create_task(self._idle.wait())
        interrupt_waiter = asyncio.create_task(queued.interrupt_event.wait())
        waiters = (idle_waiter, interrupt_waiter)
        try:
            done, _ = await asyncio.wait(
                waiters,
                timeout=queued.response_done_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self._fail_closed("realtime response idle wait timed out")
                raise asyncio.TimeoutError("realtime response idle wait timed out")
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _process(self, queued: _QueuedResponse) -> None:
        self._current = queued
        loop = asyncio.get_running_loop()
        item_acked = not queued.ack_expected
        requeued = False

        try:
            await self._wait_for_dispatch_or_interrupt(queued)
            if queued.interrupted:
                raise RuntimeError("response dispatch interrupted")
            if not self._connection_available:
                raise ConnectionError("realtime connection is unavailable")
            if self._yield_to_higher_priority(queued):
                requeued = True
                return
            await self._wait_for_idle_or_interrupt(queued)
            if queued.interrupted:
                raise RuntimeError("response dispatch interrupted")
            if not self._connection_available:
                raise ConnectionError("realtime connection is unavailable")
            self._idle.clear()
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
            if not self._connection_available:
                raise ConnectionError("realtime connection is unavailable")
            if queued.ticket.started.done():
                if queued.ticket.started.cancelled():
                    raise RuntimeError(
                        "response dispatch rejected before response.create"
                    )
                pre_response_error = queued.ticket.started.exception()
                if pre_response_error is not None:
                    raise pre_response_error
            if self._response_owner is not None:
                raise RuntimeError("response owner is already assigned")
            queued.terminal = loop.create_future()
            self._response_owner = queued
            queued.response_send_started = True
            try:
                await self._send_event(queued.response_event)
            except Exception:
                if self._response_owner is queued:
                    self._response_owner = None
                if not queued.terminal.done():
                    queued.terminal.cancel()
                raise
            if queued.server_vad_won_during_response_send:
                # The possibly-live explicit create is now indistinguishable
                # from a server response. Detach its ticket without cancelling
                # the user's pending VAD response; pending/remembered server
                # lifecycle state keeps the lane closed until all live work
                # reaches a terminal event.
                provider_error = (
                    queued.ticket.started.exception()
                    if queued.ticket.started.done()
                    and not queued.ticket.started.cancelled()
                    else None
                )
                if self._response_owner is queued:
                    self._response_owner = None
                if not queued.terminal.done():
                    queued.terminal.cancel()
                if provider_error is not None:
                    raise provider_error
                raise RuntimeError(
                    "response dispatch interrupted by pending server VAD response"
                )
            if queued.interrupted:
                await self._send_event({"type": "response.cancel"})
                try:
                    await asyncio.wait_for(
                        asyncio.shield(queued.terminal), queued.cancel_timeout
                    )
                except Exception:
                    if not queued.terminal.done():
                        queued.terminal.cancel()
                    await self._fail_closed(
                        "interrupted response could not reach a terminal state"
                    )
                raise RuntimeError("response dispatch interrupted")
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
            if queued.terminal_error is not None:
                raise queued.terminal_error

            result = ResponseDispatchResult(
                item_acknowledged=item_acked,
                context_persistence_uncertain=not item_acked,
            )
            if not queued.ticket.done.done():
                queued.ticket.done.set_result(result)
        except Exception as exc:
            self._fail_ticket(queued.ticket, exc)
        finally:
            if self._current is queued:
                self._current = None
            if self._response_owner is queued:
                started_failed = (
                    queued.ticket.started.done()
                    and not queued.ticket.started.cancelled()
                    and queued.ticket.started.exception() is not None
                )
                if not self._server_response_active or (
                    started_failed and self._server_response_ids
                ):
                    # A response.create rejected before response.created owns
                    # no live response. Detach it even when a server-VAD
                    # response started during the preceding item-ack wait.
                    # The separately remembered server response id continues
                    # holding the lane until its own terminal event. For an
                    # id-less server response, retain the owner as the only
                    # safe terminal correlation instead of reopening early.
                    self._response_owner = None
                    if queued.terminal is not None and not queued.terminal.done():
                        queued.terminal.cancel()
                    self._release_lane_if_clear()
            if (
                not requeued
                and queued.completed is not None
                and not queued.completed.done()
            ):
                queued.completed.set_result(None)
            if not requeued:
                self._queued_by_ticket.pop(id(queued.ticket), None)
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
            if queued.terminal is not None and not queued.terminal.done():
                queued.terminal.cancel()
            await self._fail_closed(
                "response lifecycle could not reach a terminal state"
            )
        raise original_timeout
