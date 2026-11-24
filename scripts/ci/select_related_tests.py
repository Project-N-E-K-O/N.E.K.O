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
  ``usefixtures``, also through fixtures requesting fixtures, across the
  conftests above it, a nearer one overriding a farther one) depends on what
  that fixture imports. A module imported at conftest level or by an autouse
  fixture (or a fixture it requests) runs for every test in the conftest's
  directory, so a change to it or to anything it imports selects the whole
  directory (the full suite for the root conftests).
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
* Markdown files (docs/ or elsewhere) are not Python, so they only select
  the tests that read them by name; Python files under docs/ are modules
  like any other.
* A data file under tests/ also matches through its nearest specific
  directory name, for tests that glob a directory of samples.
* Tests that scan the whole repository (layering, locale and template
  sweeps) are only selected when what they name changes; the full run
  after merge covers the rest, and the layering gate also runs in
  analyze.yml on every PR.

The selected files are split into at most ``MAX_SHARDS`` shards, each holding
roughly ``SHARD_SHARE`` of the full suite's cost, balanced greedily. A file's
cost is its measured run time from a JUnit report of an earlier full run
(``--durations``, xunit1 so every case carries its file); files the report
does not know are estimated from their test count at the report's average
seconds per test. Without a report the cost is the test count: on the
Windows runners that put one shard at 13k tests and 18 minutes next to one
at 18k tests and 9 minutes, so the report matters.

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
from collections import Counter
from fnmatch import fnmatch
import xml.etree.ElementTree as ET
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
        # tests/ has no __init__.py, so pytest imports every test file as a
        # module of the `unit` package: this runs before each of them.
        "tests/unit/__init__.py",
        ".github/workflows/unit-tests.yml",
        SELF_PATH,
    }
)
# conftest files whose helpers are global; deeper ones scope to their directory.
ROOT_CONFTESTS = ("tests/conftest.py", "tests/unit/conftest.py")
SHARD_SHARE = 0.30
MAX_SHARDS = 4
MIN_LITERAL_LEN = 6
VENDOR_ROOT = "deps"
# Directories holding test samples; a file below one may be globbed by directory.
DATA_DIRS = frozenset(
    {"fixtures", "fixture", "data", "testdata", "samples", "golden", "snapshots", "assets", "resources", "inputs", "outputs", "files"}
)
# Directory names too generic to stand for the files in them.
GENERIC_DIRS = DATA_DIRS | {"static", "unit", "tests"}
COMMON_NAME_COUNT = 2  # a Python file name shared by this many files needs its directories too
MIN_FILE_SECONDS = 0.01  # a fully skipped file still costs its import
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
        "README.md",
        "CHANGELOG.md",
        "index.md",
    }
)
PRUNE_DIRS = frozenset({"node_modules", "__pycache__", "venv", "build", "dist", "site-packages"})
_IMPORT_MODULE_RE = re.compile(r"import_module\(\s*['\"]([\w.]+)['\"]")
_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_TOKEN_RE = re.compile(r"[\w.\-]+")
_GLOB_RE = re.compile(r"""['"]([^'"\n]*\*[^'"\n]*)['"]""")
SCRIPT_SUFFIXES = (".js", ".mjs", ".cjs")


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
    # JavaScript test scripts under tests/: their file-name tokens, and the
    # Python wrappers that run them
    script_tokens: dict[str, frozenset[str]] = field(default_factory=dict)
    script_runners: dict[str, set[str]] = field(default_factory=dict)
    # conftest path -> modules outside tests/ that run for every test in its scope
    conftest_autouse: dict[str, set[str]] = field(default_factory=dict)
    # module -> path, including modules the change deletes
    known_paths: dict[str, str] = field(default_factory=dict)

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


