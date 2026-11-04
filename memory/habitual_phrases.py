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

"""HabitualPhraseStore -- per-character list of phrases she keeps reusing.

The natural-expression miner (``utils/natural_expression_candidates.py``)
already finds repeated wording in her replies, but only for the review panel;
nothing ever acted on it. This module turns its output into an avoid list that
the next session start reads.

- **Computed in the background, read at session start.** ``compute_entries``
  is pure and deterministic -- no model call, no network. The memory server
  runs it while idle, about once a day
  (``app/memory_server/habitual_phrase_loop.py``), and writes
  ``memory/{name}/habitual_phrases.json``. ``_build_initial_prompt`` only reads
  that file; nothing on the session-start path mines.
- **Across sessions, not within one.** Rows of time_indexed.db carry one id per
  memory write batch rather than per sitting, so sessions are rebuilt from
  reply timestamps: a silence longer than ``HABITUAL_PHRASE_SESSION_GAP_SECONDS``
  starts a new one. A phrase qualifies only when it shows up in
  ``HABITUAL_PHRASE_MIN_SESSIONS`` distinct sessions; a dense burst inside one
  session never does.
- **What her card gives her stays hers.** A phrase written into her card or
  system prompt is never listed, and neither is one that contains a short card
  value, a quoted card phrase, her name, the user's name, or either one's
  nicknames. Code and URLs never reach the list: the miner drops every reply
  that carries either.
- **Nothing is permanent.** Every recompute replaces the whole list, so a
  phrase she stopped using drops out on the next pass, and a file older than
  ``HABITUAL_PHRASE_MAX_AGE_SECONDS`` is ignored as well.

Stored shape, kept stable because later jobs read it as input::

    {
      "schema_version": "habitual-phrases/v1",
      "computed_at": <epoch seconds>,
      "language": <miner language tag, e.g. "zh-CN">,
      "parameters": {"min_sessions", "session_gap_seconds", "max_items"},
      "source": {"reply_count", "analyzed_reply_count", "session_count",
                 "window_start_at", "window_end_at"},
      "entries": [{"phrase", "normalized_phrase", "occurrence_count",
                   "message_count", "session_count", "day_count",
                   "first_seen_at", "last_seen_at"}, ...]
    }

Entries are ranked by sessions, then days, then occurrences. Times are epoch
seconds; ``*_seen_at`` are the stored times of the first and last reply the
phrase was mined from.

Logging: phrases are her own words, so ``logger`` only ever records counts.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from config import (
    CHARACTER_RESERVED_FIELDS,
    HABITUAL_PHRASE_MAX_AGE_SECONDS,
    HABITUAL_PHRASE_MAX_ITEMS,
    HABITUAL_PHRASE_MIN_SESSIONS,
    HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
    HABITUAL_PHRASE_SESSION_GAP_SECONDS,
)
from config.prompts.prompts_directives import render_habitual_phrases_block
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.language_utils import normalize_language_code
from utils.logger_config import get_module_logger
from utils.natural_expression_candidates import (
    DEFAULT_CJK_NGRAM_MIN,
    DEFAULT_WORD_NGRAM_MIN,
    USER_REVIEW_MAX_OCCURRENCES,
    CandidateBudgetExceededError,
    MiningConfig,
    SourceMessage,
    build_report,
)
from utils.tokenize import count_tokens

logger = get_module_logger(__name__, "Memory")


HABITUAL_PHRASES_FILENAME = "habitual_phrases.json"
SCHEMA_VERSION = "habitual-phrases/v1"

# Languages the miner tokenizes by whitespace. Mirrors the miner's own set: a
# name or card term in these is matched as whole words, while in zh/ja/ko it
# is matched as a substring, since a particle attaches straight to it.
_SPACED_LANGUAGES = frozenset({"en", "es", "pt", "ru"})
# A reply longer than this is mined from its head only. A catchphrase does not
# hide in the tail of a pasted wall of text, and one such reply must not be
# what busts the occurrence budget for the whole window.
_MAX_REPLY_CHARACTERS = 2000
# A card value this short reads as a term (a self-address, a verbal tic, a
# nickname) rather than prose, and so excludes every phrase containing it.
_MAX_CARD_TERM_CHARACTERS = 12
# Text inside these quotes in her card is something she is MEANT to say.
# ASCII single quotes are left out: they are apostrophes far more often.
_CARD_QUOTE_RE = re.compile(
    r"「([^「」\n]+)」|『([^『』\n]+)』|“([^“”\n]+)”|‘([^‘’\n]+)’|\"([^\"\n]+)\""
)
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
# How far a mined window may be grown back into the phrase it came from.
# The miner stops at 8 characters / 5 words; a catchphrase can run longer, but
# a prompt line has to stay short.
_MAX_PHRASE_CHARACTERS = 16
_MAX_PHRASE_WORDS = 8
# A clock this far ahead of the stored time is a rollback, not a fresh file.
_FUTURE_SKEW_SECONDS = 86400.0


@dataclass(frozen=True)
class TimedReply:
    """One assistant reply with the time it was stored."""

    text: str
    at: datetime


def _fold(text: str) -> str:
    """The miner's own normalization: NFKC, then casefold."""
    return unicodedata.normalize("NFKC", text).casefold()


