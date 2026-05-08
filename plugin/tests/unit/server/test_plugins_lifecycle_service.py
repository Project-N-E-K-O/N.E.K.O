from __future__ import annotations

import copy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plugin.core import registry as registry_module
from plugin.server.application.plugins import query_service as query_module
from plugin.server.application.plugins import lifecycle_service as module
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import runtime_overrides as runtime_overrides_module
from plugin.sdk.plugin.decorators import plugin_entry


class _FakeProcessHost:
    def __init__(
        self,
        plugin_id: str,
        entry_point: str,
        config_path: Path,
        extension_configs: list | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.entry_point = entry_point
        self.config_path = config_path
        self.extension_configs = extension_configs
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


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, *_args, **_kwargs) -> None:
        return

    def debug(self, *_args, **_kwargs) -> None:
        return

    def warning(self, message, *args, **_kwargs) -> None:
        rendered = str(message)
        for arg in args:
            rendered = rendered.replace("{}", str(arg), 1)
        self.messages.append(rendered)

    def error(self, *_args, **_kwargs) -> None:
        return


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
def test_parse_single_plugin_config_warns_on_directory_id_mismatch(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "bilibili_danmaku"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'bilibili-danmaku'",
                "entry = 'plugin.plugins.bilibili_danmaku:Plugin'",
            ]
        ),
        encoding="utf-8",
    )

    messages: list[str] = []

    class _Logger:
        def info(self, *_args, **_kwargs) -> None:
            return

        def debug(self, *_args, **_kwargs) -> None:
            return

        def warning(self, message, *args, **_kwargs) -> None:
            rendered = str(message)
            for arg in args:
                rendered = rendered.replace("{}", str(arg), 1)
            messages.append(rendered)

        def error(self, *_args, **_kwargs) -> None:
            return

    parsed = registry_module._parse_single_plugin_config(
        config_path,
        set(),
        _Logger(),
    )

    assert parsed is not None
    assert any("directory name" in message and "does not match declared plugin.id" in message for message in messages)


