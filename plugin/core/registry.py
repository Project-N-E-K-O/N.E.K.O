from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Callable, Type, Optional, cast

from loguru import logger

_DEFAULT_LOGGER = logger


_pending_async_shutdown_tasks: set = set()


# _LoggerAdapter 和 _wrap_logger 已删除，统一使用 loguru
# 为保持向后兼容，_wrap_logger 现在直接返回 logger
def _wrap_logger(logger: Any) -> Any:
    """向后兼容函数，现在统一返回 loguru logger"""
    return logger

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin._types.events import EventHandler, EventMeta, EVENT_META_ATTR
from plugin._types.version import SDK_VERSION
from plugin.core.state import state
from plugin._types.models import PluginMeta, PluginAuthor, PluginDependency
from plugin._types.exceptions import (
    PluginImportError,
    PluginLoadError,
    PluginMetadataError,
)
from plugin.settings import PLUGIN_ENABLE_ID_CONFLICT_CHECK, PLUGIN_ENABLE_DEPENDENCY_CHECK
from plugin.utils import parse_bool_config

# 从 dependency.py 导入依赖相关函数
from plugin.core.dependency import (
    _parse_specifier,
    _version_matches,
    _find_plugins_by_entry,
    _find_plugins_by_custom_event,
    _check_plugin_dependency,
    _check_single_plugin_version,
    _parse_plugin_dependencies,
    _get_dependency_plugin_ids,
    _topological_sort_plugins,
)

try:
    from packaging.version import Version, InvalidVersion
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
except ImportError:  # pragma: no cover
    Version = None  # type: ignore
    InvalidVersion = Exception  # type: ignore
    SpecifierSet = None  # type: ignore
    InvalidSpecifier = Exception  # type: ignore


# SimpleEntryMeta 已删除，统一使用 sdk/events.py 中的 EventMeta


@dataclass
class PluginContext:
    """插件加载上下文，存储解析后的插件配置信息"""
    pid: str
    toml_path: Path
    conf: Dict[str, Any]
    pdata: Dict[str, Any]
    entry: str
    dependencies: List[PluginDependency]
    sdk_supported_str: Optional[str]
    sdk_recommended_str: Optional[str]
    sdk_untested_str: Optional[str]
    sdk_conflicts_list: List[str]
    enabled: bool
    auto_start: bool


# Mapping from (plugin_id, entry_id) -> actual python method name on the instance.
plugin_entry_method_map: Dict[tuple, str] = {}


# _parse_specifier, _version_matches 已移动到 dependency.py




def get_plugins() -> List[Dict[str, Any]]:
    """Return list of plugin dicts (in-process access)."""
    with state.acquire_plugins_read_lock():
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
    with state.acquire_plugins_read_lock():
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
    with state.acquire_plugin_hosts_read_lock():
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

    with state.acquire_plugins_read_lock():
        plugins_snapshot = dict(state.plugins)
    with state.acquire_plugin_hosts_read_lock():
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
        return None

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
    logger_ = cast(Any, _wrap_logger(logger or _DEFAULT_LOGGER))
    
    # 准备插件数据用于哈希计算
    plugin_data = {
        "id": plugin.id,
        "name": plugin.name,
        "version": plugin.version,
        "entry": entry_point or "",
    }

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
            type=plugin.type,
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
    
    with state.acquire_plugins_write_lock():
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
    # 使用模块级 logger
    for name, member in inspect.getmembers(cls):
        event_meta = getattr(member, EVENT_META_ATTR, None)
        if event_meta is None and hasattr(member, "__wrapped__"):
            event_meta = getattr(member.__wrapped__, EVENT_META_ATTR, None)

        if event_meta:
            etype = getattr(event_meta, "event_type", None) or "plugin_entry"
            eid = getattr(event_meta, "id", name)
            handler_obj = EventHandler(meta=event_meta, handler=member)
            with state.acquire_event_handlers_write_lock():
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
            entry_meta = EventMeta(
                event_type="plugin_entry",
                id=eid,
                name=ent.get("name", "") if isinstance(ent, dict) else "",
                description=ent.get("description", "") if isinstance(ent, dict) else "",
                input_schema=ent.get("input_schema", {}) if isinstance(ent, dict) else {},
            )
            eh = EventHandler(meta=entry_meta, handler=handler_fn)
            with state.acquire_event_handlers_write_lock():
                state.event_handlers[f"{pid}.{eid}"] = eh
                state.event_handlers[f"{pid}:plugin_entry:{eid}"] = eh
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning("Error parsing entry {} for plugin {}: {}", ent, pid, e, exc_info=True)
            # 继续处理其他条目，不中断整个插件加载


