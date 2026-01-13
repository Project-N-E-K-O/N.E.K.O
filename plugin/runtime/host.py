from __future__ import annotations

import asyncio
import importlib
import inspect
import multiprocessing
import os
import sys
import threading
import time
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Type
from multiprocessing import Queue
from queue import Empty

from loguru import logger as loguru_logger

from plugin.sdk.events import EVENT_META_ATTR, EventHandler
from plugin.core.state import state
from plugin.core.context import PluginContext
from plugin.runtime.communication import PluginCommunicationResourceManager
from plugin.api.models import HealthCheckResponse
from plugin.api.exceptions import (
    PluginLifecycleError,
    PluginTimerError,
    PluginEntryNotFoundError,
    PluginExecutionError,
    PluginError,
)
from plugin.settings import (
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
    PROCESS_TERMINATE_TIMEOUT,
)


def _sanitize_plugin_id(raw: Any, max_len: int = 64) -> str:
    """
    Produce a filesystem- and identifier-safe plugin ID derived from the given input.
    
    Parameters:
        raw (Any): Value to convert into a safe plugin identifier. It will be stringified before sanitization.
        max_len (int): Maximum allowed length of the resulting identifier (default 64).
    
    Returns:
        str: A sanitized identifier that:
            - preserves ASCII alphanumeric characters, dash (-), and underscore (_);
            - replaces all other characters with underscores;
            - strips leading and trailing dashes/underscores;
            - if empty after stripping, is replaced with a 16-character SHA-256 hex digest of the input;
            - if longer than `max_len`, is truncated and suffixed with a 12-character SHA-256 hex digest such that the total length does not exceed `max_len`.
    """
    s = str(raw)
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in s)
    safe = safe.strip("_-")
    if not safe:
        safe = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if len(safe) > max_len:
        digest = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]
        safe = f"{safe[:max_len - 13]}_{digest}"
    return safe


