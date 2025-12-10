from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import threading


def _now_iso() -> str:
    """统一的 ISO 时间戳生成"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PluginStatusManager:
    """
    插件状态管理器
    
    负责：
    - 状态存储和查询
    - 状态消费后台任务管理
    """
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("plugin.status"))
    _plugin_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    # 状态消费任务相关
    _status_consumer_task: Optional[asyncio.Task] = field(default=None, init=False)
    _shutdown_event: Optional[asyncio.Event] = field(default=None, init=False)
    _plugin_hosts_getter: Optional[callable] = field(default=None, init=False)
    
    def __post_init__(self):
        """初始化异步资源"""
        self._shutdown_event = asyncio.Event()

    def apply_status_update(self, plugin_id: str, status: Dict[str, Any], source: str) -> None:
        """统一落地插件状态的内部工具函数。"""
        if not plugin_id:
            return
        with self._lock:
            self._plugin_status[plugin_id] = {
                "plugin_id": plugin_id,
                "status": status,
                "updated_at": _now_iso(),
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
        with self._lock:
            if plugin_id is None:
                return {pid: s.copy() for pid, s in self._plugin_status.items()}
            return self._plugin_status.get(plugin_id, {}).copy()

    async def start_status_consumer(self, plugin_hosts_getter: callable) -> None:
        """
        启动状态消费任务
        
        Args:
            plugin_hosts_getter: 返回 plugin_hosts 字典的回调函数
        """
        self._plugin_hosts_getter = plugin_hosts_getter
        if self._status_consumer_task is None or self._status_consumer_task.done():
            self._status_consumer_task = asyncio.create_task(self._consume_status())
            self.logger.debug("Started status consumer task")

    async def shutdown_status_consumer(self, timeout: float = 5.0) -> None:
        """
        关闭状态消费任务
        
        Args:
            timeout: 等待任务退出的超时时间
        """
        self.logger.debug("Shutting down status consumer")
        
        if self._status_consumer_task and not self._status_consumer_task.done():
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
                        messages = comm_manager.get_status_messages(max_count=100)
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
                await asyncio.sleep(0.1)
                
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
