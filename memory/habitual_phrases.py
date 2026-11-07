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

- **Computed in the background, read at session start.** The memory server
  recomputes the list while idle, about once a day
  (``app/memory_server/habitual_phrase_loop.py``), and writes
  ``memory/{name}/habitual_phrases.json``. ``_build_initial_prompt`` only reads
  that file; nothing on the session-start path mines or calls a model.
- **Rules find candidates.** ``compute_entries`` is pure and deterministic.
  Rows of time_indexed.db carry one id per memory write batch rather than per
  sitting, so sessions are rebuilt from reply timestamps: a silence longer
  than ``HABITUAL_PHRASE_SESSION_GAP_SECONDS`` starts a new one, and a phrase
  qualifies only in ``HABITUAL_PHRASE_MIN_SESSIONS`` distinct sessions. Never
  a candidate: wording from her card or system prompt; a phrase holding a
  short card value, a quoted card phrase, either side's name or nickname;
  anything she herself puts in quotes (names and titles); a phrase made only
  of function words; and code or URLs, whose replies the miner drops whole.
- **A model judges them.** Rules cannot tell a verbal habit from a topic
  being discussed. ``areview_candidates`` asks once per recompute about the
  candidates without a fresh verdict; only a ``habit`` verdict suppresses.
  A candidate it could not judge is suppressed by the rules alone.
- **The user has the last word.** ``habitual_phrase_overrides.json``, written
  only from the memory browser, allows a phrase (it and any phrase holding it
  are never suppressed) or suppresses one the model kept. Both apply the
  moment they are saved, since every read decides against the current file.
- **Nothing is permanent.** Every recompute replaces the whole list, so a
  phrase she stopped using drops out on the next pass, and a file older than
  ``HABITUAL_PHRASE_MAX_AGE_SECONDS`` is ignored as well.

Stored shape, kept stable because later jobs read it as input::

    {
      "schema_version": "habitual-phrases/v1",
      "computed_at": <epoch seconds>,
      "language": <miner language tag, e.g. "zh-CN">,
      "parameters": {"min_sessions", "session_gap_seconds", "max_items",
                     "candidate_pool"},
      "source": {"reply_count", "analyzed_reply_count", "session_count",
                 "window_start_at", "window_end_at"},
      "review": {"status", "reviewed", "at"},
      "entries": [{"phrase", "normalized_phrase", "occurrence_count",
                   "message_count", "session_count", "day_count",
                   "first_seen_at", "last_seen_at", "excerpts",
                   "review": {"verdict", "reason", "at"} | null,
                   "state", "suppressed"}, ...],
      "reviews": {<normalized phrase>: {"verdict", "reason", "at"}}
    }

``entries`` are every candidate, ranked by sessions, then days, then
occurrences; ``suppressed`` marks the ones the avoid list carries (see
``annotate_entries`` for ``state``). ``reviews`` caches verdicts across
recomputes. Times are epoch seconds; ``*_seen_at`` are the stored times of the
first and last reply the phrase was mined from.

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
    HABITUAL_PHRASE_CANDIDATE_POOL,
    HABITUAL_PHRASE_MAX_AGE_SECONDS,
    HABITUAL_PHRASE_MAX_ITEMS,
    HABITUAL_PHRASE_MIN_SESSIONS,
    HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
    HABITUAL_PHRASE_REVIEW_CARD_MAX_TOKENS,
    HABITUAL_PHRASE_REVIEW_INPUT_MAX_TOKENS,
    HABITUAL_PHRASE_REVIEW_MAX_AGE_SECONDS,
    HABITUAL_PHRASE_SESSION_GAP_SECONDS,
)
from config.prompts.prompts_directives import render_habitual_phrases_block
from config.prompts.prompts_habitual import (
    HABITUAL_PHRASE_FUNCTION_CHARACTERS,
    HABITUAL_PHRASE_FUNCTION_WORDS,
    get_habitual_phrase_review_item,
    get_habitual_phrase_review_no_card,
    get_habitual_phrase_review_prompt,
    habitual_phrase_review_locale,
)
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
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Memory")


HABITUAL_PHRASES_FILENAME = "habitual_phrases.json"
HABITUAL_PHRASE_OVERRIDES_FILENAME = "habitual_phrase_overrides.json"
SCHEMA_VERSION = "habitual-phrases/v1"

# Review verdicts. Only ``habit`` suppresses; the rest name why a phrase is not
# one, which the memory browser shows.
VERDICT_HABIT = "habit"
VERDICTS = frozenset({VERDICT_HABIT, "normal", "topic", "name", "card"})

