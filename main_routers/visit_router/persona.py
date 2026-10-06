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

"""The public visit persona (OD-10 v3; design §4.6 persona, §5 PR-09a ``persona.py``).

A visit session never sees the original character card: every line the peer
says goes into that LLM and its output goes straight back to the peer, so a
private card could be talked out of it line by line. Instead the session uses
a public persona generated from the card once, checked by two automatic
privacy checks that do not trust the generating call, and confirmed by the
user before the first visit.

Storage: ``config_dir/visit_persona/<character_uid>.json`` = ``{text,
source_card_hash, generated_at, edited, reviewed, private_sections,
scan_card_hash, scan_complete}`` (atomic write, ``0o600``; keyed by the
stable character id, so a rename keeps it). ``GET`` only reads the file:
the private-section list is computed when the persona is generated and
persisted with it.

Privacy check (:func:`persona_privacy_check`): (1) deterministic sensitive
tokens collected from the whole card -- family names, phone numbers, email
addresses, URLs, runs of five or more digits, the value after keywords such
as WeChat / QQ / phone / address / "lives in", address-like fragments -- any
of them in the persona is a hit, however short; (2) private sections = the
rule sections (containing ``{MASTER_NAME}``, a family name or a sensitive
token) plus the sections an independent scan call lists; any 8-gram shared
with the persona is a hit. A hit regenerates once; a second hit is not saved
(``persona_sensitive_overlap``).

Gate (:func:`persona_gate`, called before a room is reserved): missing or
unreviewed -> refuse; the card changed and the persona was never edited ->
regenerate in the background and refuse; the card changed but the user
edited the persona -> allowed, ``card_changed`` is only reported.
"""

from __future__ import annotations

import asyncio
import weakref
import hashlib
import os
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.prompts.prompts_visit import (
    build_visit_persona_private_scan_prompt,
    build_visit_persona_prompt,
    get_family_neutral_term,
)
from config.visit_settings import (
    VISIT_LLM_TIMEOUT_S,
    VISIT_PEER_NGRAM_N,
    VISIT_PERSONA_DIRNAME,
    VISIT_PERSONA_MAX_TOKENS,
)
from main_logic.visit import local_chars
from main_logic.visit.sanitize import find_peer_ngram, fold_text, redact_outbound, strip_control_chars
from main_logic.visit.subjects import path_lock
from main_routers.system_router._shared import _read_json_object
from main_routers.visit_router import llm as visit_llm
from main_routers.visit_router.local_context import CharacterContext, load_character_context, prompt_lang
from main_routers.visit_router.local_guard import http_denied
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, truncate_to_tokens
from utils.visit_wire import id_path

logger = get_module_logger(__name__, "Main")

router = APIRouter()

PERSONA_STATES = ("missing", "generating", "unreviewed", "ready")
PERSONA_FIELDS = (
    "text", "source_card_hash", "generated_at", "edited", "reviewed",
    "private_sections", "scan_card_hash", "scan_complete",
)

CHARACTER_UID_RE = re.compile(r"^[0-9a-f]{32}$")

PERSONA_CARD_MAX_TOKENS = 8000
"""Input budget of the card sent to the generation and scan calls (cut at the end)."""

PERSONA_SCAN_MAX_TOKENS = 2000
"""Output budget of the private-section scan call (a JSON list of copied passages)."""