def _build_plugin_meta(
    pid: str,
    pdata: dict,
    *,
    sdk_supported_str: Optional[str] = None,
    sdk_recommended_str: Optional[str] = None,
    sdk_untested_str: Optional[str] = None,
    sdk_conflicts_list: Optional[List[str]] = None,
    dependencies: Optional[List[PluginDependency]] = None,
    input_schema: Optional[Dict[str, Any]] = None,
    host_plugin_id: Optional[str] = None,
) -> PluginMeta:
    """统一构建 PluginMeta，消除 disabled / extension / normal 三处重复。"""
    author_data = pdata.get("author")
    author = None
    if author_data and isinstance(author_data, dict):
        author = PluginAuthor(
            name=author_data.get("name"),
            email=author_data.get("email"),
        )

    return PluginMeta(
        id=pid,
        name=pdata.get("name", pid),
        type=pdata.get("type", "plugin"),
        description=pdata.get("description", ""),
        version=pdata.get("version", "0.1.0"),
        sdk_version=sdk_supported_str or SDK_VERSION,
        sdk_recommended=sdk_recommended_str,
        sdk_supported=sdk_supported_str,
        sdk_untested=sdk_untested_str,
        sdk_conflicts=sdk_conflicts_list or [],
        input_schema=input_schema or {"type": "object", "properties": {}},
        author=author,
        dependencies=dependencies or [],
        host_plugin_id=host_plugin_id,
    )


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


# ============================================================================
# load_plugins_from_toml 辅助函数
# ============================================================================

def _check_sdk_compatibility(
    pid: str,
    sdk_config: Optional[Dict[str, Any]],
    logger: Any,
) -> tuple[bool, Optional[str], Optional[str], Optional[str], List[str]]:
    """
    检查插件的 SDK 版本兼容性。
    
    Args:
        pid: 插件 ID
        sdk_config: SDK 配置字典 (plugin.sdk)
        logger: 日志记录器
    
    Returns:
        (is_compatible, sdk_supported_str, sdk_recommended_str, sdk_untested_str, sdk_conflicts_list)
        如果不兼容，is_compatible 为 False
    """
    sdk_supported_str = None
    sdk_recommended_str = None
    sdk_untested_str = None
    sdk_conflicts_list: List[str] = []
    
    # 解析 SDK 配置
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
        logger.error(
            "Plugin {}: SDK configuration must be a dict (plugin.sdk block), got {}; skipping load",
            pid, type(sdk_config).__name__
        )
        return False, None, None, None, []
    
    # 版本检查
    host_version_obj: Optional[Any] = None
    if Version and SpecifierSet:
        try:
            host_version_obj = Version(SDK_VERSION)
        except InvalidVersion as e:
            logger.error("Invalid host SDK_VERSION {}: {}", SDK_VERSION, e)
            host_version_obj = None
    
    if host_version_obj:
        supported_spec = _parse_specifier(sdk_supported_str, logger)
        recommended_spec = _parse_specifier(sdk_recommended_str, logger)
        untested_spec = _parse_specifier(sdk_untested_str, logger)
        conflict_specs = [_parse_specifier(c, logger) for c in sdk_conflicts_list]
        
        # 验证 specifier 格式
        if sdk_supported_str and supported_spec is None:
            logger.error("Plugin {}: invalid supported SDK spec '{}'; skipping load", pid, sdk_supported_str)
            return False, None, None, None, []
        if sdk_untested_str and untested_spec is None:
            logger.error("Plugin {}: invalid untested SDK spec '{}'; skipping load", pid, sdk_untested_str)
            return False, None, None, None, []
        invalid_conflicts = [c for c, s in zip(sdk_conflicts_list, conflict_specs) if c and s is None]
        if invalid_conflicts:
            logger.error("Plugin {}: invalid conflict SDK spec(s) {}; skipping load", pid, invalid_conflicts)
            return False, None, None, None, []
        
        # 冲突检查
        if any(spec and _version_matches(spec, host_version_obj) for spec in conflict_specs):
            logger.error(
                "Plugin {} conflicts with host SDK {} (conflict ranges: {}); skipping load",
                pid, SDK_VERSION, sdk_conflicts_list
            )
            return False, None, None, None, []
        
        # 兼容性检查
        in_supported = _version_matches(supported_spec, host_version_obj)
        in_untested = _version_matches(untested_spec, host_version_obj)
        
        if supported_spec and not (in_supported or in_untested):
            logger.error(
                "Plugin {} requires SDK in {} (or untested {}) but host SDK is {}; skipping load",
                pid, sdk_supported_str, sdk_untested_str, SDK_VERSION
            )
            return False, None, None, None, []
        
        # 警告
        if recommended_spec and not _version_matches(recommended_spec, host_version_obj):
            logger.warning("Plugin {}: host SDK {} is outside recommended range {}", pid, SDK_VERSION, sdk_recommended_str)
        if in_untested and not in_supported:
            logger.warning("Plugin {}: host SDK {} is within untested range {}; proceed with caution", pid, SDK_VERSION, sdk_untested_str)
    else:
        # 回退到字符串比较
        if sdk_supported_str and sdk_supported_str != SDK_VERSION:
            logger.error("Plugin {} requires sdk_version {} but host SDK is {}; skipping load", pid, sdk_supported_str, SDK_VERSION)
            return False, None, None, None, []
    
    return True, sdk_supported_str, sdk_recommended_str, sdk_untested_str, sdk_conflicts_list


