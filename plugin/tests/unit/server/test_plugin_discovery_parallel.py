"""Discovery scans plugins concurrently, and the order still means something.

Reading a plugin's metadata means importing it, which is why each one gets a
throwaway subprocess. On Windows that is a full interpreter start every time —
measured ~0.84 s per plugin, almost none of it the plugin's own code — so doing
it serially costs about 14 s for the 17 plugins on this tree, against a 30 s
front-end timeout.

Running them concurrently is worth ~4-5x, but only if the results still come
back in submission order: ``_select_effective_records`` derives its grouping
order from first appearance, so completion-order results would reshuffle which
copy of a shadowed plugin wins.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import registry_service as module

pytestmark = pytest.mark.plugin_unit


def _make_root(tmp_path: Path, names: list[str]) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    for name in names:
        (root / name).mkdir()
        (root / name / "plugin.toml").write_text("", encoding="utf-8")
    return root


def _install_stubs(monkeypatch: pytest.MonkeyPatch, root: Path, build) -> None:
    def _parse(config_path, processed_paths, logger):
        return SimpleNamespace(pid=config_path.parent.name, toml_path=config_path)

    monkeypatch.setattr(module, "_parse_single_plugin_config", _parse)
    monkeypatch.setattr(module, "_build_discovery_record_from_context", build)
    monkeypatch.setattr(
        module, "_select_effective_records", lambda records, roots: (records, [])
    )


def test_results_keep_submission_order_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first plugin submitted is the slowest, so completion order differs.

    Mutation: swap ``pool.map`` for ``as_completed``.
    """
    names = [f"p{i:02d}" for i in range(8)]
    root = _make_root(tmp_path, names)

    def _build(ctx, *, scan_timeout=None, force=False):
        # p00 finishes last; anything ordering by completion puts it at the end.
        delay = 0.25 if ctx.pid == "p00" else 0.01
        time.sleep(delay)
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "8")

    snapshot = module._discover_registry_snapshot_sync((root,))

    assert [r.plugin_id for r in snapshot.records] == names, (
        "并发结果按完成顺序回来了——影子选择的分组顺序会跟着变"
    )


def test_concurrency_is_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the point of the change, not just its safety.

    Eight 0.2 s scans finish in well under the 1.6 s they would take serially.
    Without this, a fix that quietly fell back to a serial loop would still pass
    the ordering test above.
    """
    names = [f"p{i:02d}" for i in range(8)]
    root = _make_root(tmp_path, names)

    def _build(ctx, *, scan_timeout=None, force=False):
        time.sleep(0.2)
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "8")

    started = time.monotonic()
    module._discover_registry_snapshot_sync((root,))
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"串行了：8 个 0.2s 的扫描用了 {elapsed:.2f}s"


def test_one_bad_plugin_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raise inside the pool must become a failure entry, not kill the batch."""
    names = ["good_a", "explodes", "good_b"]
    root = _make_root(tmp_path, names)

    def _build(ctx, *, scan_timeout=None, force=False):
        if ctx.pid == "explodes":
            raise RuntimeError("module-level boom")
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "4")

    snapshot = module._discover_registry_snapshot_sync((root,))

    assert [r.plugin_id for r in snapshot.records] == ["good_a", "good_b"]
    assert [f.plugin_id for f in snapshot.failures] == ["explodes"]
    assert "module-level boom" in snapshot.failures[0].error


@pytest.mark.parametrize(
    ("cpu", "pending", "expected"),
    [
        (4, 20, 2),    # 小机器：夹在下界
        (20, 20, 5),   # 本机：cpu // 4
        (64, 20, 8),   # 大机器：夹在上界
        (20, 3, 3),    # 待扫的比预算少，不多开
    ],
)
def test_worker_budget_is_clamped(
    monkeypatch: pytest.MonkeyPatch, cpu: int, pending: int, expected: int
) -> None:
    """Neither one-at-a-time on a big box nor eight interpreters on a small one."""
    monkeypatch.delenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", raising=False)
    monkeypatch.setattr(module.os, "cpu_count", lambda: cpu)

    assert module._discovery_scan_workers(pending) == expected


