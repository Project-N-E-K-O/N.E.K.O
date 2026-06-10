#!/usr/bin/env python3
"""PR-only check: frontend i18n locale files must be modified in lockstep.

Two groups, one rule each. Diff is computed against the merge-base of HEAD
and the base ref (default ``origin/main``). On a regular ``main`` push or
when nothing in the watched folders changed, this script is a no-op.

Group 1 — static/locales/*.json
   When ANY file in static/locales/ changes, ALL 8 language files must
   change AND every hunk header (``@@ -OLD,COUNT +NEW,COUNT @@``) must
   match across all 8. The locale JSONs are line-aligned by convention
   (the same key sits on the same line across languages); strict alignment
   catches "added a key in zh-CN but forgot the others" at the diff stage.

Group 2 — frontend/plugin-manager/src/i18n/locales/*.ts
   Same rule but for plugin-manager TS locales. ``yuiGuide.ts`` is
   excluded from the group because it's a domain-scoped guide that
   intentionally ships single-language only.

Suppression
-----------
None at the line level. If a structural reorganisation legitimately needs
to break alignment, edit the script's GROUPS definition in the same PR
and explain in the description.

Output
------
Each violation prints a short line. Exit 1 on any, 0 otherwise.

Usage:
    python scripts/check_i18n_sync.py [--base origin/main]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GROUPS: list[dict[str, object]] = [
    {
        "name": "static/locales",
        "dir": "static/locales",
        "files": [
            "en.json", "es.json", "ja.json", "ko.json",
            "pt.json", "ru.json", "zh-CN.json", "zh-TW.json",
        ],
    },
    {
        "name": "plugin-manager i18n locales",
        "dir": "frontend/plugin-manager/src/i18n/locales",
        "files": [
            "en-US.ts", "es.ts", "ja.ts", "ko.ts",
            "pt.ts", "ru.ts", "zh-CN.ts", "zh-TW.ts",
        ],
        # yuiGuide.ts intentionally excluded — domain-scoped single-language guide.
    },
]


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(2)
    return result.stdout


def _changed_files(base: str) -> set[str]:
    """Files changed in HEAD relative to merge-base with `base`. Posix-style."""
    out = _git("diff", "--name-only", f"{base}...HEAD")
    return {ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()}


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)

def _parse_hunks(diff_text: str) -> list[tuple[int, int, int, int]]:
    """Parse a unified diff. Returns list of (old_start, old_count, new_start, new_count).

    Implicit count is ``1`` per the unified-diff spec (matching ``git diff``)."""
    out: list[tuple[int, int, int, int]] = []
    for line in diff_text.splitlines():
        m = _HUNK_HEADER_RE.match(line)
        if m:
            os_, oc, ns, nc = m.groups()
            out.append((
                int(os_),
                int(oc) if oc is not None else 1,
                int(ns),
                int(nc) if nc is not None else 1,
            ))
    return out


def _file_diff(base: str, path: str) -> str:
    return _git("diff", "--unified=0", f"{base}...HEAD", "--", path)


def _check_group(group: dict, base: str, changed: set[str]) -> list[str]:
    errors: list[str] = []
    dir_prefix = str(group["dir"]).rstrip("/") + "/"
    files: list[str] = list(group["files"])  # type: ignore[assignment]
    files_in_group = [dir_prefix + name for name in files]
    touched = [f for f in files_in_group if f in changed]

    if not touched:
        return errors  # nothing in the group changed → quiet

    missing = [f for f in files_in_group if f not in touched]
    if missing:
        errors.append(
            f"[{group['name']}] modified {len(touched)}/{len(files_in_group)} "
            f"locale file(s); missing changes in: {', '.join(missing)}. "
            f"All locales in the group must be edited together."
        )
        return errors  # alignment check is meaningless when files are missing

    hunks_by_file: dict[str, list[tuple[int, int, int, int]]] = {}
    for f in files_in_group:
        diff = _file_diff(base, f)
        hunks_by_file[f] = sorted(_parse_hunks(diff))

    ref_file = files_in_group[0]
    ref_hunks = hunks_by_file[ref_file]
    for f in files_in_group[1:]:
        if hunks_by_file[f] != ref_hunks:
            errors.append(
                f"[{group['name']}] hunk ranges in {f} differ from "
                f"{ref_file}; locale changes must touch the same line "
                f"ranges across all languages.\n"
                f"  reference ({ref_file}): {ref_hunks}\n"
                f"  this file ({f}): {hunks_by_file[f]}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify all locale files in a group change in lockstep "
                    "(same set of hunk ranges, no language left behind).",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("I18N_SYNC_BASE", "origin/main"),
        help="Base ref to diff against (default: origin/main, "
             "override via $I18N_SYNC_BASE).",
    )
    args = parser.parse_args(argv)

    # If base ref doesn't exist, the whole check is moot — skip with a warning.
    rev_check = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", args.base],
        cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    if rev_check.returncode != 0:
        print(
            f"i18n-sync: base ref `{args.base}` not found; skipping. "
            f"(Set $I18N_SYNC_BASE or pass --base to override.)",
            file=sys.stderr,
        )
        return 0

    changed = _changed_files(args.base)
    all_errors: list[str] = []
    for group in GROUPS:
        all_errors.extend(_check_group(group, args.base, changed))

    if all_errors:
        for e in all_errors:
            print(e)
        print(
            f"\n{len(all_errors)} i18n-sync violation(s) found.\n"
            "Locale files within each group must move together — same set of "
            "line ranges modified across all languages.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
