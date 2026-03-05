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
