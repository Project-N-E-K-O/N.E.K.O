"""
服务器生命周期管理

处理服务器启动和关闭时的插件加载、资源初始化等。
"""
import asyncio
import atexit
import logging
import os
import sys
import subprocess
import time
from pathlib import Path
import threading

from loguru import logger

from plugin.core.state import state
from plugin.core.registry import load_plugins_from_toml
from plugin.core.host import PluginProcessHost
from plugin.core.status import status_manager
from plugin.server.monitoring.metrics import metrics_collector
from plugin.server.plugin_router import plugin_router
from plugin.server.messaging.bus_subscriptions import bus_subscription_manager
from plugin.server.infrastructure.auth import generate_admin_code, set_admin_code
from plugin.server.services import _enqueue_lifecycle
from plugin.server.messaging.plane_bridge import start_bridge, stop_bridge
from plugin.message_plane.runner import build_message_plane_runner
from plugin.server.infrastructure.utils import now_iso
from plugin.settings import (
    PLUGIN_CONFIG_ROOT,
    NEKO_LOGURU_LEVEL,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT,
)


_message_plane_thread: threading.Thread | None = None
_message_plane_ingest_thread: threading.Thread | None = None
_message_plane_rpc = None
_message_plane_ingest = None
_message_plane_pub = None
_message_plane_proc: subprocess.Popen | None = None

_message_plane_runner = None


def _start_message_plane_embedded() -> None:
    global _message_plane_thread, _message_plane_ingest_thread, _message_plane_rpc, _message_plane_ingest, _message_plane_pub
    if _message_plane_thread is not None and _message_plane_thread.is_alive():
        return
    try:
        from plugin.message_plane.ingest_server import MessagePlaneIngestServer
        from plugin.message_plane.pub_server import MessagePlanePubServer
        from plugin.message_plane.rpc_server import MessagePlaneRpcServer
        from plugin.message_plane.stores import StoreRegistry, TopicStore
        from plugin.settings import (
            MESSAGE_PLANE_STORE_MAXLEN,
            MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
            MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
            MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
        )

        stores = StoreRegistry(default_store="messages")
        # conversations 是独立的 store，用于存储对话上下文（与 messages 分离）
        for name in ("messages", "events", "lifecycle", "runs", "export", "memory", "conversations"):
            stores.register(TopicStore(name=name, maxlen=MESSAGE_PLANE_STORE_MAXLEN))

        pub_srv = MessagePlanePubServer(endpoint=str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT))
        ingest_srv = MessagePlaneIngestServer(endpoint=str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT), stores=stores, pub_server=pub_srv)
        rpc_srv = MessagePlaneRpcServer(endpoint=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT), pub_server=pub_srv, stores=stores)

        ingest_thread = threading.Thread(target=ingest_srv.serve_forever, daemon=True, name="message-plane-ingest")
        ingest_thread.start()

        def _run_rpc() -> None:
            try:
                rpc_srv.serve_forever()
            finally:
                try:
                    rpc_srv.close()
                except Exception:
                    pass

        t = threading.Thread(target=_run_rpc, daemon=True, name="message-plane-rpc")
        t.start()

        _message_plane_thread = t
        _message_plane_ingest_thread = ingest_thread
        _message_plane_rpc = rpc_srv
        _message_plane_ingest = ingest_srv
        _message_plane_pub = pub_srv
        logger.info("message_plane embedded started")
    except Exception as e:
        try:
            logger.warning("message_plane embedded start failed: {}", e)
        except Exception:
            pass


def _stop_message_plane_embedded() -> None:
    global _message_plane_thread, _message_plane_ingest_thread, _message_plane_rpc, _message_plane_ingest, _message_plane_pub
    rpc_srv = _message_plane_rpc
    ingest_srv = _message_plane_ingest
    pub_srv = _message_plane_pub
    ingest_thread = _message_plane_ingest_thread
    rpc_thread = _message_plane_thread

    _message_plane_rpc = None
    _message_plane_ingest = None
    _message_plane_pub = None
    _message_plane_thread = None
    _message_plane_ingest_thread = None

    try:
        if rpc_srv is not None:
            rpc_srv.stop()
    except Exception:
        pass
    try:
        if ingest_srv is not None:
            ingest_srv.stop()
    except Exception:
        pass
    try:
        if ingest_thread is not None and ingest_thread.is_alive():
            ingest_thread.join(timeout=1.0)
    except Exception:
        pass
    try:
        if rpc_thread is not None and rpc_thread.is_alive():
            rpc_thread.join(timeout=1.0)
    except Exception:
        pass
    try:
        if pub_srv is not None:
            pub_srv.close()
    except Exception:
        pass


