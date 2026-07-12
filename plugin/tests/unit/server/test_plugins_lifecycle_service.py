from __future__ import annotations

import asyncio
import copy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plugin._types.exceptions import PluginLifecycleError
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

    async def start(self, message_target_queue: object, startup_timeout: float | None = None) -> None:
        self.startup_timeout = startup_timeout
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
        self.errors: list[str] = []

    def info(self, *_args, **_kwargs) -> None:
        return

    def debug(self, *_args, **_kwargs) -> None:
        return

    def warning(self, message, *args, **_kwargs) -> None:
        rendered = str(message)
        for arg in args:
            rendered = rendered.replace("{}", str(arg), 1)
        self.messages.append(rendered)

    def error(self, message, *args, **_kwargs) -> None:
        rendered = str(message)
        for arg in args:
            rendered = rendered.replace("{}", str(arg), 1)
        self.errors.append(rendered)


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
def test_parse_single_plugin_config_rejects_removed_script_type(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "legacy_script"
    plugin_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'legacy_script'",
                "type = 'script'",
                "entry = 'plugin.plugins.legacy_script:Plugin'",
            ]
        ),
        encoding="utf-8",
    )
    logger = _CaptureLogger()

    parsed = registry_module._parse_single_plugin_config(config_path, set(), logger)

    assert parsed is None
    assert any("unsupported type='script'" in message for message in logger.errors)


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
def test_normalize_startup_failure_policy_defaults_none_to_warn() -> None:
    assert module._normalize_startup_failure_policy(None, plugin_id="demo") == "warn"


