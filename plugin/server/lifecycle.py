"""Server lifecycle orchestration."""
from __future__ import annotations

import atexit
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from plugin.core.host import PluginProcessHost
from plugin.core.registry import load_plugins_from_toml
from plugin.core.state import state
from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.server.infrastructure.auth import generate_admin_code, set_admin_code
from plugin.utils.time_utils import now_iso
from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.plane_bridge import start_bridge, stop_bridge
from plugin.server.messaging.plane_runner import MessagePlaneRunner, build_message_plane_runner
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.plugin_router import plugin_router
from plugin.settings import PLUGIN_CONFIG_ROOT, PLUGIN_SHUTDOWN_TIMEOUT, PLUGIN_SHUTDOWN_TOTAL_TIMEOUT

logger = get_logger("server.lifecycle")


@runtime_checkable
class _PluginHostContract(Protocol):
    async def start(self, message_target_queue: object) -> None: ...

    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None: ...


@dataclass(slots=True)
class _ShutdownResult:
    timed_out: bool
    had_errors: bool


class ServerLifecycleService:
    def __init__(self) -> None:
        self._message_plane_runner: MessagePlaneRunner | None = None

    @staticmethod
    def _persist_admin_code_for_non_tty(admin_code: str) -> Path | None:
        code_file = (PLUGIN_CONFIG_ROOT.parent / ".plugin_server_admin_code").resolve()
        try:
            code_file.write_text(f"{admin_code}\n", encoding="utf-8")
            try:
                code_file.chmod(0o600)
            except OSError:
                logger.warning("failed to chmod admin code file: path={}", code_file)
            return code_file
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return None

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
                    logger.debug("cleaned stale plugin process: plugin_id={}", plugin_id)
                except (AttributeError, RuntimeError, OSError, TypeError, ValueError) as exc:
                    logger.warning(
                        "failed to terminate stale plugin process: plugin_id={}, err_type={}, err={}",
                        plugin_id,
                        type(exc).__name__,
                        str(exc),
                    )
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
        emit_lifecycle_event({"type": "server_startup_begin", "plugin_id": "server", "time": now_iso()})

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
        logger.info("plugin router started")

        try:
            await self._start_message_plane()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            logger.warning(
                "message_plane start failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._message_plane_runner = None

        load_plugins_from_toml(PLUGIN_CONFIG_ROOT, logger, self._plugin_factory)

        with state.acquire_plugin_hosts_read_lock():
            for plugin_id in list(state.plugin_hosts.keys()):
                emit_lifecycle_event({"type": "plugin_loaded", "plugin_id": plugin_id, "time": now_iso()})

        await bus_subscription_manager.start()
        logger.info("bus subscription manager started")

        try:
            start_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to start message bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        await self._start_hosts()

        def _get_hosts() -> dict[str, object]:
            return self._get_plugin_hosts_snapshot()

        await status_manager.start_status_consumer(plugin_hosts_getter=_get_hosts)
        logger.info("status consumer started")

        await metrics_collector.start(plugin_hosts_getter=_get_hosts)
        logger.info("metrics collector started")
        emit_lifecycle_event({"type": "server_startup_ready", "plugin_id": "server", "time": now_iso()})

        admin_code = generate_admin_code()
        set_admin_code(admin_code)
        if sys.stdout.isatty():
            print("\n" + "=" * 60, flush=True)
            print(f"ADMIN CODE: {admin_code}", flush=True)
            print("Add HTTP header: Authorization: Bearer <admin_code>", flush=True)
            print("=" * 60 + "\n", flush=True)
        else:
            code_file = self._persist_admin_code_for_non_tty(admin_code)
            if code_file is not None:
                logger.warning(
                    "admin code generated and persisted for non-interactive stdout: path={}",
                    code_file,
                )
            else:
                logger.warning("admin code generated but not printed/persisted on non-interactive stdout")
        logger.info("admin authentication code generated")

    async def _shutdown_hosts(self) -> bool:
        hosts_snapshot = self._get_plugin_hosts_snapshot()
        if not hosts_snapshot:
            return False

        tasks: list[asyncio.Task[None]] = []
        for plugin_id, host_obj in hosts_snapshot.items():
            emit_lifecycle_event({"type": "plugin_shutdown_requested", "plugin_id": plugin_id, "time": now_iso()})
            if not isinstance(host_obj, _PluginHostContract):
                logger.warning(
                    "invalid plugin host object skipped during shutdown: plugin_id={}, host_type={}",
                    plugin_id,
                    type(host_obj).__name__,
                )
                continue
            tasks.append(asyncio.create_task(host_obj.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)))

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
        emit_lifecycle_event({"type": "server_shutdown_begin", "plugin_id": "server", "time": now_iso()})

        had_errors = False

        try:
            stop_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            had_errors = True
            logger.warning(
                "failed to stop message bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        runner = self._message_plane_runner
        self._message_plane_runner = None
        if runner is not None:
            try:
                runner.stop()
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
                had_errors = True
                logger.warning(
                    "failed to stop message_plane runner: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        try:
            await metrics_collector.stop()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            had_errors = True
            logger.warning(
                "failed to stop metrics collector: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        try:
            await status_manager.shutdown_status_consumer(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            had_errors = True
            logger.warning(
                "failed to stop status consumer: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        had_errors = (await self._shutdown_hosts()) or had_errors

        try:
            await bus_subscription_manager.stop()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            had_errors = True
            logger.warning(
                "failed to stop bus subscription manager: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        try:
            await plugin_router.stop()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            had_errors = True
            logger.warning(
                "failed to stop plugin router: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        try:
            await asyncio.wait_for(asyncio.to_thread(state.close_plugin_resources), timeout=1.5)
        except asyncio.TimeoutError:
            had_errors = True
            logger.warning("cleanup plugin communication resources timed out")
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            had_errors = True
            logger.warning(
                "failed to cleanup plugin communication resources: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

        emit_lifecycle_event({"type": "server_shutdown_complete", "plugin_id": "server", "time": now_iso()})
        return _ShutdownResult(timed_out=False, had_errors=had_errors)

    async def shutdown(self) -> None:
        try:
            result = await asyncio.wait_for(self._shutdown_internal(), timeout=PLUGIN_SHUTDOWN_TOTAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("server shutdown timed out after {}s", PLUGIN_SHUTDOWN_TOTAL_TIMEOUT)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(state.close_plugin_resources),
                    timeout=1.5,
                )
            except asyncio.TimeoutError:
                logger.warning("forced cleanup after timeout also timed out")
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
                logger.warning(
                    "forced cleanup after timeout failed: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
            return

        if result.had_errors:
            logger.warning("server shutdown completed with errors")
        else:
            logger.info("server shutdown completed")


_service = ServerLifecycleService()


def _final_log_flush() -> None:
    try:
        logger.info("final log flush before process exit")
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
