"""Unit tests for ``scripts/check_module_layering.py``.

Two-pronged coverage:

1. End-to-end: run the real script against the live repo. Exit must be 0.
   This is the gate that will fail CI the moment someone introduces a new
   layering inversion or cycle.

2. Synthetic fixture: construct a tiny in-memory ``LAYERS`` graph plus a
   handful of ``edges`` and assert the analyzer flags the right things.
   We poke ``find_violations`` directly so the unit test stays fast and
   doesn't touch the filesystem.
"""
from __future__ import annotations

import importlib.util
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
    an inversion on its own."""
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
    forward, back = cycles[0]
    # Both directions captured; pair is (a→b, b→a) regardless of order.
    pair = {(forward[3], forward[4]), (back[3], back[4])}
    assert pair == {("a", "b"), ("b", "a")}


@pytest.mark.unit
def test_three_node_cycle_is_flagged(script_module, monkeypatch) -> None:
    """A→B→C→A (no two-cycles) is still detected via SCC analysis."""
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
    assert len(cycles) >= 1


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