def test_the_worker_pool_never_exceeds_the_global_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool wider than the semaphore is not throughput, it is scan failures.

    The surplus threads can only queue on ``_SCAN_SLOTS``, and waiting for a slot
    spends the plugin's own scan budget — so a pool of 2 against a gate of 1
    turns one plugin per wave into a spurious scan failure. Both the lower bound
    and the operator override have to be capped by the gate, or the comment
    claiming the two constants cannot disagree is simply false (CodeRabbit).

    Mutation: drop the final cap, so ``_DISCOVERY_SCAN_MIN_WORKERS`` or the
    override can push the pool past the gate.
    """
    monkeypatch.setattr(module.os, "cpu_count", lambda: 64)

    # 下界方向：全局闸调到 1，下界 2 不许把它顶穿。
    monkeypatch.setattr(module, "_DISCOVERY_SCAN_MAX_WORKERS", 1)
    monkeypatch.delenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", raising=False)
    assert module._discovery_scan_workers(10) == 1, "下界顶穿了全局闸"

    # 覆盖方向：显式调大 worker 数也不许超过闸——要更多就去调闸本身。
    monkeypatch.setattr(module, "_DISCOVERY_SCAN_MAX_WORKERS", 8)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "32")
    assert module._discovery_scan_workers(50) == 8, "env 覆盖顶穿了全局闸"


def test_the_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator on a constrained box must be able to force it down to one."""
    monkeypatch.setattr(module.os, "cpu_count", lambda: 64)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "1")

    assert module._discovery_scan_workers(20) == 1


def test_the_time_budget_stops_spawning_more_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-item timeout does not bound the total; the budget does.

    17 plugins at 5-way concurrency is four waves, so a 10 s per-item cap still
    allows 40 s — past the front end's 30 s. Once the budget is gone the
    remaining plugins must be handed a non-positive timeout, which the scanner
    turns into a failure *without* starting a process.

    Mutation: drop the deadline and always pass the per-item timeout.
    """
    names = [f"p{i:02d}" for i in range(6)]
    root = _make_root(tmp_path, names)
    seen: list[float] = []

    def _build(ctx, *, scan_timeout=None, force=False):
        seen.append(scan_timeout)
        time.sleep(0.12)
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)
    monkeypatch.setenv("NEKO_PLUGIN_DISCOVERY_SCAN_WORKERS", "1")
    monkeypatch.setattr(module, "_DISCOVERY_SCAN_BUDGET_SECONDS", 0.25)

    module._discover_registry_snapshot_sync((root,))

    assert seen, "前提没成立：一个都没扫"
    assert seen[0] > 0, "第一个就没预算了，预算设得太小"
    assert any(t == 0.0 for t in seen), (
        "预算用完后仍在给正的 timeout——剩下的插件还会继续起子进程"
    )


def test_a_single_plugin_refresh_forces_only_that_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refreshing one plugin must not make the other sixteen skip their cache.

    ``POST /plugin/{id}/refresh`` used to hand ``force=True`` to the whole
    discovery run, so every plugin bypassed its cache: unrelated slow plugins
    ate the scan budget before the requested one was reached — marking a
    perfectly healthy target as scan-failed — and even the happy path paid for
    a full cold rescan of the registry (codex / CodeRabbit).

    Mutation: propagate the caller's ``force`` to every record again.
    """
    names = ["p00", "p01", "p02"]
    root = _make_root(tmp_path, names)
    forced: dict[str, bool] = {}

    def _build(ctx, *, scan_timeout=None, force=False):
        forced[ctx.pid] = force
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)

    module._discover_registry_snapshot_sync(
        (root,), force_targets=frozenset({"p01"})
    )

    assert forced == {"p00": False, "p01": True, "p02": False}, (
        f"force 的作用范围不对：{forced}"
    )


