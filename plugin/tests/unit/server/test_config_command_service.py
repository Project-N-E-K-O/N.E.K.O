from __future__ import annotations

import pytest
from fastapi import HTTPException

from plugin.server.application.config.command_service import ConfigCommandService
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_replace_plugin_config_maps_http_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigCommandService()

    def _raise_http(plugin_id: str, config: dict[str, object]) -> dict[str, object]:
        raise HTTPException(status_code=404, detail="missing")

    monkeypatch.setattr(
        "plugin.server.application.config.command_service.infrastructure_replace_plugin_config",
        _raise_http,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await service.replace_plugin_config(plugin_id="demo", config={"runtime": {"enabled": True}})

    assert exc_info.value.code == "PLUGIN_CONFIG_REPLACE_FAILED"
    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "missing"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_update_plugin_config_maps_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigCommandService()

    def _raise_runtime(plugin_id: str, updates: dict[str, object]) -> dict[str, object]:
        raise ValueError("boom")

    monkeypatch.setattr(
        "plugin.server.application.config.command_service.infrastructure_update_plugin_config",
        _raise_runtime,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await service.update_plugin_config(plugin_id="demo", updates={"runtime": {"enabled": True}})

    assert exc_info.value.code == "PLUGIN_CONFIG_UPDATE_FAILED"
    assert exc_info.value.status_code == 500


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_upsert_profile_config_rejects_non_bool_make_active() -> None:
    service = ConfigCommandService()

    with pytest.raises(ServerDomainError) as exc_info:
        await service.upsert_plugin_profile_config(
            plugin_id="demo",
            profile_name="dev",
            config={"runtime": {"enabled": True}},
            make_active="yes",
        )

    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_hot_update_plugin_config_normalizes_mode_and_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigCommandService()
    captured: dict[str, object] = {}

    async def _fake_hot_update_plugin_config(
        *,
        plugin_id: str,
        updates: dict[str, object],
        mode: str,
        profile: str | None,
    ) -> dict[str, object]:
        captured.update({"plugin_id": plugin_id, "updates": updates, "mode": mode, "profile": profile})
        return {"success": True}

    monkeypatch.setattr(
        "plugin.server.application.config.command_service.application_hot_update_plugin_config",
        _fake_hot_update_plugin_config,
    )

    payload = await service.hot_update_plugin_config(
        plugin_id="demo",
        updates={"runtime": {"enabled": True}},
        mode="  TEMPORARY  ",
        profile=" dev ",
    )

    assert payload == {"success": True}
    assert captured["mode"] == "temporary"
    assert captured["profile"] == "dev"


import pytest
from fastapi import HTTPException

import plugin.server.application.config.command_service as cs
from plugin.server.application.config.command_service import ConfigCommandService
from plugin.server.domain.errors import ServerDomainError


@pytest.mark.plugin_unit
def test_command_service_helper_normalizers() -> None:
    assert cs._to_message("x", fallback="y") == "x"
    assert cs._to_message(None, fallback="y") == "y"

    err = cs._from_http_exception(HTTPException(status_code=409, detail="conflict"), code="E", fallback_message="f")
    assert err.code == "E"
    assert err.status_code == 409
    assert err.message == "conflict"

    with pytest.raises(ServerDomainError):
        cs._normalize_profile_name("")
    assert cs._normalize_profile_name(" dev ") == "dev"

    with pytest.raises(ServerDomainError):
        cs._normalize_mode(1)
    with pytest.raises(ServerDomainError):
        cs._normalize_mode("x")
    assert cs._normalize_mode(" TEMPORARY ") == "temporary"

    assert cs._normalize_make_active(None) is None
    assert cs._normalize_make_active(True) is True
    with pytest.raises(ServerDomainError):
        cs._normalize_make_active("yes")

    with pytest.raises(ServerDomainError):
        cs._normalize_payload([], context="ctx")
    with pytest.raises(ServerDomainError):
        cs._normalize_payload({1: "x"}, context="ctx")
    assert cs._normalize_payload({"ok": 1}, context="ctx") == {"ok": 1}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, infra_name, kwargs, code",
    [
        ("replace_plugin_config", "infrastructure_replace_plugin_config", {"plugin_id": "demo", "config": {}}, "PLUGIN_CONFIG_REPLACE_FAILED"),
        ("update_plugin_config", "infrastructure_update_plugin_config", {"plugin_id": "demo", "updates": {}}, "PLUGIN_CONFIG_UPDATE_FAILED"),
        ("update_plugin_config_toml", "infrastructure_update_plugin_config_toml", {"plugin_id": "demo", "toml": "x=1"}, "PLUGIN_CONFIG_TOML_UPDATE_FAILED"),
    ],
)
async def test_command_service_base_methods_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    infra_name: str,
    kwargs: dict[str, object],
    code: str,
) -> None:
    service = ConfigCommandService()

    monkeypatch.setattr(cs, infra_name, lambda *a, **k: (_ for _ in ()).throw(HTTPException(status_code=404, detail="missing")))
    with pytest.raises(ServerDomainError) as exc_http:
        await getattr(service, method_name)(**kwargs)
    assert exc_http.value.code == code
    assert exc_http.value.status_code == 404

    monkeypatch.setattr(cs, infra_name, lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_rt:
        await getattr(service, method_name)(**kwargs)
    assert exc_rt.value.code == code
    assert exc_rt.value.status_code == 500


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_command_service_profile_methods_error_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigCommandService()

    monkeypatch.setattr(cs, "infrastructure_upsert_profile_config", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=400, detail="bad")))
    with pytest.raises(ServerDomainError) as exc_upsert_http:
        await service.upsert_plugin_profile_config(plugin_id="demo", profile_name="dev", config={}, make_active=None)
    assert exc_upsert_http.value.code == "PLUGIN_PROFILE_UPSERT_FAILED"

    monkeypatch.setattr(cs, "infrastructure_upsert_profile_config", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_upsert_rt:
        await service.upsert_plugin_profile_config(plugin_id="demo", profile_name="dev", config={}, make_active=None)
    assert exc_upsert_rt.value.code == "PLUGIN_PROFILE_UPSERT_FAILED"

    monkeypatch.setattr(cs, "infrastructure_delete_profile_config", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404, detail="missing")))
    with pytest.raises(ServerDomainError) as exc_del_http:
        await service.delete_plugin_profile_config(plugin_id="demo", profile_name="dev")
    assert exc_del_http.value.code == "PLUGIN_PROFILE_DELETE_FAILED"

    monkeypatch.setattr(cs, "infrastructure_delete_profile_config", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_del_rt:
        await service.delete_plugin_profile_config(plugin_id="demo", profile_name="dev")
    assert exc_del_rt.value.code == "PLUGIN_PROFILE_DELETE_FAILED"

    monkeypatch.setattr(cs, "infrastructure_set_active_profile", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404, detail="missing")))
    with pytest.raises(ServerDomainError) as exc_set_http:
        await service.set_plugin_active_profile(plugin_id="demo", profile_name="dev")
    assert exc_set_http.value.code == "PLUGIN_PROFILE_ACTIVATE_FAILED"

    monkeypatch.setattr(cs, "infrastructure_set_active_profile", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_set_rt:
        await service.set_plugin_active_profile(plugin_id="demo", profile_name="dev")
    assert exc_set_rt.value.code == "PLUGIN_PROFILE_ACTIVATE_FAILED"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_command_service_hot_update_error_mapping_and_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigCommandService()

    monkeypatch.setattr(cs, "application_hot_update_plugin_config", lambda **kwargs: (_ for _ in ()).throw(HTTPException(status_code=400, detail="bad")))
    with pytest.raises(ServerDomainError) as exc_http:
        await service.hot_update_plugin_config(plugin_id="demo", updates={}, mode="temporary", profile=None)
    assert exc_http.value.code == "PLUGIN_CONFIG_HOT_UPDATE_FAILED"

    monkeypatch.setattr(cs, "application_hot_update_plugin_config", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ServerDomainError) as exc_rt:
        await service.hot_update_plugin_config(plugin_id="demo", updates={}, mode="temporary", profile=None)
    assert exc_rt.value.code == "PLUGIN_CONFIG_HOT_UPDATE_FAILED"

    async def _raise_domain(**kwargs):  # noqa: ANN003
        raise ServerDomainError(code="X", message="x", status_code=499, details={})

    monkeypatch.setattr(cs, "application_hot_update_plugin_config", _raise_domain)
    with pytest.raises(ServerDomainError) as exc_domain:
        await service.hot_update_plugin_config(plugin_id="demo", updates={}, mode="temporary", profile=None)
    assert exc_domain.value.code == "X"
