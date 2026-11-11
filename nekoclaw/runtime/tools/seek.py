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
#
# The four comparison levels and the punctuation table follow
# codex-rs/apply-patch/src/seek_sequence.rs of openai/codex
# (https://github.com/openai/codex, commit 87b7bdb, Apache License 2.0,
# Copyright 2025 OpenAI). This is a Python reimplementation of that algorithm,
# extended to report every match instead of the first.

"""Find a block of lines with decreasing strictness.

Used by ``edit`` when its literal search finds nothing. The levels, strictest
first:

1. ``exact``                  - lines equal.
2. ``trailing_whitespace``    - lines equal after removing trailing whitespace.
3. ``surrounding_whitespace`` - lines equal after removing leading and trailing
   whitespace.
4. ``unicode_punctuation``    - as 3, and typographic dashes, quotes and
   spaces compare equal to their ASCII forms.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

LEVELS: tuple[str, ...] = (
    "exact",
    "trailing_whitespace",
    "surrounding_whitespace",
    "unicode_punctuation",
)

LEVEL_DESCRIPTIONS: dict[str, str] = {
    "trailing_whitespace": "ignoring trailing whitespace",
    "surrounding_whitespace": "ignoring leading and trailing whitespace",
    "unicode_punctuation": (
        "ignoring leading and trailing whitespace and treating typographic dashes, quotes and "
        "spaces as their ASCII forms"
    ),
}

_DASHES = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)
_SINGLE_QUOTES = (0x2018, 0x2019, 0x201A, 0x201B)
_DOUBLE_QUOTES = (0x201C, 0x201D, 0x201E, 0x201F)
_SPACES = (
    0x00A0,
    0x2002,
    0x2003,
    0x2004,
    0x2005,
    0x2006,
    0x2007,
    0x2008,
    0x2009,
    0x200A,
    0x202F,
    0x205F,
    0x3000,
)

_PUNCTUATION_TABLE: dict[int, str] = {
    **{code: "-" for code in _DASHES},
    **{code: "'" for code in _SINGLE_QUOTES},
    **{code: '"' for code in _DOUBLE_QUOTES},
    **{code: " " for code in _SPACES},
}


def normalise_punctuation(text: str) -> str:
    """Strip the line and map typographic punctuation to ASCII."""
    return text.strip().translate(_PUNCTUATION_TABLE)


_KEYS: dict[str, Callable[[str], str]] = {
    "exact": lambda line: line,
    "trailing_whitespace": str.rstrip,
    "surrounding_whitespace": str.strip,
    "unicode_punctuation": normalise_punctuation,
}


def _matches_at(lines: Sequence[str], pattern: Sequence[str], start: int) -> bool:
    return all(lines[start + offset] == wanted for offset, wanted in enumerate(pattern))


def find_block(lines: Sequence[str], pattern: Sequence[str]) -> tuple[str, list[int]] | None:
    """Locate ``pattern`` in ``lines`` at the strictest level that matches.

    Returns the level name and the start index of every non-overlapping match
    at that level, or ``None`` when no level matches. An empty pattern or one
    longer than the input never matches.
    """
    if not pattern or len(pattern) > len(lines):
        return None
    last_start = len(lines) - len(pattern)
    for level in LEVELS:
        key = _KEYS[level]
        keyed_lines = [key(line) for line in lines]
        keyed_pattern = [key(line) for line in pattern]
        starts: list[int] = []
        index = 0
        while index <= last_start:
            if _matches_at(keyed_lines, keyed_pattern, index):
                starts.append(index)
                index += len(pattern)
            else:
                index += 1
        if starts:
            return level, starts
    return None
