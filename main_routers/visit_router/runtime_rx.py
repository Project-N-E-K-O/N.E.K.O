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

"""Receive pipeline of a visit runtime (design §4.1 / §4.2, ``recv`` of §4.3).

Order for every reassembled data-channel message:

1. per-sender frame buckets (``PeerRateLimiter.admit_frame``) before parsing;
2. sender binding: once the peer is verified only its ``vid`` is accepted
   (a guest knows the host's ``vid`` from its credentials); before that only
   ``hello`` is looked at;
3. ``decode_msg`` (schema), then the control / lossy buckets;
4. ``InboxSequencer``: reliable messages strictly in ``seq`` order, an
   ``ack`` owed for every reliable one (duplicates too);
5. the reception gate: until the visit is activated only ``hello / ready /
   leave / hb / ack`` are processed -- a ``text`` is acked but neither shown,
   stored nor answered;
6. per type: ``observe_lp`` first, the ``text`` token bucket after ordering
   (new ``seq`` only), then ``VisitRoom`` and the effects.

The reader never holds the route lock and never awaits a finalize: endings
are requested (:meth:`VisitRuntime.request_finalize`) and run as a task.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from config.visit_settings import VISIT_GOODBYE_MAX_CHARS
from main_logic.visit.identity import JtiWindow, PeerBlocked, TicketRejected, verify_identity_ticket
from main_logic.visit.limits import RateChannel, channel_for
from main_logic.visit.room import IncomingLineDone, IncomingLineStart, LineRef, RoomEffects
from main_logic.visit.sanitize import clamp_peer_line, defang_markdown_media
from main_routers.visit_router.runtime_common import (
    PHASE_AWAITING,
    decode_addressee,
    finalize_reason_for_peer_leave,
    side_of_ln,
)
from utils.logger_config import get_module_logger
from utils.visit_wire import LineDeltaAssembler, decode_msg, is_reliable, proto_compatible

logger = get_module_logger(__name__, "Main")

_DISPLAY_BACKLOG_MAX = 64
_DISPLAY_QUEUE_MAX = 256
_DROPPABLE_DISPLAY_TYPES = frozenset({"visit_line_delta", "visit_typing"})
"""Queued display frames above which subtitle pieces / typing are dropped (final lines never)."""

GATE_PASS = frozenset({"hello", "ready", "leave", "hb", "ack"})
"""What the reception gate lets through before the visit is activated (§4.2)."""

_JTI_WINDOW = JtiWindow()
"""Accepted ticket ``jti`` values of this process (same room and vid may replay)."""

_REASON_MAX = 32


@dataclass
class PeerInfo:
    """The verified peer (identity ticket claims plus its ``hello`` caps)."""

    uid: str
    vid: str
    char_tag: str
    raw_display: Optional[str]
    display: str
    short_id: str
    video: bool
    lang: Optional[str]
    crop: str
    jti: str


class ReceiveMixin:
    """``on_recv`` and the per-type handlers (mixed into ``VisitRuntime``)."""

    def _init_rx(self) -> None:
        self.pending_peer_reason: Optional[str] = None
        self._pending_leave_final: Optional[str] = None
        self.gate_dropped = 0
        self._delivering: Optional[asyncio.Future] = None
        # Seal boundary: set before awaiting accepted cleanup messages; later arrivals are rejected (no sequencing or delivery).
        self._rx_closed = False
        self.binding_dropped = 0
        self.rate_dropped = 0
        self.display_dropped = 0
        self._early_effects: list = []
        self._peer_lines: dict[str, dict] = {}
        self._line_quota: dict[str, bool] = {}
        self._ln_by_key: dict[tuple, str] = {}
        self._line_frames: dict[tuple, dict] = {}
        self._deltas = LineDeltaAssembler()
        self._rejected_lines: dict[str, None] = {}
        self._lp_owner: dict[Any, str] = {}
        self._display: deque = deque()
        self._display_task: Optional[asyncio.Task] = None
        self._peer_lp: dict[str, int] = {}
        self.last_peer_goodbye = ""

    # ── Entry ─────────────────────────────────────────────────────────

    async def on_recv(self, *, from_vid: str, cmd: int, payload: dict, nbytes: int) -> None:
        """One reassembled message from the iframe (``recv``)."""
        while self._delivering is not None and not self._delivering.done():
            # Previously accepted messages still process after their receive handler was cancelled; await completion to preserve delivery order.
            # Check in a loop: among concurrent receivers, the first waking handler yields a new batch without yielding control; others await that batch.
            await asyncio.wait([self._delivering])
        if self._rx_closed:
            # Reception closed (cleanup awaits accepted messages before sealing): accept no new messages or deliveries.
            return
        now = self.clock()
        frame = self.limiter.admit_frame(from_vid, nbytes, now=now)
        if not frame.allowed:
            self.rate_dropped += 1
            if frame.sustained_overflow and self.peer is not None and from_vid == self.peer.vid:
                # Only persistent verified-peer excess ends a visit; third parties / superseded identities are rate-limited but cannot end it.
                self.request_finalize("peer_protocol_violation")
            return
        expected = self.peer.vid if self.peer is not None else None
        creds = self.creds
        if expected is None and self.side == "guest" and creds is not None:
            expected = creds.peer_vid
        if expected is not None and from_vid != expected:
            # Sender binding: discard every unverified peer vid (room third parties, superseded identities).
            self.binding_dropped += 1
            self._count_anomaly("sender_binding", streak=False)
            return
        try:
            msg = decode_msg(payload, cmd=cmd)
        except ValueError:
            self._count_anomaly("malformed")
            return
        for kind in msg.pop("_soft_anomalies", None) or ():
            # Decode normalized invalid fields and message still processes: count only, excluding consecutive violations.
            self._count_anomaly(str(kind), streak=False)
        t = msg.get("t")
        if self.peer is None and t != "hello":
            # Before verification, inspect hello only; peers resend / re-ack everything else (including ack) afterward.
            self.gate_dropped += 1
            return
        if self.finalizing and t not in ("ack", "leave") and not is_reliable(str(t)):
            # During cleanup ignore lossy messages; reliable ones still pass sequencing (advance seq, owe ack), allowing peer leave acknowledgment.
            return
        if self.peer is not None:
            self.liveness.on_peer_message(now)
        channel = channel_for(str(t), cmd)
        if channel is not None and channel is not RateChannel.TEXT:
            decision = self.limiter.admit(from_vid, channel, now=now)
            if not decision.allowed:
                self.rate_dropped += 1
                if decision.sustained_overflow:
                    self.request_finalize("peer_protocol_violation")
                return
        res = self.sequencer.accept(msg, now)
        if res.rejected:
            self._count_anomaly("ln_prefix")
        if res.violation is not None:
            self.request_finalize("peer_protocol_violation")
            return
        if res.leave is None and not any(is_reliable(str(item.get("t"))) and item.get("t") != "ack"
                                         for item in res.deliver):
            # Lossy messages / ack only: process locally without another yield; peers expect no guaranteed delivery if lost.
            await self._deliver(res, from_vid, now)
            return
        # The sequencer accepted these reliable messages (advanced contiguous watermark, deduped); peers will not resend, so cancellation mid-delivery loses them forever
        # (e.g. partially verified hello then reconnected retransmission treated as duplicate). This is a shielded runtime-owned task,
        # completed even if reception is cancelled.
        delivering = self._delivering = self.spawn(self._deliver(res, from_vid, now), name="deliver")
        await asyncio.shield(delivering)

    async def _deliver(self, res: Any, from_vid: str, now: float) -> None:
        early, self._early_effects = self._early_effects, []
        for eff in early:
            self.apply_effects(eff)
        # Process ordered deliveries before leave: the missing message preceding leave (often the last line's
        # text{final}) must enter the transcript first; dialogue processing stops after ending.
        for item in res.deliver:
            if self.finalizing and item.get("t") != "ack":
                # Whole sentences arriving during cleanup only enter transcripts and finish existing partial bubbles; no history or replies.
                # Process ack normally (leave awaits acknowledgment).
                if item.get("t") == "text":
                    await self._record_late_text(item, from_vid, now)
                continue
            await self._dispatch(item, from_vid, now)
        if res.leave is not None:
            self._on_peer_leave(res.leave, now)
        if res.leave_gap_filled and self._pending_leave_final is not None:
            if self.liveness.on_gap_filled(now) is not None:
                self.request_finalize(self._pending_leave_final, peer_reason=self.pending_peer_reason)
        if self.sequencer.ack_due(now):
            self.kick()

    def _on_early_wrap_up(self, msg: dict, now: float) -> None:
        """``wrap_up{ph:'speaking'}`` handed out ahead of a ``seq`` gap (stops the step timer)."""
        if self.room is None or self.finalizing:
            return
        # It bypassed a seq gap: treat as retransmission (range / large regression checks only), without raising reliable-message lp floor,
        # or earlier wrap_up{begin} filling the gap falsely looks out of order.
        violation = self.room.observe_lp(msg.get("lp"), reliable=True, is_retransmit=True)
        if violation is not None:
            # This early delivery bypasses _rx_wrap_up; validate lp and count violations here too.
            self._early_effects.append(self.room.violation_effects(violation))
            return
        eff = self.room.on_incoming_wrap_up(
            "speaking", str(msg.get("reason") or ""), msg.get("lp", 0), now, msg.get("ln"),
        )
        if eff.violation is None:
            # Bypasses _dispatch: this valid message still resets consecutive violations.
            self.room.record_valid_message()
        self._early_effects.append(eff)

    def _count_anomaly(self, kind: str, *, streak: bool = True) -> None:
        if self.room is not None and streak:
            self.apply_effects(self.room.record_anomaly(kind))  # apply_effects records the upload anomaly once.
            return
        self._note_journal_anomaly()
        if self.room is not None:
            self.room.anomalies_total += 1
        else:
            self.pre_room_anomalies += 1

    # ── Dispatch ─────────────────────────────────────────────────────────

    async def _dispatch(self, m: dict, from_vid: str, now: float) -> None:
        t = m.get("t")
        if t == "_unknown":
            if self.room is not None:
                self.room.record_unknown_type()
            return
        if t == "_invalid":
            self._count_anomaly(f"invalid_{m.get('raw_t')}")
            return
        if self.finalizing and t != "ack":
            return
        if not self.activated and t not in GATE_PASS:
            # Pre-reception gate: discard dialogue with separate counts, excluding consecutive anomalies so early dialogue cannot eject peers.
            self.gate_dropped += 1
            return
        handler = _HANDLERS.get(str(t))
        if handler is None:
            return
        room = self.room
        before = room.anomalies_total if room is not None else None
        await handler(self, m, from_vid, now)
        # A valid message resets consecutive counts only after processing without any anomaly; resetting first lets every subsequent
        # message clear persistent overspeed / bad-field violations, never reaching VISIT_ANOMALY_FINALIZE_COUNT.
        if room is not None and self.room is room and room.anomalies_total == before:
            room.record_valid_message()

    async def _rx_hello(self, m: dict, from_vid: str, now: float) -> None:
        if not proto_compatible(1, m.get("caps") or {}):
            # decode_msg retains only caps.proto for incompatible hello versions (new formats/identity tickets may differ),
            # so recognize it only before verification to show upgrade notices. Entering vendor rooms requires Servers-issued credentials.
            await self.status("VISIT_PROTO_MISMATCH")
            self.request_finalize("proto_mismatch")
            return
        creds = self.creds
        if creds is None:
            return
        try:
            pubkeys = await self.deps.fetch_pubkeys()
            blocklist = await self.deps.load_blocklist(self.config_dir)
            claims = verify_identity_ticket(
                m.get("ticket"), expect_visit_id=self.visit_id, expect_role=self.peer_side,
                expect_vid=from_vid, expect_transport=creds.transport, now=self.wall(),
                pubkeys=pubkeys, blocklist=blocklist, jti_window=_JTI_WINDOW,
            )
        except PeerBlocked:
            self.request_finalize("peer_blocked")
            return
        except TicketRejected as exc:
            logger.warning("visit %s: peer ticket rejected: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("peer_identity_rejected")
            return
        except Exception as exc:  # noqa: BLE001 - Unreadable public key / blocklist: fail closed.
            logger.warning("visit %s: peer ticket not verifiable: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("peer_identity_rejected")
            return
        if self.finalizing:
            return
        caps = m.get("caps") or {}
        if self.peer is not None:
            # Reconnected/reloaded peers replaying the same ticket only refresh liveness clocks.
            if claims.sub != self.peer.uid or claims.vid != self.peer.vid:
                self.request_finalize("peer_identity_rejected")
                return
            self.liveness.on_peer_verified(now)
            return
        from main_logic.visit.subjects import derive_short_code

        display = await self._clean_peer_name(claims.display_name, claims.sub)
        if self.finalizing or self.peer is not None:
            # Visit ended during local-name lookup (or another hello installed the peer): do not install or invite.
            return
        try:
            # Peers may be blocked during post-verification awaits while hooks cannot yet find them; recheck before installing,
            # after which self.peer makes them visible to hooks.
            blocked = (await self.deps.load_blocklist(self.config_dir)).is_blocked(claims.sub)
        except Exception as exc:  # noqa: BLE001 - Unreadable: fail closed, matching verification.
            logger.warning("visit %s: blocklist recheck failed: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("peer_identity_rejected")
            return
        if blocked:
            self.request_finalize("peer_blocked")
            return
        if self.finalizing or self.peer is not None:
            return
        self.peer = PeerInfo(
            uid=claims.sub, vid=claims.vid, char_tag=claims.char_tag, raw_display=claims.display_name,
            display=display,
            short_id=derive_short_code(claims.sub), video=caps.get("video") is True,
            lang=m.get("lang") if isinstance(m.get("lang"), str) else None,
            crop=caps.get("crop") if caps.get("crop") in ("upper", "full") else "upper", jti=claims.jti,
        )
        now = self.clock()  # Verification and name lookup both awaited: start the deadline at verification completion.
        self.liveness.on_peer_verified(now)
        self._set_phase(PHASE_AWAITING)
        creds = self.creds
        if self.side == "host":
            from config.visit_settings import VISIT_ACCEPT_TIMEOUT_S

            self._accept_deadline = now + VISIT_ACCEPT_TIMEOUT_S
            self._invite_frame = {
                "type": "visit_invite", "visit_id": self.visit_id, "peer_name": self.peer.display,
                "peer_short_id": self.peer.short_id, "cross_region": bool(creds and creds.cross_region),
                "expires_at": self.wall() + VISIT_ACCEPT_TIMEOUT_S,
            }
            self._post_display(dict(self._invite_frame))  # Same ordered queue as phase frames.
            # Fill peer_vid (subscription remains false: no viewing before reception).
            await self.send_media()
        else:
            await self.push(PHASE_AWAITING)

    async def _clean_peer_name(self, raw: Optional[str], uid: str) -> str:
        from main_logic.visit.sanitize import neutralize_display_name
        from main_logic.visit.subjects import derive_short_code
        from main_routers.visit_router.local_context import prompt_lang, protected_display_names

        lang = self.lang or prompt_lang()
        try:
            ctx = await self.deps.character_context()
            family = tuple(getattr(ctx, "family_names", ()) or ())
            chars = tuple(getattr(ctx, "char_names", ()) or ())
        except Exception:  # noqa: BLE001 - If local names are unreadable, rely only on generic rules.
            family, chars = (), ()
        return neutralize_display_name(
            raw, protected_names=protected_display_names(lang, family, chars),
            generic_label=self.speaker_label("peer_cat", lang), short_code=derive_short_code(uid),
        )

    async def _rx_ready(self, m: dict, from_vid: str, now: float) -> None:
        if self.side == "guest":
            await self.on_ready()
        else:
            self._count_anomaly("ready_bad_direction")

    async def _rx_ack(self, m: dict, from_vid: str, now: float) -> None:
        before = self.outbox.ack_beyond_sent
        for _seq, t in self.outbox.on_ack(m.get("seq"), now):
            if t == "hello":
                self.liveness.on_hello_acked(now)
        if self.outbox.ack_beyond_sent > before:
            self._count_anomaly("ack_beyond_sent", streak=False)

    async def _rx_hb(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is not None:
            self.apply_effects(self.room.on_incoming_hb(m.get("lp_seen"), m.get("crop"), m.get("hidden"), now))

    async def _rx_state(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is not None:
            self.apply_effects(self.room.on_incoming_state(m.get("hidden"), m.get("crop"), now))

    async def _rx_wrap_up(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is None:
            return
        if self._lp_rejected(self.room.observe_lp(m.get("lp"), reliable=True)):
            return
        ph = m.get("ph")
        if ph == "done":
            self.done_received = True
        self.apply_effects(self.room.on_incoming_wrap_up(ph, str(m.get("reason") or ""), m.get("lp"), now,
                                                         m.get("ln")))

    async def _rx_line_delta(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is None:
            return
        ln, lp = m.get("ln"), m.get("lp")
        if self._lp_rejected(self.room.observe_lp(lp, ln=ln)):
            return
        if self._lp_reused(ln, lp):
            return
        if not self._line_admitted(ln, from_vid, now):
            # This line lacks text quota: never display any part, including deltas, matching overspeed text behavior.
            return
        before = self._deltas.anomalies
        if not self._deltas.feed(m):
            # Duplicate i, finalized lines, changed-lp fragments, etc.: do not forward; count protocol anomalies once.
            if self._deltas.anomalies > before:
                self._count_anomaly("line_delta")
                if ln not in self._peer_lines:
                    self._reject_line(ln)  # New line violates on start (overlap, etc.): invalidate the whole line.
            return
        meta = self._peer_lines.get(ln)
        if m.get("i") == 0 and meta is None:
            ad_side, ad_kind = decode_addressee(m.get("ad"))
            meta = {"sp": m.get("sp"), "ad": m.get("ad"), "rt": m.get("rt") or "", "wu": bool(m.get("wu")),
                    "lp": lp}
            eff = self.room.on_incoming_start(IncomingLineStart(
                ref=LineRef(ln, lp, self.peer_side), speaker="human" if m.get("sp") == "h" else "cat",
                addressee_side=ad_side, addressee_kind=ad_kind, reply_to=self._ref_of(meta["rt"]),
                goodbye=meta["wu"],
            ), now)
            self.apply_effects(eff)
            if eff.violation is not None:
                self._reject_line(ln)  # Room rejected the line (overlap, etc.): invalidate the whole line.
                return
            self._remember_peer_line(ln, meta)
        meta = meta or {"sp": "c", "ad": None, "rt": "", "wu": False, "lp": lp}
        ad_side, ad_kind = decode_addressee(meta.get("ad"))
        self._post_display({
            "type": "visit_line_delta", "visit_id": self.visit_id, "line_id": ln, "i": m.get("i"),
            "lp": lp, "text": defang_markdown_media(str(m.get("txt") or "")),
            "speaker": self.speaker_payload(self.peer_side, "human" if meta.get("sp") == "h" else "cat"),
            "addressee": {"side": ad_side, "kind": ad_kind}, "goodbye": bool(meta.get("wu")),
            "ts": self.wall(), "paced": "audio",
        }, droppable=True)

    def _post_display(self, frame: dict, *, droppable: bool = False) -> None:
        """Queue a display frame in order without awaiting it on the receive path.

        The display socket may be backpressured (each write waits up to its
        own bound); reliable ``text`` / ``ack`` / ``leave`` must not queue up
        behind it. Over ``_DISPLAY_BACKLOG_MAX`` queued frames, droppable
        ones (subtitle pieces, typing) are dropped; final lines never are.
        """
        if self._terminated or self._ended_published:
            return  # Ended notification already sent / visit finalized: stop forwarding late frames to the page.
        if droppable and len(self._display) >= _DISPLAY_BACKLOG_MAX:
            self.display_dropped += 1
            return
        if len(self._display) >= _DISPLAY_QUEUE_MAX:
            # Slow page consumers: evict oldest lossy queued frames (subtitle fragments / typing), never whole sentences
            # (bounded by per-visit sentence caps, so cannot grow indefinitely).
            for i, queued in enumerate(self._display):
                if queued.get("type") in _DROPPABLE_DISPLAY_TYPES:
                    del self._display[i]
                    self.display_dropped += 1
                    break
        self._display.append(frame)
        if self._display_task is None or self._display_task.done():
            self._display_task = self.spawn(self._drain_display())

    async def _drain_display(self) -> None:
        while self._display:
            frame = self._display.popleft()
            try:
                await self.host.send_frame(frame)
            except Exception as exc:  # noqa: BLE001 - One unsendable frame must not block subsequent whole sentences.
                logger.warning("visit %s: display frame not sent: %s", self.visit_id[:6], type(exc).__name__)

    async def _flush_display(self, timeout: float) -> bool:
        """Wait (bounded) until the display queue has been sent to the page; False if it was not."""
        task = self._display_task
        if self._display and (task is None or task.done()):
            # Sender exited/cancelled with queued frames: restart it, preserving farewell sentences.
            task = self._display_task = self.spawn(self._drain_display())
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=timeout)
        return task is None or task.done()

    async def _retire_display(self, timeout: float) -> None:
        """Stop the display queue and wait (bounded) for the frame being written to finish."""
        task = self._display_task
        self._stop_display()
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=timeout)

    def _stop_display(self) -> None:
        """The visit ended: drop what is still queued for the page and stop sending it."""
        self._display.clear()
        task = self._display_task
        if task is not None and not task.done():
            task.cancel()

    async def _record_late_text(self, m: dict, from_vid: str, now: float) -> None:
        ln, lp = m.get("ln"), m.get("lp")
        if self.room is None or not self.activated:
            return  # Continue rejecting pre-reception dialogue, matching _dispatch's gate.
        self._deltas.close(m)  # Finalization: never display later fragments of this line.
        # Fragments opened a partial bubble before ended was sent: finalize it on-page (complete the sentence or remove it).
        show = self._peer_lines.pop(ln, None) is not None

        def drop_bubble() -> None:
            if show:
                self._post_display({"type": "visit_line_abort", "visit_id": self.visit_id, "line_id": ln,
                                    "i_done": 0, "reason": "rejected", "ts": self.wall()})

        # Same checks as before _rx_text accounting: lp range/regression, one line per lp, line text quota.
        if self._lp_rejected(self.room.observe_lp(lp, ln=ln, reliable=True, closes_line=True)):
            drop_bubble()
            return
        if self._lp_reused(ln, lp) or str(ln) in self._rejected_lines:
            return  # Neither type of line ever displayed fragments.
        sp = "human" if m.get("sp") == "h" else "cat"
        ad_side, ad_kind = decode_addressee(m.get("ad"))
        if self.room.incoming_meta_mismatch(IncomingLineDone(
            ref=LineRef(str(ln), lp, self.peer_side), truncated=m.get("truncated") is True,
            tail_ms=m.get("tail_ms", 0), goodbye=m.get("wu") is True, speaker=sp, addressee_side=ad_side,
            addressee_kind=ad_kind, reply_to=self._ref_of(m.get("rt")),
        )):
            # Finalization metadata mismatches start (same as room line_meta_mismatch): speaker/etc. are unreliable, so exclude from transcript.
            self._count_anomaly("line_meta_mismatch")
            drop_bubble()
            return
        if not self._line_admitted(ln, from_vid, now):
            return  # Same quota decision as ordinary finalization: lines overspeed on fragments (never displayed) also stay out of transcripts if completed during cleanup.
        # Cache full frame shape: GET /state replays it on page reload (recipient / reply / farewell / i_done).
        frame = self._peer_line_frame(m, str(ln), lp)
        await self.record_line(f"peer_{sp}", side=self.peer_side, lp=lp, ln=str(ln), text=str(m.get("txt") or ""),
                               truncated=m.get("truncated") is True)
        if show:
            # A partial bubble is open: complete it with the sentence (no history/reply); if ended was sent while awaiting persistence,
            # _post_display blocks it itself.
            self._post_display(frame)

    def _lp_reused(self, ln: Any, lp: Any) -> bool:
        """A second peer line claiming an ``lp`` another of its lines already holds: rejected whole.

        An honest sender allocates ``lp = max(own, seen) + 1`` per line, so two
        of its lines never share one; letting it through would give two
        transcript rows one ``(lp, side)`` identity.
        """
        key = str(ln)
        holder = self._lp_owner.get(lp)
        if holder is None:
            self._lp_owner[lp] = key
            while len(self._lp_owner) > 1024:
                self._lp_owner.pop(next(iter(self._lp_owner)))
            return False
        if holder == key:
            return False
        if key not in self._rejected_lines:
            self._count_anomaly("lp_reused")
            self._reject_line(key)
        return True

    def _reject_line(self, ln: Any) -> None:
        """Drop a peer line whole: no more deltas on screen, its ``text`` kept out of transcript and history."""
        key = str(ln)
        self._rejected_lines[key] = None
        while len(self._rejected_lines) > 256:
            self._rejected_lines.pop(next(iter(self._rejected_lines)))
        self._deltas.drop(key)

    def _lp_rejected(self, violation: Optional[str]) -> bool:
        """``observe_lp`` refused the message: record it and apply the protocol-violation cutoff."""
        if violation is None:
            return False
        self.apply_effects(self.room.violation_effects(violation))
        return True

    def _line_admitted(self, ln: Any, from_vid: str, now: float) -> bool:
        """The ``RateChannel.TEXT`` decision of one peer line, taken once (first delta, or its text)."""
        key = str(ln)
        decided = self._line_quota.get(key)
        if decided is not None:
            return decided
        decision = self.limiter.admit(from_vid, RateChannel.TEXT, now=now)
        self._line_quota[key] = decision.allowed
        while len(self._line_quota) > 256:
            self._line_quota.pop(next(iter(self._line_quota)))
        if not decision.allowed:
            self.rate_dropped += 1
            self._count_anomaly(decision.reason or "text_rate")
        return decision.allowed

    def _remember_peer_line(self, ln: str, meta: dict) -> None:
        self._peer_lines[ln] = meta
        self._peer_lp[ln] = meta.get("lp") or 0
        while len(self._peer_lines) > 256:
            self._peer_lines.pop(next(iter(self._peer_lines)))
        while len(self._peer_lp) > 1024:
            self._peer_lp.pop(next(iter(self._peer_lp)))

    def _ref_of(self, rt: Any) -> Optional[LineRef]:
        if not isinstance(rt, str) or not rt:
            return None
        side = side_of_ln(rt)
        if side is None:
            return None
        lp = self._peer_lp.get(rt) if side == self.peer_side else self.own_line_lp.get(rt)
        return LineRef(rt, int(lp or 0), side)  # type: ignore[arg-type]

    async def _rx_line_abort(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is None:
            return
        ln = m.get("ln")
        if self._deltas.closed(str(ln)):
            return  # Late/replayed abort: text already finalized this line; do not change its complete bubble to truncated.
        if self._lp_rejected(self.room.observe_lp(m.get("lp"), ln=ln)):
            return
        self._deltas.drop(str(ln))  # Stop displaying fragments after speech stops (text still finalizes normally).
        self.apply_effects(self.room.on_incoming_abort(ln, now, m.get("reason")))
        self._post_display({
            "type": "visit_line_abort", "visit_id": self.visit_id, "line_id": ln,
            "i_done": m.get("i_done"), "reason": m.get("reason"), "ts": self.wall(),
        })

    async def _rx_typing(self, m: dict, from_vid: str, now: float) -> None:
        # typing is only a UI hint: validate lp range / large regression without advancing local clocks.
        if self.room is not None and self._lp_rejected(self.room.check_lp(m.get("lp"))):
            return
        kind = "human" if m.get("sp") == "h" else "cat"
        self._post_display({
            "type": "visit_typing", "visit_id": self.visit_id,
            "speaker": self.speaker_payload(self.peer_side, kind), "on": m.get("on", True) is not False,
        }, droppable=True)

    async def _rx_stats(self, m: dict, from_vid: str, now: float) -> None:
        return None

    async def _rx_text(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is None:
            return
        ln, lp = m.get("ln"), m.get("lp")
        if self._lp_rejected(self.room.observe_lp(lp, ln=ln, reliable=True, closes_line=True)):
            # This finalization already consumed seq and was acked, so cannot recur: finalize here as _record_late_text does;
            # stop displaying fragments, remove partial bubbles, and remove room unfinished status.
            self._deltas.close(m)
            if self._peer_lines.pop(ln, None) is not None:
                self._post_display({"type": "visit_line_abort", "visit_id": self.visit_id, "line_id": ln,
                                    "i_done": 0, "reason": "rejected", "ts": self.wall()})
            self.apply_effects(self.room.on_incoming_abort(str(ln), now, "rejected"))
            return
        if self._lp_reused(ln, lp):
            self._deltas.close(m)
            return
        self._deltas.close(m)  # Finalization: never display later fragments of this line.
        if str(ln) in self._rejected_lines:
            return  # Room rejected the line on start (violation recorded): finalization also stays out of transcript/history.
        if not self._line_admitted(ln, from_vid, now):
            # Overspeed: ack already sent (prevent retries until delivery_failed), but no display/history/transcript/reply.
            return
        self.last_text_at = now
        sp = "human" if m.get("sp") == "h" else "cat"
        ad_side, ad_kind = decode_addressee(m.get("ad"))
        truncated = m.get("truncated") is True
        trunc_reason = m.get("trunc_reason") if isinstance(m.get("trunc_reason"), str) else None
        self._peer_lines.pop(ln, None)
        self._peer_lp[ln] = lp
        eff = self.room.on_incoming_done(IncomingLineDone(
            ref=LineRef(ln, lp, self.peer_side), truncated=truncated, tail_ms=m.get("tail_ms", 0),
            goodbye=m.get("wu") is True, speaker=sp, addressee_side=ad_side, addressee_kind=ad_kind,
            reply_to=self._ref_of(m.get("rt")), trunc_reason=trunc_reason,
        ), now)
        if eff.violation == "line_meta_mismatch":
            self.apply_effects(eff)  # Record anomaly and enforce cleanup for consecutive violations.
            # Fragments may already be visible: remove the bubble, leaving no permanently incomplete utterance.
            self._post_display({"type": "visit_line_abort", "visit_id": self.visit_id, "line_id": ln,
                                "i_done": 0, "reason": "rejected", "ts": self.wall()})
            return
        # Apply speech stopping, pending-reply cancellation, and cleanup immediately without awaiting disk; only replies await history insertion.
        reply, say_goodbye = eff.reply, eff.say_goodbye
        eff.reply, eff.say_goodbye = None, False
        self.apply_effects(eff)  # apply_effects records each violation as one anomaly centrally.
        txt = str(m.get("txt") or "")
        speaker_from = "peer_cat" if sp == "cat" else "peer_human"
        await self.record_line(speaker_from, side=self.peer_side, lp=lp, ln=ln, text=txt, truncated=truncated)
        self.add_peer_history(ln, lp, speaker_from, txt, truncated=truncated, trunc_reason=trunc_reason)
        if m.get("wu") is True and sp == "cat":
            # Goes into the next local prompt: sanitize again on receiving side and truncate to farewell limits (peers may ignore LineSpeaker caps).
            self.last_peer_goodbye = clamp_peer_line(txt)[:VISIT_GOODBYE_MAX_CHARS]
        self._post_display(self._peer_line_frame(m, ln, lp))
        if reply is not None or say_goodbye:
            self.apply_effects(RoomEffects(reply=reply, say_goodbye=say_goodbye))

    def _peer_line_frame(self, m: dict, ln: Any, lp: int) -> dict:
        """The ``visit_line`` frame of a peer's final ``text`` (also cached for the ``GET /state`` replay)."""
        ad_side, ad_kind = decode_addressee(m.get("ad"))
        return self.visit_line_payload(
            ln=ln, lp=lp, side=self.peer_side, kind="human" if m.get("sp") == "h" else "cat",
            ad_side=ad_side, ad_kind=ad_kind, reply_to=str(m.get("rt") or ""), goodbye=m.get("wu") is True,
            text=str(m.get("txt") or ""), truncated=m.get("truncated") is True, i_done=m.get("i_done", 0),
            trunc_reason=m.get("trunc_reason") if isinstance(m.get("trunc_reason"), str) else None,
        )

    # ── leave ────────────────────────────────────────────────────────

    def _on_peer_leave(self, msg: dict, now: float) -> None:
        raw = msg.get("reason")
        self.pending_peer_reason = raw[:_REASON_MAX] if isinstance(raw, str) else None
        self._pending_leave_final = finalize_reason_for_peer_leave(raw)
        last = msg.get("last_seq")
        if not isinstance(last, int) or isinstance(last, bool):
            last = 0
        verdict = self.liveness.on_peer_leave_message(now, last, self.sequencer.contiguous_seq)
        if verdict is not None:
            self.request_finalize(self._pending_leave_final, peer_reason=self.pending_peer_reason)

    def leave_verdict_reason(self, verdict: str) -> str:
        """A liveness ``peer_left`` that came from a pending ``leave`` keeps the leave's mapped reason."""
        if verdict == "peer_left" and self._pending_leave_final is not None:
            return self._pending_leave_final
        return verdict

    # ── Local display ─────────────────────────────────────────────────────

    def speaker_payload(self, side: str, kind: str) -> dict:
        own = side == self.side
        if kind == "cat":
            name = self.lanlan_name if own else (self.peer.display if self.peer is not None else "")
        else:
            name = self.speaker_label("own_human" if own else "peer_human")
        return {"side": side, "kind": kind, "name": name, "self": own}

    def visit_line_payload(
        self, *, ln: str, lp: int, side: str, kind: str, ad_side: Optional[str], ad_kind: Optional[str],
        reply_to: str, goodbye: bool, text: str, truncated: bool, i_done: Any = 0,
        trunc_reason: Optional[str] = None, request_id: Any = None,
    ) -> dict:
        payload = {
            "type": "visit_line", "visit_id": self.visit_id, "line_id": ln, "lp": lp,
            "speaker": self.speaker_payload(side, kind), "addressee": {"side": ad_side, "kind": ad_kind},
            "reply_to": reply_to, "goodbye": bool(goodbye), "text": defang_markdown_media(text),
            "final": True, "truncated": bool(truncated),
            "i_done": i_done if isinstance(i_done, int) and not isinstance(i_done, bool) else 0,
            "ts": self.wall(),
        }
        if trunc_reason:
            payload["trunc_reason"] = trunc_reason
        if isinstance(request_id, str) and request_id:
            # Local relative line: originating client id lets the page claim its bubble (local display frames only, never wire).
            payload["request_id"] = request_id
        # GET /state replays the same shape by (lp, side) on reload (recipient / reply / farewell / i_done / request_id included).
        self._line_frames[(lp, side)] = payload
        while len(self._line_frames) > 512:
            self._line_frames.pop(next(iter(self._line_frames)))
        return payload

    def visit_line_payload_from_record(self, record: dict) -> dict:
        """A ``visit_line``-shaped entry of ``GET /state`` from an in-memory transcript record."""
        speaker_from = str(record.get("from") or "")
        side = record.get("side") or self.side
        frame = self._line_frames.get((record.get("lp"), side))
        if frame is not None:
            return dict(frame)
        kind = "human" if speaker_from.endswith("_human") else "cat"
        return {
            "type": "visit_line", "visit_id": self.visit_id, "lp": record.get("lp"),
            # Upload records omit ln: retrieve original line_id by (lp, side), or derive a unique one if absent.
            "line_id": self._ln_by_key.get((record.get("lp"), side)) or f"{side}:{record.get('lp')}",
            "speaker": self.speaker_payload(side, kind), "text": defang_markdown_media(str(record.get("text") or "")),
            "final": True, "truncated": bool(record.get("truncated")), "ts": record.get("ts"),
        }


_HANDLERS = {
    "hello": ReceiveMixin._rx_hello,
    "ready": ReceiveMixin._rx_ready,
    "ack": ReceiveMixin._rx_ack,
    "hb": ReceiveMixin._rx_hb,
    "state": ReceiveMixin._rx_state,
    "wrap_up": ReceiveMixin._rx_wrap_up,
    "line_delta": ReceiveMixin._rx_line_delta,
    "line_abort": ReceiveMixin._rx_line_abort,
    "typing": ReceiveMixin._rx_typing,
    "stats": ReceiveMixin._rx_stats,
    "text": ReceiveMixin._rx_text,
}