def _words(text: str) -> List[str]:
    return _WORD_RE.findall(_fold(text))


def _compact(text: str) -> str:
    """Letters and digits only, so a quoted tic with a trailing "~" matches as its word."""
    return "".join(ch for ch in _fold(text) if ch.isalnum())


def _word_runs(normalized: str) -> set[tuple[str, ...]]:
    words = normalized.split(" ")
    size = DEFAULT_WORD_NGRAM_MIN
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _char_runs(normalized: str) -> set[str]:
    compact = normalized.replace(" ", "")
    size = DEFAULT_CJK_NGRAM_MIN
    return {compact[i : i + size] for i in range(len(compact) - size + 1)}


class _Claims:
    """Wording already taken by a listed or excluded phrase.

    The miner emits every n-gram, so one long catchphrase surfaces as many
    overlapping fragments with near-identical counts. Two phrases overlap when
    they share a run the miner would itself have mined as a phrase: two words
    when both are word phrases, otherwise four characters (spaces ignored).
    Claimed runs are kept as sets, so a check costs one pass over the phrase
    however many phrases are claimed; a reply repeated verbatim yields
    thousands of fragments, and pairwise comparison took tens of seconds.
    """

    def __init__(self) -> None:
        self._word_runs: set[tuple[str, ...]] = set()
        self._char_runs_of_words: set[str] = set()
        self._char_runs_of_chars: set[str] = set()

    def overlaps(self, normalized: str) -> bool:
        chars = _char_runs(normalized)
        if " " in normalized:
            return not (
                self._word_runs.isdisjoint(_word_runs(normalized))
                and self._char_runs_of_chars.isdisjoint(chars)
            )
        return not (
            self._char_runs_of_chars.isdisjoint(chars)
            and self._char_runs_of_words.isdisjoint(chars)
        )

    def add(self, normalized: str) -> None:
        if " " in normalized:
            self._word_runs |= _word_runs(normalized)
            self._char_runs_of_words |= _char_runs(normalized)
        else:
            self._char_runs_of_chars |= _char_runs(normalized)


_Unit = Optional[tuple[str, str]]


def _units(text: str, spaced: bool) -> List[_Unit]:
    """``(folded, surface)`` per word or character; None marks a boundary.

    Words when ``spaced``, else characters. Punctuation is a boundary, so a
    phrase is never grown across a sentence break.
    """
    nfkc = unicodedata.normalize("NFKC", text)
    if not spaced:
        return [(ch.casefold(), ch) if ch.isalnum() else None for ch in nfkc]
    units: List[_Unit] = []
    cursor = 0
    for match in _WORD_RE.finditer(nfkc):
        if units and nfkc[cursor : match.start()].strip():
            units.append(None)
        units.append((match.group().casefold(), match.group()))
        cursor = match.end()
    return units


@dataclass(frozen=True)
class _MessageUnits:
    """One reply split into units once, with a key to locate a phrase in it."""

    units: List[_Unit]
    # Folded words for word phrases; for character phrases a string with
    # one character per unit, so a phrase is found with ``str.find``.
    key: Any

    @classmethod
    def of(cls, text: str, spaced: bool) -> "_MessageUnits":
        units = _units(text, spaced)
        if spaced:
            return cls(units, [unit[0] if unit else None for unit in units])
        return cls(
            units,
            "".join(unit[0] if unit and len(unit[0]) == 1 else "\x00" for unit in units),
        )

    def starts(self, normalized: str, spaced: bool) -> List[int]:
        if spaced:
            needle = normalized.split(" ")
            size = len(needle)
            return [
                start for start in range(len(self.key) - size + 1)
                if self.key[start : start + size] == needle
            ]
        found: List[int] = []
        start = self.key.find(normalized)
        while start != -1:
            found.append(start)
            start = self.key.find(normalized, start + 1)
        return found