def _plugin_process_runner(
    plugin_id: str,
    entry_point: str,
    config_path: Path,
    cmd_queue: Queue,
    res_queue: Queue,
    status_queue: Queue,
    message_queue: Queue,
    response_queue: Queue,
    stop_event: Any | None = None,
    plugin_comm_queue: Queue | None = None,
) -> None:
    """
    Run a plugin inside a separate process: load the plugin module/class, instantiate it with a PluginContext, map entry points and events, start lifecycle handlers/timers/custom auto-start events, and execute incoming commands until shutdown.
    
    This function performs all plugin-process responsibilities and communicates with the parent process exclusively via the provided multiprocessing queues. It loads the plugin specified by `entry_point`, populates the PluginContext with queues and adapters (optionally a ZeroMQ IPC client), executes lifecycle startup and shutdown handlers, launches timer and auto-start custom event threads, and processes command messages (e.g., STOP, TRIGGER, TRIGGER_CUSTOM, BUS_CHANGE) from `cmd_queue`, sending results or error payloads to `res_queue`. The function respects an external stop event when present and attempts best-effort cleanup of threads and queues on termination; on an unexpected crash it will attempt to push a CRASH response to `res_queue` before re-raising.
    
    Parameters:
        plugin_id (str): Identifier for the plugin; used for logging and workspace naming.
        entry_point (str): Import path and class name in the form "module.path:ClassName".
        config_path (Path): Path to the plugin configuration file (used to locate project root).
        cmd_queue (Queue): Incoming command queue; expected message types include "STOP", "TRIGGER", "TRIGGER_CUSTOM", and "BUS_CHANGE".
        res_queue (Queue): Outgoing result queue; receives response payloads with keys like `req_id`, `success`, `data`, and `error`.
        status_queue (Queue): Queue for emitting status updates (passed into PluginContext).
        message_queue (Queue): Queue for plugin-generated messages (passed into PluginContext).
        response_queue (Queue): Per-plugin response queue reference stored on the PluginContext for internal coordination.
        stop_event (Any | None): Optional multiprocessing.Event-like object that, if set, requests prompt shutdown of the process (best-effort).
        plugin_comm_queue (Queue | None): Optional queue used for plugin-host communication resources (attached to the PluginContext).
    
    Note:
        - The function does not return a value; it runs until it receives a STOP command, the stop_event is set, or an unrecoverable exception occurs.
        - Command and response payload shapes are observable from the message handling logic (e.g., TRIGGER/TRIGGER_CUSTOM expect `entry_id`/`event_type`/`event_id`, `args` and `req_id`).
    """
    # 获取项目根目录（假设 config_path 在 plugin/plugins/xxx/plugin.toml）
    # 由于部署/启动方式可能改变工作目录与 sys.path，使用“向上探测”确保能找到仓库根。
    def _find_project_root(p: Path) -> Path:
        """
        Locate the repository project root directory that contains both "plugin" and "utils" subdirectories.
        
        Searches upward from the given path (file or directory) for an ancestor directory containing both "plugin" and "utils". If no such ancestor is found, returns a best-effort fallback: the ancestor expected for a layout like "plugin/plugins/<id>/plugin.toml", or the provided path's parent as a last resort.
        
        Parameters:
            p (Path): Starting path (file or directory) to begin the search from.
        
        Returns:
            Path: The discovered project root directory, or a fallback directory if the canonical root cannot be found.
        """
        cur = p.resolve()
        try:
            if cur.is_file():
                cur = cur.parent
        except Exception:
            pass
        for _ in range(10):
            try:
                candidate = cur
                # Repo root should contain both plugin/ and utils/.
                if (candidate / "plugin").is_dir() and (candidate / "utils").is_dir():
                    return candidate
            except Exception:
                pass
            if cur.parent == cur:
                break
            cur = cur.parent
        # Fallback: assume layout plugin/plugins/<id>/plugin.toml
        try:
            loguru_logger.debug(
                "[Plugin Process] Could not find project root via exploration from %s; using fallback pattern",
                p,
            )
        except Exception:
            # Logging should never prevent fallback resolution
            pass
        try:
            return p.parent.parent.parent.parent.resolve()
        except Exception:
            return p.parent.resolve()

    # Preserve the process-level stop event passed from the parent. Do not reuse this name
    # for other purposes, otherwise the out-of-band shutdown signal may stop working.
    process_stop_event = stop_event

    project_root = _find_project_root(config_path)
    
    # 配置loguru logger for plugin process
    from loguru import logger
    # 移除默认handler
    logger.remove()
    # 绑定插件ID到logger上下文
    logger = logger.bind(plugin_id=plugin_id)
    # 添加控制台输出
    logger.add(
        sys.stdout,
        format=f"<green>{{time:YYYY-MM-DD HH:mm:ss}}</green> | <level>{{level: <8}}</level> | [Proc-{_sanitize_plugin_id(plugin_id)}] <level>{{message}}</level>",
        level="INFO",
        colorize=True,
    )
    # 添加文件输出（使用项目根目录的log目录）
    safe_pid = _sanitize_plugin_id(plugin_id)
    log_dir = project_root / "log" / "plugins" / safe_pid
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{safe_pid}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    logger.add(
        str(log_file),
        format=f"{{time:YYYY-MM-DD HH:mm:ss}} | {{level: <8}} | [Proc-{safe_pid}] {{message}}",
        level="INFO",
        rotation="10 MB",
        retention=10,
        encoding="utf-8",
    )
    
    # 拦截标准库 logging 并转发到 loguru
    try:
        import logging
        
        # 确保项目根目录在 path 中，以便能导入 utils
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            
        logger.info("[Plugin Process] Resolved project_root: {}", project_root)
        logger.info("[Plugin Process] Python path (head): {}", sys.path[:3])

        handler_cls: Optional[Type[logging.Handler]] = None
        try:
            import utils.logger_config as _lc

            handler_cls = getattr(_lc, "InterceptHandler", None)
        except Exception:
            handler_cls = None

        if handler_cls is None:
            class _InterceptHandler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    """
                    Forward a standard logging.LogRecord into the configured loguru logger, preserving level, message, and exception information.
                    
                    Parameters:
                        record (logging.LogRecord): The log record to forward to loguru.
                    """
                    try:
                        level = record.levelname
                        msg = record.getMessage()
                        logger.opt(depth=6, exception=record.exc_info).log(level, msg)
                    except Exception:
                        pass

            handler_cls = _InterceptHandler

        logging.basicConfig(handlers=[handler_cls()], level=0, force=True)

        # 显式设置 uvicorn logger，并禁止传播以避免重复
        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
            logging_logger = logging.getLogger(logger_name)
            logging_logger.handlers = [handler_cls()]
            logging_logger.propagate = False
        
        logger.info("[Plugin Process] Standard logging intercepted and redirected to loguru")
    except Exception as e:
        logger.warning("[Plugin Process] Failed to setup logging interception: {}", e)

    try:
        # 设置 Python 路径，确保能够导入插件模块
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            logger.info("[Plugin Process] Added project root to sys.path: {}", project_root)
        
        logger.info("[Plugin Process] Starting plugin process for {}", plugin_id)
        logger.info("[Plugin Process] Entry point: {}", entry_point)
        logger.info("[Plugin Process] Config path: {}", config_path)
        logger.info("[Plugin Process] Current working directory: {}", os.getcwd())
        logger.info("[Plugin Process] Python path: {}", sys.path[:3])  # 只显示前3个路径
        
        module_path, class_name = entry_point.split(":", 1)
        logger.info("[Plugin Process] Importing module: {}", module_path)
        mod = importlib.import_module(module_path)
        logger.info("[Plugin Process] Module imported successfully: {}", module_path)
        logger.info("[Plugin Process] Getting class: {}", class_name)
        cls = getattr(mod, class_name)
        logger.info("[Plugin Process] Class found: {}", cls)

        # 注意：_entry_map 和 _instance 在 PluginContext 中定义为 Optional，
        # 这里先设置为 None，在创建 instance 和扫描入口映射后再设置。
        # 在设置之前，context 的方法不应该访问这些属性。
        ctx = PluginContext(
            plugin_id=plugin_id,
            logger=logger,
            config_path=config_path,
            status_queue=status_queue,
            message_queue=message_queue,
            _plugin_comm_queue=plugin_comm_queue,
            _zmq_ipc_client=None,
            _cmd_queue=cmd_queue,  # 传递命令队列，用于在等待期间处理命令
            _res_queue=res_queue,  # 传递结果队列，用于在等待期间处理响应
            _response_queue=response_queue,
            _response_pending={},
            _entry_map=None,  # 将在创建 instance 后设置（见下方第116行）
            _instance=None,  # 将在创建 instance 后设置（见下方第117行）
        )

        try:
            from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

            if PLUGIN_ZMQ_IPC_ENABLED:
                from plugin.zeromq_ipc import ZmqIpcClient

                ctx._zmq_ipc_client = ZmqIpcClient(plugin_id=plugin_id, endpoint=PLUGIN_ZMQ_IPC_ENDPOINT)
                try:
                    logger.info("[Plugin Process] ZeroMQ IPC enabled: {}", PLUGIN_ZMQ_IPC_ENDPOINT)
                except Exception:
                    pass
        except Exception:
            try:
                logger.warning("[Plugin Process] ZeroMQ IPC enabled but client init failed")
            except Exception:
                pass
            pass
        instance = cls(ctx)

        entry_map: Dict[str, Any] = {}
        events_by_type: Dict[str, Dict[str, Any]] = {}

        # 扫描方法映射
        for name, member in inspect.getmembers(instance, predicate=callable):
            if name.startswith("_") and not hasattr(member, EVENT_META_ATTR):
                continue
            event_meta = getattr(member, EVENT_META_ATTR, None)
            if not event_meta:
                wrapped = getattr(member, "__wrapped__", None)
                if wrapped is not None:
                    event_meta = getattr(wrapped, EVENT_META_ATTR, None)

            if event_meta:
                eid = getattr(event_meta, "id", name)
                entry_map[eid] = member
                etype = getattr(event_meta, "event_type", "plugin_entry")
                events_by_type.setdefault(etype, {})
                events_by_type[etype][eid] = member
            else:
                entry_map[name] = member

        logger.info("Plugin instance created. Mapped entries: {}", list(entry_map.keys()))
        
        # 设置入口映射和实例到上下文，用于在等待期间处理命令
        # _cmd_queue 和 _res_queue 已在 PluginContext 构造函数中初始化
        ctx._entry_map = entry_map
        ctx._instance = instance

        # 生命周期：startup
        lifecycle_events = events_by_type.get("lifecycle", {})
        startup_fn = lifecycle_events.get("startup")
        if startup_fn:
            try:
                with ctx._handler_scope("lifecycle.startup"):
                    if asyncio.iscoroutinefunction(startup_fn):
                        asyncio.run(startup_fn())
                    else:
                        startup_fn()
            except (KeyboardInterrupt, SystemExit):
                # 系统级中断，直接抛出
                raise
            except Exception as e:
                error_msg = f"Error in lifecycle.startup: {str(e)}"
                logger.exception(error_msg)
                # 记录错误但不中断进程启动
                # 如果启动失败是致命的，可以在这里 raise PluginLifecycleError

        # 定时任务：timer auto_start interval
        def _run_timer_interval(fn, interval_seconds: int, fn_name: str, stop_event: threading.Event):
            """
            Continuously invokes the provided handler at a fixed interval until the stop event is set.
            
            Supports coroutine functions (they will be awaited) and logs errors without stopping the loop. A KeyboardInterrupt or SystemExit will stop the timer loop.
            
            Parameters:
                fn (Callable): The handler to execute on each interval; may be a coroutine function or regular callable.
                interval_seconds (int): Seconds to wait between handler invocations.
                fn_name (str): Human-readable name used in logging for this timer.
                stop_event (threading.Event): Event used to request termination of the timer loop.
            """
            while not stop_event.is_set():
                try:
                    with ctx._handler_scope(f"timer.{fn_name}"):
                        if asyncio.iscoroutinefunction(fn):
                            asyncio.run(fn())
                        else:
                            fn()
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，停止定时任务
                    logger.info("Timer '{}' interrupted, stopping", fn_name)
                    break
                except Exception:
                    logger.exception("Timer '{}' failed", fn_name)
                    # 定时任务失败不应中断循环，继续执行
                stop_event.wait(interval_seconds)

        timer_events = events_by_type.get("timer", {})
        timer_stop_events: list[threading.Event] = []
        for eid, fn in timer_events.items():
            meta = getattr(fn, EVENT_META_ATTR, None)
            if not meta or not getattr(meta, "auto_start", False):
                continue
            mode = getattr(meta, "extra", {}).get("mode")
            if mode == "interval":
                seconds = getattr(meta, "extra", {}).get("seconds", 0)
                if seconds > 0:
                    timer_stop_event = threading.Event()
                    timer_stop_events.append(timer_stop_event)
                    t = threading.Thread(
                        target=_run_timer_interval,
                        args=(fn, seconds, eid, timer_stop_event),
                        daemon=True,
                    )
                    t.start()
                    logger.info("Started timer '{}' every {}s", eid, seconds)

        # 处理自定义事件：自动启动
        def _run_custom_event_auto(fn, fn_name: str, event_type: str):
            """
            Run an auto-start custom event handler within the plugin handler scope.
            
            Parameters:
                fn (callable): The event handler to execute; may be an async coroutine function or a regular function.
                fn_name (str): Name of the handler for scoping and log messages.
                event_type (str): Custom event type used for scoping and log messages.
            
            Notes:
                KeyboardInterrupt and SystemExit are caught and logged as interruptions; other exceptions are logged with their traceback.
            """
            try:
                with ctx._handler_scope(f"{event_type}.{fn_name}"):
                    if asyncio.iscoroutinefunction(fn):
                        asyncio.run(fn())
                    else:
                        fn()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Custom event '{}' (type: {}) interrupted", fn_name, event_type)
            except Exception:
                logger.exception("Custom event '{}' (type: {}) failed", fn_name, event_type)

        # 扫描所有自定义事件类型
        for event_type, events in events_by_type.items():
            if event_type in ("plugin_entry", "lifecycle", "message", "timer"):
                continue  # 跳过标准类型
            
            # 这是自定义事件类型
            logger.info("Found custom event type: {} with {} handlers", event_type, len(events))
            for eid, fn in events.items():
                meta = getattr(fn, EVENT_META_ATTR, None)
                if not meta:
                    continue
                
                # 处理自动启动的自定义事件
                if getattr(meta, "auto_start", False):
                    trigger_method = getattr(meta, "extra", {}).get("trigger_method", "auto")
                    if trigger_method == "auto":
                        # 在独立线程中启动
                        t = threading.Thread(
                            target=_run_custom_event_auto,
                            args=(fn, eid, event_type),
                            daemon=True,
                        )
                        t.start()
                        logger.info("Started auto custom event '{}' (type: {})", eid, event_type)

        # 命令循环
        while True:
            try:
                if process_stop_event is not None and process_stop_event.is_set():
                    break
            except Exception:
                # stop_event is best-effort; never break command loop due to errors here
                pass
            try:
                msg = cmd_queue.get(timeout=QUEUE_GET_TIMEOUT)
            except Empty:
                continue

            if msg["type"] == "STOP":
                break

            if msg["type"] == "BUS_CHANGE":
                try:
                    from plugin.sdk.bus.types import dispatch_bus_change

                    dispatch_bus_change(
                        sub_id=str(msg.get("sub_id") or ""),
                        bus=str(msg.get("bus") or ""),
                        op=str(msg.get("op") or ""),
                        delta=msg.get("delta") if isinstance(msg.get("delta"), dict) else None,
                    )
                except Exception as e:
                    logger.debug("Failed to dispatch bus change: {}", e)  
                continue

            if msg["type"] == "TRIGGER_CUSTOM":
                # 触发自定义事件（通过命令队列）
                event_type = msg.get("event_type")
                event_id = msg.get("event_id")
                args = msg.get("args", {})
                req_id = msg.get("req_id", "unknown")
                
                logger.info(
                    "[Plugin Process] Received TRIGGER_CUSTOM: plugin_id={}, event_type={}, event_id={}, req_id={}",
                    plugin_id,
                    event_type,
                    event_id,
                    req_id,
                )
                
                # 查找自定义事件处理器
                custom_events = events_by_type.get(event_type, {})
                method = custom_events.get(event_id)
                
                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
                
                try:
                    if not method:
                        ret_payload["error"] = f"Custom event '{event_type}.{event_id}' not found"
                    else:
                        # 执行自定义事件
                        logger.debug(
                            "[Plugin Process] Executing custom event {}.{}, req_id={}",
                            event_type,
                            event_id,
                            req_id,
                        )
                        if asyncio.iscoroutinefunction(method):
                            logger.debug("[Plugin Process] Custom event is async, running in thread to avoid blocking command loop")
                            # 在独立线程中运行异步方法，避免阻塞命令循环
                            # 这样命令循环可以继续处理其他命令（包括响应命令）
                            result_container = {"result": None, "exception": None, "done": False}
                            event = threading.Event()
                            
                            def _run_async_thread(method=method, args=args, result_container=result_container, event=event, event_type=event_type, event_id=event_id):
                                """
                                Execute an asynchronous handler in a thread, capture its outcome, and notify a waiting event.
                                
                                Parameters:
                                    method (Callable): The coroutine function to execute.
                                    args (dict): Keyword arguments to pass to `method`.
                                    result_container (dict): Mutable mapping where keys will be set:
                                        - "result": the returned value if execution succeeds.
                                        - "exception": the raised Exception instance if execution fails.
                                        - "done": set to True when execution finishes.
                                    event (threading.Event): Event to set when execution completes.
                                    event_type (str): Logical category/name used for scoping (used for handler scope context).
                                    event_id (str): Identifier of the specific event (used for handler scope context).
                                
                                Returns:
                                    None
                                """
                                try:
                                    with ctx._handler_scope(f"{event_type}.{event_id}"):
                                        result_container["result"] = asyncio.run(method(**args))
                                except Exception as e:
                                    result_container["exception"] = e
                                finally:
                                    result_container["done"] = True
                                    event.set()
                            
                            thread = threading.Thread(target=_run_async_thread, daemon=True)
                            thread.start()
                            
                            # 等待异步方法完成（允许超时）
                            start_time = time.time()
                            timeout_seconds = PLUGIN_TRIGGER_TIMEOUT
                            check_interval = 0.01  # 10ms
                            
                            while not result_container["done"]:
                                if time.time() - start_time > timeout_seconds:
                                    logger.error(
                                        "Custom event {}.{} execution timed out",
                                        event_type,
                                        event_id,
                                    )
                                    raise TimeoutError(
                                        f"Custom event execution timed out after {timeout_seconds}s"
                                    )
                                event.wait(timeout=check_interval)
                            
                            if result_container["exception"]:
                                raise result_container["exception"]
                            else:
                                res = result_container["result"]
                        else:
                            logger.debug("[Plugin Process] Custom event is sync, calling directly")
                            with ctx._handler_scope(f"{event_type}.{event_id}"):
                                res = method(**args)
                        ret_payload["success"] = True
                        ret_payload["data"] = res
                        logger.debug(
                            "[Plugin Process] Custom event {}.{} completed, req_id={}",
                            event_type, event_id, req_id
                        )
                except Exception as e:
                    logger.exception("Error executing custom event {}.{}", event_type, event_id)
                    ret_payload["error"] = str(e)
                
                # 发送响应到结果队列
                logger.debug(
                    "[Plugin Process] Sending response for req_id={}, success={}",
                    req_id,
                    ret_payload.get("success"),
                )
                try:
                    # multiprocessing.Queue.put() 默认会阻塞直到有空间
                    # 使用 timeout 避免无限阻塞，但通常不会阻塞
                    res_queue.put(ret_payload, timeout=10.0)
                    logger.debug(
                        "[Plugin Process] Response sent successfully for req_id={}",
                        req_id,
                    )
                except Exception:
                    logger.exception(
                        "[Plugin Process] Failed to send response for req_id={}",
                        req_id,
                    )
                    # 即使发送失败，也要继续处理下一个命令（防御性编程）
                continue

            if msg["type"] == "TRIGGER":
                entry_id = msg["entry_id"]
                args = msg["args"]
                req_id = msg["req_id"]
                
                # 关键日志：记录接收到的触发消息
                logger.info(
                    "[Plugin Process] Received TRIGGER: plugin_id={}, entry_id={}, req_id={}",
                    plugin_id,
                    entry_id,
                    req_id,
                )
                # 详细参数信息使用 DEBUG
                logger.debug(
                    "[Plugin Process] Args: type={}, keys={}, content={}",
                    type(args),
                    list(args.keys()) if isinstance(args, dict) else "N/A",
                    args,
                )
                
                method = entry_map.get(entry_id) or getattr(instance, entry_id, None) or getattr(
                    instance, f"entry_{entry_id}", None
                )

                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}

                try:
                    if not method:
                        raise PluginEntryNotFoundError(plugin_id, entry_id)
                    
                    method_name = getattr(method, "__name__", entry_id)
                    # 关键日志：记录开始执行
                    logger.info(
                        "[Plugin Process] Executing entry '{}' using method '{}'",
                        entry_id,
                        method_name,
                    )
                    
                    # 详细方法签名和参数匹配信息使用 DEBUG
                    try:
                        sig = inspect.signature(method)
                        params = list(sig.parameters.keys())
                        logger.debug(
                            "[Plugin Process] Method signature: params={}, args_keys={}",
                            params,
                            list(args.keys()) if isinstance(args, dict) else "N/A",
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug("[Plugin Process] Failed to inspect signature: {}", e)
                    
                    if asyncio.iscoroutinefunction(method):
                        logger.debug("[Plugin Process] Method is async, running in thread to avoid blocking command loop")
                        # 关键修复：在独立线程中运行异步方法，避免阻塞命令循环
                        # 这样命令循环可以继续处理其他命令（包括响应命令）
                        result_container = {"result": None, "exception": None, "done": False}
                        event = threading.Event()
                        
                        def run_async(method=method, args=args, result_container=result_container, event=event, entry_id=entry_id):
                            """
                            Run an async handler and publish its outcome into a shared container and an event.
                            
                            Executes the coroutine function `method` with keyword arguments `args` using `asyncio.run`, stores the successful return value in `result_container["result"]`, stores any raised exception in `result_container["exception"]`, sets `result_container["done"] = True`, and signals `event` when finished.
                            
                            Parameters:
                                method (Callable[..., Coroutine]): The coroutine function to execute.
                                args (dict): Keyword arguments to pass to `method`.
                                result_container (dict): Mutable mapping where results are stored under keys `"result"`, `"exception"`, and `"done"`.
                                event (threading.Event): Event to set once execution completes (success or failure).
                                entry_id (str): Identifier used for handler scoping/logging context.
                            """
                            try:
                                with ctx._handler_scope(f"plugin_entry.{entry_id}"):
                                    result_container["result"] = asyncio.run(method(**args))
                            except Exception as e:
                                result_container["exception"] = e
                            finally:
                                result_container["done"] = True
                                event.set()
                        
                        thread = threading.Thread(target=run_async, daemon=True)
                        thread.start()
                        
                        # 等待异步方法完成（允许超时）
                        start_time = time.time()
                        timeout_seconds = PLUGIN_TRIGGER_TIMEOUT
                        check_interval = 0.01  # 10ms
                        
                        while not result_container["done"]:
                            if time.time() - start_time > timeout_seconds:
                                logger.error(
                                    "Async method {} execution timed out",
                                    entry_id,
                                )
                                raise TimeoutError(
                                    f"Async method execution timed out after {timeout_seconds}s"
                                )
                            event.wait(timeout=check_interval)
                        
                        if result_container["exception"]:
                            raise result_container["exception"]
                        else:
                            res = result_container["result"]
                    else:
                        logger.debug("[Plugin Process] Method is sync, calling directly")
                        try:
                            logger.debug(
                                "[Plugin Process] Calling method with args: {}",
                                args,
                            )
                            with ctx._handler_scope(f"plugin_entry.{entry_id}"):
                                res = method(**args)
                            logger.debug(
                                "[Plugin Process] Method call succeeded, result type: {}",
                                type(res),
                            )
                        except TypeError:
                            # 参数不匹配，记录详细信息并抛出
                            sig = inspect.signature(method)
                            params = list(sig.parameters.keys())
                            logger.exception(
                                "[Plugin Process] Invalid call to entry {}, params={}, args_keys={}",
                                entry_id,
                                params,
                                list(args.keys()) if isinstance(args, dict) else "N/A",
                            )
                            raise
                    
                    ret_payload["success"] = True
                    ret_payload["data"] = res
                    
                except PluginError as e:
                    # 插件系统已知异常，直接使用
                    logger.warning("Plugin error executing {}: {}", entry_id, e)
                    ret_payload["error"] = str(e)
                except (TypeError, ValueError, AttributeError) as e:
                    # 参数或方法调用错误
                    logger.exception("Invalid call to entry {}", entry_id)
                    ret_payload["error"] = f"Invalid call: {str(e)}"
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，需要特殊处理
                    logger.warning("Entry {} interrupted", entry_id)
                    ret_payload["error"] = "Execution interrupted"
                    raise  # 重新抛出系统级异常
                except Exception as e:
                    # 其他未知异常
                    logger.exception("Unexpected error executing {}", entry_id)
                    ret_payload["error"] = f"Unexpected error: {str(e)}"

                res_queue.put(ret_payload)

        # 触发生命周期：shutdown（尽力而为），并停止所有定时任务
        try:
            for ev in timer_stop_events:
                try:
                    ev.set()
                except Exception:
                    pass
        except Exception:
            pass

        shutdown_fn = lifecycle_events.get("shutdown")
        if shutdown_fn:
            try:
                with ctx._handler_scope("lifecycle.shutdown"):
                    if asyncio.iscoroutinefunction(shutdown_fn):
                        asyncio.run(shutdown_fn())
                    else:
                        shutdown_fn()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.exception("Error in lifecycle.shutdown: {}", e)

        for q in (cmd_queue, res_queue, status_queue, message_queue):
            try:
                q.cancel_join_thread()
            except Exception:
                pass
            try:
                q.close()
            except Exception:
                pass

    except (KeyboardInterrupt, SystemExit):
        # 系统级中断，正常退出
        logger.info("Plugin process {} interrupted", plugin_id)
        raise
    except Exception as e:
        # 进程崩溃，记录详细信息
        logger.exception("Plugin process {} crashed", plugin_id)
        # 尝试发送错误信息到结果队列（如果可能）
        try:
            res_queue.put({
                "req_id": "CRASH",
                "success": False,
                "data": None,
                "error": f"Process crashed: {str(e)}"
            })
        except Exception:
            pass  # 如果队列也坏了，只能放弃
        raise  # 重新抛出，让进程退出


class PluginHost:
    """
    插件进程宿主
    
    负责管理插件进程的完整生命周期：
    - 进程的启动、停止、监控（直接实现）
    - 进程间通信（通过 PluginCommunicationResourceManager）
    """

    def __init__(self, plugin_id: str, entry_point: str, config_path: Path):
        """
        Initialize a PluginHost: prepare IPC queues, register response primitives, start the plugin process, and create its communication manager.
        
        This constructor registers a response queue for the plugin, ensures shared notification primitives exist in parent process, launches the plugin process (targeting the plugin runner) and verifies its liveness, and instantiates a PluginCommunicationResourceManager to manage the plugin's queues. It also stores queue references and a process stop event for later shutdown and synchronous operations.
        
        Parameters:
            plugin_id (str): Unique identifier for the plugin; bound to the host logger.
            entry_point (str): Module and class path used to load the plugin (e.g., "package.module:Class").
            config_path (Path): Filesystem path to the plugin configuration; used for project-root resolution inside the plugin process.
        """
        self.plugin_id = plugin_id
        self.entry_point = entry_point
        self.config_path = config_path
        # 使用loguru logger，绑定插件ID
        self.logger = loguru_logger.bind(plugin_id=plugin_id, host=True)
        
        # 创建队列（由通信资源管理器管理）
        cmd_queue: Queue = multiprocessing.Queue()
        res_queue: Queue = multiprocessing.Queue()
        status_queue: Queue = multiprocessing.Queue()
        message_queue: Queue = multiprocessing.Queue()
        response_queue: Queue = multiprocessing.Queue()
        
        # 创建并启动进程
        # 获取插件间通信队列（从 state 获取）
        plugin_comm_queue = state.plugin_comm_queue

        try:
            state.set_plugin_response_queue(plugin_id, response_queue)
        except Exception:
            pass

        self._process_stop_event: Any = multiprocessing.Event()

        # Important: initialize shared response notification primitives in the parent
        # BEFORE forking the plugin process, otherwise each child may create its own
        # Event/Manager proxies and wait_for_plugin_response will never be woken.
        try:
            _ = state.plugin_response_map
        except Exception:
            pass
        try:
            _ = state.plugin_response_notify_event
        except Exception:
            pass
        
        self.process = multiprocessing.Process(
            target=_plugin_process_runner,
            args=(
                plugin_id,
                entry_point,
                config_path,
                cmd_queue,
                res_queue,
                status_queue,
                message_queue,
                response_queue,
                self._process_stop_event,
                plugin_comm_queue,
            ),
            daemon=True,
        )
        self.process.start()
        self.logger.info(f"Plugin {plugin_id} process started (pid: {self.process.pid})")
        
        # 验证进程状态
        if not self.process.is_alive():
            self.logger.error(f"Plugin {plugin_id} process is not alive after initialization (exitcode: {self.process.exitcode})")
        else:
            self.logger.info(f"Plugin {plugin_id} process is alive and running (pid: {self.process.pid})")
        
        # 创建通信资源管理器
        self.comm_manager = PluginCommunicationResourceManager(
            plugin_id=plugin_id,
            cmd_queue=cmd_queue,
            res_queue=res_queue,
            status_queue=status_queue,
            message_queue=message_queue,
        )
        
        # 保留队列引用（用于 shutdown_sync 等同步方法）
        self.cmd_queue = cmd_queue
        self.res_queue = res_queue
        self.status_queue = status_queue
        self.message_queue = message_queue
        self.response_queue = response_queue
    
    async def start(self, message_target_queue=None) -> None:
        """
        Start the plugin's communication manager and associated background tasks.
        
        Parameters:
            message_target_queue: Optional queue in the main process that will receive messages pushed from the plugin.
        """
        await self.comm_manager.start(message_target_queue=message_target_queue)
    
    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        Gracefully shut down the plugin and all associated communication resources and process.
        
        Performs these actions (in order): sends a stop command to the plugin, shuts down the communication manager and its background tasks, cancels multiprocessing queue join threads and deregisters the plugin response queue, then attempts to stop the plugin process within the given timeout.
        
        Parameters:
            timeout (float): Maximum number of seconds to wait for the plugin process to stop.
        """
        self.logger.info(f"Shutting down plugin {self.plugin_id}")

        # Set out-of-band stop event first so the child can exit promptly even if cmd_queue is backlogged.
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        
        # 1. 发送停止命令
        await self.comm_manager.send_stop_command()
        
        # 2. 关闭通信资源（包括后台任务）
        await self.comm_manager.shutdown(timeout=timeout)
        
        # 3. 取消队列等待（防止 atexit 阻塞）
        # 必须在进程关闭前调用，告诉 multiprocessing 不要等待这些队列的后台线程
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue, self.response_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)

        try:
            state.remove_plugin_response_queue(self.plugin_id)
        except Exception:
            pass

        # 4. 关闭进程
        success = await asyncio.to_thread(self._shutdown_process, timeout)
        
        if success:
            self.logger.info(f"Plugin {self.plugin_id} shutdown successfully")
        else:
            self.logger.warning(f"Plugin {self.plugin_id} shutdown with issues")
    
    def shutdown_sync(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        Perform a synchronous shutdown of the plugin host for non-async contexts.
        
        Sends a STOP command to the plugin, signals the communication manager's shutdown event if present, cancels queue join threads to avoid atexit blocking, removes the plugin's response queue from global state, and attempts to terminate the plugin process within the given timeout.
        
        Parameters:
            timeout (float): Maximum seconds to wait for the plugin process to shut down.
        """
        # 发送停止命令（同步）
        try:
            self.cmd_queue.put({"type": "STOP"}, timeout=QUEUE_GET_TIMEOUT)
        except Exception as e:
            self.logger.warning(f"Failed to send STOP command: {e}")
        
        # 尽量通知通信管理器停止（即使不等待）
        if getattr(self, "comm_manager", None) is not None:
            try:
                # 标记 shutdown event，后台协程会自行退出
                _ev = getattr(self.comm_manager, "_shutdown_event", None)
                if _ev is not None:
                    _ev.set()
            except Exception:
                # 保持同步关闭的"尽力而为"语义，不要让这里抛异常
                pass
        
        # 关闭进程
        # 取消队列等待
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue, self.response_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)

        try:
            state.remove_plugin_response_queue(self.plugin_id)
        except Exception:
            pass
                
        self._shutdown_process(timeout=timeout)
    
    async def trigger(self, entry_id: str, args: dict, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        """
        Trigger the plugin entry point identified by `entry_id`.
        
        Parameters:
            entry_id (str): Identifier of the plugin entry point to invoke.
            args (dict): Arguments to pass to the entry point.
            timeout (float): Maximum number of seconds to wait for the entry execution.
        
        Returns:
            Any: The value produced by the plugin entry point.
        """
        # 关键日志：记录触发请求
        self.logger.info(
            "[PluginHost] Trigger called: plugin_id={}, entry_id={}",
            self.plugin_id,
            entry_id,
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[PluginHost] Args: type={}, keys={}, content={}",
            type(args),
            list(args.keys()) if isinstance(args, dict) else "N/A",
            args,
        )
        # 发送 TRIGGER 命令到子进程并等待结果
        # 委托给通信资源管理器处理
        return await self.comm_manager.trigger(entry_id, args, timeout)
    
    async def trigger_custom_event(
        self, 
        event_type: str, 
        event_id: str, 
        args: dict, 
        timeout: float = PLUGIN_TRIGGER_TIMEOUT
    ) -> Any:
        """
        Trigger a plugin custom event handler.
        
        Parameters:
            event_type (str): Custom event category (e.g., "file_change", "user_action").
            event_id (str): Identifier of the custom event to invoke.
            args (dict): Arguments passed to the event handler.
            timeout (float): Maximum time in seconds to wait for the handler to complete.
        
        Returns:
            Any: The return value produced by the event handler.
        
        Raises:
            PluginError: If the specified event does not exist or its execution fails.
        """
        self.logger.info(
            "[PluginHost] Trigger custom event: plugin_id={}, event_type={}, event_id={}",
            self.plugin_id,
            event_type,
            event_id,
        )
        return await self.comm_manager.trigger_custom_event(event_type, event_id, args, timeout)

    async def push_bus_change(self, *, sub_id: str, bus: str, op: str, delta: Dict[str, Any] | None = None) -> None:
        """
        Notify the host of a change on a named bus for a given subscription.
        
        Parameters:
            sub_id (str): Subscription identifier affected by the change.
            bus (str): Name of the bus where the change occurred.
            op (str): Operation type (for example, "set", "update", "delete") describing the change.
            delta (Dict[str, Any] | None): Optional payload describing the change details; may be omitted for operations without data.
        """
        await self.comm_manager.push_bus_change(sub_id=sub_id, bus=bus, op=op, delta=delta)
    
    def is_alive(self) -> bool:
        """
        Return whether the plugin subprocess is currently running.
        
        Returns:
            True if the subprocess is alive and has not set an exit code, False otherwise.
        """
        return self.process.is_alive() and self.process.exitcode is None
    
    def health_check(self) -> HealthCheckResponse:
        """执行健康检查，返回详细状态"""
        alive = self.is_alive()
        exitcode = self.process.exitcode
        pid = self.process.pid if self.process.is_alive() else None
        
        if alive:
            status = "running"
        elif exitcode == 0:
            status = "stopped"
        else:
            status = "crashed"
        
        return HealthCheckResponse(
            alive=alive,
            exitcode=exitcode,
            pid=pid,
            status=status,
            communication={
                "pending_requests": len(self.comm_manager._pending_futures),
                "consumer_running": (
                    self.comm_manager._result_consumer_task is not None
                    and not self.comm_manager._result_consumer_task.done()
                ),
            },
        )
    
    def _shutdown_process(self, timeout: float = PROCESS_SHUTDOWN_TIMEOUT) -> bool:
        """
        Attempt a graceful shutdown of the plugin process, escalating to terminate/kill if it does not exit within time.
        
        Parameters:
            timeout (float): Seconds to wait for the process to exit gracefully.
        
        Returns:
            bool: `True` if the process was stopped (gracefully or after escalation), `False` if shutdown failed or an error occurred.
        """
        if not self.process.is_alive():
            self.logger.info(f"Plugin {self.plugin_id} process already stopped")
            return True
        
        try:
            # 先尝试优雅关闭（进程会从队列读取 STOP 命令后退出）
            self.process.join(timeout=timeout)
            
            if self.process.is_alive():
                self.logger.warning(
                    f"Plugin {self.plugin_id} didn't stop gracefully within {timeout}s, terminating"
                )
                self.process.terminate()
                self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                
                if self.process.is_alive():
                    self.logger.error(f"Plugin {self.plugin_id} failed to terminate, killing")
                    self.process.kill()
                    self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                    return False
            
            self.logger.info(f"Plugin {self.plugin_id} process shutdown successfully")
            return True
            
        except Exception:
            self.logger.exception("Error while shutting down plugin {}", self.plugin_id)
            return False


# Backwards-compatible alias
PluginProcessHost = PluginHost