_PRIVATE_SECTIONS_MAX = 256
_SECTION_MAX_CHARS = 2000
_TOKEN_MIN_CHARS = 2
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# ── 规则敏感词与私人段落 ───────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。、！？；：,;:()（）\[\]【】<>「」『』\"']+", re.IGNORECASE)
# 句末标点不算网址的一部分（「见 https://a.example/x.」→「https://a.example/x」）
_URL_TRAILING = ".,!?'\"…"
# 裸域名（「private-family.example」）：只认小写写法，免得把「Mr.Smith」之类当成主机名
_HOST_RE = re.compile(r"(?<![A-Za-z0-9@._\-])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?![A-Za-z0-9_\-])")
_DIGITS_RE = re.compile(r"[0-9]{5,}")
# 关键词后面跟的值：到标点或行尾为止，中间可以有空格（「住在桂花路」→「桂花路」，
# 「address: 12 Main Street」→「12 Main Street」）
_KEYWORD_VALUE = r"(?P<value>[^\n，。、！？；：,;:!?()（）\[\]【】<>「」『』\"']{2,40})"
# 中文地址关键词本来就常不带分隔（「住在桂花路」）；联系方式关键词要有分隔（冒号 / 是 / 为），
# 或值像号码 / 账号（以数字、字母、+、# 开头）：「手机游戏」「电话会议」不收
_CJK_KEYWORD_VALUE_RE = re.compile(
    r"(?:(?P<kw>住址|地址|家住|住在|位于|位於)\s*[:：是为為在]?[ \t]*"
    r"|(?P<kw4>微信号?|微訊號?|qq号|手机号?|手機號?|电话号码?|電話號碼?|电话|電話|邮箱|郵箱)"
    r"(?:\s*[:：]\s*|\s*[是为為]\s*|\s*(?=[A-Za-z0-9+#])))" + _KEYWORD_VALUE,
    re.IGNORECASE,
)
# 拉丁关键词要整词出现（「smartphone」里的 phone 不算），后面要有真正的分隔：冒号 / 等号；
# 或值以数字 / # / + 开头（「phone 138 0013 8000」「address is 12 Main Street」）；或空格后的
# 第一个词像账号（带数字 / 下划线：「wechat mimi_cat」，或驼峰：「wechat AliceFoo」）；显式带 id 的
# 关键词（「line id alicefoo」）后接任意词；「address」后接大写开头的词
# （「address Maple Grove」）；「lives in」本身就是分隔。「phone games」这种普通名词不算
_LATIN_KEYWORD_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?P<kw>(?:wechat|weixin|vx|qq)(?:\s*id)?|e-?mail|phone(?:\s*number)?|address|line\s*id)(?![A-Za-z0-9])"
    r"(?:\s*[:：=]\s*|\s+(?:is\s+|at\s+)?(?=[#+0-9])|\s+(?=[A-Za-z0-9.\-]*[0-9_])"
    r"|(?<=[iI][dD])\s+|\s+(?=(?-i:[A-Za-z]*[a-z][A-Z])))"
    r"|(?P<kw2>lives?\s+in)\s+"
    r"|(?P<kw3>address)\s+(?=(?-i:[A-Z])))" + _KEYWORD_VALUE,
    re.IGNORECASE,
)
# 地址类关键词：值在逗号处停下，同一行逗号后面的片段（「address: Apt 4, 12 Main Street」）也要找街名
_ADDRESS_KEYWORDS = frozenset({"住址", "地址", "家住", "住在", "位于", "位於", "address"})
_ADDRESS_SEGMENT_SPLIT_RE = re.compile(r"[,，;；、]")
_LINE_END_RE = re.compile(r"[\n。！？!?]")
# 带分隔符的电话号码（「138 0013 8000」「+1 (555) 010-0199」）：按纯数字比对
_PHONE_RE = re.compile(r"\+?[0-9][0-9 \-().]{5,}[0-9]")
_PHONE_MIN_DIGITS = 7
# 拉丁值里取「全是大写开头的词或数字」的连续两词以上片段（「Main Street」），小写虚词不算
_LATIN_WORD_RE = re.compile(r"[0-9]+|[A-Z][A-Za-z'\-]*|[a-z][A-Za-z'\-]*")
_ROAD_SUFFIXES = "路|街|大道|巷|胡同|弄"
_ESTATE_SUFFIXES = "小区|小區|公寓|大厦|大廈|新村|社区|社區"
# 地址样式片段只取后缀前两个字作核心（「我们住在桂花路」→「桂花路」）。「X路 / X街」这类后缀也是
# 常用词（走路、一路），只在地址关键词后的值里与门牌号前取；「小区 / 公寓」等全卡都取
_CJK = "[" + chr(0x4E00) + "-" + chr(0x9FFF) + "]"
_ROAD_CORE_RE = re.compile(rf"{_CJK}{{2}}(?:{_ROAD_SUFFIXES}|{_ESTATE_SUFFIXES})")
_ESTATE_CORE_RE = re.compile(rf"{_CJK}{{2}}(?:{_ESTATE_SUFFIXES})")
_ROAD_NUMBER_RE = re.compile(rf"{_CJK}{{2,6}}(?:{_ROAD_SUFFIXES})\s*[0-9]+\s*[号號]")
_UNIT_RE = re.compile(r"[0-9]+\s*(?:号楼|號樓|号|號|栋|棟|幢|单元|單元|室)")
_SECTION_SPLIT_RE = re.compile(r"\n|(?<=[。！？!?；;])")
_MASTER_PLACEHOLDER = "{MASTER_NAME}"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _place_cores(value: str, pattern: re.Pattern[str]) -> list[str]:
    return [m.group(0) for m in pattern.finditer(value)]


# 以门牌号开头的值里，门牌号后面的街名（「12 main street」→「main street」）：小写写法也算。
# 数字在值中间（「a flat with 2 cats playing」）不是门牌号
_STREET_AFTER_NUMBER_RE = re.compile(
    r"\s*(?:#|no\.?\s*)?[0-9]+[A-Za-z]?\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){1,3})",
    re.IGNORECASE,
)


