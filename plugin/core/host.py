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

from loguru import logger

from plugin._types.events import EVENT_META_ATTR
from plugin.sdk import PERSIST_ATTR
from plugin.core.state import state
from plugin.core.context import PluginContext
from plugin.core.communication import PluginCommunicationResourceManager
from plugin._types.models import HealthCheckResponse
from plugin._types.exceptions import (
    PluginLifecycleError,
    PluginEntryNotFoundError,
    PluginError,
)
from plugin.settings import (
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
    PROCESS_TERMINATE_TIMEOUT,
)
from plugin.sdk.shared.core.router import PluginRouter
from plugin.core.bus.types import dispatch_bus_change
from plugin.core.zmq_transport import (
    HostTransport, ChildTransport, CH_CMD, CH_RES, CH_STS, CH_MSG, CH_COMM, CH_RESP,
)


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

    # 优先使用 settings 中的插件根目录集合，回退到路径推导
    try:
        from plugin.settings import PLUGIN_CONFIG_ROOTS
        plugin_config_roots = tuple(PLUGIN_CONFIG_ROOTS)
    except Exception:
        plugin_config_roots = (host_config_path.parent.parent,)

    injected_count = 0
    for plugin_config_root in plugin_config_roots:
        try:
            root = plugin_config_root.resolve()
        except Exception:
            root = plugin_config_root

        try:
            if not root.exists():
                continue
        except Exception:
            continue

        for toml_path in root.glob("*/plugin.toml"):
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


