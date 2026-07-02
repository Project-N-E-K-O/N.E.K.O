from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from plugin.server.routes import plugin_ui as plugin_ui_route_module


class _PluginUiQueryService:
    def __init__(self, config_path: str):
        self._config_path = config_path

    async def get_plugin_meta(self, plugin_id: str) -> dict[str, str]:
        assert plugin_id == "demo"
        return {"config_path": self._config_path}


class _ActionService:
    def __init__(self):
        self.seen_timeout_seconds = None

    async def call_surface_action(self, *args, **kwargs):
        self.seen_timeout_seconds = kwargs.get("timeout_seconds")
        return {"ok": True}


@pytest.mark.asyncio
async def test_hosted_ui_artifact_treats_tilde_user_as_relative_path(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    monkeypatch.setattr(
        plugin_ui_route_module,
        "plugin_ui_query_service",
        _PluginUiQueryService(str(config_path)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await plugin_ui_route_module.plugin_hosted_ui_artifact(
            "demo",
            file_path="~definitely_missing_neko_user/artifact.png",
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_hosted_ui_artifact_only_serves_allowed_artifact_dirs(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "demo"
    artifacts_dir = plugin_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    (artifacts_dir / "report.txt").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(
        plugin_ui_route_module,
        "plugin_ui_query_service",
        _PluginUiQueryService(str(config_path)),
    )

    response = await plugin_ui_route_module.plugin_hosted_ui_artifact(
        "demo",
        file_path="artifacts/report.txt",
    )
    assert response.path == str(artifacts_dir / "report.txt")

    with pytest.raises(HTTPException) as exc_info:
        await plugin_ui_route_module.plugin_hosted_ui_artifact(
            "demo",
            file_path="plugin.toml",
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_hosted_ui_action_preserves_zero_timeout(monkeypatch):
    service = _ActionService()
    monkeypatch.setattr(plugin_ui_route_module, "plugin_ui_query_service", service)

    response = await plugin_ui_route_module.plugin_hosted_ui_action(
        "demo",
        "export",
        plugin_ui_route_module.HostedUiActionRequest(timeout_ms=0),
    )
    body = json.loads(response.body)

    assert body == {"ok": True}
    assert service.seen_timeout_seconds == 0.0
