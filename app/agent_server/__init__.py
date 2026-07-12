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

"""Agent (tool) server package.

Formerly the monolithic ``app/agent_server.py`` (5.7k lines); now split by
domain following the ``main_routers/system_router`` facade pattern (#2148):
``_shared`` (state bag + primitives), ``tracker``, ``registry``,
``plugin_host``, ``capabilities``, ``results`` and ``channels/`` (one
symmetric dispatch module per execution method). The import path
``app.agent_server`` and every top-level symbol are unchanged — this facade
re-exports them all, and the launcher keeps using
``from app import agent_server; agent_server.app``.

The HTTP routes, the ZMQ session-event handling, the analyzer dispatcher
and the runtime-intent restore family intentionally stay in this module:
their function ``__globals__`` must be THIS module dict so that existing
``monkeypatch.setattr(app.agent_server, "<helper>", ...)`` calls in tests
keep rebinding the very names those functions resolve at call time.

Not re-exported (rebindable owner globals; a facade snapshot would go stale
on every rebind): ``registry._task_registry_last_cleanup`` and
``plugin_host._plugin_name_cache`` / ``_plugin_name_cache_time``.

Run directly with ``python -m app.agent_server`` (replaces the former
``python app/agent_server.py``).
"""

import sys
import os
# Three levels up from this file (app/agent_server/__init__.py); the former
# monolith computed two levels up from app/agent_server.py.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Always insert at position 0 so project-root ``utils/`` (and ``config/``,
# etc.) are found *before* ``plugin/`` which may contain identically-named
# sub-packages.  The check ``not in`` is deliberately removed: ``_repo_root``
# may already exist later in sys.path (e.g. via .venv site-packages), but
# that position loses to ``plugin/`` which is inserted at index 1 by
# ``_start_embedded_user_plugin_server`` (plugin_host.py).
if sys.path[0:1] != [_repo_root]:
    sys.path.insert(0, _repo_root)

# Wire DI bindings explicitly — direct script invocation
# (``python -m app.agent_server``) doesn't run app/__init__.py.
# Idempotent under launcher's ``from app import agent_server`` path too.
from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

import mimetypes
import json
mimetypes.add_type("application/javascript", ".js")
import asyncio
import uuid
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ``_shared`` calls setup_logging() before any config/brain import so
# import-time failures are persisted — same order as the old monolith.
from ._shared import (  # noqa: F401
    logger,
    log_config,
    setup_logging,
    ThrottledLogger,
    AgentServerEventBridge,
    ComputerUseAdapter,
    BrowserUseAdapter,
    OpenClawAdapter,
    OpenFangAdapter,
    TaskDeduper,
    DirectTaskExecutor,
    get_session_manager,
    parse_computer_use_result,
    parse_browser_use_result,
    parse_plugin_result,
    _rp_phrase,
    _rp_lang,
    Modules,
    PLUGIN_NAME_CACHE_TTL,
    TASK_REGISTRY_CLEANUP_TTL,
    DEFERRED_TASK_TIMEOUT,
    OPENCLAW_ENABLE_CHECK_ATTEMPTS,
    OPENCLAW_ENABLE_CHECK_INTERVAL,
    _get_throttled_logger,
    _bump_state_revision,
    _set_capability,
    _track_background_task,
    _create_tracked_task,
)

from config import (  # noqa: F401  (tail entries keep facade parity with the old monolith namespace)
    USER_PLUGIN_SERVER_PORT,
    OPENFANG_BASE_URL,
    TASK_ERROR_MAX_TOKENS,
    EXCEPTION_TEXT_MAX_CHARS,
    USER_NOTIFICATION_REASON_MAX_CHARS,
    USER_NOTIFICATION_ERROR_MAX_CHARS,
    TOOL_SERVER_PORT,
    TASK_DETAIL_MAX_TOKENS,
    AGENT_HISTORY_TURNS,
    ERROR_MESSAGE_MAX_CHARS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    TASK_TRACKER_INJECT_DETAIL_MAX_CHARS,
    AGENT_PROACTIVE_ANALYZE_ENABLED,
    AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION,
)
from utils.config_manager import get_config_manager
from utils.tokenize import truncate_to_tokens as _tt

from .tracker import (  # noqa: F401
    TASK_TRACKER_MAX_RECORDS,
    TASK_TRACKER_TTL,
    AgentTaskTracker,
    _task_tracker,
    _normalize_lanlan_key,
    _user_message_sender_id,
    _user_message_payload_text,
    _build_user_turn_fingerprint,
    _build_assistant_turn_fingerprint,
    _build_analyze_event_fingerprint,
    _user_message_signature,
    _last_user_message_signature,
    REDACTED_USER_TURN_MARKER,
    _redact_cancelled_user_turns,
)
from .registry import (  # noqa: F401
    _LEGACY_CORRECTION_PUBLIC_KEYS,
    _now_iso,
    _cleanup_task_registry,
    _collect_existing_task_descriptions,
    _is_duplicate_task,
    _spawn_task,
    _set_internal_correction_context,
    _get_internal_correction_context,
    _tracker_desc_for_task_info,
    _public_task_info,
    _spawn_background_cancel,
)
from .plugin_host import (  # noqa: F401
    _plugin_name_cache_lock,
    _bind_deferred_task,
    _get_plugin_friendly_name,
    _get_plugin_display_id,
    _start_embedded_user_plugin_server,
    _stop_embedded_user_plugin_server,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _fire_user_plugin_capability_check,
)
from .capabilities import (  # noqa: F401
    _rewire_computer_use_dependents,
    _try_refresh_computer_use_adapter,
    _llm_check_lock,
    _fire_agent_llm_connectivity_check,
    _agent_flags_snapshot,
    _collect_agent_status_snapshot,
    _emit_agent_status_update,
)
from .results import (  # noqa: F401
    _emit_task_result,
    _emit_main_event,
)
from . import channels
from .channels.computer_use import (  # noqa: F401
    _run_computer_use_task,
    _computer_use_scheduler_loop,
)
from .channels.openclaw import (  # noqa: F401
    _default_openclaw_task_description,
    _resolve_openclaw_sender_id,
    _collect_active_openclaw_task_ids,
    _cancel_openclaw_tasks_for_stop,
    _openclaw_pending,
    _cancel_openclaw_enable_probe,
    _openclaw_first_reason,
    _openclaw_reason_code,
    _openclaw_reason_text,
    _openclaw_notification,
    _start_openclaw_enable_probe,
    _run_openclaw_enable_probe,
)
from .channels.openfang import (  # noqa: F401
    _patch_openai_response,
    _patch_usage,
    _patch_malformed_tool_calls,
    _extract_tool_intent_as_text,
)
from .channels.user_plugin import (  # noqa: F401
    _plugin_terminal_status,
    _resolve_delivery_mode,
    _lookup_llm_result_fields,
    _is_reply_suppressed,
)


app = FastAPI(title="N.E.K.O Tool Server")


class ToolCorrectionPayload(BaseModel):
    correct_tool: str = Field(min_length=1)
    correct_instruction: str = Field(min_length=1)
    user_note: str = ""


def _check_agent_api_gate() -> Dict[str, Any]:
    """Unified agent API gate check."""
    try:
        cm = get_config_manager()
        ok, reasons = cm.is_agent_api_ready()
        return {"ready": ok, "reasons": reasons, "is_free_version": cm.is_agent_free()}
    except Exception as e:
        return {"ready": False, "reasons": [f"Agent API check failed: {e}"], "is_free_version": False}


def _agent_master_enabled() -> bool:
    return bool(Modules.analyzer_enabled)


def _user_plugins_enabled() -> bool:
    return bool((Modules.agent_flags or {}).get("user_plugin_enabled", False))


def _voice_transcript_plugin_gate_reason() -> str:
    if not _agent_master_enabled():
        return "agent_disabled"
    if not _user_plugins_enabled():
        return "user_plugin_disabled"
    return ""


async def _handle_voice_transcript_request(event: Dict[str, Any]) -> None:
    event_id = str((event or {}).get("event_id") or "")
    lanlan_name = (event or {}).get("lanlan_name")

    try:
        from plugin.server.application.plugins import voice_transcript_bridge

        if not voice_transcript_bridge.voice_transcript_event_has_text(event):
            logger.debug("[VoiceBridge] observed transcript skipped: empty event_id=%s", event_id)
        elif gate_reason := _voice_transcript_plugin_gate_reason():
            if gate_reason == "agent_disabled":
                logger.debug("[VoiceBridge] observed transcript skipped: agent disabled event_id=%s", event_id)
            else:
                logger.debug(
                    "[VoiceBridge] observed transcript skipped: user plugins disabled event_id=%s",
                    event_id,
                )
        else:
            lifecycle_ready = bool(Modules.plugin_lifecycle_started)
            if not lifecycle_ready:
                lifecycle_ready = await _ensure_plugin_lifecycle_started()

            if not lifecycle_ready:
                logger.debug(
                    "[VoiceBridge] observed transcript skipped: plugin lifecycle unavailable event_id=%s",
                    event_id,
                )
            else:
                result = await voice_transcript_bridge.resolve_voice_transcript_request(
                    event,
                    timeout=voice_transcript_bridge.VOICE_TRANSCRIPT_DISPATCH_TIMEOUT_SECONDS,
                )
                logger.debug(
                    "[VoiceBridge] observed transcript dispatched: event_id=%s lanlan=%s action=%s",
                    event_id,
                    lanlan_name,
                    result.get("action") if isinstance(result, dict) else "",
                )
    except Exception as exc:
        logger.debug(
            "[VoiceBridge] plugin dispatch failed: event_id=%s lanlan=%s err=%s",
            event_id,
            lanlan_name,
            exc,
        )


def _handle_proactive_analyze(messages, lanlan_name, lanlan_key, conversation_id) -> None:
    """Throttled proactive-analyze path (opt-in via AGENT_PROACTIVE_ANALYZE_ENABLED).

    A proactive turn has no new user input, so the ordinary user-turn dedupe
    would drop it. Instead we let lanlan's self-initiated utterance trigger one
    analyzer pass, bounded by three gates so it can never fire frequently:
      * the master enable switch (off → never run);
      * an assistant-text fingerprint (dedupe identical proactive utterances —
        a re-sent proactive turn must not re-analyze);
      * a per-session count cap (AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION), reset
        on greeting_check. This is the anti-cheap-layer / cost ceiling: it counts
        analyzer RUNS (incl. ones that dispatch no tool), so a session can spend
        at most N proactive analyzer calls regardless of how chatty lanlan is.
    """
    if not bool(AGENT_PROACTIVE_ANALYZE_ENABLED):
        logger.info("[AgentAnalyze] skip proactive: disabled (lanlan=%s)", lanlan_name)
        return
    # fp is None ⟺ no assistant utterance to analyze (the executor pulls the
    # actual proactive intent text from the same assistant turn downstream).
    fp = _build_assistant_turn_fingerprint(messages)
    if fp is None:
        logger.info("[AgentAnalyze] skip proactive: no assistant utterance (lanlan=%s)", lanlan_name)
        return
    if Modules.last_proactive_assistant_fingerprint.get(lanlan_key) == fp:
        logger.info("[AgentAnalyze] skip proactive: duplicate proactive utterance (lanlan=%s)", lanlan_name)
        return
    cap = max(0, int(AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION))
    used = int(Modules.proactive_analyze_count.get(lanlan_key, 0))
    if used >= cap:
        logger.info("[AgentAnalyze] skip proactive: per-session cap reached (%d/%d, lanlan=%s)", used, cap, lanlan_name)
        return
    # Reserve the slot + dedupe fp BEFORE dispatch so concurrent proactive events
    # can't both pass the cap check.
    Modules.proactive_analyze_count[lanlan_key] = used + 1
    Modules.last_proactive_assistant_fingerprint[lanlan_key] = fp
    logger.info("[AgentAnalyze] proactive analyze accepted (%d/%d, lanlan=%s)", used + 1, cap, lanlan_name)
    _create_tracked_task(_background_analyze_and_plan(
        messages, lanlan_name, conversation_id=conversation_id,
        external_intent=None, proactive=True,
    ))


