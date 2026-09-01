"""Every wait in the plugin server needs an upper bound or a reason not to have one.

The failures these guard are not crashes — they are a UI that spins until the
front end gives up at 30 s, and in the lock's case an operation that lands
anyway after the user was told it failed.
"""

from __future__ import annotations

import asyncio
import threading
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

    assert module._OPERATION_WAIT_DEADLINE.get() is None
    with module.bounded_operation_wait(5.0):
        assert module._OPERATION_WAIT_DEADLINE.get() is not None
    assert module._OPERATION_WAIT_DEADLINE.get() is None


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
async def test_clearing_tools_for_a_plugin_with_none_skips_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """That await sits inside stop_plugin's lock.

    With main_server not listening, a loopback connection refusal costs about
    2 s on Windows, so reload-all of eight plugins spends ~16 s of dead time in
    the lock chain — for a request whose receiving end short-circuits on an
    empty list anyway.

    Mutation: remove the ``if not owned`` early return.
    """
    from plugin.server.messaging import llm_tool_registry as module

    posted: list[str] = []

    class _Client:
        async def post(self, url, **kwargs):  # pragma: no cover - must not run
            posted.append(url)
            raise AssertionError("posted /api/tools/clear for a plugin with no tools")

    monkeypatch.setattr(module, "_get_http_client", lambda: _Client())

    result = await module.clear_plugin_tools("plugin-with-no-tools")

    assert posted == []
    assert result.get("cleared") == 0


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

    async def _slow_stop(plugin_id: str):
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
