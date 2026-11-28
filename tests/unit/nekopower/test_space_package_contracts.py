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

"""Facade, late-binding, logger, layout, and mixin contracts for ``nekopower.host.space``."""

from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path
from typing import Any

import pytest

from nekopower.host import space as space_facade
from nekopower.host.ledger import Commission
from nekopower.host.paths import SpacePaths
from nekopower.host.space import evidence as space_evidence
from nekopower.host.space import fs as space_fs
from nekopower.host.space import manager as space_manager
from nekopower.host.space import markdown as space_markdown
from nekopower.host.space import models as space_models
from nekopower.host.space import projects as space_projects
from nekopower.host.space import storage as space_storage
from nekopower.host.space import tidy as space_tidy

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_ROOT = _REPO_ROOT / "tests"
_HOST_ROOT = _REPO_ROOT / "nekopower" / "host"
_SPACE_PKG_DIR = _HOST_ROOT / "space"
_LEGACY_SPACE_FILE = _HOST_ROOT / "space.py"
_THIS_FILE = Path(__file__).resolve()

_EXPECTED_MODULES = frozenset({
    "__init__",
    "models",
    "markdown",
    "evidence",
    "fs",
    "storage",
    "projects",
    "tidy",
    "manager",
})

_SUBMODULES_USING_LOGGER = (
    space_facade,
    space_models,
    space_evidence,
    space_fs,
    space_storage,
    space_projects,
    space_tidy,
    space_manager,
)

# Minimum baseline set of top-level facade symbols known to be patched by tests.
# Any additional literal or wrapper-forwarded targets discovered under tests/ are
# merged with this baseline automatically and checked by the late-binding guard.
_BASELINE_PATCHED_SYMBOLS = frozenset({
    "_COUNTED_COMMISSIONS",
    "_STANDING_CHARACTERS_MAX",
    "_STANDING_STEPS_MAX",
    "_WRITE_RETRY_SECONDS",
    "_asked",
    "_asked_off_the_loop",
    "_cut",
    "_files_of",
    "_gone",
    "_line_put_right",
    "_move",
    "_place_of",
    "_real_place",
    "_recorded",
    "_standing_marks",
    "_stands",
    "_stood",
    "_texts_of",
    "evidence_of",
})

_NON_MODELS_SUBMODULES = ("markdown", "evidence", "fs", "storage", "projects", "tidy", "manager")
_OWNER_MODULES = frozenset({"models", "markdown", "evidence", "fs"})
_SPACE_MODULE_PREFIX = "nekopower.host.space."
_FACADE_NAME = "nekopower.host.space"


