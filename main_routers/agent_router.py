# -*- coding: utf-8 -*-
"""
Agent Router

Handles agent-related endpoints including:
- Agent flags
- Health checks
- Task status
- Admin control
"""

import logging

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
import httpx

from .shared_state import get_session_manager
from config import TOOL_SERVER_PORT

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger("Main")


# Key mappings: frontend ↔ internal
# Frontend uses: enable_cu, enable_mcp, enable_up
# Internal uses: computer_use_enabled, mcp_enabled, agent_enabled
FRONTEND_TO_INTERNAL = {
    'enable_cu': 'computer_use_enabled',
    'enable_mcp': 'mcp_enabled',
    'enable_up': 'agent_enabled',
    # Also accept internal keys directly for new clients
    'computer_use_enabled': 'computer_use_enabled',
    'mcp_enabled': 'mcp_enabled',
    'agent_enabled': 'agent_enabled',
}

INTERNAL_TO_FRONTEND = {
    'computer_use_enabled': 'enable_cu',
    'mcp_enabled': 'enable_mcp',
    'agent_enabled': 'enable_up',
}


@router.post('/flags')
async def update_agent_flags(request: Request):
    """来自前端的Agent开关更新，级联到各自的session manager。"""
    session_manager = get_session_manager()
    
    try:
        data = await request.json()
        
        # Map incoming keys to internal names
        mapped_flags = {}
        for key, value in data.items():
            if key in FRONTEND_TO_INTERNAL:
                internal_key = FRONTEND_TO_INTERNAL[key]
                mapped_flags[internal_key] = value
        
        # Update each session manager using the proper method
        for lanlan_name, mgr in session_manager.items():
            if hasattr(mgr, 'update_agent_flags'):
                mgr.update_agent_flags(mapped_flags)
            elif hasattr(mgr, 'agent_flags'):
                # Fallback for managers without update_agent_flags method
                for k, v in mapped_flags.items():
                    if isinstance(v, bool):
                        mgr.agent_flags[k] = v
        
        return {"success": True, "message": "Agent flags已更新"}
    except Exception as e:
        logger.error(f"更新Agent flags失败: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get('/flags')
async def get_agent_flags():
    """获取当前 agent flags 状态（供前端同步）"""
    session_manager = get_session_manager()
    
    # 返回第一个session manager的flags作为参考
    for lanlan_name, mgr in session_manager.items():
        if hasattr(mgr, 'agent_flags'):
            # Map internal keys to frontend-friendly names
            internal_flags = mgr.agent_flags
            frontend_flags = {}
            for internal_key, frontend_key in INTERNAL_TO_FRONTEND.items():
                if internal_key in internal_flags:
                    frontend_flags[frontend_key] = internal_flags[internal_key]
            return {
                "success": True,
                "flags": frontend_flags
            }
    
    return {
        "success": True,
        "flags": {
            "enable_cu": False,
            "enable_mcp": False,
            "enable_up": False
        }
    }


@router.get('/health')
async def agent_health():
    """Check tool_server health via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/health")
            return response.json()
    except Exception as e:
        return {"status": "offline", "error": str(e)}


@router.get('/computer_use/availability')
async def proxy_cu_availability():
    """Check Computer Use availability"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/cu/availability")
            return response.json()
    except Exception as e:
        return {"available": False, "error": str(e)}


@router.get('/mcp/availability')
async def proxy_mcp_availability():
    """Check MCP availability"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/mcp/availability")
            return response.json()
    except Exception as e:
        return {"available": False, "error": str(e)}


@router.get('/user_plugin/availability')
async def proxy_up_availability():
    """Check User Plugin availability"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/up/availability")
            return response.json()
    except Exception as e:
        return {"available": False, "error": str(e)}


@router.get('/tasks')
async def proxy_tasks():
    """Get all tasks from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            return response.json()
    except Exception as e:
        return {"tasks": [], "error": str(e)}


@router.get('/tasks/{task_id}')
async def proxy_task_detail(task_id: str):
    """Get specific task details from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/task/{task_id}")
            return response.json()
    except Exception as e:
        return {"error": str(e)}


@router.get('/task_status')
async def get_task_status():
    """Get current task status for frontend polling."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            data = response.json()
            return {"success": True, "tasks": data.get("tasks", [])}
    except Exception as e:
        return {"success": False, "tasks": [], "error": str(e)}


@router.post('/admin/control')
async def proxy_admin_control(payload: dict = Body(...)):
    """Proxy admin control commands to tool server."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"http://localhost:{TOOL_SERVER_PORT}/admin/control",
                json=payload
            )
            return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}
