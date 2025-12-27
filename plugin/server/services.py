"""
业务逻辑服务

提供插件相关的业务逻辑处理。
"""
import asyncio
import logging
import os
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
    PluginExecutionError,
    PluginCommunicationError,
)
from plugin.server.error_handler import handle_plugin_error
from plugin.server.utils import now_iso
from plugin.settings import (
    PLUGIN_EXECUTION_TIMEOUT,
    MESSAGE_QUEUE_DEFAULT_MAX_COUNT,
)
from plugin.sdk.errors import ErrorCode
from plugin.sdk.responses import fail, is_envelope

logger = logging.getLogger("user_plugin_server")


def _format_log_text(value: Any) -> str:
    s = "" if value is None else str(value)

    try:
        max_len = int(os.getenv("NEKO_PLUGIN_LOG_CONTENT_MAX", "200"))
    except Exception:
        max_len = 200
    if max_len <= 0:
        max_len = 200

    truncated = False
    if len(s) > max_len:
        s = s[:max_len]
        truncated = True

    try:
        wrap = int(os.getenv("NEKO_PLUGIN_LOG_WRAP", "0"))
    except Exception:
        wrap = 0

    if wrap and wrap > 0:
        # Hard-wrap to avoid giant single-line log entries.
        s = "\n".join(s[i : i + wrap] for i in range(0, len(s), wrap))

    if truncated:
        s = s + "...(truncated)"

    return s


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
    
    # 获取运行状态（需要检查 plugin_hosts）
    with state.plugin_hosts_lock:
        running_plugins = set(state.plugin_hosts.keys())
        # 创建 host 的副本以便后续检查（在锁外使用）
        hosts_copy = dict(state.plugin_hosts)
    
    for plugin_id, plugin_meta in plugins_copy.items():
        try:
            plugin_info = plugin_meta.copy()
            plugin_info["entries"] = []
            
            # 检查插件是否正在运行
            is_running = False
            if plugin_id in running_plugins:
                host = hosts_copy.get(plugin_id)
                if host and hasattr(host, 'is_alive'):
                    is_running = host.is_alive()
            
            plugin_info["status"] = "running" if is_running else "stopped"
            
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
    # 关键日志：记录触发请求
    logger.info(
        "[plugin_trigger] Processing trigger: plugin_id=%s, entry_id=%s, task_id=%s",
        plugin_id, entry_id, task_id
    )
    
    # 详细参数信息使用 DEBUG
    logger.debug(
        "[plugin_trigger] Args: type=%s, keys=%s, content=%s",
        type(args),
        list(args.keys()) if isinstance(args, dict) else "N/A",
        args,
    )
    
    # 记录事件到队列
    trace_id = str(uuid.uuid4())
    event = {
        "type": "plugin_triggered",
        "plugin_id": plugin_id,
        "entry_id": entry_id,
        "args": args,
        "task_id": task_id,
        "client": client_host,
        "received_at": now_iso(),
        "trace_id": trace_id,
    }
    _enqueue_event(event)
    
    # 首先检查插件是否已注册
    with state.plugins_lock:
        plugin_registered = plugin_id in state.plugins
    
    # 获取插件宿主（检查是否正在运行）
    with state.plugin_hosts_lock:
        host = state.plugin_hosts.get(plugin_id)
        all_running_plugin_ids = list(state.plugin_hosts.keys())
    
    if not host:
        logger.debug(
            "Plugin {} not found in plugin_hosts. Registered plugins: {}, Running plugins: {}",
            plugin_id,
            list(state.plugins.keys()) if state.plugins else [],
            all_running_plugin_ids
        )
        # 插件未运行，检查是否已注册
        if plugin_registered:
            plugin_response = fail(
                ErrorCode.NOT_READY,
                f"Plugin '{plugin_id}' is registered but not running",
                details={
                    "hint": f"Start the plugin via POST /plugin/{plugin_id}/start",
                    "running_plugins": all_running_plugin_ids,
                },
                retriable=True,
                trace_id=trace_id,
            )
        else:
            plugin_response = fail(
                ErrorCode.NOT_FOUND,
                f"Plugin '{plugin_id}' is not found/registered",
                details={"known_plugins": list(state.plugins.keys()) if state.plugins else []},
                trace_id=trace_id,
            )

        return PluginTriggerResponse(
            success=False,
            plugin_id=plugin_id,
            executed_entry=entry_id,
            args=args,
            plugin_response=plugin_response,
            received_at=event["received_at"],
            plugin_forward_error=None,
        )
    
    # 检查进程健康状态
    try:
        health = host.health_check()
        if not health.alive:
            plugin_response = fail(
                ErrorCode.NOT_READY,
                f"Plugin '{plugin_id}' process is not alive (status: {health.status})",
                details={"status": health.status, "pid": health.pid, "exitcode": health.exitcode},
                retriable=True,
                trace_id=trace_id,
            )
            return PluginTriggerResponse(
                success=False,
                plugin_id=plugin_id,
                executed_entry=entry_id,
                args=args,
                plugin_response=plugin_response,
                received_at=event["received_at"],
                plugin_forward_error=None,
            )
    except (AttributeError, RuntimeError) as e:
        logger.error(f"Failed to check health for plugin {plugin_id}: {e}")
        plugin_response = fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health check failed",
            details={"error": str(e)},
            retriable=True,
            trace_id=trace_id,
        )
        return PluginTriggerResponse(
            success=False,
            plugin_id=plugin_id,
            executed_entry=entry_id,
            args=args,
            plugin_response=plugin_response,
            received_at=event["received_at"],
            plugin_forward_error=None,
        )
    
    # 执行插件
    plugin_response: Any = None
    
    logger.debug(
        "[plugin_trigger] Calling host.trigger: entry_id=%s, args=%s",
        entry_id,
        args,
    )
    
    try:
        plugin_response = await host.trigger(entry_id, args, timeout=PLUGIN_EXECUTION_TIMEOUT)
        logger.debug(
            "[plugin_trigger] Plugin response: %s",
            str(plugin_response)[:500] if plugin_response else None,
        )
    except (TimeoutError, asyncio.TimeoutError) as e:
        logger.error(f"Plugin {plugin_id} entry {entry_id} timed out: {e}")
        plugin_response = fail(
            ErrorCode.TIMEOUT,
            "Plugin execution timed out",
            details={"plugin_id": plugin_id, "entry_id": entry_id},
            retriable=True,
            trace_id=trace_id,
        )
    except PluginError as e:
        logger.warning(f"Plugin {plugin_id} entry {entry_id} error: {e}")
        plugin_response = fail(
            ErrorCode.INTERNAL,
            str(e),
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(e).__name__},
            trace_id=trace_id,
        )
    except (ConnectionError, OSError) as e:
        logger.error(f"Communication error with plugin {plugin_id}: {e}")
        plugin_response = fail(
            ErrorCode.NOT_READY,
            "Communication error with plugin",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "error": str(e)},
            retriable=True,
            trace_id=trace_id,
        )
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Invalid parameters for plugin {plugin_id} entry {entry_id}: {e}")
        plugin_response = fail(
            ErrorCode.VALIDATION_ERROR,
            "Invalid request parameters",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "error": str(e)},
            trace_id=trace_id,
        )
    except Exception as e:
        logger.exception(f"plugin_trigger: Unexpected error type invoking plugin {plugin_id} via IPC")
        plugin_response = fail(
            ErrorCode.INTERNAL,
            "An internal error occurred",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(e).__name__},
            trace_id=trace_id,
        )

    if not is_envelope(plugin_response):
        plugin_response = fail(
            ErrorCode.INVALID_RESPONSE,
            "Plugin returned an invalid response shape (expected SDK envelope)",
            details={
                "plugin_id": plugin_id,
                "entry_id": entry_id,
                "type": type(plugin_response).__name__,
            },
            trace_id=trace_id,
        )
    else:
        if plugin_response.get("trace_id") is None:
            plugin_response = dict(plugin_response)
            plugin_response["trace_id"] = trace_id
    
    return PluginTriggerResponse(
        success=bool(plugin_response.get("success")) if isinstance(plugin_response, dict) else False,
        plugin_id=plugin_id,
        executed_entry=entry_id,
        args=args,
        plugin_response=plugin_response,
        received_at=event["received_at"],
        plugin_forward_error=None,
    )


