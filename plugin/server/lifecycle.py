"""Server lifecycle orchestration."""
from __future__ import annotations

import atexit
import asyncio
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from plugin.core.state import state
from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.utils.time_utils import now_iso
from plugin.server.application.plugins import PluginLifecycleService, PluginRegistryService
from plugin.server.application.messages.live_vision_service import (
    live_vision_query_service,
)
from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.plane_bridge import start_bridge, stop_bridge
from plugin.server.messaging.proactive_bridge import start_proactive_bridge, stop_proactive_bridge
from plugin.server.messaging.plane_runner import MessagePlaneRunner, build_message_plane_runner
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.messaging.request_router import plugin_router
from plugin.settings import PLUGIN_SHUTDOWN_TIMEOUT, PLUGIN_SHUTDOWN_TOTAL_TIMEOUT
from utils.logger_config import get_module_logger

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"
_SHUTDOWN_PERMISSION_REVOKE_TIMEOUT = 0.4
_AUTOSTART_PERMISSION_REVOKE_ATTEMPTS = 16
_AUTOSTART_PERMISSION_REVOKE_ATTEMPT_TIMEOUT = 1.0
_AUTOSTART_PERMISSION_REVOKE_RETRY_SECONDS = 1.0
_PERMISSION_REHYDRATE_INTERVAL_SECONDS = 2.0
_PERMISSION_REHYDRATE_TIMEOUT_SECONDS = 1.0

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
        self._plugin_registry_service = PluginRegistryService()
        self._plugin_lifecycle_service = PluginLifecycleService()
        self._permission_rehydration_task: asyncio.Task[None] | None = None

    async def _permission_rehydration_loop(self) -> None:
        while True:
            await asyncio.sleep(_PERMISSION_REHYDRATE_INTERVAL_SECONDS)
            await live_vision_query_service.rehydrate_active_permissions(
                timeout=_PERMISSION_REHYDRATE_TIMEOUT_SECONDS,
            )

    def _start_permission_rehydration(self) -> None:
        task = self._permission_rehydration_task
        if task is None or task.done():
            self._permission_rehydration_task = asyncio.create_task(
                self._permission_rehydration_loop(),
                name="plugin-permission-rehydration",
            )

    async def _stop_permission_rehydration(self) -> None:
        task = self._permission_rehydration_task
        self._permission_rehydration_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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
            health_check_async = getattr(self._message_plane_runner, "health_check_async", None)
            if health_check_async is not None and asyncio.iscoroutinefunction(health_check_async):
                healthy = await health_check_async(timeout_s=1.0)
            else:
                # Fallback: runner only exposes the sync API — offload to a worker thread so we
                # never block the event loop on the ~1s TCP probe + RPC round-trip.
                healthy = await asyncio.to_thread(
                    self._message_plane_runner.health_check, timeout_s=1.0
                )
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.warning(
                "message_plane health check failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return
        if not healthy:
            logger.warning("message_plane health check returned false; it may still be starting")

    async def _revoke_permissions_before_autostart(
        self,
        plugin_ids: list[str],
    ) -> set[str]:
        pending_ids = list(plugin_ids)
        last_results: dict[str, object] = {}
        for attempt in range(_AUTOSTART_PERMISSION_REVOKE_ATTEMPTS):
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(
                        self._plugin_lifecycle_service.revoke_plugin_permissions(
                            plugin_id
                        ),
                        timeout=_AUTOSTART_PERMISSION_REVOKE_ATTEMPT_TIMEOUT,
                    )
                    for plugin_id in pending_ids
                ),
                return_exceptions=True,
            )
            retry_ids: list[str] = []
            for plugin_id, result in zip(pending_ids, results, strict=True):
                if result is False or isinstance(result, BaseException):
                    retry_ids.append(plugin_id)
                    last_results[plugin_id] = result
            if not retry_ids:
                return set()
            pending_ids = retry_ids
            if attempt + 1 < _AUTOSTART_PERMISSION_REVOKE_ATTEMPTS:
                if attempt == 0:
                    logger.warning(
                        "main_server permission endpoint unavailable during startup; "
                        "retrying plugin revocation before autostart: plugin_count={}",
                        len(pending_ids),
                    )
                await asyncio.sleep(_AUTOSTART_PERMISSION_REVOKE_RETRY_SECONDS)

        for plugin_id in pending_ids:
            result = last_results.get(plugin_id)
            if isinstance(result, BaseException):
                logger.error(
                    "plugin permission revocation raised at startup: "
                    "plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(result).__name__,
                    str(result),
                )
        return set(pending_ids)

    async def _refresh_registry_and_start_autostart_plugins(self) -> None:
        try:
            refresh_result = await self._plugin_registry_service.refresh_registry()
            logger.info(
                "plugin registry refresh completed: added={}, updated={}, removed={}, failed={}",
                len(refresh_result.get("added", [])),
                len(refresh_result.get("updated", [])),
                len(refresh_result.get("removed", [])),
                len(refresh_result.get("failed", [])),
            )
            with state.acquire_plugins_read_lock():
                registered_plugin_ids = sorted(
                    plugin_id
                    for plugin_id in state.plugins
                    if isinstance(plugin_id, str) and plugin_id
                )
            failed_revocation_ids = await self._revoke_permissions_before_autostart(
                registered_plugin_ids
            )
            autostart_plugin_ids = await self._plugin_registry_service.list_autostart_plugin_ids()
        except Exception as exc:
            logger.error(
                "plugin registry refresh failed at startup: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return

        if not autostart_plugin_ids:
            logger.warning("no autostart plugins discovered at startup; plugins may need manual start")
            return

        for plugin_id in autostart_plugin_ids:
            if plugin_id in failed_revocation_ids:
                logger.error(
                    "skipping plugin autostart because permission revocation failed: plugin_id={}",
                    plugin_id,
                )
                continue
            try:
                await self._plugin_lifecycle_service.start_plugin(plugin_id, refresh_registry=False)
                logger.debug("autostart plugin started: plugin_id={}", plugin_id)
            except Exception as exc:
                logger.error(
                    "failed to autostart plugin at startup: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    str(exc),
                )

    async def startup(self) -> None:
        try:
            emit_lifecycle_event({"type": "server_startup_begin", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_startup_begin event: {}", exc)

        self._clear_runtime_state()

        await ensure_plugin_messaging_started()
        self._start_permission_rehydration()

        try:
            cleaned_profiles = await self._plugin_lifecycle_service.retry_deferred_profile_cleanup()
            if cleaned_profiles:
                logger.info("retried deferred package profile cleanup: cleaned={}", cleaned_profiles)
        except Exception as exc:
            logger.warning(
                "deferred package profile cleanup retry failed at startup: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        try:
            await self._start_message_plane()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            logger.warning(
                "message_plane start failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._message_plane_runner = None

        await self._refresh_registry_and_start_autostart_plugins()

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

        async def _shutdown_one(plugin_id: str, host_obj: object) -> None:
            revoke_failed = False
            try:
                if not isinstance(host_obj, _PluginHostContract):
                    logger.warning(
                        "invalid plugin host object skipped during shutdown: plugin_id={}, host_type={}",
                        plugin_id,
                        type(host_obj).__name__,
                    )
                else:
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
            finally:
                revoked = await self._plugin_lifecycle_service.revoke_plugin_permissions(
                    plugin_id,
                    timeout=_SHUTDOWN_PERMISSION_REVOKE_TIMEOUT,
                )
                revoke_failed = revoked is False
            if revoke_failed:
                raise RuntimeError(
                    f"plugin permission revoke failed for {plugin_id}"
                )

        tasks: list[asyncio.Task[None]] = []
        for plugin_id, host_obj in hosts_snapshot.items():
            try:
                emit_lifecycle_event({"type": "plugin_shutdown_requested", "plugin_id": plugin_id, "time": now_iso()})
            except Exception as exc:
                logger.warning("failed to emit plugin_shutdown_requested event: plugin_id={}, err={}", plugin_id, exc)
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

    async def _revoke_host_permissions(self) -> bool:
        plugin_ids = sorted(self._get_plugin_hosts_snapshot())
        if not plugin_ids:
            return False
        results = await asyncio.gather(*(
            self._plugin_lifecycle_service.revoke_plugin_permissions(
                plugin_id,
                timeout=_SHUTDOWN_PERMISSION_REVOKE_TIMEOUT,
            )
            for plugin_id in plugin_ids
        ), return_exceptions=True)
        had_errors = False
        for plugin_id, result in zip(plugin_ids, results):
            if result is False or isinstance(result, BaseException):
                had_errors = True
                logger.warning(
                    "failed to revoke plugin permissions during shutdown: plugin_id={}, result={}",
                    plugin_id,
                    result,
                )
        return had_errors

    async def _shutdown_internal(self) -> _ShutdownResult:
        try:
            emit_lifecycle_event({"type": "server_shutdown_begin", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_shutdown_begin event: {}", exc)

        had_errors = False

        await self._stop_permission_rehydration()

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
            await self._revoke_host_permissions()
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


async def ensure_plugin_messaging_started() -> None:
    """Start plugin request messaging without running full plugin lifecycle."""
    try:
        _ = state.plugin_response_map
    except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
        logger.warning(
            "failed to initialize plugin response map early: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )

    await plugin_router.start()
    logger.debug("plugin router started")


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
