#!/usr/bin/env python3
"""Static gate: structural contracts of the ``main_logic/core`` package.

The ``LLMSessionManager`` mixin split (#2272) rests on a set of contracts
that comments alone cannot enforce. This check makes every one of them a
CI failure:

CORE_PATCH_ROUTING
    Every facade symbol that any test rebinds via
    ``monkeypatch.setattr("main_logic.core.<attr>", ...)`` (string form) or
    ``monkeypatch.setattr(core_module, "<attr>", ...)`` /
    ``patch.object(core_module, "<attr>", ...)`` (object form) must be read
    by manager/mixin code ONLY through the ``_core_facade`` late-binding
    module object. A module-level from-import or a bare global read in a
    mixin snapshots the value at import time: assertion-style stubs then
    fail loudly, but isolation-style stubs (blocking disk/network IO) go
    silently green while calling the real function. The patched-symbol set
    is harvested from ``tests/`` by AST on every run, so the contract
    tightens automatically as tests grow. Function-local imports from the
    OWNER module (e.g. ``from utils.preferences import ...`` inside a
    method) are a different, deliberate late-binding pattern and stay
    allowed: the facade patch never targeted them.

CORE_PATCH_TARGET_EXISTS
    Every patched facade attribute must actually exist at the facade top
    level (typo guard), unless the call passes ``raising=False`` (negative
    guards intentionally patch absent names).

CORE_MIXIN_SHAPE
    A mixin module's top level holds only a docstring, imports and exactly
    one ``*Mixin`` class; the class body holds only a docstring and
    methods. Instance state has a single home (``LLMSessionManager.
    __init__``), and module-level state in a mixin would sit outside the
    facade's rebind semantics entirely.

CORE_MANAGER_SHAPE
    ``manager.py`` holds exactly one class, ``LLMSessionManager``; its only
    method is ``__init__`` (class-level constants are allowed). New
    behavior belongs in a domain mixin.

CORE_MIXIN_DISJOINT
    No method name is defined by two mixin classes (or by both a mixin and
    the manager class). Python would resolve the clash silently by MRO
    order; this makes it a build failure instead.

CORE_MIXIN_BASES
    The base list of ``LLMSessionManager`` is exactly the set of ``*Mixin``
    classes defined in the package — no orphan mixin module, no missing
    base.

CORE_FACADE_LAYOUT
    ``__init__.py`` defines no class at top level, and its last statement
    is ``from .manager import LLMSessionManager`` — the facade namespace
    must be fully populated before the class modules bind it as
    ``_core_facade``.

Every violation prints as ``path:line:col  CODE  message``. Exit 1 on any
violation, 0 otherwise, 2 when the expected layout itself is missing (this
gate hard-fails rather than silently skipping when paths move — see the
agent_server split postmortem).

Usage:
    python scripts/check_core_contracts.py [--root PATH]
"""
from __future__ import annotations

import argparse
import ast
import sys
import symtable
from pathlib import Path

FACADE_MODULE_ALIAS = "_core_facade"
OWNER_SUBMODULES = {"_shared", "callback_render", "notices"}
PATCH_CALL_NAMES = {"setattr", "patch", "delattr"}


class Violation:
    def __init__(self, path, line, col, code, message):
        self.path, self.line, self.col, self.code, self.message = path, line, col, code, message

    def render(self, root: Path) -> str:
        try:
            rel = self.path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line}:{self.col}  {self.code}  {self.message}"


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# --------------------------------------------------------------- tests scan
def collect_patch_targets(tests_dir: Path):
    """Return {attr: [(file, line, raising_false)]} for facade rebind sites."""
    targets: dict[str, list[tuple[Path, int, bool]]] = {}

    def add(name, path, node, raising_false):
        targets.setdefault(name, []).append((path, node.lineno, raising_false))

    for path in sorted(tests_dir.rglob("*.py")):
        try:
            tree = parse(path)
        except SyntaxError:
            continue
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "main_logic.core" and a.asname:
                        aliases.add(a.asname)
            elif isinstance(node, ast.ImportFrom) and node.module == "main_logic":
                for a in node.names:
                    if a.name == "core":
                        aliases.add(a.asname or "core")

        def is_core_ref(expr):
            if isinstance(expr, ast.Name) and expr.id in aliases:
                return True
            return (isinstance(expr, ast.Attribute) and expr.attr == "core"
                    and isinstance(expr.value, ast.Name) and expr.value.id == "main_logic")

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            raising_false = any(
                kw.arg == "raising" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                for kw in node.keywords)
            args = node.args
            if fname in PATCH_CALL_NAMES and args:
                first = args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    s = first.value
                    if s.startswith("main_logic.core.") and s.count(".") == 2:
                        add(s.rsplit(".", 1)[1], path, node, raising_false)
            if fname in (PATCH_CALL_NAMES | {"object"}) and len(args) >= 2:
                if is_core_ref(args[0]) and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                    add(args[1].value, path, node, raising_false)
    return targets


