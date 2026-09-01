"""Every wait in the plugin server needs an upper bound or a reason not to have one.

The failures these guard are not crashes — they are a UI that spins until the
front end gives up at 30 s, and in the lock's case an operation that lands
anyway after the user was told it failed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.plugin_unit


# ── 抢锁的截止期 ───────────────────────────────────────────────────────


def test_bounded_wait_sets_and_clears_the_deadline() -> None:
    """Outside the block there must be no deadline at all.

    Leaking one would put every later background operation — autostart
    reconcile, install transactions — under a budget meant for a human who is
    watching a spinner.
    """
    from plugin.server.application.plugins import operation_lock as module

    assert module._OPERATION_WAIT_BUDGET.get() is None
    with module.bounded_operation_wait(5.0):
        assert module._OPERATION_WAIT_BUDGET.get() == 5.0
    assert module._OPERATION_WAIT_BUDGET.get() is None


@pytest.mark.asyncio
async def test_an_expired_deadline_refuses_instead_of_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real async path, executor hop included.

    That hop is the whole point: the acquire runs via
    ``loop.run_in_executor``, which — unlike ``asyncio.to_thread`` — does NOT
    propagate contextvars. Setting the deadline in the request context and
    reading it inside the worker gets ``None``, so the budget silently does
    nothing. The first version of this test set the deadline *inside* the
    worker thread and passed while production was dead (Greptile caught it).

    Mutation: read the deadline from the ContextVar inside
    ``_acquire_file_lock_sync`` instead of taking it as an argument.
    """
    from plugin.server.application.plugins import operation_lock as module

    def _always_contended(handle):
        raise OSError(module.errno.EACCES, "held")

    monkeypatch.setattr(module, "_lock_file_once", _always_contended)
    monkeypatch.setattr(module, "_is_file_lock_contention", lambda exc: True)
    monkeypatch.setattr(module, "_FILE_LOCK_RETRY_INTERVAL_SECONDS", 0.01)

    started = time.monotonic()
    with module.bounded_operation_wait(0.15):
        with pytest.raises(module.PluginOperationBusy):
            await asyncio.wait_for(
                module._acquire_file_lock_cancellation_safe(), timeout=8.0
            )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"截止期没穿过 executor，等了 {elapsed:.1f}s"


def test_run_in_executor_really_does_drop_contextvars() -> None:
    """Pins the reason the deadline is passed as an argument, not inherited.

    If a later refactor swaps the executor for ``asyncio.to_thread`` this stops
    being true, and whoever reads it should know the argument is then optional
    rather than load-bearing.
    """
    import contextvars

    probe: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "probe", default=None
    )

    async def _check() -> tuple[object, object]:
        probe.set("SET")
        loop = asyncio.get_event_loop()
        via_executor = await loop.run_in_executor(None, probe.get)
        via_to_thread = await asyncio.to_thread(probe.get)
        return via_executor, via_to_thread

    via_executor, via_to_thread = asyncio.run(_check())

    assert via_executor is None, "run_in_executor 开始传播上下文了——注释要更新"
    assert via_to_thread == "SET"


def test_without_a_deadline_it_still_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background callers must keep the old unbounded behaviour.

    Guards the half of the change that is *not* supposed to happen: a global
    timeout here would make autostart reconcile fail under normal contention.
    """
    from plugin.server.application.plugins import operation_lock as module

    attempts: list[int] = []

    def _contended_then_ok(handle):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError(module.errno.EACCES, "held")

    monkeypatch.setattr(module, "_lock_file_once", _contended_then_ok)
    monkeypatch.setattr(module, "_is_file_lock_contention", lambda exc: True)
    monkeypatch.setattr(module, "_FILE_LOCK_RETRY_INTERVAL_SECONDS", 0.01)

    handle = module._acquire_file_lock_sync()
    assert len(attempts) == 3, "无截止期时应该一直重试到拿到锁"
    handle.close()


# ── 没有工具就不该发那次 HTTP ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_clearing_tools_still_asks_when_local_tracking_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty local table does not mean the remote has nothing.

    After a plugin-server restart ``_plugin_tools`` is empty while main_server
    can still hold tools tagged with this plugin's source. Skipping the request
    on an empty table — which an earlier version of this change did, to dodge
    the connect cost — leaves ghost tools the model can still call. Local
    bookkeeping is not authoritative for remote state.

    The cost is handled by a shorter timeout instead, because this await sits
    inside stop_plugin's cross-process lock.

    Mutation: reinstate the ``if not owned: return`` early exit.
    """
    from plugin.server.messaging import llm_tool_registry as module

    seen: list[object] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"ok": True}

    class _Client:
        async def post(self, url, **kwargs):
            seen.append(kwargs.get("timeout"))
            return _Resp()

    monkeypatch.setattr(module, "_get_http_client", lambda: _Client())

    await module.clear_plugin_tools("plugin-with-no-local-record")

    assert len(seen) == 1, "本地表为空就不发了——重启后会留下幽灵工具"
    timeout = seen[0]
    assert timeout is not None, "用了默认超时——这一步在锁里面，连不上要等满 2s"
    assert getattr(timeout, "connect", None) == 0.3


