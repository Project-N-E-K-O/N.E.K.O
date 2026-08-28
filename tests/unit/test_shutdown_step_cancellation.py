"""Cancellation during one shutdown step must not skip the remaining cleanups."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_defers_cancellation_and_lets_later_steps_run() -> None:
    from app.main_server import _run_shutdown_step

    ran: list[str] = []

    async def cancelled_step() -> None:
        raise asyncio.CancelledError()

    async def later_step() -> None:
        ran.append("later")

    pending = await _run_shutdown_step(cancelled_step(), what="first")
    assert isinstance(pending, asyncio.CancelledError)

    # The whole point: the next cleanup still runs.
    pending = await _run_shutdown_step(
        later_step(), what="second", pending_cancellation=pending
    )
    assert ran == ["later"]
    assert isinstance(pending, asyncio.CancelledError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_keeps_the_first_cancellation() -> None:
    from app.main_server import _run_shutdown_step

    first = asyncio.CancelledError()

    async def cancelled_step() -> None:
        raise asyncio.CancelledError()

    pending = await _run_shutdown_step(
        cancelled_step(), what="second", pending_cancellation=first
    )
    assert pending is first


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_passes_through_success() -> None:
    from app.main_server import _run_shutdown_step

    async def ok_step() -> None:
        return None

    assert await _run_shutdown_step(ok_step(), what="ok") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_step_clears_the_cancelling_counter() -> None:
    """After absorbing a real cancel, ``cancelling()`` must be back to zero.

    This is not an incidental detail: ``_cancel_task_if_running`` decides whether
    to re-raise by reading exactly this counter
    (``if current is not None and current.cancelling(): raise``). Leave it above
    zero and every later shutdown step that goes through that helper re-raises
    immediately, which is the same skipped-cleanup bug from the other direction.
    """
    from app.main_server import _run_shutdown_step

    entered = asyncio.Event()

    async def shutdown_like() -> int:
        async def blocking_step() -> None:
            entered.set()
            await asyncio.sleep(3600)

        await _run_shutdown_step(blocking_step(), what="blocking")
        current = asyncio.current_task()
        assert current is not None
        return current.cancelling()

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    assert await task == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_later_cancel_helper_still_runs_after_an_absorbed_cancel() -> None:
    """The counter reset must actually keep the *next* helper call working.

    Dual of the test above, at the call site rather than on the predicate: a
    second ``_cancel_task_if_running`` after an absorbed cancellation has to
    complete its own cleanup instead of re-raising straight back out.
    """
    from app.main_server import _cancel_task_if_running, _run_shutdown_step

    ran: list[str] = []
    entered = asyncio.Event()

    async def shutdown_like() -> None:
        async def blocking_step() -> None:
            entered.set()
            await asyncio.sleep(3600)

        pending = await _run_shutdown_step(blocking_step(), what="blocking")

        victim_started = asyncio.Event()

        async def victim() -> None:
            victim_started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                ran.append("victim-stopped")

        victim_task = asyncio.create_task(victim())
        await victim_started.wait()
        await _cancel_task_if_running(victim_task, name="victim", timeout=1.0)
        ran.append("reached-end")
        assert pending is not None

    task = asyncio.create_task(shutdown_like())
    await entered.wait()
    task.cancel()
    await task
    assert ran == ["victim-stopped", "reached-end"]


def _unprotected_shutdown_awaits() -> list[tuple[int, str]]:
    """Every await in ``on_shutdown`` a cancellation can escape from.

    Escape means: not wrapped in ``_run_shutdown_step`` AND not inside a ``try``
    whose handlers catch ``CancelledError``/``BaseException``. ``except
    Exception`` does not count — ``CancelledError`` is a ``BaseException``.
    """
    import ast
    import inspect
    import textwrap

    from app.main_server import on_shutdown

    tree = ast.parse(textwrap.dedent(inspect.getsource(on_shutdown)))
    fn = tree.body[0]
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
        # Every case is decided on entry, so the classification does not depend
        # on which branch recursed into this node.
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            # Nested coroutines own their own cancellation semantics.
            return
        if isinstance(node, ast.Try):
            # Only the body is covered by this try's handlers; code inside the
            # handlers and finally is not.
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


# Pre-existing awaits a cancellation can still escape from. Frozen as a ratchet
# baseline rather than silently fixed: wrapping these touches Cloud Save,
# character release, memory-server and HTTP-pool business branches (two of them
# need the awaited value, so the caller has to grow a cancelled path), and
# whether shutdown should absorb cancellation that far is a product call — the
# direct-run entry point deliberately keeps a 30s watchdog and an os._exit(130)
# on a second signal. The steps this PR touches ARE wrapped; this list must only
# ever shrink.
_KNOWN_CANCELLATION_ESCAPES = frozenset(
    {
        "agent_event_bridge.stop()",
        "close_fn()",
        "asyncio.wait_for(close_all_crawlers(), timeout=1.0)",
        "asyncio.wait_for(asyncio.gather(*(_release_one(n) for n in releasable_names), re",
        "_run_cloudsave_manager_action('upload_existing_snapshot', **upload_action_kwargs",
        "_request_memory_server_shutdown()",
        "asyncio.wait_for(aclose_internal_http_client(), timeout=1.0)",
        "asyncio.wait_for(aclose_external_http_client(), timeout=2.0)",
        "asyncio.wait({knowledge_finish_task}, timeout=knowledge_shutdown_timeout_seconds",
    }
)


@pytest.mark.unit
def test_on_shutdown_adds_no_new_cancellation_escape() -> None:
    """Derive escapes from the AST instead of checking a hand-written call list.

    The first version of this guard named four helpers and asserted none of them
    was awaited bare. That is the list-shaped failure: it passed while nine other
    cleanups were still unwrapped, purely because they were not on the list. This
    version enumerates every escape and diffs against a frozen baseline, so a new
    unwrapped await fails even if nobody remembers to update a list.
    """
    current = {code for _, code in _unprotected_shutdown_awaits()}
    new = current - _KNOWN_CANCELLATION_ESCAPES
    assert not new, (
        "new awaits let a cancellation escape on_shutdown and skip every cleanup "
        "after them; wrap them in _run_shutdown_step:\n"
        + "\n".join(f"  {code}" for code in sorted(new))
    )


@pytest.mark.unit
def test_cancellation_escape_baseline_only_shrinks() -> None:
    """The baseline must not rot: an entry that got fixed has to be removed."""
    current = {code for _, code in _unprotected_shutdown_awaits()}
    stale = _KNOWN_CANCELLATION_ESCAPES - current
    assert not stale, (
        "these awaits are no longer cancellation escapes — drop them from "
        f"_KNOWN_CANCELLATION_ESCAPES so the ratchet keeps its teeth: {sorted(stale)}"
    )
