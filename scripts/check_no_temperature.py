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
This check runs over ``app/memory_server/`` + ``memory/`` + ``utils/`` +
``nekoclaw/`` (the agent runtime, the host and the built-in tools) only.
Other parts of the codebase (``main_routers/``, plugin-specific LLM
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

Known static-analysis boundaries
--------------------------------
The unpacked-dict tracker is intentionally lightweight and best-effort. It
tracks local and module-level dict bindings, literals, ``dict(...)``,
``.copy()``, ``.update()``, ``.setdefault()``, ``.clear()``,
``.pop('temperature', ...)``, ``del d['temperature']``, static default
parameter dicts, walrus assignments, destructuring assignments, ``with`` target
bindings, import bindings, and structured control-flow merges.
It intentionally does NOT perform points-to alias analysis (e.g.
``alias = opts; alias['temperature'] = 0.2``), attribute-target tracking
(``self.opts[...]``), ``copy.copy`` / ``copy.deepcopy``, forward-declared
module dicts defined after a referencing function, mid-``try`` exception
states before a later ``.clear()``, conditionally-bound class-body fallback to globals,
short-circuit / conditional expression mutations (e.g.
``flag and opts.clear()``, ``opts.clear() if flag else None``), inline
dict-literal side effects mutating a source dict while unpacking it
(``{**opts, 'max_tokens': (opts.clear() or 100)}``), ``match``/``case``
branch isolation, or comprehension walrus bindings (``[o := {...} for ...]``).
External exception classes not declared in the same file (other than known
``BaseException`` subclasses such as ``CancelledError``) and bare ``raise``
inside bare ``except:`` are treated as ``Exception`` subclasses when checked
against ``except Exception`` / ``suppress(Exception)`` so third-party
exceptions do not trigger false positives; dynamic ``ExceptionGroup``
payloads whose member classes are not statically listed are handled
conservatively.

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
import builtins
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default scope: the memory subsystem, the helpers it relies on, and NekoClaw
# (runtime, host and built-in tools). See module docstring for rationale.
DEFAULT_PATHS: list[str] = ["app/memory_server", "memory", "utils", "nekoclaw"]

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

_VIOLATION_MSG = (
    "passing `temperature=...` to an LLM client / wrapper is forbidden — "
    "remove the kwarg entirely. Project policy lets the provider's own "
    "default decide; see memory/__init__.py and .agent/rules/neko-guide.md "
    "for the rationale."
)

_UNPACK_VIOLATION_MSG = (
    "unpacking `**...` containing `'temperature'` into an LLM client / wrapper "
    "is forbidden — remove the `'temperature'` entry entirely. Project policy "
    "lets the provider's own default decide; see memory/__init__.py and "
    ".agent/rules/neko-guide.md for the rationale."
)


def _is_dict_ctor(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "dict"
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "builtins"
        and func.attr == "dict"
    )


def _is_dict_fromkeys(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "dict"
        and func.attr == "fromkeys"
    )


def _pair_has_temperature(node: ast.AST) -> bool:
    return (
        isinstance(node, (ast.Tuple, ast.List))
        and len(node.elts) == 2
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "temperature"
    )


def _arg_names(args: ast.arguments) -> list[str]:
    names = [a.arg for a in args.posonlyargs]
    names.extend(a.arg for a in args.args)
    if args.vararg is not None:
        names.append(args.vararg.arg)
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return names


_KNOWN_NON_EXCEPTION_BASE_EXCEPTIONS = frozenset(
    {
        "BaseException",
        "BaseExceptionGroup",
        "GeneratorExit",
        "KeyboardInterrupt",
        "SystemExit",
        "CancelledError",
    }
)


def _raised_exc_name(exc: ast.expr | None) -> str | None:
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    if isinstance(exc, ast.Call):
        return _raised_exc_name(exc.func)
    return None


def _raised_exc_group_members(exc: ast.expr | None) -> tuple[str, ...] | None:
    if not isinstance(exc, ast.Call):
        return None
    if _raised_exc_name(exc.func) not in ("ExceptionGroup", "BaseExceptionGroup"):
        return None
    if len(exc.args) < 2 or not isinstance(exc.args[1], (ast.List, ast.Tuple)):
        return None
    members: list[str] = []
    for elt in exc.args[1].elts:
        name = _raised_exc_name(elt)
        if name is None:
            return None
        members.append(name)
    return tuple(members) if members else None


def _handler_exc_names(expr: ast.expr) -> set[str]:
    if isinstance(expr, ast.Name):
        return {expr.id}
    if isinstance(expr, ast.Attribute):
        return {expr.attr}
    if isinstance(expr, ast.Tuple):
        names: set[str] = set()
        for elt in expr.elts:
            names.update(_handler_exc_names(elt))
        return names
    return set()


def _collect_class_bases(tree: ast.AST) -> dict[str, set[str]]:
    bases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names: set[str] = set()
            for base in node.bases:
                names.update(_handler_exc_names(base))
            if names:
                bases.setdefault(node.name, set()).update(names)
    return bases


def _resolve_exc_ancestors(
    exc_name: str,
    class_bases: dict[str, set[str]],
) -> set[str]:
    ancestors: set[str] = set()
    visited: set[str] = {exc_name}
    stack: list[str] = [exc_name]
    while stack:
        curr = stack.pop()
        direct = class_bases.get(curr)
        if direct is not None:
            for parent in direct:
                ancestors.add(parent)
                if parent not in visited:
                    visited.add(parent)
                    stack.append(parent)
            continue
        cls = getattr(builtins, curr, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            for base_cls in cls.__mro__[1:]:
                if issubclass(base_cls, BaseException):
                    ancestors.add(base_cls.__name__)
    return ancestors


def _exc_matches_names(
    exc_name: str | None,
    handled_names: set[str],
    class_bases: dict[str, set[str]] | None = None,
) -> bool:
    if "BaseException" in handled_names:
        return True
    if exc_name is None:
        return "Exception" in handled_names
    if exc_name in handled_names:
        return True
    bases = class_bases or {}
    ancestors = _resolve_exc_ancestors(exc_name, bases)
    if ancestors & handled_names:
        return True
    if ((ancestors | {exc_name}) & _KNOWN_NON_EXCEPTION_BASE_EXCEPTIONS) and (
        "Exception" not in ancestors
    ):
        return False
    if exc_name not in bases:
        exc_cls = getattr(builtins, exc_name, None)
        if isinstance(exc_cls, type) and issubclass(exc_cls, BaseException):
            return False
    return "Exception" in handled_names


def _handler_catches_exc(
    handler: ast.ExceptHandler,
    exc_name: str | None,
    *,
    is_try_star: bool,
    class_bases: dict[str, set[str]] | None = None,
) -> bool:
    if handler.type is None:
        return True
    handled = _handler_exc_names(handler.type)
    if is_try_star:
        if exc_name == "BaseExceptionGroup":
            return "BaseException" in handled
        if exc_name == "ExceptionGroup":
            return "BaseException" in handled or "Exception" in handled
        return _exc_matches_names(exc_name, handled, class_bases)
    return _exc_matches_names(exc_name, handled, class_bases)


def _is_const_truthy(expr: ast.expr | None) -> bool:
    return isinstance(expr, ast.Constant) and bool(expr.value)


def _suppress_exc_names(expr: ast.expr) -> set[str] | None:
    if not isinstance(expr, ast.Call):
        return None
    func = expr.func
    is_suppress = (isinstance(func, ast.Name) and func.id == "suppress") or (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "contextlib"
        and func.attr == "suppress"
    )
    if not is_suppress:
        return None
    names: set[str] = set()
    for arg in expr.args:
        names.update(_handler_exc_names(arg))
    return names


class _LoopFrame:
    __slots__ = ("breaks", "continues")

    def __init__(self) -> None:
        self.breaks: list[dict[str, bool]] = []
        self.continues: list[dict[str, bool]] = []


class _TryBodyFrame:
    __slots__ = ("raises",)

    def __init__(self) -> None:
        self.raises: list[tuple[str | None, tuple[str, ...] | None, dict[str, bool]]] = []


class _TryFinallyFrame:
    __slots__ = ("breaks", "continues", "returns", "raises")

    def __init__(self) -> None:
        self.breaks: list[dict[str, bool]] = []
        self.continues: list[dict[str, bool]] = []
        self.returns: list[dict[str, bool]] = []
        self.raises: list[tuple[str | None, dict[str, bool]]] = []


class _WithSuppressFrame:
    __slots__ = ("suppressed_names", "suppressed_raises")

    def __init__(self, suppressed_names: set[str]) -> None:
        self.suppressed_names: set[str] = suppressed_names
        self.suppressed_raises: list[dict[str, bool]] = []


class TemperatureKwargChecker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: set[tuple[int, int, str]] = set()
        self.scopes: list[dict[str, bool]] = [{}]
        self._scope_is_class: list[bool] = [False]
        self._cf_stacks: list[
            list[_LoopFrame | _TryBodyFrame | _TryFinallyFrame | _WithSuppressFrame]
        ] = [[]]
        self._active_exc_names: list[list[set[str] | None]] = [[]]
        self._class_bases: dict[str, set[str]] = {}

    def visit_Module(self, node: ast.Module) -> None:
        self._class_bases = _collect_class_bases(node)
        self.generic_visit(node)

    def _push_scope(
        self,
        initial: dict[str, bool] | None = None,
        *,
        is_class: bool = False,
    ) -> None:
        self.scopes.append(dict(initial) if initial else {})
        self._scope_is_class.append(is_class)
        self._cf_stacks.append([])
        self._active_exc_names.append([])

    def _pop_scope(self) -> None:
        self.scopes.pop()
        self._scope_is_class.pop()
        self._cf_stacks.pop()
        self._active_exc_names.pop()

    def _record_jump(
        self,
        kind: str,
        snapshot: dict[str, bool],
        exc_name: str | None = None,
        group_members: tuple[str, ...] | None = None,
    ) -> None:
        for frame in reversed(self._cf_stacks[-1]):
            if isinstance(frame, _WithSuppressFrame):
                if kind == "raise" and _exc_matches_names(
                    exc_name, frame.suppressed_names, self._class_bases
                ):
                    frame.suppressed_raises.append(snapshot)
                    return
                continue
            if isinstance(frame, _TryBodyFrame):
                if kind == "raise":
                    frame.raises.append((exc_name, group_members, snapshot))
                    return
                continue
            if isinstance(frame, _TryFinallyFrame):
                if kind == "break":
                    frame.breaks.append(snapshot)
                elif kind == "continue":
                    frame.continues.append(snapshot)
                elif kind == "return":
                    frame.returns.append(snapshot)
                elif kind == "raise":
                    frame.raises.append((exc_name, snapshot))
                return
            if isinstance(frame, _LoopFrame):
                if kind == "break":
                    frame.breaks.append(snapshot)
                    return
                if kind == "continue":
                    frame.continues.append(snapshot)
                    return

    def _lookup(self, name: str) -> bool:
        top_idx = len(self.scopes) - 1
        for idx in range(top_idx, -1, -1):
            if idx != top_idx and self._scope_is_class[idx]:
                continue
            scope = self.scopes[idx]
            if name in scope:
                return scope[name]
        return False

    def _set_local(self, name: str, value: bool) -> None:
        self.scopes[-1][name] = value

    def _add_violation(self, node: ast.AST, fallback: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", None) or getattr(fallback, "lineno", 1)
        col = getattr(node, "col_offset", None)
        if col is None:
            col = getattr(fallback, "col_offset", 0)
        self.violations.add((lineno, col + 1, message))

    def _pairs_contain_temperature(self, expr: ast.AST) -> bool:
        if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
            return any(_pair_has_temperature(elt) for elt in expr.elts)
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return _pair_has_temperature(expr.elt)
        return False

    def _expr_contains_temperature(self, expr: ast.AST) -> bool:
        if isinstance(expr, ast.Name):
            return self._lookup(expr.id)
        if isinstance(expr, ast.Dict):
            for key, value in zip(expr.keys, expr.values):
                if key is None:
                    if self._expr_contains_temperature(value):
                        return True
                elif isinstance(key, ast.Constant) and key.value == "temperature":
                    return True
            return False
        if isinstance(expr, ast.DictComp):
            return isinstance(expr.key, ast.Constant) and expr.key.value == "temperature"
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self._expr_contains_temperature(expr.left) or self._expr_contains_temperature(
                expr.right
            )
        if isinstance(expr, ast.IfExp):
            return self._expr_contains_temperature(expr.body) or self._expr_contains_temperature(
                expr.orelse
            )
        if isinstance(expr, ast.BoolOp):
            return any(self._expr_contains_temperature(v) for v in expr.values)
        if isinstance(expr, ast.NamedExpr):
            return self._expr_contains_temperature(expr.value)
        if isinstance(expr, ast.Call):
            if _is_dict_ctor(expr.func):
                for kw in expr.keywords:
                    if kw.arg == "temperature":
                        return True
                    if kw.arg is None and self._expr_contains_temperature(kw.value):
                        return True
                if expr.args:
                    arg0 = expr.args[0]
                    if self._expr_contains_temperature(arg0) or self._pairs_contain_temperature(arg0):
                        return True
                return False
            if _is_dict_fromkeys(expr.func) and expr.args:
                arg0 = expr.args[0]
                if isinstance(arg0, (ast.List, ast.Tuple, ast.Set)):
                    return any(
                        isinstance(elt, ast.Constant) and elt.value == "temperature"
                        for elt in arg0.elts
                    )
            if (
                isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "copy"
                and not expr.args
                and not expr.keywords
            ):
                return self._expr_contains_temperature(expr.func.value)
        return False

    def _visit_stmt(self, stmt: ast.stmt) -> bool:
        res = self.visit(stmt)
        return True if res is None else bool(res)

    def _visit_stmt_list(self, stmts: list[ast.stmt]) -> bool:
        for idx, stmt in enumerate(stmts):
            if not self._visit_stmt(stmt):
                if idx + 1 < len(stmts):
                    saved_scope = self.scopes[-1]
                    self.scopes[-1] = dict(saved_scope)
                    self._cf_stacks.append([])
                    try:
                        self._visit_stmt_list(stmts[idx + 1 :])
                    finally:
                        self._cf_stacks.pop()
                        self.scopes[-1] = saved_scope
                return False
        return True

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            self._set_local(bound, False)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                bound = alias.asname or alias.name
                self._set_local(bound, False)

    def visit_Return(self, node: ast.Return) -> bool:
        if node.value is not None:
            self.visit(node.value)
        self._record_jump("return", dict(self.scopes[-1]))
        return False

    def visit_Raise(self, node: ast.Raise) -> bool:
        if node.exc is not None:
            self.visit(node.exc)
        if node.cause is not None:
            self.visit(node.cause)
        snap = dict(self.scopes[-1])
        if node.exc is not None:
            self._record_jump(
                "raise",
                snap,
                exc_name=_raised_exc_name(node.exc),
                group_members=_raised_exc_group_members(node.exc),
            )
        else:
            active = (
                self._active_exc_names[-1][-1] if self._active_exc_names[-1] else None
            )
            if active:
                for exc_name in sorted(active):
                    self._record_jump("raise", snap, exc_name=exc_name)
            else:
                self._record_jump("raise", snap, exc_name=None)
        return False

    def visit_Break(self, node: ast.Break) -> bool:
        self._record_jump("break", dict(self.scopes[-1]))
        return False

    def visit_Continue(self, node: ast.Continue) -> bool:
        self._record_jump("continue", dict(self.scopes[-1]))
        return False

    def _visit_with_node(self, node: ast.With | ast.AsyncWith) -> bool:
        suppressed_names: set[str] = set()
        for item in node.items:
            self.visit(item.context_expr)
            names = _suppress_exc_names(item.context_expr)
            if names:
                suppressed_names.update(names)
            if item.optional_vars is not None:
                self._apply_assign_target(item.optional_vars, False)
        suppress_frame = (
            _WithSuppressFrame(suppressed_names) if suppressed_names else None
        )
        if suppress_frame is not None:
            self._cf_stacks[-1].append(suppress_frame)
        try:
            body_falls = self._visit_stmt_list(node.body)
        finally:
            if suppress_frame is not None:
                self._cf_stacks[-1].pop()
        if suppress_frame is not None and suppress_frame.suppressed_raises:
            exits = list(suppress_frame.suppressed_raises)
            if body_falls:
                exits.append(dict(self.scopes[-1]))
            self.scopes[-1] = self._merge_scopes(exits)
            return True
        return body_falls

    def visit_With(self, node: ast.With) -> bool:
        return self._visit_with_node(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> bool:
        return self._visit_with_node(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _initial_param_states(self, args: ast.arguments) -> dict[str, bool]:
        params = {name: False for name in _arg_names(args)}
        positional = list(args.posonlyargs) + list(args.args)
        if args.defaults:
            offset = len(positional) - len(args.defaults)
            for arg, default in zip(positional[offset:], args.defaults):
                params[arg.arg] = self._expr_contains_temperature(default)
        for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
            if kw_default is not None:
                params[arg.arg] = self._expr_contains_temperature(kw_default)
        return params

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            *([node.args.vararg] if node.args.vararg is not None else ()),
            *node.args.kwonlyargs,
            *([node.args.kwarg] if node.args.kwarg is not None else ()),
        ):
            if arg.annotation is not None:
                self.visit(arg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for default in node.args.defaults:
            self.visit(default)
        for kw_default in node.args.kw_defaults:
            if kw_default is not None:
                self.visit(kw_default)
        params = self._initial_param_states(node.args)
        self._push_scope(params, is_class=False)
        try:
            self._visit_stmt_list(node.body)
        finally:
            self._pop_scope()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in node.args.defaults:
            self.visit(default)
        for kw_default in node.args.kw_defaults:
            if kw_default is not None:
                self.visit(kw_default)
        params = self._initial_param_states(node.args)
        self._push_scope(params, is_class=False)
        try:
            self.visit(node.body)
        finally:
            self._pop_scope()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw.value)
        self._push_scope(is_class=True)
        try:
            self._visit_stmt_list(node.body)
        finally:
            self._pop_scope()

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        *exprs: ast.expr,
    ) -> None:
        if generators:
            self.visit(generators[0].iter)
        self._push_scope(is_class=False)
        try:
            for idx, comp in enumerate(generators):
                if idx > 0:
                    self.visit(comp.iter)
                self._apply_assign_target(comp.target, False)
                for if_expr in comp.ifs:
                    self.visit(if_expr)
            for expr in exprs:
                self.visit(expr)
        finally:
            self._pop_scope()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, node.key, node.value)

    def _eval_rhs(self, expr: ast.AST) -> bool | tuple[Any, ...]:
        if isinstance(expr, (ast.Tuple, ast.List)) and not any(
            isinstance(elt, ast.Starred) for elt in expr.elts
        ):
            return tuple(self._eval_rhs(elt) for elt in expr.elts)
        return self._expr_contains_temperature(expr)

    def _apply_assign_target(
        self, target: ast.AST, val: bool | tuple[Any, ...]
    ) -> None:
        if isinstance(target, ast.Name):
            self._set_local(target.id, val if isinstance(val, bool) else False)
        elif isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)
            if (
                isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "temperature"
            ):
                self._set_local(target.value.id, True)
        elif isinstance(target, (ast.Tuple, ast.List)):
            star_indices = [
                idx
                for idx, elt in enumerate(target.elts)
                if isinstance(elt, ast.Starred)
            ]
            if (
                isinstance(val, tuple)
                and not star_indices
                and len(target.elts) == len(val)
            ):
                for sub_target, sub_val in zip(target.elts, val):
                    self._apply_assign_target(sub_target, sub_val)
            elif (
                isinstance(val, tuple)
                and len(star_indices) == 1
                and len(val) >= len(target.elts) - 1
            ):
                star_idx = star_indices[0]
                trailing = len(target.elts) - star_idx - 1
                mid_end = len(val) - trailing
                for sub_target, sub_val in zip(target.elts[:star_idx], val[:star_idx]):
                    self._apply_assign_target(sub_target, sub_val)
                star_node = target.elts[star_idx]
                assert isinstance(star_node, ast.Starred)
                self._apply_assign_target(star_node.value, val[star_idx:mid_end])
                if trailing:
                    for sub_target, sub_val in zip(
                        target.elts[star_idx + 1 :], val[mid_end:]
                    ):
                        self._apply_assign_target(sub_target, sub_val)
            else:
                for sub_target in target.elts:
                    self._apply_assign_target(sub_target, False)
        elif isinstance(target, ast.Starred):
            self._apply_assign_target(target.value, False)
        else:
            self.visit(target)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        val = self._eval_rhs(node.value)
        for target in node.targets:
            self._apply_assign_target(target, val)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            val = self._eval_rhs(node.value)
            self._apply_assign_target(node.target, val)
        elif not isinstance(node.target, ast.Name):
            self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        if (
            isinstance(node.op, ast.BitOr)
            and isinstance(node.target, ast.Name)
            and self._expr_contains_temperature(node.value)
        ):
            self._set_local(node.target.id, True)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._set_local(node.target.id, self._expr_contains_temperature(node.value))

    def _apply_delete_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)
            if (
                isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "temperature"
            ):
                self._set_local(target.value.id, False)
        elif isinstance(target, ast.Name):
            self._set_local(target.id, False)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for sub_target in target.elts:
                self._apply_delete_target(sub_target)
        else:
            self.visit(target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._apply_delete_target(target)

    def _apply_dict_method_mutation(self, call: ast.Call) -> None:
        if not (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
        ):
            return
        target_name = call.func.value.id
        method = call.func.attr
        if method == "setdefault" and call.args:
            arg0 = call.args[0]
            if isinstance(arg0, ast.Constant) and arg0.value == "temperature":
                self._set_local(target_name, True)
        elif method == "update":
            has_temp = any(
                kw.arg == "temperature"
                or (kw.arg is None and self._expr_contains_temperature(kw.value))
                for kw in call.keywords
            )
            if not has_temp and call.args:
                arg0 = call.args[0]
                has_temp = self._expr_contains_temperature(
                    arg0
                ) or self._pairs_contain_temperature(arg0)
            if has_temp:
                self._set_local(target_name, True)
        elif method == "clear" and not call.args and not call.keywords:
            self._set_local(target_name, False)
        elif method == "pop" and call.args and not call.keywords:
            arg0 = call.args[0]
            if isinstance(arg0, ast.Constant) and arg0.value == "temperature":
                self._set_local(target_name, False)

    @staticmethod
    def _merge_scopes(snapshots: list[dict[str, bool]]) -> dict[str, bool]:
        merged: dict[str, bool] = {}
        keys: set[str] = set()
        for snap in snapshots:
            keys.update(snap.keys())
        for key in keys:
            merged[key] = any(snap.get(key, False) for snap in snapshots)
        return merged

    def visit_If(self, node: ast.If) -> bool:
        fallthrough: list[dict[str, bool]] = []
        all_exits: list[dict[str, bool]] = []
        current = node
        while True:
            self.visit(current.test)
            before = dict(self.scopes[-1])
            body_falls = self._visit_stmt_list(current.body)
            after_body = dict(self.scopes[-1])
            all_exits.append(after_body)
            if body_falls:
                fallthrough.append(after_body)
            self.scopes[-1] = dict(before)
            if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                current = current.orelse[0]
                continue
            else_falls = self._visit_stmt_list(current.orelse)
            after_else = dict(self.scopes[-1])
            all_exits.append(after_else)
            if else_falls:
                fallthrough.append(after_else)
            break
        if fallthrough:
            self.scopes[-1] = self._merge_scopes(fallthrough)
            return True
        self.scopes[-1] = self._merge_scopes(all_exits)
        return False

    def _run_finalbody_silenced(
        self,
        finalbody: list[ast.stmt],
        entry: dict[str, bool],
        cache: dict[tuple[tuple[str, bool], ...], tuple[dict[str, bool], bool]],
    ) -> tuple[dict[str, bool], bool]:
        key = tuple(sorted(entry.items()))
        if key in cache:
            return cache[key]
        saved_violations = self.violations
        saved_scope = self.scopes[-1]
        self.violations = set()
        self.scopes[-1] = dict(entry)
        self._cf_stacks.append([])
        try:
            falls = self._visit_stmt_list(finalbody)
            after = dict(self.scopes[-1])
        finally:
            self._cf_stacks.pop()
            self.scopes[-1] = saved_scope
            self.violations = saved_violations
        result = (after, falls)
        cache[key] = result
        return result

    @staticmethod
    def _active_handler_exc_names(
        handler: ast.ExceptHandler,
        *,
        is_try_star: bool,
    ) -> set[str] | None:
        if handler.type is None:
            return None
        handled = _handler_exc_names(handler.type)
        if is_try_star:
            return {"BaseExceptionGroup"} if "BaseException" in handled else {"ExceptionGroup"}
        return handled or None

    def _run_handler_silenced(
        self,
        handler: ast.ExceptHandler,
        entry: dict[str, bool],
        *,
        is_try_star: bool,
    ) -> dict[str, bool]:
        saved_violations = self.violations
        saved_scope = self.scopes[-1]
        self.violations = set()
        self.scopes[-1] = dict(entry)
        self._cf_stacks.append([])
        active_exc = self._active_handler_exc_names(handler, is_try_star=is_try_star)
        self._active_exc_names[-1].append(active_exc)
        try:
            if handler.name:
                self._set_local(handler.name, False)
            self._visit_stmt_list(handler.body)
            return dict(self.scopes[-1])
        finally:
            self._active_exc_names[-1].pop()
            self._cf_stacks.pop()
            self.scopes[-1] = saved_scope
            self.violations = saved_violations

    def _visit_try_node(self, node: ast.Try) -> bool:
        before = dict(self.scopes[-1])
        is_try_star = hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)
        finally_frame = _TryFinallyFrame() if node.finalbody else None
        if finally_frame is not None:
            self._cf_stacks[-1].append(finally_frame)
        all_snapshots: list[dict[str, bool]] = []
        fallthrough: list[dict[str, bool]] = []
        try:
            body_frame = _TryBodyFrame() if node.handlers else None
            if body_frame is not None:
                self._cf_stacks[-1].append(body_frame)
            try:
                body_falls = self._visit_stmt_list(node.body)
            finally:
                if body_frame is not None:
                    self._cf_stacks[-1].pop()
            after_body = dict(self.scopes[-1])
            body_raise_snaps: list[dict[str, bool]] = []
            if body_frame is not None:
                for exc_name, _members, snap in body_frame.raises:
                    body_raise_snaps.append(snap)
                    if not is_try_star and not any(
                        _handler_catches_exc(
                            h, exc_name, is_try_star=False, class_bases=self._class_bases
                        )
                        for h in node.handlers
                    ):
                        self._record_jump("raise", snap, exc_name=exc_name)
            handler_entry = self._merge_scopes([before, after_body, *body_raise_snaps])
            star_entry = dict(handler_entry)
            for handler in node.handlers:
                self.scopes[-1] = dict(star_entry if is_try_star else handler_entry)
                if handler.type is not None:
                    self.visit(handler.type)
                if handler.name:
                    self._set_local(handler.name, False)
                active_exc = self._active_handler_exc_names(
                    handler, is_try_star=is_try_star
                )
                self._active_exc_names[-1].append(active_exc)
                try:
                    handler_falls = self._visit_stmt_list(handler.body)
                finally:
                    self._active_exc_names[-1].pop()
                snap = dict(self.scopes[-1])
                all_snapshots.append(snap)
                if handler_falls:
                    fallthrough.append(snap)
                    if is_try_star:
                        star_entry = self._merge_scopes([star_entry, snap])
            if is_try_star and body_frame is not None:
                for exc_name, members, snap in body_frame.raises:
                    if members is not None:
                        remaining = list(members)
                        post_snap = dict(snap)
                        for handler in node.handlers:
                            matched = [
                                m
                                for m in remaining
                                if _handler_catches_exc(
                                    handler,
                                    m,
                                    is_try_star=False,
                                    class_bases=self._class_bases,
                                )
                            ]
                            if matched:
                                remaining = [m for m in remaining if m not in matched]
                                post_snap = self._run_handler_silenced(
                                    handler, post_snap, is_try_star=True
                                )
                        if remaining:
                            self._record_jump("raise", post_snap, exc_name=exc_name)
                    elif not any(
                        _handler_catches_exc(
                            h, exc_name, is_try_star=True, class_bases=self._class_bases
                        )
                        for h in node.handlers
                    ):
                        self._record_jump("raise", snap, exc_name=exc_name)
            if node.orelse:
                if body_falls:
                    self.scopes[-1] = dict(after_body)
                    orelse_falls = self._visit_stmt_list(node.orelse)
                    snap = dict(self.scopes[-1])
                    all_snapshots.append(snap)
                    if orelse_falls:
                        fallthrough.append(snap)
                else:
                    saved_scope = self.scopes[-1]
                    self.scopes[-1] = dict(after_body)
                    self._cf_stacks.append([])
                    try:
                        self._visit_stmt_list(node.orelse)
                    finally:
                        self._cf_stacks.pop()
                        self.scopes[-1] = saved_scope
            else:
                all_snapshots.append(after_body)
                if body_falls:
                    fallthrough.append(after_body)
        finally:
            if finally_frame is not None:
                self._cf_stacks[-1].pop()

        normal_falls = bool(fallthrough)
        after_try = self._merge_scopes(fallthrough or all_snapshots)
        if not node.finalbody:
            self.scopes[-1] = after_try
            return normal_falls

        assert finally_frame is not None
        all_finally_entries: list[dict[str, bool]] = list(fallthrough)
        all_finally_entries.extend(finally_frame.breaks)
        all_finally_entries.extend(finally_frame.continues)
        all_finally_entries.extend(finally_frame.returns)
        all_finally_entries.extend(snap for _, snap in finally_frame.raises)
        if not all_finally_entries:
            all_finally_entries = list(all_snapshots)
        finally_check_entry = self._merge_scopes(all_finally_entries)

        self.scopes[-1] = dict(finally_check_entry)
        final_falls = self._visit_stmt_list(node.finalbody)
        after_pass1 = dict(self.scopes[-1])
        cache: dict[tuple[tuple[str, bool], ...], tuple[dict[str, bool], bool]] = {
            tuple(sorted(finally_check_entry.items())): (after_pass1, final_falls)
        }

        if self._cf_stacks[-1]:
            for kind, snaps in (
                ("break", finally_frame.breaks),
                ("continue", finally_frame.continues),
                ("return", finally_frame.returns),
            ):
                if snaps:
                    kind_entry = self._merge_scopes(snaps)
                    after_kind, kind_final_falls = self._run_finalbody_silenced(
                        node.finalbody, kind_entry, cache
                    )
                    if kind_final_falls:
                        self._record_jump(kind, after_kind)
            if finally_frame.raises:
                raises_by_exc: dict[str | None, list[dict[str, bool]]] = {}
                for exc_name, snap in finally_frame.raises:
                    raises_by_exc.setdefault(exc_name, []).append(snap)
                for exc_name, snaps in raises_by_exc.items():
                    kind_entry = self._merge_scopes(snaps)
                    after_kind, kind_final_falls = self._run_finalbody_silenced(
                        node.finalbody, kind_entry, cache
                    )
                    if kind_final_falls:
                        self._record_jump("raise", after_kind, exc_name=exc_name)

        if normal_falls:
            after_normal, normal_final_falls = self._run_finalbody_silenced(
                node.finalbody, after_try, cache
            )
            self.scopes[-1] = after_normal
            return normal_final_falls

        self.scopes[-1] = after_pass1
        return False

    def visit_Try(self, node: ast.Try) -> bool:
        return self._visit_try_node(node)

    if hasattr(ast, "TryStar"):

        def visit_TryStar(self, node: ast.TryStar) -> bool:  # type: ignore[name-defined]
            return self._visit_try_node(node)  # type: ignore[arg-type]

    def _visit_loop(
        self,
        body: list[ast.stmt],
        orelse: list[ast.stmt],
        test: ast.expr | None = None,
        target: ast.AST | None = None,
    ) -> bool:
        state = dict(self.scopes[-1])
        merged_breaks: dict[str, bool] | None = None
        normal_exit = dict(state)
        iters = 0
        while True:
            iters += 1
            self.scopes[-1] = dict(state)
            if test is not None:
                self.visit(test)
                after_test = dict(self.scopes[-1])
            else:
                after_test = None
            if target is not None:
                self._apply_assign_target(target, False)
            loop_frame = _LoopFrame()
            self._cf_stacks[-1].append(loop_frame)
            try:
                body_falls = self._visit_stmt_list(body)
            finally:
                self._cf_stacks[-1].pop()
            if loop_frame.breaks:
                merged_breaks = self._merge_scopes(
                    ([merged_breaks] if merged_breaks is not None else [])
                    + loop_frame.breaks
                )
            next_candidates = [state]
            if body_falls:
                next_candidates.append(dict(self.scopes[-1]))
            next_candidates.extend(loop_frame.continues)
            next_state = self._merge_scopes(next_candidates)
            if next_state == state or iters >= max(len(next_state) + 64, 256):
                normal_exit = after_test if after_test is not None else next_state
                break
            state = next_state
        if _is_const_truthy(test):
            if orelse:
                saved_scope = self.scopes[-1]
                self.scopes[-1] = dict(normal_exit)
                self._cf_stacks.append([])
                try:
                    self._visit_stmt_list(orelse)
                finally:
                    self._cf_stacks.pop()
                    self.scopes[-1] = saved_scope
            if merged_breaks is not None:
                self.scopes[-1] = dict(merged_breaks)
                return True
            self.scopes[-1] = dict(normal_exit)
            return False
        self.scopes[-1] = dict(normal_exit)
        if orelse:
            orelse_falls = self._visit_stmt_list(orelse)
            exits: list[dict[str, bool]] = []
            if merged_breaks is not None:
                exits.append(merged_breaks)
            if orelse_falls:
                exits.append(dict(self.scopes[-1]))
            if exits:
                self.scopes[-1] = self._merge_scopes(exits)
                return True
            self.scopes[-1] = normal_exit
            return False
        if merged_breaks is not None:
            self.scopes[-1] = self._merge_scopes([normal_exit, merged_breaks])
        return True

    def visit_For(self, node: ast.For) -> bool:
        self.visit(node.iter)
        return self._visit_loop(node.body, node.orelse, target=node.target)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> bool:
        self.visit(node.iter)
        return self._visit_loop(node.body, node.orelse, target=node.target)

    def visit_While(self, node: ast.While) -> bool:
        return self._visit_loop(node.body, node.orelse, test=node.test)

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)
            if kw.arg == "temperature":
                self._add_violation(kw.value, node, _VIOLATION_MSG)
            elif kw.arg is None and self._expr_contains_temperature(kw.value):
                self._add_violation(kw.value, node, _UNPACK_VIOLATION_MSG)
        self._apply_dict_method_mutation(node)


