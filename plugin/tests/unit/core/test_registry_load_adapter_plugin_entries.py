from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from plugin.core import registry as module
from plugin.server.application.plugins import query_service as query_module
from plugin.sdk.plugin.decorators import plugin_entry


class _FakeAdapterPlugin:
    @plugin_entry(id="list_servers", name="List Servers", description="List configured MCP servers")
    async def list_servers(self) -> dict[str, object]:
        return {"servers": []}


class _FakeHost:
    def __init__(self) -> None:
        self.process = SimpleNamespace(pid=1234, is_alive=lambda: True)

    def is_alive(self) -> bool:
        return True


def test_load_adapter_plugin_registers_entries_preview_and_handlers() -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
        module.state.invalidate_snapshot_cache()

        ctx = module.PluginContext(
            pid="mcp_adapter",
            conf={"adapter": {"mode": "gateway"}},
            pdata={
                "id": "mcp_adapter",
                "name": "MCP Adapter",
                "type": "adapter",
                "description": "desc",
                "version": "0.1.0",
            },
            toml_path=Path("/tmp/mcp_adapter/plugin.toml"),
            entry="tests.fake_mcp:FakeAdapterPlugin",
            sdk_supported_str=">=0.1.0,<0.3.0",
            sdk_recommended_str=">=0.1.0,<0.2.0",
            sdk_untested_str=None,
            sdk_conflicts_list=[],
            dependencies=[],
            enabled=True,
            auto_start=True,
        )

        original_import_module = module.importlib.import_module
        original_register_plugin = module.register_plugin
        try:
            module.importlib.import_module = lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin)

            def _register_plugin(plugin_meta, logger=None, config_path=None, entry_point=None):
                plugin_dump = plugin_meta.model_dump()
                if config_path is not None:
                    plugin_dump["config_path"] = str(config_path)
                if entry_point is not None:
                    plugin_dump["entry_point"] = entry_point
                with module.state.acquire_plugins_write_lock():
                    module.state.plugins[plugin_meta.id] = plugin_dump
                module.state.invalidate_snapshot_cache("plugins")
                return plugin_meta.id

            module.register_plugin = _register_plugin

            host = module._load_adapter_plugin(
                ctx,
                logger=module._DEFAULT_LOGGER,
                process_host_factory=lambda *args, **kwargs: _FakeHost(),
            )
        finally:
            module.importlib.import_module = original_import_module
            module.register_plugin = original_register_plugin

        assert host is not None

        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["mcp_adapter"])
        assert [entry["id"] for entry in plugin_meta["entries_preview"]] == ["list_servers"]

        with module.state.acquire_event_handlers_read_lock():
            assert "mcp_adapter.list_servers" in module.state.event_handlers

        plugin_list = query_module._build_plugin_list_sync()
        plugin_info = next(item for item in plugin_list if item["id"] == "mcp_adapter")
        assert [entry["id"] for entry in plugin_info["entries"]] == ["list_servers"]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


def test_load_adapter_plugin_rolls_back_scanned_handlers_when_register_fails() -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    method_map_backup = dict(module.plugin_entry_method_map)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
        module.plugin_entry_method_map.clear()
        module.state.invalidate_snapshot_cache()

        ctx = module.PluginContext(
            pid="mcp_adapter",
            conf={"adapter": {"mode": "gateway"}},
            pdata={
                "id": "mcp_adapter",
                "name": "MCP Adapter",
                "type": "adapter",
                "description": "desc",
                "version": "0.1.0",
            },
            toml_path=Path("/tmp/mcp_adapter/plugin.toml"),
            entry="tests.fake_mcp:FakeAdapterPlugin",
            sdk_supported_str=">=0.1.0,<0.3.0",
            sdk_recommended_str=">=0.1.0,<0.2.0",
            sdk_untested_str=None,
            sdk_conflicts_list=[],
            dependencies=[],
            enabled=True,
            auto_start=True,
        )

        original_import_module = module.importlib.import_module
        original_register_plugin = module.register_plugin
        try:
            module.importlib.import_module = lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin)
            module.register_plugin = lambda *args, **kwargs: None

            host = module._load_adapter_plugin(
                ctx,
                logger=module._DEFAULT_LOGGER,
                process_host_factory=lambda *args, **kwargs: _FakeHost(),
            )
        finally:
            module.importlib.import_module = original_import_module
            module.register_plugin = original_register_plugin

        assert host is None
        with module.state.acquire_event_handlers_read_lock():
            assert "mcp_adapter.list_servers" not in module.state.event_handlers
        assert ("mcp_adapter", "list_servers") not in module.plugin_entry_method_map
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        module.plugin_entry_method_map.clear()
        module.plugin_entry_method_map.update(method_map_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup
