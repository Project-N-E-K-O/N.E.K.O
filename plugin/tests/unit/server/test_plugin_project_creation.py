from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from plugin.neko_plugin_cli.core import project as creator
from plugin.server.application import plugin_projects as service
from plugin.server.infrastructure import development_access
from plugin.server.routes import plugin_projects as routes
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        development_access,
        "_configured_development_origins",
        lambda: frozenset({ORIGIN}),
    )
    application = FastAPI()
    application.include_router(router)
    return application


def client(app, *, peer="127.0.0.1", origin=ORIGIN, header=True):
    headers = {"Origin": origin}
    if header:
        headers["X-Neko-Development"] = "1"
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url=ORIGIN,
        headers=headers,
    )


def request(parent: Path, **changes):
    return {
        "parent_dir": str(parent),
        "plugin_id": "project_example",
        "name": "Example 项目",
        "description": 'A "quoted" description\nSecond line',
        "author": "Developer",
        "plugin_type": "plugin",
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("plugin_type", ["plugin", "adapter"])
async def test_preview_and_creation_share_the_real_scaffold(
    app, tmp_path, monkeypatch, packaged, plugin_type
):
    if packaged:
        monkeypatch.setitem(service.__dict__, "__compiled__", object())
        assert service.source_repo_root() is None
    parent = tmp_path / "插件 projects"
    parent.mkdir()
    body = request(parent, plugin_type=plugin_type)
    async with client(app) as http:
        preview = await http.post("/plugin-projects/preview", json=body)
        assert preview.status_code == 200, preview.text
        assert list(parent.iterdir()) == []
        result = preview.json()
        assert result["created"] is False
        assert result["entry"] == "plugin.plugins.project_example:ProjectExamplePlugin"
        assert result["project_dir"] == str(parent / "project_example")
        created = await http.post("/plugin-projects/create", json=body)
        assert created.status_code == 201, created.text
    target = parent / "project_example"
    assert created.json() == {**result, "created": True}
    assert {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(target).parts
    } == set(result["files"]) - {".git/"}
    import tomllib

    manifest = tomllib.loads((target / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["name"] == body["name"]
    assert manifest["plugin"]["description"] == body["description"]
    assert manifest["plugin"]["author"]["name"] == body["author"]
    assert "async def" in (target / "__init__.py").read_text(encoding="utf-8")
    git = await asyncio.to_thread(
        subprocess.run,
        ["git", "symbolic-ref", "HEAD"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
    )
    assert git.stdout.strip() == "refs/heads/main"
    assert (
        await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=target,
            capture_output=True,
        )
    ).returncode != 0
    assert (
        await asyncio.to_thread(
            subprocess.run,
            ["git", "remote"],
            cwd=target,
            capture_output=True,
            text=True,
        )
    ).stdout == ""
    if packaged:
        assert "uv run --project" not in (target / "README.md").read_text(
            encoding="utf-8"
        )
        assert json.loads((target / ".vscode/tasks.json").read_text())["tasks"] == []


@pytest.mark.asyncio
async def test_existing_directory_is_never_overwritten_even_after_preview(
    app, tmp_path
):
    async with client(app) as http:
        assert (
            await http.post("/plugin-projects/preview", json=request(tmp_path))
        ).status_code == 200
        target = tmp_path / "project_example"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("existing content")
        response = await http.post("/plugin-projects/create", json=request(tmp_path))
    assert response.status_code == 409
    assert sentinel.read_text() == "existing content"
    assert list(target.iterdir()) == [sentinel]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"plugin_id": "../escape"},
        {"plugin_id": "UpperCase"},
        {"plugin_type": "extension"},
        {"name": "   "},
    ],
)
async def test_invalid_identity_cannot_write(app, tmp_path, changes):
    async with client(app) as http:
        result = await http.post(
            "/plugin-projects/create", json=request(tmp_path, **changes)
        )
    assert result.status_code == 422
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer,origin,header",
    [
        ("192.168.1.4", ORIGIN, True),
        ("127.0.0.1", "http://evil.example", True),
        ("127.0.0.1", ORIGIN, False),
    ],
)
async def test_filesystem_routes_require_local_development_access(
    app, tmp_path, peer, origin, header
):
    async with client(app, peer=peer, origin=origin, header=header) as http:
        for endpoint in ("preview", "create"):
            assert (
                await http.post(f"/plugin-projects/{endpoint}", json=request(tmp_path))
            ).status_code == 403
        assert (
            await http.post("/plugin-projects/pick-directory", json={})
        ).status_code == 403
        assert (
            await http.post(
                "/plugin-projects/open-directory", json={"path": str(tmp_path)}
            )
        ).status_code == 403
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_git_leaves_no_partial_project(app, tmp_path, monkeypatch):
    monkeypatch.setattr(creator.shutil, "which", lambda name: None)
    async with client(app) as http:
        result = await http.post("/plugin-projects/create", json=request(tmp_path))
    assert result.json()["detail"]["code"] == "GIT_NOT_FOUND"
    assert list(tmp_path.iterdir()) == []


def test_failed_git_initialization_rolls_back_only_the_new_directory(tmp_path):
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep")

    def fail(*args, **kwargs):
        raise RuntimeError("Git initialization failed")

    with pytest.raises(RuntimeError):
        creator.create_project(
            PluginSpec("example", quick_start=True),
            tmp_path / "example",
            repo_root=None,
            git_runner=fail,
        )
    assert list(tmp_path.iterdir()) == [sentinel]


@pytest.mark.asyncio
async def test_pick_directory_returns_the_native_selection(app, tmp_path, monkeypatch):
    seen = {}

    def pick(*, start_path):
        seen["start_path"] = start_path
        return str(tmp_path)

    monkeypatch.setattr(routes, "pick_directory", pick)
    async with client(app) as http:
        result = await http.post(
            "/plugin-projects/pick-directory", json={"start_path": "C:/start"}
        )
    assert result.status_code == 200
    assert result.json() == {
        "ok": True,
        "cancelled": False,
        "selected_root": str(tmp_path),
    }
    assert seen == {"start_path": "C:/start"}


@pytest.mark.asyncio
async def test_pick_directory_reports_cancellation_and_unavailable_dialogs(
    app, monkeypatch
):
    def cancel(*, start_path):
        raise routes.DirectoryPickerCancelled()

    def unavailable(*, start_path):
        raise routes.DirectoryPickerUnavailable("directory_picker_unavailable", "no dialog")

    async with client(app) as http:
        monkeypatch.setattr(routes, "pick_directory", cancel)
        cancelled = await http.post("/plugin-projects/pick-directory", json={})
        assert cancelled.json() == {"ok": True, "cancelled": True, "selected_root": ""}
        monkeypatch.setattr(routes, "pick_directory", unavailable)
        failed = await http.post("/plugin-projects/pick-directory", json={})
    assert failed.status_code == 400
    assert failed.json()["detail"]["code"] == "directory_picker_unavailable"

