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
from datetime import datetime, timezone
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
    TASK_REGISTRY_CLEANUP_TTL,
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


# 能承载「上游回了一句需要许可」的状态——**只有 completed**。逐条理由见
# _has_recent_openclaw_task 里那张表；判据只有一句：**用户有没有可能看见那句审批提示**。
#
# ⚠️ partial 对 openclaw 不可达——本文件只写 running/completed/failed/cancelled，
# 列进来是死条目、还像是一次刻意的放行，所以不列（有测试从源码自动核对）。
#
# 真的从别处（比如 QwenPaw 自己的控制台）得知需要批准的用户，仍可直接敲字面
# `/openclaw approve`——显式命令一律豁免闸。
_APPROVAL_WINDOW_STATUSES = frozenset({"completed"})

# ⚠️ 兑现标记。一条 completed 记录**只能授权一次**推断批准：不消费的话，同一条记录会在
# 整个 TTL 内给每一句「同意」「沒問題」放行，而后面那些并没有对应的新审批提示——很可能
# 批到同一个上游会话里更晚出现的另一个挂起动作上（Codex P1）。
_APPROVAL_CONSUMED_KEY = "_approval_window_consumed"

# ⚠️ 窗口还要求那条回复**真的问过问题**。原来只判「5 分钟内有任务跑完过」，于是任何一次
# 成功的任务（哪怕回的是「整理完成，共移动 12 个文件」）都会给随后一句随口的「同意」开闸。
# 上游那句 reply 就存在 registry 条目的 result 里，拿来判一下几乎不要钱。
#
# ⚠️ 判据只认**明确的疑问标记**，不枚举「审批提示长什么样」——后者是开集。代价记在
# 这里：上游若用不带问号的陈述句征询（英文 "Proceed?" 有问号，但 "Let me know" 没有），
# 这条会误关窗口，用户得手敲字面命令。方向是 fail-closed。
#
# ⚠️ 曾经还收了 `确认` / `確認` / `是否`，已删——它们在**陈述句**里同样常见，而这里是
# 子串匹配：`已确认配置无误` / `確認完成` / `已检查是否有重复` 都不是在征询，却会把窗口
# 顶开，随后一句随口的「同意」就发出去了（Codex P2）。留下的这几个没有这个毛病：`？`
# `?` 是标点，`吗` `嗎` `要不要` 只出现在真的在问的句子里。
_APPROVAL_PROMPT_MARKERS = ("？", "?", "吗", "嗎", "要不要")


