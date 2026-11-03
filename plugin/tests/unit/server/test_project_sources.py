"""Workbench projects as external plugin sources of the runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin import settings
from plugin.core.state import state
from plugin.neko_plugin_cli.core import project as creator
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugin_cli.source_resolver import PluginSourceResolver
from plugin.server.application.plugins import lifecycle_service, project_sources, registry_service
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure import autostart_approvals
from plugin.server.infrastructure import authoring_project_store as store


def scaffold(parent: Path, plugin_id: str) -> Path:
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

    target = parent / plugin_id
    creator.create_project(PluginSpec(plugin_id, quick_start=True), target, repo_root=None)
    return target.resolve()


def project_meta(source: Path, project_id: str = "p1") -> dict:
    return {
        "id": source.name,
        "source": "project",
        "project_id": project_id,
        "source_dir": str(source),
        "config_path": str(source / "plugin.toml"),
    }


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    monkeypatch.setattr(settings, "get_plugin_state_root", lambda: tmp_path / "state" / "plugins")
    monkeypatch.setattr(settings, "PLUGIN_CONFIG_ROOTS", (installed,))
    monkeypatch.setattr(registry_service, "PLUGIN_CONFIG_ROOTS", (installed,))
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(state, "plugins", {})
    monkeypatch.setattr(autostart_approvals, "_cache", set())
    monkeypatch.setattr(autostart_approvals, "_load_failed", False)
    monkeypatch.setattr(autostart_approvals, "_save_locked", lambda _value: True)
    return installed


def test_the_first_project_owns_a_plugin_id(tmp_path):
    first = scaffold(tmp_path / "a", "demo")
    second = scaffold(tmp_path / "b", "demo")
    store.add_record(first)
    store.add_record(second)
    sources, failures = project_sources.list_project_sources_sync()
    assert [item.source_dir for item in sources] == [first]
    assert [item.source_dir for item in failures] == [second]
    assert "already provided" in failures[0].error


def test_projects_cannot_live_inside_managed_plugin_roots(isolated):
    inside = scaffold(isolated, "inside")
    with pytest.raises(ValueError, match="overlaps a managed plugin directory"):
        project_sources.inspect_source_directory(inside)


@pytest.mark.asyncio
async def test_discovery_publishes_projects_and_installed_ids_win(isolated, tmp_path):
    scaffold(isolated, "taken")
    store.add_record(scaffold(tmp_path / "projects", "taken"))
    mine = scaffold(tmp_path / "projects", "mine")
    record = store.add_record(mine)

    result = await registry_service.PluginRegistryService().refresh_registry()

    assert state.plugins["mine"]["source"] == "project"
    assert state.plugins["mine"]["project_id"] == record.project_id
    assert state.plugins["mine"]["runtime_auto_start"] is False
    assert state.plugins["taken"].get("source") != "project"
    assert any("conflicts with an installed plugin" in item["error"] for item in result["failed"])


@pytest.mark.asyncio
async def test_uninstall_never_deletes_a_project_source(tmp_path):
    source = scaffold(tmp_path, "mine")
    state.plugins["mine"] = project_meta(source)
    with pytest.raises(ServerDomainError) as caught:
        await lifecycle_service.PluginLifecycleService().delete_plugin("mine")
    assert caught.value.code == "PLUGIN_PROJECT_REMOVE_REQUIRED"
    assert (source / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_reload_keeps_a_running_project_when_its_source_breaks(tmp_path, monkeypatch):
    source = scaffold(tmp_path, "mine")
    (source / "__init__.py").write_text("def broken(:\n", encoding="utf-8")
    state.plugins["mine"] = project_meta(source)
    stopped = []

    async def stop(self, plugin_id, **_kwargs):
        stopped.append(plugin_id)
        return {"success": True}

    monkeypatch.setattr(lifecycle_service.PluginLifecycleService, "stop_plugin", stop)
    with pytest.raises(ServerDomainError) as caught:
        await lifecycle_service.PluginLifecycleService().reload_plugin("mine")
    assert caught.value.code == "PLUGIN_SOURCE_INVALID"
    assert stopped == []


def test_build_sources_include_projects(tmp_path):
    source = scaffold(tmp_path / "projects", "mine")
    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=tmp_path / "user",
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=tmp_path / "profiles",
    )
    resolver = PluginSourceResolver(policy, project_dirs=lambda: [source])

    listed = resolver.list_plugins()
    assert [(item.root_id, item.plugin_id, item.plugin_dir) for item in listed] == [
        ("project", "mine", source)
    ]
    by_ref = resolver.resolve_plugin_ref({"root_id": "project", "directory_name": "mine"})
    assert by_ref.plugin_dir == source
    assert resolver.resolve_string("mine").plugin_dir == source
    assert resolver.resolve_absolute_path(source).root_id == "project"
    with pytest.raises(FileNotFoundError):
        resolver.resolve_plugin_ref({"root_id": "project", "directory_name": "other"})


def test_a_project_builds_from_its_own_directory(tmp_path):
    from plugin.neko_plugin_cli.core.build import PluginBuilder

    source = scaffold(tmp_path / "projects", "mine")
    output = tmp_path / "packages" / "mine.neko-plugin"
    result = PluginBuilder(source_only_roots=(source,)).build_plugin(source, output)
    assert result.plugin_id == "mine"
    assert output.is_file()


def test_plugin_config_access_follows_the_project_source(tmp_path):
    from plugin.server.routes import config as config_routes

    source = scaffold(tmp_path / "projects", "mine")
    state.plugins["mine"] = project_meta(source)
    snapshot = config_routes._config_snapshot_sync("mine")
    assert snapshot is not None
    assert snapshot.manifest_path == source / "plugin.toml"

    # An unknown external path is never treated as a plugin source.
    state.plugins["mine"] = {"id": "mine", "config_path": str(source / "plugin.toml")}
    assert config_routes._config_snapshot_sync("mine") is None
