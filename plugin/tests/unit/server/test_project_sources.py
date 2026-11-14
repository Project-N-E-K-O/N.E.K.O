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


def test_cli_and_reload_scan_the_same_plugin_python_files(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.commands.validate_cmd import _plugin_python_files
    from plugin.utils.source_paths import PYTHON_SOURCE_EXCLUDED_DIR_NAMES

    source = scaffold(tmp_path, "scan_demo")
    original = set(_plugin_python_files(source))
    for name in (*PYTHON_SOURCE_EXCLUDED_DIR_NAMES, ".vendor.backup-0123abcd"):
        folder = source / name
        folder.mkdir(exist_ok=True)
        (folder / "dependency.py").write_text("broken syntax !\n", encoding="utf-8")
    owned = source / ".vendor.backup-notes"
    owned.mkdir()
    (owned / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    upper = source / "extra.PY"
    upper.write_text("VALUE = 1\n", encoding="utf-8")
    expected = original | {owned / "owned.py", upper}
    assert set(_plugin_python_files(source)) == expected
    monkeypatch.setattr(registry_service, "_build_discovery_payload", lambda *_a, **_kw: {})
    project_sources.preflight_source(source, "scan_demo")
    from plugin.server.application.plugins.hot_reload_service import _signature_sync

    assert set(_signature_sync(source)) == {
        path.relative_to(source).as_posix() for path in expected
    } | {"plugin.toml"}
    upper.write_text("broken syntax !\n", encoding="utf-8")
    with pytest.raises(ValueError):
        project_sources.preflight_source(source, "scan_demo")


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


def test_build_source_enumeration_reports_a_broken_project_store(monkeypatch):
    from plugin.server.application.plugin_cli.source_resolver import _registered_project_sources

    def broken():
        raise ServerDomainError(code="PROJECT_STORE_INVALID", message="broken store", status_code=500)

    monkeypatch.setattr(store, "list_records", broken)
    with pytest.raises(ServerDomainError) as caught:
        _registered_project_sources()
    assert caught.value.code == "PROJECT_STORE_INVALID"


@pytest.mark.asyncio
async def test_bad_project_payload_does_not_interrupt_other_discovery(isolated, tmp_path):
    scaffold(isolated, "managed")
    good = scaffold(tmp_path / "projects", "good")
    bad = scaffold(tmp_path / "projects", "bad")
    text = (bad / "plugin.toml").read_text(encoding="utf-8")
    text = text.replace('name = "bad"', 'name = "bad"\ndescription = ["invalid"]')
    (bad / "plugin.toml").write_text(text, encoding="utf-8")
    store.add_record(good)
    store.add_record(bad)
    result = await registry_service.PluginRegistryService().refresh_registry()
    assert {"managed", "good"}.issubset(state.plugins)
    assert any(x["config_path"] == str(bad / "plugin.toml") for x in result["failed"])


@pytest.mark.asyncio
async def test_config_write_and_project_removal_share_the_operation_lock(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from plugin.server.routes import config as config_routes
    from plugin.server.infrastructure.config_access import ConfigAccessSnapshot
    from plugin.server.application import plugin_projects

    source = scaffold(tmp_path, "config_demo")
    record = store.add_record(source)
    state.plugins["config_demo"] = project_meta(source, record.project_id)
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(config_routes, "_config_snapshot_sync", lambda pid: ConfigAccessSnapshot(pid, source / "plugin.toml", None))

    async def write(**kwargs):
        entered.set()
        await release.wait()
        return {"ok": True}

    writing = asyncio.create_task(config_routes._dispatch_config(SimpleNamespace(method="PUT"), write, plugin_id="config_demo"))
    await entered.wait()
    removing = asyncio.create_task(plugin_projects.remove_project(record.project_id))
    await asyncio.sleep(0.01)
    assert not removing.done()
    release.set()
    await writing
    await removing
    assert store.list_records() == []


def test_projects_cannot_live_inside_managed_plugin_roots(isolated):
    inside = scaffold(isolated, "inside")
    with pytest.raises(ValueError, match="overlaps a managed plugin directory"):
        project_sources.inspect_source_directory(inside)


def test_a_cloned_kebab_directory_loads_its_snake_package(tmp_path):
    """`git clone` keeps `my-cool-plugin/`; `plugin.entry` cannot contain '-'. (#3724)"""
    source = scaffold(tmp_path, "cloned_plugin")
    kebab = source.with_name("cloned-plugin")
    source.rename(kebab)
    manifest = kebab / "plugin.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'id = "cloned_plugin"', 'id = "cloned-plugin"'
        ),
        encoding="utf-8",
    )

    metadata = project_sources.inspect_source_directory(kebab.resolve())

    assert metadata.plugin_id == "cloned-plugin"
    assert metadata.entry.startswith("plugins.cloned_plugin:")


def test_a_project_directory_still_must_name_its_entry_package(tmp_path):
    source = scaffold(tmp_path, "cloned_plugin")
    renamed = source.with_name("something_else")
    source.rename(renamed)
    manifest = renamed / "plugin.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'id = "cloned_plugin"', 'id = "something_else"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="targets package"):
        project_sources.inspect_source_directory(renamed.resolve())


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
    resolver = PluginSourceResolver(policy, project_sources=lambda: ([source], []))

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


@pytest.mark.asyncio
async def test_config_write_wait_budget_expires_without_a_later_mutation(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from plugin.server.routes import config as config_routes
    from plugin.server.application.plugins.operation_lock import serialized_plugin_operation

    entered, release = asyncio.Event(), asyncio.Event()
    writes = []

    @serialized_plugin_operation
    async def hold_lock():
        entered.set()
        await release.wait()

    async def write(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(config_routes, "_OPERATION_WAIT_BUDGET_SECONDS", 0.01)
    holding = asyncio.create_task(hold_lock())
    await entered.wait()
    try:
        with pytest.raises(ServerDomainError) as caught:
            await config_routes._dispatch_config(SimpleNamespace(method="PUT"), write, plugin_id="demo")
        assert caught.value.code == "PLUGIN_OPERATION_BUSY"
        assert caught.value.status_code == 409
    finally:
        release.set()
        await holding
    await asyncio.sleep(0)
    assert writes == []


@pytest.mark.asyncio
async def test_invalid_project_refresh_does_not_replace_it_with_an_installed_plugin(isolated, tmp_path):
    source = scaffold(tmp_path / "projects", "same_id")
    record = store.add_record(source)
    meta = project_meta(source, record.project_id)
    state.plugins["same_id"] = meta
    (source / "plugin.toml").write_text("invalid TOML [", encoding="utf-8")
    scaffold(isolated, "same_id")

    with pytest.raises(ServerDomainError) as caught:
        await registry_service.PluginRegistryService().refresh_plugin("same_id")
    assert caught.value.code == "PLUGIN_CONFIG_NOT_FOUND"
    assert state.plugins["same_id"] is meta
    assert store.list_records()[0].project_id == record.project_id


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_sources", [False, True])
async def test_build_all_reports_invalid_projects_and_builds_valid_sources(tmp_path, monkeypatch, valid_sources):
    from plugin.server.application.plugin_cli.service import PluginCliService

    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=tmp_path / "user",
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=tmp_path / "profiles",
    )
    missing = tmp_path / "projects" / "missing"
    broken = scaffold(tmp_path / "projects", "broken")
    (broken / "plugin.toml").write_text("invalid TOML [", encoding="utf-8")
    store.add_record(missing)
    store.add_record(broken)
    if valid_sources:
        scaffold(policy.builtin_plugins_root, "builtin_demo")
        scaffold(policy.user_plugins_root, "user_demo")
        store.add_record(scaffold(tmp_path / "projects", "good"))
    monkeypatch.setattr(PluginCliService, "_path_policy", lambda self: policy)

    result = await PluginCliService().build(mode="all")

    assert result["ok"] is False
    assert result["failed_count"] == 2
    assert {failure["plugin"] for failure in result["failed"]} == {"project/missing", "project/broken"}
    assert all(failure["error"] for failure in result["failed"])
    expected = ["builtin_demo", "user_demo", "good"] if valid_sources else []
    assert [built["plugin_id"] for built in result["built"]] == expected
    assert result["built_count"] == len(expected)
    assert all(Path(built["package_path"]).is_file() for built in result["built"])
    assert len(store.list_records()) == (3 if valid_sources else 2)
    if valid_sources:
        single = await PluginCliService().build(mode="single", plugin_ref={"root_id": "builtin", "directory_name": "builtin_demo"})
        selected = await PluginCliService().build(mode="selected", plugin_refs=[{"root_id": "project", "directory_name": "good"}])
        assert single["ok"] is True and single["failed"] == []
        assert selected["ok"] is True and selected["failed"] == []


@pytest.mark.asyncio
async def test_unreadable_project_store_still_fails_all_builds_but_not_explicit_managed_builds(tmp_path, monkeypatch):
    from plugin.server.application.plugin_cli.service import PluginCliService

    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=tmp_path / "user",
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=tmp_path / "profiles",
    )
    scaffold(policy.builtin_plugins_root, "builtin_demo")
    monkeypatch.setattr(PluginCliService, "_path_policy", lambda self: policy)
    def broken():
        raise ServerDomainError(code="PROJECT_STORE_INVALID", message="broken store", status_code=500)
    monkeypatch.setattr(store, "list_records", broken)
    with pytest.raises(ServerDomainError) as caught:
        await PluginCliService().build(mode="all")
    assert caught.value.code == "PROJECT_STORE_INVALID"
    with pytest.raises(ServerDomainError) as caught:
        await PluginCliService().build(mode="selected", plugin_refs=[{"root_id": "project", "directory_name": "demo"}])
    assert caught.value.code == "PROJECT_STORE_INVALID"
    result = await PluginCliService().build(mode="single", plugin_ref={"root_id": "builtin", "directory_name": "builtin_demo"})
    assert result["ok"] is True
    assert result["built_count"] == 1


@pytest.mark.asyncio
async def test_all_build_failures_identify_projects_with_the_same_directory_name(tmp_path, monkeypatch):
    from plugin.server.application.plugin_cli.service import PluginCliService

    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin", user_plugins_root=tmp_path / "user",
        package_artifacts_root=tmp_path / "packages", package_profiles_root=tmp_path / "profiles",
    )
    records = [store.add_record(tmp_path / parent / "missing") for parent in ["first", "second"]]
    monkeypatch.setattr(PluginCliService, "_path_policy", lambda self: policy)
    result = await PluginCliService().build(mode="all")
    assert result["failed_count"] == 2
    for record, failure in zip(records, result["failed"], strict=True):
        assert failure["plugin"] == "project/missing"
        assert record.project_id in failure["error"]
        assert str(record.source_dir) in failure["error"]
        assert "does not exist or is not accessible" in failure["error"]


