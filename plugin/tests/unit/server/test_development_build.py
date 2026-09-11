from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from pathlib import Path
import zipfile
import threading
import shutil

import pytest

from plugin.neko_plugin_cli.public import inspect_package, install_package
from plugin.server.application.plugin_cli import development_build as build
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.application.plugins import development as dev

pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "selected", "bundle", "all"])
async def test_development_archives_require_local_download_after_detach(workspace, monkeypatch, mode):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes import development, plugin_cli

    monkeypatch.setattr(dev.settings, "USER_PLUGIN_PACKAGES_ROOT", workspace / "packages")
    snapshot = register(workspace)
    ordinary = register(workspace, "ordinary_demo")
    dev.remove_registration_sync(ordinary)
    shutil.copytree(ordinary.source_dir, workspace / "installed" / "ordinary_demo")
    ref = {"registration_id": snapshot.registration_id, "revision": snapshot.revision}
    kwargs = {"development_ref": ref} if mode == "single" else {"development_refs": [ref]} if mode != "all" else {}
    if mode in {"selected", "bundle"}:
        kwargs["plugins"] = ["ordinary_demo"]
    result = await PluginCliService().build(mode=mode, **kwargs)
    assert result["ok"], result
    private = [Path(item["package_path"]) for item in result["built"]
               if Path(item["package_path"]).parent == workspace / "packages-development"]
    assert len(private) == 1
    package = private[0]
    expected_bytes = package.read_bytes()
    assert inspect_package(package).payload_hash_verified
    # Artifacts remain private independently of registration/runtime state.
    dev.remove_registration_sync(snapshot)
    dev.set_enabled_sync(False)
    app = FastAPI()
    app.include_router(development.router)
    app.include_router(plugin_cli.router)
    async with AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.2", 1234)),
                           base_url="http://127.0.0.1") as remote:
        listed = await remote.get("/plugin-cli/packages")
        assert listed.status_code == 200
        assert package.name not in listed.text
        for reference in (str(package), package.name, "../packages-development/" + package.name):
            response = await remote.get("/plugin-cli/download", params={"package": reference})
            assert response.status_code in {400, 404}
        denied = await remote.get("/plugins/development/download", params={"package": str(package)},
                                  headers={"X-Neko-Development": "1"})
        assert denied.status_code == 403
        # Ordinary packages produced in a mixed build keep their public contract.
        for item in listed.json()["packages"]:
            downloaded = await remote.get("/plugin-cli/download", params={"package": item["path"]})
            assert downloaded.status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as local:
        for headers in ({}, {"X-Neko-Development": "1", "Origin": "https://evil.example"},
                        {"X-Neko-Development": "1", "Host": "evil.example"}):
            response = await local.get("/plugins/development/download", params={"package": str(package)}, headers=headers)
            assert response.status_code == 403
        for reference in (str(package), package.name):
            downloaded = await local.get("/plugins/development/download", params={"package": reference},
                                         headers={"X-Neko-Development": "1", "Origin": "http://localhost:48911"})
            assert downloaded.status_code == 200
            assert downloaded.content == expected_bytes
        for reference, status in (("../outside.neko-plugin", 400), ("missing.neko-plugin", 404),
                                  ("output.neko-plugin.pending", 400)):
            response = await local.get("/plugins/development/download", params={"package": reference},
                                       headers={"X-Neko-Development": "1"})
            assert response.status_code == status


@pytest.mark.parametrize("suggestion", ["directory", "file"])
def test_development_output_suggestions_stay_private(workspace, suggestion):
    snapshot = register(workspace)
    result = build.build_development_sources(
        development=[snapshot], ordinary=[], mode="single", target_root=workspace / "packages",
        target_dir=str(workspace / "packages" / "nested") if suggestion == "directory" else None,
        out=str(workspace / "packages" / "nested" / "custom.neko-plugin") if suggestion == "file" else None,
        keep_staging=False, bundle_id=None, package_name=None, package_description=None, version=None,
    )
    assert result["ok"], result
    assert Path(result["built"][0]["package_path"]).parent == workspace / "packages-development" / "nested"
    assert not list((workspace / "packages").rglob("*.neko-plugin"))