def _walk(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Python files, vendored packages, and JavaScript test scripts under tests/.

    A vendored package (under deps/, with its own pyproject.toml or setup.py)
    such as deps/rapidocr_pillow is put on sys.path by the code that uses it,
    so its modules are imported by names relative to it.
    """
    files: list[str] = []
    dist_roots: list[str] = []
    scripts: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in PRUNE_DIRS and not d.endswith(".egg-info")
        )
        here = Path(dirpath).relative_to(root).as_posix()
        if here.startswith(VENDOR_ROOT + "/") and ("pyproject.toml" in filenames or "setup.py" in filenames):
            dist_roots.append(here)
        for name in sorted(filenames):
            if name.endswith(".py"):
                files.append((Path(dirpath) / name).relative_to(root).as_posix())
            elif name.endswith(SCRIPT_SUFFIXES) and (here == "tests" or here.startswith("tests/")):
                scripts.append((Path(dirpath) / name).relative_to(root).as_posix())
    return files, dist_roots, scripts


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
    inner: set[int] = set()  # attribute nodes that are a prefix of a longer chain
    in_test_tree = rel.startswith("tests/")
    # One walk: chains are resolved after it, because a function-local import
    # can come after the attribute access it explains.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Attribute):
                inner.add(id(node.value))
            # ast.walk is breadth-first, so an outer chain is seen before its prefixes.
            if id(node) not in inner:
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

    def __init__(
        self,
        known: dict[str, str],
        packages: set[str],
        exports: dict[str, dict[str, str]],
        aliases: dict[str, str] | None = None,
    ):
        self.known = known
        self.aliases = aliases or {}
        self.packages = packages
        self.exports = exports

    def resolve(self, name: str, loose: bool) -> tuple[set[str], set[str]]:
        hit = _longest_known(name, self.known)
        if hit is None:
            return set(), set()
        mod, used = hit
        mod = self.aliases.get(mod, mod)
        parts = name.split(".")
        weak = _parents(mod, self.known)
        for _ in range(8):  # follow re-exports through nested facades
            if mod not in self.packages or used >= len(parts):
                break
            target = self.exports.get(mod, {}).get(parts[used])
            target = self.aliases.get(target, target)  # a facade may re-export by import name
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


def build_index(
    root: Path, extra_modules: dict[str, str] | None = None, extra_scripts: list[str] | None = None
) -> RepoIndex:
    root = root.resolve()
    modules: dict[str, str] = {}
    trees: dict[str, ast.Module | None] = {}
    texts: dict[str, str] = {}
    files, dist_roots, scripts = _walk(root)
    for rel in files:
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
    # Modules of a separately packaged directory also answer to their import
    # name ("rapidocr_onnxruntime._pillow_cv" for
    # deps/rapidocr_pillow/rapidocr_onnxruntime/_pillow_cv.py); real modules win.
    # Only vendored packages count (deps/): other packaged directories, such as
    # a plugin or a CLI test fixture, are imported by their full path.
    aliases: dict[str, str] = {}
    collisions: set[str] = set()
    top_level = {m.split(".")[0] for m in known}
    for dist_root in dist_roots:  # _walk only reports packages under deps/
        prefix = dist_root + "/"
        for mod, rel in list(known.items()):
            if not rel.startswith(prefix) or rel == prefix + "__init__.py":
                continue
            alias = module_name(rel[len(prefix):])
            if not alias or alias.split(".")[0] in top_level:
                continue
            if alias in aliases and aliases[alias] != mod:
                collisions.add(alias)
            aliases[alias] = mod
    for alias in collisions:
        del aliases[alias]
    resolvable = {**{a: known[m] for a, m in aliases.items()}, **known}
    packages = {mod for mod, rel in modules.items() if rel.endswith("/__init__.py")}
    exports = {mod: _package_exports(modules[mod], trees.get(modules[mod]), resolvable) for mod in packages}
    resolver = _Resolver(resolvable, packages, exports, aliases)

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
    conftests = _conftest_fixtures(modules, trees, resolver)
    for rel, tree in trees.items():
        if tree is None or not rel.startswith(TEST_ROOT + "/") or not is_test_module(rel):
            continue
        mod = module_name(rel)
        visible = _visible_fixtures(conftests, rel)
        used = _used_fixture_names(tree)
        deps[mod] |= _expand(visible, used, "deps") - {mod}
        weak_deps[mod] |= _expand(visible, used, "weak") - deps[mod] - {mod}
    conftest_autouse = _autouse_closures(conftests, known, deps, weak_deps)

    test_tree_text = {rel: text for rel, text in texts.items() if rel.startswith("tests/")}
    script_tokens = {rel: _tokens(_read(root / rel)) for rel in scripts}
    # A script the change deletes still has the wrappers that globbed for it.
    for rel in extra_scripts or ():
        script_tokens.setdefault(rel, frozenset())
    script_runners = _script_runners(test_tree_text, script_tokens)
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
        known_paths=known,
        script_tokens=script_tokens,
        script_runners=script_runners,
    )


def _script_runners(test_tree_text: dict[str, str], script_tokens: dict[str, frozenset[str]]) -> dict[str, set[str]]:
    """Which Python test-tree files run each JavaScript test script.

    A wrapper either names the script, or globs for it
    (``(ROOT / 'tests/frontend').glob('test_watch_together*.mjs')``): a glob
    pattern with a specific fixed part that matches the script's file name
    counts when the wrapper also names the script's directory.
    """
    runners: dict[str, set[str]] = {rel: set() for rel in script_tokens}
    if not script_tokens:
        return runners
    names = {rel: PurePosixPath(rel).name for rel in script_tokens}
    for py, text in test_tree_text.items():
        tokens = _tokens(text)
        # Only globs with a specific fixed part ("test_watch_together*.mjs"); "*.mjs"
        # next to the word "frontend" would claim every script in the directory.
        globs = [
            g
            for g in (g.rsplit("/", 1)[-1] for g in _GLOB_RE.findall(text))
            if len(re.sub(r"[*?\[\]]", "", g).rsplit(".", 1)[0]) >= MIN_LITERAL_LEN
        ]
        for rel, name in names.items():
            if name in tokens:
                runners[rel].add(py)
            elif globs and PurePosixPath(rel).parent.name in tokens and any(fnmatch(name, g) for g in globs):
                runners[rel].add(py)
    return runners


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


def _resolve_nodes(
    resolver: _Resolver, rel: str, nodes: list[ast.stmt], imports_only: bool = False
) -> tuple[set[str], set[str]]:
    """(strong, weak) dependencies of some statements, as for a whole module."""
    scanned = _scan_imports(rel, ast.Module(body=list(nodes), type_ignores=[]), "")
    strong: set[str] = set()
    weak: set[str] = set()
    groups = ((scanned.exact, False),) if imports_only else ((scanned.exact, False), (scanned.loose, True))
    for names, loose in groups:
        for name in names:
            s_hit, w_hit = resolver.resolve(name, loose)
            strong |= s_hit
            weak |= w_hit
    return strong, weak - strong


@dataclass
class _Fixture:
    deps: set[str]  # modules it imports or names (dotted literals, attribute chains)
    weak: set[str]  # package __init__ files those imports merely execute
    imports: set[str]  # modules it imports (import statements only)
    import_weak: set[str]  # package __init__ files those import statements execute
    params: set[str]  # fixtures it requests
    autouse: bool


@dataclass
class _Conftest:
    directory: str  # "tests/unit/" for tests/unit/conftest.py
    fixtures: dict[str, _Fixture]
    imports: set[str]  # module-level imports
    import_weak: set[str]


def _conftest_fixtures(
    modules: dict[str, str], trees: dict[str, ast.Module | None], resolver: _Resolver
) -> dict[str, _Conftest]:
    """The fixtures each conftest under tests/ defines, keyed by conftest path."""
    out: dict[str, _Conftest] = {}
    for rel in modules.values():
        tree = trees.get(rel)
        if tree is None or not rel.startswith("tests/") or PurePosixPath(rel).name != "conftest.py":
            continue
        fixtures: dict[str, _Fixture] = {}
        top_level: list[ast.stmt] = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    top_level.append(node)
                continue
            for dec in node.decorator_list:
                is_fixture, is_autouse, alias = _fixture_decorator(dec)
                if is_fixture:
                    deps, weak = _resolve_nodes(resolver, rel, [node])
                    # Import statements only: `sys.modules.get("pkg.mod")` in a
                    # reset fixture touches a module some test already loaded.
                    imports, import_weak = _resolve_nodes(resolver, rel, [node], imports_only=True)
                    fixtures[alias or node.name] = _Fixture(
                        deps=deps,
                        weak=weak,
                        imports=imports,
                        import_weak=import_weak,
                        params={a.arg for a in node.args.args + node.args.kwonlyargs},
                        autouse=is_autouse,
                    )
                    break
        imports, import_weak = _resolve_nodes(resolver, rel, top_level, imports_only=True)
        out[rel] = _Conftest(
            directory=rel.rsplit("/", 1)[0] + "/", fixtures=fixtures, imports=imports, import_weak=import_weak
        )
    return out


def _visible_fixtures(conftests: dict[str, _Conftest], rel: str) -> dict[str, list[_Fixture]]:
    """Fixtures a file under ``rel``'s directory can request, farthest definition first.

    The last definition of a name is the one a request gets; a fixture that
    requests its own name gets the definition before it (pytest's override
    pattern ``def service(service)``).
    """
    visible: dict[str, list[_Fixture]] = {}
    for conftest in sorted(conftests.values(), key=lambda c: len(c.directory)):
        if rel.startswith(conftest.directory):
            for name, fixture in conftest.fixtures.items():
                visible.setdefault(name, []).append(fixture)
    return visible


def _expand(visible: dict[str, list[_Fixture]], names: set[str], *attrs: str) -> set[str]:
    """Modules reached by requesting ``names``, following fixtures that request fixtures."""
    out: set[str] = set()
    seen: set[tuple[str, int]] = set()
    stack = [(n, len(visible[n]) - 1) for n in names if n in visible]
    while stack:
        name, level = stack.pop()
        if (name, level) in seen:
            continue
        seen.add((name, level))
        fixture = visible[name][level]
        for attr in attrs:
            out |= getattr(fixture, attr)
        for requested in fixture.params:
            if requested == name:
                if level > 0:
                    stack.append((name, level - 1))
            elif requested in visible:
                stack.append((requested, len(visible[requested]) - 1))
    return out


def _autouse_closures(
    conftests: dict[str, _Conftest],
    known: dict[str, str],
    deps: dict[str, set[str]],
    weak_deps: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Per conftest: application modules that run for every test in its directory.

    That is what the conftest imports at module level, plus what every autouse
    fixture visible there (its own and inherited ones, resolved as a test in
    that directory would resolve them) imports, through the fixtures those
    request, and everything those modules import in turn. Test-tree helpers
    are left out here: the conftest rule already scopes them, and the
    application code a helper merely imports runs once at import time, which
    the tests that use that code catch. Following helpers as well put 34 of
    the last 150 PRs on the full suite instead of 18.
    """

    def outside_tests(mod: str) -> bool:
        # `known` still has the path of a module the change deletes.
        return not known.get(mod, "tests/").startswith("tests/")

    out: dict[str, set[str]] = {}
    for rel, conftest in conftests.items():
        visible = _visible_fixtures(conftests, rel)
        autouse = {name for name, defs in visible.items() if any(d.autouse for d in defs)}
        roots = {m for m in conftest.imports | _expand(visible, autouse, "imports") if outside_tests(m)}
        seen = set(roots)
        stack = list(roots)
        while stack:
            for dep in deps.get(stack.pop(), ()):
                if dep not in seen and outside_tests(dep):
                    seen.add(dep)
                    stack.append(dep)
        # Package __init__ files on the way to any of those imports run too,
        # without making everything else they import count.
        executed = conftest.import_weak | _expand(visible, autouse, "import_weak")
        for mod in seen:
            executed |= weak_deps.get(mod, set())
        seen |= {m for m in executed if outside_tests(m)}
        out[rel] = seen
    return out


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
            # known_paths: a helper the change deletes still has to count.
            dep_rel = index.known_paths.get(dep) or index.modules.get(dep, "")
            if dep not in seen and dep_rel.startswith("tests/"):
                seen.add(dep)
                stack.append(dep)
    paths = {**index.modules, **index.known_paths}
    return {paths[m] for m in seen if m in paths}


def _literal_for(rel: str, common: frozenset[str] = frozenset()) -> tuple[str, ...] | None:
    """How a test names ``rel``: its file name, or its last directories and file name together.

    ``common`` holds file names several Python files share (``core.py``);
    like the generic names, those only count together with up to two of
    their directories, so ``plugin/tests/conftest.py`` is not mistaken for
    ``tests/conftest.py``.
    """
    path = PurePosixPath(rel)
    if len(path.name) < MIN_LITERAL_LEN:
        return None
    if path.name in GENERIC_BASENAMES or path.name in common:
        if not path.parent.name:
            # A bare "README.md" at the root names nothing in particular, but a
            # root launcher.py is what a test reading "launcher.py" means.
            return None if path.name in GENERIC_BASENAMES else (path.name,)
        # "templates/index.html" is often spelled ROOT / "templates" / "index.html",
        # so the parts are matched as separate tokens.
        return (*path.parent.parts[-2:], path.name)
    return (path.name,)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t.strip(".-") for t in _TOKEN_RE.findall(text))


