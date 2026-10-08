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

from dataclasses import dataclass
from typing import Any, Optional

from main_logic.visit.identity import JtiWindow, PeerBlocked, TicketRejected, verify_identity_ticket
from main_logic.visit.limits import RateChannel, channel_for
from main_logic.visit.room import IncomingLineDone, IncomingLineStart, LineRef
from config.visit_settings import VISIT_GOODBYE_MAX_CHARS
from main_logic.visit.sanitize import clamp_peer_line, defang_markdown_media
from main_routers.visit_router.runtime_common import (
    PHASE_AWAITING,
    decode_addressee,
    finalize_reason_for_peer_leave,
    side_of_ln,
)
from utils.logger_config import get_module_logger
from utils.visit_wire import decode_msg, proto_compatible

logger = get_module_logger(__name__, "Main")

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
        self.binding_dropped = 0
        self.rate_dropped = 0
        self._early_effects: list = []
        self._peer_lines: dict[str, dict] = {}
        self._line_quota: dict[str, bool] = {}
        self._peer_lp: dict[str, int] = {}
        self.last_peer_goodbye = ""

    # ── 入口 ─────────────────────────────────────────────────────────

    async def on_recv(self, *, from_vid: str, cmd: int, payload: dict, nbytes: int) -> None:
        """One reassembled message from the iframe (``recv``)."""
        now = self.clock()
        frame = self.limiter.admit_frame(from_vid, nbytes, now=now)
        if not frame.allowed:
            self.rate_dropped += 1
            if frame.sustained_overflow:
                self.request_finalize("peer_protocol_violation")
            return
        expected = self.peer.vid if self.peer is not None else None
        creds = self.creds
        if expected is None and self.side == "guest" and creds is not None:
            expected = creds.peer_vid
        if expected is not None and from_vid != expected:
            # 发送者绑定：不是经核验的对端 vid 一律丢弃（同房第三人、被顶掉的旧身份）
            self.binding_dropped += 1
            self._count_anomaly("sender_binding", streak=False)
            return
        try:
            msg = decode_msg(payload, cmd=cmd)
        except ValueError:
            self._count_anomaly("malformed")
            return
        t = msg.get("t")
        if self.peer is None and t != "hello":
            # 核验之前只看 hello；其余的（含 ack）对端在核验后会重发 / 再回
            self.gate_dropped += 1
            return
        if self.finalizing and t not in ("ack", "leave"):
            # 收尾中只处理 ack（leave 的确认）与对端的 leave
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
        early, self._early_effects = self._early_effects, []
        for eff in early:
            self.apply_effects(eff)
        # 先按序处理交付的消息，再看 leave：补齐 leave 之前缺口的那一条（常是最后一行
        # text{final}）必须先进转录，结束之后就不再处理台词了
        for item in res.deliver:
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
        violation = self.room.observe_lp(msg.get("lp"), reliable=True)
        if violation is not None:
            # 提前交付的这条不再经过 _rx_wrap_up：在这里同样校验 lp、计违约
            self._early_effects.append(self.room.violation_effects(violation))
            return
        self._early_effects.append(self.room.on_incoming_wrap_up(
            "speaking", str(msg.get("reason") or ""), msg.get("lp", 0), now, msg.get("ln"),
        ))

    def _count_anomaly(self, kind: str, *, streak: bool = True) -> None:
        self.journal.note_anomaly()
        if self.room is not None and streak:
            self.apply_effects(self.room.record_anomaly(kind))
        elif self.room is not None:
            self.room.anomalies_total += 1
        else:
            self.pre_room_anomalies += 1

    # ── 分派 ─────────────────────────────────────────────────────────

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
            # 接待前闸门：台词类一律丢弃并单独计数（不进连续异常，免得早到的台词把对端踢掉）
            self.gate_dropped += 1
            return
        handler = _HANDLERS.get(str(t))
        if handler is None:
            return
        room = self.room
        before = room.anomalies_total if room is not None else None
        await handler(self, m, from_vid, now)
        # 处理完且没记任何异常才算一条合法消息、清零连续计数：先清零的话，持续超速 / 坏字段的
        # 对端每条都被下一条清零，永远到不了 VISIT_ANOMALY_FINALIZE_COUNT
        if room is not None and self.room is room and room.anomalies_total == before:
            room.record_valid_message()

    async def _rx_hello(self, m: dict, from_vid: str, now: float) -> None:
        if not proto_compatible(1, m.get("caps") or {}):
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
        except Exception as exc:  # noqa: BLE001 - 公钥 / 黑名单读不到：fail closed
            logger.warning("visit %s: peer ticket not verifiable: %s", self.visit_id[:6], type(exc).__name__)
            self.request_finalize("peer_identity_rejected")
            return
        if self.finalizing:
            return
        caps = m.get("caps") or {}
        if self.peer is not None:
            # 对端重连 / 重载后重放同一张票：只刷新存活时钟
            if claims.sub != self.peer.uid or claims.vid != self.peer.vid:
                self.request_finalize("peer_identity_rejected")
                return
            self.liveness.on_peer_verified(now)
            return
        from main_logic.visit.subjects import derive_short_code

        display = await self._clean_peer_name(claims.display_name, claims.sub)
        if self.finalizing or self.peer is not None:
            # 读本机名字期间这场已被结束（或另一条 hello 先装好了对端）：不再装对端、不发邀请
            return
        self.peer = PeerInfo(
            uid=claims.sub, vid=claims.vid, char_tag=claims.char_tag, raw_display=claims.display_name,
            display=display,
            short_id=derive_short_code(claims.sub), video=caps.get("video") is True,
            lang=m.get("lang") if isinstance(m.get("lang"), str) else None,
            crop=caps.get("crop") if caps.get("crop") in ("upper", "full") else "upper", jti=claims.jti,
        )
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
            await self.host.send_frame(dict(self._invite_frame))
            # peer_vid 补齐（订阅仍是 false：接待之前不收看）
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
        except Exception:  # noqa: BLE001 - 读不到本机名字时只靠通用规则
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
        if not self._line_admitted(ln, from_vid, now):
            # 这一行没拿到 text 配额：整行不上屏（增量也不发），与超速的 text 同一个结果
            return
        meta = self._peer_lines.get(ln)
        if m.get("i") == 0 and meta is None:
            ad_side, ad_kind = decode_addressee(m.get("ad"))
            meta = {"sp": m.get("sp"), "ad": m.get("ad"), "rt": m.get("rt") or "", "wu": bool(m.get("wu")),
                    "lp": lp}
            self._remember_peer_line(ln, meta)
            self.apply_effects(self.room.on_incoming_start(IncomingLineStart(
                ref=LineRef(ln, lp, self.peer_side), speaker="human" if m.get("sp") == "h" else "cat",
                addressee_side=ad_side, addressee_kind=ad_kind, reply_to=self._ref_of(meta["rt"]),
                goodbye=meta["wu"],
            ), now))
        meta = meta or {"sp": "c", "ad": None, "rt": "", "wu": False, "lp": lp}
        ad_side, ad_kind = decode_addressee(meta.get("ad"))
        await self.host.send_frame({
            "type": "visit_line_delta", "visit_id": self.visit_id, "line_id": ln, "i": m.get("i"),
            "lp": lp, "text": defang_markdown_media(str(m.get("txt") or "")),
            "speaker": self.speaker_payload(self.peer_side, "human" if meta.get("sp") == "h" else "cat"),
            "addressee": {"side": ad_side, "kind": ad_kind}, "goodbye": bool(meta.get("wu")),
            "ts": self.wall(), "paced": "audio",
        })

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
        self.apply_effects(self.room.on_incoming_abort(ln, now, m.get("reason")))
        await self.host.send_frame({
            "type": "visit_line_abort", "visit_id": self.visit_id, "line_id": ln,
            "i_done": m.get("i_done"), "reason": m.get("reason"), "ts": self.wall(),
        })

    async def _rx_typing(self, m: dict, from_vid: str, now: float) -> None:
        kind = "human" if m.get("sp") == "h" else "cat"
        await self.host.send_frame({
            "type": "visit_typing", "visit_id": self.visit_id,
            "speaker": self.speaker_payload(self.peer_side, kind), "on": m.get("on", True) is not False,
        })

    async def _rx_stats(self, m: dict, from_vid: str, now: float) -> None:
        return None

    async def _rx_text(self, m: dict, from_vid: str, now: float) -> None:
        if self.room is None:
            return
        ln, lp = m.get("ln"), m.get("lp")
        if self._lp_rejected(self.room.observe_lp(lp, ln=ln, reliable=True, closes_line=True)):
            return
        if not self._line_admitted(ln, from_vid, now):
            # 超速：已回 ack（不让对端重传到 delivery_failed），但不上屏、不入史、不进转录、不触发回复
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
            self.apply_effects(eff)  # 记异常，并让连续违约的收尾生效
            return
        txt = str(m.get("txt") or "")
        speaker_from = "peer_cat" if sp == "cat" else "peer_human"
        await self.record_line(speaker_from, side=self.peer_side, lp=lp, ln=ln, text=txt, truncated=truncated)
        self.add_peer_history(ln, lp, speaker_from, txt, truncated=truncated, trunc_reason=trunc_reason)
        if m.get("wu") is True and sp == "cat":
            # 要拼进本侧下一轮 prompt：按收件侧再清洗、按告别句上限截（对端可能不守 LineSpeaker 的上限）
            self.last_peer_goodbye = clamp_peer_line(txt)[:VISIT_GOODBYE_MAX_CHARS]
        await self.host.send_frame(self.visit_line_payload(
            ln=ln, lp=lp, side=self.peer_side, kind=sp, ad_side=ad_side, ad_kind=ad_kind,
            reply_to=str(m.get("rt") or ""), goodbye=m.get("wu") is True, text=txt, truncated=truncated,
            i_done=m.get("i_done", 0), trunc_reason=trunc_reason,
        ))
        self.apply_effects(eff)  # 违约由 apply_effects 统一记一次异常

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

    # ── 本机展示 ─────────────────────────────────────────────────────

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
        trunc_reason: Optional[str] = None,
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
        return payload

    def visit_line_payload_from_record(self, record: dict) -> dict:
        """A ``visit_line``-shaped entry of ``GET /state`` from an in-memory transcript record."""
        speaker_from = str(record.get("from") or "")
        side = record.get("side") or self.side
        kind = "human" if speaker_from.endswith("_human") else "cat"
        return {
            "type": "visit_line", "visit_id": self.visit_id, "line_id": "", "lp": record.get("lp"),
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
