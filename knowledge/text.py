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

"""Deterministic text handling for untrusted pack content.

Everything in a knowledge pack comes from a third party, and some of it ends
up inside the model's context. The helpers here make sure that text can never
pose as conversation structure:

* ``strip_chat_markup`` removes ChatML-style control tokens and line-leading
  role markers, repeated until nothing changes. A single pass is not enough:
  ``<|im_<|im_end|>start|>`` turns into ``<|im_start|>`` once the inner token
  is removed.
* ``neutralize_fence`` breaks any run of ``=`` long enough to imitate the
  ``======[...]======`` delimiters used when knowledge is rendered for the
  model, so a card cannot close the reference block early.
"""

from __future__ import annotations

import re
import unicodedata


# Control characters, plus lone UTF-16 surrogates: JSON can carry "\ud800",
# but such a string cannot be encoded as UTF-8 again.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
_CHAT_TOKEN_RE = re.compile(
    r"<\|\s*(?:im_start|im_end|im_sep|endoftext|system|user|assistant|"
    r"start_header_id|end_header_id|eot_id|begin_of_text)\s*\|>",
    re.IGNORECASE,
)
# Unicode default-ignorable code points, rendered as nothing: soft hyphen,
# combining grapheme joiner, zero-width spaces/joiners, bidi controls, BOM,
# Hangul fillers, variation selectors, tags. They are removed from pack text,
# and the role-marker pattern also tolerates them, so an invisible prefix
# cannot hide a line-leading role.
_INVISIBLE = (
    "\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
    "\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0"
    "\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE}]")
_ROLE_MARKER_RE = re.compile(
    rf"(?im)^[ \t{_INVISIBLE}]*(?:system|developer|assistant|user|human)[ \t{_INVISIBLE}]*[:：]"
)
_FENCE_RUN_RE = re.compile(r"={3,}")
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TITLE_SPACE_RE = re.compile(r"\s+")

# CJK ideographs, kana and hangul are indexed as character bigrams; everything
# else that is a word character is indexed as a whole lower-cased word.
_CJK_RANGES = (
    "぀-ヿ"  # hiragana + katakana
    "㐀-䶿"  # CJK extension A
    "一-鿿"  # CJK unified ideographs
    "가-힯"  # hangul syllables
    "豈-﫿"  # CJK compatibility ideographs
    "𠀀-𿿿"  # supplementary planes: CJK extensions B and later
)
_TOKEN_RE = re.compile(rf"[{_CJK_RANGES}]+|[^\W_{_CJK_RANGES}]+")
_CJK_RUN_RE = re.compile(rf"^[{_CJK_RANGES}]+$")
MAX_QUERY_TOKENS = 128


_MAX_MARKUP_PASSES = 16
_UNICODE_LINE_BREAKS = str.maketrans({"\u0085": "\n", "\u2028": "\n", "\u2029": "\n"})


def _drop_orphan_marks(text: str) -> str:
    """Remove combining marks that start a line: they attach to nothing and
    would otherwise sit, invisible, in front of a role marker."""
    if not any(unicodedata.category(ch).startswith("M") for ch in text):
        return text
    lines = text.split("\n")
    for index, line in enumerate(lines):
        start = 0
        while start < len(line) and (
            unicodedata.category(line[start]).startswith("M") or line[start] in " \t"
        ):
            start += 1
        if start and any(unicodedata.category(ch).startswith("M") for ch in line[:start]):
            lines[index] = line[start:]
    return "\n".join(lines)


def strip_chat_markup(value: str) -> str:
    """Remove chat-control tokens and role markers until a fixed point.

    Unicode line breaks (NEL, LS, PS) become ``\n`` first: a role marker after
    one of them starts a line for a reader, but not for ``^`` in a regex.
    """
    text = _INVISIBLE_RE.sub("", str(value or "").translate(_UNICODE_LINE_BREAKS))
    for _ in range(_MAX_MARKUP_PASSES):
        cleaned = _CHAT_TOKEN_RE.sub("", text)
        cleaned = _drop_orphan_marks(cleaned)
        cleaned = _ROLE_MARKER_RE.sub("", cleaned)
        if cleaned == text:
            return cleaned
        text = cleaned
    # Deliberately nested markup: stop rescanning (each pass is linear, an
    # unbounded loop is not) and defuse what is left in one pass, without
    # deleting anything that could expose yet another marker.
    text = text.replace("<", "\u2039").replace(">", "\u203a")
    return _ROLE_MARKER_RE.sub(lambda match: match.group(0)[:-1] + "\u2236", text)


