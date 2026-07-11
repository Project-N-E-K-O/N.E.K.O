#!/usr/bin/env python3
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

"""Static check: keep heavy SDKs OFF the startup import chain (lazy-import them).

Why this exists — the regression pattern
----------------------------------------
Production (Steam / Nuitka frozen) runs merged single-process mode:
``launcher.run_merged_servers`` imports the three app modules
(memory/agent/main) **serially, before any port binds**, so every module
pulled in at module scope is paid on the every-launch critical path
between double-click and the Pet window becoming interactive.

This class of regression is silent: nothing breaks, startup just gets
slower. Reference incident: PR #1496 cut ``import app.main_server`` to
~0.6s; six weeks later it had crept back to ~2.1s — mostly the openai
2.x SDK's pydantic ``types`` tree growing heavier plus anthropic, both
riding in via a module-level ``from openai import ...`` in
``utils/llm_client.py`` that every server imports transitively. Nobody
noticed, because nothing guarded it.

The repo-wide pattern for these SDKs is **lazy import + background
warmup** (``utils/module_warmup.py``): import inside the function that
first needs it, and list the module in the warmup table so a daemon
thread pre-imports it right after the server is ready. First real use
then never waits. This script pins that pattern down for the modules
already converted, so they cannot silently move back to module scope.

What it flags
-------------
A ``import X`` / ``from X import ...`` of a banned heavy module at
**module scope** (including inside module-level ``if`` / ``try`` /
``with`` blocks and class bodies — those all execute at import time) in
the startup-chain source trees. Imports inside functions/methods are
the sanctioned lazy form and are never flagged. ``if TYPE_CHECKING:``
blocks are skipped — they don't execute at runtime and are the standard
home for annotation-only imports.

Banned modules (all already lazy today; each one's first use is covered
by ``utils/module_warmup.py``):

    openai, anthropic     — LLM SDKs, ~0.7s combined, pydantic class
                            building (CPU-bound, survives freezing);
                            lazy accessors live in utils/llm_client.py
    bs4, bilibili_api     — scraping stack, ~0.3s, lazy in utils/web_scraper.py
    google.genai          — ~0.6s + drags mcp, lazy since PR #1496
    translatepy, googletrans, dashscope, pyncm_async
                          — feature-router deps, lazy per PR #1496
    onnxruntime           — embedding runtime, lazy in memory/embeddings.py

Suppression
-----------
Per-line: append ``# noqa: STARTUP_LAZY_IMPORT`` with a justification
comment when a module-scope import is genuinely required (rare — e.g. a
module that is itself only ever imported lazily AND needs the symbol at
class-definition time). Prefer restructuring to the lazy pattern first.
Directory-level: ``EXCLUDE_DIRS`` lists trees that are not on the
startup import chain (plugins load on demand; brain/cua is only
imported from on-demand agent paths).

Output
------
Every violation prints as ``path:line:col  STARTUP_LAZY_IMPORT  message``.
Exit status is 1 when any violation is found, 0 otherwise.

Usage:
    python scripts/check_startup_import_lazy.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Startup-chain source trees: everything launcher.run_merged_servers reaches
# via `from app import memory_server / agent_server / main_server`, plus the
# launcher itself and the config package (imported by all of them).
DEFAULT_PATHS: list[str] = [
    "app",
    "brain",
    "config",
    "main_logic",
    "main_routers",
    "memory",
    "utils",
    "launcher.py",
]

EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "dist",
    "build",
    "node_modules",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    # Not on the startup import chain: cua is only imported from on-demand
    # agent execution paths (no module-scope route from the three app
    # modules reaches it). If that ever changes, lazify its openai/anthropic
    # imports first, then remove this exclusion.
    "brain/cua",
}

CODE = "STARTUP_LAZY_IMPORT"

# module head -> where its sanctioned lazy home is (for the error message)
BANNED_MODULES: dict[str, str] = {
    "openai": "utils/llm_client.py (in-function imports + retry-type accessors)",
    "anthropic": "utils/llm_client.py (in-function imports + retry-type accessors)",
    "bs4": "utils/web_scraper.py (in-function imports)",
    "bilibili_api": "utils/web_scraper.py (find_spec probe; import only in handlers)",
    "google.genai": "main_logic/omni_offline_client.py (_ensure_genai)",
    "translatepy": "feature handlers (lazy since PR #1496)",
    "googletrans": "feature handlers (lazy since PR #1496)",
    "dashscope": "feature handlers (lazy since PR #1496)",
    "pyncm_async": "feature handlers (lazy since PR #1496)",
    "onnxruntime": "memory/embeddings.py (_load_session_blocking)",
}


def _banned_key(module_name: str | None) -> str | None:
    if not module_name:
        return None
    for banned in BANNED_MODULES:
        if module_name == banned or module_name.startswith(banned + "."):
            return banned
    return None


def _is_type_checking_if(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def _iter_module_scope_stmts(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements that execute at import time (never descends into functions)."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(stmt, ast.If):
            if _is_type_checking_if(stmt):
                # TYPE_CHECKING body never runs; the else branch still does.
                yield from _iter_module_scope_stmts(stmt.orelse)
                continue
            yield from _iter_module_scope_stmts(stmt.body)
            yield from _iter_module_scope_stmts(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            yield from _iter_module_scope_stmts(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_module_scope_stmts(handler.body)
            yield from _iter_module_scope_stmts(stmt.orelse)
            yield from _iter_module_scope_stmts(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
            yield from _iter_module_scope_stmts(stmt.body)
            yield from _iter_module_scope_stmts(getattr(stmt, "orelse", []))
        elif isinstance(stmt, ast.ClassDef):
            # Class bodies execute at import time too.
            yield from _iter_module_scope_stmts(stmt.body)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                yield from _iter_module_scope_stmts(case.body)
        elif isinstance(stmt, ast.TryStar):
            yield from _iter_module_scope_stmts(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_module_scope_stmts(handler.body)
            yield from _iter_module_scope_stmts(stmt.orelse)
            yield from _iter_module_scope_stmts(stmt.finalbody)


def _noqa_lines(source: str) -> set[int]:
    lines: set[int] = set()
    for idx, line in enumerate(source.splitlines(), start=1):
        if "noqa" in line and CODE in line:
            lines.add(idx)
    return lines


def check_source(path: Path, source: str, tree: ast.Module) -> list[tuple[int, int, str]]:
    violations: list[tuple[int, int, str]] = []
    suppressed = _noqa_lines(source)
    for stmt in _iter_module_scope_stmts(tree.body):
        found: list[tuple[str, str]] = []  # (banned key, import text)
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                key = _banned_key(alias.name)
                if key is not None:
                    found.append((key, f"import {alias.name}"))
        elif isinstance(stmt, ast.ImportFrom):
            key = _banned_key(stmt.module)
            if key is not None:
                names = ", ".join(a.name for a in stmt.names) or "*"
                found.append((key, f"from {stmt.module} import {names}"))
            elif stmt.module and stmt.level == 0:
                # `from google import genai` resolves to google.genai — match
                # each alias against the banned list too, or namespace-package
                # spellings slip through.
                for alias in stmt.names:
                    alias_key = _banned_key(f"{stmt.module}.{alias.name}")
                    if alias_key is not None:
                        found.append((alias_key, f"from {stmt.module} import {alias.name}"))
        # noqa on any line of a multiline import suppresses the statement.
        end_lineno = getattr(stmt, "end_lineno", None) or stmt.lineno
        for key, text in found:
            if any(ln in suppressed for ln in range(stmt.lineno, end_lineno + 1)):
                continue
            violations.append(
                (
                    stmt.lineno,
                    stmt.col_offset + 1,
                    f"`{text}` at module scope puts `{key}` back on the "
                    f"startup import chain — merged production mode imports "
                    f"this tree serially before any port binds, so every "
                    f"launch pays the import again (the #1496→openai-2.x "
                    f"silent-regression pattern). Move the import inside the "
                    f"function that first needs it; background warmup in "
                    f"utils/module_warmup.py keeps first use fast. Sanctioned "
                    f"lazy home: {BANNED_MODULES[key]}.",
                )
            )
    return violations


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid module-scope imports of heavy lazy-by-contract SDKs on the startup chain."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: startup-chain trees).",
    )
    args = parser.parse_args(argv)

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    total = 0
    for file in _iter_python_files(targets):
        try:
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"{file}: skipped — {e}", file=sys.stderr)
            continue
        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError as e:
            print(f"{file}:{e.lineno}: syntax error — {e.msg}", file=sys.stderr)
            continue
        for lineno, col, msg in check_source(file, source, tree):
            rel = file.relative_to(REPO_ROOT) if file.is_relative_to(REPO_ROOT) else file
            print(f"{rel}:{lineno}:{col}  {CODE}  {msg}")
            total += 1

    if total:
        print(
            f"\n{total} startup-chain lazy-import violation(s) found.\n"
            "These SDKs are lazy-by-contract: production merged mode imports the "
            "app tree serially before any port binds, so a module-scope import "
            "here slows EVERY launch, silently (see #1496 → openai 2.x creep). "
            "Import inside the function that first needs the module and make "
            "sure it is listed in utils/module_warmup.py so the background "
            "warmup thread pre-imports it after the server is ready. If module "
            "scope is genuinely unavoidable, add `# noqa: STARTUP_LAZY_IMPORT` "
            "with a justification.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
