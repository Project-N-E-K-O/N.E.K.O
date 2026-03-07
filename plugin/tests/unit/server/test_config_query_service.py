from __future__ import annotations

import pytest

from plugin.server.application.config.query_service import ConfigQueryService
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_uses_direct_config_when_profile_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _fake_get_plugin_config(*, plugin_id: str) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": {"runtime": {"enabled": True}}}

    monkeypatch.setattr(service, "get_plugin_config", _fake_get_plugin_config)

    payload = await service.get_plugin_effective_config(plugin_id="demo", profile_name=None)
    assert payload["config"] == {"runtime": {"enabled": True}}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_rejects_overlay_plugin_section(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _base(*, plugin_id: str) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": {"runtime": {"enabled": True}}}

    async def _overlay(*, plugin_id: str, profile_name: object) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": {"plugin": {"name": "bad"}}}

    monkeypatch.setattr(service, "get_plugin_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay)

    with pytest.raises(ServerDomainError) as exc_info:
        await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")

    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_merges_base_and_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _base(*, plugin_id: str) -> dict[str, object]:
        return {
            "plugin_id": plugin_id,
            "config": {
                "runtime": {"enabled": True, "level": 1},
                "feature": {"a": 1},
            },
        }

    async def _overlay(*, plugin_id: str, profile_name: object) -> dict[str, object]:
        return {
            "plugin_id": plugin_id,
            "config": {
                "runtime": {"level": 2},
                "feature": {"b": 2},
            },
        }

    monkeypatch.setattr(service, "get_plugin_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay)

    payload = await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")
    assert payload["config"] == {
        "runtime": {"enabled": True, "level": 2},
        "feature": {"a": 1, "b": 2},
    }
    assert payload["effective_profile"] == "dev"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_rejects_bad_base_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _base(*, plugin_id: str) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": "bad"}

    async def _overlay(*, plugin_id: str, profile_name: object) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": {}}

    monkeypatch.setattr(service, "get_plugin_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay)

    with pytest.raises(ServerDomainError) as exc_info:
        await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")

    assert exc_info.value.code == "INVALID_DATA_SHAPE"


from pathlib import Path

import pytest
from fastapi import HTTPException

import plugin.server.application.config.query_service as qs
from plugin.server.application.config.query_service import ConfigQueryService
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
def test_query_service_helper_normalization_and_messages() -> None:
    assert qs._to_message("x", fallback="y") == "x"
    assert qs._to_message(None, fallback="y") == "y"

    err = qs._from_http_exception(HTTPException(status_code=418, detail="teapot"), code="E", fallback_message="f")
    assert err.code == "E"
    assert err.status_code == 418
    assert err.message == "teapot"

    with pytest.raises(ServerDomainError):
        qs._normalize_payload([], context="ctx")
    with pytest.raises(ServerDomainError):
        qs._normalize_payload({1: "x"}, context="ctx")
    assert qs._normalize_payload({"a": 1}, context="ctx") == {"a": 1}

    assert qs._normalize_config_mapping(None, field="f", allow_none=True) == {}
    with pytest.raises(ServerDomainError):
        qs._normalize_config_mapping("bad", field="f")
    with pytest.raises(ServerDomainError):
        qs._normalize_config_mapping({1: "x"}, field="f")
    assert qs._normalize_config_mapping({"a": 1}, field="f") == {"a": 1}

    with pytest.raises(ServerDomainError):
        qs._normalize_profile_name("")
    assert qs._normalize_profile_name(" dev ") == "dev"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, infra_name, kwargs, code",
    [
        ("get_plugin_config", "infrastructure_load_plugin_config", {"plugin_id": "demo"}, "PLUGIN_CONFIG_QUERY_FAILED"),
        ("get_plugin_config_toml", "infrastructure_load_plugin_config_toml", {"plugin_id": "demo"}, "PLUGIN_CONFIG_TOML_QUERY_FAILED"),
        ("get_plugin_base_config", "infrastructure_load_plugin_base_config", {"plugin_id": "demo"}, "PLUGIN_BASE_CONFIG_QUERY_FAILED"),
        ("parse_toml_to_config", "infrastructure_parse_toml_to_config", {"plugin_id": "demo", "toml": "x=1"}, "PLUGIN_CONFIG_PARSE_FAILED"),
        ("render_config_to_toml", "infrastructure_render_config_to_toml", {"plugin_id": "demo", "config": {}}, "PLUGIN_CONFIG_RENDER_FAILED"),
    ],
)
async def test_query_service_methods_map_http_and_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    infra_name: str,
    kwargs: dict[str, object],
    code: str,
) -> None:
    service = ConfigQueryService()

    monkeypatch.setattr(qs, infra_name, lambda *a, **k: (_ for _ in ()).throw(HTTPException(status_code=404, detail="missing")))
    with pytest.raises(ServerDomainError) as exc_http:
        await getattr(service, method_name)(**kwargs)
    assert exc_http.value.code == code
    assert exc_http.value.status_code == 404

    monkeypatch.setattr(qs, infra_name, lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_rt:
        await getattr(service, method_name)(**kwargs)
    assert exc_rt.value.code == code
    assert exc_rt.value.status_code == 500


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_service_profile_state_and_profile_config_error_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()
    monkeypatch.setattr(qs, "get_plugin_config_path", lambda plugin_id: Path("/tmp/fake.toml"))

    monkeypatch.setattr(qs, "infrastructure_get_profiles_state", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=403, detail="denied")))
    with pytest.raises(ServerDomainError) as exc_state_http:
        await service.get_plugin_profiles_state(plugin_id="demo")
    assert exc_state_http.value.code == "PLUGIN_PROFILE_STATE_QUERY_FAILED"

    monkeypatch.setattr(qs, "infrastructure_get_profiles_state", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_state_rt:
        await service.get_plugin_profiles_state(plugin_id="demo")
    assert exc_state_rt.value.code == "PLUGIN_PROFILE_STATE_QUERY_FAILED"

    with pytest.raises(ServerDomainError):
        await service.get_plugin_profile_config(plugin_id="demo", profile_name="")

    monkeypatch.setattr(qs, "infrastructure_get_profile_config", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404, detail="missing")))
    with pytest.raises(ServerDomainError) as exc_profile_http:
        await service.get_plugin_profile_config(plugin_id="demo", profile_name="dev")
    assert exc_profile_http.value.code == "PLUGIN_PROFILE_QUERY_FAILED"

    monkeypatch.setattr(qs, "infrastructure_get_profile_config", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_profile_rt:
        await service.get_plugin_profile_config(plugin_id="demo", profile_name="dev")
    assert exc_profile_rt.value.code == "PLUGIN_PROFILE_QUERY_FAILED"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_query_service_effective_config_mapping_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _base(**kwargs):  # noqa: ANN003
        return {"config": {"runtime": {"a": 1}}}

    async def _overlay_none(**kwargs):  # noqa: ANN003
        return {"config": None}

    monkeypatch.setattr(service, "get_plugin_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay_none)
    out = await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")
    assert out["config"]["runtime"]["a"] == 1
    assert out["effective_profile"] == "dev"

    async def _overlay_bad(**kwargs):  # noqa: ANN003
        return {"config": "bad"}

    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay_bad)
    with pytest.raises(ServerDomainError):
        await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")
