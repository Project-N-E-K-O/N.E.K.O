# -*- coding: utf-8 -*-
"""
Agent Router

Handles agent-related endpoints including:
- Agent flags
- Health checks
- Task status
- Admin control
"""

import time

from utils.logger_config import get_module_logger
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
import httpx
from .shared_state import get_session_manager, get_config_manager
from config import TOOL_SERVER_PORT, USER_PLUGIN_SERVER_PORT
from main_logic.agent_event_bus import publish_session_event

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = get_module_logger(__name__, "Main")
TOOL_SERVER_BASE = f"http://127.0.0.1:{TOOL_SERVER_PORT}"
USER_PLUGIN_BASE = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"
_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(2.5, connect=0.5),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
    return _HTTP_CLIENT


@router.on_event("shutdown")
async def _close_http_client():
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


@router.post('/flags')
async def update_agent_flags(request: Request):
    """来自前端的Agent开关更新，级联到各自的session manager。"""
    try:
        data = await request.json()
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        flags = data.get('flags') or {}
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # Update core flags first
        mgr.update_agent_flags(flags)
        # Forward to tool server for Computer-Use/Browser-Use/Plugin flags
        try:
            forward_payload = {}
            if lanlan:
                forward_payload['lanlan_name'] = lanlan
            if 'computer_use_enabled' in flags:
                forward_payload['computer_use_enabled'] = bool(flags['computer_use_enabled'])
            if 'browser_use_enabled' in flags:
                forward_payload['browser_use_enabled'] = bool(flags['browser_use_enabled'])
            # Forward user_plugin_enabled as well so agent_server receives UI toggles
            if 'user_plugin_enabled' in flags:
                forward_payload['user_plugin_enabled'] = bool(flags['user_plugin_enabled'])
            if forward_payload:
                client = _get_http_client()
                r = await client.post(f"{TOOL_SERVER_BASE}/agent/flags", json=forward_payload, timeout=0.7)
                if not r.is_success:
                    raise Exception(f"tool_server responded {r.status_code}")
        except Exception as e:
            # On failure, reset flags in core to safe state (include user_plugin flag)
            mgr.update_agent_flags({'agent_enabled': False, 'computer_use_enabled': False, 'browser_use_enabled': False, 'user_plugin_enabled': False})
            return JSONResponse({"success": False, "error": f"tool_server forward failed: {e}"}, status_code=502)
        return {"success": True, "is_free_version": _config_manager.is_free_version()}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)



@router.get('/flags')
async def get_agent_flags():
    """获取当前 agent flags 状态（供前端同步）"""
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/agent/flags", timeout=0.7)
        if not r.is_success:
            return JSONResponse({"success": False, "error": "tool_server down"}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=502)


@router.get('/state')
async def get_agent_state():
    """获取 Agent 的权威状态快照（revision + flags + capabilities）。"""
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/agent/state", timeout=1.2)
        if not r.is_success:
            return JSONResponse({"success": False, "error": "tool_server down"}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=502)


@router.post('/command')
async def post_agent_command(request: Request):
    """统一命令入口，前端只发送 command，不直接操作多路开关。"""
    t0 = time.perf_counter()
    try:
        data = await request.json()
        request_id = data.get("request_id")
        command = data.get("command")
        lanlan = data.get("lanlan_name")
        session_manager = get_session_manager()
        cfg = get_config_manager()
        if not lanlan:
            try:
                _, her_name_current, _, _, _, _, _, _, _, _ = cfg.get_character_data()
                lanlan = her_name_current
                data["lanlan_name"] = lanlan
            except Exception:
                lanlan = None
        mgr = session_manager.get(lanlan) if lanlan else None
        old_flags = dict(getattr(mgr, "agent_flags", {}) or {}) if mgr else None

        # Keep main_server core flags in sync with command path.
        if mgr and command == "set_agent_enabled":
            enabled = bool(data.get("enabled"))
            if enabled:
                mgr.update_agent_flags({"agent_enabled": True})
            else:
                mgr.update_agent_flags({
                    "agent_enabled": False,
                    "computer_use_enabled": False,
                    "browser_use_enabled": False,
                    "user_plugin_enabled": False,
                })
        elif mgr and command == "set_flag":
            key = data.get("key")
            if key in {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled"}:
                mgr.update_agent_flags({key: bool(data.get("value"))})

        t_proxy = time.perf_counter()
        client = _get_http_client()
        r = await client.post(f"{TOOL_SERVER_BASE}/agent/command", json=data, timeout=1.5)
        proxy_ms = round((time.perf_counter() - t_proxy) * 1000, 2)
        if not r.is_success:
            # Rollback local state on upstream failure.
            if mgr and old_flags is not None:
                mgr.update_agent_flags(old_flags)
            logger.warning("[MainAgentTiming] request_id=%s upstream_status=%s proxy_ms=%s", request_id, r.status_code, proxy_ms)
            return JSONResponse({"success": False, "error": f"tool_server responded {r.status_code}"}, status_code=502)
        payload = r.json()
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[MainAgentTiming] request_id=%s proxy_ms=%s total_ms=%s", request_id or payload.get("request_id"), proxy_ms, total_ms)
        if isinstance(payload, dict):
            timing = payload.get("timing") or {}
            timing["main_proxy_ms"] = proxy_ms
            timing["main_total_ms"] = total_ms
            payload["timing"] = timing
            if command == "set_agent_enabled" and bool(data.get("enabled")):
                payload["is_free_version"] = cfg.is_free_version()
        return payload
    except Exception as e:
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.warning("[MainAgentTiming] proxy_exception total_ms=%s error=%s", total_ms, e)
        return JSONResponse({"success": False, "error": str(e)}, status_code=502)


@router.post('/internal/analyze_request')
async def post_internal_analyze_request(request: Request):
    """Internal bridge: accept analyze_request from child process and publish via main EventBus."""
    try:
        data = await request.json()
        event = {
            "event_type": "analyze_request",
            "trigger": data.get("trigger") or "turn_end",
            "lanlan_name": data.get("lanlan_name"),
            "messages": data.get("messages") or [],
        }
        sent = await publish_session_event(event)
        return {"success": bool(sent)}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)




