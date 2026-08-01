"""Serialize client-initiated realtime responses across their full lifecycle.

Dispatch permission is checked both before and after queue selection. If a
pause lands while an item is being selected, that item is returned to the
queue without consuming any fairness allowance. After resolving a ticket's
``sent`` future, the worker explicitly yields to its waiter before selecting
more work so that an external-turn hand-off can restore a newer pause.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterator


SendEvent = Callable[[dict[str, Any]], Awaitable[None]]
AbortTransport = Callable[[str], Awaitable[None]]
OnStuckRelease = Callable[[str, "str | None"], Awaitable[None]]
logger = logging.getLogger(__name__)

# Server-initiated response ids are remembered so their terminal events are
# never credited to an owner whose own ``response.created`` carried no id,
# and so the lane stays closed while such a response is still live even after
# the owner's own terminal has arrived. Only the most recent ids are kept: an
# id matters solely while its response is still live, so a small bound
# prevents unbounded growth on long sessions.
_SERVER_RESPONSE_ID_LIMIT = 32
# Bound on the "have I ever seen this id" set. Larger than the live-set bound
# because its entries outlive the responses they name — it exists precisely to
# still recognise an id after the live set has given up on it.
_SEEN_RESPONSE_ID_LIMIT = 128

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
# Fraction of a wait's bound past which the wait is reported even when it
# succeeds. Every fail-close bound in this file was chosen without field data
# about how long real providers actually take, and the only evidence a bound
# is too tight is a teardown that already happened. A near-miss line turns
# ordinary use into the measurement: grep for it over a few sessions and the
# distribution of "how close did we come" is there without anyone running an
# experiment. Half is deliberately generous — the point is to see the shape of
# the tail, not to warn about a healthy wait.
_WAIT_MARGIN_REPORT_FRACTION = 0.5
# Ceiling on the host's end-of-turn notification during a fail-open release.
# Escalations raised inside ``_process`` run it on the sole queue consumer,
# so an unbounded host callback would stall every later dispatch; short
# because the work it fronts is local bookkeeping plus one frontend send.
_STUCK_RELEASE_NOTIFY_TIMEOUT = 2.0


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


@dataclass(slots=True)
class _AdoptionEvidence:
    """Readings a request took of the arbiter's live adoption counters.

    Every field is a snapshot of an arbiter-wide value taken at a NAMED
    instant. Nothing pushes into this object from an event callback, which is
    what stops a fact from outliving the window it describes — the previous
    shape had one capture point plus one uncontrolled push, and the push was
    the one with no arming instant, no reset, and no way to ask "what window is
    this?".

    The two instants differ on purpose, and the rule that picks between them is
    what three rounds of review kept rediscovering one field at a time:

        Positive evidence gets the NARROWEST honest window — the one in which
        this request could have caused the thing. Disqualifying evidence gets
        the WIDEST honest window — every moment in which something else could
        explain it.

    ``arm_on_selection`` is where a disqualifier belongs: from the instant this
    request becomes ``_current``, a server-VAD boundary interrupts THIS
    request, so an automatic response announced from here on could be what we
    are about to look at. ``arm_on_send`` is where a causation claim belongs:
    nothing before this request's first byte can have been caused by it.
    """

    vad_epoch: int = -1
    serial: int = -1
    item_created_serial: int = -1

    def arm_on_selection(self, vad_epoch: int) -> None:
        self.vad_epoch = vad_epoch

    def arm_on_send(self, serial: int, item_created_serial: int) -> None:
        self.serial = serial
        self.item_created_serial = item_created_serial


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
    # Evidence this request collected for itself, so both the terminal path
    # and the started-timeout path judge an adoption from the same facts.
    adoption: _AdoptionEvidence = field(
        default_factory=_AdoptionEvidence, compare=False
    )
    item_acked: bool = field(default=False, compare=False)
    server_vad_won_during_response_send: bool = field(
        default=False,
        compare=False,
    )
    interrupted: bool = field(default=False, compare=False)
    interrupt_event: asyncio.Event = field(
        default_factory=asyncio.Event,
        compare=False,
    )
    # Set once this request's lifecycle has been escalated over. Several
    # callers can be waiting on the same stuck request, and each of their
    # timeouts fires independently; only the first escalation is meaningful.
    escalated: bool = field(default=False, compare=False)


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
        fail_open: bool = False,
        on_stuck_release: OnStuckRelease | None = None,
    ) -> None:
        self._send_event = send_event
        self._abort_transport = abort_transport
        # Escalation policy. The default tears the transport down, which is
        # what every release so far has shipped. Injected by the
        # construction site so this module reads no configuration itself.
        self._fail_open = fail_open
        # Notified that a turn ended, so the host can run the same end-of-turn
        # work its terminal event drives. Dual to ``abort_transport``.
        self._on_stuck_release = on_stuck_release
        # True while the queue consumer is suspended inside a transport
        # write; only the worker's own sends set it.
        self._worker_send_in_flight = False
        # Every response id this connection has ever attributed to anyone —
        # announced, adopted, or released. Distinct from
        # ``_server_response_ids``, which holds only ids believed LIVE and
        # therefore loses entries while the response is still running (the
        # staleness eviction, the LRU bound, and a fail-open release all drop
        # ids that may still produce events). Attribution needs the opposite
        # question — "have I ever seen this id?" — and answering it from the
        # live set credits a still-running response's terminal to whoever owns
        # the lane by then.
        self._seen_response_ids: dict[str, float] = {}
        # The single unowned ``response.created`` a request may adopt, and a
        # serial that counts every unowned announcement. A request captures the
        # serial before it sends its pre-response events; adoption requires the
        # serial to have advanced exactly once, so two announcements in the
        # window are ambiguous and neither is adoptable.
        self._adoptable_announcement: str | None = None
        self._adoptable_serial = 0
        # Set when the adoptable announcement's own terminal arrives
        # before anyone claimed it. Adoption stays deferred to the started
        # timeout, so the claim has to be able to complete a lifecycle that
        # has already ended.
        self._adoptable_terminal_status: str | None = None
        # Bumped whenever the server-VAD correlation state changes in a way
        # that could put an automatic response in the unowned bucket: the
        # pending marker being armed, and the backstop giving up on it. A
        # request that observed a different epoch than the one it started with
        # cannot know whose announcement it is looking at.
        self._vad_epoch = 0
        # Every ``conversation.item.created`` this connection has seen, in
        # arrival order. A request arms against it and compares later; it is
        # never reset, for the reason in ``_AdoptionEvidence``.
        self._item_created_serial = 0
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
        self._idless_server_response_at: float | None = None
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
                with self._report_wait_margin("unowned cancel", timeout):
                    await self.wait_until_idle(timeout)
            except asyncio.TimeoutError as original_timeout:
                await self._escalate(
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
            # The barge-in bound. Every user interruption of a speaking turn
            # arrives here, so this is the wait whose real-world margin matters
            # most and the one no synthetic provider can answer.
            with self._report_wait_margin("barge-in cancel", timeout):
                await asyncio.wait_for(asyncio.shield(current.completed), timeout)
        except asyncio.TimeoutError as original_timeout:
            await self._escalate(
                "response cancellation terminal event timed out",
                observed=current,
            )
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
            with self._report_wait_margin("targeted cancel", timeout):
                await asyncio.wait_for(asyncio.shield(queued.completed), timeout)
        except asyncio.TimeoutError as original_timeout:
            await self._escalate(
                "targeted response cancellation timed out", observed=queued
            )
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
        # Counted before anything else, including the "is there a current
        # request" check: this is a property of the CONNECTION, and a request
        # reads it by comparing against its own arming. Writing it into
        # whatever happened to be ``_current`` is what let an acknowledgement
        # belonging to an EARLIER response count as evidence for a request that
        # had not sent a byte yet.
        #
        # Counted rather than flagged, and deliberately never reset — see
        # ``_AdoptionEvidence``. Monotonic means a stale reading can only ever
        # refuse an adoption, never wrongly grant one.
        self._item_created_serial += 1
        current = self._current
        if current is None:
            return
        if current.item_ack is None or current.item_ack.done():
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
        if not isinstance(response, dict):
            return None
        response_id = response.get("id")
        # Presence, not truthiness. A provider numbering its responses from
        # zero would have had its FIRST response read as unidentified, which
        # among other things makes ``_cannot_keep_the_connection`` report "no
        # id to attribute later events by" and tear the transport down in
        # spite of the escape hatch. No configured provider numbers this way —
        # this is a trap, not a live failure — but "0 is not an id" is the
        # kind of thing that is free to get right and expensive to discover.
        #
        # An empty string still normalizes to None on purpose: it names
        # nothing, and admitting it would collapse every unidentified response
        # onto one shared identity.
        if response_id is None:
            return None
        text = str(response_id)
        return text or None

    def _remember_server_response_id(self, response_id: str) -> None:
        self._server_response_ids.pop(response_id, None)
        self._server_response_ids[response_id] = asyncio.get_running_loop().time()
        while len(self._server_response_ids) > _SERVER_RESPONSE_ID_LIMIT:
            del self._server_response_ids[next(iter(self._server_response_ids))]
        self._arm_stale_release_timer()

    def _remember_idless_server_response(self) -> None:
        self._idless_server_response_at = asyncio.get_running_loop().time()

    def _idless_server_response_live(self) -> bool:
        announced_at = self._idless_server_response_at
        if announced_at is None:
            return False
        if (asyncio.get_running_loop().time() - announced_at
                >= self._server_response_max_age):
            self._idless_server_response_at = None
            return False
        return True

    def _remember_seen_response_id(self, response_id: str | None) -> None:
        """Record that this id has been attributed to someone, ever.

        Bounded like the live set, and for the same reason; unlike the live
        set, an entry leaving here is only ever the LRU giving up, never a
        judgement that the response ended.
        """

        if response_id is None:
            return
        self._seen_response_ids.pop(response_id, None)
        self._seen_response_ids[response_id] = asyncio.get_running_loop().time()
        while len(self._seen_response_ids) > _SEEN_RESPONSE_ID_LIMIT:
            del self._seen_response_ids[next(iter(self._seen_response_ids))]

    def _note_unowned_announcement(self, response_id: str | None) -> None:
        """Offer an announcement nobody claimed as adoptable, exactly once.

        The serial advances for every unowned announcement including the
        id-less ones, so a request that cannot name what it saw refuses to
        adopt rather than guessing.
        """

        self._adoptable_serial += 1
        self._adoptable_announcement = response_id
        self._adoptable_terminal_status = None

    def _bump_vad_epoch(self) -> None:
        self._vad_epoch += 1

    def _adoptable_id_for(self, queued: _QueuedResponse) -> str | None:
        """The announcement this request may claim as its own, or None.

        Some providers begin answering when they receive the conversation
        item and never wait for ``response.create``. Their announcement then
        arrives while ``_process`` still holds the item-ack barrier, with no
        owner assigned, and is booked as server-initiated — after which the
        request's own create is never announced and its started timeout tears
        the transport down. Every turn, on every such provider.

        Never consulted before this request's own ``response.create`` has gone
        out and produced nothing. That is the discriminator, and nothing
        cheaper works: an announcement arriving between our item and its ack
        is, on the evidence available at that instant, identical whether it is
        our own echo or an automatic response the user triggered. What
        separates them is what our create then does — a provider already busy
        with someone else's response REJECTS it, and a provider that accepts it
        ANNOUNCES it. Either resolves ``started`` and this is never reached.
        Only the create that was silently redundant leaves us here.

        Both moments that qualify ask the same question, because a provider
        that answers the item directly may finish before our started allowance
        expires: the unclaimed announcement's own terminal, and the started
        timeout itself.

        Claiming it back is still only safe against evidence this request
        collected itself, so all of the following must also hold:

        - **The provider acknowledged an item while this request held the
          lane.** ``conversation.item.created`` is the only positive sign the
          announcement answers something this request sent. Deliberately not
          the item ACK: providers that assign their own item ids never match
          it, and the free routes are exactly those — requiring the ack would
          disqualify every request on the only providers that need this.
        - **Exactly one unowned announcement arrived in the window.** Two is
          ambiguous, and an id-less one cannot be named at all; the serial
          counts both, so either refuses adoption.
        - **The server-VAD correlation state did not move.** An automatic
          response announced across this window is the user's, not ours — and
          the pending marker being armed or its backstop expiring both mean
          exactly that.
        - **The id is still believed live.** An announcement whose terminal
          already arrived is finished business; adopting it would resolve this
          request against a response that has already ended.
        """

        if not queued.response_send_started or queued.response_id is not None:
            return None
        if not queued.ack_expected:
            return None
        if self._item_created_serial == queued.adoption.item_created_serial:
            return None
        if len(queued.events_before_response) != 1:
            # ``conversation.item.created`` proves the provider acknowledged
            # AN item this request sent, not WHICH one. With a single
            # pre-response event those are the same statement; with an
            # auxiliary item ahead of the instruction — a proactive vision
            # turn — they are not, and the announcement may be answering only
            # the auxiliary one. Adopting it would complete the ticket, report
            # the delivery successful and consume its scheduler state for a
            # response that never carried the text.
            return None
        if (
            self._server_vad_response_pending
            or self._vad_epoch != queued.adoption.vad_epoch
        ):
            return None
        if self._adoptable_serial != queued.adoption.serial + 1:
            return None
        adopted = self._adoptable_announcement
        if adopted is None:
            return None
        if (
            adopted not in self._server_response_ids
            and self._adoptable_terminal_status is None
        ):
            # Neither live nor known-finished: its terminal was handled as
            # somebody else's, so it is not this request's to take.
            return None
        return adopted

    def _adopt_announcement(self, queued: _QueuedResponse, response_id: str) -> None:
        """Transfer an unowned announcement to this request.

        A MOVE, not a copy. Leaving the id in ``_server_response_ids`` would
        leave the lane held by a response the owner is simultaneously
        answering for, so the owner's own terminal could not reopen it and
        every adopted turn would wedge dispatch until the staleness bound
        expired — worse than the failure this repairs. Afterwards the state is
        the one ``notify_response_created``'s owner branch would have produced
        had the announcement arrived a moment later.
        """

        self._server_response_ids.pop(response_id, None)
        if not self._server_response_ids:
            self._cancel_stale_release_timer()
        queued.response_id = response_id
        self._remember_seen_response_id(response_id)
        terminal_status = self._adoptable_terminal_status
        self._adoptable_announcement = None
        self._adoptable_terminal_status = None
        if not queued.ticket.started.done():
            queued.ticket.started.set_result(None)
        if terminal_status is not None:
            # It finished while the owner was still waiting to be told it had
            # started. Adopting only the announcement would leave the owner
            # waiting out its full response_done allowance for a terminal that
            # already came and went.
            if terminal_status not in {"completed", "success", "succeeded"}:
                queued.terminal_error = RuntimeError(
                    f"response.done status={terminal_status}"
                )
            if queued.terminal is not None and not queued.terminal.done():
                queued.terminal.set_result(None)
        logger.info(
            "adopted unowned response.created %s for %s (%s): this provider "
            "answers the conversation item without waiting for response.create",
            response_id,
            queued.source,
            "already terminated" if terminal_status is not None else "still live",
        )

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
        # The backstop is a guess, not evidence the automatic response was
        # abandoned: past this point a genuine VAD response.created can still
        # arrive and will land in the unowned bucket. Advancing the epoch is
        # what stops a request that started before this moment from adopting
        # it.
        self._bump_vad_epoch()
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
            self._remember_seen_response_id(response_id)
            if response_id is not None:
                self._remember_server_response_id(response_id)
            else:
                self._remember_idless_server_response()
            # Deliberately NOT offered for adoption. This branch is the one
            # announcement the arbiter positively knows is the provider's own
            # automatic response, so it is the last thing a queued request
            # should be allowed to claim.
            return
        owner = self._response_owner
        if owner is not None and not owner.ticket.started.done():
            # The first response.created after the owner's response.create is
            # credited to the owner. Remember its response id (when the
            # provider supplies one) so only that response's terminal event
            # can release the lane.
            owner.response_id = self._event_response_id(event)
            self._remember_seen_response_id(owner.response_id)
            owner.ticket.started.set_result(None)
            return
        # This created event cannot be credited to a waiting owner (the owner
        # already started, or no owner is pending), so it announces a
        # server-initiated response. Remember its id so its terminal event is
        # recognized as an orphan even when the owner's own response.created
        # carried no id.
        response_id = self._event_response_id(event)
        self._remember_seen_response_id(response_id)
        if response_id is not None:
            self._remember_server_response_id(response_id)
        else:
            self._remember_idless_server_response()
        # Offer it for adoption. On a provider that starts answering as soon
        # as it receives the conversation item — rather than waiting for
        # response.create — this IS the request's own announcement, arriving
        # while the item-ack barrier still holds the owner slot empty. Nothing
        # here decides that; ``_adoptable_id_for`` does, against evidence the
        # dispatching request captured for itself.
        self._note_unowned_announcement(response_id)

    def notify_server_vad_started(self) -> None:
        """Deliberately leave ownership unchanged until speech_stopped.

        The transport keeps this explicit boundary so future callers do not
        accidentally treat speech_started as evidence of an automatic
        response; only the ended utterance below may arm that correlation.
        """

    def notify_server_vad_response_pending(
        self,
        *,
        arm_timeout: bool = True,
    ) -> None:
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
        self._bump_vad_epoch()
        self._server_response_active = True
        self._idle.clear()
        if arm_timeout:
            self._arm_server_vad_pending_timer()

    def arm_server_vad_response_pending_timeout(self) -> None:
        """Start the missing-created backstop after receive handling resumes."""

        if (
            self._server_vad_response_pending
            and self._server_vad_pending_handle is None
        ):
            self._arm_server_vad_pending_timer()

    def notify_response_terminal(self, event: dict[str, Any] | None = None) -> None:
        owner = self._response_owner
        response_id = self._event_response_id(event)
        if response_id is None:
            self._idless_server_response_at = None
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
                # The owner has not seen its response.created yet. On a
                # provider that announces responses, a terminal never precedes
                # its own created event, so this belongs to another
                # (server-initiated) response and the owner keeps waiting.
                #
                # That premise is a property of the PROVIDER, not a law: some
                # never send response.created at all, and there the very same
                # shape is the owner's own terminal — indefinitely withheld
                # from it, until the started timeout tears the transport down.
                # The two readings are told apart by whether this id was ever
                # announced. Deciding it from observed behaviour rather than a
                # configured flag matters: these proxies have changed which
                # events they emit, and a flag that says "never announces"
                # about a route that started announcing guarantees the
                # teardown it was meant to prevent.
                never_announced = (
                    owner.response_send_started
                    and owner.response_id is None
                    and response_id not in self._server_response_ids
                    and response_id not in self._seen_response_ids
                )
                # The other shape, and the reason this is checked here at all:
                # a provider that answers the conversation item directly can
                # finish the whole response before the owner's started
                # allowance expires, so its terminal — not the timeout — is
                # where the adoption has to happen. Same evidence either way.
                if response_id == self._adoptable_announcement:
                    # Deliberately NOT adopted here. A provider that answers
                    # the item directly finishes before the owner's started
                    # allowance expires, so it is tempting to claim it now —
                    # but at this instant this is still indistinguishable from
                    # an automatic response the user triggered, whose terminal
                    # can equally land while the owner's create is in flight.
                    # What tells them apart is what that create does, and it
                    # has not done it yet. Remember the outcome so the started
                    # timeout can still claim it once nothing has answered.
                    self._adoptable_terminal_status = response_status or "completed"
                if never_announced:
                    # Attribution is one act, not two: the id and the
                    # announcement both belong to the owner now. Resolving
                    # only the terminal would leave the fall-through below to
                    # stamp started with "response terminated before
                    # response.created" — no teardown, but every turn on such
                    # a provider still reported as a failure.
                    owner.response_id = response_id
                    self._remember_seen_response_id(response_id)
                    owner.ticket.started.set_result(None)
                    logger.info(
                        "terminal %s claimed by %s: this connection has never "
                        "announced a response",
                        response_id,
                        owner.source,
                    )
                    # Deliberately no ``return``: this IS the owner's terminal,
                    # so it must reach the resolution below. Returning here
                    # would leave the lifecycle waiter unresolved and the
                    # started timeout would tear the transport down anyway.
                else:
                    self._server_response_ids.pop(response_id, None)
                    if not self._server_response_ids:
                        # The separately tracked server turn has ended. The
                        # pending owner still holds dispatch serialization,
                        # but a later response.create rejection must be able
                        # to detach it instead of mistaking this
                        # already-terminal turn for a live id-less response.
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
            if response_id == self._adoptable_announcement:
                # Same evidence as the owner branch above, one instant
                # earlier. A provider that answers the conversation item
                # directly can finish a short reply before the item-ack
                # barrier releases and the owner slot is filled, and the
                # terminal's arrival time carries no information the
                # announcement's did not — the serial check already proved the
                # announcement belongs to this request's window. Without this,
                # the id ends up neither live nor known-finished, adoption
                # refuses it, and the started timeout tears the socket down.
                self._adoptable_terminal_status = response_status or "completed"
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
        self._idless_server_response_at = None
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
        self._idless_server_response_at = None
        # Response ids are scoped to a connection: a provider that restarts
        # its numbering would otherwise have the new session's first terminal
        # recognised as one this arbiter has "already seen" and withheld from
        # its owner.
        self._seen_response_ids.clear()
        self._adoptable_announcement = None
        self._adoptable_terminal_status = None
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

    async def _escalate(
        self,
        reason: str,
        *,
        observed: _QueuedResponse | None = None,
        transport_write_failed: bool = False,
    ) -> None:
        """Give up on a lifecycle that cannot reach a terminal state.

        ``observed`` is the request whose failure the caller actually watched.
        Escalation is scoped to it, and happens at most once:

        - **Not current any more** — every caller here waited on one specific
          request, and by the time its timeout fires the arbiter may already
          have moved on: the stuck lifecycle completed, the worker selected
          the next queued item, and ``_current``/``_response_owner`` now name
          a perfectly healthy turn. Acting on "whatever is current" would tear
          down that healthy turn instead.
        - **Already escalated** — several callers can be waiting on the same
          stuck request and their timeouts fire independently. Only the first
          escalation is meaningful; repeating it would repeat every effect it
          has, which for anything beyond a transport teardown means doing it
          twice to the same turn.

        Callers with nothing to name (an unowned server response) pass
        nothing and keep the unscoped behaviour.
        """

        if observed is not None:
            if observed.escalated:
                logger.debug(
                    "skipping duplicate escalation for %s (%s)",
                    observed.source,
                    reason,
                )
                return
            if (
                observed is not self._current
                and observed is not self._response_owner
            ):
                logger.debug(
                    "skipping stale escalation for %s (%s): that lifecycle is "
                    "no longer current",
                    observed.source,
                    reason,
                )
                return
            if (
                observed.terminal is not None
                and observed.terminal.done()
                and not observed.terminal.cancelled()
                # Completed WITH A RESULT. notify_error finishes this same
                # future with set_exception when the provider reports a
                # correlated error — during the cancel grace period, that is
                # the ordinary way a stuck lifecycle ends — and an error is
                # not an arrival. Treating it as one skipped the recovery
                # entirely: no teardown, no release, and the lane left owned
                # until some later timeout noticed.
                and observed.terminal.exception() is None
            ):
                # Its terminal actually arrived while this caller's timeout was
                # firing. ``notify_response_terminal`` clears
                # ``_response_owner`` synchronously but the worker has not run
                # its ``finally`` yet, so ``_current`` still names a request
                # that is, in fact, done. Escalating here would end a completed
                # turn a second time — duplicate host finalization under
                # fail-open, and a teardown over a terminal that just landed
                # under the default.
                #
                # ``cancelled()`` is excluded on purpose: the escalation paths
                # themselves cancel this future immediately before calling in,
                # so treating that as an arrival would suppress every real
                # escalation.
                logger.debug(
                    "skipping escalation for %s (%s): its terminal arrived",
                    observed.source,
                    reason,
                )
                return
            observed.escalated = True

        blocker = self._cannot_keep_the_connection(
            transport_write_failed=transport_write_failed
        )
        if self._fail_open and blocker is None:
            await self._release_stuck_lifecycle(reason, observed=observed)
            return
        if self._fail_open:
            logger.warning(
                "response arbiter cannot keep the connection (%s); "
                "failing closed despite the escape hatch",
                blocker,
            )
        await self._tear_down_transport(reason)

    def _cannot_keep_the_connection(
        self, *, transport_write_failed: bool
    ) -> str | None:
        """Answer the single question fail-open rests on, and name the blocker.

        Keeping the connection is only defensible when it is still usable AND
        the arbiter can still tell whose events are whose. Anything that
        falsifies either half belongs here, so the policy has one place to
        read rather than a list of conditions to keep in sync.

        Returns the blocker's description, or ``None`` when the connection can
        be kept.
        """

        if self._worker_send_in_flight:
            # Nothing this class does to its own state unwinds an await parked
            # in the transport, and ``_run`` is the only consumer — keeping the
            # connection would leave it wedged while reporting recovery. The
            # transport's close is what wakes that write.
            return "the queue consumer is suspended inside a transport write"
        if transport_write_failed:
            # The caller's own write raised moments ago; on the fatal branch
            # the transport has already dropped its socket.
            return "a transport write just failed"
        owner = self._response_owner
        # NOTE: ``response_send_started`` is currently implied by ``owner is
        # not None`` — ``_process`` assigns the owner on the statement before
        # it sets the flag, with no await between them, and that is the only
        # assignment that makes an owner. The conjunct is kept explicit anyway:
        # it names the criterion this blocker actually rests on, so reordering
        # those two statements later cannot silently change what is being
        # tested. It also means this cell cannot discriminate the two candidate
        # criteria on its own — the discriminating case is an owner whose
        # create IS on the wire with no announcement back, covered by
        # ``test_a_create_on_the_wire_without_an_announcement_stands_the_hatch_down``.
        if owner is not None and owner.response_send_started and (
            owner.response_id is None
        ):
            # The create reached the wire, so the provider may still announce
            # this response later. Without an id there is nothing to tell that
            # announcement apart from the next turn's, and it would be credited
            # to whichever ticket dispatches next.
            #
            # The criterion is deliberately "did the create go out", not "did
            # response.created come back": a request whose create is on the
            # wire is exactly the one that can still surprise us.
            return "the abandoned response has no id to attribute later events by"
        if self._server_vad_response_pending:
            # Same shape without an owner: announced by speech_stopped, no id
            # until its response.created arrives.
            return "an announced server-VAD response has no id yet"
        if self._idless_server_response_live() or (
            self._server_response_active
            and self._response_owner is None
            and not self._server_response_ids
        ):
            # A response is live, nobody owns it, and it supplied no id — an
            # id-less proxy's orphan. Nothing identifies it, so neither its
            # deltas nor its terminal can be told from the next turn's, and a
            # late id-less terminal would complete whoever owns the lane by
            # then.
            return "an unowned response is live with no id at all"
        return None

    @contextlib.contextmanager
    def _report_wait_margin(self, label: str, timeout: float) -> Iterator[None]:
        """Report how close a bounded lifecycle wait came to its bound.

        Wraps the wait rather than replacing it: the control flow, the
        exception raised and the escalation that follows are all unchanged,
        so this cannot alter what the arbiter does — it only records what it
        saw. A wait that times out reports at 100% and pairs with the
        escalation line that follows it; a wait that succeeds late is the more
        interesting record, because nothing else in the system would ever
        mention it.
        """

        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            yield
        finally:
            elapsed = loop.time() - started
            if timeout > 0 and elapsed >= timeout * _WAIT_MARGIN_REPORT_FRACTION:
                logger.info(
                    "realtime %s waited %.2fs of its %.1fs bound (%.0f%%)",
                    label,
                    elapsed,
                    timeout,
                    100.0 * elapsed / timeout,
                )

    async def _worker_send(self, event: dict[str, Any]) -> None:
        """Send from the queue consumer, flagged for the duration of the write.

        Only the worker's sends qualify: a caller-task send that blocks hurts
        only that caller, while a blocked consumer stops every later request.
        The flag is what lets the policy notice that "the connection is
        usable" has stopped being true.
        """

        self._worker_send_in_flight = True
        try:
            await self._send_event(event)
        finally:
            self._worker_send_in_flight = False

    async def _release_stuck_lifecycle(
        self, reason: str, *, observed: _QueuedResponse | None = None
    ) -> None:
        """Drop the stuck turn and keep the connection.

        Deliberately narrower than ``_mark_connection_lost``, which is for a
        connection that is actually gone. It does NOT clear
        ``_connection_available`` (the transport still works), bump
        ``_connection_generation`` (there is no replacement connection to
        protect in-flight cancels from), set ``_dispatch_allowed`` (an
        external-ASR turn's pause must survive an unrelated stuck response),
        or fail the queue (that work is still viable).

        Order matters. The host is told the turn is over BEFORE the lane
        opens: the notification ends the turn on the host's side, and letting
        the next request dispatch first would have it rotate the speech id and
        finalize shared turn state underneath a response that had already
        started. Awaiting on a caller task alone does not serialize anything —
        the worker is a separate task and only a closed lane holds it.
        """

        exc = RuntimeError(reason)
        owner = self._response_owner
        current = self._current
        # Wake the request the CALLER watched, and only that one. The scoping
        # check in ``_escalate`` has already established it is still the
        # current or the owning lifecycle, so this both unwinds ``_process``
        # now — rather than letting it park until its own timeout escalates a
        # second time — and cannot reach anything else.
        #
        # "Anything else" is not hypothetical: ``cancel_current``'s unowned
        # branch names nothing, because what is stuck there is a
        # server-initiated response that has no ticket at all. A request
        # enqueued while that cancellation waits becomes ``_current`` as the
        # worker parks behind the live server response — undispatched, and
        # nothing to do with the stuck lifecycle. Failing it would drain
        # queued work, which ``cancel_current`` documents that it never does.
        if observed is not None:
            observed.interrupted = True
            observed.interrupt_event.set()
            self._wake_current_with_error(observed, exc)

        # Everything below straddles an await, so the release is scoped to
        # what was captured before it and finishes even if this task is
        # cancelled. The world can move while the host is being notified: the
        # abandoned response's terminal can land, the worker can wake and take
        # a queued request as the new owner. Writing "the owner" unconditionally
        # afterwards would erase that new owner, whose terminal could then
        # never resolve its ticket.
        released_owner = owner
        released_id = owner.response_id if owner is not None else None
        # The release gives up this turn's bookkeeping, but the response it
        # names may still be running and may still deliver its terminal. Keep
        # the id in the seen set so that terminal is recognised as already
        # attributed instead of looking like one this connection never
        # announced — which a successor owner would otherwise claim.
        self._remember_seen_response_id(released_id)
        # Only a released OWNER is a turn the host should end. Two escalation
        # routes reach here without one — cancel_current()'s unowned branch,
        # and an idle-wait timeout on a request that is still queued — and in
        # both the host may nonetheless be tracking a live server-initiated
        # response, because ``response.created`` is what sets its identity and
        # that event does not care who asked. Telling the host to finalize
        # then discards the turn on one side while this release deliberately
        # keeps that response's id on the other, and the response's own later
        # deltas are stale-filtered against an identity the host no longer
        # holds. A queued ``_current`` is not a turn: nothing of it has
        # reached the provider under its own name.
        had_lifecycle = released_owner is not None
        try:
            if self._on_stuck_release is not None and had_lifecycle:
                await asyncio.wait_for(
                    self._on_stuck_release(reason, released_id),
                    _STUCK_RELEASE_NOTIFY_TIMEOUT,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "stuck-release host notification exceeded %.1fs; opening "
                "the lane anyway",
                _STUCK_RELEASE_NOTIFY_TIMEOUT,
            )
        except Exception as exc_host:
            logger.warning(
                "stuck-release host notification failed: %s", exc_host
            )
        finally:
            # Give up on THIS turn's bookkeeping, and only this turn's. The
            # remembered server response ids belong to separately initiated
            # responses that are still tracking normally: clearing them would
            # discard a live response's identity, and the next queued create
            # would then overlap it. Nothing about the abandoned owner makes
            # those untrustworthy.
            if self._response_owner is released_owner:
                self._response_owner = None
            # Which is why the lane reopens through the ordinary release check
            # rather than by force — it already knows to keep the lane closed
            # while a live server response is being tracked, and to arm the
            # staleness timer that eventually retires one.
            #
            # It does NOT know about owners, though: every other caller either
            # has just cleared the owner or guards on there being none. So does
            # this one, because the owner here may not be the one it captured —
            # a successor can take the lane while the host is being notified,
            # and opening it over that successor is what lets the next
            # response.create overlap a live response. Its own terminal path
            # re-runs this check, exactly as the staleness timer's does.
            if self._response_owner is None:
                self._release_lane_if_clear()
            if current is None and released_owner is None:
                # cancel_current()'s unowned branch escalates over a
                # server-initiated response the arbiter never owned, so there
                # was no lifecycle here to release: the transport survives but
                # nothing was given up, and the lane is still held by whatever
                # server response ids are tracked. Saying "failing open" here
                # reads as though a turn was dropped and the lane reopened,
                # which sent people looking for the wrong thing.
                logger.warning(
                    "response arbiter kept the transport but had no lifecycle "
                    "to release: %s (lane held by server response ids: %s, "
                    "queue_depth=%d); the lane reopens when their terminal "
                    "events arrive, or after %.0fs if none do",
                    reason,
                    ", ".join(self._server_response_ids) or "none",
                    self._queue.qsize(),
                    self._server_response_max_age,
                )
            else:
                logger.warning(
                    "response arbiter failing open, transport kept: %s "
                    "(current=%s owner=%s queue_depth=%d)",
                    reason,
                    current.source if current is not None else None,
                    released_owner.source if released_owner is not None else None,
                    self._queue.qsize(),
                )

    async def _tear_down_transport(self, reason: str) -> None:
        # This is the only chokepoint through which the arbiter tears down the
        # transport, and to the rest of the system the result is
        # indistinguishable from a provider-side disconnect (the receive loop
        # errors first, then host cleanup runs). Log the initiator, the reason
        # and the lane state here so a field disconnect can be attributed —
        # see issue #2561, where the absence of exactly this line forced a
        # build-provenance investigation to rule the arbiter out.
        current = self._current
        owner = self._response_owner
        logger.warning(
            "response arbiter failing closed: %s "
            "(current=%s owner=%s queue_depth=%d server_vad_pending=%s)",
            reason,
            current.source if current is not None else None,
            owner.source if owner is not None else None,
            self._queue.qsize(),
            self._server_vad_response_pending,
        )
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

    async def _next_queued(
        self,
    ) -> tuple[_QueuedResponse, tuple[_QueuedResponse, ...]]:
        """Select fairly, deferring bypass accounting until dispatch is allowed."""

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
        bypassed = tuple(
            candidate for candidate in candidates if candidate is not selected
        )
        for candidate in bypassed:
            self._queue.put_nowait(candidate)
            self._queue.task_done()
        return selected, bypassed

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
            queued, bypassed = await self._next_queued()
            if not self._dispatch_allowed.is_set():
                self._queue.put_nowait(queued)
                self._queue.task_done()
                continue
            for candidate in bypassed:
                candidate.bypass_count += 1
            try:
                await self._process(queued)
            finally:
                self._queue.task_done()
            if queued.ticket.sent.done():
                # Resolving ``ticket.sent`` schedules callers that may need to
                # restore a newer external-turn pause. ``sleep(0)`` always
                # suspends the current task, unlike ``wait_for`` on an already
                # completed future on Python 3.12+, so the waiter runs before
                # this worker can select another queued response.
                await asyncio.sleep(0)

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
                await self._escalate(
                    "realtime response idle wait timed out", observed=queued
                )
                raise asyncio.TimeoutError("realtime response idle wait timed out")
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _process(self, queued: _QueuedResponse) -> None:
        self._current = queued
        # The disqualifier arms HERE, not after the waits below. From this
        # instant a server-VAD boundary interrupts this request, so an
        # automatic response announced from here on could be what a later
        # adoption is looking at — including one announced after the 5s
        # missing-created backstop gives up while this request is still
        # parked waiting for the lane.
        queued.adoption.arm_on_selection(self._vad_epoch)
        loop = asyncio.get_running_loop()
        item_acked = not queued.ack_expected
        queued.item_acked = item_acked
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
            # The causation claims arm HERE, immediately before the first
            # pre-response event goes out: nothing earlier can have been caused
            # by this request. The disqualifier armed at selection, deliberately
            # wider.
            queued.adoption.arm_on_send(
                self._adoptable_serial, self._item_created_serial
            )
            for event in queued.events_before_response:
                if queued.interrupted:
                    raise RuntimeError("response dispatch interrupted")
                await self._worker_send(event)

            if queued.item_ack is not None:
                try:
                    # The one bound that was not instrumented, which made
                    # "no wait spent half its allowance" a claim nothing could
                    # back for it. It is also the bound I once mis-reported as
                    # over budget from outside the arbiter, so leaving it
                    # unmeasured from inside was the worst possible gap.
                    with self._report_wait_margin(
                        "conversation item ack", queued.item_ack_timeout
                    ):
                        await asyncio.wait_for(
                            asyncio.shield(queued.item_ack), queued.item_ack_timeout
                        )
                    item_acked = True
                    queued.item_acked = True
                except asyncio.TimeoutError:
                    item_acked = False
                    queued.item_acked = False
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
                await self._worker_send(queued.response_event)
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
                # Same shape as ``_cancel_after_timeout`` below, and for the
                # same reason: with the send outside the try, a transport that
                # refused this cancel raised straight past the escalation, so
                # the connection that had just failed a write was never torn
                # down. ``_worker_send``'s finally has already lowered the
                # in-flight flag by then, which is why the failure has to be
                # remembered rather than re-read.
                cancel_write_failed = False
                try:
                    try:
                        await self._worker_send({"type": "response.cancel"})
                    except Exception:
                        cancel_write_failed = True
                        raise
                    await asyncio.wait_for(
                        asyncio.shield(queued.terminal), queued.cancel_timeout
                    )
                except Exception:
                    if not queued.terminal.done():
                        queued.terminal.cancel()
                    await self._escalate(
                        "interrupted response could not reach a terminal state",
                        observed=queued,
                        transport_write_failed=cancel_write_failed,
                    )
                raise RuntimeError("response dispatch interrupted")
            if not queued.ticket.sent.done():
                queued.ticket.sent.set_result(None)

            try:
                # How long the provider takes to announce a response it has
                # accepted. On a congested shared route this is the bound most
                # likely to be wrong, and a queued create is invisible to every
                # other log line.
                with self._report_wait_margin(
                    "response announcement", queued.response_started_timeout
                ):
                    await asyncio.wait_for(
                        asyncio.shield(queued.ticket.started),
                        queued.response_started_timeout,
                    )
            except asyncio.TimeoutError as started_timeout:
                # Before giving up: the create may have produced nothing
                # because this provider had already started answering the
                # conversation item. Expiring here is what makes that
                # distinguishable — an announcement that belonged to someone
                # else would have left this request's own create to be
                # rejected or announced, and either resolves ``started``
                # instead of timing out.
                adopted_id = self._adoptable_id_for(queued)
                if adopted_id is not None:
                    self._adopt_announcement(queued, adopted_id)
                else:
                    await self._cancel_after_timeout(queued, started_timeout)
            try:
                with self._report_wait_margin(
                    "response completion", queued.response_done_timeout
                ):
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
        cancel_write_failed = False
        try:
            try:
                await self._worker_send({"type": "response.cancel"})
            except Exception:
                # ``_worker_send``'s finally has already lowered the in-flight
                # flag, so without remembering this the escalation below would
                # read a usable transport moments after this one refused a write.
                cancel_write_failed = True
                raise
            assert queued.terminal is not None
            with self._report_wait_margin("cancel grace", queued.cancel_timeout):
                await asyncio.wait_for(
                    asyncio.shield(queued.terminal), queued.cancel_timeout
                )
        except Exception:
            if queued.terminal is not None and not queued.terminal.done():
                queued.terminal.cancel()
            await self._escalate(
                "response lifecycle could not reach a terminal state",
                observed=queued,
                transport_write_failed=cancel_write_failed,
            )
        raise original_timeout
