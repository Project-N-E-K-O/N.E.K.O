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


def test_a_forced_scan_with_no_budget_left_still_drops_the_stale_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"That cached answer is untrustworthy" does not depend on having time.

    A forced scan that arrives with the round budget already spent bails out
    before it can run — but if it also bails out before evicting, the entry it
    just declared stale sits there and the next ordinary read serves it. Letting
    the cache go cold is the right side to err on: cold costs one rescan, stale
    is wrong metadata that nothing will correct.

    Mutation: put ``_begin_scan`` back below the ``timeout <= 0`` return.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list[str] = []

    def _fake(**inner):
        calls.append(inner["plugin_id"])
        return "v"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _run(*, force: bool = False, timeout: float = 10.0):
        return module.scan_plugin_metadata_isolated(
            plugin_id="demo",
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
            force=force,
            timeout=timeout,
        )

    _run()  # 焐热
    assert len(calls) == 1

    _run(force=True, timeout=0.0)  # 预算见底的强制刷新
    _run()  # 普通读取：必须重扫，不能吃到那条已经被宣布不可信的

    assert len(calls) == 3, (
        f"预算见底的 force 没删掉旧条目，普通读取直接命中了它：{calls}"
    )


def test_recycling_the_generation_table_does_not_reopen_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the per-key stamps drops the evidence the check runs on.

    ``_SCAN_GENERATION`` is bounded, and the overflow path clears it. That resets
    every key to 0 — so an in-flight ordinary scan that recorded ``gen=0`` before
    the clear reads 0 again at the write and sails through the very check that
    exists to stop it. Clearing the table means "the per-key evidence is no
    longer reliable", which is what the global epoch is for (CodeRabbit).

    Mutation: clear the table without bumping the epoch.
    """
    import threading

    stale = _plugin_dir(tmp_path, name="stale")
    filler = _plugin_dir(tmp_path, name="filler")
    overflow = _plugin_dir(tmp_path, name="overflow")
    monkeypatch.setattr(module, "_SCAN_CACHE_MAX_ENTRIES", 1)

    calls: list[str] = []
    slow_inside = threading.Event()
    let_slow_finish = threading.Event()
    slow: threading.Thread | None = None

    def _fake(**inner):
        calls.append(inner["plugin_id"])
        if threading.current_thread() is slow:
            slow_inside.set()
            let_slow_finish.wait(timeout=5)
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

    stale_key = module._scan_cache_key(
        plugin_id="stale",
        module_path="entry",
        class_name="C",
        config_path=stale,
        conf={},
        pdata={},
        python_requirement_paths=(),
    )

    # 一次普通扫描停在半路：它此刻记下的是 gen=0（表里还没有它的键）。
    slow = threading.Thread(target=lambda: _run(stale))
    slow.start()
    assert slow_inside.wait(timeout=5), "前提没成立：慢扫描没进去"

    # 两次强扫把代次表撑满并触发回收，把 stale 那把键的代次一起抹掉。
    _run(filler, force=True)
    _run(overflow, force=True)
    assert stale_key not in module._SCAN_GENERATION, (
        "前提没成立：代次表没有被回收"
    )

    let_slow_finish.set()
    slow.join(timeout=5)
    before = len(calls)

    _run(stale)

    assert len(calls) == before + 1, (
        "回收代次表之后，清表前记下的在途结果又被放行写进了缓存"
    )


def test_an_ordinary_scan_does_not_overwrite_a_forced_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same generation does not mean same freshness.

    The ordering here is the whole test. The ordinary scan must start *after*
    the forced one has bumped the generation — otherwise the generation check
    stops it first and this guard proves nothing (the first version made that
    mistake and the mutation survived). Starting after, it captures the same
    generation and sails through that check; what has to stop it is the fact
    that the entry it would overwrite came from a forced scan. The two
    subprocesses import external dependencies in an unspecified order, so the
    later-starting ordinary scan can still have read the older dependency
    (codex).

    Mutation: drop the forced-entry check before the cache write.
    """
    import threading

    config_path = _plugin_dir(tmp_path)
    forced_inside = threading.Event()
    ordinary_inside = threading.Event()
    let_forced_finish = threading.Event()
    let_ordinary_finish = threading.Event()
    forced_thread: threading.Thread | None = None
    ordinary_thread: threading.Thread | None = None

    def _fake(**inner):
        current = threading.current_thread()
        if current is forced_thread:
            forced_inside.set()
            let_forced_finish.wait(timeout=5)
            return "FORCED"
        ordinary_inside.set()
        let_ordinary_finish.wait(timeout=5)
        return "ORDINARY"

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

    # 1. force 先开工：它把代次推上去，并把旧条目删掉。
    forced_thread = threading.Thread(target=lambda: _run(force=True))
    forced_thread.start()
    assert forced_inside.wait(timeout=5), "前提没成立：force 扫描没开始"

    # 2. 普通扫描在这之后才开始，所以它捕获的是**同一个**代次。
    ordinary_thread = threading.Thread(target=_run)
    ordinary_thread.start()
    assert ordinary_inside.wait(timeout=5), "前提没成立：普通扫描没开始"

    # 3. force 先落地，普通的后落地。
    let_forced_finish.set()
    forced_thread.join(timeout=5)
    let_ordinary_finish.set()
    ordinary_thread.join(timeout=5)

    assert _run() == "FORCED", (
        "普通扫描把 force 的结果盖掉了——代次相同，但它读到的依赖可能更旧"
    )


