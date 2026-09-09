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
from plugin.server.application.install_source import StartupReconciler, get_install_source_manager
from plugin.server.application.plugins import PluginLifecycleService, PluginRegistryService
from plugin.server.application.plugins.layout_migration import migrate_legacy_plugin_layout
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.plane_bridge import (
    ingest_auth_token,
    refresh_ingest_endpoint,
    start_bridge,
    stop_bridge,
)
from plugin.server.messaging.proactive_bridge import (
    start_proactive_bridge,
    stop_proactive_bridge,
    wait_for_proactive_subscriber,
)
from plugin.server.messaging.plane_runner import MessagePlaneRunner, build_message_plane_runner
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.messaging.request_router import plugin_router
from plugin.settings import PLUGIN_SHUTDOWN_TIMEOUT, PLUGIN_SHUTDOWN_TOTAL_TIMEOUT
from utils.logger_config import get_module_logger

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"

if _EMBEDDED_BY_AGENT:
    logger = get_module_logger(__name__, "Agent")
else:
    logger = get_logger("server.lifecycle")


# 等 ProactiveBridge 的 SUB 连上的上限。比它自己那一秒的 PUB bind 等待留出
# 余量，又短到起不来时不会让人以为应用卡死了。
_PROACTIVE_SUBSCRIBER_WAIT_SECONDS = 3.0


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
        # Guards ``ensure_delivery_path_started`` so the startup lifecycle and a
        # concurrent manual plugin start cannot both bring the plane up.
        self._delivery_path_lock = asyncio.Lock()
        self._delivery_path_started = False
        # Closed by ``shutdown`` under the same lock. Without it, a manual plugin
        # start could win the lock after shutdown reset the flag and stand a fresh
        # plane up that teardown has already walked past -- orphan threads and
        # sockets, plus a ``True`` flag describing a plane nobody owns.
        self._delivery_path_shutting_down = False

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

    async def _check_message_plane_health(self) -> bool:
        """Probe the current runner. Never raises; a failed probe is ``False``."""
        runner = self._message_plane_runner
        if runner is None:
            return False
        try:
            health_check_async = getattr(runner, "health_check_async", None)
            if health_check_async is not None and asyncio.iscoroutinefunction(health_check_async):
                return bool(await health_check_async(timeout_s=1.0))
            # Fallback: runner only exposes the sync API — offload to a worker thread so we
            # never block the event loop on the ~1s TCP probe + RPC round-trip.
            return bool(await asyncio.to_thread(runner.health_check, timeout_s=1.0))
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.warning(
                "message_plane health check failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return False

    async def _start_message_plane(self) -> bool:
        """Start the plane, or verify the one already standing. Returns usability."""
        if self._message_plane_runner is not None:
            # An earlier attempt in this run already stood the plane up and only
            # a later step (endpoint refresh, or a bridge) failed. Building a
            # second runner would strand the first one's threads and sockets --
            # and worse, the first still holds the configured ports, so the
            # replacement's port fallback picks DIFFERENT ones and the bridge
            # ends up refreshed onto a plane that is not the one running. Both
            # bridges guard on their own thread being alive, so reusing here
            # makes a retry re-run exactly the parts that failed and nothing else.
            #
            # Re-probed, not assumed: a non-null runner only means ``start()``
            # did not raise. The first attempt's probe may have failed or come
            # back false, and that path deliberately leaves the runner assigned
            # ("it may still be starting"). Reusing it unverified would let the
            # bridges come up against a plane that never arrived and latch the
            # whole path as ready -- push_message would keep answering
            # submitted=True with nothing behind it, which is the exact failure
            # this branch exists to prevent.
            if not await self._check_message_plane_health():
                logger.warning(
                    "message_plane is still not healthy; not reusing it yet "
                    "(kept for the next retry rather than rebuilt, so its ports "
                    "are not handed to a second runner)"
                )
                return False
            logger.debug("message_plane already running; reusing it for this retry")
            return True
        # Same process mints the credential and starts the plane that must
        # accept it; start_bridge() below is the only writer.
        self._message_plane_runner = build_message_plane_runner(
            auth_token=ingest_auth_token(),
        )
        self._message_plane_runner.start()
        if not await self._check_message_plane_health():
            # Symmetric with the reuse branch, and for the same reason. Latching
            # here would be permanent: nothing re-probes a path already marked
            # started, so a plane that never arrived would keep answering
            # ``submitted=True`` for the life of the process.
            #
            # Reporting failure costs nothing the old tolerance was buying. The
            # runner stays assigned and both bridges still come up -- the next
            # entry takes the reuse branch above, re-probes, and latches as soon
            # as the plane is actually there. A plane that was merely slow (the
            # probe is a 1s bound) recovers on its own; one that never arrives
            # keeps saying so.
            logger.warning(
                "message_plane health check returned false; it may still be "
                "starting, so the path is left unlatched for the next entry to "
                "re-probe rather than marked ready"
            )
            return False
        return True

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

    @serialized_plugin_operation
    async def _migrate_layout_and_reconcile_install_sources(self) -> None:
        """Run startup layout mutations under the shared plugin operation lock."""

        try:
            migration_result = await migrate_legacy_plugin_layout()
            if migration_result.migrated:
                logger.info(
                    "legacy plugin layout migration completed: migrated={}",
                    list(migration_result.migrated),
                )
            for issue in migration_result.blocked:
                logger.warning(
                    "legacy plugin layout migration blocked: code={}, plugin_id={}, path={}, error={}",
                    issue.code,
                    issue.plugin_id,
                    issue.path,
                    issue.message,
                )
        except Exception as exc:
            # Migration is safety infrastructure, but a damaged legacy plugin
            # must not prevent the server from starting in read-only/degraded
            # mode. Registry refresh below may still discover valid roots.
            logger.error(
                "legacy plugin layout migration failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return

        # In embedded-agent mode the HTTP lifespan starts before the
        # externally managed plugin lifecycle. Its install-source manager has
        # therefore already reconciled the pre-migration layout and may have
        # soft-deleted entries that the migration just restored. Replay it
        # while the same operation lock remains held.
        try:
            install_source_manager = get_install_source_manager()
            if install_source_manager is not None:
                await StartupReconciler(install_source_manager).run()
        except Exception as exc:
            logger.error(
                "install-source reconciliation after layout migration failed: "
                "err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

    async def startup(self) -> None:
        # Reopen the gate a previous shutdown closed: this service instance is
        # reused across a restart in the same process, and a latched-closed gate
        # would make every delivery-path start a no-op for the new run.
        async with self._delivery_path_lock:
            self._delivery_path_shutting_down = False

        try:
            emit_lifecycle_event({"type": "server_startup_begin", "plugin_id": "server", "time": now_iso()})
        except Exception as exc:
            logger.warning("failed to emit server_startup_begin event: {}", exc)

        self._clear_runtime_state()

        await self._migrate_layout_and_reconcile_install_sources()

        await ensure_plugin_messaging_started()

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

        await self.ensure_delivery_path_started()

        await self._refresh_registry_and_start_autostart_plugins()

        await bus_subscription_manager.start()
        logger.debug("bus subscription manager started")

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

    async def ensure_delivery_path_started(self) -> bool:
        """Bring up message plane + both bridges. Idempotent; safe to call twice.

        Returns whether plugin messages can actually be delivered. Callers start
        plugins either way -- refusing would break tool calls, which travel the
        router and work fine -- but they must not do it silently: a plugin
        brought up over a dead path pushes alerts that go nowhere while
        ``push_message()`` answers ``submitted=True``, which is the failure this
        whole mechanism exists to end.

        Everything a plugin says to the character -- ``push_message``, alerts,
        screenshots -- travels this path. The request router does NOT: it carries
        entry triggers and ``@llm_tool`` calls, which is why a half-started server
        can dispatch tools perfectly while every proactive message vanishes.

        That asymmetry was reachable in production. ``ensure_plugin_messaging_started``
        (the lazy path behind ``POST /plugin/{id}/start``) started only the router,
        so a plugin started by hand before the startup lifecycle reached this block
        ran with no delivery path at all -- while ``push_message()`` kept answering
        ``submitted=True``. Observed 2026-09-10: the Minecraft plugin dispatched a
        task fine, then emitted four priority-9 alerts including the character's
        own death, and not one reached the dialog LLM. Nothing logged above DEBUG.
        Both entry points funnel through here now.
        """
        async with self._delivery_path_lock:
            if self._delivery_path_shutting_down:
                # Teardown owns the plane from here on. Standing a new one up now
                # would leak it past ``_shutdown_internal``, which has already
                # decided what there was to stop.
                logger.debug("delivery path start skipped: shutting down")
                return False
            if self._delivery_path_started:
                return True
            # Latch only a path that actually came up. Every step below swallows
            # its own failure so one broken component cannot abort startup -- but
            # latching a failed attempt would make the SECOND entry point useless,
            # and recovery is the whole reason that entry point exists: a later
            # manual plugin start would skip the retry and the plugin would stay
            # mute until the process restarts.
            self._delivery_path_started = await self._start_delivery_path_locked()
            if not self._delivery_path_started:
                # Worded for what is actually known, which is less than "broken".
                # Both bridges came up regardless of this outcome (nothing above
                # returns early), and their sockets reconnect on their own once
                # the plane binds -- so a plane that was merely slow needs no
                # further entry and heals silently. Calling that a delivery
                # outage would send an operator hunting a fault that is not
                # there, and would dull the one signal that does mean messages
                # are being dropped: the per-message "message NOT delivered"
                # warnings from the plugin's own push path.
                logger.warning(
                    "delivery path not verified: the bridges are up and will "
                    "attach on their own if the plane is merely slow, and the "
                    "next entry re-probes it. If it never arrives, pushes are "
                    "accepted and dropped -- look for 'message NOT delivered'"
                )
            return self._delivery_path_started

    async def _start_delivery_path_locked(self) -> bool:
        """Returns whether the path is usable. See the caller for why that matters."""
        ok = True
        try:
            if not await self._start_message_plane():
                ok = False
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError, TimeoutError) as exc:
            logger.warning(
                "message_plane start failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            self._message_plane_runner = None
            ok = False

        # 两条 bridge 先于任何插件起来。autostart 插件可以在自己的 startup 钩
        # 子里调 push_message()，而 ProactiveBridge 的 SUB 要在它自己的线程里
        # 等约一秒才连上——PUB/SUB 对缺席的订阅方是丢弃，所以那扇窗口里推的
        # 消息角色永远不会说出口，而 push_message() 已经回了 submitted=True。
        #
        # 顺序只是第一步：SUB 的连接延迟本身还在（bridge 线程要先等约一秒让
        # message_plane 的 PUB bind 完），所以下面在放插件进来之前会等
        # wait_for_proactive_subscriber。
        #
        # ⚠️ 即便如此也不是数学上的关闭：ZMQ 的 SUBSCRIBE 返回不代表 PUB 端
        # 已经处理完这条订阅（slow joiner），极窄的一段仍在。要关死得让 bridge
        # 起来后补读一次 store 并按 message_id 去重，代价是重复投递的风险。
        #
        # 另外更正一处此前写错的机制：plane bridge **不会**因为没 start 就拒
        # 收。`_Bridge._enabled` 读的是 MESSAGE_PLANE_BRIDGE_ENABLED 这个配置
        # 开关（构造时读一次），不是「start() 跑没跑」——start() 之前
        # enqueue_delta 照常入队，线程起来后排空。实测 publish_record 在
        # start_bridge() 之前返回 True。
        # 必须在 runner 起完之后、bridge 起之前：配置端口被占时 runner 会挑
        # 一个备用端口并写回环境变量，而 _bridge 是 import 期就建好的、那时
        # 冻结的还是原来那个地址。不刷新的话，端口一冲突，push_message /
        # frames / conversations 全都发向那个被占的端点，而调用方已经拿到
        # submitted=True——正是这条路要消灭的那种静默不投递。
        try:
            refresh_ingest_endpoint()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to refresh ingest endpoint: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            # Counts as a failure: an un-refreshed bridge publishes to whatever
            # endpoint was frozen at import, which is the silent non-delivery the
            # comment above describes.
            ok = False

        try:
            start_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to start message bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            ok = False

        try:
            start_proactive_bridge()
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.warning(
                "failed to start proactive bridge: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            ok = False

        # 等订阅方真正连上再放插件进来。bridge 的线程自己要先睡约一秒等
        # message_plane 的 PUB bind，那一秒正好是窗口本身——只把 start 挪到
        # 前面并不能让它变窄。有界等待：bridge 被禁用或已经死了就立刻返回，
        # 起不来也不能把整个启动挂在这儿。
        #
        # NOT counted as a failure: the SUB may still connect after this bounded
        # wait, and the components themselves are up. Retrying the whole path on
        # a slow subscriber would tear down a working plane to rebuild it.
        if not await asyncio.to_thread(
            wait_for_proactive_subscriber, _PROACTIVE_SUBSCRIBER_WAIT_SECONDS
        ):
            logger.warning(
                "proactive subscriber not ready after {}s; autostart plugins "
                "pushing from their startup hook may go unheard",
                _PROACTIVE_SUBSCRIBER_WAIT_SECONDS,
            )

        return ok

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
        # Close the gate under the same lock the starter takes, so an
        # ``ensure_delivery_path_started`` already in flight finishes before
        # teardown proceeds and no later one can start a plane behind it. The
        # flag is cleared here too -- leaving it latched would make the starter a
        # silent no-op for the rest of the process. ``startup`` reopens the gate.
        async with self._delivery_path_lock:
            self._delivery_path_shutting_down = True
            self._delivery_path_started = False
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


async def ensure_plugin_messaging_started() -> bool:
    """Start plugin messaging without running the full plugin lifecycle.

    Both halves, not just the router. A plugin started through this path (the
    lazy call behind ``POST /plugin/{id}/start``) pushes messages the moment it
    comes up, and the router does not carry those -- see
    ``ServerLifecycleService.ensure_delivery_path_started`` for what a
    router-only start actually looked like in production.

    Returns whether the delivery half is usable, so the caller can say which
    plugin it is about to start over a dead path instead of leaving that to be
    reconstructed from missing log lines afterwards.
    """
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

    # Idempotent: the startup lifecycle calls the same method and whichever runs
    # first wins. Never skipped on the grounds that "startup will do it" -- that
    # assumption is exactly what left a hand-started plugin mute.
    return await _service.ensure_delivery_path_started()


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
