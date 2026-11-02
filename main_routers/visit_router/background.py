# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Process-level visit tasks wired by ``app/main_server`` (design §5 PR-09b).

* :func:`start_visit_background_tasks` (called once the main server runtime
  is initialized) starts, with ``asyncio.create_task`` and never awaited on
  the startup path, the visit sweep (``runtime.visit_sweep_loop``) and the
  startup recovery of visit files (``recovery.visit_spool_recovery``) with
  the runtime's callbacks injected.
* :func:`stop_visit_background_tasks` is the front of ``on_shutdown``:
  stop the sweep and the recovery, then ``runtime.stop_all('shutdown')``
  within ``VISIT_SHUTDOWN_BUDGET_S``.

Both run whatever the ``NEKO_VISIT_ENABLED`` release switch says: the
switch only closes the entry points, recovery and uploads keep working.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from config.visit_settings import VISIT_SHUTDOWN_BUDGET_S
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_sweep_task: Optional[asyncio.Task] = None
_recovery_task: Optional[asyncio.Task] = None
_invite_reset_event: Optional[asyncio.Event] = None
_invite_reset_error: Optional[Exception] = None
_invite_reset_retry: Optional[asyncio.Task] = None
_RECOVERY_STOP_WAIT_S = 0.2
_INVITE_RESET_TIMEOUT_S = 15.0
_STOP_ALL_SLACK_S = 0.5  # stop_all bounds each step with VISIT_SHUTDOWN_BUDGET_S; the outer timeout only catches hangs, leaving cleanup margin.

SHUTDOWN_STEP_BUDGET_S = VISIT_SHUTDOWN_BUDGET_S + 2 * _RECOVERY_STOP_WAIT_S + _STOP_ALL_SLACK_S + 0.3
"""Deadline of the whole visit shutdown step in ``on_shutdown`` (``stop_all`` plus the two bounded waits)."""


async def _family_names() -> tuple[str, ...]:
    from main_routers.visit_router.local_context import load_character_context

    try:
        return tuple((await load_character_context()).family_names)
    except Exception as exc:  # noqa: BLE001 - If reading fails, retain family names (the summary merely loses one protection list).
        logger.warning("visit recovery: family names unreadable: %s", type(exc).__name__)
        return ()


def _character_config_lock() -> Any:
    # Late binding: tests replace this module-level lock.
    from utils import character_memory

    return character_memory.character_config_mutation_lock


async def run_startup_recovery() -> Any:
    """One startup recovery pass with the runtime's callbacks (the body of the recovery task)."""
    from config.visit_settings import VISIT_LAST_SUMMARY_MAX_TOKENS, VISIT_LLM_TIMEOUT_S
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.recovery import visit_spool_recovery
    from main_routers.visit_router import character_hooks, runtime, transcript_upload
    from main_routers.visit_router.debrief import render_chips, resume_diary_commit
    from main_routers.visit_router.llm import one_shot_llm

    config_dir = runtime.runtime_deps().config_dir()
    report = await visit_spool_recovery(
        render_chips,
        transcript_upload.upload_visit_transcript,
        is_live=runtime.is_visit_live,
        spawn_background=runtime.spawn_visit_background,
        config_dir=config_dir,
        summary_llm=one_shot_llm(max_tokens=VISIT_LAST_SUMMARY_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S),
        family_names=await _family_names(),
        submit_report=transcript_upload.submit_queued_report,
        retry_later=transcript_upload.schedule_visit_retry,
        void_pending=default_void_pending(config_dir),
        # For sessions in the two-step diary write, schedule backoff retries using persisted next_at.
        resume_diary_commit=resume_diary_commit,
        # Guard against renaming/deletion during clear replay, shared with the clear endpoint.
        lifecycle_guard=runtime.hold_character_lifecycle,
        # Complete retired deletions by removing visit personas; assess each item under the character-config mutation lock, mutually exclusive with deletion transactions.
        retire_persona=character_hooks.retire_persona,
        config_lock=_character_config_lock,
    )
    logger.info("visit recovery done: crashed=%d chips=%d swept=%d", len(report.crashed), len(report.chips),
                report.swept)
    return report


