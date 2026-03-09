from __future__ import annotations

import pytest

from plugin.sdk_v2.public.core import config_runtime
from plugin.sdk_v2.shared.core import config as core_config


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
    assert isinstance(cfg.base, core_config.PluginConfigBaseView)
    assert isinstance(cfg.profiles, core_config.PluginConfigProfiles)

    assert (await cfg.base.dump()).unwrap()["feature"]["enabled"] is True
    assert (await cfg.base.get_bool("feature.enabled")).unwrap() is True
    assert (await cfg.base.get_int("feature.enabled")).is_err()
    assert (await cfg.base.get("feature.enabled")).unwrap() is True
    assert (await cfg.base.get("missing", 1)).unwrap() == 1
    assert (await cfg.base.require("missing")).is_err()
    assert (await cfg.base.get_section("section")).unwrap() == {"x": 1}
    assert (await cfg.base.get_section("leaf")).is_err()

    assert (await cfg.profiles.state()).unwrap()["config_profiles"]["active"] == "dev"
    assert (await cfg.profiles.list()).unwrap() == ["dev", "prod"]
    assert (await cfg.profiles.active()).unwrap() == "dev"
    assert (await cfg.profiles.require_active()).unwrap() == "dev"
    assert (await cfg.profiles.current()).unwrap()["name"] == "dev"
    assert (await cfg.profiles.exists("dev")).unwrap() is True
    assert (await cfg.profiles.get("dev")).unwrap()["feature"]["enabled"] is False
    assert (await cfg.profiles.effective()).unwrap()["feature"]["enabled"] is False
    assert (await cfg.profiles.effective("prod")).unwrap()["feature"]["enabled"] is True


@pytest.mark.asyncio
async def test_config_template_profile_write_paths() -> None:
    ctx = _CtxFull()
    cfg = core_config.PluginConfig(ctx)

    created = await cfg.profiles.create("qa", {"feature": {"enabled": True}}, make_active=True)
    assert created.unwrap()["feature"]["enabled"] is True
    assert (await cfg.profiles.active()).unwrap() == "qa"

    replaced = await cfg.profiles.replace("qa", {"x": 1})
    assert replaced.unwrap() == {"x": 1}

    updated = await cfg.profiles.update("qa", {"feature": {"mode": "fast"}})
    assert updated.unwrap()["feature"]["mode"] == "fast"

    set_result = await cfg.profiles.set("qa", "feature.flag", True)
    assert set_result.unwrap()["feature"]["flag"] is True

    assert (await cfg.profiles.activate("prod")).unwrap() is True
    assert (await cfg.profiles.delete("qa")).unwrap() is True


@pytest.mark.asyncio
async def test_config_template_main_view_write_semantics() -> None:
    ctx = _CtxFull()
    cfg = core_config.PluginConfig(ctx)
    assert (await cfg.dump()).unwrap()["feature"]["enabled"] is False
    assert (await cfg.set("feature.flag", True)).is_ok()
    assert (await cfg.update({"feature": {"mode": "fast"}})).unwrap()["feature"]["mode"] == "fast"

    fallback = core_config.PluginConfig(_CtxNoProfileApis())
    assert (await fallback.set("feature.flag", True)).is_err()
    assert (await fallback.update({"x": 1})).is_err()

    no_active_ctx = _CtxFull()
    no_active_ctx.profiles_state["config_profiles"]["active"] = None
    no_active = core_config.PluginConfig(no_active_ctx)
    assert (await no_active.profiles.require_active()).is_err()
    assert (await no_active.profiles.ensure_active("runtime", {"feature": {"enabled": True}})).unwrap() == "runtime"
    assert (await no_active.profiles.active()).unwrap() == "runtime"
    assert (await no_active.set("x", 1)).is_ok()
    assert (await no_active.update({"x": 1})).is_ok()


@pytest.mark.asyncio
async def test_config_template_error_paths() -> None:
    cfg = core_config.PluginConfig(_CtxFull())
    assert (await cfg.profiles.exists(" ")).is_err()
    assert (await cfg.profiles.get(" ")).is_err()
    assert (await cfg.profiles.effective(" ")).is_err()
    assert (await cfg.profiles.replace("dev", "bad")).is_err()
    assert (await cfg.profiles.update("dev", "bad")).is_err()
    assert (await cfg.profiles.create(" ", {})).is_err()

    class _NoWrite(_CtxFull):
        upsert_own_profile_config = None
        delete_own_profile_config = None
        set_own_active_profile = None

    nowrite = core_config.PluginConfig(_NoWrite())
    assert (await nowrite.profiles.replace("dev", {})).is_err()
    assert (await nowrite.profiles.delete("dev")).is_err()
    assert (await nowrite.profiles.activate("dev")).is_err()

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
    assert (await bad.profiles.state()).is_err()
    assert (await bad.profiles.get("dev")).is_err()
    assert (await bad.profiles.activate("dev")).is_err()
    assert (await bad.profiles.delete("dev")).is_err()


def test_public_config_runtime_helper_remaining_branch() -> None:
    assert config_runtime.get_profile_names({"config_profiles": {"files": None}}) == []


@pytest.mark.asyncio
async def test_config_template_branch_coverage() -> None:
    class _CtxBaseBad(_CtxNoProfileApis):
        async def get_own_base_config(self, timeout: float = 5.0):
            return {"config": {"leaf": 1}}

    base = core_config.PluginConfigBaseView(_CtxBaseBad())
    assert (await base.get("missing", default=None)).is_err()
    assert (await base.require("missing")).is_err()
    assert (await base.get_section("leaf")).is_err()

    class _CtxProfilesBad(_CtxFull):
        async def get_own_profiles_state(self, timeout: float = 5.0):
            return "bad"

    bad_profiles = core_config.PluginConfigProfiles(_CtxProfilesBad())
    assert (await bad_profiles.state()).is_err()
    assert (await bad_profiles.list()).is_err()
    assert (await bad_profiles.active()).is_err()
    assert (await bad_profiles.exists("dev")).is_err()

    class _CtxProfileBad(_CtxFull):
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return {"data": {"config": "bad"}}

    bad_profile = core_config.PluginConfigProfiles(_CtxProfileBad())
    assert (await bad_profile.get("dev")).is_err()
    assert (await bad_profile.update("dev", {"x": 1})).is_err()
    assert (await bad_profile.set("dev", "x", 1)).is_err()

    class _CtxReplaceBad(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            return {"data": {"config": "bad"}}

    replace_bad = core_config.PluginConfigProfiles(_CtxReplaceBad())
    assert (await replace_bad.replace("dev", {"x": 1})).is_err()

    class _CtxDeleteBad(_CtxFull):
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    delete_bad = core_config.PluginConfigProfiles(_CtxDeleteBad())
    assert (await delete_bad.delete("dev")).is_err()

    class _CtxActivateBad(_CtxFull):
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    activate_bad = core_config.PluginConfigProfiles(_CtxActivateBad())
    assert (await activate_bad.activate("dev")).is_err()

    class _CtxSetFallbackBad(_CtxNoProfileApis):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            return {"config": "bad"}

    set_fallback_bad = core_config.PluginConfig(_CtxSetFallbackBad())
    assert (await set_fallback_bad.set("x", 1)).is_err()

    class _CtxUpdateFallbackBad(_CtxNoProfileApis):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            raise RuntimeError("boom")

    update_fallback_bad = core_config.PluginConfig(_CtxUpdateFallbackBad())
    assert (await update_fallback_bad.update({"x": 1})).is_err()


def test_public_config_runtime_remaining_branch() -> None:
    assert config_runtime.get_profile_names({"config_profiles": {"files": None}}) == []


@pytest.mark.asyncio
async def test_config_template_branch_edges() -> None:
    class _CtxBaseErr:
        async def get_own_base_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")

    base = core_config.PluginConfigBaseView(_CtxBaseErr())
    assert (await base.get("x")).is_err()
    assert (await base.require("x")).is_err()
    assert (await base.get_section("x")).is_err()

    class _NoWrite(_CtxFull):
        upsert_own_profile_config = None
        delete_own_profile_config = None
        set_own_active_profile = None

    nowrite = core_config.PluginConfigProfiles(_NoWrite())
    assert (await nowrite.replace("dev", {}, timeout=0)).is_err()
    assert (await nowrite.replace("dev", {})).is_err()
    assert (await nowrite.delete("dev", timeout=0)).is_err()
    assert (await nowrite.delete(" ")).is_err()
    assert (await nowrite.delete("dev")).is_err()
    assert (await nowrite.activate("dev", timeout=0)).is_err()
    assert (await nowrite.activate(" ")).is_err()
    assert (await nowrite.activate("dev")).is_err()

    class _CtxWriteBoom(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            raise RuntimeError("boom")
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            raise RuntimeError("boom")
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            raise RuntimeError("boom")

    boom = core_config.PluginConfigProfiles(_CtxWriteBoom())
    assert (await boom.replace("dev", {})).is_err()
    assert (await boom.delete("dev")).is_err()
    assert (await boom.activate("dev")).is_err()

    class _CtxWriteBad(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            return {"data": {"config": "bad"}}
        async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0):
            return "bad"
        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0):
            return "bad"

    bad = core_config.PluginConfigProfiles(_CtxWriteBad())
    assert (await bad.replace("dev", {})).is_err()
    assert (await bad.update("dev", "bad")).is_err()
    assert (await bad.set("dev", "", 1)).is_err()
    assert (await bad.delete("dev")).is_err()
    assert (await bad.activate("dev")).is_err()

    class _CtxFallbackNoUpdater:
        async def get_own_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")

    fallback_none = core_config.PluginConfig(_CtxFallbackNoUpdater())
    assert (await fallback_none.set("x", 1)).is_err()
    assert (await fallback_none.update({"x": 1})).is_err()

    class _CtxFallbackBadPayload(_CtxNoProfileApis):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            return {"config": "bad"}

    fallback_bad = core_config.PluginConfig(_CtxFallbackBadPayload())
    assert (await fallback_bad.set("x", 1)).is_err()

    class _CtxNoActive(_CtxFull):
        def __init__(self):
            super().__init__()
            self.profiles_state["config_profiles"]["active"] = None

    no_active = core_config.PluginConfig(_CtxNoActive())
    assert (await no_active.set("x", 1)).is_err()
    assert (await no_active.update({"x": 1})).is_err()


@pytest.mark.asyncio
async def test_config_template_set_profile_write_error_branch() -> None:
    class _CtxSetFail(_CtxFull):
        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0):
            return {"data": {"config": "bad"}}

    cfg = core_config.PluginConfig(_CtxSetFail())
    assert (await cfg.set("feature.flag", True)).is_err()
