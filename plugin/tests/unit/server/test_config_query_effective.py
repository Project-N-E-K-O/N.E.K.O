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