# ---------------------------------------------------------------------------
# AST patch-target harvester across tests/
# ---------------------------------------------------------------------------


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _string_literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_facade_aliases(tree: ast.AST) -> set[str]:
    """Find local names bound to ``nekopower.host.space`` anywhere in an AST."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "nekopower.host":
            for alias in node.names:
                if alias.name == "space":
                    aliases.add(alias.asname or "space")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _FACADE_NAME and alias.asname:
                    aliases.add(alias.asname)
    return aliases


def _collect_patch_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Find aliases for ``unittest.mock`` and direct ``patch`` callables."""
    mock_aliases: set[str] = set()
    patch_fns: set[str] = {"patch"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "unittest.mock":
                    mock_aliases.add(alias.asname or "unittest")
                elif alias.name == "mock":
                    mock_aliases.add(alias.asname or "mock")
        elif isinstance(node, ast.ImportFrom) and node.module == "unittest":
            for alias in node.names:
                if alias.name == "mock":
                    mock_aliases.add(alias.asname or "mock")
        elif isinstance(node, ast.ImportFrom) and node.module == "unittest.mock":
            for alias in node.names:
                if alias.name == "patch":
                    patch_fns.add(alias.asname or "patch")
    return mock_aliases, patch_fns


def _is_facade_expr(node: ast.AST, facade_aliases: set[str]) -> bool:
    dotted = _dotted_name(node)
    return dotted == _FACADE_NAME or (isinstance(node, ast.Name) and node.id in facade_aliases)


def _extract_attr_arg(call: ast.Call) -> tuple[str | None, ast.AST | None]:
    """Return literal attribute name or dynamic AST node for setattr/patch.object calls."""
    if len(call.args) >= 2:
        return _string_literal(call.args[1]), call.args[1]
    for kw in call.keywords:
        if kw.arg in ("name", "attribute"):
            return _string_literal(kw.value), kw.value
    return None, None


def _harvest_file_patch_targets(path: Path) -> tuple[set[str], set[str], list[str]]:
    """Return ``(top_level_targets, class_targets, unresolved_descriptions)`` in one test file."""
    source = path.read_text(encoding="utf-8")
    if "nekopower.host.space" not in source and "nekopower.host" not in source:
        return set(), set(), []

    tree = ast.parse(source, filename=str(path))
    global_facades = _collect_facade_aliases(tree)
    mock_aliases, patch_fns = _collect_patch_aliases(tree)

    top_targets: set[str] = set()
    class_targets: set[str] = set()
    helper_param_map: dict[str, tuple[int, str]] = {}

    # Pass 1: find same-file helper functions like `_unanswered(monkeypatch, name, which)`
    # that forward a string parameter directly into setattr/patch.object on the facade.
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_facades = global_facades | _collect_facade_aliases(node)
        param_index = {arg.arg: idx for idx, arg in enumerate(node.args.args)}
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            attr = inner.func.attr if isinstance(inner.func, ast.Attribute) else None
            if attr not in ("setattr", "object"):
                continue
            if len(inner.args) >= 2 and _is_facade_expr(inner.args[0], local_facades):
                if isinstance(inner.args[1], ast.Name) and inner.args[1].id in param_index:
                    pname = inner.args[1].id
                    helper_param_map[node.name] = (param_index[pname], pname)

    unresolved: list[str] = []

    def inspect_scope(
        scope_node: ast.AST,
        inherited_facades: set[str],
        enclosing_fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> None:
        local_facades = inherited_facades | _collect_facade_aliases(scope_node)
        for child in ast.iter_child_nodes(scope_node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inspect_scope(child, local_facades, child)
                continue
            if isinstance(child, ast.Call):
                dotted = _dotted_name(child.func)
                # Direct helper call such as `_unanswered(monkeypatch, "evidence_of", ...)`
                if isinstance(child.func, ast.Name) and child.func.id in helper_param_map:
                    idx, _pname = helper_param_map[child.func.id]
                    if idx < len(child.args):
                        lit = _string_literal(child.args[idx])
                        if lit:
                            top_targets.add(lit)
                        else:
                            unresolved.append(f"{path}:{child.lineno}:{ast.unparse(child)}")

                # `monkeypatch.setattr(target_obj, attr, ...)` or `monkeypatch.setattr("dotted", ...)`
                is_setattr = isinstance(child.func, ast.Attribute) and child.func.attr == "setattr"
                is_builtin_setattr = isinstance(child.func, ast.Name) and child.func.id == "setattr"
                if is_setattr or is_builtin_setattr:
                    if child.args and isinstance(_string_literal(child.args[0]), str):
                        target_str = _string_literal(child.args[0])
                        assert target_str is not None
                        if target_str.startswith(_SPACE_MODULE_PREFIX):
                            rest = target_str[len(_SPACE_MODULE_PREFIX) :]
                            if "." in rest:
                                class_targets.add(rest)
                            else:
                                top_targets.add(rest)
                    elif child.args and _is_facade_expr(child.args[0], local_facades):
                        lit, raw_node = _extract_attr_arg(child)
                        if lit:
                            top_targets.add(lit)
                        elif not (
                            enclosing_fn is not None
                            and enclosing_fn.name in helper_param_map
                            and isinstance(raw_node, ast.Name)
                            and raw_node.id == helper_param_map[enclosing_fn.name][1]
                        ):
                            unresolved.append(f"{path}:{child.lineno}:{ast.unparse(child)}")

                # `patch("nekopower.host.space.<sym>", ...)` / `mock.patch(...)` / `patch.object(...)`
                is_patch_str = (
                    (isinstance(child.func, ast.Name) and child.func.id in patch_fns)
                    or (
                        dotted is not None
                        and "." in dotted
                        and dotted.split(".")[0] in mock_aliases
                        and dotted.endswith(".patch")
                    )
                )
                is_patch_object = (
                    (isinstance(child.func, ast.Attribute) and child.func.attr == "object" and isinstance(child.func.value, ast.Name) and child.func.value.id in patch_fns)
                    or (
                        dotted is not None
                        and dotted.split(".")[0] in mock_aliases
                        and dotted.endswith(".patch.object")
                    )
                )
                if is_patch_str:
                    first = child.args[0] if child.args else next((kw.value for kw in child.keywords if kw.arg == "target"), None)
                    target_str = _string_literal(first) if first is not None else None
                    if target_str and target_str.startswith(_SPACE_MODULE_PREFIX):
                        rest = target_str[len(_SPACE_MODULE_PREFIX) :]
                        if "." in rest:
                            class_targets.add(rest)
                        else:
                            top_targets.add(rest)
                elif is_patch_object and child.args and _is_facade_expr(child.args[0], local_facades):
                    lit, raw_node = _extract_attr_arg(child)
                    if lit:
                        top_targets.add(lit)
                    elif not (
                        enclosing_fn is not None
                        and enclosing_fn.name in helper_param_map
                        and isinstance(raw_node, ast.Name)
                        and raw_node.id == helper_param_map[enclosing_fn.name][1]
                    ):
                        unresolved.append(f"{path}:{child.lineno}:{ast.unparse(child)}")

            inspect_scope(child, local_facades, enclosing_fn)

    inspect_scope(tree, global_facades)
    return top_targets, class_targets, unresolved


def _harvest_all_test_patch_targets(tests_root: Path) -> tuple[set[str], set[str], list[str]]:
    top_targets: set[str] = set()
    class_targets: set[str] = set()
    unresolved: list[str] = []
    for py_file in sorted(tests_root.rglob("*.py")):
        if py_file.resolve() == _THIS_FILE:
            continue
        t, c, u = _harvest_file_patch_targets(py_file)
        top_targets.update(t)
        class_targets.update(c)
        unresolved.extend(u)
    return top_targets, class_targets, unresolved


# ---------------------------------------------------------------------------
# AST late-binding checker for package submodules
# ---------------------------------------------------------------------------


def _bound_names_in_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    bound: set[str] = set()
    for arg in (
        list(fn.args.posonlyargs)
        + list(fn.args.args)
        + list(fn.args.kwonlyargs)
        + ([fn.args.vararg] if fn.args.vararg else [])
        + ([fn.args.kwarg] if fn.args.kwarg else [])
    ):
        bound.add(arg.arg)
    stack: list[ast.AST] = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        stack.extend(ast.iter_child_nodes(node))
    return bound


def _analyze_late_binding(
    source: str,
    *,
    module_name: str,
    patched_symbols: set[str] | frozenset[str],
    owner_modules: frozenset[str] = _OWNER_MODULES,
) -> list[str]:
    """Check one submodule for eager imports, def-time reads, or bare global loads of patched symbols."""
    tree = ast.parse(source, filename=f"{module_name}.py")
    violations: list[str] = []
    facade_aliases: set[str] = set()
    owner_aliases: set[str] = set()

    def record_import_aliases(stmt: ast.AST) -> None:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == _FACADE_NAME and alias.asname:
                    facade_aliases.add(alias.asname)
                elif any(alias.name == f"{_FACADE_NAME}.{om}" for om in owner_modules):
                    owner_aliases.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(stmt, ast.ImportFrom):
            mod = stmt.module or ""
            is_facade_parent = (stmt.level == 0 and mod == "nekopower.host") or (stmt.level == 2 and mod == "")
            is_owner_import = (
                (stmt.level == 1 and mod in owner_modules)
                or (stmt.level == 0 and any(mod == f"{_FACADE_NAME}.{om}" for om in owner_modules))
                or (stmt.level == 1 and mod == "")
            )
            if is_facade_parent:
                for alias in stmt.names:
                    if alias.name == "space":
                        facade_aliases.add(alias.asname or "space")
            elif stmt.level == 1 and mod == "":
                for alias in stmt.names:
                    if alias.name in owner_modules:
                        owner_aliases.add(alias.asname or alias.name)
                    elif alias.name in patched_symbols:
                        violations.append(
                            f"{module_name}:{stmt.lineno}: forbidden from-import of patched symbol {alias.name}"
                        )
            elif (stmt.level == 0 and mod == _FACADE_NAME) or is_owner_import:
                for alias in stmt.names:
                    if alias.name in patched_symbols:
                        violations.append(
                            f"{module_name}:{stmt.lineno}: forbidden from-import of patched symbol {alias.name} from {mod}"
                        )

    def check_expr(node: ast.AST, scopes: list[set[str]], def_time: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                check_expr(dec, scopes, True)
            for d in (*node.args.defaults, *(kw for kw in node.args.kw_defaults if kw is not None)):
                check_expr(d, scopes, True)
            inner = _bound_names_in_function(node)
            for body_stmt in node.body:
                if isinstance(body_stmt, (ast.Import, ast.ImportFrom)):
                    record_import_aliases(body_stmt)
                check_expr(body_stmt, [*scopes, inner], False)
            return

        if isinstance(node, ast.Lambda):
            for d in (*node.args.defaults, *(kw for kw in node.args.kw_defaults if kw is not None)):
                check_expr(d, scopes, True)
            args_bound = {
                a.arg
                for a in (
                    list(node.args.posonlyargs)
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                    + ([node.args.vararg] if node.args.vararg else [])
                    + ([node.args.kwarg] if node.args.kwarg else [])
                )
            }
            check_expr(node.body, [*scopes, args_bound], def_time)
            return

        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                check_expr(base, scopes, True)
            for kw in node.keywords:
                check_expr(kw.value, scopes, True)
            for dec in node.decorator_list:
                check_expr(dec, scopes, True)
            for body_stmt in node.body:
                check_expr(body_stmt, scopes, True)
            return

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.attr in patched_symbols:
            target = node.value.id
            if def_time and target in facade_aliases:
                violations.append(
                    f"{module_name}:{node.lineno}: def-time facade read {target}.{node.attr}"
                )
            elif target in owner_aliases:
                kind = "def-time" if def_time else "direct-owner"
                violations.append(
                    f"{module_name}:{node.lineno}: {kind} owner read {target}.{node.attr} instead of facade"
                )
            return

        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in patched_symbols
            and not any(node.id in scope for scope in scopes)
        ):
            # Defining TaskAgentSpace inside manager.py may only reference itself if ever needed;
            # submodules outside models must read patched symbols via _space_facade.
            if not (module_name == "manager" and node.id == "TaskAgentSpace"):
                violations.append(
                    f"{module_name}:{node.lineno}: bare global read of {node.id} instead of _space_facade.{node.id}"
                )
            return

        for child in ast.iter_child_nodes(node):
            check_expr(child, scopes, def_time)

    # First collect top-level import aliases so def-time checks see them cleanly,
    # then inspect every top-level statement for violations.
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            record_import_aliases(stmt)
    for stmt in tree.body:
        check_expr(stmt, [], True)

    return violations


# ---------------------------------------------------------------------------
# Guard tests
# ---------------------------------------------------------------------------


def test_patch_target_harvester_finds_all_patched_symbols() -> None:
    top_targets, class_targets, unresolved = _harvest_all_test_patch_targets(_TESTS_ROOT)
    assert not unresolved, f"Unresolved dynamic patch calls in tests/: {unresolved}"
    assert _BASELINE_PATCHED_SYMBOLS <= top_targets, (
        f"Missing baseline patch targets: {_BASELINE_PATCHED_SYMBOLS - top_targets}"
    )
    for name in sorted(top_targets):
        assert hasattr(space_facade, name), f"Missing facade attribute on nekopower.host.space: {name}"
    for qual in sorted(class_targets):
        cls_name, attr_name = qual.split(".", 1)
        cls_obj = getattr(space_facade, cls_name)
        assert hasattr(cls_obj, attr_name), f"Missing patched class attribute {qual}"


def test_late_binding_contract_across_all_submodules() -> None:
    top_targets, _, unresolved = _harvest_all_test_patch_targets(_TESTS_ROOT)
    assert not unresolved
    all_guarded = top_targets | _BASELINE_PATCHED_SYMBOLS | {"TaskAgentSpace"}

    for mod_name in _NON_MODELS_SUBMODULES:
        source = (_SPACE_PKG_DIR / f"{mod_name}.py").read_text(encoding="utf-8")
        violations = _analyze_late_binding(
            source,
            module_name=mod_name,
            patched_symbols=all_guarded if mod_name != "manager" else (top_targets | _BASELINE_PATCHED_SYMBOLS),
        )
        assert not violations, f"Late-binding violations in {mod_name}.py:\n" + "\n".join(violations)


def test_late_binding_ast_checker_self_test() -> None:
    patched = {"_cut", "_WRITE_RETRY_SECONDS"}

    # Positive: call-time facade read and local definition never calling bare global.
    good = (
        "from nekopower.host import space as _space_facade\n"
        "def _cut(text: str, limit: int) -> str:\n"
        "    return text[:limit]\n"
        "def helper(text: str) -> str:\n"
        "    return _space_facade._cut(text, 10)\n"
    )
    assert _analyze_late_binding(good, module_name="sample", patched_symbols=patched) == []

    # Negative 1: top-level from-import from sibling owner module.
    bad_top_import = "from .markdown import _cut\n"
    assert len(_analyze_late_binding(bad_top_import, module_name="sample", patched_symbols=patched)) == 1

    # Negative 2: in-function from-import from sibling owner module.
    bad_fn_import = (
        "def helper() -> str:\n"
        "    from .markdown import _cut\n"
        "    return _cut('abc', 1)\n"
    )
    assert len(_analyze_late_binding(bad_fn_import, module_name="sample", patched_symbols=patched)) >= 1

    # Negative 3: def-time default via facade alias.
    bad_def_facade = (
        "from nekopower.host import space as _space_facade\n"
        "def helper(delay: float = _space_facade._WRITE_RETRY_SECONDS) -> float:\n"
        "    return delay\n"
    )
    assert len(_analyze_late_binding(bad_def_facade, module_name="sample", patched_symbols=patched)) == 1

    # Negative 4: def-time default via owner module alias.
    bad_def_owner = (
        "from . import models as _models\n"
        "def helper(delay: float = _models._WRITE_RETRY_SECONDS) -> float:\n"
        "    return delay\n"
    )
    assert len(_analyze_late_binding(bad_def_owner, module_name="sample", patched_symbols=patched)) == 1

    # Negative 5: bare global load inside function body.
    bad_bare = (
        "def helper(text: str) -> str:\n"
        "    return _cut(text, 2)\n"
    )
    assert len(_analyze_late_binding(bad_bare, module_name="sample", patched_symbols=patched)) == 1


def test_package_layout_and_mixin_disjointness() -> None:
    assert not _LEGACY_SPACE_FILE.exists(), "Legacy nekopower/host/space.py must be removed"
    assert _SPACE_PKG_DIR.is_dir()
    actual_modules = {p.stem for p in _SPACE_PKG_DIR.glob("*.py")}
    assert actual_modules == _EXPECTED_MODULES

    # `__init__.py` must end with `from .manager import TaskAgentSpace` so `_space_facade` binds cleanly.
    init_tree = ast.parse((_SPACE_PKG_DIR / "__init__.py").read_text(encoding="utf-8"))
    non_doc_stmts = [
        s for s in init_tree.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
    ]
    last = non_doc_stmts[-1]
    assert (
        isinstance(last, ast.ImportFrom)
        and last.level == 1
        and last.module == "manager"
        and [a.name for a in last.names] == ["TaskAgentSpace"]
    )

    mixins = (space_storage.StorageMixin, space_projects.ProjectsMixin, space_tidy.TidyMixin)
    assert space_manager.TaskAgentSpace.__bases__ == mixins

    own_attr_sets: list[tuple[str, set[str]]] = []
    for cls in (*mixins, space_manager.TaskAgentSpace):
        names = {
            k
            for k in cls.__dict__
            if not (k.startswith("__") and k.endswith("__"))
        }
        if cls is not space_manager.TaskAgentSpace:
            assert "__init__" not in cls.__dict__, f"Mixin {cls.__name__} must stay method-only without __init__"
        own_attr_sets.append((cls.__name__, names))

    for i, (left_name, left_names) in enumerate(own_attr_sets):
        for right_name, right_names in own_attr_sets[i + 1 :]:
            overlap = left_names & right_names
            assert not overlap, f"Colliding class attributes between {left_name} and {right_name}: {sorted(overlap)}"


def test_shared_logger_and_runtime_identities() -> None:
    assert space_facade.logger.name == "nekopower.host.space"
    for mod in _SUBMODULES_USING_LOGGER:
        assert mod.logger is space_facade.logger, f"{mod.__name__}.logger diverged from facade logger"

    assert space_facade.TaskAgentSpace is space_manager.TaskAgentSpace
    assert space_facade.Entry is space_models.Entry
    assert space_facade.Project is space_models.Project
    assert space_facade.Review is space_models.Review
    assert space_facade.Layer is space_models.Layer
    assert space_facade._OUT is space_models._OUT is space_evidence._OUT
    assert space_facade._ASKING is space_models._ASKING is space_evidence._ASKING

    for submod in (space_models, space_markdown, space_evidence, space_fs):
        mod_tree = ast.parse(Path(submod.__file__).read_text(encoding="utf-8"))
        for stmt in mod_tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert getattr(space_facade, stmt.name) is getattr(submod, stmt.name)


def _make_commission(cid: str, workdir: Path, started_at: float) -> Commission:
    return Commission(
        id=cid,
        lanlan_name="lanlan",
        title="Check contract",
        dispatch_text="check",
        user_text="check",
        source="dispatch",
        route="task_agent",
        project="",
        workdir=str(workdir),
        status="working",
        status_reason="",
        runtime="builtin",
        created_at=started_at,
        updated_at=started_at,
        started_at=started_at,
        ended_at=None,
        result_summary=None,
        pending_question=None,
        delivery=None,
        parent_id=None,
        conversation_id=None,
        lang="en",
        plugin_id=None,
        entry_id=None,
        name=None,
        deferred_until=None,
        host_instance=None,
    )


async def test_runtime_facade_monkeypatch_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    paths = SpacePaths(tmp_path)
    # Real TaskAgentSpace instance (never skipping TaskAgentSpace.__init__).
    space = space_facade.TaskAgentSpace(lambda: paths, clock=lambda: 1000.0)
    c1 = _make_commission("c1", tmp_path, 1000.0)
    c2 = _make_commission("c2", tmp_path, 1001.0)

    # 1. `markdown.py`: `_STANDING_STEPS_MAX` & `_cut`
    monkeypatch.setattr("nekopower.host.space._STANDING_STEPS_MAX", 1)
    assert space_markdown._standing_marks("para one\n\npara two", ["abc"]) is None

    sentinel_cut_calls: list[tuple[str, int]] = []

    def fake_cut(text: str, limit: int) -> str:
        sentinel_cut_calls.append((text, limit))
        return "CUT:" + text[:limit]

    monkeypatch.setattr("nekopower.host.space._cut", fake_cut)
    assert space_markdown._shown("alpha beta gamma delta epsilon", 2) == "CUT:.."
    assert sentinel_cut_calls == [(".../alpha beta gamma delta epsilon", 2)]

    # 2. `evidence.py`: `_real_place`
    sentinel_places: list[str] = []

    def fake_real_place(raw: str) -> str:
        sentinel_places.append(raw)
        return raw

    monkeypatch.setattr("nekopower.host.space._real_place", fake_real_place)
    space_evidence.evidence_of(["src/app.py"], [str(tmp_path)])
    assert sentinel_places == [str(tmp_path)]

    # 3. `fs.py`: `_WRITE_RETRY_SECONDS` & `_files_of` on EXDEV fallback
    src_dir = tmp_path / "src_dir"
    dst_dir = tmp_path / "dst_dir"
    src_dir.mkdir()
    (src_dir / "note.txt").write_text("ok", encoding="utf-8")
    seen_files_of: list[Path] = []
    real_files_of = space_facade._files_of

    def tracked_files_of(path: Path) -> list[tuple[str, int, int]]:
        seen_files_of.append(path)
        return real_files_of(path)

    monkeypatch.setattr("nekopower.host.space._files_of", tracked_files_of)
    monkeypatch.setattr("nekopower.host.space._WRITE_RETRY_SECONDS", 0.0)
    real_replace = space_fs.os.replace
    exdev_raised = False

    def fake_replace(src: Path, dst: Path) -> None:
        nonlocal exdev_raised
        if not exdev_raised and Path(src) == src_dir and Path(dst) == dst_dir:
            exdev_raised = True
            raise OSError(importlib.import_module("errno").EXDEV, "cross-device")
        real_replace(src, dst)

    monkeypatch.setattr(space_fs.os, "replace", fake_replace)
    space_fs._move(src_dir, dst_dir)
    assert dst_dir.is_dir() and len(seen_files_of) == 2

    # 4. `projects.py`: `_move`
    move_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr("nekopower.host.space._move", lambda s, d: move_calls.append((s, d)))
    arch_dir = paths.archived_dir("2026", "demo")
    arch_dir.mkdir(parents=True)
    archived = space_models.Project(
        name="demo",
        status=space_models.PROJECT_ARCHIVED,
        archive_year="2026",
        directory="demo",
    )
    space._restore(archived)
    assert move_calls == [(arch_dir, paths.project_dir("demo"))]

    # 5. `tidy.py`: `_line_put_right` & `TaskAgentSpace._whole_texts`
    entry_dir = paths.memory_dir(None)
    entry_dir.mkdir(parents=True, exist_ok=True)
    (entry_dir / "id1.md").write_text(
        "---\nname: Entry One\nstatus: active\n---\n\nBody.\n", encoding="utf-8"
    )
    paths.index(None).write_text("- [Entry One](memory/id1.md)\n", encoding="utf-8")
    reline_calls: list[Any] = []
    monkeypatch.setattr(
        "nekopower.host.space._line_put_right",
        lambda line, *rest: (reline_calls.append((line, rest)), (line, False))[1],
    )
    space._put_line_right(None, "id1", space_models._LineToPut(missing=False))
    assert len(reline_calls) == 1

    monkeypatch.setattr(space_facade.TaskAgentSpace, "_whole_texts", staticmethod(lambda _s: ("patched-whole",)))
    holds = space_models._Document(meta=[], body="holds")
    said = space_models._Document(meta=[], body="said")
    assert space_tidy.TidyMixin._fits_into(holds, said, "2026-11-28") is True

    # 6. `manager.py`: `_COUNTED_COMMISSIONS` & shared logger caplog routing
    monkeypatch.setattr("nekopower.host.space._COUNTED_COMMISSIONS", 1)
    await space.submit(
        c1, {"title": "Rule One", "content": "Keep first rule.", "category": "lesson", "confidence": "verified"}
    )
    await space.submit(
        c2, {"title": "Rule Two", "content": "Keep second rule.", "category": "lesson", "confidence": "verified"}
    )
    assert list(space._added_by) == ["c2"]

    with caplog.at_level(logging.WARNING, logger="nekopower.host.space"):
        space_models.logger.warning("space-sentinel-warning")
    assert any(r.message == "space-sentinel-warning" for r in caplog.records)
