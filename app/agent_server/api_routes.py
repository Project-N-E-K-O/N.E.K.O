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

"""Agent control, execution, proxy, and intent-restore endpoints."""

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
    _browser_use_dependency_status,
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
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _ensure_browser_use_adapter,
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
from .api_runtime import (  # noqa: F401
    ToolCorrectionPayload,
    _agent_master_enabled,
    _background_analyze_and_plan,
    _check_agent_api_gate,
    _do_analyze_and_plan,
    _handle_proactive_analyze,
    _handle_voice_transcript_request,
    _on_session_event,
    _user_plugins_enabled,
    _voice_transcript_plugin_gate_reason,
    cancel_task,
    complete_deferred_task,
    get_task,
    health,
    plugin_execute_direct,
    shutdown,
    startup,
    submit_task_correction,
)

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
            dependency_ready, dependency_error = _browser_use_dependency_status()
            if not dependency_ready:
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_BU_NOT_INSTALLED", "details": {"error": dependency_error}})
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["browser_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_BU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                Modules.agent_flags["browser_use_enabled"] = True
                _set_capability("browser_use", True, "")
        else:
            Modules.agent_flags["browser_use_enabled"] = False

    # Explicit disable and automatic gate demotion both release the heavy
    # browser-use graph and any keep-alive Chromium subprocess immediately.
    if bf is False or (
        old_flags.get("browser_use_enabled", False)
        and not Modules.agent_flags.get("browser_use_enabled", False)
    ):
        _create_tracked_task(_close_browser_use_adapter())

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
    dependency_ready, dependency_error = _browser_use_dependency_status()
    if not dependency_ready:
        reason = f"browser-use not installed: {dependency_error}"
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
    ready = llm_ok and dependency_ready
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
    if not await _ensure_browser_use_adapter():
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
