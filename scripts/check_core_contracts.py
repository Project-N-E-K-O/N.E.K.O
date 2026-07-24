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
    module object. A module-level from-import of the symbol's REAL binding —
    ``from <owner_module> import <original_name>`` (the module the facade
    re-exports it from) or ``from main_logic.core import <attr>`` (the facade
    itself) — snapshots the value at import time: assertion-style stubs then
    fail loudly, but isolation-style stubs (blocking disk/network IO) go
    silently green while calling the real function. Matching is by SOURCE
    MODULE + original name, so an unrelated ``from vendor import
    get_tts_worker as vw`` (a different object that merely shares the name) is
    NOT flagged. The patched-symbol set is harvested from ``tests/`` by AST
    on every run, so the contract tightens automatically as tests grow. Reads
    through the OWNER module — attribute chains (``import
    main_logic.agent_event_bus`` / ``from main_logic import agent_event_bus
    as bus`` + ``bus.<attr>(...)``) and string getattr (``getattr(bus,
    "<attr>")``) — are rejected the same way. So are facade reads inside
    method defaults/decorators/annotations and class-level constant values:
    those evaluate once at import and freeze the value. Function-local imports
    from the OWNER module (e.g. ``from utils.preferences import ...`` inside a
    method) are a different, deliberate late-binding pattern and stay allowed:
    the facade patch never targeted them, before or after the split.

    The patch-target harvest recognizes both positional and keyword call
    spellings, string (``"main_logic.core.X"``) and object
    (``setattr(core_module, "X", ...)``) forms, and object targets reached
    through a package alias (``import main_logic as ml`` -> ``ml.core``).

CORE_PATCH_TARGET_EXISTS
    Every patched facade attribute must actually exist at the facade top
    level (typo guard), unless the call passes ``raising=False`` or
    ``create=True`` (intentional absent-name guards).

CORE_MIXIN_SHAPE
    A mixin module's top level holds only a docstring, imports, exactly one
    ``*Mixin`` class, and any explicitly registered private support classes;
    the mixin class body holds only a docstring and
    methods, and the class has an empty base list (a base would pull
    inherited behavior into the MRO uncounted). Instance state has a single
    home (``LLMSessionManager.__init__``), and module-level state in a
    mixin would sit outside the facade's rebind semantics entirely. Any
    core module that is neither a mixin, ``manager.py`` nor a registered
    owner submodule is rejected — adding a new owner module is a conscious
    edit to this check.

CORE_MANAGER_SHAPE
    ``manager.py`` holds exactly one class, ``LLMSessionManager``, and its
    top level holds nothing but docstring/imports/that class; the class
    body holds nothing but a docstring, class-level constants and
    ``__init__``. Any other method, nested class or executable statement is
    behavior/state that belongs in a domain mixin.

CORE_MIXIN_DISJOINT
    No method name is defined by two mixin classes (or by both a mixin and
    the manager class). Python would resolve the clash silently by MRO
    order; this makes it a build failure instead.

CORE_MIXIN_BASES
    The base list of ``LLMSessionManager`` is exactly the set of ``*Mixin``
    classes defined in the package — no orphan mixin module, no missing
    base, every base a plain name (dotted/computed bases would make the
    exact-set comparison silently incomplete), and every base bound via a
    package-relative import (``from .focus import FocusMixin``) so a
    same-named class from outside the package cannot take the MRO slot.

CORE_FACADE_LAYOUT
    ``__init__.py`` defines no class at top level, and its last statement
    is ``from .manager import LLMSessionManager`` — the facade namespace
    must be fully populated before the class modules bind it as
    ``_core_facade``.

ASR_LAYERING
    The Core ASR bridge owns microphone ingress and Core callbacks, while the
    independent runtime owns provider state. TTS cannot import ASR, ASR cannot
    import Core, provider literals cannot leak into the bridge, and streaming
    can only enqueue audio into the bridge.

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
from pathlib import Path

FACADE_MODULE_ALIAS = "_core_facade"
OWNER_SUBMODULES = {
    "_shared",
    "callback_render",
    "notices",
}
MIXIN_SUPPORT_CLASSES = {
    "asr_runtime": {
        "_QueuedMicFrame",
        "_AudioDurationQueue",
        "_HotSwapAudioFrame",
        "_HotSwapAudioBuffer",
    },
}
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
    # Read BYTES, not text: ast.parse honors a PEP 263 coding cookie
    # (gbk/shift-jis/latin-1) and strips a UTF-8 BOM. read_text(encoding="utf-8")
    # would keep a BOM (→ SyntaxError) or raise UnicodeDecodeError on a non-UTF-8
    # cookie — either one crashes the whole gate on a valid, importable file
    # (BOM is realistic here: Windows + PowerShell `Out-File -Encoding utf8`).
    return ast.parse(path.read_bytes(), filename=str(path))


