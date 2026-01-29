from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Callable, Type, Optional, cast

from loguru import logger as loguru_logger


_pending_async_shutdown_tasks: set = set()


class _LoggerAdapter:
    def __init__(self, logger: Any):
        self._logger = logger

    def _is_stdlib(self) -> bool:
        return isinstance(self._logger, logging.Logger)

    def _fmt(self, msg: str, args: tuple[Any, ...]) -> str:
        if not args:
            return str(msg)
        try:
            return str(msg).format(*args)
        except Exception:
            return f"{msg} {args!r}"

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self._is_stdlib():
            self._logger.debug(self._fmt(msg, args), **kwargs)
        else:
            self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self._is_stdlib():
            self._logger.info(self._fmt(msg, args), **kwargs)
        else:
            self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self._is_stdlib():
            self._logger.warning(self._fmt(msg, args), **kwargs)
        else:
            self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self._is_stdlib():
            self._logger.error(self._fmt(msg, args), **kwargs)
        else:
            self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self._is_stdlib():
            self._logger.exception(self._fmt(msg, args), **kwargs)
        else:
            # loguru supports logger.exception(msg, *args)
            self._logger.exception(msg, *args, **kwargs)


def _wrap_logger(logger: Any) -> Any:
    # Avoid double-wrapping.
    if isinstance(logger, _LoggerAdapter):
        return logger
    if isinstance(logger, logging.Logger):
        return _LoggerAdapter(logger)
    return logger

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.sdk.events import EventHandler, EVENT_META_ATTR
from plugin.sdk.version import SDK_VERSION
from plugin.core.state import state
from plugin.api.models import PluginMeta, PluginAuthor, PluginDependency
from plugin.api.exceptions import (
    PluginImportError,
    PluginLoadError,
    PluginMetadataError,
)
try:
    from packaging.version import Version, InvalidVersion
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
except ImportError:  # pragma: no cover
    Version = None  # type: ignore
    InvalidVersion = Exception  # type: ignore
    SpecifierSet = None  # type: ignore
    InvalidSpecifier = Exception  # type: ignore


@dataclass
class SimpleEntryMeta:
    event_type: str = "plugin_entry"
    id: str = ""
    name: str = ""
    description: str = ""
    input_schema: dict | None = None

    def __post_init__(self):
        if self.input_schema is None:
            self.input_schema = {}


# Mapping from (plugin_id, entry_id) -> actual python method name on the instance.
plugin_entry_method_map: Dict[tuple, str] = {}


def _parse_specifier(spec: Optional[str], logger: Any) -> Optional[Any]:
    logger = _wrap_logger(logger)
    if not spec or SpecifierSet is None:
        return None
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier as e:
        logger.error("Invalid sdk specifier '{}': {}", spec, e)
        return None


def _version_matches(spec: Optional[Any], version: Any) -> bool:
    if spec is None:
        return False
    try:
        return version in spec
    except Exception:
        return False


def _find_plugins_by_entry(entry_id: str) -> List[tuple[str, Dict[str, Any]]]:
    """
    根据入口点ID查找提供该入口的所有插件（只能查找 @plugin_entry）
    
    Args:
        entry_id: 入口点ID
    
    Returns:
        (插件ID, 插件元数据) 列表
    """
    matching_plugins = []
    
    with state.event_handlers_lock:
        event_handlers_copy = dict(state.event_handlers)
    
    # 查找所有提供该入口点的插件
    found_plugin_ids = set()
    for key, eh in event_handlers_copy.items():
        # 检查 key 格式：plugin_id.entry_id 或 plugin_id:plugin_entry:entry_id
        if "." in key:
            parts = key.split(".", 1)
            if len(parts) == 2 and parts[1] == entry_id:
                # 验证是 plugin_entry 类型
                meta = getattr(eh, "meta", None)
                if meta and getattr(meta, "event_type", None) == "plugin_entry":
                    found_plugin_ids.add(parts[0])
        elif ":" in key:
            parts = key.split(":", 2)
            if len(parts) == 3 and parts[1] == "plugin_entry" and parts[2] == entry_id:
                found_plugin_ids.add(parts[0])
    
    # 获取这些插件的元数据
    with state.plugins_lock:
        for pid in found_plugin_ids:
            if pid in state.plugins:
                meta = state.plugins[pid]
                # Disabled plugins are visible but should not participate in dependency satisfaction.
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                matching_plugins.append((pid, meta))
    
    return matching_plugins


def _find_plugins_by_custom_event(event_type: str, event_id: str) -> List[tuple[str, Dict[str, Any]]]:
    """
    根据自定义事件类型和ID查找提供该事件的所有插件（只能查找 @custom_event）
    
    Args:
        event_type: 自定义事件类型
        event_id: 事件ID
    
    Returns:
        (插件ID, 插件元数据) 列表
    """
    matching_plugins = []
    
    with state.event_handlers_lock:
        event_handlers_copy = dict(state.event_handlers)
    
    # 查找所有提供该自定义事件的插件
    found_plugin_ids = set()
    for key, _eh in event_handlers_copy.items():
        # 检查 key 格式：plugin_id:event_type:event_id
        if ":" in key:
            parts = key.split(":", 2)
            if len(parts) == 3:
                pid, etype, eid = parts
                if etype == event_type and eid == event_id:
                    # 验证不是标准类型（plugin_entry, lifecycle, message, timer）
                    if etype not in ("plugin_entry", "lifecycle", "message", "timer"):
                        found_plugin_ids.add(pid)
    
    # 获取这些插件的元数据
    with state.plugins_lock:
        for pid in found_plugin_ids:
            if pid in state.plugins:
                meta = state.plugins[pid]
                # Disabled plugins are visible but should not participate in dependency satisfaction.
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                matching_plugins.append((pid, meta))
    
    return matching_plugins


