"""Server lifecycle orchestration."""
from __future__ import annotations

import atexit
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from plugin.core.host import PluginProcessHost
from plugin.core.registry import load_plugins_from_roots
from plugin.core.state import state
from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.utils.time_utils import now_iso
from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.plane_bridge import start_bridge, stop_bridge
from plugin.server.messaging.proactive_bridge import start_proactive_bridge, stop_proactive_bridge
from plugin.server.messaging.plane_runner import MessagePlaneRunner, build_message_plane_runner
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.messaging.request_router import plugin_router
from plugin.settings import PLUGIN_CONFIG_ROOTS, PLUGIN_SHUTDOWN_TIMEOUT, PLUGIN_SHUTDOWN_TOTAL_TIMEOUT
from utils.logger_config import get_module_logger

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"

if _EMBEDDED_BY_AGENT:
    logger = get_module_logger(__name__, "Agent")
else:
    logger = get_logger("server.lifecycle")


@runtime_checkable
class _PluginHostContract(Protocol):
    async def start(self, message_target_queue: object) -> None: ...

    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None: ...


@dataclass(slots=True)
class _ShutdownResult:
    had_errors: bool


class ServerLifecycleService:
    def __init__(self) -> None:
        self._message_plane_runner: MessagePlaneRunner | None = None

    @staticmethod
    def _plugin_factory(
        plugin_id: str,
        entry: str,
        config_path: Path,
        *,
        extension_configs: list | None = None,
    ) -> PluginProcessHost:
        return PluginProcessHost(
            plugin_id=plugin_id,
            entry_point=entry,
            config_path=config_path,
            extension_configs=extension_configs,
        )

    @staticmethod
    def _get_plugin_hosts_snapshot() -> dict[str, object]:
        with state.acquire_plugin_hosts_read_lock():
            return dict(state.plugin_hosts)

    @staticmethod
    def _clear_runtime_state() -> None:
        with state.acquire_plugin_hosts_write_lock():
            stale_hosts = list(state.plugin_hosts.items())
            for plugin_id, host in stale_hosts:
                process_obj = getattr(host, "process", None)
                if process_obj is None:
                    continue
                try:
                    is_alive = bool(process_obj.is_alive())
                except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                    is_alive = False
                if not is_alive:
                    continue
                try:
                    process_obj.terminate()
                    process_obj.join(timeout=1.0)
                except (AttributeError, RuntimeError, OSError, TypeError, ValueError) as exc:
                    logger.warning(
                        "failed to terminate stale plugin process: plugin_id={}, err_type={}, err={}",
                        plugin_id,
                        type(exc).__name__,
                        str(exc),
                    )
                    continue
                try:
                    still_alive = bool(process_obj.is_alive())
                except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                    still_alive = False
                if still_alive:
                    try:
                        process_obj.kill()
                        process_obj.join(timeout=0.5)
                    except (AttributeError, RuntimeError, OSError, TypeError, ValueError) as exc:
                        logger.warning(
                            "failed to kill stale plugin process: plugin_id={}, err_type={}, err={}",
                            plugin_id,
                            type(exc).__name__,
                            str(exc),
                        )
                    else:
                        logger.debug("killed stale plugin process after terminate timeout: plugin_id={}", plugin_id)
                else:
                    logger.debug("cleaned stale plugin process: plugin_id={}", plugin_id)
            state.plugin_hosts.clear()

        with state.acquire_plugins_write_lock():
            state.plugins.clear()

        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()

    async def _start_message_plane(self) -> None:
        self._message_plane_runner = build_message_plane_runner()
        self._message_plane_runner.start()
        try:
            healthy = self._message_plane_runner.health_check(timeout_s=1.0)
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.warning(
                "message_plane health check failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return
        if not healthy:
            logger.warning("message_plane health check returned false; it may still be starting")

    async def _start_hosts(self) -> None:
        hosts_snapshot = self._get_plugin_hosts_snapshot()
        if not hosts_snapshot:
            logger.warning("no plugins loaded at startup; plugins may need manual start")
            return

        for plugin_id, host_obj in hosts_snapshot.items():
            if not isinstance(host_obj, _PluginHostContract):
                logger.warning(
                    "invalid plugin host object skipped during startup: plugin_id={}, host_type={}",
                    plugin_id,
                    type(host_obj).__name__,
                )
                continue

            try:
                await host_obj.start(message_target_queue=state.message_queue)
                logger.debug("started plugin communication resources: plugin_id={}", plugin_id)
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
                logger.error(
                    "failed to start plugin communication resources: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    str(exc),
                )

    async def startup(self) -> None:
        try:
            emit_lifecycle_event({"type": "server_startup_begin", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_startup_begin event: {}", exc)

        try:
            _ = state.plugin_response_map
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.warning(
                "failed to initialize plugin response map early: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        self._clear_runtime_state()

        await plugin_router.start()
        logger.debug("plugin router started")

        try:
            await self._start_message_plane()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            logger.warning(
                "message_plane start failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._message_plane_runner = None

        load_plugins_from_roots(PLUGIN_CONFIG_ROOTS, logger, self._plugin_factory)

        await bus_subscription_manager.start()
        logger.debug("bus subscription manager started")

        try:
            start_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to start message bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        try:
            start_proactive_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to start proactive bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        await self._start_hosts()

        def _get_hosts() -> dict[str, object]:
            return self._get_plugin_hosts_snapshot()

        await status_manager.start_status_consumer(plugin_hosts_getter=_get_hosts)
        logger.debug("status consumer started")

        await metrics_collector.start(plugin_hosts_getter=_get_hosts)
        logger.debug("metrics collector started")
        try:
            emit_lifecycle_event({"type": "server_startup_ready", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_startup_ready event: {}", exc)

    async def _shutdown_hosts(self) -> bool:
        hosts_snapshot = self._get_plugin_hosts_snapshot()
        if not hosts_snapshot:
            return False

        per_host_timeout = PLUGIN_SHUTDOWN_TIMEOUT + 0.5

        async def _shutdown_one(plugin_id: str, host_obj: _PluginHostContract) -> None:
            try:
                await asyncio.wait_for(
                    host_obj.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT),
                    timeout=per_host_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "plugin {} shutdown timed out after {:.1f}s, force-killing",
                    plugin_id, per_host_timeout,
                )
                proc = getattr(host_obj, "process", None)
                if proc is not None and proc.is_alive():
                    try:
                        proc.terminate()
                    except Exception:
                        pass

        tasks: list[asyncio.Task[None]] = []
        for plugin_id, host_obj in hosts_snapshot.items():
            try:
                emit_lifecycle_event({"type": "plugin_shutdown_requested", "plugin_id": plugin_id, "time": now_iso()})
            except Exception as exc:
                logger.warning("failed to emit plugin_shutdown_requested event: plugin_id={}, err={}", plugin_id, exc)
            if not isinstance(host_obj, _PluginHostContract):
                logger.warning(
                    "invalid plugin host object skipped during shutdown: plugin_id={}, host_type={}",
                    plugin_id,
                    type(host_obj).__name__,
                )
                continue
            tasks.append(asyncio.create_task(_shutdown_one(plugin_id, host_obj)))

        if not tasks:
            return False

        had_errors = False
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                had_errors = True
                logger.warning(
                    "plugin shutdown task raised: err_type={}, err={}",
                    type(result).__name__,
                    str(result),
                )
        return had_errors

    async def _shutdown_internal(self) -> _ShutdownResult:
        try:
            emit_lifecycle_event({"type": "server_shutdown_begin", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_shutdown_begin event: {}", exc)

        had_errors = False

        # Phase 1: sync signals (instant)
        for stop_fn, label in [
            (stop_proactive_bridge, "proactive bridge"),
            (stop_bridge, "message bridge"),
        ]:
            try:
                stop_fn()
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
                had_errors = True
                logger.warning("failed to stop {}: {}", label, exc)

        runner = self._message_plane_runner
        self._message_plane_runner = None
        if runner is not None:
            try:
                runner.stop()
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
                had_errors = True
                logger.warning("failed to stop message_plane runner: {}", exc)

        # Phase 2: parallel shutdown of all async components
        async def _stop_metrics():
            await metrics_collector.stop()

        async def _stop_status():
            await status_manager.shutdown_status_consumer(timeout=PLUGIN_SHUTDOWN_TIMEOUT)

        async def _stop_bus():
            await bus_subscription_manager.stop()

        async def _stop_router():
            await plugin_router.stop()

        async def _stop_hosts():
            return await self._shutdown_hosts()

        parallel_tasks = {
            "metrics": asyncio.create_task(_stop_metrics()),
            "status_consumer": asyncio.create_task(_stop_status()),
            "hosts": asyncio.create_task(_stop_hosts()),
            "bus_subscriptions": asyncio.create_task(_stop_bus()),
            "router": asyncio.create_task(_stop_router()),
        }

        results = await asyncio.gather(*parallel_tasks.values(), return_exceptions=True)
        for (label, _task), result in zip(parallel_tasks.items(), results):
            if isinstance(result, BaseException):
                had_errors = True
                logger.warning("failed to stop {}: {}", label, result)
            elif label == "hosts" and result is True:
                had_errors = True

        # Phase 3: resource cleanup
        try:
            await asyncio.wait_for(asyncio.to_thread(state.close_plugin_resources), timeout=0.5)
        except asyncio.TimeoutError:
            had_errors = True
            logger.warning("cleanup plugin communication resources timed out")
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            had_errors = True
            logger.warning("failed to cleanup plugin communication resources: {}", exc)

        # Phase 4: clear registry so next startup() / manual start_plugin() is clean
        try:
            with state.acquire_plugin_hosts_write_lock():
                state.plugin_hosts.clear()
            with state.acquire_plugins_write_lock():
                state.plugins.clear()
            with state.acquire_event_handlers_write_lock():
                state.event_handlers.clear()
        except Exception as exc:
            had_errors = True
            logger.warning("failed to clear plugin registry during shutdown: {}", exc)

        try:
            emit_lifecycle_event({"type": "server_shutdown_complete", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_shutdown_complete event: {}", exc)
        return _ShutdownResult(had_errors=had_errors)

    async def shutdown(self) -> None:
        try:
            result = await asyncio.wait_for(self._shutdown_internal(), timeout=PLUGIN_SHUTDOWN_TOTAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("server shutdown timed out after {}s", PLUGIN_SHUTDOWN_TOTAL_TIMEOUT)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(state.close_plugin_resources),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                logger.warning("forced cleanup after timeout also timed out")
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
                logger.warning("forced cleanup after timeout failed: {}", exc)
            return

        if result.had_errors:
            logger.warning("server shutdown completed with errors")
        else:
            logger.debug("server shutdown completed")


_service = ServerLifecycleService()


def _final_log_flush() -> None:
    try:
        logger.debug("final log flush before process exit")
    except (RuntimeError, ValueError, TypeError, OSError, AttributeError):
        return

    try:
        import sys

        sys.stdout.flush()
        sys.stderr.flush()
    except (RuntimeError, OSError, AttributeError, ValueError):
        return


atexit.register(_final_log_flush)


async def startup() -> None:
    await _service.startup()


async def shutdown() -> None:
    await _service.shutdown()
