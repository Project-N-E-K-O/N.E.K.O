"""
日志路由
"""
from __future__ import annotations

from typing import NoReturn, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket

from plugin.logging_config import get_logger
from plugin.server.application.logs import LogQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import get_admin_code, require_admin
from plugin.server.logs import log_stream_endpoint

router = APIRouter()
logger = get_logger("server.routes.logs")
log_query_service = LogQueryService()


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/plugin/{plugin_id}/logs")
async def get_plugin_logs_endpoint(
    plugin_id: str,
    lines: int = Query(default=100, ge=1, le=10000),
    level: Optional[str] = Query(default=None, description="日志级别: DEBUG, INFO, WARNING, ERROR"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="关键词搜索"),
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return log_query_service.get_plugin_logs(
            plugin_id=plugin_id,
            lines=lines,
            level=level,
            start_time=start_time,
            end_time=end_time,
            search=search,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/{plugin_id}/logs/files")
async def get_plugin_log_files_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return log_query_service.get_plugin_log_files(plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.websocket("/ws/logs/{plugin_id}")
async def websocket_log_stream(websocket: WebSocket, plugin_id: str) -> None:
    code = websocket.query_params.get("code", "").upper()
    admin_code = get_admin_code()
    if not admin_code or code != admin_code:
        await websocket.close(code=1008, reason="Authentication required")
        return
    await log_stream_endpoint(websocket, plugin_id)
