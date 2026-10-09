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

"""Turn-taking, Lamport clock and wrap-up state machine of one visit (OD-08 v2).

``VisitRoom`` is a pure state machine (``docs/design/visit-infrastructure.md``
section 3.6.3 and PR-06): it never awaits, never does I/O and holds no lock.
Every time value is injected through ``now`` and the reply gap is drawn from
an injected ``rng``. Events go in, ``RoomEffects`` come out; ``VisitRuntime``
executes the effects in the fixed order

    violation -> finalize_reason -> abort_speaking -> cancel_pending_reply
    -> wrap_up (sent on the wire) -> ui_state / peer_crop / peer_hidden
    -> say_goodbye -> reply

Responsibilities kept here:

* Lamport ``lp`` allocation and validation of every received ``lp`` /
  ``lp_seen`` (value range, forward jump, regression and per-sender
  monotonicity on new lines only).
* The one-sentence rule: six cat lines in a row without a human line, or 40
  own cat lines in this visit, start the wrap-up; at most six own cat lines
  per minute (delay only).
* Staleness (``reply_to`` chain), human interruption and the guest
  "yield once" collision rule.
* The wrap-up handshake with the 15 s step timer (stopped by the peer's first
  goodbye piece or by ``wrap_up{ph:'speaking'}``), the 10 s abort of an old
  line and the 45 s hard cap.
* Consecutive anomaly counting (20 in a row finalize; unknown message types
  are diagnostics only), the own ``text`` 20 per 10 s hard limit and the 80
  cat line protocol guard. The receive-side ``text`` limit belongs to
  ``limits.PeerRateLimiter`` alone; the runtime feeds its streak-counting
  drops into :meth:`VisitRoom.record_anomaly`.
* Peer framing / visibility corrections carried by ``hb`` and ``state``.

The receive pipeline is expected to call ``observe_lp`` (or
``on_incoming_hb``) before dispatching a message here and to drop the message
when it returns a violation.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from config.visit_settings import (
    VISIT_REORDER_BUFFER_MAX,
    VISIT_ANOMALY_FINALIZE_COUNT,
    VISIT_CLAUSE_MAX_MS,
    VISIT_CROP_DEFAULT,
    VISIT_LP_MAX,
    VISIT_LP_MAX_JUMP,
    VISIT_LP_REGRESS_MAX,
    VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN,
    VISIT_MAX_LINES,
    VISIT_OUTBOX_PENDING_MAX_BYTES,
    VISIT_OWN_LINES_PER_MINUTE,
    VISIT_OWN_LINES_PER_VISIT,
    VISIT_OWN_TEXT_PER_10S,
    VISIT_PIECE_MAX_BYTES,
    VISIT_PIECES_MAX,
    VISIT_REPLY_GAP_S,
    VISIT_SPEAKING_ABORT_AFTER_S,
    VISIT_WRAP_UP_MAX_S,
    VISIT_WRAP_UP_PROPOSE_TIMEOUT_S,
    VISIT_WRAP_UP_STEP_S,
)
from utils.visit_wire import SILENCING_TRUNC_REASONS

Side = Literal["host", "guest"]
SpeakerKind = Literal["cat", "human"]
Phase = Literal["active", "wrap_up", "ending"]
WrapUpPhase = Literal["propose", "begin", "ack", "speaking", "done"]
WrapUpAction = Literal["none", "begin", "propose", "ack", "speaking", "done"]

# 一整行 text 编码后的最大体积（8 片 × 每片上限）；may_start_cat_line 的 busy 判据
# = 在途字节 > VISIT_OUTBOX_PENDING_MAX_BYTES − 这个值（§4.2 text 末尾）。
# 设计稿写的是「8 片 × 1 KiB」，按信封真实上限 1000 B 计更紧；取两者较大值偏保守。
_LINE_MAX_ENCODED_BYTES = VISIT_PIECES_MAX * max(VISIT_PIECE_MAX_BYTES, 1024)
_OWN_TEXT_WINDOW_S = 10.0
_MINUTE_WINDOW_S = 60.0
_VALID_CROPS = ("upper", "full")

# finalize reason（§3.6.8 集合内的值）
FINALIZE_WRAP_UP = "wrap_up"
FINALIZE_PROTOCOL_VIOLATION = "peer_protocol_violation"
FINALIZE_MAX_LINES = "max_lines"

# wrap_up.reason 线上枚举（§4.2 wrap_up）
WRAP_REASON_QUIET = "quiet"
WRAP_REASON_BUDGET = "budget"
WRAP_REASON_RECALL = "recall"
WRAP_REASON_TIME_UP = "time_up"
_WRAP_REASONS = frozenset({WRAP_REASON_QUIET, WRAP_REASON_BUDGET, WRAP_REASON_RECALL,
                           WRAP_REASON_TIME_UP})


def _side_rank(side: str) -> int:
    return 0 if side == "host" else 1


def _side_prefix(side: str) -> str:
    return "h:" if side == "host" else "g:"


def _other(side: Side) -> Side:
    return "guest" if side == "host" else "host"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class LineRef:
    """Identity and total-order position of one line.

    ``line_id`` is the wire ``ln`` (``"h:<n>"`` / ``"g:<n>"``, a per-side line
    counter, not the outbox ``seq``); ``lp`` is the Lamport value allocated
    when the first piece of the line was sent.
    """

    line_id: str
    lp: int
    side: Side


@dataclass(frozen=True)
class IncomingLineStart:
    """The first piece (``line_delta`` with ``i == 0``) of a peer line arrived."""

    ref: LineRef
    speaker: SpeakerKind
    addressee_side: Side
    addressee_kind: SpeakerKind
    reply_to: Optional[LineRef]
    goodbye: bool


# 截断原因里只有这几种是「被故意掐断、不该接话」：人类插话（随后有人类行）、
# 收尾掐旧行、整场结束（集合与 fit_text_to_wire 共用）。wire_size / tts_error /
# llm_error / stall 截断的行是对端这一轮已经说完——不回就两边都在等，一直拖到 idle_timeout。
_SILENCING_TRUNC_REASONS = SILENCING_TRUNC_REASONS


def _silences(truncated: bool, reason: Optional[str]) -> bool:
    """True when a truncated line must not be answered (missing reason counts)."""
    return truncated and (reason is None or reason in _SILENCING_TRUNC_REASONS)


@dataclass(frozen=True)
class IncomingLineDone:
    """A peer ``text{final}`` was delivered in ``seq`` order.

    The first four fields are the d4 shape. ``speaker`` / ``addressee_*`` /
    ``reply_to`` repeat the ``text`` fields ``sp`` / ``ad`` / ``rt`` so that a
    line whose deltas never arrived (whole-line mode, or every delta lost) is
    still handled; when ``speaker`` is ``None`` the values recorded by the
    matching ``IncomingLineStart`` are used, and without either the line is
    treated as an unaddressed cat line.

    ``trunc_reason`` is the ``text.trunc_reason`` of a truncated line. Only an
    interrupting reason (see ``_SILENCING_TRUNC_REASONS``, or a missing one)
    means "this line was cut off on purpose, do not answer it"; a line cut by
    ``wire_size`` / ``tts_error`` / ``llm_error`` / ``stall`` / ``goodbye_cap``
    is a finished turn and is answered like any other.
    """

    ref: LineRef
    truncated: bool
    tail_ms: Any
    goodbye: bool
    speaker: Optional[SpeakerKind] = None
    addressee_side: Optional[Side] = None
    addressee_kind: Optional[SpeakerKind] = None
    reply_to: Optional[LineRef] = None
    trunc_reason: Optional[str] = None


@dataclass(frozen=True)
class ReplyPlan:
    """A scheduled reply: start no earlier than the absolute time ``not_before``."""

    reply_to: LineRef
    not_before: float
    goodbye: bool = False


@dataclass(frozen=True)
class WrapUpDecision:
    """A ``wrap_up`` message this side must send now (``action == 'none'``: nothing).

    ``ln`` is set only for ``action == 'speaking'`` (the goodbye line that is
    starting); ``reason`` is the wire enum ``quiet | budget | recall |
    time_up``.
    """

    action: WrapUpAction = "none"
    reason: str = ""
    ln: Optional[str] = None


@dataclass
class RoomEffects:
    """Pure data returned by every event handler; ``VisitRuntime`` executes it.

    ``say_goodbye`` asks this side to say its single goodbye line; when
    ``reply`` is also set (with ``goodbye=True``) it carries the earliest
    start time, otherwise the goodbye may start right away. ``peer_crop`` /
    ``peer_hidden`` are set only when the value changed; the runtime turns
    them into ``media{peer_crop}`` / ``visit_state_change{peer_crop}`` and
    ``visit_state_change{peer_hidden | peer_visible}``.
    """

    reply: Optional[ReplyPlan] = None
    cancel_pending_reply: bool = False
    abort_speaking: Optional[str] = None
    wrap_up: WrapUpDecision = field(default_factory=WrapUpDecision)
    say_goodbye: bool = False
    finalize_reason: Optional[str] = None
    yield_once: bool = False
    ui_state: Optional[str] = None
    violation: Optional[str] = None
    peer_crop: Optional[str] = None
    peer_hidden: Optional[bool] = None


@dataclass
class WrapUpState:
    """Book-keeping of the wrap-up handshake (also exposed via ``snapshot``)."""

    initiated_by: Optional[Side] = None
    began_at: float = 0.0
    reason: str = ""
    reason_fallback: bool = False   # reason 是只见到告别行时的回退值，真实 reason 到达时纠正
    guest_goodbye_done: bool = False
    host_goodbye_done: bool = False
    proposed_at: float = 0.0
    begin_received: bool = False
    own_goodbye_requested: bool = False
    own_goodbye_started: bool = False
    peer_goodbye_started: bool = False
    step_started_at: Optional[float] = None
    step_awaiting_begin: bool = False
    step_stopped: bool = False
    step_expired: bool = False
    done_sent: bool = False
    abort_issued: bool = False


def _ln_of(ref: Optional[LineRef]) -> Optional[str]:
    return ref.line_id if ref is not None else None


@dataclass
class _PeerLine:
    speaker: SpeakerKind
    addressee_side: Side
    addressee_kind: SpeakerKind
    reply_to: Optional[LineRef]
    goodbye: bool
    lp: int = 0


# 记住的 ln 上限（按最近使用淘汰）：只发首片、永不收口的新行能把它撑到整场无界。
# 仍在收片的行每片都会刷新，正常场次远到不了这个量
_SEEN_LNS_MAX = 4 * (VISIT_REORDER_BUFFER_MAX + 1)


def _ln_counter(ln: str) -> Optional[tuple[str, int]]:
    """Split a wire ``ln`` (``"<side>:<n>"``) into ``(side, n)``; ``None`` if malformed."""
    side, sep, num = ln.partition(":")
    if not sep or not num.isdigit():
        return None
    return side, int(num)

_GOODBYE_ONLY_REASON = "quiet"   # 只收到告别行（没有 begin / propose）时的收尾原因


class VisitRoom:
    """Pure per-side state machine of one visit (see the module docstring)."""

    def __init__(
        self,
        side: Side,
        *,
        max_cat_turns_without_human: int = VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN,
        own_lines_per_visit: int = VISIT_OWN_LINES_PER_VISIT,
        own_lines_per_minute: int = VISIT_OWN_LINES_PER_MINUTE,
        reply_gap_s: tuple[float, float] = VISIT_REPLY_GAP_S,
        wrap_up_max_s: float = VISIT_WRAP_UP_MAX_S,
        wrap_up_step_s: float = VISIT_WRAP_UP_STEP_S,
        wrap_up_propose_timeout_s: float = VISIT_WRAP_UP_PROPOSE_TIMEOUT_S,
        speaking_abort_after_s: float = VISIT_SPEAKING_ABORT_AFTER_S,
        max_lines: int = VISIT_MAX_LINES,
        anomaly_finalize_count: int = VISIT_ANOMALY_FINALIZE_COUNT,
        own_text_per_10s: int = VISIT_OWN_TEXT_PER_10S,
        peer_crop: str = VISIT_CROP_DEFAULT,
        rng: Optional[random.Random] = None,
    ) -> None:
        """Create the room of ``side``; it starts in the ``active`` phase.

        ``peer_crop`` is the framing announced by the peer ``hello.caps.crop``.
        ``rng`` only needs ``uniform(a, b)``; inject a seeded one in tests.
        """
        if side not in ("host", "guest"):
            raise ValueError(f"invalid side: {side!r}")
        self.side: Side = side
        self.peer_side: Side = _other(side)
        self._max_quiet = int(max_cat_turns_without_human)
        self._own_per_visit = int(own_lines_per_visit)
        self._own_per_minute = int(own_lines_per_minute)
        self._gap = (float(reply_gap_s[0]), float(reply_gap_s[1]))
        self._wrap_max_s = float(wrap_up_max_s)
        self._step_s = float(wrap_up_step_s)
        self._propose_timeout_s = float(wrap_up_propose_timeout_s)
        self._abort_after_s = float(speaking_abort_after_s)
        self._max_lines = int(max_lines)
        self._anomaly_limit = int(anomaly_finalize_count)
        self._own_text_limit = int(own_text_per_10s)
        self._rng = rng if rng is not None else random.Random()

        self._phase: Phase = "active"
        self._wrap = WrapUpState()

        # 计数（§3.6.3 ⑤）
        self.cat_turns_since_human = 0
        self.own_lines_total = 0
        self.peer_cat_lines_total = 0
        self.own_line_starts: deque[float] = deque()
        self._last_human_key: Optional[tuple[int, int]] = None
        # 已计入的猫娘行的排序键：人类行可能晚于更新的猫娘行收口，
        # 「最近一次人类插话之后的猫娘句数」要按 Lamport 序重算，不能直接清零
        self._cat_line_keys: list[tuple[int, int]] = []

        # Lamport 钟
        self.own_lp = 0
        self.max_lp_seen = 0
        self._peer_max_new_lp = -1
        # 必达消息的单调水位单独记：可丢的新行首片 / typing 可能先于首发丢失、随后
        # 重传的必达消息到达，重传恰好补上 seq 时接收端看不出它是重传
        self._peer_max_reliable_lp = -1
        self._seen_lns: dict[str, int] = {}   # ln → 首次见到的 lp（一行一个 lp；LRU）
        # 被挤出 _seen_lns 的最大行号（按发送方前缀）：行号逐行递增，号不大于它的
        # 「新」ln 只能是被挤出的旧行重用，已无从比对首片 lp
        self._evicted_ln_max: dict[str, int] = {}

        # 发言状态
        self.local_speaking: Optional[LineRef] = None
        self.local_speaking_since = 0.0
        self._local_speaking_goodbye = False
        self._local_speaking_reply_to: Optional[LineRef] = None
        self._goodbye_line_id: Optional[str] = None
        self.pending_reply: Optional[ReplyPlan] = None
        self._yield_once = False
        self._yield_line: Optional[str] = None

        # 对端行
        self._peer_meta: dict[str, _PeerLine] = {}
        self._peer_open: set[str] = set()
        self._peer_done: set[str] = set()
        self._aborted: dict[str, None] = {}   # 有界（按插入先后淘汰），见 _remember_aborted
        self._latest_to_me: Optional[tuple[int, int]] = None
        # 已收口的对端行里排序最大的那条：同一发送方的 text 按收口先后发，
        # 先开口的行可能更晚收口（人类插话行常见），晚到的旧行不能盖掉新行的回复
        self._latest_peer_done: Optional[tuple[int, int]] = None
        self._overlap_reported: set[str] = set()

        # 异常计数（§4.1）
        self.violation_streak = 0
        self.anomalies_total = 0
        self.unknown_type_count = 0
        self._last_violation: Optional[str] = None

        # 限速
        self._own_text_sends: deque[float] = deque()
        # 已开口、还没发 text{final} 的本侧猫娘行：每行收口时必发一条 text，开口即占名额，
        # 免得说话途中插进来的人类行用掉最后一个名额、收口的 text 越过硬上限
        self._own_text_reserved: set[str] = set()

        # 对端画面
        self.peer_crop = peer_crop if peer_crop in _VALID_CROPS else VISIT_CROP_DEFAULT
        self.peer_hidden = False

    # ------------------------------------------------------------------
    # 查询属性

    @property
    def phase(self) -> Phase:
        """Current phase: ``active``, ``wrap_up`` or ``ending``."""
        return self._phase

    @property
    def wrap_up(self) -> WrapUpState:
        """Live wrap-up book-keeping (read only by convention)."""
        return self._wrap

    # ------------------------------------------------------------------
    # Lamport 钟与排序

    def next_lp(self) -> int:
        """Allocate the ``lp`` of a new local line: ``max(own, max_seen) + 1``."""
        self.own_lp = max(self.own_lp, self.max_lp_seen) + 1
        return self.own_lp

    @staticmethod
    def sort_key(ref: LineRef) -> tuple[int, int]:
        """Total-order key ``(lp, side_rank)``; host sorts before guest on a tie."""
        return (int(ref.lp), _side_rank(ref.side))

    def _lp_in_range(self, lp: Any) -> bool:
        if not _is_int(lp) or lp < 0 or lp > VISIT_LP_MAX:
            return False
        return lp - max(self.max_lp_seen, self.own_lp) <= VISIT_LP_MAX_JUMP

    def observe_lp(self, lp: Any, *, ln: Optional[str] = None,
                   is_retransmit: bool = False, reliable: bool = False,
                   closes_line: bool = False) -> Optional[str]:
        """Validate and observe the ``lp`` of a received message.

        Returns ``None`` when accepted (the local clock then covers it) or a
        violation code when the message must be dropped; every violation is
        counted as one anomaly (``record_anomaly`` semantics) and does not
        move the local clock:

        * ``'lp_out_of_range'``: not an integer, ``< 0``, ``> VISIT_LP_MAX`` or
          more than ``VISIT_LP_MAX_JUMP`` above the highest value seen;
        * ``'lp_regress'``: more than ``VISIT_LP_REGRESS_MAX`` below the highest
          value seen;
        * ``'lp_not_monotonic'``: below an ``lp`` the peer already used for an
          earlier new line / control event. A ``reliable`` message (one that
          carries a ``seq``) is compared only with earlier reliable messages:
          their order is already fixed by ``seq``, and a lossy delta / typing
          of a newer line may legitimately overtake a reliable message whose
          first transmission was lost (its retransmission then fills the
          ``seq`` gap exactly and cannot be told apart from a first send).
          Lossy events are compared with every earlier new line / event.
          ``closes_line`` (the ``text`` of a line) skips this check: a line's
          ``lp`` is allocated when it opens, but its ``text`` is sent when it
          closes, so any reliable message the sender emitted in between (the
          ``wrap_up{begin}`` that cuts the line off, for instance) carries a
          larger ``lp`` and legitimately precedes it in ``seq`` order;
        * ``'lp_changed'``: an already observed ``ln`` carries a different
          ``lp`` than its first piece (one line keeps one ``lp``; a changed
          value would move the line in ordering, staleness and history).

        The last two checks only apply to new lines and new control events:
        they are skipped when ``ln`` was already observed or when the caller
        flags ``is_retransmit`` (a reliable message that fills a ``seq`` gap or
        repeats an earlier ``seq``), so a retransmitted ``text{final}`` keeps
        its original ``lp`` even after later lines advanced the clock.
        """
        if not self._lp_in_range(lp):
            return self._count_anomaly("lp_out_of_range")
        known_line = ln is not None and ln in self._seen_lns
        if known_line and self._seen_lns[ln] != lp:
            return self._count_anomaly("lp_changed")
        if not known_line and ln is not None and self._ln_evicted(ln):
            # 被挤出表的旧 ln 又来了：可能换了 lp，按 lp_changed 拒绝
            return self._count_anomaly("lp_changed")
        if not known_line and not is_retransmit:
            if lp < self.max_lp_seen - VISIT_LP_REGRESS_MAX:
                return self._count_anomaly("lp_regress")
            floor = self._peer_max_reliable_lp if reliable else self._peer_max_new_lp
            # 收口一行的 text 带的是开口时分配的 lp：开口到收口之间发出的必达消息
            # （收尾时掐断这一行的 wrap_up{begin}）lp 更大、seq 更前，不能据此判它逆序
            if not closes_line and lp < floor:
                return self._count_anomaly("lp_not_monotonic")
            self._peer_max_new_lp = max(self._peer_max_new_lp, lp)
            if reliable:
                self._peer_max_reliable_lp = max(self._peer_max_reliable_lp, lp)
        self.max_lp_seen = max(self.max_lp_seen, lp)
        if ln is not None:
            if known_line:
                self._seen_lns[ln] = self._seen_lns.pop(ln)     # 刷新为最近使用
            else:
                self._seen_lns[ln] = lp
                while len(self._seen_lns) > _SEEN_LNS_MAX:
                    old = next(iter(self._seen_lns))
                    del self._seen_lns[old]
                    counter = _ln_counter(old)
                    if counter is not None:
                        side, num = counter
                        self._evicted_ln_max[side] = max(self._evicted_ln_max.get(side, -1), num)
        return None

    def _ln_evicted(self, ln: str) -> bool:
        counter = _ln_counter(ln)
        return counter is not None and counter[1] <= self._evicted_ln_max.get(counter[0], -1)

    def _remember_aborted(self, ln: str) -> None:
        # 只发 line_abort 不发 text 的新行会让这张表整场增长：按插入先后淘汰。
        # 它只用来判断待发回复是否针对已停嘴的行，待发回复总是针对最近的行
        self._aborted.pop(ln, None)
        self._aborted[ln] = None
        while len(self._aborted) > _SEEN_LNS_MAX:
            del self._aborted[next(iter(self._aborted))]

    # ------------------------------------------------------------------
    # 异常计数（§4.1 版本偏斜与异常计数）

    def _count_anomaly(self, kind: str) -> str:
        self.anomalies_total += 1
        self.violation_streak += 1
        self._last_violation = kind
        return kind

    def _anomaly_finalize_due(self) -> bool:
        return self.violation_streak >= self._anomaly_limit

    def record_anomaly(self, kind: str) -> RoomEffects:
        """Count one anomaly detected outside the room (schema, size, ``i``, ...).

        Returns ``violation=kind`` and, once ``VISIT_ANOMALY_FINALIZE_COUNT``
        anomalies arrived in a row, ``finalize_reason='peer_protocol_violation'``.
        """
        eff = RoomEffects(violation=self._count_anomaly(kind))
        self._maybe_finalize_anomalies(eff)
        return eff

    def check_lp(self, lp: Any) -> Optional[str]:
        """Range / large-regression check of a UI-only event's ``lp`` (``typing``); never moves the clock.

        Returns the violation code (counted as one anomaly) or ``None``.
        """
        if not self._lp_in_range(lp):
            return self._count_anomaly("lp_out_of_range")
        if lp < self.max_lp_seen - VISIT_LP_REGRESS_MAX:
            return self._count_anomaly("lp_regress")
        return None

    def violation_effects(self, kind: str) -> RoomEffects:
        """Effects of a violation :meth:`observe_lp` already counted (the cutoff included, no second count)."""
        eff = RoomEffects(violation=kind)
        self._maybe_finalize_anomalies(eff)
        return eff

    def record_valid_message(self) -> None:
        """A well-formed known message arrived: reset the consecutive streak."""
        self.violation_streak = 0

    def record_unknown_type(self) -> None:
        """An unknown ``t`` arrived: diagnostics only, never part of the streak."""
        self.unknown_type_count += 1

    def _maybe_finalize_anomalies(self, eff: RoomEffects) -> None:
        if self._phase != "ending" and self._anomaly_finalize_due():
            self._finalize(eff, FINALIZE_PROTOCOL_VIOLATION)

    # ------------------------------------------------------------------
    # 内部工具

    def _finalize(self, eff: RoomEffects, reason: str) -> None:
        if eff.finalize_reason is None:
            eff.finalize_reason = reason
        self._phase = "ending"
        self.pending_reply = None

    def _cancel_pending(self, eff: RoomEffects) -> None:
        if self.pending_reply is not None:
            eff.cancel_pending_reply = True
            self.pending_reply = None

    def _gap_s(self) -> float:
        return float(self._rng.uniform(self._gap[0], self._gap[1]))

    def _plan(self, reply_to: LineRef, tail_ms: int, now: float, *, goodbye: bool = False) -> ReplyPlan:
        return ReplyPlan(reply_to=reply_to, not_before=now + tail_ms / 1000.0 + self._gap_s(),
                         goodbye=goodbye)

    def _after_last_human(self, ref: LineRef) -> bool:
        return self._last_human_key is None or self.sort_key(ref) > self._last_human_key

    def _note_human(self, ref: LineRef) -> None:
        key = self.sort_key(ref)
        if self._last_human_key is not None and key <= self._last_human_key:
            # 同一条人类行的收口（首片已记过）或更旧的人类行：不动计数
            return
        self._last_human_key = key
        self.cat_turns_since_human = sum(1 for k in self._cat_line_keys if k > key)

    def _count_cat_line(self, ref: LineRef, eff: RoomEffects) -> None:
        self._cat_line_keys.append(self.sort_key(ref))
        if self._after_last_human(ref):
            self.cat_turns_since_human += 1
        if self.own_lines_total + self.peer_cat_lines_total > self._max_lines:
            self._finalize(eff, FINALIZE_MAX_LINES)

    def _enter_wrap_up(self, eff: RoomEffects, now: float, *, initiated_by: Side,
                       reason: str) -> None:
        if self._phase != "active":
            self._correct_fallback_reason(reason)
            return
        self._phase = "wrap_up"
        w = self._wrap
        w.initiated_by = initiated_by
        w.began_at = now
        w.reason = reason
        w.reason_fallback = False
        eff.ui_state = "wrap_up"
        self._cancel_pending(eff)
        self._yield_once = False
        if self.side == "host" and not w.peer_goodbye_started:
            # 15 s 步进表从 begin 真正发出时才开始（on_wrap_up_sent）：outbox
            # 暂停 / 拥塞时 begin 可能晚发，提前计时会让东家抢在客人之前送客
            w.step_awaiting_begin = True

    def _correct_fallback_reason(self, reason: Optional[str]) -> None:
        # 只见到告别行就进了收尾（reason 是回退值）：随后到达的 begin / propose / speaking
        # 带着真实原因，用它纠正，免得告别提示词与回发的 wrap_up 一直带着错的原因
        w = self._wrap
        if w.reason_fallback and reason in _WRAP_REASONS:
            w.reason = reason
            w.reason_fallback = False

    def _start_wrap_up_locally(self, eff: RoomEffects, now: float, reason: str) -> None:
        """This side detected a wrap-up condition: host begins, guest proposes."""
        if self._phase != "active":
            return
        if self.side == "host":
            self._enter_wrap_up(eff, now, initiated_by="host", reason=reason)
            eff.wrap_up = WrapUpDecision(action="begin", reason=reason)
        else:
            self._enter_wrap_up(eff, now, initiated_by="guest", reason=reason)
            self._wrap.proposed_at = now
            eff.wrap_up = WrapUpDecision(action="propose", reason=reason)

    def _request_goodbye(self, eff: RoomEffects, *, plan: Optional[ReplyPlan] = None) -> None:
        w = self._wrap
        if w.own_goodbye_requested or w.own_goodbye_started:
            return
        w.own_goodbye_requested = True
        eff.say_goodbye = True
        if plan is not None:
            eff.reply = plan
            self.pending_reply = plan

    def _peer_goodbye_started(self, now: float) -> None:
        w = self._wrap
        w.peer_goodbye_started = True
        w.step_stopped = True

    def _check_wrap_conditions(self, eff: RoomEffects, now: float) -> None:
        if self._phase != "active":
            return
        if self.own_lines_total >= self._own_per_visit:
            self._start_wrap_up_locally(eff, now, WRAP_REASON_BUDGET)
        elif self.cat_turns_since_human >= self._max_quiet:
            self._start_wrap_up_locally(eff, now, WRAP_REASON_QUIET)

    def _addressed_to_me(self, addressee_side: Optional[str], addressee_kind: Optional[str]) -> bool:
        return addressee_side == self.side and addressee_kind == "cat"

    def _collision(self, reply_to: Optional[LineRef]) -> bool:
        """True when a peer cat line arrives while this side's cat is speaking.

        The opening exemption: both lines answering nobody (``rt == ''``) may
        coexist and are not a collision.
        """
        if self.local_speaking is None or self._local_speaking_goodbye:
            return False
        if reply_to is None and self._local_speaking_reply_to is None:
            return False
        return True

    def _on_new_peer_line(self, ln: str) -> None:
        if self._yield_line is not None and ln != self._yield_line:
            self._yield_once = False
            self._yield_line = None

    def _interrupt_for_human(self, eff: RoomEffects) -> None:
        # 收尾期间人类行不打断告别、不取消送客计划（旧行由 10 s 规则收口）
        if self._phase != "active":
            return
        if self.local_speaking is not None and not self._local_speaking_goodbye:
            eff.abort_speaking = "human_interrupt"
        self._cancel_pending(eff)

    # ------------------------------------------------------------------
    # 入站事件

    def on_incoming_start(self, ev: IncomingLineStart, now: float) -> RoomEffects:
        """First piece of a peer line arrived (``line_delta`` with ``i == 0``).

        Effects: a human line interrupts this side's speaking cat; a peer cat
        line arriving while this side's cat speaks makes the guest yield once;
        a goodbye line (``wu``) enters the wrap-up and stops the step timer. A
        second open line from the same sender is dropped as
        ``violation='line_overlap'`` (one anomaly, not a finalize).
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        ln = ev.ref.line_id
        if ln in self._peer_done or ln in self._peer_meta:
            return eff
        if self._peer_open:
            # 上一行的 text{final} 可能还在 seq 缺口后面排队，而新行的首片（可丢、
            # 不经重排）先到了：lp 更大的新行说明旧行已经说完，只是收口未到。
            # 旧行移出「未收口」但保留元数据，等它的 text 照常处理；只有 lp
            # 不递增的才是真交叠。
            for other in [o for o in self._peer_open
                          if (m := self._peer_meta.get(o)) is not None and m.lp < ev.ref.lp]:
                self._peer_open.discard(other)
            if self._peer_open:
                eff.violation = self._count_anomaly("line_overlap")
                self._maybe_finalize_anomalies(eff)
                return eff
        self._peer_meta[ln] = _PeerLine(ev.speaker, ev.addressee_side, ev.addressee_kind,
                                        ev.reply_to, ev.goodbye, ev.ref.lp)
        # 只开口不收口的行有上限（同 LineDeltaAssembler）：丢最旧的元数据，它的 text
        # 若真的晚到，按「没见过首片」处理
        while len(self._peer_meta) > VISIT_REORDER_BUFFER_MAX + 1:
            oldest = next(iter(self._peer_meta))
            del self._peer_meta[oldest]
            self._peer_open.discard(oldest)
        self._peer_open.add(ln)
        self._on_new_peer_line(ln)
        if ev.speaker == "human":
            self._note_human(ev.ref)
            self._interrupt_for_human(eff)
            return eff
        if ev.goodbye:
            self._on_peer_goodbye_seen(eff, now)
            return eff
        if self._collision(ev.reply_to) and self.side == "guest":
            self._yield_once = True
            self._yield_line = ln
            eff.yield_once = True
        return eff

    def _on_peer_goodbye_seen(self, eff: RoomEffects, now: float,
                              reason: Optional[str] = None) -> None:
        """A peer goodbye line started (first ``wu`` piece, ``speaking``, or its ``text``).

        ``reason`` is the wire reason when the signal carried one
        (``wrap_up{speaking}``); otherwise a valid fallback is used and
        corrected once ``begin`` / ``propose`` arrives.
        """
        if self._phase == "active":
            # begin / propose 丢了或排在 seq 缺口后面、只见到告别行：reason 必须是协议
            # 合法值，否则本侧随后的 wrap_up{speaking} 编码不出来；speaking 自带的
            # reason 优先，回退值留待真实 reason 到达时纠正
            if reason in _WRAP_REASONS:
                self._enter_wrap_up(eff, now, initiated_by=self.peer_side, reason=reason)
            else:
                self._enter_wrap_up(eff, now, initiated_by=self.peer_side,
                                    reason=self._wrap.reason or _GOODBYE_ONLY_REASON)
                self._wrap.reason_fallback = True
        else:
            self._correct_fallback_reason(reason)
        self._peer_goodbye_started(now)
        if self.side == "guest":
            # begin 丢了也不卡死：guest 见到 host 的告别行就自己告别一句
            self._request_goodbye(eff)

    def on_incoming_done(self, ev: IncomingLineDone, now: float) -> RoomEffects:
        """A peer ``text{final}`` was delivered (in ``seq`` order, after admission).

        ``tail_ms`` is accepted only as an integer in ``[0, VISIT_CLAUSE_MAX_MS]``;
        anything else is treated as 0 and reported as
        ``violation='tail_ms_out_of_range'`` (counted in ``anomalies_total``
        but not in the consecutive streak, so the line is processed and never
        finalizes the visit). A line addressed to this side's cat produces a
        ``ReplyPlan`` with ``not_before = now + tail_ms / 1000 + U(gap)``; a
        line truncated for an interrupting reason never gets a reply (see
        ``IncomingLineDone.trunc_reason``). A final ``text`` whose speaker,
        addressee, reply target or goodbye flag contradicts the line's first
        piece is dropped as ``violation='line_meta_mismatch'``.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        tail_ms = ev.tail_ms
        if not _is_int(tail_ms) or tail_ms < 0 or tail_ms > VISIT_CLAUSE_MAX_MS:
            tail_ms = 0
            self.anomalies_total += 1
            eff.violation = "tail_ms_out_of_range"

        ln = ev.ref.line_id
        if ln in self._peer_done:
            return eff
        opened = self._peer_meta.pop(ln, None)
        self._peer_open.discard(ln)
        if opened is not None and self._meta_mismatch(ev, opened):
            # 同一行只能有一种解释：首片按人类开口（打断本侧、记人类插话）、收口又改成
            # 猫娘行，会让六句规则的计数绕开。按协议异常丢弃这一行，不两种解释都用
            self._peer_done.add(ln)
            kind = self._count_anomaly("line_meta_mismatch")
            if eff.violation is None:
                eff.violation = kind
            self._maybe_finalize_anomalies(eff)
            return eff
        # 交叠在收口时判：同一发送方的 text 按 seq 有序，诚实的对端总是先收口旧行
        # 再开新行。较新的行先收口、较旧的已开口行还没收到 text，才是真交叠
        # （开口时分不清「旧行 text 排在缺口后」与「交叠」，见 on_incoming_start）
        overlapped = [o for o, m in self._peer_meta.items()
                      if m.lp < ev.ref.lp and o not in self._overlap_reported]
        if overlapped:
            self._overlap_reported.update(overlapped)
            kind = self._count_anomaly("line_overlap")
            if eff.violation is None:
                eff.violation = kind
            self._maybe_finalize_anomalies(eff)
        speaker = ev.speaker or (opened.speaker if opened else "cat")
        ad_side = ev.addressee_side or (opened.addressee_side if opened else None)
        ad_kind = ev.addressee_kind or (opened.addressee_kind if opened else None)
        reply_to = ev.reply_to if ev.reply_to is not None else (opened.reply_to if opened else None)
        goodbye = bool(ev.goodbye or (opened.goodbye if opened else False))
        self._peer_done.add(ln)

        if opened is None:
            self._on_new_peer_line(ln)
            if speaker == "cat" and not goodbye and self._collision(reply_to) \
                    and self.side == "guest" and self._phase == "active":
                self._yield_once = True
                self._yield_line = ln
                eff.yield_once = True

        silenced = _silences(ev.truncated, ev.trunc_reason)
        if silenced:
            self._remember_aborted(ln)
            if (self.pending_reply is not None and not self.pending_reply.goodbye
                    and self.pending_reply.reply_to.line_id == ln):
                self._cancel_pending(eff)

        key = self.sort_key(ev.ref)
        older = self._latest_peer_done is not None and key < self._latest_peer_done
        if not older:
            self._latest_peer_done = key
        if speaker == "human":
            self._note_human(ev.ref)
            # 首片到达时已经打断过；只有整句模式（没见过首片）才在收口时打断，
            # 且比已收口的行旧时不打断——那会取消针对更新那行的回复
            if opened is None and not older:
                self._interrupt_for_human(eff)
        elif goodbye:
            self._on_peer_goodbye_done(eff, ev.ref, tail_ms, now, opened is None)
            return eff
        else:
            self.peer_cat_lines_total += 1
            self._count_cat_line(ev.ref, eff)
            if self._phase == "ending":
                return eff

        to_me = self._addressed_to_me(ad_side, ad_kind)
        if to_me and not silenced:
            if self._latest_to_me is None or key > self._latest_to_me:
                self._latest_to_me = key
        if self._phase == "active" and to_me and not silenced and not older:
            self._cancel_pending(eff)
            plan = self._plan(ev.ref, tail_ms, now)
            self.pending_reply = plan
            eff.reply = plan
        self._check_wrap_conditions(eff, now)
        if self._phase != "active" and eff.reply is not None and not eff.reply.goodbye:
            eff.reply = None
            eff.cancel_pending_reply = True
            self.pending_reply = None
        return eff

    def incoming_meta_mismatch(self, ev: IncomingLineDone) -> bool:
        """Whether a final ``text`` contradicts its line's first piece (read only; no state changes).

        For a ``text`` handled outside :meth:`on_incoming_done` (one that
        arrives once the visit is ending): the same judgement, nothing booked.
        """
        opened = self._peer_meta.get(ev.ref.line_id)
        return opened is not None and self._meta_mismatch(ev, opened)

    @staticmethod
    def _meta_mismatch(ev: IncomingLineDone, opened: _PeerLine) -> bool:
        """True when the final ``text`` contradicts what the line's first piece declared."""
        # 收口带全套元数据（sp/ad/rt）时逐项比；rt 只比行号（lp 由运行时按需解析）
        if ev.speaker is not None and (
            ev.speaker != opened.speaker
            or ev.addressee_side != opened.addressee_side
            or ev.addressee_kind != opened.addressee_kind
            or _ln_of(ev.reply_to) != _ln_of(opened.reply_to)
        ):
            return True
        return bool(ev.goodbye) != bool(opened.goodbye)

    def _on_peer_goodbye_done(self, eff: RoomEffects, ref: LineRef, tail_ms: int, now: float,
                              unseen_start: bool) -> None:
        w = self._wrap
        if unseen_start or not w.peer_goodbye_started:
            self._on_peer_goodbye_seen(eff, now)
        if self.peer_side == "guest":
            w.guest_goodbye_done = True
        else:
            w.host_goodbye_done = True
        if self.side == "host":
            # 客人告别说完 → 东家送客一句（按对端末句 tail_ms + 间隙排程）
            self._request_goodbye(eff, plan=self._plan(ref, tail_ms, now, goodbye=True))

    def on_incoming_abort(self, line_id: str, now: float,
                          reason: Optional[str] = None) -> RoomEffects:
        """A peer ``line_abort`` arrived: drop the pending reply to that line, if any.

        Only an interrupting ``reason`` (or a missing one) marks the line as
        aborted; a ``tts_error`` / ``llm_error`` abort is still answered once
        its ``text`` arrives.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        # line_abort = 这行已停嘴：不再算「未收口」，免得紧随其后的告别行首片被当成交叠
        self._peer_open.discard(line_id)
        if not _silences(True, reason):
            return eff
        self._remember_aborted(line_id)
        if self.pending_reply is not None and self.pending_reply.reply_to.line_id == line_id \
                and not self.pending_reply.goodbye:
            self._cancel_pending(eff)
        return eff

    def on_incoming_wrap_up(self, phase: WrapUpPhase, reason: str, lp: int, now: float,
                            ln: Optional[str] = None) -> RoomEffects:
        """A peer ``wrap_up`` message was delivered (``lp`` already observed).

        * ``propose`` (host only): enter the wrap-up and answer ``begin`` with
          the same reason, without judging any condition (``recall`` too);
          ignored when already wrapping up.
        * ``begin`` (guest only): enter the wrap-up and say the goodbye.
        * ``speaking`` (``ln`` required, prefixed with the peer side): the peer
          goodbye line started; stops the step timer (idempotent). Without a
          valid ``ln`` it is an anomaly and the timer keeps running.
        * ``done`` (guest only): finalize with ``'wrap_up'``.
        * ``ack``: no effect.

        A phase sent in the wrong direction counts as an anomaly.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        if phase == "speaking":
            if not isinstance(ln, str) or not ln.startswith(_side_prefix(self.peer_side)):
                eff.violation = self._count_anomaly("wrap_up_speaking_bad_ln")
                self._maybe_finalize_anomalies(eff)
                return eff
            if not self._wrap.peer_goodbye_started:
                self._on_peer_goodbye_seen(eff, now, reason)
            else:
                self._correct_fallback_reason(reason)
            return eff
        if phase == "ack":
            return eff
        if phase == "propose":
            if self.side != "host":
                return self.record_anomaly("wrap_up_bad_direction")
            if self._phase == "active":
                self._enter_wrap_up(eff, now, initiated_by="guest", reason=reason)
                eff.wrap_up = WrapUpDecision(action="begin", reason=reason)
            else:
                self._correct_fallback_reason(reason)
            return eff
        if phase == "begin":
            if self.side != "guest":
                return self.record_anomaly("wrap_up_bad_direction")
            self._wrap.begin_received = True
            # 已在收尾中（只见到告别行进来的）时 _enter_wrap_up 只纠正回退的 reason
            self._enter_wrap_up(eff, now, initiated_by="host", reason=reason)
            self._request_goodbye(eff)
            return eff
        if phase == "done":
            if self.side != "guest":
                return self.record_anomaly("wrap_up_bad_direction")
            self._finalize(eff, FINALIZE_WRAP_UP)
            return eff
        return self.record_anomaly("wrap_up_bad_phase")

    def on_incoming_hb(self, lp_seen: Any, crop: Any, hidden: Any, now: float) -> RoomEffects:
        """A peer ``hb{lp_seen, crop, hidden}`` arrived.

        ``lp_seen`` obeys the same range / jump rule as ``lp`` (no
        monotonicity check: ``hb`` is lossy); an invalid heartbeat is dropped
        as a whole (one anomaly, no correction applied). A valid one advances
        the Lamport clock and corrects ``peer_crop`` / ``peer_hidden``, so a
        lost ``state`` message is repaired within one heartbeat period.
        """
        eff = RoomEffects()
        if not self._lp_in_range(lp_seen):
            eff.violation = self._count_anomaly("lp_out_of_range")
            self._maybe_finalize_anomalies(eff)
            return eff
        if crop not in _VALID_CROPS or not isinstance(hidden, bool):
            eff.violation = self._count_anomaly("hb_bad_field")
            self._maybe_finalize_anomalies(eff)
            return eff
        self.max_lp_seen = max(self.max_lp_seen, lp_seen)
        self._apply_peer_view(eff, crop, hidden)
        return eff

    def on_incoming_state(self, hidden: Any, crop: Any, now: float) -> RoomEffects:
        """A peer ``state{hidden, crop, ...}`` arrived; same corrections as ``hb``."""
        eff = RoomEffects()
        if crop not in _VALID_CROPS or not isinstance(hidden, bool):
            eff.violation = self._count_anomaly("state_bad_field")
            self._maybe_finalize_anomalies(eff)
            return eff
        self._apply_peer_view(eff, crop, hidden)
        return eff

    def on_peer_frame(self, now: float) -> RoomEffects:
        """A new peer video frame arrived: a hidden peer becomes visible again."""
        eff = RoomEffects()
        if self.peer_hidden:
            self.peer_hidden = False
            eff.peer_hidden = False
        return eff

    def _apply_peer_view(self, eff: RoomEffects, crop: str, hidden: bool) -> None:
        if crop != self.peer_crop:
            self.peer_crop = crop
            eff.peer_crop = crop
        if hidden != self.peer_hidden:
            self.peer_hidden = hidden
            eff.peer_hidden = hidden

    # ------------------------------------------------------------------
    # 本地事件

    def _prune_own_text(self, now: float) -> None:
        while self._own_text_sends and self._own_text_sends[0] <= now - _OWN_TEXT_WINDOW_S:
            self._own_text_sends.popleft()

    def can_accept_local_line(self, now: float) -> bool:
        """Own ``text`` hard limit: fewer than 20 in the last 10 s (cat + human).

        A cat line that has started but not yet sent its ``text{final}`` holds
        one reserved slot until :meth:`on_local_line_done`.
        """
        self._prune_own_text(now)
        return len(self._own_text_sends) + len(self._own_text_reserved) < self._own_text_limit

    def on_local_human_line(self, ref: LineRef, now: float) -> RoomEffects:
        """The local human's line was accepted (call after ``can_accept_local_line``).

        Resets the six-line counter, interrupts this side's speaking cat
        (``abort_speaking='human_interrupt'``), cancels an unspoken reply and
        books one own ``text`` against the 20 per 10 s limit.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        self._own_text_sends.append(now)
        self._note_human(ref)
        self._interrupt_for_human(eff)
        return eff

    def on_local_line_started(self, ref: LineRef, reply_to: Optional[LineRef], goodbye: bool,
                              now: float) -> RoomEffects:
        """This side's cat line sent (or is about to send) its first piece.

        A goodbye line yields ``wrap_up=WrapUpDecision('speaking', ln=...)``,
        which the runtime sends before any piece of that line (both subtitle
        modes) and enters the wrap-up if still active. A normal line enters
        the per-minute window.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        self.local_speaking = ref
        self.local_speaking_since = now
        self._local_speaking_goodbye = bool(goodbye)
        self._local_speaking_reply_to = reply_to
        self.pending_reply = None
        self._own_text_reserved.add(ref.line_id)
        if goodbye:
            w = self._wrap
            if self._phase == "active":
                # 本侧未经 recall / time_up / 对端收尾就先开口告别：同样要合法 reason
                self._enter_wrap_up(eff, now, initiated_by=self.side,
                                    reason=w.reason or _GOODBYE_ONLY_REASON)
                w.reason_fallback = False   # 本侧主动收尾，原因就是 quiet
            w.own_goodbye_requested = True
            w.own_goodbye_started = True
            self._goodbye_line_id = ref.line_id
            eff.wrap_up = WrapUpDecision(action="speaking", reason=w.reason, ln=ref.line_id)
        else:
            self.own_line_starts.append(now)
        return eff

    def on_local_line_done(self, ref: LineRef, truncated: bool, now: float) -> RoomEffects:
        """This side's cat line sent its ``text{final}`` (normal, truncated or aborted).

        Books the line (40 per visit, six-line counter, 80 line guard, own text
        limit) and starts the wrap-up when a condition is met. For the goodbye
        line: the host answers ``wrap_up{done}`` (the runtime sends it after
        the line's ``tail_ms``); the guest starts the 15 s step timer waiting
        for the host's farewell unless it already started.
        """
        eff = RoomEffects()
        self._own_text_reserved.discard(ref.line_id)
        if self.local_speaking is not None and self.local_speaking.line_id == ref.line_id:
            self.local_speaking = None
            self._local_speaking_goodbye = False
            self._local_speaking_reply_to = None
        if self._phase == "ending":
            return eff
        self._own_text_sends.append(now)
        w = self._wrap
        is_goodbye = ref.line_id == self._goodbye_line_id
        if is_goodbye:
            if self.side == "host":
                w.host_goodbye_done = True
                if not w.done_sent:
                    w.done_sent = True
                    eff.wrap_up = WrapUpDecision(action="done", reason=w.reason)
            else:
                w.guest_goodbye_done = True
                if not w.peer_goodbye_started:
                    w.step_started_at = now
                    w.step_stopped = False
                    w.step_expired = False
            return eff
        self.own_lines_total += 1
        self._count_cat_line(ref, eff)
        if self._phase == "ending":
            return eff
        self._check_wrap_conditions(eff, now)
        return eff

    def on_wrap_up_sent(self, phase: WrapUpPhase, now: float) -> None:
        """Runtime: one of this side's ``wrap_up`` frames was written (any transmission).

        The first ``begin`` written starts the host's 15 s step timer
        (waiting for the guest's goodbye to start); it does not start before
        the guest can have received the instruction, and later writes
        (retransmissions) neither restart nor push it back. Other phases are
        ignored.
        """
        w = self._wrap
        if (phase == "begin" and self.side == "host" and w.step_awaiting_begin
                and w.step_started_at is None and not w.peer_goodbye_started):
            w.step_awaiting_begin = False
            w.step_started_at = now

    def on_local_recall(self, now: float) -> RoomEffects:
        """The recall button was pressed (guest: call her back; host: see the guest off).

        Guest: ``propose{recall}``; host: ``begin{recall}``. A repeated press
        while already wrapping up has no effect (the runtime answers
        ``VISIT_RECALL_ALREADY``).
        """
        eff = RoomEffects()
        self._start_wrap_up_locally(eff, now, WRAP_REASON_RECALL)
        return eff

    def on_time_up(self, now: float) -> RoomEffects:
        """``max_duration - 60 s`` reached: host ``begin{time_up}``, guest ``propose``."""
        eff = RoomEffects()
        self._start_wrap_up_locally(eff, now, WRAP_REASON_TIME_UP)
        return eff

    def on_tick(self, now: float) -> RoomEffects:
        """Timer fallback; call periodically (``visit_sweep_loop``).

        Handles the anomaly finalize, the propose timeout (guest starts its
        goodbye), the 10 s abort of an old line still speaking after
        ``begin``, the step timeout (host: see the guest off itself; guest:
        finalize) and the 45 s hard cap.
        """
        eff = RoomEffects()
        if self._phase == "ending":
            return eff
        if self._anomaly_finalize_due():
            self._finalize(eff, FINALIZE_PROTOCOL_VIOLATION)
            return eff
        if self._phase != "wrap_up":
            return eff
        w = self._wrap
        if now - w.began_at >= self._wrap_max_s:
            self._finalize(eff, FINALIZE_WRAP_UP)
            return eff
        if (self.side == "guest" and w.initiated_by == "guest" and not w.begin_received
                and now - w.proposed_at >= self._propose_timeout_s):
            self._request_goodbye(eff)
        if (self.local_speaking is not None and not self._local_speaking_goodbye
                and not w.abort_issued and now - w.began_at >= self._abort_after_s):
            w.abort_issued = True
            eff.abort_speaking = "wrap_up"
        if (w.step_started_at is not None and not w.step_stopped and not w.step_expired
                and now - w.step_started_at >= self._step_s):
            w.step_expired = True
            if self.side == "host":
                self._request_goodbye(eff)
            else:
                self._finalize(eff, FINALIZE_WRAP_UP)
        return eff

    # ------------------------------------------------------------------
    # 查询

    def is_stale(self, plan: ReplyPlan) -> bool:
        """True when ``plan`` should be dropped before speaking.

        Stale = the replied line was aborted / truncated, or a newer complete
        line addressed to this side's cat arrived (its own plan supersedes
        this one). Goodbye plans are never stale.
        """
        if plan.goodbye:
            return False
        if plan.reply_to.line_id in self._aborted:
            return True
        if self._latest_to_me is not None and self._latest_to_me > self.sort_key(plan.reply_to):
            return True
        return False

    def _prune_minute(self, now: float) -> None:
        while self.own_line_starts and self.own_line_starts[0] <= now - _MINUTE_WINDOW_S:
            self.own_line_starts.popleft()

    def may_start_cat_line(self, now: float, *, goodbye: bool = False,
                           outbox_pending_bytes: int = 0,
                           plan: Optional[ReplyPlan] = None) -> tuple[bool, str]:
        """Whether this side's cat may open a line now; returns ``(ok, reason)``.

        Reasons: ``ok``; ``wrap_up`` (ending, or a non-goodbye line during the
        wrap-up, or the goodbye was already said); ``visit_cap`` (40 own
        lines); ``stale`` (``plan`` given and ``is_stale(plan)``); ``yield``
        (guest collision: returned exactly once, then cleared; the runtime
        drops the plan); ``minute_cap`` (six lines in the last 60 s, retry at
        ``next_allowed_start``); ``busy`` (unacked outbox bytes
        ``outbox_pending_bytes`` exceed ``VISIT_OUTBOX_PENDING_MAX_BYTES``
        minus one maximum line, or the own ``text`` 20 per 10 s limit is
        full; retry later). The goodbye line bypasses the line caps, yield
        and staleness but not ``busy``.
        """
        if self._phase == "ending":
            return False, "wrap_up"
        if goodbye:
            if self._wrap.own_goodbye_started:
                return False, "wrap_up"
        else:
            if self.own_lines_total >= self._own_per_visit:
                return False, "visit_cap"
            if self._phase != "active":
                return False, "wrap_up"
            if plan is not None and self.is_stale(plan):
                return False, "stale"
            if self._yield_once:
                self._yield_once = False
                self._yield_line = None
                return False, "yield"
            self._prune_minute(now)
            if len(self.own_line_starts) >= self._own_per_minute:
                return False, "minute_cap"
        if outbox_pending_bytes > VISIT_OUTBOX_PENDING_MAX_BYTES - _LINE_MAX_ENCODED_BYTES:
            return False, "busy"
        if not self.can_accept_local_line(now):
            return False, "busy"
        return True, "ok"

    def next_allowed_start(self, now: float) -> float:
        """Earliest time the per-minute cap lets a new own line start."""
        self._prune_minute(now)
        if len(self.own_line_starts) < self._own_per_minute:
            return now
        return self.own_line_starts[0] + _MINUTE_WINDOW_S

    def snapshot(self) -> dict:
        """JSON-safe view for ``GET /api/visit/state`` (``room`` field)."""
        w = self._wrap
        return {
            "phase": self._phase,
            "cat_turns_since_human": self.cat_turns_since_human,
            "own_lines_total": self.own_lines_total,
            "peer_cat_lines_total": self.peer_cat_lines_total,
            "own_lp": self.own_lp,
            "max_lp_seen": self.max_lp_seen,
            "anomalies": self.anomalies_total,
            "violation_streak": self.violation_streak,
            "unknown_types": self.unknown_type_count,
            "peer_crop": self.peer_crop,
            "peer_hidden": self.peer_hidden,
            "wrap_up": {
                "initiated_by": w.initiated_by,
                "began_at": w.began_at,
                "reason": w.reason,
                "guest_goodbye_done": w.guest_goodbye_done,
                "host_goodbye_done": w.host_goodbye_done,
                "peer_goodbye_started": w.peer_goodbye_started,
                "own_goodbye_started": w.own_goodbye_started,
                "done_sent": w.done_sent,
            },
        }