@pytest.mark.parametrize("broken", ["missing", "manifest"])
def test_install_plans_keep_failed_project_ids_reserved(tmp_path, monkeypatch, broken):
    from plugin.neko_plugin_cli.core.build import PluginBuilder
    from plugin.server.application.plugin_cli.service import PluginCliService

    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin", user_plugins_root=tmp_path / "user",
        package_artifacts_root=tmp_path / "packages", package_profiles_root=tmp_path / "profiles",
    )
    package_source = scaffold(tmp_path / "package-source", "demo")
    package = policy.package_artifacts_root / "demo.neko-plugin"
    PluginBuilder(source_only_roots=(package_source,)).build_plugin(package_source, package)
    project = tmp_path / "projects" / "demo"
    if broken == "manifest":
        scaffold(project.parent, "demo")
        (project / "plugin.toml").write_text("invalid TOML [", encoding="utf-8")
    store.add_record(project)
    monkeypatch.setattr(PluginCliService, "_path_policy", lambda self: policy)
    monkeypatch.setattr(PluginCliService, "_apply_installed_package_identity", lambda self, plan, **kwargs: plan)

    service = PluginCliService()
    result = service._plan_install_sync(package=str(package), plugins_root=None, profiles_root=None)
    assert result["action"] == "blocked"
    assert result["reason"] == "project_source_conflict"
    assert result["project_sources"] == [str(project)]
    assert not (policy.user_plugins_root / "demo").exists()
    unrelated = store.add_record(tmp_path / "projects" / "unrelated")
    monkeypatch.setattr(store, "list_records", lambda: [unrelated])
    assert service._plan_install_sync(package=str(package), plugins_root=None, profiles_root=None)["action"] == "install"
    def unreadable():
        raise ServerDomainError(code="PROJECT_STORE_INVALID", message="broken store", status_code=500)
    monkeypatch.setattr(store, "list_records", unreadable)
    assert service._plan_install_sync(package=str(package), plugins_root=None, profiles_root=None)["action"] == "install"