# ------------------------------------------------------------ facade layout
def facade_top_level_names(init_tree: ast.Module) -> set[str]:
    names = set()
    for node in init_tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


# ----------------------------------------------------------- module analysis
def module_level_import_bindings(tree: ast.Module):
    """{name: lineno} bound by top-level import statements."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out[a.asname or a.name] = node.lineno
    return out


def global_read_names(path: Path) -> set[str]:
    """Names referenced as globals from any function scope in the module."""
    table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    found: set[str] = set()

    def walk(tab):
        if tab.get_type() == "function":
            for sym in tab.get_symbols():
                if sym.is_referenced() and sym.is_global():
                    found.add(sym.get_name())
        for child in tab.get_children():
            walk(child)

    walk(table)
    return found


def first_name_load_line(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            return node.lineno, node.col_offset
    return 0, 0


# ------------------------------------------------------------------- checks
def run(root: Path) -> list[Violation]:
    core_dir = root / "main_logic" / "core"
    tests_dir = root / "tests"
    init_path = core_dir / "__init__.py"
    manager_path = core_dir / "manager.py"
    for required in (core_dir, tests_dir, init_path, manager_path):
        if not required.exists():
            print(f"error: expected path missing: {required} — the core package layout moved; "
                  f"update scripts/check_core_contracts.py instead of letting the gate go dark.",
                  file=sys.stderr)
            sys.exit(2)

    violations: list[Violation] = []
    init_tree = parse(init_path)
    facade_names = facade_top_level_names(init_tree)

    # -- discover mixin modules (any core/*.py defining a single *Mixin class)
    mixin_files: dict[Path, ast.ClassDef] = {}
    manager_class = None
    for path in sorted(core_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = parse(path)
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if path == manager_path:
            manager_class = classes[0] if len(classes) == 1 and classes[0].name == "LLMSessionManager" else None
            if manager_class is None:
                violations.append(Violation(path, 1, 0, "CORE_MANAGER_SHAPE",
                                            "manager.py must define exactly one class: LLMSessionManager"))
            continue
        mixins = [c for c in classes if c.name.endswith("Mixin")]
        if mixins:
            if len(classes) != 1:
                violations.append(Violation(path, classes[1].lineno, classes[1].col_offset, "CORE_MIXIN_SHAPE",
                                            f"{path.name} must define exactly one class (found {len(classes)})"))
            mixin_files[path] = mixins[0]

    # -- CORE_MIXIN_SHAPE: top level and class body
    for path, klass in mixin_files.items():
        tree = parse(path)
        for i, node in enumerate(tree.body):
            if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # module docstring
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)):
                continue
            violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MIXIN_SHAPE",
                                        f"mixin module top level allows only docstring/imports/class, "
                                        f"found {type(node).__name__}"))
        for i, node in enumerate(klass.body):
            if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # class docstring
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "__init__":
                    violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MIXIN_SHAPE",
                                                "mixins must not define __init__ — instance state has a "
                                                "single home in LLMSessionManager.__init__ (manager.py)"))
                continue
            violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MIXIN_SHAPE",
                                        f"mixin class body allows only docstring/methods, "
                                        f"found {type(node).__name__} — state belongs in manager.__init__"))

    # -- CORE_MANAGER_SHAPE: only __init__ as a method
    if manager_class is not None:
        methods = [n for n in manager_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for m in methods:
            if m.name != "__init__":
                violations.append(Violation(manager_path, m.lineno, m.col_offset, "CORE_MANAGER_SHAPE",
                                            f"manager class defines method '{m.name}' — behavior belongs "
                                            f"in a domain mixin; manager.py keeps only __init__"))
        if not any(m.name == "__init__" for m in methods):
            violations.append(Violation(manager_path, manager_class.lineno, manager_class.col_offset,
                                        "CORE_MANAGER_SHAPE", "LLMSessionManager must define __init__ here"))

    # -- CORE_MIXIN_DISJOINT
    seen: dict[str, str] = {}
    for path, klass in sorted(mixin_files.items()):
        for node in klass.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in seen:
                    violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MIXIN_DISJOINT",
                                                f"method '{node.name}' already defined in {seen[node.name]} — "
                                                f"MRO would shadow one of them silently"))
                else:
                    seen[node.name] = f"{path.name}:{klass.name}"

    # -- CORE_MIXIN_BASES
    if manager_class is not None:
        base_names = {b.id for b in manager_class.bases if isinstance(b, ast.Name)}
        mixin_names = {k.name for k in mixin_files.values()}
        for missing in sorted(mixin_names - base_names):
            violations.append(Violation(manager_path, manager_class.lineno, manager_class.col_offset,
                                        "CORE_MIXIN_BASES",
                                        f"mixin class {missing} is defined in the package but is not a "
                                        f"base of LLMSessionManager"))
        for extra in sorted(base_names - mixin_names):
            violations.append(Violation(manager_path, manager_class.lineno, manager_class.col_offset,
                                        "CORE_MIXIN_BASES",
                                        f"base {extra} has no *Mixin class defined in the package"))

    # -- CORE_FACADE_LAYOUT
    for node in init_tree.body:
        if isinstance(node, ast.ClassDef):
            violations.append(Violation(init_path, node.lineno, node.col_offset, "CORE_FACADE_LAYOUT",
                                        f"__init__.py must not define classes (found {node.name}) — the "
                                        f"facade only re-exports; the class lives in manager.py"))
    last = init_tree.body[-1] if init_tree.body else None
    is_manager_import = (isinstance(last, ast.ImportFrom) and last.level == 1 and last.module == "manager"
                         and [a.name for a in last.names] == ["LLMSessionManager"])
    if not is_manager_import:
        violations.append(Violation(init_path, getattr(last, "lineno", 1), 0, "CORE_FACADE_LAYOUT",
                                    "the last statement of __init__.py must be "
                                    "'from .manager import LLMSessionManager' so the facade namespace is "
                                    "fully populated before the class modules bind it"))

    # -- patch-target checks
    targets = collect_patch_targets(tests_dir)
    routing_files = sorted(set(mixin_files) | {manager_path})
    module_info = {}
    for path in routing_files:
        tree = parse(path)
        module_info[path] = (tree, module_level_import_bindings(tree), global_read_names(path))

    for attr, sites in sorted(targets.items()):
        strict_sites = [s for s in sites if not s[2]]
        if attr not in facade_names:
            for site_path, line, raising_false in sites:
                if not raising_false:
                    violations.append(Violation(site_path, line, 0, "CORE_PATCH_TARGET_EXISTS",
                                                f"test patches main_logic.core.{attr} but the facade defines "
                                                f"no such name (typo? pass raising=False for negative guards)"))
            continue
        if not strict_sites:
            continue  # raising=False negative guards need no routing
        for path, (tree, imports, global_reads) in module_info.items():
            if attr in imports:
                violations.append(Violation(path, imports[attr], 0, "CORE_PATCH_ROUTING",
                                            f"'{attr}' is a test patch target on the facade but is "
                                            f"from-imported here at module level — the import snapshots the "
                                            f"value and facade patches no longer reach this module; read it "
                                            f"as {FACADE_MODULE_ALIAS}.{attr} instead"))
            elif attr in global_reads:
                line, col = first_name_load_line(tree, attr)
                violations.append(Violation(path, line, col, "CORE_PATCH_ROUTING",
                                            f"'{attr}' is a test patch target on the facade but is read here "
                                            f"as a bare global — read it as {FACADE_MODULE_ALIAS}.{attr} so "
                                            f"monkeypatch.setattr(\"main_logic.core.{attr}\", ...) keeps "
                                            f"hitting this consumer"))
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--list-targets", action="store_true",
                    help="print the harvested facade patch-target set and exit")
    args = ap.parse_args()
    root = Path(args.root)

    if args.list_targets:
        for attr, sites in sorted(collect_patch_targets(root / "tests").items()):
            files = sorted({p.name for p, _, _ in sites})
            print(f"{attr:50s} {files}")
        return 0

    violations = run(root)
    for v in violations:
        print(v.render(root))
    if violations:
        print(f"\n{len(violations)} core-contract violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
