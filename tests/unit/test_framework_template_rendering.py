"""Render production pages through the locked FastAPI and Jinja2 stack."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
import pytest

from main_routers import agent_router, cookies_login_router, pages_router, shared_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE_PATHS = [
    route.path.replace("{lanlan_name}", "test-cat")
    for route in pages_router.router.routes
]


@pytest.fixture
def pages_client(monkeypatch):
    monkeypatch.setattr(shared_state, "_state", shared_state._state.copy())
    monkeypatch.setattr(shared_state, "set_steamworks", lambda _value: None)
    shared_state.init_shared_state(
        role_state={},
        steamworks=None,
        templates=Jinja2Templates(directory=PROJECT_ROOT),
        config_manager=None,
        initialize_character_data=None,
    )
    app = FastAPI()
    app.include_router(agent_router.router)
    app.include_router(cookies_login_router.router)
    app.include_router(pages_router.router)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        yield client


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_production_pages_render_with_real_templates(pages_client, path):
    response = pages_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


@pytest.mark.parametrize("path", [
    f"{cookies_login_router.router.prefix}/page",
    f"{cookies_login_router.router.prefix}/guide",
    f"{agent_router.router.prefix}/openclaw/guide",
])
def test_credentials_and_agent_guides_render_with_real_templates(pages_client, path):
    response = pages_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()


def test_monitor_viewer_renders_with_real_templates(monkeypatch):
    from app import monitor, monitor_auth

    async def no_cleanup():
        pass

    # Keep the real lifespan and rendering, without changing global loggers or
    # leaving the production cleanup loop running in this rendering test.
    monkeypatch.setattr(monitor, "install_monitor_log_redaction", lambda: None)
    monkeypatch.setattr(monitor, "cleanup_disconnected_clients", no_cleanup)
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "template-smoke-secret")
    with TestClient(monitor.app) as client:
        response = client.get(
            "/test-cat", headers={"Authorization": "Bearer template-smoke-secret"}
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text.lower()