@pytest.mark.parametrize("plugin_id", ["my-plugin", "123plugin"])
@pytest.mark.parametrize("prefix", ["plugins", "plugin.plugins"])
def test_package_valid_ids_register_load_build_and_install(workspace, plugin_id, prefix):
    import subprocess
    import sys
    from plugin.server.application.plugins.metadata_scanner import scan_plugin_metadata_isolated

    source = workspace / "source" / plugin_id
    source.mkdir(parents=True)
    manifest = source / "plugin.toml"
    manifest.write_text(
        f'[plugin]\nid="{plugin_id}"\nname="Demo"\nversion="1.0.0"\n'
        f'entry="{prefix}.{plugin_id}:Demo"\n', encoding="utf-8",
    )
    (source / "child.py").write_text('VALUE = "relative import works"\n', encoding="utf-8")
    (source / "__init__.py").write_text(
        'from .child import VALUE\nfrom plugin.sdk.plugin.decorators import plugin_entry\n'
        'class Demo:\n    @plugin_entry(id="hello", name=VALUE)\n    def hello(self): return VALUE\n',
        encoding="utf-8",
    )
    snapshot = dev.register_directory_sync(str(source))
    metadata = scan_plugin_metadata_isolated(
        plugin_id=plugin_id, module_path=f"plugins.{plugin_id}", class_name="Demo",
        config_path=manifest, conf={}, pdata={}, source_only=True,
    )
    assert any(entry.get("name") == "relative import works" for entry in metadata.entries_preview)
    result = run_build(snapshot, workspace)
    assert result["ok"], result
    installed = install_package(result["built"][0]["package_path"],
                                plugins_root=workspace / "clean", profiles_root=workspace / "clean-profiles")
    target = installed.installed_plugins[0]
    assert target.target_plugin_id == plugin_id
    code = """
import sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
module = _import_plugin_module(sys.argv[1], Path(sys.argv[2]), get_logger('id-test'), source_only=sys.argv[3]=='true')
assert module.Demo().hello() == 'relative import works'
"""
    for directory, source_only in [(source, "true"), (target.target_dir, "false")]:
        process = subprocess.run([sys.executable, "-c", code, f"plugins.{plugin_id}",
                                  str(directory / "plugin.toml"), source_only],
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("register_later", [False, True])
async def test_remote_all_without_development_sources_keeps_ordinary_builds(workspace, monkeypatch, register_later):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes import plugin_cli as route

    ordinary = register(workspace, "ordinary_demo")
    dev.remove_registration_sync(ordinary)
    shutil.copytree(ordinary.source_dir, workspace / "installed" / "ordinary_demo")
    original = route.service.build

    async def dispatch(**kwargs):
        assert kwargs["allow_development"] is False
        if register_later:
            register(workspace, "late_development")
        return await original(**kwargs)

    monkeypatch.setattr(route.service, "build", dispatch)
    app = FastAPI()
    app.include_router(route.router)
    async with AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.2", 1234)),
                           base_url="http://127.0.0.1") as client:
        response = await client.post("/plugin-cli/build", json={"mode": "all"})
    assert response.status_code == 200, response.text
    assert [item["plugin_id"] for item in response.json()["built"]] == ["ordinary_demo"]
    if register_later:
        assert dev.registration_for_plugin_sync("late_development") is not None


