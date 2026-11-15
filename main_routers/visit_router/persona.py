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
import json
import math
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
_SECTION_CHUNK_OVERLAP = 200
_TOKEN_MIN_CHARS = 2
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


# -- Rule-sensitive terms and private passages --

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。、！？；：,;:()（）\[\]【】<>「」『』\"']+", re.IGNORECASE)
# Sentence-final punctuation is not part of URLs: see https://a.example/x. -> https://a.example/x.
_URL_TRAILING = ".,!?'\"…"
# Bare domains such as private-family.example match lowercase only, avoiding names like Mr.Smith being treated as hosts.
_HOST_RE = re.compile(r"(?<![A-Za-z0-9@._\-])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?![A-Za-z0-9_\-])")
_DIGITS_RE = re.compile(r"[0-9]{5,}")
# Values after keywords extend to punctuation or line end and may contain spaces: 住在桂花路 -> 桂花路.
# 「address: 12 Main Street」→「12 Main Street」）
_KEYWORD_VALUE = r"(?P<value>[^\n，。、！？；：,;:!?()（）\[\]【】<>「」『』\"']{2,40})"
# Chinese address keywords commonly lack separators, e.g. 住在桂花路; explicit account/number keywords 微信号/手机号
# also allow none, e.g. 微信号小雨. Generic 微信 / 手机 / 电话 / 邮箱 require colon / 是 / 为 separators,
# or number/account-like values starting with digits, letters, +, or #; exclude 手机游戏 / 电话会议 / 微信群.
_CJK_KEYWORD_VALUE_RE = re.compile(
    r"(?:(?P<kw>住址|地址|家住|住在|位于|位於)\s*(?P<sep>[:：是为為在])?[ \t]*"
    r"|(?P<kw5>微信号|微訊號|qq号|手机号|手機號|电话号码?|電話號碼?)\s*[:：是为為]?[ \t]*"
    r"|(?P<kw4>微信|微訊|手机|手機|电话|電話|邮箱|郵箱)"
    r"(?:\s*[:：]\s*|\s*[是为為]\s*|\s*(?=[A-Za-z0-9+#@])))" + _KEYWORD_VALUE,
    re.IGNORECASE,
)
# Latin keywords match whole words (phone inside smartphone does not count), followed by real colon/equals separators,
# values starting with digits/#/+/ @ (phone 138 0013 8000 / wechat @alicefoo), or a space followed
# by an account-like first token containing digits/underscores (wechat mimi_cat) or camel case (wechat AliceFoo). Explicit ID
# keywords (line id alicefoo) accept any following word; address accepts capitalized words
# (address Maple Grove), and lives in supplies its own separator. Exclude ordinary nouns like phone games.
_LATIN_KEYWORD_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?P<kw>(?:wechat|weixin|vx|qq)(?:\s*id)?|e-?mail|phone(?:\s*number)?|address|line\s*id)(?![A-Za-z0-9])"
    r"(?:\s*[:：=]\s*|\s+(?:is\s+|at\s+)?(?=[#+0-9@])|\s+(?=[A-Za-z0-9.\-]*[0-9_])"
    r"|(?<=[iI][dD])\s+(?:is\s+)?|\s+(?=(?-i:[A-Za-z]*[a-z][A-Z])))"
    r"|(?P<kw2>lives?\s+in)\s+"
    r"|(?P<kw6>line)\s*[:：]\s*"
    r"|(?P<kw3>address)\s+(?:is\s+|at\s+|(?=(?-i:[A-Z]))))" + _KEYWORD_VALUE,
    re.IGNORECASE,
)
# Unseparated Chinese address values such as 住在一起/地址保密/家住得离公司很近 require address shapes:
# street/estate/admin suffixes, house numbers, or digits. Colon / 是 / 为 / 在 separators allow the whole value.
_CJK_ADDRESS_KEYWORDS = frozenset({"住址", "地址", "家住", "住在", "位于", "位於"})
_CJK_REGION_RE = re.compile("[" + chr(0x4E00) + "-" + chr(0x9FFF) + "]{2}(?:省|市|区|區|县|縣|镇|鎮|村|乡|鄉)")


def _cjk_address_form(value: str) -> bool:
    return bool(_ROAD_CORE_RE.search(value) or _ESTATE_CORE_RE.search(value)
                or _CJK_REGION_RE.search(value) or any(ch.isdigit() for ch in value))


