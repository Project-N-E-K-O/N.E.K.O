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

"""User-plugin channel: dispatch onto the embedded plugin server plus the
plugin result semantics helpers (terminal status / delivery mode /
llm_result_fields lookup)."""

import time
import asyncio
from typing import Dict, Any, Optional

from config import (
    TASK_ERROR_MAX_TOKENS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    EXCEPTION_TEXT_MAX_CHARS,
)
from utils.tokenize import truncate_to_tokens as _tt
from utils.result_parser import parse_plugin_result

from .. import _shared
from .._shared import logger, DEFERRED_TASK_TIMEOUT
from ..tracker import _task_tracker
from ..registry import _now_iso
from ..plugin_host import (
    _bind_deferred_task,
    _get_plugin_friendly_name,
    _get_plugin_display_id,
)
from ..results import _emit_main_event, _emit_task_result


def _plugin_terminal_status(success: bool, run_data: Any) -> str:
    """Return terminal status for a plugin run.

    Default: ``"completed"`` on raw protocol success, ``"failed"`` otherwise.
    On raw success, plugins may downgrade to ``"blocked"`` / ``"failed"`` via
    explicit ``run_data`` signals:

    - ``status == "error"``                                                → "failed"
    - ``needs_confirmation=True`` / ``action == "clarify"`` /
      ``status ∈ {"blocked","clarify","confirm_required"}``                → "blocked"
    - ``observation_only=True`` bypasses the override (treated as completed)

    Raw protocol failure (``success=False``) always returns "failed" regardless
    of ``run_data`` — run_data must not be allowed to "upgrade" a failure to a
    softer status like "blocked".

    ``executed=False`` alone is intentionally NOT enough to mark blocked. Many
    plugins use it to mean "no game-side action played" while the control op
    itself succeeded (e.g. STS2 ``stop_autoplay`` returns ``status="idle",
    executed=False`` after a real stop). Inferring blocked from that would
    misreport successful control operations.
    """
    if not success:
        return "failed"
    if isinstance(run_data, dict) and not bool(run_data.get("observation_only")):
        status = str(run_data.get("status") or "").strip().lower()
        action = str(run_data.get("action") or "").strip().lower()
        if status == "error":
            return "failed"
        if bool(run_data.get("needs_confirmation")) or action == "clarify" or status in {"blocked", "clarify", "confirm_required"}:
            return "blocked"
    return "completed"


def _resolve_delivery_mode(result: Optional[Dict]) -> str:
    """Return the effective delivery mode declared by a plugin's finish envelope.

    Reads ``result.meta.agent.delivery`` (canonical, three-state string) with
    fallback to legacy ``result.meta.agent.reply`` (bool). Returns one of
    ``"proactive" | "passive" | "silent"``. Default = ``"proactive"`` (the
    main AI is interrupted to announce the result).

    Priority: when ``agent.delivery`` is present (any value, valid or not) it
    owns the decision — invalid values fall back to ``"proactive"`` rather
    than letting ``agent.reply`` quietly override. This avoids
    ``delivery="typo", reply=False`` silently flipping to ``"silent"``.
    Mirrors :func:`plugin.sdk.shared.core.finish.normalize_delivery`.
    """
    if not isinstance(result, dict):
        return "proactive"
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return "proactive"
    agent = meta.get("agent")
    if not isinstance(agent, dict):
        return "proactive"
    if "delivery" in agent:
        raw = agent["delivery"]
        if isinstance(raw, str) and raw in ("proactive", "passive", "silent"):
            return raw
        if isinstance(raw, bool):
            return "proactive" if raw else "silent"
        # delivery key was set but invalid — don't fall through to reply.
        return "proactive"
    reply_obj = agent.get("reply")
    if isinstance(reply_obj, bool):
        return "proactive" if reply_obj else "silent"
    return "proactive"


