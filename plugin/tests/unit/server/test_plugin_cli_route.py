from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.neko_plugin_cli.public import build_plugin
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.plugin_cli import router

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
    return target


@pytest.fixture
def plugin_cli_test_app() -> FastAPI:
    app = FastAPI(title="plugin-cli-test-app")
    register_exception_handlers(app)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_plugin_cli_inspect_and_verify_routes(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "route_demo.neko-plugin"
    build_plugin(plugin_dir, package_path)

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", tmp_path)

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
) -> None:
    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugin-cli/plugins")

        assert response.status_code == 200
        body = response.json()
        assert "plugins" in body
        assert "count" in body
        assert isinstance(body["plugins"], list)


@pytest.mark.asyncio
async def test_plugin_cli_list_packages_route_returns_target_packages(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path, plugin_id="route_pkg_demo")
    package_path = tmp_path / "route_pkg_demo.neko-plugin"
    build_plugin(plugin_dir, package_path)

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/plugin-cli/packages")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["target_dir"] == str(tmp_path)
        assert body["packages"][0]["name"] == "route_pkg_demo.neko-plugin"


@pytest.mark.asyncio
async def test_plugin_cli_build_bundle_route_uses_mode_payload(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_plugin_dir(tmp_path, plugin_id="route_bundle_one")
    _make_plugin_dir(tmp_path, plugin_id="route_bundle_two")
    target_dir = tmp_path / "target"

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_RUNTIME_PLUGINS_ROOT", tmp_path)
    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", tmp_path)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/build",
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
        assert body["built_count"] == 1
        assert body["built"][0]["package_type"] == "bundle"
        assert body["built"][0]["plugin_ids"] == ["route_bundle_one", "route_bundle_two"]


@pytest.mark.asyncio
async def test_plugin_cli_route_workflow_build_analyze_inspect_verify_and_install(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha_dir = _copy_fixture_plugin(tmp_path, "bundle_alpha")
    beta_dir = _copy_fixture_plugin(tmp_path, "bundle_beta")
    target_dir = tmp_path / "target"
    plugins_root = tmp_path / "runtime_plugins"
    profiles_root = tmp_path / "runtime_profiles"

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_RUNTIME_PLUGINS_ROOT", tmp_path)
    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", target_dir)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PLUGINS_ROOT", tmp_path)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PROFILES_ROOT", profiles_root)

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

        build_response = await client.post(
            "/plugin-cli/build",
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
        assert build_response.status_code == 200
        build_body = build_response.json()
        assert build_body["ok"] is True
        assert build_body["built_count"] == 1

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

        install_response = await client.post(
            "/plugin-cli/install",
            json={
                "package": str(package_path),
                "plugins_root": str(plugins_root),
                "profiles_root": str(profiles_root),
                "on_conflict": "rename",
            },
        )
        assert install_response.status_code == 200
        install_body = install_response.json()
        assert install_body["package_type"] == "bundle"
        assert install_body["installed_plugin_count"] == 2
        assert install_body["payload_hash_verified"] is True
        assert (plugins_root / "bundle_alpha" / "plugin.toml").is_file()
        assert (plugins_root / "bundle_beta" / "plugin.toml").is_file()
        assert (profiles_root / "route_workflow_bundle" / "default.toml").is_file()


@pytest.mark.asyncio
async def test_plugin_cli_install_route_uses_default_roots_when_fields_omitted(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """省略 plugins_root/profiles_root 时，默认落盘到 _INSTALL_*_ROOT 下。"""
    plugin_dir = _copy_fixture_plugin(tmp_path, "simple_plugin")
    package_path = tmp_path / "simple_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)

    default_plugins_root = tmp_path / "default_user_plugins"
    default_profiles_root = tmp_path / "default_user_profiles"

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", tmp_path)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PLUGINS_ROOT", default_plugins_root)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PROFILES_ROOT", default_profiles_root)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/install",
            json={"package": str(package_path), "on_conflict": "rename"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["plugins_root"] == str(default_plugins_root.resolve())
        assert (default_plugins_root / "simple_plugin" / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_plugin_cli_upload_and_install_route_returns_upload_and_install_shapes(
    plugin_cli_test_app: FastAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = _copy_fixture_plugin(tmp_path, "simple_plugin")
    package_path = tmp_path / "simple_plugin.neko-plugin"
    build_plugin(plugin_dir, package_path)

    packages_root = tmp_path / "packages"
    plugins_root = tmp_path / "uploaded_user_plugins"
    profiles_root = tmp_path / "uploaded_user_profiles"

    import plugin.server.application.plugin_cli.service as plugin_cli_service_module

    monkeypatch.setattr(plugin_cli_service_module, "_TARGET_ROOT", packages_root)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PLUGINS_ROOT", plugins_root)
    monkeypatch.setattr(plugin_cli_service_module, "_INSTALL_PROFILES_ROOT", profiles_root)

    transport = ASGITransport(app=plugin_cli_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/plugin-cli/upload-and-install",
            files={
                "file": (
                    "simple_plugin.neko-plugin",
                    package_path.read_bytes(),
                    "application/octet-stream",
                )
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["upload"]["name"] == "simple_plugin.neko-plugin"
        assert body["upload"]["path"].endswith("simple_plugin.neko-plugin")
        assert "unpack" not in body
        assert body["install"]["package_type"] == "plugin"
        assert body["install"]["installed_plugin_count"] == 1
        assert body["install"]["installed_plugins"][0]["target_plugin_id"] == "simple_plugin"
        assert (plugins_root / "simple_plugin" / "plugin.toml").is_file()
