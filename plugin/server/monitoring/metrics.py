"""
插件性能监控服务

提供插件性能指标的收集和查询功能。
"""
import asyncio
import threading
from datetime import datetime, timezone
from typing import Callable
from dataclasses import dataclass

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

from plugin.logging_config import get_logger
from plugin.settings import PLUGIN_LOG_SERVER_DEBUG

from plugin.core.state import state
from plugin.server.infrastructure.utils import now_iso

logger = get_logger("server.monitoring.metrics")
_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)


def _parse_iso_to_utc(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_metric_timestamp_utc(value: str) -> datetime | None:
    try:
        return _parse_iso_to_utc(value, field="timestamp")
    except ValueError:
        return None


@dataclass
class PluginMetrics:
    """插件性能指标"""
    plugin_id: str
    timestamp: str
    
    # 进程信息
    pid: int | None = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    num_threads: int = 0
    
    # 执行统计（需要从其他地方获取）
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    avg_execution_time: float = 0.0
    
    # 队列状态
    pending_requests: int = 0
    queue_size: int = 0


class MetricsCollector:
    """性能指标收集器"""
    
    # 每个插件保留的最大历史记录数
    MAX_HISTORY_SIZE = 1000
    
    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._metrics_history: dict[str, list[PluginMetrics]] = {}
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._plugin_hosts_getter: Callable[[], dict[str, object]] | None = None
        
        # 缓存机制：减少锁竞争
        self._cache: list[dict[str, object]] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 0.5  # 500ms 缓存
    
    async def start(self, plugin_hosts_getter: Callable[[], dict[str, object]]) -> None:
        """启动指标收集任务"""
        if not PSUTIL_AVAILABLE:
            logger.warning("psutil not available, metrics collection disabled")
            return
        
        self._plugin_hosts_getter = plugin_hosts_getter
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._collect_loop())
            logger.info("Metrics collector started")
    
    async def stop(self):
        """停止指标收集任务"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Metrics collector stopped")
    
    async def _collect_loop(self):
        """定期收集指标"""
        while True:
            try:
                if not self._plugin_hosts_getter:
                    await asyncio.sleep(self.interval)
                    continue
                
                plugin_hosts = self._plugin_hosts_getter()
                if not plugin_hosts:
                    if PLUGIN_LOG_SERVER_DEBUG:
                        logger.debug("No plugin hosts available for metrics collection")
                    await asyncio.sleep(self.interval)
                    continue
                
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Collecting metrics for {len(plugin_hosts)} plugins: {list(plugin_hosts.keys())}")
                for plugin_id, host in plugin_hosts.items():
                    try:
                        metrics = await asyncio.to_thread(
                            self._collect_plugin_metrics_sync,
                            plugin_id,
                            host,
                        )
                        if metrics:
                            with self._lock:
                                if plugin_id not in self._metrics_history:
                                    self._metrics_history[plugin_id] = []
                                self._metrics_history[plugin_id].append(metrics)
                                # 只保留最近 MAX_HISTORY_SIZE 条记录
                                if len(self._metrics_history[plugin_id]) > self.MAX_HISTORY_SIZE:
                                    self._metrics_history[plugin_id].pop(0)
                                if PLUGIN_LOG_SERVER_DEBUG:
                                    logger.debug(f"Successfully collected and stored metrics for plugin {plugin_id}")
                        else:
                            # 记录为什么没有收集到指标
                            process = getattr(host, "process", None)
                            if not process:
                                if PLUGIN_LOG_SERVER_DEBUG:
                                    logger.debug(f"No process object for plugin {plugin_id}")
                            elif not process.is_alive():
                                if PLUGIN_LOG_SERVER_DEBUG:
                                    logger.debug(f"Process for plugin {plugin_id} is not alive (pid: {process.pid if process else 'N/A'})")
                            else:
                                if PLUGIN_LOG_SERVER_DEBUG:
                                    logger.debug(f"Failed to collect metrics for plugin {plugin_id} (process alive but collection returned None)")
                    except _RUNTIME_ERRORS as e:
                        logger.warning(f"Exception while collecting metrics for plugin {plugin_id}: {e}", exc_info=True)
                
            except asyncio.CancelledError:
                break
            except _RUNTIME_ERRORS as e:
                logger.exception(f"Error in metrics collection loop: {e}")
            
            await asyncio.sleep(self.interval)
    
    def _collect_plugin_metrics_sync(self, plugin_id: str, host: object) -> PluginMetrics | None:
        """收集单个插件的性能指标"""
        if not PSUTIL_AVAILABLE:
            if PLUGIN_LOG_SERVER_DEBUG:
                logger.debug(f"psutil not available, cannot collect metrics for {plugin_id}")
            return None
        
        try:
            # 获取进程信息
            process = getattr(host, "process", None)
            if not process:
                logger.debug(f"No process object for plugin {plugin_id}")
                return None
            
            if not process.is_alive():
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Process for plugin {plugin_id} is not alive (pid: {process.pid})")
                return None
            
            pid = process.pid
            
            # 使用psutil获取进程信息
            try:
                ps_process = psutil.Process(pid)
                cpu_percent = ps_process.cpu_percent(interval=0.1)
                memory_info = ps_process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024
                memory_percent = ps_process.memory_percent()
                num_threads = ps_process.num_threads()
            except psutil.NoSuchProcess:
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Process {pid} for plugin {plugin_id} no longer exists (NoSuchProcess)")
                return None
            except psutil.AccessDenied:
                logger.warning(f"Access denied when collecting metrics for plugin {plugin_id} (pid: {pid})")
                return None
            
            # 获取队列状态
            comm_manager = getattr(host, "comm_manager", None)
            pending_requests = 0
            if comm_manager:
                # 使用公共方法获取待处理请求数，保持封装性
                if hasattr(comm_manager, "get_pending_requests_count"):
                    try:
                        pending_requests = comm_manager.get_pending_requests_count()
                    except _RUNTIME_ERRORS as e:
                        if PLUGIN_LOG_SERVER_DEBUG:
                            logger.debug(f"Failed to get pending requests count for {plugin_id}: {e}")
                        pending_requests = 0
            
            return PluginMetrics(
                plugin_id=plugin_id,
                timestamp=now_iso(),
                pid=pid,
                cpu_percent=cpu_percent,
                memory_mb=memory_mb,
                memory_percent=memory_percent,
                num_threads=num_threads,
                pending_requests=pending_requests,
            )
        except _RUNTIME_ERRORS as e:
            logger.warning(f"Unexpected error collecting metrics for {plugin_id}: {e}", exc_info=True)
            return None
    
    def get_current_metrics(self, plugin_id: str | None = None) -> list[dict[str, object]]:
        """获取当前性能指标（带缓存，减少锁竞争）"""
        import time
        now = time.time()
        
        if plugin_id:
            # 单个插件查询，直接获取锁
            with self._lock:
                history = self._metrics_history.get(plugin_id, [])
                if history:
                    return [self._metrics_to_dict(history[-1])]
                available_ids = list(self._metrics_history.keys())
                logger.debug(
                    f"Metrics not found for plugin_id '{plugin_id}'. "
                    f"Available plugin_ids in metrics_history: {available_ids}"
                )
                return []
        else:
            # 全量查询，使用缓存减少锁竞争
            if self._cache and (now - self._cache_timestamp) < self._cache_ttl:
                return self._cache
            
            with self._lock:
                result = []
                for _plugin_id, history in self._metrics_history.items():
                    if history:
                        result.append(self._metrics_to_dict(history[-1]))
                # 更新缓存
                self._cache = result
                self._cache_timestamp = now
                logger.debug(f"get_current_metrics (all): found {len(result)} plugins with metrics")
                return result
    
    def get_metrics_history(
        self,
        plugin_id: str,
        limit: int = 100,
        start_time: str | None = None,
        end_time: str | None = None
    ) -> list[dict[str, object]]:
        """获取性能指标历史"""
        with self._lock:
            if limit <= 0:
                return []

            history = self._metrics_history.get(plugin_id, [])

            start_dt = _parse_iso_to_utc(start_time, field="start_time") if isinstance(start_time, str) else None
            end_dt = _parse_iso_to_utc(end_time, field="end_time") if isinstance(end_time, str) else None

            # 时间过滤（基于 UTC datetime 比较）
            filtered = history
            if start_dt is not None:
                next_filtered: list[PluginMetrics] = []
                for metrics in filtered:
                    if not isinstance(metrics.timestamp, str):
                        continue
                    metric_dt = _safe_metric_timestamp_utc(metrics.timestamp)
                    if metric_dt is None:
                        continue
                    if metric_dt >= start_dt:
                        next_filtered.append(metrics)
                filtered = next_filtered
            if end_dt is not None:
                next_filtered: list[PluginMetrics] = []
                for metrics in filtered:
                    if not isinstance(metrics.timestamp, str):
                        continue
                    metric_dt = _safe_metric_timestamp_utc(metrics.timestamp)
                    if metric_dt is None:
                        continue
                    if metric_dt <= end_dt:
                        next_filtered.append(metrics)
                filtered = next_filtered

            # 限制数量
            if len(filtered) > limit:
                filtered = filtered[-limit:]

            return [self._metrics_to_dict(m) for m in filtered]
    
    def _metrics_to_dict(self, metrics: PluginMetrics) -> dict[str, object]:
        """将指标对象转换为字典"""
        return {
            "plugin_id": metrics.plugin_id,
            "timestamp": metrics.timestamp,
            "pid": metrics.pid,
            "cpu_percent": round(metrics.cpu_percent, 2),
            "memory_mb": round(metrics.memory_mb, 2),
            "memory_percent": round(metrics.memory_percent, 2),
            "num_threads": metrics.num_threads,
            "total_executions": metrics.total_executions,
            "successful_executions": metrics.successful_executions,
            "failed_executions": metrics.failed_executions,
            "avg_execution_time": round(metrics.avg_execution_time, 3),
            "pending_requests": metrics.pending_requests,
            "queue_size": metrics.queue_size,
        }


# 全局指标收集器实例
metrics_collector = MetricsCollector()