def _wait_tcp_ready(endpoint: str, *, timeout_s: float = 2.0) -> bool:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return True
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return True
    host, port_s = rest.rsplit(":", 1)
    host = host.strip() or "127.0.0.1"
    try:
        port = int(port_s)
    except Exception:
        return True
    deadline = time.time() + max(0.0, float(timeout_s))
    while time.time() < deadline:
        try:
            import socket

            with socket.create_connection((host, port), timeout=0.2):
                return True
        except Exception:
            try:
                time.sleep(0.05)
            except Exception:
                pass
    return False


def _start_message_plane_external() -> None:
    global _message_plane_proc
    if _message_plane_proc is not None and _message_plane_proc.poll() is None:
        return
    try:
        # Use the same interpreter (venv) to start an isolated message_plane process.
        cmd = [sys.executable, "-m", "plugin.message_plane.main"]
        _message_plane_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            close_fds=True,
        )
        logger.info("message_plane external process started pid={}", int(_message_plane_proc.pid))
        try:
            time.sleep(0.05)
        except Exception:
            pass
        try:
            rc = _message_plane_proc.poll()
            if rc is not None:
                logger.warning("message_plane external process exited immediately rc={}", int(rc))
        except Exception:
            pass
    except Exception as e:
        _message_plane_proc = None
        try:
            logger.warning("message_plane external process start failed: {}", e)
        except Exception:
            pass


def _stop_message_plane_external() -> None:
    global _message_plane_proc
    p = _message_plane_proc
    _message_plane_proc = None
    if p is None:
        return
    try:
        if p.poll() is None:
            p.terminate()
    except Exception:
        pass
    try:
        p.wait(timeout=1.0)
    except Exception:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass


def _factory(plugin_id: str, entry: str, config_path: Path, *, extension_configs: list | None = None) -> PluginProcessHost:
    """插件进程宿主工厂函数"""
    return PluginProcessHost(plugin_id=plugin_id, entry_point=entry, config_path=config_path, extension_configs=extension_configs)


