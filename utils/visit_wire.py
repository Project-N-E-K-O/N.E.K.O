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

"""Visit data-channel wire helpers (``docs/design/visit-infrastructure.md`` PR-03).

Pure functions and small stateful helpers shared by the visit backend
(``main_logic/visit``) and the transport router. Lives in ``utils/`` (L1), so
it only imports ``config`` and the standard library / pydantic; callers inject
anything that lives higher up (``redact_outbound``, ``redact_outbound_boundary``,
``sanitize_relay_text``).

Contents, grouped by concern:

* ID format gate and path derivation: ``VISIT_ID_RE``, ``REVOCATION_ID_RE``,
  ``require_visit_id``, ``id_path``, ``visit_path``, ``revocation_path``.
* Fragment envelope ``{v, r, m, i, n, p}`` (section 4.1): ``fragment``,
  ``wire_size``, ``Reassembler``.
* Message schema (section 4.2, pydantic) and codec: ``encode_msg``,
  ``decode_msg``, ``cmd_of``, ``is_reliable``, ``proto_compatible``.
* Wire budgets measured on the fully encoded form: ``line_delta_encoded_len``,
  ``fit_text_to_wire``, ``WireBudget``, ``widest_text_payload``.
* Dialogue helpers (section 3.6.4): ``split_clauses``, ``ClauseSplitter``,
  ``Clause``, ``estimate_speech_ms``, ``clause_release_offsets_ms``,
  ``LineDeltaAssembler``, ``max_worst_case_rates``.

Encoding convention: every JSON text produced here is compact
(``separators=(',', ':')``) and keeps non-ASCII characters raw
(``ensure_ascii=False``), which matches ``JSON.stringify`` byte for byte for
valid Unicode input. "Encoded bytes" always means the UTF-8 length of the
final form; a ``text`` / ``line_delta`` body is escaped twice on the wire
(once as payload JSON, once more as the envelope string ``p``), so a quote or
backslash costs up to 4 bytes.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Callable,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Union,
)

import regex
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from config.visit_settings import (
    VISIT_REORDER_BUFFER_MAX,
    VISIT_ACK_COALESCE_MS,
    VISIT_CJK_MS_PER_CHAR,
    VISIT_CLAUSE_MAX_MS,
    VISIT_CLAUSE_MIN_CHARS,
    VISIT_CLAUSE_MIN_MS,
    VISIT_CLAUSE_SOFT_MAX_CHARS,
    VISIT_CLAUSE_SOFT_MAX_LATIN_WORDS,
    VISIT_DATA_BUCKET_BPS,
    VISIT_DEDUP_LRU,
    VISIT_DELTA_MIN_INTERVAL_MS,
    VISIT_DELTA_TEXT_MAX_BYTES,
    VISIT_HEARTBEAT_S,
    VISIT_LATIN_MS_PER_WORD,
    VISIT_LINE_DELTA_MAX_I,
    VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES,
    VISIT_LP_MAX,
    VISIT_PIECE_MAX_BYTES,
    VISIT_PIECES_MAX,
    VISIT_PUNCT_COMMA_MS,
    VISIT_PUNCT_END_MS,
    VISIT_REASSEMBLY_MAX_ENTRIES,
    VISIT_REASSEMBLY_TIMEOUT_S,
    VISIT_TEXT_MAX_BYTES,
    VISIT_WIRE_PROTO,
)
from utils.cjk import is_cjk_char

logger = logging.getLogger(__name__)

__all__ = [
    "VISIT_ID_RE",
    "REVOCATION_ID_RE",
    "CLEARING_ID_RE",
    "require_visit_id",
    "id_path",
    "visit_path",
    "revocation_path",
    "CMD_CTL",
    "CMD_TEXT",
    "CMD_LOSSY",
    "RELIABLE_TYPES",
    "LEAVE_REASONS",
    "TRUNC_REASONS",
    "fragment",
    "wire_size",
    "Reassembler",
    "encode_msg",
    "decode_msg",
    "cmd_of",
    "is_reliable",
    "proto_compatible",
    "line_delta_encoded_len",
    "line_delta_can_merge",
    "widest_text_payload",
    "fit_text_to_wire",
    "WireBudget",
    "Clause",
    "split_clauses",
    "ClauseSplitter",
    "clause_release_offsets_ms",
    "estimate_speech_ms",
    "LineDeltaAssembler",
    "max_worst_case_rates",
]

# ── 本模块私有常量（visit_settings 里没有的协议细节）──────────────────────

_ENVELOPE_VERSION = 1          # 信封 v
# payload v（与信封 v 独立）按线协议主版本登记、由它推出：升 payload v（= 已知字段换了语义）
# 必须同时升 VISIT_WIRE_PROTO 并在这里新增一项。只升 v 不升 proto 的话，新旧两端的 hello
# 会被对方当 _invalid 吞掉、握手干等超时，走不到 proto_mismatch 的「请双方更新」提示。
# 已有的项不改（测试钉住），加字段不升版（未知字段本就忽略）
_PAYLOAD_VERSION_BY_PROTO = {1: 1}
_PAYLOAD_VERSION = _PAYLOAD_VERSION_BY_PROTO[VISIT_WIRE_PROTO]
_U32_MAX = 2 ** 32 - 1
_LN_MAX_DIGITS = 10            # ln = 'h:' + ≤10 位数字 → ≤12 字符
_WIDEST_LN = "h:" + "9" * _LN_MAX_DIGITS
_LANG_MAX_BYTES = 16
_APP_VERSION_MAX_BYTES = 16
_TIER_MAX_BYTES = 16
_TICKET_MAX_BYTES = 2048       # 票 ≈560 B，留足余量；hello 整体 ≤2 KB 由发送侧保证
_LEAVE_REASON_MAX_BYTES = 32   # 枚举外的值接收侧映射 peer_left，所以只限长度不限枚举
_TRUNC_REASON_MAX_BYTES = 32   # 同理：新版本加截断原因不能让老版本把整行判 _invalid
_RAW_T_DIAG_MAX = 32           # 未知 t 只留前 32 字符做诊断
_CLUSTER_BACKOFF_MAX = 16      # 截断时为不切开 emoji 合字最多回退的字符数

# max_worst_case_rates 的纸面参数（§4.1 限速条）
_TEXT_PIECES_TYPICAL = 5       # 普通 4096 B 正文编码后 ≤5 片
_ACK_MAX_BYTES = 80
_HB_MAX_BYTES = 100
_TYPING_MAX_BYTES = 60
_TYPING_PER_S = 1.0
_STATS_MAX_BYTES = 160
_STATS_INTERVAL_S = 5

CMD_CTL = 1
CMD_TEXT = 2
CMD_LOSSY = 3

_CMD_OF: dict[str, int] = {
    "hello": CMD_CTL,
    "ready": CMD_CTL,
    "ack": CMD_CTL,
    "hb": CMD_CTL,
    "state": CMD_CTL,
    "wrap_up": CMD_CTL,
    "leave": CMD_CTL,
    "line_delta": CMD_TEXT,
    "text": CMD_TEXT,
    "line_abort": CMD_TEXT,
    "typing": CMD_LOSSY,
    "stats": CMD_LOSSY,
}

RELIABLE_TYPES: frozenset[str] = frozenset({"hello", "ready", "wrap_up", "leave", "text"})
"""Message types that go through ``VisitOutbox`` (carry ``seq``, retransmitted until acked)."""

LEAVE_REASONS: frozenset[str] = frozenset({
    "home", "ended", "declined", "goodbye", "character_changed", "shutdown",
    "error", "wrapup", "proto_mismatch", "peer_identity_rejected",
    "peer_protocol_violation", "visit_disabled", "delivery_failed",
})
"""Known ``leave.reason`` values; the schema only bounds the length because
receivers map any other value to ``peer_left``."""

TRUNC_REASONS: tuple[str, ...] = (
    "human_interrupt", "wrap_up", "tts_error", "llm_error", "stall",
    "wire_size", "goodbye_cap", "visit_end",
)
"""Known ``text.trunc_reason`` values (what this version sends). The schema only
bounds the length: a newer peer may add a reason, and rejecting it would turn
the whole reliable ``text`` into ``_invalid``. Receivers treat an unknown
reason as a finished turn (see ``main_logic/visit/room.py``)."""

_WIDEST_TRUNC_REASON = max(TRUNC_REASONS, key=len)


# ── ID 格式闸与路径派生（§4.6）────────────────────────────────────────

VISIT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
"""Format of ``visit_id`` (``secrets.token_urlsafe(16)``); always used with ``fullmatch``."""

REVOCATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
"""Format of a revocation log id (``sha256(own_uid|peer_uid|own_char_uid)[:32]``)."""

CLEARING_ID_RE = re.compile(r"^clearing-[0-9a-f]{32}$")
"""Format of a clearing-intent sentinel name under ``visit_revocations/``."""

_FORBIDDEN_SUFFIX_CHARS = frozenset("/\\:\x00")


def require_visit_id(v: Any) -> str:
    """Return ``v`` unchanged if it is a well-formed visit id, else raise ``ValueError``.

    Every entry point that receives a visit id (local endpoints, the peer
    ``hello``, ticket claims, Servers responses, file-name scans) must pass it
    through here before using it for anything else.
    """
    if not isinstance(v, str) or VISIT_ID_RE.fullmatch(v) is None:
        raise ValueError("malformed visit_id")
    return v


def id_path(base_dir: Union[str, Path], ident: Any, pattern: re.Pattern[str], suffix: str) -> Path:
    """Derive ``<base_dir>/<ident><suffix>`` for an id-named file, refusing anything odd.

    ``ident`` must fully match ``pattern``; ``suffix`` may not contain path
    separators, ``:`` or NUL. The joined path is resolved and must be a direct
    child of ``base_dir.resolve()`` (a symlink escaping the directory fails
    too). Raises ``ValueError`` on any violation. ``resolve()`` is the only
    filesystem access; nothing is created.
    """
    if not isinstance(ident, str) or pattern.fullmatch(ident) is None:
        raise ValueError("identifier does not match its format")
    if not isinstance(suffix, str) or any(c in _FORBIDDEN_SUFFIX_CHARS for c in suffix):
        raise ValueError("illegal path suffix")
    name = f"{ident}{suffix}"
    if name in (".", ".."):
        raise ValueError("illegal file name")
    base = Path(base_dir).resolve()
    candidate = (base / name).resolve()
    if candidate.parent != base:
        raise ValueError("derived path escapes its base directory")
    return candidate


def visit_path(base_dir: Union[str, Path], visit_id: Any, suffix: str) -> Path:
    """``id_path`` for files named by ``visit_id`` (spool, report queue, upload files)."""
    return id_path(base_dir, visit_id, VISIT_ID_RE, suffix)


def revocation_path(base_dir: Union[str, Path], rev_id: Any) -> Path:
    """``id_path`` for ``visit_revocations/<rev_id>.json`` (32 lowercase hex, not a visit id)."""
    return id_path(base_dir, rev_id, REVOCATION_ID_RE, ".json")


# ── 编码工具 ────────────────────────────────────────────────────────────

def _dumps(obj: Any) -> str:
    """Compact JSON, raw non-ASCII, no NaN (mirrors ``JSON.stringify``)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_u32(v: Any) -> bool:
    return _is_int(v) and 0 <= v <= _U32_MAX


