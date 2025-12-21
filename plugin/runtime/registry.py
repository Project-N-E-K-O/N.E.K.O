from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Callable, Type, Optional

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.sdk.events import EventHandler, EVENT_META_ATTR
from plugin.sdk.version import SDK_VERSION
from plugin.core.state import state
from plugin.api.models import PluginMeta
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


def _parse_specifier(spec: Optional[str], logger: logging.Logger) -> Optional[SpecifierSet]:
    if not spec or SpecifierSet is None:
        return None
    try:
        return SpecifierSet(spec)
    except InvalidSpecifier as e:
        logger.error("Invalid sdk specifier '%s': %s", spec, e)
        return None


def _version_matches(spec: Optional[SpecifierSet], version: Version) -> bool:
    if spec is None:
        return False
    try:
        return version in spec
    except Exception:
        return False


def get_plugins() -> List[Dict[str, Any]]:
    """Return list of plugin dicts (in-process access)."""
    with state.plugins_lock:
        return list(state.plugins.values())


def register_plugin(plugin: PluginMeta) -> None:
    """Insert plugin into registry (not exposed as HTTP)."""
    with state.plugins_lock:
        state.plugins[plugin.id] = plugin.model_dump()


def scan_static_metadata(pid: str, cls: type, conf: dict, pdata: dict) -> None:
    """
    在不实例化的情况下扫描类属性，提取 @EventHandler 元数据并填充全局表。
    """
    logger = logging.getLogger(__name__)
    for name, member in inspect.getmembers(cls):
        event_meta = getattr(member, EVENT_META_ATTR, None)
        if event_meta is None and hasattr(member, "__wrapped__"):
            event_meta = getattr(member.__wrapped__, EVENT_META_ATTR, None)

        if event_meta and getattr(event_meta, "event_type", None) == "plugin_entry":
            eid = getattr(event_meta, "id", name)
            handler_obj = EventHandler(meta=event_meta, handler=member)
            with state.event_handlers_lock:
                state.event_handlers[f"{pid}.{eid}"] = handler_obj
                state.event_handlers[f"{pid}:plugin_entry:{eid}"] = handler_obj
            plugin_entry_method_map[(pid, str(eid))] = name

    entries = conf.get("entries") or pdata.get("entries") or []
    for ent in entries:
        logger = logging.getLogger(__name__)
        try:
            eid = ent.get("id") if isinstance(ent, dict) else str(ent)
            if not eid:
                continue
            try:
                handler_fn = getattr(cls, eid)
            except AttributeError:
                logger.warning(
                    "Entry id %s for plugin %s has no handler on class %s, skipping",
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
            eh = EventHandler(meta=entry_meta, handler=handler_fn)
            with state.event_handlers_lock:
                state.event_handlers[f"{pid}.{eid}"] = eh
                state.event_handlers[f"{pid}:plugin_entry:{eid}"] = eh
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning("Error parsing entry %s for plugin %s: %s", ent, pid, e, exc_info=True)
            # 继续处理其他条目，不中断整个插件加载


def load_plugins_from_toml(
    plugin_config_root: Path,
    logger: logging.Logger,
    process_host_factory: Callable[[str, str, Path], Any],
) -> None:
    """
    扫描插件配置，启动子进程，并静态扫描元数据用于注册列表。
    process_host_factory 接收 (plugin_id, entry_point, config_path) 并返回宿主对象。
    """
    if not plugin_config_root.exists():
        logger.info("No plugin config directory %s, skipping", plugin_config_root)
        return

    logger.info("Loading plugins from %s", plugin_config_root)
    
    # 设置 Python 路径，确保能够导入插件模块
    # 获取项目根目录（假设 plugin_config_root 在 plugin/plugins）
    project_root = plugin_config_root.parent.parent.resolve()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        logger.info("Added project root to sys.path: %s", project_root)
    logger.info("Current working directory: %s", os.getcwd())
    logger.info("Python path (first 3): %s", sys.path[:3])
    
    found_toml_files = list(plugin_config_root.glob("*/plugin.toml"))
    logger.info("Found %d plugin.toml files: %s", len(found_toml_files), [str(p) for p in found_toml_files])
    
    for toml_path in found_toml_files:
        logger.info("Processing plugin config: %s", toml_path)
        try:
            with toml_path.open("rb") as f:
                conf = tomllib.load(f)
            pdata = conf.get("plugin") or {}
            pid = pdata.get("id")
            if not pid:
                logger.warning("Plugin config %s has no 'id' field, skipping", toml_path)
                continue

            logger.info("Plugin ID: %s", pid)
            entry = pdata.get("entry")
            if not entry or ":" not in entry:
                logger.warning("Plugin %s has invalid entry point '%s', skipping", pid, entry)
                continue
            
            logger.info("Plugin %s entry point: %s", pid, entry)

            sdk_config = pdata.get("sdk")
            sdk_supported_str = None
            sdk_recommended_str = None
            sdk_untested_str = None
            sdk_conflicts_list: List[str] = []

            # Parse SDK version requirements from [plugin.sdk] block
            logger.debug("Plugin %s SDK config: %s", pid, sdk_config)
            if isinstance(sdk_config, dict):
                sdk_recommended_str = sdk_config.get("recommended")
                sdk_supported_str = sdk_config.get("supported") or sdk_config.get("compatible")
                sdk_untested_str = sdk_config.get("untested")
                raw_conflicts = sdk_config.get("conflicts") or []
                if isinstance(raw_conflicts, list):
                    sdk_conflicts_list = [str(c) for c in raw_conflicts if c]
                elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
                    sdk_conflicts_list = [raw_conflicts.strip()]
                logger.info(
                    "Plugin %s SDK requirements: supported=%s, recommended=%s, untested=%s, conflicts=%s",
                    pid, sdk_supported_str, sdk_recommended_str, sdk_untested_str, sdk_conflicts_list
                )
            else:
                # SDK configuration must be a dict (plugin.sdk block)
                logger.error(
                    "Plugin %s: SDK configuration must be a dict (plugin.sdk block), got %s; skipping load",
                    pid,
                    type(sdk_config).__name__
                )
                continue

            host_version_obj: Optional[Version] = None
            if Version and SpecifierSet:
                try:
                    host_version_obj = Version(SDK_VERSION)
                except InvalidVersion as e:
                    logger.error("Invalid host SDK_VERSION %s: %s", SDK_VERSION, e)
                    host_version_obj = None

            # Validate against ranges when possible
            if host_version_obj:
                supported_spec = _parse_specifier(sdk_supported_str, logger)
                recommended_spec = _parse_specifier(sdk_recommended_str, logger)
                untested_spec = _parse_specifier(sdk_untested_str, logger)
                conflict_specs = [
                    _parse_specifier(conf, logger) for conf in sdk_conflicts_list
                ]

                # Conflict check
                logger.debug("Plugin %s: checking conflicts against host SDK %s", pid, SDK_VERSION)
                if any(spec and _version_matches(spec, host_version_obj) for spec in conflict_specs):
                    logger.error(
                        "Plugin %s conflicts with host SDK %s (conflict ranges: %s); skipping load",
                        pid,
                        SDK_VERSION,
                        sdk_conflicts_list,
                    )
                    continue
                logger.debug("Plugin %s: no conflicts detected", pid)

                # Compatibility check (supported or untested range)
                logger.debug("Plugin %s: checking compatibility - supported_spec=%s, untested_spec=%s", pid, supported_spec, untested_spec)
                in_supported = _version_matches(supported_spec, host_version_obj)
                in_untested = _version_matches(untested_spec, host_version_obj)
                logger.debug("Plugin %s: compatibility check result - in_supported=%s, in_untested=%s", pid, in_supported, in_untested)

                if supported_spec and not (in_supported or in_untested):
                    logger.error(
                        "Plugin %s requires SDK in %s (or untested %s) but host SDK is %s; skipping load",
                        pid,
                        sdk_supported_str,
                        sdk_untested_str,
                        SDK_VERSION,
                    )
                    continue
                logger.info("Plugin %s: SDK version check passed", pid)

                # Recommended range warning
                if recommended_spec and not _version_matches(recommended_spec, host_version_obj):
                    logger.warning(
                        "Plugin %s: host SDK %s is outside recommended range %s",
                        pid,
                        SDK_VERSION,
                        sdk_recommended_str,
                    )

                # Untested warning
                if in_untested and not in_supported:
                    logger.warning(
                        "Plugin %s: host SDK %s is within untested range %s; proceed with caution",
                        pid,
                        SDK_VERSION,
                        sdk_untested_str,
                    )
            else:
                # If we cannot parse versions, require at least string equality for legacy sdk_version
                logger.warning("Plugin %s: Cannot parse SDK versions, using string comparison", pid)
                if sdk_supported_str and sdk_supported_str != SDK_VERSION:
                    logger.error(
                        "Plugin %s requires sdk_version %s but host SDK is %s; skipping load",
                        pid,
                        sdk_supported_str,
                        SDK_VERSION,
                    )
                    continue
                logger.info("Plugin %s: SDK version string check passed", pid)

            module_path, class_name = entry.split(":", 1)
            logger.info("Plugin %s: importing module '%s', class '%s'", pid, module_path, class_name)
            try:
                mod = importlib.import_module(module_path)
                logger.info("Plugin %s: module '%s' imported successfully", pid, module_path)
                cls: Type[Any] = getattr(mod, class_name)
                logger.info("Plugin %s: class '%s' found in module", pid, class_name)
            except (ImportError, ModuleNotFoundError) as e:
                logger.error("Failed to import module '%s' for plugin %s: %s", module_path, pid, e, exc_info=True)
                continue
            except AttributeError as e:
                logger.error("Class '%s' not found in module '%s' for plugin %s: %s", class_name, module_path, pid, e, exc_info=True)
                continue
            except Exception as e:
                logger.exception("Unexpected error importing plugin class %s for plugin %s", entry, pid)
                continue

            try:
                logger.info("Plugin %s: creating process host...", pid)
                host = process_host_factory(pid, entry, toml_path)
                logger.info("Plugin %s: process host created successfully", pid)
                state.plugin_hosts[pid] = host
                logger.info("Plugin %s: registered in plugin_hosts", pid)
            except (OSError, RuntimeError) as e:
                logger.error("Failed to start process for plugin %s: %s", pid, e, exc_info=True)
                continue
            except Exception as e:
                logger.exception("Unexpected error starting process for plugin %s", pid)
                continue

            scan_static_metadata(pid, cls, conf, pdata)

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
            )
            register_plugin(plugin_meta)

            logger.info("Loaded plugin %s (Process: %s)", pid, getattr(host, "process", None))
        except (KeyError, ValueError, TypeError) as e:
            # TOML 解析或配置错误
            logger.error("❌ Invalid plugin configuration in %s: %s", toml_path, e, exc_info=True)
        except Exception as e:
            # 其他未知错误
            logger.exception("❌ Unexpected error loading plugin from %s", toml_path)
