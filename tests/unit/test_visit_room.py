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

"""Unit tests of the pure visit room state machine (``main_logic/visit/room.py``).

Covers d4 section 7 items 1-17 (numbers updated to 15 / 45 s) and the
``test_visit_room.py`` list of PR-06 in the main design document. Everything
runs on a virtual clock; no event loop is involved.
"""
from __future__ import annotations

import random

import pytest

from config.visit_settings import (
    VISIT_ANOMALY_FINALIZE_COUNT,
    VISIT_INBOUND_TEXT_BURST,
    VISIT_LP_MAX,
    VISIT_LP_MAX_JUMP,
    VISIT_OUTBOX_PENDING_MAX_BYTES,
    VISIT_PIECES_MAX,
)
from main_logic.visit.room import (
    IncomingLineDone,
    IncomingLineStart,
    LineRef,
    ReplyPlan,
    RoomEffects,
    VisitRoom,
)


# ── helpers ─────────────────────────────────────────────────────────────

def _side_of(ln: str) -> str:
    return "host" if ln.startswith("h:") else "guest"


def ref(ln: str, lp: int) -> LineRef:
    return LineRef(ln, lp, _side_of(ln))  # type: ignore[arg-type]


def make_room(side: str = "host", **kw) -> VisitRoom:
    kw.setdefault("rng", random.Random(1234))
    return VisitRoom(side, **kw)  # type: ignore[arg-type]


class Peer:
    """Drives the peer half of a conversation against one room."""

    def __init__(self, room: VisitRoom) -> None:
        self.room = room
        self.side = room.peer_side
        self.prefix = "h:" if self.side == "host" else "g:"
        self.n = 0

    def new_ref(self) -> LineRef:
        self.n += 1
        lp = self.room.max_lp_seen + 1
        if lp <= self.room.own_lp:
            lp = self.room.own_lp + 1
        r = LineRef(f"{self.prefix}{self.n}", lp, self.side)  # type: ignore[arg-type]
        assert self.room.observe_lp(r.lp, ln=r.line_id) is None
        return r

    def start(self, r: LineRef, now: float, *, speaker="cat", to_kind="cat",
              reply_to=None, goodbye=False) -> RoomEffects:
        ev = IncomingLineStart(r, speaker, self.room.side, to_kind, reply_to, goodbye)
        return self.room.on_incoming_start(ev, now)

    def done(self, r: LineRef, now: float, *, speaker="cat", to_kind="cat", reply_to=None,
             goodbye=False, tail_ms=0, truncated=False,
             trunc_reason=None) -> RoomEffects:
        ev = IncomingLineDone(r, truncated, tail_ms, goodbye, speaker=speaker,
                              addressee_side=self.room.side, addressee_kind=to_kind,
                              reply_to=reply_to, trunc_reason=trunc_reason)
        return self.room.on_incoming_done(ev, now)

    def line(self, now: float, **kw) -> tuple[LineRef, RoomEffects]:
        r = self.new_ref()
        stream = kw.pop("stream", True)
        effs = []
        if stream:
            effs.append(self.start(r, now, speaker=kw.get("speaker", "cat"),
                                   to_kind=kw.get("to_kind", "cat"),
                                   reply_to=kw.get("reply_to"), goodbye=kw.get("goodbye", False)))
        effs.append(self.done(r, now + 3, **kw))
        return r, merge(effs)


class Own:
    """Drives the local cat of one room."""

    def __init__(self, room: VisitRoom) -> None:
        self.room = room
        self.prefix = "h:" if room.side == "host" else "g:"
        self.n = 0

    def new_ref(self) -> LineRef:
        self.n += 1
        return LineRef(f"{self.prefix}{self.n}", self.room.next_lp(), self.room.side)

    def line(self, now: float, *, reply_to=None, goodbye=False, dur=3.0,
             truncated=False) -> tuple[LineRef, RoomEffects]:
        r = self.new_ref()
        e1 = self.room.on_local_line_started(r, reply_to, goodbye, now)
        e2 = self.room.on_local_line_done(r, truncated, now + dur)
        return r, merge([e1, e2])

    def human(self, now: float) -> RoomEffects:
        return self.room.on_local_human_line(self.new_ref(), now)