# --------------------------------------------------------------- tests scan
def collect_patch_targets(tests_dir: Path):
    """Return {attr: [(file, line, raising_false)]} for facade rebind sites."""
    targets: dict[str, list[tuple[Path, int, bool]]] = {}

    def add(name, path, node, exempt):
        targets.setdefault(name, []).append((path, node.lineno, exempt))

    for path in sorted(tests_dir.rglob("*.py")):
        try:
            tree = parse(path)
        except (SyntaxError, ValueError):
            # Malformed/undecodable test file: skip its harvest rather than
            # crash the gate (ValueError covers UnicodeDecodeError).
            continue
        aliases = set()        # names bound to the main_logic.core MODULE
        pkg_aliases = set()    # names bound to the main_logic PACKAGE (for ``ml.core``)
        patch_aliases = set()  # extra names meaning unittest.mock.patch
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "main_logic.core" and a.asname:
                        aliases.add(a.asname)           # import main_logic.core as core_module
                    elif a.name == "main_logic" and a.asname:
                        pkg_aliases.add(a.asname)        # import main_logic as ml
                    elif a.name == "main_logic" or a.name.startswith("main_logic."):
                        pkg_aliases.add("main_logic")    # import main_logic[.core] → binds main_logic
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module == "main_logic":
                    for a in node.names:
                        if a.name == "core":
                            aliases.add(a.asname or "core")
                elif node.module in ("unittest.mock", "mock"):
                    for a in node.names:
                        if a.name == "patch" and a.asname:
                            patch_aliases.add(a.asname)  # from unittest.mock import patch as mock_patch

        def is_core_ref(expr):
            if isinstance(expr, ast.Name) and expr.id in aliases:
                return True
            # ``main_logic.core`` or ``<pkg-alias>.core`` (import main_logic as ml)
            return (isinstance(expr, ast.Attribute) and expr.attr == "core"
                    and isinstance(expr.value, ast.Name) and expr.value.id in pkg_aliases)

        def is_patch_ref(expr):  # a Name/Attribute referring to unittest.mock.patch
            if isinstance(expr, ast.Name):
                return expr.id == "patch" or expr.id in patch_aliases
            return isinstance(expr, ast.Attribute) and expr.attr == "patch"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            fbase = fn.value if isinstance(fn, ast.Attribute) else None
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            # A patch that need not resolve to a real facade attribute:
            #   raising=False  (pytest monkeypatch negative guard)
            #   create=True    (unittest.mock.patch / patch.object absent-name guard)
            # These waive the EXISTENCE requirement (target may be absent); they
            # do NOT waive routing when the target does exist (see run()).
            exempt = ((isinstance(kw.get("raising"), ast.Constant) and kw["raising"].value is False)
                      or (isinstance(kw.get("create"), ast.Constant) and kw["create"].value is True))
            args = node.args
            # Positional and keyword spellings are equivalent for these APIs:
            # setattr(target=..., name=...), patch(target=...),
            # patch.object(target=..., attribute=...).
            first = args[0] if args else kw.get("target")
            second = args[1] if len(args) >= 2 else (kw.get("name") or kw.get("attribute"))
            # monkeypatch.setattr/delattr, patch("...") and aliased patch("...")
            is_str_patch = fname in PATCH_CALL_NAMES or (isinstance(fn, ast.Name) and fn.id in patch_aliases)
            if is_str_patch and first is not None:
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    s = first.value
                    if s.startswith("main_logic.core.") and s.count(".") == 2:
                        add(s.rsplit(".", 1)[1], path, node, exempt)
            # object form: setattr(core_module, "X"), patch.object(core_module, "X").
            # ``.object`` counts only on a real patch receiver — do NOT fold
            # "object" into the union or ``x.object(core_ref, "y")`` on any
            # receiver would be mis-harvested (the is_patch_ref check would be
            # dead code and could invent false patch targets).
            is_obj_patch = fname in PATCH_CALL_NAMES or (fname == "object" and is_patch_ref(fbase))
            if is_obj_patch and first is not None and second is not None:
                if is_core_ref(first) and isinstance(second, ast.Constant) and isinstance(second.value, str):
                    add(second.value, path, node, exempt)
            # patch.multiple("main_logic.core", X=..., Y=...) / (core_module, X=...)
            if fname == "multiple" and is_patch_ref(fbase) and first is not None:
                target_is_core = (
                    (isinstance(first, ast.Constant) and first.value == "main_logic.core")
                    or is_core_ref(first))
                if target_is_core:
                    reserved = {"spec", "spec_set", "create", "autospec", "new_callable", "target"}
                    for k in node.keywords:
                        if k.arg and k.arg not in reserved:
                            add(k.arg, path, node, exempt)
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


def facade_owner_modules(init_tree: ast.Module) -> dict[str, tuple[str, str]]:
    """{facade name: (absolute owner module, ORIGINAL symbol name)}.

    The facade lives at package ``main_logic.core``, so relative re-exports
    (``from ._shared import CROSS_MODE_RESTART_WAIT_SECONDS``) resolve against
    it. The original name matters for aliased re-exports
    (``from ...bus import dispatch_text_user_message as send_text`` → facade
    name ``send_text`` but the owner attribute is ``dispatch_text_user_message``)
    so ``owner_module_reads`` searches the owner for the RIGHT attribute.
    """
    out: dict[str, tuple[str, str]] = {}
    for node in init_tree.body:
        if isinstance(node, ast.ImportFrom):
            base = node.module if node.level == 0 else _resolve_relative("main_logic.core", node.level, node.module)
            if not base:
                continue
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = (base, a.name)
    return out


# ----------------------------------------------------------- module analysis
def facade_snapshot_imports(tree: ast.Module, pkg: str,
                            facade_owners: dict[str, tuple[str, str]]) -> dict[str, int]:
    """{facade attr: lineno} for module-level from-imports that SNAPSHOT the
    facade's real symbol — a binding the facade patch will not reach.

    A snapshot is ``from <owner_module> import <original_name> [as x]`` (the
    real symbol, under any alias) or ``from main_logic.core import <attr>`` (the
    facade's own copy, frozen at import). Crucially, matching is by SOURCE
    MODULE + original name, not by name alone: an unrelated
    ``from vendor import get_tts_worker as vw`` imports a DIFFERENT object and
    must NOT be flagged (that was a false positive of the old name-only check).
    """
    by_owner = {(mod, orig): attr for attr, (mod, orig) in facade_owners.items()}
    out: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        src = node.module if node.level == 0 else _resolve_relative(pkg, node.level, node.module)
        if not src:
            continue
        for a in node.names:
            if a.name == "*":
                continue
            attr = by_owner.get((src, a.name))
            if attr is not None:
                out.setdefault(attr, node.lineno)          # from owner import orig_name
            elif src == "main_logic.core" and a.name in facade_owners:
                out.setdefault(a.name, node.lineno)         # from the facade itself
    return out


