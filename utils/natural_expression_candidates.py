#!/usr/bin/env python3
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

"""Deterministic natural-expression candidate analysis shared by local tools.

The analysis is pure and review-only. It never discovers conversation files,
calls a model or network service, edits the runtime rule table, or activates a
candidate. Its output is intentionally incompatible with
``config.prompts.prompts_slop.SLOP_RULES``.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence, TypeVar

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "natural-expression-candidates/v1"
ARTIFACT_TYPE = "maintainer_review_candidates"
USER_REVIEW_ARTIFACT_TYPE = "user_review_candidates"
DEFAULT_THRESHOLD = 3
DEFAULT_MESSAGE_COUNT_THRESHOLD = 3
DEFAULT_WORD_NGRAM_MIN = 2
DEFAULT_WORD_NGRAM_MAX = 5
DEFAULT_CJK_NGRAM_MIN = 4
DEFAULT_CJK_NGRAM_MAX = 8
DEFAULT_MIN_LENGTH = 4
USER_REVIEW_MAX_INPUT_CHARACTERS = 128 * 1024
USER_REVIEW_MAX_OCCURRENCES = 100_000
USER_REVIEW_MAX_CANDIDATES = 200
# Below this, halving a single reply has stopped being narrowing and the
# budget itself is the problem. Mining generates a fixed number of n-grams
# per character -- about five for CJK -- so 512 characters is roughly 2,500
# occurrences, far inside any sane limit. Reaching the floor means the limit
# was configured too small to analyse anything, which is worth surfacing.
_USER_REVIEW_MIN_TRUNCATED_CHARACTERS = 512
# How far above the AVERAGE OF THE REST a message has to sit before it is
# worth cutting its body rather than evicting messages. "Longer than all the
# others combined" was too strict: it catches exactly one outlier, so two
# comparably heavy replies evaded it and the eviction that followed threw
# away the ordinary replies carrying the repeated phrase -- the very defect
# the single-outlier clip was added to stop. At a ratio of 1 a uniformly
# large window would qualify and every body would be cut, which is the
# behaviour ``test_a_uniformly_large_window_narrows_without_cutting_a_body``
# exists to forbid; 2 is the smallest integer that separates the two.
_USER_REVIEW_OUTLIER_RATIO = 2

_LANGUAGE_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "es": "es",
    "es-es": "es",
    "es-mx": "es",
    "pt": "pt",
    "pt-br": "pt",
    "pt-pt": "pt",
    "ru": "ru",
    "ru-ru": "ru",
    "ja": "ja",
    "ja-jp": "ja",
    "ko": "ko",
    "ko-kr": "ko",
    "zh": "zh",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
}
_WHITESPACE_LANGUAGES = frozenset({"en", "es", "pt", "ru"})
_TEXT_BOUNDARY_RE = re.compile(r"[\r\n.!?。！？；;:：,，、]+")
# A URL ends at CJK punctuation. The old tail excluded only whitespace and
# brackets, and CJK prose has neither -- so a reply reading
# "请看https://a.com。我们一起去吃饭吧！" protected the sentence terminator AND
# every following sentence on the line, deleting the catchphrase after the URL.
# ``re`` reads the ``\uXXXX`` escapes itself, so these stay raw and legible:
# general CJK punctuation, then the fullwidth ASCII punctuation blocks.
_URL_STOP = (
    r"\u2018\u2019\u201c\u201d\u2026"
    r"\u3000-\u303f\uff01-\uff0f\uff1a-\uff20"
    r"\uff3b-\uff40\uff5b-\uff65"
)
_URL_ATOM = r"[^\s<>()" + _URL_STOP + r"]"
# An address is not a URL tail. ``_URL_ATOM`` admits "@" and ".", so the two
# internationalized address branches below -- written with it -- let the
# local part, the domain and every label compete for the same characters:
# "a@a@a@a..." went quadratic, 0.73s at 16 KB against an accepted reply size
# of 128 KiB, and on the live turn path since the effects sidecar masks
# every draft. Without those branches the same input is linear, so this is
# their cost and theirs to pay.
#
# The local part keeps "." -- it is the identifying half and matching only
# part of it is worse than not matching at all -- but excludes "@", and is
# ATOMIC: it stops at the first "@", and a character handed back is by
# construction not one, so backtracking into it can never help. Each domain
# LABEL excludes both, which is what makes the dotted tail unambiguous.
_EMAIL_LOCAL = r"(?>[^\s<>()@" + _URL_STOP + r"]+)"
_EMAIL_LABEL = r"(?>[^\s<>()@." + _URL_STOP + r"]+)"
_EMAIL_ADDRESS = _EMAIL_LOCAL + r"@" + _EMAIL_LABEL + r"(?:\." + _EMAIL_LABEL + r")+"
# Parentheses are NOT in the pattern. A path nests them to any depth
# (``/f(g(x))``), and one level encoded here stopped at the inner ``(``,
# leaving the rest of the path minable -- and minable means persisted to the
# effects sidecar for 120 days, by a module whose whole promise is that it
# never persists a URL. ``_url_spans`` extends each match instead.
_URL_TAIL = _URL_ATOM
_URL_RE = re.compile(
    r"(?i:https?://|www\.)" + _URL_TAIL + "+|"
    # A "mailto:" needs no alternative of its own. The bare-address rule
    # below takes the scheme into its local part and covers every case this
    # one did -- measured over 4913 address-shaped drafts, the only six
    # where the two differ are six where the mailto form protected LESS.
    # A rule that can never be the reason something is protected is a rule
    # no test can hold, which is how its guard came to pass with it deleted.
    # ANY scheme, as a rule rather than a list. A fixed allowlist guarantees
    # another round of "you missed one", and the ones it missed carried real
    # payloads: an otpauth:// TOTP secret, a postgres:// password, an
    # ssh/magnet/sms target and a Windows path all reached the 120-day
    # sidecar verbatim.
    #
    # The two lookaheads are what keep this off speech: after the colon
    # there must be an OPAQUE PART -- at least two atoms, at least one of
    # them alphanumeric -- so "together:D", "3:4" and a CJK sentence after
    # "note:" match nothing. Without them the rule runs to end of text on
    # CJK, which is the one over-protection shape this module refuses.
    #
    # Given up deliberately, since none carries a payload: the degenerate
    # "data:,", "tel:5" and "mailto:a" the old list covered. A real
    # "tel:+1-555-1234", "data:image/png;base64,..." and a real mailto
    # address stay protected.
    # A LEFT BOUNDARY, and it is the difference between linear and quadratic.
    # Without it the engine restarts the scheme scan at every letter of a long
    # colon-free run, scanning the whole remaining suffix each time: measured
    # 4.0x per doubling, 38.8s for finditer alone at the module's own 128 KiB
    # limit, where ordinary prose of the same length takes 0.48s end to end.
    # That is past the router's 30s timeout, and cancelling the request does
    # not stop work already handed to a thread. A scheme cannot begin midway
    # through a run of scheme characters anyway, so refusing to start there
    # costs no match: "ahttps://x" still matches, from the "a".
    r"(?<![A-Za-z0-9+.\-])(?i:[a-z][a-z0-9+.\-]*):"
    r"(?=" + _URL_TAIL + "{2,})(?=" + _URL_TAIL + "*[0-9A-Za-z])" + _URL_TAIL + "+|"
    # A bare address too, which is how one actually appears in a reply. The
    # local part is the identifying half, so matching only from the domain
    # was worse than not matching at all.
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+|"
    # The same shape with an UNBOUNDED alphabet, because the rule above is
    # ASCII-only and an internationalized address matched nothing at all --
    # so "用户秘密@例子.公司" had its local part, the identifying half,
    # mined and persisted. The mailto form was fixed first; a bare address
    # is how one actually appears in a reply, and it needed the same
    # treatment.
    #
    # What makes the open alphabet safe is the local@domain.tld SHAPE, and
    # the atom class doing the work here: it stops at whitespace and at CJK
    # punctuation, so a match cannot run past the sentence it sits in. In
    # unspaced CJK it does take the surrounding run with it, which is
    # over-protection of one sentence -- the accepted direction, against a
    # payload persisted for 120 days.
    r"(?<!" + _URL_ATOM + r")" + _EMAIL_ADDRESS + r"|"
    # An ASCII-only lookbehind. ``\w`` counts CJK as a word character, so a bare
    # host written straight after a hanzi never matched at all and its path
    # token was persisted verbatim -- and zh/zh-TW/ja/ko, half the languages
    # this module supports, are written without spaces.
    r"(?<![A-Za-z0-9_-])(?:(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    # A TLD may be all-lower or all-UPPER but never Capitalised. DNS is
    # case-insensitive, so every spelling of a TLD is a real host and has to
    # be protected. The cost is that a missing space after a period reads as
    # one too -- "cute.Nice", "hola.Mi", "fine.Thanks", ordinary en/es/pt
    # model output -- which merely stops those sentences being mined. That is
    # the accepted direction for this module: over-protection loses a
    # catchphrase, under-protection persists a URL for 120 days. An earlier
    # revision tried to separate the two by case SHAPE (all-lower or
    # all-UPPER is a TLD, Capitalised is a resumed sentence); it left
    # "Example.CoM/secret" unprotected, which is the wrong way to be wrong.
    # Punycode FIRST: the generic branch matches the bare "xn" of "xn--p1ai"
    # and the pattern then ends there, because everything after the TLD is
    # optional -- so the rest of the label and the whole path stayed minable.
    r"(?i:xn--[a-z0-9-]{2,59}|[a-z]{2,63})|(?:\d{1,3}\.){3}\d{1,3})"
    # A query or fragment may follow the host with no path at all
    # ("example.com?token=..."), and stopping at the host left the query
    # minable.
    r"(?::\d{1,5})?(?:[/?#]" + _URL_TAIL + "*)?|"
    r"(?<![A-Za-z0-9_-])(?i:localhost)"
    r"(?:(?::\d{1,5})(?:[/?#]" + _URL_TAIL + "*)?|[/?#]" + _URL_TAIL + "*)"
)

# The BARE runs cross line breaks, because an HTML start tag may split
# its attributes across lines and every parser reads it as one tag.
# Rejecting the newline left "<code\n class=..>" unrecognised, so the
# opener and the whole code body went unprotected while only the
# closing tag was masked.
#
# The QUOTED runs stay line-bounded, and that is not an oversight. A
# quoted value that could cross a newline can pair the closing quote of
# one tag with the opening quote of the NEXT, which is the chaining that
# made this quadratic before. The bare runs cannot chain because they
# still stop at "<", so every run is bounded by the next tag either way.
# The quoted runs are ATOMIC, which is what lets them cross line breaks.
# A quoted attribute value may legitimately span lines and hold ">", and
# refusing the newline made the quote-aware branch fail -- the loose
# fallback then took the quoted ">" for the tag end, and the attribute's
# own "</code>" closed the container before its body.
#
# Atomic is not cosmetic here. Once a run has matched to the next quote
# it cannot give characters back, so the closing quote of one tag can
# never re-pair with the opening quote of the NEXT -- the chaining that
# made this scan quadratic, and the reason the runs were line-bounded
# before. The bare branch still excludes both quotes, so a quote starts
# exactly one thing.
_HTML_ATTRIBUTE_RUN = (
    r"(?:(?>\"[^\"]*\")|(?>'[^']*')|[^<>\"'])*|[^<>]*"
)











class CandidateMinerError(ValueError):
    """A safe, content-free error suitable for CLI output."""


class CandidateBudgetExceededError(CandidateMinerError):
    """The input busts a local analysis budget; retrying with fewer messages helps.

    Distinct from the other miner errors precisely so the user-facing report can
    narrow its window and try again instead of failing the whole request. The CLI
    still surfaces it as an ordinary ``CandidateMinerError``.
    """


@dataclass(frozen=True)
class MiningConfig:
    """Deterministic mining parameters recorded in the output artifact."""

    threshold: int = DEFAULT_THRESHOLD
    word_ngram_min: int = DEFAULT_WORD_NGRAM_MIN
    word_ngram_max: int = DEFAULT_WORD_NGRAM_MAX
    cjk_ngram_min: int = DEFAULT_CJK_NGRAM_MIN
    cjk_ngram_max: int = DEFAULT_CJK_NGRAM_MAX
    min_length: int = DEFAULT_MIN_LENGTH
    exclude_covered: bool = False

    def validate(self) -> None:
        for name in (
            "threshold",
            "word_ngram_min",
            "word_ngram_max",
            "cjk_ngram_min",
            "cjk_ngram_max",
            "min_length",
        ):
            if getattr(self, name) < 1:
                raise CandidateMinerError(f"{name} must be at least 1")
        if self.word_ngram_min > self.word_ngram_max:
            raise CandidateMinerError("word_ngram_min cannot exceed word_ngram_max")
        if self.cjk_ngram_min > self.cjk_ngram_max:
            raise CandidateMinerError("cjk_ngram_min cannot exceed cjk_ngram_max")


@dataclass(frozen=True)
class SourceMessage:
    """The only source data retained during mining."""

    language: str
    content: str
    source_line: int


@dataclass(frozen=True)
class _CandidateOccurrence:
    normalized: str
    phrase: str
    coverage_text: str
    start: int
    end: int


@dataclass
class _CandidateStats:
    occurrence_count: int
    source_lines: set[int]
    phrases: set[str]
    occurrences: list[_CandidateOccurrence]


def normalize_language(raw: str) -> str:
    """Normalize an explicit locale tag without guessing from message text."""
    if not isinstance(raw, str) or not raw.strip():
        raise CandidateMinerError("language must be a non-empty string")
    normalized = raw.strip().replace("_", "-").casefold()
    try:
        return _LANGUAGE_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(set(_LANGUAGE_ALIASES.values())))
        raise CandidateMinerError(
            f"unsupported language tag; supported languages: {supported}"
        ) from exc


def read_jsonl(
    input_path: Path,
    *,
    language_override: str | None = None,
) -> tuple[list[SourceMessage], int]:
    """Read the documented JSONL contract and retain assistant text only."""
    if not input_path.is_file():
        raise CandidateMinerError(f"input file does not exist: {input_path}")
    override = normalize_language(language_override) if language_override else None
    messages: list[SourceMessage] = []
    record_count = 0

    try:
        handle = input_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CandidateMinerError(f"unable to open input file: {input_path}") from exc

    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise CandidateMinerError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise CandidateMinerError(
                    f"line {line_number}: each JSONL record must be an object"
                )
            record_count += 1

            role = record.get("role")
            content = record.get("content")
            if not isinstance(role, str) or not role:
                raise CandidateMinerError(
                    f"line {line_number}: role must be a non-empty string"
                )
            if not isinstance(content, str):
                raise CandidateMinerError(
                    f"line {line_number}: content must be a string"
                )
            conversation_id = record.get("conversation_id")
            if conversation_id is not None and not isinstance(conversation_id, str):
                raise CandidateMinerError(
                    f"line {line_number}: conversation_id must be a string when present"
                )
            if role != "assistant":
                continue

            raw_language = override or record.get("lang")
            if raw_language is None:
                raise CandidateMinerError(
                    f"line {line_number}: assistant records require lang or --language"
                )
            language = override or normalize_language(raw_language)
            messages.append(
                SourceMessage(
                    language=language,
                    content=content,
                    source_line=line_number,
                )
            )

    return messages, record_count


# The same markers, minus the leading padding and consuming only ONE space
# after the marker. The greedy form above is right when a fence opener
# follows and wrong when INDENTATION follows: "-     code" is a marker, its
# single padding space, and then a four-column indented code block -- eating
# all five spaces measured it as zero columns and mined the code as prose.
# The padding is stripped separately, and by COLUMN, because a tab is worth
# four of them; see ``_strip_containers_by_column``.
_LIST_MARKER_COLUMN_RE = re.compile(r"(?:[-+*]|\d{1,9}[.)])[ \t]")
_BLOCKQUOTE_COLUMN_RE = re.compile(r">[ \t]?")






def _indent_columns(body: str) -> int:
    """Leading indentation in COLUMNS, expanding a tab to the next multiple of 4."""
    columns = 0
    for character in body:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _strip_containers_by_column(body: str) -> tuple[str, bool, int]:
    r"""Strip container markers whose own padding is worth at most three columns.

    The two marker patterns above both open with ``[ \t]{0,3}``, and a TAB
    matched there is worth FOUR columns, not one. So a tab-indented code block
    whose first content character happened to be ``-`` or ``>`` had that
    character stripped as if it were a container marker, the residual indent
    measured zero, and the line was mined as prose -- and persisted. Only
    SPACES, at most three, can pad a marker.

    Also reports whether a LIST marker was consumed and how deep the
    blockquote prefix ran, which is what tells the caller a new block starts
    here -- a list marker or a fresh quote level interrupts the paragraph
    above it, while the same quote prefix repeated just continues one.
    """
    list_opened = False
    quote_depth = 0
    while True:
        lead = len(body) - len(body.lstrip(" "))
        if lead > 3:
            return body, list_opened, quote_depth
        rest = body[lead:]
        quote = _BLOCKQUOTE_COLUMN_RE.match(rest)
        if quote is not None:
            quote_depth += 1
            body = rest[quote.end() :]
            continue
        marker = _LIST_MARKER_COLUMN_RE.match(rest)
        if marker is None:
            return body, list_opened, quote_depth
        list_opened = True
        body = rest[marker.end() :]














def _indented_code_spans(text: str) -> list[tuple[int, int]]:
    """Return Markdown code lines indented by at least four columns.

    An indented code block cannot INTERRUPT a paragraph, so the previous line
    decides. Without that rule every indented continuation line -- centred
    ASCII art, an aligned lyric, a clause wrapped for width -- was deleted from
    mining, on 6% of a code-free speech corpus.
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    paragraph_open = False
    quote_depth = 0
    for line in text.splitlines(keepends=True):
        line_end = offset + len(line)
        body, list_opened, line_quote_depth = _strip_containers_by_column(line)
        if list_opened or line_quote_depth != quote_depth:
            # A list marker or a new quote level opens a block, and nothing
            # can be a continuation of a paragraph that is not in it.
            paragraph_open = False
        quote_depth = line_quote_depth
        is_code = (
            _indent_columns(body) >= 4 and bool(body.strip()) and not paragraph_open
        )
        if is_code:
            spans.append((offset, line_end))
        # The CONTAINER-STRIPPED body decides, not the raw line: inside a
        # blockquote a bare ">" is a blank line, and reading it as prose kept
        # the paragraph open so the indented code after it was mined.
        paragraph_open = bool(body.strip()) and not is_code
        offset = line_end
    return spans