def merge(effs: list[RoomEffects]) -> RoomEffects:
    out = RoomEffects()
    for e in effs:
        for name in vars(out):
            val = getattr(e, name)
            if name == "wrap_up":
                if val.action != "none":
                    out.wrap_up = val
            elif val not in (None, False):
                setattr(out, name, val)
    return out


def chat(room: VisitRoom, n: int, t0: float = 0.0, *, peer_first: bool = True):
    """Alternate ``n`` cat lines (peer first); returns (effects list, last time)."""
    peer, own = Peer(room), Own(room)
    effs = []
    t = t0
    last = None
    for i in range(n):
        if (i % 2 == 0) == peer_first:
            last, e = peer.line(t, reply_to=last)
        else:
            last, e = own.line(t, reply_to=last)
        effs.append(e)
        t += 10
    return effs, t, peer, own


# ── d4 section 7 items 1-17 ──────────────────────────────────────────────

def test_01_six_cat_lines_without_human_wraps_up_host_begin_guest_propose():
    for side, action in (("host", "begin"), ("guest", "propose")):
        room = make_room(side)
        effs, _, _, _ = chat(room, 6)
        assert [e.wrap_up.action for e in effs[:5]] == ["none"] * 5
        assert effs[5].wrap_up.action == action
        assert effs[5].wrap_up.reason == "quiet"
        assert effs[5].ui_state == "wrap_up"
        assert room.phase == "wrap_up"


def test_02_peer_human_line_resets_the_six_line_counter():
    room = make_room("host")
    effs, t, peer, own = chat(room, 5)
    assert room.cat_turns_since_human == 5
    peer.line(t, speaker="human")
    assert room.cat_turns_since_human == 0
    t += 10
    seen = []
    for i in range(6):
        if i % 2 == 0:
            _, e = own.line(t)
        else:
            _, e = peer.line(t)
        seen.append(e.wrap_up.action)
        t += 10
    assert seen == ["none"] * 5 + ["begin"]


def test_03_peer_all_human_cannot_lift_the_own_forty_line_cap():
    room = make_room("host")
    peer, own = Peer(room), Own(room)
    t = 0.0
    last = None
    for i in range(40):
        peer.line(t, speaker="human")
        t += 4
        _, last = own.line(t)
        t += 10
        if i < 39:
            assert last.wrap_up.action == "none"
    assert room.own_lines_total == 40
    assert last.wrap_up.action == "begin"
    assert last.wrap_up.reason == "budget"
    assert room.may_start_cat_line(t) == (False, "visit_cap")


def test_04_minute_cap_delays_the_seventh_line_until_the_window_edge():
    room = make_room("guest")
    own = Own(room)
    for i in range(6):
        own.human(i * 10.0 + 0.5)
        own.line(i * 10.0, dur=1.0)
    assert room.phase == "active"
    assert room.may_start_cat_line(55.0) == (False, "minute_cap")
    assert room.next_allowed_start(55.0) == pytest.approx(60.0)
    assert room.may_start_cat_line(61.0) == (True, "ok")


def test_05_goodbye_lines_count_toward_nothing():
    room = make_room("host")
    effs, t, peer, own = chat(room, 6)
    assert room.phase == "wrap_up"
    turns, total, starts = room.cat_turns_since_human, room.own_lines_total, len(room.own_line_starts)
    g, e = peer.line(t, goodbye=True)
    assert e.say_goodbye
    own.line(t + 5, goodbye=True)
    assert (room.cat_turns_since_human, room.own_lines_total, len(room.own_line_starts)) == \
        (turns, total, starts)
    assert room.peer_cat_lines_total == 3


def test_06_is_stale_newer_line_opening_exemption_and_abort():
    room = make_room("host")
    peer = Peer(room)
    r1, e1 = peer.line(0.0)
    plan1 = e1.reply
    assert plan1 is not None and not room.is_stale(plan1)
    r2, e2 = peer.line(1.0)
    assert e2.cancel_pending_reply and e2.reply is not None
    assert room.is_stale(plan1)
    assert not room.is_stale(e2.reply)
    # abort of the replied line drops the plan
    eff = room.on_incoming_abort(r2.line_id, 5.0)
    assert eff.cancel_pending_reply
    assert room.is_stale(e2.reply)
    # opening exemption: both sides open with rt == '' at the same time -> not a collision
    g = make_room("guest")
    own_g = Own(g)
    r = own_g.new_ref()
    g.on_local_line_started(r, None, False, 0.0)
    eff = Peer(g).start(Peer(g).new_ref(), 0.2, reply_to=None)
    assert not eff.yield_once