# Address values stop at commas, but inspect later same-line fragments for streets too, e.g. address: Apt 4, 12 Main Street.
_ADDRESS_KEYWORDS = frozenset({"住址", "地址", "家住", "住在", "位于", "位於", "address"})
# Account-like tokens contain @, underscores, mixed letters/digits, camel case, or hyphenated words, e.g. @alicefoo/mimi_cat/cat2024.
# 「AliceFoo」「alice-foo」）
_ACCOUNT_WORD_RE = re.compile(r"[@_]|[A-Za-z][0-9]|[0-9][A-Za-z]|[a-z][A-Z]|[A-Za-z0-9]-[A-Za-z0-9]")
_ADDRESS_SEGMENT_SPLIT_RE = re.compile(r"[,，;；、]")
_LINE_END_RE = re.compile(r"[\n。！？!?]")
# Compare separated phone numbers such as 138 0013 8000 / +1 (555) 010-0199 as digits only.
_PHONE_RE = re.compile(r"\+?[0-9][0-9 \-().]{5,}[0-9]")
_PHONE_MIN_DIGITS = 7
# In Latin values, collect runs of at least two capitalized words or numbers (Main Street), excluding lowercase function words.
_LATIN_WORD_RE = re.compile(r"[0-9]+|[A-Z][A-Za-z'\-]*|[a-z][A-Za-z'\-]*")
_ROAD_SUFFIXES = "路|街|大道|巷|胡同|弄"
_ESTATE_SUFFIXES = "小区|小區|公寓|大厦|大廈|新村|社区|社區"
# Chinese address fragments keep two characters before the suffix as core: 我们住在桂花路 -> 桂花路. X路/X街 suffixes
# are also common words (走路/一路), so accept them only after address keywords or before house numbers; estate/apartment suffixes match card-wide.
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


# House-number-leading values also accept lowercase street names: 12 main street -> main street.
# Numbers inside values, e.g. a flat with 2 cats playing, are not house numbers.
_STREET_AFTER_NUMBER_RE = re.compile(
    r"\s*(?:#|no\.?\s*)?[0-9]+[A-Za-z]?\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,3})",
    re.IGNORECASE,
)


# Street names stop at the first function word: 2 cats and a dog is not an address; never collect cats and.
_STREET_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "with", "of", "in", "on", "at", "to", "for", "from", "by", "her", "his",
    "their", "my", "our", "your", "is", "are", "was", "who", "that", "which",
})


# Digits followed by zero to two words then street suffixes match even away from value start, e.g. the house is at 42 main street.
_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|way|court|ct|place|pl|terrace|"
    "crescent|close|square|sq|highway|hwy|alley|row|parkway|pkwy"
)
_NUMBERED_STREET_RE = re.compile(
    rf"(?<![A-Za-z0-9])[0-9]+[A-Za-z]?\s+((?:[A-Za-z][A-Za-z'\-]*\s+){{0,2}}(?:{_STREET_TYPES}))\b\.?",
    re.IGNORECASE,
)


# Generic dwelling nouns at the start of values (Apartment is on the top floor) are not place names.
_GENERIC_DWELLING_WORDS = frozenset({
    "apartment", "apt", "flat", "house", "home", "room", "building", "unit", "floor", "suite", "studio",
    "dorm", "dormitory", "condo", "villa", "cottage", "place", "here", "there", "near", "next", "behind",
})


_STREET_TYPE_WORD_RE = re.compile(rf"(?:{_STREET_TYPES})\.?", re.IGNORECASE)


# Street names without house numbers on address lines (Apt 4, Main Street): one or two words followed by street suffixes.
# way/place/close/court are ordinary words too (the long way around), requiring preceding capitalization;
# unambiguous street/avenue/boulevard suffixes accept lowercase main street too.
_UNNUMBERED_STREET_RE = re.compile(
    rf"(?<![A-Za-z0-9])((?:(?-i:[A-Z])[A-Za-z'\-]*\s+){{1,2}}(?:{_STREET_TYPES}))\b\.?",
    re.IGNORECASE,
)
# One capitalized word making up the entire post-comma address fragment is itself a street/place name.
_PROPER_PLACE_RE = re.compile(r"(?-i:[A-Z])[A-Za-z'\-]+")
_UNAMBIGUOUS_STREET_TYPES = "street|avenue|ave|boulevard|blvd|lane|highway|hwy|parkway|pkwy|road|rd"
_LOWERCASE_STREET_RE = re.compile(
    rf"(?<![A-Za-z0-9])((?:[A-Za-z][A-Za-z'\-]*\s+){{1,2}}(?:{_UNAMBIGUOUS_STREET_TYPES}))\b\.?",
    re.IGNORECASE,
)


