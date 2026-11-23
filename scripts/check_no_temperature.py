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
parameter dicts, walrus assignments, and structured control-flow merges.
It intentionally does NOT perform points-to alias analysis (e.g.
``alias = opts; alias['temperature'] = 0.2``), attribute-target tracking
(``self.opts[...]``), ``copy.copy`` / ``copy.deepcopy``, forward-declared
module dicts defined after a referencing function, mid-``try`` exception
states before a later ``.clear()``, ``continue`` before an unreachable
``.clear()``, or conditionally-bound class-body fallback to globals.

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


def _extract_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_extract_target_names(elt))
        return names
    if isinstance(target, ast.Starred):
        return _extract_target_names(target.value)
    return []


class TemperatureKwargChecker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: set[tuple[int, int, str]] = set()
        self.scopes: list[dict[str, bool]] = [{}]
        self._scope_is_class: list[bool] = [False]

    def _push_scope(
        self,
        initial: dict[str, bool] | None = None,
        *,
        is_class: bool = False,
    ) -> None:
        self.scopes.append(dict(initial) if initial else {})
        self._scope_is_class.append(is_class)

    def _pop_scope(self) -> None:
        self.scopes.pop()
        self._scope_is_class.pop()

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

    def _visit_stmt_list(self, stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            self.visit(stmt)

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
                self.visit(comp.target)
                for name in _extract_target_names(comp.target):
                    self._set_local(name, False)
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

    def _apply_assign_target(self, target: ast.AST, val: bool) -> None:
        if isinstance(target, ast.Name):
            self._set_local(target.id, val)
        elif isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)
            if (
                isinstance(target.value, ast.Name)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "temperature"
            ):
                self._set_local(target.value.id, True)
        else:
            self.visit(target)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        val = self._expr_contains_temperature(node.value)
        for target in node.targets:
            self._apply_assign_target(target, val)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            val = self._expr_contains_temperature(node.value)
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

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
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
            else:
                self.visit(target)

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

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = dict(self.scopes[-1])
        self._visit_stmt_list(node.body)
        after_body = dict(self.scopes[-1])
        self.scopes[-1] = dict(before)
        self._visit_stmt_list(node.orelse)
        after_else = dict(self.scopes[-1])
        self.scopes[-1] = self._merge_scopes([after_body, after_else])

    def _visit_try_node(self, node: ast.Try) -> None:
        before = dict(self.scopes[-1])
        self._visit_stmt_list(node.body)
        after_body = dict(self.scopes[-1])
        snapshots: list[dict[str, bool]] = []
        for handler in node.handlers:
            self.scopes[-1] = self._merge_scopes([before, after_body])
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name:
                self._set_local(handler.name, False)
            self._visit_stmt_list(handler.body)
            snapshots.append(dict(self.scopes[-1]))
        if node.orelse:
            self.scopes[-1] = dict(after_body)
            self._visit_stmt_list(node.orelse)
            snapshots.append(dict(self.scopes[-1]))
        else:
            snapshots.append(after_body)
        self.scopes[-1] = self._merge_scopes(snapshots)
        self._visit_stmt_list(node.finalbody)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try_node(node)

    if hasattr(ast, "TryStar"):

        def visit_TryStar(self, node: ast.TryStar) -> None:  # type: ignore[name-defined]
            self._visit_try_node(node)  # type: ignore[arg-type]

    def _visit_loop(
        self,
        body: list[ast.stmt],
        orelse: list[ast.stmt],
        test: ast.expr | None = None,
        target: ast.AST | None = None,
    ) -> None:
        before = dict(self.scopes[-1])
        target_names = _extract_target_names(target) if target is not None else []
        state = dict(before)
        iters = 0
        while True:
            iters += 1
            self.scopes[-1] = dict(state)
            if target is not None:
                self.visit(target)
            for name in target_names:
                self._set_local(name, False)
            if test is not None:
                self.visit(test)
            self._visit_stmt_list(body)
            next_state = self._merge_scopes([before, dict(self.scopes[-1])])
            if next_state == state or iters >= max(len(next_state) + 64, 256):
                self.scopes[-1] = next_state
                break
            state = next_state
        if orelse:
            after_loop = dict(self.scopes[-1])
            self._visit_stmt_list(orelse)
            self.scopes[-1] = self._merge_scopes([after_loop, dict(self.scopes[-1])])

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._visit_loop(node.body, node.orelse, target=node.target)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._visit_loop(node.body, node.orelse, target=node.target)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node.body, node.orelse, test=node.test)

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
