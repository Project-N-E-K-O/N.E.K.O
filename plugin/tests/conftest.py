from __future__ import annotations

# This deliberately duplicates plugin/conftest.py: plugin/tests has its own pytest.ini, and running
# `pytest plugin/tests` makes it rootdir, so confcutdir excludes the parent plugin/conftest.py.
# Running plugin/plugins/<name>/tests directly activates only the parent copy. Retain both so
# all eight test trees are guarded. Duplicate registration is harmless: the first restores and fails on drift, the second sees none.
#
# Set sys.path before any project import: these trees have different rootdirs, while the venv's
# editable .pth points to the main repository. Inserting it midway resolves early imports from the main repository and later ones
# from this copy (observed failure in main_logic.agent_event_bus).
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = str(_Path(__file__).resolve().parents[2])
if _sys.path[:1] != [_REPO_ROOT]:
    _sys.path.insert(0, _REPO_ROOT)

# pytest discovers hooks by name in the conftest namespace, so this import has no explicit call site;
# it is not dead code: deleting it disables the entire process-wide clock guard.
from tests.clock_guard import pytest_runtest_call  # noqa: F401,E402

import asyncio.events as _events
from collections.abc import AsyncIterator
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _isolate_plugin_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime_data"))
    monkeypatch.delenv("NEKO_STORAGE_ANCHOR_ROOT", raising=False)


@pytest.fixture(autouse=True)
def _isolate_runtime_overrides(monkeypatch: pytest.MonkeyPatch):
    """Redirect plugin runtime override persistence to an in-memory dict for tests.

    Without this, lifecycle tests could write to the real user's
    ``plugin_runtime_overrides.json`` and persist test state on the developer's
    machine.
    """
    from plugin.server.infrastructure import runtime_overrides as _ro

    fake_store: dict[str, bool] = {}

    monkeypatch.setattr(_ro, "_load_from_disk", lambda: dict(fake_store))
    monkeypatch.setattr(
        _ro,
        "_save_to_disk",
        lambda overrides: fake_store.clear() or fake_store.update(overrides),
    )
    _ro.reset_cache_for_testing()
    try:
        yield fake_store
    finally:
        _ro.reset_cache_for_testing()


@pytest.fixture(autouse=True)
def _clear_leaked_running_loop(request: pytest.FixtureRequest):
    """Temporarily clear any running event loop leaked by Playwright's greenlet
    so that sync tests see a clean ``asyncio.get_running_loop() → RuntimeError``
    environment.  Async tests are left untouched."""
    if request.node.get_closest_marker("asyncio") or getattr(
        request.node.obj, "is_coroutine", False
    ) or __import__("asyncio").iscoroutinefunction(getattr(request.node, "obj", None)):
        yield
        return
    saved = _events._get_running_loop()
    _events._set_running_loop(None)
    try:
        yield
    finally:
        _events._set_running_loop(saved)


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
