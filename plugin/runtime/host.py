from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import multiprocessing
import threading
from pathlib import Path
from typing import Any, Dict
from multiprocessing import Queue
from queue import Empty

from plugin.sdk.events import EVENT_META_ATTR
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


def _plugin_process_runner(
    plugin_id: str,
    entry_point: str,
    config_path: Path,
    cmd_queue: Queue,
    res_queue: Queue,
    status_queue: Queue,
    message_queue: Queue,
) -> None:
    """
    独立进程中的运行函数，负责加载插件、映射入口、处理命令并返回结果。
    """
    logging.basicConfig(level=logging.INFO, format=f"[Proc-{plugin_id}] %(message)s")
    logger = logging.getLogger(f"plugin.{plugin_id}")

    try:
        module_path, class_name = entry_point.split(":", 1)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        ctx = PluginContext(
            plugin_id=plugin_id,
            logger=logger,
            config_path=config_path,
            status_queue=status_queue,
            message_queue=message_queue,
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

        logger.info("Plugin instance created. Mapped entries: %s", list(entry_map.keys()))

        # 生命周期：startup
        lifecycle_events = events_by_type.get("lifecycle", {})
        startup_fn = lifecycle_events.get("startup")
        if startup_fn:
            try:
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
                    if asyncio.iscoroutinefunction(fn):
                        asyncio.run(fn())
                    else:
                        fn()
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，停止定时任务
                    logger.info("Timer '%s' interrupted, stopping", fn_name)
                    break
                except Exception as e:
                    logger.exception("Timer '%s' failed: %s", fn_name, e)
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
                    logger.info("Started timer '%s' every %ss", eid, seconds)

        # 命令循环
        while True:
            try:
                msg = cmd_queue.get(timeout=1.0)
            except Empty:
                continue

            if msg["type"] == "STOP":
                break

            if msg["type"] == "TRIGGER":
                entry_id = msg["entry_id"]
                args = msg["args"]
                req_id = msg["req_id"]
                method = entry_map.get(entry_id) or getattr(instance, entry_id, None) or getattr(
                    instance, f"entry_{entry_id}", None
                )

                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}

                try:
                    if not method:
                        raise PluginEntryNotFoundError(plugin_id, entry_id)
                    
                    logger.info("Executing entry '%s' using method '%s'", entry_id, getattr(method, "__name__", entry_id))
                    
                    if asyncio.iscoroutinefunction(method):
                        res = asyncio.run(method(**args))
                    else:
                        try:
                            res = method(**args)
                        except TypeError:
                            # 尝试将 args 作为单个参数传递（向后兼容）
                            res = method(args)
                    
                    ret_payload["success"] = True
                    ret_payload["data"] = res
                    
                except PluginError as e:
                    # 插件系统已知异常，直接使用
                    logger.warning("Plugin error executing %s: %s", entry_id, e)
                    ret_payload["error"] = str(e)
                except (TypeError, ValueError, AttributeError) as e:
                    # 参数或方法调用错误
                    logger.error("Invalid call to entry %s: %s", entry_id, e)
                    ret_payload["error"] = f"Invalid call: {str(e)}"
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，需要特殊处理
                    logger.warning("Entry %s interrupted", entry_id)
                    ret_payload["error"] = "Execution interrupted"
                    raise  # 重新抛出系统级异常
                except Exception as e:
                    # 其他未知异常
                    logger.exception("Unexpected error executing %s", entry_id)
                    ret_payload["error"] = f"Unexpected error: {str(e)}"

                res_queue.put(ret_payload)

    except (KeyboardInterrupt, SystemExit):
        # 系统级中断，正常退出
        logger.info("Plugin process %s interrupted", plugin_id)
        raise
    except Exception as e:
        # 进程崩溃，记录详细信息
        logger.exception("Plugin process %s crashed: %s", plugin_id, e)
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
        self.logger = logging.getLogger(f"plugin.host.{plugin_id}")
        
        # 创建队列（由通信资源管理器管理）
        cmd_queue: Queue = multiprocessing.Queue()
        res_queue: Queue = multiprocessing.Queue()
        status_queue: Queue = multiprocessing.Queue()
        message_queue: Queue = multiprocessing.Queue()
        
        # 创建并启动进程
        self.process = multiprocessing.Process(
            target=_plugin_process_runner,
            args=(plugin_id, entry_point, config_path, cmd_queue, res_queue, status_queue, message_queue),
            daemon=False,
        )
        self.process.start()
        
        # 验证进程状态
        if not self.process.is_alive():
            self.logger.warning(f"Plugin {plugin_id} process is not alive after initialization")
        
        # 创建通信资源管理器
        self.comm_manager = PluginCommunicationResourceManager(
            plugin_id=plugin_id,
            cmd_queue=cmd_queue,
            res_queue=res_queue,
            status_queue=status_queue,
            message_queue=message_queue,
        )
        
        # 为了向后兼容，保留这些属性
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
    
    async def shutdown(self, timeout: float = 5.0) -> None:
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
        
        # 3. 关闭进程
        success = self._shutdown_process(timeout=timeout)
        
        if success:
            self.logger.info(f"Plugin {self.plugin_id} shutdown successfully")
        else:
            self.logger.warning(f"Plugin {self.plugin_id} shutdown with issues")
    
    def shutdown_sync(self, timeout: float = 5.0) -> None:
        """
        同步版本的关闭方法（用于非异步上下文）
        
        注意：这个方法不会等待异步任务完成，建议使用 shutdown()
        """
        # 发送停止命令（同步）
        try:
            self.cmd_queue.put({"type": "STOP"}, timeout=1.0)
        except Exception as e:
            self.logger.warning(f"Failed to send STOP command: {e}")
        
        # 关闭进程
        self._shutdown_process(timeout=timeout)
    
    async def trigger(self, entry_id: str, args: dict, timeout: float = 10.0) -> Any:
        """
        发送 TRIGGER 命令到子进程并等待结果
        
        委托给通信资源管理器处理
        """
        return await self.comm_manager.trigger(entry_id, args, timeout)
    
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
    
    def _shutdown_process(self, timeout: float = 5.0) -> bool:
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
                self.process.join(timeout=1.0)
                
                if self.process.is_alive():
                    self.logger.error(f"Plugin {self.plugin_id} failed to terminate, killing")
                    self.process.kill()
                    self.process.join(timeout=1.0)
                    return False
            
            self.logger.info(f"Plugin {self.plugin_id} process shutdown successfully")
            return True
            
        except Exception as e:
            self.logger.exception(f"Error shutting down plugin {self.plugin_id}: {e}")
            return False
