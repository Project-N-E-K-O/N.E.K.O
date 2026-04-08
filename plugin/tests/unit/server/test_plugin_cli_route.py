from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.plugin_cli import router

CLI_ROOT = Path(__file__).resolve().parents[3] / "neko-plugin-cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from public import pack_plugin

pytestmark = pytest.mark.plugin_unit


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
) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "route_demo.neko-plugin"
    pack_plugin(plugin_dir, package_path)

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
    pack_plugin(plugin_dir, package_path)

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
