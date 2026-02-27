"""
业务逻辑服务

提供插件相关的业务逻辑处理。
"""
import asyncio
import base64
import os
import queue as _queue
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import threading

from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from plugin.core.state import state
from plugin._types.exceptions import (
    PluginError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
)
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.infrastructure.utils import now_iso
from plugin.utils.logging import format_log_text as _format_log_text
from plugin.settings import (
    PLUGIN_EXECUTION_TIMEOUT,
    MESSAGE_QUEUE_DEFAULT_MAX_COUNT,
)
from plugin.sdk.errors import ErrorCode
from plugin.sdk.responses import ok, fail, is_envelope

# logger 已在上方导入


class TriggerResult(BaseModel):
    """trigger_plugin() 的标准返回类型。

    替代之前的裸 dict，提供类型安全和单一构造点。
    """
    success: bool
    plugin_id: str
    entry_id: str
    args: Dict[str, Any] = Field(default_factory=dict)
    plugin_response: Any = None
    received_at: str = ""


def _parse_iso_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _ingest_normalize_and_store_event(ev: Dict[str, Any]) -> None:
    if not isinstance(ev.get("trace_id"), str) or not ev.get("trace_id"):
        ev = dict(ev)
        ev["trace_id"] = str(uuid.uuid4())
    if not isinstance(ev.get("event_id"), str) or not ev.get("event_id"):
        ev = dict(ev)
        ev["event_id"] = ev.get("trace_id")
    if not isinstance(ev.get("received_at"), str) or not ev.get("received_at"):
        ev = dict(ev)
        ev["received_at"] = now_iso()
    state.append_event_record(ev)


