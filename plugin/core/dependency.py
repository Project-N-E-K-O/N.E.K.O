"""
插件依赖检查和拓扑排序模块

从 registry.py 拆分出来，负责：
- 版本规范解析
- 依赖查找（按入口点、自定义事件）
- 依赖检查
- 拓扑排序
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from plugin.core.state import state
from plugin._types.models import PluginDependency
from plugin._types.events import STANDARD_EVENT_TYPES

if TYPE_CHECKING:
    from plugin.core.registry import PluginContext

try:
    from packaging.version import Version, InvalidVersion
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
except ImportError:  # pragma: no cover
    Version = None  # type: ignore
    InvalidVersion = Exception  # type: ignore
    SpecifierSet = None  # type: ignore
    InvalidSpecifier = Exception  # type: ignore


def _wrap_logger(logger: Any) -> Any:
    """向后兼容函数，现在统一返回 loguru logger"""
    return logger


def _parse_specifier(spec: Optional[str], logger: Any) -> Optional[Any]:
    """解析版本规范字符串"""
    logger = _wrap_logger(logger)
    if not spec or SpecifierSet is None:
        return None
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier as e:
        logger.error("Invalid sdk specifier '{}': {}", spec, e)
        return None


def _version_matches(spec: Optional[Any], version: Any) -> bool:
    """检查版本是否匹配规范"""
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
    
    with state.acquire_event_handlers_read_lock():
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
    with state.acquire_plugins_read_lock():
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
    
    with state.acquire_event_handlers_read_lock():
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
                    # 验证不是标准类型
                    if etype not in STANDARD_EVENT_TYPES:
                        found_plugin_ids.add(pid)
    
    # 获取这些插件的元数据
    with state.acquire_plugins_read_lock():
        for pid in found_plugin_ids:
            if pid in state.plugins:
                meta = state.plugins[pid]
                # Disabled plugins are visible but should not participate in dependency satisfaction.
                if isinstance(meta, dict) and meta.get("runtime_enabled") is False:
                    continue
                matching_plugins.append((pid, meta))
    
    return matching_plugins


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
        with state.acquire_plugins_read_lock():
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
        with state.acquire_plugins_read_lock():
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

            with state.acquire_plugins_read_lock():
                if target_plugin_id not in state.plugins:
                    return False, f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' not found"

            # 验证指定插件确实提供该入口（且是 @plugin_entry）
            matching_plugins = _find_plugins_by_entry(target_entry_id)
            if not any(pid == target_plugin_id for pid, _ in matching_plugins):
                return False, (
                    f"Dependency entry '{entry_spec}': plugin '{target_plugin_id}' does not provide entry '{target_entry_id}'"
                )

            with state.acquire_plugins_read_lock():
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
                with state.acquire_plugins_read_lock():
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
        with state.acquire_plugins_read_lock():
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


def _parse_plugin_dependencies(
    conf: Dict[str, Any],
    logger: Any,
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


def _get_dependency_plugin_ids(dep: PluginDependency, logger: Any) -> List[str]:
    """
    从依赖配置中提取可能的插件 ID 列表。
    
    Args:
        dep: 依赖配置
        logger: 日志记录器
    
    Returns:
        可能的依赖插件 ID 列表
    """
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
    
    custom_event = getattr(dep, "custom_event", None)
    if isinstance(custom_event, str):
        try:
            parts = custom_event.split(":", 2)
            if len(parts) >= 3 and parts[0]:
                out.append(parts[0])
        except Exception:
            logger.debug("Failed to parse dependency custom_event spec '{}'", custom_event, exc_info=True)
    
    return out


def _topological_sort_plugins(
    plugin_contexts: List["PluginContext"],
    pid_to_context: Dict[str, "PluginContext"],
    logger: Any,
) -> List[str]:
    """
    Phase 2: 根据依赖关系对插件进行拓扑排序。
    
    Args:
        plugin_contexts: 插件上下文列表
        pid_to_context: pid -> context 映射
        logger: 日志记录器
    
    Returns:
        排序后的插件 ID 列表
    """
    logger.info("Sorting {} plugins based on dependencies...", len(plugin_contexts))
    
    # 构建依赖图
    graph: Dict[str, set] = {ctx.pid: set() for ctx in plugin_contexts}

    # 仅基于当前待加载集合构建 entry provider 映射，避免依赖运行时全局状态。
    # 这里采用配置中声明的 entries（conf/pdata）来近似 entry provider 关系。
    entry_providers: Dict[str, set[str]] = {}
    for pctx in plugin_contexts:
        entries = pctx.conf.get("entries") or pctx.pdata.get("entries") or []
        for ent in entries:
            try:
                entry_id = ent.get("id") if isinstance(ent, dict) else str(ent)
            except Exception:
                entry_id = None
            if entry_id:
                entry_providers.setdefault(str(entry_id), set()).add(pctx.pid)
    
    for ctx in plugin_contexts:
        for dep in ctx.dependencies:
            for dep_pid in _get_dependency_plugin_ids(dep, logger):
                if dep_pid in pid_to_context:
                    graph[ctx.pid].add(dep_pid)
                    logger.debug("Dependency edge: {} -> {}", ctx.pid, dep_pid)
            entry_spec = getattr(dep, "entry", None)
            if isinstance(entry_spec, str) and ":" not in entry_spec and entry_spec:
                for provider_pid in entry_providers.get(entry_spec, set()):
                    if provider_pid != ctx.pid and provider_pid in pid_to_context:
                        graph[ctx.pid].add(provider_pid)
                        logger.debug("Dependency edge (entry): {} -> {} via entry '{}'", ctx.pid, provider_pid, entry_spec)
    
    # Kahn 算法：构建邻接表和入度表
    adj_list: Dict[str, List[str]] = {pid: [] for pid in pid_to_context}
    in_degree: Dict[str, int] = {pid: 0 for pid in pid_to_context}
    
    for ctx in plugin_contexts:
        dependent = ctx.pid
        for dep in ctx.dependencies:
            for dependency in _get_dependency_plugin_ids(dep, logger):
                if dependency in pid_to_context:
                    adj_list[dependency].append(dependent)
                    in_degree[dependent] += 1
            entry_spec = getattr(dep, "entry", None)
            if isinstance(entry_spec, str) and ":" not in entry_spec and entry_spec:
                for provider_pid in entry_providers.get(entry_spec, set()):
                    if provider_pid != dependent and provider_pid in pid_to_context:
                        adj_list[provider_pid].append(dependent)
                        in_degree[dependent] += 1
    
    def _queue_sort_key(pid: str) -> tuple[int, int, int, str]:
        """
        同层节点排序规则（不改变拓扑约束，仅影响同一入度层的加载先后）：
        1) enabled 优先
        2) adapter 优先于普通 plugin
        3) adapter 按 adapter.priority 升序（数字越小越先启动）
        4) pid 字典序兜底
        """
        ctx = pid_to_context.get(pid)
        if ctx is None:
            return (1, 1, 0, str(pid))

        enabled_rank = 0 if ctx.enabled else 1
        plugin_type = str(ctx.pdata.get("type", "plugin")) if isinstance(ctx.pdata, dict) else "plugin"

        if plugin_type == "adapter":
            adapter_rank = 0
            adapter_conf = ctx.conf.get("adapter", {}) if isinstance(ctx.conf, dict) else {}
            priority_raw = adapter_conf.get("priority", 0) if isinstance(adapter_conf, dict) else 0
            try:
                adapter_priority = int(priority_raw)
            except (TypeError, ValueError):
                adapter_priority = 0
        else:
            adapter_rank = 1
            adapter_priority = 0

        return (enabled_rank, adapter_rank, adapter_priority, str(pid))

    # 初始化队列（入度为 0 的节点）
    queue = [pid for pid in pid_to_context if in_degree[pid] == 0]
    queue.sort(key=_queue_sort_key)
    
    final_order: List[str] = []
    while queue:
        u = queue.pop(0)
        final_order.append(u)
        
        for v in adj_list[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
        queue.sort(key=_queue_sort_key)
    
    # 检查循环依赖
    if len(final_order) != len(plugin_contexts):
        loaded_set = set(final_order)
        missing = [ctx.pid for ctx in plugin_contexts if ctx.pid not in loaded_set]
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
            missing, cycle_info,
        )
        final_order.extend(missing)
    
    logger.debug("Plugin load order: {}", final_order)
    return final_order
