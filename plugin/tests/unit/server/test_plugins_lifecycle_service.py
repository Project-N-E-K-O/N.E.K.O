from __future__ import annotations

import copy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.plugins import query_service as query_module
from plugin.server.application.plugins import lifecycle_service as module
from plugin.sdk.plugin.decorators import plugin_entry


class _FakeProcessHost:
    def __init__(self, plugin_id: str, entry_point: str, config_path: Path) -> None:
        self.plugin_id = plugin_id
        self.entry_point = entry_point
        self.config_path = config_path
        self.process = SimpleNamespace(is_alive=lambda: True, exitcode=None)
        self.started = False
        self.stopped = False

    async def start(self, message_target_queue: object) -> None:
        self.started = True

    async def shutdown(self, timeout: float = module.PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        self.stopped = True

    async def send_extension_command(
        self,
        msg_type: str,
        payload: dict[str, object],
        timeout: float = 10.0,
    ) -> object:
        return {"ok": True, "type": msg_type, "payload": payload, "timeout": timeout}

    def is_alive(self) -> bool:
        return True


class _FakeAdapterPlugin:
    @plugin_entry(id="list_servers", name="List Servers", description="List configured MCP servers")
    async def list_servers(self) -> dict[str, object]:
        return {"servers": []}


@pytest.mark.plugin_unit
def test_get_plugin_config_path_returns_existing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    config_file = root / "demo" / "plugin.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    resolved = module._get_plugin_config_path("demo")
    assert resolved == config_file.resolve()


@pytest.mark.plugin_unit
@pytest.mark.parametrize("plugin_id", ["../evil", "a/b", "", "  ", "demo..", "demo/"])
def test_get_plugin_config_path_rejects_invalid_plugin_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    plugin_id: str,
) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    assert module._get_plugin_config_path(plugin_id) is None


@pytest.mark.plugin_unit
def test_get_plugin_config_path_returns_none_for_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (root,))

    assert module._get_plugin_config_path("demo") is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_plugin_persists_entries_preview_and_invalidates_stale_caches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'mcp_adapter'",
                "name = 'MCP Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
            ]
        ),
        encoding="utf-8",
    )

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

        now = time.time()
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache["plugins"] = {"data": {}, "timestamp": now}
            module.state._snapshot_cache["hosts"] = {"data": {}, "timestamp": now}
            module.state._snapshot_cache["handlers"] = {"data": {}, "timestamp": now}

        monkeypatch.setattr(module, "_get_plugin_config_path", lambda plugin_id: config_path)
        monkeypatch.setattr(module, "apply_user_config_profiles", lambda **kwargs: kwargs["base_config"])
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        def _register_plugin(plugin_meta, logger, config_path=None, entry_point=None):
            plugin_dump = plugin_meta.model_dump()
            if config_path is not None:
                plugin_dump["config_path"] = str(config_path)
            if entry_point is not None:
                plugin_dump["entry_point"] = entry_point
            with module.state.acquire_plugins_write_lock():
                module.state.plugins[plugin_meta.id] = plugin_dump
            return plugin_meta.id

        monkeypatch.setattr(module, "register_plugin", _register_plugin)

        service = module.PluginLifecycleService()
        response = await service.start_plugin("mcp_adapter")

        assert response["success"] is True
        assert response["plugin_id"] == "mcp_adapter"

        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["mcp_adapter"])
        assert plugin_meta["runtime_enabled"] is True
        assert plugin_meta["runtime_auto_start"] is False
        assert [entry["id"] for entry in plugin_meta["entries_preview"]] == ["list_servers"]

        plugin_list = query_module._build_plugin_list_sync()
        plugin_info = next(item for item in plugin_list if item["id"] == "mcp_adapter")
        assert plugin_info["status"] == "running"
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


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_plugin_allows_retry_for_load_failed_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "broken_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'broken_adapter'",
                "name = 'Broken Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
            ]
        ),
        encoding="utf-8",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["broken_adapter"] = {
                "id": "broken_adapter",
                "name": "Broken Adapter",
                "type": "adapter",
                "description": "",
                "version": "0.1.0",
                "sdk_version": "test",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
                "runtime_load_state": "failed",
                "runtime_load_error_message": "Missing Python dependencies: ['demo-lib>=2']",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "_get_plugin_config_path", lambda plugin_id: config_path)
        monkeypatch.setattr(module, "apply_user_config_profiles", lambda **kwargs: kwargs["base_config"])
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        def _register_plugin(plugin_meta, logger, config_path=None, entry_point=None):
            plugin_dump = plugin_meta.model_dump()
            if config_path is not None:
                plugin_dump["config_path"] = str(config_path)
            if entry_point is not None:
                plugin_dump["entry_point"] = entry_point
            with module.state.acquire_plugins_write_lock():
                module.state.plugins[plugin_meta.id] = plugin_dump
            return plugin_meta.id

        monkeypatch.setattr(module, "register_plugin", _register_plugin)

        service = module.PluginLifecycleService()
        response = await service.start_plugin("broken_adapter")

        assert response["success"] is True
        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["broken_adapter"])
        assert plugin_meta["runtime_enabled"] is True
        assert "runtime_load_state" not in plugin_meta
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