# ── reload-all 的总预算 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_all_stops_at_its_budget_and_says_which_were_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stops cannot run concurrently, so the cost grows with plugin count.

    Each ``stop_plugin`` takes the cross-process lock on its own, and the lock's
    reentrancy is keyed on the asyncio Task, so gathering them buys nothing —
    N plugins is N serial lock acquisitions while the front end waits.

    The budget cuts it off. What matters as much as stopping is *reporting*:
    a plugin that was never attempted must not simply vanish from the result,
    or the caller sees a short list with nothing explaining the gap.

    Mutation: drop the deadline check, or drop the skipped-list reporting.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    plugin_ids = [f"p{i}" for i in range(6)]
    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: list(plugin_ids))
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.25)

    service = module.PluginLifecycleService()
    stopped: list[str] = []

    async def _slow_stop(plugin_id: str, *, shutdown_timeout=None):
        stopped.append(plugin_id)
        await asyncio.sleep(0.12)
        return module._ReloadOutcome(plugin_id=plugin_id, success=False, error="x")

    monkeypatch.setattr(service, "_safe_stop_for_reload", _slow_stop)

    result = await service.reload_all_plugins()

    assert len(stopped) < len(plugin_ids), "预算没有截断，全部都试了一遍"
    reported = {entry["plugin_id"] for entry in result.get("failed", [])}
    assert reported == set(plugin_ids), (
        f"被跳过的插件从结果里消失了：只报告了 {sorted(reported)}"
    )
    skipped_reasons = [
        entry["error"] for entry in result["failed"] if "budget" in str(entry["error"])
    ]
    assert skipped_reasons, "跳过的原因没写进结果，调用方无从知道为什么少了几个"


