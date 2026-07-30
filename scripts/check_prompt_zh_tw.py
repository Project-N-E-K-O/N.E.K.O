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

"""Require a 'zh-TW' key on newly added i18n prompt dicts (issue #2500).

A dict under config/prompts/ that has an 'en' key plus 'zh' or 'zh-CN' is a
localized prompt table. Most of them predate Traditional Chinese support, and
`_loc` falls back to 'en' rather than 'zh' on a missing key, so a zh-TW user
who reaches such a dict gets an English prompt.

Backfilling the existing tables is a batched effort tracked in issue #2500.
This gate only stops the hole from growing: it is a **diff ratchet** keyed on
the dict's own definition line, so it fires for dicts this change introduces
and stays quiet about the ones already in the tree. Editing an existing dict's
copy does not trip it.

Usage:
    python scripts/check_prompt_zh_tw.py [--base origin/main]
    python scripts/check_prompt_zh_tw.py --full     # report the whole backlog
    python scripts/check_prompt_zh_tw.py --count    # backlog size only

Escape hatch: put ``# noqa: PROMPT_ZH_TW`` on the dict's opening line.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "config" / "prompts"

CODE = "PROMPT_ZH_TW"

SIMPLIFIED_KEYS = ("zh", "zh-CN")
TRADITIONAL_KEY = "zh-TW"
ANCHOR_KEY = "en"


def _has_noqa(line: str) -> bool:
    return bool(re.search(rf"#\s*noqa:\s*{CODE}\b", line))


def _string_keys(node: ast.Dict) -> set[str]:
    return {
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def find_violations(
    tree: ast.Module,
    source_lines: list[str],
    changed_lines: set[int] | None = None,
) -> list[tuple[int, str]]:
    """Return (lineno, present-keys-summary) for localized dicts missing zh-TW.

    ``changed_lines`` is the ratchet: when given, only a dict whose own
    definition line is in the set is reported, so pre-existing tables are
    exempt and editing a table's copy does not trip the gate. Pass ``None`` to
    scan everything.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = _string_keys(node)
        if ANCHOR_KEY not in keys:
            continue
        if not any(k in keys for k in SIMPLIFIED_KEYS):
            continue
        if TRADITIONAL_KEY in keys:
            continue
        lineno = node.lineno
        if changed_lines is not None and lineno not in changed_lines:
            continue
        if 1 <= lineno <= len(source_lines) and _has_noqa(source_lines[lineno - 1]):
            continue
        present = ", ".join(sorted(k for k in keys if len(k) <= 6))
        out.append((lineno, present))
    return out


# ---------------------------------------------------------------------------
# git diff plumbing (mirrors scripts/check_docstring_no_cjk.py)
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(2)
    return result.stdout


def _changed_prompt_files(base: str) -> list[str]:
    out = _git("diff", "--name-only", f"{base}...HEAD")
    return [
        ln.strip().replace("\\", "/")
        for ln in out.splitlines()
        if ln.strip().startswith("config/prompts/") and ln.strip().endswith(".py")
    ]


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_lines(base: str, path: str) -> set[int]:
    """1-based line numbers in the NEW file touched by the diff."""
    diff = _git("diff", "--unified=0", f"{base}...HEAD", "--", path)
    lines: set[int] = set()
    for ln in diff.splitlines():
        m = _HUNK_HEADER_RE.match(ln)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        lines.update(range(start, start + count))
    return lines


def _parse(path: Path) -> tuple[ast.Module | None, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"{path}: cannot read ({exc})\n")
        return None, []
    try:
        return ast.parse(text), text.splitlines()
    except SyntaxError as exc:
        sys.stderr.write(f"{path}: syntax error ({exc})\n")
        return None, []


def _scan_all() -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in sorted(PROMPTS_DIR.glob("*.py")):
        tree, lines = _parse(path)
        if tree is None:
            continue
        for lineno, present in find_violations(tree, lines):
            found.append((path, lineno, present))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require 'zh-TW' on newly added localized prompt dicts "
            "(diff-ratchet against --base; --full scans everything)."
        )
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PROMPT_ZH_TW_BASE", "origin/main"),
        help="Base ref for the diff ratchet (default: origin/main).",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Scan every dict, not just newly added ones.",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the backlog size and exit 0.",
    )
    args = parser.parse_args(argv)

    if args.count:
        total = len(_scan_all())
        print(f"localized prompt dicts missing '{TRADITIONAL_KEY}': {total}")
        return 0

    if args.full:
        found = _scan_all()
        for path, lineno, present in found:
            rel = path.relative_to(REPO_ROOT).as_posix()
            print(f"{rel}:{lineno}: [{CODE}] missing '{TRADITIONAL_KEY}' (has: {present})")
        if found:
            print(f"\n{len(found)} localized prompt dict(s) missing '{TRADITIONAL_KEY}'.")
            print("This is the issue #2500 backlog; --full is informational.")
        return 1 if found else 0

    changed = _changed_prompt_files(args.base)
    if not changed:
        return 0

    violations: list[tuple[str, int, str]] = []
    for rel in changed:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        tree, lines = _parse(path)
        if tree is None:
            continue
        added = _added_lines(args.base, rel)
        for lineno, present in find_violations(tree, lines):
            if lineno in added:
                violations.append((rel, lineno, present))

    if not violations:
        return 0

    for rel, lineno, present in violations:
        print(f"{rel}:{lineno}: [{CODE}] new localized prompt dict is missing "
              f"'{TRADITIONAL_KEY}' (has: {present})")
    print(
        f"\n{len(violations)} newly added localized prompt dict(s) lack "
        f"'{TRADITIONAL_KEY}'.\n"
        "A prompt dict with 'en' + 'zh'/'zh-CN' needs 'zh-TW' too: _loc falls "
        "back to 'en', not 'zh', so Traditional Chinese users would get an "
        "English prompt. Add the template, or put '# noqa: PROMPT_ZH_TW' on the "
        "dict's opening line if it genuinely does not need one.\n"
        f"(Set $PROMPT_ZH_TW_BASE or pass --base to override the base ref.)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
