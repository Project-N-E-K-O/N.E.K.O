# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import uuid
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import multiprocessing as mp

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from config import TOOL_SERVER_PORT, MAIN_SERVER_PORT
from brain.processor import Processor
from brain.planner import TaskPlanner
from brain.analyzer import ConversationAnalyzer
from brain.computer_use import ComputerUseAdapter
from brain.deduper import TaskDeduper


app = FastAPI(title="N.E.K.O Tool Server")

# Configure logging
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Agent", log_level=logging.INFO)


class Modules:
    processor: Processor | None = None
    planner: TaskPlanner | None = None
    analyzer: ConversationAnalyzer | None = None
    computer_use: ComputerUseAdapter | None = None
    deduper: TaskDeduper | None = None
    # Task tracking
    task_registry: Dict[str, Dict[str, Any]] = {}
    result_queue: Optional[mp.Queue] = None
    poller_task: Optional[asyncio.Task] = None
    executor_reset_needed: bool = False
    analyzer_enabled: bool = False
    analyzer_profile: Dict[str, Any] = {}
    # Computer-use exclusivity and scheduling
    computer_use_queue: Optional[asyncio.Queue] = None
    computer_use_running: bool = False
    active_computer_use_task_id: Optional[str] = None
    # Agent feature flags (controlled by UI)
    agent_flags: Dict[str, Any] = {"mcp_enabled": False, "computer_use_enabled": False}
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
        return False, None


# ============ Workers (run in subprocess) ============
def _worker_processor(task_id: str, query: str, queue: mp.Queue):
    try:
        # Lazy import to avoid heavy init in parent
        from brain.processor import Processor as _Proc
        import asyncio as _aio
        proc = _Proc()
        
        # Log MCP processing start
        print(f"[MCP] Starting processor task {task_id} with query: {query[:100]}...")
        
        result = _aio.run(proc.process(query))
        
        # Log MCP processing result
        if result.get('can_execute'):
            server_id = result.get('server_id', 'unknown')
            reason = result.get('reason', 'no reason provided')
            tool_calls = result.get('tool_calls', [])
            tool_results = result.get('tool_results', [])
            
            if tool_calls:
                tools_info = ", ".join([f"'{tool}'" for tool in tool_calls])
                print(f"[MCP] ✅ Task {task_id} executed successfully using MCP server '{server_id}' with tools: {tools_info}")
                
                # Log tool execution results
                for tool_result in tool_results:
                    tool_name = tool_result.get('tool', 'unknown')
                    if tool_result.get('success'):
                        result_text = tool_result.get('result', 'No result')
                        print(f"[MCP] 🔧 Tool {tool_name} result: {result_text}")
                    else:
                        error_text = tool_result.get('error', 'Unknown error')
                        print(f"[MCP] ❌ Tool {tool_name} failed: {error_text}")
            else:
                print(f"[MCP] ✅ Task {task_id} executed successfully using MCP server '{server_id}' (no specific tools called)")
            
            print(f"[MCP]   Reason: {reason}")
        else:
            reason = result.get('reason', 'no reason provided')
            print(f"[MCP] ❌ Task {task_id} failed to execute: {reason}")
        
        queue.put({"task_id": task_id, "success": True, "result": result})
    except Exception as e:
        print(f"[MCP] 💥 Task {task_id} crashed with error: {str(e)}")
        queue.put({"task_id": task_id, "success": False, "error": str(e)})