def _check_plugin_dependency(
    dependency: PluginDependency,
    logger: Any,
    plugin_id: str
) -> tuple[bool, Optional[str]]:
    """
    检查插件依赖是否满足
    
    支持四种依赖方式：
    1. 依赖特定插件ID：id = "plugin_id"
    2. 依赖特定入口点：entry = "entry_id" 或 entry = "plugin_id:entry_id"（只能引用 @plugin_entry）
    3. 依赖特定自定义事件：custom_event = "event_type:event_id" 或 custom_event = "plugin_id:event_type:event_id"（只能引用 @custom_event）
    4. 依赖多个候选插件：providers = ["plugin1", "plugin2"]（任一满足即可）
    
    注意：entry 和 custom_event 互斥（不能同时使用）
    
    Args:
        dependency: 依赖配置
        logger: 日志记录器
        plugin_id: 当前插件 ID（用于日志）
    
    Returns:
        (是否满足, 错误信息)
    """
    logger = _wrap_logger(logger)
    def _runtime_enabled(pid: str) -> bool:
        with state.plugins_lock:
            meta = state.plugins.get(pid)
        if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
            return False
        return meta is not None

    # 如果 conflicts 是 true，表示冲突（不允许）
    if dependency.conflicts is True:
        if not dependency.id:
            return False, "Dependency with conflicts=True requires 'id' field"

        if dependency.id:
            # 检查依赖插件是否存在
            if _runtime_enabled(str(dependency.id)):
                return False, f"Dependency plugin '{dependency.id}' conflicts (conflicts=true) but plugin exists"
        return True, None  # 简化格式，插件不存在则满足
    
    # 确定要检查的插件列表
    plugins_to_check: List[tuple[str, Dict[str, Any]]] = []
    
    if dependency.providers:
        # 方式3：多个候选插件（任一满足即可）
        with state.plugins_lock:
            for provider_id in dependency.providers:
                meta = state.plugins.get(provider_id)
                if meta is None:
                    continue
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                plugins_to_check.append((provider_id, meta))
        
        if not plugins_to_check:
            return False, f"None of the provider plugins {dependency.providers} found"
        
        # 检查任一插件是否满足（只要有一个满足即可）
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency satisfied by provider '{}'", plugin_id, dep_id)
                return True, None
        
        # 所有候选插件都不满足
        return False, f"None of the provider plugins {dependency.providers} satisfy version requirements"
    
    elif dependency.entry:
        # 方式2：依赖特定入口点（只能引用 @plugin_entry）
        # 检查是否同时指定了 custom_event（互斥）
        if dependency.custom_event:
            return False, "Cannot specify both 'entry' and 'custom_event' in dependency (they are mutually exclusive)"
        
        entry_spec = dependency.entry
        if ":" in entry_spec:
            # 格式：plugin_id:entry_id
            parts = entry_spec.split(":", 1)
            if len(parts) != 2:
                return False, f"Invalid entry format: '{entry_spec}', expected 'plugin_id:entry_id' or 'entry_id'"
            target_plugin_id, target_entry_id = parts

            with state.plugins_lock:
                if target_plugin_id not in state.plugins:
                    return False, f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' not found"

            # 验证指定插件确实提供该入口（且是 @plugin_entry）
            matching_plugins = _find_plugins_by_entry(target_entry_id)
            if not any(pid == target_plugin_id for pid, _ in matching_plugins):
                return False, (
                    f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' does not provide entry '{target_entry_id}'"
                )

            with state.plugins_lock:
                plugins_to_check = [(target_plugin_id, state.plugins[target_plugin_id])]
        else:
            # 格式：entry_id（任意插件提供该入口）
            entry_id = entry_spec
            matching_plugins = _find_plugins_by_entry(entry_id)
            if not matching_plugins:
                return False, f"Dependency entry '{entry_id}' not found in any plugin"
            plugins_to_check = matching_plugins
        
        # 检查提供该入口的插件是否满足版本要求
        # 如果多个插件提供该入口，任一满足即可
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency entry '{}' satisfied by plugin '{}'", plugin_id, entry_spec, dep_id)
                return True, None
        
        # 所有提供该入口的插件都不满足版本要求
        return False, f"Dependency entry '{entry_spec}' found but version requirements not satisfied"
    
    elif dependency.custom_event:
        # 方式3：依赖特定自定义事件（只能引用 @custom_event）
        custom_event_spec = dependency.custom_event
        if ":" in custom_event_spec:
            # 解析格式：
            # - event_type:event_id（严格 1 个 ':'）
            # - plugin_id:event_type:event_id（允许 event_id 里包含 ':'，因此只切前两次）
            if custom_event_spec.count(":") == 1:
                # 格式：event_type:event_id（任意插件提供该事件）
                event_type, event_id = custom_event_spec.split(":", 1)
                matching_plugins = _find_plugins_by_custom_event(event_type, event_id)
                if not matching_plugins:
                    return False, f"Dependency custom_event '{custom_event_spec}' not found in any plugin"
                plugins_to_check = matching_plugins
            else:
                # 格式：plugin_id:event_type:event_id（指定插件必须提供该事件）
                target_plugin_id, rest = custom_event_spec.split(":", 1)
                if ":" not in rest:
                    return False, f"Invalid custom_event format: '{custom_event_spec}', expected 'event_type:event_id' or 'plugin_id:event_type:event_id'"
                event_type, event_id = rest.split(":", 1)
                # 先在锁内检查插件是否存在，并读取元数据（避免后续嵌套 plugins_lock 导致死锁）
                with state.plugins_lock:
                    dep_meta = state.plugins.get(target_plugin_id)
                if dep_meta is None:
                    return False, f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' not found"
                # Ensure disabled plugins do not satisfy dependencies.
                if isinstance(dep_meta, dict) and dep_meta.get("runtime_enabled") is False:
                    return False, f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' does not provide event '{event_type}.{event_id}'"

                # 在锁外调用，避免 _find_plugins_by_custom_event 内部再次获取 plugins_lock 造成死锁
                matching_plugins = _find_plugins_by_custom_event(event_type, event_id)
                if not any(pid == target_plugin_id for pid, _ in matching_plugins):
                    return False, (
                        f"Dependency custom_event '{custom_event_spec}': plugin '{target_plugin_id}' does not provide event '{event_type}.{event_id}'"
                    )
                plugins_to_check = [(target_plugin_id, dep_meta)]
        else:
            return False, f"Invalid custom_event format: '{custom_event_spec}', expected 'event_type:event_id' or 'plugin_id:event_type:event_id'"
        
        # 检查提供该自定义事件的插件是否满足版本要求
        # 如果多个插件提供该事件，任一满足即可
        for dep_id, dep_plugin_meta in plugins_to_check:
            satisfied, _ = _check_single_plugin_version(
                dep_id, dep_plugin_meta, dependency, logger, plugin_id
            )
            if satisfied:
                logger.debug("Plugin {}: dependency custom_event '{}' satisfied by plugin '{}'", plugin_id, custom_event_spec, dep_id)
                return True, None
        
        # 所有提供该事件的插件都不满足版本要求
        return False, f"Dependency custom_event '{custom_event_spec}' found but version requirements not satisfied"
    
    elif dependency.id:
        # 方式1：依赖特定插件ID
        dep_id = dependency.id
        with state.plugins_lock:
            if dep_id not in state.plugins:
                return False, f"Dependency plugin '{dep_id}' not found"
            dep_plugin_meta = state.plugins[dep_id]

        # Disabled plugins are visible but must not satisfy dependencies.
        if isinstance(dep_plugin_meta, dict) and dep_plugin_meta.get("runtime_enabled") is False:
            return False, f"Dependency plugin '{dep_id}' not found"
        
        return _check_single_plugin_version(
            dep_id, dep_plugin_meta, dependency, logger, plugin_id
        )
    
    else:
        return False, "Dependency must specify at least one of 'id', 'entry', 'custom_event', or 'providers'"


def _check_single_plugin_version(
    dep_id: str,
    dep_plugin_meta: Dict[str, Any],
    dependency: PluginDependency,
    logger: Any,
    plugin_id: str
) -> tuple[bool, Optional[str]]:
    """
    检查单个插件的版本是否满足依赖要求
    
    Args:
        dep_id: 依赖插件ID
        dep_plugin_meta: 依赖插件元数据
        dependency: 依赖配置
        logger: 日志记录器
        plugin_id: 当前插件ID（用于日志）
    
    Returns:
        (是否满足, 错误信息)
    """
    logger = _wrap_logger(logger)
    dep_version_str = dep_plugin_meta.get("version", "0.0.0")
    
    # 如果 conflicts 是列表，检查版本是否在冲突范围内
    if isinstance(dependency.conflicts, list) and dependency.conflicts:
        if Version and SpecifierSet:
            try:
                dep_version_obj = Version(dep_version_str)
                conflict_specs = [
                    _parse_specifier(conf, logger) for conf in dependency.conflicts
                ]
                if any(spec and _version_matches(spec, dep_version_obj) for spec in conflict_specs):
                    return False, f"Dependency plugin '{dep_id}' version {dep_version_str} conflicts with required ranges: {dependency.conflicts}"
            except InvalidVersion:
                logger.warning("Cannot parse dependency plugin '{}' version '{}'", dep_id, dep_version_str)
    
    # 如果使用依赖配置，untested 是必须的
    if dependency.untested is None:
        return False, "Dependency configuration requires 'untested' field"
    
    # 检查版本是否在 untested 范围内
    if Version and SpecifierSet:
        try:
            dep_version_obj = Version(dep_version_str)
            untested_spec = _parse_specifier(dependency.untested, logger)
            
            if untested_spec is None:
                return False, f"Invalid dependency 'untested' specifier: {dependency.untested!r}"
            if untested_spec:
                in_untested = _version_matches(untested_spec, dep_version_obj)
                if not in_untested:
                    # 检查是否在 supported 范围内
                    supported_spec = _parse_specifier(dependency.supported, logger)
                    in_supported = _version_matches(supported_spec, dep_version_obj) if supported_spec else False
                    
                    if not in_supported:
                        return False, (
                            f"Dependency plugin '{dep_id}' version {dep_version_str} "
                            f"does not match untested range '{dependency.untested}' "
                            f"(or supported range '{dependency.supported or 'N/A'}')"
                        )
            
            # 检查 recommended 范围（警告）
            if dependency.recommended:
                recommended_spec = _parse_specifier(dependency.recommended, logger)
                if recommended_spec and not _version_matches(recommended_spec, dep_version_obj):
                    logger.warning(
                        "Plugin {}: dependency '{}' version {} is outside recommended range {}",
                        plugin_id, dep_id, dep_version_str, dependency.recommended
                    )
        except InvalidVersion:
            logger.warning("Cannot parse dependency plugin '{}' version '{}'", dep_id, dep_version_str)
    
    return True, None