@pytest.mark.asyncio
async def test_remote_all_with_development_sources_remains_denied(workspace):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes.plugin_cli import router

    register(workspace)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.2", 1234)),
                           base_url="http://127.0.0.1", headers={"X-Neko-Development": "1"}) as client:
        response = await client.post("/plugin-cli/build", json={"mode": "all"})
    assert response.status_code == 403
    assert not list(workspace.glob("packages*/*.neko-plugin"))


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/plugin-cli/build", "/plugin-cli/pack"])
@pytest.mark.parametrize("repair_later", [False, True])
async def test_corrupt_store_all_builds_only_managed_sources(workspace, monkeypatch, endpoint, repair_later):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes import plugin_cli as route

    ordinary = register(workspace, "ordinary_demo")
    dev.remove_registration_sync(ordinary)
    shutil.copytree(ordinary.source_dir, workspace / "installed" / "ordinary_demo")
    builtin = register(workspace, "builtin_demo")
    dev.remove_registration_sync(builtin)
    shutil.copytree(builtin.source_dir, workspace / "builtin" / "builtin_demo")
    external = register(workspace, "external_demo")
    store_path = dev._store_path()
    healthy_store = store_path.read_bytes()
    store_path.write_bytes(b'{')
    original = route.service.build

    async def dispatch(**kwargs):
        assert kwargs["allow_development"] is False
        if repair_later:
            store_path.write_bytes(healthy_store)
        return await original(**kwargs)

    monkeypatch.setattr(route.service, "build", dispatch)
    app = FastAPI()
    app.include_router(route.router)
    async with AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.2", 1234)),
                           base_url="http://127.0.0.1") as client:
        response = await client.post(endpoint, json={"mode": "all"})
    assert response.status_code == 200, response.text
    result = response.json()
    artifacts = result["packed" if endpoint.endswith("/pack") else "built"]
    assert sorted(item["plugin_id"] for item in artifacts) == ["builtin_demo", "ordinary_demo"]
    assert all(inspect_package(item["package_path"]).payload_hash_verified is True for item in artifacts)
    assert result["ok"] is False and result["failed_count"] == 1
    assert result["failed"][0]["plugin"] == "development"
    assert str(external.source_dir) not in response.text
    assert external.registration_id not in response.text
    assert store_path.read_bytes() == (healthy_store if repair_later else b'{')
    assert len(list((workspace / "packages").glob("*.neko-plugin"))) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "selected", "bundle"])
async def test_corrupt_store_explicit_development_build_still_fails(workspace, mode):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes.plugin_cli import router

    snapshot = register(workspace)
    dev._store_path().write_bytes(b'{')
    ref = {"registration_id": snapshot.registration_id, "revision": snapshot.revision}
    payload = {"mode": mode, **({"development_ref": ref} if mode == "single" else {"development_refs": [ref]})}
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1",
                           headers={"X-Neko-Development": "1"}) as client:
        response = await client.post("/plugin-cli/build", json=payload)
    assert response.status_code == 500
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_STORE_INVALID"
    assert dev._store_path().read_bytes() == b'{'
    assert not list(workspace.glob("packages*/*.neko-plugin"))


@pytest.mark.asyncio
async def test_all_probe_does_not_swallow_other_domain_errors(workspace, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import AsyncMock
    from plugin.server.domain.errors import ServerDomainError
    from plugin.server.routes import plugin_cli as route

    def conflicting_sources(*args):
        raise ServerDomainError(code="DEVELOPMENT_CONFLICT", message="Source conflict", status_code=409)

    monkeypatch.setattr(build, "resolve_development_sources", conflicting_sources)
    dispatch = AsyncMock()
    monkeypatch.setattr(route.service, "build", dispatch)
    app = FastAPI()
    app.include_router(route.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.post("/plugin-cli/build", json={"mode": "all"})
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "DEVELOPMENT_CONFLICT"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_honors_configured_operation_wait_budget(workspace, monkeypatch):
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy, plugin_operation_lock

    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", "1")
    published = []
    async with plugin_operation_lock.hold():
        with pytest.raises(PluginOperationBusy):
            await asyncio.wait_for(build._publish_with_operation_lock(lambda: published.append(True)), 3)
    assert published == []


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "_store_path", lambda: tmp_path / "state" / "development.json")
    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin", user_plugins_root=tmp_path / "installed",
        package_artifacts_root=tmp_path / "packages", package_profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setattr(PluginCliService, "_path_policy", lambda self: policy)
    dev.set_enabled_sync(True)
    return tmp_path


def register(root, name="dev_demo"):
    source = root / "中文 source" / name
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text(
        f'[plugin]\nid = "{name}"\nname = "Demo"\nversion = "1.0.0"\n'
        f'entry = "plugins.{name}:Demo"\n', encoding="utf-8",
    )
    (source / "__init__.py").write_text('class Demo:\n    value = "old"\n', encoding="utf-8")
    return dev.register_directory_sync(str(source))


def run_build(snapshot, root, **kwargs):
    return build.build_development_sources(
        development=[snapshot], ordinary=[], mode="single", target_root=root / "packages",
        target_dir=None, out=None, keep_staging=False, bundle_id=None,
        package_name=None, package_description=None, version=None, **kwargs,
    )


def test_stopped_registered_source_builds_and_installs(workspace):
    snapshot = register(workspace)
    (snapshot.source_dir / "__pycache__").mkdir()
    (snapshot.source_dir / "__pycache__" / "old.pyc").write_bytes(b"stale")
    result = run_build(snapshot, workspace)
    assert result["ok"], result
    package = Path(result["built"][0]["package_path"])
    assert inspect_package(package).payload_hash_verified is True
    installed = install_package(package, plugins_root=workspace / "clean-install", profiles_root=workspace / "clean-profiles")
    assert installed.installed_plugins[0].target_plugin_id == "dev_demo"
    with zipfile.ZipFile(package) as archive:
        assert not any("__pycache__" in name for name in archive.namelist())
    assert (snapshot.source_dir / "__pycache__" / "old.pyc").read_bytes() == b"stale"
    assert not (snapshot.source_dir / "plugin.meta.json").exists()


def test_concurrent_builds_publish_distinct_complete_archives(workspace):
    snapshot = register(workspace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_build(snapshot, workspace), range(2)))
    assert all(result["ok"] for result in results), results
    paths = [result["built"][0]["package_path"] for result in results]
    assert len(set(paths)) == 2
    assert all(inspect_package(path).payload_hash_verified for path in paths)
    assert not list(workspace.glob("packages*/*.pending"))


