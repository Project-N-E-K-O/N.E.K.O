# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reliable outbound queue and in-order inbound sequencer of one visit (OD-30).

Design: ``docs/design/visit-infrastructure.md`` sections 3.5.4 / 3.5.5, 4.1
("rate limits and merging"), 4.2 (``ack`` / ``leave`` / ``line_delta`` /
``text`` / ``wrap_up``) and PR-06 ``outbox.py``.

``VisitOutbox`` is the authoritative backend outbound queue of one side:

* Every outgoing payload goes through :meth:`VisitOutbox.send`. Reliable
  types (``hello / ready / wrap_up / leave / text``) get a monotonic ``seq``
  starting at 1 and stay in memory until a cumulative ``ack`` covers them;
  everything else is queued once and never retransmitted.
* :meth:`VisitOutbox.due` returns what may go on the wire *now*, as
  :class:`OutboundFrame` objects. Each frame is one transport WS
  ``send{cmd, payload}`` message (section 4.3): the iframe serialises,
  fragments and envelopes the payload, so the backend hands over payload
  objects, not envelope bytes. ``utils.visit_wire.wire_size`` is still used
  to measure each payload in fully encoded bytes and pieces, which is what
  the two token buckets and the in-flight byte cap are charged with.
* Retransmission follows ``VISIT_OUTBOX_RETRY_S`` (1, 2, 4, 8, 8 s, then
  every 8 s) and only happens when both buckets have room; when they do not,
  nothing is dropped, the item just waits (retransmissions are served before
  first sends). Any reliable item unacked for ``VISIT_DELIVERY_TIMEOUT_S`` of
  *running* time since its first transmission sets ``delivery_failed``.
* Running time stops while at least one pause reason is active
  (:meth:`VisitOutbox.pause` / :meth:`VisitOutbox.resume`): own SDK
  reconnect, local page reload grace, peer not in the room yet, peer in its
  tentative-leave rejoin grace. Nothing is sent while paused; on resume every
  transmitted but unacked item is resent at once and its timer continues
  from where it stopped.
* ``leave``: sending it moves every transmitted unacked item to the front for
  one immediate resend (ignoring the backoff), then for
  ``VISIT_LEAVE_GAP_GRACE_S`` everything unacked (``leave`` included) is
  retransmitted every second; the ``ack`` of ``leave`` or the end of that
  window ends the outbox (:meth:`VisitOutbox.leave_done`). The two second
  drain before a normal ``leave`` is exposed as :meth:`begin_drain` /
  :meth:`drain_done`; the asynchronous orchestration belongs to the runtime.
* ``line_delta`` pieces of one line released less than
  ``VISIT_DELTA_MIN_INTERVAL_MS`` apart are merged (never the piece flagged
  ``final_piece``, never when the merged text would exceed one encoded
  delta), consecutive pieces of one line leave at least that interval apart,
  and ``i`` is assigned only when a piece is actually transmitted, so the
  emitted indices are ``0, 1, 2, ...`` without holes. When the line's
  ``text`` is sent, its ``i_done`` is overwritten with the exact number of
  pieces that will have been transmitted before it.
* Reliable payloads except ``hello`` are appended to
  ``<spool_dir>/<visit_id>.outbox.jsonl`` on a dedicated single writer thread
  (call order is file order, nothing blocks the event loop). ``hello``
  carries the identity ticket and is kept in memory only; lossy payloads are
  never written.

``InboxSequencer`` is the receive side: reliable messages are handed to the
upper layer strictly in ``seq`` order, messages after a gap wait in a
bounded reorder buffer, duplicates only trigger an ``ack``, and the
cumulative ``ack`` value is the highest *contiguous* ``seq``. Two reliable
types bypass the buffer: ``wrap_up{ph:'speaking'}`` is handed out at once
through ``on_early`` (it only stops the wrap-up step timer, idempotent) and
``leave`` is reported at once so the liveness timers can wait for the gap
(``VisitLiveness.on_peer_leave_message`` / ``on_gap_filled``). Both still
occupy their ``seq`` slot and are released as no-ops in order.

Both classes are pure state machines apart from the outbox file writer:
every time value is injected, nothing awaits except :meth:`VisitOutbox.flush`
and :meth:`VisitOutbox.close`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import os
import stat
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Union

from config.visit_settings import (
    VISIT_ACK_COALESCE_MS,
    VISIT_DATA_BUCKET_BPS,
    VISIT_DATA_BUCKET_BURST_BYTES,
    VISIT_DEDUP_LRU,
    VISIT_DELIVERY_TIMEOUT_S,
    VISIT_DELTA_BACKLOG_DROP_S,
    VISIT_DELTA_BACKLOG_MERGE_S,
    VISIT_DELTA_MIN_INTERVAL_MS,
    VISIT_LEAVE_DRAIN_S,
    VISIT_LEAVE_GAP_GRACE_S,
    VISIT_LINE_DELTA_MAX_I,
    VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES,
    VISIT_MSG_BUCKET_BURST,
    VISIT_MSG_BUCKET_PER_S,
    VISIT_OUTBOX_PENDING_MAX_BYTES,
    VISIT_OUTBOX_RETRY_S,
    VISIT_PIECES_MAX,
    VISIT_REORDER_BUFFER_MAX,
)
from main_logic.visit.limits import TokenBucket
from main_logic.visit.spool import OUTBOX_SUFFIX, _scan
from utils.logger_config import get_module_logger
from utils.visit_wire import (
    RELIABLE_TYPES,
    cmd_of,
    encode_msg,
    is_reliable,
    line_delta_can_merge,
    line_delta_encoded_len,
    require_visit_id,
    visit_path,
    wire_size,
)

logger = get_module_logger(__name__, "Main")

__all__ = [
    "PAUSE_TRANSPORT",
    "PAUSE_SELF_RECONNECT",
    "PAUSE_PAGE_RELOAD",
    "PAUSE_PEER_ABSENT",
    "PAUSE_PEER_AWAY",
    "OutboundFrame",
    "OutboxReservation",
    "VisitOutbox",
    "InboxResult",
    "InboxSequencer",
    "purge_outbox_files",
]

Side = Literal["host", "guest"]

# ── Pause reasons (§3.5.4: 30s delivery clock runs only while connected with peer in room) ──
PAUSE_TRANSPORT = "transport"            # General local transport unavailability (default).
PAUSE_SELF_RECONNECT = "self_reconnect"  # Local SDK reconnect (state{reconnecting}, 25s window).
PAUSE_PAGE_RELOAD = "page_reload"        # Transport WS disconnected (20s page-reload grace).
PAUSE_PEER_ABSENT = "peer_absent"        # Peer not yet joined (host awaiting guest).
PAUSE_PEER_AWAY = "peer_away"            # Peer provisionally left, within 35s reentry grace.

