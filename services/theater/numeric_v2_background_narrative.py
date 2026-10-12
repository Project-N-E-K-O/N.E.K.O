"""Bounded ledger work after reviewed delivery; a pending tail never blocks play indefinitely."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
import time

from .numeric_v2_narrative import make_narrative_record
from .numeric_v2_narrative_proposer import NumericV2NarrativeProposer
from .numeric_v2_usage import numeric_v2_usage_scope
from .numeric_v2_trace import text_trace_scope, trace_event


logger = logging.getLogger(__name__)
NEXT_INPUT_WAIT_SECONDS = 1.0
BACKGROUND_BUDGET_SECONDS = 24.0
_TASKS: dict[tuple[str, str], asyncio.Task] = {}


def _key(runtime, stored):
    return (str(runtime.store.root.resolve()), stored.session.session_id)


def schedule_narrative(*, config_manager, runtime, previous, stored, before_commit=None):
    """Retain the task until completion; the durable pending marker survives interruption."""
    key = _key(runtime, stored)

    async def work():
        record = None
        try:
            async with asyncio.timeout(BACKGROUND_BUDGET_SECONDS):
                worker = NumericV2NarrativeProposer(config_manager)
                performance = deepcopy(stored.session.performance_history[-1])
                performance.pop("narrative_record", None)
                message = stored.ledger_events[-1]["input_text"]
                performance["narrative_events"] = await worker.propose(
                    performance=performance, player_input=message, session=previous.session,
                )
                review = await worker.review(
                    performance=performance, player_input=message, session=previous.session,
                )
                record = make_narrative_record(performance, message, review,
                    state=previous.session.narrative_state)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No candidate text or private history in ordinary diagnostics.
            logger.warning("Numeric v2 background narrative failed: revision=%s", stored.session.revision)
        try:
            async with runtime.story_session_guard():
                if before_commit is not None:
                    await before_commit()
                await runtime.store.settle_narrative(stored, record)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Numeric v2 background narrative settlement failed: revision=%s", stored.session.revision)
            # A failed disk fence leaves the durable marker for the next input.

    async def run():
        started = time.monotonic()
        # A background call must not mutate the already returned HTTP usage list
        # or append to a closed foreground trace inherited by create_task.
        with numeric_v2_usage_scope() as usage, text_trace_scope(
            "background_narrative", revision=stored.session.revision,
        ):
            try:
                await work()
            finally:
                trace_event("background_narrative.finished", usage=usage,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 3))

    task = asyncio.create_task(run(), name="theater-narrative")
    _TASKS[key] = task

    def done(completed):
        if _TASKS.get(key) is completed:
            _TASKS.pop(key, None)

    task.add_done_callback(done)


async def settle_before_input(runtime, current):
    """Wait briefly, then seal an interrupted/slow job as a gap and reload the snapshot."""
    if not current.ledger_events or "narrative_pending" not in current.ledger_events[-1]:
        return current
    task = _TASKS.get(_key(runtime, current))
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), NEXT_INPUT_WAIT_SECONDS)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # Another input may have cancelled this same background job.
    # Also handles a process restart, failed job, or externally cancelled job.
    await runtime.store.settle_narrative(current, None)
    refreshed = await runtime.restore_session(current.session.session_id)
    if refreshed is None:
        from .numeric_v2_store import NumericV2SessionNotFoundError
        raise NumericV2SessionNotFoundError("numeric_session_not_found")
    return refreshed