def _unnumbered_streets(segment: str) -> list[str]:
    """Street-type phrases of an address-labelled segment, cut at the first function word."""
    out = []
    for m in (*_UNNUMBERED_STREET_RE.finditer(segment), *_LOWERCASE_STREET_RE.finditer(segment)):
        words = m.group(1).split()
        kept = list(reversed(_run_until_stopword(list(reversed(words)))))
        if len(kept) >= 2:
            out.append(" ".join(kept))
            if len(kept) > 2:
                # Extra prefixes may be prepositions/directions (near main street); also collect the street-name core,
                # final name word plus suffix, to match personas mentioning main street alone.
                out.append(" ".join(kept[-2:]))
    return out


# Locative prepositions make a following single capitalized word a place (Apartment near Broadway); with/and do not
# (Garden in House with Garden is descriptive, not a place name).
_PLACE_PREPOSITIONS = frozenset({
    "in", "on", "at", "near", "by", "off", "behind", "beside", "opposite", "along", "across", "next",
})
# Final words of two-word prepositions (next to/close to/across from/away from/far from) count only with their preceding word.
_PLACE_PREPOSITION_TAILS = {"to": frozenset({"next", "close"}), "from": frozenset({"across", "away", "far"})}


def _qualified_places(words: list[str]) -> list[str]:
    """Single proper words after a dwelling noun / preposition in an address value (``Apartment near Broadway``)."""
    out = []
    cleaned = [w.strip(".,!?;:") for w in words]
    for i in range(1, len(cleaned)):
        word, before = cleaned[i], cleaned[i - 1].lower()
        after = cleaned[i + 1] if i + 1 < len(cleaned) else ""
        two_word = i >= 2 and cleaned[i - 2].lower() in _PLACE_PREPOSITION_TAILS.get(before, ())
        if (word[:1].isupper() and len(word) >= _TOKEN_MIN_CHARS
                and word.lower() not in _STREET_STOPWORDS | _GENERIC_DWELLING_WORDS
                and (before in _PLACE_PREPOSITIONS | _GENERIC_DWELLING_WORDS or two_word)
                and not after[:1].isupper()):
            out.append(word)
    return out


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
        single = len(words) == 1 or (
            len(words) > 1 and words[1][:1].islower() and not _STREET_TYPE_WORD_RE.fullmatch(words[1]))
        if words and single and words[0][:1].isupper():
            # One capitalized word after a house number is the street name (12 Broadway / 12 Broadway likes cats, followed by lowercase
            # narration). A following street suffix or capitalized word (12 Main Street / 12 Maple
            # Grove) makes a multiword street, not separate single names; lowercase single nouns like lives in 2 cities are excluded.
            out.append(words[0])
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
            # Value-final punctuation is not content (LINE: alicefoo.); retaining it prevents matching alicefoo repeated in persona prose.
            value = m.group("value").strip().rstrip(".,!?;:").rstrip()
            if not value:
                continue
            keyword = (m.group("kw") or "").lower()
            if keyword in _CJK_ADDRESS_KEYWORDS and not m.groupdict().get("sep") and not _cjk_address_form(value):
                continue
            found.append(value)
            found.extend(_place_cores(value, _ROAD_CORE_RE))
            found.extend(_latin_phrases(value))
            found.extend(_street_names(value))
            is_address = keyword in _ADDRESS_KEYWORDS or m.groupdict().get("kw2") or m.groupdict().get("kw3")
            if not is_address:
                # Contact values can swallow surrounding prose (wechat @alicefoo likes cats / wechat: usually
                # online as @alicefoo). Also collect account-like words independently so account-only persona mentions match. Exclude ordinary
                # words such as usually and number fragments such as +1, handled by separate digit/phone rules.
                words = value.split()
                if len(words) > 1:
                    found.extend(w.strip(".,!?;:") for w in words if _ACCOUNT_WORD_RE.search(w))
                    if keyword.endswith("id") or m.groupdict().get("kw5") or m.groupdict().get("kw6"):
                        # Explicit account keywords (line id/wechat id/微信号) identify their first following word as the account itself.
                        found.append(words[0].strip(".,!?;:"))
            if is_address:
                words = value.split()
                if len(words) > 1 and words[0][:1].isupper() and words[1][:1].islower() \
                        and words[0].lower().strip(".,!?;:") not in _STREET_STOPWORDS | _GENERIC_DWELLING_WORDS:
                    # A single capitalized word starting an address value before lowercase narration (Broadway likes cats) is the place name.
                    found.append(words[0].strip(".,!?;:"))
                found.extend(_qualified_places(words))
                end = _LINE_END_RE.search(text, m.start("value"))
                rest = text[m.end("value"):end.start() if end else len(text)]
                for segment in _ADDRESS_SEGMENT_SPLIT_RE.split(rest):
                    segment = segment.strip()
                    if segment:
                        # Collect only address-shaped fragments (street cores, house-number+street, street-suffix names), never ordinary
                        # capitalized hobby phrases later on the same line such as enjoys Star Wars.
                        found.extend(_place_cores(segment, _ROAD_CORE_RE))
                        found.extend(_street_names(segment))
                        found.extend(_unnumbered_streets(segment))
                        if _PROPER_PLACE_RE.fullmatch(segment):
                            # An entire fragment of one capitalized word (Apt 4, Broadway) is the place name on an address line.
                            found.append(segment)
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


