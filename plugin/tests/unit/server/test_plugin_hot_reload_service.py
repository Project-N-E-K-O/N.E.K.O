from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from plugin.server.application.plugins import hot_reload_service as module
from plugin.server.application.plugins.operation_lock import plugin_operation_lock
from plugin.server.domain.errors import ServerDomainError


pytestmark = pytest.mark.plugin_unit


class _FakeLifecycleService:
    def __init__(self, use_real_lock: bool = False) -> None:
        self.reload_calls: list[str] = []
        self.reload_kwargs: list[dict[str, object]] = []
        self._use_real_lock = use_real_lock

    async def reload_plugin(
        self, plugin_id: str, *, only_if_running: bool = False
    ) -> dict[str, object]:
        self.reload_kwargs.append({"only_if_running": only_if_running})
        if self._use_real_lock:
            # 模拟真实 reload_plugin：它带 @serialized_plugin_operation，内部
            # 要抢操作锁。没有这一步，watcher 的 bounded_operation_wait 预算
            # 无从生效，busy 永远抛不出来。
            async with plugin_operation_lock.hold():
                self.reload_calls.append(plugin_id)
        else:
            self.reload_calls.append(plugin_id)
        return {"success": True, "plugin_id": plugin_id}


def _write_plugin_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "demo"',
                'name = "Demo"',
                'version = "0.1.0"',
                'type = "plugin"',
                'entry = "demo:DemoPlugin"',
            ]
        ),
        encoding="utf-8",
    )
    (root / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")


def _make_service(
    tmp_path: Path,
    lifecycle: _FakeLifecycleService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    running: bool = True,
) -> module.PluginHotReloadService:
    source_dir = tmp_path / "demo"
    _write_plugin_source(source_dir)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD", True)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD_INTERVAL", 0.05)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD_DEBOUNCE", 0.1)
    monkeypatch.setattr(module, "plugin_is_running_sync", lambda plugin_id: running)
    service = module.PluginHotReloadService(lifecycle_service=lifecycle)
    monkeypatch.setattr(
        service,
        "_collect_targets_sync",
        lambda: [
            module._WatchTarget(plugin_id="demo", root=source_dir, is_development=False)
        ],
    )
    return service


async def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def _stop(service: module.PluginHotReloadService) -> None:
    await service.stop(timeout=1.0)


async def test_start_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD", False)
    service = module.PluginHotReloadService(lifecycle_service=_FakeLifecycleService())
    assert service.start() is False
    assert service.is_running is False
    await _stop(service)


async def test_change_triggers_reload_after_debounce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        # 基线建立：首轮扫描只记录签名，不触发 reload。
        assert await _wait_for(lambda: bool(service._signatures))
        assert lifecycle.reload_calls == []

        (source_dir / "__init__.py").write_text(
            "VALUE = 2  # changed\n", encoding="utf-8"
        )
        assert await _wait_for(lambda: lifecycle.reload_calls == ["demo"])
        # watcher 必须带 only_if_running=True 调用：锁内复查依赖它。
        assert lifecycle.reload_kwargs == [{"only_if_running": True}]
    finally:
        await _stop(service)
    assert not service.is_running


async def test_first_scan_does_not_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    assert service.start() is True
    try:
        await asyncio.sleep(0.4)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_syntax_error_blocks_reload_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "def broken(:\n    pass\n", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []

        (source_dir / "__init__.py").write_text(
            "VALUE = 3  # fixed\n", encoding="utf-8"
        )
        assert await _wait_for(lambda: lifecycle.reload_calls == ["demo"])
    finally:
        await _stop(service)