def test_07_local_human_interrupts_speaking_cat_or_cancels_unspoken_reply():
    room = make_room("host")
    own = Own(room)
    r = own.new_ref()
    room.on_local_line_started(r, None, False, 0.0)
    eff = own.human(1.0)
    assert eff.abort_speaking == "human_interrupt"
    room.on_local_line_done(r, True, 1.2)
    # not speaking, pending reply -> cancel
    peer = Peer(room)
    _, e = peer.line(2.0)
    assert e.reply is not None
    eff = own.human(6.0)
    assert eff.abort_speaking is None
    assert eff.cancel_pending_reply


def test_08_cat_collision_guest_yields_once_host_does_not():
    for side in ("guest", "host"):
        room = make_room(side)
        peer, own = Peer(room), Own(room)
        opener, _ = peer.line(0.0)
        mine = own.new_ref()
        room.on_local_line_started(mine, opener, False, 10.0)
        clash = peer.new_ref()
        eff = peer.start(clash, 10.3, reply_to=opener)
        assert eff.abort_speaking is None
        room.on_local_line_done(mine, False, 13.0)
        done = peer.done(clash, 14.0, reply_to=opener)
        assert done.reply is not None
        if side == "guest":
            assert eff.yield_once
            assert room.may_start_cat_line(17.0) == (False, "yield")
            assert room.may_start_cat_line(17.1) == (True, "ok")
        else:
            assert not eff.yield_once
            assert room.may_start_cat_line(17.0) == (True, "ok")


def test_09_lamport_clock_monotonic_tie_break_and_regression_is_counted():
    room = make_room("guest")
    a = room.next_lp()
    assert room.observe_lp(50, ln="h:1") is None
    b = room.next_lp()
    assert a < b and b > 50
    assert VisitRoom.sort_key(ref("h:9", 7)) < VisitRoom.sort_key(ref("g:9", 7))
    assert room.observe_lp(3000, ln="h:2") is None
    before = room.anomalies_total
    assert room.observe_lp(1500, ln="h:3") == "lp_regress"
    assert room.anomalies_total == before + 1
    assert room.phase == "active"


def test_10_full_wrap_up_sequence_host_and_guest():
    host, guest = make_room("host"), make_room("guest")
    # host begins (quiet), guest receives begin
    eh = host.on_local_recall(0.0)
    assert eh.wrap_up.action == "begin"
    eg = guest.on_incoming_wrap_up("begin", "recall", 5, 0.1)
    assert eg.say_goodbye and eg.ui_state == "wrap_up"
    # guest says goodbye exactly once
    assert guest.may_start_cat_line(0.2, goodbye=True) == (True, "ok")
    gr = LineRef("g:1", guest.next_lp(), "guest")
    es = guest.on_local_line_started(gr, None, True, 8.0)
    assert es.wrap_up.action == "speaking" and es.wrap_up.ln == "g:1"
    assert guest.on_incoming_wrap_up("begin", "recall", 5, 8.1).say_goodbye is False
    # host receives it
    host.observe_lp(gr.lp, ln=gr.line_id)
    host.on_incoming_wrap_up("speaking", "recall", gr.lp, 8.1, ln="g:1")
    guest.on_local_line_done(gr, False, 12.0)
    eh = host.on_incoming_done(IncomingLineDone(gr, False, 0, True, speaker="cat",
                                                addressee_side="host", addressee_kind="cat"), 12.1)
    assert eh.say_goodbye and eh.reply is not None and eh.reply.goodbye
    assert 13.1 <= eh.reply.not_before <= 14.6
    assert host.may_start_cat_line(14.0, goodbye=True) == (True, "ok")
    hr = LineRef("h:1", host.next_lp(), "host")
    es = host.on_local_line_started(hr, gr, True, 14.0)
    assert es.wrap_up.action == "speaking"
    ed = host.on_local_line_done(hr, False, 18.0)
    assert ed.wrap_up.action == "done"
    assert host.may_start_cat_line(18.5, goodbye=True) == (False, "wrap_up")
    ef = guest.on_incoming_wrap_up("done", "recall", hr.lp + 1, 18.2)
    assert ef.finalize_reason == "wrap_up"
    assert guest.phase == "ending"
    # host finalizes via the guest leave (liveness) or the 45 s hard cap
    assert host.on_tick(44.9).finalize_reason is None
    assert host.on_tick(45.0).finalize_reason == "wrap_up"