def _parse_plugin_dependencies(
    conf: Dict[str, Any],
    logger: logging.Logger,
    plugin_id: str
) -> List[PluginDependency]:
    """
    解析插件依赖配置
    
    支持两种格式：
    1. [[plugin.dependency]] - 完整格式
    2. [[plugin.dependency]] with conflicts = true - 简化格式
    
    Args:
        conf: TOML 配置字典
        logger: 日志记录器
        plugin_id: 插件 ID（用于日志）
    
    Returns:
        依赖列表
    """
    logger = _wrap_logger(logger)
    dependencies: List[PluginDependency] = []
    
    # TOML 数组表语法 [[plugin.dependency]] 会被解析为 conf["plugin"]["dependency"] 列表
    dep_configs = conf.get("plugin", {}).get("dependency", [])
    
    # 如果不是列表，转换为列表
    if not isinstance(dep_configs, list):
        if isinstance(dep_configs, dict):
            dep_configs = [dep_configs]
        else:
            return dependencies
    
    for dep_config in dep_configs:
        if not isinstance(dep_config, dict):
            logger.warning("Plugin {}: invalid dependency config (not a dict), skipping", plugin_id)
            continue
        
        # 支持四种依赖方式：id、entry、custom_event、providers（至少需要一个）
        dep_id = dep_config.get("id")
        dep_entry = dep_config.get("entry")
        dep_custom_event = dep_config.get("custom_event")
        dep_providers = dep_config.get("providers")
        
        if not dep_id and not dep_entry and not dep_custom_event and not dep_providers:
            logger.warning("Plugin {}: dependency config must have at least one of 'id', 'entry', 'custom_event', or 'providers' field, skipping", plugin_id)
            continue
        
        # 检查 entry 和 custom_event 互斥
        if dep_entry and dep_custom_event:
            logger.warning("Plugin {}: dependency config cannot have both 'entry' and 'custom_event' fields (they are mutually exclusive), skipping", plugin_id)
            continue
        
        # 处理简化格式：conflicts = true（仅支持 id 方式）
        conflicts = dep_config.get("conflicts")
        if conflicts is True:
            if not dep_id:
                logger.warning("Plugin {}: dependency with conflicts=true requires 'id' field, skipping", plugin_id)
                continue
            # 简化格式：只有 id 和 conflicts = true
            try:
                dependencies.append(
                    PluginDependency(
                        id=dep_id,
                        conflicts=True,
                    )
                )
            except Exception:
                try:
                    logger.exception(
                        "Plugin {}: failed to parse dependency config (conflicts=true), skipping: {}",
                        plugin_id,
                        dep_config,
                    )
                except Exception:
                    import sys
                    try:
                        sys.stderr.write(
                            f"[registry] Failed to log exception for plugin {plugin_id!s} (conflicts=true)\n"
                        )
                    except Exception:
                        pass
            continue
        
        # 完整格式：解析所有字段
        # 如果使用依赖配置，untested 是必须的（除非是简化格式）
        untested = dep_config.get("untested")
        if untested is None:
            logger.warning(
                "Plugin {}: dependency missing required 'untested' field, skipping",
                plugin_id
            )
            continue
        
        # 处理 conflicts 列表
        conflicts_list = None
        raw_conflicts = dep_config.get("conflicts")
        if isinstance(raw_conflicts, list):
            conflicts_list = [str(c) for c in raw_conflicts if c]
        elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
            conflicts_list = [raw_conflicts.strip()]
        
        # 处理 providers 列表
        providers_list = None
        if isinstance(dep_providers, list):
            providers_list = [str(p) for p in dep_providers if p]
        elif isinstance(dep_providers, str) and dep_providers.strip():
            providers_list = [dep_providers.strip()]

        try:
            dependencies.append(
                PluginDependency(
                    id=dep_id,
                    entry=dep_entry,
                    custom_event=dep_custom_event,
                    providers=providers_list,
                    recommended=dep_config.get("recommended"),
                    supported=dep_config.get("supported"),
                    untested=untested,
                    conflicts=conflicts_list,
                )
            )
        except Exception:
            try:
                logger.exception(
                    "Plugin {}: failed to parse dependency config, skipping: {}",
                    plugin_id,
                    dep_config,
                )
            except Exception:
                pass

    return dependencies


def get_plugins() -> List[Dict[str, Any]]:
    """Return list of plugin dicts (in-process access)."""
    with state.plugins_lock:
        return list(state.plugins.values())


def _calculate_plugin_hash(config_path: Optional[Path] = None, entry_point: Optional[str] = None, plugin_data: Optional[Dict[str, Any]] = None) -> str:
    """
    计算插件的哈希值，用于比较插件内容是否相同
    
    注意：为了确保相同插件产生相同哈希值，路径会被规范化（resolve为绝对路径）
    
    Args:
        config_path: 插件配置文件路径
        entry_point: 插件入口点
        plugin_data: 插件配置数据（可选），应包含 id、name、version、entry 字段
    
    Returns:
        插件的哈希值（十六进制字符串）
    """
    hash_data = []
    
    # 添加配置文件路径（如果提供）- 规范化路径以确保一致性
    if config_path:
        try:
            # 使用 resolve() 获取绝对路径并规范化
            resolved_path = config_path.resolve()
            # 使用字符串表示，确保跨平台一致性
            hash_data.append(f"config_path:{str(resolved_path)}")
        except (OSError, RuntimeError):
            # 如果路径解析失败，使用原始路径的字符串表示
            hash_data.append(f"config_path:{str(config_path)}")
    
    # 添加入口点（如果提供）- 标准化格式
    if entry_point:
        hash_data.append(f"entry_point:{entry_point.strip()}")
    
    # 添加插件配置数据的关键字段（如果提供）
    if plugin_data:
        # 使用关键字段来标识插件，按固定顺序以确保一致性
        key_fields = ["id", "name", "version", "entry"]
        for field in key_fields:
            if field in plugin_data:
                value = plugin_data[field]
                # 确保值为字符串，None 转为空字符串
                if value is None:
                    value = ""
                else:
                    value = str(value).strip()
                hash_data.append(f"{field}:{value}")
    
    # 计算哈希值
    content = "|".join(hash_data)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]  # 使用前16位作为简短标识


def _get_existing_plugin_info(plugin_id: str) -> Optional[Dict[str, Any]]:
    """
    获取已存在插件的信息
    
    Args:
        plugin_id: 插件 ID
    
    Returns:
        插件信息字典，包含 config_path、entry_point、plugin_meta 等，如果不存在则返回 None
    """
    result = {}
    
    # 锁顺序规范: plugins_lock -> plugin_hosts_lock -> event_handlers_lock
    # 先从 plugins 获取插件元数据
    with state.plugins_lock:
        if plugin_id in state.plugins:
            plugin_meta_raw = state.plugins[plugin_id]
            if isinstance(plugin_meta_raw, dict):
                result["plugin_meta"] = plugin_meta_raw
                meta_config_path = plugin_meta_raw.get("config_path")
                meta_entry_point = plugin_meta_raw.get("entry_point")
                if meta_config_path:
                    result["config_path"] = meta_config_path
                if meta_entry_point:
                    result["entry_point"] = meta_entry_point
            else:
                result["plugin_meta"] = plugin_meta_raw
                meta_config_path = getattr(plugin_meta_raw, "config_path", None)
                meta_entry_point = getattr(plugin_meta_raw, "entry_point", None)
                if meta_config_path:
                    result["config_path"] = meta_config_path
                if meta_entry_point:
                    result["entry_point"] = meta_entry_point
    
    # 再从 plugin_hosts 获取运行时信息（可能更新）
    with state.plugin_hosts_lock:
        if plugin_id in state.plugin_hosts:
            host = state.plugin_hosts[plugin_id]
            config_path = getattr(host, 'config_path', None)
            entry_point = getattr(host, 'entry_point', None)
            if config_path:
                result["config_path"] = config_path
            if entry_point:
                result["entry_point"] = entry_point
    
    # 如果获取到了任何信息，返回结果
    if result:
        return result
    
    return None


