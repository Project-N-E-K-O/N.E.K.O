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
from plugin.core.host import PluginProcessHost
from plugin.core.registry import scan_static_metadata, register_plugin, _parse_plugin_dependencies, _check_plugin_dependency
from plugin.core.status import status_manager
from plugin._types.models import PluginMeta, PluginAuthor
from plugin._types.exceptions import PluginNotFoundError
from plugin.settings import (
    PLUGIN_CONFIG_ROOT,
    PLUGIN_SHUTDOWN_TIMEOUT,
)
from plugin.sdk.version import SDK_VERSION
from plugin.server.services import _enqueue_lifecycle
from plugin.server.infrastructure.utils import now_iso


def _get_plugin_config_path(plugin_id: str) -> Optional[Path]:
    """获取插件的配置文件路径"""
    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    if config_file.exists():
        return config_file
    return None


# ========== 同步锁操作辅助函数 ==========
# 这些函数用于在线程池中执行锁操作，避免阻塞事件循环

def _get_plugin_host_sync(plugin_id: str):
    """在同步上下文中获取插件 host（带锁）"""
    with state.acquire_plugin_hosts_read_lock():
        return state.plugin_hosts.get(plugin_id)


def _get_plugin_hosts_snapshot_sync() -> Dict[str, Any]:
    """在同步上下文中获取 plugin_hosts 快照（带锁）"""
    with state.acquire_plugin_hosts_read_lock():
        return dict(state.plugin_hosts)


def _check_plugin_in_hosts_sync(plugin_id: str) -> bool:
    """在同步上下文中检查插件是否在 plugin_hosts 中（带锁）"""
    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


def _register_plugin_host_sync(plugin_id: str, host) -> Optional[Any]:
    """在同步上下文中注册插件 host（带锁），返回已存在的 host 或 None"""
    with state.acquire_plugin_hosts_write_lock():
        if plugin_id in state.plugin_hosts:
            existing = state.plugin_hosts.get(plugin_id)
            if existing and hasattr(existing, 'is_alive') and existing.is_alive():
                return existing
        state.plugin_hosts[plugin_id] = host
        return None


def _remove_plugin_host_sync(plugin_id: str) -> Optional[Any]:
    """在同步上下文中移除插件 host（带锁），返回被移除的 host"""
    with state.acquire_plugin_hosts_write_lock():
        return state.plugin_hosts.pop(plugin_id, None)


def _get_plugin_meta_sync(plugin_id: str) -> Optional[Dict[str, Any]]:
    """在同步上下文中获取插件元数据（带锁）"""
    with state.acquire_plugins_read_lock():
        return state.plugins.get(plugin_id)


def _remove_event_handlers_sync(plugin_id: str) -> None:
    """在同步上下文中移除插件的事件处理器（带锁）"""
    with state.acquire_event_handlers_write_lock():
        keys_to_remove = [
            key for key in list(state.event_handlers.keys())
            if key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:")
        ]
        for key in keys_to_remove:
            del state.event_handlers[key]


def _update_plugin_meta_sync(plugin_id: str, key: str, value: Any) -> None:
    """在同步上下文中更新插件元数据（带锁）"""
    with state.acquire_plugins_write_lock():
        meta = state.plugins.get(plugin_id)
        if isinstance(meta, dict):
            meta[key] = value
            state.plugins[plugin_id] = meta


def _register_or_replace_host_sync(plugin_id: str, host) -> int:
    """在同步上下文中注册或替换插件 host（带锁），返回当前 plugin_hosts 数量"""
    with state.acquire_plugin_hosts_write_lock():
        if plugin_id in state.plugin_hosts:
            existing_host = state.plugin_hosts.get(plugin_id)
            if existing_host is not None and existing_host is not host:
                logger.warning(
                    "Plugin {} already exists in plugin_hosts, will replace",
                    plugin_id
                )
        state.plugin_hosts[plugin_id] = host
        return len(state.plugin_hosts)