def test_11_begin_lost_guest_enters_wrap_up_on_host_goodbye_line():
    guest = make_room("guest")
    peer = Peer(guest)
    r = peer.new_ref()
    eff = peer.start(r, 1.0, goodbye=True)
    assert guest.phase == "wrap_up"
    assert eff.say_goodbye


def test_12_propose_timeout_guest_starts_goodbye_and_host_follows_wu_line():
    guest = make_room("guest")
    eff = guest.on_local_recall(0.0)
    assert eff.wrap_up.action == "propose"
    assert not guest.on_tick(4.9).say_goodbye
    assert guest.on_tick(5.0).say_goodbye
    assert not guest.on_tick(6.0).say_goodbye
    host = make_room("host")
    eff = Peer(host).start(Peer(host).new_ref(), 7.0, goodbye=True)
    assert host.phase == "wrap_up" and eff.ui_state == "wrap_up"


def test_13_step_and_hard_cap_timeouts():
    # host: guest goodbye never starts within 15 s -> host sees the guest off itself
    host = make_room("host")
    host.on_local_recall(0.0)
    assert not host.on_tick(14.9).say_goodbye
    eff = host.on_tick(15.0)
    assert eff.say_goodbye and eff.finalize_reason is None
    # guest: host farewell never starts within 15 s after the guest goodbye
    guest = make_room("guest")
    guest.on_incoming_wrap_up("begin", "quiet", 3, 0.0)
    gr = LineRef("g:1", guest.next_lp(), "guest")
    guest.on_local_line_started(gr, None, True, 1.0)
    guest.on_local_line_done(gr, False, 6.0)
    assert guest.on_tick(20.9).finalize_reason is None
    assert guest.on_tick(21.0).finalize_reason == "wrap_up"
    # hard cap from begin
    host2 = make_room("host")
    host2.on_local_recall(100.0)
    host2.on_incoming_wrap_up("speaking", "recall", 1, 101.0, ln="g:1")
    assert host2.on_tick(144.9).finalize_reason is None
    assert host2.on_tick(145.0).finalize_reason == "wrap_up"


def test_14_wrap_up_only_goodbye_may_start_and_old_line_aborted_after_10s():
    room = make_room("host")
    own = Own(room)
    r = own.new_ref()
    room.on_local_line_started(r, None, False, 0.0)
    room.on_local_recall(1.0)
    assert room.may_start_cat_line(2.0) == (False, "wrap_up")
    assert room.may_start_cat_line(2.0, goodbye=True) == (True, "ok")
    assert room.on_tick(10.9).abort_speaking is None
    assert room.on_tick(11.0).abort_speaking == "wrap_up"
    assert room.on_tick(11.5).abort_speaking is None


def test_15_recall_guest_proposes_host_begins_unconditionally_repeat_is_noop():
    guest, host = make_room("guest"), make_room("host")
    e = guest.on_local_recall(0.0)
    assert e.wrap_up.action == "propose" and e.wrap_up.reason == "recall"
    again = guest.on_local_recall(1.0)
    assert again == RoomEffects()
    e = host.on_incoming_wrap_up("propose", "recall", 2, 0.2)
    assert e.wrap_up.action == "begin" and e.wrap_up.reason == "recall"
    assert host.phase == "wrap_up"


def test_16_propose_and_begin_cross_one_goodbye_each():
    host, guest = make_room("host"), make_room("guest")
    eh = host.on_time_up(0.0)
    eg = guest.on_time_up(0.0)
    assert eh.wrap_up.action == "begin" and eg.wrap_up.action == "propose"
    assert host.on_incoming_wrap_up("propose", "time_up", 1, 0.1).wrap_up.action == "none"
    e = guest.on_incoming_wrap_up("begin", "time_up", 1, 0.1)
    assert e.say_goodbye
    assert not guest.on_tick(6.0).say_goodbye
    assert host.phase == guest.phase == "wrap_up"


def test_17_overlapping_lines_from_one_sender_are_a_counted_anomaly():
    # 真交叠：新开的行 lp 不比仍未收口的行大
    room = make_room("host")
    peer = Peer(room)
    a = peer.new_ref()
    peer.start(a, 0.0)
    b = LineRef(f"{peer.prefix}99", a.lp, peer.side)
    eff = peer.start(b, 1.0)
    assert eff.violation == "line_overlap"
    assert eff.finalize_reason is None
    assert room.anomalies_total == 1


