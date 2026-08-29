"""Every host callback in the raw receive loop must re-check connection ownership.

``handle_messages`` awaits host callbacks all the way through its dispatch.
Each of those awaits is a point where a replacement connection can attach, and
from then on every later step of that iteration belongs to the successor -- so
each one is followed by ``if await retire_if_replaced(): return``.

That rule is invisible to behavioural tests: catching a violation needs a
replacement landing on one exact await, and there are a dozen of them. A
missing guard is therefore silent, and the failure it causes -- a retired
connection's event finalizing the successor's turn -- surfaces only under real
reconnect traffic.

So this DISCOVERS the call sites by walking the AST instead of listing them: an
``await self.on_*`` added later is covered the day it lands, which a hand-kept
inventory would not be.
"""

import ast
import pathlib

import pytest

_TRANSPORT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "main_logic"
    / "omni_realtime_client"
    / "_transport.py"
)
_GUARD = "retire_if_replaced"
# Every host callback observed when this guard was written. A drop below this
# means the loop was restructured and the rule needs re-deriving, not relaxing.
_MIN_HOST_AWAITS = 12


def _find_function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_TRANSPORT.name}")


def _awaited_host_callback(stmt: ast.stmt) -> str | None:
    """``await self.on_xxx(...)`` or ``await extra_event_handlers[...](...)``."""

    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Await):
        return None
    call = stmt.value.value
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
        and func.attr.startswith("on_")
    ):
        return func.attr
    if isinstance(func, ast.Subscript):
        return "extra_event_handlers"
    return None


def _is_guard(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.If)
        and isinstance(stmt.test, ast.Await)
        and isinstance(stmt.test.value, ast.Call)
        and getattr(stmt.test.value.func, "id", None) == _GUARD
    )


def _index_statements(function: ast.AST):
    """Map each statement to (its sequence, index) and to its parent statement."""

    location: dict[int, tuple[list, int]] = {}
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(node, field, None)
            if not isinstance(seq, list):
                continue
            for index, stmt in enumerate(seq):
                location[id(stmt)] = (seq, index)
                parent[id(stmt)] = node
    return location, parent


def _is_guarded(stmt: ast.stmt, location: dict, parent: dict) -> bool:
    """Guarded directly, or by a guard following the block that encloses it.

    The second form is load-bearing rather than a loophole: ``on_new_message``
    is the last statement of a ``try`` whose ``finally`` arms a timeout, so its
    guard sits after the whole ``Try``. Requiring the direct form would force
    that one call site to re-check twice.
    """

    node: ast.AST | None = stmt
    while node is not None:
        seq, index = location.get(id(node), (None, None))
        if seq is None:
            return False
        if index + 1 < len(seq):
            return _is_guard(seq[index + 1])
        node = parent.get(id(node))
    return False


@pytest.mark.unit
def test_every_host_callback_in_the_receive_loop_rechecks_ownership() -> None:
    tree = ast.parse(_TRANSPORT.read_text(encoding="utf-8"))
    loop = _find_function(tree, "handle_messages")
    location, parent = _index_statements(loop)

    host_awaits: list[tuple[ast.stmt, str]] = []
    for node in ast.walk(loop):
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(node, field, None)
            if not isinstance(seq, list):
                continue
            for stmt in seq:
                hook = _awaited_host_callback(stmt)
                if hook is not None:
                    host_awaits.append((stmt, hook))

    assert len(host_awaits) >= _MIN_HOST_AWAITS, (
        "handle_messages stopped awaiting host callbacks the way this guard "
        f"assumes (found {len(host_awaits)}, expected at least "
        f"{_MIN_HOST_AWAITS}); re-derive the rule rather than lowering it"
    )

    unguarded = [
        f"{hook} at line {stmt.lineno}"
        for stmt, hook in host_awaits
        if not _is_guarded(stmt, location, parent)
    ]
    assert unguarded == [], (
        "a host callback in handle_messages is not followed by "
        f"`if await {_GUARD}(): return` -- a replacement connection attaching "
        "during it would finalize the successor's turn: " + ", ".join(unguarded)
    )
