# -*- coding: utf-8 -*-
"""Regression: no ``return`` from a ``try`` whose ``finally`` contains a ``while``.

CPython keeps the pending return value while the finally block runs. Nuitka
4.3.1 does not when that finally contains a ``while`` loop: the compiled
function returns None, silently ends the process, or segfaults. The frozen
Windows build hit exactly this in ``_run_worker`` (wake-word release smoke got
None back from every worker), and only the frozen binary shows it -- source
runs and unit tests stay green. Store the value and return after the
try/finally instead.
"""
import ast
from pathlib import Path

from tests.repo_ast_cache import parse_source_file


REPO_ROOT = Path(__file__).resolve().parents[2]

# Everything else under the repo root can end up in a Nuitka bundle.
_SKIPPED_TOP_LEVEL = frozenset({"tests", "frontend", "node_modules", "build", "dist", "docs"})

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(statements):
    """Walk statements without entering nested function or class bodies."""
    stack = list(statements)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _NESTED_SCOPES))


def _offending_lines(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Try, ast.TryStar)) or not node.finalbody:
            continue
        guarded = node.body + node.handlers + node.orelse
        if (any(isinstance(n, ast.Return) for n in _own_nodes(guarded))
                and any(isinstance(n, ast.While) for n in _own_nodes(node.finalbody))):
            lines.append(node.lineno)
    return lines


def _packaged_sources():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        top = path.relative_to(REPO_ROOT).parts[0]
        if top.startswith(".") or top in _SKIPPED_TOP_LEVEL:
            continue
        yield path


def test_detector_flags_the_frozen_build_shape():
    tree = ast.parse(
        "async def f():\n"
        "    try:\n"
        "        return 1\n"
        "    finally:\n"
        "        while busy():\n"
        "            await tick()\n"
    )
    assert _offending_lines(tree) == [2]


def test_detector_accepts_return_after_finally():
    tree = ast.parse(
        "async def f():\n"
        "    try:\n"
        "        result = 1\n"
        "    finally:\n"
        "        while busy():\n"
        "            await tick()\n"
        "    return result\n"
    )
    assert _offending_lines(tree) == []


def test_repository_has_no_return_through_looping_finally():
    violations = []
    for path in _packaged_sources():
        try:
            tree = parse_source_file(path)
        except SyntaxError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations.extend(f"{rel}:{line}" for line in _offending_lines(tree))
    assert not violations, (
        "return inside try whose finally has a while loop (Nuitka 4.3 returns None): "
        + ", ".join(violations)
    )