def test_newer_line_start_while_previous_text_waits_behind_a_gap_is_not_overlap():
    # 上一行 text 在 seq 缺口后排队、新行首片先到：正常乱序，不是交叠
    room = make_room("host")
    peer = Peer(room)
    a = peer.new_ref()
    peer.start(a, 0.0)
    b = peer.new_ref()
    eff_b = peer.start(b, 3.0, speaker="human")
    assert eff_b.violation is None
    assert room.anomalies_total == 0
    # 旧行的 text 随后补到：元数据还在，照常收口（不当成新行）
    eff_a = room.on_incoming_done(IncomingLineDone(a, False, 0, False), 4.0)
    assert eff_a.violation is None
    eff_b2 = peer.done(b, 5.0, speaker="human")
    assert eff_b2.reply is not None and eff_b2.reply.reply_to == b


# ── main design PR-06 additions ─────────────────────────────────────────

def test_speaking_dispatch_stops_the_step_timer_only_hard_cap_remains():
    room = make_room("host")
    room.on_local_recall(0.0)
    room.on_incoming_wrap_up("speaking", "recall", 9, 10.0, ln="g:9")
    for t in (15.0, 20.0, 25.0, 44.0):
        e = room.on_tick(t)
        assert not e.say_goodbye and e.finalize_reason is None
    assert room.on_tick(45.0).finalize_reason == "wrap_up"


def test_speaking_dispatch_stops_the_guest_step_timer():
    room = make_room("guest")
    room.on_incoming_wrap_up("begin", "quiet", 1, 0.0)
    gr = LineRef("g:1", room.next_lp(), "guest")
    room.on_local_line_started(gr, None, True, 1.0)
    room.on_local_line_done(gr, False, 5.0)
    room.on_incoming_wrap_up("speaking", "quiet", 9, 10.0, ln="h:9")
    assert room.on_tick(25.0).finalize_reason is None
    assert room.on_tick(44.0).finalize_reason is None


def test_speaking_without_ln_is_an_anomaly_and_does_not_stop_the_timer():
    room = make_room("host")
    room.on_local_recall(0.0)
    eff = room.on_incoming_wrap_up("speaking", "recall", 9, 10.0)
    assert eff.violation is not None
    assert room.anomalies_total == 1
    bad_prefix = room.on_incoming_wrap_up("speaking", "recall", 9, 10.0, ln="h:3")
    assert bad_prefix.violation is not None
    assert room.on_tick(15.0).say_goodbye


def test_retransmitted_final_is_not_rejected_by_the_monotonic_check():
    room = make_room("host")
    assert room.observe_lp(9, ln="g:4") is None
    # g:5 text{final, lp=10} lost; g:6's line_delta (lossy, not reordered) observed lp=11
    assert room.observe_lp(11, ln="g:6") is None
    before = room.anomalies_total
    # g:5's final is retransmitted with lp=10 and fills the seq gap
    assert room.observe_lp(10, ln="g:5", is_retransmit=True) is None
    assert room.anomalies_total == before
    eff = room.on_incoming_done(IncomingLineDone(ref("g:5", 10), False, 0, False, speaker="cat",
                                                 addressee_side="host", addressee_kind="cat"), 3.0)
    assert eff.violation is None and eff.reply is not None
    # a line already seen (its delta arrived earlier) keeps its lp as well
    assert room.observe_lp(11, ln="g:6") is None
    assert room.anomalies_total == before


def test_new_line_lp_regression_still_counts_an_anomaly():
    room = make_room("host")
    assert room.observe_lp(11, ln="g:6") is None
    assert room.observe_lp(10, ln="g:7") == "lp_not_monotonic"
    assert room.anomalies_total == 1
    # dropped: the clock is untouched
    assert room.max_lp_seen == 11


@pytest.mark.parametrize("bad", [2 ** 53, -1, 1.5, "7", True, None])
def test_lp_value_domain_rejects_and_keeps_the_clock(bad):
    room = make_room("host")
    assert room.observe_lp(100, ln="g:1") is None
    nxt = room.own_lp
    assert room.observe_lp(bad, ln="g:2") == "lp_out_of_range"
    assert room.anomalies_total == 1
    assert room.next_lp() == max(nxt, 100) + 1


