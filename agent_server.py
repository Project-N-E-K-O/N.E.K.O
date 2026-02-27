# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import uuid
import logging
import time
import hashlib
from typing import Dict, Any, Optional, ClassVar
from datetime import datetime
import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from utils.logger_config import setup_logging, ThrottledLogger

# Configure logging as early as possible so import-time failures are persisted.
logger, log_config = setup_logging(service_name="Agent", log_level=logging.INFO)

from config import TOOL_SERVER_PORT, USER_PLUGIN_SERVER_PORT
from utils.config_manager import get_config_manager
from main_logic.agent_event_bus import AgentServerEventBridge
try:
    from brain.planner import TaskPlanner
    from brain.analyzer import ConversationAnalyzer
    from brain.computer_use import ComputerUseAdapter
    from brain.browser_use_adapter import BrowserUseAdapter
    from brain.deduper import TaskDeduper
    from brain.task_executor import DirectTaskExecutor
    from brain.agent_session import get_session_manager
except Exception as e:
    logger.exception(f"[Agent] Module import failed during startup: {e}")
    raise


app = FastAPI(title="N.E.K.O Tool Server")


class Modules:
    planner: TaskPlanner | None = None
    analyzer: ConversationAnalyzer | None = None
    computer_use: ComputerUseAdapter | None = None
    browser_use: BrowserUseAdapter | None = None
    deduper: TaskDeduper | None = None
    task_executor: DirectTaskExecutor | None = None  # 新增：合并的任务执行器
    # Task tracking
    task_registry: Dict[str, Dict[str, Any]] = {}
    executor_reset_needed: bool = False
    analyzer_enabled: bool = False
    analyzer_profile: Dict[str, Any] = {}
    # Computer-use exclusivity and scheduling
    computer_use_queue: Optional[asyncio.Queue] = None
    computer_use_running: bool = False
    active_computer_use_task_id: Optional[str] = None
    active_computer_use_async_task: Optional[asyncio.Task] = None
    # Browser-use task tracking
    active_browser_use_task_id: Optional[str] = None
    active_browser_use_bg_task: Optional[asyncio.Task] = None
    # Agent feature flags (controlled by UI)
    agent_flags: Dict[str, Any] = {"computer_use_enabled": False, "browser_use_enabled": False, "user_plugin_enabled": False}
    # Notification queue for frontend (one-time messages)
    notification: Optional[str] = None
    # 使用统一的速率限制日志记录器（业务逻辑层面）
    throttled_logger: "ThrottledLogger" = None  # 延迟初始化
    agent_bridge: AgentServerEventBridge | None = None
    state_revision: int = 0
    # Serialize analysis+dispatch to prevent duplicate tasks from concurrent analyze_request events
    analyze_lock: Optional[asyncio.Lock] = None
    # Per-lanlan fingerprint of latest user-turn payload already consumed by analyzer
    last_user_turn_fingerprint: ClassVar[Dict[str, str]] = {}
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": "not checked"},
        "browser_use": {"ready": False, "reason": "not checked"},
        "user_plugin": {"ready": False, "reason": "not checked"},
    }
    _background_tasks: ClassVar[set] = set()
    _persistent_tasks: ClassVar[set] = set()


def _rewire_computer_use_dependents() -> None:
    """Keep planner/task_executor in sync after computer_use adapter refresh."""
    try:
        if Modules.task_executor is not None and hasattr(Modules.task_executor, "computer_use"):
            Modules.task_executor.computer_use = Modules.computer_use
    except Exception:
        pass
    try:
        if Modules.planner is not None and hasattr(Modules.planner, "computer_use"):
            Modules.planner.computer_use = Modules.computer_use
    except Exception:
        pass


def _try_refresh_computer_use_adapter(force: bool = False) -> bool:
    """
    Best-effort refresh for computer-use adapter.
    Useful when API key/model settings were fixed after agent_server startup.
    Does NOT block on LLM connectivity — call ``_fire_agent_llm_connectivity_check``
    afterwards to probe the endpoint asynchronously.
    """
    current = Modules.computer_use
    if not force and current is not None and getattr(current, "init_ok", False):
        return True
    try:
        refreshed = ComputerUseAdapter()
        Modules.computer_use = refreshed
        _rewire_computer_use_dependents()
        logger.info("[Agent] ComputerUse adapter rebuilt (connectivity pending)")
        return True
    except Exception as e:
        logger.warning(f"[Agent] ComputerUse adapter refresh failed: {e}")
        return False


async def _fire_agent_llm_connectivity_check() -> None:
    """Probe the shared Agent-LLM endpoint in a background thread.

    Both ComputerUse and BrowserUse rely on the same ``agent`` model config,
    so a single connectivity check covers both capabilities.  Updates
    ``init_ok`` on the CUA adapter and refreshes the capability cache for
    *both* computer_use and browser_use.
    """
    adapter = Modules.computer_use
    if adapter is None:
        return

    def _probe():
        return adapter.check_connectivity()

    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _probe)
        reason = "" if ok else (adapter.last_error or "Agent LLM unreachable")
        _set_capability("computer_use", ok, reason)
        # BrowserUse shares the same agent endpoint — mirror the LLM result
        # but also respect its own import-readiness gate.
        bu = Modules.browser_use
        if bu is not None:
            if not ok:
                _set_capability("browser_use", False, reason)
            elif not getattr(bu, "_ready_import", False):
                _set_capability("browser_use", False, f"browser-use not installed: {bu.last_error}")
            else:
                _set_capability("browser_use", True, "")
        if ok:
            logger.info("[Agent] Agent-LLM connectivity check passed")
        else:
            logger.warning("[Agent] Agent-LLM connectivity check failed: %s", reason)
    except Exception as e:
        logger.warning("[Agent] Agent-LLM connectivity check error: %s", e)
        _set_capability("computer_use", False, str(e))
        _set_capability("browser_use", False, str(e))


