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

"""Computer-use channel: exclusive scheduler loop, task runner and dispatch."""

import asyncio

from config import TASK_DETAIL_MAX_TOKENS, TASK_ERROR_MAX_TOKENS
from utils.tokenize import truncate_to_tokens as _tt
from utils.result_parser import (
    parse_computer_use_result,
    _phrase as _rp_phrase,
    _get_lang as _rp_lang,
)

from .. import _shared
from .._shared import logger
from ..tracker import _task_tracker
from ..registry import (
    _now_iso,
    _spawn_task,
    _is_duplicate_task,
    _set_internal_correction_context,
)
from ..results import _emit_main_event, _emit_task_result


async def _run_computer_use_task(
    task_id: str,
    instruction: str,
) -> None:
    """Run a computer-use task in a thread pool; emit results directly via ZeroMQ."""
    # Telemetry: count usage by agent type (cua/browser/plugin/openclaw),
    # showing which agents are actually used and how much. Best-effort; never block agent execution.
    try:
        from utils.instrument import counter as _ic
        _ic("agent_invoked", agent_type="cua")
    except Exception:
        pass  # Best-effort instrumentation must not block cua tasks
    info = _shared.Modules.task_registry.get(task_id, {})
    lanlan_name = info.get("lanlan_name")

    # Mark running
    info["status"] = "running"
    info["start_time"] = _now_iso()
    _shared.Modules.computer_use_running = True
    _shared.Modules.active_computer_use_task_id = task_id

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={
                "id": task_id, "status": "running", "type": "computer_use",
                "start_time": info["start_time"], "params": info.get("params", {}),
            },
        )
    except Exception as e:
        logger.debug("[ComputerUse] emit task_update(running) failed: task_id=%s error=%s", task_id, e)

    # Execute in thread pool (run_instruction is synchronous/blocking)
    success = False
    cu_detail = ""
    loop = asyncio.get_running_loop()

    try:
        if _shared.Modules.computer_use is None or not hasattr(_shared.Modules.computer_use, "run_instruction"):
            success = False
            cu_detail = "ComputerUse adapter is inactive or invalid (e.g., reset)"
            info["error"] = cu_detail
            logger.error("[ComputerUse] Task %s aborted: %s", task_id, cu_detail)
        else:
            future = loop.run_in_executor(None, _shared.Modules.computer_use.run_instruction, instruction)
            res = await future
            if res is None:
                logger.debug("[ComputerUse] run_instruction returned None, treating as success")
                res = {"success": True}
            elif isinstance(res, dict) and "success" not in res:
                res["success"] = True
            success = bool(res.get("success", False))
            info["result"] = res
            _cu_ok, cu_detail = parse_computer_use_result(res)
    except asyncio.CancelledError:
        info["error"] = "Task was cancelled"
        logger.info("[ComputerUse] Task %s was cancelled", task_id)
        # The underlying thread may still be running — wait for it to finish
        # so we don't start a new task while pyautogui is still active.
        cu = _shared.Modules.computer_use
        if cu is not None and hasattr(cu, "wait_for_completion"):
            finished = await loop.run_in_executor(None, cu.wait_for_completion, 15.0)
            if not finished:
                logger.warning("[ComputerUse] Thread did not stop within 15s after cancel")
    except Exception as e:
        info["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
        # Exception strings often contain user input / model output / raw upstream responses;
        # logger records only task_id + exc_type metadata, with original text falling back to print.
        logger.error("[ComputerUse] Task %s failed (exc_type=%s)", task_id, type(e).__name__)
        print(f"[ComputerUse] Task {task_id} raw error: {e}")
    finally:
        # On exceptions, run_instruction() raises directly, leaving cu_detail empty,
        # while info["error"] already contains exception text. Copy info["error"] into
        # cu_detail so all three downstream outputs (summary / detail / error_message) receive
        # the failure reason (frontend task_update / task_result and analyzer all depend on
        # this; previously failed + error_message="" left the frontend without details).
        if not cu_detail and info.get("error"):
            cu_detail = info["error"]
        # cancel_task may have pre-marked status="cancelled" before this dispatch
        # observed the cancellation; preserve that signal regardless of whether
        # the CU thread returned normally or raised CancelledError.
        if info.get("status") == "cancelled":
            pass  # already cancelled by cancel_task
        elif info.get("error") == "Task was cancelled":
            info["status"] = "cancelled"
        else:
            info["status"] = "completed" if success else "failed"
        # If the CU thread managed to return normally *after* cancel_task flipped
        # the registry, keep the downstream task_update / task_result consistent:
        # force success=False so the emits below don't mix status="cancelled"
        # with success=True / error=None.
        if info.get("status") == "cancelled":
            success = False
        info["end_time"] = _now_iso()
        # Record task completion for analyzer deduplication
        _task_tracker.record_completed(
            lanlan_name, task_id=task_id, method="computer_use",
            desc=instruction or "",
            detail=_tt(cu_detail, TASK_DETAIL_MAX_TOKENS) if cu_detail else "",
            success=success and info["status"] != "cancelled",
            cancelled=(info["status"] == "cancelled"),
        )
        # On failure, copy parsed cu_detail into info["error"] (fill only on non-exception paths)
        if not success and not info.get("error") and cu_detail:
            info["error"] = _tt(cu_detail, TASK_ERROR_MAX_TOKENS)
        _shared.Modules.computer_use_running = False
        _shared.Modules.active_computer_use_task_id = None
        _shared.Modules.active_computer_use_async_task = None

        # Emit task_update (terminal state)
        try:
            task_obj = asyncio.create_task(_emit_main_event(
                "task_update", lanlan_name,
                task={
                    "id": task_id, "status": info["status"], "type": "computer_use",
                    "start_time": info.get("start_time"), "end_time": _now_iso(),
                    "error": info.get("error") if not success else None,
                },
            ))
            _shared.Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(_shared.Modules._background_tasks.discard)
        except Exception as e:
            logger.debug("[ComputerUse] emit task_update(terminal) failed: task_id=%s error=%s", task_id, e)

        # Emit structured task_result
        try:
            _lang = _rp_lang(None)
            _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
            params = info.get("params") or {}
            desc = params.get("query") or params.get("instruction") or ""
            if cu_detail and desc:
                summary = _rp_phrase('cu_task_done', _lang, desc=desc, status=_done, detail=cu_detail)
            elif cu_detail:
                summary = _rp_phrase('cu_task_done_no_desc', _lang, status=_done, detail=cu_detail)
            elif desc:
                summary = _rp_phrase('cu_task_desc_only', _lang, desc=desc, status=_done)
            else:
                summary = _rp_phrase('cu_done', _lang) if success else _rp_phrase('cu_fail', _lang)
            task_obj = asyncio.create_task(_emit_task_result(
                lanlan_name,
                channel="computer_use",
                task_id=task_id,
                success=success,
                summary=summary,
                detail=cu_detail if success else "",
                error_message=cu_detail if not success else "",
            ))
            _shared.Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(_shared.Modules._background_tasks.discard)
        except Exception as e:
            logger.debug("[ComputerUse] emit task_result failed: task_id=%s error=%s", task_id, e)


async def _computer_use_scheduler_loop():
    """Ensure only one computer-use task runs at a time by scheduling queued tasks."""
    if _shared.Modules.computer_use_queue is None:
        _shared.Modules.computer_use_queue = asyncio.Queue()
    while True:
        try:
            # Event-driven: block until a task is pushed. Producers (_spawn_task)
            # put_nowait from async contexts on the same loop, so get() wakes
            # immediately — no polling needed.
            next_task = await _shared.Modules.computer_use_queue.get()
            # Await the preceding CU task before checking flags, covering users disabling CU through
            # /agent/flags during the await; otherwise it would still dispatch after being disabled.
            if _shared.Modules.computer_use_running and _shared.Modules.active_computer_use_async_task is not None:
                try:
                    await _shared.Modules.active_computer_use_async_task
                except Exception as e:
                    # The preceding CU task's exceptions were already handled/logged by _run_computer_use_task's finally;
                    # this only guards unexpected propagation, keeping the scheduler alive for the next task.
                    logger.debug("[ComputerUse] prior task raised on await: %s", e)
            if not _shared.Modules.analyzer_enabled or not _shared.Modules.agent_flags.get("computer_use_enabled", False):
                # Explicitly mark queued tasks cancelled and emit task_update; otherwise the registry retains
                # zombie "queued" entries, corrupting duplicate detection and UI display.
                dropped = [next_task]
                while not _shared.Modules.computer_use_queue.empty():
                    try:
                        dropped.append(_shared.Modules.computer_use_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                now_iso = _now_iso()
                for entry in dropped:
                    tid = entry.get("task_id") if isinstance(entry, dict) else None
                    if not tid:
                        continue
                    reg = _shared.Modules.task_registry.get(tid)
                    if reg is None or reg.get("status") not in ("queued", None):
                        continue
                    reg["status"] = "cancelled"
                    reg["end_time"] = now_iso
                    reg["error"] = "computer_use disabled before dispatch"
                    lanlan_name = reg.get("lanlan_name")
                    asyncio.create_task(_emit_main_event(
                        "task_update", lanlan_name,
                        task={
                            "id": tid, "status": "cancelled", "type": "computer_use",
                            "end_time": now_iso, "error": reg["error"],
                        },
                    ))
                continue
            tid = next_task.get("task_id")
            if not tid or tid not in _shared.Modules.task_registry:
                continue
            # If cancel_task already flipped the entry to "cancelled" (or any
            # non-queued terminal state) while it was still sitting in the
            # queue, don't resurrect it — otherwise _run_computer_use_task
            # would reset status back to "running" and the cancel is lost.
            reg = _shared.Modules.task_registry.get(tid, {})
            if reg.get("status") != "queued":
                continue
            _shared.Modules.active_computer_use_async_task = asyncio.create_task(_run_computer_use_task(
                tid, next_task.get("instruction", ""),
            ))
        except Exception:
            # Never crash the scheduler
            await asyncio.sleep(0.1)


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the computer-use channel."""
    if _shared.Modules.agent_flags.get("computer_use_enabled", False):
        # Check for duplicates
        dup, matched = await _is_duplicate_task(result.task_description, lanlan_name)
        if not dup:
            ti = _spawn_task("computer_use", {"instruction": result.task_description, "screenshot": None})
            ti["lanlan_name"] = lanlan_name
            ti["_trigger_user_fingerprint"] = trigger_user_msg_sig
            _set_internal_correction_context(ti, result)
            _task_tracker.record_assigned(
                lanlan_name, task_id=ti["id"], method="computer_use",
                desc=result.task_description or "",
            )
            # task_description is raw user/LLM content; never put it in logger, use local print fallback
            logger.info(f"[ComputerUse] Scheduled task {ti['id']} (desc_len={len(result.task_description or '')})")
            print(f"[ComputerUse] task {ti['id']} description: {(result.task_description or '')[:120]}")
            try:
                await _emit_main_event(
                    "task_update",
                    lanlan_name,
                    task={
                        "id": ti.get("id"),
                        "status": ti.get("status"),
                        "type": ti.get("type"),
                        "start_time": ti.get("start_time"),
                        "params": ti.get("params", {}),
                    },
                )
            except Exception as e:
                logger.debug("[ComputerUse] emit task_update(running) failed: task_id=%s error=%s", ti.get('id'), e)
        else:
            logger.info(f"[ComputerUse] Duplicate task detected, matched with {matched}")
    else:
        logger.warning("[ComputerUse] ⚠️ Task requires ComputerUse but it's disabled")