def _mentioning(index: RepoIndex, literal: tuple[str, ...]) -> set[str]:
    """Test-tree files naming ``literal``: a whole file name, or directories and a file name.

    For a Python file the parts have to be adjacent ("config/__init__.py",
    ``"config" / "__init__.py"``, ``"config", "__init__.py"``): "config" and
    "__init__.py" anywhere in a big conftest name nothing. A data file is
    often read through a parameter (``["index.html"]`` then
    ``ROOT / "templates" / name``), so for it both words anywhere are enough,
    unless the directory is itself a generic word ("tests/README.md": "tests"
    is everywhere).
    """
    if index.test_tree_tokens is None:
        index.test_tree_tokens = {p: _tokens(text) for p, text in index.test_tree_text.items()}
    wanted = set(literal)
    found = {p for p, tokens in index.test_tree_tokens.items() if wanted <= tokens}
    generic_dir = any(part.lower() in GENERIC_DIRS or part == "docs" for part in literal[:-1])
    if len(literal) == 1 or (not literal[-1].endswith(".py") and not generic_dir):
        return found
    # Quotes and a closing parenthesis may sit between the parts: (Path("pkg") / "core.py"),
    # and a Windows path may spell the separator as an escaped backslash.
    separator = r"['\"]?\)?\s*(?:[/,]|\\{1,2})\s*['\"]?"
    pattern = re.compile(r"(?<![\w.-])" + separator.join(re.escape(part) for part in literal) + r"(?![\w-])")
    return {p for p in found if pattern.search(index.test_tree_text[p])}


