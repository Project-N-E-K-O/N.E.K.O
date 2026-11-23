#!/usr/bin/env python3
"""Pick the tests/unit files a pull request can plausibly affect.

The full ``Unit pytest (Windows)`` job runs every unmarked file under
tests/unit and takes 30+ minutes, so pull requests run only the files this
script selects; the full suite still runs after every merge to main.

Static analysis only: the repository is read as text and parsed with ``ast``.
Nothing from the application is imported, so the script runs on a bare
ubuntu runner with the standard library and cannot be broken by the code it
is analysing.

Selection rules (the "trans" strategy validated offline on 150 merged PRs):

* An import graph is built over every Python file, test helpers (files
  under tests/ that are not test modules) and test modules included. Edges
  come from ``import`` / ``from ... import`` statements (relative ones too,
  and bare sibling imports inside tests/), literal
  ``import_module("pkg.mod")`` calls, attribute chains through an imported
  name (``import a as b`` then ``b.mod.attr``), and dotted string literals in
  test-tree files that name a module (``monkeypatch.setattr("pkg.mod.attr",
  ...)``).
* Package ``__init__`` files are handled by name: ``from pkg import name``
  follows the facade's re-export to the module that defines ``name``. A
  package ``__init__`` that is merely executed on the way (the parents of
  every imported module, or a facade whose name was traced elsewhere) is a
  weak dependency: it selects the importer when that ``__init__`` itself
  changed, but does not pass on changes to everything else it imports.
* Fixtures: a test that requests a conftest fixture (as a parameter or via
  ``usefixtures``, also through fixtures requesting fixtures) depends on
  what that fixture imports. A module imported at conftest level or inside
  an autouse fixture runs for every test in the conftest's directory, so a
  change to that module itself selects the whole directory (the full suite
  for the root conftests).
* A test file is selected when anything in its transitive closure changed,
  which also covers a changed test file itself and helpers that reach
  application code indirectly.
* A test file is selected when the base name of a changed file (six
  characters or more; generic names such as ``__init__.py`` together with
  their directory) appears as a whole file name in it, or in a test-tree
  module it imports - this catches tests that read static/, templates/ or
  workflow files instead of importing them.
* Files whose module-level ``pytestmark`` carries a shard marker
  (unit_fast / runtime / integration_serial / frontend_contract) belong to
  unit-test-shards.yml and are never selected here.
* Global files force the full suite; a changed sub-directory conftest.py
  (or a test-tree helper it imports) selects every test under its directory.
* docs/** and Markdown files never select anything.

The selected files are split into at most ``MAX_SHARDS`` shards, each holding
roughly ``SHARD_SHARE`` of the full suite's test count, balanced greedily by
test count.

Outputs (``--out-dir``): ``selection.json`` plus one pytest argument file per
shard (``shard-<n>.args``, one path per line). Pass a shard to pytest as
``@shard-<n>.args``: the list never goes through the command line, which on
Windows has a hard length limit.

Exit code is 0 whenever a selection was produced (including "nothing to
run"). Any failure to compute the diff falls back to the full suite rather
than to an empty selection.
"""

from __future__ import annotations

import argparse
import ast
import heapq
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SELF_PATH = "scripts/ci/select_related_tests.py"
TEST_ROOT = "tests/unit"
SHARD_MARKERS = frozenset({"unit_fast", "runtime", "integration_serial", "frontend_contract"})
GLOBAL_FILES = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "pytest.ini",
        "tests/conftest.py",
        "tests/unit/conftest.py",
        ".github/workflows/unit-tests.yml",
        SELF_PATH,
    }
)
# conftest files whose helpers are global; deeper ones scope to their directory.
ROOT_CONFTESTS = ("tests/conftest.py", "tests/unit/conftest.py")
SHARD_SHARE = 0.30
MAX_SHARDS = 4
MIN_LITERAL_LEN = 6
# Base names too generic to identify a file on their own; they are matched
# together with their parent directory ("memory/__init__.py").
GENERIC_BASENAMES = frozenset(
    {
        "__init__.py",
        "__main__.py",
        "conftest.py",
        "index.html",
        "index.js",
        "index.ts",
        "styles.css",
        "package.json",
        "package-lock.json",
        "config.json",
        "manifest.json",
        "plugin.toml",
    }
)
PRUNE_DIRS = frozenset({"node_modules", "__pycache__", "venv", "build", "dist", "site-packages"})
_IMPORT_MODULE_RE = re.compile(r"import_module\(\s*['\"]([\w.]+)['\"]")
_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_TOKEN_RE = re.compile(r"[\w.\-]+")


