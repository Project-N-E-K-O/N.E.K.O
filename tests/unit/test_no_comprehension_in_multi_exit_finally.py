# -*- coding: utf-8 -*-
"""Regression: no list/set/dict comprehension in a ``finally`` that Nuitka clones twice.

Nuitka 4.2.2 (the version the desktop build pins) copies a ``finally`` block
once for every kind of way out of its ``try``: running off its end,
``return``, ``break`` and ``continue``. When there are two or more such kinds
and the ``finally`` holds a list, set or dict comprehension, the copies
allocate the same temporary name and compilation stops with an internal
``AssertionError: listcomp_1__.0_clone``. Several exits of one kind (two
``return`` statements, two ``break`` statements) and leaving by an exception
compile fine. The whole desktop build fails on it; source runs and unit tests
stay green. ``_host_scan`` in
plugin/tests/unit/sdk/shared/test_permission_narrowing.py hit exactly this
(a ``return`` inside a ``with``, and the ``try`` could also run off its end).

Generator expressions are compiled as real functions and are not affected,
including comprehensions nested inside them. Fix by looping instead of
comprehending in the finally, or by leaving the try one way only.
"""
import ast

from tests.repo_ast_cache import parse_source_file
from tests.unit.test_no_return_through_looping_finally import (
    _NESTED_SCOPES,
    REPO_ROOT,
    _own_nodes,
    _packaged_sources,
)


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)
# Statements after which a block does not complete normally. A block ending in
# break/continue leaves by that jump, which is counted as its own kind.
_TERMINAL = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def _walk(statements, skip):
    stack = [node for node in statements if not isinstance(node, skip)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, skip))


def _finally_has_comprehension(finalbody) -> bool:
    # A generator expression is a real function, so comprehensions nested in
    # one are not copied with the finally (checked with Nuitka 4.2.2).
    return any(isinstance(n, _COMPREHENSIONS) for n in _walk(finalbody, _NESTED_SCOPES + (ast.GeneratorExp,)))


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
    # Conservative on purpose: only the last statement is looked at, so a
    # block ending in an if/else that returns on both branches, or in
    # ``while True``, still counts as able to run off its end.
    return not statements or not isinstance(statements[-1], _TERMINAL)


def _exit_kinds(node) -> set[str]:
    guarded = node.body + node.handlers + node.orelse
    kinds = {type(jump).__name__ for jump in _escaping_loop_jumps(guarded)}
    if any(isinstance(n, ast.Return) for n in _own_nodes(guarded)):
        kinds.add("Return")
    if _may_run_off_end(node.orelse or node.body) or any(_may_run_off_end(h.body) for h in node.handlers):
        kinds.add("fall-through")
    return kinds


def _offending_lines(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Try, ast.TryStar)) or not node.finalbody:
            continue
        if _finally_has_comprehension(node.finalbody) and len(_exit_kinds(node)) >= 2:
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
    # No running off the end, but two kinds of jump out.
    for first, then in (("break", "return 1"), ("continue", "return 1"), ("break", "continue")):
        assert _lines(
            "def f(d):\n"
            "    for z in d:\n"
            "        try:\n"
            "            if z:\n"
            f"                {first}\n"
            f"            {then}\n"
            "        finally:\n"
            "            x = [k for k in d]\n"
        ) == [3], (first, then)


def test_detector_accepts_shapes_nuitka_compiles():
    # Each of these compiled with Nuitka 4.2.2.
    for source in (
        # The try always ends in a return: one kind of exit only.
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
        # Several exits of one kind: two returns.
        "def f(d, z):\n"
        "    try:\n"
        "        if z:\n"
        "            return 2\n"
        "        return 1\n"
        "    finally:\n"
        "        x = [k for k in d]\n",
        # Several exits of one kind: two breaks.
        "def f(d):\n"
        "    for z in d:\n"
        "        try:\n"
        "            if z:\n"
        "                break\n"
        "            break\n"
        "        finally:\n"
        "            x = [k for k in d]\n",
        # A comprehension nested in a generator expression.
        "def f(d, lock):\n"
        "    try:\n"
        "        with lock:\n"
        "            return 1\n"
        "    finally:\n"
        "        x = sum(len([c for c in k]) for k in d)\n",
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
        "comprehension in a finally whose try has two or more kinds of exit "
        "(Nuitka 4.2.2 crashes with AssertionError: listcomp_1__.0_clone): "
        + ", ".join(violations)
    )
