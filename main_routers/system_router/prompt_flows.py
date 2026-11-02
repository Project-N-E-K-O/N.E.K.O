# -*- coding: utf-8 -*-
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

"""Seven-day tutorial and autostart prompt state endpoints.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _read_json_object, _validate_local_mutation_request, router
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import get_config_manager
from utils.autostart_prompt_state import (
    get_autostart_prompt_state_response,
    process_autostart_prompt_heartbeat,
    record_autostart_prompt_shown,
    record_autostart_prompt_decision,
)
from utils.seven_day_tutorial_state import (
    SevenDayTutorialStateConflict,
    get_seven_day_tutorial_state_response,
    replace_seven_day_tutorial_state,
)
from utils.click_guide_state import get_click_guide_state, update_click_guide_state

_SEVEN_DAY_SUBMIT_LOCK = asyncio.Lock()
_AUTOSTART_SUBMIT_LOCK = asyncio.Lock()
_CLICK_GUIDE_SUBMIT_LOCK = asyncio.Lock()


def _consume_detached_operation_result(task: asyncio.Task) -> None:
    """Retrieve a detached operation's exception after its waiter is cancelled."""
    if not task.cancelled():
        task.exception()


async def _run_serialized_in_worker(lock: asyncio.Lock, func, /, *args, **kwargs):
    """Queue one state-family operation before it consumes an executor worker."""
    # The sync helpers also serialize on a threading.RLock. Submitting every
    # concurrent request first would make all waiters occupy default-executor
    # slots while only one can progress. asyncio.Lock suspends waiters without
    # blocking the event loop; the worker is allocated only to the active call.
    submitted = asyncio.Event()

    async def _run_owned_operation():
        async with lock:
            submitted.set()
            return await asyncio.to_thread(func, *args, **kwargs)

    # The child owns both queue position and submit-lock lifetime. Shielding it
    # lets a cancelled HTTP waiter leave promptly without releasing the lock
    # while its non-cancellable worker thread is still running.
    operation = asyncio.create_task(_run_owned_operation())
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # But only work already running is worth preserving. The tasks still waiting on the lock have nothing uncancellable,
        # and keeping them would only perform stale reads/writes after the client has already left; one slow
        # file operation plus repeated client timeouts/retries can build an unbounded queue that blocks live requests.
        #
        # This check is atomic: there is no yield between submitted.set() and the await after it, so
        # at this point the child task is either waiting at `async with lock` (not set, safe to cancel), or waiting in
        # to_thread (set, cancellation would release the lock early — exactly what shield must prevent). There is no intermediate state.
        if not submitted.is_set():
            operation.cancel()
        operation.add_done_callback(_consume_detached_operation_result)
        raise


@router.get("/seven-day-tutorial/state")
async def get_seven_day_tutorial_state():
    """Return the authoritative Day 1-7 tutorial progress."""
    # This GET also needs to move out of the loop, for two reasons:
    # 1) It is not read-only — if the store is not initialized, load_seven_day_tutorial_store calls
    #    _migrate_legacy_tutorial_state, which performs an atomic_write_json with fsync
    #    (the first progress fetch after an upgrade by an existing user will always hit it).
    # 2) More importantly, it acquires _STATE_LOCK on the loop thread, the same lock as the PUT below. The PUT has already moved to a
    #    worker, and file_utils' protection against backoff on the event loop is checked **per thread**: the worker
    #    retries the 155 ms backoff on Windows busy, while holding the RLock. As long as the loop
    #    thread also tries to acquire this lock, the backoff flows back through the lock to the event loop. Move both operations to break this path.
    return await _run_serialized_in_worker(
        _SEVEN_DAY_SUBMIT_LOCK,
        get_seven_day_tutorial_state_response,
        config_manager=get_config_manager(),
    )


@router.put("/seven-day-tutorial/state")
async def put_seven_day_tutorial_state(request: Request):
    """Replace the Day 1-7 tutorial progress after a verified local mutation."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error
    try:
        # The entire "read revision — compare — persist" operation runs under the helper's _STATE_LOCK (threading.RLock)
        # internally, and the critical section does not cross an await, so moving the whole call to a thread introduces no race; concurrent requests queue on that
        # RLock in the worker thread, and the event loop is no longer blocked by atomic_write_json's fsync.
        store = await _run_serialized_in_worker(
            _SEVEN_DAY_SUBMIT_LOCK,
            replace_seven_day_tutorial_state,
            payload.get("state"),
            expected_revision=payload.get("expectedRevision"),
            config_manager=get_config_manager(),
        )
    except SevenDayTutorialStateConflict as exc:
        return JSONResponse(status_code=409, content={
            "ok": False,
            "error_code": "seven_day_tutorial_revision_conflict",
            **exc.current_store,
        })
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    return {"ok": True, **store}


@router.get("/click-guide/state")
async def get_click_guide():
    return await _run_serialized_in_worker(
        _CLICK_GUIDE_SUBMIT_LOCK, get_click_guide_state, config_manager=get_config_manager(),
    )


@router.post("/click-guide/state")
async def post_click_guide(request: Request):
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error
    try:
        result = await _run_serialized_in_worker(
            _CLICK_GUIDE_SUBMIT_LOCK, update_click_guide_state, payload,
            config_manager=get_config_manager(),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    return JSONResponse(status_code=200 if result["ok"] else 409, content=result)


@router.get("/autostart-prompt/state")
async def get_autostart_prompt_state():
    """Return a snapshot of the autostart prompt state."""
    # Same reason and fix as the seven-day GET: when only the legacy file remains, load_autostart_prompt_state
    # writes once via save_autostart_prompt_state (utils/prompt_state/autostart.py:236),
    # and holds _AUTOSTART_STATE_LOCK on the loop thread — the three POSTs below have moved to a worker,
    # but if this one does not move, the backoff will flow through this lock and block the event loop for 155 ms.
    return await _run_serialized_in_worker(
        _AUTOSTART_SUBMIT_LOCK,
        get_autostart_prompt_state_response,
        config_manager=get_config_manager(),
    )


@router.post("/autostart-prompt/heartbeat")
async def post_autostart_prompt_heartbeat(request: Request):
    """Record homepage idle and interaction state, and decide whether to prompt about autostart."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    # Same as above: reading state, computing eligibility, and persisting all happen under _AUTOSTART_STATE_LOCK (threading.RLock)
    # internally, and the critical section does not cross an await. The frontend polls this endpoint, and each request may trigger an atomic_write_json,
    # so it must move out of the event loop.
    return await _run_serialized_in_worker(
        _AUTOSTART_SUBMIT_LOCK,
        process_autostart_prompt_heartbeat,
        payload,
        config_manager=get_config_manager(),
    )


@router.post("/autostart-prompt/shown")
async def post_autostart_prompt_shown(request: Request):
    """Record that the autostart prompt was actually shown to the user."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        # Same as above: the entire read-modify-write runs under _AUTOSTART_STATE_LOCK, so moving it to a thread is safe.
        return await _run_serialized_in_worker(
            _AUTOSTART_SUBMIT_LOCK,
            record_autostart_prompt_shown,
            payload,
            config_manager=get_config_manager(),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/autostart-prompt/decision")
async def post_autostart_prompt_decision(request: Request):
    """Record the user's decision on the autostart prompt."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        # Same as above: the entire read-modify-write runs under _AUTOSTART_STATE_LOCK, so moving it to a thread is safe.
        return await _run_serialized_in_worker(
            _AUTOSTART_SUBMIT_LOCK,
            record_autostart_prompt_decision,
            payload,
            config_manager=get_config_manager(),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