def _is_seq(v: Any) -> bool:
    """A valid reliable-message ``seq`` (1-based)."""
    return _is_int(v) and 1 <= v <= _U32_MAX


def _utf8_len_char(ch: str) -> int:
    o = ord(ch)
    if o < 0x80:
        return 1
    if o < 0x800:
        return 2
    if o < 0x10000:
        return 3
    return 4


_SHORT_ESCAPES = frozenset('\b\f\n\r\t')


def _esc1_len(ch: str) -> int:
    """UTF-8 bytes of ``ch`` once it is escaped inside a JSON string literal."""
    if ch == '"' or ch == "\\":
        return 2
    o = ord(ch)
    if o < 0x20:
        return 2 if ch in _SHORT_ESCAPES else 6
    return _utf8_len_char(ch)


def _esc2_len(ch: str) -> int:
    """UTF-8 bytes of ``ch`` after being escaped twice (payload JSON, then envelope ``p``)."""
    if ch == '"' or ch == "\\":
        return 4                       # " → \" → \\\"
    o = ord(ch)
    if o < 0x20:
        return 3 if ch in _SHORT_ESCAPES else 7   # \n → \\n ; \x01 → \\u0001
    return _utf8_len_char(ch)


def _esc_len(s: str) -> int:
    """UTF-8 bytes of ``s`` as the content of a JSON string literal (quotes excluded)."""
    return sum(_esc1_len(c) for c in s)


def _check_unicode(s: str) -> None:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("text is not valid Unicode (lone surrogate)") from exc


# ── 不切开字形簇（emoji 合字 / 组合符 / 旗帜）──────────────────────────

def _is_cluster_extender(ch: str) -> bool:
    o = ord(ch)
    if o == 0x200D or o == 0x20E3:                 # ZWJ / keycap
        return True
    if 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF:   # variation selectors
        return True
    if 0x1F3FB <= o <= 0x1F3FF:                    # skin tone modifiers
        return True
    if 0xE0020 <= o <= 0xE007F:                    # tag characters (subdivision flags)
        return True
    return unicodedata.category(ch) in ("Mn", "Me", "Mc")


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _splits_cluster(s: str, p: int) -> bool:
    """True if cutting ``s`` between ``s[p-1]`` and ``s[p]`` would split a grapheme cluster."""
    if p <= 0 or p >= len(s):
        return False
    if _is_cluster_extender(s[p]) or s[p - 1] == "\u200d":
        return True
    if _is_regional_indicator(s[p - 1]) and _is_regional_indicator(s[p]):
        run = 0
        q = p - 1
        while q >= 0 and _is_regional_indicator(s[q]):
            run += 1
            q -= 1
        return run % 2 == 1
    return False


_GRAPHEME = regex.compile(r"\X")


def grapheme_safe_cut(s: str, p: int) -> int:
    """Largest cut ``<= p`` of ``s`` that does not split an extended grapheme cluster (UAX #29).

    Unlike the budget's bounded backoff, this uses full cluster segmentation
    (emoji, flags, combining marks, Hangul jamo ...) and walks back as far as
    needed; meant for short texts such as a capped goodbye line.
    """
    q = min(max(p, 0), len(s))
    if q == len(s):
        return q
    cut = 0
    for m in _GRAPHEME.finditer(s):
        if m.end() > q:
            break
        cut = m.end()
    return cut


def _safe_cut(s: str, p: int, *, floor: int = 0) -> int:
    """Move cut ``p`` left (not below ``floor``) until it no longer splits a cluster.

    Gives up after ``_CLUSTER_BACKOFF_MAX`` characters and returns ``p`` (a
    codepoint boundary is still guaranteed because ``s`` is a ``str``).
    """
    q = p
    while q > floor and _splits_cluster(s, q) and p - q < _CLUSTER_BACKOFF_MAX:
        q -= 1
    if _splits_cluster(s, q):
        return p if p >= floor else floor
    return q


# ── 分片信封（§4.1）──────────────────────────────────────────────────

def _envelope_text(r: str, m: int, i: int, n: int, p: str) -> str:
    return _dumps({"v": _ENVELOPE_VERSION, "r": r, "m": m, "i": i, "n": n, "p": p})


def _greedy_bounds(weights: Sequence[int], budget: int) -> list[tuple[int, int]]:
    bounds: list[tuple[int, int]] = []
    start = 0
    used = 0
    for idx, w in enumerate(weights):
        if used + w > budget:
            bounds.append((start, idx))
            start = idx
            used = 0
        used += w
    bounds.append((start, len(weights)))
    return bounds


def fragment(
    payload_json: str,
    *,
    visit_id: str,
    msg_id: int,
    max_bytes: int = VISIT_PIECE_MAX_BYTES,
) -> list[bytes]:
    """Split one payload JSON text into envelope pieces, each ``<= max_bytes`` UTF-8 bytes.

    Envelope per piece (compact JSON, keys in this order)::

        {"v":1,"r":<visit_id[:8]>,"m":<msg_id>,"i":<0-based index>,"n":<count>,"p":<slice>}

    ``p`` slices are taken greedily by their *escaped* length (a quote or
    backslash inside ``p`` costs 2 bytes, control characters 2 or 6) and never
    cut a codepoint; concatenating every ``p`` in ``i`` order gives back
    ``payload_json`` exactly. The envelope overhead is computed with ``i`` and
    ``n`` at the digit width of the final piece count, so the bound is exact,
    not estimated. An empty payload still yields one piece.

    Returns the encoded pieces (``bytes``, ready for TRTC ``ArrayBuffer`` /
    LiveKit ``Uint8Array``). Raises ``ValueError`` for a malformed
    ``visit_id``, a ``msg_id`` outside u32, invalid Unicode, or a
    ``max_bytes`` too small to carry one escaped character. Callers enforce
    ``VISIT_PIECES_MAX`` themselves (see ``wire_size`` / ``fit_text_to_wire``).
    """
    r = require_visit_id(visit_id)[:8]
    if not _is_u32(msg_id):
        raise ValueError("msg_id must be a u32")
    if not isinstance(payload_json, str):
        raise ValueError("payload_json must be str")
    _check_unicode(payload_json)
    weights = [_esc1_len(c) for c in payload_json]
    for width in (1, 2, 3):
        cap = 10 ** width - 1
        overhead = len(_envelope_text(r, msg_id, cap, cap, "").encode("utf-8"))
        budget = max_bytes - overhead
        if budget < 6:
            raise ValueError("max_bytes too small for the envelope")
        bounds = _greedy_bounds(weights, budget)
        if len(bounds) <= cap:
            break
    else:
        raise ValueError("payload too large to fragment")
    n = len(bounds)
    pieces: list[bytes] = []
    for i, (a, b) in enumerate(bounds):
        data = _envelope_text(r, msg_id, i, n, payload_json[a:b]).encode("utf-8")
        if len(data) > max_bytes:  # pragma: no cover - guarded by the budget above
            raise RuntimeError("fragment exceeded max_bytes")
        pieces.append(data)
    return pieces


def wire_size(payload_json: str, *, visit_id: str, msg_id: int = _U32_MAX) -> tuple[int, int]:
    """Return ``(piece_count, total_bytes)`` of ``payload_json`` on the wire.

    ``msg_id`` defaults to the widest u32 so the result is an upper bound for
    any real id; outbox byte accounting and piece budgets use this.
    """
    pieces = fragment(payload_json, visit_id=visit_id, msg_id=msg_id)
    return len(pieces), sum(len(p) for p in pieces)


