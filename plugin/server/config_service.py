"""
插件配置服务

提供插件配置的读取和更新功能。
"""
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT

logger = logging.getLogger("user_plugin_server")

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

try:
    import tomli_w
except ImportError:
    tomli_w = None


def get_plugin_config_path(plugin_id: str) -> Path:
    """获取插件的配置文件路径"""
    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    if not config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' configuration not found"
        )
    return config_file


def load_plugin_config(plugin_id: str) -> Dict[str, Any]:
    """
    加载插件配置
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        配置数据
    """
    if tomllib is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    config_path = get_plugin_config_path(plugin_id)
    
    try:
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)
        
        stat = config_path.stat()
        
        return {
            "plugin_id": plugin_id,
            "config": config_data,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "config_path": str(config_path)
        }
    except Exception as e:
        logger.exception(f"Failed to load config for plugin {plugin_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(e)}"
        ) from e


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并字典"""
    result = base.copy()
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def update_plugin_config(plugin_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新插件配置
    
    Args:
        plugin_id: 插件ID
        updates: 要更新的配置部分
    
    Returns:
        更新后的配置
    """
    if tomllib is None or tomli_w is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    config_path = get_plugin_config_path(plugin_id)
    
    try:
        # 读取现有配置
        with open(config_path, 'rb') as f:
            current_config = tomllib.load(f)
        
        # 深度合并
        merged_config = deep_merge(current_config, updates)
        
        # 写入文件
        with open(config_path, 'wb') as f:
            tomli_w.dump(merged_config, f)
        
        # 重新加载配置
        updated = load_plugin_config(plugin_id)
        
        logger.info(f"Updated config for plugin {plugin_id}")
        return {
            "success": True,
            "plugin_id": plugin_id,
            "config": updated["config"],
            "requires_reload": True,  # 配置更新通常需要重载插件
            "message": "Config updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update config for plugin {plugin_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update config: {str(e)}"
        ) from e

