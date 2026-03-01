from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import multiprocessing
import os
import sys
import threading
import time
import hashlib
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Type
from multiprocessing import Queue
from queue import Empty

from loguru import logger

from plugin._types.events import EVENT_META_ATTR, EventHandler
from plugin.sdk.decorators import WORKER_MODE_ATTR, PERSIST_ATTR
from plugin.core.state import state
from plugin.core.context import PluginContext
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.worker import WorkerExecutor
from plugin._types.models import HealthCheckResponse
from plugin._types.exceptions import (
    PluginLifecycleError,
    PluginTimerError,
    PluginEntryNotFoundError,
    PluginExecutionError,
    PluginError,
)
from plugin.settings import (
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
    PROCESS_TERMINATE_TIMEOUT,
)
from plugin.sdk.router import PluginRouter
from plugin.sdk.bus.types import dispatch_bus_change


def _sanitize_plugin_id(raw: Any, max_len: int = 64) -> str:
    s = str(raw)
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in s)
    safe = safe.strip("_-")
    if not safe:
        safe = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if len(safe) > max_len:
        digest = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]
        safe = f"{safe[:max_len - 13]}_{digest}"
    return safe


def _inject_extensions(
    instance: Any,
    host_plugin_id: str,
    host_config_path: Path,
    logger: Any,
    extension_configs: list | None = None,
) -> None:
    """扫描所有 type=extension 且 host.plugin_id 匹配的插件，注入其 Router 到宿主实例。

    在宿主子进程内部调用，发生在 instance 创建后、collect_entries 之前。
    Extension 的 entry 指向一个 PluginRouter 子类，实例化后通过 include_router 注入。

    如果 *extension_configs* 不为空，直接使用预构建的映射（避免全量扫描 TOML）。
    每个元素格式: {"ext_id": str, "ext_entry": str, "prefix": str}
    """
    # 如果主进程已预构建映射，直接使用
    if extension_configs:
        injected_count = 0
        for ext_cfg in extension_configs:
            ext_id = ext_cfg.get("ext_id", "unknown")
            ext_entry = ext_cfg.get("ext_entry", "")
            prefix = ext_cfg.get("prefix", "")
            if not ext_entry or ":" not in ext_entry:
                logger.warning("[Extension] Pre-built config for '{}' has invalid entry, skipping", ext_id)
                continue
            module_path, class_name = ext_entry.split(":", 1)
            try:
                mod = importlib.import_module(module_path)
                router_cls = getattr(mod, class_name)
            except Exception as e:
                logger.warning("[Extension] Failed to import extension '{}': {}", ext_id, e)
                continue
            if not (isinstance(router_cls, type) and issubclass(router_cls, PluginRouter)):
                logger.warning("[Extension] '{}' is not a PluginRouter subclass, skipping", ext_id)
                continue
            try:
                router_instance = router_cls(prefix=prefix, name=ext_id)
                instance.include_router(router_instance)
                injected_count += 1
                logger.info("[Extension] Injected '{}' into host '{}' (pre-built)", ext_id, host_plugin_id)
            except Exception as e:
                logger.warning("[Extension] Failed to inject '{}': {}", ext_id, e)
        if injected_count > 0:
            logger.info("[Extension] Total {} extension(s) injected into host '{}'", injected_count, host_plugin_id)
        return
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            logger.debug("[Extension] tomllib/tomli not available, skipping extension injection")
            return

    # 优先使用 settings 中的 PLUGIN_CONFIG_ROOT，回退到路径推导
    try:
        from plugin.settings import PLUGIN_CONFIG_ROOT
        plugin_config_root = PLUGIN_CONFIG_ROOT
    except Exception:
        plugin_config_root = host_config_path.parent.parent

    try:
        if not plugin_config_root.exists():
            return
    except Exception:
        return

    injected_count = 0
    for toml_path in plugin_config_root.glob("*/plugin.toml"):
        try:
            with toml_path.open("rb") as f:
                conf = tomllib.load(f)
            pdata = conf.get("plugin") or {}

            # 只处理 type=extension
            if pdata.get("type") != "extension":
                continue

            # 检查宿主匹配
            host_conf = pdata.get("host")
            if not isinstance(host_conf, dict):
                continue
            if host_conf.get("plugin_id") != host_plugin_id:
                continue

            # 检查 enabled
            runtime_cfg = conf.get("plugin_runtime")
            if isinstance(runtime_cfg, dict):
                from plugin.utils import parse_bool_config
                if not parse_bool_config(runtime_cfg.get("enabled"), default=True):
                    logger.debug(
                        "[Extension] Extension '{}' is disabled, skipping",
                        pdata.get("id", "?"),
                    )
                    continue

            ext_id = pdata.get("id", "unknown")
            ext_entry = pdata.get("entry")
            if not ext_entry or ":" not in ext_entry:
                logger.warning(
                    "[Extension] Extension '{}' has invalid entry '{}', skipping",
                    ext_id, ext_entry,
                )
                continue

            # 导入 Extension Router 类
            module_path, class_name = ext_entry.split(":", 1)
            try:
                mod = importlib.import_module(module_path)
                router_cls = getattr(mod, class_name)
            except (ImportError, ModuleNotFoundError) as e:
                logger.warning(
                    "[Extension] Failed to import extension '{}' ({}): {}",
                    ext_id, ext_entry, e,
                )
                continue
            except AttributeError as e:
                logger.warning(
                    "[Extension] Class '{}' not found in module '{}' for extension '{}': {}",
                    class_name, module_path, ext_id, e,
                )
                continue

            # 验证是 PluginRouter 子类
            if not (isinstance(router_cls, type) and issubclass(router_cls, PluginRouter)):
                logger.warning(
                    "[Extension] Extension '{}' entry class '{}' is not a PluginRouter subclass, skipping",
                    ext_id, class_name,
                )
                continue

            # 实例化并注入
            prefix = host_conf.get("prefix", "")
            try:
                router_instance = router_cls(prefix=prefix, name=ext_id)
                instance.include_router(router_instance)
                injected_count += 1
                logger.info(
                    "[Extension] Injected extension '{}' into host '{}' with prefix '{}'",
                    ext_id, host_plugin_id, prefix,
                )
            except Exception as e:
                logger.warning(
                    "[Extension] Failed to inject extension '{}' into host '{}': {}",
                    ext_id, host_plugin_id, e,
                )
        except Exception as e:
            logger.debug("[Extension] Error processing {}: {}", toml_path, e)

    if injected_count > 0:
        logger.info(
            "[Extension] Total {} extension(s) injected into host '{}'",
            injected_count, host_plugin_id,
        )


# ============================================================================
# _plugin_process_runner 辅助函数
# ============================================================================

def _setup_plugin_logger(plugin_id: str, project_root: Path) -> Any:
    """
    配置插件进程的 loguru logger。
    
    Args:
        plugin_id: 插件 ID
        project_root: 项目根目录
    
    Returns:
        配置好的 logger 实例
    """
    from loguru import logger
    import logging
    from plugin.logging_config import get_plugin_format_console, get_plugin_format_file
    
    # 移除默认 handler，绑定插件 ID
    logger.remove()
    logger = logger.bind(plugin_id=plugin_id)
    
    # 添加控制台输出（使用统一格式）
    safe_pid = _sanitize_plugin_id(plugin_id)
    logger.add(
        sys.stdout,
        format=get_plugin_format_console(safe_pid),
        level="INFO",
        colorize=True,
        enqueue=False,
    )
    
    # 添加文件输出（使用统一格式）
    log_dir = project_root / "log" / "plugins" / safe_pid
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{safe_pid}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    logger.add(
        str(log_file),
        format=get_plugin_format_file(safe_pid),
        level="INFO",
        rotation="10 MB",
        retention=10,
        encoding="utf-8",
    )
    
    return logger


def _setup_logging_interception(logger: Any, project_root: Path) -> None:
    """
    设置标准库 logging 拦截，转发到 loguru。
    
    Args:
        logger: loguru logger 实例
        project_root: 项目根目录
    """
    import logging
    
    # 确保项目根目录在 path 中
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    logger.debug("[Plugin Process] Resolved project_root: {}", project_root)
    logger.debug("[Plugin Process] Python path (head): {}", sys.path[:3])
    
    # 尝试使用项目的 InterceptHandler
    handler_cls: Optional[Type[logging.Handler]] = None
    try:
        import utils.logger_config as _lc
        handler_cls = getattr(_lc, "InterceptHandler", None)
    except Exception:
        handler_cls = None
    
    if handler_cls is None:
        class _InterceptHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    level = record.levelname
                    msg = record.getMessage()
                    logger.opt(exception=record.exc_info).log(level, msg)
                except Exception:
                    pass
        handler_cls = _InterceptHandler
    
    logging.basicConfig(handlers=[handler_cls()], level=0, force=True)
    
    # 设置 uvicorn/fastapi logger
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging_logger = logging.getLogger(logger_name)
        logging_logger.handlers = [handler_cls()]
        logging_logger.propagate = False
    
    logger.debug("[Plugin Process] Standard logging intercepted and redirected to loguru")


