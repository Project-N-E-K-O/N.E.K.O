from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
import pytest

from main_routers import pages_router, shared_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_shared_state(monkeypatch):
    """为页面测试复制共享状态，避免初始化结果泄漏到其他单元测试。"""  # noqa: DOCSTRING_CJK
    monkeypatch.setattr(shared_state, "_state", shared_state._state.copy())
    monkeypatch.setattr(shared_state, "set_steamworks", lambda _value: None)


def _build_pages_client() -> TestClient:
    """构造只挂页面路由的轻量客户端，用于验证 theater 不影响聊天/字幕页面。"""  # noqa: DOCSTRING_CJK
    shared_state.init_shared_state(
        role_state={},
        steamworks=None,
        templates=Jinja2Templates(directory=PROJECT_ROOT),
        config_manager=None,
        logger=None,
        initialize_character_data=None,
    )
    app = FastAPI()
    app.include_router(pages_router.router)
    return TestClient(app)


def _text_for(client: TestClient, path: str) -> str:
    """请求指定页面并返回渲染后的 HTML，失败时让测试直接暴露路由问题。"""  # noqa: DOCSTRING_CJK
    response = client.get(path)
    assert response.status_code == 200
    return response.text


def test_theater_selector_and_capsule_runtime_assets_are_scoped():
    """选择器只在 theater，胶囊编排器只进入聊天宿主，字幕页不被注入。"""  # noqa: DOCSTRING_CJK
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
    """旧模式页不再保留重定向，避免重新形成第二套入口。"""  # noqa: DOCSTRING_CJK
    registered_paths = {route.path for route in pages_router.router.routes}
    assert "/theater-home" not in registered_paths
    assert "/theater-numeric" not in registered_paths