def test_a_full_refresh_still_forces_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: narrowing the scope must not break the whole-registry force.

    Install, upgrade and uninstall all go through ``refresh_registry(force=True)``
    and depend on nothing being served from cache.
    """
    names = ["p00", "p01"]
    root = _make_root(tmp_path, names)
    forced: dict[str, bool] = {}

    def _build(ctx, *, scan_timeout=None, force=False):
        forced[ctx.pid] = force
        return SimpleNamespace(plugin_id=ctx.pid, config_path=ctx.toml_path)

    _install_stubs(monkeypatch, root, _build)

    module._discover_registry_snapshot_sync((root,), force=True)

    assert forced == {"p00": True, "p01": True}, f"全量 force 被收窄了：{forced}"


def test_an_older_refresh_does_not_publish_over_a_newer_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refreshes are not serialized, and publishing is not atomic.

    Two overlapping full refreshes both walk ``snapshot.records`` and write into
    ``state.plugins``. The one that started *first* can finish last and put the
    older registry contents back — silently undoing a successful upgrade or
    source switch. The race predates this PR, but the cache amplifies it from a
    coincidence into the normal case: a warm refresh takes ~0.14 s against ~3.3 s
    cold, so "later request finishes first" is now routine (codex).

    Mutation: drop the ``_claim_registry_publish`` check.
    """
    import threading

    module._REGISTRY_REFRESH_TICKET = 0
    module._REGISTRY_PUBLISHED_TICKET = 0

    slow_started = threading.Event()
    let_slow_finish = threading.Event()
    slow: threading.Thread | None = None
    applied: list[str] = []

    def _discover(roots, *, force=False, force_targets=frozenset()):
        if threading.current_thread() is slow:
            slow_started.set()
            let_slow_finish.wait(timeout=5)
            tag = "old"
        else:
            tag = "new"
        return SimpleNamespace(
            records=[SimpleNamespace(plugin_id=tag, config_path=Path(f"/{tag}/plugin.toml"),
                                     meta_payload={})],
            failures=[],
            config_paths=set(),
            shadowed=[],
        )

    def _apply(record, *, existing_snapshot, preferred_runtime_plugin_id=None):
        applied.append(record.plugin_id)
        return record.plugin_id, {}

    monkeypatch.setattr(module, "_discover_registry_snapshot_sync", _discover)
    monkeypatch.setattr(module, "_apply_discovery_record_sync", _apply)
    monkeypatch.setattr(module, "_prepare_plugin_import_roots", lambda *a, **k: None)
    monkeypatch.setattr(module, "_get_registered_plugin_snapshot_sync", dict)
    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", list)
    monkeypatch.setattr(module, "_find_existing_runtime_plugin_id_by_config_path", lambda *a, **k: None)
    monkeypatch.setattr(module, "_select_managed_fields", lambda *a, **k: {})
    monkeypatch.setattr(module, "_collect_missing_plugin_ids_sync", lambda *a, **k: set())
    monkeypatch.setattr(module, "_remove_stale_plugin_metadata_sync", lambda *a, **k: ([], []))
    monkeypatch.setattr(module, "_source_for_config_path", lambda *a, **k: "user")

    service = module.PluginRegistryService()

    slow = threading.Thread(target=lambda: service._refresh_registry_sync())
    slow.start()
    assert slow_started.wait(timeout=5), "前提没成立：先开始的那次刷新没跑起来"

    # 后开始的这次整个跑完并发布。
    later = service._refresh_registry_sync()
    assert applied == ["new"], f"前提没成立：后开始的刷新没发布 {applied}"
    assert not later.get("superseded")

    let_slow_finish.set()
    slow.join(timeout=5)

    assert applied == ["new"], (
        f"先开始的那次刷新在后面把更新的注册表内容盖回去了：{applied}"
    )