# ``[label]: destination`` at line start, the reference form of a link. The
# label may carry anything; only the destination is protected, and only when it
# looks like one -- see ``_reference_definition_spans``.
_REFERENCE_DEFINITION_RE = re.compile(
    # The space after the colon is OPTIONAL -- "[cfg]:/api/token" is a valid
    # definition, and requiring one left its destination minable. The
    # destination-shape check in the scanner is what keeps this off ordinary
    # speech, not the space.
    # The <...> form FIRST, and it runs to the closing bracket. A
    # whitespace-delimited capture stopped at the first space, so
    # "[cfg]: <../secret helper phrase>" yielded "<../secret"; the
    # destination-shape check then rejected that fragment for having no
    # closing ">", and the whole destination stayed minable.
    #
    # A BACKSLASH-ESCAPED ">" does not close it, which is the same
    # truncation one level down: "[cfg]: <../a\> secret phrase>" cut the
    # capture at the escaped bracket and left the rest of the destination
    # minable. Escapes are consumed as a unit, so a trailing lone
    # backslash still cannot swallow the newline.
    # The LABEL honours escapes too. Fixing only the DESTINATION left
    # "[cfg\]]: /secret-helper-phrase" unrecognised as a definition at all,
    # so its destination stayed minable -- the same half-fix shape twice in
    # one pattern.
    # A definition may sit inside a blockquote or a list item, which is
    # ordinary Markdown and which every reader resolves. The bare "^"
    # anchor saw only the container marker, so "> [cfg]: /secret" was not
    # a definition at all and its destination stayed minable. Same
    # three-space padding rule as the containers themselves.
    r"^(?:[ \t]{0,3}(?:>[ \t]?|[-+*][ \t]|\d{1,9}[.)][ \t]))*"
    r"[ \t]{0,3}\[(?:\\[^\r\n]|[^\]\r\n\\])*\]:[ \t]*"
    # THREE forms, and the middle one is a fallback rather than a rule: when
    # the escape-aware form finds no LATER unescaped ">" it fails outright,
    # the whitespace-delimited form then stops at the first space, and the
    # shape check rejects that fragment for having no ">" -- so
    # "[cfg]: <../my notes/SECRET\>" ended up with no span at all, where
    # before the escape handling it had a full one. Keeping the old
    # truncating form behind the new one makes this monotone: never less
    # protected than it was.
    r"(?P<target><(?:\\[^\r\n]|[^>\r\n\\])*>|<[^>\r\n]*>|[^ \t\r\n]+)"
    # The optional TITLE is link metadata, not reply prose, and it is where
    # a human-readable string actually lives -- so it is the half of a
    # definition most likely to read as speech and be mined. CommonMark
    # allows all three delimiters.
    # A title may ESCAPE its own delimiter, exactly as the interpolation
    # bodies do -- CommonMark says so. Without it the run ended at the
    # backslash-quote and the rest of the title stayed minable, which is
    # the half of a definition most likely to read as speech.
    # And it may sit on the FOLLOWING line, which CommonMark permits and
    # every reader resolves. Accepting only spaces and tabs left
    # '[cfg]: /api/key\n  "secret helper phrase"' protecting the
    # destination alone, with the readable half minable one line down.
    # Exactly ONE line ending: after a blank line it is a paragraph, not
    # a title, and protecting that would run over ordinary speech.
    # A title may CONTINUE across lines -- CommonMark allows it, and
    # rejecting the line ending protected the destination alone while
    # leaving the rest of the title minable one line down. It ends at a
    # BLANK line, because past that it is a paragraph and protecting a
    # paragraph is how a span swallows ordinary speech.
    #
    # ATOMIC bodies, for the reason the attribute runs are atomic: once a
    # body has run to its closer it cannot give characters back, so the
    # closing delimiter of one title cannot re-pair with the opening one
    # of the next definition and turn the scan quadratic.
    r"(?P<title>(?:[ \t]+|[ \t]*(?:\r\n|\n|\r)[ \t]*)(?:\"(?>(?:\\.|(?!\r?\n[ \t]*\r?\n)[^\"\\])*)\"|'(?>(?:\\.|(?!\r?\n[ \t]*\r?\n)[^'\\])*)'|\((?>(?:\\.|(?!\r?\n[ \t]*\r?\n)[^)\\])*)\)))?",
    re.MULTILINE,
)




