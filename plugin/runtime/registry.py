from __future__ import annotations

from dataclasses import dataclass
import importlib
import inspect
import logging
from pathlib import Path
from typing import Any, Dict, List, Callable, Type

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.sdk.events import EventHandler, EVENT_META_ATTR
from plugin.core.state import state
from plugin.api.models import PluginMeta
from plugin.api.exceptions import (
    PluginImportError,
    PluginLoadError,
    PluginMetadataError,
)


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
        try:
            eid = ent.get("id") if isinstance(ent, dict) else str(ent)
            if not eid:
                continue
            handler_fn = getattr(cls, eid, None)
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
            logger = logging.getLogger(__name__)
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
                logger.exception("Unexpected error importing plugin class %s: %s", entry, e)
                continue

            try:
                host = process_host_factory(pid, entry, toml_path)
                state.plugin_hosts[pid] = host
            except (OSError, RuntimeError) as e:
                logger.error("Failed to start process for plugin %s: %s", pid, e)
                continue
            except Exception as e:
                logger.exception("Unexpected error starting process for plugin %s: %s", pid, e)
                continue

            scan_static_metadata(pid, cls, conf, pdata)

            plugin_meta = PluginMeta(
                id=pid,
                name=pdata.get("name", pid),
                description=pdata.get("description", ""),
                version=pdata.get("version", "0.1.0"),
                input_schema=getattr(cls, "input_schema", {}) or {"type": "object", "properties": {}},
            )
            register_plugin(plugin_meta)

            logger.info("Loaded plugin %s (Process: %s)", pid, getattr(host, "process", None))
        except (KeyError, ValueError, TypeError) as e:
            # TOML 解析或配置错误
            logger.error("Invalid plugin configuration in %s: %s", toml_path, e)
        except Exception as e:
            # 其他未知错误
            logger.exception("Unexpected error loading plugin from %s: %s", toml_path, e)
