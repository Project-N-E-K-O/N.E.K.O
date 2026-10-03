# -*- coding: utf-8 -*-
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

"""Receive-side rate limits and the machine-wide blocklist for visits.

``PeerRateLimiter`` (design §4.1 recv entry, §4.2 text, §5 PR-06)
    Per-sender (``vid``) limits applied by the receiving backend, because a
    modified peer client cannot be trusted to honour its own send limits:

    * frame level (``admit_frame``, before reassembly): byte bucket
      ``VISIT_PEER_RECV_BPS`` (burst 16 KiB) and message bucket
      ``VISIT_PEER_RECV_MSGS_PER_S``;
    * message level (``admit``): ``ctl`` <= ``VISIT_PEER_CTL_PER_S``/s,
      ``lossy`` <= ``VISIT_PEER_LOSSY_PER_S``/s, and ``text`` through a token
      bucket refilling ``VISIT_INBOUND_TEXT_REFILL_PER_S``/s (= 20 per 10 s)
      capped at ``VISIT_INBOUND_TEXT_BURST`` plus a per-visit hard cap
      ``VISIT_INBOUND_TEXT_MAX``.

    Every over-limit message is dropped and counted. A ``text`` overflow
    counts toward the room's consecutive-anomaly streak
    (``counts_toward_streak=True``, §4.2). Frame / ctl / lossy overflows are
    diagnostics only, but an overflow sustained for 30 s reports
    ``sustained_overflow=True`` so the caller can finalize with
    ``peer_protocol_violation`` (§4.1).

``Blocklist`` (design §3.7.5)
    ``config_dir/visit_blocklist.json`` = ``{blocked:[{visit_uid,
    display_name_at_block, blocked_at, reason?}]}``, keyed by ``visit_uid``.
    Not partitioned by community account: blocking protects the person at this
    machine and must survive an account switch. Loaded once into memory so the
    hello verification can query it synchronously; every mutation goes
    through the async, lock-serialised ``ablock`` / ``aunblock`` and is
    written atomically.
"""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from config.visit_settings import (
    VISIT_BLOCKLIST_FILENAME,
    VISIT_INBOUND_TEXT_BURST,
    VISIT_INBOUND_TEXT_MAX,
    VISIT_INBOUND_TEXT_REFILL_PER_S,
    VISIT_PEER_CTL_PER_S,
    VISIT_PEER_LOSSY_PER_S,
    VISIT_PEER_RECV_BPS,
    VISIT_PEER_RECV_MSGS_PER_S,
)
from utils.file_utils import (
    atomic_write_json_async,
    read_json,
    read_json_async,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# §4.1：字节桶容量 16 KB（速率 VISIT_PEER_RECV_BPS=10 KB/s）；visit_settings 没有这个常量。
_RECV_BYTES_BURST = 16 * 1024
# §4.1：持续超限 30 s → peer_protocol_violation；visit_settings 没有这个常量。
_SUSTAINED_OVERFLOW_S = 30.0
# 两次丢弃间隔超过它就算一段超限结束（桶已恢复过）。
_OVERFLOW_RUN_GAP_S = 1.0
_DISPLAY_NAME_MAX = 64
_REASON_MAX = 64


class RateChannel(str, Enum):
    """Message-level limiter channels."""

    TEXT = "text"
    CTL = "ctl"
    LOSSY = "lossy"


# cmd 1 = ctl（text 以外）；cmd 2 中只有 text 走 TEXT 桶，line_delta / line_abort 只受帧级桶约束；cmd 3 = lossy。
_CTL_TYPES = frozenset({"hello", "ready", "ack", "hb", "state", "wrap_up", "leave"})
_LOSSY_TYPES = frozenset({"typing", "stats"})
_FRAME_ONLY_TYPES = frozenset({"line_delta", "line_abort"})


def channel_for(t: str, cmd: int | None = None) -> RateChannel | None:
    """Map a payload type to its limiter channel (``None`` = frame buckets only).

    Unknown types fall back to ``cmd`` (1 -> ctl, 3 -> lossy, otherwise None)
    so a newer peer's message types are still rate limited.
    """
    if t == "text":
        return RateChannel.TEXT
    if t in _CTL_TYPES:
        return RateChannel.CTL
    if t in _LOSSY_TYPES:
        return RateChannel.LOSSY
    if t in _FRAME_ONLY_TYPES:
        return None
    if cmd == 1:
        return RateChannel.CTL
    if cmd == 3:
        return RateChannel.LOSSY
    return None


@dataclass(frozen=True)
class RateDecision:
    """Outcome of one limiter check.

    ``reason`` is set only when dropped: ``recv_bytes`` / ``recv_msgs`` /
    ``ctl_rate`` / ``lossy_rate`` / ``text_rate`` / ``text_visit_cap``.
    ``counts_toward_streak`` asks the caller to feed the room's
    consecutive-anomaly counter (``violation_streak``).
    ``sustained_overflow`` asks the caller to finalize with
    ``peer_protocol_violation``.
    """

    allowed: bool
    reason: str | None = None
    counts_toward_streak: bool = False
    sustained_overflow: bool = False


_ALLOWED = RateDecision(allowed=True)


@dataclass
class TokenBucket:
    """Classic token bucket with an injected time source (seconds).

    The single token-bucket implementation of the visit package (receive
    limits here, send limits in ``outbox``). ``cap_cost=True`` charges a cost
    above the capacity as the capacity, so an item larger than the bucket can
    still pass once the bucket is full (the outbox byte bucket, section 4.1).
    """

    rate: float
    capacity: float
    tokens: float
    updated_at: float
    cap_cost: bool = False

    @classmethod
    def full(cls, rate: float, capacity: float, now: float, *,
             cap_cost: bool = False) -> "TokenBucket":
        """Create a bucket that starts at capacity."""
        return cls(rate=float(rate), capacity=float(capacity), tokens=float(capacity),
                   updated_at=float(now), cap_cost=cap_cost)

    def _cost(self, cost: float) -> float:
        return min(float(cost), self.capacity) if self.cap_cost else float(cost)

    def refill(self, now: float) -> None:
        """Add the tokens earned since the last refill (capped at capacity)."""
        if now > self.updated_at:
            self.tokens = min(self.capacity, self.tokens + (now - self.updated_at) * self.rate)
            self.updated_at = now

    def fits(self, cost: float) -> bool:
        """Whether ``cost`` tokens are available now (no refill)."""
        return self.tokens + 1e-9 >= self._cost(cost)

    def charge(self, cost: float) -> None:
        """Consume ``cost`` tokens unconditionally (after :meth:`fits`)."""
        self.tokens -= self._cost(cost)

    def peek(self, cost: float, now: float) -> bool:
        """Refill and report whether ``cost`` tokens are available."""
        self.refill(now)
        return self.fits(cost)

    def take(self, cost: float, now: float) -> bool:
        """Consume ``cost`` tokens if available; return whether it did."""
        if not self.peek(cost, now):
            return False
        self.charge(cost)
        return True


@dataclass
class _SenderState:
    recv_bytes: TokenBucket
    recv_msgs: TokenBucket
    ctl: TokenBucket
    lossy: TokenBucket
    text: TokenBucket
    text_accepted: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    overflow_since: float | None = None
    last_overflow_at: float | None = None


class PeerRateLimiter:
    """Per-sender receive limiter; see the module docstring for the rules.

    ``clock`` is the time source used when a call passes no explicit ``now``.
    Call :meth:`admit` for ``text`` only for a *new* ``seq`` (deduplicated
    retransmits are not charged, §4.2).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        text_refill_per_s: float = VISIT_INBOUND_TEXT_REFILL_PER_S,
        text_burst: int = VISIT_INBOUND_TEXT_BURST,
        text_visit_max: int = VISIT_INBOUND_TEXT_MAX,
        ctl_per_s: float = VISIT_PEER_CTL_PER_S,
        lossy_per_s: float = VISIT_PEER_LOSSY_PER_S,
        recv_bps: float = VISIT_PEER_RECV_BPS,
        recv_bytes_burst: float = _RECV_BYTES_BURST,
        recv_msgs_per_s: float = VISIT_PEER_RECV_MSGS_PER_S,
        sustained_overflow_s: float = _SUSTAINED_OVERFLOW_S,
    ) -> None:
        self._clock = clock
        self._text_refill = float(text_refill_per_s)
        self._text_burst = float(text_burst)
        self._text_visit_max = int(text_visit_max)
        self._ctl_per_s = float(ctl_per_s)
        self._lossy_per_s = float(lossy_per_s)
        self._recv_bps = float(recv_bps)
        self._recv_bytes_burst = float(recv_bytes_burst)
        self._recv_msgs_per_s = float(recv_msgs_per_s)
        self._sustained_s = float(sustained_overflow_s)
        self._senders: dict[str, _SenderState] = {}

    # ── 内部 ──

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    def _state(self, sender: str, now: float) -> _SenderState:
        st = self._senders.get(sender)
        if st is None:
            st = _SenderState(
                recv_bytes=TokenBucket.full(self._recv_bps, self._recv_bytes_burst, now),
                recv_msgs=TokenBucket.full(self._recv_msgs_per_s, self._recv_msgs_per_s, now),
                ctl=TokenBucket.full(self._ctl_per_s, self._ctl_per_s, now),
                lossy=TokenBucket.full(self._lossy_per_s, self._lossy_per_s, now),
                text=TokenBucket.full(self._text_refill, self._text_burst, now),
            )
            self._senders[sender] = st
        return st

    def _drop(self, st: _SenderState, reason: str, now: float, *, streak: bool) -> RateDecision:
        st.dropped[reason] = st.dropped.get(reason, 0) + 1
        sustained = False
        if not streak:
            # 只有诊断类超限（帧 / ctl / lossy）参与「持续 30 s」判据；text 走连续异常计数。
            if st.last_overflow_at is None or now - st.last_overflow_at > _OVERFLOW_RUN_GAP_S:
                st.overflow_since = now
            st.last_overflow_at = now
            sustained = st.overflow_since is not None and now - st.overflow_since >= self._sustained_s
        return RateDecision(
            allowed=False, reason=reason, counts_toward_streak=streak, sustained_overflow=sustained,
        )

    # ── 公开接口 ──

    def admit_frame(self, sender: str, nbytes: int, *, now: float | None = None) -> RateDecision:
        """Charge one received data-channel frame of ``nbytes`` (before reassembly)."""
        t = self._now(now)
        st = self._state(sender, t)
        cost = max(0, int(nbytes))
        # 两个桶都先看余量再一起扣，免得一个桶拒了另一个桶白扣。
        if not st.recv_bytes.peek(cost, t):
            return self._drop(st, "recv_bytes", t, streak=False)
        if not st.recv_msgs.peek(1, t):
            return self._drop(st, "recv_msgs", t, streak=False)
        st.recv_bytes.take(cost, t)
        st.recv_msgs.take(1, t)
        return _ALLOWED

    def admit(
        self, sender: str, channel: RateChannel | str | None, *, now: float | None = None,
    ) -> RateDecision:
        """Charge one decoded message on ``channel`` (``None`` is always allowed)."""
        if channel is None:
            return _ALLOWED
        ch = RateChannel(channel)
        t = self._now(now)
        st = self._state(sender, t)
        if ch is RateChannel.TEXT:
            if st.text_accepted >= self._text_visit_max:
                return self._drop(st, "text_visit_cap", t, streak=True)
            if not st.text.take(1, t):
                return self._drop(st, "text_rate", t, streak=True)
            st.text_accepted += 1
            return _ALLOWED
        bucket = st.ctl if ch is RateChannel.CTL else st.lossy
        if not bucket.take(1, t):
            return self._drop(st, f"{ch.value}_rate", t, streak=False)
        return _ALLOWED

    def dropped(self, sender: str) -> dict[str, int]:
        """Return a copy of the per-reason drop counters of ``sender``."""
        st = self._senders.get(sender)
        return dict(st.dropped) if st else {}

    def total_dropped(self, sender: str) -> int:
        """Return the total number of dropped messages / frames of ``sender``."""
        return sum(self.dropped(sender).values())

    def text_accepted(self, sender: str) -> int:
        """Return how many ``text`` messages of ``sender`` were admitted this visit."""
        st = self._senders.get(sender)
        return st.text_accepted if st else 0

    def forget(self, sender: str) -> None:
        """Drop all state of ``sender``."""
        self._senders.pop(sender, None)


# ── 黑名单 ─────────────────────────────────────────────────────────────


def _norm_uid(visit_uid: Any) -> str:
    if not isinstance(visit_uid, str):
        return ""
    return visit_uid.strip().lower()


@dataclass(frozen=True)
class BlockEntry:
    """One blocklist row (``reason`` is optional)."""

    visit_uid: str
    display_name_at_block: str
    blocked_at: float
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialise in the on-disk shape."""
        out: dict[str, Any] = {
            "visit_uid": self.visit_uid,
            "display_name_at_block": self.display_name_at_block,
            "blocked_at": self.blocked_at,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out


def _parse_entries(payload: Any) -> list[BlockEntry]:
    if not isinstance(payload, dict) or not isinstance(payload.get("blocked"), list):
        raise ValueError("blocklist must be {blocked: [...]}")
    by_uid: dict[str, BlockEntry] = {}
    for row in payload["blocked"]:
        # 任一行坏了就整体不可用：丢掉那一行恰好会放进被拉黑的那个人
        if not isinstance(row, dict):
            raise ValueError("blocklist row is not an object")
        uid = _norm_uid(row.get("visit_uid"))
        if not uid:
            raise ValueError("blocklist row has no visit_uid")
        name = row.get("display_name_at_block")
        blocked_at = row.get("blocked_at")
        reason = row.get("reason")
        by_uid[uid] = BlockEntry(
            visit_uid=uid,
            display_name_at_block=name[:_DISPLAY_NAME_MAX] if isinstance(name, str) else "",
            blocked_at=float(blocked_at)
            if isinstance(blocked_at, (int, float)) and not isinstance(blocked_at, bool)
            else 0.0,
            reason=reason[:_REASON_MAX] if isinstance(reason, str) and reason else None,
        )
    return list(by_uid.values())


class BlocklistUnavailable(RuntimeError):
    """The blocklist file exists but could not be read; it must not be treated as empty."""


# 同一个黑名单文件的所有实例共用一把写锁（弱引用登记，没有实例引用后自动释放）：
# 各自一把锁时，两个实例的 ablock 互不串行，后写的整表会冲掉先写的那条
_BLOCKLIST_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _blocklist_lock(path: Path) -> asyncio.Lock:
    key = os.path.normcase(str(path.resolve()))
    lock = _BLOCKLIST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _BLOCKLIST_LOCKS[key] = lock
    return lock


class Blocklist:
    """In-memory view of ``config_dir/visit_blocklist.json`` with atomic writes.

    Build with :meth:`load` / :meth:`aload`. A missing file is an empty list.
    An unreadable or malformed file fails closed: the file is left untouched
    (a transient read error recovers on the next load, a corrupt file stays
    available for repair), :attr:`available` is False, :meth:`is_blocked`
    and every mutation raise :class:`BlocklistUnavailable`, and identity
    verification rejects every peer. Treating it as empty would let a
    blocked peer back in. Mutations write the new list first and only then
    swap it in, so a failed write leaves memory and disk consistent. Every
    instance on the same file shares one write lock, and each mutation
    re-reads the file under it before applying its change, so two instances
    never overwrite each other's rows.
    """

    def __init__(self, config_dir: str | os.PathLike[str], entries: Iterable[BlockEntry] = (),
                 *, available: bool = True) -> None:
        self._path = Path(config_dir) / VISIT_BLOCKLIST_FILENAME
        self._entries: dict[str, BlockEntry] = {e.visit_uid: e for e in entries}
        self._lock = _blocklist_lock(self._path)
        self.available = available

    @property
    def path(self) -> Path:
        """Location of the JSON file."""
        return self._path

    # ── 加载 ──

    @classmethod
    def _from_payload(cls, config_dir: str | os.PathLike[str], payload: Any) -> "Blocklist":
        return cls(config_dir, _parse_entries(payload))

    @classmethod
    def load(cls, config_dir: str | os.PathLike[str]) -> "Blocklist":
        """Synchronously load the blocklist (do not call on the event loop)."""
        path = Path(config_dir) / VISIT_BLOCKLIST_FILENAME
        # 不先 exists()：权限错误会从 exists() 直接抛出、符号链接环会被当成「不存在」，
        # 都绕过 fail closed。只有确认文件不存在才是空表
        try:
            return cls._from_payload(config_dir, read_json(path))
        except FileNotFoundError:
            return cls(config_dir)
        except (OSError, ValueError) as exc:
            return cls._unavailable(config_dir, exc)

    @classmethod
    async def aload(cls, config_dir: str | os.PathLike[str]) -> "Blocklist":
        """Async twin of :meth:`load`."""
        path = Path(config_dir) / VISIT_BLOCKLIST_FILENAME
        try:
            payload = await read_json_async(path)
            return cls._from_payload(config_dir, payload)
        except FileNotFoundError:
            return cls(config_dir)
        except (OSError, ValueError) as exc:
            return cls._unavailable(config_dir, exc)

    @classmethod
    def _unavailable(cls, config_dir: str | os.PathLike[str], exc: BaseException) -> "Blocklist":
        logger.warning("visit blocklist unreadable (%s); failing closed", type(exc).__name__)
        return cls(config_dir, available=False)

    def _require_available(self) -> None:
        if not self.available:
            raise BlocklistUnavailable("visit blocklist could not be read")

    # ── 查询（同步，内存）──

    def is_blocked(self, visit_uid: str) -> bool:
        """Return True when ``visit_uid`` is blocked (in-memory, synchronous).

        Raises :class:`BlocklistUnavailable` when the file could not be read.
        """
        self._require_available()
        uid = _norm_uid(visit_uid)
        return bool(uid) and uid in self._entries

    def get(self, visit_uid: str) -> BlockEntry | None:
        """Return the row of ``visit_uid`` or None."""
        return self._entries.get(_norm_uid(visit_uid))

    def entries(self) -> list[BlockEntry]:
        """Return all rows, oldest block first."""
        return sorted(self._entries.values(), key=lambda e: e.blocked_at)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, visit_uid: object) -> bool:
        return isinstance(visit_uid, str) and self.is_blocked(visit_uid)

    # ── 变更 ──

    def _payload(self, entries: dict[str, BlockEntry]) -> dict[str, Any]:
        return {"blocked": [e.to_json() for e in sorted(entries.values(), key=lambda e: e.blocked_at)]}

    def _with_block(
        self, visit_uid: str, display_name_at_block: str, reason: str | None, now: float | None,
    ) -> dict[str, BlockEntry] | None:
        self._require_available()
        uid = _norm_uid(visit_uid)
        if not uid:
            raise ValueError("visit_uid must be a non-empty string")
        if uid in self._entries:
            return None
        entries = dict(self._entries)
        entries[uid] = BlockEntry(
            visit_uid=uid,
            display_name_at_block=(display_name_at_block or "")[:_DISPLAY_NAME_MAX],
            blocked_at=float(time.time() if now is None else now),
            reason=reason[:_REASON_MAX] if reason else None,
        )
        return entries

    def _without(self, visit_uid: str) -> dict[str, BlockEntry] | None:
        self._require_available()
        uid = _norm_uid(visit_uid)
        if uid not in self._entries:
            return None
        entries = dict(self._entries)
        del entries[uid]
        return entries

    async def ablock(
        self, visit_uid: str, *, display_name_at_block: str, reason: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Block ``visit_uid`` and persist; return False if it was already blocked.

        The only mutation path (with :meth:`aunblock`): every change is
        serialised by one asyncio lock shared by all instances on this file
        and re-reads the file under it, so concurrent blocks / unblocks (even
        from different instances) never rebuild the list from a stale view and
        drop each other's update.
        Cancellation-safe: the write-then-swap transaction runs shielded, so a
        cancelled caller never leaves the file and the in-memory list apart.
        """
        return await asyncio.shield(self._locked_txn(
            lambda: self._with_block(visit_uid, display_name_at_block, reason, now)))

    async def _locked_txn(self, build) -> bool:
        # 写盘与切内存是一个事务：若调用方在写盘途中被取消，事务照常做完（shield），
        # 锁也一直持有到内存与磁盘一致，免得已拉黑的人在内存里还没生效、或被后续写入冲掉
        async with self._lock:
            self._require_available()
            # 锁内先读盘上最新的整表：别的实例可能刚写过，用自己手里的旧视图重建会冲掉它
            try:
                payload = await read_json_async(self._path)
            except FileNotFoundError:
                self._entries = {}
            except (OSError, ValueError) as exc:
                raise BlocklistUnavailable("visit blocklist could not be re-read") from exc
            else:
                try:
                    fresh = _parse_entries(payload)
                except ValueError as exc:
                    raise BlocklistUnavailable("visit blocklist is malformed") from exc
                self._entries = {e.visit_uid: e for e in fresh}
            entries = build()
            if entries is None:
                return False
            await atomic_write_json_async(self._path, self._payload(entries))
            self._entries = entries
            return True

    async def aunblock(self, visit_uid: str) -> bool:
        """Remove ``visit_uid`` and persist; return False if it was not blocked.

        Serialised and cancellation-safe like :meth:`ablock`.
        """
        return await asyncio.shield(self._locked_txn(lambda: self._without(visit_uid)))


__all__ = [
    "BlockEntry",
    "Blocklist",
    "BlocklistUnavailable",
    "PeerRateLimiter",
    "RateChannel",
    "RateDecision",
    "TokenBucket",
    "channel_for",
]
