from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import threading
from typing import Any, Dict, Optional

from loguru import logger

from plugin.settings import (
    STATUS_CONSUMER_SHUTDOWN_TIMEOUT,
    STATUS_MESSAGE_DEFAULT_MAX_COUNT,
    STATUS_CONSUMER_SLEEP_INTERVAL,
)
from plugin.utils.time_utils import now_iso


@dataclass
class PluginStatusManager:
    """
    插件状态管理器
    
    负责：
    - 状态存储和查询
    - 状态消费后台任务管理
    """
    logger: Any = field(default_factory=lambda: logger.bind(component="status"))
    _plugin_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    # 状态消费任务相关
    _status_consumer_task: Optional[asyncio.Task] = field(default=None, init=False)
    _shutdown_event: Optional[asyncio.Event] = field(default=None, init=False)
    _plugin_hosts_getter: Optional[callable] = field(default=None, init=False)
    
    def __post_init__(self):
        """初始化异步资源（延迟创建 Event，确保在正确的事件循环中）"""
        # 延迟到实际使用时再创建，避免在模块导入时创建 Event
        pass
    
    def _ensure_shutdown_event(self) -> None:
        """确保 shutdown_event 已创建（延迟初始化）"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()

    def apply_status_update(self, plugin_id: str, status: Dict[str, Any], source: str) -> None:
        """统一落地插件状态的内部工具函数。"""
        if not plugin_id:
            return
        with self._lock:
            self._plugin_status[plugin_id] = {
                "plugin_id": plugin_id,
                "status": status,
                "updated_at": now_iso(),
                "source": source,
            }
        self.logger.debug("插件id:%s  插件状态已更新 (来源: %s)", plugin_id, source)

    def update_plugin_status(self, plugin_id: str, status: Dict[str, Any]) -> None:
        """由同进程代码调用：直接在主进程内更新状态。"""
        self.apply_status_update(plugin_id, status, source="main_process_direct")

    def get_plugin_status(self, plugin_id: Optional[str] = None) -> Dict[str, Any]:
        """
        在进程内获取当前插件运行状态。
        - plugin_id 为 None：返回 {plugin_id: status, ...}
        - 否则只返回该插件状态（可能为空 dict）
        """
        from plugin.core.state import state
        with self._lock:
            cached = {pid: s.copy() for pid, s in self._plugin_status.items()}

        # 使用缓存快照避免锁竞争
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
        registered_plugin_ids = list(plugins_snapshot.keys())
        plugin_hosts_snapshot = hosts_snapshot

        def _build_synthetic(pid: str, status: str) -> Dict[str, Any]:
            return {
                "plugin_id": pid,
                "status": {"status": status},
                "updated_at": now_iso(),
                "source": "main_process_synthetic",
            }

        for pid in registered_plugin_ids:
            cached.setdefault(pid, _build_synthetic(pid, "stopped"))

        for pid, host in plugin_hosts_snapshot.items():
            alive = False
            try:
                is_alive = getattr(host, "is_alive", None)
                if callable(is_alive):
                    alive = bool(is_alive())
            except Exception as e:
                self.logger.debug("检查插件 %s 存活状态时出错: %s", pid, e)
                alive = False

            existing = cached.get(pid)
            if existing is None or existing.get("source") == "main_process_synthetic":
                if alive:
                    plugin_state = "running"
                else:
                    plugin_state = "crashed"
                    try:
                        proc = getattr(host, "process", None)
                        exitcode = getattr(proc, "exitcode", None) if proc is not None else None
                        if exitcode == 0:
                            plugin_state = "stopped"
                    except Exception:
                        plugin_state = "crashed"
                cached[pid] = _build_synthetic(pid, plugin_state)

        if plugin_id is None:
            return cached
        return cached.get(plugin_id, _build_synthetic(plugin_id, "stopped"))

    async def start_status_consumer(self, plugin_hosts_getter: callable) -> None:
        """
        启动状态消费任务
        
        Args:
            plugin_hosts_getter: 返回 plugin_hosts 字典的回调函数
        """
        # 每次启动都重建，避免复用已 set 的事件导致任务立即退出
        self._shutdown_event = asyncio.Event()
        self._plugin_hosts_getter = plugin_hosts_getter
        if self._status_consumer_task is None or self._status_consumer_task.done():
            self._status_consumer_task = asyncio.create_task(self._consume_status())
            self.logger.debug("Started status consumer task")

    async def shutdown_status_consumer(self, timeout: float = STATUS_CONSUMER_SHUTDOWN_TIMEOUT) -> None:
        """
        关闭状态消费任务
        
        Args:
            timeout: 等待任务退出的超时时间
        """
        self.logger.debug("Shutting down status consumer")
        
        if self._status_consumer_task and not self._status_consumer_task.done():
            self._ensure_shutdown_event()
            self._shutdown_event.set()
            try:
                await asyncio.wait_for(self._status_consumer_task, timeout=timeout)
            except asyncio.TimeoutError:
                self.logger.warning("Status consumer didn't stop in time, cancelling")
                self._status_consumer_task.cancel()
                try:
                    await self._status_consumer_task
                except asyncio.CancelledError:
                    pass
        
        self.logger.debug("Status consumer shutdown complete")
    
    async def _consume_status(self) -> None:
        """
        状态消费后台任务
        
        从所有插件的状态队列中消费状态更新消息
        """
        self._ensure_shutdown_event()
        while not self._shutdown_event.is_set():
            try:
                if not self._plugin_hosts_getter:
                    await asyncio.sleep(1)
                    continue
                
                plugin_hosts = self._plugin_hosts_getter()
                if not plugin_hosts:
                    await asyncio.sleep(1)
                    continue
                
                # 遍历所有插件，消费状态消息
                for plugin_id, host in plugin_hosts.items():
                    try:
                        # 获取通信资源管理器
                        comm_manager = getattr(host, "comm_manager", None)
                        if not comm_manager:
                            continue
                        
                        # 批量获取状态消息
                        messages = comm_manager.get_status_messages(max_count=STATUS_MESSAGE_DEFAULT_MAX_COUNT)
                        for msg in messages:
                            if msg.get("type") == "STATUS_UPDATE":
                                # 直接调用状态更新方法
                                self.apply_status_update(
                                    plugin_id=msg.get("plugin_id"),
                                    status=msg.get("data", {}),
                                    source="child_process"
                                )
                    except (AttributeError, KeyError) as e:
                        self.logger.warning(f"Invalid status message format for plugin {plugin_id}: {e}")
                    except Exception as e:
                        self.logger.exception(f"Error consuming status for plugin {plugin_id}: {e}")
                
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(STATUS_CONSUMER_SLEEP_INTERVAL)
                
            except (OSError, RuntimeError) as e:
                # 系统级错误
                if not self._shutdown_event.is_set():
                    self.logger.error(f"System error in status consumer: {e}")
                await asyncio.sleep(1)
            except Exception as e:
                # 其他未知异常
                if not self._shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error in status consumer: {e}")
                await asyncio.sleep(1)


status_manager = PluginStatusManager()