def _find_project_root(config_path: Path) -> Path:
    """
    从配置文件路径向上探测项目根目录。
    
    Args:
        config_path: 插件配置文件路径
    
    Returns:
        项目根目录路径
    """
    cur = config_path.resolve()
    try:
        if cur.is_file():
            cur = cur.parent
    except Exception:
        pass
    
    for _ in range(10):
        try:
            candidate = cur
            # Repo root should contain both plugin/ and utils/
            if (candidate / "plugin").is_dir() and (candidate / "utils").is_dir():
                return candidate
        except Exception:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    
    # Fallback: assume layout plugin/plugins/<id>/plugin.toml
    try:
        logger.debug(
            "[Plugin Process] Could not find project root via exploration from %s; using fallback pattern",
            config_path,
        )
    except Exception:
        pass
    
    try:
        return config_path.parent.parent.parent.parent.resolve()
    except Exception:
        return config_path.parent.resolve()


def _check_extension_type_guard(config_path: Path, plugin_id: str, logger: Any) -> bool:
    """
    检查插件是否是 Extension 类型（不应作为独立进程运行）。
    
    Args:
        config_path: 配置文件路径
        plugin_id: 插件 ID
        logger: 日志记录器
    
    Returns:
        True 如果是 Extension 类型（应退出），False 否则
    """
    try:
        try:
            import tomllib as _tomllib
        except ModuleNotFoundError:
            import tomli as _tomllib  # type: ignore[no-redef]
        
        with config_path.open("rb") as _f:
            _conf = _tomllib.load(_f)
        
        if _conf.get("plugin", {}).get("type") == "extension":
            logger.error(
                "[Plugin Process] FATAL: Plugin '{}' is type='extension' and must NOT run as an independent process. "
                "It should be injected into its host plugin. Exiting immediately.",
                plugin_id,
            )
            return True
    except Exception as _e:
        logger.debug("[Plugin Process] Could not perform extension type guard: {}", _e)
    
    return False


def _handle_config_update_command(
    msg: dict,
    ctx: Any,
    events_by_type: dict,
    plugin_id: str,
    res_queue: Queue,
    logger: Any,
) -> None:
    """
    处理 CONFIG_UPDATE 命令 - 配置热更新。
    
    支持两种模式：
    - temporary: 临时更新，只修改进程内缓存，不写入文件
    - permanent: 永久更新，写入 profile 文件
    
    Args:
        msg: 命令消息，包含：
            - config: 新配置（完整或部分）
            - mode: "temporary" | "permanent"
            - profile: profile 名称（permanent 模式）
            - req_id: 请求ID
        ctx: 插件上下文
        events_by_type: 事件映射
        plugin_id: 插件ID
        res_queue: 响应队列
        logger: 日志记录器
    """
    req_id = msg.get("req_id", "unknown")
    new_config = msg.get("config", {})
    mode = msg.get("mode", "temporary")  # temporary | permanent
    profile_name = msg.get("profile")
    
    ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
    
    try:
        logger.info(
            "[Plugin Process] Received CONFIG_UPDATE: plugin_id={}, mode={}, req_id={}",
            plugin_id, mode, req_id,
        )
        
        # 保存旧配置用于回调（深拷贝，避免嵌套结构共享引用）
        old_config = {}
        if hasattr(ctx, '_effective_config'):
            old_config = copy.deepcopy(ctx._effective_config) if ctx._effective_config else {}
        
        # 更新进程内配置缓存
        if hasattr(ctx, '_effective_config') and ctx._effective_config is not None:
            # 合并配置（深度合并）
            _deep_merge(ctx._effective_config, new_config)
            logger.debug("[Plugin Process] Config cache updated")
        else:
            ctx._effective_config = new_config
        
        # 触发 config_change 生命周期事件（如果存在）
        lifecycle_events = events_by_type.get("lifecycle", {})
        config_change_handler = lifecycle_events.get("config_change")
        
        if config_change_handler:
            logger.debug("[Plugin Process] Triggering config_change lifecycle event")
            try:
                if asyncio.iscoroutinefunction(config_change_handler):
                    asyncio.run(config_change_handler(
                        old_config=old_config,
                        new_config=ctx._effective_config,
                        mode=mode,
                    ))
                else:
                    config_change_handler(
                        old_config=old_config,
                        new_config=ctx._effective_config,
                        mode=mode,
                    )
                logger.info("[Plugin Process] config_change handler executed successfully")
            except Exception as e:
                logger.exception("[Plugin Process] config_change handler failed")
                # 回滚配置到变更前状态
                ctx._effective_config = old_config
                logger.debug("[Plugin Process] Config rolled back after handler failure")
                ret_payload["error"] = f"config_change handler failed: {e}"
                res_queue.put(ret_payload, timeout=10.0)
                return
        
        if mode == "permanent":
            # permanent 模式需要持久化到文件/DB；当前插件进程侧未实现写盘，不能返回假成功。
            logger.warning(
                "[Plugin Process] CONFIG_UPDATE permanent mode requested but persistence is not implemented: plugin_id={}, profile={} req_id={}",
                plugin_id, profile_name, req_id,
            )
            ret_payload["success"] = False
            ret_payload["error"] = "permanent mode persistence is not implemented"
            ret_payload["data"] = {
                "mode": mode,
                "config_applied": True,
                "handler_called": config_change_handler is not None,
                "permanent_not_implemented": True,
            }
            try:
                res_queue.put(ret_payload, timeout=10.0)
            except Exception:
                logger.exception("[Plugin Process] Failed to send CONFIG_UPDATE response")
            return

        ret_payload["success"] = True
        ret_payload["data"] = {
            "mode": mode,
            "config_applied": True,
            "handler_called": config_change_handler is not None,
        }
        
        logger.info("[Plugin Process] CONFIG_UPDATE completed successfully, mode={}", mode)
        
    except Exception as e:
        logger.exception("[Plugin Process] CONFIG_UPDATE failed")
        ret_payload["error"] = str(e)
    
    try:
        res_queue.put(ret_payload, timeout=10.0)
    except Exception:
        logger.exception("[Plugin Process] Failed to send CONFIG_UPDATE response")