def _maximal_phrase(
    normalized: str, messages: Sequence[_MessageUnits]
) -> Optional[tuple[str, str]]:
    """Grow a mined window into the whole phrase it sits in: ``(folded, surface)``.

    The miner emits fixed-size windows, so a catchphrase longer than its
    largest window comes back as several shifted ones, and a name at either
    end can fall outside the window that wins. Growing the window for as long
    as EVERY occurrence agrees on the next unit recovers the phrase she
    actually says, so dedup and exclusion judge that rather than a slice of
    it. The grown phrase occurs exactly where the window does, so the
    window's counts still hold. None when the window cannot be located.
    """
    spaced = " " in normalized
    size = len(normalized.split(" ")) if spaced else len(normalized)
    occurrences: list[tuple[List[_Unit], int, int]] = [
        (message.units, start, start + size)
        for message in messages
        for start in message.starts(normalized, spaced)
    ]
    if not occurrences:
        return None
    limit = _MAX_PHRASE_WORDS if spaced else _MAX_PHRASE_CHARACTERS

    def _all_agree(index_of) -> bool:
        seen: set[str] = set()
        for units, start, end in occurrences:
            index = index_of(start, end)
            unit = units[index] if 0 <= index < len(units) else None
            if unit is None:
                return False
            seen.add(unit[0])
        return len(seen) == 1

    while occurrences[0][2] - occurrences[0][1] < limit and _all_agree(lambda s, e: s - 1):
        occurrences = [(units, start - 1, end) for units, start, end in occurrences]
    while occurrences[0][2] - occurrences[0][1] < limit and _all_agree(lambda s, e: e):
        occurrences = [(units, start, end + 1) for units, start, end in occurrences]
    units, start, end = occurrences[0]
    joiner = " " if spaced else ""
    span = units[start:end]
    return joiner.join(unit[0] for unit in span), joiner.join(unit[1] for unit in span)