@dataclass
class TestFile:
    path: str
    weight: int
    shard_marked: bool


@dataclass
class RepoIndex:
    root: Path
    modules: dict[str, str]  # module name -> repo-relative posix path
    deps: dict[str, set[str]]  # module name -> modules it strongly depends on
    weak_deps: dict[str, set[str]]  # module name -> package __init__ files it merely executes
    test_tree_text: dict[str, str]  # repo-relative path -> text, for files under tests/
    tests: dict[str, TestFile]  # candidate test files under tests/unit
    path_to_module: dict[str, str] = field(default_factory=dict)
    test_tree_tokens: dict[str, frozenset[str]] | None = None  # built lazily
    # conftest path -> modules outside tests/ that run for every test in its scope
    conftest_autouse: dict[str, set[str]] = field(default_factory=dict)

    @property
    def total_weight(self) -> int:
        return sum(t.weight for t in self.tests.values() if not t.shard_marked)


@dataclass
class Selection:
    mode: str  # "none" | "partial" | "full"
    reason: str
    files: list[str]
    changed: list[str]


def module_name(rel: str) -> str | None:
    if not rel.endswith(".py"):
        return None
    name = rel[:-3].replace("/", ".")
    if name.endswith(".__init__"):
        name = name[: -len(".__init__")]
    return name


def is_test_module(rel: str) -> bool:
    base = PurePosixPath(rel).name
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in PRUNE_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                full = Path(dirpath) / name
                yield full.relative_to(root).as_posix()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _shard_marked(tree: ast.Module) -> bool:
    for node in tree.body:
        targets: list[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], node.value
        if value is None or not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        for sub in ast.walk(value):
            if (
                isinstance(sub, ast.Attribute)
                and sub.attr in SHARD_MARKERS
                and isinstance(sub.value, ast.Attribute)
                and sub.value.attr == "mark"
            ):
                return True
    return False


def _count_tests(tree: ast.Module) -> int:
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(
                1
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test")
            )
    return count


@dataclass
class _Imports:
    exact: set[str] = field(default_factory=set)  # import statements / import_module literals
    loose: set[str] = field(default_factory=set)  # attribute chains and dotted literals
    sibling: set[str] = field(default_factory=set)  # absolute names a test directory may shadow


def _relative_head(rel: str, level: int, module: str | None) -> str:
    # The directory is the package for both a plain module and an
    # __init__.py, so level 1 means "this directory".
    parts = rel.split("/")[:-1]
    drop = level - 1
    base = parts[: len(parts) - drop] if drop <= len(parts) else []
    return ".".join([*base, *([module] if module else [])])


def _chain(node: ast.Attribute) -> list[str] | None:
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return parts[::-1]