def _resolve_relative(pkg: str, level: int, module) -> str | None:
    """Absolute dotted base for a relative import in package ``pkg``.

    ``from . import x`` (level 1) in ``main_logic.core`` anchors at
    ``main_logic.core``; ``from .. import x`` (level 2) at ``main_logic``.
    Returns None if the level escapes the top of the tree.
    """
    parts = pkg.split(".") if pkg else []
    keep = len(parts) - (level - 1)
    if keep < 0:
        return None
    anchor = parts[:keep]
    if module:
        anchor = anchor + module.split(".")
    return ".".join(anchor)


def module_alias_paths(tree: ast.Module, pkg: str) -> dict[str, str]:
    """Module/name bindings at top level → ABSOLUTE dotted path.

    ``pkg`` is the importing module's package (``main_logic.core``) so relative
    imports resolve to absolute paths and compare cleanly against
    ``main_logic.core``. Covers ``import a.b`` (binds ``a``; full chain kept
    too), ``import a.b as c``, package aliases (``import main_logic as ml`` →
    ``ml`` → ``main_logic``, so ``ml.core`` resolves via prefix), and both
    absolute and relative from-imports (``from main_logic import agent_event_bus
    as bus`` / ``from .. import agent_event_bus as bus`` → the same owner
    module; ``from main_logic import core as _core_facade`` → ``main_logic.core``).

    From-imports of plain symbols land here too, but that is harmless: the
    routing scan (``owner_module_reads``) only flags a ``<binding>.<attr>`` read
    when the binding resolves to the attr's ACTUAL owner module, so a plain
    imported object with a coincidentally same-named method never matches.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    root = a.name.split(".")[0]
                    out.setdefault(root, root)
                    out[a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module if node.level == 0 else _resolve_relative(pkg, node.level, node.module)
            if base is None:
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                out[a.asname or a.name] = f"{base}.{a.name}"
    return out


def resolve_chain(chain: str, alias_paths: dict[str, str]) -> str | None:
    """Resolve a dotted read chain to an absolute module path, or None.

    Exact binding first (``bus`` → ``main_logic.agent_event_bus``); else
    substitute a bound prefix (``ml.agent_event_bus`` where ``ml`` →
    ``main_logic`` yields ``main_logic.agent_event_bus``).
    """
    if chain in alias_paths:
        return alias_paths[chain]
    parts = chain.split(".")
    if parts[0] in alias_paths:
        rest = parts[1:]
        return ".".join([alias_paths[parts[0]], *rest]) if rest else alias_paths[parts[0]]
    return None


def dotted_node_path(node):
    """Dotted string of a Name/Attribute node itself, or None."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def attr_value_chain(node: ast.Attribute):
    """Dotted string of an attribute node's value side, or None."""
    return dotted_node_path(node.value)


def owner_module_reads(tree: ast.Module, alias_paths: dict[str, str], owner_info):
    """(line, col, module) for reads of the owner attribute that a facade patch
    would miss.

    ``owner_info`` is ``(owner_module, original_name)``: the facade re-exports
    the patched symbol from ``owner_module`` under ``original_name`` (which
    differs from the facade name only for aliased re-exports). A monkeypatch of
    ``main_logic.core.<facade_name>`` rebinds only the facade copy, so a mixin
    that reads ``owner_module.original_name`` (or ``getattr(...)``) sees the
    un-patched original. Matching the SPECIFIC owner module and its ORIGINAL
    attribute name avoids flagging a coincidental same-named attribute on an
    unrelated object. If ``owner_info`` is unknown (attr not from-imported by
    the facade) nothing is flagged here.
    """
    if not owner_info:
        return []
    owner, name = owner_info
    sites = []
    for node in ast.walk(tree):
        chain = None
        if isinstance(node, ast.Attribute) and node.attr == name:
            chain = attr_value_chain(node)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and node.args[1].value == name):
            chain = dotted_node_path(node.args[0])
        if chain is None:
            continue
        if resolve_chain(chain, alias_paths) == owner:
            sites.append((node.lineno, node.col_offset, owner))
    return sites