async def _handle_config_update_command(
    msg: dict,
    ctx: Any,
    events_by_type: dict,
    plugin_id: str,
    res_sender: Any,
    logger: Any,
) -> None:
    """处理 CONFIG_UPDATE 命令 - 配置热更新。

    ``res_sender`` must expose ``.put(obj, timeout=...)`` — either a
    :class:`ChannelSender` or legacy ``mp.Queue``.
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
                result = config_change_handler(
                    old_config=old_config,
                    new_config=ctx._effective_config,
                    mode=mode,
                )
                if inspect.isawaitable(result):
                    await result
                logger.info("[Plugin Process] config_change handler executed successfully")
            except Exception as e:
                logger.exception("[Plugin Process] config_change handler failed")
                # 回滚配置到变更前状态
                ctx._effective_config = old_config
                logger.debug("[Plugin Process] Config rolled back after handler failure")
                ret_payload["error"] = f"config_change handler failed: {e}"
                res_sender.put(ret_payload, timeout=10.0)
                return
        
        if mode == "permanent":
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
                res_sender.put(ret_payload, timeout=10.0)
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
        res_sender.put(ret_payload, timeout=10.0)
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
    downlink_endpoint: str,
    uplink_endpoint: str,
    stop_event: Any | None = None,
    extension_configs: list | None = None,
) -> None:
    """独立进程中的运行函数。通过 ZMQ 与宿主进程通信。"""
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
    
    if _check_extension_type_guard(config_path, plugin_id, logger):
        return

    # ── ZMQ child-side transport ─────────────────────────────────
    child_transport = ChildTransport(downlink_endpoint, uplink_endpoint)
    res_sender = child_transport.channel_sender(CH_RES)
    status_sender = child_transport.channel_sender(CH_STS)
    message_sender = child_transport.channel_sender(CH_MSG)
    comm_sender = child_transport.channel_sender(CH_COMM)

    try:
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            logger.info("[Plugin Process] Added project root to sys.path: {}", project_root)
        
        logger.info("[Plugin Process] Starting plugin '{}' from {}", plugin_id, entry_point)
        
        module_path, class_name = entry_point.split(":", 1)
        logger.debug("[Plugin Process] Importing module: {}", module_path)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        logger.debug("[Plugin Process] Class loaded: {}", cls.__name__)

        ctx = PluginContext(
            plugin_id=plugin_id,
            logger=logger,
            config_path=config_path,
            status_queue=status_sender,
            message_queue=message_sender,
            _plugin_comm_queue=comm_sender,
            _zmq_ipc_client=None,
            _cmd_queue=None,
            _res_queue=None,
            _response_queue=None,
            _response_pending={},
            _entry_map=None,
            _instance=None,
        )

        try:
            from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

            enabled = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENABLED")
            if enabled is None:
                use_zmq = bool(PLUGIN_ZMQ_IPC_ENABLED)
            else:
                use_zmq = str(enabled).lower() in ("true", "1", "yes", "on")

            if use_zmq:
                from plugin.utils.zeromq_ipc import ZmqIpcClient
                endpoint = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", PLUGIN_ZMQ_IPC_ENDPOINT)
                ctx._zmq_ipc_client = ZmqIpcClient(plugin_id=plugin_id, endpoint=endpoint)
                try:
                    logger.info("[Plugin Process] ZeroMQ IPC enabled: {}", endpoint)
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
            status_sender.put_nowait({
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
            if state_persistence:
                try:
                    def _unwrap_state_result(op_name: str, result_obj: object) -> object:
                        is_ok = getattr(result_obj, "is_ok", None)
                        if callable(is_ok):
                            if is_ok():
                                return getattr(result_obj, "value", None)
                            error_obj = getattr(result_obj, "error", None)
                            logger.warning(
                                "[Plugin Process] State persistence {} failed: {}",
                                op_name,
                                error_obj,
                            )
                            raise RuntimeError(f"state persistence {op_name} failed: {error_obj}")
                        return result_obj

                    has_saved_state_result = asyncio.run(state_persistence.has_saved_state())
                    has_saved_state = _unwrap_state_result("has_saved_state", has_saved_state_result)
                    if bool(has_saved_state):
                        logger.debug("[Plugin Process] Restoring saved state...")
                        load_result_obj = asyncio.run(state_persistence.load(instance))
                        load_result = _unwrap_state_result("load", load_result_obj)
                        if bool(load_result):
                            clear_result_obj = asyncio.run(state_persistence.clear())  # 恢复后清除
                            clear_result = _unwrap_state_result("clear", clear_result_obj)
                            if not bool(clear_result):
                                raise RuntimeError("state persistence clear returned falsy result")
                            ctx._restored_from_freeze = True  # 标记为从冻结恢复
                except Exception as e:
                    logger.warning("[Plugin Process] Failed to restore saved state: {}", e)
        
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
        
        ctx._entry_map = entry_map
        ctx._instance = instance

        # asyncio.Queue fed from the downlink for plugin-to-plugin responses
        _response_inbox: asyncio.Queue = asyncio.Queue()
        ctx._response_queue = _response_inbox
        _startup_pending_downlink: list[tuple[str, dict]] = []

        async def _startup_downlink_pump(stop_event: asyncio.Event) -> None:
            poll_ms = int(QUEUE_GET_TIMEOUT * 1000)
            while not stop_event.is_set():
                result = await child_transport.recv_downlink(timeout_ms=poll_ms)
                if result is None:
                    continue
                ch, msg = result
                if ch == CH_RESP:
                    await _response_inbox.put(msg)
                    continue
                if ch == CH_CMD and isinstance(msg, dict) and msg.get("type") == "STOP":
                    stop_event.set()
                    break
                if isinstance(msg, dict):
                    _startup_pending_downlink.append((ch, msg))

        async def _run_startup_with_downlink(startup_callable: Any) -> None:
            stop_event = asyncio.Event()
            pump_task = asyncio.create_task(_startup_downlink_pump(stop_event), name="startup-downlink-pump")
            try:
                await startup_callable()
            finally:
                stop_event.set()
                pump_task.cancel()
                try:
                    await pump_task
                except asyncio.CancelledError:
                    pass

        # 生命周期：startup
        lifecycle_events = events_by_type.get("lifecycle", {})
        startup_fn = lifecycle_events.get("startup")
        if startup_fn:
            try:
                with ctx._handler_scope("lifecycle.startup"):
                    asyncio.run(_run_startup_with_downlink(startup_fn))
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
                    asyncio.run(unfreeze_fn())
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
                        asyncio.run(fn())
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
                    asyncio.run(fn())
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

        # ────────────────────────────────────────────────────
        #  Async command loop  (replaces the old sync while-loop)
        # ────────────────────────────────────────────────────

        _STALL_THRESHOLD = 5.0

        async def _run_with_watchdog(coro, label: str, timeout_seconds):
            """Run *coro* with a stall-detection watchdog and timeout."""
            task = asyncio.ensure_future(coro)

            async def _watchdog():
                while not task.done():
                    t0 = time.monotonic()
                    await asyncio.sleep(1.0)
                    wall = time.monotonic() - t0
                    if wall > _STALL_THRESHOLD and not task.done():
                        logger.warning(
                            "[Plugin Process] Event-loop stall: sleep(1s) took {:.1f}s "
                            "in '{}'. Likely blocking I/O in async code.",
                            wall, label,
                        )

            wd = asyncio.create_task(_watchdog())
            try:
                return await asyncio.wait_for(task, timeout=timeout_seconds)
            finally:
                wd.cancel()

        def _resolve_timeout(entry_id: str, requested_timeout: Any = None):
            try:
                explicit_timeout = float(requested_timeout)
                return None if explicit_timeout <= 0 else explicit_timeout
            except (TypeError, ValueError):
                pass
            entry_meta = entry_meta_map.get(entry_id)
            if entry_meta:
                timeout_value = getattr(entry_meta, "timeout", None)
                if timeout_value is not None:
                    return None if timeout_value <= 0 else timeout_value
                extra = getattr(entry_meta, "extra", None) or {}
                ct = extra.get("timeout")
                if ct is not None:
                    return None if ct <= 0 else ct
            return PLUGIN_TRIGGER_TIMEOUT

        async def _save_plugin_state(reason: str) -> None:
            sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            if not sp:
                return

            save_method = getattr(sp, "save", None)
            if not callable(save_method):
                return

            try:
                save_result = save_method(instance)
            except TypeError:
                save_result = save_method(instance, freezable_keys, reason=reason)

            if inspect.isawaitable(save_result):
                await save_result

        # run_id → asyncio.Task – used by CANCEL_RUN to propagate cancellation
        _run_tasks: Dict[str, asyncio.Task] = {}

        async def _handle_trigger(msg: dict):
            entry_id = msg.get("entry_id")
            args = msg.get("args", {})
            req_id = msg.get("req_id", "unknown")

            if not entry_id or not isinstance(args, dict):
                res_sender.put(
                    {"req_id": req_id, "success": False, "data": None,
                     "error": "Invalid TRIGGER payload: 'entry_id' required, 'args' must be dict"},
                    timeout=10.0,
                )
                return

            logger.info("[Plugin Process] TRIGGER entry='{}' req_id={}", entry_id, req_id)

            method = (
                entry_map.get(entry_id)
                or getattr(instance, entry_id, None)
                or getattr(instance, f"entry_{entry_id}", None)
            )
            if method is None and hasattr(instance, "collect_entries") and callable(instance.collect_entries):
                try:
                    _rebuild_entry_map()
                    method = (
                        entry_map.get(entry_id)
                        or getattr(instance, entry_id, None)
                        or getattr(instance, f"entry_{entry_id}", None)
                    )
                    if method is not None:
                        logger.info(
                            "[Plugin Process] Rebuilt dynamic entry map and resolved entry='{}' req_id={}",
                            entry_id,
                            req_id,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to rebuild entry map while resolving '{}' req_id={}: {}",
                        entry_id,
                        req_id,
                        e,
                    )
            ret = {"req_id": req_id, "success": False, "data": None, "error": None}

            run_id = None
            try:
                ctx_obj = args.get("_ctx") if isinstance(args, dict) else None
                if isinstance(ctx_obj, dict):
                    run_id = ctx_obj.get("run_id")
                    lanlan_name = ctx_obj.get("lanlan_name")
                    if lanlan_name:
                        ctx._current_lanlan = str(lanlan_name)
            except Exception:
                pass

            try:
                if not method:
                    raise PluginEntryNotFoundError(plugin_id, entry_id)

                if not (asyncio.iscoroutinefunction(method) or inspect.iscoroutinefunction(method)):
                    ret["error"] = f"Entry '{entry_id}' must be 'async def'. Sync entries are not supported."
                    return

                timeout_seconds = _resolve_timeout(entry_id, msg.get("timeout"))

                with ctx._handler_scope(f"plugin_entry.{entry_id}"), ctx._run_scope(run_id):
                    result = await _run_with_watchdog(
                        method(**args), entry_id, timeout_seconds,
                    )

                if hasattr(result, "is_ok") and callable(result.is_ok):
                    if result.is_ok():
                        ret["success"] = True
                        ret["data"] = result.value
                    else:
                        ret["success"] = False
                        err = result.error
                        ret["error"] = str(err) if err is not None else "Unknown error"
                else:
                    ret["success"] = True
                    ret["data"] = result

                if _should_persist(method):
                    try:
                        await _save_plugin_state("auto")
                    except Exception:
                        pass

            except asyncio.CancelledError:
                ret["error"] = "Execution cancelled"
            except asyncio.TimeoutError:
                resolved_timeout = _resolve_timeout(entry_id, msg.get("timeout"))
                logger.error("Entry '{}' timed out after {}s", entry_id, resolved_timeout)
                ret["error"] = f"Execution timed out after {resolved_timeout}s"
            except PluginError as e:
                logger.warning("Plugin error executing '{}': {}", entry_id, e)
                ret["error"] = str(e)
            except Exception as e:
                logger.exception("Unexpected error executing '{}'", entry_id)
                ret["error"] = f"Unexpected error: {e}"
            finally:
                if run_id:
                    _run_tasks.pop(run_id, None)
                try:
                    res_sender.put(ret, timeout=10.0)
                except Exception:
                    logger.exception("Failed to send response for req_id={}", req_id)

        async def _handle_trigger_custom(msg: dict):
            event_type = msg.get("event_type")
            event_id = msg.get("event_id")
            args = msg.get("args", {})
            req_id = msg.get("req_id", "unknown")

            logger.info("[Plugin Process] TRIGGER_CUSTOM {}.{} req_id={}", event_type, event_id, req_id)

            try:
                ctx_obj = args.get("_ctx") if isinstance(args, dict) else None
                if isinstance(ctx_obj, dict):
                    lanlan_name = ctx_obj.get("lanlan_name")
                    if lanlan_name:
                        ctx._current_lanlan = str(lanlan_name)
            except Exception:
                pass

            custom_events = events_by_type.get(event_type, {})
            method = custom_events.get(event_id)
            ret = {"req_id": req_id, "success": False, "data": None, "error": None}

            try:
                if not method:
                    ret["error"] = f"Custom event '{event_type}.{event_id}' not found"
                    return

                if not asyncio.iscoroutinefunction(method):
                    ret["error"] = f"Custom event '{event_type}.{event_id}' must be 'async def'."
                    return

                with ctx._handler_scope(f"{event_type}.{event_id}"):
                    result = await _run_with_watchdog(
                        method(**args),
                        f"{event_type}.{event_id}",
                        PLUGIN_TRIGGER_TIMEOUT,
                    )

                if hasattr(result, "is_ok") and callable(result.is_ok):
                    if result.is_ok():
                        ret["success"] = True
                        ret["data"] = result.value
                    else:
                        ret["success"] = False
                        err = result.error
                        ret["error"] = str(err) if err is not None else "Unknown error"
                else:
                    ret["success"] = True
                    ret["data"] = result

            except asyncio.CancelledError:
                ret["error"] = "Execution cancelled"
            except asyncio.TimeoutError:
                logger.error("Custom event {}.{} timed out", event_type, event_id)
                ret["error"] = f"Custom event timed out after {PLUGIN_TRIGGER_TIMEOUT}s"
            except Exception as e:
                logger.exception("Error executing custom event {}.{}", event_type, event_id)
                ret["error"] = str(e)
            finally:
                try:
                    res_sender.put(ret, timeout=10.0)
                except Exception:
                    logger.exception("Failed to send response for req_id={}", req_id)

        async def _async_command_loop():
            poll_ms = int(QUEUE_GET_TIMEOUT * 1000)

            while True:
                try:
                    if process_stop_event is not None and process_stop_event.is_set():
                        break
                except Exception:
                    pass

                try:
                    if _startup_pending_downlink:
                        result = _startup_pending_downlink.pop(0)
                    else:
                        result = await child_transport.recv_downlink(timeout_ms=poll_ms)
                except asyncio.CancelledError:
                    logger.info("[Plugin Process] Command loop cancelled, shutting down")
                    break
                if result is None:
                    continue
                ch, msg = result

                # Plugin-to-plugin responses arrive on the downlink tagged CH_RESP
                if ch == CH_RESP:
                    await _response_inbox.put(msg)
                    continue

                if ch != CH_CMD or not isinstance(msg, dict):
                    continue
                msg_type = msg.get("type")
                if not msg_type:
                    continue

                # ── STOP ──
                if msg_type == "STOP":
                    break

                # ── CANCEL_RUN ──
                if msg_type == "CANCEL_RUN":
                    run_id = msg.get("run_id")
                    task = _run_tasks.get(run_id) if run_id else None
                    if task and not task.done():
                        task.cancel()
                        logger.info("[Plugin Process] Cancel sent for run_id={}", run_id)
                    continue

                # ── FREEZE ──
                if msg_type == "FREEZE":
                    req_id = msg.get("req_id", "unknown")
                    logger.info("[Plugin Process] FREEZE req_id={}", req_id)
                    ret = {"req_id": req_id, "success": False, "data": None, "error": None}
                    try:
                        freeze_fn = lifecycle_events.get("freeze")
                        if freeze_fn:
                            with ctx._handler_scope("lifecycle.freeze"):
                                await freeze_fn()
                        if freezable_keys:
                            await _save_plugin_state("freeze")
                        ret["success"] = True
                        ret["data"] = {"frozen": True, "freezable_keys": freezable_keys}
                    except Exception as e:
                        logger.exception("[Plugin Process] Freeze failed")
                        ret["error"] = str(e)
                    res_sender.put(ret, timeout=10.0)
                    if ret["success"]:
                        break
                    continue

                # ── BUS_CHANGE ──
                if msg_type == "BUS_CHANGE":
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

                # ── CONFIG_UPDATE ──
                if msg_type == "CONFIG_UPDATE":
                    await _handle_config_update_command(
                        msg=msg, ctx=ctx, events_by_type=events_by_type,
                        plugin_id=plugin_id, res_sender=res_sender, logger=logger,
                    )
                    continue

                # ── TRIGGER_CUSTOM ──
                if msg_type == "TRIGGER_CUSTOM":
                    req_id = str(msg.get("req_id") or uuid.uuid4())
                    task_key = f"custom:{req_id}"
                    task = asyncio.create_task(_handle_trigger_custom(msg))
                    _run_tasks[task_key] = task
                    task.add_done_callback(lambda _t, key=task_key: _run_tasks.pop(key, None))
                    continue

                # ── DISABLE / ENABLE EXTENSION ──
                if msg_type == "DISABLE_EXTENSION":
                    ext_name = msg.get("ext_name", "")
                    req_id = msg.get("req_id", "unknown")
                    ret = {"req_id": req_id, "success": False, "data": None, "error": None}
                    try:
                        if instance.exclude_router(ext_name):
                            _rebuild_entry_map()
                            ret["success"] = True
                            ret["data"] = {"disabled": ext_name}
                        else:
                            ret["error"] = f"Extension '{ext_name}' not found"
                    except Exception as e:
                        ret["error"] = str(e)
                    res_sender.put(ret, timeout=10.0)
                    continue

                if msg_type == "ENABLE_EXTENSION":
                    ext_id = msg.get("ext_id", "")
                    ext_entry = msg.get("ext_entry", "")
                    prefix = msg.get("prefix", "")
                    req_id = msg.get("req_id", "unknown")
                    ret = {"req_id": req_id, "success": False, "data": None, "error": None}
                    try:
                        existing = instance.get_router(ext_id) if hasattr(instance, "get_router") else None
                        if existing:
                            ret["error"] = f"Extension '{ext_id}' already injected"
                        elif not ext_entry or ":" not in ext_entry:
                            ret["error"] = f"Invalid ext_entry '{ext_entry}'"
                        else:
                            mod_path, cls_name = ext_entry.split(":", 1)
                            mod = importlib.import_module(mod_path)
                            router_cls = getattr(mod, cls_name)
                            if not (isinstance(router_cls, type) and issubclass(router_cls, PluginRouter)):
                                ret["error"] = f"'{cls_name}' is not a PluginRouter subclass"
                            else:
                                router_inst = router_cls(prefix=prefix, name=ext_id)
                                instance.include_router(router_inst)
                                _rebuild_entry_map()
                                ret["success"] = True
                                ret["data"] = {"enabled": ext_id}
                    except Exception as e:
                        ret["error"] = str(e)
                    res_sender.put(ret, timeout=10.0)
                    continue

                # ── TRIGGER ──
                if msg_type == "TRIGGER":
                    run_id = None
                    try:
                        ctx_obj = msg.get("args", {}).get("_ctx")
                        if isinstance(ctx_obj, dict):
                            run_id = ctx_obj.get("run_id")
                    except Exception:
                        pass
                    task = asyncio.create_task(_handle_trigger(msg))
                    if run_id:
                        _run_tasks[run_id] = task
                    continue

            # Loop exited — cancel any in-flight tasks
            for t in _run_tasks.values():
                if not t.done():
                    t.cancel()
            if _run_tasks:
                await asyncio.gather(*_run_tasks.values(), return_exceptions=True)
                _run_tasks.clear()

        asyncio.run(_async_command_loop())

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
                    result = shutdown_fn()
                    if asyncio.iscoroutine(result):
                        asyncio.run(result)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.exception("Error in lifecycle.shutdown: {}", e)

        try:
            ctx.close()
        except Exception as e:
            logger.debug("[Plugin Process] Context close failed during shutdown: {}", e)

        child_transport.close()

    except (KeyboardInterrupt, SystemExit):
        logger.info("Plugin process {} interrupted", plugin_id)
        try:
            for ev in timer_stop_events:
                try:
                    ev.set()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            shutdown_fn = lifecycle_events.get("shutdown")
            if shutdown_fn:
                with ctx._handler_scope("lifecycle.shutdown"):
                    result = shutdown_fn()
                    if asyncio.iscoroutine(result):
                        asyncio.run(result)
        except BaseException:
            pass
        try:
            ctx.close()
        except Exception:
            pass
        try:
            child_transport.close()
        except Exception:
            pass
    except Exception as e:
        # 进程崩溃，记录详细信息
        logger.exception("Plugin process {} crashed", plugin_id)
        # 尝试发送错误信息到结果队列（如果可能）
        try:
            res_sender.put({
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
        self.logger = logger.bind(plugin_id=plugin_id, host=True)

        # ZMQ transport: 2 socket pairs replace 5 mp.Queues
        self.transport = HostTransport()

        self._process_stop_event: Any = multiprocessing.Event()

        # Shared response notification primitives must be initialized before
        # forking, otherwise each child creates its own Manager proxies.
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
                self.transport.downlink_endpoint,
                self.transport.uplink_endpoint,
                self._process_stop_event,
                extension_configs,
            ),
            # Plugin code may spawn subprocesses/Managers; daemon process would forbid that.
            daemon=False,
        )

        self.comm_manager = PluginCommunicationResourceManager(
            plugin_id=plugin_id,
            transport=self.transport,
        )
    
    async def start(self, message_target_queue=None) -> None:
        """
        启动后台任务（需要在异步上下文中调用）
        
        Args:
            message_target_queue: 主进程的消息队列，用于接收插件推送的消息
        """
        # Register this plugin's downlink sender so plugin_router can
        # route plugin-to-plugin responses back to the child process.
        state.register_downlink_sender(self.plugin_id, self.comm_manager.send_plugin_response)

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
            state.unregister_downlink_sender(self.plugin_id)
            try:
                self.transport.close()
            except Exception:
                pass
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
            state.unregister_downlink_sender(self.plugin_id)
            try:
                self.transport.close()
            except Exception:
                pass
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

        # Set out-of-band stop event first so the child can exit promptly.
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        
        # 1. 发送停止命令
        await self.comm_manager.send_stop_command()

        # 2. 关闭通信资源
        await self.comm_manager.shutdown(timeout=timeout)

        # 3. Unregister downlink sender & close ZMQ transport
        state.remove_downlink_sender(self.plugin_id)
        self.transport.close()

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

        # 尽量通知通信管理器停止（即使不等待）
        if getattr(self, "comm_manager", None) is not None:
            try:
                _ev = getattr(self.comm_manager, "_shutdown_event", None)
                if _ev is not None:
                    _ev.set()
            except Exception:
                pass

        state.remove_downlink_sender(self.plugin_id)
        self.transport.close()

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
        supports cancellation (async entries).
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
                    self.comm_manager._uplink_consumer_task is not None
                    and not self.comm_manager._uplink_consumer_task.done()
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
            await asyncio.to_thread(self._shutdown_process, timeout)
            await self.comm_manager.shutdown(timeout=timeout)
            state.remove_downlink_sender(self.plugin_id)
            self.transport.close()
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