# Why an entry is or is not suppressed, highest precedence first.
STATE_ALLOWED = "allowed"        # the user allowed it (or a phrase it holds)
STATE_FORCED = "forced"          # the user suppressed it over the review
STATE_HABIT = "habit"            # the review judged it a habit
STATE_UNREVIEWED = "unreviewed"  # no verdict yet: the rules alone decide
STATE_KEPT = "kept"              # the review judged it not a habit
_SUPPRESSING_STATES = frozenset({STATE_FORCED, STATE_HABIT, STATE_UNREVIEWED})

# Context kept on each side of a phrase in a stored excerpt, and how many.
_EXCERPT_CONTEXT_CHARACTERS = 14
_MAX_EXCERPTS = 2
# Verdicts kept across recomputes; the oldest go first past this.
_REVIEW_CACHE_MAX = 200
# Phrases kept per override list; the oldest go first past this.
_OVERRIDES_MAX = 100
_MAX_OVERRIDE_PHRASE_CHARACTERS = 64
# What she puts in quotes or title marks is a name, a title or someone else's
# words, never her own habit. ASCII single quotes are left out: apostrophes.
_REPLY_QUOTE_RE = re.compile(
    r"「([^「」\n]{1,40})」|『([^『』\n]{1,40})』|“([^“”\n]{1,40})”|‘([^‘’\n]{1,40})’"
    r"|\"([^\"\n]{1,40})\"|《([^《》\n]{1,40})》"
)
# The time stamp cross_server prefixes to each stored reply.
_REPLY_STAMP_RE = re.compile(r"^\s*\[\d{8} [^\]\n]{1,16}\]\s*")
_DELIMITER_RUN_RE = re.compile(r"={3,}")

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

    def adds_a_phrase(self, normalized: str) -> bool:
        """Whether ``normalized`` holds an unclaimed run long enough to be mined.

        A phrase that runs into a claimed one is a new habit only when what it
        adds is itself phrase-sized. A claimed four-character tic grown by one
        more character is the same habit seen one unit wider, not a second one.
        """
        if " " in normalized:
            units: Sequence[Any] = normalized.split(" ")
            size = DEFAULT_WORD_NGRAM_MIN
            claimed = [tuple(units[i : i + size]) in self._word_runs for i in range(len(units) - size + 1)]
        else:
            units = normalized
            size = DEFAULT_CJK_NGRAM_MIN
            claimed = [
                units[i : i + size] in self._char_runs_of_chars
                or units[i : i + size] in self._char_runs_of_words
                for i in range(len(units) - size + 1)
            ]
        covered = [False] * len(units)
        for start, hit in enumerate(claimed):
            if hit:
                for index in range(start, start + size):
                    covered[index] = True
        longest = run = 0
        for is_covered in covered:
            run = 0 if is_covered else run + 1
            longest = max(longest, run)
        return longest >= size


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


@dataclass(frozen=True)
class _GrownPhrase:
    """A window grown into its phrase: the whole of it, and the part shown."""

    whole: str      # folded; what dedup and exclusion judge
    shown: str      # folded; at most _MAX_PHRASE_* units, around the window
    surface: str    # ``shown`` as she wrote it


