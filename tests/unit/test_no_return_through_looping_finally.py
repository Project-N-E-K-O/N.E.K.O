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
# Local environments and caches at any depth hold third-party code, not ours.
_SKIPPED_ANY_LEVEL = frozenset({"node_modules", "site-packages", "__pycache__", "venv", "env"})

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(statements):
    """Walk statements without entering nested function or class bodies.

    A nested def's own return never passes through the enclosing finally, so
    the def is skipped whether it sits directly in the try or deeper.
    """
    stack = [node for node in statements if not isinstance(node, _NESTED_SCOPES)]
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


def _packaged_sources(root: Path = REPO_ROOT):
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if parts[0] in _SKIPPED_TOP_LEVEL:
            continue
        dirs = parts[:-1]
        if any(part.startswith(".") or part in _SKIPPED_ANY_LEVEL for part in dirs):
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


def test_detector_ignores_return_inside_nested_def():
    tree = ast.parse(
        "def f():\n"
        "    try:\n"
        "        def helper():\n"
        "            return 1\n"
        "        class Box:\n"
        "            def get(self):\n"
        "                return 2\n"
        "        register(helper, Box)\n"
        "    finally:\n"
        "        while busy():\n"
        "            tick()\n"
    )
    assert _offending_lines(tree) == []


def test_packaged_sources_skip_local_environments(tmp_path):
    kept = ["launcher.py", "main_logic/worker.py", "plugin/plugins/demo/main.py"]
    dropped = [
        "venv/Lib/site.py", "env/lib/x.py", ".venv/Lib/y.py",
        "plugin/plugins/demo/.venv/Lib/z.py", "plugin/vendor/site-packages/pkg/a.py",
        "utils/__pycache__/b.py", "tests/unit/test_c.py",
    ]
    for rel in kept + dropped:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n", encoding="utf-8")
    found = sorted(p.relative_to(tmp_path).as_posix() for p in _packaged_sources(tmp_path))
    assert found == sorted(kept)


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