def _deep_merge(base: dict, updates: dict) -> None:
    """
    深度合并字典，将 updates 合并到 base 中。
    
    Args:
        base: 基础字典（会被修改）
        updates: 更新字典
    """
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _plugin_process_runner(
    plugin_id: str,
    entry_point: str,
    config_path: Path,
    cmd_queue: Queue,
    res_queue: Queue,
    status_queue: Queue,
    message_queue: Queue,
    response_queue: Queue,
    stop_event: Any | None = None,
    plugin_comm_queue: Queue | None = None,
    extension_configs: list | None = None,
) -> None:
    """
    独立进程中的运行函数，负责加载插件、映射入口、处理命令并返回结果。
    """
    # 保存进程级 stop event
    process_stop_event = stop_event
    
    # 初始化：探测项目根目录、配置 logger
    project_root = _find_project_root(config_path)
    logger = _setup_plugin_logger(plugin_id, project_root)
    
    # 设置 logging 拦截
    try:
        _setup_logging_interception(logger, project_root)
    except Exception as e:
        logger.warning("[Plugin Process] Failed to setup logging interception: {}", e)
    
    # 防御性检查：Extension 类型不应作为独立进程运行
    if _check_extension_type_guard(config_path, plugin_id, logger):
        return

    try:
        # 设置 Python 路径
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            logger.info("[Plugin Process] Added project root to sys.path: {}", project_root)
        
        logger.info("[Plugin Process] Starting plugin '{}' from {}", plugin_id, entry_point)
        
        module_path, class_name = entry_point.split(":", 1)
        logger.debug("[Plugin Process] Importing module: {}", module_path)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        logger.debug("[Plugin Process] Class loaded: {}", cls.__name__)

        # 注意：_entry_map 和 _instance 在 PluginContext 中定义为 Optional，
        # 这里先设置为 None，在创建 instance 和扫描入口映射后再设置。
        # 在设置之前，context 的方法不应该访问这些属性。
        ctx = PluginContext(
            plugin_id=plugin_id,
            logger=logger,
            config_path=config_path,
            status_queue=status_queue,
            message_queue=message_queue,
            _plugin_comm_queue=plugin_comm_queue,
            _zmq_ipc_client=None,
            _cmd_queue=cmd_queue,  # 传递命令队列，用于在等待期间处理命令
            _res_queue=res_queue,  # 传递结果队列，用于在等待期间处理响应
            _response_queue=response_queue,
            _response_pending={},
            _entry_map=None,  # 将在创建 instance 后设置（见下方第116行）
            _instance=None,  # 将在创建 instance 后设置（见下方第117行）
        )

        try:
            from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

            if PLUGIN_ZMQ_IPC_ENABLED:
                from plugin.utils.zeromq_ipc import ZmqIpcClient

                ctx._zmq_ipc_client = ZmqIpcClient(plugin_id=plugin_id, endpoint=PLUGIN_ZMQ_IPC_ENDPOINT)
                try:
                    logger.info("[Plugin Process] ZeroMQ IPC enabled: {}", PLUGIN_ZMQ_IPC_ENDPOINT)
                except Exception:
                    pass
        except Exception:
            try:
                logger.warning("[Plugin Process] ZeroMQ IPC enabled but client init failed")
            except Exception:
                pass
            pass

        # 防御：extension（PluginRouter 子类）不应被当作独立进程启动
        if isinstance(cls, type) and issubclass(cls, PluginRouter):
            logger.error(
                "[Plugin Process] Entry class '{}' is a PluginRouter subclass, not a NekoPluginBase. "
                "This plugin should be loaded as an extension (type='extension'), not as an independent process. "
                "Aborting process for plugin '{}'.",
                cls.__name__, plugin_id,
            )
            status_queue.put({
                "type": "plugin_status",
                "plugin_id": plugin_id,
                "status": "error",
                "error": f"Plugin '{plugin_id}' entry is a PluginRouter, not a NekoPluginBase. "
                         f"Set type='extension' in plugin.toml to inject it into a host plugin.",
            })
            return

        instance = cls(ctx)

        # 注入 Extension Router（type="extension" 且 host.plugin_id 匹配的插件）
        _inject_extensions(instance, plugin_id, config_path, logger, extension_configs=extension_configs)

        # 获取 freezable 属性列表和持久化模式
        freezable_keys = getattr(instance, "__freezable__", []) or []
        # 优先级：effective config [plugin_state].persist_mode > 类属性 __persist_mode__ > __freeze_mode__(兼容) > 默认 "off"
        persist_mode = getattr(instance, "__persist_mode__", None)
        if persist_mode is None:
            persist_mode = getattr(instance, "__freeze_mode__", "off")  # 向后兼容
        # 从 effective config 读取 persist_mode（包含 profile 覆写）
        try:
            effective_cfg = instance.config.dump_effective_sync(timeout=3.0)
            # 新配置项 [plugin_state]
            state_cfg = effective_cfg.get("plugin_state", {})
            if isinstance(state_cfg, dict):
                cfg_persist_mode = state_cfg.get("persist_mode")
                if cfg_persist_mode in ("auto", "manual", "off"):
                    persist_mode = cfg_persist_mode
                    logger.debug("[Plugin Process] persist_mode from effective config: {}", persist_mode)
            # 向后兼容：旧配置项 [plugin_checkpoint]
            if persist_mode == "off":
                checkpoint_cfg = effective_cfg.get("plugin_checkpoint", {})
                if isinstance(checkpoint_cfg, dict):
                    cfg_freeze_mode = checkpoint_cfg.get("freeze_mode")
                    if cfg_freeze_mode in ("auto", "manual", "off"):
                        persist_mode = cfg_freeze_mode
                        logger.debug("[Plugin Process] persist_mode from legacy plugin_checkpoint config: {}", persist_mode)
        except Exception as e:
            logger.debug("[Plugin Process] Could not read plugin_state from effective config: {}", e)
        # 标记是否从冻结状态恢复（用于触发 unfreeze 生命周期事件）
        ctx._restored_from_freeze = False
        
        if freezable_keys:
            logger.debug("[Plugin Process] Freezable attributes: {}, mode: {}", freezable_keys, persist_mode)
            # 如果有保存的状态，尝试恢复
            state_persistence = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            if state_persistence and state_persistence.has_saved_state():
                logger.debug("[Plugin Process] Restoring saved state...")
                state_persistence.load(instance)
                state_persistence.clear()  # 恢复后清除
                ctx._restored_from_freeze = True  # 标记为从冻结恢复
        
        def _should_persist(method) -> bool:
            """判断是否应该保存状态"""
            if not freezable_keys or persist_mode == "off":
                return False
            # 检查方法级别的 persist 配置
            method_persist = getattr(method, PERSIST_ATTR, None)
            if method_persist is not None:
                return method_persist  # 方法显式指定
            # 遵循类级别配置
            return persist_mode == "auto"

        entry_map: Dict[str, Any] = {}
        entry_meta_map: Dict[str, Any] = {}  # 存储 EventMeta 用于获取自定义配置（如 timeout）
        events_by_type: Dict[str, Dict[str, Any]] = {}

        def _rebuild_entry_map() -> None:
            """重建 entry_map + events_by_type（Extension 注入/卸载后调用）。"""
            collected = instance.collect_entries(wrap_with_hooks=True)
            entry_map.clear()
            entry_meta_map.clear()
            events_by_type.clear()
            for eid, eh in collected.items():
                entry_map[eid] = eh.handler
                entry_meta_map[eid] = eh.meta
                etype = getattr(eh.meta, "event_type", "plugin_entry")
                events_by_type.setdefault(etype, {})
                events_by_type[etype][eid] = eh.handler
            ctx._entry_map = entry_map

        # 优先使用 collect_entries() 获取入口点（支持 Hook 包装）
        if hasattr(instance, "collect_entries") and callable(instance.collect_entries):
            try:
                collected = instance.collect_entries(wrap_with_hooks=True)
                for eid, event_handler in collected.items():
                    entry_map[eid] = event_handler.handler
                    entry_meta_map[eid] = event_handler.meta
                    etype = getattr(event_handler.meta, "event_type", "plugin_entry")
                    events_by_type.setdefault(etype, {})
                    events_by_type[etype][eid] = event_handler.handler
                logger.info("Plugin entries collected via collect_entries(): {}", list(entry_map.keys()))
            except Exception as e:
                logger.warning("Failed to collect entries via collect_entries(): {}, falling back to scan", e)
                entry_map.clear()
                entry_meta_map.clear()
                events_by_type.clear()
        
        # 如果 collect_entries 失败或不存在，回退到扫描方法
        if not entry_map:
            for name, member in inspect.getmembers(instance, predicate=callable):
                if name.startswith("_") and not hasattr(member, EVENT_META_ATTR):
                    continue
                event_meta = getattr(member, EVENT_META_ATTR, None)
                if not event_meta:
                    wrapped = getattr(member, "__wrapped__", None)
                    if wrapped is not None:
                        event_meta = getattr(wrapped, EVENT_META_ATTR, None)

                if event_meta:
                    eid = getattr(event_meta, "id", name)
                    entry_map[eid] = member
                    entry_meta_map[eid] = event_meta  # 存储 EventMeta 用于获取自定义配置
                    etype = getattr(event_meta, "event_type", "plugin_entry")
                    events_by_type.setdefault(etype, {})
                    events_by_type[etype][eid] = member
                else:
                    entry_map[name] = member
            logger.info("Plugin instance created. Mapped entries: {}", list(entry_map.keys()))
        
        # 设置入口映射和实例到上下文，用于在等待期间处理命令
        # _cmd_queue 和 _res_queue 已在 PluginContext 构造函数中初始化
        ctx._entry_map = entry_map
        ctx._instance = instance

        # 生命周期：startup
        lifecycle_events = events_by_type.get("lifecycle", {})
        startup_fn = lifecycle_events.get("startup")
        if startup_fn:
            try:
                with ctx._handler_scope("lifecycle.startup"):
                    if asyncio.iscoroutinefunction(startup_fn):
                        asyncio.run(startup_fn())
                    else:
                        startup_fn()
            except (KeyboardInterrupt, SystemExit):
                # 系统级中断，直接抛出
                raise
            except Exception as e:
                error_msg = f"Error in lifecycle.startup: {str(e)}"
                logger.exception(error_msg)
                # 记录错误但不中断进程启动
                # 如果启动失败是致命的，可以在这里 raise PluginLifecycleError
        
        # 生命周期：unfreeze（如果是从冻结状态恢复）
        # 通过检查是否有状态被恢复来判断是否是从冻结恢复
        _restored_from_freeze = False
        if freezable_keys:
            state_persistence = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            # 检查 ctx 中是否有恢复标记（由状态恢复逻辑设置）
            _restored_from_freeze = getattr(ctx, "_restored_from_freeze", False)
        
        unfreeze_fn = lifecycle_events.get("unfreeze")
        if unfreeze_fn and _restored_from_freeze:
            try:
                logger.info("[Plugin Process] Executing unfreeze lifecycle (restored from frozen state)...")
                with ctx._handler_scope("lifecycle.unfreeze"):
                    if asyncio.iscoroutinefunction(unfreeze_fn):
                        asyncio.run(unfreeze_fn())
                    else:
                        unfreeze_fn()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                error_msg = f"Error in lifecycle.unfreeze: {str(e)}"
                logger.exception(error_msg)

        # 定时任务：timer auto_start interval
        def _run_timer_interval(fn, interval_seconds: int, fn_name: str, stop_event: threading.Event):
            while not stop_event.is_set():
                try:
                    with ctx._handler_scope(f"timer.{fn_name}"):
                        if asyncio.iscoroutinefunction(fn):
                            asyncio.run(fn())
                        else:
                            fn()
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，停止定时任务
                    logger.info("Timer '{}' interrupted, stopping", fn_name)
                    break
                except Exception:
                    logger.exception("Timer '{}' failed", fn_name)
                    # 定时任务失败不应中断循环，继续执行
                stop_event.wait(interval_seconds)

        timer_events = events_by_type.get("timer", {})
        timer_stop_events: list[threading.Event] = []
        for eid, fn in timer_events.items():
            meta = getattr(fn, EVENT_META_ATTR, None)
            if not meta or not getattr(meta, "auto_start", False):
                continue
            mode = getattr(meta, "extra", {}).get("mode")
            if mode == "interval":
                seconds = getattr(meta, "extra", {}).get("seconds", 0)
                if seconds > 0:
                    timer_stop_event = threading.Event()
                    timer_stop_events.append(timer_stop_event)
                    t = threading.Thread(
                        target=_run_timer_interval,
                        args=(fn, seconds, eid, timer_stop_event),
                        daemon=True,
                    )
                    t.start()
                    logger.info("Started timer '{}' every {}s", eid, seconds)

        # 处理自定义事件：自动启动
        def _run_custom_event_auto(fn, fn_name: str, event_type: str):
            """执行自动启动的自定义事件"""
            try:
                with ctx._handler_scope(f"{event_type}.{fn_name}"):
                    if asyncio.iscoroutinefunction(fn):
                        asyncio.run(fn())
                    else:
                        fn()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Custom event '{}' (type: {}) interrupted", fn_name, event_type)
            except Exception:
                logger.exception("Custom event '{}' (type: {}) failed", fn_name, event_type)

        # 扫描所有自定义事件类型
        for event_type, events in events_by_type.items():
            if event_type in ("plugin_entry", "lifecycle", "message", "timer"):
                continue  # 跳过标准类型
            
            # 这是自定义事件类型
            logger.info("Found custom event type: {} with {} handlers", event_type, len(events))
            for eid, fn in events.items():
                meta = getattr(fn, EVENT_META_ATTR, None)
                if not meta:
                    continue
                
                # 处理自动启动的自定义事件
                if getattr(meta, "auto_start", False):
                    trigger_method = getattr(meta, "extra", {}).get("trigger_method", "auto")
                    if trigger_method == "auto":
                        # 在独立线程中启动
                        t = threading.Thread(
                            target=_run_custom_event_auto,
                            args=(fn, eid, event_type),
                            daemon=True,
                        )
                        t.start()
                        logger.info("Started auto custom event '{}' (type: {})", eid, event_type)

        # 初始化 Worker 执行器
        worker_executor = WorkerExecutor(max_workers=4, queue_size=100)
        logger.debug("[Plugin Process] Worker executor initialized")

        # run_id → threading.Event 映射，用于外部取消传播
        # TRIGGER 开始时注册，完成/超时时清理
        _run_cancel_events: Dict[str, threading.Event] = {}

        # 命令循环
        while True:
            try:
                if process_stop_event is not None and process_stop_event.is_set():
                    break
            except Exception:
                # stop_event is best-effort; never break command loop due to errors here
                pass
            try:
                msg = cmd_queue.get(timeout=QUEUE_GET_TIMEOUT)
            except Empty:
                continue

            if not isinstance(msg, dict):
                logger.warning("[Plugin Process] Invalid command payload type: {}", type(msg))
                continue
            msg_type = msg.get("type")
            if not isinstance(msg_type, str) or not msg_type:
                logger.warning("[Plugin Process] Invalid command payload: {}", msg)
                continue

            if msg_type == "STOP":
                break

            if msg_type == "CANCEL_RUN":
                cancel_run_id = msg.get("run_id")
                if cancel_run_id:
                    ev = _run_cancel_events.get(cancel_run_id)
                    if ev is not None:
                        ev.set()
                        logger.info("[Plugin Process] Cancel signal sent for run_id={}", cancel_run_id)
                    else:
                        logger.debug("[Plugin Process] No active cancel_event for run_id={}", cancel_run_id)
                continue

            if msg_type == "FREEZE":
                # 冻结插件：保存状态到文件，然后停止进程
                req_id = msg.get("req_id", "unknown")
                logger.info("[Plugin Process] Received FREEZE command, req_id={}", req_id)
                
                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
                
                try:
                    # 触发 freeze lifecycle 事件（如果存在）
                    freeze_fn = lifecycle_events.get("freeze")
                    if freeze_fn:
                        logger.info("[Plugin Process] Executing freeze lifecycle...")
                        with ctx._handler_scope("lifecycle.freeze"):
                            if asyncio.iscoroutinefunction(freeze_fn):
                                asyncio.run(freeze_fn())
                            else:
                                freeze_fn()
                    
                    # 保存冻结状态
                    if freezable_keys:
                        sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
                        if sp:
                            sp.save(instance, freezable_keys, reason="freeze")
                            logger.info("[Plugin Process] Frozen state saved")
                    
                    ret_payload["success"] = True
                    ret_payload["data"] = {"frozen": True, "freezable_keys": freezable_keys}
                except Exception as e:
                    logger.exception("[Plugin Process] Freeze failed")
                    ret_payload["error"] = str(e)
                
                res_queue.put(ret_payload, timeout=10.0)
                
                # 冻结后停止进程
                if ret_payload["success"]:
                    logger.info("[Plugin Process] Freeze successful, stopping process...")
                    break
                continue

            if msg["type"] == "BUS_CHANGE":
                try:
                    dispatch_bus_change(
                        sub_id=str(msg.get("sub_id") or ""),
                        bus=str(msg.get("bus") or ""),
                        op=str(msg.get("op") or ""),
                        delta=msg.get("delta") if isinstance(msg.get("delta"), dict) else None,
                    )
                except Exception as e:
                    logger.debug("Failed to dispatch bus change: {}", e)  
                continue

            if msg["type"] == "CONFIG_UPDATE":
                # 配置热更新
                _handle_config_update_command(
                    msg=msg,
                    ctx=ctx,
                    events_by_type=events_by_type,
                    plugin_id=plugin_id,
                    res_queue=res_queue,
                    logger=logger,
                )
                continue

            if msg["type"] == "TRIGGER_CUSTOM":
                # 触发自定义事件（通过命令队列）
                event_type = msg.get("event_type")
                event_id = msg.get("event_id")
                args = msg.get("args", {})
                req_id = msg.get("req_id", "unknown")
                
                logger.info(
                    "[Plugin Process] Received TRIGGER_CUSTOM: plugin_id={}, event_type={}, event_id={}, req_id={}",
                    plugin_id,
                    event_type,
                    event_id,
                    req_id,
                )
                
                # 查找自定义事件处理器
                custom_events = events_by_type.get(event_type, {})
                method = custom_events.get(event_id)
                
                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
                
                try:
                    if not method:
                        ret_payload["error"] = f"Custom event '{event_type}.{event_id}' not found"
                    else:
                        # 执行自定义事件
                        logger.debug(
                            "[Plugin Process] Executing custom event {}.{}, req_id={}",
                            event_type,
                            event_id,
                            req_id,
                        )
                        if asyncio.iscoroutinefunction(method):
                            logger.debug("[Plugin Process] Custom event is async, running in thread to avoid blocking command loop")
                            # 在独立线程中运行异步方法，避免阻塞命令循环
                            # 这样命令循环可以继续处理其他命令（包括响应命令）
                            result_container = {"result": None, "exception": None, "done": False}
                            event = threading.Event()
                            cancel_event = threading.Event()
                            
                            def _run_async_thread(
                                method=method,
                                args=args,
                                result_container=result_container,
                                event=event,
                                cancel_event=cancel_event,
                                event_type=event_type,
                                event_id=event_id,
                            ):
                                try:
                                    if cancel_event.is_set():
                                        return

                                    async def _run_with_cancel():
                                        task = asyncio.create_task(method(**args))
                                        try:
                                            while True:
                                                if cancel_event.is_set():
                                                    task.cancel()
                                                    try:
                                                        await task
                                                    except Exception:
                                                        pass
                                                    raise asyncio.CancelledError()
                                                done, _pending = await asyncio.wait({task}, timeout=0.05)
                                                if done:
                                                    return await task
                                        finally:
                                            if not task.done():
                                                task.cancel()

                                    with ctx._handler_scope(f"{event_type}.{event_id}"):
                                        result_container["result"] = asyncio.run(_run_with_cancel())
                                except Exception as e:
                                    result_container["exception"] = e
                                finally:
                                    result_container["done"] = True
                                    event.set()

                            timeout_seconds = PLUGIN_TRIGGER_TIMEOUT

                            def _wait_async_custom_event_result(
                                req_id=req_id,
                                event_type=event_type,
                                event_id=event_id,
                                ret_payload=ret_payload,
                                timeout_seconds=timeout_seconds,
                                result_container=result_container,
                                cancel_event=cancel_event,
                            ):
                                thread = threading.Thread(target=_run_async_thread, daemon=True)
                                thread.start()
                                thread.join(timeout=timeout_seconds)
                                if thread.is_alive():
                                    try:
                                        cancel_event.set()
                                    except Exception:
                                        pass
                                    try:
                                        thread.join(timeout=0.2)
                                    except Exception:
                                        pass
                                    logger.error(
                                        "Custom event {}.{} execution timed out",
                                        event_type,
                                        event_id,
                                    )
                                    ret_payload["error"] = f"Custom event execution timed out after {timeout_seconds}s"
                                elif result_container["exception"]:
                                    ret_payload["error"] = str(result_container["exception"])
                                else:
                                    ret_payload["success"] = True
                                    ret_payload["data"] = result_container["result"]

                                logger.debug(
                                    "[Plugin Process] Sending response for req_id={}, success={}",
                                    req_id,
                                    ret_payload.get("success"),
                                )
                                try:
                                    res_queue.put(ret_payload, timeout=10.0)
                                    logger.debug(
                                        "[Plugin Process] Response sent successfully for req_id={}",
                                        req_id,
                                    )
                                except Exception:
                                    logger.exception(
                                        "[Plugin Process] Failed to send response for req_id={}",
                                        req_id,
                                    )

                            threading.Thread(
                                target=_wait_async_custom_event_result,
                                daemon=True,
                                name=f"AsyncCustomWaiter-{req_id[:8]}",
                            ).start()
                            continue
                        else:
                            logger.debug("[Plugin Process] Custom event is sync, calling directly")
                            with ctx._handler_scope(f"{event_type}.{event_id}"):
                                res = method(**args)
                        ret_payload["success"] = True
                        ret_payload["data"] = res
                        logger.debug(
                            "[Plugin Process] Custom event {}.{} completed, req_id={}",
                            event_type, event_id, req_id
                        )
                except Exception as e:
                    logger.exception("Error executing custom event {}.{}", event_type, event_id)
                    ret_payload["error"] = str(e)
                
                # 发送响应到结果队列
                logger.debug(
                    "[Plugin Process] Sending response for req_id={}, success={}",
                    req_id,
                    ret_payload.get("success"),
                )
                try:
                    # multiprocessing.Queue.put() 默认会阻塞直到有空间
                    # 使用 timeout 避免无限阻塞，但通常不会阻塞
                    res_queue.put(ret_payload, timeout=10.0)
                    logger.debug(
                        "[Plugin Process] Response sent successfully for req_id={}",
                        req_id,
                    )
                except Exception:
                    logger.exception(
                        "[Plugin Process] Failed to send response for req_id={}",
                        req_id,
                    )
                    # 即使发送失败，也要继续处理下一个命令（防御性编程）
                continue

            if msg["type"] == "DISABLE_EXTENSION":
                ext_name = msg.get("ext_name", "")
                req_id = msg.get("req_id", "unknown")
                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
                try:
                    ok = instance.exclude_router(ext_name)
                    if ok:
                        _rebuild_entry_map()
                        ret_payload["success"] = True
                        ret_payload["data"] = {"disabled": ext_name}
                        logger.info("[Extension] Disabled extension '{}' in host '{}'", ext_name, plugin_id)
                    else:
                        ret_payload["error"] = f"Extension '{ext_name}' not found in host"
                except Exception as e:
                    logger.exception("[Extension] Failed to disable extension '{}'", ext_name)
                    ret_payload["error"] = str(e)
                res_queue.put(ret_payload, timeout=10.0)
                continue

            if msg["type"] == "ENABLE_EXTENSION":
                ext_id = msg.get("ext_id", "")
                ext_entry = msg.get("ext_entry", "")
                prefix = msg.get("prefix", "")
                req_id = msg.get("req_id", "unknown")
                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
                try:
                    # 检查是否已注入
                    existing = instance.get_router(ext_id) if hasattr(instance, "get_router") else None
                    if existing:
                        ret_payload["error"] = f"Extension '{ext_id}' is already injected"
                    elif not ext_entry or ":" not in ext_entry:
                        ret_payload["error"] = f"Invalid ext_entry '{ext_entry}'"
                    else:
                        mod_path, cls_name = ext_entry.split(":", 1)
                        mod = importlib.import_module(mod_path)
                        router_cls = getattr(mod, cls_name)
                        if not (isinstance(router_cls, type) and issubclass(router_cls, PluginRouter)):
                            ret_payload["error"] = f"'{cls_name}' is not a PluginRouter subclass"
                        else:
                            router_inst = router_cls(prefix=prefix, name=ext_id)
                            instance.include_router(router_inst)
                            _rebuild_entry_map()
                            ret_payload["success"] = True
                            ret_payload["data"] = {"enabled": ext_id}
                            logger.info("[Extension] Enabled extension '{}' in host '{}'", ext_id, plugin_id)
                except Exception as e:
                    logger.exception("[Extension] Failed to enable extension '{}'", ext_id)
                    ret_payload["error"] = str(e)
                res_queue.put(ret_payload, timeout=10.0)
                continue

            if msg["type"] == "TRIGGER":
                entry_id = msg.get("entry_id")
                args = msg.get("args", {})
                req_id = msg.get("req_id", "unknown")
                if not entry_id or not isinstance(args, dict):
                    try:
                        res_queue.put(
                            {"req_id": req_id, "success": False, "data": None,
                             "error": "Invalid TRIGGER payload: 'entry_id' is required and 'args' must be a dict"},
                            timeout=10.0,
                        )
                    except Exception:
                        logger.exception("[Plugin Process] Failed to send invalid TRIGGER response")
                    continue
                
                # 关键日志：记录接收到的触发消息
                logger.info(
                    "[Plugin Process] Received TRIGGER: plugin_id={}, entry_id={}, req_id={}",
                    plugin_id,
                    entry_id,
                    req_id,
                )
                # 详细参数信息使用 DEBUG
                logger.debug(
                    "[Plugin Process] Args: type={}, keys={}, content={}",
                    type(args),
                    list(args.keys()) if isinstance(args, dict) else "N/A",
                    args,
                )
                
                method = entry_map.get(entry_id) or getattr(instance, entry_id, None) or getattr(
                    instance, f"entry_{entry_id}", None
                )

                ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}

                try:
                    if not method:
                        raise PluginEntryNotFoundError(plugin_id, entry_id)

                    run_id = None
                    try:
                        ctx_obj = args.get("_ctx") if isinstance(args, dict) else None
                        if isinstance(ctx_obj, dict):
                            run_id = ctx_obj.get("run_id")
                    except Exception:
                        run_id = None

                    # 为当前 TRIGGER 创建 cancel_event 并注册到 _run_cancel_events
                    # 这使 CANCEL_RUN 命令可以传播取消信号到正在执行的 entry
                    trigger_cancel_event = threading.Event()
                    if run_id:
                        _run_cancel_events[run_id] = trigger_cancel_event
                    
                    method_name = getattr(method, "__name__", entry_id)
                    # 关键日志：记录开始执行
                    logger.info(
                        "[Plugin Process] Executing entry '{}' using method '{}'",
                        entry_id,
                        method_name,
                    )
                    
                    # 详细方法签名和参数匹配信息使用 DEBUG
                    try:
                        sig = inspect.signature(method)
                        params = list(sig.parameters.keys())
                        logger.debug(
                            "[Plugin Process] Method signature: params={}, args_keys={}",
                            params,
                            list(args.keys()) if isinstance(args, dict) else "N/A",
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug("[Plugin Process] Failed to inspect signature: {}", e)
                    
                    # 检查是否有 worker 标记
                    worker_config = getattr(method, WORKER_MODE_ATTR, None)
                    
                    if worker_config is not None:
                        # Worker 模式：提交到线程池
                        logger.debug("[Plugin Process] Method has worker mode, submitting to worker pool")
                        timeout = worker_config.timeout
                        
                        try:
                            # Set contextvars BEFORE submit() so that
                            # WorkerExecutor.copy_context() captures run_id
                            # and handler scope into the worker thread.
                            with ctx._handler_scope(f"plugin_entry.{entry_id}"), ctx._run_scope(run_id):
                                # 提交任务到 worker 线程池
                                future = worker_executor.submit(
                                    task_id=req_id,
                                    handler=method,
                                    args=(),
                                    kwargs=args,
                                    timeout=timeout
                                )
                            
                            # 等待结果（会阻塞当前线程，但这是在命令循环线程里）
                            # 为了不阻塞命令循环，我们在单独线程里等待
                            # 注意：必须绑定闭包变量，避免被后续命令覆盖
                            def _wait_worker_result(
                                entry_id=entry_id,
                                run_id=run_id,
                                future=future,
                                timeout=timeout,
                                ret_payload=ret_payload,
                                method=method,
                                cancel_event=trigger_cancel_event,
                                _rce=_run_cancel_events,
                            ):
                                try:
                                    # Sliced wait: check cancel_event every 0.2s
                                    start_ts = time.monotonic()
                                    while True:
                                        if cancel_event.is_set():
                                            try:
                                                future.cancel()
                                            except Exception:
                                                pass
                                            ret_payload["error"] = "Execution cancelled"
                                            break

                                        slice_timeout = 0.2
                                        if timeout is not None:
                                            elapsed = time.monotonic() - start_ts
                                            remain = timeout - elapsed
                                            if remain <= 0:
                                                cancel_event.set()
                                                try:
                                                    future.cancel()
                                                except Exception:
                                                    pass
                                                raise TimeoutError(f"Worker task {entry_id} timed out")
                                            slice_timeout = min(slice_timeout, remain)

                                        try:
                                            result = worker_executor.wait_for_result(future, slice_timeout)
                                            if asyncio.iscoroutine(result):
                                                result = asyncio.run(result)
                                            ret_payload["success"] = True
                                            ret_payload["data"] = result
                                            break
                                        except TimeoutError:
                                            continue

                                    # Save state after successful execution (if enabled)
                                    if ret_payload.get("success") and _should_persist(method):
                                        try:
                                            sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
                                            if sp:
                                                sp.save(instance, freezable_keys, reason="auto")
                                        except Exception as persist_err:
                                            logger.debug("Failed to persist state after worker task: {}", persist_err)
                                except TimeoutError as e:
                                    cancel_event.set()
                                    try:
                                        future.cancel()
                                    except Exception:
                                        pass
                                    logger.error("Worker task {} timed out", entry_id)
                                    ret_payload["error"] = str(e)
                                except Exception as e:
                                    logger.exception("Worker task {} failed", entry_id)
                                    ret_payload["error"] = f"Worker error: {str(e)}"
                                finally:
                                    if run_id:
                                        _rce.pop(run_id, None)
                                    # 发送响应
                                    res_queue.put(ret_payload, timeout=10.0)
                            
                            # 在单独线程里等待 worker 结果
                            threading.Thread(
                                target=_wait_worker_result,
                                daemon=True,
                                name=f"WorkerWaiter-{req_id[:8]}"
                            ).start()
                            
                            # 立即继续处理下一个命令
                            continue
                            
                        except Exception as e:
                            # 提交失败，立即返回错误
                            logger.exception("Failed to submit worker task {}", entry_id)
                            if run_id:
                                _run_cancel_events.pop(run_id, None)
                            ret_payload["error"] = f"Failed to submit worker task: {str(e)}"
                            res_queue.put(ret_payload, timeout=10.0)
                            continue
                    
                    elif asyncio.iscoroutinefunction(method) or inspect.iscoroutinefunction(method):
                        logger.debug("[Plugin Process] Method is async (iscoroutinefunction={}), running in thread", asyncio.iscoroutinefunction(method))
                        # 关键修复：在独立线程中运行异步方法，避免阻塞命令循环
                        # 这样命令循环可以继续处理其他命令（包括响应命令）
                        result_container = {"result": None, "exception": None, "done": False}
                        event = threading.Event()
                        cancel_event = trigger_cancel_event
                        
                        def run_async(
                            method=method,
                            args=args,
                            result_container=result_container,
                            event=event,
                            cancel_event=cancel_event,
                            entry_id=entry_id,
                            run_id=run_id,
                        ):
                            try:
                                if cancel_event.is_set():
                                    return

                                async def _run_with_cancel():
                                    task = asyncio.create_task(method(**args))
                                    try:
                                        while True:
                                            if cancel_event.is_set():
                                                task.cancel()
                                                try:
                                                    await task
                                                except Exception:
                                                    pass
                                                raise asyncio.CancelledError()
                                            done, _pending = await asyncio.wait({task}, timeout=0.05)
                                            if done:
                                                return await task
                                    finally:
                                        if not task.done():
                                            task.cancel()

                                with ctx._handler_scope(f"plugin_entry.{entry_id}"), ctx._run_scope(run_id):
                                    result_container["result"] = asyncio.run(_run_with_cancel())

                                if cancel_event.is_set():
                                    return

                                # Save state after successful execution (if enabled)
                                if _should_persist(method):
                                    try:
                                        sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
                                        if sp:
                                            sp.save(instance, freezable_keys, reason="auto")
                                    except Exception:
                                        pass
                            except Exception as e:
                                result_container["exception"] = e
                            finally:
                                result_container["done"] = True
                                event.set()

                        # 等待异步方法完成（允许超时）
                        # 从 EventMeta.extra 获取自定义超时，如果没有则使用默认值
                        entry_meta = entry_meta_map.get(entry_id)
                        custom_timeout = None
                        if entry_meta:
                            extra = getattr(entry_meta, "extra", None) or {}
                            custom_timeout = extra.get("timeout")
                        
                        # 确定实际超时时间
                        if custom_timeout is not None:
                            if custom_timeout <= 0:
                                # 0 或负数表示禁用超时（无限等待）
                                timeout_seconds = None
                                logger.debug("[Plugin Process] Timeout disabled for entry {}", entry_id)
                            else:
                                timeout_seconds = custom_timeout
                                logger.debug("[Plugin Process] Using custom timeout {}s for entry {}", timeout_seconds, entry_id)
                        else:
                            timeout_seconds = PLUGIN_TRIGGER_TIMEOUT

                        def _wait_async_trigger_result(
                            req_id=req_id,
                            entry_id=entry_id,
                            run_id=run_id,
                            result_container=result_container,
                            timeout_seconds=timeout_seconds,
                            ret_payload=ret_payload,
                            method=method,
                            cancel_event=cancel_event,
                            _rce=_run_cancel_events,
                        ):
                            try:
                                thread = threading.Thread(target=run_async, daemon=True)
                                thread.start()
                                thread.join(timeout=timeout_seconds)
                                if thread.is_alive():
                                    try:
                                        cancel_event.set()
                                    except Exception:
                                        pass
                                    try:
                                        thread.join(timeout=0.2)
                                    except Exception:
                                        pass
                                    logger.error(
                                        "Async method {} execution timed out after {}s",
                                        entry_id,
                                        timeout_seconds,
                                    )
                                    ret_payload["error"] = f"Async method execution timed out after {timeout_seconds}s"
                                elif result_container["exception"]:
                                    ret_payload["error"] = str(result_container["exception"])
                                else:
                                    ret_payload["success"] = True
                                    ret_payload["data"] = result_container["result"]
                            finally:
                                if run_id:
                                    _rce.pop(run_id, None)

                            try:
                                res_queue.put(ret_payload, timeout=10.0)
                            except Exception:
                                logger.exception(
                                    "[Plugin Process] Failed to send response for req_id={}",
                                    req_id,
                                )

                        threading.Thread(
                            target=_wait_async_trigger_result,
                            daemon=True,
                            name=f"AsyncWaiter-{req_id[:8]}",
                        ).start()
                        continue
                    else:
                        logger.debug("[Plugin Process] Method is sync, calling directly")
                        try:
                            logger.debug(
                                "[Plugin Process] Calling method with args: {}",
                                args,
                            )
                            with ctx._handler_scope(f"plugin_entry.{entry_id}"), ctx._run_scope(run_id):
                                res = method(**args)
                            
                            # 防御性检查：如果返回值是协程，执行它
                            if asyncio.iscoroutine(res):
                                res = asyncio.run(res)
                        except TypeError:
                            # 参数不匹配，记录详细信息并抛出
                            sig = inspect.signature(method)
                            params = list(sig.parameters.keys())
                            logger.exception(
                                "[Plugin Process] Invalid call to entry {}, params={}, args_keys={}",
                                entry_id,
                                params,
                                list(args.keys()) if isinstance(args, dict) else "N/A",
                            )
                            raise
                    
                    ret_payload["success"] = True
                    ret_payload["data"] = res
                    
                    # Save state after successful sync execution (if enabled)
                    if _should_persist(method):
                        try:
                            sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
                            if sp:
                                sp.save(instance, freezable_keys, reason="auto")
                        except Exception as persist_err:
                            logger.debug("Failed to persist state after sync execution: {}", persist_err)
                    
                except PluginError as e:
                    # 插件系统已知异常，直接使用
                    logger.warning("Plugin error executing {}: {}", entry_id, e)
                    ret_payload["error"] = str(e)
                except (TypeError, ValueError, AttributeError) as e:
                    # 参数或方法调用错误
                    logger.exception("Invalid call to entry {}", entry_id)
                    ret_payload["error"] = f"Invalid call: {str(e)}"
                except (KeyboardInterrupt, SystemExit):
                    # 系统级中断，需要特殊处理
                    logger.warning("Entry {} interrupted", entry_id)
                    ret_payload["error"] = "Execution interrupted"
                    raise  # 重新抛出系统级异常
                except Exception as e:
                    # 其他未知异常
                    logger.exception("Unexpected error executing {}", entry_id)
                    ret_payload["error"] = f"Unexpected error: {str(e)}"

                if run_id:
                    _run_cancel_events.pop(run_id, None)
                res_queue.put(ret_payload, timeout=10.0)

        # 触发生命周期：shutdown（尽力而为），并停止所有定时任务
        try:
            for ev in timer_stop_events:
                try:
                    ev.set()
                except Exception:
                    pass
        except Exception:
            pass

        shutdown_fn = lifecycle_events.get("shutdown")
        if shutdown_fn:
            try:
                with ctx._handler_scope("lifecycle.shutdown"):
                    if asyncio.iscoroutinefunction(shutdown_fn):
                        asyncio.run(shutdown_fn())
                    else:
                        shutdown_fn()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.exception("Error in lifecycle.shutdown: {}", e)

        try:
            ctx.close()
        except Exception as e:
            logger.debug("[Plugin Process] Context close failed during shutdown: {}", e)

        for q in (cmd_queue, res_queue, status_queue, message_queue, response_queue):
            try:
                q.cancel_join_thread()
            except Exception:
                pass
            try:
                q.close()
            except Exception:
                pass

    except (KeyboardInterrupt, SystemExit):
        # 系统级中断，正常退出
        logger.info("Plugin process {} interrupted", plugin_id)
        raise
    except Exception as e:
        # 进程崩溃，记录详细信息
        logger.exception("Plugin process {} crashed", plugin_id)
        # 尝试发送错误信息到结果队列（如果可能）
        try:
            res_queue.put({
                "req_id": "CRASH",
                "success": False,
                "data": None,
                "error": f"Process crashed: {str(e)}"
            })
        except Exception:
            pass  # 如果队列也坏了，只能放弃
        raise  # 重新抛出，让进程退出


class PluginHost:
    """
    插件进程宿主
    
    负责管理插件进程的完整生命周期：
    - 进程的启动、停止、监控（直接实现）
    - 进程间通信（通过 PluginCommunicationResourceManager）
    """

    def __init__(self, plugin_id: str, entry_point: str, config_path: Path, extension_configs: list | None = None):
        self.plugin_id = plugin_id
        self.entry_point = entry_point
        self.config_path = config_path
        # 使用loguru logger，绑定插件ID
        self.logger = logger.bind(plugin_id=plugin_id, host=True)
        
        # 创建队列（由通信资源管理器管理）
        cmd_queue: Queue = multiprocessing.Queue()
        res_queue: Queue = multiprocessing.Queue()
        status_queue: Queue = multiprocessing.Queue()
        message_queue: Queue = multiprocessing.Queue()
        response_queue: Queue = multiprocessing.Queue()
        
        # 创建进程（延迟到 start() 中启动）
        # 获取插件间通信队列（从 state 获取）
        plugin_comm_queue = state.plugin_comm_queue

        try:
            state.set_plugin_response_queue(plugin_id, response_queue)
        except Exception:
            pass

        self._process_stop_event: Any = multiprocessing.Event()

        # Important: initialize shared response notification primitives in the parent
        # BEFORE forking the plugin process, otherwise each child may create its own
        # Event/Manager proxies and wait_for_plugin_response will never be woken.
        try:
            _ = state.plugin_response_map
        except Exception as e:
            logger.warning(
                "Failed to pre-initialize plugin_response_map for plugin {}: {}",
                plugin_id, e
            )
        try:
            _ = state.plugin_response_notify_event
        except Exception as e:
            logger.warning(
                "Failed to pre-initialize plugin_response_notify_event for plugin {}: {}",
                plugin_id, e
            )
        
        self.process = multiprocessing.Process(
            target=_plugin_process_runner,
            args=(
                plugin_id,
                entry_point,
                config_path,
                cmd_queue,
                res_queue,
                status_queue,
                message_queue,
                response_queue,
                self._process_stop_event,
                plugin_comm_queue,
                extension_configs,
            ),
            daemon=True,
        )
        
        # 创建通信资源管理器
        self.comm_manager = PluginCommunicationResourceManager(
            plugin_id=plugin_id,
            cmd_queue=cmd_queue,
            res_queue=res_queue,
            status_queue=status_queue,
            message_queue=message_queue,
        )
        
        # 保留队列引用（用于 shutdown_sync 等同步方法）
        self.cmd_queue = cmd_queue
        self.res_queue = res_queue
        self.status_queue = status_queue
        self.message_queue = message_queue
        self.response_queue = response_queue
    
    async def start(self, message_target_queue=None) -> None:
        """
        启动后台任务（需要在异步上下文中调用）
        
        Args:
            message_target_queue: 主进程的消息队列，用于接收插件推送的消息
        """
        await self.comm_manager.start(message_target_queue=message_target_queue)

        if self.process.is_alive():
            self.logger.debug(
                "Plugin {} process already running (pid: {})",
                self.plugin_id,
                self.process.pid,
            )
            return

        try:
            await asyncio.to_thread(self.process.start)
        except Exception:
            self.logger.error(
                "Plugin {} process failed to start, shutting down comm_manager",
                self.plugin_id,
            )
            await self.comm_manager.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            raise
        self.logger.info("Plugin {} process started (pid: {})", self.plugin_id, self.process.pid)

        # 验证进程状态
        if not self.process.is_alive():
            exitcode = self.process.exitcode
            self.logger.error(
                "Plugin {} process is not alive after startup (exitcode: {})",
                self.plugin_id,
                exitcode,
            )
            await self.comm_manager.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            raise PluginLifecycleError(
                f"Plugin {self.plugin_id} failed to stay alive after startup (exitcode={exitcode})"
            )
        else:
            self.logger.info(
                "Plugin {} process is alive and running (pid: {})",
                self.plugin_id,
                self.process.pid,
            )
    
    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        优雅关闭插件
        
        按顺序关闭：
        1. 发送停止命令
        2. 关闭通信资源
        3. 关闭进程
        """
        self.logger.info(f"Shutting down plugin {self.plugin_id}")

        # Set out-of-band stop event first so the child can exit promptly even if cmd_queue is backlogged.
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        
        # 1. 发送停止命令
        await self.comm_manager.send_stop_command()
        
        # 2. 关闭通信资源（包括后台任务）
        await self.comm_manager.shutdown(timeout=timeout)
        
        # 3. 取消队列等待（防止 atexit 阻塞）
        # 必须在进程关闭前调用，告诉 multiprocessing 不要等待这些队列的后台线程
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue, self.response_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)

        try:
            state.remove_plugin_response_queue(self.plugin_id)
        except Exception:
            pass

        # 4. 关闭进程
        success = await asyncio.to_thread(self._shutdown_process, timeout)
        
        if success:
            self.logger.info(f"Plugin {self.plugin_id} shutdown successfully")
        else:
            self.logger.warning(f"Plugin {self.plugin_id} shutdown with issues")
    
    def shutdown_sync(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        同步版本的关闭方法（用于非异步上下文）
        
        注意：这个方法不会等待异步任务完成，建议使用 shutdown()
        """
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        # 发送停止命令（同步）
        try:
            self.cmd_queue.put({"type": "STOP"}, timeout=QUEUE_GET_TIMEOUT)
        except Exception as e:
            self.logger.warning(f"Failed to send STOP command: {e}")
        
        # 尽量通知通信管理器停止（即使不等待）
        if getattr(self, "comm_manager", None) is not None:
            try:
                # 标记 shutdown event，后台协程会自行退出
                _ev = getattr(self.comm_manager, "_shutdown_event", None)
                if _ev is not None:
                    _ev.set()
            except Exception:
                # 保持同步关闭的"尽力而为"语义，不要让这里抛异常
                pass
        
        # 关闭进程
        # 取消队列等待
        for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue, self.response_queue]:
            try:
                q.cancel_join_thread()
            except Exception as e:
                self.logger.debug("Failed to cancel queue join thread: {}", e)

        try:
            state.remove_plugin_response_queue(self.plugin_id)
        except Exception:
            pass
                
        self._shutdown_process(timeout=timeout)
    
    async def trigger(self, entry_id: str, args: dict, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        """
        触发插件入口点执行
        
        Args:
            entry_id: 入口点 ID
            args: 参数字典
            timeout: 超时时间
        
        Returns:
            插件返回的结果
        """
        self.logger.debug(
            "[PluginHost] Trigger called: plugin_id={}, entry_id={}",
            self.plugin_id,
            entry_id,
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[PluginHost] Args: type={}, keys={}, content={}",
            type(args),
            list(args.keys()) if isinstance(args, dict) else "N/A",
            args,
        )
        # 发送 TRIGGER 命令到子进程并等待结果
        # 委托给通信资源管理器处理
        return await self.comm_manager.trigger(entry_id, args, timeout)

    async def cancel_run(self, run_id: str) -> None:
        """Propagate a run cancellation to the child process.

        Fire-and-forget: the child process will set the cancel_event for
        the given *run_id*, causing the running entry to be cancelled if it
        supports cancellation (async / worker entries).
        """
        await self.comm_manager.send_cancel_run(run_id)
    
    async def trigger_custom_event(
        self, 
        event_type: str, 
        event_id: str, 
        args: dict, 
        timeout: float = PLUGIN_TRIGGER_TIMEOUT
    ) -> Any:
        """
        触发自定义事件执行
        
        Args:
            event_type: 自定义事件类型（例如 "file_change", "user_action"）
            event_id: 事件ID
            args: 参数字典
            timeout: 超时时间
        
        Returns:
            事件处理器返回的结果
        
        Raises:
            PluginError: 如果事件不存在或执行失败
        """
        self.logger.info(
            "[PluginHost] Trigger custom event: plugin_id={}, event_type={}, event_id={}",
            self.plugin_id,
            event_type,
            event_id,
        )
        return await self.comm_manager.trigger_custom_event(event_type, event_id, args, timeout)

    async def push_bus_change(self, *, sub_id: str, bus: str, op: str, delta: Dict[str, Any] | None = None) -> None:
        await self.comm_manager.push_bus_change(sub_id=sub_id, bus=bus, op=op, delta=delta)

    async def send_extension_command(self, msg_type: str, payload: Dict[str, Any], timeout: float = 10.0) -> Any:
        """向子进程发送 Extension 管理命令（DISABLE_EXTENSION / ENABLE_EXTENSION）。"""
        req_id = str(uuid.uuid4())
        cmd = {"type": msg_type, "req_id": req_id, **payload}
        return await self.comm_manager._send_command_and_wait(req_id, cmd, timeout, f"extension cmd {msg_type}")

    async def send_config_update(
        self,
        config: Dict[str, Any],
        mode: str = "temporary",
        profile: str | None = None,
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """
        向子进程发送 CONFIG_UPDATE 命令（配置热更新）。
        
        Args:
            config: 新配置（完整或部分）
            mode: "temporary" | "permanent"
            profile: profile 名称（permanent 模式）
            timeout: 超时时间
        
        Returns:
            {
                "success": bool,
                "config_applied": bool,
                "handler_called": bool,
            }
        """
        req_id = str(uuid.uuid4())
        cmd = {
            "type": "CONFIG_UPDATE",
            "req_id": req_id,
            "config": config,
            "mode": mode,
            "profile": profile,
        }
        return await self.comm_manager._send_command_and_wait(req_id, cmd, timeout, "CONFIG_UPDATE")

    def is_alive(self) -> bool:
        """检查进程是否存活"""
        return self.process.is_alive() and self.process.exitcode is None
    
    def health_check(self) -> HealthCheckResponse:
        """执行健康检查，返回详细状态"""
        alive = self.is_alive()
        exitcode = self.process.exitcode
        pid = self.process.pid if self.process.is_alive() else None
        
        if alive:
            status = "running"
        elif exitcode is None:
            status = "not_started"
        elif exitcode == 0:
            status = "stopped"
        else:
            status = "crashed"
        
        return HealthCheckResponse(
            alive=alive,
            exitcode=exitcode,
            pid=pid,
            status=status,
            communication={
                "pending_requests": len(self.comm_manager._pending_futures),
                "consumer_running": (
                    self.comm_manager._result_consumer_task is not None
                    and not self.comm_manager._result_consumer_task.done()
                ),
            },
        )
    
    async def freeze(self, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Dict[str, Any]:
        """
        冻结插件：保存状态到文件，然后停止进程
        
        Args:
            timeout: 超时时间
        
        Returns:
            冻结结果，包含 frozen 状态和 freezable_keys
        """
        self.logger.info(f"[PluginHost] Freezing plugin {self.plugin_id}")
        
        # 发送 FREEZE 命令并等待结果
        result = await self.comm_manager.send_freeze_command(timeout=timeout)
        
        if result.get("success"):
            # 等待进程结束
            await asyncio.to_thread(self._shutdown_process, timeout)
            # 回收通信资源
            await self.comm_manager.shutdown(timeout=timeout)
            for q in [self.cmd_queue, self.res_queue, self.status_queue, self.message_queue, self.response_queue]:
                try:
                    q.cancel_join_thread()
                except Exception as e:
                    self.logger.debug("Failed to cancel queue join thread during freeze: {}", e)
            try:
                state.remove_plugin_response_queue(self.plugin_id)
            except Exception:
                pass
            self.logger.info(f"[PluginHost] Plugin {self.plugin_id} frozen successfully")
        else:
            self.logger.error(f"[PluginHost] Plugin {self.plugin_id} freeze failed: {result.get('error')}")
        
        return result
    
    def _shutdown_process(self, timeout: float = PROCESS_SHUTDOWN_TIMEOUT) -> bool:
        """
        优雅关闭进程
        
        Args:
            timeout: 等待进程退出的超时时间（秒）
        
        Returns:
            True 如果成功关闭，False 如果超时或出错
        """
        if not self.process.is_alive():
            self.logger.info(f"Plugin {self.plugin_id} process already stopped")
            return True
        
        try:
            # 先尝试优雅关闭（进程会从队列读取 STOP 命令后退出）
            self.process.join(timeout=timeout)
            
            if self.process.is_alive():
                self.logger.warning(
                    f"Plugin {self.plugin_id} didn't stop gracefully within {timeout}s, terminating"
                )
                self.process.terminate()
                self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                
                if self.process.is_alive():
                    self.logger.error(f"Plugin {self.plugin_id} failed to terminate, killing")
                    self.process.kill()
                    self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                    return False
            
            self.logger.info(f"Plugin {self.plugin_id} process shutdown successfully")
            return True
            
        except Exception:
            self.logger.exception("Error while shutting down plugin {}", self.plugin_id)
            return False


# Backwards-compatible alias
PluginProcessHost = PluginHost
