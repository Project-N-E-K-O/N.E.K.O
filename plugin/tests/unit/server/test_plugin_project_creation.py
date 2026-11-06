from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.neko_plugin_cli.core import project as creator
from plugin.server.application import plugin_projects as service
from plugin.core.state import state as runtime_state
from plugin.server.application.plugins import lifecycle_service, operation_lock, registry_service
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.plugin_project_errors import ProjectErrorCode
from plugin.server.infrastructure import (
    autostart_approvals,
    runtime_overrides,
)
from plugin.server.infrastructure.mutation_auth import csrf_token, require_plugin_mutation_access
from plugin.server.routes import plugin_projects as routes
from plugin.server.routes.plugin_projects import router
from utils import native_directory

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path_factory):
    # Kept outside tmp_path, which several tests assert stays empty.
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: state / "plugins")
    monkeypatch.setattr(
        operation_lock, "_operation_file_lock_path", lambda: state / "operation.lock"
    )
    # Registered projects are published to the runtime registry.
    monkeypatch.setattr(runtime_state, "plugins", {})
    monkeypatch.setattr(autostart_approvals, "_cache", set())
    monkeypatch.setattr(autostart_approvals, "_load_failed", False)
    monkeypatch.setattr(autostart_approvals, "_save_locked", lambda _value: True)
    monkeypatch.setattr(runtime_overrides, "_cache", None)
    monkeypatch.setattr(runtime_overrides, "_load_from_disk", lambda: {})
    monkeypatch.setattr(runtime_overrides, "_save_to_disk", lambda _value: None)
    return state


def make_app(*, guarded: bool = False) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    if not guarded:
        application.dependency_overrides[require_plugin_mutation_access] = lambda: None
    return application


@pytest.fixture
def app():
    return make_app()


def client(app, *, origin=ORIGIN):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url=ORIGIN,
        headers={"Origin": origin, **({"X-CSRF-Token": csrf_token()} if require_plugin_mutation_access in app.dependency_overrides else {})},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("author_table", [True, False])
async def test_metadata_update_preserves_project_identity_and_other_manifest_values(
    app, tmp_path, author_table
):
    import tomli_w

    source = _scaffold(tmp_path, "editable_project")
    manifest = source / "plugin.toml"
    original = tomllib.loads(manifest.read_text(encoding="utf-8"))
    original["plugin"]["author"] = (
        {"name": "Original", "email": "author@example.com"} if author_table else "Original"
    )
    original["custom_config"] = {"enabled": True, "items": ["a", "b"]}
    manifest.write_text(tomli_w.dumps(original), encoding="utf-8")
    record = store.add_record(source)
    store_before = store.store_path().read_bytes()
    pyproject_before = (source / "pyproject.toml").read_bytes()
    metadata = {
        "name": "Updated 项目",
        "version": "0.2.0",
        "author": "New author",
        "description": 'A "quoted" description\nWith another line',
    }
    async with client(app) as http:
        result = await http.put(f"/plugin-projects/{record.project_id}/metadata", json=metadata)
        listed = await http.get("/plugin-projects")
    assert result.status_code == 200, result.text
    for field, value in metadata.items():
        assert result.json()[field] == value
        assert listed.json()[0][field] == value
    expected = original
    expected["plugin"].update({key: value for key, value in metadata.items() if key != "author"})
    if author_table:
        expected["plugin"]["author"]["name"] = metadata["author"]
    else:
        expected["plugin"]["author"] = metadata["author"]
    assert tomllib.loads(manifest.read_text(encoding="utf-8")) == expected
    assert store.store_path().read_bytes() == store_before
    assert (source / "pyproject.toml").read_bytes() == pyproject_before
    assert result.json()["plugin_id"] == "editable_project"
    assert result.json()["loaded"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"plugin_id": "other"}, {"entry": "other:Plugin"}, {"source_dir": "other"},
    {"plugin_type": "adapter"}, {"name": " "}, {"version": ""}, {"author": "a" * 81},
])
async def test_metadata_update_rejects_invalid_or_read_only_fields(app, tmp_path, changes):
    source = _scaffold(tmp_path, "editable_project")
    record = store.add_record(source)
    manifest = source / "plugin.toml"
    before = manifest.read_bytes()
    payload = {"name": "Name", "version": "0.2.0", "author": "", "description": "", **changes}
    async with client(app) as http:
        result = await http.put(f"/plugin-projects/{record.project_id}/metadata", json=payload)
    assert result.status_code == 422
    assert manifest.read_bytes() == before


@pytest.mark.asyncio
async def test_metadata_write_failure_keeps_the_original_manifest(app, tmp_path, monkeypatch):
    source = _scaffold(tmp_path, "editable_project")
    record = store.add_record(source)
    manifest = source / "plugin.toml"
    before = manifest.read_bytes()

    def fail(*_args, **_kwargs):
        raise PermissionError("Read-only project")

    monkeypatch.setattr(service, "atomic_write_text", fail)
    async with client(app) as http:
        result = await http.put(f"/plugin-projects/{record.project_id}/metadata", json={
            "name": "New", "version": "0.2.0", "author": "", "description": "",
        })
    assert result.status_code == 500
    assert result.json()["detail"]["code"] == "PROJECT_IO_FAILED"
    assert manifest.read_bytes() == before


@pytest.mark.asyncio
async def test_metadata_updates_require_registration_valid_source_and_mutation_access(app, tmp_path):
    payload = {"name": "New", "version": "0.2.0", "author": "", "description": ""}
    async with client(app) as http:
        missing = await http.put("/plugin-projects/unknown/metadata", json=payload)
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
        record = store.add_record(tmp_path)
        invalid = await http.put(f"/plugin-projects/{record.project_id}/metadata", json=payload)
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["code"] == "INVALID_PROJECT_DIRECTORY"
    async with client(make_app(guarded=True), origin="https://untrusted.example") as http:
        denied = await http.put(f"/plugin-projects/{record.project_id}/metadata", json=payload)
    assert denied.status_code == 403


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
async def test_mutations_use_the_plugin_mutation_guard(tmp_path):
    # Like every other plugin mutation, a cross-site request without the
    # page token is rejected before any side effect.
    async with client(make_app(guarded=True), origin="http://evil.example") as http:
        for endpoint in ("preview", "create"):
            assert (
                await http.post(f"/plugin-projects/{endpoint}", json=request(tmp_path))
            ).status_code == 403
        assert (
            await http.post("/plugin-projects/register", json={"source_dir": str(tmp_path)})
        ).status_code == 403
        assert (await http.delete("/plugin-projects/any")).status_code == 403
        assert (await http.post("/plugin-projects/pick-directory", json={})).status_code == 403
        assert (
            await http.post("/plugin-projects/open-directory", json={"path": str(tmp_path)})
        ).status_code == 403
        assert (await http.get("/plugin-projects")).status_code == 200
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_untrusted_project_request_is_rejected_before_reading_its_body():
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b"invalid and potentially large JSON"

    async with client(make_app(guarded=True), origin="http://evil.example") as http:
        response = await http.post("/plugin-projects/create", content=body())
    assert response.status_code == 403
    assert not consumed


