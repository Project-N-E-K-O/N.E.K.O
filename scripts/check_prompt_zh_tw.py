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

"""Stop new i18n prompt dicts from landing without a 'zh-TW' key (issue #2500).

A dict under config/prompts/ that has an 'en' key plus 'zh' or 'zh-CN' is a
localized prompt table. Most of them predate Traditional Chinese support, and
`_loc` falls back to 'en' rather than 'zh' on a missing key, so a zh-TW user who
reaches such a dict gets an English prompt.

Backfilling the existing tables is a batched effort tracked in issue #2500. This
gate only stops the hole from growing.

How the ratchet works
=====================
It compares the **multiset of key signatures** of offending dicts between the
merge-base and HEAD, rather than asking which source lines the diff touched.
A dict contributes ``frozenset(its string keys)``; HEAD needing more copies of
some signature than the base had is what fails the check.

Line-based ratcheting was tried first and is wrong in both directions:

  * A pre-existing ``{'en': ..., 'ja': ...}`` table that a PR turns into a
    localized one by adding a single ``'zh'`` line never has its own definition
    line in the diff, so the gate this is meant to be would miss exactly the
    case it exists for.
  * Renaming a prompt module with no content change makes every line of the new
    path count as added, so a rename would report the whole file's existing
    backlog.

Signatures dodge both: adding a Chinese key changes a dict's signature (and so
increments a count), while renaming a file or editing a template's copy leaves
every signature untouched.

The tradeoff is that a PR which removes one offending table and adds another
with the identical key set nets to zero and passes. That is a deliberate
accept: the alternative is matching dicts across revisions by position, which
breaks on every reformat.

Usage:
    python scripts/check_prompt_zh_tw.py [--base origin/main]
    python scripts/check_prompt_zh_tw.py --full     # list the whole backlog
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
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_SUBDIR = "config/prompts"
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
    tree: ast.Module, source_lines: list[str]
) -> list[tuple[int, frozenset[str]]]:
    """Return (lineno, key-signature) for localized dicts with no zh-TW key."""
    out: list[tuple[int, frozenset[str]]] = []
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
        if 1 <= lineno <= len(source_lines) and _has_noqa(source_lines[lineno - 1]):
            continue
        out.append((lineno, frozenset(keys)))
    return out


def _parse_source(source: str, origin: str) -> tuple[ast.Module | None, list[str]]:
    try:
        return ast.parse(source), source.splitlines()
    except SyntaxError as exc:
        sys.stderr.write(f"{origin}: syntax error ({exc})\n")
        return None, []


def signature_counter(sources: dict[str, str]) -> Counter[frozenset[str]]:
    """Count offending key signatures across a {path: source} mapping."""
    counter: Counter[frozenset[str]] = Counter()
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        for _lineno, signature in find_violations(tree, lines):
            counter[signature] += 1
    return counter


def locate(
    sources: dict[str, str],
    signature: frozenset[str],
    touched: dict[str, set[int]] | None = None,
) -> tuple[list[str], int]:
    """Locate offending dicts matching `signature`.

    Returns (likely, other_count). A dict counts as *likely* when `touched` says
    the diff added a line inside its body, which is only a presentation hint:
    the pass/fail decision is the signature comparison, because line spans alone
    both miss added-key cases and misfire on renames. Common key sets like
    {en, zh} match dozens of pre-existing tables, so surfacing the touched ones
    is what makes the failure actionable.
    """
    likely: list[str] = []
    other = 0
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        by_line = {node.lineno: node for node in ast.walk(tree)
                   if isinstance(node, ast.Dict)}
        for lineno, sig in find_violations(tree, lines):
            if sig != signature:
                continue
            node = by_line.get(lineno)
            span = range(lineno, (getattr(node, "end_lineno", lineno) or lineno) + 1)
            added = (touched or {}).get(path, set())
            if added and any(ln in added for ln in span):
                likely.append(f"{path}:{lineno}")
            else:
                other += 1
    return likely, other


# ---------------------------------------------------------------------------
# git plumbing (mirrors scripts/check_docstring_no_cjk.py)
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


def _merge_base(base: str) -> str:
    return _git("merge-base", base, "HEAD").strip()


def _prompt_files_at(rev: str) -> list[str]:
    """Prompt modules present at `rev`, including any in subpackages."""
    out = _git("ls-tree", "-r", "--name-only", rev, "--", PROMPTS_SUBDIR)
    return [
        ln.strip().replace("\\", "/")
        for ln in out.splitlines()
        if ln.strip().endswith(".py")
    ]


def _sources_at(rev: str) -> dict[str, str]:
    return {path: _git("show", f"{rev}:{path}") for path in _prompt_files_at(rev)}


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _touched_lines(rev: str) -> dict[str, set[int]]:
    """Lines the diff added per prompt file — a hint for error messages only.

    ``-M`` keeps a pure rename from reporting every line of the new path as
    added, which would make the hint point at the whole file.
    """
    diff = _git("diff", "-M", "--unified=0", f"{rev}...HEAD", "--", PROMPTS_SUBDIR)
    touched: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip().replace("\\", "/")
            continue
        match = _HUNK_HEADER_RE.match(line)
        if not match or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        touched.setdefault(current, set()).update(range(start, start + count))
    return touched


def _sources_on_disk() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            sources[rel] = path.read_text(encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"{rel}: cannot read ({exc})\n")
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require 'zh-TW' on newly added localized prompt dicts "
            "(signature ratchet against --base; --full scans everything)."
        )
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PROMPT_ZH_TW_BASE", "origin/main"),
        help="Base ref for the ratchet (default: origin/main).",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="List every offending dict, not just newly added ones.",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the backlog size and exit 0.",
    )
    args = parser.parse_args(argv)

    disk = _sources_on_disk()

    if args.count:
        total = sum(signature_counter(disk).values())
        print(f"localized prompt dicts missing '{TRADITIONAL_KEY}': {total}")
        return 0

    if args.full:
        found = 0
        for path, source in sorted(disk.items()):
            tree, lines = _parse_source(source, path)
            if tree is None:
                continue
            for lineno, signature in find_violations(tree, lines):
                present = ", ".join(sorted(k for k in signature if len(k) <= 6))
                print(f"{path}:{lineno}: [{CODE}] missing "
                      f"'{TRADITIONAL_KEY}' (has: {present})")
                found += 1
        if found:
            print(f"\n{found} localized prompt dict(s) missing '{TRADITIONAL_KEY}'.")
            print("This is the issue #2500 backlog; --full is informational.")
        return 1 if found else 0

    merge_base = _merge_base(args.base)
    base_counter = signature_counter(_sources_at(merge_base))
    head_counter = signature_counter(disk)

    added = head_counter - base_counter
    if not added:
        return 0

    touched = _touched_lines(merge_base)
    total = sum(added.values())
    for signature, count in sorted(added.items(), key=lambda kv: sorted(kv[0])):
        present = ", ".join(sorted(k for k in signature if len(k) <= 6))
        likely, other = locate(disk, signature, touched)
        print(f"[{CODE}] {count} new localized prompt dict(s) with keys "
              f"({present}) and no '{TRADITIONAL_KEY}'.")
        if likely:
            print(f"        in lines this change touched: {', '.join(likely)}")
        if other:
            print(f"        ({other} pre-existing table(s) share this key set "
                  f"and are exempt)")
        if not likely:
            print("        no touched table matches — the new table may have "
                  "moved in from elsewhere; run --full to see every offender")
    print(
        f"\n{total} newly added localized prompt dict(s) lack "
        f"'{TRADITIONAL_KEY}'.\n"
        "A prompt dict with 'en' + 'zh'/'zh-CN' needs 'zh-TW' too: _loc falls "
        "back to 'en', not 'zh', so Traditional Chinese users would get an "
        "English prompt. Add the template, or put '# noqa: PROMPT_ZH_TW' on the "
        "dict's opening line if it genuinely does not need one.\n"
        "The ratchet counts key signatures rather than source lines, so the "
        "locations above are narrowed to the tables this change touched.\n"
        f"(Set $PROMPT_ZH_TW_BASE or pass --base to override the base ref.)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
