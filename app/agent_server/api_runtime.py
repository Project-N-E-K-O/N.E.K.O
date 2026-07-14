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

"""Analyzer, lifecycle, and task endpoints for the agent server."""

from .api_shared import (  # noqa: F401
    AGENT_HISTORY_TURNS,
    AGENT_PROACTIVE_ANALYZE_ENABLED,
    AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION,
    AgentServerEventBridge,
    AgentTaskTracker,
    Any,
    BaseModel,
    BrowserUseAdapter,
    ComputerUseAdapter,
    DEFERRED_TASK_TIMEOUT,
    Dict,
    DirectTaskExecutor,
    ERROR_MESSAGE_MAX_CHARS,
    EXCEPTION_TEXT_MAX_CHARS,
    FastAPI,
    Field,
    HTTPException,
    JSONResponse,
    List,
    Modules,
    OPENCLAW_ENABLE_CHECK_ATTEMPTS,
    OPENCLAW_ENABLE_CHECK_INTERVAL,
    OPENFANG_BASE_URL,
    OpenClawAdapter,
    OpenFangAdapter,
    Optional,
    PLUGIN_NAME_CACHE_TTL,
    REDACTED_USER_TURN_MARKER,
    TASK_DETAIL_MAX_TOKENS,
    TASK_ERROR_MAX_TOKENS,
    TASK_REGISTRY_CLEANUP_TTL,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    TASK_TRACKER_INJECT_DETAIL_MAX_CHARS,
    TASK_TRACKER_MAX_RECORDS,
    TASK_TRACKER_TTL,
    TOOL_SERVER_PORT,
    TaskDeduper,
    ThrottledLogger,
    USER_NOTIFICATION_ERROR_MAX_CHARS,
    USER_NOTIFICATION_REASON_MAX_CHARS,
    USER_PLUGIN_SERVER_PORT,
    _LEGACY_CORRECTION_PUBLIC_KEYS,
    _agent_flags_snapshot,
    _bind_deferred_task,
    _build_analyze_event_fingerprint,
    _build_assistant_turn_fingerprint,
    _build_user_turn_fingerprint,
    _bump_state_revision,
    _cancel_openclaw_enable_probe,
    _cancel_openclaw_tasks_for_stop,
    _cleanup_task_registry,
    _collect_active_openclaw_task_ids,
    _collect_agent_status_snapshot,
    _collect_existing_task_descriptions,
    _close_browser_use_adapter,
    _computer_use_scheduler_loop,
    _create_tracked_task,
    _default_openclaw_task_description,
    _emit_agent_status_update,
    _emit_main_event,
    _emit_task_result,
    _ensure_browser_use_adapter,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _extract_tool_intent_as_text,
    _fire_agent_llm_connectivity_check,
    _fire_user_plugin_capability_check,
    _get_internal_correction_context,
    _get_plugin_display_id,
    _get_plugin_friendly_name,
    _get_throttled_logger,
    _install_runtime_bindings,
    _is_duplicate_task,
    _is_reply_suppressed,
    _last_user_message_signature,
    _llm_check_lock,
    _lookup_llm_result_fields,
    _normalize_lanlan_key,
    _now_iso,
    _openclaw_first_reason,
    _openclaw_notification,
    _openclaw_pending,
    _openclaw_reason_code,
    _openclaw_reason_text,
    _patch_malformed_tool_calls,
    _patch_openai_response,
    _patch_usage,
    _plugin_name_cache_lock,
    _plugin_terminal_status,
    _public_task_info,
    _redact_cancelled_user_turns,
    _repo_root,
    _resolve_delivery_mode,
    _resolve_openclaw_sender_id,
    _rewire_computer_use_dependents,
    _rp_lang,
    _rp_phrase,
    _run_computer_use_task,
    _run_openclaw_enable_probe,
    _set_capability,
    _set_internal_correction_context,
    _spawn_background_cancel,
    _spawn_task,
    _start_embedded_user_plugin_server,
    _start_openclaw_enable_probe,
    _stop_embedded_user_plugin_server,
    _task_tracker,
    _track_background_task,
    _tracker_desc_for_task_info,
    _try_refresh_computer_use_adapter,
    _tt,
    _user_message_payload_text,
    _user_message_sender_id,
    _user_message_signature,
    app,
    asyncio,
    channels,
    datetime,
    get_config_manager,
    get_session_manager,
    httpx,
    json,
    log_config,
    logger,
    mimetypes,
    os,
    parse_browser_use_result,
    parse_computer_use_result,
    parse_plugin_result,
    setup_logging,
    sys,
    time,
    timezone,
    uuid,
)

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
    """Throttled proactive-analyze path controlled by AGENT_PROACTIVE_ANALYZE_ENABLED.

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
        # Late import avoids the runtime -> routes initialization cycle while
        # preserving the original call point and shared restore-state owner.
        from .api_routes import _maybe_restore_agent_intent
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
        if Modules.agent_flags.get("browser_use_enabled", False):
            browser_use = await _ensure_browser_use_adapter()
            if browser_use is None or not getattr(browser_use, "_ready_import", False):
                Modules.agent_flags["browser_use_enabled"] = False
                reason = str(getattr(browser_use, "last_error", "") or "AGENT_BU_MODULE_NOT_LOADED")
                _set_capability("browser_use", False, reason)
                Modules.notification = json.dumps(
                    {"code": "AGENT_BU_NOT_INSTALLED", "details": {"error": reason}}
                )
                _bump_state_revision()
                await _emit_agent_status_update(lanlan_name=lanlan_name)
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

    # BrowserUse stays unloaded until its toggle, availability endpoint, or
    # direct run is requested.  OpenFang remains an independent background
    # connectivity task because Electron owns that external daemon lifecycle.
    _openfang_task = asyncio.create_task(_init_openfang_background())
    Modules._persistent_tasks.add(_openfang_task)
    _openfang_task.add_done_callback(Modules._persistent_tasks.discard)

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

    # BrowserSession.keep_alive keeps Chromium running between tasks; closing
    # the adapter is therefore required at service shutdown, not just task
    # cancellation, to reap the browser subprocess tree.
    await _close_browser_use_adapter()

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