@pytest.mark.asyncio
async def test_invalid_registered_project_keeps_its_id_reserved(app, tmp_path):
    old = _scaffold(tmp_path / "old", "reserved")
    record = store.add_record(old)
    (old / "plugin.toml").write_text("broken [", encoding="utf-8")
    new = _scaffold(tmp_path / "new", "reserved")
    async with client(app) as http:
        response = await http.post("/plugin-projects/register", json={"source_dir": str(new)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_PLUGIN_ID_CONFLICT"
    assert [x.project_id for x in store.list_records()] == [record.project_id]


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
                "loaded": True,
                "running": False,
                "problem": None,
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


def _scaffold(parent: Path, plugin_id: str) -> Path:
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    target = parent / plugin_id
    creator.create_project(PluginSpec(plugin_id, quick_start=True), target, repo_root=None)
    return target


@pytest.mark.asyncio
async def test_registered_project_is_a_source_plugin_that_never_autostarts(app, tmp_path):
    from plugin.server.application.plugins.registry_service import (
        _get_autostart_plugin_ids_sync,
    )

    async with client(app) as http:
        created = await _create(http, tmp_path)
    meta = runtime_state.plugins["project_example"]
    assert meta["source"] == "project"
    assert meta["project_id"] == created["project_id"]
    assert meta["source_dir"] == created["project_dir"]
    assert meta["runtime_auto_start"] is False
    meta["runtime_auto_start"] = True
    assert "project_example" not in _get_autostart_plugin_ids_sync()


@pytest.mark.asyncio
async def test_existing_directories_can_be_registered_once(app, tmp_path):
    target = _scaffold(tmp_path, "existing")
    async with client(app) as http:
        registered = await http.post(
            "/plugin-projects/register", json={"source_dir": str(target)}
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["plugin_id"] == "existing"
        assert registered.json()["loaded"] is True
        again = await http.post(
            "/plugin-projects/register", json={"source_dir": str(target)}
        )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "PROJECT_ALREADY_REGISTERED"


@pytest.mark.asyncio
async def test_a_plugin_id_has_one_project(app, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    async with client(app) as http:
        await _create(http, first)
        preview = await http.post("/plugin-projects/preview", json=request(second))
        created = await http.post("/plugin-projects/create", json=request(second))
    assert preview.json()["detail"]["code"] == "PROJECT_PLUGIN_ID_CONFLICT"
    assert created.json()["detail"]["code"] == "PROJECT_PLUGIN_ID_CONFLICT"
    assert list(second.iterdir()) == []


@pytest.mark.asyncio
async def test_installed_plugins_keep_their_plugin_ids(app, tmp_path, monkeypatch):
    installed_root = tmp_path / "installed"
    _scaffold(installed_root, "taken")
    monkeypatch.setattr(settings, "PLUGIN_CONFIG_ROOTS", (installed_root,))
    project = _scaffold(tmp_path / "projects", "taken")
    async with client(app) as http:
        response = await http.post("/plugin-projects/register", json={"source_dir": str(project)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_PLUGIN_ID_CONFLICT"


async def _rediscover_from_scratch() -> None:
    """Discover every source into an empty registry, as a server start does."""
    runtime_state.plugins.clear()
    await registry_service.PluginRegistryService().refresh_registry()


@pytest.mark.asyncio
async def test_a_project_shadowed_by_a_later_install_says_why_it_is_not_loaded(
    app, tmp_path, monkeypatch
):
    async with client(app) as http:
        projects = tmp_path / "projects"
        projects.mkdir()
        created = await _create(http, projects)
        installed_root = tmp_path / "installed"
        installed = _scaffold(installed_root, created["plugin_id"])
        monkeypatch.setattr(settings, "PLUGIN_CONFIG_ROOTS", (installed_root,))
        monkeypatch.setattr(registry_service, "PLUGIN_CONFIG_ROOTS", (installed_root,))
        await _rediscover_from_scratch()
        [listed] = (await http.get("/plugin-projects")).json()
    assert (listed["status"], listed["loaded"], listed["problem"]) == (
        "ok",
        False,
        "id_taken",
    )
    assert str(installed / "plugin.toml") in listed["error"]


@pytest.mark.asyncio
async def test_a_project_the_source_rules_reject_says_why_it_is_not_loaded(app, tmp_path):
    async with client(app) as http:
        created = await _create(http, tmp_path)
        manifest = Path(created["project_dir"]) / "plugin.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                created["entry"], f"plugins.{created['plugin_id']}.absent:Plugin"
            ),
            encoding="utf-8",
        )
        await _rediscover_from_scratch()
        [listed] = (await http.get("/plugin-projects")).json()
    assert (listed["status"], listed["loaded"], listed["problem"]) == (
        "ok",
        False,
        "source_rejected",
    )
    assert "Entry Python source not found" in listed["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lifecycle_running", "problem"),
    [
        # An embedded server before user plugins are switched on: nothing is
        # registered, and the valid project is not blamed for it.
        (False, "runtime_inactive"),
        (True, "not_registered"),
    ],
)
async def test_a_valid_project_missing_from_the_registry_is_not_called_invalid(
    app, tmp_path, monkeypatch, lifecycle_running, problem
):
    from plugin.server import lifecycle

    async with client(app) as http:
        await _create(http, tmp_path)
        # Empty registry, as an embedded server's before its lifecycle starts.
        runtime_state.plugins.clear()
        monkeypatch.setattr(lifecycle, "lifecycle_running", lambda: lifecycle_running)
        [listed] = (await http.get("/plugin-projects")).json()
    assert (listed["status"], listed["loaded"], listed["problem"]) == ("ok", False, problem)
    assert listed["error"] is None


@pytest.mark.asyncio
async def test_a_refused_manifest_reports_the_runtime_reason(app, tmp_path, monkeypatch):
    from plugin.core import registry as core_registry
    from plugin.server import lifecycle

    def refuse(path, _processed, logger, **_kwargs):
        logger.error("Failed to parse plugin config {}: {}", path, "Invalid value (at line 3)")
        return None

    async with client(app) as http:
        await _create(http, tmp_path)
        # A running plugin system whose manifest parser refuses the project.
        runtime_state.plugins.clear()
        monkeypatch.setattr(lifecycle, "lifecycle_running", lambda: True)
        monkeypatch.setattr(core_registry, "_parse_single_plugin_config", refuse)
        [listed] = (await http.get("/plugin-projects")).json()
    assert (listed["loaded"], listed["problem"]) == (False, "config_rejected")
    # The runtime's own words, not a generic sentence.
    assert "Invalid value (at line 3)" in listed["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "meta", "hot_reload_failed", "problem", "error"),
    [
        (
            False,
            {"runtime_load_state": "failed", "runtime_load_error_message": "No module named 'x'"},
            False,
            "load_failed",
            "No module named 'x'",
        ),
        (False, {}, True, "reload_failed", None),
        (
            True,
            {"runtime_startup_state": "degraded", "runtime_startup_error": "startup failed"},
            False,
            "startup_degraded",
            "startup failed",
        ),
        (True, {"runtime_startup_state": "ready"}, False, None, None),
    ],
)
async def test_a_loaded_project_reports_the_runtime_problem_the_plugin_pages_see(
    app, tmp_path, monkeypatch, running, meta, hot_reload_failed, problem, error
):
    async with client(app) as http:
        created = await _create(http, tmp_path)
        plugin_id = created["plugin_id"]
        runtime_state.plugins[plugin_id].update(meta)
        monkeypatch.setattr(lifecycle_service, "plugin_is_running_sync", lambda _pid: running)
        monkeypatch.setattr(
            lifecycle_service, "_hot_reload_failed", {plugin_id} if hot_reload_failed else set()
        )
        [listed] = (await http.get("/plugin-projects")).json()
    assert (listed["loaded"], listed["running"], listed["problem"], listed["error"]) == (
        True,
        running,
        problem,
        error,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relative", "missing", "not_a_plugin", "renamed"])
async def test_only_loadable_plugin_directories_can_be_registered(app, tmp_path, kind):
    if kind == "renamed":
        source = _scaffold(tmp_path, "named")
        path = str(source.rename(tmp_path / "other_name"))
    else:
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
    async with client(app) as http:
        broken = await _create(http, tmp_path, plugin_id="broken_one")
        moved = await _create(http, tmp_path, plugin_id="moved_one")
        (Path(broken["project_dir"]) / "plugin.toml").write_text("not toml [", encoding="utf-8")
        Path(moved["project_dir"]).rename(tmp_path / "moved")
        listed = (await http.get("/plugin-projects")).json()
    assert [(item["status"], item["name"], item["loaded"]) for item in listed] == [
        ("invalid", None, False),
        ("missing", None, False),
    ]
    assert listed[0]["error"]
    assert listed[1]["source_dir"] == moved["project_dir"]


@pytest.mark.asyncio
async def test_removing_a_running_project_stops_and_unloads_it_but_keeps_files(
    app, tmp_path, monkeypatch
):
    stopped = []

    async def stop(self, plugin_id, **_kwargs):
        stopped.append(plugin_id)
        return {"success": True}

    monkeypatch.setattr(lifecycle_service, "plugin_is_running_sync", lambda _pid: True)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    async with client(app) as http:
        created = await _create(http, tmp_path)
        removed = await http.delete(f"/plugin-projects/{created['project_id']}")
        assert removed.status_code == 200, removed.text
        assert (await http.get("/plugin-projects")).json() == []
        again = await http.delete(f"/plugin-projects/{created['project_id']}")
    assert stopped == ["project_example"]
    assert "project_example" not in runtime_state.plugins
    assert again.status_code == 404
    assert again.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    assert (Path(created["project_dir"]) / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_a_project_that_does_not_stop_stays_registered(app, tmp_path, monkeypatch):
    async def stop(self, plugin_id, **_kwargs):
        return {"success": False}

    monkeypatch.setattr(lifecycle_service, "plugin_is_running_sync", lambda _pid: True)
    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    async with client(app) as http:
        created = await _create(http, tmp_path)
        removed = await http.delete(f"/plugin-projects/{created['project_id']}")
        listed = (await http.get("/plugin-projects")).json()
    assert removed.status_code == 409
    assert removed.json()["detail"]["code"] == "PROJECT_STOP_FAILED"
    assert [item["project_id"] for item in listed] == [created["project_id"]]
    assert "project_example" in runtime_state.plugins


@pytest.mark.asyncio
async def test_a_project_host_that_survives_shutdown_is_retained(app, tmp_path, monkeypatch):
    class Host:
        async def start(self, **kwargs):
            pass

        async def shutdown(self, **kwargs):
            pass

        def is_alive(self):
            return True

    host = Host()
    monkeypatch.setattr(runtime_state, "plugin_hosts", {})
    monkeypatch.setattr(lifecycle_service, "emit_lifecycle_event", lambda event: None)
    async with client(app) as http:
        created = await _create(http, tmp_path)
        runtime_state.plugin_hosts["project_example"] = host
        removed = await http.delete(f"/plugin-projects/{created['project_id']}")
    assert removed.status_code == 409
    assert removed.json()["detail"]["code"] == "PROJECT_STOP_FAILED"
    assert runtime_state.plugin_hosts["project_example"] is host
    assert store.list_records()[0].project_id == created["project_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["publish", "precheck", "create-repo"])
async def test_active_project_operations_prevent_removal(app, tmp_path, operation):
    from plugin.server.application.plugin_project_commands import project_operation

    async with client(app) as http:
        created = await _create(http, tmp_path)
        with project_operation(created["project_id"], operation):
            removed = await http.delete(f"/plugin-projects/{created['project_id']}")
        assert removed.status_code == 409
        assert removed.json()["detail"]["code"] == "PLUGIN_OPERATION_BUSY"
        assert store.list_records()[0].project_id == created["project_id"]


def test_removal_keeps_the_record_when_the_runtime_write_lock_is_unavailable(tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "locked"))

    class Unavailable:
        def __enter__(self):
            raise TimeoutError("runtime lock unavailable")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(runtime_state, "acquire_plugins_write_lock", Unavailable)
    with pytest.raises(TimeoutError):
        service._remove_project_entry_sync(record.project_id)
    assert store.list_records()[0].project_id == record.project_id


def test_removal_keeps_runtime_metadata_when_the_store_write_fails(tmp_path, monkeypatch):
    record = store.add_record(_scaffold(tmp_path, "retained"))
    meta = {"source": "project", "project_id": record.project_id}
    runtime_state.plugins["retained"] = meta

    def fail(_id):
        raise ServerDomainError(code="PROJECT_STORE_WRITE_FAILED", message="locked", status_code=503)

    monkeypatch.setattr(store, "remove_record", fail)
    with pytest.raises(ServerDomainError):
        service._remove_project_entry_sync(record.project_id)
    assert runtime_state.plugins["retained"] is meta
    assert store.list_records()[0].project_id == record.project_id


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
    # The plugin ID check reads the store, so nothing is written to disk.
    assert created.json()["detail"]["code"] == "PROJECT_STORE_INVALID"
    assert list(tmp_path.iterdir()) == []
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
