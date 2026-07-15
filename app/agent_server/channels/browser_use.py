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

"""Browser-use channel: lock-serialized dispatch onto the singleton adapter."""

import uuid
import asyncio

from config import (
    TASK_ERROR_MAX_TOKENS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    EXCEPTION_TEXT_MAX_CHARS,
)
from utils.tokenize import truncate_to_tokens as _tt
from utils.result_parser import (
    parse_browser_use_result,
    _phrase as _rp_phrase,
    _get_lang as _rp_lang,
)
from brain.agent_session import get_session_manager

from .. import _shared
from .._shared import logger
from ..tracker import _task_tracker
from ..registry import _now_iso, _set_internal_correction_context
from ..results import _emit_main_event, _emit_task_result


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the browser-use channel."""
    if _shared.Modules.agent_flags.get("browser_use_enabled", False) and _shared.Modules.browser_use:
        sm = get_session_manager()
        bu_session = sm.get_or_create(None, "browser_use")
        bu_session.add_task(result.task_description)

        bu_task_id = str(uuid.uuid4())
        bu_start = _now_iso()
        bu_info = {
            "id": bu_task_id,
            "type": "browser_use",
            "status": "queued",
            "start_time": bu_start,
            "params": {"instruction": result.task_description},
            "lanlan_name": lanlan_name,
            "session_id": bu_session.session_id,
            "result": None,
            "error": None,
            "_trigger_user_fingerprint": trigger_user_msg_sig,
        }
        _set_internal_correction_context(bu_info, result)
        _shared.Modules.task_registry[bu_task_id] = bu_info
        _task_tracker.record_assigned(
            lanlan_name, task_id=bu_task_id, method="browser_use",
            desc=result.task_description or "",
        )
        try:
            await _emit_main_event(
                "task_update", lanlan_name,
                task={"id": bu_task_id, "status": "queued", "type": "browser_use",
                      "start_time": bu_start, "params": {"instruction": result.task_description},
                      "session_id": bu_session.session_id},
            )
        except Exception as e:
            logger.debug("[BrowserUse] emit task_update(queued) failed: task_id=%s error=%s", bu_task_id, e)
        async def _run_browser_use_dispatch():
            try:
                from utils.instrument import counter as _ic
                _ic("agent_invoked", agent_type="browser")
            except Exception:
                pass  # 埋点 best-effort
            try:
                if _shared.Modules.browser_use_dispatch_lock is None:
                    _shared.Modules.browser_use_dispatch_lock = asyncio.Lock()
                async with _shared.Modules.browser_use_dispatch_lock:
                    # cancel_task may have flipped the entry to
                    # "cancelled" while it sat waiting for the slot —
                    # don't resurrect it (mirrors the computer-use
                    # scheduler's queued guard).
                    if bu_info.get("status") != "queued":
                        return
                    # Recheck the feature flag: the user may have
                    # disabled browser_use while this task waited for
                    # the slot (mirrors the computer-use scheduler's
                    # disabled-drop path).
                    adapter = _shared.Modules.browser_use
                    if (
                        not _shared.Modules.analyzer_enabled
                        or not _shared.Modules.agent_flags.get("browser_use_enabled", False)
                        or adapter is None
                    ):
                        bu_info["status"] = "cancelled"
                        bu_info["end_time"] = _now_iso()
                        bu_info["error"] = "browser_use unavailable before dispatch"
                        # Close out the record_assigned entry; otherwise
                        # the tracker keeps showing [ASSIGNED] and later
                        # analyzer passes treat the same user request as
                        # still in flight instead of retrying it.
                        _task_tracker.record_completed(
                            lanlan_name, task_id=bu_task_id, method="browser_use",
                            desc=result.task_description or "",
                            detail="browser_use unavailable before dispatch",
                            success=False, cancelled=True,
                            trigger_user_fingerprint=trigger_user_msg_sig,
                        )
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": bu_task_id, "status": "cancelled", "type": "browser_use",
                                      "end_time": bu_info["end_time"], "error": bu_info["error"],
                                      "session_id": bu_session.session_id},
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_update(disabled-drop) failed: task_id=%s error=%s", bu_task_id, emit_err)
                        return
                    bu_info["status"] = "running"
                    bu_info["start_time"] = _now_iso()
                    _shared.Modules.active_browser_use_task_id = bu_task_id
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": bu_task_id, "status": "running", "type": "browser_use",
                                  "start_time": bu_info["start_time"],
                                  "params": {"instruction": result.task_description},
                                  "session_id": bu_session.session_id},
                        )
                    except Exception as e:
                        logger.debug("[BrowserUse] emit task_update(running) failed: task_id=%s error=%s", bu_task_id, e)
                    bres = await adapter.run_instruction(
                        result.task_description,
                        session_id=bu_session.session_id,
                    )
                if bu_info.get("status") == "cancelled":
                    # cancel_task set the terminal state before run_instruction
                    # returned (e.g. via fire-and-forget CDP teardown winning
                    # the race against bg.cancel()). Don't clobber it.
                    return
                success = bres.get("success", False) if isinstance(bres, dict) else False
                _bu_ok, bu_parsed = parse_browser_use_result(bres)
                _lang = _rp_lang(None)
                _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
                if bu_parsed:
                    summary = _rp_phrase('cu_task_done', _lang, desc=result.task_description, status=_done, detail=bu_parsed)
                else:
                    summary = _rp_phrase('cu_task_desc_only', _lang, desc=result.task_description, status=_done)
                bu_session.complete_task(bu_parsed or summary, success)
                _task_tracker.record_completed(
                    lanlan_name, task_id=bu_task_id, method="browser_use",
                    desc=result.task_description or "",
                    detail=bu_parsed[:TASK_TRACKER_DETAIL_MAX_CHARS] if bu_parsed else "", success=success,
                )
                bu_info["status"] = "completed" if success else "failed"
                bu_info["end_time"] = _now_iso()
                bu_info["result"] = bres
                if not success:
                    bu_info["error"] = _tt((bu_parsed or ""), TASK_ERROR_MAX_TOKENS)
                await _emit_task_result(
                    lanlan_name,
                    channel="browser_use",
                    task_id=bu_task_id,
                    success=success,
                    summary=summary,
                    detail=bu_parsed if success else "",
                    error_message=bu_parsed if not success else "",
                )
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": bu_task_id, "status": bu_info["status"],
                              "type": "browser_use", "start_time": bu_start, "end_time": _now_iso(),
                              "error": (_tt(bu_parsed, TASK_ERROR_MAX_TOKENS) if bu_parsed else "") if not success else None,
                              "session_id": bu_session.session_id},
                    )
                except Exception as emit_err:
                    logger.debug("[BrowserUse] emit task_update(terminal) failed: task_id=%s error=%s", bu_task_id, emit_err)
            except asyncio.CancelledError as e:
                cancel_msg = str(e)[:EXCEPTION_TEXT_MAX_CHARS] if str(e) else "cancelled"
                bu_info["status"] = "cancelled"
                bu_info["error"] = cancel_msg
                bu_session.complete_task(cancel_msg, success=False)
                _task_tracker.record_completed(
                    lanlan_name, task_id=bu_task_id, method="browser_use",
                    desc=result.task_description or "", detail=cancel_msg[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False, cancelled=True,
                )
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="browser_use",
                        task_id=bu_task_id,
                        success=False,
                        summary=_rp_phrase('bu_cancelled', _rp_lang(None), desc=result.task_description or ''),
                        error_message=cancel_msg,
                    )
                except Exception as emit_err:
                    logger.debug("[BrowserUse] emit task_result(cancelled) failed: task_id=%s error=%s", bu_task_id, emit_err)
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": bu_task_id, "status": "cancelled", "type": "browser_use",
                              "start_time": bu_start, "end_time": _now_iso(),
                              "error": cancel_msg, "session_id": bu_session.session_id},
                    )
                except Exception as emit_err:
                    logger.debug("[BrowserUse] emit task_update(cancelled) failed: task_id=%s error=%s", bu_task_id, emit_err)
                raise
            except Exception as e:
                if bu_info.get("status") == "cancelled":
                    # cancel_task already marked cancelled; treat incidental
                    # errors (e.g. ConnectionError from CDP teardown) as the
                    # cancel signal instead of clobbering with "failed".
                    return
                # exception 字符串可能含用户/LLM 原文，logger 只记元数据
                logger.warning(f"[BrowserUse] Failed (exc_type={type(e).__name__})")
                print(f"[BrowserUse] Task raw error: {e}")
                bu_info["status"] = "failed"
                bu_info["end_time"] = _now_iso()
                _task_tracker.record_completed(
                    lanlan_name, task_id=bu_task_id, method="browser_use",
                    desc=result.task_description or "", detail=str(e)[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False,
                )
                bu_info["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
                bu_session.complete_task(str(e), success=False)
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="browser_use",
                        task_id=bu_task_id,
                        success=False,
                        summary=f'你的任务"{result.task_description}"执行异常',
                        error_message=str(e),
                    )
                except Exception as emit_err:
                    logger.debug("[BrowserUse] emit task_result(failed) failed: task_id=%s error=%s", bu_task_id, emit_err)
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": bu_task_id, "status": "failed", "type": "browser_use",
                              "start_time": bu_start, "end_time": _now_iso(),
                              "error": _tt(str(e), TASK_ERROR_MAX_TOKENS),
                              "session_id": bu_session.session_id},
                    )
                except Exception as emit_err:
                    logger.debug("[BrowserUse] emit task_update(failed) failed: task_id=%s error=%s", bu_task_id, emit_err)
            finally:
                # Only the slot owner may clear it: a queued dispatch
                # cancelled while waiting for the lock must not wipe
                # the running task's id.
                if _shared.Modules.active_browser_use_task_id == bu_task_id:
                    _shared.Modules.active_browser_use_task_id = None

        bu_task = asyncio.create_task(_run_browser_use_dispatch())
        _shared.Modules.task_async_handles[bu_task_id] = bu_task
        _shared.Modules._background_tasks.add(bu_task)
        def _cleanup_bu_task(_t, _tid=bu_task_id):
            _shared.Modules._background_tasks.discard(_t)
            _shared.Modules.task_async_handles.pop(_tid, None)
        bu_task.add_done_callback(_cleanup_bu_task)
    else:
        logger.warning("[BrowserUse] Task requires BrowserUse but it is disabled")