async def start_plugin(plugin_id: str, restore_state: bool = False) -> Dict[str, Any]:
    """
    启动插件
    
    Args:
        plugin_id: 插件ID
        restore_state: 是否恢复保存的状态（用于 unfreeze 场景）
    
    Returns:
        操作结果
    """
    import time
    _start_time = time.perf_counter()
    logger.info("[start_plugin] BEGIN: plugin_id={}, restore_state={}", plugin_id, restore_state)
    
    # 检查插件是否已运行（在线程池中执行锁操作，避免阻塞事件循环）
    loop = asyncio.get_running_loop()
    existing_host = await loop.run_in_executor(None, _get_plugin_host_sync, plugin_id)
    if existing_host and existing_host.is_alive():
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
    
    # 检查插件是否处于冻结状态
    if state.is_plugin_frozen(plugin_id) and not restore_state:
        raise HTTPException(
            status_code=409,
            detail=f"Plugin '{plugin_id}' is frozen. Use unfreeze_plugin to restore it."
        )
    
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
    
    # 文件读取是同步 I/O，放到线程池执行
    def _read_toml():
        with open(config_path, 'rb') as f:
            return tomllib.load(f)
    
    loop = asyncio.get_running_loop()
    conf = await loop.run_in_executor(None, _read_toml)
    logger.info("[start_plugin] TOML loaded: {:.3f}s", time.perf_counter() - _start_time)

    # Apply user profile overlay (including [plugin_runtime]) so manual start
    # respects the same runtime gating rules as startup load.
    try:
        from plugin.server.config_service import _apply_user_config_profiles

        if isinstance(conf, dict):
            # _apply_user_config_profiles 可能有文件 I/O，放到线程池执行
            conf = await loop.run_in_executor(
                None,
                lambda: _apply_user_config_profiles(
                    plugin_id=str(plugin_id),
                    base_config=conf,
                    config_path=config_path,
                )
            )
    except Exception:
        pass
    
    pdata = conf.get("plugin") or {}

    # 检查 plugin_runtime.enabled：如果插件在配置中被禁用，则不允许手动启动。
    from plugin.utils import parse_bool_config
    runtime_cfg = conf.get("plugin_runtime")
    enabled_val = True
    if isinstance(runtime_cfg, dict):
        enabled_val = parse_bool_config(runtime_cfg.get("enabled"), default=True)

    if not enabled_val:
        raise HTTPException(
            status_code=400,
            detail=f"Plugin '{plugin_id}' is disabled by plugin_runtime.enabled and cannot be started",
        )

    # Extension 类型不能作为独立进程启动，它们会被注入到宿主插件进程中
    if pdata.get("type") == "extension":
        host_conf = pdata.get("host")
        host_pid = host_conf.get("plugin_id") if isinstance(host_conf, dict) else "unknown"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Plugin '{plugin_id}' is an extension (type='extension') and cannot be started as "
                f"an independent process. It will be automatically injected into its host plugin "
                f"'{host_pid}' when the host starts."
            ),
        )

    entry = pdata.get("entry")
    if not entry or ":" not in entry:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entry point for plugin '{plugin_id}'"
        )
    
    # 检测并解决插件 ID 冲突
    from plugin.core.registry import _resolve_plugin_id_conflict
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
        # PluginProcessHost.__init__ 会同步创建进程，放到线程池执行避免阻塞事件循环
        logger.info("[start_plugin] Creating process host: {:.3f}s", time.perf_counter() - _start_time)
        host = await loop.run_in_executor(
            None,
            lambda: PluginProcessHost(
                plugin_id=plugin_id,
                entry_point=entry,
                config_path=config_path
            )
        )
        logger.info("[start_plugin] Process host created: {:.3f}s", time.perf_counter() - _start_time)
        
        # 启动通信资源
        await host.start(message_target_queue=state.message_queue)
        logger.info("[start_plugin] Communication started: {:.3f}s", time.perf_counter() - _start_time)
        
        # 检查进程是否还在运行（在获取锁之前）
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
        
        # 扫描元数据（在注册之前，避免在持有锁时导入模块）
        module_path, class_name = entry.split(":", 1)
        try:
            # importlib.import_module 是同步阻塞操作，必须放到线程池执行
            logger.info("[start_plugin] Importing module: {:.3f}s", time.perf_counter() - _start_time)
            loop = asyncio.get_running_loop()
            mod = await loop.run_in_executor(None, importlib.import_module, module_path)
            logger.info("[start_plugin] Module imported: {:.3f}s", time.perf_counter() - _start_time)
            cls = getattr(mod, class_name)
            
            # scan_static_metadata 使用 inspect.getmembers，可能较慢，放到线程池执行
            logger.info("[start_plugin] Scanning metadata: {:.3f}s", time.perf_counter() - _start_time)
            await loop.run_in_executor(None, scan_static_metadata, plugin_id, cls, conf, pdata)
            logger.info("[start_plugin] Metadata scanned: {:.3f}s", time.perf_counter() - _start_time)
            
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
                # 移除刚注册的 host（在线程池中执行锁操作）
                existing_host = await loop.run_in_executor(None, _remove_plugin_host_sync, plugin_id)
                
                # 在锁外关闭进程
                if existing_host is not None:
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
            
            # 如果 ID 被进一步重命名，更新 plugin_id
            if resolved_id != plugin_id:
                logger.warning(
                    "Plugin ID changed during registration from '{}' to '{}', will use new ID",
                    plugin_id, resolved_id
                )
                # 更新 host 的 plugin_id（如果可能）
                if hasattr(host, 'plugin_id'):
                    host.plugin_id = resolved_id
                plugin_id = resolved_id
            
            # 现在可以安全地注册到 plugin_hosts（在线程池中执行锁操作）
            total_plugins = await loop.run_in_executor(
                None, _register_or_replace_host_sync, plugin_id, host
            )
            logger.info(
                "Plugin {} successfully registered in plugin_hosts (pid: {}). Total running plugins: {}",
                plugin_id,
                host.process.pid if hasattr(host, 'process') and host.process else 'N/A',
                total_plugins
            )
        except Exception as e:
            logger.exception("Failed to initialize plugin {} after process start", plugin_id)
            try:
                # 在线程池中执行锁操作
                existing_host = await loop.run_in_executor(None, _remove_plugin_host_sync, plugin_id)
                if existing_host is not None:
                    await existing_host.shutdown(timeout=1.0)
                else:
                    await host.shutdown(timeout=1.0)
            except Exception:
                logger.warning("Failed to cleanup plugin {} after initialization failure", plugin_id)
            raise
        
        logger.info("[start_plugin] DONE: plugin_id={}, total={:.3f}s", plugin_id, time.perf_counter() - _start_time)
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
    # 检查插件是否存在（在线程池中执行锁操作，避免阻塞事件循环）
    loop = asyncio.get_running_loop()
    host = await loop.run_in_executor(None, _get_plugin_host_sync, plugin_id)
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
        
        # 从状态中移除（在线程池中执行锁操作）
        await loop.run_in_executor(None, _remove_plugin_host_sync, plugin_id)
        
        # 清理事件处理器（在线程池中执行锁操作）
        await loop.run_in_executor(None, _remove_event_handlers_sync, plugin_id)
        
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
    
    # 1. 停止插件（如果正在运行）（在线程池中执行锁操作）
    loop = asyncio.get_running_loop()
    is_running = await loop.run_in_executor(None, _check_plugin_in_hosts_sync, plugin_id)
    if is_running:
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


