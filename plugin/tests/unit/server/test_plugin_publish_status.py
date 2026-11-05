"""Read-only publish status of a workbench project."""

from __future__ import annotations

import asyncio

import pytest

from plugin.server.application import plugin_publish_status as publish_status
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import authoring_project_store as store


def test_unknown_project_is_reported_as_not_found(monkeypatch):
    monkeypatch.setattr(store, "list_records", lambda: [])
    with pytest.raises(ServerDomainError) as caught:
        asyncio.run(publish_status.get_publish_status("missing"))
    assert caught.value.status_code == 404
    assert caught.value.code == "PROJECT_NOT_FOUND"


def test_project_without_a_repository_skips_every_remote_read(monkeypatch, tmp_path):
    (tmp_path / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nname = "Demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    record = store.ProjectRecord("p1", tmp_path, "2026-11-05T00:00:00+00:00")
    monkeypatch.setattr(store, "list_records", lambda: [record])

    async def forbidden(*args, **kwargs):
        raise AssertionError("GitHub must not be read without a GitHub origin")

    monkeypatch.setattr(publish_status, "read_remote_github", forbidden)
    result = asyncio.run(publish_status.get_publish_status("p1"))
    assert result["plugin_id"] == "demo"
    assert result["version"] == "0.1.0"
    assert result["repository"]["is_repository"] is False
    assert result["repository"]["tag"] == "v0.1.0"
    assert result["remote"] is None
    assert set(result["actions"].values()) == {"ADD"}