async def startup() -> None:
    """
    服务器启动时的初始化
    
    1. 从 TOML 配置加载插件
    2. 启动插件的通信资源
    3. 启动状态消费任务
    """
    # 注意：日志格式已在 user_plugin_server.py 中通过 configure_default_logger() 统一配置
    # 插件子进程会在各自进程内单独配置 loguru

    try:
        class InterceptHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    level = record.levelname
                    msg = record.getMessage()
                    logger.opt(exception=record.exc_info).log(level, msg)
                except Exception:
                    pass

        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        for logger_name in (
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access",
            "fastapi",
            "user_plugin_server",
        ):
            logging_logger = logging.getLogger(logger_name)
            logging_logger.handlers = [InterceptHandler()]
            logging_logger.propagate = False
    except Exception:
        pass

    # 确保插件响应映射在主进程中提前初始化，避免子进程各自创建新的 Manager 字典
    _ = state.plugin_response_map  # 预初始化共享响应映射
    
    # 清理旧的状态（防止重启时残留）
    _enqueue_lifecycle({"type": "server_startup_begin", "plugin_id": "server", "time": now_iso()})
    with state.acquire_plugin_hosts_write_lock():
        # 关闭所有旧的插件进程
        for plugin_id, host in list(state.plugin_hosts.items()):
            try:
                if hasattr(host, 'process') and host.process and host.process.is_alive():
                    logger.debug(f"Cleaning up old plugin process: {plugin_id}")
                    host.process.terminate()
                    host.process.join(timeout=1.0)
            except Exception as e:
                logger.debug(f"Error cleaning up old plugin {plugin_id}: {e}")
        state.plugin_hosts.clear()
    
    with state.acquire_plugins_write_lock():
        state.plugins.clear()
    
    with state.acquire_event_handlers_write_lock():
        state.event_handlers.clear()
    
    logger.debug("Cleared old plugin state")

    await plugin_router.start()
    logger.info("Plugin router started")

    # Start message_plane before loading/starting any plugin processes to avoid startup races.
    try:
        global _message_plane_runner
        _message_plane_runner = build_message_plane_runner()
        _ = _message_plane_runner.start()
        try:
            if not _message_plane_runner.health_check(timeout_s=1.0):
                logger.warning("message_plane health_check failed (may still be starting)")
        except Exception:
            pass
    except Exception:
        _message_plane_runner = None
    
    # 加载插件
    load_plugins_from_toml(PLUGIN_CONFIG_ROOT, logger, _factory)

    with state.acquire_plugin_hosts_read_lock():
        for pid in list(state.plugin_hosts.keys()):
            _enqueue_lifecycle({"type": "plugin_loaded", "plugin_id": pid, "time": now_iso()})
    
    # 立即检查 plugin_hosts 状态（诊断日志，使用 debug 级别）
    with state.acquire_plugin_hosts_read_lock():
        plugin_hosts_after_load = dict(state.plugin_hosts)
        logger.debug(
            "Plugin hosts immediately after load_plugins_from_toml: {} plugins, keys: {}",
            len(plugin_hosts_after_load),
            list(plugin_hosts_after_load.keys())
        )
    
    with state.acquire_plugins_read_lock():
        plugin_keys = list(state.plugins.keys())
    logger.debug("Plugin registry after startup: {}", plugin_keys)
    
    # 再次检查 plugin_hosts（可能在 register_plugin 调用后发生变化）
    with state.acquire_plugin_hosts_read_lock():
        plugin_hosts_after_plugins = dict(state.plugin_hosts)
        logger.debug(
            "Plugin hosts after plugins registry: {} plugins, keys: {}",
            len(plugin_hosts_after_plugins),
            list(plugin_hosts_after_plugins.keys())
        )
        if len(plugin_hosts_after_load) != len(plugin_hosts_after_plugins):
            logger.warning(
                "Plugin hosts count changed from {} to {} after plugins registry! "
                "Lost plugins: {}, Gained plugins: {}",
                len(plugin_hosts_after_load),
                len(plugin_hosts_after_plugins),
                set(plugin_hosts_after_load.keys()) - set(plugin_hosts_after_plugins.keys()),
                set(plugin_hosts_after_plugins.keys()) - set(plugin_hosts_after_load.keys())
            )
    
    # 启动诊断：列出插件实例和公共方法
    _log_startup_diagnostics()
    
    await bus_subscription_manager.start()
    logger.info("Bus subscription manager started")

    try:
        start_bridge()
    except Exception:
        pass

    _enqueue_lifecycle({"type": "server_startup_ready", "plugin_id": "server", "time": now_iso()})
    
    # 启动所有插件的通信资源管理器
    with state.acquire_plugin_hosts_read_lock():
        plugin_hosts_copy = dict(state.plugin_hosts)
        logger.info("Found {} plugins in plugin_hosts: {}", len(plugin_hosts_copy), list(plugin_hosts_copy.keys()))
    
    if not plugin_hosts_copy:
        logger.warning(
            "No plugins found in plugin_hosts after loading. "
            "Plugins may need to be started manually via POST /plugin/{{plugin_id}}/start"
        )
    
    for plugin_id, host in plugin_hosts_copy.items():
        try:
            await host.start(message_target_queue=state.message_queue)
            logger.debug("Started communication resources for plugin {}", plugin_id)
        except Exception as e:
            logger.exception("Failed to start communication resources for plugin {}: {}", plugin_id, e)
    
    # 持锁获取 plugin_hosts 副本的统一 getter
    def get_plugin_hosts():
        with state.acquire_plugin_hosts_read_lock():
            return dict(state.plugin_hosts)

    # 启动状态消费任务
    await status_manager.start_status_consumer(
        plugin_hosts_getter=get_plugin_hosts
    )
    logger.info("Status consumer started")
    
    # 启动性能指标收集器
    await metrics_collector.start(
        plugin_hosts_getter=get_plugin_hosts
    )
    logger.info("Metrics collector started")
    
    # 生成并设置管理员验证码
    admin_code = generate_admin_code()
    set_admin_code(admin_code)
    # 在终端打印验证码（使用 print 确保输出到终端）
    print("\n" + "=" * 60, flush=True)
    print(f"🔐 管理员验证码: {admin_code}", flush=True)
    print("=" * 60, flush=True)
    print("请在请求头中添加: Authorization: Bearer <验证码>", flush=True)
    print("=" * 60 + "\n", flush=True)
    logger.info("Admin authentication code generated and displayed in terminal")  