class Reassembler:
    """Receive-side reassembly of envelope pieces keyed by ``(from_vid, m)`` (section 4.1).

    ``feed`` returns the parsed payload object (``dict``) once every piece of
    a message is present (any arrival order), otherwise ``None``. Pieces are
    dropped and counted in ``dropped`` when they are oversize, not UTF-8 /
    JSON, carry a foreign ``r``, have ``i >= n`` / ``n`` outside
    ``1..max_pieces``, or disagree with the ``n`` of an existing entry (that
    entry is discarded too). The joined payload must parse to a JSON object.
    Entries older than ``timeout_s`` (from their first piece) are discarded
    and counted in both ``dropped`` and ``timeouts``; beyond ``max_entries``
    in-flight entries the oldest is evicted (``evicted`` and ``dropped``).
    A repeated piece keeps the first copy and counts in ``duplicates`` only.
    """

    def __init__(
        self,
        *,
        visit_id: str,
        timeout_s: float = VISIT_REASSEMBLY_TIMEOUT_S,
        max_entries: int = VISIT_REASSEMBLY_MAX_ENTRIES,
        max_pieces: int = VISIT_PIECES_MAX,
        max_piece_bytes: int = VISIT_PIECE_MAX_BYTES,
    ) -> None:
        self._r = require_visit_id(visit_id)[:8]
        self._timeout_s = float(timeout_s)
        self._max_entries = int(max_entries)
        self._max_pieces = int(max_pieces)
        self._max_piece_bytes = int(max_piece_bytes)
        # key → {"n": int, "pieces": list[str|None], "first_at": float}
        self._entries: "OrderedDict[tuple[str, int], dict]" = OrderedDict()
        self.dropped = 0
        self.timeouts = 0
        self.evicted = 0
        self.duplicates = 0

    @property
    def pending(self) -> int:
        """Number of in-flight (incomplete) entries."""
        return len(self._entries)

    def sweep(self, now: float) -> int:
        """Drop entries whose first piece is older than the timeout; return how many."""
        expired = [k for k, e in self._entries.items() if now - e["first_at"] > self._timeout_s]
        for k in expired:
            del self._entries[k]
        self.dropped += len(expired)
        self.timeouts += len(expired)
        return len(expired)

    def _parse(self, frag: Union[bytes, bytearray, str]) -> Optional[tuple[int, int, int, str]]:
        if isinstance(frag, str):
            data = frag.encode("utf-8", "surrogatepass")
            text = frag
        else:
            data = bytes(frag)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if len(data) > self._max_piece_bytes:
            return None
        try:
            env = json.loads(text)
        except (ValueError, RecursionError):
            # 深层嵌套（"[[[[…"）让 json.loads 抛 RecursionError：同样是一片坏数据，丢弃计数
            return None
        if not isinstance(env, dict):
            return None
        v, r, m, i, n, p = (env.get(k) for k in ("v", "r", "m", "i", "n", "p"))
        if v != _ENVELOPE_VERSION or not _is_int(v) or r != self._r:
            return None
        if not _is_u32(m) or not _is_int(n) or not _is_int(i) or not isinstance(p, str):
            return None
        if not (1 <= n <= self._max_pieces) or not (0 <= i < n):
            return None
        return m, i, n, p

    def feed(self, from_vid: str, frag: Union[bytes, bytearray, str], now: float) -> Optional[dict]:
        """Add one received piece; return the payload object when its message completes."""
        self.sweep(now)
        parsed = self._parse(frag)
        if parsed is None:
            self.dropped += 1
            return None
        m, i, n, p = parsed
        if n == 1:
            return self._finish(p)
        key = (from_vid, m)
        entry = self._entries.get(key)
        if entry is None:
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
                self.evicted += 1
                self.dropped += 1
            entry = {"n": n, "pieces": [None] * n, "first_at": now}
            self._entries[key] = entry
        elif entry["n"] != n:
            del self._entries[key]
            self.dropped += 1
            return None
        if entry["pieces"][i] is not None:
            self.duplicates += 1
            return None
        entry["pieces"][i] = p
        if any(x is None for x in entry["pieces"]):
            return None
        del self._entries[key]
        return self._finish("".join(entry["pieces"]))

    def _finish(self, joined: str) -> Optional[dict]:
        try:
            obj = json.loads(joined)
        except (ValueError, RecursionError):
            # 分片拼出的深层嵌套 payload 同样按坏消息丢弃计数，不能冲出接收处理
            self.dropped += 1
            return None
        if not isinstance(obj, dict):
            self.dropped += 1
            return None
        return obj


# ── 消息 schema（§4.2）────────────────────────────────────────────────