def test_lp_absolute_upper_bound_even_near_the_ceiling():
    room = make_room("host")
    room.max_lp_seen = VISIT_LP_MAX - 5  # white-box: clock already near the ceiling
    assert room.observe_lp(VISIT_LP_MAX + 1, ln="g:1") == "lp_out_of_range"
    assert room.observe_lp(VISIT_LP_MAX, ln="g:2") is None


def test_lp_forward_jump_bound():
    room = make_room("host")
    assert room.observe_lp(100, ln="g:1") is None
    assert room.observe_lp(100 + VISIT_LP_MAX_JUMP + 1, ln="g:2") == "lp_out_of_range"
    assert room.next_lp() == 101
    assert room.observe_lp(101 + VISIT_LP_MAX_JUMP, ln="g:3") is None


def test_hb_lp_seen_obeys_the_same_domain():
    room = make_room("host")
    eff = room.on_incoming_hb(2 ** 53, "upper", False, 1.0)
    assert eff.violation == "lp_out_of_range"
    assert room.next_lp() == 1
    eff = room.on_incoming_hb(VISIT_LP_MAX_JUMP + 5, "upper", False, 1.0)
    assert eff.violation == "lp_out_of_range"
    assert room.on_incoming_hb(50, "upper", False, 2.0).violation is None
    assert room.next_lp() == 51


def test_lost_hidden_state_is_corrected_by_the_next_heartbeat():
    room = make_room("host")
    # state{hidden:true} was lost; the next hb carries hidden
    eff = room.on_incoming_hb(0, "upper", True, 5.0)
    assert eff.peer_hidden is True and room.peer_hidden
    assert room.on_incoming_hb(0, "upper", True, 10.0).peer_hidden is None
    eff = room.on_incoming_hb(0, "upper", False, 15.0)
    assert eff.peer_hidden is False


def test_lost_first_state_peer_crop_is_corrected_by_the_next_heartbeat():
    room = make_room("host", peer_crop="upper")
    eff = room.on_incoming_hb(0, "full", False, 5.0)
    assert eff.peer_crop == "full" and room.peer_crop == "full"
    assert room.on_incoming_state(False, "full", 6.0).peer_crop is None
    assert room.on_incoming_state(False, "upper", 7.0).peer_crop == "upper"


def test_f13_slow_llm_three_clause_goodbye_host_does_not_see_off_early():
    host = make_room("host")
    host.on_local_recall(0.0)
    peer = Peer(host)
    g = peer.new_ref()
    # LLM 7.9 s, first goodbye piece arrives at 14.9 s
    peer.start(g, 14.9, goodbye=True)
    for t in (15.0, 18.0, 23.0):
        assert not host.on_tick(t).say_goodbye
    eff = peer.done(g, 24.0, goodbye=True)
    assert eff.say_goodbye and eff.reply.goodbye


def test_whole_line_mode_llm_8s_speak_7s_does_not_time_out():
    host = make_room("host")
    host.on_local_recall(0.0)
    # no line_delta at all; wrap_up{speaking} arrives at 8 s
    host.on_incoming_wrap_up("speaking", "recall", 3, 8.0, ln="g:1")
    for t in (14.0, 15.0, 15.5):
        e = host.on_tick(t)
        assert not e.say_goodbye and e.finalize_reason is None
    peer = Peer(host)
    g = peer.new_ref()
    eff = peer.done(g, 15.0, goodbye=True)
    assert eff.say_goodbye


@pytest.mark.parametrize("interleave", [False, True])
def test_unknown_types_never_finalize(interleave):
    room = make_room("host")
    for i in range(25):
        if interleave:
            room.record_valid_message()
        room.record_unknown_type()
    assert room.unknown_type_count == 25
    assert room.violation_streak == 0
    assert room.on_tick(1.0).finalize_reason is None
    assert room.phase == "active"


def test_known_violations_finalize_on_the_twentieth_in_a_row():
    room = make_room("host")
    for _ in range(VISIT_ANOMALY_FINALIZE_COUNT - 1):
        assert room.record_anomaly("piece_too_large").finalize_reason is None
    assert room.phase == "active"
    eff = room.record_anomaly("bad_i")
    assert eff.finalize_reason == "peer_protocol_violation"
    assert room.phase == "ending"