@pytest.mark.plugin_unit
@pytest.mark.parametrize("raw_value", ["", False, 0])
def test_normalize_startup_failure_policy_rejects_falsy_non_default_values(raw_value: object) -> None:
    with pytest.raises(ServerDomainError) as exc_info:
        module._normalize_startup_failure_policy(raw_value, plugin_id="demo")

    assert exc_info.value.code == "INVALID_PLUGIN_CONFIG"
    assert exc_info.value.details["error_type"] == "InvalidStartupFailurePolicy"
    assert "startup_failure" in exc_info.value.message


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
        monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
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
async def test_start_plugin_persist_user_intent_clears_stop_override_before_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolate_runtime_overrides: dict,
) -> None:
    config_path = tmp_path / "demo_plugin" / "plugin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'demo_plugin'",
                "name = 'Demo Plugin'",
                "entry = 'tests.fake:Plugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = true",
            ]
        ),
        encoding="utf-8",
    )

    seen: dict[str, object] = {}

    async def _refresh_plugin(plugin_id: str) -> dict[str, object]:
        seen["override_at_refresh"] = runtime_overrides_module.get_runtime_override(plugin_id)
        return {"success": True, "plugin_id": plugin_id}

    async def _list_extension_configs_for_host(_plugin_id: str) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", _refresh_plugin)
    monkeypatch.setattr(module.plugin_registry_service, "list_extension_configs_for_host", _list_extension_configs_for_host)
    monkeypatch.setattr(module, "_get_plugin_config_path", lambda _plugin_id: config_path)
    monkeypatch.setattr(
        module,
        "resolve_plugin_config_from_path",
        lambda *args, **kwargs: {
            "effective_config": kwargs["base_config"],
            "warnings": [],
        },
    )
    monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
    monkeypatch.setattr(module, "_find_missing_python_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
    monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(Plugin=type("Plugin", (), {})))
    monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()

        runtime_overrides_module.set_runtime_override("demo_plugin", False)

        response = await module.PluginLifecycleService().start_plugin("demo_plugin", persist_user_intent=True)

        assert response["success"] is True
        assert seen["override_at_refresh"] is True
        assert _isolate_runtime_overrides == {"demo_plugin": True}
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
async def test_start_plugin_checks_python_requirements_against_vendor_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "vendor_adapter" / "plugin.toml"
    vendor_dir = config_path.parent / "vendor"
    vendor_dir.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'vendor_adapter'",
                "name = 'Vendor Adapter'",
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
    (config_path.parent / "pyproject.toml").write_text(
        '[project]\ndependencies = ["demo-lib>=2"]\n',
        encoding="utf-8",
    )

    seen: dict[str, object] = {}

    def _fake_find_missing(requirements, *, search_paths=None):
        seen["requirements"] = list(requirements)
        seen["search_paths"] = list(search_paths or [])
        return []

    monkeypatch.setattr(module.plugin_registry_service, "refresh_plugin", lambda _plugin_id: {"success": True})
    monkeypatch.setattr(module, "_get_plugin_config_path", lambda _plugin_id: config_path)
    monkeypatch.setattr(
        module,
        "resolve_plugin_config_from_path",
        lambda *args, **kwargs: {
            "effective_config": kwargs["base_config"],
            "warnings": [],
        },
    )
    monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
    monkeypatch.setattr(module, "_find_missing_python_requirements", _fake_find_missing)
    monkeypatch.setattr(module, "PluginProcessHost", _FakeProcessHost)
    monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
    monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()

        response = await module.PluginLifecycleService().start_plugin("vendor_adapter", refresh_registry=False)

        assert response["success"] is True
        assert seen["requirements"] == ["demo-lib>=2"]
        assert seen["search_paths"] == [vendor_dir]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_start_plugin_rejects_entry_directory_mismatch_before_creating_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "repo_file_manager" / "plugin.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'file_manager'",
                "name = 'File Manager'",
                "type = 'plugin'",
                "entry = 'plugins.file_manager:FileManagerPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
            ]
        ),
        encoding="utf-8",
    )
    host_created = False

    class _UnexpectedHost(_FakeProcessHost):
        def __init__(self, *args, **kwargs) -> None:
            nonlocal host_created
            host_created = True
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        module,
        "resolve_plugin_config_from_path",
        lambda *args, **kwargs: {
            "effective_config": kwargs["base_config"],
            "warnings": [],
        },
    )
    monkeypatch.setattr(module, "PluginProcessHost", _UnexpectedHost)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["file_manager"] = {
                "id": "file_manager",
                "config_path": str(config_path),
                "runtime_enabled": True,
                "runtime_auto_start": False,
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("file_manager", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_ENTRY_DIRECTORY_MISMATCH"
        assert exc_info.value.status_code == 400
        assert "repo_file_manager" in exc_info.value.message
        assert host_created is False
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
async def test_start_plugin_uses_default_startup_timeout_when_runtime_timeout_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "default_timeout_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'default_timeout_adapter'",
                "name = 'Default Timeout Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
            ]
        ),
        encoding="utf-8",
    )

    class _RecordingHost(_FakeProcessHost):
        instances: list["_RecordingHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _RecordingHost.instances.append(self)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["default_timeout_adapter"] = {
                "id": "default_timeout_adapter",
                "name": "Default Timeout Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "PLUGIN_STARTUP_TIMEOUT", 0.01, raising=False)
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _RecordingHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        response = await module.PluginLifecycleService().start_plugin(
            "default_timeout_adapter",
            refresh_registry=False,
        )

        assert response["success"] is True
        assert _RecordingHost.instances
        assert _RecordingHost.instances[0].startup_timeout == 0.01
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
@pytest.mark.parametrize("timeout_literal", ["0", "300.1"])
async def test_start_plugin_rejects_invalid_runtime_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout_literal: str,
) -> None:
    config_path = tmp_path / "invalid_timeout_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'invalid_timeout_adapter'",
                "name = 'Invalid Timeout Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                f"timeout = {timeout_literal}",
            ]
        ),
        encoding="utf-8",
    )

    class _RecordingHost(_FakeProcessHost):
        instances: list["_RecordingHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _RecordingHost.instances.append(self)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["invalid_timeout_adapter"] = {
                "id": "invalid_timeout_adapter",
                "name": "Invalid Timeout Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _RecordingHost)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin(
                "invalid_timeout_adapter",
                refresh_registry=False,
            )

        assert exc_info.value.code == "INVALID_PLUGIN_CONFIG"
        assert "timeout" in exc_info.value.message
        assert _RecordingHost.instances == []
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
async def test_start_plugin_rejects_invalid_default_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "invalid_default_timeout_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'invalid_default_timeout_adapter'",
                "name = 'Invalid Default Timeout Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
            ]
        ),
        encoding="utf-8",
    )

    class _RecordingHost(_FakeProcessHost):
        instances: list["_RecordingHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _RecordingHost.instances.append(self)

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["invalid_default_timeout_adapter"] = {
                "id": "invalid_default_timeout_adapter",
                "name": "Invalid Default Timeout Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(module, "PLUGIN_STARTUP_TIMEOUT", 300.1, raising=False)
        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _RecordingHost)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin(
                "invalid_default_timeout_adapter",
                refresh_registry=False,
            )

        assert exc_info.value.code == "INVALID_PLUGIN_CONFIG"
        assert exc_info.value.details["error_type"] == "InvalidStartupTimeout"
        assert "PLUGIN_STARTUP_TIMEOUT" in exc_info.value.message
        assert _RecordingHost.instances == []
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
async def test_start_plugin_defaults_startup_failure_to_warn_and_marks_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "warn_startup_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'warn_startup_adapter'",
                "name = 'Warn Startup Adapter'",
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

    class _StartupWarningHost(_FakeProcessHost):
        instances: list["_StartupWarningHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _StartupWarningHost.instances.append(self)

        async def start(
            self,
            message_target_queue: object,
            startup_timeout: float | None = None,
            startup_failure: str = "warn",
        ) -> dict[str, object]:
            self.startup_timeout = startup_timeout
            self.startup_failure = startup_failure
            self.started = True
            return {"status": "failed", "startup_error": "lifecycle.startup failed"}

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["warn_startup_adapter"] = {
                "id": "warn_startup_adapter",
                "name": "Warn Startup Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _StartupWarningHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        response = await module.PluginLifecycleService().start_plugin(
            "warn_startup_adapter",
            refresh_registry=False,
        )

        assert response["success"] is True
        assert response["startup_degraded"] is True
        assert response["startup_error"] == "lifecycle.startup failed"
        assert _StartupWarningHost.instances[0].startup_failure == "warn"
        assert _StartupWarningHost.instances[0].stopped is False
        with module.state.acquire_plugins_read_lock():
            meta = module.state.plugins["warn_startup_adapter"]
        assert meta["runtime_startup_state"] == "degraded"
        assert meta["runtime_startup_error"] == "lifecycle.startup failed"
        with module.state.acquire_plugin_hosts_read_lock():
            assert "warn_startup_adapter" in module.state.plugin_hosts
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
async def test_start_plugin_startup_failure_fail_keeps_startup_error_fatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "fail_startup_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'fail_startup_adapter'",
                "name = 'Fail Startup Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "startup_failure = 'fail'",
            ]
        ),
        encoding="utf-8",
    )

    class _StrictStartupHost(_FakeProcessHost):
        instances: list["_StrictStartupHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _StrictStartupHost.instances.append(self)

        async def start(
            self,
            message_target_queue: object,
            startup_timeout: float | None = None,
            startup_failure: str = "warn",
        ) -> dict[str, object]:
            self.startup_timeout = startup_timeout
            self.startup_failure = startup_failure
            self.started = True
            if startup_failure == "fail":
                raise PluginLifecycleError(self.plugin_id, "startup", "lifecycle.startup failed")
            return {"status": "failed", "startup_error": "lifecycle.startup failed"}

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["fail_startup_adapter"] = {
                "id": "fail_startup_adapter",
                "name": "Fail Startup Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _StrictStartupHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("fail_startup_adapter", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_START_FAILED"
        assert _StrictStartupHost.instances
        host = _StrictStartupHost.instances[0]
        assert host.startup_failure == "fail"
        assert host.stopped is True
        with module.state.acquire_plugin_hosts_read_lock():
            assert "fail_startup_adapter" not in module.state.plugin_hosts
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
async def test_start_plugin_does_not_map_startup_business_timeout_to_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "business_timeout_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'business_timeout_adapter'",
                "name = 'Business Timeout Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "startup_failure = 'fail'",
            ]
        ),
        encoding="utf-8",
    )

    class _BusinessTimeoutHost(_FakeProcessHost):
        instances: list["_BusinessTimeoutHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _BusinessTimeoutHost.instances.append(self)

        async def start(
            self,
            message_target_queue: object,
            startup_timeout: float | None = None,
            startup_failure: str = "warn",
        ) -> None:
            self.startup_timeout = startup_timeout
            self.startup_failure = startup_failure
            self.started = True
            raise PluginLifecycleError(self.plugin_id, "startup", "database timeout while connecting")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["business_timeout_adapter"] = {
                "id": "business_timeout_adapter",
                "name": "Business Timeout Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _BusinessTimeoutHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("business_timeout_adapter", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_START_FAILED"
        assert exc_info.value.status_code == 500
        assert exc_info.value.details["error_type"] == "PluginLifecycleError"
        assert "database timeout while connecting" in exc_info.value.message
        assert _BusinessTimeoutHost.instances[0].stopped is True
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
async def test_start_plugin_applies_runtime_startup_timeout_to_legacy_host_and_cleans_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "slow_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'slow_adapter'",
                "name = 'Slow Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "timeout = 0.01",
            ]
        ),
        encoding="utf-8",
    )

    class _SlowProcessHost(_FakeProcessHost):
        instances: list["_SlowProcessHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.shutdown_timeout: float | None = None
            _SlowProcessHost.instances.append(self)

        async def start(self, message_target_queue: object) -> None:
            self.started = True
            await asyncio.sleep(0.05)

        async def shutdown(self, timeout: float = module.PLUGIN_SHUTDOWN_TIMEOUT) -> None:
            self.stopped = True
            self.shutdown_timeout = timeout

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["slow_adapter"] = {
                "id": "slow_adapter",
                "name": "Slow Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _SlowProcessHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("slow_adapter", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_START_TIMEOUT"
        assert _SlowProcessHost.instances
        assert _SlowProcessHost.instances[0].stopped is True
        with module.state.acquire_plugin_hosts_read_lock():
            assert "slow_adapter" not in module.state.plugin_hosts
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
async def test_start_plugin_lets_timeout_aware_host_own_startup_timeout_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "aware_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'aware_adapter'",
                "name = 'Aware Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "timeout = 0.01",
            ]
        ),
        encoding="utf-8",
    )

    class _TimeoutAwareHost(_FakeProcessHost):
        instances: list["_TimeoutAwareHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.cancelled = False
            self.startup_cleanup_ran = False
            _TimeoutAwareHost.instances.append(self)

        async def start(self, message_target_queue: object, startup_timeout: float | None = None) -> None:
            self.started = True
            self.startup_timeout = startup_timeout
            try:
                await asyncio.sleep(float(startup_timeout or 0.01) * 2)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            self.startup_cleanup_ran = True
            raise PluginLifecycleError(
                self.plugin_id,
                "startup",
                f"startup timed out after {startup_timeout}s",
            )

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["aware_adapter"] = {
                "id": "aware_adapter",
                "name": "Aware Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _TimeoutAwareHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("aware_adapter", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_START_TIMEOUT"
        assert _TimeoutAwareHost.instances
        host = _TimeoutAwareHost.instances[0]
        assert host.startup_timeout == 0.01
        assert host.cancelled is False
        assert host.startup_cleanup_ran is True
        assert host.stopped is True
        with module.state.acquire_plugin_hosts_read_lock():
            assert "aware_adapter" not in module.state.plugin_hosts
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
async def test_start_plugin_classifies_exponent_form_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tiny_timeout_adapter" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[plugin]",
                "id = 'tiny_timeout_adapter'",
                "name = 'Tiny Timeout Adapter'",
                "type = 'adapter'",
                "entry = 'tests.fake_mcp:FakeAdapterPlugin'",
                "",
                "[plugin_runtime]",
                "enabled = true",
                "auto_start = false",
                "timeout = 1e-6",
            ]
        ),
        encoding="utf-8",
    )

    class _ExponentTimeoutHost(_FakeProcessHost):
        instances: list["_ExponentTimeoutHost"] = []

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            _ExponentTimeoutHost.instances.append(self)

        async def start(self, message_target_queue: object, startup_timeout: float | None = None) -> None:
            self.started = True
            self.startup_timeout = startup_timeout
            raise PluginLifecycleError(
                self.plugin_id,
                "startup",
                f"startup timed out after {startup_timeout}s",
            )

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins["tiny_timeout_adapter"] = {
                "id": "tiny_timeout_adapter",
                "name": "Tiny Timeout Adapter",
                "type": "adapter",
                "config_path": str(config_path),
                "entry_point": "tests.fake_mcp:FakeAdapterPlugin",
            }
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()

        monkeypatch.setattr(
            module,
            "resolve_plugin_config_from_path",
            lambda *args, **kwargs: {
                "effective_config": kwargs["base_config"],
                "warnings": [],
            },
        )
        monkeypatch.setattr(module, "_resolve_plugin_id_conflict", lambda *args, **kwargs: args[0])
        monkeypatch.setattr(module, "PluginProcessHost", _ExponentTimeoutHost)
        monkeypatch.setattr(
            module,
            "_import_plugin_module",
            lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin),
        )
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        with pytest.raises(ServerDomainError) as exc_info:
            await module.PluginLifecycleService().start_plugin("tiny_timeout_adapter", refresh_registry=False)

        assert exc_info.value.code == "PLUGIN_START_TIMEOUT"
        assert exc_info.value.status_code == 504
        assert _ExponentTimeoutHost.instances
        host = _ExponentTimeoutHost.instances[0]
        assert host.startup_timeout == 1e-6
        assert host.stopped is True
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
        monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
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
        monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
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
        monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
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
        monkeypatch.setattr(module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(FakeAdapterPlugin=_FakeAdapterPlugin))
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
                "config_path": str(ext_config_path.resolve()),
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