def _data_dir_literal(rel: str) -> tuple[str, ...] | None:
    """For a sample file under a data directory in tests/, the nearest specific directory name.

    Tests often glob a directory of samples (``fixtures/scoped_client_wire/*.json``)
    without naming any file. Only files below a data directory (``fixtures``,
    ``data``, ...) qualify: ``tests/frontend/x.test.cjs`` is a test, not a
    sample, and "frontend" names half the test tree.
    """
    if not rel.startswith("tests/") or rel.endswith(".py"):
        return None
    parts = PurePosixPath(rel).parent.parts
    if not any(part.lower() in DATA_DIRS for part in parts):
        return None
    for part in reversed(parts):
        if part.lower() in GENERIC_DIRS:
            return None
        if len(part) >= MIN_LITERAL_LEN:
            return (part,)
    return None


def select(index: RepoIndex, changed: list[str]) -> Selection:
    changed = sorted(set(changed))
    candidates = {p for p, t in index.tests.items() if not t.shard_marked}

    for rel in changed:
        if rel in GLOBAL_FILES:
            return Selection("full", f"global file changed: {rel}", sorted(candidates), changed)

    # Test-tree helpers loaded by conftest files behave like the conftest itself.
    # A helper shared by several conftests scopes every one of their directories.
    conftest_scope: dict[str, set[str]] = {}  # path -> directories it scopes ("" = everything)
    for rel in index.test_tree_text:
        if PurePosixPath(rel).name != "conftest.py":
            continue
        scope = "" if rel in ROOT_CONFTESTS else rel.rsplit("/", 1)[0] + "/"
        for helper in _test_tree_closure(index, rel):
            conftest_scope.setdefault(helper, set()).add(scope)
    for rel in changed:
        if "" in conftest_scope.get(rel, ()) or rel in ROOT_CONFTESTS:
            return Selection("full", f"file loaded by a root conftest changed: {rel}", sorted(candidates), changed)

    chosen: set[str] = set()
    changed_modules = {m for m in (module_name(rel) for rel in changed) if m}
    for conftest_rel, always in sorted(index.conftest_autouse.items()):
        hit = sorted(always & changed_modules)
        if not hit:
            continue
        if conftest_rel in ROOT_CONFTESTS:
            return Selection(
                "full",
                f"{hit[0]} runs (directly or through its importers) in a fixture of {conftest_rel} for every test",
                sorted(candidates),
                changed,
            )
        scope = conftest_rel.rsplit("/", 1)[0] + "/"
        chosen |= {p for p in candidates if p.startswith(scope)}
    seeds: set[str] = set()
    literal_seeds: set[str] = set()
    scoped_literal_seeds: set[str] = set()
    name_counts = Counter(PurePosixPath(p).name for p in index.modules.values())
    common_names = frozenset(n for n, count in name_counts.items() if count >= COMMON_NAME_COUNT)
    for rel in changed:
        scopes = set(conftest_scope.get(rel, ()))
        if PurePosixPath(rel).name in ("conftest.py", "__init__.py") and rel.startswith(TEST_ROOT + "/"):
            # A test package's __init__ runs before every test module in it, like a conftest.
            scopes.add(rel.rsplit("/", 1)[0] + "/")
        for scope in scopes - {""}:
            chosen |= {p for p in candidates if p.startswith(scope)}
        mod = module_name(rel)
        if mod is not None:
            seeds.add(mod)
        dir_literal = _data_dir_literal(rel)
        if dir_literal:
            # Tests (or a conftest fixture) that glob the directory. Limited to
            # data directories, so it scopes like a file name would.
            dir_hits = _mentioning(index, dir_literal)
            literal_seeds |= dir_hits
            scoped_literal_seeds |= dir_hits
        literal = _literal_for(rel, common_names)
        if literal:
            hits = _mentioning(index, literal)
            literal_seeds |= hits
            if not (is_test_module(rel) and rel.startswith(TEST_ROOT + "/")):
                # A test file only matters to itself: a helper listing it by name
                # (an exclusion list, say) does not make its conftest's tests depend on it.
                scoped_literal_seeds |= hits

    # A wrapper may be a conftest or a helper it loads (an autouse fixture running a
    # script): then it scopes like any file such a conftest names.
    wrappers = _script_wrappers(index, changed, common_names)
    literal_seeds |= wrappers
    scoped_literal_seeds |= wrappers

    # A file a conftest (or a helper it loads) reads by name matters to every
    # test in that conftest's directory, not only to the tests importing it.
    for hit in sorted(scoped_literal_seeds):
        scopes = conftest_scope.get(hit, set())  # a conftest is part of its own closure
        if "" in scopes:
            return Selection("full", f"a root conftest (via {hit}) reads a changed file", sorted(candidates), changed)
        for scope in scopes:
            chosen |= {p for p in candidates if p.startswith(scope)}

    affected = _reverse_closure(index, seeds | {index.path_to_module[p] for p in literal_seeds if p in index.path_to_module})
    chosen |= {index.modules[m] for m in affected if index.modules.get(m) in candidates}
    if not chosen:
        return Selection("none", "no unit test depends on the changed files", [], changed)
    return Selection("partial", f"{len(chosen)} test file(s) depend on the changed files", sorted(chosen), changed)


def load_durations(path: Path) -> dict[str, float]:
    """Seconds per test file from JUnit XML reports (a file or a directory of them)."""
    reports = [path] if path.is_file() else sorted(path.rglob("*.xml")) if path.is_dir() else []
    seconds: dict[str, float] = {}
    for report in reports:
        try:
            tree = ET.parse(report)
        except (ET.ParseError, OSError):
            continue
        for case in tree.iter("testcase"):
            file = case.get("file")
            if not file:
                continue
            try:
                spent = float(case.get("time") or 0.0)
            except ValueError:
                continue
            rel = file.replace("\\", "/")
            seconds[rel] = seconds.get(rel, 0.0) + spent
    return seconds


def shard_costs(index: RepoIndex, durations: dict[str, float]) -> tuple[dict[str, float], str]:
    """Cost per candidate test file and the basis it was computed on ("durations" or "test count")."""
    candidates = {p: t for p, t in index.tests.items() if not t.shard_marked}
    known = {p: durations[p] for p in candidates if p in durations}
    known_tests = sum(candidates[p].weight for p in known)
    if not known or known_tests == 0:
        return {p: float(t.weight) for p, t in candidates.items()}, "test count"
    rate = sum(known.values()) / known_tests
    costs = {p: max(known.get(p, t.weight * rate), MIN_FILE_SECONDS) for p, t in candidates.items()}
    return costs, "durations"