def test_valid_message_resets_the_streak():
    room = make_room("host")
    for _ in range(VISIT_ANOMALY_FINALIZE_COUNT - 1):
        room.record_anomaly("missing_field")
    room.record_valid_message()
    assert room.record_anomaly("missing_field").finalize_reason is None


def test_lp_regress_counts_but_does_not_finalize_immediately():
    room = make_room("host")
    room.observe_lp(5000, ln="g:1")
    assert room.observe_lp(10, ln="g:2") == "lp_regress"
    assert room.phase == "active"
    assert room.on_tick(1.0).finalize_reason is None


def test_tail_ms_out_of_range_is_treated_as_zero():
    room = make_room("host")
    peer = Peer(room)
    t = 0.0
    for i in range(25):
        r = peer.new_ref()
        eff = peer.done(r, t, tail_ms=10 ** 9)
        assert eff.violation == "tail_ms_out_of_range"
        assert eff.reply is not None
        assert 1.0 <= eff.reply.not_before - t <= 2.5
        assert room.anomalies_total == i + 1
        assert eff.finalize_reason is None
        # keep the quiet rule out of the way
        Own(room).human(t + 0.5)
        t += 10
    for bad in (-5, 3.7):
        r = peer.new_ref()
        eff = peer.done(r, t, tail_ms=bad)
        assert eff.violation == "tail_ms_out_of_range"
        assert 1.0 <= eff.reply.not_before - t <= 2.5
        t += 10
    r = peer.new_ref()
    eff = peer.done(r, t, tail_ms=12000)
    assert eff.violation is None
    assert 13.0 <= eff.reply.not_before - t <= 14.5


def test_inbound_text_bucket_and_hard_cap():
    room = make_room("host", inbound_text_max=200)
    ok = [room.admit_incoming_text(0.0)[0] for _ in range(VISIT_INBOUND_TEXT_BURST)]
    assert all(ok)
    admitted, eff = room.admit_incoming_text(0.0)
    assert not admitted and eff.violation == "inbound_text_rate"
    room.record_valid_message()
    # refills 2 per second
    assert room.admit_incoming_text(1.0)[0]
    assert room.admit_incoming_text(1.0)[0]
    assert not room.admit_incoming_text(1.0)[0]
    room.record_valid_message()
    # after a long quiet spell the burst is capped
    assert room.inbound_text_accepted == VISIT_INBOUND_TEXT_BURST + 2
    oks = sum(room.admit_incoming_text(10_000.0)[0] for _ in range(VISIT_INBOUND_TEXT_BURST + 5))
    assert oks == VISIT_INBOUND_TEXT_BURST
    room2 = make_room("host", inbound_text_max=3)
    assert all(room2.admit_incoming_text(0.0)[0] for _ in range(3))
    admitted, eff = room2.admit_incoming_text(100.0)
    assert not admitted and eff.violation == "inbound_text_cap"


def test_sustained_inbound_flood_finalizes():
    room = make_room("host")
    for _ in range(VISIT_INBOUND_TEXT_BURST):
        room.admit_incoming_text(0.0)
    effs = [room.admit_incoming_text(0.0)[1] for _ in range(VISIT_ANOMALY_FINALIZE_COUNT)]
    assert effs[-1].finalize_reason == "peer_protocol_violation"


def test_own_text_hard_limit_twenty_per_ten_seconds():
    room = make_room("host")
    own = Own(room)
    for i in range(20):
        assert room.can_accept_local_line(i * 0.1)
        own.human(i * 0.1)
    assert not room.can_accept_local_line(2.0)
    assert room.may_start_cat_line(2.0) == (False, "busy")
    assert room.can_accept_local_line(10.05)


def test_may_start_busy_when_outbox_in_flight_bytes_too_high():
    room = make_room("host")
    limit = VISIT_OUTBOX_PENDING_MAX_BYTES - VISIT_PIECES_MAX * 1024
    assert room.may_start_cat_line(0.0, outbox_pending_bytes=limit) == (True, "ok")
    assert room.may_start_cat_line(0.0, outbox_pending_bytes=limit + 1) == (False, "busy")


def test_may_start_reports_stale_plan():
    room = make_room("host")
    peer = Peer(room)
    _, e1 = peer.line(0.0)
    peer.line(1.0)
    assert room.may_start_cat_line(6.0, plan=e1.reply) == (False, "stale")


