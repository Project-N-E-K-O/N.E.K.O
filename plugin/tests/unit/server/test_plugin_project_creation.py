from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.neko_plugin_cli.core import project as creator
from plugin.server.application import plugin_projects as service
from plugin.server.application.plugins import development as development_store
from plugin.server.application.plugins import operation_lock
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode
from plugin.server.infrastructure import development_access, native_directory
from plugin.server.routes import plugin_projects as routes
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path_factory):
    # Kept outside tmp_path, which several tests assert stays empty.
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: state / "plugins")
    monkeypatch.setattr(
        operation_lock, "_operation_file_lock_path", lambda: state / "operation.lock"
    )
    monkeypatch.setattr(development_store, "list_registration_records_sync", lambda: [])
    return state


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
        assert "project_id" not in result
        assert result["entry"] == "plugin.plugins.project_example:ProjectExamplePlugin"
        assert result["project_dir"] == str(parent / "project_example")
        created = await http.post("/plugin-projects/create", json=body)
        assert created.status_code == 201, created.text
    target = parent / "project_example"
    assert created.json() == {**result, "project_id": created.json()["project_id"]}
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
        assert (await http.get("/plugin-projects")).status_code == 403
        assert (
            await http.post(
                "/plugin-projects/register", json={"source_dir": str(tmp_path)}
            )
        ).status_code == 403
        assert (await http.delete("/plugin-projects/any")).status_code == 403
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

    def pick(*, start_path, title):
        seen.update(start_path=start_path, title=title)
        return str(tmp_path)

    monkeypatch.setattr(native_directory, "pick_directory", pick)
    async with client(app) as http:
        result = await http.post(
            "/plugin-projects/pick-directory",
            json={"start_path": "C:/start", "title": "Parent directory"},
        )
    assert result.status_code == 200
    assert result.json() == {
        "ok": True,
        "cancelled": False,
        "selected_root": str(tmp_path),
    }
    assert seen == {"start_path": "C:/start", "title": "Parent directory"}


@pytest.mark.asyncio
async def test_pick_directory_reports_cancellation_and_unavailable_dialogs(
    app, monkeypatch
):
    def cancel(*, start_path, title):
        raise native_directory.DirectoryPickerCancelled()

    def unavailable(*, start_path, title):
        raise native_directory.NativeDirectoryError("DIRECTORY_PICKER_UNAVAILABLE", "no dialog")

    async with client(app) as http:
        monkeypatch.setattr(native_directory, "pick_directory", cancel)
        cancelled = await http.post("/plugin-projects/pick-directory", json={})
        assert cancelled.json() == {"ok": True, "cancelled": True, "selected_root": ""}
        monkeypatch.setattr(native_directory, "pick_directory", unavailable)
        failed = await http.post("/plugin-projects/pick-directory", json={})
    assert failed.status_code == 400
    assert failed.json()["detail"] == {
        "code": "DIRECTORY_PICKER_UNAVAILABLE",
        "message": "no dialog",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path_kind", ["relative", "missing"])
async def test_open_directory_rejects_unusable_paths_with_a_stable_code(
    app, tmp_path, path_kind
):
    path = "relative/dir" if path_kind == "relative" else str(tmp_path / "missing")
    async with client(app) as http:
        result = await http.post("/plugin-projects/open-directory", json={"path": path})
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "DIRECTORY_UNAVAILABLE"


@pytest.mark.parametrize("plugin_id", ["Example", "1example", "has-dash", ""])
def test_creator_and_route_share_the_plugin_id_rule(tmp_path, plugin_id):
    from pydantic import ValidationError

    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    with pytest.raises(creator.ProjectCreationError) as caught:
        creator.prepare_project(
            PluginSpec(plugin_id, quick_start=True),
            tmp_path / "target",
            repo_root=None,
        )
    assert caught.value.code == "INVALID_PLUGIN_ID"
    with pytest.raises(ValidationError):
        routes.ProjectIdentityRequest(plugin_id=plugin_id, name="Example")


def test_creator_rejects_a_trailing_newline_that_dollar_would_accept(tmp_path):
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    with pytest.raises(creator.ProjectCreationError):
        creator.prepare_project(
            PluginSpec("example\n", quick_start=True),
            tmp_path / "target",
            repo_root=None,
        )



# --- Persistent project list -------------------------------------------------


async def _create(http, parent: Path, **changes) -> dict:
    response = await http.post("/plugin-projects/create", json=request(parent, **changes))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_creation_renders_the_template_once(app, tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.templates import generator

    original = generator.render_plugin_files
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(generator, "render_plugin_files", counted)
    async with client(app) as http:
        await _create(http, tmp_path)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_created_project_is_listed_from_its_live_manifest(app, tmp_path):
    async with client(app) as http:
        created = await _create(http, tmp_path)
        listed = (await http.get("/plugin-projects")).json()
        assert listed == [
            {
                "project_id": created["project_id"],
                "source_dir": created["project_dir"],
                "added_at": listed[0]["added_at"],
                "status": "ok",
                "error": None,
                "plugin_id": "project_example",
                "name": "Example 项目",
                "description": 'A "quoted" description\nSecond line',
                "author": "Developer",
                "plugin_type": "plugin",
                "version": created["version"],
                "entry": created["entry"],
                "development": "unmounted",
            }
        ]
        manifest = Path(created["project_dir"]) / "plugin.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                'name = "Example 项目"', 'name = "Renamed"'
            ),
            encoding="utf-8",
        )
        assert (await http.get("/plugin-projects")).json()[0]["name"] == "Renamed"
    saved = json.loads(store.store_path().read_text(encoding="utf-8"))
    assert saved["projects"][0].keys() == {"project_id", "source_dir", "added_at"}


