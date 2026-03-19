from __future__ import annotations

import pytest

from plugin.sdk_v2.shared.core import config as core_config
# config_runtime was in the deleted public/ layer; helpers are now in core_config
config_runtime = core_config


class _CtxFull:
    def __init__(self) -> None:
        self.base_cfg = {"feature": {"enabled": True}, "section": {"x": 1}, "leaf": 1}
        self.profiles_state = {"config_profiles": {"active": "dev", "files": {"dev": {"path": "profiles/dev.toml"}, "prod": {"path": "profiles/prod.toml"}}}}
        self.profile_cfgs = {"dev": {"feature": {"enabled": False}}, "prod": {"feature": {"enabled": True}}}
        self.updated = None

    async def get_own_config(self, timeout: float = 5.0):
        active = self.profiles_state["config_profiles"].get("active")
        if active and active in self.profile_cfgs:
            merged = config_runtime.deep_merge_config(self.base_cfg, self.profile_cfgs[active])
            return {"config": merged}
        return {"config": self.base_cfg}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self.base_cfg}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"data": self.profiles_state}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"data": {"config": self.profile_cfgs.get(profile_name, {})}}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        target = profile_name or self.profiles_state["config_profiles"].get("active")
        if target and target in self.profile_cfgs:
            return {"config": config_runtime.deep_merge_config(self.base_cfg, self.profile_cfgs[target])}
        return {"config": self.base_cfg}

    async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
        self.profile_cfgs[profile_name] = dict(config)
        self.profiles_state["config_profiles"]["files"][profile_name] = {"path": f"profiles/{profile_name}.toml"}
        if make_active:
            self.profiles_state["config_profiles"]["active"] = profile_name
        return {"data": {"config": self.profile_cfgs[profile_name]}}

    async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
        removed = profile_name in self.profile_cfgs
        self.profile_cfgs.pop(profile_name, None)
        self.profiles_state["config_profiles"]["files"].pop(profile_name, None)
        if self.profiles_state["config_profiles"].get("active") == profile_name:
            self.profiles_state["config_profiles"]["active"] = None
        return {"removed": removed}

    async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
        self.profiles_state["config_profiles"]["active"] = profile_name
        return self.profiles_state

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
        self.updated = updates
        return {"config": updates}


class _CtxNoProfileApis:
    async def get_own_config(self, timeout: float = 5.0):
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
        return {"config": updates}


@pytest.mark.asyncio
async def test_config_template_base_and_profiles_views() -> None:
    cfg = core_config.PluginConfig(_CtxFull())
    # PluginConfigBaseView and PluginConfigProfiles are aliases to PluginConfig
    assert isinstance(cfg, core_config.PluginConfigBaseView)
    assert isinstance(cfg, core_config.PluginConfigProfiles)

    assert (await cfg.base_dump())["feature"]["enabled"] is True
    assert (await cfg.get_bool("feature.enabled")) is False  # dev profile overrides base
    with pytest.raises(Exception):
        await cfg.get_int("feature.enabled")
    assert (await cfg.base_get("feature.enabled")) is True
    assert (await cfg.base_get("missing", 1)) == 1
    with pytest.raises(Exception):
        await cfg.require("missing")

    state = await cfg.profile_state()
    assert state["config_profiles"]["active"] == "dev"
    assert (await cfg.profile_list()) == ["dev", "prod"]
    assert (await cfg.profile_active()) == "dev"
    assert (await cfg.profile_get("dev"))["feature"]["enabled"] is False
    effective = await cfg.profile_effective()
    assert effective["feature"]["enabled"] is False
    assert (await cfg.profile_effective("prod"))["feature"]["enabled"] is True


@pytest.mark.asyncio
async def test_config_template_profile_write_paths() -> None:
    ctx = _CtxFull()
    cfg = core_config.PluginConfig(ctx)

    created = await cfg.profile_create("qa", {"feature": {"enabled": True}}, make_active=True)
    assert created["feature"]["enabled"] is True
    assert (await cfg.profile_active()) == "qa"

    updated = await cfg.profile_update("qa", {"feature": {"mode": "fast"}})
    assert updated["feature"]["mode"] == "fast"

    assert (await cfg.profile_activate("prod")) is True
    assert (await cfg.profile_delete("qa")) is True


@pytest.mark.asyncio
async def test_config_template_main_view_write_semantics() -> None:
    ctx = _CtxFull()
    cfg = core_config.PluginConfig(ctx)
    dumped = await cfg.dump()
    assert dumped["feature"]["enabled"] is False
    await cfg.set("feature.flag", True)
    updated = await cfg.update({"feature": {"mode": "fast"}})
    assert updated["feature"]["mode"] == "fast"

    fallback = core_config.PluginConfig(_CtxNoProfileApis())
    with pytest.raises(Exception):
        await fallback.set("feature.flag", True)
    with pytest.raises(Exception):
        await fallback.update({"x": 1})

    no_active_ctx = _CtxFull()
    no_active_ctx.profiles_state["config_profiles"]["active"] = None
    no_active = core_config.PluginConfig(no_active_ctx)
    assert (await no_active.profile_ensure_active("runtime", {"feature": {"enabled": True}})) == "runtime"
    assert (await no_active.profile_active()) == "runtime"
    await no_active.set("x", 1)
    await no_active.update({"x": 1})