# 街名到第一个虚词为止：「2 cats and a dog」不是地址，不能把「cats and」收成敏感词
_STREET_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "with", "of", "in", "on", "at", "to", "for", "from", "by", "her", "his",
    "their", "my", "our", "your", "is", "are", "was", "who", "that", "which",
})


# 数字后接 0~2 个词再接街道类词尾：门牌号不在值开头也认（「the house is at 42 main street」）
_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|way|court|ct|place|pl|terrace|"
    "crescent|close|square|sq|highway|hwy|alley|row|parkway|pkwy"
)
_NUMBERED_STREET_RE = re.compile(
    rf"(?<![A-Za-z0-9])[0-9]+[A-Za-z]?\s+((?:[A-Za-z][A-Za-z'\-]*\s+){{0,2}}(?:{_STREET_TYPES}))\b\.?",
    re.IGNORECASE,
)


def _run_until_stopword(words: list[str]) -> list[str]:
    out = []
    for word in words:
        if word.lower() in _STREET_STOPWORDS:
            break
        out.append(word)
    return out


def _street_names(value: str) -> list[str]:
    """Street names in a keyword value (``main street``), lowercase spellings included.

    Two shapes count: the words after the house number the value starts
    with (two to four, up to the first function word), and anywhere in the
    value a number followed by up to two words and a street-type word. A
    number in the middle of a value with no street word after it (``a flat
    with 2 cats playing outside``) is not an address.
    """
    out = []
    head = _STREET_AFTER_NUMBER_RE.match(value)
    if head is not None:
        words = _run_until_stopword(head.group(1).split())
        out.extend(" ".join(words[:k]) for k in range(2, len(words) + 1))
    for m in _NUMBERED_STREET_RE.finditer(value):
        words = _run_until_stopword(m.group(1).split())
        if len(words) >= 2:
            out.append(" ".join(words))
    return out


def _latin_phrases(value: str) -> list[str]:
    """Runs of two or more capitalised words / numbers inside a keyword value (``Main Street``)."""
    out: list[str] = []
    run: list[str] = []
    for word in _LATIN_WORD_RE.findall(value) + [""]:
        if word and (word[0].isdigit() or word[0].isupper()):
            run.append(word)
            continue
        for i in range(len(run)):
            for j in range(i + 2, len(run) + 1):
                out.append(" ".join(run[i:j]))
        run = []
    return out


def _url_tokens(text: str) -> list[str]:
    """URLs (sentence punctuation stripped), their host names, and bare host names."""
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_URL_TRAILING)
        out.append(url)
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", url, flags=re.IGNORECASE)
        host = re.split(r"[/?#:]", host, maxsplit=1)[0]
        if "." in host:
            out.append(host)
    out.extend(m.group(0) for m in _HOST_RE.finditer(text))
    return out


def phone_digits(text: str) -> set[str]:
    """Digit strings of the phone-like numbers in ``text`` (at least seven digits, separators dropped)."""
    out = set()
    for m in _PHONE_RE.finditer(_norm(text)):
        digits = "".join(ch for ch in m.group(0) if ch.isdigit())
        if len(digits) >= _PHONE_MIN_DIGITS:
            out.add(digits)
    return out