def test_a_plugin_behind_a_directory_symlink_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the fingerprint cannot see, the cache must not answer for.

    Neither ``os.walk`` nor the ``rglob`` it replaced descends into directory
    symlinks, so a plugin importing through ``demo/lib -> ../../shared/lib`` has
    that whole subtree missing from its key. Without a cache that was merely
    invisible; with one it means editing the target leaves the key unchanged and
    an ordinary refresh serves pre-edit entries and tool schemas (codex).

    Following the link is the wrong side of the trade — it can point at
    site-packages or form a cycle, and this walk is on the hot path. So the tree
    simply never caches: such a plugin pays a real scan every time, which is
    slow rather than wrong.

    Mutation: ignore directory symlinks and let the tree settle normally.
    """
    config_path = _plugin_dir(tmp_path)
    linked = config_path.parent / "lib"
    target = tmp_path / "shared"
    target.mkdir()
    (target / "helper.py").write_text("y = 1" + chr(10), encoding="utf-8")
    try:
        os.symlink(target, linked, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"这台机器不允许建目录软链：{exc}")
    _age(linked, 60)

    calls: list = []
    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, (
        "带目录软链的插件进了缓存——软链后面那棵树不在指纹里，改了也发现不了"
    )


def test_the_symlink_check_is_wired_into_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property without needing symlink privileges.

    The test above is skipped wherever creating a symlink is not permitted —
    which includes plenty of Windows setups, i.e. exactly the platform this PR
    is about. This one drives the real ``os.scandir`` and only overrides
    ``is_symlink()`` on one entry, so the production branch is genuinely
    exercised where a ``skipif`` guard would be silently absent.

    Its first version patched ``os.path.islink``, which the fingerprint stopped
    calling when the walk moved to ``scandir`` — the guard kept passing while
    testing nothing. The suite caught that; hence patching at the level the
    code actually reads.

    Mutation: ignore symlinks and let the tree settle normally.
    """
    config_path = _plugin_dir(tmp_path)
    (config_path.parent / "lib").mkdir()

    real_scandir = os.scandir

    class _PretendLink:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def is_symlink(self):
            return True

    class _Scan:
        def __init__(self, entries):
            self._entries = entries

        def __enter__(self):
            return self._entries

        def __exit__(self, *exc):
            return False

    def _scandir(path, *args, **kwargs):
        with real_scandir(path, *args, **kwargs) as scan:
            items = list(scan)
        return _Scan([_PretendLink(e) if e.name == "lib" else e for e in items])

    monkeypatch.setattr(os, "scandir", _scandir)
    _, newest = module._plugin_source_fingerprint(config_path)
    monkeypatch.undo()

    assert newest == module._NEVER_SETTLED, "软链没有让这棵树变成不可缓存"