def _seed_running_plugin(plugin_id: str, config_path: Path) -> None:
    with module.state.acquire_plugins_write_lock():
        module.state.plugins.clear()
        module.state.plugins[plugin_id] = {
            "id": plugin_id,
            "name": plugin_id,
            "type": "plugin",
            "config_path": str(config_path),
            "entry_point": "tests.fake:Plugin",
        }
    with module.state.acquire_plugin_hosts_write_lock():
        module.state.plugin_hosts.clear()
        module.state.plugin_hosts[plugin_id] = _FakeProcessHost(
            plugin_id=plugin_id,
            entry_point="tests.fake:Plugin",
            config_path=config_path,
        )
    with module.state.acquire_event_handlers_write_lock():
        module.state.event_handlers.clear()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_stop_plugin_persist_user_intent_writes_runtime_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolate_runtime_overrides: dict,
) -> None:
    config_path = tmp_path / "demo_plugin" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo_plugin'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        _seed_running_plugin("demo_plugin", config_path)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        await service.stop_plugin("demo_plugin", persist_user_intent=True)
        assert _isolate_runtime_overrides == {"demo_plugin": False}
        assert runtime_overrides_module.get_runtime_override("demo_plugin") is False
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
async def test_stop_plugin_internal_call_does_not_touch_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _isolate_runtime_overrides: dict,
) -> None:
    config_path = tmp_path / "demo_plugin" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo_plugin'\n", encoding="utf-8")

    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)

    try:
        _seed_running_plugin("demo_plugin", config_path)
        runtime_overrides_module.set_runtime_override("demo_plugin", True)
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)

        service = module.PluginLifecycleService()
        await service.stop_plugin("demo_plugin")
        assert _isolate_runtime_overrides == {"demo_plugin": True}
        assert runtime_overrides_module.get_runtime_override("demo_plugin") is True
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
