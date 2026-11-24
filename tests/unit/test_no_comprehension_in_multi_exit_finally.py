# -*- coding: utf-8 -*-
"""Regression: no list/set/dict comprehension in a ``finally`` that Nuitka clones twice.

Nuitka 4.2.2 (the version the desktop build pins) copies a ``finally`` block
once for every way out of its ``try``. When the ``try`` can be left both by a
``return`` / ``break`` / ``continue`` and by running off its end, and the
``finally`` holds a list, set or dict comprehension, the copies allocate the
same temporary name and compilation stops with an internal
``AssertionError: listcomp_1__.0_clone``. The whole desktop build fails on it;
source runs and unit tests stay green. ``_host_scan`` in
plugin/tests/unit/sdk/shared/test_permission_narrowing.py hit exactly this.

Generator expressions are compiled as real functions and are not affected.
Fix by looping instead of comprehending in the finally, or by making the try
end in a ``return`` / ``raise`` so it cannot run off its end.
"""
import ast

from tests.repo_ast_cache import parse_source_file
from tests.unit.test_no_return_through_looping_finally import REPO_ROOT, _own_nodes, _packaged_sources


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _escaping_loop_jumps(statements):
    """``break`` / ``continue`` that leave the try, not a loop inside it."""
    stack = [node for node in statements if not isinstance(node, _NESTED_SCOPES)]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Break, ast.Continue)):
            yield node
        elif isinstance(node, _LOOPS):
            # Jumps in the loop body bind to that loop; its else clause does not.
            stack.extend(node.orelse)
        else:
            stack.extend(
                child for child in ast.iter_child_nodes(node) if not isinstance(child, _NESTED_SCOPES)
            )


def _may_run_off_end(statements) -> bool:
    # Conservative: only a block whose last statement is a return or raise
    # counts as never completing normally.
    return not statements or not isinstance(statements[-1], (ast.Return, ast.Raise))


def _offending_lines(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Try, ast.TryStar)) or not node.finalbody:
            continue
        if not any(isinstance(n, _COMPREHENSIONS) for n in _own_nodes(node.finalbody)):
            continue
        guarded = node.body + node.handlers + node.orelse
        jumps_out = (any(isinstance(n, ast.Return) for n in _own_nodes(guarded))
                     or any(True for _ in _escaping_loop_jumps(guarded)))
        runs_off_end = (_may_run_off_end(node.orelse or node.body)
                        or any(_may_run_off_end(handler.body) for handler in node.handlers))
        if jumps_out and runs_off_end:
            lines.append(node.lineno)
    return lines


def _lines(source: str) -> list[int]:
    return _offending_lines(ast.parse(source))


def test_detector_flags_the_host_scan_shape():
    assert _lines(
        "def f(d, lock):\n"
        "    try:\n"
        "        with lock:\n"
        "            return {k: 1 for k in d}\n"
        "    finally:\n"
        "        for k in [k for k in d]:\n"
        "            pass\n"
    ) == [2]


def test_detector_flags_other_ways_out():
    # Each of these crashed Nuitka 4.2.2.
    assert _lines(
        "def f(d):\n"
        "    try:\n"
        "        if d:\n"
        "            return 1\n"
        "    finally:\n"
        "        x = {k for k in d}\n"
    ) == [2]
    assert _lines(
        "def f(d):\n"
        "    try:\n"
        "        return 1\n"
        "    except ValueError:\n"
        "        pass\n"
        "    finally:\n"
        "        x = [k for k in d]\n"
    ) == [2]
    assert _lines(
        "def f(d, lock):\n"
        "    for z in d:\n"
        "        try:\n"
        "            with lock:\n"
        "                break\n"
        "        finally:\n"
        "            x = [k for k in d]\n"
    ) == [3]


def test_detector_accepts_shapes_nuitka_compiles():
    # Each of these compiled with Nuitka 4.2.2.
    for source in (
        # The try always ends in a return: it cannot run off its end.
        "def f(d, lock):\n"
        "    try:\n"
        "        with lock:\n"
        "            x = 1\n"
        "        return x\n"
        "    finally:\n"
        "        x = [k for k in d]\n",
        # Only one way out besides an exception.
        "def f(d):\n"
        "    try:\n"
        "        return 1\n"
        "    finally:\n"
        "        x = [k for k in d]\n",
        # A plain loop in the finally.
        "def f(d, lock):\n"
        "    try:\n"
        "        with lock:\n"
        "            return 1\n"
        "    finally:\n"
        "        for k in list(d):\n"
        "            pass\n",
        # A generator expression is a real function, not an inlined comprehension.
        "def f(d, lock):\n"
        "    try:\n"
        "        with lock:\n"
        "            return 1\n"
        "    finally:\n"
        "        x = list(k for k in d)\n",
        # break inside a loop of its own does not leave the try.
        "def f(d):\n"
        "    try:\n"
        "        for k in d:\n"
        "            break\n"
        "    finally:\n"
        "        x = [k for k in d]\n",
        # A return in a nested def does not leave the try.
        "def f(d):\n"
        "    try:\n"
        "        def g():\n"
        "            return 1\n"
        "        register(g)\n"
        "    finally:\n"
        "        x = [k for k in d]\n",
    ):
        assert _lines(source) == [], source


def test_repository_has_no_comprehension_in_multi_exit_finally():
    violations = []
    for path in _packaged_sources():
        try:
            tree = parse_source_file(path)
        except SyntaxError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations.extend(f"{rel}:{line}" for line in _offending_lines(tree))
    assert not violations, (
        "comprehension in a finally whose try both jumps out and can run off its end "
        "(Nuitka 4.2.2 crashes with AssertionError: listcomp_1__.0_clone): "
        + ", ".join(violations)
    )