def _iter_approval_window_tasks(
    *,
    sender_id: Optional[str],
    lanlan_name: Optional[str],
    exclude_task_id: Optional[str] = None,
    age_bounded: bool = True,
    match_lanlan: bool = True,
    require_session: bool = True,
) -> list[str]:
    """Task ids whose recent completion may carry an unanswered approval prompt.

    "Recently completed" is the whole rule: only ``completed`` (see
    _APPROVAL_WINDOW_STATUSES) and only within the age check below. Note this is
    *disjoint* from _collect_active_openclaw_task_ids, which matches exactly the
    in-flight statuses this one rejects.

    Returns ids so callers can consume entries — one prompt authorizes one
    approval, and `/stop` retires every prompt still standing.

    ⚠️ ``age_bounded`` / ``match_lanlan`` 是给**作废**用的放宽开关，不是调参位。
    开闸和作废共用这个过滤器，但两者性质不同：开闸是**每次重新求值**的谓词，作废是
    **一次性的状态写**。所以作废的条件必须比开闸的**更宽**——凡是「现在不算窗口、
    以后可能又算」的条目，作废时漏掉一条，等它重新进窗口就是一个没人再作废得掉的洞。
    具体见两个开关各自的注释。
    """  # noqa: DOCSTRING_CJK
    # ⚠️ 这道闸和 _collect_active_openclaw_task_ids **状态集合互不相交**，不是笔误：
    # 那个是给 /stop 用的「谁还在跑」，这个是「谁刚把一次回复给到用户」。同一个判据
    # 贯穿始终——**用户有没有可能看见那句审批提示**：
    #   queued / running  reply 还没返回，_emit_task_result 还没发   → 不算
    #   failed            发的是固定失败文案，reply 一个字不出去      → 不算
    #   cancelled         用户刚亲手掐掉，正是他不想要的那个动作      → 不算
    #   completed         reply 经 _emit_task_result(detail=reply) 出去 → 算
    #
    # ⚠️ 但也**不能**靠「还在 registry 里」当窗口。_cleanup_task_registry 只在
    # capabilities.py 的状态发射路径上调用，正常的分析/派单路径根本不碰它；一个长连
    # 会话里终态条目可以无限期留着，闸就被一个几小时前的任务永久顶开（Codex P1）。
    # 所以这里显式按 end_time 判龄，不依赖清理被调用。
    now = datetime.now(timezone.utc)
    # ⚠️ 还要求条目属于**当前**会话。`/new` 会 reset_persistent_session_id 轮换
    # session，而旧任务那次审批提示是发在**旧**会话里的；只按 sender/角色过滤的话，
    # 用户 `/new` 之后随口一句「同意」会带着**新**会话 id 发出去，批到一个跟那句提示
    # 毫无关系的挂起动作上（Codex P1）。读当前 session 用只读的 peek_*，别用
    # get_or_create_*——问一句「有没有东西待批准」不该顺手建出一个会话。
    peek = getattr(_shared.Modules.openclaw, "peek_persistent_session_id", None)
    current_session = ""
    if callable(peek) and sender_id:
        try:
            current_session = str(peek(role_name=lanlan_name, sender_id=sender_id) or "")
        except Exception:
            logger.debug("[OpenClaw] peek_persistent_session_id failed", exc_info=True)
    # ⚠️ 问不出当前会话时，**开闸侧必须 fail-closed**。这里原来写的是
    # `if current_session and ...`——peek 抛异常（缓存文件读坏）或返回空，
    # `current_session` 就是 ""，于是整条 session 过滤被**跳过**，任意会话的条目都能开闸。
    # 这是把「开闸窄、作废宽」在 session 这一维上做反了：作废侧跳过过滤是更宽=安全，
    # 开闸侧跳过就是 fail-open。所以 require_session 的两边分开处理。
    if require_session and not current_session:
        logger.info(
            "[OpenClaw] approval window closed: current session unknown "
            "for sender=%s lanlan=%s",
            sender_id,
            lanlan_name,
        )
        return []
    matches: list[str] = []
    for task_id, info in _shared.Modules.task_registry.items():
        if task_id == exclude_task_id or not isinstance(info, dict):
            continue
        if info.get("type") != "openclaw":
            continue
        if sender_id and str(info.get("sender_id") or "").strip() != str(sender_id).strip():
            continue
        # ⚠️ 作废时不按角色收窄（match_lanlan=False）。上游的会话键是
        # `_build_session_key`，里面第一行就是 `del role_name`——**同一个 sender 的所有
        # 角色共用一个 QwenPaw 会话**。所以角色 B 说的「停下来」打掉的就是角色 A 那句
        # 审批提示所指的同一个挂起动作；按 lanlan_name 过滤会把 A 的窗口留下来，用户
        # 切回 A 随口一句「同意」就能把刚停掉的动作批回去。
        # 代价记账：A、B 并存且 A 正等审批时，B 的「停下来」会让 A 那次必须手敲字面
        # 命令。方向是 fail-closed，且 session 过滤仍在（跨会话不会被误伤）。
        if (
            match_lanlan
            and lanlan_name
            and str(info.get("lanlan_name") or "").strip() != str(lanlan_name).strip()
        ):
            continue
        # ⚠️ queued / running 也**不算**。同一个判据：在途请求的 reply 还没返回，
        # _run_openclaw_dispatch 要等 run_instruction 返回、把状态写成 completed
        # 之后才 _emit_task_result，所以一个还在跑的任务**不可能**已经把审批提示给到
        # 用户。放行它等于让「另一个无关任务正在跑」这件事本身去授权一个高风险动作，
        # 而那恰恰是这道闸最初要挡的场景（Codex P1）。
        if info.get("status") not in _APPROVAL_WINDOW_STATUSES:
            continue
        # ⚠️ 已经兑现过一次推断批准的条目不再算数：一次审批提示只授权一次。
        if info.get(_APPROVAL_CONSUMED_KEY):
            continue
        # ⚠️ 只有**问过问题**的那条回复才可能是审批提示。作废侧（require_session=False
        # 那一路）不判这个：宁可多作废，不可漏作废。
        if require_session:
            raw = info.get("result")
            reply = str((raw or {}).get("reply") or "") if isinstance(raw, dict) else ""
            if not any(marker in reply for marker in _APPROVAL_PROMPT_MARKERS):
                continue
        if current_session and str(info.get("session_id") or "").strip() != current_session:
            continue
        # ⚠️ 作废时不判龄（age_bounded=False），因为判龄的结果**会随时间翻转**：
        #   · 下界：时钟被回拨时 age 为负，条目此刻不算窗口，作废看不见它；等时钟追
        #     上来它又进窗口，而那次 /stop 已经过去了，再没有人来作废它。
        #   · 上界：同理，超龄条目今天不算窗口，回拨后又算。
        #   · 缺 end_time：开闸侧 fail-closed 跳过，但 end_time 是可以晚一步写上的，
        #     写上之后窗口就开了。
        # 三种都是「现在不算、以后可能算」。作废一条本来就不在窗口里的条目是无害的
        # （它本来也开不了闸），漏掉一条却是个洞——所以这里一律作废。
        if not age_bounded:
            matches.append(task_id)
            continue
        end_time = str(info.get("end_time") or "").strip()
        if not end_time:
            # 终态却没有 end_time：判不了龄，fail-closed 不放行。
            continue
        try:
            ended = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        # ⚠️ 上界之外还要下界。时钟被回拨（或条目带了未来 end_time）时
        # (now - ended) 为负，只判上界就恒成立，窗口会一直开到时钟追上来再过 5 分钟。
        age = (now - ended).total_seconds()
        if 0 <= age <= TASK_REGISTRY_CLEANUP_TTL:
            matches.append(task_id)
    return matches


