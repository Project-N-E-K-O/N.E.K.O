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

"""Text hygiene for catgirl visits (design OD-23, §3.6.6, §3.8).

Every function here is pure and synchronous.

* :func:`sanitize_relay_text` -- the one cleaning chain for relayed text:
  control characters out, prompt-delimiter / envelope forgeries escaped,
  Markdown media defanged, then cut to a token budget
  (``truncate_to_tokens``, never by characters) and to 4096 UTF-8 bytes.
* :func:`clamp_peer_line` -- the same prompt-safety steps for a received peer
  line right before it is wrapped in the nonce envelope of a HumanMessage
  (no Markdown defang: history keeps what the peer actually said).
* :func:`defang_markdown_media` -- idempotent, turns Markdown images / links /
  autolinks / reference definitions into plain text (URLs kept as words).
* :func:`redact_outbound` / :func:`redact_outbound_with_spans` -- whole-word
  replacement of family names (casefold + Unicode normalisation) with a
  neutral term supplied by the caller; the span variant also returns an
  offset map back to the original text for speech-time estimation.
* :func:`neutralize_display_name` -- a peer display name that impersonates a
  local family member / cat / the system becomes ``generic label + short id``.
* :func:`assert_no_peer_ngram` -- refuses home-coming text that copies any
  ``n`` consecutive units of what the peer said.
* :func:`wrap_nonce_envelope` / :func:`make_envelope_nonce` -- the data
  envelope that :func:`sanitize_relay_text` / :func:`clamp_peer_line` keep
  the peer from forging.

Localised words (the neutral family term, the generic peer label) live in
``config/prompts/prompts_visit.py``; this module never imports it, callers
pass them in.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Iterable, NamedTuple, Optional, Sequence

from config.visit_settings import (
    VISIT_LINE_MAX_TOKENS,
    VISIT_PEER_LABEL_MAX_TOKENS,
    VISIT_PEER_NGRAM_N,
    VISIT_TEXT_MAX_BYTES,
)
from utils.tokenize import truncate_to_tokens

_DISPLAY_NAME_MAX_CHARS = 64
_PRECUT_BYTES = 4 * VISIT_TEXT_MAX_BYTES

# ── 字符类别 ───────────────────────────────────────────────────────────

# 保留的格式字符：ZWNJ / ZWJ（emoji 组合、波斯语等需要）。其余 Cf（双向覆盖、零宽空格、BOM…）一律去掉。
_KEEP_FORMAT = frozenset({"‌", "‍"})
_LINE_BREAKS = {"\r\n": "\n", "\r": "\n", " ": "\n", " ": "\n", "\x85": "\n"}
_LINE_BREAK_RE = re.compile("\r\n|[\r  \x85]")


def strip_control_chars(text: str) -> str:
    """Remove control / format / surrogate characters; keep ``\\n`` and ``\\t``.

    Line separators (CR, CRLF, NEL, U+2028, U+2029) become ``\\n``. ZWJ and
    ZWNJ survive (emoji sequences); bidi overrides, zero-width spaces and BOMs
    do not.
    """
    if not text:
        return ""
    text = _LINE_BREAK_RE.sub(lambda m: _LINE_BREAKS[m.group(0)], text)
    out = []
    for ch in text:
        if ch in ("\n", "\t"):
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs"):
            continue
        if cat == "Cf" and ch not in _KEEP_FORMAT:
            continue
        out.append(ch)
    return "".join(out)


# ── nonce 信封与分隔符中和 ──────────────────────────────────────────────

ENVELOPE_TAG = "visit_data"
"""Tag name of the nonce envelope wrapping peer-supplied text in prompts."""

# ======X====== 成对分隔符（含全角 ＝）：3 个及以上折成 1 个，伪造不出分隔符。
_DELIMITER_RUN_RE = re.compile("[=＝]{3,}")
# 信封标签的伪造：< / ＜ / ‹ 后跟可选空白、可选斜杠、visit_data（用于检测）。
_TAG_BRACKETS = "<＜‹"
_ENVELOPE_TAG_RE = re.compile(r"[<＜‹]\s*/?\s*" + ENVELOPE_TAG, re.IGNORECASE)
_ENVELOPE_NAME_RE = re.compile(re.escape(ENVELOPE_TAG), re.IGNORECASE)


def _strip_tag_openers(text: str) -> str:
    # 以每个 visit_data 为锚向左走过紧挨着的一整串「尖括号 / 斜杠 / 空白」，从其中第一个
    # 尖括号起整串改写：尖括号全删，只留最后一个斜杠及其后的空白。只摘一个尖括号的话
    # << /visit_data、</<visit_data 剩下的又是合法标签；用正则从每个尖括号起点重试则是
    # 平方级。改写后锚点左边紧邻的已没有尖括号，一遍即可；向左最多走到上一个锚点，总体线性
    out: list[str] = []
    pos = 0
    for m in _ENVELOPE_NAME_RE.finditer(text):
        j = m.start()
        k = j
        while k > pos and (text[k - 1] in _TAG_BRACKETS or text[k - 1] == "/" or text[k - 1].isspace()):
            k -= 1
        while k < j and text[k] not in _TAG_BRACKETS:
            k += 1                         # 第一个尖括号之前的空白 / 斜杠不属于它，保留
        if k < j:
            run = text[k:j]
            slash = run.rfind("/")
            keep = "" if slash < 0 else "/" + "".join(
                c for c in run[slash + 1:] if c not in _TAG_BRACKETS
            )
            out.append(text[pos:k])
            out.append(keep)
            out.append(text[j:m.end()])
        else:
            out.append(text[pos:m.end()])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def escape_envelope(text: str) -> str:
    """Neutralise prompt-delimiter and envelope-tag forgeries.

    Runs of three or more ``=`` (ASCII or full-width) fold to a single ``=``
    so no ``======X======`` delimiter can be forged; every opening of the
    envelope tag loses its whole run of angle brackets (a run such as
    ``<< /visit_data>`` would otherwise leave a valid tag behind). Linear in
    the text length. Idempotent.
    """
    if not text:
        return ""
    text = _DELIMITER_RUN_RE.sub("=", text)
    return _strip_tag_openers(text)


def make_envelope_nonce() -> str:
    """Return a fresh random nonce for one envelope."""
    return secrets.token_hex(8)


def wrap_nonce_envelope(text: str, *, nonce: str) -> str:
    """Wrap already-cleaned peer text in a nonce envelope for a prompt.

    ``text`` must have passed :func:`clamp_peer_line` (or
    :func:`sanitize_relay_text`), which strips any forged envelope tag.
    """
    if not re.fullmatch(r"[0-9a-f]{8,64}", nonce or ""):
        raise ValueError("nonce must be lowercase hex")
    return f'<{ENVELOPE_TAG} nonce="{nonce}">\n{text}\n</{ENVELOPE_TAG} nonce="{nonce}">'


# ── Markdown 媒体降级 ───────────────────────────────────────────────────

_AUTOLINK_URI_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*)>")
_AUTOLINK_EMAIL_RE = re.compile(r"<([^\s<>@]+@[^\s<>@]+)>")
_REF_DEF_RE = re.compile(r"^([ \t]{0,3})\[((?:[^\[\]\\\n]|\\.)+)\]:", re.MULTILINE)
_DEFANG_MAX_ROUNDS = 16


def _match_pairs(text: str, opener: str, closer: str) -> dict[int, int]:
    """Stack-match ``opener`` / ``closer``; map each matched closer to its opener."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for i, ch in enumerate(text):
        if ch == opener:
            stack.append(i)
        elif ch == closer and stack:
            pairs[i] = stack.pop()
    return pairs