def _relative_to_root(path: Path, scan_root: Path | None) -> Path | None:
    for root in (REPO_ROOT, scan_root):
        if root is None:
            continue
        try:
            rel = path.relative_to(root)
            if ".." not in rel.parts:
                return Path(path.name) if root == scan_root and rel == Path(".") else rel
        except ValueError:
            pass
        try:
            rel = path.resolve().relative_to(root.resolve())
            return Path(path.name) if root == scan_root and rel == Path(".") else rel
        except (ValueError, OSError, RuntimeError):
            pass
    return None


def _is_excluded(path: Path, scan_root: Path | None = None) -> bool:
    """True if any relative path component matches EXCLUDE_DIRS, or the relative
    posix path matches EXCLUDE_FILES."""
    rel_path = _relative_to_root(path, scan_root)
    if rel_path is not None:
        parts = set(rel_path.parts)
        rel = rel_path.as_posix()
    else:
        parts = set(path.parts)
        rel = path.as_posix()
    if parts & EXCLUDE_DIRS:
        return True
    if rel in EXCLUDE_FILES:
        return True
    for ex in EXCLUDE_DIRS:
        if "/" in ex and (rel == ex or rel.startswith(ex + "/")):
            return True
    return False


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if p.suffix == ".py" and not _is_excluded(p, scan_root=p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if not _is_excluded(f, scan_root=p):
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
    return sorted(checker.violations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid `temperature=...` kwargs in LLM client / wrapper calls."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: app/memory_server/ + memory/ + utils/).",
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