def _unfinished_reset() -> Exception:
    from main_routers.visit_router.credentials import VisitServersUnreachable

    return VisitServersUnreachable("startup_invite_reset")


async def _reset_pending_invites() -> Optional[Exception]:
    """One bounded invalidation of the previous run's unused invitations; returns the failure, None once done."""
    try:
        from main_routers.visit_router.credentials import VisitServersError, cancel_pending_host_invites

        # asyncio.timeout, not wait_for: 3.11's wait_for drops a shutdown cancel that lands as the
        # reset completes, and the recovery would then keep running past stop_all.
        async with asyncio.timeout(_INVITE_RESET_TIMEOUT_S):
            cancelled = await cancel_pending_host_invites()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - Returned to the admission fence, which retries it; never blocks recovery.
        logger.warning("visit invitation reset failed: %s", type(exc).__name__)
        # Servers errors (login required, banned, unreachable) carry the copy the
        # frontend shows; a timeout or anything else reads as an unfinished reset.
        return exc if isinstance(exc, VisitServersError) else _unfinished_reset()
    if cancelled:
        logger.info("visit startup invalidated %d pending invitation(s)", cancelled)
    return None


async def _retry_invite_reset(event: asyncio.Event) -> Optional[Exception]:
    """Admission retry of a failed startup reset; records success before finishing.

    Clearing the failure inside the task (not in a waiter resumed later) closes the
    gap where a new admission would see the finished retry plus the stale failure
    and start another bulk cancel while the first caller is already minting a room.
    """
    global _invite_reset_error
    error = await _reset_pending_invites()
    if error is None and _invite_reset_event is event:
        _invite_reset_error = None
    return error


async def _recover_in_background() -> None:
    global _invite_reset_error
    event = _invite_reset_event
    error: Optional[Exception] = _unfinished_reset()
    try:
        error = await _reset_pending_invites()
    finally:
        # A cancelled rollback task can retire after a later startup already
        # installed a new fence.  Never publish the old attempt into that run.
        if _invite_reset_event is event:
            _invite_reset_error = error
        if event is not None:
            event.set()
    try:
        await run_startup_recovery()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - Failed backfill leaves files for the next startup.
        logger.error("visit recovery failed: %r", exc)


def start_visit_background_tasks() -> None:
    """Start the visit sweep and the startup recovery (``create_task``; never awaited by startup)."""
    global _sweep_task, _recovery_task, _invite_reset_event, _invite_reset_error
    from main_routers.visit_router import runtime

    if _sweep_task is None or _sweep_task.done():
        _sweep_task = asyncio.create_task(runtime.visit_sweep_loop(), name="visit-sweep")
    if _recovery_task is None or _recovery_task.done():
        _invite_reset_event = asyncio.Event()
        _invite_reset_error = None
        _recovery_task = asyncio.create_task(_recover_in_background(), name="visit-recovery")


async def wait_startup_invite_reset() -> Optional[Exception]:
    """Admission fence: wait for the startup invite invalidation (not spool recovery).

    Returns None once the reset is done, else the Servers error to answer with.
    A failed startup reset (signed out, offline, unverifiable session) is
    retried here -- one attempt shared by concurrent callers -- instead of
    refusing every room until the next restart.  Nothing can mint a room
    while the reset is unfinished, so a late retry never cancels a fresh invite.
    """
    global _invite_reset_retry
    event = _invite_reset_event
    if event is None:
        return None
    if not event.is_set():
        await event.wait()
    if _invite_reset_event is not event:
        return _unfinished_reset()
    if _invite_reset_error is None:
        return None
    retry = _invite_reset_retry
    if retry is None or retry.done():
        retry = asyncio.create_task(_retry_invite_reset(event), name="visit-invite-reset-retry")
        _invite_reset_retry = retry
    try:
        error = await asyncio.shield(retry)
    except asyncio.CancelledError:
        if not retry.cancelled():
            raise
        return _unfinished_reset()
    if _invite_reset_event is not event:
        return _unfinished_reset()
    return error


