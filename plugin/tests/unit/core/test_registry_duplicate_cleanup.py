from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from plugin.core import registry as module
from plugin.sdk.plugin.decorators import plugin_entry


class _DuplicateCleanupPlugin:
    @plugin_entry(id="demo_entry", name="Demo Entry")
    async def demo_entry(self) -> dict[str, object]:
        return {"ok": True}


class _DuplicateCleanupHost:
    def __init__(self) -> None:
        self.process = SimpleNamespace(pid=4321, is_alive=lambda: True)
        self.shutdown_called = False

    def is_alive(self) -> bool:
        return True


def test_load_plugins_from_roots_rolls_back_scanned_metadata_when_register_plugin_returns_none() -> None:
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
            pid="demo_plugin",
            conf={},
            pdata={
                "id": "demo_plugin",
                "name": "Demo Plugin",
                "type": "plugin",
                "description": "desc",
                "version": "0.1.0",
            },
            toml_path=Path("/tmp/demo_plugin/plugin.toml"),
            entry="tests.fake_plugin:DuplicateCleanupPlugin",
            sdk_supported_str=">=0.1.0,<0.3.0",
            sdk_recommended_str=">=0.1.0,<0.2.0",
            sdk_untested_str=None,
            sdk_conflicts_list=[],
            dependencies=[],
            enabled=True,
            auto_start=True,
        )

        original_import_module = module.importlib.import_module
        original_collect = module._collect_plugin_contexts_from_roots
        original_sort = module._topological_sort_plugins
        original_prepare = module._prepare_plugin_import_roots
        original_check_loaded = module._check_plugin_already_loaded
        original_check_registered = module._check_plugin_already_registered
        original_resolve_conflict = module._resolve_plugin_id_conflict
        original_register_plugin = module.register_plugin
        original_shutdown_host = module._shutdown_host_safely
        shutdown_calls: list[str] = []

        try:
            module.importlib.import_module = lambda _: SimpleNamespace(DuplicateCleanupPlugin=_DuplicateCleanupPlugin)
            module._collect_plugin_contexts_from_roots = lambda roots, logger: ([ctx], {ctx.pid: ctx})
            module._topological_sort_plugins = lambda contexts, pid_to_context, logger: [ctx.pid]
            module._prepare_plugin_import_roots = lambda roots, logger: None
            module._check_plugin_already_loaded = lambda pid, toml_path, logger: False
            module._check_plugin_already_registered = lambda pid, toml_path, logger: False
            module._resolve_plugin_id_conflict = (
                lambda pid, logger, config_path=None, entry_point=None, plugin_data=None, purpose="load", enable_rename=None: pid
            )
            module.register_plugin = lambda plugin_meta, logger=None, config_path=None, entry_point=None: None

            def _shutdown_host_safely(host, logger, plugin_id):
                shutdown_calls.append(plugin_id)

            module._shutdown_host_safely = _shutdown_host_safely

            module.load_plugins_from_roots(
                [Path("/tmp/plugins")],
                logger=module._DEFAULT_LOGGER,
                process_host_factory=lambda *args, **kwargs: _DuplicateCleanupHost(),
            )
        finally:
            module.importlib.import_module = original_import_module
            module._collect_plugin_contexts_from_roots = original_collect
            module._topological_sort_plugins = original_sort
            module._prepare_plugin_import_roots = original_prepare
            module._check_plugin_already_loaded = original_check_loaded
            module._check_plugin_already_registered = original_check_registered
            module._resolve_plugin_id_conflict = original_resolve_conflict
            module.register_plugin = original_register_plugin
            module._shutdown_host_safely = original_shutdown_host

        assert shutdown_calls == ["demo_plugin"]
        with module.state.acquire_event_handlers_read_lock():
            assert "demo_plugin.demo_entry" not in module.state.event_handlers
            assert "demo_plugin:plugin_entry:demo_entry" not in module.state.event_handlers
        assert ("demo_plugin", "demo_entry") not in module.plugin_entry_method_map
        with module.state.acquire_plugin_hosts_read_lock():
            assert "demo_plugin" not in module.state.plugin_hosts
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