async def _on_session_event(event: Dict[str, Any]) -> None:
    event_type = (event or {}).get("event_type")
    if event_type == "agent_intent_restore_signal":
        # First-real-client-session signal from main_server (sent on
        # ``greeting_check``). Restore persisted agent runtime intent now
        # — agent_server is fully ready (we're already receiving events),
        # but we delayed restore to here so we don't trigger LLM probes
        # and plugin lifecycle startup during the cold-start window
        # before the user actually opens a session. The restore helper
        # has its own once-flag, so this is safe to spam.
        # Reset the per-session proactive-analyze budget ONLY on a genuine new
        # session (character switch or a real gap) — never on a refresh/reconnect
        # or a concurrent second window, which also fire greeting_check. Otherwise
        # a user could refresh/parallel-open to farm a fresh cap mid-conversation,
        # defeating the per-session bound. ``new_session`` is decided by
        # websocket_router (is_switch or >15s gap, AND sole active connection).
        # Done BEFORE the restore await so a restore failure can't leave a genuine
        # new session stuck on the old cap / fingerprint.
        if (event or {}).get("new_session"):
            _key = _normalize_lanlan_key((event or {}).get("lanlan_name"))
            if _key:
                Modules.proactive_analyze_count.pop(_key, None)
                Modules.last_proactive_assistant_fingerprint.pop(_key, None)
        await _maybe_restore_agent_intent()
        return
    if event_type in {"voice_transcript_observed", "voice_transcript_request"}:
        _create_tracked_task(_handle_voice_transcript_request(event))
        return
    if event_type == "analyze_request":
        messages = event.get("messages", [])
        lanlan_name = event.get("lanlan_name")
        event_id = event.get("event_id")
        logger.info("[AgentAnalyze] analyze_request received: trigger=%s lanlan=%s messages=%d", event.get("trigger"), lanlan_name, len(messages) if isinstance(messages, list) else 0)
        if event_id:
            _create_tracked_task(_emit_main_event("analyze_ack", lanlan_name, event_id=event_id))
        if not _agent_master_enabled():
            logger.info("[AgentAnalyze] skip: analyzer disabled (master switch off)")
            return
        if isinstance(messages, list) and messages:
            lanlan_key = _normalize_lanlan_key(lanlan_name)
            conversation_id = event.get("conversation_id")
            # Proactive (self-initiated, no fresh user input) turn: opt-in,
            # separate throttled path. The ordinary user-turn dedupe below would
            # always drop these (the latest user message is a stale prior turn,
            # so its fingerprint matches), so proactive routing is mandatory, not
            # an optimization.
            if event.get("proactive"):
                _handle_proactive_analyze(messages, lanlan_name, lanlan_key, conversation_id)
                return
            # Consume only new user turn. Assistant turn_end without new user input should be ignored.
            fp = _build_analyze_event_fingerprint(event)
            if fp is None:
                logger.info("[AgentAnalyze] skip analyze: no user message found (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            if Modules.last_user_turn_fingerprint.get(lanlan_key) == fp:
                logger.info("[AgentAnalyze] skip analyze: no new user turn (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            # Fingerprint changed → genuinely new user content; always allow.
            # Re-dispatch prevention is handled by:
            # - _is_duplicate_task() checking recently completed tasks
            # - Cancelled tasks not emitting task_result callbacks
            # - Voice-mode hot-swap sending 'turn end agent_callback'
            Modules.last_user_turn_fingerprint[lanlan_key] = fp
            # Cheap pre-gate hint from the input-time master-emotion call (rides
            # the analyze_request payload). Absent → None → the gate fails open.
            external_intent = event.get("external_intent")
            _create_tracked_task(_background_analyze_and_plan(
                messages, lanlan_name, conversation_id=conversation_id,
                external_intent=external_intent,
            ))


async def _background_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str], conversation_id: Optional[str] = None, external_intent: Optional[float] = None, proactive: bool = False):
    """
    [Simplified] Uses DirectTaskExecutor to do everything in one step: analyze the conversation + decide the execution method + execute the task
    
    Simplified chain:
    - old: Analyzer(LLM#1) → Planner(LLM#2) → subprocess Processor(LLM#3) → MCP call
    - new: DirectTaskExecutor(LLM#1) → MCP call

    Args:
        messages: conversation message list
        lanlan_name: character name
        conversation_id: conversation ID, used to associate the trigger event with the conversation context

    Uses analyze_lock to serialize concurrent calls.  Without this, two
    near-simultaneous analyze_request events can both pass the dedup
    check before either spawns a task, resulting in duplicate execution.
    """
    if not Modules.task_executor:
        logger.warning("[TaskExecutor] task_executor not initialized, skipping")
        return

    # Lazy-init the lock (must happen inside the event loop)
    if Modules.analyze_lock is None:
        Modules.analyze_lock = asyncio.Lock()

    async with Modules.analyze_lock:
        await _do_analyze_and_plan(messages, lanlan_name, conversation_id=conversation_id, external_intent=external_intent, proactive=proactive)


async def _do_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str], conversation_id: Optional[str] = None, external_intent: Optional[float] = None, proactive: bool = False):
    """Inner implementation, always called under analyze_lock."""
    try:
        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping analysis: analyzer disabled (master switch off)")
            return
        logger.info("[AgentAnalyze] background analyze start: lanlan=%s messages=%d flags=%s analyzer_enabled=%s",
                    lanlan_name, len(messages), Modules.agent_flags, Modules.analyzer_enabled)
        # 在 inject 之前先把已被用户 UI 取消的 user turn 整段 redact，让 analyzer
        # 完全看不到那条请求；inject 阶段也会跳过 cancelled 任务的所有 record。
        redacted_messages = _redact_cancelled_user_turns(messages, lanlan_name, preserve_trailing_assistant=proactive)
        # 单条 user 消息签名：派单时塞到 task info 里。取自 redacted_messages
        # 而非 raw —— analyzer 实际看到的最新 user 才是该任务的真触发者；
        # 正常场景下 raw-latest 是 first-time bypass、没被 redact，两个签名
        # 一致，区别仅在 raw-latest 已经被 redact 的边界 case。
        # 主动搭话轮没有触发它的 user 消息：绝不把它绑到窗口里那条陈旧 user 签名上，
        # 否则用户取消这条主动任务会误把那条旧 user turn 标记为 cancelled、下一轮被
        # redact 掉。proactive → 不绑 user 触发签名。
        trigger_user_msg_sig = None if proactive else _last_user_message_signature(redacted_messages)
        enriched_messages = _task_tracker.inject(redacted_messages, lanlan_name)

        # 一步完成：分析 + 执行
        result = await Modules.task_executor.analyze_and_execute(
            messages=enriched_messages,
            lanlan_name=lanlan_name,
            agent_flags=Modules.agent_flags,
            conversation_id=conversation_id,
            external_intent=external_intent,
            proactive=proactive,
        )

        if result is None:
            return
        
        if not result.has_task:
            reason = getattr(result, "reason", "") or ""
            if "error" in reason.lower() or "timed out" in reason.lower() or "failed" in reason.lower():
                logger.warning("[TaskExecutor] Assessment failed: %s", reason)
                await _emit_main_event(
                    "agent_notification", lanlan_name,
                    text=f"⚠️ Agent评估失败: {reason[:USER_NOTIFICATION_REASON_MAX_CHARS]}",
                    source="brain",
                    status="error",
                    error_message=reason[:USER_NOTIFICATION_ERROR_MAX_CHARS],
                )
            else:
                logger.debug("[TaskExecutor] No actionable task found")
            return

        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping dispatch: analyzer disabled during analysis")
            return
        
        logger.info(
            "[TaskExecutor] Task: desc='%s', method=%s, tool=%s, entry=%s, reason=%s",
            (result.task_description or "")[:80],
            result.execution_method,
            getattr(result, "tool_name", None),
            getattr(result, "entry_id", None),
            (getattr(result, "reason", "") or "")[:120],
        )

        # Per-channel dispatch: one symmetric channels/<method>.py::dispatch()
        # per execution_method, preserving the elif order of the old monolith.
        if result.execution_method == 'mcp':
            await channels.mcp.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
            )
        elif result.execution_method == 'computer_use':
            await channels.computer_use.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
            )
        elif result.execution_method == 'user_plugin':
            await channels.user_plugin.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
            )
        elif result.execution_method == 'openclaw':
            await channels.openclaw.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
                proactive=proactive,
            )
        elif result.execution_method == 'browser_use':
            await channels.browser_use.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
            )
        elif result.execution_method == 'openfang':
            await channels.openfang.dispatch(
                result,
                messages=messages,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                trigger_user_msg_sig=trigger_user_msg_sig,
            )
        else:
            logger.info(f"[TaskExecutor] No suitable execution method: {result.reason}")
    
    except Exception as e:
        logger.error(f"[TaskExecutor] Background task error: {e}", exc_info=True)
        try:
            await _emit_main_event(
                "agent_notification", lanlan_name,
                text=f"💥 Agent后台任务异常: {type(e).__name__}: {e}",
                source="brain",
                status="error",
                error_message=str(e)[:USER_NOTIFICATION_ERROR_MAX_CHARS],
            )
        except Exception:
            logger.debug("[TaskExecutor] emit notification failed", exc_info=True)

