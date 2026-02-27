"""
日志路由
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket
from loguru import logger

from plugin._types.exceptions import PluginError
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.logs import get_plugin_logs, get_plugin_log_files, log_stream_endpoint
from plugin.server.infrastructure.utils import now_iso
from plugin.server.infrastructure.auth import require_admin, get_admin_code

router = APIRouter()


@router.get("/plugin/{plugin_id}/logs")
async def get_plugin_logs_endpoint(
    plugin_id: str,
    lines: int = Query(default=100, ge=1, le=10000),
    level: Optional[str] = Query(default=None, description="日志级别: DEBUG, INFO, WARNING, ERROR"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="关键词搜索"),
    _: str = require_admin
):
    try:
        result = get_plugin_logs(
            plugin_id=plugin_id,
            lines=lines,
            level=level,
            start_time=start_time,
            end_time=end_time,
            search=search
        )
        if "error" in result:
            logger.warning(f"Error getting logs for {plugin_id}: {result.get('error')}")
        return result
    except (PluginError, ValueError, AttributeError, OSError) as e:
        logger.warning(f"Failed to get logs for plugin {plugin_id}: {e}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to retrieve logs"
        }
    except Exception:
        logger.exception(f"Failed to get logs for plugin {plugin_id}: Unexpected error type")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to retrieve logs"
        }


@router.get("/plugin/{plugin_id}/logs/files")
async def get_plugin_log_files_endpoint(plugin_id: str, _: str = require_admin):
    try:
        files = get_plugin_log_files(plugin_id)
        return {
            "plugin_id": plugin_id,
            "log_files": files,
            "count": len(files),
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get log files for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get log files for plugin {plugin_id}: Unexpected error type")
        raise handle_plugin_error(e, f"Failed to get log files for plugin {plugin_id}", 500) from e


@router.websocket("/ws/logs/{plugin_id}")
async def websocket_log_stream(websocket: WebSocket, plugin_id: str):
    code = websocket.query_params.get("code", "").upper()
    admin_code = get_admin_code()
    
    if not admin_code or code != admin_code:
        await websocket.close(code=1008, reason="Authentication required")
        return
    
    await log_stream_endpoint(websocket, plugin_id)
