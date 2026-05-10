#!/usr/bin/env python3
"""Static check: enforce top-level module layering and forbid cycles.

Why this exists
---------------
The repo's top-level packages have a strict dependency hierarchy: each
package belongs to a layer, and a package may only depend on packages in
strictly lower layers (or peers in the same layer, provided the result
stays acyclic). The hierarchy:

    L0  config, steamworks                   ← foundation (data + vendored SDK)
    L1  utils
    L2  memory, main_logic
    L3  main_routers
    L4  plugin                                ← high-level extension surface
    L5  brain                                 ← top-of-stack agent orchestration
    L6  app, local_server, launcher.py        ← entrypoints

Violations are split into two categories, both forbidden:

    LAYER_INVERSION — package A imports from package B, but A's layer is
                      strictly lower than B's (low importing high).
    LAYER_CYCLE     — package A imports from package B AND package B (directly
                      or transitively, including via dynamic imports inside
                      function bodies) imports from package A. Same-layer pairs
                      are allowed only if no cycle exists between them.

What it flags
-------------
The walker descends into every node in every .py — module-top imports,
function-scoped imports, conditional ``try/except ImportError`` imports,
and string-form dynamic imports (``importlib.import_module("foo.bar")`` /
``__import__("foo.bar")``). Per the project convention "even dynamic
references are forbidden", a deferred import inside a function body counts
the same as a module-top one.

Suppression
-----------
None. The whole point is that the layering must be unconditional — a
suppression mechanism would let exceptions accrete back to today's mess.
If a real architectural change demands a new edge, the right fix is to
adjust ``LAYERS`` (move the package up/down) and re-run; if that creates
a violation elsewhere, that's the actual signal.

Output
------
Every violation prints as:
    path:line:col  LAYER_INVERSION  src→dst (Lx → Ly): <import>
    path:line:col  LAYER_CYCLE      src↔dst: <import>  [other half: …]

Exit status is 1 when any violation is found, 0 otherwise.

Usage:
    python scripts/check_module_layering.py [paths...]
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Layering definition
# ---------------------------------------------------------------------------
#
# Each entry: (layer_index, set-of-top-level-package-names-in-that-layer).
# Lower index = lower (more foundational) layer. A package may import from
# any package whose layer index is strictly less, or from a same-layer peer
# provided no cycle results. ``launcher`` is the launcher.py top-level
# script (no package); we treat it specially in PACKAGE_OF_FILE.

LAYERS: List[Tuple[int, Set[str]]] = [
    (0, {"config", "steamworks"}),
    (1, {"utils"}),
    (2, {"memory", "main_logic"}),
    (3, {"main_routers"}),
    (4, {"plugin"}),
    (5, {"brain"}),
    (6, {"app", "local_server", "launcher"}),
]

PACKAGE_TO_LAYER: Dict[str, int] = {
    pkg: idx for idx, pkgs in LAYERS for pkg in pkgs
}
KNOWN_PACKAGES: Set[str] = set(PACKAGE_TO_LAYER.keys())


CODE_INVERSION = "LAYER_INVERSION"
CODE_CYCLE = "LAYER_CYCLE"

DEFAULT_PATHS: list[str] = ["."]

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
    "deps",
    "docs",
    "assets",
    "static",
    "templates",
    "specs",
    # Tests are excluded: test code legitimately reaches across layers
    # (e.g. a unit test for utils may stub a main_routers symbol). Test-only
    # imports do NOT participate in the production dependency graph.
    "tests",
    # Plugin payloads are user-supplied bundles, not project code.
    "plugin/plugins",
    "plugin/tests",
    "plugin/runs",
}

# This script names the packages in strings; skip self-scan.
EXCLUDE_FILES = {
    "scripts/check_module_layering.py",
}


# ---------------------------------------------------------------------------
# File → owning top-level package
# ---------------------------------------------------------------------------


def package_of_file(path: Path) -> Optional[str]:
    """Return the top-level package owning ``path``, or ``None`` if outside
    the layering scope (and therefore not subject to the rules).
    """
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None
    # Top-level launcher.py is its own "package" so we can place it in L6.
    if rel == "launcher.py":
        return "launcher"
    head = rel.split("/", 1)[0]
    return head if head in KNOWN_PACKAGES else None


# ---------------------------------------------------------------------------
# AST walker: collect every imported module name (incl. dynamic forms)
# ---------------------------------------------------------------------------


class ImportCollector(ast.NodeVisitor):
    """Records every imported module name seen anywhere in the AST.

    Captures:
      * ``import a.b.c``                       → "a.b.c"
      * ``from a.b import c``                  → "a.b"
      * ``importlib.import_module("a.b.c")``   → "a.b.c"
      * ``__import__("a.b.c")``                → "a.b.c"

    Function bodies and ``if``/``try`` blocks are descended into — deferred
    imports are equally subject to the layering rules.
    """

    def __init__(self) -> None:
        # (lineno, col, module_name, raw_repr_for_error_message)
        self.records: list[tuple[int, int, str, str]] = []

    # -- static forms -------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.records.append(
                (node.lineno, node.col_offset + 1, alias.name, f"import {alias.name}")
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # All relative imports are intra-package — skip regardless of whether
        # ``module`` is set. Both ``from . import x`` (level>0, module=None)
        # and ``from .foo import x`` (level>0, module='foo') resolve inside
        # the current package; only ``module`` from ``level=0`` (absolute)
        # imports can name a top-level package. Without this guard, a file
        # like ``plugin/core/bus/__init__.py`` containing
        # ``from .memory import MemoryClient`` would be mis-recorded as a
        # ``plugin → memory`` edge — fabricating cross-package dependencies
        # whenever a sibling submodule shares a name with a layered top-level
        # package, which can produce false LAYER_INVERSION/LAYER_CYCLE reports.
        if node.level:
            self.generic_visit(node)
            return
        if node.module:
            names = ", ".join(a.name for a in node.names) or "*"
            self.records.append(
                (
                    node.lineno,
                    node.col_offset + 1,
                    node.module,
                    f"from {node.module} import {names}",
                )
            )
        self.generic_visit(node)

    # -- dynamic forms ------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        target = self._dynamic_import_target(node)
        if target is not None:
            arg = node.args[0] if node.args else None
            self.records.append(
                (
                    node.lineno,
                    node.col_offset + 1,
                    target,
                    f"{ast.unparse(node.func)}({_arg_repr(arg)})",
                )
            )
        self.generic_visit(node)

    @staticmethod
    def _dynamic_import_target(node: ast.Call) -> Optional[str]:
        """If ``node`` is a recognised dynamic-import call with a string
        literal first argument, return the imported module name.

        Recognised forms:
          - ``importlib.import_module("a.b")``
          - ``import_module("a.b")``           (after ``from importlib import import_module``)
          - ``__import__("a.b")``
        """
        func = node.func
        name: Optional[str] = None
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            # importlib.import_module(...) / something.import_module(...) —
            # accept any attribute target named import_module to keep the
            # check simple; false positives on user-defined methods are
            # exceedingly unlikely in this codebase.
            name = "import_module"
        elif isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}:
            name = func.id
        if name is None:
            return None
        if not node.args:
            return None
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None


def _arg_repr(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "<expr>"


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    if rel in EXCLUDE_FILES:
        return True
    for ex in EXCLUDE_DIRS:
        if "/" in ex and (rel == ex or rel.startswith(ex + "/")):
            return True
    return False


def _iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            if p.suffix == ".py" and not _is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if not _is_excluded(f):
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


# ---------------------------------------------------------------------------
# Edge collection + violation analysis
# ---------------------------------------------------------------------------


# An "edge record" is one observed import: where (file, line, col), what
# (source-package → dest-package), and the human-readable repr.
EdgeRecord = Tuple[Path, int, int, str, str, str]  # (path, line, col, src_pkg, dst_pkg, repr)


def collect_edges(paths: Iterable[Path]) -> List[EdgeRecord]:
    """Walk every .py under ``paths`` and yield (file, line, col, src, dst,
    repr) for every cross-package import that touches a known top-level
    package. Same-package edges (``utils → utils``) are skipped — they
    can't violate layering.
    """
    edges: list[EdgeRecord] = []
    for file in _iter_python_files(paths):
        src_pkg = package_of_file(file)
        if src_pkg is None:
            # File is outside the layering scope; nothing to enforce.
            continue
        tree = _parse_file(file)
        if tree is None:
            continue
        collector = ImportCollector()
        collector.visit(tree)
        for lineno, col, module_name, repr_ in collector.records:
            head = module_name.split(".", 1)[0]
            if head not in KNOWN_PACKAGES:
                continue
            if head == src_pkg:
                continue
            edges.append((file, lineno, col, src_pkg, head, repr_))
    return edges


def find_violations(
    edges: List[EdgeRecord],
) -> Tuple[List[EdgeRecord], List[Tuple[EdgeRecord, EdgeRecord]]]:
    """Split ``edges`` into:
      * pure layer-inversions (low package importing high package)
      * cycles (pairs of edges where each side imports the other; this
        captures both ``A → B`` + ``B → A`` directly, regardless of
        whether either crosses layers)

    A direct inversion is reported as a single LAYER_INVERSION violation.
    A cycle is reported once per (pkg_a, pkg_b) pair, with one example edge
    from each direction.
    """
    inversions: list[EdgeRecord] = []

    # adjacency: src_pkg -> set(dst_pkg). Used for cycle detection.
    adj: Dict[str, Set[str]] = defaultdict(set)
    # First-witness edge for each (src, dst) pair (for cycle reporting).
    witness: Dict[Tuple[str, str], EdgeRecord] = {}

    for edge in edges:
        _, _, _, src, dst, _ = edge
        adj[src].add(dst)
        witness.setdefault((src, dst), edge)
        if PACKAGE_TO_LAYER[src] < PACKAGE_TO_LAYER[dst]:
            inversions.append(edge)

    # Cycle detection over the adjacency graph. We only need 2-cycles
    # explicitly (A↔B); a longer cycle still implies at least one inversion
    # OR a same-layer cycle, both of which we want to surface.
    cycles: list[Tuple[EdgeRecord, EdgeRecord]] = []
    seen_pairs: Set[Tuple[str, str]] = set()
    for src, dsts in adj.items():
        for dst in dsts:
            if src == dst:
                continue
            pair = tuple(sorted((src, dst)))
            if pair in seen_pairs:
                continue
            if dst in adj and src in adj[dst]:
                seen_pairs.add(pair)
                cycles.append((witness[(src, dst)], witness[(dst, src)]))

    # Longer SCC detection (3+ node cycles). Use Tarjan over the package
    # graph; report any SCC with >1 node as a cycle. The first edge along
    # the SCC's first-encountered cycle is used as the witness.
    sccs = _strongly_connected_components(adj)
    for component in sccs:
        if len(component) <= 1:
            continue
        # Reduce to the canonical 2-cycles already covered above.
        # For an SCC of size N, reporting every 2-edge witness inside it is
        # noisy; we instead emit one synthetic pair: (any edge u→v inside
        # the SCC) + (any edge v→u or longer return path edge).
        component_set = set(component)
        chosen_pair: Optional[Tuple[EdgeRecord, EdgeRecord]] = None
        for u in component:
            for v in adj[u]:
                if v in component_set and u != v:
                    pair = tuple(sorted((u, v)))
                    if pair in seen_pairs:
                        chosen_pair = None
                        break
                    # Find a return witness: any edge v→x with x reachable
                    # back to u via SCC.
                    return_witness = None
                    for w in adj[v]:
                        if w in component_set and w != v:
                            return_witness = witness.get((v, w))
                            if return_witness is not None:
                                break
                    if return_witness is not None:
                        seen_pairs.add(pair)
                        chosen_pair = (witness[(u, v)], return_witness)
                        break
            if chosen_pair is not None:
                cycles.append(chosen_pair)
                break

    return inversions, cycles


def _strongly_connected_components(
    adj: Dict[str, Set[str]],
) -> List[List[str]]:
    """Tarjan's SCC. Iterative form to avoid recursion depth limits even
    though our graph is tiny — keeps the script self-contained.
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: Set[str] = set()
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    components: list[list[str]] = []

    def strongconnect(node: str) -> None:
        # Iterative Tarjan via explicit work stack.
        work: list[tuple[str, Iterator[str]]] = [(node, iter(adj.get(node, ())))]
        indices[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)

        while work:
            v, it = work[-1]
            try:
                w = next(it)
            except StopIteration:
                # All neighbours processed.
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == indices[v]:
                    scc: list[str] = []
                    while True:
                        node_pop = stack.pop()
                        on_stack.discard(node_pop)
                        scc.append(node_pop)
                        if node_pop == v:
                            break
                    components.append(scc)
                continue
            if w not in indices:
                indices[w] = index_counter[0]
                lowlink[w] = index_counter[0]
                index_counter[0] += 1
                stack.append(w)
                on_stack.add(w)
                work.append((w, iter(adj.get(w, ()))))
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])

    for node in list(adj.keys()):
        if node not in indices:
            strongconnect(node)
    return components


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _format_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Forbid layering inversions and dependency cycles between "
            "top-level packages."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/directories to scan (default: entire repo, minus EXCLUDE_DIRS).",
    )
    parser.add_argument(
        "--show-layers",
        action="store_true",
        help="Print the configured layering and exit.",
    )
    args = parser.parse_args(argv)

    if args.show_layers:
        for idx, pkgs in LAYERS:
            print(f"L{idx}  {', '.join(sorted(pkgs))}")
        return 0

    raw_paths = args.paths or DEFAULT_PATHS
    targets = [Path(p) if Path(p).is_absolute() else REPO_ROOT / p for p in raw_paths]

    edges = collect_edges(targets)
    inversions, cycles = find_violations(edges)

    total = 0
    for path, lineno, col, src, dst, repr_ in inversions:
        print(
            f"{_format_path(path)}:{lineno}:{col}  {CODE_INVERSION}  "
            f"{src} (L{PACKAGE_TO_LAYER[src]}) → {dst} (L{PACKAGE_TO_LAYER[dst]}): {repr_}"
        )
        total += 1

    for forward, back in cycles:
        f_path, f_line, f_col, f_src, f_dst, f_repr = forward
        b_path, b_line, b_col, b_src, b_dst, b_repr = back
        print(
            f"{_format_path(f_path)}:{f_line}:{f_col}  {CODE_CYCLE}  "
            f"{f_src} ↔ {f_dst}: {f_repr}  [reverse: "
            f"{_format_path(b_path)}:{b_line}: {b_repr}]"
        )
        total += 1

    if total:
        print(
            f"\n{total} layering violation(s) found.\n"
            "Top-level packages have a strict ordering — only higher layers "
            "may depend on lower ones, and the resulting graph must be "
            "acyclic. Run `python scripts/check_module_layering.py "
            "--show-layers` to print the hierarchy. Function-scoped and "
            "dynamic imports (importlib.import_module / __import__) count "
            "the same as module-top imports — there is no per-line "
            "suppression. To add a legitimate new edge, move the package "
            "in `LAYERS` and re-run; if that creates a violation elsewhere, "
            "that's the actual problem to solve.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
