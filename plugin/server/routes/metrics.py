"""
性能监控路由
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from plugin._types.exceptions import PluginError
from plugin.core.state import state
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.infrastructure.utils import now_iso
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.executor import _api_executor

router = APIRouter()


@router.get("/plugin/metrics")
async def get_all_plugin_metrics(_: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        
        def _get_metrics():
            metrics = metrics_collector.get_current_metrics()
            
            if not isinstance(metrics, list):
                logger.warning(f"get_current_metrics returned non-list: {type(metrics)}")
                return []
            
            safe_metrics = []
            for m in metrics:
                if isinstance(m, dict):
                    safe_metrics.append(m)
                else:
                    logger.warning(f"Invalid metric format: {type(m)}")
            
            total_cpu = sum(float(m.get("cpu_percent", 0.0)) for m in safe_metrics)
            total_memory_mb = sum(float(m.get("memory_mb", 0.0)) for m in safe_metrics)
            total_memory_percent = sum(float(m.get("memory_percent", 0.0)) for m in safe_metrics)
            total_threads = sum(int(m.get("num_threads", 0)) for m in safe_metrics)
            
            return {
                "metrics": safe_metrics,
                "count": len(safe_metrics),
                "global": {
                    "total_cpu_percent": round(total_cpu, 2),
                    "total_memory_mb": round(total_memory_mb, 2),
                    "total_memory_percent": round(total_memory_percent, 2),
                    "total_threads": total_threads,
                    "active_plugins": len([m for m in safe_metrics if m.get("pid") is not None])
                },
                "time": now_iso()
            }
        
        return await loop.run_in_executor(_api_executor, _get_metrics)
    except (PluginError, ValueError, AttributeError) as e:
        logger.warning(f"Failed to get plugin metrics: {e}")
        return {
            "metrics": [],
            "count": 0,
            "global": {
                "total_cpu_percent": 0.0,
                "total_memory_mb": 0.0,
                "total_memory_percent": 0.0,
                "total_threads": 0,
                "active_plugins": 0
            },
            "time": now_iso()
        }
    except Exception:
        logger.exception("Failed to get plugin metrics: Unexpected error")
        return {
            "metrics": [],
            "count": 0,
            "global": {
                "total_cpu_percent": 0.0,
                "total_memory_mb": 0.0,
                "total_memory_percent": 0.0,
                "total_threads": 0,
                "active_plugins": 0
            },
            "time": now_iso()
        }


@router.get("/plugin/metrics/{plugin_id}")
async def get_plugin_metrics(plugin_id: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        
        def _check_plugin():
            # 使用缓存快照避免锁竞争
            plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
            hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
            
            plugin_registered = plugin_id in plugins_snapshot
            plugin_running = plugin_id in hosts_snapshot
            
            if plugin_running:
                host = hosts_snapshot.get(plugin_id)
                process_alive = hasattr(host, "process") and host.process is not None
                if process_alive:
                    logger.debug(
                        f"Plugin {plugin_id} is running (pid: {host.process.pid if hasattr(host.process, 'pid') else 'unknown'})"
                    )
                else:
                    logger.debug(f"Plugin {plugin_id} host has no process object")
                return plugin_registered, plugin_running, host, process_alive, None
            else:
                host = None
                process_alive = False
                all_running_plugins = list(hosts_snapshot.keys())
                return plugin_registered, plugin_running, host, process_alive, all_running_plugins
        
        plugin_registered, plugin_running, host, process_alive, all_running_plugins = await loop.run_in_executor(_api_executor, _check_plugin)
        
        if all_running_plugins is not None:
            logger.info(
                f"Plugin {plugin_id} is registered but not in plugin_hosts. "
                f"Currently tracked plugins in plugin_hosts: {all_running_plugins}. "
                f"Plugin may need to be started manually via /plugin/{plugin_id}/start"
            )
        
        if not plugin_registered:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' not found"
            )
        
        metrics = metrics_collector.get_current_metrics(plugin_id)
        
        if not metrics:
            if not plugin_running:
                message = "Plugin is registered but not running (start the plugin to collect metrics)"
            elif not process_alive:
                message = "Plugin process is not alive (may have crashed or stopped)"
            else:
                message = "Plugin is running but no metrics available yet (may be collecting, check collector status)"
            
            logger.debug(
                f"Plugin {plugin_id} registered but no metrics: registered={plugin_registered}, "
                f"running={plugin_running}, process_alive={process_alive}, has_host={host is not None}"
            )
            
            return {
                "plugin_id": plugin_id,
                "metrics": None,
                "message": message,
                "plugin_running": plugin_running,
                "process_alive": process_alive,
                "time": now_iso()
            }
        
        return {
            "plugin_id": plugin_id,
            "metrics": metrics[0],
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, f"Failed to get metrics for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get metrics for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get metrics for plugin {plugin_id}", 500) from e


@router.get("/plugin/metrics/{plugin_id}/history")
async def get_plugin_metrics_history(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    _: str = require_admin
):
    try:
        history = metrics_collector.get_metrics_history(
            plugin_id=plugin_id,
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )
        return {
            "plugin_id": plugin_id,
            "history": history,
            "count": len(history),
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, f"Failed to get metrics history for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get metrics history for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get metrics history for plugin {plugin_id}", 500) from e