def _find_approval_window_task(
    *,
    sender_id: Optional[str],
    lanlan_name: Optional[str],
    exclude_task_id: Optional[str] = None,
) -> Optional[str]:
    """The first task id that may carry an unanswered approval prompt, or None."""
    return next(
        iter(
            _iter_approval_window_tasks(
                sender_id=sender_id,
                lanlan_name=lanlan_name,
                exclude_task_id=exclude_task_id,
            )
        ),
        None,
    )


def _retire_approval_windows(
    *,
    sender_id: Optional[str],
    lanlan_name: Optional[str],
    exclude_task_id: Optional[str] = None,
) -> list[str]:
    """Consume every standing approval window. Returns the ids retired.

    Called when the user cancels: a prompt they just answered with "stop" must
    not keep authorizing anything.
    """
    retired: list[str] = []
    for task_id in _iter_approval_window_tasks(
        sender_id=sender_id,
        lanlan_name=lanlan_name,
        exclude_task_id=exclude_task_id,
        age_bounded=False,
        match_lanlan=False,
        require_session=False,
    ):
        info = _shared.Modules.task_registry.get(task_id)
        if isinstance(info, dict):
            info[_APPROVAL_CONSUMED_KEY] = True
            retired.append(task_id)
    return retired


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
    proactive: bool = False,
) -> None:
    """Handle an analyzer decision routed to the OpenClaw channel.

    ``proactive`` marks a self-initiated turn (no triggering user). The sender
    is forced to the default rather than resolved from the messages window,
    since the "latest user" there is a stale prior turn — attributing the action
    (or a proactive ``/stop``) to that user's persistent OpenClaw session would
    be wrong in multi-user setups.
    """
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
        # Proactive tasks have no triggering user → force the default sender so a
        # self-initiated action (or proactive /stop) never runs under the stale
        # prior user's persistent OpenClaw session.
        if proactive:
            nk_sender_id = _shared.Modules.openclaw.default_sender_id
        else:
            nk_sender_id = _resolve_openclaw_sender_id(messages) or _shared.Modules.openclaw.default_sender_id
        if magic_command:
            # ⚠️ `/daemon approve` 让上游 daemon 真的批准一个挂起的高风险动作，而它
            # 一路上从来没有校验过「这条回复是不是针对某个待审批动作」——全仓库没有
            # 任何待审批状态（grep pending_approval / awaiting_approval / approval_state
            # 零命中），所以「同意」「没问题」这种日常应答会无条件批准。
            #
            # 完整的修法是 gate 在「确实存在一个待审批动作」上，但那个状态**只活在
            # 上游 QwenPaw daemon 里**：run_instruction 是一次性 POST，请求-响应，没有
            # side channel，N.E.K.O. 侧拿不到。真要拿到得让 QwenPaw 在响应里加字段或
            # 开状态端点——跨仓库契约变更。
            #
            # 这里做的是它在本地可得的近似：没有任何活着的 openclaw 任务时，一条批准
            # 在定义上就是没有意义的，直接丢弃。日常闲聊场景（占误批准的绝大多数）
            # 由此彻底关掉；任务真在跑时行为一点不变，那个窗口靠分类器侧的整子句
            # 白名单收窄。
            #
            # ⚠️ 闸只管**从自由文本推断出来的**批准。用户直接打字面 magic word
            # （`/openclaw approve` 走 core/turn.py 那条显式分支，或聊天框里直接敲
            # `/daemon approve`）意图毫无歧义，必须原样放行——那条路径上
            # _emit_task_result 是**唯一**的用户可见回复，被闸掉的话用户敲了命令、图
            # 被丢了、turn 被消耗了，屏幕上一个字都不回，只会反复重敲。
            #
            # 静默丢弃：不 _emit_task_result，所以不会念出「收到许可！」那句固定台词。
            # magic command 自己不进 task_registry（注册在下面的非 magic 分支里），
            # exclude_task_id 是防御性的。
            explicitly_typed = False
            if isinstance(result.tool_args, dict):
                # ⚠️ 「用户是不是亲手打的」要用**严格**解析：必须 `/` 开头且整条输入
                # 就是那个命令。宽松那个会把普通英文词 `stop` / `approve` 当成显式命令，
                # 于是一句英文闲聊就能拿到「显式豁免」，绕过下面整道审批闸。
                parse_typed = getattr(
                    _shared.Modules.openclaw, "parse_typed_magic_command", None
                )
                explicitly_typed = callable(parse_typed) and (
                    parse_typed(result.tool_args.get("original_user_text")) == magic_command
                )
            # ⚠️ 主动搭话轮**没有用户**。task_executor 在 proactive 轮把意图换成猫娘
            # 自己那句最新台词再喂进分类器，所以她随口一句「没问题」就会被判成批准，
            # 而这一轮里用户一个字都没说过（Codex P1）。批准是唯一一条会让上游真的
            # 执行高风险动作的命令，proactive 轮一律不放行，跟 registry 里有什么无关。
            if magic_command == "/daemon approve" and proactive:
                logger.info(
                    "[OpenClaw] /daemon approve dropped: proactive turn has no user "
                    "authorization (lanlan=%s)",
                    lanlan_name,
                )
                return
            if magic_command == "/daemon approve":
                # ⚠️ 显式命令**豁免的是准入判定，不是兑现**。这两件事之前被同一个
                # `not explicitly_typed` 一起跳过了：用户亲手敲 `/openclaw approve` 回答了
                # 那句提示，窗口却原样留着，TTL 内一句随口的「同意」还能再批一次——而那次
                # 已经没有对应的提示了（Codex P1）。豁免的理由是「显式命令意图毫无歧义、
                # 不该被闸掉」，跟「这句提示已经被回答过了」没有关系。
                if not explicitly_typed and _find_approval_window_task(
                    sender_id=nk_sender_id,
                    lanlan_name=lanlan_name,
                    exclude_task_id=result.task_id,
                ) is None:
                    logger.info(
                        "[OpenClaw] /daemon approve dropped: no openclaw task on record "
                        "for sender=%s lanlan=%s",
                        nk_sender_id,
                        lanlan_name,
                    )
                    return
                # ⚠️ 一次推断批准兑现掉**全部**窗口，而且**不看这一趟的成败**。
                #
                # 兑现全部：`/daemon approve` 不带 task id，我们**无从知道**是哪一条
                # completed 带出了那句提示。只兑现一条的话，另一条仍然站着，下一句随口的
                # 「同意」就能在没有新提示的情况下批到别的挂起动作上（Codex P1）。
                # 代价：两个任务先后各问一次时，第二句「好」会被丢，得手敲字面命令。
                #
                # 不看成败：`run_instruction` 在 POST **成功返回之后**取不到 reply 也返回
                # success=False（openclaw_adapter「did not return a final reply」那支），
                # 所以这个 flag 把「压根没发出去」和「发了、可能已经执行了、只是读不到
                # 回复」混在一起。前一种保留窗口是对的，后一种保留就是给同一句「同意」
                # 第二次机会去批另一个动作。两者分不开，取 fail-closed 的那边。
                #
                # 放在 await **之前**，理由和 /stop 那边一样：异常穿出时不能把它跳过。
                #
                # ⚠️ 这道闸**不是多用户隔离边界**，别当它是。`_resolve_openclaw_sender_id`
                # 读的是 analyze messages 上的 sender_id，而 main_logic 从不往上面挂身份
                # （cross_server.py 里 sender_id / user_id 零命中），所以它恒返回空、一路
                # 回落到 `default_sender_id`——**所有用户共用一个桶**。这个回落在
                # origin/main 上就有（本 PR 之前 sender 过滤本来也是这么算的），而且更根本
                # 的是上游会话键 `_build_session_key` 就是 `user::<sender>`，所以多用户部署
                # 里大家本来就共用同一个 QwenPaw 会话，不只是共用这道闸。
                # 而且**就算把身份传下来也堵不上**：显式敲 `/daemon approve` 按设计豁免准入
                # 判定（否则就是静默吞掉用户的命令），B 照样能批 A 的挂起动作。多用户隔离
                # 得靠上游给出「这条提示属于谁」，是跨仓库契约，见 PR 描述里的待办。
                # 这道闸管的是**别把自由文本误读成授权**，那个属性跟 sender 桶宽窄无关。
                consumed = _retire_approval_windows(
                    sender_id=nk_sender_id,
                    lanlan_name=lanlan_name,
                    exclude_task_id=result.task_id,
                )
                logger.info(
                    "[OpenClaw] inferred /daemon approve consumed %d window(s): %s",
                    len(consumed),
                    ", ".join(consumed),
                )
            if magic_command == "/stop" and not explicitly_typed:
                # ⚠️ 「停下来」「别找了」这类祈使句在日常对话里字面完全相同——用户可能是
                # 对**猫娘本人**说的（角色扮演里尤其常见），不是要掐后台任务。整子句判据
                # 挡得掉叙述（`雨停下来了` 不命中），挡不掉这一类。
                # 所以这一档额外要求**确实有在跑的 openclaw 任务**佐证。
                #
                # ⚠️ 明确指向 agent 的说法（取消这个任务 / 停止搜索 …）**不受此限**，
                # 字面命令也不受限。这是有意的：registry 恰恰在最需要 /stop 的时刻说谎
                # ——请求超时后状态被写成 failed、进程重启后 registry 全空、条目过 TTL
                # 被删，而这些时刻上游那个活儿可能还在跑。唯一能让上游停手的通道就是
                # 这次 POST，把它整个门在 registry 上等于把逃生阀焊死。
                tier = None
                tier_fn = getattr(_shared.Modules.openclaw, "stop_trigger_tier", None)
                if callable(tier_fn) and isinstance(result.tool_args, dict):
                    try:
                        tier = tier_fn(result.tool_args.get("original_user_text"))
                    except Exception:
                        logger.debug("[OpenClaw] stop_trigger_tier failed", exc_info=True)
                # ⚠️ 佐证不只是「有任务在跑」，**还站着的审批提示也算**。审批提示出口时
                # 恰恰没有 queued/running 任务——窗口是 completed 开的，两个状态集合互不
                # 相交。只看在跑的任务就会在这里提前 return，于是底下那段作废窗口的代码
                # 根本不执行：用户那句「停下来」是在**拒绝**刚问出口的提示，结果不但被
                # 静默丢弃，窗口还留着，随后一句随口的「同意」就能把他刚拒绝的动作批了。
                #
                # ⚠️ 但窗口这一侧要用**窄**判据（`_find_approval_window_task`），不是作废
                # 用的那套放宽过滤。佐证是**开闸**决策，「开闸窄、作废宽」在这里同样成立：
                # 终态条目在这条路径上可以无限期留着（清理只在 capabilities 那条路上调），
                # 用宽过滤的话**一条几小时前的、根本没问过问题的完成记录**就能让之后每一句
                # 「停下来」都放行——分档守卫等于白加（Codex P2）。窄判据不影响上面那个
                # 拒绝场景：拒绝提示的当下，窗口本来就是新鲜、同角色、同会话、带问号的。
                #
                # ⚠️ 在跑的任务这一侧则**不按角色收窄**：上游会话键只认 sender
                # （`_build_session_key` 第一行 `del role_name`），所以同一个 sender 在另一个
                # 角色下跑着的活儿，同样是「停下来」的正当指代对象，而那次 /stop POST 打的
                # 也正是同一个上游会话。
                corroborated = _collect_active_openclaw_task_ids(
                    sender_id=nk_sender_id,
                    lanlan_name=None,
                    exclude_task_id=result.task_id,
                ) or _find_approval_window_task(
                    # ⚠️ 窗口这侧同样**不按角色收窄**——理由和上面那行一样，而且不放宽就
                    # 内部不一致：作废本来就是角色无关的，在跑任务的佐证上一轮也放宽了，
                    # 唯独这里还按角色匹配的话，「角色 A 下收到提示 → 切到角色 B 说停下来」
                    # 会在这里提前 return，作废那段执行不到，A 的窗口留着给下一句「同意」。
                    # 收窄的那几维（新鲜 / 同会话 / 带疑问标记 / 未兑现）一条没动。
                    sender_id=nk_sender_id,
                    lanlan_name=None,
                    exclude_task_id=result.task_id,
                )
                if tier == "ambiguous" and not corroborated:
                    logger.info(
                        "[OpenClaw] /stop dropped: ambiguous phrasing with no running "
                        "task for sender=%s lanlan=%s",
                        nk_sender_id,
                        lanlan_name,
                    )
                    return
            if magic_command == "/stop":
                # ⚠️ 作废排在取消 helper **之前**。那个 helper 里有 `await` 和一处没包
                # try 的 `_task_tracker.record_completed`，一旦抛出就把后面的作废整个
                # 跳过，被取消任务的窗口反而留了下来。作废不依赖取消的任何结果，所以
                # 排在前面纯赚——和它排在 `run_magic_command` 之前是同一个理由。
                # ⚠️ 掐任务掐不掉**已经问出口的那句审批提示**：_cancel_… 只挑
                # queued/running，而窗口是 completed 开的，两个状态集合互不相交。不
                # 兑现的话，用户「停下来」之后随口一句「同意」还能在整个 TTL 里把他刚
                # 撤销的动作放回去（Codex P1）。同一个判据的延伸——窗口问的是「用户有
                # 没有可能看见那句提示」，而 `/stop` 正是他对着那句提示给出的回答。
                #
                # ⚠️ 必须在 if cancelled_task_ids 之外：Codex 描述的场景恰恰是**没有**
                # 在跑的任务可掐（任务刚 completed 在等审批），那时上面那个列表是空的。
                #
                # 也不等 run_magic_command 的成败：本地取消（上面那个 helper 写
                # status=cancelled）本来就不回滚，而「用户说了停」这件事跟上游那一趟调
                # 用成没成无关。真想在停之后批准，直接敲 /daemon approve——显式命令豁免。
                #
                # ⚠️ 但主动搭话轮不算。上面阻断 approve 的理由是「proactive 轮**没有
                # 用户**」，那条理由在这里同样成立：作废的依据是「/stop 是用户对着那句
                # 提示给出的回答」，而 proactive 轮里用户一个字都没说——那是猫娘自己的
                # 台词被喂进了分类器。不能替用户批准，却可以替用户撤销授权，是自相矛盾
                # 的。而且这一侧的后果不是「多批一次」而是**静默**：窗口被她说没就没，
                # 用户随后那句「同意」走 approve 闸直接 return，不 _emit_task_result，
                # 屏幕上一个字都不回，他只会反复重说。
                retired = []
                if not proactive:
                    retired = _retire_approval_windows(
                        sender_id=nk_sender_id,
                        lanlan_name=lanlan_name,
                        exclude_task_id=result.task_id,
                    )
                if retired:
                    logger.info(
                        "[OpenClaw] /stop retired %d approval window(s): %s",
                        len(retired),
                        ", ".join(retired),
                    )
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
                # 兑现已经在派单**之前**做掉了（见上面那段注释）。这里曾经按 success
                # 兑现单条，两个前提都不成立：success=False 不等于「没送出去」，
                # 单条也不等于「那条带提示的」。
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