def test_max_lines_guard_counts_cat_lines_only():
    room = make_room("host", max_cat_turns_without_human=10 ** 6, own_lines_per_visit=10 ** 6)
    peer = Peer(room)
    t = 0.0
    eff = RoomEffects()
    for i in range(81):
        peer.line(t, speaker="human")
        _, eff = peer.line(t + 1)
        t += 10
        if i < 80:
            assert eff.finalize_reason is None
    assert eff.finalize_reason == "max_lines"


def test_truncated_line_gets_no_reply():
    room = make_room("host")
    peer = Peer(room)
    _, eff = peer.line(0.0, truncated=True)
    assert eff.reply is None


@pytest.mark.parametrize("reason", ["human_interrupt", "wrap_up", "visit_end", None])
def test_line_cut_off_on_purpose_gets_no_reply(reason):
    room = make_room("host")
    peer = Peer(room)
    _, eff = peer.line(0.0, truncated=True, trunc_reason=reason)
    assert eff.reply is None


@pytest.mark.parametrize("reason", ["wire_size", "tts_error", "llm_error", "stall", "goodbye_cap"])
def test_line_truncated_by_a_fault_is_still_answered(reason):
    # 对端这一轮已说完（只是没念完 / 超长被截），不回就两边都在等到 idle_timeout
    room = make_room("host")
    peer = Peer(room)
    _, eff = peer.line(0.0, truncated=True, trunc_reason=reason)
    assert eff.reply is not None
    assert not room.is_stale(eff.reply)


def test_tts_error_abort_then_text_is_answered_and_not_stale():
    room = make_room("host")
    peer = Peer(room)
    r = peer.new_ref()
    peer.start(r, 0.0)
    room.on_incoming_abort(r.line_id, 1.0, reason="tts_error")
    eff = peer.done(r, 2.0, truncated=True, trunc_reason="tts_error")
    assert eff.reply is not None
    assert not room.is_stale(eff.reply)


def test_human_interrupt_abort_marks_pending_reply_stale():
    room = make_room("host")
    peer = Peer(room)
    r, eff = peer.line(0.0)
    plan = eff.reply
    assert plan is not None
    room.on_incoming_abort(r.line_id, 4.0, reason="human_interrupt")
    assert room.is_stale(plan)


def test_reply_plan_for_line_to_my_human_is_not_created():
    room = make_room("host")
    peer = Peer(room)
    _, eff = peer.line(0.0, to_kind="human")
    assert eff.reply is None


def test_snapshot_is_plain_data():
    room = make_room("guest")
    snap = room.snapshot()
    assert snap["phase"] == "active"
    assert snap["wrap_up"]["initiated_by"] is None
    assert isinstance(ReplyPlan(ref("h:1", 1), 1.0), ReplyPlan)


def test_older_line_closing_late_does_not_steal_the_newer_reply():
    # 对端人类行先开口（lp 小）、对端猫娘行后开口却先收口：旧行晚到不能取消新行的回复
    room = make_room("host")
    peer = Peer(room)
    human = peer.new_ref()
    peer.start(human, 0.0, speaker="human")
    cat = peer.new_ref()
    peer.start(cat, 0.5)
    eff_cat = peer.done(cat, 2.0)
    assert eff_cat.reply is not None and eff_cat.reply.reply_to == cat
    eff_human = peer.done(human, 3.0, speaker="human")
    assert eff_human.reply is None
    assert not eff_human.cancel_pending_reply
    assert room.pending_reply == eff_cat.reply
    assert not room.is_stale(room.pending_reply)


def test_whole_line_mode_human_line_still_interrupts_when_newest():
    room = make_room("host")
    peer = Peer(room)
    _, eff = peer.line(0.0)
    assert eff.reply is not None
    r = peer.new_ref()
    eff2 = peer.done(r, 1.0, speaker="human")
    assert eff2.cancel_pending_reply
    assert eff2.reply is not None and eff2.reply.reply_to == r


def test_whole_line_mode_older_human_line_keeps_the_newer_reply():
    room = make_room("host")
    peer = Peer(room)
    human = peer.new_ref()            # 先开口，但整句模式下没有首片
    cat = peer.new_ref()
    peer.start(cat, 0.5)
    eff_cat = peer.done(cat, 2.0)
    eff_human = peer.done(human, 3.0, speaker="human")
    assert not eff_human.cancel_pending_reply
    assert room.pending_reply == eff_cat.reply
