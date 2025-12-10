"""
User Plugin Server

HTTP 服务器主文件，定义所有路由端点。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query
from config import USER_PLUGIN_SERVER_PORT

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

app = FastAPI(title="N.E.K.O User Plugin Server")
logger = logging.getLogger("user_plugin_server")

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
    return {
        "status": "ok",
        "available": True,
        "plugins_count": len(state.plugins),
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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/plugin/trigger", response_model=PluginTriggerResponse)
async def plugin_trigger(payload: PluginTriggerRequest, request: Request):
    """
    触发指定插件的指定 entry
    """
    try:
        client_host = request.client.host if request.client else None
        
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
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


# ========== 消息路由 ==========

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
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/plugin/push", response_model=PluginPushMessageResponse)
async def plugin_push_message(payload: PluginPushMessageRequest, request: Request):
    """
    接收插件推送的消息（HTTP端点，主要用于外部调用或测试）
    
    注意：插件通常通过进程间通信直接推送，此端点作为备用。
    """
    try:
        # 验证插件是否存在
        if payload.plugin_id not in state.plugins:
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
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


# ========== 生命周期事件 ==========

@app.on_event("startup")
async def startup_event():
    """服务器启动时初始化"""
    await startup()


@app.on_event("shutdown")
async def shutdown_event():
    """服务器关闭时清理"""
    await shutdown()


# ========== 工具函数（向后兼容） ==========

def get_plugins():
    """返回插件列表（同进程访问）"""
    return registry_get_plugins()


# ========== 主程序入口 ==========

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.DEBUG)
    host = "127.0.0.1"  # 默认只暴露本机
    uvicorn.run(app, host=host, port=USER_PLUGIN_SERVER_PORT)