def get_messages_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
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
            content_str = _format_log_text(msg.get("content") or "")
            logger.info(
                f"[MESSAGE] Plugin: {msg.get('plugin_id', 'unknown')} | "
                f"Source: {msg.get('source', 'unknown')} | "
                f"Priority: {msg.get('priority', 0)} | "
                f"Description: {msg.get('description', '')} | "
                f"Content: {content_str}"
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


def get_events_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
) -> List[Dict[str, Any]]:
    """从事件队列中获取事件。

    Args:
        plugin_id: 过滤特定插件（可选）
        max_count: 最大数量（None 时使用默认值）

    Returns:
        事件列表（原始 dict）
    """
    if max_count is None:
        max_count = MESSAGE_QUEUE_DEFAULT_MAX_COUNT

    remaining: List[Dict[str, Any]] = []
    while True:
        try:
            ev = state.event_queue.get_nowait()
            if isinstance(ev, dict):
                remaining.append(ev)
            else:
                remaining.append({"type": "UNKNOWN", "raw": ev})
        except asyncio.QueueEmpty:
            break

    events: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    count = 0

    for ev in remaining:
        if count < max_count:
            if plugin_id and ev.get("plugin_id") != plugin_id:
                kept.append(ev)
                continue
            events.append(ev)
            count += 1
        else:
            kept.append(ev)

    for ev in kept:
        try:
            state.event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning("Event queue is full when re-queueing filtered events, dropping")
            break

    return events