def test_a_file_symlink_also_makes_the_tree_uncacheable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file symlink hides a retarget, not just a subtree.

    ``stat()`` follows the link and records only the target's mtime and size, so
    repointing it at a same-size file with the same timestamp — the ordinary
    shape of versioned files copied with timestamps preserved — leaves the key
    identical while Python imports different code (codex).

    Mutation: only treat *directory* symlinks as uncacheable.
    """
    config_path = _plugin_dir(tmp_path)
    target = tmp_path / "real_helper.py"
    target.write_text("y = 1" + chr(10), encoding="utf-8")
    link = config_path.parent / "helper.py"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"这台机器不允许建软链：{exc}")
    _age(target, 60)

    calls: list = []
    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, (
        "带文件软链的插件进了缓存——把链重新指向同样大小、同样时间戳的文件，"
        "键一点都不会变"
    )


def test_a_symlinked_plugin_root_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root itself can be the symlink, and scandir walks straight past that.

    Listing a symlinked root shows nothing unusual inside it, so the tree cached
    normally — while repointing the root at a different copy left the key
    untouched (CodeRabbit).

    Mutation: only look for symlinks among the entries, not at the root.
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "plugin.toml").write_text("[plugin]" + chr(10), encoding="utf-8")
    (real / "entry.py").write_text("x = 1" + chr(10), encoding="utf-8")
    for path in real.iterdir():
        _age(path, 60)
    linked_root = tmp_path / "demo"
    try:
        os.symlink(real, linked_root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"这台机器不允许建目录软链：{exc}")

    calls: list = []
    config_path = linked_root / "plugin.toml"
    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, "插件根自己是软链，这棵树却进了缓存"


def test_capacity_eviction_invalidates_in_flight_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing the cache for capacity throws away evidence others rely on.

    The "an ordinary scan never overwrites a forced entry" rule reads the entry
    that is there. A capacity clear removes it, so a scan sharing that
    generation then sees an empty slot and writes its older read (codex).
    Capacity eviction now invalidates in-flight results the same way the
    generation-table overflow does.

    Mutation: clear without bumping the epoch.
    """
    config_path = _plugin_dir(tmp_path)
    monkeypatch.setattr(module, "_SCAN_CACHE_MAX_ENTRIES", 1)
    module._SCAN_CACHE.clear()

    before = module._SCAN_EPOCH
    calls: list = []

    def _fake(**inner):
        calls.append(inner["plugin_id"])
        return "v"

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _fake)

    def _run(plugin_id: str):
        return module.scan_plugin_metadata_isolated(
            plugin_id=plugin_id,
            module_path="entry",
            class_name="C",
            config_path=config_path,
            conf={},
            pdata={},
        )

    _run("demo")        # 写第 1 条
    _run("demo-other")  # 不同的键，触发容量清表

    assert module._SCAN_EPOCH != before, (
        "按容量清表没有作废在途结果——共享同一代次的扫描会把更旧的读数写进来"
    )


def test_a_failed_forced_scan_still_keeps_the_key_cold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A force that never lands must not leave the slot open to a stale write.

    The "an ordinary scan never overwrites a forced result" rule only fired when
    a forced result existed. If the forced scan times out or raises, nothing is
    written — and an ordinary scan sharing that generation happily fills the
    evicted key with what it read, which may predate the very change force was
    invalidating for (codex).

    So the eviction leaves a tombstone rather than a hole: reads treat it as a
    miss, and the existing write-side check refuses ordinary writes over it. No
    in-flight table, no cleanup path — the same ``(result, forced)`` shape.

    Mutation: pop the key instead of writing a tombstone.
    """
    import threading

    config_path = _plugin_dir(tmp_path)
    forced_inside = threading.Event()
    let_forced_fail = threading.Event()
    forced_thread: threading.Thread | None = None

    def _fake(**inner):
        if threading.current_thread() is forced_thread:
            forced_inside.set()
            let_forced_fail.wait(timeout=5)
            raise module.PluginMetadataScanError("TimeoutExpired", "hung")
        return "ORDINARY"

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

    forced_outcome: list[str] = []

    def _forced():
        # 这一趟 force 是**故意**失败的：上面的 _fake 认出 force 线程就抛
        # TimeoutExpired。接住只是让线程正常收尾。
        #
        # 但不能默默吞掉——换成另一种 PluginMetadataScanError（比如槽位耗尽）时，
        # 用例的前提"force 是在扫描里超时的"已经不成立了，而吞掉之后它照样绿。
        # 记下来，join 之后断言。
        try:
            _run(force=True)
        except module.PluginMetadataScanError as exc:
            forced_outcome.append(exc.error_type)
        else:
            forced_outcome.append("<returned>")

    forced_thread = threading.Thread(target=_forced)
    forced_thread.start()
    assert forced_inside.wait(timeout=5), "前提没成立：force 扫描没开始"

    # 普通扫描在 force 之后开始，所以捕获同一个代次。
    assert _run() == "ORDINARY"
    let_forced_fail.set()
    forced_thread.join(timeout=5)
    assert forced_outcome == ["TimeoutExpired"], (
        f"前提没成立：force 这趟不是在扫描里超时的，而是 {forced_outcome}"
    )

    calls: list = []
    _scan(monkeypatch, config_path, calls)
    assert calls == ["demo"], (
        "force 失败之后，普通扫描把可能读自变更前依赖的结果填进了那个坑"
    )


def test_failing_forced_scans_cannot_grow_the_cache_without_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tombstone is an entry, and its key carries the whole file listing.

    The capacity check lived on the successful-write path only, so the
    tombstones a failing forced scan leaves behind bypassed it entirely. Run
    that against a series of distinct source fingerprints and the cache grows
    past its cap forever (CodeRabbit) — which matters here because each key
    embeds every file's path, mtime and size.

    Mutation: write the tombstone without making room first.
    """
    monkeypatch.setattr(module, "_SCAN_CACHE_MAX_ENTRIES", 3)
    module._SCAN_CACHE.clear()

    def _boom(**inner):
        raise module.PluginMetadataScanError("TimeoutExpired", "hung")

    monkeypatch.setattr(module, "_scan_plugin_metadata_uncached", _boom)

    for index in range(12):
        # 每个插件一个目录 —— 也就是一份不同的源指纹，一把不同的键。
        config_path = _plugin_dir(tmp_path, name=f"p{index}")
        with pytest.raises(module.PluginMetadataScanError):
            module.scan_plugin_metadata_isolated(
                plugin_id=f"p{index}",
                module_path="entry",
                class_name="C",
                config_path=config_path,
                conf={},
                pdata={},
                force=True,
            )

    assert len(module._SCAN_CACHE) <= module._SCAN_CACHE_MAX_ENTRIES, (
        f"失败的 force 扫描留下的墓碑绕过了容量上限：{len(module._SCAN_CACHE)} 条"
    )


