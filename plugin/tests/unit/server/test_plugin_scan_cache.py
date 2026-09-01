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


def _plugin_dir(tmp_path: Path, body: str = "x = 1\n", name: str = "demo") -> Path:
    root = tmp_path / name
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
    helper = config_path.parent / "helper.py"
    helper.write_text("y = 1\n", encoding="utf-8")
    # 新建的文件默认是"刚动过"，安定守卫会让第一次扫描根本不进缓存——那样这个
    # 用例就永远是 2 次调用，无论指纹覆盖到哪里，等于什么也没测。
    _age(helper, 60)
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _rewrite(helper, "y = 2\n")
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
    _age(data, 60)  # 见上一个用例：不放老的话第一次扫描根本不进缓存
    calls: list = []

    _scan(monkeypatch, config_path, calls)
    _rewrite(data, "entries: 2\n")
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


def test_ignored_directories_are_never_descended_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pruning has to happen before the walk descends, not after it returns.

    ``rglob("*")`` enumerates every descendant of ``.git`` and calls
    ``is_file()`` on each before the ignore check ever runs, so a development
    checkout with a real object database makes every nominal *cache hit* walk
    thousands of files it was explicitly told to skip — and that time is spent
    outside the discovery scan budget (codex).

    Mutation: go back to ``sorted(root.rglob("*"))`` with the check inside.
    """
    import os as os_module

    config_path = _plugin_dir(tmp_path)
    junk = config_path.parent / ".git" / "objects" / "ab"
    junk.mkdir(parents=True)
    for i in range(5):
        (junk / f"{i:040x}").write_bytes(b"junk")

    real_scandir = os_module.scandir
    scanned: list[str] = []

    def _recording_scandir(path=".", *args, **kwargs):
        scanned.append(str(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os_module, "scandir", _recording_scandir)
    module._plugin_source_fingerprint(config_path)
    monkeypatch.undo()

    inside_git = [entry for entry in scanned if ".git" in entry]
    assert not inside_git, f"下降进了明确忽略的目录：{inside_git}"
    assert scanned, "前提没成立：一次目录读取都没发生"


def test_a_stale_normal_scan_does_not_overwrite_a_forced_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow ordinary scan must not put back what a forced scan just replaced.

    Both scans compute the *same* key — the key only sees the plugin directory,
    and force exists precisely for changes outside it (shared vendor dirs,
    site-packages). So an ordinary scan that started before the forced one and
    finished after it writes its pre-change reading over the fresh result, and
    every ordinary read afterwards serves that stale metadata, which is exactly
    the semantics force is there to provide (CodeRabbit).

    Mutation: drop the epoch comparison before the cache write.
    """
    import threading

    config_path = _plugin_dir(tmp_path)
    slow_started = threading.Event()
    force_done = threading.Event()
    holder: dict = {}
    worker: threading.Thread

    # 两次扫描的键完全一样（conf/pdata/指纹都相同），靠线程身份区分谁是慢的那次。
    def _fake(**inner):
        if threading.current_thread() is worker:
            slow_started.set()
            force_done.wait(timeout=5)
            return "OLD"
        return "NEW"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _run(*, force: bool = False):
        return module.scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            force=force,
        )

    def _slow_normal():
        holder["result"] = _run()

    worker = threading.Thread(target=_slow_normal)
    worker.start()
    assert slow_started.wait(timeout=5), "前提没成立：慢扫描没开始"

    assert _run(force=True) == "NEW"
    force_done.set()
    worker.join(timeout=5)
    assert holder["result"] == "OLD", "前提没成立：慢扫描没拿到旧结果"

    assert _run() == "NEW", "慢扫描把 force 刚写进去的新结果盖回了旧的"