@pytest.mark.asyncio
async def test_existing_directories_can_be_registered_once(app, tmp_path):
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    target = tmp_path / "existing"
    creator.create_project(PluginSpec("existing", quick_start=True), target, repo_root=None)
    async with client(app) as http:
        registered = await http.post(
            "/plugin-projects/register", json={"source_dir": str(target)}
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["plugin_id"] == "existing"
        again = await http.post(
            "/plugin-projects/register", json={"source_dir": str(target)}
        )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "PROJECT_ALREADY_REGISTERED"


@pytest.mark.asyncio
async def test_same_plugin_id_may_live_in_two_directories(app, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    async with client(app) as http:
        await _create(http, first)
        await _create(http, second)
        listed = (await http.get("/plugin-projects")).json()
    assert [item["plugin_id"] for item in listed] == ["project_example"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relative", "missing", "not_a_plugin"])
async def test_only_existing_plugin_directories_can_be_registered(app, tmp_path, kind):
    path = {
        "relative": "relative/dir",
        "missing": str(tmp_path / "missing"),
        "not_a_plugin": str(tmp_path),
    }[kind]
    async with client(app) as http:
        response = await http.post("/plugin-projects/register", json={"source_dir": path})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_PROJECT_DIRECTORY"
        assert (await http.get("/plugin-projects")).json() == []


@pytest.mark.asyncio
async def test_unavailable_projects_are_marked_not_dropped(app, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    async with client(app) as http:
        broken = await _create(http, first)
        moved = await _create(http, second)
        (Path(broken["project_dir"]) / "plugin.toml").write_text("not toml [", encoding="utf-8")
        Path(moved["project_dir"]).rename(tmp_path / "moved")
        listed = (await http.get("/plugin-projects")).json()
    assert [(item["status"], item["name"]) for item in listed] == [
        ("invalid", None),
        ("missing", None),
    ]
    assert listed[0]["error"]
    assert listed[1]["source_dir"] == moved["project_dir"]


@pytest.mark.asyncio
async def test_removing_a_project_keeps_files_and_development_mount(
    app, tmp_path, monkeypatch
):
    async with client(app) as http:
        created = await _create(http, tmp_path)
        source = Path(created["project_dir"])
        registration = development_store.DevelopmentSnapshot("dev", 1, "project_example", source)
        monkeypatch.setattr(
            development_store, "list_registration_records_sync", lambda: [registration]
        )
        assert (await http.get("/plugin-projects")).json()[0]["development"] == "mode_disabled"
        monkeypatch.setattr(development_store, "development_enabled_sync", lambda: True)
        assert (await http.get("/plugin-projects")).json()[0]["development"] == "mounted"
        removed = await http.delete(f"/plugin-projects/{created['project_id']}")
        assert removed.status_code == 200
        assert (await http.get("/plugin-projects")).json() == []
        again = await http.delete(f"/plugin-projects/{created['project_id']}")
    assert again.status_code == 404
    assert again.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    assert (source / "plugin.toml").is_file()
    assert development_store.list_registration_records_sync() == [registration]


@pytest.mark.asyncio
async def test_unreadable_development_store_reports_unknown_mount_state(
    app, tmp_path, monkeypatch
):
    def broken():
        raise development_store._error("broken", "DEVELOPMENT_STORE_INVALID", 500)

    async with client(app) as http:
        await _create(http, tmp_path)
        monkeypatch.setattr(development_store, "list_registration_records_sync", broken)
        listed = (await http.get("/plugin-projects")).json()
    assert listed[0]["status"] == "ok"
    assert listed[0]["development"] is None


@pytest.mark.asyncio
async def test_corrupt_store_is_reported_and_never_overwritten(app, tmp_path):
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    async with client(app) as http:
        listed = await http.get("/plugin-projects")
        created = await http.post("/plugin-projects/create", json=request(tmp_path))
    assert listed.status_code == 500
    assert listed.json()["detail"]["code"] == "PROJECT_STORE_INVALID"
    assert created.status_code == 500
    assert created.json()["detail"]["code"] == "PROJECT_REGISTER_FAILED"
    # The project is kept on disk; only the registration failed.
    assert (tmp_path / "project_example" / "plugin.toml").is_file()
    assert path.read_text(encoding="utf-8") == "{"


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"version": 2, "projects": []}',
        '{"version": 1, "projects": [{"project_id": "a", "source_dir": "rel", "added_at": "t"}]}',
        '{"version": 1, "projects": [], "extra": 1}',
    ],
)
def test_store_rejects_unexpected_content(content):
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ServerDomainError) as caught:
        store.list_records()
    assert caught.value.code == "PROJECT_STORE_INVALID"


