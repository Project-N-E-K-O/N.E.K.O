from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.config.query_service import ConfigQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import config_paths


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

    monkeypatch.setattr(service, "get_plugin_effective_base_config", _base)
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

    monkeypatch.setattr(service, "get_plugin_effective_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay)

    payload = await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")
    assert payload["config"] == {
        "runtime": {"enabled": True, "level": 2},
        "feature": {"a": 1, "b": 2},
    }
    assert payload["effective_profile"] == "dev"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_keeps_manifest_tables_for_named_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_id = "named_profile_manifest_demo"
    storage_root = tmp_path / "runtime-storage"
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / plugin_id
    installed_dir.mkdir(parents=True)
    (installed_dir / "plugin.toml").write_text(
        (
            "[plugin]\n"
            f"id = '{plugin_id}'\n"
            "version = '2.0.0'\n"
            "entry = 'plugins.demo:Demo'\n"
            "\n[plugin.config_profiles]\n"
            "active = 'prod'\n"
            "\n[plugin.config_profiles.files]\n"
            "dev = 'dev.toml'\n"
            "prod = 'prod.toml'\n"
            "\n[adapter]\n"
            "mode = 'gateway'\n"
            "priority = 1\n"
            "label = 'manifest'\n"
            "\n[plugin_state]\n"
            "backend = 'file'\n"
        ),
        encoding="utf-8",
    )
    (installed_dir / "config.example.toml").write_text(
        "[adapter]\npriority = 2\nlabel = 'runtime'\n\n[plugin_state]\npersist_mode = 'auto'\n",
        encoding="utf-8",
    )
    (installed_dir / "dev.toml").write_text("[adapter]\npriority = 3\n", encoding="utf-8")
    (installed_dir / "prod.toml").write_text(
        "[adapter]\npriority = 4\nprod_only = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    payload = await ConfigQueryService().get_plugin_effective_config(
        plugin_id=plugin_id,
        profile_name="dev",
    )

    assert payload["config"] == {
        "plugin": {
            "id": plugin_id,
            "version": "2.0.0",
            "entry": "plugins.demo:Demo",
            "config_profiles": {
                "active": "prod",
                "files": {"dev": "dev.toml", "prod": "prod.toml"},
            },
        },
        "adapter": {"mode": "gateway", "priority": 3, "label": "runtime"},
        "plugin_state": {"backend": "file", "persist_mode": "auto"},
    }
    assert payload["effective_profile"] == "dev"
    base_payload = await ConfigQueryService().get_plugin_base_config(plugin_id=plugin_id)
    assert base_payload["config"] == {
        "adapter": {"priority": 2, "label": "runtime"},
        "plugin_state": {"persist_mode": "auto"},
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_effective_config_rejects_bad_base_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ConfigQueryService()

    async def _base(*, plugin_id: str) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": "bad"}

    async def _overlay(*, plugin_id: str, profile_name: object) -> dict[str, object]:
        return {"plugin_id": plugin_id, "config": {}}

    monkeypatch.setattr(service, "get_plugin_effective_base_config", _base)
    monkeypatch.setattr(service, "get_plugin_profile_config", _overlay)

    with pytest.raises(ServerDomainError) as exc_info:
        await service.get_plugin_effective_config(plugin_id="demo", profile_name="dev")

    assert exc_info.value.code == "INVALID_DATA_SHAPE"