def _scan_imports(rel: str, tree: ast.Module | None, text: str) -> _Imports:
    out = _Imports()
    out.exact.update(_IMPORT_MODULE_RE.findall(text))
    if tree is None:
        return out
    aliases: dict[str, str] = {}
    chains: list[list[str]] = []
    in_test_tree = rel.startswith("tests/")
    # One walk: chains are resolved after it, because a function-local import
    # can come after the attribute access it explains.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _chain(node)
            if chain:
                chains.append(chain)
        elif in_test_tree and isinstance(node, ast.Constant):
            if isinstance(node.value, str) and _DOTTED_RE.match(node.value):
                out.loose.add(node.value)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.exact.add(alias.name)
                out.sibling.add(alias.name)
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root_name = alias.name.split(".")[0]
                    aliases.setdefault(root_name, root_name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                head = node.module or ""
            else:
                head = _relative_head(rel, node.level, node.module)
            if not head:
                continue
            for alias in node.names:
                if alias.name == "*":
                    out.exact.add(head)
                    continue
                full = f"{head}.{alias.name}"
                out.exact.add(full)
                aliases.setdefault(alias.asname or alias.name, full)
                if node.level == 0:
                    out.sibling.add(full)
    for chain in chains:
        if chain[0] in aliases:
            out.loose.add(".".join([aliases[chain[0]], *chain[1:]]))
    return out


def _longest_known(name: str, known: dict[str, str]) -> tuple[str, int] | None:
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known:
            return candidate, i
    return None


def _parents(mod: str, known: dict[str, str]) -> set[str]:
    parts = mod.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts)) if ".".join(parts[:i]) in known}


class _Resolver:
    """Map dotted names to (strong, weak) module dependencies.

    A strong dependency means the importer runs or uses the target's code, so
    whatever the target depends on is the importer's business too. A weak one
    is a package ``__init__`` that merely gets executed on the way to a
    submodule, or a facade whose re-exported name was traced to the module
    that defines it: the importer breaks if that ``__init__`` itself changes,
    but not when some unrelated module the facade also imports changes.
    """

    def __init__(self, known: dict[str, str], packages: set[str], exports: dict[str, dict[str, str]]):
        self.known = known
        self.packages = packages
        self.exports = exports

    def resolve(self, name: str, loose: bool) -> tuple[set[str], set[str]]:
        hit = _longest_known(name, self.known)
        if hit is None:
            return set(), set()
        mod, used = hit
        parts = name.split(".")
        weak = _parents(mod, self.known)
        for _ in range(8):  # follow re-exports through nested facades
            if mod not in self.packages or used >= len(parts):
                break
            target = self.exports.get(mod, {}).get(parts[used])
            if not target or target == mod:
                break
            weak |= {mod} | _parents(target, self.known)
            mod = target
            used += 1
        if loose and mod.count(".") == 0:
            return set(), set()  # "config.get" is far more often a variable than a package
        return {mod}, weak - {mod}


def _package_exports(rel: str, tree: ast.Module | None, known: dict[str, str]) -> dict[str, str]:
    exports: dict[str, str] = {}
    if tree is None:
        return exports
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            head = node.module or ""
        else:
            head = _relative_head(rel, node.level, node.module)
        if not head:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            hit = _longest_known(f"{head}.{alias.name}", known)
            if hit is not None:
                exports[alias.asname or alias.name] = hit[0]
    return exports