def _is_definition_target(target: str) -> bool:
    """Whether a reference definition's destination LOOKS like a destination.

    One copy, because the opener detector asks the same question the span
    scanner does and two spellings of it would drift the way every other
    duplicated shape rule in this module has.
    """
    if target.startswith("<"):
        return target.endswith(">") and len(target) >= 2
    # A FRAGMENT is a destination too -- "[cfg]: #config" is a valid
    # definition and the shape check rejected it, so the whole thing
    # including its title stayed minable.
    if target.startswith(("/", "./", "../", "#")) or _URL_RE.match(target):
        return True
    # An ordinary RELATIVE destination -- "[cfg]: api/key" -- is valid
    # CommonMark that the prefix list above rejected, so its title was mined
    # and persisted.
    #
    # Requiring a path separator AND plain ASCII is what keeps this off a
    # script beat. CJK runs without spaces, so "[小八]:我们一起去吃饭吧" has a
    # whitespace-free "destination" too; accepting those swallowed every line
    # of dialogue written in script form, which is a shape a character really
    # produces. A CJK run holding a slash is still speech, not a path.
    return "/" in target and target.isascii()








# Every OPENER this module knows, and nothing that looks for a closer.
#
# The span scanners exist to find where protected text ENDS, so that the prose
# around it stays minable. That requirement is what forced depth counting,
# paired forms, line budgets and per-family closer gates into this module, and
# every one of those is a boundary a reviewer can ask a new question about --
# which is how the same class of finding kept coming back under a different
# shape.
#
# The product decision is that an opener alone discards the WHOLE reply:
# losing a catchphrase costs nothing, and both consumers of this module are
# review-only (the insights panel and the effects sidecar's attribution
# label), so over-protection cannot weaken the anti-repeat gate itself -- it
# only means the panel shows fewer phrases.
#
# So this asks one question -- is there an opener anywhere -- in one linear
# pass, with no closer search anywhere in it. Adding a newly reported shape is
# one entry here, and cannot move a boundary, because there are no boundaries.
#
# The shapes are the ones the scanners already recognised, reusing their
# constants rather than restating them:
#   * an ASCII backtick, which covers both the inline span and the ``` fence
#   * tilde and FULLWIDTH fences at three or more -- a lone U+FF40 is a
#     kaomoji face part (measured firing on 49.8% of 20k code-free replies)
#     and a lone "~" is a spoken drawl, so neither may trigger alone
#   * the template openers, without their paired or line-fallback forms
#   * a tag-shaped "<...>", keeping the leading-letter requirement that is
#     what holds this off "<3", ">_<" and "->"
_CODE_OPENER_RE = re.compile(
    "|".join(
        (
            r"`",
            r"~{3,}",
            r"｀{3,}",
            r"<!--",
            r"\{\{",
            r"\$\{",
            r"\{%",
            r"\{\#",
            r"<%",
            r"<\?",
            r"\]\(",
            rf"</?[A-Za-z](?>(?:{_HTML_ATTRIBUTE_RUN}))>",
            r"\[[A-Z](?>[A-Z0-9_-]+)\]",
            # A DOTLESS local address -- "user@localhost". The URL rule wants
            # a dot in the domain, so these reached the report and the
            # sidecar intact.
            #
            # ASCII on both sides, deliberately. The CJK spelling was
            # reported alongside it, and it is not safe to take: "我@他一下"
            # is how people say they will @ someone, so a rule that reads
            # "用户@内网" as an address swallows that whole class of reply --
            # the third time in this module that a shape which looks like
            # markup in English is ordinary speech in Chinese.
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9](?>[A-Za-z0-9-]*)",
        )
    )
)