def startup_recovery_running() -> bool:
    """Whether the startup recovery task is still running (it reconciles pending renames under their guards)."""
    return _recovery_task is not None and not _recovery_task.done()


def _cancel(task: Optional[asyncio.Task]) -> None:
    if task is not None and not task.done():
        task.cancel()


async def stop_visit_background_tasks() -> None:
    """Shutdown: stop the sweep, ``stop_all`` within ``VISIT_SHUTDOWN_BUDGET_S``, cancel the recovery."""
    global _sweep_task, _recovery_task, _invite_reset_event, _invite_reset_error, _invite_reset_retry
    from main_routers.visit_router import runtime

    sweep, recovery = _sweep_task, _recovery_task
    _sweep_task = _recovery_task = None
    if _invite_reset_event is not None:
        _invite_reset_event.set()
    _invite_reset_event = None
    _invite_reset_error = None
    _cancel(_invite_reset_retry)
    _invite_reset_retry = None
    _cancel(sweep)  # Timing no longer runs concurrently with shutdown finalization.
    # Stop backfill first and resume unfinished work next startup; otherwise it can spawn digest/summary writes
    # after stop_all snapshots background tasks, escaping shutdown cancellation.
    _cancel(recovery)
    if recovery is not None and not recovery.done():
        await asyncio.wait([recovery], timeout=_RECOVERY_STOP_WAIT_S)
    try:
        # Sharing stop_all's budget externally would time out when one shutdown() uses it all, truncating stop_all's remaining
        # digest/summary background-write cancellation; catch only hangs and leave margin.
        await asyncio.wait_for(runtime.stop_all("shutdown"), VISIT_SHUTDOWN_BUDGET_S + _STOP_ALL_SLACK_S)
    except Exception as exc:  # noqa: BLE001 - Only log timeouts/errors; leave unfinalized work for next startup's backfill.
        logger.warning("visit shutdown: stop_all did not finish: %r", exc)
    # Transcript reuploads/report retries are independent tasks outside stop_all; cancel at shutdown, retaining incomplete files for next startup.
    from main_routers.visit_router import transcript_upload

    workers = transcript_upload.cancel_retry_workers()
    # Also cancel diary backoff retries; unfinished state remains in state.json for next startup to reschedule by next_at.
    from main_routers.visit_router import debrief

    workers = list(workers or []) + debrief.cancel_commit_timers()
    if workers:
        await asyncio.wait(workers, timeout=_RECOVERY_STOP_WAIT_S)


def cancel_visit_background_tasks() -> None:
    """Startup rollback: cancel both tasks and the upload retries recovery scheduled (no visit is live yet)."""
    global _sweep_task, _recovery_task, _invite_reset_event, _invite_reset_error, _invite_reset_retry
    from main_routers.visit_router import transcript_upload

    from main_routers.visit_router import runtime

    _cancel(_sweep_task)
    _cancel(_recovery_task)
    _sweep_task = _recovery_task = None
    if _invite_reset_event is not None:
        _invite_reset_event.set()
    _invite_reset_event = None
    _invite_reset_error = None
    _cancel(_invite_reset_retry)
    _invite_reset_retry = None
    # Backfill's transcript reuploads/report retries and its spawned digest/summary writes are independent tasks; do not let them keep uploading
    # or modifying spool after rollback and overlap the next initialization.
    transcript_upload.cancel_retry_workers()
    from main_routers.visit_router import debrief

    debrief.cancel_commit_timers()
    runtime.cancel_background_writes()
