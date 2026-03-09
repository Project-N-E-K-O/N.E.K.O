from __future__ import annotations

import pytest

from plugin.sdk_v2.public.core import config_runtime


class _Ctx:
    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": {"x": 1}}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"data": {"config": {"profile": profile_name}}}


def test_public_config_runtime_unwrap_helpers() -> None:
    assert config_runtime.unwrap_config_payload({"config": {"x": 1}}) == {"x": 1}
    assert config_runtime.unwrap_config_payload({"data": {"config": {"x": 1}}}) == {"x": 1}
    assert config_runtime.unwrap_config_payload({"x": 1}) == {"x": 1}
    with pytest.raises(TypeError):
        config_runtime.unwrap_config_payload("bad")
    assert config_runtime.unwrap_profiles_state({"data": {"active": "dev"}}) == {"active": "dev"}
    with pytest.raises(TypeError):
        config_runtime.unwrap_profiles_state("bad")
    assert config_runtime.unwrap_profile_payload({"data": {"config": {"x": 1}}}) == {"x": 1}
    assert config_runtime.unwrap_profile_payload({"data": {"config": None}}) == {}
    with pytest.raises(TypeError):
        config_runtime.unwrap_profile_payload({"data": {"config": "bad"}})
    assert config_runtime.validate_profile_name("dev") == "dev"
    with pytest.raises(ValueError):
        config_runtime.validate_profile_name(" ")


@pytest.mark.asyncio
async def test_public_config_runtime_fetch_ctx_payload() -> None:
    ok = await config_runtime.fetch_ctx_payload(_Ctx(), getter_name="get_own_base_config", timeout=1)
    assert ok == {"config": {"x": 1}}
    ok = await config_runtime.fetch_ctx_payload(_Ctx(), getter_name="get_own_profile_config", timeout=1, arg="dev")
    assert ok == {"data": {"config": {"profile": "dev"}}}

    class _NoCtx:
        pass

    with pytest.raises(AttributeError):
        await config_runtime.fetch_ctx_payload(_NoCtx(), getter_name="missing", timeout=1)

    class _Boom:
        async def get_own_base_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await config_runtime.fetch_ctx_payload(_Boom(), getter_name="get_own_base_config", timeout=1)