def _parse_single_plugin_config(
    toml_path: Path,
    processed_paths: set,
    logger: Any,
) -> Optional[PluginContext]:
    """
    解析单个插件的 TOML 配置文件。
    
    Args:
        toml_path: TOML 文件路径
        processed_paths: 已处理的路径集合（用于去重）
        logger: 日志记录器
    
    Returns:
        PluginContext 或 None（如果解析失败或应跳过）
    """
    logger.info("Processing plugin config: {}", toml_path)
    
    try:
        with toml_path.open("rb") as f:
            conf = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as e:
        logger.error("Failed to parse plugin config {}: {}", toml_path, e)
        return None
    
    pdata = conf.get("plugin") or {}
    pid = pdata.get("id")
    if not pid:
        logger.warning("Plugin config {} has no 'id' field, skipping", toml_path)
        return None
    
    # 应用用户配置覆盖
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
            pid, e,
        )
    
    logger.debug("Plugin ID: {}", pid)
    
    # 检查重复路径
    try:
        resolved_path = toml_path.resolve()
        if str(resolved_path) in processed_paths:
            logger.warning(
                "Plugin config file {} has already been processed in this scan, skipping duplicate",
                toml_path
            )
            return None
        processed_paths.add(str(resolved_path))
    except (OSError, RuntimeError) as e:
        logger.debug("Failed to resolve path for duplicate check: {}", e)
    
    # 验证入口点
    entry = pdata.get("entry")
    if not entry or ":" not in entry:
        logger.warning("Plugin {} has invalid entry point '{}', skipping", pid, entry)
        return None
    
    logger.debug("Plugin {} entry point: {}", pid, entry)
    
    # 解析运行时配置
    runtime_cfg = conf.get("plugin_runtime")
    enabled_val = True
    auto_start_val = True
    if isinstance(runtime_cfg, dict):
        enabled_val = parse_bool_config(runtime_cfg.get("enabled"), default=True)
        auto_start_val = parse_bool_config(runtime_cfg.get("auto_start"), default=True)
    
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
    
    # SDK 兼容性检查
    sdk_config = pdata.get("sdk")
    is_compatible, sdk_supported_str, sdk_recommended_str, sdk_untested_str, sdk_conflicts_list = \
        _check_sdk_compatibility(pid, sdk_config, logger)
    
    if not is_compatible:
        return None
    
    # 解析依赖
    dependencies = _parse_plugin_dependencies(conf, logger, pid)
    
    return PluginContext(
        pid=pid,
        toml_path=toml_path,
        conf=conf,
        pdata=pdata,
        entry=entry,
        dependencies=dependencies,
        sdk_supported_str=sdk_supported_str,
        sdk_recommended_str=sdk_recommended_str,
        sdk_untested_str=sdk_untested_str,
        sdk_conflicts_list=sdk_conflicts_list,
        enabled=enabled_val,
        auto_start=auto_start_val,
    )


