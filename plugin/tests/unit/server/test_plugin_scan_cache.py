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


def _age(path: Path, seconds_ago: float) -> None:
    """Backdate a file so the cache's settle guard treats it as stable.

    Nothing is cached while a plugin's files are younger than the settle window
    — that is what closes the "same-size rewrite inside one timestamp tick"
    hole. Tests write and scan within microseconds, so without backdating they
    would only ever exercise the never-cached path.
    """
    import time as _time

    stamp = _time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


def _rewrite(path: Path, text: str) -> None:
    """Change a file's content *and* push its mtime forward.

    Two same-length writes inside one clock tick leave both mtime and size
    unchanged, so the fingerprint legitimately sees no change — a flake that
    only shows up when the machine is busy. Bump the timestamp explicitly so
    the test is about the fingerprint, not about timer resolution.
    """
    path.write_text(text, encoding="utf-8")
    # 比原来的新，但仍然"安定"：两个条件都要满足，否则要么指纹不变、要么结果
    # 不进缓存。
    _age(path, 30)


def _plugin_dir(tmp_path: Path, body: str = "x = 1\n") -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (root / "entry.py").write_text(body, encoding="utf-8")
    for path in root.iterdir():
        _age(path, 60)
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


def test_a_cached_plugin_is_served_even_with_no_budget_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged plugin costs nothing, so the budget should not apply to it.

    Otherwise a handful of changed or hung plugins exhausts the round budget and
    every healthy plugin queued behind them is recorded as a scan failure — its
    registry metadata overwritten with ``failed`` and, with it, its eligibility
    for autostart, despite nothing about it having changed (codex).

    Mutation: move the ``timeout <= 0`` rejection back above the cache lookup.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)  # 先把缓存焐热
    assert len(calls) == 1

    result = module.scan_plugin_metadata_isolated(
        plugin_id="demo",
        module_path="entry",
        class_name="C",
        config_path=config_path,
        conf={},
        pdata={},
        timeout=0.0,
    )

    assert result is not None
    assert len(calls) == 1, "预算没了就拒绝，连手上已有的答案都不给"


def test_no_budget_and_no_cache_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget must still stop work that would actually spawn a process."""
    config_path = _plugin_dir(tmp_path)
    spawned: list = []
    # 不打桩 _scan_plugin_metadata_uncached —— 抛 ScanBudgetExhausted 的正是它
    # 里面那道守卫。改成盯住 Popen：既验证抛错，也验证没有真的起进程。
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *a, **k: spawned.append(a),
    )

    with pytest.raises(module.PluginMetadataScanError) as excinfo:
        module.scan_plugin_metadata_isolated(
            plugin_id="never-scanned",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            timeout=0.0,
        )

    assert excinfo.value.error_type == "ScanBudgetExhausted"
    assert spawned == [], "预算耗尽还起了子进程"


def test_a_just_touched_plugin_is_not_cached_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one hole (mtime_ns, size) leaves is closed at write time, not read time.

    Two same-size writes inside a single filesystem timestamp tick are
    indistinguishable, so an entry captured that fast could already be stale.
    Rather than hashing every file on every scan — measured at 359 ms against
    47 ms for stat alone, on a hot path that currently costs 140 ms — nothing is
    cached until the plugin's files have stopped moving.

    Mutation: cache unconditionally again.
    """
    config_path = _plugin_dir(tmp_path)
    # 把文件改成"刚刚才动过"
    _age(config_path, 0)
    _age(config_path.parent / "entry.py", 0)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, "文件刚动过就进了缓存——同刻度等大小改写会被漏掉"
