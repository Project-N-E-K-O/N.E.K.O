"""The workbench previews and applies standard GitHub Actions updates."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.core.state import state as runtime_state
from plugin.neko_plugin_cli.core import project as creator
from plugin.neko_plugin_cli.templates.generator import (
    MARKET_ACTIONS_MANAGED_HEADER,
    PluginSpec,
    render_release_workflow,
)
from plugin.server.application import plugin_project_commands as commands
from plugin.server.application.plugins import operation_lock
from plugin.server.infrastructure import authoring_project_store as store
from plugin.server.infrastructure.mutation_auth import csrf_token, require_plugin_mutation_access
from plugin.server.routes.plugin_projects import router

ORIGIN = "http://127.0.0.1:48916"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path_factory):
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: state / "plugins")
    monkeypatch.setattr(
        operation_lock, "_operation_file_lock_path", lambda: state / "operation.lock"
    )
    monkeypatch.setattr(runtime_state, "plugins", {})
    commands._busy.clear()
    yield state
    commands._busy.clear()


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_plugin_mutation_access] = lambda: None
    return application


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token()},
    )


def _scaffold(parent: Path, plugin_id: str) -> Path:
    target = parent / plugin_id
    creator.create_project(PluginSpec(plugin_id, quick_start=True), target, repo_root=None)
    return target


def _outdated_release(source_dir: Path, plugin_id: str) -> Path:
    """The current managed workflow minus its managed marker: a safe upgrade."""
    release = source_dir / ".github" / "workflows" / "release.yml"
    release.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_release_workflow(PluginSpec(plugin_id=plugin_id))
    release.write_text(rendered.removeprefix(MARKET_ACTIONS_MANAGED_HEADER), encoding="utf-8")
    return release


_UNMANAGED = "outdated: true\n"


def _changes(body) -> dict[str, str]:
    return {item["path"]: item["status"] for item in body}


async def test_workflow_preview_reports_upgrades_without_writing(app, tmp_path):
    source_dir = _scaffold(tmp_path, "flow_demo")
    release = _outdated_release(source_dir, "flow_demo")
    before = release.read_text(encoding="utf-8")
    record = store.add_record(source_dir)

    async with client(app) as http:
        response = await http.get(f"/plugin-projects/{record.project_id}/workflows")
    assert response.status_code == 200, response.text
    changes = _changes(response.json())
    assert changes[".github/workflows/release.yml"] == "UPGRADE"
    assert release.read_text(encoding="utf-8") == before


async def test_workflow_apply_writes_the_standard_files(app, tmp_path):
    source_dir = _scaffold(tmp_path, "flow_demo")
    release = _outdated_release(source_dir, "flow_demo")
    record = store.add_record(source_dir)

    async with client(app) as http:
        response = await http.post(f"/plugin-projects/{record.project_id}/workflows")
    assert response.status_code == 200, response.text
    changes = _changes(response.json())
    assert changes[".github/workflows/release.yml"] == "UPGRADE"
    assert release.read_text(encoding="utf-8").startswith(MARKET_ACTIONS_MANAGED_HEADER)
    # Second apply is a no-op.
    async with client(app) as http:
        again = await http.post(f"/plugin-projects/{record.project_id}/workflows")
    assert _changes(again.json())[".github/workflows/release.yml"] == "CURRENT"


async def test_a_conflicting_workflow_is_never_written(app, tmp_path):
    source_dir = _scaffold(tmp_path, "flow_demo")
    marker = source_dir / ".github" / "workflows" / "release.yml"
    marker.parent.mkdir(parents=True)
    marker.write_text(_UNMANAGED, encoding="utf-8")
    record = store.add_record(source_dir)

    async with client(app) as http:
        preview = await http.get(f"/plugin-projects/{record.project_id}/workflows")
        response = await http.post(f"/plugin-projects/{record.project_id}/workflows")
    assert _changes(preview.json())[".github/workflows/release.yml"] == "CONFLICT"
    assert response.status_code == 200, response.text
    assert _changes(response.json())[".github/workflows/release.yml"] == "CONFLICT"
    assert marker.read_text(encoding="utf-8") == _UNMANAGED