def _collect_plugin_contexts(
    plugin_config_root: Path,
    logger: Any,
) -> tuple[List[PluginContext], Dict[str, PluginContext]]:
    """
    Phase 1: 收集和解析所有插件配置。
    
    Args:
        plugin_config_root: 插件配置根目录
        logger: 日志记录器
    
    Returns:
        (plugin_contexts, pid_to_context)
    """
    found_toml_files = list(plugin_config_root.glob("*/plugin.toml"))
    logger.info("Found {} plugin.toml files: {}", len(found_toml_files), [str(p) for p in found_toml_files])
    
    plugin_contexts: List[PluginContext] = []
    processed_paths: set = set()
    pid_to_context: Dict[str, PluginContext] = {}
    
    for toml_path in found_toml_files:
        try:
            ctx = _parse_single_plugin_config(toml_path, processed_paths, logger)
            if ctx is not None:
                if ctx.pid in pid_to_context:
                    logger.error(
                        "Duplicate plugin id '{}' found in '{}' and '{}'; skipping later config",
                        ctx.pid,
                        pid_to_context[ctx.pid].toml_path,
                        toml_path,
                    )
                    continue
                plugin_contexts.append(ctx)
                pid_to_context[ctx.pid] = ctx
        except Exception:
            logger.exception("Unexpected error processing config {}", toml_path)
            continue
    
    return plugin_contexts, pid_to_context


def _build_extension_map(
    plugin_contexts: List[PluginContext],
) -> Dict[str, List[Dict[str, str]]]:
    """
    构建 Extension 映射：host_plugin_id -> [extension_configs]
    
    Args:
        plugin_contexts: 插件上下文列表
    
    Returns:
        Extension 映射字典
    """
    extension_map: Dict[str, List[Dict[str, str]]] = {}
    
    for ctx in plugin_contexts:
        if ctx.pdata.get("type") != "extension":
            continue
        
        host_conf = ctx.pdata.get("host")
        if not isinstance(host_conf, dict):
            continue
        
        host_pid = host_conf.get("plugin_id")
        if not host_pid:
            continue
        
        # 检查是否启用
        runtime_cfg = ctx.conf.get("plugin_runtime")
        if isinstance(runtime_cfg, dict):
            if not parse_bool_config(runtime_cfg.get("enabled"), default=True):
                continue
        
        extension_map.setdefault(host_pid, []).append({
            "ext_id": ctx.pid,
            "ext_entry": ctx.entry,
            "prefix": host_conf.get("prefix", ""),
        })
    
    return extension_map


def _shutdown_host_safely(host: Any, logger: Any, plugin_id: str) -> None:
    """
    安全关闭插件 host。
    
    Args:
        host: 插件 host 对象
        logger: 日志记录器
        plugin_id: 插件 ID（用于日志）
    """
    import asyncio
    
    try:
        if hasattr(host, "shutdown_sync"):
            host.shutdown_sync(timeout=1.0)
        elif hasattr(host, "shutdown"):
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
                    logger.debug("Plugin {} scheduled async shutdown", plugin_id)
                except RuntimeError:
                    asyncio.run(host.shutdown(timeout=1.0))
            else:
                host.shutdown(timeout=1.0)
        elif hasattr(host, "process") and getattr(host, "process", None):
            host.process.terminate()
            host.process.join(timeout=1.0)
    except Exception as e:
        logger.debug("Error shutting down plugin {}: {}", plugin_id, e)