async def test_broken_manifest_blocks_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "plugin.toml").write_text(
            "[plugin\nbroken toml", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_stopped_plugin_is_not_started_by_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch, running=False)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "VALUE = 4  # changed\n", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_reload_only_if_running_leaves_a_stopped_plugin_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锁内复查（评审 #3）：watcher 锁外检查之后、拿到锁之前用户停掉了
    插件时，reload_plugin(only_if_running=True) 不得把它拉起并持久化自启
    意图——那正是三语文档承诺"不会发生"的事。"""
    from plugin.server.application.plugins import lifecycle_service as lifecycle_module
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    started: list[dict[str, object]] = []

    async def fake_start(
        self: object,
        plugin_id: str,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        started.append({"plugin_id": plugin_id, "kwargs": kwargs})
        return {"success": True}

    monkeypatch.setattr(
        lifecycle_module, "_plugin_is_running_sync", lambda plugin_id: False
    )
    monkeypatch.setattr(PluginLifecycleService, "start_plugin", fake_start)
    service = PluginLifecycleService()

    result = await service.reload_plugin(
        "hot-reload-stopped-probe", only_if_running=True
    )
    assert started == []
    assert result["skipped"] is True

    # 手动按钮（默认路径）不受影响：停着的插件照样被 reload 拉起，且带用户
    # 意图（待批准记录要被清掉，见 test_plugin_install_autostart_gate）。
    result = await service.reload_plugin("hot-reload-stopped-probe")
    assert started == [
        {
            "plugin_id": "hot-reload-stopped-probe",
            "kwargs": {"persist_user_intent": True},
        }
    ]


async def test_start_and_host_registration_are_rejected_while_shutting_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关停门闩（评审 #2）：置位后 start_plugin 快速失败；入口检查通过之后
    才置闩的窗口由注册临界区里的复查封死——被 asyncio.shield 保护的
    in-flight reload 不能再注册出没人停止的孤儿 host。"""
    from plugin.core.state import state
    from plugin.server.application.plugins import lifecycle_service as lifecycle_module
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    monkeypatch.setattr(lifecycle_module, "_operations_shutting_down", True)

    with pytest.raises(ServerDomainError) as error:
        await PluginLifecycleService().start_plugin("hot-reload-latch-probe")
    assert error.value.code == "PLUGIN_OPERATION_SHUTTING_DOWN"

    with pytest.raises(ServerDomainError) as error:
        await asyncio.to_thread(
            lifecycle_module._register_or_replace_host_sync,
            "hot-reload-latch-probe",
            object(),
        )
    assert error.value.code == "PLUGIN_OPERATION_SHUTTING_DOWN"
    assert "hot-reload-latch-probe" not in state.plugin_hosts