def _bump_state_revision() -> int:
    Modules.state_revision += 1
    return Modules.state_revision


def _set_capability(name: str, ready: bool, reason: str = "") -> None:
    Modules.capability_cache[name] = {"ready": bool(ready), "reason": reason or ""}


def _collect_existing_task_descriptions(lanlan_name: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (task_id, description) for queued/running tasks, optionally filtered by lanlan_name."""
    items: list[tuple[str, str]] = []
    # Planner task_pool
    if Modules.planner:
        for tid, t in Modules.planner.task_pool.items():
            try:
                if t.status in ("queued", "running"):
                    try:
                        if lanlan_name and t.meta.get("lanlan_name") not in (None, lanlan_name):
                            continue
                    except Exception:
                        pass
                    desc = t.title or t.original_query or ""
                    if desc:
                        items.append((tid, desc))
            except Exception:
                continue
    # Runtime tasks
    for tid, info in Modules.task_registry.items():
        try:
            if info.get("status") in ("queued", "running"):
                if lanlan_name and info.get("lanlan_name") not in (None, lanlan_name):
                    continue
                params = info.get("params") or {}
                desc = params.get("query") or params.get("instruction") or ""
                if desc:
                    items.append((tid, desc))
        except Exception:
            continue
    return items


async def _is_duplicate_task(query: str, lanlan_name: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Use LLM to judge if query duplicates any existing queued/running task."""
    try:
        if not Modules.deduper:
            return False, None
        candidates = _collect_existing_task_descriptions(lanlan_name)
        res = await Modules.deduper.judge(query, candidates)
        return bool(res.get("duplicate")), res.get("matched_id")
    except Exception as e:
        logger.warning(f"[Agent] Deduper judge failed: {e}")
        return False, None


# ============ Workers (run in subprocess) ============
# 注意: MCP processor 任务现在使用协程直接执行，不再需要子进程
# 仅 ComputerUse 任务仍使用子进程（因为需要独占执行）
# def _worker_processor(task_id: str, query: str, queue: mp.Queue):
#     try:
#         # Lazy import to avoid heavy init in parent
#         from brain.processor import Processor as _Proc
#         import asyncio as _aio
#         proc = _Proc()
        
#         # Log MCP processing start
#         print(f"[MCP] Starting processor task {task_id} with query: {query[:100]}...")
        
#         result = _aio.run(proc.process(query))
        
#         # Log MCP processing result
#         if result.get('can_execute'):
#             server_id = result.get('server_id', 'unknown')
#             reason = result.get('reason', 'no reason provided')
#             tool_calls = result.get('tool_calls', [])
#             tool_results = result.get('tool_results', [])
            
#             if tool_calls:
#                 tools_info = ", ".join([f"'{tool}'" for tool in tool_calls])
#                 print(f"[MCP] ✅ Task {task_id} executed successfully using MCP server '{server_id}' with tools: {tools_info}")
                
#                 # Log tool execution results
#                 for tool_result in tool_results:
#                     tool_name = tool_result.get('tool', 'unknown')
#                     if tool_result.get('success'):
#                         result_text = tool_result.get('result', 'No result')
#                         print(f"[MCP] 🔧 Tool {tool_name} result: {result_text}")
#                     else:
#                         error_text = tool_result.get('error', 'Unknown error')
#                         print(f"[MCP] ❌ Tool {tool_name} failed: {error_text}")
#             else:
#                 print(f"[MCP] ✅ Task {task_id} executed successfully using MCP server '{server_id}' (no specific tools called)")
            
#             print(f"[MCP]   Reason: {reason}")
#         else:
#             reason = result.get('reason', 'no reason provided')
#             print(f"[MCP] ❌ Task {task_id} failed to execute: {reason}")
        
#         queue.put({"task_id": task_id, "success": True, "result": result})
#     except Exception as e:
#         print(f"[MCP] 💥 Task {task_id} crashed with error: {str(e)}")
#         queue.put({"task_id": task_id, "success": False, "error": str(e)})




def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


async def _emit_task_result(
    lanlan_name: Optional[str],
    *,
    channel: str,
    task_id: str,
    success: bool,
    summary: str,
    detail: str = "",
    error_message: str = "",
) -> None:
    """Emit a structured task_result event to main_server."""
    if success:
        status = "completed"
    elif detail:
        status = "partial"
    else:
        status = "failed"
    _SUMMARY_LIMIT = 500
    _DETAIL_LIMIT = 1500
    _ERROR_LIMIT = 500
    await _emit_main_event(
        "task_result",
        lanlan_name,
        text=summary[:_SUMMARY_LIMIT],
        task_id=task_id,
        channel=channel,
        status=status,
        success=success,
        summary=summary[:_SUMMARY_LIMIT],
        detail=detail[:_DETAIL_LIMIT] if detail else "",
        error_message=error_message[:_ERROR_LIMIT] if error_message else "",
        timestamp=_now_iso(),
    )


def _check_agent_api_gate() -> Dict[str, Any]:
    """统一 Agent API 门槛检查。"""
    try:
        cm = get_config_manager()
        ok, reasons = cm.is_agent_api_ready()
        return {"ready": ok, "reasons": reasons, "is_free_version": cm.is_free_version()}
    except Exception as e:
        return {"ready": False, "reasons": [f"Agent API check failed: {e}"], "is_free_version": False}


async def _emit_main_event(event_type: str, lanlan_name: Optional[str], **payload) -> None:
    event = {"event_type": event_type, "lanlan_name": lanlan_name, **payload}
    if Modules.agent_bridge:
        try:
            sent = await Modules.agent_bridge.emit_to_main(event)
            if sent:
                return
        except Exception:
            pass


def _collect_agent_status_snapshot() -> Dict[str, Any]:
    gate = _check_agent_api_gate()
    flags = dict(Modules.agent_flags or {})
    capabilities = dict(Modules.capability_cache or {})
    # Include active (queued/running) tasks so frontend can restore after page refresh
    active_tasks = []
    for tid, info in Modules.task_registry.items():
        try:
            st = info.get("status")
            if st in ("queued", "running"):
                active_tasks.append({
                    "id": tid,
                    "status": st,
                    "type": info.get("type"),
                    "start_time": info.get("start_time"),
                    "params": info.get("params", {}),
                    "session_id": info.get("session_id"),
                })
        except Exception:
            continue
    return {
        "revision": Modules.state_revision,
        "server_online": True,
        "analyzer_enabled": bool(Modules.analyzer_enabled),
        "flags": flags,
        "gate": gate,
        "capabilities": capabilities,
        "active_tasks": active_tasks,
        "updated_at": _now_iso(),
    }


def _normalize_lanlan_key(lanlan_name: Optional[str]) -> str:
    name = (lanlan_name or "").strip()
    return name or "__default__"


def _build_user_turn_fingerprint(messages: Any) -> Optional[str]:
    """
    Build a stable fingerprint from user-role messages only.
    Used to ensure analyzer consumes each user turn once.
    """
    if not isinstance(messages, list):
        return None
    user_parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        # Keep text as primary signal, and attach optional metadata if present.
        if "text" in m and "content" not in m:
            logger.warning("[AgentServer] Deprecation Warning: Received user message with 'text' but no 'content'.")
        text = str(m.get("text") or m.get("content") or "").strip()
        mid = str(
            m.get("id")
            or m.get("message_id")
            or m.get("msg_id")
            or ""
        ).strip()
        ts = str(
            m.get("timestamp")
            or m.get("time")
            or m.get("created_at")
            or ""
        ).strip()
        user_parts.append(f"{text}|{mid}|{ts}")
    if not user_parts:
        return None
    payload = "\n".join(user_parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


async def _emit_agent_status_update(lanlan_name: Optional[str] = None) -> None:
    try:
        snapshot = _collect_agent_status_snapshot()
        await _emit_main_event(
            "agent_status_update",
            lanlan_name,
            snapshot=snapshot,
        )
    except Exception:
        pass


async def _on_session_event(event: Dict[str, Any]) -> None:
    if (event or {}).get("event_type") == "analyze_request":
        messages = event.get("messages", [])
        lanlan_name = event.get("lanlan_name")
        event_id = event.get("event_id")
        logger.info("[AgentAnalyze] analyze_request received: trigger=%s lanlan=%s messages=%d", event.get("trigger"), lanlan_name, len(messages) if isinstance(messages, list) else 0)
        if not Modules.analyzer_enabled:
            logger.info("[AgentAnalyze] skip: analyzer disabled (master switch off)")
            return
        if event_id:
            ack_task = asyncio.create_task(_emit_main_event("analyze_ack", lanlan_name, event_id=event_id))
            Modules._background_tasks.add(ack_task)
            ack_task.add_done_callback(Modules._background_tasks.discard)
        if isinstance(messages, list) and messages:
            # Consume only new user turn. Assistant turn_end without new user input should be ignored.
            lanlan_key = _normalize_lanlan_key(lanlan_name)
            fp = _build_user_turn_fingerprint(messages)
            if fp is None:
                logger.info("[AgentAnalyze] skip analyze: no user message found (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            if Modules.last_user_turn_fingerprint.get(lanlan_key) == fp:
                logger.info("[AgentAnalyze] skip analyze: no new user turn (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            Modules.last_user_turn_fingerprint[lanlan_key] = fp
            task = asyncio.create_task(_background_analyze_and_plan(messages, lanlan_name))
            Modules._background_tasks.add(task)
            task.add_done_callback(Modules._background_tasks.discard)



def _spawn_task(kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """生成 computer_use 任务条目并入队等待独占执行。"""
    task_id = str(uuid.uuid4())
    info = {
        "id": task_id,
        "type": kind,
        "status": "queued",
        "start_time": _now_iso(),
        "params": args,
        "result": None,
        "error": None,
    }
    if kind == "computer_use":
        Modules.task_registry[task_id] = info
        if Modules.computer_use_queue is None:
            Modules.computer_use_queue = asyncio.Queue()
        Modules.computer_use_queue.put_nowait({
            "task_id": task_id,
            "instruction": args.get("instruction", ""),
        })
        return info
    else:
        raise ValueError(f"Unknown task kind: {kind}")


async def _run_computer_use_task(
    task_id: str,
    instruction: str,
) -> None:
    """Run a computer-use task in a thread pool; emit results directly via ZeroMQ."""
    info = Modules.task_registry.get(task_id, {})
    lanlan_name = info.get("lanlan_name")

    # Mark running
    info["status"] = "running"
    info["start_time"] = _now_iso()
    Modules.computer_use_running = True
    Modules.active_computer_use_task_id = task_id

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={
                "id": task_id, "status": "running", "type": "computer_use",
                "start_time": info["start_time"], "params": info.get("params", {}),
            },
        )
    except Exception:
        pass

    # Execute in thread pool (run_instruction is synchronous/blocking)
    success = False
    cu_detail = ""
    loop = asyncio.get_running_loop()

    try:
        if Modules.computer_use is None or not hasattr(Modules.computer_use, "run_instruction"):
            success = False
            cu_detail = "ComputerUse adapter is inactive or invalid (e.g., reset)"
            info["error"] = cu_detail
            logger.error("[ComputerUse] Task %s aborted: %s", task_id, cu_detail)
        else:
            session_id = info.get("session_id")
            future = loop.run_in_executor(None, Modules.computer_use.run_instruction, instruction, session_id)
            res = await future
            if res is None:
                logger.debug("[ComputerUse] run_instruction returned None, treating as success")
                res = {"success": True}
            elif isinstance(res, dict) and "success" not in res:
                res["success"] = True
            success = bool(res.get("success", False))
            info["result"] = res
            if isinstance(res, dict):
                cu_detail = res.get("result") or res.get("message") or res.get("reason") or ""
            else:
                cu_detail = str(res) if res is not None else ""
    except asyncio.CancelledError:
        info["error"] = "Task was cancelled"
        logger.info("[ComputerUse] Task %s was cancelled", task_id)
    except Exception as e:
        info["error"] = str(e)
        logger.error("[ComputerUse] Task %s failed: %s", task_id, e)
    finally:
        info["status"] = "cancelled" if info.get("error") == "Task was cancelled" else ("completed" if success else "failed")
        Modules.computer_use_running = False
        Modules.active_computer_use_task_id = None
        Modules.active_computer_use_async_task = None

        # Emit task_update (terminal state)
        try:
            task_obj = asyncio.create_task(_emit_main_event(
                "task_update", lanlan_name,
                task={
                    "id": task_id, "status": info["status"], "type": "computer_use",
                    "start_time": info.get("start_time"), "end_time": _now_iso(),
                    "error": info.get("error"),
                },
            ))
            Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(Modules._background_tasks.discard)
        except Exception:
            pass

        # Emit structured task_result
        try:
            _done = "已完成" if success else "已结束"
            params = info.get("params") or {}
            desc = params.get("query") or params.get("instruction") or ""
            if cu_detail and desc:
                summary = f'你的任务“{desc}”{_done}：{cu_detail}'
            elif cu_detail:
                summary = f'你的任务{_done}：{cu_detail}'
            elif desc:
                summary = f'你的任务“{desc}”{_done}'
            else:
                summary = "任务已完成" if success else "任务执行失败"
            task_obj = asyncio.create_task(_emit_task_result(
                lanlan_name,
                channel="computer_use",
                task_id=task_id,
                success=success,
                summary=summary,
                detail=cu_detail,
                error_message=(info.get("error") or "") if not success else "",
            ))
            Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(Modules._background_tasks.discard)
        except Exception:
            pass

async def _computer_use_scheduler_loop():
    """Ensure only one computer-use task runs at a time by scheduling queued tasks."""
    if Modules.computer_use_queue is None:
        Modules.computer_use_queue = asyncio.Queue()
    while True:
        try:
            await asyncio.sleep(0.05)
            if Modules.computer_use_running:
                continue
            if Modules.computer_use_queue.empty():
                continue
            if not Modules.analyzer_enabled or not Modules.agent_flags.get("computer_use_enabled", False):
                while not Modules.computer_use_queue.empty():
                    try:
                        Modules.computer_use_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                continue
            next_task = await Modules.computer_use_queue.get()
            tid = next_task.get("task_id")
            if not tid or tid not in Modules.task_registry:
                continue
            Modules.active_computer_use_async_task = asyncio.create_task(_run_computer_use_task(
                tid, next_task.get("instruction", ""),
            ))
        except Exception:
            # Never crash the scheduler
            await asyncio.sleep(0.1)


async def _background_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str]):
    """
    [简化版] 使用 DirectTaskExecutor 一步完成：分析对话 + 判断执行方式 + 执行任务
    
    简化链条:
    - 旧: Analyzer(LLM#1) → Planner(LLM#2) → 子进程Processor(LLM#3) → MCP调用
    - 新: DirectTaskExecutor(LLM#1) → MCP调用

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
        await _do_analyze_and_plan(messages, lanlan_name)


async def _do_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str]):
    """Inner implementation, always called under analyze_lock."""
    try:
        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping analysis: analyzer disabled (master switch off)")
            return
        logger.info("[AgentAnalyze] background analyze start: lanlan=%s messages=%d flags=%s analyzer_enabled=%s",
                    lanlan_name, len(messages), Modules.agent_flags, Modules.analyzer_enabled)
        # testUserPlugin: log before analysis when user_plugin_enabled is true
        try:
            if Modules.agent_flags.get("user_plugin_enabled", False):
                logger.debug("testUserPlugin: Starting analyze_and_execute with user_plugin_enabled = True")
        except Exception:
            pass

        # 一步完成：分析 + 执行
        result = await Modules.task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name=lanlan_name,
            agent_flags=Modules.agent_flags
        )

        # testUserPlugin: log after analysis decision if user_plugin_enabled is true
        try:
            if Modules.agent_flags.get("user_plugin_enabled", False):
                logger.debug("testUserPlugin: analyze_and_execute completed, checking result for user plugin involvement")
                # If result indicates user_plugin execution or decision, log succinct info
                if result is None:
                    logger.debug("testUserPlugin: analyze_and_execute returned None (no task detected)")
                else:
                    # Attempt to surface if user_plugin was chosen or considered
                    try:
                        logger.debug(
                            "testUserPlugin: execution_method=%s, success=%s, tool_name=%s",
                            getattr(result, "execution_method", None),
                            getattr(result, "success", None),
                            getattr(result, "tool_name", None),
                        )
                    except Exception:
                        logger.debug("testUserPlugin: analyze_and_execute returned result but failed to introspect details")
        except Exception:
            pass

        if result is None:
            return
        
        if not result.has_task:
            logger.debug("[TaskExecutor] No actionable task found")
            return

        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping dispatch: analyzer disabled during analysis")
            return
        
        logger.info(f"[TaskExecutor] Task: {result.task_description}, method: {result.execution_method}")
        
        # 处理 MCP 任务（已在 DirectTaskExecutor 中执行完成）
        if result.execution_method == 'mcp':
            if result.success:
                # MCP 任务已成功执行，通知 main_server
                summary = f'你的任务"{result.task_description}"已完成'
                mcp_detail = ""
                if result.result:
                    try:
                        if isinstance(result.result, dict):
                            detail = result.result.get('content', [])
                            if detail and isinstance(detail, list):
                                text_parts = [item.get('text', '') for item in detail if isinstance(item, dict)]
                                mcp_detail = ' '.join(text_parts)
                                if mcp_detail:
                                    summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
                        elif isinstance(result.result, str):
                            mcp_detail = result.result
                            summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
                    except Exception:
                        pass
                
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="mcp",
                        task_id=str(getattr(result, "task_id", "") or ""),
                        success=True,
                        summary=summary,
                        detail=mcp_detail,
                    )
                    logger.info(f"[TaskExecutor] ✅ MCP task completed and notified: {result.task_description}")
                except Exception as e:
                    logger.warning(f"[TaskExecutor] Failed to notify main_server: {e}")
            else:
                logger.error(f"[TaskExecutor] ❌ MCP task failed: {result.error}")
        
        # 处理 ComputerUse 任务（需要通过子进程调度）
        elif result.execution_method == 'computer_use':
            if Modules.agent_flags.get("computer_use_enabled", False):
                # 检查重复
                dup, matched = await _is_duplicate_task(result.task_description, lanlan_name)
                if not dup:
                    # Session management for multi-turn CUA tasks
                    sm = get_session_manager()
                    cu_session = sm.get_or_create(None, "cua")
                    cu_session.add_task(result.task_description)

                    ti = _spawn_task("computer_use", {"instruction": result.task_description, "screenshot": None})
                    ti["lanlan_name"] = lanlan_name
                    ti["session_id"] = cu_session.session_id
                    logger.info(f"[ComputerUse] Scheduled task {ti['id']} (session={cu_session.session_id[:8]}): {result.task_description[:50]}...")
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
                                "session_id": cu_session.session_id,
                            },
                        )
                    except Exception:
                        pass
                else:
                    logger.info(f"[ComputerUse] Duplicate task detected, matched with {matched}")
            else:
                logger.warning("[ComputerUse] Task requires ComputerUse but it's disabled")
        elif result.execution_method == 'browser_use':
            if Modules.agent_flags.get("browser_use_enabled", False) and Modules.browser_use:
                sm = get_session_manager()
                bu_session = sm.get_or_create(None, "browser_use")
                bu_session.add_task(result.task_description)

                bu_task_id = str(uuid.uuid4())
                bu_start = _now_iso()
                bu_info = {
                    "id": bu_task_id,
                    "type": "browser_use",
                    "status": "running",
                    "start_time": bu_start,
                    "params": {"instruction": result.task_description},
                    "lanlan_name": lanlan_name,
                    "session_id": bu_session.session_id,
                    "result": None,
                    "error": None,
                }
                Modules.task_registry[bu_task_id] = bu_info
                Modules.active_browser_use_task_id = bu_task_id
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": bu_task_id, "status": "running", "type": "browser_use",
                              "start_time": bu_start, "params": {"instruction": result.task_description},
                              "session_id": bu_session.session_id},
                    )
                except Exception:
                    pass
                try:
                    bres = await Modules.browser_use.run_instruction(
                        result.task_description,
                        session_id=bu_session.session_id,
                    )
                    success = bres.get("success", False) if isinstance(bres, dict) else False
                    summary = f'你的任务"{result.task_description}"已完成' if success else f'你的任务"{result.task_description}"已结束（未完全成功）'
                    result_detail = ""
                    if isinstance(bres, dict):
                        result_detail = str(bres.get("result") or bres.get("message") or "")
                        if success:
                            summary = f'你的任务"{result.task_description}"已完成：{result_detail}' if result_detail else f'你的任务"{result.task_description}"已完成'
                        else:
                            summary = f'你的任务"{result.task_description}"已结束（未完全成功）：{result_detail}' if result_detail else f'你的任务"{result.task_description}"已结束（未完全成功）'
                    bu_session.complete_task(result_detail or summary, success)
                    bu_info["status"] = "completed" if success else "failed"
                    bu_info["result"] = bres
                    await _emit_task_result(
                        lanlan_name,
                        channel="browser_use",
                        task_id=bu_task_id,
                        success=success,
                        summary=summary,
                        detail=result_detail,
                    )
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": bu_task_id, "status": bu_info["status"],
                                  "type": "browser_use", "start_time": bu_start, "end_time": _now_iso(),
                                  "session_id": bu_session.session_id},
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"[BrowserUse] Failed: {e}")
                    bu_info["status"] = "failed"
                    bu_info["error"] = str(e)[:500]
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
                    except Exception:
                        pass
                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": bu_task_id, "status": "failed", "type": "browser_use",
                                  "start_time": bu_start, "end_time": _now_iso(),
                                  "error": str(e)[:500],
                                  "session_id": bu_session.session_id},
                        )
                    except Exception:
                        pass
                finally:
                    Modules.active_browser_use_task_id = None
            else:
                logger.warning("[BrowserUse] Task requires BrowserUse but it is disabled")
        
        else:
            logger.info(f"[TaskExecutor] No suitable execution method: {result.reason}")
    
    except Exception as e:
        logger.error(f"[TaskExecutor] Background task error: {e}", exc_info=True)

@app.on_event("startup")
async def startup():
    # 初始化新的合并执行器（推荐使用）
    Modules.computer_use = ComputerUseAdapter()
    Modules.browser_use = BrowserUseAdapter()
    Modules.task_executor = DirectTaskExecutor(computer_use=Modules.computer_use, browser_use=Modules.browser_use)
    Modules.deduper = TaskDeduper()
    
    # 保留 planner/analyzer 以支持能力探测与后台分析开关
    Modules.planner = TaskPlanner(computer_use=Modules.computer_use)
    Modules.analyzer = ConversationAnalyzer()
    _rewire_computer_use_dependents()
    # Both CUA and BrowserUse share the agent LLM — default to "not connected"
    # and probe in background.  The single check updates both capability caches.
    _set_capability("computer_use", False, "connectivity check pending")
    _set_capability("browser_use", False, "connectivity check pending")
    asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    
    try:
        async def _http_plugin_provider(force_refresh: bool = False):
            url = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"
            if force_refresh:
                url += "?refresh=true"
            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except Exception as parse_err:
                            logger.debug(f"[Agent] plugin_list_provider parse error: {parse_err}")
                            data = {}
                        return data.get("plugins", []) or []
            except Exception as e:
                logger.debug(f"[Agent] plugin_list_provider http fetch failed: {e}")
            return []

        # inject http-based provider so DirectTaskExecutor can pick up user_plugin_server plugins
        try:
            Modules.task_executor.set_plugin_list_provider(_http_plugin_provider)
            logger.info("[Agent] Registered http plugin_list_provider for task_executor")
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
    # Push initial server status so frontend can render Agent popup without waiting.
    _bump_state_revision()
    await _emit_agent_status_update()
    
    logger.info("[Agent] ✅ Agent server started with simplified task executor")


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
    新增接口：直接触发 plugin_entry。
    请求 body 可包含:
      - plugin_id: str (必需)
      - entry_id: str (可选)
      - args: dict (可选)
      - lanlan_name: str (可选，用于日志/通知)
    该接口将调用 Modules.task_executor.execute_user_plugin_direct 来执行插件触发。
    """
    if not Modules.task_executor:
        raise HTTPException(503, "Task executor not ready")
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
    if not plugin_id or not isinstance(plugin_id, str):
        raise HTTPException(400, "plugin_id required")

    # Dedup is not applied for direct plugin calls; client should dedupe if needed
    task_id = str(uuid.uuid4())
    # Log request
    logger.info(f"[Plugin] Direct execute request: plugin_id={plugin_id}, entry_id={entry_id}, lanlan={lanlan_name}")

    # Ensure task registry entry for tracking
    info = {
        "id": task_id,
        "type": "plugin_direct",
        "status": "running",
        "start_time": _now_iso(),
        "params": {"plugin_id": plugin_id, "entry_id": entry_id, "args": args},
        "lanlan_name": lanlan_name,
        "result": None,
        "error": None,
    }
    Modules.task_registry[task_id] = info

    # Execute via task_executor.execute_user_plugin_direct in background
    async def _run_plugin():
        try:
            res = await Modules.task_executor.execute_user_plugin_direct(
                task_id=task_id, plugin_id=plugin_id, plugin_args=args, entry_id=entry_id
            )
            info["result"] = res.result
            # _execute_user_plugin marks success=False for "accepted but not completed", so rely on accepted flag in result
            accepted = isinstance(res.result, dict) and res.result.get("accepted")
            info["status"] = "completed" if accepted else "failed"
            if not accepted and res.error:
                info["error"] = res.error
            # Only notify main server when actually accepted
            if accepted:
                try:
                    plugin_summary = f'插件任务 "{plugin_id}" 已接受'
                    await _emit_task_result(
                        lanlan_name,
                        channel="user_plugin",
                        task_id=task_id,
                        success=True,
                        summary=plugin_summary,
                    )
                except Exception:
                    pass
        except Exception as e:
            info["status"] = "failed"
            info["error"] = str(e)
            logger.error(f"[Plugin] Direct execute failed: {e}", exc_info=True)

    plugin_task = asyncio.create_task(_run_plugin())
    Modules._background_tasks.add(plugin_task)
    plugin_task.add_done_callback(Modules._background_tasks.discard)
    return {"success": True, "task_id": task_id, "status": info["status"], "start_time": info["start_time"]}



@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    # Look up both planner task pool and runtime tasks
    if Modules.planner and task_id in Modules.planner.task_pool:
        return Modules.planner.task_pool[task_id].__dict__
    info = Modules.task_registry.get(task_id)
    if info:
        out = {k: v for k, v in info.items() if k != "_proc"}
        return out
    raise HTTPException(404, "task not found")


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a specific running task."""
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(404, "task not found")
    if info.get("status") not in ("queued", "running"):
        return {"success": False, "error": "task is not active"}

    task_type = info.get("type")
    if task_type == "computer_use":
        if Modules.computer_use:
            Modules.computer_use.cancel_running()
        if Modules.active_computer_use_task_id == task_id and Modules.active_computer_use_async_task:
            Modules.active_computer_use_async_task.cancel()
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    elif task_type == "browser_use":
        if Modules.browser_use:
            Modules.browser_use.cancel_running()
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    else:
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"

    lanlan_name = info.get("lanlan_name")
    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={"id": task_id, "status": "cancelled", "type": task_type,
                  "end_time": _now_iso(), "error": "Cancelled by user"},
        )
    except Exception:
        pass
    logger.info("[Agent] Task %s (%s) cancelled by user", task_id, task_type)
    return {"success": True, "task_id": task_id, "status": "cancelled"}


@app.get("/capabilities")
async def capabilities():
    return {"success": True, "capabilities": {}}


@app.get("/agent/flags")
async def get_agent_flags():
    """获取当前 agent flags 状态（供前端同步）"""
    note = Modules.notification
    # Read-once notification
    if Modules.notification:
        Modules.notification = None
        
    return {
        "success": True, 
        "agent_flags": Modules.agent_flags,
        "analyzer_enabled": Modules.analyzer_enabled,
        "agent_api_gate": _check_agent_api_gate(),
        "revision": Modules.state_revision,
        "notification": note
    }


@app.get("/agent/state")
async def get_agent_state():
    snapshot = _collect_agent_status_snapshot()
    return {"success": True, "snapshot": snapshot}


@app.post("/agent/flags")
async def set_agent_flags(payload: Dict[str, Any]):
    lanlan_name = (payload or {}).get("lanlan_name")
    cf = (payload or {}).get("computer_use_enabled")
    bf = (payload or {}).get("browser_use_enabled")
    uf = (payload or {}).get("user_plugin_enabled")
    # Agent API gate: if any agent sub-feature is being enabled, gate must pass.
    gate = _check_agent_api_gate()
    changed = False
    old_flags = dict(Modules.agent_flags)
    old_analyzer_enabled = bool(Modules.analyzer_enabled)
    if gate.get("ready") is not True and any(x is True for x in (cf, bf, uf)):
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.agent_flags["browser_use_enabled"] = False
        Modules.agent_flags["user_plugin_enabled"] = False
        Modules.notification = f"无法开启 Agent: {(gate.get('reasons') or ['Agent API 未配置'])[0]}"
        if Modules.agent_flags != old_flags:
            _bump_state_revision()
            await _emit_agent_status_update(lanlan_name=lanlan_name)
        return {"success": True, "agent_flags": Modules.agent_flags}

    prev_up = Modules.agent_flags.get("user_plugin_enabled", False)

    # 1. Handle Computer Use Flag with Capability Check
    if isinstance(cf, bool):
        if cf: # Attempting to enable
            if not Modules.computer_use:
                _try_refresh_computer_use_adapter(force=True)
            if not Modules.computer_use:
                Modules.agent_flags["computer_use_enabled"] = False
                Modules.notification = "无法开启 Computer Use: 模块未加载"
                logger.warning("[Agent] Cannot enable Computer Use: Module not loaded")
            elif not getattr(Modules.computer_use, "init_ok", False):
                # Connectivity not yet confirmed — kick off a check then
                # optimistically enable; the check result will auto-disable
                # if it fails.
                Modules.agent_flags["computer_use_enabled"] = True
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                try:
                    avail = Modules.computer_use.is_available()
                    reasons = avail.get('reasons', []) if isinstance(avail, dict) else []
                    _set_capability("computer_use", bool(avail.get("ready")) if isinstance(avail, dict) else False, reasons[0] if reasons else "")
                    if avail.get("ready"):
                        Modules.agent_flags["computer_use_enabled"] = True
                    else:
                        Modules.agent_flags["computer_use_enabled"] = False
                        reason = avail.get('reasons', [])[0] if avail.get('reasons') else '未知原因'
                        Modules.notification = f"无法开启 Computer Use: {reason}"
                        logger.warning(f"[Agent] Cannot enable Computer Use: {avail.get('reasons')}")
                except Exception as e:
                    Modules.agent_flags["computer_use_enabled"] = False
                    Modules.notification = f"开启 Computer Use 失败: {str(e)}"
                    logger.error(f"[Agent] Cannot enable Computer Use: Check failed {e}")
        else: # Disabling
            Modules.agent_flags["computer_use_enabled"] = False

    # 2.5. Handle Browser Use Flag with Capability Check
    if isinstance(bf, bool):
        if bf:
            bu = getattr(Modules, "browser_use", None)
            if not bu:
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = "无法开启 Browser Use: 模块未加载"
            elif not getattr(bu, "_ready_import", False):
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = f"无法开启 Browser Use: browser-use not installed: {bu.last_error}"
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["browser_use_enabled"] = True
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                Modules.agent_flags["browser_use_enabled"] = True
                _set_capability("browser_use", True, "")
        else:
            Modules.agent_flags["browser_use_enabled"] = False
            
    if isinstance(uf, bool):
        if uf:  # Attempting to enable UserPlugin
            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    r = await client.get(f"http://localhost:{USER_PLUGIN_SERVER_PORT}/plugins")
                    if r.status_code != 200:
                        _set_capability("user_plugin", False, f"user_plugin server responded {r.status_code}")
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = "无法开启 UserPlugin: 插件服务不可用"
                        logger.warning("[Agent] Cannot enable UserPlugin: service unavailable")
                        return {"success": True, "agent_flags": Modules.agent_flags}
                    data = r.json()
                    plugins = data.get("plugins", []) if isinstance(data, dict) else []
                    if not plugins:
                        _set_capability("user_plugin", False, "未发现可用插件")
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = "无法开启 UserPlugin: 未发现可用插件"
                        logger.warning("[Agent] Cannot enable UserPlugin: no plugins found")
                        return {"success": True, "agent_flags": Modules.agent_flags}
            except Exception as e:
                _set_capability("user_plugin", False, str(e))
                Modules.agent_flags["user_plugin_enabled"] = False
                Modules.notification = f"开启 UserPlugin 失败: {str(e)}"
                logger.error(f"[Agent] Cannot enable UserPlugin: {e}")
                return {"success": True, "agent_flags": Modules.agent_flags}
        if uf:
            _set_capability("user_plugin", True, "")
        Modules.agent_flags["user_plugin_enabled"] = uf

    # testUserPlugin: log when user_plugin_enabled toggles
    try:
        new_up = Modules.agent_flags.get("user_plugin_enabled", False)
        if prev_up != new_up:
            if new_up:
                logger.info("testUserPlugin: user_plugin_enabled toggled ON via /agent/flags")
            else:
                logger.info("testUserPlugin: user_plugin_enabled toggled OFF via /agent/flags")
    except Exception:
        pass

    changed = Modules.agent_flags != old_flags or bool(Modules.analyzer_enabled) != old_analyzer_enabled
    if changed:
        _bump_state_revision()
    await _emit_agent_status_update(lanlan_name=lanlan_name)
    return {"success": True, "agent_flags": Modules.agent_flags}


@app.post("/agent/command")
async def agent_command(payload: Dict[str, Any]):
    t0 = time.perf_counter()
    request_id = (payload or {}).get("request_id") or str(uuid.uuid4())
    command = (payload or {}).get("command")
    lanlan_name = (payload or {}).get("lanlan_name")
    if command == "set_agent_enabled":
        enabled = bool((payload or {}).get("enabled"))
        if enabled:
            Modules.analyzer_enabled = True
            Modules.analyzer_profile = (payload or {}).get("profile", {}) or {}
        else:
            Modules.analyzer_enabled = False
            Modules.analyzer_profile = {}
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.agent_flags["browser_use_enabled"] = False
            Modules.agent_flags["user_plugin_enabled"] = False
            await admin_control({"action": "end_all"})
        _bump_state_revision()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {"success": True, "request_id": request_id, "timing": {"agent_total_ms": total_ms}}
    if command == "set_flag":
        key = (payload or {}).get("key")
        value = bool((payload or {}).get("value"))
        if key not in {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled"}:
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
            Modules.notification = "Computer Use 模块未加载，已自动关闭"
        raise HTTPException(503, "ComputerUse not ready")
    if not getattr(Modules.computer_use, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())

    status = Modules.computer_use.is_available()
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    _set_capability("computer_use", bool(status.get("ready")) if isinstance(status, dict) else False, reasons[0] if reasons else "")
    
    # Auto-update flag if capability lost
    if not status.get("ready") and Modules.agent_flags.get("computer_use_enabled"):
        logger.info("[Agent] Computer Use capability lost, disabling flag")
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.notification = f"Computer Use 不可用: {status.get('reasons', [])[0] if status.get('reasons') else '未知原因'}"
        
    return status


@app.post("/notify_config_changed")
async def notify_config_changed():
    """Called by the main server after API-key / model config is saved.
    Rebuilds the CUA adapter with fresh config and kicks off a non-blocking
    LLM connectivity check."""
    _try_refresh_computer_use_adapter(force=True)
    _rewire_computer_use_dependents()
    asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    return {"success": True, "message": "CUA adapter refreshed, connectivity check started"}


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
        avail = Modules.computer_use.is_available()
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
    try:
        result = await Modules.browser_use.run_instruction(instruction)
        return {"success": bool(result.get("success", False)), "result": result}
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@app.get("/mcp/availability")
async def mcp_availability():
    return {"ready": False, "capabilities_count": 0, "reasons": ["MCP 已移除"]}


@app.get("/tasks")
async def list_tasks():
    """快速返回当前所有任务状态，优化响应速度"""
    items = []
    
    try:
        # 添加运行时任务 (task_registry) - 只复制必要字段以提高速度
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
        
        # 添加规划器任务 (task_pool) - 只在planner存在时处理
        if Modules.planner and hasattr(Modules.planner, 'task_pool'):
            for tid, task in Modules.planner.task_pool.items():
                try:
                    if hasattr(task, '__dict__'):
                        task_dict = task.__dict__
                        task_item = {
                            "id": task_dict.get("id", tid),
                            "status": task_dict.get("status", "queued"),
                            "original_query": task_dict.get("original_query"),
                            "meta": task_dict.get("meta"),
                            "source": "planner"
                        }
                        items.append(task_item)
                except Exception:
                    continue
        
        # 简化调试信息
        debug_info = {
            "task_registry_count": len(Modules.task_registry),
            "task_pool_count": len(Modules.planner.task_pool) if (Modules.planner and hasattr(Modules.planner, 'task_pool')) else 0,
            "total_returned": len(items)
        }
        
        return {"tasks": items, "debug": debug_info}
    
    except Exception as e:
        # 即使出错也返回部分结果，避免完全失败（静默处理）
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
        # Cancel any in-flight background analyzer tasks
        tasks_to_await = []
        for t in list(Modules._background_tasks):
            if not t.done():
                t.cancel()
                tasks_to_await.append(t)
        if tasks_to_await:
            results = await asyncio.gather(*tasks_to_await, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    logger.warning(f"[Agent] Error awaiting cancelled background task: {res}")
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

        Modules.task_registry.clear()
        Modules.last_user_turn_fingerprint.clear()
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


if __name__ == "__main__":
    import uvicorn
    
    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_agent_server_filter
    
    # Add filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(create_agent_server_filter())
    
    uvicorn.run(app, host="127.0.0.1", port=TOOL_SERVER_PORT)