def _is_sublist(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    size = len(needle)
    return size > 0 and any(
        list(haystack[start : start + size]) == list(needle)
        for start in range(len(haystack) - size + 1)
    )


@dataclass(frozen=True)
class _Exclusions:
    """Card text and protected terms, pre-normalized once per computation."""

    card_compact: str
    card_words: str
    terms: tuple[tuple[tuple[str, ...], str], ...]

    def excludes(self, normalized: str, language: str) -> bool:
        spaced = language in _SPACED_LANGUAGES
        words = normalized.split(" ")
        compact = normalized.replace(" ", "")
        # Written into the card or system prompt: part of who she is.
        if spaced:
            if f" {normalized} " in self.card_words:
                return True
        elif compact and compact in self.card_compact:
            return True
        # Contains a name, a nickname or a short card term.
        for term_words, term_compact in self.terms:
            if _is_sublist(term_words, words):
                return True
            if not spaced and term_compact and term_compact in compact:
                return True
        return False


def card_text_from_character(
    character_config: Mapping[str, Any] | None,
    system_prompt: str = "",
) -> tuple[str, list[str]]:
    """Her card as one text plus its short values, from a characters.json entry.

    Reserved and private fields (model paths, voice ids, the ``_reserved``
    block) are skipped; ``system_prompt`` is the effective prompt the model
    actually reads, which is where a verbal tic is usually written down.
    Returns ``(text, short_values)``.
    """
    parts: list[str] = []
    short_values: list[str] = []
    if isinstance(character_config, Mapping):
        reserved = set(CHARACTER_RESERVED_FIELDS)
        for key, value in character_config.items():
            if not isinstance(key, str) or key in reserved or key.startswith("_"):
                continue
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                parts.append(item)
                if len(_compact(item)) <= _MAX_CARD_TERM_CHARACTERS:
                    short_values.append(item)
    if isinstance(system_prompt, str) and system_prompt.strip():
        parts.append(system_prompt)
    return "\n".join(parts), short_values


def _build_exclusions(card_text: str, protected_terms: Iterable[str]) -> _Exclusions:
    raw_terms = [term for term in protected_terms if isinstance(term, str)]
    for match in _CARD_QUOTE_RE.finditer(card_text or ""):
        quoted = next(group for group in match.groups() if group is not None)
        if len(_compact(quoted)) <= _MAX_CARD_TERM_CHARACTERS:
            raw_terms.append(quoted)
    terms: dict[tuple[tuple[str, ...], str], None] = {}
    for term in raw_terms:
        compact = _compact(term)
        # A term with no letter at all (an age, a bare "~") excludes nothing.
        if not any(ch.isalpha() for ch in compact):
            continue
        terms[(tuple(_words(term)), compact)] = None
    return _Exclusions(
        card_compact=_compact(card_text or ""),
        card_words=" " + " ".join(_words(card_text or "")) + " ",
        terms=tuple(terms),
    )


def _sessions_of(replies: Sequence[TimedReply], gap_seconds: float) -> list[int]:
    """Session index of every reply; a silence over ``gap_seconds`` opens a new one."""
    sessions: list[int] = []
    current = -1
    previous: datetime | None = None
    for reply in replies:
        if previous is None or (reply.at - previous).total_seconds() > gap_seconds:
            current += 1
        sessions.append(current)
        previous = reply.at
    return sessions


def _mine(
    messages: list[SourceMessage], *, min_messages: int
) -> tuple[dict[str, object] | None, int]:
    """Run the miner, dropping the oldest half of the window while it busts the budget."""
    window = messages
    while len(window) >= min_messages:
        try:
            report = build_report(
                window,
                input_record_count=len(window),
                config=MiningConfig(),
                rules_by_language={},
                message_count_threshold=min_messages,
                max_occurrences=USER_REVIEW_MAX_OCCURRENCES,
                include_source_lines=True,
            )
        except CandidateBudgetExceededError:
            window = window[len(window) // 2 :]
            continue
        return report, len(window)
    return None, len(window)


def _epoch(moment: datetime) -> float:
    return round(moment.timestamp(), 3)


def compute_entries(
    replies: Sequence[TimedReply],
    *,
    language: str,
    card_text: str = "",
    protected_terms: Iterable[str] = (),
    min_sessions: int = HABITUAL_PHRASE_MIN_SESSIONS,
    session_gap_seconds: float = HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    max_items: int = HABITUAL_PHRASE_MAX_ITEMS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Mine the phrases she reuses across sessions. Pure: no I/O, no model.

    ``replies`` are chronological; ``language`` is a miner language tag.
    Returns ``(entries, source)`` in the stored shape (see the module docstring).
    """
    timed = sorted(
        (reply for reply in replies if reply.text and reply.at is not None),
        key=lambda reply: reply.at,
    )
    sessions = _sessions_of(timed, session_gap_seconds)
    messages = [
        SourceMessage(language, reply.text[:_MAX_REPLY_CHARACTERS], line)
        for line, reply in enumerate(timed, start=1)
    ]
    report, analyzed = _mine(messages, min_messages=min_sessions)
    source = {
        "reply_count": len(timed),
        "analyzed_reply_count": analyzed if report is not None else 0,
        "session_count": (sessions[-1] + 1) if sessions else 0,
        "window_start_at": _epoch(timed[0].at) if timed else None,
        "window_end_at": _epoch(timed[-1].at) if timed else None,
    }
    if report is None:
        return [], source

    qualified: list[dict[str, Any]] = []
    for candidate in report["candidates"]:
        lines = [line for line in candidate["source_lines"] if 1 <= line <= len(timed)]
        session_count = len({sessions[line - 1] for line in lines})
        if session_count < min_sessions:
            continue
        moments = [timed[line - 1].at for line in lines]
        qualified.append(
            {
                "phrase": str(candidate["phrase"]),
                "normalized_phrase": str(candidate["normalized_phrase"]),
                "_lines": lines,
                "occurrence_count": int(candidate["occurrence_count"]),
                "message_count": len(lines),
                "session_count": session_count,
                "day_count": len({moment.date() for moment in moments}),
                "first_seen_at": _epoch(min(moments)),
                "last_seen_at": _epoch(max(moments)),
            }
        )

    # Longer first among equal counts, so a whole catchphrase is kept and its
    # fragments, which can only tie or trail it, are the ones dropped.
    qualified.sort(
        key=lambda entry: (
            -entry["session_count"],
            -entry["day_count"],
            -entry["occurrence_count"],
            -len(entry["normalized_phrase"]),
            entry["normalized_phrase"],
        )
    )
    # Exclusion is decided on the WHOLE catchphrase, after fragments are
    # folded into it. Excluding first would drop "本喵觉得不错" for carrying
    # her card's self-address and then list its fragment "喵觉得不错", which
    # is the same habit. So an excluded phrase still claims its fragments; it
    # just is not listed itself.
    exclusions = _build_exclusions(card_text, protected_terms)
    claims = _Claims()
    split_replies: dict[tuple[int, bool], _MessageUnits] = {}

    def _split(line: int, spaced: bool) -> _MessageUnits:
        key = (line, spaced)
        if key not in split_replies:
            split_replies[key] = _MessageUnits.of(
                timed[line - 1].text[:_MAX_REPLY_CHARACTERS], spaced
            )
        return split_replies[key]

    entries: list[dict[str, Any]] = []
    for entry in qualified:
        if len(entries) >= max_items:
            break
        lines = entry.pop("_lines")
        # Checked before growing too: growing only widens a phrase, so a
        # fragment of a claimed phrase stays claimed, and skipping it here
        # spares the growth scan for every one of its many fragments.
        if claims.overlaps(entry["normalized_phrase"]):
            continue
        spaced = " " in entry["normalized_phrase"]
        grown = _maximal_phrase(
            entry["normalized_phrase"], [_split(line, spaced) for line in lines],
        )
        if grown is not None:
            entry["normalized_phrase"], entry["phrase"] = grown
        # Grown into a claimed phrase: the same wording reached from a
        # lower-ranked window. It is skipped but still claimed, so its sibling
        # windows stop at the cheap check above instead of each growing again.
        duplicate = grown is not None and claims.overlaps(entry["normalized_phrase"])
        claims.add(entry["normalized_phrase"])
        if duplicate or exclusions.excludes(entry["normalized_phrase"], language):
            continue
        entries.append(entry)
    return entries, source


def build_payload(
    entries: list[dict[str, Any]],
    source: dict[str, Any],
    *,
    language: str,
    now: float,
    min_sessions: int = HABITUAL_PHRASE_MIN_SESSIONS,
    session_gap_seconds: float = HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    max_items: int = HABITUAL_PHRASE_MAX_ITEMS,
) -> dict[str, Any]:
    """The file content for one computation."""
    return {
        "schema_version": SCHEMA_VERSION,
        "computed_at": float(now),
        "language": language,
        "parameters": {
            "min_sessions": min_sessions,
            "session_gap_seconds": session_gap_seconds,
            "max_items": max_items,
        },
        "source": dict(source),
        "entries": [dict(entry) for entry in entries],
    }


def _as_count(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_time(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    phrase = raw.get("phrase")
    normalized = raw.get("normalized_phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        return None
    if not isinstance(normalized, str) or not normalized.strip():
        return None
    return {
        "phrase": phrase.strip(),
        "normalized_phrase": normalized,
        "occurrence_count": _as_count(raw.get("occurrence_count")),
        "message_count": _as_count(raw.get("message_count")),
        "session_count": _as_count(raw.get("session_count")),
        "day_count": _as_count(raw.get("day_count")),
        "first_seen_at": _as_time(raw.get("first_seen_at")),
        "last_seen_at": _as_time(raw.get("last_seen_at")),
    }


def _normalize_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """A readable payload, or None for an absent, foreign or damaged one.

    Never raises. A wrong shape reads as absent rather than as an error, so
    the background job treats the character as due and rewrites the file;
    raising here would make every later refresh fail on the same file.
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return None
    computed_at = _as_time(raw.get("computed_at"))
    language = raw.get("language")
    raw_entries = raw.get("entries")
    parameters = raw.get("parameters", {})
    source = raw.get("source", {})
    if (
        computed_at is None
        or not isinstance(language, str)
        or not language
        or not isinstance(raw_entries, list)
        or not isinstance(parameters, dict)
        or not isinstance(source, dict)
    ):
        return None
    entries = [
        entry
        for entry in (_normalize_entry(item) for item in raw_entries)
        if entry is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "computed_at": computed_at,
        "language": language,
        "parameters": dict(parameters),
        "source": dict(source),
        "entries": entries,
    }


_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(name: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[name] = lock
        return lock


def _short_language(raw: str) -> str:
    return normalize_language_code(raw, format="short") or ""


class HabitualPhraseStore:
    """Reads and writes ``memory/{name}/habitual_phrases.json``.

    Usage:
        store = get_habitual_phrase_store()
        store.write(name, build_payload(*compute_entries(...), language=..., now=...))
        phrases = await store.aprompt_phrases(name, lang)
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()

    # ── path ──────────────────────────────────────────────────

    def _file_path(self, name: str) -> str:
        """Path for READING; never creates the character directory."""
        return os.path.join(
            str(self._config_manager.memory_dir), name, HABITUAL_PHRASES_FILENAME
        )

    def _write_file_path(self, name: str) -> Optional[str]:
        """Save target, or None while a rename / delete fences this name."""
        from memory import _is_within_memory_root, ensure_character_dir
        from utils.character_memory import is_character_write_fenced

        if is_character_write_fenced(name):
            return None
        memory_dir = str(self._config_manager.memory_dir)
        if not _is_within_memory_root(memory_dir, name, os.path.join(memory_dir, name)):
            return None
        return os.path.join(
            ensure_character_dir(memory_dir, name), HABITUAL_PHRASES_FILENAME
        )

    # ── read ──────────────────────────────────────────────────

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        """The stored payload, normalized; None when absent or unreadable."""
        if not name:
            return None
        path = self._file_path(name)
        with _file_lock(name):
            if not os.path.exists(path):
                return None
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as exc:  # A damaged file reads as absent.
                logger.warning(
                    "[HabitualPhrases] load failed for %s, treating as absent: %s",
                    name, type(exc).__name__,
                )
                return None
        return _normalize_payload(raw)

    async def aread(self, name: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.read, name)

    def prompt_phrases(
        self, name: str, lang: str, *, now: Optional[float] = None
    ) -> List[str]:
        """Phrases for the session-start avoid list; [] when there is nothing to say.

        Nothing is computed here. A stale file, or one mined in another
        language than this session speaks, yields nothing; the rest is cut,
        lowest rank first, until the rendered block fits
        ``HABITUAL_PHRASE_PROMPT_MAX_TOKENS``.
        """
        payload = self.read(name)
        if payload is None or not payload["entries"]:
            return []
        ts = float(now if now is not None else time.time())
        age = ts - payload["computed_at"]
        if age > HABITUAL_PHRASE_MAX_AGE_SECONDS or age < -_FUTURE_SKEW_SECONDS:
            return []
        if _short_language(payload["language"]) != _short_language(lang):
            return []
        phrases = [
            entry["phrase"] for entry in payload["entries"][:HABITUAL_PHRASE_MAX_ITEMS]
        ]
        for keep in range(len(phrases), 0, -1):
            block = render_habitual_phrases_block(phrases[:keep], lang)
            if count_tokens(block) <= HABITUAL_PHRASE_PROMPT_MAX_TOKENS:
                if keep < len(phrases):
                    logger.info(
                        "[HabitualPhrases] %s: %d phrase(s) left out (token budget %d)",
                        name, len(phrases) - keep, HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
                    )
                return phrases[:keep]
        return []

    async def aprompt_phrases(
        self, name: str, lang: str, *, now: Optional[float] = None
    ) -> List[str]:
        return await asyncio.to_thread(self.prompt_phrases, name, lang, now=now)

    # ── write ─────────────────────────────────────────────────

    def write(self, name: str, payload: Dict[str, Any]) -> bool:
        """Replace the stored list; False when fenced or the write failed.

        Raises MaintenanceModeError while a cloud apply is replacing the
        character's memory, like every other memory writer.
        """
        from utils.cloudsave_runtime import cloudsave_writable_transaction

        with cloudsave_writable_transaction(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/{HABITUAL_PHRASES_FILENAME}",
        ):
            with _file_lock(name):
                target = self._write_file_path(name)
                if target is None:
                    logger.info("[HabitualPhrases] %s: write fenced, update dropped", name)
                    return False
                try:
                    atomic_write_json(target, payload, indent=2, ensure_ascii=False)
                except Exception as exc:
                    logger.warning(
                        "[HabitualPhrases] save failed for %s: %s",
                        name, type(exc).__name__,
                    )
                    return False
        return True

    async def awrite(self, name: str, payload: Dict[str, Any]) -> bool:
        return await asyncio.to_thread(self.write, name, payload)


_store: Optional[HabitualPhraseStore] = None
_store_guard = threading.Lock()


def get_habitual_phrase_store() -> HabitualPhraseStore:
    global _store
    if _store is None:
        with _store_guard:
            if _store is None:
                _store = HabitualPhraseStore()
    return _store