def _resolve_plugin_id_conflict(
    plugin_id: str,
    logger: Any,  # loguru.Logger or logging.Logger
    config_path: Optional[Path] = None,
    entry_point: Optional[str] = None,
    plugin_data: Optional[Dict[str, Any]] = None,
    *,
    purpose: str = "load",
    enable_rename: Optional[bool] = None,
) -> Optional[str]:
    """
    检测并解决插件 ID 冲突
    
    如果插件 ID 已存在（在 plugins 或 plugin_hosts 中），
    生成一个新的唯一 ID（添加数字后缀）并记录警告。
    如果两个插件的内容哈希值相同，会记录更详细的日志。
    
    Args:
        plugin_id: 原始插件 ID
        logger: 日志记录器
        config_path: 当前插件的配置文件路径（可选，用于哈希计算）
        entry_point: 当前插件的入口点（可选，用于哈希计算）
        plugin_data: 当前插件的配置数据（可选，用于哈希计算）
    
    Returns:
        解决冲突后的插件 ID（如果无冲突则返回原始 ID，如果是重复加载则返回 None）
    """
    logger = _wrap_logger(logger)
    from plugin.settings import PLUGIN_ENABLE_ID_CONFLICT_CHECK

    _ = entry_point
    _ = plugin_data

    if enable_rename is None:
        enable_rename = bool(PLUGIN_ENABLE_ID_CONFLICT_CHECK)

    purpose_norm = str(purpose).strip().lower() if isinstance(purpose, str) else "load"
    if purpose_norm not in ("load", "register"):
        purpose_norm = "load"

    cur_path: Optional[Path] = None
    if config_path is not None:
        try:
            cur_path = Path(config_path).resolve()
        except (OSError, RuntimeError):
            cur_path = Path(config_path)

    with state.plugins_lock:
        plugins_snapshot = dict(state.plugins)
    with state.plugin_hosts_lock:
        hosts_snapshot = dict(state.plugin_hosts)

    def _resolve_existing_path(v: Any) -> Optional[Path]:
        if v is None:
            return None
        try:
            return Path(v).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            try:
                return Path(v)
            except Exception:
                return None

    def _get_id_ref(pid: str) -> tuple[Optional[Path], Optional[str]]:
        host = hosts_snapshot.get(pid)
        if host is not None:
            hp = _resolve_existing_path(getattr(host, "config_path", None))
            he = getattr(host, "entry_point", None)
            return hp, str(he) if isinstance(he, str) and he else None
        meta = plugins_snapshot.get(pid)
        if isinstance(meta, dict):
            mp = _resolve_existing_path(meta.get("config_path"))
            me = meta.get("entry_point")
            return mp, str(me) if isinstance(me, str) and me else None
        return None, None

    def _find_id_by_path(p: Path) -> Optional[str]:
        for pid, host in hosts_snapshot.items():
            hp = _resolve_existing_path(getattr(host, "config_path", None))
            if hp is not None and hp == p:
                return str(pid)
        for pid, meta in plugins_snapshot.items():
            if not isinstance(meta, dict):
                continue
            mp = _resolve_existing_path(meta.get("config_path"))
            if mp is not None and mp == p:
                return str(pid)
        return None

    if cur_path is not None:
        existing_by_path = _find_id_by_path(cur_path)
        if isinstance(existing_by_path, str) and existing_by_path:
            if existing_by_path == str(plugin_id):
                if purpose_norm == "load" and str(plugin_id) in hosts_snapshot:
                    return None
                return str(plugin_id)
            return None

    desired = str(plugin_id)
    if desired not in plugins_snapshot and desired not in hosts_snapshot:
        return desired

    existing_path, _existing_entry = _get_id_ref(desired)
    if cur_path is not None and existing_path is not None and cur_path == existing_path:
        if purpose_norm == "load" and desired in hosts_snapshot:
            return None
        return desired

    if purpose_norm == "register" and desired in hosts_snapshot and desired not in plugins_snapshot:
        return desired

    if not bool(enable_rename):
        return desired

    counter = 1
    new_id = f"{desired}_{counter}"
    while new_id in plugins_snapshot or new_id in hosts_snapshot:
        counter += 1
        new_id = f"{desired}_{counter}"
    logger.warning(
        "Plugin ID conflict detected: '{}' is already taken by a different plugin. Renaming to '{}'",
        desired,
        new_id,
    )
    return new_id


def register_plugin(
    plugin: PluginMeta,
    logger: Optional[Any] = None,  # loguru.Logger or logging.Logger
    config_path: Optional[Path] = None,
    entry_point: Optional[str] = None
) -> Optional[str]:
    """
    注册插件到注册表
    
    Args:
        plugin: 插件元数据
        logger: 日志记录器（可选，用于冲突检测）
        config_path: 插件配置文件路径（可选，用于哈希计算）
        entry_point: 插件入口点（可选，用于哈希计算）
    
    Returns:
        实际注册的插件 ID（如果发生冲突，返回重命名后的 ID）
    """
    logger_ = cast(Any, _wrap_logger(logger or loguru_logger))
    
    # 准备插件数据用于哈希计算
    plugin_data = {
        "id": plugin.id,
        "name": plugin.name,
        "version": plugin.version,
        "entry": entry_point or "",
    }
    
    from plugin.settings import PLUGIN_ENABLE_ID_CONFLICT_CHECK

    # 检测并解决 ID 冲突
    resolved_id = _resolve_plugin_id_conflict(
        plugin.id,
        logger_,
        config_path=config_path,
        entry_point=entry_point,
        plugin_data=plugin_data,
        purpose="register",
        enable_rename=bool(PLUGIN_ENABLE_ID_CONFLICT_CHECK),
    )
    
    # 如果返回 None，说明是重复加载，不应该注册
    if resolved_id is None:
        logger_.warning(
            "Plugin {} is already loaded (duplicate detected), skipping registration",
            plugin.id
        )
        # 返回 None 作为特殊标记，表示这是重复加载
        return None
    
    # 如果 ID 被重命名，更新插件元数据
    if resolved_id != plugin.id:
        plugin = PluginMeta(
            id=resolved_id,
            name=plugin.name,
            description=plugin.description,
            version=plugin.version,
            sdk_version=plugin.sdk_version,
            sdk_recommended=plugin.sdk_recommended,
            sdk_supported=plugin.sdk_supported,
            sdk_untested=plugin.sdk_untested,
            sdk_conflicts=plugin.sdk_conflicts,
            input_schema=plugin.input_schema,
            author=plugin.author,
            dependencies=plugin.dependencies,
        )
    
    with state.plugins_lock:
        plugin_dump = plugin.model_dump()
        if config_path is not None:
            plugin_dump["config_path"] = str(config_path)
        if entry_point is not None:
            plugin_dump["entry_point"] = entry_point
        state.plugins[resolved_id] = plugin_dump
    
    return resolved_id


def scan_static_metadata(pid: str, cls: type, conf: dict, pdata: dict) -> None:
    """
    在不实例化的情况下扫描类属性，提取 @EventHandler 元数据并填充全局表。
    """
    logger = loguru_logger
    for name, member in inspect.getmembers(cls):
        event_meta = getattr(member, EVENT_META_ATTR, None)
        if event_meta is None and hasattr(member, "__wrapped__"):
            event_meta = getattr(member.__wrapped__, EVENT_META_ATTR, None)

        if event_meta:
            etype = getattr(event_meta, "event_type", None) or "plugin_entry"
            eid = getattr(event_meta, "id", name)
            handler_obj = EventHandler(meta=event_meta, handler=member)
            with state.event_handlers_lock:
                if etype == "plugin_entry":
                    state.event_handlers[f"{pid}.{eid}"] = handler_obj
                    state.event_handlers[f"{pid}:plugin_entry:{eid}"] = handler_obj
                else:
                    state.event_handlers[f"{pid}:{etype}:{eid}"] = handler_obj
            if etype == "plugin_entry":
                plugin_entry_method_map[(pid, str(eid))] = name

    entries = conf.get("entries") or pdata.get("entries") or []
    for ent in entries:
        try:
            eid = ent.get("id") if isinstance(ent, dict) else str(ent)
            if not eid:
                continue
            try:
                handler_fn = getattr(cls, eid)
            except AttributeError:
                logger.warning(
                    "Entry id {} for plugin {} has no handler on class {}, skipping",
                    eid,
                    pid,
                    cls.__name__,
                )
                continue
            entry_meta = SimpleEntryMeta(
                id=eid,
                name=ent.get("name", "") if isinstance(ent, dict) else "",
                description=ent.get("description", "") if isinstance(ent, dict) else "",
                input_schema=ent.get("input_schema", {}) if isinstance(ent, dict) else {},
            )
            eh = EventHandler(meta=cast(Any, entry_meta), handler=handler_fn)
            with state.event_handlers_lock:
                state.event_handlers[f"{pid}.{eid}"] = eh
                state.event_handlers[f"{pid}:plugin_entry:{eid}"] = eh
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning("Error parsing entry {} for plugin {}: {}", ent, pid, e, exc_info=True)
            # 继续处理其他条目，不中断整个插件加载