def _maximal_phrase(
    normalized: str, messages: Sequence[_MessageUnits]
) -> Optional[_GrownPhrase]:
    """Grow a mined window into the whole phrase it sits in.

    The miner emits fixed-size windows, so a catchphrase longer than its
    largest window comes back as several shifted ones, and a name at either
    end can fall outside the window that wins. Growing the window for as long
    as EVERY occurrence agrees on the next unit recovers the phrase she
    actually says, so dedup and exclusion judge that rather than a slice of
    it. The grown phrase occurs exactly where the window does, so the
    window's counts still hold. None when the window cannot be located.

    Growth itself is uncapped -- only the part SHOWN is cut to a prompt line.
    Capping the growth let a name past the cap escape exclusion: the head of
    a long line was listed while the windows carrying the name, overlapping
    it, were dropped as duplicates.
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

    window_end = occurrences[0][2]
    while _all_agree(lambda s, e: s - 1):
        occurrences = [(units, start - 1, end) for units, start, end in occurrences]
    while _all_agree(lambda s, e: e):
        occurrences = [(units, start, end + 1) for units, start, end in occurrences]
    units, start, end = occurrences[0]
    # The shown part: as much context before the window as fits, then after.
    shown_start = max(start, window_end - limit)
    shown_end = min(end, shown_start + limit)
    joiner = " " if spaced else ""
    shown = units[shown_start:shown_end]
    return _GrownPhrase(
        whole=joiner.join(unit[0] for unit in units[start:end]),
        shown=joiner.join(unit[0] for unit in shown),
        surface=joiner.join(unit[1] for unit in shown),
    )


def _is_sublist(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    size = len(needle)
    return size > 0 and any(
        list(haystack[start : start + size]) == list(needle)
        for start in range(len(haystack) - size + 1)
    )


@dataclass(frozen=True)
class _Exclusions:
    """Card text, protected terms and quoted spans, pre-normalized once per computation."""

    card_compact: str
    card_words: str
    terms: tuple[tuple[tuple[str, ...], str], ...]
    # What she put in quotes in the analysed replies, in both match forms.
    quoted_compact: tuple[str, ...] = ()
    quoted_words: tuple[str, ...] = ()

    def holds_term(self, normalized: str, language: str) -> bool:
        """Whether the phrase contains one of ``terms``: whole words, or a substring in zh/ja/ko."""
        spaced = language in _SPACED_LANGUAGES
        words = normalized.split(" ")
        compact = normalized.replace(" ", "")
        for term_words, term_compact in self.terms:
            if _is_sublist(term_words, words):
                return True
            if not spaced and term_compact and term_compact in compact:
                return True
        return False

    def excludes(self, normalized: str, language: str) -> bool:
        spaced = language in _SPACED_LANGUAGES
        compact = normalized.replace(" ", "")
        # Written into the card or system prompt: part of who she is.
        if spaced:
            if f" {normalized} " in self.card_words:
                return True
        elif compact and compact in self.card_compact:
            return True
        # Contains a name, a nickname or a short card term.
        if self.holds_term(normalized, language):
            return True
        # Something she quoted: a viewer's name, a title, someone's words.
        if spaced:
            if any(f" {normalized} " in quoted for quoted in self.quoted_words):
                return True
        elif compact and any(compact in quoted for quoted in self.quoted_compact):
            return True
        return _is_generic(normalized, language)


def _function_vocabulary(language: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(words, characters)`` that carry grammar rather than meaning in ``language``."""
    key = habitual_phrase_review_locale(language)
    keys = ("zh", "zh-TW") if key in ("zh", "zh-TW") else (key,)
    words: frozenset[str] = frozenset().union(
        *(HABITUAL_PHRASE_FUNCTION_WORDS.get(k, frozenset()) for k in keys)
    )
    characters: frozenset[str] = frozenset().union(
        *(HABITUAL_PHRASE_FUNCTION_CHARACTERS.get(k, frozenset()) for k in keys)
    )
    return words, characters


def _is_generic(normalized: str, language: str) -> bool:
    """Whether a phrase is made only of function words: everyday grammar, not a habit.

    Measured on local history, most false hits were of this kind -- a question
    tag, a pronoun and a copula, a "here I am" -- that any speaker produces in
    every conversation; asking her to avoid them would make her speech
    stilted. The price is the odd real habit spelled entirely in function
    words, which is the accepted direction.
    """
    words, characters = _function_vocabulary(language)
    if " " in normalized:
        return all(word in words for word in normalized.split(" "))
    text = normalized
    longest = max((len(word) for word in words), default=1)
    index = 0
    while index < len(text):
        for size in range(min(longest, len(text) - index), 1, -1):
            if text[index : index + size] in words:
                index += size
                break
        else:
            if text[index] not in characters and text[index] not in words:
                return False
            index += 1
    return True


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


def _quoted_spans(texts: Iterable[str]) -> list[str]:
    """Everything put in quotes or title marks across ``texts``."""
    spans: list[str] = []
    for text in texts:
        for match in _REPLY_QUOTE_RE.finditer(text or ""):
            spans.append(next(group for group in match.groups() if group is not None))
    return spans


def _build_exclusions(
    card_text: str,
    protected_terms: Iterable[str],
    quoted_texts: Iterable[str] = (),
) -> _Exclusions:
    quoted = list(dict.fromkeys(quoted_texts))
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
        quoted_compact=tuple(_compact(span) for span in quoted),
        quoted_words=tuple(" " + " ".join(_words(span)) + " " for span in quoted),
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


def _excerpts(texts: Sequence[str], surface: str) -> list[str]:
    """Up to ``_MAX_EXCERPTS`` distinct snippets of ``texts`` around ``surface``.

    What the review judges and the memory browser shows: the phrase is rarely
    decidable on its own, but a few characters either side usually say
    whether it is a way of speaking or the thing being spoken about.
    """
    if " " in surface:
        pattern = re.compile(r"\W+".join(map(re.escape, surface.split(" "))), re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(surface), re.IGNORECASE)
    found: list[str] = []
    for text in texts:
        body = _REPLY_STAMP_RE.sub("", unicodedata.normalize("NFKC", text or ""), count=1)
        match = pattern.search(body)
        if match is None:
            continue
        start = max(0, match.start() - _EXCERPT_CONTEXT_CHARACTERS)
        end = min(len(body), match.end() + _EXCERPT_CONTEXT_CHARACTERS)
        snippet = " ".join(body[start:end].split())
        if snippet and snippet not in found:
            found.append(snippet)
        if len(found) >= _MAX_EXCERPTS:
            break
    return found