def test_every_lower_layer_error_code_is_part_of_the_api_contract():
    declared = set(ProjectErrorCode)
    assert creator.PROJECT_CREATION_ERROR_CODES <= declared
    assert native_directory.NATIVE_DIRECTORY_ERROR_CODES <= declared
    with pytest.raises(ValueError):
        creator.ProjectCreationError("SOMETHING_NEW", "x")
    with pytest.raises(ValueError):
        native_directory.NativeDirectoryError("SOMETHING_NEW", "x")


@pytest.mark.asyncio
async def test_busy_operation_lock_is_reported_with_a_stable_code(app, tmp_path, monkeypatch):
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy

    async def busy(*args):
        raise PluginOperationBusy("held")

    monkeypatch.setattr(service, "_register_locked", busy)
    monkeypatch.setattr(service, "_remove_locked", busy)
    async with client(app) as http:
        registered = await http.post(
            "/plugin-projects/register", json={"source_dir": str(tmp_path)}
        )
        removed = await http.delete("/plugin-projects/any")
        created = await http.post("/plugin-projects/create", json=request(tmp_path))
    assert registered.status_code == removed.status_code == 409
    assert registered.json()["detail"]["code"] == "PLUGIN_OPERATION_BUSY"
    assert removed.json()["detail"]["code"] == "PLUGIN_OPERATION_BUSY"
    # The lock is only needed for registration; the created project is kept.
    assert created.json()["detail"]["code"] == "PROJECT_REGISTER_FAILED"
    assert (tmp_path / "project_example" / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_open_directory_errors_keep_their_native_code(app, tmp_path, monkeypatch):
    def fail(path):
        raise native_directory.NativeDirectoryError("OPEN_DIRECTORY_FAILED", "boom")

    monkeypatch.setattr(native_directory, "open_directory", fail)
    async with client(app) as http:
        result = await http.post("/plugin-projects/open-directory", json={"path": str(tmp_path)})
    assert result.status_code == 400
    assert result.json()["detail"] == {"code": "OPEN_DIRECTORY_FAILED", "message": "boom"}


@pytest.mark.asyncio
async def test_unreadable_store_is_reported_as_temporary_and_left_untouched(
    app, tmp_path, monkeypatch
):
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "projects": []}', encoding="utf-8")

    def locked(_path):
        raise PermissionError(13, "The file is being used by another process")

    monkeypatch.setattr(store, "read_json_tolerating_replace", locked)
    async with client(app) as http:
        listed = await http.get("/plugin-projects")
    assert listed.status_code == 503
    assert listed.json()["detail"]["code"] == "PROJECT_STORE_UNREADABLE"
    assert path.read_text(encoding="utf-8") == '{"version": 1, "projects": []}'


def test_store_write_failure_has_a_stable_code(tmp_path, monkeypatch):
    def fail(path, data):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(store, "atomic_write_json", fail)
    with pytest.raises(ServerDomainError) as caught:
        store.add_record(tmp_path)
    assert caught.value.code == "PROJECT_STORE_WRITE_FAILED"
    assert not store.store_path().exists()


def test_store_round_trips_through_the_shared_atomic_writer(tmp_path):
    first = store.add_record(tmp_path / "a")
    second = store.add_record(tmp_path / "b")
    assert store.list_records() == [first, second]
    store.remove_record(first.project_id)
    assert store.list_records() == [second]
    leftovers = {p.name for p in store.store_path().parent.iterdir()} - {"operation.lock"}
    assert leftovers == {"plugin-authoring.json"}