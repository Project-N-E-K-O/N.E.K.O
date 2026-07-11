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

"""OpenFang channel: dispatch onto the VM agent daemon plus the OpenAI
compatibility patch family used by the LLM proxy route."""

import uuid
import asyncio

from config import (
    TASK_DETAIL_MAX_TOKENS,
    TASK_ERROR_MAX_TOKENS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    EXCEPTION_TEXT_MAX_CHARS,
)
from utils.tokenize import truncate_to_tokens as _tt
from utils.result_parser import _phrase as _rp_phrase, _get_lang as _rp_lang
from brain.agent_session import get_session_manager

from .. import _shared
from .._shared import logger
from ..tracker import _task_tracker
from ..registry import _now_iso, _is_duplicate_task
from ..results import _emit_main_event, _emit_task_result


async def dispatch(
    result,
    *,
    messages,
    lanlan_name,
    conversation_id,
    trigger_user_msg_sig,
) -> None:
    """Handle an analyzer decision routed to the OpenFang channel."""
    if _shared.Modules.agent_flags.get("openfang_enabled", False) and _shared.Modules.openfang:
        dup, matched = await _is_duplicate_task(result.task_description, lanlan_name)
        if not dup:
            sm = get_session_manager()
            of_session = sm.get_or_create(None, "openfang")
            of_session.add_task(result.task_description)

            of_task_id = str(uuid.uuid4())
            of_start = _now_iso()
            of_info = {
                "id": of_task_id,
                "type": "openfang",
                "status": "running",
                "start_time": of_start,
                "params": {"instruction": result.task_description},
                "lanlan_name": lanlan_name,
                "session_id": of_session.session_id,
                "result": None,
                "error": None,
                "_trigger_user_fingerprint": trigger_user_msg_sig,
            }
            _shared.Modules.task_registry[of_task_id] = of_info
            _task_tracker.record_assigned(
                lanlan_name, task_id=of_task_id, method="openfang",
                desc=result.task_description or "",
            )

            try:
                await _emit_main_event(
                    "task_update", lanlan_name,
                    task={"id": of_task_id, "status": "running", "type": "openfang",
                          "start_time": of_start,
                          "params": {"instruction": result.task_description},
                          "session_id": of_session.session_id},
                )
            except Exception as e:
                logger.debug("[OpenFang] emit task_update(running) failed: task_id=%s error=%s", of_task_id, e)

            async def _run_openfang_dispatch():
                try:
                    from utils.instrument import counter as _ic
                    _ic("agent_invoked", agent_type="openfang")
                except Exception:
                    pass  # 埋点 best-effort
                try:
                    of_res = await _shared.Modules.openfang.run_instruction(
                        result.task_description,
                        session_id=of_session.session_id,
                        local_task_id=of_task_id,
                    )
                    # steps 列表可能含 daemon 返回的 user/AI/tool 原文，
                    # logger 只记数量，预览走 print 兜底。
                    _of_steps = of_res.get("steps")
                    _of_steps_count = len(_of_steps) if isinstance(_of_steps, list) else int(bool(_of_steps))
                    logger.info(
                        "[OpenFang] Task completed: success=%s, agent=%s, result_len=%d, steps_count=%d, artifacts_count=%d",
                        of_res.get("success"), of_res.get("agent_name"),
                        len(str(of_res.get("result", ""))),
                        _of_steps_count,
                        len(of_res.get("artifacts") or []),
                    )
                    if _of_steps is not None:
                        # debug-only：单独 try 兜底，避免不可 JSON 序列化的
                        # step 对象把整个 OpenFang 任务拖进异常分支误标失败
                        try:
                            import json as _json_for_steps
                            from utils.tokenize import truncate_to_tokens as _tt_steps
                            _steps_repr = _json_for_steps.dumps(_of_steps, ensure_ascii=False, default=str)
                            print(f"[OpenFang] steps preview: {_tt_steps(_steps_repr, 120)}")
                        except Exception as _steps_err:
                            print(f"[OpenFang] steps preview unavailable (exc_type={type(_steps_err).__name__})")
                    logger.debug("[OpenFang] ====== RAW RESULT (debug) ======")
                    logger.debug("[OpenFang] keys=%s", list(of_res.keys()))
                    # result / error / artifacts 都可能含 LLM/用户原文，
                    # 全部走 print 不进 logger
                    logger.debug(
                        "[OpenFang] result_len=%d, error_len=%d, artifacts_count=%d",
                        len(str(of_res.get("result", ""))),
                        len(str(of_res.get("error") or "")),
                        len(of_res.get("artifacts") or []),
                    )
                    print(f"[OpenFang] result (first 500): {str(of_res.get('result', ''))[:500]}")
                    # error 可能是几 KB 的堆栈/解释文本；artifacts 可能是大
                    # JSON / base64 列表，无界 print 既泄漏面大又会卡 stdout。
                    _of_err = str(of_res.get("error") or "")
                    print(f"[OpenFang] error (first 500, len={len(_of_err)}): {_of_err[:500]}")
                    _of_arts = of_res.get("artifacts")
                    if isinstance(_of_arts, list):
                        _of_art_types = [type(a).__name__ for a in _of_arts[:3]]
                        print(f"[OpenFang] artifacts: count={len(_of_arts)}, types(first3)={_of_art_types}")
                    else:
                        print(f"[OpenFang] artifacts_present={_of_arts is not None}")
                    logger.debug("[OpenFang] ==============================")
                    if of_info.get("status") == "cancelled":
                        return
                    success = of_res.get("success", False)
                    of_result_text = of_res.get("result", "") or ""
                    of_error_text = of_res.get("error", "") or ""
                    _lang = _rp_lang(None)
                    _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
                    # 两处 detail 都回流到 LLM context — 同语义统一到 200 tokens
                    # （和 result_parser._truncate / fallback Context 同一档）。
                    summary = _rp_phrase('cu_task_done', _lang, desc=result.task_description, status=_done, detail=_tt(of_result_text, TASK_DETAIL_MAX_TOKENS)) if of_result_text else \
                              _rp_phrase('cu_task_desc_only', _lang, desc=result.task_description, status=_done)
                    of_session.complete_task(of_result_text or summary, success)
                    # _of_error_src 和 task_tracker.detail 都用 fallback chain：
                    # daemon 按惯例把失败说明塞 error 而不是 result，下游 detail
                    # 也得能从 error 兜回，否则 analyzer 看到 failed 但 detail="
                    # 拿不到任何线索（前面 of_info["error"] 修过但 task_tracker
                    # 这条出口没同步）。
                    _of_error_src = of_error_text or of_result_text or "(OpenFang task failed with no error text)"
                    _track_detail = of_result_text if success else _of_error_src
                    _task_tracker.record_completed(
                        lanlan_name, task_id=of_task_id, method="openfang",
                        desc=result.task_description or "",
                        detail=_tt(_track_detail, TASK_DETAIL_MAX_TOKENS) if _track_detail else "", success=success,
                    )
                    of_info["status"] = "completed" if success else "failed"
                    of_info["end_time"] = _now_iso()
                    of_info["result"] = of_res
                    if not success:
                        of_info["error"] = _tt(_of_error_src, TASK_ERROR_MAX_TOKENS)
                    await _emit_task_result(
                        lanlan_name,
                        channel="openfang",
                        task_id=of_task_id,
                        success=success,
                        summary=summary,
                        detail=of_result_text if success else "",
                        error_message=_of_error_src if not success else "",
                    )
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": of_task_id, "status": of_info["status"],
                                  "type": "openfang", "start_time": of_start, "end_time": _now_iso(),
                                  "error": of_info.get("error"),
                                  "session_id": of_session.session_id},
                        )
                    except Exception as emit_err:
                        logger.debug("[OpenFang] emit task_update(terminal) failed: task_id=%s error=%s", of_task_id, emit_err)
                except asyncio.CancelledError as e:
                    cancel_msg = str(e)[:EXCEPTION_TEXT_MAX_CHARS] if str(e) else "cancelled"
                    # Best-effort remote cancel
                    try:
                        if _shared.Modules.openfang:
                            await _shared.Modules.openfang.cancel_running(of_task_id)
                            _shared.Modules.openfang.unregister_local_task(of_task_id)
                    except Exception as cancel_err:
                        logger.debug("[OpenFang] remote cancel failed for %s: %s", of_task_id, cancel_err)
                    of_info["status"] = "cancelled"
                    of_info["error"] = cancel_msg
                    of_session.complete_task(cancel_msg, success=False)
                    _task_tracker.record_completed(
                        lanlan_name, task_id=of_task_id, method="openfang",
                        desc=result.task_description or "", detail=cancel_msg[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False, cancelled=True,
                    )
                    try:
                        await _emit_task_result(
                            lanlan_name, channel="openfang", task_id=of_task_id,
                            success=False,
                            summary=_rp_phrase('of_cancelled', _rp_lang(None), desc=result.task_description or ''),
                            error_message=cancel_msg,
                        )
                    except Exception:
                        logger.debug("[OpenFang] emit_task_result(cancelled) failed: task_id=%s", of_task_id, exc_info=True)
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": of_task_id, "status": "cancelled", "type": "openfang",
                                  "start_time": of_start, "end_time": _now_iso(),
                                  "error": cancel_msg, "session_id": of_session.session_id},
                        )
                    except Exception:
                        logger.debug("[OpenFang] emit task_update(cancelled) failed: task_id=%s", of_task_id, exc_info=True)
                    raise
                except Exception as e:
                    if of_info.get("status") == "cancelled":
                        return
                    # exception 字符串可能含用户/LLM 原文，logger 只记元数据
                    logger.warning(f"[OpenFang] Task failed (exc_type={type(e).__name__})")
                    print(f"[OpenFang] Task raw error: {e}")
                    of_info["status"] = "failed"
                    of_info["end_time"] = _now_iso()
                    of_info["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
                    of_session.complete_task(str(e), success=False)
                    _task_tracker.record_completed(
                        lanlan_name, task_id=of_task_id, method="openfang",
                        desc=result.task_description or "", detail=str(e)[:TASK_TRACKER_DETAIL_MAX_CHARS], success=False,
                    )
                    try:
                        await _emit_task_result(
                            lanlan_name, channel="openfang", task_id=of_task_id,
                            success=False,
                            summary=f'虚拟机任务 "{result.task_description}" 执行异常',
                            error_message=str(e),
                        )
                    except Exception:
                        logger.debug("[OpenFang] emit_task_result(failed) failed: task_id=%s", of_task_id, exc_info=True)
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": of_task_id, "status": "failed", "type": "openfang",
                                  "start_time": of_start, "end_time": _now_iso(),
                                  "error": _tt(str(e), TASK_ERROR_MAX_TOKENS),
                                  "session_id": of_session.session_id},
                        )
                    except Exception:
                        logger.debug("[OpenFang] emit task_update(failed) failed: task_id=%s", of_task_id, exc_info=True)

            of_task = asyncio.create_task(_run_openfang_dispatch())
            _shared.Modules.task_async_handles[of_task_id] = of_task
            _shared.Modules._background_tasks.add(of_task)
            def _cleanup_of_task(_t, _tid=of_task_id):
                _shared.Modules._background_tasks.discard(_t)
                _shared.Modules.task_async_handles.pop(_tid, None)
            of_task.add_done_callback(_cleanup_of_task)
        else:
            logger.info(f"[OpenFang] Duplicate task detected, matched with {matched}")
    else:
        logger.warning("[OpenFang] ⚠️ Task requires OpenFang but it is disabled or unavailable")


def _patch_openai_response(data: dict) -> None:
    """
    Comprehensively patch the OpenAI-compatible response to fix OpenFang's strict-parsing compatibility issues:
    1. Fill in usage fields (completion_tokens, etc.)
    2. Fix malformed_function_call → standard tool_calls format
    3. Ensure message.content is not None
    """
    if not isinstance(data, dict):
        return

    _patch_usage(data)
    _patch_malformed_tool_calls(data)


def _patch_usage(data: dict) -> None:
    """Fill in missing usage fields."""
    if not isinstance(data, dict):
        return

    usage = data.get("usage")
    if usage is None:
        data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return

    if not isinstance(usage, dict):
        return

    # ``.get(k) is None`` covers both a missing key and an explicit JSON null.
    # Zero prompt/completion BEFORE computing total — with the old order,
    # ``{"prompt_tokens": null}`` reached the addition below and raised
    # TypeError, silently skipping the patch for that chunk.
    if usage.get("prompt_tokens") is None:
        usage["prompt_tokens"] = 0
    if usage.get("completion_tokens") is None:
        usage["completion_tokens"] = 0
    if usage.get("total_tokens") is None:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]


def _patch_malformed_tool_calls(data: dict) -> None:
    """
    Fix malformed_function_call responses returned by Gemini/OpenRouter.

    Problem: some models routed through OpenRouter don't support standard OpenAI
    function calling and emit a `call:tool_name{json_args}` format inside the
    refusal field. OpenFang expects the standard tool_calls format.

    Fix: parse the tool calls out of refusal and convert them into a standard
    tool_calls array.
    """
    choices = data.get("choices")
    if not isinstance(choices, list):
        return

    for choice in choices:
        if not isinstance(choice, dict):
            continue

        finish_reason = choice.get("finish_reason", "")
        msg = choice.get("message", {})
        if not isinstance(msg, dict):
            continue

        refusal = msg.get("refusal", "")

        # 检测 malformed function call
        # 某些模型 (Gemini via OpenRouter) 不支持 OpenAI-style function calling round-trip:
        # 即使我们把 malformed call 转成标准 tool_calls，下一轮提交 tool result 时
        # 模型会报 thought_signature 错误。
        # 正确做法：不转 tool_calls，而是提取工具调用意图转为文本内容，
        # 让 OpenFang 用文本模式回复（不走 tool use 循环）。
        if finish_reason == "malformed_function_call" and refusal:
            # 解析 call:tool_name{args} 提取意图，作为文本指令
            intent_text = _extract_tool_intent_as_text(refusal)
            msg["content"] = intent_text
            msg.pop("refusal", None)
            msg.pop("tool_calls", None)  # 确保没有 tool_calls
            choice["finish_reason"] = "stop"
            print("[LLM Proxy] Converted malformed_function_call to text intent")

        # 确保 message.content 为非 null 字符串（有些 API 返回 null 或缺失该字段）
        if "content" not in msg or msg["content"] is None:
            msg["content"] = ""


