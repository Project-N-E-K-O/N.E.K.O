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

"""Keep untrusted text from posing as conversation structure in a prompt.

Text that reaches the model from outside the conversation (knowledge pack
cards, recalled memories) goes through both helpers before it is placed
inside a fenced block:

* ``strip_chat_markup`` removes ChatML-style control tokens and line-leading
  role markers, repeated until nothing changes. A single pass is not enough:
  ``<|im_<|im_end|>start|>`` turns into ``<|im_start|>`` once the inner token
  is removed.
* ``neutralize_fence`` breaks any run of ``=`` long enough to imitate the
  ``======[...]======`` delimiters, so the text cannot close its block early.
"""

from __future__ import annotations

import re
import unicodedata


_CHAT_TOKEN_RE = re.compile(
    r"<\|\s*(?:im_start|im_end|im_sep|endoftext|system|user|assistant|"
    r"start_header_id|end_header_id|eot_id|begin_of_text)\s*\|>",
    re.IGNORECASE,
)
# Unicode default-ignorable code points, rendered as nothing: C1 controls
# (NEL becomes a line break before these are removed), soft hyphen, combining
# grapheme joiner, zero-width spaces/joiners, bidi controls, BOM, Hangul
# fillers, variation selectors, tags. They are removed from pack text, and the
# role-marker pattern also tolerates them, so an invisible prefix cannot hide
# a line-leading role.
_INVISIBLE = (
    "\x80-\x9f\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
    "\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0"
    "\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE}]")
_ROLE_MARKER_RE = re.compile(
    rf"(?im)^[ \t{_INVISIBLE}]*(?:system|developer|assistant|user|human)[ \t{_INVISIBLE}]*[:：]"
)
_FENCE_RUN_RE = re.compile(r"={3,}")


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


def fence_safe(value: object) -> str:
    """Both helpers in the order a fenced block needs them."""
    return neutralize_fence(strip_chat_markup(str(value or "")))