# ── Module-private constants (details absent from visit_settings) ─────────
_LEAVE_UNSENT_CAP_FACTOR = 2   # Wait at most 2×VISIT_LEAVE_GAP_GRACE_S for queued leave to send.
_LEAVE_RESEND_INTERVAL_S = 1.0   # After leave sends, retransmit leave/unacknowledged items every second (§4.2 leave).
_QUEUE_MAX_ITEMS = 200           # Outgoing queue cap; invalidate droppable types first on excess (§4.3 send same value).
_COALESCED_TYPES = frozenset({"ack", "hb", "state"})   # Keep newest queue item only; next replaces it.
_LOSSY_PAUSABLE = frozenset({"line_delta", "typing", "stats"})  # Pause enqueue under tx_backpressure.
_DELTA_FIRST_FIELDS = ("sp", "ad", "rt", "wu")
_U32_MAX = 2 ** 32 - 1


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class OutboundFrame:
    """One payload released for transmission (one transport WS ``send`` message).

    ``payload`` is the validated section 4.2 payload object (a private copy);
    ``nbytes`` / ``pieces`` are its fully encoded size on the wire as measured
    by ``wire_size`` (what the buckets were charged); ``seq`` is 0 for
    unsequenced types; ``retransmit`` is False only for the first
    transmission of a payload.
    """

    cmd: int
    payload: dict
    nbytes: int
    pieces: int
    seq: int = 0
    retransmit: bool = False

    @property
    def t(self) -> str:
        """The payload type ``payload['t']``."""
        return str(self.payload.get("t"))

    def to_ws(self) -> dict:
        """The transport WS message ``{'type': 'send', 'cmd', 'payload'}`` (section 4.3)."""
        return {"type": "send", "cmd": self.cmd, "payload": copy.deepcopy(self.payload)}


class OutboxReservation:
    """Bytes of the in-flight budget held for one reliable payload (:meth:`VisitOutbox.reserve`).

    Either consumed by ``VisitOutbox.send(..., reservation=...)`` or given
    back with :meth:`release` (idempotent; use it in ``finally``).
    """

    __slots__ = ("_outbox", "nbytes", "_open")

    def __init__(self, outbox: "VisitOutbox", nbytes: int) -> None:
        self._outbox = outbox
        self.nbytes = nbytes
        self._open = True

    @property
    def held(self) -> bool:
        """True until released or consumed."""
        return self._open

    def release(self) -> None:
        """Give the bytes back (no-op once released or consumed)."""
        if self._open:
            self._open = False
            self._outbox._release_reservation(self)

    def _consume(self, outbox: "VisitOutbox") -> None:
        if outbox is not self._outbox:
            raise ValueError("reservation belongs to another outbox")
        self.release()


@dataclass(eq=False)
class _Item:
    t: str
    cmd: int
    payload: dict
    enq_at: float
    seq: int = 0
    nbytes: int = 0
    pieces: int = 1
    # line_delta
    ln: Optional[str] = None
    last_release: float = 0.0
    final_piece: bool = False
    # Guaranteed-delivery item.
    emitted: int = 0
    first_active: Optional[float] = None
    next_due: float = 0.0
    acked: bool = False
    unsent_first: bool = False  # Initial write failed: next send still counts as initial.
    written: bool = False       # Transport confirmed at least one write; later stale failures never roll back.


@dataclass
class _Line:
    next_i: int = 0                    # Actual emitted chunk count = next chunk's i.
    last_emit: Optional[float] = None
    header: Optional[dict] = None      # Additional i==0 fields sp/ad/rt/wu.
    queued: int = 0                    # Unsent queued chunks.
    closed: bool = False               # This line's text queued: frozen, no merging/invalidation/new chunks.
    dropped: bool = False              # Backlog >10s invalidates line; discard every later delta for it.


