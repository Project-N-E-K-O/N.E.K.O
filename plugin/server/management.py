"""
插件管理服务

提供插件的启动、停止、重载等管理功能。
"""
import asyncio
import logging
import importlib
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import HTTPException

from plugin.core.state import state
from plugin.runtime.host import PluginProcessHost
from plugin.runtime.registry import scan_static_metadata, register_plugin
from plugin.runtime.status import status_manager
from plugin.api.models import PluginMeta
from plugin.api.exceptions import PluginNotFoundError
from plugin.settings import (
    PLUGIN_CONFIG_ROOT,
    PLUGIN_SHUTDOWN_TIMEOUT,
)
from plugin.sdk.version import SDK_VERSION

logger = logging.getLogger("user_plugin_server")


def _get_plugin_config_path(plugin_id: str) -> Optional[Path]:
    """获取插件的配置文件路径"""
    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    if config_file.exists():
        return config_file
    return None


async def start_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    启动插件
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        操作结果
    """
    # 检查插件是否已运行
    if plugin_id in state.plugin_hosts:
        host = state.plugin_hosts[plugin_id]
        if host.is_alive():
            return {
                "success": True,
                "plugin_id": plugin_id,
                "message": "Plugin is already running"
            }
    
    # 获取配置路径
    config_path = _get_plugin_config_path(plugin_id)
    if not config_path:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' configuration not found"
        )
    
    # 读取配置
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="TOML library not available. Please install 'tomli' package."
            ) from None
    
    with open(config_path, 'rb') as f:
        conf = tomllib.load(f)
    
    pdata = conf.get("plugin") or {}
    entry = pdata.get("entry")
    if not entry or ":" not in entry:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entry point for plugin '{plugin_id}'"
        )
    
    # 创建并启动插件进程
    try:
        host = PluginProcessHost(
            plugin_id=plugin_id,
            entry_point=entry,
            config_path=config_path
        )
        
        # 启动通信资源
        await host.start(message_target_queue=state.message_queue)
        
        # 注册到状态
        with state.plugin_hosts_lock:
            state.plugin_hosts[plugin_id] = host
        
        # 扫描元数据
        module_path, class_name = entry.split(":", 1)
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            scan_static_metadata(plugin_id, cls, conf, pdata)
            
            # 注册插件元数据
            plugin_meta = PluginMeta(
                id=plugin_id,
                name=pdata.get("name", plugin_id),
                description=pdata.get("description", ""),
                version=pdata.get("version", "0.1.0"),
                sdk_version=SDK_VERSION,
            )
            register_plugin(plugin_meta)
        except Exception as e:
            logger.warning(f"Failed to scan metadata for plugin {plugin_id}: {e}")
        
        logger.info(f"Plugin {plugin_id} started successfully")
        return {
            "success": True,
            "plugin_id": plugin_id,
            "message": "Plugin started successfully"
        }
        
    except Exception as e:
        logger.exception(f"Failed to start plugin {plugin_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start plugin: {str(e)}"
        ) from e


async def stop_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    停止插件
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        操作结果
    """
    # 检查插件是否存在
    host = state.plugin_hosts.get(plugin_id)
    if not host:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' is not running"
        )
    
    try:
        # 停止插件
        await host.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
        
        # 从状态中移除
        with state.plugin_hosts_lock:
            if plugin_id in state.plugin_hosts:
                del state.plugin_hosts[plugin_id]
        
        # 清理事件处理器
        with state.event_handlers_lock:
            keys_to_remove = [
                key for key in list(state.event_handlers.keys())
                if key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:")
            ]
            for key in keys_to_remove:
                del state.event_handlers[key]
        
        logger.info(f"Plugin {plugin_id} stopped successfully")
        return {
            "success": True,
            "plugin_id": plugin_id,
            "message": "Plugin stopped successfully"
        }
        
    except Exception as e:
        logger.exception(f"Failed to stop plugin {plugin_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop plugin: {str(e)}"
        ) from e


async def reload_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    重载插件
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        操作结果
    """
    logger.info(f"Reloading plugin {plugin_id}")
    
    # 1. 停止插件（如果正在运行）
    if plugin_id in state.plugin_hosts:
        try:
            await stop_plugin(plugin_id)
        except HTTPException as e:
            if e.status_code != 404:  # 如果插件不存在，继续启动
                raise
    
    # 2. 重新启动插件
    return await start_plugin(plugin_id)