def _extract_entries_preview(pid: str, cls: type, conf: dict, pdata: dict) -> List[Dict[str, Any]]:
    """Extract entry metadata for UI visibility without registering event handlers.

    NOTE: This function must not touch state.event_handlers. It is intended for disabled
    plugins (visibility only) so that UI can still display entries.
    """
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _to_dict(v: Any) -> Dict[str, Any]:
        if isinstance(v, dict):
            return v
        try:
            if hasattr(v, "model_dump"):
                d = v.model_dump()
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
        return {}

    # 1) Decorator-based metadata (@plugin_entry / EVENT_META_ATTR)
    try:
        for name, member in inspect.getmembers(cls):
            event_meta = getattr(member, EVENT_META_ATTR, None)
            if event_meta is None and hasattr(member, "__wrapped__"):
                event_meta = getattr(member.__wrapped__, EVENT_META_ATTR, None)

            if not event_meta:
                continue
            etype = getattr(event_meta, "event_type", None) or "plugin_entry"
            if etype != "plugin_entry":
                continue

            eid = str(getattr(event_meta, "id", None) or name)
            if not eid or eid in seen:
                continue
            seen.add(eid)

            input_schema = _to_dict(getattr(event_meta, "input_schema", {}) or {})
            results.append(
                {
                    "id": eid,
                    "name": str(getattr(event_meta, "name", "") or ""),
                    "description": str(getattr(event_meta, "description", "") or ""),
                    "event_key": f"{pid}.{eid}",
                    "input_schema": input_schema,
                    "return_message": str(getattr(event_meta, "return_message", "") or ""),
                }
            )
    except Exception:
        # Best-effort: preview must never break plugin listing.
        pass

    # 2) Config-specified entries (conf/pdata)
    entries = conf.get("entries") or pdata.get("entries") or []
    for ent in entries:
        try:
            if isinstance(ent, dict):
                eid = str(ent.get("id") or "")
                if not eid or eid in seen:
                    continue
                seen.add(eid)
                results.append(
                    {
                        "id": eid,
                        "name": str(ent.get("name") or ""),
                        "description": str(ent.get("description") or ""),
                        "event_key": f"{pid}.{eid}",
                        "input_schema": _to_dict(ent.get("input_schema") or {}),
                        "return_message": "",
                    }
                )
            else:
                eid = str(ent)
                if not eid or eid in seen:
                    continue
                seen.add(eid)
                results.append(
                    {
                        "id": eid,
                        "name": "",
                        "description": "",
                        "event_key": f"{pid}.{eid}",
                        "input_schema": {},
                        "return_message": "",
                    }
                )
        except Exception:
            continue

    return results


