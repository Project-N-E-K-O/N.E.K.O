"""Unit tests for ``scripts/check_module_layering.py``.

Three-pronged coverage:

1. End-to-end: run the real script against the live repo. Exit must be 0.
   This is the gate that will fail CI the moment someone introduces a new
   layering inversion or cycle.

2. Synthetic fixture: construct a tiny in-memory ``LAYERS`` graph plus a
   handful of ``edges`` and assert the analyzer flags the right things.
   We poke ``find_violations`` directly so the unit test stays fast and
   doesn't touch the filesystem.

3. Registration completeness: a top-level directory with Python code that
   is in no layer fails the gate. Exercised on throwaway trees under
   ``tmp_path``.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_module_layering.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_script_module():
    """Import ``scripts/check_module_layering.py`` as a module without
    relying on ``scripts`` being a package. Returns the loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "check_module_layering", SCRIPT_PATH,
    )
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Live-repo gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_live_repo_passes() -> None:
    """The current repo state must satisfy the layering rules.

    Runs the script as a subprocess against the live tree — same code path
    CI uses. Any inversion / cycle (including dynamic-import ones) produces
    a non-zero exit and a stderr explanation; both are surfaced in the
    pytest failure so the offending edges are immediately visible.
    """
    # 30s timeout: the script normally finishes in <2s on this repo. The cap
    # protects CI from hanging if the AST walker ever regresses into an
    # infinite loop on a malformed file.
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "Layering check timed out:\n"
            f"--- stdout ---\n{exc.stdout or ''}\n"
            f"--- stderr ---\n{exc.stderr or ''}"
        )
    assert result.returncode == 0, (
        "Live repo violates module layering:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. Synthetic-graph unit tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def _make_edge(src: str, dst: str, *, line: int = 1) -> tuple:
    """Build an EdgeRecord matching the script's tuple shape."""
    return (
        Path("synthetic.py"),    # path
        line,                    # lineno
        1,                       # col
        src,                     # src package
        dst,                     # dst package
        f"from {dst} import x",  # repr
    )


@pytest.mark.unit
def test_pure_inversion_is_flagged(script_module, monkeypatch) -> None:
    """A single low→high edge with no return path is a LAYER_INVERSION,
    not a cycle."""
    # Override the layering for the duration of this test so we don't
    # have to reason about the full real-world graph.
    monkeypatch.setattr(
        script_module, "LAYERS",
        [(0, {"low"}), (1, {"high"})],
        raising=True,
    )
    monkeypatch.setattr(
        script_module, "PACKAGE_TO_LAYER",
        {"low": 0, "high": 1},
        raising=True,
    )
    edges = [_make_edge("low", "high")]
    inversions, cycles = script_module.find_violations(edges)
    assert len(inversions) == 1
    assert inversions[0][3] == "low"
    assert inversions[0][4] == "high"
    assert cycles == []


@pytest.mark.unit
def test_two_node_cycle_is_flagged(script_module, monkeypatch) -> None:
    """Same-layer A→B + B→A is a LAYER_CYCLE even though neither edge is
    an inversion on its own. The reported cycle path must include both
    real edges as a closed loop."""
    monkeypatch.setattr(
        script_module, "LAYERS",
        [(0, {"a", "b"})],
        raising=True,
    )
    monkeypatch.setattr(
        script_module, "PACKAGE_TO_LAYER",
        {"a": 0, "b": 0},
        raising=True,
    )
    edges = [
        _make_edge("a", "b", line=10),
        _make_edge("b", "a", line=20),
    ]
    inversions, cycles = script_module.find_violations(edges)
    assert inversions == []
    assert len(cycles) == 1
    cycle = cycles[0]
    # Cycle is a closed loop of EdgeRecord; for a 2-cycle that's 2 edges.
    assert len(cycle) == 2
    # cycle[i].dst == cycle[i+1].src; loop closes (last.dst == first.src).
    assert cycle[0][4] == cycle[1][3]
    assert cycle[1][4] == cycle[0][3]
    pair = {(cycle[0][3], cycle[0][4]), (cycle[1][3], cycle[1][4])}
    assert pair == {("a", "b"), ("b", "a")}


@pytest.mark.unit
def test_three_node_cycle_is_flagged(script_module, monkeypatch) -> None:
    """A→B→C→A (no two-cycles) is detected and reported as a 3-edge path
    where every edge corresponds to a real witness — not a synthetic
    ``(u→v, v→w)`` pair masquerading as a 2-cycle."""
    monkeypatch.setattr(
        script_module, "LAYERS",
        [(0, {"a", "b", "c"})],
        raising=True,
    )
    monkeypatch.setattr(
        script_module, "PACKAGE_TO_LAYER",
        {"a": 0, "b": 0, "c": 0},
        raising=True,
    )
    edges = [
        _make_edge("a", "b"),
        _make_edge("b", "c"),
        _make_edge("c", "a"),
    ]
    inversions, cycles = script_module.find_violations(edges)
    assert inversions == []
    assert len(cycles) == 1
    cycle = cycles[0]
    # Three edges forming a closed loop: cycle[i].dst == cycle[i+1].src,
    # and cycle[-1].dst == cycle[0].src.
    assert len(cycle) == 3
    for i in range(len(cycle) - 1):
        assert cycle[i][4] == cycle[i + 1][3], (
            f"edge {i} dst {cycle[i][4]!r} != edge {i+1} src {cycle[i+1][3]!r}"
        )
    assert cycle[-1][4] == cycle[0][3], "cycle does not close"
    # The set of edges must be exactly the three input edges — no
    # fabricated ``(u→v, v→w)`` masquerading as evidence.
    actual = {(e[3], e[4]) for e in cycle}
    assert actual == {("a", "b"), ("b", "c"), ("c", "a")}


@pytest.mark.unit
def test_clean_dag_passes(script_module, monkeypatch) -> None:
    """High→low edges in a tree-like graph yield no violations."""
    monkeypatch.setattr(
        script_module, "LAYERS",
        [(0, {"low"}), (1, {"mid"}), (2, {"high"})],
        raising=True,
    )
    monkeypatch.setattr(
        script_module, "PACKAGE_TO_LAYER",
        {"low": 0, "mid": 1, "high": 2},
        raising=True,
    )
    edges = [
        _make_edge("high", "mid"),
        _make_edge("high", "low"),
        _make_edge("mid", "low"),
    ]
    inversions, cycles = script_module.find_violations(edges)
    assert inversions == []
    assert cycles == []


@pytest.mark.unit
def test_dynamic_import_is_collected(script_module, tmp_path) -> None:
    """``importlib.import_module("foo.bar")`` must be picked up the same
    as ``from foo.bar import ...``. Sanity-check via the AST collector
    rather than the full file walker.
    """
    import ast

    source = (
        "import importlib\n"
        "def f():\n"
        "    importlib.import_module('foo.bar')\n"
        "    __import__('baz')\n"
    )
    tree = ast.parse(source)
    collector = script_module.ImportCollector()
    collector.visit(tree)
    modules = {rec[2] for rec in collector.records}
    # ``import importlib`` is also collected — that's expected and harmless.
    assert "foo.bar" in modules
    assert "baz" in modules


@pytest.mark.unit
def test_function_scoped_import_is_collected(script_module) -> None:
    """Imports inside function bodies count the same as module-top ones —
    "even dynamic references are forbidden"."""
    import ast

    source = (
        "def lazy():\n"
        "    from inside_pkg import thing\n"
        "    return thing\n"
    )
    tree = ast.parse(source)
    collector = script_module.ImportCollector()
    collector.visit(tree)
    modules = {rec[2] for rec in collector.records}
    assert "inside_pkg" in modules


@pytest.mark.unit
def test_relative_imports_are_skipped(script_module) -> None:
    """All relative imports must be ignored — both ``from . import foo`` and
    ``from .foo import bar``. The risk: a sibling submodule whose name
    collides with a top-level layered package (e.g. ``plugin/core/bus`` has
    a ``memory`` submodule, plus the repo also has a top-level ``memory/``
    package) would fabricate a fake ``plugin → memory`` edge if the
    collector blindly records ``module='memory'`` from
    ``from .memory import MemoryClient``.
    """
    import ast

    source = (
        "from . import sibling\n"
        "from .memory import Foo\n"
        "from .config.sub import Bar\n"
        "from ..parent_pkg import Baz\n"
        "from absolute_pkg import qualified\n"  # this one MUST be collected
    )
    tree = ast.parse(source)
    collector = script_module.ImportCollector()
    collector.visit(tree)
    modules = {rec[2] for rec in collector.records}
    # Only the level=0 absolute import is a real cross-package edge.
    assert "absolute_pkg" in modules
    # All relative variants must be skipped — none of these are top-level
    # package names from the layering's perspective.
    assert "memory" not in modules
    assert "config" not in modules
    assert "config.sub" not in modules
    assert "parent_pkg" not in modules
    assert "sibling" not in modules


@pytest.mark.unit
def test_public_knowledge_boundaries_are_forbidden_edges(script_module) -> None:
    """Main and the plugin server reach public knowledge over HTTP only, and
    the knowledge package never depends on memory. Layers alone allow all of
    these edges (knowledge sits in L2), so ``FORBIDDEN_EDGES`` pins them."""
    root = script_module.REPO_ROOT

    def edge(rel: str, dst: str):
        src = rel.split("/", 1)[0]
        return (root / rel, 1, 1, src, dst, f"import {dst}")

    edges = [
        edge("main_logic/public_knowledge.py", "knowledge"),
        edge("main_routers/public_knowledge_router.py", "knowledge"),
        edge("app/main_server/web_app.py", "knowledge"),
        edge("plugin/server/routes/knowledge_bridge.py", "knowledge"),
        edge("knowledge/service.py", "memory"),
        # Allowed: the Memory Server hosts the subsystem.
        edge("app/memory_server/knowledge_routes.py", "knowledge"),
        edge("app/memory_server/knowledge_routes.py", "memory"),
        edge("knowledge/service.py", "utils"),
    ]
    flagged = {
        found[0][0].relative_to(root).as_posix()
        for found in script_module.find_forbidden_edges(edges)
    }
    assert flagged == {
        "main_logic/public_knowledge.py",
        "main_routers/public_knowledge_router.py",
        "app/main_server/web_app.py",
        "plugin/server/routes/knowledge_bridge.py",
        "knowledge/service.py",
    }


@pytest.mark.unit
def test_services_boundaries_are_forbidden_edges(script_module) -> None:
    """``services`` shares L2 with main_logic and knowledge, so layers alone
    allow services -> main_logic and services -> knowledge. main_logic
    imports services, and services is Main-side code that must reach public
    knowledge over HTTP, so ``FORBIDDEN_EDGES`` pins both. Its L2 peer memory
    and everything below stay importable."""
    root = script_module.REPO_ROOT

    def edge(rel: str, dst: str):
        src = rel.split("/", 1)[0]
        return (root / rel, 1, 1, src, dst, f"import {dst}")

    edges = [
        edge("services/chat_sync/profile_sync.py", "main_logic"),
        edge("services/theater/tts_bridge.py", "main_logic"),
        edge("services/chat_sync/source.py", "knowledge"),
        # Allowed: same-layer memory, lower layers, and the reverse direction.
        edge("services/theater/numeric_v2_archive.py", "memory"),
        edge("services/chat_sync/runtime.py", "utils"),
        edge("services/chat_sync/runtime.py", "config"),
        edge("main_logic/core/greeting.py", "services"),
        edge("main_routers/chat_sync_router.py", "services"),
    ]
    flagged = {
        (found[0][0].relative_to(root).as_posix(), found[0][4])
        for found in script_module.find_forbidden_edges(edges)
    }
    assert flagged == {
        ("services/chat_sync/profile_sync.py", "main_logic"),
        ("services/theater/tts_bridge.py", "main_logic"),
        ("services/chat_sync/source.py", "knowledge"),
    }


@pytest.mark.unit
def test_services_theater_workshop_and_launcher_core_are_layered(script_module) -> None:
    """services is an L2 peer of main_logic, theater_workshop an L3 peer of
    main_routers and launcher_core an L6 entrypoint next to app. With the
    real layers, services importing main_routers is an inversion while the
    legitimate edges of the three packages are not."""
    layer = script_module.PACKAGE_TO_LAYER
    assert layer["services"] == layer["main_logic"] == 2
    assert layer["theater_workshop"] == layer["main_routers"] == 3
    assert layer["launcher_core"] == layer["app"] == 6

    edges = [
        _make_edge("services", "main_routers"),
        _make_edge("services", "memory"),
        _make_edge("theater_workshop", "services"),
        _make_edge("launcher_core", "app"),
        _make_edge("main_logic", "services"),
    ]
    inversions, cycles = script_module.find_violations(edges)
    assert [(e[3], e[4]) for e in inversions] == [("services", "main_routers")]
    assert cycles == []


# ---------------------------------------------------------------------------
# 3. Registration completeness
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _write_tree(root: Path) -> None:
    """A tree where only ``newpkg`` is an unregistered top-level package."""
    for rel in (
        "registered/mod.py",
        "scripts/tool.py",                # UNLAYERED_TOP_LEVEL
        "tests/test_mod.py",              # EXCLUDE_DIRS
        "docs/conf.py",                   # EXCLUDE_DIRS
        ".hidden/mod.py",                 # dot-directory
        "data_only/readme.txt",           # no Python
        "vendored/node_modules/mod.py",   # Python only under an excluded dir
        "launcher.py",                    # root-level module, not a directory
        "newpkg/sub/mod.py",
    ):
        _write(root, rel)


@pytest.mark.unit
def test_unregistered_package_is_reported_without_git(
    script_module, tmp_path, monkeypatch,
) -> None:
    """Without git, the filesystem walk reports a top-level directory holding
    Python code that is in no layer, and only that one."""
    monkeypatch.setattr(script_module, "_tracked_python_files", lambda root: None)
    _write_tree(tmp_path)
    found = script_module.find_unregistered_packages(tmp_path, {"registered"})
    assert found == [("newpkg", "newpkg/sub/mod.py")]
    # Registering it clears the report.
    assert script_module.find_unregistered_packages(
        tmp_path, {"registered", "newpkg"},
    ) == []


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_unregistered_package_check_reads_git_index(
    script_module, tmp_path, monkeypatch,
) -> None:
    """With git, only tracked files count: an untracked local scratch
    directory and a tracked file deleted from the working tree are ignored,
    while a tracked unregistered package is still reported."""
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True, capture_output=True,
        )

    git("init", "-q")
    _write_tree(tmp_path)
    _write(tmp_path, "gone/mod.py")
    git("add", "-A")
    (tmp_path / "gone" / "mod.py").unlink()
    _write(tmp_path, "scratch/mod.py")  # untracked

    assert script_module._tracked_python_files(tmp_path) is not None
    found = script_module.find_unregistered_packages(tmp_path, {"registered"})
    assert found == [("newpkg", "newpkg/sub/mod.py")]


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_tree_inside_another_checkout_falls_back_to_the_filesystem(
    script_module, tmp_path, monkeypatch,
) -> None:
    """An exported tree unpacked inside some other git work tree must not be
    judged by that outer repository's index, which tracks none of its files."""
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True, capture_output=True)
    exported = tmp_path / "exported"
    _write_tree(exported)

    assert script_module._tracked_python_files(exported) is None
    found = script_module.find_unregistered_packages(exported, {"registered"})
    assert [directory for directory, _ in found] == ["newpkg"]


@pytest.mark.unit
def test_live_repo_has_no_unregistered_packages(script_module) -> None:
    """Every top-level directory of the repo with Python code has a layer."""
    assert script_module.find_unregistered_packages(
        script_module.REPO_ROOT, script_module.KNOWN_PACKAGES,
    ) == []


@pytest.mark.unit
def test_main_fails_on_unregistered_package(script_module, monkeypatch, capsys) -> None:
    """``main`` runs the completeness check and tells how to register."""
    monkeypatch.setattr(script_module, "collect_edges", lambda paths: [])
    monkeypatch.setattr(
        script_module, "find_unregistered_packages",
        lambda root, known: [("newpkg", "newpkg/mod.py")],
    )
    assert script_module.main([]) == 1
    out = capsys.readouterr().out
    assert "newpkg/  UNREGISTERED_PACKAGE  newpkg/mod.py" in out
    assert "Add 'newpkg' to LAYERS" in out
