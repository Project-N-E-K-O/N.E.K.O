from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.neko_plugin_cli.public import pack_plugin
from plugin.server.application.plugin_cli.service import PluginCliService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.plugin_cli import router
from plugin.server.routes import plugin_cli as plugin_cli_routes

pytestmark = pytest.mark.plugin_unit
FIXTURE_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "neko_plugin_cli" / "plugins"


def _make_plugin_dir(tmp_path: Path, plugin_id: str = "route_demo") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Route Demo"',
                'version = "0.0.1"',
                'type = "plugin"',
                "",
                f"[{plugin_id}]",
                'value = "demo"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return plugin_dir


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
    packages_root: Path | None = None,
    profiles_root: Path | None = None,
) -> None:
    import plugin.settings as plugin_settings

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", user_root or builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root or builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root or (builtin_root / "profiles"))


class _MemoryUploadFile:
    def __init__(self) -> None:
        self.filename = "demo.neko-plugin"

    async def read(self) -> bytes:
        return b"demo"


@pytest.fixture
def plugin_cli_test_app() -> FastAPI:
    app = FastAPI(title="plugin-cli-test-app")
    register_exception_handlers(app)
    app.include_router(router)
    return app


def test_upload_and_unpack_legacy_returns_unpack_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_upload_and_install(*_args, **_kwargs) -> dict[str, object]:
        return {
            "upload": {"filename": "demo.neko-plugin"},
            "install": {
                "installed_plugins": ["demo"],
                "installed_plugin_count": 1,
            },
        }

    monkeypatch.setattr(
        plugin_cli_routes,
        "plugin_cli_upload_and_install",
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