@app.on_event("startup")
async def startup():
    # Install token tracking hooks for this process
    try:
        from utils.token_tracker import TokenTracker, install_hooks
        install_hooks()
        TokenTracker.get_instance().start_periodic_save()
        # process 字段进 session_start / session_end 维度，跨进程诊断必须区分
        TokenTracker.get_instance().record_app_start(process="agent_server")
    except Exception as e:
        logger.warning(f"[Agent] Token tracker init failed: {e}")

    # 注：模块预热统一由 main_server 在其 runtime init 完成后触发（见
    # _ensure_main_server_runtime_initialized 末尾）。合并模式下三个 app 同进程，
    # 那一处覆盖本进程全部 lazy 模块；不在这里另起，避免与启动期抢 GIL。

    os.environ["NEKO_PLUGIN_HOSTED_BY_AGENT"] = "true"
    Modules.computer_use = ComputerUseAdapter()
    Modules.openclaw = OpenClawAdapter()
    Modules.task_executor = DirectTaskExecutor(
        computer_use=Modules.computer_use,
        browser_use=None,
        openclaw=Modules.openclaw,
    )
    Modules.deduper = TaskDeduper()
    Modules.throttled_logger = ThrottledLogger(logger, interval=30.0)
    _rewire_computer_use_dependents()

    async def _init_browser_use_background():
        try:
            bu = await asyncio.to_thread(BrowserUseAdapter)
            Modules.browser_use = bu
            Modules.task_executor.browser_use = bu
            logger.info("[Agent] BrowserUseAdapter ready (background init)")
            # fire-and-forget capability 刷新：check_connectivity 可能因网络不稳
            # 走到几十秒级的重试，绝不能把 OpenFang 初始化链 gate 在它上面。
            # queue=True：这是"BU 刚就绪"这种状态变化触发，不能被启动期 LLM probe
            # 持锁时的早退路径吞掉，否则 browser_use capability 会停在 PENDING。
            _refresh_task = asyncio.create_task(
                _fire_agent_llm_connectivity_check(queue=True)
            )
            Modules._persistent_tasks.add(_refresh_task)
            _refresh_task.add_done_callback(Modules._persistent_tasks.discard)
        except Exception as exc:
            logger.error("[Agent] BrowserUseAdapter background init failed: %s", exc)

    try:
        await _start_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Failed to start embedded user plugin server: {e}")
    # ── OpenFang 后台初始化 (仅通信层，进程由 Electron 管理) ──
    async def _init_openfang_background():
        """Wait for OpenFang daemon connectivity + sync config + register the executor agent."""
        try:
            adapter = OpenFangAdapter(base_url=OPENFANG_BASE_URL)
            Modules.openfang = adapter
            Modules.task_executor.openfang = adapter

            # 等待 OpenFang 就绪 (由 Electron 并行启动，通常 <1s)
            # check_connectivity 是同步 httpx 调用，用 to_thread 避免阻塞 event loop
            for _attempt in range(30):
                ok = await asyncio.to_thread(adapter.check_connectivity)
                if ok:
                    break
                await asyncio.sleep(1)

            if not adapter.init_ok:
                logger.warning("[OpenFang] not reachable after 30s")
                _set_capability("openfang", False, "OPENFANG_DAEMON_UNREACHABLE")
                return

            # 同步 API Key + 写 config.toml（允许失败 — 用户可能尚未配置 Key）
            try:
                await adapter.sync_config()
            except Exception as e:
                logger.warning("[OpenFang] sync_config failed (non-fatal): %s", e)

            # 等待 OpenFang 检测并 reload config.toml
            # OpenFang 用文件监听检测 config 变化，但 reload 可能有延迟
            try:
                import os as _os
                _home = _os.environ.get("HOME") or _os.environ.get("USERPROFILE") or ""
                _cfg = _os.path.join(_home, ".openfang", "config.toml")
                if _os.path.exists(_cfg):
                    _os.utime(_cfg, None)  # touch to trigger fswatch
            except Exception:
                logger.debug("[OpenFang] failed to touch config file for fswatch", exc_info=True)
            await asyncio.sleep(5)

            # 拉取可用工具列表
            try:
                await adapter.fetch_tools_list()
            except Exception as e:
                logger.warning("[OpenFang] fetch_tools_list failed (non-fatal): %s", e)

            # 注册无人格执行 Agent（允许失败 — 连通即可用）
            # manifest 中直接带 api_key + provider=openai，不依赖环境变量
            try:
                agent_id = await adapter.push_agent_manifest()
                # agent_id 是 daemon 返回的标识符（非用户/LLM 原文），可进 logger
                logger.debug(
                    "[OpenFang] push_agent_manifest returned: %s (executor_agent_id=%s)",
                    agent_id, adapter._executor_agent_id,
                )
            except Exception as e:
                import traceback
                logger.warning("[OpenFang] push_agent_manifest failed (non-fatal): %s", e)
                logger.debug("[OpenFang] push_agent_manifest traceback:\n%s", traceback.format_exc())
                agent_id = None

            # 只要 daemon 连通就标记 ready，不强制要求 agent 注册成功
            _set_capability("openfang", True, "")
            logger.info("[OpenFang] Ready (init_ok=%s, agent=%s, tools=%d)",
                        adapter.init_ok, agent_id, adapter._cached_tools_count or 0)
        except Exception as exc:
            logger.error("[OpenFang] background init failed: %s", exc)
            _set_capability("openfang", False, str(exc))

    # BrowserUse 与 OpenFang 都涉及较重的初始化（CPU 密集模块加载 / 进程连通性轮询），
    # 放在同一个后台任务里串行执行，避免两者并发时启动期 CPU 双峰。LLM connectivity
    # probe 是轻量 HTTP，独立 task 与这条串行链并行。
    async def _init_heavy_adapters_serial():
        await _init_browser_use_background()
        await _init_openfang_background()

    _heavy_adapters_task = asyncio.create_task(_init_heavy_adapters_serial())
    Modules._persistent_tasks.add(_heavy_adapters_task)
    _heavy_adapters_task.add_done_callback(Modules._persistent_tasks.discard)

    # Both CUA and BrowserUse share the agent LLM — default to "not connected"
    # and probe in background.  The single check updates both capability caches.
    _set_capability("computer_use", False, "connectivity check pending")
    _set_capability("browser_use", False, "connectivity check pending")
    # Plugin capability = ready (embedded HTTP server is always up), but lifecycle
    # is NOT started here — it syncs with user_plugin_enabled (default OFF).
    # The lifecycle starts on-demand when the user toggles the plugin flag ON.
    _set_capability("user_plugin", True, "")
    # OpenFang capability 由 _init_openfang_background() 管理，不在此处覆盖
    _llm_probe_task = asyncio.create_task(_fire_agent_llm_connectivity_check())
    Modules._persistent_tasks.add(_llm_probe_task)
    _llm_probe_task.add_done_callback(Modules._persistent_tasks.discard)
    
    try:
        async def _http_plugin_provider(force_refresh: bool = False):
            url = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"
            if force_refresh:
                url += "?refresh=true"
            try:
                async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except Exception as parse_err:
                            logger.debug(f"[Agent] plugin_list_provider parse error: {parse_err}")
                            data = {}
                        raw = data.get("plugins", []) or []
                        # ISOLATION BOUNDARY: only expose RUNNING plugins to the
                        # analyzer / plugin LLM. Without this filter, every plugin
                        # the host knows about (including disabled, stopped,
                        # load-failed, source-missing, and extension plugins in
                        # 'pending' state) flows into the LLM's candidate set.
                        # The LLM then wastes tokens evaluating capabilities the
                        # user explicitly didn't enable, and worse — picks a
                        # plugin that has no live process to receive the dispatch,
                        # surfacing fake "available capability" to the user. See
                        # _resolve_plugin_status() in
                        # plugin/server/application/plugins/query_service.py for
                        # the full status taxonomy; "running" is the only state
                        # where the plugin's process is alive and responsive.
                        running = [
                            p for p in raw
                            if isinstance(p, dict) and p.get("status") == "running"
                        ]
                        if len(running) != len(raw):
                            dropped = [
                                (p.get("id"), p.get("status"))
                                for p in raw
                                if isinstance(p, dict) and p.get("status") != "running"
                            ]
                            logger.debug(
                                "[Agent] plugin_list_provider filtered out %d non-running plugins: %s",
                                len(dropped), dropped,
                            )
                        # AUDIENCE BOUNDARY: ``@llm_tool``-registered methods
                        # also surface as plugin entries with id prefix
                        # ``__llm_tool__<name>`` (see plugin SDK collect_entries).
                        # Those tools are *also* exposed to the dialog LLM via
                        # ``LLMSessionManager.tool_registry`` — letting the
                        # analyzer/plugin LLM dispatch them too means the same
                        # tool can be triggered by both LLMs, with the
                        # analyzer path's ~10s decision latency racing against
                        # the dialog LLM's direct call. The dialog LLM is the
                        # canonical caller for ``@llm_tool`` (it gets the
                        # tool's full schema, can pass typed args, and runs
                        # synchronously); the analyzer should only see
                        # ``@plugin_entry`` registered entries (queries /
                        # status / config). Strip ``__llm_tool__`` entries
                        # from the analyzer's view here.
                        for p in running:
                            entries = p.get("entries")
                            if isinstance(entries, list):
                                p["entries"] = [
                                    e for e in entries
                                    if not (
                                        isinstance(e, dict)
                                        and isinstance(e.get("id"), str)
                                        and e["id"].startswith("__llm_tool__")
                                    )
                                ]
                        return running
            except Exception as e:
                logger.debug(f"[Agent] plugin_list_provider http fetch failed: {e}")
            return []

        # inject http-based provider so DirectTaskExecutor can pick up user_plugin_server plugins
        try:
            Modules.task_executor.set_plugin_list_provider(_http_plugin_provider)
            logger.debug("[Agent] Registered http plugin_list_provider for task_executor")
        except Exception as e:
            logger.warning(f"[Agent] Failed to inject plugin_list_provider into task_executor: {e}")
    except Exception as e:
        logger.warning(f"[Agent] Failed to set http plugin_list_provider: {e}")

    # Start computer-use scheduler
    sch_task = asyncio.create_task(_computer_use_scheduler_loop())
    Modules._persistent_tasks.add(sch_task)
    sch_task.add_done_callback(Modules._persistent_tasks.discard)
    # Start ZeroMQ bridge for main_server events
    try:
        Modules.agent_bridge = AgentServerEventBridge(on_session_event=_on_session_event)
        await Modules.agent_bridge.start()
    except Exception as e:
        logger.warning(f"[Agent] Event bridge startup failed: {e}")
    # 免费版 Agent 每日配额耗尽 → 节流通知前端弹提示（最多每 10 秒一次）。
    # consume_agent_daily_quota 跑在 worker 线程里调这个回调，用 run_coroutine_threadsafe
    # 把异步 ZeroMQ emit 调度回 agent_server 的事件循环；不 .result()，保持非阻塞。
    try:
        _quota_notify_loop = asyncio.get_running_loop()

        def _notify_agent_quota_exceeded(used: int, limit: int) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    _emit_main_event("agent_quota_exceeded", None, used=used, limit=limit),
                    _quota_notify_loop,
                )
            except Exception as e:
                logger.debug("[Agent] schedule agent_quota_exceeded emit failed: %s", e)

        get_config_manager().register_quota_exceeded_notifier(_notify_agent_quota_exceeded)
    except Exception as e:
        logger.warning(f"[Agent] register quota-exceeded notifier failed: {e}")
    # Push initial server status so frontend can render Agent popup without waiting.
    _bump_state_revision()


@app.on_event("shutdown")
async def shutdown():
    """Gracefully stop running tasks and release async resources."""
    logger.info("[Agent] Shutdown initiated — stopping running tasks")

    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        pass

    if Modules.computer_use:
        Modules.computer_use.cancel_running()
    if Modules.browser_use:
        try:
            Modules.browser_use.cancel_running()
        except Exception:
            pass

    for t in list(Modules._persistent_tasks):
        if not t.done():
            t.cancel()
    if Modules.active_computer_use_async_task and not Modules.active_computer_use_async_task.done():
        Modules.active_computer_use_async_task.cancel()

    try:
        await _ensure_plugin_lifecycle_stopped()
    except Exception as e:
        logger.warning(f"[Agent] Plugin lifecycle cleanup error: {e}")

    try:
        await _stop_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Embedded user plugin server cleanup error: {e}")

    logger.info("[Agent] 正在清理 AsyncClient 资源...")

    async def _close_router(name: str, module, attr: str):
        if module and hasattr(module, attr):
            try:
                router = getattr(module, attr)
                await asyncio.wait_for(router.aclose(), timeout=3.0)
                logger.debug(f"[Agent] ✅ {name}.{attr} 已清理")
            except asyncio.TimeoutError:
                logger.warning(f"[Agent] ⚠️ {name}.{attr} 清理超时，强制跳过")
            except asyncio.CancelledError:
                logger.debug(f"[Agent] {name}.{attr} 清理时被取消（正常关闭）")
            except RuntimeError as e:
                logger.debug(f"[Agent] {name}.{attr} 清理时遇到 RuntimeError（可能是正常关闭）: {e}")
            except Exception as e:
                logger.warning(f"[Agent] ⚠️ 清理 {name}.{attr} 时出现意外错误: {e}")

    try:
        _shutdown_coros = []
        for _name, _attr_name in [("DirectTaskExecutor", "task_executor")]:
            _mod = getattr(Modules, _attr_name, None)
            if _mod is not None:
                _shutdown_coros.append(_close_router(_name, _mod, "router"))
        if _shutdown_coros:
            await asyncio.wait_for(
                asyncio.gather(*_shutdown_coros, return_exceptions=True),
                timeout=5.0,
            )
    except asyncio.TimeoutError:
        logger.warning("[Agent] ⚠️ 整体清理过程超时，强制完成关闭")

    bridge = Modules.agent_bridge
    if bridge is not None:
        try:
            await bridge.stop()
            Modules.agent_bridge = None
            logger.debug("[Agent] ✅ ZMQ event bridge cleaned up")
        except Exception as e:
            logger.warning("[Agent] ⚠️ ZMQ event bridge cleanup error: %s", e)

    all_tasks = list(Modules._persistent_tasks) + list(Modules._background_tasks)
    tasks_to_await = [t for t in all_tasks if not t.done()]
    for t in tasks_to_await:
        t.cancel()
    if tasks_to_await:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_await, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[Agent] ⚠️ 部分后台任务取消超时")
    Modules._persistent_tasks.clear()
    Modules._background_tasks.clear()

    cu = Modules.computer_use
    if cu is not None and hasattr(cu, "wait_for_completion"):
        loop = asyncio.get_running_loop()
        finished = await loop.run_in_executor(None, cu.wait_for_completion, 8.0)
        if not finished:
            logger.warning("[Agent] CUA thread did not stop within 8s at shutdown")

    logger.info("[Agent] ✅ AsyncClient 资源清理完成")
    logger.info("[Agent] Shutdown cleanup complete")
    await _emit_agent_status_update()


@app.get("/health")
async def health():
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response(
        "agent",
        instance_id=INSTANCE_ID,
        extra={"agent_flags": Modules.agent_flags},
    )


