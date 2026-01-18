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
from loguru import logger as loguru_logger

from plugin.core.state import state
from plugin.api.models import (
    PluginPushMessageResponse,
)
from plugin.api.models import PluginPushMessageRequest
from plugin.api.exceptions import (
    PluginError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
)
from plugin.server.error_handler import handle_plugin_error
from plugin.server.utils import now_iso
from plugin.utils.logging import format_log_text as _format_log_text
from plugin.settings import (
    PLUGIN_EXECUTION_TIMEOUT,
    MESSAGE_QUEUE_DEFAULT_MAX_COUNT,
    MESSAGE_SCHEMA_STRICT,
    MESSAGE_SCHEMA_ALLOW_UNSAFE,
    MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS,
)
from plugin.sdk.errors import ErrorCode
from plugin.sdk.responses import fail, is_envelope

logger = loguru_logger

_mq_full_last_warn_ts: float = 0.0
_mq_full_dropped: int = 0

_msg_push_last_info_ts: float = 0.0
_msg_push_suppressed: int = 0

_msg_schema_unknown_last_warn_ts: float = 0.0
_msg_schema_unknown_suppressed: int = 0
_msg_schema_unknown_last_fields: Optional[tuple[str, ...]] = None

_push_seq_lock = threading.Lock()
_push_expected_seq: Dict[str, int] = {}


def validate_and_advance_push_seq(*, plugin_id: str, seq: int) -> None:
    pid = str(plugin_id)
    try:
        seq_i = int(seq)
    except Exception:
        return
    with _push_seq_lock:
        expected = _push_expected_seq.get(pid)
        if expected is None:
            _push_expected_seq[pid] = seq_i + 1
            return
        if seq_i != int(expected):
            logger.opt(exception=True).warning(
                "[MESSAGE PUSH] seq mismatch: plugin={} expected={} got={}",
                pid,
                int(expected),
                seq_i,
            )
            _push_expected_seq[pid] = seq_i + 1
            return
        _push_expected_seq[pid] = int(expected) + 1


def validate_and_advance_push_seq_batch(*, plugin_id: str, seq_list: List[int]) -> None:
    pid = str(plugin_id)
    if not seq_list:
        return
    try:
        seqs = [int(s) for s in seq_list]
    except Exception as e:
        raise ValueError(f"invalid seq_list: {e}") from e
    try:
        expected = int(_push_expected_seq.get(pid, 0))
    except Exception:
        expected = 0
    first = int(seqs[0])
    if first != expected:
        raise ValueError(f"push seq mismatch: expected={expected} got={first}")
    for i in range(1, len(seqs)):
        if int(seqs[i]) != int(seqs[i - 1]) + 1:
            raise ValueError(f"push seq not contiguous at index={i} prev={seqs[i-1]} got={seqs[i]}")
    _push_expected_seq[pid] = int(seqs[-1]) + 1


def validate_and_advance_push_seq_range(*, plugin_id: str, first_seq: int, last_seq: int, count: int) -> None:
    pid = str(plugin_id)
    try:
        first_i = int(first_seq)
        last_i = int(last_seq)
        n = int(count)
    except Exception as e:
        raise ValueError(f"invalid seq range args: {e}") from e
    if n <= 0:
        return
    if last_i < first_i:
        raise ValueError(f"invalid seq range: first={first_i} last={last_i}")
    if (last_i - first_i + 1) != n:
        raise ValueError(f"seq range length mismatch: first={first_i} last={last_i} count={n}")
    try:
        expected = int(_push_expected_seq.get(pid, 0))
    except Exception:
        expected = 0
    if first_i != expected:
        raise ValueError(f"push seq mismatch: expected={expected} got={first_i}")
    _push_expected_seq[pid] = int(last_i) + 1


_BUS_INGESTION_ENABLED = os.getenv("NEKO_BUS_INGESTION_LOOP", "1").lower() in ("1", "true", "yes", "on")
_bus_ingest_stop: Optional[asyncio.Event] = None
_bus_ingest_task: Optional[asyncio.Task[None]] = None


