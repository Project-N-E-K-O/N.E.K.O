"""Bound cleanup waits and preserve cancellation across independent shutdown steps."""

import asyncio
import logging
import time

default_logger = logging.getLogger(__name__)
SHUTDOWN_STEP_TASKS: set[asyncio.Task[object]] = set()


class ShutdownCancellationBudget:
    """One clock shared by all steps after the first caller cancellation."""

    def __init__(self, seconds: float = 8.0) -> None:
        self.seconds = seconds
        self.deadline: float | None = None

    def start(self) -> None:
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds

    def clamp(self, deadline: float | None) -> float | None:
        if self.deadline is None:
            return deadline
        return self.deadline if deadline is None else min(deadline, self.deadline)


def _consume_shutdown_task_result(task: asyncio.Task[object]) -> None:
    # Retrieve the outcome so asyncio does not log "exception was never
    # retrieved" for a step whose failure _run_shutdown_step already reported.
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass  # The outcome was already reported; only mark it as retrieved.


def _forget_shutdown_step(task: asyncio.Task[object]) -> None:
    SHUTDOWN_STEP_TASKS.discard(task)
    _consume_shutdown_task_result(task)


def _log_abandoned_shutdown_step_outcome(what: str, logger):
    """Report how a step ended after _run_shutdown_step stopped waiting on it."""

    def _report(task: asyncio.Task[object]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "%s failed after shutdown stopped waiting for it: %s", what, exc
            )

    return _report


async def run_shutdown_step(
    factory,
    *,
    what: str,
    deadline_monotonic: float | None,
    pending_cancellation: asyncio.CancelledError | None = None,
    cancelled_budget_seconds: float = 0.0,
    cancellation_budget: ShutdownCancellationBudget | None = None,
    cancel_grace_seconds: float = 1.0,
    logger=default_logger,
) -> asyncio.CancelledError | None:
    """Run one cleanup and defer caller cancellation to the end.

    Shutdown is a sequence of independent cleanups (connector threads, game
    tasks, integration workers, ZMQ bridge, Cloud Save, HTTP pools). A
    cancellation delivered while any one of them is awaited must not skip the
    rest, so each step absorbs it here and the caller re-raises once, after
    everything else has run. ``except Exception`` does NOT cover this:
    ``CancelledError`` is a ``BaseException``.

    The cleanup runs in a strongly referenced child task observed through
    ``asyncio.shield``, so cancelling ``on_shutdown`` cannot cancel the cleanup
    it is currently waiting on. Each delivered caller cancellation is consumed
    exactly once (``uncancel`` keeps ``cancelling()`` at zero for later steps)
    and the first instance is returned for the caller to re-raise. A step that
    misses its deadline is cancelled and, like ``asyncio.wait_for`` did before,
    given a short grace period to actually stop before the next step runs; a
    step that fails is logged. Neither is raised, because raising would drop a
    cancellation this helper already absorbed.

    ``deadline_monotonic=None`` waits for the step's own terminal state, for
    work that cancelling the await cannot stop (a worker thread). Once the
    caller has cancelled -- before this step or while it is awaited -- nothing
    else would end that wait, so the step then gets ``cancelled_budget_seconds``
    from that moment as its deadline (0 means it is not started, or is
    cancelled right away).

    When supplied, ``cancellation_budget`` is shared across the shutdown's
    steps. Its clock starts on the first caller cancellation; both step waits
    and cancellation grace are capped by it. Later cancellations do not reset
    it. Exhaustion skips remaining asynchronous steps. This bounds cooperative
    waits, not synchronous cleanup or process exit (worker threads may linger).
    Consuming cancellation and re-raising at the end is deliberate; callers
    must not rely on the task's ``cancelling()`` count retaining those requests.
    """
    if cancellation_budget is not None and pending_cancellation is not None:
        cancellation_budget.start()
    if deadline_monotonic is None and pending_cancellation is not None:
        deadline_monotonic = time.monotonic() + cancelled_budget_seconds
    if cancellation_budget is not None:
        deadline_monotonic = cancellation_budget.clamp(deadline_monotonic)
    if deadline_monotonic is not None and deadline_monotonic - time.monotonic() <= 0:
        logger.warning("%s skipped: shutdown deadline already passed", what)
        return pending_cancellation
    try:
        task = asyncio.create_task(factory(), name=f"shutdown:{what}")
    except Exception as exc:
        logger.warning("%s failed during shutdown: %s", what, exc)
        return pending_cancellation
    SHUTDOWN_STEP_TASKS.add(task)
    task.add_done_callback(_forget_shutdown_step)
    timed_out = False
    step_cancel_grace_seconds = cancel_grace_seconds
    while not task.done():
        remaining = None
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                if timed_out:
                    break
                timed_out = True
                logger.warning(
                    "%s exceeded its shutdown deadline; cancelling it", what
                )
                task.cancel()
                cancel_started = time.monotonic()
                deadline_monotonic = (
                    cancel_started + step_cancel_grace_seconds
                )
                if cancellation_budget is not None:
                    deadline_monotonic = cancellation_budget.clamp(deadline_monotonic)
                cancel_grace_seconds = max(0.0, deadline_monotonic - cancel_started)
                continue
        try:
            async with asyncio.timeout(remaining):
                await asyncio.shield(task)
        except TimeoutError:
            # The loop head decides between cancelling and giving up.
            pass
        except asyncio.CancelledError as exc:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                current.uncancel()
                if pending_cancellation is None:
                    pending_cancellation = exc
                if deadline_monotonic is None:
                    # The caller's cancel is the only thing that bounds this
                    # wait; give the step its cancelled budget from now on.
                    deadline_monotonic = time.monotonic() + cancelled_budget_seconds
                if cancellation_budget is not None:
                    cancellation_budget.start()
                    deadline_monotonic = cancellation_budget.clamp(deadline_monotonic)
                logger.debug(
                    "%s observed caller cancellation; waiting for it to finish",
                    what,
                )
            # Otherwise the step was cancelled (by itself or by the deadline);
            # the task is done and the loop exits to report it below.
        except Exception:
            # The step itself failed; the task is done and is reported below.
            pass
    if not task.done():
        logger.warning(
            "%s did not stop within %.1fs after cancellation; moving on",
            what,
            cancel_grace_seconds,
        )
        task.add_done_callback(_log_abandoned_shutdown_step_outcome(what, logger))
    elif task.cancelled():
        if not timed_out:
            logger.warning("%s cancelled itself during shutdown", what)
    elif (step_exc := task.exception()) is not None:
        logger.warning("%s failed during shutdown: %s", what, step_exc)
    return pending_cancellation
