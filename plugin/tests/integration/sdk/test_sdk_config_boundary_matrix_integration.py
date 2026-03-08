from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from plugin.sdk.config import PluginConfig, PluginConfigError


@dataclass
class _CtxOk:
    config_data: dict[str, Any]

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": self.config_data}

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        _ = timeout
        merged = dict(self.config_data)
        for k, v in updates.items():
            if isinstance(merged.get(k), dict) and isinstance(v, dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        self.config_data = merged
        return {"config": self.config_data}

    async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"data": {"config": {"base": True}}}

    async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"data": {"config_profiles": {"active": "dev", "files": {"dev": "dev.toml"}}}}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"data": {"config": {"runtime": {"profile": profile_name}}}}

    async def get_own_effective_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": {"runtime": {"effective": profile_name}}}


@dataclass
class _CtxMissingMethods:
    config_data: dict[str, Any]

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": self.config_data}


@dataclass
class _CtxBadShapes:
    config_data: dict[str, Any]

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": self.config_data}

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        _ = (updates, timeout)
        return {"config": self.config_data}

    async def get_own_profiles_state(self, timeout: float = 5.0) -> Any:
        _ = timeout
        return "bad-shape"

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, Any]:
        _ = (profile_name, timeout)
        return {"data": {"config": "not-a-dict"}}

    async def get_own_effective_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, Any]:
        _ = (profile_name, timeout)
        return {"config": "not-a-dict"}


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_async_happy_path_matrix() -> None:
    cfg = PluginConfig(_CtxOk(config_data={"runtime": {"enabled": True, "level": 1}}))

    assert await cfg.dump() == {"runtime": {"enabled": True, "level": 1}}
    assert await cfg.dump_base() == {"base": True}
    assert (await cfg.get_profiles_state())["config_profiles"]["active"] == "dev"
    assert (await cfg.get_profile("prod"))["runtime"]["profile"] == "prod"
    assert (await cfg.dump_effective("stage"))["runtime"]["effective"] == "stage"
    assert await cfg.get("runtime.level") == 1
    assert await cfg.get("runtime.missing", default="d") == "d"
    assert await cfg.require("runtime.enabled") is True

    await cfg.update({"runtime": {"level": 2}})
    assert await cfg.get("runtime.level") == 2
    await cfg.set("runtime.tag", "ok")
    assert await cfg.get("runtime.tag") == "ok"
    assert await cfg.get_section("runtime") == {"enabled": True, "level": 2, "tag": "ok"}


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_missing_method_errors() -> None:
    cfg = PluginConfig(_CtxMissingMethods(config_data={"runtime": {"enabled": True}}))

    with pytest.raises(PluginConfigError):
        await cfg.update({"x": 1})
    with pytest.raises(PluginConfigError):
        await cfg.dump_base()
    with pytest.raises(PluginConfigError):
        await cfg.get_profiles_state()
    with pytest.raises(PluginConfigError):
        await cfg.get_profile("dev")
    with pytest.raises(PluginConfigError):
        await cfg.dump_effective("dev")


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_invalid_shape_errors() -> None:
    cfg = PluginConfig(_CtxBadShapes(config_data={"runtime": {"enabled": True}}))

    with pytest.raises(PluginConfigError):
        await cfg.get_profiles_state()
    with pytest.raises(PluginConfigError):
        await cfg.get_profile("dev")
    with pytest.raises(PluginConfigError):
        await cfg.dump_effective("dev")


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_update_and_set_boundary_errors() -> None:
    cfg = PluginConfig(_CtxOk(config_data={"runtime": {"enabled": True}}))

    with pytest.raises(PluginConfigError):
        await cfg.update("not-a-dict")  # type: ignore[arg-type]
    with pytest.raises(PluginConfigError):
        await cfg.set("", "not-a-dict")


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_config_get_section_non_dict_error() -> None:
    cfg = PluginConfig(_CtxOk(config_data={"runtime": {"enabled": True}, "scalar": 1}))
    with pytest.raises(PluginConfigError):
        await cfg.get_section("scalar")