def _warn_unknown_message_fields(*, unknown: List[str], mode: str) -> None:
    if not MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS:
        return
    if not unknown:
        return
    global _msg_schema_unknown_last_warn_ts, _msg_schema_unknown_suppressed, _msg_schema_unknown_last_fields
    try:
        fields_key = tuple(sorted(set(str(x) for x in unknown if x)))
    except Exception:
        fields_key = None
    _msg_schema_unknown_suppressed += 1
    now_ts = time.time()
    if (now_ts - float(_msg_schema_unknown_last_warn_ts)) < 1.0 and _msg_schema_unknown_last_fields == fields_key:
        return
    _msg_schema_unknown_last_warn_ts = float(now_ts)
    _msg_schema_unknown_last_fields = fields_key
    n = int(_msg_schema_unknown_suppressed)
    _msg_schema_unknown_suppressed = 0
    try:
        logger.warning(
            "[MESSAGE SCHEMA] Unknown fields detected (mode={} fields={} count={})",
            str(mode),
            list(fields_key or ()),
            n,
        )
    except Exception:
        pass


def _validate_message_payload(payload: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    """Validate message payload.

    - Default: strict enabled, reject invalid/missing required fields.
    - If payload['unsafe']=True and MESSAGE_SCHEMA_ALLOW_UNSAFE is enabled: skip strict validation,
      but still enforce minimal invariants to keep runtime stable.
    - Unknown fields: warn (rate-limited) but do not reject.
    """
    if not isinstance(payload, dict):
        raise ValueError("message payload must be a dict")

    allowed = {
        # envelope / runtime fields (not part of SDK schema but present in bus records)
        "type",
        "message_id",
        "time",
        "_bus_stored",

        "plugin_id",
        "source",
        "description",
        "priority",
        "message_type",
        "content",
        "binary_data",
        "binary_url",
        "metadata",
        "unsafe",
    }
    try:
        unknown = [k for k in payload.keys() if k not in allowed]
    except Exception:
        unknown = []
    _warn_unknown_message_fields(unknown=unknown, mode=mode)

    unsafe = bool(payload.get("unsafe"))
    if unsafe and MESSAGE_SCHEMA_ALLOW_UNSAFE:
        # Minimal invariants (do not allow runtime errors downstream)
        plugin_id = payload.get("plugin_id")
        source = payload.get("source")
        message_type = payload.get("message_type")
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ValueError("plugin_id is required")
        if not isinstance(source, str) or not source:
            raise ValueError("source is required")
        if not isinstance(message_type, str) or not message_type:
            raise ValueError("message_type is required")
        md = payload.get("metadata")
        if md is not None and not isinstance(md, dict):
            raise ValueError("metadata must be a dict")
        return payload

    if not MESSAGE_SCHEMA_STRICT:
        return payload

    # Strict path: use Pydantic to validate types + cross-field constraints.
    data = {
        "plugin_id": payload.get("plugin_id"),
        "source": payload.get("source"),
        "description": payload.get("description", ""),
        "priority": payload.get("priority", 0),
        "message_type": payload.get("message_type"),
        "content": payload.get("content"),
        "binary_data": payload.get("binary_data"),
        "binary_url": payload.get("binary_url"),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "unsafe": False,
    }
    req = PluginPushMessageRequest.model_validate(data)
    out = req.model_dump()
    return out


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


def _drain_bus_queue_once(q: Any, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if q is None:
        return out
    n = int(limit)
    if n <= 0:
        return out
    for _ in range(n):
        try:
            item = q.get_nowait()
        except (asyncio.QueueEmpty, _queue.Empty):
            break
        except Exception:
            break
        if isinstance(item, dict):
            out.append(item)
    return out


def _ingest_normalize_and_store_message(msg: Dict[str, Any]) -> None:
    if msg.get("_bus_stored") is True:
        return
    if not isinstance(msg.get("message_id"), str) or not msg.get("message_id"):
        msg = dict(msg)
        msg["message_id"] = str(uuid.uuid4())
    if not isinstance(msg.get("time"), str) or not msg.get("time"):
        msg = dict(msg)
        msg["time"] = now_iso()
    state.append_message_record(msg)


def _ingest_normalize_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(msg, dict):
        return None
    if msg.get("_bus_stored") is True:
        return None
    # Validate early to avoid polluting the bus store.
    try:
        _ = _validate_message_payload(msg, mode="ingest")
    except Exception as e:
        try:
            logger.warning("[MESSAGE SCHEMA] Dropped invalid message in ingest: {}", e)
        except Exception:
            pass
        return None
    out = msg
    if not isinstance(out.get("message_id"), str) or not out.get("message_id"):
        out = dict(out)
        out["message_id"] = str(uuid.uuid4())
    if not isinstance(out.get("time"), str) or not out.get("time"):
        if out is msg:
            out = dict(out)
        out["time"] = now_iso()
    return out


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


def _bus_ingest_flush_once(limit_per_queue: int = 4096) -> int:
    drained = 0
    msgs_raw = _drain_bus_queue_once(getattr(state, "message_queue", None), limit_per_queue)
    if msgs_raw:
        msgs: List[Dict[str, Any]] = []
        for m in msgs_raw:
            nm = _ingest_normalize_message(m)
            if nm is not None:
                msgs.append(nm)
        if msgs:
            try:
                drained += int(getattr(state, "extend_message_records")(msgs))
            except Exception:
                for m in msgs:
                    _ingest_normalize_and_store_message(m)
                    drained += 1

    evs_raw = _drain_bus_queue_once(getattr(state, "event_queue", None), limit_per_queue)
    if evs_raw:
        evs: List[Dict[str, Any]] = []
        for e in evs_raw:
            ne = _ingest_normalize_event(e)
            if ne is not None:
                evs.append(ne)
        if evs:
            try:
                drained += int(getattr(state, "extend_event_records")(evs))
            except Exception:
                for e in evs:
                    _ingest_normalize_and_store_event(e)
                    drained += 1

    lfs_raw = _drain_bus_queue_once(getattr(state, "lifecycle_queue", None), limit_per_queue)
    if lfs_raw:
        lfs: List[Dict[str, Any]] = []
        for e in lfs_raw:
            ne = _ingest_normalize_lifecycle(e)
            if ne is not None:
                lfs.append(ne)
        if lfs:
            try:
                drained += int(getattr(state, "extend_lifecycle_records")(lfs))
            except Exception:
                for e in lfs:
                    _ingest_normalize_and_store_lifecycle(e)
                    drained += 1
    return drained


async def start_bus_ingestion_loop() -> None:
    global _bus_ingest_stop, _bus_ingest_task
    if not _BUS_INGESTION_ENABLED:
        return
    if _bus_ingest_task is not None and not _bus_ingest_task.done():
        return
    _bus_ingest_stop = asyncio.Event()

    async def _loop() -> None:
        assert _bus_ingest_stop is not None
        last_stats_ts = time.monotonic()
        drained_in_window = 0
        while not _bus_ingest_stop.is_set():
            drained = 0
            t0 = time.monotonic()
            # Time-bounded flush to avoid starving the event loop.
            while True:
                drained += _bus_ingest_flush_once(limit_per_queue=2048)
                if drained == 0:
                    break
                if (time.monotonic() - t0) >= 0.005:
                    break

            drained_in_window += drained
            now = time.monotonic()
            if (now - last_stats_ts) >= 5.0:
                last_stats_ts = now
                try:
                    qsz = None
                    try:
                        qsz = int(state.message_queue.qsize())
                    except Exception:
                        qsz = None
                    logger.debug(
                        "[bus.ingest] drained_5s={} message_queue_size={}",
                        int(drained_in_window),
                        qsz,
                    )
                except Exception:
                    pass
                drained_in_window = 0

            if drained > 0:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.005)

    _bus_ingest_task = asyncio.create_task(_loop(), name="bus-ingestion-loop")


async def stop_bus_ingestion_loop() -> None:
    global _bus_ingest_stop, _bus_ingest_task
    if _bus_ingest_stop is not None:
        try:
            _bus_ingest_stop.set()
        except Exception:
            pass
    if _bus_ingest_task is not None:
        try:
            await _bus_ingest_task
        except Exception:
            pass
    _bus_ingest_stop = None
    _bus_ingest_task = None
    # Final flush to minimize loss on shutdown.
    try:
        _bus_ingest_flush_once(limit_per_queue=1000000)
    except Exception:
        pass


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


async def trigger_plugin(
    plugin_id: str,
    entry_id: str,
    args: Dict[str, Any],
    task_id: Optional[str] = None,
    client_host: Optional[str] = None,
) -> Dict[str, Any]:
    """
    触发插件执行
    
    Args:
        plugin_id: 插件ID
        entry_id: 入口点ID
        args: 参数
        task_id: 任务ID（可选）
        client_host: 客户端主机（可选）
    
    Returns:
        Dict[str, Any]
    
    Raises:
        HTTPException: 如果插件不存在或执行失败
    """
    # 关键日志：记录触发请求
    logger.info(
        "[plugin_trigger] Processing trigger: plugin_id={}, entry_id={}, task_id={}",
        plugin_id,
        entry_id,
        task_id,
    )
    
    # 详细参数信息使用 DEBUG
    logger.debug(
        "[plugin_trigger] Args: type={}, keys={}, content={}",
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

        return {
            "success": False,
            "plugin_id": plugin_id,
            "executed_entry": entry_id,
            "args": args,
            "plugin_response": plugin_response,
            "received_at": event["received_at"],
            "plugin_forward_error": None,
        }
    
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
            return {
                "success": False,
                "plugin_id": plugin_id,
                "executed_entry": entry_id,
                "args": args,
                "plugin_response": plugin_response,
                "received_at": event["received_at"],
                "plugin_forward_error": None,
            }
    except (AttributeError, RuntimeError) as e:
        logger.opt(exception=True).error(f"Failed to check health for plugin {plugin_id}: {e}")
        plugin_response = fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health check failed",
            details={"error": str(e)},
            retriable=True,
            trace_id=trace_id,
        )
        return {
            "success": False,
            "plugin_id": plugin_id,
            "executed_entry": entry_id,
            "args": args,
            "plugin_response": plugin_response,
            "received_at": event["received_at"],
            "plugin_forward_error": None,
        }
    
    # 执行插件
    plugin_response: Any = None
    
    logger.debug(
        "[plugin_trigger] Calling host.trigger: entry_id={}, args={}",
        entry_id,
        args,
    )
    
    try:
        plugin_response = await host.trigger(entry_id, args, timeout=PLUGIN_EXECUTION_TIMEOUT)
        logger.debug(
            "[plugin_trigger] Plugin response: {}",
            str(plugin_response)[:500] if plugin_response else None,
        )
    except (TimeoutError, asyncio.TimeoutError) as e:
        logger.opt(exception=True).error(f"Plugin {plugin_id} entry {entry_id} timed out: {e}")
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
    
    return {
        "success": bool(plugin_response.get("success")) if isinstance(plugin_response, dict) else False,
        "plugin_id": plugin_id,
        "executed_entry": entry_id,
        "args": args,
        "plugin_response": plugin_response,
        "received_at": event["received_at"],
        "plugin_forward_error": None,
    }


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

    if not _BUS_INGESTION_ENABLED:
        # Drain queue into store (queue is an ingestion channel; store is the authoritative history)
        while True:
            try:
                msg = state.message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if not isinstance(msg, dict):
                continue
            _ingest_normalize_and_store_message(msg)

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

    if not _BUS_INGESTION_ENABLED:
        while True:
            try:
                ev = state.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not isinstance(ev, dict):
                continue
            _ingest_normalize_and_store_event(ev)

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

    if not _BUS_INGESTION_ENABLED:
        while True:
            try:
                ev = state.lifecycle_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not isinstance(ev, dict):
                continue
            _ingest_normalize_and_store_lifecycle(ev)

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
    unsafe: bool = False,
    mode: str = "unknown",
) -> str:
    """
    将消息推送到队列
    
    Returns:
        message_id
    """
    message_id = str(uuid.uuid4())
    message = {
        "type": "MESSAGE_PUSH",
        "message_id": message_id,
        "plugin_id": plugin_id,
        "source": source,
        "description": description,
        "priority": priority,
        "message_type": message_type,
        "content": content,
        "binary_data": binary_data,
        "binary_url": binary_url,
        "metadata": metadata or {},
        "unsafe": bool(unsafe),
        "time": now_iso(),
        "_bus_stored": True,
    }

    # Validate schema (strict by default; may be bypassed when unsafe is enabled).
    try:
        validated = _validate_message_payload(message, mode=str(mode))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Apply validated values back to the stored message to keep bus records consistent.
    # (Keep envelope fields like type/message_id/time as-is.)
    try:
        if isinstance(validated, dict):
            message["plugin_id"] = validated.get("plugin_id", message.get("plugin_id"))
            message["source"] = validated.get("source", message.get("source"))
            message["description"] = validated.get("description", message.get("description"))
            message["priority"] = validated.get("priority", message.get("priority"))
            message["message_type"] = validated.get("message_type", message.get("message_type"))
            message["content"] = validated.get("content", message.get("content"))
            message["binary_data"] = validated.get("binary_data", message.get("binary_data"))
            message["binary_url"] = validated.get("binary_url", message.get("binary_url"))
            message["metadata"] = validated.get("metadata", message.get("metadata")) or {}
    except Exception:
        pass
    
    try:
        state.message_queue.put_nowait(message)
        state.append_message_record(message)
        global _msg_push_last_info_ts, _msg_push_suppressed
        now_ts = time.time()
        _msg_push_suppressed += 1
        if now_ts - float(_msg_push_last_info_ts) >= 1.0:
            _msg_push_last_info_ts = float(now_ts)
            n = int(_msg_push_suppressed)
            _msg_push_suppressed = 0
            if n <= 1:
                loguru_logger.info(
                    f"[MESSAGE PUSH] mode={mode} Plugin: {plugin_id} | "
                    f"Source: {source} | "
                    f"Type: {message_type} | "
                    f"Priority: {priority} | "
                    f"Description: {description} | "
                    f"Content: {_format_log_text(content or '')}"
                )
            else:
                loguru_logger.info(
                    f"[MESSAGE PUSH] mode={mode} Plugin: {plugin_id} | "
                    f"Source: {source} | "
                    f"Type: {message_type} | "
                    f"Priority: {priority} | "
                    f"Description: {description} | "
                    f"Content: {_format_log_text(content or '')} (x{n})"
                )
    except asyncio.QueueFull:
        # 队列满时，尝试移除最旧的消息
        try:
            state.message_queue.get_nowait()
            state.message_queue.put_nowait(message)
            global _mq_full_last_warn_ts, _mq_full_dropped
            _mq_full_dropped += 1
            now_ts = time.time()
            if now_ts - float(_mq_full_last_warn_ts) >= 1.0:
                _mq_full_last_warn_ts = float(now_ts)
                dropped = int(_mq_full_dropped)
                _mq_full_dropped = 0
                if dropped <= 1:
                    loguru_logger.warning("Message queue full, dropped oldest message")
                else:
                    loguru_logger.warning("Message queue full, dropped oldest message (x{})", dropped)
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