@router.get('/health')
async def agent_health():
    """Check tool_server health via main_server proxy."""
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/health", timeout=0.7)
        if not r.is_success:
            return JSONResponse({"status": "down"}, status_code=502)
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        return {"status": "ok", **({"tool": data} if isinstance(data, dict) else {})}
    except Exception:
        return JSONResponse({"status": "down"}, status_code=502)



@router.get('/computer_use/availability')
async def proxy_cu_availability():
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/computer_use/availability", timeout=1.5)
        if not r.is_success:
            return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)



@router.get('/mcp/availability')
async def proxy_mcp_availability():
    return {"ready": False, "capabilities_count": 0, "reasons": ["MCP 已移除"]}


@router.get('/user_plugin/availability')
async def proxy_up_availability():
    try:
        client = _get_http_client()
        r = await client.get(f"{USER_PLUGIN_BASE}/available", timeout=1.5)
        if r.is_success:
            return JSONResponse({"ready": True, "reasons": ["user_plugin server reachable"]}, status_code=200)
        else:
            return JSONResponse({"ready": False, "reasons": [f"user_plugin server responded {r.status_code}"]}, status_code=502)
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)


@router.get('/browser_use/availability')
async def proxy_browser_availability():
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/browser_use/availability", timeout=1.5)
        if not r.is_success:
            return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)



@router.get('/tasks')
async def proxy_tasks():
    """Get all tasks from tool server via main_server proxy."""
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/tasks", timeout=2.5)
        if not r.is_success:
            return JSONResponse({"tasks": [], "error": f"tool_server responded {r.status_code}"}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"tasks": [], "error": f"proxy error: {e}"}, status_code=502)



@router.get('/tasks/{task_id}')
async def proxy_task_detail(task_id: str):
    """Get specific task details from tool server via main_server proxy."""
    try:
        client = _get_http_client()
        r = await client.get(f"{TOOL_SERVER_BASE}/tasks/{task_id}", timeout=1.5)
        if not r.is_success:
            return JSONResponse({"error": f"tool_server responded {r.status_code}"}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"error": f"proxy error: {e}"}, status_code=502)


@router.post('/tasks/{task_id}/cancel')
async def proxy_task_cancel(task_id: str):
    """Cancel a specific task via tool server proxy."""
    try:
        client = _get_http_client()
        r = await client.post(f"{TOOL_SERVER_BASE}/tasks/{task_id}/cancel", timeout=5.0)
        if not r.is_success:
            return JSONResponse({"success": False, "error": f"tool_server responded {r.status_code}"}, status_code=502)
        return r.json()
    except Exception as e:
        return JSONResponse({"success": False, "error": f"proxy error: {e}"}, status_code=502)


@router.post('/admin/control')
async def proxy_admin_control(payload: dict = Body(...)):
    """Proxy admin control commands to tool server."""
    try:
        client = _get_http_client()
        r = await client.post(f"{TOOL_SERVER_BASE}/admin/control", json=payload, timeout=5.0)
        if not r.is_success:
            return JSONResponse({"success": False, "error": f"tool_server responded {r.status_code}"}, status_code=502)
        
        result = r.json()
        logger.info(f"Admin control result: {result}")
        return result
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"Failed to execute admin control: {str(e)}"
        }, status_code=500)