def extract_sensitive_tokens(card: str | None, family_names: Iterable[str]) -> list[str]:
    """Deterministic sensitive tokens of ``card`` (rule 1 of the privacy check).

    Family names (as given), email addresses, URLs and host names (bare
    ones included, trailing sentence punctuation dropped), runs of five or more
    digits, phone numbers written with separators, the whole value after a
    contact / address keyword (spaces included) plus its capitalised
    multi-word runs, and address-like fragments. Every token is at least two
    characters; order is first occurrence, duplicates (by matching key)
    dropped. Phone numbers are also matched digit by digit, see
    :func:`sensitive_token_hits`.
    """
    text = _norm(card or "")
    found: list[str] = [str(n).strip() for n in family_names if isinstance(n, str) and n.strip()]
    for pattern in (_EMAIL_RE, _DIGITS_RE, _UNIT_RE, _PHONE_RE):
        found.extend(m.group(0) for m in pattern.finditer(text))
    found.extend(_url_tokens(text))
    for m in _ROAD_NUMBER_RE.finditer(text):
        found.append(m.group(0))
        found.extend(_place_cores(m.group(0), _ROAD_CORE_RE))
    for pattern in (_CJK_KEYWORD_VALUE_RE, _LATIN_KEYWORD_VALUE_RE):
        for m in pattern.finditer(text):
            value = m.group("value").strip()
            found.append(value)
            found.extend(_place_cores(value, _ROAD_CORE_RE))
            found.extend(_latin_phrases(value))
            found.extend(_street_names(value))
            keyword = (m.group("kw") or "").lower()
            if keyword in _ADDRESS_KEYWORDS or m.groupdict().get("kw2") or m.groupdict().get("kw3"):
                end = _LINE_END_RE.search(text, m.start("value"))
                rest = text[m.end("value"):end.start() if end else len(text)]
                for segment in _ADDRESS_SEGMENT_SPLIT_RE.split(rest):
                    segment = segment.strip()
                    if segment:
                        # 只认有地址形态的片段（路名核心、门牌号 + 街名）：同一行后面的爱好等普通
                        # 大写词组（「enjoys Star Wars」）不收
                        found.extend(_place_cores(segment, _ROAD_CORE_RE))
                        found.extend(_street_names(segment))
    found.extend(_place_cores(text, _ESTATE_CORE_RE))
    out: list[str] = []
    seen: set[str] = set()
    for token in found:
        token = token.strip()
        key = fold_text(token)
        if len(token) < _TOKEN_MIN_CHARS or not key or key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def split_card_sections(card: str | None) -> list[str]:
    """Split a card into sections: lines, then sentences (the unit of the 8-gram check)."""
    out = []
    for piece in _SECTION_SPLIT_RE.split(card or ""):
        piece = piece.strip()
        if piece:
            out.append(piece)
    return out


def rule_private_sections(card: str | None, family_names: Iterable[str]) -> list[str]:
    """Sections containing ``{MASTER_NAME}``, a family name or a sensitive token."""
    keys = [fold_text(t) for t in extract_sensitive_tokens(card, family_names)]
    out = []
    for section in split_card_sections(card):
        folded = fold_text(_norm(section))
        if _MASTER_PLACEHOLDER in section or any(k and k in folded for k in keys):
            out.append(section)
    return out


@dataclass(frozen=True)
class PrivacyHit:
    """One finding of :func:`persona_privacy_check`: ``kind`` is ``'token'`` or ``'section'``."""

    kind: str
    value: str


def sensitive_token_hits(card: str | None, text: str, family_names: Iterable[str]) -> list[str]:
    """Rule-1 tokens of ``card`` that occur in ``text`` (matching on :func:`fold_text` keys).

    Family names are checked by the whole-word redaction itself (``text`` is
    always redacted first), so only the other tokens are matched as plain
    substrings here. A phone number of the card also hits when ``text``
    writes the same digits with other separators.
    """
    names = [n for n in family_names if isinstance(n, str)]
    name_keys = {fold_text(n.strip()) for n in names}
    folded = fold_text(_norm(text))
    hits = []
    for token in extract_sensitive_tokens(card, names):
        key = fold_text(token)
        if key in name_keys:
            continue
        if key in folded:
            hits.append(token)
    text_phones = phone_digits(text)
    for digits in sorted(phone_digits(card or "")):
        if any(_same_number(digits, other) for other in text_phones) and digits not in hits:
            hits.append(digits)
    return hits


def _same_number(a: str, b: str) -> bool:
    """Whether two digit strings write the same phone number.

    The shorter one (at least ``_PHONE_MIN_DIGITS`` digits) inside the
    longer one counts: a country code may be left out on one side
    (``+1 555 010 0199`` / ``555-010-0199``), and an adjacent number may
    have been read into the match (``138 0013 8000 2024``).
    """
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _PHONE_MIN_DIGITS and short in long


def persona_privacy_check(
    card: str | None, text: str, family_names: Iterable[str], scanned_sections: Iterable[str],
) -> list[PrivacyHit]:
    """Both automatic checks of a (redacted) persona against its card; empty = clean."""
    names = list(family_names)
    hits = [PrivacyHit("token", t) for t in sensitive_token_hits(card, text, names)]
    sections = [*rule_private_sections(card, names), *(s for s in scanned_sections if isinstance(s, str))]
    gram = find_peer_ngram(text, sections, VISIT_PEER_NGRAM_N)
    if gram is not None:
        hits.append(PrivacyHit("section", " ".join(gram)))
    return hits


def card_hash(card: str | None) -> str:
    """``sha256`` of the raw card text (detects card edits after the persona was made)."""
    return hashlib.sha256((card or "").encode("utf-8", "surrogatepass")).hexdigest()


# ── 存储 ───────────────────────────────────────────────────────────────


