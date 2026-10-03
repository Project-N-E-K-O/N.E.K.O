from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.application.admin import query_service as module


class _HostWithAlive:
    def __init__(self, *, pid: int | None, alive: bool):
        self.process = SimpleNamespace(pid=pid)
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _HostWithProcessOnly:
    def __init__(self, *, pid: int | None, alive: bool):
        self.process = SimpleNamespace(pid=pid, is_alive=lambda: alive)


class _HostAliveRaises:
    def __init__(self, *, pid: int | None):
        self.process = SimpleNamespace(pid=pid)

    def is_alive(self) -> bool:
        raise RuntimeError("boom")


@pytest.mark.plugin_unit
def test_build_server_info_sync_uses_real_alive_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.state, "get_plugins_snapshot_cached", lambda timeout=1.0: {"a": {}, "b": {}, "c": {}})
    monkeypatch.setattr(
        module.state,
        "get_plugin_hosts_snapshot_cached",
        lambda timeout=1.0: {
            "a": _HostWithAlive(pid=101, alive=True),
            "b": _HostWithProcessOnly(pid=102, alive=False),
            "c": _HostAliveRaises(pid=103),
            1: _HostWithAlive(pid=999, alive=True),
            "none": None,
        },
    )

    payload = module._build_server_info_sync()

    assert payload["plugins_count"] == 3
    assert payload["running_plugins_count"] == 1
    assert payload["running_plugins"] == ["a"]

    status = payload["running_plugins_status"]
    assert status["a"] == {"alive": True, "pid": 101}
    assert status["b"] == {"alive": False, "pid": 102}
    assert status["c"] == {"alive": False, "pid": 103}


@pytest.mark.plugin_unit
def test_build_system_config_sync_uses_explicit_public_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import plugin.settings as settings

    monkeypatch.setattr(settings, "PUBLIC_SYSTEM_CONFIG_KEYS", ("OPEN_VALUE",), raising=False)
    monkeypatch.setattr(settings, "OPEN_VALUE", 123, raising=False)
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token", raising=False)
    monkeypatch.setattr(settings, "PRIVATE_KEY_PATH", Path("/tmp/key.pem"), raising=False)

    payload = module._build_system_config_sync()
    config = payload["config"]

    assert config["OPEN_VALUE"] == 123
    assert "API_TOKEN" not in config
    assert "PRIVATE_KEY_PATH" not in config


@pytest.mark.plugin_unit
def test_build_system_config_sync_keeps_legacy_keys_for_installed_plugins() -> None:
    import warnings

    import plugin.settings as settings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = module._build_system_config_sync()["config"]

    assert config["MARKET_URL"] == settings.MARKET_API_URL
    assert config["PLUGIN_CONFIG_ROOT"] == str(settings.BUILTIN_PLUGIN_CONFIG_ROOT)
    assert config["RESULT_CONSUMER_SLEEP_INTERVAL"] == 0.1
    assert config["PLUGIN_LOG_LEVEL"] == "INFO"
    assert config["PLUGIN_LOG_MAX_BYTES"] == 5 * 1024 * 1024
    assert config["PLUGIN_LOG_BACKUP_COUNT"] == 10
    assert config["PLUGIN_LOG_MAX_FILES"] == 20
    # The host building the snapshot is not the caller that has to migrate.
    assert not [w for w in caught if "plugin.settings." in str(w.message)]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_admin_query_service_get_system_config_validates_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    service = module.AdminQueryService()
    monkeypatch.setattr(module, "_build_system_config_sync", lambda: "bad")

    with pytest.raises(module.ServerDomainError) as exc_info:
        await service.get_system_config()

    assert exc_info.value.code == "INVALID_DATA_SHAPE"
