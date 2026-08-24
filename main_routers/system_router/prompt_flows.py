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

_SEVEN_DAY_SUBMIT_LOCK = asyncio.Lock()
_AUTOSTART_SUBMIT_LOCK = asyncio.Lock()


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
        # 但只有「worker 已经在跑」才值得保住。还排在锁上的那些没有任何不可取消的
        # 东西，留着它们只会在客户端早就走了之后再去做一次陈旧的读/写；前面一次慢
        # 文件操作 + 客户端反复超时重试，就能攒出一条无界队列挡住还活着的请求。
        #
        # 这个判断是原子的：submitted.set() 和它后面那个 await 之间没有让出点，所以
        # 此刻子任务要么挂在 `async with lock`（没 set，取消安全），要么挂在
        # to_thread（已 set，取消会提前放锁 —— 正是 shield 要防的）。不存在中间态。
        if not submitted.is_set():
            operation.cancel()
        operation.add_done_callback(_consume_detached_operation_result)
        raise


@router.get("/seven-day-tutorial/state")
async def get_seven_day_tutorial_state():
    """Return the authoritative Day 1-7 tutorial progress."""
    # 这个 GET 也要挪出循环，两个理由：
    # 1) 它不是纯读 —— load_seven_day_tutorial_store 在 store 还没 initialized 时会
    #    走 _migrate_legacy_tutorial_state，那里面是一次带 fsync 的 atomic_write_json
    #    （老用户升级后第一次拉进度必然命中）。
    # 2) 更要紧的是它在循环线程上 acquire 与下面 PUT 相同的 _STATE_LOCK。PUT 已经挪进
    #    worker，而 file_utils 的「事件循环上绝不退避」保护是**按线程**判断的：worker
    #    里撞上 Windows busy 会重新启用那 155ms 退避，且是持着 RLock 睡的。只要循环
    #    线程还会去抢这把锁，退避就会经由锁重新传回循环。两边都挪走，这条路径才真的断。
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
        # 整个「读 revision — 比对 — 落盘」都在 helper 内部的 _STATE_LOCK（threading.RLock）
        # 里完成，临界区不跨 await，所以把整次调用挪进线程不会引入竞态；并发请求由那把
        # RLock 在工作线程上排队，事件循环不再被 atomic_write_json 的 fsync 堵住。
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


@router.get("/autostart-prompt/state")
async def get_autostart_prompt_state():
    """Return a snapshot of the autostart prompt state."""
    # 与 seven-day 的 GET 同因同治：load_autostart_prompt_state 在只剩 legacy 文件时
    # 会落一次 save_autostart_prompt_state（utils/prompt_state/autostart.py:236），
    # 而且它在循环线程上持 _AUTOSTART_STATE_LOCK —— 下面三个 POST 已挪进 worker，
    # 这一条不挪，退避就会经由这把锁把 155ms 传回事件循环。
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

    # 同上：读状态、算 eligibility、落盘全在 _AUTOSTART_STATE_LOCK（threading.RLock）
    # 内部完成，临界区不跨 await。这个端点是前端轮询的，每次可能触发一次 atomic_write_json，
    # 必须挪出事件循环。
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
        # 同上：读—改—写整段在 _AUTOSTART_STATE_LOCK 内，挪进线程安全。
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
        # 同上：读—改—写整段在 _AUTOSTART_STATE_LOCK 内，挪进线程安全。
        return await _run_serialized_in_worker(
            _AUTOSTART_SUBMIT_LOCK,
            record_autostart_prompt_decision,
            payload,
            config_manager=get_config_manager(),
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