def rule_private_sections(
    card: str | None, family_names: Iterable[str], *, tokens: Sequence[str] | None = None,
) -> list[str]:
    """Sections containing ``{MASTER_NAME}``, a family name or a sensitive token.

    ``tokens``: :func:`extract_sensitive_tokens` of the same card, when the
    caller already has them (the extraction is the expensive part).
    """
    if tokens is None:
        tokens = extract_sensitive_tokens(card, family_names)
    keys = [fold_text(t) for t in tokens]
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


def sensitive_token_hits(
    card: str | None, text: str, family_names: Iterable[str], *, tokens: Sequence[str] | None = None,
) -> list[str]:
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
    for token in extract_sensitive_tokens(card, names) if tokens is None else tokens:
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
    """Both automatic checks of a (redacted) persona against its card; empty = clean.

    CPU-bound (dozens of regular expressions over the whole card, tens of
    milliseconds on a long card): async callers run it in a worker thread.
    """
    names = list(family_names)
    tokens = extract_sensitive_tokens(card, names)
    hits = [PrivacyHit("token", t) for t in sensitive_token_hits(card, text, names, tokens=tokens)]
    sections = [
        *rule_private_sections(card, names, tokens=tokens), *(s for s in scanned_sections if isinstance(s, str)),
    ]
    gram = find_peer_ngram(text, sections, VISIT_PEER_NGRAM_N)
    if gram is not None:
        hits.append(PrivacyHit("section", " ".join(gram)))
    return hits


_VERSIONED_FIELDS = (
    "text", "source_card_hash", "generated_at", "edited", "private_sections", "scan_card_hash", "scan_complete",
)


