from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


class PluginCallError(RuntimeError):
    pass


def _parse_entry_ref(ref: str) -> Tuple[str, str]:
    # "plugin_id:entry_id"
    parts = ref.split(":")
    if len(parts) != 2:
        raise PluginCallError(f"Invalid entry ref '{ref}', expected 'plugin_id:entry_id'")
    plugin_id, entry_id = parts
    if not plugin_id or not entry_id:
        raise PluginCallError(f"Invalid entry ref '{ref}'")
    return plugin_id, entry_id


def _parse_event_ref(ref: str) -> Tuple[str, str, str]:
    # "plugin_id:event_type:event_id"
    parts = ref.split(":")
    if len(parts) != 3:
        raise PluginCallError(f"Invalid event ref '{ref}', expected 'plugin_id:event_type:event_id'")
    plugin_id, event_type, event_id = parts
    if not plugin_id or not event_type or not event_id:
        raise PluginCallError(f"Invalid event ref '{ref}'")
    return plugin_id, event_type, event_id


@dataclass
class Plugins:
    ctx: Any

    def list(self, filters: Optional[Dict[str, Any]] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "query_plugins"):
            raise PluginCallError("ctx.query_plugins is not available")
        return self.ctx.query_plugins(filters or {}, timeout=timeout)

    def call_entry(self, ref: str, args: Dict[str, Any], *, timeout: float = 10.0) -> Any:
        plugin_id, entry_id = _parse_entry_ref(ref)
        return self.call(plugin_id=plugin_id, event_type="plugin_entry", event_id=entry_id, args=args, timeout=timeout)

    def call_event(self, ref: str, args: Dict[str, Any], *, timeout: float = 10.0) -> Any:
        plugin_id, event_type, event_id = _parse_event_ref(ref)
        return self.call(plugin_id=plugin_id, event_type=event_type, event_id=event_id, args=args, timeout=timeout)

    def call(self, *, plugin_id: str, event_type: str, event_id: str, args: Dict[str, Any], timeout: float = 10.0) -> Any:
        if not hasattr(self.ctx, "trigger_plugin_event"):
            raise PluginCallError("ctx.trigger_plugin_event is not available")
        return self.ctx.trigger_plugin_event(
            target_plugin_id=plugin_id,
            event_type=event_type,
            event_id=event_id,
            args=args,
            timeout=timeout,
        )

    def require(self, plugin_id: str, *, timeout: float = 5.0) -> None:
        info = self.list({"include_events": False}, timeout=timeout)
        plugins = info.get("plugins", []) if isinstance(info, dict) else []
        if not any(isinstance(p, dict) and p.get("plugin_id") == plugin_id for p in plugins):
            raise PluginCallError(f"Required plugin '{plugin_id}' not found")