async def _shutdown_internal() -> None:
    """内部关闭逻辑"""
    t0 = time.time()
    _enqueue_lifecycle({"type": "server_shutdown_begin", "plugin_id": "server", "time": now_iso()})

    try:
        stop_bridge()
    except Exception:
        pass

    try:
        global _message_plane_runner
        r = _message_plane_runner
        _message_plane_runner = None
        if r is not None:
            r.stop()
    except Exception:
        pass

    # 1. 停止性能指标收集器
    try:
        step_t0 = time.time()
        await metrics_collector.stop()
        logger.debug("Metrics collector stopped (cost={:.3f}s)", time.time() - step_t0)
    except Exception:
        logger.exception("Error stopping metrics collector")
    
    # 2. 关闭状态消费任务
    try:
        step_t0 = time.time()
        await status_manager.shutdown_status_consumer(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
        logger.debug("Status consumer stopped (cost={:.3f}s)", time.time() - step_t0)
    except Exception:
        logger.exception("Error shutting down status consumer")
    
    # 3. 关闭所有插件的资源
    step_t0 = time.time()
    with state.acquire_plugin_hosts_read_lock():
        plugin_hosts_snapshot = dict(state.plugin_hosts)
    shutdown_tasks = []
    for plugin_id, host in plugin_hosts_snapshot.items():
        _enqueue_lifecycle({"type": "plugin_shutdown_requested", "plugin_id": plugin_id, "time": now_iso()})
        shutdown_tasks.append(host.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT))
    
    # 并发关闭所有插件
    if shutdown_tasks:
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    logger.debug("Plugin hosts shutdown complete (cost={:.3f}s)", time.time() - step_t0)

    # 4. 停止插件间通信路由器（包括 ZeroMQ IPC server）
    # IMPORTANT: stop router only after all plugin processes have been shutdown,
    # otherwise plugins may still issue bus.* requests over ZeroMQ and fail with no fallback.
    try:
        step_t0 = time.time()
        try:
            await bus_subscription_manager.stop()
        except Exception:
            logger.exception("Error stopping bus subscription manager")
        await plugin_router.stop()
        logger.debug("Plugin router stopped (cost={:.3f}s)", time.time() - step_t0)
    except Exception:
        logger.exception("Error stopping plugin router")
    
    # 5. 清理插件间通信资源（队列和响应映射）
    try:
        step_t0 = time.time()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(state.cleanup_plugin_comm_resources),
                timeout=1.5,
            )
        except asyncio.TimeoutError:
            logger.warning("Plugin communication resources cleanup timed out; skipping")
        logger.debug("Plugin communication resources cleaned up (cost={:.3f}s)", time.time() - step_t0)
    except Exception:
        logger.exception("Error cleaning up plugin communication resources")

    # Ensure asyncio's default executor (used by asyncio.to_thread) is shut down.
    # Otherwise Python may block at interpreter exit while joining ThreadPoolExecutor threads,
    # requiring repeated Ctrl-C.
    try:
        loop = asyncio.get_running_loop()
        try:
            executor = getattr(loop, "_default_executor", None)
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    executor.shutdown(wait=False)
                try:
                    setattr(loop, "_default_executor", None)
                except Exception:
                    pass
            else:
                await asyncio.wait_for(loop.shutdown_default_executor(), timeout=1.5)
        except asyncio.TimeoutError:
            try:
                executor = getattr(loop, "_default_executor", None)
                if executor is not None:
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        executor.shutdown(wait=False)
                    try:
                        setattr(loop, "_default_executor", None)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        pass

    logger.debug("Shutdown internal completed (total_cost={:.3f}s)", time.time() - t0)
    _enqueue_lifecycle({"type": "server_shutdown_complete", "plugin_id": "server", "time": now_iso()})

