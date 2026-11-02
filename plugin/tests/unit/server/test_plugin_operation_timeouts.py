"""Every wait in the plugin server needs an upper bound or a reason not to have one.

The failures these guard are not crashes — they are a UI that spins until the
front end gives up at 30 s, and in the lock's case an operation that lands
anyway after the user was told it failed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

pytestmark = pytest.mark.plugin_unit


# ── Lock-acquisition deadline ──


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
    try:
        assert len(attempts) == 3, "无截止期时应该一直重试到拿到锁"
    finally:
        # Like expired-budget tests, _lock_file_once is no-op, so file regions
        # were never locked and cannot use _release_file_lock_sync. Globally registered
        # handles still require deregistration; close alone leaves closed handles for later tests.
        with module._FILE_LOCK_HANDLE_GUARD:
            if module._ACTIVE_FILE_LOCK_HANDLE is handle:
                module._ACTIVE_FILE_LOCK_HANDLE = None
            module._OPEN_FILE_LOCK_HANDLES.discard(handle)
        handle.close()


# ── No tools means no HTTP request ──


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


# ── reload-all total budget ──


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

    async def _slow_stop(plugin_id: str, *, stop_deadline=None, development_snapshot=None):
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
    async def _slow_success(plugin_id: str, *, stop_deadline=None):
        # Longer than remaining budget but truly successful.
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

    async def _stop(plugin_id: str, *, stop_deadline=None, development_snapshot=None):
        # Stops exhausted budgets but both succeeded.
        await asyncio.sleep(0.20)
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    budgets: list[float | None] = []

    async def _start(plugin_id: str, *, start_deadline=None):
        started.append(plugin_id)
        budgets.append(
            None if start_deadline is None else start_deadline - time.monotonic()
        )
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "_safe_start_for_reload", _start)

    result = await service.reload_all_plugins()

    assert started == ["p0", "p1"], (
        f"停掉了却没重新启动，reload 变成了 stop：failed={result.get('failed')}"
    )
    assert result["reloaded"] == ["p0", "p1"]
    # Two 0.20s stops exceed 0.30s. Shared startup deadlines then
    # yield negatives: attempts still occur, tested elsewhere, but only
    # minimum windows prevent slower startups. Independent startup budgets make remaining time positive.
    assert budgets[0] is not None and budgets[0] > 0, (
        f"启动阶段沿用了停止阶段的截止期，一上来预算就是负的：{budgets}"
    )


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
    # Scale budgets/sleeps for loaded CI. Require second remaining time
    # significantly smaller, never exact seconds.
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 1.20)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    service = module.PluginLifecycleService()

    async def _stop(plugin_id: str, *, stop_deadline=None, development_snapshot=None):
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    seen: list[float | None] = []

    async def _start(plugin_id: str, *, refresh_registry=True, start_deadline=None):
        # Record current remaining time, not original arguments: absolute deadlines mean second plugins
        # must see less. Recording arguments hides
        # fresh-per-plugin-budget regressions.
        seen.append(None if start_deadline is None else start_deadline - time.monotonic())
        await asyncio.sleep(0.45)
        return {"success": True}

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    await service.reload_all_plugins()

    assert len(seen) == 2, f"两个插件都该被尝试启动：{seen}"
    # Allow margin: deadline - monotonic() immediately after setting may
    # yield 1.2000000000116415, not <=1.20. Coarse upper bound;
    # significant-difference assertions below carry the requirement.
    assert seen[0] is not None and seen[0] <= 1.25, (
        f"第一个启动拿到的上限比整轮预算还大：{seen[0]}"
    )
    # Require a **real** gap, not seen[1] < seen[0], a coin flip under mutations:
    # new per-plugin budgets both approximate full allowances, ordered by microsecond jitter,
    # letting half survive (adversarial review). First startup sleeps 0.45s,
    # so actual gaps approach 0.45s.
    assert seen[1] is not None and seen[1] < seen[0] - 0.20, (
        f"第二个启动没有拿到**剩余**预算，而是又一份完整的：{seen}"
    )


def test_the_step_clamp_never_widens_and_never_reaches_zero() -> None:
    """Every direction of the clamp, on the function production actually calls.

    A spent budget must still buy a short attempt: every plugin reaching the
    start phase was just stopped by us, so refusing to try leaves it down — the
    opposite of a reload. And a plugin that declared a *shorter* timeout of its
    own must not have it widened, by a generous budget **or by the floor**. The
    sub-second case is the one the first version of this test missed: with the
    floor applied outside the ``min`` a plugin asking for 0.5 s was handed 1.0 s
    (this adversarial review).

    Mutation: drop the lower bound, drop the outer ``min``, or put the floor
    back on the outside.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    start_floor = module._MIN_CLAMPED_START_TIMEOUT
    stop_floor = module._MIN_CLAMPED_STOP_TIMEOUT
    # Lower bounds need independent constants: raising startup floors for real
    # attempts must not increase unrelated shutdown worst-case wall time.
    assert start_floor != stop_floor, (
        "启动和关停的下界又被合成同一个数了"
    )

    for floor in (start_floor, stop_floor):
        assert module._clamp_step_timeout(10.0, 0.0, floor=floor) > 0, (
            "预算见底时算出了 0，等于直接判失败"
        )
        assert module._clamp_step_timeout(10.0, 5.0, floor=floor) == 5.0, (
            "剩余预算没有压住上限"
        )
        assert module._clamp_step_timeout(2.0, 30.0, floor=floor) == 2.0, (
            "自己更短的超时被预算放宽了"
        )
        assert module._clamp_step_timeout(0.5, 0.0, floor=floor) == 0.5, (
            "自己更短的超时被下界放宽了"
        )
        assert module._clamp_step_timeout(10.0, None, floor=floor) == 10.0, (
            "没有预算时不该改动配置值"
        )

    # Startup minimums must afford real attempts: spawning/framework imports alone measured 0.74s before plugin
    # imports. Shorter guarantees timeouts and falsely failed healthy plugins.
    assert start_floor >= 2.0, (
        f"启动下界 {start_floor}s 买不到一次真正的启动尝试"
    )


@pytest.mark.asyncio
async def test_a_late_stop_is_still_asked_to_shut_down_not_just_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop side needs the same floor the start side has, derived just in time.

    Two things at once, because they are the same line. `min(SHUTDOWN, remaining)`
    with a spent budget hands the host `shutdown_timeout≈0`, which skips straight
    to killing a plugin that would have flushed and closed cleanly. And deriving
    it in the *caller* is too early: the value is computed before `stop_plugin`
    enters its decorator, so a stop that then waits out most of the budget on the
    lock still gets a timeout sized as if no waiting had happened (codex).

    So this asserts on what the host was actually told, after an already-expired
    deadline — the number has to come from the floor, not from the arithmetic.

    Mutation: derive the timeout in the reload loop again, or drop the floor.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    handed: list[float] = []

    class _Boom(Exception):
        pass

    class _Host:
        async def shutdown(self, timeout=None):
            handed.append(timeout)
            # All checks complete here; do not continue into stop_plugin finalization.
            raise _Boom

        async def start(self, *a, **k):  # pragma: no cover - contract shape only
            return None

    monkeypatch.setattr(module, "_get_plugin_host_sync", lambda plugin_id: _Host())
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    monkeypatch.setattr(module, "PluginHostContract", _Host)

    service = module.PluginLifecycleService()
    with pytest.raises(BaseException):
        await service.stop_plugin(
            "p0", stop_deadline=time.monotonic() - 5.0  # Budget long exhausted.
        )

    assert handed, "前提没成立：根本没走到 host.shutdown"
    # Floors raise only budget-compressed values, never plugin-configured limits. Shorter
    # configured shutdown timeouts remain authoritative; prevent near-zero
    # compression causing immediate kills.
    assert handed[0] >= min(
        module.PLUGIN_SHUTDOWN_TIMEOUT, module._MIN_CLAMPED_STOP_TIMEOUT
    ), (
        f"预算见底时把关停上限压到了下界以下，插件会被直接杀掉：{handed}"
    )
    assert handed[0] <= module.PLUGIN_SHUTDOWN_TIMEOUT, (
        f"关停上限比配置值还大：{handed}"
    )


@pytest.mark.asyncio
async def test_a_spent_budget_still_buys_a_real_wait_for_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero wait is not an attempt, it is a refusal wearing an attempt's clothes.

    Removing the over-budget ``break`` only helps if what replaces it can
    actually succeed. With a spent budget ``bounded_operation_wait(0.0)`` means
    "do not wait at all", so any concurrent plugin operation — a user pressing
    start, an uninstall, a source switch — makes ``start_plugin`` raise
    ``PluginOperationBusy`` immediately, and a plugin we just stopped stays
    stopped. That is the same outcome the ``break`` was removed to prevent, let
    back in through the side door (CodeRabbit). The lock wait shares the step
    floor.

    Mutation: floor the lock wait at 0.0 again.
    """
    from plugin.server.application.plugins import lifecycle_service as module
    from plugin.server.application.plugins.operation_lock import (
        serialized_plugin_operation,
    )
    from plugin.server.application.plugins import operation_lock

    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: ["p0"])
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    # Budget exhausted long before startup's turn.
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.01)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    service = module.PluginLifecycleService()
    started: list[str] = []

    async def _stop(plugin_id: str, *, stop_deadline=None, development_snapshot=None):
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    # Real decorator required to acquire the real lock.
    @serialized_plugin_operation
    async def _start(plugin_id: str, *, refresh_registry=True, start_deadline=None):
        started.append(plugin_id)
        return {"success": True}

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "start_plugin", _start)

    # Another operation holds startup locks, released after 0.3s, safely within minimum waiting allowance.
    await operation_lock._PROCESS_LOCK.acquire()

    async def _release_later():
        await asyncio.sleep(0.30)
        operation_lock._PROCESS_LOCK.release()

    releaser = asyncio.create_task(_release_later())
    try:
        result = await service.reload_all_plugins()
    finally:
        await releaser

    assert started == ["p0"], (
        f"预算见底时等锁被压成零，插件被直接判 busy 而留在停止状态：{result.get('failed')}"
    )
    assert result["reloaded"] == ["p0"]
@pytest.mark.asyncio
async def test_every_stopped_plugin_gets_a_start_attempt_even_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping a start is not deferring it — nothing comes back for it.

    Autostart runs once, at server startup; there is no periodic reconcile. So a
    plugin dropped from the start phase for being over budget stays down until
    the user starts it by hand or restarts the whole server (Greptile). Stopping
    a plugin is a promise to start it, and the budget does not release us from
    a promise we already made — the two phases are asymmetric on purpose: a
    skipped *stop* leaves a plugin running, a skipped *start* leaves it dead.

    Mutation: put the over-budget ``break`` back into the start loop.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    plugin_ids = [f"p{i}" for i in range(4)]
    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", lambda: list(plugin_ids))
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    monkeypatch.setattr(module, "_RELOAD_ALL_BUDGET_SECONDS", 0.20)

    async def _noop_refresh(*a, **k):
        return {"success": True}

    monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _noop_refresh)

    async def _ordered(ids):
        return list(ids)

    monkeypatch.setattr(module.plugin_registry_service, "order_plugin_ids", _ordered)

    service = module.PluginLifecycleService()
    attempted: list[str] = []

    async def _stop(plugin_id: str, *, stop_deadline=None, development_snapshot=None):
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    async def _start(plugin_id: str, *, start_deadline=None):
        attempted.append(plugin_id)
        # Each startup exceeds the whole budget, exhausting it after one.
        await asyncio.sleep(0.15)
        return module._ReloadOutcome(plugin_id=plugin_id, success=True)

    monkeypatch.setattr(service, "_safe_stop_for_reload", _stop)
    monkeypatch.setattr(service, "_safe_start_for_reload", _start)

    result = await service.reload_all_plugins()

    assert attempted == plugin_ids, (
        f"预算见底之后就不再尝试启动了，这些插件会一直停着：试过 {attempted}"
    )
    assert result["reloaded"] == plugin_ids


def test_the_file_lock_stage_is_only_entered_under_the_process_lock() -> None:
    """This is what keeps the single-worker executor from swallowing deadlines.

    ``_FILE_LOCK_EXECUTOR`` has one worker, so if two callers could submit to it
    at once, the second would sit in the executor *queue* — where no deadline is
    consulted, because the check lives inside the worker function — and sail
    past the front end's timeout (Greptile).

    They cannot, and the reason is ordering: ``__aenter__`` takes the in-process
    ``_PROCESS_LOCK`` first and only then reaches the file lock, so at most one
    task is ever in that executor. A second caller waits on ``_CrossLoopLock``,
    which *is* deadline-aware and refuses with ``PluginOperationBusy``.

    That invariant is load-bearing and invisible, so pin it: move the file lock
    outside the process lock, or acquire it anywhere else, and this fails.

    Mutation: acquire the file lock before ``_PROCESS_LOCK`` in ``__aenter__``.
    """
    from plugin.server.application.plugins import operation_lock as module

    held_when_entered: list[bool] = []
    real = module._acquire_file_lock_cancellation_safe

    async def _record(deadline=None):
        with module._PROCESS_LOCK._state_lock:
            held_when_entered.append(module._PROCESS_LOCK._held)
        return await real(deadline)

    async def _scenario() -> None:
        module._acquire_file_lock_cancellation_safe = _record
        try:
            async with module.plugin_operation_lock.hold():
                pass
        finally:
            module._acquire_file_lock_cancellation_safe = real

    asyncio.run(_scenario())

    assert held_when_entered == [True], (
        "进文件锁那一层时进程锁没被握着——单 worker 的 executor 会开始排队，"
        f"而排队期间没有任何东西看截止期：{held_when_entered}"
    )


@pytest.mark.asyncio
async def test_the_tool_cleanup_request_honours_the_caller_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parameter that is accepted and ignored is worse than no parameter.

    The tool cleanup runs on the stop path with the operation lock held. Its own
    two-second timeout is independent of the round budget, so a stalled
    ``main_server`` lets a stop spend that long past ``stop_deadline`` while
    holding the lock (codex). Passing a budget in must actually reach the
    request — the first version of this change added the keyword argument and
    left the body using the module constant, and every test still passed.

    Mutation: ignore ``timeout`` and always use ``_CLEAR_TOOLS_TIMEOUT``.
    """
    from plugin.server.messaging import llm_tool_registry as module

    seen: list = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self):
            return {"success": True}

    class _Client:
        async def post(self, url, json=None, timeout=None):
            seen.append(timeout)
            return _Resp()

    monkeypatch.setattr(module, "_get_http_client", lambda: _Client())

    await module.clear_plugin_tools("demo", timeout=0.25)

    assert seen, "前提没成立：根本没发出请求"
    handed = seen[0]
    total = getattr(handed, "read", None) or getattr(handed, "pool", None)
    assert total == pytest.approx(0.25), (
        f"预算没有传到真正发请求那一步，还是用的模块常量：{handed}"
    )
    assert getattr(handed, "connect", None) == pytest.approx(0.25), (
        "connect 超时没有跟着收窄，会比整段预算还长"
    )