def neutralize_fence(value: str) -> str:
    """Shorten ``=`` runs so content can never reproduce a fence line."""
    return _FENCE_RUN_RE.sub("==", str(value or ""))


def sanitize_external_text(value: object, *, max_chars: int) -> str:
    """Normalize third-party text for storage.

    The result is NFC-normalized (NFKC would rewrite full-width CJK
    punctuation into ASCII), free of control characters and chat markup, has
    collapsed horizontal whitespace and at most one blank line in a row, and
    is cut to ``max_chars``. Matching uses NFKC separately (``search_tokens``,
    ``title_key``, ``fold_surface``).
    """
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = strip_chat_markup(text)
    text = "\n".join(
        _HORIZONTAL_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")
    )
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    return text[:max_chars].strip()


def single_line(value: object, *, max_chars: int) -> str:
    """Collapse text onto one line, e.g. for titles inside rendered context."""
    return _TITLE_SPACE_RE.sub(" ", str(value or "")).strip()[:max_chars]


def title_key(value: object) -> str:
    """Identity key of a title within one pack (case- and width-insensitive)."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _TITLE_SPACE_RE.sub(" ", normalized).strip().casefold()


def strict_surface(value: object) -> str:
    """Exact-match form that keeps symbols: ``C``, ``C++`` and ``C#`` differ."""
    normalized = _strip_marks(unicodedata.normalize("NFKC", str(value or "")).casefold())
    return _TITLE_SPACE_RE.sub(" ", normalized).strip()


def is_cjk_token(token: str) -> bool:
    return bool(_CJK_RUN_RE.match(token))


def word_runs(value: str) -> set[str]:
    """Distinct word runs of already-normalized text (see ``search_view``)."""
    return set(_TOKEN_RE.findall(value))


_TRAILING_PUNCT = frozenset("?!.。？！,，、;；:：…")


def loose_surface(value: object) -> str:
    """Fallback exact-match form: only letters and digits, or "" if unsafe.

    Surrounding brackets/quotes and trailing sentence punctuation are ignored
    ("「猫」", "Python?"), and separators inside a name may differ ("Re:Zero",
    "re zero"). A name that still begins or ends with a symbol ("C++", "C#",
    ".NET") gets no loose form: dropping the symbol would make it another name.
    """
    text = strict_surface(value)
    start, end = 0, len(text)
    while start < end and (text[start] == " " or unicodedata.category(text[start]) in ("Ps", "Pi")):
        start += 1
    while end > start and (
        text[end - 1] == " "
        or text[end - 1] in _TRAILING_PUNCT
        or unicodedata.category(text[end - 1]) in ("Pe", "Pf")
    ):
        end -= 1
    core = text[start:end]
    if not core or not core[0].isalnum() or not core[-1].isalnum():
        return ""
    return "".join(ch for ch in core if ch.isalnum())


def _strip_marks(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def search_view(value: object) -> str:
    """Text normalized the way ``search_tokens`` normalizes each token."""
    return _strip_marks(unicodedata.normalize("NFKC", str(value or "")).casefold())


def search_tokens(value: object, *, unigrams: bool = False) -> list[str]:
    """Split text into the units stored in and queried against the FTS index.

    CJK runs become overlapping character bigrams (a single character stays a
    unigram); other word runs stay whole. Latin text is case-folded and loses
    its diacritics, because ``unicode61`` matches it that way too and both
    sides must agree on what a token is.

    ``unigrams`` (query side only) also yields every CJK character of longer
    runs, so a one-character name such as "猫" is found inside "介绍一下猫".
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    tokens: list[str] = []
    for run in _TOKEN_RE.findall(text):
        if _CJK_RUN_RE.match(run):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
                if unigrams:
                    tokens.extend(run)
        else:
            folded = _strip_marks(run)
            if folded:
                tokens.append(folded)
    return tokens


def fts_match_expression(value: object) -> str:
    """Build an FTS5 OR-query from user text; empty when nothing is indexable.

    Every token is double-quoted, so FTS operators typed by the user (``AND``,
    ``NEAR``, ``*``, column filters) stay literal.
    """
    unique = [t for t in dict.fromkeys(search_tokens(value, unigrams=True)) if '"' not in t]
    if len(unique) > MAX_QUERY_TOKENS:
        # Sample across the whole query rather than keeping only its start:
        # the term that matters may come last.
        last = len(unique) - 1
        unique = [unique[round(i * last / (MAX_QUERY_TOKENS - 1))] for i in range(MAX_QUERY_TOKENS)]
    return " OR ".join(f'"{token}"' for token in unique)