def build_index(root: Path, extra_modules: dict[str, str] | None = None) -> RepoIndex:
    root = root.resolve()
    modules: dict[str, str] = {}
    trees: dict[str, ast.Module | None] = {}
    texts: dict[str, str] = {}
    for rel in _iter_python_files(root):
        mod = module_name(rel)
        if not mod:
            continue
        text = _read(root / rel)
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError):
            tree = None
        modules.setdefault(mod, rel)
        trees[rel] = tree
        texts[rel] = text
    known = dict(modules)
    for mod, rel in (extra_modules or {}).items():
        known.setdefault(mod, rel)
    packages = {mod for mod, rel in modules.items() if rel.endswith("/__init__.py")}
    exports = {mod: _package_exports(modules[mod], trees.get(modules[mod]), known) for mod in packages}
    resolver = _Resolver(known, packages, exports)

    deps: dict[str, set[str]] = {}
    weak_deps: dict[str, set[str]] = {}
    for mod, rel in modules.items():
        scanned = _scan_imports(rel, trees.get(rel), texts.get(rel, ""))
        strong: set[str] = set()
        weak: set[str] = set()
        for names, loose in ((scanned.exact, False), (scanned.loose, True)):
            for name in names:
                s_hit, w_hit = resolver.resolve(name, loose)
                strong |= s_hit
                weak |= w_hit
        own_dir = rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else ""
        if rel.startswith("tests/") and own_dir:
            # pytest's default "prepend" import mode puts a rootless test
            # directory on sys.path, so `import host_fakes` can mean a sibling.
            for name in scanned.sibling:
                if _longest_known(name, known) is not None:
                    continue
                s_hit, w_hit = resolver.resolve(f"{own_dir}.{name}", False)
                s_hit = {m for m in s_hit if m.startswith(own_dir + ".")}
                if s_hit:
                    strong |= s_hit
                    weak |= w_hit
        strong.discard(mod)
        deps[mod] = strong
        weak_deps[mod] = weak - strong - {mod}

    # Fixtures reach code without an import in the test: a test that takes a
    # conftest fixture as a parameter depends on what that fixture imports.
    fixture_deps, conftest_autouse = _conftest_fixtures(modules, trees, resolver)
    for rel, tree in trees.items():
        if tree is None or not rel.startswith(TEST_ROOT + "/") or not is_test_module(rel):
            continue
        used = _used_fixture_names(tree)
        mod = module_name(rel)
        for conftest_rel, fixtures in fixture_deps.items():
            if rel.startswith(conftest_rel.rsplit("/", 1)[0] + "/"):
                for name in used & fixtures.keys():
                    deps[mod] |= fixtures[name] - {mod}

    test_tree_text = {rel: text for rel, text in texts.items() if rel.startswith("tests/")}
    tests: dict[str, TestFile] = {}
    for rel, tree in trees.items():
        if not rel.startswith(TEST_ROOT + "/") or not is_test_module(rel):
            continue
        weight = _count_tests(tree) if tree is not None else 1
        tests[rel] = TestFile(rel, max(weight, 1), _shard_marked(tree) if tree is not None else False)
    return RepoIndex(
        root=root,
        modules=modules,
        deps=deps,
        weak_deps=weak_deps,
        test_tree_text=test_tree_text,
        tests=tests,
        path_to_module={rel: mod for mod, rel in modules.items()},
        conftest_autouse=conftest_autouse,
    )


