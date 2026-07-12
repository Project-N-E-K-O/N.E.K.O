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

"""OpenClaw channel: dispatch, magic commands, /stop cancellation and the
bounded enable probe plus its reason-code helpers."""

import json
import asyncio
from typing import Any, Optional

from config import (
    AGENT_HISTORY_TURNS,
    TASK_ERROR_MAX_TOKENS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    EXCEPTION_TEXT_MAX_CHARS,
    ERROR_MESSAGE_MAX_CHARS,
    USER_NOTIFICATION_REASON_MAX_CHARS,
)
from utils.tokenize import truncate_to_tokens as _tt
from utils.result_parser import _phrase as _rp_phrase, _get_lang as _rp_lang

from .. import _shared
from .._shared import (
    logger,
    OPENCLAW_ENABLE_CHECK_ATTEMPTS,
    OPENCLAW_ENABLE_CHECK_INTERVAL,
    _set_capability,
    _bump_state_revision,
)
from ..tracker import _task_tracker
from ..registry import _now_iso, _tracker_desc_for_task_info
from ..results import _emit_main_event, _emit_task_result
from ..capabilities import _emit_agent_status_update


def _default_openclaw_task_description() -> str:
    return _rp_phrase('openclaw_processing', _rp_lang(None))


def _resolve_openclaw_sender_id(messages: list[dict[str, Any]] | None) -> str:
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages[-AGENT_HISTORY_TURNS:]):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue

        candidates: list[Any] = [
            message.get("sender_id"),
            message.get("user_id"),
        ]
        for container_key in ("meta", "metadata", "_ctx"):
            container = message.get(container_key)
            if isinstance(container, dict):
                candidates.extend([
                    container.get("sender_id"),
                    container.get("user_id"),
                ])

        for candidate in candidates:
            resolved = str(candidate or "").strip()
            if resolved:
                return resolved
    return ""


def _collect_active_openclaw_task_ids(
    *,
    sender_id: Optional[str] = None,
    lanlan_name: Optional[str] = None,
    exclude_task_id: Optional[str] = None,
) -> list[str]:
    task_ids: list[str] = []
    for task_id, info in _shared.Modules.task_registry.items():
        if task_id == exclude_task_id or not isinstance(info, dict):
            continue
        if info.get("type") != "openclaw":
            continue
        if info.get("status") not in {"queued", "running"}:
            continue
        if sender_id and str(info.get("sender_id") or "").strip() != str(sender_id).strip():
            continue
        if lanlan_name and str(info.get("lanlan_name") or "").strip() != str(lanlan_name).strip():
            continue
        task_ids.append(task_id)
    return task_ids


async def _cancel_openclaw_tasks_for_stop(
    *,
    sender_id: Optional[str],
    lanlan_name: Optional[str],
    exclude_task_id: Optional[str] = None,
) -> list[str]:
    cancelled_task_ids: list[str] = []
    for task_id in _collect_active_openclaw_task_ids(
        sender_id=sender_id,
        lanlan_name=lanlan_name,
        exclude_task_id=exclude_task_id,
    ):
        info = _shared.Modules.task_registry.get(task_id)
        if not isinstance(info, dict):
            continue

        bg = _shared.Modules.task_async_handles.get(task_id)
        if bg and not bg.done():
            bg.cancel()

        if _shared.Modules.openclaw:
            try:
                stop_result = await _shared.Modules.openclaw.stop_running(
                    sender_id=info.get("sender_id"),
                    session_id=info.get("session_id"),
                    conversation_id=info.get("session_id"),
                    role_name=info.get("lanlan_name"),
                    task_id=task_id,
                )
                if not stop_result.get("success"):
                    logger.warning(
                        "[OpenClaw] stop_running failed during /stop for %s: %s",
                        task_id,
                        stop_result.get("error"),
                    )
            except Exception as exc:
                logger.warning("[OpenClaw] stop_running failed during /stop for %s: %s", task_id, exc)

        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
        info["end_time"] = _now_iso()
        cancelled_task_ids.append(task_id)
        _task_tracker.record_completed(
            info.get("lanlan_name"),
            task_id=task_id,
            method="openclaw",
            desc=_tracker_desc_for_task_info(info),
            detail="Cancelled by user",
            success=False,
            cancelled=True,
            trigger_user_fingerprint=info.get("_trigger_user_fingerprint"),
        )

        # Let the task coroutine emit the cancelled update when it is still
        # alive; only emit here when there is no active background handle.
        if not (bg and not bg.done()):
            try:
                await _emit_main_event(
                    "task_update",
                    info.get("lanlan_name"),
                    task={
                        "id": task_id,
                        "status": "cancelled",
                        "type": "openclaw",
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"),
                        "params": info.get("params", {}),
                        "error": "Cancelled by user",
                    },
                )
            except Exception:
                logger.debug("[OpenClaw] emit task_update(cancelled by /stop) failed: task_id=%s", task_id, exc_info=True)

    return cancelled_task_ids


