"""Scanning a plugin is a whole interpreter; scanning it twice is a waste.

One scan is a throwaway subprocess — measured ~0.84 s on this machine, of which
~0.76 s is interpreter start plus importing the scanner framework, i.e. a cost
that has nothing to do with the plugin. Computing the cache key instead walks
the plugin directory and stats it: ~1 ms per plugin, three orders of magnitude
cheaper.

The interesting part is not the hit, it is when the cache must NOT answer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from plugin.server.application.plugins import metadata_scanner as module

pytestmark = pytest.mark.plugin_unit


@pytest.fixture(autouse=True)
def _clean_cache():
    module.clear_plugin_metadata_scan_cache()
    yield
    module.clear_plugin_metadata_scan_cache()


def _rewrite(path: Path, text: str) -> None:
    """Change a file's content *and* push its mtime forward.

    Two same-length writes inside one clock tick leave both mtime and size
    unchanged, so the fingerprint legitimately sees no change — a flake that
    only shows up when the machine is busy. Bump the timestamp explicitly so
    the test is about the fingerprint, not about timer resolution.
    """
    path.write_text(text, encoding="utf-8")
    stamp = path.stat().st_mtime + 10
    os.utime(path, (stamp, stamp))


def _plugin_dir(tmp_path: Path, body: str = "x = 1\n") -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (root / "entry.py").write_text(body, encoding="utf-8")
    return root / "plugin.toml"


def _scan(monkeypatch, config_path: Path, calls: list, **kw):
    def _fake(**inner):
        calls.append(inner["plugin_id"])
        return module.IsolatedPluginMetadata(
            entries_preview=[], handlers={}, entry_methods={}
        )

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)
    return module.scan_plugin_metadata_isolated(
        plugin_id="demo",
        module_path="entry",
        class_name="C",
        config_path=config_path,
        conf={},
        pdata={},
        **kw,
    )


def test_a_second_scan_of_unchanged_files_is_served_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the cache lookup."""
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert calls == ["demo"], f"扫了 {len(calls)} 次，缓存没命中"


def test_touching_a_neighbouring_module_invalidates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key covers the whole plugin directory, not just the entry file.

    Plugins routinely split code across sibling modules. Keying only on
    ``plugin.toml`` and the entry would serve a stale scan after an edit to a
    neighbour — and that is a bug nobody would think to blame on a cache.

    Mutation: narrow the fingerprint to the config file only.
    """
    config_path = _plugin_dir(tmp_path)
    (config_path.parent / "helper.py").write_text("y = 1\n", encoding="utf-8")
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    (config_path.parent / "helper.py").write_text("y = 2\n", encoding="utf-8")
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, "改了同目录别的模块，却拿到了旧扫描结果"


def test_force_bypasses_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install/upgrade/uninstall and the refresh button cannot trust the key.

    The fingerprint sees the plugin directory only, so a change in a shared
    vendor directory or in site-packages is invisible to it.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls, force=True)
    _scan(monkeypatch, config_path, calls)  # force 之后应重新填好缓存

    assert len(calls) == 2, f"force 没有绕过，或绕过后没有回填：calls={len(calls)}"


def test_failures_are_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that timed out describes the moment, not the plugin.

    Caching it would stick the failure to the plugin until something on disk
    changed — a transient slow disk would look like a broken plugin forever.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    def _boom(**inner):
        calls.append(inner["plugin_id"])
        raise module.PluginMetadataScanError("TimeoutExpired", "slow")

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _boom)
    for _ in range(2):
        with pytest.raises(module.PluginMetadataScanError):
            module.scan_plugin_metadata_isolated(
                plugin_id="demo",
                module_path="entry",
                class_name="C",
                config_path=config_path,
                conf={},
                pdata={},
            )

    assert len(calls) == 2, "失败被缓存了——下次刷新不会重试"


def test_a_different_config_is_a_different_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same files, different conf: the scan sees conf, so the key must too."""
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    def _fake(**inner):
        calls.append(inner["conf"])
        return module.IsolatedPluginMetadata(
            entries_preview=[], handlers={}, entry_methods={}
        )

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)
    for conf in ({"a": 1}, {"a": 2}, {"a": 1}):
        module.scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf=conf,
            pdata={},
        )

    assert calls == [{"a": 1}, {"a": 2}], f"conf 没进键：{calls}"


# ── 收尾不能被一个握着管道的孙进程拖住 ────────────────────────────────


def test_stderr_read_gives_up_instead_of_blocking() -> None:
    """The diagnostics read is bounded; the scan is not hostage to it.

    ``read(n)`` on a pipe returns at n bytes or EOF, and EOF needs every write
    handle closed — a grandchild that inherited one keeps it open. Today the
    worker redirects fd 2 to devnull before importing plugin code, so this is
    unreachable in production (verified end to end). But the read sits after
    every timer has been cancelled, so if that invariant ever slipped there
    would be nothing to interrupt it.

    Mutation: go back to a bare ``process.stderr.read(1000)``.
    """
    import subprocess
    import sys
    import time

    from plugin.server.application.plugins import metadata_scanner as scanner

    child = (
        "import subprocess,sys,time\n"
        "sys.stderr.write('partial')\n"
        "sys.stderr.flush()\n"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
    )
    process = subprocess.Popen([sys.executable, "-c", child], stderr=subprocess.PIPE)
    try:
        process.wait(timeout=15)
        started = time.monotonic()
        out = scanner._read_worker_stderr(process)
        elapsed = time.monotonic() - started

        assert elapsed < scanner._STDERR_READ_TIMEOUT_SECONDS + 3, (
            f"读 stderr 卡了 {elapsed:.1f}s——孙进程握着写端就永远等不到 EOF"
        )
        assert isinstance(out, str)
    finally:
        if process.poll() is None:
            process.kill()


def test_the_fingerprint_covers_non_code_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plugins derive entries from data files at import time, not just code.

    Keying on ``.py``/``.toml``/``.json`` alone serves a stale scan after a
    ``metadata.yaml`` edit, and metadata that disagrees with runtime behaviour
    is among the hardest inconsistencies to trace back to a cache (codex).

    Mutation: restrict the fingerprint to code suffixes again.
    """
    config_path = _plugin_dir(tmp_path)
    data = config_path.parent / "metadata.yaml"
    data.write_text("entries: 1\n", encoding="utf-8")
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    data.write_text("entries: 2\n", encoding="utf-8")
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, "改了同目录的数据文件却拿到旧扫描结果"


def test_pycache_churn_does_not_invalidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widening the fingerprint must not make it fire on its own byproducts.

    ``__pycache__`` is rewritten by the very scan we are caching, so counting it
    would make every entry a one-shot.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    cache_dir = config_path.parent / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "entry.cpython-311.pyc").write_bytes(b"\x00\x01")
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 1, "__pycache__ 的变动把缓存冲掉了——等于没有缓存"
