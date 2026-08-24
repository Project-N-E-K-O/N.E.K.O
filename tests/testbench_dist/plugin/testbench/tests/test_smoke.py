"""Smoke for Testbench driver helpers (no full NEKO host required)."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_mod():
    plugin_dir = Path(__file__).resolve().parents[1]
    init_path = plugin_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location("testbench_driver_mod", init_path)
    assert spec and spec.loader
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except ModuleNotFoundError as exc:
        if "plugin" in str(exc):
            return None
        raise


def test_find_script_python_or_none():
    mod = _load_mod()
    if mod is None:
        return
    prefix = mod.find_script_python()
    assert prefix is None or (isinstance(prefix, list) and prefix)


def test_resolve_code_layout_prefers_source_without_bundle(tmp_path, monkeypatch):
    mod = _load_mod()
    if mod is None:
        return
    # Use real repo layout via plugin_dir walk.
    plugin_dir = Path(__file__).resolve().parents[1]
    neko = mod._find_neko_root(plugin_dir)
    assert neko is not None
    code_dir, import_root = mod._resolve_code_layout(plugin_dir, neko)
    assert code_dir.name == "testbench"
    assert (code_dir / "server.py").is_file() or (code_dir / "run_testbench.py").is_file()
    assert import_root.is_dir()


def test_compatible_neko_wildcard_ok(monkeypatch):
    mod = _load_mod()
    if mod is None:
        return
    plugin_dir = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("NEKO_VERSION", raising=False)
    monkeypatch.setenv("NEKO_TESTBENCH_COMPATIBLE_NEKO", "*")
    assert mod._check_compatible_neko(plugin_dir) is None


def test_compatible_neko_rejects_mismatch(monkeypatch, tmp_path):
    mod = _load_mod()
    if mod is None:
        return
    plugin_dir = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("NEKO_TESTBENCH_COMPATIBLE_NEKO", ">=99.0.0")
    monkeypatch.setenv("NEKO_VERSION", "1.0.0")
    err = mod._check_compatible_neko(plugin_dir)
    assert err is not None
    assert "not in compatible_neko" in err