def def_time_facade_reads(tree: ast.Module, alias_paths: dict[str, str], attr: str):
    """(line, col) of facade reads of ``attr`` evaluated at class-creation.

    Class decorators, method decorators, defaults, evaluated annotations and
    class-level constant values run ONCE at import time; a facade read there
    (attribute ``_core_facade.<attr>`` or ``getattr(_core_facade, "<attr>")``)
    freezes the value, so later facade patches no longer reach it — the read
    must live in the method body.
    """
    sites = []
    for klass in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        def_time_nodes = list(klass.decorator_list)  # the class's own decorators
        for stmt in klass.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = stmt.args
                def_time_nodes += stmt.decorator_list
                def_time_nodes += [d for d in a.defaults if d is not None]
                def_time_nodes += [d for d in a.kw_defaults if d is not None]
                for arg in (a.posonlyargs + a.args + a.kwonlyargs
                            + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])):
                    if arg.annotation is not None:
                        def_time_nodes.append(arg.annotation)
                if stmt.returns is not None:
                    def_time_nodes.append(stmt.returns)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                # Class-level constants (allowed in manager.py) evaluate at
                # class creation too — a facade read there freezes the value.
                if stmt.value is not None:
                    def_time_nodes.append(stmt.value)
        for sub in def_time_nodes:
            for node in ast.walk(sub):
                chain = None
                if isinstance(node, ast.Attribute) and node.attr == attr:
                    chain = attr_value_chain(node)
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == "getattr" and len(node.args) >= 2
                      and isinstance(node.args[1], ast.Constant) and node.args[1].value == attr):
                    chain = dotted_node_path(node.args[0])
                if chain and resolve_chain(chain, alias_paths) == "main_logic.core":
                    sites.append((node.lineno, node.col_offset))
    return sites


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
    facade_owners = facade_owner_modules(init_tree)

    # -- the core package must stay flat: a subpackage would define classes the
    #    *.py discovery below never scans, so its mixins/state escape every
    #    shape/routing check. Reject any subdirectory carrying Python modules.
    for sub in sorted(p for p in core_dir.iterdir() if p.is_dir() and p.name != "__pycache__"):
        # rglob, not glob: a nested tree (helpers/nested/mod.py) is still
        # importable as main_logic.core.helpers.nested.mod and must be rejected.
        if any(q for q in sub.rglob("*.py") if "__pycache__" not in q.parts):
            violations.append(Violation(sub / "__init__.py", 1, 0, "CORE_MIXIN_SHAPE",
                                        f"core subpackage '{sub.name}/' is not allowed — the core package is "
                                        f"flat so every module is covered by the contract checks; keep new "
                                        f"code in a top-level core/*.py mixin or owner submodule"))

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
            else:
                # A class decorator or metaclass runs at class creation and can
                # inject/replace methods after the AST is counted — invisible to
                # every body/base/import check below.
                if manager_class.decorator_list:
                    d = manager_class.decorator_list[0]
                    violations.append(Violation(path, d.lineno, d.col_offset, "CORE_MANAGER_SHAPE",
                                                "LLMSessionManager must not be decorated — a class decorator "
                                                "can inject methods/state the shape checks cannot see"))
                if manager_class.keywords:  # metaclass= or other class kwargs
                    k = manager_class.keywords[0]
                    violations.append(Violation(path, k.value.lineno, k.value.col_offset, "CORE_MANAGER_SHAPE",
                                                f"LLMSessionManager must not set class keyword "
                                                f"'{k.arg or '**kwargs'}' (metaclass/kwargs) — it can rewrite "
                                                f"the class outside the mixin contract"))
            for i, node in enumerate(tree.body):
                if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue  # module docstring
                if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)):
                    continue
                violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MANAGER_SHAPE",
                                            f"manager.py top level allows only docstring/imports/class, "
                                            f"found {type(node).__name__}"))
            continue
        mixins = [c for c in classes if c.name.endswith("Mixin")]
        if mixins:
            expected_classes = {
                mixins[0].name,
                *MIXIN_SUPPORT_CLASSES.get(path.stem, set()),
            }
            actual_classes = {klass.name for klass in classes}
            if len(mixins) != 1 or actual_classes != expected_classes:
                violations.append(Violation(
                    path,
                    classes[0].lineno if classes else 1,
                    classes[0].col_offset if classes else 0,
                    "CORE_MIXIN_SHAPE",
                    f"{path.name} classes must be exactly {sorted(expected_classes)} "
                    f"(found {sorted(actual_classes)})",
                ))
            # A base on a mixin drags inherited methods/state into
            # LLMSessionManager's MRO uncounted by CORE_MIXIN_DISJOINT/BASES.
            if mixins[0].bases or mixins[0].keywords:
                b = (mixins[0].bases or [kw.value for kw in mixins[0].keywords])[0]
                violations.append(Violation(path, b.lineno, b.col_offset, "CORE_MIXIN_SHAPE",
                                            f"mixin class {mixins[0].name} must have an empty base list — a "
                                            f"base would smuggle behavior into LLMSessionManager's MRO "
                                            f"outside the mixin contract"))
            # A class decorator runs at class creation and can inject/replace
            # methods after the AST body is counted — invisible to DISJOINT and
            # the manager-base checks. Mixins must be plain, undecorated bags.
            if mixins[0].decorator_list:
                d = mixins[0].decorator_list[0]
                violations.append(Violation(path, d.lineno, d.col_offset, "CORE_MIXIN_SHAPE",
                                            f"mixin class {mixins[0].name} must not be decorated — a class "
                                            f"decorator can add methods/state into the MRO uncounted by the "
                                            f"other checks"))
            mixin_files[path] = mixins[0]
        elif path.stem not in OWNER_SUBMODULES:
            violations.append(Violation(path, 1, 0, "CORE_MIXIN_SHAPE",
                                        f"unknown core module '{path.name}': every core module must either "
                                        f"define one *Mixin class or be a registered owner submodule — add it "
                                        f"to OWNER_SUBMODULES in scripts/check_core_contracts.py if it is a "
                                        f"deliberate new owner module"))

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
                # A mutable-container default (``def f(self, cache={})``) is
                # allocated once at import and SHARED across every instance —
                # the exact hidden mixin state this contract keeps out (same
                # rationale as the module-level/class-body state rejections).
                # GeneratorExp too: a generator default is a single-use iterator
                # allocated once at import and shared, so a second instance sees
                # it already exhausted — the same shared-state hazard.
                MUT = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp,
                       ast.SetComp, ast.GeneratorExp)
                for dflt in [d for d in node.args.defaults if d] + [d for d in node.args.kw_defaults if d]:
                    bad = next((s for s in ast.walk(dflt) if isinstance(s, MUT)), None)
                    if bad is not None:
                        violations.append(Violation(path, dflt.lineno, dflt.col_offset, "CORE_MIXIN_SHAPE",
                                                    f"mixin method '{node.name}' has a mutable default argument "
                                                    f"— it is allocated once at import and shared across all "
                                                    f"instances; use None and build inside the method"))
                continue
            violations.append(Violation(path, node.lineno, node.col_offset, "CORE_MIXIN_SHAPE",
                                        f"mixin class body allows only docstring/methods, "
                                        f"found {type(node).__name__} — state belongs in manager.__init__"))

    # -- ASR_LAYERING: keep microphone ingress, Core integration, and provider
    # runtime ownership on their explicit sides of the composition boundary.
    asr_bridge_path = core_dir / "asr_runtime.py"
    tts_path = core_dir / "tts_runtime.py"
    streaming_path = core_dir / "streaming.py"
    asr_client_dir = root / "main_logic" / "asr_client"
    asr_component_path = asr_client_dir / "runtime.py"
    asr_audio_path = asr_client_dir / "audio.py"
    voice_input_path = root / "main_logic" / "voice_turn" / "audio_input.py"
    for required in (
        asr_bridge_path,
        tts_path,
        streaming_path,
        asr_client_dir,
        asr_component_path,
        asr_audio_path,
        voice_input_path,
    ):
        if not required.exists():
            violations.append(Violation(
                required,
                1,
                0,
                "ASR_LAYERING",
                "required ASR layering path is missing",
            ))

    if tts_path.exists():
        tts_tree = parse(tts_path)
        forbidden_ingress_methods = {
            "_ensure_audio_stream_worker",
            "_clear_audio_stream_queue",
            "_cancel_audio_stream_worker",
            "_enqueue_audio_stream_data",
            "_audio_stream_worker_loop",
        }
        for node in ast.walk(tts_tree):
            imported = None
            if isinstance(node, ast.Import):
                imported = next(
                    (a.name for a in node.names if a.name.startswith("main_logic.asr_client")),
                    None,
                )
            elif isinstance(node, ast.ImportFrom):
                imported = node.module
            if imported and imported.startswith("main_logic.asr_client"):
                violations.append(Violation(
                    tts_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    "tts_runtime.py must not import main_logic.asr_client",
                ))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in forbidden_ingress_methods
            ):
                violations.append(Violation(
                    tts_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    f"microphone ingress method '{node.name}' belongs in core/asr_runtime.py",
                ))

    if asr_client_dir.exists():
        for path in sorted(asr_client_dir.rglob("*.py")):
            tree = parse(path)
            pkg = ".".join(path.relative_to(root).parts[:-1])
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    resolved = (
                        node.module
                        if node.level == 0
                        else _resolve_relative(pkg, node.level, node.module)
                    )
                    if resolved:
                        modules.append(resolved)
                if any(
                    module == "main_logic.core"
                    or module.startswith("main_logic.core.")
                    for module in modules
                ):
                    violations.append(Violation(
                        path,
                        node.lineno,
                        node.col_offset,
                        "ASR_LAYERING",
                        "asr_client must not import main_logic.core",
                    ))

    if asr_bridge_path.exists():
        bridge_tree = parse(asr_bridge_path)
        route_setter_found = False
        forbidden_runtime_reads = {"lifecycle", "route_mode", "required"}
        for node in ast.walk(bridge_tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().lower() in {"soniox", "gemini", "free"}
            ):
                violations.append(Violation(
                    asr_bridge_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    f"provider literal '{node.value}' must stay below the Core ASR bridge",
                ))
            if (
                isinstance(node, ast.Attribute)
                and node.attr in forbidden_runtime_reads
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == "_asr_runtime"
            ):
                violations.append(Violation(
                    asr_bridge_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    f"Core must not read IndependentAsrRuntime.{node.attr}",
                ))
        for node in ast.walk(bridge_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name == "_set_microphone_route":
                route_setter_found = True
            if node.name in {"_init_asr_runtime_state", "_set_microphone_route"}:
                continue
            for child in ast.walk(node):
                targets = []
                if isinstance(child, ast.Assign):
                    targets = child.targets
                elif isinstance(child, ast.AnnAssign):
                    targets = [child.target]
                if any(
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == "_asr_route_mode"
                    for target in targets
                ):
                    violations.append(Violation(
                        asr_bridge_path,
                        child.lineno,
                        child.col_offset,
                        "ASR_LAYERING",
                        "Core route changes must go through _set_microphone_route()",
                    ))
        if not route_setter_found:
            violations.append(Violation(
                asr_bridge_path,
                1,
                0,
                "ASR_LAYERING",
                "core/asr_runtime.py must define _set_microphone_route()",
            ))

    for path in (asr_bridge_path, asr_component_path):
        if not path.exists():
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "ProcessedVoiceFrame" for alias in node.names
            ) and node.module != "main_logic.voice_turn.audio_input":
                violations.append(Violation(
                    path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    "ProcessedVoiceFrame must come from voice_turn.audio_input",
                ))

    if asr_audio_path.exists():
        for node in ast.walk(parse(asr_audio_path)):
            if isinstance(node, ast.ClassDef) and node.name in {
                "ProcessedVoiceFrame",
                "VoiceInputAudioPipeline",
            }:
                violations.append(Violation(
                    asr_audio_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    f"provider-neutral {node.name} belongs in voice_turn/audio_input.py",
                ))

    if asr_component_path.exists():
        component_tree = parse(asr_component_path)
        component_class = next(
            (
                node
                for node in component_tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == "IndependentAsrRuntime"
            ),
            None,
        )
        if component_class is None:
            violations.append(Violation(
                asr_component_path,
                1,
                0,
                "ASR_LAYERING",
                "asr_client/runtime.py must define IndependentAsrRuntime",
            ))
        elif component_class.bases or component_class.keywords:
            violations.append(Violation(
                asr_component_path,
                component_class.lineno,
                component_class.col_offset,
                "ASR_LAYERING",
                "IndependentAsrRuntime must be a plain composed object, not a mixin subclass",
            ))
        if component_class is not None:
            forbidden_fields = {
                "_asr_route_mode",
                "_asr_required",
                "_voice_lease_connection_id",
                "_voice_lease_generation",
                "_voice_lease_synchronized",
                "_voice_lease_owner",
                "_voice_lease_hard_muted",
                "_voice_lease_focus_suppressed",
                "_voice_input_suppressed",
            }
            forbidden_methods = {
                "activate_native_route",
                "deactivate_audio_route",
                "block_audio_route",
                "sync_voice_lease",
                "apply_voice_lease_state",
                "process_audio",
                "_process_microphone_audio",
            }
            public_methods = {
                "__init__",
                "display_name",
                "close",
                "capture_ingress_token",
                "suspend",
                "resume",
                "abort",
                "wait_transcript_idle",
                "start",
                "submit",
            }
            for node in ast.walk(component_class):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and node.attr in forbidden_fields
                ):
                    violations.append(Violation(
                        asr_component_path,
                        node.lineno,
                        node.col_offset,
                        "ASR_LAYERING",
                        f"IndependentAsrRuntime must not mirror Core state {node.attr}",
                    ))
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node in component_class.body
                ):
                    if node.name in forbidden_methods:
                        violations.append(Violation(
                            asr_component_path,
                            node.lineno,
                            node.col_offset,
                            "ASR_LAYERING",
                            f"IndependentAsrRuntime must not define {node.name}()",
                        ))
                    if not node.name.startswith("_") and node.name not in public_methods:
                        violations.append(Violation(
                            asr_component_path,
                            node.lineno,
                            node.col_offset,
                            "ASR_LAYERING",
                            f"unexpected IndependentAsrRuntime public method {node.name}()",
                        ))
            for node in ast.walk(component_tree):
                if (
                    isinstance(node, ast.Name)
                    and node.id == "VoiceInputAudioPipeline"
                ):
                    violations.append(Violation(
                        asr_component_path,
                        node.lineno,
                        node.col_offset,
                        "ASR_LAYERING",
                        "IndependentAsrRuntime must not own the Core PCM pipeline",
                    ))

        callbacks_class = next(
            (
                node for node in component_tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == "AsrRuntimeCallbacks"
            ),
            None,
        )
        callback_events = {
            "on_partial": "VoicePartialEvent",
            "on_final": "VoiceTranscriptEvent",
            "on_failure": "AsrFailureEvent",
            "on_status": "AsrStatusEvent",
            "on_lifecycle": "AsrLifecycleNotification",
        }
        annotations = {
            node.target.id: {name.id for name in ast.walk(node.annotation) if isinstance(name, ast.Name)}
            for node in (callbacks_class.body if callbacks_class is not None else [])
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        for callback_name, event_name in callback_events.items():
            if event_name not in annotations.get(callback_name, set()):
                violations.append(Violation(
                    asr_component_path,
                    getattr(callbacks_class, "lineno", 1),
                    0,
                    "ASR_LAYERING",
                    f"{callback_name} must receive immutable {event_name}",
                ))
        for node in ast.walk(component_tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                violations.append(Violation(
                    asr_component_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    "asr_client/runtime.py must not define a manager mixin",
                ))
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr
                in {
                    "session",
                    "handle_new_message",
                    "handle_input_transcript",
                    "send_status",
                }
            ):
                violations.append(Violation(
                    asr_component_path,
                    node.lineno,
                    node.col_offset,
                    "ASR_LAYERING",
                    f"IndependentAsrRuntime must not access Core attribute self.{node.attr}",
                ))

    if streaming_path.exists():
        streaming_tree = parse(streaming_path)
        stream_data_method = next(
            (
                node
                for node in ast.walk(streaming_tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "stream_data"
            ),
            None,
        )
        audio_branch = None
        if stream_data_method is not None:
            for node in ast.walk(stream_data_method):
                if (
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and len(node.test.ops) == 1
                    and isinstance(node.test.ops[0], ast.Eq)
                    and len(node.test.comparators) == 1
                    and isinstance(node.test.comparators[0], ast.Constant)
                    and node.test.comparators[0].value == "audio"
                ):
                    audio_branch = node
                    break
        valid_audio_branch = False
        if audio_branch is not None and len(audio_branch.body) == 2:
            call_stmt, return_stmt = audio_branch.body
            awaited = call_stmt.value if isinstance(call_stmt, ast.Expr) else None
            call = awaited.value if isinstance(awaited, ast.Await) else None
            valid_audio_branch = bool(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "self"
                and call.func.attr == "_enqueue_audio_stream_data"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "message"
                and not call.keywords
                and isinstance(return_stmt, ast.Return)
                and return_stmt.value is None
            )
        if not valid_audio_branch:
            violations.append(Violation(
                streaming_path,
                getattr(audio_branch or stream_data_method, "lineno", 1),
                getattr(audio_branch or stream_data_method, "col_offset", 0),
                "ASR_LAYERING",
                "stream_data audio branch may only await _enqueue_audio_stream_data(message) and return",
            ))

    # -- CORE_MANAGER_SHAPE: class body is only docstring / class constants /
    #    __init__. Any other statement (extra method, nested class, class-level
    #    cache, executable logic) is behavior/state that belongs in a mixin.
    mixin_method_names = {n.name for k in mixin_files.values() for n in k.body
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if manager_class is not None:
        methods = [n for n in manager_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for i, node in enumerate(manager_class.body):
            if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # class docstring
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Class-level CONSTANTS are allowed, but a mutable container
                # literal (``CACHE = {}``) is shared state, a Call
                # (``TOKEN = open_socket()``) runs behavior at import, and a
                # Lambda (``extra = lambda self: ...``) is a method in disguise
                # — all drift this contract prevents. Recurse: a nested literal
                # (``TOKEN = (open_socket(),)``) hides it behind an outer Tuple.
                # ``node.value`` is None for an annotation-only attr (``x: int``)
                # — nothing to evaluate, so it is fine.
                MUTABLE = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp,
                           ast.SetComp, ast.GeneratorExp, ast.Call, ast.Lambda)
                bad = None if node.value is None else \
                    next((s for s in ast.walk(node.value) if isinstance(s, MUTABLE)), None)
                if bad is not None:
                    kind = ("a Call (import-time behavior)" if isinstance(bad, ast.Call)
                            else "a lambda (a method in disguise)" if isinstance(bad, ast.Lambda)
                            else "a mutable container (shared instance state)")
                    violations.append(Violation(manager_path, node.lineno, node.col_offset, "CORE_MANAGER_SHAPE",
                                                f"manager class attribute is/contains {kind} — only immutable "
                                                f"class constants are allowed; move state into __init__ (per "
                                                f"instance) or behavior into a mixin"))
                # A class attribute that shares a name with a mixin method (or
                # with the manager's own __init__) wins attribute lookup and
                # silently removes/replaces it — ``__init__ = external`` after
                # ``def __init__`` overwrites the initializer while the method
                # count still passes. An annotation-ONLY entry (``foo: int`` with
                # no value) creates no attribute — only ``__annotations__`` — so
                # it shadows nothing and is skipped.
                if isinstance(node, ast.AnnAssign) and node.value is None:
                    targets = []
                else:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and (t.id in mixin_method_names or t.id == "__init__"):
                        what = "the manager's __init__" if t.id == "__init__" else "a mixin method"
                        violations.append(Violation(manager_path, node.lineno, node.col_offset, "CORE_MANAGER_SHAPE",
                                                    f"manager class attribute '{t.id}' shadows {what} of the same "
                                                    f"name — it would win attribute lookup and drop/replace it in "
                                                    f"LLMSessionManager's API"))
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                violations.append(Violation(manager_path, node.lineno, node.col_offset, "CORE_MANAGER_SHAPE",
                                            f"manager class defines method '{node.name}' — behavior belongs "
                                            f"in a domain mixin; manager.py keeps only __init__"))
            else:
                violations.append(Violation(manager_path, node.lineno, node.col_offset, "CORE_MANAGER_SHAPE",
                                            f"manager class body allows only docstring/constants/__init__, "
                                            f"found {type(node).__name__} — state/behavior belongs in a mixin"))
        if not any(m.name == "__init__" for m in methods):
            violations.append(Violation(manager_path, manager_class.lineno, manager_class.col_offset,
                                        "CORE_MANAGER_SHAPE", "LLMSessionManager must define __init__ here"))

    # -- CORE_MIXIN_DISJOINT: a method name defined in two DIFFERENT mixins is
    #    an MRO shadowing bug. A property group legitimately repeats the name
    #    WITHIN one mixin, but a VALID group fills each of Python's three
    #    property slots at most once: getter (@property / @x.getter), setter
    #    (@x.setter), deleter (@x.deleter). Two of any slot — or any plain
    #    method sharing the name — is a real shadow (Python keeps only the last).
    def _prop_role(fn):
        for d in fn.decorator_list:
            if isinstance(d, ast.Name) and d.id == "property":
                return "getter"
            if isinstance(d, ast.Attribute) and d.attr in ("getter", "setter", "deleter"):
                return d.attr
        return None  # a plain method

    seen: dict[str, str] = {}
    for path, klass in sorted(mixin_files.items()):
        here = f"{path.name}:{klass.name}"
        groups: dict[str, list] = {}
        for node in klass.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                groups.setdefault(node.name, []).append(node)
        for name, defs in groups.items():
            if len(defs) > 1:
                roles = [_prop_role(d) for d in defs]
                valid_group = (all(r is not None for r in roles)
                               and roles.count("getter") <= 1
                               and roles.count("setter") <= 1
                               and roles.count("deleter") <= 1)
                if not valid_group:
                    last = defs[-1]
                    violations.append(Violation(path, last.lineno, last.col_offset, "CORE_MIXIN_DISJOINT",
                                                f"method '{name}' is defined {len(defs)}× in {here} and is not a "
                                                f"valid property group (at most one each of @property getter / "
                                                f"@{name}.setter / @{name}.deleter) — a later definition shadows "
                                                f"an earlier one"))
            if name in seen:
                first = defs[0]
                violations.append(Violation(path, first.lineno, first.col_offset, "CORE_MIXIN_DISJOINT",
                                            f"method '{name}' already defined in {seen[name]} — "
                                            f"MRO would shadow one of them silently"))
            else:
                seen[name] = here

    # -- CORE_MIXIN_BASES
    if manager_class is not None:
        # Two files defining the same *Mixin name collapse in the set below and
        # only one enters the real MRO; the other is silently orphaned.
        by_name: dict[str, Path] = {}
        for mpath, mklass in sorted(mixin_files.items()):
            if mklass.name in by_name:
                violations.append(Violation(mpath, mklass.lineno, mklass.col_offset, "CORE_MIXIN_BASES",
                                            f"mixin class {mklass.name} is also defined in "
                                            f"{by_name[mklass.name].name} — duplicate names collapse and only "
                                            f"one enters the MRO; give each mixin a unique name"))
            else:
                by_name[mklass.name] = mpath
        for b in manager_class.bases:
            if not isinstance(b, ast.Name):
                violations.append(Violation(manager_path, b.lineno, b.col_offset, "CORE_MIXIN_BASES",
                                            f"non-Name base '{ast.unparse(b)}' — LLMSessionManager bases must "
                                            f"be plain *Mixin names so this check can verify the exact set"))
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
        # A base name matching a package mixin is not enough: the NAME must be
        # bound to THE module that actually defines it, i.e.
        # ``from .<defining_module> import <Mixin>`` (level 1, module == the
        # mixin's own file stem). Binding the same name from any other sibling
        # (``from ._shared import FocusMixin``) or a level-2+ import
        # (``from .. import ...``) would put a different/outside class in the
        # MRO while the real package mixin sits orphaned, yet the set check
        # would still pass. ``defining_stem`` comes from where the class was
        # discovered.
        defining_imports = {
            mklass.name: (1, mpath.stem, mklass.name)
            for mpath, mklass in mixin_files.items()
        }
        # bound name -> (relative level, module, ORIGINAL imported symbol).
        import_binds = {}
        for node in parse(manager_path).body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    import_binds[a.asname or a.name] = (
                        node.level,
                        node.module,
                        a.name,
                    )
        for name in sorted(base_names & mixin_names):
            want = defining_imports[name]
            if import_binds.get(name) != want:
                got = import_binds.get(name)
                if got is None:
                    where = "not bound via an accepted import"
                else:
                    prefix = "." * got[0]
                    where = f"bound from '{prefix}{got[1]}' as symbol '{got[2]}'"
                prefix = "." * want[0]
                violations.append(Violation(manager_path, manager_class.lineno, manager_class.col_offset,
                                            "CORE_MIXIN_BASES",
                                            f"base {name} must be imported as the class named {name} from its "
                                            f"defining module (from {prefix}{want[1]} import {name}) but is "
                                            f"{where}; the "
                                            f"MRO may be using a different/outside class while the package mixin "
                                            f"is orphaned"))

    # -- CORE_FACADE_LAYOUT: the facade only re-exports — docstring + imports.
    #    A top-level function, assignment or executable statement would put
    #    behavior/state in the facade (and, for a facade read of a patched
    #    symbol, freeze it at import) — reject anything but docstring/imports.
    for i, node in enumerate(init_tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ClassDef):
            violations.append(Violation(init_path, node.lineno, node.col_offset, "CORE_FACADE_LAYOUT",
                                        f"__init__.py must not define classes (found {node.name}) — the "
                                        f"facade only re-exports; the class lives in manager.py"))
        else:
            violations.append(Violation(init_path, node.lineno, node.col_offset, "CORE_FACADE_LAYOUT",
                                        f"__init__.py top level allows only docstring/imports, found "
                                        f"{type(node).__name__} — the facade only re-exports, no behavior/state"))
    last = init_tree.body[-1] if init_tree.body else None
    is_manager_import = (isinstance(last, ast.ImportFrom) and last.level == 1 and last.module == "manager"
                         and [a.name for a in last.names] == ["LLMSessionManager"]
                         and last.names[0].asname is None)
    if not is_manager_import:
        violations.append(Violation(init_path, getattr(last, "lineno", 1), 0, "CORE_FACADE_LAYOUT",
                                    "the last statement of __init__.py must be "
                                    "'from .manager import LLMSessionManager' so the facade namespace is "
                                    "fully populated before the class modules bind it"))
    # An EARLIER ``.manager`` import (before the re-export block finishes) would
    # import manager/mixins against a half-populated facade namespace, defeating
    # the ordering contract — the manager import must appear only as the last line.
    for node in init_tree.body[:-1]:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "manager":
            violations.append(Violation(init_path, node.lineno, node.col_offset, "CORE_FACADE_LAYOUT",
                                        "'.manager' is imported before the end of __init__.py — it must appear "
                                        "only as the final statement, after every re-export, or the mixins bind "
                                        "the facade before it is fully populated"))

    # -- patch-target checks
    targets = collect_patch_targets(tests_dir)
    # Facade patch routing applies only to core-owned modules. The explicitly
    # registered ASR mixin must read its real owner modules and must not depend
    # on the core facade.
    routing_files = sorted(
        {path for path in mixin_files if path.parent == core_dir} | {manager_path}
    )
    module_info = {}
    for path in routing_files:
        tree = parse(path)
        pkg = ".".join(path.resolve().relative_to(root.resolve()).parts[:-1])
        module_info[path] = (tree, facade_snapshot_imports(tree, pkg, facade_owners),
                             module_alias_paths(tree, pkg))

    for attr, sites in sorted(targets.items()):
        if attr not in facade_names:
            for site_path, line, exempt in sites:
                if not exempt:
                    violations.append(Violation(site_path, line, 0, "CORE_PATCH_TARGET_EXISTS",
                                                f"test patches main_logic.core.{attr} but the facade defines "
                                                f"no such name (typo? pass raising=False / create=True for "
                                                f"intentional absent-name guards)"))
            continue
        # The attr exists on the facade, so every patch of it is real (a
        # raising=False / create=True guard only waives the existence check,
        # not the routing requirement) — route ALL consumers.
        for path, (tree, snapshot, alias_paths) in module_info.items():
            if attr in snapshot:
                violations.append(Violation(path, snapshot[attr], 0, "CORE_PATCH_ROUTING",
                                            f"'{attr}' is a test patch target on the facade but its real symbol "
                                            f"is from-imported here at module level — the import snapshots the "
                                            f"pre-patch value and facade patches no longer reach this module; "
                                            f"read it as {FACADE_MODULE_ALIAS}.{attr} instead"))
            # Reads through some OTHER imported module — attribute chains
            # (``bus.dispatch_...``) and string getattr (``getattr(bus,
            # "dispatch_...")``) — dodge facade patches the same way. Reads
            # through main_logic.core itself ARE the facade contract and are
            # skipped inside the helper.
            for line, col, resolved in owner_module_reads(tree, alias_paths, facade_owners.get(attr)):
                violations.append(Violation(
                    path, line, col, "CORE_PATCH_ROUTING",
                    f"'{attr}' is a test patch target on the facade but is read here "
                    f"through module '{resolved}' — that read follows the owner module, "
                    f"not the facade; read it as {FACADE_MODULE_ALIAS}.{attr} instead"))
            for line, col in def_time_facade_reads(tree, alias_paths, attr):
                violations.append(Violation(
                    path, line, col, "CORE_PATCH_ROUTING",
                    f"'{attr}' is read from the facade inside a default/decorator/annotation/"
                    f"class attribute — that expression runs once at import and freezes the "
                    f"value, so facade patches no longer reach it; move the "
                    f"{FACADE_MODULE_ALIAS}.{attr} read into the method body"))
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
