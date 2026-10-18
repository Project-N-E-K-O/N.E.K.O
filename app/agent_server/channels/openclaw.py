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


# Only completed can represent an upstream reply requesting permission. See the per-state rationale
# in _has_recent_openclaw_task; the criterion is whether the user could have seen that approval prompt.
#
# partial is unreachable for openclaw: this file writes only running/completed/failed/cancelled;
# listing it would be dead code resembling deliberate permission, so omit it (tests check source automatically).
#
# Users who learn approval is needed elsewhere (such as QwenPaw's own console) may still type the literal
# `/openclaw approve`; explicit commands always bypass the gate.
_APPROVAL_WINDOW_STATUSES = frozenset({"completed"})

# Redemption marker: a completed record authorizes inferred approval **once only**. Without consumption, it would
# allow every 同意 / 沒問題 throughout the TTL without a corresponding new approval prompt, potentially
# approving a later, different pending action in the same upstream session (Codex P1).
_APPROVAL_CONSUMED_KEY = "_approval_window_consumed"

# The window also requires the reply to have **actually asked a question**. Checking only completion within 5 minutes made any
# successful task (even a reply saying 12 files were organized) open the gate for a subsequent casual agreement.
# The upstream reply is already in the registry entry's result, making this check nearly free.
#
# Recognize only **explicit question markers**, not an enumeration of approval-prompt forms, which is open-ended. The cost:
# upstream requests phrased without a question mark ("Proceed?" has one, "Let me know" does not)
# falsely close the window and require the literal command. This fails closed.
#
# Removed `确认` / `確認` / `是否`: they also occur in **statements**, and this uses
# substring matching. `已确认配置无误` / `確認完成` / `已检查是否有重复` ask nothing but would open
# the window for a casual agreement to be sent (Codex P2). Remaining markers avoid that issue: `？`
# and `?` are punctuation; `吗` `嗎` `要不要` occur only in actual questions.
_APPROVAL_PROMPT_MARKERS = ("？", "?", "吗", "嗎")


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

    \"Recently completed\" is the whole rule: only ``completed`` (see
    _APPROVAL_WINDOW_STATUSES) and only within the age check below. Note this is
    *disjoint* from _collect_active_openclaw_task_ids, which matches exactly the
    in-flight statuses this one rejects.

    Returns ids so callers can consume entries — one prompt authorizes one
    approval, and `/stop` retires every prompt still standing.

    ``age_bounded`` / ``match_lanlan`` relax filtering for **invalidation**, not tuning.
    Opening and invalidation share this filter but differ: opening is a predicate **reevaluated each time**, while invalidation
    is a **one-time state write**. Its criteria must therefore be **broader**: any entry outside the window now
    but potentially inside later becomes an unfixable hole if invalidation misses it.
    See the individual switches' comments for details.
    """  # noqa: DOCSTRING_CJK
    # This gate's state set is **disjoint** from _collect_active_openclaw_task_ids, deliberately:
    # that identifies running tasks for /stop; this identifies tasks whose reply just reached the user. One criterion
    # applies throughout: **could the user have seen the approval prompt**?
    #   queued / running  reply not returned or sent by _emit_task_result -> excluded
    #   failed            fixed failure message sent; no reply content leaves -> excluded
    #   cancelled         user just stopped the unwanted action -> excluded
    #   completed         reply sent via _emit_task_result(detail=reply) -> included
    #
    # Registry presence alone **cannot** define the window. _cleanup_task_registry runs only in
    # capabilities.py's status-emission path, never ordinary analysis/dispatch; terminal entries can linger indefinitely
    # in long-lived sessions, keeping the gate open for hours-old tasks (Codex P1).
    # Explicitly bound age by end_time rather than rely on cleanup being called.
    now = datetime.now(timezone.utc)
    # Entries must also belong to the **current** session. `/new` rotates the session via reset_persistent_session_id,
    # while the old task's approval prompt was sent in the **old** session. Filtering only sender/role would let
    # a casual agreement after `/new` go out with the **new** session ID and approve a pending action unrelated
    # to that prompt (Codex P1). Read the current session using read-only peek_*, never
    # get_or_create_*: asking whether approval is pending must not create a session.
    peek = getattr(_shared.Modules.openclaw, "peek_persistent_session_id", None)
    current_session = ""
    if callable(peek) and sender_id:
        try:
            current_session = str(peek(role_name=lanlan_name, sender_id=sender_id) or "")
        except Exception:
            logger.debug("[OpenClaw] peek_persistent_session_id failed", exc_info=True)
    # If the current session cannot be read, **the opening side must fail closed**. Previously
    # `if current_session and ...` let peek exceptions (corrupt cache reads) or empty returns set
    # `current_session` to "", **skipping** the entire session filter so any session's entry opened the gate.
    # This reverses narrow opening / broad invalidation on the session dimension: skipping invalidation filtering broadens it safely,
    # but skipping opening filters fails open. Handle the two require_session sides separately.
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
        # Invalidation does not narrow by role (match_lanlan=False). The upstream session key uses
        # `_build_session_key`, whose first line is `del role_name`: **all roles for the same sender
        # share one QwenPaw session**. Role B's stop therefore cancels the same pending action referenced by role A's
        # approval prompt. Filtering lanlan_name leaves A's window alive, allowing the user to switch back
        # to A and casually approve the just-stopped action.
        # Cost: if A awaits approval while A and B coexist, B's stop forces A to use the literal
        # command. This fails closed; session filtering remains, avoiding cross-session invalidation.
        if (
            match_lanlan
            and lanlan_name
            and str(info.get("lanlan_name") or "").strip() != str(lanlan_name).strip()
        ):
            continue
        # queued / running are **also excluded**: the same criterion applies because in-flight replies have not returned.
        # _run_openclaw_dispatch waits for run_instruction to return, writes completed,
        # then calls _emit_task_result, so a running task **cannot** have delivered its approval prompt
        # to the user. Including it would let an unrelated running task authorize a high-risk action,
        # precisely the scenario this gate was created to block (Codex P1).
        if info.get("status") not in _APPROVAL_WINDOW_STATUSES:
            continue
        # Entries already redeemed for inferred approval no longer count: one prompt authorizes once.
        if info.get(_APPROVAL_CONSUMED_KEY):
            continue
        # Only a reply that **asked a question** might be an approval prompt. Invalidation (the require_session=False
        # path) skips this check: prefer excessive invalidation over missing it.
        if require_session:
            raw = info.get("result")
            reply = str((raw or {}).get("reply") or "") if isinstance(raw, dict) else ""
            if not any(marker in reply for marker in _APPROVAL_PROMPT_MARKERS):
                continue
        # `require_session` disables the **entire session criterion**, not merely failing closed when
        # the session is unavailable. With `if current_session and ...`, the switch is misleading:
        # invalidation still matches sessions, making another session's window impossible to invalidate. Although rotated
        # old sessions never become current again (and cannot open the gate), if the global invariant invalidation ⊇ opening
        # fails in any dimension, the next maintainer will stumble over it here again.
        if (
            require_session
            and current_session
            and str(info.get("session_id") or "").strip() != current_session
        ):
            continue
        # Invalidation skips age checks (age_bounded=False), since their results **can change over time**:
        #   Lower bound: a clock rollback makes age negative, hiding the entry from invalidation; when the clock
        #   catches up it reenters the window, after /stop has already passed with nobody left to invalidate it.
        #   Upper bound: likewise, an overage entry excluded today can reenter after clock rollback.
        #   Missing end_time: opening fails closed and skips it, but end_time can be written later,
        #   opening the window afterward.
        # All three mean excluded now but possibly included later. Invalidating entries currently outside the window is harmless
        # (they cannot open the gate anyway); missing one creates a hole, so invalidate them all.
        if not age_bounded:
            matches.append(task_id)
            continue
        end_time = str(info.get("end_time") or "").strip()
        if not end_time:
            # Terminal without end_time: age is unknown, so fail closed and deny.
            continue
        try:
            ended = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        # Require a lower bound as well as an upper bound. Clock rollback or future end_time makes
        # (now - ended) negative; checking only the upper bound keeps the window open until five minutes after the clock catches up.
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
            # `/daemon approve` really authorizes a pending high-risk action upstream, yet its entire path
            # never checked whether the reply targeted a pending approval. The repository contains no
            # pending approval state (searching pending_approval / awaiting_approval / approval_state
            # yields no matches), so casual replies such as 同意 / 没问题 would approve unconditionally.
            #
            # The complete fix gates on an actual pending approval, but that state **exists only in the
            # upstream QwenPaw daemon**: run_instruction is one POST request/response without a
            # side channel, inaccessible to N.E.K.O. Exposing it requires a response field or
            # status endpoint from QwenPaw, changing the cross-repository contract.
            #
            # Use the locally available approximation: approval is meaningless without a live openclaw task,
            # so discard it directly. This closes casual-chat cases (the vast majority of mistaken approvals);
            # behavior while real tasks run stays unchanged, with that window narrowed by the classifier's whole-clause
            # allowlist.
            #
            # The gate covers only approval **inferred from free text**. Explicitly typed magic words
            # (`/openclaw approve` via core/turn.py's explicit branch, or directly typing
            # `/daemon approve` in chat) have unambiguous intent and must pass unchanged. On that path,
            # _emit_task_result is the **only** user-visible response; gating it swallows the typed command,
            # drops the graph, consumes the turn, and shows nothing, causing repeated retyping.
            #
            # Silently discard without _emit_task_result, avoiding the fixed permission-received announcement.
            # Magic commands do not enter task_registry (registration is in the nonmagic branch below);
            # exclude_task_id is defensive.
            explicitly_typed = False
            if isinstance(result.tool_args, dict):
                # Determining whether the user typed it requires **strict** parsing: the entire input must be
                # the slash-prefixed command. Loose parsing treats ordinary English `stop` / `approve` as explicit commands,
                # granting casual English chat an explicit exemption from the entire approval gate below.
                parse_typed = getattr(
                    _shared.Modules.openclaw, "parse_typed_magic_command", None
                )
                explicitly_typed = callable(parse_typed) and (
                    parse_typed(result.tool_args.get("original_user_text")) == magic_command
                )
            # Proactive turns have **no user**. task_executor replaces intent with the catgirl's latest line
            # before classification, so her casual agreement becomes approval,
            # despite the user saying nothing in that turn (Codex P1). Approval is the sole command that actually
            # executes upstream high-risk actions; always reject it in proactive turns regardless of registry contents.
            if magic_command == "/daemon approve" and proactive:
                logger.info(
                    "[OpenClaw] /daemon approve dropped: proactive turn has no user "
                    "authorization (lanlan=%s)",
                    lanlan_name,
                )
                return
            if magic_command == "/daemon approve":
                # Explicit commands bypass **admission checks, not redemption**. Previously a single
                # `not explicitly_typed` skipped both: the user typed `/openclaw approve` to answer
                # the prompt, yet the window remained and casual agreement within TTL could approve again,
                # without a corresponding prompt (Codex P1). Exemption follows unambiguous explicit intent that
                # should not be blocked; it says nothing about whether the prompt has already been answered.
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
                # Inferred approval redeems **all** windows, **regardless of this call's success**.
                #
                # Redeem all: `/daemon approve` has no task ID, so we **cannot know** which
                # completed entry carried the prompt. Redeeming only one leaves another window that a subsequent casual
                # agreement could use to approve another pending action without a new prompt (Codex P1).
                # Cost: when two tasks ask in succession, the second agreement is dropped and requires a literal command.
                #
                # Ignore success: `run_instruction` can return success=False even **after a successful POST**
                # when no reply is available (openclaw_adapter's did-not-return-a-final-reply branch),
                # conflating never sent with sent and possibly executed but unreadable
                # reply. Retaining the window is correct in the former, but gives the same agreement
                # a second chance to authorize another action in the latter. They are indistinguishable; fail closed.
                #
                # Place this **before** await, as with /stop, so escaping exceptions cannot skip it.
                #
                # This gate is **not a multiuser isolation boundary**. `_resolve_openclaw_sender_id`
                # reads sender_id from analyze messages, but main_logic never attaches identity there
                # (cross_server.py has no sender_id / user_id matches), so it always returns empty and
                # falls back to `default_sender_id`: **all users share one bucket**. This fallback
                # already exists on origin/main (sender filtering worked this way before this PR); more fundamentally,
                # upstream `_build_session_key` uses `user::<sender>`, so multiuser deployments
                # share one QwenPaw session, not just this gate.
                # **Passing identity downstream would not close this either**: explicit `/daemon approve` bypasses admission
                # by design (otherwise user commands are silently swallowed), so B can still approve A's pending action. Multiuser isolation
                # requires upstream to identify whose prompt it is, a cross-repository contract; see the PR description's TODO.
                # This gate prevents **misreading free text as authorization**, a property independent of sender-bucket breadth.
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
                # Imperatives such as 停下来 / 别找了 are identical in everyday conversation; users may be addressing
                # **the catgirl herself**, especially in roleplay, rather than stopping background tasks. Whole-clause matching
                # excludes narration (`雨停下来了` does not match), but cannot exclude these imperatives.
                # This tier therefore additionally requires evidence of an **actually running openclaw task**.
                #
                # Phrases explicitly targeting the agent (取消这个任务 / 停止搜索 ...) **are exempt**,
                # as are literal commands. Deliberately so: the registry lies precisely when /stop is most needed,
                # after a request times out and becomes failed, after restart empties the registry, or after TTL
                # cleanup, while upstream work may still be running. This POST is the only channel that can stop it;
                # gating the whole path on the registry would seal the escape valve.
                tier = None
                tier_fn = getattr(_shared.Modules.openclaw, "stop_trigger_tier", None)
                if callable(tier_fn) and isinstance(result.tool_args, dict):
                    try:
                        tier = tier_fn(result.tool_args.get("original_user_text"))
                    except Exception:
                        logger.debug("[OpenClaw] stop_trigger_tier failed", exc_info=True)
                # Evidence includes **an outstanding approval prompt**, not just running work. When the prompt is emitted,
                # no queued/running task exists: completed opens the window, and the two state sets are
                # disjoint. Checking only running tasks returns early and skips the invalidation code below:
                # the user's stop is **rejecting** the just-issued prompt, yet is silently discarded,
                # leaving a window for casual agreement to approve the very action they rejected.
                #
                # The window side must use the **narrow** criterion (`_find_approval_window_task`), not invalidation's
                # relaxed filtering. Corroboration is an **opening** decision, so narrow opening / broad invalidation applies:
                # terminal entries can linger indefinitely on this path (cleanup only runs through capabilities);
                # broad filtering would let **an hours-old completed entry that asked no question** permit every later
                # stop, nullifying the tiered guard (Codex P2). Narrow criteria preserve the rejection case above:
                # at rejection the window is already fresh, same-role, same-session, and marked as a question.
                #
                # The running-task side **does not narrow by role**: upstream session keys use sender alone
                # (`_build_session_key` starts with `del role_name`), so work under another role for the same sender
                # is a legitimate target for stop, and that /stop POST addresses
                # the same upstream session.
                corroborated = _collect_active_openclaw_task_ids(
                    sender_id=nk_sender_id,
                    lanlan_name=None,
                    exclude_task_id=result.task_id,
                ) or _find_approval_window_task(
                    # The window side likewise **does not narrow by role**, for the same reason; otherwise it would be
                    # inconsistent: invalidation is role-independent and running-task corroboration was already broadened,
                    # but role matching here would return early when a prompt arrives under A and the user switches to B to stop,
                    # skipping invalidation and preserving A's window for the next casual agreement.
                    # All narrow dimensions remain: fresh / same session / question marker / unredeemed.
                    sender_id=nk_sender_id,
                    lanlan_name=None,
                    exclude_task_id=result.task_id,
                )
                if tier != "addressed" and not corroborated:
                    logger.info(
                        "[OpenClaw] /stop dropped: ambiguous phrasing with no running "
                        "task for sender=%s lanlan=%s",
                        nk_sender_id,
                        lanlan_name,
                    )
                    return
            if magic_command == "/stop":
                # Invalidate **before** the cancellation helper. That helper contains `await` and an unguarded
                # `_task_tracker.record_completed`; an exception would skip all subsequent invalidation,
                # leaving cancelled tasks' windows alive. Invalidation depends on no cancellation result, so
                # placing it first is strictly beneficial, as with placing it before `run_magic_command`.
                # Cancelling tasks cannot remove **an already-issued approval prompt**: _cancel_... selects only
                # queued/running, while completed opens windows; the state sets are disjoint. Without
                # redemption, casual agreement after the user's stop can reopen the just-revoked action
                # throughout TTL (Codex P1). This extends the same criterion: windows ask whether the user
                # could have seen the prompt, and `/stop` is their answer to it.
                #
                # Keep this outside if cancelled_task_ids: Codex's scenario specifically has **no**
                # running task to cancel (just completed, awaiting approval), leaving that list empty.
                #
                # Do not wait for run_magic_command success either: local cancellation (the helper above writes
                # status=cancelled) is never rolled back, and the user's stop is independent of upstream call
                # success. To approve after stopping, type /daemon approve directly: explicit commands are exempt.
                #
                # Proactive turns are excluded. The reason approval is blocked above is that proactive turns **have no
                # user**; the same applies here. Invalidation relies on /stop being the user's answer to the
                # prompt, whereas in proactive turns the user said nothing: the catgirl's own line
                # entered the classifier. Preventing approval for the user while allowing revocation for them is contradictory.
                # Here the consequence is **silence**, not extra approval: she removes the window,
                # then the user's agreement returns directly from the approve gate without _emit_task_result,
                # showing nothing and prompting repeated attempts.
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
                # Cancellation also skips role narrowing, or it contradicts corroboration above: this /stop was allowed **because**
                # work exists under another role, yet failing to cancel it leaves the UI and tracker
                # showing work the user just stopped. Upstream POST targets the shared session and already stops
                # that work; failing to update local state would be misleading.
                cancelled_task_ids = await _cancel_openclaw_tasks_for_stop(
                    sender_id=nk_sender_id,
                    lanlan_name=None,
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
                # Redemption already occurred **before** dispatch (see above). Previously this redeemed one entry based on success,
                # but both assumptions fail: success=False does not mean unsent,
                # and a single entry is not necessarily the one carrying the prompt.
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
                pass  # Best-effort instrumentation.
            try:
                nk_result = await _shared.Modules.openclaw.run_instruction(
                    instruction,
                    attachments=attachments,
                    sender_id=nk_sender_id,
                    session_id=nk_session_id,
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