@pytest.mark.asyncio
async def test_config_template_error_paths() -> None:
    cfg = core_config.PluginConfig(_CtxFull())
    with pytest.raises(Exception):
        await cfg.profile_get(" ")
    with pytest.raises(Exception):
        await cfg.profile_effective(" ")
    with pytest.raises(Exception):
        await cfg.profile_create(" ", {})

    class _NoWrite(_CtxFull):
        upsert_own_profile_config = None
        delete_own_profile_config = None
        set_own_active_profile = None

    nowrite = core_config.PluginConfig(_NoWrite())
    with pytest.raises(Exception):
        await nowrite.profile_delete("dev")
    with pytest.raises(Exception):
        await nowrite.profile_activate("dev")

    class _BadPayload(_CtxFull):
        async def get_own_profiles_state(self, timeout: float = 5.0):
            return "bad"
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return {"data": {"config": "bad"}}
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            return "bad"
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    bad = core_config.PluginConfig(_BadPayload())
    with pytest.raises(Exception):
        await bad.profile_state()
    with pytest.raises(Exception):
        await bad.profile_get("dev")
    with pytest.raises(Exception):
        await bad.profile_activate("dev")
    with pytest.raises(Exception):
        await bad.profile_delete("dev")


def test_public_config_runtime_helper_remaining_branch() -> None:
    assert config_runtime.get_profile_names({"config_profiles": {"files": None}}) == []


@pytest.mark.asyncio
async def test_config_template_branch_coverage() -> None:
    class _CtxProfilesBad(_CtxFull):
        async def get_own_profiles_state(self, timeout: float = 5.0):
            return "bad"

    bad_profiles = core_config.PluginConfig(_CtxProfilesBad())
    with pytest.raises(Exception):
        await bad_profiles.profile_state()
    with pytest.raises(Exception):
        await bad_profiles.profile_list()
    with pytest.raises(Exception):
        await bad_profiles.profile_active()

    class _CtxProfileBad(_CtxFull):
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return {"data": {"config": "bad"}}

    bad_profile = core_config.PluginConfig(_CtxProfileBad())
    with pytest.raises(Exception):
        await bad_profile.profile_get("dev")

    class _CtxDeleteBad(_CtxFull):
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    delete_bad = core_config.PluginConfig(_CtxDeleteBad())
    with pytest.raises(Exception):
        await delete_bad.profile_delete("dev")

    class _CtxActivateBad(_CtxFull):
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    activate_bad = core_config.PluginConfig(_CtxActivateBad())
    with pytest.raises(Exception):
        await activate_bad.profile_activate("dev")

    class _CtxSetFallbackBad(_CtxNoProfileApis):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            return {"config": "bad"}

    set_fallback_bad = core_config.PluginConfig(_CtxSetFallbackBad())
    with pytest.raises(Exception):
        await set_fallback_bad.set("x", 1)

    class _CtxUpdateFallbackBad(_CtxNoProfileApis):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            raise RuntimeError("boom")

    update_fallback_bad = core_config.PluginConfig(_CtxUpdateFallbackBad())
    with pytest.raises(Exception):
        await update_fallback_bad.update({"x": 1})


def test_public_config_runtime_remaining_branch() -> None:
    assert config_runtime.get_profile_names({"config_profiles": {"files": None}}) == []


@pytest.mark.asyncio
async def test_config_template_branch_edges() -> None:
    class _CtxBaseErr:
        async def get_own_base_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")

    base = core_config.PluginConfig(_CtxBaseErr())
    with pytest.raises(Exception):
        await base.base_dump()

    class _NoWrite(_CtxFull):
        upsert_own_profile_config = None
        delete_own_profile_config = None
        set_own_active_profile = None

    nowrite = core_config.PluginConfig(_NoWrite())
    with pytest.raises(Exception):
        await nowrite.profile_delete(" ")
    with pytest.raises(Exception):
        await nowrite.profile_delete("dev")
    with pytest.raises(Exception):
        await nowrite.profile_activate(" ")
    with pytest.raises(Exception):
        await nowrite.profile_activate("dev")

    class _CtxWriteBoom(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            raise RuntimeError("boom")
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            raise RuntimeError("boom")
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            raise RuntimeError("boom")

    boom = core_config.PluginConfig(_CtxWriteBoom())
    with pytest.raises(Exception):
        await boom.profile_delete("dev")
    with pytest.raises(Exception):
        await boom.profile_activate("dev")

    class _CtxWriteBad(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            return {"data": {"config": "bad"}}
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            return "bad"
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    bad = core_config.PluginConfig(_CtxWriteBad())
    with pytest.raises(Exception):
        await bad.profile_delete("dev")
    with pytest.raises(Exception):
        await bad.profile_activate("dev")

    class _CtxFallbackNoUpdater:
        async def get_own_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")

    fallback_none = core_config.PluginConfig(_CtxFallbackNoUpdater())
    with pytest.raises(Exception):
        await fallback_none.set("x", 1)
    with pytest.raises(Exception):
        await fallback_none.update({"x": 1})

    class _CtxNoActive(_CtxFull):
        def __init__(self):
            super().__init__()
            self.profiles_state["config_profiles"]["active"] = None

    no_active = core_config.PluginConfig(_CtxNoActive())
    with pytest.raises(Exception):
        await no_active.set("x", 1)
    with pytest.raises(Exception):
        await no_active.update({"x": 1})


@pytest.mark.asyncio
async def test_config_template_set_profile_write_error_branch() -> None:
    class _CtxSetFail(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            return {"data": {"config": "bad"}}

    cfg = core_config.PluginConfig(_CtxSetFail())
    with pytest.raises(Exception):
        await cfg.set("feature.flag", True)
