"""
业务逻辑服务

提供插件相关的业务逻辑处理。
"""
import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from plugin.core.state import state
from plugin.api.models import (
    PluginTriggerResponse,
    PluginPushMessage,
    PluginPushMessageResponse,
)
from plugin.api.exceptions import (
    PluginError,
    PluginTimeoutError,
)
from plugin.server.utils import now_iso
from plugin.settings import (
    PLUGIN_EXECUTION_TIMEOUT,
    MESSAGE_QUEUE_DEFAULT_MAX_COUNT,
)

logger = logging.getLogger("user_plugin_server")


def build_plugin_list() -> List[Dict[str, Any]]:
    """
    构建插件列表
    
    返回格式化的插件信息列表，包括每个插件的入口点信息。
    """
    result = []
    
    with state.plugins_lock:
        if not state.plugins:
            return result
        
        # 创建副本以避免长时间持有锁
        plugins_copy = dict(state.plugins)
    
    logger.info("加载插件列表成功")
    
    for plugin_id, plugin_meta in plugins_copy.items():
        try:
            plugin_info = plugin_meta.copy()
            plugin_info["entries"] = []
            
            # 处理每个插件的入口点
            seen = set()  # 用于去重 (event_type, id)
            # 创建 event_handlers 的副本以避免长时间持有锁
            with state.event_handlers_lock:
                event_handlers_copy = dict(state.event_handlers)
            for key, eh in event_handlers_copy.items():
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
                
                # 安全获取各字段
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
    return result


async def trigger_plugin(
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    task_id: Optional[str] = None,
    client_host: Optional[str] = None,
) -> PluginTriggerResponse:
    """
    触发插件执行
    
    Args:
        plugin_id: 插件ID
        entry_id: 入口点ID
        args: 参数
        task_id: 任务ID（可选）
        client_host: 客户端主机（可选）
    
    Returns:
        PluginTriggerResponse
    
    Raises:
        HTTPException: 如果插件不存在或执行失败
    """
    logger.info(
        "[plugin_trigger] plugin_id=%s entry_id=%s task_id=%s args=%s",
        plugin_id, entry_id, task_id, args
    )
    
    # 记录事件到队列
    event = {
        "type": "plugin_triggered",
        "plugin_id": plugin_id,
        "entry_id": entry_id,
        "args": args,
        "task_id": task_id,
        "client": client_host,
        "received_at": now_iso(),
    }
    _enqueue_event(event)
    
    # 获取插件宿主
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
    
    # 执行插件
    plugin_response: Any = None
    plugin_error: Optional[Dict[str, Any]] = None
    
    try:
        plugin_response = await host.trigger(entry_id, args, timeout=PLUGIN_EXECUTION_TIMEOUT)
    except TimeoutError as e:
        plugin_error = {"error": "Plugin execution timed out"}
        logger.error(f"Plugin {plugin_id} entry {entry_id} timed out: {e}")
    except PluginError as e:
        logger.warning(f"Plugin {plugin_id} entry {entry_id} error: {e}")
        plugin_error = {"error": str(e)}
    except (ConnectionError, OSError) as e:
        logger.error(f"Communication error with plugin {plugin_id}: {e}")
        plugin_error = {"error": f"Communication error: {str(e)}"}
    except Exception as e:
        logger.exception(f"plugin_trigger: unexpected error invoking plugin {plugin_id} via IPC")
        plugin_error = {"error": f"Unexpected error: {str(e)}"}
    
    return PluginTriggerResponse(
        success=plugin_error is None,
        plugin_id=plugin_id,
        executed_entry=entry_id,
        args=args,
        plugin_response=plugin_response,
        received_at=event["received_at"],
        plugin_forward_error=plugin_error,
    )


def get_messages_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int = None,
    priority_min: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    从消息队列中获取消息
    
    Args:
        plugin_id: 过滤特定插件（可选）
        max_count: 最大数量（None 时使用默认值）
        priority_min: 最低优先级（可选）
    
    Returns:
        消息列表
    """
    if max_count is None:
        max_count = MESSAGE_QUEUE_DEFAULT_MAX_COUNT
    
    # 先把当前队列内容全部取出
    remaining: List[Dict[str, Any]] = []
    while True:
        try:
            msg = state.message_queue.get_nowait()
            remaining.append(msg)
        except asyncio.QueueEmpty:
            break
    
    # 在内存里按顺序过滤 + 构造返回
    messages: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    count = 0
    
    for msg in remaining:
        if count < max_count:
            # 过滤插件ID
            if plugin_id and msg.get("plugin_id") != plugin_id:
                kept.append(msg)
                continue
            
            # 过滤优先级
            if priority_min is not None:
                msg_priority = msg.get("priority", 0)
                if msg_priority < priority_min:
                    kept.append(msg)
                    continue
            
            # 命中的消息构建 PluginPushMessage
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
                timestamp=msg.get("time", now_iso()),
                message_id=message_id,
            )
            message_dict = plugin_message.model_dump()
            messages.append(message_dict)
            
            # 服务器终端日志输出
            content_str = msg.get("content") or ""
            logger.info(
                f"[MESSAGE] Plugin: {msg.get('plugin_id', 'unknown')} | "
                f"Source: {msg.get('source', 'unknown')} | "
                f"Priority: {msg.get('priority', 0)} | "
                f"Description: {msg.get('description', '')} | "
                f"Content: {content_str[:100]}"
            )
            
            count += 1
        else:
            # 已达到最大数量，剩余消息保留
            kept.append(msg)
    
    # 未消费的消息按原顺序放回队列
    for msg in kept:
        try:
            state.message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("Message queue is full when re-queueing filtered messages, dropping")
            break
    
    return messages


def push_message_to_queue(
    plugin_id: str,
    source: str,
    message_type: str,
    description: str = "",
    priority: int = 0,
    content: Optional[str] = None,
    binary_data: Optional[bytes] = None,
    binary_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    将消息推送到队列
    
    Returns:
        message_id
    """
    message_id = str(uuid.uuid4())
    message = {
        "type": "MESSAGE_PUSH",
        "plugin_id": plugin_id,
        "source": source,
        "description": description,
        "priority": priority,
        "message_type": message_type,
        "content": content,
        "binary_data": binary_data,
        "binary_url": binary_url,
        "metadata": metadata or {},
        "time": now_iso(),
    }
    
    try:
        state.message_queue.put_nowait(message)
        logger.info(
            f"[MESSAGE PUSH] Plugin: {plugin_id} | "
            f"Source: {source} | "
            f"Type: {message_type} | "
            f"Priority: {priority} | "
            f"Description: {description} | "
            f"Content: {(content or '')[:100]}"
        )
    except asyncio.QueueFull:
        # 队列满时，尝试移除最旧的消息
        try:
            state.message_queue.get_nowait()
            state.message_queue.put_nowait(message)
            logger.warning("Message queue full, dropped oldest message")
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
    
    return message_id


def _enqueue_event(event: Dict[str, Any]) -> None:
    """将事件加入事件队列（非阻塞，失败不影响主流程）"""
    try:
        if state.event_queue:
            state.event_queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            state.event_queue.get_nowait()
            state.event_queue.put_nowait(event)
            logger.debug("Event queue was full, dropped oldest event")
        except (asyncio.QueueEmpty, AttributeError) as e:
            logger.warning(f"Event queue operation failed after queue full: {e}")
    except (AttributeError, RuntimeError) as e:
        logger.warning(f"Event queue error, continuing without queueing: {e}")

