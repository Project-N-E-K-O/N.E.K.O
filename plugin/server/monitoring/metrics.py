"""
Plugin performance-monitoring service.

Collects and queries plugin performance metrics.
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

from plugin.utils.time_utils import now_iso

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
    """Plugin performance metrics."""
    plugin_id: str
    timestamp: str
    
    # Process information.
    pid: int | None = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    num_threads: int = 0
    
    # Execution statistics, obtained elsewhere.
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    avg_execution_time: float = 0.0
    
    # Queue state.
    pending_requests: int = 0
    queue_size: int = 0


class MetricsCollector:
    """Performance-metrics collector."""
    
    # Maximum history entries retained per plugin.
    MAX_HISTORY_SIZE = 1000
    
    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._metrics_history: dict[str, list[PluginMetrics]] = {}
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._plugin_hosts_getter: Callable[[], dict[str, object]] | None = None
        
        # Caching reduces lock contention.
        self._cache: list[dict[str, object]] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 0.5  # 500ms cache.
        # Appends bump this. A full-query cache publish must not replace a
        # snapshot that was taken later.
        self._history_version = 0
        self._cache_version = -1
        # Live-set changes bump this, and only this. A full query retries when
        # it moves, so an ordinary append does not discard an in-flight snapshot.
        self._liveness_version = 0

        # Reuse psutil.Process per plugin_id, storing (pid, Process),
        # so cpu_percent(interval=None) computes a nonblocking delta from the previous sample.
        self._ps_processes: dict[str, tuple[int, object]] = {}
        # Plugins that produced a sample in the latest collection tick, or whose
        # process is still the one behind their last sample and only missed this
        # tick's read. History is kept for stopped plugins, but "current" metrics
        # must only report processes that are still alive.
        self._live_plugin_ids: set[str] = set()
    
    async def start(self, plugin_hosts_getter: Callable[[], dict[str, object]]) -> None:
        """Start the metrics-collection task."""
        if not PSUTIL_AVAILABLE:
            logger.warning("psutil not available, metrics collection disabled")
            return
        
        self._plugin_hosts_getter = plugin_hosts_getter
        if self._task is None or self._task.done():
            # Every collection round owns a new cache: stop() cancels the coroutine but not the running
            # to_thread sampler, so late old threads may write/delete only their old cache.
            ps_processes: dict[str, tuple[int, object]] = {}
            self._ps_processes = ps_processes
            self._task = asyncio.create_task(self._collect_loop(ps_processes))
            logger.info("Metrics collector started")
    
    async def stop(self):
        """Stop the metrics-collection task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Metrics collector stopped")
    
    async def _collect_loop(self, ps_processes: dict[str, tuple[int, object]]):
        """Collect metrics periodically."""
        while True:
            try:
                if not self._plugin_hosts_getter:
                    await asyncio.sleep(self.interval)
                    continue
                
                plugin_hosts = self._plugin_hosts_getter()
                self._prune_ps_processes(plugin_hosts or {}, ps_processes)
                if not plugin_hosts:
                    if PLUGIN_LOG_SERVER_DEBUG:
                        logger.debug("No plugin hosts available for metrics collection")
                    self._publish_live_plugin_ids(set())
                    await asyncio.sleep(self.interval)
                    continue
                
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Collecting metrics for {len(plugin_hosts)} plugins: {list(plugin_hosts.keys())}")
                live_plugin_ids: set[str] = set()
                for plugin_id, host in plugin_hosts.items():
                    try:
                        metrics = await asyncio.to_thread(
                            self._collect_plugin_metrics_sync,
                            plugin_id,
                            host,
                            ps_processes,
                        )
                        if metrics:
                            with self._lock:
                                if plugin_id not in self._metrics_history:
                                    self._metrics_history[plugin_id] = []
                                self._metrics_history[plugin_id].append(metrics)
                                self._history_version += 1
                                # Retain only the latest MAX_HISTORY_SIZE records.
                                if len(self._metrics_history[plugin_id]) > self.MAX_HISTORY_SIZE:
                                    self._metrics_history[plugin_id].pop(0)
                                if PLUGIN_LOG_SERVER_DEBUG:
                                    logger.debug(f"Successfully collected and stored metrics for plugin {plugin_id}")
                            live_plugin_ids.add(plugin_id)
                        else:
                            # Record why metrics were not collected.
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
                                # The process is still there. Keep its previous sample in
                                # the current totals instead of treating one failed read
                                # as a stop, but only if that sample came from this same
                                # process: after a restart the old PID's numbers are stale.
                                with self._lock:
                                    history = self._metrics_history.get(plugin_id)
                                    same_process = bool(history) and history[-1].pid == process.pid
                                if same_process:
                                    live_plugin_ids.add(plugin_id)
                    except _RUNTIME_ERRORS as e:
                        logger.warning(f"Exception while collecting metrics for plugin {plugin_id}: {e}", exc_info=True)
                self._publish_live_plugin_ids(live_plugin_ids)
                
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in metrics collection loop")
            
            await asyncio.sleep(self.interval)
    
    def _publish_live_plugin_ids(self, live_plugin_ids: set[str]) -> None:
        """Record which plugins were alive in the latest tick.

        Stopped or crashed plugins keep their history, but their last sample must
        not keep counting towards current / global totals.
        """
        with self._lock:
            if live_plugin_ids == self._live_plugin_ids:
                return
            self._live_plugin_ids = set(live_plugin_ids)
            # Newer than any cached snapshot, so a stale publish cannot win.
            # Appends keep their own counter; only a live-set change retries a query.
            self._liveness_version += 1
            self._cache = []
            self._cache_timestamp = 0.0

    def _prune_ps_processes(
        self,
        plugin_hosts: dict[str, object],
        ps_processes: dict[str, tuple[int, object]] | None = None,
    ) -> None:
        """Discard cached psutil.Process entries for plugins absent from plugin_hosts."""
        cache = self._ps_processes if ps_processes is None else ps_processes
        with self._lock:
            for stale_id in [cached_id for cached_id in cache if cached_id not in plugin_hosts]:
                del cache[stale_id]

    def _drop_ps_process(self, plugin_id: str, ps_processes: dict[str, tuple[int, object]]) -> None:
        with self._lock:
            ps_processes.pop(plugin_id, None)

    def _get_ps_process(
        self, plugin_id: str, pid: int, ps_processes: dict[str, tuple[int, object]]
    ) -> object:
        """Get or create/cache psutil.Process for plugin_id's PID, recreating on PID change."""
        with self._lock:
            cached = ps_processes.get(plugin_id)
            if cached is not None and cached[0] == pid:
                return cached[1]
        ps_process = psutil.Process(pid)
        with self._lock:
            ps_processes[plugin_id] = (pid, ps_process)
        return ps_process

    def _collect_plugin_metrics_sync(
        self,
        plugin_id: str,
        host: object,
        ps_processes: dict[str, tuple[int, object]] | None = None,
    ) -> PluginMetrics | None:
        """Collect performance metrics for one plugin.

        ps_processes is the caller's sampling-round cache; defaults to the current cache.
        """
        cache = self._ps_processes if ps_processes is None else ps_processes
        if not PSUTIL_AVAILABLE:
            if PLUGIN_LOG_SERVER_DEBUG:
                logger.debug(f"psutil not available, cannot collect metrics for {plugin_id}")
            return None
        
        try:
            # Get process information.
            process = getattr(host, "process", None)
            if not process:
                self._drop_ps_process(plugin_id, cache)
                logger.debug(f"No process object for plugin {plugin_id}")
                return None
            
            if not process.is_alive():
                self._drop_ps_process(plugin_id, cache)
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Process for plugin {plugin_id} is not alive (pid: {process.pid})")
                return None
            
            pid = process.pid
            
            # Get process information with psutil.
            try:
                ps_process = self._get_ps_process(plugin_id, pid, cache)
                # Nonblocking sampling returns CPU usage since the previous call.
                # The first call for a new process or changed PID establishes a baseline and returns 0.0.
                cpu_percent = ps_process.cpu_percent(interval=None)
                memory_info = ps_process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024
                memory_percent = ps_process.memory_percent()
                num_threads = ps_process.num_threads()
            except psutil.NoSuchProcess:
                self._drop_ps_process(plugin_id, cache)
                if PLUGIN_LOG_SERVER_DEBUG:
                    logger.debug(f"Process {pid} for plugin {plugin_id} no longer exists (NoSuchProcess)")
                return None
            except psutil.AccessDenied:
                logger.warning(f"Access denied when collecting metrics for plugin {plugin_id} (pid: {pid})")
                return None
            
            # Get queue state.
            comm_manager = getattr(host, "comm_manager", None)
            pending_requests = 0
            if comm_manager:
                # Use the public pending-request count method to preserve encapsulation.
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
        """Get current performance metrics with caching to reduce lock contention."""
        import time
        now = time.time()
        
        if plugin_id:
            # Existing records are append-only. Copy the latest reference
            # while holding the lock, then serialize outside it so rounding
            # and dict allocation cannot delay a collector tick.
            with self._lock:
                history = self._metrics_history.get(plugin_id, [])
                latest = history[-1] if history and plugin_id in self._live_plugin_ids else None
                available_ids = list(self._metrics_history.keys())
            if latest is not None:
                return [self._metrics_to_dict(latest)]
            if plugin_id not in available_ids:
                logger.debug(
                    f"Metrics not found for plugin_id '{plugin_id}'. "
                    f"Available plugin_ids in metrics_history: {available_ids}"
                )
            return []
        else:
            # Full query uses caching to reduce lock contention. Read cached list and timestamp together under lock:
            # separate reads could combine an invalidated old list with a new timestamp and return stopped
            # plugins as fresh data.
            with self._lock:
                cached = self._cache
                cached_at = self._cache_timestamp
            if cached and (now - cached_at) < self._cache_ttl:
                return cached

            def _live_snapshot() -> tuple[int, int, list[PluginMetrics]]:
                with self._lock:
                    return self._history_version, self._liveness_version, [
                        history[-1]
                        for history_plugin_id, history in self._metrics_history.items()
                        if history and history_plugin_id in self._live_plugin_ids
                    ]

            # Existing records are not mutated after append, so conversion can
            # happen outside the lock. A liveness publish may clear _cache
            # while that conversion is still running; publishing the old
            # snapshot would put stopped plugins back into the TTL cache.
            # Retry only when the live set moved. An append keeps the
            # newer-cache guard so a later snapshot is not overwritten.
            for _ in range(3):
                version, liveness_version, latest_records = _live_snapshot()
                result = [self._metrics_to_dict(record) for record in latest_records]
                with self._lock:
                    if liveness_version != self._liveness_version:
                        continue
                    if version < self._cache_version and self._cache:
                        return self._cache
                    self._cache = result
                    self._cache_timestamp = time.time()
                    self._cache_version = version
                logger.debug(f"get_current_metrics (all): found {len(result)} plugins with metrics")
                return result

            _, _, latest_records = _live_snapshot()
            return [self._metrics_to_dict(record) for record in latest_records]
    
    def get_metrics_history(
        self,
        plugin_id: str,
        limit: int = 100,
        start_time: str | None = None,
        end_time: str | None = None
    ) -> list[dict[str, object]]:
        """Get performance-metrics history."""
        if limit <= 0:
            return []

        # Parse user input before taking the lock. This keeps the existing
        # ValueError contract while avoiding a lock hold on invalid requests.
        start_dt = _parse_iso_to_utc(start_time, field="start_time") if isinstance(start_time, str) else None
        end_dt = _parse_iso_to_utc(end_time, field="end_time") if isinstance(end_time, str) else None

        with self._lock:
            # PluginMetrics records are append-only. A shallow list snapshot is
            # enough to isolate filtering from later collector appends.
            history = list(self._metrics_history.get(plugin_id, []))

        # Time filtering using UTC datetime comparison.
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
            next_filtered = []
            for metrics in filtered:
                if not isinstance(metrics.timestamp, str):
                    continue
                metric_dt = _safe_metric_timestamp_utc(metrics.timestamp)
                if metric_dt is None:
                    continue
                if metric_dt <= end_dt:
                    next_filtered.append(metrics)
            filtered = next_filtered

        # Limit count.
        if len(filtered) > limit:
            filtered = filtered[-limit:]

        return [self._metrics_to_dict(m) for m in filtered]
    
    def _metrics_to_dict(self, metrics: PluginMetrics) -> dict[str, object]:
        """Convert a metrics object into a dictionary."""
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


# Global metrics-collector instance.
metrics_collector = MetricsCollector()
