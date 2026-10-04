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

"""Liveness timers of one visit (OD-11 v2, one rule per sentence).

``VisitLiveness`` is a pure timer set (``docs/design/visit-infrastructure.md``
PR-06 ``liveness.py`` and section 3.2.7): no await, no I/O, no lock; every
time value comes in through ``now``. The runtime feeds it transport events
and polls ``tick(now)``, which returns the first verdict reached (sticky) or
``None``:

* ``invite_expired``: host waited ``invite_wait_s`` (600 s) for a verified
  peer ``hello``; observing the peer entering the room extends the deadline
  to ``max(deadline, now + VISIT_JOIN_ALLOWANCE_S)``.
* ``peer_lost``: guest waited ``peer_lost_s`` (30 s) for the host ``hello``;
  or, after verification, no peer message for more than 30 s (suspended
  while a vendor-level leave of the peer is in its rejoin grace).
* ``declined``: guest got its ``hello`` acked but no ``ready`` within
  ``VISIT_ACCEPT_TIMEOUT_S + VISIT_ACTIVATION_ALLOWANCE_S +
  VISIT_READY_DELIVERY_MARGIN_S`` (85 s).
* ``relay_lost``: own connection down past ``min(disconnect + 25 s, last
  successful send + 30 s - VISIT_RECONNECT_MARGIN_S)``.
* ``local_page_lost``: a page reload missed its deadline. Two stages
  (design §4.8): while the transport WS is down, ``min(this drop + 20 s,
  absolute)``; once a new socket is back, the absolute deadline
  ``min(left + VISIT_PEER_REJOIN_GRACE_S - VISIT_PAGE_REJOIN_SAFETY_S, last
  successful send + 30 s - VISIT_RECONNECT_MARGIN_S)`` for the SDK reload
  and room re-entry. A deadline that already passed is never moved. The
  last-send term (also in ``relay_lost``) applies only once the peer acked
  our ``hello`` (``on_hello_acked``, both sides): only then does its
  heartbeat clock run on our messages.
* ``peer_left``: authenticated ``leave`` (after the ``seq`` gap is filled or
  ``VISIT_LEAVE_GAP_GRACE_S`` expires), or a vendor-level leave not undone
  within ``VISIT_PEER_REJOIN_GRACE_S``.

``kicked{banned|room_disband}`` is finalized by the runtime directly and
vendor timeout events (TRTC reason 1, LiveKit disconnect without bye) are
left to the heartbeat clock.
"""
from __future__ import annotations

from typing import Literal, Optional

from config.visit_settings import (
    VISIT_ACCEPT_TIMEOUT_S,
    VISIT_ACTIVATION_ALLOWANCE_S,
    VISIT_HEARTBEAT_S,
    VISIT_INVITE_WAIT_S,
    VISIT_JOIN_ALLOWANCE_S,
    VISIT_LEAVE_GAP_GRACE_S,
    VISIT_LOCAL_PAGE_GRACE_S,
    VISIT_PAGE_REJOIN_SAFETY_S,
    VISIT_PEER_LOST_S,
    VISIT_PEER_REJOIN_GRACE_S,
    VISIT_READY_DELIVERY_MARGIN_S,
    VISIT_RECONNECT_MARGIN_S,
    VISIT_SELF_RECONNECT_S,
)

Side = Literal["host", "guest"]
LivenessVerdict = Literal[
    "invite_expired", "peer_lost", "declined", "relay_lost", "local_page_lost", "peer_left",
]

READY_WAIT_S = VISIT_ACCEPT_TIMEOUT_S + VISIT_ACTIVATION_ALLOWANCE_S + VISIT_READY_DELIVERY_MARGIN_S
"""Guest wait for ``ready`` after its ``hello`` is acked (60 + 15 + 10 = 85 s)."""