def load_plugins_from_toml(
    plugin_config_root: Path,
    logger: Any,
    process_host_factory: Callable[[str, str, Path], Any],
) -> None:
    """
    扫描插件配置，启动子进程，并静态扫描元数据用于注册列表。
    process_host_factory 接收 (plugin_id, entry_point, config_path) 并返回宿主对象。
    
    加载过程分为三个阶段：
    1. 收集（Collect）：扫描所有 TOML 文件，解析配置和依赖。
    2. 排序（Sort）：根据插件依赖关系进行拓扑排序，确保依赖先加载。
    3. 加载（Load）：按顺序执行实际加载。
    """
    logger = _wrap_logger(logger)
    if not plugin_config_root.exists():
        logger.info("No plugin config directory {}, skipping", plugin_config_root)
        return

    logger.info("Loading plugins from {}", plugin_config_root)
    
    # 设置 Python 路径，确保能够导入插件模块
    # 获取项目根目录（假设 plugin_config_root 在 plugin/plugins）
    project_root = plugin_config_root.parent.parent.resolve()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        logger.info("Added project root to sys.path: {}", project_root)
    logger.info("Current working directory: {}", os.getcwd())
    logger.info("Python path (first 3): {}", sys.path[:3])
    
    found_toml_files = list(plugin_config_root.glob("*/plugin.toml"))
    logger.info("Found {} plugin.toml files: {}", len(found_toml_files), [str(p) for p in found_toml_files])
    
    # === Phase 1: Collect and Parse ===
    plugin_contexts = []
    processed_paths = set()
    # 临时映射：pid -> context，用于后续构建依赖图
    pid_to_context = {}
    
    for toml_path in found_toml_files:
        logger.info("Processing plugin config: {}", toml_path)
        try:
            with toml_path.open("rb") as f:
                conf = tomllib.load(f)
            pdata = conf.get("plugin") or {}
            pid = pdata.get("id")
            if not pid:
                logger.warning("Plugin config {} has no 'id' field, skipping", toml_path)
                continue

            # Apply user profile overlay (including [plugin_runtime]) before making runtime decisions.
            try:
                from plugin.server.config_service import _apply_user_config_profiles

                if isinstance(conf, dict):
                    conf = _apply_user_config_profiles(
                        plugin_id=str(pid),
                        base_config=conf,
                        config_path=toml_path,
                    )
            except Exception as e:
                logger.warning(
                    "Plugin {}: failed to apply user config profile overlay: {}. Using base config only.",
                    pid,
                    e,
                )

            logger.info("Plugin ID: {}", pid)
            
            # 检查配置文件路径是否已经被处理过（检测重复扫描）
            try:
                resolved_path = toml_path.resolve()
                if str(resolved_path) in processed_paths:
                    logger.warning(
                        "Plugin config file {} has already been processed in this scan, skipping duplicate",
                        toml_path
                    )
                    continue
                processed_paths.add(str(resolved_path))
            except (OSError, RuntimeError) as e:
                logger.debug("Failed to resolve path for duplicate check: {}", e)
            
            entry = pdata.get("entry")
            if not entry or ":" not in entry:
                logger.warning("Plugin {} has invalid entry point '{}', skipping", pid, entry)
                continue
            
            logger.info("Plugin {} entry point: {}", pid, entry)

            # 在主加载流程中：
            # - enabled = false 的插件：跳过本次运行时加载流程。
            # - enabled = true 且 auto_start = false 的插件：仍然参与解析/注册（保证前端可见），但不在本次启动进程。
            runtime_cfg = conf.get("plugin_runtime")
            enabled_val = True
            auto_start_val = True
            if isinstance(runtime_cfg, dict):
                v_enabled = runtime_cfg.get("enabled")
                v_auto = runtime_cfg.get("auto_start")

                if isinstance(v_enabled, bool):
                    enabled_val = v_enabled
                elif isinstance(v_enabled, str):
                    s = v_enabled.strip().lower()
                    if s in ("0", "false", "no", "off"):
                        enabled_val = False
                    elif s in ("1", "true", "yes", "on"):
                        enabled_val = True

                if isinstance(v_auto, bool):
                    auto_start_val = v_auto
                elif isinstance(v_auto, str):
                    s = v_auto.strip().lower()
                    if s in ("0", "false", "no", "off"):
                        auto_start_val = False
                    elif s in ("1", "true", "yes", "on"):
                        auto_start_val = True

            # enabled=false: still collect for visibility, but do not participate in runtime load.
            if not enabled_val:
                logger.info(
                    "Plugin {} is disabled by plugin_runtime.enabled=false; will register for visibility only (no runtime load)",
                    pid,
                )

            if not auto_start_val:
                logger.info(
                    "Plugin {} has plugin_runtime.auto_start=false; treating as manual-start-only (will register metadata but skip auto process start)",
                    pid,
                )

            sdk_config = pdata.get("sdk")
            sdk_supported_str = None
            sdk_recommended_str = None
            sdk_untested_str = None
            sdk_conflicts_list: List[str] = []

            # Parse SDK version requirements from [plugin.sdk] block
            if isinstance(sdk_config, dict):
                sdk_recommended_str = sdk_config.get("recommended")
                sdk_supported_str = sdk_config.get("supported") or sdk_config.get("compatible")
                sdk_untested_str = sdk_config.get("untested")
                raw_conflicts = sdk_config.get("conflicts") or []
                if isinstance(raw_conflicts, list):
                    sdk_conflicts_list = [str(c) for c in raw_conflicts if c]
                elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
                    sdk_conflicts_list = [raw_conflicts.strip()]
            elif sdk_config is not None:
                # SDK configuration must be a dict (plugin.sdk block) if present
                logger.error(
                    "Plugin {}: SDK configuration must be a dict (plugin.sdk block), got {}; skipping load",
                    pid,
                    type(sdk_config).__name__
                )
                continue

            # SDK Version Checks
            host_version_obj: Optional[Any] = None
            if Version and SpecifierSet:
                try:
                    host_version_obj = Version(SDK_VERSION)
                except InvalidVersion as e:
                    logger.error("Invalid host SDK_VERSION {}: {}", SDK_VERSION, e)
                    host_version_obj = None

            # Validate against ranges when possible
            if host_version_obj:
                supported_spec = _parse_specifier(sdk_supported_str, logger)
                recommended_spec = _parse_specifier(sdk_recommended_str, logger)
                untested_spec = _parse_specifier(sdk_untested_str, logger)
                conflict_specs = [
                    _parse_specifier(conf, logger) for conf in sdk_conflicts_list
                ]

                if sdk_supported_str and supported_spec is None:
                    logger.error(
                        "Plugin {}: invalid supported SDK spec '{}'; skipping load",
                        pid,
                        sdk_supported_str,
                    )
                    continue
                if sdk_untested_str and untested_spec is None:
                    logger.error(
                        "Plugin {}: invalid untested SDK spec '{}'; skipping load",
                        pid,
                        sdk_untested_str,
                    )
                    continue
                invalid_conflicts = [
                    c for c, s in zip(sdk_conflicts_list, conflict_specs) if c and s is None
                ]
                if invalid_conflicts:
                    logger.error(
                        "Plugin {}: invalid conflict SDK spec(s) {}; skipping load",
                        pid,
                        invalid_conflicts,
                    )
                    continue

                # Conflict check
                if any(spec and _version_matches(spec, host_version_obj) for spec in conflict_specs):
                    logger.error(
                        "Plugin {} conflicts with host SDK {} (conflict ranges: {}); skipping load",
                        pid, SDK_VERSION, sdk_conflicts_list
                    )
                    continue

                # Compatibility check
                in_supported = _version_matches(supported_spec, host_version_obj)
                in_untested = _version_matches(untested_spec, host_version_obj)

                if supported_spec and not (in_supported or in_untested):
                    logger.error(
                        "Plugin {} requires SDK in {} (or untested {}) but host SDK is {}; skipping load",
                        pid, sdk_supported_str, sdk_untested_str, SDK_VERSION
                    )
                    continue
                
                # Warnings
                if recommended_spec and not _version_matches(recommended_spec, host_version_obj):
                    logger.warning("Plugin {}: host SDK {} is outside recommended range {}", pid, SDK_VERSION, sdk_recommended_str)
                if in_untested and not in_supported:
                    logger.warning("Plugin {}: host SDK {} is within untested range {}; proceed with caution", pid, SDK_VERSION, sdk_untested_str)
            else:
                # Fallback string comparison
                if sdk_supported_str and sdk_supported_str != SDK_VERSION:
                    logger.error("Plugin {} requires sdk_version {} but host SDK is {}; skipping load", pid, sdk_supported_str, SDK_VERSION)
                    continue

            # 解析依赖
            dependencies = _parse_plugin_dependencies(conf, logger, pid)
            
            # 保存上下文
            context = {
                "pid": pid,
                "toml_path": toml_path,
                "conf": conf,
                "pdata": pdata,
                "entry": entry,
                "dependencies": dependencies,
                "sdk_supported_str": sdk_supported_str,
                "sdk_recommended_str": sdk_recommended_str,
                "sdk_untested_str": sdk_untested_str,
                "sdk_conflicts_list": sdk_conflicts_list,
                "enabled": enabled_val,
                "auto_start": auto_start_val,
            }
            plugin_contexts.append(context)
            pid_to_context[pid] = context
            
        except (tomllib.TOMLDecodeError, OSError) as e:
            logger.error("Failed to parse plugin config {}: {}", toml_path, e)
            continue
        except Exception:
            logger.exception("Unexpected error processing config {}", toml_path)
            continue

    # === Phase 2: Topological Sort ===
    logger.info("Sorting {} plugins based on dependencies...", len(plugin_contexts))
    
    # 构建图：pid -> set(dependency_pids)
    graph: Dict[str, set] = {ctx["pid"]: set() for ctx in plugin_contexts}

    def _candidate_dependency_pids(dep: PluginDependency) -> List[str]:
        if getattr(dep, "conflicts", None) is True:
            return []
        out: List[str] = []
        if getattr(dep, "id", None):
            out.append(str(dep.id))
        providers = getattr(dep, "providers", None)
        if isinstance(providers, list):
            for p in providers:
                if p:
                    out.append(str(p))
        entry = getattr(dep, "entry", None)
        if isinstance(entry, str):
            if ":" in entry:
                try:
                    pid_part, _rest = entry.split(":", 1)
                    if pid_part:
                        out.append(pid_part)
                except Exception:
                    logger.debug("Failed to parse dependency entry spec '{}'", entry, exc_info=True)
            else:
                # entry 也可能直接引用插件 ID（某些配置/文档会这么写）
                if entry:
                    out.append(entry)
        custom_event = getattr(dep, "custom_event", None)
        if isinstance(custom_event, str):
            try:
                # 支持：plugin_id:event_type:event_id
                # 其中 event_id 可能包含额外 ':'，因此只切前两次。
                parts = custom_event.split(":", 2)
                if len(parts) >= 3 and parts[0]:
                    out.append(parts[0])
            except Exception:
                logger.debug("Failed to parse dependency custom_event spec '{}'", custom_event, exc_info=True)
        return out
    
    for ctx in plugin_contexts:
        pid = ctx["pid"]
        for dep in ctx["dependencies"]:
            for dep_pid in _candidate_dependency_pids(dep):
                # 只有当依赖的插件也在本次加载列表中时，才添加边
                # 如果依赖是外部已加载的插件，不影响本次排序顺序
                if dep_pid in pid_to_context:
                    graph[pid].add(dep_pid)
                    logger.debug("Dependency edge: {} -> {}", pid, dep_pid)
    
    # 重新构建图以便于 Kahn 算法：Node = Plugin, Edge = Dependency -> Dependent
    # 即：如果 A 依赖 B，则有一条边 B -> A (B 必须先完成)
    adj_list: Dict[str, List[str]] = {pid: [] for pid in pid_to_context}
    in_degree = {pid: 0 for pid in pid_to_context}
    
    for ctx in plugin_contexts:
        dependent = ctx["pid"]
        for dep in ctx["dependencies"]:
            for dependency in _candidate_dependency_pids(dep):
                if dependency in pid_to_context:
                    # Dependency -> Dependent
                    adj_list[dependency].append(dependent)
                    in_degree[dependent] += 1
    
    # 队列中放入所有入度为 0 的节点（无依赖或依赖已满足）
    queue = [pid for pid in pid_to_context if in_degree[pid] == 0]
    # 为了保持确定性：同一层里优先加载 enabled 插件，避免 disabled 的可见性注册抢占 ID。
    queue.sort(key=lambda x: (0 if pid_to_context.get(x, {}).get("enabled", True) else 1, str(x)))
    
    final_order = []
    while queue:
        u = queue.pop(0)
        final_order.append(u)
        
        for v in adj_list[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
        # 保持队列有序：同一层里优先 enabled
        queue.sort(key=lambda x: (0 if pid_to_context.get(x, {}).get("enabled", True) else 1, str(x)))
    
    # 检查是否有循环依赖
    if len(final_order) != len(plugin_contexts):
        loaded_set = set(final_order)
        missing = [ctx["pid"] for ctx in plugin_contexts if ctx["pid"] not in loaded_set]
        cycle_info = []
        try:
            for pid in missing:
                deps = [d for d in graph.get(pid, set()) if d in missing]
                if deps:
                    cycle_info.append(f"{pid} -> {deps}")
        except Exception:
            cycle_info = []
        logger.error(
            "Circular dependency detected or failed sort! Missing plugins: {}. Dependency chains: {}. "
            "These plugins will be loaded in undefined order and may fail.",
            missing,
            cycle_info,
        )
        # 这种情况下，我们将未排序的插件追加到后面，尝试尽力加载
        final_order.extend(missing)
    
    logger.info("Plugin load order: {}", final_order)
    
    # === Phase 3: Load ===
    for pid in final_order:
        context = pid_to_context.get(pid)
        if not context:
            continue
            
        toml_path = context["toml_path"]
        conf = context["conf"]
        pdata = context["pdata"]
        entry = context["entry"]
        dependencies = context["dependencies"]
        sdk_supported_str = context["sdk_supported_str"]
        sdk_recommended_str = context["sdk_recommended_str"]
        sdk_untested_str = context["sdk_untested_str"]
        sdk_conflicts_list = context["sdk_conflicts_list"]
        
        logger.info("Loading plugin: {}", pid)

        enabled_val = context.get("enabled", True)
        auto_start_val = context.get("auto_start", True)

        # disabled plugins: visibility only. Do not load runtime, do not scan event handlers,
        # and must not participate in dependency/conflict evaluation.
        if not enabled_val:
            # Safer default: do not import disabled plugin code (module import can run top-level side effects).
            entries_preview = _extract_entries_preview(
                pid,
                cls=type("DisabledPluginStub", (), {}),
                conf=conf,
                pdata=pdata,
            )

            author_data = pdata.get("author")
            author = None
            if author_data and isinstance(author_data, dict):
                author = PluginAuthor(
                    name=author_data.get("name"),
                    email=author_data.get("email")
                )

            plugin_meta = PluginMeta(
                id=pid,
                name=pdata.get("name", pid),
                description=pdata.get("description", ""),
                version=pdata.get("version", "0.1.0"),
                sdk_version=sdk_supported_str or SDK_VERSION,
                sdk_recommended=sdk_recommended_str,
                sdk_supported=sdk_supported_str,
                sdk_untested=sdk_untested_str,
                sdk_conflicts=sdk_conflicts_list,
                input_schema={"type": "object", "properties": {}},
                author=author,
                dependencies=dependencies,
            )
            resolved_id = register_plugin(
                plugin_meta,
                logger,
                config_path=toml_path,
                entry_point=entry,
            )
            if resolved_id is not None:
                with state.plugins_lock:
                    meta = state.plugins.get(resolved_id)
                    if isinstance(meta, dict):
                        meta["runtime_enabled"] = False
                        meta["runtime_auto_start"] = False
                        meta["entries_preview"] = entries_preview
                        state.plugins[resolved_id] = meta
            continue

        # 依赖检查（可通过配置禁用）
        from plugin.settings import PLUGIN_ENABLE_DEPENDENCY_CHECK
        dependency_check_failed = False
        if PLUGIN_ENABLE_DEPENDENCY_CHECK and dependencies:
            logger.info("Plugin {}: found {} dependency(ies), checking...", pid, len(dependencies))
            for dep in dependencies:
                # 检查依赖（包括简化格式和完整格式）
                satisfied, error_msg = _check_plugin_dependency(dep, logger, pid)
                if not satisfied:
                    logger.error(
                        "Plugin {}: dependency check failed: {}; skipping load",
                        pid, error_msg
                    )
                    dependency_check_failed = True
                    break
                logger.debug("Plugin {}: dependency '{}' check passed", pid, getattr(dep, 'id', getattr(dep, 'entry', getattr(dep, 'custom_event', 'unknown'))))
            if not dependency_check_failed:
                logger.info("Plugin {}: all dependencies satisfied", pid)
        elif not PLUGIN_ENABLE_DEPENDENCY_CHECK and dependencies:
            logger.warning(
                "Plugin {}: has {} dependency(ies), but dependency check is disabled. "
                "Loading plugin without dependency validation.",
                pid, len(dependencies)
            )
        else:
            logger.debug("Plugin {}: no dependencies to check", pid)
        
        if dependency_check_failed:
            logger.info("Plugin {}: skipping due to failed dependency check", pid)
            continue

        # 检查插件是否已经加载（通过检查 config_path 是否相同）
        # 如果同一个配置文件已经被加载，直接跳过
        with state.plugin_hosts_lock:
            if pid in state.plugin_hosts:
                existing_host = state.plugin_hosts[pid]
                existing_config_path = getattr(existing_host, 'config_path', None)
                if existing_config_path:
                    try:
                        # 规范化路径进行比较
                        existing_resolved = Path(existing_config_path).resolve()
                        current_resolved = toml_path.resolve()
                        if existing_resolved == current_resolved:
                            logger.warning(
                                "Plugin {} from {} is already loaded (same config path), skipping duplicate load",
                                pid, toml_path
                            )
                            continue
                    except (OSError, RuntimeError):
                        # 如果路径解析失败，使用字符串比较
                        if str(existing_config_path) == str(toml_path):
                            logger.warning(
                                "Plugin {} from {} is already loaded (same config path), skipping duplicate load",
                                pid, toml_path
                            )
                            continue
        
        # 检测并解决插件 ID 冲突（在创建 host 之前，依赖检查之后）
        # 构建用于哈希计算的 plugin_data（与 register_plugin 中的格式一致）
        plugin_data_for_hash = {
            "id": pid,
            "name": pdata.get("name", pid),
            "version": pdata.get("version", "0.1.0"),
            "entry": entry or "",
        }
        
        original_pid = pid
        
        from plugin.settings import PLUGIN_ENABLE_ID_CONFLICT_CHECK

        resolved_pid = _resolve_plugin_id_conflict(
            pid,
            logger,
            config_path=toml_path,
            entry_point=entry,
            plugin_data=plugin_data_for_hash,
            purpose="load",
            enable_rename=bool(PLUGIN_ENABLE_ID_CONFLICT_CHECK),
        )

        if resolved_pid is None:
            logger.info(
                "Plugin {} from {} is already loaded (duplicate detected), skipping",
                original_pid,
                toml_path,
            )
            continue

        pid = resolved_pid
        if pid != original_pid:
            logger.warning(
                "Plugin {} from {}: ID changed from '{}' to '{}' due to conflict",
                original_pid, toml_path, original_pid, pid
            )

        # 在创建 host 之前，先检查插件是否已注册
        # 如果已注册，检查是否已有运行的 host
        with state.plugins_lock:
            plugin_already_registered = pid in state.plugins
        
        if plugin_already_registered:
            with state.plugin_hosts_lock:
                if pid in state.plugin_hosts:
                    existing_host = state.plugin_hosts[pid]
                    if hasattr(existing_host, 'is_alive') and existing_host.is_alive():
                        logger.info(
                            "Plugin {} from {} is already registered and running, skipping duplicate load",
                            pid, toml_path
                        )
                        continue
                    else:
                        logger.info(
                            "Plugin {} from {} is already registered but not running, skipping duplicate load",
                            pid, toml_path
                        )
                        continue
                else:
                    # 已注册但未运行，跳过自动启动（需要手动启动）
                    # 这是关键问题：插件已注册但没有 host，需要手动启动
                    logger.warning(
                        "Plugin {} from {} is already registered in state.plugins but has no host in plugin_hosts. "
                        "This indicates the plugin was registered but the host creation was skipped or failed. "
                        "Please start the plugin manually via POST /plugin/{}/start",
                        pid, toml_path, pid
                    )
                    continue

        module_path, class_name = entry.split(":", 1)
        logger.info("Plugin {}: importing module '{}', class '{}'", pid, module_path, class_name)
        try:
            mod = importlib.import_module(module_path)
            logger.info("Plugin {}: module '{}' imported successfully", pid, module_path)
            cls: Type[Any] = getattr(mod, class_name)
            logger.info("Plugin {}: class '{}' found in module", pid, class_name)
        except (ImportError, ModuleNotFoundError) as e:
            logger.error("Failed to import module '{}' for plugin {}: {}", module_path, pid, e, exc_info=True)
            continue
        except AttributeError as e:
            logger.error("Class '{}' not found in module '{}' for plugin {}: {}", class_name, module_path, pid, e, exc_info=True)
            continue
        except Exception:
            logger.exception("Unexpected error importing plugin class {} for plugin {}", entry, pid)
            continue

        host = None
        if enabled_val and auto_start_val:
            try:
                logger.info("Plugin {}: creating process host...", pid)
                host = process_host_factory(pid, entry, toml_path)
                logger.info(
                    "Plugin {}: process host created successfully (pid: {}, alive: {})",
                    pid,
                    getattr(host.process, 'pid', 'N/A') if hasattr(host, 'process') and host.process else 'N/A',
                    host.process.is_alive() if hasattr(host, 'process') and host.process else False
                )
                
                # 如果 ID 被重命名，更新 host 的 plugin_id（如果支持）
                if pid != original_pid and hasattr(host, 'plugin_id'):
                    host.plugin_id = pid
                    logger.debug("Updated host plugin_id to '{}'", pid)
                
                skip_register = False
                with state.plugin_hosts_lock:
                    # 检查是否已经存在（防止重复注册）
                    if pid in state.plugin_hosts:
                        existing_host = state.plugin_hosts[pid]
                        existing_config = getattr(existing_host, 'config_path', None)
                        if existing_config:
                            try:
                                if Path(existing_config).resolve() == toml_path.resolve():
                                    logger.warning(
                                        "Plugin {} from {} is already registered in plugin_hosts, skipping duplicate registration",
                                        pid, toml_path
                                    )
                                    skip_register = True
                            except (OSError, RuntimeError):
                                pass

                    if not skip_register:
                        # 注册 host
                        state.plugin_hosts[pid] = host
                        # 立即验证注册是否成功
                        registered_keys = list(state.plugin_hosts.keys())
                        logger.info(
                            "Plugin {}: registered in plugin_hosts. Current plugin_hosts keys: {}",
                            pid, registered_keys
                        )
                        # 在同一个锁内验证 host 是否还在（防止在注册后立即被其他代码移除）
                        if pid not in state.plugin_hosts:
                            logger.error(
                                "Plugin {} host was removed from plugin_hosts immediately after registration! "
                                "This should not happen. Current plugin_hosts keys: {}. "
                                "Re-registering host to continue...",
                                pid, list(state.plugin_hosts.keys())
                            )
                            # 重新注册 host（可能是被意外清空了）
                            state.plugin_hosts[pid] = host
                            logger.info("Plugin {}: re-registered in plugin_hosts", pid)

                if skip_register:
                    try:
                        if hasattr(host, "shutdown_sync"):
                            host.shutdown_sync(timeout=1.0)
                        elif hasattr(host, "shutdown"):
                            import asyncio

                            if asyncio.iscoroutinefunction(host.shutdown):
                                try:
                                    loop = asyncio.get_running_loop()
                                    task = loop.create_task(host.shutdown(timeout=1.0))
                                    _pending_async_shutdown_tasks.add(task)

                                    def _on_done(t: asyncio.Task) -> None:
                                        _pending_async_shutdown_tasks.discard(t)
                                        try:
                                            _ = t.exception()
                                        except asyncio.CancelledError:
                                            pass
                                        except Exception:
                                            pass

                                    task.add_done_callback(_on_done)
                                except RuntimeError:
                                    asyncio.run(host.shutdown(timeout=1.0))
                            else:
                                host.shutdown(timeout=1.0)
                        elif hasattr(host, "process") and getattr(host, "process", None):
                            host.process.terminate()
                            host.process.join(timeout=1.0)
                    except Exception:
                        logger.debug("Plugin {}: failed to cleanup duplicate-created host", pid, exc_info=True)
                    continue
            except (OSError, RuntimeError) as e:
                logger.error("Failed to start process for plugin {}: {}", pid, e, exc_info=True)
                continue
            except Exception:
                logger.exception("Unexpected error starting process for plugin {}", pid)
                continue

        scan_static_metadata(pid, cls, conf, pdata)

        # 读取作者信息
        author_data = pdata.get("author")
        author = None
        if author_data and isinstance(author_data, dict):
            author = PluginAuthor(
                name=author_data.get("name"),
                email=author_data.get("email")
            )

        plugin_meta = PluginMeta(
            id=pid,
            name=pdata.get("name", pid),
            description=pdata.get("description", ""),
            version=pdata.get("version", "0.1.0"),
            sdk_version=sdk_supported_str or SDK_VERSION,
            sdk_recommended=sdk_recommended_str,
            sdk_supported=sdk_supported_str,
            sdk_untested=sdk_untested_str,
            sdk_conflicts=sdk_conflicts_list,
            input_schema=getattr(cls, "input_schema", {}) or {"type": "object", "properties": {}},
            author=author,
            dependencies=dependencies,
        )
        
        # 在调用 register_plugin 之前，验证 host 是否还在 plugin_hosts 中。
        # 对于 manual-start-only 插件（auto_start=false），host 允许为 None，此时不应要求在 plugin_hosts 中存在。
        host_still_exists = False
        if host is not None:
            with state.plugin_hosts_lock:
                host_still_exists = pid in state.plugin_hosts
                if not host_still_exists:
                    logger.error(
                        "Plugin {} host was removed from plugin_hosts before register_plugin call! "
                        "This should not happen. Current plugin_hosts keys: {}",
                        pid, list(state.plugin_hosts.keys())
                    )
        
        resolved_id = register_plugin(
            plugin_meta,
            logger,
            config_path=toml_path,
            entry_point=entry
        )

        # Mark runtime flags for dependency/conflict filtering.
        if resolved_id is not None:
            with state.plugins_lock:
                meta = state.plugins.get(resolved_id)
                if isinstance(meta, dict):
                    meta["runtime_enabled"] = True
                    meta["runtime_auto_start"] = bool(auto_start_val)
                    state.plugins[resolved_id] = meta
        
        logger.debug(
            "Plugin {}: register_plugin returned resolved_id={}, original pid={}",
            pid, resolved_id, pid
        )
        
        # 验证 register_plugin 调用后 host 是否还在
        if host is not None:
            with state.plugin_hosts_lock:
                host_after_register = pid in state.plugin_hosts
                all_keys_after = list(state.plugin_hosts.keys())
                if host_still_exists and not host_after_register:
                    logger.error(
                        "Plugin {} host was removed from plugin_hosts during register_plugin call! "
                        "resolved_id={}, host_still_exists={}, host_after_register={}, "
                        "Current plugin_hosts keys: {}",
                        pid, resolved_id, host_still_exists, host_after_register, all_keys_after
                    )
                elif host_still_exists and host_after_register:
                    logger.debug(
                        "Plugin {} host still exists in plugin_hosts after register_plugin (resolved_id={})",
                        pid, resolved_id
                    )
        
        # 如果 register_plugin 返回 None 或原始 ID 但检测到重复，说明这是重复加载
        # 需要移除刚注册的 host 和清理资源
        if resolved_id is None:
            logger.warning(
                "Plugin {} from {} detected as duplicate in register_plugin, removing from plugin_hosts",
                pid, toml_path
            )
            existing_host = None
            # 移除刚注册的 host
            with state.plugin_hosts_lock:
                if pid in state.plugin_hosts:
                    existing_host = state.plugin_hosts.pop(pid)

            # 尝试关闭进程
            if existing_host is not None:
                try:
                    if hasattr(existing_host, 'shutdown'):
                        import asyncio
                        # 如果是异步的，需要处理
                        if asyncio.iscoroutinefunction(existing_host.shutdown):
                            try:
                                loop = asyncio.get_running_loop()
                                task = loop.create_task(existing_host.shutdown(timeout=1.0))
                                _pending_async_shutdown_tasks.add(task)

                                def _on_done(t: asyncio.Task) -> None:
                                    _pending_async_shutdown_tasks.discard(t)
                                    try:
                                        _ = t.exception()
                                    except asyncio.CancelledError:
                                        pass
                                    except Exception:
                                        pass

                                task.add_done_callback(_on_done)
                                logger.debug("Plugin {} scheduled async shutdown", pid)
                            except RuntimeError:
                                asyncio.run(existing_host.shutdown(timeout=1.0))
                        else:
                            existing_host.shutdown(timeout=1.0)
                    elif hasattr(existing_host, 'process') and existing_host.process:
                        existing_host.process.terminate()
                        existing_host.process.join(timeout=1.0)
                except Exception as e:
                    logger.debug("Error shutting down duplicate plugin {}: {}", pid, e)
            logger.info("Plugin {} removed from plugin_hosts due to duplicate detection", pid)
            continue
        
        if resolved_id != pid:
            old_pid = pid
            # 如果 ID 被进一步重命名（双重冲突），需要更新 plugin_hosts 中的键
            logger.warning(
                "Plugin ID changed during registration from '{}' to '{}', updating plugin_hosts",
                pid, resolved_id
            )
            # 更新 plugin_hosts 中的键
            with state.plugin_hosts_lock:
                if pid in state.plugin_hosts:
                    host = state.plugin_hosts.pop(pid)
                    state.plugin_hosts[resolved_id] = host
                    # 更新 host 的 plugin_id（如果可能）
                    if hasattr(host, 'plugin_id'):
                        host.plugin_id = resolved_id
                    logger.info(
                        "Plugin host moved from '{}' to '{}' in plugin_hosts",
                        pid, resolved_id
                    )

            # 迁移 response queue 映射，确保通过新 plugin_id 能找到队列
            with state._plugin_response_queues_lock:
                if old_pid in state._plugin_response_queues:
                    q = state._plugin_response_queues.pop(old_pid)
                    state._plugin_response_queues[resolved_id] = q

            # 迁移 event handler 映射，确保通过新 plugin_id 能找到处理器
            with state.event_handlers_lock:
                handlers_to_migrate = [
                    k
                    for k in list(state.event_handlers.keys())
                    if k.startswith(f"{old_pid}.") or k.startswith(f"{old_pid}:")
                ]
                for old_key in handlers_to_migrate:
                    if old_key.startswith(f"{old_pid}."):
                        new_key = old_key.replace(f"{old_pid}.", f"{resolved_id}.", 1)
                    else:
                        new_key = old_key.replace(f"{old_pid}:", f"{resolved_id}:", 1)
                    state.event_handlers[new_key] = state.event_handlers.pop(old_key)

            # 迁移 plugin_entry_method_map，保持入口调用映射一致
            for (p, eid), method in list(plugin_entry_method_map.items()):
                if p == old_pid:
                    plugin_entry_method_map[(resolved_id, eid)] = method
                    del plugin_entry_method_map[(p, eid)]
            pid = resolved_id

        logger.info("Loaded plugin {} (Process: {})", pid, getattr(host, "process", None))
        try:
            from plugin.server.services import _enqueue_lifecycle
            from plugin.server.infrastructure.utils import now_iso

            _enqueue_lifecycle({"type": "plugin_loaded", "plugin_id": pid, "time": now_iso()})
        except Exception:
            logger.debug("Failed to enqueue lifecycle event for plugin {}", pid, exc_info=True)
