from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
import pytest

from main_routers import pages_router, shared_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_shared_state(monkeypatch):
    """Copy shared state for page tests to prevent initialization leaking into other unit tests."""  # noqa: DOCSTRING_CJK
    monkeypatch.setattr(shared_state, "_state", shared_state._state.copy())
    monkeypatch.setattr(shared_state, "set_steamworks", lambda _value: None)


def _build_pages_client() -> TestClient:
    """Create a lightweight page-route-only client to verify theater does not affect chat/subtitle pages."""  # noqa: DOCSTRING_CJK
    shared_state.init_shared_state(
        role_state={},
        steamworks=None,
        templates=Jinja2Templates(directory=PROJECT_ROOT),
        config_manager=None,
        initialize_character_data=None,
    )
    app = FastAPI()
    app.include_router(pages_router.router)
    return TestClient(app)


def _text_for(client: TestClient, path: str) -> str:
    """Request a page and return rendered HTML, allowing failures to expose routing errors directly."""  # noqa: DOCSTRING_CJK
    response = client.get(path)
    assert response.status_code == 200
    return response.text


def test_theater_selector_and_capsule_runtime_assets_are_scoped():
    """Only theater gets the selector; only chat hosts get the capsule orchestrator; subtitle pages receive neither."""  # noqa: DOCSTRING_CJK
    with _build_pages_client() as client:
        theater_html = _text_for(client, "/theater")
        chat_html = _text_for(client, "/chat")
        subtitle_html = _text_for(client, "/subtitle")

    assert "data-theater-selector-app" in theater_html
    assert "/static/js/theater_transport.js" in theater_html
    assert "/static/js/theater_selector.js" in theater_html
    assert "/static/css/theater.css" not in theater_html
    assert "/static/app/app-theater-runtime.js" not in theater_html

    assert "react-chat-window-root" in chat_html
    assert "/static/js/theater_transport.js" in chat_html
    assert "/static/app/app-theater-runtime.js" in chat_html
    assert "/static/js/theater_selector.js" not in chat_html

    assert "subtitle-display" in subtitle_html
    assert "/static/js/theater_transport.js" not in subtitle_html
    assert "/static/app/app-theater-runtime.js" not in subtitle_html
    assert "/static/js/theater_selector.js" not in subtitle_html


def test_retired_theater_pages_are_not_registered():
    """Legacy-mode pages no longer retain redirects, avoiding a second entry-point path."""  # noqa: DOCSTRING_CJK
    registered_paths = {route.path for route in pages_router.router.routes}
    assert "/theater-home" not in registered_paths
    assert "/theater-numeric" not in registered_paths
