"""Smoke for Testbench driver helpers (no full NEKO host required)."""
from __future__ import annotations

import importlib.util
import inspect
import threading
from pathlib import Path

import pytest


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
            pytest.skip(f"NEKO plugin SDK unavailable: {exc}", allow_module_level=True)
        raise


def test_find_script_python_or_none():
    mod = _load_mod()
    prefix = mod.find_script_python()
    assert prefix is None or (isinstance(prefix, list) and prefix)


def test_resolve_code_layout_prefers_source_without_bundle(tmp_path, monkeypatch):
    mod = _load_mod()
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
    plugin_dir = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("NEKO_VERSION", raising=False)
    monkeypatch.setenv("NEKO_TESTBENCH_COMPATIBLE_NEKO", "*")
    assert mod._check_compatible_neko(plugin_dir) is None


def test_compatible_neko_rejects_mismatch(monkeypatch, tmp_path):
    mod = _load_mod()
    plugin_dir = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("NEKO_TESTBENCH_COMPATIBLE_NEKO", ">=99.0.0")
    monkeypatch.setenv("NEKO_VERSION", "1.0.0")
    err = mod._check_compatible_neko(plugin_dir)
    assert err is not None
    assert "not in compatible_neko" in err


def test_resolve_neko_root_for_start_uses_bundled_without_repo(tmp_path):
    mod = _load_mod()
    plugin_dir = tmp_path / "pkg"
    bundled = plugin_dir / "bundled" / "tests" / "testbench"
    bundled.mkdir(parents=True)
    (bundled / "server.py").write_text("# stub\n", encoding="utf-8")
    root = mod._resolve_neko_root_for_start(plugin_dir)
    assert root == (plugin_dir / "bundled").resolve()


def test_hosted_ui_entries_accept_ctx_kwarg():
    mod = _load_mod()
    cls = mod.TestbenchDriverPlugin
    for name in ("start", "stop", "open_ui", "status", "dashboard", "startup", "shutdown"):
        sig = inspect.signature(getattr(cls, name))
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()), name


def test_reconcile_runtime_state_clears_stale_file(tmp_path, monkeypatch):
    mod = _load_mod()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_path = data_dir / "driver_state.json"
    state_path.write_text(
        '{"url":"http://127.0.0.1:59999","shell_pid":999999,"mode":"A","ui":"webview"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_health_ok", lambda _url, timeout=0.35: False)
    monkeypatch.setattr(mod, "_pid_alive", lambda _pid: False)

    plugin = mod.TestbenchDriverPlugin.__new__(mod.TestbenchDriverPlugin)
    plugin._plugin_dir = Path(__file__).resolve().parents[1]
    plugin._shell_proc = None
    plugin._embed_server = None
    plugin._embed_thread = None
    plugin._webview_proc = None
    plugin._runtime_lock = threading.Lock()
    plugin._start_in_progress = False
    plugin._script_python = []
    plugin.data_path = lambda *parts: data_dir.joinpath(*parts) if parts else data_dir  # type: ignore[method-assign]
    status = plugin._status_dict()
    assert status["running"] is False
    assert not state_path.is_file()


def test_reconcile_mode_b_clears_when_embed_dead_despite_host_pid(tmp_path, monkeypatch):
    mod = _load_mod()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_path = data_dir / "driver_state.json"
    state_path.write_text(
        '{"url":"http://127.0.0.1:59999","shell_pid":12345,"mode":"B","ui":"browser"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_health_ok", lambda _url, timeout=0.35: False)
    monkeypatch.setattr(mod, "_pid_alive", lambda _pid: True)

    plugin = mod.TestbenchDriverPlugin.__new__(mod.TestbenchDriverPlugin)
    plugin._plugin_dir = Path(__file__).resolve().parents[1]
    plugin._shell_proc = None
    plugin._embed_server = None
    plugin._embed_thread = None
    plugin._webview_proc = None
    plugin._runtime_lock = threading.Lock()
    plugin._start_in_progress = False
    plugin._script_python = []
    plugin.data_path = lambda *parts: data_dir.joinpath(*parts) if parts else data_dir  # type: ignore[method-assign]
    status = plugin._status_dict()
    assert status["running"] is False
    assert not state_path.is_file()