def test_edit_during_build_refuses_publication(workspace, monkeypatch):
    snapshot = register(workspace)
    original = build.build_plugin

    def editing_build(*args, **kwargs):
        result = original(*args, **kwargs)
        (snapshot.source_dir / "__init__.py").write_text('class Demo:\n    value = "new"\n')
        return result

    monkeypatch.setattr(build, "build_plugin", editing_build)
    result = run_build(snapshot, workspace)
    assert not result["ok"]
    assert "Source changed" in result["failed"][0]["error"]
    assert not list(workspace.glob("packages*/*.neko-plugin"))


def test_detach_during_build_refuses_publication(workspace, monkeypatch):
    snapshot = register(workspace)
    original = build.build_plugin

    def detaching_build(*args, **kwargs):
        result = original(*args, **kwargs)
        dev.remove_registration_sync(snapshot)
        return result

    monkeypatch.setattr(build, "build_plugin", detaching_build)
    result = run_build(snapshot, workspace)
    assert not result["ok"]
    assert not list(workspace.glob("packages*/*.neko-plugin"))
    assert snapshot.source_dir.is_dir()


def test_source_output_overlap_is_rejected(workspace):
    snapshot = register(workspace)
    with pytest.raises(ValueError, match="source directory"):
        build.build_development_sources(
            development=[snapshot], ordinary=[], mode="single", target_root=snapshot.source_dir,
            target_dir=None, out=None, keep_staging=False, bundle_id=None,
            package_name=None, package_description=None, version=None,
        )


@pytest.mark.asyncio
async def test_service_selected_deduplicates_and_all_includes_development(workspace):
    snapshot = register(workspace)
    ref = {"registration_id": snapshot.registration_id, "revision": snapshot.revision}
    service = PluginCliService()
    selected = await service.build(mode="selected", development_refs=[ref, ref])
    assert selected["built_count"] == 1, selected
    all_sources = await service.build(mode="all")
    assert all_sources["built_count"] == 1, all_sources


@pytest.mark.asyncio
async def test_service_rejects_unregistered_raw_path(workspace):
    snapshot = register(workspace)
    from plugin.server.domain.errors import ServerDomainError

    with pytest.raises(ServerDomainError):
        await PluginCliService().build(mode="single", plugin=str(snapshot.source_dir))


