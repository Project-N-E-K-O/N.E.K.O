#!/usr/bin/env python3
"""Static check (PR-only): require change reports in the PR description.

Two project policies, both enforced against the pull request body:

REGRESSION_REPORT
    If the PR touches app/, main_logic/, or memory/ (any ``*.py`` file),
    the body must carry a non-empty "回归报告" section documenting the
    change, its rationale / necessity, before-and-after behaviour, and the
    potential regressions. These three are the project's highest-risk
    modules (session orchestration, the memory pipeline, the server entry
    points), so every code change to them must come with a written report.

NO_SPLIT_RATIONALE
    If the PR changes more than 20 files, the body must carry a non-empty
    "不拆分理由" section explaining why it is not split into smaller PRs.

The check only verifies that a substantive section EXISTS — it cannot judge
whether the report is any good. Report quality is the reviewer's job; the
companion .github/CODEOWNERS routes these paths to a maintainer so a human
gate sits behind this machine gate.

Escape hatch
------------
Apply the ``report-exempt`` label to the PR to skip both rules (pure
renames, bulk reformatting, generated code, etc.). The label set is read
from the ``PR_LABELS`` env var (comma-separated).

Inputs (all from env, set by the workflow):
    PR_BODY    — the pull request description (markdown)
    PR_LABELS  — comma-separated label names
Changed files come from ``git diff --name-only <base>...HEAD``.

Exit 1 on any violation, 0 otherwise (2 on git failure).

Usage:
    python scripts/check_pr_report.py [--base origin/main]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules whose every *.py change must ship a regression report.
WATCHED_PREFIXES = ("app/", "main_logic/", "memory/")
# A PR touching more than this many files must justify not splitting.
FILE_COUNT_LIMIT = 20
# Maintainer escape hatch.
EXEMPT_LABEL = "report-exempt"

REGRESSION_REPORT = "REGRESSION_REPORT"
NO_SPLIT_RATIONALE = "NO_SPLIT_RATIONALE"

# Section bodies equal to one of these (after stripping) count as "not filled":
# the author left a placeholder instead of writing the report.
_PLACEHOLDERS = {
    "", "不适用", "无", "暂无", "n/a", "na", "none", "-", "/", "tbd", "todo",
}

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADER_RE = re.compile(r"^#{1,6}\s")


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


def _changed_files(base: str) -> list[str]:
    """All files changed in HEAD relative to the merge-base with `base`.
    Posix-style repo-relative paths; deletions are included (a deletion is a
    change for the purpose of the file-count rule)."""
    out = _git("diff", "--name-only", f"{base}...HEAD")
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# PR-body parsing
# ---------------------------------------------------------------------------


def _section_body(body: str, keyword: str) -> str | None:
    """Text under the first heading whose title contains `keyword`, with HTML
    comments stripped. Returns None if no such heading exists (instructions
    live in <!-- --> comments, so they never count as content)."""
    text = _COMMENT_RE.sub("", body or "")
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if _HEADER_RE.match(ln) and keyword in ln:
            start = i + 1
            break
    if start is None:
        return None
    collected: list[str] = []
    for ln in lines[start:]:
        if _HEADER_RE.match(ln):
            break
        collected.append(ln)
    return "\n".join(collected).strip()


def _is_filled(section: str | None) -> bool:
    if section is None:
        return False
    return section.strip().lower() not in _PLACEHOLDERS


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require regression / no-split reports in the PR body."
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PR_REPORT_BASE", "origin/main"),
        help="Merge-base ref to diff against (default: origin/main).",
    )
    args = parser.parse_args()

    labels = {
        lbl.strip().lower()
        for lbl in os.environ.get("PR_LABELS", "").split(",")
        if lbl.strip()
    }
    if EXEMPT_LABEL in labels:
        print(f"[pr-report] '{EXEMPT_LABEL}' label present — skipping report checks.")
        return 0

    body = os.environ.get("PR_BODY", "")
    changed = _changed_files(args.base)
    watched = [
        f for f in changed
        if f.endswith(".py") and any(f.startswith(p) for p in WATCHED_PREFIXES)
    ]

    violations: list[tuple[str, str]] = []

    if watched and not _is_filled(_section_body(body, "回归报告")):
        sample = ", ".join(watched[:5]) + (" …" if len(watched) > 5 else "")
        violations.append((
            REGRESSION_REPORT,
            f"This PR changes {len(watched)} file(s) under app/ | main_logic/ | "
            f"memory/ ({sample}) but the PR body has no filled-in '回归报告' "
            f"section. Document the change, its rationale/necessity, "
            f"before-and-after behaviour, and potential regressions.",
        ))

    if len(changed) > FILE_COUNT_LIMIT and not _is_filled(
        _section_body(body, "不拆分理由")
    ):
        violations.append((
            NO_SPLIT_RATIONALE,
            f"This PR changes {len(changed)} files (> {FILE_COUNT_LIMIT}) but the "
            f"PR body has no filled-in '不拆分理由' section. Explain why this is "
            f"not split into smaller PRs.",
        ))

    if not violations:
        print(
            f"[pr-report] OK — {len(changed)} file(s) changed, "
            f"{len(watched)} under watched modules."
        )
        return 0

    for code, msg in violations:
        sys.stderr.write(f"{code}  {msg}\n\n")
    sys.stderr.write(
        f"Fill in the missing section(s) in the PR description (template: "
        f".github/pull_request_template.md), or apply the '{EXEMPT_LABEL}' "
        f"label if a maintainer agrees the PR is exempt.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