def _defang_inline_once(text: str) -> str:
    # 一遍栈配对：每个 "](" 向左取配平的 "["、向右取配平的 ")"，整段改写为 "text (dest)"。
    if "](" not in text:
        return text
    open_of_bracket = _match_pairs(text, "[", "]")
    close_of_paren = {o: c for c, o in _match_pairs(text, "(", ")").items()}
    out: list[str] = []
    pos = 0
    k = text.find("](")
    while k >= 0:
        start = open_of_bracket.get(k, -1)
        end = close_of_paren.get(k + 1, -1)
        if start >= pos and end > k:
            if start > pos and text[start - 1] == "!":
                out.append(text[pos:start - 1])
            else:
                out.append(text[pos:start])
            out.append(f"{text[start + 1:k]} ({text[k + 2:end]})")
            pos = end + 1
            k = text.find("](", pos)
        else:
            k = text.find("](", k + 1)
    out.append(text[pos:])
    return "".join(out)


def _defang_round(text: str) -> str:
    text = _defang_inline_once(text)
    text = _AUTOLINK_URI_RE.sub(lambda m: m.group(1), text)
    text = _AUTOLINK_EMAIL_RE.sub(lambda m: m.group(1), text)
    return _REF_DEF_RE.sub(lambda m: m.group(1) + m.group(2) + ":", text)