async def test_busy_reload_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真锁场景：用户操作持锁时，watcher 在防抖预算内拿到 busy 并顺延，
    锁释放后由后续 tick 自动重试成功（参照 test_development_plugins 的
    bounded_operation_wait 写法，但走完整 watcher 循环）。"""
    lifecycle = _FakeLifecycleService(use_real_lock=True)
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    # 主测试任务先持锁，模拟另一个正在进行的插件操作。
    release = asyncio.Event()
    entered = asyncio.Event()

    async def hold_lock() -> None:
        async with plugin_operation_lock.hold():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold_lock())
    await asyncio.wait_for(entered.wait(), 3)
    try:
        assert service.start() is True
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "VALUE = 5  # changed\n", encoding="utf-8"
        )
        # 防抖截止先到（第一次尝试开始），随后 pending 被推回未来——这是
        # "真的走到过 PluginOperationBusy"的证据：无界等锁的旧实现会让
        # deadline 停在过去不动。
        assert await _wait_for(
            lambda: service._pending.get("demo", float("inf")) <= time.monotonic()
        )
        assert await _wait_for(
            lambda: service._pending.get("demo", 0.0) > time.monotonic()
        )
        assert lifecycle.reload_calls == []
        assert service.is_running

        release.set()
        await asyncio.wait_for(holder, 3)
        # 顺延的那一轮由 watcher 自己重试成功，不需要外部再推一次。
        assert await _wait_for(lambda: lifecycle.reload_calls == ["demo"])
    finally:
        release.set()
        try:
            await asyncio.wait_for(holder, 1)
        except asyncio.TimeoutError:  # pragma: no cover - 已完成的快路径
            pass
        await _stop(service)


async def test_stop_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _make_service(tmp_path, _FakeLifecycleService(), monkeypatch)
    await _stop(service)  # 未启动也能停
    assert service.start() is True
    await _stop(service)
    await _stop(service)
    assert not service.is_running


async def test_stop_timeout_keeps_task_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = asyncio.Event()

    class _HangingLifecycle:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def reload_plugin(
            self, plugin_id: str, *, only_if_running: bool = False
        ) -> dict[str, object]:
            # 模拟真实路径里被操作锁屏蔽取消的 in-flight reload：cancel
            # 打不断它（吞掉后继续等 release），只有 release 置位才返回。
            self.started.set()
            while not release.is_set():
                try:
                    await asyncio.shield(release.wait())
                except asyncio.CancelledError:
                    continue
            return {"success": True, "plugin_id": plugin_id}

    hang = _HangingLifecycle()
    service = _make_service(tmp_path, hang, monkeypatch)
    source_dir = tmp_path / "demo"
    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
        assert await _wait_for(lambda: hang.started.is_set())

        # 超时停不掉：必须保留 task 引用，否则下面的 start() 看不到存活中的
        # watcher 而另起一个，两个 watcher 会并发写 _signatures。
        await service.stop(timeout=0.01)
        assert service.is_running is True

        # orphan 仍在跑时 start() 应短路（返回 True 且不新建 task）。
        assert service.start() is True
        assert service.is_running is True
    finally:
        release.set()
        await _stop(service)
    assert not service.is_running


async def test_failed_event_carries_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FailingLifecycle:
        async def reload_plugin(
            self, plugin_id: str, *, only_if_running: bool = False
        ) -> dict[str, object]:
            raise ServerDomainError(
                code="PLUGIN_START_FAILED", message="boom", status_code=500
            )

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        module, "emit_lifecycle_event", lambda payload: events.append(payload)
    )
    service = _make_service(tmp_path, _FailingLifecycle(), monkeypatch)
    source_dir = tmp_path / "demo"
    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
        assert await _wait_for(
            lambda: any(e["type"] == "plugin_hot_reload_failed" for e in events)
        )
    finally:
        await _stop(service)
    failed = next(e for e in events if e["type"] == "plugin_hot_reload_failed")
    # reason 让前端不用去日志里对时间戳就能区分失败类型。
    assert failed["reason"] == "PLUGIN_START_FAILED"


async def test_restart_rebaselines_without_spurious_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    assert await _wait_for(lambda: bool(service._signatures))
    await _stop(service)

    # 停机窗口内的变更：重启后只重建基线，不应触发 reload。
    (source_dir / "__init__.py").write_text(
        "VALUE = 6  # changed while down\n", encoding="utf-8"
    )
    assert service.start() is True
    try:
        await asyncio.sleep(0.4)
        assert lifecycle.reload_calls == []
        assert await _wait_for(lambda: bool(service._signatures))
    finally:
        await _stop(service)


def test_signature_tracks_python_and_manifest_only(tmp_path: Path) -> None:
    source_dir = tmp_path / "demo"
    _write_plugin_source(source_dir)
    (source_dir / "asset.png").write_bytes(b"png")
    cache_dir = source_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "demo.cpython-311.pyc").write_bytes(b"pyc")

    signature = module._signature_sync(source_dir)
    assert set(signature.keys()) == {"plugin.toml", "__init__.py"}


def test_preflight_compile_sync(tmp_path: Path) -> None:
    source_dir = tmp_path / "good"
    _write_plugin_source(source_dir)
    assert module._preflight_compile_sync(source_dir) is None

    broken = tmp_path / "broken"
    _write_plugin_source(broken)
    (broken / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    error = module._preflight_compile_sync(broken)
    assert error is not None and "bad.py" in error

    bad_manifest = tmp_path / "bad_manifest"
    _write_plugin_source(bad_manifest)
    (bad_manifest / "plugin.toml").write_text("[plugin\n", encoding="utf-8")
    error = module._preflight_compile_sync(bad_manifest)
    assert error is not None and "plugin.toml" in error