def _fixture_decorator(dec: ast.expr) -> tuple[bool, bool, str | None]:
    """(is a fixture, autouse, name override) for one decorator."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Attribute):
        name = target.attr
    elif isinstance(target, ast.Name):
        name = target.id
    else:
        return False, False, None
    if name != "fixture":
        return False, False, None
    autouse, alias = False, None
    if isinstance(dec, ast.Call):
        for kw in dec.keywords:
            if kw.arg == "autouse" and isinstance(kw.value, ast.Constant):
                autouse = bool(kw.value.value)
            elif kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                alias = kw.value.value
    return True, autouse, alias


def _resolve_nodes(resolver: _Resolver, rel: str, nodes: list[ast.stmt], imports_only: bool = False) -> set[str]:
    scanned = _scan_imports(rel, ast.Module(body=list(nodes), type_ignores=[]), "")
    out: set[str] = set()
    groups = ((scanned.exact, False),) if imports_only else ((scanned.exact, False), (scanned.loose, True))
    for names, loose in groups:
        for name in names:
            out |= resolver.resolve(name, loose)[0]
    return out


def _conftest_fixtures(
    modules: dict[str, str], trees: dict[str, ast.Module | None], resolver: _Resolver
) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    """Per conftest: fixture name -> modules it imports, and the modules every test in scope runs.

    The second map holds what the conftest imports at module level or inside
    an autouse fixture, outside tests/ (test-tree helpers are already scoped
    by the conftest rule). Only those modules themselves count, not their
    imports: an autouse fixture touches exactly what it imports.
    """
    fixture_deps: dict[str, dict[str, set[str]]] = {}
    autouse: dict[str, set[str]] = {}
    for rel in modules.values():
        tree = trees.get(rel)
        if tree is None or not rel.startswith("tests/") or PurePosixPath(rel).name != "conftest.py":
            continue
        fixtures: dict[str, set[str]] = {}
        params: dict[str, set[str]] = {}
        always: list[ast.stmt] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    always.append(node)
                continue
            for dec in node.decorator_list:
                is_fixture, is_autouse, alias = _fixture_decorator(dec)
                if is_fixture:
                    name = alias or node.name
                    fixtures[name] = _resolve_nodes(resolver, rel, [node])
                    params[name] = {a.arg for a in node.args.args + node.args.kwonlyargs}
                    if is_autouse:
                        always.append(node)
                    break
        for _ in range(len(fixtures)):  # fixtures requesting other fixtures
            changed = False
            for name, requested in params.items():
                for other in requested & fixtures.keys():
                    if other != name and not fixtures[other] <= fixtures[name]:
                        fixtures[name] |= fixtures[other]
                        changed = True
            if not changed:
                break
        fixture_deps[rel] = fixtures
        # Import statements only: `sys.modules.get("pkg.mod")` in a reset
        # fixture touches a module some test already loaded, it loads nothing.
        autouse[rel] = {
            m
            for m in _resolve_nodes(resolver, rel, always, imports_only=True)
            if not modules.get(m, "tests/").startswith("tests/")
        }
    return fixture_deps, autouse


def _used_fixture_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names |= {a.arg for a in node.args.args + node.args.kwonlyargs}
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == "usefixtures":
                names |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return names


def _reverse_closure(index: RepoIndex, seeds: set[str]) -> set[str]:
    """Modules affected by changes to ``seeds``.

    Weak edges only fire on a module that changed itself; strong edges carry
    the effect on transitively.
    """
    reverse: dict[str, set[str]] = {}
    for mod, targets in index.deps.items():
        for target in targets:
            reverse.setdefault(target, set()).add(mod)
    start = set(seeds)
    for mod, targets in index.weak_deps.items():
        if targets & seeds:
            start.add(mod)
    seen = set(start)
    stack = list(start)
    while stack:
        for importer in reverse.get(stack.pop(), ()):
            if importer not in seen:
                seen.add(importer)
                stack.append(importer)
    return seen


def _test_tree_closure(index: RepoIndex, rel: str) -> set[str]:
    """Paths of test-tree modules reachable from ``rel`` through test-tree modules only."""
    start = index.path_to_module.get(rel)
    if start is None:
        return set()
    seen = {start}
    stack = [start]
    while stack:
        for dep in index.deps.get(stack.pop(), ()):
            dep_rel = index.modules.get(dep, "")
            if dep not in seen and dep_rel.startswith("tests/"):
                seen.add(dep)
                stack.append(dep)
    return {index.modules[m] for m in seen if m in index.modules}


def _literal_for(rel: str) -> tuple[str, ...] | None:
    """File-name tokens that must all appear in a test for it to count as naming ``rel``."""
    path = PurePosixPath(rel)
    if len(path.name) < MIN_LITERAL_LEN:
        return None
    if path.name in GENERIC_BASENAMES and path.parent.name:
        # "templates/index.html" is often spelled ROOT / "templates" / "index.html",
        # so the directory and the name are matched as separate tokens.
        return (path.parent.name, path.name)
    return (path.name,)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t.strip(".-") for t in _TOKEN_RE.findall(text))


def _mentioning(index: RepoIndex, literal: tuple[str, ...]) -> set[str]:
    """Test-tree files naming every token of ``literal`` as a whole word (not inside a longer name)."""
    if index.test_tree_tokens is None:
        index.test_tree_tokens = {p: _tokens(text) for p, text in index.test_tree_text.items()}
    wanted = set(literal)
    return {p for p, tokens in index.test_tree_tokens.items() if wanted <= tokens}


def _ignored(rel: str) -> bool:
    return rel.startswith("docs/") or rel.lower().endswith(".md")


def select(index: RepoIndex, changed: list[str]) -> Selection:
    changed = sorted(set(changed))
    candidates = {p for p, t in index.tests.items() if not t.shard_marked}

    for rel in changed:
        if rel in GLOBAL_FILES:
            return Selection("full", f"global file changed: {rel}", sorted(candidates), changed)

    # Test-tree helpers loaded by conftest files behave like the conftest itself.
    conftest_scope: dict[str, str] = {}  # helper path -> directory it scopes ("" = everything)
    for rel in index.test_tree_text:
        if PurePosixPath(rel).name != "conftest.py":
            continue
        scope = "" if rel in ROOT_CONFTESTS else rel.rsplit("/", 1)[0] + "/"
        for helper in _test_tree_closure(index, rel):
            previous = conftest_scope.get(helper)
            if previous is None or len(scope) < len(previous):
                conftest_scope[helper] = scope
    for rel in changed:
        if conftest_scope.get(rel) == "" or rel in ROOT_CONFTESTS:
            return Selection("full", f"file loaded by a root conftest changed: {rel}", sorted(candidates), changed)

    chosen: set[str] = set()
    changed_modules = {m for m in (module_name(rel) for rel in changed) if m}
    for conftest_rel, always in sorted(index.conftest_autouse.items()):
        hit = sorted(always & changed_modules)
        if not hit:
            continue
        if conftest_rel in ROOT_CONFTESTS:
            return Selection(
                "full", f"{hit[0]} runs in a fixture of {conftest_rel} for every test", sorted(candidates), changed
            )
        scope = conftest_rel.rsplit("/", 1)[0] + "/"
        chosen |= {p for p in candidates if p.startswith(scope)}
    seeds: set[str] = set()
    literal_seeds: set[str] = set()
    for rel in changed:
        if _ignored(rel):
            continue
        scope = conftest_scope.get(rel)
        if PurePosixPath(rel).name == "conftest.py" and rel.startswith("tests/"):
            scope = rel.rsplit("/", 1)[0] + "/"
        if scope:
            chosen |= {p for p in candidates if p.startswith(scope)}
        mod = module_name(rel)
        if mod is not None:
            seeds.add(mod)
        literal = _literal_for(rel)
        if literal:
            literal_seeds |= _mentioning(index, literal)

    affected = _reverse_closure(index, seeds | {index.path_to_module[p] for p in literal_seeds if p in index.path_to_module})
    chosen |= {index.modules[m] for m in affected if index.modules.get(m) in candidates}
    if not chosen:
        return Selection("none", "no unit test depends on the changed files", [], changed)
    return Selection("partial", f"{len(chosen)} test file(s) depend on the changed files", sorted(chosen), changed)


def plan_shards(
    files: list[str],
    weights: dict[str, int],
    total_weight: int,
    share: float = SHARD_SHARE,
    max_shards: int = MAX_SHARDS,
) -> list[list[str]]:
    if not files:
        return []
    selected_weight = sum(weights.get(f, 1) for f in files)
    capacity = max(1.0, share * max(total_weight, 1))
    count = max(1, min(max_shards, math.ceil(selected_weight / capacity), len(files)))
    heap: list[tuple[int, int]] = [(0, i) for i in range(count)]
    shards: list[list[str]] = [[] for _ in range(count)]
    for path in sorted(files, key=lambda f: (-weights.get(f, 1), f)):
        load, i = heapq.heappop(heap)
        shards[i].append(path)
        heapq.heappush(heap, (load + weights.get(path, 1), i))
    return [sorted(s) for s in shards]


def changed_files_from_git(root: Path, base: str, head: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p]


def write_outputs(
    index: RepoIndex,
    selection: Selection,
    shards: list[list[str]],
    out_dir: Path,
    github_output: Path | None,
    summary: Path | None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = {p: t.weight for p, t in index.tests.items()}
    total = index.total_weight
    shard_info = []
    for i, files in enumerate(shards, start=1):
        (out_dir / f"shard-{i}.args").write_bytes(("\n".join(files) + "\n").encode("utf-8"))
        shard_info.append({"index": i, "files": len(files), "tests": sum(weights.get(f, 1) for f in files)})
    selected_tests = sum(weights.get(f, 1) for f in selection.files)
    report = {
        "mode": selection.mode,
        "reason": selection.reason,
        "changed_files": selection.changed,
        "selected_files": selection.files,
        "selected_tests": selected_tests,
        "total_tests": total,
        "selected_share": round(selected_tests / total, 4) if total else 0.0,
        "shards": shard_info,
    }
    (out_dir / "selection.json").write_bytes(json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8"))
    if github_output is not None:
        lines = [
            f"mode={selection.mode}",
            f"count={len(selection.files)}",
            f"shard_count={len(shards)}",
            f"matrix={json.dumps([s['index'] for s in shard_info])}",
        ]
        with github_output.open("ab") as fh:
            fh.write(("\n".join(lines) + "\n").encode("utf-8"))
    if summary is not None:
        md = [
            "### Related unit tests",
            "",
            f"- mode: `{selection.mode}` ({selection.reason})",
            f"- changed files: {len(selection.changed)}",
            f"- selected: {len(selection.files)} file(s), {selected_tests} of {total} tests "
            f"({report['selected_share']:.1%})",
            f"- shards: {len(shards)} " + ", ".join(f"#{s['index']}={s['tests']}" for s in shard_info),
        ]
        if selection.files and len(selection.files) <= 200:
            md += ["", "<details><summary>selected files</summary>", ""]
            md += [f"- `{f}`" for f in selection.files]
            md += ["", "</details>"]
        with summary.open("ab") as fh:
            fh.write(("\n".join(md) + "\n").encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--base", help="base revision; the diff is taken from merge-base(base, head)")
    parser.add_argument("--head", default="HEAD", help="head revision")
    parser.add_argument("--changed-files", help="read changed paths from this file (one per line) instead of git")
    parser.add_argument("--out-dir", default="related-tests", help="where selection.json and shard-*.args go")
    parser.add_argument("--github-output", help="append step outputs to this file ($GITHUB_OUTPUT)")
    parser.add_argument("--summary", help="append a Markdown summary to this file ($GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    changed: list[str] | None
    fallback_reason = ""
    if args.changed_files:
        raw = Path(args.changed_files).read_bytes().decode("utf-8")
        changed = [line.strip().replace("\\", "/") for line in raw.splitlines() if line.strip()]
    elif args.base:
        try:
            changed = changed_files_from_git(root, args.base, args.head)
        except (OSError, subprocess.CalledProcessError) as exc:
            changed = None
            fallback_reason = f"could not diff {args.base}...{args.head}: {exc}"
    else:
        parser.error("either --base or --changed-files is required")

    extra = {}
    for rel in changed or []:
        mod = module_name(rel)
        if mod:
            extra[mod] = rel  # deleted modules still resolve as import targets
    index = build_index(root, extra_modules=extra)
    if changed is None:
        candidates = sorted(p for p, t in index.tests.items() if not t.shard_marked)
        selection = Selection("full", fallback_reason, candidates, [])
    else:
        selection = select(index, changed)
    weights = {p: t.weight for p, t in index.tests.items()}
    shards = plan_shards(selection.files, weights, index.total_weight)
    report = write_outputs(
        index,
        selection,
        shards,
        Path(args.out_dir),
        Path(args.github_output) if args.github_output else None,
        Path(args.summary) if args.summary else None,
    )
    print(
        f"mode={report['mode']} files={len(report['selected_files'])} "
        f"tests={report['selected_tests']}/{report['total_tests']} ({report['selected_share']:.1%}) "
        f"shards={[s['tests'] for s in report['shards']]} reason={report['reason']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
