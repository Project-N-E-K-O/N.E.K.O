from __future__ import annotations

import asyncio
from collections.abc import Mapping

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso
from plugin.server.monitoring.metrics import metrics_collector

logger = get_logger("server.application.monitoring.query")


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _normalize_mapping_list(raw_items: list[object], *, context: str) -> list[dict[str, object]]:
    normalized_items: list[dict[str, object]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} item is not an object",
                status_code=500,
                details={"index": index, "item_type": type(item).__name__},
            )
        normalized_items.append(_normalize_mapping(item, context=f"{context}[{index}]"))
    return normalized_items


def _to_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return float(stripped)
        except ValueError:
            return default
    return default


def _to_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return int(stripped)
        except ValueError:
            return default
    return default


def _metrics_snapshot_for_plugin_sync(
    plugin_id: str,
) -> tuple[bool, bool, bool, list[str]]:
    plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
    hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)

    plugin_registered = plugin_id in plugins_snapshot
    plugin_running = plugin_id in hosts_snapshot
    process_alive = False

    if plugin_running:
        host = hosts_snapshot.get(plugin_id)
        if host is not None and hasattr(host, "process"):
            process_obj = getattr(host, "process")
            process_alive = process_obj is not None

    running_plugin_ids = [str(pid) for pid in hosts_snapshot.keys()]
    return plugin_registered, plugin_running, process_alive, running_plugin_ids


class MetricsQueryService:
    async def get_all_plugin_metrics(self) -> dict[str, object]:
        try:
            raw_metrics = await asyncio.to_thread(metrics_collector.get_current_metrics)
            if not isinstance(raw_metrics, list):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="metrics collector returned non-array",
                    status_code=500,
                    details={"result_type": type(raw_metrics).__name__},
                )
            metrics = _normalize_mapping_list(raw_metrics, context="plugin_metrics")

            total_cpu = sum(_to_float(metric.get("cpu_percent")) for metric in metrics)
            total_memory_mb = sum(_to_float(metric.get("memory_mb")) for metric in metrics)
            total_memory_percent = sum(_to_float(metric.get("memory_percent")) for metric in metrics)
            total_threads = sum(_to_int(metric.get("num_threads")) for metric in metrics)
            active_plugins = len([metric for metric in metrics if metric.get("pid") is not None])

            return {
                "metrics": metrics,
                "count": len(metrics),
                "global": {
                    "total_cpu_percent": round(total_cpu, 2),
                    "total_memory_mb": round(total_memory_mb, 2),
                    "total_memory_percent": round(total_memory_percent, 2),
                    "total_threads": total_threads,
                    "active_plugins": active_plugins,
                },
                "time": now_iso(),
            }
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_all_plugin_metrics failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="METRICS_QUERY_FAILED",
                message="Failed to get plugin metrics",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_metrics(self, plugin_id: str) -> dict[str, object]:
        try:
            plugin_registered, plugin_running, process_alive, running_plugin_ids = await asyncio.to_thread(
                _metrics_snapshot_for_plugin_sync,
                plugin_id,
            )

            if not plugin_registered:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            raw_metrics = metrics_collector.get_current_metrics(plugin_id)
            if not isinstance(raw_metrics, list):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="metrics collector returned non-array",
                    status_code=500,
                    details={
                        "plugin_id": plugin_id,
                        "result_type": type(raw_metrics).__name__,
                    },
                )
            metrics = _normalize_mapping_list(raw_metrics, context=f"plugin_metrics[{plugin_id}]")

            if not metrics:
                logger.info(
                    "No metrics for plugin {}: running={}, process_alive={}, running_plugins={}",
                    plugin_id,
                    plugin_running,
                    process_alive,
                    running_plugin_ids,
                )
                if not plugin_running:
                    message = "Plugin is registered but not running (start the plugin to collect metrics)"
                elif not process_alive:
                    message = "Plugin process is not alive (may have crashed or stopped)"
                else:
                    message = "Plugin is running but no metrics available yet (may be collecting, check collector status)"

                return {
                    "plugin_id": plugin_id,
                    "metrics": None,
                    "message": message,
                    "plugin_running": plugin_running,
                    "process_alive": process_alive,
                    "time": now_iso(),
                }

            return {
                "plugin_id": plugin_id,
                "metrics": metrics[0],
                "time": now_iso(),
            }
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_plugin_metrics failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="METRICS_QUERY_FAILED",
                message=f"Failed to get metrics for plugin {plugin_id}",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def get_plugin_metrics_history(
        self,
        *,
        plugin_id: str,
        limit: int,
        start_time: str | None,
        end_time: str | None,
    ) -> dict[str, object]:
        try:
            raw_history = await asyncio.to_thread(
                metrics_collector.get_metrics_history,
                plugin_id,
                limit,
                start_time,
                end_time,
            )
            if not isinstance(raw_history, list):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="metrics history result is not an array",
                    status_code=500,
                    details={
                        "plugin_id": plugin_id,
                        "result_type": type(raw_history).__name__,
                    },
                )

            history = _normalize_mapping_list(raw_history, context=f"metrics_history[{plugin_id}]")
            return {
                "plugin_id": plugin_id,
                "history": history,
                "count": len(history),
                "time": now_iso(),
            }
        except ServerDomainError:
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "get_plugin_metrics_history failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="METRICS_HISTORY_QUERY_FAILED",
                message=f"Failed to get metrics history for plugin {plugin_id}",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc
