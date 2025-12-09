from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from config import USER_PLUGIN_SERVER_PORT
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse

from plugin.server_base import state
from plugin.registry import (
    load_plugins_from_toml,
    get_plugins as registry_get_plugins,
)
from plugin.status import status_manager
from plugin.host import PluginProcessHost

app = FastAPI(title="N.E.K.O User Plugin Server")

logger = logging.getLogger("user_plugin_server")
# In-memory plugin registry (initially empty). Plugins are dicts with keys:
# { "id": str, "name": str, "description": str, "endpoint": str, "input_schema": dict }
# Registration endpoints are intentionally not implemented now.
# Where to look for plugin.toml files: ./plugins/<any>/plugin.toml
PLUGIN_CONFIG_ROOT = Path(__file__).parent / "plugins"
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

@app.get("/health")
async def health():
    return {"status": "ok", "time": _now_iso()}

@app.get("/available")
async def available():
    """Return availability and basic stats."""
    return {
        "status": "ok",
        "available": True,
        "plugins_count": len(state.plugins),
        "time": _now_iso()
    }

@app.get("/plugin/status")
async def plugin_status(plugin_id: Optional[str] = Query(default=None)):
    """
    查询插件运行状态：
    - GET /plugin/status                -> 所有插件状态
    - GET /plugin/status?plugin_id=xxx  -> 指定插件状态
    """
    try:
        if plugin_id:
            return {
                "plugin_id": plugin_id,
                "status": status_manager.get_plugin_status(plugin_id),
                "time": _now_iso(),
            }
        else:
            return {
                "plugins": status_manager.get_plugin_status(),  # {pid: status}
                "time": _now_iso(),
            }
    except Exception as e:
        logger.exception("Failed to get plugin status")
        raise HTTPException(status_code=500, detail=str(e)) from e