class VisitLiveness:
    """Pure liveness timers of one side (see the module docstring)."""

    def __init__(
        self,
        side: Side,
        now: float,
        *,
        invite_wait_s: float = VISIT_INVITE_WAIT_S,
        peer_lost_s: float = VISIT_PEER_LOST_S,
        join_allowance_s: float = VISIT_JOIN_ALLOWANCE_S,
        ready_wait_s: float = READY_WAIT_S,
        self_reconnect_s: float = VISIT_SELF_RECONNECT_S,
        reconnect_margin_s: float = VISIT_RECONNECT_MARGIN_S,
        page_grace_s: float = VISIT_LOCAL_PAGE_GRACE_S,
        leave_gap_grace_s: float = VISIT_LEAVE_GAP_GRACE_S,
        rejoin_grace_s: float = VISIT_PEER_REJOIN_GRACE_S,
        heartbeat_s: float = VISIT_HEARTBEAT_S,
        page_rejoin_safety_s: float = VISIT_PAGE_REJOIN_SAFETY_S,
    ) -> None:
        """Start in the "waiting for the peer" state.

        The host constructs it at ``invite_ready`` (wait limit
        ``invite_wait_s``); the guest at its own room join, when the host is
        already in the room (wait limit ``peer_lost_s``).
        """
        if side not in ("host", "guest"):
            raise ValueError(f"invalid side: {side!r}")
        # 与 config 的不变量同一组关系：注入的时长也要满足
        if not page_rejoin_safety_s > 0:
            raise ValueError("page_rejoin_safety_s must be positive")
        if not rejoin_grace_s - page_rejoin_safety_s > page_grace_s:
            raise ValueError("the absolute page reload deadline must outlast the socket grace")
        self.side: Side = side
        self._peer_lost_s = float(peer_lost_s)
        self._join_allowance_s = float(join_allowance_s)
        self._ready_wait_s = float(ready_wait_s)
        self._self_reconnect_s = float(self_reconnect_s)
        self._reconnect_margin_s = float(reconnect_margin_s)
        self._page_grace_s = float(page_grace_s)
        self._leave_gap_grace_s = float(leave_gap_grace_s)
        self._rejoin_grace_s = float(rejoin_grace_s)
        self._heartbeat_s = float(heartbeat_s)
        self._page_rejoin_safety_s = float(page_rejoin_safety_s)

        self.waiting = True
        wait = float(invite_wait_s) if side == "host" else self._peer_lost_s
        self.wait_deadline: float = now + wait
        self.peer_last_seen: Optional[float] = None
        self.ready_deadline: Optional[float] = None
        self.ready_received = False
        self.hello_acked = False
        self.last_sent_at: Optional[float] = None
        self.self_disconnected_at: Optional[float] = None
        self.page_departed_at: Optional[float] = None
        self.page_deadline: Optional[float] = None
        self.page_socket_back = False
        self.leave_received_at: Optional[float] = None
        self.peer_departed_at: Optional[float] = None
        self.vendor_timeout_events = 0
        self._next_heartbeat = now + self._heartbeat_s
        self._verdict: Optional[LivenessVerdict] = None

    # ------------------------------------------------------------------
    # 对端存在

    def on_peer_entered(self, now: float) -> None:
        """Host observed the peer entering the vendor room: extend the wait deadline.

        New deadline = ``max(deadline, now + VISIT_JOIN_ALLOWANCE_S)``; no
        effect once the peer is verified or on the guest side.
        """
        if self.side == "host" and self.waiting:
            self.wait_deadline = max(self.wait_deadline, now + self._join_allowance_s)

    def on_peer_verified(self, now: float) -> None:
        """The peer ``hello`` verified: leave the waiting state.

        ``peer_last_seen`` restarts at ``now``; a later re-verification (same
        ``jti`` after a reconnect) only refreshes it.
        """
        self.waiting = False
        self.peer_last_seen = now if self.peer_last_seen is None else max(self.peer_last_seen, now)
        # 核验通过的 hello 本身就证明对端在场：等待期里留下的暂定离开（对端刷新后
        # 换了 vendor 身份重进，runtime 不会调 on_peer_vendor_rejoined）不能再判 peer_left
        self.peer_departed_at = None

    def on_peer_message(self, now: float) -> None:
        """Any peer message arrived (reliable, ``hb``, lossy): refresh ``peer_last_seen``.

        While still waiting it is only recorded; the wait deadline is unchanged.
        """
        if self.peer_last_seen is None or now > self.peer_last_seen:
            self.peer_last_seen = now

    def on_hello_acked(self, now: float) -> None:
        """The peer acked our ``hello`` (both sides; the runtime calls it for each).

        From here the peer verified us and its heartbeat clock runs on our
        messages, so the last-send term of the own-reconnect and page reload
        deadlines applies (a reload already running is tightened by the same
        formula). Guest: also starts the 85 s wait for ``ready`` -- ignored
        once ``ready`` already arrived: the first ack may be lost and a later
        cumulative ack (triggered by a retransmitted ``hello``) must not re-arm
        the wait of an already active visit.
        """
        if not self.hello_acked:
            self.hello_acked = True
            if self.page_deadline is not None and not self.page_expired(now):
                self.page_deadline = min(self.page_deadline, self.page_reload_deadline())
        if self.side == "guest" and self.ready_deadline is None and not self.ready_received:
            self.ready_deadline = now + self._ready_wait_s

    def on_ready(self, now: float) -> None:
        """Guest: ``ready`` arrived; stop the ``ready`` wait for good."""
        self.ready_received = True
        self.ready_deadline = None

    # ------------------------------------------------------------------
    # 本侧连接与页面

    def on_message_sent(self, now: float) -> None:
        """A heartbeat or a reliable message was successfully handed to the vendor."""
        if self.last_sent_at is None or now > self.last_sent_at:
            self.last_sent_at = now

    def on_self_disconnected(self, now: float) -> None:
        """Own vendor connection is reconnecting; start the self deadline (idempotent)."""
        if self.self_disconnected_at is None:
            self.self_disconnected_at = now

    def on_self_connected(self, now: float) -> None:
        """Own vendor connection is back (``CONNECTED`` / ``Reconnected`` / rejoined)."""
        self.self_disconnected_at = None

    def self_deadline(self) -> Optional[float]:
        """Deadline of the current own disconnect, or ``None`` when connected.

        ``min(disconnect + 25 s, last successful send + 30 s - 3 s)``: the
        peer's 30 s clock counts from our last message, not from our
        disconnect, so the first message after a reconnect must beat it.
        """
        if self.self_disconnected_at is None:
            return None
        deadline = self.self_disconnected_at + self._self_reconnect_s
        death = self._peer_death_deadline()
        return deadline if death is None else min(deadline, death)

    def _peer_death_deadline(self) -> Optional[float]:
        """When the peer's heartbeat clock gives up on us: last successful send + 30 s - margin.

        Shared by the own-reconnect deadline and the page reload deadline
        (design §4.8 calls them "the same formula"). ``None`` until the peer
        acked our ``hello``: before that it has not verified us and keeps no
        heartbeat clock on our messages (whether we already verified the peer
        says nothing about that), so an old send must not end a reload or a
        reconnect early.
        """
        if not self.hello_acked or self.last_sent_at is None:
            return None
        return self.last_sent_at + self._peer_lost_s - self._reconnect_margin_s

    # —— 页面重载（transport WS 断开到重新入房）——

    def page_reload_deadline(self) -> Optional[float]:
        """Absolute deadline of the current page reload, or ``None`` when no reload is running.

        ``min(left + VISIT_PEER_REJOIN_GRACE_S - VISIT_PAGE_REJOIN_SAFETY_S,
        peer death deadline)`` (design §4.8, rejoin grace row): the peer gives
        a departed ``vid`` 35 s, or judges us dead 30 s after our last message
        when the vendor reported no explicit leave.
        """
        if self.page_departed_at is None:
            return None
        deadline = self.page_departed_at + self._rejoin_grace_s - self._page_rejoin_safety_s
        death = self._peer_death_deadline()
        return deadline if death is None else min(deadline, death)

    def page_reload_state(self) -> tuple[Optional[float], Optional[float], bool]:
        """Opaque copy of the page reload fields, for :meth:`restore_page_reload_state`."""
        return (self.page_departed_at, self.page_deadline, self.page_socket_back)

    def restore_page_reload_state(self, state: tuple[Optional[float], Optional[float], bool]) -> None:
        """Put back a :meth:`page_reload_state` copy (a re-entry that failed after clearing it)."""
        self.page_departed_at, self.page_deadline, self.page_socket_back = state

    def page_expired(self, now: float) -> bool:
        """True once the running page reload missed its deadline (whether or not ``tick`` ran yet)."""
        return self.page_deadline is not None and now >= self.page_deadline

    def on_page_lost(self, now: float) -> None:
        """The transport WS (page / iframe) dropped.

        The first drop of a reload records its start (the iframe leaves the
        vendor room when its socket closes). This socket stage ends at
        ``min(now + 20 s, absolute)`` -- counted from THIS drop, so a page
        that came back and dropped again still gets its socket budget, capped
        by the absolute deadline. A deadline that already passed is kept,
        and so is a socket-stage deadline that no new socket ended yet (a
        repeated call does not extend it).
        """
        if self.page_expired(now):
            return
        if self.page_deadline is not None and not self.page_socket_back:
            return
        if self.page_departed_at is None:
            self.page_departed_at = now
        self.page_socket_back = False
        self.page_deadline = min(now + self._page_grace_s, self.page_reload_deadline())

    def on_page_socket_back(self, now: float) -> None:
        """A new transport socket authenticated: the SDK reload and re-entry get the absolute deadline.

        A socket that replaced a live one starts the reload now. A deadline
        that already passed is kept (a late socket cannot revive the page).
        ``VISIT_CAPS_SDK_TIMEOUT_S`` is the capability gate's own timer
        (``caps{stage:'sdk'}``), run by the runtime as ``min(20 s, absolute
        remaining)``; it is not part of this deadline.
        """
        if self.page_expired(now):
            return
        if self.page_departed_at is None:
            self.page_departed_at = now
        self.page_socket_back = True
        self.page_deadline = self.page_reload_deadline()

    def on_page_back(self, now: float) -> None:
        """The reloaded page is back in the vendor room: the reload is over.

        No effect once the deadline passed: the verdict stays
        ``local_page_lost`` even if ``tick`` has not observed it yet.
        """
        if self.page_expired(now):
            return
        self.page_departed_at = None
        self.page_deadline = None
        self.page_socket_back = False

    # ------------------------------------------------------------------
    # 对端离开

    def on_peer_leave_message(self, now: float, last_seq: int,
                              contiguous_seq: int) -> Optional[LivenessVerdict]:
        """An authenticated data-channel ``leave`` arrived.

        ``contiguous_seq >= last_seq`` (nothing missing before it) ends the
        visit immediately and returns ``'peer_left'``; otherwise the gap gets
        ``VISIT_LEAVE_GAP_GRACE_S`` to be retransmitted (``on_gap_filled``)
        before ``tick`` reports ``peer_left``.
        """
        if contiguous_seq >= last_seq:
            return self._set_verdict("peer_left")
        if self.leave_received_at is None:
            self.leave_received_at = now
        return None

    def on_gap_filled(self, now: Optional[float] = None) -> Optional[LivenessVerdict]:
        """The ``seq`` gap before a pending ``leave`` is filled: ``peer_left`` now."""
        if self.leave_received_at is None:
            return None
        return self._set_verdict("peer_left")

    def on_peer_vendor_left(self, now: float) -> None:
        """Explicit vendor-level leave of the peer (TRTC reason 0 / LiveKit disconnect).

        Tentative: starts the ``VISIT_PEER_REJOIN_GRACE_S`` grace, during which
        the heartbeat death clock is paused (a page reload of the peer also
        looks like this). Before the peer is verified only the wait deadline
        applies.
        """
        if self.peer_departed_at is None:
            self.peer_departed_at = now

    def on_peer_vendor_rejoined(self, now: float) -> None:
        """The same peer ``vid`` reappeared: clear any grace, restart the heartbeat clock.

        Applies to every rejoin, also after a timeout-class disconnect or a
        missed explicit leave (no grace running): the vendor just confirmed
        the peer is present, so a heartbeat that was about to expire must
        not report ``peer_lost`` right after.
        """
        self.peer_departed_at = None
        if self.peer_last_seen is None or now > self.peer_last_seen:
            self.peer_last_seen = now

    def on_peer_vendor_timeout(self, now: float) -> None:
        """Vendor timeout-class leave (TRTC reason 1, LiveKit disconnect without bye).

        Deliberately not handled: only counted for diagnostics; the heartbeat
        clock decides.
        """
        self.vendor_timeout_events += 1

    # ------------------------------------------------------------------
    # 判定

    def _set_verdict(self, verdict: LivenessVerdict) -> LivenessVerdict:
        if self._verdict is None:
            self._verdict = verdict
        return self._verdict

    def tick(self, now: float) -> Optional[LivenessVerdict]:
        """Return the verdict reached at ``now`` (sticky once reached) or ``None``.

        Checked in this order: explicit leave, page loss, own relay loss,
        missing ``ready``, waiting deadline, vendor rejoin grace, heartbeat.
        """
        if self._verdict is not None:
            return self._verdict
        if self.leave_received_at is not None \
                and now - self.leave_received_at >= self._leave_gap_grace_s:
            return self._set_verdict("peer_left")
        if self.page_deadline is not None and now >= self.page_deadline:
            return self._set_verdict("local_page_lost")
        deadline = self.self_deadline()
        if deadline is not None and now >= deadline:
            return self._set_verdict("relay_lost")
        if self.ready_deadline is not None and now >= self.ready_deadline:
            return self._set_verdict("declined")
        if self.waiting:
            if now >= self.wait_deadline:
                return self._set_verdict("invite_expired" if self.side == "host" else "peer_lost")
            return None
        if self.peer_departed_at is not None:
            if now - self.peer_departed_at >= self._rejoin_grace_s:
                return self._set_verdict("peer_left")
            return None
        if self.peer_last_seen is not None and now - self.peer_last_seen > self._peer_lost_s:
            return self._set_verdict("peer_lost")
        return None

    def heartbeat_due(self, now: float) -> bool:
        """True when an ``hb`` should be sent now; advances the 5 s schedule.

        Returns True at most once per period; after a long pause it resumes
        one period from ``now`` instead of bursting.
        """
        if now < self._next_heartbeat:
            return False
        self._next_heartbeat += self._heartbeat_s
        if self._next_heartbeat <= now:
            self._next_heartbeat = now + self._heartbeat_s
        return True