def test_a_scan_that_ran_out_of_budget_does_not_disqualify_autostart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"No time right now" is not "this plugin is broken".

    ``runtime_load_state="failed"`` is not merely a display state:
    ``_get_autostart_plugin_ids_sync`` drops those plugins from the autostart
    set entirely. Before this PR discovery had no budget at all, so a slow cold
    start could not produce that state — with one, the tail of a slow first scan
    would silently stop auto-starting on boot, with nothing wrong with it. The
    error detail is still recorded for diagnosis; only the disqualifying state
    is withheld, and the next refresh retries.

    Mutation: let a transient scan error fall into the ``failed`` branch.
    """
    from plugin.server.application.plugins import metadata_scanner

    ctx = SimpleNamespace(
        pid="slowpoke",
        toml_path=tmp_path / "slowpoke" / "plugin.toml",
        entry="mod:Cls",
        enabled=True,
        auto_start=True,
        conf={},
        pdata={},
        dependencies=[],
        python_requirements=[],
        python_requirement_paths=(),
        sdk_supported_str="",
        sdk_recommended_str="",
        sdk_untested_str="",
        sdk_conflicts_list=[],
    )

    def _blew_the_budget(**kwargs):
        raise metadata_scanner.PluginMetadataScanError(
            "ScanBudgetExhausted", "discovery time budget exhausted"
        )

    monkeypatch.setattr(module, "scan_plugin_metadata_isolated", _blew_the_budget)
    monkeypatch.setattr(
        module, "describe_plugin_entry_directory_mismatch", lambda *a, **k: None
    )
    monkeypatch.setattr(module, "_check_plugin_dependency", lambda *a, **k: (True, None))

    payload = module._build_discovery_payload(ctx, plugin_id="slowpoke")

    assert payload.get("runtime_load_state") != "failed", (
        "扫描超预算把插件标成 failed —— 它会因此被排除在自启动之外"
    )
    assert payload.get("runtime_load_error_type") == "ScanBudgetExhausted", (
        "错误细节也丢了，出问题时无从诊断"
    )


def test_a_real_load_failure_still_marks_the_plugin_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: a genuinely broken plugin must still be marked.

    Otherwise the fix above would quietly disable the failure state altogether.
    """
    from plugin.server.application.plugins import metadata_scanner

    ctx = SimpleNamespace(
        pid="broken",
        toml_path=tmp_path / "broken" / "plugin.toml",
        entry="mod:Cls",
        enabled=True,
        auto_start=True,
        conf={},
        pdata={},
        dependencies=[],
        python_requirements=[],
        python_requirement_paths=(),
        sdk_supported_str="",
        sdk_recommended_str="",
        sdk_untested_str="",
        sdk_conflicts_list=[],
    )

    def _really_broken(**kwargs):
        raise metadata_scanner.PluginMetadataScanError("SyntaxError", "bad code")

    monkeypatch.setattr(module, "scan_plugin_metadata_isolated", _really_broken)
    monkeypatch.setattr(
        module, "describe_plugin_entry_directory_mismatch", lambda *a, **k: None
    )
    monkeypatch.setattr(module, "_check_plugin_dependency", lambda *a, **k: (True, None))

    payload = module._build_discovery_payload(ctx, plugin_id="broken")

    assert payload.get("runtime_load_state") == "failed", "真正坏掉的插件没有被标记"


def test_a_non_positive_timeout_never_starts_a_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget only bites if the scanner honours it before spawning.

    Mutation: remove the ``timeout <= 0`` guard at the top of
    ``scan_plugin_metadata_isolated``.
    """
    from plugin.server.application.plugins import metadata_scanner

    spawned: list[object] = []
    monkeypatch.setattr(
        metadata_scanner.subprocess,
        "Popen",
        lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
            AssertionError("spawned a worker with no budget left")
        ),
    )

    with pytest.raises(metadata_scanner.PluginMetadataScanError) as excinfo:
        metadata_scanner.scan_plugin_metadata_isolated(
            plugin_id="x",
            module_path="m",
            class_name="C",
            config_path=Path("plugin.toml"),
            conf={},
            pdata={},
            timeout=0.0,
        )

    assert excinfo.value.error_type == "ScanBudgetExhausted"
    assert spawned == []
