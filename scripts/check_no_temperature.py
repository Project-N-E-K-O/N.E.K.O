#!/usr/bin/env python3
"""Static check: forbid passing ``temperature=`` to LLM client constructors.

Why this exists
---------------
The project standardised on letting the model's own default decide. Every
``utils.llm_client.create_chat_llm`` / ``ChatOpenAI`` (and any wrapper that
forwards through to them) MUST NOT carry an explicit ``temperature=`` kwarg.
Reasons:

* o1 / o3 / gpt-5-thinking / Claude extended-thinking reject the field
  outright. Hard-coding e.g. 0.5 at a call site silently breaks them.
* Different memory tasks were each picking 0.1 / 0.2 / 0.3 / 0.5 / 1.0
  ad-hoc; the drift between tasks made regressions hard to reproduce.
* Provider defaults are already in a sensible band; we don't gain anything
  from re-stating them at every call site.

The default value of ``temperature`` parameter in both ``ChatOpenAI.__init__``
and ``create_chat_llm`` is ``None`` (= "don't write the field into the request
body at all"). Any caller passing ``temperature=...`` defeats this contract.

Scope
-----
This check runs over ``memory_server.py`` + ``memory/`` + ``utils/`` only.
Other parts of the codebase (``brain/``, ``main_routers/``, plugin-specific LLM
adapters, testbench harnesses) have their own temperature semantics and are
explicitly out of scope. Pass paths explicitly to scan elsewhere.

What it flags
-------------
Any keyword argument literally named ``temperature`` in a call expression.
This is intentionally broad — wrapper helpers (``FactStore._allm_call_with_retries``
historically) used to take a ``temperature=`` parameter that forwarded
through; we want any new wrapper to fail this check too.

Suppression
-----------
None. Three legitimate exceptions live in ``utils/llm_client.py`` itself
(the parameter declaration, the assignment, and the ``temperature=temperature``
forwarding inside the factory). The script excludes that file by path.

Output
------
Every violation prints as ``path:line:col  NO_TEMPERATURE  message``. Exit
status is 1 when any violation is found, 0 otherwise.

Usage:
    python scripts/check_no_temperature.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default scope: only the memory subsystem and the helpers it relies on.
# See module docstring for rationale.
DEFAULT_PATHS: list[str] = ["memory_server.py", "memory", "utils"]

# Mirrors check_no_loguru.py for consistency.
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

# Files that legitimately mention ``temperature=`` because they ARE the
# canonical client wrapper (parameter declaration / assignment / forwarding).
# The script itself trivially mentions the name in strings.
EXCLUDE_FILES = {
    "scripts/check_no_temperature.py",
    "utils/llm_client.py",
}

CODE = "NO_TEMPERATURE"


class TemperatureKwargChecker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[tuple[int, int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            # ``f(**kwargs)`` has arg=None and is fine (we can't statically
            # see what's inside, and forwarding-through patterns are rare
            # outside of the factory itself).
            if kw.arg == "temperature":
                self.violations.append(
                    (
                        kw.value.lineno if hasattr(kw.value, "lineno") else node.lineno,
                        (kw.value.col_offset if hasattr(kw.value, "col_offset") else node.col_offset) + 1,
                        "passing `temperature=...` to an LLM client / wrapper is forbidden — "
                        "remove the kwarg entirely. Project policy lets the provider's own "
                        "default decide; see memory/__init__.py and .agent/rules/neko-guide.md "
                        "for the rationale.",
                    )
                )
        # Descend into nested calls (e.g. positional arguments that are themselves calls).
        self.generic_visit(node)


def _is_excluded(path: Path) -> bool:
    """True if any path component matches EXCLUDE_DIRS, or the relative
    posix path matches EXCLUDE_FILES."""
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
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
    checker = TemperatureKwargChecker(path)
    checker.visit(tree)
    return checker.violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid `temperature=...` kwargs in LLM client / wrapper calls."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: memory_server.py + memory/ + utils/).",
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
            f"\n{total} forbidden `temperature=` kwarg(s) found.\n"
            "Project policy: do NOT pass `temperature=...` to "
            "`utils.llm_client.create_chat_llm`, `ChatOpenAI`, or any wrapper "
            "that forwards through to them. The factory default is `None`, "
            "which omits the field from the request body and lets the provider "
            "decide — required for o1/o3/gpt-5-thinking/Claude extended-thinking, "
            "and avoids per-call-site temperature drift across memory tasks. "
            "If you genuinely need to set it, delete `scripts/check_no_temperature.py` "
            "in the same PR and justify it in the description.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