def contains_code_shape(text: str) -> bool:
    """Whether a reply carries any code or markup opener at all.

    Best effort and deliberately coarse: a false positive drops one reply from
    a review panel, while a false negative persists text the user never saw.
    """
    if not text:
        return False
    if _CODE_OPENER_RE.search(text):
        return True
    if _URL_RE.search(text):
        return True
    # Line-oriented and already a single pass, so it is reused whole rather
    # than restated as a pattern -- the "cannot interrupt a paragraph" rule is
    # what keeps centred ASCII art and aligned lyrics out of it.
    if _indented_code_spans(text):
        return True
    # EVERY definition, not the first: a leading one whose destination fails
    # the shape test must not hide a later one that passes.
    return any(
        _is_definition_target(match.group("target"))
        for match in _REFERENCE_DEFINITION_RE.finditer(text)
    )


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """The whole reply, or none of it -- an opener discards the segment.

    Both this and ``_runtime_protected_spans`` used to merge the output of
    eight independent scanners, each of which had to locate a CLOSER so the
    prose around a protected region stayed minable. Keeping a catchphrase is
    not worth that: see ``contains_code_shape`` for why an opener alone is now
    allowed to drop the whole thing, and what it bought.

    The two remain distinct functions because their CALLERS differ in what
    they do with the answer, not in the answer itself.
    """
    return [(0, len(text))] if contains_code_shape(text) else []


def _runtime_protected_spans(text: str) -> list[tuple[int, int]]:
    """Same answer as ``_protected_spans``, on the persistence path.

    These two once differed: the template placeholders were layered on in
    ``_protected_spans`` alone, so a ``${...}`` payload was kept out of the
    REPORT while still reaching the effects sidecar. One detector answers both
    now, which is the asymmetry gone rather than merely documented.
    """
    return _protected_spans(text)


def _unprotected_segments(text: str) -> Iterator[tuple[str, int]]:
    """Yield text and offsets outside protected spans without bridging them."""
    cursor = 0
    for start, end in _protected_spans(text):
        if cursor < start:
            yield text[cursor:start], cursor
        cursor = end
    if cursor < len(text):
        yield text[cursor:], cursor