def _worker_computer_use(task_id: str, instruction: str, screenshot: Optional[bytes], queue: mp.Queue):
    try:
        from brain.computer_use import ComputerUseAdapter as _CU
        cu = _CU()
        # Ensure exclusive run within this process; ComputerUseAdapter.run_instruction
        # is synchronous by design. We intentionally do not pass screenshot here
        # to match the adapter signature.
        res = cu.run_instruction(instruction)
        if res is None:
            res = {"success": True}
        elif isinstance(res, dict) and "success" not in res:
            res["success"] = True
        queue.put({"task_id": task_id, "success": bool(res.get("success", False)), "result": res})
    except Exception as e:
        queue.put({"task_id": task_id, "success": False, "error": str(e)})


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _spawn_task(kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(uuid.uuid4())
    info = {
        "id": task_id,
        "type": kind,
        "status": "running",
        "start_time": _now_iso(),
        "params": args,
        "result": None,
        "error": None,
    }
    # Ensure result queue exists lazily
    if Modules.result_queue is None:
        Modules.result_queue = mp.Queue()
    if kind == "processor":
        p = mp.Process(target=_worker_processor, args=(task_id, args.get("query", ""), Modules.result_queue))
        info["pid"] = None
        Modules.task_registry[task_id] = info
        p.daemon = True
        p.start()
        info["pid"] = p.pid
        info["_proc"] = p
        return info
    elif kind == "computer_use":
        # Queue the task for exclusive execution by the scheduler
        info["status"] = "queued"
        info["pid"] = None
        Modules.task_registry[task_id] = info
        if Modules.computer_use_queue is None:
            Modules.computer_use_queue = asyncio.Queue()
        # Put a minimal payload; scheduler will spawn the process
        Modules.computer_use_queue.put_nowait({
            "task_id": task_id,
            "instruction": args.get("instruction", ""),
            "screenshot": args.get("screenshot"),
        })
        return info
    else:
        raise ValueError(f"Unknown task kind: {kind}")


def _start_computer_use_process(task_info: Dict[str, Any]) -> None:
    """Spawn the actual computer-use worker process for a queued task."""
    task_id = task_info.get("task_id")
    instruction = task_info.get("instruction", "")
    screenshot = task_info.get("screenshot")
    if Modules.result_queue is None:
        Modules.result_queue = mp.Queue()
    p = mp.Process(target=_worker_computer_use, args=(task_id, instruction, screenshot, Modules.result_queue))
    p.daemon = True
    p.start()
    # Update registry entry
    info = Modules.task_registry.get(task_id, {})
    info["status"] = "running"
    info["pid"] = p.pid
    info["_proc"] = p
    Modules.task_registry[task_id] = info
    Modules.computer_use_running = True
    Modules.active_computer_use_task_id = task_id


async def _poll_results_loop():
    while True:
        await asyncio.sleep(0.1)
        try:
            if Modules.result_queue is None:
                continue
            while True:
                try:
                    msg = Modules.result_queue.get_nowait()
                except Exception:
                    break
                if not isinstance(msg, dict):
                    continue
                tid = msg.get("task_id")
                if not tid or tid not in Modules.task_registry:
                    continue
                info = Modules.task_registry[tid]
                info["status"] = "completed" if msg.get("success") else "failed"
                if "result" in msg:
                    info["result"] = msg["result"]
                if "error" in msg:
                    info["error"] = msg["error"]
                # If this was the active computer-use task, allow next to run
                if Modules.active_computer_use_task_id == tid:
                    Modules.computer_use_running = False
                    Modules.active_computer_use_task_id = None
                # Notify main server about completion so it can insert an extra reply next turn
                try:
                    import requests as _rq
                    summary = "任务已完成"
                    try:
                        # Build a compact result summary if possible
                        r = info.get("result")
                        if isinstance(r, dict):
                            detail = r.get("result") or r.get("message") or r.get("reason") or ""
                        else:
                            detail = str(r) if r is not None else ""
                        # Include task description if available
                        params = info.get("params") or {}
                        desc = params.get("query") or params.get("instruction") or ""
                        if detail and desc:
                            summary = f"你的任务“{desc}”已完成：{detail}"[:240]
                        elif detail:
                            summary = f"你的任务已完成：{detail}"[:240]
                        elif desc:
                            summary = f"你的任务“{desc}”已完成"[:240]
                    except Exception:
                        pass
                    _rq.post(
                        f"http://localhost:{MAIN_SERVER_PORT}/api/notify_task_result",
                        json={"text": summary, "lanlan_name": info.get("lanlan_name")},
                        timeout=0.5,
                    )
                except Exception:
                    pass
        except Exception:
            pass


async def _computer_use_scheduler_loop():
    """Ensure only one computer-use task runs at a time by scheduling queued tasks."""
    # Initialize queue if missing
    if Modules.computer_use_queue is None:
        Modules.computer_use_queue = asyncio.Queue()
    while True:
        try:
            await asyncio.sleep(0.05)
            # If a task is running, check if it finished (poller will clear flags)
            if Modules.computer_use_running:
                continue
            # No active task: try to dequeue next
            if Modules.computer_use_queue.empty():
                continue
            next_task = await Modules.computer_use_queue.get()
            # Validate registry presence
            tid = next_task.get("task_id")
            if not tid or tid not in Modules.task_registry:
                continue
            # Start the process for this queued task
            _start_computer_use_process(next_task)
        except Exception:
            # Never crash the scheduler
            await asyncio.sleep(0.1)


async def _background_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str]):
    """Run analyzer and planner in background and schedule executable work without blocking the request."""
    if not Modules.analyzer or not Modules.planner:
        return
    try:
        # Analyzer.analyze is now async, no need for executor
        analysis = await Modules.analyzer.analyze(messages)
    except Exception:
        return
    try:
        import uuid as _uuid
        tasks = analysis.get("tasks", []) if isinstance(analysis, dict) else []

        for q in tasks:
            try:
                # Do NOT register into task_pool before dedup/scheduling
                t = await Modules.planner.assess_and_plan(str(_uuid.uuid4()), q, register=False)
            except Exception:
                continue
            # Mirror /plan scheduling behavior
            try:
                # Attach lanlan context
                try:
                    t.meta["lanlan_name"] = lanlan_name
                except Exception:
                    pass
                if t.meta.get("mcp", {}).get("can_execute") and Modules.agent_flags.get("mcp_enabled", False):
                    for step in t.steps:
                        dup, matched = await _is_duplicate_task(step, lanlan_name)
                        if dup:
                            continue
                        ti = _spawn_task("processor", {"query": step})
                        ti["lanlan_name"] = lanlan_name
                else:
                    cu_dec = t.meta.get("computer_use_decision") or {}
                    if cu_dec.get("use_computer") and Modules.agent_flags.get("computer_use_enabled", False):
                        dup, matched = await _is_duplicate_task(t.original_query, lanlan_name)
                        if not dup:
                            ti = _spawn_task("computer_use", {"instruction": t.original_query, "screenshot": None})
                            ti["lanlan_name"] = lanlan_name
            except Exception:
                continue
    except Exception:
        return

