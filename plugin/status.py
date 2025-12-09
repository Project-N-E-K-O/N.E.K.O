from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import threading

from plugin.server_base import state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PluginStatusManager:
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("user_plugin_server"))
    _plugin_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
        self.logger.info("插件id:%s  插件状态:%s", plugin_id, self._plugin_status[plugin_id])

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

    async def status_consumer(self):
        """轮询子进程上报的状态并落库到本进程内存表。"""
        while True:
            for pid, host in state.plugin_hosts.items():
                try:
                    while not host.status_queue.empty():
                        msg = host.status_queue.get_nowait()
                        if msg.get("type") == "STATUS_UPDATE":
                            self.apply_status_update(
                                plugin_id=msg["plugin_id"],
                                status=msg["data"],
                                source="child_process",
                            )
                except Exception:
                    self.logger.exception("Error consuming status for plugin %s", pid)
            await asyncio.sleep(1)


status_manager = PluginStatusManager()