@pytest.mark.asyncio
async def test_a_slow_but_successful_stop_is_not_reported_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``asyncio.wait_for`` cannot bound a ``@serialized_plugin_operation``.

    Once that wrapper holds the lock it swallows cancellation and waits for the
    inner call to finish before re-raising, so the outer ``wait_for`` blocks for
    the whole shutdown anyway and *then* reports a stop that actually succeeded
    as a timeout. The plugin drops out of the restart list and is left stopped
    rather than reloaded — a reload that quietly turns into a stop (codex).

    The budget is threaded into the operation instead. This stub carries the
    real decorator, because the bug lives in the decorator's behaviour.

    Mutation: wrap the stop in ``asyncio.wait_for(..., timeout=remaining)``.
    """
    from plugin.server.application.plugins import lifecycle_service as module
    from plugin.server.application.plugins.operation_lock import (
        serialized_plugin_operation,
    )

    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: ["p0"])
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.20)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    service = module.PluginLifecycleService()

    @serialized_plugin_operation
    async def _slow_success(plugin_id: str, *, shutdown_timeout=None):
        # 比剩余预算长，但确实成功了。
        await asyncio.sleep(0.45)
        return {"success": True}

    started: list[str] = []

    async def _start(plugin_id: str, *, start_deadline=None):
        started.append(plugin_id)
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    monkeypatch.setattr(service, "stop_plugin", _slow_success)
    monkeypatch.setattr(service, "_safe_start_for_reload", _start)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    result = await service.reload_all_plugins()

    assert started == ["p0"], (
        f"停成功了却没重启——被当成超时丢掉了：failed={result.get('failed')}"
    )
    assert result["reloaded"] == ["p0"]
    assert not result["failed"], f"成功的停止被记成失败：{result['failed']}"


@pytest.mark.asyncio
async def test_a_stopped_plugin_is_always_started_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping a plugin creates an obligation to start it.

    The two phases must not share one deadline: a slow stop phase would then
    leave nothing for the starts, and every plugin already taken down would be
    reported as "over budget" and left stopped. That turns a reload into a
    silent mass stop — far worse than a reload that answers late, since the
    operation itself succeeded and only the response was slow.

    Mutation: reuse ``stop_deadline`` in the start loop.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: ["p0", "p1"])
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.30)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    service = module.PluginLifecycleService()
    started: list[str] = []

    async def _stop(plugin_id: str, *, shutdown_timeout=None):
        # 停止阶段把预算花光——但两个都停成功了。
        await asyncio.sleep(0.20)
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    async def _start(plugin_id: str, *, start_deadline=None):
        started.append(plugin_id)
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "_safe_start_for_reload", _start)

    result = await service.reload_all_plugins()

    assert started == ["p0", "p1"], (
        f"停掉了却没重新启动，reload 变成了 stop：failed={result.get('failed')}"
    )
    assert result["reloaded"] == ["p0", "p1"]


@pytest.mark.asyncio
async def test_a_start_that_begins_late_gets_a_shortened_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking the deadline before a start does not bound the start itself.

    A plugin whose start begins a moment before the deadline still waits out its
    own startup timeout — ten seconds by default — so reload-all overruns its
    advertised wall clock and keeps mutating plugin state long after the front
    end gave up (codex / CodeRabbit / Greptile). The remaining budget is pushed
    down into the start instead, with a floor so a nearly-spent budget still
    buys a real attempt rather than an instant failure.

    Mutation: drop the clamp, or drop its lower bound.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: ["p0", "p1"])
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.40)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    service = module.PluginLifecycleService()

    async def _stop(plugin_id: str, *, shutdown_timeout=None):
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    seen: list[float | None] = []

    async def _start(plugin_id: str, *, refresh_registry=True, start_deadline=None):
        # 记的是"这一刻还剩多少"，不是原始参数：传的是绝对截止期，所以第二个插件
        # 看到的剩余量必须比第一个小。记参数本身的话，"每个插件各起一份新预算"
        # 这个退化是看不出来的。
        seen.append(None if start_deadline is None else start_deadline - time.monotonic())
        await asyncio.sleep(0.30)
        return {"success": True}

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    await service.reload_all_plugins()

    assert len(seen) == 2, f"两个插件都该被尝试启动：{seen}"
    assert seen[0] is not None and seen[0] <= 0.40, (
        f"第一个启动拿到的上限比整轮预算还大：{seen[0]}"
    )
    assert seen[1] is not None and seen[1] < seen[0], (
        f"第二个启动没有拿到**剩余**预算，而是又一份完整的：{seen}"
    )


def test_the_startup_clamp_never_widens_and_never_reaches_zero() -> None:
    """Both directions of the clamp, on the function production actually calls.

    A spent budget must still buy a short attempt: every plugin reaching the
    start phase was just stopped by us, so refusing to try leaves it down — the
    opposite of a reload. And a plugin that declared a *shorter* timeout of its
    own must not have it widened by a generous budget.

    Mutation: drop the lower bound, or drop the ``min``.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    assert module._clamp_startup_timeout(10.0, 0.0) > 0, (
        "预算见底时算出了 0，等于直接判启动失败"
    )
    assert module._clamp_startup_timeout(10.0, 3.0) == 3.0, "剩余预算没有压住启动上限"
    assert module._clamp_startup_timeout(2.0, 30.0) == 2.0, "插件自己更短的超时被放宽了"
    assert module._clamp_startup_timeout(10.0, None) == 10.0, "没有预算时不该改动配置值"