def _migrate_plugin_id(
    old_pid: str,
    new_pid: str,
    host: Any,
    logger: Any,
) -> None:
    """
    迁移插件 ID：更新所有相关映射。
    
    Args:
        old_pid: 原插件 ID
        new_pid: 新插件 ID
        host: 插件 host 对象
        logger: 日志记录器
    """
    logger.warning(
        "Plugin ID changed during registration from '{}' to '{}', updating plugin_hosts",
        old_pid, new_pid
    )
    
    # 更新 plugin_hosts
    with state.acquire_plugin_hosts_write_lock():
        if old_pid in state.plugin_hosts:
            existing_host = state.plugin_hosts.pop(old_pid)
            state.plugin_hosts[new_pid] = existing_host
            if hasattr(existing_host, 'plugin_id'):
                existing_host.plugin_id = new_pid
            logger.info("Plugin host moved from '{}' to '{}' in plugin_hosts", old_pid, new_pid)
        else:
            # old_pid not in plugin_hosts; register the passed-in host under new_pid
            if host is not None:
                state.plugin_hosts[new_pid] = host
                if hasattr(host, 'plugin_id'):
                    host.plugin_id = new_pid
                logger.warning("Plugin host for '{}' not found during migration; registered passed-in host under '{}'", old_pid, new_pid)
            else:
                logger.warning("Plugin host for '{}' not found during migration and passed-in host is None; skipping host registration for '{}'", old_pid, new_pid)
    
    # 迁移 response queue
    with state._plugin_response_queues_lock:
        if old_pid in state._plugin_response_queues:
            q = state._plugin_response_queues.pop(old_pid)
            state._plugin_response_queues[new_pid] = q
    
    # 迁移 event handlers
    with state.acquire_event_handlers_write_lock():
        handlers_to_migrate = [
            k for k in list(state.event_handlers.keys())
            if k.startswith(f"{old_pid}.") or k.startswith(f"{old_pid}:")
        ]
        for old_key in handlers_to_migrate:
            if old_key.startswith(f"{old_pid}."):
                new_key = old_key.replace(f"{old_pid}.", f"{new_pid}.", 1)
            else:
                new_key = old_key.replace(f"{old_pid}:", f"{new_pid}:", 1)
            state.event_handlers[new_key] = state.event_handlers.pop(old_key)
    
    # 迁移 plugin_entry_method_map
    for (p, eid), method in list(plugin_entry_method_map.items()):
        if p == old_pid:
            plugin_entry_method_map[(new_pid, eid)] = method
            del plugin_entry_method_map[(p, eid)]


def _load_disabled_plugin(
    ctx: PluginContext,
    logger: Any,
) -> None:
    """
    加载禁用的插件（仅注册元数据，不启动进程）。
    
    Args:
        ctx: 插件上下文
        logger: 日志记录器
    """
    entries_preview = _extract_entries_preview(
        ctx.pid,
        cls=type("DisabledPluginStub", (), {}),
        conf=ctx.conf,
        pdata=ctx.pdata,
    )
    
    plugin_meta = _build_plugin_meta(
        ctx.pid, ctx.pdata,
        sdk_supported_str=ctx.sdk_supported_str,
        sdk_recommended_str=ctx.sdk_recommended_str,
        sdk_untested_str=ctx.sdk_untested_str,
        sdk_conflicts_list=ctx.sdk_conflicts_list,
        dependencies=ctx.dependencies,
    )
    
    resolved_id = register_plugin(
        plugin_meta,
        logger,
        config_path=ctx.toml_path,
        entry_point=ctx.entry,
    )
    
    if resolved_id is not None:
        with state.acquire_plugins_write_lock():
            meta = state.plugins.get(resolved_id)
            if isinstance(meta, dict):
                meta["runtime_enabled"] = False
                meta["runtime_auto_start"] = False
                meta["entries_preview"] = entries_preview
                state.plugins[resolved_id] = meta


def _load_extension_plugin(
    ctx: PluginContext,
    logger: Any,
) -> None:
    """
    加载 Extension 类型插件（仅注册元数据，不启动独立进程）。
    
    Args:
        ctx: 插件上下文
        logger: 日志记录器
    """
    host_conf = ctx.pdata.get("host")
    host_pid = host_conf.get("plugin_id") if isinstance(host_conf, dict) else None
    
    plugin_meta = _build_plugin_meta(
        ctx.pid, ctx.pdata,
        sdk_supported_str=ctx.sdk_supported_str,
        sdk_recommended_str=ctx.sdk_recommended_str,
        sdk_untested_str=ctx.sdk_untested_str,
        sdk_conflicts_list=ctx.sdk_conflicts_list,
        dependencies=ctx.dependencies,
        host_plugin_id=host_pid,
    )
    
    resolved_id = register_plugin(
        plugin_meta,
        logger,
        config_path=ctx.toml_path,
        entry_point=ctx.entry,
    )
    
    if resolved_id is not None:
        with state.acquire_plugins_write_lock():
            meta = state.plugins.get(resolved_id)
            if isinstance(meta, dict):
                meta["runtime_enabled"] = ctx.enabled
                meta["runtime_auto_start"] = False
                state.plugins[resolved_id] = meta
    
    logger.info(
        "Extension '{}' registered (host='{}'); will be injected into host process at runtime",
        ctx.pid, host_pid,
    )


