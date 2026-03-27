from __future__ import annotations

import copy
import time

from plugin._types.models import PluginMeta
from plugin.core import registry as module
from plugin.sdk.plugin.decorators import plugin_entry


class _RegistryCachePlugin:
    @plugin_entry(id="demo_entry", name="Demo Entry")
    async def demo_entry(self) -> dict[str, object]:
        return {"ok": True}


def test_register_plugin_invalidates_plugins_snapshot_cache() -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        now = time.time()
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache["plugins"] = {"data": {}, "timestamp": now}

        resolved_id = module.register_plugin(
            PluginMeta(
                id="demo_registry",
                name="Demo Registry",
                type="plugin",
                description="",
                version="0.1.0",
                sdk_version="test",
            )
        )

        snapshot = module.state.get_plugins_snapshot_cached()
        assert resolved_id == "demo_registry"
        assert "demo_registry" in snapshot
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


def test_scan_static_metadata_invalidates_handlers_snapshot_cache() -> None:
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
        now = time.time()
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache["handlers"] = {"data": {}, "timestamp": now}

        module.scan_static_metadata(
            "demo_registry",
            _RegistryCachePlugin,
            conf={},
            pdata={},
        )

        snapshot = module.state.get_event_handlers_snapshot_cached()
        assert "demo_registry.demo_entry" in snapshot
    finally:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup
