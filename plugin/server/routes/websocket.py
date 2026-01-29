"""
WebSocket 路由
"""
from fastapi import APIRouter, WebSocket

from plugin.server.runs.websocket import ws_run_endpoint
from plugin.server.websocket.admin import ws_admin_endpoint

router = APIRouter()


@router.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await ws_run_endpoint(websocket)


@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    await ws_admin_endpoint(websocket)
