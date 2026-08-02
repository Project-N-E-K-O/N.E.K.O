"""Tokenizer for the tactical document index.

Mirrors the rule the host uses for its own retrieval (`memory/hybrid_recall.py`):
Latin-ish runs become whole tokens, CJK runs become overlapping n-grams. It is
re-implemented here rather than imported because the host functions are private
and bound to the memory server's stop-name list and fact store.

Why n-grams instead of SQLite FTS5: every FTS5 table in this repo uses the
`unicode61` tokenizer, which treats a run of Han characters as a *single* token,
so "巡洋舰" would not match "重巡洋舰应该保持距离". That is why the host's real
retrieval path hand-rolls n-grams too.

Two token sets, on purpose:

* `index_terms` -- words plus CJK **2-grams**. This is what gets persisted, and
  2-grams are a recall superset of 3-grams, so nothing becomes unreachable.
* `rank_terms` -- words plus CJK 2-grams **and** 3-grams. Only ever computed on
  the small candidate set that survived stage one, where precision matters and
  the text has already been loaded.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Runs of letters/digits/underscore; everything else separates.
_SEGMENT_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Han, Hiragana, Katakana, Hangul and the CJK compatibility block. A run of these
# has no spaces to split on, which is the whole reason n-grams are needed.
_CJK_RANGES = (
    "\u3040-\u309f"   # Hiragana
    "\u30a0-\u30ff"   # Katakana
    "\u3400-\u4dbf"   # CJK Extension A
    "\u4e00-\u9fff"   # CJK Unified Ideographs
    "\uac00-\ud7af"   # Hangul syllables
    "\uf900-\ufaff"   # CJK Compatibility Ideographs
)
_CJK_RUN_RE = re.compile(f"[{_CJK_RANGES}]+")
_NON_CJK_RUN_RE = re.compile(f"[^{_CJK_RANGES}]+")

# Single characters carry too little signal to be worth a posting.
MIN_WORD_LENGTH = 2


def normalize(text: str) -> str:
    """NFKC-fold and lowercase so full-width and half-width forms unify."""
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _split_runs(segment: str) -> list[tuple[str, bool]]:
    """Split a segment into (run, is_cjk) pairs.

    The host applies a majority-CJK test to the whole segment, which loses the
    Latin half of mixed gaming vocabulary like "AP弹" or "T10巡洋". Splitting at
    the script boundary keeps both halves searchable.
    """
    runs: list[tuple[str, bool]] = []
    index = 0
    while index < len(segment):
        cjk_match = _CJK_RUN_RE.match(segment, index)
        if cjk_match is not None:
            runs.append((cjk_match.group(), True))
            index = cjk_match.end()
            continue
        other_match = _NON_CJK_RUN_RE.match(segment, index)
        if other_match is None:  # pragma: no cover - the two regexes are total
            break
        runs.append((other_match.group(), False))
        index = other_match.end()
    return runs


def _ngrams(run: str, sizes: tuple[int, ...]) -> list[str]:
    out: list[str] = []
    for size in sizes:
        if len(run) < size:
            # A run shorter than the window is still worth indexing whole,
            # otherwise a two-character ship class would vanish at n=3.
            if len(run) >= MIN_WORD_LENGTH and size == sizes[0]:
                out.append(run)
            continue
        for start in range(len(run) - size + 1):
            out.append(run[start:start + size])
    return out


def _tokens(text: str, cjk_sizes: tuple[int, ...]) -> list[str]:
    normalized = normalize(text)
    out: list[str] = []
    for segment in _SEGMENT_RE.findall(normalized):
        for run, is_cjk in _split_runs(segment):
            if is_cjk:
                out.extend(_ngrams(run, cjk_sizes))
            elif len(run) >= MIN_WORD_LENGTH:
                out.append(run)
    return out


def index_terms(text: str) -> list[str]:
    """Words plus CJK 2-grams. Multiplicity preserved for term frequency."""
    return _tokens(text, (2,))


def rank_terms(text: str) -> list[str]:
    """Words plus CJK 2-grams and 3-grams, for scoring a candidate."""
    return _tokens(text, (2, 3))


def trigrams(text: str) -> list[str]:
    """CJK 3-grams only, used as a precision bonus over the 2-gram recall set."""
    normalized = normalize(text)
    out: list[str] = []
    for segment in _SEGMENT_RE.findall(normalized):
        for run, is_cjk in _split_runs(segment):
            if is_cjk and len(run) >= 3:
                out.extend(run[i:i + 3] for i in range(len(run) - 2))
    return out


def term_frequencies(text: str) -> Counter[str]:
    return Counter(index_terms(text))


__all__ = [
    "MIN_WORD_LENGTH",
    "index_terms",
    "normalize",
    "rank_terms",
    "term_frequencies",
    "trigrams",
]
