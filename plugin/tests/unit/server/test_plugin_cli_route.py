from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import errno
import io
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import threading

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.neko_plugin_cli.public import pack_plugin
from plugin.neko_plugin_cli.core.archive_utils import PackageValidationError
from plugin.neko_plugin_cli.core.install import PackageInstaller
from plugin.core.state import state as plugin_state
from plugin.server.application.plugin_cli.service import (
    PluginCliService,
    _merge_staged_payload,
    _merge_staged_profile_preserving_existing,
)
from plugin.server.application.plugin_cli import service as plugin_cli_service_module
from plugin.server.application.plugins.inventory_store import (
    PluginInventoryError,
    get_inventory_resolution,
    get_deleted_plugin_ids,
    mark_plugin_deleted,
    record_user_installation,
    remove_user_installation,
)
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.application.install_source import (
    InstallSourceManager,
    PluginDirectoryScanner,
    get_install_source_manager,
    set_global_manager,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.plugin_cli import router
from plugin.server.routes import plugin_cli as plugin_cli_routes

pytestmark = pytest.mark.plugin_unit
FIXTURE_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "neko_plugin_cli" / "plugins"


def test_package_validation_domain_error_uses_safe_stable_contract() -> None:
    error = PluginCliService()._domain_error_from_exception(
        PackageValidationError(
            "PLUGIN_PACKAGE_PLUGIN_MANIFEST_INVALID",
            "'plugin.toml' in 'C:\\Users\\name\\private.neko-plugin' contains invalid TOML",
        ),
        action="inspect",
    )

    assert error.code == "PLUGIN_PACKAGE_PLUGIN_MANIFEST_INVALID"
    assert error.status_code == 400
    assert error.message == "A packaged plugin.toml is invalid."
    assert "C:\\Users" not in error.message


def _make_plugin_dir(
    tmp_path: Path,
    plugin_id: str = "route_demo",
    *,
    version: str = "0.0.1",
    entry: str | None = None,
) -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    resolved_entry = entry or f"{plugin_id}:Plugin"
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Route Demo"',
                f'version = "{version}"',
                'type = "plugin"',
                f'entry = "{resolved_entry}"',
                "",
                f"[{plugin_id}]",
                'value = "demo"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "class Plugin:\n    pass\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_staged_payload_rejects_junction_before_writing_outside_root(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "staging"
    staged_data = source_dir / "data"
    staged_data.mkdir(parents=True)
    staged_file = staged_data / "package.bin"
    staged_file.write_bytes(b"package")
    target_dir = tmp_path / "plugin"
    target_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = target_dir / "data"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    with pytest.raises(FileExistsError, match="linked path"):
        _merge_staged_payload(source_dir, target_dir)

    assert staged_file.read_bytes() == b"package"
    assert not (outside / "package.bin").exists()


def _copy_fixture_plugin(tmp_path: Path, fixture_name: str) -> Path:
    source = FIXTURE_PLUGINS_ROOT / fixture_name
    target = tmp_path / fixture_name
    shutil.copytree(source, target)
    if fixture_name == "bundle_alpha":
        _write_vendor_dist(target, "shared-lib", "2.0.0")
        _write_vendor_dist(target, "alpha-only", "0.1.0")
    elif fixture_name == "bundle_beta":
        _write_vendor_dist(target, "shared-lib", "2.0.0")
        _write_vendor_dist(target, "beta-only", "0.5.0")
    return target


def test_merge_staged_payload_recurses_into_preserved_state_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging" / "demo"
    target = tmp_path / "plugins" / "demo"
    (source / "data" / "defaults").mkdir(parents=True)
    (source / "data" / "defaults" / "labels.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (source / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (target / "data").mkdir(parents=True)
    (target / "data" / "user.db").write_text("user-state\n", encoding="utf-8")

    moved = _merge_staged_payload(source, target)

    assert (target / "data" / "user.db").read_text(encoding="utf-8") == "user-state\n"
    assert (target / "data" / "defaults" / "labels.json").read_text(
        encoding="utf-8"
    ) == "{}\n"
    assert (target / "plugin.toml").is_file()
    assert set(moved) == {
        target / "data" / "defaults",
        target / "plugin.toml",
    }


def test_merge_staged_payload_restores_all_moves_on_state_file_collision(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging" / "demo"
    target = tmp_path / "plugins" / "demo"
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "shared.json").write_text("package\n", encoding="utf-8")
    (target / "data").mkdir(parents=True)
    (target / "data" / "shared.json").write_text("user-state\n", encoding="utf-8")

    with pytest.raises(
        FileExistsError,
        match=r"preserved state path: data/shared\.json",
    ):
        _merge_staged_payload(source, target)

    assert not (target / "plugin.toml").exists()
    assert (target / "data" / "shared.json").read_text(encoding="utf-8") == "user-state\n"
    assert (source / "plugin.toml").is_file()
    assert (source / "data" / "shared.json").read_text(encoding="utf-8") == "package\n"


def test_merge_staged_payload_restores_moves_when_destination_is_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "staging" / "demo"
    target = tmp_path / "plugins" / "demo"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "first.txt").write_text("first\n", encoding="utf-8")
    (source / "locked.txt").write_text("locked\n", encoding="utf-8")
    original_iterdir = Path.iterdir
    original_rename = Path.rename

    def ordered_iterdir(path: Path):
        children = list(original_iterdir(path))
        return iter(sorted(children, key=lambda child: child.name))

    def fail_locked_rename(path: Path, target_path: Path):
        if path.name == "locked.txt":
            raise PermissionError("simulated antivirus file lock")
        return original_rename(path, target_path)

    monkeypatch.setattr(Path, "iterdir", ordered_iterdir)
    monkeypatch.setattr(Path, "rename", fail_locked_rename)

    with pytest.raises(PermissionError, match="antivirus file lock"):
        _merge_staged_payload(source, target)

    assert not (target / "first.txt").exists()
    assert not (target / "locked.txt").exists()
    assert (source / "first.txt").read_text(encoding="utf-8") == "first\n"
    assert (source / "locked.txt").read_text(encoding="utf-8") == "locked\n"


def test_merge_staged_profile_preserves_existing_and_adds_missing_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staging" / "profile"
    target = tmp_path / "profiles" / "demo"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "default.toml").write_bytes(b"package-default\n")
    (source / "new.toml").write_bytes(b"package-new\n")
    (target / "default.toml").write_bytes(b"user-default\x00\xff\n")

    created = _merge_staged_profile_preserving_existing(source, target)

    assert (target / "default.toml").read_bytes() == b"user-default\x00\xff\n"
    assert (target / "new.toml").read_bytes() == b"package-new\n"
    assert created == (target / "new.toml",)


def test_staged_install_cleans_partial_extract_when_disk_fills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_plugin_dir(tmp_path / "source", plugin_id="disk_full_demo")
    package = tmp_path / "disk_full_demo.neko-plugin"
    pack_plugin(source, package)
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    original_extract_member = PackageInstaller.extract_member
    extracted_files = 0

    def fail_after_first_file(
        installer: PackageInstaller,
        archive,
        member_name: str,
        target_path: Path,
    ) -> None:
        nonlocal extracted_files
        if not archive.getinfo(member_name).is_dir():
            extracted_files += 1
            if extracted_files == 2:
                raise OSError(errno.ENOSPC, "simulated disk full")
        original_extract_member(installer, archive, member_name, target_path)

    monkeypatch.setattr(PackageInstaller, "extract_member", fail_after_first_file)

    with pytest.raises(OSError) as exc_info:
        PluginCliService()._install_via_staging_sync(
            package=package,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict="fail",
        )

    assert exc_info.value.errno == errno.ENOSPC
    assert not (plugins_root / "disk_full_demo").exists()
    assert not list(plugins_root.glob(".neko_staging_*"))
    assert not list(profiles_root.glob(".neko_staging_*"))