@app.on_event("startup")
async def startup():
    Modules.processor = Processor()
    Modules.computer_use = ComputerUseAdapter()
    Modules.planner = TaskPlanner(computer_use=Modules.computer_use)
    Modules.analyzer = ConversationAnalyzer()
    Modules.deduper = TaskDeduper()
    # Warm up router discovery
    try:
        await Modules.planner.refresh_capabilities()
    except Exception:
        pass
    # Start result poller
    if Modules.poller_task is None:
        Modules.poller_task = asyncio.create_task(_poll_results_loop())
    # Start computer-use scheduler
    asyncio.create_task(_computer_use_scheduler_loop())


@app.get("/health")
async def health():
    return {"status": "ok", "agent_flags": Modules.agent_flags}


# 1) 处理器模块：接受自然语言query，交给MCP client处理
@app.post("/process")
async def process_query(payload: Dict[str, Any]):
    if not Modules.processor:
        raise HTTPException(503, "Processor not ready")
    query = (payload or {}).get("query", "").strip()
    if not query:
        raise HTTPException(400, "query required")
    lanlan_name = (payload or {}).get("lanlan_name")
    
    # Log MCP processing request
    logger.info(f"[MCP] Received process request from {lanlan_name}: {query[:100]}...")
    
    # Dedup check
    dup, matched = await _is_duplicate_task(query, lanlan_name)
    if dup:
        logger.info(f"[MCP] Duplicate task detected, matched with {matched}")
        return JSONResponse(content={"success": False, "duplicate": True, "matched_id": matched}, status_code=409)
    info = _spawn_task("processor", {"query": query})
    info["lanlan_name"] = lanlan_name
    
    logger.info(f"[MCP] Spawned processor task {info['id']} for {lanlan_name}")
    return {"success": True, "task_id": info["id"], "status": info["status"], "start_time": info["start_time"]}


# 2) 规划器模块：预载server能力，评估可执行性，入池并分解步骤
@app.post("/plan")
async def plan_task(payload: Dict[str, Any]):
    if not Modules.planner:
        raise HTTPException(503, "Planner not ready")
    query = (payload or {}).get("query", "").strip()
    task_id = (payload or {}).get("task_id") or str(uuid.uuid4())
    if not query:
        raise HTTPException(400, "query required")
    lanlan_name = (payload or {}).get("lanlan_name")
    
    # Log MCP planning request
    logger.info(f"[MCP] Received plan request from {lanlan_name} for task {task_id}: {query[:100]}...")
    
    # Dedup check against existing tasks
    dup, matched = await _is_duplicate_task(query, lanlan_name)
    if dup:
        logger.info(f"[MCP] Duplicate task detected, matched with {matched}")
        return JSONResponse(content={"success": False, "duplicate": True, "matched_id": matched}, status_code=409)
    # Do NOT register before dedup/scheduling
    task = await Modules.planner.assess_and_plan(task_id, query, register=False)
    try:
        task.meta["lanlan_name"] = lanlan_name
    except Exception:
        pass
    scheduled = []
    # If MCP plan executable → schedule steps as processor tasks
    if task.meta.get("mcp", {}).get("can_execute"):
        logger.info(f"[MCP] Task {task_id} will be executed by MCP with {len(task.steps)} steps")
        for step in task.steps:
            d2, m2 = await _is_duplicate_task(step, lanlan_name)
            if d2:
                scheduled.append({"duplicate": True, "matched_id": m2, "query": step})
                continue
            ti = _spawn_task("processor", {"query": step})
            ti["lanlan_name"] = lanlan_name
            scheduled.append({"task_id": ti["id"], "type": "processor", "start_time": ti["start_time"]})
            logger.info(f"[MCP] Scheduled processor task {ti['id']} for step: {step[:50]}...")
    else:
        # If computer use suggested → schedule one-shot
        cu_dec = task.meta.get("computer_use_decision") or {}
        if cu_dec.get("use_computer"):
            logger.info(f"[MCP] Task {task_id} will be executed by Computer Use")
            d3, m3 = await _is_duplicate_task(task.original_query, lanlan_name)
            if d3:
                scheduled.append({"duplicate": True, "matched_id": m3, "query": task.original_query})
            else:
                ti = _spawn_task("computer_use", {"instruction": task.original_query, "screenshot": None})
                ti["lanlan_name"] = lanlan_name
                scheduled.append({"task_id": ti["id"], "type": "computer_use", "start_time": ti["start_time"]})
        else:
            logger.info(f"[MCP] Task {task_id} cannot be executed by any available method")
    # Now safe to register this logical task into pool
    try:
        Modules.planner.task_pool[task.id] = task
    except Exception:
        pass
    return {"success": True, "task": task.__dict__, "scheduled": scheduled}


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


