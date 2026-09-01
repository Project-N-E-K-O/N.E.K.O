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