def test_concurrent_state_only_installs_leave_one_complete_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    source = _make_plugin_dir(tmp_path / "source", plugin_id="concurrent_demo")
    package = tmp_path / "concurrent_demo.neko-plugin"
    pack_plugin(source, package)
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    target = plugins_root / "concurrent_demo"
    for state_name in ("config", "data", "cache"):
        (target / state_name).mkdir(parents=True, exist_ok=True)
    sentinel = target / "data" / "sentinel.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    barrier = threading.Barrier(2)

    def install_once() -> object:
        barrier.wait(timeout=5)
        try:
            return PluginCliService()._install_via_staging_sync(
                package=package,
                plugins_root=plugins_root,
                profiles_root=profiles_root,
                on_conflict="fail",
            )
        except Exception as exc:  # returned for deterministic result inspection
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: install_once(), range(2)))

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1, repr(results)
    assert len(failures) == 1, repr(results)
    assert isinstance(failures[0], FileExistsError)
    assert (target / "plugin.toml").is_file()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert not list(plugins_root.glob(".neko_staging_*"))
    assert not list(profiles_root.glob(".neko_staging_*"))


def _write_vendor_dist(plugin_dir: Path, name: str, version: str) -> None:
    dist_dir = plugin_dir / "vendor" / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )


def _patch_plugin_cli_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    builtin_root: Path,
    user_root: Path | None = None,
    managed_root: Path | None = None,
    packages_root: Path | None = None,
    profiles_root: Path | None = None,
) -> None:
    import plugin.settings as plugin_settings
    from plugin.core import registry as core_registry_module
    from plugin.server.application.plugins import registry_service as registry_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", user_root or builtin_root)
    resolved_managed_root = managed_root or user_root or builtin_root
    monkeypatch.setattr(
        plugin_settings,
        "MANAGED_PLUGIN_INSTALLATIONS_ROOT",
        resolved_managed_root,
    )
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root or builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root or (builtin_root / "profiles"))
    monkeypatch.setattr(
        registry_module,
        "PLUGIN_CONFIG_ROOTS",
        tuple(
            dict.fromkeys(
                (builtin_root, resolved_managed_root, user_root or builtin_root)
            )
        ),
    )
    monkeypatch.setattr(
        core_registry_module,
        "BUILTIN_PLUGIN_CONFIG_ROOT",
        builtin_root,
    )


class _MemoryUploadFile:
    def __init__(self) -> None:
        self.filename = "demo.neko-plugin"
        self.file = io.BytesIO(b"demo")

    async def read(self) -> bytes:
        raise AssertionError("upload routes must not copy the whole file into memory")


@pytest.fixture
def plugin_cli_test_app() -> FastAPI:
    app = FastAPI(title="plugin-cli-test-app")
    register_exception_handlers(app)
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _isolate_plugin_registry_state() -> None:
    plugins_backup = copy.deepcopy(plugin_state.plugins)
    cache_backup = copy.deepcopy(plugin_state._snapshot_cache)
    with plugin_state.acquire_plugins_write_lock():
        plugin_state.plugins.clear()
    try:
        yield
    finally:
        with plugin_state.acquire_plugins_write_lock():
            plugin_state.plugins.clear()
            plugin_state.plugins.update(plugins_backup)
        with plugin_state._snapshot_cache_lock:
            plugin_state._snapshot_cache = cache_backup


def test_upload_and_unpack_legacy_returns_unpack_key_without_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_upload_and_install(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "upload": {"filename": "demo.neko-plugin"},
            "install": {
                "installed_plugins": ["demo"],
                "installed_plugin_count": 1,
            },
        }

    monkeypatch.setattr(
        plugin_cli_routes.service,
        "upload_and_install",
        fake_upload_and_install,
    )

    import asyncio

    body = asyncio.run(
        plugin_cli_routes.plugin_cli_upload_and_unpack_legacy(
            _MemoryUploadFile(),  # type: ignore[arg-type]
            on_conflict="fail",
            _="",
        )
    )

    assert "install" not in body
    assert body["upload"] == {"filename": "demo.neko-plugin"}
    assert body["unpack"] == {
        "unpacked_plugins": ["demo"],
        "unpacked_plugin_count": 1,
    }
    assert captured["activate_installation"] is False
    assert captured["record_install_source"] is False
    assert captured["source_file"] is not None
    assert "content" not in captured


@pytest.mark.asyncio
async def test_upload_and_install_streams_and_activates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_upload_and_install(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "upload": {"filename": "demo.neko-plugin"},
            "install": {
                "installed_plugins": [],
                "installed_plugin_count": 0,
            },
        }

    monkeypatch.setattr(
        plugin_cli_routes.service,
        "upload_and_install",
        fake_upload_and_install,
    )

    await plugin_cli_routes.plugin_cli_upload_and_install(
        _MemoryUploadFile(),  # type: ignore[arg-type]
        on_conflict="fail",
        _="",
    )

    assert captured["activate_installation"] is True
    assert captured["source_file"] is not None
    assert "content" not in captured


class _BoundedReadStream(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert size > 0, "streaming save must always use a bounded read"
        self.read_sizes.append(size)
        return super().read(size)


def test_streamed_upload_enforces_limit_and_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages_root = tmp_path / "packages"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        packages_root=packages_root,
    )
    monkeypatch.setattr(plugin_cli_service_module, "_UPLOAD_MAX_BYTES", 5)
    stream = _BoundedReadStream(b"123456")

    with pytest.raises(ServerDomainError):
        PluginCliService()._save_uploaded_file_sync(
            filename="too-large.neko-plugin",
            source_file=stream,
        )

    assert stream.read_sizes
    assert all(0 < size <= 1024 * 1024 for size in stream.read_sizes)
    assert not list(packages_root.glob("*"))


@pytest.mark.asyncio
async def test_streamed_developer_unpack_does_not_record_source_or_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    managed_root = tmp_path / "plugin-installations"
    user_root = tmp_path / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    inventory_path = tmp_path / "plugin-installations.json"
    package_source = tmp_path / "developer.neko-plugin"
    pack_plugin(
        _make_plugin_dir(source_root, plugin_id="developer_unpack"),
        package_source,
    )
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        managed_root=managed_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    previous_manager = get_install_source_manager()
    set_global_manager(None)
    try:
        result = await PluginCliService().upload_and_install(
            filename=package_source.name,
            source_file=io.BytesIO(package_source.read_bytes()),
            activate_installation=False,
            record_install_source=False,
        )
    finally:
        set_global_manager(previous_manager)

    assert result["install"]["installed_plugin_count"] == 1
    assert (managed_root / "developer_unpack" / "plugin.toml").is_file()
    assert not inventory_path.exists()


@pytest.mark.asyncio
async def test_plugin_cli_inspect_and_verify_routes(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "route_demo.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _patch_plugin_cli_settings(monkeypatch, builtin_root=tmp_path, packages_root=tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        inspect_response = await client.post(
            "/plugin-cli/inspect",
            json={"package": str(package_path)},
        )
        assert inspect_response.status_code == 200
        inspect_body = inspect_response.json()
        assert inspect_body["package_id"] == "route_demo"
        assert inspect_body["payload_hash_verified"] is True

        verify_response = await client.post(
            "/plugin-cli/verify",
            json={"package": str(package_path)},
        )
        assert verify_response.status_code == 200
        verify_body = verify_response.json()
        assert verify_body["ok"] is True