def _valid_doc(doc: Any) -> bool:
    if not isinstance(doc, dict) or set(doc) != set(PERSONA_FIELDS):
        return False
    sections = doc["private_sections"]
    generated_at = doc["generated_at"]
    return (
        isinstance(doc["text"], str)
        and isinstance(doc["source_card_hash"], str) and _HASH_RE.fullmatch(doc["source_card_hash"]) is not None
        and (generated_at is None or (isinstance(generated_at, (int, float)) and not isinstance(generated_at, bool)))
        and all(isinstance(doc[k], bool) for k in ("edited", "reviewed", "scan_complete"))
        and isinstance(sections, list) and all(isinstance(s, str) for s in sections)
        and isinstance(doc["scan_card_hash"], str) and _HASH_RE.fullmatch(doc["scan_card_hash"]) is not None
    )


class VisitPersonaStore:
    """``config_dir/visit_persona/<character_uid>.json`` files (one per character)."""

    def __init__(self, config_dir: str | os.PathLike[str]) -> None:
        self.dir = Path(config_dir) / VISIT_PERSONA_DIRNAME

    def path(self, character_uid: str) -> Path:
        """The persona file of ``character_uid``; ``ValueError`` for anything but 32 hex."""
        return id_path(self.dir, character_uid, CHARACTER_UID_RE, ".json")

    def _load_sync(self, character_uid: str) -> dict | None:
        import json

        path = self.path(character_uid)
        try:
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError) as exc:
            logger.warning("visit persona %s unreadable: %s", path.name, type(exc).__name__)
            return None
        if not _valid_doc(doc):
            logger.warning("visit persona %s has an unexpected shape, ignored", path.name)
            return None
        return doc

    async def load(self, character_uid: str) -> dict | None:
        """The stored persona, or None when missing or unreadable (both count as not generated)."""
        return await asyncio.to_thread(self._load_sync, character_uid)

    def _save_sync(self, character_uid: str, doc: dict) -> None:
        if not _valid_doc(doc):
            raise ValueError("persona document has an unexpected shape")
        path = self.path(character_uid)
        with path_lock(path):
            atomic_write_json(path, doc)
            try:
                os.chmod(path, 0o600)
            except OSError as exc:
                # 与其它串门文件同一立场：权限位尽力而为（Windows 上本就无效）
                logger.debug("visit persona: chmod 0600 failed for %s: %s", path.name, exc)

    async def save(self, character_uid: str, doc: dict) -> None:
        """Validate and atomically write the persona of ``character_uid``."""
        await asyncio.to_thread(self._save_sync, character_uid, doc)

    def _retire_sync(self, character_uid: str) -> bool:
        path = self.path(character_uid)
        with path_lock(path):
            try:
                path.unlink()
            except FileNotFoundError:
                return False
        return True

    async def retire(self, character_uid: str) -> bool:
        """Delete the persona of a deleted character (``pending_retire``, PR-09b); True if one existed."""
        return await asyncio.to_thread(self._retire_sync, character_uid)


# ── 生成 ───────────────────────────────────────────────────────────────

PersonaLLM = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class PersonaResult:
    """Outcome of :func:`generate_visit_persona`: a document to save, or an ``error`` code."""

    doc: dict | None = None
    error: str | None = None
    hits: tuple[PrivacyHit, ...] = ()


def _clean_persona_text(raw: str, family_names: Sequence[str], lang: str | None) -> str:
    text = strip_control_chars(str(raw or "")).strip()
    # 先整段脱敏再截 token：先截会把名字截成半截认不出；替换成的中性称呼可能比名字长，截在最后才守得住上限
    text = redact_outbound(text, family_names=family_names, replacement=get_family_neutral_term(lang))
    return truncate_to_tokens(text, VISIT_PERSONA_MAX_TOKENS).strip()


def _parse_scan(raw: str) -> list[str]:
    import json

    text = str(raw or "").strip()
    # 模型偶尔给 JSON 包一层 ``` 代码块：只取第一个 [ 到最后一个 ] 之间
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("scan reply is not a JSON list")
    items = json.loads(text[start:end + 1])
    if not isinstance(items, list):
        raise ValueError("scan reply is not a JSON list")
    return [str(item).strip()[:_SECTION_MAX_CHARS] for item in items if isinstance(item, str) and item.strip()]


def _merge_sections(*groups: Iterable[str]) -> tuple[list[str], bool]:
    """Deduplicated sections, capped at ``_PRIVATE_SECTIONS_MAX``; the flag tells whether some were cut."""
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for section in group:
            key = fold_text(section)
            if key and key not in seen:
                seen.add(key)
                out.append(section)
    return out[:_PRIVATE_SECTIONS_MAX], len(out) > _PRIVATE_SECTIONS_MAX