def _utf8_cap(limit: int) -> AfterValidator:
    def check(v: str) -> str:
        try:
            n = len(v.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("not valid Unicode") from exc
        if n > limit:
            raise ValueError(f"longer than {limit} UTF-8 bytes")
        return v
    return AfterValidator(check)


_U32 = Annotated[int, Field(ge=0, le=_U32_MAX)]
# 必达消息的 seq 从 1 起（累计 ack 可以是 0）：seq=0 的必达帧会被收端当成「已见过」静默吞掉
_Seq = Annotated[int, Field(ge=1, le=_U32_MAX)]
_Lp = Annotated[int, Field(ge=0, le=VISIT_LP_MAX)]
_IIdx = Annotated[int, Field(ge=0, le=VISIT_LINE_DELTA_MAX_I)]
_Ln = Annotated[str, Field(pattern=r"^[hg]:[0-9]{1,10}$")]
_Rt = Annotated[str, Field(pattern=r"^(?:[hg]:[0-9]{1,10})?$")]
_Sp = Literal["c", "h"]
_Ad = Literal["hc", "hh", "gc", "gh"]
_Crop = Literal["upper", "full"]
_Lang = Annotated[str, _utf8_cap(_LANG_MAX_BYTES)]
_Tier = Annotated[str, _utf8_cap(_TIER_MAX_BYTES)]
_NonNegFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_NonNegInt = Annotated[int, Field(ge=0)]


class _Msg(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    # 只认本版 payload：v 升版意味着已知字段换了语义（加字段不必升版，未知字段本就忽略），
    # 按 v1 解释会把未来版本的语义套错；必达消息走 _invalid 空操作推进 seq
    v: Annotated[int, Field(ge=_PAYLOAD_VERSION, le=_PAYLOAD_VERSION)] = _PAYLOAD_VERSION


class _HelloCaps(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    video: bool
    tier: _Tier
    proto: _NonNegInt
    app_version: Annotated[str, _utf8_cap(_APP_VERSION_MAX_BYTES)]
    crop: _Crop


class _Hello(_Msg):
    t: Literal["hello"]
    seq: _Seq
    ticket: Annotated[str, _utf8_cap(_TICKET_MAX_BYTES)]
    caps: _HelloCaps
    lang: _Lang
    jti_reuse: bool = False


class _Ready(_Msg):
    t: Literal["ready"]
    seq: _Seq


class _Ack(_Msg):
    t: Literal["ack"]
    seq: _U32


class _Hb(_Msg):
    t: Literal["hb"]
    lp_seen: _Lp
    crop: _Crop
    hidden: bool


class _State(_Msg):
    t: Literal["state"]
    hidden: bool
    crop: _Crop
    tier: _Tier
    enc: Optional[Literal["h264", "vp8", "vp9"]] = None


class _WrapUp(_Msg):
    t: Literal["wrap_up"]
    seq: _Seq
    lp: _Lp
    ph: Literal["propose", "begin", "ack", "speaking", "done"]
    ln: Optional[_Ln] = None
    reason: Literal["quiet", "budget", "recall", "time_up"]
    initiated_by: Literal["host", "guest"]

    @model_validator(mode="after")
    def _ln_only_when_speaking(self) -> "_WrapUp":
        if self.ph == "speaking":
            if self.ln is None:
                raise ValueError("wrap_up speaking requires ln")
        else:
            self.ln = None
        return self


class _Leave(_Msg):
    t: Literal["leave"]
    seq: _Seq
    last_seq: _U32
    reason: Annotated[str, Field(min_length=1), _utf8_cap(_LEAVE_REASON_MAX_BYTES)]

    @model_validator(mode="after")
    def _watermark_is_previous_seq(self) -> "_Leave":
        # last_seq 必须恰是 seq - 1：低水位会让接收方跳过缺口等待、立刻结束
        if self.last_seq != self.seq - 1:
            raise ValueError("leave.last_seq must equal seq - 1")
        return self


class _LineDelta(_Msg):
    t: Literal["line_delta"]
    ln: _Ln
    i: _IIdx
    lp: _Lp
    txt: Annotated[str, _utf8_cap(VISIT_DELTA_TEXT_MAX_BYTES)]
    sp: Optional[_Sp] = None
    ad: Optional[_Ad] = None
    rt: Optional[_Rt] = None
    wu: Optional[bool] = None

    @model_validator(mode="after")
    def _first_piece_fields(self) -> "_LineDelta":
        if self.i == 0:
            if self.sp is None or self.ad is None or self.rt is None or self.wu is None:
                raise ValueError("line_delta i==0 requires sp/ad/rt/wu")
        else:
            self.sp = self.ad = self.rt = self.wu = None
        return self


class _Text(_Msg):
    t: Literal["text"]
    ln: _Ln
    lp: _Lp
    seq: _Seq
    sp: _Sp
    ad: _Ad
    rt: _Rt
    wu: bool
    final: Literal[True]
    txt: Annotated[str, _utf8_cap(VISIT_TEXT_MAX_BYTES)]
    truncated: bool
    i_done: _IIdx
    trunc_reason: Optional[
        Annotated[str, Field(min_length=1), _utf8_cap(_TRUNC_REASON_MAX_BYTES)]
    ] = None
    lang: Optional[_Lang] = None
    tail_ms: Optional[Annotated[int, Field(ge=0, le=VISIT_CLAUSE_MAX_MS)]] = None


class _LineAbort(_Msg):
    t: Literal["line_abort"]
    ln: _Ln
    lp: _Lp
    i_done: _IIdx
    reason: Literal["human_interrupt", "wrap_up", "tts_error", "llm_error"]


class _Typing(_Msg):
    t: Literal["typing"]
    lp: _Lp
    sp: _Sp
    on: Optional[bool] = None   # 只在 VISIT_STREAM_DELTAS=False 的整句模式里带


class _Stats(_Msg):
    t: Literal["stats"]
    rx_fps: _NonNegFloat
    rx_kbps: _NonNegInt
    rtt_ms: _NonNegInt
    loss_pct: _NonNegFloat
    rx_w: _NonNegInt
    rx_h: _NonNegInt
    qlr: Optional[Literal["none", "bandwidth", "cpu", "other"]] = None


_MODELS: dict[str, type[_Msg]] = {
    "hello": _Hello,
    "ready": _Ready,
    "ack": _Ack,
    "hb": _Hb,
    "state": _State,
    "wrap_up": _WrapUp,
    "leave": _Leave,
    "line_delta": _LineDelta,
    "text": _Text,
    "line_abort": _LineAbort,
    "typing": _Typing,
    "stats": _Stats,
}
assert set(_MODELS) == set(_CMD_OF)


def cmd_of(t: str) -> int:
    """Return the channel (1 ctl / 2 text / 3 lossy) of a known message type; ``ValueError`` otherwise."""
    try:
        return _CMD_OF[t]
    except (KeyError, TypeError):
        raise ValueError(f"unknown message type: {t!r}") from None


def is_reliable(t: str) -> bool:
    """True for the outbox-backed types ``hello / ready / wrap_up / leave / text``."""
    return t in RELIABLE_TYPES


def proto_compatible(local_major: int, peer_caps: Mapping[str, Any]) -> bool:
    """True when ``peer_caps['proto']`` is an int equal to ``local_major``.

    A missing, non-integer or different major version is incompatible
    (the caller then ends with ``leave{reason:'proto_mismatch'}``).
    """
    if not isinstance(peer_caps, Mapping):
        return False
    proto = peer_caps.get("proto")
    return _is_int(proto) and proto == local_major


def _dump_model(obj: _Msg) -> dict:
    data = obj.model_dump(exclude_none=True)
    return {"t": data.pop("t"), **data}


def encode_msg(msg: Mapping[str, Any]) -> str:
    """Validate ``msg`` against its schema and return the compact payload JSON text.

    ``msg['t']`` selects the schema; ``v`` defaults to 1; ``None`` optionals
    are omitted; fields only allowed on the first ``line_delta`` piece are
    dropped when ``i > 0``. Additional checks beyond the field types:

    * ``line_delta``: the escaped form (the payload JSON as it will sit inside
      envelope ``p``) must be ``<= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES`` (900),
      which guarantees one piece (``n == 1``).
    * ``text``: ``txt`` is capped at ``VISIT_TEXT_MAX_BYTES`` but the 8-piece
      wire budget is *not* enforced here; run ``fit_text_to_wire`` first.

    Raises ``ValueError`` (unknown ``t``, schema violation, oversize delta).
    """
    if not isinstance(msg, Mapping):
        raise ValueError("message must be a mapping")
    model = _MODELS.get(msg.get("t"))  # type: ignore[arg-type]
    if model is None:
        raise ValueError(f"unknown message type: {msg.get('t')!r}")
    try:
        obj = model.model_validate(dict(msg))
    except ValidationError as exc:
        raise ValueError(f"invalid {msg.get('t')} message: {exc.errors()[0].get('msg')}") from exc
    text = _dumps(_dump_model(obj))
    if obj.t == "line_delta" and _esc_len(text) > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES:  # type: ignore[attr-defined]
        raise ValueError("line_delta exceeds the encoded payload budget")
    return text


def decode_msg(text: Union[str, bytes, Mapping[str, Any]], *, cmd: Optional[int] = None) -> dict:
    """Parse and validate one received payload; return a plain ``dict``.

    ``text`` is the payload JSON text (or an already parsed mapping, as
    delivered by ``recv{payload}``). ``cmd`` is the channel it arrived on when
    known; a ``cmd`` outside ``1..3`` is rejected, and a known type on the
    wrong channel counts as malformed. Unknown fields are ignored.

    Results:

    * Known, valid message → its fields (``None`` optionals omitted).
    * Unknown ``t`` → ``{'t': '_unknown', 'raw_t': t[:32], 'cmd': cmd,
      'seq': seq-or-None}``. On channels 1 / 2 (or unknown channel) a present
      ``seq`` is validated as u32 and kept so ``InboxSequencer`` can consume
      it as a no-op delivery and advance ack; on channel 3 ``seq`` is None.
    * Known reliable type that fails validation but carries a valid u32
      ``seq`` → ``{'t': '_invalid', 'raw_t', 'cmd', 'seq', 'error'}``: the
      caller counts an anomaly and still consumes the ``seq`` (no permanent
      gap), mirroring the rule for ``ln``-prefix mismatches.
    * ``hello`` whose ``caps.proto`` is an int different from
      ``VISIT_WIRE_PROTO`` → minimal ``{'t': 'hello', 'v', 'seq', 'caps':
      {'proto': p}}`` without validating the rest, so the caller can answer
      ``leave{reason:'proto_mismatch'}`` even if the newer schema differs.
    * ``text`` with ``tail_ms`` that is not an int in ``0..VISIT_CLAUSE_MAX_MS``
      → ``tail_ms`` becomes 0 and ``'_soft_anomalies': ['tail_ms']`` is added;
      the message itself stays valid. Absent ``tail_ms`` means 0.
    * ``line_delta`` whose payload is over 900 bytes is malformed.

    Anything else malformed raises ``ValueError`` (drop + anomaly count).
    Stateful checks (``lp`` jumps, ``ln`` prefix binding, duplicates, ``i``
    reuse) belong to the caller.
    """
    if cmd is not None and cmd not in (CMD_CTL, CMD_TEXT, CMD_LOSSY):
        raise ValueError(f"unknown cmd {cmd!r}")
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("payload is not UTF-8") from exc
    if isinstance(text, str):
        try:
            raw = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise ValueError("payload is not JSON") from exc
    elif isinstance(text, Mapping):
        raw = dict(text)
    else:
        raise ValueError("payload must be str, bytes or a mapping")
    if not isinstance(raw, dict):
        raise ValueError("payload is not a JSON object")
    t = raw.get("t")
    if not isinstance(t, str):
        raise ValueError("payload has no string 't'")

    model = _MODELS.get(t)
    if model is None:
        seq = None
        if cmd != CMD_LOSSY and "seq" in raw:
            seq = raw["seq"]
            if not _is_seq(seq):
                raise ValueError("unknown message with malformed seq")
        return {"t": "_unknown", "raw_t": t[:_RAW_T_DIAG_MAX], "cmd": cmd, "seq": seq}

    expected = _CMD_OF[t]
    problem: Optional[str] = None
    soft: list[str] = []
    obj: Optional[_Msg] = None
    if cmd is not None and cmd != expected:
        problem = "wrong channel"
    elif t == "hello":
        caps = raw.get("caps")
        if isinstance(caps, dict) and _is_int(caps.get("proto")) and caps["proto"] != VISIT_WIRE_PROTO:
            if not _is_seq(raw.get("seq")):
                raise ValueError("hello with malformed seq")
            v = raw.get("v")
            return {
                "t": "hello",
                "v": v if _is_int(v) and v >= 1 else _PAYLOAD_VERSION,
                "seq": raw["seq"],
                "caps": {"proto": caps["proto"]},
            }
    elif t == "text" and "tail_ms" in raw:
        tail = raw["tail_ms"]
        if not (_is_int(tail) and 0 <= tail <= VISIT_CLAUSE_MAX_MS):
            raw["tail_ms"] = 0
            soft.append("tail_ms")
    elif t == "line_delta":
        try:
            # 与 encode_msg / line_delta_encoded_len 同一口径：payload JSON 再作为信封 p
            # 转义一次后的字节；只量 JSON 会放过引号 / 反斜杠密集、实际要分多片的 delta
            size = _esc_len(_dumps(raw))
        except (ValueError, TypeError, UnicodeEncodeError):
            size = VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES + 1
        if size > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES:
            problem = "line_delta payload over budget"

    if problem is None:
        try:
            obj = model.model_validate(raw)
        except ValidationError as exc:
            problem = str(exc.errors()[0].get("msg"))

    if problem is not None or obj is None:
        if t in RELIABLE_TYPES and _is_seq(raw.get("seq")):
            return {"t": "_invalid", "raw_t": t, "cmd": expected, "seq": raw["seq"],
                    "error": problem or "invalid"}
        raise ValueError(f"malformed {t}: {problem}")

    out = _dump_model(obj)
    if soft:
        out["_soft_anomalies"] = soft
    return out


# ── wire 预算（以完全编码后字节为准）──────────────────────────────────

def _widest_delta_payload(txt: str) -> dict:
    # i==0 的附加字段全部在场、各字段取最大宽度
    return {
        "t": "line_delta", "v": _PAYLOAD_VERSION, "ln": _WIDEST_LN,
        "i": VISIT_LINE_DELTA_MAX_I, "lp": VISIT_LP_MAX, "txt": txt,
        "sp": "c", "ad": "hc", "rt": _WIDEST_LN, "wu": False,
    }


def line_delta_encoded_len(txt: str) -> int:
    """Fully encoded size budget of one ``line_delta`` carrying ``txt``.

    Builds the widest possible payload (``i==0`` extras present, ``ln`` / ``rt``
    at 12 characters, ``i`` at 3 digits, ``lp`` at ``VISIT_LP_MAX``,
    ``wu:false``), serialises it as compact JSON, then measures the UTF-8
    bytes of that JSON once more escaped as the envelope ``p`` string (the
    surrounding quotes excluded). Every real delta of the same ``txt`` is at
    most this long, so ``<= 900`` here means ``n == 1`` on the wire.

    The value is additive in ``txt``: ``len(a + b) == len(a) + len(b) -
    len('')``. ``ClauseSplitter`` and the outbox delta merging (250 ms and
    backlog merges) both judge with this function, never with raw bytes.
    """
    return _esc_len(_dumps(_widest_delta_payload(txt)))


_DELTA_OVERHEAD = line_delta_encoded_len("")


def line_delta_can_merge(a: str, b: str) -> bool:
    """True when ``a + b`` still fits one delta (raw ``<= 800`` B and encoded ``<= 900`` B)."""
    joined = a + b
    return (len(joined.encode("utf-8")) <= VISIT_DELTA_TEXT_MAX_BYTES
            and line_delta_encoded_len(joined) <= VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES)


def widest_text_payload(header: Mapping[str, Any], txt: str) -> dict:
    """The ``text{final}`` payload used for wire budgeting of a line still being produced.

    Fields the sender cannot know yet are taken at their widest: ``lp =
    VISIT_LP_MAX``, ``seq = 2**32-1``, ``i_done = 255``, ``tail_ms =
    VISIT_CLAUSE_MAX_MS``, ``truncated = False`` together with the longest
    ``trunc_reason``. Keys present in ``header`` (``ln``, ``sp``, ``ad``,
    ``rt``, ``wu``, ``lang``) are used as given; missing ones are widened
    (``ln`` / ``rt`` 12 characters, ``lang`` 16 characters, ``wu:false``).
    A ``lang`` of ``None`` in ``header`` means the field is not sent.
    """
    payload = {
        "t": "text", "v": _PAYLOAD_VERSION,
        "ln": header.get("ln", _WIDEST_LN),
        "lp": VISIT_LP_MAX, "seq": _U32_MAX,
        "sp": header.get("sp", "c"), "ad": header.get("ad", "hc"),
        "rt": header.get("rt", _WIDEST_LN), "wu": header.get("wu", False),
        "final": True, "txt": txt, "truncated": False,
        "i_done": VISIT_LINE_DELTA_MAX_I, "trunc_reason": _WIDEST_TRUNC_REASON,
        "tail_ms": VISIT_CLAUSE_MAX_MS,
    }
    lang = header.get("lang", "x" * _LANG_MAX_BYTES)
    if lang is not None:
        payload["lang"] = lang
    return payload


def _text_pieces(payload: Mapping[str, Any], visit_id: str) -> int:
    body = dict(payload)
    body.setdefault("seq", _U32_MAX)
    return wire_size(encode_msg(body), visit_id=visit_id)[0]


SILENCING_TRUNC_REASONS = frozenset({"human_interrupt", "wrap_up", "visit_end"})
"""Truncation reasons meaning "cut off on purpose, do not answer this line"."""


def fit_text_to_wire(
    payload: Mapping[str, Any],
    *,
    visit_id: str,
    max_pieces: int = VISIT_PIECES_MAX,
    on_truncate: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Return a copy of a ``text`` payload that is guaranteed to fit ``max_pieces`` pieces.

    Pieces are counted on the final envelope form (payload JSON plus the
    second escaping as ``p``; ``m`` at its widest, ``seq`` at its widest when
    still missing). A fitting payload is returned unchanged (as a new dict).
    Otherwise ``txt`` is cut to the longest prefix that fits — on a codepoint
    boundary, also avoiding split emoji clusters — with ``truncated=True`` and
    ``trunc_reason='wire_size'`` included in the measurement (an existing
    reason from ``SILENCING_TRUNC_REASONS`` is kept instead, so the peer still
    does not answer the line; the full text is kept when it fits once the
    reason is set), and a
    diagnostic is emitted (``on_truncate`` callback with byte counts, plus a
    log warning).

    Used as the primary cap for human lines (after ``clamp_text_utf8(4096)``)
    and as the last-resort cap for cat lines, whose normal budget is
    ``WireBudget``. Raises ``ValueError`` if ``payload`` is not a valid
    ``text`` or cannot fit even with an empty ``txt``.
    """
    if payload.get("t") != "text":
        raise ValueError("fit_text_to_wire only handles text payloads")
    base = dict(payload)
    pieces_before = _text_pieces(base, visit_id)
    if pieces_before <= max_pieces:
        return base
    txt = str(base.get("txt", ""))
    # 已因「不该接话」的原因截断（人类插话 / 收尾 / 整场结束）时保留原因：改成 wire_size
    # 会让对端把这句当成说完的一轮去接话
    reason = base.get("trunc_reason")
    if not (base.get("truncated") is True and reason in SILENCING_TRUNC_REASONS):
        reason = "wire_size"
    trial = dict(base, truncated=True, trunc_reason=reason)

    def fits(k: int) -> bool:
        trial["txt"] = txt[:k]
        return _text_pieces(trial, visit_id) <= max_pieces

    if not fits(0):
        raise ValueError("text payload does not fit the wire even when empty")
    lo, hi = 0, len(txt)
    if fits(hi):
        lo = hi            # 只换了截断原因（更短）就放得下：整句保留
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid
    k = lo if lo == len(txt) else _safe_cut(txt, lo, floor=0)
    result = dict(base, txt=txt[:k], truncated=True, trunc_reason=reason)
    diag = {
        "event": "visit_text_wire_size",
        "ln": base.get("ln"),
        "pieces_before": pieces_before,
        "orig_bytes": len(txt.encode("utf-8")),
        "kept_bytes": len(txt[:k].encode("utf-8")),
    }
    logger.warning("visit text truncated to fit the wire: %s", diag)
    if on_truncate is not None:
        on_truncate(diag)
    return result


# ── 脱敏注入契约 ──────────────────────────────────────────────────────

RedactSpans = Sequence[tuple[int, int, int, int]]
RedactResult = Union[str, tuple[str, RedactSpans]]
RedactFn = Callable[[str], RedactResult]
RedactBoundary = Callable[[str], bool]


def _identity(s: str) -> str:
    return s


# 只用 C 层字符串操作量长度：流式热路径上不做逐字符的 Python 循环
_SURROGATE_RE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_CONTROL_RE = re.compile("[" + chr(0) + "-" + chr(0x1F) + "]")


def _utf8_len(s: str) -> int:
    """UTF-8 bytes of ``s``; a lone surrogate counts 3 like ``_utf8_len_char``."""
    return len(s.encode("utf-8", "surrogatepass"))


def _esc2_text_len(s: str) -> int:
    """``sum(_esc2_len(c) for c in s)`` computed with C-level string operations."""
    n = _utf8_len(s) + 3 * (s.count('"') + s.count("\\"))
    for ch in _CONTROL_RE.findall(s):
        n += _esc2_len(ch) - 1
    return n


class _OffsetMap:
    """Maps positions between the raw buffer and its redacted form.

    ``spans`` are ``(raw_start, raw_end, red_start, red_end)`` replacement
    regions, ascending and disjoint; outside them both strings are identical.
    Lookups bisect the span starts. ``extend_shifted`` / ``truncate`` let a
    map of committed segments carry a temporary tail.
    """

    def __init__(self, raw: str, red: str, spans: RedactSpans) -> None:
        self._rs: list[int] = []
        self._re: list[int] = []
        self._ds: list[int] = []
        self._de: list[int] = []
        prev_raw = prev_red = 0
        for span in spans:
            rs, re_, ds, de = (int(x) for x in span)
            if not (prev_raw <= rs <= re_ <= len(raw) and prev_red <= ds <= de <= len(red)):
                raise ValueError("redact spans must be ascending, disjoint and in range")
            if raw[prev_raw:rs] != red[prev_red:ds]:
                raise ValueError("redact spans leave unequal text between replacements")
            self._push(rs, re_, ds, de)
            prev_raw, prev_red = re_, de
        if raw[prev_raw:] != red[prev_red:]:
            raise ValueError("redact spans leave unequal trailing text")

    def __len__(self) -> int:
        return len(self._rs)

    def _push(self, rs: int, re_: int, ds: int, de: int) -> None:
        self._rs.append(rs)
        self._re.append(re_)
        self._ds.append(ds)
        self._de.append(de)

    def extend_shifted(self, other: "_OffsetMap", raw_off: int, red_off: int) -> None:
        """Append ``other``'s spans moved to start at ``(raw_off, red_off)``."""
        for rs, re_, ds, de in zip(other._rs, other._re, other._ds, other._de):
            self._push(rs + raw_off, re_ + raw_off, ds + red_off, de + red_off)

    def truncate(self, count: int) -> None:
        """Keep only the first ``count`` spans."""
        for lst in (self._rs, self._re, self._ds, self._de):
            del lst[count:]

    # 三个查询等价于按顺序线性扫描：起点 < pos 的区间是一段前缀，其中只有最后一个可能盖住 pos
    def red_to_raw(self, pos: int) -> int:
        k = bisect_left(self._ds, pos) - 1
        if k < 0:
            return pos
        if pos < self._de[k]:
            return self._re[k]
        return pos + self._re[k] - self._de[k]

    def raw_to_red(self, pos: int) -> int:
        k = bisect_left(self._rs, pos) - 1
        if k < 0:
            return pos
        if pos < self._re[k]:
            return self._de[k]
        return pos + self._de[k] - self._re[k]

    def red_span_around(self, pos: int) -> Optional[tuple[int, int]]:
        """The replacement span strictly containing redacted position ``pos``, if any."""
        k = bisect_left(self._ds, pos) - 1
        if k >= 0 and pos < self._de[k]:
            return self._ds[k], self._de[k]
        return None


def _map_redaction(raw: str, result: Any, *, derive: bool) -> tuple[str, _OffsetMap]:
    if isinstance(result, tuple):
        red, spans = result
        if not isinstance(red, str):
            raise ValueError("redact must return str or (str, spans)")
        return red, _OffsetMap(raw, red, spans)
    if not isinstance(result, str):
        raise ValueError("redact must return str or (str, spans)")
    if result == raw:
        return result, _OffsetMap(raw, result, ())
    if not derive:
        # difflib 在整句上推出的区间与分段推出的不一定相同，分段模式只能接受自报区间
        raise ValueError("redact_boundary requires redact to return (text, spans)")
    matcher = difflib.SequenceMatcher(None, raw, result, autojunk=False)
    spans = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]
    return result, _OffsetMap(raw, result, spans)


def _run_redact(fn: RedactFn, raw: str) -> tuple[str, _OffsetMap]:
    return _map_redaction(raw, fn(raw), derive=True)


def _run_redact_segment(fn: RedactFn, raw: str) -> tuple[str, _OffsetMap]:
    return _map_redaction(raw, fn(raw), derive=False)


def _redacted_only(fn: RedactFn, raw: str) -> str:
    result = fn(raw)
    if isinstance(result, tuple):
        result = result[0]
    if not isinstance(result, str):
        raise ValueError("redact must return str or (str, spans)")
    return result


class _SegmentedRedaction:
    """Redaction of a growing buffer that restarts after boundary characters.

    Relies on the ``redact_boundary`` contract: for any ``a + b`` where ``a``
    ends with a character ``boundary`` accepts, ``redact(a + b)`` is
    ``redact(a)`` followed by ``redact(b)`` (texts concatenated, spans of
    ``b`` shifted). The buffer is kept as committed segments, each ending with
    such a character, plus an open tail; only the tail is redacted again.
    ``red`` / ``map`` hold the committed part (``map`` only when
    ``with_spans``).
    """

    def __init__(self, fn: RedactFn, boundary: RedactBoundary, *, with_spans: bool) -> None:
        self._fn = fn
        self._boundary = boundary
        self._with_spans = with_spans
        self.raw_end = 0          # raw[:raw_end] 已提交
        self.red = ""             # redact(raw[:raw_end]) 的文本
        self.map = _OffsetMap("", "", ())
        self._scanned = 0         # raw[:_scanned] 已查过边界字符
        self._cut = 0             # raw[:_scanned] 里最后一个边界字符之后的位置

    def advance(self, raw: str) -> None:
        """Commit ``raw`` (an extension of every earlier argument) up to its last boundary."""
        for i in range(self._scanned, len(raw)):
            if self._boundary(raw[i]):
                self._cut = i + 1
        self._scanned = len(raw)
        if self._cut > self.raw_end:
            seg = raw[self.raw_end:self._cut]
            if self._with_spans:
                red, omap = _run_redact_segment(self._fn, seg)
                self.map.extend_shifted(omap, self.raw_end, len(self.red))
            else:
                red = _redacted_only(self._fn, seg)
            self.red += red
            self.raw_end = self._cut

    def tail_text(self, raw: str, extra: str = "") -> str:
        """Redacted text of ``raw[raw_end:] + extra``."""
        seg = raw[self.raw_end:] + extra
        return _redacted_only(self._fn, seg) if seg else ""

    def tail(self, raw: str) -> tuple[str, _OffsetMap]:
        """Redacted text and offset map of ``raw[raw_end:]``."""
        seg = raw[self.raw_end:]
        if not seg:
            # 契约：redact(a + '') == redact(a)，所以空尾的脱敏结果只能是空
            return "", _OffsetMap("", "", ())
        return _run_redact_segment(self._fn, seg)


class WireBudget:
    """Wire budget of one cat line, applied where LLM deltas enter (before TTS).

    ``take(delta)`` appends ``delta`` to the line's raw buffer only as far as
    the *outbound* form still fits: ``sanitize(redact(buffer))`` must stay
    ``<= VISIT_TEXT_MAX_BYTES`` UTF-8 bytes and, placed in
    ``widest_text_payload(header, ...)``, encode to ``<= max_pieces`` envelope
    pieces. It returns the accepted prefix of ``delta`` (codepoint boundary,
    emoji clusters kept whole); the caller feeds exactly that prefix to TTS
    and to ``ClauseSplitter``. The first time a delta is cut, ``exhausted``
    becomes True and every later ``take`` returns ``''``: the caller finishes
    the TTS stream, cancels the LLM and marks the line ``wire_size``.

    ``redact`` / ``sanitize`` are injected (``redact_outbound`` /
    ``sanitize_relay_text`` live in ``main_logic``). ``redact`` may return a
    plain string or the ``(text, spans)`` tuple described on
    ``ClauseSplitter``; only the text is used here. A ``sanitize`` that caps
    its output (``sanitize_relay_text`` cuts by tokens and bytes) must come
    with ``clean``, the same chain without the caps (``clean_relay_text``):
    a candidate whose sanitized form differs from its cleaned form was cut,
    so it does not fit. Without it the budget would only ever measure the
    already-cut text and keep accepting deltas TTS would speak but the final
    ``text`` would not carry. The search assumes the
    outbound size grows with the buffer; if a redaction makes it shrink, the
    accepted prefix is still guaranteed to fit, merely not maximal.

    ``redact_boundary`` (optional) is the restart predicate described on
    ``ClauseSplitter``; with it only the text after the last accepted
    boundary character is redacted again for each candidate. Only the text
    half of that contract is used here, so a plain-string ``redact`` works.

    Cost: every answer equals measuring the whole candidate, but the work
    is kept proportional to the delta where that is provable. Piece counts
    use an exact additive escaped length and a greedy-packing bound, so
    ``fragment`` only runs within a few dozen bytes of the piece limit. With
    the default identity ``redact`` / ``sanitize`` and no ``clean`` the
    outbound form is the buffer itself and its sizes are tracked
    incrementally. Injected ``sanitize`` / ``clean`` are black boxes and
    still see the whole buffer per candidate (a token cap has no sound
    incremental bound: appending text can re-merge earlier BPE tokens).
    """

    def __init__(
        self,
        *,
        visit_id: str,
        header: Mapping[str, Any],
        max_pieces: int = VISIT_PIECES_MAX,
        redact: RedactFn = _identity,
        sanitize: Callable[[str], str] = _identity,
        clean: Optional[Callable[[str], str]] = None,
        redact_boundary: Optional[RedactBoundary] = None,
    ) -> None:
        self._visit_id = require_visit_id(visit_id)
        self._header = dict(header)
        self._max_pieces = int(max_pieces)
        self._redact = redact
        self._sanitize = sanitize
        self._clean = clean
        self._raw = ""
        self.exhausted = False
        self._plain = redact is _identity and sanitize is _identity and clean is None
        self._seg = (
            _SegmentedRedaction(redact, redact_boundary, with_spans=False)
            if redact is not _identity and redact_boundary is not None else None
        )
        self._raw_bytes = 0       # 仅 plain 模式：已接受文本的 UTF-8 字节
        self._raw_esc = 0         # 仅 plain 模式：已接受文本两次转义后的字节
        # 出站文本两次转义后不超过它就一定 <= max_pieces 片；首次完整测量成功后才设
        # （header 不合法时 encode_msg 抛错的时机保持与逐次完整测量相同）
        self._esc_cap: Optional[int] = None

    @property
    def accepted_text(self) -> str:
        """Raw text accepted so far (what TTS speaks)."""
        return self._raw

    def outbound_text(self) -> str:
        """``sanitize(redact(accepted_text))``: the outbound form that was budgeted."""
        return self._sanitize(_redacted_only(self._redact, self._raw))

    def _certain_esc_cap(self) -> int:
        # fragment 贪心装片：除最后一片外每片装了 > budget - 6 字节（单字符转义后最多 6 B），
        # 所以 n 片意味着 W >= (n-1)(budget-5)+1；W <= max_pieces*(budget-5) 时 n <= max_pieces。
        # budget 取三位数 i/n 时最小的那个，任一位宽下都成立
        if not 1 <= self._max_pieces <= 999:
            return -1
        overhead = len(_envelope_text(self._visit_id[:8], _U32_MAX, 999, 999, "").encode("utf-8"))
        budget = VISIT_PIECE_MAX_BYTES - overhead
        base = _esc_len(encode_msg(widest_text_payload(self._header, "")))
        return self._max_pieces * (budget - 5) - base

    def _pieces_fit(self, out: str) -> bool:
        pieces = _text_pieces(widest_text_payload(self._header, out), self._visit_id)
        if self._esc_cap is None:
            self._esc_cap = self._certain_esc_cap()
        return pieces <= self._max_pieces

    def _fits(self, tail: str) -> bool:
        """Whether ``accepted_text + tail`` fits; the same answer as a full measurement."""
        if self._plain and self._esc_cap is not None and _SURROGATE_RE.search(tail) is None:
            if self._raw_bytes + _utf8_len(tail) > VISIT_TEXT_MAX_BYTES:
                return False
            if self._raw_esc + _esc2_text_len(tail) <= self._esc_cap:
                return True
            return self._pieces_fit(self._raw + tail)
        if self._redact is _identity:
            redacted = self._raw + tail
        elif self._seg is not None:
            redacted = self._seg.red + self._seg.tail_text(self._raw, tail)
        else:
            redacted = _redacted_only(self._redact, self._raw + tail)
        out = self._sanitize(redacted)
        if self._clean is not None and out != self._clean(redacted):
            return False                      # sanitize 自己截掉了内容
        if len(out.encode("utf-8")) > VISIT_TEXT_MAX_BYTES:
            return False
        if self._esc_cap is not None and _esc2_text_len(out) <= self._esc_cap:
            return True
        return self._pieces_fit(out)

    def _accept(self, text: str) -> None:
        self._raw += text
        if self._plain:
            self._raw_bytes += _utf8_len(text)
            self._raw_esc += _esc2_text_len(text)
        if self._seg is not None:
            self._seg.advance(self._raw)

    def take(self, delta: str) -> str:
        """Accept as much of ``delta`` as the line's wire budget allows; see the class doc."""
        if self.exhausted or not delta:
            return ""
        if self._fits(delta):
            self._accept(delta)
            return delta
        lo, hi = 0, len(delta)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self._fits(delta[:mid]):
                lo = mid
            else:
                hi = mid
        combined = self._raw + delta
        k = _safe_cut(combined, len(self._raw) + lo, floor=len(self._raw)) - len(self._raw)
        accepted = delta[:k]
        self._accept(accepted)
        self.exhausted = True
        return accepted


# ── 分句（§3.6.4 / d4 §2.1）──────────────────────────────────────────

_END_MARKS = frozenset("。！？；…!?;.")
_ASCII_END_MARKS = frozenset("!?;.")
_COMMA_MARKS = frozenset("，,、")
_CLOSERS = frozenset("」』”’\"')）]】》〉")
_SOFT_BREAK_CHARS = _END_MARKS | _COMMA_MARKS | _CLOSERS | frozenset("：:")


@dataclass(frozen=True)
class Clause:
    """One subtitle clause: ``text`` is the redacted slice (before emotion stripping and
    sanitising), ``raw`` the matching slice of the original line that TTS speaks."""

    text: str
    raw: str


def _hard_cut(pending: str, k: int, holdback: int) -> int:
    """Cut for a clause whose first ``k`` characters fit and the ``k+1``-th does not."""
    limit = k - holdback if k - holdback >= 1 else k
    limit = max(1, limit)
    floor = max(1, limit // 2)
    for p in range(limit, floor - 1, -1):
        prev = pending[p - 1]
        if (prev.isspace() or prev in _SOFT_BREAK_CHARS) and not _splits_cluster(pending, p):
            return p
    return _safe_cut(pending, limit, floor=1)


class _CutScanner:
    """Finds the length of the next clause at the head of ``pending``.

    The decision only depends on the characters up to the boundary plus one
    character of lookahead (or up to the first character that overflows the
    delta budget), so feeding a line in any chunking yields the same cuts.
    The scan is a left-to-right state machine; when it ends without a cut
    the state is kept, and the next call resumes from it if the new
    ``pending`` starts with the text already scanned (otherwise it starts
    over). Results are identical to a fresh scan.
    """

    __slots__ = ("_text", "_state")

    def __init__(self) -> None:
        self._text = ""
        self._state: tuple = ()

    def next_cut(self, pending: str, *, final: bool, holdback: int) -> Optional[int]:
        """Length of the next clause, or None to wait for more text."""
        # 只有没下刀（即没超 VISIT_DELTA_TEXT_MAX_BYTES）才保存状态，所以 _text 不超过
        # 800 个字符：这次前缀比较是与整句长度无关的定长 memcmp
        if self._text and pending.startswith(self._text):
            (start, raw_bytes, enc, cjk, words, in_word, in_token, token_space,
             token_ascii, first_ns, last_ns) = self._state
        else:
            start = raw_bytes = cjk = words = 0
            enc = _DELTA_OVERHEAD
            in_word = False
            in_token = False       # 处在句末 / 换行 / 合格逗号之后的边界记号里
            token_space = False    # 记号里已出现空白（之后只吸收空白）
            token_ascii = False    # 记号目前只有 ASCII 句末符（"3.14" 之类不算边界）
            first_ns = last_ns = -1   # 已扫部分首 / 末个非空白字符：len(pending[:k].strip()) 的增量形式
        self._text = ""
        n = len(pending)
        for k in range(start, n):
            ch = pending[k]
            if in_token:
                continues = ch.isspace() or (not token_space and (
                    ch in _END_MARKS or ch in _COMMA_MARKS or ch in _CLOSERS))
                if continues:
                    if ch.isspace():
                        token_space = True
                    elif ch not in _ASCII_END_MARKS:
                        token_ascii = False
                else:
                    in_token = False
                    decimal_like = token_ascii and not token_space and ch.isascii() and ch.isalnum()
                    stripped = last_ns - first_ns + 1 if first_ns >= 0 else 0
                    if not decimal_like and stripped >= VISIT_CLAUSE_MIN_CHARS:
                        return k
            if not ch.isspace():
                if first_ns < 0:
                    first_ns = k
                last_ns = k
            b = _utf8_len_char(ch)
            w = _esc2_len(ch)
            if raw_bytes + b > VISIT_DELTA_TEXT_MAX_BYTES or enc + w > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES:
                return _hard_cut(pending, k, holdback)
            raw_bytes += b
            enc += w
            if in_token:
                continue
            if ch in _END_MARKS or ch == "\n":
                in_token = True
                token_space = ch == "\n"
                token_ascii = ch in _ASCII_END_MARKS
                in_word = False
            elif ch in _COMMA_MARKS and (cjk >= VISIT_CLAUSE_SOFT_MAX_CHARS
                                         or words >= VISIT_CLAUSE_SOFT_MAX_LATIN_WORDS):
                in_token = True
                token_space = False
                token_ascii = False
                in_word = False
            elif is_cjk_char(ch):
                cjk += 1
                in_word = False
            elif ch.isalnum():
                if not in_word:
                    words += 1
                    in_word = True
            else:
                in_word = False
        if final and n > 0:
            return n
        if not final:
            self._text = pending
            self._state = (n, raw_bytes, enc, cjk, words, in_word, in_token, token_space,
                           token_ascii, first_ns, last_ns)
        return None


class ClauseSplitter:
    """Incremental ``split_clauses`` over one line's LLM delta stream (subtitle alignment only).

    Boundaries: a run of sentence-ending marks (``。！？；…`` and ASCII
    ``. ! ? ;``, an ASCII mark directly followed by an ASCII letter / digit
    does not count) or a newline, together with trailing closers and
    whitespace; a comma / enumeration mark (``，,、``) only once the clause
    holds ``VISIT_CLAUSE_SOFT_MAX_CHARS`` CJK characters or
    ``VISIT_CLAUSE_SOFT_MAX_LATIN_WORDS`` words. A would-be clause shorter
    than ``VISIT_CLAUSE_MIN_CHARS`` (after strip) is merged into the next.
    Hard cap: a clause never exceeds ``VISIT_DELTA_TEXT_MAX_BYTES`` raw bytes
    nor ``line_delta_encoded_len(clause) > VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES``
    (judged on the fully encoded form); on overflow it is cut at a codepoint
    boundary, preferably after punctuation or whitespace in the second half,
    otherwise not inside an emoji cluster.

    Redaction comes first: before cutting, ``redact`` is applied to the whole
    raw buffer of the line and clauses are cut from its result, so a
    protected word is never split across clauses before being replaced. For
    hard cuts the last ``holdback_chars`` characters before the overflow
    point are not released in that clause (they start the next one); pass
    ``max(len(protected word)) - 1`` so a protected word whose tail has not
    arrived yet cannot be cut. Punctuation boundaries get no holdback: a
    caller whose protected words may contain clause punctuation or spaces
    (``"J. Smith"``) must keep a trailing prefix of such a word out of
    ``feed`` until it is complete or the line ends.

    Redact injection contract (``redact(raw_buffer)``):

    * Return ``(redacted, spans)`` where ``spans`` is a sequence of
      ``(raw_start, raw_end, red_start, red_end)`` replacement regions,
      ascending and disjoint in both strings; outside the spans the two
      strings must be identical (validated, ``ValueError`` otherwise).
      Zero-width spans express pure insertions or deletions.
    * Or return a plain ``str``: identical to the input means no change;
      otherwise the spans are derived with ``difflib`` (correct but slower,
      prefer returning spans).
    * It is called with ever longer prefixes of the same line and must be
      prefix-stable on the part already released (true for whole-word
      replacement once the holdback above is respected).

    ``redact_boundary`` (optional) is a predicate on single characters: it
    may accept ``ch`` only if, for any ``a`` ending with ``ch`` and any
    ``b``, ``redact(a + b)`` equals ``redact(a)`` followed by ``redact(b)``
    (texts concatenated, spans of ``b`` shifted by ``len(a)`` and
    ``len(redact(a)[0])``; ``redact('')`` is empty).
    ``main_logic.visit.sanitize.redact_outbound_boundary`` builds it for
    ``redact_outbound_with_spans``. With it the buffer is redacted in
    segments that end at accepted characters and only the open tail is
    redacted again on each ``feed``; ``redact`` must then report spans for
    any text it changes (a plain changed string raises ``ValueError``). The
    clauses are the same as with whole-buffer redaction. Without it every
    ``feed`` redacts the whole buffer (with ``difflib`` for plain strings).
    The default identity ``redact`` does no redaction work at all, and the
    boundary scan resumes where the previous ``feed`` stopped, so the cost
    of a ``feed`` follows the delta and the unreleased tail, not the line.

    ``feed(delta)`` returns the clauses completed by this delta; ``flush()``
    returns the rest and closes the splitter (``feed`` afterwards raises
    ``RuntimeError``). Each ``Clause`` carries ``raw``, the slice of the
    original line it corresponds to: schedule subtitle release with
    ``estimate_speech_ms(raw)`` because TTS speaks the original. Invariants:
    ``''.join(c.raw) == line``; with identity redaction
    ``''.join(c.text) == line`` and the cuts equal ``split_clauses(line)``
    for any chunking. If the tail of a line is removed entirely by
    redaction, ``flush`` returns a final clause with empty ``text`` so the
    ``raw`` invariant still holds.
    """

    def __init__(
        self,
        *,
        redact: RedactFn = _identity,
        holdback_chars: int = 0,
        redact_boundary: Optional[RedactBoundary] = None,
    ) -> None:
        if holdback_chars < 0:
            raise ValueError("holdback_chars must be >= 0")
        self._redact = redact
        self._holdback = int(holdback_chars)
        self._raw = ""
        self._emitted_raw = 0
        self._closed = False
        self._scanner = _CutScanner()
        self._seg = (
            _SegmentedRedaction(redact, redact_boundary, with_spans=True)
            if redact is not _identity and redact_boundary is not None else None
        )

    def feed(self, delta: str) -> list[Clause]:
        """Append ``delta``; return the clauses it completes (possibly empty)."""
        if self._closed:
            raise RuntimeError("ClauseSplitter already flushed")
        if not delta:
            return []
        self._raw += delta
        return self._drain(final=False)

    def flush(self) -> list[Clause]:
        """Release everything still buffered (end of line) and close the splitter."""
        if self._closed:
            return []
        out = self._drain(final=True)
        self._closed = True
        return out

    def _drain(self, *, final: bool) -> list[Clause]:
        seg = self._seg
        if self._redact is _identity:
            omap: Optional[_OffsetMap] = None
            e_red = self._emitted_raw
            pending = self._raw[e_red:]
        elif seg is None:
            red, omap = _run_redact(self._redact, self._raw)
            e_red = omap.raw_to_red(self._emitted_raw)
            pending = red[e_red:]
        else:
            seg.advance(self._raw)
            tail_red, tail_map = seg.tail(self._raw)
            omap = seg.map
            committed = len(omap)
            omap.extend_shifted(tail_map, seg.raw_end, len(seg.red))
            try:
                e_red = omap.raw_to_red(self._emitted_raw)
                if e_red >= len(seg.red):
                    pending = tail_red[e_red - len(seg.red):]
                else:
                    pending = seg.red[e_red:] + tail_red
                return self._cut(e_red, pending, omap, final)
            finally:
                omap.truncate(committed)
        return self._cut(e_red, pending, omap, final)

    def _cut(self, base: int, pending: str, omap: Optional[_OffsetMap], final: bool) -> list[Clause]:
        # pending 是脱敏全文从 base 起的部分；omap 为 None 表示未脱敏（位置一一对应）
        out: list[Clause] = []
        e_red = base
        while e_red - base < len(pending):
            rest = pending[e_red - base:] if e_red > base else pending
            cut = self._scanner.next_cut(rest, final=final, holdback=self._holdback)
            if cut is None:
                break
            if omap is not None:
                span = omap.red_span_around(e_red + cut)
                if span is not None:
                    # 不在替换词中间下刀：优先切在替换词之前，否则整词带走
                    cut = span[0] - e_red if span[0] > e_red else span[1] - e_red
            new_red = e_red + cut
            new_raw = new_red if omap is None else omap.red_to_raw(new_red)
            new_raw = max(new_raw, self._emitted_raw)
            out.append(Clause(text=pending[e_red - base:new_red - base],
                              raw=self._raw[self._emitted_raw:new_raw]))
            e_red = new_red
            self._emitted_raw = new_raw
        if final and self._emitted_raw < len(self._raw):
            out.append(Clause(text="", raw=self._raw[self._emitted_raw:]))
            self._emitted_raw = len(self._raw)
        return out


def split_clauses(line: str) -> list[str]:
    """Split a whole line into subtitle clauses; ``''.join(result) == line``.

    Same rules as ``ClauseSplitter`` (identity redaction, no holdback); an
    empty line yields ``[]``.
    """
    splitter = ClauseSplitter()
    clauses = splitter.feed(line) + splitter.flush()
    return [c.text for c in clauses]


# ── 文本估时（§3.6.4 / d4 §3.4）────────────────────────────────────────

_WORD_JOINERS = frozenset(".,'’")


def estimate_speech_ms(text: str) -> int:
    """Estimated speaking time of ``text`` in ms, clamped to ``[VISIT_CLAUSE_MIN_MS, VISIT_CLAUSE_MAX_MS]``.

    ``VISIT_CJK_MS_PER_CHAR`` per CJK character (Han, kana, Hangul),
    ``VISIT_LATIN_MS_PER_WORD`` per word (a run of other letters / digits, so
    Cyrillic and numbers count as words; ``3.14``, ``1,000`` and ``don't``
    are one word), ``VISIT_PUNCT_END_MS`` per run of sentence-ending marks
    and ``VISIT_PUNCT_COMMA_MS`` per run of comma / enumeration marks (a run
    such as ``……`` or ``!?`` is one pause).
    """
    cjk = words = ends = commas = 0
    in_word = False
    prev = ""   # 上一个字符的类别：'end' / 'comma' / ''
    n = len(text)
    for idx, ch in enumerate(text):
        if is_cjk_char(ch):
            cjk += 1
            in_word = False
            prev = ""
        elif ch.isalnum():
            if not in_word:
                words += 1
                in_word = True
            prev = ""
        elif (ch in _WORD_JOINERS and in_word and idx + 1 < n
              and text[idx + 1].isalnum() and not is_cjk_char(text[idx + 1])):
            continue   # 词内连接符：3.14 / 1,000 / don't
        elif ch in _END_MARKS:
            in_word = False
            if prev != "end":
                ends += 1
            prev = "end"
        elif ch in _COMMA_MARKS:
            in_word = False
            if prev != "comma":
                commas += 1
            prev = "comma"
        else:
            in_word = False
            prev = ""
    ms = (VISIT_CJK_MS_PER_CHAR * cjk + VISIT_LATIN_MS_PER_WORD * words
          + VISIT_PUNCT_END_MS * ends + VISIT_PUNCT_COMMA_MS * commas)
    return int(min(VISIT_CLAUSE_MAX_MS, max(VISIT_CLAUSE_MIN_MS, ms)))


def clause_release_offsets_ms(clauses: Sequence[Clause]) -> list[int]:
    """Release threshold of each clause: ``sum(estimate_speech_ms(c.raw) for earlier c)``.

    Clause ``i`` may be released once played audio reaches element ``i``.
    Estimates use ``raw`` (what TTS speaks), never the redacted ``text``.
    """
    out: list[int] = []
    acc = 0
    for c in clauses:
        out.append(acc)
        acc += estimate_speech_ms(c.raw)
    return out


# ── 接收侧字幕拼接（§4.2 line_delta）─────────────────────────────────

class LineDeltaAssembler:
    """Receive-side subtitle assembly of one sender's ``line_delta`` stream.

    Pieces land by their own ``i``; a forward jump is packet loss, not a
    protocol violation: missing indices render as one gap mark per run and
    nothing is requested again (the reliable ``text`` closes the line). Each
    of the following is dropped and counted in ``anomalies``: ``i`` not an
    integer in ``0..VISIT_LINE_DELTA_MAX_I`` (checked before indexing), an
    ``i`` already seen for that ``ln``, a later piece whose ``lp`` differs
    from the one its line opened with, missing ``ln`` / ``txt``, and a new
    ``ln`` whose ``lp`` is not above the still-open line's (overlap). A new
    line with a larger ``lp`` retires the open one (its pieces stay
    renderable until its ``text`` closes it): the old line's reliable
    ``text`` may simply be waiting behind a ``seq`` gap while the lossy
    first piece of the next line overtook it (same rule as
    ``VisitRoom.on_incoming_start``). Pieces arriving after ``close`` for
    their ``ln`` are ignored silently. Retired lines beyond
    ``VISIT_REORDER_BUFFER_MAX`` are evicted oldest first; an evicted ``ln``
    is kept as a tombstone (like :meth:`drop`) and its ``lp`` raises an
    eviction watermark, so a new ``ln`` whose ``lp`` is not above it is
    dropped as an anomaly and an evicted subtitle never reappears.
    """

    def __init__(self, *, max_i: int = VISIT_LINE_DELTA_MAX_I, gap_mark: str = "…",
                 closed_lru: int = VISIT_DEDUP_LRU) -> None:
        self._max_i = int(max_i)
        self._gap = gap_mark
        self._lru = int(closed_lru)
        self._lines: dict[str, dict[int, str]] = {}
        self._lps: dict[str, Optional[int]] = {}
        # 被容量淘汰的行里最大的 lp：墓碑有上限，水位兜住被挤出墓碑的更老的行
        self._evicted_lp: Optional[int] = None
        self._open: Optional[str] = None
        self._open_lp: Optional[int] = None
        self._final: "OrderedDict[str, str]" = OrderedDict()
        self._stalled: "OrderedDict[str, None]" = OrderedDict()
        self.anomalies = 0

    def feed(self, msg: Mapping[str, Any]) -> bool:
        """Place one ``line_delta``; return True if it was stored."""
        ln = msg.get("ln")
        i = msg.get("i")
        txt = msg.get("txt")
        if not isinstance(ln, str) or not isinstance(txt, str) or not _is_int(i) \
                or not (0 <= i <= self._max_i):
            self.anomalies += 1
            return False
        if ln in self._final or ln in self._stalled:
            return False
        lp = msg.get("lp")
        if ln in self._lines and (lp if _is_int(lp) else None) != self._lps.get(ln):
            # 一行只有一个 lp（与 VisitRoom.observe_lp 同一不变量）：后续分片换了 lp，
            # 拼出来的字幕就混进了声称不同排序位置的片段
            self.anomalies += 1
            return False
        retired = ln in self._lines and ln != self._open
        if self._open is not None and ln != self._open and not retired:
            if not (_is_int(lp) and self._open_lp is not None and lp > self._open_lp):
                self.anomalies += 1
                return False
            # 新行 lp 更大：旧行只是 text 还在 seq 缺口后排队，退出「打开」但保留其分片
            self._open = None
        if ln not in self._lines and self._evicted_lp is not None \
                and not (_is_int(lp) and lp > self._evicted_lp):
            # 不高于淘汰水位的「新」行：被淘汰旧行的晚到 / 重放分片，不能当新行装回去
            self.anomalies += 1
            return False
        if ln not in self._lines:
            self._lps[ln] = lp if _is_int(lp) else None
        clauses = self._lines.setdefault(ln, {})
        if i in clauses:
            self.anomalies += 1
            return False
        clauses[i] = txt
        if retired:
            # 已被较新行替下、text 尚未到的旧行：晚到的分片照常落位，不抢回「打开」
            return True
        if self._open != ln:
            self._open = ln
            self._open_lp = lp if _is_int(lp) else None
            self._bound_unclosed()
        return True

    def _bound_unclosed(self) -> None:
        # 被替下、text 迟迟不来的旧行有上限：诚实对端排在 seq 缺口后的 text 最多
        # VISIT_REORDER_BUFFER_MAX 条，再多就是只发首片不收口的对端，丢最旧的
        while len(self._lines) > VISIT_REORDER_BUFFER_MAX + 1:
            oldest = next(k for k in self._lines if k != self._open)
            evicted_lp = self._lps.get(oldest)
            # 墓碑与 drop 相同：晚到分片静默忽略，可靠 text 仍能收口
            self.drop(oldest)
            if evicted_lp is not None and (self._evicted_lp is None or evicted_lp > self._evicted_lp):
                self._evicted_lp = evicted_lp

    def close(self, msg: Mapping[str, Any]) -> str:
        """Close a line with its ``text`` message; the full ``txt`` replaces the pieces."""
        ln = str(msg.get("ln"))
        txt = str(msg.get("txt", ""))
        self._lines.pop(ln, None)
        self._lps.pop(ln, None)
        self._stalled.pop(ln, None)
        if self._open == ln:
            self._open = None
            self._open_lp = None
        self._final[ln] = txt
        self._final.move_to_end(ln)
        while len(self._final) > self._lru:
            self._final.popitem(last=False)
        return txt

    def closed(self, ln: str) -> bool:
        """True once ``ln`` was closed by its ``text`` or dropped (its tombstone is still kept)."""
        return ln in self._final or ln in self._stalled

    def drop(self, ln: str) -> None:
        """Forget an open line locally (stall truncation) without a ``text``.

        The ``ln`` is kept as a tombstone: later pieces of it are ignored (the
        stalled subtitle must not reappear), while its reliable ``text`` can
        still close it through :meth:`close`.
        """
        self._lines.pop(ln, None)
        self._lps.pop(ln, None)
        if self._open == ln:
            self._open = None
            self._open_lp = None
        self._stalled[ln] = None
        self._stalled.move_to_end(ln)
        while len(self._stalled) > self._lru:
            self._stalled.popitem(last=False)

    def render(self, ln: str) -> Optional[str]:
        """Current subtitle text of ``ln`` (final text once closed), or None if unknown."""
        if ln in self._final:
            return self._final[ln]
        clauses = self._lines.get(ln)
        if not clauses:
            return None
        parts: list[str] = []
        in_gap = False
        for k in range(max(clauses) + 1):
            if k in clauses:
                parts.append(clauses[k])
                in_gap = False
            elif not in_gap:
                parts.append(self._gap)
                in_gap = True
        return "".join(parts)


# ── 纸面最坏速率（§4.1 限速条 / PR-03 测试同源）──────────────────────

def max_worst_case_rates() -> tuple[float, float]:
    """Paper upper bound of one side's outbound data channel: ``(messages/s, KB/s)``.

    Messages per second (demand, every class at its cap simultaneously)::

        line_delta 1000 / VISIT_DELTA_MIN_INTERVAL_MS          = 4
        text       1 line/s x 5 pieces + one 5-piece resend      = 10
        ack        1000 / VISIT_ACK_COALESCE_MS                 = 2
        typing     1
        hb         1 / VISIT_HEARTBEAT_S                         = 0.2
        stats      1 / 5 s                                       = 0.2
                                                         total ~= 17.4

    Bytes per second: the same classes at their per-message maxima (1000 B
    per piece, ack 80, typing 60, hb 100, stats 160) demand ~14 KB/s on
    paper, but the outbound byte bucket releases at most
    ``VISIT_DATA_BUCKET_BPS`` on average, so the returned KB/s is
    ``min(demand, bucket) / 1024`` (5.0). ``wrap_up`` / ``state`` /
    ``line_abort`` are a handful per visit and ignored.
    """
    delta_ps = 1000 / VISIT_DELTA_MIN_INTERVAL_MS
    text_ps = _TEXT_PIECES_TYPICAL * 2
    ack_ps = 1000 / VISIT_ACK_COALESCE_MS
    hb_ps = 1 / VISIT_HEARTBEAT_S
    stats_ps = 1 / _STATS_INTERVAL_S
    msgs = delta_ps + text_ps + ack_ps + _TYPING_PER_S + hb_ps + stats_ps
    demand_bytes = ((delta_ps + text_ps) * VISIT_PIECE_MAX_BYTES + ack_ps * _ACK_MAX_BYTES
                    + _TYPING_PER_S * _TYPING_MAX_BYTES + hb_ps * _HB_MAX_BYTES
                    + stats_ps * _STATS_MAX_BYTES)
    kb = min(demand_bytes, VISIT_DATA_BUCKET_BPS) / 1024
    return round(msgs, 3), round(kb, 3)