def _ingest_normalize_event(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(ev, dict):
        return None
    out = ev
    if not isinstance(out.get("trace_id"), str) or not out.get("trace_id"):
        out = dict(out)
        out["trace_id"] = str(uuid.uuid4())
    if not isinstance(out.get("event_id"), str) or not out.get("event_id"):
        if out is ev:
            out = dict(out)
        out["event_id"] = out.get("trace_id")
    if not isinstance(out.get("received_at"), str) or not out.get("received_at"):
        if out is ev:
            out = dict(out)
        out["received_at"] = now_iso()
    return out


def _ingest_normalize_and_store_lifecycle(ev: Dict[str, Any]) -> None:
    if not isinstance(ev.get("trace_id"), str) or not ev.get("trace_id"):
        ev = dict(ev)
        ev["trace_id"] = str(uuid.uuid4())
    if not isinstance(ev.get("lifecycle_id"), str) or not ev.get("lifecycle_id"):
        ev = dict(ev)
        ev["lifecycle_id"] = ev.get("trace_id")
    if not isinstance(ev.get("time"), str) or not ev.get("time"):
        ev = dict(ev)
        ev["time"] = now_iso()
    state.append_lifecycle_record(ev)


def _ingest_normalize_lifecycle(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(ev, dict):
        return None
    out = ev
    if not isinstance(out.get("trace_id"), str) or not out.get("trace_id"):
        out = dict(out)
        out["trace_id"] = str(uuid.uuid4())
    if not isinstance(out.get("lifecycle_id"), str) or not out.get("lifecycle_id"):
        if out is ev:
            out = dict(out)
        out["lifecycle_id"] = out.get("trace_id")
    if not isinstance(out.get("time"), str) or not out.get("time"):
        if out is ev:
            out = dict(out)
        out["time"] = now_iso()
    return out


 


def _b64_bytes(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return None
    try:
        return base64.b64encode(bytes(value)).decode("utf-8")
    except Exception:
        return None


def build_plugin_list() -> List[Dict[str, Any]]:
    """
    构建插件列表
    
    返回格式化的插件信息列表，包括每个插件的入口点信息。
    
    锁顺序规范: plugins_lock -> plugin_hosts_lock -> event_handlers_lock
    """
    result = []
    
    # 一次性获取所有需要的数据快照，避免在循环中反复获取锁
    # 使用带缓存的快照方法（500ms TTL），进一步减少锁竞争
    try:
        plugins_copy = state.get_plugins_snapshot_cached(timeout=2.0)
        if not plugins_copy:
            return result
        
        hosts_copy = state.get_plugin_hosts_snapshot_cached(timeout=2.0)
        running_plugins = set(hosts_copy.keys())
        event_handlers_copy = state.get_event_handlers_snapshot_cached(timeout=2.0)
    except Exception as e:
        logger.warning("Failed to get state snapshots in build_plugin_list: {}", e)
        # 如果无法获取快照，返回空列表而不是阻塞
        return result
    
    logger.info("加载插件列表成功")
    
    for plugin_id, plugin_meta in plugins_copy.items():
        try:
            plugin_info = plugin_meta.copy()
            plugin_info["entries"] = []
            
            # 根据插件类型推导状态
            plugin_type = plugin_meta.get("type", "plugin")
            if plugin_type == "extension":
                # Extension 不是独立进程，状态取决于宿主
                host_pid = plugin_meta.get("host_plugin_id")
                runtime_enabled = plugin_meta.get("runtime_enabled", True)
                if not runtime_enabled:
                    plugin_info["status"] = "disabled"
                elif host_pid and host_pid in running_plugins:
                    plugin_info["status"] = "injected"
                else:
                    plugin_info["status"] = "pending"
            else:
                # 普通插件：检查是否正在运行
                # 注意：不调用 host.is_alive()，因为 multiprocessing.Process.is_alive() 可能阻塞事件循环
                is_running = plugin_id in running_plugins
                plugin_info["status"] = "running" if is_running else "stopped"
            
            # 处理每个插件的入口点（使用快照数据，无需再获取锁）
            seen = set()  # 用于去重 (event_type, id)
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

            # Fallback: disabled plugins (visibility only) may carry entries_preview instead of
            # registering into state.event_handlers.
            if not plugin_info["entries"]:
                preview = plugin_meta.get("entries_preview")
                if isinstance(preview, list):
                    for ent in preview:
                        if not isinstance(ent, dict):
                            continue
                        eid = ent.get("id")
                        if not eid:
                            continue
                        dedup_key = ("plugin_entry", str(eid))
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        plugin_info["entries"].append(
                            {
                                "id": str(eid),
                                "name": ent.get("name", ""),
                                "description": ent.get("description", ""),
                                "event_key": ent.get("event_key", f"{plugin_id}.{eid}"),
                                "input_schema": ent.get("input_schema", {}) or {},
                                "return_message": ent.get("return_message", "") or "",
                            }
                        )
            
            result.append(plugin_info)
            
        except (AttributeError, KeyError, TypeError) as e:
            logger.opt(exception=True).warning("Error processing plugin {} metadata: {}", plugin_id, e)
            # 即使元数据有问题，也返回基本信息
            result.append({
                "id": plugin_id,
                "name": plugin_meta.get("name", plugin_id),
                "description": plugin_meta.get("description", ""),
                "entries": [],
            })
    
    logger.debug("Loaded plugins: {}", result)
    return result


def _resolve_and_check_host(
    plugin_id: str,
    trace_id: str,
) -> tuple[Any, Optional[Dict[str, Any]]]:
    """Resolve the plugin host and verify it is healthy.

    Returns:
        ``(host, None)`` on success, or ``(None, fail_response)`` on error.
        The caller should construct a ``TriggerResult`` from *fail_response*
        when it is not ``None``.
    """
    # 锁顺序: plugins_lock -> plugin_hosts_lock
    try:
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
    except Exception as e:
        logger.warning(
            "Failed to get state snapshots for plugin {}: {}, using fallback",
            plugin_id,
            e
        )
        return None, fail(
            ErrorCode.NOT_READY,
            "System is busy, please retry",
            details={"hint": "State snapshots unavailable"},
            retriable=True,
            trace_id=trace_id,
        )

    host = hosts_snapshot.get(plugin_id)

    if not host:
        plugin_registered = plugin_id in plugins_snapshot
        all_running_plugin_ids = list(hosts_snapshot.keys())
        logger.debug(
            "Plugin {} not found in plugin_hosts. Registered plugins: {}, Running plugins: {}",
            plugin_id,
            list(plugins_snapshot.keys()),
            all_running_plugin_ids
        )
        if plugin_registered:
            err = fail(
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
            err = fail(
                ErrorCode.NOT_FOUND,
                f"Plugin '{plugin_id}' is not found/registered",
                details={"known_plugins": list(plugins_snapshot.keys())},
                trace_id=trace_id,
            )
        return None, err

    # 健康检查
    try:
        health = host.health_check()
        if not health.alive:
            return None, fail(
                ErrorCode.NOT_READY,
                f"Plugin '{plugin_id}' process is not alive (status: {health.status})",
                details={"status": health.status, "pid": health.pid, "exitcode": health.exitcode},
                retriable=True,
                trace_id=trace_id,
            )
    except (AttributeError, RuntimeError) as e:
        logger.opt(exception=True).error(f"Failed to check health for plugin {plugin_id}: {e}")
        return None, fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health check failed",
            details={"error": str(e)},
            retriable=True,
            trace_id=trace_id,
        )

    return host, None


# ---------------------------------------------------------------------------
# Pipeline helpers for trigger_plugin
# ---------------------------------------------------------------------------

# Declarative exception → (ErrorCode, message, log_level, retriable) mapping.
# Order matters: first match wins (most specific first).
_TRIGGER_ERROR_MAP: list[tuple[type | tuple[type, ...], ErrorCode, str, bool]] = [
    ((TimeoutError, asyncio.TimeoutError), ErrorCode.TIMEOUT, "error", True),
    (PluginError,                          ErrorCode.INTERNAL, "warning", False),
    ((ConnectionError, OSError),           ErrorCode.NOT_READY, "error", True),
    ((ValueError, TypeError, AttributeError), ErrorCode.VALIDATION_ERROR, "error", False),
]

_TRIGGER_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.TIMEOUT: "Plugin execution timed out",
    ErrorCode.NOT_READY: "Communication error with plugin",
    ErrorCode.VALIDATION_ERROR: "Invalid request parameters",
}


async def _execute_trigger(
    host: Any,
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    trace_id: str,
) -> Any:
    """Execute host.trigger() and convert any exception to a fail() envelope.

    Returns the raw plugin response on success, or a fail() dict on error.
    """
    try:
        resp = await host.trigger(entry_id, args, timeout=PLUGIN_EXECUTION_TIMEOUT)
        logger.debug(
            "[plugin_trigger] Plugin response: {}",
            str(resp)[:500] if resp else None,
        )
        return resp
    except Exception as exc:
        ctx = {"plugin_id": plugin_id, "entry_id": entry_id}
        for exc_types, code, level, retriable in _TRIGGER_ERROR_MAP:
            if isinstance(exc, exc_types):
                msg = _TRIGGER_ERROR_MESSAGES.get(code, str(exc))
                ctx["type"] = type(exc).__name__
                getattr(logger.opt(exception=True), level)(
                    "plugin_trigger: {} invoking plugin {} entry {}",
                    type(exc).__name__, plugin_id, entry_id,
                )
                return fail(code, msg, details=ctx, retriable=retriable, trace_id=trace_id)
        # Fallback: unexpected exception type
        logger.exception("plugin_trigger: Unexpected error invoking plugin {} via IPC", plugin_id)
        ctx["type"] = type(exc).__name__
        return fail(
            ErrorCode.INTERNAL, "An internal error occurred",
            details=ctx, trace_id=trace_id,
        )


def _normalize_plugin_response(plugin_response: Any, trace_id: str) -> dict:
    """Ensure the plugin response is a standard ok()/fail() envelope.

    Auto-wraps plain return values so plugins can simply ``return {"key": val}``.
    """
    if not is_envelope(plugin_response):
        if isinstance(plugin_response, dict):
            return ok(data=plugin_response, trace_id=trace_id)
        elif plugin_response is None:
            return ok(trace_id=trace_id)
        else:
            return ok(data=plugin_response, trace_id=trace_id)
    # Already an envelope — ensure trace_id is present
    if plugin_response.get("trace_id") is None:
        plugin_response = dict(plugin_response)
        plugin_response["trace_id"] = trace_id
    return plugin_response


async def trigger_plugin(
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    task_id: Optional[str] = None,
    client_host: Optional[str] = None,
) -> TriggerResult:
    """
    触发插件执行
    
    Args:
        plugin_id: 插件ID
        entry_id: 入口点ID
        args: 参数
        task_id: 任务ID（可选）
        client_host: 客户端主机（可选）
    
    Returns:
        TriggerResult
    
    Raises:
        HTTPException: 如果插件不存在或执行失败
    """
    # --- Stage 1: Log ---
    logger.info(
        "[plugin_trigger] Processing trigger: plugin_id={}, entry_id={}, task_id={}",
        plugin_id, entry_id, task_id,
    )
    logger.debug(
        "[plugin_trigger] Args: type={}, keys={}",
        type(args), list(args.keys()) if isinstance(args, dict) else "N/A",
    )

    # --- Stage 2: Trace ---
    trace_id = str(uuid.uuid4())
    received_at = now_iso()
    _enqueue_event({
        "type": "plugin_triggered",
        "plugin_id": plugin_id, "entry_id": entry_id,
        "args": args, "task_id": task_id,
        "client": client_host, "received_at": received_at,
        "trace_id": trace_id,
    })

    def _fail_result(response: Any) -> TriggerResult:
        return TriggerResult(
            success=False, plugin_id=plugin_id, entry_id=entry_id,
            args=args, plugin_response=response, received_at=received_at,
        )

    # --- Stage 3: Resolve ---
    host, resolve_error = _resolve_and_check_host(plugin_id, trace_id)
    if resolve_error is not None:
        return _fail_result(resolve_error)

    # --- Stage 4: Execute ---
    plugin_response = await _execute_trigger(host, plugin_id, entry_id, args, trace_id)

    # --- Stage 5: Normalize ---
    plugin_response = _normalize_plugin_response(plugin_response, trace_id)

    # --- Stage 6: Return ---
    return TriggerResult(
        success=bool(plugin_response.get("success")) if isinstance(plugin_response, dict) else False,
        plugin_id=plugin_id,
        entry_id=entry_id,
        args=args,
        plugin_response=plugin_response,
        received_at=received_at,
    )


def get_messages_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
    priority_min: Optional[int] = None,
    source: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    strict: bool = True,
    since_ts: Optional[float] = None,
    raw: bool = False,
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

    # messages are authoritative in message_plane; control-plane keeps a cache refreshed on demand.
    try:
        state.refresh_messages_cache_from_message_plane(
            limit=int(max_count) if max_count is not None else 100,
            timeout=1.0,
            ttl_seconds=0.5,
            force=False,
        )
    except Exception:
        # Best-effort: if message_plane is unavailable, serve the last cached snapshot.
        pass

    # Optimize common case: scan from the tail and expand window until we have enough matches.
    # Worst-case still falls back to full scan, preserving semantics.
    store_size = 0
    try:
        store_size = int(state.message_store_len())
    except Exception:
        store_size = 0

    flt = dict(filter) if isinstance(filter, dict) else {}
    if source is None and isinstance(flt.get("source"), str) and flt.get("source"):
        source = str(flt.get("source"))
    if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt.get("plugin_id"):
        plugin_id = str(flt.get("plugin_id"))
    if priority_min is None and flt.get("priority_min") is not None:
        try:
            v = flt.get("priority_min")
            if isinstance(v, (int, float, str)):
                priority_min = int(v)
        except Exception:
            priority_min = priority_min
    if since_ts is None and flt.get("since_ts") is not None:
        try:
            v = flt.get("since_ts")
            if isinstance(v, (int, float, str)):
                since_ts = float(v)
        except Exception:
            since_ts = since_ts

    def _re_ok(field: str, pattern: Optional[str], value: Optional[str]) -> bool:
        if pattern is None:
            return True
        if value is None:
            return False
        try:
            return re.search(str(pattern), str(value)) is not None
        except re.error as e:
            if bool(strict):
                raise e
            return False

    def _match_message(msg: Dict[str, Any]) -> bool:
        if not flt:
            return True
        if flt.get("kind") is not None and msg.get("kind") != flt.get("kind"):
            return False
        if flt.get("type") is not None and msg.get("message_type") != flt.get("type") and msg.get("type") != flt.get("type"):
            return False
        if flt.get("plugin_id") is not None and msg.get("plugin_id") != flt.get("plugin_id"):
            return False
        if flt.get("source") is not None and msg.get("source") != flt.get("source"):
            return False
        if not _re_ok("kind_re", flt.get("kind_re"), msg.get("kind")):
            return False
        if not _re_ok("type_re", flt.get("type_re"), msg.get("message_type") or msg.get("type")):
            return False
        if not _re_ok("plugin_id_re", flt.get("plugin_id_re"), msg.get("plugin_id")):
            return False
        if not _re_ok("source_re", flt.get("source_re"), msg.get("source")):
            return False
        if not _re_ok("content_re", flt.get("content_re"), msg.get("content")):
            return False
        if flt.get("priority_min") is not None:
            vmin = flt.get("priority_min")
            if isinstance(vmin, (int, float, str)):
                if isinstance(msg.get("priority"), (int, float, str)) and int(msg.get("priority", 0)) < int(vmin):
                    return False
        if flt.get("since_ts") is not None:
            ts = _parse_iso_ts(msg.get("time"))
            try:
                v = flt.get("since_ts")
                if v is None:
                    return True
                if ts is None or ts <= float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        if flt.get("until_ts") is not None:
            ts = _parse_iso_ts(msg.get("time"))
            try:
                v = flt.get("until_ts")
                if v is None:
                    return True
                if ts is None or ts > float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        return True

    picked_rev: List[Dict[str, Any]] = []
    want = int(max_count)
    if want <= 0:
        want = 1

    # Avoid copying the entire store on every call: scan tail windows and expand only when needed.
    scanned = 0
    window = max(256, want * 4)
    if store_size > 0 and window > store_size:
        window = store_size

    while True:
        picked_rev.clear()
        try:
            tail_items = state.list_message_records_tail(int(window))
        except Exception:
            tail_items = state.list_message_records()

        for msg in reversed(tail_items):
            if plugin_id and msg.get("plugin_id") != plugin_id:
                continue
            if source and msg.get("source") != source:
                continue
            if priority_min is not None:
                try:
                    if int(msg.get("priority", 0)) < int(priority_min):
                        continue
                except Exception:
                    continue
            if since_ts is not None:
                ts = _parse_iso_ts(msg.get("time"))
                if ts is None or ts <= float(since_ts):
                    continue
            if not _match_message(msg):
                continue
            picked_rev.append(msg)
            if len(picked_rev) >= want:
                break

        picked_rev.reverse()

        scanned = len(tail_items)
        if len(picked_rev) >= want:
            break
        if store_size > 0 and scanned >= store_size:
            break
        if store_size <= 0 and scanned >= int(window):
            # Unknown store size; fall back after one expansion.
            pass
        if store_size > 0:
            window = min(int(store_size), int(window) * 2)
        else:
            window = int(window) * 2
        if window <= scanned:
            break

    if bool(raw):
        out: List[Dict[str, Any]] = []
        for msg in picked_rev:
            if isinstance(msg, dict):
                try:
                    bd = msg.get("binary_data")
                except Exception:
                    bd = None
                keep_bd = False
                if isinstance(bd, (bytes, bytearray)):
                    try:
                        keep_bd = len(bd) <= 16384
                    except Exception:
                        keep_bd = False
                if bd is None or keep_bd:
                    out.append(msg)
                else:
                    try:
                        m = dict(msg)
                        m["binary_data"] = None
                        out.append(m)
                    except Exception:
                        out.append(msg)
        return out

    messages: List[Dict[str, Any]] = []
    for msg in picked_rev:
        # Keep response schema consistent with PluginPushMessage.model_dump()
        messages.append(
            {
                "plugin_id": msg.get("plugin_id", ""),
                "source": msg.get("source", ""),
                "description": msg.get("description", ""),
                "priority": msg.get("priority", 0),
                "message_type": msg.get("message_type", "text"),
                "content": msg.get("content"),
                "binary_data": _b64_bytes(msg.get("binary_data")),
                "binary_url": msg.get("binary_url"),
                "metadata": msg.get("metadata", {}),
                "timestamp": msg.get("time", now_iso()),
                "message_id": str(msg.get("message_id") or ""),
            }
        )

    return messages


def get_events_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
    filter: Optional[Dict[str, Any]] = None,
    strict: bool = True,
    since_ts: Optional[float] = None,
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

    store_size = 0
    try:
        store_size = int(state.event_store_len())
    except Exception:
        store_size = 0
    flt = dict(filter) if isinstance(filter, dict) else {}
    if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt.get("plugin_id"):
        plugin_id = str(flt.get("plugin_id"))
    if since_ts is None and flt.get("since_ts") is not None:
        try:
            v = flt.get("since_ts")
            if isinstance(v, (int, float, str)):
                since_ts = float(v)
        except Exception:
            since_ts = since_ts

    def _re_ok(field: str, pattern: Optional[str], value: Optional[str]) -> bool:
        if pattern is None:
            return True
        if value is None:
            return False
        try:
            return re.search(str(pattern), str(value)) is not None
        except re.error as e:
            if bool(strict):
                raise e
            return False

    def _match_event(ev: Dict[str, Any]) -> bool:
        if not flt:
            return True
        if flt.get("kind") is not None and ev.get("kind") != flt.get("kind"):
            return False
        if flt.get("type") is not None and ev.get("type") != flt.get("type"):
            return False
        if flt.get("plugin_id") is not None and ev.get("plugin_id") != flt.get("plugin_id"):
            return False
        if flt.get("source") is not None and ev.get("source") != flt.get("source"):
            return False
        if not _re_ok("kind_re", flt.get("kind_re"), ev.get("kind")):
            return False
        if not _re_ok("type_re", flt.get("type_re"), ev.get("type")):
            return False
        if not _re_ok("plugin_id_re", flt.get("plugin_id_re"), ev.get("plugin_id")):
            return False
        if not _re_ok("source_re", flt.get("source_re"), ev.get("source")):
            return False
        if not _re_ok("content_re", flt.get("content_re"), ev.get("content")):
            return False
        if flt.get("since_ts") is not None:
            ts = _parse_iso_ts(ev.get("received_at"))
            try:
                v = flt.get("since_ts")
                if v is None:
                    return True
                if ts is None or ts <= float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        if flt.get("until_ts") is not None:
            ts = _parse_iso_ts(ev.get("received_at"))
            try:
                v = flt.get("until_ts")
                if v is None:
                    return True
                if ts is None or ts > float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        return True

    picked_rev: List[Dict[str, Any]] = []
    scan_limit = int(max_count)
    if scan_limit < 0:
        scan_limit = 0
    # Avoid copying the full store on every request (O(N) per call).
    # Use a bounded tail window to keep latency stable under high load.
    if scan_limit <= 0:
        scan_limit = 200
    try:
        scan_limit = int(min(int(store_size or 0), max(scan_limit * 20, 2000)))
    except Exception:
        scan_limit = max(int(max_count) * 20, 2000)

    try:
        snapshot = state.list_event_records_tail(scan_limit)
    except Exception:
        try:
            snapshot = state.list_event_records()
        except Exception:
            snapshot = []

    for ev in reversed(snapshot):
        if plugin_id and ev.get("plugin_id") != plugin_id:
            continue
        if since_ts is not None:
            ts = _parse_iso_ts(ev.get("received_at"))
            if ts is None or ts <= float(since_ts):
                continue
        if not _match_event(ev):
            continue
        picked_rev.append(ev)
        if len(picked_rev) >= int(max_count):
            break
    picked_rev.reverse()
    return picked_rev


def get_lifecycle_from_queue(
    plugin_id: Optional[str] = None,
    max_count: int | None = None,
    filter: Optional[Dict[str, Any]] = None,
    strict: bool = True,
    since_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    if max_count is None:
        max_count = MESSAGE_QUEUE_DEFAULT_MAX_COUNT

    store_size = 0
    try:
        store_size = int(state.lifecycle_store_len())
    except Exception:
        store_size = 0
    flt = dict(filter) if isinstance(filter, dict) else {}
    if plugin_id is None and isinstance(flt.get("plugin_id"), str) and flt.get("plugin_id"):
        plugin_id = str(flt.get("plugin_id"))
    if since_ts is None and flt.get("since_ts") is not None:
        try:
            v = flt.get("since_ts")
            if isinstance(v, (int, float, str)):
                since_ts = float(v)
        except Exception:
            since_ts = since_ts

    def _re_ok(field: str, pattern: Optional[str], value: Optional[str]) -> bool:
        if pattern is None:
            return True
        if value is None:
            return False
        try:
            return re.search(str(pattern), str(value)) is not None
        except re.error as e:
            if bool(strict):
                raise e
            return False

    def _match_lifecycle(ev: Dict[str, Any]) -> bool:
        if not flt:
            return True
        if flt.get("kind") is not None and ev.get("kind") != flt.get("kind"):
            return False
        if flt.get("type") is not None and ev.get("type") != flt.get("type"):
            return False
        if flt.get("plugin_id") is not None and ev.get("plugin_id") != flt.get("plugin_id"):
            return False
        if flt.get("source") is not None and ev.get("source") != flt.get("source"):
            return False
        if not _re_ok("kind_re", flt.get("kind_re"), ev.get("kind")):
            return False
        if not _re_ok("type_re", flt.get("type_re"), ev.get("type")):
            return False
        if not _re_ok("plugin_id_re", flt.get("plugin_id_re"), ev.get("plugin_id")):
            return False
        if not _re_ok("source_re", flt.get("source_re"), ev.get("source")):
            return False
        if not _re_ok("content_re", flt.get("content_re"), ev.get("content")):
            return False
        if flt.get("since_ts") is not None:
            ts = _parse_iso_ts(ev.get("time"))
            try:
                v = flt.get("since_ts")
                if v is None:
                    return True
                if ts is None or ts <= float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        if flt.get("until_ts") is not None:
            ts = _parse_iso_ts(ev.get("time"))
            try:
                v = flt.get("until_ts")
                if v is None:
                    return True
                if ts is None or ts > float(v):
                    return False
            except Exception as e:
                if bool(strict):
                    raise e
                return False
        return True

    picked_rev: List[Dict[str, Any]] = []
    scan_limit = int(max_count)
    if scan_limit < 0:
        scan_limit = 0
    if scan_limit <= 0:
        scan_limit = 200
    try:
        scan_limit = int(min(int(store_size or 0), max(scan_limit * 20, 2000)))
    except Exception:
        scan_limit = max(int(max_count) * 20, 2000)

    try:
        snapshot = state.list_lifecycle_records_tail(scan_limit)
    except Exception:
        try:
            snapshot = state.list_lifecycle_records()
        except Exception:
            snapshot = []

    for ev in reversed(snapshot):
        if plugin_id and ev.get("plugin_id") != plugin_id:
            continue
        if since_ts is not None:
            ts = _parse_iso_ts(ev.get("time"))
            if ts is None or ts <= float(since_ts):
                continue
        if not _match_lifecycle(ev):
            continue
        picked_rev.append(ev)
        if len(picked_rev) >= int(max_count):
            break
    picked_rev.reverse()
    return picked_rev


def delete_message_from_store(message_id: str) -> bool:
    return state.delete_message(message_id)


def delete_event_from_store(event_id: str) -> bool:
    return state.delete_event(event_id)


def delete_lifecycle_from_store(lifecycle_id: str) -> bool:
    return state.delete_lifecycle(lifecycle_id)


def _enqueue_event(event: Dict[str, Any]) -> None:
    """
    将事件加入事件队列（非阻塞，失败不影响主流程）
    
    注意：此函数设计为静默失败，因为事件队列不是关键路径
    """
    try:
        if state.event_queue:
            state.event_queue.put_nowait(event)
        if isinstance(event, dict):
            ev = dict(event)
            if not isinstance(ev.get("trace_id"), str) or not ev.get("trace_id"):
                ev["trace_id"] = str(uuid.uuid4())
            if not isinstance(ev.get("event_id"), str) or not ev.get("event_id"):
                ev["event_id"] = ev.get("trace_id")
            if not isinstance(ev.get("received_at"), str) or not ev.get("received_at"):
                ev["received_at"] = now_iso()
            state.append_event_record(ev)
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
        if isinstance(event, dict):
            ev = dict(event)
            if not isinstance(ev.get("trace_id"), str) or not ev.get("trace_id"):
                ev["trace_id"] = str(uuid.uuid4())
            if not isinstance(ev.get("lifecycle_id"), str) or not ev.get("lifecycle_id"):
                ev["lifecycle_id"] = ev.get("trace_id")
            if not isinstance(ev.get("time"), str) or not ev.get("time"):
                ev["time"] = now_iso()
            state.append_lifecycle_record(ev)
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