def compute_entries(
    replies: Sequence[TimedReply],
    *,
    language: str,
    card_text: str = "",
    protected_terms: Iterable[str] = (),
    min_sessions: int = HABITUAL_PHRASE_MIN_SESSIONS,
    session_gap_seconds: float = HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    max_items: int = HABITUAL_PHRASE_CANDIDATE_POOL,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Mine the candidates: phrases she reuses across sessions. Pure: no I/O, no model.

    ``replies`` are chronological; ``language`` is a miner language tag.
    Returns ``(entries, source)``: up to ``max_items`` candidates, ranked,
    each with its excerpts but not yet a verdict or a state.
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
    exclusions = _build_exclusions(
        card_text,
        protected_terms,
        _quoted_spans(reply.text[:_MAX_REPLY_CHARACTERS] for reply in timed),
    )
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
        # A window inside a phrase already claimed (listed or excluded) is a
        # fragment of it. Skipping it before growing spares the growth scan
        # for every one of the many fragments one long line produces.
        if claims.overlaps(entry["normalized_phrase"]):
            continue
        spaced = " " in entry["normalized_phrase"]
        grown = _maximal_phrase(
            entry["normalized_phrase"], [_split(line, spaced) for line in lines],
        )
        whole = entry["normalized_phrase"]
        if grown is not None:
            whole = grown.whole
            entry["normalized_phrase"], entry["phrase"] = grown.shown, grown.surface
        # A window that grows into a claimed phrase is a duplicate unless what
        # it adds is phrase-sized: "怎么又是这" next to "怎么又是" is one habit,
        # while a second habit fused onto the first with no punctuation between
        # them adds a whole phrase and is kept.
        duplicate = (
            grown is not None
            and claims.overlaps(whole)
            and not claims.adds_a_phrase(whole)
        )
        # The WHOLE phrase is claimed either way, so every other window of the
        # same line stops at the cheap check above.
        claims.add(whole)
        # The whole phrase decides: a name anywhere in it excludes the line,
        # not only one inside the part that would be shown. The shown part is
        # checked too, since it alone may be what her card spells out.
        if (
            duplicate
            or exclusions.excludes(whole, language)
            or exclusions.excludes(entry["normalized_phrase"], language)
        ):
            continue
        entry["excerpts"] = _excerpts(
            [timed[line - 1].text[:_MAX_REPLY_CHARACTERS] for line in lines],
            entry["phrase"],
        )
        entries.append(entry)
    return entries, source


@dataclass(frozen=True)
class Overrides:
    """The user's decisions from the memory browser, oldest first.

    ``allowed`` phrases, and any phrase holding one, are never suppressed.
    ``suppressed`` phrases are suppressed whatever the review said; only a
    candidate the rules still find can be, so one she stopped using lapses
    with the rest of the list.
    """

    allowed: tuple[dict[str, Any], ...] = ()
    suppressed: tuple[dict[str, Any], ...] = ()

    def allowed_phrases(self) -> list[str]:
        return [item["phrase"] for item in self.allowed]

    def suppressed_keys(self) -> set[str]:
        return {item["normalized_phrase"] for item in self.suppressed}

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "allowed": [dict(item) for item in self.allowed],
            "suppressed": [dict(item) for item in self.suppressed],
        }


OVERRIDE_ACTIONS = frozenset({"allow", "suppress", "clear"})


def normalize_override_phrase(raw: Any) -> Optional[tuple[str, str]]:
    """``(phrase, normalized_phrase)`` for a phrase the user acted on, or None."""
    if not isinstance(raw, str):
        return None
    phrase = " ".join(unicodedata.normalize("NFKC", raw).split())
    if not phrase or len(phrase) > _MAX_OVERRIDE_PHRASE_CHARACTERS:
        return None
    if not any(ch.isalnum() for ch in phrase):
        return None
    return phrase, phrase.casefold()


