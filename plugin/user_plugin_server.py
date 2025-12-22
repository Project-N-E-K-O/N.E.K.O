"""
User Plugin Server

HTTP 服务器主文件，定义所有路由端点。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query, Body, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from config import USER_PLUGIN_SERVER_PORT

# 配置服务器日志
from utils.logger_config import setup_logging
server_logger, server_log_config = setup_logging(service_name="PluginServer", log_level="INFO")

from plugin.core.state import state
from plugin.api.models import (
    PluginTriggerRequest,
    PluginTriggerResponse,
    PluginPushMessageRequest,
    PluginPushMessageResponse,
)
from plugin.runtime.registry import get_plugins as registry_get_plugins
from plugin.runtime.status import status_manager
from plugin.server.exceptions import register_exception_handlers
from plugin.server.services import (
    build_plugin_list,
    trigger_plugin,
    get_messages_from_queue,
    push_message_to_queue,
)
from plugin.server.lifecycle import startup, shutdown
from plugin.server.utils import now_iso
from plugin.server.management import start_plugin, stop_plugin, reload_plugin
from plugin.server.logs import get_plugin_logs, get_plugin_log_files, log_stream_endpoint
from plugin.server.config_service import load_plugin_config, update_plugin_config
from plugin.server.metrics_service import metrics_collector
from plugin.server.auth import require_admin
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    await startup()
    yield
    await shutdown()


app = FastAPI(title="N.E.K.O User Plugin Server", lifespan=lifespan)
# 使用配置好的服务器 logger
logger = server_logger

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite 开发服务器
        "http://127.0.0.1:5173",
        "http://localhost:48911",  # 主服务器（如果需要）
        "http://127.0.0.1:48911",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册异常处理中间件
register_exception_handlers(app)


# ========== 基础路由 ==========

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "time": now_iso()}


@app.get("/available")
async def available():
    """返回可用性和基本统计"""
    with state.plugins_lock:
        plugins_count = len(state.plugins)
    return {
        "status": "ok",
        "available": True,
        "plugins_count": plugins_count,
        "time": now_iso()
    }


@app.get("/server/info")
async def server_info(_: str = require_admin):
    """
    返回服务器信息，包括SDK版本
    
    - 需要管理员验证码（Bearer Token）
    """
    from plugin.sdk.version import SDK_VERSION
    
    with state.plugins_lock:
        plugins_count = len(state.plugins)
        registered_plugins = list(state.plugins.keys())
    
    with state.plugin_hosts_lock:
        running_plugins_count = len(state.plugin_hosts)
        running_plugins = list(state.plugin_hosts.keys())
        # 检查每个运行插件的进程状态
        running_details = {}
        for pid, host in state.plugin_hosts.items():
            if hasattr(host, 'process') and host.process:
                running_details[pid] = {
                    "pid": host.process.pid,
                    "alive": host.process.is_alive(),
                    "exitcode": host.process.exitcode
                }
            else:
                running_details[pid] = {"error": "No process object"}
    
    return {
        "sdk_version": SDK_VERSION,
        "plugins_count": plugins_count,
        "registered_plugins": registered_plugins,
        "running_plugins_count": running_plugins_count,
        "running_plugins": running_plugins,
        "running_details": running_details,
        "time": now_iso()
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
                "time": now_iso(),
            }
        else:
            return {
                "plugins": status_manager.get_plugin_status(),
                "time": now_iso(),
            }
    except Exception as e:
        logger.exception("Failed to get plugin status")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 插件管理路由 ==========

@app.get("/plugins")
async def list_plugins():
    """
    返回已知插件列表
    
    统一返回结构：
    {
        "plugins": [ ... ],
        "message": "..."
    }
    """
    try:
        plugins = build_plugin_list()
        
        if plugins:
            return {"plugins": plugins, "message": ""}
        else:
            logger.info("No plugins registered.")
            return {
                "plugins": [],
                "message": "no plugins registered"
            }
    except Exception as e:
        logger.exception("Failed to list plugins")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/plugin/trigger", response_model=PluginTriggerResponse)
async def plugin_trigger(payload: PluginTriggerRequest, request: Request):
    """
    触发指定插件的指定 entry
    """
    try:
        client_host = request.client.host if request.client else None
        
        # 关键日志：记录接收到的请求
        logger.info(
            "[plugin_trigger] Received trigger request: plugin_id=%s, entry_id=%s, task_id=%s",
            payload.plugin_id,
            payload.entry_id,
            payload.task_id,
        )
        # 详细参数信息使用 DEBUG（脱敏处理，避免泄露敏感数据）
        safe_args = payload.args
        if isinstance(safe_args, dict):
            # 脱敏敏感字段
            redacted = {}
            sensitive_keys = {"api_key", "apikey", "token", "authorization", "cookie", "password", "secret", "credential"}
            for k, v in safe_args.items():
                if k.lower() in sensitive_keys or any(sensitive in k.lower() for sensitive in sensitive_keys):
                    redacted[k] = "***REDACTED***"
                else:
                    # 对于非敏感字段，如果是字符串且过长则截断
                    if isinstance(v, str) and len(v) > 100:
                        redacted[k] = v[:100] + "...(truncated)"
                    else:
                        redacted[k] = v
            safe_args = redacted
        
        # 截断整个输出，避免日志爆炸
        args_preview = str(safe_args)
        if len(args_preview) > 500:
            args_preview = args_preview[:500] + "...(truncated)"
        
        logger.debug(
            "[plugin_trigger] Request args: type=%s, keys=%s, preview=%s",
            type(payload.args),
            list(payload.args.keys()) if isinstance(payload.args, dict) else "N/A",
            args_preview,
        )
        
        return await trigger_plugin(
            plugin_id=payload.plugin_id,
            entry_id=payload.entry_id,
            args=payload.args,
            task_id=payload.task_id,
            client_host=client_host,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("plugin_trigger: unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 消息路由 ==========

@app.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=MESSAGE_QUEUE_DEFAULT_MAX_COUNT, ge=1, le=1000),
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
        messages = get_messages_from_queue(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
        )
        
        return {
            "messages": messages,
            "count": len(messages),
            "time": now_iso(),
        }
    except Exception as e:
        logger.exception("Failed to get plugin messages")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/plugin/push", response_model=PluginPushMessageResponse)
async def plugin_push_message(payload: PluginPushMessageRequest):
    """
    接收插件推送的消息（HTTP端点，主要用于外部调用或测试）
    
    注意：插件通常通过进程间通信直接推送，此端点作为备用。
    """
    try:
        # 验证插件是否存在
        with state.plugins_lock:
            plugin_exists = payload.plugin_id in state.plugins
        if not plugin_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{payload.plugin_id}' is not registered"
            )
        
        # 推送消息到队列
        message_id = push_message_to_queue(
            plugin_id=payload.plugin_id,
            source=payload.source,
            message_type=payload.message_type,
            description=payload.description,
            priority=payload.priority,
            content=payload.content,
            binary_data=payload.binary_data,
            binary_url=payload.binary_url,
            metadata=payload.metadata,
        )
        
        return PluginPushMessageResponse(
            success=True,
            message_id=message_id,
            received_at=now_iso(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("plugin_push: unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 插件管理路由（扩展） ==========

@app.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    启动插件
    
    - POST /plugin/{plugin_id}/start
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await start_plugin(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to start plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    停止插件
    
    - POST /plugin/{plugin_id}/stop
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await stop_plugin(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to stop plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    重载插件
    
    - POST /plugin/{plugin_id}/reload
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await reload_plugin(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to reload plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 性能监控路由 ==========

@app.get("/plugin/metrics")
async def get_all_plugin_metrics(_: str = require_admin):
    """
    获取所有插件的性能指标
    
    - GET /plugin/metrics
    - 需要管理员验证码（Bearer Token）
    """
    try:
        metrics = metrics_collector.get_current_metrics()
        
        # 确保 metrics 是列表
        if not isinstance(metrics, list):
            logger.warning(f"get_current_metrics returned non-list: {type(metrics)}")
            metrics = []
        
        # 确保每个 metric 都是字典
        safe_metrics = []
        for m in metrics:
            if isinstance(m, dict):
                safe_metrics.append(m)
            else:
                logger.warning(f"Invalid metric format: {type(m)}")
        
        # 计算全局性能指标（metrics 是字典列表）
        total_cpu = sum(float(m.get("cpu_percent", 0.0)) for m in safe_metrics)
        total_memory_mb = sum(float(m.get("memory_mb", 0.0)) for m in safe_metrics)
        total_memory_percent = sum(float(m.get("memory_percent", 0.0)) for m in safe_metrics)
        total_threads = sum(int(m.get("num_threads", 0)) for m in safe_metrics)
        
        return {
            "metrics": safe_metrics,
            "count": len(safe_metrics),
            "global": {
                "total_cpu_percent": round(total_cpu, 2),
                "total_memory_mb": round(total_memory_mb, 2),
                "total_memory_percent": round(total_memory_percent, 2),
                "total_threads": total_threads,
                "active_plugins": len([m for m in safe_metrics if m.get("pid") is not None])
            },
            "time": now_iso()
        }
    except Exception:
        logger.exception("Failed to get plugin metrics")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "metrics": [],
            "count": 0,
            "global": {
                "total_cpu_percent": 0.0,
                "total_memory_mb": 0.0,
                "total_memory_percent": 0.0,
                "total_threads": 0,
                "active_plugins": 0
            },
            "time": now_iso()
        }


@app.get("/plugin/metrics/{plugin_id}")
async def get_plugin_metrics(plugin_id: str, _: str = require_admin):
    """
    获取指定插件的性能指标
    
    - GET /plugin/metrics/{plugin_id}
    - 需要管理员验证码（Bearer Token）
    
    如果插件正在运行但没有指标数据（比如刚启动），返回 200 但 metrics 为 null。
    如果插件不存在，返回 404。
    """
    try:
        # 检查插件是否已注册（在 state.plugins 中）
        with state.plugins_lock:
            plugin_registered = plugin_id in state.plugins
        
        # 检查插件是否正在运行（在 state.plugin_hosts 中）
        with state.plugin_hosts_lock:
            plugin_running = plugin_id in state.plugin_hosts
            if plugin_running:
                host = state.plugin_hosts[plugin_id]
                # 检查进程状态
                process_alive = False
                if hasattr(host, "process") and host.process:
                    process_alive = host.process.is_alive()
                    if process_alive:
                        logger.debug(
                            f"Plugin {plugin_id} is running (pid: {host.process.pid})"
                        )
                    else:
                        # 进程已退出，记录退出码
                        exitcode = getattr(host.process, 'exitcode', None)
                        logger.debug(
                            f"Plugin {plugin_id} process is not alive (exitcode: {exitcode}, pid: {host.process.pid if hasattr(host.process, 'pid') else 'N/A'})"
                        )
                else:
                    logger.debug(f"Plugin {plugin_id} host has no process object")
            else:
                host = None
                process_alive = False
                # 调试：列出所有正在运行的插件
                all_running_plugins = list(state.plugin_hosts.keys())
                logger.info(
                    f"Plugin {plugin_id} is registered but not in plugin_hosts. "
                    f"Currently tracked plugins in plugin_hosts: {all_running_plugins}. "
                    f"Plugin may need to be started manually via /plugin/{plugin_id}/start"
                )
        
        # 如果插件未注册，返回 404
        if not plugin_registered:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' not found"
            )
        
        # 获取指标数据
        metrics = metrics_collector.get_current_metrics(plugin_id)
        
        if not metrics:
            # 插件已注册但没有指标数据
            # 检查进程状态以提供更详细的信息
            if not plugin_running:
                message = "Plugin is registered but not running (start the plugin to collect metrics)"
            elif not process_alive:
                message = "Plugin process is not alive (may have crashed or stopped)"
            else:
                message = "Plugin is running but no metrics available yet (may be collecting, check collector status)"
            
            logger.debug(
                f"Plugin {plugin_id} registered but no metrics: registered={plugin_registered}, "
                f"running={plugin_running}, process_alive={process_alive}, has_host={host is not None}"
            )
            
            return {
                "plugin_id": plugin_id,
                "metrics": None,
                "message": message,
                "plugin_running": plugin_running,
                "process_alive": process_alive,
                "time": now_iso()
            }
        
        return {
            "plugin_id": plugin_id,
            "metrics": metrics[0],
            "time": now_iso()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get metrics for plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.get("/plugin/metrics/{plugin_id}/history")
async def get_plugin_metrics_history(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    _: str = require_admin
):
    """
    获取插件性能指标历史
    
    - GET /plugin/metrics/{plugin_id}/history?limit=100
    - 需要管理员验证码（Bearer Token）
    """
    try:
        history = metrics_collector.get_metrics_history(
            plugin_id=plugin_id,
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )
        return {
            "plugin_id": plugin_id,
            "history": history,
            "count": len(history),
            "time": now_iso()
        }
    except Exception as e:
        logger.exception(f"Failed to get metrics history for plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 配置管理路由 ==========

@app.get("/plugin/{plugin_id}/config")
async def get_plugin_config_endpoint(plugin_id: str):
    """
    获取插件配置
    
    - GET /plugin/{plugin_id}/config
    """
    try:
        return load_plugin_config(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get config for plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: dict


@app.put("/plugin/{plugin_id}/config")
async def update_plugin_config_endpoint(plugin_id: str, payload: ConfigUpdateRequest, _: str = require_admin):
    """
    更新插件配置
    
    - PUT /plugin/{plugin_id}/config
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return update_plugin_config(plugin_id, payload.config)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update config for plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ========== 日志路由 ==========