async def freeze_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    冻结插件：保存状态并停止进程
    
    冻结后插件进程会停止，但状态会被保存。
    只能通过 unfreeze_plugin 恢复冻结的插件。
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        操作结果
    """
    # 检查插件是否存在（在线程池中执行锁操作）
    loop = asyncio.get_running_loop()
    host = await loop.run_in_executor(None, _get_plugin_host_sync, plugin_id)
    if not host:
        # 检查是否已经冻结
        if state.is_plugin_frozen(plugin_id):
            raise HTTPException(
                status_code=409,
                detail=f"Plugin '{plugin_id}' is already frozen"
            )
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' is not running"
        )
    
    try:
        _enqueue_lifecycle({
            "type": "plugin_freeze_requested",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
        
        # 调用 host.freeze() 保存状态并停止进程
        result = await host.freeze(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
        
        if result.get("success"):
            # 从运行状态中移除（在线程池中执行锁操作）
            await loop.run_in_executor(None, _remove_plugin_host_sync, plugin_id)
            
            # 标记为冻结状态
            state.mark_plugin_frozen(plugin_id)
            
            logger.info(f"Plugin {plugin_id} frozen successfully")
            _enqueue_lifecycle({
                "type": "plugin_frozen",
                "plugin_id": plugin_id,
                "time": now_iso(),
                "data": result.get("data"),
            })
            return {
                "success": True,
                "plugin_id": plugin_id,
                "message": "Plugin frozen successfully",
                "freezable_keys": result.get("data", {}).get("freezable_keys", []),
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to freeze plugin: {result.get('error')}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to freeze plugin {plugin_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to freeze plugin: {str(e)}"
        ) from e


async def reload_all_plugins() -> Dict[str, Any]:
    """
    重载所有插件（并行优化版）
    
    1. 并行停止所有运行中的插件
    2. 并行启动所有插件
    
    Returns:
        操作结果，包含成功和失败的插件列表
    """
    import time as time_module
    _start_time = time_module.perf_counter()
    
    logger.info("Reloading all plugins (parallel mode)...")
    _enqueue_lifecycle({
        "type": "plugins_reload_all_requested",
        "time": now_iso(),
    })
    
    results: Dict[str, Any] = {
        "success": True,
        "reloaded": [],
        "failed": [],
        "skipped": [],
    }
    
    # 获取所有运行中的插件 ID（在线程池中执行锁操作）
    loop = asyncio.get_running_loop()
    hosts_snapshot = await loop.run_in_executor(None, _get_plugin_hosts_snapshot_sync)
    running_plugin_ids = list(hosts_snapshot.keys())
    
    if not running_plugin_ids:
        results["message"] = "No running plugins to reload"
        return results
    
    logger.info(f"Found {len(running_plugin_ids)} running plugins to reload: {running_plugin_ids}")
    
    # Phase 1: 并行停止所有插件
    logger.info("[reload_all] Phase 1: Stopping all plugins in parallel...")
    stop_tasks = []
    for plugin_id in running_plugin_ids:
        stop_tasks.append(_safe_stop_plugin(plugin_id))
    
    stop_results = await asyncio.gather(*stop_tasks, return_exceptions=True)
    
    # 收集成功停止的插件
    plugins_to_start = []
    for plugin_id, result in zip(running_plugin_ids, stop_results):
        if isinstance(result, Exception):
            logger.error(f"Failed to stop plugin {plugin_id}: {result}")
            results["failed"].append({
                "plugin_id": plugin_id,
                "error": f"Stop failed: {str(result)}"
            })
        else:
            plugins_to_start.append(plugin_id)
    
    logger.info(f"[reload_all] Phase 1 done: {len(plugins_to_start)} stopped, {len(results['failed'])} failed ({time_module.perf_counter() - _start_time:.3f}s)")
    
    # Phase 2: 并行启动所有插件
    if plugins_to_start:
        logger.info("[reload_all] Phase 2: Starting all plugins in parallel...")
        start_tasks = []
        for plugin_id in plugins_to_start:
            start_tasks.append(_safe_start_plugin(plugin_id))
        
        start_results = await asyncio.gather(*start_tasks, return_exceptions=True)
        
        for plugin_id, result in zip(plugins_to_start, start_results):
            if isinstance(result, Exception):
                logger.error(f"Failed to start plugin {plugin_id}: {result}")
                results["failed"].append({
                    "plugin_id": plugin_id,
                    "error": f"Start failed: {str(result)}"
                })
            else:
                results["reloaded"].append(plugin_id)
                logger.info(f"Plugin {plugin_id} reloaded successfully")
    
    total_time = time_module.perf_counter() - _start_time
    
    # 如果有失败的插件，标记整体为部分成功
    if results["failed"]:
        results["success"] = False
        results["message"] = f"Reloaded {len(results['reloaded'])} plugins, {len(results['failed'])} failed (took {total_time:.3f}s)"
    else:
        results["message"] = f"Successfully reloaded {len(results['reloaded'])} plugins (took {total_time:.3f}s)"
    
    logger.info(f"[reload_all] Completed in {total_time:.3f}s")
    
    _enqueue_lifecycle({
        "type": "plugins_reload_all_completed",
        "time": now_iso(),
        "data": {
            "reloaded_count": len(results["reloaded"]),
            "failed_count": len(results["failed"]),
            "duration_seconds": round(total_time, 3),
        },
    })
    
    return results


async def _safe_stop_plugin(plugin_id: str) -> bool:
    """安全停止插件，捕获异常"""
    try:
        await stop_plugin(plugin_id)
        return True
    except HTTPException as e:
        if e.status_code == 404:  # 插件不存在，视为已停止
            return True
        raise


async def _safe_start_plugin(plugin_id: str) -> bool:
    """安全启动插件，捕获异常"""
    await start_plugin(plugin_id)
    return True


async def unfreeze_plugin(plugin_id: str) -> Dict[str, Any]:
    """
    解冻插件：启动进程并恢复状态
    
    只能用于已冻结的插件。如果插件未冻结，请使用 start_plugin。
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        操作结果
    
    Raises:
        HTTPException: 如果插件未冻结或已在运行
    """
    # 检查插件是否处于冻结状态
    if not state.is_plugin_frozen(plugin_id):
        # 检查是否已在运行（在线程池中执行锁操作）
        loop = asyncio.get_running_loop()
        _already_running = await loop.run_in_executor(None, _check_plugin_in_hosts_sync, plugin_id)
        if _already_running:
            raise HTTPException(
                status_code=409,
                detail=f"Plugin '{plugin_id}' is already running. Use stop_plugin first if you want to restart."
            )
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' is not frozen. Use start_plugin for normal startup."
        )
    
    _enqueue_lifecycle({
        "type": "plugin_unfreeze_requested",
        "plugin_id": plugin_id,
        "time": now_iso(),
    })
    
    # 调用 start_plugin，它会自动检测并恢复冻结状态
    result = await start_plugin(plugin_id, restore_state=True)
    
    if result.get("success"):
        # 取消冻结状态标记
        state.unmark_plugin_frozen(plugin_id)
        
        _enqueue_lifecycle({
            "type": "plugin_unfrozen",
            "plugin_id": plugin_id,
            "time": now_iso(),
        })
        result["message"] = "Plugin unfrozen successfully"
        result["restored_from_frozen"] = True
    
    return result


def _validate_extension_sync(ext_id: str) -> tuple[Dict[str, Any], str, Optional[PluginProcessHost]]:
    """验证 Extension 元数据，返回 (ext_meta, host_plugin_id, host_or_None)。同步版本，在线程池中调用。"""
    with state.acquire_plugins_read_lock():
        ext_meta = state.plugins.get(ext_id)
    if not ext_meta or not isinstance(ext_meta, dict):
        raise HTTPException(status_code=404, detail=f"Extension '{ext_id}' not found")
    if ext_meta.get("type") != "extension":
        raise HTTPException(status_code=400, detail=f"'{ext_id}' is not an extension plugin")
    host_pid = ext_meta.get("host_plugin_id")
    if not host_pid:
        raise HTTPException(status_code=400, detail=f"Extension '{ext_id}' has no host_plugin_id")
    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(host_pid)
    return ext_meta, host_pid, host


async def _validate_extension(ext_id: str) -> tuple[Dict[str, Any], str, Optional[PluginProcessHost]]:
    """验证 Extension 元数据，返回 (ext_meta, host_plugin_id, host_or_None)。异步版本。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _validate_extension_sync, ext_id)


