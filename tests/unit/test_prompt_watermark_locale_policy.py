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

"""The `======...======` frames come in two kinds, and zh-TW must follow suit.

A frame is a **watermark** when every locale spells it with the same Chinese
string: it is a literal boundary marker the model is meant to see identically in
every language, not prose. Translating it into Traditional for the zh-TW row
alone silently breaks that "one marker, all locales" property.

A frame is **prose** when ja/ko/ru carry their own translation of it. There the
zh-TW row must be Traditional like everything else in that row, otherwise a
Traditional user reads Simplified text inside an otherwise-Traditional prompt.

Both directions were violated while backfilling issue #2500 -- two watermarks
were translated and six prose frames were left Simplified -- and both are
mechanically checkable, which is what this module does. The classification is
per marker SLOT, not per dict: ACTIVITY_GUESS_PROMPTS alone carries one
watermark pair and two prose pairs.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "config" / "prompts"

_MARKER = re.compile(r"={4,}[^=\n]*={4,}")
_CJK = re.compile(r"[一-鿿]")
_OTHER_LOCALES = ("en", "ja", "ko", "ru", "es", "pt")


def _strings(node: ast.AST) -> list[str]:
    """Every string constant under a locale's value, in source order."""
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, (ast.Dict, ast.Tuple, ast.List, ast.Set)):
        children = node.values if isinstance(node, ast.Dict) else node.elts
        for child in children:
            out.extend(_strings(child))
    return out


def _slots(node: ast.AST) -> list[str]:
    return [m.group(0) for s in _strings(node) for m in _MARKER.finditer(s)]


def _localized_tables():
    """Yield (path, lineno, slot_index, zh, zh_tw, sibling_spellings).

    Slots are compared BY POSITION, which is only meaningful when every row
    carries the same number of markers. Some tables legitimately differ -- a
    locale whose template inlines an extra bracketed section, for instance --
    and there slot *i* is a different logical frame per row. Comparing those
    reports nonsense: at prompts_memory.py:411 it pairs the Chinese rows'
    "contents" frame against the other locales' "dialogue" frame and calls the
    mismatch a violation. A table whose rows disagree on marker count is
    therefore skipped rather than guessed at.
    """
    for path in sorted(_PROMPTS_DIR.glob("prompts_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            rows = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if "zh-TW" not in rows:
                continue
            tw = _slots(rows["zh-TW"])
            if not tw:
                continue
            zh = _slots(rows["zh"]) if "zh" in rows else []
            sib = {
                loc: _slots(rows[loc])
                for loc in _OTHER_LOCALES
                if loc in rows and _slots(rows[loc])
            }
            aligned = {loc: v for loc, v in sib.items() if len(v) == len(tw)}
            if len(aligned) < 2:
                continue
            if zh and len(zh) != len(tw):
                zh = []
            for index, mine in enumerate(tw):
                yield (
                    path.relative_to(_REPO_ROOT).as_posix(),
                    node.lineno,
                    index,
                    zh[index] if len(zh) > index else None,
                    mine,
                    [v[index] for v in aligned.values()],
                )


def test_zh_tw_copies_every_cross_locale_watermark_verbatim():
    """A frame every other locale spells identically is a marker, not prose.

    Only fires when the zh-TW row broke ranks **on its own** -- that is, when it
    differs from zh. Where zh and zh-TW agree but both differ from the other six
    (at prompts_memory.py:411 both Chinese rows name the block "contents" while
    every other locale names it "dialogue"), the divergence predates Traditional
    support and is the Chinese rows' business, not a zh-TW translation mistake.
    Flagging it here would push a template-only change into rewriting live
    memory prompts.
    """
    offenders = []
    for path, line, index, zh, mine, spellings in _localized_tables():
        unanimous = len(set(spellings)) == 1
        if not (unanimous and _CJK.search(spellings[0])):
            continue
        if mine == spellings[0]:
            continue
        if zh is not None and mine == zh:
            continue
        offenders.append(f"{path}:{line} slot#{index}: zh-TW={mine!r} != watermark={spellings[0]!r}")
    assert not offenders, (
        "zh-TW translated a cross-locale watermark; copy it byte for byte:\n"
        + "\n".join(offenders)
    )


def test_zh_tw_does_not_leave_prose_frames_in_simplified():
    """A frame ja/ko/ru each translate is prose; zh-TW must not clone the zh row."""
    offenders = []
    for path, line, index, zh, mine, spellings in _localized_tables():
        if len(set(spellings)) == 1:
            continue  # watermark — the sibling test owns this slot
        if zh is None or mine != zh:
            continue
        # Only meaningful when the two scripts would actually differ: a frame
        # whose characters are identical in both (e.g. 上下文) is not evidence of
        # a missed translation. None exist today; the guard stays honest anyway.
        if not _CJK.search(mine):
            continue
        offenders.append(f"{path}:{line} slot#{index}: zh-TW copies zh verbatim ({mine!r}) while siblings translate: {sorted(set(spellings))[:3]}")
    assert not offenders, (
        "zh-TW left a per-locale frame in Simplified:\n" + "\n".join(offenders)
    )
