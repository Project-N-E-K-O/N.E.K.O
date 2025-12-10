from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from config import USER_PLUGIN_SERVER_PORT
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse

from plugin.server_base import state
from plugin.models import (
    PluginTriggerRequest,
    PluginTriggerResponse,
    PluginPushMessageRequest,
    PluginPushMessage,
    PluginPushMessageResponse,
)
from plugin.registry import (
    load_plugins_from_toml,
    get_plugins as registry_get_plugins,
)
from plugin.status import status_manager
from plugin.host import PluginProcessHost
from plugin.exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
    PluginQueueError,
)

app = FastAPI(title="N.E.K.O User Plugin Server")

logger = logging.getLogger("user_plugin_server")


# 异常处理中间件
@app.exception_handler(PluginError)
async def plugin_error_handler(request: Request, exc: PluginError):
    """统一处理插件系统异常"""
    logger.warning(f"Plugin error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Plugin error",
            "detail": str(exc),
            "type": exc.__class__.__name__
        }
    )


@app.exception_handler(PluginNotFoundError)
async def plugin_not_found_handler(request: Request, exc: PluginNotFoundError):
    """处理插件未找到异常"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Plugin not found",
            "detail": str(exc),
            "plugin_id": exc.plugin_id
        }
    )


@app.exception_handler(PluginNotRunningError)
async def plugin_not_running_handler(request: Request, exc: PluginNotRunningError):
    """处理插件未运行异常"""
    return JSONResponse(
        status_code=503,
        content={
            "error": "Plugin not running",
            "detail": str(exc),
            "plugin_id": exc.plugin_id,
            "status": exc.status
        }
    )


@app.exception_handler(PluginTimeoutError)
async def plugin_timeout_handler(request: Request, exc: PluginTimeoutError):
    """处理插件超时异常"""
    return JSONResponse(
        status_code=504,
        content={
            "error": "Plugin timeout",
            "detail": str(exc),
            "plugin_id": exc.plugin_id,
            "entry_id": exc.entry_id,
            "timeout": exc.timeout
        }
    )
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
                try:
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
                except (AttributeError, KeyError, TypeError) as e:
                    logger.warning(f"Error processing plugin {plugin_id} metadata: {e}", exc_info=True)
                    # 即使元数据有问题，也返回基本信息
                    result.append({
                        "id": plugin_id,
                        "name": plugin_meta.get("name", plugin_id),
                        "description": plugin_meta.get("description", ""),
                        "entries": [],
                    })

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
                except (AttributeError, TypeError) as e:
                    logger.debug(f"startup-diagnostics: failed to enumerate methods for {pid}: {e}")
                    methods = []
                logger.info(f"startup-diagnostics: instance '{pid}' methods: {methods}")
        else:
            logger.info("startup-diagnostics: no plugin instances loaded")
    except (AttributeError, KeyError) as e:
        logger.warning(f"startup-diagnostics: failed to enumerate plugin instances: {e}", exc_info=True)
    
    # 启动所有插件的通信资源管理器
    for plugin_id, host in state.plugin_hosts.items():
        try:
            await host.start(message_target_queue=state.message_queue)
            logger.debug(f"Started communication resources for plugin {plugin_id}")
        except Exception as e:
            logger.exception(f"Failed to start communication resources for plugin {plugin_id}: {e}")
    
    # 启动状态消费任务
    await status_manager.start_status_consumer(
        plugin_hosts_getter=lambda: state.plugin_hosts
    )
    logger.info("Status consumer started")
    
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
    logger.info("Shutting down all plugins...")
    
    # 关闭状态消费任务
    try:
        await status_manager.shutdown_status_consumer(timeout=5.0)
    except Exception as e:
        logger.exception("Error shutting down status consumer: {e}")
    
    # 关闭所有插件的资源
    shutdown_tasks = []
    for plugin_id, host in state.plugin_hosts.items():
        shutdown_tasks.append(host.shutdown(timeout=5.0))
    
    # 并发关闭所有插件
    if shutdown_tasks:
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    
    logger.info("All plugins have been gracefully shutdown.")

@app.post("/plugin/trigger", response_model=PluginTriggerResponse)
async def plugin_trigger(payload: PluginTriggerRequest, request: Request):
    """
    触发指定插件的指定 entry
    """
    try:
        client_host = request.client.host if request.client else None
        plugin_id = payload.plugin_id
        entry_id = payload.entry_id
        args = payload.args
        task_id = payload.task_id

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
                logger.debug("Event queue was full, dropped oldest event")
            except (asyncio.QueueEmpty, AttributeError) as e:
                logger.warning(f"Event queue operation failed after queue full: {e}")
        except (AttributeError, RuntimeError) as e:
            # 队列报错不应影响主流程，但需要记录
            logger.warning(f"Event queue error, continuing without queueing: {e}")
        # --- 3. [核心修改] 使用 ProcessHost 进行跨进程调用 ---
        
        # 不再查找 _plugin_instances，而是查找进程宿主 _plugin_hosts
        host = state.plugin_hosts.get(plugin_id)
        if not host:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' is not running/loaded"
            )

        # 检查进程健康状态
        try:
            health = host.health_check()
            if not health.alive:
                raise HTTPException(
                    status_code=503,
                    detail=f"Plugin '{plugin_id}' process is not alive (status: {health.status})"
                )
        except (AttributeError, RuntimeError) as e:
            logger.error(f"Failed to check health for plugin {plugin_id}: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Plugin '{plugin_id}' health check failed"
            )

        plugin_response: Any = None
        plugin_error: Optional[Dict[str, Any]] = None

        try:
            # 调用宿主对象的 trigger 方法，它会负责将消息发送给子进程并等待结果
            # 注意：参数校验、反射调用、sync/async 兼容处理都在子进程里完成了
            plugin_response = await host.trigger(entry_id, args, timeout=30.0) 

        except TimeoutError as e:
            plugin_error = {"error": "Plugin execution timed out"}
            logger.error(f"Plugin {plugin_id} entry {entry_id} timed out: {e}")
        except PluginError as e:
            # 插件系统已知异常，直接使用
            logger.warning(f"Plugin {plugin_id} entry {entry_id} error: {e}")
            plugin_error = {"error": str(e)}
        except (ConnectionError, OSError) as e:
            # 进程间通信错误
            logger.error(f"Communication error with plugin {plugin_id}: {e}")
            plugin_error = {"error": f"Communication error: {str(e)}"}
        except Exception as e:
            # 未知异常，记录详细信息
            logger.exception(
                "plugin_trigger: unexpected error invoking plugin %s via IPC",
                plugin_id
            )
            plugin_error = {"error": f"Unexpected error: {str(e)}"}

        # --- 4. 构造响应 ---
        resp = PluginTriggerResponse(
            success=plugin_error is None,
            plugin_id=plugin_id,
            executed_entry=entry_id,
            args=args,
            plugin_response=plugin_response,
            received_at=event["received_at"],
            plugin_forward_error=plugin_error,
        )

        return resp

    except HTTPException:
        raise
    except PluginError as e:
        logger.error(f"plugin_trigger: plugin error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"plugin_trigger: invalid request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid request: {str(e)}") from e
    except Exception as e:
        logger.exception("plugin_trigger: unexpected error")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@app.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=100, ge=1, le=1000),
    priority_min: Optional[int] = Query(default=None, description="最低优先级（包含）"),
):
    """
    获取插件推送的消息队列
    
    - GET /plugin/messages                    -> 获取所有插件的消息
    - GET /plugin/messages?plugin_id=xxx       -> 获取指定插件的消息
    - GET /plugin/messages?max_count=50        -> 限制返回数量
    - GET /plugin/messages?priority_min=5      -> 只返回优先级>=5的消息
    """
    try:
        messages = []
        count = 0
        
        # 从消息队列中获取消息（非阻塞）
        while count < max_count:
            try:
                msg = state.message_queue.get_nowait()
                
                # 过滤插件ID
                if plugin_id and msg.get("plugin_id") != plugin_id:
                    continue
                
                # 过滤优先级
                if priority_min is not None:
                    msg_priority = msg.get("priority", 0)
                    if msg_priority < priority_min:
                        continue
                
                # 构造完整的消息对象
                message_id = str(uuid.uuid4())
                plugin_message = PluginPushMessage(
                    plugin_id=msg.get("plugin_id", ""),
                    source=msg.get("source", ""),
                    description=msg.get("description", ""),
                    priority=msg.get("priority", 0),
                    message_type=msg.get("message_type", "text"),
                    content=msg.get("content"),
                    binary_data=msg.get("binary_data"),
                    binary_url=msg.get("binary_url"),
                    metadata=msg.get("metadata", {}),
                    timestamp=msg.get("time", _now_iso()),
                    message_id=message_id,
                )
                messages.append(plugin_message.model_dump())
                count += 1
            except asyncio.QueueEmpty:
                break
        
        return {
            "messages": messages,
            "count": len(messages),
            "time": _now_iso(),
        }
    except Exception as e:
        logger.exception("Failed to get plugin messages")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/plugin/push", response_model=PluginPushMessageResponse)
async def plugin_push_message(payload: PluginPushMessageRequest, request: Request):
    """
    接收插件推送的消息（HTTP端点，主要用于外部调用或测试）
    
    注意：插件通常通过进程间通信直接推送，此端点作为备用。
    """
    try:
        client_host = request.client.host if request.client else None
        
        # 验证插件是否存在
        if payload.plugin_id not in state.plugins:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{payload.plugin_id}' is not registered"
            )
        
        # 构造消息
        message_id = str(uuid.uuid4())
        message = {
            "type": "MESSAGE_PUSH",
            "plugin_id": payload.plugin_id,
            "source": payload.source,
            "description": payload.description,
            "priority": payload.priority,
            "message_type": payload.message_type,
            "content": payload.content,
            "binary_data": payload.binary_data,
            "binary_url": payload.binary_url,
            "metadata": payload.metadata,
            "time": _now_iso(),
            "client": client_host,
        }
        
        # 将消息放入队列
        try:
            state.message_queue.put_nowait(message)
            logger.info(
                f"[plugin_push] plugin_id={payload.plugin_id} source={payload.source} "
                f"type={payload.message_type} priority={payload.priority}"
            )
        except asyncio.QueueFull:
            # 队列满时，尝试移除最旧的消息
            try:
                state.message_queue.get_nowait()
                state.message_queue.put_nowait(message)
                logger.warning(f"Message queue full, dropped oldest message")
            except (asyncio.QueueEmpty, AttributeError, RuntimeError) as e:
                logger.error(f"Failed to enqueue message, queue full and cleanup failed: {e}")
                raise HTTPException(
                    status_code=503,
                    detail="Message queue is full, please try again later"
                )
        except (AttributeError, RuntimeError) as e:
            logger.error(f"Message queue error: {e}")
            raise HTTPException(
                status_code=503,
                detail="Message queue is not available"
            )
        
        return PluginPushMessageResponse(
            success=True,
            message_id=message_id,
            received_at=message["time"],
        )
        
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"plugin_push: invalid request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid request: {str(e)}") from e
    except Exception as e:
        logger.exception("plugin_push: unexpected error")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.DEBUG)
    host = "127.0.0.1"  # 默认只暴露本机喵
    uvicorn.run(app, host=host, port=USER_PLUGIN_SERVER_PORT)
