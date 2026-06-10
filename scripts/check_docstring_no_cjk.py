#!/usr/bin/env python3
"""Static check: new/modified Python docstrings must be English-only (no CJK).

DOCSTRING_CJK
   Any module / class / function docstring containing CJK characters
   (CJK Unified Ideographs, Hiragana, Katakana, Hangul Syllables) is
   flagged. Comments and non-docstring strings are out of scope — those
   are covered by other conventions (prompt hygiene, i18n-in-config).

Diff-ratchet enforcement
------------------------
The repo carries thousands of legacy CJK docstrings, so full enforcement
would light up every file at once. Instead the check is diff-based: in the
default mode it only flags docstrings whose line span overlaps lines
added/modified in HEAD relative to the merge-base with ``--base``
(default ``origin/main``). Touch a docstring → rewrite it in English.
The legacy stock converges file by file as code gets edited.

``--full`` scans every docstring regardless of the diff — useful for
migration sweeps and stats, not wired into CI.

Note: diff mode reads file contents from the working tree but line ranges
from ``git diff <base>...HEAD``; it assumes the working tree matches HEAD,
which is always true in CI (and after ``git commit`` locally).

Suppression
-----------
Append ``# noqa: DOCSTRING_CJK`` to any line spanned by the docstring
(start line through end line). Bare ``# noqa`` also matches. Use sparingly
— e.g. test fixtures whose CJK content is itself the thing under test.

Output
------
Every violation prints as ``path:line:col  DOCSTRING_CJK  message``.
Exit 1 on any violation, 0 otherwise (2 on git failure).

Usage:
    python scripts/check_docstring_no_cjk.py [--base origin/main]
    python scripts/check_docstring_no_cjk.py --full [paths...]
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Junk dirs plus two deliberate policy exemptions:
#   plugin/       — plugin internals follow their own conventions (same
#                   rationale as check_prompt_hygiene.py / the review-scope
#                   convention: the framework doesn't police plugin bodies).
#   local_server/ — subprocess-spawned TTS / telemetry servers, maintained
#                   as a semi-independent unit.
# Unlike check_prompt_hygiene.py, config/ and tests/ ARE covered: the rule
# is about the language of documentation, which applies to them the same.
EXCLUDE_DIRS = {
    ".venv", "venv",
    ".git", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", "node_modules",
    ".claude",
    "plugin", "local_server",
}

# CJK ranges: CJK Unified, Hiragana, Katakana, Hangul Syllables.
# Kept in sync with scripts/check_prompt_hygiene.py.
CJK_RANGES = (
    ("一", "鿿"),
    ("぀", "ゟ"),
    ("゠", "ヿ"),
    ("가", "힣"),
)

CODE = "DOCSTRING_CJK"


def _first_cjk(s: str) -> str | None:
    """Return the first CJK character in `s`, or None."""
    for ch in s:
        for lo, hi in CJK_RANGES:
            if lo <= ch <= hi:
                return ch
    return None


def _has_noqa(line: str, code: str) -> bool:
    """True if `line` contains `# noqa` (bare) or `# noqa: ...,CODE,...`.

    Tolerates a trailing explanatory comment after the noqa, but it must
    start with ``#`` (``# noqa: CODE  # rationale``) — the codes block
    stops only at the next ``#`` or end-of-line, so other separators like
    ``— rationale`` break the match. Same behaviour as
    scripts/check_prompt_hygiene.py (and ruff/flake8)."""
    m = re.search(r"#\s*noqa\b(?:\s*:\s*([A-Za-z0-9_,\s]+?))?(?=#|$)", line)
    if not m:
        return False
    raw = m.group(1)
    if raw is None or not raw.strip():
        return True
    codes = {c.strip() for c in raw.split(",") if c.strip()}
    return code in codes


def _docstring_nodes(tree: ast.Module) -> Iterator[tuple[ast.AST, ast.Constant]]:
    """Yield (owner, docstring Constant node) for every module / class /
    function docstring in the tree. We need the node (not just the text from
    ``ast.get_docstring``) to know its line span."""
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield node, first.value


def find_violations(
    tree: ast.Module,
    source_lines: list[str],
    changed_lines: set[int] | None,
) -> list[tuple[int, int, str]]:
    """Return (lineno, col, message) for each CJK docstring.

    ``changed_lines`` is the set of 1-based line numbers added/modified in
    the diff; a docstring is only flagged if its span intersects the set.
    ``None`` means full-scan mode (every docstring is in scope).
    """
    violations: list[tuple[int, int, str]] = []
    for owner, doc in _docstring_nodes(tree):
        text = doc.value
        ch = _first_cjk(text)
        if ch is None:
            continue
        start = doc.lineno
        end = doc.end_lineno or start
        if changed_lines is not None and not any(
            ln in changed_lines for ln in range(start, end + 1)
        ):
            continue
        last = min(end, len(source_lines))
        if any(
            _has_noqa(source_lines[ln - 1], CODE) for ln in range(start, last + 1)
        ):
            continue
        owner_name = getattr(owner, "name", None) or "<module>"
        violations.append((
            start,
            (doc.col_offset or 0) + 1,
            f"docstring of {owner_name} contains CJK characters "
            f"(first: '{ch}'); write docstrings in English, or append "
            f"`# noqa: {CODE}` if the CJK content is intentional.",
        ))
    return violations


# ---------------------------------------------------------------------------
# git diff plumbing (mirrors scripts/check_i18n_sync.py)
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


def _changed_py_files(base: str) -> list[str]:
    """Python files changed in HEAD relative to merge-base with `base`.
    Posix-style repo-relative paths; deletions drop out naturally because
    the path no longer exists on disk (filtered by the caller)."""
    out = _git("diff", "--name-only", f"{base}...HEAD")
    return [
        ln.strip().replace("\\", "/")
        for ln in out.splitlines()
        if ln.strip().endswith(".py")
    ]


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_lines(base: str, path: str) -> set[int]:
    """1-based line numbers in the NEW file touched by the diff.
    Implicit count is ``1`` per the unified-diff spec (matching ``git diff``);
    pure-deletion hunks have count 0 and contribute nothing."""
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


# ---------------------------------------------------------------------------
# File iteration / parsing
# ---------------------------------------------------------------------------


def _is_excluded(path: Path) -> bool:
    try:
        rel_parts = path.relative_to(REPO_ROOT).parts
    except ValueError:
        rel_parts = path.parts
    return bool(set(rel_parts) & EXCLUDE_DIRS)


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if p.suffix == ".py" and not _is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if not _is_excluded(f):
                    yield f


def _parse_file(path: Path) -> tuple[ast.Module | None, list[str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None, []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None, []
    return tree, source.splitlines()


def _report(path: Path, violations: list[tuple[int, int, str]]) -> int:
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = path
    for lineno, col, msg in violations:
        print(f"{rel.as_posix()}:{lineno}:{col}  {CODE}  {msg}")
    return len(violations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Forbid CJK characters in new/modified Python docstrings "
            "(diff-ratchet against --base; --full scans everything)."
        )
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("DOCSTRING_CJK_BASE", "origin/main"),
        help="Base ref to diff against (default: origin/main, "
             "override via $DOCSTRING_CJK_BASE).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Scan ALL docstrings instead of only diff-touched ones "
             "(migration aid; the legacy stock is large).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan in --full mode (default: repo root).",
    )
    args = parser.parse_args(argv)

    total = 0

    if args.full:
        raw = args.paths or ["."]
        targets = [
            Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw
        ]
        for file in _iter_python_files(targets):
            tree, lines = _parse_file(file)
            if tree is None:
                continue
            total += _report(file, find_violations(tree, lines, None))
    else:
        # If base ref doesn't exist the whole check is moot — skip with a
        # warning (e.g. shallow local clone without origin/main).
        rev_check = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", args.base],
            cwd=REPO_ROOT,
            capture_output=True, text=True, check=False,
        )
        if rev_check.returncode != 0:
            print(
                f"docstring-cjk: base ref `{args.base}` not found; skipping. "
                f"(Set $DOCSTRING_CJK_BASE or pass --base to override.)",
                file=sys.stderr,
            )
            return 0
        for rel in _changed_py_files(args.base):
            file = REPO_ROOT / rel
            if not file.is_file() or _is_excluded(file):
                continue
            changed = _added_lines(args.base, rel)
            if not changed:
                continue
            tree, lines = _parse_file(file)
            if tree is None:
                continue
            total += _report(file, find_violations(tree, lines, changed))

    if total:
        print(
            f"\n{total} docstring-language violation(s) found.\n"
            "Docstrings touched by this change must be written in English. "
            f"To override a single docstring, append `# noqa: {CODE}` to one "
            "of its lines.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
