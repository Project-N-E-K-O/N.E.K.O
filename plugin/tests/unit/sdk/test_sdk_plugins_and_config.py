from __future__ import annotations

from dataclasses import dataclass

import pytest

from plugin.sdk.config import PluginConfig, PluginConfigError, _get_by_path, _set_by_path
from plugin.sdk.plugins import PluginCallError, Plugins, _parse_entry_ref, _parse_event_ref


@dataclass
class _PluginCtx:
    config_data: dict[str, object]

    @staticmethod
    def _deep_merge(base: dict[str, object], updates: dict[str, object]) -> dict[str, object]:
        merged = dict(base)
        for key, value in updates.items():
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = _PluginCtx._deep_merge(current, value)
            else:
                merged[key] = value
        return merged

    def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "a"}, {"plugin_id": "b"}]}

    def trigger_plugin_event(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {
            "target_plugin_id": target_plugin_id,
            "event_type": event_type,
            "event_id": event_id,
            "params": params,
            "timeout": timeout,
        }

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": self.config_data}

    async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": self.config_data}

    async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, object]:
        return {"data": {"config_profiles": {"active": "dev"}}}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, object]:
        return {"data": {"config": {"runtime": {"profile": profile_name}}}}

    async def get_own_effective_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"runtime": {"effective": profile_name}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        self.config_data = self._deep_merge(self.config_data, updates)
        return {"config": self.config_data}


@pytest.mark.plugin_unit
def test_parse_entry_and_event_ref() -> None:
    assert _parse_entry_ref("a:b") == ("a", "b")
    assert _parse_event_ref("a:event:x") == ("a", "event", "x")

    with pytest.raises(PluginCallError):
        _parse_entry_ref("bad")
    with pytest.raises(PluginCallError):
        _parse_event_ref("bad")


@pytest.mark.plugin_unit
def test_plugins_call_helpers_and_require() -> None:
    plugins = Plugins(ctx=_PluginCtx(config_data={"runtime": {"enabled": True}}))

    call_result = plugins.call_entry("a:run", {"x": 1}, timeout=2.5)
    assert call_result["target_plugin_id"] == "a"
    assert call_result["event_type"] == "plugin_entry"

    event_result = plugins.call_event("a:custom:run", {"x": 2})
    assert event_result["event_type"] == "custom"

    plugins.require("a")
    with pytest.raises(PluginCallError):
        plugins.require("missing")


@pytest.mark.plugin_unit
def test_config_path_helpers() -> None:
    payload = {"a": {"b": 1}}
    assert _get_by_path(payload, "a.b") == 1
    with pytest.raises(PluginConfigError):
        _get_by_path(payload, "a.c")

    patch: dict[str, object] = {}
    _set_by_path(patch, "x.y", 3)
    assert patch == {"x": {"y": 3}}


@pytest.mark.plugin_unit
def test_plugin_config_sync_methods() -> None:
    ctx = _PluginCtx(config_data={"runtime": {"enabled": True}})
    cfg = PluginConfig(ctx)

    assert cfg.dump_sync()["runtime"]["enabled"] is True
    assert cfg.get_sync("runtime.enabled") is True
    assert cfg.get_sync("runtime.missing", default="d") == "d"
    assert cfg.require_sync("runtime.enabled") is True
    assert cfg.get_profiles_state_sync()["config_profiles"]["active"] == "dev"
    assert cfg.get_profile_sync("prod")["runtime"]["profile"] == "prod"
    assert cfg.dump_effective_sync("stage")["runtime"]["effective"] == "stage"

    updated = cfg.update_sync({"feature": {"x": 1}})
    assert updated["feature"]["x"] == 1

    set_result = cfg.set_sync("runtime.level", 2)
    assert set_result["runtime"]["level"] == 2

    section = cfg.get_section_sync("runtime")
    assert section["enabled"] is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_config_async_proxy_methods() -> None:
    ctx = _PluginCtx(config_data={"runtime": {"enabled": True}})
    cfg = PluginConfig(ctx)

    dump_coro = cfg.dump()
    assert hasattr(dump_coro, "__await__")
    dump_payload = await dump_coro
    assert dump_payload["runtime"]["enabled"] is True

    value = await cfg.get("runtime.enabled")
    assert value is True