@pytest.mark.asyncio
async def test_bundle_combines_registered_sources_and_installs(workspace):
    first, second = register(workspace), register(workspace, "second_demo")
    refs = [{"registration_id": item.registration_id, "revision": item.revision} for item in (first, second)]
    result = await PluginCliService().build(mode="bundle", development_refs=refs, bundle_id="demo-bundle")
    assert result["ok"], result
    installed = install_package(result["built"][0]["package_path"], plugins_root=workspace / "clean", profiles_root=workspace / "clean-profiles")
    assert {item.target_plugin_id for item in installed.installed_plugins} == {"dev_demo", "second_demo"}


def test_missing_vendor_dependency_fails_without_package(workspace):
    snapshot = register(workspace)
    (snapshot.source_dir / "pyproject.toml").write_text('[project]\ndependencies = ["missing-neko-test-dependency>=1"]\n')
    result = run_build(snapshot, workspace)
    assert not result["ok"]
    assert "vendor" in result["failed"][0]["error"]
    assert not list(workspace.glob("packages*/*.neko-plugin"))


@pytest.mark.asyncio
async def test_cancelled_build_does_not_publish_later(workspace, monkeypatch):
    snapshot = register(workspace)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = build.build_plugin

    def slow_build(*args, **kwargs):
        result = original(*args, **kwargs)
        started.set()
        assert release.wait(5)
        return result

    original_sync = PluginCliService._build_sync

    def track_completion(*args, **kwargs):
        try:
            return original_sync(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(build, "build_plugin", slow_build)
    monkeypatch.setattr(PluginCliService, "_build_sync", track_completion)
    task = asyncio.create_task(PluginCliService().build(
        mode="single", development_ref={"registration_id": snapshot.registration_id, "revision": snapshot.revision},
    ))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await asyncio.to_thread(finished.wait, 5)
    assert not list(workspace.glob("packages*/*.neko-plugin"))
    assert not list(workspace.glob("packages*/*.pending"))


@pytest.mark.asyncio
async def test_unapproved_all_cannot_gain_new_development_sources(workspace):
    register(workspace)
    from plugin.server.domain.errors import ServerDomainError

    # The route captures allow_development before dispatching the worker. A
    # mode switch between that check and the worker cannot grant path access.
    with pytest.raises(ServerDomainError, match="No plugin.toml"):
        await PluginCliService().build(mode="all", allow_development=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,peer,expected", [
    ({}, "127.0.0.1", 403),
    ({"X-Neko-Development": "1", "Origin": "https://example.org"}, "127.0.0.1", 403),
    ({"X-Neko-Development": "1"}, "192.168.1.2", 403),
    ({"X-Neko-Development": "1", "Origin": "http://localhost:48911"}, "127.0.0.1", 200),
])
async def test_build_route_guards_development_sources(workspace, headers, peer, expected):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from plugin.server.routes.plugin_cli import router

    snapshot = register(workspace)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app, client=(peer, 1234)), base_url="http://127.0.0.1") as client:
        response = await client.post("/plugin-cli/build", headers=headers, json={
            "mode": "single", "development_ref": {
                "registration_id": snapshot.registration_id, "revision": snapshot.revision,
            },
        })
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["built_count"] == 1
    else:
        assert not list(workspace.glob("packages*/*.neko-plugin"))


@pytest.mark.asyncio
async def test_all_prefers_user_override_and_includes_development(workspace):
    register(workspace)
    ordinary = register(workspace, "ordinary_demo")
    dev.remove_registration_sync(ordinary)
    for root in (workspace / "builtin", workspace / "installed"):
        shutil.copytree(ordinary.source_dir, root / "ordinary_demo")
    result = await PluginCliService().build(mode="all")
    assert result["ok"], result
    assert result["built_count"] == 2
    assert {item["plugin_id"] for item in result["built"]} == {"dev_demo", "ordinary_demo"}


@pytest.mark.asyncio
async def test_all_reports_missing_registration_and_builds_healthy_sources(workspace):
    missing = register(workspace, "missing_demo")
    register(workspace)
    ordinary = register(workspace, "ordinary_demo")
    dev.remove_registration_sync(ordinary)
    shutil.copytree(ordinary.source_dir, workspace / "installed" / "ordinary_demo")
    missing.source_dir.rename(missing.source_dir.with_name("moved_demo"))
    result = await PluginCliService().build(mode="all")
    assert not result["ok"], result
    assert result["failed_count"] == 1
    assert result["failed"][0]["plugin"] == "missing_demo"
    assert {item["plugin_id"] for item in result["built"]} == {"dev_demo", "ordinary_demo"}
    assert all(inspect_package(item["package_path"]).payload_hash_verified for item in result["built"])
    assert dev.require_registration_sync(missing.registration_id, missing.revision) == missing