async def disable_extension(ext_id: str) -> Dict[str, Any]:
    """禁用 Extension：通知宿主进程卸载 Router，更新元数据状态。"""
    ext_meta, host_pid, host = await _validate_extension(ext_id)

    result: Dict[str, Any] = {"success": False, "ext_id": ext_id, "host_plugin_id": host_pid}

    if host and host.is_alive():
        try:
            data = await host.send_extension_command(
                "DISABLE_EXTENSION", {"ext_name": ext_id}, timeout=10.0,
            )
            result["success"] = True
            result["data"] = data
        except Exception as e:
            logger.exception("Failed to disable extension '{}' in host '{}'", ext_id, host_pid)
            raise HTTPException(status_code=500, detail=f"Failed to disable extension: {e}") from e
    else:
        # 宿主未运行，只更新元数据
        result["success"] = True
        result["message"] = "Host not running; extension metadata updated"

    # 更新元数据（在线程池中执行锁操作）
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _update_plugin_meta_sync, ext_id, "runtime_enabled", False)

    _enqueue_lifecycle({"type": "extension_disabled", "plugin_id": ext_id, "host_plugin_id": host_pid, "time": now_iso()})
    return result


async def enable_extension(ext_id: str) -> Dict[str, Any]:
    """启用 Extension：通知宿主进程重新注入 Router，更新元数据状态。"""
    ext_meta, host_pid, host = await _validate_extension(ext_id)
    ext_entry = ext_meta.get("entry_point", "")

    # 从 TOML 中读取 prefix
    config_path = ext_meta.get("config_path")
    prefix = ""
    if config_path:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]
        try:
            with Path(config_path).open("rb") as f:
                conf = tomllib.load(f)
            host_conf = conf.get("plugin", {}).get("host", {})
            prefix = host_conf.get("prefix", "")
        except Exception as exc:
            logger.warning("Failed to read prefix from config '{}': {}", config_path, exc)

    result: Dict[str, Any] = {"success": False, "ext_id": ext_id, "host_plugin_id": host_pid}

    if host and host.is_alive():
        try:
            data = await host.send_extension_command(
                "ENABLE_EXTENSION",
                {"ext_id": ext_id, "ext_entry": ext_entry, "prefix": prefix},
                timeout=10.0,
            )
            result["success"] = True
            result["data"] = data
        except Exception as e:
            logger.exception("Failed to enable extension '{}' in host '{}'", ext_id, host_pid)
            raise HTTPException(status_code=500, detail=f"Failed to enable extension: {e}") from e
    else:
        result["success"] = True
        result["message"] = "Host not running; extension will be injected when host starts"

    # 更新元数据（在线程池中执行锁操作）
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _update_plugin_meta_sync, ext_id, "runtime_enabled", True)

    _enqueue_lifecycle({"type": "extension_enabled", "plugin_id": ext_id, "host_plugin_id": host_pid, "time": now_iso()})
    return result