def push_messages_to_queue_batch(
    *,
    plugin_id: str,
    items: List[Dict[str, Any]],
    mode: str = "unknown",
) -> int:
    if not isinstance(items, list) or not items:
        return 0

    pid = str(plugin_id)
    now_s = now_iso()
    fast = str(mode) == "zmq-fast"
    out_records: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = it.get("message_id")
        if not isinstance(mid, str) or not mid:
            mid = str(uuid.uuid4())
        t = it.get("time")
        if not isinstance(t, str) or not t:
            t = now_s
        msg = {
            "type": "MESSAGE_PUSH",
            "message_id": mid,
            "plugin_id": pid,
            "source": it.get("source") if isinstance(it.get("source"), str) else "",
            "description": it.get("description") if isinstance(it.get("description"), str) else "",
            "priority": it.get("priority", 0),
            "message_type": it.get("message_type") if isinstance(it.get("message_type"), str) else "",
            "content": it.get("content"),
            "binary_data": it.get("binary_data"),
            "binary_url": it.get("binary_url"),
            "metadata": it.get("metadata") if isinstance(it.get("metadata"), dict) else {},
            "unsafe": True,
            "time": t,
            "_bus_stored": True,
        }
        if fast:
            # Fast path: avoid per-item pydantic/field-scan overhead.
            # Enforce minimal invariants inline.
            if not msg.get("source") or not msg.get("message_type"):
                continue
            if not isinstance(msg.get("metadata"), dict):
                msg["metadata"] = {}
        else:
            try:
                _ = _validate_message_payload(msg, mode=str(mode))
            except Exception:
                continue
        out_records.append(msg)

    if not out_records:
        return 0

    if not fast:
        try:
            for rec in out_records:
                try:
                    state.message_queue.put_nowait(rec)
                except Exception:
                    break
        except Exception:
            pass

    stored = 0
    try:
        # For fast mode, avoid per-message bus_change notifications.
        # A single coalesced notification (single rev bump) drastically reduces overhead.
        if str(mode) == "zmq-fast":
            stored = int(state.extend_message_records_coalesced(out_records))
        else:
            stored = int(state.extend_message_records(out_records))
    except Exception:
        try:
            for rec in out_records:
                state.append_message_record(rec)
                stored += 1
        except Exception:
            stored = int(stored)

    try:
        global _msg_push_last_info_ts, _msg_push_suppressed
        now_ts = time.time()
        _msg_push_suppressed += int(stored)
        if now_ts - float(_msg_push_last_info_ts) >= 1.0:
            _msg_push_last_info_ts = float(now_ts)
            n = int(_msg_push_suppressed)
            _msg_push_suppressed = 0
            loguru_logger.info(
                f"[MESSAGE PUSH] mode={mode} Plugin: {pid} | "
                f"Content: (batch x{n})"
            )
    except Exception:
        pass

    return int(stored)


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

