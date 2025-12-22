"""
服务器生命周期管理

处理服务器启动和关闭时的插件加载、资源初始化等。
"""
import asyncio
import logging
import os
from pathlib import Path

from loguru import logger

from plugin.core.state import state
from plugin.runtime.registry import load_plugins_from_toml
from plugin.runtime.host import PluginProcessHost
from plugin.runtime.status import status_manager
from plugin.server.metrics_service import metrics_collector
from plugin.server.plugin_router import plugin_router
from plugin.settings import (
    PLUGIN_CONFIG_ROOT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT,
)


def _factory(plugin_id: str, entry: str, config_path: Path) -> PluginProcessHost:
    """插件进程宿主工厂函数"""
    return PluginProcessHost(plugin_id=plugin_id, entry_point=entry, config_path=config_path)


async def startup() -> None:
    """
    服务器启动时的初始化
    
    1. 从 TOML 配置加载插件
    2. 启动插件的通信资源
    3. 启动状态消费任务
    """
    # 确保插件响应映射在主进程中提前初始化，避免子进程各自创建新的 Manager 字典
    _ = state.plugin_response_map  # 预初始化共享响应映射
    
    # 清理旧的状态（防止重启时残留）
    with state.plugin_hosts_lock:
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
    
    with state.plugins_lock:
        state.plugins.clear()
    
    with state.event_handlers_lock:
        state.event_handlers.clear()
    
    logger.debug("Cleared old plugin state")
    
    # 加载插件
    load_plugins_from_toml(PLUGIN_CONFIG_ROOT, logger, _factory)
    with state.plugins_lock:
        plugin_keys = list(state.plugins.keys())
    logger.info("Plugin registry after startup: {}", plugin_keys)
    
    # 启动诊断：列出插件实例和公共方法
    _log_startup_diagnostics()
    
    # 启动插件间通信路由器
    await plugin_router.start()
    logger.info("Plugin router started")
    
    # 启动所有插件的通信资源管理器
    for plugin_id, host in state.plugin_hosts.items():
        try:
            await host.start(message_target_queue=state.message_queue)
            logger.debug(f"Started communication resources for plugin {plugin_id}")
        except Exception as e:
            logger.exception(f"Failed to start communication resources for plugin {plugin_id}: {e}")
    
    # 启动状态消费任务
    await status_manager.start_status_consumer(
        plugin_hosts_getter=lambda: state.plugin_hosts
    )
    logger.info("Status consumer started")
    
    # 启动性能指标收集器
    await metrics_collector.start(
        plugin_hosts_getter=lambda: state.plugin_hosts
    )
    logger.info("Metrics collector started")


async def _shutdown_internal() -> None:
    """内部关闭逻辑"""
    # 1. 停止插件间通信路由器
    try:
        await plugin_router.stop()
        logger.debug("Plugin router stopped")
    except Exception:
        logger.exception("Error stopping plugin router")
    
    # 2. 停止性能指标收集器
    try:
        await metrics_collector.stop()
        logger.debug("Metrics collector stopped")
    except Exception:
        logger.exception("Error stopping metrics collector")
    
    # 3. 关闭状态消费任务
    try:
        await status_manager.shutdown_status_consumer(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
        logger.debug("Status consumer stopped")
    except Exception:
        logger.exception("Error shutting down status consumer")
    
    # 4. 关闭所有插件的资源
    shutdown_tasks = []
    for plugin_id, host in state.plugin_hosts.items():
        shutdown_tasks.append(host.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT))
    
    # 并发关闭所有插件
    if shutdown_tasks:
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    
    # 5. 清理插件间通信资源（队列和响应映射）
    try:
        state.cleanup_plugin_comm_resources()
        logger.debug("Plugin communication resources cleaned up")
    except Exception:
        logger.exception("Error cleaning up plugin communication resources")

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
        logger.error(f"Plugin shutdown process timed out ({PLUGIN_SHUTDOWN_TOTAL_TIMEOUT}s), forcing cleanup")
        # 尝试最后的清理
        try:
            state.cleanup_plugin_comm_resources()
        except Exception as e:
            logger.debug(f"Failed to cleanup plugin comm resources during forced shutdown: {e}")
        
        # 强制退出，防止进程卡死
        os._exit(1)
    except Exception:
        logger.exception("Unexpected error during shutdown")


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