def test_adding_a_symlink_invalidates_an_already_cached_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marking the result non-settled is useless once the entry already exists.

    The symlink rule only decided whether a *new* result could be cached. The
    fingerprint itself was byte-identical to a tree without the link, so a
    plugin cached before the link appeared kept being served from that warm
    entry and never reached the non-cacheable path at all (codex).

    Mutation: keep the symlink out of the fingerprint tuple.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    _scan(monkeypatch, config_path, calls)          # 先把这棵树缓存好
    _scan(monkeypatch, config_path, calls)
    assert calls == ["demo"], "前提没成立：第一次没进缓存"

    target = tmp_path / "shared"
    target.mkdir()
    try:
        os.symlink(target, config_path.parent / "lib", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"这台机器不允许建目录软链：{exc}")

    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, (
        "加了一条软链之后，键没变，还在端着加链之前那条缓存"
    )


def test_a_successful_force_on_fresh_files_does_not_strand_the_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tombstone must not outlive the scan that installed it.

    A forced scan of files edited moments ago succeeds, but the settle window
    says the result is not cacheable — so nothing replaced the tombstone. Every
    later ordinary scan was then refused the write, because the entry sitting
    there is marked forced, and the plugin lost caching *permanently* (codex).
    "Edit a plugin, then press refresh" is about the most ordinary sequence
    there is, which is what makes this worse than the sticky-cold cost I had
    described for the failure path.

    Mutation: leave the tombstone in place when a forced scan is unsettled.
    """
    config_path = _plugin_dir(tmp_path)
    calls: list = []

    # 文件的 mtime 从头到尾不动 —— 动它就等于换了一把键，墓碑压根不会参与，
    # 这条守卫的第一版就是这么把自己废掉的（变异存活才发现）。改成拨安定窗口：
    # 先把窗口调得极长，让这次 force"成功但不该进缓存"。
    monkeypatch.setattr(module, "_CACHE_SETTLE_NS", 3600 * 1_000_000_000)
    _scan(monkeypatch, config_path, calls, force=True)
    assert len(calls) == 1

    # 再把窗口调回正常：同一把键，现在算安定了，普通扫描应当能重新开始缓存。
    monkeypatch.setattr(module, "_CACHE_SETTLE_NS", 2_000_000_000)
    _scan(monkeypatch, config_path, calls)
    _scan(monkeypatch, config_path, calls)

    assert len(calls) == 2, (
        "墓碑被落在那儿了——这个插件之后永远进不了缓存"
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
