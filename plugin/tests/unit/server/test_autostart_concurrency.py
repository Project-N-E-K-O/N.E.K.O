"""自启动按波次持锁并发，依赖方随后串行，波次之间让排队操作执行。

串行自启动 12 个插件实测 14871ms，占冷启动的 79%（``service.start_plugin`` 的
sum/max≈10.4 → 零重叠）。插件子进程是**独立进程**，不受 GIL 约束，16 逻辑核实测
12 个并发冷启动整组 1430ms（串行等效 8007ms，5.6x），单个只慢 1.92x。

每波锁内 ``asyncio.gather`` 跑未加装饰的 ``_start_plugin_under_lock``；串行使用公开入口。
这里钉住四件在后续重构里很容易被悄悄丢掉、而丢掉之后**不是变慢而是挂死或静默失败**
的事：

1. ``_start_plugin_under_lock`` 体内不得再取锁。批次是在 gather 的**子任务**里跑它的，而
   ``serialized_plugin_operation`` 的重入判定按 ``asyncio.current_task()`` 认
   （``operation_lock.py`` 的 ``_OPERATION_OWNER``）：子任务里 current_task 是子任务
   自己、owner 是父任务，判定必然失败 → 去抢一把已被父任务持有的锁 → 整批挂死。
   这也是 ``reload_all_plugins`` 当年用 gather 一点并发都买不到的原因。
2. 声明了依赖的插件必须排在并发组**之后**。依赖检查
   （``core/dependency.py:_find_plugins_by_entry``）读的是 ``state.event_handlers``，
   要求被依赖方已经启动并注册完 handler；拓扑序只在串行时天然成立。
3. 单个插件失败不得拖垮同批其他插件——与原来 for 循环的语义一致。
4. 并发上限设成 1 时必须完全退回串行（回退开关）。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from plugin.server.application.plugins import lifecycle_service as module
from plugin.server.application.plugins import operation_lock as locks
from plugin.server.application.plugins import registry_service as registry_module

pytestmark = pytest.mark.plugin_unit


# ── 1. 死锁守卫：锁内跑的那个函数不得再取锁 ─────────────────────────────


def _lock_decorated_names() -> set[str]:
    """模块里所有被 @serialized_plugin_operation 装饰的可调用名。

    用 ``__wrapped__`` 认，而不是硬编码名单：functools.wraps 会设它，而新加一个
    受装饰的方法时这个集合会自动跟上——硬编码名单会在最该报警的时候漏掉。
    """
    names: set[str] = set()
    for owner in (module, module.PluginLifecycleService):
        for name, value in vars(owner).items():
            if callable(value) and getattr(value, "__wrapped__", None) is not None:
                names.add(name)
    return names


def test_start_plugin_under_lock_never_reacquires_the_lock() -> None:
    """变异：在 _start_plugin_under_lock 里加一句 self.stop_plugin(...) 或 hold()。

    批次在 gather 的子任务里跑它，重入判定认的是父任务 → 子任务会去抢一把已被
    持有的锁 → 整个启动挂死。这个失败不会在单插件测试里出现（那时没有父任务持锁），
    只在真实自启动批次里挂，所以必须有静态守卫。
    """
    source = Path(inspect.getfile(module.PluginLifecycleService)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "PluginLifecycleService"
    )
    inner = next(
        node
        for node in cls.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "_start_plugin_under_lock"
    )

    decorated = _lock_decorated_names()
    assert "start_plugin" in decorated, "前提没成立：start_plugin 应该是被锁装饰的那个"

    offenders: list[str] = []
    for node in ast.walk(inner):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in decorated:
                offenders.append(f"{func.attr}() @line {node.lineno}")
            if func.attr == "hold":
                offenders.append(f"hold() @line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id in decorated:
            offenders.append(f"{func.id}() @line {node.lineno}")

    assert not offenders, (
        "_start_plugin_under_lock 体内取锁了：" + ", ".join(offenders) +
        "——批次在 gather 子任务里跑它，重入判定按 current_task 认，子任务会去抢"
        "父任务已持有的锁，整个自启动批次挂死。要串行化就调 _start_plugin_under_lock，"
        "别调被装饰的那个。"
    )


def test_start_plugin_still_owns_the_lock_for_its_other_callers() -> None:
    """另一侧的守卫：抽函数体不能把公开入口的锁一起抽走。

    start_plugin 有 8 个生产调用方（HTTP 路由、reload、安装/卸载/换源事务、
    development_service），它们靠装饰器拿锁。
    """
    assert getattr(module.PluginLifecycleService.start_plugin, "__wrapped__", None) is not None, (
        "start_plugin 不再被 @serialized_plugin_operation 装饰——其余调用方就此失去互斥"
    )
    assert getattr(module.PluginLifecycleService._start_plugin_under_lock, "__wrapped__", None) is None, (
        "_start_plugin_under_lock 被装饰了：批次会在子任务里抢父任务的锁，挂死"
    )
    # 公开签名一字不变（8 个调用方按名字/关键字传参）。结构化比对而不是比字符串：
    # 本模块有 from __future__ import annotations，str(signature) 会把注解渲染成
    # 带引号的形式，逐字比对会在与本次改动无关的重构里假报警。
    signature = inspect.signature(module.PluginLifecycleService.start_plugin)
    assert [
        (p.name, p.kind.name, p.default) for p in signature.parameters.values()
    ] == [
        ("self", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("plugin_id", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("restore_state", "POSITIONAL_OR_KEYWORD", False),
        ("refresh_registry", "KEYWORD_ONLY", True),
        ("persist_user_intent", "KEYWORD_ONLY", False),
        ("start_deadline", "KEYWORD_ONLY", None),
    ], f"start_plugin 的公开签名变了：{signature}"


# ── 批次行为 ────────────────────────────────────────────────────────────


class _LockHoldCounter:
    """Count entries only; ownership and exclusion use the real-lock test below."""

    def __init__(self) -> None:
        self.entered = 0

    def hold(self):
        counter = self

        class _Held:
            async def __aenter__(self):
                counter.entered += 1
                return self

            async def __aexit__(self, *exc):
                return False

        return _Held()


def _service_with_recorder(monkeypatch, *, delay=0.0, fail_ids=()):
    """造一个只记录并发度的 service；_start_plugin_under_lock 被换成替身。"""
    service = module.PluginLifecycleService()
    state = {"live": 0, "peak": 0, "order": [], "finished": []}

    async def _fake_inner(plugin_id, restore_state=False, **kwargs):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        state["order"].append(("start", plugin_id))
        try:
            if delay:
                await asyncio.sleep(delay)
            if plugin_id in fail_ids:
                raise RuntimeError(f"boom {plugin_id}")
        finally:
            state["live"] -= 1
            state["finished"].append(plugin_id)
            state["order"].append(("end", plugin_id))

    monkeypatch.setattr(service, "_start_plugin_under_lock", _fake_inner)
    counter = _LockHoldCounter()
    monkeypatch.setattr(module, "plugin_operation_lock", counter)
    monkeypatch.setattr(locks, "plugin_operation_lock", counter)
    return service, state, counter


@pytest.mark.asyncio
async def test_batch_overlaps_one_independent_wave(monkeypatch) -> None:
    """变异：把 hold() 挪进 _start_one，或把 gather 换回 for 循环。"""
    service, state, counter = _service_with_recorder(monkeypatch, delay=0.05)

    result = await service.start_plugins_batch(["a", "b", "c", "d"], concurrency=4)

    assert counter.entered == 1, (
        f"同一波应该只持锁一次，实际 {counter.entered} 次——每个插件各自持锁就退回串行了"
    )
    assert state["peak"] > 1, "并发组没有真的重叠（peak=%d）" % state["peak"]
    assert sorted(result["started"]) == ["a", "b", "c", "d"]
    assert result["failed"] == []


@pytest.mark.asyncio
async def test_concurrency_one_is_a_full_rollback_to_serial(monkeypatch) -> None:
    """回退开关：上限设 1 必须完全串行。线上出问题时的逃生门。"""
    service, state, counter = _service_with_recorder(monkeypatch, delay=0.02)

    await service.start_plugins_batch(["a", "b", "c"], concurrency=1)

    assert state["peak"] == 1, f"concurrency=1 却出现了重叠（peak={state['peak']}）"
    assert [p for _, p in state["order"] if _ == "start"] == ["a", "b", "c"]
    assert counter.entered == 3


@pytest.mark.asyncio
async def test_concurrency_limit_is_respected(monkeypatch) -> None:
    """上限是承重的一部分：PLUGIN_STARTUP_TIMEOUT 是**每插件**的，弱机上过多并发
    会把单个子进程启动拖到撞超时，把"慢"变成"启动失败"。"""
    service, state, _ = _service_with_recorder(monkeypatch, delay=0.05)

    await service.start_plugins_batch([f"p{i}" for i in range(8)], concurrency=3)

    assert state["peak"] <= 3, f"并发超过上限：peak={state['peak']} > 3"
    assert state["peak"] > 1, "上限 3 却完全没并发"


@pytest.mark.asyncio
async def test_ordered_group_starts_only_after_the_whole_independent_group(monkeypatch) -> None:
    """变异：把两个 for/gather 段落调个顺序，或让它们一起进 gather。

    依赖检查读 state.event_handlers，要求被依赖方**已注册完 handler**。并发组没跑完
    就放依赖方进去，依赖方会以 PLUGIN_DEPENDENCY_CHECK_FAILED 失败——那是并发化
    引入的新失败，不是既有的。
    """
    service, state, _ = _service_with_recorder(monkeypatch, delay=0.03)

    await service.start_plugins_batch(
        ["indep1", "indep2", "indep3"],
        ["dep1", "dep2"],
        concurrency=3,
    )

    finished = state["finished"]
    last_independent = max(finished.index(p) for p in ("indep1", "indep2", "indep3"))
    first_dependent = min(finished.index(p) for p in ("dep1", "dep2"))
    assert last_independent < first_dependent, (
        f"依赖方与它的提供者重叠了：并发组最后完成于 {last_independent}，"
        f"按序组最早开始于 {first_dependent}（完成序列 {finished}）"
    )
    # 按序组内部保持传入的拓扑序
    dependent_starts = [p for kind, p in state["order"] if kind == "start" and p in ("dep1", "dep2")]
    assert dependent_starts == ["dep1", "dep2"]


@pytest.mark.asyncio
async def test_one_failure_does_not_take_down_the_batch(monkeypatch) -> None:
    """与原来的 for 循环语义一致：单个插件起不来只记 error。"""
    service, state, _ = _service_with_recorder(
        monkeypatch, delay=0.02, fail_ids={"bad1", "bad2"}
    )

    result = await service.start_plugins_batch(
        ["ok1", "bad1", "ok2", "bad2", "ok3"], concurrency=5
    )

    assert sorted(result["started"]) == ["ok1", "ok2", "ok3"]
    assert sorted(result["failed"]) == ["bad1", "bad2"]
    assert len(state["finished"]) == 5, "有插件根本没被尝试"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "independent, ordered, concurrency",
    [
        (["bad", "good"], [], 1),
        (["bad"], ["good"], 4),
        ([], ["bad", "good"], 4),
        (["provider1", "provider2"], ["bad", "good"], 2),
    ],
)
async def test_serial_paths_isolate_start_failures(monkeypatch, independent, ordered, concurrency):
    service, state, _ = _service_with_recorder(monkeypatch, fail_ids={"bad"})
    result = await service.start_plugins_batch(independent, ordered, concurrency=concurrency)
    assert result["failed"] == ["bad"]
    assert "good" in result["started"]
    assert state["finished"] == independent + ordered


@pytest.mark.asyncio
async def test_cancelled_batch_keeps_the_real_lock_until_starts_finish(monkeypatch, tmp_path):
    from plugin.server.application.plugins import operation_lock as locks

    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(locks, "_reload_install_source_manager_sync", lambda: None)
    entered = asyncio.Event()
    release = asyncio.Event()
    attempted = asyncio.Event()
    outsider_entered = asyncio.Event()
    completed = []
    service = module.PluginLifecycleService()

    async def start(plugin_id, **_kwargs):
        if plugin_id == "b":
            entered.set()
        await release.wait()
        completed.append(plugin_id)

    monkeypatch.setattr(service, "_start_plugin_under_lock", start)

    async def outsider():
        attempted.set()
        async with locks.plugin_operation_lock.hold():
            outsider_entered.set()

    batch = asyncio.create_task(service.start_plugins_batch(["a", "b"], concurrency=2))
    competing = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        competing = asyncio.create_task(outsider())
        await attempted.wait()
        batch.cancel()
        await asyncio.sleep(0)
        batch.cancel()
        await asyncio.sleep(0)
        assert not batch.done()
        assert not outsider_entered.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(batch, 2)
        await asyncio.wait_for(competing, 2)
        assert sorted(completed) == ["a", "b"]
        assert outsider_entered.is_set()
    finally:
        release.set()
        await asyncio.gather(batch, *([competing] if competing else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency,dependent", [(1, False), (2, False), (3, True)])
@pytest.mark.parametrize("cancel_pending", [False, True])
async def test_queued_mutation_runs_before_the_next_start_group(
    monkeypatch, tmp_path, concurrency, dependent, cancel_pending
):
    """Management requests must get the lock between waves or serial starts."""
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(locks, "_reload_install_source_manager_sync", lambda: None)
    service = module.PluginLifecycleService()
    first_size = 1 if dependent else concurrency
    plugin_ids = [f"p{i}" for i in range(first_size + 1)]
    first_started = asyncio.Event()
    finish_first = asyncio.Event()
    attempted = asyncio.Event()
    mutation_entered = asyncio.Event()
    finish_mutation = asyncio.Event()
    transcript = []

    async def start(plugin_id, restore_state=False, **_kwargs):
        transcript.append(plugin_id)
        if plugin_id in plugin_ids[:first_size]:
            if len(transcript) == first_size:
                first_started.set()
            await finish_first.wait()

    async def mutation():
        attempted.set()
        with locks.bounded_operation_wait(2):
            async with locks.plugin_operation_lock.hold():
                transcript.append("mutation")
                mutation_entered.set()
                await finish_mutation.wait()

    monkeypatch.setattr(service, "_start_plugin_under_lock", start)
    batch = asyncio.create_task(service.start_plugins_batch(
        [] if dependent else plugin_ids,
        plugin_ids if dependent else [],
        concurrency=concurrency,
    ))
    competing = None
    try:
        await asyncio.wait_for(first_started.wait(), 2)
        competing = asyncio.create_task(mutation())
        await attempted.wait()
        finish_first.set()
        await asyncio.wait_for(mutation_entered.wait(), 2)
        assert transcript == plugin_ids[:first_size] + ["mutation"]
        if cancel_pending:
            batch.cancel()
            await asyncio.sleep(0)
            batch.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(batch, 2)
            assert transcript == plugin_ids[:first_size] + ["mutation"]
        finish_mutation.set()
        if cancel_pending:
            await asyncio.wait_for(competing, 2)
        else:
            await asyncio.wait_for(asyncio.gather(batch, competing), 2)
            assert transcript == plugin_ids[:first_size] + ["mutation", plugin_ids[-1]]
    finally:
        finish_first.set()
        finish_mutation.set()
        await asyncio.gather(batch, *([competing] if competing else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_cancelling_a_batch_waiting_for_the_lock_starts_nothing(
    monkeypatch, tmp_path, concurrency
):
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(locks, "_reload_install_source_manager_sync", lambda: None)
    service = module.PluginLifecycleService()
    started = []

    async def start(plugin_id, restore_state=False, **_kwargs):
        started.append(plugin_id)

    monkeypatch.setattr(service, "_start_plugin_under_lock", start)
    async with locks.plugin_operation_lock.hold():
        batch = asyncio.create_task(service.start_plugins_batch(["a", "b"], concurrency=concurrency))
        await asyncio.sleep(0)
        assert not batch.done()
        batch.cancel()
        await asyncio.sleep(0)
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(batch, 2)
        assert started == []

    async with locks.plugin_operation_lock.hold():
        assert started == []


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop_that_still_does_not_explode(monkeypatch) -> None:
    service, state, counter = _service_with_recorder(monkeypatch)

    result = await service.start_plugins_batch([], [])

    assert result == {"started": [], "failed": []}
    assert state["peak"] == 0
    assert counter.entered == 0


async def test_internal_start_requires_lock_ownership():
    service = module.PluginLifecycleService()
    with pytest.raises(RuntimeError, match="requires the operation lock"):
        await service._start_plugin_under_lock("unstarted")


async def test_borrowed_scope_cannot_be_reused_after_release(monkeypatch, tmp_path):
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(locks, "_reload_install_source_manager_sync", lambda: None)
    async with locks.plugin_operation_lock.hold() as scope:
        scope.require_active()
    service = module.PluginLifecycleService()
    with pytest.raises(RuntimeError, match="active operation scope"):
        await service._start_plugin_under_lock("unstarted", operation_scope=scope)


@pytest.mark.asyncio
async def test_cancelling_the_batch_never_cuts_a_start_in_half(monkeypatch) -> None:
    """变异：把 _start_one 里的 asyncio.shield 去掉，直接 await。

    ``_start_plugin_under_lock`` 的 except 分支只接 Exception 类族（ServerDomainError /
    HTTPException / PluginError / ImportError / RUNTIME_ERRORS），而 CancelledError
    是 BaseException，一个都接不住。从中间掐断就会留下一个已经 spawn、却还没走到
    ``_register_or_replace_host_sync`` 的 host 进程——落在 host 快照之后，成为没人
    停止的孤儿。这也是 ``serialized_plugin_operation`` 要用 shield 的同一个理由。
    """
    completed: list[str] = []
    service = module.PluginLifecycleService()

    async def _slow_inner(plugin_id, restore_state=False, **kwargs):
        await asyncio.sleep(0.25)
        completed.append(plugin_id)  # 只有真的跑完才会到这里

    monkeypatch.setattr(service, "_start_plugin_under_lock", _slow_inner)
    counter = _LockHoldCounter()
    monkeypatch.setattr(module, "plugin_operation_lock", counter)

    batch = asyncio.create_task(
        service.start_plugins_batch(["a", "b", "c"], concurrency=3)
    )
    await asyncio.sleep(0.05)  # 三个都已经进到 sleep 里
    batch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await batch

    assert sorted(completed) == ["a", "b", "c"], (
        f"批次被取消后仍有启动没落地：{completed}——那些插件的 host 可能已经 spawn "
        "却没注册，成为没人停止的孤儿进程"
    )


@pytest.mark.asyncio
async def test_a_failing_sibling_does_not_cancel_the_rest(monkeypatch) -> None:
    """A normal startup failure must not stop the batch from awaiting siblings."""
    service, state, _ = _service_with_recorder(monkeypatch, delay=0.08, fail_ids={"bad"})

    result = await service.start_plugins_batch(
        ["bad", "ok1", "ok2", "ok3"], concurrency=4
    )

    assert result["failed"] == ["bad"]
    assert sorted(result["started"]) == ["ok1", "ok2", "ok3"], (
        f"一个插件失败把兄弟一起带走了：{result}"
    )
    assert len(state["finished"]) == 4


# ── 分组：谁可以并发 ────────────────────────────────────────────────────


def test_groups_partition_preserves_topological_order(monkeypatch) -> None:
    """变异：让分组打乱顺序，或把声明依赖的插件放进并发组。

    两组合起来必须还是 _get_autostart_plugin_ids_sync() 的同一个序列（只是内部
    分组），否则"提供者在依赖方之前"这个既有保证就被破坏了。
    """
    ordered = ["prov1", "prov2", "dep1", "prov3", "dep2"]
    monkeypatch.setattr(registry_module, "_get_autostart_plugin_ids_sync", lambda: list(ordered))
    monkeypatch.setattr(
        registry_module,
        "_dependency_declaring_runtime_plugin_ids",
        lambda: {"dep1", "dep2"},
    )

    independent, dependent = registry_module._get_autostart_plugin_groups_sync()

    assert independent == ["prov1", "prov2", "prov3"], independent
    assert dependent == ["dep1", "dep2"], dependent
    # 集合不变、各组内部相对顺序不变
    assert sorted(independent + dependent) == sorted(ordered)
    assert independent == [p for p in ordered if p in set(independent)]
    assert dependent == [p for p in ordered if p in set(dependent)]


def test_no_dependency_declarations_means_everything_is_concurrent(monkeypatch) -> None:
    """内置 12 个插件都不声明依赖 —— 这是常态，必须走全并发。"""
    ordered = [f"p{i}" for i in range(12)]
    monkeypatch.setattr(registry_module, "_get_autostart_plugin_ids_sync", lambda: list(ordered))
    monkeypatch.setattr(
        registry_module, "_dependency_declaring_runtime_plugin_ids", lambda: set()
    )

    independent, dependent = registry_module._get_autostart_plugin_groups_sync()

    assert independent == ordered
    assert dependent == []


def test_single_plugin_skips_the_extra_context_scan(monkeypatch) -> None:
    """0 或 1 个插件时并发没有意义，也不该多跑一次 context 收集（那步实测 ~36ms）。"""
    calls = []
    monkeypatch.setattr(registry_module, "_get_autostart_plugin_ids_sync", lambda: ["only"])
    monkeypatch.setattr(
        registry_module,
        "_dependency_declaring_runtime_plugin_ids",
        lambda: calls.append(1) or {"only"},
    )

    independent, dependent = registry_module._get_autostart_plugin_groups_sync()

    assert (independent, dependent) == (["only"], [])
    assert calls == [], "单插件批次不该付 context 收集的钱"


def test_autostart_ids_are_already_topologically_ordered() -> None:
    """钉住前提：分组之所以能只按"是否声明依赖"切，是因为拿到的序列已经是拓扑序。

    变异：把 _get_autostart_plugin_ids_sync 末尾的 _build_ordered_plugin_ids_sync
    换成 sorted(candidates) —— 那样"提供者在依赖方之前"就不再成立，而并发组内
    的顺序也就不再有任何保证。
    """
    source = Path(inspect.getfile(registry_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_get_autostart_plugin_ids_sync"
    )
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "找不到返回语句"
    last = ast.unparse(returns[-1].value)
    assert "_build_ordered_plugin_ids_sync" in last, (
        f"自启动列表不再是拓扑序了（return {last}）——并发分组的前提就此失效："
        "依赖检查要求提供者已注册完 handler，只有拓扑序能保证它排在前面"
    )


def test_empty_autostart_selection_does_not_read_plugin_configuration(monkeypatch):
    def unexpected_read(*_args):
        pytest.fail("An empty selection must not read plugin configurations")

    monkeypatch.setattr(registry_module, "_collect_plugin_contexts_from_roots_sync", unexpected_read)
    assert registry_module._build_ordered_plugin_ids_sync(set()) == []


@pytest.mark.asyncio
async def test_renamed_plugin_keeps_its_own_dependency_declarations(monkeypatch, tmp_path):
    """Use real discovery: duplicate manifest IDs may have different dependencies."""
    root = tmp_path / "plugins"
    for directory, declared_id, dependencies in (
        ("a_provider", "a_provider", []),
        ("demo", "demo", []),
        ("demo_1", "demo", ["a_provider"]),
    ):
        folder = root / directory
        folder.mkdir(parents=True)
        dependency_line = f"dependencies = {dependencies!r}\n" if dependencies else ""
        (folder / "plugin.toml").write_text(
            f"[plugin]\nid = {declared_id!r}\nname = {directory!r}\n"
            f"type = 'plugin'\nentry = '{directory}:Plugin'\nversion = '1.0.0'\n"
            f"{dependency_line}[plugin_runtime]\nenabled = true\nauto_start = true\n",
            encoding="utf-8",
        )

    # Discovery's ID dependency check reads the registered provider, just as a
    # refresh with an existing registry does. It must preserve both demo sources.
    monkeypatch.setattr(registry_module.state, "plugins", {
        "a_provider": {
            "id": "a_provider",
            "version": "1.0.0",
            "runtime_enabled": True,
            "config_path": str(root / "a_provider" / "plugin.toml"),
        },
    })
    monkeypatch.setattr(registry_module.state, "plugin_hosts", {})
    monkeypatch.setattr(registry_module, "PLUGIN_CONFIG_ROOTS", (root,))
    monkeypatch.setattr(registry_module, "is_autostart_approved", lambda _pid: True)
    result = await registry_module.PluginRegistryService().refresh_registry()
    assert result["success"], result
    assert set(registry_module.state.plugins) == {"a_provider", "demo", "demo_1"}
    independent, dependent = await registry_module.PluginRegistryService().list_autostart_plugin_groups()
    assert independent == ["a_provider", "demo"]
    assert dependent == ["demo_1"]


def test_dependency_classification_accepts_legacy_metadata_without_dependencies(monkeypatch):
    monkeypatch.setattr(registry_module, "_get_registered_plugin_snapshot_sync", lambda: {
        "legacy": {}, "empty": {"dependencies": []},
        "renamed": {"dependencies": [{"id": "provider"}]},
    })
    assert registry_module._dependency_declaring_runtime_plugin_ids() == {"renamed"}


@pytest.mark.asyncio
async def test_failed_wave_lock_does_not_abort_later_plugins(monkeypatch):
    service, state, counter = _service_with_recorder(monkeypatch)
    original = counter.hold
    attempts = 0

    def hold():
        nonlocal attempts
        attempts += 1
        if attempts != 1:
            return original()

        class BrokenLock:
            async def __aenter__(self):
                raise PermissionError("lock unavailable")

            async def __aexit__(self, *args):
                return False

        return BrokenLock()

    monkeypatch.setattr(counter, "hold", hold)
    result = await service.start_plugins_batch(["a", "b", "c", "d"], ["dependent"], concurrency=2)
    assert result == {"started": ["c", "d", "dependent"], "failed": ["a", "b"]}
    assert state["finished"] == ["c", "d", "dependent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_batch_deduplicates_ids_across_both_groups(monkeypatch, concurrency):
    service, state, _ = _service_with_recorder(monkeypatch)
    result = await service.start_plugins_batch(["a", "a", "b"], ["b", "c", "c"], concurrency=concurrency)
    assert result == {"started": ["a", "b", "c"], "failed": []}
    assert state["finished"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_independently_cancelled_start_is_reported_as_failed(monkeypatch):
    service, _, _ = _service_with_recorder(monkeypatch)

    async def start(plugin_id, **kwargs):
        if plugin_id == "cancelled":
            raise asyncio.CancelledError

    monkeypatch.setattr(service, "_start_plugin_under_lock", start)
    result = await service.start_plugins_batch(["cancelled", "ok"], concurrency=2)
    assert result == {"started": ["ok"], "failed": ["cancelled"]}