def _log_shutdown_diagnostics() -> None:
    """记录关闭时的诊断信息，用于排查超时问题"""
    try:
        # 记录当前插件状态
        with state.acquire_plugin_hosts_read_lock():
            plugin_hosts_snapshot = dict(state.plugin_hosts)
        
        if plugin_hosts_snapshot:
            logger.error("Shutdown timeout diagnostics: {} plugin(s) still registered:", len(plugin_hosts_snapshot))
            for plugin_id, host in plugin_hosts_snapshot.items():
                try:
                    is_alive = False
                    exitcode = None
                    if hasattr(host, 'process') and host.process:
                        is_alive = host.process.is_alive()
                        exitcode = host.process.exitcode
                    
                    logger.error(
                        "  - Plugin '{}': process_alive={}, exitcode={}, host_type={}",
                        plugin_id,
                        is_alive,
                        exitcode,
                        type(host).__name__
                    )
                except Exception as e:
                    logger.error("  - Plugin '{}': failed to get status: {}", plugin_id, e)
        else:
            logger.error("Shutdown timeout diagnostics: no plugins registered")
        
        # 记录当前运行的任务
        try:
            tasks = [t for t in asyncio.all_tasks() if not t.done()]
            if tasks:
                logger.error("Shutdown timeout diagnostics: {} task(s) still running:", len(tasks))
                for task in tasks:
                    logger.error(
                        "  - Task '{}': done={}, cancelled={}, exception={}",
                        task.get_name(),
                        task.done(),
                        task.cancelled(),
                        task.exception() if task.done() else None
                    )
            else:
                logger.error("Shutdown timeout diagnostics: no tasks running")
        except Exception as e:
            logger.error("Shutdown timeout diagnostics: failed to enumerate tasks: {}", e)
    except Exception as e:
        logger.error("Shutdown timeout diagnostics: failed to collect diagnostics: {}", e, exc_info=True)


def _final_log_flush() -> None:
    """进程退出前的最后日志刷新"""
    try:
        # 强制刷新 loguru 的所有日志处理器
        logger.info("Final log flush before process exit")
        # loguru 会自动处理，但我们可以显式调用
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as e:
        # 最后的尝试：直接写到 stderr
        try:
            import sys
            print(f"Failed to flush logs: {e}", file=sys.stderr, flush=True)
        except:
            pass  # 真的没办法了喵


# 注册 atexit 处理器，确保进程退出时刷新日志
atexit.register(_final_log_flush)


async def shutdown() -> None:
    """
    服务器关闭时的清理
    
    增加超时保护，防止关闭过程无限挂起
    """
    logger.info("Shutting down all plugins...")
    
    try:
        # 给整个关闭过程设置超时
        await asyncio.wait_for(_shutdown_internal(), timeout=PLUGIN_SHUTDOWN_TOTAL_TIMEOUT)
        logger.info("All plugins have been gracefully shutdown.")
    except asyncio.TimeoutError:
        logger.error(
            "Plugin shutdown process timed out ({}s), forcing cleanup",
            PLUGIN_SHUTDOWN_TOTAL_TIMEOUT
        )
        
        # 记录详细的诊断信息
        _log_shutdown_diagnostics()
        
        # 尝试最后的清理
        try:
            state.cleanup_plugin_comm_resources()
            logger.debug("Plugin communication resources cleaned up during forced shutdown")
        except Exception as e:
            logger.debug("Failed to cleanup plugin comm resources during forced shutdown: {}", e)
        
        # 强制刷新日志
        _final_log_flush()
        
        # 强制退出，防止进程卡死
        os._exit(1)
    except Exception:
        logger.exception("Unexpected error during shutdown")
        # 即使出错也尝试刷新日志
        _final_log_flush()


def _log_startup_diagnostics() -> None:
    """记录启动诊断信息"""
    try:
        if state.plugin_instances:
            logger.info(f"startup-diagnostics: plugin instances loaded: {list(state.plugin_instances.keys())}")
            for pid, pobj in list(state.plugin_instances.items()):
                try:
                    methods = [m for m in dir(pobj) if callable(getattr(pobj, m)) and not m.startswith('_')]
                except (AttributeError, TypeError) as e:
                    logger.debug(f"startup-diagnostics: failed to enumerate methods for {pid}: {e}")
                    methods = []
                logger.info(f"startup-diagnostics: instance '{pid}' methods: {methods}")
        else:
            logger.info("startup-diagnostics: no plugin instances loaded")
    except (AttributeError, KeyError) as e:
        logger.warning(f"startup-diagnostics: failed to enumerate plugin instances: {e}", exc_info=True)

