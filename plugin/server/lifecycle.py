"""
服务器生命周期管理

处理服务器启动和关闭时的插件加载、资源初始化等。
"""
import asyncio
import logging
from pathlib import Path

from plugin.core.state import state
from plugin.runtime.registry import load_plugins_from_toml
from plugin.runtime.host import PluginProcessHost
from plugin.runtime.status import status_manager

logger = logging.getLogger("user_plugin_server")

# 插件配置根目录
PLUGIN_CONFIG_ROOT = Path(__file__).parent.parent / "plugins"


def _factory(pid: str, entry: str, config_path: Path) -> PluginProcessHost:
    """插件进程宿主工厂函数"""
    return PluginProcessHost(plugin_id=pid, entry_point=entry, config_path=config_path)


async def startup() -> None:
    """
    服务器启动时的初始化
    
    1. 从 TOML 配置加载插件
    2. 启动插件的通信资源
    3. 启动状态消费任务
    """
    # 加载插件
    load_plugins_from_toml(PLUGIN_CONFIG_ROOT, logger, _factory)
    logger.info("Plugin registry after startup: %s", list(state.plugins.keys()))
    
    # 启动诊断：列出插件实例和公共方法
    _log_startup_diagnostics()
    
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


async def shutdown() -> None:
    """
    服务器关闭时的清理
    
    1. 关闭状态消费任务
    2. 关闭所有插件的资源
    """
    logger.info("Shutting down all plugins...")
    
    # 关闭状态消费任务
    try:
        await status_manager.shutdown_status_consumer(timeout=5.0)
    except Exception as e:
        logger.exception("Error shutting down status consumer: {e}")
    
    # 关闭所有插件的资源
    shutdown_tasks = []
    for plugin_id, host in state.plugin_hosts.items():
        shutdown_tasks.append(host.shutdown(timeout=5.0))
    
    # 并发关闭所有插件
    if shutdown_tasks:
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    
    logger.info("All plugins have been gracefully shutdown.")


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