def _normalize_override_items(raw: Any) -> tuple[dict[str, Any], ...]:
    items: dict[str, dict[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_override_phrase(item.get("phrase"))
        if normalized is None:
            continue
        phrase, key = normalized
        items.pop(key, None)
        items[key] = {"phrase": phrase, "normalized_phrase": key, "at": _as_time(item.get("at"))}
    return tuple(list(items.values())[-_OVERRIDES_MAX:])


def apply_override(overrides: Overrides, phrase: str, action: str, *, now: float) -> Overrides:
    """``overrides`` after the user allowed, suppressed or cleared ``phrase``.

    The two lists are exclusive: allowing a phrase lifts a suppression of it
    and the other way round, and clearing removes it from both.
    """
    if action not in OVERRIDE_ACTIONS:
        raise ValueError("unknown override action")
    normalized = normalize_override_phrase(phrase)
    if normalized is None:
        raise ValueError("invalid phrase")
    text, key = normalized
    allowed = [item for item in overrides.allowed if item["normalized_phrase"] != key]
    suppressed = [item for item in overrides.suppressed if item["normalized_phrase"] != key]
    item = {"phrase": text, "normalized_phrase": key, "at": float(now)}
    if action == "allow":
        allowed.append(item)
    elif action == "suppress":
        suppressed.append(item)
    return Overrides(tuple(allowed[-_OVERRIDES_MAX:]), tuple(suppressed[-_OVERRIDES_MAX:]))


def annotate_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    language: str,
    overrides: Overrides = Overrides(),
    max_items: int = HABITUAL_PHRASE_MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Each entry with ``state`` and ``suppressed``, decided against ``overrides``.

    States, highest precedence first: ``allowed`` (the user allowed it or a
    phrase it holds), ``forced`` (the user suppressed it), ``habit`` (the
    review judged it one), ``unreviewed`` (no verdict, so the rules alone
    decide) and ``kept`` (the review judged it not a habit). The first three
    suppress, at most ``max_items`` of them: forced ones first, then in rank
    order. One call decides for every reader, so the session start, the
    memory browser and the stored file never disagree.
    """
    allowed = _build_exclusions("", overrides.allowed_phrases()) if overrides.allowed else None
    forced = overrides.suppressed_keys()
    annotated: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        normalized = str(item.get("normalized_phrase") or "")
        review = item.get("review")
        if allowed is not None and allowed.holds_term(normalized, language):
            state = STATE_ALLOWED
        elif normalized in forced:
            state = STATE_FORCED
        elif not isinstance(review, Mapping):
            state = STATE_UNREVIEWED
        elif review.get("verdict") == VERDICT_HABIT:
            state = STATE_HABIT
        else:
            state = STATE_KEPT
        item["state"] = state
        item["suppressed"] = False
        annotated.append(item)
    suppressing = sorted(
        (index for index, item in enumerate(annotated) if item["state"] in _SUPPRESSING_STATES),
        key=lambda index: (annotated[index]["state"] != STATE_FORCED, index),
    )
    for index in suppressing[:max_items]:
        annotated[index]["suppressed"] = True
    return annotated


def _suppressed_phrases(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    """The suppressed phrases in prompt order: the user's own first, then by rank.

    The token budget cuts from the end, so a phrase the user suppressed is the
    last to go, the same precedence ``annotate_entries`` gives it under the cap.
    """
    suppressed = [entry for entry in entries if entry["suppressed"]]
    ordered = sorted(
        range(len(suppressed)),
        key=lambda index: (suppressed[index]["state"] != STATE_FORCED, index),
    )
    return [suppressed[index]["phrase"] for index in ordered]


def _normalize_review(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("verdict") not in VERDICTS:
        return None
    reason = raw.get("reason")
    return {
        "verdict": raw["verdict"],
        "reason": reason.strip()[:200] if isinstance(reason, str) else "",
        "at": _as_time(raw.get("at")),
    }


def fresh_reviews(
    payload: Optional[Mapping[str, Any]], *, now: float
) -> dict[str, dict[str, Any]]:
    """Cached verdicts young enough to reuse, keyed by normalized phrase."""
    if not payload:
        return {}
    fresh: dict[str, dict[str, Any]] = {}
    for key, review in (payload.get("reviews") or {}).items():
        at = review.get("at")
        if at is not None and 0 <= now - at <= HABITUAL_PHRASE_REVIEW_MAX_AGE_SECONDS:
            fresh[key] = dict(review)
    return fresh


def _bounded_reviews(reviews: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    newest = sorted(reviews.items(), key=lambda item: item[1].get("at") or 0.0, reverse=True)
    return {key: dict(review) for key, review in newest[:_REVIEW_CACHE_MAX]}


def build_payload(
    entries: list[dict[str, Any]],
    source: dict[str, Any],
    *,
    language: str,
    now: float,
    reviews: Optional[Mapping[str, Mapping[str, Any]]] = None,
    review_status: str = "skipped",
    reviewed: int = 0,
    overrides: Overrides = Overrides(),
    min_sessions: int = HABITUAL_PHRASE_MIN_SESSIONS,
    session_gap_seconds: float = HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    max_items: int = HABITUAL_PHRASE_MAX_ITEMS,
    candidate_pool: int = HABITUAL_PHRASE_CANDIDATE_POOL,
) -> dict[str, Any]:
    """The file content for one computation: candidates with verdicts and states.

    ``review_status`` says what happened to the review this time: ``ok``,
    ``failed``, ``disabled`` (its switches are off), ``not_needed`` (every
    candidate already had a fresh verdict) or ``skipped``.
    """
    cache = dict(reviews or {})
    with_reviews = [
        {**entry, "review": cache.get(entry["normalized_phrase"])} for entry in entries
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "computed_at": float(now),
        "language": language,
        "parameters": {
            "min_sessions": min_sessions,
            "session_gap_seconds": session_gap_seconds,
            "max_items": max_items,
            "candidate_pool": candidate_pool,
        },
        "source": dict(source),
        "review": {
            "status": review_status,
            "reviewed": int(reviewed),
            "at": float(now) if reviewed else None,
        },
        "entries": annotate_entries(
            with_reviews, language=language, overrides=overrides, max_items=max_items,
        ),
        "reviews": _bounded_reviews(cache),
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
    excerpts = raw.get("excerpts")
    return {
        "phrase": phrase.strip(),
        "normalized_phrase": normalized,
        "occurrence_count": _as_count(raw.get("occurrence_count")),
        "message_count": _as_count(raw.get("message_count")),
        "session_count": _as_count(raw.get("session_count")),
        "day_count": _as_count(raw.get("day_count")),
        "first_seen_at": _as_time(raw.get("first_seen_at")),
        "last_seen_at": _as_time(raw.get("last_seen_at")),
        "excerpts": [
            text[:200] for text in (excerpts if isinstance(excerpts, list) else [])
            if isinstance(text, str) and text.strip()
        ][:_MAX_EXCERPTS],
        "review": _normalize_review(raw.get("review")),
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
    review = raw.get("review", {})
    reviews = raw.get("reviews", {})
    if (
        computed_at is None
        or not isinstance(language, str)
        or not language
        or not isinstance(raw_entries, list)
        or not isinstance(parameters, dict)
        or not isinstance(source, dict)
        or not isinstance(review, dict)
        or not isinstance(reviews, dict)
    ):
        return None
    entries = [
        entry
        for entry in (_normalize_entry(item) for item in raw_entries)
        if entry is not None
    ]
    cache = {
        key: normalized
        for key, normalized in ((k, _normalize_review(v)) for k, v in reviews.items())
        if isinstance(key, str) and normalized is not None
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "computed_at": computed_at,
        "language": language,
        "parameters": dict(parameters),
        "source": dict(source),
        "review": dict(review),
        "entries": entries,
        "reviews": cache,
    }


# ── the review ────────────────────────────────────────────────


def _defang(text: str) -> str:
    """Quoted text may not draw its own block delimiters."""
    return _DELIMITER_RUN_RE.sub("==", text or "")


def build_review_prompt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    ai_name: str,
    card_text: str,
    language: str,
) -> tuple[str, dict[int, str]]:
    """The review prompt, and the normalized phrase behind each id it shows.

    Whole candidates are cut once the block would pass
    ``HABITUAL_PHRASE_REVIEW_INPUT_MAX_TOKENS``; those stay unjudged until a
    later recompute, and a verdict can only land on an id that was shown.
    """
    item = get_habitual_phrase_review_item(language)
    blocks: list[str] = []
    for number, candidate in enumerate(candidates, start=1):
        lines = [
            item.format(
                id=number,
                phrase=_defang(str(candidate["phrase"])),
                sessions=_as_count(candidate.get("session_count")),
                days=_as_count(candidate.get("day_count")),
            )
        ]
        lines += [f"    …{_defang(text)}…" for text in candidate.get("excerpts") or []]
        blocks.append("\n".join(lines))
    shown_blocks, _dropped = take_lines_within_token_budget(
        blocks, HABITUAL_PHRASE_REVIEW_INPUT_MAX_TOKENS, separator="\n"
    )
    shown = {
        number: str(candidates[number - 1]["normalized_phrase"])
        for number in range(1, len(shown_blocks) + 1)
    }
    card = truncate_to_tokens(_defang((card_text or "").strip()), HABITUAL_PHRASE_REVIEW_CARD_MAX_TOKENS)
    values = {
        "AI_NAME": ai_name,
        "COUNT": str(len(shown_blocks)),
        "CARD": card or get_habitual_phrase_review_no_card(language),
        "CANDIDATES": "\n".join(shown_blocks),
    }
    # One pass, so a placeholder spelled inside the card or a reply is not
    # expanded in turn.
    prompt = re.sub(
        r"\{(AI_NAME|COUNT|CARD|CANDIDATES)\}",
        lambda match: values[match.group(1)],
        get_habitual_phrase_review_prompt(language),
    )
    return prompt, shown


def parse_review_verdicts(
    raw: str, shown: Mapping[int, str], *, now: float
) -> dict[str, dict[str, Any]]:
    """Model output → verdicts by normalized phrase; anything malformed is dropped."""
    from utils.file_utils import robust_json_loads

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = robust_json_loads(text) if text else None
    except Exception:  # noqa: BLE001 — malformed model output judges nothing
        return {}
    if isinstance(parsed, dict) and isinstance(parsed.get("verdicts"), list):
        parsed = parsed["verdicts"]
    verdicts: dict[str, dict[str, Any]] = {}
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        number = item.get("id")
        if isinstance(number, str) and number.strip().isdigit():
            number = int(number.strip())
        if not isinstance(number, int) or isinstance(number, bool) or number not in shown:
            continue
        review = _normalize_review({**item, "at": now})
        if review is not None:
            verdicts.setdefault(shown[number], review)
    return verdicts


async def areview_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    ai_name: str,
    card_text: str,
    language: str,
    config_manager,
    now: float,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Ask the model about ``candidates``: ``(verdicts by normalized phrase, ids shown)``.

    One summary-tier call; the prompt is input-budgeted by
    ``build_review_prompt``. Raises on a failed call, so the caller can tell a
    model that judged nothing from one that could not be reached.
    """
    from config import LLM_OUTPUT_GUARD_MAX_TOKENS, MEMORY_LLM_HARD_TIMEOUT_SECONDS
    from utils.llm_client import create_chat_llm_async
    from utils.token_tracker import set_call_type

    if not candidates:
        return {}, 0
    prompt, shown = build_review_prompt(
        candidates, ai_name=ai_name, card_text=card_text, language=language,
    )
    if not shown:
        return {}, 0
    set_call_type("memory_habitual_phrase_review")
    api_config = await config_manager.aget_model_api_config('summary')
    llm = await create_chat_llm_async(
        api_config['model'],
        api_config['base_url'], api_config['api_key'],
        timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
        max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; the answer is one short verdict per candidate
        provider_type=api_config.get('provider_type'),
    )
    try:
        response = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # build_review_prompt cuts candidates to HABITUAL_PHRASE_REVIEW_INPUT_MAX_TOKENS and the card to HABITUAL_PHRASE_REVIEW_CARD_MAX_TOKENS.
    finally:
        await llm.aclose()
    return parse_review_verdicts(getattr(response, 'content', '') or '', shown, now=now), len(shown)


# ── storage ───────────────────────────────────────────────────


_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(key: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


def _short_language(raw: str) -> str:
    return normalize_language_code(raw, format="short") or ""


class HabitualPhraseStore:
    """Reads and writes ``memory/{name}/habitual_phrases.json`` and its overrides file.

    Usage:
        store = get_habitual_phrase_store()
        store.write(name, build_payload(*compute_entries(...), language=..., now=...))
        phrases = await store.aprompt_phrases(name, lang)
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()

    # ── path ──────────────────────────────────────────────────

    def _file_path(self, name: str, filename: str = HABITUAL_PHRASES_FILENAME) -> str:
        """Path for READING; never creates the character directory."""
        return os.path.join(str(self._config_manager.memory_dir), name, filename)

    def _write_file_path(
        self, name: str, filename: str = HABITUAL_PHRASES_FILENAME
    ) -> Optional[str]:
        """Save target, or None while a rename / delete fences this name."""
        from memory import _is_within_memory_root, ensure_character_dir
        from utils.character_memory import is_character_write_fenced

        if is_character_write_fenced(name):
            return None
        memory_dir = str(self._config_manager.memory_dir)
        if not _is_within_memory_root(memory_dir, name, os.path.join(memory_dir, name)):
            return None
        return os.path.join(ensure_character_dir(memory_dir, name), filename)

    def _read_json(self, name: str, filename: str) -> Any:
        path = self._file_path(name, filename)
        with _file_lock(f"{name}/{filename}"):
            if not os.path.exists(path):
                return None
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:  # A damaged file reads as absent.
                logger.warning(
                    "[HabitualPhrases] load failed for %s/%s, treating as absent: %s",
                    name, filename, type(exc).__name__,
                )
                return None

    def _write_json(self, name: str, filename: str, payload: Mapping[str, Any]) -> bool:
        from utils.cloudsave_runtime import cloudsave_writable_transaction

        with cloudsave_writable_transaction(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/{filename}",
        ):
            with _file_lock(f"{name}/{filename}"):
                target = self._write_file_path(name, filename)
                if target is None:
                    logger.info("[HabitualPhrases] %s: write fenced, update dropped", name)
                    return False
                try:
                    atomic_write_json(target, dict(payload), indent=2, ensure_ascii=False)
                except Exception as exc:
                    logger.warning(
                        "[HabitualPhrases] save failed for %s/%s: %s",
                        name, filename, type(exc).__name__,
                    )
                    return False
        return True

    # ── the list ──────────────────────────────────────────────

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        """The stored payload, normalized; None when absent or unreadable."""
        if not name:
            return None
        return _normalize_payload(self._read_json(name, HABITUAL_PHRASES_FILENAME))

    async def aread(self, name: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.read, name)

    def write(self, name: str, payload: Dict[str, Any]) -> bool:
        """Replace the stored list; False when fenced or the write failed.

        Raises MaintenanceModeError while a cloud apply is replacing the
        character's memory, like every other memory writer.
        """
        return self._write_json(name, HABITUAL_PHRASES_FILENAME, payload)

    async def awrite(self, name: str, payload: Dict[str, Any]) -> bool:
        return await asyncio.to_thread(self.write, name, payload)

    # ── the user's overrides ──────────────────────────────────

    def read_overrides(self, name: str) -> Overrides:
        """The user's allow / suppress decisions; empty when none were made."""
        if not name:
            return Overrides()
        raw = self._read_json(name, HABITUAL_PHRASE_OVERRIDES_FILENAME)
        if not isinstance(raw, dict):
            return Overrides()
        return Overrides(
            _normalize_override_items(raw.get("allowed")),
            _normalize_override_items(raw.get("suppressed")),
        )

    async def aread_overrides(self, name: str) -> Overrides:
        return await asyncio.to_thread(self.read_overrides, name)

    def update_override(
        self, name: str, phrase: str, action: str, *, now: Optional[float] = None
    ) -> Overrides:
        """Record the user's decision on ``phrase``; raises ValueError for bad input.

        The memory browser is this file's only writer, so a read-modify-write
        under the file lock cannot interleave with the background recompute,
        which only reads it.
        """
        ts = float(now if now is not None else time.time())
        updated = apply_override(self.read_overrides(name), phrase, action, now=ts)
        if not self._write_json(name, HABITUAL_PHRASE_OVERRIDES_FILENAME, updated.as_dict()):
            raise OSError("overrides not saved")
        return updated

    async def aupdate_override(
        self, name: str, phrase: str, action: str, *, now: Optional[float] = None
    ) -> Overrides:
        return await asyncio.to_thread(self.update_override, name, phrase, action, now=now)

    # ── what the session start sees ───────────────────────────

    def _usable(self, payload: Optional[Mapping[str, Any]], lang: str, ts: float) -> bool:
        if payload is None:
            return False
        age = ts - payload["computed_at"]
        if age > HABITUAL_PHRASE_MAX_AGE_SECONDS or age < -_FUTURE_SKEW_SECONDS:
            return False
        return _short_language(payload["language"]) == _short_language(lang)

    def _phrases_within_budget(self, name: str, phrases: List[str], lang: str) -> List[str]:
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

    def prompt_phrases(
        self, name: str, lang: str, *, now: Optional[float] = None
    ) -> List[str]:
        """Phrases for the session-start avoid list; [] when there is nothing to say.

        Nothing is computed here. A stale file, or one mined in another
        language than this session speaks, yields nothing. The stored entries
        are decided again against the CURRENT overrides, so an allow or a
        suppression from the memory browser counts from the next session on;
        the rest is cut, lowest rank first, until the rendered block fits
        ``HABITUAL_PHRASE_PROMPT_MAX_TOKENS``.
        """
        payload = self.read(name)
        ts = float(now if now is not None else time.time())
        if not self._usable(payload, lang, ts) or not payload["entries"]:
            return []
        entries = annotate_entries(
            payload["entries"],
            language=payload["language"],
            overrides=self.read_overrides(name),
        )
        return self._phrases_within_budget(
            name, _suppressed_phrases(entries), lang,
        )

    async def aprompt_phrases(
        self, name: str, lang: str, *, now: Optional[float] = None
    ) -> List[str]:
        return await asyncio.to_thread(self.prompt_phrases, name, lang, now=now)

    # ── what the memory browser shows ─────────────────────────

    def describe(self, name: str, lang: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Everything the memory browser shows for one character.

        The stored candidates decided against the current overrides, the
        overrides themselves, and the phrases the next session start will
        actually carry -- which can be fewer than the suppressed ones, past
        the token budget, or none, when the list is stale or in another
        language than ``lang``.
        """
        ts = float(now if now is not None else time.time())
        payload = self.read(name)
        overrides = self.read_overrides(name)
        entries = (
            annotate_entries(payload["entries"], language=payload["language"], overrides=overrides)
            if payload is not None
            else []
        )
        usable = self._usable(payload, lang, ts)
        injected = (
            self._phrases_within_budget(
                name, _suppressed_phrases(entries), lang,
            )
            if usable
            else []
        )
        stale = payload is not None and ts - payload["computed_at"] > HABITUAL_PHRASE_MAX_AGE_SECONDS
        return {
            "computed_at": payload["computed_at"] if payload else None,
            "language": payload["language"] if payload else None,
            "language_matches": bool(payload) and _short_language(payload["language"]) == _short_language(lang),
            "stale": stale,
            "parameters": payload["parameters"] if payload else {},
            "source": payload["source"] if payload else {},
            "review": payload["review"] if payload else {},
            "entries": entries,
            "injected": injected,
            "overrides": overrides.as_dict(),
        }

    async def adescribe(self, name: str, lang: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self.describe, name, lang, now=now)


_store: Optional[HabitualPhraseStore] = None
_store_guard = threading.Lock()


def get_habitual_phrase_store() -> HabitualPhraseStore:
    global _store
    if _store is None:
        with _store_guard:
            if _store is None:
                _store = HabitualPhraseStore()
    return _store