def defang_markdown_media(text: str) -> str:
    """Turn Markdown images / links into plain text; idempotent and pure.

    ``![alt](url)`` -> ``alt (url)``, ``[text](url)`` -> ``text (url)``,
    ``<scheme:...>`` / ``<user@host>`` autolinks lose their angle brackets,
    reference definitions ``[id]: url`` lose their square brackets. URLs stay
    as ordinary words (bare URLs are left to the plain-text renderer). Any
    ``](`` left over (unbalanced or nested oddities) is split to ``] (``,
    so the output never contains inline image / link syntax.
    """
    if not text:
        return ""
    for _ in range(_DEFANG_MAX_ROUNDS):
        nxt = _defang_round(text)
        if nxt == text:
            break
        text = nxt
    # 兜底：CommonMark 行内链接要求 ] 与 ( 紧邻，拆开即失效。
    return text.replace("](", "] (")


# ── 长度上限 ────────────────────────────────────────────────────────────


def clamp_text_utf8(text: str, max_bytes: int = VISIT_TEXT_MAX_BYTES) -> str:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes on a character boundary.

    Lone surrogates (not encodable) are dropped.
    """
    if not text:
        return ""
    data = text.encode("utf-8", errors="ignore")
    if len(data) <= max_bytes:
        return data.decode("utf-8")
    return data[:max(0, max_bytes)].decode("utf-8", errors="ignore")


def _truncate_tokens(text: str, max_tokens: int) -> str:
    out = truncate_to_tokens(text, max_tokens)
    if out != text:
        # BPE 切在多字节字符中间时解码会留下 U+FFFD，去掉。
        out = out.rstrip("�")
    return out


def _precut(text: str) -> str:
    # 先粗截到 4 倍字节上限：后续各步都只会让文本变短或基本不变，输出最多 4096 B，
    # 粗截点永远落在输出之外；只为挡住超长输入让 defang / tokenizer 白跑。
    return clamp_text_utf8(text, _PRECUT_BYTES)


def sanitize_relay_text(text: str, *, max_tokens: int = VISIT_LINE_MAX_TOKENS) -> str:
    """The OD-23 cleaning chain for relayed visit text.

    Order: :func:`strip_control_chars` -> :func:`escape_envelope` ->
    :func:`defang_markdown_media` -> ``truncate_to_tokens(max_tokens)`` ->
    :func:`clamp_text_utf8` (4096 B). Leading / trailing whitespace is kept so
    sanitised fragments concatenate to the sanitised line. Human lines pass
    ``max_tokens=VISIT_HUMAN_LINE_MAX_TOKENS``.
    """
    text = _precut(escape_envelope(strip_control_chars(text or "")))
    text = defang_markdown_media(text)
    text = _truncate_tokens(text, max_tokens)
    return clamp_text_utf8(text, VISIT_TEXT_MAX_BYTES)


def clean_relay_text(text: str) -> str:
    """:func:`sanitize_relay_text` without its size caps (no token / byte cut).

    For callers that must tell whether the capped form lost text: the capped
    result differs from this one exactly when a cap cut something (the wire
    budget passes it as ``WireBudget(clean=...)``).
    """
    return defang_markdown_media(escape_envelope(strip_control_chars(text or "")))


def clamp_peer_line(text: str, *, max_tokens: int = VISIT_LINE_MAX_TOKENS) -> str:
    """Make a received peer line safe to place inside a prompt envelope.

    Strips control characters, escapes delimiter / envelope forgeries, then
    cuts to ``max_tokens`` tokens and 4096 UTF-8 bytes. Markdown is left as
    received (rendering safety is :func:`defang_markdown_media`'s job).
    """
    text = _precut(escape_envelope(strip_control_chars(text or "")))
    text = _truncate_tokens(text, max_tokens)
    return clamp_text_utf8(text, VISIT_TEXT_MAX_BYTES)


# ── 折叠视图（casefold + 兼容归一）────────────────────────────────────────


def _is_unspaced_script(ch: str) -> bool:
    """CJK ideographs, kana, hangul, Thai, Lao, Khmer, Myanmar: no word spaces."""
    cp = ord(ch)
    return (
        0x2E80 <= cp <= 0x2FDF       # CJK 部首
        or 0x3040 <= cp <= 0x30FF    # 平假名 / 片假名
        or 0x31F0 <= cp <= 0x31FF    # 片假名扩展
        or 0x3400 <= cp <= 0x4DBF    # CJK 扩展 A
        or 0x4E00 <= cp <= 0x9FFF    # CJK 统一汉字
        or 0xF900 <= cp <= 0xFAFF    # CJK 兼容汉字
        or 0x20000 <= cp <= 0x3134F  # CJK 扩展 B-G
        or 0x1100 <= cp <= 0x11FF    # 谚文字母
        or 0x3130 <= cp <= 0x318F    # 谚文兼容字母
        or 0xAC00 <= cp <= 0xD7AF    # 谚文音节
        or 0x0E00 <= cp <= 0x0EFF    # 泰文 / 老挝文
        or 0x1000 <= cp <= 0x109F    # 缅甸文
        or 0x1780 <= cp <= 0x17FF    # 高棉文
        or 0xFF66 <= cp <= 0xFF9F    # 半角片假名
    )


def _is_word_char(ch: str) -> bool:
    """A letter / digit / combining mark of a space-delimited script."""
    if _is_unspaced_script(ch):
        return False
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N", "M") or ch == "_"


def _fold_char(ch: str) -> str:
    """Fold one character for matching: drop format chars, NFKD + casefold."""
    if unicodedata.category(ch) == "Cf":
        return ""
    return unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", ch).casefold())


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Folded view of ``text`` plus, per folded char, the source index."""
    folded: list[str] = []
    src: list[int] = []
    for i, ch in enumerate(text):
        f = _fold_char(ch)
        folded.append(f)
        src.extend([i] * len(f))
    return "".join(folded), src


def fold_text(text: str) -> str:
    """Return the matching key used by redaction and impersonation checks.

    Casefolded and compatibility-decomposed (a superset of "casefold + NFC":
    full-width and decomposed spellings match too); format characters such as
    zero-width spaces are dropped.
    """
    return _fold_with_map(text or "")[0]


# ── 亲人名整词替换 ──────────────────────────────────────────────────────


class RedactSpan(NamedTuple):
    """One replacement made by :func:`redact_outbound_with_spans`.

    ``raw_start:raw_end`` is the replaced slice of the *input* text and
    ``out_start:out_end`` the replacement's slice of the *output* text
    (half-open, Python string indices). Text between spans is copied
    verbatim, so offsets outside spans shift by a constant per gap.
    """

    raw_start: int
    raw_end: int
    out_start: int
    out_end: int


def _prepare_names(family_names: Iterable[str]) -> list[str]:
    keys = {fold_text(n.strip()) for n in family_names if isinstance(n, str) and n.strip()}
    keys.discard("")
    return sorted(keys, key=len, reverse=True)


def redact_outbound_with_spans(
    text: str, *, family_names: Iterable[str], replacement: str,
    partial_tail: bool = False,
) -> tuple[str, list[RedactSpan]]:
    """Replace whole-word family names; also return the offset map.

    Matching is done on :func:`fold_text` keys (casefold + Unicode
    compatibility normalisation, zero-width characters ignored). "Whole
    word" applies to space-delimited scripts only: a match whose edge
    character is a Latin / Cyrillic / ... letter or digit must not touch
    another such character (``Ann`` does not hit ``Anna``). CJK, kana and
    hangul names match as substrings (no word spaces; particles attach).
    Longer names win over their prefixes; matches never overlap.

    ``partial_tail=True`` additionally replaces a trailing proper prefix of
    a name (``"... A-ming"`` cut after ``"A"``). Use it only when a line is
    closed early (``wire_size`` truncation): a streaming buffer must not use
    it, because the rest of the name may still arrive.

    Returns ``(redacted, spans)`` where ``spans`` is the ordered list of
    :class:`RedactSpan`; :func:`map_redacted_offset` converts an output
    offset back to an input offset.
    """
    if not text:
        return "", []
    names = _prepare_names(family_names)
    if not names:
        return text, []
    folded, src = _fold_with_map(text)
    n = len(folded)
    out: list[str] = []
    spans: list[RedactSpan] = []
    raw_pos = 0
    out_len = 0
    i = 0
    while i < n:
        hit = None
        for name in names:
            if not folded.startswith(name, i):
                continue
            j = i + len(name)
            if _is_word_char(name[0]) and i > 0 and _is_word_char(folded[i - 1]):
                continue
            if _is_word_char(name[-1]) and j < n and _is_word_char(folded[j]):
                continue
            hit = j
            break
        if hit is None:
            i += 1
            continue
        raw_start = src[i]
        # 结束位置：最后一个折叠字符所属源字符的下一个；吞掉紧随其后被折叠为空的格式字符不必要。
        raw_end = src[hit - 1] + 1
        if raw_start < raw_pos:
            i += 1
            continue
        out.append(text[raw_pos:raw_start])
        out_len += raw_start - raw_pos
        out.append(replacement)
        spans.append(RedactSpan(raw_start, raw_end, out_len, out_len + len(replacement)))
        out_len += len(replacement)
        raw_pos = raw_end
        # 跳到下一个源字符对应的折叠位置。
        while hit < n and src[hit] < raw_end:
            hit += 1
        i = hit
    if partial_tail:
        cut = _partial_name_tail(folded, src, names, raw_pos)
        if cut is not None:
            out.append(text[raw_pos:cut])
            out_len += cut - raw_pos
            out.append(replacement)
            spans.append(RedactSpan(cut, len(text), out_len, out_len + len(replacement)))
            return "".join(out), spans
    out.append(text[raw_pos:])
    return "".join(out), spans


def _partial_name_tail(folded: str, src: list[int], names: Sequence[str],
                       raw_pos: int) -> Optional[int]:
    """Raw offset where a trailing proper prefix of a name starts, else None.

    The longest such prefix wins; the prefix must start after the last full
    replacement and respect the same word-start rule as a full match.
    """
    n = len(folded)
    best: Optional[int] = None
    for name in names:
        for length in range(min(len(name) - 1, n), 0, -1):
            fi = n - length
            if not folded.endswith(name[:length]):
                continue
            if src[fi] < raw_pos:
                break
            if _is_word_char(name[0]) and fi > 0 and _is_word_char(folded[fi - 1]):
                continue
            if best is None or src[fi] < best:
                best = src[fi]
            break
    return best


def redact_outbound(text: str, *, family_names: Iterable[str], replacement: str,
                    partial_tail: bool = False) -> str:
    """Replace whole-word family names in outbound text with ``replacement``.

    ``replacement`` is the localised ``FAMILY_NEUTRAL_TERM`` the caller takes
    from ``config/prompts/prompts_visit.py``. See
    :func:`redact_outbound_with_spans` for the matching rules.
    """
    return redact_outbound_with_spans(
        text, family_names=family_names, replacement=replacement, partial_tail=partial_tail,
    )[0]


def map_redacted_offset(spans: Sequence[RedactSpan], out_offset: int, *, inside: str = "end") -> int:
    """Map an offset of the redacted text back to the original text.

    Offsets outside replacements shift by the accumulated length difference.
    An offset strictly inside a replacement maps to the start (``inside=
    'start'``) or end (``inside='end'``) of the replaced original slice, so a
    fragment boundary never splits an original name.
    """
    delta = 0
    for sp in spans:
        if out_offset <= sp.out_start:
            break
        if out_offset < sp.out_end:
            return sp.raw_start if inside == "start" else sp.raw_end
        delta = sp.raw_end - sp.out_end
    return out_offset + delta


# ── 显示名冒名归一 ──────────────────────────────────────────────────────

# 系统 / 角色类保留标签（8 语）：对端显示名与之相等即视为冒充。
_RESERVED_LABELS = (
    "system", "系统", "系統", "システム", "시스템", "система", "sistema", "système",
    "assistant", "助手", "アシスタント", "어시스턴트", "ассистент", "asistente",
    "user", "用户", "用戶", "ユーザー", "사용자", "пользователь", "usuario", "utilisateur",
    "admin", "administrator", "管理员", "管理員", "管理者", "관리자", "администратор", "administrador",
    "tool", "developer",
)
_LABEL_STRUCTURAL_RE = re.compile(r"[\[\]|<>{}［］｜＜＞]")


def _impersonation_key(name: str) -> str:
    # 只留字母 / 数字（含 CJK）：空格、标点、零宽字符都不能用来绕开相等判断。
    return "".join(ch for ch in fold_text(name) if unicodedata.category(ch)[0] in ("L", "N"))


def neutralize_display_name(
    raw: str | None,
    *,
    protected_names: Iterable[str],
    generic_label: str,
    short_code: str = "",
) -> str:
    """Clean a peer display name and replace it when it impersonates someone.

    Cleaning follows ``FactStore.sanitize_speaker_label`` /
    ``_sanitized_display_name`` (structural characters and non-printables to
    spaces, whitespace collapsed), folds ``======`` runs, applies NFC, then
    caps at ``VISIT_PEER_LABEL_MAX_TOKENS`` tokens and 64 characters.

    The result is replaced by ``"{generic_label} {short_code}"`` when it is
    empty, or when its letters-and-digits key (casefold + compatibility
    normalisation) equals that of any ``protected_names`` entry (local family
    names, local cat names, the neutral family term, the generic label) or of
    a reserved system label. ``short_code`` is ``visit_uid[:6].upper()``.
    """
    fallback = f"{generic_label} {short_code}".strip()
    text = str(raw or "")
    text = _LABEL_STRUCTURAL_RE.sub(" ", text)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = escape_envelope(text)
    text = unicodedata.normalize("NFC", " ".join(text.split()))
    text = _truncate_tokens(text, VISIT_PEER_LABEL_MAX_TOKENS)[:_DISPLAY_NAME_MAX_CHARS].strip()
    key = _impersonation_key(text)
    if not key:
        return fallback
    protected = {_impersonation_key(n) for n in protected_names if isinstance(n, str)}
    protected.update(_impersonation_key(n) for n in _RESERVED_LABELS)
    protected.discard("")
    if key in protected:
        return fallback
    return text


# ── 回家自述 n-gram 断言 ────────────────────────────────────────────────


class PeerNgramHit(ValueError):
    """Raised by :func:`assert_no_peer_ngram`; ``ngram`` holds the matched units.

    The message never contains the text itself (safe to log).
    """

    def __init__(self, ngram: tuple[str, ...]) -> None:
        self.ngram = ngram
        super().__init__(f"text repeats a {len(ngram)}-unit sequence of the peer's lines")


def _ngram_fold(text: str) -> str:
    # n-gram 用 NFKC（组合形）：NFKD 会把谚文音节拆成字母、把浊音假名拆成两字符，单位就不对了。
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


def ngram_units(text: str) -> list[str]:
    """Split text into n-gram units (casefold + NFKC, format characters dropped).

    Each CJK / kana / hangul / Thai... character is one unit; each run of
    letters / digits of a space-delimited script is one unit (a word).
    Whitespace, punctuation and symbols separate units and are dropped.
    """
    units: list[str] = []
    word: list[str] = []
    for ch in _ngram_fold(text):
        if _is_unspaced_script(ch):
            if word:
                units.append("".join(word))
                word = []
            units.append(ch)
        elif _is_word_char(ch):
            word.append(ch)
        elif word:
            units.append("".join(word))
            word = []
    if word:
        units.append("".join(word))
    return units


def find_peer_ngram(
    text: str, peer_lines: Iterable[str], n: int = VISIT_PEER_NGRAM_N,
) -> tuple[str, ...] | None:
    """Return the first ``n``-unit sequence of ``text`` found in any peer line."""
    if n <= 0:
        raise ValueError("n must be positive")
    grams: set[tuple[str, ...]] = set()
    for line in peer_lines:
        units = ngram_units(line or "")
        for k in range(len(units) - n + 1):
            grams.add(tuple(units[k:k + n]))
    if not grams:
        return None
    units = ngram_units(text or "")
    for k in range(len(units) - n + 1):
        gram = tuple(units[k:k + n])
        if gram in grams:
            return gram
    return None


def assert_no_peer_ngram(text: str, peer_lines: Iterable[str], n: int = VISIT_PEER_NGRAM_N) -> None:
    """Raise :class:`PeerNgramHit` when ``text`` copies ``n`` consecutive peer units.

    Units are those of :func:`ngram_units` (one CJK character, or one word of
    a space-delimited script), so ``n=8`` means eight characters of Chinese
    or eight words of English. Lines with fewer than ``n`` units cannot hit.
    """
    gram = find_peer_ngram(text, peer_lines, n)
    if gram is not None:
        raise PeerNgramHit(gram)


__all__ = [
    "ENVELOPE_TAG",
    "PeerNgramHit",
    "RedactSpan",
    "assert_no_peer_ngram",
    "clamp_peer_line",
    "clamp_text_utf8",
    "defang_markdown_media",
    "escape_envelope",
    "find_peer_ngram",
    "fold_text",
    "make_envelope_nonce",
    "map_redacted_offset",
    "neutralize_display_name",
    "ngram_units",
    "redact_outbound",
    "redact_outbound_with_spans",
    "sanitize_relay_text",
    "strip_control_chars",
    "wrap_nonce_envelope",
]
