"""Read-only publish status of a workbench project."""

from __future__ import annotations

import asyncio

import pytest

from plugin.neko_plugin_cli.repo_state import RepoState
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



def test_fresh_is_passed_through_to_the_remote_read(monkeypatch, tmp_path):
    state = RepoState(
        git_available=True,
        is_repository=True,
        tag="v0.1.0",
        github_repository="owner/repo",
        head="a" * 40,
        detached=False,
        dirty_paths=("notes.txt",),
    )
    local = publish_status.LocalStatus(tmp_path, "demo", "0.1.0", state, {})
    monkeypatch.setattr(publish_status, "read_local_status", lambda _id: local)
    calls = []

    async def remote(*args, fresh=False):
        calls.append((args, fresh))
        return {"reachable": True}

    monkeypatch.setattr(publish_status, "read_remote_github", remote)
    result = asyncio.run(publish_status.get_publish_status("p1", fresh=True))
    assert calls == [(("owner/repo", "a" * 40, "v0.1.0", "demo"), True)]
    assert result["repository"]["dirty_paths"] == ("notes.txt",)
    assert result["repository"]["detached"] is False
