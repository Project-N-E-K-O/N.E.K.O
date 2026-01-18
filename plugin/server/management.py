"""
插件管理服务

提供插件的启动、停止、重载等管理功能。
"""
import asyncio
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, cast

from fastapi import HTTPException
from loguru import logger

from plugin.core.state import state
from plugin.runtime.host import PluginProcessHost
from plugin.runtime.registry import scan_static_metadata, register_plugin, _parse_plugin_dependencies, _check_plugin_dependency
from plugin.runtime.status import status_manager
from plugin.api.models import PluginMeta, PluginAuthor
from plugin.api.exceptions import PluginNotFoundError
from plugin.settings import (
    PLUGIN_CONFIG_ROOT,
    PLUGIN_SHUTDOWN_TIMEOUT,
)
from plugin.sdk.version import SDK_VERSION
from plugin.server.services import _enqueue_lifecycle
from plugin.server.utils import now_iso


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
            _enqueue_lifecycle({
                "type": "plugin_start_skipped",
                "plugin_id": plugin_id,
                "time": now_iso(),
            })
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

    # Apply user profile overlay (including [plugin_runtime]) so manual start
    # respects the same runtime gating rules as startup load.
    try:
        from plugin.server.config_service import _apply_user_config_profiles

        if isinstance(conf, dict):
            conf = _apply_user_config_profiles(
                plugin_id=str(plugin_id),
                base_config=conf,
                config_path=config_path,
            )
    except Exception:
        pass
    
    pdata = conf.get("plugin") or {}

    # 检查 plugin_runtime.enabled：如果插件在配置中被禁用，则不允许手动启动。
    runtime_cfg = conf.get("plugin_runtime")
    enabled_val = True
    if isinstance(runtime_cfg, dict):
        v_enabled = runtime_cfg.get("enabled")
        if isinstance(v_enabled, bool):
            enabled_val = v_enabled
        elif isinstance(v_enabled, str):
            s = v_enabled.strip().lower()
            if s in ("0", "false", "no", "off"):
                enabled_val = False
            elif s in ("1", "true", "yes", "on"):
                enabled_val = True

    if not enabled_val:
        raise HTTPException(
            status_code=400,
            detail=f"Plugin '{plugin_id}' is disabled by plugin_runtime.enabled and cannot be started",
        )
    entry = pdata.get("entry")
    if not entry or ":" not in entry:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entry point for plugin '{plugin_id}'"
        )
    
    # 检测并解决插件 ID 冲突
    from plugin.runtime.registry import _resolve_plugin_id_conflict
    from plugin.settings import PLUGIN_ENABLE_ID_CONFLICT_CHECK

    original_plugin_id = plugin_id
    resolved_pid = _resolve_plugin_id_conflict(
        plugin_id,
        logger,
        config_path=config_path,
        entry_point=entry,
        plugin_data=pdata,
        purpose="load",
        enable_rename=bool(PLUGIN_ENABLE_ID_CONFLICT_CHECK),
    )
    if resolved_pid is None:
        raise HTTPException(
            status_code=409,
            detail=f"Plugin '{plugin_id}' is already loaded (duplicate detected)",
        )
    plugin_id = resolved_pid
    if plugin_id != original_plugin_id:
        logger.debug(
            "Plugin ID changed from '{}' to '{}' due to conflict (detailed warning logged above)",
            original_plugin_id,
            plugin_id,
        )
    
    # 创建并启动插件进程
    try:
        _enqueue_lifecycle({
            "type": "plugin_start_requested",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
        host = PluginProcessHost(
            plugin_id=plugin_id,
            entry_point=entry,
            config_path=config_path
        )
        
        # 启动通信资源
        await host.start(message_target_queue=state.message_queue)
        
        # 注册到状态
        with state.plugin_hosts_lock:
            # 注意：这里不要在持有 plugin_hosts_lock 时调用 _resolve_plugin_id_conflict。
            # _resolve_plugin_id_conflict 内部也会尝试获取 plugin_hosts_lock，threading.Lock 不是可重入锁，
            # 会导致自锁死，从而让整个 FastAPI 事件循环看起来“卡死”。
            if plugin_id in state.plugin_hosts:
                existing_host = state.plugin_hosts.get(plugin_id)
                if existing_host is not None and existing_host is not host:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Plugin '{plugin_id}' is already registered in plugin_hosts"
                    )
            
            # 检查进程是否还在运行
            if hasattr(host, 'process') and host.process:
                if not host.process.is_alive():
                    logger.error(
                        "Plugin {} process died immediately after startup (exitcode: {})",
                        plugin_id, host.process.exitcode
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Plugin '{plugin_id}' process died immediately after startup (exitcode: {host.process.exitcode})"
                    )
                logger.info(
                    "Plugin {} registered in plugin_hosts (pid: {}, alive: {})",
                    plugin_id, host.process.pid, host.process.is_alive()
                )
            else:
                logger.warning("Plugin {} host has no process object", plugin_id)
            
            state.plugin_hosts[plugin_id] = host
            logger.info(
                "Plugin {} successfully registered in plugin_hosts (pid: {}). Total running plugins: {}",
                plugin_id,
                host.process.pid if hasattr(host, 'process') and host.process else 'N/A',
                len(state.plugin_hosts)
            )
        
        # 验证注册是否成功
        with state.plugin_hosts_lock:
            if plugin_id not in state.plugin_hosts:
                logger.error(
                    "Plugin {} was not found in plugin_hosts after registration! This should not happen.",
                    plugin_id
                )
            else:
                logger.debug(
                    "Plugin {} confirmed in plugin_hosts after registration",
                    plugin_id
                )
        
        # 扫描元数据
        module_path, class_name = entry.split(":", 1)
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            scan_static_metadata(plugin_id, cls, conf, pdata)
            
            # 读取作者信息
            author_data = pdata.get("author")
            author = None
            if author_data and isinstance(author_data, dict):
                author = PluginAuthor(
                    name=author_data.get("name"),
                    email=author_data.get("email")
                )
            
            # 解析并检查插件依赖
            dependencies = _parse_plugin_dependencies(conf, cast(Any, logger), plugin_id)
            dependency_check_failed = False
            if dependencies:
                logger.info("Plugin {}: found {} dependency(ies)", plugin_id, len(dependencies))
                for dep in dependencies:
                    # 检查依赖（包括简化格式和完整格式）
                    satisfied, error_msg = _check_plugin_dependency(dep, cast(Any, logger), plugin_id)
                    if not satisfied:
                        logger.error(
                            "Plugin {}: dependency check failed: {}; cannot start",
                            plugin_id, error_msg
                        )
                        dependency_check_failed = True
                        break
                    logger.debug("Plugin {}: dependency check passed", plugin_id)
            
            # 如果依赖检查失败，抛出异常
            if dependency_check_failed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Plugin dependency check failed for plugin '{plugin_id}'"
                )
            
            # 注册插件元数据
            plugin_meta = PluginMeta(
                id=plugin_id,
                name=pdata.get("name", plugin_id),
                description=pdata.get("description", ""),
                version=pdata.get("version", "0.1.0"),
                sdk_version=SDK_VERSION,
                author=author,
                dependencies=dependencies,
            )
            resolved_id = register_plugin(
                plugin_meta,
                logger,
                config_path=config_path,
                entry_point=entry
            )
            
            # 如果 register_plugin 返回 None，说明检测到重复，需要清理
            if resolved_id is None:
                logger.warning(
                    "Plugin {} detected as duplicate in register_plugin, removing from plugin_hosts",
                    plugin_id
                )
                # 移除刚注册的 host
                with state.plugin_hosts_lock:
                    if plugin_id in state.plugin_hosts:
                        existing_host = state.plugin_hosts.pop(plugin_id)
                        # 尝试关闭进程
                        try:
                            if hasattr(existing_host, 'shutdown'):
                                await existing_host.shutdown(timeout=1.0)
                            elif hasattr(existing_host, 'process') and existing_host.process:
                                existing_host.process.terminate()
                                existing_host.process.join(timeout=1.0)
                        except Exception as e:
                            logger.warning(
                                "Error shutting down duplicate plugin {}: {}",
                                plugin_id, e, exc_info=True
                            )
                raise HTTPException(
                    status_code=400,
                    detail=f"Plugin '{plugin_id}' is already registered (duplicate detected)"
                )
            
            # 如果 ID 被进一步重命名（双重冲突），需要更新 plugin_hosts 中的键
            if resolved_id != plugin_id:
                logger.warning(
                    "Plugin ID changed during registration from '{}' to '{}', updating plugin_hosts",
                    plugin_id, resolved_id
                )
                # 更新 plugin_hosts 中的键
                with state.plugin_hosts_lock:
                    if plugin_id in state.plugin_hosts:
                        host = state.plugin_hosts.pop(plugin_id)
                        state.plugin_hosts[resolved_id] = host
                        # 更新 host 的 plugin_id（如果可能）
                        if hasattr(host, 'plugin_id'):
                            host.plugin_id = resolved_id
                        logger.info(
                            "Plugin host moved from '{}' to '{}' in plugin_hosts",
                            plugin_id, resolved_id
                        )
                plugin_id = resolved_id
        except Exception as e:
            logger.exception("Failed to initialize plugin {} after process start", plugin_id)
            try:
                with state.plugin_hosts_lock:
                    existing_host = state.plugin_hosts.pop(plugin_id, None)
                if existing_host is not None:
                    await existing_host.shutdown(timeout=1.0)
                else:
                    await host.shutdown(timeout=1.0)
            except Exception:
                logger.warning("Failed to cleanup plugin {} after initialization failure", plugin_id)
            raise
        
        logger.info(f"Plugin {plugin_id} started successfully")
        _enqueue_lifecycle({
            "type": "plugin_started",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
        response = {
            "success": True,
            "plugin_id": plugin_id,
            "message": "Plugin started successfully"
        }
        # 如果 ID 被重命名，在响应中提示
        if plugin_id != original_plugin_id:
            response["original_plugin_id"] = original_plugin_id
            response["message"] = f"Plugin started successfully (renamed from '{original_plugin_id}' to '{plugin_id}' due to ID conflict)"
        return response
        
    except HTTPException:
        raise
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
        _enqueue_lifecycle({
            "type": "plugin_stop_requested",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
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
        _enqueue_lifecycle({
            "type": "plugin_stopped",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
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
    _enqueue_lifecycle({
        "type": "plugin_reload_requested",
        "plugin_id": plugin_id,
        "time": now_iso(),
    })
    
    # 1. 停止插件（如果正在运行）
    if plugin_id in state.plugin_hosts:
        try:
            await stop_plugin(plugin_id)
        except HTTPException as e:
            if e.status_code != 404:  # 如果插件不存在，继续启动
                raise
    
    # 2. 重新启动插件
    result = await start_plugin(plugin_id)
    _enqueue_lifecycle({
        "type": "plugin_reloaded",
        "plugin_id": plugin_id,
        "time": now_iso(),
    })
    return result