@app.get("/capabilities")
async def capabilities():
    if not Modules.planner:
        raise HTTPException(503, "Planner not ready")
    try:
        caps = await Modules.planner.refresh_capabilities()
        return {"success": True, "capabilities": caps}
    except Exception as e:
        return JSONResponse(content={"success": False, "capabilities": {}, "error": str(e)})


@app.post("/agent/flags")
async def set_agent_flags(payload: Dict[str, Any]):
    mf = (payload or {}).get("mcp_enabled")
    cf = (payload or {}).get("computer_use_enabled")
    if isinstance(mf, bool):
        Modules.agent_flags["mcp_enabled"] = mf
    if isinstance(cf, bool):
        Modules.agent_flags["computer_use_enabled"] = cf
    return {"success": True, "agent_flags": Modules.agent_flags}


# 3) 分析器模块：接收 cross-server 的对话片段，识别潜在任务，转发到规划器
@app.post("/analyze_and_plan")
async def analyze_and_plan(payload: Dict[str, Any]):
    if not Modules.analyzer or not Modules.planner:
        raise HTTPException(503, "Analyzer/Planner not ready")
    messages = (payload or {}).get("messages", [])
    if not isinstance(messages, list):
        raise HTTPException(400, "messages must be a list of {role, text}")
    # Fire-and-forget background processing and scheduling
    asyncio.create_task(_background_analyze_and_plan(messages, (payload or {}).get("lanlan_name")))
    return {"success": True, "status": "processed", "accepted_at": _now_iso()}


@app.get("/computer_use/availability")
async def computer_use_availability():
    if not Modules.computer_use:
        raise HTTPException(503, "ComputerUse not ready")
    return Modules.computer_use.is_available()


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


@app.get("/mcp/availability")
async def mcp_availability():
    if not Modules.planner:
        raise HTTPException(503, "Planner not ready")
    try:
        caps = await Modules.planner.refresh_capabilities()
        count = len(caps or {})
        ready = count > 0
        reasons = [] if ready else ["MCP router unreachable or no servers discovered"]
        
        # Log MCP availability check
        logger.info(f"[MCP] Availability check - Found {count} capabilities, ready: {ready}")
        for cap_id, cap_info in caps.items():
            logger.info(f"[MCP]   - {cap_id}: {cap_info.get('title', 'No title')} (status: {cap_info.get('status', 'unknown')})")
        
        return {"ready": ready, "capabilities_count": count, "reasons": reasons}
    except Exception as e:
        logger.error(f"[MCP] Availability check failed: {e}")
        return {"ready": False, "capabilities_count": 0, "reasons": [str(e)]}


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
        # terminate all running processes and clear registry
        for tid, info in list(Modules.task_registry.items()):
            p = info.get("_proc")
            try:
                if p is not None and p.is_alive():
                    p.terminate()
                    p.join(timeout=1.0)
            except Exception:
                pass
        Modules.task_registry.clear()
        # Clear scheduling state and queue
        Modules.computer_use_running = False
        Modules.active_computer_use_task_id = None
        try:
            if Modules.computer_use_queue is not None:
                while not Modules.computer_use_queue.empty():
                    await Modules.computer_use_queue.get()
        except Exception:
            pass
        # drain queue
        try:
            if Modules.result_queue is not None:
                while True:
                    Modules.result_queue.get_nowait()
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
    uvicorn.run(app, host="0.0.0.0", port=TOOL_SERVER_PORT)