class VisitOutbox:
    """Authoritative outbound queue of one side (see the module docstring)."""

    def __init__(
        self,
        visit_id: str,
        side: Side,
        *,
        clock: Callable[[], float],
        spool_dir: Union[str, Path],
        peer_present: bool = False,
        retry_s: Iterable[float] = VISIT_OUTBOX_RETRY_S,
        delivery_timeout_s: float = VISIT_DELIVERY_TIMEOUT_S,
        data_bps: float = VISIT_DATA_BUCKET_BPS,
        data_burst_bytes: float = VISIT_DATA_BUCKET_BURST_BYTES,
        msg_per_s: float = VISIT_MSG_BUCKET_PER_S,
        msg_burst: float = VISIT_MSG_BUCKET_BURST,
        delta_min_interval_ms: float = VISIT_DELTA_MIN_INTERVAL_MS,
        backlog_merge_s: float = VISIT_DELTA_BACKLOG_MERGE_S,
        backlog_drop_s: float = VISIT_DELTA_BACKLOG_DROP_S,
        leave_grace_s: float = VISIT_LEAVE_GAP_GRACE_S,
        leave_drain_s: float = VISIT_LEAVE_DRAIN_S,
        pending_max_bytes: int = VISIT_OUTBOX_PENDING_MAX_BYTES,
    ) -> None:
        """Create the outbox of ``side`` for ``visit_id``.

        ``clock`` returns the current monotonic time and is used whenever a
        method is called without ``now``. ``spool_dir`` is
        ``<config_dir>/visit_spool``; the outbox file is created lazily by the
        first persisted payload. ``peer_present=False`` (the default) starts
        paused with ``PAUSE_PEER_ABSENT``: the runtime calls
        ``resume(now, PAUSE_PEER_ABSENT)`` when the peer enters the room, so
        a host waiting for its guest never starts the delivery timer of its
        ``hello``.
        """
        if side not in ("host", "guest"):
            raise ValueError(f"invalid side: {side!r}")
        self.visit_id = require_visit_id(visit_id)
        self.side: Side = side
        self._clock = clock
        self.spool_dir = Path(spool_dir)
        self.path = visit_path(self.spool_dir, self.visit_id, OUTBOX_SUFFIX)
        retry = tuple(float(x) for x in retry_s)
        if not retry or any(x <= 0 for x in retry):
            raise ValueError("retry_s must be a non-empty sequence of positive delays")
        self._retry = retry
        self._timeout_s = float(delivery_timeout_s)
        self._interval_s = float(delta_min_interval_ms) / 1000.0
        self._merge_s = float(backlog_merge_s)
        self._drop_s = float(backlog_drop_s)
        self._leave_grace_s = float(leave_grace_s)
        self._drain_s = float(leave_drain_s)
        self._pending_max = int(pending_max_bytes)

        now = float(clock())
        # Capacity smaller than one message never fills a normal token bucket sufficiently; cap charge at capacity (§4.1 byte bucket).
        self._bytes = TokenBucket.full(data_bps, data_burst_bytes, now, cap_cost=True)
        self._msgs = TokenBucket.full(msg_per_s, msg_burst, now, cap_cost=True)

        self._next_seq = 1
        self._queue: deque[_Item] = deque()            # Items awaiting first send (FIFO).
        self._unacked: OrderedDict[int, _Item] = OrderedDict()   # Guaranteed items ordered by seq.
        self._urgent: deque[int] = deque()             # Immediate resend (leave / resume / reload).
        self._oneshot: deque[_Item] = deque()          # Acked hello resent on demand, untracked.
        self._lines: dict[str, _Line] = {}
        self._hello: Optional[_Item] = None
        self._backpressure = False

        # Running time, stopped while paused.
        self._pause_reasons: set[str] = set() if peer_present else {PAUSE_PEER_ABSENT}
        self._active_base = 0.0
        self._active_since: Optional[float] = None if self._pause_reasons else now

        self.failed_seq: Optional[int] = None
        self._leave_seq: Optional[int] = None
        self._leave_started: Optional[float] = None
        self._leave_sent_at: Optional[float] = None
        self._leave_acked = False
        self._drain_deadline: Optional[float] = None
        self.dropped_lossy = 0
        self.ack_beyond_sent = 0

        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._last_write: Optional[concurrent.futures.Future] = None
        self._closed = False
        self._close_task: Optional["asyncio.Future[None]"] = None
        self.write_errors = 0
        self._reserved_bytes = 0

    # ------------------------------------------------------------------
    # Queries.

    @property
    def last_seq(self) -> int:
        """The highest ``seq`` assigned so far (0 before the first reliable send)."""
        return self._next_seq - 1

    @property
    def unacked_seqs(self) -> list[int]:
        """``seq`` of every reliable item not yet covered by an ``ack``, ascending."""
        return list(self._unacked)

    @property
    def reserved_bytes(self) -> int:
        """Bytes held by reservations not consumed or released yet (admitted sends still persisting)."""
        return self._reserved_bytes

    @property
    def pending_bytes(self) -> int:
        """Encoded bytes of all unacked reliable items (queued or transmitted) plus held reservations."""
        return sum(item.nbytes for item in self._unacked.values()) + self._reserved_bytes

    def try_reserve(self, nbytes: int) -> bool:
        """Whether ``nbytes`` more fit under ``VISIT_OUTBOX_PENDING_MAX_BYTES``.

        A check, not a hold: call :meth:`send` right after it, without
        awaiting in between. A human line that does not fit is refused by the
        caller with ``status{VISIT_E_BUSY}`` (section 4.2 ``text``). A caller
        that has to await between the check and the send uses :meth:`reserve`.
        """
        return self.pending_bytes + max(0, int(nbytes)) <= self._pending_max

    def reserve(self, nbytes: int) -> Optional["OutboxReservation"]:
        """Hold ``nbytes`` of the in-flight budget; None when they do not fit.

        The held bytes count in :attr:`pending_bytes` until the reservation
        is released or consumed by ``send(..., reservation=...)``, so other
        lines cannot take them while the holder awaits (section 4.5
        ``stream_data``: reserve, persist, then enqueue). Releasing is
        idempotent; a consumed reservation releases nothing.
        """
        size = max(0, int(nbytes))
        if not self.try_reserve(size):
            return None
        self._reserved_bytes += size
        return OutboxReservation(self, size)

    def _release_reservation(self, reservation: "OutboxReservation") -> None:
        self._reserved_bytes = max(0, self._reserved_bytes - reservation.nbytes)

    def encoded_size(self, msg: Mapping[str, Any]) -> tuple[int, int]:
        """``(pieces, bytes)`` of ``msg`` as it would go on the wire, for ``try_reserve``.

        A missing ``seq`` is measured at its widest (u32 max) and a missing
        ``leave.last_seq`` as ``seq - 1`` (the schema's relation, same digit
        count), and a ``text``'s ``i_done`` always at its widest
        (``VISIT_LINE_DELTA_MAX_I``; :meth:`send` and the first transmission
        rewrite it), so the result is an upper bound of what :meth:`send`
        will charge and callers need not fill internal fields.
        """
        payload = dict(msg)
        if is_reliable(str(payload.get("t"))):
            payload.setdefault("seq", _U32_MAX)
            if payload.get("t") == "leave":
                # Schema requires last_seq == seq - 1; seq estimated at u32 maximum, same-digit last_seq retains upper bound.
                payload.setdefault("last_seq", payload["seq"] - 1)
            if payload.get("t") == "text":
                payload["i_done"] = VISIT_LINE_DELTA_MAX_I
        return wire_size(encode_msg(payload), visit_id=self.visit_id)

    @property
    def paused(self) -> bool:
        """True while any pause reason is active."""
        return bool(self._pause_reasons)

    @property
    def pause_reasons(self) -> frozenset[str]:
        """The active pause reasons."""
        return frozenset(self._pause_reasons)

    def active_time(self, now: float) -> float:
        """Total running (unpaused) time since construction."""
        if self._active_since is None:
            return self._active_base
        return self._active_base + max(0.0, now - self._active_since)

    @property
    def delivery_failed(self) -> bool:
        """Sticky: some reliable item stayed unacked for the full delivery timeout."""
        return self.failed_seq is not None

    def line_pieces(self, ln: str) -> int:
        """Number of ``line_delta`` pieces of line ``ln`` actually transmitted so far."""
        line = self._lines.get(ln)
        return line.next_i if line is not None else 0

    def line_i_done(self, ln: str) -> int:
        """Pieces of ``ln`` transmitted plus still queued (an upper bound for ``i_done``).

        The ``text`` gets this value when queued; the authoritative value is
        written when the ``text`` is first transmitted (queued pieces of the
        line may still be dropped as stale before then).
        """
        line = self._lines.get(ln)
        return (line.next_i + line.queued) if line is not None else 0

    @property
    def hello_payload(self) -> Optional[dict]:
        """A copy of the last ``hello`` payload kept in memory (never on disk), or None."""
        return copy.deepcopy(self._hello.payload) if self._hello is not None else None

    # ------------------------------------------------------------------
    # Pause / resume.

    def pause(self, now: Optional[float] = None, reason: str = PAUSE_TRANSPORT) -> None:
        """Add a pause reason; the delivery timer and all transmission stop.

        Reasons: ``PAUSE_SELF_RECONNECT``, ``PAUSE_PAGE_RELOAD``,
        ``PAUSE_PEER_ABSENT``, ``PAUSE_PEER_AWAY`` or ``PAUSE_TRANSPORT``.
        Reasons are independent; the outbox runs again only when all are
        resumed.
        """
        now = self._now(now)
        if not self._pause_reasons and self._active_since is not None:
            self._active_base += max(0.0, now - self._active_since)
            self._active_since = None
        self._pause_reasons.add(str(reason))

    def resume(self, now: Optional[float] = None, reason: str = PAUSE_TRANSPORT) -> bool:
        """Clear a pause reason; return True when the outbox is running afterwards.

        When the last reason clears, the delivery timer continues from where
        it stopped and every transmitted unacked item is queued for an
        immediate resend (:meth:`replay_after_reload`).
        """
        now = self._now(now)
        if str(reason) not in self._pause_reasons:
            return not self._pause_reasons
        self._pause_reasons.discard(str(reason))
        if self._pause_reasons:
            return False
        self._active_since = now
        self.replay_after_reload(now)
        return True

    def replay_after_reload(self, now: Optional[float] = None) -> int:
        """Queue every transmitted but unacked reliable item for an immediate resend.

        Used after a Pet page reload / SDK reconnect (also called by
        :meth:`resume`). Acked items are never resent; items never
        transmitted keep their place in the first-send queue. Returns the
        number of items queued.
        """
        count = 0
        for seq, item in self._unacked.items():
            if item.emitted and not item.acked and seq not in self._urgent:
                self._urgent.append(seq)
                count += 1
        return count

    def resend_hello(self, now: Optional[float] = None) -> bool:
        """Resend the in-memory ``hello`` (same ``seq``, same ticket) at the front.

        Section 3.2.7 item 28: after a page reload the backend resends
        ``hello`` from memory even if it was acked (the peer only acks the
        duplicate ``seq``). Returns False when no ``hello`` was ever sent.
        """
        item = self._hello
        if item is None:
            return False
        if item.seq in self._unacked:
            if item.emitted and item.seq not in self._urgent:
                self._urgent.appendleft(item.seq)
            return True
        # Repeated reconnects while paused/token-starved retain one copy; ticket frames would fill rate buckets and block guaranteed messages.
        if not any(queued is item for queued in self._oneshot):
            self._oneshot.append(item)
        return True

    # ------------------------------------------------------------------
    # Enqueue.

    def _now(self, now: Optional[float]) -> float:
        return float(self._clock()) if now is None else float(now)

    def set_backpressure(self, on: bool) -> None:
        """``tx_backpressure`` from the iframe: while on, new ``line_delta / typing / stats`` are dropped."""
        self._backpressure = bool(on)

    def send(self, msg: Mapping[str, Any], *, now: Optional[float] = None,
             final_piece: bool = False, reservation: Optional["OutboxReservation"] = None) -> int:
        """Queue one section 4.2 payload; return its ``seq`` (0 for unsequenced types).

        ``reservation`` (from :meth:`reserve` on this outbox) is consumed when
        the payload is queued: its bytes stop counting once the item's own
        bytes do. It stays held when the payload is rejected.

        The outbox is authoritative for these fields and overwrites them:
        ``seq`` of reliable types; ``leave.last_seq`` (``seq - 1``);
        ``text.i_done`` (pieces of that ``ln`` transmitted or still queued,
        see :meth:`line_i_done`); ``line_delta.i`` (assigned on
        transmission). ``final_piece=True`` marks the last ``line_delta`` of
        a line, which is never merged into a previous piece.

        ``ack`` / ``hb`` / ``state`` still waiting in the queue are replaced
        by the newer one. While ``tx_backpressure`` is on, ``typing`` /
        ``stats`` are dropped and a ``line_delta`` drops the rest of its line
        (returns 0); a line whose pieces were dropped (backpressure or a
        backlog over ``VISIT_DELTA_BACKLOG_DROP_S``) gets no further pieces,
        its ``text`` closes it. After :meth:`close` has started nothing is
        queued any more: lossy types return 0, reliable ones raise
        ``ValueError`` like after ``leave`` (no ``seq`` that is neither sent
        nor in the file). Raises ``ValueError``
        for an invalid payload, a ``text`` over ``VISIT_PIECES_MAX`` pieces,
        or a reliable payload after ``leave`` or after :meth:`close`.
        """
        now = self._now(now)
        if not isinstance(msg, Mapping):
            raise ValueError("message must be a mapping")
        t = msg.get("t")
        cmd = cmd_of(t)  # Unknown type → ValueError.
        if self._closed:
            # Closed: pump stopped, file deleted. Drop droppable items; guaranteed items error as after leave (callers treat as not enqueued),
            # never allocate seq impossible to send or persist.
            if is_reliable(t):
                raise ValueError("reliable message after close")
            return 0
        if t == "line_delta":
            self._send_delta(msg, now, final_piece=final_piece)
            return 0
        if t in _LOSSY_PAUSABLE and self._backpressure:
            self.dropped_lossy += 1
            return 0
        if not is_reliable(t):
            text = encode_msg(msg)
            payload = json.loads(text)
            pieces, nbytes = wire_size(text, visit_id=self.visit_id)
            if t in _COALESCED_TYPES:
                for item in self._queue:
                    if item.t == t:
                        item.payload, item.pieces, item.nbytes = payload, pieces, nbytes
                        return 0
            self._queue.append(_Item(t=t, cmd=cmd, payload=payload, enq_at=now,
                                     nbytes=nbytes, pieces=pieces))
            self._enforce_queue_cap()
            return 0

        if self._leave_seq is not None:
            raise ValueError("reliable message after leave")
        seq = self._next_seq
        raw = dict(msg)
        raw["seq"] = seq
        ln = raw.get("ln") if t == "text" else None
        if t == "leave":
            raw["last_seq"] = seq - 1
        if t == "text":
            raw["i_done"] = self.line_i_done(str(ln))
        text = encode_msg(raw)
        pieces, nbytes = wire_size(text, visit_id=self.visit_id)
        if pieces > VISIT_PIECES_MAX:
            raise ValueError("payload exceeds VISIT_PIECES_MAX pieces; fit it first")
        if reservation is not None:
            reservation._consume(self)
        self._next_seq += 1
        item = _Item(t=t, cmd=cmd, payload=json.loads(text), enq_at=now, seq=seq,
                     nbytes=nbytes, pieces=pieces, ln=ln)
        if t == "text":
            line = self._lines.setdefault(str(ln), _Line())
            line.closed = True
        self._queue.append(item)
        self._unacked[seq] = item
        if t == "hello":
            # Tickets never persist: hello stays memory-only for retransmission (§3.5.4).
            self._hello = item
        else:
            self._persist(item)
        if t == "leave":
            self._start_leave(item, now)
        return seq

    def _send_delta(self, msg: Mapping[str, Any], now: float, *, final_piece: bool) -> None:
        ln = msg.get("ln")
        txt = msg.get("txt")
        if not isinstance(ln, str) or not isinstance(txt, str):
            raise ValueError("line_delta requires str ln and txt")
        if self._backpressure:
            # tx_backpressure invalidates all remaining line deltas (i allocated on actual send; losing a middle chunk
            # but continuing hides gaps from receivers; final text covers body).
            self._lines.setdefault(ln, _Line()).dropped = True
            self.dropped_lossy += 1
            return
        if line_delta_encoded_len(txt) > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES:
            raise ValueError("line_delta txt exceeds the encoded delta budget")
        line = self._lines.get(ln)
        if line is None:
            header = {k: msg.get(k) for k in _DELTA_FIRST_FIELDS}
            if any(v is None for v in header.values()):
                raise ValueError("first line_delta of a line requires sp/ad/rt/wu")
        else:
            if line.closed or line.dropped:
                self.dropped_lossy += 1
                return
            header = line.header
        base = {"t": "line_delta", "v": msg.get("v", 1), "ln": ln, "lp": msg.get("lp"), "txt": txt}
        # Validate first using i=0 and first-chunk fields, rejecting corrupt data at enqueue rather than due();
        # register only after success, so rejected first chunks leave no bad header poisoning corrected retries.
        encode_msg({**base, "i": 0, **(header or {})})
        if line is None:
            line = self._lines[ln] = _Line(header=header)

        prev = self._last_queued_delta(ln)
        if (prev is not None and not final_piece and not prev.final_piece
                and now - prev.last_release < self._interval_s
                and line_delta_can_merge(prev.payload["txt"], txt)):
            # Same-line starts <250ms apart with previous chunk unsent → merge into one i.
            prev.payload["txt"] = prev.payload["txt"] + txt
            prev.last_release = now
            return
        if line.next_i + line.queued >= VISIT_LINE_DELTA_MAX_I:
            # At most 255 emitted chunks per line (i=0..254), keeping i_done ≤255 as schema requires;
            # subsequent content enters text{final} only.
            self.dropped_lossy += 1
            return
        line.queued += 1
        self._queue.append(_Item(t="line_delta", cmd=cmd_of("line_delta"), payload=base,
                                 enq_at=now, ln=ln, last_release=now, final_piece=final_piece))
        self._enforce_queue_cap()

    def _last_queued_delta(self, ln: str) -> Optional[_Item]:
        for item in reversed(self._queue):
            if item.t == "line_delta" and item.ln == ln:
                return item
        return None

    def _enforce_queue_cap(self) -> None:
        while len(self._queue) > _QUEUE_MAX_ITEMS:
            victim = None
            for item in self._queue:
                if self._droppable(item):
                    victim = item
                    break
            if victim is None:
                return
            self._drop(victim)

    def _droppable(self, item: _Item) -> bool:
        if item.t == "line_delta":
            # Finalized lines can invalidate queued subtitle chunks too: text i_done uses actual emitted count
            # at first true send (due()), so invalidation preserves correctness.
            return str(item.ln) in self._lines
        return item.cmd == cmd_of("typing")

    def _drop(self, item: _Item) -> None:
        self._queue.remove(item)
        self.dropped_lossy += 1
        if item.t == "line_delta":
            line = self._lines[str(item.ln)]
            line.queued -= 1
            # Backlog invalidation: discard all remaining line deltas, rejecting later chunks; final text covers body.
            line.dropped = True
            for other in [x for x in self._queue if x.t == "line_delta" and x.ln == item.ln]:
                self._queue.remove(other)
                line.queued -= 1
                self.dropped_lossy += 1

    # ------------------------------------------------------------------
    # ack / leave / drain.

    def on_ack(self, seq: Any, now: Optional[float] = None) -> list[tuple[int, str]]:
        """Apply a cumulative peer ``ack{seq}``; return the ``(seq, t)`` pairs it released.

        Every reliable item with ``seq <= ack`` is removed, but only up to the
        first item that was never actually sent: the peer cannot have
        received it, so an ack reaching past it (a broken or modified peer)
        releases nothing beyond that point and bumps
        :attr:`ack_beyond_sent` for the runtime to count as an anomaly. The
        runtime uses the result for e.g. ``VisitLiveness.on_hello_acked``; an
        acked ``leave`` ends the outbox (:meth:`leave_done`).
        """
        if not _is_int(seq) or seq <= 0:
            return []
        released: list[tuple[int, str]] = []
        for s in list(self._unacked):
            if s > seq:
                break
            pending = self._unacked[s]
            if not pending.emitted or not pending.written:
                # Never-emitted or never-successfully-written items cannot reach peer; out-of-range acks cannot acknowledge them.
                self.ack_beyond_sent += 1
                break
            item = self._unacked.pop(s)
            item.acked = True
            released.append((s, item.t))
            if s == self._leave_seq:
                self._leave_acked = True
        if released:
            acked = {s for s, _t in released}
            self._urgent = deque(s for s in self._urgent if s not in acked)
        return released

    def _start_leave(self, item: _Item, now: float) -> None:
        self._leave_seq = item.seq
        self._leave_started = now
        # Sending leave immediately moves all prior emitted unacknowledged items to queue front for one resend, ignoring backoff.
        for seq, other in self._unacked.items():
            if seq != item.seq and other.emitted and seq not in self._urgent:
                self._urgent.append(seq)

    def final_ack_frame(self, seq: int, now: Optional[float] = None) -> OutboundFrame:
        """A standalone ``ack`` frame for the last owed ack when the channel closes.

        Bypasses the queue: :meth:`due` releases nothing once :meth:`leave_done`
        holds, but the peer's own ``leave`` may still be waiting for this ack.
        """
        text = encode_msg({"t": "ack", "v": 1, "seq": int(seq)})
        pieces, nbytes = wire_size(text, visit_id=self.visit_id)
        item = _Item(t="ack", cmd=cmd_of("ack"), payload=json.loads(text), enq_at=self._now(now),
                     nbytes=nbytes, pieces=pieces)
        return self._frame(item, retransmit=False)

    def leave_done(self, now: Optional[float] = None) -> bool:
        """True once ``leave`` is acked, or ``VISIT_LEAVE_GAP_GRACE_S`` passed since it was written.

        The grace starts when the ``leave`` frame is first written by the
        transport (:meth:`written`; it is queued behind every earlier
        reliable first send, so the receiver's gap window never opens before
        those are on the wire). A ``leave`` still not written ``2 × grace``
        after it was queued (peer absent, bucket starved, writes stuck or
        failing) ends the outbox anyway.
        """
        if self._leave_started is None:
            return False
        now = self._now(now)
        if self._leave_acked:
            return True
        if self._leave_sent_at is not None:
            # Grace starts at leave's first successful write; FIFO places it after prior guaranteed messages,
            # so receiver gap-filling window starts only after those messages send.
            return now - self._leave_sent_at >= self._leave_grace_s
        # Fallback: still unsent 2×grace after enqueue (peer absent/bucket full), stop waiting.
        return now - self._leave_started >= _LEAVE_UNSENT_CAP_FACTOR * self._leave_grace_s

    def begin_drain(self, now: Optional[float] = None) -> float:
        """Start the pre-``leave`` drain of a normal finalize; return its deadline.

        The runtime waits until :meth:`drain_done` (all reliable items acked,
        or ``VISIT_LEAVE_DRAIN_S`` passed) and only then sends ``leave``.
        Disconnect / shutdown finalizes skip the drain.
        """
        now = self._now(now)
        if self._drain_deadline is None:
            self._drain_deadline = now + self._drain_s
        return self._drain_deadline

    def drain_done(self, now: Optional[float] = None) -> bool:
        """True when nothing is unacked or the drain deadline passed."""
        if not self._unacked:
            return True
        if self._drain_deadline is None:
            return False
        return self._now(now) >= self._drain_deadline

    # ------------------------------------------------------------------
    # Outgoing.

    def _fits(self, item: _Item) -> bool:
        return self._bytes.fits(item.nbytes) and self._msgs.fits(item.pieces)

    def _charge(self, item: _Item) -> None:
        self._bytes.charge(item.nbytes)
        self._msgs.charge(item.pieces)

    def written(self, frame: OutboundFrame, now: Optional[float] = None) -> None:
        """The transport wrote ``frame``: the first such write of a reliable item anchors its timers.

        ``due`` books the release time for the delivery timeout; the first
        write that actually succeeds (possibly a resend on a replacement
        connection, after the first attempt failed or is still stuck) moves
        it to its own time and starts the ``leave`` grace, which nothing else
        starts. Later writes change nothing.
        """
        if not frame.seq:
            return
        item = self._unacked.get(frame.seq)
        if item is None or item.acked or item.written:
            return
        now = self._now(now)
        item.written = True
        item.unsent_first = False
        item.first_active = self.active_time(now)
        if item.seq == self._leave_seq:
            self._leave_sent_at = now

    def write_failed(self, frame: OutboundFrame, now: Optional[float] = None) -> None:
        """The transport did not write ``frame``: retry a reliable one at once; an unwritten item does not count as sent.

        ``due`` books a frame as transmitted when it releases it. If the
        write then fails and the item was never written (:meth:`written`),
        it is due again right away and not counted as sent: the ``leave``
        grace and the delivery timeout do not start from it, and the next
        transmission is flagged as the first one (``retransmit`` False). A
        failure of an item already written elsewhere (a stale connection
        reporting late) changes nothing.
        """
        if not frame.seq:
            return
        item = self._unacked.get(frame.seq)
        if item is None or item.acked or item.written:
            # Already written (e.g. replacement connection resent successfully): stale old-connection failure neither rolls back nor requests extra resend.
            return
        now = self._now(now)
        if item.emitted:
            # Never written (initial and all retransmissions failed): no send counts.
            item.unsent_first = True
            item.first_active = None
            if item.seq == self._leave_seq:
                self._leave_sent_at = None
        item.next_due = now

    def _frame(self, item: _Item, *, retransmit: bool) -> OutboundFrame:
        return OutboundFrame(cmd=item.cmd, payload=copy.deepcopy(item.payload),
                             nbytes=item.nbytes, pieces=item.pieces, seq=item.seq,
                             retransmit=retransmit)

    def _transmitted(self, item: _Item, now: float) -> bool:
        """Book-keeping after a reliable item went on the wire; True when this is its first transmission."""
        was_unsent = item.unsent_first
        first = item.emitted == 0 or was_unsent
        item.unsent_first = False
        if was_unsent:
            item.emitted = 0  # Failed write does not count; retry interval starts from first tier.
        if first:
            # Leave grace starts elsewhere: emitted is not written (may block/fail); written() starts at first success.
            item.first_active = self.active_time(now)
        item.emitted += 1
        if self._leave_started is not None:
            item.next_due = now + _LEAVE_RESEND_INTERVAL_S
        else:
            item.next_due = now + self._retry[min(item.emitted - 1, len(self._retry) - 1)]
        return first

    def check_delivery(self, now: Optional[float] = None) -> Optional[int]:
        """Evaluate the delivery timeout; return the failed ``seq`` (sticky) or None.

        Only running time counts; checks stop once ``leave`` is sent.
        """
        if self.failed_seq is not None:
            return self.failed_seq
        if self._leave_started is not None:
            return None
        now = self._now(now)
        active = self.active_time(now)
        for seq, item in self._unacked.items():
            if item.emitted and item.first_active is not None \
                    and active - item.first_active >= self._timeout_s:
                self.failed_seq = seq
                logger.warning("visit outbox: seq %d unacked for %.0f s of connected time",
                               seq, self._timeout_s)
                break
        return self.failed_seq

    def due(self, now: Optional[float] = None) -> list[OutboundFrame]:
        """Release whatever may be transmitted at ``now``, in wire order.

        Order: immediate resends (``leave`` / resume / reload) and the
        in-memory ``hello`` resend, then retransmissions whose backoff
        expired (earliest due first, ties by ``seq``), then first sends in FIFO
        order. Once ``leave`` is queued the last two swap, so the first sends
        queued before it and the ``leave`` itself go out before the 1 s leave
        retransmissions can starve them.
        Retransmissions are released only while both buckets have room
        (otherwise everything waits; nothing is dropped). A ``line_delta``
        is held until ``VISIT_DELTA_MIN_INTERVAL_MS`` after the previous
        piece of its line; later items of the same line wait behind it and
        reliable first sends never overtake each other. Returns an empty list
        while paused and after :meth:`leave_done`. Also updates the delivery
        timeout (:attr:`delivery_failed`).
        """
        now = self._now(now)
        if self.leave_done(now):
            return []
        self._bytes.refill(now)
        self._msgs.refill(now)
        self.check_delivery(now)
        if self._pause_reasons:
            return []
        out: list[OutboundFrame] = []

        # ① Immediate resend: on-demand acknowledged hello + leave/resume/reload-requeued unacknowledged items.
        while self._oneshot:
            item = self._oneshot[0]
            if not self._fits(item):
                return out
            self._charge(item)
            self._oneshot.popleft()
            out.append(self._frame(item, retransmit=True))
        while self._urgent:
            item = self._unacked.get(self._urgent[0])
            if item is None or not item.emitted:
                self._urgent.popleft()
                continue
            if not self._fits(item):
                return out
            self._charge(item)
            self._urgent.popleft()
            first = self._transmitted(item, now)
            out.append(self._frame(item, retransmit=not first))

        # ②③ Normally due retransmissions first (outbox front), initial sends second. After leave enqueues,
        # reverse: prior initial sends and leave first. Leave mode retransmits every second;
        # retransmitting large text first could exhaust buckets and starve leave forever
        # (grace starts only when leave actually sends; see leave_done).
        if self._leave_started is None:
            if self._release_retransmits(now, out):
                self._release_first_sends(now, out)
        else:
            if self._release_first_sends(now, out):
                self._release_retransmits(now, out)
        return out

    def _release_retransmits(self, now: float, out: list[OutboundFrame]) -> bool:
        """Release retransmissions whose backoff expired; False when the buckets ran dry."""
        # ② Due retransmissions at outbox front, ordered by due time, seq on ties;
        # seq-only ordering starves high seq under tight buckets as low seq repeatedly comes due first.
        due_items = sorted((x for x in self._unacked.values() if x.emitted and x.next_due <= now),
                           key=lambda x: (x.next_due, x.seq))
        for item in due_items:
            if not self._fits(item):  # retransmit-only-with-room
                return False
            self._charge(item)
            first = self._transmitted(item, now)
            out.append(self._frame(item, retransmit=not first))
        return True

    def _release_first_sends(self, now: float, out: list[OutboundFrame]) -> bool:
        """Release first sends in FIFO order; False when the buckets ran dry."""
        # ③ Initial sends (FIFO).
        self._maintain_backlog(now)
        blocked_lines: set[str] = set()
        reliable_blocked = False
        remaining: deque[_Item] = deque()
        stop = False
        for item in self._queue:
            if stop:
                remaining.append(item)
                continue
            if item.seq and item.acked:
                continue
            if item.ln is not None and item.ln in blocked_lines:
                remaining.append(item)
                if item.seq:
                    reliable_blocked = True
                continue
            if item.seq and reliable_blocked:
                remaining.append(item)
                continue
            if item.t == "line_delta":
                line = self._lines[str(item.ln)]
                if line.last_emit is not None and now < line.last_emit + self._interval_s:
                    blocked_lines.add(str(item.ln))
                    remaining.append(item)
                    continue
                self._prepare_delta(item, line)
            if not self._fits(item):
                # Bucket full: queue, never drop or allow overtaking, avoiding small messages starving large text.
                stop = True
                remaining.append(item)
                continue
            self._charge(item)
            if item.t == "line_delta":
                line = self._lines[str(item.ln)]
                line.next_i += 1
                line.queued -= 1
                line.last_emit = now
            elif item.seq:
                if item.t == "text" and item.emitted == 0:
                    # Same-line deltas precede text FIFO, so all emitted or invalidated now.
                    item.payload["i_done"] = self._lines[str(item.ln)].next_i
                self._transmitted(item, now)
            out.append(self._frame(item, retransmit=False))
        self._queue = remaining
        return not stop

    def _prepare_delta(self, item: _Item, line: _Line) -> None:
        """Fix ``i`` (and the ``i == 0`` extras) of a piece about to be transmitted."""
        payload = {k: v for k, v in item.payload.items() if k not in _DELTA_FIRST_FIELDS and k != "i"}
        payload["i"] = line.next_i
        if line.next_i == 0 and line.header is not None:
            payload.update(line.header)
        text = encode_msg(payload)
        item.payload = json.loads(text)
        item.pieces, item.nbytes = wire_size(text, visit_id=self.visit_id)

    def _maintain_backlog(self, now: float) -> None:
        """Backlog rules of section 4.1: drop stale lossy items, merge old deltas."""
        for item in list(self._queue):
            if item not in self._queue:
                continue
            age = now - item.enq_at
            if age > self._drop_s and self._droppable(item):
                self._drop(item)
        # Backlog >3s: keep merging adjacent same-line deltas to encoded ≤900B (exclude final chunk/finalized lines).
        merged: deque[_Item] = deque()
        for item in self._queue:
            prev = merged[-1] if merged else None
            if (prev is not None and item.t == "line_delta" and prev.t == "line_delta"
                    and prev.ln == item.ln and not item.final_piece and not prev.final_piece
                    and now - item.enq_at > self._merge_s
                    and not self._lines[str(item.ln)].closed
                    and line_delta_can_merge(prev.payload["txt"], item.payload["txt"])):
                prev.payload["txt"] = prev.payload["txt"] + item.payload["txt"]
                prev.last_release = max(prev.last_release, item.last_release)
                self._lines[str(item.ln)].queued -= 1
                continue
            merged.append(item)
        self._queue = merged

    # ------------------------------------------------------------------
    # Persistence: single writer thread, call order.

    def _persist(self, item: _Item) -> None:
        if item.t == "hello" or item.seq == 0:
            return
        line = json.dumps({"seq": item.seq, "t": item.t, "cmd": item.cmd, "payload": item.payload},
                          ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        if self._closed:
            # Closed: file deleted/pending deletion, writer stopped; late items must not recreate content files.
            return
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"visit-outbox-{self.visit_id[:6]}")
        fut = self._executor.submit(self._append_sync, data)
        fut.add_done_callback(self._on_write_done)
        self._last_write = fut

    def _append_sync(self, data: bytes) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)

    def _on_write_done(self, fut: concurrent.futures.Future) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            self.write_errors += 1
            logger.warning("visit outbox: append failed: %s", exc)

    async def flush(self) -> None:
        """Wait until every queued outbox file write has finished."""
        fut = self._last_write
        if fut is not None:
            try:
                await asyncio.wrap_future(fut)
            except Exception:  # Already logged in callback.
                pass

    def _unlink_sync(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    async def close(self, *, delete: bool = True) -> None:
        """Finish pending writes, stop the writer thread and (by default) delete the file.

        Called when the background close task of a finalize ends (``leave``
        acked or the grace expired): the file holds ``text`` bodies and must
        not outlive the visit. Idempotent. Cancelling the caller does not stop
        the cleanup: the pending write, the writer shutdown and the unlink
        still run, so no file with ``text`` bodies is left behind.
        """
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.ensure_future(self._close_impl(delete))
        await asyncio.shield(self._close_task)

    @property
    def close_task(self) -> Optional["asyncio.Future[None]"]:
        """The (single) cleanup started by :meth:`close`, or None before it."""
        return self._close_task

    @property
    def closed(self) -> bool:
        """``close`` has started: nothing is written to the file any more."""
        return self._closed

    async def _close_impl(self, delete: bool) -> None:
        await self.flush()
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False)
        if delete:
            await asyncio.to_thread(self._unlink_sync)