def _text_segments(text: str, base_offset: int) -> Iterator[tuple[str, int]]:
    """Yield punctuation-bounded mining segments with original-text offsets."""
    cursor = 0
    for match in _TEXT_BOUNDARY_RE.finditer(text):
        if cursor < match.start():
            yield text[cursor : match.start()], base_offset + cursor
        cursor = match.end()
    if cursor < len(text):
        yield text[cursor:], base_offset + cursor


_T = TypeVar("_T")


def _bounded_ngrams(
    values: Sequence[_T],
    minimum: int,
    maximum: int,
) -> Iterator[tuple[_T, ...]]:
    upper = min(maximum, len(values))
    for size in range(minimum, upper + 1):
        for start in range(0, len(values) - size + 1):
            yield tuple(values[start : start + size])


def _is_meaningful(value: str, min_length: int) -> bool:
    compact = "".join(value.split())
    return len(compact) >= min_length and any(char.isalpha() for char in compact)


def _word_tokens(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield NFKC-normalized word tokens with spans in the original text."""
    index = 0
    while index < len(text):
        if not text[index].isalnum() or text[index] == "_":
            index += 1
            continue
        start = index
        index += 1
        while index < len(text):
            char = text[index]
            if char.isalnum() and char != "_":
                index += 1
                continue
            if unicodedata.category(char).startswith("M"):
                index += 1
                continue
            if (
                char in {"'", "\u2019"}
                and index + 1 < len(text)
                and text[index + 1].isalnum()
                and text[index + 1] != "_"
            ):
                index += 1
                continue
            break
        yield unicodedata.normalize("NFKC", text[start:index]), start, index


def _word_candidates(
    text: str,
    config: MiningConfig,
) -> Iterator[_CandidateOccurrence]:
    for unprotected, unprotected_start in _unprotected_segments(text):
        for segment, segment_start in _text_segments(unprotected, unprotected_start):
            token_run: list[tuple[str, int, int]] = []
            for token, start, end in _word_tokens(segment):
                if not any(char.isalpha() for char in token):
                    yield from _word_run_candidates(
                        token_run,
                        config,
                        text,
                    )
                    token_run = []
                    continue
                token_run.append((token, segment_start + start, segment_start + end))
            yield from _word_run_candidates(token_run, config, text)


def _word_run_candidates(
    token_run: Sequence[tuple[str, int, int]],
    config: MiningConfig,
    coverage_text: str,
) -> Iterator[_CandidateOccurrence]:
    for gram in _bounded_ngrams(
        token_run,
        config.word_ngram_min,
        config.word_ngram_max,
    ):
        phrase = " ".join(token for token, _, _ in gram)
        normalized = " ".join(token.casefold() for token, _, _ in gram)
        if _is_meaningful(normalized, config.min_length):
            yield _CandidateOccurrence(
                normalized=normalized,
                phrase=phrase,
                coverage_text=coverage_text,
                start=gram[0][1],
                end=gram[-1][2],
            )


def _is_han(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x3134F
    )


def _is_japanese(char: str) -> bool:
    codepoint = ord(char)
    return (
        _is_han(char)
        or codepoint == 0x3005
        or 0x3031 <= codepoint <= 0x3035
        or codepoint == 0x303B
        or 0x3040 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
        or 0xFF66 <= codepoint <= 0xFF9D
    )


def _is_hangul(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _is_hangul_jamo(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _normalized_characters(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield normalized characters mapped to their original source spans."""
    index = 0
    while index < len(text):
        start = index
        index += 1
        if _is_hangul_jamo(text[start]):
            while index < len(text) and _is_hangul_jamo(text[index]):
                index += 1
        while index < len(text) and (
            unicodedata.category(text[index]).startswith("M")
            or text[index] in {"\uff9e", "\uff9f"}
        ):
            index += 1
        normalized = unicodedata.normalize("NFKC", text[start:index])
        for char in normalized:
            yield char, start, index


def _script_runs(
    text: str,
    predicate,
) -> Iterator[list[tuple[str, int, int]]]:
    run: list[tuple[str, int, int]] = []
    for char, start, end in _normalized_characters(text):
        if predicate(char):
            run.append((char, start, end))
        elif run:
            yield run
            run = []
    if run:
        yield run


def _character_candidates(
    text: str,
    config: MiningConfig,
    predicate,
) -> Iterator[_CandidateOccurrence]:
    for unprotected, unprotected_start in _unprotected_segments(text):
        for segment, segment_start in _text_segments(unprotected, unprotected_start):
            for run in _script_runs(segment, predicate):
                upper = min(config.cjk_ngram_max, len(run))
                for size in range(config.cjk_ngram_min, upper + 1):
                    for start in range(0, len(run) - size + 1):
                        gram = run[start : start + size]
                        phrase = "".join(char for char, _, _ in gram)
                        normalized = phrase.casefold()
                        if _is_meaningful(normalized, config.min_length):
                            yield _CandidateOccurrence(
                                normalized=normalized,
                                phrase=phrase,
                                coverage_text=text,
                                start=segment_start + gram[0][1],
                                end=segment_start + gram[-1][2],
                            )


def _message_candidates(
    message: SourceMessage,
    config: MiningConfig,
) -> Iterator[_CandidateOccurrence]:
    language = message.language
    if language in _WHITESPACE_LANGUAGES:
        yield from _word_candidates(message.content, config)
        return
    if language.startswith("zh"):
        yield from _character_candidates(message.content, config, _is_han)
        return
    if language == "ja":
        yield from _character_candidates(message.content, config, _is_japanese)
        return
    if language == "ko":
        # Korean prose is normally space-delimited, but repeated compounds and
        # onomatopoeia often are not. Keep both families, but do not count an
        # identical single-token occurrence once in each strategy.
        overlapping_word_candidates = set()
        for candidate in _word_candidates(message.content, config):
            overlapping_word_candidates.add(
                (
                    candidate.normalized,
                    candidate.coverage_text,
                    candidate.start,
                    candidate.end,
                )
            )
            yield candidate
        for candidate in _character_candidates(message.content, config, _is_hangul):
            overlap_key = (
                candidate.normalized,
                candidate.coverage_text,
                candidate.start,
                candidate.end,
            )
            if overlap_key in overlapping_word_candidates:
                continue
            yield candidate
        return
    raise CandidateMinerError(f"unsupported normalized language: {language}")


def _coverage_language(language: str) -> str:
    return "zh" if language in {"zh", "zh-CN"} else language


def _coverage_result(
    language: str,
    occurrences: Sequence[_CandidateOccurrence],
    rules_by_language: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    compiled_rules_cache: dict[str, tuple[tuple[str, re.Pattern[str]], ...]],
    protected_cache: dict[str, list[tuple[int, int]]],
    match_cache: dict[tuple[str, str, int], tuple[tuple[int, int], ...]],
) -> tuple[list[str], bool]:
    covered: set[str] = set()
    coverage_language = _coverage_language(language)
    compiled_rules = compiled_rules_cache.get(coverage_language)
    if compiled_rules is None:
        pending_rules: list[tuple[str, re.Pattern[str]]] = []
        for rule in rules_by_language.get(coverage_language, ()):
            rule_id = rule.get("id")
            pattern = rule.get("find")
            flags = rule.get("flags", 0)
            if not isinstance(rule_id, str) or not isinstance(pattern, str):
                continue
            try:
                compiled = re.compile(pattern, int(flags))
            except (re.error, TypeError, ValueError) as exc:
                raise CandidateMinerError(
                    f"existing rule {rule_id} has an invalid pattern"
                ) from exc
            pending_rules.append((rule_id, compiled))
        compiled_rules = tuple(pending_rules)
        compiled_rules_cache[coverage_language] = compiled_rules

    all_occurrences_covered = bool(occurrences)
    for occurrence in occurrences:
        occurrence_covered = False
        protected = protected_cache.get(occurrence.coverage_text)
        if protected is None:
            protected = _runtime_protected_spans(occurrence.coverage_text)
            protected_cache[occurrence.coverage_text] = protected
        for rule_index, (rule_id, compiled) in enumerate(compiled_rules):
            cache_key = (coverage_language, occurrence.coverage_text, rule_index)
            match_spans = match_cache.get(cache_key)
            if match_spans is None:
                match_spans = tuple(
                    (match.start(), match.end())
                    for match in compiled.finditer(occurrence.coverage_text)
                    if match.start() != match.end()
                    and not any(
                        match.start() < protected_end and match.end() > protected_start
                        for protected_start, protected_end in protected
                    )
                )
                match_cache[cache_key] = match_spans
            if any(
                start <= occurrence.start and occurrence.end <= end
                for start, end in match_spans
            ):
                covered.add(rule_id)
                occurrence_covered = True
        if not occurrence_covered:
            all_occurrences_covered = False
    return sorted(covered), all_occurrences_covered


def load_current_rules() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """Load the curated runtime table for read-only coverage analysis."""
    try:
        from config.prompts.prompts_slop import SLOP_RULES
    except Exception as exc:
        raise CandidateMinerError("unable to load current SLOP_RULES") from exc
    return SLOP_RULES


def build_report(
    messages: Sequence[SourceMessage],
    *,
    input_record_count: int,
    config: MiningConfig,
    rules_by_language: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    message_count_threshold: int = 1,
    max_occurrences: int | None = None,
) -> dict[str, object]:
    """Build a deterministic, review-only candidate report."""
    config.validate()
    if message_count_threshold < 1:
        raise CandidateMinerError("message_count_threshold must be at least 1")
    if max_occurrences is not None and max_occurrences < 1:
        raise CandidateMinerError("max_occurrences must be at least 1")
    current_rules = (
        load_current_rules() if rules_by_language is None else rules_by_language
    )
    stats: dict[tuple[str, str], _CandidateStats] = {}
    retained_occurrence_count = 0

    for message in messages:
        for occurrence in _message_candidates(message, config):
            retained_occurrence_count += 1
            if (
                max_occurrences is not None
                and retained_occurrence_count > max_occurrences
            ):
                raise CandidateBudgetExceededError(
                    "assistant history exceeds local analysis limit"
                )
            key = (message.language, occurrence.normalized)
            candidate_stats = stats.get(key)
            if candidate_stats is None:
                candidate_stats = _CandidateStats(0, set(), set(), [])
                stats[key] = candidate_stats
            candidate_stats.occurrence_count += 1
            candidate_stats.source_lines.add(message.source_line)
            candidate_stats.phrases.add(occurrence.phrase)
            candidate_stats.occurrences.append(occurrence)

    candidates: list[dict[str, object]] = []
    compiled_rules_cache: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {}
    protected_cache: dict[str, list[tuple[int, int]]] = {}
    match_cache: dict[tuple[str, str, int], tuple[tuple[int, int], ...]] = {}
    for (language, normalized), candidate_stats in stats.items():
        if candidate_stats.occurrence_count < config.threshold:
            continue
        if len(candidate_stats.source_lines) < message_count_threshold:
            continue
        covered_by, all_occurrences_covered = _coverage_result(
            language,
            candidate_stats.occurrences,
            current_rules,
            compiled_rules_cache=compiled_rules_cache,
            protected_cache=protected_cache,
            match_cache=match_cache,
        )
        if config.exclude_covered and all_occurrences_covered:
            continue
        candidates.append(
            {
                "covered_by_rule_ids": covered_by,
                "language": language,
                "message_count": len(candidate_stats.source_lines),
                "normalized_phrase": normalized,
                "occurrence_count": candidate_stats.occurrence_count,
                "phrase": min(
                    candidate_stats.phrases, key=lambda item: (item.casefold(), item)
                ),
                "status": "pending",
            }
        )

    candidates.sort(
        key=lambda item: (
            item["language"],
            -item["message_count"],
            -item["occurrence_count"],
            item["normalized_phrase"],
            item["phrase"],
        )
    )
    language_counts = Counter(message.language for message in messages)

    return {
        "artifact_type": ARTIFACT_TYPE,
        "candidates": candidates,
        "parameters": {
            "cjk_ngram_range": [config.cjk_ngram_min, config.cjk_ngram_max],
            "exclude_covered": config.exclude_covered,
            "min_length": config.min_length,
            "occurrence_threshold": config.threshold,
            "word_ngram_range": [config.word_ngram_min, config.word_ngram_max],
        },
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "assistant_message_count": len(messages),
            "candidate_count": len(candidates),
            "input_record_count": input_record_count,
            "language_counts": dict(sorted(language_counts.items())),
            "languages": sorted(language_counts),
        },
    }


