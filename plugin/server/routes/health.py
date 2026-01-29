"""
健康检查和基础路由
"""
import asyncio

from fastapi import APIRouter

from plugin.core.state import state
from plugin.server.infrastructure.utils import now_iso
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.executor import _api_executor
from plugin.sdk.version import SDK_VERSION

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "time": now_iso()}


@router.get("/available")
async def available():
    loop = asyncio.get_running_loop()
    
    def _get_count():
        with state.plugins_lock:
            return len(state.plugins)
    
    plugins_count = await loop.run_in_executor(_api_executor, _get_count)
    return {
        "status": "ok",
        "available": True,
        "plugins_count": plugins_count,
        "time": now_iso()
    }


@router.get("/server/info")
async def server_info(_: str = require_admin):
    loop = asyncio.get_running_loop()
    
    def _get_info():
        with state.plugins_lock:
            plugins_count = len(state.plugins)
            registered_plugins = list(state.plugins.keys())
        
        with state.plugin_hosts_lock:
            running_plugins_count = len(state.plugin_hosts)
            running_plugins = list(state.plugin_hosts.keys())
        running_plugins_status = {}
        for pid in running_plugins:
            host = state.plugin_hosts.get(pid)
            if host:
                running_plugins_status[pid] = {
                    "alive": True,
                    "pid": host.process.pid if hasattr(host, 'process') and host.process else None
                }
        
        return {
            "plugins_count": plugins_count,
            "registered_plugins": registered_plugins,
            "running_plugins_count": running_plugins_count,
            "running_plugins": running_plugins,
            "running_plugins_status": running_plugins_status,
        }
    
    info = await loop.run_in_executor(_api_executor, _get_info)
    info["sdk_version"] = SDK_VERSION
    info["time"] = now_iso()
    return info