def test_one_deadline_covers_both_lock_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two lock stages share a budget; they must not each spend a full one.

    ``_CrossLoopLock`` runs first and the file lock second. With each stage
    calling ``_wait_deadline()`` for itself, a request waiting out most of its
    budget behind a same-process operation then starts a *fresh* full budget on
    the file lock, so a nominally 20 s request can still mutate state well past
    the front end's 30 s (codex).

    Mutation: have ``__aenter__`` pass ``None`` to either stage.
    """
    from plugin.server.application.plugins import operation_lock as module

    budget = 0.60
    process_lock_held_for = 0.40

    # 文件锁永远争用：预算怎么分配，全看跨进程那一层还剩多少。
    def _always_contended(handle):
        raise OSError(module.errno.EACCES, "held")

    monkeypatch.setattr(module, "_lock_file_once", _always_contended)
    monkeypatch.setattr(module, "_FILE_LOCK_RETRY_INTERVAL_SECONDS", 0.01)

    async def _scenario() -> tuple[str, float]:
        held = module._HeldPluginOperationLock()
        try:
            await module._PROCESS_LOCK.acquire()

            async def _release_later():
                await asyncio.sleep(process_lock_held_for)
                module._PROCESS_LOCK.release()

            releaser = asyncio.create_task(_release_later())
            started = time.monotonic()
            with module.bounded_operation_wait(budget):
                try:
                    await held.__aenter__()
                except module.PluginOperationBusy:
                    return "busy", time.monotonic() - started
                finally:
                    await releaser
            return "acquired", time.monotonic() - started
        finally:
            # 这个用例直接调 __aenter__，所以没有 __aexit__ 替我们收尾。真让它拿到
            # 了锁（说明被测的行为坏了）就必须在这里还回去：全局那把锁留在持有态
            # 的话，后面每一个要用它的用例都会永远挂住——而挂住不是失败，是没有
            # 结果。这一段本身不是断言，是不让一条红用例把整个会话带走。
            if held._acquired:
                if held._file_lock_handle is not None:
                    module._release_file_lock_sync(held._file_lock_handle)
                module._PROCESS_LOCK.release()

    outcome, elapsed = asyncio.run(_scenario())

    assert outcome == "busy"
    assert elapsed < budget + 0.30, (
        f"两层锁各花了一份预算：等了 {elapsed:.2f}s，预算只有 {budget}s"
    )


def test_an_expired_budget_still_takes_an_uncontended_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Wait zero seconds" is a legitimate answer, and it is not a refusal.

    reload-all hands each stop whatever is left of the round budget, so the
    last one routinely gets ~0 s. Checking the deadline *before* trying meant a
    caller with nothing left was refused while the lock sat there free — a 409
    with no contention anywhere.

    Mutation: move the deadline check back above ``_lock_file_once``.
    """
    from plugin.server.application.plugins import operation_lock as module

    attempts: list[int] = []

    def _free(handle):
        attempts.append(1)

    monkeypatch.setattr(module, "_lock_file_once", _free)

    handle = module._acquire_file_lock_sync(None, time.monotonic() - 5.0)
    try:
        assert attempts == [1], "预算过期就拒绝了，可锁根本没人占"
    finally:
        # 拿锁成功时 handle 会被登记进 _ACTIVE_FILE_LOCK_HANDLE 和
        # _OPEN_FILE_LOCK_HANDLES，只 close 就会把一个已关闭的 handle 留在全局里
        # 给后面的用例踩（CodeRabbit）。
        #
        # 但也不能直接调 _release_file_lock_sync：这个用例把 _lock_file_once 打成了
        # 空操作，文件区间其实从没被锁过，走那条路会在 msvcrt 解锁那步抛
        # PermissionError。所以只做登记那两步，再关。
        with module._FILE_LOCK_HANDLE_GUARD:
            if module._ACTIVE_FILE_LOCK_HANDLE is handle:
                module._ACTIVE_FILE_LOCK_HANDLE = None
            module._OPEN_FILE_LOCK_HANDLES.discard(handle)
        handle.close()


def test_a_domain_error_passes_through_the_wrapper_untouched() -> None:
    """The wrapper must not touch exceptions travelling through it.

    ``@contextmanager``'s ``__exit__`` assigns ``exc.__traceback__`` before
    throwing back into the generator, and ``ServerDomainError`` refuses
    attribute assignment — so a generator-based wrapper turned every domain
    error raised inside a wrapped endpoint into
    ``TypeError: super(type, obj): obj must be an instance or subtype of type``,
    losing the real 409 and its error code. Caught by a route test, not by the
    unit tests here, because it only happens when a *real* domain error
    propagates.

    Mutation: reimplement ``bounded_operation_wait`` with ``@contextmanager``.
    """
    from plugin.server.application.plugins.operation_lock import (
        bounded_operation_wait,
    )
    from plugin.server.domain.errors import ServerDomainError

    original = ServerDomainError(
        code="PLUGIN_MANUAL_NOT_MANAGED",
        message="manual plugin is not managed",
        status_code=409,
    )

    with pytest.raises(ServerDomainError) as excinfo:
        with bounded_operation_wait(5.0):
            raise original

    assert excinfo.value is original, "异常在穿过包装时被换掉或被改写了"
    assert excinfo.value.code == "PLUGIN_MANUAL_NOT_MANAGED"