def _load_adapter_plugin(
    ctx: PluginContext,
    logger: Any,
    process_host_factory: Callable[..., Any],
    plugin_id: Optional[str] = None,
) -> Optional[Any]:
    """
    加载 Adapter 类型插件。
    
    Adapter 是一种特殊的插件类型，用于：
    1. 作为网关转发外部协议请求到 NEKO 插件
    2. 作为路由器直接处理外部请求
    3. 作为桥接器在不同协议间转换
    
    Adapter 作为独立进程运行，但具有更高的启动优先级。
    
    Args:
        ctx: 插件上下文
        logger: 日志记录器
        process_host_factory: 进程宿主工厂函数
    
    Returns:
        创建的 host 对象，或 None 如果加载失败
    """
    pid = plugin_id or ctx.pid
    pdata = ctx.pdata
    toml_path = ctx.toml_path
    entry = ctx.entry
    
    # 解析 adapter 配置
    adapter_conf = ctx.conf.get("adapter", {})
    adapter_mode = adapter_conf.get("mode", "hybrid")
    
    logger.info(
        "Loading adapter '{}' (mode={})",
        pid, adapter_mode,
    )
    
    # 构建插件元数据
    plugin_meta = _build_plugin_meta(
        pid, pdata,
        sdk_supported_str=ctx.sdk_supported_str,
        sdk_recommended_str=ctx.sdk_recommended_str,
        sdk_untested_str=ctx.sdk_untested_str,
        sdk_conflicts_list=ctx.sdk_conflicts_list,
        dependencies=ctx.dependencies,
    )
    
    # 创建进程宿主
    host = None
    try:
        logger.debug("Adapter {}: creating process host...", pid)
        host = process_host_factory(pid, entry, toml_path, extension_configs=None)
        logger.info(
            "Adapter {}: process host created successfully (pid: {}, alive: {})",
            pid,
            getattr(host.process, 'pid', 'N/A') if hasattr(host, 'process') and host.process else 'N/A',
            host.process.is_alive() if hasattr(host, 'process') and host.process else False
        )
        
        # 注册到 plugin_hosts
        with state.acquire_plugin_hosts_write_lock():
            state.plugin_hosts[pid] = host
        
    except (OSError, RuntimeError) as e:
        logger.error("Failed to start adapter process for {}: {}", pid, e, exc_info=True)
        return None
    except Exception:
        logger.exception("Unexpected error starting adapter process for {}", pid)
        return None
    
    # 注册插件元数据
    resolved_id = register_plugin(
        plugin_meta,
        logger,
        config_path=toml_path,
        entry_point=entry,
    )
    
    if resolved_id is None:
        # 重复加载，关闭 host
        if host is not None:
            _shutdown_host_safely(host, logger, pid)
            with state.acquire_plugin_hosts_write_lock():
                state.plugin_hosts.pop(pid, None)
        return None
    
    # 更新运行时状态
    with state.acquire_plugins_write_lock():
        meta = state.plugins.get(resolved_id)
        if isinstance(meta, dict):
            meta["runtime_enabled"] = ctx.enabled
            meta["runtime_auto_start"] = ctx.auto_start
            # type 是唯一权威字段；plugin_type 仅做兼容镜像。
            meta["type"] = "adapter"
            meta["plugin_type"] = meta["type"]
            meta["adapter_mode"] = adapter_mode
            state.plugins[resolved_id] = meta

    if resolved_id != pid:
        _migrate_plugin_id(pid, resolved_id, host, logger)
    
    logger.info("Adapter '{}' loaded successfully", resolved_id)
    return host


def _check_plugin_already_loaded(
    pid: str,
    toml_path: Path,
    logger: Any,
) -> bool:
    """
    检查插件是否已经加载。
    
    Args:
        pid: 插件 ID
        toml_path: 配置文件路径
        logger: 日志记录器
    
    Returns:
        True 如果已加载，应跳过
    """
    with state.acquire_plugin_hosts_read_lock():
        if pid in state.plugin_hosts:
            existing_host = state.plugin_hosts[pid]
            existing_config_path = getattr(existing_host, 'config_path', None)
            if existing_config_path:
                try:
                    existing_resolved = Path(existing_config_path).resolve()
                    current_resolved = toml_path.resolve()
                    if existing_resolved == current_resolved:
                        logger.warning(
                            "Plugin {} from {} is already loaded (same config path), skipping duplicate load",
                            pid, toml_path
                        )
                        return True
                except (OSError, RuntimeError):
                    if str(existing_config_path) == str(toml_path):
                        logger.warning(
                            "Plugin {} from {} is already loaded (same config path), skipping duplicate load",
                            pid, toml_path
                        )
                        return True
    return False


