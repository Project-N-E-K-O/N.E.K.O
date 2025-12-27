from __future__ import annotations

import asyncio
import importlib
import inspect
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
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


def _plugin_process_runner(
    plugin_id: str,
    entry_point: str,
    config_path: Path,
    cmd_queue: Queue,
    res_queue: Queue,
    status_queue: Queue,
    message_queue: Queue,
    plugin_comm_queue: Queue | None = None,
) -> None:
    """
    独立进程中的运行函数，负责加载插件、映射入口、处理命令并返回结果。
    """
    # 获取项目根目录（假设 config_path 在 plugin/plugins/xxx/plugin.toml）
    # config_path.parent.parent.parent 只能得到 ./plugin 目录
    # 需要再往上一级才能得到项目根目录（包含 plugin/ 目录的那一层）
    project_root = config_path.parent.parent.parent.parent.resolve()
    
    # 配置loguru logger for plugin process
    from loguru import logger
    # 移除默认handler
    logger.remove()
    # 绑定插件ID到logger上下文
    logger = logger.bind(plugin_id=plugin_id)
    # 添加控制台输出
    logger.add(
        sys.stdout,
        format=f"<green>{{time:YYYY-MM-DD HH:mm:ss}}</green> | <level>{{level: <8}}</level> | [Proc-{plugin_id}] <level>{{message}}</level>",
        level="INFO",
        colorize=True,
    )
    # 添加文件输出（使用项目根目录的log目录）
    log_dir = project_root / "log" / "plugins" / plugin_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{plugin_id}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    logger.add(
        str(log_file),
        format=f"{{time:YYYY-MM-DD HH:mm:ss}} | {{level: <8}} | [Proc-{plugin_id}] {{message}}",
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
            
        from utils.logger_config import InterceptHandler
            
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
        
        # 显式设置 uvicorn logger，并禁止传播以避免重复
        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
            logging_logger = logging.getLogger(logger_name)
            logging_logger.handlers = [InterceptHandler()]
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
            _cmd_queue=cmd_queue,  # 传递命令队列，用于在等待期间处理命令
            _res_queue=res_queue,  # 传递结果队列，用于在等待期间处理响应
            _entry_map=None,  # 将在创建 instance 后设置（见下方第116行）
            _instance=None,  # 将在创建 instance 后设置（见下方第117行）
        )
        instance = cls(ctx)

        entry_map: Dict[str, Any] = {}
        events_by_type: Dict[str, Dict[str, Any]] = {}

        # 扫描方法映射
        for name, member in inspect.getmembers(instance, predicate=callable):
            if name.startswith("_") and not hasattr(member, EVENT_META_ATTR):
                continue
            event_meta = getattr(member, EVENT_META_ATTR, None)
            if not event_meta and hasattr(member, "__wrapped__"):
                event_meta = getattr(member.__wrapped__, EVENT_META_ATTR, None)

            if event_meta:
                eid = getattr(event_meta, "id", name)
                entry_map[eid] = member
                etype = getattr(event_meta, "event_type", "plugin_entry")
                events_by_type.setdefault(etype, {})
                events_by_type[etype][eid] = member
            else:
                entry_map[name] = member

        logger.info("Plugin instance created. Mapped entries: {}", list(entry_map.keys()))
        
        # 设置命令队列、入口映射和实例到上下文，用于在等待期间处理命令
        # 这些属性在 PluginContext 中定义为 Optional，现在进行初始化
        ctx._cmd_queue = cmd_queue
        ctx._res_queue = res_queue
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
        for eid, fn in timer_events.items():
            meta = getattr(fn, EVENT_META_ATTR, None)
            if not meta or not getattr(meta, "auto_start", False):
                continue
            mode = getattr(meta, "extra", {}).get("mode")
            if mode == "interval":
                seconds = getattr(meta, "extra", {}).get("seconds", 0)
                if seconds > 0:
                    stop_event = threading.Event()
                    t = threading.Thread(
                        target=_run_timer_interval,
                        args=(fn, seconds, eid, stop_event),
                        daemon=True,
                    )
                    t.start()
                    logger.info("Started timer '{}' every {}s", eid, seconds)

        # 处理自定义事件：自动启动
        def _run_custom_event_auto(fn, fn_name: str, event_type: str):
            """执行自动启动的自定义事件"""
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
                msg = cmd_queue.get(timeout=QUEUE_GET_TIMEOUT)
            except Empty:
                continue

            if msg["type"] == "STOP":
                break

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
                            "[Plugin Process] Executing custom event %s.%s, req_id=%s",
                            event_type, event_id, req_id
                        )
                        if asyncio.iscoroutinefunction(method):
                            logger.debug("[Plugin Process] Custom event is async, running in thread to avoid blocking command loop")
                            # 在独立线程中运行异步方法，避免阻塞命令循环
                            # 这样命令循环可以继续处理其他命令（包括响应命令）
                            result_container = {"result": None, "exception": None, "done": False}
                            event = threading.Event()
                            
                            def run_async(method=method, args=args, result_container=result_container, event=event):
                                try:
                                    with ctx._handler_scope(f"{event_type}.{event_id}"):
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
                                    logger.error(f"Custom event {event_type}.{event_id} execution timed out")
                                    raise TimeoutError(f"Custom event execution timed out after {timeout_seconds}s")
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
                    "[Plugin Process] Sending response for req_id=%s, success=%s",
                    req_id, ret_payload.get("success")
                )
                try:
                    # multiprocessing.Queue.put() 默认会阻塞直到有空间
                    # 使用 timeout 避免无限阻塞，但通常不会阻塞
                    res_queue.put(ret_payload, timeout=10.0)
                    logger.debug(
                        "[Plugin Process] Response sent successfully for req_id=%s",
                        req_id
                    )
                except Exception:
                    logger.exception(
                        "[Plugin Process] Failed to send response for req_id=%s",
                        req_id
                    )
                    # 即使发送失败，也要继续处理下一个命令（防御性编程）
                continue

            if msg["type"] == "TRIGGER":
                entry_id = msg["entry_id"]
                args = msg["args"]
                req_id = msg["req_id"]
                
                # 关键日志：记录接收到的触发消息
                logger.info(
                    "[Plugin Process] Received TRIGGER: plugin_id=%s, entry_id=%s, req_id=%s",
                    plugin_id,
                    entry_id,
                    req_id,
                )
                # 详细参数信息使用 DEBUG
                logger.debug(
                    "[Plugin Process] Args: type=%s, keys=%s, content=%s",
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
                        "[Plugin Process] Executing entry '%s' using method '%s'",
                        entry_id,
                        method_name,
                    )
                    
                    # 详细方法签名和参数匹配信息使用 DEBUG
                    try:
                        sig = inspect.signature(method)
                        params = list(sig.parameters.keys())
                        logger.debug(
                            "[Plugin Process] Method signature: params=%s, args_keys=%s",
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
                        
                        def run_async(method=method, args=args, result_container=result_container, event=event):
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
                                logger.error(f"Async method {entry_id} execution timed out")
                                raise TimeoutError(f"Async method execution timed out after {timeout_seconds}s")
                            event.wait(timeout=check_interval)
                        
                        if result_container["exception"]:
                            raise result_container["exception"]
                        else:
                            res = result_container["result"]
                    else:
                        logger.debug("[Plugin Process] Method is sync, calling directly")
                        try:
                            logger.debug(
                                "[Plugin Process] Calling method with args: %s",
                                args,
                            )
                            with ctx._handler_scope(f"plugin_entry.{entry_id}"):
                                res = method(**args)
                            logger.debug(
                                "[Plugin Process] Method call succeeded, result type: %s",
                                type(res),
                            )
                        except TypeError:
                            # 参数不匹配，记录详细信息并抛出
                            sig = inspect.signature(method)
                            params = list(sig.parameters.keys())
                            logger.exception(
                                "[Plugin Process] Invalid call to entry %s, params=%s, args_keys=%s",
                                entry_id, params, list(args.keys()) if isinstance(args, dict) else "N/A"
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


class PluginProcessHost:
    """
    插件进程宿主
    
    负责管理插件进程的完整生命周期：
    - 进程的启动、停止、监控（直接实现）
    - 进程间通信（通过 PluginCommunicationResourceManager）
    """

    def __init__(self, plugin_id: str, entry_point: str, config_path: Path):
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
        
        # 创建并启动进程
        # 获取插件间通信队列（从 state 获取）
        plugin_comm_queue = state.plugin_comm_queue
        
        self.process = multiprocessing.Process(
            target=_plugin_process_runner,
            args=(plugin_id, entry_point, config_path, cmd_queue, res_queue, status_queue, message_queue, plugin_comm_queue),
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
    
    async def start(self, message_target_queue=None) -> None:
        """
        启动后台任务（需要在异步上下文中调用）
        
        Args:
            message_target_queue: 主进程的消息队列，用于接收插件推送的消息
        """
        await self.comm_manager.start(message_target_queue=message_target_queue)
    
    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        优雅关闭插件
        
        按顺序关闭：
        1. 发送停止命令
        2. 关闭通信资源
        3. 关闭进程
        """
        self.logger.info(f"Shutting down plugin {self.plugin_id}")
        
        # 1. 发送停止命令
        await self.comm_manager.send_stop_command()
        
        # 2. 关闭通信资源（包括后台任务）
        await self.comm_manager.shutdown(timeout=timeout)
        
        # 3. 取消队列等待（防止 atexit 阻塞）
        # 必须在进程关闭前调用，告诉 multiprocessing 不要等待这些队列的后台线程
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)

        # 4. 关闭进程
        success = await asyncio.to_thread(self._shutdown_process, timeout)
        
        if success:
            self.logger.info(f"Plugin {self.plugin_id} shutdown successfully")
        else:
            self.logger.warning(f"Plugin {self.plugin_id} shutdown with issues")
    
    def shutdown_sync(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        同步版本的关闭方法（用于非异步上下文）
        
        注意：这个方法不会等待异步任务完成，建议使用 shutdown()
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
                if getattr(self.comm_manager, "_shutdown_event", None) is not None:
                    self.comm_manager._shutdown_event.set()
            except Exception:
                # 保持同步关闭的"尽力而为"语义，不要让这里抛异常
                pass
        
        # 关闭进程
        # 取消队列等待
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)
                
        self._shutdown_process(timeout=timeout)
    
    async def trigger(self, entry_id: str, args: dict, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        """
        触发插件入口点执行
        
        Args:
            entry_id: 入口点 ID
            args: 参数字典
            timeout: 超时时间
        
        Returns:
            插件返回的结果
        """
        # 关键日志：记录触发请求
        self.logger.info(
            "[PluginHost] Trigger called: plugin_id=%s, entry_id=%s",
            self.plugin_id,
            entry_id,
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[PluginHost] Args: type=%s, keys=%s, content=%s",
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
        触发自定义事件执行
        
        Args:
            event_type: 自定义事件类型（例如 "file_change", "user_action"）
            event_id: 事件ID
            args: 参数字典
            timeout: 超时时间
        
        Returns:
            事件处理器返回的结果
        
        Raises:
            PluginError: 如果事件不存在或执行失败
        """
        self.logger.info("[PluginHost] Trigger custom event: plugin_id=%s, event_type=%s, event_id=%s" % (self.plugin_id, event_type, event_id))
        return await self.comm_manager.trigger_custom_event(event_type, event_id, args, timeout)
    
    def is_alive(self) -> bool:
        """检查进程是否存活"""
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
        优雅关闭进程
        
        Args:
            timeout: 等待进程退出的超时时间（秒）
        
        Returns:
            True 如果成功关闭，False 如果超时或出错
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
            
        except Exception as e:
            self.logger.exception(f"Error shutting down plugin {self.plugin_id}: {e}")
            return False