def _lookup_llm_result_fields(plugin_id: str, entry_id: Optional[str]) -> Optional[list]:
    """Look up the llm_result_fields declaration of the given entry in plugin_list."""
    try:
        plugins = getattr(_shared.Modules.task_executor, "plugin_list", None) or []
        for p in plugins:
            if not isinstance(p, dict) or p.get("id") != plugin_id:
                continue
            for e in p.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                if e.get("id") == entry_id:
                    fields = e.get("llm_result_fields")
                    return list(fields) if isinstance(fields, list) else None
            break
    except Exception as e:
        logger.debug("_lookup_llm_result_fields failed: plugin_id=%s entry_id=%s error=%s", plugin_id, entry_id, e)
    return None


def _is_reply_suppressed(result: Optional[Dict]) -> bool:
    """Backward-compat shim: returns True iff delivery mode is "silent".

    Prefer :func:`_resolve_delivery_mode` for new code — it returns the full
    three-state value.
    """
    return _resolve_delivery_mode(result) == "silent"


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the user-plugin channel."""
    # Dispatch: 与 CU/BU 一致，由 agent_server 统一调度执行
    if _shared.Modules.agent_flags.get("user_plugin_enabled", False) and _shared.Modules.task_executor:
        plugin_id = result.tool_name
        plugin_args = result.tool_args or {}
        entry_id = result.entry_id
        up_start = _now_iso()
        # 获取插件友好名称（用于 HUD 显示）
        plugin_name = await _get_plugin_friendly_name(plugin_id)
        logger.info(
            "[TaskExecutor] Dispatching UserPlugin: plugin_id=%s, entry_id=%s, plugin_name=%s",
            plugin_id, entry_id, plugin_name,
        )
        # 构建任务参数（包含友好名称）
        task_params = {"plugin_id": plugin_id, "entry_id": entry_id}
        if plugin_name:
            task_params["plugin_name"] = plugin_name
        if result.task_description:
            task_params["description"] = result.task_description
        # Register in task_registry (mirrors CU _spawn_task) so GET /tasks can recover on refresh
        _shared.Modules.task_registry[result.task_id] = {
            "id": result.task_id,
            "type": "user_plugin",
            "status": "running",
            "start_time": up_start,
            "params": task_params,
            "lanlan_name": lanlan_name,
            "result": None,
            "error": None,
            "_trigger_user_fingerprint": trigger_user_msg_sig,
        }
        # 记录任务分派（供后续 analyzer 去重）
        _task_tracker.record_assigned(
            lanlan_name,
            task_id=result.task_id,
            method="user_plugin",
            desc=f"{plugin_id}.{entry_id}: {result.task_description or ''}",
        )
        # Emit task_update (running) so AgentHUD shows a running card
        try:
            _initial_task_payload: Dict[str, Any] = {
                "id": result.task_id, "status": "running", "type": "user_plugin",
                "start_time": up_start,
                "params": task_params,
            }
            await _emit_main_event("task_update", lanlan_name, task=_initial_task_payload)
        except Exception as emit_err:
            logger.debug("[TaskExecutor] emit task_update(running) failed: task_id=%s plugin_id=%s error=%s", result.task_id, plugin_id, emit_err)
        async def _on_plugin_progress(
            *, progress=None, stage=None, message=None, step=None, step_total=None,
        ):
            """Forward run progress updates to NEKO frontend via task_update."""
            # If cancel_task already flipped the registry to a terminal
            # state, a late progress callback would otherwise clobber
            # "cancelled" with a fresh "running" update on the HUD.
            _reg = _shared.Modules.task_registry.get(result.task_id)
            if _reg and _reg.get("status") != "running":
                return
            task_payload: Dict[str, Any] = {
                "id": result.task_id, "status": "running", "type": "user_plugin",
                "start_time": up_start,
                "params": task_params,
            }
            if progress is not None:
                task_payload["progress"] = progress
            if stage is not None:
                task_payload["stage"] = stage
            if message is not None:
                task_payload["message"] = message
            if step is not None:
                task_payload["step"] = step
            if step_total is not None:
                task_payload["step_total"] = step_total
            await _emit_main_event("task_update", lanlan_name, task=task_payload)

        async def _run_user_plugin_dispatch():
            try:
                from utils.instrument import counter as _ic
                # agent_invoked 只按 agent_type 分，保持单 key 即"plugin
                # 总计"——本地 admin 视图 get_top_counters 按完整 metric_key
                # GROUP BY、不做 dim 聚合，若把 plugin_id 塞进这里会把该
                # 总计行打散成 per-plugin 行、丢掉聚合。per-plugin 细分另发
                # 独立指标 plugin_invoked，其全量之和恒等于本行，互不重复
                # 计数。plugin_id 基数由已安装插件数限定，截断兜底防异常长
                # id 撑爆 counter key 空间。
                _ic("agent_invoked", agent_type="plugin")
                _ic("plugin_invoked", plugin_id=str(plugin_id or "unknown")[:48])
            except Exception:
                pass  # 埋点 best-effort，不阻塞 plugin 分派
            # Default delivery mode; overridden after the plugin result
            # is parsed below. Cancel / exception branches read this so
            # they honor whatever the plugin already declared, not a
            # hard-coded "proactive" — see _resolve_delivery_mode call.
            _delivery_mode = "proactive"
            try:
                up_result = await _shared.Modules.task_executor._execute_user_plugin(
                    task_id=result.task_id,
                    plugin_id=plugin_id,
                    plugin_args=plugin_args if isinstance(plugin_args, dict) else None,
                    entry_id=entry_id,
                    task_description=result.task_description,
                    reason=result.reason,
                    lanlan_name=lanlan_name,
                    conversation_id=conversation_id,
                    latest_user_request=getattr(result, "latest_user_request", "") or "",
                    on_progress=_on_plugin_progress,
                )
                run_data = up_result.result.get("run_data") if isinstance(up_result.result, dict) else None
                run_error = up_result.result.get("run_error") if isinstance(up_result.result, dict) else None
                _llm_fields = _lookup_llm_result_fields(plugin_id, entry_id)
                _plugin_msg = str(up_result.result.get("message") or "") if isinstance(up_result.result, dict) else ""
                _error_to_pass = (run_error or up_result.error) if not up_result.success else None
                detail = parse_plugin_result(
                    run_data,
                    llm_result_fields=_llm_fields,
                    plugin_message=_plugin_msg,
                    error=_error_to_pass,
                )
                up_terminal = _plugin_terminal_status(up_result.success, run_data)
                # Resolve plugin's declared delivery mode (proactive/passive/silent).
                # silent → skip task_result emit entirely; the rest reach
                # main_server which routes proactive vs passive scheduling.
                _delivery_mode = _resolve_delivery_mode(up_result.result if isinstance(up_result.result, dict) else None)
                _suppress_reply = _delivery_mode == "silent"
                # 检查插件是否返回 deferred 标志（如备忘提醒：调度成功但提醒尚未触发）
                is_deferred = isinstance(run_data, dict) and run_data.get("deferred") is True
                # Update task_registry（deferred 任务保持 running，不写 terminal 状态）
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg and _reg.get("status") == "cancelled":
                    # cancel_task pre-marked cancelled; don't clobber with a late terminal write.
                    return
                if _reg and not (up_result.success and is_deferred):
                    _reg["status"] = up_terminal
                    _reg["end_time"] = _now_iso()
                    _reg["result"] = up_result.result
                    if up_terminal != "completed":
                        _reg["error"] = _tt((detail or str(up_result.error or "")), TASK_ERROR_MAX_TOKENS)
                if up_result.success and is_deferred:
                    # 保持任务为 running 状态，等待 daemon 触发后回调完成
                    reminder_id = run_data.get("reminder_id") if isinstance(run_data, dict) else None
                    logger.info("[Deferred] Task %s kept running, reminder_id=%s", result.task_id, reminder_id)
                    # 设置超时，防止绑定失败导致任务永远卡在 running
                    if _reg:
                        _reg["deferred_timeout"] = time.time() + DEFERRED_TASK_TIMEOUT
                    if reminder_id:
                        # 在线程中执行（含 HTTP 轮询，避免阻塞事件循环）
                        loop = asyncio.get_event_loop()
                        loop.run_in_executor(None, _bind_deferred_task, plugin_id, reminder_id, result.task_id)
                    # 不进入后续 completed/failed 流程
                elif up_result.success:
                    _completed = up_terminal == "completed"
                    _task_tracker.record_completed(
                        lanlan_name, task_id=result.task_id, method="user_plugin",
                        desc=f"{plugin_id}.{entry_id}: {result.task_description or ''}",
                        detail=detail or "", success=_completed,
                    )
                    if _completed:
                        logger.info(f"[TaskExecutor] ✅ UserPlugin completed: {plugin_id}")
                    else:
                        logger.info(f"[TaskExecutor] ⚠️ UserPlugin did not execute: {plugin_id}")
                    if not _suppress_reply:
                        display_id = await _get_plugin_display_id(plugin_id)
                        # summary is now plain detail; the LLM-facing
                        # i18n wrap (来自插件「X」的任务{status}…) lives
                        # in main_logic via SYSTEM_NOTIFICATION_PROACTIVE
                        # + SOURCE_DESCRIPTORS + TASK_STATUS_PHRASES.
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="user_plugin",
                                task_id=str(up_result.task_id or ""),
                                success=_completed,
                                summary=detail,
                                detail=detail,
                                direct_reply=False,
                                status=None if _completed else up_terminal,
                                source_kind="plugin",
                                source_name=display_id,
                                delivery_mode=_delivery_mode,
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_result(success) failed: task_id=%s plugin_id=%s error=%s", up_result.task_id, plugin_id, emit_err)
                else:
                    _task_tracker.record_completed(
                        lanlan_name, task_id=result.task_id, method="user_plugin",
                        desc=f"{plugin_id}.{entry_id}: {result.task_description or ''}",
                        detail=detail or str(up_result.error or ""), success=False,
                    )
                    logger.warning(f"[TaskExecutor] ❌ UserPlugin failed: {up_result.error}")
                    if not _suppress_reply:
                        try:
                            display_id = await _get_plugin_display_id(plugin_id)
                            _err_text = (detail or str(up_result.error or "")).strip()
                            # summary 不再套 plugin_failed_with；状态由
                            # main_logic 的外层 SYSTEM_NOTIFICATION_PROACTIVE
                            # （+ status="failed" → "执行失败"）表达。
                            # 显式传 status="failed"，否则 _emit_task_result
                            # 看到 success=False + 非空 detail 会默认推到
                            # "partial"，把单纯失败误标成"部分完成"。
                            await _emit_task_result(
                                lanlan_name,
                                channel="user_plugin",
                                task_id=str(up_result.task_id or ""),
                                success=False,
                                summary=_err_text,
                                detail=_err_text,
                                error_message=_err_text,
                                status="failed",
                                source_kind="plugin",
                                source_name=display_id,
                                delivery_mode=_delivery_mode,
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_result(failed) failed: task_id=%s plugin_id=%s error=%s", up_result.task_id, plugin_id, emit_err)
                # Emit task_update (terminal) — deferred 任务跳过，保持 running
                if not (up_result.success and is_deferred):
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": result.task_id, "status": up_terminal, "type": "user_plugin",
                                  "start_time": up_start, "end_time": _now_iso(),
                                  "params": task_params,
                                  "error": _tt((detail or str(up_result.error or "")), TASK_ERROR_MAX_TOKENS) if up_terminal != "completed" else None},
                        )
                    except Exception as emit_err:
                        logger.debug("[TaskExecutor] emit task_update(terminal) failed: task_id=%s plugin_id=%s error=%s", result.task_id, plugin_id, emit_err)
            except asyncio.CancelledError as e:
                cancel_msg = str(e)[:EXCEPTION_TEXT_MAX_CHARS] if str(e) else "cancelled"
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg:
                    _reg["status"] = "cancelled"
                    _reg["error"] = cancel_msg
                _task_tracker.record_completed(
                    lanlan_name, task_id=result.task_id, method="user_plugin",
                    desc=f"{plugin_id}.{entry_id}: {result.task_description or ''}",
                    detail=cancel_msg[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False, cancelled=True,
                )
                # Honor plugin's resolved delivery mode if it had a chance
                # to run before cancel; default to "proactive" otherwise.
                # silent → skip the emit entirely (matches success path).
                if _delivery_mode != "silent":
                    try:
                        display_id = await _get_plugin_display_id(plugin_id)
                        await _emit_task_result(
                            lanlan_name,
                            channel="user_plugin",
                            task_id=str(result.task_id or ""),
                            success=False,
                            summary=cancel_msg,
                            detail=cancel_msg,
                            error_message=cancel_msg,
                            status="cancelled",
                            source_kind="plugin",
                            source_name=display_id,
                            delivery_mode=_delivery_mode,
                        )
                    except Exception as emit_err:
                        logger.debug("[TaskExecutor] emit task_result(cancelled) failed: task_id=%s error=%s", result.task_id, emit_err)
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": result.task_id, "status": "cancelled", "type": "user_plugin",
                              "start_time": up_start, "end_time": _now_iso(),
                              "params": task_params,
                              "error": cancel_msg},
                    )
                except Exception as emit_err:
                    logger.debug("[TaskExecutor] emit task_update(cancelled) failed: task_id=%s error=%s", result.task_id, emit_err)
                raise
            except Exception as e:
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg and _reg.get("status") == "cancelled":
                    return
                # exception 字符串可能含用户/LLM 原文，logger 只记元数据
                logger.error("[TaskExecutor] UserPlugin dispatch failed (exc_type=%s)", type(e).__name__)
                print(f"[TaskExecutor] UserPlugin dispatch raw error: {e}")
                if _reg:
                    _reg["status"] = "failed"
                    _reg["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
                _task_tracker.record_completed(
                    lanlan_name, task_id=result.task_id, method="user_plugin",
                    desc=f"{plugin_id}.{entry_id}: {result.task_description or ''}",
                    detail=str(e)[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False,
                )
                # Honor plugin's resolved delivery mode (if any); silent
                # plugins stay silent even on dispatch exception.
                if _delivery_mode != "silent":
                    try:
                        display_id = await _get_plugin_display_id(plugin_id)
                        _exc_text = str(e)[:EXCEPTION_TEXT_MAX_CHARS]
                        await _emit_task_result(
                            lanlan_name,
                            channel="user_plugin",
                            task_id=str(result.task_id or ""),
                            success=False,
                            summary=_exc_text,
                            detail=_exc_text,
                            error_message=_exc_text,
                            status="failed",
                            source_kind="plugin",
                            source_name=display_id,
                            delivery_mode=_delivery_mode,
                        )
                    except Exception as emit_err:
                        logger.debug("[TaskExecutor] emit task_result(dispatch_failed) failed: task_id=%s error=%s", result.task_id, emit_err)
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": result.task_id, "status": "failed", "type": "user_plugin",
                              "start_time": up_start, "end_time": _now_iso(),
                              "params": task_params,
                              "error": _tt(str(e), TASK_ERROR_MAX_TOKENS)},
                    )
                except Exception as emit_err:
                    logger.debug("[TaskExecutor] emit task_update(dispatch_failed) failed: task_id=%s error=%s", result.task_id, emit_err)

        up_task = asyncio.create_task(_run_user_plugin_dispatch())
        _shared.Modules.task_async_handles[result.task_id] = up_task
        _shared.Modules._background_tasks.add(up_task)
        def _cleanup_up_task(_t, _tid=result.task_id):
            _shared.Modules._background_tasks.discard(_t)
            _shared.Modules.task_async_handles.pop(_tid, None)
        up_task.add_done_callback(_cleanup_up_task)
    else:
        logger.warning("[UserPlugin] ⚠️ Task requires UserPlugin but it's disabled")