@pytest.mark.plugin_unit
def test_parse_single_plugin_config_warns_on_noncanonical_fields(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo_plugin'",
                "entry = ' plugin.plugins.demo_plugin:Plugin '",
                "keywords = 'tag1'",
                "passive = 'yes'",
                "",
                "[plugin_runtime]",
                "enabled = 'true'",
                "auto_start = 'false'",
            ]
        ),
        encoding="utf-8",
    )

    messages: list[str] = []

    class _Logger:
        def info(self, *_args, **_kwargs) -> None:
            return

        def debug(self, *_args, **_kwargs) -> None:
            return

        def warning(self, message, *args, **_kwargs) -> None:
            rendered = str(message)
            for arg in args:
                rendered = rendered.replace("{}", str(arg), 1)
            messages.append(rendered)

        def error(self, *_args, **_kwargs) -> None:
            return

    parsed = registry_module._parse_single_plugin_config(
        config_path,
        set(),
        _Logger(),
    )

    assert parsed is not None
    assert any("[plugin].entry" in message and "leading/trailing whitespace" in message for message in messages)
    assert any("[plugin].keywords should be a string list" in message for message in messages)
    assert any("[plugin].passive" in message and "prefer true/false" in message for message in messages)
    assert any("[plugin_runtime].enabled" in message and "prefer true/false" in message for message in messages)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_plugin_refreshes_registry_before_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "refresh_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'refresh_adapter'",
                "name = 'Refresh Adapter'",
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
    refresh_calls: list[str] = []

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        async def _refresh_plugin(plugin_id: str) -> dict[str, object]:
            refresh_calls.append(plugin_id)
            return {"success": True, "plugin_id": plugin_id, "status": "added"}

        monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", _refresh_plugin)
        monkeypatch.setattr(module, "_get_plugin_config_path", lambda plugin_id: config_path)
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        response = await service.start_plugin("refresh_adapter")

        assert response["success"] is True
        assert refresh_calls == ["refresh_adapter"]
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
                "short_description = 'persist me'",
                "keywords = ['mcp']",
                "",
                "[plugin.sdk]",
                "supported = '>=0.1'",
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
            module.state.plugins["mcp_adapter"] = {
                "id": "mcp_adapter",
                "name": "MCP Adapter",
                "type": "adapter",
                "description": "",
                "version": "0.1.0",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
                "short_description": "persist me",
                "keywords": ["mcp"],
                "sdk_supported": ">=0.1",
            }
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
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

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
        assert plugin_meta["short_description"] == "persist me"
        assert plugin_meta["keywords"] == ["mcp"]
        assert plugin_meta["sdk_supported"] == ">=0.1"
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
async def test_start_plugin_logs_structured_config_warnings_from_resolver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "warn_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'warn_adapter'",
                "name = 'Warn Adapter'",
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
    capture_logger = _CaptureLogger()

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "_get_plugin_config_path", lambda plugin_id: config_path)
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [
                    {
                        "code": "PLUGIN_ENTRY_WHITESPACE",
                        "field": "plugin.entry",
                        "message": "entry has leading/trailing whitespace",
                        "severity": "warning",
                        "source": "semantic",
                    }
                ],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)
        monkeypatch.setattr(module, "logger", capture_logger)

        service = module.PluginLifecycleService()
        response = await service.start_plugin("warn_adapter")

        assert response["success"] is True
        assert any(
            "Plugin config warning [PLUGIN_ENTRY_WHITESPACE] field=plugin.entry msg=entry has leading/trailing whitespace"
            in message
            for message in capture_logger.messages
        )
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
async def test_start_plugin_maps_resolver_http_exception_to_profile_domain_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\nentry='demo:Plugin'\n", encoding="utf-8")

    monkeypatch.setattr(module, "_get_plugin_config_path", lambda plugin_id: config_path)

    def _raise_profile_error(*args, **kwargs):
        raise HTTPException(status_code=422, detail="profile merge failed")

    monkeypatch.setattr(module, "resolve_plugin_config_from_path", _raise_profile_error)

    service = module.PluginLifecycleService()

    with pytest.raises(ServerDomainError) as exc_info:
        await service.start_plugin("demo")

    assert exc_info.value.code == "PLUGIN_CONFIG_PROFILE_FAILED"
    assert exc_info.value.status_code == 422
    assert exc_info.value.message == "profile merge failed"
    assert exc_info.value.details["plugin_id"] == "demo"


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
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

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


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_plugin_passes_prebuilt_extension_configs_to_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_config_path = tmp_path / "host_plugin" / "plugin.toml"
    ext_config_path = tmp_path / "demo_ext" / "plugin.toml"
    host_config_path.parent.mkdir(parents=True, exist_ok=True)
    ext_config_path.parent.mkdir(parents=True, exist_ok=True)
    host_config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'host_plugin'",
                "name = 'Host Plugin'",
                "type = 'plugin'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
            ]
        ),
        encoding="utf-8",
    )
    ext_config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo_ext'",
                "name = 'Demo Extension'",
                "type = 'extension'",
                "entry = 'tests.fake_ext:DemoExtRouter'",
                "",
                "[plugin.host]",
                "plugin_id = 'host_plugin'",
                "prefix = '/demo'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
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
            module.state.plugins["host_plugin"] = {
                "id": "host_plugin",
                "name": "Host Plugin",
                "type": "plugin",
                "description": "",
                "version": "0.1.0",
                "config_path": str(host_config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
                "short_description": "keep me",
                "keywords": ["host"],
                "sdk_supported": ">=0.1",
                "runtime_enabled": True,
                "runtime_auto_start": True,
            }
            module.state.plugins["demo_ext"] = {
                "id": "demo_ext",
                "name": "Demo Extension",
                "type": "extension",
                "config_path": str(ext_config_path),
                "entry_point": "tests.fake_ext:DemoExtRouter",
                "host_plugin_id": "host_plugin",
                "runtime_enabled": True,
                "runtime_auto_start": False,
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
        monkeypatch.setattr(module.importlib, "import_module", lambda _: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        response = await service.start_plugin("host_plugin", refresh_registry=False)

        assert response["success"] is True
        with module.state.acquire_plugin_hosts_read_lock():
            host_obj = module.state.plugin_hosts["host_plugin"]
        assert isinstance(host_obj, _FakeProcessHost)
        assert host_obj.extension_configs == [
            {
                "ext_id": "demo_ext",
                "ext_entry": "tests.fake_ext:DemoExtRouter",
                "prefix": "/demo",
            }
        ]

        with module.state.acquire_plugins_read_lock():
            plugin_meta = dict(module.state.plugins["host_plugin"])
        assert plugin_meta["short_description"] == "keep me"
        assert plugin_meta["keywords"] == ["host"]
        assert plugin_meta["sdk_supported"] == ">=0.1"
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
def test_parse_single_plugin_config_uses_resolver_effective_config_and_logs_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo_plugin'",
                "entry = 'demo.module:Plugin'",
                "name = 'Base Name'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
            ]
        ),
        encoding="utf-8",
    )

    capture_logger = _CaptureLogger()

    monkeypatch.setattr(
        registry_module,
        "resolve_plugin_config_from_path",
        lambda *args, **kwargs: {
            "effective_config": {
                "plugin": {
                    "id": "demo_plugin",
                    "entry": "demo.module:Plugin",
                    "name": "Overlay Name",
                    "description": "resolved by profile",
                },
                "plugin_runtime": {
                    "enabled": False,
                    "auto_start": False,
                },
            },
            "warnings": [
                {
                    "code": "PLUGIN_KEYWORDS_NON_LIST",
                    "field": "plugin.keywords",
                    "message": "keywords should be a string list",
                    "severity": "warning",
                    "source": "semantic",
                }
            ],
        },
    )

    parsed = registry_module._parse_single_plugin_config(
        config_path,
        set(),
        capture_logger,
    )

    assert parsed is not None
    assert parsed.pdata["name"] == "Overlay Name"
    assert parsed.pdata["description"] == "resolved by profile"
    assert parsed.enabled is False
    assert parsed.auto_start is False
    assert any(
        "Plugin config warning [PLUGIN_KEYWORDS_NON_LIST] field=plugin.keywords msg=keywords should be a string list"
        in message
        for message in capture_logger.messages
    )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_plugin_removes_directory_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    config_path = plugin_dir / "plugin.toml"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo_plugin'\nentry='tests.fake:Plugin'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    refresh_calls: list[str] = []
    events: list[dict[str, object]] = []

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo_plugin"] = {
                "id": "demo_plugin",
                "name": "Demo Plugin",
                "type": "plugin",
                "config_path": str(config_path),
                "entry_point": "tests.fake:Plugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers["demo_plugin.plugin_entry:run"] = object()

        async def _refresh_registry() -> dict[str, object]:
            refresh_calls.append("refresh")
            return {"success": True}

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (tmp_path,))
        monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: events.append(dict(event)))

        service = module.PluginLifecycleService()
        response = await service.delete_plugin("demo_plugin")

        assert response["success"] is True
        assert response["plugin_id"] == "demo_plugin"
        assert response["deleted_from_disk"] is True
        assert refresh_calls == ["refresh"]
        assert plugin_dir.exists() is False
        with module.state.acquire_plugins_read_lock():
            assert "demo_plugin" not in module.state.plugins
        with module.state.acquire_event_handlers_read_lock():
            assert "demo_plugin.plugin_entry:run" not in module.state.event_handlers
        assert any(event.get("type") == "plugin_deleted" for event in events)
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
async def test_delete_plugin_stops_running_host_before_removing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "running_plugin"
    config_path = plugin_dir / "plugin.toml"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='running_plugin'\nentry='tests.fake:Plugin'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    stop_calls: list[str] = []

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["running_plugin"] = {
                "id": "running_plugin",
                "name": "Running Plugin",
                "type": "plugin",
                "config_path": str(config_path),
                "entry_point": "tests.fake:Plugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts["running_plugin"] = _FakeProcessHost(
                plugin_id="running_plugin",
                entry_point="tests.fake:Plugin",
                config_path=config_path,
            )
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        async def _refresh_registry() -> dict[str, object]:
            return {"success": True}

        original_stop_plugin = module.PluginLifecycleService.stop_plugin

        async def _tracked_stop(self, plugin_id: str) -> dict[str, object]:
            stop_calls.append(plugin_id)
            return await original_stop_plugin(self, plugin_id)

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (tmp_path,))
        monkeypatch.setattr(module.PluginLifecycleService, "stop_plugin", _tracked_stop)
        monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        response = await service.delete_plugin("running_plugin")

        assert response["success"] is True
        assert stop_calls == ["running_plugin"]
        assert plugin_dir.exists() is False
        with module.state.acquire_plugin_hosts_read_lock():
            assert "running_plugin" not in module.state.plugin_hosts
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
async def test_delete_extension_disables_runtime_when_host_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ext_dir = tmp_path / "demo_ext"
    config_path = ext_dir / "plugin.toml"
    ext_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo_ext'",
                "type = 'extension'",
                "entry = 'tests.fake_ext:Plugin'",
                "",
                "[plugin.host]",
                "plugin_id = 'host_plugin'",
            ]
        ),
        encoding="utf-8",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    disabled_calls: list[str] = []

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo_ext"] = {
                "id": "demo_ext",
                "name": "Demo Extension",
                "type": "extension",
                "config_path": str(config_path),
                "entry_point": "tests.fake_ext:Plugin",
                "host_plugin_id": "host_plugin",
                "runtime_enabled": True,
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts["host_plugin"] = _FakeProcessHost(
                plugin_id="host_plugin",
                entry_point="tests.fake:Host",
                config_path=tmp_path / "host_plugin" / "plugin.toml",
            )
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        async def _refresh_registry() -> dict[str, object]:
            return {"success": True}

        original_disable_extension = module.PluginLifecycleService.disable_extension

        async def _tracked_disable(self, ext_id: str) -> dict[str, object]:
            disabled_calls.append(ext_id)
            return await original_disable_extension(self, ext_id)

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (tmp_path,))
        monkeypatch.setattr(module.PluginLifecycleService, "disable_extension", _tracked_disable)
        monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        response = await service.delete_plugin("demo_ext")

        assert response["success"] is True
        assert response["host_plugin_id"] == "host_plugin"
        assert disabled_calls == ["demo_ext"]
        assert ext_dir.exists() is False
        with module.state.acquire_plugins_read_lock():
            assert "demo_ext" not in module.state.plugins
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
async def test_delete_plugin_rejects_host_with_bound_extensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_dir = tmp_path / "host_plugin"
    host_config_path = host_dir / "plugin.toml"
    host_dir.mkdir(parents=True, exist_ok=True)
    host_config_path.write_text("[plugin]\nid='host_plugin'\nentry='tests.fake:Host'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["host_plugin"] = {
                "id": "host_plugin",
                "name": "Host Plugin",
                "type": "plugin",
                "config_path": str(host_config_path),
                "entry_point": "tests.fake:Host",
            }
            module.state.plugins["demo_ext"] = {
                "id": "demo_ext",
                "name": "Demo Extension",
                "type": "extension",
                "config_path": str(tmp_path / "demo_ext" / "plugin.toml"),
                "entry_point": "tests.fake_ext:Plugin",
                "host_plugin_id": "host_plugin",
                "runtime_enabled": True,
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (tmp_path,))

        service = module.PluginLifecycleService()
        with pytest.raises(ServerDomainError) as exc_info:
            await service.delete_plugin("host_plugin")

        assert exc_info.value.code == "PLUGIN_DELETE_BLOCKED_BY_EXTENSIONS"
        assert exc_info.value.status_code == 409
        assert "demo_ext" in exc_info.value.message
        assert host_dir.exists() is True
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


def _seed_extension(config_path: Path, host_plugin_id: str = "host_plugin") -> None:
    with module.state.acquire_plugins_write_lock():
        module.state.plugins.clear()
        module.state.plugins["demo_ext"] = {
            "id": "demo_ext",
            "name": "Demo Extension",
            "type": "extension",
            "config_path": str(config_path),
            "entry_point": "tests.fake_ext:Plugin",
            "host_plugin_id": host_plugin_id,
            "runtime_enabled": True,
        }
    with module.state.acquire_plugin_hosts_write_lock():
        module.state.plugin_hosts.clear()
        module.state.plugin_hosts[host_plugin_id] = _FakeProcessHost(
            plugin_id=host_plugin_id,
            entry_point="tests.fake:Host",
            config_path=config_path.parent.parent / host_plugin_id / "plugin.toml",
        )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_disable_and_enable_extension_persist_runtime_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolate_runtime_overrides: dict,
) -> None:
    config_path = tmp_path / "demo_ext" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo_ext'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        _seed_extension(config_path)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        await service.disable_extension("demo_ext")
        assert _isolate_runtime_overrides == {"demo_ext": False}
        assert runtime_overrides_module.get_runtime_override("demo_ext") is False

        await service.enable_extension("demo_ext")
        assert _isolate_runtime_overrides == {"demo_ext": True}
        assert runtime_overrides_module.get_runtime_override("demo_ext") is True
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_delete_plugin_clears_runtime_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolate_runtime_overrides: dict,
) -> None:
    plugin_dir = tmp_path / "demo_plugin"
    config_path = plugin_dir / "plugin.toml"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "[plugin]\nid='demo_plugin'\nentry='tests.fake:Plugin'\n",
        encoding="utf-8",
    )

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["demo_plugin"] = {
                "id": "demo_plugin",
                "name": "Demo",
                "type": "plugin",
                "config_path": str(config_path),
                "entry_point": "tests.fake:Plugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        runtime_overrides_module.set_runtime_override("demo_plugin", False)
        assert _isolate_runtime_overrides == {"demo_plugin": False}

        async def _refresh_registry() -> dict[str, object]:
            return {"success": True}

        monkeypatch.setattr(module, "PLUGIN_CONFIG_ROOTS", (tmp_path,))
        monkeypatch.setattr(module.plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        await service.delete_plugin("demo_plugin")

        assert _isolate_runtime_overrides == {}
        assert runtime_overrides_module.get_runtime_override("demo_plugin") is None
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