@pytest.mark.asyncio
async def test_the_tool_cleanup_is_bounded_as_a_whole_not_per_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``httpx.Timeout`` bounds phases, not the call.

    A response that keeps dribbling bytes never trips connect/read/write
    individually, so the whole request can run far past the budget while the
    operation lock is held (CodeRabbit demonstrated this against a real chunked
    server). The per-phase limits stay; an outer ``asyncio.wait_for`` is what
    makes the number a total.

    Mutation: drop the ``asyncio.wait_for`` wrapper.
    """
    import time as _time

    from plugin.server.messaging import llm_tool_registry as module

    class _Client:
        async def post(self, url, json=None, timeout=None):
            # Every phase stays under its timeout but never finishes overall.
            await asyncio.sleep(5)
            raise AssertionError("should have been cut off")

    monkeypatch.setattr(module, "_get_http_client", lambda: _Client())

    started = _time.monotonic()
    result = await module.clear_plugin_tools("demo", timeout=0.2)
    elapsed = _time.monotonic() - started

    assert elapsed < 2.0, f"整通调用没有被总时长兜住，花了 {elapsed:.1f}s"
    assert result["ok"] is False and result.get("error") == "timeout", (
        f"超时没有按尽力而为处理：{result}"
    )


@pytest.mark.asyncio
async def test_a_spent_stop_budget_still_attempts_the_cleanup_on_a_short_leash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ways to get this wrong, and this pins both.

    Too generous: ``_clamp_step_timeout``'s one-second floor exists because a
    plugin we stopped must get a real start attempt. Reusing it here meant an
    exhausted stop budget still bought a second of lock time per plugin for a
    cleanup that is explicitly best-effort (CodeRabbit).

    Too stingy: the version that fixed that skipped the cleanup outright once the
    budget was spent — and this is the only place in the tree that clears the
    remote tool registration, with no reconciliation and no retry behind it. The
    host is already gone, so the skipped tools stay advertised to the model,
    which then picks one and gets "plugin not running" forever (Greptile).

    So: always attempt, on a leash far shorter than the start-attempt floor. The
    POST clears by source and is idempotent, so a failure is no worse than the
    skip was.

    Mutations: clamp with ``_clamp_step_timeout`` again; or skip when the budget
    is spent.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    handed: list = []

    async def _clear(plugin_id, *, timeout=None):
        handed.append(timeout)
        return {"ok": True}

    class _Host:
        async def shutdown(self, timeout=None):
            return None

        async def start(self, *a, **k):  # pragma: no cover - contract shape only
            return None

    monkeypatch.setattr(module, "clear_plugin_llm_tools", _clear)
    monkeypatch.setattr(module, "_get_plugin_host_sync", lambda plugin_id: _Host())
    monkeypatch.setattr(module, "PluginHostContract", _Host)
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)

    service = module.PluginLifecycleService()
    # Stub host completes shutdown; later stop_plugin cleanup raises domain errors
    # because registry lacks plugins. Inspect cleanup initiation, not exception type,
    # but do not swallow KeyboardInterrupt: catch Exception only.
    with contextlib.suppress(Exception):
        await service.stop_plugin("p0", stop_deadline=time.monotonic() - 5.0)

    assert handed, (
        "预算见底就跳过了清理——host 已经摘掉，而远端工具表还在向模型公布，"
        "没有任何东西会重试"
    )
    assert handed[0] == module._MIN_TOOL_CLEANUP_TIMEOUT, (
        f"预算见底时给的不是那一小段下界：{handed}"
    )
    assert handed[0] < module._MIN_CLAMPED_STOP_TIMEOUT, (
        f"又把「启动尝试」的下界套回到这次尽力而为的远端调用上了：{handed}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["returns_not_ok", "raises"])
async def test_a_failed_tool_cleanup_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A cleanup that did not land must leave a trace naming the plugin.

    ``clear_plugin_tools`` is deliberately quiet inside itself — a noisy failure
    there would mask the real shutdown reason — and it returns its verdict as a
    value. The stop path used to discard that value, so a stop that left the
    model holding an uncallable tool looked exactly like a clean stop. Nothing
    else reconciles the remote registry, so that trace is the only way a ghost
    tool is ever attributable.

Cover both failure paths with identical wording: returning ``{"ok": false}`` and **raising**.
    Exceptions are subtler: clear_plugin_tools catches only httpx.HTTPError and
    asyncio.TimeoutError; malformed bodies claiming JSON content-type make
    resp.json() raise ValueError into callers' except Exception. The initial fix
    covered return values only (CodeRabbit). Consequences match, requiring identical levels/wording,
    or operators must search two messages to find every case.

    Mutation: drop either warning, or go back to discarding the returned dict.
    """
    from plugin.server.application.plugins import lifecycle_service as module

    warnings: list = []

    async def _clear(plugin_id, *, timeout=None):
        if failure == "raises":
            # httpx raises ValueError subclasses for JSON content-type with non-JSON bodies,
            # uncaught by clear_plugin_tools.
            raise ValueError("malformed JSON body")
        return {"ok": False, "error": "timeout", "owned_count": 3}

    class _Host:
        async def shutdown(self, timeout=None):
            return None

        async def start(self, *a, **k):  # pragma: no cover - contract shape only
            return None

    monkeypatch.setattr(module, "clear_plugin_llm_tools", _clear)
    monkeypatch.setattr(module, "_get_plugin_host_sync", lambda plugin_id: _Host())
    monkeypatch.setattr(module, "PluginHostContract", _Host)
    monkeypatch.setattr(module, "_emit_lifecycle_event", lambda **kw: None)
    class _RecordingLogger:
        # Replace whole logger, not warning: PluginLoggerAdapter methods are
        # readonly properties and setattr raises.
        def warning(self, msg, *a, **k):
            warnings.append((msg, a))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(module, "logger", _RecordingLogger())

    service = module.PluginLifecycleService()
    with contextlib.suppress(Exception):
        await service.stop_plugin("p0")

    hits = [w for w in warnings if "tools may still be advertised" in w[0]]
    assert hits, f"清理失败没有留下任何可追查的痕迹：{warnings}"
    assert "p0" in hits[0][1], f"痕迹里没有插件 id，出现幽灵工具时无从归因：{hits[0]}"
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

    # File locks always contested; budget allocation follows remaining cross-process time.
    reached_file_lock: list[int] = []

    def _always_contended(handle):
        reached_file_lock.append(1)
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
            # Direct __aenter__ lacks __aexit__ cleanup. If locks actually acquire,
            # indicating broken behavior, release them here; retained global locks
            # hang every subsequent dependent test forever, giving no result rather than failure.
            # Cleanup is not an assertion, but keeps one failed test from consuming the session.
            if held._acquired:
                if held._file_lock_handle is not None:
                    module._release_file_lock_sync(held._file_lock_handle)
                module._PROCESS_LOCK.release()

    outcome, elapsed = asyncio.run(_scenario())

    assert outcome == "busy"
    # Premise: reach layer two. Cross-process exhaustion could pass busy without checking
    # shared deadlines, this test's purpose (adversarial review).
    assert reached_file_lock, "没走到文件锁那一层，这一轮什么也没验证"
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
        # Acquisition registers _ACTIVE_FILE_LOCK_HANDLE and
        # _OPEN_FILE_LOCK_HANDLES; closing alone leaves closed globals
        # for later tests (CodeRabbit).
        #
        # Do not call _release_file_lock_sync directly: _lock_file_once is
        # no-op, leaving unlocked file regions, so msvcrt unlock raises
        # PermissionError. Undo both registrations, then close.
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
        await lock.acquire()  # Already occupied by another operation.
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
        ("20s", 12.0),      # Unparseable.
        ("inf", 12.0),      # Infinity: max() cannot constrain it; infinite timeout means no deadline.
        ("1e309", 12.0),    # Overflow to inf.
        ("-5", 1.0),        # Negative: clamp to minimum, not reject.
        ("30", 30.0),       # Normal values apply normally.
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


# Former test_clearing_one_plugin_leaves_the_others_cached pinned
# clear_plugin_metadata_scan_cache's config_path branch with no production callers.
# Single-plugin refresh uses force, deleting its cache key itself, so this guard
# falsely implied coverage of unexecuted code. Handcrafted tuples by key positions also
# merely forced test updates on layout changes, proving nothing. Remove branch/guard together.


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
        # Simulate pre-acquisition work exceeding the entire budget.
        time.sleep(0.30)
        second = module._wait_deadline()

    assert second is not None
    assert second > time.monotonic(), (
        "抢锁之前的耗时把等锁预算吃光了——没人占锁也会立刻 409"
    )