def test_concurrent_forced_scans_do_not_cancel_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forced full refresh scans many plugins at once. They are not rivals.

    With a single global generation counter every worker in that refresh bumps
    the same number, so all but the last one see a changed generation at the
    write and throw their fresh result away. And because force did not evict,
    the *old* entries stayed put — so the next ordinary refresh serves exactly
    the stale metadata the forced refresh existed to replace. The whole refresh
    is voided (codex).

    Mutation: go back to one global counter bumped by force.
    """
    import threading

    first = _plugin_dir(tmp_path, name="a")
    second = _plugin_dir(tmp_path, name="b")
    calls: list[str] = []
    guard = threading.Lock()
    overlapping = [True]
    both_inside = threading.Barrier(2, timeout=5)

    def _fake(**inner):
        with guard:
            calls.append(inner["plugin_id"])
        # 两个强扫必须在时间上真的重叠，否则这个用例测不到互相作废。屏障只在那
        # 一段生效：留着的话，后面本不该发生的扫描会卡在屏障上，把一次失败变成
        # 一次挂死。
        if overlapping[0]:
            both_inside.wait()
        return "v"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _run(config_path: Path, *, force: bool = False):
        return module.scan_plugin_metadata_isolated(
            plugin_id=config_path.parent.name,
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            force=force,
        )

    workers = [
        threading.Thread(target=_run, args=(path,), kwargs={"force": True})
        for path in (first, second)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    overlapping[0] = False
    assert sorted(calls) == ["a", "b"], f"前提没成立：两个强扫没都跑起来 {calls}"

    # 两份结果都该留在缓存里；再普通读一次不应该再起扫描。
    _run(first)
    _run(second)

    assert sorted(calls) == ["a", "b"], (
        f"并发强扫互相把结果作废了，普通读取又扫了一遍：{calls}"
    )


def test_force_drops_the_stale_entry_before_it_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force means the cached answer is not trustworthy — so drop it now.

    Waiting to overwrite it is not enough: if this scan's write is later
    discarded (a second force superseded it), the old entry is still sitting
    there and the next ordinary read serves it (codex).

    Mutation: remove the ``_SCAN_CACHE.pop(key, None)`` from the forced path.
    """
    import threading

    config_path = _plugin_dir(tmp_path)
    calls: list[str] = []
    forcer: threading.Thread | None = None
    forced_inside = threading.Event()
    let_forced_finish = threading.Event()

    def _fake(**inner):
        calls.append(inner["plugin_id"])
        if threading.current_thread() is forcer:
            forced_inside.set()
            let_forced_finish.wait(timeout=5)
        return "v"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _run(*, force: bool = False):
        return module.scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            force=force,
        )

    _run()  # 焐热
    assert len(calls) == 1

    forcer = threading.Thread(target=lambda: _run(force=True))
    forcer.start()
    assert forced_inside.wait(timeout=5), "前提没成立：强扫没进去"

    # 强扫已经开始、还没写回。此刻普通读取绝不能拿到那条被宣布不可信的旧条目。
    _run()
    let_forced_finish.set()
    forcer.join(timeout=5)

    assert len(calls) == 3, (
        f"force 开始后旧条目还留在缓存里，普通读取直接命中了它：{calls}"
    )


def test_concurrent_scans_are_capped_across_the_whole_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool's ``max_workers`` bounds one refresh, not the server.

    Refresh routes are not serialized against each other and forced refreshes
    skip the cache, so repeated clicks, retries or several callers at once can
    launch ``requests x workers`` metadata interpreters. At the measured ~66 MB
    resident each, a few overlapping refreshes exhaust memory and take the
    plugin server down despite the advertised per-refresh cap (codex).

    Mutation: drop the semaphore from ``_scan_with_slot``.
    """
    import threading
    import time

    monkeypatch.setattr(module, "_SCAN_SLOTS", threading.BoundedSemaphore(2))
    config_path = _plugin_dir(tmp_path)
    live = 0
    peak = 0
    guard = threading.Lock()
    release = threading.Event()

    def _fake(**inner):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        release.wait(timeout=5)
        with guard:
            live -= 1
        return "v"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _one(index: int):
        module.scan_plugin_metadata_isolated(
            plugin_id=f"demo{index}",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            timeout=4.0,
        )

    threads = [threading.Thread(target=_one, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    # 给它们足够时间全部挤进来——闸没了的话六个会同时在里面。
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and peak < 3:
        time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert peak <= 2, f"同时有 {peak} 个扫描在跑，全局闸没管住"
    assert peak > 0, "前提没成立：一个都没跑起来"


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