async def purge_outbox_files(spool_dir: Union[str, Path]) -> list[Path]:
    """Delete every leftover ``<visit_id>.outbox.jsonl`` under ``spool_dir`` (startup cleanup).

    Visits that crashed before their close task keep the file; the backend
    never replays an outbox after a restart, so it is removed on the next
    start. Only names made of a well-formed visit id plus the outbox suffix
    are touched. Returns the deleted paths.
    """
    base = Path(spool_dir)

    def run() -> list[Path]:
        # Share filename recognition with spool (_scan: visit ID + known suffixes), avoiding deletion omissions after suffix changes.
        deleted: list[Path] = []
        for _visit_id, suffix, path, st in _scan(base):
            if suffix != OUTBOX_SUFFIX or not stat.S_ISREG(st.st_mode):
                continue
            try:
                os.unlink(path)
                deleted.append(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                # Undeletable file waits for next startup; clean others normally.
                logger.warning("visit outbox: could not delete %s (%s)", path.name, exc)
        return deleted

    return await asyncio.to_thread(run)


# ══════════════════════════════════════════════════════════════════════
# Receiving side.


@dataclass
class InboxResult:
    """Outcome of :meth:`InboxSequencer.accept`.

    ``deliver``: messages for the upper layer, in order (reliable ones in
    ``seq`` order, a lossy one by itself). ``_unknown`` / ``_invalid``
    entries are no-op deliveries: the upper layer only counts them
    (``VisitRoom.record_unknown_type`` / ``record_anomaly``).
    ``duplicate``: this message itself was already seen (its ``seq``, or a
    reused ``ln``) and is not delivered again; an ``ack`` is owed. It does
    NOT mean "nothing to deliver": a duplicate can still fill a ``seq`` gap
    and release buffered messages, so ``deliver`` must always be processed
    whatever ``duplicate`` says. ``early``: the ``wrap_up{ph:'speaking'}`` handed out ahead of a
    gap. ``leave``: a ``leave`` seen for the first time (feed it to
    ``VisitLiveness.on_peer_leave_message`` with :attr:`contiguous_seq`).
    ``leave_gap_filled``: this call closed the gap before a pending
    ``leave`` (call ``VisitLiveness.on_gap_filled``). ``violation``: the
    reorder buffer overflowed (``'peer_protocol_violation'``, sticky).
    ``rejected``: a ``line_delta`` / ``line_abort`` whose ``ln`` does not
    carry the authenticated sender's prefix was dropped (count an anomaly).
    """

    deliver: list[dict] = field(default_factory=list)
    duplicate: bool = False
    early: Optional[dict] = None
    leave: Optional[dict] = None
    leave_gap_filled: bool = False
    violation: Optional[str] = None
    rejected: bool = False


_SEQUENCED_PLACEHOLDER_TYPES = frozenset({"_unknown", "_invalid"})


class InboxSequencer:
    """In-order delivery of the peer's reliable messages (see the module docstring)."""

    def __init__(
        self,
        lru: int = VISIT_DEDUP_LRU,
        reorder_max: int = VISIT_REORDER_BUFFER_MAX,
        *,
        on_early: Optional[Callable[[dict, float], None]] = None,
        peer_ln_prefix: Optional[str] = None,
        ack_coalesce_ms: float = VISIT_ACK_COALESCE_MS,
    ) -> None:
        """Create an empty sequencer (nothing received: :attr:`contiguous_seq` is 0).

        ``lru`` bounds the ``ln`` idempotency set of ``text``; ``seq``
        duplicates are recognised exactly through the contiguous watermark
        and the buffer. ``reorder_max`` bounds the messages waiting behind a
        gap. ``on_early(msg, now)`` receives ``wrap_up{ph:'speaking'}`` the
        first time it is seen (``now`` is the ``accept`` time, which
        ``VisitRoom.on_incoming_wrap_up`` needs). ``peer_ln_prefix`` (``'h:'`` / ``'g:'``), when set,
        turns a reliable message whose ``ln`` has another prefix into an
        ``_invalid`` no-op that still consumes its ``seq`` (section 4.1
        sender binding).
        """
        self._lru_max = int(lru)
        self._reorder_max = int(reorder_max)
        self._on_early = on_early
        self._prefix = peer_ln_prefix
        self._coalesce_s = float(ack_coalesce_ms) / 1000.0
        self._contiguous = 0
        self._buffer: dict[int, dict] = {}
        self._placeholders: set[int] = set()   # Already delivered early/deduplicated seq: consume as no-op when its ordered turn arrives.
        self._lns: OrderedDict[str, None] = OrderedDict()
        self._pending_leave_last: Optional[int] = None
        self._ack_pending = False
        self._last_ack_at: Optional[float] = None
        self.violation: Optional[str] = None
        self.unknown_consumed = 0
        self.invalid_consumed = 0
        self.duplicates = 0
        self.prefix_rejected = 0

    @property
    def contiguous_seq(self) -> int:
        """Highest ``seq`` received with no gap before it (the cumulative ``ack`` value)."""
        return self._contiguous

    @property
    def buffered(self) -> int:
        """Reliable messages waiting behind a gap (plus early-delivered slots)."""
        return len(self._buffer) + len(self._placeholders)

    def leave_waiting(self, last_seq: int) -> bool:
        """True while some reliable message up to ``last_seq`` (from ``leave``) is still missing."""
        return self._contiguous < last_seq

    def _remember_ln(self, ln: str) -> None:
        self._lns[ln] = None
        self._lns.move_to_end(ln)
        while len(self._lns) > self._lru_max:
            self._lns.popitem(last=False)

    def accept(self, msg: Mapping[str, Any], now: float) -> InboxResult:
        """Feed one decoded message (``utils.visit_wire.decode_msg`` output).

        Messages without a ``seq`` (lossy types, ``ack``, ``hb``, ``state``,
        unknown lossy types) are delivered at once. Reliable messages
        (including ``_unknown`` / ``_invalid`` with a ``seq``) are
        deduplicated, buffered behind gaps and released in order; every one
        of them, duplicates included, makes a cumulative ``ack`` due
        (:meth:`poll_ack`).
        """
        res = InboxResult()
        if self.violation is not None:
            res.violation = self.violation
            return res
        m = dict(msg)
        t = m.get("t")
        seq = m.get("seq")
        sequenced = (t in RELIABLE_TYPES or t in _SEQUENCED_PLACEHOLDER_TYPES) and _is_int(seq)
        if not sequenced:
            if (self._prefix is not None and t in ("line_delta", "line_abort")
                    and not str(m.get("ln")).startswith(self._prefix)):
                # Unsequenced line events also bind sender, or peer could overwrite/cut local subtitles using local ln.
                self.prefix_rejected += 1
                res.rejected = True
                return res
            res.deliver.append(m)
            return res

        self._ack_pending = True
        if seq <= self._contiguous or seq in self._buffer or seq in self._placeholders:
            self.duplicates += 1
            res.duplicate = True
            return res

        if self._prefix is not None and "ln" in m and t in ("text", "wrap_up") \
                and not str(m.get("ln")).startswith(self._prefix):
            m = {"t": "_invalid", "raw_t": t, "cmd": m.get("cmd"), "seq": seq, "error": "ln_prefix"}
            t = "_invalid"

        if t == "text":
            ln = str(m.get("ln"))
            if ln in self._lns:
                # Same line with new seq: consume sequence in order, without reprocessing.
                self.duplicates += 1
                res.duplicate = True
                self._placeholders.add(seq)
                self._advance(res)
                return self._check_overflow(res)
            self._remember_ln(ln)

        if t == "wrap_up" and m.get("ph") == "speaking":
            # Only guaranteed message delivered early: skip gap waiting, no-op at ordered turn.
            self._placeholders.add(seq)
            res.early = m
            if self._on_early is not None:
                self._on_early(m, now)
        elif t == "leave":
            # leave bypasses reordering: pass immediately upward for gap judgment, waiting at most VISIT_LEAVE_GAP_GRACE_S.
            self._placeholders.add(seq)
            res.leave = m
            last = m.get("last_seq")
            if _is_int(last) and self.leave_waiting(last):
                self._pending_leave_last = last
        else:
            self._buffer[seq] = m
        self._advance(res)
        return self._check_overflow(res)

    def _check_overflow(self, res: InboxResult) -> InboxResult:
        if len(self._buffer) + len(self._placeholders) > self._reorder_max:
            self.violation = "peer_protocol_violation"
            res.violation = self.violation
        return res

    def _advance(self, res: InboxResult) -> None:
        while True:
            nxt = self._contiguous + 1
            if nxt in self._buffer:
                m = self._buffer.pop(nxt)
                if m.get("t") == "_unknown":
                    self.unknown_consumed += 1
                elif m.get("t") == "_invalid":
                    self.invalid_consumed += 1
                res.deliver.append(m)
            elif nxt in self._placeholders:
                self._placeholders.discard(nxt)
            else:
                break
            self._contiguous = nxt
        if self._pending_leave_last is not None and not self.leave_waiting(self._pending_leave_last):
            self._pending_leave_last = None
            res.leave_gap_filled = True

    def ack_due(self, now: float) -> bool:
        """True when a cumulative ``ack`` should be sent now.

        Owed after any reliable message (duplicates included); at most one
        per ``VISIT_ACK_COALESCE_MS``, so an owed ``ack`` leaves within that
        window.
        """
        if not self._ack_pending:
            return False
        return self._last_ack_at is None or now - self._last_ack_at >= self._coalesce_s

    def poll_ack(self, now: float, *, force: bool = False) -> Optional[int]:
        """Return the ``seq`` for an ``ack`` to send now (and mark it sent), or None.

        ``force`` skips the coalescing window (an owed ``ack`` still goes out
        right before the channel closes).
        """
        if not (self._ack_pending if force else self.ack_due(now)):
            return None
        self._ack_pending = False
        self._last_ack_at = now
        return self._contiguous