@app.get("/plugin/{plugin_id}/logs")
async def get_plugin_logs_endpoint(
    plugin_id: str,
    lines: int = Query(default=100, ge=1, le=10000),
    level: Optional[str] = Query(default=None, description="日志级别: DEBUG, INFO, WARNING, ERROR"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="关键词搜索"),
    _: str = require_admin
):
    """
    获取插件日志或服务器日志
    
    - GET /plugin/{plugin_id}/logs?lines=100&level=INFO&search=error
    - GET /plugin/_server/logs - 获取服务器日志
    - 需要管理员验证码（Bearer Token）
    """
    try:
        result = get_plugin_logs(
            plugin_id=plugin_id,
            lines=lines,
            level=level,
            start_time=start_time,
            end_time=end_time,
            search=search
        )
        # 如果返回结果中包含错误信息，记录但不抛出异常（返回空日志列表）
        if "error" in result:
            logger.warning(f"Error getting logs for {plugin_id}: {result.get('error')}")
        return result
    except Exception as e:
        logger.exception(f"Failed to get logs for plugin {plugin_id}")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": str(e)
        }


@app.get("/plugin/{plugin_id}/logs/files")
async def get_plugin_log_files_endpoint(plugin_id: str, _: str = require_admin):
    """
    获取插件日志文件列表或服务器日志文件列表
    
    - GET /plugin/{plugin_id}/logs/files
    - GET /plugin/_server/logs/files - 获取服务器日志文件列表
    - 需要管理员验证码（Bearer Token）
    """
    try:
        files = get_plugin_log_files(plugin_id)
        return {
            "plugin_id": plugin_id,
            "log_files": files,
            "count": len(files),
            "time": now_iso()
        }
    except Exception as e:
        logger.exception(f"Failed to get log files for plugin {plugin_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.websocket("/ws/logs/{plugin_id}")
async def websocket_log_stream(websocket: WebSocket, plugin_id: str):
    """
    WebSocket 端点：实时推送日志流
    
    - WS /ws/logs/{plugin_id} - 实时接收插件日志
    - WS /ws/logs/_server - 实时接收服务器日志
    - 注意：WebSocket 认证需要在连接时通过查询参数传递验证码
    """
    # WebSocket 认证通过查询参数实现
    code = websocket.query_params.get("code", "").upper()
    from plugin.server.auth import get_admin_code
    admin_code = get_admin_code()
    
    if not admin_code or code != admin_code:
        await websocket.close(code=1008, reason="Authentication required")
        return
    
    await log_stream_endpoint(websocket, plugin_id)


# ========== 主程序入口 ==========

if __name__ == "__main__":
    import uvicorn
    import os
    import signal
    
    host = "127.0.0.1"  # 默认只暴露本机
    
    try:
        uvicorn.run(app, host=host, port=USER_PLUGIN_SERVER_PORT, log_config=None)
    finally:
        # 强制清理所有子进程
        try:
            # 尝试使用 psutil 清理子进程（更安全）
            import psutil
            parent = psutil.Process(os.getpid())
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            # 等待一会
            _, alive = psutil.wait_procs(children, timeout=3)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        except ImportError:
            # 如果没有 psutil，尝试使用进程组清理（Linux/Mac）
            if hasattr(os, 'killpg'):
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                except Exception:
                    pass
        except Exception:
            pass