def _check_plugin_already_registered(
    pid: str,
    toml_path: Path,
    logger: Any,
) -> bool:
    """
    检查插件是否已注册但未运行。
    
    Args:
        pid: 插件 ID
        toml_path: 配置文件路径
        logger: 日志记录器
    
    Returns:
        True 如果已注册，应跳过
    """
    with state.acquire_plugins_read_lock():
        plugin_already_registered = pid in state.plugins
    
    if plugin_already_registered:
        with state.acquire_plugin_hosts_read_lock():
            if pid in state.plugin_hosts:
                existing_host = state.plugin_hosts[pid]
                if hasattr(existing_host, 'is_alive') and existing_host.is_alive():
                    logger.info(
                        "Plugin {} from {} is already registered and running, skipping duplicate load",
                        pid, toml_path
                    )
                    return True
                else:
                    logger.info(
                        "Plugin {} from {} is already registered but not running, skipping duplicate load",
                        pid, toml_path
                    )
                    return True
            else:
                logger.warning(
                    "Plugin {} from {} is already registered in state.plugins but has no host in plugin_hosts. "
                    "This indicates the plugin was registered but the host creation was skipped or failed. "
                    "Please start the plugin manually via POST /plugin/{}/start",
                    pid, toml_path, pid
                )
                return True
    return False