@app.get("/plugins")
async def list_plugins():
    """
    Return the list of known plugins.
    统一返回结构：
    {
        "plugins": [ ... ],
        "message": "..."
    }
    """
    try:
        result = []

        if state.plugins:
            logger.info("加载插件列表成功")
            # 已加载的插件（来自 TOML），直接返回
            for plugin_id, plugin_meta in state.plugins.items():
                plugin_info = plugin_meta.copy()  # Make a copy to modify
                plugin_info["entries"] = []
                # 处理每个 plugin 的 method，添加描述
                seen = set()  # 用于去重 (event_type, id)
                for key, eh in state.event_handlers.items():
                    if not (key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:plugin_entry:")):
                        continue
                    if getattr(eh.meta, "event_type", None) != "plugin_entry":
                        continue
                    # 去重判定键：优先使用 meta.id，再退回到 key
                    eid = getattr(eh.meta, "id", None) or key
                    dedup_key = (getattr(eh.meta, "event_type", "plugin_entry"), eid)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    # 安全获取各字段，避免缺属性时报错
                    returned_message = getattr(eh.meta, "return_message", "")
                    plugin_info["entries"].append({
                        "id": getattr(eh.meta, "id", eid),
                        "name": getattr(eh.meta, "name", ""),
                        "description": getattr(eh.meta, "description", ""),
                        "event_key": key,
                        "input_schema": getattr(eh.meta, "input_schema", {}),
                        "return_message": returned_message,
                    })
                result.append(plugin_info)

            logger.debug("Loaded plugins: %s", result)

            return {"plugins": result, "message": ""}

        else:
            logger.info("No plugins registered.")
            return {
                "plugins": [],
                "message": "no plugins registered"
            }

    except Exception as e:
        logger.exception("Failed to list plugins")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Utility to allow other parts of the application (same process) to query plugin list
def get_plugins() -> List[Dict[str, Any]]:
    """Return list of plugin dicts (in-process access)."""
    return registry_get_plugins()

# Utility to register a plugin programmatically (internal use only)
def _load_plugins_from_toml() -> None:
    """
    扫描插件配置，启动子进程，并静态扫描元数据用于注册列表。
    """
    def _factory(pid: str, entry: str, config_path: Path):
        return PluginProcessHost(plugin_id=pid, entry_point=entry, config_path=config_path)

    load_plugins_from_toml(PLUGIN_CONFIG_ROOT, logger, _factory)

@app.on_event("startup")
async def _startup_load_plugins():
    """
    服务启动时，从 TOML 配置加载插件。
    """
    _load_plugins_from_toml()
    logger.info("Plugin registry after startup: %s", list(state.plugins.keys()))
    # Startup diagnostics: list available plugin instances and their public methods to aid debugging
    try:
        if state.plugin_instances:
            logger.info(f"startup-diagnostics: plugin instances loaded: {list(state.plugin_instances.keys())}")
            for pid, pobj in list(state.plugin_instances.items()):
                try:
                    methods = [m for m in dir(pobj) if callable(getattr(pobj, m)) and not m.startswith('_')]
                except Exception:
                    methods = []
                logger.info(f"startup-diagnostics: instance '{pid}' methods: {methods}")
        else:
            logger.info("startup-diagnostics: no plugin instances loaded")
    except Exception:
        logger.exception("startup-diagnostics: failed to enumerate plugin instances")
        
@app.on_event("startup")
async def start_status_monitor():
    asyncio.create_task(status_manager.status_consumer())
    
# New endpoint: /plugin/trigger
# This endpoint is intended to be called by TaskExecutor (or other components) when a plugin should be triggered.
# Expected JSON body:
#   {
#       "plugin_id": "thePluginId",
#       "args": { ... }    # optional object with plugin-specific arguments
#   }
#
# Behavior:
# - Validate plugin_id presence
        # - Enqueue a standardized event into state.event_queue for inspection/processing
# - Return JSON response summarizing the accepted event

@app.on_event("shutdown")
async def shutdown_plugins():
    """在应用关闭时，优雅地关闭所有插件"""
    for plugin_host in state.plugin_hosts.values():
        plugin_host.shutdown(timeout=5.0)
    logger.info("All plugins have been gracefully shutdown.")

@app.post("/plugin/trigger")
async def plugin_trigger(payload: Dict[str, Any], request: Request):
    """
    触发指定插件的指定 entry（前端约定只会传以下结构）：
    {
        "task_id": "xxx",          # 可选
        "plugin_id": "tkWindow",   # 必填
        "entry_id": "open",        # 必填：要调用的插件 entry id
        "args": { ... }            # 可选：entry 需要的参数
    }
    """
    try:
        # --- 1. 基础校验 (保持不变) ---
        client_host = request.client.host if request.client else None

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")

        plugin_id = payload.get("plugin_id")
        if not plugin_id or not isinstance(plugin_id, str):
            raise HTTPException(status_code=400, detail="plugin_id (string) required")

        entry_id = payload.get("entry_id")
        if not entry_id or not isinstance(entry_id, str):
            raise HTTPException(status_code=400, detail="entry_id (string) required")

        args = payload.get("args") or {}
        if not isinstance(args, dict):
            raise HTTPException(status_code=400, detail="args must be an object")

        task_id = payload.get("task_id")

        logger.info(
            "[plugin_trigger] plugin_id=%s entry_id=%s task_id=%s args=%s",
            plugin_id, entry_id, task_id, args
        )

        # --- 2. 审计日志/事件队列 (保持不变) ---
        event = {
            "type": "plugin_triggered",
            "plugin_id": plugin_id,
            "entry_id": entry_id,
            "args": args,
            "task_id": task_id,
            "client": client_host,
            "received_at": _now_iso(),
        }
        try:
            if state.event_queue: # 简单判空防止未初始化报错
                state.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                state.event_queue.get_nowait()
                state.event_queue.put_nowait(event)
            except Exception:
                logger.debug("Event queue operation failed, event dropped")
        except Exception:
            # 队列报错不应影响主流程
            logger.debug("Event queue error, continuing without queueing")
        # --- 3. [核心修改] 使用 ProcessHost 进行跨进程调用 ---
        
        # 不再查找 _plugin_instances，而是查找进程宿主 _plugin_hosts
        host = state.plugin_hosts.get(plugin_id)
        if not host:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' is not running/loaded")

        plugin_response: Any = None
        plugin_error: Optional[Dict[str, Any]] = None

        try:
            # 调用宿主对象的 trigger 方法，它会负责将消息发送给子进程并等待结果
            # 注意：参数校验、反射调用、sync/async 兼容处理都在子进程里完成了
            plugin_response = await host.trigger(entry_id, args, timeout=30.0) 

        except TimeoutError:
             plugin_error = {"error": "Plugin execution timed out"}
             logger.error(f"Plugin {plugin_id} entry {entry_id} timed out")
        except Exception as e:
            # 这里的异常可能是 host.trigger 抛出的（子进程报错传回来的，或者通信错误）
            logger.exception(
                "plugin_trigger: error invoking plugin %s via IPC",
                plugin_id
            )
            plugin_error = {"error": str(e)}

        # --- 4. 构造响应 (保持原有格式兼容) ---
        resp: Dict[str, Any] = {
            "success": plugin_error is None,
            "plugin_id": plugin_id,
            "executed_entry": entry_id,
            "args": args,
            "plugin_response": plugin_response,
            "received_at": event["received_at"],
        }
        if plugin_error:
            resp["plugin_forward_error"] = plugin_error

        return JSONResponse(resp)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("plugin_trigger: unexpected error")
        raise HTTPException(status_code=500, detail=str(e)) from e
if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.DEBUG)
    host = "127.0.0.1"  # 默认只暴露本机喵
    uvicorn.run(app, host=host, port=USER_PLUGIN_SERVER_PORT)