def test_the_same_process_lock_also_honours_the_deadline() -> None:
    """The process lock is taken *before* the file lock, so bounding only the
    file lock bounds the rarer half.

    Two HTTP requests hitting the same server contend here, not on the file
    lock — that is the ordinary case, and it queued unboundedly.

    Mutation: drop the deadline branch from ``_CrossLoopLock.acquire``.
    """
    from plugin.server.application.plugins import operation_lock as module

    async def _scenario() -> str:
        lock = module._CrossLoopLock()
        await lock.acquire()  # 先被别人占住
        with module.bounded_operation_wait(0.15):
            try:
                await asyncio.wait_for(lock.acquire(), timeout=6.0)
            except module.PluginOperationBusy:
                return "busy"
            except asyncio.TimeoutError:
                return "queued-forever"
        return "acquired"

    assert asyncio.run(_scenario()) == "busy"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20s", 12.0),      # 解析不了
        ("inf", 12.0),      # 无穷大：max() 留不住它，而无限超时等于没有截止期
        ("1e309", 12.0),    # 溢出成 inf
        ("-5", 1.0),        # 负数：夹到下界，不是拒绝
        ("30", 30.0),       # 正常值照常生效
    ],
)
def test_unusable_budget_env_vars_fall_back_instead_of_breaking(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    """These parse at import time, so anything they raise stops the server.

    ``inf`` is the subtle one: it parses fine and survives ``max()``, and an
    infinite timeout is worse than the default because it looks configured
    while removing the deadline entirely (CodeRabbit).

    Mutation: drop the ``math.isfinite`` check, or the try/except.
    """
    from plugin.server.application.plugins._env_budgets import env_seconds

    monkeypatch.setenv("NEKO_TEST_BUDGET", raw)

    assert env_seconds("NEKO_TEST_BUDGET", 12.0) == expected


def test_clearing_one_plugin_leaves_the_others_cached() -> None:
    """Refreshing one plugin must not make the other sixteen pay for a rescan.

    Mutation: make the scoped clear fall through to clearing everything.
    """
    from pathlib import Path

    from plugin.server.application.plugins import metadata_scanner as module

    mine, theirs = Path("/a/plugin.toml"), Path("/b/plugin.toml")
    module._SCAN_CACHE.clear()
    module._SCAN_CACHE[("i", "m", "c", str(mine), (), "{}", "{}", ())] = "v"
    module._SCAN_CACHE[("i", "m", "c", str(theirs), (), "{}", "{}", ())] = "v"
    try:
        module.clear_plugin_metadata_scan_cache(mine)

        remaining = list(module._SCAN_CACHE)
        assert len(remaining) == 1, "定向清理牵连了别的插件"
        assert remaining[0][3] == str(theirs)
    finally:
        module._SCAN_CACHE.clear()


def test_work_before_the_lock_does_not_eat_the_wait_budget() -> None:
    """The budget is "how long to wait for the lock", not "how long the request may take".

    ``reload_all_plugins`` runs a registry refresh *before* its first serialized
    stop, and that refresh has its own budget of the same size. Storing an
    absolute deadline at request entry meant the refresh could exhaust it, so
    the first acquisition raised 409 with nobody holding the lock — and since
    timed-out scans are not cached, every retry did the same (codex).

    Mutation: store ``time.monotonic() + seconds`` at ``__enter__`` again.
    """
    from plugin.server.application.plugins import operation_lock as module

    with module.bounded_operation_wait(0.20):
        first = module._wait_deadline()
        assert first is not None
        # 模拟抢锁之前的慢活儿，长于整个预算
        time.sleep(0.30)
        second = module._wait_deadline()

    assert second is not None
    assert second > time.monotonic(), (
        "抢锁之前的耗时把等锁预算吃光了——没人占锁也会立刻 409"
    )
