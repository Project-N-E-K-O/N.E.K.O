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

"""Structural and late-binding facade contracts for ``nekopower/host/task_agents/``.

Guards the mixin package split of ``TaskAgentManager`` so test monkeypatches on
``nekopower.host.task_agents.<attr>`` cannot silently snapshot stale imports,
mixin methods stay disjoint with ``TreeWork`` and ``TaskAgentManager``, and the
compatibility facade stays intact.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_DIR = REPO_ROOT / "nekopower" / "host" / "task_agents"
TREE_WORK_PATH = REPO_ROOT / "nekopower" / "host" / "tree_work.py"
TESTS_DIR = REPO_ROOT / "tests"

FACADE_MODULE_PATH = "nekopower.host.task_agents"
FACADE_ALIAS = "_task_agents_facade"
OWNER_SUBMODULES = frozenset({"types"})
EXPECTED_MIXIN_MODULES = frozenset({
    "billing_usage",
    "callbacks",
    "delegation",
    "forks_beside",
    "idle_compact",
    "messaging",
    "opening",
})
_PATCH_CALL_NAMES = frozenset({"setattr", "delattr", "patch", "object"})


def _parse_file(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes(), filename=str(path))


def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _call_has_absent_opt_out(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "raising" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
        if kw.arg == "create" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _harvest_facade_patch_targets(tests_root: Path) -> tuple[dict[str, set[str]], int]:
    """AST-harvest every attribute tests patch on ``nekopower.host.task_agents``."""
    targets: dict[str, set[str]] = {}
    scanned = 0
    for path in sorted(tests_root.rglob("*.py")):
        rel_parts = path.relative_to(tests_root).parts
        if "__pycache__" in rel_parts:
            continue
        scanned += 1
        raw = path.read_bytes()
        if b"task_agents" not in raw:
            continue
        try:
            tree = ast.parse(raw, filename=str(path))
        except (SyntaxError, ValueError):
            continue

        ta_aliases: set[str] = set()
        host_aliases: set[str] = set()
        patch_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == FACADE_MODULE_PATH:
                        ta_aliases.add(alias.asname or FACADE_MODULE_PATH)
                    elif alias.name == "nekopower.host":
                        host_aliases.add(alias.asname or "nekopower.host")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "nekopower.host":
                    for alias in node.names:
                        if alias.name == "task_agents":
                            ta_aliases.add(alias.asname or "task_agents")
                elif node.module == "nekopower":
                    for alias in node.names:
                        if alias.name == "host":
                            host_aliases.add(alias.asname or "host")
                elif node.module in ("unittest.mock", "mock"):
                    for alias in node.names:
                        if alias.name == "patch":
                            patch_aliases.add(alias.asname or "patch")
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                for arg in node.value.args:
                    if isinstance(arg, ast.Constant) and arg.value == FACADE_MODULE_PATH:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                ta_aliases.add(target.id)

        def _is_facade_expr(expr: ast.AST) -> bool:
            if isinstance(expr, ast.Name) and expr.id in ta_aliases:
                return True
            if isinstance(expr, ast.Attribute) and expr.attr == "task_agents":
                if isinstance(expr.value, ast.Name) and expr.value.id in host_aliases:
                    return True
                if isinstance(expr.value, ast.Attribute) and ast.unparse(expr.value) == "nekopower.host":
                    return True
            return False

        def _is_patch_call(call: ast.Call) -> bool:
            if isinstance(call.func, ast.Attribute):
                if call.func.attr in _PATCH_CALL_NAMES:
                    return True
                if isinstance(call.func.value, ast.Name) and call.func.value.id in patch_aliases:
                    return call.func.attr == "object"
                return False
            if isinstance(call.func, ast.Name):
                return call.func.id in _PATCH_CALL_NAMES or call.func.id in patch_aliases
            return False

        rel = path.relative_to(tests_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_patch_call(node):
                continue
            if _call_has_absent_opt_out(node):
                continue

            # String form: patch("nekopower.host.task_agents.<attr>", ...) / setattr("nekopower.host.task_agents.<attr>", ...)
            str_candidates: list[ast.AST] = []
            if node.args:
                str_candidates.append(node.args[0])
            for kw in node.keywords:
                if kw.arg == "target":
                    str_candidates.append(kw.value)
            for cand in str_candidates:
                if (
                    isinstance(cand, ast.Constant)
                    and isinstance(cand.value, str)
                    and cand.value.startswith(FACADE_MODULE_PATH + ".")
                ):
                    rest = cand.value[len(FACADE_MODULE_PATH) + 1 :]
                    attr = rest.split(".", 1)[0]
                    if attr:
                        targets.setdefault(attr, set()).add(f"{rel}:{node.lineno}")

            # Object form: monkeypatch.setattr(ta_mod, "<attr>", ...) / patch.object(ta_mod, "<attr>", ...)
            obj_target: ast.AST | None = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "target":
                    obj_target = kw.value
            if obj_target is not None and _is_facade_expr(obj_target):
                attr_node: ast.AST | None = node.args[1] if len(node.args) >= 2 else None
                for kw in node.keywords:
                    if kw.arg in ("name", "attribute"):
                        attr_node = kw.value
                if isinstance(attr_node, ast.Constant) and isinstance(attr_node.value, str) and attr_node.value:
                    targets.setdefault(attr_node.value, set()).add(f"{rel}:{node.lineno}")

    return targets, scanned


def _check_patch_routing_in_tree(
    tree: ast.Module,
    *,
    filename: str,
    patched_symbols: set[str],
) -> list[str]:
    """Return violations where ``patched_symbols`` bypass ``_task_agents_facade`` late binding."""
    violations: list[str] = []
    owner_aliases: set[str] = set()
    facade_aliases: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            is_owner_or_facade = (
                (node.level == 1 and mod in ("", "types"))
                or mod in (FACADE_MODULE_PATH, f"{FACADE_MODULE_PATH}.types")
            )
            for alias in node.names:
                local_name = alias.asname or alias.name
                if is_owner_or_facade and alias.name in patched_symbols:
                    violations.append(
                        f"{filename}:{node.lineno}: module-level from-import of patched symbol "
                        f"{alias.name!r} snapshots it; read via {FACADE_ALIAS}.{alias.name}"
                    )
                if (node.level == 1 and mod == "" and alias.name == "types") or (
                    mod == FACADE_MODULE_PATH and alias.name == "types"
                ):
                    owner_aliases.add(local_name)
                if mod == "nekopower.host" and alias.name == "task_agents":
                    facade_aliases.add(local_name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == f"{FACADE_MODULE_PATH}.types":
                    owner_aliases.add(alias.asname or alias.name)
                elif alias.name == FACADE_MODULE_PATH:
                    facade_aliases.add(alias.asname or alias.name)

    def _collect_fn_bound(fn: ast.AST) -> set[str]:
        bound: set[str] = set()
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (
                list(fn.args.posonlyargs)
                + list(fn.args.args)
                + list(fn.args.kwonlyargs)
                + ([fn.args.vararg] if fn.args.vararg else [])
                + ([fn.args.kwarg] if fn.args.kwarg else [])
            ):
                bound.add(arg.arg)
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(n.name)
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                bound.add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    bound.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    bound.add(a.asname or a.name)
            stack.extend(ast.iter_child_nodes(n))
        return bound

    def _is_owner_import_from(node: ast.ImportFrom) -> bool:
        mod = node.module or ""
        return (node.level == 1 and mod == "types") or mod == f"{FACADE_MODULE_PATH}.types"

    def _check_def_time_expr(expr: ast.AST | None, context: str) -> None:
        if expr is None:
            return
        for sub in ast.walk(expr):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in facade_aliases
                and sub.attr in patched_symbols
            ):
                violations.append(
                    f"{filename}:{sub.lineno}: def-time facade read {sub.value.id}.{sub.attr} "
                    f"in {context} freezes the value at import time"
                )
            elif (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in owner_aliases
                and sub.attr in patched_symbols
            ):
                violations.append(
                    f"{filename}:{sub.lineno}: def-time owner read {sub.value.id}.{sub.attr} "
                    f"in {context} bypasses the facade"
                )
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in patched_symbols:
                violations.append(
                    f"{filename}:{sub.lineno}: bare read of patched symbol {sub.id!r} in {context}"
                )

    def _visit_body(node: ast.AST, scopes: list[set[str]], local_owner_aliases: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                _check_def_time_expr(dec, f"decorator of {node.name}")
            for default in node.args.defaults:
                _check_def_time_expr(default, f"default arg of {node.name}")
            for kw_default in node.args.kw_defaults:
                _check_def_time_expr(kw_default, f"kw-default arg of {node.name}")
            inner = _collect_fn_bound(node)
            fn_owner_aliases = set(local_owner_aliases)
            for stmt in node.body:
                _visit_body(stmt, [*scopes, inner], fn_owner_aliases)
            return
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _visit_body(member, scopes, local_owner_aliases)
                elif isinstance(member, ast.Assign):
                    _check_def_time_expr(member.value, f"class attribute in {node.name}")
                elif isinstance(member, ast.AnnAssign):
                    _check_def_time_expr(member.value, f"class attribute in {node.name}")
            return
        if scopes and isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _is_owner_import_from(node):
                for alias in node.names:
                    if alias.name in patched_symbols:
                        violations.append(
                            f"{filename}:{node.lineno}: in-function from-import of patched symbol "
                            f"{alias.name!r} from owner module bypasses the facade; "
                            f"read via {FACADE_ALIAS}.{alias.name}"
                        )
            elif (node.level == 1 and mod == "") or mod == FACADE_MODULE_PATH:
                for alias in node.names:
                    if alias.name == "types":
                        local_owner_aliases.add(alias.asname or alias.name)
            return
        if scopes and isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == f"{FACADE_MODULE_PATH}.types":
                    local_owner_aliases.add(alias.asname or alias.name)
            return
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in local_owner_aliases
            and node.attr in patched_symbols
        ):
            violations.append(
                f"{filename}:{node.lineno}: read of patched symbol {node.attr!r} through owner module "
                f"{node.value.id!r} bypasses the facade"
            )
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in patched_symbols:
            if not any(node.id in s for s in scopes):
                violations.append(
                    f"{filename}:{node.lineno}: bare global read of patched symbol {node.id!r}; "
                    f"read via {FACADE_ALIAS}.{node.id}"
                )
            return
        for child in ast.iter_child_nodes(node):
            _visit_body(child, scopes, local_owner_aliases)

    for stmt in tree.body:
        _visit_body(stmt, [], set(owner_aliases))

    return violations


def test_patch_routing_contract_across_task_agents_package() -> None:
    """Every symbol tests patch on ``nekopower.host.task_agents`` must exist and be late-bound."""
    patched_map, scanned = _harvest_facade_patch_targets(TESTS_DIR)
    assert scanned >= 100, f"expected >= 100 test files scanned, got {scanned}"
    expected_baseline = {
        "_FORK_REMOVE_RETRY_SECONDS",
        "_SERVICE_CLOSE_POLL_SECONDS",
        "_SERVICE_CLOSE_SECONDS",
        "_SERVICE_HANDOVER_SECONDS",
        "_SHUTDOWN_START_WAIT_SECONDS",
        "_begun_in",
        "_lies_in",
    }
    assert expected_baseline <= set(patched_map), (
        f"AST harvester missed known patch targets: {expected_baseline - set(patched_map)}"
    )

    facade = importlib.import_module(FACADE_MODULE_PATH)
    for sym, locs in sorted(patched_map.items()):
        assert hasattr(facade, sym), f"Patched facade symbol {sym!r} missing from {FACADE_MODULE_PATH} ({sorted(locs)})"

    violations: list[str] = []
    checked_modules: set[str] = set()
    for py_path in sorted(PKG_DIR.glob("*.py")):
        stem = py_path.stem
        if stem == "__init__" or stem in OWNER_SUBMODULES:
            continue
        checked_modules.add(stem)
        tree = _parse_file(py_path)
        violations.extend(
            _check_patch_routing_in_tree(
                tree,
                filename=py_path.relative_to(REPO_ROOT).as_posix(),
                patched_symbols=set(patched_map),
            )
        )

    assert checked_modules == EXPECTED_MIXIN_MODULES | {"manager"}
    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("bad_source", "expected_fragment"),
    [
        (
            "from .types import _lies_in\nclass M:\n    def f(self): return _lies_in\n",
            "module-level from-import",
        ),
        (
            "from nekopower.host.task_agents import _begun_in\nclass M:\n    def f(self): return _begun_in\n",
            "module-level from-import",
        ),
        (
            "from . import types\nclass M:\n    def f(self): return types._lies_in\n",
            "through owner module",
        ),
        (
            "from nekopower.host import task_agents as _task_agents_facade\n"
            "class M:\n"
            "    def f(self, t=_task_agents_facade._SERVICE_CLOSE_SECONDS): return t\n",
            "def-time facade read",
        ),
        (
            "from nekopower.host import task_agents as _task_agents_facade\n"
            "class M:\n"
            "    _FROZEN = _task_agents_facade._SHUTDOWN_START_WAIT_SECONDS\n",
            "def-time facade read",
        ),
        (
            "class M:\n    def f(self): return _SERVICE_HANDOVER_SECONDS\n",
            "bare global read",
        ),
        (
            "class M:\n    def f(self):\n        from .types import _lies_in\n        return _lies_in\n",
            "in-function from-import",
        ),
        (
            "from . import types as shared\n"
            "class M:\n"
            "    def f(self, probe=shared._begun_in): return probe\n",
            "def-time owner read",
        ),
    ],
)
def test_patch_routing_guard_catches_violation_forms(bad_source: str, expected_fragment: str) -> None:
    """Self-test: the AST checker rejects every snapshot/bypass pattern."""
    patched = {
        "_lies_in",
        "_begun_in",
        "_SERVICE_CLOSE_SECONDS",
        "_SHUTDOWN_START_WAIT_SECONDS",
        "_SERVICE_HANDOVER_SECONDS",
    }
    tree = ast.parse(bad_source, filename="<synthetic>")
    found = _check_patch_routing_in_tree(tree, filename="<synthetic>", patched_symbols=patched)
    assert found and any(expected_fragment in msg for msg in found), f"expected {expected_fragment!r}, got {found}"


def test_patch_routing_guard_accepts_late_facade_read() -> None:
    """Self-test: valid late-binding facade reads (and call-time facade imports) pass cleanly."""
    good_source = (
        "from nekopower.host import task_agents as _task_agents_facade\n"
        "class M:\n"
        "    def f(self):\n"
        "        return _task_agents_facade._SERVICE_CLOSE_SECONDS\n"
        "    def g(self):\n"
        "        from nekopower.host.task_agents import _lies_in\n"
        "        return _lies_in\n"
    )
    tree = ast.parse(good_source, filename="<synthetic>")
    found = _check_patch_routing_in_tree(
        tree,
        filename="<synthetic>",
        patched_symbols={"_SERVICE_CLOSE_SECONDS", "_lies_in"},
    )
    assert found == []


def test_harvester_recognizes_aliased_mock_patch_without_matching_plain_calls(tmp_path: Path) -> None:
    """Self-test: harvester tracks ``from unittest.mock import patch as ...`` without false positives."""
    (tmp_path / "test_aliased.py").write_text(
        "from unittest.mock import patch as mock_patch\n"
        'mock_patch("nekopower.host.task_agents._CAUGHT_UP_SECONDS", 0.01)\n',
        encoding="utf-8",
    )
    (tmp_path / "test_plain.py").write_text(
        "def helper(name, val): return name, val\n"
        'helper("nekopower.host.task_agents._NOT_PATCHED", 1)\n',
        encoding="utf-8",
    )
    targets, scanned = _harvest_facade_patch_targets(tmp_path)
    assert scanned == 2
    assert "_CAUGHT_UP_SECONDS" in targets
    assert "_NOT_PATCHED" not in targets


def test_package_layout_mixins_disjoint_and_facade_shape() -> None:
    """Enforce mixin shape, manager bases, disjoint methods (including TreeWork), and facade layout."""
    py_files = {p.stem: p for p in PKG_DIR.glob("*.py")}
    expected_stems = {"__init__", "manager"} | OWNER_SUBMODULES | EXPECTED_MIXIN_MODULES
    assert set(py_files) == expected_stems, f"Unexpected package modules: {set(py_files) ^ expected_stems}"

    # 1. Facade layout: no class definitions; last statement is `from .manager import TaskAgentManager` (unaliased).
    init_tree = _parse_file(py_files["__init__"])
    assert not any(isinstance(n, ast.ClassDef) for n in init_tree.body)
    last_stmt = init_tree.body[-1]
    assert isinstance(last_stmt, ast.ImportFrom)
    assert last_stmt.level == 1 and last_stmt.module == "manager"
    assert len(last_stmt.names) == 1 and last_stmt.names[0].name == "TaskAgentManager"
    assert last_stmt.names[0].asname is None

    # 2. Mixin shape + method collection.
    mixin_classes: dict[str, str] = {}
    member_owner: dict[str, str] = {}
    for stem in sorted(EXPECTED_MIXIN_MODULES):
        tree = _parse_file(py_files[stem])
        classes: list[ast.ClassDef] = []
        for stmt in tree.body:
            if _is_docstring_stmt(stmt) or isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(stmt, ast.ClassDef):
                classes.append(stmt)
                continue
            pytest.fail(f"{stem}.py has disallowed top-level statement {type(stmt).__name__} at line {stmt.lineno}")
        assert len(classes) == 1, f"{stem}.py must define exactly one mixin class"
        cls = classes[0]
        assert cls.name.endswith("Mixin"), f"{stem}.py class {cls.name} must end with 'Mixin'"
        assert cls.bases == [], f"{stem}.py mixin {cls.name} must have empty bases"
        mixin_classes[stem] = cls.name
        for member in cls.body:
            if _is_docstring_stmt(member):
                continue
            assert isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)), (
                f"{stem}.py mixin {cls.name} body must contain methods only (found {type(member).__name__} at L{member.lineno})"
            )
            assert member.name != "__init__", f"{stem}.py mixin {cls.name} must not define __init__"
            assert member.name not in member_owner, (
                f"Method {member.name!r} clashes between {member_owner[member.name]} and {stem}"
            )
            member_owner[member.name] = stem

    # 3. Manager shape + exact base set matching all mixins + TreeWork.
    mgr_tree = _parse_file(py_files["manager"])
    mgr_classes: list[ast.ClassDef] = []
    relative_mixin_imports: dict[str, str] = {}
    for stmt in mgr_tree.body:
        if _is_docstring_stmt(stmt):
            continue
        if isinstance(stmt, ast.ImportFrom):
            if stmt.level == 1 and stmt.module in EXPECTED_MIXIN_MODULES:
                for alias in stmt.names:
                    relative_mixin_imports[alias.asname or alias.name] = stmt.module
            continue
        if isinstance(stmt, ast.Import):
            continue
        if isinstance(stmt, ast.ClassDef):
            mgr_classes.append(stmt)
            continue
        pytest.fail(f"manager.py has disallowed top-level statement {type(stmt).__name__} at line {stmt.lineno}")
    assert len(mgr_classes) == 1 and mgr_classes[0].name == "TaskAgentManager"
    mgr_cls = mgr_classes[0]
    base_names: list[str] = []
    for base in mgr_cls.bases:
        assert isinstance(base, ast.Name), f"TaskAgentManager base must be plain Name, got {ast.dump(base)}"
        base_names.append(base.id)
    assert set(base_names) == set(mixin_classes.values()) | {"TreeWork"}
    for stem, cls_name in mixin_classes.items():
        assert relative_mixin_imports.get(cls_name) == stem, (
            f"TaskAgentManager base {cls_name} must be imported via `from .{stem} import {cls_name}`"
        )
    for member in mgr_cls.body:
        if _is_docstring_stmt(member):
            continue
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert member.name not in member_owner, (
                f"Method {member.name!r} clashes between {member_owner[member.name]} and manager"
            )
            member_owner[member.name] = "manager"

    # 4. TreeWork disjointness.
    tw_tree = _parse_file(TREE_WORK_PATH)
    tw_cls = [n for n in tw_tree.body if isinstance(n, ast.ClassDef) and n.name == "TreeWork"][0]
    for member in tw_cls.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert member.name not in member_owner, (
                f"Method {member.name!r} clashes between {member_owner[member.name]} and TreeWork"
            )


def test_facade_runtime_identities_and_shared_logger() -> None:
    """Re-exported symbols keep exact identity and every submodule logs under ``nekopower.host.task_agents``."""
    facade = importlib.import_module(FACADE_MODULE_PATH)
    types_mod = importlib.import_module(f"{FACADE_MODULE_PATH}.types")
    manager_mod = importlib.import_module(f"{FACADE_MODULE_PATH}.manager")

    assert facade.__file__ is not None
    assert Path(facade.__file__).resolve().parent == PKG_DIR.resolve()
    assert facade.TaskAgentManager is manager_mod.TaskAgentManager
    assert facade._Session is types_mod._Session
    assert facade._Beside is types_mod._Beside
    assert facade._UsageCut is types_mod._UsageCut
    assert facade.ForkRefused is types_mod.ForkRefused
    assert facade.Handover is types_mod.Handover
    assert facade.logger is types_mod.logger
    assert facade.logger.name == FACADE_MODULE_PATH

    for stem in sorted(EXPECTED_MIXIN_MODULES | {"manager"}):
        sub = importlib.import_module(f"{FACADE_MODULE_PATH}.{stem}")
        if hasattr(sub, "logger"):
            assert sub.logger is facade.logger, f"{stem}.logger must be the shared package logger"


@pytest.mark.asyncio
async def test_facade_monkeypatch_reaches_split_consumers_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """End-to-end sentinel check: monkeypatching the facade really reaches mixin/manager consumers."""
    import asyncio
    from nekopower.host import task_agents as facade

    # 1. _FORK_REMOVE_RETRY_SECONDS in ForksBesideMixin._remove_fork_dir
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(facade, "_FORK_REMOVE_RETRY_SECONDS", 0.1234)

    class _StubForkRemover(facade.TaskAgentManager):
        def __init__(self) -> None:
            self._closed = True

    stub = _StubForkRemover()
    target = tmp_path / "missing-fork"
    target.mkdir()
    # Keep removal leaving the directory on disk so the retry sleep uses the patched constant.
    monkeypatch.setattr("shutil.rmtree", lambda *_a, **_kw: None)
    await stub._remove_fork_dir("s-fork", target)
    assert sleeps and all(s == pytest.approx(0.1234) for s in sleeps)

    # 2. _SERVICE_HANDOVER_SECONDS in IdleCompactMixin._stop_compacting
    wait_timeouts: list[float | None] = []
    real_wait = asyncio.wait

    async def spy_wait(fs, *, timeout=None, **kwargs):
        wait_timeouts.append(timeout)
        return set(), set(fs)

    monkeypatch.setattr(asyncio, "wait", spy_wait)
    monkeypatch.setattr(facade, "_SERVICE_HANDOVER_SECONDS", 0.4321)

    blocker: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    class _StubIdle(facade.TaskAgentManager):
        def __init__(self) -> None:
            self._idle_timers = {}
            self._compacting = {"s1": blocker}
            self._idle_over = {}
            self._idle_cut_short = set()
            self._idle_stops = 0

        def _cut_short(self, task) -> None:
            pass

    idle_stub = _StubIdle()
    await idle_stub._stop_compacting("s1")
    assert 0.4321 in wait_timeouts
    monkeypatch.setattr(asyncio, "wait", real_wait)

    # 3. _SERVICE_CLOSE_SECONDS / _SERVICE_CLOSE_POLL_SECONDS / _SHUTDOWN_START_WAIT_SECONDS in IdleCompactMixin._end_service
    end_service_timeouts: list[float | None] = []

    class _FakeRuntime:
        async def close(self, session_id: str) -> None:
            pass

    monkeypatch.setattr(facade, "_SERVICE_CLOSE_POLL_SECONDS", 0.0777)
    monkeypatch.setattr(facade, "_SERVICE_CLOSE_SECONDS", 0.0)
    monkeypatch.setattr(facade, "_SHUTDOWN_START_WAIT_SECONDS", 999.0)

    class _StubServiceEnd(facade.TaskAgentManager):
        def __init__(self) -> None:
            self._closed = False
            self._service = {}

    svc_stub = _StubServiceEnd()
    call_count = 0

    async def spy_wait_then_close(fs, *, timeout=None, **kwargs):
        nonlocal call_count
        end_service_timeouts.append(timeout)
        call_count += 1
        if call_count >= 1:
            svc_stub._closed = True
            monkeypatch.setattr(facade, "_SHUTDOWN_START_WAIT_SECONDS", 0.0)
        return set(), set(fs)

    monkeypatch.setattr(asyncio, "wait", spy_wait_then_close)
    events: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    with caplog.at_level("WARNING", logger=FACADE_MODULE_PATH):
        await svc_stub._end_service("s1", _FakeRuntime(), events, True)
    assert end_service_timeouts == [pytest.approx(0.0777)]
    assert any(
        rec.name == FACADE_MODULE_PATH and "has not said that session s1 is closed" in rec.getMessage()
        for rec in caplog.records
    )
    monkeypatch.setattr(asyncio, "wait", real_wait)

    # 4. _lies_in in MessagingMixin._rule_on_other_session (late-bound via facade)
    def sentinel_lies_in(*_args):
        return True

    monkeypatch.setattr(facade, "_lies_in", sentinel_lies_in)
    assert sys.modules[f"{FACADE_MODULE_PATH}.messaging"]._task_agents_facade._lies_in is sentinel_lies_in

    # 5. _begun_in in manager.py (late-bound via facade)
    def sentinel_begun(*_args):
        return "sentinel-runtime"

    monkeypatch.setattr(facade, "_begun_in", sentinel_begun)
    assert sys.modules[f"{FACADE_MODULE_PATH}.manager"]._task_agents_facade._begun_in is sentinel_begun

    # 6. _SHUTDOWN_START_WAIT_SECONDS in BillingUsageMixin._settle_usage
    settle_timeouts: list[float | None] = []
    pending_write: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    class _StubUsage(facade.TaskAgentManager):
        def __init__(self) -> None:
            self._usage_owed = {}
            self._usage_locks = {}
            self._usage_writes = {pending_write}

    usage_stub = _StubUsage()

    async def spy_wait_settle(fs, *, timeout=None, **kwargs):
        settle_timeouts.append(timeout)
        usage_stub._usage_writes.clear()
        return set(), set(fs)

    monkeypatch.setattr(asyncio, "wait", spy_wait_settle)
    monkeypatch.setattr(facade, "_SHUTDOWN_START_WAIT_SECONDS", 123.4)
    await usage_stub._settle_usage()
    assert len(settle_timeouts) == 1
    assert settle_timeouts[0] is not None and 100.0 < settle_timeouts[0] <= 123.5
    blocker.cancel()
    pending_write.cancel()
