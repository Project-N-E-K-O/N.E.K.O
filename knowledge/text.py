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
_ROLE_MARKER_RE = re.compile(
    r"(?im)^[ \t]*(?:system|developer|assistant|user|human)[ \t]*[:：]"
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
)
_TOKEN_RE = re.compile(rf"[{_CJK_RANGES}]+|[^\W_{_CJK_RANGES}]+")
_CJK_RUN_RE = re.compile(rf"^[{_CJK_RANGES}]+$")
MAX_QUERY_TOKENS = 64


_UNICODE_LINE_BREAKS = str.maketrans({"\u0085": "\n", "\u2028": "\n", "\u2029": "\n"})


def strip_chat_markup(value: str) -> str:
    """Remove chat-control tokens and role markers until a fixed point.

    Unicode line breaks (NEL, LS, PS) become ``\n`` first: a role marker after
    one of them starts a line for a reader, but not for ``^`` in a regex.
    """
    text = str(value or "").translate(_UNICODE_LINE_BREAKS)
    while True:
        cleaned = _CHAT_TOKEN_RE.sub("", text)
        cleaned = _ROLE_MARKER_RE.sub("", cleaned)
        if cleaned == text:
            return cleaned
        text = cleaned


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


def fold_surface(value: object) -> str:
    """Comparison form for exact title / alias / recognition matching."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in _strip_marks(normalized) if ch.isalnum())


def _strip_marks(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def search_tokens(value: object) -> list[str]:
    """Split text into the units stored in and queried against the FTS index.

    CJK runs become overlapping character bigrams (a single character stays a
    unigram); other word runs stay whole. Latin text is case-folded and loses
    its diacritics, because ``unicode61`` matches it that way too and both
    sides must agree on what a token is.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    tokens: list[str] = []
    for run in _TOKEN_RE.findall(text):
        if _CJK_RUN_RE.match(run):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
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
    unique = list(dict.fromkeys(search_tokens(value)))[:MAX_QUERY_TOKENS]
    return " OR ".join(f'"{token}"' for token in unique if '"' not in token)