@pytest.mark.asyncio
async def test_all_with_only_invalid_registration_returns_individual_failure(workspace):
    missing = register(workspace)
    (missing.source_dir / "plugin.toml").write_text("[invalid", encoding="utf-8")
    result = await PluginCliService().build(mode="all")
    assert result["built_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed"][0]["plugin"] == missing.plugin_id


@pytest.mark.asyncio
async def test_all_isolates_new_install_conflict_from_other_builds(workspace, monkeypatch):
    conflict = register(workspace, "conflict_demo")
    register(workspace)
    shutil.copytree(conflict.source_dir, workspace / "installed" / "conflict_demo")
    monkeypatch.setattr(dev.settings, "PLUGIN_CONFIG_ROOTS", (workspace / "builtin", workspace / "installed"))
    result = await PluginCliService().build(mode="all")
    assert result["failed_count"] == 1, result
    assert result["failed"][0]["plugin"] == "conflict_demo"
    assert "already exists" in result["failed"][0]["error"]
    assert {item["plugin_id"] for item in result["built"]} == {"dev_demo", "conflict_demo"}


def test_corrected_package_name_can_be_rebound_built_and_installed(workspace):
    from plugin.core.entry_points import describe_plugin_entry_directory_mismatch, normalize_plugin_entry_point
    from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source

    original = register(workspace, "release_demo")
    dev.remove_registration_sync(original)
    moved = original.source_dir.with_name("source_pkg")
    original.source_dir.rename(moved)
    manifest = moved / "plugin.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("plugins.release_demo", "plugins.source_pkg"), encoding="utf-8")
    snapshot = dev.register_directory_sync(str(moved))
    assert not run_build(snapshot, workspace)["ok"]

    moved.rename(original.source_dir)
    manifest = original.source_dir / "plugin.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("plugins.source_pkg", "plugins.release_demo"), encoding="utf-8")
    updated = dev.rebind_registration_sync(snapshot, str(original.source_dir))
    result = run_build(updated, workspace)
    assert result["ok"], result
    installed = install_package(result["built"][0]["package_path"], plugins_root=workspace / "clean", profiles_root=workspace / "clean-profiles")
    source = load_plugin_source(installed.installed_plugins[0].target_dir)
    entry = normalize_plugin_entry_point(source.plugin_toml["plugin"]["entry"], config_path=source.plugin_toml_path, builtin_plugin_root=workspace / "builtin")
    assert not describe_plugin_entry_directory_mismatch(entry, config_path=source.plugin_toml_path)
    assert source.plugin_id == "release_demo"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "all", "bundle"])
async def test_directory_id_mismatch_cannot_publish_broken_package(workspace, mode):
    snapshot = register(workspace, "source_pkg")
    dev.remove_registration_sync(snapshot)
    manifest = snapshot.source_dir / "plugin.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('id = "source_pkg"', 'id = "release_demo"'), encoding="utf-8")
    snapshot = dev.register_directory_sync(str(snapshot.source_dir))
    before = {path.name: path.read_bytes() for path in snapshot.source_dir.iterdir()}
    ref = {"registration_id": snapshot.registration_id, "revision": snapshot.revision}
    kwargs = {"development_ref": ref} if mode == "single" else {"development_refs": [ref]} if mode == "bundle" else {}
    result = await PluginCliService().build(mode=mode, **kwargs)
    assert not result["ok"], result
    assert result["built_count"] == 0
    assert "release_demo" in result["failed"][0]["error"]
    assert "source_pkg" in result["failed"][0]["error"]
    assert "rebind" in result["failed"][0]["error"]
    assert not list(workspace.glob("packages*/*.neko-*"))
    assert {path.name: path.read_bytes() for path in snapshot.source_dir.iterdir()} == before
