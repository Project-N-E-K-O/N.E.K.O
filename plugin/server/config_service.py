"""
插件配置服务

提供插件配置的读取和更新功能。
"""
import logging
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT

# 跨平台文件锁支持
try:
    if sys.platform == 'win32':
        import msvcrt
        _has_file_lock = True
    else:
        import fcntl
        _has_file_lock = True
except ImportError:
    _has_file_lock = False

logger = logging.getLogger("user_plugin_server")


@contextmanager
def file_lock(file_obj):
    """
    跨平台文件锁上下文管理器
    
    使用文件锁保护文件操作，避免并发写入冲突。
    在 Unix/Linux/macOS 上使用 fcntl，在 Windows 上使用 msvcrt。
    
    Args:
        file_obj: 文件对象
    """
    if not _has_file_lock:
        # 如果没有文件锁支持，直接返回（不锁定）
        yield
        return
    
    try:
        if sys.platform == 'win32':
            # Windows 使用 msvcrt 锁定整个文件
            # 获取文件大小以锁定整个文件
            file_obj.seek(0, 2)  # 移动到文件末尾
            file_size = file_obj.tell()
            file_obj.seek(0)  # 回到文件开头
            if file_size > 0:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, file_size)
        else:
            # Unix/Linux/macOS 使用 fcntl
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == 'win32':
            try:
                file_obj.seek(0, 2)
                file_size = file_obj.tell()
                file_obj.seek(0)
                if file_size > 0:
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, file_size)
            except Exception:
                pass
        else:
            try:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

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
        # 使用文件锁保护读写操作，避免并发写入冲突
        with open(config_path, 'r+b') as f:
            with file_lock(f):
                # 读取现有配置
                current_config = tomllib.load(f)
                
                # 深度合并
                merged_config = deep_merge(current_config, updates)
                
                # 写入文件（先清空再写入）
                f.seek(0)
                f.truncate()
                tomli_w.dump(merged_config, f)
                f.flush()  # 确保数据写入磁盘
        
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