async def generate_visit_persona(
    card: str | None,
    lang: str | None,
    *,
    family_names: Sequence[str],
    llm: PersonaLLM,
    scan_llm: PersonaLLM,
    now: float | None = None,
) -> PersonaResult:
    """Generate, clean and check a public persona from ``card`` (not saved here).

    One scan call lists the card's private sections (a failure leaves only
    the rule sections and ``scan_complete:false``); one generation call
    writes the persona, cut to ``VISIT_PERSONA_MAX_TOKENS`` and redacted.
    A privacy hit regenerates once; a second hit returns
    ``persona_sensitive_overlap``. Any failed generation call returns
    ``llm_unavailable``. ``family_names`` are the names to redact and to
    check for; the card is cut to ``PERSONA_CARD_MAX_TOKENS`` before it is
    sent anywhere.
    """
    names = list(family_names)
    card_in = truncate_to_tokens(card or "", PERSONA_CARD_MAX_TOKENS)
    try:
        scanned = _parse_scan(await asyncio.wait_for(
            scan_llm(build_visit_persona_private_scan_prompt(card_in, lang)), VISIT_LLM_TIMEOUT_S,
        ))
        # 卡片超出输入预算被截过：截掉的尾巴没被扫描，如实标成检查不完整
        scan_complete = len(card_in) >= len(card or "")
    except Exception as exc:  # noqa: BLE001 - 扫描失败只退回规则段落，并如实落盘「不完整」
        logger.warning("visit persona: private-section scan failed: %s", type(exc).__name__)
        scanned, scan_complete = [], False
    hits: list[PrivacyHit] = []
    for _attempt in range(2):
        try:
            raw = await asyncio.wait_for(llm(build_visit_persona_prompt(card_in, lang)), VISIT_LLM_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit persona: generation failed: %s", type(exc).__name__)
            return PersonaResult(error="llm_unavailable")
        text = _clean_persona_text(raw, names, lang)
        if not text:
            return PersonaResult(error="llm_unavailable")
        hits = persona_privacy_check(card, text, names, scanned)
        if not hits:
            digest = card_hash(card)
            sections, cut = _merge_sections(rule_private_sections(card, names), scanned)
            if cut:
                # 清单放不下：面板上看不全「不会带出门」的段落，如实标成检查不完整
                logger.warning("visit persona: private-section list capped at %d", _PRIVATE_SECTIONS_MAX)
            return PersonaResult(doc={
                "text": text,
                "source_card_hash": digest,
                "generated_at": float(time.time() if now is None else now),
                "edited": False,
                "reviewed": False,
                "private_sections": sections,
                "scan_card_hash": digest,
                "scan_complete": scan_complete and not cut,
            })
        logger.warning("visit persona: generated text overlaps private card content (%d hits)", len(hits))
    return PersonaResult(error="persona_sensitive_overlap", hits=tuple(hits))


# ── 运行时钩子与后台生成 ───────────────────────────────────────────────


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


@dataclass
class PersonaHooks:
    """Injection points (tests and PR-09b wiring); see :func:`configure_persona`."""

    config_dir: Callable[[], Path]
    load_context: Callable[[], Awaitable[CharacterContext]]
    resolve_char_uid: Callable[[str], Awaitable[str | None]]
    llm: PersonaLLM
    scan_llm: PersonaLLM
    lang: Callable[[], str]


_hooks = PersonaHooks(
    config_dir=_default_config_dir,
    load_context=load_character_context,
    resolve_char_uid=local_chars.resolve_char_uid,
    llm=visit_llm.one_shot_llm(max_tokens=VISIT_PERSONA_MAX_TOKENS + 200, timeout=VISIT_LLM_TIMEOUT_S),
    scan_llm=visit_llm.one_shot_llm(max_tokens=PERSONA_SCAN_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S),
    lang=prompt_lang,
)


def configure_persona(**hooks: Any) -> None:
    """Replace hooks: ``config_dir``, ``load_context``, ``resolve_char_uid``, ``llm``, ``scan_llm``, ``lang``."""
    for name, value in hooks.items():
        if not hasattr(_hooks, name):
            raise TypeError(f"unknown persona hook {name!r}")
        setattr(_hooks, name, value)


_jobs: dict[str, asyncio.Task] = {}
_errors: dict[str, str] = {}


def _reset_for_tests() -> None:
    for task in _jobs.values():
        task.cancel()
    _jobs.clear()
    _errors.clear()
    _write_versions.clear()


def is_generating(character_uid: str) -> bool:
    task = _jobs.get(character_uid)
    return task is not None and not task.done()


def store() -> VisitPersonaStore:
    return VisitPersonaStore(_hooks.config_dir())


_PERSONA_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def persona_lock(character_uid: str) -> asyncio.Lock:
    """Per-character lock around every persona write (hand edit vs. regeneration commit)."""
    lock = _PERSONA_LOCKS.get(character_uid)
    if lock is None:
        lock = asyncio.Lock()
        _PERSONA_LOCKS[character_uid] = lock
    return lock


_write_versions: dict[str, int] = {}
"""character_uid -> count of persona writes in this process (hand edits and regeneration commits)."""


def _note_write(character_uid: str) -> None:
    _write_versions[character_uid] = _write_versions.get(character_uid, 0) + 1


async def _regenerate(name: str, character_uid: str, started_version: int) -> None:
    try:
        ctx = await _hooks.load_context()
        card = ctx.card(name)
        if card is None:
            _errors[character_uid] = "unknown_catgirl"
            return
        result = await generate_visit_persona(
            card, _hooks.lang(), family_names=ctx.family_names, llm=_hooks.llm, scan_llm=_hooks.scan_llm,
        )
        if result.doc is None:
            _errors[character_uid] = result.error or "llm_unavailable"
            return
        async with persona_lock(character_uid):
            if _write_versions.get(character_uid, 0) != started_version:
                # 开始生成之后人设被写过（另一个窗口的手写确认，哪怕它在开始前就已拿着锁）：
                # 不拿生成结果覆盖它
                logger.info("visit persona: regeneration superseded by an edit, result dropped")
                return
            await store().save(character_uid, result.doc)
            _note_write(character_uid)
        _errors.pop(character_uid, None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务：失败记进状态，GET 可见
        logger.warning("visit persona: regeneration failed: %s", type(exc).__name__)
        _errors[character_uid] = "llm_unavailable"


def start_regeneration(name: str, character_uid: str) -> asyncio.Task | None:
    """Start a background regeneration; None when one is already running for this character."""
    if is_generating(character_uid):
        return None
    _errors.pop(character_uid, None)
    # 在启动的同一步（同步、无 await）记下写入版本：之后任何写入都会让这次结果作废
    started_version = _write_versions.get(character_uid, 0)
    task = asyncio.create_task(_regenerate(name, character_uid, started_version),
                               name=f"visit-persona-{character_uid[:6]}")
    _jobs[character_uid] = task

    def _done(t: asyncio.Task) -> None:
        if _jobs.get(character_uid) is t:
            del _jobs[character_uid]

    task.add_done_callback(_done)
    return task


def persona_state(doc: dict | None, character_uid: str) -> str:
    if is_generating(character_uid):
        return "generating"
    if doc is None:
        return "missing"
    return "ready" if doc["reviewed"] else "unreviewed"


_GATE_ATTEMPTS = 3
"""Re-reads of the persona when it is written while the gate is reading it."""


@dataclass(frozen=True)
class PersonaGate:
    """Verdict of :func:`persona_gate`; ``text`` is the persona to build the session with when ``ok``."""

    ok: bool
    state: str
    character_uid: str | None = None
    text: str | None = None


async def persona_gate(name: str) -> PersonaGate:
    """Whether ``name`` may start or join a visit with its persona (call before reserving a room).

    Refused when the persona is missing, unreviewed or being generated. When
    the card changed since generation and the persona was never edited, a
    background regeneration starts and the visit is refused
    (``state='generating'``); an edited persona stays usable.
    """
    character_uid = await _hooks.resolve_char_uid(name)
    if not character_uid:
        return PersonaGate(ok=False, state="missing")
    for _attempt in range(_GATE_ATTEMPTS):
        if is_generating(character_uid):
            return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        version = _write_versions.get(character_uid, 0)
        doc = await store().load(character_uid)
        if doc is None or not doc["reviewed"]:
            return PersonaGate(ok=False, state=persona_state(doc, character_uid), character_uid=character_uid)
        if not doc["edited"]:
            ctx = await _hooks.load_context()
            if card_hash(ctx.card(name)) != doc["source_card_hash"]:
                start_regeneration(name, character_uid)
                return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        if is_generating(character_uid):
            # 读盘 / 读卡期间另一个窗口点了重新生成：手里这份已不作数
            return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        if _write_versions.get(character_uid, 0) == version:
            return PersonaGate(ok=True, state="ready", character_uid=character_uid, text=doc["text"])
        # 读的过程中人设被写过（手写确认 / 重生成落盘）：按新的那份再判一次
    return PersonaGate(ok=False, state="generating", character_uid=character_uid)


# ── 路由 ───────────────────────────────────────────────────────────────


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


async def _view(name: str, character_uid: str, ctx: CharacterContext) -> dict:
    doc = await store().load(character_uid)
    current_hash = card_hash(ctx.card(name))
    out = {
        "catgirl": name,
        "character_uid": character_uid,
        "state": persona_state(doc, character_uid),
        "text": doc["text"] if doc else None,
        "edited": bool(doc and doc["edited"]),
        "reviewed": bool(doc and doc["reviewed"]),
        "generated_at": doc["generated_at"] if doc else None,
        "card_changed": bool(doc) and doc["source_card_hash"] != current_hash,
        "scan_complete": bool(doc and doc["scan_complete"]),
        "private_sections": list(doc["private_sections"]) if doc else [],
        # 私人段落清单基于哪张卡：与当前卡不一致时面板提示「清单基于旧卡片」
        "sections_outdated": bool(doc) and doc["scan_card_hash"] != current_hash,
    }
    error = _errors.get(character_uid)
    if error and not is_generating(character_uid):
        out["error"] = error
    return out


async def _resolve(name: str) -> tuple[str, CharacterContext] | JSONResponse:
    if not isinstance(name, str) or not name:
        return _error(400, "catgirl_required")
    character_uid = await _hooks.resolve_char_uid(name)
    ctx = await _hooks.load_context()
    if not character_uid or ctx.card(name) is None:
        return _error(404, "unknown_catgirl")
    return character_uid, ctx


@router.get("/persona")
async def get_persona(request: Request, catgirl: str = ""):
    """The visit persona of ``catgirl`` and its review state (read-only, no scan)."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    resolved = await _resolve(catgirl)
    if isinstance(resolved, JSONResponse):
        return resolved
    character_uid, ctx = resolved
    return JSONResponse(await _view(catgirl, character_uid, ctx))


@router.put("/persona")
async def put_persona(request: Request, catgirl: str = ""):
    """Confirm the persona (``reviewed:true``), optionally replacing its text by hand."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    if payload.get("reviewed") is not True:
        return _error(400, "reviewed_required")
    text = payload.get("text")
    if text is not None and not isinstance(text, str):
        return _error(400, "invalid_text")
    resolved = await _resolve(catgirl)
    if isinstance(resolved, JSONResponse):
        return resolved
    character_uid, ctx = resolved
    # 与后台重生成的落盘互斥：检查「没在生成」到写盘之间不能被重生成插进来
    async with persona_lock(character_uid):
        if is_generating(character_uid):
            return _error(409, "persona_generating")
        persona_store = store()
        doc = await persona_store.load(character_uid)
        if text is None:
            if doc is None:
                return _error(409, "persona_missing")
            doc = {**doc, "reviewed": True}
        else:
            lang = _hooks.lang()
            cleaned = redact_outbound(
                strip_control_chars(text).strip(), family_names=ctx.family_names,
                replacement=get_family_neutral_term(lang),
            ).strip()
            if not cleaned:
                return _error(400, "invalid_text")
            if count_tokens(cleaned) > VISIT_PERSONA_MAX_TOKENS:
                return _error(400, "persona_too_long")
            card = ctx.card(catgirl)
            # 与生成路径同一套检查：规则敏感词 + 与私人段落（规则段落 + 同一张卡扫描出的段落）的 8-gram
            scanned = doc["private_sections"] if doc is not None and doc["scan_card_hash"] == card_hash(card) else ()
            hits = persona_privacy_check(card, cleaned, ctx.family_names, scanned)
            if hits:
                return _error(400, "persona_sensitive_overlap", hits=[hit.value for hit in hits])
            if doc is None:
                # 从没生成过就手写：清单只有规则段落，没有独立扫描
                digest = card_hash(card)
                doc = {
                    "text": cleaned, "source_card_hash": digest, "generated_at": None,
                    "edited": True, "reviewed": True,
                    "private_sections": _merge_sections(rule_private_sections(card, ctx.family_names))[0],
                    "scan_card_hash": digest, "scan_complete": False,
                }
            else:
                # 手写不动私人段落清单与它依据的卡片哈希
                doc = {**doc, "text": cleaned, "edited": True, "reviewed": True}
        await persona_store.save(character_uid, doc)
        _note_write(character_uid)
        _errors.pop(character_uid, None)
    return JSONResponse(await _view(catgirl, character_uid, ctx))


@router.post("/persona/regenerate")
async def regenerate_persona(request: Request, catgirl: str = ""):
    """Regenerate the persona in the background (202); the result needs a new review."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    resolved = await _resolve(catgirl)
    if isinstance(resolved, JSONResponse):
        return resolved
    character_uid, _ctx = resolved
    if start_regeneration(catgirl, character_uid) is None:
        return _error(409, "persona_generating")
    return JSONResponse({"state": "generating"}, status_code=202)
