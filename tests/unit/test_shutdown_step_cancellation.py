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
    """A step that overruns is cancelled, as the old ``asyncio.wait_for`` did.

    Leaving it running would let e.g. the character release keep using the
    internal HTTP pool that the next steps close.
    """
    from app.main_server import _run_shutdown_step

    child_cancelled = asyncio.Event()

    async def stuck_step() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            child_cancelled.set()
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
    assert time.monotonic() - started < 1.0
    await asyncio.wait_for(child_cancelled.wait(), timeout=1.0)


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
