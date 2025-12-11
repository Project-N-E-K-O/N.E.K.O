from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
import logging
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
    for toml_path in plugin_config_root.glob("*/plugin.toml"):
        try:
            with toml_path.open("rb") as f:
                conf = tomllib.load(f)
            pdata = conf.get("plugin") or {}
            pid = pdata.get("id")
            if not pid:
                continue

            entry = pdata.get("entry")
            if not entry or ":" not in entry:
                continue

            sdk_config = pdata.get("sdk")
            sdk_supported_str = None
            sdk_recommended_str = None
            sdk_untested_str = None
            sdk_conflicts_list: List[str] = []

            # Backward compatibility: fall back to single sdk_version when no sdk block present
            if isinstance(sdk_config, dict):
                sdk_recommended_str = sdk_config.get("recommended")
                sdk_supported_str = sdk_config.get("supported") or sdk_config.get("compatible")
                sdk_untested_str = sdk_config.get("untested")
                raw_conflicts = sdk_config.get("conflicts") or []
                if isinstance(raw_conflicts, list):
                    sdk_conflicts_list = [str(c) for c in raw_conflicts if c]
                elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
                    sdk_conflicts_list = [raw_conflicts.strip()]
            else:
                sdk_supported_str = pdata.get("sdk_version") or sdk_config

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
                if any(spec and _version_matches(spec, host_version_obj) for spec in conflict_specs):
                    logger.error(
                        "Plugin %s conflicts with host SDK %s (conflict ranges: %s); skipping load",
                        pid,
                        SDK_VERSION,
                        sdk_conflicts_list,
                    )
                    continue

                # Compatibility check (supported or untested range)
                in_supported = _version_matches(supported_spec, host_version_obj)
                in_untested = _version_matches(untested_spec, host_version_obj)

                if supported_spec and not (in_supported or in_untested):
                    logger.error(
                        "Plugin %s requires SDK in %s (or untested %s) but host SDK is %s; skipping load",
                        pid,
                        sdk_supported_str,
                        sdk_untested_str,
                        SDK_VERSION,
                    )
                    continue

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
                if sdk_supported_str and sdk_supported_str != SDK_VERSION:
                    logger.error(
                        "Plugin %s requires sdk_version %s but host SDK is %s; skipping load",
                        pid,
                        sdk_supported_str,
                        SDK_VERSION,
                    )
                    continue

            module_path, class_name = entry.split(":", 1)
            try:
                mod = importlib.import_module(module_path)
                cls: Type[Any] = getattr(mod, class_name)
            except (ImportError, ModuleNotFoundError) as e:
                logger.error("Failed to import module '%s' for plugin %s: %s", module_path, pid, e)
                continue
            except AttributeError as e:
                logger.error("Class '%s' not found in module '%s' for plugin %s: %s", class_name, module_path, pid, e)
                continue
            except Exception as e:
                logger.exception("Unexpected error importing plugin class %s", entry)
                continue

            try:
                host = process_host_factory(pid, entry, toml_path)
                state.plugin_hosts[pid] = host
            except (OSError, RuntimeError) as e:
                logger.error("Failed to start process for plugin %s: %s", pid, e)
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
            logger.error("Invalid plugin configuration in %s: %s", toml_path, e)
        except Exception as e:
            # 其他未知错误
            logger.exception("Unexpected error loading plugin from %s", toml_path)