def _extract_tool_intent_as_text(refusal_text: str) -> str:
    """
    Extract the tool-call intent from a malformed function call and convert it to natural-language text.

    Example:
    input: "Malformed function call: call:web_search{queries:["中国到日本 机票价格"]}"
    output: "I'll search for: 中国到日本 机票价格, China to Japan flight prices..."

    This lets OpenFang use the text as the agent's reply instead of trying to execute an incompatible tool call.
    """  # noqa: DOCSTRING_CJK
    import re as _re

    cleaned = refusal_text.replace("Malformed function call: ", "").strip()

    # 提取 call:name{args} 中的 args 部分
    pattern = r'call:(\w+)\s*(\{.*\})'
    match = _re.search(pattern, cleaned, _re.DOTALL)

    if not match:
        # Context 会回到 LLM 的下一轮上下文 — token 而非字符。
        # 给固定前缀预留 budget，保证整条 fallback ≤ 200 token。
        from utils.tokenize import count_tokens
        prefix = "I attempted to perform an action but encountered a compatibility issue. Let me provide what I know instead.\n\nContext: "
        prefix_tokens = count_tokens(prefix)
        if prefix_tokens >= 200:
            # 极端 / 文案被改长 / 本地化场景的兜底：把整条前缀也截到 200，
            # 保证返回串永远不超预算。
            return _tt(prefix, TASK_DETAIL_MAX_TOKENS)
        return prefix + _tt(cleaned, 200 - prefix_tokens)

    tool_name = match.group(1)
    args_raw = match.group(2)

    # 尝试提取可读的参数内容
    # 常见格式: {queries:["q1","q2",...]} 或 {query:"..."}
    readable_args = []
    # 提取引号中的字符串
    strings = _re.findall(r'"([^"]*)"', args_raw)
    if strings:
        readable_args = strings[:5]  # 最多取5个

    tool_descriptions = {
        "web_search": "search the web for",
        "web_fetch": "fetch the web page",
        "file_read": "read the file",
        "file_write": "write to a file",
        "shell_exec": "run a command",
        "browser_navigate": "navigate to",
    }
    action = tool_descriptions.get(tool_name, f"use {tool_name} for")

    if readable_args:
        args_text = ", ".join(readable_args)
        result = (
            f"I wanted to {action}: {args_text}\n\n"
            f"However, due to a model compatibility issue with tool calling, "
            f"I cannot execute this tool directly. "
            f"Based on my knowledge, let me provide what information I can about this topic."
        )
    else:
        result = (
            f"I attempted to {action}, but encountered a compatibility issue.\n\n"
            f"Let me provide what information I can based on my existing knowledge."
        )
    # 统一兜底：args_text 可能含长 query 串（multi-string args 或 base64
    # 之类），就算上面的 readable_args[:5] 取过 5 个，每个都长的话整段
    # 仍可能超 200 token。这里再过一次 _tt 保证最终交回 LLM 的 message
    # 严格 ≤ 200 token，与 not match 分支语义对齐。
    return _tt(result, TASK_DETAIL_MAX_TOKENS)