def load_plugins_from_toml(
    plugin_config_root: Path,
    logger: Any,
    process_host_factory: Callable[..., Any],
) -> None:
    """
    扫描插件配置，启动子进程，并静态扫描元数据用于注册列表。
    process_host_factory 接收 (plugin_id, entry_point, config_path, extension_configs=None) 并返回宿主对象。
    
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
    project_root = plugin_config_root.parent.parent.resolve()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        logger.info("Added project root to sys.path: {}", project_root)
    logger.info("Current working directory: {}", os.getcwd())
    logger.info("Python path (first 3): {}", sys.path[:3])
    
    # === Phase 1: Collect and Parse ===
    plugin_contexts, pid_to_context = _collect_plugin_contexts(plugin_config_root, logger)
    
    if not plugin_contexts:
        logger.info("No valid plugins found to load")
        return
    
    # === Phase 2: Topological Sort ===
    final_order = _topological_sort_plugins(plugin_contexts, pid_to_context, logger)
    
    # === Phase 3: Load ===
    # 预构建 extension 映射
    extension_map = _build_extension_map(plugin_contexts)
    
    # 加载每个插件
    for pid in final_order:
        ctx = pid_to_context.get(pid)
        if not ctx:
            continue
            
        toml_path = ctx.toml_path
        conf = ctx.conf
        pdata = ctx.pdata
        entry = ctx.entry
        dependencies = ctx.dependencies
        sdk_supported_str = ctx.sdk_supported_str
        sdk_recommended_str = ctx.sdk_recommended_str
        sdk_untested_str = ctx.sdk_untested_str
        sdk_conflicts_list = ctx.sdk_conflicts_list
        enabled_val = ctx.enabled
        auto_start_val = ctx.auto_start
        
        logger.info("Loading plugin: {}", pid)

        # disabled plugins: visibility only
        if not enabled_val:
            _load_disabled_plugin(ctx, logger)
            continue

        # 根据插件类型分发加载逻辑
        plugin_type = pdata.get("type", "plugin")
        
        # extension 类型：不启动独立进程，只注册元数据
        if plugin_type == "extension":
            _load_extension_plugin(ctx, logger)
            continue
        
        # 依赖检查（可通过配置禁用）
        dependency_check_failed = False
        if PLUGIN_ENABLE_DEPENDENCY_CHECK and dependencies:
            logger.debug("Plugin {}: checking {} dependency(ies)...", pid, len(dependencies))
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
                logger.debug("Plugin {}: all dependencies satisfied", pid)
        elif not PLUGIN_ENABLE_DEPENDENCY_CHECK and dependencies:
            logger.warning(
                "Plugin {}: has {} dependency(ies), but dependency check is disabled. "
                "Loading plugin without dependency validation.",
                pid, len(dependencies)
            )
        else:
            logger.debug("Plugin {}: no dependencies to check", pid)
        
        if dependency_check_failed:
            logger.debug("Plugin {}: skipping due to failed dependency check", pid)
            continue

        # 检查插件是否已经加载
        if _check_plugin_already_loaded(pid, toml_path, logger):
            continue
        
        # 检测并解决插件 ID 冲突
        plugin_data_for_hash = {
            "id": pid,
            "name": pdata.get("name", pid),
            "version": pdata.get("version", "0.1.0"),
            "entry": entry or "",
        }
        original_pid = pid
        resolved_pid = _resolve_plugin_id_conflict(
            pid, logger,
            config_path=toml_path,
            entry_point=entry,
            plugin_data=plugin_data_for_hash,
            purpose="load",
            enable_rename=bool(PLUGIN_ENABLE_ID_CONFLICT_CHECK),
        )

        if resolved_pid is None:
            logger.info("Plugin {} from {} is already loaded (duplicate detected), skipping", original_pid, toml_path)
            continue

        pid = resolved_pid
        if pid != original_pid:
            logger.warning("Plugin {} from {}: ID changed from '{}' to '{}' due to conflict", original_pid, toml_path, original_pid, pid)
            # 同步 extension_map：将 original_pid 下收集的扩展迁移到新 pid
            if original_pid in extension_map:
                moved_exts = extension_map.pop(original_pid)
                extension_map.setdefault(pid, []).extend(moved_exts)

        # 检查插件是否已注册
        if _check_plugin_already_registered(pid, toml_path, logger):
            continue

        # adapter 类型：在通过统一依赖和重复检查后，再走 adapter-specific 启动逻辑
        if plugin_type == "adapter":
            _load_adapter_plugin(ctx, logger, process_host_factory, plugin_id=pid)
            continue

        module_path, class_name = entry.split(":", 1)
        logger.debug("Plugin {}: importing {}:{}", pid, module_path, class_name)
        try:
            mod = importlib.import_module(module_path)
            cls: Type[Any] = getattr(mod, class_name)
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
                logger.debug("Plugin {}: creating process host...", pid)
                ext_cfgs = extension_map.get(pid)
                host = process_host_factory(pid, entry, toml_path, extension_configs=ext_cfgs)
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
                with state.acquire_plugin_hosts_write_lock():
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
                            logger.debug("Plugin {}: re-registered in plugin_hosts", pid)

                if skip_register:
                    _shutdown_host_safely(host, logger, pid)
                    continue
            except (OSError, RuntimeError) as e:
                logger.error("Failed to start process for plugin {}: {}", pid, e, exc_info=True)
                continue
            except Exception:
                logger.exception("Unexpected error starting process for plugin {}", pid)
                continue

        scan_static_metadata(pid, cls, conf, pdata)

        plugin_meta = _build_plugin_meta(
            pid, pdata,
            sdk_supported_str=sdk_supported_str,
            sdk_recommended_str=sdk_recommended_str,
            sdk_untested_str=sdk_untested_str,
            sdk_conflicts_list=sdk_conflicts_list,
            dependencies=dependencies,
            input_schema=getattr(cls, "input_schema", {}) or {"type": "object", "properties": {}},
        )
        
        # 在调用 register_plugin 之前，验证 host 是否还在 plugin_hosts 中。
        # 对于 manual-start-only 插件（auto_start=false），host 允许为 None，此时不应要求在 plugin_hosts 中存在。
        host_still_exists = False
        if host is not None:
            with state.acquire_plugin_hosts_read_lock():
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
            with state.acquire_plugins_write_lock():
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
            with state.acquire_plugin_hosts_read_lock():
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
        
        # 如果 register_plugin 返回 None，说明这是重复加载
        if resolved_id is None:
            logger.warning("Plugin {} from {} detected as duplicate in register_plugin, removing from plugin_hosts", pid, toml_path)
            existing_host = None
            with state.acquire_plugin_hosts_write_lock():
                if pid in state.plugin_hosts:
                    existing_host = state.plugin_hosts.pop(pid)
            if existing_host is not None:
                _shutdown_host_safely(existing_host, logger, pid)
            logger.debug("Plugin {} removed from plugin_hosts due to duplicate detection", pid)
            continue
        
        # 如果 ID 被进一步重命名，迁移所有相关映射
        if resolved_id != pid:
            _migrate_plugin_id(pid, resolved_id, host, logger)
            pid = resolved_id

        logger.info("Loaded plugin {} (Process: {})", pid, getattr(host, "process", None))
        try:
            from plugin.server.services import _enqueue_lifecycle
            from plugin.server.infrastructure.utils import now_iso

            _enqueue_lifecycle({"type": "plugin_loaded", "plugin_id": pid, "time": now_iso()})
        except Exception:
            logger.debug("Failed to enqueue lifecycle event for plugin {}", pid, exc_info=True)
