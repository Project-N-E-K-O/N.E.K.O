#!/usr/bin/env python3
"""Static check: forbid `loguru` (and friends) anywhere in the repo.

Why this exists
---------------
The project standardised on stdlib logging via ``utils.logger_config`` /
``RobustLoggerConfig``. Re-introducing loguru fragments the logging surface
(formatter, sinks, file naming, multi-process semantics) and breaks the
plugin/main parity that the unified config restores. A previous round of
"just one more loguru sink, this once" cost real time and a dedicated
cleanup PR — this lint exists so the next attempt fails CI before merge.

The same ban applies to ``structlog`` and ``logbook`` for symmetry: any
third-party logging frontend goes through review the hard way (delete this
script first).

What it flags
-------------
Any of the following at module scope, function scope, or inside ``if`` /
``try`` blocks (the AST walker descends everywhere):

    import loguru
    import loguru.something
    import loguru as foo
    from loguru import logger
    from loguru.something import x
    from loguru import logger as foo

…and the same for ``structlog`` and ``logbook``.

Comments, docstrings, and string literals mentioning the names are NOT
flagged — only real ``import`` statements. That keeps kill-warning comments
("don't add loguru back") legal.

Suppression
-----------
None. If you have a genuine reason to use one of these libraries, delete
this script in the same PR and explain in the description; reviewers will
make the call. A per-line escape hatch would defeat the purpose.

Output
------
Every violation prints as ``path:line:col  NO_LOGURU  message``. Exit
status is 1 when any violation is found, 0 otherwise.

Usage:
    python scripts/check_no_loguru.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default scope: the entire repo. We rely on EXCLUDE_DIRS for noise control
# (vendored deps, build outputs, the frontend bundle) rather than an
# allowlist — a banned import anywhere in our own code is a violation.
DEFAULT_PATHS: list[str] = ["."]

# Directories never scanned. Mirrors the ruff `extend-exclude` in
# pyproject.toml plus a few CI/build artefacts that may exist in the
# checkout.
EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "frontend",
    "dist",
    "build",
    "node_modules",
    ".git",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "plugin/plugins",  # third-party plugin payloads, not our code
}

# The script itself mentions loguru in strings; skip it so the check
# doesn't flag the source of the check. Same for the kill-warning sites
# in user-facing docs/test fixtures if any get added later.
EXCLUDE_FILES = {
    "scripts/check_no_loguru.py",
}

CODE = "NO_LOGURU"

# Top-level package names that are banned. Match on the first dotted
# component so ``loguru.handlers`` is caught the same as ``loguru``.
BANNED_PACKAGES = {
    "loguru",
    "structlog",
    "logbook",
}


def _is_banned(module_name: str | None) -> str | None:
    """If ``module_name`` is rooted at a banned package, return the package
    name. ``None`` for non-matches and for ``None`` input (used by
    ``from . import x`` where ``module`` is ``None``).
    """
    if not module_name:
        return None
    head = module_name.split(".", 1)[0]
    if head in BANNED_PACKAGES:
        return head
    return None


class LoguruImportChecker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[tuple[int, int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            banned = _is_banned(alias.name)
            if banned is not None:
                self.violations.append(
                    (
                        node.lineno,
                        node.col_offset + 1,
                        f"`import {alias.name}` is forbidden — use stdlib logging via "
                        f"`from utils.logger_config import get_module_logger` "
                        f"(or `plugin.logging_config.logger` inside plugin code).",
                    )
                )
        # No children we care about.

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # ``from . import x`` has module=None; never banned.
        banned = _is_banned(node.module)
        if banned is not None:
            names = ", ".join(a.name for a in node.names) or "*"
            self.violations.append(
                (
                    node.lineno,
                    node.col_offset + 1,
                    f"`from {node.module} import {names}` is forbidden — use stdlib "
                    f"logging via `from utils.logger_config import get_module_logger` "
                    f"(or `plugin.logging_config.logger` inside plugin code).",
                )
            )


def _is_excluded(path: Path) -> bool:
    """True if any path component matches EXCLUDE_DIRS, or the relative
    posix path matches EXCLUDE_FILES."""
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    # Also catch composite excludes like "plugin/plugins".
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    if rel in EXCLUDE_FILES:
        return True
    for ex in EXCLUDE_DIRS:
        if "/" in ex and (rel == ex or rel.startswith(ex + "/")):
            return True
    return False


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if p.suffix == ".py" and not _is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if not _is_excluded(f):
                    yield f


def _parse_file(path: Path) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"{path}: skipped — {e}", file=sys.stderr)
        return None
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"{path}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
        return None


def check_file(path: Path, tree: ast.Module) -> list[tuple[int, int, str]]:
    checker = LoguruImportChecker(path)
    checker.visit(tree)
    return checker.violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid `loguru` / `structlog` / `logbook` imports anywhere in the repo."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: entire repo, minus EXCLUDE_DIRS).",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    total = 0
    for file in _iter_python_files(targets):
        tree = _parse_file(file)
        if tree is None:
            continue
        for lineno, col, msg in check_file(file, tree):
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} forbidden-import violation(s) found.\n"
            "The project uses stdlib logging via `utils.logger_config` / "
            "`plugin.logging_config`. Do NOT reintroduce loguru / structlog / "
            "logbook — the unified config exists so plugin and main-body logs "
            "share format, sinks, and writable-dir fallback. If you genuinely "
            "need a third-party logging frontend, delete `scripts/check_no_loguru.py` "
            "in the same PR and justify it in the description.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
