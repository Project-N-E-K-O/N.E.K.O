#!/usr/bin/env python3
"""Commit and push plugin driver changes to the Market git repo (nested .git).

Run from the N.E.K.O repository root after the monorepo commit is done:

    uv run python tests/testbench_dist/scripts/sync_plugin_repo.py
    uv run python tests/testbench_dist/scripts/sync_plugin_repo.py -m "fix: ..."

Skips generated/local paths (bundled/, vendor/, uv.lock, .venv/).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PLUGIN_REL = Path("tests/testbench_dist/plugin/testbench")
SKIP_UNTRACKED = {"uv.lock", ".venv"}


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=cwd, text=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-m",
        "--message",
        default="chore: sync from N.E.K.O monorepo",
        help="Commit message for the plugin repository",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be committed without pushing",
    )
    parser.add_argument(
        "--allow-dirty-monorepo",
        action="store_true",
        help="Do not require a clean monorepo plugin/ tree",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    plugin_dir = root / PLUGIN_REL
    if not (plugin_dir / ".git").is_dir():
        print(f"error: nested plugin git not found at {plugin_dir}", file=sys.stderr)
        return 1

    if not args.allow_dirty_monorepo:
        dirty = _run(
            ["git", "diff", "--quiet", "HEAD", "--", str(PLUGIN_REL)],
            cwd=root,
        )
        if dirty.returncode != 0:
            print(
                "error: monorepo has uncommitted changes under plugin/testbench/. "
                "Commit in N.E.K.O first, or pass --allow-dirty-monorepo.",
                file=sys.stderr,
            )
            return 1

    status = _run(["git", "status", "--porcelain"], cwd=plugin_dir)
    if status.returncode != 0:
        return status.returncode
    lines = [ln for ln in (status.stdout or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not any(part in SKIP_UNTRACKED for part in ln.split())]
    if not lines:
        print("plugin repo: nothing to commit (already in sync)")
        return 0

    if _run(["git", "add", "-u", "."], cwd=plugin_dir).returncode != 0:
        return 1

    if args.dry_run:
        _run(["git", "status", "--short"], cwd=plugin_dir)
        print("dry-run: skipped commit/push")
        return 0

    if _run(["git", "commit", "-m", args.message], cwd=plugin_dir).returncode != 0:
        return 1
    if _run(["git", "push", "origin", "HEAD"], cwd=plugin_dir).returncode != 0:
        return 1

    head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=plugin_dir)
    if head.returncode == 0:
        print(f"plugin repo synced at {head.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