def persona_version(doc: dict) -> str:
    """Version tag of everything the review panel shows (all fields but ``reviewed``).

    A confirmation must name the version the user saw: a regeneration that
    happens to produce the same text still brings a new private-section list.
    """
    payload = json.dumps({k: doc.get(k) for k in _VERSIONED_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def card_hash(card: str | None) -> str:
    """``sha256`` of the raw card text (detects card edits after the persona was made)."""
    return hashlib.sha256((card or "").encode("utf-8", "surrogatepass")).hexdigest()


# -- Storage --


def _utf8_ok(text: str) -> bool:
    # Escaped lone JSON surrogates ("\ud800") parse as strings but cannot encode to UTF-8, causing whole-panel responses to return 500.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_doc(doc: Any) -> bool:
    if not isinstance(doc, dict) or set(doc) != set(PERSONA_FIELDS):
        return False
    sections = doc["private_sections"]
    generated_at = doc["generated_at"]
    return (
        isinstance(doc["text"], str) and _utf8_ok(doc["text"])
        and isinstance(doc["source_card_hash"], str) and _HASH_RE.fullmatch(doc["source_card_hash"]) is not None
        and (generated_at is None or (isinstance(generated_at, (int, float)) and not isinstance(generated_at, bool)
                                      and math.isfinite(generated_at)))
        and all(isinstance(doc[k], bool) for k in ("edited", "reviewed", "scan_complete"))
        and isinstance(sections, list) and all(isinstance(s, str) and _utf8_ok(s) for s in sections)
        and isinstance(doc["scan_card_hash"], str) and _HASH_RE.fullmatch(doc["scan_card_hash"]) is not None
    )


class PersonaUnavailable(RuntimeError):
    """The persona file exists but cannot be read right now (in use / permissions)."""


class VisitPersonaStore:
    """``config_dir/visit_persona/<character_uid>.json`` files (one per character)."""

    def __init__(self, config_dir: str | os.PathLike[str]) -> None:
        self.dir = Path(config_dir) / VISIT_PERSONA_DIRNAME

    def path(self, character_uid: str) -> Path:
        """The persona file of ``character_uid``; ``ValueError`` for anything but 32 hex."""
        return id_path(self.dir, character_uid, CHARACTER_UID_RE, ".json")

    def _load_sync(self, character_uid: str) -> dict | None:
        path = self.path(character_uid)
        try:
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except FileNotFoundError:
            return None
        except OSError as exc:
            # Temporarily unreadable (busy/permissions) does not mean absent: report unavailable, not ungenerated, which invites overwrite through regeneration.
            logger.warning("visit persona %s unreadable: %s", path.name, type(exc).__name__)
            raise PersonaUnavailable(path.name) from None
        except (ValueError, RecursionError) as exc:
            logger.warning("visit persona %s unreadable: %s", path.name, type(exc).__name__)
            return None
        if not _valid_doc(doc):
            logger.warning("visit persona %s has an unexpected shape, ignored", path.name)
            return None
        return doc

    async def load(self, character_uid: str) -> dict | None:
        """The stored persona, or None when missing or malformed (both count as not generated).

        Raises :class:`PersonaUnavailable` when the file is there but cannot
        be read right now.
        """
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
                # Like other visit files, permissions are best effort and ineffective on Windows anyway.
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
        """Delete the persona file of ``character_uid``; True if one existed.

        Bare file operation: a deleted character's persona is retired through
        :func:`retire_persona`, which serializes with edits and regeneration.
        """
        return await asyncio.to_thread(self._retire_sync, character_uid)


# -- Generation --

PersonaLLM = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class PersonaResult:
    """Outcome of :func:`generate_visit_persona`: a document to save, or an ``error`` code."""

    doc: dict | None = None
    error: str | None = None
    hits: tuple[PrivacyHit, ...] = ()


def _clean_persona_text(raw: str, family_names: Sequence[str], lang: str | None) -> str:
    text = strip_control_chars(str(raw or "")).strip()
    # Redact before token truncation: truncation can split names beyond recognition, and neutral replacements may be longer, so enforce the cap last.
    text = redact_outbound(text, family_names=family_names, replacement=get_family_neutral_term(lang))
    return truncate_to_tokens(text, VISIT_PERSONA_MAX_TOKENS).strip()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", fold_text(text or ""))


def _parse_scan(raw: str, card: str | None = None) -> tuple[list[str], bool]:
    """Passages of a scan reply, and whether the scan is complete.

    Incomplete when an entry is not a string, or (given ``card``) a passage
    is not copied from the card (whitespace and case aside): the model
    paraphrased or made it up, so the real passage may be missing.
    """
    text = str(raw or "").strip()
    # Models sometimes wrap JSON in ``` fences; take content from first [ through final ] only.
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("scan reply is not a JSON list")
    items = json.loads(text[start:end + 1])
    if not isinstance(items, list):
        raise ValueError("scan reply is not a JSON list")
    out = []
    complete = True
    for item in items:
        if not isinstance(item, str):
            # Noncontract strings such as {"section":...} cannot identify listed passages: honestly mark scanning incomplete,
            # while retaining recognized passages from the same response for comparison.
            complete = False
            continue
        if item.strip():
            if card is not None and _squash(item) not in _squash(card):
                # Rewritten/fabricated text is not card-verbatim: still compare it but mark incomplete because the actual passage may be omitted.
                complete = False
            out.extend(_section_chunks(item.strip()))
    return out, complete


def _section_chunks(section: str) -> list[str]:
    """A passage cut into ``_SECTION_MAX_CHARS`` pieces that overlap, so no part of it is dropped."""
    if len(section) <= _SECTION_MAX_CHARS:
        return [section]
    # Overlap adjacent chunks so every cross-boundary 8-gram remains complete within at least one chunk.
    step = _SECTION_MAX_CHARS - _SECTION_CHUNK_OVERLAP
    return [section[i:i + _SECTION_MAX_CHARS] for i in range(0, len(section) - _SECTION_CHUNK_OVERLAP, step)]


def _private_section_list(
    card: str | None, family_names: Sequence[str], scanned: Iterable[str] = (),
) -> tuple[list[str], bool]:
    """The persisted private-section list: rule sections plus scanned ones (CPU-bound)."""
    return _merge_sections(rule_private_sections(card, family_names), scanned)


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
        scanned, entries_ok = _parse_scan(await asyncio.wait_for(
            scan_llm(build_visit_persona_private_scan_prompt(card_in, lang)), VISIT_LLM_TIMEOUT_S,
        ), card_in)
        # Input-budget truncation leaves the tail unscanned: report incomplete honestly.
        scan_complete = entries_ok and len(card_in) >= len(card or "")
    except Exception as exc:  # noqa: BLE001 - Scan failure falls back to rule passages and persists incomplete status honestly.
        logger.warning("visit persona: private-section scan failed: %s", type(exc).__name__)
        scanned, scan_complete = [], False
    hits: list[PrivacyHit] = []
    for _attempt in range(2):
        try:
            rejected_fragments = truncate_to_tokens("\n".join(hit.value for hit in hits), VISIT_PERSONA_MAX_TOKENS)
            raw = await asyncio.wait_for(llm(build_visit_persona_prompt(
                card_in, lang, rejected_fragments=rejected_fragments,
            )), VISIT_LLM_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit persona: generation failed: %s", type(exc).__name__)
            return PersonaResult(error="llm_unavailable")
        text = _clean_persona_text(raw, names, lang)
        if not text:
            return PersonaResult(error="llm_unavailable")
        # Dozens of regexes on long cards take tens of milliseconds; never calculate on the event loop.
        hits = await asyncio.to_thread(persona_privacy_check, card, text, names, scanned)
        if not hits:
            digest = card_hash(card)
            sections, cut = await asyncio.to_thread(_private_section_list, card, names, scanned)
            if cut:
                # An oversized list hides excluded passages from the panel; report incomplete honestly.
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


# -- Runtime hooks and background generation --


def _default_config_dir() -> Path:
    from utils.config_manager import get_config_manager

    return Path(get_config_manager().config_dir)


@dataclass
class PersonaHooks:
    """Injection points (tests and PR-09b wiring); see :func:`configure_persona`."""

    config_dir: Callable[[], Path]
    load_context: Callable[[], Awaitable[CharacterContext]]
    resolve_char_uid: Callable[[str], Awaitable[str | None]]
    resolve_char_name: Callable[[str], Awaitable[str | None]]
    llm: PersonaLLM
    scan_llm: PersonaLLM
    lang: Callable[[], str]


_hooks = PersonaHooks(
    config_dir=_default_config_dir,
    load_context=load_character_context,
    resolve_char_uid=local_chars.resolve_char_uid,
    resolve_char_name=local_chars.resolve_char_name,
    llm=visit_llm.one_shot_llm(max_tokens=VISIT_PERSONA_MAX_TOKENS + 200, timeout=VISIT_LLM_TIMEOUT_S),
    scan_llm=visit_llm.one_shot_llm(max_tokens=PERSONA_SCAN_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S),
    lang=prompt_lang,
)


def configure_persona(**hooks: Any) -> None:
    """Replace hooks: ``config_dir``, ``load_context``, ``resolve_char_uid``, ``resolve_char_name``, ``llm``,
    ``scan_llm``, ``lang``."""
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
        # Resolve the character's current name by UID before reading its card; rename plus same-name creation must not put the new character's card into the original persona.
        # Read the card snapshot before resolving names: an intervening rename makes the new name absent from the old snapshot, yielding missing rather than another character's card.
        ctx = await _hooks.load_context()
        current = await _hooks.resolve_char_name(character_uid)
        card = ctx.card(current) if current is not None else None
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
            if await _hooks.resolve_char_name(character_uid) is None:
                # Character deleted during generation: do not recreate its persona file after deletion.
                # Check by UID: renames still allow saving because UID-keyed files are unaffected.
                _errors[character_uid] = "unknown_catgirl"
                logger.info("visit persona: character gone during regeneration, result dropped")
                return
            if _write_versions.get(character_uid, 0) != started_version:
                # Persona written since generation began, including another window's confirmation that already held the lock:
                # never overwrite it with generated output.
                logger.info("visit persona: regeneration superseded by an edit, result dropped")
                return
            await store().save(character_uid, result.doc)
            _note_write(character_uid)
        _errors.pop(character_uid, None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - Background task: record failures in state for GET visibility.
        logger.warning("visit persona: regeneration failed: %s", type(exc).__name__)
        _errors[character_uid] = "llm_unavailable"


async def retire_persona(character_uid: str) -> bool:
    """Retire the persona of a deleted character (``pending_retire``, PR-09b); True if a file existed.

    Holds the per-character persona lock, so a hand edit or a regeneration
    commit already writing finishes first and the file is deleted after it.
    A running regeneration is not cancelled: cancelling would release the
    lock while its write may still run in a worker thread. It drops its
    result instead (the character is gone and the write version moved on).
    """
    async with persona_lock(character_uid):
        _note_write(character_uid)
        _errors.pop(character_uid, None)
        return await store().retire(character_uid)


def start_regeneration(name: str, character_uid: str) -> asyncio.Task | None:
    """Start a background regeneration; None when one is already running for this character."""
    if is_generating(character_uid):
        return None
    _errors.pop(character_uid, None)
    # Capture write version synchronously at startup, without await; any later write invalidates this result.
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
    character_uid: str | None = None
    for _attempt in range(_GATE_ATTEMPTS):
        # Resolve characters by name anew each round: if the original renamed and another took its name during reads, never authorize using the original persona.
        character_uid = await _hooks.resolve_char_uid(name)
        if not character_uid:
            return PersonaGate(ok=False, state="missing")
        if is_generating(character_uid):
            return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        version = _write_versions.get(character_uid, 0)
        try:
            doc = await store().load(character_uid)
        except PersonaUnavailable:
            # Existing but temporarily unreadable files are neither absent (which invites regeneration) nor acceptable.
            return PersonaGate(ok=False, state="unavailable", character_uid=character_uid)
        if doc is None or not doc["reviewed"]:
            return PersonaGate(ok=False, state=persona_state(doc, character_uid), character_uid=character_uid)
        ctx = await _hooks.load_context()
        if not doc["edited"]:
            if card_hash(ctx.card(name)) != doc["source_card_hash"]:
                if is_generating(character_uid):
                    return PersonaGate(ok=False, state="generating", character_uid=character_uid)
                if _write_versions.get(character_uid, 0) != version:
                    # Persona changed during card read by manual confirmation: re-evaluate the new version, never trigger regeneration from the stale copy.
                    continue
                if await _hooks.resolve_char_uid(name) != character_uid:
                    continue
                start_regeneration(name, character_uid)
                return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        if is_generating(character_uid):
            # Another window requested regeneration during disk/card reads: this copy is obsolete.
            return PersonaGate(ok=False, state="generating", character_uid=character_uid)
        if _write_versions.get(character_uid, 0) == version and await _hooks.resolve_char_uid(name) == character_uid:
            # Family profile names/nicknames may change after confirmation; redact against the current roster before departure, since old confirmation cannot cover new names.
            # Match generation order: neutral replacements may be longer, so enforce the token limit after replacement again.
            text = _clean_persona_text(doc["text"], ctx.family_names, _hooks.lang())
            return PersonaGate(ok=True, state="ready", character_uid=character_uid, text=text)
        # Persona written during read by manual confirmation / regenerated persistence: evaluate the new copy again.
    # Repeated concurrent writes do not mean generating; reject as pending confirmation and direct users to the latest panel version, avoiding endless frontend generating waits.
    return PersonaGate(ok=False, state="unreviewed", character_uid=character_uid)


# -- Routes --


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
        # Confirmation-only PUT without text returns this: users must confirm the exact body and private-passage list they viewed.
        "persona_version": persona_version(doc) if doc else None,
        "edited": bool(doc and doc["edited"]),
        "reviewed": bool(doc and doc["reviewed"]),
        "generated_at": doc["generated_at"] if doc else None,
        "card_changed": bool(doc) and doc["source_card_hash"] != current_hash,
        "scan_complete": bool(doc and doc["scan_complete"]),
        "private_sections": list(doc["private_sections"]) if doc else [],
        # Card basis for the private-passage list: the panel warns when it differs from the current card.
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
    if await _hooks.resolve_char_uid(name) != character_uid:
        # Rename and same-name replacement between steps: never pair the original persona with the new character's card.
        return _error(409, "catgirl_changed")
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
    return await _view_response(catgirl, character_uid, ctx)


def _scanned_sections(doc: dict | None, card: str | None) -> Sequence[str]:
    """Scanned private sections a hand-written persona is checked against for the current ``card``."""
    if doc is None:
        return ()
    if doc["scan_card_hash"] == card_hash(card):
        return doc["private_sections"]
    # Changed cards leave scans based on old text; still compare passages retained verbatim in the current card, common when hand-editing after card changes.
    # Match scan normalization: case / fullwidth-halfwidth / whitespace-only changes still count as present.
    current = _squash(card or "")
    return [section for section in doc["private_sections"] if _squash(section) in current]


async def _view_response(name: str, character_uid: str, ctx: CharacterContext) -> JSONResponse:
    try:
        return JSONResponse(await _view(name, character_uid, ctx))
    except PersonaUnavailable:
        # Temporarily busy persona files report unavailable for later panel retry, never ungenerated.
        return _error(503, "persona_unavailable")


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
    # Exclude background regeneration persistence: it cannot enter between checking not-generating and writing.
    async with persona_lock(character_uid):
        if is_generating(character_uid):
            return _error(409, "persona_generating")
        persona_store = store()
        try:
            doc = await persona_store.load(character_uid)
        except PersonaUnavailable:
            # Unreadability means unknown disk identity: neither create as never-generated nor confirm; retry later.
            return _error(503, "persona_unavailable")
        previous = doc
        if await _hooks.resolve_char_name(character_uid) is None:
            # Character deleted since request arrival: do not recreate its deleted persona file.
            return _error(404, "unknown_catgirl")
        if await _hooks.resolve_char_uid(catgirl) != character_uid:
            # The name now identifies another character after rename/recreation; name-based card reads return the new card,
            # which must not enter the original persona. Make the panel refetch.
            return _error(409, "catgirl_changed")
        if text is not None:
            # Reread cards under lock: if private content was added while waiting, check the new card. Edited personas have edited=True,
            # preventing automatic regeneration on card changes, so old-card validation would permit new private content.
            ctx = await _hooks.load_context()
            if ctx.card(catgirl) is None:
                return _error(404, "unknown_catgirl")
        if text is None:
            if doc is None:
                return _error(409, "persona_missing")
            seen = payload.get("persona_version")
            if not isinstance(seen, str):
                return _error(400, "persona_version_required")
            if seen != persona_version(doc):
                # Persona replaced since panel opening by another window's regeneration / card-triggered regeneration: the user has not viewed this version.
                return _error(409, "persona_changed")
            if doc["edited"]:
                # Recheck edited text against the current card before confirmation: it may be pending manual text from interrupted persistence, validated only against an old card;
                # edited personas no longer regenerate on card changes, so accepting here would keep it indefinitely.
                ctx = await _hooks.load_context()
                card = ctx.card(catgirl)
                if card is None:
                    return _error(404, "unknown_catgirl")
                hits = await asyncio.to_thread(
                    persona_privacy_check, card, doc["text"], ctx.family_names, _scanned_sections(doc, card))
                if hits:
                    return _error(400, "persona_sensitive_overlap", hits=[hit.value for hit in hits])
                if card_hash((await _hooks.load_context()).card(catgirl)) != card_hash(card):
                    return _error(409, "persona_card_changed")
                if await _hooks.resolve_char_uid(catgirl) != character_uid:
                    # Character identity changed during checking, even with identical card text: do not confirm for the original character.
                    return _error(409, "catgirl_changed")
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
            # Same generation checks: sensitive-rule terms plus 8-grams against private passages from rules and the same card's scan.
            hits = await asyncio.to_thread(
                persona_privacy_check, card, cleaned, ctx.family_names, _scanned_sections(doc, card))
            if hits:
                return _error(400, "persona_sensitive_overlap", hits=[hit.value for hit in hits])
            if card_hash((await _hooks.load_context()).card(catgirl)) != card_hash(card):
                # Cards can change outside persona locks; do not save manual text checked against an old card. Panel resubmission checks the new one.
                return _error(409, "persona_card_changed")
            if doc is None:
                # Manual creation before any generation: list contains rule passages only, without independent scanning.
                digest = card_hash(card)
                doc = {
                    "text": cleaned, "source_card_hash": digest, "generated_at": None,
                    "edited": True, "reviewed": True,
                    "private_sections": (await asyncio.to_thread(
                        _private_section_list, card, ctx.family_names))[0],
                    "scan_card_hash": digest, "scan_complete": False,
                }
            else:
                # Manual edits leave private-passage lists and their basis-card hashes unchanged.
                doc = {**doc, "text": cleaned, "edited": True, "reviewed": True}
        if text is not None:
            # Persist manual edits in two steps: pending confirmation first, then confirm only after verifying cards remained unchanged during writing outside persona locks.
            # Intermediate gates see only pending confirmation and reject; failed verification/withdrawal or process exit likewise leaves only pending state.
            await persona_store.save(character_uid, {**doc, "reviewed": False})
            _note_write(character_uid)
            try:
                changed = card_hash((await _hooks.load_context()).card(catgirl)) != card_hash(card)
                # Character identity changed during validation, even with identical card text: this manual edit is not for the original character.
                changed = changed or await _hooks.resolve_char_uid(catgirl) != character_uid
            except Exception as exc:  # noqa: BLE001 - Unverifiable means changed: prefer refusing persistence.
                logger.warning("visit persona: cannot recheck the card: %s", type(exc).__name__)
                changed = True
            if changed:
                # Manual text checked only against the old card: withdraw and restore the original. Card edits after persistence instead count as
                # edited personas remaining stable across card changes, outside this case.
                if previous is None:
                    await persona_store.retire(character_uid)
                else:
                    await persona_store.save(character_uid, previous)
                _note_write(character_uid)
                return _error(409, "persona_card_changed")
        await persona_store.save(character_uid, doc)
        _note_write(character_uid)
        _errors.pop(character_uid, None)
    return await _view_response(catgirl, character_uid, ctx)


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