def get_lifecycle_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
) -> List[Dict[str, Any]]:
    if max_count is None:
        max_count = MESSAGE_QUEUE_DEFAULT_MAX_COUNT

    remaining: List[Dict[str, Any]] = []
    while True:
        try:
            ev = state.lifecycle_queue.get_nowait()
            if isinstance(ev, dict):
                remaining.append(ev)
            else:
                remaining.append({"type": "UNKNOWN", "raw": ev})
        except asyncio.QueueEmpty:
            break

    events: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    count = 0

    for ev in remaining:
        if count < max_count:
            if plugin_id and ev.get("plugin_id") != plugin_id:
                kept.append(ev)
                continue
            events.append(ev)
            count += 1
        else:
            kept.append(ev)

    for ev in kept:
        try:
            state.lifecycle_queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning("Lifecycle queue is full when re-queueing filtered events, dropping")
            break

    return events


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
            f"Content: {_format_log_text(content or '')}"
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
            ) from e
    except (AttributeError, RuntimeError) as e:
        logger.error(f"Message queue error: {e}")
        raise HTTPException(
            status_code=503,
            detail="Message queue is not available"
        ) from e
    except Exception as e:
        logger.exception(f"Unexpected error in push_message_to_queue: {type(e).__name__}")
        raise HTTPException(
            status_code=503,
            detail="Failed to enqueue message"
        ) from e
    
    return message_id


def _enqueue_event(event: Dict[str, Any]) -> None:
    """
    将事件加入事件队列（非阻塞，失败不影响主流程）
    
    注意：此函数设计为静默失败，因为事件队列不是关键路径
    """
    try:
        if state.event_queue:
            state.event_queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            state.event_queue.get_nowait()
            state.event_queue.put_nowait(event)
            logger.debug("Event queue was full, dropped oldest event")
        except (asyncio.QueueEmpty, AttributeError) as e:
            logger.debug(f"Event queue operation failed after queue full: {e}")
        except Exception as e:
            logger.debug(f"Event queue cleanup failed: {type(e).__name__}")
    except (AttributeError, RuntimeError) as e:
        logger.debug(f"Event queue error, continuing without queueing: {e}")
    except Exception as e:
        # 静默失败，不影响主流程
        logger.debug(f"Event queue unexpected error: {type(e).__name__}")


def _enqueue_lifecycle(event: Dict[str, Any]) -> None:
    try:
        if state.lifecycle_queue:
            state.lifecycle_queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            state.lifecycle_queue.get_nowait()
            state.lifecycle_queue.put_nowait(event)
        except (asyncio.QueueEmpty, AttributeError):
            pass
        except Exception:
            pass
    except (AttributeError, RuntimeError):
        pass
    except Exception:
        pass

