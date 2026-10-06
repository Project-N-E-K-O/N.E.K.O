"""Cancellation during one shutdown step must not skip the remaining cleanups."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_defers_cancellation_and_lets_later_steps_run() -> None:
    from app.main_server import _run_shutdown_step

    ran: list[str] = []

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_step() -> None:
        entered.set()
        await release.wait()
        ran.append("first")

    async def later_step() -> None:
        ran.append("later")

    async def shutdown_like() -> asyncio.CancelledError | None:
        pending = await _run_shutdown_step(
            blocking_step,
            what="first",
            deadline_monotonic=time.monotonic() + 1.0,
        )
        pending = await _run_shutdown_step(
            later_step,
            what="second",
            deadline_monotonic=time.monotonic() + 1.0,
            pending_cancellation=pending,
        )
        return pending

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "caller cancellation must not cancel the cleanup child"
    release.set()
    pending = await task
    assert ran == ["first", "later"]
    assert isinstance(pending, asyncio.CancelledError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failing_step_keeps_the_caller_cancellation_it_absorbed() -> None:
    """A step that raises after absorbing a caller cancel must still return it.

    Re-raising the step's exception would drop the cancellation the helper had
    already uncancelled, so on_shutdown would return normally instead of
    honouring the caller's cancel once every cleanup had run.
    """
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()
    release = asyncio.Event()

    async def failing_step() -> None:
        entered.set()
        await release.wait()
        raise RuntimeError("cleanup failed")

    async def shutdown_like() -> asyncio.CancelledError | None:
        return await _run_shutdown_step(
            failing_step,
            what="failing",
            deadline_monotonic=time.monotonic() + 1.0,
        )

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    pending = await task
    assert isinstance(pending, asyncio.CancelledError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_keeps_the_first_cancellation() -> None:
    from app.main_server import _run_shutdown_step

    first = asyncio.CancelledError()

    async def ok_step() -> None:
        return None

    pending = await _run_shutdown_step(
        ok_step,
        what="second",
        deadline_monotonic=time.monotonic() + 1.0,
        pending_cancellation=first,
    )
    assert pending is first


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_passes_through_success_and_failure() -> None:
    from app.main_server import _run_shutdown_step

    async def ok_step() -> None:
        return None

    async def failing_step() -> None:
        raise RuntimeError("cleanup failed")

    def factory_raises():
        raise RuntimeError("factory failed")

    for step in (ok_step, failing_step, factory_raises):
        assert (
            await _run_shutdown_step(
                step,
                what="step",
                deadline_monotonic=time.monotonic() + 1.0,
            )
            is None
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_does_not_treat_child_cancel_as_caller_cancel() -> None:
    from app.main_server import _run_shutdown_step

    async def cancelled_step() -> None:
        raise asyncio.CancelledError()

    assert (
        await _run_shutdown_step(
            cancelled_step,
            what="child",
            deadline_monotonic=time.monotonic() + 1.0,
        )
        is None
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_cancels_the_child_at_its_deadline() -> None:
    """A step that overruns is cancelled and has STOPPED before the helper returns.

    Like the old ``asyncio.wait_for``: only sending the cancel would let e.g. the
    character release still be unwinding on the internal HTTP pool that the
    next steps close.
    """
    from app.main_server import _run_shutdown_step

    stopped: list[str] = []

    async def stuck_step() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Unwinding takes a few loop turns, as a real HTTP request does.
            await asyncio.sleep(0.05)
            stopped.append("stuck")
            raise

    started = time.monotonic()
    assert (
        await _run_shutdown_step(
            stuck_step,
            what="stuck",
            deadline_monotonic=time.monotonic() + 0.05,
        )
        is None
    )
    assert stopped == ["stuck"]
    assert time.monotonic() - started < 1.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_grace_after_deadline_cancel_is_bounded(monkeypatch) -> None:
    from app import main_server

    monkeypatch.setattr(main_server, "_SHUTDOWN_STEP_CANCEL_GRACE_SECONDS", 0.05)
    release = asyncio.Event()

    async def refuses_to_stop() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    started = time.monotonic()
    await main_server._run_shutdown_step(
        refuses_to_stop,
        what="stubborn",
        deadline_monotonic=time.monotonic() + 0.05,
    )
    assert time.monotonic() - started < 1.0
    stubborn = [t for t in main_server._SHUTDOWN_STEP_TASKS if not t.done()]
    assert stubborn, "the abandoned step must stay strongly referenced"
    release.set()
    await asyncio.gather(*stubborn, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_without_deadline_waits_for_the_step() -> None:
    from app.main_server import _run_shutdown_step

    done: list[str] = []

    async def slow_step() -> None:
        await asyncio.sleep(0.1)
        done.append("slow")

    assert (
        await _run_shutdown_step(slow_step, what="slow", deadline_monotonic=None)
        is None
    )
    assert done == ["slow"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_caller_cancel_bounds_the_wait_on_a_step_without_deadline() -> None:
    """Without a deadline the caller's cancel is the only thing bounding the wait.

    After the cancel the step gets its cancelled budget, then is cancelled like
    any overrunning step, and the remaining steps still run.
    """
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()
    stopped: list[str] = []
    ran: list[str] = []

    async def stuck_step() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append("stuck")
            raise

    async def later_step() -> None:
        ran.append("later")

    async def shutdown_like() -> asyncio.CancelledError | None:
        pending = await _run_shutdown_step(
            stuck_step,
            what="stuck",
            deadline_monotonic=None,
            cancelled_budget_seconds=0.05,
        )
        return await _run_shutdown_step(
            later_step,
            what="later",
            deadline_monotonic=time.monotonic() + 1.0,
            pending_cancellation=pending,
        )

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    # asyncio.wait, not wait_for: wait_for would cancel again and then block on
    # the very wait this test is checking for.
    done, _ = await asyncio.wait({task}, timeout=2.0)
    assert done, "cancelling the caller must bound the wait on a step without deadline"
    assert isinstance(task.result(), asyncio.CancelledError)
    assert stopped == ["stuck"]
    assert ran == ["later"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_step_without_deadline_still_finishes_within_its_cancelled_budget() -> None:
    """A cancel must not throw away work that completes within its budget."""
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()
    release = asyncio.Event()
    finished: list[str] = []

    async def slow_step() -> None:
        entered.set()
        await release.wait()
        finished.append("slow")

    async def shutdown_like() -> asyncio.CancelledError | None:
        return await _run_shutdown_step(
            slow_step,
            what="slow",
            deadline_monotonic=None,
            cancelled_budget_seconds=1.0,
        )

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    asyncio.get_running_loop().call_later(0.05, release.set)
    done, _ = await asyncio.wait({task}, timeout=2.0)
    assert done
    assert isinstance(task.result(), asyncio.CancelledError)
    assert finished == ["slow"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("budget", "expect_called"), ((1.0, True), (0.0, False)))
async def test_step_without_deadline_after_an_earlier_cancel_gets_its_budget(
    budget, expect_called
) -> None:
    """A cancel absorbed by an earlier step will not arrive again to end the wait.

    So an unbounded step entered with a pending cancel is bounded by its
    cancelled budget right away; with no budget it is not started at all.
    """
    from app.main_server import _run_shutdown_step

    called = False

    async def step() -> None:
        nonlocal called
        called = True

    first = asyncio.CancelledError()
    pending = await _run_shutdown_step(
        step,
        what="unbounded",
        deadline_monotonic=None,
        pending_cancellation=first,
        cancelled_budget_seconds=budget,
    )
    assert pending is first
    assert called is expect_called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_abandoned_step_failure_is_still_logged(monkeypatch) -> None:
    """A step shutdown stopped waiting on must not fail silently afterwards."""
    from app import main_server

    monkeypatch.setattr(main_server, "_SHUTDOWN_STEP_CANCEL_GRACE_SECONDS", 0.05)
    warnings: list[str] = []
    monkeypatch.setattr(
        main_server.logger,
        "warning",
        lambda msg, *args: warnings.append(msg % args),
    )
    release = asyncio.Event()

    async def refuses_then_fails() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        raise RuntimeError("upload failed late")

    await main_server._run_shutdown_step(
        refuses_then_fails,
        what="late",
        deadline_monotonic=time.monotonic() + 0.05,
    )
    stuck = [
        t for t in main_server._SHUTDOWN_STEP_TASKS if t.get_name() == "shutdown:late"
    ]
    assert stuck, "the abandoned step must stay strongly referenced"
    release.set()
    await asyncio.gather(*stuck, return_exceptions=True)
    await asyncio.sleep(0)
    late = [w for w in warnings if "upload failed late" in w]
    assert late == ["late failed after shutdown stopped waiting for it: upload failed late"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_skips_a_step_whose_deadline_already_passed() -> None:
    from app.main_server import _run_shutdown_step

    called = False

    async def step() -> None:
        nonlocal called
        called = True

    await _run_shutdown_step(
        step,
        what="late",
        deadline_monotonic=time.monotonic() - 1.0,
    )
    assert called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_clears_the_cancelling_counter() -> None:
    """After absorbing a real cancel, ``cancelling()`` must be back to zero.

    The cancel has been handled here and is re-raised explicitly at the end of
    on_shutdown; until then nothing that inspects the counter (a ``TaskGroup``,
    ``asyncio.timeout``, a helper that re-raises when ``cancelling()`` is set)
    may see it as still outstanding.
    """
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()

    async def shutdown_like() -> int:
        release = asyncio.Event()

        async def blocking_step() -> None:
            entered.set()
            await release.wait()

        current = asyncio.current_task()
        assert current is not None
        current.get_loop().call_later(0.01, release.set)
        await _run_shutdown_step(
            blocking_step,
            what="blocking",
            deadline_monotonic=time.monotonic() + 1.0,
        )
        return current.cancelling()

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    assert await task == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_later_step_deadline_still_works_after_an_absorbed_cancel() -> None:
    """An absorbed caller cancel must not disarm a later step's deadline.

    The later step overruns: it still has to be cancelled at its deadline, and
    the first cancellation still has to come back for the final re-raise.
    """
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()
    stuck_cancelled = asyncio.Event()

    async def shutdown_like() -> asyncio.CancelledError | None:
        release = asyncio.Event()

        async def blocking_step() -> None:
            entered.set()
            await release.wait()

        async def stuck_step() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stuck_cancelled.set()
                raise

        asyncio.get_running_loop().call_later(0.01, release.set)
        pending = await _run_shutdown_step(
            blocking_step,
            what="blocking",
            deadline_monotonic=time.monotonic() + 1.0,
        )
        return await _run_shutdown_step(
            stuck_step,
            what="stuck",
            deadline_monotonic=time.monotonic() + 0.05,
            pending_cancellation=pending,
        )

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    pending = await asyncio.wait_for(task, timeout=2.0)
    assert isinstance(pending, asyncio.CancelledError)
    await asyncio.wait_for(stuck_cancelled.wait(), timeout=1.0)


def _unprotected_awaits(fn: ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """Every await in ``fn`` a cancellation can escape from.

    Escape means: not wrapped in ``_run_shutdown_step`` AND not inside a ``try``
    whose handlers catch ``CancelledError``/``BaseException``. ``except
    Exception`` does not count: ``CancelledError`` is a ``BaseException``.
    """
    found: list[tuple[int, str]] = []

    def catches_cancel(handler: ast.ExceptHandler) -> bool:
        if handler.type is None:
            return True
        raw = handler.type
        names = (
            [ast.unparse(e) for e in raw.elts]
            if isinstance(raw, ast.Tuple)
            else [ast.unparse(raw)]
        )
        return any(n.endswith("CancelledError") or n == "BaseException" for n in names)

    def walk(node, try_stack) -> None:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
            # Nested coroutines run as the step itself, inside the helper.
            return
        if isinstance(node, ast.Try):
            # Only the body is covered by this try's handlers; code inside the
            # handlers, else and finally is not.
            for st in node.body:
                walk(st, try_stack + [node])
            for handler in node.handlers:
                for st in handler.body:
                    walk(st, try_stack)
            for st in node.orelse + node.finalbody:
                walk(st, try_stack)
            return
        if isinstance(node, ast.Await):
            value = node.value
            wrapped = (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == "_run_shutdown_step"
            )
            protected = any(
                any(catches_cancel(h) for h in t.handlers) for t in try_stack
            )
            if not wrapped and not protected:
                found.append((node.lineno, ast.unparse(value)[:80]))
        for child in ast.iter_child_nodes(node):
            walk(child, try_stack)

    for statement in fn.body:
        walk(statement, [])
    return found


def _parse_async_fn(source: str) -> ast.AsyncFunctionDef:
    fn = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    return fn


@pytest.mark.unit
def test_on_shutdown_has_no_cancellation_escape() -> None:
    """Derive escapes from the AST instead of checking a hand-written call list.

    A list of helper names passes while other cleanups stay unwrapped, purely
    because they are not on the list. Enumerating every await means a new
    bare cleanup fails here even if nobody remembers to update anything.
    """
    from app.main_server import on_shutdown

    escapes = _unprotected_awaits(_parse_async_fn(inspect.getsource(on_shutdown)))
    calls = [
        node for node in ast.walk(_parse_async_fn(inspect.getsource(on_shutdown)))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_run_shutdown_step"
    ]
    assert calls
    assert all(
        any(kw.arg == "cancellation_budget" for kw in call.keywords)
        for call in calls
    ), "every shutdown step must share the cancellation budget"
    assert not escapes, (
        "these awaits let a cancellation escape on_shutdown and skip every cleanup "
        "after them; wrap them in _run_shutdown_step:\n"
        + "\n".join(f"  line {line}: {code}" for line, code in escapes)
    )


@pytest.mark.unit
def test_escape_scan_flags_bare_awaits_and_spares_protected_ones() -> None:
    """Keep the guard above from going vacuous."""
    fn = _parse_async_fn(
        """
        async def on_shutdown():
            await bare_one()
            try:
                await under_except_exception()
            except Exception:
                pass
            try:
                await under_except_cancelled()
            except asyncio.CancelledError:
                await in_handler()
            await _run_shutdown_step(step, what="x", deadline_monotonic=0)

            async def nested():
                await inside_nested()
        """
    )
    assert [code for _, code in _unprotected_awaits(fn)] == [
        "bare_one()",
        "under_except_exception()",
        "in_handler()",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("unbounded", (False, True))
async def test_shared_cancel_budget_caps_wait_and_grace(monkeypatch, unbounded):
    """Repeated caller cancels cannot extend either a step or its grace."""
    from app import main_server

    monkeypatch.setattr(main_server, "_SHUTDOWN_CANCELLED_BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(main_server, "_SHUTDOWN_STEP_CANCEL_GRACE_SECONDS", 1.0)
    budget = main_server._ShutdownCancellationBudget()
    entered = asyncio.Event()
    release = asyncio.Event()
    child_cancelled = asyncio.Event()
    observed_deadlines = []
    later_called = []

    async def refuses_cancel():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                child_cancelled.set()

    async def later():
        later_called.append(True)

    async def shutdown_like():
        pending = await main_server._run_shutdown_step(
            refuses_cancel,
            what="shared-budget",
            deadline_monotonic=None if unbounded else time.monotonic() + 10.0,
            cancelled_budget_seconds=5.5,
            cancellation_budget=budget,
        )
        return await main_server._run_shutdown_step(
            later,
            what="after-budget",
            deadline_monotonic=time.monotonic() + 10.0,
            pending_cancellation=pending,
            cancellation_budget=budget,
        )

    caller = asyncio.create_task(shutdown_like())
    await entered.wait()
    caller.cancel("first")
    await asyncio.sleep(0)
    observed_deadlines.append(budget.deadline)
    caller.cancel("second")
    await asyncio.sleep(0)
    observed_deadlines.append(budget.deadline)
    done, _ = await asyncio.wait({caller}, timeout=0.5)
    try:
        assert done, "shared budget must cap step wait AND cancellation grace"
        assert caller.result().args == ("first",)
        assert observed_deadlines[0] is not None
        assert observed_deadlines == [budget.deadline, budget.deadline]
        assert child_cancelled.is_set()
        assert not later_called, "spent shared budget must skip later async work"
    finally:
        release.set()
        await asyncio.gather(
            caller, *list(main_server._SHUTDOWN_STEP_TASKS), return_exceptions=True
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_budget_is_shared_across_successful_steps(monkeypatch):
    """The next cleanup receives only what the previous step left over."""
    from app import main_server

    monkeypatch.setattr(main_server, "_SHUTDOWN_CANCELLED_BUDGET_SECONDS", 0.15)
    budget = main_server._ShutdownCancellationBudget()
    pending = asyncio.CancelledError("earlier")
    first_finished = []
    second_cancelled = []

    async def first():
        await asyncio.sleep(0.05)
        first_finished.append(True)

    async def second():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            second_cancelled.append(True)
            raise

    result = await main_server._run_shutdown_step(
        first, what="first", deadline_monotonic=time.monotonic() + 10.0,
        pending_cancellation=pending, cancellation_budget=budget,
    )
    deadline = budget.deadline
    caller = asyncio.create_task(main_server._run_shutdown_step(
        second, what="second", deadline_monotonic=None,
        cancelled_budget_seconds=5.5, pending_cancellation=result,
        cancellation_budget=budget,
    ))
    done, _ = await asyncio.wait({caller}, timeout=0.4)
    try:
        assert done, "later steps must use the remaining shared budget"
        assert caller.result() is pending
        assert first_finished and second_cancelled
        assert budget.deadline == deadline
    finally:
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