def _truncate_each(
    analyzed: list["SourceMessage"], max_length_of
) -> list["SourceMessage"]:
    """Shorten every message to its own limit, keeping all of them.

    The move both budgets make once the window is down to the number of
    distinct messages a candidate needs: a SHORTER look at three replies can
    still find a phrase in all three, while two whole replies cannot find it
    in three. One implementation, because the two budgets had already drifted
    -- the character path floored and the occurrence path halved the window.
    """
    shortened: list[SourceMessage] = []
    for message in analyzed:
        limit = max_length_of(message)
        if len(message.content) <= limit:
            shortened.append(message)
            continue
        shortened.append(
            SourceMessage(
                language=message.language,
                content=message.content[:limit],
                source_line=message.source_line,
            )
        )
    return shortened


def _clip_dominant_message(
    analyzed: list[SourceMessage],
) -> list[SourceMessage] | None:
    """Halve the one message that dominates the window, or None if none does.

    Found by POSITION, not assumed to be the newest. Both budgets clipped
    only the newest and then evicted history, so an oversized reply sitting
    anywhere else took the whole window down with it: three ordinary replies
    sharing a phrase plus one 128 KiB second-newest reply left exactly ONE
    message analyzed -- the evictions threw away the replies that make the
    distinct-message threshold reachable, and then threw away the outlier
    too -- and the panel reported not enough history for a window that had
    plenty.

    "Dominant" is measured against the rest rather than assumed, so a window
    that is merely large as a whole still narrows by dropping messages,
    which is the cheaper cut. Halving also terminates: a message stops being
    dominant after enough halvings, and the caller falls back to evicting.
    """
    if len(analyzed) < 2:
        return None
    index = max(
        range(len(analyzed)), key=lambda position: len(analyzed[position].content)
    )
    longest = analyzed[index]
    others = sum(len(message.content) for message in analyzed) - len(
        longest.content
    )
    # Against the AVERAGE of the rest, not their sum. Integer arithmetic so
    # the comparison cannot drift on a float.
    if (
        len(longest.content) * (len(analyzed) - 1)
        <= _USER_REVIEW_OUTLIER_RATIO * others
        or len(longest.content) <= _USER_REVIEW_MIN_TRUNCATED_CHARACTERS
    ):
        return None
    return (
        analyzed[:index]
        + [
            SourceMessage(
                language=longest.language,
                content=longest.content[: len(longest.content) // 2],
                source_line=longest.source_line,
            )
        ]
        + analyzed[index + 1 :]
    )


def build_user_review_report(
    messages: Sequence[SourceMessage],
    *,
    message_count_threshold: int = DEFAULT_MESSAGE_COUNT_THRESHOLD,
    rules_by_language: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> dict[str, object]:
    """Build the privacy-minimal report exposed by the user review UI.

    The maintainer CLI historically filters by total occurrences.  The user
    workflow is deliberately stricter: a phrase must also occur in at least
    ``message_count_threshold`` distinct assistant messages.

    Budget handling narrows the window instead of failing the request.  The two
    budgets are wildly out of proportion: n-gram expansion scales with the length
    of each punctuation-bounded *segment*, not with the total character count, so
    100 replies of ~280 unbroken Han characters bust
    ``USER_REVIEW_MAX_OCCURRENCES`` at only ~21% of
    ``USER_REVIEW_MAX_INPUT_CHARACTERS``.  Raising there turned an ordinary
    request into a 422 the UI could only render as "please try again", which never
    succeeds.  The oldest messages are dropped until the window fits, and the
    summary reports what was actually analyzed.
    """
    if message_count_threshold < 1:
        raise CandidateMinerError("message_count_threshold must be at least 1")

    analyzed = list(messages)
    content_truncated = False

    # Clip the NEWEST reply BEFORE narrowing, and leave room behind it.
    #
    # Narrowing first meant an oversized newest reply kept the total over
    # budget until every older reply had been dropped, and only the survivor
    # was then clipped. A character with plenty of ordinary history plus one
    # very long latest reply was therefore analysed as a single message, the
    # distinct-message threshold removed every candidate, and the panel told
    # the user there was not enough history -- while their history sat right
    # there.
    #
    # Clipping it to the WHOLE budget would not have helped: it would still
    # fill the window on its own and the loop below would still drop the
    # rest. So when there is history behind it, the newest reply may take at
    # most half, and the loop fills the remainder with as many preceding
    # replies as fit.
    #
    # Cutting mid-container is safe in the protective direction: an
    # unterminated fence or <code>/<pre> protects through the end of the
    # text, so a severed block stays protected rather than exposed.
    if analyzed:
        newest_budget = (
            USER_REVIEW_MAX_INPUT_CHARACTERS // 2
            if len(analyzed) > 1
            else USER_REVIEW_MAX_INPUT_CHARACTERS
        )
        newest = analyzed[-1]
        if len(newest.content) > newest_budget:
            analyzed = analyzed[:-1] + [
                SourceMessage(
                    language=newest.language,
                    content=newest.content[:newest_budget],
                    source_line=newest.source_line,
                )
            ]
            content_truncated = True

    while (
        len(analyzed) > 1
        and sum(len(message.content) for message in analyzed)
        > USER_REVIEW_MAX_INPUT_CHARACTERS
    ):
        # Clip a dominant message before evicting any. Evicting from the
        # front first discarded the replies that carry the repeated phrase
        # and then discarded the outlier anyway, so the window shrank to
        # one message and the panel reported not enough history.
        clipped = _clip_dominant_message(analyzed)
        if clipped is not None:
            analyzed = clipped
            content_truncated = True
            continue
        # Drop the oldest message that is ITSELF over its fair share of
        # the budget, not simply the oldest. A short old reply is not what
        # busted the budget and dropping it buys almost nothing, so with
        # four budget-sized replies in the window the three short ones
        # ahead of them were thrown away one by one and the window
        # collapsed to a single message -- the repeated phrase went with
        # them, and the panel reported not enough history.
        #
        # Order among the survivors is untouched: exactly one message
        # leaves. When nothing is over its share the oldest goes.
        #
        # Unless leaving would take the window below the number of
        # DISTINCT messages a candidate needs. Three uniformly large
        # replies sharing a phrase used to lose one and report "not
        # enough history" for a window that had it -- nothing dominates,
        # so nothing gets clipped, and eviction takes it to two. A
        # SHORTER look at three messages can still find a phrase in all
        # three; two whole messages cannot find it in three.
        if len(analyzed) <= message_count_threshold:
            share = USER_REVIEW_MAX_INPUT_CHARACTERS // len(analyzed)
            analyzed = _truncate_each(analyzed, lambda message: share)
            content_truncated = True
            break
        share = USER_REVIEW_MAX_INPUT_CHARACTERS // len(analyzed)
        victim = next(
            (
                index
                for index, message in enumerate(analyzed)
                if len(message.content) > share
            ),
            0,
        )
        analyzed = analyzed[:victim] + analyzed[victim + 1 :]

    # A lone survivor can still be over the budget if it was never the
    # newest -- it cannot be, since narrowing keeps the newest, but the
    # single-message case takes the full budget above and needs no second cut.
    if analyzed and len(analyzed[0].content) > USER_REVIEW_MAX_INPUT_CHARACTERS:
        oversized = analyzed[0]
        analyzed = [
            SourceMessage(
                language=oversized.language,
                content=oversized.content[:USER_REVIEW_MAX_INPUT_CHARACTERS],
                source_line=oversized.source_line,
            )
        ] + analyzed[1:]
        content_truncated = True

    config = MiningConfig(threshold=DEFAULT_THRESHOLD)
    while True:
        try:
            maintainer_report = build_report(
                analyzed,
                input_record_count=len(analyzed),
                config=config,
                rules_by_language=rules_by_language,
                message_count_threshold=message_count_threshold,
                max_occurrences=USER_REVIEW_MAX_OCCURRENCES,
            )
            break
        except CandidateBudgetExceededError:
            # When the NEWEST reply is the outlier, halve ITS body first.
            #
            # Halving the window instead discarded the very history that
            # makes the distinct-message threshold reachable: three ordinary
            # replies sharing a phrase plus one very long reply narrowed to
            # that one reply, and the repeated phrase went with the messages
            # that carried it. The panel then reported not enough history.
            # Same ordering fault the character budget had, in the path that
            # actually binds: a reply long enough to matter passes the
            # occurrence budget long before the character one.
            #
            # "Outlier" is measured against the rest rather than assumed, so
            # a window that is merely large as a whole still narrows by
            # dropping messages, which is the cheaper cut.
            if len(analyzed) > 1:
                # By POSITION, not newest-only: this path had the same
                # blind spot the character budget did, and an outlier
                # sitting anywhere but last took the history with it.
                clipped = _clip_dominant_message(analyzed)
                if clipped is not None:
                    analyzed = clipped
                    content_truncated = True
                    continue
                # Nothing dominates, so nothing was clipped. Halving the
                # WINDOW here is what the character budget already refuses
                # to do: three uniformly occurrence-heavy replies sharing a
                # phrase went to one, and the panel reported "not enough
                # history" for a window in which every single message
                # carried the phrase. This budget binds long before the
                # character one -- an uninterrupted CJK reply busts it at
                # about 20k characters, a sixth of the advertised limit --
                # so it is the path that actually reaches this case.
                #
                # Halve the BODIES instead, and keep every message. The
                # floor is the same one the character budget uses, for the
                # same reason, through the same helper.
                #
                # The floor YIELDS rather than fails. Once every message is
                # down to the minimum useful length and the window still
                # busts the budget, keeping the sample would mean returning
                # a 422 -- and the panel can only render that as "try
                # again", which never helps. Narrowing below the threshold
                # is a degraded answer; refusing is no answer, and the
                # browser's minimum-sample check exists to render exactly
                # this degraded case honestly.
                if len(analyzed) <= message_count_threshold and any(
                    len(message.content) > _USER_REVIEW_MIN_TRUNCATED_CHARACTERS
                    for message in analyzed
                ):
                    analyzed = _truncate_each(
                        analyzed,
                        lambda message: max(
                            _USER_REVIEW_MIN_TRUNCATED_CHARACTERS,
                            len(message.content) // 2,
                        ),
                    )
                    content_truncated = True
                    continue
                analyzed = analyzed[-(len(analyzed) // 2):]
                continue
            # One reply left and it still busts the budget. Dropping whole
            # messages floors at one, so rethrowing here turned every
            # selection containing that reply into a 422 the panel can only
            # render as "please try again" -- and retrying never helps,
            # because the same reply is still the newest one. Halve its BODY
            # instead, which is the move the character cap above already
            # makes for the same reason.
            #
            # Not an exotic input: mining generates a fixed number of n-grams
            # per character, so an uninterrupted Chinese reply stops fitting
            # at about 20k characters -- a sixth of the 128 KiB the character
            # limit advertises. ASCII reaches roughly twice as far. Cutting
            # mid-container stays safe in the protective direction, for the
            # same reason the character cap gives above.
            remaining = analyzed[0]
            if len(remaining.content) <= _USER_REVIEW_MIN_TRUNCATED_CHARACTERS:
                raise
            analyzed = [
                SourceMessage(
                    language=remaining.language,
                    content=remaining.content[: len(remaining.content) // 2],
                    source_line=remaining.source_line,
                )
            ]
            content_truncated = True
    all_candidates = maintainer_report["candidates"]
    candidates = all_candidates[:USER_REVIEW_MAX_CANDIDATES]
    parameters = dict(maintainer_report["parameters"])
    parameters["message_count_threshold"] = message_count_threshold
    parameters["input_character_limit"] = USER_REVIEW_MAX_INPUT_CHARACTERS
    parameters["occurrence_retention_limit"] = USER_REVIEW_MAX_OCCURRENCES
    parameters["candidate_output_limit"] = USER_REVIEW_MAX_CANDIDATES

    return {
        "artifact_type": USER_REVIEW_ARTIFACT_TYPE,
        "candidates": candidates,
        "parameters": parameters,
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "assistant_message_count": len(messages),
            "analyzed_message_count": len(analyzed),
            # WHICH replies survived, not just how many. The eviction drops
            # the oldest message that is over its fair share, which can be an
            # INTERIOR one, so the survivors are no longer a contiguous
            # suffix -- and the caller that reconstructed them as "the last
            # N" then attributed effectiveness to replies that were dropped
            # while omitting ones that were mined.
            #
            # Source lines, because that is what a caller can align against:
            # every clip rebuilds its message with the original line, so a
            # body cut short still maps to the reply it came from.
            "analyzed_source_lines": [
                message.source_line for message in analyzed
            ],
            # Two distinct mechanisms, reported separately: whole messages
            # dropped off the front (derivable from the two counts) versus one
            # oversized reply's BODY cut short (not derivable at all). Collapsing
            # them made the panel say "only the latest 1 of 1 fit".
            "messages_truncated": len(analyzed) < len(messages),
            "content_truncated": content_truncated,
            "candidate_count": len(all_candidates),
            "returned_candidate_count": len(candidates),
            "candidates_truncated": len(candidates) < len(all_candidates),
        },
    }


def serialize_report(report: Mapping[str, object]) -> str:
    """Serialize with stable key ordering and a single trailing newline."""
    return (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def write_report(output_path: Path, report: Mapping[str, object]) -> None:
    """Atomically write a report using stable LF newlines."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(serialize_report(report))
        os.replace(temporary_name, output_path)
    except OSError as exc:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                # Do not mask the primary report write failure with cleanup failure.
                pass
        raise CandidateMinerError(
            f"unable to write output file: {output_path}"
        ) from exc


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mine pending natural-expression candidates from an explicitly provided "
            "local JSONL file. No rules are generated, modified, or activated."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="input JSONL file")
    parser.add_argument("--output", required=True, type=Path, help="review JSON file")
    parser.add_argument(
        "--language",
        help="explicit language/locale for every assistant record; overrides record lang",
    )
    parser.add_argument(
        "--threshold",
        type=_positive_int,
        default=DEFAULT_THRESHOLD,
        help=f"minimum occurrence count (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--word-ngram-min",
        type=_positive_int,
        default=DEFAULT_WORD_NGRAM_MIN,
    )
    parser.add_argument(
        "--word-ngram-max",
        type=_positive_int,
        default=DEFAULT_WORD_NGRAM_MAX,
    )
    parser.add_argument(
        "--cjk-ngram-min",
        type=_positive_int,
        default=DEFAULT_CJK_NGRAM_MIN,
    )
    parser.add_argument(
        "--cjk-ngram-max",
        type=_positive_int,
        default=DEFAULT_CJK_NGRAM_MAX,
    )
    parser.add_argument(
        "--min-length",
        type=_positive_int,
        default=DEFAULT_MIN_LENGTH,
        help=f"minimum non-space character length (default: {DEFAULT_MIN_LENGTH})",
    )
    parser.add_argument(
        "--exclude-covered",
        action="store_true",
        help="omit candidates matched by a current curated SLOP_RULES pattern",
    )
    parser.add_argument(
        "--debug-candidates",
        action="store_true",
        help="explicitly print candidate phrases; may expose assistant text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path:
        parser.error("--input and --output must be different files")

    config = MiningConfig(
        threshold=args.threshold,
        word_ngram_min=args.word_ngram_min,
        word_ngram_max=args.word_ngram_max,
        cjk_ngram_min=args.cjk_ngram_min,
        cjk_ngram_max=args.cjk_ngram_max,
        min_length=args.min_length,
        exclude_covered=args.exclude_covered,
    )
    try:
        messages, record_count = read_jsonl(
            input_path,
            language_override=args.language,
        )
        report = build_report(
            messages,
            input_record_count=record_count,
            config=config,
        )
        write_report(output_path, report)
    except CandidateMinerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = report["summary"]
    languages = ", ".join(summary["languages"]) or "none"
    print(f"input: {input_path}")
    print(f"output: {output_path}")
    print(
        "assistant_messages="
        f"{summary['assistant_message_count']} candidates={summary['candidate_count']} "
        f"languages={languages}"
    )
    if args.debug_candidates:
        for candidate in report["candidates"]:
            print(f"[debug candidate] {candidate['language']}: {candidate['phrase']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
