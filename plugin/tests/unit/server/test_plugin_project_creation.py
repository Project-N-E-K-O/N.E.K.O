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
        identity = request(tmp_path)
        identity.pop("parent_dir")
        for endpoint in ("browser/preview", "browser/bundle"):
            assert (
                await http.post(f"/plugin-projects/{endpoint}", json=identity)
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
@pytest.mark.parametrize("plugin_type", ["plugin", "adapter"])
@pytest.mark.parametrize("packaged", [False, True])
async def test_browser_bundle_is_portable_and_preserves_a_real_git_repository(
    app, tmp_path, monkeypatch, plugin_type, packaged
):
    if packaged:
        monkeypatch.setitem(service.__dict__, "__compiled__", object())
    identity = request(tmp_path, plugin_type=plugin_type)
    identity.pop("parent_dir")
    async with client(app) as http:
        preview = await http.post("/plugin-projects/browser/preview", json=identity)
        assert preview.status_code == 200, preview.text
        assert list(tmp_path.iterdir()) == []
        bundle = await http.post("/plugin-projects/browser/bundle", json=identity)
        assert bundle.status_code == 200, bundle.text
    result = bundle.json()
    assert {key: result[key] for key in preview.json()} == preview.json()
    assert result["created"] is False
    assert result["project_dir"] == "project_example"
    target = tmp_path / result["plugin_id"]
    target.mkdir()
    for directory in result["directories"]:
        (target / directory).mkdir(parents=True, exist_ok=True)
    for file in result["contents"]:
        path = target / file["path"]
        assert path.resolve().is_relative_to(target.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(file["content_base64"]))
    assert not (target / ".git/hooks").exists()
    assert not (target / ".git/config").exists()
    assert set(preview.json()["files"]) - {".git/"} == {
        file["path"]
        for file in result["contents"]
        if not file["path"].startswith(".git/")
    }
    assert (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "symbolic-ref", "HEAD"],
            cwd=target,
            text=True,
        )
    ).strip() == "refs/heads/main"
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
            subprocess.check_output, ["git", "remote"], cwd=target, text=True
        )
    ) == ""
    assert json.loads((target / ".vscode/tasks.json").read_text())["tasks"] == []
    assert (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=target,
            text=True,
        )
    ).strip() == "true"
    assert "uv run --project" not in (target / "README.md").read_text(encoding="utf-8")