def _openclaw_pending() -> bool:
    task = getattr(_shared.Modules, "openclaw_enable_task", None)
    return bool(task and not task.done())


def _cancel_openclaw_enable_probe() -> None:
    _shared.Modules.openclaw_enable_seq += 1
    task = getattr(_shared.Modules, "openclaw_enable_task", None)
    if task and not task.done():
        task.cancel()
    _shared.Modules.openclaw_enable_task = None


def _openclaw_first_reason(reasons: Any) -> str:
    if isinstance(reasons, list) and reasons:
        return str(reasons[0] or "").strip()
    return str(reasons or "").strip()


def _openclaw_reason_code(reasons: Any) -> str:
    reason = _openclaw_first_reason(reasons)
    if not reason:
        return "AGENT_OPENCLAW_UNAVAILABLE"
    if reason.startswith("AGENT_"):
        return reason

    lower = reason.lower()
    if "pending" in lower or "未检查" in reason:
        return "AGENT_PRECHECK_PENDING"
    if "module not loaded" in lower or "adapter 未加载" in lower or "模块未加载" in reason:
        return "AGENT_OPENCLAW_MODULE_NOT_LOADED"
    if (
        "unavailable" in lower
        or "connect" in lower
        or "connection" in lower
        or "timeout" in lower
        or "timed out" in lower
        or "refused" in lower
        or "连接" in reason
    ):
        return "AGENT_CONNECTIVITY_FAILED"
    return "AGENT_OPENCLAW_UNAVAILABLE"


def _openclaw_reason_text(reasons: Any) -> str:
    reason = _openclaw_first_reason(reasons) or "unknown"
    display_reasons = {
        "AGENT_OPENCLAW_MODULE_NOT_LOADED": "module not loaded",
        "AGENT_OPENCLAW_UNAVAILABLE": "OpenClaw service unavailable",
        "AGENT_PRECHECK_PENDING": "connectivity check pending",
        "AGENT_CONNECTIVITY_FAILED": "OpenClaw service connection failed",
    }
    reason = display_reasons.get(reason, reason)
    reason = reason.replace("OpenClaw(QwenPaw)", "OpenClaw").replace("QwenPaw", "OpenClaw service")
    return reason[:USER_NOTIFICATION_REASON_MAX_CHARS] if reason else "unknown"


def _openclaw_notification(code: str, reasons: Any) -> str:
    reason = _openclaw_reason_text(reasons)
    return json.dumps({
        "code": code,
        "details": {"reason": reason, "reason_code": _openclaw_reason_code(reasons)},
    })


def _start_openclaw_enable_probe(lanlan_name: Optional[str]) -> None:
    adapter = _shared.Modules.openclaw
    if not adapter:
        _cancel_openclaw_enable_probe()
        _shared.Modules.agent_flags["openclaw_enabled"] = False
        _set_capability("openclaw", False, "AGENT_OPENCLAW_MODULE_NOT_LOADED")
        _shared.Modules.notification = json.dumps({"code": "AGENT_OPENCLAW_MODULE_NOT_LOADED"})
        return

    _cancel_openclaw_enable_probe()
    _shared.Modules.agent_flags["openclaw_enabled"] = True
    _set_capability("openclaw", False, "AGENT_PRECHECK_PENDING")
    _shared.Modules.notification = json.dumps({"code": "AGENT_OPENCLAW_ENABLED_CHECKING"})
    task = asyncio.create_task(_run_openclaw_enable_probe(_shared.Modules.openclaw_enable_seq, lanlan_name))
    _shared.Modules.openclaw_enable_task = task
    _shared.Modules._persistent_tasks.add(task)
    task.add_done_callback(_shared.Modules._persistent_tasks.discard)


