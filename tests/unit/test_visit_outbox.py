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

"""Unit tests of the visit reliable outbox and inbox sequencer (``main_logic/visit/outbox.py``).

Follows the ``test_visit_outbox.py`` list of PR-06 in the main design
document. Everything runs on a virtual clock; the sender is a real
``VisitOutbox``, the receiver a small harness made of the real
``InboxSequencer`` + ``VisitLiveness`` (+ ``VisitRoom`` where the wrap-up
timer matters), connected by a lossy in-memory link.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

import pytest

from config.visit_settings import (
    VISIT_DATA_BUCKET_BPS,
    VISIT_DATA_BUCKET_BURST_BYTES,
    VISIT_MSG_BUCKET_BURST,
    VISIT_MSG_BUCKET_PER_S,
    VISIT_OUTBOX_PENDING_MAX_BYTES,
    VISIT_REORDER_BUFFER_MAX,
)
from main_logic.visit.liveness import VisitLiveness
from main_logic.visit.outbox import (
    PAUSE_PAGE_RELOAD,
    PAUSE_PEER_ABSENT,
    PAUSE_PEER_AWAY,
    PAUSE_SELF_RECONNECT,
    InboxSequencer,
    OutboundFrame,
    VisitOutbox,
    purge_outbox_files,
)
from main_logic.visit.room import VisitRoom
from utils.visit_wire import decode_msg

VID = "visitAAAAAAAAAAAAAAAAA"
TICKET = "SECRET-TICKET-abc.def"
STEP = 20  # ticks per second (50 ms)


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_outbox(tmp_path, *, peer_present: bool = True, clock: Optional[Clock] = None,
                **kw) -> VisitOutbox:
    return VisitOutbox(VID, "host", clock=clock or Clock(), spool_dir=tmp_path / "visit_spool",
                       peer_present=peer_present, **kw)


def text(n: int, txt: str = "hello", side: str = "h", **extra) -> dict:
    msg = {"t": "text", "ln": f"{side}:{n}", "lp": n, "sp": "c", "ad": "gc", "rt": "",
           "wu": False, "final": True, "txt": txt, "truncated": False, "i_done": 0}
    msg.update(extra)
    return msg


def hello() -> dict:
    return {"t": "hello", "ticket": TICKET,
            "caps": {"video": True, "tier": "sd600", "proto": 1, "app_version": "1.0",
                     "crop": "upper"},
            "lang": "zh-CN"}


def delta(ln: str, txt: str, *, first: bool = False, lp: int = 1) -> dict:
    msg = {"t": "line_delta", "ln": ln, "lp": lp, "txt": txt}
    if first:
        msg.update({"sp": "c", "ad": "gc", "rt": "", "wu": False})
    return msg


def ticks(start: float, end: float):
    """Virtual times ``start, start + 50 ms, ...`` up to and including ``end``."""
    for k in range(round(start * STEP), round(end * STEP) + 1):
        yield k / STEP


class Receiver:
    """Receive-side harness wired like the runtime: sequencer -> liveness / room / history."""

    def __init__(self, *, peer_prefix: str = "h:", room: Optional[VisitRoom] = None,
                 early_all: bool = False) -> None:
        self.inbox = InboxSequencer(on_early=self._on_early, peer_ln_prefix=peer_prefix)
        self.liveness = VisitLiveness("guest" if peer_prefix == "h:" else "host", 0.0)
        self.liveness.on_peer_verified(0.0)
        self.room = room
        self.history: list[tuple[int, str, str]] = []   # 入史 / spool / 转录（同序）
        self.early: list[dict] = []
        self.unknown = 0
        self.now = 0.0

    def _on_early(self, msg: dict, now: float) -> None:
        self.early.append(msg)
        if msg.get("t") == "text":   # 只在「全部提前投递」的变异下会走到
            self.history.append((msg["seq"], msg["ln"], msg["txt"]))
        elif self.room is not None:
            self.room.on_incoming_wrap_up(msg["ph"], msg["reason"], msg["lp"], now,
                                          ln=msg.get("ln"))

    def feed(self, payload: dict, now: float, cmd: Optional[int] = None):
        self.now = now
        msg = decode_msg(payload, cmd=cmd)
        self.liveness.on_peer_message(now)
        res = self.inbox.accept(msg, now)
        for m in res.deliver:
            if m["t"] == "text":
                self.history.append((m["seq"], m["ln"], m["txt"]))
            elif m["t"] == "_unknown":
                self.unknown += 1
                if self.room is not None:
                    self.room.record_unknown_type()
        if res.leave is not None:
            self.liveness.on_peer_leave_message(now, res.leave["last_seq"],
                                                self.inbox.contiguous_seq)
        if res.leave_gap_filled:
            self.liveness.on_gap_filled(now)
        return res


def pump(sender: VisitOutbox, rx: Receiver, now: float,
         drop: Optional[Callable[[OutboundFrame, float], bool]] = None) -> list[OutboundFrame]:
    """One step of the link: release frames, deliver the surviving ones, return the ack."""
    frames = sender.due(now)
    for f in frames:
        if drop is not None and drop(f, now):
            continue
        rx.feed(f.payload, now, cmd=f.cmd)
    ack = rx.inbox.poll_ack(now)
    if ack is not None:
        sender.on_ack(ack, now)
    return frames


# ── leave 前的缺口补齐 ─────────────────────────────────────────────────

def _leave_scenario(tmp_path, drop_seq9_until: float, end: float):
    clock = Clock()
    tx = make_outbox(tmp_path, clock=clock)
    rx = Receiver()
    for n in range(1, 9):
        tx.send(text(n), now=0.0)
    pump(tx, rx, 0.0)
    assert rx.inbox.contiguous_seq == 8 and tx.unacked_seqs == []
    assert tx.send(text(9, "last line"), now=0.5) == 9
    sent9: list[float] = []
    verdict_at: Optional[float] = None
    tick_at_leave = "unset"

    def drop(f: OutboundFrame, t: float) -> bool:
        if f.seq == 9:
            sent9.append(t)
            return t < drop_seq9_until
        return False

    for t in ticks(0.5, end):
        if t == 16.0:
            seq = tx.send({"t": "leave", "reason": "home"}, now=t)
            assert seq == 10
        pump(tx, rx, t, drop)
        if t == 16.0:
            tick_at_leave = rx.liveness.tick(t)
        if verdict_at is None and rx.liveness.tick(t) == "peer_left":
            verdict_at = t
    return tx, rx, sent9, verdict_at, tick_at_leave


def test_leave_fills_the_gap_before_finalize(tmp_path):
    tx, rx, sent9, verdict_at, tick_at_leave = _leave_scenario(tmp_path, 17.5, 25.0)
    assert tick_at_leave is None          # 缺口没补齐前不结束
    assert rx.history[-1] == (9, "h:9", "last line")   # 最后一行进了历史
    assert verdict_at == 18.0             # 补齐即结束
    assert tx.leave_done(18.0)            # leave 的 ack 已到
    # 发 leave 时 seq 9 的退避已在 8 s 档（下一次常规重传 23.5 s，落在 5 s 窗口外）
    assert sent9[:5] == [0.5, 1.5, 3.5, 7.5, 15.5]
    # 发 leave 的同时立即重发，之后每 1 s 重发
    assert sent9[5:8] == [16.0, 17.0, 18.0]


def test_leave_gap_grace_expires_when_never_filled(tmp_path):
    tx, rx, _sent9, verdict_at, tick_at_leave = _leave_scenario(tmp_path, 1e9, 30.0)
    assert tick_at_leave is None
    assert verdict_at == 21.0             # VISIT_LEAVE_GAP_GRACE_S 到期
    assert all(ln != "h:9" for _s, ln, _t in rx.history)
    assert rx.inbox.leave_waiting(9)
    assert tx.leave_done(21.0)
    assert tx.due(21.0) == []


def test_leave_payload_and_no_reliable_after_leave(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(text(1), now=0.0)
    tx.send({"t": "leave", "reason": "home", "last_seq": 77}, now=0.0)
    frames = tx.due(0.0)
    leave = [f for f in frames if f.t == "leave"][0]
    assert leave.payload["seq"] == 2 and leave.payload["last_seq"] == 1
    with pytest.raises(ValueError):
        tx.send(text(2), now=0.1)
    tx.send({"t": "ack", "seq": 3}, now=0.1)   # 控制消息照发


def test_drain_before_leave(tmp_path):
    tx = make_outbox(tmp_path)
    rx = Receiver()
    tx.send(text(1), now=0.0)
    tx.due(0.0)                                  # 首发丢了
    deadline = tx.begin_drain(0.0)
    assert deadline == 2.0
    assert not tx.drain_done(0.5)
    pump(tx, rx, 1.0)                            # 1 s 重传送达并被 ack
    assert tx.drain_done(1.0)
    assert tx.send({"t": "leave", "reason": "home"}, now=1.0) == 2

    tx2 = make_outbox(tmp_path)
    tx2.send(text(1), now=0.0)
    tx2.due(0.0)
    tx2.begin_drain(0.0)
    assert not tx2.drain_done(1.95)
    assert tx2.drain_done(2.0)                   # 最多等 2 s


# ── 未知类型 / 票据 / 落盘 ─────────────────────────────────────────────

def test_unknown_reliable_type_advances_the_window():
    room = VisitRoom("guest")
    rx = Receiver(room=room)
    for n in range(1, 5):
        rx.feed(text(n, seq=n), 0.0, cmd=2)
    res5 = rx.feed({"t": "future_thing", "v": 1, "seq": 5, "x": 1}, 1.0, cmd=1)
    assert [m["t"] for m in res5.deliver] == ["_unknown"]
    res6 = rx.feed(text(6, "after unknown", seq=6), 1.1, cmd=2)
    assert [m["t"] for m in res6.deliver] == ["text"]
    assert rx.inbox.poll_ack(1.1) == 6
    assert rx.history[-1] == (6, "h:6", "after unknown")
    assert room.unknown_type_count == 1
    assert rx.inbox.unknown_consumed == 1


async def _read_lines(path) -> list[dict]:
    raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line]


async def test_hello_ticket_never_persisted(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(hello(), now=0.0)
    tx.send(text(1), now=0.0)
    first = tx.due(0.0)
    assert [f.t for f in first] == ["hello", "text"]
    await tx.flush()
    raw = await asyncio.to_thread(tx.path.read_text, encoding="utf-8")
    assert TICKET not in raw
    assert [rec["t"] for rec in await _read_lines(tx.path)] == ["text"]
    # 页面重载：后端从内存重发 hello，票据与原来相同
    tx.pause(1.0, PAUSE_PAGE_RELOAD)
    assert tx.due(1.5) == []
    assert tx.resume(1.5, PAUSE_PAGE_RELOAD)
    again = tx.due(1.5)
    hello_again = [f for f in again if f.t == "hello"]
    assert hello_again and hello_again[0].payload["ticket"] == TICKET
    assert hello_again[0].seq == first[0].seq and hello_again[0].retransmit
    # 已 ack 之后重连，同样从内存重发同一 hello
    tx.on_ack(2, 2.0)
    assert tx.resend_hello(3.0)
    assert tx.resend_hello(3.0) and tx.resend_hello(3.0)   # 连续重连只留一份待发
    resent = tx.due(3.0)
    assert [f.payload["ticket"] for f in resent if f.t == "hello"] == [TICKET]
    await tx.close()


async def test_jsonl_holds_only_reliable_messages(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(hello(), now=0.0)
    tx.send(text(1), now=0.0)
    tx.send({"t": "ack", "seq": 3}, now=0.0)
    tx.send({"t": "hb", "lp_seen": 1, "crop": "upper", "hidden": False}, now=0.0)
    tx.send(delta("h:2", "piece", first=True), now=0.0)
    tx.send({"t": "typing", "lp": 2, "sp": "c"}, now=0.0)
    tx.send({"t": "wrap_up", "lp": 3, "ph": "begin", "reason": "quiet",
             "initiated_by": "host"}, now=0.0)
    await tx.flush()
    recs = await _read_lines(tx.path)
    assert [(r["seq"], r["t"]) for r in recs] == [(2, "text"), (3, "wrap_up")]
    assert recs[0]["payload"]["txt"] == "hello"
    await tx.close()
    assert not await asyncio.to_thread(tx.path.exists)


async def test_purge_outbox_files_on_startup(tmp_path):
    spool = tmp_path / "visit_spool"
    spool.mkdir()
    keep = ["x.jsonl", f"{VID}.jsonl", f"{VID}.state.json", "bad.outbox.jsonl"]
    for name in keep + [f"{VID}.outbox.jsonl"]:
        (spool / name).write_text("{}\n", encoding="utf-8")
    deleted = await purge_outbox_files(spool)
    assert [p.name for p in deleted] == [f"{VID}.outbox.jsonl"]
    assert sorted(p.name for p in spool.iterdir()) == sorted(keep)
    assert await purge_outbox_files(tmp_path / "missing") == []


# ── ack 与重传 ────────────────────────────────────────────────────────

def test_cumulative_ack_clears_every_item_up_to_seq(tmp_path):
    tx = make_outbox(tmp_path)
    for n in range(1, 6):
        tx.send(text(n), now=0.0)
    tx.due(0.0)
    assert tx.on_ack(3, 0.1) == [(1, "text"), (2, "text"), (3, "text")]
    assert tx.unacked_seqs == [4, 5]
    assert tx.on_ack(2, 0.2) == []
    assert tx.on_ack(99, 0.3) == [(4, "text"), (5, "text")]
    assert tx.pending_bytes == 0


def test_ack_cannot_release_items_that_were_never_sent(tmp_path):
    # 对端提前发来越界的累计 ack：未发出的 text / leave 不能被当成已确认
    tx = make_outbox(tmp_path, peer_present=False)
    tx.send(text(1), now=0.0)
    tx.send({"t": "leave", "reason": "ended"}, now=0.0)
    assert tx.due(0.0) == []            # 对端未入房：什么都没发
    assert tx.on_ack(99, 0.1) == []
    assert tx.unacked_seqs == [1, 2]
    assert tx.ack_beyond_sent == 1
    assert not tx.leave_done(0.2)


def test_ack_past_the_sent_prefix_releases_only_the_sent_part(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(text(1), now=0.0)
    assert [f.seq for f in tx.due(0.0)] == [1]
    tx.send(text(2), now=0.5)           # 入队但还没 due() 发出
    assert tx.on_ack(2, 0.6) == [(1, "text")]
    assert tx.unacked_seqs == [2]
    assert tx.ack_beyond_sent == 1


def test_retransmit_schedule_then_every_eight_seconds(tmp_path):
    tx = make_outbox(tmp_path, delivery_timeout_s=10_000)
    tx.send(text(1), now=0.0)
    sent = [t for t in ticks(0.0, 60.0) for f in tx.due(t) if f.seq == 1]
    assert sent == [0.0, 1.0, 3.0, 7.0, 15.0, 23.0, 31.0, 39.0, 47.0, 55.0]


def test_delivery_failed_after_30s_even_with_heartbeats(tmp_path):
    tx = make_outbox(tmp_path)
    rx = Receiver()
    tx.send(text(1), now=0.0)
    for t in ticks(0.0, 31.0):
        if t % 5 == 0:
            tx.send({"t": "hb", "lp_seen": 1, "crop": "upper", "hidden": False}, now=t)
        pump(tx, rx, t, drop=lambda f, _t: f.seq == 1)
        if t < 30.0:
            assert not tx.delivery_failed, t
        assert rx.liveness.tick(t) is None      # 心跳照常，对端不会判死
    assert tx.delivery_failed and tx.failed_seq == 1


def test_self_reconnect_pauses_the_delivery_timer(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(text(1), now=0.0)
    for t in ticks(0.0, 15.0):
        tx.due(t)
    tx.pause(15.0, PAUSE_SELF_RECONNECT)
    for t in ticks(15.0, 34.0):
        assert tx.due(t) == []
        assert not tx.delivery_failed
    tx.resume(34.0, PAUSE_SELF_RECONNECT)
    resent = tx.due(34.0)
    assert [(f.seq, f.retransmit) for f in resent] == [(1, True)]
    for t in ticks(34.05, 48.95):
        tx.due(t)
        assert not tx.delivery_failed, t
    tx.due(49.0)
    assert tx.delivery_failed


def test_host_waits_for_guest_hello_first_sent_on_join(tmp_path):
    tx = make_outbox(tmp_path, peer_present=False)
    rx = Receiver(peer_prefix="h:")
    tx.send(hello(), now=0.0)
    for t in ticks(0.0, 119.95):
        assert tx.due(t) == []
    assert not tx.delivery_failed
    tx.resume(120.0, PAUSE_PEER_ABSENT)
    first = tx.due(120.0)
    assert [(f.t, f.retransmit) for f in first] == [("hello", False)]
    rx.feed(first[0].payload, 120.2, cmd=first[0].cmd)
    tx.on_ack(rx.inbox.poll_ack(120.2), 120.2)
    for t in ticks(120.2, 200.0):
        tx.due(t)
    assert not tx.delivery_failed and tx.unacked_seqs == []


def test_peer_tentative_leave_pauses_the_delivery_timer(tmp_path):
    tx = make_outbox(tmp_path)
    rx = Receiver()
    tx.send(text(1), now=10.0)
    tx.due(10.0)                                  # 首发丢失
    tx.pause(12.0, PAUSE_PEER_AWAY)               # 对端暂定离开
    for t in ticks(12.0, 45.0):
        assert tx.due(t) == []
        assert not tx.delivery_failed
    tx.resume(45.0, PAUSE_PEER_AWAY)              # 33 s 后重入
    for t in ticks(45.0, 80.0):
        pump(tx, rx, t)
    assert rx.history == [(1, "h:1", "hello")]
    assert not tx.delivery_failed and tx.unacked_seqs == []


def test_pause_reasons_are_independent(tmp_path):
    tx = make_outbox(tmp_path)
    tx.pause(1.0, PAUSE_PAGE_RELOAD)
    tx.pause(2.0, PAUSE_PEER_AWAY)
    assert not tx.resume(3.0, PAUSE_PAGE_RELOAD)
    assert tx.paused
    assert tx.resume(4.0, PAUSE_PEER_AWAY)
    assert tx.active_time(5.0) == pytest.approx(2.0)


def test_ack_only_advances_to_the_contiguous_seq(tmp_path):
    tx = make_outbox(tmp_path)
    rx = Receiver()
    for n in range(1, 4):
        tx.send(text(n), now=0.0)
    pump(tx, rx, 0.0, drop=lambda f, t: f.seq == 2)
    assert tx.unacked_seqs == [2, 3]            # 回的是 ack{1}，2 仍在 outbox
    assert [s for s, _ln, _t in rx.history] == [1]
    pump(tx, rx, 1.0)                            # 2、3 的 1 s 重传
    assert [s for s, _ln, _t in rx.history] == [1, 2, 3]
    assert tx.unacked_seqs == []


def test_replay_after_reload_resends_only_unacked(tmp_path):
    tx = make_outbox(tmp_path)
    for n in range(1, 4):
        tx.send(text(n), now=0.0)
    tx.due(0.0)
    tx.on_ack(1, 0.1)
    assert tx.replay_after_reload(0.5) == 2
    assert [(f.seq, f.retransmit) for f in tx.due(0.5)] == [(2, True), (3, True)]
    tx.pause(0.6, PAUSE_PAGE_RELOAD)
    tx.on_ack(2, 0.7)
    tx.resume(5.0, PAUSE_PAGE_RELOAD)
    assert [f.seq for f in tx.due(5.0)] == [3]


# ── 接收侧按序 ────────────────────────────────────────────────────────

def test_in_order_effect_after_gap():
    rx = Receiver()
    rx.feed(text(1, "one", seq=1), 0.0, cmd=2)
    res = rx.feed(text(3, "three", seq=3), 0.1, cmd=2)
    assert res.deliver == [] and rx.inbox.buffered == 1
    assert [s for s, _l, _t in rx.history] == [1]
    res = rx.feed(text(2, "two", seq=2), 0.2, cmd=2)
    assert [m["seq"] for m in res.deliver] == [2, 3]
    assert rx.history == [(1, "h:1", "one"), (2, "h:2", "two"), (3, "h:3", "three")]
    assert rx.inbox.poll_ack(0.2) == 3


def test_reorder_buffer_overflow_is_a_protocol_violation():
    inbox = InboxSequencer()
    for n in range(2, 2 + VISIT_REORDER_BUFFER_MAX):
        res = inbox.accept(decode_msg(text(n, seq=n)), 0.0)
        assert res.violation is None
    assert inbox.buffered == VISIT_REORDER_BUFFER_MAX
    n = 2 + VISIT_REORDER_BUFFER_MAX
    res = inbox.accept(decode_msg(text(n, seq=n)), 0.0)
    assert res.violation == "peer_protocol_violation"
    assert inbox.accept(decode_msg(text(1, seq=1)), 0.1).violation == "peer_protocol_violation"


def test_lossy_messages_bypass_the_reorder_buffer():
    rx = Receiver()
    rx.feed(text(1, seq=1), 0.0, cmd=2)
    rx.feed(text(3, seq=3), 0.0, cmd=2)          # 缺 2
    res = rx.feed({**delta("h:4", "live", first=True), "i": 0}, 0.1, cmd=2)
    assert [m["t"] for m in res.deliver] == ["line_delta"]
    assert rx.inbox.poll_ack(0.1) == 1


def test_duplicate_seq_or_ln_only_acks():
    rx = Receiver()
    assert len(rx.feed(text(1, "a", seq=1), 0.0, cmd=2).deliver) == 1
    assert rx.inbox.poll_ack(0.0) == 1
    res = rx.feed(text(1, "a", seq=1), 1.0, cmd=2)
    assert res.duplicate and res.deliver == []
    assert rx.inbox.poll_ack(1.0) == 1           # 重复也回 ack
    res = rx.feed(text(1, "a again", seq=2), 2.0, cmd=2)   # 同一 ln 换了 seq
    assert res.duplicate and res.deliver == []
    assert rx.inbox.poll_ack(2.0) == 2
    assert rx.history == [(1, "h:1", "a")]


def test_ack_is_coalesced_within_the_window():
    inbox = InboxSequencer()
    inbox.accept(decode_msg(text(1, seq=1)), 0.0)
    assert inbox.poll_ack(0.0) == 1
    inbox.accept(decode_msg(text(2, seq=2)), 0.1)
    inbox.accept(decode_msg(text(3, seq=3)), 0.2)
    assert inbox.poll_ack(0.3) is None
    assert inbox.poll_ack(0.5) == 3
    assert inbox.poll_ack(2.0) is None


def test_ln_prefix_mismatch_consumes_seq_as_invalid():
    rx = Receiver(peer_prefix="h:")
    res = rx.feed(text(1, "spoof", side="g", seq=1), 0.0, cmd=2)
    assert [m["t"] for m in res.deliver] == ["_invalid"]
    assert rx.inbox.contiguous_seq == 1 and rx.history == []
    assert len(rx.feed(text(1, "real", side="h", seq=2), 0.1, cmd=2).deliver) == 1


def _speaking(seq: int, ln: str = "g:4") -> dict:
    return {"t": "wrap_up", "v": 1, "seq": seq, "lp": 10, "ph": "speaking", "ln": ln,
            "reason": "time_up", "initiated_by": "host"}


def test_wrap_up_speaking_is_not_blocked_by_a_seq_gap():
    room = VisitRoom("host")
    room.on_time_up(0.0)
    room.on_wrap_up_sent("begin", 0.0)            # host 的 begin 已发出，15 s 步进计时开始
    assert room.wrap_up.step_started_at == 0.0
    rx = Receiver(peer_prefix="g:", room=room)
    rx.feed(text(1, "g one", side="g", seq=1), 0.0, cmd=2)
    rx.feed(text(2, "g two", side="g", seq=2), 0.0, cmd=2)
    # seq 3（text）首发丢失；seq 4 是 speaking
    speaking = rx.feed(_speaking(4), 1.0, cmd=1)
    assert rx.inbox.poll_ack(1.0) == 2            # ack 仍停在 N-1
    gap_text = rx.feed(text(5, "g five", side="g", seq=5), 1.5, cmd=2)
    eff = room.on_tick(16.0)                      # 不因 15 s 步进而收尾
    assert not eff.say_goodbye and eff.finalize_reason is None
    assert not room.wrap_up.step_expired and room.wrap_up.step_stopped
    # 同样缺口下的其它必达消息仍被缓存、不提前生效
    assert [s for s, _l, _t in rx.history] == [1, 2]
    assert gap_text.deliver == []
    res = rx.feed(text(3, "g three", side="g", seq=3), 17.0, cmd=2)
    assert [s for s, _l, _t in rx.history] == [1, 2, 3, 5]
    assert [m["seq"] for m in res.deliver] == [3, 5]   # 4 是 no-op
    assert rx.inbox.poll_ack(17.0) == 5
    res = rx.feed(_speaking(4), 17.5, cmd=1)       # 同 seq 重传只回 ack
    assert res.duplicate
    assert speaking.early is not None and len(rx.early) == 1   # 只提前投递一次
    assert room.on_tick(44.0).finalize_reason is None
    assert room.on_tick(45.0).finalize_reason == "wrap_up"   # 只剩 45 s 硬顶


# ── 限速与合并 ────────────────────────────────────────────────────────

def test_full_bucket_queues_retransmits_without_dropping(tmp_path):
    tx = make_outbox(tmp_path, data_bps=100, data_burst_bytes=2000)
    body = "x" * 700
    tx.send(text(1, body), now=0.0)
    tx.send(text(2, body), now=0.0)
    first = tx.due(0.0)
    assert [f.seq for f in first] == [1, 2]
    size = first[0].nbytes
    assert 2000 - 2 * size < size                # 桶里已不够一条
    seen: list[tuple[float, int]] = []
    for t in ticks(0.05, 40.0):
        for f in tx.due(t):
            assert f.retransmit
            seen.append((t, f.seq))
    assert seen, "retransmits must eventually leave"
    assert seen[0][0] > 1.0                       # 1 s 到期时桶满：不吐
    assert [s for _t, s in seen[:2]] == [1, 2]    # 余量恢复后按序吐
    assert tx.unacked_seqs == [1, 2]              # 排队不丢


def test_message_bucket_and_byte_bucket_cap_independently(tmp_path):
    tx = make_outbox(tmp_path)
    for _ in range(100):
        tx.send({"t": "typing", "lp": 1, "sp": "c"}, now=0.0)
    count = sum(len(tx.due(t)) for t in ticks(0.0, 1.0))
    assert count <= VISIT_MSG_BUCKET_BURST + VISIT_MSG_BUCKET_PER_S
    assert count >= VISIT_MSG_BUCKET_BURST + VISIT_MSG_BUCKET_PER_S - 1

    tx2 = make_outbox(tmp_path, delivery_timeout_s=10_000)
    for n in range(1, 31):
        tx2.send(text(n, "y" * 3800), now=0.0)
    total = 0
    pieces = 0
    for t in ticks(0.0, 10.0):
        for f in tx2.due(t):
            total += f.nbytes
            pieces += f.pieces
    cap = VISIT_DATA_BUCKET_BURST_BYTES + VISIT_DATA_BUCKET_BPS * 10
    assert total <= cap
    assert total >= cap - 5000                    # 字节桶确实是瓶颈
    assert pieces < VISIT_MSG_BUCKET_BURST + VISIT_MSG_BUCKET_PER_S * 10


def test_line_delta_merge_assigns_contiguous_i_and_i_done(tmp_path):
    tx = make_outbox(tmp_path)
    sent: list[OutboundFrame] = []

    def step(t: float) -> None:
        sent.extend(tx.due(t))

    tx.send(delta("h:1", "p0", first=True), now=0.0)
    step(0.0)
    tx.send(delta("h:1", "p1"), now=0.1)
    step(0.1)
    tx.send(delta("h:1", "p2"), now=0.2)          # <250 ms：并进 p1
    step(0.2)
    step(0.25)
    tx.send(delta("h:1", "p3"), now=0.6)
    step(0.6)
    tx.send(delta("h:1", "p4"), now=0.65)
    step(0.65)
    tx.send(delta("h:1", "p5"), now=0.7, final_piece=True)   # 末片不合并
    tx.send(text(1, "p0p1p2p3p4p5"), now=0.7)
    for t in ticks(0.7, 2.0):
        step(t)
    deltas = [f for f in sent if f.t == "line_delta"]
    assert [f.payload["i"] for f in deltas] == [0, 1, 2, 3, 4]
    assert [f.payload["txt"] for f in deltas] == ["p0", "p1p2", "p3", "p4", "p5"]
    assert "sp" in deltas[0].payload and all("sp" not in f.payload for f in deltas[1:])
    final = [f for f in sent if f.t == "text"]
    assert len(final) == 1 and sent[-1] is final[0]          # text 在本行末片之后
    assert final[0].payload["i_done"] == len(deltas) == tx.line_pieces("h:1")


def test_line_delta_min_interval_between_pieces(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(delta("h:1", "a", first=True), now=0.0)
    emitted: list[float] = []
    for k, t in enumerate(ticks(0.0, 3.0)):
        if k % 2 == 1 and t < 2.0:
            tx.send(delta("h:1", "b" * 300), now=t)      # 每 100 ms 一片，合并后 >900 B 则不合并
        emitted.extend(t for f in tx.due(t) if f.t == "line_delta")
    gaps = [b - a for a, b in zip(emitted, emitted[1:])]
    assert gaps and min(gaps) >= 0.25 - 1e-9


def test_stale_lossy_backlog_is_dropped_and_i_done_stays_exact(tmp_path):
    tx = make_outbox(tmp_path, peer_present=False)
    tx.send(delta("h:1", "old", first=True), now=0.0)
    tx.send({"t": "typing", "lp": 1, "sp": "c"}, now=0.0)
    tx.resume(11.0, PAUSE_PEER_ABSENT)
    assert tx.due(11.0) == []                    # 积压 >10 s 的可丢类作废
    tx.send(delta("h:1", "later"), now=11.0)      # 本行剩余 delta 一律作废
    tx.send(text(1, "oldlater"), now=11.0)
    frames = tx.due(11.0)
    assert [f.t for f in frames] == ["text"]
    assert frames[0].payload["i_done"] == 0


def test_backpressure_pauses_lossy_but_not_text(tmp_path):
    tx = make_outbox(tmp_path)
    tx.set_backpressure(True)
    assert tx.send(delta("h:1", "a", first=True), now=0.0) == 0
    assert tx.send({"t": "typing", "lp": 1, "sp": "c"}, now=0.0) == 0
    assert tx.send(text(2), now=0.0) == 1
    assert [f.t for f in tx.due(0.0)] == ["text"]
    tx.set_backpressure(False)
    assert tx.send(delta("h:1", "b"), now=0.1) == 0       # 本行剩余片作废，不报错
    tx.send(text(1, "ab"), now=0.1)
    frames = tx.due(0.1)
    assert [f.t for f in frames] == ["text"] and frames[0].payload["i_done"] == 0


def test_coalesced_control_messages_keep_only_the_latest(tmp_path):
    tx = make_outbox(tmp_path, peer_present=False)
    tx.send({"t": "ack", "seq": 1}, now=0.0)
    tx.send({"t": "ack", "seq": 4}, now=0.1)
    tx.resume(0.2, PAUSE_PEER_ABSENT)
    assert [f.payload for f in tx.due(0.2)] == [{"t": "ack", "v": 1, "seq": 4}]


def test_pending_bytes_and_try_reserve(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(text(1, "z" * 2000), now=0.0)
    used = tx.pending_bytes
    pieces, size = tx.encoded_size(text(1, "z" * 2000))
    assert used > 2000 and size >= used and pieces >= 2
    assert tx.try_reserve(VISIT_OUTBOX_PENDING_MAX_BYTES - used)
    assert not tx.try_reserve(VISIT_OUTBOX_PENDING_MAX_BYTES - used + 1)
    tx.due(0.0)
    tx.on_ack(1, 0.1)
    assert tx.pending_bytes == 0


def test_frame_to_ws_shape(tmp_path):
    tx = make_outbox(tmp_path)
    tx.send(text(1), now=0.0)
    frame = tx.due(0.0)[0]
    ws = frame.to_ws()
    assert ws["type"] == "send" and ws["cmd"] == 2 and ws["payload"]["seq"] == 1
    ws["payload"]["txt"] = "mutated"
    assert tx.due(1.0)[0].payload["txt"] == "hello"


def test_a_line_emits_at_most_255_pieces_so_i_done_fits_the_schema(tmp_path):
    tx = make_outbox(tmp_path, delivery_timeout_s=10_000)
    sent = 0
    t = 0.0
    for n in range(300):
        tx.send(delta("h:1", f"{n},", first=(n == 0)), now=t, final_piece=True)
        sent += sum(1 for f in tx.due(t) if f.t == "line_delta")
        t += 0.3
    assert tx.send(text(1, txt="x"), now=t) == 1     # 不会因 i_done=256 抛错
    frames = [f for f in tx.due(t) if f.t == "text"]
    assert sent == 255
    assert frames and frames[0].payload["i_done"] == 255


def test_stale_deltas_of_a_closed_line_are_dropped_and_i_done_follows(tmp_path):
    # 暂停期间整行（delta + text）都排进队列，恢复时已超 10 s：字幕片作废，
    # text 不被旧字幕堵住，i_done 等于实际发出的 0 片
    tx = make_outbox(tmp_path, peer_present=False)
    for n in range(6):
        tx.send(delta("h:1", f"part{n}", first=(n == 0)), now=0.0 + n * 0.3, final_piece=True)
    tx.send(text(1, txt="part0part1part2part3part4part5"), now=2.0)
    tx.resume(15.0, PAUSE_PEER_ABSENT)
    frames = tx.due(15.0)
    assert [f.t for f in frames] == ["text"]
    assert frames[0].payload["i_done"] == 0


def test_leave_follows_queued_reliables_and_its_grace_starts_when_sent(tmp_path):
    # 桶紧时 leave 排在已入队的 text 之后发出；宽限从 leave 真正发出起算，
    # 所以接收方的补齐窗口不会在那几条 text 发出之前开始
    tx = make_outbox(tmp_path, delivery_timeout_s=10_000)
    for n in range(1, 4):
        tx.send(text(n, txt="好" * 1300), now=0.0)
    tx.send({"t": "leave", "reason": "ended"}, now=0.0)
    order: list[str] = []
    leave_at = None
    for t in ticks(0.0, 12.0):
        for f in tx.due(t):
            if not f.retransmit:
                order.append(f.t)
                if f.t == "leave":
                    leave_at = t
    assert order == ["text", "text", "text", "leave"]
    assert leave_at is not None and leave_at > 0.0
    assert not tx.leave_done(leave_at + 4.9)
    assert tx.leave_done(leave_at + 5.0)


def test_unsent_leave_gives_up_after_twice_the_grace(tmp_path):
    tx = make_outbox(tmp_path, peer_present=False)
    tx.send({"t": "leave", "reason": "ended"}, now=0.0)
    assert tx.due(1.0) == []
    assert not tx.leave_done(9.9)
    assert tx.leave_done(10.0)


def test_unsequenced_line_events_with_a_foreign_prefix_are_rejected():
    # line_delta / line_abort 不经序号，也必须绑定已认证发送方的 ln 前缀
    rx = InboxSequencer(peer_ln_prefix="g:")
    spoof = decode_msg({"t": "line_delta", "ln": "h:3", "i": 0, "lp": 5, "txt": "x",
                        "sp": "c", "ad": "hc", "rt": "", "wu": False})
    res = rx.accept(spoof, 0.0)
    assert res.rejected and res.deliver == []
    abort = decode_msg({"t": "line_abort", "ln": "h:3", "lp": 5, "i_done": 0,
                        "reason": "human_interrupt"})
    assert rx.accept(abort, 0.0).rejected
    ok = decode_msg({"t": "line_delta", "ln": "g:3", "i": 0, "lp": 5, "txt": "x",
                     "sp": "c", "ad": "hc", "rt": "", "wu": False})
    assert rx.accept(ok, 0.0).deliver
    assert rx.prefix_rejected == 2


async def test_cancelled_close_still_deletes_the_outbox_file(tmp_path):
    import threading

    tx = make_outbox(tmp_path)
    gate = threading.Event()
    real_write = VisitOutbox._append_sync

    def slow_write(self, *args, **kwargs):
        gate.wait(5)
        return real_write(self, *args, **kwargs)

    VisitOutbox._append_sync = slow_write
    try:
        tx.send(text(1), now=0.0)
        closing = asyncio.create_task(tx.close())
        await asyncio.sleep(0.05)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        pending = tx._last_write
        gate.set()
        while not pending.done():          # 先等被挡住的那次写入真正落盘
            await asyncio.sleep(0.01)
        for _ in range(200):
            if not tx.path.exists():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert not tx.path.exists()
    finally:
        VisitOutbox._append_sync = real_write


def test_encoded_size_is_an_upper_bound_for_text(tmp_path):
    # i_done 由 send / 首发改写：估算按最大宽度，缺字段也不报错
    import utils.visit_wire as vw

    tx = make_outbox(tmp_path)
    msg = text(1, "z" * 763)
    without = {k: v for k, v in msg.items() if k != "i_done"}
    est = tx.encoded_size(without)
    actual = vw.wire_size(vw.encode_msg(dict(msg, seq=2 ** 32 - 1, i_done=255)), visit_id=tx.visit_id)
    assert est == actual
    assert tx.encoded_size(dict(msg, i_done=0)) == actual
