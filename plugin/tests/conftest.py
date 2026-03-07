from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.infrastructure.auth import verify_admin_code
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes.health import router as health_router
from plugin.server.routes.metrics import router as metrics_router
from plugin.server.routes.runs import router as runs_router


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-plugin-e2e",
        action="store_true",
        default=False,
        help="run plugin e2e tests (requires browser + running UI server)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-plugin-e2e"):
        return

    skip_marker = pytest.mark.skip(reason="needs --run-plugin-e2e to run")
    for item in items:
        if "plugin_e2e" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture
def plugin_test_app() -> FastAPI:
    app = FastAPI(title="plugin-test-app")
    register_exception_handlers(app)
    app.dependency_overrides[verify_admin_code] = lambda: "test-authenticated"
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(runs_router)
    return app


@pytest.fixture
async def plugin_async_client(plugin_test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=plugin_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