async def _run_openclaw_enable_probe(seq: int, lanlan_name: Optional[str]) -> None:
    last_reasons: list[str] = []
    try:
        for attempt in range(OPENCLAW_ENABLE_CHECK_ATTEMPTS):
            if seq != _shared.Modules.openclaw_enable_seq or not _shared.Modules.agent_flags.get("openclaw_enabled"):
                return
            adapter = _shared.Modules.openclaw
            if not adapter:
                last_reasons = ["AGENT_OPENCLAW_MODULE_NOT_LOADED"]
                break

            status = await asyncio.to_thread(adapter.is_available)
            ready = bool(status.get("ready")) if isinstance(status, dict) else False
            last_reasons = status.get("reasons", []) if isinstance(status, dict) else []
            status_code = status.get("status_code") if isinstance(status, dict) else None
            if ready:
                _set_capability("openclaw", True, "")
                logger.info("[Agent] OpenClaw(QwenPaw) ready after enable probe attempt %s", attempt + 1)
                _bump_state_revision()
                await _emit_agent_status_update(lanlan_name=lanlan_name)
                return

            auth_error_codes = getattr(adapter, "AUTH_ERROR_STATUS_CODES", frozenset({401, 403}))
            if status_code in auth_error_codes:
                break
            if attempt < OPENCLAW_ENABLE_CHECK_ATTEMPTS - 1:
                await asyncio.sleep(OPENCLAW_ENABLE_CHECK_INTERVAL)

        if seq == _shared.Modules.openclaw_enable_seq and _shared.Modules.agent_flags.get("openclaw_enabled"):
            _shared.Modules.agent_flags["openclaw_enabled"] = False
            _set_capability("openclaw", False, _openclaw_reason_text(last_reasons))
            _shared.Modules.notification = _openclaw_notification("AGENT_OPENCLAW_UNAVAILABLE", last_reasons)
            logger.warning("[Agent] Cannot enable OpenClaw: %s", last_reasons)
            _bump_state_revision()
            await _emit_agent_status_update(lanlan_name=lanlan_name)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if seq == _shared.Modules.openclaw_enable_seq and _shared.Modules.agent_flags.get("openclaw_enabled"):
            reason = f"OpenClaw(QwenPaw) check failed: {exc}"
            _shared.Modules.agent_flags["openclaw_enabled"] = False
            _set_capability("openclaw", False, reason)
            _shared.Modules.notification = _openclaw_notification("AGENT_OPENCLAW_UNAVAILABLE", [reason])
            logger.warning("[Agent] OpenClaw enable probe failed: %s", exc)
            _bump_state_revision()
            await _emit_agent_status_update(lanlan_name=lanlan_name)


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the OpenClaw channel."""
    if _shared.Modules.agent_flags.get("openclaw_enabled", False) and _shared.Modules.openclaw:
        nk_start = _now_iso()
        instruction = ""
        attachments = []
        magic_command = None
        direct_reply = False
        if isinstance(result.tool_args, dict):
            instruction = str(result.tool_args.get("instruction") or "")
            attachments = result.tool_args.get("attachments") or []
            magic_command = _shared.Modules.openclaw.normalize_magic_command(result.tool_args.get("magic_command"))
            direct_reply = bool(result.tool_args.get("direct_reply"))
        task_params = {
            "description": result.task_description or _default_openclaw_task_description(),
            "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
        }
        if magic_command:
            task_params["magic_command"] = magic_command
        nk_sender_id = _resolve_openclaw_sender_id(messages) or _shared.Modules.openclaw.default_sender_id
        if magic_command:
            if magic_command == "/stop":
                cancelled_task_ids = await _cancel_openclaw_tasks_for_stop(
                    sender_id=nk_sender_id,
                    lanlan_name=lanlan_name,
                    exclude_task_id=result.task_id,
                )
                if cancelled_task_ids:
                    task_params["cancelled_task_ids"] = cancelled_task_ids
            try:
                nk_result = await _shared.Modules.openclaw.run_magic_command(
                    magic_command,
                    sender_id=nk_sender_id,
                    role_name=lanlan_name,
                )
                success = bool(nk_result.get("success"))
                reply = str(nk_result.get("reply") or "")
                if success:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=True,
                        summary=reply[:EXCEPTION_TEXT_MAX_CHARS] if reply else _rp_phrase('openclaw_done', _rp_lang(None)),
                        detail=reply,
                        direct_reply=direct_reply,
                    )
                else:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=False,
                        summary=_rp_phrase('openclaw_failed', _rp_lang(None)),
                        error_message=str(nk_result.get("error") or "")[:ERROR_MESSAGE_MAX_CHARS],
                    )
            except Exception as e:
                logger.exception("[OpenClaw] magic command dispatch failed: %s", e)
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=False,
                        summary=_rp_phrase('openclaw_dispatch_failed', _rp_lang(None)),
                        error_message=str(e)[:ERROR_MESSAGE_MAX_CHARS],
                    )
                except Exception:
                    pass
            return
        nk_session_id = _shared.Modules.openclaw.get_or_create_persistent_session_id(
            role_name=lanlan_name,
            sender_id=nk_sender_id,
        )
        _shared.Modules.task_registry[result.task_id] = {
            "id": result.task_id,
            "type": "openclaw",
            "status": "running",
            "start_time": nk_start,
            "params": task_params,
            "lanlan_name": lanlan_name,
            "sender_id": nk_sender_id,
            "session_id": nk_session_id,
            "conversation_id": conversation_id,
            "result": None,
            "error": None,
            "_trigger_user_fingerprint": trigger_user_msg_sig,
        }
        _task_tracker.record_assigned(
            lanlan_name, task_id=result.task_id, method="openclaw",
            desc=result.task_description or instruction or "",
        )
        try:
            await _emit_main_event(
                "task_update",
                lanlan_name,
                task={
                    "id": result.task_id,
                    "status": "running",
                    "type": "openclaw",
                    "start_time": nk_start,
                    "params": task_params,
                },
            )
        except Exception as emit_err:
            logger.debug("[OpenClaw] emit task_update(running) failed: task_id=%s error=%s", result.task_id, emit_err)
        try:
            ack_text = _rp_phrase("openclaw_try", _rp_lang(None))
            await _emit_main_event(
                "proactive_message",
                lanlan_name,
                text=ack_text,
                detail=ack_text,
                direct_reply=True,
                timestamp=_now_iso(),
            )
        except Exception as emit_err:
            logger.debug("[OpenClaw] emit proactive_message(ack) failed: task_id=%s error=%s", result.task_id, emit_err)
        async def _run_openclaw_dispatch():
            try:
                from utils.instrument import counter as _ic
                _ic("agent_invoked", agent_type="openclaw")
            except Exception:
                pass  # 埋点 best-effort
            try:
                nk_result = await _shared.Modules.openclaw.run_instruction(
                    instruction,
                    attachments=attachments,
                    sender_id=nk_sender_id,
                    session_id=nk_session_id,
                    conversation_id=conversation_id,
                    role_name=lanlan_name,
                )
                success = bool(nk_result.get("success"))
                reply = str(nk_result.get("reply") or "")
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg and _reg.get("status") == "cancelled":
                    # cancel_task already marked cancelled; skip terminal writes
                    return
                if _reg:
                    _reg["status"] = "completed" if success else "failed"
                    _reg["end_time"] = _now_iso()
                    _reg["result"] = nk_result
                    _reg["session_id"] = str(nk_result.get("session_id") or _reg.get("session_id") or "")
                    if not success:
                        _reg["error"] = _tt(str(nk_result.get("error") or ""), TASK_ERROR_MAX_TOKENS)
                _task_tracker.record_completed(
                    lanlan_name, task_id=result.task_id, method="openclaw",
                    desc=result.task_description or instruction or "",
                    detail=reply[:TASK_TRACKER_DETAIL_MAX_CHARS] if reply else "", success=success,
                )
                if success:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=True,
                        summary=reply[:EXCEPTION_TEXT_MAX_CHARS] if reply else _rp_phrase('openclaw_done', _rp_lang(None)),
                        detail=reply,
                        direct_reply=direct_reply,
                    )
                else:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=False,
                        summary=_rp_phrase('openclaw_failed', _rp_lang(None)),
                        error_message=str(nk_result.get("error") or "")[:ERROR_MESSAGE_MAX_CHARS],
                    )
                await _emit_main_event(
                    "task_update",
                    lanlan_name,
                    task={
                        "id": result.task_id,
                        "status": "completed" if success else "failed",
                        "type": "openclaw",
                        "start_time": nk_start,
                        "end_time": _now_iso(),
                        "params": task_params,
                        "error": _tt(str(nk_result.get("error") or ""), TASK_ERROR_MAX_TOKENS) if not success else None,
                    },
                )
            except asyncio.CancelledError as e:
                cancel_msg = str(e)[:EXCEPTION_TEXT_MAX_CHARS] if str(e) else "cancelled"
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg:
                    _reg["status"] = "cancelled"
                    _reg["error"] = cancel_msg
                _task_tracker.record_completed(
                    lanlan_name, task_id=result.task_id, method="openclaw",
                    desc=result.task_description or instruction or "",
                    detail=cancel_msg[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False, cancelled=True,
                    trigger_user_fingerprint=(_reg or {}).get("_trigger_user_fingerprint"),
                )
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=False,
                        summary=_rp_phrase('openclaw_cancelled', _rp_lang(None)),
                        error_message=cancel_msg,
                    )
                except Exception:
                    pass
                try:
                    await _emit_main_event(
                        "task_update",
                        lanlan_name,
                        task={
                            "id": result.task_id,
                            "status": "cancelled",
                            "type": "openclaw",
                            "start_time": nk_start,
                            "end_time": _now_iso(),
                            "params": task_params,
                            "error": cancel_msg,
                        },
                    )
                except Exception:
                    pass
                raise
            except Exception as e:
                _reg = _shared.Modules.task_registry.get(result.task_id)
                if _reg and _reg.get("status") == "cancelled":
                    return
                logger.exception("[OpenClaw] dispatch failed: %s", e)
                if _reg:
                    _reg["status"] = "failed"
                    _reg["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
                _task_tracker.record_completed(
                    lanlan_name, task_id=result.task_id, method="openclaw",
                    desc=result.task_description or instruction or "",
                    detail=str(e)[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False,
                )
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="openclaw",
                        task_id=str(result.task_id or ""),
                        success=False,
                        summary=_rp_phrase('openclaw_dispatch_failed', _rp_lang(None)),
                        error_message=str(e)[:ERROR_MESSAGE_MAX_CHARS],
                    )
                except Exception:
                    pass
                try:
                    await _emit_main_event(
                        "task_update",
                        lanlan_name,
                        task={
                            "id": result.task_id,
                            "status": "failed",
                            "type": "openclaw",
                            "start_time": nk_start,
                            "end_time": _now_iso(),
                            "params": task_params,
                            "error": _tt(str(e), TASK_ERROR_MAX_TOKENS),
                        },
                    )
                except Exception:
                    pass

        nk_task = asyncio.create_task(_run_openclaw_dispatch())
        _shared.Modules.task_async_handles[result.task_id] = nk_task
        _shared.Modules._background_tasks.add(nk_task)

        def _cleanup_nk_task(_t, _tid=result.task_id):
            _shared.Modules._background_tasks.discard(_t)
            _shared.Modules.task_async_handles.pop(_tid, None)

        nk_task.add_done_callback(_cleanup_nk_task)
    else:
        logger.warning("[OpenClaw] ⚠️ Task requires OpenClaw but it's disabled")