@pytest.mark.asyncio
async def test_plugin_cli_list_plugins_route_returns_shape(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_plugin_dir(tmp_path, plugin_id="route_list_demo")
    _patch_plugin_cli_settings(monkeypatch, builtin_root=tmp_path, packages_root=tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugin-cli/plugins")

        assert response.status_code == 200
        body = response.json()
        assert "plugins" in body
        assert "count" in body
        assert isinstance(body["plugins"], list)
        assert body["plugins"] == ["route_list_demo"]
        assert body["plugin_refs"] == [
            {
                "root_id": "builtin",
                "directory_name": "route_list_demo",
                "plugin_id": "route_list_demo",
                "label": "builtin/route_list_demo",
            }
        ]


@pytest.mark.asyncio
async def test_plugin_cli_build_single_legacy_string_resolves_user_root_when_builtin_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steam_builtin_root = tmp_path / "steam" / "steamapps" / "common" / "NEKO" / "resources" / "plugin" / "plugins"
    user_root = tmp_path / "documents" / "Neko" / "plugins"
    packages_root = tmp_path / "documents" / "Neko" / "packages"
    steam_builtin_root.mkdir(parents=True)
    user_root.mkdir(parents=True)
    packages_root.mkdir(parents=True)
    _make_plugin_dir(user_root, plugin_id="neko_minecraft")
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=steam_builtin_root,
        user_root=user_root,
        packages_root=packages_root,
    )

    body = await PluginCliService().build(mode="single", plugin="neko_minecraft")

    assert body["ok"] is True
    assert body["built_count"] == 1
    built = body["built"][0]
    assert built["plugin_id"] == "neko_minecraft"
    assert Path(built["package_path"]).is_relative_to(packages_root.resolve())


@pytest.mark.asyncio
async def test_plugin_cli_build_all_includes_builtin_and_user_in_stable_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    _make_plugin_dir(builtin_root, plugin_id="builtin_z")
    _make_plugin_dir(builtin_root, plugin_id="builtin_a")
    _make_plugin_dir(user_root, plugin_id="user_a")
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
    )

    body = await PluginCliService().build(mode="all")

    assert body["ok"] is True
    assert [item["plugin_id"] for item in body["built"]] == [
        "builtin_a",
        "builtin_z",
        "user_a",
    ]