def _script_wrappers(index: RepoIndex, changed: list[str], common_names: frozenset[str]) -> set[str]:
    """Python wrappers running a JavaScript test script that is, or reaches, a changed file.

    A script reaches a file it names (an ``import`` of ``../../static/x.mjs``),
    and the files the scripts it names reach (a shared harness).
    """
    touched = {rel for rel in changed if rel in index.script_tokens}
    for rel in changed:
        literal = _literal_for(rel, common_names)
        if literal:
            wanted = set(literal)
            touched |= {s for s, tokens in index.script_tokens.items() if wanted <= tokens}
    frontier = set(touched)
    while frontier:
        names = {PurePosixPath(s).name for s in frontier}
        frontier = {
            s for s, tokens in index.script_tokens.items() if s not in touched and names & tokens
        }
        touched |= frontier
    wrappers: set[str] = set()
    for script in touched:
        wrappers |= index.script_runners.get(script, set())
    return wrappers


def plan_shards(
    files: list[str],
    weights: dict[str, float],
    total_weight: float,
    share: float = SHARD_SHARE,
    max_shards: int = MAX_SHARDS,
) -> list[list[str]]:
    if not files:
        return []
    selected_weight = sum(weights.get(f, 1) for f in files)
    capacity = max(1.0, share * max(total_weight, 1))
    count = max(1, min(max_shards, math.ceil(selected_weight / capacity), len(files)))
    heap: list[tuple[float, int]] = [(0.0, i) for i in range(count)]
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
    costs: dict[str, float] | None = None,
    basis: str = "test count",
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = {p: t.weight for p, t in index.tests.items()}
    costs = costs or {p: float(w) for p, w in weights.items()}
    total = index.total_weight
    shard_info = []
    for i, files in enumerate(shards, start=1):
        (out_dir / f"shard-{i}.args").write_bytes(("\n".join(files) + "\n").encode("utf-8"))
        shard_info.append(
            {
                "index": i,
                "files": len(files),
                "tests": sum(weights.get(f, 1) for f in files),
                "cost": round(sum(costs.get(f, 1.0) for f in files), 1),
            }
        )
    selected_tests = sum(weights.get(f, 1) for f in selection.files)
    report = {
        "mode": selection.mode,
        "reason": selection.reason,
        "changed_files": selection.changed,
        "selected_files": selection.files,
        "selected_tests": selected_tests,
        "total_tests": total,
        "selected_share": round(selected_tests / total, 4) if total else 0.0,
        "cost_basis": basis,
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
            f"- shards: {len(shards)}, balanced by {basis}: "
            + ", ".join(f"#{s['index']}={s['tests']} tests/{s['cost']:g}" for s in shard_info),
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
    parser.add_argument("--durations", help="JUnit XML report(s) of an earlier full run, a file or a directory")
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
    deleted_scripts = [
        rel
        for rel in changed or []
        if rel.startswith("tests/") and rel.endswith(SCRIPT_SUFFIXES) and not (root / rel).exists()
    ]
    index = build_index(root, extra_modules=extra, extra_scripts=deleted_scripts)
    if changed is None:
        candidates = sorted(p for p, t in index.tests.items() if not t.shard_marked)
        selection = Selection("full", fallback_reason, candidates, [])
    else:
        selection = select(index, changed)
    durations = load_durations(Path(args.durations)) if args.durations else {}
    costs, basis = shard_costs(index, durations)
    shards = plan_shards(selection.files, costs, sum(costs.values()))
    report = write_outputs(
        index,
        selection,
        shards,
        Path(args.out_dir),
        Path(args.github_output) if args.github_output else None,
        Path(args.summary) if args.summary else None,
        costs,
        basis,
    )
    print(
        f"mode={report['mode']} files={len(report['selected_files'])} "
        f"tests={report['selected_tests']}/{report['total_tests']} ({report['selected_share']:.1%}) "
        f"shards({basis})={[s['cost'] for s in report['shards']]} reason={report['reason']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
