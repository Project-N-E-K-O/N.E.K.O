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
        # 使用缓存快照避免锁竞争
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        return len(plugins_snapshot)
    
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
        # 使用缓存快照避免锁竞争
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
        
        plugins_count = len(plugins_snapshot)
        registered_plugins = list(plugins_snapshot.keys())
        running_plugins_count = len(hosts_snapshot)
        running_plugins = list(hosts_snapshot.keys())
        
        running_plugins_status = {}
        for pid, host in hosts_snapshot.items():
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
