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

"""A `======...======` block must open and close in the same wording family.

Project convention is below/above duality: `以下为X` closes with `以上为X`, never
with `以上是X`, and an English `Below is X` never closes with a Chinese footer.
The drift is invisible in review because opener and closer sit dozens of lines
apart inside one template, so it is checked mechanically instead.

Scope is deliberately the pairs that live inside a **single string constant**,
where opener and closer are unambiguously the two ends of one block. Tables that
put the opener in a header dict and the closer in a separate footer dict are not
covered here: nothing in the source says which header goes with which footer, so
pairing them would be a guess. Those are the `_INJECTION_*` header/footer dicts
in prompts_proactive.py.

Only the wording *family* is asserted, not the name after it. Several blocks
deliberately re-word the closer (`以下为近期搭话记录（你应该避免雷同…）` closes with
`以上为近期搭话记录（不可重复…）`, re-stating the constraint at both ends), so a
name-equality assertion would fight the prompt design rather than catch drift.

One closer is exempt: see `_WATERMARK_CLOSERS`.

The markers quoted above are the literals under test, so this docstring cannot
be written without them.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "config" / "prompts"

_MARKER = re.compile(r"======([^=\n]+)======")

# (opener, closer) per wording family; index identity is what the test asserts.
_FAMILIES = [
    ("以下为", "以上为"),
    ("以下是", "以上是"),
    ("以下為", "以上為"),
    ("以下は", "以上は"),
    ("Below is", "Above is"),
    ("아래는", "위는"),
    ("이하", "이상"),
    ("Ниже", "Выше"),
    ("Abajo están", "Arriba están"),
    ("Abajo está", "Arriba está"),
    ("Abaixo estão", "Acima estão"),
    ("Abaixo está", "Acima está"),
]
# Longest prefix first so "Abajo está" never shadows "Abajo están".
_OPENERS = sorted(
    ((p, i) for i, (p, _) in enumerate(_FAMILIES)), key=lambda x: -len(x[0])
)
_CLOSERS = sorted(
    ((p, i) for i, (_, p) in enumerate(_FAMILIES)), key=lambda x: -len(x[0])
)


# CAT_GREETING_* keeps a localized opener but closes every locale with one fixed
# Simplified string, on purpose: #2376 ("embed watermark in prompt templates")
# replaced the per-locale closers with it so the marker the model must not emit
# is byte-identical everywhere. That asymmetry is the design, not drift, and
# test_proactive_text_does_not_dehumanize.py pins it. The zh row still has to
# open with 以下为 so at least that locale reads as a pair, which is why the
# exemption is on the closer rather than on the whole table.
_WATERMARK_CLOSERS = frozenset({"以上为环境提示"})


def _classify(body: str) -> tuple[str, int] | None:
    """('open'|'close', family index) for a marker body, or None if neither.

    Plain section labels ("Reply Format" and friends) open nothing and close
    nothing, so they drop out here rather than being paired with anything.
    """
    for prefix, family in _OPENERS:
        if body.startswith(prefix):
            return "open", family
    for prefix, family in _CLOSERS:
        if body.startswith(prefix):
            return "close", family
    return None


def _pairs_in(text: str):
    """Yield (opener_body, closer_body, same_family) for markers in one string.

    Markers nest as a stack, so a template that frames conversation history and
    then screen content yields both pairs. A closer with no opener in the same
    string belongs to a header/footer dict split and is skipped.
    """
    stack: list[tuple[str, int]] = []
    for match in _MARKER.finditer(text):
        body = match.group(1).strip()
        kind = _classify(body)
        if kind is None:
            continue
        role, family = kind
        if role == "open":
            stack.append((body, family))
        elif stack:
            opener_body, opener_family = stack.pop()
            if body in _WATERMARK_CLOSERS:
                continue
            yield opener_body, body, opener_family == family


def _prompt_sources():
    for path in sorted(_PROMPTS_DIR.glob("prompts_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path, node.lineno, node.value


def test_marker_blocks_open_and_close_in_the_same_family():
    offenders = []
    for path, lineno, text in _prompt_sources():
        for opener, closer, same in _pairs_in(text):
            if not same:
                rel = path.relative_to(_REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: ======{opener}====== ... ======{closer}======")
    assert not offenders, (
        "marker block opens and closes in different wording families "
        "(see _FAMILIES for the allowed pairings):\n" + "\n".join(sorted(offenders))
    )


def test_guard_sees_the_markers_it_claims_to_check():
    """A silent zero here would make the assertion above vacuous."""
    checked = sum(1 for _p, _l, t in _prompt_sources() for _ in _pairs_in(t))
    assert checked > 200, f"only {checked} marker pairs discovered"