# 插件直接触发路由（放在顶层，确保不在其它函数体内）
@app.post("/plugin/execute")
async def plugin_execute_direct(payload: Dict[str, Any]):
    """
    New endpoint: trigger a plugin_entry directly.
    The request body may contain:
      - plugin_id: str (required)
      - entry_id: str (optional)
      - args: dict (optional)
      - lanlan_name: str (optional, for logging/notifications)
    This endpoint calls Modules.task_executor.execute_user_plugin_direct to run the plugin trigger.
    """
    if not Modules.task_executor:
        raise HTTPException(503, "Task executor not ready")
    # Master gate first: with the new semantics where set_agent_enabled(False)
    # no longer wipes sub-flag state, ``user_plugin_enabled`` can legitimately
    # stay True after the master is turned off. Without this check, requests
    # would slip through to a plugin lifecycle that ``_ensure_plugin_lifecycle
    # _stopped`` has already torn down, producing confusing failures.
    if not Modules.analyzer_enabled:
        raise HTTPException(403, "Agent master switch is off")
    # 当后端显式关闭用户插件功能时，直接拒绝调用，避免绕过前端开关
    if not Modules.agent_flags.get("user_plugin_enabled", False):
        raise HTTPException(403, "User plugin is disabled")
    plugin_id = (payload or {}).get("plugin_id")
    entry_id = (payload or {}).get("entry_id")
    raw_args = (payload or {}).get("args", {}) or {}
    if not isinstance(raw_args, dict):
        raise HTTPException(400, "args must be a JSON object")
    args = raw_args
    lanlan_name = (payload or {}).get("lanlan_name")
    conversation_id = (payload or {}).get("conversation_id")
    if not plugin_id or not isinstance(plugin_id, str):
        raise HTTPException(400, "plugin_id required")

    # Dedup is not applied for direct plugin calls; client should dedupe if needed
    task_id = str(uuid.uuid4())
    # Log request
    logger.info(f"[Plugin] Direct execute request: plugin_id={plugin_id}, entry_id={entry_id}, lanlan={lanlan_name}")

    # 获取插件友好名称（用于 HUD 显示）
    plugin_name = await _get_plugin_friendly_name(plugin_id)
    task_params = {"plugin_id": plugin_id, "entry_id": entry_id, "args": args}
    if plugin_name:
        task_params["plugin_name"] = plugin_name

    # Ensure task registry entry for tracking
    info = {
        "id": task_id,
        "type": "plugin_direct",
        "status": "running",
        "start_time": _now_iso(),
        "params": task_params,
        "lanlan_name": lanlan_name,
        "result": None,
        "error": None,
    }
    Modules.task_registry[task_id] = info

    # Execute via task_executor.execute_user_plugin_direct in background
    async def _run_plugin():
        try:
            await _emit_main_event(
                "task_update", lanlan_name,
                task={
                    "id": task_id,
                    "status": "running",
                    "type": "plugin_direct",
                    "start_time": info["start_time"],
                    "params": task_params,
                },
            )
        except Exception as emit_err:
            logger.debug("[Plugin] emit task_update(running) failed: task_id=%s error=%s", task_id, emit_err)

        async def _on_plugin_progress(
            *, progress=None, stage=None, message=None, step=None, step_total=None,
        ):
            # If cancel_task already flipped the registry to a terminal state,
            # swallow the progress callback — otherwise it would clobber
            # "cancelled" with a fresh "running" update on the HUD.
            _reg = Modules.task_registry.get(task_id)
            if _reg and _reg.get("status") != "running":
                return
            task_payload: Dict[str, Any] = {
                "id": task_id,
                "status": "running",
                "type": "plugin_direct",
                "start_time": info["start_time"],
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

        # Default delivery mode; overridden after the plugin result is parsed
        # below. Cancel / exception branches read this so they honor whatever
        # the plugin already declared, not a hard-coded "proactive".
        _delivery_mode = "proactive"
        try:
            res = await Modules.task_executor.execute_user_plugin_direct(
                task_id=task_id,
                plugin_id=plugin_id,
                plugin_args=args,
                entry_id=entry_id,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                on_progress=_on_plugin_progress,
            )
            if info.get("status") == "cancelled":
                # cancel_task pre-marked cancelled; skip terminal clobber + emits.
                return
            info["result"] = res.result
            info["end_time"] = _now_iso()
            # 兜底终态先行：下面 inner try 里 detail/delivery_mode 的解析若抛
            # 异常会被 except 吞掉（只 debug 日志），没有这行 info["status"]
            # 会永远停在 "running"，finally 的 task_update 只能把 running 广播
            # 出去，HUD 卡片永久转圈。_plugin_terminal_status 算出精确终态后
            # 会再覆盖。
            info["status"] = "completed" if res.success else "failed"
            try:
                run_data = res.result.get("run_data") if isinstance(res.result, dict) else None
                run_error = res.result.get("run_error") if isinstance(res.result, dict) else None
                _llm_fields = _lookup_llm_result_fields(plugin_id, entry_id)
                _plugin_msg = str(res.result.get("message") or "") if isinstance(res.result, dict) else ""
                _error_to_pass = (run_error or res.error) if not res.success else None
                detail = parse_plugin_result(
                    run_data,
                    llm_result_fields=_llm_fields,
                    plugin_message=_plugin_msg,
                    error=_error_to_pass,
                )
                _delivery_mode = _resolve_delivery_mode(res.result if isinstance(res.result, dict) else None)
                _suppress_reply = _delivery_mode == "silent"
                _terminal_status = _plugin_terminal_status(res.success, run_data)
                info["status"] = _terminal_status
                _completed = _terminal_status == "completed"
                if not _suppress_reply:
                    if not _completed:
                        info["error"] = _tt((detail or str(res.error or "")), TASK_ERROR_MAX_TOKENS)
                    display_id = await _get_plugin_display_id(plugin_id)
                    # summary = plain detail; status/source rendering handled in main_logic.
                    # 失败情况下显式传 status="failed"，避免 _emit_task_result 把
                    # success=False+非空 detail 默认推到 "partial"（"部分完成"）。
                    if _completed:
                        _summary_text = detail
                        _detail_text = detail
                        _err_text = ""
                        _explicit_status = None
                    elif res.success:
                        _summary_text = detail
                        _detail_text = detail
                        _err_text = ""
                        _explicit_status = _terminal_status
                    else:
                        _err_text = (detail or str(res.error or "")).strip()
                        _summary_text = _err_text
                        _detail_text = _err_text
                        _explicit_status = "failed"
                    await _emit_task_result(
                        lanlan_name,
                        channel="user_plugin",
                        task_id=task_id,
                        success=_completed,
                        summary=_summary_text,
                        detail=_detail_text,
                        error_message=_err_text,
                        direct_reply=False,
                        status=_explicit_status,
                        source_kind="plugin",
                        source_name=display_id,
                        delivery_mode=_delivery_mode,
                    )
                elif not _completed:
                    info["error"] = _tt((detail or str(res.error or "")), TASK_ERROR_MAX_TOKENS)
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_result failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
        except asyncio.CancelledError:
            info["status"] = "cancelled"
            if not info.get("error"):
                info["error"] = "Cancelled by shutdown"
            # Honor plugin's resolved delivery mode if it had a chance to
            # run before cancel; default to "proactive" otherwise. silent
            # plugins stay silent.
            if _delivery_mode != "silent":
                try:
                    display_id = await _get_plugin_display_id(plugin_id)
                    await _emit_task_result(
                        lanlan_name,
                        channel="user_plugin",
                        task_id=task_id,
                        success=False,
                        summary="cancelled",
                        detail="cancelled",
                        error_message="cancelled",
                        status="cancelled",
                        source_kind="plugin",
                        source_name=display_id,
                        delivery_mode=_delivery_mode,
                    )
                except Exception as emit_err:
                    logger.debug("[Plugin] emit task_result(cancelled) failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
            raise
        except Exception as e:
            if info.get("status") == "cancelled":
                return
            info["status"] = "failed"
            info["end_time"] = _now_iso()
            info["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
            # exception 字符串可能含 provider/plugin 原文 / 用户输入；logger
            # 只记元数据，原文 + traceback 走 print 兜底。
            import traceback as _tb
            logger.error(
                "[Plugin] Direct execute failed: task_id=%s plugin_id=%s exc_type=%s",
                task_id, plugin_id, type(e).__name__,
            )
            print(f"[Plugin] Direct execute raw error (task_id={task_id}, plugin_id={plugin_id}):\n{_tb.format_exc()}")
            # Honor plugin's resolved delivery mode (if any); silent plugins
            # stay silent even on dispatch exception.
            if _delivery_mode != "silent":
                try:
                    display_id = await _get_plugin_display_id(plugin_id)
                    _exc_text = str(e)[:EXCEPTION_TEXT_MAX_CHARS]
                    await _emit_task_result(
                        lanlan_name,
                        channel="user_plugin",
                        task_id=task_id,
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
                    logger.debug("[Plugin] emit task_result(exception) failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
        finally:
            try:
                await _emit_main_event(
                    "task_update", lanlan_name,
                    task={
                        "id": task_id,
                        "status": info.get("status"),
                        "type": "plugin_direct",
                        "start_time": info.get("start_time"),
                        "end_time": _now_iso(),
                        "params": info.get("params", {}),
                        "error": info.get("error"),
                    },
                )
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_update(terminal) failed: task_id=%s error=%s", task_id, emit_err)

    plugin_task = asyncio.create_task(_run_plugin())
    Modules.task_async_handles[task_id] = plugin_task
    Modules._background_tasks.add(plugin_task)
    def _cleanup_plugin_task(_t, _tid=task_id):
        Modules._background_tasks.discard(_t)
        Modules.task_async_handles.pop(_tid, None)
    plugin_task.add_done_callback(_cleanup_plugin_task)
    return {"success": True, "task_id": task_id, "status": info["status"], "start_time": info["start_time"]}



@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    info = Modules.task_registry.get(task_id)
    if info:
        return _public_task_info(info)
    raise HTTPException(404, "task not found")


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a specific running task.

    Cancellation is a two-phase operation:
      1. Mark the task "cancelled" in the registry and cancel the wrapping
         asyncio task synchronously. This is what the dispatch coroutines
         observe first, so they take the cancelled code path.
      2. Fire-and-forget the provider-specific teardown (browser process tree
         kill, remote /stop HTTP, etc.) so this endpoint returns to the
         frontend immediately instead of blocking on a slow remote.
    """
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(404, "task not found")
    if info.get("status") not in ("queued", "running"):
        # Include the real terminal status so the HUD's local fallback can
        # mirror it instead of mislabeling the card "cancelled".
        return {"success": False, "error": "task is not active", "status": info.get("status")}

    task_type = info.get("type")
    # Mark cancelled up front so any late terminal writes from the dispatch
    # coroutine can see it and skip clobbering the status (see _run_*_dispatch
    # terminal guards).
    info["status"] = "cancelled"
    info["error"] = "Cancelled by user"
    lanlan_name = info.get("lanlan_name")
    _task_tracker.record_completed(
        lanlan_name,
        task_id=task_id,
        method=str(task_type or ""),
        desc=_tracker_desc_for_task_info(info),
        detail="Cancelled by user",
        success=False,
        cancelled=True,
        trigger_user_fingerprint=info.get("_trigger_user_fingerprint"),
    )

    bg = Modules.task_async_handles.get(task_id)
    if bg and not bg.done():
        bg.cancel()

    if task_type == "computer_use":
        if Modules.computer_use:
            Modules.computer_use.cancel_running()
        if Modules.active_computer_use_task_id == task_id and Modules.active_computer_use_async_task:
            Modules.active_computer_use_async_task.cancel()
    elif task_type == "browser_use":
        # Tear down the shared browser only for the task that owns the slot.
        # A queued task's dispatch coroutine dies at the lock via bg.cancel()
        # above; ripping the browser for it would kill the unrelated running
        # task that is actually using it.
        if Modules.active_browser_use_task_id == task_id:
            if Modules.browser_use:
                _spawn_background_cancel(
                    Modules.browser_use.cancel(), label=f"browser_use:{task_id}"
                )
            Modules.active_browser_use_task_id = None
    elif task_type == "openfang":
        if Modules.openfang:
            # unregister_local_task must run AFTER cancel_running, not before:
            # OpenFangAdapter.cancel_running looks up the remote task_id in
            # _active_tasks and no-ops if missing. Unregistering first would
            # turn the remote /cancel call into a silent no-op and leave the
            # VM task running even though we report success locally.
            async def _openfang_cancel_then_unregister(
                adapter=Modules.openfang, tid=task_id
            ):
                try:
                    await adapter.cancel_running(tid)
                finally:
                    adapter.unregister_local_task(tid)
            _spawn_background_cancel(
                _openfang_cancel_then_unregister(),
                label=f"openfang:{task_id}",
            )
    elif task_type == "openclaw":
        if Modules.openclaw:
            _spawn_background_cancel(
                Modules.openclaw.stop_running(
                    sender_id=info.get("sender_id"),
                    session_id=info.get("session_id"),
                    conversation_id=info.get("conversation_id") or info.get("session_id"),
                    role_name=info.get("lanlan_name"),
                    task_id=task_id,
                ),
                label=f"openclaw:{task_id}",
            )

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={"id": task_id, "status": "cancelled", "type": task_type,
                  "end_time": _now_iso(), "params": info.get("params", {}),
                  "error": "Cancelled by user"},
        )
    except Exception:
        pass
    logger.info("[Agent] Task %s (%s) cancelled by user", task_id, task_type)
    return {"success": True, "task_id": task_id, "status": "cancelled"}


@app.post("/api/agent/tasks/{task_id}/correction")
async def submit_task_correction(task_id: str, body: ToolCorrectionPayload):
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="Task not found")

    task_type = str(info.get("type") or "").strip()
    if task_type not in {"computer_use", "browser_use"}:
        raise HTTPException(
            status_code=400,
            detail="Only computer_use/browser_use tasks support tool correction",
        )
    if Modules.task_executor is None:
        raise HTTPException(status_code=503, detail="Task executor not ready")

    correct_tool = str(body.correct_tool or "").strip()
    if correct_tool not in {"computer_use", "browser_use"}:
        raise HTTPException(
            status_code=400,
            detail="correct_tool must be computer_use or browser_use",
        )
    if correct_tool == task_type:
        raise HTTPException(
            status_code=400,
            detail="correct_tool must be different from the current task type",
        )

    instr = str(body.correct_instruction or "").strip()
    if not instr:
        raise HTTPException(
            status_code=400,
            detail="correct_instruction cannot be blank",
        )

    correction_info = _get_internal_correction_context(info)
    if correction_info is None:
        raise HTTPException(
            status_code=400,
            detail="Task correction context is unavailable for this task",
        )
    task_status = str(info.get("status") or info.get("state") or "").strip().lower()
    if task_status not in {"completed", "failed", "cancelled"}:
        raise HTTPException(
            status_code=400,
            detail="Task correction is only allowed after the task reaches a terminal state",
        )

    try:
        event = Modules.task_executor.record_tool_correction(
            {
                **correction_info,
                "task_id": task_id,
                "type": task_type,
            },
            correct_tool=correct_tool,
            correct_instruction=instr,
            user_note=body.user_note,
        )
    except Exception as exc:
        logger.exception("[CorrectionMemory] Failed to record correction for %s: %s", task_id, exc)
        raise HTTPException(status_code=500, detail="Failed to record correction") from exc

    logger.info(
        "[CorrectionMemory] Recorded correction: task_id=%s chosen=%s correct=%s",
        task_id,
        task_type,
        correct_tool,
    )
    return {"success": True, "task_id": task_id}


@app.post("/api/agent/tasks/{task_id}/complete")
async def complete_deferred_task(task_id: str):
    """Callback for the plugin daemon: mark a deferred task as completed and notify the frontend HUD."""
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="Task not found")
    if info.get("status") != "running":
        # 已经是 terminal 状态，幂等返回
        return {"ok": True, "skipped": True, "status": info.get("status")}

    # 验证这是一个 deferred 任务（只有 user_plugin 且有 deferred_timeout 的任务才能通过此端点完成）
    if info.get("type") != "user_plugin":
        raise HTTPException(status_code=403, detail="Only user_plugin tasks can be completed via this endpoint")
    if not info.get("deferred_timeout"):
        raise HTTPException(status_code=400, detail="Not a deferred task - use normal completion flow")

    info["status"] = "completed"
    info["end_time"] = _now_iso()
    lanlan_name = info.get("lanlan_name")
    params = info.get("params", {})
    plugin_id = params.get("plugin_id", "")
    entry_id = params.get("entry_id", "")
    desc = params.get("description", "")

    # 关闭 tracker 记录（deferred 任务之前只有 assigned 没有 completed）
    _task_tracker.record_completed(
        lanlan_name, task_id=task_id, method="user_plugin",
        desc=f"{plugin_id}.{entry_id}: {desc}" if plugin_id else desc,
        detail="deferred callback completed", success=True,
    )

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={
                "id": task_id,
                "status": "completed",
                "type": info.get("type"),
                "start_time": info.get("start_time"),
                "end_time": info["end_time"],
                "params": params,
            },
        )
    except Exception as e:
        logger.warning("[Deferred] emit task_update(complete) failed: task_id=%s error=%s", task_id, e)

    logger.info("[Deferred] Task %s marked completed via callback", task_id)
    return {"ok": True}


# ── OpenFang LLM Proxy ──────────────────────────────────────
# OpenFang 的 Rust LLM driver 严格要求 OpenAI 格式的 completion_tokens 等字段。
# lanlan.app 的 API 可能不返回这些字段，导致 OpenFang parse error。
# 此代理拦截 LLM 请求，转发到真实 API，并在响应中补全缺失字段。

from fastapi import Request
from starlette.responses import StreamingResponse as StarletteStreamingResponse

@app.api_route("/openfang-llm-proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openfang_llm_proxy(request: Request, path: str):
    """
    Transparent proxy: OpenFang → this endpoint → lanlan.app (or the user-configured agent API).
    Fills in OpenAI compatibility fields in the response (completion_tokens, prompt_tokens, etc.).
    """
    # 获取真实 API 地址
    cm = get_config_manager()
    agent_cfg = cm.get_model_api_config('agent')
    real_base_url = (agent_cfg.get("base_url") or "").strip().rstrip("/")
    real_api_key = (agent_cfg.get("api_key") or "").strip()

    if not real_base_url:
        return JSONResponse({"error": "Agent API base_url not configured"}, status_code=502)

    # 智能拼接 URL：避免 /v1/v1 双重路径
    # OpenFang 调用：proxy_base/v1/chat/completions → path="v1/chat/completions"
    # 如果 real_base_url 已含 /v1，则去掉 path 中的 /v1 前缀
    if real_base_url.rstrip("/").endswith("/v1") and path.startswith("v1/"):
        path = path[3:]  # 去掉 "v1/"
    target_url = f"{real_base_url}/{path}"
    # 保留原始请求的 query string
    qs = request.url.query
    if qs:
        target_url = f"{target_url}?{qs}"

    print(f"[LLM Proxy] path={path}, real_base_url={real_base_url}, target_url={target_url}")

    # 读取请求体
    body = await request.body()

    # 构建转发请求头（保留 Content-Type，替换 Authorization）
    forward_headers = {}
    ct = request.headers.get("content-type")
    if ct:
        forward_headers["Content-Type"] = ct
    if real_api_key:
        forward_headers["Authorization"] = f"Bearer {real_api_key}"

    # 检查是否请求流式
    is_stream = False
    if body:
        try:
            req_json = json.loads(body)
            is_stream = req_json.get("stream", False)
        except Exception:
            logger.debug("[LLM Proxy] failed to parse request body for stream detection", exc_info=True)

    try:
        if is_stream:
            # 流式：手动管理 client 生命周期（generator 延迟消费，不能用 async with）
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
            try:
                upstream_resp = await client.send(
                    client.build_request(request.method, target_url, content=body, headers=forward_headers),
                    stream=True,
                )
            except Exception:
                await client.aclose()
                raise
            upstream_status = upstream_resp.status_code

            async def _stream_with_patch():
                try:
                    async for line in upstream_resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                chunk = json.loads(line[6:])
                                _patch_openai_response(chunk)
                                yield f"data: {json.dumps(chunk)}\n\n"
                                continue
                            except Exception:
                                logger.debug("[LLM Proxy] failed to parse streaming chunk", exc_info=True)
                        yield line + "\n"
                finally:
                    await upstream_resp.aclose()
                    await client.aclose()

            return StarletteStreamingResponse(
                _stream_with_patch(),
                status_code=upstream_status,
                media_type="text/event-stream",
            )
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                # 非流式：一次性读取并 patch
                resp = await client.request(
                    request.method, target_url,
                    content=body, headers=forward_headers,
                )
                logger.info("[LLM Proxy] upstream response: status=%s, len=%d", resp.status_code, len(resp.content))
                # body 可能含 LLM 生成原文；不写 logger，仅本地 print
                print(f"[LLM Proxy] upstream body (first 500): {resp.text[:500]}")
                # 尝试 JSON patch
                try:
                    data = resp.json()
                    _patch_openai_response(data)
                    return JSONResponse(data, status_code=resp.status_code)
                except Exception:
                    # 非 JSON 响应原样返回 (使用 raw Response 避免二次编码)
                    from starlette.responses import Response as RawResponse
                    return RawResponse(
                        content=resp.content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/octet-stream"),
                    )
    except httpx.TimeoutException:
        return JSONResponse({"error": "Upstream API timeout"}, status_code=504)
    except Exception as e:
        logger.warning("[LLM Proxy] upstream error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=502)


# ── OpenFang endpoints ──────────────────────────────────────

@app.get("/openfang/availability")
async def openfang_availability():
    """Check OpenFang availability."""
    if not Modules.openfang:
        return {"enabled": False, "ready": False, "reason": "adapter 未加载"}
    return await asyncio.to_thread(Modules.openfang.is_available)


@app.get("/openclaw/availability")
async def openclaw_availability():
    if not Modules.openclaw:
        return {"enabled": False, "ready": False, "reasons": ["adapter 未加载"]}
    status = await asyncio.to_thread(Modules.openclaw.is_available)
    ready = bool(status.get("ready")) if isinstance(status, dict) else False
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    pending = _openclaw_pending()
    if ready:
        was_ready = bool(((Modules.capability_cache or {}).get("openclaw") or {}).get("ready"))
        if pending:
            _cancel_openclaw_enable_probe()
        _set_capability("openclaw", True, "")
        if pending or not was_ready:
            await _emit_agent_status_update()
        return status
    if pending and Modules.agent_flags.get("openclaw_enabled"):
        _set_capability("openclaw", False, "AGENT_PRECHECK_PENDING")
        if isinstance(status, dict):
            status = dict(status)
            status["pending"] = True
        return status
    reason = reasons[0] if reasons else ""
    was_openclaw_enabled = bool(Modules.agent_flags.get("openclaw_enabled"))
    was_ready = bool(((Modules.capability_cache or {}).get("openclaw") or {}).get("ready"))
    _set_capability("openclaw", False, reason)
    if was_openclaw_enabled:
        Modules.agent_flags["openclaw_enabled"] = False
        Modules.notification = _openclaw_notification("AGENT_OPENCLAW_CAPABILITY_LOST", reasons)
    if was_openclaw_enabled or was_ready:
        await _emit_agent_status_update()
    return status


@app.post("/openfang/run")
async def openfang_run(payload: Dict[str, Any]):
    """Execute a task directly via OpenFang (bypassing routing decisions)."""
    instruction = payload.get("instruction")
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    if not Modules.openfang or not Modules.openfang.init_ok:
        return JSONResponse({"error": "VM agent not available"}, status_code=503)

    task_id = f"of_{uuid.uuid4().hex[:12]}"

    _lanlan = payload.get("lanlan_name")

    async def _run():
        try:
            Modules.task_registry[task_id] = {
                "id": task_id, "type": "openfang", "status": "running",
                "params": {"instruction": instruction},
                "lanlan_name": _lanlan,
                "session_id": payload.get("conversation_id"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            # Emit initial running event with full task object
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=Modules.task_registry[task_id],
                )
            except Exception:
                logger.debug("[OpenFang] initial task_update emit failed", exc_info=True)

            def _on_progress(info):
                try:
                    reg = Modules.task_registry.get(task_id, {})
                    # cancel_task pre-marks status="cancelled" and we must not
                    # let a late progress tick overwrite it with "running".
                    if reg.get("status") and reg.get("status") != "running":
                        return
                    reg["status"] = info.get("status", reg.get("status", "running"))
                    reg["elapsed"] = info.get("elapsed", 0)
                    asyncio.create_task(_emit_main_event(
                        "task_update", _lanlan,
                        task_id=task_id, channel="openfang",
                        task=reg,
                    ))
                except Exception as e:
                    logger.debug("[OpenFang] _on_progress emit failed: %s", e)

            result = await Modules.openfang.run_instruction(
                instruction=instruction,
                session_id=payload.get("conversation_id"),
                on_progress=_on_progress,
                local_task_id=task_id,
            )
            reg = Modules.task_registry[task_id]
            if reg.get("status") == "cancelled":
                return
            final_status = "completed" if result.get("success") else "failed"
            reg["status"] = final_status
            reg["result"] = result
            reg["end_time"] = datetime.now(timezone.utc).isoformat()
            _r = result if isinstance(result, dict) else {}
            _success = _r.get("success", False)
            _result_text = _r.get("result", "") or ""
            _error_text = _r.get("error", "") or ""
            # 跟 _run_openfang_dispatch 同款的 fallback chain：daemon 失败时
            # 可能把原因塞进 result 而非 error；成功时 result 偶尔为空（如
            # 仅有 artifacts）。两条出口都做兜底，避免前端拿到空 summary
            # 或丢失败原因。
            # 极端兜底：result 和 error 都为空时（e.g. 仅 artifacts 的成功
            # 返回）summary 走默认占位串，避免前端 / LLM callback 拿到空
            # summary。
            _summary_src = _result_text or _error_text or (
                "(OpenFang task completed with no result text)"
                if _success
                else "(OpenFang task failed with no error text)"
            )
            _err_src = _error_text or _result_text
            if not _success:
                reg["error"] = _tt(_err_src or "(OpenFang task failed with no error text)", TASK_ERROR_MAX_TOKENS)

            # callback summary 进 LLM context — 与 _sanitize_correction_text per-item 同档（400 tokens）
            await _emit_task_result(
                _lanlan,
                channel="openfang",
                task_id=task_id,
                success=_success,
                summary=_tt(_summary_src, 400),
                detail=_result_text,
                error_message=(_err_src or "(OpenFang task failed with no error text)") if not _success else "",
            )
            # Terminal task_update so HUD transitions out of running
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=reg,
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_update emit failed", exc_info=True)
        except Exception as e:
            reg = Modules.task_registry[task_id]
            if reg.get("status") == "cancelled":
                return
            # exception 字符串可能含用户/LLM 原文，logger 只记元数据
            logger.error("[OpenFang] Task %s failed (exc_type=%s)", task_id, type(e).__name__)
            print(f"[OpenFang] Task {task_id} raw error: {e}")
            reg["status"] = "failed"
            reg["error"] = _tt(str(e), TASK_ERROR_MAX_TOKENS)
            reg["end_time"] = datetime.now(timezone.utc).isoformat()
            try:
                # except 路径也走非空 summary，避免前端 / LLM callback 拿到
                # 空摘要；error_message 用 exception 原文（已被外层 reg["error"]
                # truncate，这里独立 cap）。
                _exc_msg = str(e) or "(OpenFang task raised with no message)"
                await _emit_task_result(
                    _lanlan,
                    channel="openfang",
                    task_id=task_id,
                    success=False,
                    summary=_tt(_exc_msg, 400),
                    error_message=_tt(_exc_msg, TASK_ERROR_MAX_TOKENS),
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_result emit failed", exc_info=True)
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=reg,
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_update emit failed", exc_info=True)

    bg = asyncio.create_task(_run())
    Modules.task_async_handles[task_id] = bg
    Modules._background_tasks.add(bg)
    def _cleanup_of_bg(_t, _tid=task_id):
        Modules._background_tasks.discard(_t)
        Modules.task_async_handles.pop(_tid, None)
    bg.add_done_callback(_cleanup_of_bg)

    return {"success": True, "task_id": task_id, "status": "running"}


@app.post("/openfang/sync_config")
async def openfang_sync_config():
    """Manually trigger API key config sync to OpenFang."""
    if not Modules.openfang:
        return {"success": False, "error": "adapter 未加载"}
    ok = await Modules.openfang.sync_config()
    return {"success": ok}


@app.get("/capabilities")
async def capabilities():
    return {"success": True, "capabilities": {}}


@app.get("/agent/flags")
async def get_agent_flags():
    """Get the current agent flags state (for frontend sync)"""
    note = Modules.notification
    # Read-once notification
    if Modules.notification:
        Modules.notification = None
        
    return {
        "success": True, 
        "agent_flags": _agent_flags_snapshot(),
        "analyzer_enabled": Modules.analyzer_enabled,
        "agent_api_gate": _check_agent_api_gate(),
        "revision": Modules.state_revision,
        "notification": note
    }


@app.get("/agent/state")
async def get_agent_state():
    if not Modules.task_executor:
        raise HTTPException(503, "Task executor not ready")
    snapshot = _collect_agent_status_snapshot()
    return {"success": True, "snapshot": snapshot}


@app.post("/agent/flags")
async def set_agent_flags(payload: Dict[str, Any]):
    lanlan_name = (payload or {}).get("lanlan_name")
    cf = (payload or {}).get("computer_use_enabled")
    bf = (payload or {}).get("browser_use_enabled")
    uf = (payload or {}).get("user_plugin_enabled")
    nf = (payload or {}).get("openclaw_enabled")
    # ``_persist_intent`` (default True) gates whether this call writes the
    # user's intent to ``agent_runtime_intent.json``. The restore path replays
    # past intents through this same function with ``_persist_intent=False``
    # so the replay doesn't re-write what it's reading.
    persist_intent = bool((payload or {}).get("_persist_intent", True))
    # Agent API gate: if any agent sub-feature is being enabled, gate must pass.
    gate = _check_agent_api_gate()
    changed = False
    old_flags = dict(Modules.agent_flags)
    old_analyzer_enabled = bool(Modules.analyzer_enabled)
    of = (payload or {}).get("openfang_enabled")
    # Agent LLM gate fail (endpoint/key not configured) blocks **only** the
    # four LLM-dependent sub flags. ``user_plugin_enabled`` runs entirely on
    # the plugin lifecycle (no agent LLM involved) so the gate must not
    # short-circuit its toggle path — historically this branch reset all five
    # and early-returned, which silently swallowed legitimate user_plugin
    # enable/disable requests whenever the user hadn't configured an agent
    # endpoint. Here we instead cancel just the four LLM-coupled requests by
    # nullifying them, then fall through to the per-flag handling so uf still
    # processes normally.
    if gate.get("ready") is not True and any(x is True for x in (cf, bf, nf, of)):
        _cancel_openclaw_enable_probe()
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.agent_flags["browser_use_enabled"] = False
        Modules.agent_flags["openclaw_enabled"] = False
        Modules.agent_flags["openfang_enabled"] = False
        first_reason = (gate.get('reasons') or ['AGENT_ENDPOINT_NOT_CONFIGURED'])[0]
        _set_capability("computer_use", False, first_reason)
        _set_capability("browser_use", False, first_reason)
        _set_capability("openclaw", False, first_reason)
        _set_capability("openfang", False, first_reason)
        # Swallow these requests so the per-flag handlers below don't re-toggle
        # them ON; ``uf`` is intentionally left alone so user_plugin processing
        # proceeds.
        cf = bf = nf = of = None

    prev_up = Modules.agent_flags.get("user_plugin_enabled", False)
    prev_nk = Modules.agent_flags.get("openclaw_enabled", False)

    # 1. Handle Computer Use Flag with Capability Check
    if isinstance(cf, bool):
        if cf: # Attempting to enable
            if not Modules.computer_use:
                _try_refresh_computer_use_adapter(force=True)
            if not Modules.computer_use:
                Modules.agent_flags["computer_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_CU_MODULE_NOT_LOADED"})
                logger.warning("[Agent] Cannot enable Computer Use: Module not loaded")
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["computer_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_CU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                try:
                    avail = await asyncio.to_thread(Modules.computer_use.is_available)
                    reasons = avail.get('reasons', []) if isinstance(avail, dict) else []
                    _set_capability("computer_use", bool(avail.get("ready")) if isinstance(avail, dict) else False, reasons[0] if reasons else "")
                    if avail.get("ready"):
                        Modules.agent_flags["computer_use_enabled"] = True
                    else:
                        Modules.agent_flags["computer_use_enabled"] = False
                        reason = avail.get('reasons', [])[0] if avail.get('reasons') else 'unknown'
                        Modules.notification = json.dumps({"code": "AGENT_CU_UNAVAILABLE", "details": {"reason_code": reason}})
                        logger.warning(f"[Agent] Cannot enable Computer Use: {avail.get('reasons')}")
                except Exception as e:
                    Modules.agent_flags["computer_use_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_CU_ENABLE_FAILED", "details": {"error": str(e)}})
                    logger.error(f"[Agent] Cannot enable Computer Use: Check failed {e}")
        else: # Disabling
            Modules.agent_flags["computer_use_enabled"] = False

    # 2.5. Handle Browser Use Flag with Capability Check
    if isinstance(bf, bool):
        if bf:
            bu = getattr(Modules, "browser_use", None)
            if not bu:
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_BU_MODULE_NOT_LOADED"})
            elif not getattr(bu, "_ready_import", False):
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_BU_NOT_INSTALLED", "details": {"error": str(bu.last_error)}})
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["browser_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_BU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                Modules.agent_flags["browser_use_enabled"] = True
                _set_capability("browser_use", True, "")
        else:
            Modules.agent_flags["browser_use_enabled"] = False
            
    if isinstance(uf, bool):
        if uf:  # Attempting to enable UserPlugin — non-blocking (like CUA)
            Modules.agent_flags["user_plugin_enabled"] = True
            Modules.notification = json.dumps({"code": "AGENT_UP_ENABLED_CHECKING"})

            async def _bg_plugin_enable():
                _ln = lanlan_name
                try:
                    started = await _ensure_plugin_lifecycle_started()
                    if not started:
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                        logger.warning("[Agent] Cannot enable UserPlugin: lifecycle startup failed")
                        _bump_state_revision()
                        await _emit_agent_status_update(lanlan_name=_ln)
                        return

                    plugins = []
                    for _attempt in range(8):
                        await asyncio.sleep(0.5)
                        try:
                            async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
                                r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
                                if r.status_code == 200:
                                    data = r.json()
                                    plugins = data.get("plugins", []) if isinstance(data, dict) else []
                                    if plugins:
                                        break
                        except Exception:
                            pass

                    if not plugins:
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_NO_PLUGINS_FOUND"})
                        logger.warning("[Agent] Cannot enable UserPlugin: no plugins found after lifecycle start")
                        await _ensure_plugin_lifecycle_stopped()
                    else:
                        _set_capability("user_plugin", True, "")
                        logger.info("[Agent] UserPlugin lifecycle ready (%d plugins)", len(plugins))
                except Exception as exc:
                    Modules.agent_flags["user_plugin_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                    logger.error("[Agent] Background plugin enable failed: %s", exc)
                finally:
                    _bump_state_revision()
                    await _emit_agent_status_update(lanlan_name=_ln)

            _bg = asyncio.create_task(_bg_plugin_enable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)
        else:  # Disabling UserPlugin — non-blocking
            Modules.agent_flags["user_plugin_enabled"] = False
            _set_capability("user_plugin", True, "")

            async def _bg_plugin_disable():
                try:
                    await _ensure_plugin_lifecycle_stopped()
                except Exception as exc:
                    logger.warning("[Agent] Background plugin disable error: %s", exc)

            _bg = asyncio.create_task(_bg_plugin_disable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)

    if isinstance(nf, bool):
        if nf:
            if Modules.analyzer_enabled:
                _start_openclaw_enable_probe(lanlan_name)
            else:
                Modules.agent_flags["openclaw_enabled"] = True
                _set_capability("openclaw", False, "")
        else:
            _cancel_openclaw_enable_probe()
            Modules.agent_flags["openclaw_enabled"] = False
            _set_capability("openclaw", False, "")

    try:
        new_up = Modules.agent_flags.get("user_plugin_enabled", False)
        if prev_up != new_up:
            logger.info("[Agent] user_plugin_enabled toggled %s via /agent/flags", "ON" if new_up else "OFF")
    except Exception:
        pass
    try:
        new_nk = Modules.agent_flags.get("openclaw_enabled", False)
        if prev_nk != new_nk:
            logger.info("[Agent] openclaw_enabled toggled %s via /agent/flags", "ON" if new_nk else "OFF")
    except Exception:
        pass

    # 4. Handle OpenFang Flag
    if isinstance(of, bool):
        if of:
            adapter = Modules.openfang
            if adapter and adapter.init_ok:
                Modules.agent_flags["openfang_enabled"] = True
                _set_capability("openfang", True, "")
            elif adapter:
                # init_ok 为 False，尝试重新连接
                ok = await asyncio.to_thread(adapter.check_connectivity)
                if ok:
                    _set_capability("openfang", True, "")
                    Modules.agent_flags["openfang_enabled"] = True
                    logger.info("[Agent] OpenFang re-connected on toggle")
                else:
                    Modules.agent_flags["openfang_enabled"] = False
                    _set_capability("openfang", False, "OPENFANG_DAEMON_UNREACHABLE")
                    logger.warning("[Agent] Cannot enable OpenFang: not connected (%s)", adapter.last_error)
            else:
                Modules.agent_flags["openfang_enabled"] = False
                logger.warning("[Agent] Cannot enable OpenFang: adapter not initialized")
        else:
            Modules.agent_flags["openfang_enabled"] = False
            # Cancel any in-flight openfang tasks
            if Modules.openfang:
                try:
                    await Modules.openfang.cancel_running(None)
                except Exception as e:
                    logger.warning("[Agent] OpenFang cancel on disable failed: %s", e)

    # Persist user intent for each explicitly-requested flag.
    # Rule: a flag is persisted only when the user's request actually took
    # effect in-memory. If the user requested ON but capability auto-rejected
    # (LLM unreachable, module not loaded, etc.), the in-memory flag stays
    # False — we do NOT persist a True intent for that case, because the
    # toggle visibly didn't take. Disable requests (False) are always
    # persisted faithfully (no capability check involved).
    # The capability-auto-disable path inside
    # ``_fire_agent_llm_connectivity_check`` also intentionally does NOT
    # touch intent — it flips the in-memory flag but leaves persisted intent
    # so a transient LLM blip doesn't wipe the user's preference.
    if persist_intent:
        try:
            from app.agent_runtime_intent import set_intent
            for key, requested in (
                ("computer_use_enabled", cf),
                ("browser_use_enabled", bf),
                ("user_plugin_enabled", uf),
                ("openclaw_enabled", nf),
                ("openfang_enabled", of),
            ):
                if not isinstance(requested, bool):
                    continue
                if requested is False:
                    set_intent(key, False)
                elif bool(Modules.agent_flags.get(key, False)):
                    set_intent(key, True)
                # else: requested=True but capability rejected → leave intent untouched
        except Exception as exc:
            logger.warning("[Agent] Failed to persist agent flag intent: %s", exc)

    changed = Modules.agent_flags != old_flags or bool(Modules.analyzer_enabled) != old_analyzer_enabled
    if changed:
        _bump_state_revision()
    await _emit_agent_status_update(lanlan_name=lanlan_name)
    return {"success": True, "agent_flags": _agent_flags_snapshot()}


@app.post("/agent/command")
async def agent_command(payload: Dict[str, Any]):
    t0 = time.perf_counter()
    request_id = (payload or {}).get("request_id") or str(uuid.uuid4())
    command = (payload or {}).get("command")
    lanlan_name = (payload or {}).get("lanlan_name")
    if command == "set_agent_enabled":
        enabled = bool((payload or {}).get("enabled"))
        # ``_persist_intent`` (default True) gates whether this call writes
        # the user's intent to ``agent_runtime_intent.json``. The restore
        # path replays past intents through this same code path with
        # ``_persist_intent=False`` so the replay doesn't re-write what it's
        # reading.
        persist_intent = bool((payload or {}).get("_persist_intent", True))
        gate = _check_agent_api_gate()
        if enabled:
            Modules.analyzer_enabled = True
            Modules.analyzer_profile = (payload or {}).get("profile", {}) or {}
            if gate.get("ready") is True:
                adapter_refreshed = _try_refresh_computer_use_adapter(force=True)
                if not adapter_refreshed and Modules.computer_use is not None:
                    logger.info("[Agent] ComputerUse adapter refresh failed; falling back to existing adapter")
                if Modules.computer_use is not None:
                    _set_capability("computer_use", False, "AGENT_PRECHECK_PENDING")
                    _set_capability("browser_use", False, "AGENT_PRECHECK_PENDING")
                    asyncio.ensure_future(_fire_agent_llm_connectivity_check(queue=True))
                else:
                    _set_capability("computer_use", False, "AGENT_CU_MODULE_NOT_LOADED")
                    _set_capability("browser_use", False, "AGENT_CU_MODULE_NOT_LOADED")
                if Modules.agent_flags.get("openclaw_enabled"):
                    _start_openclaw_enable_probe(lanlan_name)
            else:
                first_reason = (gate.get("reasons") or ["AGENT_ENDPOINT_NOT_CONFIGURED"])[0]
                _set_capability("computer_use", False, first_reason)
                _set_capability("browser_use", False, first_reason)
        else:
            Modules.analyzer_enabled = False
            Modules.analyzer_profile = {}
            _cancel_openclaw_enable_probe()
            # NOTE: sub flags are NOT reset here. The master switch is a runtime
            # gate, not a clear-all command — sub flags carry the user's intent
            # for each component and must survive a master OFF/ON cycle (so the
            # user doesn't have to re-tick every sub-toggle after disabling the
            # master). All analysis / dispatch paths upstream of sub-flag checks
            # already test ``Modules.analyzer_enabled`` first (see lines ~1653,
            # 2007, 2056, 3453), so leaving sub flags ON cannot let any
            # component "secretly keep running". The actual stop is enforced by
            # ``end_all`` + ``_ensure_plugin_lifecycle_stopped`` + the probe
            # cancel above; ``intent`` (persistent) is also intentionally left
            # untouched here for the same reason.
            _set_capability("user_plugin", True, "")
            _set_capability("openclaw", False, "")
            await admin_control({"action": "end_all"})
            await _ensure_plugin_lifecycle_stopped()
        if persist_intent:
            try:
                from app.agent_runtime_intent import set_intent
                set_intent("analyzer_enabled", enabled)
            except Exception as exc:
                logger.warning("[Agent] Failed to persist analyzer_enabled intent: %s", exc)
        _bump_state_revision()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {
            "success": True,
            "request_id": request_id,
            "is_free_version": bool(gate.get("is_free_version")),
            "agent_api_gate": gate,
            "timing": {"agent_total_ms": total_ms},
        }
    if command == "set_flag":
        key = (payload or {}).get("key")
        value = bool((payload or {}).get("value"))
        if key not in {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled", "openclaw_enabled", "openfang_enabled"}:
            raise HTTPException(400, "invalid flag key")
        t_set = time.perf_counter()
        await set_agent_flags({"lanlan_name": lanlan_name, key: value})
        set_ms = round((time.perf_counter() - t_set) * 1000, 2)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s key=%s set_flags_ms=%s total_ms=%s", request_id, command, key, set_ms, total_ms)
        return {"success": True, "request_id": request_id, "timing": {"set_flags_ms": set_ms, "agent_total_ms": total_ms}}
    if command == "refresh_state":
        snapshot = _collect_agent_status_snapshot()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {"success": True, "request_id": request_id, "snapshot": snapshot, "timing": {"agent_total_ms": total_ms}}
    raise HTTPException(400, "unknown command")


# ─── Agent runtime intent restore ───────────────────────────────────────
#
# At server start, ``Modules.analyzer_enabled`` and ``Modules.agent_flags``
# are all False; the user must re-tick every toggle they had on before
# restart. Restore replays the persisted intent (see ``agent_runtime_intent``
# module) the first time a real client session enters via
# ``greeting_check``, so the user's switches "just come back" the way the
# plugin manager's per-plugin disable already does.
#
# The replay walks the same ``set_agent_enabled`` / ``set_agent_flags`` code
# paths a manual UI toggle would, so capability checks, gate logic, and
# notifications all behave identically — and ``_persist_intent=False`` makes
# the replay non-recursive (it doesn't overwrite the intent file it's
# reading).
#
# Failure mode: LLM-dependent flags get a 15s probe window (3 × 4s ping with
# 5s spacing). Any permanent reason or all-three failure clears that intent
# to False and surfaces ``AGENT_AUTO_DISABLED_*`` notifications — the goal
# is to tell the user "your API is dead, fix it" rather than retry forever.

_intent_restore_done = False
_intent_restore_lock: Optional[asyncio.Lock] = None

# Restore probe budget. Worst-case wall time when probes keep timing out:
#   3 attempts × 6s timeout + 2 inter-attempt sleeps × 7s = ~32s.
# In practice the ping resolves in <1s on a healthy connection so users
# typically see toggles flip back within the first attempt. Tuning rationale:
# 6s per-call timeout gives cold-start DNS / TLS handshake comfortable room
# without dragging out the failure path; 7s gap lets a transient burst
# throttle window expire between attempts.
_RESTORE_PING_TIMEOUT_S = 6.0
_RESTORE_PING_INTERVAL_S = 7.0
_RESTORE_PING_MAX_ATTEMPTS = 3


async def _maybe_restore_agent_intent() -> None:
    """Idempotent restore entry. Safe to call from every greeting_check."""
    global _intent_restore_done, _intent_restore_lock
    if _intent_restore_done:
        return
    if os.environ.get("NEKO_DISABLE_AGENT_AUTO_RESTORE") == "1":
        # Escape hatch: if some restore step ever causes server lockup,
        # the user can launch with this env var to skip restore entirely
        # and re-toggle manually.
        _intent_restore_done = True
        logger.info("[Agent] NEKO_DISABLE_AGENT_AUTO_RESTORE=1, skipping intent restore")
        return
    if _intent_restore_lock is None:
        _intent_restore_lock = asyncio.Lock()
    async with _intent_restore_lock:
        if _intent_restore_done:
            return
        _intent_restore_done = True
        try:
            await _do_restore_agent_intent()
        except Exception as exc:
            logger.error("[Agent] Intent restore failed: %s", exc, exc_info=True)


async def _do_restore_agent_intent() -> None:
    from app.agent_runtime_intent import load_intent

    intent = load_intent()
    if not intent:
        logger.info("[Agent] No persisted agent intent to restore")
        return
    logger.info("[Agent] Restoring agent intent: %s", intent)

    # Master gate is the runtime prerequisite for *any* sub component:
    # sub-flag intents only matter when the master switch is ON. Since
    # set_agent_enabled(False) no longer wipes sub-flag intent, it's a
    # legitimate persisted state to have e.g. ``analyzer_enabled=False``
    # alongside ``user_plugin_enabled=True`` (the user toggled the master
    # off but kept their sub-flag preferences). In that case we must NOT
    # spin up plugin lifecycle / probe LLM / fire openclaw probe — the
    # user explicitly disabled the master. Sub-flag intents stay in the
    # file untouched, so the next time the user turns the master back on
    # those flags will activate via the normal toggle path.
    master_enabled = bool(intent.get("analyzer_enabled"))
    if not master_enabled:
        logger.info(
            "[Agent] Restore: analyzer_enabled intent is %s, skipping sub-flag restore",
            intent.get("analyzer_enabled"),
        )
        return

    # Master ON — call agent_command directly (plain async fn despite the
    # FastAPI decorator) with _persist_intent=False so the replay doesn't
    # re-write what we just read.
    try:
        await agent_command({
            "command": "set_agent_enabled",
            "enabled": True,
            "_persist_intent": False,
        })
    except Exception as exc:
        logger.warning("[Agent] Failed to restore analyzer_enabled: %s", exc)
        # Master gate failed to activate → don't even try sub flags
        return

    # 2. Two fully-independent parallel tracks. CU/BU are LLM-coupled
    # (probe-gated). user_plugin runs on its own lifecycle and explicitly
    # does NOT wait for the LLM — plugins don't depend on the agent model.
    parallel: List[asyncio.Task] = []

    if intent.get("computer_use_enabled") or intent.get("browser_use_enabled"):
        t = asyncio.create_task(_restore_llm_dependent_flags(intent))
        Modules._persistent_tasks.add(t)
        t.add_done_callback(Modules._persistent_tasks.discard)
        parallel.append(t)

    if intent.get("user_plugin_enabled"):
        t = asyncio.create_task(_restore_user_plugin())
        Modules._persistent_tasks.add(t)
        t.add_done_callback(Modules._persistent_tasks.discard)
        parallel.append(t)

    # OpenClaw has its own bounded probe — no separate retry needed,
    # ``set_agent_flags`` will fire the probe task and we trust that.
    if intent.get("openclaw_enabled"):
        try:
            await set_agent_flags({
                "openclaw_enabled": True,
                "_persist_intent": False,
            })
        except Exception as exc:
            logger.warning("[Agent] Failed to restore openclaw_enabled: %s", exc)

    # OpenFang is similar — single capability check on the adapter, fast,
    # no separate retry needed.
    if intent.get("openfang_enabled"):
        try:
            await set_agent_flags({
                "openfang_enabled": True,
                "_persist_intent": False,
            })
        except Exception as exc:
            logger.warning("[Agent] Failed to restore openfang_enabled: %s", exc)

    # We deliberately don't gather() the parallel tasks — they update
    # capability + flags + intent on their own, and the user sees the
    # results via the normal status snapshot push. Awaiting here would
    # block the greeting_check handler for up to 15s.


async def _restore_llm_dependent_flags(intent: dict) -> None:
    """Probe LLM ≤3 times with 5s spacing. On success flip the in-memory
    CU/BU flags via set_agent_flags; on permanent failure or all-three
    fail, clear those intents and emit AGENT_AUTO_DISABLED_* notifications."""
    from app.agent_runtime_intent import set_intent
    from brain.computer_use import PERMANENT_CONNECTIVITY_REASONS

    adapter = Modules.computer_use
    if adapter is None:
        # Module not loaded is permanent — no point retrying.
        logger.warning("[Agent] Restore: computer_use module not loaded; clearing CU/BU intent")
        for key, code in (
            ("computer_use_enabled", "AGENT_AUTO_DISABLED_COMPUTER"),
            ("browser_use_enabled", "AGENT_AUTO_DISABLED_BROWSER"),
        ):
            if intent.get(key):
                set_intent(key, False)
                Modules.notification = json.dumps({
                    "code": code,
                    "details": {"reason_code": "AGENT_CU_MODULE_NOT_LOADED"},
                })
        _bump_state_revision()
        await _emit_agent_status_update()
        return

    last_reason = "AGENT_LLM_UNREACHABLE"
    success = False
    for attempt in range(_RESTORE_PING_MAX_ATTEMPTS):
        try:
            ok, reason = await asyncio.to_thread(
                adapter.check_connectivity,
                timeout_s=_RESTORE_PING_TIMEOUT_S,
            )
            if ok:
                success = True
                last_reason = ""
                break
            last_reason = reason or "AGENT_LLM_UNREACHABLE"
            if last_reason in PERMANENT_CONNECTIVITY_REASONS:
                logger.info(
                    "[Agent] Restore: permanent connectivity reason %s after %d/%d attempts; not retrying",
                    last_reason, attempt + 1, _RESTORE_PING_MAX_ATTEMPTS,
                )
                break
        except Exception as exc:
            logger.warning(
                "[Agent] Restore probe attempt %d/%d raised: %s",
                attempt + 1, _RESTORE_PING_MAX_ATTEMPTS, exc,
            )
            last_reason = "AGENT_LLM_UNREACHABLE"
        if attempt < _RESTORE_PING_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_RESTORE_PING_INTERVAL_S)

    if success:
        # Hand off to the regular toggle path so capability cache + UI
        # snapshot stay consistent with manual toggling.
        payload: Dict[str, Any] = {"_persist_intent": False}
        if intent.get("computer_use_enabled"):
            payload["computer_use_enabled"] = True
        if intent.get("browser_use_enabled"):
            payload["browser_use_enabled"] = True
        if len(payload) > 1:
            try:
                await set_agent_flags(payload)
                logger.info("[Agent] Restored CU/BU flags after successful probe")
            except Exception as exc:
                logger.warning("[Agent] Failed to apply CU/BU after probe: %s", exc)
        return

    # All retries exhausted (or permanent error): tell the user, clear intent.
    for key, code in (
        ("computer_use_enabled", "AGENT_AUTO_DISABLED_COMPUTER"),
        ("browser_use_enabled", "AGENT_AUTO_DISABLED_BROWSER"),
    ):
        if intent.get(key):
            set_intent(key, False)
            Modules.notification = json.dumps({
                "code": code,
                "details": {"reason_code": last_reason},
            })
            logger.info(
                "[Agent] Restore: cleared intent for %s after %d failed probes (reason=%s)",
                key, _RESTORE_PING_MAX_ATTEMPTS, last_reason,
            )
    _bump_state_revision()
    await _emit_agent_status_update()


async def _restore_user_plugin() -> None:
    """Hand off to the standard /agent/flags path. user_plugin does NOT
    require the LLM probe to be green — plugins run on their own lifecycle,
    so we trigger them straight away in parallel. Any startup failure goes
    through the existing _bg_plugin_enable async path and lazy-init fallback
    at first ``analyze`` time still covers leftover cases."""
    try:
        await set_agent_flags({
            "user_plugin_enabled": True,
            "_persist_intent": False,
        })
        logger.info("[Agent] Restore: user_plugin_enabled requested")
    except Exception as exc:
        logger.warning("[Agent] Failed to restore user_plugin_enabled: %s", exc)


def _reset_intent_restore_for_testing() -> None:
    """Test helper: clear the once-flag so a test can re-run restore."""
    global _intent_restore_done, _intent_restore_lock
    _intent_restore_done = False
    _intent_restore_lock = None


@app.get("/computer_use/availability")
async def computer_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    if not Modules.computer_use:
        _try_refresh_computer_use_adapter(force=True)
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    if not Modules.computer_use:
        if Modules.agent_flags.get("computer_use_enabled"):
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.notification = json.dumps({"code": "AGENT_CU_AUTO_CLOSED"})
        raise HTTPException(503, "ComputerUse not ready")
    if not getattr(Modules.computer_use, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())

    status = await asyncio.to_thread(Modules.computer_use.is_available)
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    _set_capability("computer_use", bool(status.get("ready")) if isinstance(status, dict) else False, reasons[0] if reasons else "")
    
    # Auto-update flag if capability lost
    if not status.get("ready") and Modules.agent_flags.get("computer_use_enabled"):
        logger.info("[Agent] Computer Use capability lost, disabling flag")
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.notification = json.dumps({"code": "AGENT_CU_CAPABILITY_LOST", "details": {"reason_code": status.get('reasons', [])[0] if status.get('reasons') else 'unknown'}})
        
    return status


@app.post("/notify_config_changed")
async def notify_config_changed():
    """Called by the main server after API-key / model config is saved.
    Rebuilds the CUA adapter with fresh config and kicks off a non-blocking
    LLM connectivity check — but only when the user actually has the master
    switch on AND at least one LLM-dependent sub flag enabled.

    The master gate is required because with the new master-OFF semantics
    (sub flags carry user intent and survive master cycling),
    ``computer_use_enabled``/``browser_use_enabled`` can legitimately stay
    True while the master is off. The old ``or`` condition would otherwise
    fire a probe on every voice/chat config save and pop a transient
    "cat-paw preflight failed" toast for a feature the user has explicitly
    disabled at the master.

    Sub-flag check still gates probes when the master is on but the user
    isn't using CU/BU — same rationale as the original docstring: routine
    config saves shouldn't probe for a feature nobody's using."""
    _try_refresh_computer_use_adapter(force=True)
    _rewire_computer_use_dependents()
    flags = Modules.agent_flags or {}
    if Modules.analyzer_enabled and (
        flags.get("computer_use_enabled") or flags.get("browser_use_enabled")
    ):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
        return {"success": True, "message": "CUA adapter refreshed, connectivity check started"}
    return {"success": True, "message": "CUA adapter refreshed; probe skipped (agent idle)"}


@app.get("/browser_use/availability")
async def browser_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    bu = Modules.browser_use
    if not bu:
        raise HTTPException(503, "BrowserUse not ready")
    if not getattr(bu, "_ready_import", False):
        reason = f"browser-use not installed: {bu.last_error}"
        _set_capability("browser_use", False, reason)
        return {"enabled": True, "ready": False, "reasons": [reason], "provider": "browser-use"}
    # LLM connectivity — reuse the shared agent-LLM check
    cua = Modules.computer_use
    if cua and not getattr(cua, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    llm_ok = cua is not None and getattr(cua, "init_ok", False)
    reasons = []
    if not llm_ok:
        reasons.append(cua.last_error if cua and cua.last_error else "Agent LLM not connected")
    ready = llm_ok and getattr(bu, "_ready_import", False)
    _set_capability("browser_use", ready, reasons[0] if reasons else "")
    return {"enabled": True, "ready": ready, "reasons": reasons, "provider": "browser-use"}


@app.post("/computer_use/run")
async def computer_use_run(payload: Dict[str, Any]):
    if not Modules.computer_use:
        raise HTTPException(503, "ComputerUse not ready")
    instruction = (payload or {}).get("instruction", "").strip()
    screenshot_b64 = (payload or {}).get("screenshot_b64")
    if not instruction:
        raise HTTPException(400, "instruction required")
    import base64
    screenshot = base64.b64decode(screenshot_b64) if isinstance(screenshot_b64, str) else None
    # Preflight readiness check to avoid scheduling tasks that will fail immediately
    try:
        avail = await asyncio.to_thread(Modules.computer_use.is_available)
        if not avail.get("ready"):
            return JSONResponse(content={"success": False, "error": "ComputerUse not ready", "reasons": avail.get("reasons", [])}, status_code=503)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"availability check failed: {e}"}, status_code=503)
    lanlan_name = (payload or {}).get("lanlan_name")
    # Dedup check
    dup, matched = await _is_duplicate_task(instruction, lanlan_name)
    if dup:
        return JSONResponse(content={"success": False, "duplicate": True, "matched_id": matched}, status_code=409)
    info = _spawn_task("computer_use", {"instruction": instruction, "screenshot": screenshot})
    info["lanlan_name"] = lanlan_name
    return {"success": True, "task_id": info["id"], "status": info["status"], "start_time": info["start_time"]}


@app.post("/browser_use/run")
async def browser_use_run(payload: Dict[str, Any]):
    if not Modules.browser_use:
        raise HTTPException(503, "BrowserUse not ready")
    instruction = (payload or {}).get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "instruction required")
    # Debug/API entry: must share the dispatch mutex with the analyzer path —
    # the adapter is a singleton whose cancel flag and browser session cannot
    # tolerate concurrent run_instruction calls.
    if Modules.browser_use_dispatch_lock is None:
        Modules.browser_use_dispatch_lock = asyncio.Lock()

    async def _locked_run():
        async with Modules.browser_use_dispatch_lock:
            return await Modules.browser_use.run_instruction(instruction)

    # Run as a tracked background task so end_all can cancel a wedged direct
    # run (otherwise it would survive end_all still holding the mutex).
    run_task = asyncio.create_task(_locked_run())
    Modules._background_tasks.add(run_task)
    run_task.add_done_callback(Modules._background_tasks.discard)
    try:
        result = await run_task
        return {"success": bool(result.get("success", False)), "result": result}
    except asyncio.CancelledError:
        if run_task.cancelled():
            # end_all tore this direct run down.
            return JSONResponse(content={"success": False, "error": "cancelled by end_all"}, status_code=500)
        # The HTTP request itself was cancelled — don't leak the inner task.
        run_task.cancel()
        raise
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@app.get("/mcp/availability")
async def mcp_availability():
    return {"ready": False, "capabilities_count": 0, "reasons": ["MCP 已移除"]}


@app.get("/tasks")
async def list_tasks():
    """Quickly return the status of all current tasks, optimized for response speed"""
    items = []
    
    try:
        for tid, info in Modules.task_registry.items():
            try:
                task_item = {
                    "id": info.get("id", tid),
                    "type": info.get("type"),
                    "status": info.get("status"),
                    "start_time": info.get("start_time"),
                    "params": info.get("params"),
                    "result": info.get("result"),
                    "error": info.get("error"),
                    "lanlan_name": info.get("lanlan_name"),
                    "source": "runtime"
                }
                items.append(task_item)
            except Exception:
                continue
        
        debug_info = {
            "task_registry_count": len(Modules.task_registry),
            "total_returned": len(items)
        }
        
        return {"tasks": items, "debug": debug_info}
    
    except Exception as e:
        return {
            "tasks": items,
            "debug": {
                "error": str(e),
                "partial_results": True,
                "total_returned": len(items)
            }
        }


@app.post("/admin/control")
async def admin_control(payload: Dict[str, Any]):
    action = (payload or {}).get("action")
    if action == "end_all":
        # Mark every active registry task cancelled and notify the frontend
        # BEFORE the potentially-slow teardown below. The task HUD is purely
        # event-driven (no HTTP polling), so without these emits the cards of
        # tasks whose dispatch coroutine is stuck (e.g. a browser-use agent
        # wedged inside an LLM call) would stay "running" forever once the
        # registry is cleared. Dispatch coroutines that do wake up emit the
        # same terminal event again; duplicate cancel records are tolerated
        # by design (see get_cancelled_user_sigs).
        async def _mark_and_emit_cancelled() -> None:
            for tid, info in list(Modules.task_registry.items()):
                if info.get("status") not in ("queued", "running"):
                    continue
                info["status"] = "cancelled"
                info["error"] = "Cancelled by user"
                _task_tracker.record_completed(
                    info.get("lanlan_name"),
                    task_id=tid,
                    method=str(info.get("type") or ""),
                    desc=_tracker_desc_for_task_info(info),
                    detail="Cancelled by user",
                    success=False,
                    cancelled=True,
                    trigger_user_fingerprint=info.get("_trigger_user_fingerprint"),
                )
                try:
                    await _emit_main_event(
                        "task_update", info.get("lanlan_name"),
                        task={"id": tid, "status": "cancelled", "type": info.get("type"),
                              "end_time": _now_iso(), "params": info.get("params", {}),
                              "error": "Cancelled by user"},
                    )
                except Exception as exc:
                    logger.debug("[Agent] end_all: emit task_update(cancelled) failed: task_id=%s error=%s", tid, exc)

        await _mark_and_emit_cancelled()

        # Cancel any in-flight background analyzer/dispatch tasks. Include the
        # per-task dispatch handles explicitly so a handle that fell out of
        # _background_tasks bookkeeping still receives the cancel.
        tasks_to_await = []
        for t in set(Modules._background_tasks) | set(Modules.task_async_handles.values()):
            if not t.done():
                t.cancel()
                tasks_to_await.append(t)
        if tasks_to_await:
            # Bounded wait: a dispatch coroutine stuck in an uncancellable
            # spot must not wedge end_all itself (the frontend proxy gives up
            # after 5s and the user sees the ✕ do nothing).
            done, pending = await asyncio.wait(tasks_to_await, timeout=10.0)
            if pending:
                logger.warning(
                    "[Agent] end_all: %d task(s) still not finished 10s after cancel; continuing teardown",
                    len(pending),
                )
                # A wedged dispatch coroutine may still hold the browser-use
                # mutex; future tasks would queue on it forever. Every known
                # handle was cancelled above (the ghost raises CancelledError
                # at its next await) and the browser session is torn down
                # below, so handing fresh tasks a new lock is safe.
                lock = Modules.browser_use_dispatch_lock
                if lock is not None and lock.locked():
                    logger.warning("[Agent] end_all: browser_use dispatch lock still held after timeout; resetting")
                    Modules.browser_use_dispatch_lock = asyncio.Lock()
            for res in done:
                try:
                    exc = res.exception()
                except asyncio.CancelledError:
                    continue
                if exc is not None:
                    logger.warning(f"[Agent] Error awaiting cancelled background task: {exc}")
        Modules._background_tasks.clear()

        # Signal computer-use adapter to cancel at next step boundary
        if Modules.computer_use:
            Modules.computer_use.cancel_running()

        # Cancel any in-flight asyncio tasks and clear registry
        if Modules.active_computer_use_async_task and not Modules.active_computer_use_async_task.done():
            Modules.active_computer_use_async_task.cancel()
            try:
                await Modules.active_computer_use_async_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[Agent] Error awaiting cancelled computer use task: {e}")

        # Wait for the underlying thread to actually finish before clearing state,
        # so no pyautogui calls are still in-flight when we allow new tasks.
        cu = Modules.computer_use
        if cu is not None and hasattr(cu, "wait_for_completion"):
            loop = asyncio.get_running_loop()
            finished = await loop.run_in_executor(None, cu.wait_for_completion, 10.0)
            if not finished:
                logger.warning("[Agent] CUA thread did not stop within 10s during end_all")

        # Rescan right before wiping the registry: an in-flight analyzer may
        # have registered a new task while any of the awaits above yielded.
        # Its dispatch handle was cancelled above (or its scheduler guard
        # skips it), but the frontend still needs the terminal event.
        await _mark_and_emit_cancelled()

        Modules.task_registry.clear()
        Modules.last_user_turn_fingerprint.clear()
        Modules.proactive_analyze_count.clear()
        Modules.last_proactive_assistant_fingerprint.clear()
        # Clear scheduling state
        Modules.computer_use_running = False
        Modules.active_computer_use_task_id = None
        Modules.active_computer_use_async_task = None
        # Drain the asyncio scheduler queue
        try:
            if Modules.computer_use_queue is not None:
                while not Modules.computer_use_queue.empty():
                    await Modules.computer_use_queue.get()
        except Exception:
            pass
        # Signal browser-use adapter to cancel at next step boundary
        try:
            if Modules.browser_use:
                Modules.browser_use.cancel_running()
                Modules.browser_use._stop_overlay()
                Modules.browser_use._agents.clear()
                try:
                    if Modules.browser_use._browser_session is not None:
                        await Modules.browser_use._remove_overlay(Modules.browser_use._browser_session)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Agent] Error cleaning browser-use agents during end_all: {e}")
        Modules.active_browser_use_task_id = None
        # Cancel any in-flight openfang tasks
        try:
            if Modules.openfang:
                await Modules.openfang.cancel_running(None)
        except Exception as e:
            logger.warning(f"[Agent] Error cancelling openfang tasks during end_all: {e}")
        # Reset computer-use step history so stale context is cleared
        try:
            if Modules.computer_use:
                Modules.computer_use.reset()
        except Exception:
            pass
        return {"success": True, "message": "all tasks terminated and cleared"}
    elif action == "enable_analyzer":
        Modules.analyzer_enabled = True
        Modules.analyzer_profile = (payload or {}).get("profile", {})
        return {"success": True, "analyzer_enabled": True, "profile": Modules.analyzer_profile}
    elif action == "disable_analyzer":
        Modules.analyzer_enabled = False
        Modules.analyzer_profile = {}
        # cascade end_all
        await admin_control({"action": "end_all"})
        return {"success": True, "analyzer_enabled": False}
    else:
        raise HTTPException(400, "unknown action")

