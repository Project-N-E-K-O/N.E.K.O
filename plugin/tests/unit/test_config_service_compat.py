from __future__ import annotations

import pytest

from plugin.config import service as module


def test_load_plugin_config_delegates_to_server_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _impl(plugin_id: str, *, validate: bool = True) -> dict[str, object]:
        captured["plugin_id"] = plugin_id
        captured["validate"] = validate
        return {"plugin_id": plugin_id, "config": {"runtime": {"enabled": True}}}

    monkeypatch.setattr(
        "plugin.server.infrastructure.config_queries.load_plugin_config",
        _impl,
    )

    payload = module.load_plugin_config("demo", validate=False)

    assert payload["plugin_id"] == "demo"
    assert captured == {"plugin_id": "demo", "validate": False}


def test_update_plugin_config_delegates_to_server_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _impl(plugin_id: str, updates: dict[str, object]) -> dict[str, object]:
        captured["plugin_id"] = plugin_id
        captured["updates"] = updates
        return {"success": True}

    monkeypatch.setattr(
        "plugin.server.infrastructure.config_updates.update_plugin_config",
        _impl,
    )

    payload = module.update_plugin_config("demo", {"runtime": {"enabled": True}})

    assert payload == {"success": True}
    assert captured == {
        "plugin_id": "demo",
        "updates": {"runtime": {"enabled": True}},
    }


def test_profile_write_functions_delegate_to_server_profiles_write(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _upsert(*, plugin_id: str, profile_name: str, config: dict[str, object], make_active: bool | None) -> dict[str, object]:
        captured["upsert"] = (plugin_id, profile_name, config, make_active)
        return {"ok": "upsert"}

    def _delete(*, plugin_id: str, profile_name: str) -> dict[str, object]:
        captured["delete"] = (plugin_id, profile_name)
        return {"ok": "delete"}

    def _set_active(*, plugin_id: str, profile_name: str) -> dict[str, object]:
        captured["set_active"] = (plugin_id, profile_name)
        return {"ok": "active"}

    monkeypatch.setattr("plugin.server.infrastructure.config_profiles_write.upsert_profile_config", _upsert)
    monkeypatch.setattr("plugin.server.infrastructure.config_profiles_write.delete_profile_config", _delete)
    monkeypatch.setattr("plugin.server.infrastructure.config_profiles_write.set_active_profile", _set_active)

    assert module.upsert_plugin_profile_config("demo", "dev", {"runtime": {}}, make_active=True) == {"ok": "upsert"}
    assert module.delete_plugin_profile_config("demo", "dev") == {"ok": "delete"}
    assert module.set_plugin_active_profile("demo", "dev") == {"ok": "active"}
    assert captured == {
        "upsert": ("demo", "dev", {"runtime": {}}, True),
        "delete": ("demo", "dev"),
        "set_active": ("demo", "dev"),
    }


@pytest.mark.asyncio
async def test_hot_update_plugin_config_maps_domain_error_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.server.domain.errors import ServerDomainError

    async def _impl(
        *,
        plugin_id: str,
        updates: dict[str, object],
        mode: str = "temporary",
        profile: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, object]:
        raise ServerDomainError(
            code="PLUGIN_NOT_RUNNING",
            message="plugin is offline",
            status_code=409,
            details={"plugin_id": plugin_id},
        )

    monkeypatch.setattr(
        "plugin.server.application.config.hot_update_service.hot_update_plugin_config",
        _impl,
    )

    with pytest.raises(module.HTTPException) as exc_info:
        await module.hot_update_plugin_config("demo", {"runtime": {"enabled": True}})

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "plugin is offline"
