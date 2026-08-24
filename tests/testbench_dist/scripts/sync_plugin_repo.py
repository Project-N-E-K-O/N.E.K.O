#!/usr/bin/env python3
"""Mirror Testbench plugin driver sources to the Market git repository.

Local (nested .git present):

    uv run python tests/testbench_dist/scripts/sync_plugin_repo.py -m "fix: ..."

CI / no nested .git (rsync monorepo tree into a cloned plugin repo):

    python tests/testbench_dist/scripts/sync_plugin_repo.py --ci \\
        --target-repo /tmp/plugin-repo \\
        --monorepo-sha "$GITHUB_SHA" \\
        -m "chore: auto-sync from N.E.K.O"
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_REL = Path("tests/testbench_dist/plugin/testbench")
SKIP_DIR_NAMES = frozenset(
    {
        "bundled",
        "vendor",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        ".git",
    }
)
SKIP_FILE_NAMES = frozenset({"uv.lock", ".neko-plugin"})
# Paths only required in the Market repo; never delete during CI mirror if absent from monorepo.
PRESERVE_IN_TARGET = frozenset({".vscode"})


def _run(cmd: list[str], *, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        check=False,
        capture_output=capture,
    )


def _should_skip(rel: Path) -> bool:
    if rel.name in SKIP_FILE_NAMES:
        return True
    return any(part in SKIP_DIR_NAMES for part in rel.parts)


def _iter_source_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if _should_skip(rel):
            continue
        files.append(rel)
    return files


def mirror_plugin_tree(*, source: Path, target_repo: Path) -> bool:
    """Copy source plugin files into target repo. Return True if anything changed."""
    if not source.is_dir():
        raise FileNotFoundError(f"source plugin dir missing: {source}")
    if not (target_repo / ".git").is_dir():
        raise FileNotFoundError(f"target plugin git repo missing: {target_repo}")

    changed = False
    source_files = set(_iter_source_files(source))

    for rel in sorted(source_files):
        src = source / rel
        dest = target_repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or not filecmp.cmp(src, dest, shallow=False):
            shutil.copy2(src, dest)
            changed = True

    for path in list(target_repo.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(target_repo)
        if _should_skip(rel):
            continue
        if rel not in source_files:
            if rel.parts and rel.parts[0] in PRESERVE_IN_TARGET:
                continue
            path.unlink()
            changed = True

    return changed


def _commit_and_push(repo: Path, *, message: str, push: bool) -> int:
    if _run(["git", "add", "-A"], cwd=repo).returncode != 0:
        return 1
    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if diff.returncode == 0:
        print("plugin repo: nothing to commit (already in sync)")
        return 0
    if _run(["git", "commit", "-m", message], cwd=repo).returncode != 0:
        return 1
    if not push:
        print("push skipped")
        return 0
    if _run(["git", "push", "origin", "HEAD"], cwd=repo).returncode != 0:
        return 1
    head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture=True)
    if head.returncode == 0:
        print(f"plugin repo synced at {head.stdout.strip()}")
    return 0


def sync_ci(
    *,
    source: Path,
    target_repo: Path,
    message: str,
    push: bool,
) -> int:
    mirror_plugin_tree(source=source, target_repo=target_repo)
    return _commit_and_push(target_repo, message=message, push=push)


def sync_local_nested(
    *,
    root: Path,
    plugin_dir: Path,
    message: str,
    dry_run: bool,
    allow_dirty_monorepo: bool,
) -> int:
    if not (plugin_dir / ".git").is_dir():
        print(
            "error: nested plugin git not found; use --ci --target-repo for CI mirror",
            file=sys.stderr,
        )
        return 1

    if not allow_dirty_monorepo:
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

    status = _run(["git", "status", "--porcelain"], cwd=plugin_dir, capture=True)
    if status.returncode != 0:
        return status.returncode
    lines = [ln for ln in (status.stdout or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not any(part in SKIP_FILE_NAMES for part in ln.split())]
    if not lines:
        print("plugin repo: nothing to commit (already in sync)")
        return 0

    if _run(["git", "add", "-u", "."], cwd=plugin_dir).returncode != 0:
        return 1

    if dry_run:
        _run(["git", "status", "--short"], cwd=plugin_dir)
        print("dry-run: skipped commit/push")
        return 0

    return _commit_and_push(plugin_dir, message=message, push=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-m",
        "--message",
        default="chore: sync from N.E.K.O monorepo",
        help="Commit message for the plugin repository",
    )
    parser.add_argument("--dry-run", action="store_true", help="Local nested mode only")
    parser.add_argument("--allow-dirty-monorepo", action="store_true")
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Mirror into a separately cloned plugin repo (GitHub Actions)",
    )
    parser.add_argument(
        "--target-repo",
        type=Path,
        help="Path to cloned n.e.k.o_plugin_testbench repo (--ci)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Monorepo plugin source dir (default: tests/testbench_dist/plugin/testbench)",
    )
    parser.add_argument(
        "--monorepo-sha",
        default="",
        help="Short/full SHA appended to default commit message (--ci)",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit only, do not push (--ci)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    source = (args.source or (root / PLUGIN_REL)).resolve()

    if args.ci:
        if args.target_repo is None:
            print("error: --ci requires --target-repo", file=sys.stderr)
            return 1
        message = args.message
        if args.monorepo_sha and message == "chore: sync from N.E.K.O monorepo":
            short = args.monorepo_sha[:12]
            message = f"chore: auto-sync from N.E.K.O @ {short}"
        return sync_ci(
            source=source,
            target_repo=args.target_repo.resolve(),
            message=message,
            push=not args.no_push,
        )

    return sync_local_nested(
        root=root,
        plugin_dir=source,
        message=args.message,
        dry_run=args.dry_run,
        allow_dirty_monorepo=args.allow_dirty_monorepo,
    )


if __name__ == "__main__":
    raise SystemExit(main())