@pytest.mark.asyncio
async def test_plugin_cli_build_single_plugin_ref_routes_to_exact_user_plugin(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    _make_plugin_dir(builtin_root, plugin_id="shared")
    shared_user = user_root / "shared"
    shared_user.mkdir(parents=True, exist_ok=True)
    (shared_user / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "shared_user"',
                'name = "Shared User"',
                'version = "0.0.1"',
                'type = "plugin"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/build",
            json={
                "mode": "single",
                "plugin_ref": {"root_id": "user", "directory_name": "shared"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["built"][0]["plugin_id"] == "shared_user"


@pytest.mark.asyncio
async def test_plugin_cli_build_rejects_target_dir_outside_package_artifacts_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin_root = tmp_path / "builtin"
    packages_root = tmp_path / "packages"
    outside_root = tmp_path / "outside"
    _make_plugin_dir(builtin_root, plugin_id="route_outside_demo")
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        packages_root=packages_root,
    )

    with pytest.raises(ServerDomainError) as info:
        await PluginCliService().build(
            mode="single",
            plugin="route_outside_demo",
            target_dir=str(outside_root),
        )

    assert info.value.status_code == 400
    assert not list(outside_root.glob("*.neko-plugin"))
    assert not list(packages_root.glob("*.neko-plugin"))


@pytest.mark.asyncio
async def test_plugin_cli_list_packages_route_returns_target_packages(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path, plugin_id="route_pkg_demo")
    package_path = tmp_path / "route_pkg_demo.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    _patch_plugin_cli_settings(monkeypatch, builtin_root=tmp_path, packages_root=tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugin-cli/packages")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["target_dir"] == str(tmp_path)
        assert body["packages"][0]["name"] == "route_pkg_demo.neko-plugin"


@pytest.mark.asyncio
async def test_plugin_cli_pack_bundle_route_uses_mode_payload(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_plugin_dir(tmp_path, plugin_id="route_bundle_one")
    _make_plugin_dir(tmp_path, plugin_id="route_bundle_two")
    target_dir = tmp_path / "target"
    _patch_plugin_cli_settings(monkeypatch, builtin_root=tmp_path, packages_root=tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/pack",
            json={
                "mode": "bundle",
                "plugins": ["route_bundle_one", "route_bundle_two"],
                "bundle_id": "route_bundle_demo",
                "target_dir": str(target_dir),
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["packed_count"] == 1
        assert body["packed"][0]["package_type"] == "bundle"
        assert body["packed"][0]["plugin_ids"] == ["route_bundle_one", "route_bundle_two"]


@pytest.mark.asyncio
async def test_plugin_cli_route_workflow_pack_analyze_inspect_verify_and_unpack(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha_dir = _copy_fixture_plugin(tmp_path, "bundle_alpha")
    beta_dir = _copy_fixture_plugin(tmp_path, "bundle_beta")
    target_dir = tmp_path / "target"
    plugins_root = tmp_path / "runtime_plugins"
    profiles_root = tmp_path / "runtime_profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path,
        user_root=tmp_path,
        packages_root=tmp_path,
        profiles_root=profiles_root,
    )
    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        analyze_response = await client.post(
            "/plugin-cli/analyze",
            json={
                "plugins": [alpha_dir.name, beta_dir.name],
                "current_sdk_version": "2.3.0",
            },
        )
        assert analyze_response.status_code == 200
        analyze_body = analyze_response.json()
        assert analyze_body["plugin_ids"] == ["bundle_alpha", "bundle_beta"]
        assert analyze_body["sdk_supported_analysis"]["current_sdk_supported_by_all"] is True
        assert analyze_body["common_dependencies"][0]["name"] == "shared-lib"

        pack_response = await client.post(
            "/plugin-cli/pack",
            json={
                "mode": "bundle",
                "plugins": [alpha_dir.name, beta_dir.name],
                "bundle_id": "route_workflow_bundle",
                "package_name": "Route Workflow Bundle",
                "package_description": "Route workflow integration bundle.",
                "version": "1.0.0",
                "target_dir": str(target_dir),
            },
        )
        assert pack_response.status_code == 200
        pack_body = pack_response.json()
        assert pack_body["ok"] is True
        assert pack_body["packed_count"] == 1

        package_path = target_dir / "route_workflow_bundle.neko-bundle"
        assert package_path.is_file()

        inspect_response = await client.post(
            "/plugin-cli/inspect",
            json={"package": str(package_path)},
        )
        assert inspect_response.status_code == 200
        inspect_body = inspect_response.json()
        assert inspect_body["package_type"] == "bundle"
        assert inspect_body["package_name"] == "Route Workflow Bundle"
        assert inspect_body["plugin_count"] == 2
        assert inspect_body["payload_hash_verified"] is True

        verify_response = await client.post(
            "/plugin-cli/verify",
            json={"package": str(package_path)},
        )
        assert verify_response.status_code == 200
        verify_body = verify_response.json()
        assert verify_body["ok"] is True
        assert verify_body["payload_hash_verified"] is True

        unpack_response = await client.post(
            "/plugin-cli/unpack",
            json={
                "package": str(package_path),
                "plugins_root": str(plugins_root),
                "profiles_root": str(profiles_root),
                "on_conflict": "fail",
            },
        )
        assert unpack_response.status_code == 200
        unpack_body = unpack_response.json()
        assert unpack_body["package_type"] == "bundle"
        assert unpack_body["unpacked_plugin_count"] == 2
        assert unpack_body["payload_hash_verified"] is True
        assert "activation" not in unpack_body
        assert (plugins_root / "bundle_alpha" / "plugin.toml").is_file()
        assert (plugins_root / "bundle_beta" / "plugin.toml").is_file()
        assert (profiles_root / "route_workflow_bundle" / "default.toml").is_file()


@pytest.mark.asyncio
async def test_plugin_cli_unpack_route_uses_default_roots_when_fields_omitted(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """省略 plugins_root/profiles_root 时，默认落盘到 _INSTALL_*_ROOT 下。"""
    plugin_dir = _copy_fixture_plugin(tmp_path, "simple_plugin")
    package_path = tmp_path / "simple_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)

    default_plugins_root = tmp_path / "default_user_plugins"
    default_profiles_root = tmp_path / "default_user_profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path,
        user_root=default_plugins_root,
        packages_root=tmp_path,
        profiles_root=default_profiles_root,
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/unpack",
            json={"package": str(package_path)},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["plugins_root"] == str(default_plugins_root.resolve())
        assert "activation" not in body
        assert (default_plugins_root / "simple_plugin" / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_plugin_cli_install_plan_reports_matching_plugin_upgrade(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_fixture_plugin(tmp_path, "simple_plugin")
    package_path = tmp_path / "simple_plugin.neko-plugin"
    pack_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    installed = plugins_root / "simple_plugin"
    shutil.copytree(source, installed)
    manifest = (installed / "plugin.toml").read_text(encoding="utf-8")
    (installed / "plugin.toml").write_text(
        manifest.replace('version = "0.1.0"', 'version = "0.0.9"'),
        encoding="utf-8",
    )
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path,
        user_root=plugins_root,
        packages_root=tmp_path,
        profiles_root=tmp_path / "profiles",
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/install-plan",
            json={"package": str(package_path)},
        )

    assert response.status_code == 200
    assert response.json()["action"] == "upgrade"
    assert response.json()["plugin_id"] == "simple_plugin"
    assert response.json()["target_ownership"] == "unmanaged"


@pytest.mark.asyncio
async def test_plugin_cli_route_upgrades_in_place_after_confirmation(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "route_upgrade_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    v1_source = _make_plugin_dir(
        tmp_path / "v1-source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    v2_source = _make_plugin_dir(
        tmp_path / "v2-source",
        plugin_id=plugin_id,
        version="2.0.0",
    )
    v1_package = packages_root / f"{plugin_id}-1.0.0.neko-plugin"
    v2_package = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(v1_source, v1_package)
    pack_plugin(v2_source, v2_package)
    user_root = tmp_path / "user-plugins"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        install_response = await client.post(
            "/plugin-cli/install",
            json={"package": str(v1_package)},
        )
        assert install_response.status_code == 200, install_response.text
        assert install_response.json()["operation"] == "install"

        plan_response = await client.post(
            "/plugin-cli/install-plan",
            json={"package": str(v2_package)},
        )
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["action"] == "upgrade"
        assert plan["target_ownership"] == "managed"

        upgrade_response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(v2_package),
                "confirm_upgrade": True,
                "confirmation_token": plan["confirmation_token"],
            },
        )

    assert upgrade_response.status_code == 200, upgrade_response.text
    assert upgrade_response.json()["operation"] == "upgrade"
    installed_manifest = (user_root / plugin_id / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "2.0.0"' in installed_manifest
    assert not (user_root / f"{plugin_id}_1").exists()


@pytest.mark.asyncio
async def test_successful_upgrade_preserves_collocated_runtime_state_byte_for_byte(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "collocated_state_upgrade_demo"
    runtime_root = tmp_path / "runtime"
    user_root = runtime_root / "plugins"
    packages_root = runtime_root / "packages"
    profiles_root = runtime_root / "profiles"
    packages_root.mkdir(parents=True)
    old_source = _make_plugin_dir(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    new_source = _make_plugin_dir(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
    )
    old_package = packages_root / f"{plugin_id}-1.0.0.neko-plugin"
    new_package = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(old_source, old_package)
    pack_plugin(new_source, new_package)
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(runtime_root / "plugin-installations.json"),
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        install_response = await client.post(
            "/plugin-cli/install",
            json={"package": str(old_package)},
        )
        assert install_response.status_code == 200, install_response.text

        installed_dir = user_root / plugin_id
        expected_state = {
            Path("config") / "settings.bin": b"config-state\x00\xff",
            Path("data") / "user.db": b"data-state\x00\xfe",
            Path("cache") / "index.bin": b"cache-state\x00\xfd",
        }
        for relative_path, payload in expected_state.items():
            state_path = installed_dir / relative_path
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_bytes(payload)

        plan_response = await client.post(
            "/plugin-cli/install-plan",
            json={"package": str(new_package)},
        )
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["action"] == "upgrade"

        upgrade_response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(new_package),
                "confirm_upgrade": True,
                "confirmation_token": plan["confirmation_token"],
            },
        )

    assert upgrade_response.status_code == 200, upgrade_response.text
    assert {
        relative_path: (installed_dir / relative_path).read_bytes()
        for relative_path in expected_state
    } == expected_state


@pytest.mark.asyncio
async def test_upgrade_replaces_package_owned_data_and_preserves_user_created_data(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "package_owned_data_demo"
    runtime_root = tmp_path / "runtime"
    user_root = runtime_root / "plugins"
    packages_root = runtime_root / "packages"
    profiles_root = runtime_root / "profiles"
    packages_root.mkdir(parents=True)
    old_source = _make_plugin_dir(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    new_source = _make_plugin_dir(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
    )
    for source, payload in ((old_source, b"old-package-data"), (new_source, b"new-package-data")):
        package_asset = source / "data" / "defaults.json"
        package_asset.parent.mkdir(parents=True)
        package_asset.write_bytes(payload)
    old_package = packages_root / f"{plugin_id}-1.0.0.neko-plugin"
    new_package = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(old_source, old_package)
    pack_plugin(new_source, new_package)
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(runtime_root / "plugin-installations.json"),
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        install_response = await client.post(
            "/plugin-cli/install",
            json={"package": str(old_package)},
        )
        assert install_response.status_code == 200, install_response.text
        installed_dir = user_root / plugin_id
        user_data = installed_dir / "data" / "user.db"
        user_data.write_bytes(b"user-created-data\x00\xff")

        plan_response = await client.post(
            "/plugin-cli/install-plan",
            json={"package": str(new_package)},
        )
        plan = plan_response.json()
        upgrade_response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(new_package),
                "confirm_upgrade": True,
                "confirmation_token": plan["confirmation_token"],
            },
        )

    assert upgrade_response.status_code == 200, upgrade_response.text
    assert (installed_dir / "data" / "defaults.json").read_bytes() == b"new-package-data"
    assert user_data.read_bytes() == b"user-created-data\x00\xff"


@pytest.mark.asyncio
async def test_upgrade_rejects_user_modified_package_owned_data_and_restores_old_version(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "modified_package_data_demo"
    runtime_root = tmp_path / "runtime"
    user_root = runtime_root / "plugins"
    packages_root = runtime_root / "packages"
    profiles_root = runtime_root / "profiles"
    packages_root.mkdir(parents=True)
    old_source = _make_plugin_dir(
        tmp_path / "old-source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    new_source = _make_plugin_dir(
        tmp_path / "new-source",
        plugin_id=plugin_id,
        version="2.0.0",
    )
    for source, payload in ((old_source, b"old-package-data"), (new_source, b"new-package-data")):
        package_asset = source / "data" / "defaults.json"
        package_asset.parent.mkdir(parents=True)
        package_asset.write_bytes(payload)
    old_package = packages_root / f"{plugin_id}-1.0.0.neko-plugin"
    new_package = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(old_source, old_package)
    pack_plugin(new_source, new_package)
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(runtime_root / "plugin-installations.json"),
    )

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        install_response = await client.post(
            "/plugin-cli/install",
            json={"package": str(old_package)},
        )
        assert install_response.status_code == 200, install_response.text
        installed_dir = user_root / plugin_id
        package_asset = installed_dir / "data" / "defaults.json"
        package_asset.write_bytes(b"user-modified-package-data")

        plan_response = await client.post(
            "/plugin-cli/install-plan",
            json={"package": str(new_package)},
        )
        plan = plan_response.json()
        upgrade_response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(new_package),
                "confirm_upgrade": True,
                "confirmation_token": plan["confirmation_token"],
            },
        )

    assert upgrade_response.status_code == 409, upgrade_response.text
    assert upgrade_response.headers["x-error-code"] == "PLUGIN_PACKAGE_STATE_CONFLICT"
    assert package_asset.read_bytes() == b"user-modified-package-data"
    assert "version = \"1.0.0\"" in (installed_dir / "plugin.toml").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_local_import_replaces_same_id_market_install_after_confirmation(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse source switch also keeps exactly one logical plugin."""

    plugin_id = "market_then_import"
    package_source_root = tmp_path / "package-source"
    packages_root = tmp_path / "packages"
    package_source_root.mkdir()
    packages_root.mkdir()
    market_source = _make_plugin_dir(
        tmp_path / "market-source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    local_source = _make_plugin_dir(
        tmp_path / "local-source",
        plugin_id=plugin_id,
        version="2.0.0",
    )
    market_package = package_source_root / f"{plugin_id}-1.0.0.neko-plugin"
    local_package = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(market_source, market_package)
    pack_plugin(local_source, local_package)
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user-plugins"
    profiles_root = tmp_path / "profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()
    set_global_manager(manager)
    try:
        await PluginCliService().upload_and_install(
            filename=market_package.name,
            package_path=str(market_package),
            on_conflict="fail",
            install_source_override={
                "channel": "market",
                "mode": "install",
                "market_detail": {
                    "plugin_market_id": "4096",
                    "expected_plugin_toml_id": plugin_id,
                    "version": "1.0.0",
                    "package_url": "https://market.example/plugin.neko-plugin",
                    "package_sha256": hashlib.sha256(
                        market_package.read_bytes()
                    ).hexdigest(),
                    "payload_hash": None,
                    "channel": "stable",
                    "published_at": "2026-08-15T00:00:00.000Z",
                },
            },
        )
        [market_entry] = [
            entry
            for entry in manager.snapshot().entries
            if entry.plugin_id == plugin_id and not entry.removed
        ]
        assert market_entry.channel == "market"

        transport = ASGITransport(app=plugin_cli_test_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            plan_response = await client.post(
                "/plugin-cli/install-plan",
                json={"package": str(local_package)},
            )
            assert plan_response.status_code == 200, plan_response.text
            plan = plan_response.json()
            assert plan["action"] == "upgrade"

            install_response = await client.post(
                "/plugin-cli/install",
                json={
                    "package": str(local_package),
                    "install_source": "imported",
                    "confirm_upgrade": True,
                    "confirmation_token": plan["confirmation_token"],
                },
            )
    finally:
        set_global_manager(None)

    assert install_response.status_code == 200, install_response.text
    assert install_response.json()["operation"] == "upgrade"
    assert "2.0.0" in (user_root / plugin_id / "plugin.toml").read_text(
        encoding="utf-8"
    )
    assert not (user_root / f"{plugin_id}_1").exists()
    [imported_entry] = [
        entry
        for entry in manager.snapshot().entries
        if entry.plugin_id == plugin_id and not entry.removed
    ]
    assert imported_entry.channel == "imported"


@pytest.mark.asyncio
async def test_plugin_cli_install_returns_structured_rollback_details(
    plugin_cli_test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_install(**_kwargs: object) -> dict[str, object]:
        raise ServerDomainError(
            code="PLUGIN_UPGRADE_ROLLED_BACK",
            message="Plugin replacement failed and rollback completed",
            status_code=500,
            details={"stage": "install", "rollback_status": "completed"},
        )

    monkeypatch.setattr(plugin_cli_routes.service, "install", fail_install)
    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/install",
            json={"package": "/packages/demo.neko-plugin"},
        )

    assert response.status_code == 500
    assert response.headers["x-error-code"] == "PLUGIN_UPGRADE_ROLLED_BACK"
    assert response.json() == {
        "detail": {
            "code": "PLUGIN_UPGRADE_ROLLED_BACK",
            "message": "Plugin replacement failed and rollback completed",
            "details": {"stage": "install", "rollback_status": "completed"},
        }
    }


@pytest.mark.asyncio
async def test_plugin_cli_install_records_uploaded_package_as_imported(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "route_import_demo"
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_source_root = tmp_path / "package-source"
    package_source_root.mkdir()
    package_path = package_source_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    package_bytes = package_path.read_bytes()
    packages_root = tmp_path / "packages"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user-plugins"
    profiles_root = tmp_path / "profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    deletion_path = tmp_path / "plugin-installations.json"
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(deletion_path))
    mark_plugin_deleted(plugin_id, path=deletion_path)
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    try:
        transport = ASGITransport(app=plugin_cli_test_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload_response = await client.post(
                "/plugin-cli/upload",
                files={"file": (package_path.name, package_bytes, "application/octet-stream")},
            )
            assert upload_response.status_code == 200, upload_response.text
            response = await client.post(
                "/plugin-cli/install",
                json={
                    "package": upload_response.json()["path"],
                    "install_source": "imported",
                },
            )
    finally:
        set_global_manager(None)

    assert response.status_code == 200, response.text
    installed_dir = user_root / plugin_id
    source_view = manager.to_api_view(plugin_id, directory_path=installed_dir)
    assert source_view["source"] == "imported"
    assert source_view["source_detail"] == {
        "package_filename": package_path.name,
        "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
    }
    assert response.json()["activation"] == {
        "status": "active",
        "plugin_ids": [plugin_id],
        "reason": "user_installation_selected",
    }
    assert get_deleted_plugin_ids(path=deletion_path) == frozenset()


@pytest.mark.asyncio
async def test_local_import_source_write_failure_rolls_back_new_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "source_rollback_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    profiles_root = tmp_path / "profiles"
    inventory_path = tmp_path / "plugin-installations.json"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()

    def fail_record_import(**_kwargs: object) -> None:
        raise OSError("simulated source write failure")

    monkeypatch.setattr(manager, "record_import", fail_record_import)
    set_global_manager(manager)
    try:
        with pytest.raises(Exception, match="source write failure"):
            await PluginCliService().install(
                package=str(package_path),
                install_source="imported",
            )
    finally:
        set_global_manager(None)

    assert not (user_root / plugin_id).exists()
    assert manager.snapshot().entries == ()
    assert get_inventory_resolution(path=inventory_path).active_user_directories == {}


@pytest.mark.asyncio
async def test_local_import_secondary_rollback_failure_still_removes_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "secondary_rollback_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()
    monkeypatch.setattr(
        manager,
        "record_import",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("primary source failure")),
    )
    monkeypatch.setattr(
        manager,
        "restore_snapshot_for_rollback",
        lambda _snapshot: (_ for _ in ()).throw(OSError("secondary rollback failure")),
    )
    set_global_manager(manager)
    try:
        with pytest.raises(ServerDomainError) as exc_info:
            await PluginCliService().install(
                package=str(package_path),
                install_source="imported",
            )
    finally:
        set_global_manager(None)

    assert exc_info.value.code == "PLUGIN_INSTALL_ROLLBACK_INCOMPLETE"
    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_state_only_inventory_failure_preserves_preexisting_user_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "state_only_rollback_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "plugins"
    target = user_root / plugin_id
    for state_name in ("config", "data", "cache"):
        (target / state_name).mkdir(parents=True, exist_ok=True)
    user_db = target / "data" / "user.db"
    user_db.write_bytes(b"preexisting-user-state\x00\xff")
    before_files = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    before_dirs = {
        path.relative_to(target)
        for path in target.rglob("*")
        if path.is_dir()
    }
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )
    monkeypatch.setattr(
        plugin_cli_service_module,
        "record_user_installation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PluginInventoryError("simulated state-only inventory failure")
        ),
    )

    with pytest.raises(PluginInventoryError, match="state-only inventory failure"):
        await PluginCliService().install(package=str(package_path))

    assert target.is_dir()
    assert user_db.read_bytes() == b"preexisting-user-state\x00\xff"
    assert {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    } == before_files
    assert {
        path.relative_to(target)
        for path in target.rglob("*")
        if path.is_dir()
    } == before_dirs


@pytest.mark.asyncio
async def test_reinstall_after_delete_adopts_preexisting_package_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "deleted_reinstall_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / plugin_id
    profile_dir.mkdir(parents=True)
    existing_profile = profile_dir / "default.toml"
    existing_bytes = b'user_value = "keep-me"\r\n'
    existing_profile.write_bytes(existing_bytes)
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )

    result = await PluginCliService().install(package=str(package_path))

    assert result["installed_plugins"][0]["target_plugin_id"] == plugin_id
    assert (user_root / plugin_id / "plugin.toml").is_file()
    assert existing_profile.read_bytes() == existing_bytes


@pytest.mark.asyncio
async def test_reinstall_profile_survives_inventory_failure_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "deleted_reinstall_rollback_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / plugin_id
    profile_dir.mkdir(parents=True)
    existing_profile = profile_dir / "default.toml"
    existing_bytes = b'user_value = "keep-me"\x00\xff\r\n'
    existing_profile.write_bytes(existing_bytes)
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv(
        "NEKO_PLUGIN_INSTALLATIONS_PATH",
        str(tmp_path / "plugin-installations.json"),
    )
    monkeypatch.setattr(
        plugin_cli_service_module,
        "record_user_installation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PluginInventoryError("simulated profile adoption inventory failure")
        ),
    )

    with pytest.raises(PluginInventoryError, match="profile adoption inventory failure"):
        await PluginCliService().install(package=str(package_path))

    assert not (user_root / plugin_id).exists()
    assert profile_dir.is_dir()
    assert existing_profile.read_bytes() == existing_bytes


@pytest.mark.asyncio
async def test_reinstall_profile_conflict_does_not_expose_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "deleted_reinstall_conflict_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    (profiles_root / plugin_id).write_text("not a profile directory\n", encoding="utf-8")
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await PluginCliService().install(package=str(package_path))

    assert exc_info.value.code == "PLUGIN_CLI_CONFLICT"
    assert str(tmp_path) not in exc_info.value.message
    assert "conflict" in exc_info.value.message.lower()
    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_plugin_mutation_guard_serializes_different_plugins() -> None:
    mutation_guard = getattr(plugin_cli_service_module, "_plugin_mutation_guard", None)
    assert mutation_guard is not None
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_attempted = asyncio.Event()
    second_entered = asyncio.Event()
    order: list[str] = []

    async def second() -> None:
        second_attempted.set()
        async with mutation_guard():
            order.append("second_entered")
            second_entered.set()

    async def first() -> None:
        async with mutation_guard():
            order.append("first_entered")
            second_task = asyncio.create_task(second())
            await second_attempted.wait()
            first_entered.set()
            await release_first.wait()
            order.append("first_released")
        await second_task

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    assert not second_entered.is_set()

    release_first.set()
    await first_task
    assert order == ["first_entered", "first_released", "second_entered"]


@pytest.mark.asyncio
async def test_failed_install_rollback_does_not_clobber_later_plugin_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_a = "rollback_a"
    plugin_b = "successful_b"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_a = packages_root / f"{plugin_a}.neko-plugin"
    package_b = packages_root / f"{plugin_b}.neko-plugin"
    pack_plugin(_make_plugin_dir(tmp_path / "source-a", plugin_id=plugin_a), package_a)
    pack_plugin(_make_plugin_dir(tmp_path / "source-b", plugin_id=plugin_b), package_b)
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    inventory_path = tmp_path / "plugin-installations.json"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()
    service = PluginCliService()
    original_record = service._record_installations_for_result
    a_reached_inventory = asyncio.Event()
    release_a = asyncio.Event()
    b_attempted = asyncio.Event()

    async def controlled_record(
        install_result: dict[str, object],
        *,
        source: str,
    ) -> dict[str, object]:
        installed = install_result.get("installed_plugins")
        target_id = ""
        if isinstance(installed, list) and installed and isinstance(installed[0], dict):
            target_id = str(installed[0].get("target_plugin_id") or "")
        if target_id == plugin_a:
            a_reached_inventory.set()
            await release_a.wait()
            raise PluginInventoryError("simulated plugin A inventory failure")
        return await original_record(install_result, source=source)

    monkeypatch.setattr(service, "_record_installations_for_result", controlled_record)

    async def install_b() -> dict[str, object]:
        b_attempted.set()
        return await service.install(
            package=str(package_b),
            install_source="imported",
        )

    set_global_manager(manager)
    try:
        task_a = asyncio.create_task(
            service.install(package=str(package_a), install_source="imported")
        )
        await a_reached_inventory.wait()
        task_b = asyncio.create_task(install_b())
        await b_attempted.wait()
        assert not (user_root / plugin_b).exists()
        release_a.set()
        result_a, result_b = await asyncio.gather(
            task_a,
            task_b,
            return_exceptions=True,
        )
    finally:
        set_global_manager(None)

    assert isinstance(result_a, PluginInventoryError)
    assert isinstance(result_b, dict)
    assert not (user_root / plugin_a).exists()
    assert (user_root / plugin_b / "plugin.toml").is_file()
    assert manager.to_api_view(
        plugin_b,
        directory_path=user_root / plugin_b,
    )["source"] == "imported"
    assert get_inventory_resolution(
        path=inventory_path
    ).active_user_directories == {plugin_b: plugin_b}


@pytest.mark.asyncio
async def test_market_inventory_write_failure_rolls_back_files_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "inventory_rollback_demo"
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_source_root = tmp_path / "package-source"
    package_source_root.mkdir()
    package_path = package_source_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    packages_root = tmp_path / "packages"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    profiles_root = tmp_path / "profiles"
    inventory_path = tmp_path / "plugin-installations.json"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    manager.load()

    def fail_inventory(*_args: object, **_kwargs: object) -> None:
        raise PluginInventoryError("simulated inventory write failure")

    monkeypatch.setattr(
        "plugin.server.application.plugin_cli.service.record_user_installation",
        fail_inventory,
    )
    set_global_manager(manager)
    try:
        with pytest.raises(PluginInventoryError, match="inventory write failure"):
            await PluginCliService().upload_and_install(
                filename=package_path.name,
                package_path=str(package_path),
                on_conflict="fail",
                install_source_override={
                    "channel": "market",
                    "mode": "install",
                    "market_detail": {
                        "plugin_market_id": "501",
                        "expected_plugin_toml_id": plugin_id,
                        "version": "0.0.1",
                        "package_url": "https://market.example/plugin.neko-plugin",
                        "package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                    },
                },
            )
    finally:
        set_global_manager(None)

    assert not (user_root / plugin_id).exists()
    assert manager.snapshot().entries == ()
    assert get_inventory_resolution(path=inventory_path).active_user_directories == {}


@pytest.mark.asyncio
async def test_market_declared_version_must_match_plugin_manifest_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "market_version_mismatch_demo"
    source = _make_plugin_dir(
        tmp_path / "source",
        plugin_id=plugin_id,
        version="1.0.0",
    )
    package_path = tmp_path / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "user"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=tmp_path / "packages",
        profiles_root=tmp_path / "profiles",
    )

    with pytest.raises(ValueError, match="plugin version mismatch"):
        await PluginCliService().upload_and_install(
            filename=package_path.name,
            package_path=str(package_path),
            on_conflict="fail",
            install_source_override={
                "channel": "market",
                "mode": "install",
                "market_detail": {
                    "plugin_market_id": "501",
                    "expected_plugin_toml_id": plugin_id,
                    "version": "2.0.0",
                    "package_url": "https://market.example/plugin.neko-plugin",
                    "package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                },
            },
        )

    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_upgrade_inventory_write_failure_restores_previous_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "upgrade_inventory_rollback_demo"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    old_source = _make_plugin_dir(
        tmp_path / "old-source", plugin_id=plugin_id, version="1.0.0"
    )
    new_source = _make_plugin_dir(
        tmp_path / "new-source", plugin_id=plugin_id, version="2.0.0"
    )
    package_path = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(new_source, package_path)
    user_root = tmp_path / "user"
    installed_dir = user_root / plugin_id
    shutil.copytree(old_source, installed_dir)
    inventory_path = tmp_path / "plugin-installations.json"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    record_user_installation(
        plugin_id,
        directory_name=plugin_id,
        package_id=plugin_id,
        source="manual",
        path=inventory_path,
    )
    inventory_before = inventory_path.read_bytes()
    before = {
        path.relative_to(installed_dir): path.read_bytes()
        for path in installed_dir.rglob("*")
        if path.is_file()
    }
    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    assert plan["action"] == "upgrade"

    def fail_inventory(*_args: object, **_kwargs: object) -> None:
        raise PluginInventoryError("simulated upgrade inventory write failure")

    monkeypatch.setattr(
        "plugin.server.application.plugin_cli.service.record_user_installation",
        fail_inventory,
    )
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )
    assert exc_info.value.code == "PLUGIN_UPGRADE_ROLLED_BACK"
    assert exc_info.value.details["stage"] == "commit"
    assert exc_info.value.details["rollback_status"] == "completed"

    after = {
        path.relative_to(installed_dir): path.read_bytes()
        for path in installed_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert get_inventory_resolution(path=inventory_path).active_user_directories == {
        plugin_id: plugin_id
    }
    assert inventory_path.read_bytes() == inventory_before


@pytest.mark.asyncio
async def test_upgrade_finish_failure_keeps_committed_plugin_and_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "upgrade_committed_finish_failure"
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    old_source = _make_plugin_dir(
        tmp_path / "old-source", plugin_id=plugin_id, version="1.0.0"
    )
    new_source = _make_plugin_dir(
        tmp_path / "new-source", plugin_id=plugin_id, version="2.0.0"
    )
    package_path = packages_root / f"{plugin_id}-2.0.0.neko-plugin"
    pack_plugin(new_source, package_path)
    user_root = tmp_path / "user"
    installed_dir = user_root / plugin_id
    shutil.copytree(old_source, installed_dir)
    inventory_path = tmp_path / "plugin-installations.json"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    record_user_installation(
        plugin_id,
        directory_name=plugin_id,
        package_id=plugin_id,
        source="manual",
        path=inventory_path,
    )
    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))

    def fail_finish(_journal: object) -> None:
        raise OSError("simulated committed journal finish failure")

    monkeypatch.setattr(
        plugin_cli_service_module.upgrade_support._ReplacementJournal,
        "finish",
        fail_finish,
    )

    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == (
        "PLUGIN_REPLACEMENT_COMMITTED_CLEANUP_INCOMPLETE"
    )
    assert exc_info.value.details["committed"] is True
    assert 'version = "2.0.0"' in (installed_dir / "plugin.toml").read_text(
        encoding="utf-8"
    )
    assert get_inventory_resolution(path=inventory_path).active_user_directories == {
        plugin_id: plugin_id
    }
    journal_path = next(
        (user_root / ".upgrade-backups" / ".transactions").glob("*.json")
    )
    assert journal_path.exists()
    assert journal_path.with_suffix(".owner").exists()


@pytest.mark.asyncio
async def test_local_import_installs_newer_same_id_over_builtin_without_duplicate(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "builtin_overlay_local"
    canonical_entry = f"plugin.plugins.{plugin_id}:Plugin"
    builtin_root = tmp_path / "distribution" / "plugin" / "plugins"
    builtin_dir = _make_plugin_dir(
        builtin_root,
        plugin_id=plugin_id,
        version="1.0.0",
        entry=canonical_entry,
    )
    builtin_manifest_before = (builtin_dir / "plugin.toml").read_bytes()
    source = _make_plugin_dir(
        tmp_path / "source",
        plugin_id=plugin_id,
        version="2.0.0",
        entry=canonical_entry,
    )
    packaged_data = source / "data" / "defaults"
    packaged_data.mkdir(parents=True)
    (packaged_data / "labels.json").write_text("{}\n", encoding="utf-8")
    package_path = tmp_path / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "plugins"
    managed_root = tmp_path / "plugin-installations"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    state_config = user_root / plugin_id / "config" / "plugin.toml"
    state_data = user_root / plugin_id / "data" / "value.txt"
    state_config.parent.mkdir(parents=True)
    state_data.parent.mkdir()
    state_config.write_text("user_config = true\n", encoding="utf-8")
    state_data.write_text("preserve me\n", encoding="utf-8")
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        managed_root=managed_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=managed_root,
        scanner=PluginDirectoryScanner(builtin_root, managed_root),
    )
    set_global_manager(manager)
    try:
        transport = ASGITransport(app=plugin_cli_test_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload_response = await client.post(
                "/plugin-cli/upload",
                files={
                    "file": (
                        package_path.name,
                        package_path.read_bytes(),
                        "application/octet-stream",
                    )
                },
            )
            assert upload_response.status_code == 200, upload_response.text
            response = await client.post(
                "/plugin-cli/install",
                json={
                    "package": upload_response.json()["path"],
                    "install_source": "imported",
                },
            )
    finally:
        set_global_manager(None)

    assert response.status_code == 200, response.text
    assert response.json()["activation"] == {
        "status": "active",
        "plugin_ids": [plugin_id],
        "reason": "user_installation_selected",
    }
    assert (builtin_dir / "plugin.toml").read_bytes() == builtin_manifest_before
    assert (managed_root / plugin_id / "plugin.toml").is_file()
    assert not (user_root / plugin_id / "plugin.toml").exists()
    assert state_config.read_text(encoding="utf-8") == "user_config = true\n"
    assert state_data.read_text(encoding="utf-8") == "preserve me\n"
    assert get_inventory_resolution().active_installations[
        plugin_id
    ].installation_kind == "managed"
    assert (managed_root / plugin_id / "data" / "defaults" / "labels.json").read_text(
        encoding="utf-8"
    ) == "{}\n"
    with plugin_state.acquire_plugins_read_lock():
        assert set(plugin_state.plugins) == {plugin_id}
        projected = dict(plugin_state.plugins[plugin_id])
    assert Path(str(projected["config_path"])).parent == (managed_root / plugin_id).resolve()
    assert projected.get("runtime_load_state") != "failed"

    with plugin_state.acquire_plugins_write_lock():
        plugin_state.plugins.clear()
    await PluginRegistryService().refresh_registry()
    with plugin_state.acquire_plugins_read_lock():
        restarted_projection = dict(plugin_state.plugins[plugin_id])
    assert Path(str(restarted_projection["config_path"])).parent == (
        managed_root / plugin_id
    ).resolve()

    assert remove_user_installation(plugin_id) is True
    shutil.rmtree(managed_root / plugin_id)
    with plugin_state.acquire_plugins_write_lock():
        plugin_state.plugins.clear()
    await PluginRegistryService().refresh_registry()
    with plugin_state.acquire_plugins_read_lock():
        fallback_projection = dict(plugin_state.plugins[plugin_id])
    assert Path(str(fallback_projection["config_path"])).parent == builtin_dir.resolve()
    assert state_config.read_text(encoding="utf-8") == "user_config = true\n"
    assert state_data.read_text(encoding="utf-8") == "preserve me\n"


@pytest.mark.asyncio
async def test_market_install_clears_deleted_plugin_only_after_source_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "market_reinstall_demo"
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    package_source_root = tmp_path / "package-source"
    package_source_root.mkdir()
    package_path = package_source_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    packages_root = tmp_path / "packages"
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user-plugins"
    profiles_root = tmp_path / "profiles"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    deletion_path = tmp_path / "plugin-installations.json"
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(deletion_path))
    mark_plugin_deleted(plugin_id, path=deletion_path)
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=PluginDirectoryScanner(builtin_root, user_root),
    )
    set_global_manager(manager)
    try:
        result = await PluginCliService().upload_and_install(
            filename=package_path.name,
            package_path=str(package_path),
            on_conflict="fail",
            install_source_override={
                "channel": "market",
                "mode": "install",
                "market_detail": {
                    "plugin_market_id": "123",
                    "expected_plugin_toml_id": plugin_id,
                    "version": "0.0.1",
                    "package_url": "https://market.example/plugin.neko-plugin",
                    "package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                    "payload_hash": None,
                    "channel": "stable",
                    "published_at": "2026-08-15T00:00:00.000Z",
                },
            },
        )
    finally:
        set_global_manager(None)

    assert result["install"]["plugin_id"] == plugin_id
    assert result["unpack"]["activation"] == {
        "status": "active",
        "plugin_ids": [plugin_id],
        "reason": "user_installation_selected",
    }
    assert (user_root / plugin_id / "plugin.toml").is_file()
    assert get_deleted_plugin_ids(path=deletion_path) == frozenset()


@pytest.mark.asyncio
async def test_market_install_same_id_as_builtin_uses_managed_payload_and_shared_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "neko_warthunder"
    canonical_entry = f"plugin.plugins.{plugin_id}:Plugin"
    builtin_root = tmp_path / "distribution" / "plugin" / "plugins"
    builtin_dir = _make_plugin_dir(
        builtin_root,
        plugin_id=plugin_id,
        version="0.1.1",
        entry=canonical_entry,
    )
    builtin_bytes = (builtin_dir / "plugin.toml").read_bytes()
    source = _make_plugin_dir(
        tmp_path / "market-source",
        plugin_id=plugin_id,
        version="0.1.1",
        entry=canonical_entry,
    )
    (source / "market-build.txt").write_text("different bytes\n", encoding="utf-8")
    package_path = tmp_path / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)

    managed_root = tmp_path / "plugin-installations"
    state_root = tmp_path / "plugins"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    state_config = state_root / plugin_id / "config" / "plugin.toml"
    state_data = state_root / plugin_id / "data" / ".runtime_state.json"
    state_config.parent.mkdir(parents=True)
    state_data.parent.mkdir()
    state_config.write_bytes(b"user-config\x00")
    state_data.write_bytes(b"opaque-state\x00")
    inventory_path = tmp_path / "plugin-installations.json"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setenv("NEKO_PLUGIN_INSTALLATIONS_PATH", str(inventory_path))
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=builtin_root,
        user_root=state_root,
        managed_root=managed_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json",
        builtin_root=builtin_root,
        user_root=managed_root,
        scanner=PluginDirectoryScanner(builtin_root, managed_root),
    )
    set_global_manager(manager)
    try:
        result = await PluginCliService().upload_and_install(
            filename=package_path.name,
            package_path=str(package_path),
            on_conflict="fail",
            install_source_override={
                "channel": "market",
                "mode": "install",
                "market_detail": {
                    "plugin_market_id": "42",
                    "expected_plugin_toml_id": plugin_id,
                    "version": "0.1.1",
                    "package_url": "https://market.example/neko_warthunder.neko-plugin",
                    "package_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                    "payload_hash": None,
                    "channel": "stable",
                    "published_at": "2026-08-16T00:00:00.000Z",
                },
            },
        )
    finally:
        set_global_manager(None)

    assert result["unpack"]["activation"]["status"] == "active"
    assert (builtin_dir / "plugin.toml").read_bytes() == builtin_bytes
    assert (managed_root / plugin_id / "market-build.txt").read_text(
        encoding="utf-8"
    ) == "different bytes\n"
    assert not (state_root / plugin_id / "market-build.txt").exists()
    assert state_config.read_bytes() == b"user-config\x00"
    assert state_data.read_bytes() == b"opaque-state\x00"
    inventory = get_inventory_resolution(path=inventory_path)
    assert inventory.active_installations[plugin_id].installation_kind == "managed"

    with plugin_state.acquire_plugins_write_lock():
        plugin_state.plugins.clear()
    await PluginRegistryService().refresh_registry()
    with plugin_state.acquire_plugins_read_lock():
        assert set(plugin_state.plugins) == {plugin_id}
        projected = dict(plugin_state.plugins[plugin_id])
    assert Path(str(projected["config_path"])).parent == (
        managed_root / plugin_id
    ).resolve()


@pytest.mark.asyncio
async def test_installation_of_running_same_id_defers_switch_until_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "running_overlay_demo"
    target_dir = _make_plugin_dir(tmp_path / "user", plugin_id=plugin_id)
    refresh_calls: list[str] = []

    async def _is_running(candidate_id: str) -> bool:
        return candidate_id == plugin_id

    async def _unexpected_refresh(_self) -> dict[str, object]:
        refresh_calls.append("refresh")
        return {"success": True}

    monkeypatch.setattr(
        "plugin.server.application.plugin_cli.service.upgrade_support.plugin_is_running",
        _is_running,
    )
    monkeypatch.setattr(
        "plugin.server.application.plugin_cli.service.PluginRegistryService.refresh_registry",
        _unexpected_refresh,
    )
    install_result: dict[str, object] = {
        "package_id": plugin_id,
        "installed_plugins": [
            {
                "target_dir": str(target_dir),
            }
        ],
    }

    activation = await PluginCliService()._record_installations_for_result(
        install_result,
        source="market",
    )

    assert activation == {
        "status": "pending_restart",
        "plugin_ids": [plugin_id],
        "reason": "currently_running_version_remains_active_until_restart",
    }
    assert install_result["activation"] == activation
    assert refresh_calls == []


@pytest.mark.asyncio
async def test_plugin_cli_install_rolls_back_when_strict_source_hashing_fails(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "route_import_hash_failure"
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "user-plugins"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )
    def _hash_failure(_path: Path) -> str:
        raise OSError("package archive disappeared")

    monkeypatch.setattr(plugin_cli_routes.service, "_sha256_file", _hash_failure)
    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(package_path),
                "install_source": "imported",
            },
        )

    assert response.status_code == 500, response.text
    assert response.headers["x-error-code"] == "PLUGIN_INSTALL_SOURCE_PREPARE_FAILED"
    assert response.json()["detail"] == "plugin install source could not be verified"
    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_plugin_cli_install_rolls_back_when_activation_is_blocked(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "route_activation_blocked"
    source = _make_plugin_dir(tmp_path / "source", plugin_id=plugin_id)
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    package_path = packages_root / f"{plugin_id}.neko-plugin"
    pack_plugin(source, package_path)
    user_root = tmp_path / "user-plugins"
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=tmp_path / "profiles",
    )

    async def blocked_refresh(_self: object) -> dict[str, object]:
        return {
            "failed": [
                {
                    "plugin_id": plugin_id,
                    "error": "selected_installation_not_projected",
                }
            ]
        }

    monkeypatch.setattr(
        plugin_cli_service_module.PluginRegistryService,
        "refresh_registry",
        blocked_refresh,
    )
    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/install",
            json={"package": str(package_path)},
        )

    assert response.status_code == 409, response.text
    assert response.headers["x-error-code"] == "PLUGIN_INSTALLATION_ACTIVATION_BLOCKED"
    assert not (user_root / plugin_id).exists()


@pytest.mark.asyncio
async def test_plugin_cli_upload_and_install_failure_cleans_staging_and_saved_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    package_source_root = tmp_path / "package_source"
    user_root = tmp_path / "user_plugins"
    profiles_root = tmp_path / "profiles"
    packages_root = tmp_path / "packages"
    plugin_dir = _make_plugin_dir(source_root, plugin_id="simple_plugin")
    package_source_root.mkdir(parents=True, exist_ok=True)
    package_path = package_source_root / "simple_plugin.neko-plugin"
    pack_plugin(plugin_dir, package_path)
    existing_target = user_root / "simple_plugin"
    existing_target.mkdir(parents=True, exist_ok=True)
    (existing_target / "plugin.toml").write_text(
        '[plugin]\nid = "simple_plugin"\n',
        encoding="utf-8",
    )
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=user_root,
        packages_root=packages_root,
        profiles_root=profiles_root,
    )

    with pytest.raises(ServerDomainError):
        await PluginCliService().upload_and_install(
            filename="simple_plugin.neko-plugin",
            package_path=str(package_path),
            on_conflict="fail",
        )

    assert (existing_target / "plugin.toml").is_file()
    assert not list(user_root.glob(".neko_staging_*"))
    assert not list(profiles_root.glob(".neko_staging_*"))
    assert not list(packages_root.glob("*.neko-plugin"))
